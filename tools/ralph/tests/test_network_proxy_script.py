"""Unit tests for the network proxy — allowlist, health, CONNECT, and HTTP forwarding.

These tests cover the proxy's filtering logic without requiring real upstream servers.
"""

import http.client
import io
import socket
import sys
import threading
import time
from http.server import HTTPServer
from unittest.mock import MagicMock, patch

import pytest

# The proxy script lives outside the ralph package, so import it by path.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]
                       / "docker" / "agent-loop" / "proxy"))
import network_proxy as np
import proxy_base


# ---------------------------------------------------------------------------
# parse_allowed_hosts
# ---------------------------------------------------------------------------

class TestParseAllowedHosts:
    def test_comma_separated(self):
        result = np.parse_allowed_hosts("example.com,api.github.com")
        assert result == frozenset({"example.com", "api.github.com"})

    def test_strips_whitespace(self):
        result = np.parse_allowed_hosts(" example.com , api.github.com ")
        assert result == frozenset({"example.com", "api.github.com"})

    def test_empty_string(self):
        result = np.parse_allowed_hosts("")
        assert result == frozenset()

    def test_none(self):
        result = np.parse_allowed_hosts(None)
        assert result == frozenset()

    def test_single_host(self):
        result = np.parse_allowed_hosts("example.com")
        assert result == frozenset({"example.com"})

    def test_lowercased(self):
        result = np.parse_allowed_hosts("Example.COM")
        assert result == frozenset({"example.com"})

    def test_trailing_comma_ignored(self):
        result = np.parse_allowed_hosts("example.com,")
        assert result == frozenset({"example.com"})


# ---------------------------------------------------------------------------
# is_host_allowed
# ---------------------------------------------------------------------------

class TestIsHostAllowed:
    def test_allowed_host(self):
        hosts = frozenset({"example.com", "api.github.com"})
        assert np.is_host_allowed("example.com", hosts) is True

    def test_denied_host(self):
        hosts = frozenset({"example.com"})
        assert np.is_host_allowed("evil.com", hosts) is False

    def test_case_insensitive(self):
        hosts = frozenset({"example.com"})
        assert np.is_host_allowed("EXAMPLE.COM", hosts) is True

    def test_empty_allowlist_denies_all(self):
        assert np.is_host_allowed("example.com", frozenset()) is False


# ---------------------------------------------------------------------------
# extract_host_from_authority
# ---------------------------------------------------------------------------

class TestExtractHostFromAuthority:
    def test_host_port(self):
        assert np.extract_host_from_authority("example.com:443") == "example.com"

    def test_host_only(self):
        assert np.extract_host_from_authority("example.com") == "example.com"

    def test_ipv6_with_port(self):
        assert np.extract_host_from_authority("[::1]:443") == "::1"

    def test_ipv6_without_port(self):
        assert np.extract_host_from_authority("[::1]") == "::1"

    def test_lowercased(self):
        assert np.extract_host_from_authority("Example.COM:80") == "example.com"


# ---------------------------------------------------------------------------
# Handler — health endpoint
# ---------------------------------------------------------------------------

