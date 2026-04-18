"""Token management for the credential injection proxy."""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

from ralph.agents import get_agent, get_auth_mode

MS_PER_DAY = 86400 * 1000
DEFAULT_EXPIRY_DAYS = 365


def _resolve_mode_string(agent, auth_mode):
    """Return the resolved auth mode string, or None for single-mode agents."""
    cfg = get_agent(agent)
    if "auth_modes" not in cfg:
        return None
    if auth_mode is not None:
        return auth_mode.replace("-", "_")
    return cfg["default_auth_mode"]


def keychain_service_name(agent, auth_mode=None):
    """Return the Keychain service name for the given agent and auth mode."""
    mode_config = get_auth_mode(agent, auth_mode)
    if mode_config is not None:
        return mode_config["keychain_service"]
    return f"{agent}-token"


def read_token_from_keychain(agent, auth_mode=None):
    """Read and parse the token JSON from macOS Keychain.

    Returns the parsed dict, or None if not found.
    """
    service = keychain_service_name(agent, auth_mode)
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", "agent-loop", "-w"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
        )
        return json.loads(result.stdout.strip())
    except subprocess.CalledProcessError:
        return None
    except (json.JSONDecodeError, ValueError):
        return None


def write_token_to_keychain(agent, json_str, auth_mode=None):
    """Write token JSON string to macOS Keychain."""
    service = keychain_service_name(agent, auth_mode)
    subprocess.run(
        ["security", "add-generic-password",
         "-s", service, "-a", "agent-loop", "-w", json_str, "-U"],
        check=True,
    )


def format_expiry_date(expires_at_ms):
    """Format an expiresAt timestamp (ms) as a human-readable date string."""
    return datetime.datetime.fromtimestamp(
        expires_at_ms / 1000, tz=datetime.timezone.utc
    ).strftime("%Y-%m-%d")


def run_claude_setup_token():
    """Run 'claude setup-token' interactively via pty and extract the token.

    Uses pty.spawn so setup-token gets a real terminal (renders its TUI
    normally for the user), while we capture output in the background
    and extract the sk-ant-* token via regex.
    """
    import fcntl
    import io
    import pty
    import select
    import struct
    import termios
    import tty

    if not shutil.which("claude"):
        print("ralph: 'claude' command not found", file=sys.stderr)
        sys.exit(1)

    # Create pty pair and set width to 512 columns so the token (108+ chars)
    # doesn't wrap. pty.spawn uses os.forkpty which doesn't let us set the
    # window size, so we manage the fork ourselves.
    master_fd, slave_fd = pty.openpty()
    winsize = struct.pack('HHHH', 24, 512, 0, 0)
    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

    pid = os.fork()
    if pid == 0:
        # Child: connect to slave pty and exec setup-token
        os.close(master_fd)
        os.setsid()
        os.login_tty(slave_fd)
        os.execlp("claude", "claude", "setup-token")

    # Parent
    os.close(slave_fd)
    captured = []

    # Set raw mode on stdin so keypresses pass through to the child
    try:
        old_attrs = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())
        restore_term = True
    except (termios.error, io.UnsupportedOperation):
        restore_term = False

    try:
        while True:
            try:
                rfds = select.select([master_fd, sys.stdin.fileno()], [], [], 0.1)[0]
            except (ValueError, OSError):
                break
            if master_fd in rfds:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                captured.append(data)
                os.write(sys.stdout.fileno(), data)
            if sys.stdin.fileno() in rfds:
                try:
                    data = os.read(sys.stdin.fileno(), 1024)
                except OSError:
                    break
                if not data:
                    break
                os.write(master_fd, data)
    finally:
        if restore_term:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSAFLUSH, old_attrs)
        os.close(master_fd)

    _, status = os.waitpid(pid, 0)
    exit_code = os.waitstatus_to_exitcode(status) if hasattr(os, 'waitstatus_to_exitcode') else status >> 8

    if exit_code != 0:
        print("ralph: claude setup-token failed", file=sys.stderr)
        sys.exit(1)

    full_output = b"".join(captured).decode("utf-8", errors="replace")
    # Token chars are alphanumeric, hyphens, and underscores.
    # Find all matches and take the longest (in case of partial matches).
    matches = re.findall(r'sk-ant-oat01-[A-Za-z0-9_-]+', full_output)
    if not matches:
        print("ralph: could not find token in setup-token output", file=sys.stderr)
        sys.exit(1)

    return max(matches, key=len)


