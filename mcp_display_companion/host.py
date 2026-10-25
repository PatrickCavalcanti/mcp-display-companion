"""What "acting on the computer" means on each operating system.

Everything that touches the operating system lives here behind a small interface
(`Host`), and the rest of the service does not know the difference:

- actions (`actions.py`) and loop (`runtime.py`) can be tested on any machine,
  without winrt or pywin32 (which is what runs in CI);
- a new platform (macOS, Linux, Wayland) is added by implementing a `Host`,
  without touching service logic.

On Windows media control uses the native API (GSMTC), which does not depend on
which window has focus; without it (or if it fails), it falls back to media keys.
"""

from __future__ import annotations

import base64
import io
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

VK_MEDIA_NEXT = 0xB0
VK_MEDIA_PREV = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF

MEDIA_KEYS = {
    "play_pause": VK_MEDIA_PLAY_PAUSE,
    "next": VK_MEDIA_NEXT,
    "previous": VK_MEDIA_PREV,
}
VOLUME_KEYS = {"up": VK_VOLUME_UP, "down": VK_VOLUME_DOWN, "mute": VK_VOLUME_MUTE}

NavigateFn = Callable[[Path], str]


class Host:
    """Base implementation: standard library only. Baseline for all systems."""

    name = "generic"

    def __init__(self, log: Callable[[str], None] = lambda msg: None) -> None:
        self.log = log

    # -- input --------------------------------------------------------------
    def press_key(self, vk: int) -> bool:
        self.log(f"press_key({vk}) not implemented on {self.name}")
        return False

    def media(self, command: str) -> bool:
        """play_pause / next / previous."""
        return self.press_key(MEDIA_KEYS[command])

    def volume(self, direction: str) -> bool:
        return self.press_key(VOLUME_KEYS[direction])

    # -- opening targets ----------------------------------------------------
    def open_folder(self, path: Path) -> bool:
        import sys

        opener = "open" if sys.platform == "darwin" else "xdg-open"
        try:
            subprocess.Popen([opener, str(path)])
            return True
        except OSError as exc:
            self.log(f"could not open {path}: {exc}")
            return False

    def open_app(self, target: str) -> bool:
        try:
            subprocess.Popen([target])
            return True
        except OSError as exc:
            self.log(f"could not open {target}: {exc}")
            return False

    def open_url(self, url: str, browser: str = "", new_window: bool = False) -> bool:
        try:
            import webbrowser

            return bool(webbrowser.open(url, new=1 if new_window else 0))
        except Exception as exc:  # noqa: BLE001 - best effort
            self.log(f"could not open {url}: {exc}")
            return False

    def run_command(self, cmd: list[str], cwd: Path | None = None, detached: bool = False) -> bool:
        try:
            kwargs: dict[str, Any] = {"cwd": str(cwd) if cwd else None}
            if detached and os.name == "nt":
                kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
            subprocess.Popen(cmd, **kwargs)
            return True
        except OSError as exc:
            self.log(f"command failed ({cmd[0]}): {exc}")
            return False

    # -- media (metadata) ---------------------------------------------------
    def media_info(self, cover: bool = True, cover_px: int = 160) -> dict[str, Any] | None:
        """Current track metadata. `None` = unknown (keep existing on screen)."""
        return None


