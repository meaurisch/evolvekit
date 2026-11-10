"""Settings per kind of case (`per_group: true`, design §6.4): one search and
one final check per group folder (`runs/<id>/groups/<name>/`), in sequence
under the study's time and AI cap split by the groups' cases; the verdict's
`groups` and its top-level rule; `settings_by_group.json` and `rule.md`; the
report's "Per kind of case"; a stop that reaches every group; and no resume,
continuation or second check of such a run (D37). The runs here are written by
hand except in the slow test."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from evolvekit.app import AppError, jobs
from evolvekit.app.report import render_report
from evolvekit.app.verdict import build_verdict
from evolvekit.harness import execute, groups
from evolvekit.harness.compile import CONFIG, write_run
from evolvekit.harness.manifest import load_harness
from evolvekit.harness.plan import Plan
from evolvekit.harness.study import Budget, CodeVary, load_study, save_study, study_problems
from evolvekit.stopping import STOP_REQUEST
from test_app_results import document_of, finished_run
from test_groups import fake_tables, study_with_cases

ROOT = Path(__file__).resolve().parents[1]
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"


def per_group_study(tmp_path: Path, sizes: list[int] | None = None, held: int = 4,
                    tags: dict[str, int] | None = None) -> Path:
    """A demo-tour study with cases `cases/c<i>.json` (`{"seed": i, "cities": size}`), the last
    `held` held back, `per_group: true`; with `tags` ({"small": 4, "large": 4}) the first four
    cases tagged `small` and the next four `large`."""
    sizes = [20, 22, 24, 26, 80, 82, 84, 86] if sizes is None else sizes
    root = study_with_cases(tmp_path, sizes, held)
    study = load_study(root)
    save_study(replace(study, per_group=True), root)
    cases = list(study.training) + list(study.test)
    at = 0
    for tag, count in (tags or {}).items():
        for case in cases[at:at + count]:
            groups.write_tag(root, case, tag)
        at += count
    return root


def _check(folder: Path, decision: str, cases: list[str]) -> None:
    """The final check of a hand-written run over `cases`: the best 4.2 % better (-4.2 % for `worse`)."""
    mean = -4.2 if decision == "worse" else 0.3 if decision == "keep" else 4.2
    check = folder / "confirm" / "final"
    (check / "comparison.json").write_text(json.dumps({
        "seeds": [1001, 1002, 1003], "instances": cases,
        "per_candidate": {"c1": {"summary": {"n": len(cases), "mean": mean, "ci95": [mean - 1.2, mean + 1.2],
                                             "chance": 0.97 if decision == "use" else 0.5, "worst": mean - 1.2}}},
    }), encoding="utf-8")
    rows = []
    for case in cases:
        for seed in (1001, 1002, 1003):
            rows.append({"configuration": "baseline", "instance": case, "seed": seed, "ok": True,
                         "kpis": {"tour_length": 50.0, "longest_leg": 3.0}})
            rows.append({"configuration": "c1", "instance": case, "seed": seed, "ok": True,
                         "kpis": {"tour_length": 50.0 * (1 - mean / 100), "longest_leg": 3.1}})
    (check / "results.json").write_text(json.dumps(rows), encoding="utf-8")


def per_group_run(tmp_path: Path, spec: dict[str, tuple[str, int]]) -> tuple[Path, Path]:
    """A finished per-group run `r1` written by hand: four cases to learn from per group plus
    `held` held back, tagged by the group's name; `groups.json` by tags; the run folder's
    `job.json` with `per_group`; in each group folder its `study.yaml` and a finished run
    (`test_app_results.finished_run`) whose final check says `decision` over the held-back cases."""
    sizes, tags, held_names = [], {}, []
    for index, (name, (_decision, held)) in enumerate(spec.items()):
        sizes += [20 + 60 * index] * (4 + held)
    root = study_with_cases(tmp_path, sizes, 0)
    study = load_study(root)
    cases = list(study.training)
    at, training, test = 0, [], []
    for name, (_decision, held) in spec.items():
        own = cases[at:at + 4 + held]
        at += 4 + held
        for case in own:
            groups.write_tag(root, case, name)
            tags[case] = name
        training += own[:4]
        test += own[4:]
        held_names += own[4:]
    save_study(replace(study, training=training, test=test, per_group=True), root)
    doc = groups.compute(root, tables=fake_tables({c: n for c, n in zip(cases, sizes)}))
    assert doc["method"] == "tags", doc
    run_dir = root / "runs" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "study.yaml").write_bytes((root / "study.yaml").read_bytes())
    (run_dir / groups.GROUPS).write_bytes((root / groups.GROUPS).read_bytes())
    order = list(doc["groups"])
    job = {"phase": "done", "pid": 2 ** 22 + 7, "started_at": "2026-01-01T00:00:00+00:00",
           "finished_at": "2026-01-01T00:30:00+00:00",
           "per_group": {"order": order, "current": order[-1], "dirs": {n: f"groups/{groups.slug_of(n)}" for n in order}},
           "plan": {"hours": 0.5, "groups": {}}}
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    full = load_study(root)
    for name in order:
        part = doc["groups"][name]
        folder = run_dir / "groups" / groups.slug_of(name)
        decision = spec[name][0]
        finished_run(folder, decision=decision, held=part["test"][0])
        save_study(replace(full, training=part["training"], test=part["test"], per_group=False), folder)
        _check(folder, decision, part["test"])
    return root, run_dir


# ---------------------------------------------------------------------------
# the study key, the compile and the budget
# ---------------------------------------------------------------------------


def test_per_group_compiles_one_run_per_group(tmp_path):
    root = per_group_study(tmp_path, held=2, tags={"small": 4, "large": 4})
    study = load_study(root)
    doc = groups.compute(root, tables=fake_tables({c: 20 for c in study.training + study.test}))
    assert set(doc["groups"]) == {"small", "large"}
    for name, part in doc["groups"].items():
        study_g = replace(load_study(root), training=part["training"], test=part["test"])
        folder = write_run(study_g, load_harness(root / "harness"), Plan.fixed(), root, "r1", group=name)
        assert folder == root / "runs" / "r1" / "groups" / name
        config = yaml.safe_load((folder / CONFIG).read_text(encoding="utf-8"))
        full = config["evaluate"]["stages"][-1]
        assert full["instances"] == [f"../../../../{c}" for c in part["training"]]
        assert "../../../../harness/runner.py" in full["command"]
        assert load_study(folder).training == part["training"]


def test_per_group_is_written_only_when_true(tmp_path):
    root = per_group_study(tmp_path)
    assert load_study(root).per_group is True
    assert "per_group: true" in (root / "study.yaml").read_text(encoding="utf-8")
    save_study(replace(load_study(root), per_group=False), root)
    assert "per_group" not in (root / "study.yaml").read_text(encoding="utf-8")
    assert load_study(root).per_group is False


def test_per_group_budget_split(tmp_path):
    study = replace(load_study(per_group_study(tmp_path, sizes=[20] * 6, held=0)),
                    budget=Budget(hours=1.0, ai_enabled=True, ai_max_usd=1.0))
    split = execute.split_budget(study, {"a": {"training": ["1", "2", "3", "4"], "test": []},
                                         "b": {"training": ["5", "6"], "test": []}})
    assert split == {"a": (0.6666, 0.6666), "b": (0.3333, 0.3333)}
    assert sum(h for h, _ in split.values()) <= 1.0 and sum(u for _, u in split.values()) <= 1.0
    held = execute.split_budget(replace(study, budget=Budget(hours=2.0, ai_enabled=False, ai_max_usd=3.0)),
                                {"a": {"training": ["1", "2"], "test": ["3"]}, "b": {"training": ["4", "5"], "test": []}})
    assert held == {"a": (1.2, 1.8), "b": (0.8, 1.2)}, "held-back cases count too"


def test_per_group_refused_with_code_or_schedule(tmp_path):
    from evolvekit.harness.schedule import PRESETS, Schedule

    root = per_group_study(tmp_path)
    study, harness = load_study(root), load_harness(root / "harness")
    assert not [p for p in study_problems(study, harness, root=root) if p.startswith("per_group")]
    coded = replace(study, code=CodeVary(repo=str(tmp_path), brief="faster"))
    assert ("per_group: tunes settings per kind of case; a code study changes one source for every case"
            in study_problems(coded, harness, root=root))
    scheduled = replace(study, schedule=Schedule.parse(PRESETS["weekends"]["schedule"]))
    assert ("per_group: a campaign runs one search; leave out schedule, or per_group, for now"
            in study_problems(scheduled, harness, root=root))
    untuned = replace(study, settings={})
    assert "per_group: tune at least one setting" in study_problems(untuned, harness, root=root)


def test_a_campaign_of_a_per_group_study_is_refused_before_it_is_armed(tmp_path):
    from evolvekit.app import scheduler
    from evolvekit.app.store import Home
    from evolvekit.harness.schedule import PRESETS, Schedule

    root = per_group_study(tmp_path)
    save_study(replace(load_study(root), schedule=Schedule.parse(PRESETS["weekends"]["schedule"])), root)
    with pytest.raises(AppError, match="a campaign runs one search"):
        scheduler.start_campaign(Home(tmp_path / "home"), root, now=jobs._now(), at_once=False)  # noqa: SLF001
    assert not (root / scheduler.CAMPAIGN).exists()


def test_per_group_is_a_key_the_app_saves(tmp_path):
    from evolvekit.app.store import Home

    home = Home(tmp_path / "home")
    slug = home.create_study("demo-tour", "tune", "Kinds")
    home.save_step(slug, {"per_group": True})
    assert load_study(home.study_root(slug)).per_group is True
    home.save_step(slug, {"per_group": None})
    assert load_study(home.study_root(slug)).per_group is False


# ---------------------------------------------------------------------------
# the verdict, the exports and the report
# ---------------------------------------------------------------------------


def test_per_group_verdict_requires_every_group(tmp_path):
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("keep", 2)})   # (decision, held-back cases)
    doc = build_verdict(root, "r1")
    assert doc["decision"] == "keep"
    assert doc["line"] == "Per kind of case: not every group is shown to beat today's settings (large: keep)."
    assert doc["advice"] == "Keep today's settings, or use only the groups whose own line says use."
    assert set(doc["groups"]) == {"small", "large"} and "groups" not in doc["groups"]["small"]
    assert "mean_pct" not in doc and {r["group"] for r in doc["by_case"]} == {"small", "large"}
    assert doc["held_back"]["cases"] == 4 and doc["groups"]["small"]["decision"] == "use"
    assert doc["groups"]["small"]["run"] == "r1" and doc["groups"]["small"]["mean_pct"] is not None
    root, _ = per_group_run(tmp_path / "b", {"small": ("use", 2), "large": ("use", 2)})
    both = build_verdict(root, "r1")
    assert both["decision"] == "use"
    assert both["line"] == "Per kind of case: each group checked on two or more held-back cases is probably better."
    assert both["advice"] == ("Use each group's settings: settings_by_group.json has them, "
                              "rule.md says which cases get which.")


def test_per_group_single_case_group(tmp_path):
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("single", 1)})
    doc = build_verdict(root, "r1")
    assert doc["decision"] == "use" and doc["groups"]["large"]["decision"] == "single"
    execute.write_group_exports(root, run_dir, doc)
    settings = json.loads((run_dir / "downloads" / "settings_by_group.json").read_text(encoding="utf-8"))
    assert settings["groups"]["large"]["from"] == "today" and settings["groups"]["small"]["from"] == "found"
    assert settings["method"] == "tags" and settings["groups"]["small"]["decision"] == "use"
    assert settings["groups"]["small"]["settings"]["restart_after"] == 13000, "the best's values"
    assert settings["groups"]["large"]["settings"]["restart_after"] != 13000, "today's settings"
    rule = (run_dir / "downloads" / "rule.md").read_text(encoding="utf-8")
    assert "For cases tagged large, use today's settings (large: not proven, one held-back case)." in rule
    assert "For cases tagged small, use the settings of small." in rule
    assert build_verdict(root, "r1")["files"] == ["runs/r1/downloads/rule.md", "runs/r1/downloads/settings_by_group.json"]


def test_no_group_with_two_held_back_cases_is_single(tmp_path):
    root, _ = per_group_run(tmp_path, {"small": ("single", 1), "large": ("single", 1)})
    doc = build_verdict(root, "r1")
    assert doc["decision"] == "single"
    assert doc["line"] == "Per kind of case: no group has two or more held-back cases, so none is proven."


def test_a_group_not_yet_checked_is_pending(tmp_path):
    import shutil

    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("use", 2)})
    shutil.rmtree(run_dir / "groups" / "small")
    doc = build_verdict(root, "r1")
    assert doc["decision"] == "pending" and doc["groups"]["small"]["decision"] == "pending"
    assert doc["line"] == "Per kind of case: the final check of small has not finished."


def test_rule_md_claims_nothing_for_groups_not_use(tmp_path):
    root, run_dir = per_group_run(tmp_path, {"small": ("keep", 2), "large": ("worse", 2)})
    execute.write_group_exports(root, run_dir, build_verdict(root, "r1"))
    rule = (run_dir / "downloads" / "rule.md").read_text(encoding="utf-8")
    assert "better" not in rule.lower() and rule.count("today's settings") == 2
    assert "(small: not shown to beat today's)" in rule
    settings = json.loads((run_dir / "downloads" / "settings_by_group.json").read_text(encoding="utf-8"))
    assert {g["from"] for g in settings["groups"].values()} == {"today"}


def test_rule_stump_picks_best_split():
    features = {f"c{i}": {"rows_stops": float(n), "mean_stops_weight": 1.0} for i, n in enumerate([30, 40, 80, 90])}
    labels = {"c0": "group 1", "c1": "group 1", "c2": "group 2", "c3": "group 2"}
    assert groups.rule_stump(features, labels) == {"feature": "rows_stops", "thresholds": [60.0],
                                                   "sides": ["group 1", "group 2"], "misplaced": 0}


def test_rule_stump_ties_and_three_groups():
    # Two features split the groups cleanly: the widest gap wins.
    features = {"a": {"f": 1.0, "g": 0.0}, "b": {"f": 2.0, "g": 1.0}, "c": {"f": 3.0, "g": 9.0}, "d": {"f": 4.0, "g": 10.0}}
    labels = {"a": "low", "b": "low", "c": "high", "d": "high"}
    assert groups.rule_stump(features, labels) == {"feature": "g", "thresholds": [5.0], "sides": ["low", "high"],
                                                   "misplaced": 0}
    # One case on the wrong side is said.
    labels = {"a": "low", "b": "high", "c": "high", "d": "high"}
    labels["c"] = "low"
    stump = groups.rule_stump({k: {"f": v["f"]} for k, v in features.items()}, labels)
    assert stump["misplaced"] == 1
    three = {f"c{i}": {"n": float(v)} for i, v in enumerate([10, 12, 50, 52, 90, 92])}
    named = {"c0": "a", "c1": "a", "c2": "b", "c3": "b", "c4": "c", "c5": "c"}
    assert groups.rule_stump(three, named) == {"feature": "n", "thresholds": [31.0, 71.0], "sides": ["a", "b", "c"],
                                               "misplaced": 0}


def test_rule_lines_by_features():
    doc = {"method": "features", "describe": {"rows_stops": "the number of rows in stops"},
           "features": {"c0": {"rows_stops": 20.0}, "c1": {"rows_stops": 22.0}, "c2": {"rows_stops": 80.0},
                        "c3": {"rows_stops": 82.0}, "c4": {"rows_stops": 30.0}},
           "groups": {"group 1": {"training": ["c0", "c1"], "test": ["c4"]}, "group 2": {"training": ["c2", "c3"], "test": []}}}
    lines = groups.rule_lines(doc, {"group 1": "use", "group 2": "use"})
    assert lines[0] == ("If the number of rows in stops is above 51, use the settings of group 2; "
                        "otherwise those of group 1.")
    lines = groups.rule_lines(doc, {"group 1": "keep", "group 2": "use"})
    assert lines[0] == ("If the number of rows in stops is above 51, use the settings of group 2; "
                        "otherwise today's settings (group 1: not shown to beat today's).")
    three = {**doc, "features": {**doc["features"], "c5": {"rows_stops": 140.0}, "c6": {"rows_stops": 142.0}},
             "groups": {**doc["groups"], "group 3": {"training": ["c5", "c6"], "test": []}}}
    lines = groups.rule_lines(three, {"group 1": "use", "group 2": "single", "group 3": "use"})
    assert lines[0] == ("If the number of rows in stops is at most 51, use the settings of group 1; if it is above 111, "
                        "those of group 3; otherwise today's settings (group 2: not proven, one held-back case).")
    crossed = {**doc, "features": {**doc["features"], "c1": {"rows_stops": 81.0}}}
    lines = groups.rule_lines(crossed, {"group 1": "use", "group 2": "use"})
    assert "The rule puts 1 of the 4 cases to learn from in another group than its own." in lines


def test_the_report_has_per_kind_of_case(tmp_path):
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("keep", 2)})
    doc = build_verdict(root, "r1")
    report = render_report(doc, jobs.results(root, "r1", document_of), root=root)
    assert "## Per kind of case" in report
    assert doc["line"] in report and doc["groups"]["small"]["line"] in report
    assert "For cases tagged small, use the settings of small." in report
    assert "For cases tagged large, use today's settings (large: not shown to beat today's)." in report


def test_results_and_status_read_a_group(tmp_path):
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("keep", 2)})
    first = jobs.results(root, "r1", document_of)
    assert first["run"] == "r1" and first["per_group"]["group"] == "large", "a finished run: its first group"
    small = jobs.results(root, "r1", document_of, group="small")
    assert small["per_group"]["group"] == "small" and small["final"]["decision"] == "use"
    assert small["per_group"]["decision"] == "keep" and small["final"]["line"] != small["per_group"]["line"]
    assert [g["name"] for g in small["per_group"]["groups"]] == ["large", "small"]
    assert {r["group"] for r in small["per_group"]["by_case"]} == {"small", "large"}
    with pytest.raises(AppError):
        jobs.results(root, "r1", document_of, group="medium")
    status = jobs.status(root, "r1", document_of, group="small")
    assert status["run"] == "r1" and status["phase"] == "done" and status["group"]["name"] == "small"
    assert status["can_check"] is False and status["can_resume"] is False and status["can_continue"] is False
    card = jobs.run_line(root, load_study(root), run_dir)
    assert card["kind"] == "finished" and card["decision"] == "keep"
    names = [d["name"] for d in jobs.results(root, "r1", document_of)["downloads"]]
    assert "settings_by_group" in names and "rule" in names
    body, content_type, filename = jobs.download(root, "r1", "rule", document_of)
    assert b"For cases tagged small" in body and filename.endswith("rule.md")


def test_group_dirs(tmp_path):
    _, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("keep", 2)})
    assert execute.group_dirs(run_dir) == {"large": run_dir / "groups" / "large", "small": run_dir / "groups" / "small"}
    assert execute.group_dirs(run_dir.parent / "nothing") == {}


def test_stop_reaches_the_current_group(tmp_path):
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("keep", 2)})
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    job.update({"phase": "search", "pid": os.getpid(), "started_at": jobs._now().isoformat()})  # noqa: SLF001
    job.pop("finished_at")
    job["per_group"]["current"] = "large"
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    assert jobs.stop(root, "r1") == {"stopping": True, "run": "r1"}
    for folder in (run_dir, run_dir / "groups" / "small", run_dir / "groups" / "large"):
        assert (folder / STOP_REQUEST).is_file(), folder
    # Ended with a search on record and the files a resume or a continuation reads: still neither (D37).
    (run_dir / "runs.jsonl").write_text("{}\n", encoding="utf-8")
    (run_dir / CONFIG).write_text("{}\n", encoding="utf-8")
    ended = {**job, "phase": "stopped", "search": {"generations": 1, "aborted": False}, "plan": {"generations": 4}}
    assert jobs.can_resume(run_dir, ended) is False and jobs.can_continue(run_dir, ended) is False
    with pytest.raises(execute.HarnessError, match="per kind of case"):
        execute.final_check(root, "r1", log=lambda line: None)


def test_a_per_group_run_stopped_before_it_began(tmp_path):
    root = per_group_study(tmp_path, held=2, tags={"small": 4, "large": 4})
    run_dir = root / "runs" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / STOP_REQUEST).write_text("stop\n", encoding="utf-8")
    state = execute.run_study(root, "r1", log=lambda line: None)
    assert state["phase"] == "stopped" and state["per_group"]["order"] == []
    # With a groups.json in the study, the verdict still names no group the run never made.
    study = load_study(root)
    groups.compute(root, tables=fake_tables({c: 20 for c in study.training + study.test}))
    doc = build_verdict(root, "r1")
    assert doc["decision"] == "stopped" and doc["groups"] == {} and doc["rule_lines"] == []
    assert doc["line"] == "Per kind of case: the run stopped before any group began."


# ---------------------------------------------------------------------------
# fix round 1: the verdict is the only claim on every surface, and the minors
# ---------------------------------------------------------------------------


def _home(root: Path):
    from evolvekit.app.store import Home

    return Home(root.parent.parent)


def test_compare_refuses_a_per_group_run(tmp_path):
    from evolvekit.app import compare

    root, _ = per_group_run(tmp_path, {"small": ("use", 2), "large": ("keep", 2)})
    with pytest.raises(AppError, match="a run tuned per kind of case is compared group by group on its own page") as err:
        compare.compare(_home(root), f"{root.name}/r1", f"{root.name}/r1", document_of)
    assert err.value.status == 409
    listed = jobs.runs(root)
    assert listed[0]["per_group"] is True, "the runs list says so: the page offers no compare of it"
    assert jobs.run_line(root, load_study(root), root / "runs" / "r1")["per_group"] is True


def test_the_html_report_leads_with_the_runs_own_words(tmp_path):
    root, _ = per_group_run(tmp_path, {"small": ("use", 2), "large": ("keep", 2)})
    run_line = build_verdict(root, "r1")["line"]
    data = jobs.results(root, "r1", document_of, group="small")
    small_line, large_line = (next(g["line"] for g in data["per_group"]["groups"] if g["name"] == n) for n in ("small", "large"))
    page = jobs.report_html(data)
    escaped = page.replace("&#x27;", "'")
    assert escaped.index(run_line) < escaped.index(small_line), "the run's line comes first"
    assert large_line in escaped and "<h2>Per kind of case</h2>" in page
    assert "The group small" in page and "The final check of small" in page and "What changed of small" in page


def test_the_summary_leads_with_the_runs_own_words(tmp_path):
    root, _ = per_group_run(tmp_path, {"small": ("use", 2), "large": ("keep", 2)})
    run = build_verdict(root, "r1")
    text = jobs.results(root, "r1", document_of, group="small")["summary_text"]
    lines = text.splitlines()
    assert lines[1] == f"Answer: {run['line']} {run['advice']}"
    assert f"- large: {run['groups']['large']['line']}" in lines and f"- small: {run['groups']['small']['line']}" in lines
    assert any(line.startswith("Final check (small): ") for line in lines)
    assert not any(line.startswith("Final check: ") for line in lines), "no group figure without its group's name"


def test_a_group_whose_ai_share_rounds_to_nothing_searches_without_ai(tmp_path):
    study = replace(load_study(per_group_study(tmp_path, sizes=[20] * 9, held=0)),
                    budget=Budget(hours=1.0, ai_enabled=True, ai_max_usd=0.0003))
    parts = {"a": {"training": [str(i) for i in range(8)], "test": []}, "b": {"training": ["8"], "test": []}}
    split = execute.split_budget(study, parts)
    assert split["b"][1] == 0.0
    assert execute.group_study(study, parts["b"], *split["b"]).budget.ai_enabled is False, "never the $1 default cap"
    own = execute.group_study(study, parts["a"], *split["a"])
    assert own.budget.ai_enabled is True and own.budget.ai_max_usd == split["a"][1] and own.per_group is False


def test_a_stopped_run_shows_the_group_it_stopped_in(tmp_path):
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("keep", 2)})
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    job["phase"], job["per_group"]["current"] = "stopped", "small"
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    assert jobs.status(root, "r1", document_of)["group"]["name"] == "small"
    assert jobs.results(root, "r1", document_of)["per_group"]["group"] == "small"
    status = jobs.status(root, "r1", document_of)
    assert status["dashboard"] == f"/studies/{root.name}/runs/r1/groups/small/dashboard/"
    job["phase"] = "done"
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    assert jobs.results(root, "r1", document_of)["per_group"]["group"] == "large", "a finished run: its first group"


def test_the_dashboard_route_reaches_a_group_folder():
    from evolvekit.app import server

    found = server._DASHBOARD.match("/studies/s/runs/r1/groups/small/dashboard/api/status")  # noqa: SLF001
    assert found and found.group("run") == "r1" and found.group("group") == "small" and found.group("rest") == "api/status"
    plain = server._DASHBOARD.match("/studies/s/runs/r1/dashboard/")  # noqa: SLF001
    assert plain and plain.group("group") is None


def test_with_tags_a_start_solves_no_case_for_its_features(tmp_path):
    root = per_group_study(tmp_path, held=2, tags={"small": 4, "large": 4})

    def no_solve(_root, case, say=None):
        raise AssertionError(f"{case} was solved")

    doc = groups.compute(root, tables=no_solve, features=False)
    assert doc["method"] == "tags" and doc["features"] == {} and set(doc["groups"]) == {"small", "large"}


def test_the_rules_split_is_kept_once_the_run_is_done(tmp_path):
    doc = {"method": "features", "describe": {"rows_stops": "the number of rows in stops"},
           "features": {"c0": {"rows_stops": 20.0}, "c1": {"rows_stops": 22.0}, "c2": {"rows_stops": 80.0},
                        "c3": {"rows_stops": 82.0}},
           "groups": {"group 1": {"training": ["c0", "c1"], "test": []}, "group 2": {"training": ["c2", "c3"], "test": []}}}
    path = tmp_path / "groups.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert groups.cache_stump(path)["thresholds"] == [51.0]
    kept = json.loads(path.read_text(encoding="utf-8"))
    kept["stump"]["thresholds"] = [40.0]  # the kept split is the one read, not computed again
    lines = groups.rule_lines(kept, {"group 1": "use", "group 2": "use"})
    assert lines[0].startswith("If the number of rows in stops is above 40,")


def test_a_per_group_run_is_not_resumed_continued_or_checked_again(tmp_path):
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("keep", 2)})
    for refuse in (lambda: execute.final_check(root, "r1", log=lambda line: None),
                   lambda: execute.resume_study(root, "r1", log=lambda line: None),
                   lambda: execute.continue_study(root, "r1", hours=0.1, log=lambda line: None)):
        with pytest.raises(execute.HarnessError, match="a run per kind of case cannot be resumed, continued or checked again"):
            refuse()
    home = _home(root)
    for refuse in (lambda: jobs.continue_run(home, root, "r1", 0.1), lambda: jobs.resume_final_check(home, root, "r1")):
        with pytest.raises(AppError, match="a run per kind of case") as err:
            refuse()
        assert err.value.status == 409


# ---------------------------------------------------------------------------
# the whole thing, for real
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_per_group_end_to_end(tmp_path, capsys):
    from evolvekit.cli import main

    home = tmp_path / "home"
    assert main(["study", "new", "Kinds", "--harness", "demo-tour", "--template", "tune", "--app", str(SOLVER),
                 "--home", str(home)]) == 0
    slug = capsys.readouterr().out.strip().splitlines()[-1]
    files = []
    for i, cities in enumerate([20, 20, 20, 20, 80, 80, 80, 80]):
        path = tmp_path / "in" / f"c{i}-{cities}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"seed": i, "cities": cities}), encoding="utf-8")
        files.append(str(path))
    assert main(["study", "cases", slug, "--add", *files, "--home", str(home)]) == 0
    capsys.readouterr()
    root = home / "studies" / slug
    study = load_study(root)
    cases = sorted(study.training + study.test)
    small, large = cases[:4], cases[4:]
    for case in small:
        groups.write_tag(root, case, "small")
    for case in large:
        groups.write_tag(root, case, "large")
    save_study(replace(study, training=small[:2] + large[:2], test=small[2:] + large[2:], per_group=True,
                       budget=replace(study.budget, hours=0.05)), root)
    code = main(["study", "start", slug, "--wait", "--json", "--timeout", "900", "--home", str(home)])
    out = capsys.readouterr().out
    assert code == 0, out
    document = json.loads(out.strip().splitlines()[-1])
    assert set(document["groups"]) == {"small", "large"}, document
    assert all(g["decision"] for g in document["groups"].values())
    run_dir = root / "runs" / document["run"]
    assert (run_dir / "downloads" / "settings_by_group.json").is_file()
    assert (run_dir / "downloads" / "rule.md").is_file()
    assert (run_dir / "groups" / "small" / "study.yaml").is_file()
    assert json.loads((run_dir / "job.json").read_text(encoding="utf-8"))["phase"] == "done"
    assert main(["study", "report", slug, "--markdown", "--home", str(home)]) == 0
    assert "## Per kind of case" in capsys.readouterr().out


def test_a_run_merges_a_group_without_two_held_back_cases_before_it_searches(tmp_path, monkeypatch):
    root = per_group_study(tmp_path, sizes=[20] * 5 + [80] * 5, held=3, tags={"small": 5, "large": 5})

    def stop_here(_root):
        raise RuntimeError("stop here")

    monkeypatch.setattr(execute, "require_valid", lambda *args, **kwargs: None)  # no application is installed here
    monkeypatch.setattr(execute, "measured", stop_here)  # called right after the run's groups.json is written
    with pytest.raises(RuntimeError, match="stop here"):
        execute.run_per_group(root, "r1", log=lambda line: None)
    written = json.loads((root / "runs" / "r1" / groups.GROUPS).read_text(encoding="utf-8"))
    assert list(written["groups"]) == ["large"] and written["groups"]["large"]["merged_from"] == ["small"]
    own = json.loads((root / groups.GROUPS).read_text(encoding="utf-8"))
    assert set(own["groups"]) == {"small", "large"}, "the study's own groups.json is left as computed"


def _posterior(folder: Path, *, chance: float) -> None:
    """The group's final comparison as A1 writes it: a posterior summary on its two held-back cases."""
    path = folder / "confirm" / "final" / "comparison.json"
    comparison = json.loads(path.read_text(encoding="utf-8"))
    comparison["per_candidate"]["c1"]["summary"].update(
        {"chance": chance, "n": 2, "decision": "use", "posterior": {"df": 1}})
    path.write_text(json.dumps(comparison), encoding="utf-8")


