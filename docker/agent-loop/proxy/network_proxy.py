#!/usr/bin/env python3
"""network-proxy — HTTP forward proxy with hostname allowlist

Sits between a container and the internet, filtering requests by hostname.
Only requests to hostnames in the allowlist are forwarded; all others are
denied with 403.

Handles CONNECT (for HTTPS tunnels) and plain HTTP methods (GET, POST, etc.).

Environment variables:
  LISTEN_PORT    — port to listen on (default: 18082)
  ALLOWED_HOSTS  — comma-separated list of allowed hostnames
  IDLE_TIMEOUT   — seconds of inactivity before self-shutdown (default: 300, 0=disabled)
  PID_FILE       — optional path to write PID
"""

import http.client
import http.server
import os
import select
import socket
import sys
import urllib.parse

from proxy_base import CHUNK_SIZE, run_proxy_server


def parse_allowed_hosts(hosts_str):
    """Parse a comma-separated string of hostnames into a frozenset."""
    if not hosts_str:
        return frozenset()
    return frozenset(h.strip().lower() for h in hosts_str.split(",") if h.strip())


def is_host_allowed(hostname, allowed_hosts):
    """Check if a hostname is in the allowlist (case-insensitive)."""
    return hostname.lower() in allowed_hosts


def extract_host_from_authority(authority):
    """Extract hostname from a host:port authority string."""
    # Strip port if present.
    if authority.startswith("["):
        # IPv6: [::1]:443
        bracket_end = authority.find("]")
        if bracket_end != -1:
            return authority[1:bracket_end].lower()
        return authority.lower()
    if ":" in authority:
        return authority.rsplit(":", 1)[0].lower()
    return authority.lower()


