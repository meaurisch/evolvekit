"""The harness SDK (`evolvekit/harness/sdk/evk_harness.py`), driven the way
evolvekit drives it: a runner in its own directory, run as a subprocess."""

from __future__ import annotations

import ast
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from evolvekit.harness.sdk import SDK_PATH
from evolvekit.harness.sdk import evk_harness as evk
from test_expressions import CASES

RUNNER = '''\
import json
import evk_harness as evk

def read_case(path):
    data = json.load(open(path, encoding="utf-8"))
    if "items" not in data:
        raise ValueError("this is not a list of items")
    return evk.Case({"items": [dict(item) for item in data["items"]]}, native=data)

def solve(case, settings, time_limit_s, seed):
    if settings.get("crash"):
        raise RuntimeError("the solver fell over")
    items = case.tables["items"]
    picked = [r["id"] for r in items if r["value"] > settings["threshold"]]
    total = sum(r["weight"] * r["value"] for r in items) * settings["factor"]
    return {"total": total, "picked": picked, "time_limit": time_limit_s, "seed": seed}

def solution_tables(case, solution):
    return {"picks": [{"id": i} for i in solution["picked"]],
            "summary": [{"total": solution["total"], "time_limit": solution["time_limit"]}]}

def apply_lever(case, name, rows, value):
    for row in rows:
        row["tag"] = "boosted"
        row["value"] = row["value"] + value

def measure(case, solution, tables):
    return {"picked_count": len(solution["picked"])}

def discover():
    return {"version": "9.9", "settings": {"factor": {"type": "float", "default": 1.0}}}

if __name__ == "__main__":
    evk.main(globals())
'''

TABLES = {
    "items": {"id": "int", "kind": "text", "value": "float", "weight": "float", "count": "int", "tag": "text"},
    "picks": {"id": "int"},
    "summary": {"total": "float", "time_limit": "float"},
}

CASE = {"items": [
    {"id": 1, "kind": "a", "value": 2.0, "weight": 1.0, "count": 3, "tag": ""},
    {"id": 2, "kind": "b", "value": 5.0, "weight": 2.0, "count": 4, "tag": ""},
    {"id": 3, "kind": "a", "value": 7.0, "weight": 0.5, "count": 5, "tag": ""},
]}


def _study(**changes):
    study = {
        "study_run": 1,
        "time_limit_s": 5,
        "settings": {"base": {"threshold": 3.0, "factor": 1.0, "crash": False}, "tuned": ["factor"]},
        "levers": {},
        "constraints": [],
        "kpis": {
            "total": {"sql": "SELECT total FROM summary", "direction": "lower"},
            "picked_count": {"measure": True, "direction": "higher"},
            "a_value": {"sql": "SELECT TOTAL(value) FROM items WHERE kind = :kind", "params": {"kind": "a"},
                        "direction": "lower"},
            "combo": {"weighted": {"total": 1.0, "picked_count": 2.0}, "direction": "lower"},
        },
        "guardrails": [{"kpi": "picked_count", "min": 1}],
        "tables": TABLES,
    }
    study.update(changes)
    return study


@pytest.fixture
def harness(tmp_path):
    shutil.copy(SDK_PATH, tmp_path / "evk_harness.py")
    (tmp_path / "runner.py").write_text(RUNNER, encoding="utf-8")
    (tmp_path / "case.json").write_text(json.dumps(CASE), encoding="utf-8")
    return tmp_path


def _run(harness: Path, *args: str, study=None, values=None):
    if study is not None:
        (harness / "study.json").write_text(json.dumps(study), encoding="utf-8")
    if values is not None:
        (harness / "values.json").write_text(json.dumps(values), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(harness / "runner.py"), *args],
        cwd=harness, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


def _solve(harness, study, values=None, *extra):
    return _run(harness, "solve", "--case", "case.json", "--seed", "3", "--values", "values.json",
                "--study", "study.json", *extra, study=study, values=values if values is not None else {"factor": 1.0})