def test_each_groups_use_pays_for_the_number_of_groups(tmp_path):
    from evolvekit.app.verdict import PER_GROUP_RULE

    # Two groups, one leg (the chance of a gain), Holm at 0.05 on p = 1 - chance. 0.96 and 0.97 are
    # p = 0.04 and 0.03; the smaller must be <= 0.05 / 2 = 0.025, so neither passes.
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("use", 2)})
    _posterior(run_dir / "groups" / "small", chance=0.96)
    _posterior(run_dir / "groups" / "large", chance=0.97)
    execute.apply_group_holm(run_dir)
    doc = build_verdict(root, "r1")
    assert doc["groups"]["small"]["decision"] == "keep" and doc["groups"]["large"]["decision"] == "keep"
    assert doc["groups"]["small"]["line"].startswith("Not proven on 2 held-back cases with 2 kinds of case compared: ")
    assert "1 kinds" not in json.dumps(doc)
    assert doc["decision"] == "keep" and doc["rule"] == PER_GROUP_RULE
    for name in ("small", "large"):
        job = json.loads((run_dir / "groups" / name / "job.json").read_text(encoding="utf-8"))
        assert job["final"]["decision"] == "keep" and job["final"]["confirmed"] is False
    # p = 0.01 <= 0.025 and then 0.04 <= 0.05: both pass.
    root, run_dir = per_group_run(tmp_path / "b", {"small": ("use", 2), "large": ("use", 2)})
    _posterior(run_dir / "groups" / "small", chance=0.99)
    _posterior(run_dir / "groups" / "large", chance=0.96)
    execute.apply_group_holm(run_dir)
    assert build_verdict(root, "r1")["decision"] == "use"


