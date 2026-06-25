"""Tests for export_handoff shape and inbound ingest_handoff."""

import pytest

from sliver_mcp.handoff import normalize_ingest


# -- normalize_ingest (pure) ------------------------------------------------
def test_normalize_prefers_redirector():
    plan = normalize_ingest({"redirector": "r.example.com", "protocol": "https"})
    assert plan["host"] == "r.example.com"
    assert plan["protocol"] == "https"


def test_normalize_falls_back_to_hosts_list():
    plan = normalize_ingest({"hosts": ["1.2.3.4"], "protocol": "mtls", "port": 8888})
    assert plan["host"] == "1.2.3.4"
    assert plan["port"] == 8888


def test_normalize_os_windows_detection():
    assert normalize_ingest({"host": "x", "os": "Windows Server 2019"})["os"] == "windows"
    assert normalize_ingest({"host": "x", "os": "Ubuntu Linux"})["os"] == "linux"


def test_normalize_requires_host():
    with pytest.raises(ValueError):
        normalize_ingest({"protocol": "https"})


def test_normalize_dns_domains_default():
    plan = normalize_ingest({"host": "c2.example.com", "protocol": "dns"})
    assert plan["domains"] == ["c2.example.com"]


# -- export_handoff (through the server) ------------------------------------
@pytest.mark.asyncio
async def test_export_handoff_shape(server, call):
    res = await call(server, "export_handoff")
    assert res["status"] == "ok"
    assert res["operator"] == "unit-operator"
    for key in ("sessions", "beacons", "listeners", "implant_builds", "counts",
                "noise", "armed"):
        assert key in res
    assert res["counts"]["sessions"] == 1
    assert res["counts"]["listeners"] == 1


@pytest.mark.asyncio
async def test_ingest_handoff_creates_listener_and_beacon(server, call):
    res = await call(server, "ingest_handoff", {
        "handoff_data": {"redirector": "r.example.com", "protocol": "https",
                         "os": "windows"}})
    assert res["status"] == "ok"
    assert res["listener"]["protocol"] == "https"
    assert res["beacon"]["c2"] == "https://r.example.com:443"
    assert res["beacon"]["size"] > 0


@pytest.mark.asyncio
async def test_ingest_handoff_bad_input(server, call):
    res = await call(server, "ingest_handoff", {"handoff_data": {"protocol": "https"}})
    assert res["status"] == "error"
    assert "invalid argument" in res["message"]


@pytest.mark.asyncio
async def test_export_handoff_not_connected(disconnected_server, call):
    res = await call(disconnected_server, "export_handoff")
    assert res["status"] == "error"
    assert "not connected" in res["message"]