def _result(completed):
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_solve_prints_every_kpi_the_weighted_sum_and_the_guardrail(harness):
    result = _result(_solve(harness, _study(), {"factor": 2.0}))
    kpis = result["kpis"]
    assert kpis["total"] == pytest.approx((2 + 10 + 3.5) * 2)
    assert kpis["picked_count"] == 2 and kpis["a_value"] == pytest.approx(9.0)
    assert kpis["combo"] == pytest.approx(31.0 - 2 * 2), "lower-is-better adds, higher-is-better subtracts"
    assert kpis["guardrail_violation"] == 0.0 and kpis["solve_s"] >= 0
    assert "guardrails hold" in result["text_feedback"]


def test_a_guardrail_that_does_not_hold_is_a_violation_not_a_failure(harness):
    study = _study(settings={"base": {"threshold": 100.0, "factor": 1.0, "crash": False}, "tuned": []})
    result = _result(_solve(harness, study))
    assert result["kpis"]["picked_count"] == 0
    assert result["kpis"]["guardrail_violation"] == pytest.approx(1.0)
    assert "guardrails broken: picked_count = 0 < 1" in result["text_feedback"]


def test_levers_change_exactly_the_selected_cells(harness):
    study = _study(levers={
        "a_scale": {"lever": "item_values", "table": "items", "column": "value", "where": "kind = 'a'", "mode": "scale"},
        "b_count": {"lever": "item_counts", "table": "items", "column": "count", "where": "id = 2", "mode": "scale",
                    "integer": True},
        "all_weights": {"lever": "item_weights", "table": "items", "column": "weight", "mode": "add"},
    })
    completed = _solve(harness, study, {"factor": 1.0, "a_scale": 1.5, "b_count": 1.3, "all_weights": 0.25},
                       "--tables-out", "tables.sqlite")
    _result(completed)
    db = sqlite3.connect(harness / "tables.sqlite")
    now = db.execute("SELECT id, value, weight, count FROM items ORDER BY id").fetchall()
    before = db.execute("SELECT id, value, weight, count FROM orig_items ORDER BY id").fetchall()
    assert before == [(1, 2.0, 1.0, 3), (2, 5.0, 2.0, 4), (3, 7.0, 0.5, 5)]
    assert now == [(1, 3.0, 1.25, 3), (2, 5.0, 2.25, 5), (3, 10.5, 0.75, 5)], "4 x 1.3 = 5.2, rounded"
    assert db.execute("SELECT COUNT(*) FROM picks").fetchone() == (2,)


def test_a_code_lever_is_applied_by_the_runner(harness):
    study = _study(levers={"boost": {"lever": "boost", "table": "items", "where": "kind = 'b'", "code": True}})
    _result(_solve(harness, study, {"factor": 1.0, "boost": 10.0}, "--tables-out", "t.sqlite"))
    rows = sqlite3.connect(harness / "t.sqlite").execute("SELECT id, tag, value FROM items ORDER BY id").fetchall()
    assert rows == [(1, "", 2.0), (2, "boosted", 15.0), (3, "", 7.0)]


def test_a_code_lever_hook_gets_the_mode_when_it_takes_one():
    seen = []

    def with_mode(case, name, rows, value, mode=None):
        seen.append((name, [row["id"] for row in rows], value, mode))

    def without_mode(case, name, rows, value):
        seen.append((name, [row["id"] for row in rows], value))

    case = evk.Case({"items": [dict(item) for item in CASE["items"]]})
    levers = {"w": {"lever": "windows", "table": "items", "where": "kind = 'a'", "code": True, "mode": "add"}}
    evk.apply_levers(case, levers, {"w": 2.0}, {"apply_lever": with_mode}, TABLES)
    evk.apply_levers(case, levers, {"w": 3.0}, {"apply_lever": without_mode}, TABLES)
    assert seen == [("windows", [1, 3], 2.0, "add"), ("windows", [1, 3], 3.0)]


