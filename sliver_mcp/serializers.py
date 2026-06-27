"""Protobuf → plain-dict conversion.

The driving agent never sees a protobuf object; these helpers project the Sliver
gRPC messages down to the handful of fields that matter, with snake_case keys.
Kept deliberately explicit (rather than a generic MessageToDict) so the output is
stable, readable, and free of base64 byte blobs.
"""

from __future__ import annotations

import gzip
from typing import Any

# Pool build name convention: pool-<proto>-<osarch>  e.g. pool-https-linuxamd64
# Longest arches first so "arm64" matches before "arm".
_KNOWN_ARCHES: tuple[str, ...] = ("arm64", "amd64", "arm", "386")
_KNOWN_OSES: frozenset[str] = frozenset({"linux", "windows", "darwin"})


def parse_osarch_from_name(name: str) -> tuple[str, str]:
    """Extract (os, arch) from a pool build name (pool-<proto>-<osarch>).

    Supports the pool naming convention used by ``regenerate_or_build``:
    e.g. ``pool-https-linuxamd64`` -> ``("linux", "amd64")``.
    Returns ``("", "")`` when the name does not match any known pattern.
    This is exposed publicly so ``regenerate_or_build`` can reuse it for
    fallback matching when the proto GOOS/GOARCH fields are empty.
    """
    parts = name.split("-")
    # Take the last segment (e.g. "linuxamd64" from "pool-https-linuxamd64").
    osarch = parts[-1].lower() if parts else ""
    for arch in _KNOWN_ARCHES:
        if osarch.endswith(arch):
            os_part = osarch[: -len(arch)]
            if os_part in _KNOWN_OSES:
                return os_part, arch
    return "", ""


def serialize_version(v: Any) -> dict:
    return {
        "version": f"{v.Major}.{v.Minor}.{v.Patch}",
        "commit": v.Commit,
        "dirty": v.Dirty,
        "os": v.OS,
        "arch": v.Arch,
        "compiled_at": v.CompiledAt,
    }


def serialize_session(s: Any) -> dict:
    return {
        "id": s.ID,
        "name": s.Name,
        "hostname": s.Hostname,
        "username": s.Username,
        "uid": s.UID,
        "gid": s.GID,
        "os": s.OS,
        "arch": s.Arch,
        "transport": s.Transport,
        "remote_address": s.RemoteAddress,
        "pid": s.PID,
        "filename": s.Filename,
        "last_checkin": s.LastCheckin,
        "active_c2": s.ActiveC2,
        "version": s.Version,
        "is_dead": s.IsDead,
        "burned": s.Burned,
        "kind": "session",
    }


def serialize_beacon(b: Any) -> dict:
    return {
        "id": b.ID,
        "name": b.Name,
        "hostname": b.Hostname,
        "username": b.Username,
        "uid": b.UID,
        "os": b.OS,
        "arch": b.Arch,
        "transport": b.Transport,
        "remote_address": b.RemoteAddress,
        "pid": b.PID,
        "filename": b.Filename,
        "last_checkin": b.LastCheckin,
        "next_checkin": b.NextCheckin,
        "interval": b.Interval,
        "jitter": b.Jitter,
        "tasks_count": b.TasksCount,
        "tasks_completed": b.TasksCountCompleted,
        "active_c2": b.ActiveC2,
        "is_dead": b.IsDead,
        "burned": b.Burned,
        "kind": "beacon",
    }


def serialize_job(j: Any) -> dict:
    return {
        "job_id": j.ID,
        "name": j.Name,
        "description": j.Description,
        "protocol": j.Protocol,
        "port": j.Port,
        "domains": list(j.Domains),
    }


def serialize_pivot_listener(p: Any) -> dict:
    return {
        "id": p.ID,
        "type": p.Type,
        "bind_address": p.BindAddress,
        "pivots": len(p.Pivots),
    }


def serialize_execute(e: Any) -> dict:
    """Execute result. stdout/stderr are bytes on the wire → decoded text."""
    return {
        "exit_status": e.Status,
        "pid": e.Pid,
        "stdout": _text(e.Stdout),
        "stderr": _text(e.Stderr),
    }


