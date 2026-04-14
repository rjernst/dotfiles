"""Tests for ralph.agents — agent configuration registry."""

import pytest

from ralph.agents import AGENTS, VALID_AGENTS, VALID_AUTH_MODES, get_agent, get_auth_mode


class TestGetAgent:
    def test_claude_config(self):
        cfg = get_agent("claude")
        assert cfg["cli_command"] == "claude"
        assert cfg["sandbox_agent"] == "claude"
        assert cfg["default_model"] == "sonnet"
        assert cfg["uses_proxy"] is True
        assert cfg["env_var_name"] == "CLAUDE_CODE_OAUTH_TOKEN"
        assert "api.anthropic.com" in cfg["allowed_hosts"]
        assert "statsig.anthropic.com" in cfg["allowed_hosts"]
        assert "sentry.io" in cfg["allowed_hosts"]

    def test_cursor_config(self):
        cfg = get_agent("cursor")
        assert cfg["cli_command"] == "cursor-agent"
        assert cfg["sandbox_agent"] == "shell"
        assert cfg["default_model"] == "auto"
        assert cfg["uses_proxy"] is False
        assert cfg["env_var_name"] == "CURSOR_API_KEY"
        assert "api2.cursor.sh" in cfg["allowed_hosts"]
        assert "api5.cursor.sh" in cfg["allowed_hosts"]
        assert "sentry.io" in cfg["allowed_hosts"]

    def test_unknown_agent_raises(self):
        with pytest.raises(ValueError, match="unknown agent 'unknown'"):
            get_agent("unknown")

    def test_unknown_agent_lists_valid(self):
        with pytest.raises(ValueError, match="claude.*cursor"):
            get_agent("bogus")


class TestCliFlagsCallable:
    def test_claude_flags(self):
        cfg = get_agent("claude")
        flags = cfg["cli_flags"]("sonnet")
        assert "--dangerously-skip-permissions" in flags
        assert "--effort" in flags
        assert "high" in flags

    def test_cursor_flags(self):
        cfg = get_agent("cursor")
        flags = cfg["cli_flags"]("auto")
        assert "--force" in flags
        assert "--trust" in flags
        assert "--output-format" in flags
        assert "text" in flags

    def test_claude_flags_no_cursor_flags(self):
        flags = get_agent("claude")["cli_flags"]("sonnet")
        assert "--force" not in flags
        assert "--trust" not in flags

    def test_cursor_flags_no_claude_flags(self):
        flags = get_agent("cursor")["cli_flags"]("auto")
        assert "--dangerously-skip-permissions" not in flags
        assert "--effort" not in flags


class TestValidAgents:
    def test_contains_claude(self):
        assert "claude" in VALID_AGENTS

    def test_contains_cursor(self):
        assert "cursor" in VALID_AGENTS

    def test_matches_agents_keys(self):
        assert set(VALID_AGENTS) == set(AGENTS.keys())


class TestValidAuthModes:
    def test_contains_oauth(self):
        assert "oauth" in VALID_AUTH_MODES

    def test_contains_api_key(self):
        assert "api_key" in VALID_AUTH_MODES


class TestGetAuthMode:
    def test_claude_default_returns_oauth(self):
        result = get_auth_mode("claude")
        assert result["keychain_service"] == "claude-token"
        assert result["validation_env_var"] == "CLAUDE_CODE_OAUTH_TOKEN"

    def test_claude_explicit_oauth(self):
        result = get_auth_mode("claude", "oauth")
        assert result["keychain_service"] == "claude-token"

    def test_claude_api_key(self):
        result = get_auth_mode("claude", "api_key")
        assert result["keychain_service"] == "claude-api-key"
        assert result["validation_env_var"] == "ANTHROPIC_API_KEY"

    def test_claude_api_key_cli_form(self):
        """CLI-style 'api-key' with hyphen is normalized to 'api_key'."""
        result = get_auth_mode("claude", "api-key")
        assert result["keychain_service"] == "claude-api-key"

    def test_cursor_returns_none(self):
        assert get_auth_mode("cursor") is None

    def test_cursor_with_mode_returns_none(self):
        assert get_auth_mode("cursor", "oauth") is None

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="unknown auth mode 'bogus'"):
            get_auth_mode("claude", "bogus")

    def test_unknown_agent_raises(self):
        with pytest.raises(ValueError, match="unknown agent"):
            get_auth_mode("unknown")
