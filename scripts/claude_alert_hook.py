"""Claude Code hook: tells the wall panel when a session waits for a permission,
and lets the panel answer it.

Register it for the events below through the launcher command documented in
docs/configuration.md (it exits 0 with no output even if this file is missing;
a bare `python missing.py` would exit 2, which Claude Code reads as "deny"):

* ``PermissionRequest`` (synchronous, timeout 300 s): writes an *actionable* alert
  ``<alerts>/<session>-<h>-<rand>.json`` (``h`` = hash of the tool call) and waits for ``<same id>.answer`` (``allow`` or
  ``deny``, written by the companion when you press a button on the panel). It then
  prints the decision JSON. If the alert file disappears (you answered on the PC) or
  the wait ends, it exits with no output and the normal dialog goes on.
* ``Notification`` (matcher ``permission_prompt``): fallback. Writes the plain
  ``<alerts>/<session>.json`` unless a ``PermissionRequest`` alert is already up.
* ``PostToolUse``, ``PostToolUseFailure``: removes ``<session>.json`` and only the
  request files of *that* tool call (parallel calls and subagents keep theirs).
* ``UserPromptSubmit``, ``Stop``, ``SessionEnd``: removes every alert file of that session.
* ``ExitPlanMode``, ``AskUserQuestion`` and ``mcp__ccd_*`` tools are never offered
  for approval from the panel (no file, no output).

Standard library only, never raises, and prints nothing except a decision JSON.
See docs/configuration.md (`alerts`).
"""
import hashlib
import json
import os
import re
import secrets
import sys
import time

CLEAR_ALL_EVENTS = ("UserPromptSubmit", "Stop", "SessionEnd")
CLEAR_CALL_EVENTS = ("PostToolUse", "PostToolUseFailure")
# <sid>-<8 hex of the call>-<16 random hex> must fit the 80 characters the
# companion accepts for an id: 54 + 1 + 8 + 1 + 16 = 80
MAX_ID = 54
BASH_CAP = 1000
OTHER_CAP = 600
TOOL_CAP = 80
BASH_KEYS = ("command", "description", "timeout", "run_in_background")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
POLL_S = 0.25
DEFAULT_WAIT_S = 290.0


def alerts_dir():
    custom = os.environ.get("PANEL_COMPANION_ALERTS")
    if custom:
        return custom
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config")
    return os.path.join(base, "mcp-display-companion", "alerts")


def safe_id(value, limit=MAX_ID):
    text = "".join(c for c in str(value or "") if c.isascii() and (c.isalnum() or c in "_-"))
    return text[:limit]


def project_of(data):
    cwd = str(data.get("cwd") or "").replace("\\", "/").rstrip("/")
    return os.path.basename(cwd) or cwd


def write_json(folder, name, payload):
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f".{name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False)
    os.replace(tmp, os.path.join(folder, name))


def own_files(folder, sid, call=None):
    """Names of the alert/answer files of this session.

    `call` is the hash of one tool call: then only `<sid>.json` and the
    `<sid>-<call>-*` request files match. Without it, every file of the session.
    """
    names = []
    prefix = sid + "-" if call is None else f"{sid}-{call}-"
    try:
        with os.scandir(folder) as it:
            for entry in it:
                name = entry.name
                stem, dot, ext = name.rpartition(".")
                if not dot or ext not in ("json", "answer"):
                    continue
                if stem == sid and ext == "json" or stem.startswith(prefix):
                    names.append(name)
    except OSError:
        pass
    return names


def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def short_server(server):
    return UUID_RE.sub(lambda m: m.group(0)[:8], server)


def display_tool(tool):
    """Name shown on the panel: `mcp__<server>__<op>` becomes `<server>: <op>`.

    Returns (text, cut). The operation is never shortened: if it does not fit,
    `cut` is True and the panel will not offer to approve.
    """
    if tool.startswith("mcp__") and "__" in tool[5:]:
        server, _, op = tool[5:].partition("__")
        server = short_server(server)
        text = f"{server[:40]}: {op}"
        return text, len(op) > TOOL_CAP
    return tool[:TOOL_CAP], len(tool) > TOOL_CAP