def serialize_beacon_task(t: Any) -> dict:
    """Project a BeaconTask down to the fields a caller polls.

    ``state`` is a string on the wire (``pending`` / ``sent`` / ``completed`` /
    ``canceled``). The Request/Response envelopes are intentionally omitted —
    use the dedicated content lookup if a task's output is needed.
    """
    return {
        "id": getattr(t, "ID", ""),
        "beacon_id": getattr(t, "BeaconID", ""),
        "state": getattr(t, "State", ""),
        "description": getattr(t, "Description", ""),
        "created_at": getattr(t, "CreatedAt", 0),
        "sent_at": getattr(t, "SentAt", 0),
        "completed_at": getattr(t, "CompletedAt", 0),
    }


def serialize_ls(ls: Any) -> dict:
    return {
        "path": ls.Path,
        "exists": ls.Exists,
        "files": [
            {
                "name": f.Name,
                "is_dir": f.IsDir,
                "size": f.Size,
                "mode": f.Mode,
                "mod_time": f.ModTime,
            }
            for f in ls.Files
        ],
    }


def serialize_event(ev: Any) -> dict:
    """Best-effort projection of a streamed Event into a flat dict."""
    out: dict[str, Any] = {"type": getattr(ev, "EventType", "")}
    sess = getattr(ev, "Session", None)
    if sess is not None and getattr(sess, "ID", ""):
        out["session"] = {"id": sess.ID, "hostname": sess.Hostname, "os": sess.OS}
    beacon = getattr(ev, "Beacon", None)
    if beacon is not None and getattr(beacon, "ID", ""):
        out["beacon"] = {"id": beacon.ID, "hostname": beacon.Hostname, "os": beacon.OS}
    job = getattr(ev, "Job", None)
    if job is not None and getattr(job, "ID", 0):
        out["job"] = {"id": job.ID, "protocol": job.Protocol, "port": job.Port}
    data = getattr(ev, "Data", b"")
    if data:
        out["data"] = _text(data)
    return out


def serialize_implant_build(name: str, c: Any) -> dict:
    """Project an ImplantConfig from ``implant_builds()`` into a readable dict.

    The Sliver server can return ``ImplantConfig`` objects with empty
    ``GOOS``/``GOARCH`` for pool builds (the proto metadata was not stored when
    the build was compiled). When that happens, ``os`` and ``arch`` are derived
    from the build name via the pool naming convention
    (``pool-<proto>-<osarch>``) so the caller can confirm a pool hit without
    relying on proto metadata. The ``os_source`` key identifies whether values
    came from the proto (``"proto"``) or the build name (``"name"``).

    A ``target_triple`` key (``linux/amd64`` style) is always returned; it is
    the canonical way the Sliver team server identifies a build's target
    platform and is what the agent should use when reasoning about pool matches.

    ``c2_urls`` lists the callback URLs baked into the build, matching the C2
    URL the ``regenerate_or_build`` pool discipline checks against.
    """
    goos: str = getattr(c, "GOOS", "") or ""
    goarch: str = getattr(c, "GOARCH", "") or ""
    # Forward-compat: newer Sliver proto versions may expose "beacon" (lowercase).
    is_beacon: bool = bool(
        getattr(c, "IsBeacon", False) or getattr(c, "beacon", False)
    )
    fmt: int = int(getattr(c, "Format", 0))
    c2_list = getattr(c, "C2", []) or []
    c2_urls: list[str] = [getattr(entry, "URL", "") for entry in c2_list]

    os_source = "proto"
    if not goos and not goarch:
        # Fallback: derive os/arch from the build name when proto fields are blank.
        goos, goarch = parse_osarch_from_name(name)
        if goos or goarch:
            os_source = "name"

    target_triple = (
        f"{goos}/{goarch}" if (goos and goarch)
        else goos or goarch  # partial info is still useful
    )

    return {
        "name": name,
        "os": goos,
        "arch": goarch,
        "target_triple": target_triple,
        "is_beacon": is_beacon,
        "format": fmt,
        "c2_urls": c2_urls,
        "os_source": os_source,
    }


def decode_download(d: Any) -> bytes:
    """Return the raw file bytes from a Download message, ungzipping if needed."""
    data = d.Data
    if getattr(d, "Encoder", "") == "gzip" and data:
        try:
            return gzip.decompress(data)
        except OSError:
            return data
    return data


def _text(b: Any) -> str:
    if isinstance(b, bytes):
        return b.decode("utf-8", errors="replace")
    return str(b) if b is not None else ""
