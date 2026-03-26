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
# load_sandbox_config
# ---------------------------------------------------------------------------

class TestLoadSandboxConfig:
    def test_default_when_no_config(self, tmp_path):
        """No .agent-loop/config.json returns docker default."""
        assert ralph.load_sandbox_config(str(tmp_path)) == {"type": "docker"}

    def test_default_when_no_agent_loop_dir(self, tmp_path):
        """No .agent-loop directory at all returns docker default."""
        assert ralph.load_sandbox_config(str(tmp_path)) == {"type": "docker"}

    def test_docker_type(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text('{"type": "docker"}')
        assert ralph.load_sandbox_config(str(tmp_path)) == {"type": "docker"}

    def test_tart_type_with_base_image(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        config = {
            "type": "tart",
            "base_image": "ghcr.io/cirruslabs/macos-sequoia-xcode:latest",
        }
        (config_dir / "config.json").write_text(json.dumps(config))
        result = ralph.load_sandbox_config(str(tmp_path))
        assert result["type"] == "tart"
        assert result["base_image"] == "ghcr.io/cirruslabs/macos-sequoia-xcode:latest"

    def test_tart_type_with_optional_fields(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        config = {
            "type": "tart",
            "base_image": "ghcr.io/cirruslabs/macos-sequoia-xcode:latest",
            "cpu": 4,
            "memory_gb": 8,
        }
        (config_dir / "config.json").write_text(json.dumps(config))
        result = ralph.load_sandbox_config(str(tmp_path))
        assert result["type"] == "tart"
        assert result["cpu"] == 4
        assert result["memory_gb"] == 8

    def test_missing_type_defaults_to_docker(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text('{"some_key": "value"}')
        result = ralph.load_sandbox_config(str(tmp_path))
        assert result["type"] == "docker"

    def test_unknown_type_raises_value_error(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text('{"type": "kubernetes"}')
        with pytest.raises(ValueError, match="unknown sandbox type"):
            ralph.load_sandbox_config(str(tmp_path))

    def test_malformed_json_raises(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text("{not valid json")
        with pytest.raises(json.JSONDecodeError):
            ralph.load_sandbox_config(str(tmp_path))


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

class TestProxyPortForAgent:
    def test_claude_default(self):
        assert ralph.proxy_port_for_agent("claude") == 18080

    def test_unknown_agent_uses_default(self):
        assert ralph.proxy_port_for_agent("unknown") == ralph.DEFAULT_PROXY_PORT


class TestProxyHealthCheck:
    @patch("ralph.urllib.request.urlopen")
    def test_returns_healthy_with_version(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"agent-loop-proxy ok v=abc123def456"
        mock_urlopen.return_value = mock_resp
        healthy, version = ralph.proxy_health_check(18080)
        assert healthy is True
        assert version == "abc123def456"

    @patch("ralph.urllib.request.urlopen")
    def test_returns_healthy_none_version_on_old_format(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"agent-loop-proxy ok"
        mock_urlopen.return_value = mock_resp
        healthy, version = ralph.proxy_health_check(18080)
        assert healthy is True
        assert version is None

    @patch("ralph.urllib.request.urlopen")
    def test_returns_unhealthy_on_non_200(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_urlopen.return_value = mock_resp
        healthy, version = ralph.proxy_health_check(18080)
        assert healthy is False
        assert version is None

    @patch("ralph.urllib.request.urlopen", side_effect=Exception("connection refused"))
    def test_returns_unhealthy_on_connection_error(self, mock_urlopen):
        healthy, version = ralph.proxy_health_check(18080)
        assert healthy is False
        assert version is None


class TestStartProxy:
    @patch("builtins.open", MagicMock())
    @patch("ralph.subprocess.Popen")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_launches_python3_with_proxy_script(self, mock_time, mock_read, mock_popen):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test-token", "expiresAt": future_ms}
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        result = ralph.start_proxy("claude", 18080, "/fake/dotfiles")
        assert result is mock_proc

        # Verify python3 proxy.py command
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "python3"
        assert cmd[1].endswith("proxy.py")
        # Verify env vars
        env = mock_popen.call_args[1]["env"]
        assert env["LISTEN_PORT"] == "18080"
        assert "PID_FILE" in env
        # Verify token piped via stdin
        mock_proc.stdin.write.assert_called_once_with(b"sk-test-token\n")
        mock_proc.stdin.close.assert_called_once()

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

    @patch("builtins.open", MagicMock())
    @patch("ralph.subprocess.Popen", side_effect=OSError("python3 not found"))
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_popen_failure_raises(self, mock_time, mock_read, mock_popen):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        with pytest.raises(OSError):
            ralph.start_proxy("claude", 18080, "/fake/dotfiles")


class TestStopProxy:
    @patch("ralph.os.kill")
    @patch("builtins.open", MagicMock(return_value=io.StringIO("12345")))
    def test_sends_sigterm_to_pid(self, mock_kill):
        ralph.stop_proxy("claude")
        mock_kill.assert_called_once_with(12345, ralph.signal.SIGTERM)

    def test_no_error_when_pid_file_missing(self):
        # Should silently handle missing PID file
        ralph.stop_proxy("nonexistent-agent-999")

    @patch("ralph.os.kill", side_effect=ProcessLookupError)
    @patch("builtins.open", MagicMock(return_value=io.StringIO("99999")))
    def test_no_error_when_process_gone(self, mock_kill):
        # Should silently handle already-dead process
        ralph.stop_proxy("claude")


class TestProxyKeepalive:
    @patch("ralph.urllib.request.urlopen")
    def test_pings_health_endpoint(self, mock_urlopen):
        stop = ralph.start_proxy_keepalive(18080, interval=0.05)
        try:
            time.sleep(0.15)
        finally:
            stop.set()
        assert mock_urlopen.call_count >= 2
        url = mock_urlopen.call_args[0][0]
        assert "localhost:18080/health" in url

    @patch("ralph.urllib.request.urlopen", side_effect=Exception("refused"))
    def test_continues_on_error(self, mock_urlopen):
        stop = ralph.start_proxy_keepalive(18080, interval=0.05)
        try:
            time.sleep(0.15)
        finally:
            stop.set()
        # Should have kept pinging despite errors
        assert mock_urlopen.call_count >= 2

    @patch("ralph.urllib.request.urlopen")
    def test_stops_when_event_set(self, mock_urlopen):
        stop = ralph.start_proxy_keepalive(18080, interval=0.05)
        time.sleep(0.1)
        stop.set()
        count_at_stop = mock_urlopen.call_count
        time.sleep(0.15)
        # Should not have made significantly more calls after stop
        assert mock_urlopen.call_count <= count_at_stop + 1


class TestEnsureProxy:
    @patch("ralph.compute_proxy_version", return_value="abc123def456")
    @patch("ralph.proxy_health_check", return_value=(True, "abc123def456"))
    def test_reuses_healthy_current_proxy(self, mock_health, mock_version):
        result = ralph.ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert result == 18080
        mock_health.assert_called_once_with(18080)

    @patch("ralph.compute_proxy_version", return_value="newversion123")
    @patch("ralph.proxy_health_check", return_value=(True, "oldversion456"))
    def test_reuses_outdated_proxy_with_warning(self, mock_health, mock_version, capsys):
        result = ralph.ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert result == 18080
        captured = capsys.readouterr()
        assert "outdated" in captured.out

    @patch("ralph.proxy_health_check", side_effect=[(False, None)] + [(True, "abc123")])
    @patch("ralph.start_proxy")
    @patch("ralph.time.sleep")
    def test_starts_new_when_none_running(self, mock_sleep, mock_start, mock_health):
        result = ralph.ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert result == 18080
        mock_start.assert_called_once_with("claude", 18080, "/fake/dotfiles")

    @patch("ralph.os.path.isfile", return_value=False)
    @patch("ralph.stop_proxy")
    @patch("ralph.proxy_health_check", return_value=(False, None))
    @patch("ralph.start_proxy")
    @patch("ralph.time.sleep")
    def test_exits_when_proxy_fails_to_become_healthy(self, mock_sleep, mock_start,
                                                       mock_health, mock_stop,
                                                       mock_isfile):
        with pytest.raises(SystemExit) as exc_info:
            ralph.ensure_proxy("claude", 18080, "/fake/dotfiles")
        assert exc_info.value.code == 1
        mock_stop.assert_called_once_with("claude")



# ---------------------------------------------------------------------------
# SandboxBackend
# ---------------------------------------------------------------------------

class TestSandboxBackend:
    """SandboxBackend interface methods raise NotImplementedError."""

    def test_proxy_host_raises(self):
        with pytest.raises(NotImplementedError):
            ralph.SandboxBackend().proxy_host()

    def test_ensure_image_raises(self):
        with pytest.raises(NotImplementedError):
            ralph.SandboxBackend().ensure_image("claude")

    def test_ensure_sandbox_raises(self):
        with pytest.raises(NotImplementedError):
            ralph.SandboxBackend().ensure_sandbox("claude", "main", "/work")

    def test_setup_git_config_raises(self):
        with pytest.raises(NotImplementedError):
            ralph.SandboxBackend().setup_git_config("name", "user", "email")

    def test_run_iteration_raises(self):
        with pytest.raises(NotImplementedError):
            ralph.SandboxBackend().run_iteration("name", "spec", "model")

    def test_preflight_backend_checks_raises(self):
        with pytest.raises(NotImplementedError):
            ralph.SandboxBackend()._preflight_backend_checks("name")

    def test_cleanup_sandbox_raises(self):
        with pytest.raises(NotImplementedError):
            ralph.SandboxBackend().cleanup_sandbox("agent", "branch")

    def test_prune_sandboxes_raises(self):
        with pytest.raises(NotImplementedError):
            ralph.SandboxBackend().prune_sandboxes("agent")

    def test_remove_sandbox_raises(self):
        with pytest.raises(NotImplementedError):
            ralph.SandboxBackend().remove_sandbox("name")

    def test_check_prerequisites_raises(self):
        with pytest.raises(NotImplementedError):
            ralph.SandboxBackend().check_prerequisites()

    def test_check_in_sync_raises(self):
        with pytest.raises(NotImplementedError):
            ralph.SandboxBackend().check_in_sync("name", "/work", None)

    def test_reset_to_host_raises(self):
        with pytest.raises(NotImplementedError):
            ralph.SandboxBackend().reset_to_host("name", "/work", None)

    def test_sync_to_host_raises(self):
        with pytest.raises(NotImplementedError):
            ralph.SandboxBackend().sync_to_host("name", "abc", "def", "/work")

    def test_sandbox_name_shared(self):
        """sandbox_name is shared logic, not abstract."""
        assert ralph.SandboxBackend.sandbox_name("claude", "fix-auth") == "agent-loop-claude-fix-auth"

    def test_docker_sandbox_inherits_sandbox_name(self):
        """DockerSandbox inherits sandbox_name from SandboxBackend."""
        assert ralph.DockerSandbox.sandbox_name("claude", "fix-auth") == "agent-loop-claude-fix-auth"


# ---------------------------------------------------------------------------
# create_sandbox_backend factory
# ---------------------------------------------------------------------------

class TestCreateSandboxBackend:
    def test_docker_returns_docker_sandbox(self):
        backend = ralph.create_sandbox_backend("docker", "/dotfiles")
        assert isinstance(backend, ralph.DockerSandbox)
        assert backend.dotfiles_dir == "/dotfiles"

    def test_tart_returns_tart_sandbox(self):
        backend = ralph.create_sandbox_backend(
            "tart", "/dotfiles",
            base_image="ghcr.io/cirruslabs/macos-sequoia-xcode:latest")
        assert isinstance(backend, ralph.TartSandbox)
        assert backend.dotfiles_dir == "/dotfiles"
        assert backend.base_image == "ghcr.io/cirruslabs/macos-sequoia-xcode:latest"

    def test_tart_reads_dependencies_from_project_dir(self, tmp_path):
        agent_loop = tmp_path / ".agent-loop"
        agent_loop.mkdir()
        (agent_loop / "dependencies").write_text("brew install jq\n")
        backend = ralph.create_sandbox_backend(
            "tart", "/dotfiles",
            base_image="img:latest", project_dir=str(tmp_path))
        assert backend.dependencies_content == "brew install jq\n"

    def test_tart_no_dependencies_file(self, tmp_path):
        backend = ralph.create_sandbox_backend(
            "tart", "/dotfiles",
            base_image="img:latest", project_dir=str(tmp_path))
        assert backend.dependencies_content == ""

    def test_tart_explicit_dependencies_not_overridden(self, tmp_path):
        """If dependencies_content is passed explicitly, don't read from file."""
        agent_loop = tmp_path / ".agent-loop"
        agent_loop.mkdir()
        (agent_loop / "dependencies").write_text("from file\n")
        backend = ralph.create_sandbox_backend(
            "tart", "/dotfiles",
            base_image="img:latest", project_dir=str(tmp_path),
            dependencies_content="explicit\n")
        assert backend.dependencies_content == "explicit\n"

    def test_unknown_type_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown sandbox type 'podman'"):
            ralph.create_sandbox_backend("podman", "/dotfiles")

    def test_kwargs_passed_through(self):
        """Extra kwargs don't break DockerSandbox creation."""
        backend = ralph.create_sandbox_backend(
            "docker", "/dotfiles", base_image="foo", cpu=4)
        assert isinstance(backend, ralph.DockerSandbox)


# ---------------------------------------------------------------------------
# TartSandbox._template_name
# ---------------------------------------------------------------------------

class TestTartTemplateName:
    def _make(self, **kwargs):
        config = {"base_image": "img:latest", "dependencies_content": ""}
        config.update(kwargs)
        return ralph.TartSandbox("/dotfiles", config=config)

    def test_deterministic(self):
        t1 = self._make()
        t2 = self._make()
        assert t1._template_name("claude") == t2._template_name("claude")

    def test_format(self):
        name = self._make()._template_name("claude")
        assert name.startswith("agent-loop-template-claude-")
        # Hash part is 12 hex chars
        hash_part = name.split("-")[-1]
        assert len(hash_part) == 12
        assert all(c in "0123456789abcdef" for c in hash_part)

    def test_changes_with_base_image(self):
        n1 = self._make(base_image="img:v1")._template_name("claude")
        n2 = self._make(base_image="img:v2")._template_name("claude")
        assert n1 != n2

    def test_changes_with_dependencies(self):
        n1 = self._make(dependencies_content="brew install jq")._template_name("claude")
        n2 = self._make(dependencies_content="brew install yq")._template_name("claude")
        assert n1 != n2

    def test_changes_with_agent(self):
        t = self._make()
        assert t._template_name("claude") != t._template_name("cursor")


# ---------------------------------------------------------------------------
# TartSandbox._list_vms
# ---------------------------------------------------------------------------

class TestTartListVms:
    def _make(self):
        return ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})

    def setup_method(self):
        self._saved_cache = ralph.TartSandbox._vm_list_cache
        ralph.TartSandbox._vm_list_cache = (0, [])

    def teardown_method(self):
        ralph.TartSandbox._vm_list_cache = self._saved_cache

    @patch("subprocess.run")
    def test_parses_json(self, mock_run):
        vms = [{"Name": "vm1", "State": "Running"}, {"Name": "vm2", "State": "Stopped"}]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(vms))
        result = self._make()._list_vms()
        assert result == vms
        mock_run.assert_called_once_with(
            ["tart", "list", "--format", "json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )

    @patch("subprocess.run")
    def test_failure_returns_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert self._make()._list_vms() == []

    @patch("subprocess.run")
    def test_invalid_json_returns_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        assert self._make()._list_vms() == []


# ---------------------------------------------------------------------------
# TartSandbox._list_vms caching
# ---------------------------------------------------------------------------

class TestTartListVmsCache:
    def _make(self):
        return ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})

    def setup_method(self):
        self._saved_cache = ralph.TartSandbox._vm_list_cache
        ralph.TartSandbox._vm_list_cache = (0, [])

    def teardown_method(self):
        ralph.TartSandbox._vm_list_cache = self._saved_cache

    @patch("subprocess.run")
    def test_second_call_within_ttl_uses_cache(self, mock_run):
        vms = [{"Name": "vm1", "State": "Running"}]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(vms))
        t = self._make()
        result1 = t._list_vms()
        result2 = t._list_vms()
        assert result1 == vms
        assert result2 == vms
        # Only one subprocess call — second was cached
        assert mock_run.call_count == 1

    @patch("time.monotonic")
    @patch("subprocess.run")
    def test_call_after_ttl_fetches_fresh(self, mock_run, mock_time):
        vms_old = [{"Name": "vm1", "State": "Running"}]
        vms_new = [{"Name": "vm1", "State": "Stopped"}]
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(vms_old)),
            MagicMock(returncode=0, stdout=json.dumps(vms_new)),
        ]
        mock_time.side_effect = [10.0, 13.0]  # 3s apart, > 2s TTL
        t = self._make()
        result1 = t._list_vms()
        result2 = t._list_vms()
        assert result1 == vms_old
        assert result2 == vms_new
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_cache_shared_across_instances(self, mock_run):
        vms = [{"Name": "vm1", "State": "Running"}]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(vms))
        t1 = self._make()
        t2 = self._make()
        t1._list_vms()
        result = t2._list_vms()
        assert result == vms
        # Only one subprocess call — t2 used t1's cache
        assert mock_run.call_count == 1


# ---------------------------------------------------------------------------
# TartSandbox._check_vm_limit
# ---------------------------------------------------------------------------

class TestTartCheckVmLimit:
    def _make(self):
        return ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})

    @patch.object(ralph.TartSandbox, "_running_vm_count", return_value=0)
    def test_zero_running_passes(self, _):
        self._make()._check_vm_limit()  # should not raise

    @patch.object(ralph.TartSandbox, "_running_vm_count", return_value=1)
    def test_one_running_passes(self, _):
        self._make()._check_vm_limit()  # should not raise

    @patch.object(ralph.TartSandbox, "_running_vm_count", return_value=2)
    def test_two_running_raises(self, _):
        with pytest.raises(RuntimeError, match="cannot start VM"):
            self._make()._check_vm_limit()

    @patch.object(ralph.TartSandbox, "_running_vm_count", return_value=2)
    def test_error_message_includes_count(self, _):
        with pytest.raises(RuntimeError, match="2 macOS VMs already running"):
            self._make()._check_vm_limit()

    @patch.object(ralph.TartSandbox, "_running_vm_count", return_value=3)
    def test_three_running_raises(self, _):
        with pytest.raises(RuntimeError, match="3 macOS VMs already running"):
            self._make()._check_vm_limit()


