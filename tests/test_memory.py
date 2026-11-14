"""What finished studies learned (`evolvekit/harness/memory.py`), on made-up runs."""

from __future__ import annotations

import functools
import json
import math
import random
import shutil
from pathlib import Path
from statistics import fmean

import pytest
import yaml
from test_app_results import finished_run

from evolvekit.app.store import Home
from evolvekit.harness import memory
from evolvekit.harness.manifest import load_harness
from evolvekit.harness.study import load_study

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
                             "n": importance[n][1] if n in importance else 0,
                             # (value, n, p); a pair is a status item from before A4.3, without `p`
                             **({"p": importance[n][2]} if len(importance.get(n, ())) > 2 else {})}}
             for n in [*importance, *[b for b in best if b not in importance]]]
    docs[run_dir] = {"parameters": {"available": True, "items": items}}
    return root


def again(root, **kw):
    shutil.rmtree(root / "runs" / "r1" / "confirm")
    finished_run(root / "runs" / "r1", started="2026-10-01T00:00:00+00:00", **kw)


def test_importance_pools_only_studies_whose_correlation_beat_chance(tmp_path):
    """Decision 2 (A4.3, S9): a setting's strength is shown when its studies' permutation p's,
    combined by Fisher's rule, are under 0.1; it is then the mean |rho| over every study with at
    least five fully evaluated combinations, not only over those that beat chance on their own."""
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "One", decision="keep", importance={"neighbours": (0.6, 20, 0.01), "init": (0.3, 20, 0.5)},
             finished_at="2026-10-01")
    finished(home, docs, "Two", decision="keep", importance={"neighbours": (0.2, 20, 0.4), "init": (0.4, 20, 0.3)},
             finished_at="2026-10-08")
    finished(home, docs, "Three", decision="keep", importance={"neighbours": (0.9, 3, 0.001)}, finished_at="2026-10-15")
    got = memory.learned(home, "demo-tour", document_of=docs.get)
    n = got["settings"]["neighbours"]
    assert n["tuned_in"] == 3 and n["importance"] == pytest.approx(0.4) and n["importance_n"] == 2
    assert n["importance_of"] == 2, "the study of three combinations is too small to count"
    assert n["importance_p"] == pytest.approx(0.004 * (1 - math.log(0.004)))  # Fisher's rule, two studies
    init = got["settings"]["init"]
    assert init["importance"] is None and init["importance_n"] == 0 and init["importance_of"] == 2
    assert init["importance_p"] >= memory.IMPORTANCE_P == 0.1
    assert [s["name"] for s in got["studies"]] == ["One", "Two", "Three"]


def test_a_status_item_from_before_the_permutation_p_counts_as_not_shown(tmp_path):
    """Legacy split: an item without `importance.p` joins Fisher's rule as p = 1 -- it is counted, and
    it tells nothing from chance."""
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "One", decision="keep", importance={"neighbours": (0.6, 20)}, finished_at="2026-10-01")
    n = memory.learned(home, "demo-tour", document_of=docs.get)["settings"]["neighbours"]
    assert n["importance"] is None and n["importance_of"] == 1 and n["importance_p"] == pytest.approx(1.0)
    assert memory._combined_p([0.01, None]) == pytest.approx(memory._combined_p([0.01, 1.0]))
    assert memory._combined_p([]) is None


def test_a_setting_no_study_could_tell_from_chance_is_not_reranked(tmp_path):
    """Three studies, the template tunes `recommended`: restart_after went with the measure most
    strongly (0.9) but never beat chance, so it keeps the harness's rank order behind the settings
    with a strength."""
    home, docs = Home(tmp_path / "my home"), {}
    shown = {"accept_worse": (0.7, 20, 0.01), "init": (0.6, 20, 0.02), "neighbours": (0.5, 20, 0.03),
             "restart_after": (0.9, 20, 0.6)}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12}, importance=shown, finished_at="2026-10-01")
    finished(home, docs, "Summer", decision="keep", importance=shown, finished_at="2026-10-08")
    finished(home, docs, "Autumn", decision="keep", importance=shown, finished_at="2026-10-15")
    facts = memory.learned(home, "demo-tour", document_of=docs.get)["settings"]
    assert facts["restart_after"]["importance"] is None and facts["restart_after"]["importance_of"] == 3
    slug = home.create_study("demo-tour", "tune", "Winter")
    winter = home.study_root(slug)
    warmed, _ = memory.warm_start(home, load_harness(winter / "harness"), load_study(winter), document_of=docs.get)
    assert warmed.warm.ranked == ["accept_worse", "init", "neighbours", "restart_after"]


