"""The smoke test of the built Windows app, run by hand after `python tasks.py dist`:

    .venv/Scripts/python.exe tests/dist_smoke.py [ZIP] [--pyvrp-python PATH] [--keep]

It extracts the app's zip into a fresh folder whose name has a space and a
non-ASCII letter, simulates a user profile there, strips every Python from
`PATH` for the exes it starts (and proves it), and then drives the bundle's own
`evolvekit-cli.exe` and `evolvekit-app.exe` alone: the version, the bundled
interpreter, `doctor`, the PyVRP harness zip, the app's page, a demo-tour study
from start to verdict, the stop, and that the bundle folder is unchanged. No
`--home` is passed anywhere: the default home is what is tested (D25).

ZIP defaults to the newest `dist/evolvekit-app-*-win64.zip`; the PyVRP harness
zip is the `pyvrp-*.zip` beside it. Not collected by pytest (no `test_`
prefix): it needs a built zip, Windows and a few minutes. Exit 0 with
`smoke: all 13 checks passed` as its last line; exit 1 at the first failed
check, named, with the command's output; exit 2 when it cannot start.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PYTHON_NAMES = ("python.exe", "python3.exe", "pythonw.exe", "py.exe")
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CLI_TIMEOUT_S = 180
APP_WAIT_S = 15
PORT = "8795"  # the first port the app tries: never the owner's 8780


class Failed(Exception):
    """A check that did not hold: its message says what, with the command's output."""


def python_free_path(path: str) -> str:
    """The `os.pathsep` entries of `path` without any folder that holds a
    Python (`python.exe`, `python3.exe`, `pythonw.exe`, `py.exe`), nor any
    `...\\WindowsApps` folder (the Store's python aliases)."""
    kept = []
    for entry in path.split(os.pathsep):
        folder = entry.strip().strip('"')
        if not folder:
            continue
        if Path(folder).name.lower() == "windowsapps":
            continue
        if any(os.path.isfile(os.path.join(folder, name)) for name in PYTHON_NAMES):
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


def clean_environment(profile: Path) -> dict[str, str]:
    """`os.environ` as a fresh user `profile` sees it: its home and app-data
    folders (made), `PATH` without any Python, and no evolvekit or Python
    variables of this machine's."""
    env = dict(os.environ)
    drive, rest = os.path.splitdrive(str(profile))
    folders = {"APPDATA": profile / "AppData" / "Roaming", "LOCALAPPDATA": profile / "AppData" / "Local",
               "TEMP": profile / "AppData" / "Local" / "Temp", "TMP": profile / "AppData" / "Local" / "Temp"}
    for folder in (profile, *folders.values()):
        folder.mkdir(parents=True, exist_ok=True)
    env.update({"USERPROFILE": str(profile), "HOME": str(profile), "HOMEDRIVE": drive, "HOMEPATH": rest,
                **{name: str(folder) for name, folder in folders.items()},
                "PATH": python_free_path(os.environ.get("PATH", ""))})
    for name in ("EVOLVEKIT_HOME", "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "EVOLVEKIT_NO_DOTENV"):
        env.pop(name, None)
    return env


def snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    """`{relative path: (size, crc32)}` of every file under `folder`; a folder is `(-1, 0)`."""
    files = {}
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder).as_posix()
        if path.is_dir():
            files[relative] = (-1, 0)
        else:
            files[relative] = (path.stat().st_size, zlib.crc32(path.read_bytes()))
    return files


