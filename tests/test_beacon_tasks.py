"""Tests for beacon-task resolution: session results pass through, beacon
Futures are awaited, and a never-resolving (dead) beacon times out cleanly."""

import asyncio
from types import SimpleNamespace

import pytest

from sliver_mcp import server as srv_mod
from sliver_mcp.server import _maybe_await


@pytest.mark.asyncio
async def test_maybe_await_passthrough_for_session_message():
    msg = SimpleNamespace(Status=0)
    assert await _maybe_await(msg) is msg


@pytest.mark.asyncio
async def test_maybe_await_resolves_beacon_future():
    async def beacon_task():
        return SimpleNamespace(Status=0, Stdout=b"ok", Stderr=b"", Pid=1)

    res = await _maybe_await(beacon_task())
    assert res.Status == 0


@pytest.mark.asyncio
async def test_maybe_await_times_out(monkeypatch):
    monkeypatch.setattr(srv_mod, "BEACON_TASK_TIMEOUT", 1)

    async def never():
        await asyncio.sleep(10)

    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        await _maybe_await(never(), timeout=1)


@pytest.mark.asyncio
async def test_execute_on_dead_beacon_times_out(server, connected, call, monkeypatch):
    """A queued task to a dead beacon must surface a structured timeout, not hang."""
    monkeypatch.setattr(srv_mod, "BEACON_TASK_TIMEOUT", 1)

    # A beacon's execute returns a Future quickly; the Future resolves on the
    # next check-in. A dead beacon never resolves it → bounded wait must fire.
    async def execute(*a, **k):
        return asyncio.get_event_loop().create_future()  # never resolves

    connected._client._interactive.execute = execute
    await call(server, "set_noise", {"level": "yellow"})
    res = await call(server, "execute", {"target_id": "beac-1", "path": "whoami"})
    assert res["status"] == "error"
    assert "timed out" in res["message"]
