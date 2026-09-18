"""Tests for ralph.selftest — selftest orchestration."""

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from ralph.docker_proxy import DOCKER_PROXY_PORT
from ralph.runtime.nono import NonoRuntime
from ralph.selftest import (
    _first_line, _parse_env, _SelftestAbort, _summarize, selftest,
)


# ---------------------------------------------------------------------------
# _SelftestAbort
# ---------------------------------------------------------------------------

class TestSelftestAbort:
    def test_is_exception(self):
        assert issubclass(_SelftestAbort, Exception)

    def test_can_be_raised_and_caught(self):
        try:
            raise _SelftestAbort()
        except _SelftestAbort:
            pass


# ---------------------------------------------------------------------------
# selftest (mocked pipeline) — Docker
# ---------------------------------------------------------------------------

class TestSelftest:
    """Tests for the selftest() smoke test function."""

    FUTURE_MS = 1700000000000 + 30 * 86400 * 1000  # 30 days from now

    # proxy_health_check returns unhealthy for the initial
    # proxy_existed_before check, then healthy after ensure_proxy.
    @patch("ralph.selftest.DockerSandboxRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandboxRuntime.apply_network_policy")
    @patch("ralph.selftest.DockerSandboxRuntime._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandboxRuntime._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandboxRuntime.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "abc123", "oauth", "127.0.0.1")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_all_checks_pass(self, mock_time, mock_read, mock_ensure_proxy,
                             mock_health, mock_img, mock_resolve, mock_create,
                             mock_policy, mock_run, mock_stop, mock_remove,
                             capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        # sandbox exec calls: proxy reachable (ok), claude (ok), curl google (blocked)
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),    # curl proxy health
            MagicMock(returncode=0, stdout="ok", stderr=""),    # claude via proxy
            MagicMock(returncode=28, stdout="", stderr=""),     # curl google (blocked)
        ]

        rc = selftest("claude", "/fake/dotfiles")
        assert rc == 0

        captured = capsys.readouterr()
        assert "PASS: check token" in captured.out
        assert "PASS: proxy health" in captured.out
        assert "PASS: build image" in captured.out
        assert "PASS: create sandbox" in captured.out
        assert "PASS: network policy" in captured.out
        assert "PASS: proxy reachable from sandbox" in captured.out
        assert "PASS: claude auth via proxy" in captured.out
        assert "PASS: network isolation" in captured.out
        assert "all 9 checks passed" in captured.out
        # Proxy was not running before selftest, so it should be stopped
        mock_stop.assert_called_once_with("claude")

    @patch("ralph.selftest.DockerSandboxRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandboxRuntime.apply_network_policy")
    @patch("ralph.selftest.DockerSandboxRuntime._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandboxRuntime._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandboxRuntime.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check",
           return_value=(True, "abc123", "oauth", "127.0.0.1"))
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_does_not_stop_preexisting_proxy(self, mock_time, mock_read,
                                              mock_ensure_proxy, mock_health,
                                              mock_img, mock_resolve,
                                              mock_create, mock_policy,
                                              mock_run, mock_stop,
                                              mock_remove, capsys):
        """When proxy was already running before selftest, don't stop it."""
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=28, stdout="", stderr=""),
        ]

        rc = selftest("claude", "/fake/dotfiles")
        assert rc == 0
        # Proxy existed before, so stop_proxy should NOT be called
        mock_stop.assert_not_called()

    @patch("ralph.selftest.proxy_health_check",
           return_value=(False, None, None, None))
    @patch("ralph.selftest.read_token_from_keychain", return_value=None)
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_missing_token_aborts_early(self, mock_time, mock_read,
                                        mock_health, capsys):
        rc = selftest("claude", "/fake/dotfiles")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: check token" in captured.out
        assert "selftest aborted" in captured.out

    @patch("ralph.selftest.proxy_health_check",
           return_value=(False, None, None, None))
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_expired_token_aborts_early(self, mock_time, mock_read,
                                        mock_health, capsys):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-old", "expiresAt": past_ms}
        rc = selftest("claude", "/fake/dotfiles")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: check token" in captured.out
        assert "credentials expired" in captured.out

    @patch("ralph.selftest.DockerSandboxRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandboxRuntime.apply_network_policy")
    @patch("ralph.selftest.DockerSandboxRuntime._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandboxRuntime._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandboxRuntime.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "abc123", "oauth", "127.0.0.1")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_cleans_up_sandbox_on_failure(self, mock_time, mock_read,
                                          mock_ensure_proxy, mock_health,
                                          mock_img, mock_resolve, mock_create,
                                          mock_policy, mock_run, mock_stop,
                                          mock_remove):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        # proxy reachable fails, which causes failures but cleanup should still run
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr=""),     # curl proxy health (fail)
            MagicMock(returncode=1, stdout="", stderr=""),     # claude (fail)
            MagicMock(returncode=0, stdout="ok", stderr=""),   # curl google (not blocked)
        ]

        selftest("claude", "/fake/dotfiles")

        # Verify cleanup was called
        mock_remove.assert_called_with("agent-loop-selftest-claude")
        mock_stop.assert_called_once_with("claude")

    @patch("ralph.selftest.DockerSandboxRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandboxRuntime.apply_network_policy")
    @patch("ralph.selftest.DockerSandboxRuntime._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandboxRuntime._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandboxRuntime.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "abc123", "oauth", "127.0.0.1")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_reports_failed_checks(self, mock_time, mock_read,
                                   mock_ensure_proxy, mock_health,
                                   mock_img, mock_resolve, mock_create,
                                   mock_policy, mock_run, mock_stop,
                                   mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        # proxy reachable ok, claude fails, network not blocked
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),   # curl proxy health
            MagicMock(returncode=1, stdout="", stderr="err"),  # claude fails
            MagicMock(returncode=0, stdout="ok", stderr=""),   # curl google (NOT blocked)
        ]

        rc = selftest("claude", "/fake/dotfiles")
        assert rc == 1

        captured = capsys.readouterr()
        assert "FAIL: claude auth via proxy" in captured.out
        assert "FAIL: network isolation" in captured.out
        assert "2/9 checks failed" in captured.out

    @patch("ralph.selftest.DockerSandboxRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.DockerSandboxRuntime.ensure_image", side_effect=RuntimeError("build failed"))
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "abc123", "oauth", "127.0.0.1")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_image_build_failure_aborts(self, mock_time, mock_read,
                                       mock_ensure_proxy, mock_health,
                                       mock_img, mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        rc = selftest("claude", "/fake/dotfiles")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: build image" in captured.out
        assert "selftest aborted" in captured.out
        # Proxy should still be stopped (was started, didn't exist before)
        mock_stop.assert_called_once_with("claude")

    @patch("ralph.selftest.DockerSandboxRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.DockerSandboxRuntime.apply_network_policy")
    @patch("ralph.selftest.DockerSandboxRuntime._docker_sandbox_create",
           side_effect=subprocess.CalledProcessError(1, "docker"))
    @patch("ralph.selftest.DockerSandboxRuntime._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandboxRuntime.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "abc123", "oauth", "127.0.0.1")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_sandbox_create_failure_aborts(self, mock_time, mock_read,
                                          mock_ensure_proxy, mock_health,
                                          mock_img, mock_resolve, mock_create,
                                          mock_policy, mock_stop,
                                          mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        rc = selftest("claude", "/fake/dotfiles")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: create sandbox" in captured.out
        assert "selftest aborted" in captured.out
        # Proxy should be stopped (didn't exist before)
        mock_stop.assert_called_once_with("claude")

    @patch("ralph.selftest.DockerSandboxRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandboxRuntime.apply_network_policy")
    @patch("ralph.selftest.DockerSandboxRuntime._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandboxRuntime._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandboxRuntime.ensure_project_image",
           return_value="agent-loop-sandbox-claude-myproject:vdeadbeef")
    @patch("ralph.selftest.DockerSandboxRuntime.find_project_config",
           return_value=("dependencies", "/proj/.agent-loop/dependencies"))
    @patch("ralph.selftest.DockerSandboxRuntime.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "abc123", "oauth", "127.0.0.1")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_project_image_check_with_dependencies(
            self, mock_time, mock_read, mock_ensure_proxy, mock_health,
            mock_img, mock_find_config, mock_proj_img, mock_resolve,
            mock_create, mock_policy, mock_run, mock_stop, mock_remove,
            capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),    # curl proxy health
            MagicMock(returncode=0, stdout="ok", stderr=""),    # claude via proxy
            MagicMock(returncode=28, stdout="", stderr=""),     # curl google (blocked)
        ]

        rc = selftest("claude", "/fake/dotfiles")
        assert rc == 0

        captured = capsys.readouterr()
        assert "PASS: build project image" in captured.out
        assert "agent-loop-sandbox-claude-myproject:vdeadbeef" in captured.out
        assert "all 10 checks passed" in captured.out

    @patch("ralph.selftest.DockerSandboxRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandboxRuntime.apply_network_policy")
    @patch("ralph.selftest.DockerSandboxRuntime._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandboxRuntime._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandboxRuntime.ensure_project_image",
           return_value="agent-loop-sandbox-claude-myproject:vdeadbeef")
    @patch("ralph.selftest.DockerSandboxRuntime.find_project_config",
           return_value=("dockerfile", "/proj/.agent-loop/Dockerfile.sandbox"))
    @patch("ralph.selftest.DockerSandboxRuntime.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "abc123", "oauth", "127.0.0.1")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_project_image_check_with_dockerfile(
            self, mock_time, mock_read, mock_ensure_proxy, mock_health,
            mock_img, mock_find_config, mock_proj_img, mock_resolve,
            mock_create, mock_policy, mock_run, mock_stop, mock_remove,
            capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),    # curl proxy health
            MagicMock(returncode=0, stdout="ok", stderr=""),    # claude via proxy
            MagicMock(returncode=28, stdout="", stderr=""),     # curl google (blocked)
        ]

        rc = selftest("claude", "/fake/dotfiles")
        assert rc == 0

        captured = capsys.readouterr()
        assert "PASS: build project image" in captured.out
        assert "all 10 checks passed" in captured.out

    @patch("ralph.selftest.DockerSandboxRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandboxRuntime.apply_network_policy")
    @patch("ralph.selftest.DockerSandboxRuntime._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandboxRuntime._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandboxRuntime.find_project_config", return_value=None)
    @patch("ralph.selftest.DockerSandboxRuntime.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "abc123", "oauth", "127.0.0.1")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_project_image_skipped_when_no_config(
            self, mock_time, mock_read, mock_ensure_proxy, mock_health,
            mock_img, mock_find_config, mock_resolve, mock_create,
            mock_policy, mock_run, mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),    # curl proxy health
            MagicMock(returncode=0, stdout="ok", stderr=""),    # claude via proxy
            MagicMock(returncode=28, stdout="", stderr=""),     # curl google (blocked)
        ]

        rc = selftest("claude", "/fake/dotfiles")
        assert rc == 0

        captured = capsys.readouterr()
        assert "PASS: build project image" not in captured.out
        assert "skipping project image check" in captured.out
        assert "all 9 checks passed" in captured.out

    @patch("ralph.selftest.DockerSandboxRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandboxRuntime.apply_network_policy")
    @patch("ralph.selftest.DockerSandboxRuntime._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandboxRuntime.ensure_project_image",
           side_effect=RuntimeError("project build failed"))
    @patch("ralph.selftest.DockerSandboxRuntime.find_project_config",
           return_value=("dependencies", "/proj/.agent-loop/dependencies"))
    @patch("ralph.selftest.DockerSandboxRuntime.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "abc123", "oauth", "127.0.0.1")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_project_image_build_failure_reported(
            self, mock_time, mock_read, mock_ensure_proxy, mock_health,
            mock_img, mock_find_config, mock_proj_img, mock_create,
            mock_policy, mock_run, mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=28, stdout="", stderr=""),
        ]

        rc = selftest("claude", "/fake/dotfiles")
        assert rc == 1

        captured = capsys.readouterr()
        assert "FAIL: build project image" in captured.out
        assert "project build failed" in captured.out
        assert "selftest aborted" in captured.out
        mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# selftest — Tart
