from collections import deque
import pytest
from mcp_display_companion.client import RateLimited, SessionGone, ToolError
from mcp_display_companion.config import DisplaySection
from mcp_display_companion.runtime import CursorStore, Runtime


class FakeClient:
    def __init__(self):
        self.batches = deque()
        self.reads = []
        self.updates = []
        self.shows = []
        self.fail_updates = 0
        self.rate_limited = 0
        self.read_error = None
        self.connections = 0

    def connect(self): self.connections += 1
    def read_events(self, since):
        self.reads.append(since)
        if self.read_error is not None:
            raise self.read_error
        return self.batches.popleft() if self.batches else {'cursor': since, 'eventos': []}
    def update_view(self, data, presentation=None):
        self.updates.append((data, presentation))
        if self.rate_limited:
            self.rate_limited -= 1
            raise RateLimited('panel rate limit exceeded (429)')
        if self.fail_updates:
            self.fail_updates -= 1
            raise ToolError('screen expired')
    def show_view(self, html, **kwargs):
        self.shows.append((html, kwargs))
        return f'screen-{len(self.shows)}'


class FakeLive:
    def __init__(self):
        self.calls = []
        self.acked = []
        self.delivered = {}
    def due(self, now, force=False):
        self.calls.append((now, force))
        return {'midia': {'titulo': 'Song'}} if force else {}
    def ack(self, data): self.acked.append(data)
    def snapshot(self): return dict(self.delivered)
    def refresh_media(self): return None


@pytest.fixture
def runtime(make_config, host, monkeypatch):
    # Runtime creates an asyncio loop even though these synchronous fakes never
    # use it. Avoid the Windows loop's internal socketpair entirely.
    class UnusedLoop:
        def run_until_complete(self, coro):
            raise AssertionError('Unexpected asynchronous host call')
        def close(self): pass
    monkeypatch.setattr('mcp_display_companion.runtime.asyncio.new_event_loop', UnusedLoop)
    client, live = FakeClient(), FakeLive()
    now = [1000.0]
    cfg = make_config(display=DisplaySection(enabled=False))
    instance = Runtime(cfg, client=client, host=host, live=live, clock=lambda: now[0])
    try:
        yield instance, client, host, live, now
    finally:
        instance._loop.close()


def test_cursor_roundtrip(tmp_path):
    store = CursorStore(tmp_path / 'nested' / 'cursor.json')
    assert store.load() is None
    store.save(42)
    assert CursorStore(store.path).load() == 42


@pytest.mark.parametrize('text', ['{broken', '[]', '{\"cursor\": \"42\"}'])
def test_cursor_corrupted(tmp_path, text):
    path = tmp_path / 'cursor.json'
    path.write_text(text)
    assert CursorStore(path).load() is None


def test_save_none_does_not_write(tmp_path):
    path = tmp_path / 'cursor.json'
    store = CursorStore(path)
    store.save(None)
    assert not path.exists()
    store.save(9)
    original = path.read_bytes()
    store.save(None)
    assert path.read_bytes() == original


def test_first_tick_only_seeds_cursor(runtime, touch):
    rt, client, host, _, _ = runtime
    client.batches.append({'cursor': 42, 'eventos': [touch()]})
    rt.tick()
    assert rt.cursor == 42 and rt.cursor_store.load() == 42
    assert client.reads == [None]
    assert not host.calls
    assert not any('confirmacao' in data for data, _ in client.updates)


def test_next_touch_executes_and_confirms(runtime, touch):
    rt, client, host, _, _ = runtime
    client.batches.extend([{'cursor': 41, 'eventos': []}, {'cursor': 42, 'eventos': [touch()]}])
    rt.tick()
    rt.tick()
    assert client.reads == [None, 41]
    assert host.calls == [('press_key', (175,), {})]
    assert ({'confirmacao': {'acao': 'pc_key', 'ok': True}}, 'event-screen') in client.updates


def test_old_touch_is_ignored(runtime, touch):
    rt, client, host, _, _ = runtime
    client.batches.extend([{'cursor': 41}, {'cursor': 42, 'eventos': [touch(em=0)]}])
    rt.tick()
    rt.tick()
    assert not host.calls
    assert not any('confirmacao' in data for data, _ in client.updates)