class WindowsHost(Host):
    """Windows: GSMTC for media, Shell COM for folders, startfile for apps."""

    name = "windows"

    def __init__(
        self,
        run_async: Callable[[Any], Any] | None = None,
        log: Callable[[str], None] = lambda msg: None,
    ) -> None:
        super().__init__(log=log)
        self.run_async = run_async or _run_async_oneshot
        self._navigating = False

    # -- input --------------------------------------------------------------
    def press_key(self, vk: int) -> bool:
        try:
            import ctypes

            user32 = ctypes.windll.user32
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.03)
            user32.keybd_event(vk, 0, 2, 0)
            return True
        except Exception as exc:  # noqa: BLE001
            self.log(f"key {vk:#x} failed: {exc}")
            return False

    def media(self, command: str) -> bool:
        """Tries native API; if unresponsive, falls back to virtual key."""
        method = {
            "play_pause": "try_toggle_play_pause_async",
            "next": "try_skip_next_async",
            "previous": "try_skip_previous_async",
        }[command]
        try:
            from winrt.windows.media.control import (  # noqa: PLC0415 - optional
                GlobalSystemMediaTransportControlsSessionManager as SMTC,
            )

            async def call() -> bool:
                manager = await SMTC.request_async()
                session = manager.get_current_session()
                if not session:
                    return False
                return bool(await getattr(session, method)())

            if self.run_async(call()):
                return True
        except Exception as exc:  # noqa: BLE001 - no winrt, no session, or no permission
            self.log(f"GSMTC unavailable for {command} ({exc}); using media key")
        return self.press_key(MEDIA_KEYS[command])

    # -- opening folders ----------------------------------------------------
    def open_folder(self, path: Path) -> bool:
        """Navigates existing Explorer window (COM) with timeout.

        If Shell COM does not respond in 4s, falls back to `explorer.exe`:
        window opens anyway, and event loop never hangs on stuck COM call.
        """
        import threading

        if self._navigating:
            self.log(f"previous navigation still in progress; ignoring {path}")
            return False

        box: dict[str, str] = {}
        abandoned = {"yes": False}

        def worker() -> None:
            try:
                res = _navigate_with(path)
                if abandoned["yes"]:
                    # fallback already opened folder: double navigation occurred
                    self.log(
                        f"warning: COM navigation of {path} completed after timeout "
                        f"({res}); window may have navigated twice"
                    )
                box["r"] = res
            finally:
                self._navigating = False

        self._navigating = True
        thread = threading.Thread(target=worker, daemon=True, name="navegar")
        thread.start()
        thread.join(timeout=4)
        if thread.is_alive():
            abandoned["yes"] = True
            self.log(f"Shell COM timed out after 4s; opening via explorer.exe: {path}")
            return self.run_command(["explorer.exe", str(path)])

        res = box.get("r", "")
        if res == "navigated":
            self.log(f"folder opened in existing window: {path}")
            return True
        self.log(f"Shell COM notice ({res}); opening via explorer.exe")
        return self.run_command(["explorer.exe", str(path)])

    def open_app(self, target: str) -> bool:
        try:
            os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606 - target comes from config
            return True
        except OSError as exc:
            self.log(f"could not open {target} ({exc}); trying via shell")
            return self.run_command(["cmd", "/c", "start", "", target])

    def open_url(self, url: str, browser: str = "", new_window: bool = False) -> bool:
        if browser:
            exe = _find_browser(browser)
            if exe:
                args = [exe]
                if new_window:
                    args.append("--new-window")
                args.append(url)
                return self.run_command(args)
            self.log(f"browser {browser!r} not found; using default")
        return super().open_url(url, browser, new_window)

    # -- media (metadata) ---------------------------------------------------
    def media_info(self, cover: bool = True, cover_px: int = 160) -> dict[str, Any] | None:
        try:
            return self.run_async(_read_media(cover, cover_px))
        except Exception as exc:  # noqa: BLE001
            self.log(f"media read failed: {exc}")
            return None


# --------------------------------------------------------------------------- #
# Windows helpers
# --------------------------------------------------------------------------- #

def _run_async_oneshot(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


async def _read_media(cover: bool, cover_px: int) -> dict[str, Any]:
    from winrt.windows.media.control import (  # noqa: PLC0415 - optional
        GlobalSystemMediaTransportControlsSessionManager as SMTC,
    )

    manager = await SMTC.request_async()
    session = manager.get_current_session()
    if not session:
        return {"tocando": False, "titulo": None, "artista": None, "capa": None}

    info = session.get_playback_info()
    playing = bool(info and info.playback_status.name == "PLAYING")
    props = await session.try_get_media_properties_async()
    title = props.title if props else None
    artist = props.artist if props else None

    cover_art = None
    if cover and props and props.thumbnail:
        cover_art = await _cover_base64(props.thumbnail, cover_px)
    return {"tocando": playing, "titulo": title, "artista": artist, "capa": cover_art}


async def _cover_base64(thumbnail: Any, cover_px: int) -> str | None:
    try:
        from PIL import Image
        from winrt.windows.storage.streams import DataReader

        stream = await thumbnail.open_read_async()
        size = stream.size
        if not size:
            return None
        reader = DataReader(stream)
        await reader.load_async(size)
        buf = bytearray(size)
        reader.read_bytes(buf)
        image = Image.open(io.BytesIO(buf))
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.thumbnail((cover_px, cover_px))
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001 - cover is decorative: screen proceeds without it
        return None


def _navigate_with(path: Path) -> str:
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        return f"no pywin32: {exc}"

    pythoncom.CoInitialize()
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        windows = shell.Windows()
        url = "file:///" + str(path).replace("\\", "/")
        for index in range(windows.Count):
            try:
                window = windows.Item(index)
                window.Navigate(url)
                window.Visible = True
                return "navigated"
            except Exception:  # noqa: BLE001 - tabs that reject Navigate
                continue
        return "no Explorer window"
    except Exception as exc:  # noqa: BLE001
        return f"com failed: {exc}"
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass


def _find_browser(name: str) -> str | None:
    candidates = {
        "chrome": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
        "edge": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
        "firefox": [r"C:\Program Files\Mozilla Firefox\firefox.exe"],
    }.get(name.lower(), [])
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def make_host(
    run_async: Callable[[Any], Any] | None = None,
    log: Callable[[str], None] = lambda msg: None,
) -> Host:
    """The host implementation for the current OS."""
    if os.name == "nt":
        return WindowsHost(run_async=run_async, log=log)
    return Host(log=log)
