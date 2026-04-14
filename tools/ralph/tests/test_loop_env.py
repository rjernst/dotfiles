"""Tests for mode-dependent sandbox env-var construction in loop.py."""

from unittest.mock import MagicMock, patch, call

import pytest

from ralph.loop import process_issue


def _make_mocks():
    """Build a set of mocks that satisfy process_issue's dependencies."""
    git = MagicMock()
    git.output.side_effect = lambda *a, **kw: {
        ("remote", "get-url", "origin"): "git@github.com:owner/repo.git",
        ("rev-parse", "--show-toplevel"): "/fake/repo",
    }.get(a, "deadbeef")
    git.run.return_value = MagicMock(returncode=0)

    gh = MagicMock()
    gh.issue_view_title.return_value = "[my-branch] Test issue"
    gh.issue_view_body.return_value = (
        "---\nbranch: my-branch\n---\nSpec body"
    )

    runtime = MagicMock()
    runtime.proxy_host.return_value = "host.docker.internal"
    runtime.ensure_sandbox.return_value = "sandbox-1"
    runtime.check_in_sync.return_value = True
    # Make run_iteration return rc=0, and simulate "no commit" (head unchanged)
    runtime.run_iteration.return_value = (0, "spec body [done]")

    return git, gh, runtime


@pytest.fixture
def mock_env():
    """Patch the heavy dependencies so process_issue runs without real I/O."""
    git, gh, runtime = _make_mocks()

    patches = {
        "resolve_repo": patch("ralph.loop.resolve_repo", return_value="owner/repo"),
        "check_deps": patch("ralph.loop.check_dependencies", return_value=[]),
        "unblock": patch("ralph.loop.unblock_ready_specs"),
        "ensure_wt": patch("ralph.loop.ensure_worktree", return_value="/fake/worktree"),
        "try_ff": patch("ralph.loop.try_fast_forward", return_value=None),
        "load_cfg": patch("ralph.loop.load_runtime_config",
                         side_effect=lambda *a, **kw: {
                             "type": "docker-sandbox",
                             "project_dir": "/fake/repo",
                         }),
        "create_rt": patch("ralph.loop.create_runtime", return_value=runtime),
        "health": patch("ralph.loop.proxy_health_check", return_value=(True, "abc", "oauth")),
        "ensure_px": patch("ralph.loop.ensure_proxy"),
    }
    started = {k: p.start() for k, p in patches.items()}
    yield git, gh, runtime, started
    for p in patches.values():
        p.stop()


class TestEnvVarsBranching:
    """process_issue should set mode-dependent env vars for proxy-based agents."""

    def test_oauth_mode_sets_oauth_env_vars(self, mock_env):
        git, gh, runtime, _ = mock_env
        process_issue(
            issue_number=1, git=git, dotfiles_dir="/fake/dotfiles",
            gh=gh, agent="claude", push=False, model="sonnet",
            git_user="test", git_email="test@test.com",
            proxy_port=18080, token="ignored",
            auth_mode="oauth",
        )
        # Inspect the env_vars passed to run_iteration
        call_args = runtime.run_iteration.call_args
        env_vars = call_args[0][3]  # 4th positional arg
        assert env_vars["CLAUDE_CODE_OAUTH_TOKEN"] == "phantom"
        assert "ANTHROPIC_BASE_URL" in env_vars
        assert "http://host.docker.internal:18080" == env_vars["ANTHROPIC_BASE_URL"]
        assert "ANTHROPIC_CUSTOM_MODEL_OPTION" in env_vars
        assert "ANTHROPIC_API_KEY" not in env_vars

    def test_oauth_mode_resolves_model_alias(self, mock_env):
        git, gh, runtime, _ = mock_env
        process_issue(
            issue_number=1, git=git, dotfiles_dir="/fake/dotfiles",
            gh=gh, agent="claude", push=False, model="sonnet",
            git_user="test", git_email="test@test.com",
            proxy_port=18080, token="ignored",
            auth_mode="oauth",
        )
        call_args = runtime.run_iteration.call_args
        env_vars = call_args[0][3]
        # "sonnet" should be resolved to the full model ID
        assert env_vars["ANTHROPIC_CUSTOM_MODEL_OPTION"] == "claude-sonnet-4-6"

    def test_api_key_mode_sets_api_key_env_vars(self, mock_env):
        git, gh, runtime, _ = mock_env
        process_issue(
            issue_number=1, git=git, dotfiles_dir="/fake/dotfiles",
            gh=gh, agent="claude", push=False, model="sonnet",
            git_user="test", git_email="test@test.com",
            proxy_port=18080, token="ignored",
            auth_mode="api_key",
        )
        call_args = runtime.run_iteration.call_args
        env_vars = call_args[0][3]
        assert env_vars["ANTHROPIC_API_KEY"] == "phantom"
        assert env_vars["ANTHROPIC_BASE_URL"] == "http://host.docker.internal:18080"
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env_vars
        assert "ANTHROPIC_CUSTOM_MODEL_OPTION" not in env_vars

    def test_default_auth_mode_uses_oauth(self, mock_env):
        """When auth_mode is None, process_issue should use oauth env vars."""
        git, gh, runtime, _ = mock_env
        process_issue(
            issue_number=1, git=git, dotfiles_dir="/fake/dotfiles",
            gh=gh, agent="claude", push=False, model="sonnet",
            git_user="test", git_email="test@test.com",
            proxy_port=18080, token="ignored",
            auth_mode=None,
        )
        call_args = runtime.run_iteration.call_args
        env_vars = call_args[0][3]
        assert "CLAUDE_CODE_OAUTH_TOKEN" in env_vars
        assert "ANTHROPIC_API_KEY" not in env_vars

    def test_api_key_is_none_for_proxy_agents(self, mock_env):
        """api_key passed to run_iteration should be None for proxy agents."""
        git, gh, runtime, _ = mock_env
        for mode in ("oauth", "api_key"):
            process_issue(
                issue_number=1, git=git, dotfiles_dir="/fake/dotfiles",
                gh=gh, agent="claude", push=False, model="sonnet",
                git_user="test", git_email="test@test.com",
                proxy_port=18080, token="ignored",
                auth_mode=mode,
            )
            call_kwargs = runtime.run_iteration.call_args[1]
            assert call_kwargs.get("api_key") is None


class TestProxyRecoveryPassesAuthMode:
    """When the proxy dies mid-iteration, ensure_proxy is called with auth_mode."""

    def test_proxy_recovery_passes_auth_mode(self, mock_env):
        git, gh, runtime, mocks = mock_env
        # First call: iteration fails (rc=1), proxy health returns unhealthy
        # Second call: iteration succeeds, no new commit
        runtime.run_iteration.side_effect = [
            (1, "spec body"),  # failure
            (0, "spec body [done]"),  # success after recovery
        ]
        mocks["health"].return_value = (False, None, None)

        process_issue(
            issue_number=1, git=git, dotfiles_dir="/fake/dotfiles",
            gh=gh, agent="claude", push=False, model="sonnet",
            git_user="test", git_email="test@test.com",
            proxy_port=18080, token="ignored",
            auth_mode="api_key",
        )
        # ensure_proxy should have been called with auth_mode="api_key"
        mocks["ensure_px"].assert_called_once_with(
            "claude", 18080, "/fake/dotfiles", "api_key"
        )
