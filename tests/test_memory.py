"""What finished studies learned (`evolvekit/harness/memory.py`), on made-up runs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from test_app_results import finished_run

from evolvekit.app.store import Home
from evolvekit.harness import memory

STARTS = {"neighbours": 8, "restart_after": 2000, "accept_worse": 0.0, "init": "random"}


def finished(home, docs, name, *, decision, best=None, importance=None, finished_at, start=None, template="tune",
             run="r1", mean=4.2) -> Path:
    """A study of the demo tour with one finished run; `docs` gets the run's status document."""
    slug = home.create_study("demo-tour", template, name)
    root = home.studies_dir / slug
    run_dir = root / "runs" / run
    finished_run(run_dir, decision=decision, mean=mean, started=f"{finished_at}T00:00:00+00:00")
    best, importance, start = best or {}, importance or {}, {**STARTS, **(start or {})}
    rows = [json.loads(line) for line in (run_dir / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    rows[1]["params"] = {**rows[1]["params"], **best}
    (run_dir / "runs.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    items = [{"name": n, "default": start.get(n), "best": best.get(n),
              "importance": {"value": importance[n][0] if n in importance else None,
                             "n": importance[n][1] if n in importance else 0}}
             for n in [*importance, *[b for b in best if b not in importance]]]
    docs[run_dir] = {"parameters": {"available": True, "items": items}}
    return root


def again(root, **kw):
    shutil.rmtree(root / "runs" / "r1" / "confirm")
    finished_run(root / "runs" / "r1", started="2026-10-01T00:00:00+00:00", **kw)


def test_learned_counts_and_importance_mean(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "One", decision="keep", importance={"neighbours": (0.6, 20)}, finished_at="2026-10-01")
    finished(home, docs, "Two", decision="keep", importance={"neighbours": (0.2, 20)}, finished_at="2026-10-08")
    finished(home, docs, "Three", decision="keep", importance={"neighbours": (0.9, 3)}, finished_at="2026-10-15")
    got = memory.learned(home, "demo-tour", document_of=docs.get)
    n = got["settings"]["neighbours"]
    assert n["tuned_in"] == 3 and n["importance"] == pytest.approx(0.4) and n["importance_n"] == 2
    assert [s["name"] for s in got["studies"]] == ["One", "Two", "Three"]


def test_winners_only_from_use_studies(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12}, finished_at="2026-10-01")
    finished(home, docs, "Summer", decision="keep", best={"neighbours": 20}, finished_at="2026-10-08")
    n = memory.learned(home, "demo-tour", document_of=docs.get)["settings"]["neighbours"]
    assert n["winners"] == [{"value": 12, "study": "spring", "finished_at": "2026-10-01T00:12:00+00:00"}]
    assert n["direction"] == "up", "from the starting value 8"


def test_direction_mixed_and_none(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12, "init": "nearest"}, finished_at="2026-10-01")
    finished(home, docs, "Summer", decision="use", best={"neighbours": 5, "init": "random"}, finished_at="2026-10-08")
    got = memory.learned(home, "demo-tour", document_of=docs.get)["settings"]
    assert got["neighbours"]["direction"] == "mixed"
    assert got["init"]["direction"] is None and len(got["init"]["winners"]) == 2


def test_learned_says_where_the_searches_leaned(tmp_path):
    """Persona p4, H4: after three studies with no winner the planner had nothing to try. `leaned` is
    the median of the settings studies' search-best values (the most common value of a choice; none on
    a tie), whatever their verdicts; a code study's best never counts."""
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "One", decision="keep", best={"neighbours": 4, "init": "nearest", "accept_worse": 0.02},
             finished_at="2026-10-01")
    finished(home, docs, "Two", decision="keep", best={"neighbours": 12, "init": "nearest", "accept_worse": 0.04},
             finished_at="2026-10-08")
    finished(home, docs, "Three", decision="use", best={"neighbours": 5, "init": "random"}, finished_at="2026-10-15")
    finished(home, docs, "Code", decision="keep", best={"neighbours": 30, "init": "random"}, finished_at="2026-10-20",
             template="faster")
    got = memory.learned(home, "demo-tour", document_of=docs.get)["settings"]
    assert got["neighbours"]["leaned"] == 5
    assert got["init"]["leaned"] == "nearest"
    assert got["accept_worse"]["leaned"] == pytest.approx(0.03)


