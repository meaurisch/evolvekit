"""`study.yaml`: one analysis on top of a harness, and what keeps it from running."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from evolvekit.harness import HarnessError
from evolvekit.harness.manifest import load_harness
from evolvekit.harness.study import (
    Study,
    load_study,
    parse_within,
    save_study,
    study_from_template,
    study_problems,
)
from harness_toy import write_toy_harness

APPLICATION = Path(sys.executable).as_posix()
"""A study's application must be an absolute path that exists: this interpreter is one."""

EXAMPLE = {
    "study": 1,
    "name": "Item values for the March lists",
    "harness": {"id": "toy", "version": "1.0.0"},
    "template": "values",
    "application": {"path": APPLICATION, "version": "3.12.1"},
    "cases": {"training": ["cases/a.json", "cases/b.json", "cases/c.json"], "test": ["cases/d.json"]},
    "inputs": {"start": "inputs/start.json"},
    "vary": {
        "settings": {"threshold": "tune", "factor": {"tune": True, "low": 0.8, "high": 1.5, "start": 1.0},
                     "crash": {"fixed": False}},
        "data": {"a_values": {"lever": "item_values", "column": "value", "where": "kind = 'a'", "mode": "scale",
                              "low": 0.5, "high": 2.0, "start": 1.0},
                 "b_boost": {"lever": "boost", "where": "kind = 'b'", "mode": "add", "low": 0, "high": 5, "start": 0}},
    },
    "constraints": [
        {"says": "Values of kind a at most double the factor", "expr": "a_values <= 2 * factor"},
        {"says": "No item above 50", "sql": "SELECT COUNT(*) = 0 FROM items WHERE value > 50"},
    ],
    "kpis": {
        "total": {"from": "harness"},
        "picked_count": {"from": "harness"},
        "a_value": {"template": "kind_value", "params": {"kind": "a"}},
        "heavy": {"says": "Weight of the heavy items", "direction": "lower", "unit": "kg",
                  "sql": "SELECT TOTAL(weight) FROM items WHERE weight > 1",
                  "rows_sql": "SELECT id, weight FROM items WHERE weight > 1"},
        "penalty": {"says": "Heavy weight, plus half the value of a", "direction": "lower",
                    "weighted": {"heavy": 1.0, "a_value": 0.5}},
    },
    "goal": {"levels": [{"kpi": "picked_count", "direction": "higher", "equal_within": "10 %"},
                        {"kpi": "penalty", "direction": "lower"}]},
    "guardrails": [{"kpi": "total", "max": 500}],
    "limits": {"time_per_case_s": 5, "retries": 1, "runs_per_case": 2},
    "budget": {"hours": 0.5, "ai": {"enabled": False, "max_usd": 2.0}},
    "plan": {"auto": True},
    "step": 5,
}


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    return load_harness(write_toy_harness(tmp_path_factory.mktemp("h") / "toy"))


def _study_folder(tmp_path, document=EXAMPLE):
    for case in [*document["cases"]["training"], *document["cases"]["test"]]:
        (tmp_path / case).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / case).write_text('{"items": []}', encoding="utf-8")
    (tmp_path / "inputs").mkdir(exist_ok=True)
    (tmp_path / "inputs" / "start.json").write_text(json.dumps({"threshold": 4.0}), encoding="utf-8")
    return tmp_path


def test_the_example_study_is_ready_to_run(tmp_path, harness):
    study = Study.parse(EXAMPLE)
    assert study.tunables() == ["threshold", "factor", "a_values", "b_boost"]
    assert study_problems(study, harness, root=_study_folder(tmp_path)) == []


def test_a_study_survives_a_round_trip_through_its_file(tmp_path):
    study = Study.parse(EXAMPLE)
    save_study(study, tmp_path)
    assert load_study(tmp_path) == study
    assert not list(tmp_path.glob("*.tmp")), "written atomically"


def test_a_new_study_from_a_template_starts_from_the_harness_defaults(harness):
    study = study_from_template(harness, "tune", "Settings for March")
    assert study.template == "tune" and study.tuned_settings() == ["threshold", "factor"]
    assert [level.kpi for level in study.goal] == ["total"]
    assert (study.limits.time_per_case_s, study.budget.hours) == (5, 0.05)
    blank = study_from_template(harness, None, "Blank")
    assert blank.tunables() == [] and blank.goal == []
    with pytest.raises(HarnessError, match="the harness has no template 'nope'"):
        study_from_template(harness, "nope", "x")