def test_one_group_pays_for_nothing(tmp_path):
    root, run_dir = per_group_run(tmp_path, {"only": ("use", 2)})
    _posterior(run_dir / "groups" / "only", chance=0.951)
    execute.apply_group_holm(run_dir)
    path = run_dir / "groups" / "only" / "confirm" / "final" / "comparison.json"
    assert "holm" not in json.loads(path.read_text(encoding="utf-8"))["per_candidate"]["c1"]
    assert build_verdict(root, "r1")["decision"] == "use"


def _group_check(look):
    """One group's final check as `execute._extend_check` reads it: look 1 on the planned 3 runs
    per case; while that answer is `keep` and more runs could help (`runs_can_help`), every run
    the 12 seeds allow at once and look 2 -- whatever it says."""
    from evolvekit.confirm import decision_of, runs_can_help

    result = look(3)
    if decision_of(result) == "keep" and runs_can_help(result):
        result = look(12)
    return result


def _family_wise_null_use(cases: int, studies: int) -> tuple[float, float, float]:
    """Three kinds of case of `cases` held-back cases each, no real gain anywhere, runs noisier than
    cases (the pure run-noise worst case: the rule is scale-free): each group's check read as
    `_group_check` reads it, then Holm over the groups (`apply_group_holm`). Returns the share of
    `studies` seeded studies in which some kind of case says use without Holm, with it, and the
    standard error of the second."""
    import math
    import random

    from evolvekit.confirm import apply_holm, decision_of
    from test_confirm import _a_study

    rng = random.Random(20261109 + cases)
    alone = paid = 0
    for _ in range(studies):
        results = {g: _group_check(_a_study(rng, gains=[0.0] * cases, run_sd=0.02)) for g in range(3)}
        alone += any(decision_of(r) == "use" for r in results.values())
        apply_holm(results, what="kinds of case")
        paid += any(decision_of(r) == "use" for r in results.values())
    rate = paid / studies
    return alone / studies, rate, math.sqrt(rate * (1 - rate) / studies)


