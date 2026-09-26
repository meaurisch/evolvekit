"""The app's library home on disk (`evolvekit/app/store.py`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evolvekit.app import AppError
from evolvekit.app.store import Home, safe_name
from evolvekit.harness.study import load_study

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "harnesses" / "demo-tour"
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"


@pytest.fixture
def home(tmp_path) -> Home:
    return Home(tmp_path / "home")


def _demo_study(home: Home, name: str = "Tour settings") -> str:
    slug = home.create_study("demo-tour", "tune", name)
    home.save_step(slug, {"application": {"path": str(SOLVER), "version": ""}})
    return slug


def test_settings_have_defaults_and_refuse_what_they_do_not_know(home):
    assert home.settings()["provider"] == "openrouter"
    assert home.settings()["models"]["assistant"] == "anthropic/claude-sonnet-5"
    home.save_settings({"provider": "claude-cli", "models": {"search": "sonnet"}})
    assert home.settings()["provider"] == "claude-cli" and home.settings()["models"]["search"] == "sonnet"
    with pytest.raises(AppError, match="provider: one of"):
        home.save_settings({"provider": "somewhere"})
    home.remember_application("C:/python.exe")
    home.remember_application("C:/other.exe")
    assert home.settings()["recent_applications"][:2] == ["C:/other.exe", "C:/python.exe"]


def test_keys_are_written_and_only_ever_reported_as_set(home, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert home.keys()["OPENROUTER_API_KEY"] is False
    home.set_keys({"OPENROUTER_API_KEY": "sk-secret-value"})
    assert home.keys()["OPENROUTER_API_KEY"] is True
    assert "sk-secret-value" not in json.dumps(home.settings()) + json.dumps(home.keys())
    assert home.job_environment() == {"OPENROUTER_API_KEY": "sk-secret-value"}
    with pytest.raises(AppError, match="not a key the app keeps"):
        home.set_keys({"PATH": "C:/evil"})
    with pytest.raises(AppError, match="one line"):
        home.set_keys({"OPENROUTER_API_KEY": "a\nPATH=x"})
    home.set_keys({"OPENROUTER_API_KEY": ""})
    assert home.keys()["OPENROUTER_API_KEY"] is False


@pytest.mark.parametrize("name", ["..", "../x.json", "a/b.json", "a\\b.json", "C:x.json", "\\\\host\\share\\x.json",
                                  "con.json", "LPT1.json", "x\x00.json", ""])
def test_unsafe_upload_names_are_refused(name):
    with pytest.raises(AppError):
        safe_name(name, (".json",))


def test_upload_names_are_cleaned_and_must_be_a_format_the_harness_reads():
    assert safe_name("Monday run (1).json", (".json",)) == "Monday-run-1-.json"
    assert safe_name("county-50.JSON", (".json",)) == "county-50.JSON"
    with pytest.raises(AppError, match="this harness reads .json files"):
        safe_name("notes.txt", (".json",))


def test_studies_get_unique_slugs_and_bad_slugs_are_not_found(home):
    first = home.create_study("demo-tour", "tune", "Demo")
    second = home.create_study("demo-tour", None, "Demo")
    assert (first, second) == ("demo", "demo-2")
    assert home.slugs() == ["demo", "demo-2"]
    for slug in ("../demo", "Demo", "a/b", "nope"):
        with pytest.raises(AppError) as caught:
            home.study_root(slug)
        assert caught.value.status == 404
    with pytest.raises(AppError, match="no harness 'nope'"):
        home.create_study("nope", None, "x")


def test_a_case_is_stored_added_to_training_and_inspected(home):
    slug = _demo_study(home)
    added = home.put_case(slug, "town-40.json", (DEMO / "samples" / "town-40.json").read_bytes())
    assert added == {"name": "town-40.json", "inspected": {"ok": True, "summary": "40 stops", "tables": {"stops": 40}}}
    assert load_study(home.study_root(slug)).training == ["cases/town-40.json"]
    broken = home.put_case(slug, "broken.json", b'{"nothing": 1}')
    assert broken["inspected"]["ok"] is False
    assert "neither `stops` nor a `seed`" in broken["inspected"]["error"]
    with pytest.raises(AppError, match="this harness reads .json files"):
        home.put_case(slug, "notes.txt", b"x")
    home.delete_case(slug, "broken.json")
    assert [c["name"] for c in home.cases(slug)] == ["town-40.json"]
    assert load_study(home.study_root(slug)).training == ["cases/town-40.json"]


def test_a_case_before_the_application_waits_for_it(home):
    slug = home.create_study("demo-tour", "tune", "No app yet")
    added = home.put_case(slug, "town-40.json", (DEMO / "samples" / "town-40.json").read_bytes())
    assert added["inspected"]["ok"] is None and "choose the application first" in added["inspected"]["error"]


def test_the_samples_and_a_split_that_spans_the_sizes(home):
    slug = _demo_study(home)
    assert home.use_samples(slug) == ["county-50.json", "north-south-30.json", "region-60.json", "town-40.json"]
    assert all(c["inspected"]["ok"] for c in home.cases(slug))
    assert home.suggested_test_count(slug) == 1
    assert home.split(slug, 1) == ["cases/county-50.json"]
    assert home.split(slug, 2) == ["cases/town-40.json", "cases/region-60.json"]
    study = load_study(home.study_root(slug))
    assert study.training == ["cases/north-south-30.json", "cases/county-50.json"], "the smallest stays for the preview"
    assert home.split(slug, 0) == []
    with pytest.raises(AppError, match="at most 3"):
        home.split(slug, 4)


def test_problems_land_on_their_steps_and_go_away_when_the_step_is_done(home):
    slug = home.create_study("demo-tour", "tune", "Steps")
    steps = {p["step"] for p in home.problems(slug)}
    assert steps == {2, 3}, home.problems(slug)
    home.save_step(slug, {"application": {"path": str(SOLVER), "version": ""}})
    home.use_samples(slug)
    assert home.problems(slug) == []
    document = home.document(slug)
    assert [s["title"] for s in document["steps"]][:3] == ["Question", "Application", "Cases"]
    assert all(s["problems"] == 0 for s in document["steps"])


def test_saving_a_step_checks_its_shape_and_keeps_the_harness(home):
    slug = _demo_study(home)
    home.save_step(slug, {"limits": {"time_per_case_s": 1.5, "retries": 0, "runs_per_case": 1}, "step": 6})
    study = load_study(home.study_root(slug))
    assert study.limits.time_per_case_s == 1.5 and study.step == 6
    with pytest.raises(AppError, match="keeps its harness"):
        home.save_step(slug, {"harness": {"id": "pyvrp", "version": "1.0.0"}})
    with pytest.raises(AppError, match="not part of a study"):
        home.save_step(slug, {"surprise": 1})
    with pytest.raises(AppError, match="expected a number"):
        home.save_step(slug, {"limits": {"time_per_case_s": "soon"}})


def test_a_settings_input_is_checked_against_the_harness(home):
    slug = home.create_study("pyvrp", "tune", "PyVRP")
    with pytest.raises(AppError, match="not a setting of PyVRP"):
        home.put_input(slug, "solver_settings", "mine.json", b'{"num_neighbours": 40, "speed": 3}')
    with pytest.raises(AppError, match="num_neighbours"):
        home.put_input(slug, "solver_settings", "mine.json", b'{"num_neighbours": 4000}')
    assert home.put_input(slug, "solver_settings", "My settings.json", b'{"num-neighbours": 40}') == "My-settings.json"
    assert load_study(home.study_root(slug)).inputs == {"solver_settings": "inputs/My-settings.json"}
    home.remove_input(slug, "solver_settings")
    assert load_study(home.study_root(slug)).inputs == {}
