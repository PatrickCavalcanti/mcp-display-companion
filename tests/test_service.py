"""Supervisor: the lock is per user, and the supervisor does not force a folder lock.

The child is faked: `subprocess.Popen` (forbidden in tests) is replaced by a
launcher that records the call and reports a clean, quick exit -- which is the
path that ends the supervisor, so `main` returns instead of looping forever.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # `service.py` lives at the repository root
    sys.path.insert(0, str(ROOT))

import service  # noqa: E402  (after the sys.path tweak)
from mcp_display_companion import config as config_module  # noqa: E402
from mcp_display_companion.instance import InstanceLock  # noqa: E402


class FakeProc:
    def __init__(self, code=0):
        self.code = code
        self.terminated = False

    def wait(self):
        return self.code

    def terminate(self):
        self.terminated = True


@pytest.fixture(autouse=True)
def quiet_log(monkeypatch, tmp_path):
    """`service.log` is written next to the supervisor: keep it out of the repo."""
    log_file = tmp_path / "service.log"
    monkeypatch.setattr(service, "LOG_FILE", str(log_file))
    return log_file


def fake_launcher(monkeypatch, code=0):
    calls = []

    def popen(args, **kwargs):
        calls.append({"args": args, **kwargs})
        return FakeProc(code)

    monkeypatch.setattr(service.subprocess, "Popen", popen)
    return calls


# --------------------------------------------------------------------------- #
# which lock the supervisor takes
# --------------------------------------------------------------------------- #
def test_supervisor_uses_the_per_user_lock(monkeypatch):
    monkeypatch.delenv("PANEL_COMPANION_LOCK", raising=False)
    lock_path = service.companion_lock_path()
    assert lock_path == config_module.default_lock_path()
    # not the folder the supervisor was started from: that lock is per folder
    assert lock_path != Path(service.BASE) / "companion.lock"


def test_lock_from_the_environment_wins(monkeypatch, tmp_path):
    custom = tmp_path / "custom" / "companion.lock"
    monkeypatch.setenv("PANEL_COMPANION_LOCK", str(custom))
    assert service.companion_lock_path() == custom


def test_supervisor_lock_does_not_sit_beside_the_companion_lock(tmp_path):
    """`InstanceLock` writes `companion.pid` beside its lock: that pid is the child's."""
    lock_path = tmp_path / "cfg" / "companion.lock"
    supervisor_lock = service.supervisor_lock_path(lock_path)
    assert supervisor_lock.parent != lock_path.parent
    assert supervisor_lock != lock_path


# --------------------------------------------------------------------------- #
# what the supervisor hands to the child
# --------------------------------------------------------------------------- #
def test_supervisor_does_not_force_a_lock_on_the_child(monkeypatch):
    monkeypatch.delenv("PANEL_COMPANION_LOCK", raising=False)
    calls = fake_launcher(monkeypatch)

    assert service.main([]) == 0

    assert len(calls) == 1
    # nothing injected: the child resolves the per-user lock by itself
    assert "PANEL_COMPANION_LOCK" not in calls[0]["env"]


def test_supervisor_passes_through_the_lock_from_the_environment(monkeypatch, tmp_path):
    custom = tmp_path / "custom" / "companion.lock"
    monkeypatch.setenv("PANEL_COMPANION_LOCK", str(custom))
    calls = fake_launcher(monkeypatch)

    assert service.main([]) == 0

    assert calls[0]["env"]["PANEL_COMPANION_LOCK"] == str(custom)


# --------------------------------------------------------------------------- #
# one supervisor per user, whatever folder it was started from
# --------------------------------------------------------------------------- #
def test_second_supervisor_in_another_folder_is_refused(monkeypatch, tmp_path, quiet_log):
    monkeypatch.delenv("PANEL_COMPANION_LOCK", raising=False)
    first = InstanceLock(
        service.supervisor_lock_path(service.companion_lock_path()),
        owner={"folder": str(tmp_path / "old-clone")},
    )
    assert first.acquire() is True
    calls = fake_launcher(monkeypatch)
    try:
        assert service.main([]) == 0
    finally:
        first.release()

    assert calls == []  # no second child was started
    logged = quiet_log.read_text(encoding="utf-8")
    assert "another supervisor is already active" in logged
    assert str(tmp_path / "old-clone") in logged


def test_supervisor_leaves_when_a_companion_is_already_running(monkeypatch, quiet_log):
    """A companion started by hand (or orphaned) blocks the supervisor as well."""
    monkeypatch.delenv("PANEL_COMPANION_LOCK", raising=False)
    running = InstanceLock(
        service.companion_lock_path(), owner={"folder": str(Path(service.BASE))}
    )
    assert running.acquire() is True
    calls = fake_launcher(monkeypatch)
    try:
        assert service.main([]) == 0
    finally:
        running.release()

    assert calls == []
    assert "another companion instance is already active" in quiet_log.read_text(encoding="utf-8")
