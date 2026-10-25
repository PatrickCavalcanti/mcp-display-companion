"""Panel MCP client: transport layer defined by the MCP Display contract.

What this module does (the panel guide, `panel://guide`, is the reference):

- `POST /mcp` with `Authorization`, `Accept: application/json, text/event-stream`
  and `Mcp-Session-Id` tracked across responses;
- response can arrive in JSON **or** SSE (`data:` lines) -- both are parsed;
- `resources/subscribe` to `panel://events` with a persistent `GET /mcp` stream
  thread: touches arrive in ~40 ms instead of waiting for next poll;
- 404 = session gone (reconnect), 429 = rate limit (wait),
  `isError` = tool returned error.

Polling remains active as fallback when the stream cannot be opened.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any, Callable

from .config import PanelSection


class PanelError(Exception):
    """Generic panel transport failure."""


class SessionGone(PanelError):
    """404: the panel no longer recognizes this MCP session -- time to reconnect."""


class RateLimited(PanelError):
    """429: panel rate limit reached (10 per 10s on update_view/set_display)."""


class ToolError(PanelError):
    """The tool responded with `isError`."""


class PanelClient:
    def __init__(self, section: PanelSection, log: Callable[[str], None] = lambda m: None) -> None:
        self.section = section
        self.log = log
        self.session_id: str | None = None
        self._id = 0
        self._lock = threading.Lock()
        self.stream_ok = threading.Event()
        self._stream_resp: Any = None
        self._generation = 0

    # ------------------------------------------------------------------ #
    # transport
    # ------------------------------------------------------------------ #
    def _redact(self, text: str) -> str:
        """Removes the token from any message that might go into logs.

        A server or proxy may echo headers in error messages; logs are written
        to disk and must not leak credentials.
        """
        token = self.section.token
        if token and token in text:
            return text.replace(token, "***")
        return text

    def _headers(self, accept: str = "application/json, text/event-stream") -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.section.token}",
            "Accept": accept,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _post(
        self,
        payload: dict[str, Any],
        timeout: float | None = None,
        accept: str = "application/json, text/event-stream",
    ) -> dict[str, Any] | None:
        body = json.dumps(payload).encode("utf-8")
        headers = self._headers(accept)
        headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.section.url, data=body, headers=headers)
        raw, content_type = self._open(request, timeout)
        if "id" not in payload:  # notification: no response
            return None
        return _pick_message(raw, content_type, payload["id"], self.section.token)

    def _open(self, request: urllib.request.Request, timeout: float | None) -> tuple[str, str]:
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.section.timeout_s) as resp:
                session = resp.headers.get("Mcp-Session-Id")
                if session:
                    self.session_id = session
                return resp.read().decode("utf-8", "replace"), resp.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise SessionGone("unknown MCP session (404)") from None
            if exc.code == 429:
                raise RateLimited("panel rate limit exceeded (429)") from None
            if exc.code in (401, 403):
                raise PanelError(
                    f"panel rejected the token ({exc.code}). Check the token in "
                    "secrets.json or request pairing again."
                ) from None
            raise PanelError(self._redact(f"HTTP {exc.code}: {exc.reason}")) from None
        except urllib.error.URLError as exc:
            raise PanelError(self._redact(f"panel unreachable ({exc.reason})")) from None
        except TimeoutError:
            raise PanelError("panel did not respond in time") from None

    def _rpc(self, method: str, params: dict[str, Any] | None = None,
             timeout: float | None = None) -> dict[str, Any]:
        with self._lock:
            self._id += 1
            request_id = self._id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        response = self._post(payload, timeout=timeout)
        if response is None:
            raise PanelError(f"{method}: empty response")
        if "error" in response:
            error = response["error"] or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise PanelError(self._redact(f"{method}: {message}"))
        return response.get("result") or {}

    # ------------------------------------------------------------------ #
    # session
    # ------------------------------------------------------------------ #
    def connect(self) -> dict[str, Any]:
        self.session_id = None
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": self.section.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": self.section.client_name, "version": "1.0"},
            },
        )
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except PanelError:
            pass  # some servers do not acknowledge notification bodies
        return result

    # ------------------------------------------------------------------ #
    # tools
    # ------------------------------------------------------------------ #
    def call_tool(self, name: str, arguments: dict[str, Any] | None = None,
                  timeout: float | None = None) -> dict[str, Any]:
        result = self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        )
        if result.get("isError"):
            detail = ""
            content = result.get("content") or []
            if content and isinstance(content, list):
                detail = str(content[0].get("text", ""))[:200]
            raise ToolError(self._redact(f"{name}: {detail or 'rejected'}"))
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        # server without structuredContent: fallback to text
        content = result.get("content") or []
        if content and isinstance(content, list):
            text = str(content[0].get("text", ""))
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {}
            except ValueError:
                return {}
        return {}

    def read_events(self, since: int | None) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        if since is not None:
            arguments["since"] = since
        return self.call_tool("read_events", arguments)

    def update_view(self, data: dict[str, Any], presentation: str | None = None) -> dict[str, Any]:
        arguments: dict[str, Any] = {"data": data}
        if presentation:
            arguments["presentation"] = presentation
        return self.call_tool("update_view", arguments, timeout=5)

    def set_display(self, brightness: str | int) -> dict[str, Any]:
        return self.call_tool("set_display", {"brightness": brightness}, timeout=5)

    def show_view(self, html: str, ttl_min: int = 0, title: str | None = None) -> str | None:
        arguments: dict[str, Any] = {"html": html, "ttl_min": ttl_min}
        if title:
            arguments["title"] = title
        result = self.call_tool("show_view", arguments, timeout=20)
        return result.get("presentation")

    # ------------------------------------------------------------------ #
    # event stream (optional)
    # ------------------------------------------------------------------ #
    def close_stream(self) -> None:
        """Closes current SSE stream (if open). The stream worker thread exits."""
        self._generation += 1
        self.stream_ok.clear()
        resp, self._stream_resp = self._stream_resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass

    def subscribe(self, on_update: Callable[[], None]) -> threading.Thread:
        """Subscribes to `panel://events` and calls `on_update` on each panel update.

        The thread is a daemon and dies when the stream disconnects; `stream_ok`
        tells the loop whether it can rely on it (~40 ms latency) or fall back to polling.
        """

        self.close_stream()
        generation = self._generation

        def worker() -> None:
            try:
                request = urllib.request.Request(
                    self.section.url,
                    headers=self._headers(accept="text/event-stream"),
                )
                with urllib.request.urlopen(request, timeout=None) as stream:
                    self._stream_resp = stream
                    try:
                        self._rpc("resources/subscribe", {"uri": "panel://events"})
                    except PanelError as exc:
                        self.log(f"event subscription rejected ({exc}); falling back to polling")
                        return
                    if generation != self._generation:
                        return
                    self.stream_ok.set()
                    self.log("subscribed to SSE events (low latency)")
                    for line in stream:
                        if b"notifications/resources/updated" in line:
                            on_update()
            except Exception as exc:  # noqa: BLE001 - falls back to polling
                self.log(f"event stream terminated ({exc}); falling back to polling")
            finally:
                # only current generation may declare stream down: an old connection
                # being closed must not take down the new one
                if generation == self._generation:
                    self.stream_ok.clear()

        thread = threading.Thread(target=worker, daemon=True, name="sse-eventos")
        thread.start()
        return thread


def _redact_inline(text: str, token: str | None) -> str:
    if token and token in text:
        return text.replace(token, "***")
    return text


def _pick_message(raw: str, content_type: str, request_id: int,
                  token: str | None = None) -> dict[str, Any] | None:
    """The response can be plain JSON or an SSE block with one `data:` line per message."""
    if content_type != "text/event-stream":
        try:
            parsed = json.loads(raw) if raw.strip() else None
        except ValueError as exc:
            raise PanelError(
                f"response is not JSON: {_redact_inline(raw[:120], token)!r} ({exc})"
            ) from None
        return parsed if isinstance(parsed, dict) else None

    fallback: dict[str, Any] | None = None
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            message = json.loads(line[5:].strip())
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        if message.get("id") == request_id:
            return message
        fallback = fallback or message
    return fallback
