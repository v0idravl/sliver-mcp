"""Tests for implant/beacon generation and the ImplantConfig builder."""

import pytest

from sliver_mcp.implant import build_implant_config, c2_url


def test_c2_url_https_default_port():
    assert c2_url("https", "redir.example.com") == "https://redir.example.com:443"


def test_c2_url_dns_has_no_port():
    assert c2_url("dns", "c2.example.com") == "dns://c2.example.com"


def test_build_config_beacon_timing_nanoseconds():
    cfg, url = build_implant_config(
        c2_host="c2.x", is_beacon=True, interval=60, jitter=30, protocol="https")
    assert cfg.IsBeacon is True
    assert cfg.BeaconInterval == 60_000_000_000
    assert cfg.BeaconJitter == 30_000_000_000
    assert url == "https://c2.x:443"
    assert cfg.C2[0].URL == url


def test_build_config_rejects_bad_os():
    with pytest.raises(ValueError):
        build_implant_config(c2_host="c2.x", os="solaris")


def test_build_config_rejects_bad_format():
    with pytest.raises(ValueError):
        build_implant_config(c2_host="c2.x", fmt="ova")


def test_build_config_requires_host():
    with pytest.raises(ValueError):
        build_implant_config(c2_host="")


def test_build_config_shellcode_flags():
    cfg, _ = build_implant_config(c2_host="c2.x", fmt="shellcode")
    assert cfg.IsShellcode is True
    assert cfg.IsSharedLib is False


@pytest.mark.asyncio
async def test_generate_beacon_saves_file(server, call, tmp_path):
    res = await call(server, "generate_beacon",
                     {"c2_host": "redir.example.com", "os": "windows"})
    assert res["status"] == "ok"
    assert res["is_beacon"] is True
    assert res["size"] > 0
    assert res["c2"] == "https://redir.example.com:443"
    # file actually written
    from pathlib import Path
    assert Path(res["saved_path"]).read_bytes()


@pytest.mark.asyncio
async def test_generate_implant_invalid_os(server, call):
    res = await call(server, "generate_implant",
                     {"c2_host": "c2.x", "os": "plan9"})
    assert res["status"] == "error"
    assert "invalid argument" in res["message"]


@pytest.mark.asyncio
async def test_list_implant_builds_empty(server, call):
    res = await call(server, "list_implant_builds")
    assert res["status"] == "ok"
    assert res["count"] == 0
