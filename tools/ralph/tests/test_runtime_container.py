"""Unit tests for ralph.runtime.container — DockerContainerRuntime backend."""

import json
import subprocess
import time
from unittest.mock import MagicMock, call, patch

import pytest

from ralph.runtime.container import DockerContainerRuntime, NETWORK_NAME


# ---------------------------------------------------------------------------
# DockerContainerRuntime basics
# ---------------------------------------------------------------------------

class TestContainerBasics:
    def test_proxy_host(self):
        rt = DockerContainerRuntime("/dotfiles")
        assert rt.proxy_host() == "host.docker.internal"

    def test_check_prerequisites_docker_present(self):
        with patch("ralph.runtime.container.shutil.which", return_value="/usr/bin/docker"):
            assert DockerContainerRuntime("/dotfiles").check_prerequisites() == []

    def test_check_prerequisites_docker_missing(self):
        with patch("ralph.runtime.container.shutil.which", return_value=None):
            errors = DockerContainerRuntime("/dotfiles").check_prerequisites()
            assert errors == ["docker is not installed"]

    def test_allowed_hosts_stored(self):
        rt = DockerContainerRuntime("/dotfiles", allowed_hosts=["pypi.org"])
        assert rt.allowed_hosts == ("pypi.org",)

    def test_no_allowed_hosts_defaults_empty(self):
        rt = DockerContainerRuntime("/dotfiles")
        assert rt.allowed_hosts == ()

    def test_inherits_sandbox_name(self):
        assert DockerContainerRuntime.sandbox_name("claude", "fix-auth") == "agent-loop-claude-fix-auth"


# ---------------------------------------------------------------------------
# DockerContainerRuntime._ensure_network
# ---------------------------------------------------------------------------

class TestContainerEnsureNetwork:
    @patch("ralph.runtime.container.subprocess.run")
    def test_creates_internal_network(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        DockerContainerRuntime._ensure_network()
        mock_run.assert_called_once_with(
            ["docker", "network", "create", "--internal", NETWORK_NAME],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            check=False,
        )

    @patch("ralph.runtime.container.subprocess.run")
    def test_ignores_already_exists(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stderr="network with name ralph-agent-loop already exists")
        # Should not raise
        DockerContainerRuntime._ensure_network()

    @patch("ralph.runtime.container.subprocess.run")
    def test_raises_on_other_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stderr="permission denied")
        with pytest.raises(RuntimeError, match="failed to create network"):
            DockerContainerRuntime._ensure_network()


# ---------------------------------------------------------------------------
# DockerContainerRuntime._container_exists
# ---------------------------------------------------------------------------

class TestContainerExists:
    @patch("ralph.runtime.container.subprocess.run")
    def test_true_when_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        rt = DockerContainerRuntime("/dotfiles")
        assert rt._container_exists("test-container") is True
        mock_run.assert_called_once_with(
            ["docker", "inspect", "--type", "container", "test-container"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )

    @patch("ralph.runtime.container.subprocess.run")
    def test_false_when_not_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        rt = DockerContainerRuntime("/dotfiles")
        assert rt._container_exists("test-container") is False


# ---------------------------------------------------------------------------
# DockerContainerRuntime.exec_output
# ---------------------------------------------------------------------------

class TestContainerExecOutput:
    @patch("ralph.runtime.container.subprocess.run")
    def test_returns_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="hello\n")
        result = DockerContainerRuntime.exec_output("mycontainer", "echo", "hello")
        assert result == "hello"
        mock_run.assert_called_once_with(
            ["docker", "exec", "mycontainer", "echo", "hello"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )

    @patch("ralph.runtime.container.subprocess.run")
    def test_with_workdir(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="/work\n")
        result = DockerContainerRuntime.exec_output(
            "mycontainer", "pwd", workdir="/work")
        assert result == "/work"
        cmd = mock_run.call_args[0][0]
        assert cmd[:4] == ["docker", "exec", "-w", "/work"]

    @patch("ralph.runtime.container.subprocess.run")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="error output")
        result = DockerContainerRuntime.exec_output("mycontainer", "false")
        assert result == ""


# ---------------------------------------------------------------------------
# DockerContainerRuntime.ensure_sandbox
# ---------------------------------------------------------------------------

