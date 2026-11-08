from __future__ import annotations

import json
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evolvekit.cli import main
from evolvekit.stopping import STOP_REQUEST

from campaign_helpers import campaign_study, local, write_run

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


def test_study_application_takes_a_relative_path(tmp_path, monkeypatch, capsys):
    _new(tmp_path)
    monkeypatch.chdir(ROOT)
    assert main(["study", "application", "depot-a", "examples/cli-solver/solver.py", "--home", str(tmp_path)]) == 0
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
    out, err = capsys.readouterr()
    assert code == 0, err
    assert out.splitlines()[0] == "ready: no problems"
    assert out.splitlines()[1].startswith("plan: automatic (budget "), "the plan it will run, from the template"


def test_study_check_echoes_a_manual_plan(tmp_path, capsys):
    import yaml

    _new(tmp_path, "Depot A", "--template", "tune", "--app", str(SOLVER))
    assert main(["study", "cases", "depot-a", "--add", *map(str, SAMPLES), "--hold-back", "1", "--home", str(tmp_path)]) == 0
    path = tmp_path / "studies" / "depot-a" / "study.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["plan"] = {"auto": False, "children": 2, "generations": 2}
    data["budget"]["hours"] = 0.05
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    capsys.readouterr()
    assert main(["study", "check", "depot-a", "--home", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    # One case held back: the thin final check's note follows the plan (persona round 2, D2).
    assert out.startswith("ready: no problems\nplan: 2 rounds × 2 combinations, budget 0.05 h\nnote: With 1 held-back")


def test_study_steps_advance_from_the_command_line(tmp_path, capsys):
    """Application, cases and a clean check each complete a step of the app's
    Set up: the study reads as a ready draft, not "step 1 of 7"."""
    from evolvekit.app import jobs
    from evolvekit.app.store import STEPS, Home

    _new(tmp_path, "Depot A", "--template", "tune")
    assert _app(tmp_path) == 0
    assert _study_yaml(tmp_path)["step"] == 3
    assert main(["study", "cases", "depot-a", "--add", *map(str, SAMPLES), "--hold-back", "1", "--home", str(tmp_path)]) == 0
    assert _study_yaml(tmp_path)["step"] == 4
    assert main(["study", "check", "depot-a", "--home", str(tmp_path)]) == 0
    assert _study_yaml(tmp_path)["step"] == len(STEPS)
    capsys.readouterr()
    assert main(["study", "list", "--home", str(tmp_path)]) == 0
    listed = capsys.readouterr().out
    assert "draft" in listed and "step 1" not in listed
    root, study, _ = Home(tmp_path).load("depot-a")
    assert main(["study", "status", "depot-a", "--home", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == jobs.state_line(root, study)["line"] == f"draft · step {len(STEPS)} of {len(STEPS)}"
    # a later command never moves the step back
    assert _app(tmp_path) == 0
    assert _study_yaml(tmp_path)["step"] == len(STEPS)


def test_study_new_dir_with_app_records_the_application(tmp_path):
    target = tmp_path / "elsewhere" / "s"
    assert main(["study", "new", "S", "--harness", "demo-tour", "--dir", str(target), "--app", str(SOLVER),
                 "--home", str(tmp_path)]) == 0
    import yaml

    application = yaml.safe_load((target / "study.yaml").read_text(encoding="utf-8"))["application"]
    assert application["path"] == str(SOLVER) and application["version"]


def test_compiled_study_is_not_a_failed_run(tmp_path, capsys):
    from evolvekit.app import jobs

    _new(tmp_path, "Depot A", "--template", "tune", "--app", str(SOLVER))
    assert main(["study", "cases", "depot-a", "--add", *map(str, SAMPLES), "--hold-back", "1", "--home", str(tmp_path)]) == 0
    root = tmp_path / "studies" / "depot-a"
    assert main(["study", "check", "depot-a", "--home", str(tmp_path)]) == 0
    assert not (root / "runs").exists(), "a check leaves no run folder"
    assert main(["study", "compile", "depot-a", "--home", str(tmp_path)]) == 0
    assert not (root / "runs").exists(), "a compile without --run-id leaves no run folder (persona round 4, F2)"
    assert "nothing written" in capsys.readouterr().out
    assert main(["study", "compile", "depot-a", "--run-id", "keep1", "--home", str(tmp_path)]) == 0
    assert [p.name for p in (root / "runs").iterdir()] == ["keep1"], "a named compile keeps its files"
    assert jobs.latest_run(root) is None and jobs.runs(root) == [], "a compile-only folder is not a run"
    capsys.readouterr()
    assert main(["study", "status", "depot-a", "--home", str(tmp_path)]) == 0
    assert capsys.readouterr().out.startswith("draft")
    assert main(["study", "list", "--json", "--home", str(tmp_path)]) == 0
    assert "failed" not in json.loads(capsys.readouterr().out)[0]["state"]
    assert jobs.latest_run(root) is None


def _with_ai(home, slug, **vary):
    import yaml

    path = home / "studies" / slug / "study.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["budget"]["ai"] = {"enabled": True, "max_usd": 1.0}
    data["vary"].update(vary)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@pytest.mark.parametrize("template", ["tune", "faster"])
def test_compile_takes_the_models_of_settings_on_a_study_with_ai(tmp_path, capsys, monkeypatch, template):
    """Persona round 1, F7: `study compile` failed on every study with AI search help ("no model is set
    up"), because it never read the models `study start` takes from Settings. A settings study and a
    code study compile with them; without a key for the provider it says what to do, as `study start` does."""
    import shutil
    import subprocess

    import yaml

    from evolvekit.app.store import Home

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    app = SOLVER
    if template == "faster":
        repo = tmp_path / "solver repo"
        shutil.copytree(SOLVER.parent, repo, ignore=shutil.ignore_patterns("fresh", "__pycache__"))
        for argv in (["init", "-q", "-b", "main"], ["add", "-A"],
                     ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"]):
            subprocess.run(["git", *argv], cwd=repo, check=True, capture_output=True)
        app = repo / "solver.py"
    _new(tmp_path, "Depot A", "--template", template, "--app", str(app))
    assert main(["study", "cases", "depot-a", "--add", *map(str, SAMPLES), "--hold-back", "1", "--home", str(tmp_path)]) == 0
    _with_ai(tmp_path, "depot-a", **({"code": {**_study_yaml(tmp_path)["vary"]["code"], "repo": str(app.parent)}}
                                     if template == "faster" else {}))
    capsys.readouterr()
    assert main(["study", "compile", "depot-a", "--home", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "AI search help is on, and there is no key for it: add one in Settings, or turn AI help off" in err
    Home(tmp_path).save_settings({"provider": "claude-cli", "models": {"search": ""}})
    assert main(["study", "compile", "depot-a", "--run-id", "c1", "--home", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    # Persona round 2, D1: the plan counts the AI's time, and says what it assumes for each change:
    # the coding agent beside the diff prompt in a code study with claude-cli, a reply in a settings study.
    seconds = 20 if template == "faster" else 10
    assert f"tried (assumes about {seconds} s for each AI change), " in out
    config = yaml.safe_load((tmp_path / "studies" / "depot-a" / "runs" / "c1" / "evolvekit.yaml").read_text(encoding="utf-8"))
    assert config["models"]["strong"]["provider"] == "claude-cli"


def test_compile_on_a_home_study_points_to_study_start(tmp_path, capsys):
    _new(tmp_path, "Depot A", "--template", "tune", "--app", str(SOLVER))
    assert main(["study", "cases", "depot-a", "--add", *map(str, SAMPLES), "--hold-back", "1", "--home", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["study", "compile", "depot-a", "--home", str(tmp_path)]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("compiled, nothing written")
    assert f'next: evolvekit study start depot-a --home "{tmp_path}"' in err.splitlines()
    assert "--config" not in out + err


def test_study_cases_expands_a_pattern(tmp_path, capsys):
    _new(tmp_path)
    capsys.readouterr()
    pattern = str(SAMPLES[0].parent / "*.json")
    assert main(["study", "cases", "depot-a", "--add", pattern, "--hold-back", "1", "--home", str(tmp_path)]) == 0
    assert "4 cases added, 1 held back" in capsys.readouterr().out
    nothing = str(tmp_path / "*.csv")
    assert main(["study", "cases", "depot-a", "--add", nothing, "--home", str(tmp_path)]) == 1
    assert f"no file matches {nothing}" in capsys.readouterr().err


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


def test_study_cases_on_an_empty_study_with_nothing_readable(tmp_path, capsys):
    _new(tmp_path)
    capsys.readouterr()
    missing = tmp_path / "missing.json"
    assert main(["study", "cases", "depot-a", "--add", str(missing), "--hold-back", "1", "--home", str(tmp_path)]) == 1
    out, err = capsys.readouterr()
    assert out.strip() == "nothing added"
    assert "missing.json: cannot be read" in err and "Traceback" not in err


def test_study_cases_hold_back_more_than_there_are_is_a_sentence(tmp_path, capsys):
    _new(tmp_path)
    capsys.readouterr()
    assert main(["study", "cases", "depot-a", "--add", *map(str, SAMPLES[:2]), "--hold-back", "5",
                 "--home", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "at most 1" in err and "Traceback" not in err


def test_study_cases_default_hold_back_is_the_suggestion(tmp_path):
    from evolvekit.app.store import Home

    _new(tmp_path)
    assert main(["study", "cases", "depot-a", "--add", *map(str, SAMPLES), "--home", str(tmp_path)]) == 0
    home = Home(tmp_path)
    assert len(_study_yaml(tmp_path)["cases"]["test"]) == home.suggested_test_count("depot-a")


# -- start, stop, status, watch and wait: the app's runner, from the terminal


def _ready(home, name="Depot A", slug="depot-a"):
    """A study that can start: the demo solver, the samples with one held
    back, and the small plan of the app's tests (2 combinations, 2 rounds)."""
    from evolvekit.app.store import Home

    assert _new(home, name, "--template", "tune", "--app", str(SOLVER)) == 0
    assert main(["study", "cases", slug, "--add", *map(str, SAMPLES), "--hold-back", "1", "--home", str(home)]) == 0
    doc = _study_yaml(home, slug)
    Home(home).save_step(slug, {
        "limits": {**(doc.get("limits") or {}), "time_per_case_s": 0.5, "retries": 1, "runs_per_case": 1},
        "budget": {**(doc.get("budget") or {}), "hours": 0.05, "ai": {"enabled": False, "max_usd": 2.0}},
        "plan": {**(doc.get("plan") or {}), "auto": False, "children": 2, "generations": 2},
    })
    return slug


def _fake_active(home, slug, run="20260101-000000"):
    """A run of `slug` that reads as starting: launched as this very process,
    which is alive, and no job written yet -- nothing is really launched."""
    run_dir = home / "studies" / slug / "runs" / run
    run_dir.mkdir(parents=True)
    (run_dir / "launch.json").write_text(json.dumps({
        "pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": socket.gethostname()}), encoding="utf-8")
    return run_dir


def _queued(home, slug, run="20260101-000001"):
    run_dir = home / "studies" / slug / "runs" / run
    run_dir.mkdir(parents=True)
    (run_dir / "queued.json").write_text(json.dumps({"queued_at": "2026-01-01T00:00:01+00:00"}), encoding="utf-8")
    return run_dir


@pytest.fixture
def started_study(tmp_path):
    _new(tmp_path)
    _fake_active(tmp_path, "depot-a")
    return tmp_path


@pytest.fixture
def app(tmp_path):
    from evolvekit.app.server import AppServer

    server = AppServer(tmp_path / "home", port=0)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def finished_study(app):
    home = app.home.root
    slug = _ready(home)
    run_dir = home / "studies" / slug / "runs" / "20260101-000000"
    run_dir.mkdir(parents=True)
    (run_dir / "job.json").write_text(json.dumps({
        "phase": "done", "pid": 2 ** 22 + 7, "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:10:00+00:00", "plan": {"generations": 2, "rounds": 2, "hours": 0.05},
        "search": {"stop_reason": "the plan's rounds are done", "generations": 2, "aborted": False},
        "final": {"skipped": "no cases were held back"}}), encoding="utf-8")
    (run_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (run_dir / "runs.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"id": "seed", "operator": "human-seed", "score": 100.0},
        {"id": "c1", "operator": "mutate", "score": 90.0},
    ]) + "\n", encoding="utf-8")
    return slug


@pytest.mark.slow
def test_start_wait_verdict_round_trip(tmp_path, capsys):
    from evolvekit.app.verdict import DECISIONS

    slug = _ready(tmp_path)
    capsys.readouterr()
    assert main(["study", "start", slug, "--json", "--home", str(tmp_path)]) == 0
    started = json.loads(capsys.readouterr().out)
    assert started["started"] is True and started["run"]
    code = main(["study", "wait", slug, "--json", "--timeout", "600", "--home", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0, out
    document = json.loads(out)
    assert document["decision"] in DECISIONS and document["run"] == started["run"]
    job = json.loads((tmp_path / "studies" / slug / "runs" / started["run"] / "job.json").read_text(encoding="utf-8"))
    assert job["phase"] == "done"


@pytest.mark.slow
def test_watch_prints_documents_until_done(tmp_path, capsys):
    slug = _ready(tmp_path)
    capsys.readouterr()
    assert main(["study", "start", slug, "--home", str(tmp_path)]) == 0
    assert capsys.readouterr().out.startswith("started run ")
    assert main(["study", "watch", slug, "--json", "--every", "1", "--home", str(tmp_path)]) == 0
    documents = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert documents and documents[-1]["phase"] in ("done", "stopped", "failed")


def test_start_twice_is_refused(started_study, capsys):
    assert main(["study", "start", "depot-a", "--home", str(started_study)]) == 1
    assert "already running" in capsys.readouterr().err


def test_start_queues_behind_another(tmp_path, capsys):
    _new(tmp_path, "Depot B")
    slug = _ready(tmp_path)
    _fake_active(tmp_path, "depot-b")
    capsys.readouterr()
    assert main(["study", "start", slug, "--json", "--home", str(tmp_path)]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["queued"] is True
    assert (tmp_path / "studies" / slug / "runs" / answer["run"] / "queued.json").is_file()
    assert main(["study", "stop", slug, "--home", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["study", "start", slug, "--home", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == ("queued behind Depot B; it starts when that study finishes "
                                               "(the app or `evolvekit study wait` starts it)")


def test_pending_marker_counts_as_active(tmp_path, monkeypatch):
    from evolvekit.app import jobs
    from evolvekit.app.store import Home

    _ready(tmp_path, "Depot A", "depot-a")
    _ready(tmp_path, "Depot B", "depot-b")
    home = Home(tmp_path)
    # The spawn happened, and the process that launched it has not written the launch yet.
    monkeypatch.setattr(jobs, "_spawn", lambda *args, **kwargs: 2 ** 22 + 7)
    monkeypatch.setattr(jobs, "_record_launch", lambda *args, **kwargs: None)
    started = jobs.start(home, home.study_root("depot-a"))
    run_dir = home.study_root("depot-a") / "runs" / started["run"]
    assert json.loads((run_dir / "launch.json").read_text(encoding="utf-8"))["pending"] is True
    assert jobs.active_run(home) == ("depot-a", started["run"])
    assert jobs.phase(run_dir)["phase"] == "starting"
    assert jobs.start(home, home.study_root("depot-b"))["queued"] is True


def test_pending_marker_expires(tmp_path):
    from evolvekit.app import jobs

    run_dir = tmp_path / "runs" / "r"
    run_dir.mkdir(parents=True)
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")
    (run_dir / "launch.json").write_text(json.dumps({"pending": True, "at": old}), encoding="utf-8")
    (run_dir / "job.log").write_text("Traceback ...\nOSError: no such program\n", encoding="utf-8")
    job = jobs.phase(run_dir)
    assert job["phase"] == "failed" and "did not start" in job["error"] and "no such program" in job["error"]


def _pending(run_dir):
    (run_dir / "launch.json").write_text(json.dumps({
        "pending": True, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}), encoding="utf-8")


def test_launch_queued_skips_a_run_claimed_meanwhile(tmp_path, monkeypatch):
    from evolvekit.app import jobs
    from evolvekit.app.store import Home

    _new(tmp_path)
    run_dir = _queued(tmp_path, "depot-a")
    asked = []

    def nothing_active(home):  # another process claims the run between the scan and the claim
        asked.append(1)
        if len(asked) == 2:
            _pending(run_dir)
        return None

    spawned = []
    monkeypatch.setattr(jobs, "active_run", nothing_active)
    monkeypatch.setattr(jobs, "_spawn", lambda *args, **kwargs: spawned.append(args) or 4242)
    assert jobs.launch_queued(Home(tmp_path)) is None
    assert spawned == [] and len(asked) == 2
    assert (run_dir / "queued.json").is_file() and json.loads((run_dir / "launch.json").read_text())["pending"] is True


def test_a_launch_that_started_nothing_queues_the_run_again(tmp_path, monkeypatch):
    from evolvekit.app import AppError, jobs
    from evolvekit.app.store import Home

    _new(tmp_path)
    run_dir = _queued(tmp_path, "depot-a")

    def no_pid(*args, **kwargs):
        raise AppError("the run could not be started: no message", 500)

    monkeypatch.setattr(jobs, "_spawn", no_pid)
    with pytest.raises(AppError):
        jobs.launch_queued(Home(tmp_path))
    assert (run_dir / "queued.json").is_file() and not (run_dir / "launch.json").exists()
    assert jobs.phase(run_dir)["phase"] == "queued"


def test_a_run_that_may_have_started_is_never_queued_again(tmp_path, monkeypatch):
    from evolvekit.app import jobs
    from evolvekit.app.store import Home

    _new(tmp_path)
    run_dir = _queued(tmp_path, "depot-a")

    def started_then_timed_out(env, argv, root, run_dir):  # the job began; the launcher never said its pid
        (run_dir / "job.json").write_text(json.dumps({
            "phase": "search", "pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }), encoding="utf-8")
        raise RuntimeError("the launcher timed out")

    monkeypatch.setattr(jobs, "_spawn", started_then_timed_out)
    with pytest.raises(RuntimeError):
        jobs.launch_queued(Home(tmp_path))
    assert not (run_dir / "queued.json").exists()
    assert jobs.phase(run_dir)["phase"] != "queued"
    assert jobs.launch_queued(Home(tmp_path)) is None, "the run is not launched a second time"
    # A queued.json that comes back anyway never outweighs the run's own job.
    (run_dir / "launch.json").unlink()
    (run_dir / "queued.json").write_text(json.dumps({"queued_at": "2026-01-01T00:00:01+00:00"}), encoding="utf-8")
    assert jobs.phase(run_dir)["phase"] != "queued"


def test_stop_leaves_a_run_claimed_by_a_launch(tmp_path, monkeypatch):
    from evolvekit.app import jobs

    _new(tmp_path)
    run_dir = _queued(tmp_path, "depot-a")
    read = jobs.phase
    calls = []

    def claimed_after_the_first_look(path):  # a launch in another process claims the run meanwhile
        calls.append(1)
        if len(calls) == 1:
            answer = read(path)
            _pending(run_dir)
            return answer
        return read(path)

    monkeypatch.setattr(jobs, "phase", claimed_after_the_first_look)
    answer = jobs.stop(tmp_path / "studies" / "depot-a", run_dir.name)
    assert run_dir.is_dir() and (run_dir / "launch.json").is_file()
    assert answer == {"stopping": True, "run": run_dir.name}, "a run that is starting is asked to stop"
    assert (run_dir / STOP_REQUEST).is_file()


def test_wait_ticks_the_queue(tmp_path, monkeypatch, capsys):
    from evolvekit import cli
    from evolvekit.app import jobs

    _new(tmp_path)
    run_dir = _queued(tmp_path, "depot-a")
    launched = []

    def launch(home, root, run_dir, extra, *, with_keys, **kwargs):  # a run that stopped at once
        launched.append(run_dir.name)
        (run_dir / "launch.json").write_text(json.dumps({"pid": 2 ** 22 + 7, "started_at": "2026-01-01T00:00:02+00:00"}),
                                             encoding="utf-8")
        (run_dir / "job.json").write_text(json.dumps({"phase": "stopped", "pid": 2 ** 22 + 7,
                                                      "started_at": "2026-01-01T00:00:02+00:00"}), encoding="utf-8")
        return 2 ** 22 + 7

    monkeypatch.setattr(jobs, "_launch", launch)
    monkeypatch.setattr(cli, "WAIT_EVERY_S", 0.05)
    assert main(["study", "wait", "depot-a", "--timeout", "5", "--home", str(tmp_path)]) == 1
    assert launched == [run_dir.name]
    assert not (run_dir / "queued.json").exists()


def test_wait_shows_the_state_line_as_it_changes(tmp_path, monkeypatch, capsys):
    """For people, `wait` says on stderr what `watch` says, once per change, so a long wait is not a hang;
    stdout stays the answer at the end."""
    from evolvekit import cli
    from evolvekit.app import AppError, jobs, verdict

    _new(tmp_path)
    _queued(tmp_path, "depot-a")
    phases = iter(["queued", "search", "search", "search", "check", "stopped"])
    lines = {"queued": "queued", "search": "running · round 1 of 2", "check": "running · final check",
             "stopped": "stopped"}
    current = {"phase": "queued"}

    def phase(_run_dir):
        current["phase"] = next(phases, "stopped")
        return {"phase": current["phase"]}

    def no_verdict(*_args, **_kwargs):
        raise AppError("no final check", 404)

    monkeypatch.setattr(jobs, "launch_queued", lambda home: None)
    monkeypatch.setattr(jobs, "phase", phase)
    monkeypatch.setattr(jobs, "run_line", lambda root, study, run_dir, document_of=None: {"line": lines[current["phase"]]})
    monkeypatch.setattr(verdict, "build_verdict", no_verdict)
    monkeypatch.setattr(cli, "WAIT_EVERY_S", 0.01)
    capsys.readouterr()
    assert main(["study", "wait", "depot-a", "--timeout", "5", "--home", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert captured.err.splitlines() == ["queued", "running · round 1 of 2", "running · final check"], captured.err
    assert captured.out == "stopped\n"


def test_wait_json_shows_no_progress(tmp_path, monkeypatch, capsys):
    from evolvekit import cli
    from evolvekit.app import AppError, jobs, verdict

    _new(tmp_path)
    _queued(tmp_path, "depot-a")
    phases = iter(["search", "check", "stopped"])
    monkeypatch.setattr(jobs, "launch_queued", lambda home: None)
    monkeypatch.setattr(jobs, "phase", lambda _d: {"phase": next(phases, "stopped")})
    monkeypatch.setattr(jobs, "run_line", lambda *a, **k: {"line": "a state line"})
    monkeypatch.setattr(verdict, "build_verdict", lambda *a, **k: (_ for _ in ()).throw(AppError("none", 404)))
    monkeypatch.setattr(cli, "WAIT_EVERY_S", 0.01)
    capsys.readouterr()
    assert main(["study", "wait", "depot-a", "--json", "--timeout", "5", "--home", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert captured.err == "" and json.loads(captured.out)["phase"] == "stopped"


def test_a_fault_in_the_queue_does_not_end_wait(tmp_path, monkeypatch, capsys):
    from evolvekit import cli
    from evolvekit.app import jobs
    from evolvekit.app.store import Home

    def broken(home):  # another study's launcher timed out
        raise RuntimeError("the launcher timed out")

    monkeypatch.setattr(jobs, "launch_queued", broken)
    cli._tick_queue(Home(tmp_path))  # noqa: SLF001
    assert capsys.readouterr().err.strip() == "could not start a queued run: the launcher timed out"


def test_wait_ends_when_the_run_is_gone(tmp_path, monkeypatch, capsys):
    import shutil

    from evolvekit import cli
    from evolvekit.app import jobs

    _new(tmp_path)
    run_dir = _queued(tmp_path, "depot-a")
    ticks = []

    def dequeued_elsewhere(home):  # `study stop` in another terminal, or the app's card
        ticks.append(1)
        if len(ticks) == 2:
            shutil.rmtree(run_dir)
        if len(ticks) > 50:
            raise KeyboardInterrupt  # it never ended by itself

    monkeypatch.setattr(jobs, "launch_queued", dequeued_elsewhere)
    monkeypatch.setattr(cli, "WAIT_EVERY_S", 0.01)
    capsys.readouterr()
    assert main(["study", "wait", "depot-a", "--home", str(tmp_path)]) == 1
    assert f"run {run_dir.name} is gone" in capsys.readouterr().err


def test_watch_ends_when_the_run_left_the_queue(tmp_path, monkeypatch, capsys):
    from evolvekit.app import jobs

    _new(tmp_path)
    run_dir = _queued(tmp_path, "depot-a")
    ticks = []

    def dequeued_but_the_folder_stayed(home):
        ticks.append(1)
        (run_dir / "queued.json").unlink(missing_ok=True)
        (run_dir / "keep.txt").write_text("x", encoding="utf-8")
        if len(ticks) > 50:
            raise KeyboardInterrupt

    monkeypatch.setattr(jobs, "launch_queued", dequeued_but_the_folder_stayed)
    capsys.readouterr()
    assert main(["study", "watch", "depot-a", "--every", "0.01", "--home", str(tmp_path)]) == 1
    assert f"run {run_dir.name} is gone" in capsys.readouterr().err


def test_a_claimed_launch_writes_the_marker_once(tmp_path, monkeypatch):
    from evolvekit.app import jobs
    from evolvekit.app.store import Home

    _new(tmp_path)
    run_dir = _queued(tmp_path, "depot-a")
    marks, seen = [], []
    mark = jobs._mark_pending  # noqa: SLF001
    monkeypatch.setattr(jobs, "_mark_pending", lambda *a, **k: marks.append(k) or mark(*a, **k))

    def spawn(env, argv, root, run_dir):
        seen.append(json.loads((run_dir / "launch.json").read_text(encoding="utf-8")))
        return 4242

    monkeypatch.setattr(jobs, "_spawn", spawn)
    assert jobs.launch_queued(Home(tmp_path)) == f"depot-a/{run_dir.name}"
    assert marks == [{"exclusive": True}], "the claim is the marker: no second truncating write"
    assert seen[0]["pending"] is True
    assert json.loads((run_dir / "launch.json").read_text(encoding="utf-8"))["pid"] == 4242
    assert not list(run_dir.glob("*.tmp"))


def test_wait_timeout_exits_4(started_study, capsys):
    assert main(["study", "wait", "depot-a", "--timeout", "1", "--home", str(started_study)]) == 4
    assert capsys.readouterr().err.strip()


def test_stop_dequeues_a_queued_run(tmp_path, capsys):
    _new(tmp_path)
    run_dir = _queued(tmp_path, "depot-a")
    capsys.readouterr()
    assert main(["study", "stop", "depot-a", "--home", str(tmp_path)]) == 0
    assert not run_dir.exists() and not (run_dir / "queued.json").exists()
    assert "dequeued" in capsys.readouterr().out


def test_status_json_equals_api_document(app, finished_study, capsys):
    from test_app_server import call

    capsys.readouterr()
    assert main(["study", "status", finished_study, "--json", "--home", str(app.home.root)]) == 0
    mine = json.loads(capsys.readouterr().out)
    status, theirs = call(app, "GET", f"/api/studies/{finished_study}/status")
    assert status == 200 and mine == theirs
    assert main(["study", "status", finished_study, "--home", str(app.home.root)]) == 0
    assert capsys.readouterr().out.strip().startswith("finished")


def test_open_prints_study_url_of_running_app(app, capsys):
    home = app.home.root
    _new(home)
    capsys.readouterr()
    assert main(["study", "open", "depot-a", "--no-browser", "--home", str(home)]) == 0
    assert capsys.readouterr().out == app.url_with_token + "#/study/depot-a\n"


def test_open_starts_an_app_when_none_runs(tmp_path, monkeypatch, capsys):
    import threading

    from evolvekit.app import detach
    from evolvekit.app.server import AppServer

    home = tmp_path / "home"
    _new(home)
    capsys.readouterr()
    calls, servers = [], []
    recorded = threading.Event()  # set once the server is in `servers`: app.json is seen before that

    def start_app(argv, **kwargs):
        calls.append(list(argv))

        def serve():
            try:
                server = AppServer(home, port=0)  # writes app.json
                servers.append(server)
            finally:
                recorded.set()
            server.start()

        threading.Thread(target=serve, daemon=True).start()
        return 4242

    monkeypatch.setattr(detach, "spawn_detached", start_app)
    try:
        assert main(["study", "open", "depot-a", "--no-browser", "--home", str(home)]) == 0
        assert recorded.wait(10)
        assert len(calls) == 1 and calls[0][-4:] == ["app", "--home", str(home), "--service"]
        assert capsys.readouterr().out == servers[0].url_with_token + "#/study/depot-a\n"
    finally:
        if calls:
            recorded.wait(10)
        for server in servers:
            server.stop()


def test_open_times_out_with_a_sentence(tmp_path, monkeypatch, capsys):
    from evolvekit import cli
    from evolvekit.app import detach

    home = tmp_path / "home"
    _new(home)
    capsys.readouterr()
    monkeypatch.setattr(detach, "spawn_detached", lambda argv, **kwargs: 4242)
    monkeypatch.setattr(cli, "OPEN_WAIT_S", 1.0)
    assert main(["study", "open", "depot-a", "--no-browser", "--home", str(home)]) == 1
    assert "the app did not start; run `evolvekit app` and look at its output" in capsys.readouterr().err


def test_open_refuses_a_study_that_is_not_there(tmp_path, monkeypatch, capsys):
    from evolvekit.app import detach

    def never(argv, **kwargs):
        raise AssertionError("no app is started for a study that is not there")

    monkeypatch.setattr(detach, "spawn_detached", never)
    assert main(["study", "open", "nope", "--no-browser", "--home", str(tmp_path / "home")]) == 1
    assert "there is no study" in capsys.readouterr().err


# -- the next step, on stderr: for people, never in the way of a script


def test_a_ready_check_says_start_next(tmp_path, capsys):
    _new(tmp_path, "Depot A", "--template", "tune", "--app", str(SOLVER))
    assert main(["study", "cases", "depot-a", "--add", *map(str, SAMPLES), "--hold-back", "1", "--home", str(tmp_path)]) == 0
    assert f'next: evolvekit study check depot-a --home "{tmp_path}"' in capsys.readouterr().err
    assert main(["study", "check", "depot-a", "--home", str(tmp_path)]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("ready: no problems\nplan: "), "stdout keeps its contract: the answer, then the plan"
    assert "next:" not in out
    assert f'next: evolvekit study start depot-a --home "{tmp_path}"' in err


class _Screen:
    """A terminal both streams write to: stdout buffered until flushed (as when piped), stderr at once."""

    def __init__(self):
        self.seen: list[str] = []

    def stream(self, buffered: bool):
        screen = self

        class _Stream:
            encoding = "utf-8"

            def __init__(self):
                self.held: list[str] = []

            def write(self, text):
                (self.held if buffered else screen.seen).append(text)
                return len(text)

            def flush(self):
                screen.seen.extend(self.held)
                self.held.clear()

            def isatty(self):
                return False

        return _Stream()


def test_the_result_comes_before_the_next_step(tmp_path, monkeypatch):
    import sys

    screen = _Screen()
    out, err = screen.stream(buffered=True), screen.stream(buffered=False)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    assert main(["study", "new", "Depot A", "--harness", "demo-tour", "--template", "tune", "--app", str(SOLVER),
                 "--home", str(tmp_path)]) == 0
    assert main(["study", "cases", "depot-a", "--add", *map(str, SAMPLES), "--hold-back", "1", "--home", str(tmp_path)]) == 0
    assert main(["study", "check", "depot-a", "--home", str(tmp_path)]) == 0
    out.flush()
    text = "".join(screen.seen)
    assert text.index("depot-a\n") < text.index("next: evolvekit study cases")
    assert text.index("4 cases added") < text.index("next: evolvekit study check")
    assert text.index("ready: no problems") < text.index("plan: ") < text.index("next: evolvekit study start")


def test_study_new_says_the_application_then_the_next_step(tmp_path, capsys):
    assert _new(tmp_path, "Depot A", "--app", str(SOLVER)) == 0
    out, err = capsys.readouterr()
    assert out == "depot-a\n"
    lines = err.splitlines()
    assert lines[0].startswith(f"application: {SOLVER} (version ")
    assert lines[1].startswith("next: evolvekit study cases depot-a --add <files> --hold-back N --home ")


def test_no_hint_with_json_or_without_home(tmp_path, monkeypatch, capsys):
    _new(tmp_path, "Depot A", "--json")
    assert "next:" not in capsys.readouterr().err
    monkeypatch.setenv("EVOLVEKIT_HOME", str(tmp_path))
    assert main(["study", "new", "Depot B", "--harness", "demo-tour"]) == 0
    assert "next: evolvekit study cases depot-b --add <files> --hold-back N\n" in capsys.readouterr().err, "no --home given"


def test_start_and_a_finished_wait_say_what_comes_next(tmp_path, monkeypatch, capsys):
    from evolvekit.app import jobs

    _new(tmp_path)
    monkeypatch.setattr(jobs, "start", lambda home, root: {"run": "20260101-000000", "started": True})
    capsys.readouterr()
    assert main(["study", "start", "depot-a", "--home", str(tmp_path)]) == 0
    out, err = capsys.readouterr()
    assert out == "started run 20260101-000000\n"
    assert "next: evolvekit study wait depot-a" in err and ", or evolvekit study watch depot-a" in err
    run_dir = tmp_path / "studies" / "depot-a" / "runs" / "20260101-000000"
    run_dir.mkdir(parents=True)
    (run_dir / "job.json").write_text(json.dumps({
        "phase": "done", "pid": 2 ** 22 + 7, "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:10:00+00:00", "plan": {"generations": 2, "rounds": 2, "hours": 0.05},
        "final": {"skipped": "no cases were held back"}}), encoding="utf-8")
    assert main(["study", "wait", "depot-a", "--home", str(tmp_path)]) == 0
    err = capsys.readouterr().err
    assert f'next: evolvekit study report depot-a --markdown --home "{tmp_path}", or evolvekit study open depot-a' in err
    assert "or evolvekit study open" in err, "each alternative with its evolvekit"
    assert main(["study", "wait", "depot-a", "--json", "--home", str(tmp_path)]) == 0
    assert "next:" not in capsys.readouterr().err


def test_a_run_with_a_job_is_never_gone(tmp_path):
    from evolvekit.cli import _gone

    run_dir = tmp_path / "runs" / "r"
    run_dir.mkdir(parents=True)
    assert _gone(run_dir, "unknown"), "nothing in it: gone"
    (run_dir / "job.json").write_text("{}", encoding="utf-8")
    assert not _gone(run_dir, "unknown"), "a job.json is a run, whatever its phase reads as"


THIN_ONE = "With 1 held-back case a 'use' is unlikely: the worst case stays wide. Hold back more cases for a firmer answer."


def test_study_check_and_compile_say_a_thin_final_check_before_the_run(tmp_path, capsys):
    """Persona round 2, D2: a note, not a problem: the study still checks ready and compiles (exit 0)."""
    _new(tmp_path, "Depot A", "--template", "tune", "--app", str(SOLVER))
    assert main(["study", "cases", "depot-a", "--add", *map(str, SAMPLES), "--hold-back", "1", "--home", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["study", "check", "depot-a", "--home", str(tmp_path)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "ready: no problems" and f"note: {THIN_ONE}" in out
    assert main(["study", "compile", "depot-a", "--home", str(tmp_path)]) == 0
    assert capsys.readouterr().out.rstrip().endswith(THIN_ONE), "the plan line ends with it"
    _new(tmp_path, "Depot B", "--template", "tune", "--app", str(SOLVER))
    assert main(["study", "cases", "depot-b", "--add", *map(str, SAMPLES), "--hold-back", "3", "--home", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["study", "check", "depot-b", "--home", str(tmp_path)]) == 0
    assert "note:" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# campaigns: study start on a schedule, --now, stop, wait, status
# ---------------------------------------------------------------------------


def _friday_evening(monkeypatch):
    """The clock reads Friday 13 November 2026, 18:00, for every command."""
    from evolvekit.app import scheduler

    monkeypatch.setattr(scheduler, "clock_of", lambda home: scheduler.Clock(start=local("2026-11-13 18:00"), origin=time.time()))


def test_start_scheduled_prints_next_window(tmp_path, capsys, monkeypatch):
    from evolvekit.app import jobs, scheduler

    _friday_evening(monkeypatch)
    home, root = campaign_study(tmp_path); (root / scheduler.CAMPAIGN).unlink()
    started = []
    monkeypatch.setattr(jobs, "start", lambda *a, **k: started.append(a) or {"run": "x"})
    assert main(["study", "start", root.name, "--home", str(home.root)]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[0] == "scheduled: next window Sat 00:00 (the app, or evolvekit study wait, starts it then)"
    assert "evolvekit app install-startup" in err and started == []
    assert scheduler.read_campaign(root)["state"] == "armed"
    assert main(["study", "start", root.name, "--home", str(home.root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[0]) == {"scheduled": True, "next": local("2026-11-14 00:00").isoformat(), "edge": "start"}


def test_start_now_starts_at_once(tmp_path, capsys, monkeypatch):
    from evolvekit.app import jobs, scheduler

    _friday_evening(monkeypatch)
    home, root = campaign_study(tmp_path); (root / scheduler.CAMPAIGN).unlink()
    started = []
    monkeypatch.setattr(jobs, "start", lambda *a, **k: started.append((a, k)) or {"run": "x"})
    assert main(["study", "start", root.name, "--now", "--home", str(home.root)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "started run x"
    assert len(started) == 1 and started[0][1] == {}
    campaign = scheduler.read_campaign(root)
    assert campaign["state"] == "armed" and campaign["by_hand"] == {"run": "x", "session": 0}


def test_stop_pauses_the_campaign(tmp_path, capsys):
    from evolvekit.app import scheduler

    home, root = campaign_study(tmp_path)
    assert main(["study", "stop", root.name, "--home", str(home.root)]) == 0
    assert capsys.readouterr().out.strip() == "paused the campaign (no run was running); evolvekit study start resumes it"
    assert scheduler.read_campaign(root)["state"] == "paused"


def test_stop_pauses_the_campaign_and_stops_its_run(tmp_path, capsys):
    from evolvekit.app import scheduler

    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "search", alive=True)
    assert main(["study", "stop", root.name, "--home", str(home.root)]) == 0
    assert capsys.readouterr().out.strip() == (f"paused the campaign and asked run r1 to stop (evolvekit study wait "
                                               f"{root.name} returns when it has); evolvekit study start resumes it")
    assert scheduler.read_campaign(root)["state"] == "paused" and (root / "runs" / "r1" / STOP_REQUEST).exists()


def test_status_on_a_campaign_says_its_line(tmp_path, capsys, monkeypatch):
    _friday_evening(monkeypatch)
    home, root = campaign_study(tmp_path)
    assert main(["study", "status", root.name, "--home", str(home.root)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "campaign · next window Sat 00:00"
    assert main(["study", "status", root.name, "--home", str(home.root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["campaign"]["line"] == "Campaign · next window Sat 00:00"


def test_wait_on_a_campaign_waits_through_window_stops(tmp_path, capsys, monkeypatch):
    from evolvekit import cli
    from evolvekit.app import jobs, scheduler, verdict

    _friday_evening(monkeypatch)
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "stopped", generations=2, planned=6)
    # A window's stop, the next window's search, then done: the campaign is done only then.
    steps = [("stopped", False, "Campaign · next window Sat 00:00"), ("stopped", False, "Campaign · next window Sat 00:00"),
             ("search", False, "Campaign · this window ends Sun 23:59"), ("done", True, "Campaign finished")]
    at = {"i": 0}

    def step():
        return steps[min(at["i"], len(steps) - 1)]

    def done(root, study, *, now):
        answer = step()[1]
        at["i"] += 1
        return answer

    monkeypatch.setattr(cli, "_tick_queue", lambda home: None)
    monkeypatch.setattr(scheduler, "campaign_done", done)
    monkeypatch.setattr(scheduler, "campaign_state", lambda root, study, *, now: {"state": "armed", "line": steps[at["i"] - 1][2]})
    monkeypatch.setattr(jobs, "phase", lambda run_dir: {"phase": steps[min(at["i"], len(steps)) - 1][0]})
    monkeypatch.setattr(verdict, "build_verdict", lambda root, run: {"line": "Not proven on the held-back cases.", "advice": ""})
    monkeypatch.setattr(cli, "WAIT_EVERY_S", 0.01)
    capsys.readouterr()
    assert main(["study", "wait", root.name, "--timeout", "5", "--home", str(home.root)]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[0] == "Not proven on the held-back cases."
    said = [line for line in err.splitlines() if line.startswith("Campaign")]
    assert said == ["Campaign · next window Sat 00:00", "Campaign · this window ends Sun 23:59"], err


def test_wait_on_a_paused_campaign_without_a_finished_run_exits_1(tmp_path, capsys, monkeypatch):
    from evolvekit import cli
    from evolvekit.app import scheduler

    _friday_evening(monkeypatch)
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "stopped", generations=2, planned=6)
    monkeypatch.setattr(cli, "_tick_queue", lambda home: scheduler.pause(root, reason="stopped by you"))
    monkeypatch.setattr(cli, "WAIT_EVERY_S", 0.01)
    capsys.readouterr()
    assert main(["study", "wait", root.name, "--timeout", "5", "--home", str(home.root)]) == 1
    assert capsys.readouterr().out.strip() == "Campaign · paused: stopped by you"


def test_wait_on_a_campaign_times_out_with_4(tmp_path, capsys, monkeypatch):
    from evolvekit import cli

    _friday_evening(monkeypatch)
    home, root = campaign_study(tmp_path)
    monkeypatch.setattr(cli, "_tick_queue", lambda home: None)
    monkeypatch.setattr(cli, "WAIT_EVERY_S", 0.01)
    assert main(["study", "wait", root.name, "--timeout", "0.1", "--home", str(home.root)]) == 4, "no run needed"
    assert "Campaign · next window Sat 00:00" in capsys.readouterr().err


def test_start_after_a_finished_campaign_begins_a_new_one(tmp_path, capsys, monkeypatch):
    from evolvekit.app import jobs, scheduler

    _friday_evening(monkeypatch)
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "done", generations=6, planned=6, used_s=3600 - 60, final={"decision": "use"})
    scheduler.pause(root, reason=scheduler.COMPLETE, state="complete")
    assert main(["study", "start", root.name, "--home", str(home.root)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == ("scheduled: a new campaign (the last one finished); next window "
                                                       "Sat 00:00 (the app, or evolvekit study wait, starts it then)")
    scheduler.pause(root, reason=scheduler.COMPLETE, state="complete")
    started = []
    monkeypatch.setattr(jobs, "start", lambda *a, **k: started.append(k) or {"run": "r2"})
    assert main(["study", "start", root.name, "--now", "--home", str(home.root)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "started run r2: a new campaign (the last one finished)"
    assert started == [{}], "a new run, at once"


def test_wait_ends_when_the_schedule_is_taken_out(tmp_path, capsys, monkeypatch):
    from evolvekit import cli
    from evolvekit.harness.study import load_study, save_study

    _friday_evening(monkeypatch)
    home, root = campaign_study(tmp_path)
    tick = cli._tick_queue  # noqa: SLF001

    def taken_out_then_tick(home):
        study = load_study(root)
        study.schedule = None
        save_study(study, root)  # "As soon as I start it" in Set up, while the wait waits
        tick(home)

    monkeypatch.setattr(cli, "_tick_queue", taken_out_then_tick)
    monkeypatch.setattr(cli, "WAIT_EVERY_S", 0.01)
    assert main(["study", "wait", root.name, "--timeout", "5", "--home", str(home.root)]) == 1
    assert capsys.readouterr().out.strip() == "Campaign · paused: the study has no schedule any more"