@pytest.mark.parametrize("cases", [2, 4])
def test_three_null_kinds_of_case_family_wise_false_use_fast_guard_after_each_groups_two_reads(
        cases, capsys):
    """After a group's second read, 1 - chance is no longer a single read's p-value, so Holm's 5 %
    is not kept: some kind of case says use in about 8.8 % (3 kinds x 2 cases) and 8.5 % (3 x 4)
    of null studies (measured on 40000 each, +- 0.14 %). The fast guard is looser than the claim, on
    purpose: 2000 seeded studies have a standard error of about 0.65 %, and this seed reads 9.55 % for
    2 cases, so it asserts under 11 % -- it catches a regression of two points or more. The slow test
    holds the claim itself (under 10 % with two standard errors to spare)."""
    studies = 2000
    alone, rate, se = _family_wise_null_use(cases, studies)
    with capsys.disabled():
        print(f"\nfalse use over 3 null kinds of case x {cases} held-back cases, two reads each "
              f"({studies} studies): unpaid {alone:.3f}, family-wise with Holm {rate:.4f} +- {se:.4f}")
    assert alone > rate, "unpaid, three chances to be wrong say use more often"
    assert rate < 0.11, f"{rate:.4f} of {studies} null studies: some kind of case said use"


@pytest.mark.slow
@pytest.mark.parametrize("cases", [2, 4])
def test_three_null_kinds_of_case_say_use_family_wise_in_under_one_in_ten_two_standard_errors_clear(
        cases, capsys):
    """The same on 8000 seeded studies, so that 10 % is more than two standard errors above the
    measured family-wise rate."""
    studies = 8000
    alone, rate, se = _family_wise_null_use(cases, studies)
    with capsys.disabled():
        print(f"\nfalse use over 3 null kinds of case x {cases} held-back cases, two reads each "
              f"({studies} studies): unpaid {alone:.3f}, family-wise with Holm {rate:.4f} +- {se:.4f}")
    assert rate + 2 * se < 0.10, f"{rate:.4f} family-wise (2 SE = {2 * se:.4f})"