# ---------------------------------------------------------------------------
# TartSandbox._wait_for_guest_agent
# ---------------------------------------------------------------------------

class TestTartWaitForGuestAgent:
    def _make(self):
        return ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_succeeds_first_try(self, mock_run, _sleep):
        mock_run.return_value = MagicMock(returncode=0)
        self._make()._wait_for_guest_agent("test-vm", timeout=10)
        mock_run.assert_called_once_with(
            ["tart", "exec", "test-vm", "--", "echo", "ok"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )

    @patch("time.sleep")
    @patch("time.time")
    @patch("subprocess.run")
    def test_succeeds_after_retries(self, mock_run, mock_time, _sleep):
        # First two calls fail, third succeeds
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=1),
            MagicMock(returncode=0),
        ]
        # time.time() returns values within the timeout window
        mock_time.side_effect = [0, 2, 4, 6]
        self._make()._wait_for_guest_agent("test-vm", timeout=120)
        assert mock_run.call_count == 3

    @patch("time.sleep")
    @patch("time.time")
    @patch("subprocess.run")
    def test_timeout_raises(self, mock_run, mock_time, _sleep):
        mock_run.return_value = MagicMock(returncode=1)
        # Time jumps past deadline
        mock_time.side_effect = [0, 130]
        with pytest.raises(RuntimeError, match="guest agent not responding"):
            self._make()._wait_for_guest_agent("test-vm", timeout=120)

    @patch("time.sleep")
    @patch("time.time")
    @patch("subprocess.run")
    def test_timeout_includes_vm_name(self, mock_run, mock_time, _sleep):
        mock_run.return_value = MagicMock(returncode=1)
        mock_time.side_effect = [0, 200]
        with pytest.raises(RuntimeError, match="test-vm"):
            self._make()._wait_for_guest_agent("test-vm", timeout=120)


# ---------------------------------------------------------------------------
# TartSandbox.ensure_image
# ---------------------------------------------------------------------------

