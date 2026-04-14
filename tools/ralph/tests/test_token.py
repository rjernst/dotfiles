"""Unit tests for ralph.token — token management functions."""

import io
import json
import re
import subprocess
import time
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from ralph.token import (
    MS_PER_DAY,
    DEFAULT_EXPIRY_DAYS,
    _resolve_mode_string,
    _validate_api_key,
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
# _resolve_mode_string
# ---------------------------------------------------------------------------

class TestResolveModeString:
    def test_claude_default_is_oauth(self):
        assert _resolve_mode_string("claude", None) == "oauth"

    def test_claude_explicit_oauth(self):
        assert _resolve_mode_string("claude", "oauth") == "oauth"

    def test_claude_api_key(self):
        assert _resolve_mode_string("claude", "api_key") == "api_key"

    def test_claude_api_key_with_hyphen(self):
        assert _resolve_mode_string("claude", "api-key") == "api_key"

    def test_cursor_returns_none(self):
        assert _resolve_mode_string("cursor", None) is None

    def test_cursor_with_mode_returns_none(self):
        """Cursor has no auth_modes, so any mode arg is ignored."""
        assert _resolve_mode_string("cursor", "oauth") is None


# ---------------------------------------------------------------------------
# keychain_service_name
# ---------------------------------------------------------------------------

class TestKeychainServiceName:
    def test_claude_default_oauth(self):
        assert keychain_service_name("claude") == "claude-token"

    def test_claude_explicit_oauth(self):
        assert keychain_service_name("claude", "oauth") == "claude-token"

    def test_claude_api_key(self):
        assert keychain_service_name("claude", "api_key") == "claude-api-key"

    def test_claude_api_key_with_hyphen(self):
        assert keychain_service_name("claude", "api-key") == "claude-api-key"

    def test_cursor_returns_agent_token(self):
        assert keychain_service_name("cursor") == "cursor-token"


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

    @patch("ralph.token.subprocess.run")
    def test_api_key_mode_uses_correct_service(self, mock_run):
        token_data = {"accessToken": "sk-ant-api03-test", "expiresAt": 9999999999999}
        mock_run.return_value = MagicMock(
            stdout=json.dumps(token_data) + "\n", returncode=0
        )
        result = read_token_from_keychain("claude", "api_key")
        assert result == token_data
        mock_run.assert_called_once_with(
            ["security", "find-generic-password", "-s", "claude-api-key", "-a", "agent-loop", "-w"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
        )


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
    def test_uses_cursor_service_name(self, mock_run):
        write_token_to_keychain("cursor", '{}')
        cmd = mock_run.call_args[0][0]
        assert "-s" in cmd
        s_idx = cmd.index("-s")
        assert cmd[s_idx + 1] == "cursor-token"

    @patch("ralph.token.subprocess.run")
    def test_api_key_mode_uses_correct_service(self, mock_run):
        write_token_to_keychain("claude", '{}', auth_mode="api_key")
        cmd = mock_run.call_args[0][0]
        s_idx = cmd.index("-s")
        assert cmd[s_idx + 1] == "claude-api-key"


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
# _validate_api_key (mocked urllib)
# ---------------------------------------------------------------------------

class TestValidateApiKey:
    @patch("ralph.token.urllib.request.urlopen")
    def test_valid_key_succeeds(self, mock_urlopen, capsys):
        mock_urlopen.return_value = MagicMock()
        _validate_api_key("sk-ant-api03-valid-key")
        captured = capsys.readouterr()
        assert "API key validated successfully" in captured.err

    @patch("ralph.token.urllib.request.urlopen")
    def test_401_prints_rejected_message(self, mock_urlopen, capsys):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages", 401,
            "Unauthorized", {}, io.BytesIO(b""))
        with pytest.raises(SystemExit) as exc_info:
            _validate_api_key("sk-ant-api03-bad")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "API key rejected by api.anthropic.com (HTTP 401)" in captured.err

    @patch("ralph.token.urllib.request.urlopen")
    def test_403_prints_rejected_message(self, mock_urlopen, capsys):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages", 403,
            "Forbidden", {}, io.BytesIO(b""))
        with pytest.raises(SystemExit) as exc_info:
            _validate_api_key("sk-ant-api03-bad")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "API key rejected by api.anthropic.com (HTTP 403)" in captured.err

    @patch("ralph.token.urllib.request.urlopen")
    def test_429_prints_validation_failed(self, mock_urlopen, capsys):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages", 429,
            "Too Many Requests", {}, io.BytesIO(b""))
        with pytest.raises(SystemExit) as exc_info:
            _validate_api_key("sk-ant-api03-bad")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "API key validation failed: Too Many Requests" in captured.err

    @patch("ralph.token.urllib.request.urlopen")
    def test_url_error_prints_validation_failed(self, mock_urlopen, capsys):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        with pytest.raises(SystemExit) as exc_info:
            _validate_api_key("sk-ant-api03-bad")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "API key validation failed: Connection refused" in captured.err

    @patch("ralph.token.urllib.request.urlopen")
    def test_sends_correct_request(self, mock_urlopen):
        mock_urlopen.return_value = MagicMock()
        _validate_api_key("sk-ant-api03-test")
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://api.anthropic.com/v1/messages"
        assert req.get_header("X-api-key") == "sk-ant-api03-test"
        assert req.get_header("Anthropic-version") == "2023-06-01"
        assert req.get_header("Content-type") == "application/json"
        body = json.loads(req.data)
        assert body["model"] == "claude-haiku-4-5"
        assert body["max_tokens"] == 4