def _real_check(folder: Path, cases: list[str], *, chance: float) -> None:
    """A real comparison (`confirm._analyse`, its json and its markdown) for candidate c1, given a chance of `chance`."""
    from evolvekit.confirm import BASELINE, Comparison, _analyse, render_markdown

    comparison = Comparison(label="final", objective="tour_length", direction="minimize", stage="full",
                            seeds=[1, 2, 3], instances=cases, baseline_id=BASELINE, candidates=["c1"])
    for number, case in enumerate(cases):
        for seed in (1, 2, 3):
            value = 50.0 + number * 7 + seed / 10
            comparison.runs.append({"configuration": BASELINE, "instance": case, "seed": seed, "ok": True,
                                    "kpis": {"tour_length": value}})
            comparison.runs.append({"configuration": "c1", "instance": case, "seed": seed, "ok": True,
                                    "kpis": {"tour_length": value * (0.9 + 0.01 * number)}})
    _analyse(comparison)
    comparison.per_candidate["c1"]["summary"]["chance"] = chance
    out = folder / "confirm" / "final"
    (out / "comparison.json").write_text(json.dumps(comparison.to_json()), encoding="utf-8")
    (out / "comparison.md").write_text(render_markdown(comparison), encoding="utf-8")


def test_a_group_held_by_holm_has_its_markdown_rewritten_too(tmp_path):
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("use", 2)})
    for name, chance in (("small", 0.96), ("large", 0.97)):
        part = json.loads((run_dir / groups.GROUPS).read_text(encoding="utf-8"))["groups"][name]
        _real_check(run_dir / "groups" / name, part["test"], chance=chance)
    before = (run_dir / "groups" / "small" / "confirm" / "final" / "comparison.md").read_text(encoding="utf-8")
    assert "Holm" not in before
    execute.apply_group_holm(run_dir)
    after = (run_dir / "groups" / "small" / "confirm" / "final" / "comparison.md").read_text(encoding="utf-8")
    assert "Judged with the 2 kinds of case together (Holm): does not pass" in after
    assert "Chosen among" not in after, "kinds of case are not a choice among rivals"
    assert "Not proven among 2 kinds of case (Holm)" in after


