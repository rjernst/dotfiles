"""Unit tests for scripts/ralph pure functions and mockable helpers."""

import io
import json
import re
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

class TestExtractTokenFromOutput:
    """Test the token extraction regex logic used by run_claude_setup_token."""

    def _extract(self, raw_bytes):
        """Simulate the extraction logic from run_claude_setup_token."""
        full_output = raw_bytes.decode("utf-8", errors="replace")
        matches = re.findall(r'sk-ant-oat01-[A-Za-z0-9_-]+', full_output)
        if not matches:
            return None
        return max(matches, key=len)

    def test_extracts_token_from_clean_output(self):
        data = b"Your token: sk-ant-oat01-realtoken123abc\nDone.\n"
        assert self._extract(data) == "sk-ant-oat01-realtoken123abc"

    def test_extracts_from_noisy_tui_output(self):
        noisy = (
            b"\x1b[?2026hWelcome to Claude Code v2.1.76\n"
            b"\xe2\x80\xa6\xe2\x80\xa6\xe2\x80\xa6\xe2\x80\xa6\n"
            b"\n"
            b"Your token: sk-ant-oat01-abc123XYZ_defGHI\n"
            b"\n"
            b"Set via: export CLAUDE_CODE_OAUTH_TOKEN=<token>\n"
            b"\x1b[?2026l\n"
        )
        assert self._extract(noisy) == "sk-ant-oat01-abc123XYZ_defGHI"

    def test_token_adjacent_to_ansi_escape(self):
        data = b"sk-ant-oat01-fulltoken123abc_XYZ\x1b[?2026l"
        assert self._extract(data) == "sk-ant-oat01-fulltoken123abc_XYZ"

    def test_takes_longest_match(self):
        data = b"sk-ant-oat01-short\nfull: sk-ant-oat01-short-and-longer-version\n"
        assert self._extract(data) == "sk-ant-oat01-short-and-longer-version"

    def test_handles_108_char_token(self):
        token = "sk-ant-oat01-" + "A" * 67 + "_" + "B" * 27  # 108 chars
        data = f"Your token: {token}\nDone.\n".encode()
        result = self._extract(data)
        assert result == token
        assert len(result) == 108

    def test_returns_none_when_no_token(self):
        assert self._extract(b"No token here\n") is None


class TestRunClaudeSetupToken:
    @patch("shutil.which", return_value=None)
    def test_exits_when_claude_not_found(self, mock_which):
        with pytest.raises(SystemExit) as exc_info:
            ralph.run_claude_setup_token()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# store_token (mocked stdin + keychain)
# ---------------------------------------------------------------------------

def _mock_validation_success():
    """Return a patch that makes token validation succeed."""
    return patch("ralph.subprocess.run",
                 return_value=MagicMock(returncode=0, stderr=""))