def summarize(tool, tool_input):
    """(summary text, truncated). `truncated` means the text hides part of the call."""
    if not isinstance(tool_input, dict):
        tool_input = {}
    if tool == "Bash":
        command = tool_input.get("command")
        command = command if isinstance(command, str) else ("" if command is None else str(command))
        text = command[:BASH_CAP]
        truncated = len(command) > BASH_CAP
        extra = [str(k)[:40] for k in tool_input if k not in BASH_KEYS]
        if extra:
            truncated = True
            text += "".join(f" [{k}]" for k in extra[:5])
        return text, truncated
    try:
        compact = json.dumps(tool_input, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return "(entrada ilegível)", True
    if compact == "{}":
        return "(sem argumentos)", False
    return compact[:OTHER_CAP], len(compact) > OTHER_CAP


def call_hash(tool, tool_input):
    try:
        canonical = json.dumps(tool_input, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        canonical = repr(tool_input)
    digest = hashlib.sha1((str(tool) + canonical).encode("utf-8", "replace"))
    return digest.hexdigest()[:8]


def not_approvable(tool):
    return (tool in ("ExitPlanMode", "AskUserQuestion") or "ExitPlan" in tool
            or tool.startswith("mcp__ccd_"))


def decision_json(answer):
    decision = {"behavior": "allow"} if answer == "allow" else {
        "behavior": "deny", "message": "Denied by the panel"}
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PermissionRequest", "decision": decision}})


def emit(text):
    out = getattr(sys.stdout, "buffer", None)
    if out is not None:
        out.write(text.encode("utf-8"))
        out.flush()
    else:
        sys.stdout.write(text)
        sys.stdout.flush()


def wait_seconds():
    try:
        return max(0.0, float(os.environ.get("PANEL_COMPANION_ALERT_WAIT", DEFAULT_WAIT_S)))
    except ValueError:
        return DEFAULT_WAIT_S


def permission_request(data, sid, folder):
    raw_tool = str(data.get("tool_name") or "?")
    if not_approvable(raw_tool):
        return
    tool_input = data.get("tool_input")
    rid = f"{sid}-{call_hash(raw_tool, tool_input)}-{secrets.token_hex(8)}"
    tool, tool_cut = display_tool(raw_tool)
    summary, summary_cut = summarize(raw_tool, tool_input)
    request_path = os.path.join(folder, rid + ".json")
    answer_path = os.path.join(folder, rid + ".answer")
    write_json(folder, rid + ".json", {
        "session": sid,
        "project": project_of(data),
        "message": f"Claude wants to use {tool}"[:300],
        "at": time.time(),
        "request": {"tool": tool, "summary": summary, "truncated": bool(tool_cut or summary_cut)},
        "actionable": True,
    })
    deadline = time.monotonic() + wait_seconds()
    while True:
        try:
            with open(answer_path, encoding="utf-8") as file:
                answer = file.read().strip()
        except OSError:
            answer = ""
        if answer in ("allow", "deny"):
            remove(request_path)
            remove(answer_path)
            emit(decision_json(answer))
            return
        if answer:  # junk answer: ignore it and keep waiting
            remove(answer_path)
        if not os.path.exists(request_path):  # answered on the PC: stay silent
            remove(answer_path)
            return
        if time.monotonic() >= deadline:
            remove(request_path)
            remove(answer_path)
            return
        time.sleep(POLL_S)


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        data = json.loads(raw or "{}")
        if not isinstance(data, dict):
            return
        sid = safe_id(data.get("session_id"))
        if not sid:
            return
        event = data.get("hook_event_name")
        folder = alerts_dir()
        if event in CLEAR_ALL_EVENTS:
            for name in own_files(folder, sid):
                remove(os.path.join(folder, name))
        elif event in CLEAR_CALL_EVENTS:
            tool = data.get("tool_name")
            call = call_hash(tool, data.get("tool_input")) if tool else "-"
            for name in own_files(folder, sid, call):
                remove(os.path.join(folder, name))
        elif event == "PermissionRequest":
            permission_request(data, sid, folder)
        elif event == "Notification":
            if any(n.startswith(sid + "-") and n.endswith(".json") for n in own_files(folder, sid)):
                return  # a PermissionRequest alert is already up
            write_json(folder, sid + ".json", {
                "session": sid,
                "project": project_of(data),
                "message": str(data.get("message") or ""),
                "at": time.time(),
            })
    except Exception:  # noqa: BLE001 - a hook must never raise
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
