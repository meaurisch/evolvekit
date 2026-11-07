import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import agent_gate
from agent_gate import (DEFAULT_SENTENCE, MISSING_TIME_SENTENCE, build_fixture, checks, claims, question_checks,
                        redact, report_checks, tool_command)
from evolvekit.app.store import Home
from evolvekit.harness.manifest import load_harness
from evolvekit.harness.study import load_study, save_study

ROOT = Path(__file__).resolve().parents[1]
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"
SAMPLES = sorted(p.name for p in (ROOT / "harnesses" / "demo-tour" / "samples").glob("*.json"))
URL = "http://127.0.0.1:8780/?token=abc123#/study/gate"
LINE = ("Probably better: 1.3 % lower tour length on 2 held-back cases, a chance of about 19 in 20; "
        "at worst 0.3 % worse than your starting point.")
ADVICE = "Use the new settings: they are probably better on cases the search never saw."
TOOLS = ["Bash", "PowerShell", "Read", "Edit", "Write", "Glob", "Grep", "Skill"]
FAKE_TOOL = """\
import subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
Path(sys.argv[1]).write_text(str(child.pid), encoding="utf-8")
print("partial output", flush=True)
time.sleep(120)
"""


def _ok(results):
    return {r["name"]: r["ok"] for r in results}


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_build_fixture_layout(tmp_path):
    repo, home = tmp_path / "a repo", tmp_path / "home"
    build_fixture(repo, home=home)
    assert (repo / ".git").is_dir()
    for name in ("evolvekit-study", "evolvekit-harness"):
        assert (repo / ".agents" / "skills" / name / "SKILL.md").is_file()
        assert (repo / ".agents" / "skills" / name / "references" / "evolvekit.md").is_file()
        assert (repo / ".claude" / "skills" / name / "SKILL.md").is_file()
    assert load_harness(repo / "harness").id == "demo-tour"
    assert not list((repo / "harness").rglob("__pycache__"))
    assert (repo / "solver.py").read_bytes() == SOLVER.read_bytes()
    assert sorted(p.name for p in (repo / "cases").iterdir()) == SAMPLES
    assert len((repo / "README.md").read_text(encoding="utf-8").splitlines()) == 2
    assert home.is_dir() and list(home.iterdir()) == []
    assert not (repo / ".claude" / "settings.json").exists(), "an untrusted workspace ignores it: the flags carry the run"
    bare = tmp_path / "bare"
    build_fixture(bare, home=tmp_path / "home2", harness=False)
    assert not (bare / "harness").exists() and (bare / "solver.py").is_file()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_the_no_harness_fixture_is_a_program_evolvekit_does_not_know(tmp_path):
    bare = tmp_path / "bare"
    build_fixture(bare, home=tmp_path / "home", harness=False)
    solver = bare / "solver.py"
    version = subprocess.run([sys.executable, str(solver), "--version"], capture_output=True, text=True, check=True)
    assert version.stdout.strip() == "tour-solver 0.9"
    check = subprocess.run([sys.executable, "-m", "evolvekit", "harness", "check", str(ROOT / "harnesses" / "demo-tour"),
                            "--app", str(solver), "--json"], capture_output=True, text=True, cwd=ROOT)
    assert check.returncode == 2
    probe = next(c for c in json.loads(check.stdout)["checks"] if c["id"] == "probe")
    assert probe["ok"] is False and "tour-solver 0.9" in probe["message"]
    text = solver.read_text(encoding="utf-8")
    assert [word for word in ("demo-tour", "harnesses/", "evolvekit") if word in text.lower()] == []
    code = SOLVER.read_text(encoding="utf-8").split('"""', 2)[2]  # the example's code after its docstring
    assert text.split('"""', 2)[2] == code.replace('version="cli-solver 1.0"', 'version="tour-solver 0.9"')
    names = sorted(p.name for p in (bare / "cases").iterdir())
    assert names == [f"requests-2026-03-0{day}.json" for day in (2, 3, 4, 5)]
    for name, sample in zip(names, SAMPLES):
        case = (bare / "cases" / name).read_bytes()
        original = (ROOT / "harnesses" / "demo-tour" / "samples" / sample).read_bytes()
        old, new = f'"name": "{Path(sample).stem}"'.encode(), f'"name": "{Path(name).stem}"'.encode()
        assert case == original.replace(old, new), "only the name changes"
        assert json.loads(case)["name"] == Path(name).stem
        assert [s for s in (b"north-south", b"town-40", b"region-60", b"county-50") if s in case] == []
        solved = subprocess.run([sys.executable, str(solver), "--instance", str(bare / "cases" / name),
                                 "--time-limit", "0.2"], capture_output=True, text=True, timeout=60)
        assert solved.returncode == 0 and solved.stdout.strip(), (name, solved.stderr)


