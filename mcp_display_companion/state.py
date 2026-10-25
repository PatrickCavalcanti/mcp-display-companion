"""State that needs to persist across screen reloads.

The View is reloaded on every `show_view` (reconnection, config change,
service restart), so choices made via touch -- such as rest mode on/off --
cannot live only in JavaScript: the PC persists them.

Small JSON file next to the cursor (`%APPDATA%\\mcp-display-companion\\state.json`
on Windows, `~/.config/mcp-display-companion/state.json` elsewhere). Tolerant reading:
corrupt or missing file falls back to configuration defaults.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable


class StateStore:
    def __init__(self, path: Path, log: Callable[[str], None] = lambda m: None) -> None:
        self.path = Path(path)
        self.log = log
        self._data: dict[str, Any] = self._read()

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            self.log(f"unreadable state in {self.path} ({exc}); starting from default")
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str, default: Any = None) -> Any:
        value = self._data.get(key, default)
        return default if value is None else value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            # do not lose user preference just because disk rejected a write:
            # the value remains in memory until the process terminates
            self.log(f"could not write state to {self.path}: {exc}")
