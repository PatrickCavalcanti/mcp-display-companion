"""Claude Code plugin packaging: manifests, hook registration and their match with the hook script."""

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_JSON = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = ROOT / ".claude-plugin" / "marketplace.json"
HOOKS_JSON = ROOT / "hooks" / "hooks.json"
HOOK = ROOT / "scripts" / "claude_alert_hook.py"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def hook_script_events():
    """Every event the script reacts to: the tuples at the top plus the `event ==` branches."""
    tree = ast.parse(HOOK.read_text(encoding="utf-8"))
    events = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in ("CLEAR_ALL_EVENTS", "CLEAR_CALL_EVENTS")
                for t in node.targets):
            events.update(ast.literal_eval(node.value))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                and node.left.id == "event" and isinstance(node.ops[0], ast.Eq)
                and isinstance(node.comparators[0], ast.Constant)):
            events.add(node.comparators[0].value)
    return events


def script_constant(name):
    tree = ast.parse(HOOK.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in the hook script")


def registered_hooks():
    """[(event, matcher, hook dict)] from hooks/hooks.json."""
    out = []
    for event, groups in load(HOOKS_JSON)["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                out.append((event, group.get("matcher"), hook))
    return out


@pytest.mark.parametrize("path", [PLUGIN_JSON, MARKETPLACE_JSON, HOOKS_JSON])
def test_plugin_json_files_are_valid(path):
    assert isinstance(load(path), dict)


def test_plugin_version_matches_the_package():
    version = load(PLUGIN_JSON)["version"]
    pyproject = re.search(r'(?m)^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    init = re.search(r'__version__\s*=\s*"([^"]+)"',
                     (ROOT / "mcp_display_companion" / "__init__.py").read_text(encoding="utf-8"))
    assert version == pyproject.group(1) == init.group(1)


def test_marketplace_lists_the_plugin_at_the_repo_root():
    plugin = load(PLUGIN_JSON)
    market = load(MARKETPLACE_JSON)
    assert market["name"] == "mcp-display"
    assert market["owner"]["name"]
    assert len(market["plugins"]) == 1
    entry = market["plugins"][0]
    assert entry["name"] == plugin["name"] == "mcp-display-alerts"
    assert entry["source"] in ("./", ".")


def test_every_registered_event_is_handled_by_the_script_and_vice_versa():
    registered = {event for event, _, _ in registered_hooks()}
    assert registered == hook_script_events()


def test_notification_hook_is_limited_to_permission_prompts():
    matchers = {event: matcher for event, matcher, _ in registered_hooks()}
    assert matchers["Notification"] == "permission_prompt"


def test_every_path_in_the_hooks_exists_in_the_repo():
    for _, _, hook in registered_hooks():
        paths = re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"\s]+)", hook["command"])
        assert paths, hook["command"]
        for rel in paths:
            assert (ROOT / rel).is_file(), rel


def test_timeouts_fit_the_hook_wait():
    wait = script_constant("DEFAULT_WAIT_S")
    by_event = {event: hook for event, _, hook in registered_hooks()}
    permission = by_event["PermissionRequest"]
    assert permission["timeout"] > wait  # Claude Code must not cut the hook before its own wait ends
    assert permission["onFailure"] == "continue"  # a failing hook falls back to the normal PC dialog
    assert not permission.get("async")
    for event, hook in by_event.items():
        if event != "PermissionRequest":
            assert hook.get("async") is True, event  # clear and fallback hooks never hold Claude Code up
            assert "onFailure" not in hook


def test_command_never_denies_when_the_interpreter_is_missing():
    # a launcher falling back from python3 to python; no plain `exit 2` path, no `onFailure: block`
    for _, _, hook in registered_hooks():
        assert hook["command"].startswith("python3 ")
        assert " || python " in hook["command"]
        assert hook.get("onFailure") != "block"
