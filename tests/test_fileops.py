"""Tests for file operations and the rm safety gate."""

import base64

import pytest


@pytest.mark.asyncio
async def test_ls(server, call):
    res = await call(server, "ls", {"target_id": "sess-1", "path": "C:\\"})
    assert res["status"] == "ok"
    assert res["exists"] is True
    assert len(res["files"]) == 2


@pytest.mark.asyncio
async def test_pwd_cd_mkdir(server, call):
    assert (await call(server, "pwd", {"target_id": "sess-1"}))["path"] == "C:\\Users"
    assert (await call(server, "cd", {"target_id": "sess-1", "path": "C:\\Windows"}))["path"] == "C:\\Windows"
    assert (await call(server, "mkdir", {"target_id": "sess-1", "path": "C:\\new"}))["status"] == "ok"


@pytest.mark.asyncio
async def test_download_writes_file(server, call):
    res = await call(server, "download",
                     {"target_id": "sess-1", "remote_path": "C:\\loot.txt"})
    assert res["status"] == "ok"
    assert res["size"] > 0
    assert res["preview"] == "file contents"
    from pathlib import Path
    assert Path(res["saved_path"]).exists()


@pytest.mark.asyncio
async def test_download_missing(server, connected, call):
    from types import SimpleNamespace
    connected._client._interactive.download.return_value = SimpleNamespace(
        Exists=False, Data=b"", Encoder="")
    res = await call(server, "download",
                     {"target_id": "sess-1", "remote_path": "C:\\nope"})
    assert res["status"] == "error"
    assert "does not exist" in res["message"]


@pytest.mark.asyncio
async def test_upload_from_b64(server, call):
    await call(server, "set_noise", {"level": "yellow"})
    data = base64.b64encode(b"payload").decode()
    res = await call(server, "upload",
                     {"target_id": "sess-1", "remote_path": "C:\\u.txt",
                      "data_b64": data})
    assert res["status"] == "ok"
    assert res["size"] == len(b"payload")


@pytest.mark.asyncio
async def test_upload_requires_source(server, call):
    await call(server, "set_noise", {"level": "yellow"})
    res = await call(server, "upload",
                     {"target_id": "sess-1", "remote_path": "C:\\u.txt"})
    assert res["status"] == "error"
    assert "local_path or data_b64" in res["message"]


@pytest.mark.asyncio
async def test_rm_blocked_until_armed(server, call):
    # rm is red + armed; even at red ceiling it needs arming. Default green:
    res = await call(server, "rm", {"target_id": "sess-1", "path": "C:\\x"})
    assert res["status"] == "error"
    assert res.get("blocked") is True


@pytest.mark.asyncio
async def test_rm_after_arm(server, call):
    await call(server, "arm_dangerous")
    res = await call(server, "rm",
                     {"target_id": "sess-1", "path": "C:\\x", "force": True})
    assert res["status"] == "ok"
    assert res["path"] == "C:\\gone"


@pytest.mark.asyncio
async def test_list_pivots(server, call):
    res = await call(server, "list_pivots", {"session_id": "sess-1"})
    assert res["status"] == "ok"
    assert res["count"] == 1
    assert res["pivots"][0]["type"] == "tcp"
