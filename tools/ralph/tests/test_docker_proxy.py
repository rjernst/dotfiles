"""Unit tests for ralph.docker_proxy — Docker socket proxy lifecycle functions."""

import io
import os
import signal
from unittest.mock import MagicMock, patch, call

import pytest

from ralph.docker_proxy import (
    DOCKER_PROXY_PORT,
    DOCKER_PROXY_SOCKET,
    PID_FILE,
    LOCK_FILE,
    LOG_FILE,
    docker_proxy_script_path,
    compute_docker_proxy_version,
    docker_proxy_health_check,
    docker_proxy_socket_health_check,
    docker_proxy_socket_state_paths,
    start_docker_proxy,
    start_docker_proxy_socket,
    stop_docker_proxy,
    ensure_docker_proxy,
    ensure_docker_proxy_socket,
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
    def _sources(self):
        """Fresh handles per call: the script and proxy_base are both read."""
        return lambda *args, **kwargs: io.BytesIO(b"proxy source code")

    def test_returns_12_char_hex_hash(self):
        with patch("builtins.open", self._sources()):
            version = compute_docker_proxy_version("/fake/dotfiles")
        assert len(version) == 12
        assert all(c in "0123456789abcdef" for c in version)

    def test_same_input_same_hash(self):
        with patch("builtins.open", self._sources()):
            v1 = compute_docker_proxy_version("/fake/dotfiles")
        with patch("builtins.open", self._sources()):
            v2 = compute_docker_proxy_version("/fake/dotfiles")
        assert v1 == v2

    def test_proxy_base_is_part_of_the_version(self):
        """A fix in the shared module must retire running proxies."""
        sources = iter([b"script", b"base one", b"script", b"base two"])
        with patch("builtins.open",
                   lambda *a, **k: io.BytesIO(next(sources))):
            first = compute_docker_proxy_version("/fake/dotfiles")
            second = compute_docker_proxy_version("/fake/dotfiles")
        assert first != second


# ---------------------------------------------------------------------------
# docker_proxy_health_check
# ---------------------------------------------------------------------------


class TestDockerProxyHealthCheck:
    @patch("ralph.docker_proxy.urllib.request.urlopen")
    def test_returns_healthy_with_version(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"docker-socket-proxy ok v=abc123def456 addr=::"
        mock_urlopen.return_value = mock_resp
        healthy, version, addr = docker_proxy_health_check(18081)
        assert healthy is True
        assert version == "abc123def456"
        assert addr == "::"

    @patch("ralph.docker_proxy.urllib.request.urlopen")
    def test_returns_healthy_none_version_on_old_format(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"docker-socket-proxy ok"
        mock_urlopen.return_value = mock_resp
        healthy, version, addr = docker_proxy_health_check(18081)
        assert healthy is True
        assert version is None
        assert addr is None

    @patch("ralph.docker_proxy.urllib.request.urlopen")
    def test_returns_unhealthy_on_non_200(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_urlopen.return_value = mock_resp
        healthy, version, addr = docker_proxy_health_check(18081)
        assert healthy is False
        assert version is None

    @patch(
        "ralph.docker_proxy.urllib.request.urlopen",
        side_effect=Exception("connection refused"),
    )
    def test_returns_unhealthy_on_connection_error(self, mock_urlopen):
        healthy, version, addr = docker_proxy_health_check(18081)
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
        assert env["LISTEN_ADDR"] == "::"
        assert env["PID_FILE"] == PID_FILE
        # Verify stdin is DEVNULL (no token needed)
        assert mock_popen.call_args[1]["stdin"] == __import__("subprocess").DEVNULL

    @patch("builtins.open", MagicMock())
    @patch("ralph.docker_proxy.subprocess.Popen")
    def test_passes_listen_addr_to_proxy_env(self, mock_popen):
        mock_popen.return_value = MagicMock()
        start_docker_proxy(18081, "/fake/dotfiles", "127.0.0.1")
        env = mock_popen.call_args[1]["env"]
        assert env["LISTEN_ADDR"] == "127.0.0.1"

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
        return_value=(True, "abc123def456", "::"),
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
        return_value=(True, "oldversion456", "::"),
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
        side_effect=[(False, None, None)] + [(True, "abc123", "::")],
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
        mock_start.assert_called_once_with(18081, "/fake/dotfiles", "::")

    @patch("ralph.docker_proxy.os.path.isfile", return_value=False)
    @patch("ralph.docker_proxy.stop_docker_proxy")
    @patch(
        "ralph.docker_proxy.docker_proxy_health_check",
        return_value=(False, None, None),
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


class TestEnsureDockerProxyListenAddr:
    @patch(
        "ralph.docker_proxy.compute_docker_proxy_version",
        return_value="abc123def456",
    )
    @patch(
        "ralph.docker_proxy.docker_proxy_health_check",
        return_value=(True, "abc123def456", "127.0.0.1"),
    )
    def test_reuses_proxy_on_matching_addr(self, mock_health, mock_version):
        result = ensure_docker_proxy(18081, "/fake/dotfiles", "127.0.0.1")
        assert result == 18081

    @patch("ralph.docker_proxy.stop_docker_proxy")
    @patch(
        "ralph.docker_proxy.docker_proxy_health_check",
        side_effect=[(True, "abc123", "::"), (True, "abc123", "127.0.0.1")],
    )
    @patch("ralph.docker_proxy.start_docker_proxy")
    @patch("ralph.docker_proxy.time.sleep")
    def test_restarts_when_addr_differs(
        self, mock_sleep, mock_start, mock_health, mock_stop, capsys
    ):
        result = ensure_docker_proxy(18081, "/fake/dotfiles", "127.0.0.1")
        assert result == 18081
        mock_stop.assert_called_once_with(wait=True)
        mock_start.assert_called_once_with(18081, "/fake/dotfiles", "127.0.0.1")
        captured = capsys.readouterr()
        assert ("docker socket proxy listening on ::, restarting on 127.0.0.1"
                in captured.out)

    @patch("ralph.docker_proxy.stop_docker_proxy")
    @patch(
        "ralph.docker_proxy.docker_proxy_health_check",
        side_effect=[(True, "abc123", None), (True, "abc123", "::")],
    )
    @patch("ralph.docker_proxy.start_docker_proxy")
    @patch("ralph.docker_proxy.time.sleep")
    def test_restarts_proxy_without_addr_field(
        self, mock_sleep, mock_start, mock_health, mock_stop, capsys
    ):
        result = ensure_docker_proxy(18081, "/fake/dotfiles")
        assert result == 18081
        mock_stop.assert_called_once_with(wait=True)
        captured = capsys.readouterr()
        assert ("docker socket proxy listening on unknown, restarting on ::"
                in captured.out)


class TestEnsureDockerProxyStaleCleanup:
    @patch("ralph.docker_proxy.stop_docker_proxy")
    @patch(
        "ralph.docker_proxy.docker_proxy_health_check",
        side_effect=[(False, None, None), (True, "abc123", "::")],
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
        mock_start.assert_called_once_with(18081, "/fake/dotfiles", "::")


# ---------------------------------------------------------------------------
# Unix socket mode
# ---------------------------------------------------------------------------


class TestDockerProxySocketStatePaths:
    def test_state_files_sit_beside_the_socket(self):
        pid, lock, log = docker_proxy_socket_state_paths("/home/u/.ralph/d.sock")
        assert pid == "/home/u/.ralph/docker-proxy-sock.pid"
        assert lock == "/home/u/.ralph/docker-proxy-sock.lock"
        assert log == "/home/u/.ralph/docker-proxy-sock.log"

    def test_default_socket_yields_spec_pid_path(self):
        pid, _, _ = docker_proxy_socket_state_paths(DOCKER_PROXY_SOCKET)
        assert pid == os.path.expanduser("~/.ralph/docker-proxy-sock.pid")


class TestDockerProxySocketHealthCheck:
    @patch("ralph.docker_proxy._UnixHTTPConnection")
    def test_parses_version_and_addr(self, mock_conn_cls):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = (
            b"docker-socket-proxy ok v=abc123def456 addr=unix:/tmp/d.sock"
        )
        mock_conn_cls.return_value.getresponse.return_value = mock_resp
        healthy, version, addr = docker_proxy_socket_health_check("/tmp/d.sock")
        assert healthy is True
        assert version == "abc123def456"
        assert addr == "unix:/tmp/d.sock"
        mock_conn_cls.return_value.request.assert_called_once_with(
            "GET", "/health"
        )

    @patch("ralph.docker_proxy._UnixHTTPConnection")
    def test_non_200_is_unhealthy(self, mock_conn_cls):
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_conn_cls.return_value.getresponse.return_value = mock_resp
        assert docker_proxy_socket_health_check("/tmp/d.sock") == (
            False, None, None)

    @patch(
        "ralph.docker_proxy._UnixHTTPConnection",
        side_effect=ConnectionRefusedError,
    )
    def test_unreachable_socket_is_unhealthy(self, mock_conn_cls):
        assert docker_proxy_socket_health_check("/tmp/d.sock") == (
            False, None, None)

    @patch("ralph.docker_proxy._UnixHTTPConnection")
    def test_closes_connection(self, mock_conn_cls):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"docker-socket-proxy ok v=abc123"
        mock_conn_cls.return_value.getresponse.return_value = mock_resp
        docker_proxy_socket_health_check("/tmp/d.sock")
        mock_conn_cls.return_value.close.assert_called_once()


class TestStartDockerProxySocket:
    @patch("ralph.docker_proxy.subprocess.Popen")
    @patch("builtins.open", MagicMock())
    def test_passes_listen_socket_and_pid_file(self, mock_popen):
        start_docker_proxy_socket("/home/u/.ralph/d.sock", "/fake/dotfiles")
        args, kwargs = mock_popen.call_args
        assert args[0] == [
            "python3",
            "/fake/dotfiles/docker/agent-loop/proxy/docker_socket_proxy.py",
        ]
        assert kwargs["env"]["LISTEN_SOCKET"] == "/home/u/.ralph/d.sock"
        assert (kwargs["env"]["PID_FILE"]
                == "/home/u/.ralph/docker-proxy-sock.pid")


class TestEnsureDockerProxySocket:
    @patch(
        "ralph.docker_proxy.compute_docker_proxy_version",
        return_value="abc123def456",
    )
    @patch(
        "ralph.docker_proxy.docker_proxy_socket_health_check",
        return_value=(True, "abc123def456", "unix:/s/d.sock"),
    )
    def test_reuses_healthy_current_proxy(
        self, mock_health, mock_version, tmp_path
    ):
        sock = str(tmp_path / "d.sock")
        assert ensure_docker_proxy_socket("/fake/dotfiles", sock) == sock
        mock_health.assert_called_once_with(sock)

    @patch(
        "ralph.docker_proxy.compute_docker_proxy_version",
        return_value="newversion123",
    )
    @patch(
        "ralph.docker_proxy.docker_proxy_socket_health_check",
        return_value=(True, "oldversion456", "unix:/s/d.sock"),
    )
    def test_reuses_outdated_proxy_with_warning(
        self, mock_health, mock_version, tmp_path, capsys
    ):
        sock = str(tmp_path / "d.sock")
        assert ensure_docker_proxy_socket("/fake/dotfiles", sock) == sock
        assert "outdated" in capsys.readouterr().out

    @patch("ralph.docker_proxy.stop_docker_proxy")
    @patch(
        "ralph.docker_proxy.docker_proxy_socket_health_check",
        side_effect=[(False, None, None), (True, "abc123", "unix:/s/d.sock")],
    )
    @patch("ralph.docker_proxy.start_docker_proxy_socket")
    @patch("ralph.docker_proxy.time.sleep")
    def test_starts_new_when_none_running(
        self, mock_sleep, mock_start, mock_health, mock_stop, tmp_path
    ):
        sock = str(tmp_path / "d.sock")
        assert ensure_docker_proxy_socket("/fake/dotfiles", sock) == sock
        mock_stop.assert_called_once_with(
            wait=True, pid_file=str(tmp_path / "docker-proxy-sock.pid")
        )
        mock_start.assert_called_once_with(sock, "/fake/dotfiles")

    @patch("ralph.docker_proxy.stop_docker_proxy")
    @patch(
        "ralph.docker_proxy.docker_proxy_socket_health_check",
        return_value=(False, None, None),
    )
    @patch("ralph.docker_proxy.start_docker_proxy_socket")
    @patch("ralph.docker_proxy.time.sleep")
    def test_exits_when_proxy_fails_to_become_healthy(
        self, mock_sleep, mock_start, mock_health, mock_stop, tmp_path
    ):
        sock = str(tmp_path / "d.sock")
        pid_file = str(tmp_path / "docker-proxy-sock.pid")
        with pytest.raises(SystemExit) as exc_info:
            ensure_docker_proxy_socket("/fake/dotfiles", sock)
        assert exc_info.value.code == 1
        # Both stops must target the socket proxy's own PID file — never the
        # TCP proxy's.
        assert mock_stop.call_args_list == [
            call(wait=True, pid_file=pid_file),
            call(pid_file=pid_file),
        ]

    @patch(
        "ralph.docker_proxy.compute_docker_proxy_version",
        return_value="abc123def456",
    )
    @patch(
        "ralph.docker_proxy.docker_proxy_socket_health_check",
        return_value=(True, "abc123def456", "unix:/s/d.sock"),
    )
    def test_creates_socket_directory_with_0700(
        self, mock_health, mock_version, tmp_path
    ):
        sock = str(tmp_path / "ralph" / "d.sock")
        ensure_docker_proxy_socket("/fake/dotfiles", sock)
        parent = tmp_path / "ralph"
        assert parent.is_dir()
        assert parent.stat().st_mode & 0o777 == 0o700

    @patch(
        "ralph.docker_proxy.compute_docker_proxy_version",
        return_value="abc123def456",
    )
    @patch(
        "ralph.docker_proxy.docker_proxy_socket_health_check",
        return_value=(True, "abc123def456", "unix:/s/d.sock"),
    )
    def test_tightens_existing_lax_socket_directory(
        self, mock_health, mock_version, tmp_path
    ):
        """~/.ralph is created elsewhere without a mode — tighten it here."""
        parent = tmp_path / "ralph"
        parent.mkdir(mode=0o755)
        ensure_docker_proxy_socket("/fake/dotfiles", str(parent / "d.sock"))
        assert parent.stat().st_mode & 0o777 == 0o700

    @patch(
        "ralph.docker_proxy.compute_docker_proxy_version",
        return_value="abc123def456",
    )
    @patch(
        "ralph.docker_proxy.docker_proxy_socket_health_check",
        return_value=(True, "abc123def456", "unix:/s/d.sock"),
    )
    def test_defaults_to_module_socket_path(
        self, mock_health, mock_version, tmp_path, monkeypatch
    ):
        sock = str(tmp_path / "d.sock")
        monkeypatch.setattr("ralph.docker_proxy.DOCKER_PROXY_SOCKET", sock)
        assert ensure_docker_proxy_socket("/fake/dotfiles") == sock


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_default_port(self):
        assert DOCKER_PROXY_PORT == 18081

    def test_socket_path(self):
        assert DOCKER_PROXY_SOCKET == os.path.expanduser(
            "~/.ralph/docker-proxy.sock")

    def test_pid_file_path(self):
        assert PID_FILE == "/tmp/ralph-docker-proxy.pid"

    def test_lock_file_path(self):
        assert LOCK_FILE == "/tmp/ralph-docker-proxy.lock"

    def test_log_file_path(self):
        assert LOG_FILE == "/tmp/ralph-docker-proxy.log"
