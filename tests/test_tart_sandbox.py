"""Unit tests for ralph tart sandbox backend."""

import os
import subprocess
import sys
from unittest.mock import patch

import pytest

# Add tools/ralph/src and tools/libs to the path
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_repo_root, "tools", "ralph", "src"))
sys.path.insert(0, os.path.join(_repo_root, "tools", "libs"))

from ralph.sandbox.tart import TartSandbox


class TestResolveGitCommonDir:
    def test_returns_none_for_regular_repo(self, tmp_path):
        """A regular repo has a .git directory, not a file — returns None."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert TartSandbox._resolve_git_common_dir(str(repo)) is None

    def test_returns_common_dir_for_worktree(self, tmp_path):
        """A worktree's .git file points into the main repo's .git dir."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wt = tmp_path / "worktree"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", "test-branch",
             str(wt)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        result = TartSandbox._resolve_git_common_dir(str(wt))
        expected = os.path.realpath(str(repo / ".git"))
        assert result == expected

    def test_returns_none_when_no_git(self, tmp_path):
        """Non-git directory has no .git file — returns None."""
        assert TartSandbox._resolve_git_common_dir(str(tmp_path)) is None

    def test_returns_none_for_broken_worktree(self, tmp_path):
        """A worktree whose .git file was replaced with a directory."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wt = tmp_path / "worktree"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", "test-branch",
             str(wt)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Simulate the breakage: replace .git file with a directory
        git_path = wt / ".git"
        git_path.unlink()
        git_path.mkdir()

        assert TartSandbox._resolve_git_common_dir(str(wt)) is None


class TestSetupGitCommonDirSymlink:
    def test_creates_symlink_via_tart_exec(self):
        sandbox = TartSandbox("/fake/dotfiles")
        git_common_dir = "/Users/rjernst/code/felt-league/.git"

        with patch("ralph.sandbox.tart.subprocess.run") as mock_run:
            sandbox._setup_git_common_dir_symlink("my-vm", git_common_dir)

        mock_run.assert_called_once()
        args = mock_run.call_args
        cmd = args[0][0]
        assert cmd[0:3] == ["tart", "exec", "my-vm"]
        bash_script = cmd[-1]
        assert "mkdir -p" in bash_script
        assert "/Users/rjernst/code/felt-league" in bash_script
        assert TartSandbox.SHARED_DIR_GITDIR in bash_script
        assert git_common_dir in bash_script


class TestEnsureSandboxGitDir:
    """Test that ensure_sandbox mounts the git common dir for worktrees."""

    @patch("ralph.sandbox.tart.TartSandbox._wait_for_guest_agent")
    @patch("ralph.sandbox.tart.TartSandbox._check_vm_limit")
    @patch("ralph.sandbox.tart.TartSandbox.ensure_image",
           return_value="template-abc")
    @patch("ralph.sandbox.tart.TartSandbox._vm_state", return_value=None)
    @patch("ralph.sandbox.tart.subprocess.Popen")
    @patch("ralph.sandbox.tart.subprocess.run")
    def test_mounts_git_common_dir_for_worktree(
        self, mock_run, mock_popen, mock_state, mock_image,
        mock_limit, mock_wait,
    ):
        sandbox = TartSandbox("/fake/dotfiles")
        git_common = "/Users/rjernst/code/myrepo/.git"

        with patch.object(
            TartSandbox, "_resolve_git_common_dir", return_value=git_common,
        ), patch.object(
            TartSandbox, "_setup_git_common_dir_symlink",
        ) as mock_symlink:
            sandbox.ensure_sandbox("claude", "my-branch",
                                   "/Users/rjernst/code/myrepo-my-branch")

        # Check tart run includes both --dir flags
        popen_cmd = mock_popen.call_args[0][0]
        assert "--dir=workspace:/Users/rjernst/code/myrepo-my-branch" in \
            " ".join(popen_cmd)
        assert f"--dir=gitdir:{git_common}" in " ".join(popen_cmd)

        # Symlink setup called
        mock_symlink.assert_called_once_with(
            "agent-loop-claude-my-branch", git_common)

    @patch("ralph.sandbox.tart.TartSandbox._wait_for_guest_agent")
    @patch("ralph.sandbox.tart.TartSandbox._check_vm_limit")
    @patch("ralph.sandbox.tart.TartSandbox.ensure_image",
           return_value="template-abc")
    @patch("ralph.sandbox.tart.TartSandbox._vm_state", return_value=None)
    @patch("ralph.sandbox.tart.subprocess.Popen")
    @patch("ralph.sandbox.tart.subprocess.run")
    def test_skips_git_dir_for_regular_repo(
        self, mock_run, mock_popen, mock_state, mock_image,
        mock_limit, mock_wait,
    ):
        sandbox = TartSandbox("/fake/dotfiles")

        with patch.object(
            TartSandbox, "_resolve_git_common_dir", return_value=None,
        ), patch.object(
            TartSandbox, "_setup_git_common_dir_symlink",
        ) as mock_symlink:
            sandbox.ensure_sandbox("claude", "my-branch",
                                   "/Users/rjernst/code/myrepo")

        # No gitdir mount
        popen_cmd = mock_popen.call_args[0][0]
        cmd_str = " ".join(popen_cmd)
        assert "--dir=gitdir:" not in cmd_str

        # No symlink setup
        mock_symlink.assert_not_called()
