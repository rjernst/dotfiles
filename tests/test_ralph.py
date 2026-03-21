"""Unit tests for scripts/ralph pure functions and mockable helpers."""

import io
import json
import subprocess
import time
from unittest.mock import MagicMock, patch, call

import pytest

from conftest import import_script

ralph = import_script("ralph")


# ---------------------------------------------------------------------------
# parse_duration
# ---------------------------------------------------------------------------

class TestParseDuration:
    def test_plain_number_treated_as_seconds(self):
        assert ralph.parse_duration("30") == 30

    def test_seconds_suffix(self):
        assert ralph.parse_duration("30s") == 30

    def test_minutes(self):
        assert ralph.parse_duration("5m") == 300

    def test_hours(self):
        assert ralph.parse_duration("2h") == 7200

    def test_days(self):
        assert ralph.parse_duration("1d") == 86400

    def test_invalid_input_raises_value_error(self):
        with pytest.raises(ValueError):
            ralph.parse_duration("abc")

    def test_invalid_suffix_raises_value_error(self):
        with pytest.raises(ValueError):
            ralph.parse_duration("5x")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            ralph.parse_duration("")


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_bracket_list_with_multiple_values(self):
        body = "---\ndepends: [11, 17]\nbranch: my-branch\n---\nSome content"
        assert ralph.parse_frontmatter(body, "depends") == "11 17"

    def test_bracket_list_with_single_value(self):
        body = "---\ndepends: [11]\n---\nSome content"
        assert ralph.parse_frontmatter(body, "depends") == "11"

    def test_scalar_value(self):
        body = "---\ndepends: 11\n---\nSome content"
        assert ralph.parse_frontmatter(body, "depends") == "11"

    def test_bracket_list_with_three_values(self):
        body = "---\ndepends: [11, 17, 18]\n---\nSome content"
        assert ralph.parse_frontmatter(body, "depends") == "11 17 18"

    def test_missing_field_returns_none(self):
        body = "---\nbranch: my-branch\n---\nSome content"
        assert ralph.parse_frontmatter(body, "depends") is None

    def test_no_frontmatter_returns_none(self):
        body = "Some content without frontmatter"
        assert ralph.parse_frontmatter(body, "depends") is None

    def test_extracts_branch_field(self):
        body = "---\nbranch: workon-skill\ndepends: [11, 17]\n---\nSome content"
        assert ralph.parse_frontmatter(body, "branch") == "workon-skill"

    def test_strips_whitespace_in_list_values(self):
        body = "---\ndepends: [ 11 , 17 ]\n---\nSome content"
        assert ralph.parse_frontmatter(body, "depends") == "11 17"

    def test_extracts_branch_from_frontmatter(self):
        body = "---\nbranch: fix-auth\n---\n# Spec"
        assert ralph.parse_frontmatter(body, "branch") == "fix-auth"

    def test_extracts_base_from_frontmatter(self):
        body = "---\nbranch: fix-auth\nbase: 8.x\n---\n# Spec"
        assert ralph.parse_frontmatter(body, "base") == "8.x"

    def test_no_frontmatter_returns_none_for_branch(self):
        body = "no frontmatter here"
        assert ralph.parse_frontmatter(body, "branch") is None

    def test_missing_field_returns_none_for_base(self):
        body = "---\nbranch: fix-auth\n---\n# Spec"
        assert ralph.parse_frontmatter(body, "base") is None

    def test_ignores_extra_fields(self):
        body = "---\nbranch: fix-auth\nbase: 8.x\nextra: ignored\n---\n# Spec"
        assert ralph.parse_frontmatter(body, "branch") == "fix-auth"

    def test_handles_whitespace_after_colon(self):
        body = "---\nbranch:   fix-auth\n---\n# Spec"
        assert ralph.parse_frontmatter(body, "branch") == "fix-auth"


# ---------------------------------------------------------------------------
# parse_issue_branch
# ---------------------------------------------------------------------------

class TestParseIssueBranch:
    def test_extracts_branch_from_title(self):
        assert ralph.parse_issue_branch("[my-branch] Some Title") == "my-branch"

    def test_handles_branches_with_slashes(self):
        assert ralph.parse_issue_branch("[feature/foo] Title") == "feature/foo"

    def test_handles_branches_with_numbers_and_hyphens(self):
        assert ralph.parse_issue_branch("[fix-123-bug] Title") == "fix-123-bug"

    def test_malformed_title_returns_none(self):
        assert ralph.parse_issue_branch("No brackets here") is None


