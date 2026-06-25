"""Tests for session/beacon listing, info, execution."""

import pytest


@pytest.mark.asyncio
async def test_list_sessions(server, call):
    res = await call(server, "list_sessions")
    assert res["status"] == "ok"
    assert res["count"] == 1
    assert res["sessions"][0]["kind"] == "session"
    assert res["sessions"][0]["hostname"] == "victim"


@pytest.mark.asyncio
async def test_list_beacons(server, call):
    res = await call(server, "list_beacons")
    assert res["count"] == 1
    assert res["beacons"][0]["kind"] == "beacon"


@pytest.mark.asyncio
async def test_session_info_found(server, call):
    res = await call(server, "session_info", {"session_id": "sess-1"})
    assert res["status"] == "ok"
    assert res["session"]["id"] == "sess-1"


@pytest.mark.asyncio
async def test_session_info_missing(server, call):
    res = await call(server, "session_info", {"session_id": "nope"})
    assert res["status"] == "error"
    assert "no session" in res["message"]


@pytest.mark.asyncio
async def test_execute_blocked_at_default_noise(server, call):
    # execute is yellow; default ceiling is green → blocked
    res = await call(server, "execute", {"target_id": "sess-1", "path": "whoami"})
    assert res["status"] == "error"
    assert res.get("blocked") is True


@pytest.mark.asyncio
async def test_execute_after_raising_noise(server, call):
    await call(server, "set_noise", {"level": "yellow"})
    res = await call(server, "execute", {"target_id": "sess-1", "path": "whoami"})
    assert res["status"] == "ok"
    assert res["target_kind"] == "session"
    assert "system" in res["stdout"].lower()


@pytest.mark.asyncio
async def test_execute_command_on_beacon(server, call):
    await call(server, "set_noise", {"level": "yellow"})
    res = await call(server, "execute_command",
                     {"target_id": "beac-1", "command_line": "ipconfig /all"})
    assert res["status"] == "ok"
    assert res["target_kind"] == "beacon"


@pytest.mark.asyncio
async def test_execute_unknown_target(server, call):
    await call(server, "set_noise", {"level": "yellow"})
    res = await call(server, "execute", {"target_id": "ghost", "path": "whoami"})
    assert res["status"] == "error"
    assert "no session or beacon" in res["message"]


@pytest.mark.asyncio
async def test_kill_session_yellow(server, call):
    await call(server, "set_noise", {"level": "yellow"})
    res = await call(server, "kill_session", {"session_id": "sess-1"})
    assert res["status"] == "ok"
