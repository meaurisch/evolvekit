"""The app's HTTP server: routing and security.

A standard-library `ThreadingHTTPServer`, bound to this machine, serving one
page (`static/app.html`), the JSON API (`api.py`), and the existing dashboard
for every run of a study. The rules are the dashboard's, and stricter because
the app writes:

* it binds to 127.0.0.1 and answers only to a loopback `Host` with its own
  port (DNS rebinding would otherwise let a hostile page talk to it by that
  page's name);
* a request the browser marks `Sec-Fetch-Site: cross-site` is refused;
* every request that changes something (POST, PUT, PATCH, DELETE) must carry
  a same-origin `Origin` -- which browsers always send on those methods --
  so no other site can make a visitor's browser start a run;
* bodies are limited to 200 MB, names from URLs are cleaned (`store.safe_name`)
  and every path is judged as text before the filesystem sees it.
"""

from __future__ import annotations

import ipaddress
import json
import re
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, unquote, urlparse

from evolvekit.app import AppError
from evolvekit.app.store import MAX_UPLOAD, Home

__all__ = ["AppServer", "DEFAULT_PORT", "FileResponse", "PAGE", "Request"]

DEFAULT_PORT = 8780
PORT_ATTEMPTS = 20
PAGE = Path(__file__).with_name("static") / "app.html"
CHANGING = {"POST", "PUT", "PATCH", "DELETE"}
_RUN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_DASHBOARD = re.compile(r"^/studies/(?P<slug>[^/]+)/runs/(?P<run>[^/]+)/dashboard/(?P<rest>.*)$")


