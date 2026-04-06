"""Network proxy lifecycle management.

Manages an HTTP forward proxy that filters outbound requests by hostname
allowlist. Follows the same lifecycle pattern as the credential injection
proxy and Docker socket proxy.
"""

import fcntl
import hashlib
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request

# Default port for the network proxy.
NETWORK_PROXY_PORT = 18082

PID_FILE = "/tmp/ralph-network-proxy.pid"
LOCK_FILE = "/tmp/ralph-network-proxy.lock"
LOG_FILE = "/tmp/ralph-network-proxy.log"


def network_proxy_script_path(dotfiles_dir):
    """Return the path to network_proxy.py."""
    return os.path.join(
        dotfiles_dir, "docker", "agent-loop", "proxy", "network_proxy.py"
    )


def compute_network_proxy_version(dotfiles_dir):
    """Hash network_proxy.py source and return a 12-char hex version string."""
    path = network_proxy_script_path(dotfiles_dir)
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def network_proxy_health_check(port):
    """Check if the network proxy is healthy at the given port.

    Returns (healthy, version, hosts) where version is the v=<hash> string
    and hosts is a frozenset of allowed hostnames from the health response,
    or (False, None, None) if unhealthy.
    """
    try:
        resp = urllib.request.urlopen(
            f"http://localhost:{port}/health", timeout=3
        )
        if resp.status != 200:
            return False, None, None
        body = resp.read().decode().strip()
        # Parse "network-proxy ok hosts=<comma-list> v=<hash>"
        m_version = re.search(r"v=([a-f0-9]+)", body)
        version = m_version.group(1) if m_version else None
        m_hosts = re.search(r"hosts=(\S*)", body)
        if m_hosts:
            hosts_str = m_hosts.group(1)
            hosts = frozenset(
                h.strip().lower() for h in hosts_str.split(",") if h.strip()
            )
        else:
            hosts = frozenset()
        return True, version, hosts
    except Exception:
        return False, None, None


def start_network_proxy(port, dotfiles_dir, allowed_hosts):
    """Start the network proxy as a native subprocess.

    No stdin token is needed — this proxy has no secrets, just hostname
    filtering.  Stderr goes to a log file for debugging.

    Returns the subprocess.Popen object.
    """
    script = network_proxy_script_path(dotfiles_dir)
    hosts_str = ",".join(sorted(allowed_hosts)) if allowed_hosts else ""

    print(f"ralph: starting network proxy on port {port}...")
    log_fh = open(LOG_FILE, "a")
    proc = subprocess.Popen(
        ["python3", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=log_fh,
        env={
            **os.environ,
            "LISTEN_PORT": str(port),
            "ALLOWED_HOSTS": hosts_str,
            "PID_FILE": PID_FILE,
        },
    )
    log_fh.close()

    return proc


def stop_network_proxy(wait=False):
    """Stop the network proxy via SIGTERM to its PID.

    If wait=True, block until the process exits (up to 5 seconds,
    then SIGKILL).
    """
    try:
        with open(PID_FILE) as f:
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


def ensure_network_proxy(port, dotfiles_dir, allowed_hosts):
    """Ensure the network proxy is running and healthy with the given allowlist.

    If a proxy is already running and healthy with the same allowlist, reuse it.
    If the allowlist has changed, stop and restart.  Otherwise start a new one.

    Uses a file lock to serialize proxy lifecycle management across
    concurrent ralph instances sharing the same port.

    Returns the proxy port.
    """
    wanted_hosts = frozenset(
        h.strip().lower() for h in allowed_hosts if h.strip()
    )

    with open(LOCK_FILE, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)

        healthy, version, running_hosts = network_proxy_health_check(port)
        if healthy:
            if running_hosts == wanted_hosts:
                current = compute_network_proxy_version(dotfiles_dir)
                if version == current:
                    print(f"ralph: reusing healthy network proxy on port {port}")
                else:
                    print(
                        f"ralph: reusing network proxy on port {port} "
                        f"(outdated v={version}, current v={current})"
                    )
                return port
            else:
                # Allowlist changed — restart.
                print(
                    f"ralph: restarting network proxy on port {port} "
                    f"(allowlist changed)"
                )
                stop_network_proxy(wait=True)

        else:
            # Kill any lingering proxy process so the port is free.
            stop_network_proxy(wait=True)

        start_network_proxy(port, dotfiles_dir, sorted(wanted_hosts))

        # Wait for health check
        for _ in range(10):
            time.sleep(0.5)
            healthy, _, _ = network_proxy_health_check(port)
            if healthy:
                return port

    print("ralph: network proxy failed to become healthy", file=sys.stderr)
    if os.path.isfile(LOG_FILE):
        try:
            with open(LOG_FILE) as f:
                lines = f.readlines()
            for line in lines[-20:]:
                print(line, end="", file=sys.stderr)
        except OSError:
            pass
    stop_network_proxy()
    sys.exit(1)
