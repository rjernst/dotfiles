"""Unit tests for ralph.docker_proxy — Docker socket proxy lifecycle functions."""

import io
import signal
from unittest.mock import MagicMock, patch, call

import pytest

from ralph.docker_proxy import (
    DOCKER_PROXY_PORT,
    PID_FILE,
    LOCK_FILE,
    LOG_FILE,
    docker_proxy_script_path,
    compute_docker_proxy_version,
    docker_proxy_health_check,
    start_docker_proxy,
    stop_docker_proxy,
    ensure_docker_proxy,
)


# ---------------------------------------------------------------------------
# docker_proxy_script_path
# ---------------------------------------------------------------------------


class TestDockerProxyScriptPath:
    def test_returns_path_to_docker_socket_proxy(self):
        path = docker_proxy_script_path("/fake/dotfiles")
        assert path == "/fake/dotfiles/docker/agent-loop/proxy/docker_socket_proxy.py"


# ---------------------------------------------------------------------------
# compute_docker_proxy_version
# ---------------------------------------------------------------------------


class TestComputeDockerProxyVersion:
    @patch(
        "builtins.open",
        MagicMock(return_value=io.BytesIO(b"proxy source code")),
    )
    def test_returns_12_char_hex_hash(self):
        version = compute_docker_proxy_version("/fake/dotfiles")
        assert len(version) == 12
        assert all(c in "0123456789abcdef" for c in version)

    def test_same_input_same_hash(self):
        with patch("builtins.open", return_value=io.BytesIO(b"proxy source code")):
            v1 = compute_docker_proxy_version("/fake/dotfiles")
        with patch("builtins.open", return_value=io.BytesIO(b"proxy source code")):
            v2 = compute_docker_proxy_version("/fake/dotfiles")
        assert v1 == v2


# ---------------------------------------------------------------------------
# docker_proxy_health_check
# ---------------------------------------------------------------------------