class TestTartEnsureImage:
    def _make(self, deps=""):
        config = {"base_image": "img:latest", "dependencies_content": deps}
        return ralph.TartSandbox("/dotfiles", config=config)

    @patch.object(ralph.TartSandbox, "_vm_exists", return_value=True)
    def test_cached_template_reused(self, _exists):
        t = self._make()
        name = t.ensure_image("claude")
        assert name == t._template_name("claude")

    @patch("subprocess.Popen")
    @patch("subprocess.run")
    @patch.object(ralph.TartSandbox, "_wait_for_guest_agent")
    @patch.object(ralph.TartSandbox, "_check_vm_limit")
    @patch.object(ralph.TartSandbox, "_vm_exists", return_value=False)
    def test_clones_without_deps(self, _exists, _limit, _wait, mock_run, _popen):
        t = self._make(deps="")
        name = t.ensure_image("claude")
        # Should call tart clone
        clone_call = mock_run.call_args_list[0]
        assert clone_call[0][0] == ["tart", "clone", "img:latest", name]
        # Should NOT start VM (no deps)
        _popen.assert_not_called()
        _wait.assert_not_called()

    @patch("subprocess.Popen")
    @patch("subprocess.run")
    @patch.object(ralph.TartSandbox, "_wait_for_guest_agent")
    @patch.object(ralph.TartSandbox, "_check_vm_limit")
    @patch.object(ralph.TartSandbox, "_vm_exists", return_value=False)
    def test_installs_deps(self, _exists, _limit, _wait, mock_run, mock_popen):
        vm_proc = MagicMock()
        mock_popen.return_value = vm_proc
        t = self._make(deps="brew install jq\n")
        name = t.ensure_image("claude")
        # Should start VM
        mock_popen.assert_called_once_with(
            ["tart", "run", name, "--no-graphics"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Should exec dependencies
        exec_calls = [c for c in mock_run.call_args_list
                      if c[0][0][:3] == ["tart", "exec", "-i"]]
        assert len(exec_calls) == 1
        assert exec_calls[0].kwargs["input"] == "brew install jq\n"
        # Should stop VM
        stop_calls = [c for c in mock_run.call_args_list
                      if c[0][0][:2] == ["tart", "stop"]]
        assert len(stop_calls) == 1
        vm_proc.wait.assert_called_once()

    @patch("subprocess.Popen")
    @patch("subprocess.run")
    @patch.object(ralph.TartSandbox, "_wait_for_guest_agent")
    @patch.object(ralph.TartSandbox, "_check_vm_limit")
    @patch.object(ralph.TartSandbox, "_vm_exists", side_effect=[True, False])
    def test_force_rebuild_deletes_old(self, _exists, _limit, _wait, mock_run, _popen):
        t = self._make(deps="")
        name = t.ensure_image("claude", force_rebuild=True)
        # First call should be tart delete
        delete_call = mock_run.call_args_list[0]
        assert delete_call[0][0] == ["tart", "delete", name]
        # Then tart clone
        clone_call = mock_run.call_args_list[1]
        assert clone_call[0][0] == ["tart", "clone", "img:latest", name]

    @patch("subprocess.Popen")
    @patch("subprocess.run")
    @patch.object(ralph.TartSandbox, "_wait_for_guest_agent",
                  side_effect=RuntimeError("timeout"))
    @patch.object(ralph.TartSandbox, "_check_vm_limit")
    @patch.object(ralph.TartSandbox, "_vm_exists", return_value=False)
    def test_stops_vm_on_failure(self, _exists, _limit, _wait, mock_run, mock_popen):
        """VM is stopped even if dependency install fails."""
        vm_proc = MagicMock()
        mock_popen.return_value = vm_proc
        t = self._make(deps="brew install jq\n")
        with pytest.raises(RuntimeError, match="timeout"):
            t.ensure_image("claude")
        # Should still stop VM
        stop_calls = [c for c in mock_run.call_args_list
                      if len(c[0][0]) >= 2 and c[0][0][:2] == ["tart", "stop"]]
        assert len(stop_calls) == 1
        vm_proc.wait.assert_called_once()


# ---------------------------------------------------------------------------
# TartSandbox.exec_output
# ---------------------------------------------------------------------------

class TestTartExecOutput:
    @patch("subprocess.run")
    def test_returns_stdout_on_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="  hello world  \n")
        result = ralph.TartSandbox.exec_output("test-vm", "echo", "hello")
        assert result == "hello world"
        mock_run.assert_called_once_with(
            ["tart", "exec", "test-vm", "--", "echo", "hello"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )

    @patch("subprocess.run")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="error stuff")
        result = ralph.TartSandbox.exec_output("test-vm", "false")
        assert result == ""


# ---------------------------------------------------------------------------
# TartSandbox.ensure_sandbox
# ---------------------------------------------------------------------------

class TestTartEnsureSandbox:
    def setup_method(self):
        self._saved = ralph.TartSandbox._vm_procs.copy()
        ralph.TartSandbox._vm_procs.clear()

    def teardown_method(self):
        ralph.TartSandbox._vm_procs.clear()
        ralph.TartSandbox._vm_procs.update(self._saved)

    def _make(self, **kwargs):
        config = {"base_image": "img:latest", "dependencies_content": ""}
        config.update(kwargs)
        return ralph.TartSandbox("/dotfiles", config=config)

    @patch.object(ralph.TartSandbox, "_wait_for_guest_agent")
    @patch("subprocess.Popen")
    @patch("subprocess.run")
    @patch.object(ralph.TartSandbox, "ensure_image", return_value="template-name")
    @patch.object(ralph.TartSandbox, "_check_vm_limit")
    @patch.object(ralph.TartSandbox, "_vm_state", return_value=None)
    def test_creates_new_vm(self, _state, _limit, _ensure, mock_run, mock_popen, _wait):
        mock_popen.return_value = MagicMock()
        t = self._make()
        name = t.ensure_sandbox("claude", "my-branch", "/work/my-branch")
        assert name == ralph.TartSandbox.sandbox_name("claude", "my-branch")

        # Should clone from template
        clone_call = mock_run.call_args_list[0]
        assert clone_call[0][0] == ["tart", "clone", "template-name", name]

        # Should start VM with directory sharing
        mock_popen.assert_called_once()
        popen_args = mock_popen.call_args[0][0]
        assert popen_args[:4] == ["tart", "run", name, "--no-graphics"]
        assert f"--dir=workspace:/work/my-branch" in popen_args

    @patch.object(ralph.TartSandbox, "_vm_state", return_value="Running")
    def test_reuses_running_vm(self, _state):
        t = self._make()
        name = t.ensure_sandbox("claude", "my-branch", "/work/my-branch")
        assert name == ralph.TartSandbox.sandbox_name("claude", "my-branch")

    @patch.object(ralph.TartSandbox, "_wait_for_guest_agent")
    @patch("subprocess.Popen")
    @patch("subprocess.run")
    @patch.object(ralph.TartSandbox, "ensure_image", return_value="template-name")
    @patch.object(ralph.TartSandbox, "_check_vm_limit")
    @patch.object(ralph.TartSandbox, "_vm_state", return_value="Stopped")
    def test_deletes_stopped_vm_and_recreates(self, _state, _limit, _ensure,
                                               mock_run, mock_popen, _wait):
        mock_popen.return_value = MagicMock()
        t = self._make()
        name = t.ensure_sandbox("claude", "my-branch", "/work/my-branch")

        # First call should be tart delete
        delete_call = mock_run.call_args_list[0]
        assert delete_call[0][0] == ["tart", "delete", name]
        # Then clone
        clone_call = mock_run.call_args_list[1]
        assert clone_call[0][0] == ["tart", "clone", "template-name", name]

    @patch.object(ralph.TartSandbox, "_vm_state", return_value=None)
    @patch.object(ralph.TartSandbox, "_check_vm_limit",
                  side_effect=RuntimeError("too many VMs"))
    def test_vm_limit_check(self, _limit, _state):
        t = self._make()
        with pytest.raises(RuntimeError, match="too many VMs"):
            t.ensure_sandbox("claude", "my-branch", "/work/my-branch")

    @patch.object(ralph.TartSandbox, "_wait_for_guest_agent")
    @patch("subprocess.Popen")
    @patch("subprocess.run")
    @patch.object(ralph.TartSandbox, "ensure_image", return_value="template-name")
    @patch.object(ralph.TartSandbox, "_check_vm_limit")
    @patch.object(ralph.TartSandbox, "_vm_state", return_value=None)
    def test_stores_vm_proc(self, _state, _limit, _ensure, _run, mock_popen, _wait):
        vm_proc = MagicMock()
        mock_popen.return_value = vm_proc
        t = self._make()
        name = t.ensure_sandbox("claude", "my-branch", "/work/my-branch")
        assert t._vm_procs[name] is vm_proc


# ---------------------------------------------------------------------------
# TartSandbox.setup_git_config
# ---------------------------------------------------------------------------

class TestTartSetupGitConfig:
    @patch("subprocess.run")
    def test_configures_user_email_safedir(self, mock_run):
        t = ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})
        t.setup_git_config("test-vm", "Test User", "test@example.com")
        assert mock_run.call_count == 3

        calls = mock_run.call_args_list
        # user.name
        assert calls[0][0][0] == [
            "tart", "exec", "test-vm", "--",
            "git", "config", "--global", "user.name", "Test User"]
        # user.email
        assert calls[1][0][0] == [
            "tart", "exec", "test-vm", "--",
            "git", "config", "--global", "user.email", "test@example.com"]
        # safe.directory
        assert calls[2][0][0] == [
            "tart", "exec", "test-vm", "--",
            "git", "config", "--global", "--add", "safe.directory", "*"]


# ---------------------------------------------------------------------------
# TartSandbox.run_iteration
# ---------------------------------------------------------------------------