# ---------------------------------------------------------------------------

class TestSelftestTart:
    """Tests for tart-specific selftest path."""

    FUTURE_MS = 1700000000000 + 30 * 86400 * 1000  # 30 days from now

    @patch("ralph.selftest.TartRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.subprocess.Popen")
    @patch("ralph.selftest.TartRuntime._wait_for_guest_agent")
    @patch("ralph.selftest.TartRuntime.proxy_host", return_value="192.168.64.1")
    @patch("ralph.selftest.TartRuntime.ensure_image",
           return_value="agent-loop-template-claude-abc123")
    @patch("ralph.selftest.TartRuntime.check_prerequisites", return_value=[])
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "abc123", "oauth", "127.0.0.1")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_all_checks_pass(self, mock_time, mock_read, mock_ensure_proxy,
                              mock_health, mock_prereq, mock_img,
                              mock_proxy_host, mock_wait, mock_popen,
                              mock_run, mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test",
                                  "expiresAt": self.FUTURE_MS}
        mock_popen.return_value = MagicMock()
        # subprocess.run calls: tart clone (ok), proxy reachable (ok),
        # claude auth (ok)
        mock_run.side_effect = [
            MagicMock(returncode=0),   # tart clone
            MagicMock(returncode=0),   # curl proxy health from VM
            MagicMock(returncode=0),   # claude via proxy
        ]

        rc = selftest("claude", "/fake/dotfiles", runtime_type="tart")
        assert rc == 0

        captured = capsys.readouterr()
        assert "selftest starting (tart)" in captured.out
        assert "PASS: check token" in captured.out
        assert "PASS: prerequisites" in captured.out
        assert "PASS: proxy health" in captured.out
        assert "PASS: build template" in captured.out
        assert "PASS: create test VM" in captured.out
        assert "PASS: tart exec" in captured.out
        assert "PASS: proxy reachable from VM" in captured.out
        assert "PASS: claude auth via proxy" in captured.out
        assert "network isolation — skipping" in captured.out
        # proxy was not running before, so should be stopped
        mock_stop.assert_called_once_with("claude")

    @patch("ralph.selftest.TartRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.TartRuntime.ensure_image",
           side_effect=RuntimeError("clone failed"))
    @patch("ralph.selftest.TartRuntime.check_prerequisites", return_value=[])
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "abc123", "oauth", "127.0.0.1")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_template_build_failure_aborts(self, mock_time, mock_read,
                                            mock_ensure_proxy, mock_health,
                                            mock_prereq, mock_img,
                                            mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test",
                                  "expiresAt": self.FUTURE_MS}
        rc = selftest("claude", "/fake/dotfiles", runtime_type="tart")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: build template" in captured.out
        assert "selftest aborted" in captured.out

    @patch("ralph.selftest.TartRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.TartRuntime.check_prerequisites",
           return_value=["tart is not installed"])
    @patch("ralph.selftest.proxy_health_check",
           return_value=(False, None, None, None))
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_prerequisites_failure_aborts(self, mock_time, mock_read,
                                          mock_health, mock_prereq,
                                          mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test",
                                  "expiresAt": self.FUTURE_MS}
        rc = selftest("claude", "/fake/dotfiles", runtime_type="tart")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: prerequisites" in captured.out
        assert "prerequisites not met" in captured.out


