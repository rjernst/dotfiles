"""Token management for the credential injection proxy."""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time

from ralph.agents import get_agent

MS_PER_DAY = 86400 * 1000
DEFAULT_EXPIRY_DAYS = 365


def keychain_service_name(agent):
    """Return the Keychain service name for the given agent."""
    return f"{agent}-token"


def read_token_from_keychain(agent):
    """Read and parse the token JSON from macOS Keychain.

    Returns the parsed dict, or None if not found.
    """
    service = keychain_service_name(agent)
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


def write_token_to_keychain(agent, json_str):
    """Write token JSON string to macOS Keychain."""
    service = keychain_service_name(agent)
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

    Used for agents that use simple API keys (e.g. cursor) rather than
    OAuth token flows.
    """
    print(f"Enter your {agent} API key:", file=sys.stderr)
    try:
        raw = input().strip()
    except EOFError:
        raw = ""
    if not raw:
        print(f"ralph: no API key provided for agent {agent}", file=sys.stderr)
        sys.exit(1)
    return raw


def _parse_and_store_token(agent, raw):
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

    # Validate the token before storing (claude only — cursor API keys are
    # long-lived and can't be validated without a full agent run)
    agent_config = get_agent(agent)
    token = data["accessToken"]
    if agent_config["uses_proxy"]:
        print(f"ralph: validating token ({len(token)} chars)...", file=sys.stderr)
        result = subprocess.run(
            [agent_config["cli_command"], "-p", "--model", "haiku", "ok"],
            env={**os.environ, agent_config["env_var_name"]: token},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        if result.returncode != 0:
            print("ralph: token validation failed — token is not valid", file=sys.stderr)
            if "401" in result.stderr or "authentication" in result.stderr.lower():
                print("ralph: the token was rejected by the API (401 Unauthorized)", file=sys.stderr)
            sys.exit(1)
        print("ralph: token validated successfully", file=sys.stderr)

    json_str = json.dumps(data)
    write_token_to_keychain(agent, json_str)
    expiry_date = format_expiry_date(data["expiresAt"])
    print(f"ralph: token stored for agent {agent} (expires {expiry_date})")
    return data


def store_token(agent):
    """Store a token in Keychain.

    For claude: runs `claude setup-token` interactively, or reads from stdin.
    For other agents: prompts for an API key interactively, or reads from stdin.
    """
    agent_config = get_agent(agent)
    if sys.stdin.isatty():
        if agent_config["uses_proxy"]:
            raw = run_claude_setup_token()
        else:
            raw = prompt_for_api_key(agent)
    else:
        raw = sys.stdin.read().strip()
    if not raw:
        print("ralph: no token provided on stdin", file=sys.stderr)
        sys.exit(1)
    _parse_and_store_token(agent, raw)


def check_token(agent):
    """Check token validity in Keychain. Exit 0 if valid, 1 if expired/missing."""
    data = read_token_from_keychain(agent)
    if data is None:
        print(f"ralph: no token found for agent {agent}"
              " — run: ralph store-token", file=sys.stderr)
        sys.exit(1)

    now_ms = int(time.time() * 1000)
    expires_at = data.get("expiresAt", 0)
    expiry_date = format_expiry_date(expires_at)

    if expires_at > now_ms:
        remaining_days = int((expires_at - now_ms) / MS_PER_DAY)
        print(f"ralph: token valid for agent {agent}"
              f" (expires {expiry_date}, {remaining_days} days remaining)")
        sys.exit(0)
    else:
        print(f"ralph: token expired for agent {agent}"
              f" (expired {expiry_date})", file=sys.stderr)
        sys.exit(1)


def get_token(agent):
    """Print bare accessToken to stdout. Exit 1 if missing or expired."""
    data = read_token_from_keychain(agent)
    if data is None:
        print(f"ralph: no token found for agent {agent}", file=sys.stderr)
        sys.exit(1)

    now_ms = int(time.time() * 1000)
    expires_at = data.get("expiresAt", 0)

    if expires_at <= now_ms:
        print(f"ralph: token expired for agent {agent}", file=sys.stderr)
        sys.exit(1)

    print(data["accessToken"], end="")


def ensure_token(agent):
    """Ensure a valid token exists.

    For claude: runs `claude setup-token` if missing or expired.
    For other agents: prompts for an API key if missing or expired.
    """
    data = read_token_from_keychain(agent)
    now_ms = int(time.time() * 1000)

    if data is not None:
        expires_at = data.get("expiresAt", 0)
        if expires_at > now_ms:
            return data["accessToken"]

    agent_config = get_agent(agent)
    if data is not None:
        print(f"ralph: token expired for agent {agent}, requesting new token...",
              file=sys.stderr)
    else:
        print(f"ralph: no token found for agent {agent}, requesting new token...",
              file=sys.stderr)

    if agent_config["uses_proxy"]:
        raw = run_claude_setup_token()
    else:
        raw = prompt_for_api_key(agent)

    stored = _parse_and_store_token(agent, raw)
    return stored["accessToken"]
