"""Executes what a touch requested -- only what is present in configuration.

Panel security rule (see docs/configuration.md, "Security notes"): **the event carries an
action name, not a command.** This module only executes IDs that exist in
`config.actions`; any other name is ignored. Text coming from a touch is never
turned into a path, argument, or shell command line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .config import DEFAULT_HOTKEY, NAMED_KEYS, Action, Config, expand_path, parse_key
from .host import Host


# URI scheme: spotify:, ms-settings:, https://... (not a filesystem path)
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+:")


class PanelApi(Protocol):
    """Minimal interface an action needs from the panel."""

    def set_display(self, brightness: str | int) -> Any: ...


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    detail: str = ""

    def __bool__(self) -> bool:
        return self.ok


class ActionRunner:
    def __init__(
        self,
        config: Config,
        host: Host,
        panel: PanelApi | None = None,
        rest: Callable[[bool], bool] | None = None,
        overlay: Callable[[bool], bool] | None = None,
    ) -> None:
        self.actions = config.actions_by_id
        self.host = host
        self.panel = panel
        # rest mode is service state (persists across screen reloads),
        # so it is injected from the outside rather than living here
        self.rest = rest
        # clock overlay on photos is also service state
        self.overlay = overlay
        # hotkey_toggle state: action id -> virtual keys currently held down
        self.held_hotkeys: dict[str, list[int]] = {}
        # relative paths in `folder`/`command.cwd` are relative to config.json,
        # not the current working directory from which service was started
        self.base_dir = config.base_dir

    def _path(self, value: str) -> Path:
        path = expand_path(value)
        return path if path.is_absolute() else (self.base_dir / path)

    def knows(self, action_id: str) -> bool:
        return action_id in self.actions

    def run(self, action_id: str) -> ActionResult | None:
        """`None` when the action is not ours (loop ignores, without confirming)."""
        action = self.actions.get(action_id)
        if action is None:
            return None
        handler = getattr(self, f"_run_{action.type}", None)
        if handler is None:  # impossible: config validates type
            return ActionResult(False, f"type without execution handler: {action.type}")
        try:
            return handler(action)
        except Exception as exc:  # noqa: BLE001 - an action never crashes the service
            return ActionResult(False, f"{action.id}: {exc}")

    # ------------------------------------------------------------------ #
    # action types
    # ------------------------------------------------------------------ #
    def _run_media(self, action: Action) -> ActionResult:
        command = action.params["command"]
        ok = self.host.media(command)
        return ActionResult(bool(ok), f"media {command}")

    def _run_key(self, action: Action) -> ActionResult:
        vk = int(action.params["vk"])
        return ActionResult(self.host.press_key(vk), f"vk {vk:#x}")

    def is_hotkey_active(self, action_id: str) -> bool:
        return action_id in self.held_hotkeys

    def release_all_held_keys(self) -> None:
        """Lets go of every key still held (service stopping): never leave a key stuck."""
        for keys in list(self.held_hotkeys.values()):
            for vk in reversed(keys):
                try:
                    self.host.key_up(vk)
                except Exception:  # noqa: BLE001 - keep releasing the others
                    pass
        self.held_hotkeys.clear()

    def hotkey_states(self) -> dict[str, bool]:
        """Every configured `hotkey_toggle` action id -> whether its keys are held now."""
        return {a.id: self.is_hotkey_active(a.id) for a in self.actions.values() if a.type == "hotkey_toggle"}

    def release_configured_hotkeys(self) -> None:
        """At startup: key_up for the keys of every `hotkey_toggle` action.

        Recovers the keyboard after a killed service left keys held. Releasing a key
        that is not down is harmless. The generic host cannot press keys, so there
        is nothing to recover and it is left out (it would only log a complaint on
        every start); any other failure is ignored.
        """
        if getattr(self.host, "name", "") == "generic":
            return
        for action in self.actions.values():
            if action.type != "hotkey_toggle" or action.id in self.held_hotkeys:
                continue
            try:
                keys = [parse_key(k) for k in action.params.get("keys", DEFAULT_HOTKEY)]
                for vk in reversed(keys):
                    self.host.key_up(vk)
            except Exception:  # noqa: BLE001 - best effort, never blocks the start
                pass

    def _run_hotkey_toggle(self, action: Action) -> ActionResult:
        keys_raw = list(action.params.get("keys", DEFAULT_HOTKEY))
        held = self.held_hotkeys.pop(action.id, None)
        if held is not None:
            ok = True
            for vk in reversed(held):
                ok = bool(self.host.key_up(vk)) and ok
            return ActionResult(ok, f"released {keys_raw}")
        vk_list = [parse_key(k) for k in keys_raw]
        # Register each key BEFORE pressing it: whatever path ends the run (an
        # exception, the watchdog), release_all_held_keys already knows the key.
        pressed = self.held_hotkeys.setdefault(action.id, [])
        for vk in vk_list:
            pressed.append(vk)
            if not self.host.key_down(vk):
                pressed.pop()  # this key never went down
                self.held_hotkeys.pop(action.id, None)
                for done in reversed(pressed):
                    self.host.key_up(done)
                return ActionResult(False, f"could not hold {keys_raw}")
        return ActionResult(True, f"holding {keys_raw}")

    def _run_volume(self, action: Action) -> ActionResult:
        direction = action.params["direction"]
        return ActionResult(self.host.volume(direction), f"volume {direction}")

    def _run_folder(self, action: Action) -> ActionResult:
        path = self._path(str(action.params["path"]))
        if not path.exists():
            return ActionResult(False, f"folder not found: {path}")
        # new window by default: navigating an open Explorer window moves the
        # user out of where they were
        reuse_window = bool(action.params.get("reuse_window", False))
        ok = self.host.open_folder(path, reuse_window=reuse_window)
        return ActionResult(ok, f"folder {path} (reused window)" if reuse_window else f"folder {path}")

    def _run_app(self, action: Action) -> ActionResult:
        target = str(action.params["target"])
        # protocols ("spotify:", "ms-settings:") and command names ("notepad.exe")
        # are passed as-is; only actual file paths go through expand_path
        looks_like_path = any(marker in target for marker in ("/", "\\", "%", "~"))
        if not SCHEME_RE.match(target) and looks_like_path:
            target = str(self._path(target))
        return ActionResult(self.host.open_app(target), f"app {target}")

    def _run_url(self, action: Action) -> ActionResult:
        params = action.params
        ok = self.host.open_url(
            str(params["url"]),
            browser=str(params.get("browser") or ""),
            # new window by default: a tab in a browser already open is hidden
            # behind other windows and looks like the button did nothing
            new_window=bool(params.get("new_window", True)),
        )
        return ActionResult(ok, f"url {params['url']}")

    def _run_command(self, action: Action) -> ActionResult:
        params = action.params
        cmd = [str(part) for part in params["cmd"]]
        cwd = params.get("cwd")
        cwd_path: Path | None = self._path(str(cwd)) if cwd else None
        ok = self.host.run_command(cmd, cwd=cwd_path, detached=bool(params.get("detached", True)))
        return ActionResult(ok, f"command {cmd[0]}")

    def _run_rest(self, action: Action) -> ActionResult:
        if self.rest is None:
            return ActionResult(False, "rest mode without state storage")
        enabled = bool(action.params["state"])
        self.rest(enabled)
        return ActionResult(True, f"rest {'enabled' if enabled else 'disabled'}")

    def _run_overlay(self, action: Action) -> ActionResult:
        if self.overlay is None:
            return ActionResult(False, "photo clock overlay without state storage")
        enabled = bool(action.params["state"])
        self.overlay(enabled)
        return ActionResult(True, f"photo clock overlay {'enabled' if enabled else 'disabled'}")

    def _run_display(self, action: Action) -> ActionResult:
        if self.panel is None:
            return ActionResult(False, "no panel access to adjust brightness")
        brightness = action.params.get("brightness", "max")
        self.panel.set_display(brightness)
        return ActionResult(True, f"brightness {brightness}")
