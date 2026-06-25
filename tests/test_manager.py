"""Tests for SliverManager: connect path, lazy/graceful behavior, events."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from sliver_mcp.manager import SliverManager, default_config_path


def test_disconnected_by_default():
    m = SliverManager(config_path=None)
    assert m.connected is False


@pytest.mark.asyncio
async def test_connect_missing_config_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("SLIVER_CONFIG", raising=False)
    m = SliverManager(config_path=str(tmp_path / "nope.cfg"))
    with pytest.raises(FileNotFoundError):
        await m.connect()


@pytest.mark.asyncio
async def test_connect_no_path_at_all(monkeypatch):
    monkeypatch.delenv("SLIVER_CONFIG", raising=False)
    # No config discoverable anywhere.
    monkeypatch.setattr("sliver_mcp.manager.default_config_path", lambda: None)
    m = SliverManager(config_path=None)
    m._config_path = None
    with pytest.raises(FileNotFoundError):
        await m.connect()


@pytest.mark.asyncio
async def test_connect_success(monkeypatch, tmp_path):
    cfg = tmp_path / "op.cfg"
    cfg.write_text("{}")

    fake_client = MagicMock()
    fake_client.connect = AsyncMock(
        return_value=SimpleNamespace(Major=1, Minor=7, Patch=3))
    fake_client.is_connected = MagicMock(return_value=True)
    fake_client.events = MagicMock(side_effect=lambda: _empty_aiter())

    fake_sliver = SimpleNamespace(
        SliverClient=MagicMock(return_value=fake_client),
        SliverClientConfig=SimpleNamespace(
            parse_config_file=MagicMock(return_value=SimpleNamespace(operator="op"))),
    )
    monkeypatch.setitem(sys.modules, "sliver", fake_sliver)

    m = SliverManager(config_path=str(cfg))
    version = await m.connect()
    assert version.Major == 1
    assert m.connected is True
    assert m.operator == "op"


async def _empty_aiter():
    return
    yield  # pragma: no cover


def test_drain_events_empties_buffer():
    m = SliverManager(config_path=None)
    m._events.append({"type": "x"})
    m._events.append({"type": "y"})
    drained = m.drain_events()
    assert len(drained) == 2
    assert m.drain_events() == []
