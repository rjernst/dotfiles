"""Unit tests for ralph.token — token management functions."""

import io
import json
import re
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from ralph.token import (
    MS_PER_DAY,
    DEFAULT_EXPIRY_DAYS,
    keychain_service_name,
    read_token_from_keychain,
    write_token_to_keychain,
    format_expiry_date,
    run_claude_setup_token,
    prompt_for_api_key,
    _parse_and_store_token,
    store_token,
    check_token,
    get_token,
    ensure_token,
)


# ---------------------------------------------------------------------------
# keychain_service_name
# ---------------------------------------------------------------------------

class TestKeychainServiceName:
    def test_default_agent(self):
        assert keychain_service_name("claude") == "claude-token"

    def test_custom_agent(self):
        assert keychain_service_name("codex") == "codex-token"


# ---------------------------------------------------------------------------
# format_expiry_date
# ---------------------------------------------------------------------------

class TestFormatExpiryDate:
    def test_known_timestamp(self):
        # 2027-01-01T00:00:00Z = 1798761600000 ms
        assert format_expiry_date(1798761600000) == "2027-01-01"


# ---------------------------------------------------------------------------
# read_token_from_keychain (mocked subprocess)
# ---------------------------------------------------------------------------

class TestReadTokenFromKeychain:
    @patch("ralph.token.subprocess.run")
    def test_returns_parsed_json(self, mock_run):
        token_data = {"accessToken": "sk-test", "expiresAt": 9999999999999}
        mock_run.return_value = MagicMock(
            stdout=json.dumps(token_data) + "\n", returncode=0
        )
        result = read_token_from_keychain("claude")
        assert result == token_data
        mock_run.assert_called_once_with(
            ["security", "find-generic-password", "-s", "claude-token", "-a", "agent-loop", "-w"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
        )

    @patch("ralph.token.subprocess.run")
    def test_returns_none_when_not_found(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "security")
        result = read_token_from_keychain("claude")
        assert result is None

    @patch("ralph.token.subprocess.run")
    def test_returns_none_on_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(stdout="not-json\n", returncode=0)
        result = read_token_from_keychain("claude")
        assert result is None


# ---------------------------------------------------------------------------
# write_token_to_keychain (mocked subprocess)
# ---------------------------------------------------------------------------

class TestWriteTokenToKeychain:
    @patch("ralph.token.subprocess.run")
    def test_calls_security_with_correct_args(self, mock_run):
        write_token_to_keychain("claude", '{"accessToken":"t","expiresAt":1}')
        mock_run.assert_called_once_with(
            ["security", "add-generic-password",
             "-s", "claude-token", "-a", "agent-loop",
             "-w", '{"accessToken":"t","expiresAt":1}', "-U"],
            check=True,
        )

    @patch("ralph.token.subprocess.run")
    def test_uses_custom_agent_in_service_name(self, mock_run):
        write_token_to_keychain("codex", '{}')
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
            run_claude_setup_token()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# store_token (mocked stdin + keychain)
# ---------------------------------------------------------------------------

def _mock_validation_success():
    """Return a patch that makes token validation succeed."""
    return patch("ralph.token.subprocess.run",
                 return_value=MagicMock(returncode=0, stderr=""))


class TestStoreToken:
    @_mock_validation_success()
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO("sk-ant-oat01-abc123"))
    def test_bare_string_wraps_in_json(self, mock_stdin, mock_time, mock_write, mock_validate):
        store_token("claude")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-ant-oat01-abc123"
        expected_expiry = 1700000000000 + 365 * 86400 * 1000
        assert data["expiresAt"] == expected_expiry

    @_mock_validation_success()
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO(
        json.dumps({"accessToken": "sk-test", "expiresAt": 1800000000000})
    ))
    def test_json_input_preserved(self, mock_stdin, mock_time, mock_write, mock_validate):
        store_token("claude")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-test"
        assert data["expiresAt"] == 1800000000000

    @_mock_validation_success()
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO(
        json.dumps({"accessToken": "sk-test"})
    ))
    def test_json_input_without_expiry_gets_default(self, mock_stdin, mock_time, mock_write, mock_validate):
        store_token("claude")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-test"
        expected_expiry = 1700000000000 + 365 * 86400 * 1000
        assert data["expiresAt"] == expected_expiry

    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO(""))
    def test_empty_stdin_exits_with_error(self, mock_stdin):
        with pytest.raises(SystemExit) as exc_info:
            store_token("claude")
        assert exc_info.value.code == 1

    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO("cur_token"))
    def test_uses_correct_agent(self, mock_stdin, mock_time, mock_write):
        store_token("cursor")
        assert mock_write.call_args[0][0] == "cursor"

    @_mock_validation_success()
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.run_claude_setup_token", return_value="sk-from-setup")
    @patch("ralph.token.sys.stdin")
    def test_interactive_runs_claude_setup_token(self, mock_stdin, mock_setup, mock_time, mock_write, mock_validate):
        mock_stdin.isatty.return_value = True
        store_token("claude")
        mock_setup.assert_called_once()
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-from-setup"

    @_mock_validation_success()
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO("sk-token"))
    def test_prints_confirmation(self, mock_stdin, mock_time, mock_write, mock_validate, capsys):
        store_token("claude")
        captured = capsys.readouterr()
        assert "ralph: token stored for agent claude" in captured.out
        assert "expires" in captured.out

    @patch("ralph.token.subprocess.run",
           return_value=MagicMock(returncode=1, stderr="401 authentication_error"))
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO("sk-bad-token"))
    def test_invalid_token_rejected(self, mock_stdin, mock_time, mock_validate):
        with pytest.raises(SystemExit) as exc_info:
            store_token("claude")
        assert exc_info.value.code == 1

    @_mock_validation_success()
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO(
        json.dumps({"foo": "bar"})
    ))
    def test_json_without_access_token_warns(self, mock_stdin, mock_time, mock_write, mock_validate, capsys):
        store_token("claude")
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
    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_valid_token_exits_0(self, mock_time, mock_read):
        future_ms = 1700000000000 + 30 * 86400 * 1000  # 30 days from now
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        with pytest.raises(SystemExit) as exc_info:
            check_token("claude")
        assert exc_info.value.code == 0

    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_expired_token_exits_1(self, mock_time, mock_read):
        past_ms = 1700000000000 - 86400 * 1000  # 1 day ago
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": past_ms}
        with pytest.raises(SystemExit) as exc_info:
            check_token("claude")
        assert exc_info.value.code == 1

    @patch("ralph.token.read_token_from_keychain", return_value=None)
    def test_missing_token_exits_1(self, mock_read):
        with pytest.raises(SystemExit) as exc_info:
            check_token("claude")
        assert exc_info.value.code == 1

    @patch("ralph.token.read_token_from_keychain", return_value=None)
    def test_missing_token_suggests_store_token(self, mock_read, capsys):
        with pytest.raises(SystemExit):
            check_token("claude")
        captured = capsys.readouterr()
        assert "ralph store-token" in captured.err


