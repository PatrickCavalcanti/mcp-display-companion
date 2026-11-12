# Configuration reference

`config.json` sits next to `service.py` (or wherever `-c/--config` points). It
has **no secret** in it — the panel token lives outside the repository.

Start from `config.example.json`:

```bash
cp config.example.json config.json
python -m mcp_display_companion --check     # validates and prints a summary
```

Every key is optional except `panel.url` and `actions`. Wrong values fail loudly,
with the path of the key that is wrong (`configuração inválida: ações[3] ...`).

## `panel`

| Key | Default | Meaning |
|---|---|---|
| `url` | — (**required**) | MCP endpoint of the panel, `http(s)://host:7777/mcp` |
| `token` | — | token **inline** (avoid: use the secrets file). Kept for a single-file local setup |
| `token_file` | `%APPDATA%\mcp-display-companion\secrets.json` / `~/.config/mcp-display-companion/secrets.json` | JSON file with `{"panel_token": "..."}` |
| `client_name` | `mcp-display-companion` | name sent in `initialize` |
| `protocol_version` | `2025-06-18` | MCP protocol version sent in `initialize` |
| `cursor_file` | `%APPDATA%\mcp-display-companion\cursor.json` | where the event cursor is persisted; if a write fails, the service retries every tick instead of silently losing it |
| `state_file` | next to `cursor_file` | small JSON with what the screen chose and the PC must remember (rest mode on/off) |
| `timeout_s` | `8` | HTTP timeout per call |
| `subscribe` | `true` | subscribe to `panel://events` (SSE). `false` = pure polling |
| `poll_ms` | `700` | polling interval used while the SSE stream is not available (with the stream up, the loop waits 5 s and wakes on each panel notification) |

### Token

Resolution order, first one that exists wins:

1. environment variable **`PANEL_TOKEN`**
2. `panel.token_file` (JSON `{"panel_token": "..."}`)
3. `panel.token` in `config.json`
4. the default secrets file (`%APPDATA%\mcp-display-companion\secrets.json` or
   `~/.config/mcp-display-companion/secrets.json`)

The secrets file should be readable only by you (`icacls <arquivo>
/inheritance:r /grant:r "<usuário>:(F)"` on Windows, `chmod 600` elsewhere).
To rotate: pair the panel again and replace the value in that file — the service
picks it up on the next restart.

## `loop`

| Key | Default | Meaning |
|---|---|---|
| `heartbeat_s` | `60` | a `Heartbeat: ativo` line in the log and a `{"vivo": ...}` to the panel (the screen shows **PC offline** after 150 s without it) |
| `max_event_age_s` | `60` | touches older than this are discarded (`0` disables the check) |
| `confirm` | `true` | send `{"confirmacao": {...}}` back to the View on every touch |
| `log_file` | `companion.log` | path (relative to the config file) |

## `display`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | may use `set_display` |
| `brightness` | `"max"` | `max`, `min`, `auto`, `off` or `0..100`. `off` blanks the screen: the first touch only wakes it (`acordou` event, not a touch) |
| `refresh_min` | `5` | re-applies brightness this often (a phone may reset it) |

## `live.weather`

