"""Settings page, rest mode, and state that persists across screen reloads."""

import json

import pytest

from mcp_display_companion import view
from mcp_display_companion.actions import ActionRunner
from mcp_display_companion.config import (
    Action,
    Config,
    ConfigError,
    DisplaySection,
    PanelSection,
    RestSection,
)
from mcp_display_companion.runtime import Runtime
from mcp_display_companion.state import StateStore

REST_ON = {"id": "pc_descanso_ativar", "type": "rest", "state": True, "pages": [3]}
REST_OFF = {"id": "pc_descanso_desativar", "type": "rest", "state": False, "pages": [3]}


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
def test_rest_defaults(load_config, raw_config):
    cfg = load_config(raw_config)
    assert cfg.rest.enabled is False
    assert cfg.rest.idle_s == 60
    assert cfg.page3 == []


def test_rest_section_is_read(load_config, raw_config):
    raw_config["rest"] = {"enabled": True, "idle_s": 45}
    cfg = load_config(raw_config)
    assert (cfg.rest.enabled, cfg.rest.idle_s) == (True, 45)


def test_idle_s_has_a_floor(load_config, raw_config):
    raw_config["rest"] = {"idle_s": 1}
    with pytest.raises(ConfigError, match="rest.idle_s"):
        load_config(raw_config)


@pytest.mark.parametrize("state", [None, "yes", 1, 0])
def test_rest_action_requires_boolean_state(load_config, raw_config, state):
    action = {"id": "pc_descanso", "type": "rest", "pages": [3]}
    if state is not None:
        action["state"] = state
    raw_config["actions"] = [action]
    with pytest.raises(ConfigError, match="pc_descanso"):
        load_config(raw_config)


def test_page_three_only_accepts_rest_actions(load_config, raw_config):
    raw_config["actions"] = [{"id": "pc_key", "type": "key", "vk": 175, "pages": [3]}]
    with pytest.raises(ConfigError, match="page 3"):
        load_config(raw_config)


def test_page_three_actions_are_listed(load_config, raw_config):
    raw_config["actions"] = [REST_ON, REST_OFF, {"id": "pc_key", "type": "key", "vk": 175}]
    cfg = load_config(raw_config)
    assert [a.id for a in cfg.page3] == ["pc_descanso_ativar", "pc_descanso_desativar"]


def test_state_file_defaults_next_to_the_cursor(load_config, raw_config, tmp_path):
    raw_config["panel"]["cursor_file"] = "state/cursor.json"
    cfg = load_config(raw_config)
    assert cfg.state_file == tmp_path / "state" / "state.json"


# --------------------------------------------------------------------------- #
# page 3 (settings)
# --------------------------------------------------------------------------- #
def rest_config(make_config, **kwargs):
    actions = [Action("pc_descanso_ativar", "Activate rest", "rest", pages=(3,),
                      params={"state": True}),
               Action("pc_descanso_desativar", "Deactivate rest", "rest", pages=(3,),
                      params={"state": False})]
    return make_config(actions, **kwargs)


def test_settings_page_renders_one_big_switch(make_config):
    html = view.render(rest_config(make_config, rest=RestSection(enabled=False, idle_s=45)))
    assert 'id="chave-descanso"' in html
    assert 'data-acao-ligar="pc_descanso_ativar"' in html
    assert 'data-acao-desligar="pc_descanso_desativar"' in html
    assert "45 s" in html
    # starts off: touch requests activation
    assert 'data-acao="pc_descanso_ativar"' in html


def test_settings_page_absent_without_rest_actions(make_config):
    html = view.render(make_config())
    assert 'class="config-item' not in html
    assert 'id="p3"' not in html


def test_pager_counts_pages(make_config):
    def dots(html):
        return html.count('data-p="')

    only_featured = Action("pc_key", "Key", "key", params={"vk": 175}, pages=(1,), featured=True)
    assert dots(view.render(make_config([only_featured]))) == 1

    with_shortcuts = view.render(
        make_config([Action("pc_key", "Key", "key", params={"vk": 175}, pages=(2,))])
    )
    assert dots(with_shortcuts) == 2

    # only settings: home page + settings
    assert dots(view.render(rest_config(make_config))) == 2

    # shortcuts + settings: three pages
    full = make_config(
        [Action("pc_key", "Key", "key", params={"vk": 175}, pages=(2,)),
         Action("pc_descanso_ativar", "Ativar", "rest", pages=(3,), params={"state": True}),
         Action("pc_descanso_desativar", "Desativar", "rest", pages=(3,), params={"state": False})]
    )
    assert dots(view.render(full)) == 3


