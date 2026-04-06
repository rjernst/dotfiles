"""Unit tests for the Docker socket proxy — allowlist, health, and handler logic.

These tests cover the proxy's filtering logic without requiring a real Docker socket.
"""

import http.client
import io
import json
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
import docker_socket_proxy as dsp
import proxy_base


# ---------------------------------------------------------------------------
# is_allowed — allowlist matching
# ---------------------------------------------------------------------------

class TestIsAllowed:
    """Verify the allowlist permits expected Docker API paths and blocks others."""

    # --- allowed paths ---

    def test_get_ping(self):
        assert dsp.is_allowed("GET", "/_ping") is True

    def test_head_ping(self):
        assert dsp.is_allowed("HEAD", "/_ping") is True

    def test_get_version(self):
        assert dsp.is_allowed("GET", "/version") is True

    def test_get_info(self):
        assert dsp.is_allowed("GET", "/info") is True

    def test_post_build(self):
        assert dsp.is_allowed("POST", "/build") is True

    def test_post_build_with_query(self):
        assert dsp.is_allowed("POST", "/build?t=myimage:latest&nocache=1") is True

    def test_post_images_create(self):
        assert dsp.is_allowed("POST", "/images/create") is True

    def test_post_images_create_with_query(self):
        assert dsp.is_allowed("POST", "/images/create?fromImage=alpine&tag=latest") is True

    def test_get_images_json(self):
        assert dsp.is_allowed("GET", "/images/json") is True

    def test_get_images_inspect(self):
        assert dsp.is_allowed("GET", "/images/sha256:abc123/json") is True

    # --- allowed with version prefix ---

    def test_versioned_ping(self):
        assert dsp.is_allowed("GET", "/v1.45/_ping") is True

    def test_versioned_build(self):
        assert dsp.is_allowed("POST", "/v1.45/build") is True

    def test_versioned_images_create(self):
        assert dsp.is_allowed("POST", "/v1.43/images/create?fromImage=ubuntu") is True

    def test_versioned_images_json(self):
        assert dsp.is_allowed("GET", "/v1.45/images/json") is True

    # --- denied paths ---

    def test_post_containers_create(self):
        assert dsp.is_allowed("POST", "/containers/create") is False

    def test_post_exec(self):
        assert dsp.is_allowed("POST", "/containers/abc/exec") is False

    def test_delete_containers(self):
        assert dsp.is_allowed("DELETE", "/containers/abc") is False

    def test_post_volumes_create(self):
        assert dsp.is_allowed("POST", "/volumes/create") is False

    def test_post_networks_create(self):
        assert dsp.is_allowed("POST", "/networks/create") is False

    def test_get_containers_json(self):
        assert dsp.is_allowed("GET", "/containers/json") is False

    def test_versioned_containers_create(self):
        assert dsp.is_allowed("POST", "/v1.45/containers/create") is False

    def test_put_not_allowed(self):
        assert dsp.is_allowed("PUT", "/images/json") is False

    def test_delete_images(self):
        assert dsp.is_allowed("DELETE", "/images/abc123") is False

    def test_post_ping(self):
        """POST to /_ping is not allowed — only GET and HEAD."""
        assert dsp.is_allowed("POST", "/_ping") is False

    def test_build_prefix_not_matched(self):
        """POST /buildx should NOT be allowed — /build is anchored."""
        assert dsp.is_allowed("POST", "/buildx") is False

    def test_images_create_prefix_not_matched(self):
        """POST /images/create-foo should NOT be allowed — anchored."""
        assert dsp.is_allowed("POST", "/images/create-foo") is False

    def test_path_traversal_blocked(self):
        """Path traversal attempt should not bypass allowlist."""
        assert dsp.is_allowed("POST", "/build/../containers/create") is False

    def test_versioned_path_traversal_blocked(self):
        assert dsp.is_allowed("POST", "/v1.45/build/../containers/create") is False


# ---------------------------------------------------------------------------
# strip_version_prefix
# ---------------------------------------------------------------------------

