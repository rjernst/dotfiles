#!/usr/bin/env python3
"""agent-loop-proxy — credential injection reverse proxy

Reads an auth mode and credential from stdin (two lines), then proxies
HTTP requests to a configurable upstream target, injecting the real
credential in the appropriate header.  The real credential never touches
disk, logs, or env vars.

Supported modes:
  oauth   — replaces the Authorization header with Bearer <credential>
  api_key — replaces the x-api-key header with <credential>

Environment variables:
  LISTEN_PORT   — port to listen on (default: 18080)
  TARGET        — upstream base URL (default: https://api.anthropic.com)
  IDLE_TIMEOUT  — seconds of inactivity before self-shutdown (default: 300, 0=disabled)
"""

import hashlib
import http.server
import os
import signal
import socket
import socketserver
import sys
import threading
import urllib.error
import urllib.request

# Headers that should not be copied from the client request to upstream.
_STRIP_HEADERS = frozenset(["host", "content-length", "transfer-encoding"])

_VALID_MODES = frozenset(["oauth", "api_key"])


def compute_version_hash():
    """Hash this script's source and return a 12-char hex version string."""
    path = os.path.realpath(__file__)
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def read_mode_and_credential():
    """Read auth mode and credential from stdin (two lines).

    Returns:
        (mode, credential) tuple.
    """
    mode = sys.stdin.readline().strip()
    credential = sys.stdin.readline().strip()
    if not mode or mode not in _VALID_MODES or not credential:
        print("proxy: expected mode and credential on stdin", file=sys.stderr)
        sys.exit(1)
    sys.stdin.close()
    return mode, credential


class IdleShutdown:
    """Shuts down an HTTPServer after a period of inactivity."""

    def __init__(self, timeout, server):
        self.timeout = timeout
        self.server = server
        self._timer = None

    def reset(self):
        """Reset the idle countdown. Call on every request."""
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self.timeout, self._shutdown)
        self._timer.daemon = True
        self._timer.start()

    def _shutdown(self):
        print(f"proxy: idle for {self.timeout}s, shutting down", file=sys.stderr)
        self.server.shutdown()


class DualStackHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Threaded HTTPServer that accepts both IPv4 and IPv6 connections."""

    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    """Forwards requests to TARGET, injecting the real credential."""

    # Assigned by the factory before the server starts.
    real_credential = None
    target = None
    idle_shutdown = None
    version_hash = None
    AUTH_MODE = None

    # Suppress default stderr request logging — we do our own.
    def log_message(self, fmt, *args):
        pass

    # --- health endpoint ---------------------------------------------------

    def do_GET(self):
        if self.idle_shutdown:
            self.idle_shutdown.reset()
        if self.path == "/health":
            body = f"agent-loop-proxy ok v={self.version_hash} mode={self.AUTH_MODE}".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._proxy()

    # --- proxy all other methods ------------------------------------------

    def do_POST(self):
        self._proxy()

    def do_PUT(self):
        self._proxy()

    def do_PATCH(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()

    def _proxy(self):
        if self.idle_shutdown:
            self.idle_shutdown.reset()

        # Read request body (if any).
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else None

        # Build upstream URL.
        url = self.target.rstrip("/") + self.path

        # Build upstream headers — inject the real credential for the active mode.
        skip_header = "authorization" if self.AUTH_MODE == "oauth" else "x-api-key"
        headers = {}
        for key, value in self.headers.items():
            if key.lower() in _STRIP_HEADERS:
                continue
            if key.lower() == skip_header:
                continue
            headers[key] = value
        if self.AUTH_MODE == "oauth":
            headers["Authorization"] = f"Bearer {self.real_credential}"
        else:
            headers["x-api-key"] = self.real_credential

        req = urllib.request.Request(url, data=body, headers=headers, method=self.command)

        try:
            resp = urllib.request.urlopen(req, timeout=300)
            self._stream_response(resp.status, resp.headers, resp)
        except urllib.error.HTTPError as exc:
            print(f"proxy: {self.command} {self.path} → {exc.code}", file=sys.stderr)
            self._stream_response(exc.code, exc.headers, exc)
        except urllib.error.URLError as exc:
            print(f"proxy: {self.command} {self.path} → upstream error: {exc.reason}",
                  file=sys.stderr)
            body = f"proxy: upstream unreachable: {exc.reason}".encode()
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def _stream_response(self, status, headers, body_stream):
        self.send_response(status)
        for key, value in headers.items():
            low = key.lower()
            # Skip hop-by-hop headers.
            if low in ("transfer-encoding", "connection", "keep-alive"):
                continue
            self.send_header(key, value)
        self.end_headers()

        # Stream body in chunks.
        while True:
            chunk = body_stream.read(16384)
            if not chunk:
                break
            self.wfile.write(chunk)


def main():
    mode, credential = read_mode_and_credential()
    port = int(os.environ.get("LISTEN_PORT", "18080"))
    target = os.environ.get("TARGET", "https://api.anthropic.com")
    idle_timeout = int(os.environ.get("IDLE_TIMEOUT", "300"))
    pid_file = os.environ.get("PID_FILE", "")

    version = compute_version_hash()
    ProxyHandler.real_credential = credential
    ProxyHandler.target = target
    ProxyHandler.version_hash = version
    ProxyHandler.AUTH_MODE = mode

    server = DualStackHTTPServer(("::", port), ProxyHandler)

    # Write PID file after port bind succeeds (port bind is the mutex).
    if pid_file:
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))

    def _cleanup_and_exit(signum, frame):
        """Graceful shutdown on SIGTERM."""
        print("proxy: received SIGTERM, shutting down", file=sys.stderr)
        server.shutdown()

    signal.signal(signal.SIGTERM, _cleanup_and_exit)

    if idle_timeout > 0:
        idle = IdleShutdown(idle_timeout, server)
        ProxyHandler.idle_shutdown = idle
        idle.reset()
        print(f"proxy: listening on :{port}, target={target}, "
              f"idle_timeout={idle_timeout}s, v={version}, mode={mode}",
              file=sys.stderr)
    else:
        print(f"proxy: listening on :{port}, target={target}, "
              f"v={version}, mode={mode}",
              file=sys.stderr)

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


if __name__ == "__main__":
    main()
