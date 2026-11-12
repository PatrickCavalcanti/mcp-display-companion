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
from pathlib import Path, PureWindowsPath
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
LaunchFn = Callable[[list[str]], bool]


def _spawn(args: list[str]) -> bool:
    """Starts a detached process: one list of arguments, never a shell line."""
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
    subprocess.Popen(args, **kwargs)
    return True


class Host:
    """Base implementation: standard library only. Baseline for all systems."""

    name = "generic"

    def __init__(
        self,
        log: Callable[[str], None] = lambda msg: None,
        launch: LaunchFn | None = None,
    ) -> None:
        self.log = log
        # how a new window is started; injected in tests (nothing is launched)
        self.launch: LaunchFn = launch or _spawn

    # -- input --------------------------------------------------------------
    def press_key(self, vk: int) -> bool:
        self.log(f"press_key({vk}) not implemented on {self.name}")
        return False

    def key_down(self, vk: int) -> bool:
        self.log(f"key_down({vk}) not implemented on {self.name}")
        return False

    def key_up(self, vk: int) -> bool:
        self.log(f"key_up({vk}) not implemented on {self.name}")
        return False

    def media(self, command: str) -> bool:
        """play_pause / next / previous."""
        return self.press_key(MEDIA_KEYS[command])

    def volume(self, direction: str) -> bool:
        return self.press_key(VOLUME_KEYS[direction])

    # -- opening targets ----------------------------------------------------
    def open_folder(self, path: Path, reuse_window: bool = False) -> bool:
        """Always a new window of the default file manager (Windows: Win+E)."""
        import sys

        if reuse_window:
            self.log(f"window reuse is not available on this system; opening a new one: {path}")
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        return self._launch([opener, str(path)], f"folder {path}")

    def open_app(self, target: str) -> bool:
        try:
            subprocess.Popen([target])
            return True
        except OSError as exc:
            self.log(f"could not open {target}: {exc}")
            return False

    def open_url(self, url: str, browser: str = "", new_window: bool = True) -> bool:
        try:
            import webbrowser

            opener = webbrowser.open_new if new_window else webbrowser.open
            return bool(opener(url))
        except Exception as exc:  # noqa: BLE001 - best effort
            self.log(f"could not open {url}: {exc}")
            return False

    def _launch(self, args: list[str], what: str = "") -> bool:
        try:
            return bool(self.launch(args))
        except OSError as exc:
            self.log(f"could not open {what or args[0]}: {exc}")
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
        launch: LaunchFn | None = None,
        allow_foreground: Callable[[], bool] | None = None,
        browser_lookup: Callable[[], str | None] | None = None,
    ) -> None:
        super().__init__(log=log, launch=launch)
        self.run_async = run_async or _run_async_oneshot
        # the service runs in the background: without this the new window opens
        # behind whatever is on screen and the user does not see it
        self.allow_foreground = allow_foreground or _allow_set_foreground_window
        # default browser comes from the registry (UrlAssociations\https)
        self.browser_lookup = browser_lookup or default_browser_exe
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

    # extended keys need KEYEVENTF_EXTENDEDKEY (1); key release is KEYEVENTF_KEYUP (2).
    # Per Microsoft ("Keyboard Input Overview", Extended-Key Flag): right Alt/Ctrl, the
    # Ins/Del/Home/End/PgUp/PgDn/arrow cluster, Break, Print Screen, numpad Divide,
    # Windows and Application keys. Num Lock and right Shift are NOT extended.
    _EXTENDED_VKS = (
        0x03,  # VK_CANCEL (Ctrl+Break)
        0x21, 0x22, 0x23, 0x24,  # PgUp, PgDn, End, Home
        0x25, 0x26, 0x27, 0x28,  # left, up, right, down
        0x2C, 0x2D, 0x2E,  # Print Screen, Insert, Delete
        0x5B, 0x5C, 0x5D,  # left Win, right Win, Application
        0x6F,  # numpad Divide
        0xA3, 0xA5,  # right Ctrl, right Alt
    )

    def key_down(self, vk: int) -> bool:
        try:
            import ctypes

            flags = 1 if vk in self._EXTENDED_VKS else 0
            ctypes.windll.user32.keybd_event(vk, 0, flags, 0)
            return True
        except Exception as exc:  # noqa: BLE001
            self.log(f"key_down {vk:#x} failed: {exc}")
            return False

    def key_up(self, vk: int) -> bool:
        try:
            import ctypes

            flags = 2 | (1 if vk in self._EXTENDED_VKS else 0)
            ctypes.windll.user32.keybd_event(vk, 0, flags, 0)
            return True
        except Exception as exc:  # noqa: BLE001
            self.log(f"key_up {vk:#x} failed: {exc}")
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
    def open_folder(self, path: Path, reuse_window: bool = False) -> bool:
        """New Explorer window by default -- like Win+E.

        Navigating an existing window pulls the user out of where they were, so
        it is only done when the config asks for it (`"reuse_window": true`).
        """
        if not reuse_window:
            return self._launch_foreground(["explorer.exe", str(path)], f"folder {path}")
        return self._navigate_existing_window(path)

    def _navigate_existing_window(self, path: Path) -> bool:
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
            return self._launch_foreground(["explorer.exe", str(path)], f"folder {path}")

        res = box.get("r", "")
        if res == "navigated":
            self.log(f"folder opened in existing window: {path}")
            return True
        self.log(f"Shell COM notice ({res}); opening via explorer.exe")
        return self._launch_foreground(["explorer.exe", str(path)], f"folder {path}")

    def open_app(self, target: str) -> bool:
        try:
            os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606 - target comes from config
            return True
        except OSError as exc:
            self.log(f"could not open {target} ({exc}); trying via shell")
            return self.run_command(["cmd", "/c", "start", "", target])

    def open_url(self, url: str, browser: str = "", new_window: bool = True) -> bool:
        exe = _find_browser(browser) if browser else None
        if browser and not exe:
            self.log(f"browser {browser!r} not found; using the default one")
        if not exe:
            exe = self.browser_lookup()
        if exe:
            args = new_window_command(exe, url, new_window)
            if args:
                return self._launch_foreground(args, f"url {url}")
            self.log(f"unknown browser {exe!r}; opening {url} through the shell")
        return super().open_url(url, browser, new_window)

    def _launch_foreground(self, args: list[str], what: str = "") -> bool:
        """Lets the new process take the focus before starting it."""
        try:
            self.allow_foreground()
        except Exception as exc:  # noqa: BLE001 - focus is best effort
            self.log(f"could not hand the foreground to the new window: {exc}")
        return self._launch(args, what)

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


