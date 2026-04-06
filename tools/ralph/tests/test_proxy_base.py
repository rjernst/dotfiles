"""Unit tests for proxy_base — shared proxy infrastructure."""

import sys
import time
from unittest.mock import MagicMock

import pytest

# The proxy_base module lives outside the ralph package.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]
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
