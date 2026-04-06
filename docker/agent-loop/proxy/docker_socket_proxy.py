#!/usr/bin/env python3
"""docker-socket-proxy — Docker API filtering reverse proxy

Sits between a container and /var/run/docker.sock, filtering API calls
to allow only build-related operations.  All other requests are denied
with 403.

Environment variables:
  LISTEN_PORT    — port to listen on (default: 18081)
  DOCKER_SOCKET  — path to Docker socket (default: /var/run/docker.sock)
  IDLE_TIMEOUT   — seconds of inactivity before self-shutdown (default: 300, 0=disabled)
  PID_FILE       — optional path to write PID
"""

import http.client
import http.server
import os
import re
import socket
import sys

from proxy_base import CHUNK_SIZE, run_proxy_server

# Docker API version prefix pattern: /v1.45/...
_VERSION_PREFIX = re.compile(r"^/v\d+\.\d+")

# Allowlist: (method, compiled path regex)
# Paths are matched AFTER stripping the version prefix.
_ALLOWLIST = [
    ("GET", re.compile(r"^/_ping$")),
    ("GET", re.compile(r"^/version$")),
    ("GET", re.compile(r"^/info$")),
    ("HEAD", re.compile(r"^/_ping$")),
    ("POST", re.compile(r"^/build$")),
    ("POST", re.compile(r"^/images/create$")),
    ("GET", re.compile(r"^/images/")),
    ("GET", re.compile(r"^/images$")),
]


def strip_version_prefix(path):
    """Remove /v1.XX/ prefix from a Docker API path."""
    return _VERSION_PREFIX.sub("", path)


def is_allowed(method, path):
    """Check if a request method + path is on the allowlist."""
    # Strip query string for matching.
    bare_path = path.split("?", 1)[0]
    bare_path = strip_version_prefix(bare_path)
    for allowed_method, pattern in _ALLOWLIST:
        if method == allowed_method and pattern.match(bare_path):
            return True
    return False


class UnixHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection subclass that connects to a Unix domain socket."""

    def __init__(self, socket_path, timeout=300):
        # host is required but unused for Unix sockets.
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._socket_path)


class DockerSocketProxyHandler(http.server.BaseHTTPRequestHandler):
    """Filters Docker API requests and proxies allowed ones to the daemon."""

    # Set by proxy_base.run_proxy_server / main.
    _script_path = os.path.realpath(__file__)
    docker_socket = None
    idle_shutdown = None
    version_hash = None

    # Suppress default stderr request logging.
    def log_message(self, fmt, *args):
        pass

    # --- health endpoint ---------------------------------------------------

    def _handle_health(self):
        body = f"docker-socket-proxy ok v={self.version_hash}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # --- deny response -----------------------------------------------------

    def _deny(self):
        msg = f"docker-socket-proxy: blocked {self.command} {self.path}".encode()
        print(msg.decode(), file=sys.stderr)
        self.send_response(403)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg)

    # --- request handlers --------------------------------------------------

    def do_GET(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_PUT(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()

    def _dispatch(self):
        if self.idle_shutdown:
            self.idle_shutdown.reset()

        # Health endpoint is handled locally, not proxied.
        if self.path == "/health":
            self._handle_health()
            return

        if not is_allowed(self.command, self.path):
            # Drain request body if Content-Length is set.  For chunked
            # requests (no Content-Length) we force-close the connection
            # instead of trying to parse the chunked stream.
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length:
                self.rfile.read(content_length)
            else:
                self.close_connection = True
            self._deny()
            return

        self._proxy()

    # --- proxy logic -------------------------------------------------------

    def _proxy(self):
        conn = None
        try:
            conn = UnixHTTPConnection(self.docker_socket)
            conn.connect()

            # Forward the request line.
            conn.putrequest(self.command, self.path, skip_host=True)

            # Forward headers, tracking whether body is chunked.
            chunked = False
            content_length = None
            for key, value in self.headers.items():
                low = key.lower()
                if low == "host":
                    # Docker daemon doesn't care about Host, but send it.
                    conn.putheader(key, "localhost")
                    continue
                if low == "transfer-encoding" and "chunked" in value.lower():
                    chunked = True
                if low == "content-length":
                    content_length = int(value)
                conn.putheader(key, value)
            conn.endheaders()

            # Stream request body.
            if chunked:
                self._stream_chunked_request(conn)
            elif content_length and content_length > 0:
                remaining = content_length
                while remaining > 0:
                    chunk = self.rfile.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    conn.sock.sendall(chunk)
                    remaining -= len(chunk)

            # Read the response.
            resp = conn.getresponse()
            self._stream_response(resp)

        except (OSError, http.client.HTTPException, ValueError) as exc:
            print(f"docker-socket-proxy: {self.command} {self.path} → "
                  f"upstream error: {exc}", file=sys.stderr)
            body = f"docker-socket-proxy: upstream error: {exc}".encode()
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            if conn:
                conn.close()

    def _stream_chunked_request(self, conn):
        """Read and forward a chunked request body."""
        while True:
            # Read chunk size line.
            size_line = self.rfile.readline()
            if not size_line:
                break
            conn.sock.sendall(size_line)

            chunk_size = int(size_line.strip(), 16)
            if chunk_size == 0:
                # Terminal chunk — read and forward trailing CRLF.
                trailing = self.rfile.readline()
                conn.sock.sendall(trailing)
                break

            # Read and forward chunk data + trailing CRLF.
            remaining = chunk_size + 2  # +2 for \r\n
            while remaining > 0:
                data = self.rfile.read(min(CHUNK_SIZE, remaining))
                if not data:
                    break
                conn.sock.sendall(data)
                remaining -= len(data)

    def _stream_response(self, resp):
        """Stream the upstream response back to the client."""
        self.send_response(resp.status)

        # Check if response is chunked.
        is_chunked = False
        for key, value in resp.getheaders():
            low = key.lower()
            # Skip hop-by-hop headers that Python's HTTP server manages.
            if low == "connection":
                continue
            if low == "transfer-encoding" and "chunked" in value.lower():
                is_chunked = True
                # Don't forward chunked encoding — we send full body.
                continue
            self.send_header(key, value)
        self.end_headers()

        # Stream body in chunks.
        while True:
            chunk = resp.read(CHUNK_SIZE)
            if not chunk:
                break
            self.wfile.write(chunk)


def main():
    port = int(os.environ.get("LISTEN_PORT", "18081"))
    docker_socket = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
    idle_timeout = int(os.environ.get("IDLE_TIMEOUT", "300"))
    pid_file = os.environ.get("PID_FILE", "")

    DockerSocketProxyHandler.docker_socket = docker_socket

    run_proxy_server(
        name="docker-socket-proxy",
        port=port,
        handler_class=DockerSocketProxyHandler,
        idle_timeout=idle_timeout,
        pid_file=pid_file,
    )


if __name__ == "__main__":
    main()
