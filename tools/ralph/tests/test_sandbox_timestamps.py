"""Tests for sandbox timestamp tracking, age-based pruning, and cleanup.

The prune tests in test_runtime_docker_sandbox.py and test_runtime_tart.py
mock _sandbox_last_used; these exercise the real timestamp files under
SANDBOX_STATE_DIR so the max_age_days arithmetic is covered end to end.
"""

import os
import time
from unittest.mock import MagicMock, patch

from ralph.runtime.docker_sandbox import DockerSandboxRuntime
from ralph.runtime.tart import TartRuntime


def _tart(dotfiles_dir="/fake"):
    return TartRuntime(dotfiles_dir, config={"base_image": "img:latest"})


# ---------------------------------------------------------------------------
# Base class timestamp tracking
# ---------------------------------------------------------------------------

class TestTimestampTracking:
    def test_touch_creates_file(self, tmp_path):
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandboxRuntime("/fake")
            sb._touch_sandbox_timestamp("test-sandbox")
            assert (tmp_path / "test-sandbox").exists()

    def test_touch_updates_mtime(self, tmp_path):
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandboxRuntime("/fake")
            sb._touch_sandbox_timestamp("test-sandbox")
            first_mtime = os.path.getmtime(tmp_path / "test-sandbox")
            # Backdate so the refresh is observable without sleeping
            os.utime(tmp_path / "test-sandbox", (first_mtime - 10, first_mtime - 10))
            sb._touch_sandbox_timestamp("test-sandbox")
            second_mtime = os.path.getmtime(tmp_path / "test-sandbox")
            assert second_mtime > first_mtime - 10

    def test_last_used_returns_mtime(self, tmp_path):
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandboxRuntime("/fake")
            sb._touch_sandbox_timestamp("test-sandbox")
            result = sb._sandbox_last_used("test-sandbox")
            assert result is not None
            assert abs(result - time.time()) < 2

    def test_last_used_returns_none_for_missing(self, tmp_path):
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandboxRuntime("/fake")
            assert sb._sandbox_last_used("nonexistent") is None

    def test_remove_deletes_file(self, tmp_path):
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandboxRuntime("/fake")
            sb._touch_sandbox_timestamp("test-sandbox")
            assert (tmp_path / "test-sandbox").exists()
            sb._remove_sandbox_timestamp("test-sandbox")
            assert not (tmp_path / "test-sandbox").exists()

    def test_remove_ignores_missing(self, tmp_path):
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandboxRuntime("/fake")
            sb._remove_sandbox_timestamp("nonexistent")  # should not raise

    def test_touch_creates_state_dir(self, tmp_path):
        state_dir = str(tmp_path / "nested" / "state")
        with patch("ralph.runtime.SANDBOX_STATE_DIR", state_dir):
            sb = DockerSandboxRuntime("/fake")
            sb._touch_sandbox_timestamp("test-sandbox")
            assert os.path.isdir(state_dir)


# ---------------------------------------------------------------------------
# Docker age-based pruning against real timestamp files
# ---------------------------------------------------------------------------

