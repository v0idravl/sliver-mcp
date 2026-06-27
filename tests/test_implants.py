"""Tests for implant/beacon generation and the ImplantConfig builder."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sliver_mcp.implant import build_implant_config, c2_url
from sliver_mcp.serializers import parse_osarch_from_name, serialize_implant_build


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


# ---------------------------------------------------------------------------
# serialize_implant_build unit tests (no server needed)
# ---------------------------------------------------------------------------

def _c2(url):
    return SimpleNamespace(URL=url)


def test_serialize_implant_build_proto_fields():
    """When GOOS/GOARCH are populated, use them directly."""
    cfg = SimpleNamespace(
        GOOS="linux", GOARCH="amd64", IsBeacon=True,
        Format=0, C2=[_c2("https://redir.example.com:443")]
    )
    out = serialize_implant_build("pool-https-linuxamd64", cfg)
    assert out["os"] == "linux"
    assert out["arch"] == "amd64"
    assert out["target_triple"] == "linux/amd64"
    assert out["is_beacon"] is True
    assert out["c2_urls"] == ["https://redir.example.com:443"]
    assert out["os_source"] == "proto"


def test_serialize_implant_build_empty_proto_falls_back_to_name():
    """When GOOS/GOARCH are empty strings, parse from the build name."""
    cfg = SimpleNamespace(
        GOOS="", GOARCH="", IsBeacon=False,  # the Data-box bug shape
        Format=0, C2=[_c2("https://redir.example.com:443")]
    )
    out = serialize_implant_build("pool-https-linuxamd64", cfg)
    assert out["os"] == "linux"
    assert out["arch"] == "amd64"
    assert out["target_triple"] == "linux/amd64"
    assert out["os_source"] == "name"


def test_serialize_implant_build_windows():
    cfg = SimpleNamespace(
        GOOS="windows", GOARCH="amd64", IsBeacon=False,
        Format=0, C2=[_c2("https://redir.example.com:443")]
    )
    out = serialize_implant_build("WACKY_IMPLANT", cfg)
    assert out["os"] == "windows"
    assert out["target_triple"] == "windows/amd64"
    assert out["os_source"] == "proto"


def test_serialize_implant_build_empty_proto_unknown_name():
    """When GOOS/GOARCH are empty and name doesn't follow pool convention."""
    cfg = SimpleNamespace(
        GOOS="", GOARCH="", IsBeacon=False, Format=0, C2=[]
    )
    out = serialize_implant_build("WACKY_IMPLANT", cfg)
    assert out["os"] == ""
    assert out["arch"] == ""
    assert out["target_triple"] == ""
    assert out["os_source"] == "proto"  # no name fallback applied


# ---------------------------------------------------------------------------
# parse_osarch_from_name unit tests
# ---------------------------------------------------------------------------

def test_parse_osarch_linux_amd64():
    assert parse_osarch_from_name("pool-https-linuxamd64") == ("linux", "amd64")


def test_parse_osarch_windows_amd64():
    assert parse_osarch_from_name("pool-mtls-windowsamd64") == ("windows", "amd64")


def test_parse_osarch_linux_arm64():
    assert parse_osarch_from_name("pool-https-linuxarm64") == ("linux", "arm64")


def test_parse_osarch_darwin_amd64():
    assert parse_osarch_from_name("pool-https-darwinamd64") == ("darwin", "amd64")


def test_parse_osarch_unknown_name():
    assert parse_osarch_from_name("WACKY_IMPLANT") == ("", "")


def test_parse_osarch_unknown_os_suffix():
    # "solarisamd64" is not a known OS
    assert parse_osarch_from_name("pool-https-solarisamd64") == ("", "")


# ---------------------------------------------------------------------------
# list_implant_builds integration tests (via server)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_implant_builds_with_metadata(server, call, fake_client):
    """list_implant_builds returns target_triple, c2_urls, os_source per build."""
    build_cfg = SimpleNamespace(
        GOOS="linux", GOARCH="amd64", IsBeacon=True,
        Format=0, C2=[_c2("https://redir.example.com:443")]
    )
    fake_client.implant_builds = AsyncMock(
        return_value={"pool-https-linuxamd64": build_cfg}
    )

    res = await call(server, "list_implant_builds")
    assert res["status"] == "ok"
    assert res["count"] == 1
    b = res["builds"][0]
    assert b["name"] == "pool-https-linuxamd64"
    assert b["os"] == "linux"
    assert b["arch"] == "amd64"
    assert b["target_triple"] == "linux/amd64"
    assert b["is_beacon"] is True
    assert b["c2_urls"] == ["https://redir.example.com:443"]
    assert b["os_source"] == "proto"


@pytest.mark.asyncio
async def test_list_implant_builds_empty_proto_fallback(server, call, fake_client):
    """When GOOS/GOARCH are empty (Data-box bug), list shows name-derived os/arch."""
    build_cfg = SimpleNamespace(
        GOOS="", GOARCH="", IsBeacon=False,
        Format=0, C2=[_c2("https://redir.example.com:443")]
    )
    fake_client.implant_builds = AsyncMock(
        return_value={"pool-https-linuxamd64": build_cfg}
    )

    res = await call(server, "list_implant_builds")
    assert res["status"] == "ok"
    b = res["builds"][0]
    assert b["os"] == "linux"
    assert b["arch"] == "amd64"
    assert b["target_triple"] == "linux/amd64"
    assert b["os_source"] == "name"
    assert b["c2_urls"] == ["https://redir.example.com:443"]
