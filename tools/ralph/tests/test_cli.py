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
    @patch("ralph.cli.sys.argv", ["ralph", "--issue", "42", "--agent", "codex"])
    @patch("ralph.cli.process_issue", return_value=0)
    @patch("ralph.cli.ensure_token", return_value="sk-test")
    @patch("ralph.cli.ensure_proxy", return_value=18080)
    @patch("ralph.cli.Git")
    @patch("ralph.cli.check_dependencies_prereq")
    def test_agent_flag_passed_through(self, mock_prereq,
                                       mock_git_cls, mock_proxy,
                                       mock_token, mock_process):
        mock_git_cls.return_value = MagicMock(
            output=MagicMock(return_value="user"))
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

        # Verify ensure_token called with agent "codex"
        mock_token.assert_called_once_with("codex")

        # Verify process_issue called with agent "codex", proxy_port, and rebuild
        call_args = mock_process.call_args[0]
        call_kwargs = mock_process.call_args[1]
        assert call_args[4] == "codex"  # agent parameter
        assert call_args[9] == 18080  # proxy_port (not an oauth token string)
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
            "claude", mock_selftest.call_args[0][1], sandbox_type="docker")

    @patch("ralph.cli.selftest", return_value=0)
    @patch("ralph.cli.check_dependencies_prereq")
    @patch("ralph.cli.sys.argv", ["ralph", "selftest", "--agent", "codex"])
    def test_main_routes_selftest_with_agent(self, mock_prereq, mock_selftest):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_selftest.assert_called_once_with(
            "codex", mock_selftest.call_args[0][1], sandbox_type="docker")

    @patch("ralph.cli.sys.argv", ["ralph", "selftest", "--badopt"])
    def test_main_selftest_rejects_unknown_option(self):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    @patch("ralph.cli.selftest", return_value=0)
    @patch("ralph.cli.check_dependencies_prereq")
    @patch("ralph.cli.sys.argv", ["ralph", "selftest", "--type", "tart"])
    def test_main_routes_selftest_with_type(self, mock_prereq, mock_selftest):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_selftest.assert_called_once_with(
            "claude", mock_selftest.call_args[0][1], sandbox_type="tart")

    @patch("ralph.cli.selftest", return_value=0)
    @patch("ralph.cli.check_dependencies_prereq")
    @patch("ralph.cli.sys.argv",
           ["ralph", "selftest", "--type", "tart", "--agent", "codex"])
    def test_main_routes_selftest_type_and_agent(self, mock_prereq,
                                                  mock_selftest):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_selftest.assert_called_once_with(
            "codex", mock_selftest.call_args[0][1], sandbox_type="tart")

    @patch("ralph.cli.sys.argv", ["ralph", "selftest", "--type", "podman"])
    def test_main_selftest_rejects_unknown_type(self):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# prune-sandboxes --type
# ---------------------------------------------------------------------------

class TestPruneSandboxesType:
    """Tests for --type flag on prune-sandboxes subcommand."""

    @patch("ralph.cli.DockerSandbox.prune_sandboxes", return_value=[])
    @patch("ralph.cli.sys.argv", ["ralph", "prune-sandboxes"])
    def test_default_uses_docker(self, mock_prune, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_prune.assert_called_once_with("claude")

    @patch("ralph.cli.TartSandbox.prune_sandboxes", return_value=["vm1"])
    @patch("ralph.cli.sys.argv",
           ["ralph", "prune-sandboxes", "--type", "tart"])
    def test_type_tart_uses_tart_sandbox(self, mock_prune):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_prune.assert_called_once_with("claude")

    @patch("ralph.cli.TartSandbox.prune_sandboxes", return_value=[])
    @patch("ralph.cli.sys.argv",
           ["ralph", "prune-sandboxes", "--type", "tart", "--agent", "codex"])
    def test_type_tart_with_agent(self, mock_prune, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_prune.assert_called_once_with("codex")

    @patch("ralph.cli.sys.argv",
           ["ralph", "prune-sandboxes", "--type", "podman"])
    def test_rejects_unknown_type(self):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2
