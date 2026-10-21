"""Loopback dashboard server with optional bearer-token district access.

EPISCOPE_TOKENS is a JSON object mapping long random tokens to
{"role":"national"} or {"role":"district","district_id":"01"}.
Do not put real tokens in source control. Without configured tokens only a
loopback national development session is available. This is a local teaching
prototype, not an internet-facing health production service.
"""
from __future__ import annotations

import argparse
import copy
import hmac
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


def filter_payload(payload, principal):
    if principal.get("role") == "national":
        result = copy.deepcopy(payload)
        result["access"] = {"role": "national", "label": "National planning"}
        return result
    if principal.get("role") != "district":
        raise PermissionError("Unknown role")
    identifier = str(principal.get("district_id", "")).zfill(2)
    district_indices = [i for i, district in enumerate(payload["districts"]) if district["district_id"] == identifier]
    if len(district_indices) != 1:
        raise PermissionError("District is not in the published registry")
    index = district_indices[0]
    result = copy.deepcopy(payload)
    district = payload["districts"][index]
    result["districts"] = [copy.deepcopy(district)]
    for table in ("observed", "forecasts", "signals", "allocations"):
        result[table] = [[0, *row[1:]] for row in payload.get(table, []) if row[0] == index]
    result["geography"]["features"] = [feature for feature in result["geography"].get("features", []) if feature["properties"].get("district_id") == identifier]
    result["calendar"] = [row for row in result.get("calendar", []) if row[0] == district["division"]]
    # National evaluation summaries are not needed by district officers.
    result["tradeoff"] = []
    result["scenarios"] = []
    result['quality'] = []  # Source-load metadata is scoped nationally.
    result["access"] = {"role": "district", "district_id": identifier, "label": district["district_name"] + " district"}
    return result


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, dashboard_root, tokens=None):
        self.dashboard_root = Path(dashboard_root).resolve()
        self.tokens = tokens or {}
        super().__init__(address, DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "EpiScopeLocal/1.0"

    def log_message(self, fmt, *args):
        # Do not log credentials or request query strings.
        print(f"{self.client_address[0]} {self.command} {urlsplit(self.path).path}")

    def _respond(self, code, body, content_type="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, value):
        self._respond(code, json.dumps(value, separators=(",", ":"), allow_nan=False).encode())

    def _principal(self):
        if not self.server.tokens:
            return {"role": "national"}
        authorization = self.headers.get("Authorization", "")
        candidate = authorization[7:] if authorization.startswith("Bearer ") else ""
        for token, principal in self.server.tokens.items():
            if hmac.compare_digest(candidate, token):
                return principal
        return None

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/api/health":
            return self._json(200, {"ok": True, "authentication_required": bool(self.server.tokens), "data_ready": (self.server.dashboard_root / "data" / "dashboard.json").exists()})
        if path == "/api/data":
            principal = self._principal()
            if principal is None:
                return self._json(401, {"error": "A valid access token is required."})
            try:
                payload = json.loads((self.server.dashboard_root / "data" / "dashboard.json").read_text(encoding="utf-8"))
                return self._json(200, filter_payload(payload, principal))
            except FileNotFoundError:
                return self._json(503, {"error": "No published gold release. Run the pipeline and serving export first."})
            except PermissionError as error:
                return self._json(403, {"error": str(error)})
        # Explicit allowlist prevents bypassing role checks via static JSON,
        # source paths, encoded traversal, directory listings or lake folders.
        allowed = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/vendor/d3.v7.min.js": "vendor/d3.v7.min.js", "/vendor/D3-LICENSE.txt": "vendor/D3-LICENSE.txt"}
        if path not in allowed:
            return self._json(404, {"error": "Not found"})
        file = self.server.dashboard_root / allowed[path]
        if not file.is_file():
            return self._json(404, {"error": "Not found"})
        content_type = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
        self._respond(200, file.read_bytes(), content_type + "; charset=utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("dashboard"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    tokens = json.loads(os.environ.get("EPISCOPE_TOKENS", "{}"))
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("This prototype only binds to loopback. Use a reviewed authenticated TLS reverse proxy for remote access.")
    for token, principal in tokens.items():
        if len(token) < 24 or principal.get("role") not in ("national", "district"):
            parser.error("Each token must be at least 24 characters and have a valid role.")
        if principal["role"] == "district" and not principal.get("district_id"):
            parser.error("District tokens require district_id.")
    server = DashboardServer((args.host, args.port), args.root, tokens)
    print(f"EpiScope: http://{args.host}:{args.port} — " + ("token access enabled" if tokens else "local national development mode"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
