"""Claude Code permission alerts: hook script, runtime scanning/dismissal, View banner."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from mcp_display_companion import view
from mcp_display_companion.config import (
    Action,
    AlertsSection,
    Config,
    ConfigError,
    DisplaySection,
    PanelSection,
    ViewSection,
)
from mcp_display_companion.runtime import Runtime

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "scripts" / "claude_alert_hook.py"
# `tema/` holds the owner's local theme and is not versioned: test it only when present
TEMPLATES = [
    p for p in (
        ROOT / "mcp_display_companion" / "templates" / "painel.html",
        ROOT / "tema" / "turquesa.html",
    ) if p.exists()
]
REAL_POPEN = subprocess.Popen  # conftest replaces Popen with a guard; the hook test needs it


# --------------------------------------------------------------------------- #
# hook script (run for real, as Claude Code does)
# --------------------------------------------------------------------------- #
@pytest.fixture
def alerts_dir(tmp_path):
    return tmp_path / "alerts-hook"


def run_hook(alerts_dir, payload, raw=None, monkeypatch=None):
    env = {**os.environ, "PANEL_COMPANION_ALERTS": str(alerts_dir)}
    stdin = raw if raw is not None else json.dumps(payload)
    proc = REAL_POPEN(
        [sys.executable, "-I", "-S", str(HOOK)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    out, err = proc.communicate(stdin.encode("utf-8", "surrogatepass"), timeout=30)
    return proc.returncode, out.decode(), err.decode()


def notify(sid="sess-1", cwd=r"C:\Users\me\proj-a", message="Claude needs your permission to use Bash"):
    return {"session_id": sid, "cwd": cwd, "hook_event_name": "Notification",
            "notification_type": "permission_prompt", "message": message}


def test_hook_notification_writes_the_alert(alerts_dir):
    code, out, _ = run_hook(alerts_dir, notify())
    assert (code, out) == (0, "")
    data = json.loads((alerts_dir / "sess-1.json").read_text(encoding="utf-8"))
    assert data["session"] == "sess-1"
    assert data["project"] == "proj-a"
    assert data["message"].startswith("Claude needs")
    assert isinstance(data["at"], float)
    assert [p.name for p in alerts_dir.iterdir()] == ["sess-1.json"]  # no temp file left


@pytest.mark.parametrize("event", ["PostToolUse", "PostToolUseFailure", "UserPromptSubmit", "Stop", "SessionEnd"])
def test_hook_clearing_events_delete_the_alert(alerts_dir, event):
    run_hook(alerts_dir, notify())
    code, out, _ = run_hook(alerts_dir, {"session_id": "sess-1", "cwd": "x", "hook_event_name": event})
    assert (code, out) == (0, "")
    assert not (alerts_dir / "sess-1.json").exists()


def test_hook_clear_without_alert_or_folder_is_silent(alerts_dir):
    result = run_hook(alerts_dir, {"session_id": "nope", "hook_event_name": "Stop"})
    assert result == (0, "", "")
    assert not alerts_dir.exists()


def test_hook_sanitizes_the_session_id(alerts_dir):
    run_hook(alerts_dir, notify(sid="../../evil/..\\x y" + "a" * 300))
    names = [p.name for p in alerts_dir.iterdir()]
    assert len(names) == 1
    stem = names[0][:-5]
    assert set(stem) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    assert 0 < len(stem) <= 80
    assert not (alerts_dir.parent / "evil").exists()


@pytest.mark.parametrize("raw", [
    "", "not json", "[]", "null", '{"hook_event_name": "Notification"}',
    '{"session_id": 5, "hook_event_name": "Notification", "cwd": null}', "\udcff\udcfe",
])
def test_hook_never_raises_on_garbage(alerts_dir, raw):
    assert run_hook(alerts_dir, None, raw=raw) == (0, "", "")


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
def test_alerts_defaults(load_config, raw_config, tmp_path):
    cfg = load_config(raw_config)
    assert cfg.alerts.enabled is True and cfg.alerts.wake is True
    assert cfg.alerts.max_age_s == 1800
    assert cfg.alerts.brightness == "max"
    assert cfg.alerts.dir == tmp_path / "alerts"  # PANEL_COMPANION_ALERTS from conftest


def test_alerts_brightness_follows_display(load_config, raw_config):
    raw_config["display"] = {"brightness": 40}
    assert load_config(raw_config).alerts.brightness == 40
    raw_config["alerts"] = {"brightness": "max", "max_age_s": 60, "dir": "x/y", "wake": False}
    cfg = load_config(raw_config)
    assert (cfg.alerts.brightness, cfg.alerts.max_age_s, cfg.alerts.wake) == ("max", 60, False)
    assert cfg.alerts.dir.name == "y"


@pytest.mark.parametrize("alerts", [
    {"enabled": "yes"}, {"wake": 1}, {"brightness": "bright"}, {"brightness": 101},
    {"max_age_s": 1}, {"dir": ""}, "oops",
])
def test_alerts_validation(load_config, raw_config, alerts):
    raw_config["alerts"] = alerts
    with pytest.raises(ConfigError):
        load_config(raw_config)


# --------------------------------------------------------------------------- #
# runtime
# --------------------------------------------------------------------------- #
class FakeStream:
    def is_set(self):
        return False

    def clear(self):
        pass


class FakeClient:
    def __init__(self):
        self.updates, self.shows, self.brightness = [], [], []
        self.stream_ok = FakeStream()
        self.events = []

    def connect(self): pass
    def subscribe(self, on_update): return None
    def close_stream(self): pass

    def read_events(self, since):
        batch, self.events = self.events, []
        return {"cursor": (since or 0) + len(batch), "eventos": batch}

    def update_view(self, data, presentation=None): self.updates.append((data, presentation))

    def show_view(self, html, **kwargs):
        self.shows.append(kwargs)
        return "screen-1"

    def set_display(self, brightness): self.brightness.append(brightness)

    def alert_updates(self):
        return [d["alertas"] for d, _ in self.updates if "alertas" in d]

    def alert_payloads(self):
        return [d for d, _ in self.updates if "alertas" in d]


class FakeLive:
    def due(self, now, force=False): return {}
    def ack(self, data): pass
    def snapshot(self): return {}
    def refresh_media(self): return None


@pytest.fixture
def rt(tmp_path, monkeypatch):
    class UnusedLoop:
        def run_until_complete(self, coro): raise AssertionError("unexpected async call")
        def close(self): pass

    monkeypatch.setattr("mcp_display_companion.runtime.asyncio.new_event_loop", UnusedLoop)
    adir = tmp_path / "alerts"
    cfg = Config(
        panel=PanelSection("http://127.0.0.1/mcp", "fake-token", cursor_file=tmp_path / "cursor.json"),
        actions=[Action("pc_key", "Key", "key", params={"vk": 175})],
        display=DisplaySection(enabled=True, brightness="max", refresh_min=5),
        alerts=AlertsSection(dir=adir, max_age_s=1800, brightness=70, allow_approve=True),
        state_file=tmp_path / "state.json",
    )
    client = FakeClient()
    now = [10_000.0]
    runtime = Runtime(cfg, client=client, host=object(), live=FakeLive(), clock=lambda: now[0])
    runtime.now = now
    runtime.client_ = client
    runtime.adir = adir
    runtime.cursor = 0
    runtime.presentation = "p"  # the View this service published; the tests' touches come from it
    runtime._last_heartbeat = runtime._last_brightness = now[0]  # keep the periodic refresh out of the way
    return runtime


def put(rt, sid, at=None, project="proj", message="allow Bash?"):
    rt.adir.mkdir(parents=True, exist_ok=True)
    path = rt.adir / f"{sid}.json"
    path.write_text(json.dumps({"session": sid, "project": project, "message": message,
                                "at": rt.now[0] if at is None else at}), encoding="utf-8")
    return path


def test_new_alert_is_sent_and_wakes_once(rt):
    client = rt.client_
    rt.tick()
    assert client.alert_updates() == [] and client.brightness == []  # dir missing: nothing
    put(rt, "s1")
    rt.tick()
    rt.tick()
    assert client.alert_updates() == [[{"id": "s1", "project": "proj", "message": "allow Bash?", "at": 10_000.0}]]
    assert client.brightness == ["70"]  # the alert brightness, once


def test_a_second_alert_wakes_again_and_removal_clears(rt):
    client = rt.client_
    put(rt, "s1", at=10_000.0)
    rt.tick()
    put(rt, "s2", at=10_001.0)
    rt.tick()
    assert [len(x) for x in client.alert_updates()] == [1, 2]
    assert client.brightness == ["70", "70"]
    (rt.adir / "s1.json").unlink()
    (rt.adir / "s2.json").unlink()
    rt.tick()
    assert client.alert_updates()[-1] == []
    assert client.brightness == ["70", "70", "max"]  # removal does not wake, it restores the normal brightness


def test_wake_disabled_only_sends_the_list(rt):
    rt.config = replace(rt.config, alerts=replace(rt.config.alerts, wake=False))
    put(rt, "s1")
    rt.tick()
    assert len(rt.client_.alert_updates()) == 1 and rt.client_.brightness == []


def test_disabled_alerts_do_nothing(rt):
    rt.config = replace(rt.config, alerts=replace(rt.config.alerts, enabled=False))
    put(rt, "s1")
    rt.tick()
    assert rt.client_.alert_updates() == []


def test_alert_list_is_resent_after_reconnect_and_republish(rt):
    client = rt.client_
    put(rt, "s1")
    rt.tick()
    n = len(client.alert_updates())
    rt._connect()          # reconnect: the View may have reloaded
    rt.tick()
    assert len(client.alert_updates()) == n + 1
    rt.tick()
    assert len(client.alert_updates()) == n + 1  # and not again
    rt._publish()          # republish: a fresh, empty View
    rt.tick()
    assert len(client.alert_updates()) == n + 2


def test_stale_alert_is_ignored_and_deleted(rt):
    path = put(rt, "old", at=10_000.0 - 1801)
    rt.tick()
    assert rt.client_.alert_updates() == []
    assert not path.exists()


def test_future_alert_counts_as_fresh(rt):
    path = put(rt, "skew", at=10_000.0 + 500)
    rt.tick()
    assert len(rt.client_.alert_updates()) == 1 and path.exists()


def test_garbage_and_foreign_files_are_skipped(rt):
    rt.adir.mkdir()
    (rt.adir / "bad.json").write_text("{half", encoding="utf-8")
    (rt.adir / "list.json").write_text("[1]", encoding="utf-8")
    (rt.adir / ".tmp.json").write_text("{}", encoding="utf-8")
    (rt.adir / "note.txt").write_text("x", encoding="utf-8")
    (rt.adir / "sub").mkdir()
    rt.tick()
    assert rt.client_.alert_updates() == []
    put(rt, "bad")  # completed later: read again
    rt.tick()
    assert [a["id"] for a in rt.client_.alert_updates()[-1]] == ["bad"]


def dismiss(rt, args=None, **changes):
    data = {"tool": "pc_alerta_dispensar"}
    if args is not None:
        data["arguments"] = args
    event = {"id": 1, "tipo": "toque", "em": rt.now[0] * 1000, "apresentacao": "p", "dados": data, **changes}
    rt.client_.events.append(event)
    rt.tick()


def test_dismiss_deletes_only_that_id(rt):
    put(rt, "s1", at=10_000.0)
    put(rt, "s2", at=10_001.0)
    rt.tick()
    dismiss(rt, {"id": "s2"})
    assert (rt.adir / "s1.json").exists() and not (rt.adir / "s2.json").exists()
    assert [a["id"] for a in rt.client_.alert_updates()[-1]] == ["s1"]


@pytest.mark.parametrize("args", [None, {}, {"id": ""}, {"id": None}])
def test_dismiss_without_id_deletes_nothing(rt, args):
    put(rt, "s1")
    put(rt, "s2")
    rt.tick()
    dismiss(rt, args)
    assert sorted(p.name for p in rt.adir.iterdir() if p.name != ".service-alive") == ["s1.json", "s2.json"]


def test_dismiss_of_an_unknown_id_deletes_nothing(rt):
    put(rt, "s1")
    rt.tick()
    dismiss(rt, {"id": "other"})
    assert (rt.adir / "s1.json").exists()


def test_dismiss_never_leaves_the_alerts_dir(rt, tmp_path):
    outside = tmp_path / "secret.json"
    outside.write_text("{}", encoding="utf-8")
    put(rt, "s1")
    for bad in ("../secret", "..\\secret", "a/b", "x" * 81, 5, ["s1"], "s1.json"):
        dismiss(rt, {"id": bad})
    assert outside.exists() and (rt.adir / "s1.json").exists()


def test_old_dismiss_touch_is_ignored(rt):
    put(rt, "s1")
    rt.tick()
    dismiss(rt, {"id": "s1"}, em=(rt.now[0] - 3600) * 1000)
    assert (rt.adir / "s1.json").exists()


def test_dismiss_is_builtin_not_a_configured_action(rt):
    assert rt.runner.knows("pc_alerta_dispensar") is False
    put(rt, "s1")
    rt.tick()
    dismiss(rt, {"id": "s1"})
    assert not (rt.adir / "s1.json").exists()


def test_rest_exit_keeps_alert_brightness_while_an_alert_shows(rt):
    put(rt, "s1")
    rt.tick()
    rt.client_.brightness.clear()
    rt.client_.events.append({"id": 1, "tipo": "toque", "em": rt.now[0] * 1000, "apresentacao": "p",
                              "dados": {"tool": "pc_rest_left"}})
    rt.tick()
    assert rt.client_.brightness == ["70"]


# --------------------------------------------------------------------------- #
# View
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_view_has_the_banner(template, make_config):
    html = view.render(make_config(view=ViewSection(template=template)))
    for needle in ('id="alerta"', 'id="alerta-t1"', 'id="alerta-t2"', 'id="alerta-mais"',
                   "aplicarAlertas", "pc_alerta_dispensar", "d.alertas", "simularAlertas",
                   "right:56px"):
        assert needle in html, needle
    banner = html[html.index('<div id="alerta"'):html.index("<script>")]
    assert "<svg" in banner
    assert html.index('<div id="alerta"') > html.index('id="rodape"')  # outside #app (it has a transform)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_view_alert_strings_are_not_injected_as_html(template):
    text = template.read_text(encoding="utf-8")
    start = text.index("function aplicarAlertas")
    end = text.index("simularAlertas = ")
    block = text[start:end]
    assert "innerHTML" not in block and "insertAdjacent" not in block and "document.write" not in block
    assert block.count("textContent") >= 3


# --------------------------------------------------------------------------- #
# approve / deny from the panel
# --------------------------------------------------------------------------- #
import time  # noqa: E402


def permission(sid="sess-1", tool="Bash", tool_input=None, cwd=r"C:\Users\me\proj-a"):
    return {"session_id": sid, "cwd": cwd, "hook_event_name": "PermissionRequest",
            "tool_name": tool, "tool_input": tool_input if tool_input is not None else {"command": "ls -la"}}


def write_alive(alerts_dir, age=0.0):
    """The service's sign of life, `age` seconds old (what the runtime keeps renewing)."""
    alerts_dir.mkdir(parents=True, exist_ok=True)
    (alerts_dir / ".service-alive").write_text(
        json.dumps({"at": time.time() - age, "pid": 1}), encoding="utf-8")