class TestStoreToken:
    @_mock_validation_success()
    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO("sk-ant-oat01-abc123"))
    def test_bare_string_wraps_in_json(self, mock_stdin, mock_time, mock_write, mock_validate):
        ralph.store_token("claude")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-ant-oat01-abc123"
        expected_expiry = 1700000000000 + 365 * 86400 * 1000
        assert data["expiresAt"] == expected_expiry

    @_mock_validation_success()
    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO(
        json.dumps({"accessToken": "sk-test", "expiresAt": 1800000000000})
    ))
    def test_json_input_preserved(self, mock_stdin, mock_time, mock_write, mock_validate):
        ralph.store_token("claude")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-test"
        assert data["expiresAt"] == 1800000000000

    @_mock_validation_success()
    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO(
        json.dumps({"accessToken": "sk-test"})
    ))
    def test_json_input_without_expiry_gets_default(self, mock_stdin, mock_time, mock_write, mock_validate):
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

    @_mock_validation_success()
    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO("sk-token"))
    def test_uses_correct_agent(self, mock_stdin, mock_time, mock_write, mock_validate):
        ralph.store_token("codex")
        assert mock_write.call_args[0][0] == "codex"

    @_mock_validation_success()
    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.run_claude_setup_token", return_value="sk-from-setup")
    @patch("ralph.sys.stdin")
    def test_interactive_runs_claude_setup_token(self, mock_stdin, mock_setup, mock_time, mock_write, mock_validate):
        mock_stdin.isatty.return_value = True
        ralph.store_token("claude")
        mock_setup.assert_called_once()
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-from-setup"

    @_mock_validation_success()
    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO("sk-token"))
    def test_prints_confirmation(self, mock_stdin, mock_time, mock_write, mock_validate, capsys):
        ralph.store_token("claude")
        captured = capsys.readouterr()
        assert "ralph: token stored for agent claude" in captured.out
        assert "expires" in captured.out

    @patch("ralph.subprocess.run",
           return_value=MagicMock(returncode=1, stderr="401 authentication_error"))
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO("sk-bad-token"))
    def test_invalid_token_rejected(self, mock_stdin, mock_time, mock_validate):
        with pytest.raises(SystemExit) as exc_info:
            ralph.store_token("claude")
        assert exc_info.value.code == 1

    @_mock_validation_success()
    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO(
        json.dumps({"foo": "bar"})
    ))
    def test_json_without_access_token_warns(self, mock_stdin, mock_time, mock_write, mock_validate, capsys):
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

    @_mock_validation_success()
    @patch("ralph.write_token_to_keychain")
    @patch("ralph.run_claude_setup_token", return_value="sk-fresh")
    @patch("ralph.read_token_from_keychain", return_value=None)
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_runs_setup_when_missing(self, mock_time, mock_read, mock_setup, mock_write, mock_validate):
        result = ralph.ensure_token("claude")
        assert result == "sk-fresh"
        mock_setup.assert_called_once()

    @_mock_validation_success()
    @patch("ralph.write_token_to_keychain")
    @patch("ralph.run_claude_setup_token", return_value="sk-renewed")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_runs_setup_when_expired(self, mock_time, mock_read, mock_setup, mock_write, mock_validate):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-old", "expiresAt": past_ms}
        result = ralph.ensure_token("claude")
        assert result == "sk-renewed"
        mock_setup.assert_called_once()

    @_mock_validation_success()
    @patch("ralph.write_token_to_keychain")
    @patch("ralph.run_claude_setup_token", return_value="sk-fresh")
    @patch("ralph.read_token_from_keychain", return_value=None)
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_stores_token_after_setup(self, mock_time, mock_read, mock_setup, mock_write, mock_validate):
        ralph.ensure_token("claude")
        mock_write.assert_called_once()
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-fresh"

    @_mock_validation_success()
    @patch("ralph.write_token_to_keychain")
    @patch("ralph.run_claude_setup_token", return_value="sk-fresh")
    @patch("ralph.read_token_from_keychain", return_value=None)
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_missing_token_prints_status(self, mock_time, mock_read, mock_setup, mock_write, mock_validate, capsys):
        ralph.ensure_token("claude")
        captured = capsys.readouterr()
        assert "no token found" in captured.err
        assert "running claude setup-token" in captured.err

    @_mock_validation_success()
    @patch("ralph.write_token_to_keychain")
    @patch("ralph.run_claude_setup_token", return_value="sk-renewed")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_expired_token_prints_status(self, mock_time, mock_read, mock_setup, mock_write, mock_validate, capsys):
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
    @_mock_validation_success()
    @patch("ralph.write_token_to_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    @patch("ralph.sys.stdin", new_callable=lambda: io.StringIO("sk-round-trip-token"))
    def test_store_then_read_round_trip(self, mock_stdin, mock_time, mock_write, mock_validate):
        """Verify the JSON written by store_token can be parsed back correctly."""
        ralph.store_token("claude")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-round-trip-token"
        assert isinstance(data["expiresAt"], int)
        assert data["expiresAt"] > 1700000000000


# ---------------------------------------------------------------------------
# Proxy lifecycle
# ---------------------------------------------------------------------------

class TestProxyContainerName:
    def test_default_agent(self):
        assert ralph.proxy_container_name("claude") == "agent-loop-proxy-claude"

    def test_custom_agent(self):
        assert ralph.proxy_container_name("codex") == "agent-loop-proxy-codex"


class TestProxyPortForAgent:
    def test_claude_default(self):
        assert ralph.proxy_port_for_agent("claude") == 18080

    def test_unknown_agent_uses_default(self):
        assert ralph.proxy_port_for_agent("unknown") == ralph.DEFAULT_PROXY_PORT


class TestProxyHealthCheck:
    @patch("ralph.urllib.request.urlopen")
    def test_returns_true_on_200(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value = mock_resp
        assert ralph.proxy_health_check(18080) is True

    @patch("ralph.urllib.request.urlopen")
    def test_returns_false_on_non_200(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_urlopen.return_value = mock_resp
        assert ralph.proxy_health_check(18080) is False

    @patch("ralph.urllib.request.urlopen", side_effect=Exception("connection refused"))
    def test_returns_false_on_connection_error(self, mock_urlopen):
        assert ralph.proxy_health_check(18080) is False


class TestStartProxy:
    @patch("ralph.compute_proxy_tag", return_value="agent-loop-proxy:vfake123")
    @patch("ralph.subprocess.Popen")
    @patch("ralph.subprocess.run")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_constructs_correct_docker_run_command(self, mock_time, mock_read, mock_run, mock_popen, mock_tag):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test-token", "expiresAt": future_ms}
        # Image inspect succeeds
        mock_run.return_value = MagicMock(returncode=0)
        # Popen mock with stdin
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        result = ralph.start_proxy("claude", 18080, "/fake/dotfiles")
        assert result == "agent-loop-proxy-claude"

        # Verify docker run call (via Popen, no -d flag)
        cmd = mock_popen.call_args[0][0]
        assert cmd[:3] == ["docker", "run", "-i"]
        assert "--rm" not in cmd
        assert "-d" not in cmd
        assert "--name" in cmd
        name_idx = cmd.index("--name")
        assert cmd[name_idx + 1] == "agent-loop-proxy-claude"
        assert "-p" in cmd
        p_idx = cmd.index("-p")
        assert cmd[p_idx + 1] == "18080:18080"
        assert cmd[-1].startswith("agent-loop-proxy:v")
        # Verify token piped via stdin
        mock_proc.stdin.write.assert_called_once_with(b"sk-test-token\n")
        mock_proc.stdin.close.assert_called_once()

    @patch("ralph.compute_proxy_tag", return_value="agent-loop-proxy:vfake123")
    @patch("ralph.subprocess.Popen")
    @patch("ralph.subprocess.run")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_builds_image_when_missing(self, mock_time, mock_read, mock_run, mock_popen, mock_tag):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_run.side_effect = [
            MagicMock(returncode=1),  # image inspect — not found
            MagicMock(returncode=0),  # docker build
        ]
        mock_popen.return_value = MagicMock()

        ralph.start_proxy("claude", 18080, "/fake/dotfiles")

        build_call = mock_run.call_args_list[1]
        cmd = build_call[0][0]
        assert cmd[0:2] == ["docker", "build"]
        assert any(c.startswith("agent-loop-proxy:v") for c in cmd)

    @patch("ralph.read_token_from_keychain", return_value=None)
    def test_exits_when_no_token(self, mock_read):
        with pytest.raises(SystemExit) as exc_info:
            ralph.start_proxy("claude", 18080, "/fake/dotfiles")
        assert exc_info.value.code == 1

    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_exits_when_token_expired(self, mock_time, mock_read):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-old", "expiresAt": past_ms}
        with pytest.raises(SystemExit) as exc_info:
            ralph.start_proxy("claude", 18080, "/fake/dotfiles")
        assert exc_info.value.code == 1

    @patch("ralph.compute_proxy_tag", return_value="agent-loop-proxy:vfake123")
    @patch("ralph.subprocess.Popen")
    @patch("ralph.subprocess.run")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_popen_failure_raises(self, mock_time, mock_read, mock_run, mock_popen, mock_tag):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_run.return_value = MagicMock(returncode=0)  # image inspect
        mock_popen.side_effect = OSError("docker not available")
        with pytest.raises(OSError):
            ralph.start_proxy("claude", 18080, "/fake/dotfiles")


class TestStopProxy:
    @patch("ralph.subprocess.run")
    def test_calls_docker_stop(self, mock_run):
        ralph.stop_proxy("claude")
        mock_run.assert_called_once_with(
            ["docker", "stop", "agent-loop-proxy-claude"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )

    @patch("ralph.subprocess.run")
    def test_uses_correct_agent_name(self, mock_run):
        ralph.stop_proxy("codex")
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "agent-loop-proxy-codex"


class TestEnsureProxy:
    @patch("ralph.compute_proxy_tag", return_value="agent-loop-proxy:vfake123")
    @patch("ralph.subprocess.run")
    @patch("ralph.proxy_health_check", return_value=True)
    def test_reuses_healthy_proxy(self, mock_health, mock_run, mock_tag):
        # docker inspect returns matching image tag
        mock_run.return_value = MagicMock(returncode=0, stdout="agent-loop-proxy:vfake123\n")
        result = ralph.ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert result == 18080
        mock_health.assert_called_once_with(18080)

    @patch("ralph.proxy_health_check", side_effect=[False] + [True])
    @patch("ralph.start_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.time.sleep")
    def test_starts_new_when_none_running(self, mock_sleep, mock_run, mock_start, mock_health):
        # docker inspect fails (no container exists)
        mock_run.return_value = MagicMock(returncode=1)

        result = ralph.ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert result == 18080
        mock_start.assert_called_once_with("claude", 18080, "/fake/dotfiles")

    @patch("ralph.proxy_health_check", side_effect=[False] + [True])
    @patch("ralph.start_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.time.sleep")
    def test_stops_stale_container_before_starting(self, mock_sleep, mock_run, mock_start, mock_health):
        # docker inspect succeeds (stale container exists)
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="true\n"),  # inspect
            MagicMock(returncode=0),  # docker logs
            MagicMock(returncode=0),  # stop
            MagicMock(returncode=0),  # rm
        ]

        ralph.ensure_proxy("claude", 18080, "/fake/dotfiles")

        # Verify stop and rm were called
        stop_call = mock_run.call_args_list[2]
        assert stop_call[0][0] == ["docker", "stop", "agent-loop-proxy-claude"]
        rm_call = mock_run.call_args_list[3]
        assert rm_call[0][0] == ["docker", "rm", "agent-loop-proxy-claude"]
        mock_start.assert_called_once()

    @patch("ralph.stop_proxy")
    @patch("ralph.proxy_health_check", return_value=False)
    @patch("ralph.start_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.time.sleep")
    def test_exits_when_proxy_fails_to_become_healthy(self, mock_sleep, mock_run, mock_start, mock_health, mock_stop):
        mock_run.return_value = MagicMock(returncode=1)  # no stale container

        with pytest.raises(SystemExit) as exc_info:
            ralph.ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert exc_info.value.code == 1
        mock_stop.assert_called_once_with("claude")



# ---------------------------------------------------------------------------
# Sandbox.parse_base_image
# ---------------------------------------------------------------------------

class TestSandboxParseBaseImage:
    def test_extracts_from_line(self):
        content = "FROM docker/sandbox-templates:claude-code\nUSER root"
        assert ralph.Sandbox.parse_base_image(content) == "docker/sandbox-templates:claude-code"

    def test_returns_none_when_no_from(self):
        assert ralph.Sandbox.parse_base_image("RUN echo hi") is None

    def test_ignores_comment_lines(self):
        content = "# FROM fake:image\nFROM real:latest"
        assert ralph.Sandbox.parse_base_image(content) == "real:latest"

    def test_returns_final_stage_in_multistage(self):
        content = "FROM builder:latest AS build\nRUN make\nFROM runtime:slim"
        assert ralph.Sandbox.parse_base_image(content) == "runtime:slim"


# ---------------------------------------------------------------------------
# Sandbox.content_hash
# ---------------------------------------------------------------------------

class TestSandboxContentHash:
    def test_deterministic(self):
        h1 = ralph.Sandbox.content_hash("FROM a", "digest1")
        h2 = ralph.Sandbox.content_hash("FROM a", "digest1")
        assert h1 == h2

    def test_changes_when_dockerfile_changes(self):
        h1 = ralph.Sandbox.content_hash("FROM a\nRUN echo old", "digest1")
        h2 = ralph.Sandbox.content_hash("FROM a\nRUN echo new", "digest1")
        assert h1 != h2

    def test_changes_when_base_digest_changes(self):
        df = "FROM a\nRUN echo same"
        h1 = ralph.Sandbox.content_hash(df, "sha256:aaa")
        h2 = ralph.Sandbox.content_hash(df, "sha256:bbb")
        assert h1 != h2

    def test_length_is_8(self):
        h = ralph.Sandbox.content_hash("FROM a", "d")
        assert len(h) == 8


# ---------------------------------------------------------------------------
# Sandbox.image_tag
# ---------------------------------------------------------------------------

class TestSandboxImageTag:
    def test_format(self):
        tag = ralph.Sandbox.image_tag("claude", "abc123de")
        assert tag == "agent-loop-sandbox-claude:vabc123de"

    def test_custom_agent(self):
        tag = ralph.Sandbox.image_tag("codex", "xyz")
        assert tag == "agent-loop-sandbox-codex:vxyz"


# ---------------------------------------------------------------------------
# Sandbox.parse_dependencies
# ---------------------------------------------------------------------------

class TestSandboxParseDependencies:
    def test_basic_package_list(self):
        content = "openjdk-21-jdk\npython3-venv\nnodejs"
        assert ralph.Sandbox.parse_dependencies(content) == [
            "openjdk-21-jdk", "python3-venv", "nodejs"
        ]

    def test_comment_only_lines_skipped(self):
        content = "# This is a comment\npkg1\n# Another comment\npkg2"
        assert ralph.Sandbox.parse_dependencies(content) == ["pkg1", "pkg2"]

    def test_inline_comments_stripped(self):
        content = "pkg1 # this is a comment\npkg2 # another"
        assert ralph.Sandbox.parse_dependencies(content) == ["pkg1", "pkg2"]

    def test_blank_lines_skipped(self):
        content = "pkg1\n\n\npkg2\n\npkg3"
        assert ralph.Sandbox.parse_dependencies(content) == ["pkg1", "pkg2", "pkg3"]

    def test_whitespace_handling(self):
        content = "  pkg1  \n\tpkg2\t\n  pkg3  # comment  "
        assert ralph.Sandbox.parse_dependencies(content) == ["pkg1", "pkg2", "pkg3"]

    def test_empty_content(self):
        assert ralph.Sandbox.parse_dependencies("") == []

    def test_only_comments_and_blanks(self):
        content = "# comment\n\n# another\n  \n"
        assert ralph.Sandbox.parse_dependencies(content) == []


# ---------------------------------------------------------------------------
# Sandbox.generate_project_dockerfile
# ---------------------------------------------------------------------------

class TestSandboxGenerateProjectDockerfile:
    def test_single_package(self):
        result = ralph.Sandbox.generate_project_dockerfile(["openjdk-21-jdk"])
        assert "apt-get install -y --no-install-recommends" in result
        assert "openjdk-21-jdk" in result

    def test_multiple_packages_joined(self):
        result = ralph.Sandbox.generate_project_dockerfile(["pkg1", "pkg2", "pkg3"])
        assert "'pkg1' 'pkg2' 'pkg3'" in result

    def test_packages_are_shell_quoted(self):
        result = ralph.Sandbox.generate_project_dockerfile(["pkg; rm -rf /"])
        assert "\"pkg; rm -rf /\"" not in result
        assert "'pkg; rm -rf /'" in result

    def test_contains_arg_and_from(self):
        result = ralph.Sandbox.generate_project_dockerfile(["pkg1"])
        assert "ARG BASE_IMAGE" in result
        assert "FROM ${BASE_IMAGE}" in result

    def test_user_switching(self):
        result = ralph.Sandbox.generate_project_dockerfile(["pkg1"])
        lines = result.splitlines()
        assert "USER root" in lines
        assert "USER agent" in lines
        assert lines.index("USER root") < lines.index("USER agent")

    def test_apt_cleanup(self):
        result = ralph.Sandbox.generate_project_dockerfile(["pkg1"])
        assert "rm -rf /var/lib/apt/lists/*" in result

    def test_no_install_recommends(self):
        result = ralph.Sandbox.generate_project_dockerfile(["pkg1"])
        assert "--no-install-recommends" in result


# ---------------------------------------------------------------------------
# Sandbox.find_project_config
# ---------------------------------------------------------------------------

class TestSandboxFindProjectConfig:
    def test_returns_none_when_no_agent_loop_dir(self, tmp_path):
        result = ralph.Sandbox.find_project_config(str(tmp_path))
        assert result is None

    def test_returns_none_when_agent_loop_empty(self, tmp_path):
        (tmp_path / ".agent-loop").mkdir()
        result = ralph.Sandbox.find_project_config(str(tmp_path))
        assert result is None

    def test_prefers_dockerfile_over_dependencies(self, tmp_path):
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\n")
        (al / "Dockerfile.sandbox").write_text("FROM base\n")
        config_type, path = ralph.Sandbox.find_project_config(str(tmp_path))
        assert config_type == "dockerfile"
        assert path == str(al / "Dockerfile.sandbox")

    def test_returns_dependencies_when_no_dockerfile(self, tmp_path):
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\n")
        config_type, path = ralph.Sandbox.find_project_config(str(tmp_path))
        assert config_type == "dependencies"
        assert path == str(al / "dependencies")

    def test_returns_dockerfile_when_no_dependencies(self, tmp_path):
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "Dockerfile.sandbox").write_text("FROM base\n")
        config_type, path = ralph.Sandbox.find_project_config(str(tmp_path))
        assert config_type == "dockerfile"
        assert path == str(al / "Dockerfile.sandbox")


# ---------------------------------------------------------------------------
# Sandbox.project_image_tag
# ---------------------------------------------------------------------------

class TestSandboxProjectImageTag:
    def test_includes_agent_and_project_in_tag(self):
        tag = ralph.Sandbox.project_image_tag("claude", "myproject", "base:v1", "content")
        assert tag.startswith("agent-loop-sandbox-claude-myproject:v")

    def test_hash_is_8_chars(self):
        tag = ralph.Sandbox.project_image_tag("claude", "proj", "base:v1", "content")
        chash = tag.split(":v")[1]
        assert len(chash) == 8

    def test_different_content_produces_different_hash(self):
        tag1 = ralph.Sandbox.project_image_tag("claude", "proj", "base:v1", "content-a")
        tag2 = ralph.Sandbox.project_image_tag("claude", "proj", "base:v1", "content-b")
        assert tag1 != tag2

    def test_same_content_produces_same_hash(self):
        tag1 = ralph.Sandbox.project_image_tag("claude", "proj", "base:v1", "content")
        tag2 = ralph.Sandbox.project_image_tag("claude", "proj", "base:v1", "content")
        assert tag1 == tag2

    def test_different_base_tag_produces_different_hash(self):
        tag1 = ralph.Sandbox.project_image_tag("claude", "proj", "base:v1", "content")
        tag2 = ralph.Sandbox.project_image_tag("claude", "proj", "base:v2", "content")
        assert tag1 != tag2


# ---------------------------------------------------------------------------
# Sandbox.ensure_project_image
# ---------------------------------------------------------------------------

class TestSandboxEnsureProjectImage:
    @staticmethod
    def _make_sandbox(tmp_path):
        agent_dir = tmp_path / "docker" / "agent-loop" / "claude"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "Dockerfile").write_text("FROM base:latest\nRUN echo hi")
        return ralph.Sandbox(str(tmp_path))

    def test_returns_base_tag_when_no_project_config(self, tmp_path):
        sb = self._make_sandbox(tmp_path)
        result = sb.ensure_project_image("claude", "base:v1", str(tmp_path))
        assert result == "base:v1"

    @patch("ralph.subprocess.run")
    def test_builds_when_tag_missing_with_dependencies(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\npkg2\n")
        # image_exists returns False (tag not cached)
        mock_run.return_value = MagicMock(returncode=1)
        tag = sb.ensure_project_image("claude", "base:v1", str(tmp_path))
        assert tag.startswith("agent-loop-sandbox-claude-")
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(build_calls) == 1
        # Verify --build-arg BASE_IMAGE passed
        build_cmd = build_calls[0][0][0]
        assert "--build-arg" in build_cmd
        assert "BASE_IMAGE=base:v1" in build_cmd

    @patch("ralph.subprocess.run")
    def test_builds_with_dockerfile_sandbox(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "Dockerfile.sandbox").write_text(
            "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\nRUN echo custom\n")
        mock_run.return_value = MagicMock(returncode=1)
        tag = sb.ensure_project_image("claude", "base:v1", str(tmp_path))
        assert tag.startswith("agent-loop-sandbox-claude-")
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(build_calls) == 1
        build_cmd = build_calls[0][0][0]
        # Uses -f Dockerfile.sandbox with .agent-loop/ as context
        assert "-f" in build_cmd
        assert "Dockerfile.sandbox" in build_cmd
        assert str(al) in build_cmd

    @patch("ralph.subprocess.run")
    def test_skips_build_when_tag_exists(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\n")
        # image_exists returns True (tag cached)
        mock_run.return_value = MagicMock(returncode=0)
        tag = sb.ensure_project_image("claude", "base:v1", str(tmp_path))
        assert tag.startswith("agent-loop-sandbox-claude-")
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(build_calls) == 0

    @patch("ralph.subprocess.run")
    def test_force_rebuild_forces_build(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\n")
        mock_run.return_value = MagicMock(returncode=0)
        sb.ensure_project_image("claude", "base:v1", str(tmp_path),
                                force_rebuild=True)
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(build_calls) == 1

    @patch("ralph.subprocess.run")
    def test_project_name_derived_from_dir(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        project = tmp_path / "elasticsearch"
        project.mkdir()
        al = project / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\n")
        mock_run.return_value = MagicMock(returncode=1)
        tag = sb.ensure_project_image("claude", "base:v1", str(project))
        assert "elasticsearch" in tag


# ---------------------------------------------------------------------------
# Sandbox._parse_docker_timestamp
# ---------------------------------------------------------------------------

class TestSandboxParseDockerTimestamp:
    def test_z_suffix(self):
        dt = ralph.Sandbox._parse_docker_timestamp("2024-06-15T10:30:00Z")
        assert dt.year == 2024 and dt.month == 6

    def test_truncates_nanoseconds(self):
        dt = ralph.Sandbox._parse_docker_timestamp("2024-06-15T10:30:00.123456789Z")
        assert dt.microsecond == 123456

    def test_offset_format(self):
        dt = ralph.Sandbox._parse_docker_timestamp("2024-06-15T10:30:00+00:00")
        assert dt.year == 2024


# ---------------------------------------------------------------------------
# Sandbox.needs_rebuild
# ---------------------------------------------------------------------------

class TestSandboxNeedsRebuild:
    @staticmethod
    def _make_sandbox(tmp_path, dockerfile="FROM base:latest\nRUN echo hi"):
        agent_dir = tmp_path / "docker" / "agent-loop" / "claude"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "Dockerfile").write_text(dockerfile)
        return ralph.Sandbox(str(tmp_path))

    @patch("ralph.subprocess.run")
    def test_true_when_image_missing(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.return_value = MagicMock(returncode=1)
        assert sb.needs_rebuild("claude") is True

    @patch("ralph.subprocess.run")
    def test_true_when_image_old(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.return_value = MagicMock(returncode=0, stdout="2020-01-01T00:00:00Z\n")
        assert sb.needs_rebuild("claude") is True

    @patch("ralph.subprocess.run")
    def test_false_when_image_recent(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.return_value = MagicMock(returncode=0, stdout="2099-01-01T00:00:00Z\n")
        assert sb.needs_rebuild("claude") is False


# ---------------------------------------------------------------------------
# Sandbox.ensure_image
# ---------------------------------------------------------------------------

class TestSandboxEnsureImage:
    @staticmethod
    def _make_sandbox(tmp_path, dockerfile="FROM base:latest\nRUN echo hi"):
        agent_dir = tmp_path / "docker" / "agent-loop" / "claude"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "Dockerfile").write_text(dockerfile)
        return ralph.Sandbox(str(tmp_path))

    @staticmethod
    def _side_effect(image_exists=True, base_age="2099-01-01T00:00:00Z"):
        def fn(cmd, **kwargs):
            # docker image inspect <img> --format ... → base digest
            if cmd[1:3] == ["image", "inspect"] and "--format" in cmd:
                return MagicMock(returncode=0, stdout="sha256:abc\n")
            # docker image inspect <tag> → existence check
            if cmd[1:3] == ["image", "inspect"]:
                rc = 0 if image_exists else 1
                return MagicMock(returncode=rc)
            # docker inspect --format {{.Created}} <img> → age check
            if cmd[1] == "inspect":
                return MagicMock(returncode=0, stdout=f"{base_age}\n")
            return MagicMock(returncode=0)
        return fn

    @patch("ralph.subprocess.run")
    def test_skips_build_when_tag_exists(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.side_effect = self._side_effect(image_exists=True)
        tag = sb.ensure_image("claude")
        assert tag.startswith("agent-loop-sandbox-claude:v")
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(build_calls) == 0

    @patch("ralph.subprocess.run")
    def test_builds_when_tag_missing(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.side_effect = self._side_effect(image_exists=False)
        sb.ensure_image("claude")
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(build_calls) == 1

    @patch("ralph.subprocess.run")
    def test_rebuild_forces_pull_and_build(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.side_effect = self._side_effect(image_exists=True)
        sb.ensure_image("claude", force_rebuild=True)
        pull_calls = [c for c in mock_run.call_args_list
                      if c[0][0][1] == "pull"]
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(pull_calls) == 1
        assert len(build_calls) == 1

    @patch("ralph.subprocess.run")
    def test_pulls_when_base_image_stale(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.side_effect = self._side_effect(
            image_exists=False, base_age="2020-01-01T00:00:00Z")
        sb.ensure_image("claude")
        pull_calls = [c for c in mock_run.call_args_list
                      if c[0][0][1] == "pull"]
        assert len(pull_calls) == 1


# ---------------------------------------------------------------------------
# Sandbox.sandbox_name
# ---------------------------------------------------------------------------

class TestSandboxName:
    def test_simple_branch(self):
        assert ralph.Sandbox.sandbox_name("claude", "fix-auth") == "agent-loop-claude-fix-auth"

    def test_branch_with_slashes(self):
        assert ralph.Sandbox.sandbox_name("claude", "feature/foo") == "agent-loop-claude-feature-foo"

    def test_branch_with_multiple_slashes(self):
        assert ralph.Sandbox.sandbox_name("claude", "user/feature/bar") == "agent-loop-claude-user-feature-bar"

    def test_branch_uppercase_lowered(self):
        assert ralph.Sandbox.sandbox_name("claude", "Fix-Auth") == "agent-loop-claude-fix-auth"

    def test_consecutive_slashes_collapsed(self):
        assert ralph.Sandbox.sandbox_name("claude", "a//b") == "agent-loop-claude-a-b"

    def test_leading_trailing_hyphens_stripped(self):
        assert ralph.Sandbox.sandbox_name("claude", "-branch-") == "agent-loop-claude-branch"

    def test_custom_agent(self):
        assert ralph.Sandbox.sandbox_name("codex", "my-branch") == "agent-loop-codex-my-branch"


# ---------------------------------------------------------------------------
# Sandbox.ensure_sandbox (mocked docker)
# ---------------------------------------------------------------------------

class TestSandboxEnsureSandbox:
    @staticmethod
    def _make_sandbox(tmp_path, dockerfile="FROM base:latest\nRUN echo hi"):
        agent_dir = tmp_path / "docker" / "agent-loop" / "claude"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "Dockerfile").write_text(dockerfile)
        return ralph.Sandbox(str(tmp_path))

    @patch.object(ralph.Sandbox, "apply_network_policy")
    @patch.object(ralph.Sandbox, "_docker_sandbox_create")
    @patch.object(ralph.Sandbox, "ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(ralph.Sandbox, "sandbox_exists", return_value=False)
    def test_creates_new_sandbox(self, mock_exists, mock_img, mock_create, mock_policy):
        sb = ralph.Sandbox("/dotfiles")
        name = sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        assert name == "agent-loop-claude-fix-auth"
        mock_create.assert_called_once_with(
            "agent-loop-claude-fix-auth", "agent-loop-sandbox-claude:vabc", "/work/fix-auth")
        mock_policy.assert_called_once_with("agent-loop-claude-fix-auth")

    @patch.object(ralph.Sandbox, "sandbox_exists", return_value=True)
    def test_reuses_existing_sandbox(self, mock_exists):
        sb = ralph.Sandbox("/dotfiles")
        name = sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        assert name == "agent-loop-claude-fix-auth"

    @patch.object(ralph.Sandbox, "apply_network_policy")
    @patch.object(ralph.Sandbox, "_docker_sandbox_create")
    @patch.object(ralph.Sandbox, "ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(ralph.Sandbox, "sandbox_exists", return_value=True)
    def test_reuse_skips_create_and_policy(self, mock_exists, mock_img, mock_create, mock_policy):
        sb = ralph.Sandbox("/dotfiles")
        sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        mock_create.assert_not_called()
        mock_policy.assert_not_called()
        mock_img.assert_not_called()

    @patch.object(ralph.Sandbox, "apply_network_policy")
    @patch.object(ralph.Sandbox, "_docker_sandbox_create")
    @patch.object(ralph.Sandbox, "ensure_project_image",
                  return_value="agent-loop-sandbox-claude-myproj:vdef12345")
    @patch.object(ralph.Sandbox, "ensure_image",
                  return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(ralph.Sandbox, "sandbox_exists", return_value=False)
    def test_calls_ensure_project_image_when_project_dir(
            self, mock_exists, mock_img, mock_proj, mock_create, mock_policy):
        sb = ralph.Sandbox("/dotfiles")
        sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth",
                          project_dir="/repo/root")
        mock_proj.assert_called_once_with(
            "claude", "agent-loop-sandbox-claude:vabc", "/repo/root",
            force_rebuild=False)
        mock_create.assert_called_once_with(
            "agent-loop-claude-fix-auth",
            "agent-loop-sandbox-claude-myproj:vdef12345",
            "/work/fix-auth")

    @patch.object(ralph.Sandbox, "apply_network_policy")
    @patch.object(ralph.Sandbox, "_docker_sandbox_create")
    @patch.object(ralph.Sandbox, "ensure_image",
                  return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(ralph.Sandbox, "sandbox_exists", return_value=False)
    def test_skips_project_image_when_no_project_dir(
            self, mock_exists, mock_img, mock_create, mock_policy):
        sb = ralph.Sandbox("/dotfiles")
        with patch.object(ralph.Sandbox, "ensure_project_image") as mock_proj:
            sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
            mock_proj.assert_not_called()
        mock_create.assert_called_once_with(
            "agent-loop-claude-fix-auth",
            "agent-loop-sandbox-claude:vabc",
            "/work/fix-auth")

    @patch.object(ralph.Sandbox, "apply_network_policy")
    @patch.object(ralph.Sandbox, "_docker_sandbox_create")
    @patch.object(ralph.Sandbox, "ensure_project_image",
                  return_value="agent-loop-sandbox-claude-myproj:vdef12345")
    @patch.object(ralph.Sandbox, "ensure_image",
                  return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(ralph.Sandbox, "sandbox_exists", return_value=False)
    def test_force_rebuild_passed_through(
            self, mock_exists, mock_img, mock_proj, mock_create, mock_policy):
        sb = ralph.Sandbox("/dotfiles")
        sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth",
                          project_dir="/repo/root", force_rebuild=True)
        mock_img.assert_called_once_with("claude", force_rebuild=True)
        mock_proj.assert_called_once_with(
            "claude", "agent-loop-sandbox-claude:vabc", "/repo/root",
            force_rebuild=True)


# ---------------------------------------------------------------------------
# Sandbox.apply_network_policy
# ---------------------------------------------------------------------------

class TestSandboxApplyNetworkPolicy:
    @patch("ralph.subprocess.run")
    def test_correct_command(self, mock_run):
        ralph.Sandbox.apply_network_policy("agent-loop-claude-fix-auth")
        mock_run.assert_called_once_with(
            ["docker", "sandbox", "network", "proxy", "agent-loop-claude-fix-auth",
             "--policy", "deny",
             "--allow-host", "localhost",
             "--allow-host", "api.anthropic.com",
             "--allow-host", "statsig.anthropic.com",
             "--allow-host", "sentry.io"],
            check=True,
        )


# ---------------------------------------------------------------------------
# Sandbox.cleanup_sandbox
# ---------------------------------------------------------------------------

class TestSandboxCleanup:
    @patch("ralph.subprocess.run")
    def test_removes_sandbox(self, mock_run):
        ralph.Sandbox.cleanup_sandbox("claude", "fix-auth")
        mock_run.assert_called_once_with(
            ["docker", "sandbox", "rm", "agent-loop-claude-fix-auth"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )


# ---------------------------------------------------------------------------
# Sandbox.prune_sandboxes (mocked docker + filesystem)
# ---------------------------------------------------------------------------

class TestSandboxPruneSandboxes:
    @patch("ralph.subprocess.run")
    @patch.object(ralph.Sandbox, "_docker_sandbox_ls")
    def test_removes_orphans(self, mock_ls, mock_run, tmp_path):
        # Create one workspace that exists
        existing = tmp_path / "workspace"
        existing.mkdir()
        mock_ls.return_value = {
            "vms": [
                {"name": "agent-loop-claude-active", "workspace": str(existing)},
                {"name": "agent-loop-claude-orphan", "workspace": "/nonexistent/path"},
            ]
        }
        sb = ralph.Sandbox(str(tmp_path))
        pruned = sb.prune_sandboxes("claude")
        assert pruned == ["agent-loop-claude-orphan"]
        mock_run.assert_called_once_with(
            ["docker", "sandbox", "rm", "agent-loop-claude-orphan"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )

    @patch("ralph.subprocess.run")
    @patch.object(ralph.Sandbox, "_docker_sandbox_ls")
    def test_keeps_active_sandboxes(self, mock_ls, mock_run, tmp_path):
        existing = tmp_path / "workspace"
        existing.mkdir()
        mock_ls.return_value = {
            "vms": [
                {"name": "agent-loop-claude-active", "workspace": str(existing)},
            ]
        }
        sb = ralph.Sandbox(str(tmp_path))
        pruned = sb.prune_sandboxes("claude")
        assert pruned == []
        mock_run.assert_not_called()

    @patch.object(ralph.Sandbox, "_docker_sandbox_ls")
    def test_ignores_other_agents(self, mock_ls, tmp_path):
        mock_ls.return_value = {
            "vms": [
                {"name": "agent-loop-codex-orphan", "workspace": "/nonexistent"},
            ]
        }
        sb = ralph.Sandbox(str(tmp_path))
        pruned = sb.prune_sandboxes("claude")
        assert pruned == []

    @patch.object(ralph.Sandbox, "_docker_sandbox_ls")
    def test_empty_vm_list(self, mock_ls, tmp_path):
        mock_ls.return_value = {"vms": []}
        sb = ralph.Sandbox(str(tmp_path))
        pruned = sb.prune_sandboxes("claude")
        assert pruned == []


# ---------------------------------------------------------------------------
# Sandbox._docker_sandbox_ls (mocked subprocess)
# ---------------------------------------------------------------------------

class TestSandboxDockerSandboxLs:
    @patch("ralph.subprocess.run")
    def test_parses_json_output(self, mock_run):
        vms_data = {"vms": [{"name": "test-vm", "workspace": "/tmp/w"}]}
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps(vms_data))
        result = ralph.Sandbox._docker_sandbox_ls()
        assert result == vms_data

    @patch("ralph.subprocess.run")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = ralph.Sandbox._docker_sandbox_ls()
        assert result == {"vms": []}

    @patch("ralph.subprocess.run")
    def test_returns_empty_on_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        result = ralph.Sandbox._docker_sandbox_ls()
        assert result == {"vms": []}


# ---------------------------------------------------------------------------
# Sandbox.preflight_check (mocked token, proxy, docker)
# ---------------------------------------------------------------------------

class TestSandboxPreflightCheck:
    SANDBOX_NAME = "agent-loop-claude-fix-auth"

    @staticmethod
    def _run_side_effect(echo_rc=0, curl_rc=28):
        """Create a subprocess.run side_effect for sandbox exec calls."""
        def fn(cmd, **kwargs):
            if "echo" in cmd:
                return MagicMock(returncode=echo_rc, stdout="ok\n", stderr="")
            if "curl" in cmd:
                return MagicMock(returncode=curl_rc, stdout="", stderr="")
            return MagicMock(returncode=0)
        return fn

    @patch("ralph.subprocess.run")
    @patch("ralph.urllib.request.urlopen")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_all_checks_pass(self, mock_time, mock_read, mock_urlopen, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_urlopen.return_value = MagicMock(status=200)
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = ralph.Sandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert failures == []

    @patch("ralph.subprocess.run")
    @patch("ralph.urllib.request.urlopen")
    @patch("ralph.read_token_from_keychain", return_value=None)
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_token_missing_returns_error(self, mock_time, mock_read, mock_urlopen, mock_run):
        mock_urlopen.return_value = MagicMock(status=200)
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = ralph.Sandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "no token found" in failures[0]
        assert "ralph store-token" in failures[0]

    @patch("ralph.subprocess.run")
    @patch("ralph.urllib.request.urlopen")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_token_expired_returns_error(self, mock_time, mock_read, mock_urlopen, mock_run):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": past_ms}
        mock_urlopen.return_value = MagicMock(status=200)
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = ralph.Sandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "token expired" in failures[0]
        assert "ralph store-token" in failures[0]

    @patch("ralph.subprocess.run")
    @patch("ralph.urllib.request.urlopen")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_proxy_down_returns_error(self, mock_time, mock_read, mock_urlopen, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_urlopen.side_effect = Exception("Connection refused")
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = ralph.Sandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "proxy not reachable" in failures[0]
        assert "start the credential proxy" in failures[0]

    @patch("ralph.subprocess.run")
    @patch("ralph.urllib.request.urlopen")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_sandbox_unresponsive_returns_error(self, mock_time, mock_read, mock_urlopen, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_urlopen.return_value = MagicMock(status=200)
        mock_run.side_effect = self._run_side_effect(echo_rc=1, curl_rc=28)
        sb = ralph.Sandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "not responding" in failures[0]
        assert f"docker sandbox rm {self.SANDBOX_NAME}" in failures[0]

    @patch("ralph.subprocess.run")
    @patch("ralph.urllib.request.urlopen")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_sandbox_unresponsive_skips_network_check(self, mock_time, mock_read, mock_urlopen, mock_run):
        """When sandbox is unresponsive, network policy check is skipped."""
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_urlopen.return_value = MagicMock(status=200)
        mock_run.side_effect = self._run_side_effect(echo_rc=1, curl_rc=0)
        sb = ralph.Sandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        # Should only have sandbox error, not network policy error
        assert len(failures) == 1
        assert "not responding" in failures[0]
        # curl should not have been called
        curl_calls = [c for c in mock_run.call_args_list if "curl" in c[0][0]]
        assert len(curl_calls) == 0

    @patch("ralph.subprocess.run")
    @patch("ralph.urllib.request.urlopen")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_network_policy_not_applied_returns_error(self, mock_time, mock_read, mock_urlopen, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_urlopen.return_value = MagicMock(status=200)
        # echo succeeds, curl also succeeds (google.com reachable = bad)
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=0)
        sb = ralph.Sandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "network policy not applied" in failures[0]
        assert "outbound requests should be blocked" in failures[0]

    @patch("ralph.subprocess.run")
    @patch("ralph.urllib.request.urlopen", side_effect=Exception("refused"))
    @patch("ralph.read_token_from_keychain", return_value=None)
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_multiple_failures_collected(self, mock_time, mock_read, mock_urlopen, mock_run):
        """All failures are collected, not just the first one."""
        mock_run.side_effect = self._run_side_effect(echo_rc=1, curl_rc=28)
        sb = ralph.Sandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        # token missing + proxy down + sandbox unresponsive = 3 failures
        assert len(failures) == 3


# ---------------------------------------------------------------------------
# Sandbox.setup_git_config (mocked subprocess)
# ---------------------------------------------------------------------------

class TestSandboxSetupGitConfig:
    @patch("ralph.subprocess.run")
    def test_sets_user_name_email_and_safe_directory(self, mock_run):
        ralph.Sandbox.setup_git_config("my-sandbox", "Ralph", "ralph@test.com")
        assert mock_run.call_count == 3

        name_call = mock_run.call_args_list[0]
        assert name_call[0][0] == [
            "docker", "sandbox", "exec", "my-sandbox",
            "git", "config", "--global", "user.name", "Ralph",
        ]

        email_call = mock_run.call_args_list[1]
        assert email_call[0][0] == [
            "docker", "sandbox", "exec", "my-sandbox",
            "git", "config", "--global", "user.email", "ralph@test.com",
        ]

        safe_call = mock_run.call_args_list[2]
        assert safe_call[0][0] == [
            "docker", "sandbox", "exec", "my-sandbox",
            "git", "config", "--global", "--add", "safe.directory", "*",
        ]


# ---------------------------------------------------------------------------
# Sandbox.run_iteration (mocked subprocess)
# ---------------------------------------------------------------------------

class TestSandboxRunIteration:
    @patch("ralph.subprocess.run")
    def test_writes_spec_runs_claude_reads_back(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=0),  # run claude
            MagicMock(returncode=0, stdout="updated spec"),  # read spec
        ]
        sb = ralph.Sandbox("/dotfiles")
        rc, updated = sb.run_iteration("my-sandbox", "original spec", "sonnet")
        assert rc == 0
        assert updated == "updated spec"

        # Verify write call pipes spec content via tee
        write_call = mock_run.call_args_list[0]
        assert write_call[1]["input"] == "original spec"
        assert "tee" in write_call[0][0]
        assert "/tmp/spec.md" in write_call[0][0]

        # Verify claude call
        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        assert "claude" in cmd
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "sonnet"
        assert "--dangerously-skip-permissions" in cmd
        assert "--effort" in cmd

        # Verify read-back call
        read_call = mock_run.call_args_list[2]
        assert "cat" in read_call[0][0]
        assert "/tmp/spec.md" in read_call[0][0]

    @patch("ralph.subprocess.run")
    def test_passes_env_vars_to_claude(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=0),  # run claude
            MagicMock(returncode=0, stdout="spec"),  # read spec
        ]
        sb = ralph.Sandbox("/dotfiles")
        sb.run_iteration("my-sandbox", "spec", "sonnet",
                         env_vars={"CLAUDE_CODE_OAUTH_TOKEN": "sk-test"})

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        assert "-e" in cmd
        e_idx = cmd.index("-e")
        assert cmd[e_idx + 1] == "CLAUDE_CODE_OAUTH_TOKEN=sk-test"

    @patch("ralph.subprocess.run")
    def test_returns_original_spec_on_write_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        sb = ralph.Sandbox("/dotfiles")
        rc, updated = sb.run_iteration("my-sandbox", "original", "sonnet")
        assert rc == 1
        assert updated == "original"
        # Only the write call should have been made
        assert mock_run.call_count == 1

    @patch("ralph.subprocess.run")
    def test_returns_original_spec_on_read_failure(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=0),  # run claude
            MagicMock(returncode=1, stdout=""),  # read spec fails
        ]
        sb = ralph.Sandbox("/dotfiles")
        rc, updated = sb.run_iteration("my-sandbox", "original", "sonnet")
        assert rc == 0
        assert updated == "original"

    @patch("ralph.subprocess.run")
    def test_returns_claude_exit_code(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=42),  # claude fails
            MagicMock(returncode=0, stdout="spec"),  # read spec
        ]
        sb = ralph.Sandbox("/dotfiles")
        rc, _ = sb.run_iteration("my-sandbox", "spec", "sonnet")
        assert rc == 42


# ---------------------------------------------------------------------------
# ensure_worktree
# ---------------------------------------------------------------------------

def _mock_git_for_worktree(*, remotes="origin", porcelain="",
                           toplevel="/Users/me/code/myrepo",
                           symbolic_ref="refs/remotes/origin/main",
                           default_branch_name="main",
                           rev_parse_verify_ok=True,
                           ls_remote_ok=False,
                           local_branch_exists=False):
    """Build a MagicMock Git instance with side-effects for ensure_worktree."""
    git = MagicMock()

    # git.output dispatches on first arg
    def output_side_effect(*args, **kwargs):
        if args[0] == "rev-parse" and args[1] == "--show-toplevel":
            return toplevel
        if args[0] == "remote":
            return remotes
        if args[0] == "symbolic-ref":
            # "symbolic-ref refs/remotes/<remote>/HEAD" → full ref
            if len(args) > 1 and "remotes" in args[1]:
                return symbolic_ref
            # "symbolic-ref --short HEAD" → short branch name
            return default_branch_name
        return ""
    git.output.side_effect = output_side_effect

    # git.run dispatches on first arg
    def run_side_effect(*args, **kwargs):
        check = kwargs.get("check", True)
        if args[0] == "worktree" and args[1] == "list":
            return MagicMock(stdout=porcelain)
        if args[0] == "rev-parse" and args[1] == "--verify":
            if "refs/heads/" in args[2]:
                # Checking if a local branch exists
                rc = 0 if local_branch_exists else 128
            else:
                # Verifying default branch name is valid
                rc = 0 if rev_parse_verify_ok else 128
            result = MagicMock(returncode=rc)
            if check and rc != 0:
                raise subprocess.CalledProcessError(rc, "git")
            return result
        if args[0] == "ls-remote":
            rc = 0 if ls_remote_ok else 2
            return MagicMock(returncode=rc)
        # worktree add, etc. — just succeed
        return MagicMock(returncode=0)
    git.run.side_effect = run_side_effect

    return git


class TestEnsureWorktree:
    def test_returns_existing_worktree(self):
        """If a worktree already exists for the branch, return its path."""
        porcelain = (
            "worktree /Users/me/code/myrepo\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /Users/me/code/myrepo-my-feature\n"
            "branch refs/heads/my-feature\n"
            "\n"
        )
        git = _mock_git_for_worktree(porcelain=porcelain)

        result = ralph.ensure_worktree(git, "my-feature")
        assert result == "/Users/me/code/myrepo-my-feature"
        # Should not call worktree add
        for c in git.run.call_args_list:
            assert c[0][0:2] != ("worktree", "add")

    def test_creates_new_branch_from_default(self):
        """No remote branch, no local branch — creates new branch from default."""
        git = _mock_git_for_worktree(remotes="origin", ls_remote_ok=False,
                                     local_branch_exists=False)

        result = ralph.ensure_worktree(git, "new-feature")
        assert result == "/Users/me/code/myrepo-new-feature"
        git.run.assert_any_call(
            "worktree", "add", "-b", "new-feature",
            "/Users/me/code/myrepo-new-feature", "main")

    def test_tracks_remote_branch(self):
        """Remote branch exists — creates tracking worktree."""
        git = _mock_git_for_worktree(remotes="origin", ls_remote_ok=True,
                                     local_branch_exists=False)

        result = ralph.ensure_worktree(git, "remote-feature")
        assert result == "/Users/me/code/myrepo-remote-feature"
        git.run.assert_any_call(
            "worktree", "add", "--track", "-b", "remote-feature",
            "/Users/me/code/myrepo-remote-feature", "origin/remote-feature")

    def test_uses_existing_local_branch(self):
        """Local branch exists but no worktree — attaches without -b."""
        git = _mock_git_for_worktree(remotes="origin", local_branch_exists=True)

        result = ralph.ensure_worktree(git, "existing-branch")
        assert result == "/Users/me/code/myrepo-existing-branch"
        git.run.assert_any_call(
            "worktree", "add",
            "/Users/me/code/myrepo-existing-branch", "existing-branch")

    def test_prefers_upstream_over_origin(self):
        """When both upstream and origin exist, use upstream."""
        git = _mock_git_for_worktree(
            remotes="origin\nupstream", ls_remote_ok=False,
            local_branch_exists=False,
            symbolic_ref="refs/remotes/upstream/main",
        )

        ralph.ensure_worktree(git, "feat")
        # Should resolve default branch via upstream
        git.output.assert_any_call("symbolic-ref", "refs/remotes/upstream/HEAD")

    def test_no_remote_creates_branch_from_head(self):
        """No remotes — falls back to HEAD for base branch."""
        git = _mock_git_for_worktree(remotes="", local_branch_exists=False)

        result = ralph.ensure_worktree(git, "solo-feature")
        assert result == "/Users/me/code/myrepo-solo-feature"
        git.run.assert_any_call(
            "worktree", "add", "-b", "solo-feature",
            "/Users/me/code/myrepo-solo-feature", "main")

    def test_base_override(self):
        """Explicit base overrides the resolved default branch."""
        git = _mock_git_for_worktree(remotes="origin", ls_remote_ok=False,
                                     local_branch_exists=False)

        result = ralph.ensure_worktree(git, "feat", base="develop")
        assert result == "/Users/me/code/myrepo-feat"
        git.run.assert_any_call(
            "worktree", "add", "-b", "feat",
            "/Users/me/code/myrepo-feat", "develop")

    def test_slash_in_branch_name_sanitized(self):
        """Slashes in branch names are replaced with hyphens in the path."""
        git = _mock_git_for_worktree(remotes="origin", ls_remote_ok=False,
                                     local_branch_exists=False)

        result = ralph.ensure_worktree(git, "user/my-feature")
        assert result == "/Users/me/code/myrepo-user-my-feature"

    def test_worktree_list_failure_treated_as_empty(self):
        """If git worktree list fails, treat as no existing worktrees."""
        git = _mock_git_for_worktree(remotes="origin", ls_remote_ok=False,
                                     local_branch_exists=False)
        # Override worktree list to raise
        original_side_effect = git.run.side_effect
        def run_with_wt_failure(*args, **kwargs):
            if args[0] == "worktree" and args[1] == "list":
                raise subprocess.CalledProcessError(1, "git")
            return original_side_effect(*args, **kwargs)
        git.run.side_effect = run_with_wt_failure

        result = ralph.ensure_worktree(git, "new-feat")
        assert result == "/Users/me/code/myrepo-new-feat"


# ---------------------------------------------------------------------------
# try_fast_forward
# ---------------------------------------------------------------------------

def _mock_git_for_ff(*, remotes="origin",
                     symbolic_ref="refs/remotes/origin/main",
                     fetch_ok=True, merge_ok=True):
    """Build a MagicMock Git instance for try_fast_forward tests."""
    git = MagicMock()

    def output_side_effect(*args, **kwargs):
        if args[0] == "remote":
            return remotes
        if args[0] == "symbolic-ref":
            return symbolic_ref
        return ""
    git.output.side_effect = output_side_effect

    def run_side_effect(*args, **kwargs):
        if args[0] == "fetch":
            return MagicMock(returncode=0 if fetch_ok else 1)
        if args[0] == "merge":
            return MagicMock(returncode=0 if merge_ok else 1)
        return MagicMock(returncode=0)
    git.run.side_effect = run_side_effect

    return git


class TestTryFastForward:
    def test_fast_forwards_to_main(self):
        git = _mock_git_for_ff()
        result = ralph.try_fast_forward(git, "/work/my-branch")
        assert result == "origin/main"
        git.run.assert_any_call("fetch", "origin", "main",
                                cwd="/work/my-branch", check=False)
        git.run.assert_any_call("merge", "--ff-only", "origin/main",
                                cwd="/work/my-branch", check=False)

    def test_uses_explicit_base(self):
        git = _mock_git_for_ff()
        result = ralph.try_fast_forward(git, "/work/feat", base="8.x")
        assert result == "origin/8.x"
        git.run.assert_any_call("fetch", "origin", "8.x",
                                cwd="/work/feat", check=False)

    def test_prefers_upstream(self):
        git = _mock_git_for_ff(remotes="origin\nupstream",
                               symbolic_ref="refs/remotes/upstream/main")
        result = ralph.try_fast_forward(git, "/work/feat")
        assert result == "upstream/main"

    def test_returns_none_when_no_remote(self):
        git = _mock_git_for_ff(remotes="")
        result = ralph.try_fast_forward(git, "/work/feat")
        assert result is None

    def test_returns_none_when_fetch_fails(self):
        git = _mock_git_for_ff(fetch_ok=False)
        result = ralph.try_fast_forward(git, "/work/feat")
        assert result is None

    def test_returns_none_when_not_ff(self):
        """Branch has diverged — merge --ff-only fails, returns None."""
        git = _mock_git_for_ff(merge_ok=False)
        result = ralph.try_fast_forward(git, "/work/feat")
        assert result is None

    def test_returns_none_when_no_default_branch_detected(self):
        git = _mock_git_for_ff(symbolic_ref="")
        result = ralph.try_fast_forward(git, "/work/feat")
        assert result is None


# ---------------------------------------------------------------------------
# process_issue (sandbox-based, mocked)
# ---------------------------------------------------------------------------

class TestProcessIssueSandbox:
    @patch("ralph.unblock_ready_specs")
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
    def test_resets_sandbox_when_out_of_sync(self, mock_repo, mock_wt, mock_unblock):
        git = MagicMock()

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        sandbox.run_iteration.return_value = (0, "updated spec")
        sandbox.check_in_sync.return_value = False
        sandbox.reset_to_host.return_value = True
        # HEAD doesn't change = spec complete
        sandbox.exec_output.return_value = "abc123"

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        ralph.process_issue(
            42, git, sandbox, gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)

        sandbox.check_in_sync.assert_called_once_with(
            "agent-loop-claude-my-branch", "/work/my-branch", git)
        sandbox.reset_to_host.assert_called_once_with(
            "agent-loop-claude-my-branch", "/work/my-branch", git)

    @patch("ralph.unblock_ready_specs")
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
    def test_recreates_sandbox_when_reset_fails(self, mock_repo, mock_wt, mock_unblock):
        git = MagicMock()

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        sandbox.run_iteration.return_value = (0, "updated spec")
        sandbox.check_in_sync.return_value = False
        sandbox.reset_to_host.return_value = False
        # HEAD doesn't change = spec complete
        sandbox.exec_output.return_value = "abc123"

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        ralph.process_issue(
            42, git, sandbox, gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)

        sandbox.remove_sandbox.assert_called_once_with("agent-loop-claude-my-branch")
        # ensure_sandbox called twice: initial + recreation
        assert sandbox.ensure_sandbox.call_count == 2
        # setup_git_config called twice: initial + after recreation
        assert sandbox.setup_git_config.call_count == 2

    @patch("ralph.unblock_ready_specs")
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
    def test_uses_ensure_sandbox_and_run_iteration(self, mock_repo, mock_wt, mock_unblock):
        git = MagicMock()
        git.output.return_value = "/repo/root"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        sandbox.run_iteration.return_value = (0, "updated spec")
        # HEAD doesn't change = spec complete
        sandbox.exec_output.return_value = "abc123"

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        result = ralph.process_issue(
            42, git, sandbox, gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)
        assert result == 0

        sandbox.ensure_sandbox.assert_called_once_with(
            "claude", "my-branch", "/work/my-branch",
            project_dir="/repo/root", force_rebuild=False)
        sandbox.setup_git_config.assert_called_once_with(
            "agent-loop-claude-my-branch", "user", "user@test.com")
        sandbox.run_iteration.assert_called_once()

        # Verify run_iteration received phantom token + proxy base URL
        call_args = sandbox.run_iteration.call_args
        env_vars = call_args[1].get("env_vars") or call_args[0][3]
        assert env_vars["CLAUDE_CODE_OAUTH_TOKEN"] == "phantom"
        assert env_vars["ANTHROPIC_BASE_URL"] == "http://host.docker.internal:18080"

    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
    def test_iteration_failure_marks_needs_attention(self, mock_repo, mock_wt):
        git = MagicMock()

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        sandbox.run_iteration.return_value = (1, "spec")
        sandbox.exec_output.return_value = "abc123"

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        result = ralph.process_issue(
            42, git, sandbox, gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)
        assert result == 1

        gh.issue_edit.assert_any_call(
            42, "owner/repo",
            remove_label="status:in-progress",
            add_label="status:needs-attention")

    @patch("ralph.unblock_ready_specs")
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
    def test_pushes_after_iteration_when_flag_set(self, mock_repo, mock_wt, mock_unblock):
        git = MagicMock()

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        sandbox.run_iteration.return_value = (0, "updated spec")
        # First call returns "abc", second returns "def" (new commit),
        # third returns "def" (no new commit = done)
        sandbox.exec_output.side_effect = ["abc", "def", "def", "def"]

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        ralph.process_issue(
            42, git, sandbox, gh, "claude", True, "sonnet",
            "user", "user@test.com", 18080)

        git.run.assert_any_call("push", cwd="/work/my-branch", check=False)

    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
    def test_agent_codex_uses_correct_names(self, mock_repo, mock_wt):
        git = MagicMock()
        git.output.return_value = "/repo/root"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-codex-my-branch"
        sandbox.run_iteration.return_value = (0, "spec")
        sandbox.exec_output.return_value = "abc123"

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        ralph.process_issue(
            42, git, sandbox, gh, "codex", False, "sonnet",
            "user", "user@test.com", 18080)

        sandbox.ensure_sandbox.assert_called_once_with(
            "codex", "my-branch", "/work/my-branch",
            project_dir="/repo/root", force_rebuild=False)

    @patch("ralph.unblock_ready_specs")
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
    def test_rebuild_flag_passed_to_ensure_sandbox(self, mock_repo, mock_wt, mock_unblock):
        git = MagicMock()
        git.output.return_value = "/repo/root"

        sandbox = MagicMock()
        sandbox.ensure_sandbox.return_value = "agent-loop-claude-my-branch"
        sandbox.run_iteration.return_value = (0, "spec")
        sandbox.exec_output.return_value = "abc123"

        gh = MagicMock()
        gh.issue_view_title.return_value = "[my-branch] Test Issue"
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSpec"

        ralph.process_issue(
            42, git, sandbox, gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080, rebuild=True)

        sandbox.ensure_sandbox.assert_called_once_with(
            "claude", "my-branch", "/work/my-branch",
            project_dir="/repo/root", force_rebuild=True)


# ---------------------------------------------------------------------------
# main() — sandbox integration
# ---------------------------------------------------------------------------

class TestMainSandboxFlags:
    @patch("ralph.sys.argv", ["ralph", "--issue", "42", "--agent", "codex"])
    @patch("ralph.process_issue", return_value=0)
    @patch("ralph.ensure_token", return_value="sk-test")
    @patch("ralph.ensure_proxy", return_value=18080)
    @patch("ralph.Git")
    @patch("ralph.check_dependencies_prereq")
    def test_agent_flag_passed_through(self, mock_prereq,
                                       mock_git_cls, mock_proxy,
                                       mock_token, mock_process):
        mock_git_cls.return_value = MagicMock(
            output=MagicMock(return_value="user"))
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 0

        # Verify ensure_token called with agent "codex"
        mock_token.assert_called_once_with("codex")

        # Verify process_issue called with agent "codex", proxy_port, and rebuild
        call_args = mock_process.call_args[0]
        call_kwargs = mock_process.call_args[1]
        assert call_args[4] == "codex"  # agent parameter
        assert call_args[9] == 18080  # proxy_port (not an oauth token string)
        assert call_kwargs.get("rebuild") is False

    @patch("ralph.sys.argv", ["ralph", "--issue", "42", "--rebuild"])
    @patch("ralph.process_issue", return_value=0)
    @patch("ralph.ensure_token", return_value="sk-test")
    @patch("ralph.ensure_proxy", return_value=18080)
    @patch("ralph.Git")
    @patch("ralph.check_dependencies_prereq")
    def test_rebuild_forces_image_rebuild(self, mock_prereq,
                                          mock_git_cls, mock_proxy,
                                          mock_token, mock_process):
        mock_git_cls.return_value = MagicMock(
            output=MagicMock(return_value="user"))
        with pytest.raises(SystemExit):
            ralph.main()
        # Rebuild is now deferred — passed through process_issue to ensure_sandbox
        call_kwargs = mock_process.call_args[1]
        assert call_kwargs.get("rebuild") is True

    @patch("ralph.sys.argv", ["ralph", "--issue", "42"])
    @patch("ralph.process_issue", return_value=0)
    @patch("ralph.ensure_token", return_value="sk-test")
    @patch("ralph.ensure_proxy", return_value=18080)
    @patch("ralph.Git")
    @patch("ralph.check_dependencies_prereq")
    def test_starts_proxy_before_processing(self, mock_prereq,
                                            mock_git_cls, mock_proxy,
                                            mock_token, mock_process):
        mock_git_cls.return_value = MagicMock(
            output=MagicMock(return_value="user"))
        with pytest.raises(SystemExit):
            ralph.main()
        mock_proxy.assert_called_once()

    @patch("ralph.sys.argv", ["ralph", "--issue", "42"])
    @patch("ralph.process_issue", return_value=0)
    @patch("ralph.Git")
    @patch("ralph.check_dependencies_prereq")
    def test_ensure_token_called_before_proxy(self, mock_prereq,
                                              mock_git_cls, mock_process):
        mock_git_cls.return_value = MagicMock(
            output=MagicMock(return_value="user"))
        call_order = []
        with patch("ralph.ensure_token") as mock_token, \
             patch("ralph.ensure_proxy", return_value=18080) as mock_proxy:
            mock_token.side_effect = lambda a: call_order.append("token")
            mock_proxy.side_effect = lambda a, p, d: (
                call_order.append("proxy"), p)[-1]
            with pytest.raises(SystemExit):
                ralph.main()
        assert call_order == ["token", "proxy"]

    @patch("ralph.sys.argv", ["ralph", "--packages", "foo"])
    def test_packages_flag_rejected(self):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# selftest (mocked pipeline)
# ---------------------------------------------------------------------------

class TestSelftest:
    """Tests for the selftest() smoke test function."""

    FUTURE_MS = 1700000000000 + 30 * 86400 * 1000  # 30 days from now

    @patch("ralph.Sandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.Sandbox.apply_network_policy")
    @patch("ralph.Sandbox._docker_sandbox_create")
    @patch("ralph.Sandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", return_value=True)
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_all_checks_pass(self, mock_time, mock_read, mock_ensure_proxy,
                             mock_health, mock_img, mock_create, mock_policy,
                             mock_run, mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        # sandbox exec calls: proxy reachable (ok), claude (ok), curl google (blocked)
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),    # curl proxy health
            MagicMock(returncode=0, stdout="ok", stderr=""),    # claude via proxy
            MagicMock(returncode=28, stdout="", stderr=""),     # curl google (blocked)
        ]

        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 0

        captured = capsys.readouterr()
        assert "PASS: check token" in captured.out
        assert "PASS: proxy health" in captured.out
        assert "PASS: build image" in captured.out
        assert "PASS: create sandbox" in captured.out
        assert "PASS: network policy" in captured.out
        assert "PASS: proxy reachable from sandbox" in captured.out
        assert "PASS: claude auth via proxy" in captured.out
        assert "PASS: network isolation" in captured.out
        assert "all 8 checks passed" in captured.out

    @patch("ralph.read_token_from_keychain", return_value=None)
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_missing_token_aborts_early(self, mock_time, mock_read, capsys):
        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: check token" in captured.out
        assert "selftest aborted" in captured.out

    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_expired_token_aborts_early(self, mock_time, mock_read, capsys):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-old", "expiresAt": past_ms}
        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: check token" in captured.out
        assert "token expired" in captured.out

    @patch("ralph.Sandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.Sandbox.apply_network_policy")
    @patch("ralph.Sandbox._docker_sandbox_create")
    @patch("ralph.Sandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", return_value=True)
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_cleans_up_sandbox_on_failure(self, mock_time, mock_read,
                                          mock_ensure_proxy, mock_health,
                                          mock_img, mock_create, mock_policy,
                                          mock_run, mock_stop, mock_remove):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        # proxy reachable fails, which causes failures but cleanup should still run
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr=""),     # curl proxy health (fail)
            MagicMock(returncode=1, stdout="", stderr=""),     # claude (fail)
            MagicMock(returncode=0, stdout="ok", stderr=""),   # curl google (not blocked)
        ]

        ralph.selftest("claude", "/fake/dotfiles")

        # Verify cleanup was called
        mock_remove.assert_called_with("agent-loop-selftest-claude")
        mock_stop.assert_called_once_with("claude")

    @patch("ralph.Sandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.Sandbox.apply_network_policy")
    @patch("ralph.Sandbox._docker_sandbox_create")
    @patch("ralph.Sandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", return_value=True)
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_reports_failed_checks(self, mock_time, mock_read,
                                   mock_ensure_proxy, mock_health,
                                   mock_img, mock_create, mock_policy,
                                   mock_run, mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        # proxy reachable ok, claude fails, network not blocked
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),   # curl proxy health
            MagicMock(returncode=1, stdout="", stderr="err"),  # claude fails
            MagicMock(returncode=0, stdout="ok", stderr=""),   # curl google (NOT blocked)
        ]

        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 1

        captured = capsys.readouterr()
        assert "FAIL: claude auth via proxy" in captured.out
        assert "FAIL: network isolation" in captured.out
        assert "2/8 checks failed" in captured.out

    @patch("ralph.Sandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.Sandbox.ensure_image", side_effect=RuntimeError("build failed"))
    @patch("ralph.proxy_health_check", return_value=True)
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_image_build_failure_aborts(self, mock_time, mock_read,
                                       mock_ensure_proxy, mock_health,
                                       mock_img, mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: build image" in captured.out
        assert "selftest aborted" in captured.out
        # Proxy should still be stopped (was started)
        mock_stop.assert_called_once_with("claude")

    @patch("ralph.Sandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.Sandbox.apply_network_policy")
    @patch("ralph.Sandbox._docker_sandbox_create",
           side_effect=subprocess.CalledProcessError(1, "docker"))
    @patch("ralph.Sandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", return_value=True)
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_sandbox_create_failure_aborts(self, mock_time, mock_read,
                                          mock_ensure_proxy, mock_health,
                                          mock_img, mock_create, mock_policy,
                                          mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: create sandbox" in captured.out
        assert "selftest aborted" in captured.out
        # Sandbox was never created, so remove_sandbox for cleanup shouldn't
        # be called (only the pre-cleanup remove_sandbox at the start was called)
        # But proxy should be stopped
        mock_stop.assert_called_once_with("claude")

    @patch("ralph.selftest", return_value=0)
    @patch("ralph.check_dependencies_prereq")
    @patch("ralph.sys.argv", ["ralph", "selftest"])
    def test_main_routes_selftest(self, mock_prereq, mock_selftest):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 0
        mock_selftest.assert_called_once_with("claude", mock_selftest.call_args[0][1])

    @patch("ralph.selftest", return_value=0)
    @patch("ralph.check_dependencies_prereq")
    @patch("ralph.sys.argv", ["ralph", "selftest", "--agent", "codex"])
    def test_main_routes_selftest_with_agent(self, mock_prereq, mock_selftest):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 0
        mock_selftest.assert_called_once_with("codex", mock_selftest.call_args[0][1])

    @patch("ralph.sys.argv", ["ralph", "selftest", "--badopt"])
    def test_main_selftest_rejects_unknown_option(self):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 2

    @patch("ralph.Sandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.Sandbox.apply_network_policy")
    @patch("ralph.Sandbox._docker_sandbox_create")
    @patch("ralph.Sandbox.ensure_project_image",
           return_value="agent-loop-sandbox-claude-myproject:vdeadbeef")
    @patch("ralph.Sandbox.find_project_config",
           return_value=("dependencies", "/proj/.agent-loop/dependencies"))
    @patch("ralph.Sandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", return_value=True)
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_project_image_check_with_dependencies(
            self, mock_time, mock_read, mock_ensure_proxy, mock_health,
            mock_img, mock_find_config, mock_proj_img, mock_create,
            mock_policy, mock_run, mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=28, stdout="", stderr=""),
        ]

        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 0

        captured = capsys.readouterr()
        assert "PASS: build project image" in captured.out
        assert "agent-loop-sandbox-claude-myproject:vdeadbeef" in captured.out
        assert "all 9 checks passed" in captured.out

    @patch("ralph.Sandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.Sandbox.apply_network_policy")
    @patch("ralph.Sandbox._docker_sandbox_create")
    @patch("ralph.Sandbox.ensure_project_image",
           return_value="agent-loop-sandbox-claude-myproject:vdeadbeef")
    @patch("ralph.Sandbox.find_project_config",
           return_value=("dockerfile", "/proj/.agent-loop/Dockerfile.sandbox"))
    @patch("ralph.Sandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", return_value=True)
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_project_image_check_with_dockerfile(
            self, mock_time, mock_read, mock_ensure_proxy, mock_health,
            mock_img, mock_find_config, mock_proj_img, mock_create,
            mock_policy, mock_run, mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=28, stdout="", stderr=""),
        ]

        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 0

        captured = capsys.readouterr()
        assert "PASS: build project image" in captured.out
        assert "all 9 checks passed" in captured.out

    @patch("ralph.Sandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.Sandbox.apply_network_policy")
    @patch("ralph.Sandbox._docker_sandbox_create")
    @patch("ralph.Sandbox.find_project_config", return_value=None)
    @patch("ralph.Sandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", return_value=True)
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_project_image_skipped_when_no_config(
            self, mock_time, mock_read, mock_ensure_proxy, mock_health,
            mock_img, mock_find_config, mock_create, mock_policy,
            mock_run, mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=28, stdout="", stderr=""),
        ]

        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 0

        captured = capsys.readouterr()
        assert "build project image" not in captured.out
        assert "all 8 checks passed" in captured.out

    @patch("ralph.Sandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.Sandbox.apply_network_policy")
    @patch("ralph.Sandbox._docker_sandbox_create")
    @patch("ralph.Sandbox.ensure_project_image",
           side_effect=RuntimeError("project build failed"))
    @patch("ralph.Sandbox.find_project_config",
           return_value=("dependencies", "/proj/.agent-loop/dependencies"))
    @patch("ralph.Sandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", return_value=True)
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_project_image_build_failure_reported(
            self, mock_time, mock_read, mock_ensure_proxy, mock_health,
            mock_img, mock_find_config, mock_proj_img, mock_create,
            mock_policy, mock_run, mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=28, stdout="", stderr=""),
        ]

        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 1

        captured = capsys.readouterr()
        assert "FAIL: build project image" in captured.out
        assert "project build failed" in captured.out
        assert "selftest aborted" in captured.out
        mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# ensure_proxy — docker wait timeout
# ---------------------------------------------------------------------------

class TestEnsureProxyStaleCleanup:
    @patch("ralph.proxy_health_check", side_effect=[False] + [True])
    @patch("ralph.start_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.time.sleep")
    def test_logs_and_removes_stale_container(self, mock_sleep, mock_run,
                                               mock_start, mock_health):
        """Stale proxy is logged, stopped, removed, then a new one starts."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="true\n"),  # inspect (stale exists)
            MagicMock(returncode=0),                    # docker logs
            MagicMock(returncode=0),                    # stop
            MagicMock(returncode=0),                    # rm
        ]

        result = ralph.ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert result == 18080
        mock_start.assert_called_once_with("claude", 18080, "/fake/dotfiles")


# ---------------------------------------------------------------------------
# poll_loop — exception handling
# ---------------------------------------------------------------------------

class TestPollLoopExceptionHandling:
    @patch("ralph.time.sleep")
    @patch("ralph.time.time")
    @patch("ralph.unblock_ready_specs")
    @patch("ralph.resolve_repo", return_value="owner/repo")
    def test_exception_marks_needs_attention_and_logs(self, mock_repo,
                                                      mock_unblock,
                                                      mock_time, mock_sleep,
                                                      capsys):
        """When process_issue raises, issue is labeled needs-attention."""
        # time.time() calls: deadline check, then post-sleep deadline, then timeout
        mock_time.side_effect = [0, 0, 0, 999]

        git = MagicMock()
        sandbox = MagicMock()
        gh = MagicMock()
        gh.issue_list.return_value = [42]

        with patch("ralph.process_issue", side_effect=RuntimeError("boom")):
            ralph.poll_loop(git, sandbox, gh, "claude", False, "sonnet",
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

    @patch("ralph.time.sleep")
    @patch("ralph.time.time")
    @patch("ralph.unblock_ready_specs")
    @patch("ralph.resolve_repo", return_value="owner/repo")
    def test_exception_in_label_update_does_not_crash(self, mock_repo,
                                                       mock_unblock,
                                                       mock_time, mock_sleep,
                                                       capsys):
        """If the needs-attention label update itself fails, the loop continues."""
        mock_time.side_effect = [0, 0, 0, 999]

        git = MagicMock()
        sandbox = MagicMock()
        gh = MagicMock()
        gh.issue_list.return_value = [42]
        gh.issue_edit.side_effect = RuntimeError("gh failed")

        with patch("ralph.process_issue", side_effect=RuntimeError("boom")):
            # Should not raise
            ralph.poll_loop(git, sandbox, gh, "claude", False, "sonnet",
                            "user", "user@test.com", 18080, 30, 1)