class Smoke:
    def __init__(self, zip_path: Path, pyvrp_zip: Path, pyvrp_python: Path | None):
        self.zip_path, self.pyvrp_zip, self.pyvrp_python = zip_path, pyvrp_zip, pyvrp_python
        self.tmp: Path | None = None
        self.bundle = self.profile = self.home = None
        self.env: dict[str, str] = {}
        self.app: subprocess.Popen | None = None
        self.slug: str | None = None
        self.before: dict[str, tuple[int, int]] = {}
        self.waited: dict = {}
        self.url = self.token = ""

    # -- helpers

    @property
    def cli(self) -> str:
        return str(self.bundle / "evolvekit-cli.exe")

    def run(self, *args: str, timeout: float = CLI_TIMEOUT_S, codes: tuple[int, ...] | None = (0,)) -> subprocess.CompletedProcess:
        """`evolvekit-cli.exe ARGS` in the profile, its exit one of `codes` (None: any)."""
        argv = [self.cli, *args]
        try:
            done = subprocess.run(argv, cwd=str(self.profile), env=self.env, capture_output=True,
                                  encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise Failed(f"{subprocess.list2cmdline(argv)} did not end within {timeout:g} s\n"
                         f"{exc.stdout or ''}{exc.stderr or ''}") from None
        if codes is not None and done.returncode not in codes:
            raise Failed(f"{subprocess.list2cmdline(argv)} exited {done.returncode} (wanted {' or '.join(map(str, codes))})\n"
                         f"--- stdout\n{done.stdout}--- stderr\n{done.stderr}")
        return done

    def bundle_processes(self) -> list[str]:
        """`pid name` of every process whose exe is inside the bundle folder."""
        pattern = str(self.bundle).replace("'", "''") + "\\*"
        script = (f"Get-CimInstance Win32_Process | Where-Object {{ $_.ExecutablePath -like '{pattern}' }} | "
                  "ForEach-Object { \"$($_.ProcessId) $($_.Name)\" }")
        done = subprocess.run([_powershell(), "-NoProfile", "-Command", script], cwd=str(self.profile), env=self.env,
                              capture_output=True, encoding="utf-8", errors="replace", timeout=60)
        return [line for line in done.stdout.splitlines() if line.strip()]

    # -- the checks

    def setup(self) -> str:
        self.tmp = Path(tempfile.mkdtemp(prefix="evolvekit smoke ë "))
        with zipfile.ZipFile(self.zip_path) as archive:
            archive.extractall(self.tmp)
        self.bundle = self.tmp / "evolvekit-app"
        if not (self.bundle / "evolvekit-cli.exe").is_file() or not (self.bundle / "evolvekit-app.exe").is_file():
            raise Failed(f"{self.zip_path.name} holds no evolvekit-app\\evolvekit-cli.exe and evolvekit-app.exe")
        self.profile = self.tmp / "Users" / "Zoë Smoke"
        self.home = self.profile / "evolvekit"
        self.env = clean_environment(self.profile)
        found = {name: shutil.which(name, path=self.env["PATH"]) for name in ("python", "python3", "py")}
        if any(found.values()):
            raise Failed(f"a Python is still on PATH, so the smoke test would prove nothing: {found}")
        self.before = snapshot(self.bundle)
        return f"setup: the bundle in {self.bundle}, the profile {self.profile}, no Python on PATH"

    def check_version(self) -> str:
        import tasks

        out = self.run("--version").stdout.strip()
        if out != f"evolvekit {tasks.project_version()}":
            raise Failed(f"--version printed {out!r}, wanted 'evolvekit {tasks.project_version()}'")
        return f"evolvekit-cli.exe --version: {out}"

    def check_executable(self) -> str:
        out = self.run("-c", "import sys; print(sys.executable)").stdout.strip()
        if not os.path.samefile(out, self.cli):
            raise Failed(f"sys.executable is {out}, not the bundle's evolvekit-cli.exe")
        return "sys.executable is the bundle's evolvekit-cli.exe"

    def check_utf8(self) -> str:
        out = self.run("-c", "import sys; print(sys.flags.utf8_mode, sys.stdout.encoding)").stdout.strip()
        if out != "1 utf-8":
            raise Failed(f"utf8_mode and stdout encoding are {out!r}, wanted '1 utf-8'")
        return "UTF-8 mode, stdout utf-8"

    def check_doctor(self) -> str:
        done = self.run("doctor", "--json", codes=(0, 1))
        try:
            items = json.loads(done.stdout)
        except ValueError:
            raise Failed(f"doctor --json printed no JSON\n{done.stdout}{done.stderr}") from None
        if not isinstance(items, list) or not all(isinstance(i, dict) and set(i) == {"ok", "name", "message", "fix"}
                                                  for i in items):
            raise Failed(f"doctor --json is not a list of {{ok, name, message, fix}}\n{done.stdout}")
        by_name = {item["name"]: item for item in items}
        python = by_name.get("python", {}).get("message", "")
        if not python.startswith("bundled"):
            raise Failed(f"doctor's python check says {python!r}, wanted 'bundled ...'\n{done.stdout}")
        if str(self.home) not in by_name.get("home", {}).get("message", ""):
            raise Failed(f"doctor's home check does not name {self.home}\n{done.stdout}")
        return f"doctor --json: python {python}; home {self.home}"

    def check_pyvrp_zip(self) -> str:
        installed = self.home / "harnesses" / "pyvrp-1.0.0"
        out = self.run("harness", "install", str(self.pyvrp_zip)).stdout.strip()
        if out != f"installed PyVRP 1.0.0 in {installed}":
            raise Failed(f"harness install printed {out!r}, wanted 'installed PyVRP 1.0.0 in {installed}'")
        listed = self.run("harness", "list").stdout
        rows = sorted(tuple(line.split()[:3]) for line in listed.splitlines() if line.strip())
        wanted = [("demo-tour", "1.1.0", "built-in"), ("prompt-tune", "1.0.0", "built-in"), ("pyvrp", "1.0.0", "installed")]
        if [(r[0], r[2]) for r in rows] != [(w[0], w[2]) for w in wanted]:
            raise Failed(f"harness list is not demo-tour and prompt-tune built-in, pyvrp installed\n{listed}")
        if self.pyvrp_python is None:
            return f"{self.pyvrp_zip.name} installed and listed; PyVRP Python not given: harness check skipped"
        done = self.run("harness", "check", str(installed), "--app", str(self.pyvrp_python), "--samples", "1", "--json",
                        timeout=600, codes=(0, 1))
        try:
            ok = json.loads(done.stdout).get("ok")
        except (ValueError, AttributeError):
            raise Failed(f"harness check --json printed no JSON\n{done.stdout}{done.stderr}") from None
        return (f"{self.pyvrp_zip.name} installed and listed; harness check with {self.pyvrp_python} "
                f"exit {done.returncode}, ok {ok}")

    def check_app_starts(self) -> str:
        self.app = subprocess.Popen([str(self.bundle / "evolvekit-app.exe"), "--no-browser", "--port", PORT],
                                    cwd=str(self.profile), env=self.env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
        app_json = self.home / "app.json"
        deadline = time.monotonic() + APP_WAIT_S
        while time.monotonic() < deadline:
            try:
                doc = json.loads(app_json.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                doc = None
            if isinstance(doc, dict) and doc.get("url"):
                self.url = str(doc["url"])
                self.token = (urllib.parse.parse_qs(urllib.parse.urlparse(self.url).query).get("token") or [""])[-1]
                if not self.token:
                    raise Failed(f"app.json names {self.url}, which has no token")
                return f"evolvekit-app.exe --no-browser wrote app.json (pid {doc.get('pid')}, {urllib.parse.urlparse(self.url).netloc})"
            if self.app.poll() is not None:
                raise Failed(f"evolvekit-app.exe ended with exit {self.app.returncode} before writing app.json")
            time.sleep(0.2)
        raise Failed(f"no app.json within {APP_WAIT_S} s — an antivirus scan of a new exe can take longer; run it again")

    def check_api_home(self) -> str:
        parts = urllib.parse.urlparse(self.url)
        request = urllib.request.Request(f"{parts.scheme}://{parts.netloc}/api/home",
                                         headers={"X-Evolvekit-Token": self.token})
        try:
            with urllib.request.urlopen(request, timeout=30) as answer:
                status, body = answer.status, answer.read().decode("utf-8")
        except OSError as exc:
            raise Failed(f"GET /api/home failed: {exc}") from None
        if status != 200:
            raise Failed(f"GET /api/home answered {status}\n{body}")
        doc = json.loads(body)
        if not isinstance(doc, dict) or not isinstance(doc.get("studies"), list):
            raise Failed(f"GET /api/home has no list of studies\n{body[:2000]}")
        harnesses = {h.get("id"): h.get("built_in") for h in doc.get("harnesses") or []}
        if harnesses != {"demo-tour": True, "prompt-tune": True, "pyvrp": False}:
            raise Failed(f"GET /api/home's harnesses are {harnesses}, wanted demo-tour and prompt-tune built in, pyvrp installed")
        return "GET /api/home: 200, studies a list, demo-tour and prompt-tune built in, pyvrp installed"

    def check_study_list(self) -> str:
        self.run("study", "list")
        return "study list"

    def check_study(self) -> str:
        import yaml

        from evolvekit.app.store import Home

        solver = self.bundle / "_internal" / "examples" / "cli-solver" / "solver.py"
        made = self.run("study", "new", "Smoke tour", "--harness", "demo-tour", "--template", "tune", "--app", str(solver),
                        "--json").stdout
        try:
            self.slug = json.loads(made)["slug"]
        except (ValueError, KeyError, TypeError):
            raise Failed(f"study new --json printed no slug\n{made}") from None
        samples = sorted((self.bundle / "_internal" / "harnesses" / "demo-tour" / "samples").glob("*.json"))
        if len(samples) != 4:
            raise Failed(f"the bundle has {len(samples)} demo-tour samples, wanted 4: {samples}")
        self.run("study", "cases", self.slug, "--add", *map(str, samples), "--hold-back", "1")
        # The suite's small plan (tests/test_cli_study.py `_ready`): preparation, not a run.
        doc = yaml.safe_load((self.home / "studies" / self.slug / "study.yaml").read_text(encoding="utf-8"))
        Home(self.home).save_step(self.slug, {
            "limits": {**(doc.get("limits") or {}), "time_per_case_s": 0.5, "retries": 1, "runs_per_case": 1},
            "budget": {**(doc.get("budget") or {}), "hours": 0.05, "ai": {"enabled": False, "max_usd": 2.0}},
            "plan": {**(doc.get("plan") or {}), "auto": False, "children": 2, "generations": 2},
        })
        started = self.run("study", "start", self.slug, "--json").stdout
        documents = [json.loads(line) for line in started.splitlines() if line.strip().startswith("{")]
        if not any(d.get("started") is True for d in documents):
            raise Failed(f"study start --json did not say started\n{started}")
        waited = self.run("study", "wait", self.slug, "--json", "--timeout", "900", timeout=960).stdout
        try:
            self.waited = json.loads(waited)
        except ValueError:
            raise Failed(f"study wait --json printed no JSON\n{waited}") from None
        return f"study {self.slug}: made, cases added (1 held back), started (run {self.waited.get('run')}), waited: done"

    def check_verdict(self) -> str:
        from evolvekit.app.verdict import DECISIONS

        decision, line = self.waited.get("decision"), self.waited.get("line")
        if decision not in DECISIONS or not line:
            raise Failed(f"the verdict has decision {decision!r} and line {line!r}\n{json.dumps(self.waited)[:2000]}")
        print(f"verdict: {line}", flush=True)
        self.run("study", "verdict", self.slug)
        return f"the verdict: {decision}; study verdict"

    def check_app_stops(self) -> str:
        out = self.run("app", "stop").stdout.strip()
        if out != "stopped the app":
            raise Failed(f"app stop printed {out!r}, wanted 'stopped the app'")
        if (self.home / "app.json").exists():
            raise Failed("app.json is still there after app stop")
        try:
            self.app.wait(timeout=APP_WAIT_S)
        except subprocess.TimeoutExpired:
            raise Failed(f"evolvekit-app.exe has not ended {APP_WAIT_S} s after app stop") from None
        return f"app stop: stopped the app, app.json gone, evolvekit-app.exe ended (exit {self.app.returncode})"

    def check_no_process(self) -> str:
        deadline = time.monotonic() + 10
        while True:
            left = self.bundle_processes()
            if not left or time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        if left:
            raise Failed(f"processes from the bundle are still running: {left}")
        return "no process from the bundle is left"

    def check_bundle_unchanged(self) -> str:
        after = snapshot(self.bundle)
        added = sorted(set(after) - set(self.before))
        removed = sorted(set(self.before) - set(after))
        changed = sorted(p for p in set(after) & set(self.before) if after[p] != self.before[p])
        if added or removed or changed:
            raise Failed(f"the bundle folder changed: added {added[:20]}, removed {removed[:20]}, changed {changed[:20]}")
        runs = self.home / "studies" / self.slug / "runs"
        held = [p.name for p in runs.iterdir() if p.is_dir()] if runs.is_dir() else []
        if not held:
            raise Failed(f"{runs} holds no run")
        return f"the bundle unchanged ({len(self.before)} entries, size and CRC-32); the run in {runs / held[0]}"

    # -- the end, whatever failed

    def cleanup(self, keep: bool, failed: bool) -> None:
        if self.bundle is None:
            if self.tmp is not None and not keep:
                shutil.rmtree(self.tmp, ignore_errors=True)
            return
        try:
            if self.slug and failed:  # a run cut short by a failed check
                self.run("study", "stop", self.slug, codes=None, timeout=60)
            self.run("app", "stop", codes=None, timeout=60)
        except Failed as exc:
            print(f"cleanup: {exc}", file=sys.stderr)
        if self.app is not None and self.app.poll() is None:
            self.app.kill()
            self.app.wait(timeout=30)
        try:
            left = self.bundle_processes()
        except (OSError, subprocess.SubprocessError):
            left = []
        for row in left:  # only processes of this temporary bundle
            print(f"cleanup: stopping {row}", file=sys.stderr)
            subprocess.run(["taskkill", "/F", "/T", "/PID", row.split()[0]], capture_output=True, timeout=30)
        if keep:
            print(f"kept: {self.tmp}")
        else:
            time.sleep(1)  # Windows releases the exes' files a moment after they end
            shutil.rmtree(self.tmp, ignore_errors=True)


def _powershell() -> str:
    return shutil.which("powershell") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def _newest(folder: Path, pattern: str) -> Path | None:
    found = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="tests/dist_smoke.py", description=__doc__.splitlines()[0])
    parser.add_argument("zip", nargs="?", help="the app's zip (default: the newest dist/evolvekit-app-*-win64.zip)")
    parser.add_argument("--pyvrp-python", help="a Python with PyVRP, for harness check "
                                               "(default: the checkout's .venv-pyvrp when it exists)")
    parser.add_argument("--keep", action="store_true", help="keep the temporary folder and print its path")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("error: the smoke test runs the Windows app; run it on Windows", file=sys.stderr)
        return 2
    zip_path = Path(args.zip).resolve() if args.zip else _newest(ROOT / "dist", "evolvekit-app-*-win64.zip")
    pyvrp_zip = _newest(zip_path.parent, "pyvrp-*.zip") if zip_path is not None and zip_path.is_file() else None
    if zip_path is None or not zip_path.is_file() or pyvrp_zip is None:
        print("error: no app zip and pyvrp zip found: run python tasks.py dist first", file=sys.stderr)
        return 2
    default_pyvrp = ROOT / ".venv-pyvrp" / "Scripts" / "python.exe"
    pyvrp_python = Path(args.pyvrp_python) if args.pyvrp_python else (default_pyvrp if default_pyvrp.is_file() else None)

    smoke = Smoke(zip_path, pyvrp_zip, pyvrp_python)
    checks = [
        (1, "the version", smoke.check_version),
        (2, "the bundled interpreter", smoke.check_executable),
        (3, "UTF-8 mode", smoke.check_utf8),
        (4, "doctor", smoke.check_doctor),
        (5, "the PyVRP harness zip", smoke.check_pyvrp_zip),
        (6, "the app starts", smoke.check_app_starts),
        (7, "the app's /api/home", smoke.check_api_home),
        (8, "study list", smoke.check_study_list),
        (9, "a demo-tour study", smoke.check_study),
        (10, "the verdict", smoke.check_verdict),
        (11, "the app stops", smoke.check_app_stops),
        (12, "no process left", smoke.check_no_process),
        (13, "the bundle holds no state", smoke.check_bundle_unchanged),
    ]
    failed = True
    try:
        began = time.monotonic()
        try:
            said = smoke.setup()
        except Failed as exc:
            print(f"✗ 0 setup\n{exc}", flush=True)
            return 1
        print(f"  {said} ({time.monotonic() - began:.1f} s)", flush=True)
        for number, what, check in checks:
            began = time.monotonic()
            try:
                said = check()
            except Failed as exc:
                print(f"✗ {number} {what}\n{exc}", flush=True)
                return 1
            print(f"✓ {number} {said} ({time.monotonic() - began:.1f} s)", flush=True)
        failed = False
    finally:
        smoke.cleanup(args.keep, failed)
    print(f"{zip_path.name}: {zip_path.stat().st_size / (1024 * 1024):.1f} MB")
    print(f"smoke: all {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
