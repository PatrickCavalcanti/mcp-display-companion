import json
import socket
import subprocess
import urllib.request
import pytest
from mcp_display_companion.config import Action, Config, PanelSection


class FakeHost:
    def __init__(self):
        self.calls = []
        self.info = None

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return True

    def media(self, command): return self._record('media', command)
    def press_key(self, vk): return self._record('press_key', vk)
    def volume(self, direction): return self._record('volume', direction)
    def open_folder(self, path): return self._record('open_folder', path)
    def open_app(self, target): return self._record('open_app', target)
    def open_url(self, url, **kwargs): return self._record('open_url', url, **kwargs)
    def run_command(self, cmd, **kwargs): return self._record('run_command', cmd, **kwargs)
    def media_info(self, **kwargs):
        self._record('media_info', **kwargs)
        return self.info


@pytest.fixture(autouse=True)
def isolated_io(monkeypatch, tmp_path):
    """Only the explicitly local HTTP fixture may use sockets; no process/OS actions."""
    from mcp_display_companion import config
    monkeypatch.delenv('PANEL_TOKEN', raising=False)
    monkeypatch.setattr(config, 'default_secrets_path', lambda: tmp_path / 'secrets.json')
    monkeypatch.setattr(config, 'default_cursor_path', lambda: tmp_path / 'cursor.json')
    def forbidden(*args, **kwargs):
        raise AssertionError('External I/O is forbidden in tests')
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.fixture
def host():
    return FakeHost()


@pytest.fixture
def make_config(tmp_path):
    def make(actions=None, **kwargs):
        return Config(panel=PanelSection('http://127.0.0.1/mcp', 'fake-token',
                      cursor_file=tmp_path / 'cursor.json', subscribe=False),
                      actions=actions if actions is not None else [Action('pc_key', 'Key', 'key', params={'vk': 175})],
                      **kwargs)
    return make


@pytest.fixture
def load_config(tmp_path):
    from mcp_display_companion.config import load
    def read(raw):
        path = tmp_path / 'input.json'
        path.write_text(json.dumps(raw), encoding='utf-8')
        return load(path)
    return read


@pytest.fixture
def raw_config():
    return {'panel': {'url': 'http://localhost/mcp', 'token': 'fake-token'},
            'actions': [{'id': 'pc_key', 'type': 'key', 'vk': 175}]}


@pytest.fixture
def touch():
    def make(**changes):
        return {'id': 42, 'tipo': 'toque', 'em': 1_000_000,
                'apresentacao': 'event-screen', 'dados': {'tool': 'pc_key'}, **changes}
    return make