def test_a_lever_that_selects_nothing_on_this_case_changes_nothing_and_says_so(harness):
    study = _study(levers={"c": {"lever": "v", "table": "items", "column": "value", "where": "kind = 'c'", "mode": "set"}})
    result = _result(_solve(harness, study, {"factor": 1.0, "c": 0.0}))
    assert "c selects no row of items" in result["text_feedback"]


def test_a_broken_constraint_exits_2_with_its_sentence_before_any_solving(harness):
    expr = _study(constraints=[{"says": "The factor stays below 2", "expr": "factor < 2"}],
                  settings={"base": {"threshold": 3.0, "factor": 1.0, "crash": True}, "tuned": ["factor"]})
    completed = _solve(harness, expr, {"factor": 2.5})
    assert completed.returncode == 2
    assert completed.stderr.strip().splitlines()[-1] == 'the values break the constraint "The factor stays below 2"'
    sql = _study(constraints=[{"says": "No value above 6", "sql": "SELECT COUNT(*) = 0 FROM items WHERE value > 6"}])
    completed = _solve(harness, sql)
    assert completed.returncode == 2
    assert completed.stderr.strip().splitlines()[-1] == 'the changed case breaks the constraint "No value above 6"'


def test_a_case_that_cannot_be_read_exits_3_with_one_sentence(harness):
    (harness / "bad.json").write_text('{"things": []}', encoding="utf-8")
    completed = _run(harness, "solve", "--case", "bad.json", "--study", "study.json", study=_study())
    assert completed.returncode == 3
    assert completed.stderr.strip().splitlines()[-1] == "cannot read bad.json: this is not a list of items"
    missing = _run(harness, "inspect", "--case", "nowhere.json")
    assert missing.returncode == 3 and "there is no case file" in missing.stderr


def test_a_crash_exits_1_with_the_traceback_first_and_one_sentence_last(harness):
    study = _study(settings={"base": {"threshold": 3.0, "factor": 1.0, "crash": True}, "tuned": []})
    completed = _solve(harness, study)
    assert completed.returncode == 1
    lines = completed.stderr.strip().splitlines()
    assert lines[-1] == "error: RuntimeError: the solver fell over"
    assert any("Traceback" in line for line in lines[:-1])


def test_the_time_limit_on_the_command_line_wins(harness):
    result = _solve(harness, _study(kpis={"limit": {"sql": "SELECT time_limit FROM summary", "direction": "lower"}},
                                    guardrails=[]), None, "--time-limit", "1.25")
    assert _result(result)["kpis"]["limit"] == 1.25


def test_inspect_and_describe(harness):
    inspected = json.loads(_run(harness, "inspect", "--case", "case.json").stdout.strip().splitlines()[-1])
    assert inspected == {"ok": True, "summary": "3 items", "tables": {"items": 3}}
    described = json.loads(_run(harness, "describe").stdout.strip().splitlines()[-1])
    assert described["sdk"] == evk.SDK_VERSION and described["discover"]["version"] == "9.9"


def test_the_built_in_exports(harness):
    (harness / "study.json").write_text(json.dumps(_study(levers={
        "a_scale": {"lever": "item_values", "table": "items", "column": "value", "where": "kind = 'a'", "mode": "scale"}})),
        encoding="utf-8")
    (harness / "values.json").write_text(json.dumps({"factor": 1.5, "a_scale": 2.0}), encoding="utf-8")
    for fmt, out in (("settings_json", "s.json"), ("settings_flags", "s.txt"), ("data_changes", "d.json")):
        completed = _run(harness, "export", "--format", fmt, "--values", "values.json", "--study", "study.json", "--out", out)
        assert completed.returncode == 0, completed.stderr
    assert json.loads((harness / "s.json").read_text()) == {"crash": False, "factor": 1.5, "threshold": 3.0}
    assert (harness / "s.txt").read_text().split() == ["--threshold", "3.0", "--factor", "1.5", "--crash", "false"]
    changes = json.loads((harness / "d.json").read_text())
    assert changes == [{"change": "a_scale", "lever": "item_values", "table": "items", "column": "value",
                        "rows": "kind = 'a'", "mode": "scale", "value": 2.0}]
    assert (harness / "d.csv").read_text().startswith("change,lever,table,column,rows,mode,value")