class TestStripVersionPrefix:
    def test_strips_v1_45(self):
        assert dsp.strip_version_prefix("/v1.45/build") == "/build"

    def test_strips_v1_43(self):
        assert dsp.strip_version_prefix("/v1.43/images/json") == "/images/json"

    def test_no_prefix_unchanged(self):
        assert dsp.strip_version_prefix("/build") == "/build"

    def test_double_prefix_strips_first(self):
        """Only the first version prefix is stripped."""
        assert dsp.strip_version_prefix("/v1.45/v1.43/build") == "/v1.43/build"


# ---------------------------------------------------------------------------
# UnixHTTPConnection
# ---------------------------------------------------------------------------

class TestUnixHTTPConnection:
    def test_creates_unix_socket_on_connect(self):
        conn = dsp.UnixHTTPConnection("/var/run/docker.sock", timeout=5)
        assert conn._socket_path == "/var/run/docker.sock"

    @patch("socket.socket")
    def test_connect_uses_af_unix(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        conn = dsp.UnixHTTPConnection("/tmp/test.sock", timeout=10)
        conn.connect()

        mock_socket_cls.assert_called_once_with(socket.AF_UNIX, socket.SOCK_STREAM)
        mock_sock.settimeout.assert_called_once_with(10)
        mock_sock.connect.assert_called_once_with("/tmp/test.sock")


# ---------------------------------------------------------------------------
# DockerSocketProxyHandler — health + deny + proxy
# ---------------------------------------------------------------------------

class TestHandlerHealthEndpoint:
    """Test the /health endpoint returns expected response."""

    def setup_method(self):
        """Start a proxy server on a random port with a fake docker socket."""
        dsp.DockerSocketProxyHandler.docker_socket = "/nonexistent.sock"
        dsp.DockerSocketProxyHandler.idle_shutdown = None
        dsp.DockerSocketProxyHandler.version_hash = "test123abc00"

        self.server = HTTPServer(("127.0.0.1", 0), dsp.DockerSocketProxyHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def teardown_method(self):
        self.server.shutdown()
        self.server.server_close()

    def test_health_returns_200_with_version(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 200
        assert body == "docker-socket-proxy ok v=test123abc00"
        conn.close()


class TestHandlerDenyEndpoint:
    """Test that blocked endpoints return 403."""

    def setup_method(self):
        dsp.DockerSocketProxyHandler.docker_socket = "/nonexistent.sock"
        dsp.DockerSocketProxyHandler.idle_shutdown = None
        dsp.DockerSocketProxyHandler.version_hash = "test123abc00"

        self.server = HTTPServer(("127.0.0.1", 0), dsp.DockerSocketProxyHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def teardown_method(self):
        self.server.shutdown()
        self.server.server_close()

    def test_post_containers_create_returns_403(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", "/v1.45/containers/create")
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 403
        assert "blocked" in body
        assert "/v1.45/containers/create" in body
        conn.close()

    def test_delete_returns_403(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("DELETE", "/containers/abc123")
        resp = conn.getresponse()
        assert resp.status == 403
        resp.read()
        conn.close()

    def test_exec_returns_403(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", "/v1.45/containers/abc/exec")
        resp = conn.getresponse()
        assert resp.status == 403
        resp.read()
        conn.close()


class TestHandlerProxyUpstreamError:
    """Test that unreachable Docker socket returns 502."""

    def setup_method(self):
        dsp.DockerSocketProxyHandler.docker_socket = "/tmp/nonexistent-docker.sock"
        dsp.DockerSocketProxyHandler.idle_shutdown = None
        dsp.DockerSocketProxyHandler.version_hash = "test123abc00"

        self.server = HTTPServer(("127.0.0.1", 0), dsp.DockerSocketProxyHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def teardown_method(self):
        self.server.shutdown()
        self.server.server_close()

    def test_allowed_path_with_no_socket_returns_502(self):
        """An allowed request with no Docker socket should return 502."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/_ping")
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 502
        assert "upstream error" in body
        conn.close()


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
        # Should not have shut down yet — timer was reset.
        server.shutdown.assert_not_called()
        time.sleep(0.2)
        server.shutdown.assert_called_once()
