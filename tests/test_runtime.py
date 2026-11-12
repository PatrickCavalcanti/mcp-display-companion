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


# --------------------------------------------------------------------------- #
# another companion owns the panel: republish with backoff, then stop
# --------------------------------------------------------------------------- #
def test_rejected_republish_backs_off_then_gives_up(runtime):
    from mcp_display_companion.runtime import REPUBLISH_MAX_ATTEMPTS

    rt, client, _, _, now = runtime
    rt._connect()
    messages = []
    rt.log = messages.append
    client.fail_updates = 10 ** 6
    screens = len(client.shows)

    for _ in range(6):
        assert rt._send({'vivo': 1}) is False
        now[0] += 6  # long enough for every backoff step

    # it tried a couple of times and stopped instead of looping every tick
    assert len(client.shows) - screens == REPUBLISH_MAX_ATTEMPTS
    assert any('outro agente/serviço está usando o painel' in m for m in messages)

    # after the give-up window it may try again -- once, not on every tick
    now[0] += 301
    screens = len(client.shows)
    assert rt._send({'vivo': 2}) is False
    assert len(client.shows) - screens == 1
    assert rt._send({'vivo': 3}) is False
    assert len(client.shows) - screens == 1


def test_republish_waits_for_the_backoff(runtime):
    rt, client, _, _, now = runtime
    rt._connect()
    client.fail_updates = 10 ** 6
    screens = len(client.shows)

    assert rt._send({'vivo': 2}) is False  # rejected, one republish attempt fails
    assert len(client.shows) == screens + 1
    assert rt._send({'vivo': 3}) is False  # inside the backoff window
    assert len(client.shows) == screens + 1
    now[0] += 2
    assert rt._send({'vivo': 4}) is False
    assert len(client.shows) == screens + 2


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


class _StopLoop(BaseException):
    """Escapes Runtime.run(), whose loop swallows every Exception."""


def test_reconnect_attempts_keep_the_watchdog_fed(runtime, monkeypatch):
    # With the phone offline, each reconnect attempt is the loop working, not
    # a COM/winrt hang: the watchdog must not kill the service every 120s.
    from mcp_display_companion.client import PanelError
    instance, client, *_ = runtime
    clock = [5000.0]
    monkeypatch.setattr('mcp_display_companion.runtime.time.time', lambda: clock[0])
    monkeypatch.setattr('mcp_display_companion.runtime.time.sleep',
                        lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr('mcp_display_companion.runtime.threading.Thread',
                        lambda *a, **k: type('T', (), {'start': lambda self: None})())
    instance._pulse = clock[0]
    stalls = []

    def connect():
        client.connections += 1
        stalls.append(clock[0] - instance._pulse)
        if client.connections >= 40:  # ~40 failed attempts, well past 120s
            raise _StopLoop
        clock[0] += 8  # each attempt times out after a few seconds
        raise PanelError('panel unreachable (timed out)')

    client.connect = connect
    with pytest.raises(_StopLoop):
        instance.run()
    assert clock[0] - 5000.0 > 120
    assert max(stalls) < 120


def test_publish_passes_connection_only_when_home_enabled(make_config, host, runtime):
    from dataclasses import replace
    from mcp_display_companion.config import HomeSection
    rt, client, _, _, _ = runtime
    rt._publish()
    assert client.shows[-1][1]['connection'] is None
    rt.config = replace(rt.config, home=HomeSection(enabled=True, connection='ha'))
    rt._publish()
    assert client.shows[-1][1]['connection'] == 'ha'


def test_home_tool_touch_is_logged_not_executed(runtime, touch):
    rt, client, host, _, now = runtime
    logs = []
    rt.log = logs.append
    ev = touch(dados={'tool': 'intent__HassTurnOn', 'destino_indisponivel': True})
    rt._handle(ev, now[0])
    assert host.calls == [] and client.updates == []
    assert any('home assistant unreachable: intent__HassTurnOn' in m for m in logs)
    assert not any('malformed' in m for m in logs)


def test_publish_without_home_marker_skips_connection_and_warns_once(make_config, host, runtime, tmp_path):
    from dataclasses import replace
    from mcp_display_companion.config import HomeSection, ViewSection
    rt, client, _, _, _ = runtime
    tpl = tmp_path / 'old.html'
    tpl.write_text('<div id="trilho">{{PAGINA_ATALHOS}}</div>{{PONTOS}}', encoding='utf-8')
    logs = []
    rt.log = logs.append
    rt.config = replace(rt.config, home=HomeSection(enabled=True, connection='ha'),
                        view=ViewSection(template=tpl))
    rt._publish(); rt._publish()
    assert client.shows[-1][1]['connection'] is None
    assert sum('template has no {{PAGINA_CASA}}; home page disabled' in m for m in logs) == 1


def test_home_refresh_failures_are_muted_for_ten_minutes(runtime, touch):
    rt, client, host, _, now = runtime
    logs = []
    rt.log = logs.append
    clock = [10_000.0]
    rt.clock = lambda: clock[0]
    def ev(i, tool):
        return touch(id=i, dados={'tool': tool, 'destino_indisponivel': True})
    def refresh_logs():
        return sum('unreachable (refresh)' in m for m in logs)
    rt._handle(ev(50, 'homeassistant__GetLiveContext'), now[0])
    clock[0] += 60
    rt._handle(ev(51, 'homeassistant__GetLiveContext'), now[0])
    assert refresh_logs() == 1
    rt._handle(ev(52, 'intent__HassTurnOn'), now[0])
    rt._handle(ev(53, 'intent__HassTurnOn'), now[0])
    assert sum('unreachable: intent__HassTurnOn' in m for m in logs) == 2
    clock[0] += 601
    rt._handle(ev(54, 'homeassistant__GetLiveContext'), now[0])
    assert refresh_logs() == 2
