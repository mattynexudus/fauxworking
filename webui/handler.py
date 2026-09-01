"""
The control panel's HTTP surface — one `BaseHTTPRequestHandler` subclass,
routed by a small regex table. JSON in / JSON out everywhere except the SSE
stream (`GET /api/stream/<run_id>`) and the plain-text/CSV file downloads.

The shared `JobManager`, bind host and bind port live on the server object
(`self.server.manager` / `.ui_host` / `.ui_port`), set by `webui/server.py`.
`protocol_version` stays at HTTP/1.0 (`Connection: close`) — the simplest
correct choice for a single local user; the one long-lived response is the
event stream.
"""

from __future__ import annotations

import json
import re
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import config
from webui import auth, report, registry
from webui.jobs import RunInProgress
from webui.registry import BadRequest

_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_FILES = {
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}
_RUN_ID_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[a-z_]+(?:-[0-9]+)?")
_CSV_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+\.csv")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "FauxworkingUI/1.0"

    # Quieter than the default one-line-per-request stderr spam.
    def log_message(self, fmt, *args):
        if not str(args[1] if len(args) > 1 else "").startswith("2"):
            super().log_message(fmt, *args)

    # -- dispatch ---------------------------------------------------------
    def do_GET(self):
        path, query = self._split(self.path)
        try:
            if path == "/":
                return self._send_static_page()
            m = re.fullmatch(r"/static/([^/]+)", path)
            if m:
                return self._send_static_asset(m.group(1))
            if path == "/api/status":
                return self._api_status()
            if path == "/api/businesses":
                return self._api_businesses(query)
            if path == "/api/commands":
                return self._send_json(registry.commands_json())
            if path == "/api/report":
                return self._send_json(report.gather_report())
            if path == "/api/plan":
                return self._send_json(report.gather_plan())
            if path == "/api/runs":
                return self._api_runs(query)
            m = re.fullmatch(r"/api/runs/([^/]+)/log", path)
            if m:
                return self._api_run_log(m.group(1))
            m = re.fullmatch(r"/api/runs/([^/]+)", path)
            if m:
                return self._api_run_detail(m.group(1))
            m = re.fullmatch(r"/api/stream/([^/]+)", path)
            if m:
                return self._api_stream(m.group(1))
            m = re.fullmatch(r"/api/output/([^/]+)", path)
            if m:
                return self._api_output_csv(m.group(1))
            self._send_json({"error": "not found"}, status=404)
        except BrokenPipeError:
            pass
        except ConnectionResetError:
            pass

    def do_POST(self):
        path, _ = self._split(self.path)
        try:
            if path == "/api/run":
                return self._api_run()
            if path == "/api/argv":
                return self._api_argv()
            if path == "/api/auth/login":
                return self._api_login()
            if path == "/api/auth/logout":
                return self._api_logout()
            m = re.fullmatch(r"/api/runs/([^/]+)/cancel", path)
            if m:
                return self._api_cancel(m.group(1))
            self._send_json({"error": "not found"}, status=404)
        except BrokenPipeError:
            pass
        except ConnectionResetError:
            pass

    # -- endpoints ----------------------------------------------------------
    def _api_status(self):
        mgr = self.server.manager
        active = mgr.active()
        self._send_json({
            "authenticated": auth.is_authenticated(),
            "host": self.server.ui_host,
            "port": self.server.ui_port,
            "active_run": active.to_dict() if active else None,
        })

    def _api_businesses(self, query):
        refresh = query.get("refresh", ["0"])[0] in ("1", "true", "yes")
        self._send_json(auth.businesses(refresh=refresh))

    def _api_runs(self, query):
        try:
            limit = int(query.get("limit", ["50"])[0])
        except ValueError:
            limit = 50
        self._send_json(self.server.manager.recent(limit=limit))

    def _api_run_detail(self, run_id):
        job = self.server.manager.get(run_id)
        if job is None:
            return self._send_json({"error": "no such run"}, status=404)
        self._send_json({**job.to_dict(), "log_tail": job.tail(200)})

    def _api_run_log(self, run_id):
        if not _RUN_ID_RE.fullmatch(run_id):
            return self._send_json({"error": "bad run id"}, status=400)
        path = config.LOGS_DIR / f"run-{run_id}.log"
        if not path.is_file() or path.parent != config.LOGS_DIR:
            return self._send_json({"error": "no such log"}, status=404)
        self._send_bytes(path.read_bytes(), "text/plain; charset=utf-8")

    def _api_output_csv(self, name):
        if not _CSV_NAME_RE.fullmatch(name):
            return self._send_json({"error": "bad name"}, status=400)
        path = config.OUTPUT_DIR / Path(name).name
        if not path.is_file() or path.parent != config.OUTPUT_DIR:
            return self._send_json({"error": "no such file"}, status=404)
        self._send_bytes(path.read_bytes(), "text/csv; charset=utf-8")

    def _api_run(self):
        body = self._read_json()
        if body is None:
            return
        mgr = self.server.manager
        biz = auth.businesses()
        try:
            job = mgr.start(
                command_id=body.get("command"),
                params=body.get("params") or {},
                business_id=body.get("business_id"),
                dry_run=bool(body.get("dry_run")),
                confirm=body.get("confirm"),
                businesses_mode=biz.get("mode", "single"),
            )
        except RunInProgress as e:
            return self._send_json(
                {"error": "A run is already in progress.", "active_run": e.active.to_dict()},
                status=409)
        except BadRequest as e:
            return self._send_json({"error": str(e)}, status=400)
        self._send_json({
            "run_id": job.run_id,
            "stream_url": f"/api/stream/{job.run_id}",
            "log_url": f"/api/runs/{job.run_id}/log",
        }, status=201)

    def _api_argv(self):
        """Preview-only: the exact argv a POST /api/run with this body would
        spawn, without spawning it. Runs the same per-field validation
        build_argv does (BadRequest -> 400), but not the business/confirm
        guards JobManager.start applies — those are "is this session allowed
        to run it right now" checks, not "is this argv well-formed" ones."""
        body = self._read_json()
        if body is None:
            return
        try:
            argv = registry.build_argv(
                command_id=body.get("command"),
                params=body.get("params") or {},
                business_id=body.get("business_id"),
                dry_run=bool(body.get("dry_run")),
            )
        except BadRequest as e:
            return self._send_json({"error": str(e)}, status=400)
        self._send_json({"argv": argv, "display": " ".join(registry.pretty_argv(argv))})

    def _api_logout(self):
        if self.server.manager.active() is not None:
            return self._send_json(
                {"error": "A run is in progress — wait for it to finish before signing out."},
                status=409)
        auth.logout()
        self._send_json({"authenticated": False})

    def _api_cancel(self, run_id):
        try:
            self.server.manager.cancel(run_id)
        except BadRequest as e:
            return self._send_json({"error": str(e)}, status=409)
        self._send_json({"status": "cancelling"}, status=202)

    def _api_login(self):
        body = self._read_json()
        if body is None:
            return
        email = (body.get("email") or "").strip()
        password = body.get("password") or ""
        if not email or not password:
            return self._send_json({"error": "email and password are required"}, status=400)
        try:
            result = auth.login(email, password)
        except SystemExit as e:
            return self._send_json({"error": str(e)}, status=400)
        except Exception as e:  # noqa: BLE001
            return self._send_json({"error": f"login failed: {e}"}, status=400)
        self._send_json(result)

    def _api_stream(self, run_id):
        job = self.server.manager.get(run_id)
        if job is None:
            return self._send_json({"error": "no such run"}, status=404)
        try:
            from_index = int(self.headers.get("Last-Event-ID", "0"))
        except (TypeError, ValueError):
            from_index = 0

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(b": open\n\n")
            self.wfile.flush()
            for frame in job.sse_events(from_index):
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return  # browser closed the stream; the job keeps running

    # -- helpers ----------------------------------------------------------
    def _send_static_page(self):
        path = _STATIC_DIR / "index.html"
        if not path.is_file():
            return self._send_json({"error": "index.html missing"}, status=500)
        self._send_bytes(path.read_bytes(), "text/html; charset=utf-8")

    def _send_static_asset(self, name):
        ctype = _STATIC_FILES.get(name)
        path = _STATIC_DIR / name
        if ctype is None or not path.is_file():
            return self._send_json({"error": "not found"}, status=404)
        self._send_bytes(path.read_bytes(), ctype)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            self._send_json({"error": "invalid JSON body"}, status=400)
            return None

    def _send_json(self, obj, status=200):
        self._send_bytes(json.dumps(obj).encode("utf-8"),
                         "application/json; charset=utf-8", status=status)

    def _send_bytes(self, data, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    @staticmethod
    def _split(raw_path):
        if "?" not in raw_path:
            return raw_path, {}
        path, qs = raw_path.split("?", 1)
        query = {}
        for pair in qs.split("&"):
            if not pair:
                continue
            k, _, v = pair.partition("=")
            query.setdefault(k, []).append(v)
        return path, query
