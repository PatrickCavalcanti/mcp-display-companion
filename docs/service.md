# Running it as a service

A companion that dies when a session closes is a demo, not a service. The panel
has no way to tell you the PC stopped listening — the screen keeps its clock and
the weather card — so this has to be right.

## Why this exists

If nobody on the computer is listening, a touch on the panel becomes an event in
a queue that nobody reads: the screen looks alive and nothing happens. An agent
session can read those events for a while, and dies with the session, silently.
This service is the boring part that stays up: it publishes its own screen,
listens, acts, confirms, and says out loud (in its log and on the panel) when it
is not there.

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
automatically when the process dies) and writes its PID to `companion.pid`. The
lock is **per user**, in the same folder as the token saved by `--set-token`
(`%APPDATA%\mcp-display-companion\companion.lock` on Windows,
`~/.config/mcp-display-companion/companion.lock` elsewhere) — *not* next to
`config.json`, so a second clone in another folder is refused too.
`PANEL_COMPANION_LOCK` overrides the path. `companion.owner.json` records who
holds it (pid and folder), and a second start logs
`another companion is already running (pid …, folder …)`
and exits without publishing. The Windows installer reads `companion.pid` — in
the repository folder *and* next to the token — so it can kill an orphaned child
whose supervisor is already gone.

The **supervisor** keeps a lock of its own,
`…/mcp-display-companion/supervisor/companion.lock`, so a second supervisor
started from another folder logs `another supervisor is already active` and
exits even while its child is between restarts. It lives in a subfolder on
purpose: `InstanceLock` writes `companion.pid` beside its lock file, and that
pid has to stay the companion's.

If the panel keeps rejecting `update_view` (`presentation não é a sua View aberta
agora`), the service republishes with backoff — a couple of attempts, spaced out
— and then stops for a while, logging
`outro agente/serviço está usando o painel`. The screen stops jumping back to the
first page; look for a second process before anything else.

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
2. `python -m mcp_display_companion` in the foreground — publishes, then logs `listening for panel touches`.
3. Touch a button: the log shows `touch: pc_... (ev #1) -> ok`, and the button turns
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

## What the service does with events

| Event | What the service does |
|---|---|
| `toque` with a known action | runs it, then confirms with `update_view` so the button turns green without reloading |
| `toque` of another connection (`destino_indisponivel`) or unknown name | logs and ignores: the button is not ours |
| `toque` older than `loop.max_event_age_s` | ignores (opening a folder from 20 minutes ago is worse than not opening) |
| `acordou` (screen was off and got touched) | logs only; does not touch brightness again |
| `lacuna: true` (the panel may have dropped events) | does not replay actions; republishes live data |

The cursor is persisted (`cursor.json`), so a restart does not repeat taps.
First read after a cold start only takes the cursor: the panel's queue may hold
touches from hours ago.

## Upgrading from an earlier version

- **Opening a browser and opening a folder now use a new window.** A `url`
  action behaves like `--new-window` and a `folder` action like Win+E, instead of
  reusing the window already open. For the old behavior, set
  `"new_window": false` on the `url` action, or `"reuse_window": true` on the
  `folder` action ([actions.md](actions.md)).
- **The panel is in English by default since 1.1.** To keep Portuguese, set
  `"language": "pt-BR"` under `view` in `config.json`.
- **A custom `view.template` copied from an earlier version still renders**, but
  it keeps its own fixed texts and ignores `view.language`. To get the language,
  make the copy again from the current template.
- **The permission hook needs the service's sign of life.** Since this version the
  hook only waits for an answer while the service renews `.service-alive` in the
  alerts folder; with an older service (no sign) it never offers the banner and
  Claude Code shows its normal dialog. The hook and the service come from the same
  repository: update both together.
- **The single instance lock is per user now** (next to the token), so a second
  clone in another folder no longer starts alongside the first.

After you update the repository, **restart the service**; running the installer
again does that. Without a restart the old service keeps running, it writes no sign
of life, and the new hook never offers the banner.

After `git pull`, **re-run the installer** instead of only restarting the task:
`powershell -ExecutionPolicy Bypass -File scripts\install-service.ps1` (Windows)
or `./scripts/install-service.sh` (macOS/Linux). It is idempotent and installs
nothing, but it stops the old supervisor **and its child**. Restarting only the
task can leave the old child alive, and that child holds the *old* per-folder
lock, which the new version no longer looks at.

## Limits

- Windows-only for media metadata/cover, media and volume buttons, and Explorer
  navigation. On macOS and Linux the generic host opens folders, URLs, apps and
  commands; `press_key`, media and volume are not implemented there and the
  buttons report a failed action.
- Polling fallback is 0.7 s; with `subscribe` the panel pushes and the touch
  reaches the action in ~40 ms.
- The panel limits `update_view`/`set_display` to 10 calls per 10 s; the service
  batches live data and skips unchanged values.
- Weather comes from open-meteo (no key); failure is silent and the card keeps
  the last value.
