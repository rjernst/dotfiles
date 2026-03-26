"""Tests for ralph.selftest — selftest orchestration."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ralph.selftest import _SelftestAbort, selftest


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

    # proxy_health_check returns (False, None) for the initial
    # proxy_existed_before check, then (True, "v123") after ensure_proxy.
    @patch("ralph.selftest.DockerSandbox.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandbox.apply_network_policy")
    @patch("ralph.selftest.DockerSandbox._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
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

    @patch("ralph.selftest.DockerSandbox.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandbox.apply_network_policy")
    @patch("ralph.selftest.DockerSandbox._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check", return_value=(True, "abc123"))
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

    @patch("ralph.selftest.proxy_health_check", return_value=(False, None))
    @patch("ralph.selftest.read_token_from_keychain", return_value=None)
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_missing_token_aborts_early(self, mock_time, mock_read,
                                        mock_health, capsys):
        rc = selftest("claude", "/fake/dotfiles")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: check token" in captured.out
        assert "selftest aborted" in captured.out

    @patch("ralph.selftest.proxy_health_check", return_value=(False, None))
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
        assert "token expired" in captured.out

    @patch("ralph.selftest.DockerSandbox.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandbox.apply_network_policy")
    @patch("ralph.selftest.DockerSandbox._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
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

    @patch("ralph.selftest.DockerSandbox.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandbox.apply_network_policy")
    @patch("ralph.selftest.DockerSandbox._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
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

    @patch("ralph.selftest.DockerSandbox.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.DockerSandbox.ensure_image", side_effect=RuntimeError("build failed"))
    @patch("ralph.selftest.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
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

    @patch("ralph.selftest.DockerSandbox.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.DockerSandbox.apply_network_policy")
    @patch("ralph.selftest.DockerSandbox._docker_sandbox_create",
           side_effect=subprocess.CalledProcessError(1, "docker"))
    @patch("ralph.selftest.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
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

    @patch("ralph.selftest.DockerSandbox.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandbox.apply_network_policy")
    @patch("ralph.selftest.DockerSandbox._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandbox.ensure_project_image",
           return_value="agent-loop-sandbox-claude-myproject:vdeadbeef")
    @patch("ralph.selftest.DockerSandbox.find_project_config",
           return_value=("dependencies", "/proj/.agent-loop/dependencies"))
    @patch("ralph.selftest.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
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

    @patch("ralph.selftest.DockerSandbox.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandbox.apply_network_policy")
    @patch("ralph.selftest.DockerSandbox._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandbox.ensure_project_image",
           return_value="agent-loop-sandbox-claude-myproject:vdeadbeef")
    @patch("ralph.selftest.DockerSandbox.find_project_config",
           return_value=("dockerfile", "/proj/.agent-loop/Dockerfile.sandbox"))
    @patch("ralph.selftest.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
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

    @patch("ralph.selftest.DockerSandbox.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandbox.apply_network_policy")
    @patch("ralph.selftest.DockerSandbox._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.selftest.DockerSandbox.find_project_config", return_value=None)
    @patch("ralph.selftest.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
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

    @patch("ralph.selftest.DockerSandbox.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.DockerSandbox.apply_network_policy")
    @patch("ralph.selftest.DockerSandbox._docker_sandbox_create")
    @patch("ralph.selftest.DockerSandbox.ensure_project_image",
           side_effect=RuntimeError("project build failed"))
    @patch("ralph.selftest.DockerSandbox.find_project_config",
           return_value=("dependencies", "/proj/.agent-loop/dependencies"))
    @patch("ralph.selftest.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.selftest.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
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

    @patch("ralph.selftest.TartSandbox.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.subprocess.run")
    @patch("ralph.selftest.subprocess.Popen")
    @patch("ralph.selftest.TartSandbox._wait_for_guest_agent")
    @patch("ralph.selftest.TartSandbox.proxy_host", return_value="192.168.64.1")
    @patch("ralph.selftest.TartSandbox.ensure_image",
           return_value="agent-loop-template-claude-abc123")
    @patch("ralph.selftest.TartSandbox.check_prerequisites", return_value=[])
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None), (True, "abc123")])
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

        rc = selftest("claude", "/fake/dotfiles", sandbox_type="tart")
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

    @patch("ralph.selftest.TartSandbox.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.TartSandbox.ensure_image",
           side_effect=RuntimeError("clone failed"))
    @patch("ralph.selftest.TartSandbox.check_prerequisites", return_value=[])
    @patch("ralph.selftest.proxy_health_check",
           side_effect=[(False, None), (True, "abc123")])
    @patch("ralph.selftest.ensure_proxy")
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_template_build_failure_aborts(self, mock_time, mock_read,
                                            mock_ensure_proxy, mock_health,
                                            mock_prereq, mock_img,
                                            mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test",
                                  "expiresAt": self.FUTURE_MS}
        rc = selftest("claude", "/fake/dotfiles", sandbox_type="tart")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: build template" in captured.out
        assert "selftest aborted" in captured.out

    @patch("ralph.selftest.TartSandbox.remove_sandbox")
    @patch("ralph.selftest.stop_proxy")
    @patch("ralph.selftest.TartSandbox.check_prerequisites",
           return_value=["tart is not installed"])
    @patch("ralph.selftest.proxy_health_check", return_value=(False, None))
    @patch("ralph.selftest.read_token_from_keychain")
    @patch("ralph.selftest.time.time", return_value=1700000000.0)
    def test_prerequisites_failure_aborts(self, mock_time, mock_read,
                                          mock_health, mock_prereq,
                                          mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test",
                                  "expiresAt": self.FUTURE_MS}
        rc = selftest("claude", "/fake/dotfiles", sandbox_type="tart")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: prerequisites" in captured.out
        assert "prerequisites not met" in captured.out
