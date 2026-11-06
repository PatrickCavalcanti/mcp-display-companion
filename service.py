"""Supervisor: keeps the panel service running.

Why it exists: the companion silently dies when the session that started it
disappears -- a terminal session, an agent, or a script. The display remains
showing on the phone while touch actions stop working without any error (which
was precisely the bug that led to this file). Here it is restarted in seconds,
and each exit is logged with its exit code to `service.log`.

This file is started by the **system service manager**
(`scripts/install-service.ps1` on Windows, `scripts/install-service.sh` on
macOS/Linux), never an interactive terminal.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from mcp_display_companion import config as config_module
from mcp_display_companion.instance import InstanceLock

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE, "service.log")
DEFAULT_CONFIG = os.environ.get("PANEL_COMPANION_CONFIG", "config.json")


def companion_lock_path() -> Path:
    """Lock the service will take: the same one `python -m mcp_display_companion`
    uses -- per user, unless `PANEL_COMPANION_LOCK` says otherwise.

    A lock per folder (next to `config.json`) would let a second clone, in
    another folder, run alongside this one; the token is shared, so both would
    publish to the same panel and fight over the View.
    """
    from_env = os.environ.get("PANEL_COMPANION_LOCK")
    return Path(from_env) if from_env else config_module.default_lock_path()


def supervisor_lock_path(lock_path: Path) -> Path:
    """The supervisor's own lock, in a folder of its own.

    It cannot sit beside the companion lock: `InstanceLock` writes
    `companion.pid` next to the lock file, and that pid is the companion's --
    the installer reads it to stop the right process. With a lock of its own the
    supervisor is seen by a second supervisor started from another folder, even
    while its child is between restarts.
    """
    return lock_path.parent / "supervisor" / "companion.lock"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Panel service supervisor.")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG,
                        help=f"configuration passed to service (default: {DEFAULT_CONFIG})")
    return parser.parse_args(argv)


WAIT_MIN = 5
WAIT_MAX = 60
# below this the companion died early: wait time doubles to avoid hot looping
SHORT_LIFE_S = 120


def log(msg: str) -> None:
    line = f"{time.strftime('[%Y-%m-%d %H:%M:%S]')} {msg}"
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001 - no console when running under pythonw
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as file:
            file.write(line + "\n")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv).config
    log("========== supervisor started ==========")
    log(f"config: {config}")
    lock_path = companion_lock_path()
    log(f"lock: {lock_path}")
    # One supervisor per user: a second one, started from another folder, would
    # only add a second child competing for the same panel.
    supervisor_lock = InstanceLock(
        supervisor_lock_path(lock_path), owner={"folder": BASE, "config": config}
    )
    if not supervisor_lock.acquire():
        log(
            f"another supervisor is already active ({supervisor_lock.describe_owner()}); "
            "this supervisor exits without starting a second one"
        )
        return 0
    try:
        return supervise(config, lock_path)
    finally:
        supervisor_lock.release()


def supervise(config: str, lock_path: Path) -> int:
    # The child holds the lock file while alive; if it is already locked, another
    # companion is publishing to the panel and this supervisor has nothing to do.
    probe = InstanceLock(lock_path)
    if not probe.acquire():
        log(
            f"another companion instance is already active (pid {probe.owner_pid()}); "
            "this supervisor exits without starting a second one"
        )
        return 0
    probe.release()

    wait_time = WAIT_MIN
    while True:
        started = time.time()
        log(f"starting mcp_display_companion (parent {os.getpid()})")
        code: int | None = None
        try:
            # The child resolves the lock itself (per user, or the path in
            # PANEL_COMPANION_LOCK); the supervisor must not force its own on it.
            env = dict(os.environ)
            proc = subprocess.Popen(
                [sys.executable, "-m", "mcp_display_companion", "-c", config],
                cwd=BASE,
                env=env,
            )
        except OSError as exc:
            log(f"could not start service: {exc}")
        else:
            try:
                code = proc.wait()
            except KeyboardInterrupt:
                log("supervisor interrupted; terminating service")
                proc.terminate()
                return 0

        lived = time.time() - started
        # clean and quick exit = another instance took over or clean dry-run; do not insist
        if code == 0 and lived < 10:
            log("service exited without error (single instance or configuration check); supervisor exiting")
            return 0
        wait_time = WAIT_MIN if lived >= SHORT_LIFE_S else min(wait_time * 2, WAIT_MAX)
        log(f"service exited (code {code}, lasted {lived:.0f}s); restarting in {wait_time}s")
        time.sleep(wait_time)


if __name__ == "__main__":
    raise SystemExit(main())
