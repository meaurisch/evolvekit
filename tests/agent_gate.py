"""The agent gate (design §4.4): can a fresh coding agent turn one sentence into a running study, and later
report only the verdict?

It builds a temporary repository -- the skills installed, the demo-tour harness in `harness/`, its samples in
`cases/`, `solver.py` at the root -- and a clean library home; runs the tool once, non-interactively, with the
sentence; checks what it left in the home and what it said; then waits for the study itself, runs the tool a
second time, fresh, with "report its result", and checks that answer against the verdict document.

Run it by hand, never by pytest (the file is not named `test_*`) and never beside `python tasks.py check` (it
runs a real study, and the full gate has timing-sensitive tests):

    .venv/Scripts/python.exe tests/agent_gate.py [--tool claude|copilot] [--sentence "..."] [--expect-question]
        [--timeout S] [--wait-timeout S] [--log-dir DIR] [--keep]
    .venv/Scripts/python.exe tests/agent_gate.py --fixture DIR [--no-harness]

Exit 0 when every check passes, 1 when one fails, 2 when the tool is not installed (the gate did not run).
`--fixture DIR` only builds `DIR/repo` and `DIR/home` (the persona rounds' materials) and prints the three
lines to set before working there.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evolvekit import skills  # noqa: E402
from evolvekit.app import jobs  # noqa: E402
from evolvekit.app.running import read_running  # noqa: E402
from evolvekit.app.store import Home  # noqa: E402
from evolvekit.app.verdict import build_verdict  # noqa: E402
from evolvekit.harness.study import load_study  # noqa: E402

DEFAULT_SENTENCE = ("Use evolvekit to find solver settings that give shorter tours on the requests in cases/; "
                    "the solver is solver.py in this folder; hold two back; ten minutes in total; "
                    "today's settings are the starting point.")
MISSING_TIME_SENTENCE = DEFAULT_SENTENCE.replace("ten minutes in total; ", "")
REPORT_PROMPT = "The evolvekit study {slug} has finished; report its result."

ALLOWED_TOOLS = ["Bash", "PowerShell", "Read", "Edit", "Write", "Glob", "Grep"]
TOOLS: dict[str, list[str]] = {
    "claude": ["-p", "--allowedTools", ",".join(ALLOWED_TOOLS), "--add-dir", "{home}",
               "--no-session-persistence", "--output-format", "text"],
    "copilot": ["-p", "{sentence}", "--allow-all-tools", "--add-dir", "{home}"],
}
"""Each tool's one-shot flags. claude reads the sentence on stdin (a .cmd on Windows mangles quotes in
arguments); `--add-dir` lets it edit study.yaml in the library home, outside the repository. copilot's flags are
the plan's and unverified: it was not installed when the gate was written."""

LIVE = ("queued", "starting", "search", "check")
CLAIM_WORDS = re.compile(r"\b(better|improved|faster|cheaper)\b", re.IGNORECASE)
PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s?%")
README = "# Tour solver\nsolver.py plans a tour through the stops of each request in cases/.\n"


class ToolMissing(Exception):
    """The tool is not on PATH: the gate cannot run."""


