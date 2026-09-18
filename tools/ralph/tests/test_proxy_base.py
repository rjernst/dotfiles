"""Unit tests for proxy_base — shared proxy infrastructure."""

import os
import pathlib
import shutil
import socket
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler
from unittest.mock import MagicMock

import pytest

# The proxy_base module lives outside the ralph package.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]
                       / "docker" / "agent-loop" / "proxy"))
import proxy_base


class TestComputeVersionHash:
    def test_returns_12_char_hex(self, tmp_path):
        script = tmp_path / "test.py"
        script.write_text("print('hello')")
        v = proxy_base.compute_version_hash(str(script))
        assert len(v) == 12
        assert all(c in "0123456789abcdef" for c in v)

    def test_same_content_same_hash(self, tmp_path):
        s1 = tmp_path / "a.py"
        s2 = tmp_path / "b.py"
        s1.write_text("same content")
        s2.write_text("same content")
        assert proxy_base.compute_version_hash(str(s1)) == \
               proxy_base.compute_version_hash(str(s2))

    def test_different_content_different_hash(self, tmp_path):
        s1 = tmp_path / "a.py"
        s2 = tmp_path / "b.py"
        s1.write_text("content a")
        s2.write_text("content b")
        assert proxy_base.compute_version_hash(str(s1)) != \
               proxy_base.compute_version_hash(str(s2))


class TestIdleShutdown:
    def test_shuts_down_after_timeout(self):
        server = MagicMock()
        idle = proxy_base.IdleShutdown(0.1, server, name="test-proxy")
        idle.reset()
        time.sleep(0.3)
        server.shutdown.assert_called_once()

    def test_reset_extends_timeout(self):
        server = MagicMock()
        idle = proxy_base.IdleShutdown(0.2, server, name="test-proxy")
        idle.reset()
        time.sleep(0.1)
        idle.reset()
        time.sleep(0.1)
        server.shutdown.assert_not_called()
        time.sleep(0.2)
        server.shutdown.assert_called_once()

    def test_uses_name_in_shutdown_message(self, capsys):
        server = MagicMock()
        idle = proxy_base.IdleShutdown(0.05, server, name="my-proxy")
        idle.reset()
        time.sleep(0.2)
        captured = capsys.readouterr()
        assert "my-proxy" in captured.err


class TestDualStackHTTPServer:
    def test_class_exists(self):
        assert hasattr(proxy_base, 'DualStackHTTPServer')


class TestMakeServer:
    """make_server picks the socket family from the listen address."""

    def _serve(self, listen_addr):
        """Bind a server on an ephemeral port; caller closes it."""
        handler = type("H", (BaseHTTPRequestHandler,), {})
        return proxy_base.make_server(listen_addr, 0, handler)

    def test_wildcard_is_dual_stack(self):
        server = self._serve("::")
        try:
            assert type(server) is proxy_base.DualStackHTTPServer
            assert server.socket.family == socket.AF_INET6
            assert server.socket.getsockopt(
                socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) == 0
        finally:
            server.server_close()

    def test_default_listen_addr_is_dual_stack(self):
        """DEFAULT_LISTEN_ADDR is the dual-stack wildcard."""
        assert proxy_base.DEFAULT_LISTEN_ADDR == "::"

    def test_ipv6_literal_is_v6only(self):
        server = self._serve("::1")
        try:
            assert type(server) is proxy_base.IPv6HTTPServer
            assert server.socket.family == socket.AF_INET6
            assert server.socket.getsockopt(
                socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) == 1
        finally:
            server.server_close()

    def test_ipv4_literal_uses_af_inet(self):
        server = self._serve("127.0.0.1")
        try:
            assert type(server) is proxy_base.IPv4HTTPServer
            assert server.socket.family == socket.AF_INET
        finally:
            server.server_close()

    def test_ipv4_wildcard_uses_af_inet(self):
        server = self._serve("0.0.0.0")
        try:
            assert type(server) is proxy_base.IPv4HTTPServer
            assert server.socket.family == socket.AF_INET
        finally:
            server.server_close()

    def test_binds_requested_address(self):
        server = self._serve("127.0.0.1")
        try:
            assert server.server_address[0] == "127.0.0.1"
            assert server.server_address[1] != 0
        finally:
            server.server_close()


@pytest.fixture
def sock_dir():
    """A short directory for socket paths.

    Not tmp_path: macOS caps an AF_UNIX path at ~104 bytes, and pytest's
    own temp paths are longer than that.
    """
    path = tempfile.mkdtemp(prefix="/tmp/ralph-sock-")
    try:
        yield pathlib.Path(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class TestMakeUnixServer:
    """make_unix_server binds an AF_UNIX socket with a private mode."""

    def _serve(self, socket_path):
        handler = type("H", (BaseHTTPRequestHandler,), {})
        return proxy_base.make_unix_server(socket_path, handler)

    def test_binds_af_unix_at_the_path(self, sock_dir):
        path = str(sock_dir / "a.sock")
        server = self._serve(path)
        try:
            assert type(server) is proxy_base.UnixStreamHTTPServer
            assert server.socket.family == socket.AF_UNIX
            assert server.server_address == path
        finally:
            server.server_close()

    def test_mode_is_0600_under_permissive_umask(self, sock_dir):
        path = str(sock_dir / "b.sock")
        old_umask = os.umask(0o000)
        try:
            server = self._serve(path)
        finally:
            os.umask(old_umask)
        try:
            assert os.stat(path).st_mode & 0o777 == 0o600
        finally:
            server.server_close()

    def test_restores_umask(self, sock_dir):
        path = str(sock_dir / "c.sock")
        before = os.umask(0o022)
        os.umask(before)
        server = self._serve(path)
        try:
            after = os.umask(0o022)
            os.umask(after)
            assert after == before
        finally:
            server.server_close()

    def test_replaces_stale_socket_file(self, sock_dir):
        path = str(sock_dir / "d.sock")
        with open(path, "w") as f:
            f.write("stale")
        server = self._serve(path)
        try:
            assert server.socket.family == socket.AF_UNIX
            assert os.stat(path).st_mode & 0o777 == 0o600
        finally:
            server.server_close()

    def test_server_close_unlinks_socket(self, sock_dir):
        path = str(sock_dir / "e.sock")
        server = self._serve(path)
        server.server_close()
        assert not os.path.exists(path)

    def test_server_close_keeps_a_replacement_socket(self, sock_dir):
        """A later proxy that took over the path must survive our shutdown."""
        path = str(sock_dir / "f.sock")
        first = self._serve(path)
        second = self._serve(path)  # unlinks first's socket, binds its own
        try:
            first.server_close()
            assert os.path.exists(path)
            assert os.stat(path).st_ino == second._socket_ident[1]
        finally:
            second.server_close()


class TestFormatExtra:
    def test_empty_handler(self):
        handler = type("H", (), {})
        assert proxy_base._format_extra(handler) == ""

    def test_with_docker_socket(self):
        handler = type("H", (), {"docker_socket": "/var/run/docker.sock"})
        result = proxy_base._format_extra(handler)
        assert "socket=/var/run/docker.sock" in result

    def test_with_allowed_hosts(self):
        handler = type("H", (), {
            "allowed_hosts": frozenset({"b.com", "a.com"})
        })
        result = proxy_base._format_extra(handler)
        assert "hosts=a.com,b.com" in result

    def test_with_empty_hosts(self):
        handler = type("H", (), {"allowed_hosts": frozenset()})
        result = proxy_base._format_extra(handler)
        assert "hosts=(none)" in result
