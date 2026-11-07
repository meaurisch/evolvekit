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
