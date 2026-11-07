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
    .venv/Scripts/python.exe tests/agent_gate.py --fixture DIR [--no-harness] [--shell bash|powershell] [--force]

Exit 0 when every check passes, 1 when one fails (a failure is always a FAIL line, never a traceback), 2 when
the tool is not installed (the gate did not run). `--fixture DIR` only builds `DIR/repo` and `DIR/home` (the
persona rounds' materials) and prints, in the chosen shell's syntax, the lines to set before working there
(one unset per session variable the gate drops) and the one-shot, isolated command to run there.
"""

from __future__ import annotations

import argparse
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

ALLOWED_TOOLS = ["Bash", "PowerShell", "Read", "Edit", "Write", "Glob", "Grep", "Skill"]
ISOLATION = ["--setting-sources", "project", "--strict-mcp-config", "--permission-prompts", "none"]
"""A fresh agent, not the operator's: only project settings (no user settings, so no user plugins; the fixture
writes none, since an untrusted workspace ignores them and these flags carry the run),
no MCP servers, and anything that would prompt is denied instead of waiting (claude 2.1.304's `--help`)."""
TOOLS: dict[str, list[str]] = {
    "claude": ["-p", "--allowedTools", ",".join(ALLOWED_TOOLS), "--add-dir", "{home}", *ISOLATION,
               "--no-session-persistence", "--output-format", "text"],
    "copilot": ["-p", "{sentence}", "--allow-all-tools", "--add-dir", "{home}"],
}
"""Each tool's one-shot flags. claude reads the sentence on stdin (a .cmd on Windows mangles quotes in
arguments); `--add-dir` lets it edit study.yaml in the library home, outside the repository. copilot's flags are
the plan's and unverified: it was not installed when the gate was written."""
SHELLS = ("bash", "powershell")

LIVE = ("queued", "starting", "search", "check")
CLAIM_WORDS = re.compile(r"\b(better|improved|faster|cheaper)\b", re.IGNORECASE)
PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s?%")
README = "# Tour solver\nsolver.py plans a tour through the stops of each request in cases/.\n"


class ToolMissing(Exception):
    """The tool is not on PATH: the gate cannot run."""


KNOWN_VERSION = 'version="cli-solver 1.0"'
UNKNOWN_VERSION = 'version="tour-solver 0.9"'
UNKNOWN_DOCSTRING = '''"""tour-solver: plans a tour through the stops of a request.

    solver.py --instance FILE --seed N --time-limit SECONDS [--flag value ...]

It logs progress lines, then prints one line of JSON with the result. A
request lists its stops, `"stops": [[x, y, weight], ...]` (or names a seed and
a number of cities, placed at random). A leg between two stops costs its
length times the mean of their weights; `cost` is what the solver minimised,
`length` the tour's plain length. The tour starts random or nearest-neighbour
and is improved by 2-opt moves, with a few knobs.
"""
'''
NEUTRAL_CASES = [f"requests-2026-03-0{day}.json" for day in (2, 3, 4, 5)]
"""The no-harness fixture's cases: the demo samples, in their sorted order, under an engineer's own names."""


def _unknown_solver(text: str) -> str:
    """The example solver as an engineer's own program: its own docstring (the example's names evolvekit and
    its demo harness) and its own `--version` (demo-tour's probe expects "cli-solver N.N"); the code is the same."""
    head, _, rest = text.partition('"""')
    _, _, code = rest.partition('"""\n')
    assert head == "" and code.count(KNOWN_VERSION) == 1, "examples/cli-solver/solver.py changed its shape"
    return UNKNOWN_DOCSTRING + code.replace(KNOWN_VERSION, UNKNOWN_VERSION)


def build_fixture(repo: Path, *, home: Path, harness: bool = True) -> None:
    """The engineer's repository, with the skills installed, and an empty library home."""
    repo, home = Path(repo), Path(home)
    repo.mkdir(parents=True)
    home.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    skills.install(repo)
    if harness:
        shutil.copytree(ROOT / "harnesses" / "demo-tour", repo / "harness",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    solver = ROOT / "examples" / "cli-solver" / "solver.py"
    if harness:
        shutil.copy2(solver, repo / "solver.py")
    else:  # a program evolvekit does not know
        with open(repo / "solver.py", "w", encoding="utf-8", newline="") as handle:
            handle.write(_unknown_solver(solver.read_text(encoding="utf-8")))
    (repo / "cases").mkdir()
    samples = sorted((ROOT / "harnesses" / "demo-tour" / "samples").glob("*.json"))
    names = [sample.name for sample in samples] if harness else NEUTRAL_CASES
    assert len(names) == len(samples), "the demo samples changed: name the no-harness cases again"
    for sample, name in zip(samples, names):
        if harness:
            shutil.copy2(sample, repo / "cases" / name)
            continue
        # the case carries its own name; every other byte (the layout, the line ends) stays as the sample has it
        old, new = f'"name": "{sample.stem}"'.encode(), f'"name": "{Path(name).stem}"'.encode()
        data = sample.read_bytes()
        assert data.count(old) == 1, f"{sample.name}: its name is not where the fixture expects it"
        (repo / "cases" / name).write_bytes(data.replace(old, new))
    with open(repo / "README.md", "w", encoding="utf-8", newline="\n") as handle:
        handle.write(README)


SESSION_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_PREVIEW_")
SESSION_NAMES = ("CLAUDECODE",)
KEEP = ("CLAUDE_CODE_GIT_BASH_PATH", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_SKIP_BEDROCK_AUTH", "CLAUDE_CODE_SKIP_VERTEX_AUTH")
"""The launching coding-agent session's identity: with it the one-shot tool runs as that session's child (or
refuses to start), so a fresh agent sees none of it. `KEEP` is configuration an engineer's sign-in may need, and
every `ANTHROPIC_*` variable (the key, the endpoint, the model) and `CLAUDE_CLI_PATH` (evolvekit's own) are kept."""


def _from_session(name: str) -> bool:
    upper = name.upper()
    if upper in KEEP:
        return False
    return upper in SESSION_NAMES or upper.startswith(SESSION_PREFIXES)


def _session_names() -> list[str]:
    """What `--fixture` unsets: `CLAUDECODE`, and every other name the gate drops that is set here."""
    return ["CLAUDECODE", *sorted(k for k in os.environ if _from_session(k) and k.upper() != "CLAUDECODE")]


def gate_env(home: Path) -> dict[str, str]:
    """The tool's environment: this home, the venv's `Scripts` first so `evolvekit` resolves, and nothing of
    the session that launched the gate."""
    inherited = {k: v for k, v in os.environ.items() if not _from_session(k)}
    return {**inherited, "EVOLVEKIT_HOME": str(home), "PYTHONIOENCODING": "utf-8",
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


def _kill_tree(process: subprocess.Popen) -> None:
    """End the process and everything it started: on Windows the tool is a `.cmd`, so the agent is a grandchild
    holding the pipes, and killing `cmd.exe` alone would leave `communicate()` waiting for it."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, timeout=60)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001 - the plain kill below is the fallback
        pass
    try:
        process.kill()
    except OSError:
        pass


def run_tool(tool: str, cwd: Path, sentence: str, *, timeout_s: int, env: Mapping[str, str],
             transcript: Path) -> str:
    """Run the tool once (never resuming a session); write the redacted transcript; return the raw output.
    On timeout the whole process tree is killed and what it wrote so far is kept."""
    argv, stdin = tool_command(tool, sentence, Path(env["EVOLVEKIT_HOME"]))
    process = subprocess.Popen(argv, cwd=cwd, env=dict(env), text=True, encoding="utf-8", errors="replace",
                               stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=(os.name != "nt"))
    try:
        out, err = process.communicate(stdin, timeout=timeout_s)
        output = _text(out) + _text(err)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            out, err = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        output = _text(out) + _text(err) + f"\n[the gate stopped the tool after {timeout_s} s]"
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


def _figure(text: str) -> str:
    return re.sub(r"\s+", "", text)


def report_checks(document: dict, output: str) -> list[dict]:
    """The second answer: the verdict's line verbatim, and no other percentage (the words are the persona's
    to judge, the percentages here -- spec §4.4)."""
    line = document.get("line") or ""
    problems = []
    if not line or line not in output:
        problems.append(f"the verdict's line is missing: {line!r}")
    advice = document.get("advice") or ""
    # A figure the verdict itself gives (`0.3 %` or `0.3%`) is not another claim: the agent may repeat it in
    # the one sentence of its own (spec §3.5); any other figure is.
    given = {_figure(m.group(0)) for m in PERCENT.finditer(f"{line}\n{advice}")}
    extra = [c for c in claims(output, allowed=(line, advice)) if PERCENT.fullmatch(c) and _figure(c) not in given]
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


def _posix(path: Path) -> str:
    """A path as Git Bash writes it: `C:\\x\\y` -> `/c/x/y`; unchanged elsewhere."""
    text = Path(path).as_posix()
    if re.match(r"^[A-Za-z]:/", text):
        return f"/{text[0].lower()}{text[2:]}"
    return text


def fixture_lines(repo: Path, home: Path, shell: str = "bash") -> list[str]:
    """What to set before working in the fixture, in one shell's syntax, and the command to run there: the
    same one-shot, isolated claude the gate runs, with the message as its argument."""
    scripts = Path(sys.executable).parent
    flags = TOOLS["claude"][1:]
    if shell == "powershell":
        rest = " ".join(f'"{home}"' if a == "{home}" else a for a in flags)
        return [*(f"Remove-Item Env:{name} -ErrorAction SilentlyContinue" for name in _session_names()),
                f'$env:EVOLVEKIT_HOME = "{home}"', f'$env:PATH = "{scripts}{os.pathsep}" + $env:PATH',
                f'Set-Location "{repo}"', f'claude -p "<message>" {rest}']
    rest = " ".join(f'"{Path(home).as_posix()}"' if a == "{home}" else a for a in flags)
    return [*(f"unset {name}" for name in _session_names()),
            f'export EVOLVEKIT_HOME="{Path(home).as_posix()}"', f'export PATH="{_posix(scripts)}:$PATH"',
            f'cd "{Path(repo).as_posix()}"', f'claude -p "<message>" {rest}']


def _fixture(where: Path, *, harness: bool, shell: str, force: bool) -> int:
    where = where.resolve()
    repo, home = where / "repo", where / "home"
    taken = [p for p in (repo, home) if p.exists()]
    if taken and not force:
        print(f"FAIL fixture: {', '.join(str(p) for p in taken)} already exists; "
              "choose another folder, or pass --force to build it again")
        return 1
    for path in taken:
        shutil.rmtree(path)
    try:
        build_fixture(repo, home=home, harness=harness)
    except Exception as exc:  # noqa: BLE001 - a FAIL line, never a traceback
        print(f"FAIL fixture: {exc}")
        return 1
    for line in fixture_lines(repo, home, shell):
        print(line)
    return 0


def _gate(args: argparse.Namespace, work: Path, results: list[dict], transcripts: list[Path]) -> None:
    """The gate's run, every failure a check: nothing here raises."""
    repo, home = work / "repo", work / "home"
    try:
        build_fixture(repo, home=home)
    except Exception as exc:  # noqa: BLE001
        results.append(_check("fixture", False, str(exc)))
        return
    env = gate_env(home)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    transcripts.append(args.log_dir / f"{stamp}-{args.tool}-1.txt")
    try:
        out1 = run_tool(args.tool, repo, args.sentence, timeout_s=args.timeout, env=env, transcript=transcripts[0])
    except Exception as exc:  # noqa: BLE001
        results.append(_check("tool_run", False, f"{type(exc).__name__}: {exc}"))
        return
    try:
        if args.expect_question:
            results.extend(question_checks(home, out1))
            return
        results.extend(checks(home, out1))
        live = {r["name"]: r["ok"] for r in results}.get("study_started_or_queued")
        found = the_study(home)
    except Exception as exc:  # noqa: BLE001
        results.append(_check("checks", False, f"{type(exc).__name__}: {exc}"))
        return
    if not live or found is None:
        return
    slug, root = found
    try:
        code = _evolvekit("study", "wait", slug, "--home", str(home), "--timeout", str(args.wait_timeout), env=env,
                          timeout=args.wait_timeout + 120)
    except Exception as exc:  # noqa: BLE001
        results.append(_check("study_finished", False, f"study wait: {type(exc).__name__}: {exc}"))
        return
    if code != 0:
        results.append(_check("study_finished", False, f"study wait exited {code}"))
        return
    try:
        document = build_verdict(root)
    except Exception as exc:  # noqa: BLE001
        results.append(_check("report_equals_verdict_line", False, f"no verdict: {type(exc).__name__}: {exc}"))
        return
    transcripts.append(args.log_dir / f"{stamp}-{args.tool}-2.txt")
    try:
        out2 = run_tool(args.tool, repo, REPORT_PROMPT.format(slug=slug), timeout_s=args.timeout, env=env,
                        transcript=transcripts[1])
    except Exception as exc:  # noqa: BLE001
        results.append(_check("report_equals_verdict_line", False, f"second run: {type(exc).__name__}: {exc}"))
        return
    results.extend(report_checks(document, out2))


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
    parser.add_argument("--no-harness", action="store_true", help="with --fixture: no harness/ in the repository, and a solver.py "
                        "evolvekit does not know (its --version says tour-solver 0.9)")
    parser.add_argument("--shell", choices=SHELLS, default="bash", help="with --fixture: the printed lines' syntax")
    parser.add_argument("--force", action="store_true", help="with --fixture: build DIR/repo and DIR/home again")
    args = parser.parse_args(argv)

    if args.fixture is not None:
        return _fixture(args.fixture, harness=not args.no_harness, shell=args.shell, force=args.force)

    try:
        tool_command(args.tool, args.sentence, Path("."))
    except ToolMissing as exc:
        print(f"FAIL tool: {exc}")
        return 2

    results: list[dict] = []
    transcripts: list[Path] = []
    work = Path(tempfile.mkdtemp(prefix="evk gate "))
    _gate(args, work, results, transcripts)
    for result in results:
        print(f"PASS {result['name']}" if result["ok"] else f"FAIL {result['name']}: {result['detail']}")
    for path in transcripts:
        print(f"transcript: {path}")
    if args.keep:
        print(f"kept: {work}")
    else:
        try:
            _clean_up(work, work / "home", gate_env(work / "home"))
        except Exception as exc:  # noqa: BLE001
            failed = _check("clean_up", False, f"{type(exc).__name__}: {exc}")
            results.append(failed)
            print(f"FAIL clean_up: {failed['detail']}")
    return 0 if results and all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

