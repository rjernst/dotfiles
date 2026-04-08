"""Unit tests for ralph.runtime.tart — TartRuntime backend."""

import json
import os
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from ralph.runtime.tart import TartRuntime


# ---------------------------------------------------------------------------
# TartRuntime._template_name
# ---------------------------------------------------------------------------

class TestTartTemplateName:
    def _make(self, **kwargs):
        config = {"base_image": "img:latest", "dependencies_content": ""}
        config.update(kwargs)
        return TartRuntime("/dotfiles", config=config)

    def test_deterministic(self):
        t1 = self._make()
        t2 = self._make()
        assert t1._template_name("claude") == t2._template_name("claude")

    def test_format(self):
        name = self._make()._template_name("claude")
        assert name.startswith("agent-loop-template-claude-")
        # Hash part is 12 hex chars
        hash_part = name.split("-")[-1]
        assert len(hash_part) == 12
        assert all(c in "0123456789abcdef" for c in hash_part)

    def test_changes_with_base_image(self):
        n1 = self._make(base_image="img:v1")._template_name("claude")
        n2 = self._make(base_image="img:v2")._template_name("claude")
        assert n1 != n2

    def test_changes_with_dependencies(self):
        n1 = self._make(dependencies_content="brew install jq")._template_name("claude")
        n2 = self._make(dependencies_content="brew install yq")._template_name("claude")
        assert n1 != n2

    def test_changes_with_agent(self):
        t = self._make()
        assert t._template_name("claude") != t._template_name("cursor")


# ---------------------------------------------------------------------------
# TartRuntime._max_sandbox_name_length (macOS sun_path cap)
# ---------------------------------------------------------------------------

class TestTartSandboxNameLimit:
    def test_limit_matches_control_socket_budget(self):
        """Limit = 103 - len(~/.tart/vms/) - len(/control.sock)."""
        limit = TartRuntime._max_sandbox_name_length()
        socket_dir = os.path.expanduser("~/.tart/vms/")
        expected = 103 - len(socket_dir) - len("/control.sock")
        assert limit == expected

    def test_short_name_is_not_truncated(self):
        name = TartRuntime.sandbox_name("claude", "my-branch")
        assert name == "agent-loop-claude-my-branch"

    @patch.object(TartRuntime, "_max_sandbox_name_length")
    def test_long_name_is_truncated(self, mock_max):
        mock_max.return_value = 40
        name = TartRuntime.sandbox_name("claude", "x" * 100)
        assert len(name) <= 40
        assert name.startswith("agent-loop-claude-")


# ---------------------------------------------------------------------------
# TartRuntime._list_vms
# ---------------------------------------------------------------------------

class TestTartListVms:
    def _make(self):
        return TartRuntime("/dotfiles", config={"base_image": "img:latest"})

    def setup_method(self):
        self._saved_cache = TartRuntime._vm_list_cache
        TartRuntime._vm_list_cache = (0, [])

    def teardown_method(self):
        TartRuntime._vm_list_cache = self._saved_cache

    @patch("ralph.runtime.tart.subprocess.run")
    def test_parses_json(self, mock_run):
        vms = [{"Name": "vm1", "State": "Running"}, {"Name": "vm2", "State": "Stopped"}]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(vms))
        result = self._make()._list_vms()
        assert result == vms
        mock_run.assert_called_once_with(
            ["tart", "list", "--format", "json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )

    @patch("ralph.runtime.tart.subprocess.run")
    def test_failure_returns_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert self._make()._list_vms() == []

    @patch("ralph.runtime.tart.subprocess.run")
    def test_invalid_json_returns_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        assert self._make()._list_vms() == []


# ---------------------------------------------------------------------------
# TartRuntime._list_vms caching
# ---------------------------------------------------------------------------

class TestTartListVmsCache:
    def _make(self):
        return TartRuntime("/dotfiles", config={"base_image": "img:latest"})

    def setup_method(self):
        self._saved_cache = TartRuntime._vm_list_cache
        TartRuntime._vm_list_cache = (0, [])

    def teardown_method(self):
        TartRuntime._vm_list_cache = self._saved_cache

    @patch("ralph.runtime.tart.subprocess.run")
    def test_second_call_within_ttl_uses_cache(self, mock_run):
        vms = [{"Name": "vm1", "State": "Running"}]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(vms))
        t = self._make()
        result1 = t._list_vms()
        result2 = t._list_vms()
        assert result1 == vms
        assert result2 == vms
        # Only one subprocess call — second was cached
        assert mock_run.call_count == 1

    @patch("ralph.runtime.tart.time.monotonic")
    @patch("ralph.runtime.tart.subprocess.run")
    def test_call_after_ttl_fetches_fresh(self, mock_run, mock_time):
        vms_old = [{"Name": "vm1", "State": "Running"}]
        vms_new = [{"Name": "vm1", "State": "Stopped"}]
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(vms_old)),
            MagicMock(returncode=0, stdout=json.dumps(vms_new)),
        ]
        mock_time.side_effect = [10.0, 13.0]  # 3s apart, > 2s TTL
        t = self._make()
        result1 = t._list_vms()
        result2 = t._list_vms()
        assert result1 == vms_old
        assert result2 == vms_new
        assert mock_run.call_count == 2

    @patch("ralph.runtime.tart.subprocess.run")
    def test_cache_shared_across_instances(self, mock_run):
        vms = [{"Name": "vm1", "State": "Running"}]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(vms))
        t1 = self._make()
        t2 = self._make()
        t1._list_vms()
        result = t2._list_vms()
        assert result == vms
        # Only one subprocess call — t2 used t1's cache
        assert mock_run.call_count == 1


