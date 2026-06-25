# sliver-mcp — Claude dev + drive context

MCP server for the Sliver C2 framework. C2 leg of the dagar-red stack
(p0rtix recon → metasploit exploitation → **sliver** C2). Authorized
adversary-emulation research only.

## What this is

Python + FastMCP server wrapping [sliver-py](https://github.com/moloch--/sliver-py)
(the official async client). Registers as `sliver`; tools are `mcp__sliver__*`.
Mirrors the p0rtix MCP architecture deliberately so the three servers feel the
same to the driving agent.

## Layout

```
sliver_mcp/
  server.py       FastMCP app: build_server(manager) + all tools + main()
  manager.py      SliverManager: lazy connect, client singleton, asyncio.Lock,
                  event-pump task, session/beacon interact() resolver
  safety.py       SafetyState: noise tiers + arm_dangerous gate (ports p0rtix)
  implant.py      build_implant_config(): friendly args → ImplantConfig protobuf
  handoff.py      build_export() + normalize_ingest()
  serializers.py  protobuf → plain dict (explicit, snake_case)
  errors.py       ok()/err() uniform dict shaping
tests/            pytest, sliver-py fully mocked (64 tests, no server needed)
docs/             live-test.md (E2E), integration.md (dagar-red wiring)
```

## Design rules (keep these)

- **All sliver-py contact lives in `manager.py` and `implant.py`.** A library or
  protobuf change touches one place. Tools never import `sliver` directly.
- **Tools return dicts, never raise.** The `@tool(...)` wrapper in `server.py`
  catches everything and shapes it via `err()`.
- **Lazy + graceful connect.** The server starts without a team server; tools
  needing a client return `not connected` until `connect()` succeeds. (Unlike the
  msf MCP, which fail-fasts.) This matches p0rtix's `open_target`-first pattern.
- **Safety gate on every tool.** `@tool(tier=..., requires_client=..., armed=...)`.
  Default ceiling is green; `rm` is red + armed.

## Drive loop

```text
connect → set_noise("yellow") → start_*_listener → generate_beacon
→ poll_events → list_sessions → execute_command → export_handoff
```

## Verify

```bash
./venv/bin/pytest                 # unit suite (mocked)
./venv/bin/python scripts/smoke.py  # live, needs SLIVER_CONFIG + a team server
```

## Backlog / known gaps

- SOCKS / port-forward tunnels — not in sliver-py; only `list_pivots` exposed.
- Interactive PTY shell — out of scope (streaming ≠ request/response).
- `cp`/`chmod`/`chown`, loot/creds store — add when sliver-py exposes them.
- Live E2E was validated against a local team server; re-run `scripts/smoke.py`
  after any sliver-py bump.
