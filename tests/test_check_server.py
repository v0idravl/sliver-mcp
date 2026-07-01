"""Tests for the check_server tool: gRPC port probe."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_RESTART_CMD = (
    "/home/v0idravl/sliver-bin/sliver-server daemon -l 127.0.0.1 -p 31337"
)


async def _fake_open_connection_success(host, port):
    """Simulate a successful TCP connection (port is listening)."""
    reader = MagicMock()
    writer = MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock(return_value=None)
    return reader, writer


async def _fake_open_connection_refused(host, port):
    """Simulate ECONNREFUSED (daemon not running)."""
    raise ConnectionRefusedError("Connection refused")


async def _fake_open_connection_timeout(host, port):
    """Simulate a filtered port (connection timeout)."""
    await asyncio.sleep(10)  # longer than the 2 s probe timeout


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_server_up(disconnected_server, call):
    """When the gRPC port accepts a connection, report server_up=True."""
    with patch(
        "asyncio.open_connection",
        side_effect=_fake_open_connection_success,
    ):
        res = await call(disconnected_server, "check_server", {})

    assert res["status"] == "ok"
    assert res["server_up"] is True
    assert res["port"] == 31337
    assert res["host"] == "127.0.0.1"
    # No restart_command when the server is up
    assert "restart_command" not in res


@pytest.mark.asyncio
async def test_check_server_not_running_refused(disconnected_server, call):
    """When the gRPC port refuses the connection, report server_up=False with restart_command."""
    with patch(
        "asyncio.open_connection",
        side_effect=_fake_open_connection_refused,
    ):
        res = await call(disconnected_server, "check_server", {})

    assert res["status"] == "ok"
    assert res["server_up"] is False
    assert res["restart_command"] == _RESTART_CMD
    assert "hint" in res
    assert _RESTART_CMD in res["hint"]


@pytest.mark.asyncio
async def test_check_server_not_running_timeout(disconnected_server, call):
    """When the probe times out, also report server_up=False with restart_command."""
    with patch(
        "asyncio.open_connection",
        side_effect=_fake_open_connection_timeout,
    ):
        res = await call(disconnected_server, "check_server", {})

    assert res["status"] == "ok"
    assert res["server_up"] is False
    assert res["restart_command"] == _RESTART_CMD
    assert "timed out" in res["message"].lower()


@pytest.mark.asyncio
async def test_check_server_custom_port(disconnected_server, call):
    """Custom host/port are forwarded to open_connection and echoed back."""
    captured: list = []

    async def _capture(host, port):
        captured.append((host, port))
        raise ConnectionRefusedError()

    with patch("asyncio.open_connection", side_effect=_capture):
        res = await call(
            disconnected_server,
            "check_server",
            {"host": "10.0.0.1", "port": 9999},
        )

    assert captured == [("10.0.0.1", 9999)]
    assert res["host"] == "10.0.0.1"
    assert res["port"] == 9999
    assert res["server_up"] is False


@pytest.mark.asyncio
async def test_check_server_works_without_client(disconnected_server, call):
    """check_server must work even when no Sliver client is connected."""
    with patch(
        "asyncio.open_connection",
        side_effect=_fake_open_connection_refused,
    ):
        res = await call(disconnected_server, "check_server", {})

    # Must not return a "not connected" error — the tool is client-independent
    assert res["status"] == "ok"
    assert "not connected" not in res.get("message", "")
