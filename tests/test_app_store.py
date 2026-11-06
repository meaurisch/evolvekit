"""The app's library home on disk (`evolvekit/app/store.py`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evolvekit.app import AppError
from evolvekit.app.store import Home, safe_name, split_words
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


def test_more_cases_hold_more_back(home):
    sample = (DEMO / "samples" / "town-40.json").read_bytes()

    def counts_for(n: int) -> int:
        slug = _demo_study(home, f"{n} cases")
        for i in range(n):
            home.put_case(slug, f"town-{i:02d}.json", sample)
        return home.suggested_test_count(slug)

    # the demo harness holds back a 0.3 share; more cases allow a firmer check
    assert counts_for(8) == 3 and counts_for(10) == 4 and counts_for(20) == 8 and counts_for(3) == 1
    assert counts_for(1) == 0 and counts_for(2) == 1, "never every case: the search keeps one"


def test_the_split_says_how_firm_the_answer_can_be():
    assert split_words(0, 5) == "Nothing held back: there will be no final check, so no answer about cases the search never saw."
    assert split_words(1, 5) == "1 held back: the final check can give a number, but no range and no verdict."
    assert split_words(2, 6) == "2 held back: a rough answer; the range will be wide."
    assert split_words(3, 8) == "3 held back: a fair answer for a clear, consistent gain."
    assert split_words(5, 12) == "5 held back: a firm answer."


def test_the_document_carries_the_split_words(home):
    slug = _demo_study(home)
    home.use_samples(slug)
    assert home.document(slug)["split_words"] == split_words(0, 4)
    home.split(slug, 2)
    assert home.document(slug)["split_words"] == split_words(2, 4)


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


def test_prices_are_kept_per_model_and_checked(home):
    assert home.settings()["prices"] == {}
    home.save_settings({"prices": {"my-deployment": [1.5, 6]}})
    assert home.settings()["prices"] == {"my-deployment": [1.5, 6.0]}
    for bad in ({"m": [1]}, {"m": [-1, 2]}, {"m": "cheap"}, ["m"], {"": [1, 2]}):
        with pytest.raises(AppError, match="prices"):
            home.save_settings({"prices": bad})
    home.save_settings({"prices": {}})
    assert home.settings()["prices"] == {}


def test_an_empty_model_means_the_cli_default_and_is_refused_elsewhere(home):
    home.save_settings({"provider": "claude-cli", "models": {"assistant": "", "search": ""}})
    assert home.settings()["models"] == {"assistant": "", "search": ""}
    with pytest.raises(AppError, match="deployment name"):
        home.save_settings({"provider": "azure", "models": {"assistant": ""}})
    with pytest.raises(AppError, match="models.assistant"):
        home.save_settings({"provider": "openrouter", "models": {"assistant": ""}})


# ---------------------------------------------------------------------------
# managing studies: the trash, duplicates, the zip, a removed case
# ---------------------------------------------------------------------------


def test_a_deleted_study_waits_in_the_trash_under_its_slug_and_a_stamp(home):
    import re

    slug = _demo_study(home, "Demo")
    entry = home.delete_study(slug)
    assert re.fullmatch(r"demo~\d{8}-\d{6}", entry), entry
    assert home.slugs() == [], "the trash is not a study"
    with pytest.raises(AppError) as caught:
        home.study_root(slug)
    assert caught.value.status == 404
    (listed,) = home.trash()
    assert listed["entry"] == entry and listed["name"] == "Demo" and listed["deleted_at"][:4] == entry[5:9]
    assert home.create_study("demo-tour", "tune", "Demo") == "demo", "the slug is free again"
    assert home.restore_study(entry) == "demo-2", "the slug was taken meanwhile"
    assert home.trash() == [] and home.slugs() == ["demo", "demo-2"]
    with pytest.raises(AppError) as caught:
        home.restore_study(entry)
    assert caught.value.status == 404
    for bad in ("../x", "demo~x", "demo", ".."):
        with pytest.raises(AppError):
            home.restore_study(bad)


def test_the_trash_is_emptied_after_seven_days(home):
    slug = _demo_study(home, "Old")
    entry = home.delete_study(slug)
    old = home.studies_dir / ".trash" / "old~20200101-120000"
    (home.studies_dir / ".trash" / entry).rename(old)
    fresh = home.delete_study(_demo_study(home, "Fresh"))
    assert home.clean_trash(days=7) == ["old~20200101-120000"]
    assert not old.exists() and (home.studies_dir / ".trash" / fresh).is_dir()
    assert [e["entry"] for e in home.trash()] == [fresh]
    assert home.clean_trash(days=7) == []


def test_archiving_is_a_marker_and_not_a_study_field(home):
    slug = _demo_study(home, "Shelf")
    assert home.is_archived(slug) is False
    home.set_archived(slug, True)
    assert home.is_archived(slug) is True and (home.study_root(slug) / ".archived").is_file()
    assert "archived" not in load_study(home.study_root(slug)).to_yaml()
    home.set_archived(slug, False)
    assert home.is_archived(slug) is False and not (home.study_root(slug) / ".archived").exists()


def test_a_duplicate_takes_the_setup_and_not_the_runs(home):
    slug = _demo_study(home, "Original")
    home.use_samples(slug)
    home.save_step(slug, {"step": 5})
    root = home.study_root(slug)
    (root / "runs" / "20260101-000000").mkdir(parents=True)
    (root / "runs" / "20260101-000000" / "job.json").write_text('{"phase": "done"}', encoding="utf-8")
    (root / "preview").mkdir()
    (root / "assistant.jsonl").write_text("{}\n", encoding="utf-8")
    copy = home.duplicate_study(slug, "Second try")
    assert copy == "second-try"
    new_root = home.study_root(copy)
    assert not (new_root / "runs").exists() and not (new_root / "preview").exists() and not (new_root / "assistant.jsonl").exists()
    study = load_study(new_root)
    assert study.name == "Second try" and study.step == 5 and study.training == load_study(root).training
    assert (new_root / "harness" / "harness.yaml").is_file()
    assert [c["name"] for c in home.cases(copy)] == [c["name"] for c in home.cases(slug)]
    assert all(c["inspected"] for c in home.cases(copy)), "the inspection cache came along: nothing is read again"
    assert home.duplicate_study(slug, "") == "original-copy", "a copy without a name is named after the original"
    assert load_study(home.study_root("original-copy")).name == "Original (copy)"


def test_a_removed_case_can_be_restored_without_reading_it_again(home):
    slug = _demo_study(home)
    home.put_case(slug, "town-40.json", (DEMO / "samples" / "town-40.json").read_bytes())
    home.put_case(slug, "county-50.json", (DEMO / "samples" / "county-50.json").read_bytes())
    home.split(slug, 1)
    held = load_study(home.study_root(slug)).test
    name = held[0].replace("cases/", "")
    home.delete_case(slug, name)
    assert (home.study_root(slug) / ".trash" / "cases" / name).is_file()
    assert name not in [c["name"] for c in home.cases(slug)] and load_study(home.study_root(slug)).test == []
    home.restore_case(slug, name)
    assert (home.study_root(slug) / "cases" / name).is_file()
    study = load_study(home.study_root(slug))
    assert f"cases/{name}" in study.training and study.test == [], "restored into training"
    restored = next(c for c in home.cases(slug) if c["name"] == name)
    assert restored["inspected"] and restored["inspected"]["ok"] is True, "the inspection was kept"
    with pytest.raises(AppError) as caught:
        home.restore_case(slug, name)
    assert caught.value.status == 404
    with pytest.raises(AppError):
        home.restore_case(slug, "../x.json")


def _zip(members: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_a_study_travels_as_one_zip(home):
    import io
    import zipfile

    slug = _demo_study(home, "Travel")
    home.use_samples(slug)
    root = home.study_root(slug)
    (root / "runs" / "r1").mkdir(parents=True)
    (root / "runs" / "r1" / "job.json").write_text("{}", encoding="utf-8")
    (root / "assistant.jsonl").write_text("{}\n", encoding="utf-8")
    data = home.export_study(slug)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "study.yaml" in names and "harness/harness.yaml" in names and "cases/town-40.json" in names
    assert not any(n.startswith(("runs/", "preview/", "downloads/")) or n == "assistant.jsonl" or ".inspected" in n for n in names), names
    copy = home.import_study(data, "travel.zip")
    assert copy == "travel-2" and load_study(home.study_root(copy)).name == "Travel"
    assert [c["name"] for c in home.cases(copy)] == [c["name"] for c in home.cases(slug)]
    assert not (home.study_root(copy) / "runs").exists()

    with pytest.raises(AppError, match="study.yaml"):
        home.import_study(_zip({"harness/harness.yaml": b"id: x"}), "x.zip")
    with pytest.raises(AppError, match="outside"):
        home.import_study(_zip({"study.yaml": b"x", "../evil.txt": b"x"}), "x.zip")
    with pytest.raises(AppError, match="outside"):
        home.import_study(_zip({"study.yaml": b"x", "cases/..\\evil.txt": b"x"}), "x.zip")
    with pytest.raises(AppError, match="not a zip"):
        home.import_study(b"not a zip", "x.zip")
    with pytest.raises(AppError):
        home.import_study(_zip({"study.yaml": b"study: 1\nname: Broken\n"}), "x.zip")
    assert home.slugs() == ["travel", "travel-2"], "a refused import leaves nothing behind"
    assert not any(p.name.startswith(".") for p in home.studies_dir.iterdir() if p.name != ".trash")