def test_sentences():
    assert "ten minutes in total" in DEFAULT_SENTENCE
    assert MISSING_TIME_SENTENCE == DEFAULT_SENTENCE.replace("ten minutes in total; ", "")


def test_checks_on_a_fake_output(tmp_path):
    home = tmp_path / "home"
    assert _ok(checks(home, "")) == {"study_exists": False, "application_set": False,
                                     "study_started_or_queued": False, "output_names_the_url": False,
                                     "no_claim_of_improvement": True}
    store = Home(home)
    slug = store.create_study("demo-tour", "tune", "Gate")
    root = store.study_root(slug)
    said = f"Your study is running: {URL}. It tries about 12 combinations in 4 rounds and ends around 14:20."
    assert _ok(checks(home, said)) == {"study_exists": True, "application_set": False,
                                       "study_started_or_queued": False, "output_names_the_url": True,
                                       "no_claim_of_improvement": True}
    study = load_study(root)
    study.application_path = str(SOLVER)
    save_study(study, root)
    run = root / "runs" / "20261107-120000"
    run.mkdir(parents=True)
    (run / "queued.json").write_text('{"queued_at": "2026-11-07T12:00:00+00:00"}', encoding="utf-8")
    assert all(_ok(checks(home, said)).values())
    for claim in ("It found better settings.", "Tours are 3.1 % shorter.", "It improved.", "faster", "cheaper"):
        assert _ok(checks(home, f"{said} {claim}"))["no_claim_of_improvement"] is False, claim
    assert _ok(checks(home, "http://127.0.0.1:8780/"))["output_names_the_url"] is False
    (run / "queued.json").unlink()
    (run / "job.json").write_text('{"phase": "done"}', encoding="utf-8")
    assert _ok(checks(home, said))["study_started_or_queued"] is False  # a one-shot run that waited is a fault


def test_question_checks_truth_table(tmp_path):
    home = tmp_path / "home"
    asked = "I have everything but one answer. How much time may the whole study take, in minutes?"
    assert _ok(question_checks(home, asked)) == {"no_study_made": True, "asks_the_missing_question": True}
    assert _ok(question_checks(home, "Starting the study now."))["asks_the_missing_question"] is False
    assert _ok(question_checks(home, "Which cases should I hold back?"))["asks_the_missing_question"] is False
    Home(home).create_study("demo-tour", "tune", "Gate")
    assert _ok(question_checks(home, asked))["no_study_made"] is False


def test_no_claim_detector_ignores_the_verdict_line():
    document = {"line": LINE, "advice": ADVICE}
    said = f"The study has finished.\n\n{LINE}\n{ADVICE}\n\nThe report is in evolvekit-report.md."
    assert claims(LINE) != []
    assert claims(said, allowed=(LINE, ADVICE)) == []
    assert _ok(report_checks(document, said)) == {"report_equals_verdict_line": True}
    extra = said + "\nOn the cases it learned from it was 4.2 % lower."
    assert _ok(report_checks(document, extra))["report_equals_verdict_line"] is False
    assert _ok(report_checks(document, "Probably better: 1.3 % lower."))["report_equals_verdict_line"] is False


def test_tool_command_is_one_shot(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_gate.shutil, "which", lambda name: f"/bin/{name}")
    home = tmp_path / "a home"
    argv, stdin = tool_command("claude", "Use evolvekit.", home)
    assert argv[0] == "/bin/claude" and "-p" in argv and stdin == "Use evolvekit."
    assert argv[argv.index("--add-dir") + 1] == str(home)
    assert argv[argv.index("--allowedTools") + 1] == ",".join(TOOLS)
    assert "--no-session-persistence" in argv
    assert not {"-c", "--continue", "-r", "--resume"} & set(argv)
    argv, stdin = tool_command("copilot", "Use evolvekit.", home)
    assert stdin is None and argv[argv.index("-p") + 1] == "Use evolvekit."
    monkeypatch.setattr(agent_gate.shutil, "which", lambda name: None)
    with pytest.raises(agent_gate.ToolMissing):
        tool_command("copilot", "x", home)
    assert redact(f"see {URL}") == "see http://127.0.0.1:8780/?token=…#/study/gate"


def test_a_missing_tool_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(agent_gate.shutil, "which", lambda name: None)
    assert agent_gate.main(["--tool", "copilot"]) == 2
    assert "FAIL tool: copilot is not installed" in capsys.readouterr().out