class TestHandlerHealth:
    """Test the /health endpoint returns expected response."""

    def setup_method(self):
        np.NetworkProxyHandler.allowed_hosts = frozenset({"example.com", "api.github.com"})
        np.NetworkProxyHandler.idle_shutdown = None
        np.NetworkProxyHandler.version_hash = "abc123def456"

        self.server = HTTPServer(("127.0.0.1", 0), np.NetworkProxyHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def teardown_method(self):
        self.server.shutdown()
        self.server.server_close()

    def test_health_returns_200_with_hosts_and_version(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 200
        assert "network-proxy ok" in body
        assert "v=abc123def456" in body
        # Hosts should be sorted.
        assert "hosts=api.github.com,example.com" in body
        conn.close()

    def test_health_with_empty_hosts(self):
        np.NetworkProxyHandler.allowed_hosts = frozenset()
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 200
        assert "hosts=" in body
        assert "v=abc123def456" in body
        conn.close()


# ---------------------------------------------------------------------------
# Handler — deny responses
# ---------------------------------------------------------------------------

class TestHandlerDeny:
    """Test that blocked requests return 403."""

    def setup_method(self):
        np.NetworkProxyHandler.allowed_hosts = frozenset({"allowed.com"})
        np.NetworkProxyHandler.idle_shutdown = None
        np.NetworkProxyHandler.version_hash = "test000000"

        self.server = HTTPServer(("127.0.0.1", 0), np.NetworkProxyHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def teardown_method(self):
        self.server.shutdown()
        self.server.server_close()

    def test_connect_denied_host_returns_403(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("CONNECT", "evil.com:443")
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 403
        assert "blocked" in body
        assert "evil.com" in body
        conn.close()

    def test_http_denied_host_returns_403(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "http://evil.com/path", headers={"Host": "evil.com"})
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 403
        assert "blocked" in body
        conn.close()

    def test_empty_allowlist_denies_connect(self):
        np.NetworkProxyHandler.allowed_hosts = frozenset()
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("CONNECT", "anything.com:443")
        resp = conn.getresponse()
        assert resp.status == 403
        resp.read()
        conn.close()

    def test_empty_allowlist_denies_http(self):
        np.NetworkProxyHandler.allowed_hosts = frozenset()
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "http://anything.com/", headers={"Host": "anything.com"})
        resp = conn.getresponse()
        assert resp.status == 403
        resp.read()
        conn.close()

    def test_post_with_body_denied(self):
        """POST with a request body to a denied host should return 403."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", "http://evil.com/upload",
                      body=b"some data payload",
                      headers={"Host": "evil.com",
                               "Content-Length": "17",
                               "Content-Type": "application/octet-stream"})
        resp = conn.getresponse()
        assert resp.status == 403
        body = resp.read().decode()
        assert "blocked" in body
        conn.close()


# ---------------------------------------------------------------------------
# Handler — CONNECT tunnel
# ---------------------------------------------------------------------------

class TestHandlerConnect:
    """Test CONNECT handling for allowed hosts."""

    def setup_method(self):
        np.NetworkProxyHandler.allowed_hosts = frozenset({"127.0.0.1"})
        np.NetworkProxyHandler.idle_shutdown = None
        np.NetworkProxyHandler.version_hash = "test000000"

        self.server = HTTPServer(("127.0.0.1", 0), np.NetworkProxyHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def teardown_method(self):
        self.server.shutdown()
        self.server.server_close()

    def test_connect_to_allowed_host_returns_200(self):
        """CONNECT to an allowed host that is actually listening should succeed."""
        # Start a simple echo server as the upstream.
        echo_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        echo_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        echo_server.bind(("127.0.0.1", 0))
        echo_port = echo_server.getsockname()[1]
        echo_server.listen(1)

        def echo_handler():
            client, _ = echo_server.accept()
            data = client.recv(1024)
            client.sendall(data)
            client.close()
            echo_server.close()

        echo_thread = threading.Thread(target=echo_handler)
        echo_thread.daemon = True
        echo_thread.start()

        # Use raw socket for CONNECT since http.client doesn't preserve
        # the socket after getresponse() on a CONNECT request.
        sock = socket.create_connection(("127.0.0.1", self.port))
        sock.sendall(
            f"CONNECT 127.0.0.1:{echo_port} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{echo_port}\r\n\r\n".encode()
        )

        # Read the HTTP response line.
        response = b""
        while b"\r\n\r\n" not in response:
            response += sock.recv(4096)
        assert b"200" in response

        # Send data through the tunnel and verify echo.
        sock.sendall(b"hello tunnel")
        data = sock.recv(1024)
        assert data == b"hello tunnel"
        sock.close()
        echo_thread.join(timeout=2)

    def test_connect_upstream_unreachable_returns_502(self):
        """CONNECT to an allowed host on a closed port should return 502."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        # Use a port that is almost certainly not listening.
        conn.request("CONNECT", "127.0.0.1:19999")
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 502
        assert "upstream error" in body
        conn.close()

    def test_connect_malformed_port_returns_400(self):
        """CONNECT with non-numeric port should return 400."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("CONNECT", "127.0.0.1:notaport")
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 400
        assert "bad CONNECT authority" in body
        conn.close()


# ---------------------------------------------------------------------------
# Handler — plain HTTP forwarding
# ---------------------------------------------------------------------------

class TestHandlerHTTPForward:
    """Test plain HTTP forwarding for allowed hosts."""

    def setup_method(self):
        np.NetworkProxyHandler.allowed_hosts = frozenset({"127.0.0.1"})
        np.NetworkProxyHandler.idle_shutdown = None
        np.NetworkProxyHandler.version_hash = "test000000"

        self.server = HTTPServer(("127.0.0.1", 0), np.NetworkProxyHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

        # Start a simple HTTP server as the upstream.
        class UpstreamHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"upstream response"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                req_body = self.rfile.read(length) if length else b""
                body = f"echo:{req_body.decode()}".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                pass

        self.upstream = HTTPServer(("127.0.0.1", 0), UpstreamHandler)
        self.upstream_port = self.upstream.server_address[1]
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever)
        self.upstream_thread.daemon = True
        self.upstream_thread.start()

    def teardown_method(self):
        self.server.shutdown()
        self.server.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()

    def test_get_allowed_host_proxied(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", f"http://127.0.0.1:{self.upstream_port}/test",
                      headers={"Host": "127.0.0.1"})
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 200
        assert body == "upstream response"
        conn.close()

    def test_post_allowed_host_proxied(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", f"http://127.0.0.1:{self.upstream_port}/submit",
                      body=b"data", headers={
                          "Host": "127.0.0.1",
                          "Content-Length": "4",
                          "Content-Type": "text/plain",
                      })
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 200
        assert body == "echo:data"
        conn.close()

    def test_get_with_query_string_preserved(self):
        """Query strings should be forwarded to the upstream."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", f"http://127.0.0.1:{self.upstream_port}/test?key=value&foo=bar",
                      headers={"Host": "127.0.0.1"})
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 200
        assert body == "upstream response"
        conn.close()

    def test_http_upstream_unreachable_returns_502(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "http://127.0.0.1:19999/nope",
                      headers={"Host": "127.0.0.1"})
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 502
        assert "upstream error" in body
        conn.close()


# ---------------------------------------------------------------------------
# Handler — Host header fallback
# ---------------------------------------------------------------------------

class TestHandlerHostHeaderFallback:
    """Test that Host header is used when URL has no hostname."""

    def setup_method(self):
        np.NetworkProxyHandler.allowed_hosts = frozenset({"denied.com"})
        np.NetworkProxyHandler.idle_shutdown = None
        np.NetworkProxyHandler.version_hash = "test000000"

        self.server = HTTPServer(("127.0.0.1", 0), np.NetworkProxyHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def teardown_method(self):
        self.server.shutdown()
        self.server.server_close()

    def test_relative_url_uses_host_header(self):
        """When the URL is relative (no scheme/host), fall back to Host header."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/relative/path", headers={"Host": "notallowed.com"})
        resp = conn.getresponse()
        # Should be denied because notallowed.com is not in the allowlist.
        assert resp.status == 403
        resp.read()
        conn.close()

    def test_no_host_at_all_returns_403(self):
        """Request with no hostname in URL and no Host header gets denied."""
        # Use raw socket since http.client always adds a Host header.
        sock = socket.create_connection(("127.0.0.1", self.port))
        sock.sendall(b"GET /path HTTP/1.1\r\n\r\n")
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        assert b"403" in response
        sock.close()


# ---------------------------------------------------------------------------
# IdleShutdown
# ---------------------------------------------------------------------------

class TestIdleShutdown:
    def test_shuts_down_after_timeout(self):
        server = MagicMock()
        idle = proxy_base.IdleShutdown(0.1, server)
        idle.reset()
        time.sleep(0.3)
        server.shutdown.assert_called_once()

    def test_reset_extends_timeout(self):
        server = MagicMock()
        idle = proxy_base.IdleShutdown(0.2, server)
        idle.reset()
        time.sleep(0.1)
        idle.reset()
        time.sleep(0.1)
        server.shutdown.assert_not_called()
        time.sleep(0.2)
        server.shutdown.assert_called_once()


# ---------------------------------------------------------------------------
# Port and wildcard handling
# ---------------------------------------------------------------------------

class TestPortHandling:
    """Test CONNECT with various host:port formats."""

    def setup_method(self):
        np.NetworkProxyHandler.allowed_hosts = frozenset({"example.com"})
        np.NetworkProxyHandler.idle_shutdown = None
        np.NetworkProxyHandler.version_hash = "test000000"

        self.server = HTTPServer(("127.0.0.1", 0), np.NetworkProxyHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def teardown_method(self):
        self.server.shutdown()
        self.server.server_close()

    def test_connect_port_443_extracts_host(self):
        """CONNECT example.com:443 should check 'example.com' against allowlist."""
        # The host resolves but connection will likely fail — we just check
        # that the allowlist check passes (no 403).
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("CONNECT", "example.com:443")
        resp = conn.getresponse()
        # Should not be 403 (allowlist passed), but likely 502 (network blocked).
        assert resp.status != 403
        resp.read()
        conn.close()

    def test_connect_port_8443_extracts_host(self):
        """CONNECT example.com:8443 should check 'example.com'."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("CONNECT", "example.com:8443")
        resp = conn.getresponse()
        assert resp.status != 403
        resp.read()
        conn.close()
