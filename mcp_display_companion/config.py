"""Configuration and secrets for the companion service.

Two rules that apply across the entire project:

- **Token never in code.** It comes from `panel.token_file`
  (`%APPDATA%\\mcp-display-companion\\secrets.json` on Windows,
  `~/.config/mcp-display-companion/secrets.json` elsewhere) or from the
  `PANEL_TOKEN` environment variable, in this order: env > file.
- **`config.json` can be committed to git** (no secrets); `secrets.json`
  never. The repository provides `config.example.json` as a template.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Action types that the service knows how to execute. See docs/actions.md.
ACTION_TYPES = {
    "media",    # play/pause, next, previous (Windows: GSMTC; otherwise: media keys)
    "key",      # presses a virtual key (e.g. volume)
    "volume",   # volume + / - / mute (media keys)
    "folder",   # opens a folder in Explorer/Finder
    "app",      # opens/activates an application or protocol (e.g. spotify:)
    "url",      # opens a URL in browser
    "command",  # runs a command declared in configuration
    "display",  # adjusts panel screen brightness
    "rest",     # toggles rest mode (black screen after idle time)
    "overlay",  # toggles large translucent clock over photos
}

BYTE_UNITS = {"KB": 1024, "MB": 1024 ** 2}

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
PAGES = (1, 2, 3)
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ConfigError(Exception):
    """Configuration error: the message is intended for the user to read and fix."""


# `%VAR%` is Windows syntax, but configuration is the same across all OSes:
# expanding here keeps behavior uniform (and testable) on Linux.
PERCENT_VAR_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")


def _expand_percent(value: str) -> str:
    return PERCENT_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


def expand_path(value: str) -> Path:
    """Expands `~`, `%VAR%`, and `$VAR` and returns an absolute Path."""
    text = os.path.expandvars(_expand_percent(str(value)))
    return Path(os.path.expanduser(text)).expanduser()


def default_secrets_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "mcp-display-companion" / "secrets.json"


def default_template() -> Path:
    """The template bundled with the package (the user can point to another)."""
    return Path(__file__).resolve().parent / "templates" / "painel.html"


def default_cursor_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "mcp-display-companion" / "cursor.json"


# --------------------------------------------------------------------------- #
# JSON loading
# --------------------------------------------------------------------------- #

def _load_json(path: Path, what: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{what} not found: {path}") from None
    except OSError as exc:
        raise ConfigError(f"could not read {what} ({path}): {exc}") from None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ConfigError(f"{what} is not valid JSON ({path}): {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{what} must be a JSON object ({path})")
    return data


def _get(obj: dict[str, Any], key: str, default: Any = None) -> Any:
    value = obj.get(key, default)
    return default if value is None else value


def _as_int(value: Any, what: str, minimum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{what} must be an integer, got {value!r}") from None
    if minimum is not None and number < minimum:
        raise ConfigError(f"{what} must be >= {minimum}, got {number}")
    return number


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Action:
    """A screen button: `id` is the name of the tool called by View on touch."""

    id: str
    label: str
    type: str
    icon: str = "dot"
    color: str = ""
    icon_svg: str = ""
    pages: tuple[int, ...] = (2,)
    featured: bool = False
    accent: bool = False
    params: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        extra = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.id} ({self.type}{', ' + extra if extra else ''})"


def parse_action(raw: Any, index: int) -> Action:
    if not isinstance(raw, dict):
        raise ConfigError(f"actions[{index}] must be an object")
    action_id = str(raw.get("id", ""))
    if not ID_RE.match(action_id):
        raise ConfigError(
            f"actions[{index}].id invalid: {action_id!r} "
            "(use lowercase letters, numbers, and _, starting with a letter; e.g.: pc_open_downloads)"
        )
    action_type = str(raw.get("type", "")).strip().lower()
    if action_type not in ACTION_TYPES:
        raise ConfigError(
            f"actions[{index}] ({action_id}): unknown type {action_type!r}; "
            f"use one of {sorted(ACTION_TYPES)}"
        )

    params: dict[str, Any] = dict(raw.get("params") or {})
    # convenience fields at action level (params level takes precedence)
    for key in ("command", "vk", "direction", "path", "target", "url", "browser",
                "new_window", "cmd", "cwd", "detached", "brightness", "state"):
        if key in raw and key not in params:
            params[key] = raw[key]

    _validate_action_params(action_id, action_type, params)

    pages_raw = raw.get("pages", raw.get("page", 2))
    if isinstance(pages_raw, int):
        pages_raw = [pages_raw]
    if not isinstance(pages_raw, (list, tuple)) or not pages_raw:
        raise ConfigError(f"actions[{index}].page/pages must be 1, 2 or [1, 2]")
    pages: list[int] = []
    for item in pages_raw:
        page = _as_int(item, f"actions[{index}].pages")
        if page not in PAGES:
            raise ConfigError(
                f"actions[{index}].pages accepts 1, 2 and 3 (got {page}); "
                "page 3 is the settings page and only accepts `rest` and `overlay` actions"
            )
        if page not in pages:
            pages.append(page)

    color = str(raw.get("color", "") or "")
    if color and not COLOR_RE.match(color):
        raise ConfigError(f"actions[{index}].color must be #rrggbb (got {color!r})")

    return Action(
        id=action_id,
        label=str(raw.get("label") or action_id),
        type=action_type,
        icon=str(raw.get("icon") or ("dot" if action_type != "media" else "playpause")),
        color=color,
        icon_svg=str(raw.get("icon_svg") or ""),
        pages=tuple(sorted(pages)),
        featured=bool(raw.get("featured", False)),
        accent=bool(raw.get("accent", False)),
        params=params,
    )


def _validate_action_params(action_id: str, action_type: str, params: dict[str, Any]) -> None:
    def require(name: str, kind: type, hint: str = "") -> Any:
        if name not in params:
            raise ConfigError(f"action {action_id}: missing '{name}'{(' - ' + hint) if hint else ''}")
        value = params[name]
        if not isinstance(value, kind):
            raise ConfigError(f"action {action_id}: '{name}' must be {kind.__name__}, got {value!r}")
        return value

    if action_type == "media":
        command = str(params.get("command", "")).lower()
        if command not in {"play_pause", "next", "previous"}:
            raise ConfigError(
                f"action {action_id}: media.command must be play_pause, next or previous "
                f"(got {command!r})"
            )
        params["command"] = command
    elif action_type == "key":
        vk = require("vk", int, "virtual key code, e.g. 175 = volume +")
        if not 0 < vk < 256:
            raise ConfigError(f"action {action_id}: vk outside 1..255 (got {vk})")
    elif action_type == "volume":
        direction = str(params.get("direction", "")).lower()
        if direction not in {"up", "down", "mute"}:
            raise ConfigError(
                f"action {action_id}: volume.direction must be up, down or mute "
                f"(got {direction!r})"
            )
        params["direction"] = direction
    elif action_type in {"folder", "app"}:
        require("path" if action_type == "folder" else "target", str)
    elif action_type == "url":
        url = require("url", str)
        if not re.match(r"^https?://", url):
            raise ConfigError(f"action {action_id}: url must start with http(s):// (got {url!r})")
    elif action_type == "command":
        cmd = params.get("cmd")
        if isinstance(cmd, str):
            params["cmd"] = [cmd]
        elif not (isinstance(cmd, list) and cmd and all(isinstance(p, str) for p in cmd)):
            raise ConfigError(
                f"action {action_id}: command.cmd must be a list of strings, e.g.: [\"notepad.exe\"]"
            )
    elif action_type == "rest":
        if not isinstance(params.get("state"), bool):
            raise ConfigError(
                f"action {action_id}: rest.state must be true (turn rest on) "
                "or false (turn off)"
            )
    elif action_type == "overlay":
        if not isinstance(params.get("state"), bool):
            raise ConfigError(
                f"action {action_id}: overlay.state must be true (show clock "
                "over photos) or false (hide)"
            )
    elif action_type == "display":
        brightness = params.get("brightness", "max")
        if isinstance(brightness, int):
            if not 0 <= brightness <= 100:
                raise ConfigError(f"action {action_id}: display.brightness integer must be between 0 and 100")
        elif brightness not in {"max", "min", "auto", "off"}:
            raise ConfigError(
                f"action {action_id}: display.brightness must be max, min, auto, off or 0..100"
            )
        params["brightness"] = brightness


@dataclass(frozen=True)
class PanelSection:
    url: str
    token: str
    client_name: str = "mcp-display-companion"
    protocol_version: str = "2025-06-18"
    cursor_file: Path = field(default_factory=default_cursor_path)
    timeout_s: float = 8.0
    subscribe: bool = True
    poll_ms: int = 700


@dataclass(frozen=True)
class LoopSection:
    heartbeat_s: int = 60
    max_event_age_s: int = 60
    confirm: bool = True
    log_file: Path = field(default_factory=lambda: Path("companion.log"))


@dataclass(frozen=True)
class DisplaySection:
    enabled: bool = True
    brightness: str = "max"
    refresh_min: int = 5


@dataclass(frozen=True)
class WeatherSection:
    enabled: bool = False
    latitude: float = 0.0
    longitude: float = 0.0
    label: str = ""
    refresh_min: int = 15
    timeout_s: float = 4.0


@dataclass(frozen=True)
class MediaSection:
    enabled: bool = False
    interval_s: float = 1.0
    cover: bool = True
    cover_px: int = 160


@dataclass(frozen=True)
class SlidesSection:
    """Slideshow page: a folder of images on loop."""

    enabled: bool = False
    folder: Path = field(default_factory=lambda: Path("slides"))
    interval_s: int = 20
    shuffle: bool = False
    width: int = 1384
    height: int = 720
    quality: int = 72
    rescan_s: int = 60
    caption: bool = False
    clock: bool = False


@dataclass(frozen=True)
class RestSection:
    """Rest mode: the same page 1, in black and white, after an idle period."""

    enabled: bool = False
    idle_s: int = 60


@dataclass(frozen=True)
class ViewSection:
    template: Path = field(default_factory=default_template)
    title: str = "Painel"
    show_clock: bool = True
    show_weather: bool = True


@dataclass(frozen=True)
class Config:
    panel: PanelSection
    actions: list[Action]
    loop: LoopSection = field(default_factory=LoopSection)
    display: DisplaySection = field(default_factory=DisplaySection)
    rest: RestSection = field(default_factory=RestSection)
    slides: SlidesSection = field(default_factory=SlidesSection)
    state_file: Path = field(default_factory=lambda: Path("state.json"))
    weather: WeatherSection = field(default_factory=WeatherSection)
    media: MediaSection = field(default_factory=MediaSection)
    view: ViewSection = field(default_factory=ViewSection)
    base_dir: Path = field(default_factory=Path.cwd)

    @property
    def actions_by_id(self) -> dict[str, Action]:
        return {a.id: a for a in self.actions}

    @property
    def page1(self) -> list[Action]:
        return [a for a in self.actions if 1 in a.pages]

    @property
    def page2(self) -> list[Action]:
        return [a for a in self.actions if 2 in a.pages]

    @property
    def page3(self) -> list[Action]:
        return [a for a in self.actions if 3 in a.pages]

    @property
    def featured(self) -> Action | None:
        for action in self.actions:
            if action.featured:
                return action
        return None


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #

def save_token(token: str, path: Path | None = None) -> Path:
    """Saves the token to the secrets file (outside the repository).

    User-only permissions: `chmod 600` on POSIX and `icacls` on Windows
    (best effort -- on FAT/exFAT the command fails and the file remains).
    """
    token = str(token).strip()
    if not token:
        raise ConfigError("empty token")
    dest = Path(path) if path else default_secrets_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"panel_token": token}, indent=2) + chr(10), encoding="utf-8")
    if os.name == "nt":
        user = os.environ.get("USERNAME", "")
        try:
            subprocess.run(
                ["icacls", str(dest), "/inheritance:r", "/grant:r", f"{user}:(F)"],
                check=False, capture_output=True,
            )
        except OSError:
            pass
    else:
        try:
            dest.chmod(0o600)
        except OSError:
            pass
    return dest


def load_token(panel_raw: dict[str, Any], base_dir: Path) -> str:
    """Panel token: `PANEL_TOKEN` > `panel.token_file` > `panel.token`.

    The latter is the lowest precedence and exists only for single local files --
    the repository must never have one.
    """
    env = os.environ.get("PANEL_TOKEN", "").strip()
    if env:
        return env

    token_file = panel_raw.get("token_file")
    if token_file:
        path = expand_path(token_file)
        if not path.is_absolute():
            path = base_dir / path
        data = _load_json(path, "secrets file")
        token = str(data.get("panel_token") or data.get("token") or "").strip()
        if not token:
            raise ConfigError(
                f"{path} exists but has no 'panel_token' "
                '({"panel_token": "<panel token>"})'
            )
        return token

    token = str(panel_raw.get("token") or "").strip()
    if token:
        return token

    default = default_secrets_path()
    if default.exists():
        data = _load_json(default, "secrets file")
        token = str(data.get("panel_token") or data.get("token") or "").strip()
        if token:
            return token

    raise ConfigError(
        "missing panel token. Place in "
        f"{default} as {{\"panel_token\": \"...\"}}, "
        "or define PANEL_TOKEN in the environment. The token is the one the agent uses "
        "to talk to the panel (not the MCP token of another connection)."
    )


def load(path: str | Path) -> Config:
    """Reads config.json, validates, and returns the Config object."""
    config_path = expand_path(str(path))
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    raw = _load_json(config_path, "configuration file")
    base_dir = config_path.parent

    panel_raw = raw.get("panel")
    if not isinstance(panel_raw, dict):
        raise ConfigError("missing 'panel' section with panel url")
    url = str(panel_raw.get("url", "")).strip()
    if not re.match(r"^https?://", url):
        raise ConfigError("panel.url must start with http:// or https://")

    token = load_token(panel_raw, base_dir)

    cursor_file = panel_raw.get("cursor_file")
    cursor_path = expand_path(cursor_file) if cursor_file else default_cursor_path()
    if not cursor_path.is_absolute():
        cursor_path = base_dir / cursor_path

    panel = PanelSection(
        url=url,
        token=token,
        client_name=str(panel_raw.get("client_name") or "mcp-display-companion"),
        protocol_version=str(panel_raw.get("protocol_version") or "2025-06-18"),
        cursor_file=cursor_path,
        timeout_s=float(panel_raw.get("timeout_s") or 8.0),
        subscribe=bool(panel_raw.get("subscribe", True)),
        poll_ms=_as_int(panel_raw.get("poll_ms", 700), "panel.poll_ms", 50),
    )

    raw_actions = raw.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise ConfigError("'actions' must be a list with at least one action")
    actions = [parse_action(item, i) for i, item in enumerate(raw_actions)]
    seen: set[str] = set()
    for action in actions:
        if action.id in seen:
            raise ConfigError(f"duplicate action: {action.id}")
        seen.add(action.id)
        if 3 in action.pages and action.type not in {"rest", "overlay"}:
            raise ConfigError(
                f"action {action.id}: page 3 is the settings page and only accepts "
                f'actions of types "rest" and "overlay" (got "{action.type}")'
            )
        if 1 in action.pages and not action.featured:
            raise ConfigError(
                f"action {action.id} is on page 1 but is not featured. "
                'Page 1 has only one slot: use "featured": true on it, '
                'or change to "page": 2 (or "pages": [1, 2] with featured).'
            )
    if not any(a.featured for a in actions) and not any(1 in a.pages for a in actions):
        # without a featured button page 1 has no action: informational only, not an error
        pass

    loop_raw = raw.get("loop") or {}
    log_file = loop_raw.get("log_file") or "companion.log"
    log_path = expand_path(str(log_file))
    if not log_path.is_absolute():
        log_path = base_dir / log_path
    loop = LoopSection(
        heartbeat_s=_as_int(loop_raw.get("heartbeat_s", 60), "loop.heartbeat_s", 10),
        max_event_age_s=_as_int(loop_raw.get("max_event_age_s", 60), "loop.max_event_age_s", 0),
        confirm=bool(loop_raw.get("confirm", True)),
        log_file=log_path,
    )

    display_raw = raw.get("display") or {}
    brightness = display_raw.get("brightness", "max")
    if not (isinstance(brightness, int) and 0 <= brightness <= 100) and brightness not in {
        "max", "min", "auto", "off"
    }:
        raise ConfigError("display.brightness must be max, min, auto, off or 0..100")
    display = DisplaySection(
        enabled=bool(display_raw.get("enabled", True)),
        brightness=brightness,
        refresh_min=_as_int(display_raw.get("refresh_min", 5), "display.refresh_min", 0),
    )

    slides_raw = raw.get("slides") or {}
    folder = slides_raw.get("folder")
    folder_path = expand_path(str(folder)) if folder else Path("slides")
    if not folder_path.is_absolute():
        folder_path = base_dir / folder_path
    slides = SlidesSection(
        enabled=bool(slides_raw.get("enabled", bool(folder))),
        folder=folder_path,
        interval_s=_as_int(slides_raw.get("interval_s", 20), "slides.interval_s", 2),
        shuffle=bool(slides_raw.get("shuffle", False)),
        width=_as_int(slides_raw.get("width", 1384), "slides.width", 320),
        height=_as_int(slides_raw.get("height", 720), "slides.height", 180),
        quality=_as_int(slides_raw.get("quality", 72), "slides.quality", 30),
        rescan_s=_as_int(slides_raw.get("rescan_s", 60), "slides.rescan_s", 10),
        caption=bool(slides_raw.get("caption", False)),
        clock=bool(slides_raw.get("clock", False)),
    )
    if slides.quality > 95:
        raise ConfigError("slides.quality maximum is 95")

    rest_raw = raw.get("rest") or {}
    rest = RestSection(
        enabled=bool(rest_raw.get("enabled", False)),
        idle_s=_as_int(rest_raw.get("idle_s", 60), "rest.idle_s", 5),
    )

    state_file = panel_raw.get("state_file")
    state_path = expand_path(state_file) if state_file else cursor_path.with_name("state.json")
    if not state_path.is_absolute():
        state_path = base_dir / state_path

    live_raw = raw.get("live") or {}
    weather_raw = live_raw.get("weather") or {}
    weather = WeatherSection(
        enabled=bool(weather_raw.get("enabled", False)),
        latitude=float(weather_raw.get("latitude") or 0.0),
        longitude=float(weather_raw.get("longitude") or 0.0),
        label=str(weather_raw.get("label") or ""),
        refresh_min=_as_int(weather_raw.get("refresh_min", 15), "live.weather.refresh_min", 1),
        timeout_s=float(weather_raw.get("timeout_s") or 4.0),
    )
    if weather.enabled and not weather.label:
        raise ConfigError("live.weather.enabled=true requires 'label' (the display name)")
    if weather.enabled and any(weather_raw.get(key) is None for key in ("latitude", "longitude")):
        raise ConfigError("live.weather.enabled=true requires 'latitude' and 'longitude'")

    media_raw = live_raw.get("media") or {}
    media = MediaSection(
        enabled=bool(media_raw.get("enabled", False)),
        interval_s=float(media_raw.get("interval_s") or 1.0),
        cover=bool(media_raw.get("cover", True)),
        cover_px=_as_int(media_raw.get("cover_px", 160), "live.media.cover_px", 16),
    )

    view_raw = raw.get("view") or {}
    template_raw = view_raw.get("template")
    template = expand_path(str(template_raw)) if template_raw else default_template()
    if not template.is_absolute():
        template = base_dir / template
    view = ViewSection(
        template=template,
        title=str(view_raw.get("title") or "Painel"),
        show_clock=bool(view_raw.get("show_clock", True)),
        show_weather=bool(view_raw.get("show_weather", True)),
    )

    return Config(
        panel=panel,
        actions=actions,
        loop=loop,
        display=display,
        rest=rest,
        slides=slides,
        state_file=state_path,
        weather=weather,
        media=media,
        view=view,
        base_dir=base_dir,
    )
