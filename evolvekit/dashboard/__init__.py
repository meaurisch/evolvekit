"""A live dashboard for a run directory: one HTML file and a JSON endpoint.

    python -m evolvekit run --config ... --dashboard      # while it runs
    python -m evolvekit dashboard --run-dir runs/x        # any run: live, finished, dead
    python -m evolvekit dashboard --run-dir runs/x --export report.html

The page draws exactly one thing: the document `evolvekit.status.build_status`
returns, fetched from `/api/status`. `status --json` prints the same document,
so what a person sees and what an agent reads cannot drift apart -- there is no
second implementation to drift.

It is deliberately small. The server is `http.server` from the standard
library, bound to localhost, read-only, and does nothing until a browser asks;
the page is a single file with its styles and scripts inline, so it needs no
build step, no package manager and no network -- it works on a machine that is
offline because it has never heard of a CDN. `--export` writes the same page
with the document embedded, which opens from disk and can be attached to a
ticket or a pull request.

Reading a run directory is all it ever does, so it cannot slow a run down by
more than the cost of reading those files when somebody is actually looking:
the document is rebuilt at most once a second, and not at all while no page is
open.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from evolvekit.status import build_status, candidate_detail

__all__ = ["DashboardServer", "export_html", "DEFAULT_PORT", "PAGE"]

DEFAULT_PORT = 8765
PORT_ATTEMPTS = 20
"""`--port` is where the search for a free port starts, not a demand: two runs
on one machine should both get a dashboard."""

PAGE = Path(__file__).with_name("index.html")

REBUILD_AFTER_S = 1.0
LOG_SUFFIXES = {".log", ".json", ".jsonl", ".py", ".md", ".txt"}
LOG_BYTES = 512 * 1024
"""The tail of a log is what gets read; a solver that printed a gigabyte must
not be able to take the page, or the server, down with it."""


class _Cache:
    """The status document, rebuilt at most once per `REBUILD_AFTER_S`."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self._lock = threading.Lock()
        self._built = 0.0
        self._body = b""

    def body(self) -> bytes:
        with self._lock:
            if not self._body or time.monotonic() - self._built >= REBUILD_AFTER_S:
                document = build_status(self.run_dir)
                self._body = json.dumps(document, allow_nan=False).encode("utf-8")
                self._built = time.monotonic()
            return self._body


def _safe_path(run_dir: Path, relative: str) -> Path | None:
    """`relative` resolved inside `run_dir`, or `None`. The run directory is the
    whole of what this server may read."""
    try:
        root = run_dir.resolve()
        target = (root / relative).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        return None
    if target.suffix.lower() not in LOG_SUFFIXES or not target.is_file():
        return None
    return target


def _read_tail(path: Path) -> str:
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - LOG_BYTES))
        data = handle.read()
    text = data.decode("utf-8", errors="replace")
    if size > LOG_BYTES:
        text = f"[... the first {size - LOG_BYTES:,} bytes are not shown ...]\n" + text
    return text


def _handler(run_dir: Path, cache: _Cache) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "evolvekit-dashboard"

        def log_message(self, *_args: Any) -> None:  # a poll every two seconds
            return None

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, allow_nan=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8", status)

        def do_GET(self) -> None:  # noqa: N802 - the name http.server requires
            url = urlparse(self.path)
            try:
                if url.path in ("/", "/index.html"):
                    self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
                elif url.path == "/api/status":
                    self._send(cache.body(), "application/json; charset=utf-8")
                elif url.path.startswith("/api/candidate/"):
                    detail = candidate_detail(run_dir, unquote(url.path.rsplit("/", 1)[-1]))
                    self._json(detail or {"error": "no such candidate"}, 200 if detail else 404)
                elif url.path == "/api/log":
                    wanted = (parse_qs(url.query).get("path") or [""])[0]
                    target = _safe_path(run_dir, wanted)
                    if target is None:
                        self._json({"error": "not a readable file of this run"}, 404)
                    else:
                        self._send(_read_tail(target).encode("utf-8"), "text/plain; charset=utf-8")
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except (BrokenPipeError, ConnectionError):
                pass  # the tab was closed mid-response
            except Exception as exc:  # noqa: BLE001 - a view must not kill the server
                try:
                    self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
                except OSError:
                    pass

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # `HTTPServer` sets SO_REUSEADDR. On POSIX that only skips TIME_WAIT; on
    # Windows it lets a second server bind a port that is *in use*, after which
    # requests go to either one -- and "try the next port" never gets to try.
    allow_reuse_address = sys.platform != "win32"


class DashboardServer:
    """Serves one run directory on localhost. `start()` returns the URL."""

    def __init__(
        self, run_dir: str | Path, *, host: str = "127.0.0.1", port: int = DEFAULT_PORT
    ) -> None:
        self.run_dir = Path(run_dir)
        self.host = host
        self._cache = _Cache(self.run_dir)
        self._server = self._bind(host, port)
        self._thread: threading.Thread | None = None

    def _bind(self, host: str, port: int) -> ThreadingHTTPServer:
        handler = _handler(self.run_dir, self._cache)
        last: OSError | None = None
        for candidate in [port + i for i in range(PORT_ATTEMPTS)] if port else [0]:
            try:
                return _Server((host, candidate), handler)
            except OSError as exc:
                last = exc
        raise OSError(
            f"no free port between {port} and {port + PORT_ATTEMPTS - 1} on {host}: {last}"
        )

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/"

    def start(self) -> str:
        """Serve from a daemon thread; the caller carries on with its run."""
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="evolvekit-dashboard", daemon=True
        )
        self._thread.start()
        return self.url

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def stop(self) -> None:
        # `shutdown()` waits for a `serve_forever` loop to notice; with none
        # running in the background it would wait for ever.
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=5)
            self._thread = None
        self._server.server_close()


def export_html(run_dir: str | Path, out_path: str | Path) -> Path:
    """The dashboard as one self-contained file, frozen at this moment."""
    directory = Path(run_dir)
    document = build_status(directory)
    details = {
        str(row["id"]): candidate_detail(directory, str(row["id"]))
        for row in document.get("candidates") or []
    }
    payload = json.dumps({"status": document, "candidates": details}, allow_nan=False)
    # Candidate source and stderr are untrusted, and an HTML parser reads a
    # script element before JavaScript does: `</script>` would end it and
    # `<!--<script` would swallow what follows. No `<` survives, so neither can.
    embedded = payload.replace("<", "\\u003c")
    page = PAGE.read_text(encoding="utf-8").replace(
        "/*__EVOLVEKIT_EMBEDDED__*/null", embedded, 1
    )
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8", newline="\n")
    return target


def open_in_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - a headless machine is not an error
        pass