class TestDockerProxyHealthCheck:
    @patch("ralph.docker_proxy.urllib.request.urlopen")
    def test_returns_healthy_with_version(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"docker-socket-proxy ok v=abc123def456"
        mock_urlopen.return_value = mock_resp
        healthy, version = docker_proxy_health_check(18081)
        assert healthy is True
        assert version == "abc123def456"

    @patch("ralph.docker_proxy.urllib.request.urlopen")
    def test_returns_healthy_none_version_on_old_format(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"docker-socket-proxy ok"
        mock_urlopen.return_value = mock_resp
        healthy, version = docker_proxy_health_check(18081)
        assert healthy is True
        assert version is None

    @patch("ralph.docker_proxy.urllib.request.urlopen")
    def test_returns_unhealthy_on_non_200(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_urlopen.return_value = mock_resp
        healthy, version = docker_proxy_health_check(18081)
        assert healthy is False
        assert version is None

    @patch(
        "ralph.docker_proxy.urllib.request.urlopen",
        side_effect=Exception("connection refused"),
    )
    def test_returns_unhealthy_on_connection_error(self, mock_urlopen):
        healthy, version = docker_proxy_health_check(18081)
        assert healthy is False
        assert version is None


# ---------------------------------------------------------------------------
# start_docker_proxy
# ---------------------------------------------------------------------------


class TestStartDockerProxy:
    @patch("builtins.open", MagicMock())
    @patch("ralph.docker_proxy.subprocess.Popen")
    def test_launches_python3_with_proxy_script(self, mock_popen):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        result = start_docker_proxy(18081, "/fake/dotfiles")
        assert result is mock_proc

        # Verify python3 docker_socket_proxy.py command
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "python3"
        assert cmd[1].endswith("docker_socket_proxy.py")
        # Verify env vars
        env = mock_popen.call_args[1]["env"]
        assert env["LISTEN_PORT"] == "18081"
        assert env["PID_FILE"] == PID_FILE
        # Verify stdin is DEVNULL (no token needed)
        assert mock_popen.call_args[1]["stdin"] == __import__("subprocess").DEVNULL

    @patch("builtins.open", MagicMock())
    @patch(
        "ralph.docker_proxy.subprocess.Popen",
        side_effect=OSError("python3 not found"),
    )
    def test_popen_failure_raises(self, mock_popen):
        with pytest.raises(OSError):
            start_docker_proxy(18081, "/fake/dotfiles")


# ---------------------------------------------------------------------------
# stop_docker_proxy
# ---------------------------------------------------------------------------


class TestStopDockerProxy:
    @patch("ralph.docker_proxy.os.kill")
    @patch("builtins.open", MagicMock(return_value=io.StringIO("12345")))
    def test_sends_sigterm_to_pid(self, mock_kill):
        stop_docker_proxy()
        mock_kill.assert_called_once_with(12345, signal.SIGTERM)

    def test_no_error_when_pid_file_missing(self, tmp_path):
        # PID_FILE points to a file that doesn't exist by default,
        # but to be safe we patch it to a known-missing path
        with patch("ralph.docker_proxy.PID_FILE", str(tmp_path / "nonexistent.pid")):
            stop_docker_proxy()  # Should not raise

    @patch("ralph.docker_proxy.os.kill", side_effect=ProcessLookupError)
    @patch("builtins.open", MagicMock(return_value=io.StringIO("99999")))
    def test_no_error_when_process_gone(self, mock_kill):
        stop_docker_proxy()  # Should not raise

    @patch("ralph.docker_proxy.time.sleep")
    @patch("ralph.docker_proxy.os.kill")
    @patch("builtins.open", MagicMock(return_value=io.StringIO("12345")))
    def test_wait_polls_until_process_exits(self, mock_kill, mock_sleep):
        # SIGTERM succeeds, then kill(0) raises ProcessLookupError (exited)
        mock_kill.side_effect = [None, ProcessLookupError]
        stop_docker_proxy(wait=True)
        assert mock_kill.call_args_list == [
            call(12345, signal.SIGTERM),
            call(12345, 0),
        ]

    @patch("ralph.docker_proxy.time.sleep")
    @patch("ralph.docker_proxy.os.kill")
    @patch("builtins.open", MagicMock(return_value=io.StringIO("12345")))
    def test_wait_sends_sigkill_after_timeout(self, mock_kill, mock_sleep):
        # SIGTERM succeeds, kill(0) always succeeds (process won't die)
        mock_kill.return_value = None
        stop_docker_proxy(wait=True)
        # 1 SIGTERM + 50 kill(0) polls + 1 SIGKILL = 52 calls
        assert mock_kill.call_count == 52
        mock_kill.assert_called_with(12345, signal.SIGKILL)


# ---------------------------------------------------------------------------
# ensure_docker_proxy
# ---------------------------------------------------------------------------


class TestEnsureDockerProxy:
    @patch(
        "ralph.docker_proxy.compute_docker_proxy_version",
        return_value="abc123def456",
    )
    @patch(
        "ralph.docker_proxy.docker_proxy_health_check",
        return_value=(True, "abc123def456"),
    )
    def test_reuses_healthy_current_proxy(self, mock_health, mock_version):
        result = ensure_docker_proxy(18081, "/fake/dotfiles")
        assert result == 18081
        mock_health.assert_called_once_with(18081)

    @patch(
        "ralph.docker_proxy.compute_docker_proxy_version",
        return_value="newversion123",
    )
    @patch(
        "ralph.docker_proxy.docker_proxy_health_check",
        return_value=(True, "oldversion456"),
    )
    def test_reuses_outdated_proxy_with_warning(
        self, mock_health, mock_version, capsys
    ):
        result = ensure_docker_proxy(18081, "/fake/dotfiles")
        assert result == 18081
        captured = capsys.readouterr()
        assert "outdated" in captured.out

    @patch("ralph.docker_proxy.stop_docker_proxy")
    @patch(
        "ralph.docker_proxy.docker_proxy_health_check",
        side_effect=[(False, None)] + [(True, "abc123")],
    )
    @patch("ralph.docker_proxy.start_docker_proxy")
    @patch("ralph.docker_proxy.time.sleep")
    def test_starts_new_when_none_running(
        self, mock_sleep, mock_start, mock_health, mock_stop
    ):
        result = ensure_docker_proxy(18081, "/fake/dotfiles")
        assert result == 18081
        # Kills lingering proxy before starting new one
        mock_stop.assert_called_once_with(wait=True)
        mock_start.assert_called_once_with(18081, "/fake/dotfiles")

    @patch("ralph.docker_proxy.os.path.isfile", return_value=False)
    @patch("ralph.docker_proxy.stop_docker_proxy")
    @patch(
        "ralph.docker_proxy.docker_proxy_health_check",
        return_value=(False, None),
    )
    @patch("ralph.docker_proxy.start_docker_proxy")
    @patch("ralph.docker_proxy.time.sleep")
    def test_exits_when_proxy_fails_to_become_healthy(
        self, mock_sleep, mock_start, mock_health, mock_stop, mock_isfile
    ):
        with pytest.raises(SystemExit) as exc_info:
            ensure_docker_proxy(18081, "/fake/dotfiles")
        assert exc_info.value.code == 1
        # Called twice: once with wait=True before start, once on cleanup
        assert mock_stop.call_count == 2
        mock_stop.assert_any_call(wait=True)
        mock_stop.assert_any_call()


class TestEnsureDockerProxyStaleCleanup:
    @patch("ralph.docker_proxy.stop_docker_proxy")
    @patch(
        "ralph.docker_proxy.docker_proxy_health_check",
        side_effect=[(False, None), (True, "abc123")],
    )
    @patch("ralph.docker_proxy.start_docker_proxy")
    @patch("ralph.docker_proxy.time.sleep")
    def test_stops_stale_and_starts_new(
        self, mock_sleep, mock_start, mock_health, mock_stop
    ):
        """Stale proxy is stopped, then a new one starts."""
        result = ensure_docker_proxy(18081, "/fake/dotfiles")
        assert result == 18081
        mock_stop.assert_called_once_with(wait=True)
        mock_start.assert_called_once_with(18081, "/fake/dotfiles")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_default_port(self):
        assert DOCKER_PROXY_PORT == 18081

    def test_pid_file_path(self):
        assert PID_FILE == "/tmp/ralph-docker-proxy.pid"

    def test_lock_file_path(self):
        assert LOCK_FILE == "/tmp/ralph-docker-proxy.lock"

    def test_log_file_path(self):
        assert LOG_FILE == "/tmp/ralph-docker-proxy.log"
