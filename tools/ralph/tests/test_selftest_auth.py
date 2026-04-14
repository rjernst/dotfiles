"""Tests for selftest mode-awareness (auth_mode support)."""

import time
from unittest.mock import MagicMock, patch, call

import pytest

from ralph.selftest import selftest
from ralph.proxy import build_proxy_env, MODEL_ALIASES
from ralph.token import MS_PER_DAY


# ---------------------------------------------------------------------------
# build_proxy_env tests
# ---------------------------------------------------------------------------

class TestBuildProxyEnv:
    def test_oauth_mode_with_model(self):
        env = build_proxy_env("oauth", "host.docker.internal", 18080, "sonnet")
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "phantom"
        assert env["ANTHROPIC_BASE_URL"] == "http://host.docker.internal:18080"
        assert env["ANTHROPIC_CUSTOM_MODEL_OPTION"] == MODEL_ALIASES["sonnet"]
        assert "ANTHROPIC_API_KEY" not in env

    def test_oauth_mode_without_model(self):
        env = build_proxy_env("oauth", "host.docker.internal", 18080)
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "phantom"
        assert env["ANTHROPIC_BASE_URL"] == "http://host.docker.internal:18080"
        assert "ANTHROPIC_CUSTOM_MODEL_OPTION" not in env

    def test_api_key_mode(self):
        env = build_proxy_env("api_key", "host.docker.internal", 18080, "sonnet")
        assert env["ANTHROPIC_API_KEY"] == "phantom"
        assert env["ANTHROPIC_BASE_URL"] == "http://host.docker.internal:18080"
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        assert "ANTHROPIC_CUSTOM_MODEL_OPTION" not in env

    def test_none_mode_defaults_to_oauth(self):
        env = build_proxy_env(None, "localhost", 8080, "haiku")
        assert "CLAUDE_CODE_OAUTH_TOKEN" in env
        assert "ANTHROPIC_API_KEY" not in env

    def test_model_alias_resolved(self):
        env = build_proxy_env("oauth", "localhost", 8080, "haiku")
        assert env["ANTHROPIC_CUSTOM_MODEL_OPTION"] == MODEL_ALIASES["haiku"]

    def test_unknown_model_passed_through(self):
        env = build_proxy_env("oauth", "localhost", 8080, "claude-custom-model")
        assert env["ANTHROPIC_CUSTOM_MODEL_OPTION"] == "claude-custom-model"


# ---------------------------------------------------------------------------
# selftest mode-awareness tests
# ---------------------------------------------------------------------------

def _patch_selftest_deps():
    """Return a dict of patches that isolate selftest from real I/O."""
    return {
        "proxy_health_check": patch(
            "ralph.selftest.proxy_health_check",
            return_value=(False, None, None)),
        "proxy_port": patch(
            "ralph.selftest.proxy_port_for_agent", return_value=18080),
        "ensure_proxy": patch("ralph.selftest.ensure_proxy"),
        "stop_proxy": patch("ralph.selftest.stop_proxy"),
        "read_token": patch("ralph.selftest.read_token_from_keychain"),
        "runtime_cls": patch(
            "ralph.selftest.DockerSandboxRuntime"),
    }


