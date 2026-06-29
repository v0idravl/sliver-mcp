"""Regression tests for stale pool build handling in regenerate_or_build.

Two complementary stale scenarios:

1. **Stale on-disk build directory** (Chemistry run, 2026-06-28):
   The Sliver DB has no record of the build but the compile directory still
   exists on disk from a prior run. The ``generate_implant`` RPC fails with
   "rename import dir: target exists: ..." -- this should surface as a
   structured, actionable error (not a raw gRPC blob).

2. **Stale C2 URL on a same-profile build** (Arctic run, 2026-06-27):
   A build exists in the DB with matching os/arch/format but the embedded
   callback URL points at a stale VPN allocation. When ``evict_stale=True``
   (the default), the stale build must be deleted and a fresh one compiled.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sliver_mcp.implant import _FORMATS


# ---------------------------------------------------------------------------
# Helpers (mirrors test_pool.py to keep the fixtures clear)
# ---------------------------------------------------------------------------

def _make_c2(url: str):
    return SimpleNamespace(URL=url)


def _make_build_cfg(goos="linux", goarch="amd64",
                    fmt_key="exe", c2_url="https://old-lhost.example.com:443"):
    fmt_val = _FORMATS[fmt_key]
    return SimpleNamespace(
        GOOS=goos,
        GOARCH=goarch,
        Format=fmt_val,
        IsBeacon=True,
        C2=[_make_c2(c2_url)],
    )


# ---------------------------------------------------------------------------
# Scenario 1 — stale on-disk build directory
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stale_build_dir_returns_actionable_error(server, call, fake_client, connected):
    """'rename import dir: target exists' gRPC error becomes a structured stale_build_dir error.

    The DB is empty (no matching build), but the fresh compile fails because the
    build directory already exists on disk from a previous run. The tool must
    return status='error', stale_build_dir=True, and a remedy string, not a
    raw 'sliver error: ...' blob.
    """
    fake_client.implant_builds = AsyncMock(return_value={})
    connected.generate_implant = AsyncMock(
        side_effect=RuntimeError(
            "rename import dir: target exists: "
            "/root/.sliver/slivers/linux/amd64/pool-https-linuxamd64/src/runc/cgroup"
        )
    )

    res = await call(server, "regenerate_or_build", {
        "c2_host": "redir.example.com",
        "protocol": "https",
        "os": "linux",
        "arch": "amd64",
    })

    assert res["status"] == "error"
    assert res.get("stale_build_dir") is True
    assert res.get("pool_name") == "pool-https-linuxamd64"
    assert res.get("os") == "linux"
    assert res.get("arch") == "amd64"
    # remedy must name the exact path to remove
    remedy = res.get("remedy", "")
    assert "pool-https-linuxamd64" in remedy
    assert "linux" in remedy
    assert "amd64" in remedy
    # message must suggest generate_beacon as a bypass
    msg = res.get("message", "")
    assert "generate_beacon" in msg or "unique name" in msg


@pytest.mark.asyncio
async def test_stale_build_dir_different_os(server, call, fake_client, connected):
    """Same stale-dir pattern but for a windows/amd64 pool build."""
    fake_client.implant_builds = AsyncMock(return_value={})
    connected.generate_implant = AsyncMock(
        side_effect=RuntimeError(
            "rename import dir: target exists: "
            "/root/.sliver/slivers/windows/amd64/pool-https-windowsamd64/src/runc/cgroup"
        )
    )

    res = await call(server, "regenerate_or_build", {
        "c2_host": "redir.example.com",
        "protocol": "https",
        "os": "windows",
        "arch": "amd64",
    })

    assert res["status"] == "error"
    assert res.get("stale_build_dir") is True
    assert res.get("pool_name") == "pool-https-windowsamd64"
    assert "windows" in res.get("remedy", "")


@pytest.mark.asyncio
async def test_non_stale_dir_grpc_error_propagates_normally(server, call, fake_client, connected):
    """A gRPC error that is NOT the stale-dir pattern should surface as a generic sliver error.

    The stale-dir detection must be specific: only 'rename import dir' + 'target
    exists' together trigger the structured error. Other compile failures fall
    through to the tool wrapper's generic handler.
    """
    fake_client.implant_builds = AsyncMock(return_value={})
    connected.generate_implant = AsyncMock(
        side_effect=RuntimeError("connection refused: team server unreachable")
    )

    res = await call(server, "regenerate_or_build", {
        "c2_host": "redir.example.com",
        "protocol": "https",
        "os": "linux",
        "arch": "amd64",
    })

    assert res["status"] == "error"
    # Must NOT have stale_build_dir set; this is a plain sliver error
    assert not res.get("stale_build_dir")
    assert "sliver error" in res.get("message", "")


# ---------------------------------------------------------------------------
# Scenario 2 — stale C2 URL eviction (c2-evict follow-up, Arctic run)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stale_c2_url_evicts_and_recompiles(server, call, fake_client, connected):
    """A same-profile build with a mismatched callback URL is evicted before recompiling.

    When evict_stale=True (default), delete_implant_build must be called for the
    stale build, and the fresh compile must proceed. evicted_stale should equal
    the stale build name.
    """
    stale_cfg = _make_build_cfg(
        goos="linux", goarch="amd64", fmt_key="exe",
        c2_url="https://old-lhost.example.com:443",   # stale VPN allocation
    )
    fake_client.implant_builds = AsyncMock(
        return_value={"pool-https-linuxamd64": stale_cfg}
    )

    res = await call(server, "regenerate_or_build", {
        "c2_host": "new-lhost.example.com",   # current VPN, different from stale
        "protocol": "https",
        "os": "linux",
        "arch": "amd64",
    })

    assert res["status"] == "ok"
    assert res["reused"] is False
    assert res.get("evicted_stale") == "pool-https-linuxamd64"
    assert "evicted" in res.get("message", "")
    fake_client.delete_implant_build.assert_awaited_once_with("pool-https-linuxamd64")
    connected.generate_implant.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_c2_url_no_evict_when_disabled(server, call, fake_client, connected):
    """When evict_stale=False, the stale build is left alone and a fresh compile proceeds."""
    stale_cfg = _make_build_cfg(
        goos="linux", goarch="amd64", fmt_key="exe",
        c2_url="https://old-lhost.example.com:443",
    )
    fake_client.implant_builds = AsyncMock(
        return_value={"pool-https-linuxamd64": stale_cfg}
    )

    res = await call(server, "regenerate_or_build", {
        "c2_host": "new-lhost.example.com",
        "protocol": "https",
        "os": "linux",
        "arch": "amd64",
        "evict_stale": False,
    })

    assert res["status"] == "ok"
    assert res["reused"] is False
    # delete must NOT have been called
    fake_client.delete_implant_build.assert_not_awaited()
    assert res.get("evicted_stale") is None


@pytest.mark.asyncio
async def test_stale_c2_url_eviction_failure_still_compiles(
    server, call, fake_client, connected
):
    """If delete_implant_build raises (NOT_FOUND), the fresh compile still proceeds.

    Eviction is best-effort: a failure should not block the compile. evicted_stale
    should be None (eviction did not succeed) but the overall result is still ok.
    """
    stale_cfg = _make_build_cfg(
        goos="linux", goarch="amd64", fmt_key="exe",
        c2_url="https://old-lhost.example.com:443",
    )
    fake_client.implant_builds = AsyncMock(
        return_value={"pool-https-linuxamd64": stale_cfg}
    )
    fake_client.delete_implant_build = AsyncMock(
        side_effect=RuntimeError("NOT_FOUND")
    )

    res = await call(server, "regenerate_or_build", {
        "c2_host": "new-lhost.example.com",
        "protocol": "https",
        "os": "linux",
        "arch": "amd64",
    })

    assert res["status"] == "ok"
    assert res["reused"] is False
    assert res.get("evicted_stale") is None  # eviction failed, compile still ran
    connected.generate_implant.assert_awaited_once()