# ---------------------------------------------------------------------------
# prompt_for_api_key
# ---------------------------------------------------------------------------

class TestPromptForApiKey:
    @patch("builtins.input", return_value="cur_abc123xyz")
    def test_returns_api_key(self, mock_input, capsys):
        result = prompt_for_api_key("cursor")
        assert result == "cur_abc123xyz"
        captured = capsys.readouterr()
        assert "Enter your cursor API key" in captured.err

    @patch("builtins.input", return_value="sk-ant-api03-test")
    def test_claude_prompt_says_anthropic(self, mock_input, capsys):
        result = prompt_for_api_key("claude")
        assert result == "sk-ant-api03-test"
        captured = capsys.readouterr()
        assert "Enter your Anthropic API key:" in captured.err

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
# store_token — api_key mode
# ---------------------------------------------------------------------------

class TestStoreTokenApiKey:
    @patch("ralph.token._validate_api_key")
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO("sk-ant-api03-testkey"))
    def test_stores_api_key_with_far_future_expiry(self, mock_stdin, mock_time,
                                                    mock_write, mock_validate):
        store_token("claude", auth_mode="api_key")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-ant-api03-testkey"
        expected_expiry = 1700000000000 + 10 * 365 * MS_PER_DAY
        assert data["expiresAt"] == expected_expiry

    @patch("ralph.token._validate_api_key")
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO("sk-ant-api03-testkey"))
    def test_writes_to_api_key_service(self, mock_stdin, mock_time,
                                        mock_write, mock_validate):
        store_token("claude", auth_mode="api_key")
        assert mock_write.call_args[1]["auth_mode"] == "api_key"

    @patch("ralph.token._validate_api_key")
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO("sk-ant-api03-testkey"))
    def test_validates_via_api_not_claude_p(self, mock_stdin, mock_time,
                                             mock_write, mock_validate):
        with patch("ralph.token.subprocess.run") as mock_run:
            store_token("claude", auth_mode="api_key")
            # subprocess.run should NOT be called (no claude -p validation)
            mock_run.assert_not_called()
        # _validate_api_key should be called instead
        mock_validate.assert_called_once_with("sk-ant-api03-testkey")

    @patch("ralph.token._validate_api_key")
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.prompt_for_api_key", return_value="sk-ant-api03-interactive")
    @patch("ralph.token.sys.stdin")
    def test_interactive_prompts_for_api_key(self, mock_stdin, mock_prompt,
                                              mock_time, mock_write, mock_validate):
        mock_stdin.isatty.return_value = True
        store_token("claude", auth_mode="api_key")
        mock_prompt.assert_called_once_with("claude")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-ant-api03-interactive"

    @patch("ralph.token._validate_api_key")
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin")
    def test_interactive_does_not_run_claude_setup(self, mock_stdin, mock_time,
                                                    mock_write, mock_validate):
        mock_stdin.isatty.return_value = True
        with patch("ralph.token.prompt_for_api_key", return_value="sk-key"):
            with patch("ralph.token.run_claude_setup_token") as mock_setup:
                store_token("claude", auth_mode="api_key")
                mock_setup.assert_not_called()

    @patch("ralph.token._validate_api_key",
           side_effect=SystemExit(1))
    @patch("ralph.token.time.time", return_value=1700000000.0)
    @patch("ralph.token.sys.stdin", new_callable=lambda: io.StringIO("sk-ant-api03-INVALID"))
    def test_invalid_api_key_exits_1(self, mock_stdin, mock_time, mock_validate):
        with pytest.raises(SystemExit) as exc_info:
            store_token("claude", auth_mode="api_key")
        assert exc_info.value.code == 1


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


