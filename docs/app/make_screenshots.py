"""Screenshots of the app for docs/app.md: the screens it shows, a few in
dark as well, and two at phone width.

    python docs/app/make_screenshots.py --home LIBRARY --study SLUG [--running SLUG]

LIBRARY is a library home with a finished study SLUG (the step pages and the
results are taken from it) and, optionally, a study that is running right now
(for the running page). The app is started on a free port inside this
process, and a Chromium browser (Edge or Chrome) in headless mode takes each
picture. The pictures were checked by eye in the in-app browser as well.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from evolvekit.app.server import AppServer  # noqa: E402

BROWSERS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/chromium", "/usr/bin/google-chrome",
)
DESKTOP, PHONE = (1280, 900), (500, 900)
"""PHONE is the narrowest window a headless Edge or Chrome lays out at (about 490
px): given less, it lays the page out that wide anyway and crops the picture.
The page itself is checked down to 360 px in a browser that emulates a phone."""


def shots(study: str, running: str | None) -> list[tuple[str, str, tuple[int, int], tuple[str, ...]]]:
    """(name, route, window, themes): the pictures docs/app.md shows."""
    pages = [
        ("home", "#/", DESKTOP, ("light", "dark")),
        ("application", f"#/study/{study}/2", DESKTOP, ("light",)),
        ("change", f"#/study/{study}/4", DESKTOP, ("light",)),
        ("goal", f"#/study/{study}/5", DESKTOP, ("light",)),
        ("results", f"#/study/{study}/results", DESKTOP, ("light", "dark")),
        ("home-phone", "#/", PHONE, ("light",)),
        ("results-phone", f"#/study/{study}/results", PHONE, ("light",)),
    ]
    if running:
        pages.insert(4, ("running", f"#/study/{running}/running", DESKTOP, ("light", "dark")))
    return pages


def _settled(path: Path, ready, timeout: float = 60.0) -> bool:
    """Wait until `ready()` holds and `path` stops changing, or the timeout."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        if ready():
            now = path.stat().st_size if path.is_file() else None
            if now == last:
                return True
            last = now
        time.sleep(0.5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--study", required=True, help="a finished study in the library")
    parser.add_argument("--running", help="a study that is running now")
    parser.add_argument("--out", type=Path, default=HERE)
    parser.add_argument("--browser", default=next((b for b in BROWSERS if Path(b).exists()), None))
    args = parser.parse_args()
    if not args.browser:
        sys.exit("no Chromium browser found: give --browser PATH")
    server = AppServer(args.home, port=0)
    server.start()
    try:
        for name, route, (width, height), themes in shots(args.study, args.running):
            for theme in themes:
                target = args.out / f"{name}-{theme}.png"
                target.unlink(missing_ok=True)
                url = f"http://127.0.0.1:{server.port}/?theme={theme}{route}"
                # A profile of its own each time: a browser that finds its
                # profile in use hands the page to that instance. And Edge's
                # launcher returns at once, the picture a few seconds later:
                # wait for the file, then for the browser to let go of the
                # profile.
                profile = Path(tempfile.mkdtemp(prefix="evk-shot-"))
                done = subprocess.run([
                    args.browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                    "--disable-extensions", f"--user-data-dir={profile}",
                    f"--window-size={width},{height}", "--virtual-time-budget=15000", f"--screenshot={target}", url,
                ], capture_output=True, text=True, timeout=120)
                if not _settled(target, lambda: target.is_file() and target.stat().st_size > 0):
                    sys.exit(f"{target.name} was not written: {(done.stderr or done.stdout).strip()[-300:]}")
                _settled(profile, lambda: not (profile / "lockfile").exists())
                shutil.rmtree(profile, ignore_errors=True)
                print(target.name, flush=True)
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
