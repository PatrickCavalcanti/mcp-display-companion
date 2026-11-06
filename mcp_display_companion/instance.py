"""Single instance locking.

Two companion instances connected to the same panel conflict over the View:
each publishes, the other receives "presentation is not currently open view"
and re-publishes -- the display flickers and touch responses fail. Task
Scheduler prevents duplicate *tasks*, but cannot stop a manually started
process or an orphaned child of a crashed supervisor.

The lock uses OS-level file locking rather than a plain PID file: it is released
automatically if the process dies, even abnormally, never leaving a stale lock
blocking future starts. The PID is stored alongside only so the installer knows
which process to terminate; `companion.owner.json` says who holds it (pid and
folder), so a second start can tell the user exactly which companion is running.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class InstanceLock:
    def __init__(self, path: Path, owner: dict[str, Any] | None = None) -> None:
        self.path = Path(path)
        # extra information written with the lock, so the next start can report
        # which folder the running companion was started from
        self.owner = dict(owner or {})
        self._file = None

    # On Windows file locking also blocks reading the locked byte range;
    # installer reads PID from companion.pid, and lock byte is placed high.
    LOCK_OFFSET = 4096

    def acquire(self, block: bool = False) -> bool:
        """Attempts to acquire the single instance lock. `False` = already running."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_obj = open(self.path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                file_obj.seek(self.LOCK_OFFSET)
                msvcrt.locking(file_obj.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            file_obj.close()
            return False
        self._file = file_obj
        self._write_owner_files()
        return True

    def _write_owner_files(self) -> None:
        pid = os.getpid()
        try:
            self.pid_file().write_text(str(pid), encoding="utf-8")
        except OSError:
            pass
        try:
            info = {"pid": pid, **self.owner}
            self.owner_file().write_text(json.dumps(info), encoding="utf-8")
        except OSError:
            pass

    def pid_file(self) -> Path:
        return self.path.with_name("companion.pid")

    def owner_file(self) -> Path:
        return self.path.with_name("companion.owner.json")

    def owner_details(self) -> dict[str, Any]:
        """Who holds the lock: `pid` and the `folder` it was started from."""
        data: dict[str, Any] = {}
        try:
            raw = json.loads(self.owner_file().read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, ValueError):
            data = {}
        return {
            "pid": data.get("pid") or self.owner_pid(),
            "folder": data.get("folder"),
            "config": data.get("config"),
        }

    def describe_owner(self) -> str:
        """`pid 1234, folder C:\\...` for the message shown by the second start."""
        details = self.owner_details()
        pid = details.get("pid")
        folder = details.get("folder")
        return (
            f"pid {pid if pid else 'unknown'}, "
            f"folder {folder if folder else 'unknown'}"
        )

    def release(self) -> None:
        file_obj, self._file = self._file, None
        if file_obj is None:
            return
        try:
            self.pid_file().unlink(missing_ok=True)
            self.owner_file().unlink(missing_ok=True)
        except OSError:
            pass
        try:
            if os.name == "nt":
                file_obj.seek(self.LOCK_OFFSET)
                msvcrt.locking(file_obj.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            file_obj.close()

    def owner_pid(self) -> int | None:
        """PID of the process holding the instance lock (best effort for installer)."""
        try:
            text = self.pid_file().read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return int(text) if text.isdigit() else None

    def __enter__(self) -> "InstanceLock":
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
