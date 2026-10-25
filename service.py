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

from mcp_display_companion.instance import InstanceLock

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE, "service.log")
DEFAULT_CONFIG = os.environ.get("PANEL_COMPANION_CONFIG", "config.json")


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
    # The child holds the lock file while alive; if it is already locked, another
    # companion is publishing to the panel and this supervisor has nothing to do.
    lock_path = os.path.join(BASE, "companion.lock")
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
            env = dict(os.environ, PANEL_COMPANION_LOCK=lock_path)
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