LEGACY_SUMMARY = {"n": 2, "mean": 3.0, "ci95": [-1.0, 7.0], "chance": 0.93, "worst": -0.4, "wins": 2, "losses": 0,
                  "wilcoxon": {"p": 0.5}, "verdict": "probably better: chance 0.93"}
"""A summary written before A1: a one-sided t chance, a Wilcoxon test, no posterior, no `chance_margin`."""


def test_a_group_written_before_the_posterior_keeps_its_page_when_the_others_pay_for_holm(tmp_path):
    from evolvekit.confirm import LEGACY_RULE_WORDS, RULE_WORDS

    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("use", 2), "old": ("use", 2)})
    for name, chance in (("small", 0.96), ("large", 0.97)):
        part = json.loads((run_dir / groups.GROUPS).read_text(encoding="utf-8"))["groups"][name]
        _real_check(run_dir / "groups" / name, part["test"], chance=chance)
    old = run_dir / "groups" / "old" / "confirm" / "final"
    loaded = json.loads((run_dir / "groups" / "small" / "confirm" / "final" / "comparison.json").read_text(encoding="utf-8"))
    result = loaded["per_candidate"]["c1"]
    result.update(summary=dict(LEGACY_SUMMARY), failure_counted_as_pct=None)
    result.pop("finished_only", None)
    loaded.pop("looks", None), loaded.pop("prior", None)
    (old / "comparison.json").write_text(json.dumps(loaded), encoding="utf-8")
    (old / "comparison.md").write_text("old page", encoding="utf-8")
    execute.apply_group_holm(run_dir)  # renders every group's page again: the old one must not raise
    text = (old / "comparison.md").read_text(encoding="utf-8")
    assert LEGACY_RULE_WORDS in text and RULE_WORDS not in text
    assert "Wilcoxon signed-rank p = 0.5000" in text and "credible" not in text and "losing more than" not in text
    assert "Holm" not in text, "a summary written before A1 is not in the family"


