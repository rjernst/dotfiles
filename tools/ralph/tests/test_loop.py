"""Tests for ralph.loop — process_issue and poll_loop."""

from unittest.mock import MagicMock, patch

import pytest

from ralph.loop import process_issue, poll_loop


# ---------------------------------------------------------------------------
# process_issue (sandbox-based, mocked)
# ---------------------------------------------------------------------------

class TestProcessIssueSandbox:
    @patch("ralph.loop.create_sandbox_backend")
    @patch("ralph.loop.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_resets_sandbox_when_out_of_sync(self, mock_repo, mock_wt, mock_unblock,
                                             mock_config, mock_create):
        git = MagicMock()
        # All git.output calls return same value — HEAD doesn't change
        git.output.return_value = "/repo/root"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        sandbox.run_iteration.return_value = (0, "updated spec")
        sandbox.check_in_sync.return_value = False
        sandbox.reset_to_host.return_value = True
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)

        sandbox.check_in_sync.assert_called_once_with(
            "agent-loop-claude-my-branch", "/work/my-branch", git)
        sandbox.reset_to_host.assert_called_once_with(
            "agent-loop-claude-my-branch", "/work/my-branch", git)

    @patch("ralph.loop.create_sandbox_backend")
    @patch("ralph.loop.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_recreates_sandbox_when_reset_fails(self, mock_repo, mock_wt, mock_unblock,
                                                 mock_config, mock_create):
        git = MagicMock()
        git.output.return_value = "/repo/root"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        sandbox.run_iteration.return_value = (0, "updated spec")
        sandbox.check_in_sync.return_value = False
        sandbox.reset_to_host.return_value = False
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)

        sandbox.remove_sandbox.assert_called_once_with("agent-loop-claude-my-branch")
        # ensure_sandbox called twice: initial + recreation
        assert sandbox.ensure_sandbox.call_count == 2
        # Recreation should pass project_dir and force_rebuild
        second_call = sandbox.ensure_sandbox.call_args_list[1]
        assert second_call[1].get("project_dir") == "/repo/root"
        assert second_call[1].get("force_rebuild") is False
        # setup_git_config called twice: initial + after recreation
        assert sandbox.setup_git_config.call_count == 2

    @patch("ralph.loop.create_sandbox_backend")
    @patch("ralph.loop.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_uses_ensure_sandbox_and_run_iteration(self, mock_repo, mock_wt, mock_unblock,
                                                    mock_config, mock_create):
        git = MagicMock()
        git.output.return_value = "/repo/root"

        sandbox = MagicMock()
        sandbox.proxy_host.return_value = "host.docker.internal"
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        sandbox.run_iteration.return_value = (0, "updated spec")
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        result = process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)
        assert result == 0

        mock_config.assert_called_once_with("/repo/root")
        mock_create.assert_called_once_with("docker", "/dotfiles",
                                            project_dir="/repo/root")

        sandbox.ensure_sandbox.assert_called_once_with(
            "claude", "my-branch", "/work/my-branch",
            project_dir="/repo/root", force_rebuild=False)
        sandbox.setup_git_config.assert_called_once_with(
            "agent-loop-claude-my-branch", "user", "user@test.com")
        sandbox.run_iteration.assert_called_once()

        # Verify run_iteration received phantom token + proxy base URL (no workdir)
        call_args = sandbox.run_iteration.call_args
        assert "workdir" not in (call_args[1] or {})
        env_vars = call_args[0][3]
        assert env_vars["CLAUDE_CODE_OAUTH_TOKEN"] == "phantom"
        assert env_vars["ANTHROPIC_BASE_URL"] == "http://host.docker.internal:18080"

    @patch("ralph.loop.create_sandbox_backend")
    @patch("ralph.loop.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_proxy_host_used_in_env_vars(self, mock_repo, mock_wt, mock_unblock,
                                          mock_config, mock_create):
        """Verify sandbox.proxy_host() is called for constructing the proxy URL."""
        git = MagicMock()
        git.output.return_value = "/repo/root"

        sandbox = MagicMock()
        sandbox.proxy_host.return_value = "192.168.64.1"
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        sandbox.run_iteration.return_value = (0, "spec")
        sandbox.exec_output.return_value = "abc123"
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)

        sandbox.proxy_host.assert_called_once()
        call_args = sandbox.run_iteration.call_args
        env_vars = call_args[1].get("env_vars") or call_args[0][3]
        assert env_vars["ANTHROPIC_BASE_URL"] == "http://192.168.64.1:18080"

    @patch("ralph.loop.create_sandbox_backend")
    @patch("ralph.loop.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_iteration_failure_marks_needs_attention(self, mock_repo, mock_wt,
                                                      mock_config, mock_create):
        git = MagicMock()
        git.output.return_value = "/repo/root"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        sandbox.run_iteration.return_value = (1, "spec")
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        result = process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)
        assert result == 1

        gh.issue_edit.assert_any_call(
            42, "owner/repo",
            remove_label="status:in-progress",
            add_label="status:needs-attention")

    @patch("ralph.loop.create_sandbox_backend")
    @patch("ralph.loop.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.loop.try_fast_forward", return_value=None)
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_sync_failure_marks_needs_attention(self, mock_repo, mock_wt, mock_ff,
                                                 mock_config, mock_create):
        heads = iter(["abc123", "def456"])
        def _git_output(*args, **kwargs):
            if args == ("rev-parse", "--show-toplevel"):
                return "/repo/root"
            if args[0] == "rev-parse" and len(args) > 1 and args[1] == "HEAD":
                return next(heads)
            return ""
        git = MagicMock()
        git.output.side_effect = _git_output

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        sandbox.run_iteration.return_value = (0, "spec")
        sandbox.sync_to_host.return_value = False
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        result = process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)
        assert result == 1

        gh.issue_edit.assert_any_call(
            42, "owner/repo",
            remove_label="status:in-progress",
            add_label="status:needs-attention")

    @patch("ralph.loop.create_sandbox_backend")
    @patch("ralph.loop.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.loop.try_fast_forward", return_value=None)
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_pushes_after_iteration_when_flag_set(self, mock_repo, mock_wt,
                                                  mock_unblock, mock_ff,
                                                  mock_config, mock_create):
        # HEAD changes on first iteration (abc→def), stays same on second (def→def)
        heads = iter(["abc", "def", "def", "def"])
        def _git_output(*args, **kwargs):
            if args == ("rev-parse", "--show-toplevel"):
                return "/repo/root"
            if args[0] == "rev-parse" and len(args) > 1 and args[1] == "HEAD":
                return next(heads)
            return ""
        git = MagicMock()
        git.output.side_effect = _git_output

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        sandbox.run_iteration.return_value = (0, "updated spec")
        # First call returns "abc", second returns "def" (new commit),
        # third returns "def" (no new commit = done)
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        process_issue(
            42, git, "/dotfiles", gh, "claude", True, "sonnet",
            "user", "user@test.com", 18080)

        git.run.assert_any_call("push", cwd="/work/my-branch", check=False)

    @patch("ralph.loop.create_sandbox_backend")
    @patch("ralph.loop.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_agent_codex_uses_correct_names(self, mock_repo, mock_wt,
                                             mock_config, mock_create):
        git = MagicMock()
        git.output.return_value = "/repo/root"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-codex-my-branch"
        sandbox.run_iteration.return_value = (0, "spec")
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        process_issue(
            42, git, "/dotfiles", gh, "codex", False, "sonnet",
            "user", "user@test.com", 18080)

        sandbox.ensure_sandbox.assert_called_once_with(
            "codex", "my-branch", "/work/my-branch",
            project_dir="/repo/root", force_rebuild=False)

    @patch("ralph.loop.create_sandbox_backend")
    @patch("ralph.loop.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_rebuild_flag_passed_to_ensure_sandbox(self, mock_repo, mock_wt, mock_unblock,
                                                    mock_config, mock_create):
        git = MagicMock()
        git.output.return_value = "/repo/root"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        sandbox.run_iteration.return_value = (0, "spec")
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080, rebuild=True)

        sandbox.ensure_sandbox.assert_called_once_with(
            "claude", "my-branch", "/work/my-branch",
            project_dir="/repo/root", force_rebuild=True)

    @patch("ralph.loop.create_sandbox_backend")
    @patch("ralph.loop.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_stores_issue_number_in_git_config(self, mock_repo, mock_wt, mock_unblock,
                                                mock_config, mock_create):
        git = MagicMock()
        git.output.return_value = "abc123"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        sandbox.run_iteration.return_value = (0, "updated spec")
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)

        git.run.assert_any_call(
            "config", "branch.my-branch.issue", "42",
            cwd="/work/my-branch", check=False)

    @patch("ralph.loop.create_sandbox_backend")
    @patch("ralph.loop.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/feat/slash-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_stores_issue_number_with_slashes_in_branch(self, mock_repo, mock_wt, mock_unblock,
                                                         mock_config, mock_create):
        git = MagicMock()
        git.output.return_value = "abc123"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-feat-slash-branch"
        sandbox.run_iteration.return_value = (0, "updated spec")
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[feat/slash-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: feat/slash-branch\n---\nSpec"

        process_issue(
            99, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)

        git.run.assert_any_call(
            "config", "branch.feat/slash-branch.issue", "99",
            cwd="/work/feat/slash-branch", check=False)

    @patch("ralph.loop.create_sandbox_backend")
    @patch("ralph.loop.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_blocked_marker_marks_needs_attention(self, mock_repo, mock_wt,
                                                   mock_config, mock_create):
        git = MagicMock()
        # HEAD doesn't change = no commit made
        git.output.return_value = "abc123"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        spec_body = (
            "---\nbranch: my-branch\n---\n"
            "# Spec: Test Feature\n\n"
            "## Implementation Plan\n\n"
            "### Step 1: Write code [done]\n\n"
            "### Step 2: Run tests [blocked: pytest not installed]\n"
        )
        sandbox.run_iteration.return_value = (0, spec_body)
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        result = process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)
        assert result == 0

        gh.issue_edit.assert_any_call(
            42, "owner/repo",
            remove_label="status:in-progress",
            add_label="status:needs-attention")

    @patch("ralph.loop.create_sandbox_backend")
    @patch("ralph.loop.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_no_blocked_marker_marks_done_and_unblocks(self, mock_repo, mock_wt, mock_unblock,
                                                        mock_config, mock_create):
        git = MagicMock()
        # HEAD doesn't change = no commit made
        git.output.return_value = "abc123"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        spec_body = (
            "---\nbranch: my-branch\n---\n"
            "# Spec: Test Feature\n\n"
            "## Implementation Plan\n\n"
            "### Step 1: Write code [done]\n\n"
            "### Step 2: Run tests [done]\n"
        )
        sandbox.run_iteration.return_value = (0, spec_body)
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        result = process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)
        assert result == 0

        gh.issue_edit.assert_any_call(
            42, "owner/repo",
            remove_label="status:in-progress",
            add_label="status:done")
        mock_unblock.assert_called_once_with("owner/repo", gh)


