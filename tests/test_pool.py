"""Tests for implant-pool primitives: remove_implant_build and regenerate_or_build."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sliver_mcp.implant import _FORMATS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_c2(url: str):
    return SimpleNamespace(URL=url)


def _make_build_cfg(goos="windows", goarch="amd64",
                    fmt_key="exe", c2_url="https://redir.example.com:443"):
    """Build a fake ImplantConfig shaped like sliver-py returns from implant_builds."""
    fmt_val = _FORMATS[fmt_key]
    return SimpleNamespace(
        GOOS=goos,
        GOARCH=goarch,
        Format=fmt_val,
        IsBeacon=False,
        C2=[_make_c2(c2_url)],
    )


# ---------------------------------------------------------------------------
# remove_implant_build
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_implant_build_ok(server, call, fake_client):
    # remove_implant_build is yellow-tier; default ceiling is green — raise first
    await call(server, "set_noise", {"level": "yellow"})
    res = await call(server, "remove_implant_build", {"name": "STALE_BUILD"})
    assert res["status"] == "ok"
    assert res["name"] == "STALE_BUILD"
    fake_client.delete_implant_build.assert_awaited_once_with("STALE_BUILD")


@pytest.mark.asyncio
async def test_remove_implant_build_server_error(server, call, fake_client):
    await call(server, "set_noise", {"level": "yellow"})
    fake_client.delete_implant_build = AsyncMock(side_effect=RuntimeError("not found"))
    res = await call(server, "remove_implant_build", {"name": "GHOST"})
    assert res["status"] == "error"
    assert "sliver error" in res["message"]


# ---------------------------------------------------------------------------
# regenerate_or_build — cache hit (regenerate path)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regenerate_or_build_hit_reuses_existing(server, call, fake_client, connected):
    """When a build matches on os+arch+format+C2 URL it is regenerated, not recompiled."""
    build_cfg = _make_build_cfg(
        goos="windows", goarch="amd64", fmt_key="exe",
        c2_url="https://redir.example.com:443",
    )
    fake_client.implant_builds = AsyncMock(return_value={"EXISTING_POOL": build_cfg})
    fake_client.regenerate_implant = AsyncMock(
        return_value=SimpleNamespace(File=SimpleNamespace(Name="EXISTING_POOL", Data=b"MZ_regen"))
    )

    res = await call(server, "regenerate_or_build", {
        "c2_host": "redir.example.com",
        "protocol": "https",
        "os": "windows",
        "arch": "amd64",
        "fmt": "exe",
    })

    assert res["status"] == "ok"
    assert res["reused"] is True
    assert res["matched_name"] == "EXISTING_POOL"
    assert res["size"] == len(b"MZ_regen")
    assert res["c2"] == "https://redir.example.com:443"
    fake_client.regenerate_implant.assert_awaited_once_with("EXISTING_POOL")
    # generate path should NOT have been called
    connected.generate_implant.assert_not_awaited()


@pytest.mark.asyncio
async def test_regenerate_or_build_hit_saves_file(server, call, fake_client, tmp_path):
    build_cfg = _make_build_cfg(c2_url="https://redir.example.com:443")
    fake_client.implant_builds = AsyncMock(return_value={"POOL_BUILD": build_cfg})
    fake_client.regenerate_implant = AsyncMock(
        return_value=SimpleNamespace(File=SimpleNamespace(Name="POOL_BUILD", Data=b"MZ_data"))
    )

    res = await call(server, "regenerate_or_build", {
        "c2_host": "redir.example.com",
        "protocol": "https",
        "os": "windows",
        "arch": "amd64",
    })

    assert res["status"] == "ok"
    assert Path(res["saved_path"]).read_bytes() == b"MZ_data"


# ---------------------------------------------------------------------------
# regenerate_or_build — cache miss (compile path)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regenerate_or_build_miss_compiles_fresh(server, call, fake_client, connected):
    """When no matching build exists, compile fresh and name it pool-<proto>-<osarch>."""
    fake_client.implant_builds = AsyncMock(return_value={})

    res = await call(server, "regenerate_or_build", {
        "c2_host": "redir.example.com",
        "protocol": "https",
        "os": "linux",
        "arch": "amd64",
    })

    assert res["status"] == "ok"
    assert res["reused"] is False
    assert res["pool_name"] == "pool-https-linuxamd64"
    assert res["size"] > 0
    # generate should have been called with the pool name
    connected.generate_implant.assert_awaited_once()
    call_kwargs = connected.generate_implant.call_args
    assert call_kwargs.kwargs.get("name") == "pool-https-linuxamd64"


@pytest.mark.asyncio
async def test_regenerate_or_build_miss_os_mismatch(server, call, fake_client, connected):
    """A build that matches format+protocol but wrong OS is not reused."""
    build_cfg = _make_build_cfg(
        goos="linux",  # different OS
        goarch="amd64",
        fmt_key="exe",
        c2_url="https://redir.example.com:443",
    )
    fake_client.implant_builds = AsyncMock(return_value={"LINUX_BUILD": build_cfg})

    res = await call(server, "regenerate_or_build", {
        "c2_host": "redir.example.com",
        "protocol": "https",
        "os": "windows",  # requesting windows
        "arch": "amd64",
    })

    assert res["status"] == "ok"
    assert res["reused"] is False  # should not match


@pytest.mark.asyncio
async def test_regenerate_or_build_miss_url_mismatch(server, call, fake_client, connected):
    """A build for a different C2 host is not reused."""
    build_cfg = _make_build_cfg(c2_url="https://other-redir.example.com:443")
    fake_client.implant_builds = AsyncMock(return_value={"OTHER_BUILD": build_cfg})

    res = await call(server, "regenerate_or_build", {
        "c2_host": "redir.example.com",  # different host
        "protocol": "https",
        "os": "windows",
        "arch": "amd64",
    })

    assert res["status"] == "ok"
    assert res["reused"] is False


# ---------------------------------------------------------------------------
# regenerate_or_build — validation errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regenerate_or_build_bad_os(server, call):
    res = await call(server, "regenerate_or_build", {
        "c2_host": "redir.example.com",
        "os": "solaris",
    })
    assert res["status"] == "error"
    assert "invalid argument" in res["message"]


@pytest.mark.asyncio
async def test_regenerate_or_build_bad_protocol(server, call):
    res = await call(server, "regenerate_or_build", {
        "c2_host": "redir.example.com",
        "protocol": "ftp",
    })
    assert res["status"] == "error"
    assert "invalid argument" in res["message"]


@pytest.mark.asyncio
async def test_regenerate_or_build_missing_host(server, call):
    res = await call(server, "regenerate_or_build", {"c2_host": ""})
    assert res["status"] == "error"
    assert "invalid argument" in res["message"]


# ---------------------------------------------------------------------------
# noise gate: regenerate_or_build requires >= green
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regenerate_or_build_blocked_below_green(server, call, connected):
    # lower the ceiling to passive — green tools should then be blocked
    await call(server, "set_noise", {"level": "passive"})
    res = await call(server, "regenerate_or_build", {
        "c2_host": "redir.example.com",
    })
    assert res["status"] == "error"
    assert res.get("blocked") is True


@pytest.mark.asyncio
async def test_remove_implant_build_blocked_at_green(server, call, connected):
    # default ceiling is already green — yellow tools are blocked
    res = await call(server, "remove_implant_build", {"name": "X"})
    assert res["status"] == "error"
    assert res.get("blocked") is True


# ---------------------------------------------------------------------------
# regenerate_or_build — name-based fallback when proto fields are empty
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regenerate_or_build_hit_empty_proto_fields(
    server, call, fake_client, connected
):
    """Pool match uses build name when GOOS/GOARCH are empty (the Data-box bug).

    The Sliver server returned pool-https-linuxamd64 with empty GOOS/GOARCH
    and Format=0; the matching logic must fall back to name parsing so the
    existing pool build is reused instead of triggering a slow fresh compile.
    """
    c2 = _make_c2("https://redir.example.com:443")
    build_cfg = SimpleNamespace(
        GOOS="", GOARCH="",   # empty proto fields -- the bug shape
        Format=0,             # also unset
        IsBeacon=False,
        C2=[c2],
    )
    fake_client.implant_builds = AsyncMock(
        return_value={"pool-https-linuxamd64": build_cfg}
    )
    fake_client.regenerate_implant = AsyncMock(
        return_value=SimpleNamespace(
            File=SimpleNamespace(Name="pool-https-linuxamd64", Data=b"MZ_regen")
        )
    )

    res = await call(server, "regenerate_or_build", {
        "c2_host": "redir.example.com",
        "protocol": "https",
        "os": "linux",
        "arch": "amd64",
    })

    assert res["status"] == "ok"
    assert res["reused"] is True
    assert res["matched_name"] == "pool-https-linuxamd64"
    assert res["c2"] == "https://redir.example.com:443"
    fake_client.regenerate_implant.assert_awaited_once_with("pool-https-linuxamd64")
    connected.generate_implant.assert_not_awaited()


@pytest.mark.asyncio
async def test_regenerate_or_build_miss_wrong_name_os(
    server, call, fake_client, connected
):
    """Name-based fallback: build name OS mismatch still produces a miss."""
    c2 = _make_c2("https://redir.example.com:443")
    build_cfg = SimpleNamespace(
        GOOS="", GOARCH="",  # empty proto fields
        Format=0, IsBeacon=False, C2=[c2],
    )
    # Build name says windows but we are requesting linux.
    fake_client.implant_builds = AsyncMock(
        return_value={"pool-https-windowsamd64": build_cfg}
    )

    res = await call(server, "regenerate_or_build", {
        "c2_host": "redir.example.com",
        "protocol": "https",
        "os": "linux",
        "arch": "amd64",
    })

    assert res["status"] == "ok"
    assert res["reused"] is False
    connected.generate_implant.assert_awaited_once()