def leftovers(alerts_dir):
    """Files in the alerts folder other than the service's sign of life."""
    if not alerts_dir.exists():
        return []
    return [p for p in alerts_dir.iterdir() if p.name != ".service-alive"]


def start_hook(alerts_dir, payload, wait="20", alive=True, alive_max="600"):
    """Run the hook as a subprocess. By default the service looks alive for the whole
    test (fresh sign, a 10 min limit); `alive=False` leaves the sign out."""
    if alive:
        write_alive(alerts_dir)
    env = {**os.environ, "PANEL_COMPANION_ALERTS": str(alerts_dir), "PANEL_COMPANION_ALERT_WAIT": wait,
           "PANEL_COMPANION_ALIVE_MAX_AGE": alive_max}
    proc = REAL_POPEN([sys.executable, "-I", "-S", str(HOOK)], stdin=subprocess.PIPE,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    proc.stdin.write(json.dumps(payload).encode("utf-8"))
    proc.stdin.close()
    proc.stdin = None   # Python 3.11 on POSIX: communicate() would flush the closed pipe
    return proc


def wait_for(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def request_files(alerts_dir, sid="sess-1"):
    return sorted(alerts_dir.glob(f"{sid}-*.json"))


@pytest.mark.parametrize("answer,behavior", [("allow", "allow"), ("deny", "deny")])
def test_hook_permission_request_waits_for_the_answer(alerts_dir, answer, behavior):
    proc = start_hook(alerts_dir, permission(tool_input={"command": "x" * 500, "description": "d"}))
    try:
        assert wait_for(lambda: request_files(alerts_dir))
        req = request_files(alerts_dir)[0]
        data = json.loads(req.read_text(encoding="utf-8"))
        assert data["actionable"] is True and data["project"] == "proj-a" and data["session"] == "sess-1"
        assert data["request"] == {"tool": "Bash", "summary": "x" * 500, "truncated": False}
        assert data["message"] == "Claude wants to use Bash" and isinstance(data["at"], float)
        req.with_suffix(".answer").write_text(answer, encoding="utf-8")
        out, _ = proc.communicate(timeout=10)
    finally:
        proc.kill()
    assert proc.returncode == 0
    decision = json.loads(out.decode("utf-8"))["hookSpecificOutput"]
    assert decision["hookEventName"] == "PermissionRequest"
    assert decision["decision"]["behavior"] == behavior
    if behavior == "deny":
        assert decision["decision"]["message"] == "Denied by the panel"
    assert leftovers(alerts_dir) == []


def test_hook_permission_request_is_silent_when_the_file_disappears(alerts_dir):
    proc = start_hook(alerts_dir, permission())
    try:
        assert wait_for(lambda: request_files(alerts_dir))
        run_hook(alerts_dir, {**permission(), "hook_event_name": "PostToolUse"})  # PC answered
        out, _ = proc.communicate(timeout=10)
    finally:
        proc.kill()
    assert (proc.returncode, out) == (0, b"")
    assert leftovers(alerts_dir) == []


def test_hook_permission_request_times_out_silently_and_cleans_up(alerts_dir):
    proc = start_hook(alerts_dir, permission(), wait="0.6")
    try:
        out, _ = proc.communicate(timeout=10)
    finally:
        proc.kill()
    assert (proc.returncode, out) == (0, b"")
    assert leftovers(alerts_dir) == []


def request_of(alerts_dir, tool, tool_input):
    proc = start_hook(alerts_dir, permission(tool=tool, tool_input=tool_input), wait="5")
    try:
        assert wait_for(lambda: request_files(alerts_dir))
        return json.loads(request_files(alerts_dir)[0].read_text(encoding="utf-8"))["request"]
    finally:
        proc.kill()
        proc.communicate()


@pytest.mark.parametrize("tool,tool_input,summary", [
    ("Edit", {"file_path": "C:/a/b.py"}, '{"file_path":"C:/a/b.py"}'),
    ("WebFetch", {"url": "https://example.com/x"}, '{"url":"https://example.com/x"}'),
    ("Grep", {}, "(no arguments)"),
])
def test_hook_other_tools_show_compact_json(alerts_dir, tool, tool_input, summary):
    assert request_of(alerts_dir, tool, tool_input) == {"tool": tool, "summary": summary, "truncated": False}


def test_hook_other_tools_are_capped_at_600_and_marked_truncated(alerts_dir):
    request = request_of(alerts_dir, "Write", {"file_path": "a", "content": "z" * 2000})
    assert len(request["summary"]) == 600 and request["truncated"] is True


def test_hook_bash_shows_up_to_1000_chars(alerts_dir):
    ok = request_of(alerts_dir, "Bash", {"command": "a" * 1000, "description": "d", "timeout": 5})
    assert ok["summary"] == "a" * 1000 and ok["truncated"] is False


def test_hook_bash_longer_than_1000_is_truncated(alerts_dir):
    request = request_of(alerts_dir, "Bash", {"command": "a" * 1001})
    assert request["summary"] == "a" * 1000 and request["truncated"] is True


def test_hook_bash_with_extra_flags_is_truncated_and_shows_them(alerts_dir):
    request = request_of(alerts_dir, "Bash", {"command": "ls", "dangerouslyDisableSandbox": True})
    assert request["summary"] == "ls [dangerouslyDisableSandbox]" and request["truncated"] is True


@pytest.mark.parametrize("tool,shown", [
    ("mcp__1a59c906-04da-521d-bda7-7f71b9f9e01c__delete", "1a59c906: delete"),
    ("mcp__1a59c906-04da-521d-bda7-7f71b9f9e01c__update", "1a59c906: update"),
    ("mcp__srv__do_this", "srv: do_this"),
])
def test_hook_mcp_tools_show_server_and_operation(alerts_dir, tool, shown):
    request = request_of(alerts_dir, tool, {"a": 1})
    assert request == {"tool": shown, "summary": '{"a":1}', "truncated": False}


def test_hook_mcp_operations_never_look_the_same(alerts_dir):
    long_op = "x" * 200
    request = request_of(alerts_dir, "mcp__srv__" + long_op, {})
    assert request["tool"] == "srv: " + long_op and request["truncated"] is True


@pytest.mark.parametrize("tool", ["ExitPlanMode", "AskUserQuestion", "mcp__ccd_session__x", "mcp__plug__ExitPlanThing"])
def test_hook_does_not_offer_non_approvable_tools(alerts_dir, tool):
    result = run_hook(alerts_dir, permission(tool=tool))
    assert result == (0, "", "")
    assert not alerts_dir.exists()


def test_hook_request_file_name_has_call_hash_and_long_random_part(alerts_dir):
    proc = start_hook(alerts_dir, permission(), wait="5")
    try:
        assert wait_for(lambda: request_files(alerts_dir))
        stem = request_files(alerts_dir)[0].stem
    finally:
        proc.kill()
        proc.communicate()
    sid, h, rand = stem[:6], stem[7:15], stem[16:]
    assert (sid, stem[6], stem[15]) == ("sess-1", "-", "-")
    assert len(h) == 8 and len(rand) == 16 and int(rand, 16) >= 0
    assert h == hook_module().call_hash("Bash", {"command": "ls -la"})


def hook_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("claude_alert_hook_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_post_tool_use_only_clears_its_own_call(alerts_dir):
    hook = hook_module()
    alerts_dir.mkdir()
    mine = hook.call_hash("Bash", {"command": "ls"})
    other = hook.call_hash("Bash", {"command": "rm x"})
    for name in (f"sess-1-{mine}-aaaaaaaaaaaaaaaa.json", f"sess-1-{mine}-aaaaaaaaaaaaaaaa.answer",
                 f"sess-1-{other}-bbbbbbbbbbbbbbbb.json", "sess-1.json", "sess-2-" + mine + "-cccccccccccccccc.json"):
        (alerts_dir / name).write_text("{}", encoding="utf-8")
    for event in ("PostToolUse", "PostToolUseFailure"):
        run_hook(alerts_dir, {"session_id": "sess-1", "hook_event_name": event,
                              "tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert sorted(p.name for p in alerts_dir.iterdir()) == sorted([
        f"sess-1-{other}-bbbbbbbbbbbbbbbb.json", "sess-2-" + mine + "-cccccccccccccccc.json"])


def test_hook_post_tool_use_without_tool_name_keeps_the_requests(alerts_dir):
    alerts_dir.mkdir()
    for name in ("sess-1.json", "sess-1-abcd1234-aaaaaaaaaaaaaaaa.json"):
        (alerts_dir / name).write_text("{}", encoding="utf-8")
    run_hook(alerts_dir, {"session_id": "sess-1", "hook_event_name": "PostToolUse"})
    assert [p.name for p in alerts_dir.iterdir()] == ["sess-1-abcd1234-aaaaaaaaaaaaaaaa.json"]


def test_hook_two_parallel_requests_are_independent(alerts_dir):
    p1 = start_hook(alerts_dir, permission(tool_input={"command": "ls"}), wait="20")
    p2 = start_hook(alerts_dir, permission(tool_input={"command": "pwd"}), wait="20")
    try:
        assert wait_for(lambda: len(request_files(alerts_dir)) == 2)
        run_hook(alerts_dir, {**permission(tool_input={"command": "ls"}), "hook_event_name": "PostToolUse"})
        out1, _ = p1.communicate(timeout=10)   # its file vanished: answered on the PC
        assert out1 == b"" and len(request_files(alerts_dir)) == 1
        assert p2.poll() is None               # the other one keeps waiting
    finally:
        p1.kill()
        p2.kill()
        p1.communicate()
        p2.communicate()


def test_hook_long_session_id_matches_the_clearing_prefix(alerts_dir):
    sid = "s" * 200
    proc = start_hook(alerts_dir, permission(sid=sid), wait="20")
    try:
        assert wait_for(lambda: list(alerts_dir.glob("*.json")))
        name = next(alerts_dir.glob("*.json")).stem
        assert len(name) <= 80
        run_hook(alerts_dir, {"session_id": sid, "hook_event_name": "Stop"})
        out, _ = proc.communicate(timeout=10)
    finally:
        proc.kill()
    assert out == b"" and leftovers(alerts_dir) == []


# --------------------------------------------------------------------------- #
# sign of life: the hook does not wait for a service that is not there
# --------------------------------------------------------------------------- #
def finish(proc):
    try:
        out, _ = proc.communicate(timeout=10)
    finally:
        proc.kill()
    return proc.returncode, out


def test_hook_without_a_sign_of_life_leaves_at_once(alerts_dir):
    started = time.monotonic()
    assert finish(start_hook(alerts_dir, permission(), wait="100", alive=False)) == (0, b"")
    assert time.monotonic() - started < 2
    assert not alerts_dir.exists()   # it did not even write the request


def test_hook_with_an_old_sign_of_life_leaves_at_once(alerts_dir):
    write_alive(alerts_dir, age=60)
    started = time.monotonic()
    proc = start_hook(alerts_dir, permission(), wait="100", alive=False, alive_max="20")
    assert finish(proc) == (0, b"")
    assert time.monotonic() - started < 2
    assert leftovers(alerts_dir) == []


@pytest.mark.parametrize("content", ["", "{half", "[1]", '{"at": "now"}', '{"at": true}', '{"at": NaN}'])
def test_hook_with_an_unreadable_sign_of_life_leaves_at_once(alerts_dir, content):
    alerts_dir.mkdir()
    (alerts_dir / ".service-alive").write_text(content, encoding="utf-8")
    assert finish(start_hook(alerts_dir, permission(), wait="100", alive=False)) == (0, b"")
    assert leftovers(alerts_dir) == []


def test_hook_with_a_sign_in_the_future_still_waits(alerts_dir):
    write_alive(alerts_dir, age=-3600)   # the service clock is ahead of ours: alive
    proc = start_hook(alerts_dir, permission(), wait="20", alive=False, alive_max="20")
    try:
        assert wait_for(lambda: request_files(alerts_dir))
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.communicate()


def test_hook_leaves_and_removes_its_request_when_the_sign_of_life_ages(alerts_dir):
    proc = start_hook(alerts_dir, permission(), wait="60", alive_max="1.5")
    try:
        assert wait_for(lambda: request_files(alerts_dir))   # fresh sign: the request is written
        started = time.monotonic()
        out, _ = proc.communicate(timeout=15)   # nobody renews it: the hook gives up by itself
    finally:
        proc.kill()
    assert (proc.returncode, out) == (0, b"")
    assert time.monotonic() - started < 10
    assert leftovers(alerts_dir) == []


def test_hook_keeps_waiting_while_the_sign_is_renewed(alerts_dir):
    proc = start_hook(alerts_dir, permission(), wait="60", alive_max="1.5")
    try:
        assert wait_for(lambda: request_files(alerts_dir))
        for _ in range(8):   # about 3 s, twice the limit
            write_alive(alerts_dir)
            time.sleep(0.4)
        assert proc.poll() is None and request_files(alerts_dir)
        request_files(alerts_dir)[0].with_suffix(".answer").write_text("deny", encoding="utf-8")
        out, _ = proc.communicate(timeout=10)
    finally:
        proc.kill()
    assert json.loads(out.decode("utf-8"))["hookSpecificOutput"]["decision"]["behavior"] == "deny"


def test_hook_notification_is_written_even_without_a_sign_of_life(alerts_dir):
    assert run_hook(alerts_dir, notify())[:2] == (0, "")
    assert (alerts_dir / "sess-1.json").exists()


def test_the_hook_clearing_events_keep_the_sign_of_life(alerts_dir):
    write_alive(alerts_dir)
    run_hook(alerts_dir, {"session_id": "sess-1", "hook_event_name": "Stop"})
    run_hook(alerts_dir, {**permission(), "hook_event_name": "PostToolUse"})
    assert (alerts_dir / ".service-alive").exists()


# --------------------------------------------------------------------------- #
# launcher: a missing script must not exit with code 2 (= deny)
# --------------------------------------------------------------------------- #
def launcher(script):
    return [sys.executable, "-I", "-S", "-c",
            f"import os,runpy,sys; p=r'{script}'; os.path.isfile(p) or sys.exit(0); runpy.run_path(p, run_name='__main__')"]


def test_launcher_with_a_missing_script_exits_0_silently(tmp_path):
    missing = (tmp_path / "nope" / "claude_alert_hook.py").as_posix()
    proc = REAL_POPEN(launcher(missing), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate(b"{}", timeout=30)
    assert (proc.returncode, out, err) == (0, b"", b"")
    bare = REAL_POPEN([sys.executable, "-I", "-S", missing], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    bare.communicate(timeout=30)
    assert bare.returncode == 2  # the reason the launcher exists


def test_launcher_runs_the_real_script_and_passes_its_exit_through(alerts_dir):
    env = {**os.environ, "PANEL_COMPANION_ALERTS": str(alerts_dir)}
    proc = REAL_POPEN(launcher(HOOK.as_posix()), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE, env=env)
    out, err = proc.communicate(json.dumps(notify()).encode(), timeout=30)
    assert (proc.returncode, out, err) == (0, b"", b"")
    assert (alerts_dir / "sess-1.json").exists()


def test_docs_carry_the_launcher_command():
    docs = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    command = ("-I -S -c \"import os,runpy,sys; p=r'C:/path/to/scripts/claude_alert_hook.py'; "
               "os.path.isfile(p) or sys.exit(0); runpy.run_path(p, run_name='__main__')\"")
    assert command.replace('\\"', '"') in docs or command in docs
    assert '"timeout": 300' in docs and '"onFailure": "continue"' in docs


def test_hook_notification_is_skipped_when_a_request_alert_is_up(alerts_dir):
    alerts_dir.mkdir()
    (alerts_dir / "sess-1-abc123.json").write_text("{}", encoding="utf-8")
    assert run_hook(alerts_dir, notify())[:2] == (0, "")
    assert not (alerts_dir / "sess-1.json").exists()
    run_hook(alerts_dir, notify(sid="sess-2"))  # another session still gets its fallback
    assert (alerts_dir / "sess-2.json").exists()


def test_hook_clear_removes_every_file_of_the_session_only(alerts_dir):
    alerts_dir.mkdir()
    mine = ["sess-1.json", "sess-1-aaa111.json", "sess-1-bbb222.json", "sess-1-aaa111.answer"]
    others = ["sess-2.json", "sess-2-aaa111.json", "sess-10.json", "sess-10-aaa111.answer"]
    for name in mine + others:
        (alerts_dir / name).write_text("{}", encoding="utf-8")
    run_hook(alerts_dir, {"session_id": "sess-1", "hook_event_name": "Stop"})
    assert sorted(p.name for p in alerts_dir.iterdir()) == sorted(others)


def test_hook_permission_request_without_session_is_silent(alerts_dir):
    assert run_hook(alerts_dir, None, raw='{"hook_event_name":"PermissionRequest"}')[:2] == (0, "")
    assert not alerts_dir.exists()


def put_request(rt, sid="s1-aaa111", at=None, tool="Bash", summary="ls", actionable=True, truncated=False, **extra):
    rt.adir.mkdir(parents=True, exist_ok=True)
    request = {"tool": tool, "summary": summary}
    if truncated is not None:
        request["truncated"] = truncated
    data = {"session": "s1", "project": "proj", "message": "Claude quer usar " + tool,
            "at": rt.now[0] if at is None else at, "request": request,
            "actionable": actionable, **extra}
    (rt.adir / f"{sid}.json").write_text(json.dumps(data), encoding="utf-8")


def respond(rt, args=None, nonce=True, **changes):
    """A responder touch; `nonce=True` echoes the secret the service sent to the View."""
    data = {"tool": "pc_alerta_responder"}
    if args is not None:
        if nonce is True and isinstance(args, dict) and isinstance(args.get("id"), str):
            secret = rt._alerts_nonce.get(args["id"])
            args = {**args, **({"nonce": secret} if secret else {})}
        elif isinstance(nonce, str) and isinstance(args, dict):
            args = {**args, "nonce": nonce}
        data["arguments"] = args
    rt._seq = getattr(rt, "_seq", 0) + 1
    event = {"id": rt._seq, "tipo": "toque", "em": rt.now[0] * 1000, "apresentacao": "p", "dados": data, **changes}
    rt.client_.events.append(event)
    rt.tick()


def answers(rt):
    return sorted(p.name for p in rt.adir.glob("*.answer"))


@pytest.mark.parametrize("language,text", [("en", "Claude wants to use Bash"), ("pt-BR", "Claude quer usar Bash")])
def test_actionable_alert_message_follows_the_panel_language(rt, language, text):
    from dataclasses import replace
    rt.config = replace(rt.config, view=ViewSection(language=language))
    # an old hook still writes the Portuguese text: the service rebuilds it from request.tool
    put_request(rt, message="Claude quer usar Bash")
    rt.tick()
    assert rt.client_.alert_updates()[-1][0]["message"] == text


def test_alert_without_a_tool_keeps_its_message(rt):
    put(rt, "s1", message="Claude is waiting for your input")
    rt.tick()
    assert rt.client_.alert_updates()[-1][0]["message"] == "Claude is waiting for your input"


def test_actionable_alert_reaches_the_view_with_its_request(rt):
    put_request(rt, summary="rm -rf build")
    rt.tick()
    alert = rt.client_.alert_updates()[-1][0]
    assert alert["actionable"] is True
    assert alert["request"] == {"tool": "Bash", "summary": "rm -rf build", "truncated": False}
    assert isinstance(alert["nonce"], str) and len(alert["nonce"]) >= 20
    assert rt.client_.alert_payloads()[-1]["allow_approve"] is True


@pytest.mark.parametrize("decision", ["allow", "deny"])
def test_responder_writes_the_answer(rt, decision):
    put_request(rt)
    rt.tick()
    respond(rt, {"id": "s1-aaa111", "decisao": decision})
    assert answers(rt) == ["s1-aaa111.answer"]
    assert (rt.adir / "s1-aaa111.answer").read_text(encoding="utf-8") == decision
    assert (rt.adir / "s1-aaa111.json").exists()  # the hook removes it, not the service
    assert [n for n in os.listdir(rt.adir) if n.endswith(".tmp")] == []


@pytest.mark.parametrize("args", [
    None, {}, {"id": "s1-aaa111"}, {"id": "s1-aaa111", "decisao": "maybe"},
    {"id": "s1-aaa111", "decisao": ["allow"]}, {"id": "../x", "decisao": "allow"},
    {"id": "..\\x", "decisao": "deny"}, {"id": "a/b", "decisao": "allow"}, {"id": 5, "decisao": "allow"},
    {"id": "nope-123456", "decisao": "allow"}, {"id": "x" * 81, "decisao": "allow"},
    {"id": "s1-aaa111.json", "decisao": "allow"},
])
def test_responder_rejects_bad_requests(rt, args, tmp_path):
    put_request(rt)
    rt.tick()
    respond(rt, args)
    assert answers(rt) == []
    assert not (tmp_path / "x.answer").exists()


def test_responder_ignores_non_actionable_alerts(rt):
    put(rt, "plain")
    put_request(rt, sid="fake-bbb222", actionable=False)
    rt.tick()
    respond(rt, {"id": "plain", "decisao": "deny"})
    respond(rt, {"id": "fake-bbb222", "decisao": "deny"})
    assert answers(rt) == []


def test_responder_ignores_a_stale_alert(rt):
    put_request(rt, at=rt.now[0] - 3600)
    respond(rt, {"id": "s1-aaa111", "decisao": "deny"})
    assert answers(rt) == []


def test_responder_ignores_old_touches(rt):
    put_request(rt)
    rt.tick()
    respond(rt, {"id": "s1-aaa111", "decisao": "deny"}, em=(rt.now[0] - 3600) * 1000)
    assert answers(rt) == []


def test_responder_is_builtin_not_a_configured_action(rt):
    assert rt.runner.knows("pc_alerta_responder") is False


def test_allow_approve_false_only_lets_deny_through(rt):
    rt.config = replace(rt.config, alerts=replace(rt.config.alerts, allow_approve=False))
    put_request(rt)
    rt.tick()
    assert rt.client_.alert_payloads()[-1]["allow_approve"] is False
    respond(rt, {"id": "s1-aaa111", "decisao": "allow"})
    assert answers(rt) == []
    respond(rt, {"id": "s1-aaa111", "decisao": "deny"})
    assert (rt.adir / "s1-aaa111.answer").read_text(encoding="utf-8") == "deny"


def test_allow_approve_config(load_config, raw_config):
    assert load_config(raw_config).alerts.allow_approve is False  # off unless asked for
    raw_config["alerts"] = {"allow_approve": True}
    assert load_config(raw_config).alerts.allow_approve is True
    raw_config["alerts"] = {"allow_approve": "no"}
    with pytest.raises(ConfigError):
        load_config(raw_config)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_view_has_the_approve_and_deny_buttons(template, make_config):
    html = view.render(make_config(view=ViewSection(template=template)))
    for needle in ('id="alerta-negar"', 'id="alerta-aprovar"', 'id="alerta-x"', "pc_alerta_responder",
                   "ALERTA_SEGURAR_MS = 1500", "ALERTA_MOVE_PX = 12", "pointerdown", "allow_approve",
                   "#b91c1c", "#7f1d1d", "actionable", "decisao"):
        assert needle in html, needle
    script = html[html.index("function aplicarAlertas"):html.index("window.simularAlertas")]
    assert "chamar(" not in script
    assert "req('tools/call'" in script


# --------------------------------------------------------------------------- #
# runtime: who may answer, secrets, leftovers, delivery
# --------------------------------------------------------------------------- #
def test_responder_from_another_presentation_is_ignored(rt):
    put_request(rt)
    rt.tick()
    respond(rt, {"id": "s1-aaa111", "decisao": "deny"}, apresentacao="someone-else")
    assert answers(rt) == []
    respond(rt, {"id": "s1-aaa111", "decisao": "deny"}, apresentacao="")
    assert answers(rt) == []
    respond(rt, {"id": "s1-aaa111", "decisao": "deny"})
    assert answers(rt) == ["s1-aaa111.answer"]


def test_dismiss_from_another_presentation_is_ignored(rt):
    put(rt, "s1")
    rt.tick()
    dismiss(rt, {"id": "s1"}, apresentacao="someone-else")
    assert (rt.adir / "s1.json").exists()


def test_responder_needs_the_alert_nonce(rt):
    put_request(rt)
    rt.tick()
    for nonce in (False, "wrong", "", 5):
        respond(rt, {"id": "s1-aaa111", "decisao": "deny"}, nonce=nonce)
    assert answers(rt) == []
    respond(rt, {"id": "s1-aaa111", "decisao": "deny", "nonce": None}, nonce=False)
    assert answers(rt) == []


def test_nonce_is_per_alert_memory_only_and_goes_to_the_view(rt):
    put_request(rt, sid="s1-aaa111")
    put_request(rt, sid="s1-bbb222", at=10_001.0)
    put(rt, "plain", at=10_002.0)
    rt.tick()
    n1, n2 = rt._alerts_nonce["s1-aaa111"], rt._alerts_nonce["s1-bbb222"]
    assert n1 != n2 and "plain" not in rt._alerts_nonce
    sent = {a["id"]: a.get("nonce") for a in rt.client_.alert_updates()[-1]}
    assert sent == {"s1-aaa111": n1, "s1-bbb222": n2, "plain": None}
    for path in rt.adir.iterdir():
        assert n1 not in path.read_text(encoding="utf-8") and n2 not in path.read_text(encoding="utf-8")
    respond(rt, {"id": "s1-bbb222", "decisao": "deny"}, nonce=n1)  # another alert's secret
    assert answers(rt) == []
    (rt.adir / "s1-aaa111.json").unlink()
    rt.tick()
    assert "s1-aaa111" not in rt._alerts_nonce


def test_request_without_an_explicit_false_truncated_is_marked_truncated(rt):
    put_request(rt, truncated=None)
    put_request(rt, sid="s1-bbb222", truncated="no", at=10_001.0)
    put_request(rt, sid="s1-ccc333", summary="x" * 2000, at=10_002.0)
    rt.tick()
    flags = {a["id"]: a["request"]["truncated"] for a in rt.client_.alert_updates()[-1]}
    assert flags == {"s1-aaa111": True, "s1-bbb222": True, "s1-ccc333": True}


def test_actionable_alert_older_than_300s_is_dropped_and_deleted(rt):
    old = rt.adir / "s1-aaa111.json"
    put_request(rt, at=10_000.0 - 301)
    put(rt, "plain", at=10_000.0 - 301)  # a plain warning lives for max_age_s
    rt.tick()
    assert not old.exists() and (rt.adir / "plain.json").exists()
    assert [a["id"] for a in rt.client_.alert_updates()[-1]] == ["plain"]


def test_orphan_answers_and_old_tmp_files_are_swept(rt):
    rt.adir.mkdir()
    long_ago = time.time() - 3600
    files = {
        "gone-aaa111.answer": True,           # no request file: removed
        ".x.answer.tmp": True,                # old temp: removed
        ".fresh.tmp": False,                  # temp of a write in progress: kept
        "live-bbb222.answer": False,          # has a request file: kept
        "note.txt": False,
    }
    for name, old in files.items():
        path = rt.adir / name
        path.write_text("allow", encoding="utf-8")
        if old:
            os.utime(path, (long_ago, long_ago))
    put_request(rt, sid="live-bbb222")
    os.utime(rt.adir / "live-bbb222.answer", (long_ago, long_ago))
    rt.tick()
    assert sorted(p.name for p in rt.adir.iterdir()) == [".fresh.tmp", ".service-alive", "live-bbb222.answer", "live-bbb222.json", "note.txt"]


def test_rejected_alert_update_never_republishes_and_retries_every_30s(rt):
    from mcp_display_companion.client import ToolError

    calls, logs = [], []
    rt.log = logs.append

    def reject(data, presentation=None):
        calls.append(data)
        raise ToolError("rejected")

    rt.client_.update_view = reject
    put(rt, "s1")
    rt.tick()
    rt.tick()
    rt.now[0] += 10
    rt.tick()
    assert len(calls) == 1 and rt.client_.shows == []   # no republish, no retry within 30 s
    rt.now[0] += 25
    put(rt, "s1", at=10_000.0)  # same list
    rt.tick()
    assert len(calls) == 2 and rt.client_.shows == []
    assert sum("rejected" in m for m in logs) == 1 and sum("not delivered" in m for m in logs) == 1
    put(rt, "s2", at=10_100.0)   # a different list is tried at once
    rt.tick()
    assert len(calls) == 3


def test_alert_list_reaches_the_view_again_after_a_rejection_clears(rt):
    from mcp_display_companion.client import ToolError

    ok = rt.client_.update_view
    state = {"fail": True}

    def flaky(data, presentation=None):
        if state["fail"]:
            raise ToolError("rejected")
        ok(data, presentation)

    rt.client_.update_view = flaky
    put(rt, "s1")
    rt.tick()
    state["fail"] = False
    rt.now[0] += 31
    rt.tick()
    assert len(rt.client_.alert_updates()) == 1


def test_warning_when_alerts_dir_comes_from_config(rt, tmp_path):
    logs = []
    rt.log = logs.append
    rt._warn_alerts_dir()
    assert logs == []
    rt.config = replace(rt.config, alerts=replace(rt.config.alerts, dir_from_config=True))
    rt._warn_alerts_dir()
    assert len(logs) == 1 and "PANEL_COMPANION_ALERTS" in logs[0]


def test_alerts_dir_from_config_is_flagged(load_config, raw_config):
    assert load_config(raw_config).alerts.dir_from_config is False
    raw_config["alerts"] = {"dir": "x/y"}
    assert load_config(raw_config).alerts.dir_from_config is True


def test_brightness_is_not_restored_while_resting(rt):
    put(rt, "s1")
    rt.tick()
    rt.screen_dark = True
    (rt.adir / "s1.json").unlink()
    rt.client_.brightness.clear()
    rt.tick()
    assert rt.client_.brightness == []


# --------------------------------------------------------------------------- #
# View: the hold, the visible text, rest mode
# --------------------------------------------------------------------------- #
def view_script(template):
    html = template.read_text(encoding="utf-8")
    start = html.index("<script>") + len("<script>")
    return html[start:html.index("</script>", start)]


def alert_js(template):
    script = view_script(template)
    return script[script.index("var alertasDispensados"):script.index("window.simularAlertas")]


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_view_hold_approves_only_the_alert_it_started_on(template):
    block = alert_js(template)
    assert "responderAlerta(agora, 'allow')" in block
    assert "alertaAssinatura(agora) !== chave" in block               # same alert as at pointerdown
    assert "var chave = alertaAssinatura(alvo)" in block              # captured at pointerdown
    assert "function responderAlerta(a, decisao)" in block            # the target is a parameter
    assert "alertaChave(alertaAtual) !== alertaChave(a)" in block
    assert "responderAlerta('allow')" not in block and "responderAlerta('deny')" not in block
    assert "negarAlvo = alertaAtual" in block and "responderAlerta(alvo, 'deny')" in block
    apply_ = block[block.index("function aplicarAlertas"):block.index("function dispensarAlerta")]
    assert "alertaCancelarSegurar()" in apply_ and "if (mudou)" in apply_


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_view_sends_the_nonce_and_never_claims_a_send_that_did_not_happen(template):
    block = alert_js(template)
    send = block.index("req('tools/call', {name: 'pc_alerta_responder'")
    assert "nonce: a.nonce" in block[send:send + 160]
    guard = block[:send]
    assert guard.rindex("if (!pronto)") > guard.index("function responderAlerta")
    assert block.index("tx('alert_sent')") > send                            # the notice comes after the send


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_view_aprovar_needs_a_complete_and_seen_text(template, make_config):
    html = view.render(make_config(view=ViewSection(template=template)))
    block = alert_js(template)
    assert "r.truncated !== false" in block
    assert "t2.scrollHeight <= t2.clientHeight + 2 || alertaLido" in block
    assert "tx('alert_long_text')" in block
    assert 'id="alerta-aviso"' in html
    css = html[html.index("#alerta-t2{"):html.index("</style>")]
    mono = css[css.index("#alerta-t2.mono"):]
    mono = mono[:mono.index("}") + 1]
    assert "overflow-y:auto" in mono and "white-space:pre-wrap" in mono and "-webkit-line-clamp:unset" in mono
    assert "#alerta.aprovar-off #alerta-aprovar" in css
    assert "alertaPodeAprovar = false" in block                        # fail closed until the PC says otherwise


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_view_does_not_rest_while_an_alert_shows(template):
    script = view_script(template)
    watcher = script[script.index("function restWatcher"):script.index("function restWatcher") + 300]
    assert "contains('alerta-on')" in watcher
    enter = script[script.index("function enterRest"):script.index("function wakeUp")]
    assert "contains('alerta-on')" in enter
    block = alert_js(template)
    assert "wakeUp();" in block[block.index("function aplicarAlertas"):block.index("function dispensarAlerta")]


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_view_script_passes_node_check(template, make_config, tmp_path):
    import shutil

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    html = view.render(make_config(view=ViewSection(template=template)))
    start = html.index("<script>") + len("<script>")
    path = tmp_path / "view.js"
    path.write_text(html[start:html.index("</script>", start)], encoding="utf-8")
    proc = REAL_POPEN([node, "--check", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _, err = proc.communicate(timeout=60)
    assert proc.returncode == 0, err.decode()


# --------------------------------------------------------------------------- #
# sign of life written by the service
# --------------------------------------------------------------------------- #
def alive_path(rt):
    return rt.adir / ".service-alive"


def test_tick_renews_the_sign_of_life(rt):
    before = time.time()
    rt.tick()
    data = json.loads(alive_path(rt).read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid() and before - 1 <= data["at"] <= time.time() + 1
    assert [p.name for p in rt.adir.iterdir()] == [".service-alive"]   # no temp file left
    time.sleep(0.02)
    rt.tick()
    assert json.loads(alive_path(rt).read_text(encoding="utf-8"))["at"] > data["at"]


def test_sign_of_life_is_not_renewed_while_the_panel_is_unreachable(rt):
    from mcp_display_companion.client import PanelError

    def down(since):
        raise PanelError("phone off")

    rt.client_.read_events = down
    with pytest.raises(PanelError):
        rt.tick()
    assert not alive_path(rt).exists()


def test_sign_of_life_is_removed_on_reconnect_and_on_stop(rt, monkeypatch):
    rt.tick()
    assert alive_path(rt).exists()
    monkeypatch.setattr("mcp_display_companion.runtime.time.sleep", lambda s: None)
    rt._reconnect()
    assert not alive_path(rt).exists()

    class Stop(BaseException):
        pass

    rt.tick()
    monkeypatch.setattr("mcp_display_companion.runtime.threading.Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: None})())

    def connect():
        raise Stop

    rt.client_.connect = connect
    with pytest.raises(Stop):
        rt.run()
    assert not alive_path(rt).exists()


def test_disabled_alerts_write_no_sign_of_life(rt):
    rt.config = replace(rt.config, alerts=replace(rt.config.alerts, enabled=False))
    rt.tick()
    assert not rt.adir.exists()


def test_sign_of_life_is_neither_an_alert_nor_an_orphan(rt):
    rt.tick()
    old = time.time() - 3600
    os.utime(alive_path(rt), (old, old))
    put(rt, "s1")
    rt.tick()
    assert [a["id"] for a in rt.client_.alert_updates()[-1]] == ["s1"]   # not shown as an alert
    assert alive_path(rt).exists()                                       # not swept, and renewed
    assert json.loads(alive_path(rt).read_text(encoding="utf-8"))["at"] > old


def test_sign_of_life_write_failure_is_logged_once(rt, monkeypatch):
    logs = []
    rt.log = logs.append

    def denied(*args):
        raise OSError("denied")

    monkeypatch.setattr("mcp_display_companion.runtime.os.replace", denied)
    rt.tick()
    rt.tick()
    assert sum("sign of life" in m for m in logs) == 1