def test_rest_mode_state_and_idle_ride_into_the_view(make_config):
    html = view.render(rest_config(make_config, rest=RestSection(enabled=True, idle_s=90)))
    assert "var restEnabled = 1 === 1;" in html
    assert "var restSeconds = 90;" in html

    disabled = view.render(rest_config(make_config, rest=RestSection(enabled=False, idle_s=20)))
    assert "var restEnabled = 0 === 1;" in disabled


def test_shortcut_grid_is_five_columns(make_config):
    html = view.render(make_config())
    assert "grid-template-columns:repeat(5,1fr)" in html


# --------------------------------------------------------------------------- #
# state on disk
# --------------------------------------------------------------------------- #
def test_state_roundtrip(tmp_path):
    store = StateStore(tmp_path / "state.json")
    assert store.get("rest", False) is False
    store.set("rest", True)
    assert StateStore(tmp_path / "state.json").get("rest") is True


def test_state_with_broken_file_falls_back_to_default(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{this is not json", encoding="utf-8")
    assert StateStore(path).get("rest", False) is False


def test_state_survives_a_readonly_disk_in_memory(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "state.json")
    monkeypatch.setattr("pathlib.Path.write_text", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    store.set("rest", True)
    assert store.get("rest") is True


# --------------------------------------------------------------------------- #
# service execution
# --------------------------------------------------------------------------- #
class FakeHost:
    def __init__(self):
        self.calls = []


class FakeStream:
    def is_set(self):
        return False

    def clear(self):
        pass


class FakeClient:
    def __init__(self):
        self.updates = []
        self.shows = []
        self.stream_ok = FakeStream()

    def connect(self):
        pass

    def subscribe(self, on_update):
        return None

    def close_stream(self):
        pass

    def read_events(self, since):
        return {"cursor": since if since is not None else 0, "eventos": []}

    def update_view(self, data, presentation=None):
        self.updates.append((data, presentation))

    def show_view(self, html, **kwargs):
        self.shows.append(kwargs)
        return "screen-1"

    def set_display(self, brightness):
        self.brightness = getattr(self, "brightness", []) + [brightness]


class FakeLive:
    def due(self, now, force=False):
        return {}

    def ack(self, data):
        pass

    def snapshot(self):
        return {}

    def refresh_media(self):
        return None


def make_runtime(tmp_path, monkeypatch, rest_enabled=False):
    class UnusedLoop:
        def run_until_complete(self, coro):
            raise AssertionError("unexpected async call")

        def close(self):
            pass

    monkeypatch.setattr("mcp_display_companion.runtime.asyncio.new_event_loop", UnusedLoop)
    cfg = Config(
        panel=PanelSection("http://127.0.0.1/mcp", "fake-token",
                           cursor_file=tmp_path / "cursor.json"),
        actions=[Action("pc_descanso_ativar", "Ativar", "rest", pages=(3,),
                        params={"state": True}),
                 Action("pc_descanso_desativar", "Desativar", "rest", pages=(3,),
                        params={"state": False})],
        display=DisplaySection(enabled=True, brightness="max", refresh_min=5),
        rest=RestSection(enabled=rest_enabled, idle_s=30),
        state_file=tmp_path / "state.json",
    )
    client = FakeClient()
    now_ref = [1000.0]
    runtime = Runtime(cfg, client=client, host=FakeHost(), live=FakeLive(), clock=lambda: now_ref[0])
    runtime.now_ref = now_ref
    return runtime, client


def test_rest_action_flips_and_persists(tmp_path, monkeypatch):
    rt, client = make_runtime(tmp_path, monkeypatch)
    result = rt.runner.run("pc_descanso_ativar")
    assert result.ok is True
    assert rt.rest is True
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["rest"] is True

    rt.runner.run("pc_descanso_desativar")
    assert rt.rest is False


def test_connect_sends_the_rest_preference(tmp_path, monkeypatch):
    """View reloads: it needs to know whether to dim."""
    rt, client = make_runtime(tmp_path, monkeypatch, rest_enabled=True)
    rt._connect()
    sent = [data for data, _ in client.updates]
    assert {"rest": {"enabled": True, "seconds": 30}} in sent


def test_touch_on_rest_action_also_reports_the_state(tmp_path, monkeypatch, touch):
    rt, client = make_runtime(tmp_path, monkeypatch)
    rt.cursor = 10
    event = touch(id=11, dados={"tool": "pc_descanso_ativar"})
    rt._handle(event, 1000.0)
    keys = {key for d, _ in client.updates for key in d}
    assert "confirmacao" in keys and "rest" in keys


# --------------------------------------------------------------------------- #
# device brightness with rest mode
# --------------------------------------------------------------------------- #
def test_dark_screen_lowers_the_device_brightness(tmp_path, monkeypatch, touch):
    rt, client = make_runtime(tmp_path, monkeypatch)
    rt._handle(touch(id=11, dados={"tool": "pc_rest_entered"}), 1000.0)
    assert client.brightness == ["min"]
    assert rt.screen_dark is True


def test_waking_restores_the_normal_brightness(tmp_path, monkeypatch, touch):
    rt, client = make_runtime(tmp_path, monkeypatch)
    rt._handle(touch(id=11, dados={"tool": "pc_rest_entered"}), 1000.0)
    rt._handle(touch(id=12, dados={"tool": "pc_rest_left"}), 1000.0)
    assert client.brightness == ["min", "max"]
    assert rt.screen_dark is False


def test_brightness_refresh_does_not_light_the_dark_screen(tmp_path, monkeypatch, touch):
    """Periodic brightness refresh must not wake dimmed screen."""
    rt, client = make_runtime(tmp_path, monkeypatch)
    rt.config = type(rt.config)(**{**rt.config.__dict__,
                                   "display": DisplaySection(enabled=True, refresh_min=0)})
    rt._handle(touch(id=11, dados={"tool": "pc_rest_entered"}), 1000.0)
    assert client.brightness == ["min"]
    for _ in range(3):
        rt.now_ref[0] += 70      # past heartbeat and refresh interval
        rt.tick()
    assert client.brightness == ["min"], "brightness cycle must not relight screen"


def test_rest_notifications_are_not_logged_as_foreign_actions(tmp_path, monkeypatch, touch):
    rt, client = make_runtime(tmp_path, monkeypatch)
    rt._handle(touch(id=11, dados={"tool": "pc_rest_entered"}), 1000.0)
    assert not any("confirmacao" in data for data, _ in client.updates)


def test_display_disabled_means_no_brightness_call(tmp_path, monkeypatch, touch):
    rt, client = make_runtime(tmp_path, monkeypatch)
    rt.config = type(rt.config)(**{**rt.config.__dict__, "display": DisplaySection(enabled=False)})
    rt._handle(touch(id=11, dados={"tool": "pc_rest_entered"}), 1000.0)
    assert not hasattr(client, "brightness")


# --------------------------------------------------------------------------- #
# photo clock overlay switch (page 3, type `overlay`)
# --------------------------------------------------------------------------- #
def test_overlay_action_requires_boolean_state(load_config, raw_config):
    raw_config["actions"] = [{"id": "pc_relogio", "type": "overlay", "pages": [3]}]
    with pytest.raises(ConfigError, match="pc_relogio"):
        load_config(raw_config)


def test_page_three_accepts_overlay(load_config, raw_config):
    raw_config["actions"] = [
        {"id": "pc_relogio_on", "type": "overlay", "state": True, "pages": [3]},
        {"id": "pc_relogio_off", "type": "overlay", "state": False, "pages": [3]},
    ]
    cfg = load_config(raw_config)
    assert [a.type for a in cfg.page3] == ["overlay", "overlay"]


def test_settings_page_shows_one_switch_per_pair(make_config):
    cfg = make_config(
        [Action("pc_descanso_ativar", "Ativar", "rest", pages=(3,), params={"state": True}),
         Action("pc_descanso_desativar", "Desativar", "rest", pages=(3,), params={"state": False}),
         Action("pc_relogio_on", "Relogio", "overlay", pages=(3,), params={"state": True}),
         Action("pc_relogio_off", "Sem", "overlay", pages=(3,), params={"state": False})],
        rest=RestSection(enabled=False, idle_s=60),
    )
    html = view.render(cfg)
    assert 'id="chave-descanso"' in html
    assert 'id="chave-relogio"' in html
    assert html.count('class="config-item') == 2


def test_runtime_remembers_the_clock_overlay(tmp_path, monkeypatch):
    rt, client = make_runtime(tmp_path, monkeypatch)
    rt.runner.overlay(True)
    assert rt.photo_clock is True
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["photo_clock"] is True


def test_connect_sends_the_clock_overlay_state(tmp_path, monkeypatch):
    rt, client = make_runtime(tmp_path, monkeypatch, rest_enabled=False)
    rt.photo_clock = True
    rt._connect()
    sent = [data for data, _ in client.updates]
    assert {"photo_clock": {"enabled": True}} in sent
