import pytest
from mcp_display_companion.actions import ActionRunner, ActionResult
from mcp_display_companion.config import Action


def runner(make_config, host, kind, params, panel=None):
    return ActionRunner(make_config([Action('pc_action', 'Action', kind, params=params)]), host, panel)


def test_unknown_action(make_config, host):
    assert ActionRunner(make_config(), host).run('foreign') is None
    assert host.calls == []


@pytest.mark.parametrize('kind,params,method,arg', [
    ('media', {'command': 'next'}, 'media', 'next'),
    ('key', {'vk': 175}, 'press_key', 175),
    ('volume', {'direction': 'mute'}, 'volume', 'mute'),
])
def test_basic_actions(make_config, host, kind, params, method, arg):
    assert runner(make_config, host, kind, params).run('pc_action').ok is True
    assert host.calls == [(method, (arg,), {})]


@pytest.mark.parametrize('exists', [True, False])
def test_folder(make_config, host, tmp_path, monkeypatch, exists):
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    path = tmp_path / 'folder'
    if exists: path.mkdir()
    result = runner(make_config, host, 'folder', {'path': '%USERPROFILE%/folder'}).run('pc_action')
    assert result.ok == exists
    assert host.calls == ([('open_folder', (path,), {'reuse_window': False})] if exists else [])


def test_folder_reuse_window_is_opt_in(make_config, host, tmp_path, monkeypatch):
    """Navigating an open Explorer window is the worst case: only when asked."""
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    path = tmp_path / 'folder'
    path.mkdir()
    params = {'path': '%USERPROFILE%/folder', 'reuse_window': True}
    assert runner(make_config, host, 'folder', params).run('pc_action').ok
    assert host.calls == [('open_folder', (path,), {'reuse_window': True})]


@pytest.mark.parametrize('target', ['spotify:', '$HOME/app.exe', '%USERPROFILE%/app.exe'])
def test_app_target(make_config, host, monkeypatch, tmp_path, target):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    assert runner(make_config, host, 'app', {'target': target}).run('pc_action').ok
    expected = 'spotify:' if target == 'spotify:' else str(tmp_path / 'app.exe')
    assert host.calls == [('open_app', (expected,), {})]


def test_url_options(make_config, host):
    params = {'url': 'https://example.test', 'browser': 'firefox', 'new_window': True}
    assert runner(make_config, host, 'url', params).run('pc_action').ok
    assert host.calls == [('open_url', ('https://example.test',), {'browser': 'firefox', 'new_window': True})]


def test_url_opens_new_window_by_default(make_config, host):
    """A tab in a browser already open stays behind other windows."""
    assert runner(make_config, host, 'url', {'url': 'https://example.test'}).run('pc_action').ok
    assert host.calls == [('open_url', ('https://example.test',), {'browser': '', 'new_window': True})]


def test_url_tab_only_when_asked(make_config, host):
    params = {'url': 'https://example.test', 'new_window': False}
    assert runner(make_config, host, 'url', params).run('pc_action').ok
    assert host.calls == [('open_url', ('https://example.test',), {'browser': '', 'new_window': False})]


@pytest.mark.parametrize('with_cwd,detached', [(False, None), (True, False)])
def test_command(make_config, host, monkeypatch, tmp_path, with_cwd, detached):
    monkeypatch.setenv('HOME', str(tmp_path))
    params = {'cmd': ['fake.exe', '--flag']}
    if with_cwd: params['cwd'] = '$HOME'
    if detached is not None: params['detached'] = detached
    assert runner(make_config, host, 'command', params).run('pc_action').ok
    assert host.calls == [('run_command', (['fake.exe', '--flag'],),
                           {'cwd': tmp_path if with_cwd else None, 'detached': True if detached is None else detached})]


def test_display(make_config, host):
    class Panel:
        def __init__(self): self.values = []
        def set_display(self, brightness): self.values.append(brightness)
    panel = Panel()
    assert runner(make_config, host, 'display', {'brightness': 35}, panel).run('pc_action').ok
    assert panel.values == [35]
    assert not host.calls


def test_display_without_panel(make_config, host):
    assert runner(make_config, host, 'display', {}).run('pc_action').ok is False


def test_host_exception_is_result(make_config, host, monkeypatch):
    def fail(vk): raise RuntimeError('host exploded')
    monkeypatch.setattr(host, 'press_key', fail)
    result = runner(make_config, host, 'key', {'vk': 1}).run('pc_action')
    assert isinstance(result, ActionResult) and result.ok is False
    assert 'host exploded' in result.detail


# --------------------------------------------------------------------------- #
# hotkey_toggle: hold a key combination until the next tap
# --------------------------------------------------------------------------- #
def _hotkey(make_config, host, keys=('ctrl', 'win')):
    return runner(make_config, host, 'hotkey_toggle', {'keys': list(keys)})


def test_hotkey_toggle_on_presses_keys_in_order(make_config, host):
    r = _hotkey(make_config, host)
    assert r.run('pc_action').ok is True
    assert host.calls == [('key_down', (0x11,), {}), ('key_down', (0x5B,), {})]
    assert r.is_hotkey_active('pc_action') is True


