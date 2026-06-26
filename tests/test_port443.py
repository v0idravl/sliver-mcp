"""Regression: HTTPS C2 defaults to port 443 everywhere.

Operator standing-pool standard: every HTTPS listener and pool build defaults to
port **443**, not 4443. Builds that drifted to 4443 predated this standard. These
tests lock the invariant so a future change can't silently move the default
callback port off 443.
"""

import pytest


@pytest.mark.asyncio
async def test_generate_beacon_https_defaults_to_443(server, call):
    res = await call(server, "generate_beacon",
                     {"c2_host": "redir.example.com", "protocol": "https"})
    assert res["status"] == "ok"
    assert res["c2"].endswith(":443")


@pytest.mark.asyncio
async def test_generate_implant_https_defaults_to_443(server, call):
    res = await call(server, "generate_implant",
                     {"c2_host": "redir.example.com", "protocol": "https"})
    assert res["status"] == "ok"
    assert res["c2"].endswith(":443")


@pytest.mark.asyncio
async def test_regenerate_or_build_fresh_https_defaults_to_443(server, call):
    # empty build store → compiles a fresh pool build; its C2 must target 443
    res = await call(server, "regenerate_or_build",
                     {"c2_host": "redir.example.com", "protocol": "https"})
    assert res["status"] == "ok"
    assert res["reused"] is False
    assert res["c2"].endswith(":443")


@pytest.mark.asyncio
async def test_start_https_listener_default_port_is_443(server, call):
    res = await call(server, "start_https_listener", {"domain": "c2.x"})
    assert res["status"] == "ok"
    assert res["port"] == 443


@pytest.mark.asyncio
async def test_ingest_handoff_https_listener_and_beacon_use_443(server, connected, call):
    res = await call(server, "ingest_handoff",
                     {"handoff_data": {"protocol": "https", "redirector": "c2.x"}})
    assert res["status"] == "ok"
    assert res["beacon"]["c2"].endswith(":443")
    # the listener was stood up on 443, not 4443
    connected._client.start_https_listener.assert_awaited_once()
    _, kwargs = connected._client.start_https_listener.call_args
    assert kwargs["port"] == 443