# ---------------------------------------------------------------------------
# check_dependencies (mocked GitHub)
# ---------------------------------------------------------------------------

class TestCheckDependencies:
    def test_all_deps_done_returns_empty(self):
        gh = MagicMock()
        gh.issue_view_labels.return_value = ["spec", "status:done"]
        result = ralph.check_dependencies(["11", "17"], "owner/repo", gh)
        assert result == []

    def test_some_deps_not_done_returns_unmet(self):
        gh = MagicMock()
        def labels_side_effect(num, repo):
            if int(num) == 11:
                return ["spec", "status:done"]
            return ["spec", "status:in-progress"]
        gh.issue_view_labels.side_effect = labels_side_effect
        result = ralph.check_dependencies(["11", "17"], "owner/repo", gh)
        assert result == ["17"]

    def test_gh_failure_treats_dep_as_unmet(self):
        gh = MagicMock()
        gh.issue_view_labels.side_effect = subprocess.CalledProcessError(1, "gh")
        result = ralph.check_dependencies(["11"], "owner/repo", gh)
        assert "11" in result


# ---------------------------------------------------------------------------
# unblock_ready_specs (mocked GitHub)
# ---------------------------------------------------------------------------

class TestUnblockReadySpecs:
    def test_transitions_blocked_to_ready_when_deps_met(self):
        gh = MagicMock()
        gh.issue_list.return_value = [5]
        gh.issue_view_body.return_value = "---\ndepends: [11]\n---\nSome spec"
        gh.issue_view_labels.return_value = ["spec", "status:done"]

        ralph.unblock_ready_specs("owner/repo", gh)

        gh.issue_edit.assert_called_once_with(
            5, "owner/repo",
            remove_label="status:blocked",
            add_label="status:ready",
        )

    def test_leaves_blocked_when_deps_unmet(self):
        gh = MagicMock()
        gh.issue_list.return_value = [5]
        gh.issue_view_body.return_value = "---\ndepends: [11]\n---\nSome spec"
        gh.issue_view_labels.return_value = ["spec", "status:in-progress"]

        ralph.unblock_ready_specs("owner/repo", gh)

        gh.issue_edit.assert_not_called()

    def test_unblocks_spec_with_no_depends_field(self):
        gh = MagicMock()
        gh.issue_list.return_value = [8]
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSome spec with no depends"

        ralph.unblock_ready_specs("owner/repo", gh)

        gh.issue_edit.assert_called_once_with(
            8, "owner/repo",
            remove_label="status:blocked",
            add_label="status:ready",
        )


# ---------------------------------------------------------------------------
# keychain_service_name
# ---------------------------------------------------------------------------

class TestKeychainServiceName:
    def test_default_agent(self):
        assert ralph.keychain_service_name("claude") == "claude-token"

    def test_custom_agent(self):
        assert ralph.keychain_service_name("codex") == "codex-token"


# ---------------------------------------------------------------------------
# format_expiry_date
# ---------------------------------------------------------------------------

class TestFormatExpiryDate:
    def test_known_timestamp(self):
        # 2027-01-01T00:00:00Z = 1798761600000 ms
        assert ralph.format_expiry_date(1798761600000) == "2027-01-01"


# ---------------------------------------------------------------------------
# read_token_from_keychain (mocked subprocess)
# ---------------------------------------------------------------------------