# ---------------------------------------------------------------------------
# poll_loop — exception handling
# ---------------------------------------------------------------------------

class TestPollLoopExceptionHandling:
    @patch("ralph.loop.time.sleep")
    @patch("ralph.loop.time.time")
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_exception_marks_needs_attention_and_logs(self, mock_repo,
                                                      mock_unblock,
                                                      mock_time, mock_sleep,
                                                      capsys):
        """When process_issue raises, issue is labeled needs-attention."""
        # time.time() calls: deadline check, then post-sleep deadline, then timeout
        mock_time.side_effect = [0, 0, 0, 999]

        git = MagicMock()
        gh = MagicMock()
        gh.issue_list.return_value = [42]

        with patch("ralph.loop.process_issue", side_effect=RuntimeError("boom")):
            poll_loop(git, "/dotfiles", gh, "claude", False, "sonnet",
                            "user", "user@test.com", 18080, 30, 1)

        # Verify error was logged
        captured = capsys.readouterr()
        assert "unexpected error processing issue #42" in captured.err
        assert "boom" in captured.err

        # Verify needs-attention label was applied
        gh.issue_edit.assert_called_with(
            42, "owner/repo",
            remove_label="status:in-progress",
            add_label="status:needs-attention")

    @patch("ralph.loop.time.sleep")
    @patch("ralph.loop.time.time")
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_exception_in_label_update_does_not_crash(self, mock_repo,
                                                       mock_unblock,
                                                       mock_time, mock_sleep,
                                                       capsys):
        """If the needs-attention label update itself fails, the loop continues."""
        mock_time.side_effect = [0, 0, 0, 999]

        git = MagicMock()
        gh = MagicMock()
        gh.issue_list.return_value = [42]
        gh.issue_edit.side_effect = RuntimeError("gh failed")

        with patch("ralph.loop.process_issue", side_effect=RuntimeError("boom")):
            # Should not raise
            poll_loop(git, "/dotfiles", gh, "claude", False, "sonnet",
                            "user", "user@test.com", 18080, 30, 1)
