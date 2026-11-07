"""Finding the application (`evolvekit/app/detect.py`)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from evolvekit.app import detect
from evolvekit.harness.manifest import load_harness
from evolvekit.harness.probe import Probe

ROOT = Path(__file__).resolve().parents[1]
PYVRP = load_harness(ROOT / "harnesses" / "pyvrp")
DEMO = load_harness(ROOT / "harnesses" / "demo-tour")


def _interpreter(env: Path) -> Path:
    path = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    path.parent.mkdir(parents=True)
    path.write_text("not really")
    return path


def test_candidates_look_in_the_usual_places_once_each(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, "_py_launcher", lambda: [])
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    work, user = tmp_path / "work", tmp_path / "user"
    here = _interpreter(work / ".venv-solver")
    named = _interpreter(user / "venvs" / "routing")
    conda = _interpreter(user / "miniconda3" / "envs" / "vrp")
    listed = detect.candidates(cwd=work, recent=[str(here), str(tmp_path / "gone" / "python.exe")], home=user)
    assert listed[0] == sys.executable
    assert listed.index(str(here)) < listed.index(str(named)) < listed.index(str(conda))
    assert listed.count(str(here)) == 1, "a path used before and found again is listed once"
    assert str(tmp_path / "gone" / "python.exe") not in listed, "what is not there is not offered"


def test_the_py_launchers_list_is_read():
    text = " -V:3.13 *        C:\\Python313\\python.exe\n -V:3.12          C:\\Users\\me\\py312\\python.exe\n"
    assert detect.parse_py_launcher(text) == [Path("C:\\Python313\\python.exe"), Path("C:\\Users\\me\\py312\\python.exe")]


def test_matches_come_first_and_every_answer_is_a_sentence(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, "candidates", lambda **_: ["a", "b", "c"])
    answers = {"a": Probe(False, "Python 3.13, no pyvrp", python="3.13.1"),
               "b": Probe(True, "PyVRP 0.14.0 found", version="0.14.0", python="3.12.10"),
               "c": Probe(False, "c is not a Python interpreter")}
    monkeypatch.setattr(detect, "probe_application", lambda harness, path, timeout: answers[path])
    found = detect.detect(PYVRP)
    assert [f["path"] for f in found] == ["b", "a", "c"]
    assert found[0] == {"path": "b", "ok": True, "message": "PyVRP 0.14.0 found", "version": "0.14.0", "python": "3.12.10"}


def test_a_program_harness_offers_what_was_used_before(tmp_path):
    solver = ROOT / "examples" / "cli-solver" / "solver.py"
    found = detect.detect(DEMO, recent=[str(solver), str(tmp_path / "gone.exe")])
    assert found == [{"path": str(solver), "ok": True, "message": "The example solver (solver.py) 1.0 found",
                      "version": "1.0", "python": None}]


def test_the_interpreter_running_evolvekit_is_asked_for_real():
    found = detect.detect(PYVRP, recent=[], home=Path("/nonexistent-home"), cwd=Path("/nonexistent-cwd"))
    mine = next(f for f in found if f["path"] == sys.executable)
    try:
        import pyvrp  # noqa: F401
    except ImportError:
        assert mine["ok"] is False and mine["message"].endswith("no pyvrp")
    else:
        pytest.skip("this interpreter has PyVRP")