class TestCheckTokenApiKey:
    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_valid_api_key_prints_stored_message(self, mock_time, mock_read, capsys):
        far_future = 1700000000000 + 10 * 365 * MS_PER_DAY
        mock_read.return_value = {"accessToken": "sk-ant-api03-test", "expiresAt": far_future}
        with pytest.raises(SystemExit) as exc_info:
            check_token("claude", auth_mode="api_key")
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "ralph: API key stored for agent claude" in captured.out

    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_valid_api_key_does_not_print_days_remaining(self, mock_time, mock_read, capsys):
        far_future = 1700000000000 + 10 * 365 * MS_PER_DAY
        mock_read.return_value = {"accessToken": "sk-ant-api03-test", "expiresAt": far_future}
        with pytest.raises(SystemExit):
            check_token("claude", auth_mode="api_key")
        captured = capsys.readouterr()
        assert "days remaining" not in captured.out

    @patch("ralph.token.read_token_from_keychain", return_value=None)
    def test_missing_api_key_suggests_auth_flag(self, mock_read, capsys):
        with pytest.raises(SystemExit):
            check_token("claude", auth_mode="api_key")
        captured = capsys.readouterr()
        assert "ralph store-token --auth api-key" in captured.err

    @patch("ralph.token.read_token_from_keychain", return_value=None)
    def test_missing_oauth_suggests_auth_flag(self, mock_read, capsys):
        with pytest.raises(SystemExit):
            check_token("claude", auth_mode="oauth")
        captured = capsys.readouterr()
        assert "ralph store-token --auth oauth" in captured.err

    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_reads_from_correct_keychain_service(self, mock_time, mock_read):
        far_future = 1700000000000 + 10 * 365 * MS_PER_DAY
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": far_future}
        with pytest.raises(SystemExit):
            check_token("claude", auth_mode="api_key")
        mock_read.assert_called_once_with("claude", "api_key")


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


class TestGetTokenApiKey:
    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_prints_api_key(self, mock_time, mock_read, capsys):
        far_future = 1700000000000 + 10 * 365 * MS_PER_DAY
        mock_read.return_value = {"accessToken": "sk-ant-api03-test", "expiresAt": far_future}
        get_token("claude", auth_mode="api_key")
        captured = capsys.readouterr()
        assert captured.out == "sk-ant-api03-test"

    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_reads_from_correct_service(self, mock_time, mock_read):
        far_future = 1700000000000 + 10 * 365 * MS_PER_DAY
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": far_future}
        get_token("claude", auth_mode="api_key")
        mock_read.assert_called_once_with("claude", "api_key")


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


