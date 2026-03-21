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

    def test_length_is_12(self):
        h = ralph.Sandbox.content_hash("FROM a", "d")
        assert len(h) == 12


# ---------------------------------------------------------------------------
# Sandbox.image_tag
# ---------------------------------------------------------------------------

class TestSandboxImageTag:
    def test_format(self):
        tag = ralph.Sandbox.image_tag("claude", "abc123def456")
        assert tag == "agent-loop-sandbox-claude:vabc123def456"

    def test_custom_agent(self):
        tag = ralph.Sandbox.image_tag("codex", "xyz")
        assert tag == "agent-loop-sandbox-codex:vxyz"


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
