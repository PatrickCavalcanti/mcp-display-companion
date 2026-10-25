from collections import deque
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import urllib.request
import pytest
from mcp_display_companion.client import PanelClient, PanelError, SessionGone, RateLimited, ToolError

# Capture the genuine operations before the autouse fixture blocks external I/O.
_REAL_CONNECT = socket.socket.connect


@pytest.fixture
def server(monkeypatch, make_config):
    requests = []
    replies = deque()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((dict(self.headers.items()), payload))
            reply = replies.popleft() if replies else {}
            status = reply.get('status', 200)
            result = reply.get('result', {'structuredContent': {'presentation': 'screen-1'}})
            response = {'jsonrpc': '2.0', 'id': payload.get('id'), 'result': result}
            if reply.get('sse'):
                other = {'jsonrpc': '2.0', 'id': -999, 'result': {'wrong': True}}
                body = (': keepalive\n\ndata: ' + json.dumps(other) + '\n\ndata: ' + json.dumps(response) + '\n\n').encode()
            else:
                body = json.dumps(response).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/event-stream' if reply.get('sse') else 'application/json')
            self.send_header('Mcp-Session-Id', 'session-123')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = httpd.server_address[1]
    def local_only(sock, address):
        assert address[0] == '127.0.0.1' and address[1] == port, 'External connection forbidden'
        return _REAL_CONNECT(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', local_only)
    # Ignore proxies configured on the developer's machine.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    monkeypatch.setattr(urllib.request, 'urlopen', opener.open)
    thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), name='pytest-panel-http')
    thread.start()
    section = replace(make_config().panel, url=f'http://127.0.0.1:{port}/mcp')
    try:
        yield PanelClient(section), requests, replies
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_connect_headers_and_session(server):
    client, requests, _ = server
    client.connect()
    client.read_events(None)
    assert client.session_id == 'session-123'
    headers = [{k.lower(): v for k, v in h.items()} for h, p in requests]
    assert headers[0]['accept'] == 'application/json, text/event-stream'
    assert headers[0]['authorization'] == 'Bearer fake-token'
    assert 'mcp-session-id' not in headers[0]
    assert all(h['mcp-session-id'] == 'session-123' for h in headers[1:])
    assert [p['method'] for h, p in requests] == ['initialize', 'notifications/initialized', 'tools/call']


def test_sse_matches_id(server):
    client, _, replies = server
    replies.append({'sse': True, 'result': {'structuredContent': {'cursor': 42}}})
    assert client.read_events(None) == {'cursor': 42}


@pytest.mark.parametrize('status,error', [(404, SessionGone), (429, RateLimited), (401, PanelError), (403, PanelError)])
def test_http_errors(server, status, error):
    client, _, replies = server
    replies.append({'status': status})
    with pytest.raises(error) as raised:
        client.read_events(None)
    if status in (401, 403): assert 'token' in str(raised.value).lower()


def test_tool_error(server):
    client, _, replies = server
    replies.append({'result': {'isError': True, 'content': [{'type': 'text', 'text': 'refused'}]}})
    with pytest.raises(ToolError, match='refused'):
        client.read_events(None)


@pytest.mark.parametrize('since,expected', [(None, {}), (42, {'since': 42})])
def test_read_events_arguments(server, since, expected):
    client, requests, _ = server
    client.read_events(since)
    assert requests[-1][1]['params'] == {'name': 'read_events', 'arguments': expected}


@pytest.mark.parametrize('presentation', [None, 'event-screen'])
def test_update_view_arguments(server, presentation):
    client, requests, _ = server
    client.update_view({'vivo': 1}, presentation)
    expected = {'data': {'vivo': 1}}
    if presentation is not None: expected['presentation'] = presentation
    assert requests[-1][1]['params'] == {'name': 'update_view', 'arguments': expected}


def test_show_view_returns_presentation(server):
    client, requests, _ = server
    assert client.show_view('<html>test</html>', title='Test') == 'screen-1'
    assert requests[-1][1]['params'] == {'name': 'show_view', 'arguments': {'html': '<html>test</html>', 'ttl_min': 0, 'title': 'Test'}}
