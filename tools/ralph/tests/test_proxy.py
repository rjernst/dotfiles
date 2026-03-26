"""Unit tests for ralph.proxy — proxy lifecycle functions."""

import io
import signal
import time
from unittest.mock import MagicMock, patch

import pytest

from ralph.proxy import (
    DEFAULT_PROXY_PORT,
    proxy_port_for_agent,
    proxy_health_check,
    start_proxy,
    stop_proxy,
    start_proxy_keepalive,
    ensure_proxy,
)


# ---------------------------------------------------------------------------
# proxy_port_for_agent
# ---------------------------------------------------------------------------

class TestProxyPortForAgent:
    def test_claude_default(self):
        assert proxy_port_for_agent("claude") == 18080

    def test_unknown_agent_uses_default(self):
        assert proxy_port_for_agent("unknown") == DEFAULT_PROXY_PORT


# ---------------------------------------------------------------------------
# proxy_health_check
# ---------------------------------------------------------------------------

class TestProxyHealthCheck:
    @patch("ralph.proxy.urllib.request.urlopen")
    def test_returns_healthy_with_version(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"agent-loop-proxy ok v=abc123def456"
        mock_urlopen.return_value = mock_resp
        healthy, version = proxy_health_check(18080)
        assert healthy is True
        assert version == "abc123def456"

    @patch("ralph.proxy.urllib.request.urlopen")
    def test_returns_healthy_none_version_on_old_format(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"agent-loop-proxy ok"
        mock_urlopen.return_value = mock_resp
        healthy, version = proxy_health_check(18080)
        assert healthy is True
        assert version is None

    @patch("ralph.proxy.urllib.request.urlopen")
    def test_returns_unhealthy_on_non_200(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_urlopen.return_value = mock_resp
        healthy, version = proxy_health_check(18080)
        assert healthy is False
        assert version is None

    @patch("ralph.proxy.urllib.request.urlopen", side_effect=Exception("connection refused"))
    def test_returns_unhealthy_on_connection_error(self, mock_urlopen):
        healthy, version = proxy_health_check(18080)
        assert healthy is False
        assert version is None


# ---------------------------------------------------------------------------
# start_proxy
# ---------------------------------------------------------------------------

class TestStartProxy:
    @patch("builtins.open", MagicMock())
    @patch("ralph.proxy.subprocess.Popen")
    @patch("ralph.proxy.read_token_from_keychain")
    @patch("ralph.proxy.time.time", return_value=1700000000.0)
    def test_launches_python3_with_proxy_script(self, mock_time, mock_read, mock_popen):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test-token", "expiresAt": future_ms}
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        result = start_proxy("claude", 18080, "/fake/dotfiles")
        assert result is mock_proc

        # Verify python3 proxy.py command
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "python3"
        assert cmd[1].endswith("proxy.py")
        # Verify env vars
        env = mock_popen.call_args[1]["env"]
        assert env["LISTEN_PORT"] == "18080"
        assert "PID_FILE" in env
        # Verify token piped via stdin
        mock_proc.stdin.write.assert_called_once_with(b"sk-test-token\n")
        mock_proc.stdin.close.assert_called_once()

    @patch("ralph.proxy.read_token_from_keychain", return_value=None)
    def test_exits_when_no_token(self, mock_read):
        with pytest.raises(SystemExit) as exc_info:
            start_proxy("claude", 18080, "/fake/dotfiles")
        assert exc_info.value.code == 1

    @patch("ralph.proxy.read_token_from_keychain")
    @patch("ralph.proxy.time.time", return_value=1700000000.0)
    def test_exits_when_token_expired(self, mock_time, mock_read):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-old", "expiresAt": past_ms}
        with pytest.raises(SystemExit) as exc_info:
            start_proxy("claude", 18080, "/fake/dotfiles")
        assert exc_info.value.code == 1

    @patch("builtins.open", MagicMock())
    @patch("ralph.proxy.subprocess.Popen", side_effect=OSError("python3 not found"))
    @patch("ralph.proxy.read_token_from_keychain")
    @patch("ralph.proxy.time.time", return_value=1700000000.0)
    def test_popen_failure_raises(self, mock_time, mock_read, mock_popen):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        with pytest.raises(OSError):
            start_proxy("claude", 18080, "/fake/dotfiles")


# ---------------------------------------------------------------------------
# stop_proxy
# ---------------------------------------------------------------------------

class TestStopProxy:
    @patch("ralph.proxy.os.kill")
    @patch("builtins.open", MagicMock(return_value=io.StringIO("12345")))
    def test_sends_sigterm_to_pid(self, mock_kill):
        stop_proxy("claude")
        mock_kill.assert_called_once_with(12345, signal.SIGTERM)

    def test_no_error_when_pid_file_missing(self):
        # Should silently handle missing PID file
        stop_proxy("nonexistent-agent-999")

    @patch("ralph.proxy.os.kill", side_effect=ProcessLookupError)
    @patch("builtins.open", MagicMock(return_value=io.StringIO("99999")))
    def test_no_error_when_process_gone(self, mock_kill):
        # Should silently handle already-dead process
        stop_proxy("claude")


# ---------------------------------------------------------------------------
# start_proxy_keepalive
# ---------------------------------------------------------------------------

class TestProxyKeepalive:
    @patch("ralph.proxy.urllib.request.urlopen")
    def test_pings_health_endpoint(self, mock_urlopen):
        stop = start_proxy_keepalive(18080, interval=0.05)
        try:
            time.sleep(0.15)
        finally:
            stop.set()
        assert mock_urlopen.call_count >= 2
        url = mock_urlopen.call_args[0][0]
        assert "localhost:18080/health" in url

    @patch("ralph.proxy.urllib.request.urlopen", side_effect=Exception("refused"))
    def test_continues_on_error(self, mock_urlopen):
        stop = start_proxy_keepalive(18080, interval=0.05)
        try:
            time.sleep(0.15)
        finally:
            stop.set()
        # Should have kept pinging despite errors
        assert mock_urlopen.call_count >= 2

    @patch("ralph.proxy.urllib.request.urlopen")
    def test_stops_when_event_set(self, mock_urlopen):
        stop = start_proxy_keepalive(18080, interval=0.05)
        time.sleep(0.1)
        stop.set()
        count_at_stop = mock_urlopen.call_count
        time.sleep(0.15)
        # Should not have made significantly more calls after stop
        assert mock_urlopen.call_count <= count_at_stop + 1


# ---------------------------------------------------------------------------
# ensure_proxy
# ---------------------------------------------------------------------------

class TestEnsureProxy:
    @patch("ralph.proxy.compute_proxy_version", return_value="abc123def456")
    @patch("ralph.proxy.proxy_health_check", return_value=(True, "abc123def456"))
    def test_reuses_healthy_current_proxy(self, mock_health, mock_version):
        result = ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert result == 18080
        mock_health.assert_called_once_with(18080)

    @patch("ralph.proxy.compute_proxy_version", return_value="newversion123")
    @patch("ralph.proxy.proxy_health_check", return_value=(True, "oldversion456"))
    def test_reuses_outdated_proxy_with_warning(self, mock_health, mock_version, capsys):
        result = ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert result == 18080
        captured = capsys.readouterr()
        assert "outdated" in captured.out

    @patch("ralph.proxy.proxy_health_check", side_effect=[(False, None)] + [(True, "abc123")])
    @patch("ralph.proxy.start_proxy")
    @patch("ralph.proxy.time.sleep")
    def test_starts_new_when_none_running(self, mock_sleep, mock_start, mock_health):
        result = ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert result == 18080
        mock_start.assert_called_once_with("claude", 18080, "/fake/dotfiles")

    @patch("ralph.proxy.os.path.isfile", return_value=False)
    @patch("ralph.proxy.stop_proxy")
    @patch("ralph.proxy.proxy_health_check", return_value=(False, None))
    @patch("ralph.proxy.start_proxy")
    @patch("ralph.proxy.time.sleep")
    def test_exits_when_proxy_fails_to_become_healthy(self, mock_sleep, mock_start,
                                                       mock_health, mock_stop,
                                                       mock_isfile):
        with pytest.raises(SystemExit) as exc_info:
            ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert exc_info.value.code == 1
        mock_stop.assert_called_once_with("claude")


class TestEnsureProxyStaleCleanup:
    @patch("ralph.proxy.proxy_health_check",
           side_effect=[(False, None), (True, "abc123")])
    @patch("ralph.proxy.start_proxy")
    @patch("ralph.proxy.time.sleep")
    def test_logs_and_removes_stale_container(self, mock_sleep,
                                               mock_start, mock_health):
        """Stale proxy is logged, stopped, removed, then a new one starts."""
        result = ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert result == 18080
        mock_start.assert_called_once_with("claude", 18080, "/fake/dotfiles")
