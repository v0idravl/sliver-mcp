"""Tests for connection/state tools (status, version, events, noise controls)."""

import pytest


@pytest.mark.asyncio
async def test_status_disconnected(disconnected_server, call):
    res = await call(disconnected_server, "status")
    assert res["status"] == "ok"
    assert res["connected"] is False


@pytest.mark.asyncio
async def test_status_connected(server, call):
    res = await call(server, "status")
    assert res["connected"] is True
    assert res["counts"]["sessions"] == 1
    assert res["operator"] == "unit-operator"


@pytest.mark.asyncio
async def test_get_version(server, call):
    res = await call(server, "get_version")
    assert res["status"] == "ok"
    assert res["server_version"] == "1.7.3"


@pytest.mark.asyncio
async def test_get_version_needs_connection(disconnected_server, call):
    res = await call(disconnected_server, "get_version")
    assert res["status"] == "error"
    assert "not connected" in res["message"]


@pytest.mark.asyncio
async def test_poll_events_empty(server, call):
    res = await call(server, "poll_events")
    assert res["status"] == "ok"
    assert res["events"] == []


@pytest.mark.asyncio
async def test_set_noise_and_arm_flow(server, call):
    assert (await call(server, "set_noise", {"level": "yellow"}))["noise"] == "yellow"
    # cannot jump to red without arming
    r = await call(server, "set_noise", {"level": "red"})
    assert r["status"] == "error"
    armed = await call(server, "arm_dangerous")
    assert armed["armed"] is True
    assert armed["noise"] == "red"


@pytest.mark.asyncio
async def test_set_noise_invalid(server, call):
    res = await call(server, "set_noise", {"level": "loud"})
    assert res["status"] == "error"