class TestEnsureTokenApiKey:
    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_returns_cached_valid_api_key(self, mock_time, mock_read):
        far_future = 1700000000000 + 10 * 365 * MS_PER_DAY
        mock_read.return_value = {"accessToken": "sk-ant-api03-cached", "expiresAt": far_future}
        result = ensure_token("claude", auth_mode="api_key")
        assert result == "sk-ant-api03-cached"

    @patch("ralph.token._validate_api_key")
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.prompt_for_api_key", return_value="sk-ant-api03-fresh")
    @patch("ralph.token.read_token_from_keychain", return_value=None)
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_prompts_when_missing(self, mock_time, mock_read, mock_prompt,
                                   mock_write, mock_validate):
        result = ensure_token("claude", auth_mode="api_key")
        assert result == "sk-ant-api03-fresh"
        mock_prompt.assert_called_once_with("claude")

    @patch("ralph.token._validate_api_key")
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.prompt_for_api_key", return_value="sk-ant-api03-fresh")
    @patch("ralph.token.read_token_from_keychain", return_value=None)
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_does_not_run_claude_setup(self, mock_time, mock_read, mock_prompt,
                                        mock_write, mock_validate):
        with patch("ralph.token.run_claude_setup_token") as mock_setup:
            ensure_token("claude", auth_mode="api_key")
            mock_setup.assert_not_called()

    @patch("ralph.token._validate_api_key")
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.prompt_for_api_key", return_value="sk-ant-api03-fresh")
    @patch("ralph.token.read_token_from_keychain", return_value=None)
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_stores_with_far_future_expiry(self, mock_time, mock_read, mock_prompt,
                                            mock_write, mock_validate):
        ensure_token("claude", auth_mode="api_key")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        assert data["accessToken"] == "sk-ant-api03-fresh"
        expected_expiry = 1700000000000 + 10 * 365 * MS_PER_DAY
        assert data["expiresAt"] == expected_expiry

    @patch("ralph.token.read_token_from_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_reads_from_correct_service(self, mock_time, mock_read):
        far_future = 1700000000000 + 10 * 365 * MS_PER_DAY
        mock_read.return_value = {"accessToken": "sk-cached", "expiresAt": far_future}
        ensure_token("claude", auth_mode="api_key")
        mock_read.assert_called_once_with("claude", "api_key")


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
# _parse_and_store_token — api_key mode
# ---------------------------------------------------------------------------

class TestParseAndStoreTokenApiKey:
    @patch("ralph.token._validate_api_key")
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_validates_via_api_call(self, mock_time, mock_write, mock_validate):
        _parse_and_store_token("claude", "sk-ant-api03-test", auth_mode="api_key")
        mock_validate.assert_called_once_with("sk-ant-api03-test")

    @patch("ralph.token._validate_api_key")
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_sets_far_future_expiry(self, mock_time, mock_write, mock_validate):
        _parse_and_store_token("claude", "sk-ant-api03-test", auth_mode="api_key")
        written_json = mock_write.call_args[0][1]
        data = json.loads(written_json)
        expected = 1700000000000 + 10 * 365 * MS_PER_DAY
        assert data["expiresAt"] == expected

    @patch("ralph.token._validate_api_key")
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_does_not_call_subprocess(self, mock_time, mock_write, mock_validate):
        with patch("ralph.token.subprocess.run") as mock_run:
            _parse_and_store_token("claude", "sk-ant-api03-test", auth_mode="api_key")
            mock_run.assert_not_called()

    @patch("ralph.token._validate_api_key")
    @patch("ralph.token.write_token_to_keychain")
    @patch("ralph.token.time.time", return_value=1700000000.0)
    def test_writes_with_auth_mode(self, mock_time, mock_write, mock_validate):
        _parse_and_store_token("claude", "sk-ant-api03-test", auth_mode="api_key")
        assert mock_write.call_args[1]["auth_mode"] == "api_key"


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
