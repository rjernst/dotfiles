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

# Bind address used when LISTEN_ADDR is unset: a dual-stack wildcard socket.
DEFAULT_LISTEN_ADDR = "::"


def compute_version_hash(script_path=None):
    """Hash a proxy script and this module, as a 12-char hex string.

    proxy_base is hashed along with the script because the behaviour a
    running proxy reports as ``v=`` comes from both: a fix here would
    otherwise leave every running proxy looking current.

    If script_path is None, only this module is hashed.
    """
    paths = [os.path.realpath(__file__)]
    if script_path:
        paths.insert(0, os.path.realpath(script_path))
    digest = hashlib.sha256()
    for path in paths:
        with open(path, "rb") as f:
            digest.update(f.read())
    return digest.hexdigest()[:12]


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


class IPv6HTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Threaded HTTPServer bound to a single IPv6 address (IPv6-only)."""

    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


class IPv4HTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Threaded HTTPServer bound to a single IPv4 address."""

    address_family = socket.AF_INET
    daemon_threads = True


class UnixStreamHTTPServer(socketserver.ThreadingMixIn,
                           socketserver.UnixStreamServer):
    """Threaded HTTP server listening on a Unix domain socket.

    BaseHTTPRequestHandler expects ``server_name``/``server_port`` on the
    server object; AF_UNIX has neither, so they carry placeholders.
    """

    daemon_threads = True
    server_name = "localhost"
    server_port = 0

    def server_bind(self):
        # Bind under a restrictive umask so the socket is never briefly
        # world-accessible, then pin the mode explicitly.
        old_umask = os.umask(0o177)
        try:
            super().server_bind()
        finally:
            os.umask(old_umask)
        os.chmod(self.server_address, 0o600)
        st = os.stat(self.server_address)
        self._socket_ident = (st.st_dev, st.st_ino)

    def server_close(self):
        super().server_close()
        # Only remove the socket file if it is still the one we bound — a
        # newer proxy may have replaced it at this path, and unlinking that
        # one would leave a live server nobody can reach.
        try:
            st = os.stat(self.server_address)
            if (st.st_dev, st.st_ino) == self._socket_ident:
                os.unlink(self.server_address)
        except OSError:
            pass


def make_server(listen_addr, port, handler_class):
    """Create the HTTP server for a listen address.

    ``::`` binds a dual-stack wildcard socket that also accepts IPv4
    connections.  Any other address containing ``:`` is an IPv6 literal
    and binds IPv6-only; anything else binds an AF_INET socket.
    """
    if listen_addr == DEFAULT_LISTEN_ADDR:
        return DualStackHTTPServer((listen_addr, port), handler_class)
    if ":" in listen_addr:
        return IPv6HTTPServer((listen_addr, port), handler_class)
    return IPv4HTTPServer((listen_addr, port), handler_class)


def make_unix_server(socket_path, handler_class):
    """Create an HTTP server bound to a Unix domain socket.

    A stale socket file left behind by a crashed proxy is removed first.
    """
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass
    return UnixStreamHTTPServer(socket_path, handler_class)


def run_proxy_server(name, port, handler_class, idle_timeout, pid_file,
                     setup_handler=None, listen_addr=DEFAULT_LISTEN_ADDR,
                     listen_socket=None):
    """Common main loop for proxy servers.

    Args:
        name: display name for log messages (e.g. "docker-socket-proxy")
        port: port to listen on (ignored when listen_socket is set)
        handler_class: BaseHTTPRequestHandler subclass
        idle_timeout: seconds before idle shutdown (0 = disabled)
        pid_file: path to write PID (empty string = skip)
        setup_handler: optional callback(handler_class, version) called
            before the server starts, for handler-specific setup
        listen_addr: address to bind (default "::", dual-stack wildcard)
        listen_socket: Unix socket path to bind instead of a TCP port
    """
    version = compute_version_hash(
        getattr(handler_class, '_script_path', None))
    handler_class.version_hash = version

    if listen_socket:
        handler_class.listen_addr = f"unix:{listen_socket}"
    else:
        handler_class.listen_addr = listen_addr

    if setup_handler:
        setup_handler(handler_class, version)

    if listen_socket:
        server = make_unix_server(listen_socket, handler_class)
    else:
        server = make_server(listen_addr, port, handler_class)

    if pid_file:
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))

    def _cleanup_and_exit(signum, frame):
        print(f"{name}: received SIGTERM, shutting down", file=sys.stderr)
        # shutdown() blocks until serve_forever() returns, and this handler
        # runs on the thread sitting in serve_forever() — so request the
        # shutdown from a helper thread to avoid deadlocking against it.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _cleanup_and_exit)

    if listen_socket:
        where = f"unix:{listen_socket}"
    else:
        where = f"{listen_addr}:{server.server_address[1]}"
    if idle_timeout > 0:
        idle = IdleShutdown(idle_timeout, server, name=name)
        handler_class.idle_shutdown = idle
        idle.reset()
        extra = _format_extra(handler_class)
        print(f"{name}: listening on {where}, "
              f"{extra}idle_timeout={idle_timeout}s, "
              f"v={version}", file=sys.stderr)
    else:
        extra = _format_extra(handler_class)
        print(f"{name}: listening on {where}, "
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
