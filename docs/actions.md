# Action types

Every entry of `actions` in `config.json` is one button, and the `id` is the tool
name the View calls when it is touched. Fields can be at the top level of the
action (`"command": "next"`) or grouped in `"params": {...}` — both work, and
`params` wins.

Nothing outside this list runs: an unknown id is logged and ignored.

## `media` — playback transport

```json
{ "id": "pc_midia_proxima", "label": "Próxima", "type": "media", "command": "next", "icon": "next" }
```

| Field | Values |
|---|---|
| `command` | `play_pause`, `next`, `previous` |

On Windows it uses the system media session (GSMTC), which reaches Spotify (or
whatever is playing) **without caring which window has focus**. If that fails or
`winrt` is missing, it falls back to the media keys. The media session is also
what fills the banner (title, artist, cover) when `live.media.enabled` is true.

## `key` — one virtual key

```json
{ "id": "pc_f5", "label": "F5", "type": "key", "vk": 116, "icon": "terminal" }
```

| Field | Values |
|---|---|
| `vk` | 1–255, the Windows virtual-key code (`116` = F5, `0xB3` = play/pause) |

Use it for anything without a dedicated type. Keys go to the focused window.

## `volume`

```json
{ "id": "pc_volume_mais", "label": "Vol +", "type": "volume", "direction": "up", "icon": "volume_up" }
```

| Field | Values |
|---|---|
| `direction` | `up`, `down`, `mute` |

## `folder` — open a folder in the file manager

```json
{ "id": "pc_abrir_downloads", "label": "Downloads", "type": "folder",
  "path": "%USERPROFILE%/Downloads", "icon": "download", "color": "#38bdf8" }
```

| Field | Values |
|---|---|
| `path` | absolute path, `~`, `%VARS%`; a relative path is resolved against the `config.json` directory |

**Windows:** if an Explorer window is already open, it *navigates* that window
(Shell COM) instead of opening a second one — with a 4-second ceiling, so a
stuck COM call can never freeze the touch loop; on timeout it falls back to
`explorer.exe`. A missing folder is reported as a failed action, not silence.

## `app` — launch an app or protocol

```json
{ "id": "pc_abrir_spotify", "label": "Spotify", "type": "app", "target": "spotify:", "icon": "spotify" }
```

| Field | Values |
|---|---|
| `target` | an executable (`notepad.exe`), a file, or a protocol (`spotify:`, `ms-settings:`) |

Protocols are passed through untouched; paths are expanded.

## `url` — open a URL

```json
{ "id": "pc_abrir_youtube", "label": "YouTube", "type": "url",
  "url": "https://www.youtube.com", "browser": "chrome", "new_window": true,
  "icon": "youtube", "color": "#ff0000" }
```

| Field | Values |
|---|---|
| `url` | must start with `http(s)://` |
| `browser` | `chrome`, `edge`, `firefox` (Windows lookup) or empty for the default browser |
| `new_window` | opens a new window instead of a tab |

## `command` — a command line declared here

```json
{ "id": "pc_bloquear", "label": "Bloquear", "type": "command",
  "cmd": ["rundll32.exe", "user32.dll,LockWorkStation"], "icon": "power" }
```

| Field | Values |
|---|---|
| `cmd` | list of strings (a single string is accepted and wrapped) |
| `cwd` | optional working directory (relative paths against the config directory) |
| `detached` | default `true` — the service never waits for the command either way; the flag only asks the OS to detach the process (Windows: `DETACHED_PROCESS`) |

This is the escape hatch, and it is deliberately **not** parameterised by the
event: the command comes from your configuration, never from the panel.

## `display` — brightness of the panel itself

```json
{ "id": "pc_brilho_min", "label": "Descanso", "type": "display", "brightness": "min", "icon": "power" }
```

| Field | Values |
|---|---|
| `brightness` | `max`, `min`, `auto`, `off`, or `0..100` |

Prefer `min` over `off` for a rest mode: with `off` the first touch only wakes
the screen (`acordou` event) and does not reach your button.

## `rest` — rest mode switch

```json
{ "id": "pc_descanso_ativar",   "label": "Ativar descanso",   "type": "rest", "state": true,  "pages": [3] }
{ "id": "pc_descanso_desativar", "label": "Desativar descanso", "type": "rest", "state": false, "pages": [3] }
```

| Field | Values |
|---|---|
| `state` | `true` = this action turns rest mode **on**, `false` = turns it off |

These two actions **are** the settings page: with the pair configured, page 3
shows one big switch whose label and state come from here, and the choice is
stored on the PC (`state.json`) so it survives the screen being reloaded. What
the screen actually does in rest mode (black, white text, no cover, wake on
touch) comes from the `rest` section — see
[configuration.md](configuration.md#rest--rest-mode-the-settings-page).

## `overlay` — the clock over the photos

```json
{ "id": "pc_relogio_fotos_ativar",   "type": "overlay", "state": true,  "pages": [3] }
{ "id": "pc_relogio_fotos_desativar", "type": "overlay", "state": false, "pages": [3] }
```

| Field | Values |
|---|---|
| `state` | `true` = show the big translucent clock over the presentation photos, `false` = hide it |

Like `rest`, it is a settings-page switch: with the pair configured, page 3 shows
a second row, the choice lives on the PC (`state.json`) and survives a reload of
the screen. The `slides.clock` value is only the starting point.

### Reserved names

The View also calls two names that are **not** actions, handled by the service
when the `rest` section exists: `pc_rest_entered` and `pc_rest_left`. They
exist so the screen can dim the device (`set_display min`) when it goes dark and
restore `display.brightness` when it wakes. Do not reuse those ids in `actions`.

## Presentation fields

| Field | Effect |
|---|---|
| `page` / `pages` | `1`, `2`, `[1, 2]` or `3` — `[1, 2]` puts the same action on the featured slot and in the grid; page 3 is the settings page (`rest` only) |
| `featured` | the big button on page 1; for `media` it becomes the banner with cover and track |
| `accent` | purple filled button instead of the neutral card |
| `icon` | name from `mcp_display_companion/icons.py` (`play`, `pause`, `playpause`, `prev`, `next`, `stop`, `volume_up`, `volume_down`, `mute`, `spotify`, `youtube`, `chrome`, `folder`, `download`, `city`, `home`, `terminal`, `power`, `music`, `dot`) |
| `icon_svg` | inline SVG content (a `viewBox="0 0 24 24"` drawing) |
| `color` | `#rrggbb` for the icon tile background and glyph |

## Adding a new kind of action

1. Add the type to `ACTION_TYPES` in `mcp_display_companion/config.py` and validate its
   fields in `_validate_action_params`.
2. Add `_run_<type>` to `ActionRunner` in `mcp_display_companion/actions.py`.
3. If it touches the operating system, add the primitive to `Host` in
   `host.py` (and implement it for Windows in `WindowsHost`).
4. Add a test with a fake host — that is the whole reason `Host` exists.
