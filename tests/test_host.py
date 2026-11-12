"""New window, in front: the command line built for each browser, and the folder.

Nothing is launched here: the launcher is injected, so `subprocess.Popen` (forbidden
in tests) never runs and no real browser or Explorer window is opened.
"""

from pathlib import Path

import pytest

from mcp_display_companion import host as host_module
from mcp_display_companion.host import (
    WindowsHost,
    _exe_from_command,
    default_browser_exe,
    new_window_command,
)

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
BRAVE = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
FIREFOX = r"C:\Program Files\Mozilla Firefox\firefox.exe"
URL = "https://example.test"


class Launcher:
    """Stands in for `subprocess.Popen`: records the argument list."""

    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def __call__(self, args):
        self.calls.append(list(args))
        return self.ok


@pytest.fixture
def allow_foreground():
    calls = []

    def allow():
        calls.append(True)
        return True

    allow.calls = calls
    return allow


def make_host(launch, allow_foreground, browser_lookup=lambda: None, messages=None):
    return WindowsHost(
        log=(messages.append if messages is not None else (lambda msg: None)),
        launch=launch,
        allow_foreground=allow_foreground,
        browser_lookup=browser_lookup,
    )


# --------------------------------------------------------------------------- #
# command line per browser
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("exe,flag", [
    (CHROME, "--new-window"),
    (EDGE, "--new-window"),
    (BRAVE, "--new-window"),
    (FIREFOX, "-new-window"),
])
def test_new_window_command_per_browser_family(exe, flag):
    assert new_window_command(exe, URL) == [exe, flag, URL]


def test_new_window_command_without_flag_keeps_browser_window():
    assert new_window_command(CHROME, URL, new_window=False) == [CHROME, URL]


@pytest.mark.parametrize("exe", [r"C:\Apps\some-browser.exe", r"C:\Apps\unknown"])
def test_new_window_command_unknown_browser_returns_none(exe):
    assert new_window_command(exe, URL) is None


@pytest.mark.parametrize("command,expected", [
    ('"' + EDGE + '" --single-argument %1', EDGE),
    (r'C:\Apps\chrome.exe -- "%1"', r'C:\Apps\chrome.exe'),
    ("", None),
    ('"unterminated', None),
])
def test_exe_from_registry_command(command, expected):
    assert _exe_from_command(command) == expected


@pytest.mark.parametrize("command,expected", [
    # unquoted path with a space: cutting at the first space gives "C:\Program"
    (r'C:\Program Files\App\browser.exe %1', r'C:\Program Files\App\browser.exe'),
    (r'C:\Program Files\App\BROWSER.EXE --single-argument %1',
     r'C:\Program Files\App\BROWSER.EXE'),
    (r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe %1 --flag',
     r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),
    # no space at all keeps working
    (r'C:\Apps\chrome.exe -- "%1"', r'C:\Apps\chrome.exe'),
    # no .exe in sight: still the first token
    (r'C:\Apps\run %1', r'C:\Apps\run'),
])
def test_exe_from_unquoted_command_with_space_in_the_path(command, expected):
    assert _exe_from_command(command) == expected


def test_default_browser_reads_url_association(monkeypatch):
    pytest.importorskip("winreg")

    def fake_registry(root, subkey, name):
        if subkey.endswith("UserChoice"):
            return "MSEdgeHTM"
        if subkey == r"MSEdgeHTM\shell\open\command":
            return f'"{EDGE}" --single-argument %1'
        return None

    monkeypatch.setattr(host_module, "_registry_value", fake_registry)
    assert default_browser_exe() == EDGE


def test_default_browser_without_user_choice(monkeypatch):
    monkeypatch.setattr(host_module, "_registry_value", lambda *args: None)
    assert default_browser_exe() is None


# --------------------------------------------------------------------------- #
# url
# --------------------------------------------------------------------------- #
def test_url_uses_default_browser_new_window_in_front(allow_foreground):
    launch = Launcher()
    host = make_host(launch, allow_foreground, browser_lookup=lambda: FIREFOX)
    assert host.open_url(URL) is True
    assert launch.calls == [[FIREFOX, "-new-window", URL]]
    # the window must be allowed to take the focus before it is started
    assert allow_foreground.calls == [True]


