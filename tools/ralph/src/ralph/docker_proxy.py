"""Docker socket proxy lifecycle management.

Manages a filtered Docker API proxy that sits between containers and
/var/run/docker.sock, allowing only build-related operations.
Follows the same lifecycle pattern as the credential injection proxy.
"""

import fcntl
import http.client
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request

from ralph.proxy import DEFAULT_PROXY_LISTEN_ADDR, proxy_script_version

# Default port for the Docker socket proxy.
DOCKER_PROXY_PORT = 18081

PID_FILE = "/tmp/ralph-docker-proxy.pid"
LOCK_FILE = "/tmp/ralph-docker-proxy.lock"
LOG_FILE = "/tmp/ralph-docker-proxy.log"

# Unix socket the proxy listens on for runtimes that cannot reach a host
# TCP port (the nono runtime grants this path with --allow-unix-socket).
DOCKER_PROXY_SOCKET = os.path.expanduser("~/.ralph/docker-proxy.sock")


def docker_proxy_script_path(dotfiles_dir):
    """Return the path to docker_socket_proxy.py."""
    return os.path.join(
        dotfiles_dir, "docker", "agent-loop", "proxy", "docker_socket_proxy.py"
    )


def compute_docker_proxy_version(dotfiles_dir):
    """Hash docker_socket_proxy.py source and return a 12-char hex version string."""
    return proxy_script_version(docker_proxy_script_path(dotfiles_dir))


def docker_proxy_health_check(port):
    """Check if the Docker socket proxy is healthy at the given port.

    Returns (healthy, version, addr) where version is the v=<hash> string
    and addr is the addr=<addr> listen address from the health response.
    Any field absent from the response comes back as None.
    """
    try:
        resp = urllib.request.urlopen(
            f"http://localhost:{port}/health", timeout=3
        )
        if resp.status != 200:
            return False, None, None
        body = resp.read().decode().strip()
        # Parse "docker-socket-proxy ok v=<hash> addr=<addr>"
        m = re.search(r"v=([a-f0-9]+)", body)
        version = m.group(1) if m else None
        m = re.search(r"addr=(\S+)", body)
        addr = m.group(1) if m else None
        return True, version, addr
    except Exception:
        return False, None, None