def prompt_for_api_key(agent):
    """Prompt the user for an API key interactively.

    Used for agents that use simple API keys (e.g. cursor) or for
    claude in api_key auth mode.
    """
    if agent == "claude":
        print("Enter your Anthropic API key:", file=sys.stderr)
    else:
        print(f"Enter your {agent} API key:", file=sys.stderr)
    try:
        raw = input().strip()
    except EOFError:
        raw = ""
    if not raw:
        print(f"ralph: no API key provided for agent {agent}", file=sys.stderr)
        sys.exit(1)
    return raw


def prompt_for_gateway_token():
    """Prompt the user for a gateway Bearer token."""
    print("Enter your gateway Bearer token:", file=sys.stderr)
    try:
        raw = input().strip()
    except EOFError:
        raw = ""
    if not raw:
        print("ralph: no gateway token provided", file=sys.stderr)
        sys.exit(1)
    return raw


def prompt_for_base_url():
    """Prompt for a custom API base URL (optional).

    Returns the URL string, or None if left empty.
    """
    print("Enter API base URL (leave empty for default https://api.anthropic.com):",
          file=sys.stderr)
    try:
        raw = input().strip()
    except EOFError:
        raw = ""
    return raw or None


def prompt_for_gateway_base_url():
    """Prompt the user for a gateway base URL (e.g. https://gateway.example.com)."""
    print("Enter the gateway base URL (e.g. https://gateway.example.com):",
          file=sys.stderr)
    try:
        raw = input().strip()
    except EOFError:
        raw = ""
    if not raw:
        print("ralph: no base URL provided", file=sys.stderr)
        sys.exit(1)
    return raw


def prompt_for_model_prefix():
    """Prompt the user for a gateway model prefix (e.g. llm-gateway)."""
    print("Enter the model prefix (e.g. llm-gateway):", file=sys.stderr)
    try:
        raw = input().strip()
    except EOFError:
        raw = ""
    if not raw:
        print("ralph: no model prefix provided", file=sys.stderr)
        sys.exit(1)
    return raw


def _validate_gateway_token(token, base_url, model_prefix=""):
    """Validate a gateway Bearer token via a direct API call.

    Sends a minimal request to <base_url>/v1/messages with Bearer auth.
    Uses model_prefix the same way --model resolution does in build_proxy_env.
    Exits on failure.
    """
    url = base_url.rstrip("/") + "/v1/messages"
    model = "claude-haiku-4-5"
    if model_prefix:
        model = f"{model_prefix}/{model}"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        data=json.dumps({
            "model": model,
            "max_tokens": 4,
            "messages": [{"role": "user", "content": "ok"}],
        }).encode(),
    )

    print(f"ralph: validating gateway token ({len(token)} chars)...", file=sys.stderr)
    try:
        urllib.request.urlopen(req, timeout=10)
        print("ralph: gateway token validated successfully", file=sys.stderr)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            print(f"ralph: gateway token rejected (HTTP {exc.code})",
                  file=sys.stderr)
        else:
            print(f"ralph: gateway token validation failed: {exc.reason}",
                  file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"ralph: gateway token validation failed: {exc.reason}",
              file=sys.stderr)
        sys.exit(1)


def _validate_api_key(key, base_url=None):
    """Validate an Anthropic API key via a direct API call.

    Sends a minimal request to the API base URL. Exits on failure.
    """
    api_base = base_url.rstrip("/") if base_url else "https://api.anthropic.com"
    req = urllib.request.Request(
        f"{api_base}/v1/messages",
        method="POST",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        data=json.dumps({
            "model": "claude-haiku-4-5",
            "max_tokens": 4,
            "messages": [{"role": "user", "content": "ok"}],
        }).encode(),
    )

    print(f"ralph: validating API key ({len(key)} chars) against {api_base}...",
          file=sys.stderr)
    try:
        urllib.request.urlopen(req, timeout=10)
        print("ralph: API key validated successfully", file=sys.stderr)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            print(f"ralph: API key rejected by {api_base} (HTTP {exc.code})",
                  file=sys.stderr)
        else:
            print(f"ralph: API key validation failed: {exc.reason}",
                  file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"ralph: API key validation failed: {exc.reason}",
              file=sys.stderr)
        sys.exit(1)


