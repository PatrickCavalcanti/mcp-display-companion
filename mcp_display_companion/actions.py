"""Executes what a touch requested -- only what is present in configuration.

Panel security rule (see README.md, "Security notes"): **the event carries an
action name, not a command.** This module only executes IDs that exist in
`config.actions`; any other name is ignored. Text coming from a touch is never
turned into a path, argument, or shell command line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .config import Action, Config, expand_path
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

    def _run_volume(self, action: Action) -> ActionResult:
        direction = action.params["direction"]
        return ActionResult(self.host.volume(direction), f"volume {direction}")

    def _run_folder(self, action: Action) -> ActionResult:
        path = self._path(str(action.params["path"]))
        if not path.exists():
            return ActionResult(False, f"folder not found: {path}")
        return ActionResult(self.host.open_folder(path), f"folder {path}")

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
            new_window=bool(params.get("new_window", False)),
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
