"""Unit tests for the credential injection proxy script (docker/agent-loop/proxy/proxy.py).

Covers mode validation, health endpoint, and credential injection for all auth modes
including the gateway mode added in this step.
"""

import http.client
import io
import sys
import threading
from http.server import HTTPServer
from unittest.mock import patch

import pytest

# The proxy script lives outside the ralph package, so import it by path.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]
                       / "docker" / "agent-loop" / "proxy"))
import proxy as cred_proxy


# ---------------------------------------------------------------------------
# _VALID_MODES
# ---------------------------------------------------------------------------


class TestValidModes:
    def test_oauth_is_valid(self):
        assert "oauth" in cred_proxy._VALID_MODES

    def test_api_key_is_valid(self):
        assert "api_key" in cred_proxy._VALID_MODES

    def test_gateway_is_valid(self):
        assert "gateway" in cred_proxy._VALID_MODES

    def test_unknown_mode_not_valid(self):
        assert "unknown" not in cred_proxy._VALID_MODES


# ---------------------------------------------------------------------------
# read_mode_and_credential — gateway accepted without error
# ---------------------------------------------------------------------------


class TestReadModeAndCredential:
    def _call_with_stdin(self, mode, credential):
        """Call read_mode_and_credential with fake stdin."""
        fake_stdin = io.StringIO(f"{mode}\n{credential}\n")
        with patch("proxy.sys.stdin", fake_stdin):
            return cred_proxy.read_mode_and_credential()

    def test_gateway_mode_accepted(self):
        mode, cred = self._call_with_stdin("gateway", "my-bearer-token")
        assert mode == "gateway"
        assert cred == "my-bearer-token"

    def test_oauth_mode_accepted(self):
        mode, cred = self._call_with_stdin("oauth", "oauth-token")
        assert mode == "oauth"
        assert cred == "oauth-token"

    def test_api_key_mode_accepted(self):
        mode, cred = self._call_with_stdin("api_key", "sk-ant-key")
        assert mode == "api_key"
        assert cred == "sk-ant-key"

    def test_invalid_mode_exits(self):
        fake_stdin = io.StringIO("bad_mode\ncredential\n")
        with patch("proxy.sys.stdin", fake_stdin):
            with pytest.raises(SystemExit) as exc_info:
                cred_proxy.read_mode_and_credential()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# ProxyHandler — health endpoint reports gateway mode
# ---------------------------------------------------------------------------


