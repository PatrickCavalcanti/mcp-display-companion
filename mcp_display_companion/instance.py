"""Single instance locking.

Two companion instances connected to the same panel conflict over the View:
each publishes, the other receives "presentation is not currently open view"
and re-publishes -- the display flickers and touch responses fail. Task
Scheduler prevents duplicate *tasks*, but cannot stop a manually started
process or an orphaned child of a crashed supervisor.

The lock uses OS-level file locking rather than a plain PID file: it is released
automatically if the process dies, even abnormally, never leaving a stale lock
blocking future starts. The PID is stored alongside only so the installer knows
which process to terminate.
"""

from __future__ import annotations

import os
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
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
        # Store PID in companion.pid alongside
        try:
            self.pid_file().write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass
        return True

    def pid_file(self) -> Path:
        return self.path.with_name("companion.pid")

    def release(self) -> None:
        file_obj, self._file = self._file, None
        if file_obj is None:
            return
        try:
            self.pid_file().unlink(missing_ok=True)
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
