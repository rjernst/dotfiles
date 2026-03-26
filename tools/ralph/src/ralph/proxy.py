"""Proxy lifecycle management for the credential injection proxy."""

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


def proxy_log_file(agent):
    """Return the log file path for the proxy."""
    return f"/tmp/ralph-proxy-{agent}.log"


def proxy_port_for_agent(agent):
    """Return the proxy port for the given agent."""
    return DEFAULT_PROXY_PORTS.get(agent, DEFAULT_PROXY_PORT)


def proxy_health_check(port):
    """Check if the proxy is healthy at the given port.

    Returns (healthy, version) where version is the v=<hash> string
    from the health response, or None if unhealthy.
    """
    try:
        resp = urllib.request.urlopen(
            f"http://localhost:{port}/health", timeout=3)
        if resp.status != 200:
            return False, None
        body = resp.read().decode().strip()
        # Parse "agent-loop-proxy ok v=<hash>"
        m = re.search(r'v=([a-f0-9]+)', body)
        version = m.group(1) if m else None
        return True, version
    except Exception:
        return False, None


def start_proxy(agent, port, dotfiles_dir):
    """Start the credential injection proxy as a native subprocess.

    Reads the token from Keychain and pipes it via stdin.
    Stderr goes to a log file for debugging.

    Returns the subprocess.Popen object.
    """
    token_data = read_token_from_keychain(agent)
    if token_data is None:
        print(f"ralph: no token found for agent {agent}"
              " — run: ralph store-token", file=sys.stderr)
        sys.exit(1)

    now_ms = int(time.time() * 1000)
    if token_data.get("expiresAt", 0) <= now_ms:
        print(f"ralph: token expired for agent {agent}"
              " — run: ralph store-token", file=sys.stderr)
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
    proc.stdin.write((token + "\n").encode())
    proc.stdin.close()
    log_fh.close()

    return proc


def stop_proxy(agent):
    """Stop the proxy for the given agent via SIGTERM to its PID."""
    pid_file = proxy_pid_file(agent)
    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
    except (FileNotFoundError, ValueError, ProcessLookupError, OSError):
        pass


def start_proxy_keepalive(port, interval=60):
    """Start a background thread that pings the proxy to prevent idle shutdown.

    The proxy's idle timer resets on any request. This sends periodic /health
    requests so the proxy stays alive while ralph is running, even during long
    gaps between API calls (e.g., while Claude Code runs tests).

    NOTE: This piggybacks on /health to reset the idle timer. If we later need
    external monitoring that shouldn't extend proxy lifetime, split this into a
    dedicated /keepalive endpoint.

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


def ensure_proxy(agent, port, dotfiles_dir):
    """Ensure the proxy is running and healthy.

    If a proxy is already running and healthy, reuse it (even if outdated —
    the idle timeout will retire it naturally). Otherwise start a new one.

    Returns the proxy port.
    """
    healthy, version = proxy_health_check(port)
    if healthy:
        current = compute_proxy_version(dotfiles_dir)
        if version == current:
            print(f"ralph: reusing healthy proxy on port {port}")
        else:
            print(f"ralph: reusing proxy on port {port} "
                  f"(outdated v={version}, current v={current})")
        return port

    start_proxy(agent, port, dotfiles_dir)

    # Wait for health check
    for _ in range(10):
        time.sleep(0.5)
        healthy, _ = proxy_health_check(port)
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
