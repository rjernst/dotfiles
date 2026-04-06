"""Shared infrastructure for proxy scripts.

Provides the common server, idle-shutdown, and version-hash helpers used
by both docker_socket_proxy.py and network_proxy.py.
"""

import hashlib
import http.server
import os
import signal
import socket
import socketserver
import sys
import threading

CHUNK_SIZE = 16384


def compute_version_hash(script_path=None):
    """Hash a script's source and return a 12-char hex version string.

    If script_path is None, hashes the caller's __file__ (via the
    ``path`` argument to the caller).
    """
    path = os.path.realpath(script_path or __file__)
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


class IdleShutdown:
    """Shuts down an HTTPServer after a period of inactivity."""

    def __init__(self, timeout, server, name="proxy"):
        self.timeout = timeout
        self.server = server
        self._name = name
        self._timer = None

    def reset(self):
        """Reset the idle countdown. Call on every request."""
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self.timeout, self._shutdown)
        self._timer.daemon = True
        self._timer.start()

    def _shutdown(self):
        print(f"{self._name}: idle for {self.timeout}s, shutting down",
              file=sys.stderr)
        self.server.shutdown()


class DualStackHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Threaded HTTPServer that accepts both IPv4 and IPv6 connections."""

    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def run_proxy_server(name, port, handler_class, idle_timeout, pid_file,
                     setup_handler=None):
    """Common main loop for proxy servers.

    Args:
        name: display name for log messages (e.g. "docker-socket-proxy")
        port: port to listen on
        handler_class: BaseHTTPRequestHandler subclass
        idle_timeout: seconds before idle shutdown (0 = disabled)
        pid_file: path to write PID (empty string = skip)
        setup_handler: optional callback(handler_class, version) called
            before the server starts, for handler-specific setup
    """
    version = compute_version_hash(
        getattr(handler_class, '_script_path', None))
    handler_class.version_hash = version

    if setup_handler:
        setup_handler(handler_class, version)

    server = DualStackHTTPServer(("::", port), handler_class)

    if pid_file:
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))

    def _cleanup_and_exit(signum, frame):
        print(f"{name}: received SIGTERM, shutting down", file=sys.stderr)
        server.shutdown()

    signal.signal(signal.SIGTERM, _cleanup_and_exit)

    actual_port = server.server_address[1]
    if idle_timeout > 0:
        idle = IdleShutdown(idle_timeout, server, name=name)
        handler_class.idle_shutdown = idle
        idle.reset()
        extra = _format_extra(handler_class)
        print(f"{name}: listening on :{actual_port}, "
              f"{extra}idle_timeout={idle_timeout}s, "
              f"v={version}", file=sys.stderr)
    else:
        extra = _format_extra(handler_class)
        print(f"{name}: listening on :{actual_port}, "
              f"{extra}v={version}", file=sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if pid_file:
            try:
                os.unlink(pid_file)
            except OSError:
                pass


def _format_extra(handler_class):
    """Build extra info string for startup log from handler class attributes."""
    parts = []
    if hasattr(handler_class, 'docker_socket') and handler_class.docker_socket:
        parts.append(f"socket={handler_class.docker_socket}")
    if hasattr(handler_class, 'allowed_hosts'):
        hosts = handler_class.allowed_hosts
        if isinstance(hosts, frozenset):
            display = ",".join(sorted(hosts)) if hosts else "(none)"
            parts.append(f"hosts={display}")
    if parts:
        return ", ".join(parts) + ", "
    return ""
