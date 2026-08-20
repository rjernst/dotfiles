"""Tests for ralph.loop — process_issue and poll_loop."""

from unittest.mock import MagicMock, patch

import pytest

from ralph.loop import process_issue, poll_loop


# ---------------------------------------------------------------------------
# process_issue (sandbox-based, mocked)
# ---------------------------------------------------------------------------

@patch("ralph.loop.ensure_proxy")
class TestProcessIssueSandbox:
    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_resets_sandbox_when_out_of_sync(self, mock_repo, mock_wt, mock_unblock,
                                             mock_config, mock_create,
                                             mock_ensure_proxy):
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
            "user", "user@test.com", 18080, "sk-test")

        sandbox.check_in_sync.assert_called_once_with(
            "agent-loop-claude-my-branch", "/work/my-branch", git)
        sandbox.reset_to_host.assert_called_once_with(
            "agent-loop-claude-my-branch", "/work/my-branch", git)

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_recreates_sandbox_when_reset_fails(self, mock_repo, mock_wt, mock_unblock,
                                                 mock_config, mock_create,
                                                 mock_ensure_proxy):
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
            "user", "user@test.com", 18080, "sk-test")

        sandbox.remove_sandbox.assert_called_once_with("agent-loop-claude-my-branch")
        # ensure_sandbox called twice: initial + recreation
        assert sandbox.ensure_sandbox.call_count == 2
        # Recreation should pass project_dir and force_rebuild
        second_call = sandbox.ensure_sandbox.call_args_list[1]
        assert second_call[1].get("project_dir") == "/repo/root"
        assert second_call[1].get("force_rebuild") is False
        # setup_git_config called twice: initial + after recreation
        assert sandbox.setup_git_config.call_count == 2

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_uses_ensure_sandbox_and_run_iteration(self, mock_repo, mock_wt, mock_unblock,
                                                    mock_config, mock_create,
                                                    mock_ensure_proxy):
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
            "user", "user@test.com", 18080, "sk-test")
        assert result == 0

        mock_config.assert_called_once_with("/repo/root")
        mock_create.assert_called_once_with("docker-sandbox", "/dotfiles",
                                            project_dir="/repo/root",
                                            auth_mode=None, token_data=None)

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

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_proxy_host_used_in_env_vars(self, mock_repo, mock_wt, mock_unblock,
                                          mock_config, mock_create,
                                          mock_ensure_proxy):
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
            "user", "user@test.com", 18080, "sk-test")

        sandbox.proxy_host.assert_called_once()
        call_args = sandbox.run_iteration.call_args
        env_vars = call_args[1].get("env_vars") or call_args[0][3]
        assert env_vars["ANTHROPIC_BASE_URL"] == "http://192.168.64.1:18080"

    @patch("ralph.loop.proxy_health_check",
           return_value=(True, "abc123", "oauth", "127.0.0.1"))
    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_iteration_failure_marks_needs_attention(self, mock_repo, mock_wt,
                                                      mock_config, mock_create,
                                                      mock_health,
                                                      mock_ensure_proxy):
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
            "user", "user@test.com", 18080, "sk-test")
        assert result == 1

        gh.issue_edit.assert_any_call(
            42, "owner/repo",
            remove_labels="status:in-progress",
            add_label="status:needs-attention")

    @patch("ralph.loop.proxy_health_check",
           return_value=(False, None, None, None))
    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_iteration_failure_restarts_proxy_and_retries(self, mock_repo, mock_wt,
                                                           mock_unblock, mock_config,
                                                           mock_create, mock_health,
                                                           mock_ensure_proxy):
        git = MagicMock()
        git.output.return_value = "/repo/root"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        # First iteration fails (proxy down), retry succeeds
        sandbox.run_iteration.side_effect = [(1, "spec"), (0, "spec")]
        sandbox.proxy_listen_addr.return_value = "127.0.0.1"
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        result = process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080, "sk-test")
        assert result == 0

        # Called twice: proactive check at entry + reactive restart on failure
        assert mock_ensure_proxy.call_count == 2
        mock_ensure_proxy.assert_any_call("claude", 18080, "/dotfiles",
                                          None, "127.0.0.1")
        assert sandbox.run_iteration.call_count == 2

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.try_fast_forward", return_value=None)
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_sync_failure_marks_needs_attention(self, mock_repo, mock_wt, mock_ff,
                                                 mock_config, mock_create,
                                                 mock_ensure_proxy):
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
            "user", "user@test.com", 18080, "sk-test")
        assert result == 1

        gh.issue_edit.assert_any_call(
            42, "owner/repo",
            remove_labels="status:in-progress",
            add_label="status:needs-attention")

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.try_fast_forward", return_value=None)
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_pushes_after_iteration_when_flag_set(self, mock_repo, mock_wt,
                                                  mock_unblock, mock_ff,
                                                  mock_config, mock_create,
                                                  mock_ensure_proxy):
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
            "user", "user@test.com", 18080, "sk-test")

        git.run.assert_any_call("push", cwd="/work/my-branch", check=False)

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_agent_cursor_uses_correct_names(self, mock_repo, mock_wt,
                                              mock_config, mock_create,
                                              mock_ensure_proxy):
        git = MagicMock()
        git.output.return_value = "/repo/root"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-cursor-my-branch"
        sandbox.run_iteration.return_value = (0, "spec")
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        process_issue(
            42, git, "/dotfiles", gh, "cursor", False, "auto",
            "user", "user@test.com", 18080, "cursor-key")

        sandbox.ensure_sandbox.assert_called_once_with(
            "cursor", "my-branch", "/work/my-branch",
            project_dir="/repo/root", force_rebuild=False)

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_rebuild_flag_passed_to_ensure_sandbox(self, mock_repo, mock_wt, mock_unblock,
                                                    mock_config, mock_create,
                                                    mock_ensure_proxy):
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
            "user", "user@test.com", 18080, "sk-test", rebuild=True)

        sandbox.ensure_sandbox.assert_called_once_with(
            "claude", "my-branch", "/work/my-branch",
            project_dir="/repo/root", force_rebuild=True)

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_stores_issue_number_in_git_config(self, mock_repo, mock_wt, mock_unblock,
                                                mock_config, mock_create,
                                                mock_ensure_proxy):
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
            "user", "user@test.com", 18080, "sk-test")

        git.run.assert_any_call(
            "config", "branch.my-branch.issue", "42",
            cwd="/work/my-branch", check=False)

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/feat/slash-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_stores_issue_number_with_slashes_in_branch(self, mock_repo, mock_wt, mock_unblock,
                                                         mock_config, mock_create,
                                                         mock_ensure_proxy):
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
            "user", "user@test.com", 18080, "sk-test")

        git.run.assert_any_call(
            "config", "branch.feat/slash-branch.issue", "99",
            cwd="/work/feat/slash-branch", check=False)

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_blocked_marker_marks_needs_attention(self, mock_repo, mock_wt,
                                                   mock_config, mock_create,
                                                   mock_ensure_proxy):
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
            "user", "user@test.com", 18080, "sk-test")
        assert result == 0

        gh.issue_edit.assert_any_call(
            42, "owner/repo",
            remove_labels="status:in-progress",
            add_label="status:needs-attention")

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_cursor_agent_passes_no_proxy_env_vars(self, mock_repo, mock_wt, mock_unblock,
                                                    mock_config, mock_create,
                                                    mock_ensure_proxy):
        """Cursor agent should pass empty env_vars and api_key to run_iteration."""
        git = MagicMock()
        git.output.return_value = "/repo/root"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-cursor-my-branch"
        sandbox.run_iteration.return_value = (0, "updated spec")
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        process_issue(
            42, git, "/dotfiles", gh, "cursor", False, "auto",
            "user", "user@test.com", 18080, "cursor-key-123")

        call_args = sandbox.run_iteration.call_args
        env_vars = call_args[0][3]
        # Cursor should not have proxy env vars
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env_vars
        assert "ANTHROPIC_BASE_URL" not in env_vars
        assert env_vars == {}
        # Should pass agent and api_key as kwargs
        assert call_args[1]["agent"] == "cursor"
        assert call_args[1]["api_key"] == "cursor-key-123"

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_cursor_agent_does_not_check_proxy_on_failure(self, mock_repo, mock_wt, mock_unblock,
                                                           mock_config, mock_create,
                                                           mock_ensure_proxy):
        """Cursor iteration failure should not check proxy health."""
        git = MagicMock()
        git.output.return_value = "/repo/root"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-cursor-my-branch"
        sandbox.run_iteration.return_value = (1, "spec")
        mock_create.return_value = sandbox

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        with patch("ralph.loop.proxy_health_check") as mock_health:
            result = process_issue(
                42, git, "/dotfiles", gh, "cursor", False, "auto",
                "user", "user@test.com", 18080, "cursor-key")
            assert result == 1
            # Should NOT have checked proxy health
            mock_health.assert_not_called()

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_claude_agent_passes_proxy_env_vars(self, mock_repo, mock_wt, mock_unblock,
                                                 mock_config, mock_create,
                                                 mock_ensure_proxy):
        """Claude agent should pass proxy env vars and agent/api_key kwargs."""
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

        process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080, "sk-test")

        call_args = sandbox.run_iteration.call_args
        env_vars = call_args[0][3]
        assert env_vars["CLAUDE_CODE_OAUTH_TOKEN"] == "phantom"
        assert env_vars["ANTHROPIC_BASE_URL"] == "http://host.docker.internal:18080"
        assert call_args[1]["agent"] == "claude"
        assert call_args[1]["api_key"] is None

    @patch("ralph.loop._open_review_workspace")
    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "docker-sandbox"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_no_blocked_marker_marks_done_and_unblocks(self, mock_repo, mock_wt, mock_unblock,
                                                        mock_config, mock_create,
                                                        mock_open_review,
                                                        mock_ensure_proxy):
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
            "user", "user@test.com", 18080, "sk-test")
        assert result == 0

        gh.issue_edit.assert_any_call(
            42, "owner/repo",
            remove_labels="status:in-progress",
            add_label="status:done")
        mock_unblock.assert_called_once_with("owner/repo", gh)
        mock_open_review.assert_called_once_with("my-branch", 42)