# ---------------------------------------------------------------------------
# selftest — Docker Container
# ---------------------------------------------------------------------------

class TestSelftestDockerContainer:
    """Tests for docker-container-specific selftest path."""

    FUTURE_MS = 1700000000000 + 30 * 86400 * 1000  # 30 days from now

    @patch("ralph.selftest.stop_network_proxy")
    @patch("ralph.selftest.stop_docker_proxy")
    @patch("ralph.selftest.DockerContainerRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerContainerRuntime._ensure_network")
    @patch("ralph.selftest.DockerContainerRuntime._resolve_git_common_dir",
           return_value="/fake/.git")
    @patch("ralph.selftest.DockerContainerRuntime.ensure_image",
           return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.ensure_network_proxy")
    @patch("ralph.selftest.network_proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "def456", frozenset(), "127.0.0.1")])
    @patch("ralph.selftest.docker_proxy_health_check",
           side_effect=[(False, None, None), (True, "abc123", "127.0.0.1")])
    @patch("ralph.selftest.ensure_docker_proxy")
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "abc123", "oauth", "127.0.0.1")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_all_checks_pass(self, mock_time, mock_read, mock_ensure_proxy,
                              mock_proxy_health, mock_ensure_docker_proxy,
                              mock_docker_health, mock_network_health,
                              mock_ensure_network, mock_img, mock_resolve,
                              mock_network, mock_run, mock_stop_proxy,
                              mock_remove, mock_stop_docker,
                              mock_stop_network, capsys):
        mock_read.return_value = {"accessToken": "sk-test",
                                  "expiresAt": self.FUTURE_MS}
        # subprocess.run calls:
        # 1. docker run -d (create container)
        # 2. curl credential proxy health
        # 3. claude via proxy
        # 4. curl google (blocked)
        # 5. curl docker socket proxy from container
        # 6. curl network proxy from container
        # 7. curl allowed host (api.anthropic.com)
        # 8. curl non-allowed host (example.com, blocked)
        mock_run.side_effect = [
            MagicMock(returncode=0),   # docker run -d
            MagicMock(returncode=0, stdout="ok", stderr=""),   # curl proxy
            MagicMock(returncode=0, stdout="ok", stderr=""),   # claude
            MagicMock(returncode=28, stdout="", stderr=""),    # curl google
            MagicMock(returncode=0, stdout="ok", stderr=""),   # curl docker proxy
            MagicMock(returncode=0, stdout="ok", stderr=""),   # curl network proxy
            MagicMock(returncode=0, stdout="ok", stderr=""),   # curl allowed host
            MagicMock(returncode=28, stdout="", stderr=""),    # curl non-allowed
        ]

        rc = selftest("claude", "/fake/dotfiles",
                       runtime_type="docker-container")
        assert rc == 0

        captured = capsys.readouterr()
        assert "selftest starting (docker-container)" in captured.out
        assert "PASS: check token" in captured.out
        assert "PASS: docker socket proxy" in captured.out
        assert "PASS: network proxy" in captured.out
        assert "PASS: build image" in captured.out
        assert "PASS: create container" in captured.out
        assert "PASS: proxy reachable from container" in captured.out
        assert "PASS: claude auth via proxy" in captured.out
        assert "PASS: network isolation" in captured.out
        assert "PASS: docker socket proxy from container" in captured.out
        assert "PASS: network proxy from container" in captured.out
        assert "PASS: allowed host via proxy" in captured.out
        assert "PASS: non-allowed host blocked" in captured.out
        # Docker proxy didn't exist before, so should be stopped
        mock_stop_docker.assert_called_once()
        # Network proxy didn't exist before, so should be stopped
        mock_stop_network.assert_called_once()

    @patch("ralph.selftest.stop_network_proxy")
    @patch("ralph.selftest.stop_docker_proxy")
    @patch("ralph.selftest.DockerContainerRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.DockerContainerRuntime.ensure_image",
           side_effect=RuntimeError("build failed"))
    @patch("ralph.selftest.ensure_network_proxy")
    @patch("ralph.selftest.network_proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "def456", frozenset(), "127.0.0.1")])
    @patch("ralph.selftest.docker_proxy_health_check",
           side_effect=[(False, None, None), (True, "abc123", "127.0.0.1")])
    @patch("ralph.selftest.ensure_docker_proxy")
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "abc123", "oauth", "127.0.0.1")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_image_build_failure_aborts(self, mock_time, mock_read,
                                         mock_ensure_proxy, mock_proxy_health,
                                         mock_ensure_docker, mock_docker_health,
                                         mock_network_health,
                                         mock_ensure_network, mock_img,
                                         mock_stop_proxy,
                                         mock_remove, mock_stop_docker,
                                         mock_stop_network, capsys):
        mock_read.return_value = {"accessToken": "sk-test",
                                  "expiresAt": self.FUTURE_MS}
        rc = selftest("claude", "/fake/dotfiles",
                       runtime_type="docker-container")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: build image" in captured.out
        assert "selftest aborted" in captured.out

    @patch("ralph.selftest.stop_network_proxy")
    @patch("ralph.selftest.stop_docker_proxy")
    @patch("ralph.selftest.DockerContainerRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.network_proxy_health_check",
           return_value=(False, None, None, None))
    @patch("ralph.selftest.docker_proxy_health_check",
           return_value=(False, None, None))
    @patch("ralph.selftest.ensure_docker_proxy",
           side_effect=RuntimeError("socket not available"))
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "abc123", "oauth", "127.0.0.1")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_docker_proxy_failure_aborts(self, mock_time, mock_read,
                                          mock_ensure_proxy, mock_proxy_health,
                                          mock_ensure_docker,
                                          mock_docker_health,
                                          mock_network_health,
                                          mock_stop_proxy, mock_remove,
                                          mock_stop_docker,
                                          mock_stop_network, capsys):
        mock_read.return_value = {"accessToken": "sk-test",
                                  "expiresAt": self.FUTURE_MS}
        rc = selftest("claude", "/fake/dotfiles",
                       runtime_type="docker-container")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: docker socket proxy" in captured.out

    @patch("ralph.selftest.stop_network_proxy")
    @patch("ralph.selftest.stop_docker_proxy")
    @patch("ralph.selftest.DockerContainerRuntime.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.network_proxy_health_check",
           side_effect=[(False, None, None, None), (False, None, None, None)])
    @patch("ralph.selftest.ensure_network_proxy",
           side_effect=RuntimeError("proxy failed"))
    @patch("ralph.selftest.docker_proxy_health_check",
           side_effect=[(False, None, None), (True, "abc123", "127.0.0.1")])
    @patch("ralph.selftest.ensure_docker_proxy")
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None, None, None),
                        (True, "abc123", "oauth", "127.0.0.1")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_network_proxy_failure_aborts(self, mock_time, mock_read,
                                           mock_ensure_proxy, mock_proxy_health,
                                           mock_ensure_docker,
                                           mock_docker_health,
                                           mock_ensure_network,
                                           mock_network_health,
                                           mock_stop_proxy, mock_remove,
                                           mock_stop_docker,
                                           mock_stop_network, capsys):
        mock_read.return_value = {"accessToken": "sk-test",
                                  "expiresAt": self.FUTURE_MS}
        rc = selftest("claude", "/fake/dotfiles",
                       runtime_type="docker-container")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: network proxy" in captured.out