def test_a_failing_holm_step_fails_the_run_instead_of_finishing_it(tmp_path, monkeypatch):
    from types import SimpleNamespace

    root = per_group_study(tmp_path, sizes=[20] * 4 + [80] * 4 + [20, 20, 80, 80], held=4, tags={"small": 6, "large": 6})
    monkeypatch.setattr(execute, "require_valid", lambda *args, **kwargs: None)
    monkeypatch.setattr(execute, "measured", lambda _root: None)
    monkeypatch.setattr(execute, "plan_for", lambda *args, **kwargs: Plan.fixed())

    def make_folder(study, harness, plan, root_, run_id, **kwargs):
        folder = root_ / "runs" / run_id / "groups" / kwargs["group"]
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    class FakeDriver:
        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            return SimpleNamespace(stop_reason="done", generations=1, best=None, aborted=False)

    monkeypatch.setattr(execute, "write_run", make_folder)
    monkeypatch.setattr(execute, "load_config", lambda path: None)
    monkeypatch.setattr("evolvekit.search.driver.Driver", FakeDriver)
    monkeypatch.setattr(execute, "_final_check", lambda study, plan, config, folder, job, log, **kw: job.finish("done"))

    def broken(_run_dir):
        raise RuntimeError("holm broke")

    monkeypatch.setattr(execute, "apply_group_holm", broken)
    with pytest.raises(RuntimeError, match="holm broke"):
        execute.run_per_group(root, "r1", log=lambda line: None)
    job = json.loads((root / "runs" / "r1" / "job.json").read_text(encoding="utf-8"))
    assert job["phase"] == "failed" and "holm broke" in job["error"]


