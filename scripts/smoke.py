#!/usr/bin/env python3
"""Live smoke test for sliver-mcp against a running Sliver team server.

Authorized/lab use only. Requires SLIVER_CONFIG to point at an operator .cfg and
a reachable team server. Drives the manager directly (no MCP transport) to prove
the sliver-py adapter end to end.

Usage:
    SLIVER_CONFIG=~/.sliver-client/configs/dagar.cfg ./venv/bin/python scripts/smoke.py \
        [--protocol mtls] [--port 8443] [--os linux]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sliver_mcp.implant import build_implant_config
from sliver_mcp.manager import SliverManager, payload_dir


async def main(args: argparse.Namespace) -> int:
    config = os.environ.get("SLIVER_CONFIG") or args.config
    if not config:
        print("set SLIVER_CONFIG or pass --config", file=sys.stderr)
        return 2

    m = SliverManager(config)
    v = await m.connect()
    print(f"[+] connected to Sliver {v.Major}.{v.Minor}.{v.Patch} as {m.operator!r}")

    print(f"[*] starting {args.protocol} listener on :{args.port}")
    if args.protocol == "mtls":
        lis = await m.client.start_mtls_listener(host="0.0.0.0", port=args.port)
    elif args.protocol == "https":
        lis = await m.client.start_https_listener(host="0.0.0.0", port=args.port)
    else:
        print(f"smoke supports mtls|https, not {args.protocol}", file=sys.stderr)
        return 2
    print(f"[+] listener job {lis.JobID}")

    cfg, url = build_implant_config(
        c2_host=args.host, c2_port=args.port, protocol=args.protocol,
        os=args.os, fmt="exe", is_beacon=True, interval=5, jitter=1)
    print(f"[*] generating beacon (c2 {url}) …")
    gen = await m.generate_implant(cfg)  # routes via the vendored-pb path
    out = payload_dir() / (gen.File.Name or "beacon")
    out.write_bytes(gen.File.Data)
    print(f"[+] beacon {out} ({len(gen.File.Data)} bytes)")

    sessions = await m.client.sessions()
    beacons = await m.client.beacons()
    jobs = await m.client.jobs()
    print(f"[=] sessions={len(sessions)} beacons={len(beacons)} jobs={len(jobs)}")

    print("[*] cleaning up listener")
    try:
        await m.client.kill_job(int(lis.JobID))
    except Exception as exc:  # cleanup is best-effort
        print(f"[!] kill_job({lis.JobID}) failed (non-fatal): {exc}")
    await m.disconnect()
    print("[+] smoke complete")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=None)
    p.add_argument("--protocol", default="mtls", choices=["mtls", "https"])
    p.add_argument("--port", type=int, default=8443)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--os", default="linux", choices=["windows", "linux", "darwin"])
    raise SystemExit(asyncio.run(main(p.parse_args())))
