"""Proxy lifecycle management for the credential injection proxy."""

import fcntl
import hashlib
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request

from ralph.token import read_token_from_keychain

# Default port for the credential injection proxy, keyed by agent name.
DEFAULT_PROXY_PORTS = {"claude": 18080}
DEFAULT_PROXY_PORT = 18080

# Map short model aliases to full model IDs for ANTHROPIC_CUSTOM_MODEL_OPTION.
MODEL_ALIASES = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}


def build_proxy_env(auth_mode, proxy_host, proxy_port, model=None):
    """Build sandbox env vars for credential injection proxy communication.

    Returns a dict of environment variables. For oauth mode, includes
    CLAUDE_CODE_OAUTH_TOKEN and ANTHROPIC_CUSTOM_MODEL_OPTION (if model is
    provided). For api_key mode, includes ANTHROPIC_API_KEY. Both include
    ANTHROPIC_BASE_URL.

    Note: api_key mode does not set ANTHROPIC_CUSTOM_MODEL_OPTION because
    API key auth uses standard Anthropic API validation, which doesn't
    require a model override to bypass subscription tier checks (unlike
    oauth phantom tokens which have no tier metadata).
    """
    base_url = f"http://{proxy_host}:{proxy_port}"
    if auth_mode == "api_key":
        return {
            "ANTHROPIC_API_KEY": "phantom",
            "ANTHROPIC_BASE_URL": base_url,
        }
    # OAuth mode (default)
    env = {
        "CLAUDE_CODE_OAUTH_TOKEN": "phantom",
        "ANTHROPIC_BASE_URL": base_url,
    }
    if model:
        env["ANTHROPIC_CUSTOM_MODEL_OPTION"] = MODEL_ALIASES.get(model, model)
    return env


def proxy_script_path(dotfiles_dir):
    """Return the path to proxy.py."""
    return os.path.join(dotfiles_dir, "docker", "agent-loop", "proxy", "proxy.py")


def compute_proxy_version(dotfiles_dir):
    """Hash proxy.py source and return a 12-char hex version string."""
    path = proxy_script_path(dotfiles_dir)
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def proxy_pid_file(agent):
    """Return the PID file path for the proxy."""
    return f"/tmp/ralph-proxy-{agent}.pid"


def proxy_lock_file(agent):
    """Return the lock file path for proxy lifecycle serialization."""
    return f"/tmp/ralph-proxy-{agent}.lock"


def proxy_log_file(agent):
    """Return the log file path for the proxy."""
    return f"/tmp/ralph-proxy-{agent}.log"


def proxy_port_for_agent(agent):
    """Return the proxy port for the given agent."""
    return DEFAULT_PROXY_PORTS.get(agent, DEFAULT_PROXY_PORT)


def proxy_health_check(port):
    """Check if the proxy is healthy at the given port.

    Returns (healthy, version, mode) where version is the v=<hash> string
    and mode is the mode=<mode> string from the health response, or None
    if unhealthy/absent.
    """
    try:
        resp = urllib.request.urlopen(
            f"http://localhost:{port}/health", timeout=3)
        if resp.status != 200:
            return False, None, None
        body = resp.read().decode().strip()
        # Parse "agent-loop-proxy ok v=<hash> mode=<mode>"
        m = re.search(r'v=([a-f0-9]+)', body)
        version = m.group(1) if m else None
        m = re.search(r'mode=(\w+)', body)
        mode = m.group(1) if m else None
        return True, version, mode
    except Exception:
        return False, None, None


