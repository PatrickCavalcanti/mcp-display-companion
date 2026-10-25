"""Single instance: two companions on the same panel conflict over View."""

import pytest

from mcp_display_companion.instance import InstanceLock


def test_second_instance_is_refused(tmp_path):
    first = InstanceLock(tmp_path / "companion.lock")
    assert first.acquire() is True
    second = InstanceLock(tmp_path / "companion.lock")
    assert second.acquire() is False
    # PID of instance owner remains readable alongside lock
    assert first.owner_pid() > 0
    first.release()
    assert second.acquire() is True
    second.release()


def test_pid_file_goes_away_on_release(tmp_path):
    lock = InstanceLock(tmp_path / "state" / "companion.lock")
    assert lock.acquire() is True
    assert lock.pid_file().exists()
    lock.release()
    assert not lock.pid_file().exists()


def test_lock_survives_a_stale_pid_file(tmp_path):
    """Dead process PID must not block service indefinitely."""
    lock_path = tmp_path / "companion.lock"
    (tmp_path / "companion.pid").write_text("999999", encoding="utf-8")
    lock = InstanceLock(lock_path)
    assert lock.acquire() is True
    assert lock.owner_pid() != 999999
    lock.release()


@pytest.mark.parametrize("times", [1, 2, 3])
def test_acquire_release_cycles(tmp_path, times):
    lock = InstanceLock(tmp_path / "companion.lock")
    for _ in range(times):
        assert lock.acquire() is True
        lock.release()