# ---------------------------------------------------------------------------
# get_token (mocked keychain + time)
# ---------------------------------------------------------------------------

class TestGetToken:
    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_prints_access_token(self, mock_time, mock_read, capsys):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-ant-oat01-secret", "expiresAt": future_ms}
        get_token("claude")
        captured = capsys.readouterr()
        assert captured.out == "sk-ant-oat01-secret"

    @patch("ralph.token.read_token_from_keychain", return_value=None)
    def test_missing_token_exits_1(self, mock_read):
        with pytest.raises(SystemExit) as exc_info:
            get_token("claude")
        assert exc_info.value.code == 1

    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_expired_token_exits_1(self, mock_time, mock_read):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": past_ms}
        with pytest.raises(SystemExit) as exc_info:
            get_token("claude")
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# ensure_token (mocked keychain + setup-token)
# ---------------------------------------------------------------------------

class TestEnsureToken:
    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_returns_cached_valid_token(self, mock_time, mock_read):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-cached", "expiresAt": future_ms}
        result = ensure_token("claude")
        assert result == "sk-cached"

    @_mock_validation_success()
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.run_claude_setup_token", return_value="sk-fresh")
    @patch("ralph.token.read_token_from_keychain", return_value=None)
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_runs_setup_when_missing(self, mock_time, mock_read, mock_setup, mock_write, mock_validate):
        result = ensure_token("claude")
        assert result == "sk-fresh"
        mock_setup.assert_called_once()

    @_mock_validation_success()
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.run_claude_setup_token", return_value="sk-renewed")
    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_runs_setup_when_expired(self, mock_time, mock_read, mock_setup, mock_write, mock_validate):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-old", "expiresAt": past_ms}
        result = ensure_token("claude")
        assert result == "sk-renewed"
        mock_setup.assert_called_once()

    @_mock_validation_success()
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.run_claude_setup_token", return_value="sk-fresh")
    @patch("ralph.token.read_token_from_keychain", return_value=None)
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_stores_token_after_setup(self, mock_time, mock_read, mock_setup, mock_write, mock_validate):
        ensure_token("claude")
        mock_write.assert_called_once()
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-fresh"

    @_mock_validation_success()
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.run_claude_setup_token", return_value="sk-fresh")
    @patch("ralph.token.read_token_from_keychain", return_value=None)
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_missing_token_prints_status(self, mock_time, mock_read, mock_setup, mock_write, mock_validate, capsys):
        ensure_token("claude")
        captured = capsys.readouterr()
        assert "no token found" in captured.err
        assert "requesting new token" in captured.err

    @_mock_validation_success()
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.run_claude_setup_token", return_value="sk-renewed")
    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_expired_token_prints_status(self, mock_time, mock_read, mock_setup, mock_write, mock_validate, capsys):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-old", "expiresAt": past_ms}
        ensure_token("claude")
        captured = capsys.readouterr()
        assert "token expired" in captured.err
        assert "requesting new token" in captured.err

    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_valid_token_does_not_run_setup(self, mock_time, mock_read):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-cached", "expiresAt": future_ms}
        with patch("ralph.token.run_claude_setup_token") as mock_setup:
            ensure_token("claude")
            mock_setup.assert_not_called()


