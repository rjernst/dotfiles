"""Tests for ralph.cli — CLI argument parsing and routing."""

from unittest.mock import MagicMock, patch

import pytest

from ralph.cli import main


# ---------------------------------------------------------------------------
# main() — token subcommand routing and usage
# ---------------------------------------------------------------------------

class TestMainTokenSubcommands:
    @patch("ralph.cli.sys.argv", ["ralph", "store-token", "--help"])
    def test_store_token_help_shows_usage(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "store-token" in captured.out
        assert "check-token" in captured.out
        assert "get-token" in captured.out

    @patch("ralph.cli.sys.argv", ["ralph", "check-token", "--help"])
    def test_check_token_help_shows_usage(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "store-token" in captured.out
        assert "--agent" in captured.out

    @patch("ralph.cli.check_token")
    @patch("ralph.cli.sys.argv", ["ralph", "--agent", "codex", "check-token"])
    def test_agent_before_subcommand(self, mock_check):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_check.assert_called_once_with("codex")

    @patch("ralph.cli.check_token")
    @patch("ralph.cli.sys.argv", ["ralph", "check-token", "--agent", "codex"])
    def test_agent_after_subcommand(self, mock_check):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_check.assert_called_once_with("codex")

    @patch("ralph.cli.sys.argv", ["ralph", "get-token", "--help"])
    def test_get_token_help_shows_usage(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "get-token" in captured.out

    @patch("ralph.cli.sys.argv", ["ralph", "store-token", "--badopt"])
    def test_unknown_option_exits_2(self):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    @patch("ralph.cli.sys.argv", ["ralph", "check-token", "--agent"])
    def test_agent_missing_value_exits_2(self):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# main() — sandbox integration
# ---------------------------------------------------------------------------

class TestMainSandboxFlags:
    @patch("ralph.cli.sys.argv", ["ralph", "--issue", "42", "--agent", "cursor"])
    @patch("ralph.cli.process_issue", return_value=0)
    @patch("ralph.cli.ensure_token", return_value="sk-test")
    @patch("ralph.cli.Git")
    @patch("ralph.cli.check_dependencies_prereq")
    def test_agent_flag_passed_through(self, mock_prereq,
                                       mock_git_cls,
                                       mock_token, mock_process):
        mock_git_cls.return_value = MagicMock(
            output=MagicMock(return_value="user"))
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

        # Verify ensure_token called with agent "cursor"
        mock_token.assert_called_once_with("cursor")

        # Verify process_issue called with agent "cursor", proxy_port=None, token, and rebuild
        call_args = mock_process.call_args[0]
        call_kwargs = mock_process.call_args[1]
        assert call_args[4] == "cursor"  # agent parameter
        assert call_args[9] is None  # proxy_port is None for non-proxy agents
        assert call_args[10] == "sk-test"  # token passed through
        assert call_kwargs.get("rebuild") is False

    @patch("ralph.cli.sys.argv", ["ralph", "--issue", "42", "--rebuild"])
    @patch("ralph.cli.process_issue", return_value=0)
    @patch("ralph.cli.ensure_token", return_value="sk-test")
    @patch("ralph.cli.ensure_proxy", return_value=18080)
    @patch("ralph.cli.Git")
    @patch("ralph.cli.check_dependencies_prereq")
    def test_rebuild_forces_image_rebuild(self, mock_prereq,
                                          mock_git_cls, mock_proxy,
                                          mock_token, mock_process):
        mock_git_cls.return_value = MagicMock(
            output=MagicMock(return_value="user"))
        with pytest.raises(SystemExit):
            main()
        # Rebuild is now deferred — passed through process_issue to ensure_sandbox
        call_kwargs = mock_process.call_args[1]
        assert call_kwargs.get("rebuild") is True

    @patch("ralph.cli.sys.argv", ["ralph", "--issue", "42"])
    @patch("ralph.cli.process_issue", return_value=0)
    @patch("ralph.cli.ensure_token", return_value="sk-test")
    @patch("ralph.cli.ensure_proxy", return_value=18080)
    @patch("ralph.cli.Git")
    @patch("ralph.cli.check_dependencies_prereq")
    def test_starts_proxy_before_processing(self, mock_prereq,
                                            mock_git_cls, mock_proxy,
                                            mock_token, mock_process):
        mock_git_cls.return_value = MagicMock(
            output=MagicMock(return_value="user"))
        with pytest.raises(SystemExit):
            main()
        mock_proxy.assert_called_once()

    @patch("ralph.cli.sys.argv", ["ralph", "--issue", "42"])
    @patch("ralph.cli.process_issue", return_value=0)
    @patch("ralph.cli.Git")
    @patch("ralph.cli.check_dependencies_prereq")
    def test_ensure_token_called_before_proxy(self, mock_prereq,
                                              mock_git_cls, mock_process):
        mock_git_cls.return_value = MagicMock(
            output=MagicMock(return_value="user"))
        call_order = []
        with patch("ralph.cli.ensure_token") as mock_token, \
             patch("ralph.cli.ensure_proxy", return_value=18080) as mock_proxy:
            mock_token.side_effect = lambda a: call_order.append("token")
            mock_proxy.side_effect = lambda a, p, d: (
                call_order.append("proxy"), p)[-1]
            with pytest.raises(SystemExit):
                main()
        assert call_order == ["token", "proxy"]

    @patch("ralph.cli.sys.argv", ["ralph", "--packages", "foo"])
    def test_packages_flag_rejected(self):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    @patch("ralph.cli.sys.argv", ["ralph", "--issue", "42", "--agent", "cursor"])
    @patch("ralph.cli.process_issue", return_value=0)
    @patch("ralph.cli.ensure_token", return_value="sk-test")
    @patch("ralph.cli.ensure_proxy")
    @patch("ralph.cli.start_proxy_keepalive")
    @patch("ralph.cli.Git")
    @patch("ralph.cli.check_dependencies_prereq")
    def test_cursor_skips_proxy(self, mock_prereq, mock_git_cls,
                                mock_keepalive, mock_proxy,
                                mock_token, mock_process):
        mock_git_cls.return_value = MagicMock(
            output=MagicMock(return_value="user"))
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

        # Proxy should not be started for cursor
        mock_proxy.assert_not_called()
        mock_keepalive.assert_not_called()

        # process_issue gets proxy_port=None, token="sk-test"
        call_args = mock_process.call_args[0]
        assert call_args[9] is None
        assert call_args[10] == "sk-test"

    @patch("ralph.cli.sys.argv", ["ralph", "--issue", "42", "--agent", "claude"])
    @patch("ralph.cli.process_issue", return_value=0)
    @patch("ralph.cli.ensure_token", return_value="sk-test")
    @patch("ralph.cli.ensure_proxy")
    @patch("ralph.cli.start_proxy_keepalive")
    @patch("ralph.cli.proxy_port_for_agent", return_value=18080)
    @patch("ralph.cli.Git")
    @patch("ralph.cli.check_dependencies_prereq")
    def test_claude_starts_proxy(self, mock_prereq, mock_git_cls,
                                 mock_port, mock_keepalive, mock_proxy,
                                 mock_token, mock_process):
        mock_git_cls.return_value = MagicMock(
            output=MagicMock(return_value="user"))
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

        # Proxy should be started for claude
        mock_port.assert_called_once_with("claude")
        mock_proxy.assert_called_once()
        mock_keepalive.assert_called_once_with(18080)

        # process_issue gets proxy_port=18080, token="sk-test"
        call_args = mock_process.call_args[0]
        assert call_args[9] == 18080
        assert call_args[10] == "sk-test"


# ---------------------------------------------------------------------------
# main() — default model handling
# ---------------------------------------------------------------------------

class TestMainDefaultModel:
    @patch("ralph.cli.sys.argv", ["ralph", "--issue", "42"])
    @patch("ralph.cli.process_issue", return_value=0)
    @patch("ralph.cli.ensure_token", return_value="sk-test")
    @patch("ralph.cli.ensure_proxy", return_value=18080)
    @patch("ralph.cli.Git")
    @patch("ralph.cli.check_dependencies_prereq")
    def test_claude_defaults_to_sonnet(self, mock_prereq, mock_git_cls,
                                       mock_proxy, mock_token, mock_process):
        mock_git_cls.return_value = MagicMock(
            output=MagicMock(return_value="user"))
        with pytest.raises(SystemExit):
            main()
        # model is positional arg 5 (index 5) in process_issue call
        call_args = mock_process.call_args[0]
        assert call_args[6] == "sonnet"  # model parameter

    @patch("ralph.cli.sys.argv", ["ralph", "--issue", "42", "--agent", "cursor"])
    @patch("ralph.cli.process_issue", return_value=0)
    @patch("ralph.cli.ensure_token", return_value="sk-test")
    @patch("ralph.cli.Git")
    @patch("ralph.cli.check_dependencies_prereq")
    def test_cursor_defaults_to_auto(self, mock_prereq, mock_git_cls,
                                      mock_token, mock_process):
        mock_git_cls.return_value = MagicMock(
            output=MagicMock(return_value="user"))
        with pytest.raises(SystemExit):
            main()
        call_args = mock_process.call_args[0]
        assert call_args[6] == "auto"  # model parameter

    @patch("ralph.cli.sys.argv",
           ["ralph", "--issue", "42", "--agent", "cursor", "--model", "gpt-5"])
    @patch("ralph.cli.process_issue", return_value=0)
    @patch("ralph.cli.ensure_token", return_value="sk-test")
    @patch("ralph.cli.Git")
    @patch("ralph.cli.check_dependencies_prereq")
    def test_explicit_model_overrides_agent_default(self, mock_prereq,
                                                     mock_git_cls,
                                                     mock_token,
                                                     mock_process):
        mock_git_cls.return_value = MagicMock(
            output=MagicMock(return_value="user"))
        with pytest.raises(SystemExit):
            main()
        call_args = mock_process.call_args[0]
        assert call_args[6] == "gpt-5"  # model parameter


# ---------------------------------------------------------------------------
# main() — selftest routing
# ---------------------------------------------------------------------------

class TestMainSelftestRouting:
    @patch("ralph.cli.selftest", return_value=0)
    @patch("ralph.cli.check_dependencies_prereq")
    @patch("ralph.cli.sys.argv", ["ralph", "selftest"])
    def test_main_routes_selftest(self, mock_prereq, mock_selftest):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_selftest.assert_called_once_with(
            "claude", mock_selftest.call_args[0][1], runtime_type="docker-sandbox")

    @patch("ralph.cli.selftest", return_value=0)
    @patch("ralph.cli.check_dependencies_prereq")
    @patch("ralph.cli.sys.argv", ["ralph", "selftest", "--agent", "codex"])
    def test_main_routes_selftest_with_agent(self, mock_prereq, mock_selftest):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_selftest.assert_called_once_with(
            "codex", mock_selftest.call_args[0][1], runtime_type="docker-sandbox")

    @patch("ralph.cli.sys.argv", ["ralph", "selftest", "--badopt"])
    def test_main_selftest_rejects_unknown_option(self):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    @patch("ralph.cli.selftest", return_value=0)
    @patch("ralph.cli.check_dependencies_prereq")
    @patch("ralph.cli.sys.argv", ["ralph", "selftest", "--runtime", "tart"])
    def test_main_routes_selftest_with_runtime(self, mock_prereq, mock_selftest):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_selftest.assert_called_once_with(
            "claude", mock_selftest.call_args[0][1], runtime_type="tart")

    @patch("ralph.cli.selftest", return_value=0)
    @patch("ralph.cli.check_dependencies_prereq")
    @patch("ralph.cli.sys.argv",
           ["ralph", "selftest", "--runtime", "tart", "--agent", "codex"])
    def test_main_routes_selftest_runtime_and_agent(self, mock_prereq,
                                                     mock_selftest):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_selftest.assert_called_once_with(
            "codex", mock_selftest.call_args[0][1], runtime_type="tart")

    @patch("ralph.cli.selftest", return_value=0)
    @patch("ralph.cli.check_dependencies_prereq")
    @patch("ralph.cli.sys.argv",
           ["ralph", "selftest", "--runtime", "docker-container"])
    def test_main_routes_selftest_docker_container(self, mock_prereq,
                                                     mock_selftest):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_selftest.assert_called_once_with(
            "claude", mock_selftest.call_args[0][1],
            runtime_type="docker-container")

    @patch("ralph.cli.sys.argv", ["ralph", "selftest", "--runtime", "podman"])
    def test_main_selftest_rejects_unknown_runtime(self):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# prune-sandboxes --runtime
# ---------------------------------------------------------------------------

class TestPruneSandboxesRuntime:
    """Tests for --runtime flag on prune-sandboxes subcommand."""

    @patch("ralph.cli.create_runtime")
    @patch("ralph.cli.sys.argv", ["ralph", "prune-sandboxes"])
    def test_default_uses_docker_sandbox(self, mock_create, capsys):
        mock_runtime = MagicMock()
        mock_runtime.prune_sandboxes.return_value = []
        mock_create.return_value = mock_runtime
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_create.assert_called_once_with("docker-sandbox", mock_create.call_args[0][1])
        mock_runtime.prune_sandboxes.assert_called_once_with("claude", max_age_days=None)

    @patch("ralph.cli.create_runtime")
    @patch("ralph.cli.sys.argv",
           ["ralph", "prune-sandboxes", "--runtime", "tart"])
    def test_runtime_tart_uses_tart_runtime(self, mock_create):
        mock_runtime = MagicMock()
        mock_runtime.prune_sandboxes.return_value = ["vm1"]
        mock_create.return_value = mock_runtime
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_create.assert_called_once_with("tart", mock_create.call_args[0][1])
        mock_runtime.prune_sandboxes.assert_called_once_with("claude", max_age_days=None)

    @patch("ralph.cli.create_runtime")
    @patch("ralph.cli.sys.argv",
           ["ralph", "prune-sandboxes", "--runtime", "tart", "--agent", "codex"])
    def test_runtime_tart_with_agent(self, mock_create, capsys):
        mock_runtime = MagicMock()
        mock_runtime.prune_sandboxes.return_value = []
        mock_create.return_value = mock_runtime
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_runtime.prune_sandboxes.assert_called_once_with("codex", max_age_days=None)

    @patch("ralph.cli.create_runtime")
    @patch("ralph.cli.sys.argv",
           ["ralph", "prune-sandboxes", "--runtime", "docker-container"])
    def test_runtime_docker_container(self, mock_create):
        mock_runtime = MagicMock()
        mock_runtime.prune_sandboxes.return_value = []
        mock_create.return_value = mock_runtime
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_create.assert_called_once_with(
            "docker-container", mock_create.call_args[0][1])
        mock_runtime.prune_sandboxes.assert_called_once_with(
            "claude", max_age_days=None)

    @patch("ralph.cli.sys.argv",
           ["ralph", "prune-sandboxes", "--runtime", "podman"])
    def test_rejects_unknown_runtime(self):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2
