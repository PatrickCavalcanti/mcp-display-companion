from io import BytesIO
import json
import urllib.request
import pytest
from mcp_display_companion.config import WeatherSection, MediaSection
from mcp_display_companion.live import Weather, Media, LiveFeed


@pytest.fixture
def weather_http(monkeypatch):
    calls = []
    def open_fake(request, timeout):
        calls.append((request.full_url, timeout))
        return BytesIO(json.dumps({'current': {'temperature_2m': 27.4, 'weather_code': 3}}).encode())
    monkeypatch.setattr(urllib.request, 'urlopen', open_fake)
    return calls


def test_weather_refresh_window(weather_http):
    weather = Weather(WeatherSection(enabled=True, label='City', refresh_min=2))
    expected = {'temp': 27, 'condicao': 'Nublado', 'cidade': 'City'}
    assert weather.due(0) == expected
    assert weather.due(119) is None
    assert len(weather_http) == 1
    assert weather.due(120) == expected
    assert len(weather_http) == 2


def test_weather_disabled(weather_http):
    assert Weather(WeatherSection()).due(1000, force=True) is None
    assert not weather_http


def test_weather_failure(monkeypatch):
    def fail(*args, **kwargs): raise OSError('offline')
    monkeypatch.setattr(urllib.request, 'urlopen', fail)
    assert Weather(WeatherSection(enabled=True)).due(1000) is None


@pytest.mark.parametrize('field,value', [('titulo', 'Other'), ('artista', 'Other'), ('tocando', False), ('capa', 'cover-B')])
def test_media_changed_fields(host, field, value):
    media = Media(MediaSection(enabled=True), host)
    host.info = {'titulo': 'Song', 'artista': 'Artist', 'tocando': True, 'capa': 'cover-A'}
    first = dict(host.info)
    assert media.due(1000) == first
    assert media.due(1000.5) is None
    assert media.due(1001) is None
    host.info = {**first, field: value}
    assert media.due(1002) == host.info
    assert media.due(1003) is None
    assert media.due(1004, force=True) == host.info
    assert host.calls[0] == ('media_info', (), {'cover': True, 'cover_px': 160})


def test_media_none_and_disabled(host):
    assert Media(MediaSection(enabled=True), host).due(1000) is None
    assert Media(MediaSection(enabled=False), host).due(1000, force=True) is None
    assert len(host.calls) == 1


def test_live_feed_combines_and_omits(weather_http, host):
    host.info = {'titulo': 'Song'}
    feed = LiveFeed(Weather(WeatherSection(enabled=True, label='City')), Media(MediaSection(enabled=True), host))
    first = feed.due(1000)
    assert first == {'clima': {'temp': 27, 'condicao': 'Nublado', 'cidade': 'City'}, 'midia': host.info}
    # without panel acknowledgment pending data continues being offered
    assert feed.due(1001) == first
    feed.ack(first)
    assert feed.due(1001) == {}
    host.info = {'titulo': 'Next'}
    assert feed.due(1002) == {'midia': host.info}
    feed.ack({'midia': host.info})
    assert set(feed.due(1003, force=True)) == {'clima', 'midia'}


def test_live_feed_snapshot_is_the_last_delivered(weather_http, host):
    host.info = {'titulo': 'Song'}
    feed = LiveFeed(Weather(WeatherSection(enabled=True, label='City')), Media(MediaSection(enabled=True), host))
    assert feed.snapshot() == {}
    delivery = feed.due(1000)
    feed.ack(delivery)
    assert feed.snapshot() == delivery
    host.info = {'titulo': 'Next'}
    feed.due(2000)
    # pending data is not delivered state: snapshot does not change until ack
    assert feed.snapshot() == delivery
