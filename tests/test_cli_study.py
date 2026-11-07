from __future__ import annotations

import json
from pathlib import Path

from evolvekit.cli import main

ROOT = Path(__file__).resolve().parents[1]


def _new(home, name="Depot A", *extra):
    return main(["study", "new", name, "--harness", "demo-tour", "--home", str(home), *extra])


def test_study_new_lands_in_the_home(tmp_path, capsys):
    assert _new(tmp_path) == 0
    assert (tmp_path / "studies" / "depot-a" / "study.yaml").exists()
    assert capsys.readouterr().out.strip() == "depot-a"


def test_study_new_json(tmp_path, capsys):
    _new(tmp_path, "Depot A", "--json")
    doc = json.loads(capsys.readouterr().out)
    assert doc["slug"] == "depot-a" and Path(doc["root"]).is_dir()


def test_study_new_dir_writes_a_standalone_folder(tmp_path):
    target = tmp_path / "elsewhere" / "s"
    assert main(["study", "new", "S", "--harness", "demo-tour", "--dir", str(target), "--home", str(tmp_path)]) == 0
    assert (target / "study.yaml").exists()
    assert not (tmp_path / "studies").exists()


def test_study_new_harness_folder(tmp_path):
    folder = ROOT / "harnesses" / "demo-tour"
    assert main(["study", "new", "Depot A", "--harness", str(folder), "--home", str(tmp_path)]) == 0
    assert (tmp_path / "studies" / "depot-a" / "study.yaml").exists()


def test_second_study_same_name_gets_dash_two(tmp_path):
    assert _new(tmp_path) == 0 and _new(tmp_path) == 0
    assert (tmp_path / "studies" / "depot-a" / "study.yaml").exists()
    assert (tmp_path / "studies" / "depot-a-2" / "study.yaml").exists()


def test_study_root_prefers_directory(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    _new(home)
    work = tmp_path / "work"
    (work / "depot-a").mkdir(parents=True)
    monkeypatch.chdir(work)
    capsys.readouterr()
    main(["study", "preview", "depot-a", "--home", str(home)])
    assert "using the folder" in capsys.readouterr().err


def test_preview_accepts_a_slug(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _new(home)
    monkeypatch.chdir(tmp_path)
    by_slug = main(["study", "preview", "depot-a", "--home", str(home)])
    by_path = main(["study", "preview", str(home / "studies" / "depot-a"), "--home", str(home)])
    assert by_slug == by_path


def test_missing_study_is_exit_1_with_a_sentence(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["study", "preview", "nope", "--home", str(tmp_path / "home")]) == 1
    assert "there is no study" in capsys.readouterr().err


def test_absolute_path_does_not_print_using_the_folder(tmp_path, capsys):
    home = tmp_path / "home"
    _new(home)
    capsys.readouterr()
    main(["study", "preview", str(home / "studies" / "depot-a"), "--home", str(home)])
    assert "using the folder" not in capsys.readouterr().err


def test_a_folder_path_creates_no_home(tmp_path, monkeypatch):
    study = tmp_path / "elsewhere"
    main(["study", "new", "S", "--harness", "demo-tour", "--dir", str(study)])
    ghost = tmp_path / "ghost"
    monkeypatch.setenv("EVOLVEKIT_HOME", str(ghost))
    main(["study", "preview", str(study)])
    assert not ghost.exists()


def test_a_broken_harness_is_exit_2(tmp_path, capsys):
    bad = tmp_path / "bad"
    bad.mkdir()
    assert main(["study", "new", "S", "--harness", str(bad), "--dir", str(tmp_path / "s")]) == 2
    assert capsys.readouterr().err
