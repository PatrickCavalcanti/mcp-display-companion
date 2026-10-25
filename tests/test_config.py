import json
import pytest
from mcp_display_companion.config import ConfigError, expand_path


def test_minimal_defaults(load_config, raw_config):
    cfg = load_config(raw_config)
    assert (cfg.loop.heartbeat_s, cfg.loop.confirm, cfg.panel.subscribe,
            cfg.panel.poll_ms, cfg.display.brightness) == (60, True, True, 700, 'max')
    assert cfg.actions_by_id == {'pc_key': cfg.actions[0]}


@pytest.mark.parametrize('env,file,expected', [(True, True, 'env'), (False, True, 'file'), (False, False, 'fake-token')])
def test_token_precedence(load_config, raw_config, tmp_path, monkeypatch, env, file, expected):
    if file:
        (tmp_path / 'token.json').write_text(json.dumps({'panel_token': 'file'}))
        raw_config['panel']['token_file'] = 'token.json'
    if env:
        monkeypatch.setenv('PANEL_TOKEN', 'env')
    assert load_config(raw_config).panel.token == expected


def test_missing_token_explains_choices(load_config, raw_config):
    del raw_config['panel']['token']
    with pytest.raises(ConfigError) as error:
        load_config(raw_config)
    assert 'secrets.json' in str(error.value) and 'PANEL_TOKEN' in str(error.value)


@pytest.mark.parametrize('url', [None, 'localhost/mcp', 'ftp://localhost'])
def test_invalid_panel_url(load_config, raw_config, url):
    raw_config['panel']['url'] = url
    with pytest.raises(ConfigError, match='panel.url'):
        load_config(raw_config)


@pytest.mark.parametrize('action_id', ['PC_key', 'pc key', ' pc_key '])
def test_invalid_id(load_config, raw_config, action_id):
    raw_config['actions'][0]['id'] = action_id
    with pytest.raises(ConfigError):
        load_config(raw_config)


def test_unknown_type_names_action(load_config, raw_config):
    raw_config['actions'][0]['type'] = 'alien'
    with pytest.raises(ConfigError, match='pc_key'):
        load_config(raw_config)


def test_duplicate_id(load_config, raw_config):
    raw_config['actions'] *= 2
    with pytest.raises(ConfigError, match='pc_key'):
        load_config(raw_config)


@pytest.mark.parametrize('kind,params', [
    ('media', {'command': 'stop'}), ('key', {}), ('key', {'vk': 0}), ('key', {'vk': 256}),
    ('volume', {'direction': 'sideways'}), ('folder', {}), ('app', {}),
    ('url', {'url': 'example.org'}), ('url', {'url': 'spotify:'}),
    ('command', {'cmd': 1}), ('command', {'cmd': ['ok', 1]}), ('command', {'cmd': []}),
    ('display', {'brightness': 'bright'}), ('display', {'brightness': -1}), ('display', {'brightness': 101}),
])
def test_invalid_action_params(load_config, raw_config, kind, params):
    raw_config['actions'] = [{'id': 'pc_bad', 'type': kind, **params}]
    with pytest.raises(ConfigError, match='pc_bad'):
        load_config(raw_config)


def test_command_string_becomes_list(load_config, raw_config):
    raw_config['actions'] = [{'id': 'pc_cmd', 'type': 'command', 'cmd': 'notepad.exe'}]
    assert load_config(raw_config).actions[0].params['cmd'] == ['notepad.exe']


@pytest.mark.parametrize('brightness', ['bad', -1, 101])
def test_invalid_display_section(load_config, raw_config, brightness):
    raw_config['display'] = {'brightness': brightness}
    with pytest.raises(ConfigError, match='display.brightness'):
        load_config(raw_config)


def test_pages_and_featured(load_config, raw_config):
    raw_config['actions'] = [
        {'id': 'pc_both', 'type': 'key', 'vk': 2, 'pages': [1, 2], 'featured': True},
        {'id': 'pc_two', 'type': 'key', 'vk': 3, 'pages': [2]},
    ]
    cfg = load_config(raw_config)
    assert [a.id for a in cfg.page1] == ['pc_both']
    assert [a.id for a in cfg.page2] == ['pc_both', 'pc_two']
    assert cfg.featured is cfg.actions_by_id['pc_both']


def test_page_one_requires_featured(load_config, raw_config):
    """Page 1 has only one slot: page: 1 without featured is an error."""
    raw_config['actions'] = [{'id': 'pc_one', 'type': 'key', 'vk': 1, 'page': 1}]
    with pytest.raises(ConfigError, match='pc_one.*featured'):
        load_config(raw_config)


def test_page_three_invalid(load_config, raw_config):
    raw_config['actions'][0]['page'] = 3
    with pytest.raises(ConfigError):
        load_config(raw_config)


@pytest.mark.parametrize('prefix', ['%USERPROFILE%', '$HOME', '~'])
def test_expand_paths(load_config, raw_config, monkeypatch, tmp_path, prefix):
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    assert expand_path(prefix + '/folder') == tmp_path / 'folder'
    raw_config['actions'] = [{'id': 'pc_folder', 'type': 'folder', 'path': prefix + '/folder'}]
    raw_config['panel']['cursor_file'] = 'state/cursor.json'
    cfg = load_config(raw_config)
    assert expand_path(cfg.actions[0].params['path']) == tmp_path / 'folder'
    assert cfg.panel.cursor_file == tmp_path / 'state' / 'cursor.json'


@pytest.mark.parametrize('weather', [
    {'latitude': 1, 'longitude': 2}, {'label': 'City'},
    {'label': 'City', 'latitude': 1}, {'label': 'City', 'longitude': 2},
])
def test_weather_requires_label_and_coordinates(load_config, raw_config, weather):
    raw_config['live'] = {'weather': {'enabled': True, **weather}}
    with pytest.raises(ConfigError, match='live.weather'):
        load_config(raw_config)


@pytest.mark.parametrize('latitude,longitude', [(0, 0), (0, 10), (10, 0)])
def test_explicit_zero_coordinates_are_valid(load_config, raw_config, latitude, longitude):
    raw_config['live'] = {'weather': {'enabled': True, 'label': 'City',
                         'latitude': latitude, 'longitude': longitude}}
    cfg = load_config(raw_config)
    assert (cfg.weather.latitude, cfg.weather.longitude) == (latitude, longitude)


@pytest.mark.parametrize('kind,params', [
    ('media', {'command': 'play_pause'}), ('media', {'command': 'next'}), ('media', {'command': 'previous'}),
    ('key', {'vk': 1}), ('key', {'vk': 255}),
    ('volume', {'direction': 'up'}), ('volume', {'direction': 'down'}), ('volume', {'direction': 'mute'}),
    ('url', {'url': 'https://example.test'}), ('url', {'url': 'http://example.test'}),
    ('display', {'brightness': 0}), ('display', {'brightness': 100}),
])
def test_valid_action_parameter_boundaries(load_config, raw_config, kind, params):
    raw_config['actions'] = [{'id': 'pc_valid', 'type': kind, **params}]
    assert load_config(raw_config).actions[0].params == params
