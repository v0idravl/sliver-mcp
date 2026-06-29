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
        # Tunnel registry: local_port → asyncio.Task
        self._socks_proxies: dict[int, asyncio.Task] = {}
        self._portfwd_proxies: dict[int, asyncio.Task] = {}
        self._tunnel_lock = asyncio.Lock()

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

    # -- SOCKS5 / portfwd tunnels ------------------------------------------
    def list_tunnels(self) -> dict:
        return {
            "socks": list(self._socks_proxies.keys()),
            "portfwd": list(self._portfwd_proxies.keys()),
        }

    async def start_socks(self, session_id: str, local_port: int) -> int:
        async with self._tunnel_lock:
            if local_port in self._socks_proxies:
                raise ValueError(f"SOCKS proxy already active on port {local_port}")
            task = asyncio.ensure_future(
                self._socks_proxy_loop(session_id, local_port))
            self._socks_proxies[local_port] = task
        return local_port

    async def stop_socks(self, local_port: int) -> bool:
        async with self._tunnel_lock:
            task = self._socks_proxies.pop(local_port, None)
            if task is not None:
                task.cancel()
                return True
        return False

    async def start_portfwd(self, session_id: str, local_port: int,
                             remote_host: str, remote_port: int) -> int:
        async with self._tunnel_lock:
            if local_port in self._portfwd_proxies:
                raise ValueError(f"portfwd already active on port {local_port}")
            task = asyncio.ensure_future(
                self._portfwd_proxy_loop(session_id, local_port, remote_host, remote_port))
            self._portfwd_proxies[local_port] = task
        return local_port

    async def stop_portfwd(self, local_port: int) -> bool:
        async with self._tunnel_lock:
            task = self._portfwd_proxies.pop(local_port, None)
            if task is not None:
                task.cancel()
                return True
        return False

    async def _socks_proxy_loop(self, session_id: str, local_port: int) -> None:
        from sliver.pb.sliverpb import sliver_pb2 as _sliverpb
        from sliver.pb.commonpb import common_pb2 as _commonpb

        channel = getattr(self._client, "_channel", None)
        if channel is None:
            return

        create_socks = channel.unary_unary(
            "/rpcpb.SliverRPC/CreateSocks",
            request_serializer=_sliverpb.Socks.SerializeToString,
            response_deserializer=_sliverpb.Socks.FromString,
        )
        proxy_mc = channel.stream_stream(
            "/rpcpb.SliverRPC/SocksProxy",
            request_serializer=_sliverpb.SocksData.SerializeToString,
            response_deserializer=_sliverpb.SocksData.FromString,
        )
        proxy_stream = proxy_mc()

        conn_map: dict[int, asyncio.StreamWriter] = {}
        send_lock = asyncio.Lock()

        async def recv_loop() -> None:
            try:
                async for msg in proxy_stream:
                    writer = conn_map.get(msg.TunnelID)
                    if writer is None:
                        continue
                    if msg.CloseConn:
                        conn_map.pop(msg.TunnelID, None)
                        try:
                            writer.close()
                            await writer.wait_closed()
                        except Exception:
                            pass
                    elif msg.Data:
                        writer.write(msg.Data)
                        try:
                            await writer.drain()
                        except Exception:
                            pass
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

        async def handle_conn(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            tunnel_id = 0
            request = _commonpb.Request(SessionID=session_id)
            try:
                resp = await create_socks(_sliverpb.Socks(SessionID=session_id))
                tunnel_id = resp.TunnelID
                conn_map[tunnel_id] = writer
                seq = 0
                while True:
                    data = await reader.read(32768)
                    if not data:
                        break
                    async with send_lock:
                        await proxy_stream.write(_sliverpb.SocksData(
                            TunnelID=tunnel_id,
                            Data=data,
                            Sequence=seq,
                            Request=request,
                        ))
                    seq += 1
            except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
                pass
            except Exception:
                pass
            finally:
                conn_map.pop(tunnel_id, None)
                if tunnel_id:
                    try:
                        async with send_lock:
                            await proxy_stream.write(_sliverpb.SocksData(
                                TunnelID=tunnel_id,
                                CloseConn=True,
                                Request=request,
                            ))
                    except Exception:
                        pass
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        recv_task = asyncio.ensure_future(recv_loop())
        try:
            server = await asyncio.start_server(handle_conn, "127.0.0.1", local_port)
            async with server:
                await server.serve_forever()
        except asyncio.CancelledError:
            pass
        finally:
            recv_task.cancel()
            try:
                await proxy_stream.done_writing()
            except Exception:
                pass
            async with self._tunnel_lock:
                self._socks_proxies.pop(local_port, None)

    async def _portfwd_proxy_loop(self, session_id: str, local_port: int,
                                   remote_host: str, remote_port: int) -> None:
        from sliver.pb.sliverpb import sliver_pb2 as _sliverpb
        from sliver.pb.commonpb import common_pb2 as _commonpb

        channel = getattr(self._client, "_channel", None)
        if channel is None:
            return

        create_tunnel = channel.unary_unary(
            "/rpcpb.SliverRPC/CreateTunnel",
            request_serializer=_sliverpb.Tunnel.SerializeToString,
            response_deserializer=_sliverpb.Tunnel.FromString,
        )
        close_tunnel = channel.unary_unary(
            "/rpcpb.SliverRPC/CloseTunnel",
            request_serializer=_sliverpb.Tunnel.SerializeToString,
            response_deserializer=lambda b: b,
        )
        portfwd_rpc = channel.unary_unary(
            "/rpcpb.SliverRPC/Portfwd",
            request_serializer=_sliverpb.PortfwdReq.SerializeToString,
            response_deserializer=_sliverpb.Portfwd.FromString,
        )
        tunnel_mc = channel.stream_stream(
            "/rpcpb.SliverRPC/TunnelData",
            request_serializer=_sliverpb.TunnelData.SerializeToString,
            response_deserializer=_sliverpb.TunnelData.FromString,
        )
        tunnel_stream = tunnel_mc()

        conn_map: dict[int, asyncio.StreamWriter] = {}
        send_lock = asyncio.Lock()

        async def recv_loop() -> None:
            try:
                async for msg in tunnel_stream:
                    writer = conn_map.get(msg.TunnelID)
                    if writer is None:
                        continue
                    if msg.Closed:
                        conn_map.pop(msg.TunnelID, None)
                        try:
                            writer.close()
                            await writer.wait_closed()
                        except Exception:
                            pass
                    elif msg.Data:
                        writer.write(msg.Data)
                        try:
                            await writer.drain()
                        except Exception:
                            pass
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

        async def handle_conn(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            tunnel_id = 0
            try:
                tun = await create_tunnel(_sliverpb.Tunnel(SessionID=session_id))
                tunnel_id = tun.TunnelID
                conn_map[tunnel_id] = writer

                pf_resp = await portfwd_rpc(_sliverpb.PortfwdReq(
                    Host=remote_host,
                    Port=remote_port,
                    Protocol=1,  # PortFwdProtoTCP
                    TunnelID=tunnel_id,
                    Request=_commonpb.Request(SessionID=session_id),
                ))
                if (pf_resp.Response is not None
                        and getattr(pf_resp.Response, "Err", "")):
                    return

                seq = 0
                while True:
                    data = await reader.read(32768)
                    if not data:
                        break
                    async with send_lock:
                        await tunnel_stream.write(_sliverpb.TunnelData(
                            TunnelID=tunnel_id,
                            SessionID=session_id,
                            Data=data,
                            Sequence=seq,
                        ))
                    seq += 1
            except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
                pass
            except Exception:
                pass
            finally:
                conn_map.pop(tunnel_id, None)
                if tunnel_id:
                    try:
                        async with send_lock:
                            await tunnel_stream.write(_sliverpb.TunnelData(
                                TunnelID=tunnel_id,
                                SessionID=session_id,
                                Closed=True,
                            ))
                    except Exception:
                        pass
                    try:
                        await close_tunnel(_sliverpb.Tunnel(
                            TunnelID=tunnel_id, SessionID=session_id))
                    except Exception:
                        pass
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        recv_task = asyncio.ensure_future(recv_loop())
        try:
            server = await asyncio.start_server(handle_conn, "127.0.0.1", local_port)
            async with server:
                await server.serve_forever()
        except asyncio.CancelledError:
            pass
        finally:
            recv_task.cancel()
            try:
                await tunnel_stream.done_writing()
            except Exception:
                pass
            async with self._tunnel_lock:
                self._portfwd_proxies.pop(local_port, None)