def test_hotkey_toggle_off_releases_in_reverse_order(make_config, host):
    r = _hotkey(make_config, host)
    r.run('pc_action')
    host.calls.clear()
    assert r.run('pc_action').ok is True
    assert host.calls == [('key_up', (0x5B,), {}), ('key_up', (0x11,), {})]
    assert r.is_hotkey_active('pc_action') is False


def test_hotkey_toggle_accepts_names_hex_and_numbers(make_config, host):
    r = _hotkey(make_config, host, ('Shift', '0x41', 66, '67'))
    r.run('pc_action')
    assert [c[1][0] for c in host.calls] == [0x10, 0x41, 66, 67]


def test_hotkey_toggle_default_keys_are_ctrl_win(make_config, host):
    r = runner(make_config, host, 'hotkey_toggle', {})
    r.run('pc_action')
    assert [c[1][0] for c in host.calls] == [0x11, 0x5B]


def test_hotkey_toggle_failure_releases_what_was_pressed(make_config, host, monkeypatch):
    r = _hotkey(make_config, host)
    pressed = []

    def key_down(vk):
        pressed.append(vk)
        return len(pressed) < 2

    monkeypatch.setattr(host, 'key_down', key_down)
    result = r.run('pc_action')
    assert result.ok is False
    assert r.is_hotkey_active('pc_action') is False
    assert host.calls == [('key_up', (0x11,), {})]


def test_release_all_held_keys_lets_go_of_every_action(make_config, host):
    config = make_config([Action('pc_a', 'A', 'hotkey_toggle', params={'keys': ['ctrl', 'win']}),
                          Action('pc_b', 'B', 'hotkey_toggle', params={'keys': ['shift']})])
    r = ActionRunner(config, host)
    r.run('pc_a')
    r.run('pc_b')
    host.calls.clear()
    r.release_all_held_keys()
    assert host.calls == [('key_up', (0x5B,), {}), ('key_up', (0x11,), {}), ('key_up', (0x10,), {})]
    assert not r.is_hotkey_active('pc_a') and not r.is_hotkey_active('pc_b')
    host.calls.clear()
    r.release_all_held_keys()
    assert host.calls == []


def test_release_all_keeps_going_when_a_key_up_raises(make_config, host, monkeypatch):
    r = _hotkey(make_config, host)
    r.run('pc_action')
    released = []

    def key_up(vk):
        released.append(vk)
        raise RuntimeError('boom')

    monkeypatch.setattr(host, 'key_up', key_up)
    r.release_all_held_keys()
    assert released == [0x5B, 0x11]
    assert not r.is_hotkey_active('pc_action')


def test_hotkey_toggle_on_generic_host_fails_cleanly(make_config):
    from mcp_display_companion.host import Host
    messages = []
    generic = Host(log=messages.append)
    r = ActionRunner(make_config([Action('pc_action', 'A', 'hotkey_toggle', params={'keys': ['ctrl', 'win']})]),
                     generic)
    result = r.run('pc_action')
    assert result.ok is False
    assert r.is_hotkey_active('pc_action') is False
    assert any('key_down' in m and 'not implemented' in m for m in messages)
    r.release_all_held_keys()  # nothing held: must not raise


def test_hotkey_states_lists_every_toggle_action(make_config, host):
    config = make_config([Action('pc_a', 'A', 'hotkey_toggle', params={'keys': ['ctrl']}),
                          Action('pc_b', 'B', 'hotkey_toggle', params={'keys': ['shift']}),
                          Action('pc_k', 'K', 'key', params={'vk': 1})])
    r = ActionRunner(config, host)
    assert r.hotkey_states() == {'pc_a': False, 'pc_b': False}
    r.run('pc_b')
    assert r.hotkey_states() == {'pc_a': False, 'pc_b': True}


def test_release_configured_hotkeys_sends_key_up_for_every_toggle_action(make_config, host):
    config = make_config([Action('pc_a', 'A', 'hotkey_toggle', params={'keys': ['ctrl', 'win']}),
                          Action('pc_b', 'B', 'hotkey_toggle', params={'keys': ['shift']}),
                          Action('pc_k', 'K', 'key', params={'vk': 1})])
    ActionRunner(config, host).release_configured_hotkeys()
    assert host.calls == [('key_up', (0x5B,), {}), ('key_up', (0x11,), {}), ('key_up', (0x10,), {})]


def test_release_configured_hotkeys_does_nothing_without_toggle_actions(make_config, host):
    ActionRunner(make_config([Action('pc_k', 'K', 'key', params={'vk': 1})]), host).release_configured_hotkeys()
    assert host.calls == []


def test_release_configured_hotkeys_is_silent_on_the_generic_host(make_config):
    from mcp_display_companion.host import Host
    messages = []
    r = ActionRunner(make_config([Action('pc_a', 'A', 'hotkey_toggle', params={'keys': ['ctrl']})]),
                     Host(log=messages.append))
    r.release_configured_hotkeys()
    assert messages == []


def test_release_configured_hotkeys_survives_a_failing_host(make_config, host, monkeypatch):
    def boom(vk):
        raise RuntimeError('no keyboard')
    monkeypatch.setattr(host, 'key_up', boom)
    ActionRunner(make_config([Action('pc_a', 'A', 'hotkey_toggle', params={'keys': ['ctrl']})]),
                 host).release_configured_hotkeys()
