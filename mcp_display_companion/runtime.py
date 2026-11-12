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
import hmac
import json
import os
import re
import secrets
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
from .strings import t

# without SSE stream, polling needs to be fast so touch responses feel immediate
FAST_POLL_S = 0.7
POLL_RAPIDO_S = FAST_POLL_S
# with SSE stream, polling serves only as a safety net
SLOW_POLL_S = 5.0
HOME_REFRESH_LOG_S = 600.0  # mute repeated "home assistant unreachable (refresh)" lines
POLL_LENTO_S = SLOW_POLL_S
WATCHDOG_LIMIT_S = 120
WATCHDOG_LIMITE_S = WATCHDOG_LIMIT_S

# Republishing reloads the View and sends the screen back to the first page, so
# a rejected `update_view` is retried with backoff, and only a few times: when
# another companion/agent owns the panel, fighting for it is an endless loop.
REPUBLISH_BACKOFF_S = (2.0, 5.0, 15.0)
REPUBLISH_MAX_ATTEMPTS = 2
REPUBLISH_GIVE_UP_S = 300.0
OTHER_OWNER_LOG = (
    "outro agente/serviço está usando o painel; parando de republicar "
    f"por {REPUBLISH_GIVE_UP_S:.0f}s"
)


ALERT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
ALERT_MAX_SHOWN = 10
ALERT_ACTIONABLE_MAX_AGE_S = 300  # a request nobody answered in 5 min is gone (the hook waits 290 s)
ALERT_SUMMARY_MAX = 1200
ALERT_TOOL_MAX = 140
ALERT_ORPHAN_ANSWER_S = 2.0
ALERT_TMP_MAX_AGE_S = 60.0
ALERT_RETRY_S = 30.0
ALERT_ALIVE_FILE = ".service-alive"  # the hook reads it; see scripts/claude_alert_hook.py


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
    # Reserved name sent by the View when the banner of a Claude Code permission
    # alert is tapped (arguments: {id}, or nothing for "all")
    ALERT_DISMISS = "pc_alerta_dispensar"
    # Reserved name: approve/deny a permission request (arguments: {id, decisao})
    ALERT_ANSWER = "pc_alerta_responder"

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
        self._home_warned = False
        self._home_refresh_logged = float("-inf")

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
            Weather(config.weather, log=log, language=config.view.language),
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
        # republish control: consecutive rejected republishes and the earliest
        # time the next one is allowed (see REPUBLISH_* above)
        self._republish_attempts = 0
        self._republish_at = 0.0
        # Claude Code permission alerts (files dropped by scripts/claude_alert_hook.py)
        self._alerts_cache: dict[str, tuple[tuple[int, int], dict[str, Any] | None]] = {}
        self._alerts_active: list[dict[str, Any]] = []
        self._alerts_sent: tuple | None = ()  # what the View currently shows
        self._alerts_seen: set[tuple[str, float]] = set()
        # secret per actionable alert: lives only here and in the View that gets the
        # list; the touch that answers must echo it
        self._alerts_nonce: dict[str, str] = {}
        self._alerts_fail_sig: tuple | None = None  # signature the View rejected
        self._alerts_retry_at = 0.0
        self._alive_failed = False



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
                try:
                    self.runner.release_all_held_keys()
                finally:
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
        target = "min" if dimmed else str(self._normal_brightness())
        self._set_display(target)
        self.log(f"panel brightness -> {target}" + (" (rest mode)" if dimmed else ""))

    def _normal_brightness(self) -> str | int:
        """Brightness of an awake screen: the alert one while an alert is showing."""
        alerts = self.config.alerts
        if alerts.enabled and alerts.wake and self._alerts_active:
            return alerts.brightness
        return self.config.display.brightness

    # ------------------------------------------------------------------ #
    # Claude Code permission alerts
    # ------------------------------------------------------------------ #
    def _read_alert(self, entry_path: str, stem: str, mtime_ns: int) -> dict[str, Any] | None:
        try:
            with open(entry_path, encoding="utf-8") as file:
                raw = json.load(file)
        except (OSError, ValueError):
            return None  # unreadable or half written: skipped until it changes
        if not isinstance(raw, dict):
            return None
        at = raw.get("at")
        if isinstance(at, bool) or not isinstance(at, (int, float)):
            at = mtime_ns / 1e9
        alert = {
            "id": stem,
            "project": str(raw.get("project") or "")[:80],
            "message": str(raw.get("message") or "")[:300],
            "at": round(float(at), 3),
        }
        request = raw.get("request")
        if raw.get("actionable") is True and isinstance(request, dict):
            summary = str(request.get("summary") or "")
            tool = str(request.get("tool") or "")
            # fail closed: only an explicit `false` from the hook lets the panel approve
            cut = (request.get("truncated") is not False
                   or len(summary) > ALERT_SUMMARY_MAX or len(tool) > ALERT_TOOL_MAX)
            if tool:
                # the hook writes the message in English; the panel language wins
                alert["message"] = t(self.config.view.language, "alert_wants_tool", tool=tool)[:300]
            alert["actionable"] = True
            alert["request"] = {
                "tool": tool[:ALERT_TOOL_MAX],
                "summary": summary[:ALERT_SUMMARY_MAX],
                "truncated": cut,
            }
        return alert

    def _scan_alerts(self, now: float) -> None:
        cfg = self.config.alerts
        if not cfg.enabled:
            return
        found: dict[str, tuple[tuple[int, int], dict[str, Any] | None]] = {}
        answers: list[tuple[str, str, float]] = []
        tmps: list[tuple[str, float]] = []
        try:
            with os.scandir(cfg.dir) as it:
                entries = list(it)
        except OSError:
            entries = []  # missing dir: no alerts
        wall = time.time()
        for entry in entries:
            name = entry.name
            try:
                stat = entry.stat()
            except OSError:
                continue
            if name.startswith(".") and name.endswith(".tmp"):
                tmps.append((entry.path, stat.st_mtime))
                continue
            if name.endswith(".answer") and ALERT_ID_RE.match(name[:-7]):
                answers.append((name[:-7], entry.path, stat.st_mtime))
                continue
            if not name.endswith(".json") or not ALERT_ID_RE.match(name[:-5]):
                continue
            stem = name[:-5]
            sig = (stat.st_mtime_ns, stat.st_size)
            cached = self._alerts_cache.get(stem)
            if cached is None or cached[0] != sig:
                cached = (sig, self._read_alert(entry.path, stem, stat.st_mtime_ns))
            found[stem] = cached
        self._alerts_cache = found
        self._sweep_leftovers(found, answers, tmps, wall)

        had_alerts = bool(self._alerts_active)
        active: list[dict[str, Any]] = []
        for stem, (_, alert) in list(found.items()):
            if alert is None:
                continue
            limit = cfg.max_age_s
            if alert.get("actionable"):
                limit = min(limit, ALERT_ACTIONABLE_MAX_AGE_S)
            if now - min(alert["at"], now) > limit:  # a future `at` (clock skew) counts as fresh
                self._delete_alert(stem, "stale")
                found.pop(stem, None)
                continue
            active.append(alert)
        active.sort(key=lambda a: (a["at"], a["id"]))
        active = active[-ALERT_MAX_SHOWN:]
        self._alerts_active = active
        for alert in active:
            if alert.get("actionable") and alert["id"] not in self._alerts_nonce:
                self._alerts_nonce[alert["id"]] = secrets.token_urlsafe(16)
        for stem in [k for k in self._alerts_nonce if k not in found]:
            del self._alerts_nonce[stem]

        signature = tuple((a["id"], a["at"]) for a in active)
        new = [key for key in signature if key not in self._alerts_seen]
        self._alerts_seen = set(signature)
        if signature != self._alerts_sent:
            self._push_alerts(signature, now)
        if had_alerts and not active and cfg.wake and not self.screen_dark and self.config.display.enabled:
            # the alert brightness is over: back to the normal one
            self._set_display(str(self._normal_brightness()))
            self._last_brightness = self.clock()
        if new:
            self.log("claude alert: " + ", ".join(
                f"{a['project'] or '?'} ({a['id'][:8]})" for a in active if (a["id"], a["at"]) in new))
            if cfg.wake:
                self._wake_screen()

    def _renew_alive(self) -> None:
        """Tell the hook the panel is reachable: `<alerts>/.service-alive`, atomic.

        Called at the end of a tick that read the panel's events, never while the
        service reconnects, so the hook does not wait for a banner that cannot show.
        """
        cfg = self.config.alerts
        if not cfg.enabled:
            return
        folder = Path(cfg.dir)
        tmp = folder / f"{ALERT_ALIVE_FILE}.{os.getpid()}.tmp"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({"at": time.time(), "pid": os.getpid()}), encoding="utf-8")
            os.replace(tmp, folder / ALERT_ALIVE_FILE)
        except OSError as exc:
            if not self._alive_failed:
                self._alive_failed = True
                self.log(f"could not write the service sign of life: {exc}")
            self._remove_quietly(str(tmp))
        else:
            self._alive_failed = False

    def _clear_alive(self) -> None:
        if self.config.alerts.enabled:
            self._remove_quietly(str(Path(self.config.alerts.dir) / ALERT_ALIVE_FILE))

    def _warn_alerts_dir(self) -> None:
        cfg = self.config.alerts
        if cfg.enabled and cfg.dir_from_config:
            self.log(
                f"WARNING: alerts.dir is set ({cfg.dir}); the hook only reads the env var "
                "PANEL_COMPANION_ALERTS, so set the same path there"
            )

    def _push_alerts(self, signature: tuple, now: float) -> None:
        """Send the list to the View. Never republishes; a rejection is retried slowly."""
        if signature == self._alerts_fail_sig and now < self._alerts_retry_at:
            return
        first_failure = signature != self._alerts_fail_sig
        if self._send(self._alerts_payload(), allow_republish=False, log_failures=first_failure):
            self._alerts_sent = signature
            self._alerts_fail_sig = None
            return
        self._alerts_fail_sig = signature
        self._alerts_retry_at = now + ALERT_RETRY_S
        if first_failure:
            self.log(f"alert list not delivered to the View; trying again every {ALERT_RETRY_S:.0f}s")

    def _sweep_leftovers(
        self,
        found: dict[str, Any],
        answers: list[tuple[str, str, float]],
        tmps: list[tuple[str, float]],
        wall: float,
    ) -> None:
        """Remove `.answer` files with no request and old `.tmp` files left by a crash."""
        for stem, path, mtime in answers:
            if stem not in found and wall - mtime > ALERT_ORPHAN_ANSWER_S:
                self._remove_quietly(path)
        for path, mtime in tmps:
            if wall - mtime > ALERT_TMP_MAX_AGE_S:
                self._remove_quietly(path)

    def _remove_quietly(self, path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    def _alerts_payload(self) -> dict[str, Any]:
        return {
            "alertas": [
                {**a, "nonce": self._alerts_nonce[a["id"]]}
                if a.get("actionable") and a["id"] in self._alerts_nonce else dict(a)
                for a in self._alerts_active
            ],
            "allow_approve": bool(self.config.alerts.allow_approve),
        }

    def _wake_screen(self) -> None:
        # the View leaves its own rest mode when it receives the alert list
        self.screen_dark = False
        if self.config.display.enabled:
            self._set_display(str(self._normal_brightness()))
            self._last_brightness = self.clock()

    def _delete_alert(self, stem: str, why: str) -> bool:
        if not ALERT_ID_RE.match(stem):
            return False
        try:
            os.remove(Path(self.config.alerts.dir) / f"{stem}.json")
        except FileNotFoundError:
            return False
        except OSError as exc:
            self.log(f"could not delete alert {stem[:8]} ({why}): {exc}")
            return False
        return True

    def _answer_alert(self, data: dict[str, Any], now: float) -> None:
        """Write `<id>.answer` for an actionable, fresh alert. Nothing else, ever."""
        cfg = self.config.alerts
        args = data.get("arguments", data.get("argumentos", data.get("args")))
        args = args if isinstance(args, dict) else {}
        raw_id, decision = args.get("id"), args.get("decisao")
        if not (isinstance(raw_id, str) and ALERT_ID_RE.match(raw_id)):
            self.log("alert answer ignored: invalid id")
            return
        if decision not in ("allow", "deny"):
            self.log("alert answer ignored: invalid decision")
            return
        if decision == "allow" and not cfg.allow_approve:
            self.log("alert answer ignored: approving from the panel is disabled")
            return
        self._scan_alerts(now)  # a fresh view of the folder, so stale files are dropped
        alert = next((a for a in self._alerts_active if a["id"] == raw_id), None)
        if alert is None or not alert.get("actionable"):
            self.log(f"alert answer ignored: {raw_id[:8]} is not an actionable alert")
            return
        expected = self._alerts_nonce.get(raw_id)
        nonce = args.get("nonce")
        if not (expected and isinstance(nonce, str) and nonce
                and hmac.compare_digest(nonce.encode("utf-8"), expected.encode("utf-8"))):
            self.log(f"alert answer ignored: missing or wrong nonce for {raw_id[:8]}")
            return
        folder = Path(cfg.dir)
        tmp = folder / f".{raw_id}.answer.tmp"
        try:
            tmp.write_text(decision, encoding="utf-8")
            os.replace(tmp, folder / f"{raw_id}.answer")
        except OSError as exc:
            self.log(f"could not write the answer for {raw_id[:8]}: {exc}")
            return
        request = alert.get("request") or {}
        self.log(f"permission {decision} from the panel: {alert['project'] or '?'} {request.get('tool', '')}")

    def _dismiss_alerts(self, data: dict[str, Any], now: float) -> None:
        args = data.get("arguments", data.get("argumentos", data.get("args")))
        args = args if isinstance(args, dict) else {}
        raw_id = args.get("id")
        if not (isinstance(raw_id, str) and ALERT_ID_RE.match(raw_id)):
            self.log("alert dismiss ignored: an alert id is required")
            return
        if raw_id not in self._alerts_cache:
            self.log(f"alert dismiss ignored: {raw_id[:8]} is not a known alert")
            return
        removed = int(self._delete_alert(raw_id, "dismissed"))
        self.log(f"alert dismissed from the panel: {raw_id[:8]} ({removed} file(s))")
        self._scan_alerts(now)

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
        home = self.config.home
        home_on = view.home_supported(self.config)
        if home.enabled and not home_on and not self._home_warned:
            self._home_warned = True
            self.log("template has no {{PAGINA_CASA}}; home page disabled")
        self.presentation = self.client.show_view(
            html,
            ttl_min=0,
            title=self.config.view.title,
            connection=home.connection if home_on else None,
        )
        # newly published view is awake: reset dimmed screen flag
        self.screen_dark = False
        self._alerts_sent = ()  # a fresh View has no banner
        self._alerts_fail_sig = None
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
        toggles = self.runner.hotkey_states()
        if toggles:
            self._send({"toggles": toggles}, allow_republish=False)
        self._alerts_sent = None  # the View may have reloaded: send the list again
        self._alerts_fail_sig = None
        if self.config.panel.subscribe and not self._stream_started:
            self._stream_started = True
            self._last_subscription = self.clock()
            self.client.subscribe(self._wake.set)
        if self.config.display.enabled:
            self._set_display(str(self._normal_brightness()))

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
        log_failures: bool = True,
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
                self._republish_attempts = 0
                return True
            except RateLimited as exc:
                self.log(f"{exc}; waiting 1s to retry")
                if attempt == 0:
                    time.sleep(1)
                    continue
                return False
            except ToolError as exc:
                if log_failures:
                    self.log(f"update_view rejected ({exc})")
                break
            except PanelError as exc:
                if log_failures:
                    self.log(f"update_view failed: {exc}")
                return False
        if not allow_republish:
            return False
        if not self._may_republish():
            return False
        try:
            self._publish()
            self._send(self.live.snapshot(), allow_republish=False)
            self.client.update_view(data, presentation=self.presentation)
            self._republish_attempts = 0
            return True
        except ToolError as exc:
            self._republish_attempts += 1
            index = min(self._republish_attempts, len(REPUBLISH_BACKOFF_S)) - 1
            backoff = REPUBLISH_BACKOFF_S[index]
            self._republish_at = self.clock() + backoff
            self.log(
                f"update_view rejected again ({exc}); not republishing for {backoff:.0f}s"
            )
            return False
        except PanelError as exc:
            self.log(f"republish failed: {exc}")
            return False

    def _may_republish(self) -> bool:
        """Backoff and give-up rule for republishing a rejected View."""
        if self.clock() < self._republish_at:
            return False
        if self._republish_attempts >= REPUBLISH_MAX_ATTEMPTS:
            # another companion or agent keeps taking the view: the screen would
            # jump back to the first page every few seconds, so stop trying
            self._republish_attempts = 0
            self._republish_at = self.clock() + REPUBLISH_GIVE_UP_S
            self.log(OTHER_OWNER_LOG)
            return False
        return True

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
                self._set_display(str(self._normal_brightness()))

        self._scan_alerts(now)

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
        # last on purpose: reaching here means the panel answered this round
        self._renew_alive()

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
            if tool in {self.ALERT_DISMISS, self.ALERT_ANSWER}:
                ts = event.get("em")
                max_age = self.config.loop.max_event_age_s
                if isinstance(ts, (int, float)) and max_age > 0 and now - ts / 1000.0 > max_age:
                    self.log("alert touch ignored: old touch")
                    return
                # only the View this service published may answer: a touch from any
                # other presentation (another agent's View) is not ours
                shown = str(event.get("apresentacao") or "")
                if not self.presentation or shown != self.presentation:
                    self.log(f"alert touch ignored: from another presentation ({shown[:12] or 'none'})")
                    return
                payload = data if isinstance(data, dict) else {}
                if tool == self.ALERT_ANSWER:
                    self._answer_alert(payload, now)
                else:
                    self._dismiss_alerts(payload, now)
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
            if action is not None and action.type == "hotkey_toggle":
                delivery["toggles"] = {action.id: self.runner.is_hotkey_active(action.id)}
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
        elif decision.kind == events.FOREIGN and decision.tool in events.HOME_TOOLS:
            # the mixed screen could not reach Home Assistant: nothing to run here
            if decision.tool == "homeassistant__GetLiveContext":
                now = self.clock()
                if now - self._home_refresh_logged < HOME_REFRESH_LOG_S:
                    return
                self._home_refresh_logged = now
                self.log("home assistant unreachable (refresh); further refresh failures muted for 10 min")
            else:
                self.log(f"home assistant unreachable: {decision.tool} (ev #{decision.event_id})")
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
        self._warn_alerts_dir()
        self.runner.release_configured_hotkeys()  # a killed run may have left keys held
        threading.Thread(target=self._watchdog, daemon=True, name="watchdog").start()

        try:
            while True:
                # A reconnect attempt is the loop working, not a hang: with the
                # phone offline, tick() never runs, and without this pulse the
                # watchdog would kill the service every WATCHDOG_LIMIT_S.
                self._pulse = time.time()
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
        finally:
            # service stopping (normal exit or exception): never leave keys held down
            self.runner.release_all_held_keys()
            self._clear_alive()

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
        self._clear_alive()  # no panel to show a banner: the hook must not wait for one
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