@dataclass
class Request:
    """What a handler gets: the parts of the URL, the body, and the home."""

    method: str
    path: str
    params: dict[str, str]
    query: dict[str, str]
    headers: Mapping[str, str]
    body: bytes
    home: Home
    server: "AppServer"
    _json: Any = field(default=None, repr=False)

    def json(self) -> dict[str, Any]:
        if self._json is None:
            if not self.body:
                self._json = {}
            else:
                try:
                    self._json = json.loads(self.body.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    raise AppError("the request's body is not JSON") from None
            if not isinstance(self._json, dict):
                raise AppError("the request's body must be a JSON object")
        return self._json


@dataclass
class FileResponse:
    body: bytes
    content_type: str
    filename: str | None = None
    status: int = 200


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _handler(app: "AppServer") -> type[BaseHTTPRequestHandler]:
    from evolvekit.app import api

    class Handler(BaseHTTPRequestHandler):
        server_version = "evolvekit-app"
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:  # the page polls; a log line per poll helps nobody
            return None

        # -- answering --------------------------------------------------------

        def _send(self, body: bytes, content_type: str, status: int = 200, headers: Mapping[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, payload: Any, status: int = 200) -> None:
            self._send(json.dumps(payload, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8", status)

        def _error(self, message: str, status: int) -> None:
            try:
                self._json({"error": message}, status)
            except OSError:
                pass

        # -- who is asking ----------------------------------------------------

        def _allowed_hosts(self) -> set[str]:
            port = self.server.server_address[1]
            return {f"{name}:{port}" for name in ("127.0.0.1", "localhost", "[::1]")}

        def _refusal(self) -> str | None:
            if self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
                return "refused: a request from another site"
            if _is_loopback(self.server.server_address[0]) and self.headers.get("Host", "").lower() not in self._allowed_hosts():
                return "refused: the app answers to 127.0.0.1 and localhost only"
            if self.command in CHANGING:
                origin = self.headers.get("Origin", "").lower()
                if origin not in {f"http://{host}" for host in self._allowed_hosts()}:
                    return "refused: a change must come from the app's own page"
            return None

        # -- dispatch ---------------------------------------------------------

        def _body(self) -> bytes | None:
            length = self.headers.get("Content-Length")
            if length is None:
                return b""
            try:
                size = int(length)
            except ValueError:
                self._error("Content-Length is not a number", 400)
                return None
            if size > MAX_UPLOAD:
                self._error("the upload is larger than 200 MB", 413)
                self.close_connection = True
                return None
            return self.rfile.read(size) if size > 0 else b""

        def _dispatch(self) -> None:
            url = urlparse(self.path)
            refusal = self._refusal()
            body = self._body() if self.command in CHANGING else b""
            if body is None:
                return
            if refusal:
                self._error(refusal, 403)
                return
            path = url.path
            query = {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
            try:
                if self.command in ("GET", "HEAD") and path in ("/", "/index.html"):
                    self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
                    return
                dashboard = _DASHBOARD.match(path)
                if dashboard and self.command in ("GET", "HEAD"):
                    self._dashboard(dashboard.group("slug"), dashboard.group("run"), dashboard.group("rest"), query)
                    return
                for method, pattern, handle in api.ROUTES:
                    match = pattern.fullmatch(path)
                    if match and (method == self.command or (method == "GET" and self.command == "HEAD")):
                        request = Request(
                            method=self.command, path=path,
                            params={k: unquote(v) for k, v in match.groupdict().items()},
                            query=query, headers=self.headers, body=body, home=app.home, server=app,
                        )
                        self._answer(handle(request))
                        return
                self._error("not found", 404)
            except AppError as exc:
                self._error(str(exc), exc.status)
            except (BrokenPipeError, ConnectionError):
                pass  # the tab was closed mid-response
            except Exception as exc:  # noqa: BLE001 - one request must never take the app down
                traceback.print_exc(file=sys.stderr)
                self._error(f"something went wrong inside the app: {type(exc).__name__}: {exc}", 500)

        def _answer(self, result: Any) -> None:
            if isinstance(result, FileResponse):
                headers = {}
                if result.filename:
                    headers["Content-Disposition"] = f'attachment; filename="{result.filename}"'
                self._send(result.body, result.content_type, result.status, headers)
            else:
                self._json(result)

        def _dashboard(self, slug: str, run: str, rest: str, query: dict[str, str]) -> None:
            from evolvekit.dashboard import PAGE as DASHBOARD_PAGE
            from evolvekit.dashboard import _read_tail, _safe_path
            from evolvekit.status import candidate_detail

            root = app.home.study_root(unquote(slug))
            run = unquote(run)
            run_dir = root / "runs" / run
            if not _RUN.match(run) or not run_dir.is_dir():
                raise AppError(f"there is no run {run!r} in this study", 404)
            if rest in ("", "index.html"):
                self._send(DASHBOARD_PAGE.read_bytes(), "text/html; charset=utf-8")
            elif rest == "api/status":
                self._json(app.status_document(run_dir))
            elif rest.startswith("api/candidate/"):
                detail = candidate_detail(run_dir, unquote(rest.rsplit("/", 1)[-1]))
                self._json(detail or {"error": "no such candidate"}, 200 if detail else 404)
            elif rest == "api/log":
                target = _safe_path(run_dir, query.get("path", ""))
                if target is None:
                    raise AppError("not a readable file of this run", 404)
                self._send(_read_tail(target).encode("utf-8"), "text/plain; charset=utf-8")
            else:
                raise AppError("not found", 404)

        do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = _dispatch  # noqa: N815

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second server bind a port in use (see the dashboard).
    allow_reuse_address = sys.platform != "win32"


class AppServer:
    """The app for one library home. `start()` serves from a thread and
    returns the URL; `serve_forever()` blocks."""

    def __init__(self, home: str | Path, *, port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> None:
        self.home = Home(home)
        self.work: dict[str, Any] = {}
        """Background work of this process (previews, test runs), by key."""
        self.assistant_provider: Any = None
        """A provider object to use instead of the one Settings name: for tests."""
        self.lock = threading.Lock()
        self._status_cache: dict[str, tuple[float, Any]] = {}
        self._server = self._bind(host, port)
        self._thread: threading.Thread | None = None

    def _bind(self, host: str, port: int) -> ThreadingHTTPServer:
        handler = _handler(self)
        last: OSError | None = None
        for candidate in [port + i for i in range(PORT_ATTEMPTS)] if port else [0]:
            try:
                return _Server((host, candidate), handler)
            except OSError as exc:
                last = exc
        raise OSError(f"no free port between {port} and {port + PORT_ATTEMPTS - 1} on {host}: {last}")

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def status_document(self, run_dir: Path) -> Any:
        """`build_status`, rebuilt at most once a second per run."""
        from evolvekit.status import build_status

        key = str(run_dir)
        with self.lock:
            cached = self._status_cache.get(key)
            if cached and time.monotonic() - cached[0] < 1.0:
                return cached[1]
        document = build_status(run_dir)
        with self.lock:
            self._status_cache[key] = (time.monotonic(), document)
        return document

    def start(self) -> str:
        self._thread = threading.Thread(target=self._server.serve_forever, name="evolvekit-app", daemon=True)
        self._thread.start()
        return self.url

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def stop(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=5)
            self._thread = None
        self._server.server_close()


def route(method: str, pattern: str) -> Callable[[Callable[[Request], Any]], tuple[str, re.Pattern[str], Callable[[Request], Any]]]:
    """A routing-table entry; `{name}` in the pattern is one path segment."""
    compiled = re.compile(re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern))

    def entry(handle: Callable[[Request], Any]) -> tuple[str, re.Pattern[str], Callable[[Request], Any]]:
        return method, compiled, handle

    return entry