class TestTartRunIteration:
    def _make(self):
        return ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})

    @patch("subprocess.run")
    def test_writes_spec_runs_claude_reads_spec(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # tee (write spec)
            MagicMock(returncode=0),  # bash -c claude
            MagicMock(returncode=0, stdout="updated spec"),  # cat (read spec)
        ]
        t = self._make()
        rc, spec = t.run_iteration("test-vm", "original spec", "sonnet",
                                   {"KEY": "val"})
        assert rc == 0
        assert spec == "updated spec"

        # Check write command
        write_call = mock_run.call_args_list[0]
        assert write_call[0][0] == [
            "tart", "exec", "-i", "test-vm", "--", "tee", "/tmp/spec.md"]
        assert write_call.kwargs["input"] == "original spec"

        # Check claude command uses bash -c with env vars
        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        assert cmd[:5] == ["tart", "exec", "test-vm", "--", "bash"]
        assert cmd[5] == "-c"
        bash_cmd = cmd[6]
        assert "env KEY='val'" in bash_cmd or "env KEY=val" in bash_cmd
        assert "--model" in bash_cmd
        assert "--dangerously-skip-permissions" in bash_cmd
        assert f"cd '{ralph.TartSandbox.SHARED_DIR}'" in bash_cmd

        # Check read command
        read_call = mock_run.call_args_list[2]
        assert read_call[0][0] == [
            "tart", "exec", "test-vm", "--", "cat", "/tmp/spec.md"]

    @patch("subprocess.run")
    def test_write_failure_returns_original_spec(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        t = self._make()
        rc, spec = t.run_iteration("test-vm", "original spec", "sonnet")
        assert rc == 1
        assert spec == "original spec"

    @patch("subprocess.run")
    def test_read_failure_returns_original_spec(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # tee
            MagicMock(returncode=0),  # claude
            MagicMock(returncode=1, stdout=""),  # cat fails
        ]
        t = self._make()
        rc, spec = t.run_iteration("test-vm", "original spec", "sonnet")
        assert rc == 0
        assert spec == "original spec"

    @patch("subprocess.run")
    def test_no_env_vars(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # tee
            MagicMock(returncode=0),  # claude
            MagicMock(returncode=0, stdout="spec"),  # cat
        ]
        t = self._make()
        t.run_iteration("test-vm", "spec", "sonnet")
        claude_call = mock_run.call_args_list[1]
        bash_cmd = claude_call[0][0][6]
        assert "env " not in bash_cmd or bash_cmd.startswith("cd ")

    @patch("subprocess.run")
    def test_env_vars_shell_escaped(self, mock_run):
        """Env vars with special chars are properly shell-escaped."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # tee
            MagicMock(returncode=0),  # claude
            MagicMock(returncode=0, stdout="spec"),  # cat
        ]
        t = self._make()
        t.run_iteration("test-vm", "spec", "sonnet",
                        {"KEY": "val with spaces"})
        claude_call = mock_run.call_args_list[1]
        bash_cmd = claude_call[0][0][6]
        assert "KEY='val with spaces'" in bash_cmd


# ---------------------------------------------------------------------------
# TartSandbox.proxy_host
# ---------------------------------------------------------------------------

class TestTartProxyHost:
    def setup_method(self):
        self._saved = ralph.TartSandbox._vm_procs.copy()
        ralph.TartSandbox._vm_procs.clear()

    def teardown_method(self):
        ralph.TartSandbox._vm_procs.clear()
        ralph.TartSandbox._vm_procs.update(self._saved)

    def _make(self):
        return ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})

    @patch("subprocess.run")
    def test_gateway_from_vm(self, mock_run):
        """Parses gateway IP from route output inside VM."""
        route_output = (
            "   route to: default\n"
            "destination: default\n"
            "       mask: default\n"
            "    gateway: 192.168.64.1\n"
            "  interface: en0\n"
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=route_output)
        t = self._make()
        # Add a fake running VM proc
        proc = MagicMock()
        proc.poll.return_value = None  # still running
        t._vm_procs["test-vm"] = proc

        result = t.proxy_host()
        assert result == "192.168.64.1"

    @patch("subprocess.run")
    def test_fallback_to_en0(self, mock_run):
        """Falls back to ipconfig getifaddr en0 on host."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="10.0.0.5\n"),  # ipconfig
        ]
        t = self._make()
        # No running VMs
        result = t.proxy_host()
        assert result == "10.0.0.5"

    @patch("subprocess.run")
    def test_final_fallback(self, mock_run):
        """Falls back to well-known 192.168.64.1."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        t = self._make()
        result = t.proxy_host()
        assert result == "192.168.64.1"

    @patch("subprocess.run")
    def test_caches_result(self, mock_run):
        """proxy_host caches after first call."""
        mock_run.return_value = MagicMock(returncode=0, stdout="10.0.0.5\n")
        t = self._make()
        t.proxy_host()
        t.proxy_host()
        # ipconfig should only be called once
        assert mock_run.call_count == 1

    def test_cached_value_used_directly(self):
        """If _cached_proxy_host is set, no subprocess calls."""
        t = self._make()
        t._cached_proxy_host = "10.0.0.99"
        assert t.proxy_host() == "10.0.0.99"


# ---------------------------------------------------------------------------
# TartSandbox.cleanup_sandbox
# ---------------------------------------------------------------------------

class TestTartCleanupSandbox:
    def setup_method(self):
        self._saved = ralph.TartSandbox._vm_procs.copy()
        ralph.TartSandbox._vm_procs.clear()

    def teardown_method(self):
        ralph.TartSandbox._vm_procs.clear()
        ralph.TartSandbox._vm_procs.update(self._saved)

    @patch("subprocess.run")
    def test_stops_and_deletes(self, mock_run):
        t = ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})
        t.cleanup_sandbox("claude", "my-branch")

        calls = mock_run.call_args_list
        name = ralph.TartSandbox.sandbox_name("claude", "my-branch")
        # Stop
        assert calls[0][0][0] == ["tart", "stop", name]
        # Delete
        assert calls[1][0][0] == ["tart", "delete", name]

    @patch("subprocess.run")
    def test_waits_for_tracked_proc(self, mock_run):
        t = ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})
        name = ralph.TartSandbox.sandbox_name("claude", "my-branch")
        proc = MagicMock()
        t._vm_procs[name] = proc
        t.cleanup_sandbox("claude", "my-branch")
        proc.wait.assert_called_once()
        assert name not in t._vm_procs

    @patch("subprocess.run")
    def test_no_tracked_proc(self, mock_run):
        """Works fine even without a tracked proc."""
        t = ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})
        t.cleanup_sandbox("claude", "my-branch")  # should not raise


# ---------------------------------------------------------------------------
# TartSandbox.remove_sandbox
# ---------------------------------------------------------------------------

class TestTartRemoveSandbox:
    def setup_method(self):
        self._saved = ralph.TartSandbox._vm_procs.copy()
        ralph.TartSandbox._vm_procs.clear()

    def teardown_method(self):
        ralph.TartSandbox._vm_procs.clear()
        ralph.TartSandbox._vm_procs.update(self._saved)

    @patch("subprocess.run")
    def test_stops_and_deletes_by_name(self, mock_run):
        t = ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})
        t.remove_sandbox("some-vm")
        calls = mock_run.call_args_list
        assert calls[0][0][0] == ["tart", "stop", "some-vm"]
        assert calls[1][0][0] == ["tart", "delete", "some-vm"]

    @patch("subprocess.run")
    def test_cleans_up_tracked_proc(self, mock_run):
        t = ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})
        proc = MagicMock()
        t._vm_procs["some-vm"] = proc
        t.remove_sandbox("some-vm")
        proc.wait.assert_called_once()
        assert "some-vm" not in t._vm_procs


# ---------------------------------------------------------------------------
# TartSandbox.prune_sandboxes
# ---------------------------------------------------------------------------

class TestTartPruneSandboxes:
    @patch("subprocess.run")
    @patch.object(ralph.TartSandbox, "_list_vms")
    def test_removes_stopped_non_template_vms(self, mock_list, mock_run):
        mock_list.return_value = [
            {"Name": "agent-loop-claude-old-branch", "State": "Stopped"},
            {"Name": "agent-loop-claude-active", "State": "Running"},
            {"Name": "agent-loop-template-claude-abc123", "State": "Stopped"},
            {"Name": "other-vm", "State": "Stopped"},
        ]
        t = ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})
        pruned = t.prune_sandboxes("claude")
        assert pruned == ["agent-loop-claude-old-branch"]

    @patch("subprocess.run")
    @patch.object(ralph.TartSandbox, "_list_vms")
    def test_keeps_running_vms(self, mock_list, mock_run):
        mock_list.return_value = [
            {"Name": "agent-loop-claude-active", "State": "Running"},
        ]
        t = ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})
        pruned = t.prune_sandboxes("claude")
        assert pruned == []
        mock_run.assert_not_called()

    @patch("subprocess.run")
    @patch.object(ralph.TartSandbox, "_list_vms")
    def test_skips_templates(self, mock_list, mock_run):
        mock_list.return_value = [
            {"Name": "agent-loop-template-claude-abc123", "State": "Stopped"},
        ]
        t = ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})
        pruned = t.prune_sandboxes("claude")
        assert pruned == []
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# TartSandbox.preflight_check
# ---------------------------------------------------------------------------

class TestTartPreflightCheck:
    def _make(self):
        return ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})

    @patch("subprocess.run")
    @patch("ralph.proxy_health_check", return_value=(True, ""))
    @patch("ralph.read_token_from_keychain",
           return_value={"expiresAt": int(time.time() * 1000) + 600000})
    def test_all_pass(self, _token, _proxy, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        t = self._make()
        failures = t.preflight_check("test-vm", "claude", 18080)
        assert failures == []

    @patch("subprocess.run")
    @patch("ralph.proxy_health_check", return_value=(True, ""))
    @patch("ralph.read_token_from_keychain", return_value=None)
    def test_token_missing(self, _token, _proxy, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        t = self._make()
        failures = t.preflight_check("test-vm", "claude", 18080)
        assert any("no token found" in f for f in failures)

    @patch("subprocess.run")
    @patch("ralph.proxy_health_check", return_value=(True, ""))
    @patch("ralph.read_token_from_keychain",
           return_value={"expiresAt": 0})
    def test_token_expired(self, _token, _proxy, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        t = self._make()
        failures = t.preflight_check("test-vm", "claude", 18080)
        assert any("token expired" in f for f in failures)

    @patch("subprocess.run")
    @patch("ralph.proxy_health_check", return_value=(False, ""))
    @patch("ralph.read_token_from_keychain",
           return_value={"expiresAt": int(time.time() * 1000) + 600000})
    def test_proxy_down(self, _token, _proxy, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        t = self._make()
        failures = t.preflight_check("test-vm", "claude", 18080)
        assert any("proxy not reachable" in f for f in failures)

    @patch("subprocess.run")
    @patch("ralph.proxy_health_check", return_value=(True, ""))
    @patch("ralph.read_token_from_keychain",
           return_value={"expiresAt": int(time.time() * 1000) + 600000})
    def test_vm_unresponsive(self, _token, _proxy, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        t = self._make()
        failures = t.preflight_check("test-vm", "claude", 18080)
        assert any("not responding" in f for f in failures)


# ---------------------------------------------------------------------------
# TartSandbox.sync_to_host
# ---------------------------------------------------------------------------

class TestTartSyncToHost:
    @patch("subprocess.run")
    def test_returns_true_when_commit_visible(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        t = ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})
        assert t.sync_to_host("test-vm", "abc", "def", "/work") is True
        mock_run.assert_called_once_with(
            ["git", "rev-parse", "--verify", "def"],
            cwd="/work", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )

    @patch("subprocess.run")
    def test_returns_false_when_commit_not_visible(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        t = ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})
        assert t.sync_to_host("test-vm", "abc", "def", "/work") is False


# ---------------------------------------------------------------------------
# TartSandbox.check_in_sync
# ---------------------------------------------------------------------------

class TestTartCheckInSync:
    def test_always_true(self):
        t = ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})
        assert t.check_in_sync("vm", "/work", MagicMock()) is True


# ---------------------------------------------------------------------------
# TartSandbox.reset_to_host
# ---------------------------------------------------------------------------

class TestTartResetToHost:
    def test_always_true(self):
        t = ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})
        assert t.reset_to_host("vm", "/work", MagicMock()) is True


# ---------------------------------------------------------------------------
# TartSandbox._atexit_stop_all
# ---------------------------------------------------------------------------

class TestTartAtexitStopAll:
    def setup_method(self):
        """Save and clear class-level _vm_procs before each test."""
        self._saved = ralph.TartSandbox._vm_procs.copy()
        ralph.TartSandbox._vm_procs.clear()

    def teardown_method(self):
        """Restore class-level _vm_procs after each test."""
        ralph.TartSandbox._vm_procs.clear()
        ralph.TartSandbox._vm_procs.update(self._saved)

    @patch("subprocess.run")
    def test_stops_each_tracked_vm(self, mock_run):
        proc1 = MagicMock()
        proc2 = MagicMock()
        ralph.TartSandbox._vm_procs["vm-one"] = proc1
        ralph.TartSandbox._vm_procs["vm-two"] = proc2

        ralph.TartSandbox._atexit_stop_all()

        # tart stop called for each VM
        stop_calls = [c for c in mock_run.call_args_list
                      if c[0][0][:2] == ["tart", "stop"]]
        stopped_names = {c[0][0][2] for c in stop_calls}
        assert stopped_names == {"vm-one", "vm-two"}

        # wait called for each proc
        proc1.wait.assert_called_once_with(timeout=10)
        proc2.wait.assert_called_once_with(timeout=10)

    @patch("subprocess.run")
    def test_clears_vm_procs_after_cleanup(self, mock_run):
        ralph.TartSandbox._vm_procs["vm-x"] = MagicMock()
        ralph.TartSandbox._atexit_stop_all()
        assert ralph.TartSandbox._vm_procs == {}

    @patch("subprocess.run")
    def test_handles_empty_vm_procs(self, mock_run):
        ralph.TartSandbox._atexit_stop_all()
        mock_run.assert_not_called()
        assert ralph.TartSandbox._vm_procs == {}

    @patch("subprocess.run", side_effect=OSError("no tart"))
    def test_suppresses_stop_errors(self, mock_run):
        proc = MagicMock()
        ralph.TartSandbox._vm_procs["vm-err"] = proc
        # Should not raise
        ralph.TartSandbox._atexit_stop_all()
        proc.wait.assert_called_once_with(timeout=10)
        assert ralph.TartSandbox._vm_procs == {}

    @patch("subprocess.run")
    def test_suppresses_wait_timeout(self, mock_run):
        proc = MagicMock()
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="tart", timeout=10)
        ralph.TartSandbox._vm_procs["vm-stuck"] = proc
        # Should not raise
        ralph.TartSandbox._atexit_stop_all()
        assert ralph.TartSandbox._vm_procs == {}

    def test_vm_procs_shared_across_instances(self):
        t1 = ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})
        t2 = ralph.TartSandbox("/dotfiles", config={"base_image": "img:latest"})
        t1._vm_procs["shared-vm"] = MagicMock()
        assert "shared-vm" in t2._vm_procs
        assert t1._vm_procs is t2._vm_procs
        assert t1._vm_procs is ralph.TartSandbox._vm_procs


# ---------------------------------------------------------------------------
# DockerSandbox.parse_base_image
# ---------------------------------------------------------------------------

class TestSandboxParseBaseImage:
    def test_extracts_from_line(self):
        content = "FROM docker/sandbox-templates:claude-code\nUSER root"
        assert ralph.DockerSandbox.parse_base_image(content) == "docker/sandbox-templates:claude-code"

    def test_returns_none_when_no_from(self):
        assert ralph.DockerSandbox.parse_base_image("RUN echo hi") is None

    def test_ignores_comment_lines(self):
        content = "# FROM fake:image\nFROM real:latest"
        assert ralph.DockerSandbox.parse_base_image(content) == "real:latest"

    def test_returns_final_stage_in_multistage(self):
        content = "FROM builder:latest AS build\nRUN make\nFROM runtime:slim"
        assert ralph.DockerSandbox.parse_base_image(content) == "runtime:slim"


# ---------------------------------------------------------------------------
# Sandbox.content_hash
# ---------------------------------------------------------------------------

class TestSandboxContentHash:
    def test_deterministic(self):
        h1 = ralph.DockerSandbox.content_hash("FROM a", "digest1")
        h2 = ralph.DockerSandbox.content_hash("FROM a", "digest1")
        assert h1 == h2

    def test_changes_when_dockerfile_changes(self):
        h1 = ralph.DockerSandbox.content_hash("FROM a\nRUN echo old", "digest1")
        h2 = ralph.DockerSandbox.content_hash("FROM a\nRUN echo new", "digest1")
        assert h1 != h2

    def test_changes_when_base_digest_changes(self):
        df = "FROM a\nRUN echo same"
        h1 = ralph.DockerSandbox.content_hash(df, "sha256:aaa")
        h2 = ralph.DockerSandbox.content_hash(df, "sha256:bbb")
        assert h1 != h2

    def test_length_is_8(self):
        h = ralph.DockerSandbox.content_hash("FROM a", "d")
        assert len(h) == 8


# ---------------------------------------------------------------------------
# Sandbox.image_tag
# ---------------------------------------------------------------------------

class TestSandboxImageTag:
    def test_format(self):
        tag = ralph.DockerSandbox.image_tag("claude", "abc123de")
        assert tag == "agent-loop-sandbox-claude:vabc123de"

    def test_custom_agent(self):
        tag = ralph.DockerSandbox.image_tag("codex", "xyz")
        assert tag == "agent-loop-sandbox-codex:vxyz"


# ---------------------------------------------------------------------------
# Sandbox.parse_dependencies
# ---------------------------------------------------------------------------

class TestSandboxParseDependencies:
    def test_basic_package_list(self):
        content = "openjdk-21-jdk\npython3-venv\nnodejs"
        assert ralph.DockerSandbox.parse_dependencies(content) == [
            "openjdk-21-jdk", "python3-venv", "nodejs"
        ]

    def test_comment_only_lines_skipped(self):
        content = "# This is a comment\npkg1\n# Another comment\npkg2"
        assert ralph.DockerSandbox.parse_dependencies(content) == ["pkg1", "pkg2"]

    def test_inline_comments_stripped(self):
        content = "pkg1 # this is a comment\npkg2 # another"
        assert ralph.DockerSandbox.parse_dependencies(content) == ["pkg1", "pkg2"]

    def test_blank_lines_skipped(self):
        content = "pkg1\n\n\npkg2\n\npkg3"
        assert ralph.DockerSandbox.parse_dependencies(content) == ["pkg1", "pkg2", "pkg3"]

    def test_whitespace_handling(self):
        content = "  pkg1  \n\tpkg2\t\n  pkg3  # comment  "
        assert ralph.DockerSandbox.parse_dependencies(content) == ["pkg1", "pkg2", "pkg3"]

    def test_empty_content(self):
        assert ralph.DockerSandbox.parse_dependencies("") == []

    def test_only_comments_and_blanks(self):
        content = "# comment\n\n# another\n  \n"
        assert ralph.DockerSandbox.parse_dependencies(content) == []

    def test_rejects_shell_injection(self):
        with pytest.raises(ValueError, match="invalid package name"):
            ralph.DockerSandbox.parse_dependencies("pkg; rm -rf /")

    def test_rejects_uppercase_names(self):
        with pytest.raises(ValueError, match="invalid package name"):
            ralph.DockerSandbox.parse_dependencies("BadPkg")

    def test_accepts_arch_qualifier(self):
        result = ralph.DockerSandbox.parse_dependencies("libc6:amd64")
        assert result == ["libc6:amd64"]

    def test_accepts_version_pinning(self):
        result = ralph.DockerSandbox.parse_dependencies("openjdk-21-jdk=21.0.1+12-1")
        assert result == ["openjdk-21-jdk=21.0.1+12-1"]

# ---------------------------------------------------------------------------
# Sandbox.generate_project_dockerfile
# ---------------------------------------------------------------------------

class TestSandboxGenerateProjectDockerfile:
    def test_single_package(self):
        result = ralph.DockerSandbox.generate_project_dockerfile(["openjdk-21-jdk"])
        assert "apt-get install -y --no-install-recommends" in result
        assert "openjdk-21-jdk" in result

    def test_multiple_packages_joined(self):
        result = ralph.DockerSandbox.generate_project_dockerfile(["pkg1", "pkg2", "pkg3"])
        assert "'pkg1' 'pkg2' 'pkg3'" in result

    def test_packages_are_shell_quoted(self):
        result = ralph.DockerSandbox.generate_project_dockerfile(["pkg; rm -rf /"])
        assert "\"pkg; rm -rf /\"" not in result
        assert "'pkg; rm -rf /'" in result

    def test_contains_arg_and_from(self):
        result = ralph.DockerSandbox.generate_project_dockerfile(["pkg1"])
        assert "ARG BASE_IMAGE" in result
        assert "FROM ${BASE_IMAGE}" in result

    def test_user_switching(self):
        result = ralph.DockerSandbox.generate_project_dockerfile(["pkg1"])
        lines = result.splitlines()
        assert "USER root" in lines
        assert "USER agent" in lines
        assert lines.index("USER root") < lines.index("USER agent")

    def test_apt_cleanup(self):
        result = ralph.DockerSandbox.generate_project_dockerfile(["pkg1"])
        assert "rm -rf /var/lib/apt/lists/*" in result

    def test_no_install_recommends(self):
        result = ralph.DockerSandbox.generate_project_dockerfile(["pkg1"])
        assert "--no-install-recommends" in result


# ---------------------------------------------------------------------------
# Sandbox.find_project_config
# ---------------------------------------------------------------------------

class TestSandboxFindProjectConfig:
    def test_returns_none_when_no_agent_loop_dir(self, tmp_path):
        result = ralph.DockerSandbox.find_project_config(str(tmp_path))
        assert result is None

    def test_returns_none_when_agent_loop_empty(self, tmp_path):
        (tmp_path / ".agent-loop").mkdir()
        result = ralph.DockerSandbox.find_project_config(str(tmp_path))
        assert result is None

    def test_prefers_dockerfile_over_dependencies(self, tmp_path):
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\n")
        (al / "Dockerfile.sandbox").write_text("FROM base\n")
        config_type, path = ralph.DockerSandbox.find_project_config(str(tmp_path))
        assert config_type == "dockerfile"
        assert path == str(al / "Dockerfile.sandbox")

    def test_returns_dependencies_when_no_dockerfile(self, tmp_path):
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\n")
        config_type, path = ralph.DockerSandbox.find_project_config(str(tmp_path))
        assert config_type == "dependencies"
        assert path == str(al / "dependencies")

    def test_returns_dockerfile_when_no_dependencies(self, tmp_path):
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "Dockerfile.sandbox").write_text("FROM base\n")
        config_type, path = ralph.DockerSandbox.find_project_config(str(tmp_path))
        assert config_type == "dockerfile"
        assert path == str(al / "Dockerfile.sandbox")


# ---------------------------------------------------------------------------
# Sandbox.project_image_tag
# ---------------------------------------------------------------------------

class TestSandboxProjectImageTag:
    def test_includes_agent_and_project_in_tag(self):
        tag = ralph.DockerSandbox.project_image_tag("claude", "myproject", "base:v1", "content")
        assert tag.startswith("agent-loop-sandbox-claude-myproject:v")

    def test_hash_is_8_chars(self):
        tag = ralph.DockerSandbox.project_image_tag("claude", "proj", "base:v1", "content")
        chash = tag.split(":v")[1]
        assert len(chash) == 8

    def test_different_content_produces_different_hash(self):
        tag1 = ralph.DockerSandbox.project_image_tag("claude", "proj", "base:v1", "content-a")
        tag2 = ralph.DockerSandbox.project_image_tag("claude", "proj", "base:v1", "content-b")
        assert tag1 != tag2

    def test_same_content_produces_same_hash(self):
        tag1 = ralph.DockerSandbox.project_image_tag("claude", "proj", "base:v1", "content")
        tag2 = ralph.DockerSandbox.project_image_tag("claude", "proj", "base:v1", "content")
        assert tag1 == tag2

    def test_different_base_tag_produces_different_hash(self):
        tag1 = ralph.DockerSandbox.project_image_tag("claude", "proj", "base:v1", "content")
        tag2 = ralph.DockerSandbox.project_image_tag("claude", "proj", "base:v2", "content")
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
        return ralph.DockerSandbox(str(tmp_path))

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

    def test_rejects_trailing_slash(self, tmp_path):
        sb = self._make_sandbox(tmp_path)
        with pytest.raises(ValueError, match="must not end with /"):
            sb.ensure_project_image("claude", "base:v1", "/some/path/")

# ---------------------------------------------------------------------------
# Sandbox._parse_docker_timestamp
# ---------------------------------------------------------------------------

class TestSandboxParseDockerTimestamp:
    def test_z_suffix(self):
        dt = ralph.DockerSandbox._parse_docker_timestamp("2024-06-15T10:30:00Z")
        assert dt.year == 2024 and dt.month == 6

    def test_truncates_nanoseconds(self):
        dt = ralph.DockerSandbox._parse_docker_timestamp("2024-06-15T10:30:00.123456789Z")
        assert dt.microsecond == 123456

    def test_offset_format(self):
        dt = ralph.DockerSandbox._parse_docker_timestamp("2024-06-15T10:30:00+00:00")
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
        return ralph.DockerSandbox(str(tmp_path))

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
        return ralph.DockerSandbox(str(tmp_path))

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
# SandboxBackend.sandbox_name
# ---------------------------------------------------------------------------

class TestSandboxName:
    def test_simple_branch(self):
        assert ralph.DockerSandbox.sandbox_name("claude", "fix-auth") == "agent-loop-claude-fix-auth"

    def test_branch_with_slashes(self):
        assert ralph.DockerSandbox.sandbox_name("claude", "feature/foo") == "agent-loop-claude-feature-foo"

    def test_branch_with_multiple_slashes(self):
        assert ralph.DockerSandbox.sandbox_name("claude", "user/feature/bar") == "agent-loop-claude-user-feature-bar"

    def test_branch_uppercase_lowered(self):
        assert ralph.DockerSandbox.sandbox_name("claude", "Fix-Auth") == "agent-loop-claude-fix-auth"

    def test_consecutive_slashes_collapsed(self):
        assert ralph.DockerSandbox.sandbox_name("claude", "a//b") == "agent-loop-claude-a-b"

    def test_leading_trailing_hyphens_stripped(self):
        assert ralph.DockerSandbox.sandbox_name("claude", "-branch-") == "agent-loop-claude-branch"

    def test_custom_agent(self):
        assert ralph.DockerSandbox.sandbox_name("codex", "my-branch") == "agent-loop-codex-my-branch"


# ---------------------------------------------------------------------------
# Sandbox.ensure_sandbox (mocked docker)
# ---------------------------------------------------------------------------

class TestSandboxEnsureSandbox:
    @staticmethod
    def _make_sandbox(tmp_path, dockerfile="FROM base:latest\nRUN echo hi"):
        agent_dir = tmp_path / "docker" / "agent-loop" / "claude"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "Dockerfile").write_text(dockerfile)
        return ralph.DockerSandbox(str(tmp_path))

    @patch.object(ralph.DockerSandbox, "apply_network_policy")
    @patch.object(ralph.DockerSandbox, "_docker_sandbox_create")
    @patch.object(ralph.DockerSandbox, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(ralph.DockerSandbox, "ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(ralph.DockerSandbox, "sandbox_exists", return_value=False)
    def test_creates_new_sandbox(self, mock_exists, mock_img, mock_resolve,
                                 mock_create, mock_policy):
        sb = ralph.DockerSandbox("/dotfiles")
        name = sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        assert name == "agent-loop-claude-fix-auth"
        mock_create.assert_called_once_with(
            "agent-loop-claude-fix-auth", "agent-loop-sandbox-claude:vabc",
            "/work/fix-auth", "/repo/.git")
        mock_policy.assert_called_once_with("agent-loop-claude-fix-auth")

    @patch.object(ralph.DockerSandbox, "sandbox_exists", return_value=True)
    def test_reuses_existing_sandbox(self, mock_exists):
        sb = ralph.DockerSandbox("/dotfiles")
        name = sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        assert name == "agent-loop-claude-fix-auth"

    @patch.object(ralph.DockerSandbox, "apply_network_policy")
    @patch.object(ralph.DockerSandbox, "_docker_sandbox_create")
    @patch.object(ralph.DockerSandbox, "ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(ralph.DockerSandbox, "sandbox_exists", return_value=True)
    def test_reuse_skips_create_and_policy(self, mock_exists, mock_img, mock_create, mock_policy):
        sb = ralph.DockerSandbox("/dotfiles")
        sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        mock_create.assert_not_called()
        mock_policy.assert_not_called()
        mock_img.assert_not_called()

    @patch.object(ralph.DockerSandbox, "apply_network_policy")
    @patch.object(ralph.DockerSandbox, "_docker_sandbox_create")
    @patch.object(ralph.DockerSandbox, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(ralph.DockerSandbox, "ensure_project_image",
                  return_value="agent-loop-sandbox-claude-myproj:vdef12345")
    @patch.object(ralph.DockerSandbox, "ensure_image",
                  return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(ralph.DockerSandbox, "sandbox_exists", return_value=False)
    def test_calls_ensure_project_image_when_project_dir(
            self, mock_exists, mock_img, mock_proj, mock_resolve,
            mock_create, mock_policy):
        sb = ralph.DockerSandbox("/dotfiles")
        sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth",
                          project_dir="/repo/root")
        mock_proj.assert_called_once_with(
            "claude", "agent-loop-sandbox-claude:vabc", "/repo/root",
            force_rebuild=False)
        mock_create.assert_called_once_with(
            "agent-loop-claude-fix-auth",
            "agent-loop-sandbox-claude-myproj:vdef12345",
            "/work/fix-auth", "/repo/.git")

    @patch.object(ralph.DockerSandbox, "apply_network_policy")
    @patch.object(ralph.DockerSandbox, "_docker_sandbox_create")
    @patch.object(ralph.DockerSandbox, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(ralph.DockerSandbox, "ensure_image",
                  return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(ralph.DockerSandbox, "sandbox_exists", return_value=False)
    def test_skips_project_image_when_no_project_dir(
            self, mock_exists, mock_img, mock_resolve, mock_create, mock_policy):
        sb = ralph.DockerSandbox("/dotfiles")
        with patch.object(ralph.DockerSandbox, "ensure_project_image") as mock_proj:
            sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
            mock_proj.assert_not_called()
        mock_create.assert_called_once_with(
            "agent-loop-claude-fix-auth",
            "agent-loop-sandbox-claude:vabc",
            "/work/fix-auth", "/repo/.git")

    @patch.object(ralph.DockerSandbox, "apply_network_policy")
    @patch.object(ralph.DockerSandbox, "_docker_sandbox_create")
    @patch.object(ralph.DockerSandbox, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(ralph.DockerSandbox, "ensure_project_image",
                  return_value="agent-loop-sandbox-claude-myproj:vdef12345")
    @patch.object(ralph.DockerSandbox, "ensure_image",
                  return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(ralph.DockerSandbox, "sandbox_exists", return_value=False)
    def test_force_rebuild_passed_through(
            self, mock_exists, mock_img, mock_proj, mock_resolve,
            mock_create, mock_policy):
        sb = ralph.DockerSandbox("/dotfiles")
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
        ralph.DockerSandbox.apply_network_policy("agent-loop-claude-fix-auth")
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
        ralph.DockerSandbox("/dotfiles").cleanup_sandbox("claude", "fix-auth")
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
    @patch.object(ralph.DockerSandbox, "_docker_sandbox_ls")
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
        sb = ralph.DockerSandbox(str(tmp_path))
        pruned = sb.prune_sandboxes("claude")
        assert pruned == ["agent-loop-claude-orphan"]
        mock_run.assert_called_once_with(
            ["docker", "sandbox", "rm", "agent-loop-claude-orphan"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )

    @patch("ralph.subprocess.run")
    @patch.object(ralph.DockerSandbox, "_docker_sandbox_ls")
    def test_keeps_active_sandboxes(self, mock_ls, mock_run, tmp_path):
        existing = tmp_path / "workspace"
        existing.mkdir()
        mock_ls.return_value = {
            "vms": [
                {"name": "agent-loop-claude-active", "workspace": str(existing)},
            ]
        }
        sb = ralph.DockerSandbox(str(tmp_path))
        pruned = sb.prune_sandboxes("claude")
        assert pruned == []
        mock_run.assert_not_called()

    @patch.object(ralph.DockerSandbox, "_docker_sandbox_ls")
    def test_ignores_other_agents(self, mock_ls, tmp_path):
        mock_ls.return_value = {
            "vms": [
                {"name": "agent-loop-codex-orphan", "workspace": "/nonexistent"},
            ]
        }
        sb = ralph.DockerSandbox(str(tmp_path))
        pruned = sb.prune_sandboxes("claude")
        assert pruned == []

    @patch.object(ralph.DockerSandbox, "_docker_sandbox_ls")
    def test_empty_vm_list(self, mock_ls, tmp_path):
        mock_ls.return_value = {"vms": []}
        sb = ralph.DockerSandbox(str(tmp_path))
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
        result = ralph.DockerSandbox._docker_sandbox_ls()
        assert result == vms_data

    @patch("ralph.subprocess.run")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = ralph.DockerSandbox._docker_sandbox_ls()
        assert result == {"vms": []}

    @patch("ralph.subprocess.run")
    def test_returns_empty_on_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        result = ralph.DockerSandbox._docker_sandbox_ls()
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
    @patch("ralph.proxy_health_check", return_value=(True, "abc123"))
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_all_checks_pass(self, mock_time, mock_read, mock_health, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = ralph.DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert failures == []

    @patch("ralph.subprocess.run")
    @patch("ralph.proxy_health_check", return_value=(True, "abc123"))
    @patch("ralph.read_token_from_keychain", return_value=None)
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_token_missing_returns_error(self, mock_time, mock_read, mock_health, mock_run):
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = ralph.DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "no token found" in failures[0]
        assert "ralph store-token" in failures[0]

    @patch("ralph.subprocess.run")
    @patch("ralph.proxy_health_check", return_value=(True, "abc123"))
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_token_expired_returns_error(self, mock_time, mock_read, mock_health, mock_run):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": past_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = ralph.DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "token expired" in failures[0]
        assert "ralph store-token" in failures[0]

    @patch("ralph.subprocess.run")
    @patch("ralph.proxy_health_check", return_value=(False, None))
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_proxy_down_returns_error(self, mock_time, mock_read, mock_health, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = ralph.DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "proxy not reachable" in failures[0]
        assert "start the credential proxy" in failures[0]

    @patch("ralph.subprocess.run")
    @patch("ralph.proxy_health_check", return_value=(True, "abc123"))
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_sandbox_unresponsive_returns_error(self, mock_time, mock_read, mock_health, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=1, curl_rc=28)
        sb = ralph.DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "not responding" in failures[0]
        assert f"docker sandbox rm {self.SANDBOX_NAME}" in failures[0]

    @patch("ralph.subprocess.run")
    @patch("ralph.proxy_health_check", return_value=(True, "abc123"))
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_sandbox_unresponsive_skips_network_check(self, mock_time, mock_read, mock_health, mock_run):
        """When sandbox is unresponsive, network policy check is skipped."""
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=1, curl_rc=0)
        sb = ralph.DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        # Should only have sandbox error, not network policy error
        assert len(failures) == 1
        assert "not responding" in failures[0]
        # curl should not have been called
        curl_calls = [c for c in mock_run.call_args_list if "curl" in c[0][0]]
        assert len(curl_calls) == 0

    @patch("ralph.subprocess.run")
    @patch("ralph.proxy_health_check", return_value=(True, "abc123"))
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_network_policy_not_applied_returns_error(self, mock_time, mock_read, mock_health, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        # echo succeeds, curl also succeeds (google.com reachable = bad)
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=0)
        sb = ralph.DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "network policy not applied" in failures[0]
        assert "outbound requests should be blocked" in failures[0]

    @patch("ralph.subprocess.run")
    @patch("ralph.proxy_health_check", return_value=(False, None))
    @patch("ralph.read_token_from_keychain", return_value=None)
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_multiple_failures_collected(self, mock_time, mock_read, mock_health, mock_run):
        """All failures are collected, not just the first one."""
        mock_run.side_effect = self._run_side_effect(echo_rc=1, curl_rc=28)
        sb = ralph.DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        # token missing + proxy down + sandbox unresponsive = 3 failures
        assert len(failures) == 3


# ---------------------------------------------------------------------------
# Sandbox.setup_git_config (mocked subprocess)
# ---------------------------------------------------------------------------

class TestSandboxSetupGitConfig:
    @patch("ralph.subprocess.run")
    def test_sets_user_name_email_and_safe_directory(self, mock_run):
        ralph.DockerSandbox("/dotfiles").setup_git_config("my-sandbox", "Ralph", "ralph@test.com")
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
        sb = ralph.DockerSandbox("/dotfiles")
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
        sb = ralph.DockerSandbox("/dotfiles")
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
        sb = ralph.DockerSandbox("/dotfiles")
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
        sb = ralph.DockerSandbox("/dotfiles")
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
        sb = ralph.DockerSandbox("/dotfiles")
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


class TestSandboxSyncToHost:
    @patch("ralph.subprocess.run")
    def test_returns_true_when_host_can_see_commit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        result = ralph.DockerSandbox("/dotfiles").sync_to_host("sandbox", "abc123", "def456", "/work")
        assert result is True
        cmd = mock_run.call_args[0][0]
        assert "rev-parse" in cmd
        assert "--verify" in cmd
        assert "def456" in cmd

    @patch("ralph.subprocess.run")
    def test_returns_false_when_host_cannot_see_commit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        result = ralph.DockerSandbox("/dotfiles").sync_to_host("sandbox", "abc", "def", "/work")
        assert result is False


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
    @patch("ralph.create_sandbox_backend")
    @patch("ralph.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.unblock_ready_specs")
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
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

        ralph.process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)

        sandbox.check_in_sync.assert_called_once_with(
            "agent-loop-claude-my-branch", "/work/my-branch", git)
        sandbox.reset_to_host.assert_called_once_with(
            "agent-loop-claude-my-branch", "/work/my-branch", git)

    @patch("ralph.create_sandbox_backend")
    @patch("ralph.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.unblock_ready_specs")
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
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

        ralph.process_issue(
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

    @patch("ralph.create_sandbox_backend")
    @patch("ralph.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.unblock_ready_specs")
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
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

        result = ralph.process_issue(
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

    @patch("ralph.create_sandbox_backend")
    @patch("ralph.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.unblock_ready_specs")
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
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

        ralph.process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)

        sandbox.proxy_host.assert_called_once()
        call_args = sandbox.run_iteration.call_args
        env_vars = call_args[1].get("env_vars") or call_args[0][3]
        assert env_vars["ANTHROPIC_BASE_URL"] == "http://192.168.64.1:18080"

    @patch("ralph.create_sandbox_backend")
    @patch("ralph.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
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

        result = ralph.process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)
        assert result == 1

        gh.issue_edit.assert_any_call(
            42, "owner/repo",
            remove_label="status:in-progress",
            add_label="status:needs-attention")

    @patch("ralph.create_sandbox_backend")
    @patch("ralph.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.try_fast_forward", return_value=None)
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
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

        result = ralph.process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)
        assert result == 1

        gh.issue_edit.assert_any_call(
            42, "owner/repo",
            remove_label="status:in-progress",
            add_label="status:needs-attention")

    @patch("ralph.create_sandbox_backend")
    @patch("ralph.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.try_fast_forward", return_value=None)
    @patch("ralph.unblock_ready_specs")
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
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

        ralph.process_issue(
            42, git, "/dotfiles", gh, "claude", True, "sonnet",
            "user", "user@test.com", 18080)

        git.run.assert_any_call("push", cwd="/work/my-branch", check=False)

    @patch("ralph.create_sandbox_backend")
    @patch("ralph.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
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

        ralph.process_issue(
            42, git, "/dotfiles", gh, "codex", False, "sonnet",
            "user", "user@test.com", 18080)

        sandbox.ensure_sandbox.assert_called_once_with(
            "codex", "my-branch", "/work/my-branch",
            project_dir="/repo/root", force_rebuild=False)

    @patch("ralph.create_sandbox_backend")
    @patch("ralph.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.unblock_ready_specs")
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
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

        ralph.process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080, rebuild=True)

        sandbox.ensure_sandbox.assert_called_once_with(
            "claude", "my-branch", "/work/my-branch",
            project_dir="/repo/root", force_rebuild=True)

    @patch("ralph.create_sandbox_backend")
    @patch("ralph.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.unblock_ready_specs")
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
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

        ralph.process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)

        git.run.assert_any_call(
            "config", "branch.my-branch.issue", "42",
            cwd="/work/my-branch", check=False)

    @patch("ralph.create_sandbox_backend")
    @patch("ralph.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.unblock_ready_specs")
    @patch("ralph.ensure_worktree", return_value="/work/feat/slash-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
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

        ralph.process_issue(
            99, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)

        git.run.assert_any_call(
            "config", "branch.feat/slash-branch.issue", "99",
            cwd="/work/feat/slash-branch", check=False)

    @patch("ralph.create_sandbox_backend")
    @patch("ralph.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
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

        result = ralph.process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)
        assert result == 0

        gh.issue_edit.assert_any_call(
            42, "owner/repo",
            remove_label="status:in-progress",
            add_label="status:needs-attention")

    @patch("ralph.create_sandbox_backend")
    @patch("ralph.load_sandbox_config", return_value={"type": "docker"})
    @patch("ralph.unblock_ready_specs")
    @patch("ralph.ensure_worktree", return_value="/work/my-branch")
    @patch("ralph.resolve_repo", return_value="owner/repo")
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

        result = ralph.process_issue(
            42, git, "/dotfiles", gh, "claude", False, "sonnet",
            "user", "user@test.com", 18080)
        assert result == 0

        gh.issue_edit.assert_any_call(
            42, "owner/repo",
            remove_label="status:in-progress",
            add_label="status:done")
        mock_unblock.assert_called_once_with("owner/repo", gh)


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

    # proxy_health_check returns (False, None) for the initial
    # proxy_existed_before check, then (True, "v123") after ensure_proxy.
    @patch("ralph.DockerSandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.DockerSandbox.apply_network_policy")
    @patch("ralph.DockerSandbox._docker_sandbox_create")
    @patch("ralph.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_all_checks_pass(self, mock_time, mock_read, mock_ensure_proxy,
                             mock_health, mock_img, mock_resolve, mock_create,
                             mock_policy, mock_run, mock_stop, mock_remove,
                             capsys):
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
        assert "all 9 checks passed" in captured.out
        # Proxy was not running before selftest, so it should be stopped
        mock_stop.assert_called_once_with("claude")

    @patch("ralph.DockerSandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.DockerSandbox.apply_network_policy")
    @patch("ralph.DockerSandbox._docker_sandbox_create")
    @patch("ralph.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", return_value=(True, "abc123"))
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_does_not_stop_preexisting_proxy(self, mock_time, mock_read,
                                              mock_ensure_proxy, mock_health,
                                              mock_img, mock_resolve,
                                              mock_create, mock_policy,
                                              mock_run, mock_stop,
                                              mock_remove, capsys):
        """When proxy was already running before selftest, don't stop it."""
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=0, stdout="ok", stderr=""),
            MagicMock(returncode=28, stdout="", stderr=""),
        ]

        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 0
        # Proxy existed before, so stop_proxy should NOT be called
        mock_stop.assert_not_called()

    @patch("ralph.proxy_health_check", return_value=(False, None))
    @patch("ralph.read_token_from_keychain", return_value=None)
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_missing_token_aborts_early(self, mock_time, mock_read,
                                        mock_health, capsys):
        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: check token" in captured.out
        assert "selftest aborted" in captured.out

    @patch("ralph.proxy_health_check", return_value=(False, None))
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_expired_token_aborts_early(self, mock_time, mock_read,
                                        mock_health, capsys):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-old", "expiresAt": past_ms}
        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: check token" in captured.out
        assert "token expired" in captured.out

    @patch("ralph.DockerSandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.DockerSandbox.apply_network_policy")
    @patch("ralph.DockerSandbox._docker_sandbox_create")
    @patch("ralph.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_cleans_up_sandbox_on_failure(self, mock_time, mock_read,
                                          mock_ensure_proxy, mock_health,
                                          mock_img, mock_resolve, mock_create,
                                          mock_policy, mock_run, mock_stop,
                                          mock_remove):
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

    @patch("ralph.DockerSandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.DockerSandbox.apply_network_policy")
    @patch("ralph.DockerSandbox._docker_sandbox_create")
    @patch("ralph.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_reports_failed_checks(self, mock_time, mock_read,
                                   mock_ensure_proxy, mock_health,
                                   mock_img, mock_resolve, mock_create,
                                   mock_policy, mock_run, mock_stop,
                                   mock_remove, capsys):
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
        assert "2/9 checks failed" in captured.out

    @patch("ralph.DockerSandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.DockerSandbox.ensure_image", side_effect=RuntimeError("build failed"))
    @patch("ralph.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
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
        # Proxy should still be stopped (was started, didn't exist before)
        mock_stop.assert_called_once_with("claude")

    @patch("ralph.DockerSandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.DockerSandbox.apply_network_policy")
    @patch("ralph.DockerSandbox._docker_sandbox_create",
           side_effect=subprocess.CalledProcessError(1, "docker"))
    @patch("ralph.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_sandbox_create_failure_aborts(self, mock_time, mock_read,
                                          mock_ensure_proxy, mock_health,
                                          mock_img, mock_resolve, mock_create,
                                          mock_policy, mock_stop,
                                          mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: create sandbox" in captured.out
        assert "selftest aborted" in captured.out
        # Proxy should be stopped (didn't exist before)
        mock_stop.assert_called_once_with("claude")

    @patch("ralph.selftest", return_value=0)
    @patch("ralph.check_dependencies_prereq")
    @patch("ralph.sys.argv", ["ralph", "selftest"])
    def test_main_routes_selftest(self, mock_prereq, mock_selftest):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 0
        mock_selftest.assert_called_once_with(
            "claude", mock_selftest.call_args[0][1], sandbox_type="docker")

    @patch("ralph.selftest", return_value=0)
    @patch("ralph.check_dependencies_prereq")
    @patch("ralph.sys.argv", ["ralph", "selftest", "--agent", "codex"])
    def test_main_routes_selftest_with_agent(self, mock_prereq, mock_selftest):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 0
        mock_selftest.assert_called_once_with(
            "codex", mock_selftest.call_args[0][1], sandbox_type="docker")

    @patch("ralph.sys.argv", ["ralph", "selftest", "--badopt"])
    def test_main_selftest_rejects_unknown_option(self):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 2

    @patch("ralph.DockerSandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.DockerSandbox.apply_network_policy")
    @patch("ralph.DockerSandbox._docker_sandbox_create")
    @patch("ralph.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.DockerSandbox.ensure_project_image",
           return_value="agent-loop-sandbox-claude-myproject:vdeadbeef")
    @patch("ralph.DockerSandbox.find_project_config",
           return_value=("dependencies", "/proj/.agent-loop/dependencies"))
    @patch("ralph.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_project_image_check_with_dependencies(
            self, mock_time, mock_read, mock_ensure_proxy, mock_health,
            mock_img, mock_find_config, mock_proj_img, mock_resolve,
            mock_create, mock_policy, mock_run, mock_stop, mock_remove,
            capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),    # curl proxy health
            MagicMock(returncode=0, stdout="ok", stderr=""),    # claude via proxy
            MagicMock(returncode=28, stdout="", stderr=""),     # curl google (blocked)
        ]

        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 0

        captured = capsys.readouterr()
        assert "PASS: build project image" in captured.out
        assert "agent-loop-sandbox-claude-myproject:vdeadbeef" in captured.out
        assert "all 10 checks passed" in captured.out

    @patch("ralph.DockerSandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.DockerSandbox.apply_network_policy")
    @patch("ralph.DockerSandbox._docker_sandbox_create")
    @patch("ralph.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.DockerSandbox.ensure_project_image",
           return_value="agent-loop-sandbox-claude-myproject:vdeadbeef")
    @patch("ralph.DockerSandbox.find_project_config",
           return_value=("dockerfile", "/proj/.agent-loop/Dockerfile.sandbox"))
    @patch("ralph.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_project_image_check_with_dockerfile(
            self, mock_time, mock_read, mock_ensure_proxy, mock_health,
            mock_img, mock_find_config, mock_proj_img, mock_resolve,
            mock_create, mock_policy, mock_run, mock_stop, mock_remove,
            capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),    # curl proxy health
            MagicMock(returncode=0, stdout="ok", stderr=""),    # claude via proxy
            MagicMock(returncode=28, stdout="", stderr=""),     # curl google (blocked)
        ]

        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 0

        captured = capsys.readouterr()
        assert "PASS: build project image" in captured.out
        assert "all 10 checks passed" in captured.out

    @patch("ralph.DockerSandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.DockerSandbox.apply_network_policy")
    @patch("ralph.DockerSandbox._docker_sandbox_create")
    @patch("ralph.DockerSandbox._resolve_git_common_dir", return_value="/fake/.git")
    @patch("ralph.DockerSandbox.find_project_config", return_value=None)
    @patch("ralph.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_project_image_skipped_when_no_config(
            self, mock_time, mock_read, mock_ensure_proxy, mock_health,
            mock_img, mock_find_config, mock_resolve, mock_create,
            mock_policy, mock_run, mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": self.FUTURE_MS}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="ok", stderr=""),    # curl proxy health
            MagicMock(returncode=0, stdout="ok", stderr=""),    # claude via proxy
            MagicMock(returncode=28, stdout="", stderr=""),     # curl google (blocked)
        ]

        rc = ralph.selftest("claude", "/fake/dotfiles")
        assert rc == 0

        captured = capsys.readouterr()
        assert "PASS: build project image" not in captured.out
        assert "skipping project image check" in captured.out
        assert "all 9 checks passed" in captured.out

    @patch("ralph.DockerSandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.DockerSandbox.apply_network_policy")
    @patch("ralph.DockerSandbox._docker_sandbox_create")
    @patch("ralph.DockerSandbox.ensure_project_image",
           side_effect=RuntimeError("project build failed"))
    @patch("ralph.DockerSandbox.find_project_config",
           return_value=("dependencies", "/proj/.agent-loop/dependencies"))
    @patch("ralph.DockerSandbox.ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch("ralph.proxy_health_check", side_effect=[(False, None), (True, "abc123")])
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
# TartSandbox.check_prerequisites
# ---------------------------------------------------------------------------


class TestTartCheckPrerequisites:
    def _make(self, **kwargs):
        config = {"base_image": "img:latest"}
        config.update(kwargs)
        return ralph.TartSandbox("/dotfiles", config=config)

    @patch("ralph.shutil.which", return_value="/usr/local/bin/tart")
    def test_tart_and_docker_present(self, mock_which):
        mock_which.side_effect = lambda cmd: (
            "/usr/local/bin/tart" if cmd == "tart"
            else "/usr/local/bin/docker" if cmd == "docker"
            else None)
        ts = self._make()
        assert ts.check_prerequisites() == []

    @patch("ralph.shutil.which", return_value=None)
    def test_tart_missing(self, mock_which):
        mock_which.side_effect = lambda cmd: (
            None if cmd == "tart"
            else "/usr/local/bin/docker" if cmd == "docker"
            else None)
        ts = self._make()
        errors = ts.check_prerequisites()
        assert len(errors) == 1
        assert "tart is not installed" in errors[0]

    @patch("ralph.shutil.which", return_value=None)
    def test_docker_missing(self, mock_which):
        mock_which.side_effect = lambda cmd: (
            "/usr/local/bin/tart" if cmd == "tart"
            else None)
        ts = self._make()
        errors = ts.check_prerequisites()
        assert len(errors) == 1
        assert "docker is not installed" in errors[0]

    @patch("ralph.shutil.which", return_value=None)
    def test_both_missing(self, mock_which):
        ts = self._make()
        errors = ts.check_prerequisites()
        assert len(errors) == 2


# ---------------------------------------------------------------------------
# selftest --type tart routing
# ---------------------------------------------------------------------------


class TestSelftestTart:
    """Tests for tart-specific selftest path."""

    FUTURE_MS = 1700000000000 + 30 * 86400 * 1000  # 30 days from now

    @patch("ralph.TartSandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.subprocess.run")
    @patch("ralph.subprocess.Popen")
    @patch("ralph.TartSandbox._wait_for_guest_agent")
    @patch("ralph.TartSandbox.proxy_host", return_value="192.168.64.1")
    @patch("ralph.TartSandbox.ensure_image",
           return_value="agent-loop-template-claude-abc123")
    @patch("ralph.TartSandbox.check_prerequisites", return_value=[])
    @patch("ralph.proxy_health_check",
           side_effect=[(False, None), (True, "abc123")])
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_all_checks_pass(self, mock_time, mock_read, mock_ensure_proxy,
                              mock_health, mock_prereq, mock_img,
                              mock_proxy_host, mock_wait, mock_popen,
                              mock_run, mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test",
                                  "expiresAt": self.FUTURE_MS}
        mock_popen.return_value = MagicMock()
        # subprocess.run calls: tart clone (ok), proxy reachable (ok),
        # claude auth (ok)
        mock_run.side_effect = [
            MagicMock(returncode=0),   # tart clone
            MagicMock(returncode=0),   # curl proxy health from VM
            MagicMock(returncode=0),   # claude via proxy
        ]

        rc = ralph.selftest("claude", "/fake/dotfiles", sandbox_type="tart")
        assert rc == 0

        captured = capsys.readouterr()
        assert "selftest starting (tart)" in captured.out
        assert "PASS: check token" in captured.out
        assert "PASS: prerequisites" in captured.out
        assert "PASS: proxy health" in captured.out
        assert "PASS: build template" in captured.out
        assert "PASS: create test VM" in captured.out
        assert "PASS: tart exec" in captured.out
        assert "PASS: proxy reachable from VM" in captured.out
        assert "PASS: claude auth via proxy" in captured.out
        assert "network isolation — skipping" in captured.out
        # proxy was not running before, so should be stopped
        mock_stop.assert_called_once_with("claude")

    @patch("ralph.TartSandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.TartSandbox.ensure_image",
           side_effect=RuntimeError("clone failed"))
    @patch("ralph.TartSandbox.check_prerequisites", return_value=[])
    @patch("ralph.proxy_health_check",
           side_effect=[(False, None), (True, "abc123")])
    @patch("ralph.ensure_proxy")
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_template_build_failure_aborts(self, mock_time, mock_read,
                                            mock_ensure_proxy, mock_health,
                                            mock_prereq, mock_img,
                                            mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test",
                                  "expiresAt": self.FUTURE_MS}
        rc = ralph.selftest("claude", "/fake/dotfiles", sandbox_type="tart")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: build template" in captured.out
        assert "selftest aborted" in captured.out

    @patch("ralph.TartSandbox.remove_sandbox")
    @patch("ralph.stop_proxy")
    @patch("ralph.TartSandbox.check_prerequisites",
           return_value=["tart is not installed"])
    @patch("ralph.proxy_health_check", return_value=(False, None))
    @patch("ralph.read_token_from_keychain")
    @patch("ralph.time.time", return_value=1700000000.0)
    def test_prerequisites_failure_aborts(self, mock_time, mock_read,
                                          mock_health, mock_prereq,
                                          mock_stop, mock_remove, capsys):
        mock_read.return_value = {"accessToken": "sk-test",
                                  "expiresAt": self.FUTURE_MS}
        rc = ralph.selftest("claude", "/fake/dotfiles", sandbox_type="tart")
        assert rc == 1
        captured = capsys.readouterr()
        assert "FAIL: prerequisites" in captured.out
        assert "prerequisites not met" in captured.out

    @patch("ralph.selftest", return_value=0)
    @patch("ralph.check_dependencies_prereq")
    @patch("ralph.sys.argv", ["ralph", "selftest", "--type", "tart"])
    def test_main_routes_selftest_with_type(self, mock_prereq, mock_selftest):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 0
        mock_selftest.assert_called_once_with(
            "claude", mock_selftest.call_args[0][1], sandbox_type="tart")

    @patch("ralph.selftest", return_value=0)
    @patch("ralph.check_dependencies_prereq")
    @patch("ralph.sys.argv",
           ["ralph", "selftest", "--type", "tart", "--agent", "codex"])
    def test_main_routes_selftest_type_and_agent(self, mock_prereq,
                                                  mock_selftest):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 0
        mock_selftest.assert_called_once_with(
            "codex", mock_selftest.call_args[0][1], sandbox_type="tart")

    @patch("ralph.sys.argv", ["ralph", "selftest", "--type", "podman"])
    def test_main_selftest_rejects_unknown_type(self):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# prune-sandboxes --type
# ---------------------------------------------------------------------------


class TestPruneSandboxesType:
    """Tests for --type flag on prune-sandboxes subcommand."""

    @patch("ralph.DockerSandbox.prune_sandboxes", return_value=[])
    @patch("ralph.sys.argv", ["ralph", "prune-sandboxes"])
    def test_default_uses_docker(self, mock_prune, capsys):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 0
        mock_prune.assert_called_once_with("claude")

    @patch("ralph.TartSandbox.prune_sandboxes", return_value=["vm1"])
    @patch("ralph.sys.argv",
           ["ralph", "prune-sandboxes", "--type", "tart"])
    def test_type_tart_uses_tart_sandbox(self, mock_prune):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 0
        mock_prune.assert_called_once_with("claude")

    @patch("ralph.TartSandbox.prune_sandboxes", return_value=[])
    @patch("ralph.sys.argv",
           ["ralph", "prune-sandboxes", "--type", "tart", "--agent", "codex"])
    def test_type_tart_with_agent(self, mock_prune, capsys):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 0
        mock_prune.assert_called_once_with("codex")

    @patch("ralph.sys.argv",
           ["ralph", "prune-sandboxes", "--type", "podman"])
    def test_rejects_unknown_type(self):
        with pytest.raises(SystemExit) as exc_info:
            ralph.main()
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# ensure_proxy — docker wait timeout
# ---------------------------------------------------------------------------


class TestEnsureProxyStaleCleanup:
    @patch("ralph.proxy_health_check",
           side_effect=[(False, None), (True, "abc123")])
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
# GitHub — retry logic
# ---------------------------------------------------------------------------


class TestGitHubRetry:
    @patch("ralph.time.sleep")
    @patch("ralph.subprocess.run")
    def test_retries_on_transient_failure(self, mock_run, mock_sleep):
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, "gh", stderr="API error"),
            MagicMock(returncode=0, stdout='[{"number": 1}]'),
        ]
        gh = ralph.GitHub()
        numbers = gh.issue_list("owner/repo", ["spec"])
        assert numbers == [1]
        assert mock_run.call_count == 2
        mock_sleep.assert_called_once_with(1)

    @patch("ralph.time.sleep")
    @patch("ralph.subprocess.run")
    def test_raises_after_max_retries(self, mock_run, mock_sleep):
        mock_run.side_effect = subprocess.CalledProcessError(1, "gh", stderr="down")
        gh = ralph.GitHub()
        with pytest.raises(subprocess.CalledProcessError):
            gh.issue_list("owner/repo", ["spec"])
        assert mock_run.call_count == 3
        assert mock_sleep.call_count == 3

    @patch("ralph.subprocess.run")
    def test_no_retry_on_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='[{"number": 5}]')
        gh = ralph.GitHub()
        numbers = gh.issue_list("owner/repo", ["spec"])
        assert numbers == [5]
        assert mock_run.call_count == 1


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
        gh = MagicMock()
        gh.issue_list.return_value = [42]

        with patch("ralph.process_issue", side_effect=RuntimeError("boom")):
            ralph.poll_loop(git, "/dotfiles", gh, "claude", False, "sonnet",
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
        gh = MagicMock()
        gh.issue_list.return_value = [42]
        gh.issue_edit.side_effect = RuntimeError("gh failed")

        with patch("ralph.process_issue", side_effect=RuntimeError("boom")):
            # Should not raise
            ralph.poll_loop(git, "/dotfiles", gh, "claude", False, "sonnet",
                            "user", "user@test.com", 18080, 30, 1)


# ---------------------------------------------------------------------------
# ITERATION_PROMPT content
# ---------------------------------------------------------------------------

class TestIterationPrompt:
    """Verify ITERATION_PROMPT contains required execution instructions."""

    def test_contains_blocked_marker_rule(self):
        assert "[blocked:" in ralph.DockerSandbox.ITERATION_PROMPT

    def test_contains_run_all_checks_rule(self):
        assert "Run all checks" in ralph.DockerSandbox.ITERATION_PROMPT

    def test_contains_spec_maintenance_rules(self):
        assert "Spec maintenance rules" in ralph.DockerSandbox.ITERATION_PROMPT

    def test_contains_step_structure(self):
        assert "Each step follows this structure" in ralph.DockerSandbox.ITERATION_PROMPT

    def test_contains_unfulfillable_tasks_section(self):
        assert "Unfulfillable tasks" in ralph.DockerSandbox.ITERATION_PROMPT