class TestDockerPruneSandboxAge:
    def _age(self, tmp_path, sb, name, age_days):
        """Create a timestamp file for name, backdated by age_days."""
        sb._touch_sandbox_timestamp(name)
        old_time = time.time() - age_days * 86400
        os.utime(tmp_path / name, (old_time, old_time))

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_prunes_stale_sandbox(self, mock_run, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandboxRuntime("/fake")
            self._age(tmp_path, sb, "agent-loop-claude-old", 5)
            sb._docker_sandbox_ls = MagicMock(return_value={"vms": [
                {"name": "agent-loop-claude-old", "workspace": str(workspace)},
            ]})
            assert sb.prune_sandboxes("claude") == ["agent-loop-claude-old"]
            # Pruning also clears the timestamp file
            assert not (tmp_path / "agent-loop-claude-old").exists()

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_keeps_fresh_sandbox(self, mock_run, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandboxRuntime("/fake")
            self._age(tmp_path, sb, "agent-loop-claude-fresh", 0)
            sb._docker_sandbox_ls = MagicMock(return_value={"vms": [
                {"name": "agent-loop-claude-fresh", "workspace": str(workspace)},
            ]})
            assert sb.prune_sandboxes("claude") == []
            mock_run.assert_not_called()

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_prunes_sandbox_without_timestamp(self, mock_run, tmp_path):
        """Sandboxes predating timestamp tracking are treated as stale."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandboxRuntime("/fake")
            sb._docker_sandbox_ls = MagicMock(return_value={"vms": [
                {"name": "agent-loop-claude-legacy", "workspace": str(workspace)},
            ]})
            assert sb.prune_sandboxes("claude") == ["agent-loop-claude-legacy"]

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_custom_max_age(self, mock_run, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandboxRuntime("/fake")
            # 3 days old — fresh at max_age_days=5, stale at 2
            self._age(tmp_path, sb, "agent-loop-claude-mid", 3)
            sb._docker_sandbox_ls = MagicMock(return_value={"vms": [
                {"name": "agent-loop-claude-mid", "workspace": str(workspace)},
            ]})
            assert sb.prune_sandboxes("claude", max_age_days=5) == []
            assert sb.prune_sandboxes("claude", max_age_days=2) == \
                ["agent-loop-claude-mid"]

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_default_max_age_is_prune_max_age_days(self, mock_run, tmp_path):
        """Omitting max_age_days uses PRUNE_MAX_AGE_DAYS."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandboxRuntime("/fake")
            just_fresh = DockerSandboxRuntime.PRUNE_MAX_AGE_DAYS - 0.5
            self._age(tmp_path, sb, "agent-loop-claude-a", just_fresh)
            sb._docker_sandbox_ls = MagicMock(return_value={"vms": [
                {"name": "agent-loop-claude-a", "workspace": str(workspace)},
            ]})
            assert sb.prune_sandboxes("claude") == []

            just_stale = DockerSandboxRuntime.PRUNE_MAX_AGE_DAYS + 0.5
            self._age(tmp_path, sb, "agent-loop-claude-a", just_stale)
            assert sb.prune_sandboxes("claude") == ["agent-loop-claude-a"]


# ---------------------------------------------------------------------------
# Tart age-based pruning of running VMs against real timestamp files
# ---------------------------------------------------------------------------

class TestTartPruneSandboxAge:
    @patch("ralph.runtime.tart.subprocess.run")
    def test_prunes_stale_running_vm(self, mock_run, tmp_path):
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            t = _tart()
            t._touch_sandbox_timestamp("agent-loop-claude-stale")
            old_time = time.time() - 5 * 86400
            os.utime(tmp_path / "agent-loop-claude-stale", (old_time, old_time))
            t._list_vms = MagicMock(return_value=[
                {"Name": "agent-loop-claude-stale", "State": "Running"},
            ])
            assert t.prune_sandboxes("claude") == ["agent-loop-claude-stale"]
            assert not (tmp_path / "agent-loop-claude-stale").exists()

    @patch("ralph.runtime.tart.subprocess.run")
    def test_keeps_fresh_running_vm(self, mock_run, tmp_path):
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            t = _tart()
            t._touch_sandbox_timestamp("agent-loop-claude-active")
            t._list_vms = MagicMock(return_value=[
                {"Name": "agent-loop-claude-active", "State": "Running"},
            ])
            assert t.prune_sandboxes("claude") == []
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# ensure_sandbox touches the timestamp on both create and reuse
# ---------------------------------------------------------------------------

class TestEnsureSandboxTouchesTimestamp:
    @patch.object(DockerSandboxRuntime, "apply_network_policy")
    @patch.object(DockerSandboxRuntime, "_write_sandbox_fingerprint")
    @patch.object(DockerSandboxRuntime, "_docker_sandbox_create")
    @patch.object(DockerSandboxRuntime, "ensure_project_image",
                  return_value="img:v1")
    @patch.object(DockerSandboxRuntime, "ensure_image", return_value="img:v1")
    @patch.object(DockerSandboxRuntime, "sandbox_exists", return_value=False)
    @patch.object(DockerSandboxRuntime, "_resolve_git_common_dir",
                  return_value=None)
    def test_docker_touches_on_create(self, _resolve, _exists, _image,
                                      _project_image, _create, _write_fp,
                                      _network, tmp_path):
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandboxRuntime("/fake")
            sb.ensure_sandbox("claude", "feat", "/ws", project_dir="/proj")
            assert (tmp_path / "agent-loop-claude-feat").exists()

    @patch.object(DockerSandboxRuntime, "_read_sandbox_fingerprint")
    @patch.object(DockerSandboxRuntime, "_config_fingerprint",
                  return_value="fp")
    @patch.object(DockerSandboxRuntime, "ensure_image", return_value="img:v1")
    @patch.object(DockerSandboxRuntime, "sandbox_exists", return_value=True)
    @patch.object(DockerSandboxRuntime, "_resolve_git_common_dir",
                  return_value=None)
    def test_docker_touches_on_reuse(self, _resolve, _exists, _image,
                                     _fingerprint, mock_read, tmp_path):
        """A reused sandbox must be refreshed or pruning will reclaim it."""
        mock_read.return_value = "fp"
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandboxRuntime("/fake")
            sb.ensure_sandbox("claude", "feat", "/ws")
            assert (tmp_path / "agent-loop-claude-feat").exists()

    @patch.object(TartRuntime, "_setup_worktree_git")
    @patch.object(TartRuntime, "_resolve_git_common_dir", return_value=None)
    @patch.object(TartRuntime, "_vm_state", return_value="Running")
    def test_tart_touches_on_reuse(self, _state, _resolve, _setup, tmp_path):
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            t = _tart()
            t.ensure_sandbox("claude", "feat", "/ws")
            assert (tmp_path / "agent-loop-claude-feat").exists()


# ---------------------------------------------------------------------------
# cleanup_sandbox / remove_sandbox remove the timestamp
# ---------------------------------------------------------------------------

class TestCleanupRemovesTimestamp:
    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_docker_cleanup_removes_timestamp(self, mock_run, tmp_path):
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandboxRuntime("/fake")
            sb._touch_sandbox_timestamp("agent-loop-claude-done")
            assert (tmp_path / "agent-loop-claude-done").exists()
            sb.cleanup_sandbox("claude", "done")
            assert not (tmp_path / "agent-loop-claude-done").exists()

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_docker_remove_sandbox_removes_timestamp(self, mock_run, tmp_path):
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandboxRuntime("/fake")
            sb._touch_sandbox_timestamp("my-sandbox")
            sb.remove_sandbox("my-sandbox")
            assert not (tmp_path / "my-sandbox").exists()

    @patch("ralph.runtime.tart.subprocess.run")
    def test_tart_cleanup_removes_timestamp(self, mock_run, tmp_path):
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            t = _tart()
            t._touch_sandbox_timestamp("agent-loop-claude-done")
            t.cleanup_sandbox("claude", "done")
            assert not (tmp_path / "agent-loop-claude-done").exists()

    @patch("ralph.runtime.tart.subprocess.run")
    def test_tart_remove_sandbox_removes_timestamp(self, mock_run, tmp_path):
        with patch("ralph.runtime.SANDBOX_STATE_DIR", str(tmp_path)):
            t = _tart()
            t._touch_sandbox_timestamp("my-vm")
            t.remove_sandbox("my-vm")
            assert not (tmp_path / "my-vm").exists()