def test_gap_refreshes_without_replaying(runtime, touch):
    rt, client, host, live, _ = runtime
    # Seed, handle an event, then receive the same event in a gap response.
    client.batches.extend([{'cursor': 41}, {'cursor': 42, 'eventos': [touch()]},
                           {'cursor': 42, 'lacuna': True, 'eventos': [touch()]}])
    rt.tick()
    rt.tick()
    rt.tick()
    assert host.calls == [('press_key', (175,), {})]
    assert live.calls[-1] == (1000, True)
    assert any(data == {'midia': {'titulo': 'Song'}} for data, _ in client.updates)


def test_heartbeat_interval(runtime):
    rt, client, _, _, now = runtime
    rt.tick()
    now[0] += 59
    rt.tick()
    assert [data for data, _ in client.updates if 'vivo' in data] == [{'vivo': 1000000}]
    now[0] += 1
    rt.tick()
    assert [data for data, _ in client.updates if 'vivo' in data] == [{'vivo': 1000000}, {'vivo': 1060000}]


def test_connect_publishes_once(runtime):
    rt, client, _, _, _ = runtime
    assert rt.presentation is None
    rt._connect()
    rt._connect()
    assert len(client.shows) == 1
    assert rt.presentation == 'screen-1'
    assert 'data-acao="pc_key"' in client.shows[0][0]


def test_tool_error_on_live_data_republishes_and_retries(runtime):
    rt, client, _, _, _ = runtime
    rt._connect()
    client.fail_updates = 1
    rt.tick()
    assert len(client.shows) == 2
    assert rt.presentation == 'screen-2'
    assert client.updates[-1] == ({'vivo': 1000000}, 'screen-2')


def test_confirmation_never_republishes_the_screen(runtime, touch):
    """Confirming a touch on the wrong presentation is worse than not confirming."""
    rt, client, host, _, _ = runtime
    rt._connect()
    client.fail_updates = 1
    rt.cursor = 41
    rt._handle(touch(), 1000)
    assert host.calls == [('press_key', (175,), {})]
    assert len(client.shows) == 1
    assert rt.presentation == 'screen-1'
    assert all('confirmacao' not in data or presentation == 'event-screen'
               for data, presentation in client.updates if 'confirmacao' in data)


def test_rate_limited_delivery_is_retried(runtime):
    rt, client, _, _, _ = runtime
    rt._connect()
    client.rate_limited = 1
    assert rt._send({'vivo': 1}) is True
    assert client.updates[-1] == ({'vivo': 1}, 'screen-1')


def test_duplicate_event_ids_in_one_batch_run_once(runtime, touch):
    rt, client, host, _, _ = runtime
    rt.cursor = 41
    client.batches.append({'cursor': 42, 'eventos': [touch(), touch()]})
    rt.tick()
    assert host.calls == [('press_key', (175,), {})]


def test_failure_then_recovery_then_next_touch_runs(runtime, touch):
    """Session expired midway: reconnects and next touch executes."""
    rt, client, host, _, _ = runtime
    rt._connect()
    client.read_error = SessionGone('unknown MCP session (404)')
    with pytest.raises(SessionGone):
        rt.tick()
    rt._reconnect()
    rt._connect()
    client.read_error = None
    rt.cursor_store.save(41)
    rt.cursor = 41
    client.batches.append({'cursor': 42, 'eventos': [touch()]})
    rt.tick()
    assert host.calls == [('press_key', (175,), {})]
    assert client.connections == 2


def test_cursor_save_failure_is_retried(runtime, touch, monkeypatch):
    rt, client, _, _, _ = runtime
    rt.cursor = 41
    failures = {'n': 1}
    original = rt.cursor_store.save

    def failing_save(cursor):
        if failures['n']:
            failures['n'] -= 1
            return  # simulate write failure
        original(cursor)

    monkeypatch.setattr(rt.cursor_store, 'save', failing_save)
    client.batches.append({'cursor': 42, 'eventos': []})
    rt.tick()
    assert rt._cursor_dirty is True
    rt.tick()
    assert rt._cursor_dirty is False
    assert rt.cursor_store.load() == 42


def test_gap_still_executes_new_events(runtime, touch):
    rt, client, host, _, _ = runtime
    client.batches.extend([{'cursor': 41},
        {'cursor': 43, 'lacuna': True, 'eventos': [touch(id=41), touch(id=43)]}])
    rt.tick()
    rt.tick()
    assert host.calls == [('press_key', (175,), {})]
    assert rt.cursor_store.load() == 43
