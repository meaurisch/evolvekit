"""Screenshots of the app for docs/app.md: every screen, light and dark, and
a few at phone width.

    python docs/app/make_screenshots.py --home LIBRARY --study SLUG [--running SLUG]

LIBRARY is a library home with a finished study SLUG (the step pages and the
results are taken from it) and, optionally, a study that is running right now
(for the running page). The app is started on a free port inside this
process, and a Chromium browser (Edge or Chrome) in headless mode takes each
picture. The pictures were checked by eye in the in-app browser as well.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
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
DESKTOP, PHONE = (1280, 900), (390, 844)


def shots(study: str, running: str | None) -> list[tuple[str, str, tuple[int, int]]]:
    pages = [
        ("home", "#/", DESKTOP),
        ("new", "#/new", DESKTOP),
        ("question", f"#/study/{study}/1", DESKTOP),
        ("application", f"#/study/{study}/2", DESKTOP),
        ("cases", f"#/study/{study}/3", DESKTOP),
        ("change", f"#/study/{study}/4", DESKTOP),
        ("goal", f"#/study/{study}/5", DESKTOP),
        ("limits", f"#/study/{study}/6", DESKTOP),
        ("review", f"#/study/{study}/7", DESKTOP),
        ("results", f"#/study/{study}/results", DESKTOP),
        ("settings", "#/settings", DESKTOP),
        ("home-phone", "#/", PHONE),
        ("change-phone", f"#/study/{study}/4", PHONE),
        ("results-phone", f"#/study/{study}/results", PHONE),
    ]
    if running:
        pages.insert(9, ("running", f"#/study/{running}/running", DESKTOP))
        pages.append(("running-phone", f"#/study/{running}/running", PHONE))
    return pages


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
        with tempfile.TemporaryDirectory() as profile:
            for name, route, (width, height) in shots(args.study, args.running):
                for theme in ("light", "dark"):
                    if name.endswith("-phone") and theme == "dark" and not name.startswith("home"):
                        continue
                    target = args.out / f"{name}-{theme}.png"
                    url = f"http://127.0.0.1:{server.port}/?theme={theme}{route}"
                    subprocess.run([
                        args.browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                        f"--user-data-dir={profile}", f"--window-size={width},{height}",
                        "--virtual-time-budget=15000", f"--screenshot={target}", url,
                    ], check=True, capture_output=True, timeout=120)
                    print(f"{target.name}")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
