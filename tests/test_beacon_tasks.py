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


def _never_resolving_execute():
    """A beacon execute that returns a Future which never completes (a task that
    is never picked up — either pending too long or a dead beacon)."""
    async def execute(*a, **k):
        return asyncio.get_event_loop().create_future()  # never resolves
    return execute


@pytest.mark.asyncio
async def test_execute_on_dead_beacon_returns_dead_status(server, connected, call, monkeypatch):
    """A queued task to a *dead* beacon surfaces a structured 'dead' status, not a hang."""
    monkeypatch.setattr(srv_mod, "BEACON_TASK_TIMEOUT", 1)
    connected._client._beacon.IsDead = True
    connected._client._interactive.execute = _never_resolving_execute()
    await call(server, "set_noise", {"level": "yellow"})
    res = await call(server, "execute", {"target_id": "beac-1", "path": "whoami"})
    assert res["status"] == "error"
    assert res["task_state"] == "dead"
    assert res["is_dead"] is True
    assert res["beacon_id"] == "beac-1"


@pytest.mark.asyncio
async def test_execute_command_on_live_beacon_returns_queued_status(
        server, connected, call, monkeypatch):
    """A task to a live beacon that hasn't checked in yet is reported as queued,
    with the check-in timing, not as an opaque timeout error."""
    monkeypatch.setattr(srv_mod, "BEACON_TASK_TIMEOUT", 1)
    connected._client._interactive.execute = _never_resolving_execute()
    await call(server, "set_noise", {"level": "yellow"})
    res = await call(server, "execute_command",
                     {"target_id": "beac-1", "command_line": "whoami /priv"})
    assert res["status"] == "ok"
    assert res["task_state"] == "queued"
    assert res["is_dead"] is False
    assert res["command"] == "whoami /priv"
    assert "next_checkin" in res
    assert res["waited_seconds"] == 1


@pytest.mark.asyncio
async def test_get_beacon_tasks_lists_and_counts_pending(server, call):
    res = await call(server, "get_beacon_tasks", {"beacon_id": "beac-1"})
    assert res["status"] == "ok"
    assert res["count"] == 1
    assert res["pending"] == 1
    assert res["tasks"][0]["state"] == "pending"
    assert res["tasks"][0]["id"] == "task-1"


@pytest.mark.asyncio
async def test_get_beacon_tasks_unknown_beacon(server, call):
    res = await call(server, "get_beacon_tasks", {"beacon_id": "nope"})
    assert res["status"] == "error"
    assert "no beacon" in res["message"]
