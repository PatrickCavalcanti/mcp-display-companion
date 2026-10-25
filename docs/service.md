# Running it as a service

A companion that dies when a session closes is a demo, not a service. The panel
has no way to tell you the PC stopped listening — the screen keeps its clock and
the weather card — so this has to be right.

## The failure this file exists for

First version, 2026-10-25: the service was started from an agent session
(`python companion.py` in a tool call, then via a `wscript` launcher, then via a
Startup shortcut created by that same session). It worked when published and
stopped working "after a while".

What the evidence said:

- `companion.log` simply stopped, with no `FATAL` line and no Windows
  `Application Error` — so it was not an exception and not a crash;
- the panel's queue kept the touches of the following minutes: the phone was
  fine, the PC was not reading;
- the process was gone from the process list minutes after the launching
  session ended.

Conclusion: the process tree of the agent session was torn down and took the
companion with it. Starting it again from the same place reproduces the bug.

Hence: **the service is started by the operating system**, which owns the
process outside any terminal — Task Scheduler on Windows, `launchd` on macOS,
`systemd --user` on Linux. On all three, what the system starts is `service.py`,
the supervisor: if the companion exits or hangs (the runtime's watchdog kills
itself after 120 s without progress), the supervisor restarts it in seconds and
logs the exit code. The system's own restart-on-failure is the second layer, for
the case where the supervisor itself dies.

## Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-service.ps1
```

It registers the scheduled task `PanelCompanion` (trigger: **at logon**, in
your interactive session — opening Explorer windows and controlling media need
it), with `pythonw.exe` (no console), `MultipleInstances: IgnoreNew`,
`ExecutionTimeLimit: 0` and restart-on-failure. The task is interactive, so it
*ends at logoff* and *starts again at the next logon* — it survives a closed
terminal, a finished agent session and a crash, not a logged-out machine. It also
**removes** the old `PanelCompanion.lnk` from the Startup folder: two companions
would run every touch twice.

When restarting, the installer kills only the supervisor of *this* directory and
its children (matched by the absolute path in the command line, not by file
name), so another Python program that happens to run a `service.py` is safe.

```powershell
# state and processes
Get-ScheduledTask -TaskName PanelCompanion | Select TaskName, State
Get-Content .\companion.log -Tail 5      # 'Heartbeat: ativo' every 60 s
Get-Content .\service.log -Tail 5        # restarts and exit codes

# remove everything
powershell -ExecutionPolicy Bypass -File scripts\install-service.ps1 -Remover
```

## macOS and Linux

```bash
./scripts/install-service.sh            # launchd agent / systemd --user unit
./scripts/install-service.sh --remove
```

- macOS: `~/Library/LaunchAgents/com.mcp-display-companion.plist`, `KeepAlive: true`.
- Linux: `~/.config/systemd/user/mcp-display-companion.service`, `Restart=always`.
- Both run `service.py --config <arquivo>` with quoted paths, so a checkout
  directory with spaces works.
- Logs in `logs/` (and the service's own `companion.log`).

## Never run two of them

Two companions on the same panel fight over the View: each one publishes, the
other gets `presentation não é a sua View aberta agora`, republishes, and the
screen flickers while touches get lost. This really happened here — a supervisor
was stopped while its child survived, and a second install added another pair.

The companion takes an **OS level file lock** (`companion.lock`, released
automatically when the process dies) and writes its PID to `companion.pid`. A
second start logs
`já existe um companheiro rodando com esta configuração (pid …); saindo sem publicar nada`
and exits without publishing. The Windows installer reads `companion.pid` so it
can kill an orphaned child whose supervisor is already gone.

If you ever see `republicando a tela` repeating every minute in the log, look
for a second process before anything else.

## Reading the logs

| File | Written by | What it tells you |
|---|---|---|
| `companion.log` | the service | connection, publish, heartbeat every 60 s, each touch and its result, ignored events and why |
| `service.log` | the supervisor | each start, each exit with its code and how long the process lived |

A healthy service has a heartbeat every `loop.heartbeat_s`. **A gap in the
heartbeats is the bug**: either the service died (see `service.log`) or the loop
is stuck (the watchdog will end it and the supervisor will bring it back).

The panel also tells you: the footer shows **PC offline** in red when it has not
received anything for 150 s.

## Health checklist

1. `python -m mcp_display_companion --check` — config parses, token is found (masked).
2. `python -m mcp_display_companion` in the foreground — publishes, "escutando toques".
3. Touch a button: the log shows `toque: pc_... -> ok`, and the button turns
   green on the panel (confirmation).
4. Install the service, repeat 3 with the terminal closed.

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| `painel recusou o token (401/403)` | token revoked or wrong | pair again, update `secrets.json`, restart the service |
| `sessão MCP desconhecida (404)` | the panel restarted | the service reconnects by itself; if it loops, check `panel.url` |
| touches do nothing, footer says **PC offline** | the service is not running | `Get-ScheduledTask` / `systemctl --user status`; check `service.log` |
| touches work, nothing happens on the PC | action id not in `config.json` (logged as "toque de outra tela ignorado") | add the action, restart |
| button stays yellow, "sem resposta do PC" | the service is up but slow / rate-limited (429) | check the log; the panel accepts 10 calls per 10 s |
