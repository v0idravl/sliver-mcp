# sliver-mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server for the
[Sliver](https://github.com/BishopFox/sliver) C2 framework. It exposes the Sliver
operator surface — listeners, implant/beacon generation, sessions and beacons,
command execution, file operations, and a structured handoff — as
`mcp__sliver__*` tools an LLM agent can drive.

Built to slot into the **dagar-red** adversary-emulation skill stack alongside the
**p0rtix** (recon) and **metasploit** (exploitation) MCP servers, and to mirror
their conventions: Python + FastMCP, async tools, structured-dict returns, and a
noise / `arm_dangerous` safety gate.

> ⚠️ **Authorized use only.** This drives a live C2 framework. Use it only against
> infrastructure you own or are explicitly authorized to test — owned labs, CTFs,
> and contracted engagements. It is built for adversary *emulation*: standing up
> realistic C2 so defenders can test and improve detection and response. The
> noise tiers and the `arm_dangerous` gate exist to keep operation deliberate and
> in scope.

## How it relates to Sliver's built-in MCP

Sliver ships an experimental built-in MCP, but it is **filesystem-only** (≈11
tools: `fs_ls`, `fs_cat`, `fs_rm`, …). `sliver-mcp` is a **superset** focused on
the full operator workflow — listeners, payload generation, sessions/beacons,
execution, and cross-tool handoff — so an agent can run an engagement end to end.

## Install

Requires Python ≥ 3.11 and a reachable Sliver **team server** with an operator
config (`.cfg`). See [`docs/live-test.md`](docs/live-test.md) for standing up a
local server and generating an operator config.

```bash
git clone git@github.com:v0idravl/sliver-mcp.git
cd sliver-mcp
python3 -m venv venv
./venv/bin/pip install -e .
```

## Register with Claude Code

Add to `~/.claude.json` (or via `claude mcp add`). Point `SLIVER_CONFIG` at your
operator config:

```json
"sliver": {
  "type": "stdio",
  "command": "/home/youruser/projects/sliver-mcp/venv/bin/sliver-mcp",
  "args": [],
  "env": { "SLIVER_CONFIG": "/home/youruser/.sliver-client/configs/operator.cfg" }
}
```

The server starts whether or not the team server is up — call `connect()` first;
tools that need a live client return a structured "not connected" error until it
succeeds.

## Tool surface (`mcp__sliver__*`)

| Category | Tools |
|----------|-------|
| Connection / state | `connect`, `status`, `get_version`, `poll_events`, `disconnect` |
| Listeners | `start_https_listener`, `start_http_listener`, `start_mtls_listener`, `start_dns_listener`, `start_wg_listener`, `list_jobs`, `kill_job` |
| Implant generation | `generate_implant`, `generate_beacon`, `list_implant_builds`, `list_implant_profiles`, `regenerate_implant` |
| Sessions / beacons | `list_sessions`, `list_beacons`, `session_info`, `beacon_info`, `kill_session`, `kill_beacon` |
| Execution | `execute`, `execute_command` |
| File operations | `ls`, `pwd`, `cd`, `mkdir`, `download`, `upload`, `rm` |
| Pivots | `list_pivots` |
| Handoff | `export_handoff`, `ingest_handoff` |
| Safety | `set_noise`, `arm_dangerous` |

## Safety / noise model

Every tool carries a noise **tier**. A call above the current ceiling is refused
with a structured reason — never silently downgraded.

| Tier | Meaning | Examples |
|------|---------|----------|
| `passive` | read-only state | `status`, `list_sessions`, `export_handoff` |
| `green` | build / stand up our own infra | listeners, `generate_*`, `ls`, `download` |
| `yellow` | actions that touch the target | `execute`, `upload`, `kill_session` |
| `red` | destructive | `rm` (also requires `arm_dangerous()`) |

The default ceiling is **green**: call `set_noise("yellow")` before running
commands on a host (the sliver-ops loop does this explicitly), and
`arm_dangerous()` to unlock `rm`.

## Typical loop

```text
connect()
set_noise("yellow")
start_https_listener(port=443, domain="<redirector>")
generate_beacon(c2_host="<redirector>", os="windows", interval=60, jitter=30)
# … deliver the beacon (payload-delivery / loader-injection-tradecraft) …
poll_events()            # watch for the callback
list_sessions()
execute_command(target_id, "whoami")
export_handoff()         # feed C2 state back to internal-dispatch
```

## Beacons vs sessions

`execute` and the file tools accept either a **session** id (interactive, low
latency) or a **beacon** id (asynchronous — the result returns after the next
check-in, every `interval` ± `jitter` seconds). Use `poll_events()` to watch for
new callbacks and task completion.

## Known limitations (v1)

These reflect the current sliver-py surface, not the design:

- **No client-side SOCKS / port-forward tunnels.** sliver-py does not implement
  the tunnel streaming, so only `list_pivots` (enumerate pivot listeners on a
  session) is exposed. Use the Sliver console for `socks`/`portfwd` for now.
- **No interactive PTY shell.** A streaming PTY can't be a single request/response
  tool; `execute_command` covers command execution.
- **No `cp` / `chmod` / `chown` and no loot/creds store** — not in sliver-py's
  base command set. Planned once upstream exposes them.

## Tests

```bash
./venv/bin/pip install -e '.[dev]'
./venv/bin/pytest          # 64 tests, no live server required
```

The suite mocks sliver-py, so it is green on a clean machine. For a live
end-to-end smoke test, see [`docs/live-test.md`](docs/live-test.md).

## License

MIT. See [`LICENSE`](LICENSE).
