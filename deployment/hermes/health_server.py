#!/usr/bin/env python3
"""Health/metrics server + reverse proxy for Teams webhook notifications.

Serves on port 8080 (Container Apps ingress target):
  GET  /health       — liveness probe
  GET  /health/ready — readiness probe
  GET  /metrics      — Prometheus metrics
  POST /api/notifications — proxied to Teams adapter on TEAMS_WEBHOOK_PORT
  GET  /api/notifications — proxied (Graph validation handshake)
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# STORY-304: Presence endpoint handler
try:
    from deployment.hermes.presence_endpoint import handle_presence_request
    _HAS_PRESENCE_ENDPOINT = True
except ImportError:
    _HAS_PRESENCE_ENDPOINT = False

START_TIME = time.time()

# Dispatch queue poller (STORY-027)
try:
    from deployment.hermes.dispatch_poller import start_dispatch_poller
    _HAS_DISPATCH_POLLER = True
except ImportError:
    _HAS_DISPATCH_POLLER = False
TEAMS_WEBHOOK_PORT = int(os.environ.get("TEAMS_WEBHOOK_PORT", "3978"))


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesHealth/1.0"

    def _send_json(self, status_code: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, status_code: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _proxy_to_teams(self, method: str) -> None:
        """Forward request to the Teams adapter webhook server."""
        # Reconstruct the full path + query string
        target = f"http://127.0.0.1:{TEAMS_WEBHOOK_PORT}{self.path}"
        try:
            body = None
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                body = self.rfile.read(content_length)

            req = Request(target, data=body, method=method)
            req.add_header("Content-Type", self.headers.get("Content-Type", "application/json"))
            auth = self.headers.get("Authorization")
            if auth:
                req.add_header("Authorization", auth)

            with urlopen(req, timeout=10) as resp:
                resp_body = resp.read()
                self.send_response(resp.status)
                for key, val in resp.getheaders():
                    if key.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(key, val)
                self.end_headers()
                self.wfile.write(resp_body)
        except HTTPError as e:
            # Preserve upstream status/body so callers get the real error.
            body = e.read()
            self.send_response(e.code)
            for key, val in e.headers.items():
                if key.lower() not in ("transfer-encoding", "connection"):
                    self.send_header(key, val)
            self.end_headers()
            self.wfile.write(body)
        except URLError as e:
            print(f"[health-http] Proxy to teams adapter failed: {e}", flush=True)
            self._send_json(502, {"error": "teams adapter not reachable", "detail": str(e)})

    def do_GET(self) -> None:  # noqa: N802
        uptime = int(time.time() - START_TIME)
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "service": "dan", "uptime_seconds": uptime})
            return
        if self.path == "/health/ready":
            self._send_json(200, {"status": "ready", "service": "dan", "uptime_seconds": uptime})
            return
        if self.path.startswith("/debug/file"):
            # Read a file from the container filesystem for debugging
            import urllib.parse
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            fpath = qs.get("path", [""])[0]
            if not fpath:
                self._send_json(400, {"error": "missing ?path= parameter"})
                return
            try:
                import os as _os
                if _os.path.isdir(fpath):
                    entries = _os.listdir(fpath)
                    self._send_json(200, {"path": fpath, "entries": entries})
                else:
                    with open(fpath, "r") as f:
                        self._send_json(200, {"path": fpath, "content": f.read()[:50000]})
            except Exception as e:
                self._send_json(404, {"error": str(e)})
            return
        if self.path == "/metrics":
            self._send_text(
                200,
                "# HELP hermes_uptime_seconds Process uptime in seconds\n"
                "# TYPE hermes_uptime_seconds gauge\n"
                f"hermes_uptime_seconds {uptime}\n",
            )
            return
        # Proxy Graph API validation handshake (GET /api/notifications?validationToken=...)
        if self.path.startswith("/api/notifications"):
            self._proxy_to_teams("GET")
            return
        self._send_json(404, {"status": "not_found", "path": self.path})

    def do_POST(self) -> None:  # noqa: N802
        # STORY-304: Presence endpoint
        if self.path == "/internal/presence" and _HAS_PRESENCE_ENDPOINT:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""
            headers = {
                "X-API-Key": self.headers.get("X-API-Key", ""),
                "Content-Type": self.headers.get("Content-Type", ""),
            }
            status, response = handle_presence_request(headers, body)
            self._send_json(status, response)
            return
        # Proxy all POSTs to /api/notifications and /api/messages to the Teams adapter
        if self.path.startswith("/api/notifications") or self.path.startswith("/api/messages"):
            self._proxy_to_teams("POST")
            return
        self._send_json(404, {"status": "not_found", "path": self.path})

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[health-http] {self.address_string()} - {fmt % args}", flush=True)


if __name__ == "__main__":
    host = os.environ.get("HERMES_HEALTH_HOST", "0.0.0.0")
    port = int(os.environ.get("HERMES_HEALTH_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[health-http] listening on {host}:{port} (proxying /api/notifications to :{TEAMS_WEBHOOK_PORT})", flush=True)
    # Start dispatch queue poller if available (STORY-027)
    if _HAS_DISPATCH_POLLER:
        start_dispatch_poller()

    server.serve_forever()
