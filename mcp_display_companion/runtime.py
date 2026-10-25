"""The service loop: connect, listen for touches, act, confirm, and update.

Core responsibilities:

1. **Never go silent.** Heartbeat logged every `loop.heartbeat_s` and a
   watchdog that terminates the process if the loop hangs (COM/winrt lockup).
2. **Never duplicate a touch.** The cursor continuously advances on disk
   and survives reconnection; stale touches are never executed.
3. **Never republish the screen unnecessarily.** `show_view` reloads the view
   and resets the open page; publication only occurs when no view exists or
   when updating live data fails permanently.
4. **Report accurate status.** Each decision (executed, ignored, foreign)
   is logged; transport failures reconnect rather than failing silently.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from . import events, view
from .actions import ActionRunner
from .client import PanelClient, PanelError, RateLimited, SessionGone, ToolError
from .config import Config
from .host import Host, make_host
from .live import LiveFeed, Media, Weather
from .slideshow import SlideDeck
from .state import StateStore

# without SSE stream, polling needs to be fast so touch responses feel immediate
FAST_POLL_S = 0.7
POLL_RAPIDO_S = FAST_POLL_S
# with SSE stream, polling serves only as a safety net
SLOW_POLL_S = 5.0
POLL_LENTO_S = SLOW_POLL_S
WATCHDOG_LIMIT_S = 120
WATCHDOG_LIMITE_S = WATCHDOG_LIMIT_S


def setup_log(path: Path) -> Callable[[str], None]:
    """Logs to stdout and file -- without console (pythonw), the file is critical."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def log(message: str) -> None:
        line = f"{time.strftime('[%Y-%m-%d %H:%M:%S]')} {message}"
        try:
            print(line, flush=True)
        except Exception:  # noqa: BLE001 - no console under pythonw
            pass
        try:
            with open(path, "a", encoding="utf-8") as file:
                file.write(line + "\n")
        except OSError:
            pass

    return log


class CursorStore:
    """Cursor persistence: service restarts do not re-execute previously consumed touches."""

    def __init__(self, path: Path, log: Callable[[str], None] = lambda m: None) -> None:
        self.path = path
        self.log = log

    def load(self) -> int | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        cursor = data.get("cursor") if isinstance(data, dict) else None
        return cursor if isinstance(cursor, int) else None

    def save(self, cursor: int | None) -> None:
        if cursor is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"cursor": cursor, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S")}),
                encoding="utf-8",
            )
        except OSError as exc:
            self.log(f"could not save cursor to {self.path}: {exc}")


