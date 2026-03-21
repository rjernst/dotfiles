#!/usr/bin/env python3
"""agent-loop-proxy — credential injection reverse proxy

Reads a bearer token from stdin, then proxies HTTP requests to a
configurable upstream target, replacing the Authorization header with
the real token.  The real token never touches disk, logs, or env vars.

Environment variables:
  LISTEN_PORT  — port to listen on (default: 18080)
  TARGET       — upstream base URL (default: https://api.anthropic.com)
"""

import http.server
import os
import sys
import urllib.error
import urllib.request

# Headers that should not be copied from the client request to upstream.
_STRIP_HEADERS = frozenset(["host", "content-length", "transfer-encoding"])


def read_token():
    """Read a single line from stdin and return the stripped value."""
    token = sys.stdin.readline().strip()
    if not token:
        print("proxy: no token received on stdin", file=sys.stderr)
        sys.exit(1)
    # Close stdin so the fd is not left open.
    sys.stdin.close()
    return token


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    """Forwards requests to TARGET, injecting the real bearer token."""

    # Assigned by the factory before the server starts.
    real_token = None
    target = None

    # Suppress default stderr request logging — we do our own.
    def log_message(self, fmt, *args):
        pass

    # --- health endpoint ---------------------------------------------------

    def do_GET(self):
        if self.path == "/health":
            body = b"agent-loop-proxy ok"
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
        print(f"proxy: {self.command} {self.path}", file=sys.stderr)

        # Read request body (if any).
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else None

        # Build upstream URL.
        url = self.target.rstrip("/") + self.path

        # Build upstream headers — replace Authorization.
        headers = {}
        for key, value in self.headers.items():
            if key.lower() in _STRIP_HEADERS:
                continue
            if key.lower() == "authorization":
                continue
            headers[key] = value
        headers["Authorization"] = f"Bearer {self.real_token}"

        req = urllib.request.Request(url, data=body, headers=headers, method=self.command)

        try:
            resp = urllib.request.urlopen(req, timeout=300)
            self._stream_response(resp.status, resp.headers, resp)
        except urllib.error.HTTPError as exc:
            self._stream_response(exc.code, exc.headers, exc)
        except urllib.error.URLError as exc:
            print(f"proxy: upstream error: {exc.reason}", file=sys.stderr)
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
    token = read_token()
    port = int(os.environ.get("LISTEN_PORT", "18080"))
    target = os.environ.get("TARGET", "https://api.anthropic.com")

    ProxyHandler.real_token = token
    ProxyHandler.target = target

    server = http.server.HTTPServer(("0.0.0.0", port), ProxyHandler)
    print(f"proxy: listening on :{port}, target={target}", file=sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
