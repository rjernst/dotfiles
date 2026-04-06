"""Unit tests for ralph.network_proxy — Network proxy lifecycle functions."""

import io
import signal
from unittest.mock import MagicMock, patch, call

import pytest

from ralph.network_proxy import (
    NETWORK_PROXY_PORT,
    PID_FILE,
    LOCK_FILE,
    LOG_FILE,
    network_proxy_script_path,
    compute_network_proxy_version,
    network_proxy_health_check,
    start_network_proxy,
    stop_network_proxy,
    ensure_network_proxy,
)


# ---------------------------------------------------------------------------
# network_proxy_script_path
# ---------------------------------------------------------------------------


class TestNetworkProxyScriptPath:
    def test_returns_path_to_network_proxy(self):
        path = network_proxy_script_path("/fake/dotfiles")
        assert path == "/fake/dotfiles/docker/agent-loop/proxy/network_proxy.py"


# ---------------------------------------------------------------------------
# compute_network_proxy_version
# ---------------------------------------------------------------------------


class TestComputeNetworkProxyVersion:
    @patch(
        "builtins.open",
        MagicMock(return_value=io.BytesIO(b"proxy source code")),
    )
    def test_returns_12_char_hex_hash(self):
        version = compute_network_proxy_version("/fake/dotfiles")
        assert len(version) == 12
        assert all(c in "0123456789abcdef" for c in version)

    def test_same_input_same_hash(self):
        with patch("builtins.open", return_value=io.BytesIO(b"proxy source code")):
            v1 = compute_network_proxy_version("/fake/dotfiles")
        with patch("builtins.open", return_value=io.BytesIO(b"proxy source code")):
            v2 = compute_network_proxy_version("/fake/dotfiles")
        assert v1 == v2


# ---------------------------------------------------------------------------
# network_proxy_health_check
# ---------------------------------------------------------------------------


