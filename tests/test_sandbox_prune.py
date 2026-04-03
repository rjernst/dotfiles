"""Tests for sandbox timestamp tracking, age-based pruning, and cleanup-on-completion."""

import os
import sys
import time
from unittest.mock import MagicMock, patch, call

import pytest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_repo_root, "tools", "ralph", "src"))
sys.path.insert(0, os.path.join(_repo_root, "tools", "libs"))

from ralph.sandbox import SandboxBackend, SANDBOX_STATE_DIR
from ralph.sandbox.docker import DockerSandbox
from ralph.sandbox.tart import TartSandbox


# ---------------------------------------------------------------------------
# Base class timestamp tracking
# ---------------------------------------------------------------------------

class TestTimestampTracking:
    def test_touch_creates_file(self, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            sb._touch_sandbox_timestamp("test-sandbox")
            assert (tmp_path / "test-sandbox").exists()

    def test_touch_updates_mtime(self, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            sb._touch_sandbox_timestamp("test-sandbox")
            first_mtime = os.path.getmtime(tmp_path / "test-sandbox")
            # Ensure time advances
            os.utime(tmp_path / "test-sandbox", (first_mtime - 10, first_mtime - 10))
            sb._touch_sandbox_timestamp("test-sandbox")
            second_mtime = os.path.getmtime(tmp_path / "test-sandbox")
            assert second_mtime > first_mtime - 10

    def test_last_used_returns_mtime(self, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            sb._touch_sandbox_timestamp("test-sandbox")
            result = sb._sandbox_last_used("test-sandbox")
            assert result is not None
            assert abs(result - time.time()) < 2

    def test_last_used_returns_none_for_missing(self, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            assert sb._sandbox_last_used("nonexistent") is None

    def test_remove_deletes_file(self, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            sb._touch_sandbox_timestamp("test-sandbox")
            assert (tmp_path / "test-sandbox").exists()
            sb._remove_sandbox_timestamp("test-sandbox")
            assert not (tmp_path / "test-sandbox").exists()

    def test_remove_ignores_missing(self, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            sb._remove_sandbox_timestamp("nonexistent")  # should not raise

    def test_touch_creates_state_dir(self, tmp_path):
        state_dir = str(tmp_path / "nested" / "state")
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", state_dir):
            sb = DockerSandbox("/fake")
            sb._touch_sandbox_timestamp("test-sandbox")
            assert os.path.isdir(state_dir)


# ---------------------------------------------------------------------------
# Docker prune_sandboxes with age-based pruning
# ---------------------------------------------------------------------------

class TestDockerPruneSandboxes:
    def _make_sandbox(self, tmp_path, sb, name, age_days=None, workspace=None):
        """Create a mock sandbox entry and optionally a timestamp file."""
        if workspace:
            os.makedirs(workspace, exist_ok=True)
        if age_days is not None:
            sb._touch_sandbox_timestamp(name)
            ts_path = tmp_path / name
            old_time = time.time() - age_days * 86400
            os.utime(ts_path, (old_time, old_time))

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_prunes_missing_workspace(self, mock_run, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            sb._docker_sandbox_ls = MagicMock(return_value={"vms": [
                {"name": "agent-loop-claude-gone", "workspace": "/nonexistent"},
            ]})
            pruned = sb.prune_sandboxes("claude")
            assert pruned == ["agent-loop-claude-gone"]
            assert not (tmp_path / "agent-loop-claude-gone").exists()

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_prunes_stale_sandbox(self, mock_run, tmp_path):
        workspace = str(tmp_path / "ws")
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            self._make_sandbox(tmp_path, sb, "agent-loop-claude-old",
                               age_days=5, workspace=workspace)
            sb._docker_sandbox_ls = MagicMock(return_value={"vms": [
                {"name": "agent-loop-claude-old", "workspace": workspace},
            ]})
            pruned = sb.prune_sandboxes("claude")
            assert pruned == ["agent-loop-claude-old"]

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_keeps_fresh_sandbox(self, mock_run, tmp_path):
        workspace = str(tmp_path / "ws")
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            self._make_sandbox(tmp_path, sb, "agent-loop-claude-fresh",
                               age_days=0, workspace=workspace)
            sb._docker_sandbox_ls = MagicMock(return_value={"vms": [
                {"name": "agent-loop-claude-fresh", "workspace": workspace},
            ]})
            pruned = sb.prune_sandboxes("claude")
            assert pruned == []

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_prunes_sandbox_without_timestamp(self, mock_run, tmp_path):
        """Sandboxes predating timestamp tracking should be treated as stale."""
        workspace = str(tmp_path / "ws")
        os.makedirs(workspace)
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            sb._docker_sandbox_ls = MagicMock(return_value={"vms": [
                {"name": "agent-loop-claude-legacy", "workspace": workspace},
            ]})
            pruned = sb.prune_sandboxes("claude")
            assert pruned == ["agent-loop-claude-legacy"]

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_custom_max_age(self, mock_run, tmp_path):
        workspace = str(tmp_path / "ws")
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            # 3 days old — stale at default 2 days, but fresh at 5 days
            self._make_sandbox(tmp_path, sb, "agent-loop-claude-mid",
                               age_days=3, workspace=workspace)
            sb._docker_sandbox_ls = MagicMock(return_value={"vms": [
                {"name": "agent-loop-claude-mid", "workspace": workspace},
            ]})
            pruned = sb.prune_sandboxes("claude", max_age_days=5)
            assert pruned == []
            pruned = sb.prune_sandboxes("claude", max_age_days=2)
            assert pruned == ["agent-loop-claude-mid"]

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_ignores_other_agents(self, mock_run, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            sb._docker_sandbox_ls = MagicMock(return_value={"vms": [
                {"name": "agent-loop-codex-branch", "workspace": "/gone"},
            ]})
            pruned = sb.prune_sandboxes("claude")
            assert pruned == []


# ---------------------------------------------------------------------------
# Tart prune_sandboxes with age-based pruning
# ---------------------------------------------------------------------------

class TestTartPruneSandboxes:
    @patch("ralph.sandbox.tart.subprocess.run")
    def test_prunes_stopped_vm(self, mock_run, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = TartSandbox("/fake")
            sb._list_vms = MagicMock(return_value=[
                {"Name": "agent-loop-claude-stopped", "State": "Stopped"},
            ])
            pruned = sb.prune_sandboxes("claude")
            assert pruned == ["agent-loop-claude-stopped"]

    @patch("ralph.sandbox.tart.subprocess.run")
    def test_prunes_stale_running_vm(self, mock_run, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = TartSandbox("/fake")
            sb._touch_sandbox_timestamp("agent-loop-claude-stale")
            old_time = time.time() - 5 * 86400
            os.utime(tmp_path / "agent-loop-claude-stale",
                      (old_time, old_time))
            sb._list_vms = MagicMock(return_value=[
                {"Name": "agent-loop-claude-stale", "State": "Running"},
            ])
            pruned = sb.prune_sandboxes("claude")
            assert pruned == ["agent-loop-claude-stale"]

    @patch("ralph.sandbox.tart.subprocess.run")
    def test_keeps_fresh_running_vm(self, mock_run, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = TartSandbox("/fake")
            sb._touch_sandbox_timestamp("agent-loop-claude-active")
            sb._list_vms = MagicMock(return_value=[
                {"Name": "agent-loop-claude-active", "State": "Running"},
            ])
            pruned = sb.prune_sandboxes("claude")
            assert pruned == []

    @patch("ralph.sandbox.tart.subprocess.run")
    def test_skips_templates(self, mock_run, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = TartSandbox("/fake")
            sb._list_vms = MagicMock(return_value=[
                {"Name": "agent-loop-template-claude-abc123", "State": "Stopped"},
            ])
            pruned = sb.prune_sandboxes("claude")
            assert pruned == []


# ---------------------------------------------------------------------------
# Docker ensure_sandbox touches timestamp
# ---------------------------------------------------------------------------

class TestDockerEnsureSandboxTimestamp:
    @patch("ralph.sandbox.docker.DockerSandbox.apply_network_policy")
    @patch("ralph.sandbox.docker.DockerSandbox._docker_sandbox_create")
    @patch("ralph.sandbox.docker.DockerSandbox.ensure_project_image",
           return_value="img:v1")
    @patch("ralph.sandbox.docker.DockerSandbox.ensure_image",
           return_value="img:v1")
    @patch("ralph.sandbox.docker.DockerSandbox.sandbox_exists",
           return_value=False)
    @patch("ralph.sandbox.docker.DockerSandbox._resolve_git_common_dir",
           return_value=None)
    @patch("ralph.sandbox.docker.get_agent",
           return_value={"sandbox_agent": "claude", "allowed_hosts": []})
    def test_touches_on_create(self, m1, m2, m3, m4, m5, m6, m7, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            sb.ensure_sandbox("claude", "feat", "/ws", project_dir="/proj")
            assert (tmp_path / "agent-loop-claude-feat").exists()

    @patch("ralph.sandbox.docker.DockerSandbox.sandbox_exists",
           return_value=True)
    def test_touches_on_reuse(self, mock_exists, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            sb.ensure_sandbox("claude", "feat", "/ws")
            assert (tmp_path / "agent-loop-claude-feat").exists()


# ---------------------------------------------------------------------------
# cleanup_sandbox removes timestamp
# ---------------------------------------------------------------------------

class TestCleanupRemovesTimestamp:
    @patch("ralph.sandbox.docker.subprocess.run")
    def test_docker_cleanup_removes_timestamp(self, mock_run, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            sb._touch_sandbox_timestamp("agent-loop-claude-done")
            assert (tmp_path / "agent-loop-claude-done").exists()
            sb.cleanup_sandbox("claude", "done")
            assert not (tmp_path / "agent-loop-claude-done").exists()

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_docker_remove_sandbox_removes_timestamp(self, mock_run, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = DockerSandbox("/fake")
            sb._touch_sandbox_timestamp("my-sandbox")
            sb.remove_sandbox("my-sandbox")
            assert not (tmp_path / "my-sandbox").exists()

    @patch("ralph.sandbox.tart.subprocess.run")
    def test_tart_cleanup_removes_timestamp(self, mock_run, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = TartSandbox("/fake")
            sb._touch_sandbox_timestamp("agent-loop-claude-done")
            sb.cleanup_sandbox("claude", "done")
            assert not (tmp_path / "agent-loop-claude-done").exists()

    @patch("ralph.sandbox.tart.subprocess.run")
    def test_tart_remove_sandbox_removes_timestamp(self, mock_run, tmp_path):
        with patch("ralph.sandbox.SANDBOX_STATE_DIR", str(tmp_path)):
            sb = TartSandbox("/fake")
            sb._touch_sandbox_timestamp("my-vm")
            sb.remove_sandbox("my-vm")
            assert not (tmp_path / "my-vm").exists()