# ---------------------------------------------------------------------------
# Token JSON round-trip
# ---------------------------------------------------------------------------

class TestTokenRoundTrip:
    @_mock_validation_success()
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO("sk-round-trip-token"))
    def test_store_then_read_round_trip(self, mock_stdin, mock_time, mock_write, mock_validate):
        """Verify the JSON written by store_token can be parsed back correctly."""
        store_token("claude")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-round-trip-token"
        assert isinstance(data["expiresAt"], int)
        assert data["expiresAt"] > 1700000000000


# ---------------------------------------------------------------------------
# prompt_for_api_key (cursor-specific)
# ---------------------------------------------------------------------------

class TestPromptForApiKey:
    @patch("builtins.input", return_value="cur_abc123xyz")
    def test_returns_api_key(self, mock_input, capsys):
        result = prompt_for_api_key("cursor")
        assert result == "cur_abc123xyz"
        captured = capsys.readouterr()
        assert "Enter your cursor API key" in captured.err

    @patch("builtins.input", return_value="  cur_abc123xyz  ")
    def test_strips_whitespace(self, mock_input):
        result = prompt_for_api_key("cursor")
        assert result == "cur_abc123xyz"

    @patch("builtins.input", return_value="")
    def test_empty_input_exits(self, mock_input):
        with pytest.raises(SystemExit) as exc_info:
            prompt_for_api_key("cursor")
        assert exc_info.value.code == 1

    @patch("builtins.input", side_effect=EOFError)
    def test_eof_exits(self, mock_input):
        with pytest.raises(SystemExit) as exc_info:
            prompt_for_api_key("cursor")
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# store_token — cursor agent (no proxy, no validation)
# ---------------------------------------------------------------------------