def test_claude_is_isolated_from_the_operators_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_gate.shutil, "which", lambda name: f"/bin/{name}")
    argv, _ = tool_command("claude", "Use evolvekit.", tmp_path)
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--permission-prompts") + 1] == "none"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_fixture_prints_bash_lines(tmp_path, capsys):
    where = tmp_path / "persona round"
    assert agent_gate.main(["--fixture", str(where), "--no-harness"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    unset = [line for line in lines if line.startswith("unset ")]
    assert "unset CLAUDECODE" in unset and lines[:len(unset)] == unset
    del lines[:len(unset)]
    home, repo = (where / "home").resolve(), (where / "repo").resolve()
    assert lines[0] == f'export EVOLVEKIT_HOME="{home.as_posix()}"'
    assert lines[1].startswith('export PATH="') and lines[1].endswith(':$PATH"') and "\\" not in lines[1]
    assert lines[1][len('export PATH="'):-len(':$PATH"')] == agent_gate._posix(Path(sys.executable).parent)
    assert lines[2] == f'cd "{repo.as_posix()}"'
    assert lines[3].startswith('claude -p "<message>" ')
    for flag in ("--setting-sources project", "--strict-mcp-config", "--permission-prompts none",
                 f'--add-dir "{home.as_posix()}"', "--allowedTools " + ",".join(TOOLS)):
        assert flag in lines[3], flag
    assert len(lines) == 4
    assert (repo / "solver.py").is_file() and not (repo / "harness").exists()
    assert not (repo / ".claude" / "settings.json").exists()


DROPPED = ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_PID", "CLAUDE_EFFORT",
           "CLAUDE_PREVIEW_PORT"]
KEPT = ["CLAUDE_CODE_GIT_BASH_PATH", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_SKIP_BEDROCK_AUTH", "CLAUDE_CODE_SKIP_VERTEX_AUTH", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
        "ANTHROPIC_API_KEY", "CLAUDE_CLI_PATH"]


def _session_env(monkeypatch):
    for name in list(agent_gate.os.environ):
        if agent_gate._from_session(name):
            monkeypatch.delenv(name)
    for name in DROPPED + KEPT:
        monkeypatch.setenv(name, "1")


def test_gate_env_drops_the_launching_sessions_identity_and_keeps_the_sign_in(tmp_path, monkeypatch):
    _session_env(monkeypatch)
    env = agent_gate.gate_env(tmp_path / "home")
    upper = {k.upper() for k in env}
    assert not set(DROPPED) & upper
    assert set(KEPT) <= upper
    assert set(agent_gate.KEEP) == set(KEPT[:6])
    assert env["EVOLVEKIT_HOME"] == str(tmp_path / "home")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_fixture_unsets_what_the_gate_drops(tmp_path, monkeypatch, capsys, shell):
    _session_env(monkeypatch)
    monkeypatch.delenv("CLAUDECODE")
    assert agent_gate.main(["--fixture", str(tmp_path / "f"), "--shell", shell]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    unset = "unset " if shell == "bash" else "Remove-Item Env:"
    names = [line[len(unset):].split()[0] for line in lines if line.startswith(unset)]
    assert sorted(names) == sorted(DROPPED), "CLAUDECODE always, and every dropped name present"
    assert lines[:len(names)] == [line for line in lines if line.startswith(unset)]


def test_posix_path():
    assert agent_gate._posix(Path("C:/Users/a b/x")) in ("/c/Users/a b/x", "C:/Users/a b/x")
    if agent_gate.os.name == "nt":
        assert agent_gate._posix(Path("C:/Users/a b/x")) == "/c/Users/a b/x"
    assert agent_gate._posix(Path("/usr/bin")) == "/usr/bin"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_fixture_prints_powershell_lines_and_refuses_a_taken_folder(tmp_path, capsys):
    where = tmp_path / "persona round"
    assert agent_gate.main(["--fixture", str(where), "--shell", "powershell"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    unset = [line for line in lines if line.startswith("Remove-Item Env:")]
    assert "Remove-Item Env:CLAUDECODE -ErrorAction SilentlyContinue" in unset and lines[:len(unset)] == unset
    del lines[:len(unset)]
    home, repo = (where / "home").resolve(), (where / "repo").resolve()
    scripts = Path(sys.executable).parent
    assert lines[:3] == [f'$env:EVOLVEKIT_HOME = "{home}"', f'$env:PATH = "{scripts}{agent_gate.os.pathsep}" + $env:PATH',
                         f'Set-Location "{repo}"']
    assert lines[3].startswith('claude -p "<message>" ') and f'--add-dir "{home}"' in lines[3]
    assert agent_gate.main(["--fixture", str(where)]) == 1
    assert "already exists" in capsys.readouterr().out
    (repo / "stale.txt").write_text("x", encoding="utf-8")
    assert agent_gate.main(["--fixture", str(where), "--force"]) == 0
    assert not (repo / "stale.txt").exists() and (repo / "harness").is_dir()


def _alive(pid):
    if agent_gate.os.name == "nt":
        found = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in found
    try:
        agent_gate.os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_a_timeout_kills_the_whole_tree(tmp_path, monkeypatch):
    script = tmp_path / "fake tool.py"
    script.write_text(FAKE_TOOL, encoding="utf-8")
    monkeypatch.setitem(agent_gate.TOOLS, "fake", [str(script), "{sentence}"])
    monkeypatch.setattr(agent_gate.shutil, "which", lambda name: sys.executable)
    pid_file = tmp_path / "child.pid"
    transcript = tmp_path / "logs" / "t.txt"
    env = {**agent_gate.os.environ, "EVOLVEKIT_HOME": str(tmp_path / "home")}
    began = time.monotonic()
    output = agent_gate.run_tool("fake", tmp_path, str(pid_file), timeout_s=5, env=env, transcript=transcript)
    assert "stopped the tool after 5 s" in output
    assert transcript.is_file()
    deadline = time.monotonic() + 10  # under load the fake tool may write its pid file late
    while not pid_file.is_file() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert pid_file.is_file(), "the fake tool never started its child"
    assert "partial output" in output
    pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _alive(pid)
    assert time.monotonic() - began < 40


def _gate_args(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_gate.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(agent_gate.tempfile, "mkdtemp", lambda prefix="": str(tmp_path / "work"))
    monkeypatch.setattr(agent_gate, "build_fixture",
                        lambda repo, *, home, harness=True: (repo.mkdir(parents=True), home.mkdir(parents=True)))
    return ["--log-dir", str(tmp_path / "logs")]


def _boom(*args, **kwargs):
    raise RuntimeError("boom")


def test_a_failing_tool_run_is_a_fail_line(tmp_path, monkeypatch, capsys):
    argv = _gate_args(tmp_path, monkeypatch)
    monkeypatch.setattr(agent_gate, "run_tool", _boom)
    assert agent_gate.main(argv) == 1
    out = capsys.readouterr().out
    assert "FAIL tool_run: RuntimeError: boom" in out and "Traceback" not in out
    assert not (tmp_path / "work").exists()


def test_a_failing_verdict_and_clean_up_are_fail_lines(tmp_path, monkeypatch, capsys):
    argv = _gate_args(tmp_path, monkeypatch)
    root = tmp_path / "study"
    monkeypatch.setattr(agent_gate, "run_tool", lambda *a, **k: "said")
    monkeypatch.setattr(agent_gate, "checks", lambda home, output: [{"name": n, "ok": True, "detail": ""} for n in (
        "study_exists", "application_set", "study_started_or_queued", "output_names_the_url",
        "no_claim_of_improvement")])
    monkeypatch.setattr(agent_gate, "the_study", lambda home: ("gate", root))
    monkeypatch.setattr(agent_gate, "_evolvekit", lambda *a, **k: 0)
    monkeypatch.setattr(agent_gate, "build_verdict", _boom)
    monkeypatch.setattr(agent_gate, "_clean_up", _boom)
    assert agent_gate.main(argv) == 1
    out = capsys.readouterr().out
    assert "FAIL report_equals_verdict_line: no verdict: RuntimeError: boom" in out
    assert "FAIL clean_up: RuntimeError: boom" in out and "PASS study_exists" in out


def test_a_study_wait_that_hangs_is_a_fail_line(tmp_path, monkeypatch, capsys):
    argv = _gate_args(tmp_path, monkeypatch)
    monkeypatch.setattr(agent_gate, "run_tool", lambda *a, **k: "said")
    monkeypatch.setattr(agent_gate, "checks", lambda home, output: [{"name": n, "ok": True, "detail": ""} for n in (
        "study_exists", "application_set", "study_started_or_queued")])
    monkeypatch.setattr(agent_gate, "the_study", lambda home: ("gate", tmp_path / "study"))
    monkeypatch.setattr(agent_gate, "_clean_up", lambda *a, **k: None)
    given = {}

    def hangs(*args, env, timeout=None):
        given["timeout"] = timeout
        raise subprocess.TimeoutExpired(args, timeout)

    monkeypatch.setattr(agent_gate, "_evolvekit", hangs)
    assert agent_gate.main([*argv, "--wait-timeout", "30"]) == 1
    assert given["timeout"] == 150
    out = capsys.readouterr().out
    assert "FAIL study_finished: study wait: TimeoutExpired" in out and "Traceback" not in out