class Runtime:
    # Reserved action names sent by View on rest dimming and wakeup
    REST_ENTERED = "pc_rest_entered"
    REST_LEFT = "pc_rest_left"
    APAGOU = REST_ENTERED
    ACORDOU = REST_LEFT

    def __init__(
        self,
        config: Config,
        log: Callable[[str], None] = lambda m: None,
        *,
        client: PanelClient | None = None,
        host: Host | None = None,
        live: LiveFeed | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.log = log
        self.clock = clock

        self._loop = asyncio.new_event_loop()

        def run_async(coro: Any, timeout_s: float = 10.0) -> Any:
            # Single event loop for process: creating/destroying loops on every
            # 1s media read destabilizes winrt/COM. Timeout prevents hung WinRT calls.
            return self._loop.run_until_complete(asyncio.wait_for(coro, timeout_s))

        self.run_async = run_async
        self.host = host or make_host(run_async=run_async, log=log)
        self.client = client or PanelClient(config.panel, log=log)
        self.state = StateStore(config.state_file, log=log)
        self.runner = ActionRunner(
            config,
            self.host,
            panel=self.client,
            rest=self.set_rest,
            overlay=self.set_photo_clock,
        )
        self.rest = bool(self.state.get("rest", config.rest.enabled))
        self.photo_clock = bool(
            self.state.get("photo_clock", self.state.get("relogio_imagens", config.slides.clock))
        )
        # Screen dimmed by rest mode: brightness is at minimum and must not be
        # overwritten by normal periodic brightness refresh
        self.screen_dark = False
        self.live = live or LiveFeed(
            Weather(config.weather, log=log),
            Media(config.media, self.host, log=log),
        )
        self.slides = SlideDeck(config.slides, log=log)
        self.cursor_store = CursorStore(config.panel.cursor_file, log=log)
        self.cursor: int | None = self.cursor_store.load()
        self._cursor_dirty = False
        self.presentation: str | None = None
        self._wake = threading.Event()
        self._pulse = time.time()
        self._last_heartbeat = 0.0
        self._last_brightness = 0.0
        self._stream_started = False
        self._last_subscription = 0.0



    # ------------------------------------------------------------------ #
    # watchdog
    # ------------------------------------------------------------------ #
    def _watchdog(self) -> None:
        while True:
            time.sleep(10)
            stalled = time.time() - self._pulse
            if stalled > WATCHDOG_LIMIT_S:
                self.log(
                    f"WATCHDOG: loop has stalled for {stalled:.0f}s "
                    "(COM/winrt hang?). Exiting for supervisor restart."
                )
                os._exit(9)

    # ------------------------------------------------------------------ #
    # rest mode (display preference, saved on PC)
    # ------------------------------------------------------------------ #
    def set_rest(self, enabled: bool) -> bool:
        self.rest = bool(enabled)
        self.state.set("rest", self.rest)
        self.log(f"rest mode {'enabled' if self.rest else 'disabled'}")
        return self.rest

    def _rest_brightness(self, dimmed: bool) -> None:
        if not self.config.display.enabled:
            return
        self.screen_dark = dimmed
        target = "min" if dimmed else str(self.config.display.brightness)
        self._set_display(target)
        self.log(f"panel brightness -> {target}" + (" (rest mode)" if dimmed else ""))

    def set_photo_clock(self, enabled: bool) -> bool:
        self.photo_clock = bool(enabled)
        self.state.set("photo_clock", self.photo_clock)
        self.log(f"photo clock {'enabled' if self.photo_clock else 'disabled'}")
        return self.photo_clock

    def _photo_clock_payload(self) -> dict[str, Any]:
        return {"photo_clock": {"enabled": self.photo_clock}}

    def _rest_payload(self) -> dict[str, Any]:
        return {
            "rest": {
                "enabled": self.rest,
                "seconds": self.config.rest.idle_s,
            }
        }

    # ------------------------------------------------------------------ #
    # session and view publishing
    # ------------------------------------------------------------------ #
    def _publish(self) -> None:
        has_slides = (
            getattr(self.slides, "has_images", getattr(self.slides, "tem_imagens", lambda: False))()
            if self.config.slides.enabled
            else False
        )
        html = view.render(self.config, has_slides=has_slides)
        self.presentation = self.client.show_view(html, ttl_min=0, title=self.config.view.title)
        # newly published view is awake: reset dimmed screen flag
        self.screen_dark = False
        self.log(f"display published (presentation {self.presentation})")

    def _connect(self) -> None:
        self.client.connect()
        self.log("MCP session opened")
        if self.presentation is None:
            self._publish()
            self._send(self.live.snapshot(), allow_republish=False)
        # View reloaded: send persisted preferences for switches
        self._send(self._rest_payload(), allow_republish=False)
        self._send(self._photo_clock_payload(), allow_republish=False)
        if self.config.panel.subscribe and not self._stream_started:
            self._stream_started = True
            self._last_subscription = self.clock()
            self.client.subscribe(self._wake.set)
        if self.config.display.enabled:
            self._set_display(str(self.config.display.brightness))

    def _set_display(self, brightness: str | int) -> None:
        try:
            self.client.set_display(brightness)
        except ToolError as exc:
            self.log(f"panel rejected brightness ({exc})")
        except PanelError as exc:
            self.log(f"brightness not applied: {exc}")

    def _send(
        self,
        data: dict[str, Any],
        presentation: str | None = None,
        allow_republish: bool = True,
    ) -> bool:
        """Delivers data payload to View. Returns True if accepted by panel.

        `allow_republish=False` is used for touch confirmations: confirming on
        the wrong view presentation is worse than not confirming.
        """
        if not data:
            return True
        for attempt in range(2):
            try:
                self.client.update_view(data, presentation=presentation or self.presentation)
                return True
            except RateLimited as exc:
                self.log(f"{exc}; waiting 1s to retry")
                if attempt == 0:
                    time.sleep(1)
                    continue
                return False
            except ToolError as exc:
                self.log(f"update_view rejected ({exc})")
                break
            except PanelError as exc:
                self.log(f"update_view failed: {exc}")
                return False
        if not allow_republish:
            return False
        try:
            self._publish()
            self._send(self.live.snapshot(), allow_republish=False)
            self.client.update_view(data, presentation=self.presentation)
            return True
        except PanelError as exc:
            self.log(f"republish failed: {exc}")
            return False

    # ------------------------------------------------------------------ #
    # single loop tick
    # ------------------------------------------------------------------ #
    def tick(self) -> None:
        """One round: heartbeat, live data feeds, events. Public for tests."""
        self._pulse = time.time()
        now = self.clock()

        if now - self._last_heartbeat >= self.config.loop.heartbeat_s:
            self._last_heartbeat = now
            self.log(f"Heartbeat: active (cursor={self.cursor})")
            self._send({"vivo": int(now * 1000)})
            if (
                self.config.display.enabled
                and not self.screen_dark
                and now - self._last_brightness >= self.config.display.refresh_min * 60
            ):
                self._last_brightness = now
                self._set_display(str(self.config.display.brightness))

        live_data = self.live.due(now)
        if live_data and self._send(live_data):
            self.live.ack(live_data)

        check_fn = getattr(self.slides, "check_changed", getattr(self.slides, "verificar", None))
        if check_fn and check_fn(now):
            # images folder added or removed images: slideshow page needs to appear/disappear
            self.log("image list changed; republishing view")
            try:
                self._publish()
            except PanelError as exc:
                self.log(f"could not republish: {exc}")

        slide = self.slides.due(now)
        if slide is not None:
            # one image per interval: 1384x720 in JPEG stays well below 256 KB.
            # When display is dimmed by rest mode, skip sending to save bandwidth and power.
            if not self.screen_dark:
                slide = {**slide, "interval": self.config.slides.interval_s}
                self._send({"slide": slide})

        if self._cursor_dirty and self.cursor is not None:
            self.cursor_store.save(self.cursor)
            self._cursor_dirty = self.cursor_store.load() != self.cursor

        self._read_events(now)

    def _read_events(self, now: float) -> None:
        first = self.cursor is None
        prev_cursor = self.cursor
        batch = self.client.read_events(self.cursor)
        new_cursor = batch.get("cursor")
        if isinstance(new_cursor, int) and new_cursor != self.cursor:
            self.cursor = new_cursor
            self.cursor_store.save(new_cursor)
            self._cursor_dirty = self.cursor_store.load() != new_cursor

        if first:
            self.log(f"initial cursor {self.cursor}; old touches will not be executed")
            return

        if batch.get("lacuna"):
            self.log("event gap detected: touches may have been lost; not replaying old actions")
            self._send(self.live.due(now, force=True))

        seen: set[int] = set()
        for event in batch.get("eventos") or []:
            event_id = event.get("id") if isinstance(event, dict) else None
            if isinstance(event_id, int):
                if event_id in seen:
                    continue  # server repeated event in same batch
                seen.add(event_id)
                if prev_cursor is not None and event_id <= prev_cursor:
                    continue  # already consumed
            self._handle(event, now)

    def _handle(self, event: Any, now: float) -> None:
        if isinstance(event, dict) and event.get("tipo") == "toque":
            data = event.get("dados")
            tool = str((data or {}).get("tool") or "")
            if tool in {self.REST_ENTERED, self.REST_LEFT}:
                self._rest_brightness(tool == self.REST_ENTERED)
                return

        decision = events.classify(
            event,
            is_known=self.runner.knows,
            now=now,
            max_age_s=self.config.loop.max_event_age_s,
        )
        if decision.kind == events.RUN:
            result = self.runner.run(decision.tool)
            ok = bool(result and result.ok)
            detail = result.detail if result else ""
            self.log(
                f"touch: {decision.tool} (ev #{decision.event_id}) -> "
                f"{'ok' if ok else 'failed'}{(' - ' + detail) if detail else ''}"
            )
            # confirmation and media refresh sent in same update_view call
            delivery: dict[str, Any] = {}
            if self.config.loop.confirm:
                delivery["confirmacao"] = {"acao": decision.tool, "ok": ok}
            if "midia" in decision.tool:
                track = self.live.refresh_media()
                if track:
                    delivery["midia"] = track
            action = self.runner.actions.get(decision.tool)
            if action is not None and action.type == "rest":
                delivery.update(self._rest_payload())
            if action is not None and action.type == "overlay":
                delivery.update(self._photo_clock_payload())
            if delivery:
                sent = self._send(
                    delivery,
                    presentation=decision.presentation or None,
                    allow_republish=False,
                )
                if sent and "midia" in delivery:
                    self.live.ack({"midia": delivery["midia"]})
            return

        if decision.kind == events.OLD:
            self.log(f"touch ignored (ev #{decision.event_id}): {decision.reason}")
        elif decision.kind == events.FOREIGN:
            self.log(f"touch from other screen ignored (ev #{decision.event_id}): {decision.reason}"
                     + (f" [{decision.tool}]" if decision.tool else ""))
        elif decision.kind == events.OTHER:
            self.log(f"panel event: {decision.reason}")
        else:
            self.log(f"malformed event discarded: {decision.reason}")

    # ------------------------------------------------------------------ #
    # main loop
    # ------------------------------------------------------------------ #
    def run(self) -> None:
        self.log("=========== service started ===========")
        threading.Thread(target=self._watchdog, daemon=True, name="watchdog").start()

        while True:
            try:
                self._connect()
                self.log("listening for panel touches")
                while True:
                    self.tick()
                    self._renew_stream()
                    wait_time = SLOW_POLL_S if self.client.stream_ok.is_set() else self._poll_wait()
                    if self._wake.wait(timeout=wait_time):
                        self._wake.clear()
            except SessionGone as exc:
                self.log(f"{exc}; reconnecting")
                self._reconnect()
            except RateLimited as exc:
                self.log(f"{exc}; waiting 2s")
                time.sleep(2)
            except PanelError as exc:
                self.log(f"transport failed ({exc}); reconnecting in 5s")
                time.sleep(5)
                self._reconnect()
            except Exception:  # noqa: BLE001 - loop must survive transient exceptions
                self.log(f"unexpected error in loop:\n{traceback.format_exc()}")
                self._reconnect()

    def _poll_wait(self) -> float:
        """Without SSE, polling uses `panel.poll_ms` (default 0.7s)."""
        return max(self.config.panel.poll_ms / 1000.0, 0.05)

    def _espera_polling(self) -> float:
        return self._poll_wait()

    def _renew_stream(self) -> None:
        """If subscription disconnected, try reconnecting without full session restart."""
        if not self.config.panel.subscribe or self.client.stream_ok.is_set():
            return
        now = self.clock()
        if now - self._last_subscription < 30:
            return
        self._last_subscription = now
        self.log("event stream disconnected; resubscribing (polling covers in the meantime)")
        self.client.subscribe(self._wake.set)

    def _renovar_stream(self) -> None:
        self._renew_stream()

    def _reconnect(self) -> None:
        self.client.session_id = None
        self._stream_started = False
        self._last_subscription = 0.0
        try:
            self.client.close_stream()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)

    def _reconectar(self) -> None:
        self._reconnect()