# how each browser family asks for a new window
NEW_WINDOW_FLAGS = {"chromium": "--new-window", "firefox": "-new-window"}

CHROMIUM_NAMES = {
    "chrome", "msedge", "brave", "chromium", "vivaldi", "opera",
    "opera_gx", "thorium", "yandex", "arc",
}
FIREFOX_NAMES = {"firefox", "firefoxdeveloperedition", "waterfox", "librewolf"}


def browser_family(exe: str) -> str | None:
    """`chromium`, `firefox`, or `None` when the flag is not known."""
    # Browser executables are Windows paths; PureWindowsPath splits "\\" on any OS (and still accepts "/").
    name = PureWindowsPath(exe).stem.lower()
    if name in CHROMIUM_NAMES:
        return "chromium"
    if name in FIREFOX_NAMES:
        return "firefox"
    return None


def new_window_command(exe: str, url: str, new_window: bool = True) -> list[str] | None:
    """Command line for `url` in a new window of `exe`; `None` = unknown browser."""
    args = [exe]
    if new_window:
        flag = NEW_WINDOW_FLAGS.get(browser_family(exe) or "")
        if flag is None:
            return None
        args.append(flag)
    args.append(url)
    return args


def _registry_value(root: Any, subkey: str, name: str) -> str | None:
    try:
        import winreg

        with winreg.OpenKey(root, subkey) as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    except ImportError:
        return None
    return str(value) if value else None


def _exe_from_command(command: str) -> str | None:
    """`"C:\\...\\chrome.exe" --single-argument %1` -> the executable path.

    Without quotes the path may still contain spaces (`C:\\Program Files\\...`),
    so the cut is at the end of `.exe`, not at the first space -- cutting at the
    space would yield `C:\\Program` and the launch would fail.
    """
    text = command.strip()
    if not text:
        return None
    if text.startswith('"'):
        end = text.find('"', 1)
        if end < 0:
            return None
        return text[1:end] or None
    end = text.lower().find(".exe")
    if end >= 0:
        return text[: end + len(".exe")].strip() or None
    return text.split()[0]


def default_browser_exe() -> str | None:
    """Executable of the default browser, read from the registry.

    `HKCU\\...\\UrlAssociations\\https\\UserChoice\\ProgId` gives something like
    `MSEdgeHTM`; `HKCR\\<ProgId>\\shell\\open\\command` gives the command line.
    """
    try:
        import winreg
    except ImportError:  # not Windows
        return None

    prog_id = _registry_value(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
        "ProgId",
    )
    if not prog_id:
        return None
    command = _registry_value(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command", "")
    if not command:
        return None
    return _exe_from_command(command)


def _allow_set_foreground_window() -> bool:
    """Lets the window launched next take the foreground (Windows only)."""
    try:
        import ctypes

        ASFW_ANY = -1
        return bool(ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY))
    except Exception:  # noqa: BLE001 - not Windows, or no user32
        return False


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
        "firefox": [
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
            r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        ],
        "brave": [
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        ],
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
