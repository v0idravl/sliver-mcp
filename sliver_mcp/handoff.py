"""Structured handoff to/from the rest of the dagar-red MCP stack.

``build_export`` mirrors p0rtix's ``export_handoff()`` so the driving agent can
pull a single structured snapshot of C2 state and feed it back into
``internal-dispatch``. ``normalize_ingest`` does the reverse: it accepts a
p0rtix/metasploit-style handoff and distils the parameters needed to stand up a
matching listener and beacon, so a recon→C2 pivot is one tool call.
"""

from __future__ import annotations

from typing import Any

from .serializers import serialize_beacon, serialize_job, serialize_session


async def build_export(client: Any, safety_snapshot: dict, operator: str = "") -> dict:
    """Collect sessions, beacons, listeners/jobs, and implant builds."""
    version = await client.version()
    sessions = await client.sessions()
    beacons = await client.beacons()
    jobs = await client.jobs()
    builds = await client.implant_builds()

    listeners = [serialize_job(j) for j in jobs]
    build_summaries = []
    # implant_builds() → Dict[name, ImplantConfig]
    for build_name, cfg in (builds or {}).items():
        c2 = [c.URL for c in getattr(cfg, "C2", [])]
        build_summaries.append(
            {
                "name": build_name,
                "os": cfg.GOOS,
                "arch": cfg.GOARCH,
                "is_beacon": cfg.IsBeacon,
                "format": int(cfg.Format),
                "c2": c2,
            }
        )

    return {
        "status": "ok",
        "server_version": f"{version.Major}.{version.Minor}.{version.Patch}",
        "operator": operator,
        "sessions": [serialize_session(s) for s in sessions],
        "beacons": [serialize_beacon(b) for b in beacons],
        "listeners": listeners,
        "implant_builds": build_summaries,
        "counts": {
            "sessions": len(sessions),
            "beacons": len(beacons),
            "listeners": len(listeners),
            "builds": len(build_summaries),
        },
        **safety_snapshot,
    }


def normalize_ingest(handoff: dict) -> dict:
    """Distil a p0rtix/msf-style handoff into listener+beacon parameters.

    Accepts the loose shapes those tools emit and returns a normalized plan::

        {"protocol", "host", "port", "os", "arch", "domains"}

    Raises ``ValueError`` if no callback host/domain can be found.
    """
    if not isinstance(handoff, dict):
        raise ValueError("handoff must be an object")

    protocol = str(handoff.get("protocol") or "https").lower()

    # Callback host: accept the common key spellings used across the stack.
    host = (
        handoff.get("redirector")
        or handoff.get("callback_domain")
        or handoff.get("domain")
        or handoff.get("lhost")
        or handoff.get("host")
    )
    if not host:
        hosts = handoff.get("hosts")
        if isinstance(hosts, list) and hosts:
            host = hosts[0]
    if not host:
        raise ValueError(
            "no callback host found in handoff (expected one of: redirector, "
            "callback_domain, domain, lhost, host, hosts[0])"
        )

    port = int(handoff.get("port") or handoff.get("lport") or 0)

    # Target OS/arch: p0rtix may report 'windows'/'linux'; default to windows.
    os_val = str(handoff.get("os") or handoff.get("target_os") or "windows").lower()
    if "win" in os_val:
        os_val = "windows"
    elif "linux" in os_val:
        os_val = "linux"
    elif "darwin" in os_val or "mac" in os_val:
        os_val = "darwin"
    else:
        os_val = "windows"

    arch = str(handoff.get("arch") or "amd64").lower()
    domains = handoff.get("domains") or ([host] if protocol == "dns" else [])

    return {
        "protocol": protocol,
        "host": str(host),
        "port": port,
        "os": os_val,
        "arch": arch,
        "domains": list(domains),
    }
