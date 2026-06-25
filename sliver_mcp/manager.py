"""Connection lifecycle and shared state for the Sliver MCP.

One :class:`SliverManager` lives for the life of the server process. It owns the
single sliver-py client, the noise/arm safety state, and a bounded buffer of
streamed events. Connection is *lazy and graceful*: the MCP always starts (the
team server or operator config may come up afterwards), and tools that need a
client get a structured "not connected" error instead of crashing.

Every sliver-py call is funnelled through this module so a library or protobuf
change touches one file (see also :mod:`sliver_mcp.implant`).
"""

from __future__ import annotations

import asyncio
import glob
import os
from collections import deque
from pathlib import Path
from typing import Any

from .safety import SafetyState
from .serializers import serialize_event

_CONFIG_ENV = "SLIVER_CONFIG"
_PAYLOAD_ENV = "SLIVER_PAYLOAD_DIR"
_DEFAULT_CONFIG_DIR = Path.home() / ".sliver-client" / "configs"
_DEFAULT_PAYLOAD_DIR = Path.home() / "sliver-payloads"
_EVENT_BUFFER = 500


def default_config_path() -> str | None:
    """Resolve the operator config: env var, else the first ``*.cfg`` on disk."""
    env = os.environ.get(_CONFIG_ENV)
    if env:
        return env
    matches = sorted(glob.glob(str(_DEFAULT_CONFIG_DIR / "*.cfg")))
    return matches[0] if matches else None


def payload_dir() -> Path:
    p = Path(os.environ.get(_PAYLOAD_ENV, str(_DEFAULT_PAYLOAD_DIR)))
    p.mkdir(parents=True, exist_ok=True)
    return p


class SliverManager:
    """Holds the sliver-py client, safety state, and the event buffer."""

    def __init__(self, config_path: str | None = None) -> None:
        self._config_path = config_path or default_config_path()
        self._client: Any = None
        self._connect_lock = asyncio.Lock()
        self._events: deque[dict] = deque(maxlen=_EVENT_BUFFER)
        self._event_task: asyncio.Task | None = None
        self.safety = SafetyState()
        self.last_config_path: str | None = None
        self.operator: str = ""

    # -- connection ---------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected()

    @property
    def client(self) -> Any:
        return self._client

    async def connect(self, config_path: str | None = None) -> Any:
        """Connect to the team server. Returns the server Version protobuf.

        Raises ``FileNotFoundError`` if no config can be resolved and the
        underlying sliver-py errors on connection failure — the server layer
        converts both into structured ``err(...)`` dicts.
        """
        async with self._connect_lock:
            if self.connected and not config_path:
                return await self._client.version()

            path = config_path or self._config_path or default_config_path()
            if not path:
                raise FileNotFoundError(
                    "no Sliver operator config found — set SLIVER_CONFIG or pass "
                    f"config_path (looked in {_DEFAULT_CONFIG_DIR})"
                )
            if not Path(path).is_file():
                raise FileNotFoundError(f"operator config not found: {path}")

            # Imported lazily so the module (and the test suite) load without a
            # live sliver-py/grpc stack present.
            from sliver import SliverClient, SliverClientConfig

            config = SliverClientConfig.parse_config_file(path)
            client = SliverClient(config)
            version = await client.connect()

            self._client = client
            self._config_path = path
            self.last_config_path = path
            self.operator = getattr(config, "operator", "") or ""
            self._start_event_pump()
            return version

    async def disconnect(self) -> None:
        self._stop_event_pump()
        client, self._client = self._client, None
        close = getattr(client, "close", None)
        if close is not None:
            try:
                res = close()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass

    # -- implant generation ------------------------------------------------
    async def generate_implant(self, config: Any, name: str = "",
                               timeout: int = 360) -> Any:
        """Build an implant from a vendored ImplantConfig over sliver-py's channel.

        sliver-py's own ``generate_implant`` can't be used: its bundled protobuf
        lacks ``HTTPC2ConfigName`` (and the Include* fields) the current server
        requires. We reuse the authenticated gRPC channel sliver-py already
        established and send the vendored ``GenerateReq`` directly. Returns the
        vendored ``Generate`` message (``.File.Name`` / ``.File.Data``).
        """
        from ._pb import client_pb2

        channel = getattr(self._client, "_channel", None)
        if channel is None:
            raise RuntimeError("no active gRPC channel — call connect() first")

        req = client_pb2.GenerateReq(Config=config, Name=name or "")
        call = channel.unary_unary(
            "/rpcpb.SliverRPC/Generate",
            request_serializer=client_pb2.GenerateReq.SerializeToString,
            response_deserializer=client_pb2.Generate.FromString,
        )
        return await call(req, timeout=timeout)

    # -- session / beacon interaction --------------------------------------
    async def interact(self, target_id: str) -> tuple[Any, str | None]:
        """Resolve an id to an interactive object.

        Returns ``(interactive, kind)`` where kind is ``"session"`` or
        ``"beacon"``; ``(None, None)`` if the id matches neither.
        """
        session = await self._client.session_by_id(target_id)
        if session is not None:
            return await self._client.interact_session(target_id), "session"
        beacon = await self._client.beacon_by_id(target_id)
        if beacon is not None:
            return await self._client.interact_beacon(target_id), "beacon"
        return None, None

    # -- events -------------------------------------------------------------
    def _start_event_pump(self) -> None:
        if self._event_task is not None and not self._event_task.done():
            return
        try:
            self._event_task = asyncio.ensure_future(self._pump_events())
        except RuntimeError:  # no running loop (shouldn't happen under FastMCP)
            self._event_task = None

    def _stop_event_pump(self) -> None:
        if self._event_task is not None:
            self._event_task.cancel()
            self._event_task = None

    async def _pump_events(self) -> None:
        try:
            async for event in self._client.events():
                self._events.append(serialize_event(event))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # surface, don't die silently
            self._events.append({"type": "_pump_error", "message": str(exc)})

    def drain_events(self) -> list[dict]:
        out = list(self._events)
        self._events.clear()
        return out