def _parse_and_store_token(agent, raw, auth_mode=None, base_url=None,
                           model_prefix=None):
    """Parse raw token string, store in Keychain, return the data dict."""
    now_ms = int(time.time() * 1000)
    default_expiry = now_ms + DEFAULT_EXPIRY_DAYS * MS_PER_DAY

    # Try to parse as JSON
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "accessToken" in data:
            if "expiresAt" not in data:
                data["expiresAt"] = default_expiry
        else:
            # JSON but not the expected format — treat as bare token
            print("ralph: input JSON missing accessToken, treating as bare token",
                  file=sys.stderr)
            data = {"accessToken": raw, "expiresAt": default_expiry}
    except (json.JSONDecodeError, ValueError):
        # Bare token string
        data = {"accessToken": raw, "expiresAt": default_expiry}

    token = data["accessToken"]
    resolved_mode = _resolve_mode_string(agent, auth_mode)
    mode_config = get_auth_mode(agent, auth_mode)

    if resolved_mode == "api_key":
        # API key mode: validate via direct API call, set far-future expiry
        _validate_api_key(token, base_url=base_url)
        data["expiresAt"] = now_ms + 10 * 365 * MS_PER_DAY
        if base_url:
            data["baseUrl"] = base_url
    elif resolved_mode == "gateway":
        # Gateway mode: validate via Bearer token request, set far-future expiry,
        # store baseUrl and modelPrefix alongside the token
        effective_base_url = base_url or data.get("baseUrl")
        if not effective_base_url:
            print("ralph: gateway mode requires a base URL", file=sys.stderr)
            sys.exit(1)
        effective_model_prefix = model_prefix or data.get("modelPrefix", "")
        _validate_gateway_token(token, effective_base_url, effective_model_prefix)
        data["expiresAt"] = now_ms + 10 * 365 * MS_PER_DAY
        data["baseUrl"] = effective_base_url
        data["modelPrefix"] = effective_model_prefix
    elif resolved_mode == "oauth":
        # OAuth mode: validate via claude -p
        agent_config = get_agent(agent)
        env_var = mode_config["validation_env_var"]
        print(f"ralph: validating token ({len(token)} chars)...", file=sys.stderr)
        result = subprocess.run(
            [agent_config["cli_command"], "-p", "--model", "haiku", "ok"],
            env={**os.environ, env_var: token},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        if result.returncode != 0:
            print("ralph: token validation failed — token is not valid", file=sys.stderr)
            if "401" in result.stderr or "authentication" in result.stderr.lower():
                print("ralph: the token was rejected by the API (401 Unauthorized)", file=sys.stderr)
            sys.exit(1)
        print("ralph: token validated successfully", file=sys.stderr)
    # else: single-mode agent (e.g. cursor) — no validation

    json_str = json.dumps(data)
    write_token_to_keychain(agent, json_str, auth_mode=auth_mode)
    expiry_date = format_expiry_date(data["expiresAt"])
    print(f"ralph: token stored for agent {agent} (expires {expiry_date})")
    return data


def store_token(agent, auth_mode=None):
    """Store a token in Keychain.

    For claude oauth: runs `claude setup-token` interactively, or reads from stdin.
    For claude api_key: prompts for API key interactively, or reads from stdin.
    For claude gateway: prompts for token, base URL, and model prefix interactively,
        or reads a JSON blob from stdin containing accessToken, baseUrl, modelPrefix.
    For other agents: prompts for an API key interactively, or reads from stdin.
    """
    resolved_mode = _resolve_mode_string(agent, auth_mode)
    kw = {}
    if sys.stdin.isatty():
        if resolved_mode == "oauth":
            raw = run_claude_setup_token()
        elif resolved_mode == "api_key":
            raw = prompt_for_api_key(agent)
            kw["base_url"] = prompt_for_base_url()
        elif resolved_mode == "gateway":
            raw = prompt_for_gateway_token()
            kw["base_url"] = prompt_for_gateway_base_url()
            kw["model_prefix"] = prompt_for_model_prefix()
        else:
            # Single-mode agent (e.g. cursor): use original behavior
            agent_config = get_agent(agent)
            if agent_config["uses_proxy"]:
                raw = run_claude_setup_token()
            else:
                raw = prompt_for_api_key(agent)
    else:
        raw = sys.stdin.read().strip()
    if not raw:
        print("ralph: no token provided on stdin", file=sys.stderr)
        sys.exit(1)
    _parse_and_store_token(agent, raw, auth_mode=auth_mode, **kw)


def check_token(agent, auth_mode=None):
    """Check token validity in Keychain. Exit 0 if valid, 1 if expired/missing."""
    data = read_token_from_keychain(agent, auth_mode)
    if data is None:
        resolved_mode = _resolve_mode_string(agent, auth_mode)
        if resolved_mode is not None:
            cli_mode = resolved_mode.replace("_", "-")
            hint = f" — run: ralph store-token --auth {cli_mode}"
        else:
            hint = " — run: ralph store-token"
        print(f"ralph: no token found for agent {agent}{hint}", file=sys.stderr)
        sys.exit(1)

    now_ms = int(time.time() * 1000)
    expires_at = data.get("expiresAt", 0)

    if expires_at > now_ms:
        resolved_mode = _resolve_mode_string(agent, auth_mode)
        if resolved_mode == "api_key":
            print(f"ralph: API key stored for agent {agent}")
        elif resolved_mode == "gateway":
            print(f"ralph: gateway token stored for agent {agent}")
        else:
            expiry_date = format_expiry_date(expires_at)
            remaining_days = int((expires_at - now_ms) / MS_PER_DAY)
            print(f"ralph: token valid for agent {agent}"
                  f" (expires {expiry_date}, {remaining_days} days remaining)")
        sys.exit(0)
    else:
        expiry_date = format_expiry_date(expires_at)
        print(f"ralph: token expired for agent {agent}"
              f" (expired {expiry_date})", file=sys.stderr)
        sys.exit(1)


def get_token(agent, auth_mode=None):
    """Print bare accessToken to stdout. Exit 1 if missing or expired."""
    data = read_token_from_keychain(agent, auth_mode)
    if data is None:
        print(f"ralph: no token found for agent {agent}", file=sys.stderr)
        sys.exit(1)

    now_ms = int(time.time() * 1000)
    expires_at = data.get("expiresAt", 0)

    if expires_at <= now_ms:
        print(f"ralph: token expired for agent {agent}", file=sys.stderr)
        sys.exit(1)

    print(data["accessToken"], end="")


def ensure_token(agent, auth_mode=None):
    """Ensure a valid token exists.

    For claude oauth: runs `claude setup-token` if missing or expired.
    For claude api_key: prompts for API key if missing or expired.
    For claude gateway: prompts for token, base URL, and model prefix if missing or expired.
    For other agents: prompts for an API key if missing or expired.

    Returns a (access_token, token_data) tuple.
    """
    data = read_token_from_keychain(agent, auth_mode)
    now_ms = int(time.time() * 1000)

    if data is not None:
        expires_at = data.get("expiresAt", 0)
        if expires_at > now_ms:
            return data["accessToken"], data

    resolved_mode = _resolve_mode_string(agent, auth_mode)

    if data is not None:
        print(f"ralph: token expired for agent {agent}, requesting new token...",
              file=sys.stderr)
    else:
        print(f"ralph: no token found for agent {agent}, requesting new token...",
              file=sys.stderr)

    if resolved_mode == "oauth":
        raw = run_claude_setup_token()
        stored = _parse_and_store_token(agent, raw, auth_mode=auth_mode)
    elif resolved_mode == "api_key":
        raw = prompt_for_api_key(agent)
        base_url = prompt_for_base_url()
        stored = _parse_and_store_token(agent, raw, auth_mode=auth_mode,
                                        base_url=base_url)
    elif resolved_mode == "gateway":
        raw = prompt_for_gateway_token()
        base_url = prompt_for_gateway_base_url()
        model_prefix = prompt_for_model_prefix()
        stored = _parse_and_store_token(agent, raw, auth_mode=auth_mode,
                                        base_url=base_url,
                                        model_prefix=model_prefix)
    else:
        # Single-mode agent (e.g. cursor)
        agent_config = get_agent(agent)
        if agent_config["uses_proxy"]:
            raw = run_claude_setup_token()
        else:
            raw = prompt_for_api_key(agent)
        stored = _parse_and_store_token(agent, raw, auth_mode=auth_mode)

    return stored["accessToken"], stored
