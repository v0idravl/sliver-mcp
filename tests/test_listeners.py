"""Tests for listener and job tools."""

import pytest


@pytest.mark.asyncio
async def test_start_https_listener(server, call):
    res = await call(server, "start_https_listener", {"port": 443, "domain": "c2.x"})
    assert res["status"] == "ok"
    assert res["job_id"] == 1
    assert res["protocol"] == "https"


@pytest.mark.asyncio
async def test_start_mtls_listener(server, call):
    res = await call(server, "start_mtls_listener", {"port": 8888})
    assert res["status"] == "ok"
    assert res["protocol"] == "mtls"


@pytest.mark.asyncio
async def test_start_dns_listener_requires_domains(server, call):
    res = await call(server, "start_dns_listener", {"domains": []})
    assert res["status"] == "error"
    assert "domains is required" in res["message"]


@pytest.mark.asyncio
async def test_start_dns_listener_ok(server, call):
    res = await call(server, "start_dns_listener", {"domains": ["c2.example.com."]})
    assert res["status"] == "ok"
    assert res["protocol"] == "dns"


@pytest.mark.asyncio
async def test_list_jobs(server, call):
    res = await call(server, "list_jobs")
    assert res["status"] == "ok"
    assert res["count"] == 1
    assert res["jobs"][0]["protocol"] == "https"


@pytest.mark.asyncio
async def test_kill_job(server, call):
    res = await call(server, "kill_job", {"job_id": 1})
    assert res["status"] == "ok"
    assert res["job_id"] == 1


@pytest.mark.asyncio
async def test_listener_not_connected(disconnected_server, call):
    res = await call(disconnected_server, "start_https_listener", {})
    assert res["status"] == "error"
    assert "not connected" in res["message"]


@pytest.mark.asyncio
async def test_listener_backend_error(server, connected, call):
    connected._client.start_https_listener.side_effect = RuntimeError("rpc down")
    res = await call(server, "start_https_listener", {})
    assert res["status"] == "error"
    assert "sliver error" in res["message"]