# ---------------------------------------------------------------------------
# TartRuntime._check_vm_limit
# ---------------------------------------------------------------------------

class TestTartCheckVmLimit:
    def _make(self):
        return TartRuntime("/dotfiles", config={"base_image": "img:latest"})

    @patch.object(TartRuntime, "_running_vm_count", return_value=0)
    def test_zero_running_passes(self, _):
        self._make()._check_vm_limit()  # should not raise

    @patch.object(TartRuntime, "_running_vm_count", return_value=1)
    def test_one_running_passes(self, _):
        self._make()._check_vm_limit()  # should not raise

    @patch.object(TartRuntime, "_running_vm_count", return_value=2)
    def test_two_running_raises(self, _):
        with pytest.raises(RuntimeError, match="cannot start VM"):
            self._make()._check_vm_limit()

    @patch.object(TartRuntime, "_running_vm_count", return_value=2)
    def test_error_message_includes_count(self, _):
        with pytest.raises(RuntimeError, match="2 macOS VMs already running"):
            self._make()._check_vm_limit()

    @patch.object(TartRuntime, "_running_vm_count", return_value=3)
    def test_three_running_raises(self, _):
        with pytest.raises(RuntimeError, match="3 macOS VMs already running"):
            self._make()._check_vm_limit()


# ---------------------------------------------------------------------------
# TartRuntime._wait_for_guest_agent
# ---------------------------------------------------------------------------

class TestTartWaitForGuestAgent:
    def _make(self):
        return TartRuntime("/dotfiles", config={"base_image": "img:latest"})

    @patch("ralph.runtime.tart.time.sleep")
    @patch("ralph.runtime.tart.subprocess.run")
    def test_succeeds_first_try(self, mock_run, _sleep):
        mock_run.return_value = MagicMock(returncode=0)
        self._make()._wait_for_guest_agent("test-vm", timeout=10)
        mock_run.assert_called_once_with(
            ["tart", "exec", "test-vm", "echo", "ok"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )

    @patch("ralph.runtime.tart.time.sleep")
    @patch("ralph.runtime.tart.time.time")
    @patch("ralph.runtime.tart.subprocess.run")
    def test_succeeds_after_retries(self, mock_run, mock_time, _sleep):
        # First two calls fail, third succeeds
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=1),
            MagicMock(returncode=0),
        ]
        # time.time() returns values within the timeout window
        mock_time.side_effect = [0, 2, 4, 6]
        self._make()._wait_for_guest_agent("test-vm", timeout=120)
        assert mock_run.call_count == 3

    @patch("ralph.runtime.tart.time.sleep")
    @patch("ralph.runtime.tart.time.time")
    @patch("ralph.runtime.tart.subprocess.run")
    def test_timeout_raises(self, mock_run, mock_time, _sleep):
        mock_run.return_value = MagicMock(returncode=1)
        # Time jumps past deadline
        mock_time.side_effect = [0, 130]
        with pytest.raises(RuntimeError, match="guest agent not responding"):
            self._make()._wait_for_guest_agent("test-vm", timeout=120)

    @patch("ralph.runtime.tart.time.sleep")
    @patch("ralph.runtime.tart.time.time")
    @patch("ralph.runtime.tart.subprocess.run")
    def test_timeout_includes_vm_name(self, mock_run, mock_time, _sleep):
        mock_run.return_value = MagicMock(returncode=1)
        mock_time.side_effect = [0, 200]
        with pytest.raises(RuntimeError, match="test-vm"):
            self._make()._wait_for_guest_agent("test-vm", timeout=120)


# ---------------------------------------------------------------------------
# TartRuntime.ensure_image
# ---------------------------------------------------------------------------

