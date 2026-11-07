import json
import shutil
import sys
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
TOOLS = ["Bash", "PowerShell", "Read", "Edit", "Write", "Glob", "Grep"]


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
    settings = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["permissions"]["allow"] == TOOLS
    assert settings["permissions"]["additionalDirectories"] == [str(home)]
    bare = tmp_path / "bare"
    build_fixture(bare, home=tmp_path / "home2", harness=False)
    assert not (bare / "harness").exists() and (bare / "solver.py").is_file()


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


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_fixture_prints_the_three_lines(tmp_path, capsys):
    where = tmp_path / "persona round"
    assert agent_gate.main(["--fixture", str(where), "--no-harness"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    scripts = Path(sys.executable).parent
    assert lines == [f'EVOLVEKIT_HOME="{where / "home"}"', f'PATH="{scripts}{agent_gate.os.pathsep}…"',
                     f'cd "{where / "repo"}"']
    assert (where / "repo" / "solver.py").is_file() and not (where / "repo" / "harness").exists()
    assert (where / "repo" / ".claude" / "settings.json").is_file()