# ---------------------------------------------------------------------------
# selftest — nono
# ---------------------------------------------------------------------------

FUTURE_MS = 1700000000000 + 30 * 86400 * 1000  # 30 days from now

# What each command the nono selftest runs looks like when everything is
# healthy: an exit code, or (exit code, stdout, stderr).
NONO_PASSING = {
    "git-init": 0,
    "git-host": (0, "abc123def4567890\n", ""),
    "capture": (0, "sk-real-token", ""),
    "profile": 0,
    "env": (0, "PATH=/usr/bin:/bin\n"
               "ANTHROPIC_BASE_URL=http://127.0.0.1:41234/anthropic\n"
               "CLAUDE_CODE_OAUTH_TOKEN=nono-placeholder\n", ""),
    "claude": 0,
    "curl": 28,
    "loopback": 0,
    "token-store": 1,
    "docker": 0,
    "probe-claude": 1,
    "probe-gitconfig": 1,
    "probe-git-hooks": 1,
    "probe-git-config": 1,
    "commit": 0,
    "spec-save": 0,
    "git-verify": (0, "abc123def456\n", ""),
    "git-resolve": (0, ".git/worktrees/selftest\n", ""),
    "git-signature": (0, "N\n", ""),
}


def _classify(cmd):
    """Label a subprocess command by the check it belongs to."""
    cmd = list(cmd)
    if cmd[0] == "nono":
        inner = cmd[cmd.index("--") + 1:]
        head = inner[0]
        if head in ("true", "env", "claude", "curl", "docker"):
            return {"true": "profile"}.get(head, head)
        if head in ("security", "secret-tool"):
            return "token-store"
        if head == "sh":
            script = inner[2]
            if script.startswith("git "):
                return "commit"
            if "/hooks/" in script:
                return "probe-git-hooks"
            if script.rstrip().endswith("/config"):
                return "probe-git-config"
            if "gitconfig" in script:
                return "probe-gitconfig"
            return "probe-claude"
        if "os.replace" in " ".join(inner):
            return "spec-save"
        return "loopback"
    if cmd[:2] == ["git", "init"]:
        return "git-init"
    if "--format=%G?" in cmd:
        return "git-signature"
    if "--git-dir" in cmd or "--git-common-dir" in cmd:
        # NonoRuntime resolving the repo layout, not a selftest check.
        return "git-resolve"
    if "rev-parse" in cmd:
        return "git-verify"
    if cmd[0] == "git":
        return "git-host"
    return "capture"