class TestSelftestTokenCheck:
    """Verify that selftest checks tokens in the correct mode."""

    def test_oauth_mode_reads_token_with_oauth(self, capsys):
        patches = _patch_selftest_deps()
        started = {k: p.start() for k, p in patches.items()}
        try:
            started["read_token"].return_value = None
            rc = selftest("claude", "/fake/dotfiles", auth_mode="oauth")
            assert rc == 1
            started["read_token"].assert_called_once_with("claude", "oauth")
            out = capsys.readouterr().out
            assert "oauth credentials stored for agent claude" in out
            assert "store-token --auth oauth" in out
        finally:
            for p in patches.values():
                p.stop()

    def test_api_key_mode_reads_token_with_api_key(self, capsys):
        patches = _patch_selftest_deps()
        started = {k: p.start() for k, p in patches.items()}
        try:
            started["read_token"].return_value = None
            rc = selftest("claude", "/fake/dotfiles", auth_mode="api_key")
            assert rc == 1
            started["read_token"].assert_called_once_with("claude", "api_key")
            out = capsys.readouterr().out
            assert "api-key credentials stored for agent claude" in out
            assert "store-token --auth api-key" in out
        finally:
            for p in patches.values():
                p.stop()

    def test_default_resolves_to_oauth(self, capsys):
        patches = _patch_selftest_deps()
        started = {k: p.start() for k, p in patches.items()}
        try:
            started["read_token"].return_value = None
            rc = selftest("claude", "/fake/dotfiles", auth_mode=None)
            assert rc == 1
            # Should resolve to "oauth" (claude's default_auth_mode)
            started["read_token"].assert_called_once_with("claude", "oauth")
        finally:
            for p in patches.values():
                p.stop()

    def test_api_key_mode_shows_api_key_stored(self, capsys):
        patches = _patch_selftest_deps()
        started = {k: p.start() for k, p in patches.items()}
        try:
            far_future = int(time.time() * 1000) + 10 * 365 * MS_PER_DAY
            started["read_token"].return_value = {
                "accessToken": "sk-ant-test",
                "expiresAt": far_future,
            }
            # Will fail at prerequisites check, but token check should pass
            runtime_mock = started["runtime_cls"].return_value
            runtime_mock.check_prerequisites.return_value = ["docker not found"]
            rc = selftest("claude", "/fake/dotfiles", auth_mode="api_key")
            assert rc == 1  # fails at prereqs
            out = capsys.readouterr().out
            assert "PASS: check token" in out
            assert "API key stored" in out
            # Should NOT say "expires in N days" for api_key
            assert "expires in" not in out
        finally:
            for p in patches.values():
                p.stop()

    def test_oauth_mode_shows_expiry_days(self, capsys):
        patches = _patch_selftest_deps()
        started = {k: p.start() for k, p in patches.items()}
        try:
            future = int(time.time() * 1000) + 30 * MS_PER_DAY
            started["read_token"].return_value = {
                "accessToken": "sk-ant-oat01-test",
                "expiresAt": future,
            }
            runtime_mock = started["runtime_cls"].return_value
            runtime_mock.check_prerequisites.return_value = ["docker not found"]
            rc = selftest("claude", "/fake/dotfiles", auth_mode="oauth")
            assert rc == 1  # fails at prereqs
            out = capsys.readouterr().out
            assert "PASS: check token" in out
            assert "expires in" in out
            assert "API key stored" not in out
        finally:
            for p in patches.values():
                p.stop()

    def test_expired_token_shows_mode_hint(self, capsys):
        patches = _patch_selftest_deps()
        started = {k: p.start() for k, p in patches.items()}
        try:
            expired = int(time.time() * 1000) - MS_PER_DAY
            started["read_token"].return_value = {
                "accessToken": "sk-ant-test",
                "expiresAt": expired,
            }
            rc = selftest("claude", "/fake/dotfiles", auth_mode="api_key")
            assert rc == 1
            out = capsys.readouterr().out
            assert "FAIL: check token" in out
            assert "api-key credentials expired for agent claude" in out
            assert "store-token --auth api-key" in out
        finally:
            for p in patches.values():
                p.stop()


class TestSelftestPassesAuthModeToProxy:
    """Verify auth_mode is threaded through to ensure_proxy."""

    def test_ensure_proxy_called_with_auth_mode(self, capsys):
        patches = _patch_selftest_deps()
        started = {k: p.start() for k, p in patches.items()}
        try:
            far_future = int(time.time() * 1000) + 10 * 365 * MS_PER_DAY
            started["read_token"].return_value = {
                "accessToken": "sk-ant-test",
                "expiresAt": far_future,
            }
            runtime_mock = started["runtime_cls"].return_value
            runtime_mock.check_prerequisites.return_value = []
            # Make proxy health check succeed after ensure_proxy
            started["proxy_health_check"].return_value = (True, "abc123", "api_key")

            # Will fail at _selftest_docker (image build), but proxy call
            # should have happened
            runtime_mock.ensure_image.side_effect = Exception("no docker")
            runtime_mock.remove_sandbox.return_value = None

            rc = selftest("claude", "/fake/dotfiles", auth_mode="api_key")
            started["ensure_proxy"].assert_called_once_with(
                "claude", 18080, "/fake/dotfiles", "api_key")
        finally:
            for p in patches.values():
                p.stop()