Weather card on page 1. Uses [open-meteo](https://open-meteo.com/) (no API key).

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | |
| `latitude` / `longitude` | — (required when enabled) | |
| `label` | — (required when enabled) | city name shown on screen |
| `refresh_min` | `15` | |
| `timeout_s` | `4` | |

## `live.media`

Media banner: title, artist, playing state and cover art.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | |
| `interval_s` | `1` | how often the system's media session is read |
| `cover` | `true` | include the album cover (as a `data:` URL) |
| `cover_px` | `160` | cover size sent to the panel |

## `slides` — the presentation page

A page to the **left** of the clock that loops through the images of one folder
(the page only exists when the folder has at least one image).

| Key | Default | Meaning |
|---|---|---|
| `folder` | — | folder with the photos; `~`, `%VAR%` and paths relative to `config.json` work. Setting it turns the page on (the repo ships a `slides/` with **example photos**) |
| `enabled` | `true` when `folder` is set | set `false` to keep the folder configured but hide the page |
| `interval_s` | `20` | seconds per image (minimum 2) |
| `shuffle` | `false` | `true` = random order (a different order at each scan) |
| `width` / `height` | `1384` / `720` | size the photo is cropped and sent at (the panel is 692×360 CSS at 3× density) |
| `quality` | `72` | JPEG quality; if the result passes 240 KB the service retries lower |
| `rescan_s` | `60` | how often the folder is listed again — a photo dropped in there shows up within a minute, without restarting (and the page itself appears/disappears by itself) |
| `caption` | `false` | show the file name over the photo |
| `clock` | `false` | big translucent clock over the photo (the photo stays the subject); the settings page switch overrides it and the choice is remembered |

The photo is the **background of the whole screen** (no card, no frame, no
margins): the page itself is empty and the image is a fixed layer behind
everything, so it fills the panel edge to edge and only the footer (pager dots,
PC status) floats on top of it with a soft dark gradient. A small ring in the
top-right corner empties itself over `interval_s`, showing how long the current
photo still has — it restarts with every image.

```json
"slides": { "folder": "slides", "interval_s": 20, "shuffle": true }
```

Putting a photo in `slides/` is all a user has to do; the page appears as soon as
the folder has one image and disappears when it is empty.

**Where the photos come from.** The photos of the presentation page are **the
user's**: the project ships a few examples in `<project folder>/slides`, which is
what `config.example.json` points at. Either copy the user's photos into that
folder, or point `slides.folder` to one of theirs (e.g.
`%USERPROFILE%\Pictures\Painel`) — an absolute path, `~`, `%VAR%` or a path
relative to `config.json` all work. At startup the service logs one line:

```
slides: 4 photo(s) in C:\Users\me\Pictures\Painel
slides: 6 photo(s) in C:\...\mcp-display-companion\slides (example photos that ship with the project)
```

`jpg/jpeg/png/webp/bmp/gif` are accepted, sorted by name (or shuffled). A file
that cannot be read is skipped instead of freezing the slideshow. Preparing one
image takes ~0.1–0.8 s, so it happens **in a background thread** — the touch loop
is never held by a photo. Needs `pillow` (already in
`requirements-windows.txt`); without it the page is skipped with a line in the log.

## `rest` — rest mode (the settings page)

Same first screen, in pure black and white, no album cover: a wall panel using an
OLED screen and a battery-friendly setup can go dark after a while without a
touch and wake on the first tap.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | the default; the on-screen switch overrides it and the choice is stored on the PC (`state.json`) |
| `idle_s` | `60` | how long without a touch before it goes dark (minimum 5 s) |

Going dark also takes the **device brightness to the minimum** (`set_display
min`) and puts it back to `display.brightness` on the first touch — the panel
does the dimming, the service does the brightness. While the screen is dark the
normal brightness refresh (`display.refresh_min`) is suspended, otherwise it
would light the screen again every few minutes.

The View tells the service this with two **reserved tool names**, handled by the
service rather than by the configuration: `pc_rest_entered` (went dark) and
`pc_rest_left` (woke up). They are not actions and cannot be changed.

Settings page (the `pages: [3]` of an action; `p3` in the View, or `p4` when the home page exists; style it by the class `pagina-ajustes`) — exists when the config has a pair of `rest` actions
(`state: true` and `state: false`). The switch calls the one that matches the
**new** state; the PC stores the value and sends it back on every publish, so it
survives a reload of the screen.

```json
"rest": { "enabled": false, "idle_s": 60 },
"actions": [
  { "id": "pc_descanso_ativar",   "label": "Ativar descanso",   "type": "rest", "state": true,  "pages": [3] },
  { "id": "pc_descanso_desativar", "label": "Desativar descanso", "type": "rest", "state": false, "pages": [3] }
]
```

## `alerts` — Claude Code asking for permission

When any Claude Code session on this PC stops to ask for a **permission**, the
panel shows a big **red** banner over whatever page is visible ("`<project>` pede
permissão", the tool, and what it wants to do), wakes the screen, and drops the
banner by itself as soon as you answer on the PC. With several sessions waiting
the newest shows, with a `+N` badge.

From the panel you can answer too (when the hook runs as `PermissionRequest`):

* **Negar**: one tap.
* **Aprovar**: press and **hold 1.5 s** (a green fill shows the progress; release
  early or move the finger to cancel). It only exists when you can read the whole
  request: if the text was cut ("Texto longo/oculto — aprove no PC") or does not fit
  the box until you scroll it to the end, only Negar and × are offered. The hold
  approves the request that was on screen when you pressed; if another one takes
  its place, the hold is cancelled.
* **×**: hides the banner without answering (the PC dialog stays).

Approving from a wall panel is off by default (`allow_approve: false`: the panel
can only deny). Turn it on only if you accept that whoever can touch the panel can
approve what Claude Code asks to run. The request text is shown in full for `Bash`
up to 1000 characters; a longer command, or a `Bash` call with unusual options
(for example `dangerouslyDisableSandbox`), is marked as cut and cannot be approved
from the panel. Other tools show their input as compact JSON up to 600
characters. MCP tools show as `<server>: <operation>`.

Without `PermissionRequest` (only the `Notification` fallback) the banner is just
a warning: a tap on it hides it.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | watch the alerts folder |
| `dir` | `%APPDATA%\mcp-display-companion\alerts` / `~/.config/mcp-display-companion/alerts` (env `PANEL_COMPANION_ALERTS` overrides the default) | folder where the hook drops its files. **The hook only reads the env var `PANEL_COMPANION_ALERTS`**, never `config.json`: if you set `alerts.dir`, set the same path in that env var for Claude Code too (the service logs a warning at startup when `alerts.dir` is set) |
| `max_age_s` | `1800` | older files are ignored and deleted (a hook that never saw the answer must not leave the banner up forever; minimum 10). A permission **request** is dropped after 300 s whatever this value is |
| `wake` | `true` | on a **new** alert: leave rest mode on the View and set the brightness below |
| `brightness` | same as `display.brightness` | `max`, `min`, `auto`, `off` or `0`..`100`, used while an alert shows (needs `display.enabled`) |
| `allow_approve` | `false` | `true`: the panel may **approve** (hold the Aprovar button). `false`: it can only deny (the button is hidden and the service never writes `allow`) |

> **Security.** The alerts folder is a trust boundary: whoever can write
> `*.answer` files there can approve tool calls. Keep it in a private per-user
> folder; **never** put it in a shared, synced or network folder (OneDrive, Dropbox,
> a share), or other people and devices could approve what Claude Code runs. The
> service also accepts answers only from the View it published itself and only with
> the per-alert secret it sent to that View (kept in memory, never written to disk).

The folder is filled by `scripts/claude_alert_hook.py` (standard library only).
Register it in `~/.claude/settings.json`.

> **Test one terminal (CLI) session first.** The Claude desktop app shows its own
> dialog and races the hook, so either answer wins. The CLI may instead *wait* for
> the `PermissionRequest` hook (up to its timeout) before showing the prompt. Register
> the hook, run a command that asks for permission in a terminal session, and check
> that the prompt still shows up promptly before you keep it registered globally.

Do **not** call the script directly (`python script.py`): if the file is missing,
Python exits with code **2**, and Claude Code reads exit code 2 from a hook as
"block / deny". Use this launcher instead: it exits 0 with no output when the
script does not exist, and passes the script's own exit 0 through (use forward
slashes in the path; `-I -S` keeps Python fast and isolated):

* for the clear and `Notification` hooks (asynchronous):

```
C:/Python313/python.exe -I -S -c "import os,runpy,sys; p=r'C:/path/to/scripts/claude_alert_hook.py'; os.path.isfile(p) or sys.exit(0); runpy.run_path(p, run_name='__main__')"
```

* for the `PermissionRequest` hook (synchronous, timeout above the hook's own 290 s
  wait, `onFailure` continue so a crash falls back to the normal PC dialog): the
  same command line, with `"timeout": 300` and `"onFailure": "continue"`.

```json
{
  "hooks": {
    "PermissionRequest": [{ "hooks": [{ "type": "command", "timeout": 300, "onFailure": "continue",
      "command": "C:/Python313/python.exe -I -S -c \"import os,runpy,sys; p=r'C:/path/to/scripts/claude_alert_hook.py'; os.path.isfile(p) or sys.exit(0); runpy.run_path(p, run_name='__main__')\"" }] }],
    "Notification": [{ "matcher": "permission_prompt", "hooks": [{ "type": "command", "async": true,
      "command": "...same command..." }] }],
    "PostToolUse":        [{ "hooks": [{ "type": "command", "async": true, "command": "...same command..." }] }],
    "PostToolUseFailure": [{ "hooks": [{ "type": "command", "async": true, "command": "...same command..." }] }],
    "UserPromptSubmit":   [{ "hooks": [{ "type": "command", "async": true, "command": "...same command..." }] }],
    "Stop":               [{ "hooks": [{ "type": "command", "async": true, "command": "...same command..." }] }],
    "SessionEnd":         [{ "hooks": [{ "type": "command", "async": true, "command": "...same command..." }] }]
  }
}
```

If `alerts.dir` is set (see the table), also set `PANEL_COMPANION_ALERTS` to the same
path in the environment Claude Code runs in.

How it works:

* `PermissionRequest` writes `<session>-<h>-<random>.json` (`{session, project,
  message, at, request: {tool, summary, truncated}, actionable: true}`; `h` is a
  short hash of the tool call) and waits for the same name with `.answer`
  (`allow` or `deny`). On an answer it prints the decision JSON for Claude Code and
  removes both files. The Claude desktop app also shows its own dialog; whichever
  answers first wins. If you answer on the PC, the clear events below delete the
  file, and the hook exits with no output. `ExitPlanMode`, `AskUserQuestion` and
  `mcp__ccd_*` tools are never offered for approval (the hook does nothing).
* `Notification` writes the plain `<session>.json`, unless a request alert of that
  session already exists.
* `PostToolUse` and `PostToolUseFailure` delete `<session>.json` and only the
  request files of that same tool call (same `h`), so parallel calls and subagents
  of one session keep their own banners. `UserPromptSubmit`, `Stop` and
  `SessionEnd` delete every file of that session.
* The hook prints nothing except a decision, and always exits 0. Test knob: env
  `PANEL_COMPANION_ALERT_WAIT` (seconds) replaces the 290 s wait.

The View sends the reserved tools `pc_alerta_dispensar` (arguments `{id}`) and
`pc_alerta_responder` (arguments `{id, decisao: "allow"|"deny"}`). Like `pc_rest_*`
they are handled by the service and need no entry in `actions`. The id must
match `[A-Za-z0-9_-]{1,80}` and is required; dismiss only deletes `<id>.json` of a
known alert and respond only writes `<id>.answer`, both inside `dir`, and respond
only for an existing, fresh (under 300 s), actionable alert, with the `nonce` that
the service sent for it. Both are ignored unless the touch comes from the View
the service published (`apresentacao`). The list reaches the View as
`update_view({"alertas": [{id, project, message, at, actionable?, nonce?, request?}, ...],
"allow_approve": false})` (empty list when none) and is sent again after every
reconnect or republish. It never republishes the View by itself: if the panel
rejects the list, it retries the same list every 30 s and logs once. Files left
behind (`*.answer` without a request, `.*.tmp` older than 60 s) are swept. Custom templates need the `#alerta` banner and the
`aplicarAlertas` logic of `templates/painel.html`; `simularAlertas([...])` in a
browser console previews it.

## `home` — home control page (Home Assistant)

An optional page between the shortcuts and the settings: air conditioner (power,
target temperature, room temperature) and a light, for one room. The buttons call
the Home Assistant MCP tools **directly from the panel** (a "mixed screen": the
companion publishes the View with `connection`, and the panel sends the calls the
destination knows straight to it), so they keep working with the PC off. If Home
Assistant is unreachable the page says so and dims.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | the page exists only when `true` |
| `connection` | `"home-assistant"` | name of the MCP connection approved on the phone (the destination) |
| `title` | `"Home"` | title of the page |
| `climate` | `"Air conditioner"` | friendly name of the climate entity |
| `light` | `"Lamp"` | friendly name of the light entity |
| `area` | `"Home"` | kept for compatibility; no longer used (the state is read by name and domain) |
| `refresh_s` | `30` | seconds between state refreshes (minimum 5); a refresh also runs after each action |
| `temp_min` / `temp_max` | `16` / `30` | limits of the − / + buttons (1 °C steps) |

Page order is slideshow (`p0`), clock (`p1`), shortcuts (`p2`), **home (`p3`)**,
settings (`p4`, or `p3` when there is no home page); each exists only when it has content. The tools used (prefixed
as the Home Assistant server publishes them) are `intent__HassTurnOn`,
`intent__HassTurnOff`, `climate__HassClimateSetTemperature` and
`homeassistant__GetLiveContext`. If the panel cannot reach the destination it
turns such a call into an event for the companion, which only logs
`home assistant unreachable: <tool>` (failed state refreshes are logged at most once per 10 minutes). The entities must be **exposed to Assist** in Home Assistant to appear. A custom `view.template` must contain the
`{{PAGINA_CASA}}` placeholder (after `{{PAGINA_ATALHOS}}`) to show the page; without it the page is dropped (and the service logs `template has no {{PAGINA_CASA}}; home page disabled`).

```json
"home": { "enabled": true, "connection": "home-assistant", "title": "Home", "climate": "Air conditioner", "light": "Lamp", "area": "Home" }
```

## `view`

| Key | Default | Meaning |
|---|---|---|
| `template` | the one shipped in the package | another `painel.html`-style template |
| `title` | `"Panel"` | name of the View on the phone (data from you, never translated) |
| `language` | `"en"` | language of the panel's own texts: `"en"` or `"pt-BR"`. Covers the fixed texts on the screen (page titles, status line, settings, Home page, alert banner), the day and month names and the date format. Action labels come from your `actions` and are shown as you wrote them; they are not translated. Any other value is a configuration error. |
| `show_clock` | `true` | clock card on page 1 |
| `show_weather` | `true` | weather card on page 1 (also needs `live.weather.enabled`) |

Page 1 adapts to what exists: clock card, weather card and the featured button —
any combination lays out sensibly, and `show_clock: false` / `show_weather: false`
really removes the card. With no page-2 action, the panel becomes a single page
(no pager dots, no swipe).

Page 1 has one slot for an action, the featured one. `"page": 1` without
`"featured": true` is a configuration error, with that message — use
`"pages": [1, 2]` to have the same action on both pages.

## `actions`

A list; each item becomes a button (and a tool name the View may call). See
[actions.md](actions.md) for every type.

| Key | Default | Meaning |
|---|---|---|
| `id` | — (**required**) | the tool name, `[a-z0-9_]`, starting with a letter. Use a `pc_` prefix |
| `label` | the id | text under the button |
| `type` | — (**required**) | `media`, `key`, `volume`, `folder`, `app`, `url`, `command`, `display` |
| `page` / `pages` | `2` | `2`, `[1, 2]`, `1` (which **requires** `featured`) or `3` (settings page — `rest` actions only) |
| `featured` | `false` | the big button on page 1 — page 1 has exactly one slot |
| `accent` | `false` | tinted (purple) button |
| `icon` | type dependent | name from `mcp_display_companion/icons.py` |
| `icon_svg` | — | inline SVG content for a custom icon |
| `color` | — | `#rrggbb` for the icon tile |

Ids must be unique. A touch with an id that is not here is logged and ignored —
that is what keeps the service from running anything the panel feels like
sending.

## Paths

`folder.path`, `cursor_file`, `log_file`, `view.template`, `app.target` and
`command.cwd` expand `%USERPROFILE%` and `$HOME`/`${HOME}`, plus `~` — and `%VAR%`
is expanded on any operating system, so the same `config.json` works on Windows,
macOS and Linux. Relative paths resolve against the directory of `config.json`,
not against where the service was started.

Application targets that are not paths (`spotify:`, `ms-settings:`, `notepad.exe`)
are passed through untouched.