class TestStoreTokenCursor:
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO("cur_abc123"))
    def test_bare_string_stores_without_validation(self, mock_stdin, mock_time, mock_write):
        """Cursor tokens skip validation (no subprocess.run for claude)."""
        store_token("cursor")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "cur_abc123"
        expected_expiry = 1700000000000 + 365 * 86400 * 1000
        assert data["expiresAt"] == expected_expiry

    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.prompt_for_api_key", return_value="cur_interactive")
    @patch("ralph.token.sys.stdin")
    def test_interactive_calls_prompt_for_api_key(self, mock_stdin, mock_prompt, mock_time, mock_write):
        mock_stdin.isatty.return_value = True
        store_token("cursor")
        mock_prompt.assert_called_once_with("cursor")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "cur_interactive"

    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO("cur_abc123"))
    def test_prints_confirmation(self, mock_stdin, mock_time, mock_write, capsys):
        store_token("cursor")
        captured = capsys.readouterr()
        assert "ralph: token stored for agent cursor" in captured.out
        assert "expires" in captured.out

    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO("cur_abc123"))
    def test_does_not_validate_via_subprocess(self, mock_stdin, mock_time, mock_write):
        """Cursor tokens should not trigger subprocess validation."""
        with patch("ralph.token.subprocess.run") as mock_run:
            store_token("cursor")
            # subprocess.run should NOT be called for validation
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# _parse_and_store_token — cursor agent (skips validation)
# ---------------------------------------------------------------------------

class TestParseAndStoreTokenCursor:
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_skips_validation_for_cursor(self, mock_time, mock_write):
        with patch("ralph.token.subprocess.run") as mock_run:
            _parse_and_store_token("cursor", "cur_abc123")
            mock_run.assert_not_called()

    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_stores_bare_token(self, mock_time, mock_write):
        _parse_and_store_token("cursor", "cur_abc123")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "cur_abc123"

    @_mock_validation_success()
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_validates_for_claude(self, mock_time, mock_write, mock_run):
        _parse_and_store_token("claude", "sk-ant-oat01-test")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"


# ---------------------------------------------------------------------------
# ensure_token — cursor agent
# ---------------------------------------------------------------------------

class TestEnsureTokenCursor:
    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_returns_cached_valid_token(self, mock_time, mock_read):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "cur_cached", "expiresAt": future_ms}
        result = ensure_token("cursor")
        assert result == "cur_cached"

    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.prompt_for_api_key", return_value="cur_fresh")
    @patch("ralph.token.read_token_from_keychain", return_value=None)
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_prompts_when_missing(self, mock_time, mock_read, mock_prompt, mock_write):
        result = ensure_token("cursor")
        assert result == "cur_fresh"
        mock_prompt.assert_called_once_with("cursor")

    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.prompt_for_api_key", return_value="cur_renewed")
    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_prompts_when_expired(self, mock_time, mock_read, mock_prompt, mock_write):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "cur_old", "expiresAt": past_ms}
        result = ensure_token("cursor")
        assert result == "cur_renewed"
        mock_prompt.assert_called_once_with("cursor")

    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.prompt_for_api_key", return_value="cur_fresh")
    @patch("ralph.token.read_token_from_keychain", return_value=None)
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_does_not_call_claude_setup(self, mock_time, mock_read, mock_prompt, mock_write):
        with patch("ralph.token.run_claude_setup_token") as mock_setup:
            ensure_token("cursor")
            mock_setup.assert_not_called()

    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.prompt_for_api_key", return_value="cur_fresh")
    @patch("ralph.token.read_token_from_keychain", return_value=None)
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_missing_token_prints_status(self, mock_time, mock_read, mock_prompt, mock_write, capsys):
        ensure_token("cursor")
        captured = capsys.readouterr()
        assert "no token found" in captured.err
        assert "requesting new token" in captured.err

    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_valid_token_does_not_prompt(self, mock_time, mock_read):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "cur_cached", "expiresAt": future_ms}
        with patch("ralph.token.prompt_for_api_key") as mock_prompt:
            ensure_token("cursor")
            mock_prompt.assert_not_called()