class TestReadTokenFromKeychain:
    @patch("ralph.subprocess.run")
    def test_returns_parsed_json(self, mock_run):
        token_data = {"accessToken": "sk-test", "expiresAt": 9999999999999}
        mock_run.return_value = MagicMock(
            stdout=json.dumps(token_data) + "\n", returncode=0
        )
        result = ralph.read_token_from_keychain("claude")
        assert result == token_data
        mock_run.assert_called_once_with(
            ["security", "find-generic-password", "-s", "claude-token", "-a", "agent-loop", "-w"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
        )

    @patch("ralph.subprocess.run")
    def test_returns_none_when_not_found(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "security")
        result = ralph.read_token_from_keychain("claude")
        assert result is None

    @patch("ralph.subprocess.run")
    def test_returns_none_on_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(stdout="not-json\n", returncode=0)
        result = ralph.read_token_from_keychain("claude")
        assert result is None


# ---------------------------------------------------------------------------
# write_token_to_keychain (mocked subprocess)
# ---------------------------------------------------------------------------

class TestWriteTokenToKeychain:
    @patch("ralph.subprocess.run")
    def test_calls_security_with_correct_args(self, mock_run):
        ralph.write_token_to_keychain("claude", '{"accessToken":"t","expiresAt":1}')
        mock_run.assert_called_once_with(
            ["security", "add-generic-password",
             "-s", "claude-token", "-a", "agent-loop",
             "-w", '{"accessToken":"t","expiresAt":1}', "-U"],
            check=True,
        )

    @patch("ralph.subprocess.run")
    def test_uses_custom_agent_in_service_name(self, mock_run):
        ralph.write_token_to_keychain("codex", '{}')
        cmd = mock_run.call_args[0][0]
        assert "-s" in cmd
        s_idx = cmd.index("-s")
        assert cmd[s_idx + 1] == "codex-token"


# ---------------------------------------------------------------------------
# run_claude_setup_token (mocked subprocess)
# ---------------------------------------------------------------------------

class TestRunClaudeSetupToken:
    def _make_spawn(self, output_bytes, exit_status=0):
        """Create a fake pty.spawn that feeds output_bytes through master_read."""
        def fake_spawn(cmd, master_read):
            with patch("os.read", return_value=output_bytes):
                master_read(3)  # fd=3, arbitrary
            return exit_status
        return fake_spawn

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("pty.spawn")
    def test_extracts_token_from_pty_output(self, mock_spawn, mock_which):
        tui_data = b"\x1b[?2026hWelcome\n\xe2\x80\xa6\nsk-ant-oat01-realtoken123abc\nDone\x1b[?2026l"
        mock_spawn.side_effect = self._make_spawn(tui_data)
        result = ralph.run_claude_setup_token()
        assert result == "sk-ant-oat01-realtoken123abc"

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("pty.spawn")
    def test_exits_on_nonzero_return(self, mock_spawn, mock_which):
        mock_spawn.side_effect = self._make_spawn(b"", exit_status=256)
        with pytest.raises(SystemExit) as exc_info:
            ralph.run_claude_setup_token()
        assert exc_info.value.code == 1

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("pty.spawn")
    def test_exits_when_no_token_in_output(self, mock_spawn, mock_which):
        mock_spawn.side_effect = self._make_spawn(b"No token here\n")
        with pytest.raises(SystemExit) as exc_info:
            ralph.run_claude_setup_token()
        assert exc_info.value.code == 1

    @patch("shutil.which", return_value=None)
    def test_exits_when_claude_not_found(self, mock_which):
        with pytest.raises(SystemExit) as exc_info:
            ralph.run_claude_setup_token()
        assert exc_info.value.code == 1

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("pty.spawn")
    def test_extracts_token_from_noisy_output(self, mock_spawn, mock_which):
        """Ensure regex finds token buried in ANSI codes and TUI output."""
        noisy = (
            b"\x1b[?2026hWelcome to Claude Code v2.1.76\n"
            b"\xe2\x80\xa6\xe2\x80\xa6\xe2\x80\xa6\xe2\x80\xa6\n"
            b"\n"
            b"Your token: sk-ant-oat01-abc123XYZ_defGHI\n"
            b"\n"
            b"Set via: export CLAUDE_CODE_OAUTH_TOKEN=<token>\n"
            b"\x1b[?2026l\n"
        )
        mock_spawn.side_effect = self._make_spawn(noisy)
        result = ralph.run_claude_setup_token()
        assert result == "sk-ant-oat01-abc123XYZ_defGHI"

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("pty.spawn")
    def test_token_adjacent_to_ansi_escape(self, mock_spawn, mock_which):
        """Token immediately followed by ANSI escape code (no space)."""
        data = b"sk-ant-oat01-fulltoken123abc_XYZ\x1b[?2026l"
        mock_spawn.side_effect = self._make_spawn(data)
        result = ralph.run_claude_setup_token()
        assert result == "sk-ant-oat01-fulltoken123abc_XYZ"

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("pty.spawn")
    def test_takes_longest_match(self, mock_spawn, mock_which):
        """When token appears multiple times (e.g., partial + full), take longest."""
        data = b"sk-ant-oat01-short\nfull: sk-ant-oat01-short-and-longer-version\n"
        mock_spawn.side_effect = self._make_spawn(data)
        result = ralph.run_claude_setup_token()
        assert result == "sk-ant-oat01-short-and-longer-version"


# ---------------------------------------------------------------------------
# store_token (mocked stdin + keychain)
# ---------------------------------------------------------------------------

class TestStoreToken:
    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO("sk-ant-oat01-abc123"))
    def test_bare_string_wraps_in_json(self, mock_stdin, mock_time, mock_write):
        ralph.store_token("claude")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-ant-oat01-abc123"
        expected_expiry = 1700000000000 + 365 * 86400 * 1000
        assert data["expiresAt"] == expected_expiry

    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO(
        json.dumps({"accessToken": "sk-test", "expiresAt": 1800000000000})
    ))
    def test_json_input_preserved(self, mock_stdin, mock_time, mock_write):
        ralph.store_token("claude")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-test"
        assert data["expiresAt"] == 1800000000000

    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO(
        json.dumps({"accessToken": "sk-test"})
    ))
    def test_json_input_without_expiry_gets_default(self, mock_stdin, mock_time, mock_write):
        ralph.store_token("claude")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-test"
        expected_expiry = 1700000000000 + 365 * 86400 * 1000
        assert data["expiresAt"] == expected_expiry

    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO(""))
    def test_empty_stdin_exits_with_error(self, mock_stdin):
        with pytest.raises(SystemExit) as exc_info:
            ralph.store_token("claude")
        assert exc_info.value.code == 1

    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO("sk-token"))
    def test_uses_correct_agent(self, mock_stdin, mock_time, mock_write):
        ralph.store_token("codex")
        assert mock_write.call_args[0][0] == "codex"

    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.run_claude_setup_token", return_value="sk-from-setup")
    @patch("ralph.sys.stdin")
    def test_interactive_runs_claude_setup_token(self, mock_stdin, mock_setup, mock_time, mock_write):
        mock_stdin.isatty.return_value = True
        ralph.store_token("claude")
        mock_setup.assert_called_once()
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-from-setup"

    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO("sk-token"))
    def test_prints_confirmation(self, mock_stdin, mock_time, mock_write, capsys):
        ralph.store_token("claude")
        captured = capsys.readouterr()
        assert "ralph: token stored for agent claude" in captured.out
        assert "expires" in captured.out

    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO(
        json.dumps({"foo": "bar"})
    ))
    def test_json_without_access_token_warns(self, mock_stdin, mock_time, mock_write, capsys):
        ralph.store_token("claude")
        captured = capsys.readouterr()
        assert "input JSON missing accessToken" in captured.err
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        # Raw JSON string becomes the accessToken
        assert data["accessToken"] == json.dumps({"foo": "bar"})


