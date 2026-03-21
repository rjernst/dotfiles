"""Unit tests for docker/agent-loop/proxy/proxy.py."""

import http.client
import http.server
import io
import os
import sys
import threading
import urllib.error
import urllib.request
from unittest.mock import patch

import pytest

from conftest import import_script

proxy = import_script(
    "proxy",
    path=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "docker", "agent-loop", "proxy", "proxy.py",
    ),
)


# ---------------------------------------------------------------------------
# read_token
# ---------------------------------------------------------------------------

class TestReadToken:
    def test_reads_token_from_stdin(self):
        fake_stdin = io.StringIO("my-secret-token\n")
        with patch.object(sys, "stdin", fake_stdin):
            token = proxy.read_token()
        assert token == "my-secret-token"

    def test_strips_whitespace(self):
        fake_stdin = io.StringIO("  tok-with-spaces  \n")
        with patch.object(sys, "stdin", fake_stdin):
            token = proxy.read_token()
        assert token == "tok-with-spaces"

    def test_exits_on_empty_stdin(self):
        fake_stdin = io.StringIO("")
        with patch.object(sys, "stdin", fake_stdin):
            with pytest.raises(SystemExit) as exc_info:
                proxy.read_token()
            assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Helpers — use http.client for test requests so urllib.request.urlopen
# patches only affect the proxy's internal upstream calls.
# ---------------------------------------------------------------------------

def _start_proxy_server(token, target):
    """Start a proxy server on an ephemeral port and return (server, port)."""
    proxy.ProxyHandler.real_token = token
    proxy.ProxyHandler.target = target

    server = http.server.HTTPServer(("127.0.0.1", 0), proxy.ProxyHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def _http_request(port, method, path, body=None, headers=None):
    """Make an HTTP request using http.client (bypasses urllib.request mock)."""
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, data


class _FakeResponse:
    """Minimal response object for mocking urllib.request.urlopen."""
    status = 200
    headers = http.client.HTTPMessage()

    def read(self, n=-1):
        return b""


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_returns_200_with_body(self):
        server, port = _start_proxy_server("test-token", "http://unused")
        try:
            resp, data = _http_request(port, "GET", "/health")
            assert resp.status == 200
            assert data == b"agent-loop-proxy ok"
        finally:
            server.shutdown()


# ---------------------------------------------------------------------------
# Proxy forwarding
# ---------------------------------------------------------------------------

class TestProxyForwarding:
    """Test that the proxy replaces Authorization and forwards correctly."""

    def test_authorization_header_replaced(self):
        """Upstream receives the real token, not the client's phantom token."""
        captured = {}

        def mock_urlopen(req, **kwargs):
            captured["url"] = req.full_url
            captured["method"] = req.method
            captured["headers"] = dict(req.headers)
            captured["data"] = req.data
            return _FakeResponse()

        server, port = _start_proxy_server("real-secret", "http://upstream.test")
        try:
            with patch.object(urllib.request, "urlopen", side_effect=mock_urlopen):
                resp, _ = _http_request(port, "POST", "/v1/messages",
                    body=b'{"prompt":"hello"}',
                    headers={
                        "Authorization": "Bearer phantom-token",
                        "Content-Type": "application/json",
                        "X-Custom": "preserved",
                    },
                )
                assert resp.status == 200
        finally:
            server.shutdown()

        assert captured["url"] == "http://upstream.test/v1/messages"
        assert captured["method"] == "POST"
        assert captured["headers"]["Authorization"] == "Bearer real-secret"
        assert captured["headers"]["Content-type"] == "application/json"
        assert captured["headers"]["X-custom"] == "preserved"
        assert captured["data"] == b'{"prompt":"hello"}'

    def test_host_header_stripped(self):
        """Host, Content-Length, Transfer-Encoding should not be forwarded."""
        captured = {}

        def mock_urlopen(req, **kwargs):
            captured["headers"] = dict(req.headers)
            return _FakeResponse()

        server, port = _start_proxy_server("tok", "http://upstream.test")
        try:
            with patch.object(urllib.request, "urlopen", side_effect=mock_urlopen):
                _http_request(port, "POST", "/v1/messages",
                    body=b"body",
                    headers={
                        "Transfer-Encoding": "chunked",
                    },
                )
        finally:
            server.shutdown()

        forwarded_keys_lower = {k.lower() for k in captured["headers"]}
        assert "host" not in forwarded_keys_lower
        assert "content-length" not in forwarded_keys_lower
        assert "transfer-encoding" not in forwarded_keys_lower

    def test_upstream_error_forwarded(self):
        """HTTP errors from upstream should be forwarded to the caller."""

        def mock_urlopen(req, **kwargs):
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests",
                http.client.HTTPMessage(), io.BytesIO(b"rate limited"),
            )

        server, port = _start_proxy_server("tok", "http://upstream.test")
        try:
            with patch.object(urllib.request, "urlopen", side_effect=mock_urlopen):
                resp, data = _http_request(port, "POST", "/v1/messages",
                    body=b"{}",
                )
                assert resp.status == 429
                assert b"rate limited" in data
        finally:
            server.shutdown()

    def test_upstream_unreachable_returns_502(self):
        """URLError (DNS/connection failure) should return 502."""

        def mock_urlopen(req, **kwargs):
            raise urllib.error.URLError("Connection refused")

        server, port = _start_proxy_server("tok", "http://upstream.test")
        try:
            with patch.object(urllib.request, "urlopen", side_effect=mock_urlopen):
                resp, data = _http_request(port, "POST", "/v1/messages",
                    body=b"{}",
                )
                assert resp.status == 502
                assert b"upstream unreachable" in data
        finally:
            server.shutdown()


# ---------------------------------------------------------------------------
# Token security
# ---------------------------------------------------------------------------

class TestTokenSecurity:
    def test_token_not_in_log_output(self, capsys):
        """The real token must never appear in log output."""

        def mock_urlopen(req, **kwargs):
            return _FakeResponse()

        real_token = "super-secret-real-token-12345"
        server, port = _start_proxy_server(real_token, "http://upstream.test")
        try:
            with patch.object(urllib.request, "urlopen", side_effect=mock_urlopen):
                _http_request(port, "POST", "/v1/messages", body=b"{}")
        finally:
            server.shutdown()

        captured = capsys.readouterr()
        assert real_token not in captured.out
        assert real_token not in captured.err