class TestContainerEnsureSandbox:
    @patch("ralph.runtime.container.subprocess.run")
    @patch.object(DockerContainerRuntime, "_ensure_network")
    @patch.object(DockerContainerRuntime, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerContainerRuntime, "ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(DockerContainerRuntime, "_container_exists", return_value=False)
    @patch("ralph.runtime.container.ensure_docker_proxy")
    def test_creates_new_container(self, mock_proxy, mock_exists, mock_img,
                                   mock_resolve, mock_net, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        rt = DockerContainerRuntime("/dotfiles")
        name = rt.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        assert name == "agent-loop-claude-fix-auth"
        # Verify docker run was called
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0:3] == ["docker", "run", "-d"]
        assert "--name" in cmd
        assert "agent-loop-claude-fix-auth" in cmd
        assert "--network" in cmd
        assert NETWORK_NAME in cmd
        assert "-v" in cmd
        assert "/work/fix-auth:/work/fix-auth" in cmd
        assert "/repo/.git:/repo/.git" in cmd
        assert "DOCKER_HOST=tcp://host.docker.internal:18081" in cmd
        assert cmd[-2:] == ["sleep", "infinity"]
        # Proxy was started
        mock_proxy.assert_called_once()
        # Network was created
        mock_net.assert_called_once()

    @patch.object(DockerContainerRuntime, "_container_exists", return_value=True)
    def test_reuses_existing_container(self, mock_exists):
        rt = DockerContainerRuntime("/dotfiles")
        name = rt.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        assert name == "agent-loop-claude-fix-auth"

    @patch.object(DockerContainerRuntime, "_container_exists", return_value=True)
    @patch.object(DockerContainerRuntime, "ensure_image")
    def test_reuse_skips_create(self, mock_img, mock_exists):
        rt = DockerContainerRuntime("/dotfiles")
        rt.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        mock_img.assert_not_called()

    @patch("ralph.runtime.container.subprocess.run")
    @patch.object(DockerContainerRuntime, "_ensure_network")
    @patch.object(DockerContainerRuntime, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerContainerRuntime, "ensure_project_image",
                  return_value="agent-loop-sandbox-claude-myproj:vdef12345")
    @patch.object(DockerContainerRuntime, "ensure_image",
                  return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(DockerContainerRuntime, "_container_exists", return_value=False)
    @patch("ralph.runtime.container.ensure_docker_proxy")
    def test_calls_ensure_project_image_when_project_dir(
            self, mock_proxy, mock_exists, mock_img, mock_proj,
            mock_resolve, mock_net, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        rt = DockerContainerRuntime("/dotfiles")
        rt.ensure_sandbox("claude", "fix-auth", "/work/fix-auth",
                          project_dir="/repo/root")
        mock_proj.assert_called_once_with(
            "claude", "agent-loop-sandbox-claude:vabc", "/repo/root",
            force_rebuild=False)
        # Tag used for docker run should be the project image
        cmd = mock_run.call_args[0][0]
        assert "agent-loop-sandbox-claude-myproj:vdef12345" in cmd

    @patch("ralph.runtime.container.subprocess.run")
    @patch.object(DockerContainerRuntime, "_ensure_network")
    @patch.object(DockerContainerRuntime, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerContainerRuntime, "ensure_image",
                  return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(DockerContainerRuntime, "_container_exists", return_value=False)
    @patch("ralph.runtime.container.ensure_docker_proxy")
    def test_force_rebuild_passed_through(self, mock_proxy, mock_exists, mock_img,
                                          mock_resolve, mock_net, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        rt = DockerContainerRuntime("/dotfiles")
        rt.ensure_sandbox("claude", "fix-auth", "/work/fix-auth",
                          force_rebuild=True)
        mock_img.assert_called_once_with("claude", force_rebuild=True)

    @patch("ralph.runtime.container.subprocess.run")
    @patch.object(DockerContainerRuntime, "_ensure_network")
    @patch.object(DockerContainerRuntime, "_resolve_git_common_dir", return_value=None)
    @patch.object(DockerContainerRuntime, "ensure_image", return_value="img:v1")
    @patch.object(DockerContainerRuntime, "_container_exists", return_value=False)
    @patch("ralph.runtime.container.ensure_docker_proxy")
    def test_no_git_common_dir_omits_second_volume(
            self, mock_proxy, mock_exists, mock_img, mock_resolve,
            mock_net, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        rt = DockerContainerRuntime("/dotfiles")
        rt.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        cmd = mock_run.call_args[0][0]
        # Only one -v flag (for worktree)
        v_indices = [i for i, x in enumerate(cmd) if x == "-v"]
        assert len(v_indices) == 1


# ---------------------------------------------------------------------------
# DockerContainerRuntime.cleanup_sandbox
# ---------------------------------------------------------------------------

class TestContainerCleanup:
    @patch("ralph.runtime.container.subprocess.run")
    def test_stops_and_removes(self, mock_run):
        rt = DockerContainerRuntime("/dotfiles")
        rt.cleanup_sandbox("claude", "fix-auth")
        assert mock_run.call_count == 2
        mock_run.assert_any_call(
            ["docker", "stop", "agent-loop-claude-fix-auth"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        mock_run.assert_any_call(
            ["docker", "rm", "agent-loop-claude-fix-auth"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )


# ---------------------------------------------------------------------------
# DockerContainerRuntime.remove_sandbox
# ---------------------------------------------------------------------------

class TestContainerRemoveSandbox:
    @patch("ralph.runtime.container.subprocess.run")
    def test_stops_and_removes(self, mock_run):
        rt = DockerContainerRuntime("/dotfiles")
        rt.remove_sandbox("agent-loop-claude-fix-auth")
        assert mock_run.call_count == 2
        mock_run.assert_any_call(
            ["docker", "stop", "agent-loop-claude-fix-auth"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        mock_run.assert_any_call(
            ["docker", "rm", "agent-loop-claude-fix-auth"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )


# ---------------------------------------------------------------------------
# DockerContainerRuntime.prune_sandboxes
# ---------------------------------------------------------------------------

class TestContainerPruneSandboxes:
    @staticmethod
    def _make_ps_output(containers):
        """Build docker ps --format json output from a list of name strings."""
        return "\n".join(json.dumps({"Names": n}) for n in containers) + "\n"

    @patch("ralph.runtime.container.subprocess.run")
    def test_prunes_orphan_containers(self, mock_run, tmp_path):
        existing = tmp_path / "workspace"
        existing.mkdir()

        def side_effect(cmd, **kwargs):
            # docker ps
            if cmd[0:3] == ["docker", "ps", "-a"]:
                return MagicMock(
                    returncode=0,
                    stdout=TestContainerPruneSandboxes._make_ps_output([
                        "agent-loop-claude-active",
                        "agent-loop-claude-orphan",
                    ]))
            # docker inspect for mounts
            if cmd[0:2] == ["docker", "inspect"] and "--format" in cmd:
                name = cmd[-1]
                if "active" in name:
                    return MagicMock(returncode=0, stdout=f"{existing} ")
                return MagicMock(returncode=0, stdout="/nonexistent/path ")
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect
        rt = DockerContainerRuntime(str(tmp_path))
        with patch.object(rt, "_sandbox_last_used",
                          side_effect=lambda n: time.time() if "active" in n else None):
            pruned = rt.prune_sandboxes("claude")
        assert pruned == ["agent-loop-claude-orphan"]

    @patch("ralph.runtime.container.subprocess.run")
    def test_keeps_active_containers(self, mock_run, tmp_path):
        existing = tmp_path / "workspace"
        existing.mkdir()

        def side_effect(cmd, **kwargs):
            if cmd[0:3] == ["docker", "ps", "-a"]:
                return MagicMock(
                    returncode=0,
                    stdout=TestContainerPruneSandboxes._make_ps_output([
                        "agent-loop-claude-active",
                    ]))
            if cmd[0:2] == ["docker", "inspect"]:
                return MagicMock(returncode=0, stdout=f"{existing} ")
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect
        rt = DockerContainerRuntime(str(tmp_path))
        with patch.object(rt, "_sandbox_last_used", return_value=time.time()):
            pruned = rt.prune_sandboxes("claude")
        assert pruned == []

    @patch("ralph.runtime.container.subprocess.run")
    def test_ignores_other_agents(self, mock_run, tmp_path):
        def side_effect(cmd, **kwargs):
            if cmd[0:3] == ["docker", "ps", "-a"]:
                return MagicMock(
                    returncode=0,
                    stdout=TestContainerPruneSandboxes._make_ps_output([
                        "agent-loop-codex-orphan",
                    ]))
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect
        rt = DockerContainerRuntime(str(tmp_path))
        pruned = rt.prune_sandboxes("claude")
        assert pruned == []

    @patch("ralph.runtime.container.subprocess.run")
    def test_empty_container_list(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        rt = DockerContainerRuntime(str(tmp_path))
        pruned = rt.prune_sandboxes("claude")
        assert pruned == []

    @patch("ralph.runtime.container.subprocess.run")
    def test_prunes_stale_containers(self, mock_run, tmp_path):
        existing = tmp_path / "workspace"
        existing.mkdir()

        def side_effect(cmd, **kwargs):
            if cmd[0:3] == ["docker", "ps", "-a"]:
                return MagicMock(
                    returncode=0,
                    stdout=TestContainerPruneSandboxes._make_ps_output([
                        "agent-loop-claude-stale",
                    ]))
            if cmd[0:2] == ["docker", "inspect"]:
                return MagicMock(returncode=0, stdout=f"{existing} ")
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect
        rt = DockerContainerRuntime(str(tmp_path))
        # last_used is 10 days ago (well past default 2 day max age)
        old_time = time.time() - 10 * 86400
        with patch.object(rt, "_sandbox_last_used", return_value=old_time):
            pruned = rt.prune_sandboxes("claude")
        assert pruned == ["agent-loop-claude-stale"]


# ---------------------------------------------------------------------------
# DockerContainerRuntime.setup_git_config
# ---------------------------------------------------------------------------

class TestContainerSetupGitConfig:
    @patch("ralph.runtime.container.subprocess.run")
    def test_sets_user_name_email_safe_dir(self, mock_run):
        rt = DockerContainerRuntime("/dotfiles")
        rt.setup_git_config("mycontainer", "Test User", "test@example.com")
        assert mock_run.call_count == 3
        calls = [c[0][0] for c in mock_run.call_args_list]
        # user.name
        assert calls[0] == [
            "docker", "exec", "mycontainer",
            "git", "config", "--global", "user.name", "Test User"]
        # user.email
        assert calls[1] == [
            "docker", "exec", "mycontainer",
            "git", "config", "--global", "user.email", "test@example.com"]
        # safe.directory
        assert calls[2] == [
            "docker", "exec", "mycontainer",
            "git", "config", "--global", "--add", "safe.directory", "*"]


# ---------------------------------------------------------------------------
# DockerContainerRuntime.check_in_sync
# ---------------------------------------------------------------------------

class TestContainerCheckInSync:
    @patch.object(DockerContainerRuntime, "exec_output", return_value="abc123")
    def test_in_sync(self, mock_exec):
        git = MagicMock()
        git.output.return_value = "abc123"
        rt = DockerContainerRuntime("/dotfiles")
        assert rt.check_in_sync("mycontainer", "/work", git) is True

    @patch.object(DockerContainerRuntime, "exec_output", return_value="def456")
    def test_out_of_sync(self, mock_exec):
        git = MagicMock()
        git.output.return_value = "abc123"
        rt = DockerContainerRuntime("/dotfiles")
        assert rt.check_in_sync("mycontainer", "/work", git) is False

    @patch.object(DockerContainerRuntime, "exec_output", return_value="")
    def test_container_fails(self, mock_exec):
        git = MagicMock()
        git.output.return_value = "abc123"
        rt = DockerContainerRuntime("/dotfiles")
        assert rt.check_in_sync("mycontainer", "/work", git) is False


# ---------------------------------------------------------------------------
# DockerContainerRuntime.reset_to_host
# ---------------------------------------------------------------------------

class TestContainerResetToHost:
    @patch("ralph.runtime.container.subprocess.run")
    def test_resets_and_cleans(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        git = MagicMock()
        git.output.return_value = "abc123"
        rt = DockerContainerRuntime("/dotfiles")
        assert rt.reset_to_host("mycontainer", "/work", git) is True
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert calls[0] == [
            "docker", "exec", "-w", "/work", "mycontainer",
            "git", "reset", "--hard", "abc123"]
        assert calls[1] == [
            "docker", "exec", "-w", "/work", "mycontainer",
            "git", "clean", "-fd"]

    @patch("ralph.runtime.container.subprocess.run")
    def test_returns_false_on_reset_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        git = MagicMock()
        git.output.return_value = "abc123"
        rt = DockerContainerRuntime("/dotfiles")
        assert rt.reset_to_host("mycontainer", "/work", git) is False

    def test_returns_false_when_no_host_head(self):
        git = MagicMock()
        git.output.return_value = ""
        rt = DockerContainerRuntime("/dotfiles")
        assert rt.reset_to_host("mycontainer", "/work", git) is False


# ---------------------------------------------------------------------------
# DockerContainerRuntime.sync_to_host
# ---------------------------------------------------------------------------

class TestContainerSyncToHost:
    @patch("ralph.runtime.container.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        rt = DockerContainerRuntime("/dotfiles")
        assert rt.sync_to_host("mycontainer", "abc", "def", "/work") is True

    @patch("ralph.runtime.container.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        rt = DockerContainerRuntime("/dotfiles")
        assert rt.sync_to_host("mycontainer", "abc", "def", "/work") is False


# ---------------------------------------------------------------------------
# DockerContainerRuntime.run_iteration (mocked subprocess)
# ---------------------------------------------------------------------------

class TestContainerRunIteration:
    @patch("ralph.runtime.container.subprocess.run")
    def test_proxy_agent_iteration(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="updated spec")
        rt = DockerContainerRuntime("/dotfiles")
        rt._worktree_path = "/work"
        env_vars = {
            "CLAUDE_CODE_OAUTH_TOKEN": "phantom",
            "ANTHROPIC_BASE_URL": "http://host.docker.internal:18080",
        }
        rc, spec = rt.run_iteration(
            "mycontainer", "# spec", "sonnet", env_vars, agent="claude")
        assert rc == 0
        assert spec == "updated spec"
        # Check that docker exec was called for write, run, and read
        assert mock_run.call_count == 3

    @patch("ralph.runtime.container.subprocess.run")
    def test_non_proxy_agent_iteration(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="updated spec")
        rt = DockerContainerRuntime("/dotfiles")
        rt._worktree_path = "/work"
        rc, spec = rt.run_iteration(
            "mycontainer", "# spec", "auto", agent="cursor", api_key="sk-test")
        assert rc == 0
        # Check that docker exec was called for write, key write, run, and read
        assert mock_run.call_count == 4

    @patch("ralph.runtime.container.subprocess.run")
    def test_returns_original_spec_on_write_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        rt = DockerContainerRuntime("/dotfiles")
        rt._worktree_path = "/work"
        rc, spec = rt.run_iteration("mycontainer", "# spec", "sonnet", agent="claude")
        assert rc == 1
        assert spec == "# spec"

    @patch("ralph.runtime.container.subprocess.run")
    @patch("ralph.runtime.container.get_agent")
    def test_rejects_invalid_env_var_name(self, mock_get_agent, mock_run):
        mock_get_agent.return_value = {
            "cli_command": "agent",
            "cli_flags": lambda m: [],
            "uses_proxy": False,
            "env_var_name": "INVALID-NAME",
        }
        mock_run.return_value = MagicMock(returncode=0, stdout="spec")
        rt = DockerContainerRuntime("/dotfiles")
        rt._worktree_path = "/work"
        with pytest.raises(ValueError, match="invalid env_var_name"):
            rt.run_iteration("mycontainer", "# spec", "auto",
                             agent="test", api_key="key")

    @patch("ralph.runtime.container.subprocess.run")
    def test_uses_docker_exec_not_sandbox_exec(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="spec")
        rt = DockerContainerRuntime("/dotfiles")
        rt._worktree_path = "/work"
        rt.run_iteration("mycontainer", "# spec", "sonnet", agent="claude")
        for c in mock_run.call_args_list:
            cmd = c[0][0]
            # Every command should start with "docker" and never use "sandbox"
            assert cmd[0] == "docker"
            assert "sandbox" not in cmd


# ---------------------------------------------------------------------------
# DockerContainerRuntime._preflight_backend_checks
# ---------------------------------------------------------------------------

class TestContainerPreflightChecks:
    @staticmethod
    def _run_side_effect(echo_rc=0, curl_rc=28, which_curl_rc=0):
        def fn(cmd, **kwargs):
            if "echo" in cmd:
                return MagicMock(returncode=echo_rc, stdout="ok\n", stderr="")
            if "which" in cmd:
                return MagicMock(returncode=which_curl_rc)
            if "curl" in cmd:
                return MagicMock(returncode=curl_rc, stdout="", stderr="")
            return MagicMock(returncode=0)
        return fn

    @patch("ralph.runtime.container.subprocess.run")
    @patch("ralph.runtime.container.docker_proxy_health_check",
           return_value=(True, "abc123"))
    def test_all_checks_pass(self, mock_health, mock_run):
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        rt = DockerContainerRuntime("/dotfiles")
        failures = rt._preflight_backend_checks("mycontainer")
        assert failures == []

    @patch("ralph.runtime.container.subprocess.run")
    @patch("ralph.runtime.container.docker_proxy_health_check",
           return_value=(False, None))
    def test_docker_proxy_unhealthy(self, mock_health, mock_run):
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        rt = DockerContainerRuntime("/dotfiles")
        failures = rt._preflight_backend_checks("mycontainer")
        assert any("docker socket proxy" in f for f in failures)

    @patch("ralph.runtime.container.subprocess.run")
    @patch("ralph.runtime.container.docker_proxy_health_check",
           return_value=(True, "abc123"))
    def test_container_not_responsive(self, mock_health, mock_run):
        mock_run.side_effect = self._run_side_effect(echo_rc=1, curl_rc=28)
        rt = DockerContainerRuntime("/dotfiles")
        failures = rt._preflight_backend_checks("mycontainer")
        assert any("not responding" in f for f in failures)

    @patch("ralph.runtime.container.subprocess.run")
    @patch("ralph.runtime.container.docker_proxy_health_check",
           return_value=(True, "abc123"))
    def test_network_isolation_failure(self, mock_health, mock_run):
        # curl succeeds = network isolation is broken
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=0)
        rt = DockerContainerRuntime("/dotfiles")
        failures = rt._preflight_backend_checks("mycontainer")
        assert any("network isolation" in f for f in failures)

    @patch("ralph.runtime.container.subprocess.run")
    @patch("ralph.runtime.container.docker_proxy_health_check",
           return_value=(True, "abc123"))
    def test_curl_not_found_fails_preflight(self, mock_health, mock_run):
        mock_run.side_effect = self._run_side_effect(
            echo_rc=0, curl_rc=28, which_curl_rc=1)
        rt = DockerContainerRuntime("/dotfiles")
        failures = rt._preflight_backend_checks("mycontainer")
        assert any("curl not found" in f for f in failures)

    @patch("ralph.runtime.container.subprocess.run")
    @patch("ralph.runtime.container.docker_proxy_health_check",
           return_value=(True, "abc123"))
    def test_skips_network_check_when_container_down(self, mock_health, mock_run):
        # Container not responding — curl should not be called
        call_count = [0]
        def fn(cmd, **kwargs):
            if "echo" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="")
            if "curl" in cmd:
                call_count[0] += 1
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0)
        mock_run.side_effect = fn
        rt = DockerContainerRuntime("/dotfiles")
        rt._preflight_backend_checks("mycontainer")
        assert call_count[0] == 0


# ---------------------------------------------------------------------------
# Network proxy integration in ensure_sandbox
# ---------------------------------------------------------------------------

class TestContainerNetworkProxy:
    @patch("ralph.runtime.container.subprocess.run")
    @patch.object(DockerContainerRuntime, "_ensure_network")
    @patch.object(DockerContainerRuntime, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerContainerRuntime, "ensure_image", return_value="img:v1")
    @patch.object(DockerContainerRuntime, "_container_exists", return_value=False)
    @patch("ralph.runtime.container.ensure_network_proxy")
    @patch("ralph.runtime.container.ensure_docker_proxy")
    def test_ensure_sandbox_starts_network_proxy_with_allowed_hosts(
            self, mock_docker_proxy, mock_network_proxy, mock_exists,
            mock_img, mock_resolve, mock_net, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        rt = DockerContainerRuntime("/dotfiles", allowed_hosts=["pypi.org", "npm.io"])
        rt.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        mock_network_proxy.assert_called_once_with(
            18082, "/dotfiles", ("pypi.org", "npm.io"))
        # Verify proxy env vars in docker run command
        cmd = mock_run.call_args[0][0]
        assert "HTTP_PROXY=http://host.docker.internal:18082" in cmd
        assert "HTTPS_PROXY=http://host.docker.internal:18082" in cmd
        assert "NO_PROXY=host.docker.internal" in cmd

    @patch("ralph.runtime.container.subprocess.run")
    @patch.object(DockerContainerRuntime, "_ensure_network")
    @patch.object(DockerContainerRuntime, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerContainerRuntime, "ensure_image", return_value="img:v1")
    @patch.object(DockerContainerRuntime, "_container_exists", return_value=False)
    @patch("ralph.runtime.container.ensure_network_proxy")
    @patch("ralph.runtime.container.ensure_docker_proxy")
    def test_ensure_sandbox_no_network_proxy_without_allowed_hosts(
            self, mock_docker_proxy, mock_network_proxy, mock_exists,
            mock_img, mock_resolve, mock_net, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        rt = DockerContainerRuntime("/dotfiles")
        rt.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        mock_network_proxy.assert_not_called()
        # Verify no proxy env vars in docker run command
        cmd = mock_run.call_args[0][0]
        assert "HTTP_PROXY=http://host.docker.internal:18082" not in cmd
        assert "HTTPS_PROXY=http://host.docker.internal:18082" not in cmd
        assert "NO_PROXY=host.docker.internal" not in cmd

    @patch("ralph.runtime.container.subprocess.run")
    @patch("ralph.runtime.container.network_proxy_health_check",
           return_value=(True, "abc123", frozenset(["pypi.org"])))
    @patch("ralph.runtime.container.docker_proxy_health_check",
           return_value=(True, "abc123"))
    def test_preflight_checks_network_proxy_with_allowed_hosts(
            self, mock_docker_health, mock_network_health, mock_run):
        mock_run.side_effect = TestContainerPreflightChecks._run_side_effect(
            echo_rc=0, curl_rc=28)
        rt = DockerContainerRuntime("/dotfiles", allowed_hosts=["pypi.org"])
        failures = rt._preflight_backend_checks("mycontainer")
        assert failures == []
        mock_network_health.assert_called_once_with(18082)

    @patch("ralph.runtime.container.subprocess.run")
    @patch("ralph.runtime.container.network_proxy_health_check",
           return_value=(False, None, None))
    @patch("ralph.runtime.container.docker_proxy_health_check",
           return_value=(True, "abc123"))
    def test_preflight_checks_network_proxy_unhealthy(
            self, mock_docker_health, mock_network_health, mock_run):
        mock_run.side_effect = TestContainerPreflightChecks._run_side_effect(
            echo_rc=0, curl_rc=28)
        rt = DockerContainerRuntime("/dotfiles", allowed_hosts=["pypi.org"])
        failures = rt._preflight_backend_checks("mycontainer")
        assert any("network proxy" in f for f in failures)

    @patch("ralph.runtime.container.subprocess.run")
    @patch("ralph.runtime.container.network_proxy_health_check")
    @patch("ralph.runtime.container.docker_proxy_health_check",
           return_value=(True, "abc123"))
    def test_preflight_checks_skips_network_proxy_without_allowed_hosts(
            self, mock_docker_health, mock_network_health, mock_run):
        mock_run.side_effect = TestContainerPreflightChecks._run_side_effect(
            echo_rc=0, curl_rc=28)
        rt = DockerContainerRuntime("/dotfiles")
        failures = rt._preflight_backend_checks("mycontainer")
        assert failures == []
        mock_network_health.assert_not_called()