class TestTartEnsureImage:
    def _make(self, deps=""):
        config = {"base_image": "img:latest", "dependencies_content": deps}
        return TartRuntime("/dotfiles", config=config)

    @patch.object(TartRuntime, "_vm_exists", return_value=True)
    def test_cached_template_reused(self, _exists):
        t = self._make()
        name = t.ensure_image("claude")
        assert name == t._template_name("claude")

    @patch("ralph.runtime.tart.subprocess.Popen")
    @patch("ralph.runtime.tart.subprocess.run")
    @patch.object(TartRuntime, "_wait_for_guest_agent")
    @patch.object(TartRuntime, "_check_vm_limit")
    @patch.object(TartRuntime, "_vm_exists", return_value=False)
    def test_clones_without_deps(self, _exists, _limit, _wait, mock_run, _popen):
        t = self._make(deps="")
        name = t.ensure_image("claude")
        # Should call tart clone
        clone_call = mock_run.call_args_list[0]
        assert clone_call[0][0] == ["tart", "clone", "img:latest", name]
        # Should NOT start VM (no deps)
        _popen.assert_not_called()
        _wait.assert_not_called()

    @patch("ralph.runtime.tart.subprocess.Popen")
    @patch("ralph.runtime.tart.subprocess.run")
    @patch.object(TartRuntime, "_wait_for_guest_agent")
    @patch.object(TartRuntime, "_check_vm_limit")
    @patch.object(TartRuntime, "_vm_exists", return_value=False)
    def test_installs_deps(self, _exists, _limit, _wait, mock_run, mock_popen):
        vm_proc = MagicMock()
        mock_popen.return_value = vm_proc
        t = self._make(deps="brew install jq\n")
        name = t.ensure_image("claude")
        # Should start VM
        mock_popen.assert_called_once_with(
            ["tart", "run", name, "--no-graphics"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Should exec dependencies
        exec_calls = [c for c in mock_run.call_args_list
                      if c[0][0][:3] == ["tart", "exec", "-i"]]
        assert len(exec_calls) == 1
        assert exec_calls[0].kwargs["input"] == "brew install jq\n"
        # Should stop VM
        stop_calls = [c for c in mock_run.call_args_list
                      if c[0][0][:2] == ["tart", "stop"]]
        assert len(stop_calls) == 1
        vm_proc.wait.assert_called_once()

    @patch("ralph.runtime.tart.subprocess.Popen")
    @patch("ralph.runtime.tart.subprocess.run")
    @patch.object(TartRuntime, "_wait_for_guest_agent")
    @patch.object(TartRuntime, "_check_vm_limit")
    @patch.object(TartRuntime, "_vm_exists", side_effect=[True, False])
    def test_force_rebuild_deletes_old(self, _exists, _limit, _wait, mock_run, _popen):
        t = self._make(deps="")
        name = t.ensure_image("claude", force_rebuild=True)
        # First call should be tart delete
        delete_call = mock_run.call_args_list[0]
        assert delete_call[0][0] == ["tart", "delete", name]
        # Then tart clone
        clone_call = mock_run.call_args_list[1]
        assert clone_call[0][0] == ["tart", "clone", "img:latest", name]

    @patch("ralph.runtime.tart.subprocess.Popen")
    @patch("ralph.runtime.tart.subprocess.run")
    @patch.object(TartRuntime, "_wait_for_guest_agent",
                  side_effect=RuntimeError("timeout"))
    @patch.object(TartRuntime, "_check_vm_limit")
    @patch.object(TartRuntime, "_vm_exists", return_value=False)
    def test_stops_vm_on_failure(self, _exists, _limit, _wait, mock_run, mock_popen):
        """VM is stopped even if dependency install fails."""
        vm_proc = MagicMock()
        mock_popen.return_value = vm_proc
        t = self._make(deps="brew install jq\n")
        with pytest.raises(RuntimeError, match="timeout"):
            t.ensure_image("claude")
        # Should still stop VM
        stop_calls = [c for c in mock_run.call_args_list
                      if len(c[0][0]) >= 2 and c[0][0][:2] == ["tart", "stop"]]
        assert len(stop_calls) == 1
        vm_proc.wait.assert_called_once()


# ---------------------------------------------------------------------------
# TartRuntime.exec_output
# ---------------------------------------------------------------------------

class TestTartExecOutput:
    @patch("ralph.runtime.tart.subprocess.run")
    def test_returns_stdout_on_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="  hello world  \n")
        result = TartRuntime.exec_output("test-vm", "echo", "hello")
        assert result == "hello world"
        mock_run.assert_called_once_with(
            ["tart", "exec", "test-vm", "echo", "hello"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )

    @patch("ralph.runtime.tart.subprocess.run")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="error stuff")
        result = TartRuntime.exec_output("test-vm", "false")
        assert result == ""


# ---------------------------------------------------------------------------
# TartRuntime.ensure_sandbox
# ---------------------------------------------------------------------------

class TestTartEnsureSandbox:
    def setup_method(self):
        self._saved = TartRuntime._vm_procs.copy()
        TartRuntime._vm_procs.clear()

    def teardown_method(self):
        TartRuntime._vm_procs.clear()
        TartRuntime._vm_procs.update(self._saved)

    def _make(self, **kwargs):
        config = {"base_image": "img:latest", "dependencies_content": ""}
        config.update(kwargs)
        return TartRuntime("/dotfiles", config=config)

    @patch.object(TartRuntime, "_setup_worktree_git")
    @patch.object(TartRuntime, "_resolve_git_common_dir", return_value=None)
    @patch.object(TartRuntime, "_wait_for_guest_agent")
    @patch("ralph.runtime.tart.subprocess.Popen")
    @patch("ralph.runtime.tart.subprocess.run")
    @patch.object(TartRuntime, "ensure_image", return_value="template-name")
    @patch.object(TartRuntime, "_check_vm_limit")
    @patch.object(TartRuntime, "_vm_state", return_value=None)
    def test_creates_new_vm(self, _state, _limit, _ensure, mock_run, mock_popen,
                            _wait, _resolve, _setup_wt):
        mock_popen.return_value = MagicMock()
        t = self._make()
        name = t.ensure_sandbox("claude", "my-branch", "/work/my-branch")
        assert name == TartRuntime.sandbox_name("claude", "my-branch")

        # Should clone from template
        clone_call = mock_run.call_args_list[0]
        assert clone_call[0][0] == ["tart", "clone", "template-name", name]

        # Should start VM with directory sharing
        mock_popen.assert_called_once()
        popen_args = mock_popen.call_args[0][0]
        assert popen_args[:4] == ["tart", "run", name, "--no-graphics"]
        assert f"--dir=workspace:/work/my-branch" in popen_args

    @patch.object(TartRuntime, "_setup_worktree_git")
    @patch.object(TartRuntime, "_resolve_git_common_dir", return_value=None)
    @patch.object(TartRuntime, "_vm_state", return_value="Running")
    def test_reuses_running_vm(self, _state, _resolve, _setup_wt):
        t = self._make()
        name = t.ensure_sandbox("claude", "my-branch", "/work/my-branch")
        assert name == TartRuntime.sandbox_name("claude", "my-branch")

    @patch.object(TartRuntime, "_setup_worktree_git")
    @patch.object(TartRuntime, "_resolve_git_common_dir", return_value=None)
    @patch.object(TartRuntime, "_vm_state", return_value="Running")
    def test_reuse_calls_setup_worktree_git(self, _state, _resolve, mock_setup_wt):
        t = self._make()
        t.ensure_sandbox("claude", "my-branch", "/work/my-branch")
        mock_setup_wt.assert_called_once()

    @patch.object(TartRuntime, "_setup_worktree_git")
    @patch.object(TartRuntime, "_resolve_git_common_dir", return_value=None)
    @patch.object(TartRuntime, "_wait_for_guest_agent")
    @patch("ralph.runtime.tart.subprocess.Popen")
    @patch("ralph.runtime.tart.subprocess.run")
    @patch.object(TartRuntime, "ensure_image", return_value="template-name")
    @patch.object(TartRuntime, "_check_vm_limit")
    @patch.object(TartRuntime, "_vm_state", return_value="Stopped")
    def test_deletes_stopped_vm_and_recreates(self, _state, _limit, _ensure,
                                               mock_run, mock_popen, _wait,
                                               _resolve, _setup_wt):
        mock_popen.return_value = MagicMock()
        t = self._make()
        name = t.ensure_sandbox("claude", "my-branch", "/work/my-branch")

        # First call should be tart delete
        delete_call = mock_run.call_args_list[0]
        assert delete_call[0][0] == ["tart", "delete", name]
        # Then clone
        clone_call = mock_run.call_args_list[1]
        assert clone_call[0][0] == ["tart", "clone", "template-name", name]

    @patch.object(TartRuntime, "_resolve_git_common_dir", return_value=None)
    @patch.object(TartRuntime, "_vm_state", return_value=None)
    @patch.object(TartRuntime, "_check_vm_limit",
                  side_effect=RuntimeError("too many VMs"))
    def test_vm_limit_check(self, _limit, _state, _resolve):
        t = self._make()
        with pytest.raises(RuntimeError, match="too many VMs"):
            t.ensure_sandbox("claude", "my-branch", "/work/my-branch")

    @patch.object(TartRuntime, "_setup_worktree_git")
    @patch.object(TartRuntime, "_resolve_git_common_dir", return_value=None)
    @patch.object(TartRuntime, "_wait_for_guest_agent")
    @patch("ralph.runtime.tart.subprocess.Popen")
    @patch("ralph.runtime.tart.subprocess.run")
    @patch.object(TartRuntime, "ensure_image", return_value="template-name")
    @patch.object(TartRuntime, "_check_vm_limit")
    @patch.object(TartRuntime, "_vm_state", return_value=None)
    def test_stores_vm_proc(self, _state, _limit, _ensure, _run, mock_popen,
                            _wait, _resolve, _setup_wt):
        vm_proc = MagicMock()
        mock_popen.return_value = vm_proc
        t = self._make()
        name = t.ensure_sandbox("claude", "my-branch", "/work/my-branch")
        assert t._vm_procs[name] is vm_proc


# ---------------------------------------------------------------------------
# TartRuntime._wait_for_virtiofs_mounts
# ---------------------------------------------------------------------------

class TestTartWaitForVirtioFSMounts:
    def _make(self):
        return TartRuntime("/dotfiles", config={"base_image": "img:latest"})

    @patch("ralph.runtime.tart.subprocess.run")
    def test_returns_immediately_when_mounts_exist(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        t = self._make()
        t._wait_for_virtiofs_mounts("test-vm", ["/Volumes/My Shared Files/workspace"])
        # Should have checked the one mount
        assert mock_run.call_count == 1

    @patch("ralph.runtime.tart.time.sleep")
    @patch("ralph.runtime.tart.time.time")
    @patch("ralph.runtime.tart.subprocess.run")
    def test_polls_until_mount_appears(self, mock_run, mock_time, _sleep):
        # First check fails, second succeeds
        mock_run.side_effect = [
            MagicMock(returncode=1),  # not ready
            MagicMock(returncode=0),  # ready
        ]
        mock_time.side_effect = [0, 5, 10]
        t = self._make()
        t._wait_for_virtiofs_mounts("test-vm", ["/Volumes/My Shared Files/workspace"],
                                    timeout=30)

    @patch("ralph.runtime.tart.time.sleep")
    @patch("ralph.runtime.tart.time.time")
    @patch("ralph.runtime.tart.subprocess.run")
    def test_timeout_raises(self, mock_run, mock_time, _sleep):
        mock_run.return_value = MagicMock(returncode=1)
        mock_time.side_effect = [0, 10, 20, 31]
        t = self._make()
        with pytest.raises(RuntimeError, match="VirtioFS mounts not available"):
            t._wait_for_virtiofs_mounts("test-vm",
                                        ["/Volumes/My Shared Files/workspace"],
                                        timeout=30)


# ---------------------------------------------------------------------------
# TartRuntime._setup_git_common_dir_symlink
# ---------------------------------------------------------------------------

class TestTartSetupGitCommonDirSymlink:
    def _make(self):
        return TartRuntime("/dotfiles", config={"base_image": "img:latest"})

    @patch("ralph.runtime.tart.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        t = self._make()
        t._setup_git_common_dir_symlink("test-vm", "/Users/me/repo/.git")
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["tart", "exec", "test-vm"]
        # Should use sudo for both mkdir and ln (VM user can't create host paths)
        bash_cmd = cmd[5]
        assert "sudo mkdir -p" in bash_cmd
        assert "sudo ln -sfn" in bash_cmd

    @patch("ralph.runtime.tart.subprocess.run")
    def test_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="permission denied")
        t = self._make()
        with pytest.raises(RuntimeError, match="failed to create git symlink"):
            t._setup_git_common_dir_symlink("test-vm", "/Users/me/repo/.git")


# ---------------------------------------------------------------------------
# TartRuntime._verify_git_in_sandbox
# ---------------------------------------------------------------------------

class TestTartVerifyGitInSandbox:
    def _make(self):
        return TartRuntime("/dotfiles", config={"base_image": "img:latest"})

    @patch("ralph.runtime.tart.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        t = self._make()
        t._verify_git_in_sandbox("test-vm")  # should not raise

    @patch("ralph.runtime.tart.subprocess.run")
    def test_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=128, stdout="",
            stderr="fatal: not a git repository")
        t = self._make()
        with pytest.raises(RuntimeError, match="git not functional"):
            t._verify_git_in_sandbox("test-vm")


# ---------------------------------------------------------------------------
# TartRuntime._setup_worktree_git
# ---------------------------------------------------------------------------

class TestTartSetupWorktreeGit:
    def _make(self):
        return TartRuntime("/dotfiles", config={"base_image": "img:latest"})

    @patch.object(TartRuntime, "_verify_git_in_sandbox")
    @patch.object(TartRuntime, "_setup_git_common_dir_symlink")
    @patch.object(TartRuntime, "_wait_for_virtiofs_mounts")
    def test_with_git_common_dir(self, mock_wait, mock_symlink, mock_verify):
        t = self._make()
        t._setup_worktree_git("test-vm", "/Users/me/repo/.git")
        # Should wait for both workspace and gitdir mounts
        mounts = mock_wait.call_args[0][1]
        assert TartRuntime.SHARED_DIR in mounts
        assert TartRuntime.SHARED_DIR_GITDIR in mounts
        mock_symlink.assert_called_once()
        mock_verify.assert_called_once()

    @patch.object(TartRuntime, "_verify_git_in_sandbox")
    @patch.object(TartRuntime, "_setup_git_common_dir_symlink")
    @patch.object(TartRuntime, "_wait_for_virtiofs_mounts")
    def test_without_git_common_dir(self, mock_wait, mock_symlink, mock_verify):
        t = self._make()
        t._setup_worktree_git("test-vm", None)
        # Should only wait for workspace mount
        mounts = mock_wait.call_args[0][1]
        assert TartRuntime.SHARED_DIR in mounts
        assert TartRuntime.SHARED_DIR_GITDIR not in mounts
        mock_symlink.assert_not_called()
        mock_verify.assert_not_called()


# ---------------------------------------------------------------------------
# TartRuntime.setup_git_config
# ---------------------------------------------------------------------------

class TestTartSetupGitConfig:
    @patch("ralph.runtime.tart.subprocess.run")
    def test_configures_user_email_safedir(self, mock_run):
        t = TartRuntime("/dotfiles", config={"base_image": "img:latest"})
        t.setup_git_config("test-vm", "Test User", "test@example.com")
        assert mock_run.call_count == 3

        calls = mock_run.call_args_list
        # user.name
        assert calls[0][0][0] == [
            "tart", "exec", "test-vm",
            "git", "config", "--global", "user.name", "Test User"]
        # user.email
        assert calls[1][0][0] == [
            "tart", "exec", "test-vm",
            "git", "config", "--global", "user.email", "test@example.com"]
        # safe.directory
        assert calls[2][0][0] == [
            "tart", "exec", "test-vm",
            "git", "config", "--global", "--add", "safe.directory", "*"]


# ---------------------------------------------------------------------------
# TartRuntime.run_iteration
# ---------------------------------------------------------------------------

class TestTartRunIteration:
    def _make(self):
        return TartRuntime("/dotfiles", config={"base_image": "img:latest"})

    @patch("ralph.runtime.tart.subprocess.run")
    def test_writes_spec_runs_claude_reads_spec(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # tee (write spec)
            MagicMock(returncode=0),  # bash -c claude
            MagicMock(returncode=0, stdout="updated spec"),  # cat (read spec)
        ]
        t = self._make()
        rc, spec = t.run_iteration("test-vm", "original spec", "sonnet",
                                   {"KEY": "val"})
        assert rc == 0
        assert spec == "updated spec"

        # Check write command
        write_call = mock_run.call_args_list[0]
        assert write_call[0][0] == [
            "tart", "exec", "-i", "test-vm", "tee", "/tmp/spec.md"]
        assert write_call.kwargs["input"] == "original spec"

        # Check claude command uses bash -c with env vars
        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        assert cmd[:4] == ["tart", "exec", "test-vm", "bash"]
        assert cmd[4] == "-c"
        bash_cmd = cmd[5]
        assert "env KEY='val'" in bash_cmd or "env KEY=val" in bash_cmd
        assert "--model" in bash_cmd
        assert "--dangerously-skip-permissions" in bash_cmd
        assert f"cd '{TartRuntime.SHARED_DIR}'" in bash_cmd

        # Check read command
        read_call = mock_run.call_args_list[2]
        assert read_call[0][0] == [
            "tart", "exec", "test-vm", "cat", "/tmp/spec.md"]

    @patch("ralph.runtime.tart.subprocess.run")
    def test_write_failure_returns_original_spec(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        t = self._make()
        rc, spec = t.run_iteration("test-vm", "original spec", "sonnet")
        assert rc == 1
        assert spec == "original spec"

    @patch("ralph.runtime.tart.subprocess.run")
    def test_read_failure_returns_original_spec(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # tee
            MagicMock(returncode=0),  # claude
            MagicMock(returncode=1, stdout=""),  # cat fails
        ]
        t = self._make()
        rc, spec = t.run_iteration("test-vm", "original spec", "sonnet")
        assert rc == 0
        assert spec == "original spec"

    @patch("ralph.runtime.tart.subprocess.run")
    def test_no_env_vars(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # tee
            MagicMock(returncode=0),  # claude
            MagicMock(returncode=0, stdout="spec"),  # cat
        ]
        t = self._make()
        t.run_iteration("test-vm", "spec", "sonnet")
        claude_call = mock_run.call_args_list[1]
        bash_cmd = claude_call[0][0][5]
        assert "env " not in bash_cmd or bash_cmd.startswith("cd ")

    @patch("ralph.runtime.tart.subprocess.run")
    def test_env_vars_shell_escaped(self, mock_run):
        """Env vars with special chars are properly shell-escaped."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # tee
            MagicMock(returncode=0),  # claude
            MagicMock(returncode=0, stdout="spec"),  # cat
        ]
        t = self._make()
        t.run_iteration("test-vm", "spec", "sonnet",
                        {"KEY": "val with spaces"})
        claude_call = mock_run.call_args_list[1]
        bash_cmd = claude_call[0][0][5]
        assert "KEY='val with spaces'" in bash_cmd


# ---------------------------------------------------------------------------
# TartRuntime.proxy_host
# ---------------------------------------------------------------------------

class TestTartProxyHost:
    def setup_method(self):
        self._saved = TartRuntime._vm_procs.copy()
        TartRuntime._vm_procs.clear()

    def teardown_method(self):
        TartRuntime._vm_procs.clear()
        TartRuntime._vm_procs.update(self._saved)

    def _make(self):
        return TartRuntime("/dotfiles", config={"base_image": "img:latest"})

    @patch("ralph.runtime.tart.subprocess.run")
    def test_gateway_from_vm(self, mock_run):
        """Parses gateway IP from route output inside VM."""
        route_output = (
            "   route to: default\n"
            "destination: default\n"
            "       mask: default\n"
            "    gateway: 192.168.64.1\n"
            "  interface: en0\n"
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=route_output)
        t = self._make()
        # Add a fake running VM proc
        proc = MagicMock()
        proc.poll.return_value = None  # still running
        t._vm_procs["test-vm"] = proc

        result = t.proxy_host()
        assert result == "192.168.64.1"

    @patch("ralph.runtime.tart.subprocess.run")
    def test_fallback_to_en0(self, mock_run):
        """Falls back to ipconfig getifaddr en0 on host."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="10.0.0.5\n"),  # ipconfig
        ]
        t = self._make()
        # No running VMs
        result = t.proxy_host()
        assert result == "10.0.0.5"

    @patch("ralph.runtime.tart.subprocess.run")
    def test_final_fallback(self, mock_run):
        """Falls back to well-known 192.168.64.1."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        t = self._make()
        result = t.proxy_host()
        assert result == "192.168.64.1"

    @patch("ralph.runtime.tart.subprocess.run")
    def test_caches_result(self, mock_run):
        """proxy_host caches after first call."""
        mock_run.return_value = MagicMock(returncode=0, stdout="10.0.0.5\n")
        t = self._make()
        t.proxy_host()
        t.proxy_host()
        # ipconfig should only be called once
        assert mock_run.call_count == 1

    def test_cached_value_used_directly(self):
        """If _cached_proxy_host is set, no subprocess calls."""
        t = self._make()
        t._cached_proxy_host = "10.0.0.99"
        assert t.proxy_host() == "10.0.0.99"


# ---------------------------------------------------------------------------
# TartRuntime.cleanup_sandbox
# ---------------------------------------------------------------------------

class TestTartCleanupSandbox:
    def setup_method(self):
        self._saved = TartRuntime._vm_procs.copy()
        TartRuntime._vm_procs.clear()

    def teardown_method(self):
        TartRuntime._vm_procs.clear()
        TartRuntime._vm_procs.update(self._saved)

    @patch("ralph.runtime.tart.subprocess.run")
    def test_stops_and_deletes(self, mock_run):
        t = TartRuntime("/dotfiles", config={"base_image": "img:latest"})
        t.cleanup_sandbox("claude", "my-branch")

        calls = mock_run.call_args_list
        name = TartRuntime.sandbox_name("claude", "my-branch")
        # Stop
        assert calls[0][0][0] == ["tart", "stop", name]
        # Delete
        assert calls[1][0][0] == ["tart", "delete", name]

    @patch("ralph.runtime.tart.subprocess.run")
    def test_waits_for_tracked_proc(self, mock_run):
        t = TartRuntime("/dotfiles", config={"base_image": "img:latest"})
        name = TartRuntime.sandbox_name("claude", "my-branch")
        proc = MagicMock()
        t._vm_procs[name] = proc
        t.cleanup_sandbox("claude", "my-branch")
        proc.wait.assert_called_once()
        assert name not in t._vm_procs

    @patch("ralph.runtime.tart.subprocess.run")
    def test_no_tracked_proc(self, mock_run):
        """Works fine even without a tracked proc."""
        t = TartRuntime("/dotfiles", config={"base_image": "img:latest"})
        t.cleanup_sandbox("claude", "my-branch")  # should not raise


# ---------------------------------------------------------------------------
# TartRuntime.remove_sandbox
# ---------------------------------------------------------------------------

class TestTartRemoveSandbox:
    def setup_method(self):
        self._saved = TartRuntime._vm_procs.copy()
        TartRuntime._vm_procs.clear()

    def teardown_method(self):
        TartRuntime._vm_procs.clear()
        TartRuntime._vm_procs.update(self._saved)

    @patch("ralph.runtime.tart.subprocess.run")
    def test_stops_and_deletes_by_name(self, mock_run):
        t = TartRuntime("/dotfiles", config={"base_image": "img:latest"})
        t.remove_sandbox("some-vm")
        calls = mock_run.call_args_list
        assert calls[0][0][0] == ["tart", "stop", "some-vm"]
        assert calls[1][0][0] == ["tart", "delete", "some-vm"]

    @patch("ralph.runtime.tart.subprocess.run")
    def test_cleans_up_tracked_proc(self, mock_run):
        t = TartRuntime("/dotfiles", config={"base_image": "img:latest"})
        proc = MagicMock()
        t._vm_procs["some-vm"] = proc
        t.remove_sandbox("some-vm")
        proc.wait.assert_called_once()
        assert "some-vm" not in t._vm_procs


# ---------------------------------------------------------------------------
# TartRuntime.prune_sandboxes
# ---------------------------------------------------------------------------

class TestTartPruneSandboxes:
    @patch("ralph.runtime.tart.subprocess.run")
    @patch.object(TartRuntime, "_list_vms")
    def test_removes_stopped_non_template_vms(self, mock_list, mock_run):
        mock_list.return_value = [
            {"Name": "agent-loop-claude-old-branch", "State": "Stopped"},
            {"Name": "agent-loop-claude-active", "State": "Running"},
            {"Name": "agent-loop-template-claude-abc123", "State": "Stopped"},
            {"Name": "other-vm", "State": "Stopped"},
        ]
        t = TartRuntime("/dotfiles", config={"base_image": "img:latest"})
        # Mark running VM as recently used so it won't be pruned
        with patch.object(t, "_sandbox_last_used",
                          side_effect=lambda n: time.time() if "active" in n else None):
            pruned = t.prune_sandboxes("claude")
        assert pruned == ["agent-loop-claude-old-branch"]

    @patch("ralph.runtime.tart.subprocess.run")
    @patch.object(TartRuntime, "_list_vms")
    def test_keeps_running_vms(self, mock_list, mock_run):
        mock_list.return_value = [
            {"Name": "agent-loop-claude-active", "State": "Running"},
        ]
        t = TartRuntime("/dotfiles", config={"base_image": "img:latest"})
        # Mark VM as recently used
        with patch.object(t, "_sandbox_last_used", return_value=time.time()):
            pruned = t.prune_sandboxes("claude")
        assert pruned == []
        mock_run.assert_not_called()

    @patch("ralph.runtime.tart.subprocess.run")
    @patch.object(TartRuntime, "_list_vms")
    def test_skips_templates(self, mock_list, mock_run):
        mock_list.return_value = [
            {"Name": "agent-loop-template-claude-abc123", "State": "Stopped"},
        ]
        t = TartRuntime("/dotfiles", config={"base_image": "img:latest"})
        pruned = t.prune_sandboxes("claude")
        assert pruned == []
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# TartRuntime.preflight_check
# ---------------------------------------------------------------------------

class TestTartPreflightCheck:
    def _make(self):
        return TartRuntime("/dotfiles", config={"base_image": "img:latest"})

    @patch("ralph.runtime.tart.subprocess.run")
    @patch("ralph.runtime.proxy_health_check", return_value=(True, ""))
    @patch("ralph.runtime.read_token_from_keychain",
           return_value={"expiresAt": int(time.time() * 1000) + 600000})
    def test_all_pass(self, _token, _proxy, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        t = self._make()
        failures = t.preflight_check("test-vm", "claude", 18080)
        assert failures == []

    @patch("ralph.runtime.tart.subprocess.run")
    @patch("ralph.runtime.proxy_health_check", return_value=(True, ""))
    @patch("ralph.runtime.read_token_from_keychain", return_value=None)
    def test_token_missing(self, _token, _proxy, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        t = self._make()
        failures = t.preflight_check("test-vm", "claude", 18080)
        assert any("no token found" in f for f in failures)

    @patch("ralph.runtime.tart.subprocess.run")
    @patch("ralph.runtime.proxy_health_check", return_value=(True, ""))
    @patch("ralph.runtime.read_token_from_keychain",
           return_value={"expiresAt": 0})
    def test_token_expired(self, _token, _proxy, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        t = self._make()
        failures = t.preflight_check("test-vm", "claude", 18080)
        assert any("token expired" in f for f in failures)

    @patch("ralph.runtime.tart.subprocess.run")
    @patch("ralph.runtime.proxy_health_check", return_value=(False, ""))
    @patch("ralph.runtime.read_token_from_keychain",
           return_value={"expiresAt": int(time.time() * 1000) + 600000})
    def test_proxy_down(self, _token, _proxy, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        t = self._make()
        failures = t.preflight_check("test-vm", "claude", 18080)
        assert any("proxy not reachable" in f for f in failures)

    @patch("ralph.runtime.tart.subprocess.run")
    @patch("ralph.runtime.proxy_health_check", return_value=(True, ""))
    @patch("ralph.runtime.read_token_from_keychain",
           return_value={"expiresAt": int(time.time() * 1000) + 600000})
    def test_vm_unresponsive(self, _token, _proxy, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        t = self._make()
        failures = t.preflight_check("test-vm", "claude", 18080)
        assert any("not responding" in f for f in failures)


# ---------------------------------------------------------------------------
# TartRuntime.sync_to_host
# ---------------------------------------------------------------------------

class TestTartSyncToHost:
    @patch("ralph.runtime.tart.subprocess.run")
    def test_returns_true_when_commit_visible(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        t = TartRuntime("/dotfiles", config={"base_image": "img:latest"})
        assert t.sync_to_host("test-vm", "abc", "def", "/work") is True
        mock_run.assert_called_once_with(
            ["git", "rev-parse", "--verify", "def"],
            cwd="/work", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )

    @patch("ralph.runtime.tart.subprocess.run")
    def test_returns_false_when_commit_not_visible(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        t = TartRuntime("/dotfiles", config={"base_image": "img:latest"})
        assert t.sync_to_host("test-vm", "abc", "def", "/work") is False


# ---------------------------------------------------------------------------
# TartRuntime.check_in_sync
# ---------------------------------------------------------------------------

class TestTartCheckInSync:
    def test_always_true(self):
        t = TartRuntime("/dotfiles", config={"base_image": "img:latest"})
        assert t.check_in_sync("vm", "/work", MagicMock()) is True


# ---------------------------------------------------------------------------
# TartRuntime.reset_to_host
# ---------------------------------------------------------------------------

class TestTartResetToHost:
    def test_always_true(self):
        t = TartRuntime("/dotfiles", config={"base_image": "img:latest"})
        assert t.reset_to_host("vm", "/work", MagicMock()) is True


# ---------------------------------------------------------------------------
# TartRuntime._atexit_stop_all
# ---------------------------------------------------------------------------

class TestTartAtexitStopAll:
    def setup_method(self):
        """Save and clear class-level _vm_procs before each test."""
        self._saved = TartRuntime._vm_procs.copy()
        TartRuntime._vm_procs.clear()

    def teardown_method(self):
        """Restore class-level _vm_procs after each test."""
        TartRuntime._vm_procs.clear()
        TartRuntime._vm_procs.update(self._saved)

    @patch("ralph.runtime.tart.subprocess.run")
    def test_stops_each_tracked_vm(self, mock_run):
        proc1 = MagicMock()
        proc2 = MagicMock()
        TartRuntime._vm_procs["vm-one"] = proc1
        TartRuntime._vm_procs["vm-two"] = proc2

        TartRuntime._atexit_stop_all()

        # tart stop called for each VM
        stop_calls = [c for c in mock_run.call_args_list
                      if c[0][0][:2] == ["tart", "stop"]]
        stopped_names = {c[0][0][2] for c in stop_calls}
        assert stopped_names == {"vm-one", "vm-two"}

        # wait called for each proc
        proc1.wait.assert_called_once_with(timeout=10)
        proc2.wait.assert_called_once_with(timeout=10)

    @patch("ralph.runtime.tart.subprocess.run")
    def test_clears_vm_procs_after_cleanup(self, mock_run):
        TartRuntime._vm_procs["vm-x"] = MagicMock()
        TartRuntime._atexit_stop_all()
        assert TartRuntime._vm_procs == {}

    @patch("ralph.runtime.tart.subprocess.run")
    def test_handles_empty_vm_procs(self, mock_run):
        TartRuntime._atexit_stop_all()
        mock_run.assert_not_called()
        assert TartRuntime._vm_procs == {}

    @patch("ralph.runtime.tart.subprocess.run", side_effect=OSError("no tart"))
    def test_suppresses_stop_errors(self, mock_run):
        proc = MagicMock()
        TartRuntime._vm_procs["vm-err"] = proc
        # Should not raise
        TartRuntime._atexit_stop_all()
        proc.wait.assert_called_once_with(timeout=10)
        assert TartRuntime._vm_procs == {}

    @patch("ralph.runtime.tart.subprocess.run")
    def test_suppresses_wait_timeout(self, mock_run):
        proc = MagicMock()
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="tart", timeout=10)
        TartRuntime._vm_procs["vm-stuck"] = proc
        # Should not raise
        TartRuntime._atexit_stop_all()
        assert TartRuntime._vm_procs == {}

    def test_vm_procs_shared_across_instances(self):
        t1 = TartRuntime("/dotfiles", config={"base_image": "img:latest"})
        t2 = TartRuntime("/dotfiles", config={"base_image": "img:latest"})
        t1._vm_procs["shared-vm"] = MagicMock()
        assert "shared-vm" in t2._vm_procs
        assert t1._vm_procs is t2._vm_procs
        assert t1._vm_procs is TartRuntime._vm_procs


# ---------------------------------------------------------------------------
# TartRuntime.check_prerequisites
# ---------------------------------------------------------------------------

class TestTartCheckPrerequisites:
    def _make(self, **kwargs):
        config = {"base_image": "img:latest"}
        config.update(kwargs)
        return TartRuntime("/dotfiles", config=config)

    @patch("ralph.runtime.tart.shutil.which",
           return_value="/usr/local/bin/tart")
    def test_tart_and_docker_present(self, mock_which):
        mock_which.side_effect = lambda cmd: (
            "/usr/local/bin/tart" if cmd == "tart"
            else "/usr/local/bin/docker" if cmd == "docker"
            else None)
        ts = self._make()
        assert ts.check_prerequisites() == []

    @patch("ralph.runtime.tart.shutil.which", return_value=None)
    def test_tart_missing(self, mock_which):
        mock_which.side_effect = lambda cmd: (
            None if cmd == "tart"
            else "/usr/local/bin/docker" if cmd == "docker"
            else None)
        ts = self._make()
        errors = ts.check_prerequisites()
        assert len(errors) == 1
        assert "tart is not installed" in errors[0]

    @patch("ralph.runtime.tart.shutil.which", return_value=None)
    def test_docker_missing(self, mock_which):
        mock_which.side_effect = lambda cmd: (
            "/usr/local/bin/tart" if cmd == "tart"
            else None)
        ts = self._make()
        errors = ts.check_prerequisites()
        assert len(errors) == 1
        assert "docker is not installed" in errors[0]

    @patch("ralph.runtime.tart.shutil.which", return_value=None)
    def test_both_missing(self, mock_which):
        ts = self._make()
        errors = ts.check_prerequisites()
        assert len(errors) == 2