class TestNetworkProxyHealthCheck:
    @patch("ralph.network_proxy.urllib.request.urlopen")
    def test_returns_healthy_with_version_and_hosts(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = (
            b"network-proxy ok hosts=example.com,github.com v=abc123def456"
        )
        mock_urlopen.return_value = mock_resp
        healthy, version, hosts = network_proxy_health_check(18082)
        assert healthy is True
        assert version == "abc123def456"
        assert hosts == frozenset({"example.com", "github.com"})

    @patch("ralph.network_proxy.urllib.request.urlopen")
    def test_returns_empty_hosts_when_none_configured(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"network-proxy ok hosts= v=abc123def456"
        mock_urlopen.return_value = mock_resp
        healthy, version, hosts = network_proxy_health_check(18082)
        assert healthy is True
        assert version == "abc123def456"
        assert hosts == frozenset()

    @patch("ralph.network_proxy.urllib.request.urlopen")
    def test_returns_healthy_none_version_on_old_format(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"network-proxy ok"
        mock_urlopen.return_value = mock_resp
        healthy, version, hosts = network_proxy_health_check(18082)
        assert healthy is True
        assert version is None
        assert hosts == frozenset()

    @patch("ralph.network_proxy.urllib.request.urlopen")
    def test_returns_unhealthy_on_non_200(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_urlopen.return_value = mock_resp
        healthy, version, hosts = network_proxy_health_check(18082)
        assert healthy is False
        assert version is None
        assert hosts is None

    @patch(
        "ralph.network_proxy.urllib.request.urlopen",
        side_effect=Exception("connection refused"),
    )
    def test_returns_unhealthy_on_connection_error(self, mock_urlopen):
        healthy, version, hosts = network_proxy_health_check(18082)
        assert healthy is False
        assert version is None
        assert hosts is None

    @patch("ralph.network_proxy.urllib.request.urlopen")
    def test_parses_single_host(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = (
            b"network-proxy ok hosts=api.example.com v=aaa111bbb222"
        )
        mock_urlopen.return_value = mock_resp
        healthy, version, hosts = network_proxy_health_check(18082)
        assert healthy is True
        assert hosts == frozenset({"api.example.com"})


# ---------------------------------------------------------------------------
# start_network_proxy
# ---------------------------------------------------------------------------


class TestStartNetworkProxy:
    @patch("builtins.open", MagicMock())
    @patch("ralph.network_proxy.subprocess.Popen")
    def test_launches_python3_with_proxy_script(self, mock_popen):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        result = start_network_proxy(18082, "/fake/dotfiles", ["example.com", "github.com"])
        assert result is mock_proc

        # Verify python3 network_proxy.py command
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "python3"
        assert cmd[1].endswith("network_proxy.py")
        # Verify env vars
        env = mock_popen.call_args[1]["env"]
        assert env["LISTEN_PORT"] == "18082"
        assert env["PID_FILE"] == PID_FILE
        assert env["ALLOWED_HOSTS"] == "example.com,github.com"
        # Verify stdin is DEVNULL
        assert mock_popen.call_args[1]["stdin"] == __import__("subprocess").DEVNULL

    @patch("builtins.open", MagicMock())
    @patch("ralph.network_proxy.subprocess.Popen")
    def test_empty_allowed_hosts(self, mock_popen):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        start_network_proxy(18082, "/fake/dotfiles", [])
        env = mock_popen.call_args[1]["env"]
        assert env["ALLOWED_HOSTS"] == ""

    @patch("builtins.open", MagicMock())
    @patch(
        "ralph.network_proxy.subprocess.Popen",
        side_effect=OSError("python3 not found"),
    )
    def test_popen_failure_raises(self, mock_popen):
        with pytest.raises(OSError):
            start_network_proxy(18082, "/fake/dotfiles", ["example.com"])


# ---------------------------------------------------------------------------
# stop_network_proxy
# ---------------------------------------------------------------------------


class TestStopNetworkProxy:
    @patch("ralph.network_proxy.os.kill")
    @patch("builtins.open", MagicMock(return_value=io.StringIO("12345")))
    def test_sends_sigterm_to_pid(self, mock_kill):
        stop_network_proxy()
        mock_kill.assert_called_once_with(12345, signal.SIGTERM)

    def test_no_error_when_pid_file_missing(self, tmp_path):
        with patch("ralph.network_proxy.PID_FILE", str(tmp_path / "nonexistent.pid")):
            stop_network_proxy()  # Should not raise

    @patch("ralph.network_proxy.os.kill", side_effect=ProcessLookupError)
    @patch("builtins.open", MagicMock(return_value=io.StringIO("99999")))
    def test_no_error_when_process_gone(self, mock_kill):
        stop_network_proxy()  # Should not raise

    @patch("ralph.network_proxy.time.sleep")
    @patch("ralph.network_proxy.os.kill")
    @patch("builtins.open", MagicMock(return_value=io.StringIO("12345")))
    def test_wait_polls_until_process_exits(self, mock_kill, mock_sleep):
        mock_kill.side_effect = [None, ProcessLookupError]
        stop_network_proxy(wait=True)
        assert mock_kill.call_args_list == [
            call(12345, signal.SIGTERM),
            call(12345, 0),
        ]

    @patch("ralph.network_proxy.time.sleep")
    @patch("ralph.network_proxy.os.kill")
    @patch("builtins.open", MagicMock(return_value=io.StringIO("12345")))
    def test_wait_sends_sigkill_after_timeout(self, mock_kill, mock_sleep):
        mock_kill.return_value = None
        stop_network_proxy(wait=True)
        # 1 SIGTERM + 50 kill(0) polls + 1 SIGKILL = 52 calls
        assert mock_kill.call_count == 52
        mock_kill.assert_called_with(12345, signal.SIGKILL)


# ---------------------------------------------------------------------------
# ensure_network_proxy — reuse healthy proxy
# ---------------------------------------------------------------------------


class TestEnsureNetworkProxy:
    @patch(
        "ralph.network_proxy.compute_network_proxy_version",
        return_value="abc123def456",
    )
    @patch(
        "ralph.network_proxy.network_proxy_health_check",
        return_value=(True, "abc123def456", frozenset({"example.com"})),
    )
    def test_reuses_healthy_current_proxy(self, mock_health, mock_version):
        result = ensure_network_proxy(18082, "/fake/dotfiles", ["example.com"])
        assert result == 18082
        mock_health.assert_called_once_with(18082)

    @patch(
        "ralph.network_proxy.compute_network_proxy_version",
        return_value="newversion123",
    )
    @patch(
        "ralph.network_proxy.network_proxy_health_check",
        return_value=(True, "oldversion456", frozenset({"example.com"})),
    )
    def test_reuses_outdated_proxy_with_warning(
        self, mock_health, mock_version, capsys
    ):
        result = ensure_network_proxy(18082, "/fake/dotfiles", ["example.com"])
        assert result == 18082
        captured = capsys.readouterr()
        assert "outdated" in captured.out

    @patch("ralph.network_proxy.stop_network_proxy")
    @patch(
        "ralph.network_proxy.network_proxy_health_check",
        side_effect=[
            (False, None, None),
            (True, "abc123", frozenset({"example.com"})),
        ],
    )
    @patch("ralph.network_proxy.start_network_proxy")
    @patch("ralph.network_proxy.time.sleep")
    def test_starts_new_when_none_running(
        self, mock_sleep, mock_start, mock_health, mock_stop
    ):
        result = ensure_network_proxy(18082, "/fake/dotfiles", ["example.com"])
        assert result == 18082
        mock_stop.assert_called_once_with(wait=True)
        mock_start.assert_called_once_with(18082, "/fake/dotfiles", ["example.com"])

    @patch("ralph.network_proxy.os.path.isfile", return_value=False)
    @patch("ralph.network_proxy.stop_network_proxy")
    @patch(
        "ralph.network_proxy.network_proxy_health_check",
        return_value=(False, None, None),
    )
    @patch("ralph.network_proxy.start_network_proxy")
    @patch("ralph.network_proxy.time.sleep")
    def test_exits_when_proxy_fails_to_become_healthy(
        self, mock_sleep, mock_start, mock_health, mock_stop, mock_isfile
    ):
        with pytest.raises(SystemExit) as exc_info:
            ensure_network_proxy(18082, "/fake/dotfiles", ["example.com"])
        assert exc_info.value.code == 1
        # Called twice: once with wait=True before start, once on cleanup
        assert mock_stop.call_count == 2
        mock_stop.assert_any_call(wait=True)
        mock_stop.assert_any_call()


# ---------------------------------------------------------------------------
# ensure_network_proxy — allowlist change detection
# ---------------------------------------------------------------------------


class TestEnsureNetworkProxyAllowlistChange:
    @patch("ralph.network_proxy.stop_network_proxy")
    @patch(
        "ralph.network_proxy.network_proxy_health_check",
        side_effect=[
            # First check: healthy but with old allowlist
            (True, "abc123def456", frozenset({"old-host.com"})),
            # After restart: healthy with new allowlist
            (True, "abc123def456", frozenset({"new-host.com"})),
        ],
    )
    @patch("ralph.network_proxy.start_network_proxy")
    @patch("ralph.network_proxy.time.sleep")
    def test_restarts_when_allowlist_changes(
        self, mock_sleep, mock_start, mock_health, mock_stop, capsys
    ):
        result = ensure_network_proxy(18082, "/fake/dotfiles", ["new-host.com"])
        assert result == 18082
        # Should stop the old proxy and start a new one
        mock_stop.assert_called_once_with(wait=True)
        mock_start.assert_called_once_with(18082, "/fake/dotfiles", ["new-host.com"])
        captured = capsys.readouterr()
        assert "allowlist changed" in captured.out

    @patch(
        "ralph.network_proxy.compute_network_proxy_version",
        return_value="abc123def456",
    )
    @patch(
        "ralph.network_proxy.network_proxy_health_check",
        return_value=(True, "abc123def456", frozenset({"a.com", "b.com"})),
    )
    def test_order_independent_allowlist_comparison(
        self, mock_health, mock_version
    ):
        """Allowlists are compared as sets, so order doesn't matter."""
        # Pass hosts in different order than what's running
        result = ensure_network_proxy(18082, "/fake/dotfiles", ["b.com", "a.com"])
        assert result == 18082
        # Should reuse, not restart
        mock_health.assert_called_once_with(18082)


class TestEnsureNetworkProxyStaleCleanup:
    @patch("ralph.network_proxy.stop_network_proxy")
    @patch(
        "ralph.network_proxy.network_proxy_health_check",
        side_effect=[
            (False, None, None),
            (True, "abc123", frozenset({"example.com"})),
        ],
    )
    @patch("ralph.network_proxy.start_network_proxy")
    @patch("ralph.network_proxy.time.sleep")
    def test_stops_stale_and_starts_new(
        self, mock_sleep, mock_start, mock_health, mock_stop
    ):
        """Stale proxy is stopped, then a new one starts."""
        result = ensure_network_proxy(18082, "/fake/dotfiles", ["example.com"])
        assert result == 18082
        mock_stop.assert_called_once_with(wait=True)
        mock_start.assert_called_once_with(18082, "/fake/dotfiles", ["example.com"])


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_default_port(self):
        assert NETWORK_PROXY_PORT == 18082

    def test_pid_file_path(self):
        assert PID_FILE == "/tmp/ralph-network-proxy.pid"

    def test_lock_file_path(self):
        assert LOCK_FILE == "/tmp/ralph-network-proxy.lock"

    def test_log_file_path(self):
        assert LOG_FILE == "/tmp/ralph-network-proxy.log"