def start_docker_proxy(port, dotfiles_dir,
                       listen_addr=DEFAULT_PROXY_LISTEN_ADDR):
    """Start the Docker socket proxy as a native subprocess.

    No stdin token is needed — this proxy has no secrets, just endpoint
    filtering.  Stderr goes to a log file for debugging.

    Returns the subprocess.Popen object.
    """
    script = docker_proxy_script_path(dotfiles_dir)

    print(f"ralph: starting docker socket proxy on {listen_addr}:{port}...")
    log_fh = open(LOG_FILE, "a")
    proc = subprocess.Popen(
        ["python3", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=log_fh,
        env={
            **os.environ,
            "LISTEN_PORT": str(port),
            "LISTEN_ADDR": listen_addr,
            "PID_FILE": PID_FILE,
        },
    )
    log_fh.close()

    return proc


def stop_docker_proxy(wait=False, pid_file=None):
    """Stop the Docker socket proxy via SIGTERM to its PID.

    If wait=True, block until the process exits (up to 5 seconds,
    then SIGKILL).
    """
    try:
        with open(pid_file or PID_FILE) as f:
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


def ensure_docker_proxy(port, dotfiles_dir,
                        listen_addr=DEFAULT_PROXY_LISTEN_ADDR):
    """Ensure the Docker socket proxy is running and healthy.

    If a proxy is already running and healthy on the requested listen
    address, reuse it (even if outdated — the idle timeout will retire it
    naturally).  If it is bound to a different address, restart it.
    Otherwise start a new one.

    Uses a file lock to serialize proxy lifecycle management across
    concurrent ralph instances sharing the same port.

    Returns the proxy port.
    """
    with open(LOCK_FILE, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)

        healthy, version, running_addr = docker_proxy_health_check(port)
        if healthy and running_addr == listen_addr:
            current = compute_docker_proxy_version(dotfiles_dir)
            if version == current:
                print(f"ralph: reusing healthy docker socket proxy on port {port}")
            else:
                print(
                    f"ralph: reusing docker socket proxy on port {port} "
                    f"(outdated v={version}, current v={current})"
                )
            return port

        if healthy:
            print(
                f"ralph: docker socket proxy listening on "
                f"{running_addr or 'unknown'}, restarting on {listen_addr}"
            )

        # Kill any lingering proxy process so the port is free.
        stop_docker_proxy(wait=True)
        start_docker_proxy(port, dotfiles_dir, listen_addr)

        # Wait for health check
        for _ in range(10):
            time.sleep(0.5)
            healthy, _, _ = docker_proxy_health_check(port)
            if healthy:
                return port

    print("ralph: docker socket proxy failed to become healthy", file=sys.stderr)
    _dump_log_tail(LOG_FILE)
    stop_docker_proxy()
    sys.exit(1)


def _dump_log_tail(log_file):
    """Print the last 20 lines of a proxy log file to stderr."""
    if not os.path.isfile(log_file):
        return
    try:
        with open(log_file) as f:
            lines = f.readlines()
    except OSError:
        return
    for line in lines[-20:]:
        print(line, end="", file=sys.stderr)


# ---------------------------------------------------------------------------
# Unix socket mode
# ---------------------------------------------------------------------------


def docker_proxy_socket_state_paths(socket_path):
    """Return (pid_file, lock_file, log_file) for a Unix socket proxy.

    They live beside the socket so a caller that overrides the socket path
    (tests, an alternate HOME) gets a matching set of state files.
    """
    directory = os.path.dirname(socket_path)
    return (
        os.path.join(directory, "docker-proxy-sock.pid"),
        os.path.join(directory, "docker-proxy-sock.lock"),
        os.path.join(directory, "docker-proxy-sock.log"),
    )


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that talks to a Unix domain socket."""

    def __init__(self, socket_path, timeout=3):
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._socket_path)


def docker_proxy_socket_health_check(socket_path):
    """Check the Docker socket proxy over its Unix socket.

    Returns (healthy, version, addr) mirroring docker_proxy_health_check;
    addr is the ``addr=unix:<path>`` field from the health response.
    """
    conn = None
    try:
        conn = _UnixHTTPConnection(socket_path)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        if resp.status != 200:
            return False, None, None
        body = resp.read().decode().strip()
        m = re.search(r"v=([a-f0-9]+)", body)
        version = m.group(1) if m else None
        m = re.search(r"addr=(\S+)", body)
        addr = m.group(1) if m else None
        return True, version, addr
    except Exception:
        return False, None, None
    finally:
        if conn:
            conn.close()


def start_docker_proxy_socket(socket_path, dotfiles_dir):
    """Start the Docker socket proxy listening on a Unix socket.

    Returns the subprocess.Popen object.
    """
    script = docker_proxy_script_path(dotfiles_dir)
    pid_file, _, log_file = docker_proxy_socket_state_paths(socket_path)

    print(f"ralph: starting docker socket proxy on unix:{socket_path}...")
    log_fh = open(log_file, "a")
    proc = subprocess.Popen(
        ["python3", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=log_fh,
        env={
            **os.environ,
            "LISTEN_SOCKET": socket_path,
            "PID_FILE": pid_file,
        },
    )
    log_fh.close()

    return proc


def ensure_docker_proxy_socket(dotfiles_dir, socket_path=None):
    """Ensure the Docker socket proxy is serving on its Unix socket.

    Reuses a running, healthy proxy (even if outdated — the idle timeout
    retires it naturally), otherwise starts a fresh one.  Serialized
    across concurrent ralph instances with a file lock.

    Returns the socket path.
    """
    socket_path = socket_path or DOCKER_PROXY_SOCKET
    pid_file, lock_file, log_file = docker_proxy_socket_state_paths(socket_path)

    # The socket fronts the Docker daemon, so keep its directory private
    # even if something else created it with a laxer mode.
    directory = os.path.dirname(socket_path) or "."
    os.makedirs(directory, exist_ok=True)
    os.chmod(directory, 0o700)

    with open(lock_file, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)

        healthy, version, _ = docker_proxy_socket_health_check(socket_path)
        if healthy:
            current = compute_docker_proxy_version(dotfiles_dir)
            if version == current:
                print("ralph: reusing healthy docker socket proxy on "
                      f"unix:{socket_path}")
            else:
                print(
                    f"ralph: reusing docker socket proxy on unix:{socket_path} "
                    f"(outdated v={version}, current v={current})"
                )
            return socket_path

        # Kill any lingering proxy process so the socket path is free.
        stop_docker_proxy(wait=True, pid_file=pid_file)
        start_docker_proxy_socket(socket_path, dotfiles_dir)

        for _ in range(10):
            time.sleep(0.5)
            healthy, _, _ = docker_proxy_socket_health_check(socket_path)
            if healthy:
                return socket_path

    print("ralph: docker socket proxy failed to become healthy", file=sys.stderr)
    _dump_log_tail(log_file)
    stop_docker_proxy(pid_file=pid_file)
    sys.exit(1)
