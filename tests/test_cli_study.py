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


SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"
SAMPLES = sorted((ROOT / "harnesses" / "demo-tour" / "samples").glob("*.json"))


def _study_yaml(home, slug="depot-a"):
    import yaml

    return yaml.safe_load((home / "studies" / slug / "study.yaml").read_text(encoding="utf-8"))


def _app(home, slug="depot-a"):
    return main(["study", "application", slug, str(SOLVER), "--home", str(home)])


def test_study_application_records_path_and_version(tmp_path, capsys):
    _new(tmp_path)
    capsys.readouterr()
    assert _app(tmp_path) == 0
    application = _study_yaml(tmp_path)["application"]
    assert application["path"] == str(SOLVER) and application["version"]
    assert application["version"] in capsys.readouterr().out


def test_study_application_detect_lists_candidates(tmp_path, capsys):
    _new(tmp_path)
    capsys.readouterr()
    assert main(["study", "application", "depot-a", "--detect", "--json", "--home", str(tmp_path)]) == 0
    found = json.loads(capsys.readouterr().out)
    assert isinstance(found, list)
    for item in found:
        assert {"path", "ok", "message", "version", "python"} <= set(item)


def test_study_new_app_sets_the_application(tmp_path):
    assert _new(tmp_path, "Depot A", "--app", str(SOLVER)) == 0
    assert _study_yaml(tmp_path)["application"]["path"] == str(SOLVER)


def test_study_application_bad_path_is_exit_1(tmp_path, capsys):
    _new(tmp_path)
    capsys.readouterr()
    other = tmp_path / "not-the-program.txt"
    other.write_text("nothing", encoding="utf-8")
    assert main(["study", "application", "depot-a", str(other), "--home", str(tmp_path)]) == 1
    assert capsys.readouterr().err.strip()
    assert not _study_yaml(tmp_path)["application"]["path"]


def test_study_list_shows_slug_name_harness_state(tmp_path, capsys):
    _new(tmp_path, "Depot A")
    _new(tmp_path, "Depot B")
    capsys.readouterr()
    assert main(["study", "list", "--home", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    for line, (slug, name) in zip(lines, (("depot-a", "Depot A"), ("depot-b", "Depot B"))):
        assert slug in line and name in line and "Demo tour" in line and "draft · step" in line


def test_study_list_json(tmp_path, capsys):
    _new(tmp_path)
    capsys.readouterr()
    assert main(["study", "list", "--json", "--home", str(tmp_path)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [set(item) for item in listed] == [{"slug", "name", "harness", "state"}]
    assert listed[0]["slug"] == "depot-a" and listed[0]["harness"] == "Demo tour"


def test_study_check_names_problems_and_exits_2(tmp_path, capsys):
    _new(tmp_path)
    capsys.readouterr()
    assert main(["study", "check", "depot-a", "--home", str(tmp_path)]) == 2
    assert capsys.readouterr().err.splitlines()[0] == "application: say where the application is"


def test_study_check_after_application_names_cases(tmp_path, capsys):
    _new(tmp_path)
    _app(tmp_path)
    capsys.readouterr()
    assert main(["study", "check", "depot-a", "--home", str(tmp_path)]) == 2
    assert capsys.readouterr().err.splitlines()[0] == "cases.training: the search needs at least one case to learn from"


def test_study_check_ready_exits_0(tmp_path, capsys):
    _new(tmp_path, "Depot A", "--template", "tune", "--app", str(SOLVER))
    assert main(["study", "cases", "depot-a", "--add", *map(str, SAMPLES), "--hold-back", "1", "--home", str(tmp_path)]) == 0
    capsys.readouterr()
    code = main(["study", "check", "depot-a", "--home", str(tmp_path)])
    assert code == 0, capsys.readouterr().err


def test_study_cases_adds_and_splits(tmp_path, capsys):
    _new(tmp_path)
    capsys.readouterr()
    assert main(["study", "cases", "depot-a", "--add", *map(str, SAMPLES), "--hold-back", "1", "--home", str(tmp_path)]) == 0
    assert "4 cases added, 1 held back" in capsys.readouterr().out
    assert len(list((tmp_path / "studies" / "depot-a" / "cases").iterdir())) == 4
    assert len(_study_yaml(tmp_path)["cases"]["test"]) == 1


def test_study_cases_refuses_wrong_format(tmp_path, capsys):
    _new(tmp_path)
    bad = tmp_path / "table.csv"
    bad.write_text("a,b\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["study", "cases", "depot-a", "--add", str(bad), *map(str, SAMPLES[:2]), "--home", str(tmp_path)]) == 1
    assert "this harness reads .json files" in capsys.readouterr().err
    assert len(list((tmp_path / "studies" / "depot-a" / "cases").iterdir())) == 2


def test_study_cases_default_hold_back_is_the_suggestion(tmp_path):
    from evolvekit.app.store import Home

    _new(tmp_path)
    assert main(["study", "cases", "depot-a", "--add", *map(str, SAMPLES), "--home", str(tmp_path)]) == 0
    home = Home(tmp_path)
    assert len(_study_yaml(tmp_path)["cases"]["test"]) == home.suggested_test_count("depot-a")
