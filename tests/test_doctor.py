from __future__ import annotations

import json
import os
from pathlib import Path

from evolvekit.app.running import write_running
from evolvekit.app.store import Home
from evolvekit.cli import main

ROOT = Path(__file__).resolve().parents[1]
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"


def _doctor(capsys, home, *extra):
    code = main(["doctor", "--home", str(home), *extra])
    return code, capsys.readouterr().out


def test_doctor_json_is_a_list_in_order(tmp_path, capsys):
    _, out = _doctor(capsys, tmp_path, "--json")
    doc = json.loads(out)
    assert [c["name"] for c in doc][:6] == ["python", "evolvekit", "home", "app", "provider", "harnesses"]
    assert all(set(c) == {"ok", "name", "message", "fix"} for c in doc)


def test_doctor_reports_app_running(tmp_path, capsys):
    write_running(tmp_path, pid=os.getpid(), port=8780, url="http://127.0.0.1:8780/?token=abc123")
    _, out = _doctor(capsys, tmp_path, "--json")
    app = next(c for c in json.loads(out) if c["name"] == "app")
    assert app["ok"] and "http://127.0.0.1:8780" in app["message"]
    assert "abc123" not in out


def test_doctor_never_prints_a_key(tmp_path, capsys):
    Home(tmp_path).set_keys({"OPENROUTER_API_KEY": "sk-secret"})
    code, out = _doctor(capsys, tmp_path)
    assert "sk-secret" not in out
    assert "key set" in out


def test_doctor_exit_1_when_home_not_writable(tmp_path, monkeypatch, capsys):
    real = os.access
    monkeypatch.setattr(os, "access", lambda p, mode, *a, **k: False if Path(p) == tmp_path.resolve() else real(p, mode, *a, **k))
    code, out = _doctor(capsys, tmp_path, "--json")
    home = next(c for c in json.loads(out) if c["name"] == "home")
    assert code == 1 and not home["ok"] and str(tmp_path.resolve()) in home["fix"]


def test_doctor_with_app_probes_each_harness(tmp_path, capsys):
    _, out = _doctor(capsys, tmp_path, "--app", str(SOLVER), "--json")
    probes = {c["name"]: c for c in json.loads(out) if c["name"].startswith("probe:")}
    assert "probe:demo-tour" in probes and probes["probe:demo-tour"]["ok"]


def test_doctor_text_has_marks(tmp_path, capsys):
    _, out = _doctor(capsys, tmp_path)
    lines = [ln for ln in out.splitlines() if ln and not ln.startswith(" ")]
    assert lines and all(ln.startswith(("✓ ", "✗ ")) for ln in lines)


def test_doctor_lists_git_without_failing_when_missing(tmp_path, capsys, monkeypatch):
    _, out = _doctor(capsys, tmp_path, "--json")
    line = next(c for c in json.loads(out) if c["name"] == "git")
    assert line["ok"] is True and line["message"].startswith("git ")
    monkeypatch.setattr("evolvekit.tree.git_version", lambda: None)
    code, out = _doctor(capsys, tmp_path, "--json")
    line = next(c for c in json.loads(out) if c["name"] == "git")
    assert line == {"ok": True, "name": "git", "fix": "",
                    "message": "not found: only code studies need it (git-scm.com)"}


def test_doctor_says_when_git_is_too_old_for_code_studies(tmp_path, capsys, monkeypatch):
    for version, old in (("2.25.1", True), ("2.29.9.windows.1", True), ("1.9.5", True),
                         ("2.30.0", False), ("2.50.1.windows.1", False), ("2.39.3 (Apple Git-146)", False),
                         ("3.0", False), ("unknown", False)):
        monkeypatch.setattr("evolvekit.tree.git_version", lambda v=version: v)
        code, out = _doctor(capsys, tmp_path, "--json")
        line = next(c for c in json.loads(out) if c["name"] == "git")
        assert line["ok"] is True, "settings studies need no git: the line never fails"
        if old:
            assert line["message"] == f"git {version} is older than 2.30, which code studies need (git-scm.com)"
        else:
            assert line["message"] == f"git {version}"


def _second_harness(home):
    import shutil

    target = home / "harnesses" / "pyvrp"
    shutil.copytree(ROOT / "harnesses" / "pyvrp", target, ignore=shutil.ignore_patterns("__pycache__", "samples"))


def test_doctor_a_harness_that_refuses_is_not_a_fault_when_another_accepts(tmp_path, capsys):
    _second_harness(tmp_path)
    code, out = _doctor(capsys, tmp_path, "--app", str(SOLVER), "--json")
    probes = {c["name"]: c for c in json.loads(out) if c["name"].startswith("probe:")}
    assert probes["probe:demo-tour"]["ok"]
    assert probes["probe:pyvrp"]["ok"] and probes["probe:pyvrp"]["message"].startswith("not for this application: ")
    assert all(c["ok"] for c in probes.values())
    assert code == (0 if all(c["ok"] for c in json.loads(out)) else 1)


def test_doctor_no_harness_accepts_the_application(tmp_path, capsys):
    _second_harness(tmp_path)
    stranger = tmp_path / "stranger.txt"
    stranger.write_text("x", encoding="utf-8")
    code, out = _doctor(capsys, tmp_path, "--app", str(stranger), "--json")
    probes = [c for c in json.loads(out) if c["name"].startswith("probe:")]
    assert len(probes) >= 2 and code == 1 and not any(c["ok"] for c in probes)
    assert "PyVRP" in next(c for c in probes if c["name"] == "probe:pyvrp")["fix"]
    assert "solver.py" in next(c for c in probes if c["name"] == "probe:demo-tour")["fix"]


def test_doctor_never_crashes(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise ValueError("No closing quotation")

    monkeypatch.setattr("evolvekit.harness.probe.probe_application", boom)
    monkeypatch.setattr("evolvekit.harness.library.find_harnesses",
                        lambda home=None: (_ for _ in ()).throw(OSError("disk gone")))
    code, out = _doctor(capsys, tmp_path, "--app", str(SOLVER))
    assert code == 1 and "✗ harnesses: could not check: disk gone" in out and "Traceback" not in out
    monkeypatch.undo()
    monkeypatch.setattr("evolvekit.harness.probe.probe_application", boom)
    code, out = _doctor(capsys, tmp_path, "--app", str(SOLVER))
    assert code == 1 and "✗ probe:demo-tour: could not check: No closing quotation" in out


def test_doctor_writes_utf8_into_a_pipe(tmp_path):
    """A coding agent reads the CLI through a pipe: the marks arrive as UTF-8,
    not as cp1252 escapes."""
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    done = subprocess.run([sys.executable, "-m", "evolvekit", "doctor", "--home", str(tmp_path)],
                          capture_output=True, env=env, cwd=ROOT, timeout=120)
    assert b"\xe2\x9c\x93" in done.stdout, done.stdout
    assert b"\\u2713" not in done.stdout


def test_a_piped_stream_is_utf8_and_still_tolerant(monkeypatch):
    import io
    import sys

    from evolvekit.cli import _tolerant_output

    pipe = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", pipe)
    _tolerant_output()
    assert sys.stdout.encoding.lower().replace("-", "") == "utf8"
    assert sys.stdout.errors == "backslashreplace"
    sys.stdout.write("✓ \udcff")
    sys.stdout.flush()
