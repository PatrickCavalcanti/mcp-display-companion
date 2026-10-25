"""Live background data shown on the display: weather and current media track.

Each source has its own cadence (`refresh_min` for weather, `interval_s` for
media) and only issues `update_view` when values actually change -- the panel
limits 10 calls per 10s, and re-sending unchanged base64 artwork wastes bandwidth.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Callable

from .config import MediaSection, WeatherSection
from .host import Host

# open-meteo returns WMO weather codes; the display shows human-readable strings in Portuguese
CONDITIONS = {
    0: "Céu limpo",
    1: "Parcialmente nublado",
    2: "Parcialmente nublado",
    3: "Nublado",
    45: "Nevoeiro",
    48: "Nevoeiro",
    51: "Chuva fraca",
    53: "Chuva",
    55: "Chuva",
    61: "Chuva",
    63: "Chuva",
    65: "Chuva forte",
    80: "Pancadas",
    81: "Pancadas",
    82: "Pancadas fortes",
    95: "Tempestade",
    96: "Tempestade",
    99: "Tempestade",
}


class Weather:
    """Current weather by coordinates (open-meteo, no API key required)."""

    def __init__(self, section: WeatherSection, log: Callable[[str], None] = lambda m: None) -> None:
        self.section = section
        self.log = log
        self._last: float | None = None
        self._value: dict[str, Any] | None = None

    @property
    def _ultimo(self) -> float | None:
        return self._last

    @_ultimo.setter
    def _ultimo(self, val: float | None) -> None:
        self._last = val

    @property
    def _valor(self) -> dict[str, Any] | None:
        return self._value

    @_valor.setter
    def _valor(self, val: dict[str, Any] | None) -> None:
        self._value = val

    def due(self, now: float, force: bool = False) -> dict[str, Any] | None:
        if not self.section.enabled:
            return None
        if not force and self._last is not None and now - self._last < self.section.refresh_min * 60:
            return None
        self._last = now
        self._value = self._fetch()
        return self._value

    def _fetch(self) -> dict[str, Any] | None:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.section.latitude}&longitude={self.section.longitude}"
            "&current=temperature_2m,weather_code&timezone=auto"
        )
        request = urllib.request.Request(url, headers={"User-Agent": "mcp-display-companion/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=self.section.timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - weather is decorative: failure does not crash
            self.log(f"weather unavailable: {exc}")
            return None
        current = data.get("current") or {}
        temperature = current.get("temperature_2m")
        code = int(current.get("weather_code") or 0)
        return {
            "temp": round(temperature) if isinstance(temperature, (int, float)) else None,
            "condicao": CONDITIONS.get(code, "-"),
            "cidade": self.section.label,
        }

    def _buscar(self) -> dict[str, Any] | None:
        return self._fetch()


class Media:
    """Current system media track (Windows: GSMTC)."""

    def __init__(self, section: MediaSection, host: Host,
                 log: Callable[[str], None] = lambda m: None) -> None:
        self.section = section
        self.host = host
        self.log = log
        self._last = 0.0
        self._key: tuple[Any, ...] | None = None
        self._value: dict[str, Any] | None = None

    @property
    def _ultimo(self) -> float:
        return self._last

    @_ultimo.setter
    def _ultimo(self, val: float) -> None:
        self._last = val

    @property
    def _chave(self) -> tuple[Any, ...] | None:
        return self._key

    @_chave.setter
    def _chave(self, val: tuple[Any, ...] | None) -> None:
        self._key = val

    @property
    def _valor(self) -> dict[str, Any] | None:
        return self._value

    @_valor.setter
    def _valor(self, val: dict[str, Any] | None) -> None:
        self._value = val

    def due(self, now: float, force: bool = False) -> dict[str, Any] | None:
        if not self.section.enabled:
            return None
        if not force and now - self._last < self.section.interval_s:
            return None
        self._last = now
        info = self.host.media_info(cover=self.section.cover, cover_px=self.section.cover_px)
        if info is None:
            return None  # no response: maintain current screen state
        key = (
            info.get("tocando"),
            info.get("titulo"),
            info.get("artista"),
            info.get("capa"),
        )
        if key == self._key and not force:
            return None
        self._key = key
        self._value = info
        return info


class LiveFeed:
    """Combines live sources and returns a unified payload dict for `update_view`.

    Delivered data (`ack`) is stored as the last known state: if delivery
    fails (429, panel temporarily down), `due` continues returning pending data
    on next loop pass. After re-rendering the view, `snapshot()` restores full
    state so the refreshed view is not blank.
    """

    def __init__(self, weather: Weather, media: Media) -> None:
        self.weather = weather
        self.media = media
        self._pending: dict[str, Any] = {}
        self._delivered: dict[str, Any] = {}

    @property
    def _pendente(self) -> dict[str, Any]:
        return self._pending

    @_pendente.setter
    def _pendente(self, val: dict[str, Any]) -> None:
        self._pending = val

    @property
    def _entregue(self) -> dict[str, Any]:
        return self._delivered

    @_entregue.setter
    def _entregue(self, val: dict[str, Any]) -> None:
        self._delivered = val

    def due(self, now: float, force: bool = False) -> dict[str, Any]:
        new_data: dict[str, Any] = {}
        weather_data = self.weather.due(now, force=force)
        if weather_data is not None:
            new_data["clima"] = weather_data
        track_data = self.media.due(now, force=force)
        if track_data is not None:
            new_data["midia"] = track_data
        self._pending.update(new_data)
        return dict(self._pending)

    def ack(self, data: dict[str, Any]) -> None:
        """View received the update: pending transitions to last known delivered state."""
        for key, value in data.items():
            self._delivered[key] = value
            self._pending.pop(key, None)

    def snapshot(self) -> dict[str, Any]:
        """Last state confirmed by the view (for re-publishing complete state)."""
        return dict(self._delivered)

    def refresh_media(self) -> dict[str, Any] | None:
        """Forced re-read after a media touch action (without waiting for interval)."""
        track = self.media.due(time.time(), force=True)
        if track is not None:
            self._pending["midia"] = track
        return track