def start_proxy(agent, port, dotfiles_dir, auth_mode=None):
    """Start the credential injection proxy as a native subprocess.

    Reads the token from Keychain and pipes it via stdin.
    Stderr goes to a log file for debugging.

    Args:
        agent: agent name (e.g. "claude")
        port: port to listen on
        dotfiles_dir: path to the dotfiles repository
        auth_mode: auth mode string (e.g. "oauth", "api_key")

    Returns the subprocess.Popen object.
    """
    token_data = read_token_from_keychain(agent, auth_mode)
    resolved_mode = auth_mode or "oauth"
    cli_mode = resolved_mode.replace("_", "-")
    if token_data is None:
        print(f"ralph: no {resolved_mode} credentials for agent {agent}"
              f" — run: ralph store-token --auth {cli_mode}", file=sys.stderr)
        sys.exit(1)

    now_ms = int(time.time() * 1000)
    if token_data.get("expiresAt", 0) <= now_ms:
        print(f"ralph: {resolved_mode} credentials expired for agent {agent}"
              f" — run: ralph store-token --auth {cli_mode}", file=sys.stderr)
        sys.exit(1)

    token = token_data["accessToken"]
    script = proxy_script_path(dotfiles_dir)
    pid_file = proxy_pid_file(agent)
    log_file = proxy_log_file(agent)

    print(f"ralph: starting proxy on port {port}...")
    log_fh = open(log_file, "a")
    proc = subprocess.Popen(
        ["python3", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=log_fh,
        env={**os.environ,
             "LISTEN_PORT": str(port),
             "PID_FILE": pid_file},
    )
    proc.stdin.write(f"{resolved_mode}\n{token}\n".encode())
    proc.stdin.close()
    log_fh.close()

    return proc


def stop_proxy(agent, wait=False):
    """Stop the proxy for the given agent via SIGTERM to its PID.

    If wait=True, block until the process exits (up to 5 seconds,
    then SIGKILL).
    """
    pid_file = proxy_pid_file(agent)
    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
    except (FileNotFoundError, ValueError, ProcessLookupError, OSError):
        return

    if not wait:
        return

    # Wait for the process to exit so the port is released.
    for _ in range(50):  # 5 seconds
        time.sleep(0.1)
        try:
            os.kill(pid, 0)  # check if still alive
        except ProcessLookupError:
            return
        except OSError:
            return
    # Still alive — force kill
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def start_proxy_keepalive(port, interval=60):
    """Start a background thread that pings the proxy to prevent idle shutdown.

    The proxy's idle timer resets on any request. This sends periodic /health
    requests so the proxy stays alive while ralph is running, even during long
    gaps between API calls (e.g., while Claude Code runs tests).

    Returns an Event that can be set to stop the keepalive thread.
    """
    stop = threading.Event()

    def _keepalive():
        while not stop.wait(interval):
            try:
                urllib.request.urlopen(
                    f"http://localhost:{port}/health", timeout=5)
            except Exception:
                pass

    t = threading.Thread(target=_keepalive, daemon=True)
    t.start()
    return stop


def ensure_proxy(agent, port, dotfiles_dir, auth_mode=None):
    """Ensure the proxy is running and healthy in the requested mode.

    If a proxy is already running and healthy in the same mode, reuse it
    (even if outdated — the idle timeout will retire it naturally).
    If running in a different mode, restart it in the requested mode.
    Otherwise start a new one.

    Uses a file lock to serialize proxy lifecycle management across
    concurrent ralph instances sharing the same port.

    Returns the proxy port.
    """
    resolved_mode = auth_mode or "oauth"
    lock_path = proxy_lock_file(agent)
    with open(lock_path, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)

        healthy, version, running_mode = proxy_health_check(port)
        if healthy:
            if running_mode == resolved_mode:
                current = compute_proxy_version(dotfiles_dir)
                if version == current:
                    print(f"ralph: reusing healthy proxy on port {port}")
                else:
                    print(f"ralph: reusing proxy on port {port} "
                          f"(outdated v={version}, current v={current})")
                return port
            else:
                # Mode mismatch — restart in the requested mode
                print(f"ralph: proxy running in {running_mode or 'unknown'} mode, "
                      f"restarting in {resolved_mode} mode")
                stop_proxy(agent, wait=True)

        if not healthy:
            # Kill any lingering proxy process so the port is free.
            stop_proxy(agent, wait=True)

        start_proxy(agent, port, dotfiles_dir, auth_mode)

        # Wait for health check
        for _ in range(10):
            time.sleep(0.5)
            healthy, _, _ = proxy_health_check(port)
            if healthy:
                return port

    print("ralph: proxy failed to become healthy", file=sys.stderr)
    log = proxy_log_file(agent)
    if os.path.isfile(log):
        try:
            with open(log) as f:
                lines = f.readlines()
            for line in lines[-20:]:
                print(line, end="", file=sys.stderr)
        except OSError:
            pass
    stop_proxy(agent)
    sys.exit(1)