def build_fixture(repo: Path, *, home: Path, harness: bool = True) -> None:
    """The engineer's repository, with the skills installed, and an empty library home."""
    repo, home = Path(repo), Path(home)
    repo.mkdir(parents=True)
    home.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    skills.install(repo)
    settings = {"permissions": {"allow": list(ALLOWED_TOOLS), "additionalDirectories": [str(home)]}}
    (repo / ".claude").mkdir(exist_ok=True)
    (repo / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    if harness:
        shutil.copytree(ROOT / "harnesses" / "demo-tour", repo / "harness",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "examples" / "cli-solver" / "solver.py", repo / "solver.py")
    (repo / "cases").mkdir()
    for sample in sorted((ROOT / "harnesses" / "demo-tour" / "samples").glob("*.json")):
        shutil.copy2(sample, repo / "cases" / sample.name)
    with open(repo / "README.md", "w", encoding="utf-8", newline="\n") as handle:
        handle.write(README)


def gate_env(home: Path) -> dict[str, str]:
    """The tool's environment: this home, and the venv's `Scripts` first so `evolvekit` resolves."""
    return {**os.environ, "EVOLVEKIT_HOME": str(home), "PYTHONIOENCODING": "utf-8",
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"}


def tool_command(tool: str, sentence: str, home: Path) -> tuple[list[str], str | None]:
    """The argv of one fresh, one-shot run of the tool, and the text for its stdin (None: nothing)."""
    exe = shutil.which(tool)
    if exe is None:
        raise ToolMissing(f"{tool} is not installed")
    flags = TOOLS[tool]
    argv = [exe, *(a.format(home=home, sentence=sentence) for a in flags)]
    return argv, (sentence if "{sentence}" not in flags else None)


def _text(data: str | bytes | None) -> str:
    if data is None:
        return ""
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data


def run_tool(tool: str, cwd: Path, sentence: str, *, timeout_s: int, env: Mapping[str, str],
             transcript: Path) -> str:
    """Run the tool once (never resuming a session); write the redacted transcript; return the raw output."""
    argv, stdin = tool_command(tool, sentence, Path(env["EVOLVEKIT_HOME"]))
    try:
        done = subprocess.run(argv, cwd=cwd, env=dict(env), input=stdin, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout_s)
        output = _text(done.stdout) + _text(done.stderr)
    except subprocess.TimeoutExpired as exc:
        output = _text(exc.stdout) + _text(exc.stderr) + f"\n[the gate stopped the tool after {timeout_s} s]"
    transcript = Path(transcript)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    with open(transcript, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(redact(f"$ {tool} <<< {sentence}\n\n{output}"))
    return output


def redact(text: str) -> str:
    return re.sub(r"token=[^\s&#\"')]+", "token=…", text)


def claims(text: str, allowed: Sequence[str] = ()) -> list[str]:
    """The claim words, then the percentages, in `text` once every allowed sentence is taken out."""
    for sentence in allowed:
        if sentence:
            text = text.replace(sentence, "")
    return [m.group(0) for m in CLAIM_WORDS.finditer(text)] + [m.group(0) for m in PERCENT.finditer(text)]


def the_study(home: Path) -> tuple[str, Path] | None:
    """The study the agent made: the one with the newest run (a study without a run loses; ties: the first)."""
    store = Home(home)
    slugs = store.slugs()
    if not slugs:
        return None
    best: tuple[str, Path] | None = None
    best_run = ""
    for slug in slugs:
        root = store.study_root(slug)
        run = jobs.latest_run(root) or ""
        if best is None or run > best_run:
            best, best_run = (slug, root), run
    return best


def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def checks(home: Path, output: str) -> list[dict]:
    """The first answer's checks: a study exists, its application is set, it is started or queued, the output
    names the study's URL, and it claims no improvement."""
    found = the_study(home)
    results = [_check("study_exists", found is not None, f"study {found[0]}" if found else "no study in the home")]
    if found is None:
        results.append(_check("application_set", False, "no study"))
        results.append(_check("study_started_or_queued", False, "no study"))
    else:
        slug, root = found
        try:
            application = load_study(root).application_path
            results.append(_check("application_set", application, application or "study.yaml names no application"))
        except Exception as exc:  # noqa: BLE001 - a check fails, the gate goes on
            results.append(_check("application_set", False, f"study.yaml does not load: {exc}"))
        run = jobs.latest_run(root)
        if run is None:
            results.append(_check("study_started_or_queued", False, "the study has no run"))
        else:
            now = jobs.phase(root / "runs" / run).get("phase")
            results.append(_check("study_started_or_queued", now in LIVE, f"run {run} is {now}"))
    named = "http://127.0.0.1:" in output and "#/study/" in output
    results.append(_check("output_names_the_url", named, "" if named else "no http://127.0.0.1:…#/study/… URL"))
    said = claims(output)
    results.append(_check("no_claim_of_improvement", not said, ", ".join(said)))
    return results


def question_checks(home: Path, output: str) -> list[dict]:
    """The incomplete sentence's checks: no study was made, and the answer asks for the total time."""
    slugs = Home(home).slugs()
    asking = [s for s in re.split(r"(?<=[.?!])\s+", output.strip())
              if s.endswith("?") and re.search(r"\b(time|minutes?|hours?|how long)\b", s, re.IGNORECASE)]
    return [_check("no_study_made", not slugs, ", ".join(slugs)),
            _check("asks_the_missing_question", bool(asking), asking[0] if asking else "no question about the time")]


def report_checks(document: dict, output: str) -> list[dict]:
    """The second answer: the verdict's line verbatim, and no other percentage (the words are the persona's
    to judge, the percentages here -- spec §4.4)."""
    line = document.get("line") or ""
    problems = []
    if not line or line not in output:
        problems.append(f"the verdict's line is missing: {line!r}")
    extra = [c for c in claims(output, allowed=(line, document.get("advice") or "")) if PERCENT.fullmatch(c)]
    if extra:
        problems.append("other percentages: " + ", ".join(extra))
    return [_check("report_equals_verdict_line", not problems, "; ".join(problems))]


def _evolvekit(*args: str, env: Mapping[str, str], timeout: float | None = None) -> int:
    return subprocess.run([sys.executable, "-m", "evolvekit", *args], env=dict(env), timeout=timeout).returncode


def _clean_up(work: Path, home: Path, env: Mapping[str, str]) -> None:
    found = the_study(home) if home.is_dir() else None
    if found is not None:
        slug, root = found
        run = jobs.latest_run(root)
        if run is not None and jobs.phase(root / "runs" / run).get("phase") in LIVE:
            try:
                _evolvekit("study", "stop", slug, "--home", str(home), env=env, timeout=120)
            except Exception:  # noqa: BLE001 - cleaning up, best effort
                pass
    try:
        app = read_running(home)
        if app and app.get("pid"):
            os.kill(int(app["pid"]), signal.SIGTERM)
    except Exception:  # noqa: BLE001 - cleaning up, best effort
        pass
    shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The agent gate: one sentence to a running study, then the verdict.")
    parser.add_argument("--tool", choices=sorted(TOOLS), default="claude")
    parser.add_argument("--sentence", default=DEFAULT_SENTENCE)
    parser.add_argument("--expect-question", action="store_true",
                        help="judge with the question checks (use with the sentence that lacks the total time)")
    parser.add_argument("--timeout", type=int, default=900, help="seconds per tool run (default 900)")
    parser.add_argument("--wait-timeout", type=int, default=1800, help="seconds to wait for the study (default 1800)")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "docs" / "superpowers" / "reviews" / "agent-gate")
    parser.add_argument("--keep", action="store_true", help="leave the folder, the app and the run")
    parser.add_argument("--fixture", type=Path, metavar="DIR",
                        help="only build DIR/repo and DIR/home and print the lines to set there")
    parser.add_argument("--no-harness", action="store_true", help="with --fixture: no harness/ in the repository")
    args = parser.parse_args(argv)

    if args.fixture is not None:
        where = args.fixture.resolve()
        build_fixture(where / "repo", home=where / "home", harness=not args.no_harness)
        print(f'EVOLVEKIT_HOME="{where / "home"}"')
        print(f'PATH="{Path(sys.executable).parent}{os.pathsep}…"')
        print(f'cd "{where / "repo"}"')
        return 0

    try:
        tool_command(args.tool, args.sentence, Path("."))
    except ToolMissing as exc:
        print(f"FAIL tool: {exc}")
        return 2

    work = Path(tempfile.mkdtemp(prefix="evk gate "))
    repo, home = work / "repo", work / "home"
    build_fixture(repo, home=home)
    env = gate_env(home)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    transcripts = [args.log_dir / f"{stamp}-{args.tool}-1.txt"]
    try:
        out1 = run_tool(args.tool, repo, args.sentence, timeout_s=args.timeout, env=env, transcript=transcripts[0])
        if args.expect_question:
            results = question_checks(home, out1)
        else:
            results = checks(home, out1)
            live = {r["name"]: r["ok"] for r in results}.get("study_started_or_queued")
            found = the_study(home)
            if live and found is not None:
                slug, root = found
                code = _evolvekit("study", "wait", slug, "--home", str(home), "--timeout", str(args.wait_timeout),
                                  env=env)
                if code != 0:
                    results.append(_check("study_finished", False, f"study wait exited {code}"))
                else:
                    document = build_verdict(root)
                    transcripts.append(args.log_dir / f"{stamp}-{args.tool}-2.txt")
                    out2 = run_tool(args.tool, repo, REPORT_PROMPT.format(slug=slug), timeout_s=args.timeout,
                                    env=env, transcript=transcripts[1])
                    results += report_checks(document, out2)
        for result in results:
            print(f"PASS {result['name']}" if result["ok"] else f"FAIL {result['name']}: {result['detail']}")
        for path in transcripts:
            print(f"transcript: {path}")
        if args.keep:
            print(f"kept: {work}")
    finally:
        if not args.keep:
            _clean_up(work, home, env)
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