def test_learned_leans_nowhere_on_a_tie_or_with_no_best(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "One", decision="keep", best={"neighbours": 4, "init": "nearest"},
             importance={"restart_after": (0.3, 20)}, finished_at="2026-10-01")
    finished(home, docs, "Two", decision="keep", best={"neighbours": 7, "init": "random"}, finished_at="2026-10-08")
    got = memory.learned(home, "demo-tour", document_of=docs.get)["settings"]
    assert got["init"]["leaned"] is None, "one each way: no lean"
    assert got["neighbours"]["leaned"] == 4, "a whole-number setting: the lower middle of an even count"
    assert got["restart_after"]["leaned"] is None, "no search-best value"


def test_learned_ignores_settings_the_harness_no_longer_has(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    root = finished(home, docs, "Depot spring", decision="use", best={"neighbours": 12, "old_knob": 3}, finished_at="2026-10-01")
    pinned = yaml.safe_load((root / "harness" / "harness.yaml").read_text(encoding="utf-8"))
    pinned["version"] = "1.0.0"
    pinned["settings"]["old_knob"] = {"type": "int", "low": 1, "high": 9, "default": 2, "label": "Old knob"}
    pinned["settings"]["neighbours"]["high"] = 20
    (root / "harness" / "harness.yaml").write_text(yaml.safe_dump(pinned, sort_keys=False), encoding="utf-8")
    got = memory.learned(home, "demo-tour", document_of=docs.get)
    assert "old_knob" not in got["settings"]
    assert got["settings"]["neighbours"]["winners"] == [] and got["settings"]["neighbours"]["tuned_in"] == 0
    assert got["notes"] == ["old_knob (in Depot spring) is not a setting of Demo tour 1.1.0 any more: left out",
                            "neighbours: its range changed since Depot spring (2–20, now 2–30): that study's figures are left out"]


def test_learned_lists_code_studies_and_bakeoffs_without_settings_facts(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Faster", decision="use", best={"neighbours": 30}, finished_at="2026-10-01", template="faster")
    root = finished(home, docs, "Branches", decision="keep", best={"neighbours": 25}, finished_at="2026-10-08")
    job = json.loads((root / "runs" / "r1" / "job.json").read_text(encoding="utf-8"))
    job["bakeoff"] = [{"ref": "a"}]
    (root / "runs" / "r1" / "job.json").write_text(json.dumps(job), encoding="utf-8")
    got = memory.learned(home, "demo-tour", document_of=docs.get)
    assert [(s["name"], s["kind"]) for s in got["studies"]] == [("Faster", "code"), ("Branches", "bakeoff")]
    assert got["studies"][0]["decision"] == "use" and got["studies"][1]["decision"] != "use"
    assert got["studies"][0]["line"]
    assert got["settings"] == {}


def test_learned_takes_each_studys_newest_done_run(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    root = finished(home, docs, "Tours", decision="use", best={"neighbours": 12}, finished_at="2026-10-01", run="r1")
    for run, decision, day in (("r2", "keep", "2026-10-02"), ("r3", "use", "2026-10-03")):
        finished_run(root / "runs" / run, decision=decision, started=f"{day}T00:00:00+00:00")
        docs[root / "runs" / run] = docs[root / "runs" / "r1"]
    job = json.loads((root / "runs" / "r3" / "job.json").read_text(encoding="utf-8"))
    job["phase"] = "stopped"
    (root / "runs" / "r3" / "job.json").write_text(json.dumps(job), encoding="utf-8")
    got = memory.learned(home, "demo-tour", document_of=docs.get)
    assert [(s["run"], s["decision"]) for s in got["studies"]] == [("r2", "keep")]
    assert got["settings"]["neighbours"]["winners"] == []


def test_learned_empty_home(tmp_path):
    assert memory.learned(Home(tmp_path / "my home"), "demo-tour") == {"studies": [], "settings": {}, "notes": []}


def test_archived_included_and_trash_excluded(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    kept = finished(home, docs, "Kept", decision="keep", importance={"neighbours": (0.5, 20)}, finished_at="2026-10-01")
    gone = finished(home, docs, "Gone", decision="keep", importance={"neighbours": (0.5, 20)}, finished_at="2026-10-02")
    home.set_archived(kept.name, True)
    home.delete_study(gone.name)
    got = memory.learned(home, "demo-tour", document_of=docs.get)
    assert [s["name"] for s in got["studies"]] == ["Kept"]


def test_damaged_run_folder_is_skipped(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Fine", decision="keep", importance={"neighbours": (0.5, 20)}, finished_at="2026-10-01")
    root = finished(home, docs, "Broken", decision="keep", finished_at="2026-10-02")
    (root / "runs" / "r1" / "job.json").write_text("{ garbage", encoding="utf-8")
    got = memory.learned(home, "demo-tour", document_of=docs.get)
    assert [s["name"] for s in got["studies"]] == ["Fine"]


def test_words_follow_a_changed_run_and_a_failed_read_is_not_kept(tmp_path, monkeypatch):
    from evolvekit.app import verdict

    home, docs = Home(tmp_path / "my home"), {}
    root = finished(home, docs, "Tours", decision="use", finished_at="2026-10-01")
    assert memory.learned(home, "demo-tour", document_of=docs.get)["studies"][0]["decision"] == "use"
    again(root, decision="keep")
    assert memory.learned(home, "demo-tour", document_of=docs.get)["studies"][0]["decision"] == "keep"
    real = verdict.build_verdict
    monkeypatch.setattr(verdict, "build_verdict", lambda *a, **k: (_ for _ in ()).throw(OSError("locked")))
    again(root, decision="use", mean=7.0)
    assert memory.learned(home, "demo-tour", document_of=docs.get)["studies"][0]["decision"] == ""
    monkeypatch.setattr(verdict, "build_verdict", real)
    assert memory.learned(home, "demo-tour", document_of=docs.get)["studies"][0]["decision"] == "use"


@pytest.mark.parametrize("edit, note", [
    (lambda s: s["neighbours"].update(type="float"),
     "neighbours: its type changed since Old (float, now int): that study's figures are left out"),
    (lambda s: s["restart_after"].update(log=False),
     "restart_after: its scale changed since Old (linear, now log): that study's figures are left out"),
    (lambda s: s["init"].update(choices=["random", "nearest", "greedy"]),
     "init: its choices changed since Old (random, nearest, greedy, now random, nearest): that study's figures are left out"),
])
def test_type_scale_and_choice_changes_leave_the_figures_out(tmp_path, edit, note):
    home, docs = Home(tmp_path / "my home"), {}
    root = finished(home, docs, "Old", decision="use", finished_at="2026-10-01",
                    best={"neighbours": 12, "restart_after": 5000, "init": "random"})
    pinned = yaml.safe_load((root / "harness" / "harness.yaml").read_text(encoding="utf-8"))
    edit(pinned["settings"])
    (root / "harness" / "harness.yaml").write_text(yaml.safe_dump(pinned, sort_keys=False), encoding="utf-8")
    got = memory.learned(home, "demo-tour", document_of=docs.get)
    assert got["notes"] == [note]


@pytest.mark.parametrize("decision, mean", [("single", 4.2), ("worse", -3.0), ("keep", 0.0)])
def test_no_winner_unless_the_verdict_says_use(tmp_path, decision, mean):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Tours", decision=decision, best={"neighbours": 12}, finished_at="2026-10-01", mean=mean)
    got = memory.learned(home, "demo-tour", document_of=docs.get)
    assert got["studies"][0]["decision"] != "use"
    assert got["settings"]["neighbours"]["winners"] == []


def test_data_changes_in_the_status_items_are_not_settings(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    root = finished(home, docs, "Both", decision="use", best={"neighbours": 12, "stop_weight": 1.5}, finished_at="2026-10-01")
    study = yaml.safe_load((root / "study.yaml").read_text(encoding="utf-8"))
    study.setdefault("vary", {})["data"] = {"stop_weight": {"lever": "weights", "mode": "scale", "low": 1, "high": 2, "start": 1}}
    (root / "study.yaml").write_text(yaml.safe_dump(study, sort_keys=False), encoding="utf-8")
    shutil_copy = root / "runs" / "r1" / "study.yaml"
    shutil_copy.write_text(yaml.safe_dump(study, sort_keys=False), encoding="utf-8")
    got = memory.learned(home, "demo-tour", document_of=docs.get)
    assert "stop_weight" not in got["settings"] and got["notes"] == []