class NetworkProxyHandler(http.server.BaseHTTPRequestHandler):
    """Forward proxy that filters by hostname allowlist."""

    # Set by proxy_base.run_proxy_server / main.
    _script_path = os.path.realpath(__file__)
    allowed_hosts = frozenset()
    idle_shutdown = None
    version_hash = None

    # Suppress default stderr request logging.
    def log_message(self, fmt, *args):
        pass

    # --- health endpoint ---------------------------------------------------

    def _handle_health(self):
        hosts_str = ",".join(sorted(self.allowed_hosts))
        body = f"network-proxy ok hosts={hosts_str} v={self.version_hash}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # --- deny response -----------------------------------------------------

    def _deny(self, host):
        msg = f"network-proxy: blocked {self.command} to {host}".encode()
        print(msg.decode(), file=sys.stderr)
        self.send_response(403)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg)

    # --- CONNECT handler (HTTPS tunneling) ---------------------------------

    def do_CONNECT(self):
        if self.idle_shutdown:
            self.idle_shutdown.reset()

        # self.path is "host:port" for CONNECT.
        hostname = extract_host_from_authority(self.path)

        if not is_host_allowed(hostname, self.allowed_hosts):
            self._deny(hostname)
            return

        # Parse host and port from the authority.
        try:
            if ":" in self.path and not self.path.startswith("["):
                host, port_str = self.path.rsplit(":", 1)
                port = int(port_str)
            elif self.path.endswith("]"):
                # IPv6 without port
                host = self.path[1:-1]
                port = 443
            elif "]:" in self.path:
                bracket_end = self.path.index("]")
                host = self.path[1:bracket_end]
                port = int(self.path[bracket_end + 2:])
            else:
                host = self.path
                port = 443
        except (ValueError, IndexError):
            body = f"network-proxy: bad CONNECT authority: {self.path}".encode()
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        try:
            upstream = socket.create_connection((host, port), timeout=30)
        except OSError as exc:
            print(f"network-proxy: CONNECT {self.path} → upstream error: {exc}",
                  file=sys.stderr)
            body = f"network-proxy: upstream error: {exc}".encode()
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Tell the client the tunnel is established.
        self.send_response(200, "Connection Established")
        self.end_headers()

        # Bidirectional tunnel using select.
        client_sock = self.connection
        try:
            self._tunnel(client_sock, upstream)
        finally:
            upstream.close()

    def _tunnel(self, client_sock, upstream_sock):
        """Shuttle bytes between client and upstream using select."""
        sockets = [client_sock, upstream_sock]
        while True:
            try:
                readable, _, errored = select.select(sockets, [], sockets, 30)
            except (OSError, ValueError):
                break

            if errored:
                break

            for sock in readable:
                try:
                    data = sock.recv(CHUNK_SIZE)
                except OSError:
                    return
                if not data:
                    return
                other = upstream_sock if sock is client_sock else client_sock
                try:
                    other.sendall(data)
                except OSError:
                    return

    # --- plain HTTP handlers -----------------------------------------------

    def do_GET(self):
        self._dispatch_http()

    def do_HEAD(self):
        self._dispatch_http()

    def do_POST(self):
        self._dispatch_http()

    def do_PUT(self):
        self._dispatch_http()

    def do_DELETE(self):
        self._dispatch_http()

    def do_PATCH(self):
        self._dispatch_http()

    def _dispatch_http(self):
        if self.idle_shutdown:
            self.idle_shutdown.reset()

        # Health endpoint is a direct request to the proxy itself.
        if self.path == "/health":
            self._handle_health()
            return

        # For forward proxy, the path is an absolute URL.
        parsed = urllib.parse.urlparse(self.path)
        hostname = parsed.hostname
        if not hostname:
            # Fall back to Host header.
            host_header = self.headers.get("Host", "")
            hostname = extract_host_from_authority(host_header)

        if not hostname or not is_host_allowed(hostname, self.allowed_hosts):
            # Drain request body to prevent connection desync on keep-alive.
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length:
                self.rfile.read(content_length)
            else:
                self.close_connection = True
            self._deny(hostname or "unknown")
            return

        self._proxy_http(parsed, hostname)

    def _proxy_http(self, parsed, hostname):
        """Forward a plain HTTP request to the target."""
        host = parsed.hostname or hostname
        port = parsed.port or 80
        # Reconstruct the path (with query string).
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"

        conn = None
        try:
            conn = http.client.HTTPConnection(host, port, timeout=30)

            # Forward headers.
            headers = {}
            content_length = None
            for key, value in self.headers.items():
                low = key.lower()
                # Skip hop-by-hop and proxy headers.
                if low in ("proxy-connection", "proxy-authorization",
                           "connection", "keep-alive"):
                    continue
                if low == "content-length":
                    content_length = int(value)
                headers[key] = value

            # Read request body if present.
            body = None
            if content_length and content_length > 0:
                body = self.rfile.read(content_length)

            conn.request(self.command, path, body=body, headers=headers)
            resp = conn.getresponse()

            # Stream response back to client.
            self.send_response(resp.status)
            for key, value in resp.getheaders():
                low = key.lower()
                if low in ("connection", "keep-alive", "transfer-encoding"):
                    continue
                self.send_header(key, value)
            self.end_headers()

            while True:
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                self.wfile.write(chunk)

        except (OSError, http.client.HTTPException) as exc:
            print(f"network-proxy: {self.command} {self.path} → "
                  f"upstream error: {exc}", file=sys.stderr)
            body = f"network-proxy: upstream error: {exc}".encode()
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            if conn:
                conn.close()


def main():
    port = int(os.environ.get("LISTEN_PORT", "18082"))
    allowed_hosts_str = os.environ.get("ALLOWED_HOSTS", "")
    idle_timeout = int(os.environ.get("IDLE_TIMEOUT", "300"))
    pid_file = os.environ.get("PID_FILE", "")

    allowed = parse_allowed_hosts(allowed_hosts_str)
    NetworkProxyHandler.allowed_hosts = allowed

    run_proxy_server(
        name="network-proxy",
        port=port,
        handler_class=NetworkProxyHandler,
        idle_timeout=idle_timeout,
        pid_file=pid_file,
    )


if __name__ == "__main__":
    main()