class TestHealthEndpointGatewayMode:
    """The /health endpoint should report mode=gateway when started in gateway mode."""

    def setup_method(self):
        cred_proxy.ProxyHandler.real_credential = "my-gateway-token"
        cred_proxy.ProxyHandler.target = "https://gateway.example.com"
        cred_proxy.ProxyHandler.version_hash = "abc123def456"
        cred_proxy.ProxyHandler.AUTH_MODE = "gateway"
        cred_proxy.ProxyHandler.idle_shutdown = None

        self.server = HTTPServer(("127.0.0.1", 0), cred_proxy.ProxyHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def teardown_method(self):
        self.server.shutdown()
        self.server.server_close()

    def test_health_returns_200(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        assert resp.status == 200
        conn.close()

    def test_health_includes_mode_gateway(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        body = resp.read().decode()
        assert "mode=gateway" in body
        conn.close()

    def test_health_includes_version(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        body = resp.read().decode()
        assert "v=abc123def456" in body
        conn.close()


# ---------------------------------------------------------------------------
# ProxyHandler — Bearer injection for gateway mode
# ---------------------------------------------------------------------------


class TestGatewayBearerInjection:
    """Gateway mode must inject Authorization: Bearer <token>, not x-api-key."""

    def setup_method(self):
        cred_proxy.ProxyHandler.real_credential = "gw-secret-token"
        cred_proxy.ProxyHandler.version_hash = "test000000"
        cred_proxy.ProxyHandler.idle_shutdown = None

        # Start a simple upstream that echoes request headers.
        class HeaderEchoHandler(http.server.BaseHTTPRequestHandler):
            captured_headers = {}

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                HeaderEchoHandler.captured_headers = dict(self.headers)
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                pass

        self.echo_handler_class = HeaderEchoHandler
        self.upstream = HTTPServer(("127.0.0.1", 0), HeaderEchoHandler)
        self.upstream_port = self.upstream.server_address[1]
        upstream_thread = threading.Thread(target=self.upstream.serve_forever)
        upstream_thread.daemon = True
        upstream_thread.start()

        cred_proxy.ProxyHandler.target = f"http://127.0.0.1:{self.upstream_port}"
        cred_proxy.ProxyHandler.AUTH_MODE = "gateway"

        self.server = HTTPServer(("127.0.0.1", 0), cred_proxy.ProxyHandler)
        self.port = self.server.server_address[1]
        proxy_thread = threading.Thread(target=self.server.serve_forever)
        proxy_thread.daemon = True
        proxy_thread.start()

    def teardown_method(self):
        self.server.shutdown()
        self.server.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()

    def test_gateway_injects_bearer_authorization(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", "/v1/messages",
                     body=b"{}",
                     headers={"Content-Length": "2", "Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        conn.close()

        headers = self.echo_handler_class.captured_headers
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer gw-secret-token"

    def test_gateway_strips_client_authorization_header(self):
        """Client-supplied Authorization header must be replaced, not forwarded."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", "/v1/messages",
                     body=b"{}",
                     headers={
                         "Content-Length": "2",
                         "Content-Type": "application/json",
                         "Authorization": "Bearer client-fake-token",
                     })
        resp = conn.getresponse()
        resp.read()
        conn.close()

        headers = self.echo_handler_class.captured_headers
        assert headers.get("Authorization") == "Bearer gw-secret-token"

    def test_gateway_does_not_inject_x_api_key(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", "/v1/messages",
                     body=b"{}",
                     headers={"Content-Length": "2", "Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        conn.close()

        headers = self.echo_handler_class.captured_headers
        assert "x-api-key" not in {k.lower(): v for k, v in headers.items()}


# ---------------------------------------------------------------------------
# ProxyHandler — Bearer injection for oauth (unchanged)
# ---------------------------------------------------------------------------


class TestOauthBearerInjectionUnchanged:
    """Ensure oauth mode still works identically after gateway additions."""

    def setup_method(self):
        cred_proxy.ProxyHandler.real_credential = "oauth-secret"
        cred_proxy.ProxyHandler.version_hash = "test000000"
        cred_proxy.ProxyHandler.idle_shutdown = None

        class HeaderEchoHandler(http.server.BaseHTTPRequestHandler):
            captured_headers = {}

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                HeaderEchoHandler.captured_headers = dict(self.headers)
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                pass

        self.echo_handler_class = HeaderEchoHandler
        self.upstream = HTTPServer(("127.0.0.1", 0), HeaderEchoHandler)
        self.upstream_port = self.upstream.server_address[1]
        upstream_thread = threading.Thread(target=self.upstream.serve_forever)
        upstream_thread.daemon = True
        upstream_thread.start()

        cred_proxy.ProxyHandler.target = f"http://127.0.0.1:{self.upstream_port}"
        cred_proxy.ProxyHandler.AUTH_MODE = "oauth"

        self.server = HTTPServer(("127.0.0.1", 0), cred_proxy.ProxyHandler)
        self.port = self.server.server_address[1]
        proxy_thread = threading.Thread(target=self.server.serve_forever)
        proxy_thread.daemon = True
        proxy_thread.start()

    def teardown_method(self):
        self.server.shutdown()
        self.server.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()

    def test_oauth_still_injects_bearer(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", "/v1/messages",
                     body=b"{}",
                     headers={"Content-Length": "2", "Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        conn.close()

        headers = self.echo_handler_class.captured_headers
        assert headers.get("Authorization") == "Bearer oauth-secret"


# ---------------------------------------------------------------------------
# ProxyHandler — x-api-key injection for api_key (unchanged)
# ---------------------------------------------------------------------------


class TestApiKeyInjectionUnchanged:
    """Ensure api_key mode still uses x-api-key header after gateway additions."""

    def setup_method(self):
        cred_proxy.ProxyHandler.real_credential = "sk-ant-api-key"
        cred_proxy.ProxyHandler.version_hash = "test000000"
        cred_proxy.ProxyHandler.idle_shutdown = None

        class HeaderEchoHandler(http.server.BaseHTTPRequestHandler):
            captured_headers = {}

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                HeaderEchoHandler.captured_headers = dict(self.headers)
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                pass

        self.echo_handler_class = HeaderEchoHandler
        self.upstream = HTTPServer(("127.0.0.1", 0), HeaderEchoHandler)
        self.upstream_port = self.upstream.server_address[1]
        upstream_thread = threading.Thread(target=self.upstream.serve_forever)
        upstream_thread.daemon = True
        upstream_thread.start()

        cred_proxy.ProxyHandler.target = f"http://127.0.0.1:{self.upstream_port}"
        cred_proxy.ProxyHandler.AUTH_MODE = "api_key"

        self.server = HTTPServer(("127.0.0.1", 0), cred_proxy.ProxyHandler)
        self.port = self.server.server_address[1]
        proxy_thread = threading.Thread(target=self.server.serve_forever)
        proxy_thread.daemon = True
        proxy_thread.start()

    def teardown_method(self):
        self.server.shutdown()
        self.server.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()

    def test_api_key_injects_x_api_key(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", "/v1/messages",
                     body=b"{}",
                     headers={"Content-Length": "2", "Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        conn.close()

        headers = {k.lower(): v for k, v in self.echo_handler_class.captured_headers.items()}
        assert headers.get("x-api-key") == "sk-ant-api-key"

    def test_api_key_does_not_inject_bearer(self):
        """api_key mode must not inject Authorization: Bearer."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", "/v1/messages",
                     body=b"{}",
                     headers={"Content-Length": "2", "Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        conn.close()

        headers = {k.lower(): v for k, v in self.echo_handler_class.captured_headers.items()}
        assert "authorization" not in headers