# ---------------------------------------------------------------------------
# check_token (mocked keychain + time)
# ---------------------------------------------------------------------------

class TestCheckToken:
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_valid_token_exits_0(self, mock_time, mock_read):
        future_ms = 1700000000000 + 30 * 86400 * 1000  # 30 days from now
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        with pytest.raises(SystemExit) as exc_info:
            ralph.check_token("claude")
        assert exc_info.value.code == 0

    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_expired_token_exits_1(self, mock_time, mock_read):
        past_ms = 1700000000000 - 86400 * 1000  # 1 day ago
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": past_ms}
        with pytest.raises(SystemExit) as exc_info:
            ralph.check_token("claude")
        assert exc_info.value.code == 1

    @patch("ralph.read_token_from_keychain", return_value=None)
    def test_missing_token_exits_1(self, mock_read):
        with pytest.raises(SystemExit) as exc_info:
            ralph.check_token("claude")
        assert exc_info.value.code == 1

    @patch("ralph.read_token_from_keychain", return_value=None)
    def test_missing_token_suggests_store_token(self, mock_read, capsys):
        with pytest.raises(SystemExit):
            ralph.check_token("claude")
        captured = capsys.readouterr()
        assert "ralph store-token" in captured.err


# ---------------------------------------------------------------------------
# get_token (mocked keychain + time)
# ---------------------------------------------------------------------------

class TestGetToken:
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_prints_access_token(self, mock_time, mock_read, capsys):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-ant-oat01-secret", "expiresAt": future_ms}
        ralph.get_token("claude")
        captured = capsys.readouterr()
        assert captured.out == "sk-ant-oat01-secret"

    @patch("ralph.read_token_from_keychain", return_value=None)
    def test_missing_token_exits_1(self, mock_read):
        with pytest.raises(SystemExit) as exc_info:
            ralph.get_token("claude")
        assert exc_info.value.code == 1

    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_expired_token_exits_1(self, mock_time, mock_read):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": past_ms}
        with pytest.raises(SystemExit) as exc_info:
            ralph.get_token("claude")
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# ensure_token (mocked keychain + setup-token)
# ---------------------------------------------------------------------------