class FakeRun:
    """subprocess.run stand-in dispatching on the command being run."""

    def __init__(self, **overrides):
        self.results = dict(NONO_PASSING)
        self.results.update(overrides)
        self.calls = []

    def __call__(self, cmd, **kwargs):
        kind = _classify(cmd)
        self.calls.append((kind, list(cmd), kwargs))
        outcome = self.results[kind]
        if isinstance(outcome, Exception):
            raise outcome
        if kind == "spec-save" and outcome == 0:
            # Stand in for the sandboxed interpreter actually saving.
            with open(cmd[-1], "w") as f:
                f.write("- [x] task\n")
        if isinstance(outcome, tuple):
            rc, out, err = outcome
        else:
            rc, out, err = outcome, "", ""
        return MagicMock(returncode=rc, stdout=out, stderr=err)

    @property
    def kinds(self):
        return [kind for kind, _, _ in self.calls]

    def call(self, kind):
        """First (cmd, kwargs) recorded for a kind, or (None, None)."""
        for seen, cmd, kwargs in self.calls:
            if seen == kind:
                return cmd, kwargs
        return None, None


@pytest.fixture
def nono_host(tmp_path, monkeypatch):
    """A fake host: temp HOME, no real nono, docker, git, or keystore."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("ralph.runtime.nono.ensure_docker_proxy_socket",
                        MagicMock())
    monkeypatch.setattr("ralph.runtime.nono.ephemeral_port_range",
                        lambda *a, **k: (49152, 65535))
    monkeypatch.setattr("ralph.runtime.nono.shutil.which",
                        lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(NonoRuntime, "_nono_version",
                        lambda self: (0, 77, 0))
    monkeypatch.setattr(NonoRuntime, "_resolve_git_common_dir",
                        staticmethod(lambda path: os.path.join(path, ".git")))
    # SANDBOX_STATE_DIR is captured at import time, so it ignores HOME.
    monkeypatch.setattr(NonoRuntime, "_touch_sandbox_timestamp",
                        lambda self, name: None)
    monkeypatch.setattr(NonoRuntime, "_remove_sandbox_timestamp",
                        lambda self, name: None)
    return tmp_path


def run_nono_selftest(fake_run, socket_healthy=False, token=None, **kwargs):
    """Run selftest --runtime nono against a fake host. Returns the rc."""
    token = token or {"accessToken": "sk-real-token", "expiresAt": FUTURE_MS}
    with patch("ralph.selftest.subprocess.run", fake_run), \
            patch("ralph.selftest.read_token_from_keychain",
                  return_value=token), \
            patch("ralph.selftest.docker_proxy_socket_health_check",
                  return_value=(socket_healthy, "abc", "unix:/x")), \
            patch("ralph.selftest.time.time", return_value=1700000000.0), \
            patch("ralph.selftest.stop_docker_proxy") as stop:
        rc = selftest("claude", "/fake/dotfiles", runtime_type="nono",
                      **kwargs)
    return rc, stop


class TestSelftestNono:
    """Tests for the nono-specific selftest path."""

    def test_all_checks_pass(self, nono_host, capsys):
        fake = FakeRun()
        rc, _ = run_nono_selftest(fake)
        assert rc == 0

        out = capsys.readouterr().out
        assert "selftest starting (nono)" in out
        for name in ("check token", "prerequisites", "create sandbox",
                     "nono profile accepted", "credential capture command",
                     "credential proxy env", "real token hidden from sandbox",
                     "claude auth via nono", "network isolation",
                     "loopback ports", "token store denied",
                     "docker via socket proxy", "home config write denied",
                     "git hooks and config denied", "git commit in worktree",
                     "commit is unsigned", "spec editable by the agent"):
            assert f"PASS: {name}" in out
        assert "all 17 checks passed" in out
        assert "nono 0.77.0" in out

    def test_never_starts_the_credential_proxy(self, nono_host):
        """nono injects credentials itself — ralph's TCP proxy stays down."""
        with patch("ralph.selftest.ensure_proxy") as ensure, \
                patch("ralph.selftest.proxy_health_check") as health, \
                patch("ralph.selftest.stop_proxy") as stop:
            rc, _ = run_nono_selftest(FakeRun())
        assert rc == 0
        ensure.assert_not_called()
        health.assert_not_called()
        stop.assert_not_called()

    def test_checks_run_through_the_generated_profile(self, nono_host):
        fake = FakeRun()
        rc, _ = run_nono_selftest(fake)
        assert rc == 0

        cmd, kwargs = fake.call("claude")
        assert cmd[:2] == ["nono", "run"]
        assert cmd[2] == "-p"
        profile_path = cmd[3]
        assert profile_path.endswith("/profile.json")
        assert "--no-rollback" in cmd
        assert "--suppress-save-prompt" in cmd
        workdir = cmd[cmd.index("--workdir") + 1]
        assert cmd[cmd.index("--") + 1:] == [
            "claude", "-p", "reply with OK", "--model", "haiku"]
        # Filtered mode, so no --allow-net escape hatch
        assert "--allow-net" not in cmd
        # The agent gets the iteration environment, not ralph's own
        assert kwargs["env"]["GIT_CONFIG_GLOBAL"].endswith("/gitconfig")
        assert kwargs["env"]["DOCKER_HOST"].startswith("unix://")
        assert "ANTHROPIC_BASE_URL" not in kwargs["env"]
        assert kwargs["cwd"] == workdir

    def test_uses_the_profiles_own_capture_command(self, nono_host):
        fake = FakeRun()
        rc, _ = run_nono_selftest(fake)
        assert rc == 0
        cmd, _ = fake.call("capture")
        assert cmd[0].endswith("/scripts/ralph")
        assert cmd[1:] == ["get-token", "--agent", "claude", "--auth", "oauth"]

    def test_cleans_up_state_dir_and_repo(self, nono_host):
        fake = FakeRun()
        rc, _ = run_nono_selftest(fake)
        assert rc == 0

        cmd, _ = fake.call("profile")
        workdir = cmd[cmd.index("--workdir") + 1]
        state_dir = os.path.dirname(cmd[3])
        assert not os.path.exists(workdir)
        assert not os.path.exists(state_dir)

    def test_stops_a_docker_proxy_it_started(self, nono_host):
        rc, stop = run_nono_selftest(FakeRun(), socket_healthy=False)
        assert rc == 0
        stop.assert_called_once()
        pid_file = stop.call_args.kwargs["pid_file"]
        assert pid_file.endswith("/docker-proxy-sock.pid")

    def test_leaves_a_preexisting_docker_proxy_running(self, nono_host):
        rc, stop = run_nono_selftest(FakeRun(), socket_healthy=True)
        assert rc == 0
        stop.assert_not_called()

    def test_missing_token_aborts_before_any_command(self, nono_host, capsys):
        fake = FakeRun()
        with patch("ralph.selftest.subprocess.run", fake), \
                patch("ralph.selftest.read_token_from_keychain",
                      return_value=None):
            rc = selftest("claude", "/fake/dotfiles", runtime_type="nono")
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL: check token" in out
        assert "selftest aborted" in out
        assert fake.calls == []

    def test_prerequisites_failure_aborts(self, nono_host, capsys,
                                          monkeypatch):
        monkeypatch.setattr(NonoRuntime, "check_prerequisites",
                            lambda self: ["nono is not installed"])
        fake = FakeRun()
        rc, _ = run_nono_selftest(fake)
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL: prerequisites — nono is not installed" in out
        assert "prerequisites not met" in out
        assert fake.calls == []

    def test_rejected_profile_aborts_remaining_checks(self, nono_host,
                                                      capsys):
        fake = FakeRun(profile=(1, "", "unknown field `credential_capture`"))
        rc, _ = run_nono_selftest(fake)
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL: nono profile accepted — unknown field" in out
        assert "profile required for remaining checks" in out
        # Nothing after the profile check ran, but cleanup still did
        assert "claude" not in fake.kinds
        assert "cleaning up test sandbox" in out

    def test_reports_leaked_token(self, nono_host, capsys):
        fake = FakeRun(env=(0, "ANTHROPIC_BASE_URL=http://127.0.0.1:41234/x\n"
                               "CLAUDE_CODE_OAUTH_TOKEN=sk-real-token\n", ""))
        rc, _ = run_nono_selftest(fake)
        assert rc == 1
        out = capsys.readouterr().out
        assert ("FAIL: real token hidden from sandbox "
                "— CLAUDE_CODE_OAUTH_TOKEN holds the real token") in out

    def test_reports_unset_token_var(self, nono_host, capsys):
        fake = FakeRun(env=(0, "ANTHROPIC_BASE_URL=http://127.0.0.1:1/x\n",
                            ""))
        rc, _ = run_nono_selftest(fake)
        assert rc == 1
        out = capsys.readouterr().out
        assert "CLAUDE_CODE_OAUTH_TOKEN not set in sandbox" in out

    def test_reports_base_url_not_on_loopback(self, nono_host, capsys):
        fake = FakeRun(env=(0, "ANTHROPIC_BASE_URL=https://api.anthropic.com\n"
                               "CLAUDE_CODE_OAUTH_TOKEN=placeholder\n", ""))
        rc, _ = run_nono_selftest(fake)
        assert rc == 1
        out = capsys.readouterr().out
        assert ("FAIL: credential proxy env "
                "— https://api.anthropic.com") in out

    def test_reports_unblocked_network(self, nono_host, capsys):
        fake = FakeRun(curl=0)
        rc, _ = run_nono_selftest(fake)
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL: network isolation" in out
        assert "network filter ineffective" in out

    def test_reports_readable_token_store(self, nono_host, capsys):
        fake = FakeRun(**{"token-store": 0})
        rc, _ = run_nono_selftest(fake)
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL: token store denied" in out
        assert "token store IS readable" in out

    def test_token_store_command_matches_the_platform(self, nono_host,
                                                      monkeypatch):
        monkeypatch.setattr("ralph.runtime.nono.sys.platform", "darwin")
        fake = FakeRun()
        rc, _ = run_nono_selftest(fake)
        assert rc == 0
        cmd, _ = fake.call("token-store")
        assert cmd[cmd.index("--") + 1:] == [
            "security", "find-generic-password",
            "-s", "claude-token", "-a", "agent-loop", "-w"]

    def test_reports_writable_home_config(self, nono_host, capsys):
        (nono_host / ".claude").mkdir()
        fake = FakeRun(**{"probe-claude": 0, "probe-gitconfig": 0})
        rc, _ = run_nono_selftest(fake)
        assert rc == 1
        out = capsys.readouterr().out
        assert ("FAIL: home config write denied — writable from the sandbox: "
                "~/.claude, ~/.gitconfig") in out

    def test_probes_home_when_claude_dir_is_absent(self, nono_host, capsys):
        """Writing into a missing ~/.claude would fail for the wrong reason."""
        fake = FakeRun()
        rc, _ = run_nono_selftest(fake)
        assert rc == 0
        out = capsys.readouterr().out
        assert "PASS: home config write denied — ~ and ~/.gitconfig" in out
        cmd, _ = fake.call("probe-claude")
        assert cmd[cmd.index("--") + 1:][2].endswith(
            f"{nono_host}/.ralph-selftest-probe")

    def test_probe_file_left_behind_counts_as_writable(self, nono_host,
                                                       capsys):
        """A non-zero exit still fails the check if the file appeared."""
        probe = nono_host / ".claude" / "ralph-selftest-probe"
        probe.parent.mkdir()
        probe.write_text("")
        fake = FakeRun()
        rc, _ = run_nono_selftest(fake)
        assert rc == 1
        out = capsys.readouterr().out
        assert "writable from the sandbox: ~/.claude" in out
        # ...and the stray probe is removed
        assert not probe.exists()

    def test_removes_a_gitconfig_the_probe_created(self, nono_host):
        """An ineffective sandbox leaves an empty ~/.gitconfig behind."""
        gitconfig = nono_host / ".gitconfig"
        fake = FakeRun(**{"probe-gitconfig": 0})
        original = fake.__call__

        def run(cmd, **kwargs):
            result = original(cmd, **kwargs)
            if _classify(cmd) == "probe-gitconfig":
                gitconfig.write_text("")
            return result

        rc, _ = run_nono_selftest(run)
        assert rc == 1
        assert not gitconfig.exists()

    def test_keeps_an_existing_gitconfig(self, nono_host):
        gitconfig = nono_host / ".gitconfig"
        gitconfig.write_text("[user]\n\tname = Real User\n")
        rc, _ = run_nono_selftest(FakeRun())
        assert rc == 0
        assert gitconfig.read_text() == "[user]\n\tname = Real User\n"

    def test_reports_failed_commit(self, nono_host, capsys):
        fake = FakeRun(commit=(1, "", "nothing to commit"))
        rc, _ = run_nono_selftest(fake)
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL: git commit in worktree — nothing to commit" in out

    def test_commit_unverifiable_on_host_fails(self, nono_host, capsys):
        fake = FakeRun(**{"git-verify": (128, "", ""),
                          "git-signature": (128, "", "")})
        rc, _ = run_nono_selftest(fake)
        assert rc == 1
        assert "FAIL: git commit in worktree" in capsys.readouterr().out

    def test_timeout_is_reported_and_cleaned_up(self, nono_host, capsys):
        fake = FakeRun(claude=subprocess.TimeoutExpired("claude", 60))
        rc, _ = run_nono_selftest(fake)
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL: timeout — a check timed out" in out
        assert "cleaning up test sandbox" in out

    def test_profile_is_written_with_the_spec_shape(self, nono_host):
        """The profile handed to nono is the one an iteration would get."""
        written = {}

        class CapturingRun(FakeRun):
            def __call__(self, cmd, **kwargs):
                if _classify(cmd) == "profile" and not written:
                    with open(list(cmd)[3]) as f:
                        written.update(json.load(f))
                return super().__call__(cmd, **kwargs)

        rc, _ = run_nono_selftest(CapturingRun())
        assert rc == 0
        assert written["meta"]["name"].startswith("ralph-agent-loop-claude-")
        assert written["network"]["open_port_range"] == [[49152, 65535]]
        assert (written["network"]["custom_credentials"]["anthropic"]
                ["env_var"]) == "CLAUDE_CODE_OAUTH_TOKEN"
        # The real token is never written into the profile
        assert "sk-real-token" not in json.dumps(written)

    def test_api_key_mode_checks_its_own_token_var(self, nono_host, capsys):
        fake = FakeRun(env=(0, "ANTHROPIC_BASE_URL=http://127.0.0.1:1/x\n"
                               "ANTHROPIC_API_KEY=placeholder\n", ""))
        rc, _ = run_nono_selftest(
            fake, token={"accessToken": "sk-ant-key", "expiresAt": FUTURE_MS},
            auth_mode="api_key")
        assert rc == 0
        out = capsys.readouterr().out
        assert "PASS: check token — API key stored" in out
        assert "ANTHROPIC_API_KEY holds a placeholder" in out
        cmd, _ = fake.call("capture")
        assert cmd[-1] == "api-key"

    def test_gateway_mode_routes_to_the_stored_base_url(self, nono_host):
        written = {}

        class CapturingRun(FakeRun):
            def __call__(self, cmd, **kwargs):
                if _classify(cmd) == "profile" and not written:
                    with open(list(cmd)[3]) as f:
                        written.update(json.load(f))
                return super().__call__(cmd, **kwargs)

        fake = CapturingRun(
            env=(0, "ANTHROPIC_BASE_URL=http://127.0.0.1:1/x\n"
                    "ANTHROPIC_AUTH_TOKEN=placeholder\n", ""))
        rc, _ = run_nono_selftest(
            fake,
            token={"accessToken": "sk-gw", "expiresAt": FUTURE_MS,
                   "baseUrl": "https://gateway.example.com"},
            auth_mode="gateway")
        assert rc == 0
        assert (written["network"]["custom_credentials"]["anthropic"]
                ["upstream"]) == "https://gateway.example.com"

    def test_loopback_probe_runs_the_ralph_interpreter(self, nono_host):
        fake = FakeRun()
        rc, _ = run_nono_selftest(fake)
        assert rc == 0
        cmd, _ = fake.call("loopback")
        inner = cmd[cmd.index("--") + 1:]
        assert inner[0] == sys.executable
        assert inner[1] == "-c"
        assert "127.0.0.1" in inner[2]


# ---------------------------------------------------------------------------
# Shared reporting helpers
# ---------------------------------------------------------------------------

class TestReportingHelpers:
    def test_first_line_skips_blanks(self):
        assert _first_line("\n\n  boom  \nnext\n") == "boom"

    def test_first_line_of_empty_output(self):
        assert _first_line("") == ""
        assert _first_line(None) == ""

    def test_parse_env_splits_on_first_equals(self):
        assert _parse_env("A=1\nB=x=y\n") == {"A": "1", "B": "x=y"}

    def test_parse_env_ignores_continuation_lines(self):
        assert _parse_env("A=one\ntwo\nB=3\n") == {"A": "one", "B": "3"}

    def test_parse_env_keeps_the_first_binding(self):
        assert _parse_env("A=1\nA=2\n") == {"A": "1"}

    def test_summarize_all_passed(self, capsys):
        assert _summarize([True, True]) == 0
        assert "all 2 checks passed" in capsys.readouterr().out

    def test_summarize_counts_failures(self, capsys):
        assert _summarize([True, False, False]) == 1
        assert "2/3 checks failed" in capsys.readouterr().out

