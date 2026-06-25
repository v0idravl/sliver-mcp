"""Protobuf → plain-dict conversion.

The driving agent never sees a protobuf object; these helpers project the Sliver
gRPC messages down to the handful of fields that matter, with snake_case keys.
Kept deliberately explicit (rather than a generic MessageToDict) so the output is
stable, readable, and free of base64 byte blobs.
"""

from __future__ import annotations

import gzip
from typing import Any


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