@functools.cache
def _null_studies(trials: int = 2000, studies: int = 3, rows: int = 8) -> tuple:
    """`trials` sets of `studies` null studies of `rows` ranked rows (independent value and score),
    each as (|rho|, p) through `status._permutation_p`; random.Random(20261114), the draws of the
    builder check's `sim_perm.py pool` (rho = 0, three studies)."""
    from evolvekit import stats, status

    rng = random.Random(20261114)
    out = []
    for _ in range(trials):
        one = []
        for _ in range(studies):
            xs = [rng.random() for _ in range(rows)]
            ys = [rng.gauss(0.0, 1.0) for _ in range(rows)]
            one.append((abs(stats.spearman(xs, ys)), status._permutation_p(xs, ys)))
        out.append(tuple(one))
    return tuple(out)


def test_a_setting_that_does_nothing_is_shown_in_about_8_percent_after_three_studies():
    """The product's rule (decision 2): three null studies of 8 rows each through status._permutation_p
    and `memory._pooled` (Fisher's combined p < 0.1); 2000 seeded trials. Measured 8.50 % +- 0.62, the
    builder check's figure to the trial; asserts [6.0, 11.0] %."""
    trials = _null_studies()
    shown = sum(memory._pooled([(r, 20, p) for r, p in one])["importance"] is not None for one in trials)
    rate = shown / len(trials)
    print(f"null setting shown after three studies (Fisher): {rate:.2%} +- {math.sqrt(rate * (1 - rate) / len(trials)):.2%}")
    assert 0.06 <= rate <= 0.11


def test_under_the_designs_rule_a_setting_that_does_nothing_would_be_shown_in_about_26_percent_after_three_studies():
    """The design's rule, kept as a measured comparison (decision 2 departs from it): pool |rho| over
    the studies whose own p < 0.1. Same 2000 seeded trials as the Fisher test. Measured
    25.65 % +- 0.98 (the builder check's figure), the pooled |rho| when shown 0.75 (a real rho = 0.5
    setting's is 0.77); asserts [23, 30] %."""
    trials = _null_studies()
    pooled = [[r for r, p in one if p < memory.IMPORTANCE_P] for one in trials]
    shown = [fmean(rs) for rs in pooled if rs]
    rate = len(shown) / len(trials)
    print(f"null setting shown after three studies (design's rule): {rate:.2%} +- "
          f"{math.sqrt(rate * (1 - rate) / len(trials)):.2%}; pooled |rho| when shown {fmean(shown):.2f}")
    assert 0.23 <= rate <= 0.30
    assert fmean(shown) > 0.6, "what it would rank: noise as strong as a real effect"


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


def test_a_study_whose_search_did_not_run_is_listed_and_adds_up_nothing(tmp_path):
    """Persona p5 fix round 1, I2: a run that stopped before its first round "found" the starting point; it
    stays in the studies with its line, and is no study that tuned, ranked or leaned anywhere."""
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "One", decision="keep", best={"neighbours": 4}, importance={"neighbours": (0.6, 20, 0.01)},
             finished_at="2026-10-01")
    root = finished(home, docs, "Idle", decision="keep", best={"neighbours": 8}, importance={"neighbours": (0.1, 20)},
                    finished_at="2026-10-08")
    job_path = root / "runs" / "r1" / "job.json"
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job["search"] = {"generations": 0, "aborted": False, "stop_reason": "time limit: …"}
    job["final"] = {"skipped": "the search did not run: there was not enough time left for its first round",
                    "not_run": True}
    job_path.write_text(json.dumps(job), encoding="utf-8")
    got = memory.learned(home, "demo-tour", document_of=docs.get)
    idle = next(s for s in got["studies"] if s["name"] == "Idle")
    assert idle["line"].startswith("The search did not run") and idle["not_run"] is True
    n = got["settings"]["neighbours"]
    assert n["tuned_in"] == 1 and n["importance"] == pytest.approx(0.6) and n["leaned"] == 4
