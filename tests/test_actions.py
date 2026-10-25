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
    assert host.calls == ([('open_folder', (path,), {})] if exists else [])


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