# ---------------------------------------------------------------------------
# _open_review_workspace
# ---------------------------------------------------------------------------

class TestOpenReviewWorkspace:
    @patch("ralph.loop.subprocess.run")
    @patch("ralph.loop.platform.system", return_value="Darwin")
    def test_opens_workspace_and_notifies_on_macos(self, mock_platform, mock_run):
        from ralph.loop import _open_review_workspace
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        _open_review_workspace("my-branch", 42)

        first_call = mock_run.call_args_list[0]
        assert first_call[0][0] == ["ta", "workspace", "create", "my-branch",
                                     "--cmd", 'claude "/review"']
        second_call = mock_run.call_args_list[1]
        assert second_call[0][0][0] == "osascript"
        assert "my-branch" in second_call[0][0][-1]
        assert "42" in second_call[0][0][-1]

    @patch("ralph.loop.subprocess.run")
    @patch("ralph.loop.platform.system", return_value="Linux")
    def test_no_notification_on_non_macos(self, mock_platform, mock_run):
        from ralph.loop import _open_review_workspace
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        _open_review_workspace("my-branch", 42)

        assert mock_run.call_count == 1  # only the ta workspace create call

    @patch("ralph.loop.subprocess.run")
    @patch("ralph.loop.platform.system", return_value="Darwin")
    def test_workspace_failure_does_not_raise(self, mock_platform, mock_run, capsys):
        from ralph.loop import _open_review_workspace
        mock_run.return_value = MagicMock(returncode=1, stderr="session exists")

        _open_review_workspace("my-branch", 42)

        captured = capsys.readouterr()
        assert "warning" in captured.err


