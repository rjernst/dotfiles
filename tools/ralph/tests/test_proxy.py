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
    def test_returns_healthy_with_version_and_mode(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"agent-loop-proxy ok v=abc123def456 mode=oauth"
        mock_urlopen.return_value = mock_resp
        healthy, version, mode = proxy_health_check(18080)
        assert healthy is True
        assert version == "abc123def456"
        assert mode == "oauth"

    @patch("ralph.proxy.urllib.request.urlopen")
    def test_parses_api_key_mode(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"agent-loop-proxy ok v=abc123 mode=api_key"
        mock_urlopen.return_value = mock_resp
        healthy, version, mode = proxy_health_check(18080)
        assert healthy is True
        assert version == "abc123"
        assert mode == "api_key"

    @patch("ralph.proxy.urllib.request.urlopen")
    def test_returns_none_mode_for_old_format(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"agent-loop-proxy ok v=abc123def456"
        mock_urlopen.return_value = mock_resp
        healthy, version, mode = proxy_health_check(18080)
        assert healthy is True
        assert version == "abc123def456"
        assert mode is None

    @patch("ralph.proxy.urllib.request.urlopen")
    def test_returns_healthy_none_version_on_old_format(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"agent-loop-proxy ok"
        mock_urlopen.return_value = mock_resp
        healthy, version, mode = proxy_health_check(18080)
        assert healthy is True
        assert version is None
        assert mode is None

    @patch("ralph.proxy.urllib.request.urlopen")
    def test_returns_unhealthy_on_non_200(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_urlopen.return_value = mock_resp
        healthy, version, mode = proxy_health_check(18080)
        assert healthy is False
        assert version is None
        assert mode is None

    @patch("ralph.proxy.urllib.request.urlopen", side_effect=Exception("connection refused"))
    def test_returns_unhealthy_on_connection_error(self, mock_urlopen):
        healthy, version, mode = proxy_health_check(18080)
        assert healthy is False
        assert version is None
        assert mode is None


# ---------------------------------------------------------------------------
# start_proxy
# ---------------------------------------------------------------------------

class TestStartProxy:
    @patch("builtins.open", MagicMock())
    @patch("ralph.proxy.subprocess.Popen")
    @patch("ralph.proxy.read_token_from_keychain")
    @patch("ralph.proxy.time.time", return_value=1700000000.0)
    def test_launches_with_oauth_mode_by_default(self, mock_time, mock_read, mock_popen):
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
        # Verify mode + token piped via stdin
        mock_proc.stdin.write.assert_called_once_with(b"oauth\nsk-test-token\n")
        mock_proc.stdin.close.assert_called_once()

    @patch("builtins.open", MagicMock())
    @patch("ralph.proxy.subprocess.Popen")
    @patch("ralph.proxy.read_token_from_keychain")
    @patch("ralph.proxy.time.time", return_value=1700000000.0)
    def test_writes_oauth_mode_to_stdin(self, mock_time, mock_read, mock_popen):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "oauth-token", "expiresAt": future_ms}
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        start_proxy("claude", 18080, "/fake/dotfiles", auth_mode="oauth")
        mock_proc.stdin.write.assert_called_once_with(b"oauth\noauth-token\n")
        mock_read.assert_called_once_with("claude", "oauth")

    @patch("builtins.open", MagicMock())
    @patch("ralph.proxy.subprocess.Popen")
    @patch("ralph.proxy.read_token_from_keychain")
    @patch("ralph.proxy.time.time", return_value=1700000000.0)
    def test_writes_api_key_mode_to_stdin(self, mock_time, mock_read, mock_popen):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-ant-api03-key", "expiresAt": future_ms}
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        start_proxy("claude", 18080, "/fake/dotfiles", auth_mode="api_key")
        mock_proc.stdin.write.assert_called_once_with(b"api_key\nsk-ant-api03-key\n")
        mock_read.assert_called_once_with("claude", "api_key")

    @patch("ralph.proxy.read_token_from_keychain", return_value=None)
    def test_exits_with_actionable_message_when_no_token(self, mock_read, capsys):
        with pytest.raises(SystemExit) as exc_info:
            start_proxy("claude", 18080, "/fake/dotfiles")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "no oauth credentials" in captured.err
        assert "ralph store-token --auth oauth" in captured.err

    @patch("ralph.proxy.read_token_from_keychain", return_value=None)
    def test_exits_with_api_key_hint_when_no_api_key(self, mock_read, capsys):
        with pytest.raises(SystemExit) as exc_info:
            start_proxy("claude", 18080, "/fake/dotfiles", auth_mode="api_key")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "no api_key credentials" in captured.err
        assert "ralph store-token --auth api-key" in captured.err

    @patch("ralph.proxy.read_token_from_keychain")
    @patch("ralph.proxy.time.time", return_value=1700000000.0)
    def test_exits_when_token_expired(self, mock_time, mock_read, capsys):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-old", "expiresAt": past_ms}
        with pytest.raises(SystemExit) as exc_info:
            start_proxy("claude", 18080, "/fake/dotfiles")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "credentials expired" in captured.err

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

    @patch("ralph.proxy.time.sleep")
    @patch("ralph.proxy.os.kill")
    @patch("builtins.open", MagicMock(return_value=io.StringIO("12345")))
    def test_wait_polls_until_process_exits(self, mock_kill, mock_sleep):
        # SIGTERM succeeds, then kill(0) raises ProcessLookupError (exited)
        mock_kill.side_effect = [None, ProcessLookupError]
        stop_proxy("claude", wait=True)
        assert mock_kill.call_args_list == [
            ((12345, signal.SIGTERM),),
            ((12345, 0),),
        ]

    @patch("ralph.proxy.time.sleep")
    @patch("ralph.proxy.os.kill")
    @patch("builtins.open", MagicMock(return_value=io.StringIO("12345")))
    def test_wait_sends_sigkill_after_timeout(self, mock_kill, mock_sleep):
        # SIGTERM succeeds, kill(0) always succeeds (process won't die)
        mock_kill.return_value = None
        stop_proxy("claude", wait=True)
        # 1 SIGTERM + 50 kill(0) polls + 1 SIGKILL = 52 calls
        assert mock_kill.call_count == 52
        mock_kill.assert_called_with(12345, signal.SIGKILL)


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
    @patch("ralph.proxy.proxy_health_check", return_value=(True, "abc123def456", "oauth"))
    def test_reuses_healthy_current_proxy_same_mode(self, mock_health, mock_version):
        result = ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert result == 18080
        mock_health.assert_called_once_with(18080)

    @patch("ralph.proxy.compute_proxy_version", return_value="abc123def456")
    @patch("ralph.proxy.proxy_health_check", return_value=(True, "abc123def456", "api_key"))
    def test_reuses_healthy_proxy_when_mode_matches(self, mock_health, mock_version):
        result = ensure_proxy("claude", 18080, "/fake/dotfiles", auth_mode="api_key")
        assert result == 18080

    @patch("ralph.proxy.compute_proxy_version", return_value="newversion123")
    @patch("ralph.proxy.proxy_health_check", return_value=(True, "oldversion456", "oauth"))
    def test_reuses_outdated_proxy_with_warning(self, mock_health, mock_version, capsys):
        result = ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert result == 18080
        captured = capsys.readouterr()
        assert "outdated" in captured.out

    @patch("ralph.proxy.stop_proxy")
    @patch("ralph.proxy.proxy_health_check",
           side_effect=[(False, None, None), (True, "abc123", "oauth")])
    @patch("ralph.proxy.start_proxy")
    @patch("ralph.proxy.time.sleep")
    def test_starts_new_when_none_running(self, mock_sleep, mock_start, mock_health, mock_stop):
        result = ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert result == 18080
        mock_stop.assert_called_once_with("claude", wait=True)
        mock_start.assert_called_once_with("claude", 18080, "/fake/dotfiles", None)

    @patch("ralph.proxy.stop_proxy")
    @patch("ralph.proxy.proxy_health_check",
           side_effect=[(False, None, None), (True, "abc123", "api_key")])
    @patch("ralph.proxy.start_proxy")
    @patch("ralph.proxy.time.sleep")
    def test_starts_new_with_auth_mode(self, mock_sleep, mock_start, mock_health, mock_stop):
        result = ensure_proxy("claude", 18080, "/fake/dotfiles", auth_mode="api_key")
        assert result == 18080
        mock_start.assert_called_once_with("claude", 18080, "/fake/dotfiles", "api_key")

    @patch("ralph.proxy.os.path.isfile", return_value=False)
    @patch("ralph.proxy.stop_proxy")
    @patch("ralph.proxy.proxy_health_check", return_value=(False, None, None))
    @patch("ralph.proxy.start_proxy")
    @patch("ralph.proxy.time.sleep")
    def test_exits_when_proxy_fails_to_become_healthy(self, mock_sleep, mock_start,
                                                       mock_health, mock_stop,
                                                       mock_isfile):
        with pytest.raises(SystemExit) as exc_info:
            ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert exc_info.value.code == 1
        # Called twice: once with wait=True before start, once on cleanup
        assert mock_stop.call_count == 2
        mock_stop.assert_any_call("claude", wait=True)
        mock_stop.assert_any_call("claude")

    @patch("ralph.proxy.stop_proxy")
    @patch("ralph.proxy.proxy_health_check",
           side_effect=[(True, "abc123", "oauth"), (True, "abc123", "api_key")])
    @patch("ralph.proxy.start_proxy")
    @patch("ralph.proxy.time.sleep")
    def test_restarts_when_mode_differs(self, mock_sleep, mock_start, mock_health,
                                         mock_stop, capsys):
        """When proxy is healthy but in wrong mode, stop and restart."""
        result = ensure_proxy("claude", 18080, "/fake/dotfiles", auth_mode="api_key")
        assert result == 18080
        # Should have stopped the old proxy
        mock_stop.assert_called_once_with("claude", wait=True)
        # Should have started a new one with the requested mode
        mock_start.assert_called_once_with("claude", 18080, "/fake/dotfiles", "api_key")
        captured = capsys.readouterr()
        assert "proxy running in oauth mode, restarting in api_key mode" in captured.out

    @patch("ralph.proxy.stop_proxy")
    @patch("ralph.proxy.proxy_health_check",
           side_effect=[(True, "abc123", "api_key"), (True, "abc123", "oauth")])
    @patch("ralph.proxy.start_proxy")
    @patch("ralph.proxy.time.sleep")
    def test_restarts_from_api_key_to_oauth(self, mock_sleep, mock_start, mock_health,
                                             mock_stop, capsys):
        """Mode switch from api_key to oauth also triggers restart."""
        result = ensure_proxy("claude", 18080, "/fake/dotfiles", auth_mode="oauth")
        assert result == 18080
        mock_stop.assert_called_once_with("claude", wait=True)
        mock_start.assert_called_once_with("claude", 18080, "/fake/dotfiles", "oauth")
        captured = capsys.readouterr()
        assert "proxy running in api_key mode, restarting in oauth mode" in captured.out

    @patch("ralph.proxy.stop_proxy")
    @patch("ralph.proxy.proxy_health_check",
           side_effect=[(True, "abc123", None), (True, "abc123", "oauth")])
    @patch("ralph.proxy.start_proxy")
    @patch("ralph.proxy.time.sleep")
    def test_restarts_when_mode_is_none(self, mock_sleep, mock_start, mock_health,
                                         mock_stop, capsys):
        """Stale proxy with no mode field triggers restart."""
        result = ensure_proxy("claude", 18080, "/fake/dotfiles", auth_mode="oauth")
        assert result == 18080
        mock_stop.assert_called_once_with("claude", wait=True)
        mock_start.assert_called_once_with("claude", 18080, "/fake/dotfiles", "oauth")
        captured = capsys.readouterr()
        assert "proxy running in unknown mode, restarting in oauth mode" in captured.out


class TestEnsureProxyStaleCleanup:
    @patch("ralph.proxy.stop_proxy")
    @patch("ralph.proxy.proxy_health_check",
           side_effect=[(False, None, None), (True, "abc123", "oauth")])
    @patch("ralph.proxy.start_proxy")
    @patch("ralph.proxy.time.sleep")
    def test_logs_and_removes_stale_container(self, mock_sleep,
                                               mock_start, mock_health,
                                               mock_stop):
        """Stale proxy is logged, stopped, removed, then a new one starts."""
        result = ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert result == 18080
        mock_stop.assert_called_once_with("claude", wait=True)
        mock_start.assert_called_once_with("claude", 18080, "/fake/dotfiles", None)