def _with(**changes):
    document = json.loads(json.dumps(EXAMPLE))
    for dotted, value in changes.items():
        keys = dotted.split("__")
        target = document
        for key in keys[:-1]:
            target = target[int(key)] if isinstance(target, list) else target[key]
        target[keys[-1]] = value
    return document


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"vary__settings__speed": "tune"}, "vary.settings.speed: the harness has no setting 'speed'"),
        ({"vary__settings__crash": {"fixed": "yes"}}, "vary.settings.crash.fixed: expected true or false"),
        ({"vary__settings__factor": {"tune": True, "low": 0.1, "high": 1.5}}, "vary.settings.factor: the range must lie within [0.5, 2]"),
        ({"vary__settings__factor": {"tune": True, "low": 0.8, "high": 1.5, "start": 1.9}},
         "vary.settings.factor.start: 1.9 lies outside the range [0.8, 1.5]"),
        ({"vary__data__a_values__lever": "prices"}, "vary.data.a_values.lever: the harness has no data change 'prices'"),
        ({"vary__data__a_values__column": "weight"}, "vary.data.a_values.column: item_values changes ['value'], not 'weight'"),
        ({"vary__data__a_values__mode": "square"}, "vary.data.a_values.mode: item_values can ['scale', 'set', 'add'], not 'square'"),
        ({"vary__data__a_values__where": "colour = 'red'"}, "vary.data.a_values.where: does not run on table items: no such column: colour"),
        ({"vary__data__a_values__start": 3.0}, "vary.data.a_values.start: 3 lies outside [0.5, 2]"),
        ({"vary__data__threshold": EXAMPLE["vary"]["data"]["a_values"]}, "vary.data.threshold: 'threshold' is the name of a setting"),
        ({"constraints__0__expr": "speed < 2"}, "constraints[0].expr: 'speed' is not something the study tunes"),
        ({"constraints__0__expr": "a_values <"}, "constraints[0].expr: not a valid expression"),
        ({"constraints__1__sql": "SELECT COUNT(*) FROM itemz"}, "constraints[1].sql: does not run against the tables: no such table: itemz"),
        ({"kpis__distance": {"from": "harness"}}, "kpis.distance: the harness has no ready-made KPI 'distance'"),
        ({"kpis__a_value__params": {}}, "kpis.a_value.params: fill in ['kind']"),
        ({"kpis__heavy__sql": "SELECT TOTAL(wieght) FROM items"}, "kpis.heavy.sql: does not run against the tables: no such column: wieght"),
        ({"kpis__penalty__weighted": {"heavy": 1.0, "missing": 2}}, "kpis.penalty.weighted.missing: a weighted sum adds up KPIs of the study"),
        ({"goal__levels__1__kpi": "speed"}, "goal.levels[1].kpi: 'speed' is not a KPI of the study"),
        ({"goal__levels__0__equal_within": None}, "goal.levels[0].equal_within: say when two results count as equal"),
        ({"goal__levels__1__equal_within": "1 %"}, "goal.levels[1].equal_within: the last level decides whatever is left"),
        ({"goal__levels__0__equal_within": "a bit"}, "goal.levels[0].equal_within: expected a tolerance such as '1 %'"),
        ({"guardrails": [{"kpi": "total", "max": 1, "min": 0}]}, "guardrails[0]: give `max` or `min`, one of them"),
        ({"guardrails": [{"kpi": "speed", "max": 1}]}, "guardrails[0].kpi: 'speed' is not a KPI of the study"),
        ({"cases__test": ["cases/a.json"]}, "cases.test: cases/a.json is in both sets"),
        ({"cases__training": ["C:/elsewhere/a.json"]}, "cases.training: 'C:/elsewhere/a.json' must be a path inside the study folder"),
        ({"cases__training": ["../a.json"]}, "cases.training: '../a.json' must be a path inside the study folder"),
        ({"cases__training": []}, "cases.training: the search needs at least one case to learn from"),
        ({"inputs__weights": "inputs/w.json"}, "inputs.weights: the harness takes no input called 'weights'"),
        ({"limits__time_per_case_s": 0.5}, "limits.time_per_case_s: at least 1 s"),
        ({"application__path": ""}, "application: say where the application is"),
        ({"application__path": "venv/bin/python"}, "application: give the full path to the application"),
        ({"application__path": "C:/nowhere/python.exe"}, "application: there is nothing at C:/nowhere/python.exe any more"),
        ({"name": ""}, "name: give the study a name"),
    ],
)
def test_what_keeps_a_study_from_running_is_said_per_key(tmp_path, harness, changes, message):
    document = _with(**changes)
    problems = study_problems(Study.parse(document), harness, root=_study_folder(tmp_path, document))
    assert any(problem.startswith(message) for problem in problems), problems


def test_a_second_run_of_a_case_needs_a_seed(tmp_path, harness):
    unseeded = harness.__class__(**{**harness.__dict__, "seeds": False})
    problems = study_problems(Study.parse(EXAMPLE), unseeded, root=_study_folder(tmp_path))
    assert "limits.runs_per_case: the application takes no seed" in problems[0]


def test_the_shape_of_the_document_is_checked_when_it_is_read():
    with pytest.raises(HarnessError, match=r"<study>: unknown key\(s\) \['colour'\]"):
        Study.parse({**EXAMPLE, "colour": "blue"})
    with pytest.raises(HarnessError, match="kpis.total: give one of `from: harness`, `template`, `sql` or `weighted`"):
        Study.parse(_with(kpis__total={"says": "nothing else"}))
    with pytest.raises(HarnessError, match="constraints.0.: give `expr`|constraints\\[0\\]: give `expr`"):
        Study.parse(_with(constraints=[{"says": "x"}]))


@pytest.mark.parametrize(
    "text, expected",
    [("1 %", (0.01, True)), ("2.5%", (0.025, True)), (0.5, (0.5, False)), ("0.5", (0.5, False)), (None, None)],
)
def test_equal_within_is_relative_or_absolute(text, expected):
    result = parse_within(text)
    assert result == (pytest.approx(expected[0]), expected[1]) if expected else result is None


@pytest.mark.parametrize("text", ["a bit", 0, "-1 %", True])
def test_equal_within_refuses_what_is_no_tolerance(text):
    with pytest.raises(ValueError):
        parse_within(text)
