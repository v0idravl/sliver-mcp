"""Test fixtures: a fake sliver-py client injected into the manager.

The whole suite runs with no live Sliver server. Every test talks to a
``FakeClient`` whose async methods return ``SimpleNamespace`` stand-ins shaped
like the real protobuf messages (the serializers only read named attributes).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sliver_mcp.manager import SliverManager
from sliver_mcp.server import build_server


# -- protobuf stand-ins -----------------------------------------------------
def make_version(major=1, minor=7, patch=3):
    return SimpleNamespace(Major=major, Minor=minor, Patch=patch, Commit="abc123",
                           Dirty=False, OS="linux", Arch="amd64", CompiledAt=0)


def make_session(id="sess-1", **kw):
    base = dict(ID=id, Name="UNIT", Hostname="victim", Username="user", UID="1000",
                GID="1000", OS="windows", Arch="amd64", Transport="mtls",
                RemoteAddress="10.0.0.5:1234", PID=4321, Filename="impl.exe",
                LastCheckin=0, ActiveC2="mtls://c2:8888", Version="10",
                IsDead=False, Burned=False)
    base.update(kw)
    return SimpleNamespace(**base)


def make_beacon(id="beac-1", **kw):
    base = dict(ID=id, Name="UNITB", Hostname="victim2", Username="user", UID="1000",
                GID="1000", OS="windows", Arch="amd64", Transport="https",
                RemoteAddress="10.0.0.6:443", PID=555, Filename="b.exe",
                LastCheckin=0, NextCheckin=60, Interval=60_000_000_000,
                Jitter=30_000_000_000, TasksCount=0, TasksCountCompleted=0,
                ActiveC2="https://c2:443", IsDead=False, Burned=False)
    base.update(kw)
    return SimpleNamespace(**base)


def make_job(id=1, protocol="https", port=443):
    return SimpleNamespace(ID=id, Name="https", Description="", Protocol=protocol,
                           Port=port, Domains=["c2.example.com"])


def make_execute(stdout=b"nt authority\\system\n", status=0):
    return SimpleNamespace(Status=status, Pid=1, Stdout=stdout, Stderr=b"")


def make_ls(path="C:\\", files=("a.txt", "b")):
    fobjs = [SimpleNamespace(Name=n, IsDir=False, Size=10, Mode="-rw-", ModTime=0)
             for n in files]
    return SimpleNamespace(Path=path, Exists=True, Files=fobjs)


def make_listener(job_id=7):
    return SimpleNamespace(JobID=job_id)


def make_generate(name="WACKY_IMPLANT", data=b"MZ\x90\x00binary"):
    return SimpleNamespace(File=SimpleNamespace(Name=name, Data=data))


class FakeInteractive:
    """Stands in for InteractiveSession / InteractiveBeacon."""

    def __init__(self):
        self.execute = AsyncMock(return_value=make_execute())
        self.ls = AsyncMock(return_value=make_ls())
        self.pwd = AsyncMock(return_value=SimpleNamespace(Path="C:\\Users"))
        self.cd = AsyncMock(return_value=SimpleNamespace(Path="C:\\Windows"))
        self.mkdir = AsyncMock(return_value=SimpleNamespace(Path="C:\\new"))
        self.rm = AsyncMock(return_value=SimpleNamespace(Path="C:\\gone"))
        self.upload = AsyncMock(return_value=SimpleNamespace(Path="C:\\up.txt"))
        self.download = AsyncMock(return_value=SimpleNamespace(
            Exists=True, Data=b"file contents", Encoder=""))
        self.pivot_listeners = AsyncMock(return_value=[
            SimpleNamespace(ID="p1", Type="tcp", BindAddress="0.0.0.0:9000", Pivots=[])])


class FakeClient:
    """Async fake of sliver-py's SliverClient with one session and one beacon."""

    def __init__(self):
        self._session = make_session()
        self._beacon = make_beacon()
        self._interactive = FakeInteractive()

        self.version = AsyncMock(return_value=make_version())
        self.sessions = AsyncMock(return_value=[self._session])
        self.beacons = AsyncMock(return_value=[self._beacon])
        self.jobs = AsyncMock(return_value=[make_job()])
        self.implant_builds = AsyncMock(return_value={})
        self.implant_profiles = AsyncMock(return_value=[])
        self.kill_job = AsyncMock(return_value=SimpleNamespace(Success=True))
        self.kill_session = AsyncMock(return_value=None)
        self.kill_beacon = AsyncMock(return_value=None)
        self.generate_implant = AsyncMock(return_value=make_generate())
        self.regenerate_implant = AsyncMock(return_value=make_generate())

        self.start_https_listener = AsyncMock(return_value=make_listener(1))
        self.start_http_listener = AsyncMock(return_value=make_listener(2))
        self.start_mtls_listener = AsyncMock(return_value=make_listener(3))
        self.start_dns_listener = AsyncMock(return_value=make_listener(4))
        self.start_wg_listener = AsyncMock(return_value=make_listener(5))

        self.session_by_id = AsyncMock(side_effect=self._session_by_id)
        self.beacon_by_id = AsyncMock(side_effect=self._beacon_by_id)
        self.interact_session = AsyncMock(return_value=self._interactive)
        self.interact_beacon = AsyncMock(return_value=self._interactive)

    def is_connected(self) -> bool:
        return True

    async def _session_by_id(self, sid, timeout=60):
        return self._session if sid == self._session.ID else None

    async def _beacon_by_id(self, bid, timeout=60):
        return self._beacon if bid == self._beacon.ID else None


# -- pytest fixtures --------------------------------------------------------
@pytest.fixture(autouse=True)
def _payload_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIVER_PAYLOAD_DIR", str(tmp_path / "payloads"))


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture
def manager():
    """A disconnected manager (no client)."""
    return SliverManager(config_path=None)


@pytest.fixture
def connected(manager, fake_client):
    """A manager with the fake client injected (as if connect() succeeded).

    Implant generation goes through ``manager.generate_implant`` (which, live,
    issues a raw gRPC call over sliver-py's channel), so we stub that method
    rather than the client.
    """
    manager._client = fake_client
    manager.operator = "unit-operator"
    manager.generate_implant = AsyncMock(return_value=make_generate())
    return manager


@pytest.fixture
def server(connected):
    return build_server(connected)


@pytest.fixture
def disconnected_server(manager):
    return build_server(manager)


@pytest.fixture
def call():
    """Return an async helper that calls a tool and parses its JSON result."""

    async def _call(srv, name, args=None):
        res = await srv.call_tool(name, args or {})
        content = res[0] if isinstance(res, tuple) else res
        return json.loads(content[0].text)

    return _call