def test_url_named_browser_opens_new_window(monkeypatch, allow_foreground):
    monkeypatch.setattr(host_module, "_find_browser", lambda name: CHROME)
    launch = Launcher()
    assert make_host(launch, allow_foreground).open_url(URL, browser="chrome") is True
    assert launch.calls == [[CHROME, "--new-window", URL]]


def test_url_named_browser_missing_falls_back_to_default(monkeypatch, allow_foreground):
    monkeypatch.setattr(host_module, "_find_browser", lambda name: None)
    launch = Launcher()
    assert make_host(launch, allow_foreground, browser_lookup=lambda: EDGE).open_url(
        URL, browser="safari"
    ) is True
    assert launch.calls == [[EDGE, "--new-window", URL]]


def test_url_unknown_browser_goes_through_the_shell(monkeypatch, allow_foreground):
    shell_calls = []

    def shell(self, url, browser="", new_window=True):
        shell_calls.append((url, new_window))
        return True

    monkeypatch.setattr(host_module.Host, "open_url", shell)
    launch = Launcher()
    host = make_host(launch, allow_foreground, browser_lookup=lambda: r"C:\Apps\weird.exe")
    assert host.open_url(URL) is True
    assert shell_calls == [(URL, True)]
    assert launch.calls == []


def test_url_launcher_failure_is_a_failed_action(allow_foreground):
    host = make_host(Launcher(ok=False), allow_foreground, browser_lookup=lambda: CHROME)
    assert host.open_url(URL) is False


# --------------------------------------------------------------------------- #
# folder
# --------------------------------------------------------------------------- #
def test_folder_opens_new_window_by_default(allow_foreground):
    path = Path("C:/Users/me/Downloads")
    launch = Launcher()
    assert make_host(launch, allow_foreground).open_folder(path) is True
    assert launch.calls == [["explorer.exe", str(path)]]
    assert allow_foreground.calls == [True]


def test_folder_reuses_window_only_when_asked(monkeypatch, allow_foreground):
    monkeypatch.setattr(host_module, "_navigate_with", lambda path: "navigated")
    launch = Launcher()
    messages = []
    host = make_host(launch, allow_foreground, messages=messages)
    assert host.open_folder(Path("C:/tmp"), reuse_window=True) is True
    assert launch.calls == []
    assert any("existing window" in message for message in messages)


def test_folder_reuse_falls_back_to_a_new_window(monkeypatch, allow_foreground):
    monkeypatch.setattr(host_module, "_navigate_with", lambda path: "no Explorer window")
    path = Path("C:/tmp")
    launch = Launcher()
    assert make_host(launch, allow_foreground).open_folder(path, reuse_window=True) is True
    assert launch.calls == [["explorer.exe", str(path)]]


# --------------------------------------------------------------------------- #
# held keys: the generic host refuses, the Windows host calls keybd_event
# --------------------------------------------------------------------------- #
def test_generic_host_key_down_and_up_fail_with_a_message():
    messages = []
    generic = host_module.Host(log=messages.append)
    assert generic.key_down(0x11) is False
    assert generic.key_up(0x11) is False
    assert len(messages) == 2 and all("not implemented" in m for m in messages)


@pytest.mark.parametrize("vk,down,up", [
    (0x11, 0, 2),   # ctrl: plain key
    (0x5B, 1, 3),   # left win: extended key
    (0xA3, 1, 3),   # right ctrl: extended key
])
def test_windows_host_key_events_flags(monkeypatch, allow_foreground, vk, down, up):
    import ctypes
    import types
    events = []
    fake_user32 = types.SimpleNamespace(keybd_event=lambda *a: events.append(a))
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=fake_user32), raising=False)
    host = make_host(Launcher(), allow_foreground)
    assert host.key_down(vk) is True
    assert host.key_up(vk) is True
    assert events == [(vk, 0, down, 0), (vk, 0, up, 0)]
