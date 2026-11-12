"""Single instance: two companions on the same panel conflict over View."""

import json

import pytest

from mcp_display_companion import config as config_module
from mcp_display_companion.__main__ import main
from mcp_display_companion.instance import InstanceLock


def write_config(folder, actions=None):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "config.json"
    path.write_text(
        json.dumps({
            "panel": {"url": "http://127.0.0.1/mcp", "token": "fake-token"},
            "actions": actions or [{"id": "pc_key", "type": "key", "vk": 175}],
        }),
        encoding="utf-8",
    )
    return path


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


# --------------------------------------------------------------------------- #
# the lock belongs to the user, not to the folder the clone was started from
# --------------------------------------------------------------------------- #
def test_default_lock_lives_next_to_the_token(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "default_secrets_path", lambda: tmp_path / "cfg" / "secrets.json")
    assert config_module.default_lock_path() == tmp_path / "cfg" / "companion.lock"


def test_second_clone_in_another_folder_is_refused(tmp_path, capsys):
    """A new clone has its own `companion.lock` path... unless the lock is per user."""
    clone = tmp_path / "clone"
    cfg_path = write_config(clone)
    lock_path = config_module.default_lock_path()
    first = InstanceLock(lock_path, owner={"folder": str(tmp_path / "old-companion")})
    assert first.acquire() is True
    try:
        assert main(["-c", str(cfg_path)]) == 0
        owner_pid = first.owner_details()["pid"]
    finally:
        first.release()

    out = capsys.readouterr().out
    assert "another companion is already running" in out
    assert f"pid {owner_pid}" in out
    assert str(tmp_path / "old-companion") in out


def test_lock_reports_the_folder_of_its_owner(tmp_path):
    lock = InstanceLock(tmp_path / "companion.lock", owner={"folder": r"C:\work\companion"})
    assert lock.acquire() is True
    try:
        other = InstanceLock(tmp_path / "companion.lock")
        assert other.acquire() is False
        assert other.owner_details()["folder"] == r"C:\work\companion"
        assert "folder" in other.describe_owner()
        assert r"C:\work\companion" in other.describe_owner()
    finally:
        lock.release()


def test_owner_file_goes_away_on_release(tmp_path):
    lock = InstanceLock(tmp_path / "companion.lock", owner={"folder": str(tmp_path)})
    assert lock.acquire() is True
    assert lock.owner_file().exists()
    lock.release()
    assert not lock.owner_file().exists()


# --------------------------------------------------------------------------- #
# --check also renders the view and shows language, home and alerts
# --------------------------------------------------------------------------- #
def test_check_shows_language_home_and_alerts(tmp_path, capsys):
    cfg_path = write_config(tmp_path / "clone")
    assert main(["-c", str(cfg_path), "--check"]) == 0
    out = capsys.readouterr().out
    assert "language         : en" in out
    assert "home             : " in out
    assert "alerts           : " in out


def test_check_fails_when_the_template_has_an_unknown_text_key(tmp_path, capsys):
    template = tmp_path / "custom.html"
    template.write_text("<html>{{T:no_such_key}}</html>", encoding="utf-8")
    cfg_path = write_config(tmp_path / "clone")
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data["view"] = {"template": str(template)}
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    assert main(["-c", str(cfg_path), "--check"]) == 2
    assert "invalid view" in capsys.readouterr().err