def test_while_a_later_group_is_still_checking_no_group_says_better(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "run_alive", lambda *args, **kwargs: True)  # the run's process lives
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("use", 2)})
    path = run_dir / "job.json"
    job = json.loads(path.read_text(encoding="utf-8"))
    job["phase"] = "search"
    path.write_text(json.dumps(job), encoding="utf-8")
    doc = build_verdict(root, "r1")
    assert doc["decision"] == "pending"
    for name in ("small", "large"):
        assert doc["groups"][name]["decision"] == "pending"
        assert doc["groups"][name]["line"] == "Its answer is read once every kind of case is checked."
        assert "Probably better" not in doc["groups"][name]["line"]
    assert "Probably better" not in json.dumps(doc)


def _run_phase(run_dir: Path, phase: str) -> None:
    path = run_dir / "job.json"
    job = json.loads(path.read_text(encoding="utf-8"))
    job["phase"] = phase
    path.write_text(json.dumps(job), encoding="utf-8")


def test_while_every_waiting_kind_of_case_is_held_the_run_says_not_every_one_is_read(tmp_path, monkeypatch):
    """M1: no group's check is still going -- they wait for each other -- so the run does not name
    one as unfinished."""
    monkeypatch.setattr(jobs, "run_alive", lambda *args, **kwargs: True)  # the run's process lives
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("use", 2)})
    _run_phase(run_dir, "check")
    doc = build_verdict(root, "r1")
    assert doc["decision"] == "pending"
    assert doc["line"] == "Per kind of case: not every kind of case is read yet."
    assert "has not finished" not in doc["line"]
    assert doc["groups"]["small"]["line"] == "Its answer is read once every kind of case is checked."


@pytest.mark.parametrize("phase", ["stopped", "failed"])
def test_a_run_that_ended_early_says_a_held_kind_of_case_was_not_read(tmp_path, phase):
    """M2: a held group of a run that stopped or failed is never read: it says so, not that it waits."""
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("use", 2)})
    _run_phase(run_dir, phase)
    doc = build_verdict(root, "r1")
    for name in ("small", "large"):
        assert doc["groups"][name]["decision"] == "pending"
        assert doc["groups"][name]["line"] == ("Its answer was not read: the run ended before every kind of case "
                                               "was checked.")
    assert "Probably better" not in json.dumps(doc)


def test_the_results_of_a_held_group_say_what_the_verdict_says_not_better(tmp_path, monkeypatch):
    """I1: the group's own final check says use, the run is not done, so the verdict document holds
    the group pending; its results page and summary say the document's words, with no figures."""
    monkeypatch.setattr(jobs, "run_alive", lambda *args, **kwargs: True)  # the run's process lives
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("use", 2)})
    _run_phase(run_dir, "check")
    doc = build_verdict(root, "r1")
    data = jobs.results(root, "r1", document_of, group="small")
    final = data["final"]
    assert final["decision"] == "pending" and final["state"] == "missing"
    assert final["line"] == "Its answer is read once every kind of case is checked."
    assert final["line"] == doc["groups"]["small"]["line"] and final["advice"] == doc["groups"]["small"]["advice"]
    for key in ("mean", "ci95", "worst_pct", "chance", "chance_words", "why_keep"):
        assert key not in final, key
    assert "Probably better" not in json.dumps(final) and "Probably better" not in data["summary_text"]
    assert f"Final check (small): {doc['groups']['small']['line']}" in data["summary_text"].splitlines()
    _run_phase(run_dir, "done")
    assert jobs.results(root, "r1", document_of, group="small")["final"]["decision"] == "use", "read once all are done"
