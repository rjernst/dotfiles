"""Docker socket proxy lifecycle management.

Manages a filtered Docker API proxy that sits between containers and
/var/run/docker.sock, allowing only build-related operations.
Follows the same lifecycle pattern as the credential injection proxy.
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

# Default port for the Docker socket proxy.
DOCKER_PROXY_PORT = 18081

PID_FILE = "/tmp/ralph-docker-proxy.pid"
LOCK_FILE = "/tmp/ralph-docker-proxy.lock"
LOG_FILE = "/tmp/ralph-docker-proxy.log"


def docker_proxy_script_path(dotfiles_dir):
    """Return the path to docker_socket_proxy.py."""
    return os.path.join(
        dotfiles_dir, "docker", "agent-loop", "proxy", "docker_socket_proxy.py"
    )


def compute_docker_proxy_version(dotfiles_dir):
    """Hash docker_socket_proxy.py source and return a 12-char hex version string."""
    path = docker_proxy_script_path(dotfiles_dir)
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def docker_proxy_health_check(port):
    """Check if the Docker socket proxy is healthy at the given port.

    Returns (healthy, version) where version is the v=<hash> string
    from the health response, or None if unhealthy.
    """
    try:
        resp = urllib.request.urlopen(
            f"http://localhost:{port}/health", timeout=3
        )
        if resp.status != 200:
            return False, None
        body = resp.read().decode().strip()
        # Parse "docker-socket-proxy ok v=<hash>"
        m = re.search(r"v=([a-f0-9]+)", body)
        version = m.group(1) if m else None
        return True, version
    except Exception:
        return False, None


def start_docker_proxy(port, dotfiles_dir):
    """Start the Docker socket proxy as a native subprocess.

    No stdin token is needed — this proxy has no secrets, just endpoint
    filtering.  Stderr goes to a log file for debugging.

    Returns the subprocess.Popen object.
    """
    script = docker_proxy_script_path(dotfiles_dir)

    print(f"ralph: starting docker socket proxy on port {port}...")
    log_fh = open(LOG_FILE, "a")
    proc = subprocess.Popen(
        ["python3", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=log_fh,
        env={
            **os.environ,
            "LISTEN_PORT": str(port),
            "PID_FILE": PID_FILE,
        },
    )
    log_fh.close()

    return proc


def stop_docker_proxy(wait=False):
    """Stop the Docker socket proxy via SIGTERM to its PID.

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


def ensure_docker_proxy(port, dotfiles_dir):
    """Ensure the Docker socket proxy is running and healthy.

    If a proxy is already running and healthy, reuse it (even if outdated —
    the idle timeout will retire it naturally).  Otherwise start a new one.

    Uses a file lock to serialize proxy lifecycle management across
    concurrent ralph instances sharing the same port.

    Returns the proxy port.
    """
    with open(LOCK_FILE, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)

        healthy, version = docker_proxy_health_check(port)
        if healthy:
            current = compute_docker_proxy_version(dotfiles_dir)
            if version == current:
                print(f"ralph: reusing healthy docker socket proxy on port {port}")
            else:
                print(
                    f"ralph: reusing docker socket proxy on port {port} "
                    f"(outdated v={version}, current v={current})"
                )
            return port

        # Kill any lingering proxy process so the port is free.
        stop_docker_proxy(wait=True)
        start_docker_proxy(port, dotfiles_dir)

        # Wait for health check
        for _ in range(10):
            time.sleep(0.5)
            healthy, _ = docker_proxy_health_check(port)
            if healthy:
                return port

    print("ralph: docker socket proxy failed to become healthy", file=sys.stderr)
    if os.path.isfile(LOG_FILE):
        try:
            with open(LOG_FILE) as f:
                lines = f.readlines()
            for line in lines[-20:]:
                print(line, end="", file=sys.stderr)
        except OSError:
            pass
    stop_docker_proxy()
    sys.exit(1)
