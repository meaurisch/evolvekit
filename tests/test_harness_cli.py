"""`evolvekit harness ...` and `evolvekit study ...` on the command line."""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from evolvekit.cli import main
from evolvekit.harness.library import install_harness, pack_harness
from evolvekit.harness.manifest import load_harness
from evolvekit.harness.sdk import SDK_PATH
from evolvekit.harness.study import load_study, save_study

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "harnesses" / "demo-tour"
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"


def test_list_shows_the_built_in_harnesses(capsys, tmp_path):
    assert main(["harness", "list", "--home", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "demo-tour" in out and "1.0.0" in out and "built-in" in out


@pytest.mark.parametrize("kind", ["python", "program"])
def test_a_new_skeleton_loads_and_carries_the_sdk_and_agents_md(tmp_path, kind, capsys):
    target = tmp_path / "mine"
    assert main(["harness", "new", str(target), "--kind", kind]) == 0
    assert "harness check" in capsys.readouterr().out
    harness = load_harness(target)
    assert harness.application.kind == kind and set(harness.templates) == {"tune"}
    assert (target / "evk_harness.py").read_bytes() == SDK_PATH.read_bytes()
    assert "harness check . --app" in (target / "AGENTS.md").read_text(encoding="utf-8")
    assert len(list((target / "samples").glob("*.json"))) == 2


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["python", "program"])
def test_a_new_skeleton_passes_its_own_check(tmp_path, kind, capsys):
    target = tmp_path / "mine"
    main(["harness", "new", str(target), "--kind", kind])
    capsys.readouterr()
    app = sys.executable if kind == "python" else str(target / "kit" / "toy_program.py")
    code = main(["harness", "check", str(target), "--app", app, "--json"])
    document = json.loads(capsys.readouterr().out)
    assert code == 0, [c for c in document["checks"] if not c["ok"]]
    assert document["ok"] is True


def test_new_copies_the_closest_harness_by_id(tmp_path):
    target = tmp_path / "tour2"
    assert main(["harness", "new", str(target), "--from", "demo-tour"]) == 0
    assert load_harness(target).id == "demo-tour"
    assert (target / "samples" / "town-40.json").is_file() and (target / "AGENTS.md").is_file()


def test_a_new_harness_needs_an_empty_folder(tmp_path, capsys):
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "x.txt").write_text("x")
    assert main(["harness", "new", str(tmp_path / "busy")]) == 1
    assert "must be new or empty" in capsys.readouterr().err


def test_pack_and_install(tmp_path):
    source = tmp_path / "demo"
    shutil.copytree(DEMO, source)
    (source / "__pycache__").mkdir(exist_ok=True)
    (source / "__pycache__" / "runner.cpython-313.pyc").write_bytes(b"cache")
    archive = pack_harness(source, tmp_path)
    assert archive.name == "demo-tour-1.0.0.zip"
    names = zipfile.ZipFile(archive).namelist()
    assert "demo-tour-1.0.0/harness.yaml" in names and "demo-tour-1.0.0/samples/town-40.json" in names
    assert not any("__pycache__" in name for name in names)
    home = tmp_path / "home"
    installed = install_harness(archive, home)
    assert installed.root == (home / "harnesses" / "demo-tour-1.0.0").resolve()


def test_a_zip_that_would_write_outside_its_folder_is_refused(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as bundle:
        bundle.writestr("harness.yaml", "harness: 1")
        bundle.writestr("../outside.txt", "gotcha")
    with pytest.raises(ValueError, match="would land outside the harness folder"):
        install_harness(evil, tmp_path / "home")
    assert not (tmp_path / "home" / "outside.txt").exists() and not (tmp_path / "outside.txt").exists()


def test_a_study_from_the_command_line(tmp_path, capsys):
    folder = tmp_path / "study"
    assert main(["study", "new", str(folder), "--harness", "demo-tour", "--template", "tune", "--name", "Tour settings"]) == 0
    capsys.readouterr()
    for sample in ("town-40.json", "county-50.json", "region-60.json"):
        shutil.copy(DEMO / "samples" / sample, folder / "cases" / sample)
    study = load_study(folder)
    assert study.name == "Tour settings"
    study.application_path = str(SOLVER)
    study.training = ["cases/town-40.json", "cases/county-50.json"]
    study.test = ["cases/region-60.json"]
    study.limits.time_per_case_s = 0.5
    study.budget.hours = 0.02
    save_study(study, folder)
    assert main(["study", "preview", str(folder)]) == 0
    assert "preview on cases/town-40.json" in capsys.readouterr().out
    assert main(["study", "compile", str(folder), "--run-id", "first"]) == 0
    out = capsys.readouterr().out
    assert "compiled into" in out and "rounds" in out
    config = yaml.safe_load((folder / "runs" / "first" / "evolvekit.yaml").read_text(encoding="utf-8"))
    assert config["evaluate"]["stages"][-1]["instances"] == ["../../cases/town-40.json", "../../cases/county-50.json"]


def test_a_study_that_is_not_ready_says_what_is_missing(tmp_path, capsys):
    folder = tmp_path / "study"
    main(["study", "new", str(folder), "--harness", str(DEMO)])
    capsys.readouterr()
    assert main(["study", "compile", str(folder)]) == 1
    assert "error: application: say where the application is" in capsys.readouterr().err
