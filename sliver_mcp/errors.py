"""Uniform result shaping.

Every tool returns a plain ``dict`` with a ``status`` key — never a raw string and
never a raised exception that would surface to the model as an opaque error. This
mirrors the p0rtix / metasploit MCP convention so the driving agent can branch on
structure instead of parsing prose.
"""

from __future__ import annotations

from typing import Any


def ok(message: str | None = None, **fields: Any) -> dict:
    """A success result. Extra keyword fields are merged in verbatim."""
    out: dict[str, Any] = {"status": "ok"}
    if message:
        out["message"] = message
    out.update(fields)
    return out


def err(message: str, **fields: Any) -> dict:
    """An error result. Use for backend failures, bad input, or gate refusals."""
    out: dict[str, Any] = {"status": "error", "message": message}
    out.update(fields)
    return out


class SliverMCPError(Exception):
    """Internal error type; callers convert to an ``err(...)`` dict at the edge."""
