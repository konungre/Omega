"""Same-origin HTTP command API and authenticated SSE activity subscriptions."""
import hmac
import json
import logging
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .store import APIError, encode, require

LOG = logging.getLogger(__name__)
ASSETS = {"/favicon.svg": ("favicon.svg", "image/svg+xml"), "/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/api-client.js": ("api-client.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/vendor/openclaw-tokens.css": ("vendor/openclaw-tokens.css", "text/css; charset=utf-8"),
          "/vendor/OPENCLAW-LICENSE": ("vendor/OPENCLAW-LICENSE", "text/plain; charset=utf-8")}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, runtime, token, assets=None):
        if not isinstance(token, str) or len(token.encode()) < 32:
            raise ValueError("OMEGA_DASHBOARD_TOKEN must contain at least 32 bytes")
        self.runtime, self.token = runtime, token
        self.assets = Path(assets or Path(__file__).resolve().parents[1] / "dashboard")
        self.stopping = threading.Event()
        super().__init__(address, Handler)
        runtime.server = self

    def shutdown(self):
        self.stopping.set()
        super().shutdown()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, fmt, *args):
        # Never log URLs, auth headers, message contents, or command payloads.
        LOG.debug("Omega API response served")

    def headers_common(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def reply(self, status, body, content_type="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else encode(body).encode()
        self.send_response(status)
        self.headers_common()
        self.send_header("Content-Type", content_type)
        if status == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="omega"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        auth = self.headers.get("Authorization", "")
        if not hmac.compare_digest(auth.encode(), f"Bearer {self.server.token}".encode()):
            raise APIError(401, "unauthorized", "Enter the Omega dashboard token to connect")
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            require(parsed.scheme in ("http", "https") and parsed.netloc == self.headers.get("Host"),
                    "Cross-origin API requests are not allowed", 403, "origin_denied")

    def do_GET(self):
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path in ASSETS:
                name, mime = ASSETS[path]
                self.reply(200, (self.server.assets / name).read_bytes(), mime)
                return
            self.authorized()
            runtime = self.server.runtime
            if path == "/api/v1/openapi.json":
                self.reply(200, (Path(__file__).resolve().parents[1] / "docs" / "omega-api-v1.openapi.json").read_bytes())
            elif path == "/api/v1/snapshot":
                self.reply(200, runtime.store.snapshot(runtime.agent()))
            elif path.startswith("/api/v1/requests/"):
                self.reply(200, runtime.store.request(path.removeprefix("/api/v1/requests/")))
            elif path.startswith("/api/v1/evidence/"):
                record = runtime.store.get("evidence", path.removeprefix("/api/v1/evidence/"))
                require(record is not None, "Evidence not found", 404, "not_found")
                self.reply(200, record)
            elif path == "/api/v1/events":
                query = parse_qs(parsed.query)
                cursor = query.get("after", [self.headers.get("Last-Event-ID", "0")])[0]
                require(bool(re.fullmatch(r"[0-9]{1,19}", cursor)), "Invalid event cursor")
                after = int(cursor)
                events = runtime.store.events(after)
                if query.get("stream") == ["0"]:
                    self.reply(200, {"events": events, "cursor": events[-1]["seq"] if events else after})
                else:
                    instance = query.get("instance_id", [None])[0]
                    require(instance == runtime.store.get("meta", "instance")["value"],
                            "API instance changed; fetch a fresh snapshot", 409, "instance_changed")
                    self.stream(after, set(query.get("types", [""])[0].split(",")) - {""})
            else:
                raise APIError(404, "not_found", "API endpoint not found")
        except APIError as exc:
            self.reply(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            LOG.exception("Omega API read failed")
            self.reply(500, {"error": {"code": "internal_error", "message": "Unable to read Omega state"}})

    def stream(self, after, types):
        self.send_response(200)
        self.headers_common()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(b": connected\n\n")
        self.wfile.flush()
        ticks = 0
        while not self.server.stopping.is_set():
            events = self.server.runtime.store.events(after)
            for event in events:
                after = event["seq"]
                if not types or event["type"] in types or event["type"].split(".")[0] in types:
                    self.wfile.write(f"id: {after}\nevent: activity\ndata: {encode(event)}\n\n".encode())
            if events:
                # Advance even when a subscription filters out all events.
                self.wfile.write(f"id: {after}\nevent: cursor\ndata: {{}}\n\n".encode())
            if ticks % 15 == 0:
                self.wfile.write(b": heartbeat\n\n")
            self.wfile.flush()
            ticks += 1
            if len(events) < 200:
                self.server.stopping.wait(1)

    def do_POST(self):
        # A new connection per command keeps rejected/unread bodies out of the next request.
        self.close_connection = True
        try:
            self.authorized()
            require(self.path == "/api/v1/commands", "API endpoint not found", 404, "not_found")
            require(self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json",
                    "Use Content-Type: application/json", 415, "unsupported_media_type")
            raw_length = self.headers.get("Content-Length", "")
            require(raw_length.isdigit() and 0 < int(raw_length) <= 65536 and not self.headers.get("Transfer-Encoding"),
                    "Provide a JSON body no larger than 64 KiB", 413, "invalid_body_size")
            raw = self.rfile.read(int(raw_length))
            try:
                body = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            except (ValueError, UnicodeDecodeError):
                raise APIError(400, "invalid_json", "Body must be valid JSON")
            record, replayed = self.server.runtime.store.submit(body, self.server.runtime.connected)
            self.reply(200 if replayed or record["status"] == "completed" else 202,
                       {"request": record, "replayed": replayed})
        except APIError as exc:
            self.reply(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except Exception:
            LOG.exception("Omega API command failed")
            self.reply(500, {"error": {"code": "internal_error", "message": "Unable to process command; retry with the same request_id"}})
