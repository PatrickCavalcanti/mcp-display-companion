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
| `folder` | — | folder with the photos; `~`, `%VAR%` and paths relative to `config.json` work. Setting it turns the page on (the repo ships an empty `slides/`) |
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

Page 3 — the settings page — exists when the config has a pair of `rest` actions
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

## `view`

| Key | Default | Meaning |
|---|---|---|
| `template` | the one shipped in the package | another `painel.html`-style template |
| `title` | `"Painel"` | |
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