# ---------------------------------------------------------------------------
# process_issue — runtimes that inject credentials themselves (nono)
# ---------------------------------------------------------------------------

@patch("ralph.loop.ensure_proxy")
class TestProcessIssueNoCredentialProxy:
    @staticmethod
    def _runtime(rc=0):
        runtime = MagicMock()
        runtime.uses_credential_proxy = False
        runtime.proxy_host.return_value = "127.0.0.1"
        runtime.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        runtime.run_iteration.return_value = (rc, "updated spec")
        return runtime

    @staticmethod
    def _gh():
        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"
        return gh

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "nono"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_iteration_runs_without_ensure_proxy(self, mock_repo, mock_wt,
                                                 mock_unblock, mock_config,
                                                 mock_create,
                                                 mock_ensure_proxy):
        git = MagicMock()
        git.output.return_value = "/repo/root"
        runtime = self._runtime()
        mock_create.return_value = runtime

        result = process_issue(
            42, git, "/dotfiles", self._gh(), "claude", False, "sonnet",
            "user", "user@test.com", None, "sk-test")
        assert result == 0

        mock_create.assert_called_once_with("nono", "/dotfiles",
                                            project_dir="/repo/root",
                                            auth_mode=None, token_data=None)
        runtime.run_iteration.assert_called_once()
        mock_ensure_proxy.assert_not_called()

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "nono"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_credentials_passed_to_runtime(self, mock_repo, mock_wt,
                                           mock_unblock, mock_config,
                                           mock_create, mock_ensure_proxy):
        """The token store's auth mode and data reach the runtime backend."""
        git = MagicMock()
        git.output.return_value = "/repo/root"
        mock_create.return_value = self._runtime()
        token_data = {"accessToken": "phantom",
                      "baseUrl": "https://gw.example.com"}

        process_issue(
            42, git, "/dotfiles", self._gh(), "claude", False, "sonnet",
            "user", "user@test.com", None, "sk-test",
            auth_mode="gateway", token_data=token_data)

        mock_create.assert_called_once_with("nono", "/dotfiles",
                                            project_dir="/repo/root",
                                            auth_mode="gateway",
                                            token_data=token_data)

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config",
           return_value={"type": "nono", "auth_mode": "api_key",
                         "token_data": {"accessToken": "from-config"}})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_project_config_cannot_override_credentials(
            self, mock_repo, mock_wt, mock_unblock, mock_config, mock_create,
            mock_ensure_proxy):
        git = MagicMock()
        git.output.return_value = "/repo/root"
        mock_create.return_value = self._runtime()

        process_issue(
            42, git, "/dotfiles", self._gh(), "claude", False, "sonnet",
            "user", "user@test.com", None, "sk-test",
            auth_mode="oauth", token_data={"accessToken": "real"})

        mock_create.assert_called_once_with(
            "nono", "/dotfiles", project_dir="/repo/root",
            auth_mode="oauth", token_data={"accessToken": "real"})

    @patch("ralph.loop.create_runtime")
    @patch("ralph.loop.load_runtime_config", return_value={"type": "nono"})
    @patch("ralph.loop.unblock_ready_specs")
    @patch("ralph.loop.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.loop.resolve_repo", return_value="owner/repo")
    def test_failed_iteration_does_not_check_proxy(self, mock_repo, mock_wt,
                                                   mock_unblock, mock_config,
                                                   mock_create,
                                                   mock_ensure_proxy):
        git = MagicMock()
        git.output.return_value = "/repo/root"
        mock_create.return_value = self._runtime(rc=1)
        gh = self._gh()

        with patch("ralph.loop.proxy_health_check") as mock_health:
            result = process_issue(
                42, git, "/dotfiles", gh, "claude", False, "sonnet",
                "user", "user@test.com", None, "sk-test")
            assert result == 1
            mock_health.assert_not_called()

        mock_ensure_proxy.assert_not_called()
        gh.issue_edit.assert_any_call(
            42, "owner/repo",
            remove_labels="status:in-progress",
            add_label="status:needs-attention")


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
                            "user", "user@test.com", 18080, "sk-test", 30, 1)

        # Verify error was logged
        captured = capsys.readouterr()
        assert "unexpected error processing issue #42" in captured.err
        assert "boom" in captured.err

        # Verify needs-attention label was applied (removes both ready and in-progress)
        gh.issue_edit.assert_called_with(
            42, "owner/repo",
            remove_labels=["status:ready", "status:in-progress"],
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
                            "user", "user@test.com", 18080, "sk-test", 30, 1)