# -- in process ------------------------------------------------------------------


def test_sql_only_reads_and_stops_after_its_time():
    db = evk.build_database({"t": [{"x": 1}, {"x": 2}]})
    clock = evk.guard(db, limit_s=0.2)
    assert evk.query_value(db, "SELECT SUM(x) FROM t", clock=clock) == (3.0, None)
    for sql in ("DELETE FROM t", "INSERT INTO t VALUES (3)", "DROP TABLE t", "ATTACH DATABASE 'x.db' AS other",
                "PRAGMA writable_schema = 1", "CREATE TABLE u (y)"):
        with pytest.raises(evk.HarnessStop):
            evk.query_value(db, sql, clock=clock)
    endless = "WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c) SELECT COUNT(*) FROM c"
    with pytest.raises(evk.HarnessStop, match="longer than 0.2 s"):
        evk.query_value(db, endless, clock=clock, limit_s=0.2)
    assert evk.query_value(db, "SELECT SUM(x) FROM t", clock=clock) == (3.0, None), "still usable afterwards"


def test_null_counts_as_zero_and_says_so():
    db = evk.build_database({"t": [{"x": 1}]})
    clock = evk.guard(db)
    assert evk.query_value(db, "SELECT MAX(x) FROM t WHERE x > 5", clock=clock) == (0.0, "no value (NULL), counted as 0")
    with pytest.raises(evk.HarnessStop, match="not a number"):
        evk.query_value(db, "SELECT 'abc'", clock=clock)


def test_private_row_keys_never_become_columns():
    db = evk.build_database({"t": [{"x": 1, "_native": object()}]})
    assert [c[1] for c in db.execute("PRAGMA table_info(t)")] == ["x"]


def test_the_weighted_sum_and_the_guardrail_violation():
    assert evk.weighted_sum({"late": 1.0, "served": 0.5}, {"late": 3, "served": 10}, {"served": "higher"}) == -2.0
    rails = [{"kpi": "missed", "max": 0}, {"kpi": "on_time", "min": 0.95}, {"kpi": "cost", "max": 200}]
    assert evk.guardrail_violation(rails, {"missed": 2, "on_time": 0.9, "cost": 250}) == pytest.approx(
        2 / 1 + 0.05 / 1 + 50 / 200
    )
    assert evk.broken_guardrails(rails, {"missed": 0, "on_time": 0.96, "cost": 10}) == []


@pytest.mark.parametrize("text, values, expected", CASES)
def test_the_sdk_speaks_the_engines_expression_language(text, values, expected):
    expression = evk.Expression.parse(text)
    if isinstance(expected, type) and issubclass(expected, Exception):
        with pytest.raises(evk.ExpressionError):
            expression.evaluate(values)
    else:
        assert expression.evaluate(values) == expected


def test_the_sdk_runs_on_the_standard_library_alone_and_on_python_3_9():
    source = SDK_PATH.read_text(encoding="utf-8")
    ast.parse(source, feature_version=(3, 9))  # the application's Python may be older than ours
    imported = {
        line.split()[1].split(".")[0]
        for line in source.splitlines()
        if line.startswith(("import ", "from ")) and not line.startswith("from __future__")
    }
    assert imported <= {"argparse", "ast", "copy", "csv", "inspect", "json", "math", "os", "sqlite3", "sys", "time", "traceback"}