class TestEnsureToken:
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_returns_cached_valid_token(self, mock_time, mock_read):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-cached", "expiresAt": future_ms}
        result = ralph.ensure_token("claude")
        assert result == "sk-cached"

    @patch("ralph.write_token_to_keychain")
    @patch("ralph.run_claude_setup_token", return_value="sk-fresh")
    @patch("ralph.read_token_from_keychain", return_value=None)
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_runs_setup_when_missing(self, mock_time, mock_read, mock_setup, mock_write):
        result = ralph.ensure_token("claude")
        assert result == "sk-fresh"
        mock_setup.assert_called_once()

    @patch("ralph.write_token_to_keychain")
    @patch("ralph.run_claude_setup_token", return_value="sk-renewed")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_runs_setup_when_expired(self, mock_time, mock_read, mock_setup, mock_write):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-old", "expiresAt": past_ms}
        result = ralph.ensure_token("claude")
        assert result == "sk-renewed"
        mock_setup.assert_called_once()

    @patch("ralph.write_token_to_keychain")
    @patch("ralph.run_claude_setup_token", return_value="sk-fresh")
    @patch("ralph.read_token_from_keychain", return_value=None)
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_stores_token_after_setup(self, mock_time, mock_read, mock_setup, mock_write):
        ralph.ensure_token("claude")
        mock_write.assert_called_once()
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-fresh"

    @patch("ralph.write_token_to_keychain")
    @patch("ralph.run_claude_setup_token", return_value="sk-fresh")
    @patch("ralph.read_token_from_keychain", return_value=None)
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_missing_token_prints_status(self, mock_time, mock_read, mock_setup, mock_write, capsys):
        ralph.ensure_token("claude")
        captured = capsys.readouterr()
        assert "no token found" in captured.err
        assert "running claude setup-token" in captured.err

    @patch("ralph.write_token_to_keychain")
    @patch("ralph.run_claude_setup_token", return_value="sk-renewed")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_expired_token_prints_status(self, mock_time, mock_read, mock_setup, mock_write, capsys):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-old", "expiresAt": past_ms}
        ralph.ensure_token("claude")
        captured = capsys.readouterr()
        assert "token expired" in captured.err
        assert "running claude setup-token" in captured.err

    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_valid_token_does_not_run_setup(self, mock_time, mock_read):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-cached", "expiresAt": future_ms}
        with patch("ralph.run_claude_setup_token") as mock_setup:
            ralph.ensure_token("claude")
            mock_setup.assert_not_called()


# ---------------------------------------------------------------------------
# Token JSON round-trip
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# main() — token subcommand routing and usage
# ---------------------------------------------------------------------------

class TestMainTokenSubcommands:
    @patch("ralph.sys.argv", ["ralph", "store-token", "--help"])
    def test_store_token_help_shows_usage(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "store-token" in captured.out
        assert "check-token" in captured.out
        assert "get-token" in captured.out

    @patch("ralph.sys.argv", ["ralph", "check-token", "--help"])
    def test_check_token_help_shows_usage(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "store-token" in captured.out
        assert "--agent" in captured.out

    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.argv", ["ralph", "--agent", "codex", "check-token"])
    def test_agent_before_subcommand(self, mock_time, mock_read):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 0
        mock_read.assert_called_once_with("codex")

    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.argv", ["ralph", "check-token", "--agent", "codex"])
    def test_agent_after_subcommand(self, mock_time, mock_read):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 0
        mock_read.assert_called_once_with("codex")

    @patch("ralph.sys.argv", ["ralph", "get-token", "--help"])
    def test_get_token_help_shows_usage(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "get-token" in captured.out

    @patch("ralph.sys.argv", ["ralph", "store-token", "--badopt"])
    def test_unknown_option_exits_2(self):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 2

    @patch("ralph.sys.argv", ["ralph", "check-token", "--agent"])
    def test_agent_missing_value_exits_2(self):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 2


class TestTokenRoundTrip:
    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO("sk-round-trip-token"))
    def test_store_then_read_round_trip(self, mock_stdin, mock_time, mock_write):
        """Verify the JSON written by store_token can be parsed back correctly."""
        ralph.store_token("claude")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-round-trip-token"
        assert isinstance(data["expiresAt"], int)
        assert data["expiresAt"] > 1700000000000
