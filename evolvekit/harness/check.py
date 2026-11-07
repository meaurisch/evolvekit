"""`evolvekit harness check DIR`: does this harness work, with this application?

From cheap to expensive, stopping where going on would only repeat a failure:

1. the manifest: its schema, and every SQL statement against the declared tables;
2. the application probe;
3. `describe`, and drift: settings the installed application has that the
   harness lacks, settings the harness declares that it no longer has, and
   defaults that changed -- each one sentence naming the key to edit;
4. for up to three sample cases: `inspect`; a short solve at the defaults;
   the tables the runner produces against the declaration; every ready-made
   KPI finite; every KPI template instantiated with its first choice; every
   lever, applied to every other row, changing exactly those cells; a
   constraint and a guardrail computed; every export;
5. three random settings vectors solve without error;
6. the comparability trap: a study template whose goal is a KPI marked
   `changes_with_levers` while it varies levers.

Each finding is a `Check` with a fix. `--json` prints `{"ok": bool, "checks":
[{"id", "ok", "severity", "message", "fix"}]}`, so an AI coding tool can go
round until it passes; the exit code is 0 when everything passes, 1 for
warnings only, 2 for failures.
"""

from __future__ import annotations

import json
import math
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evolvekit.harness import HarnessError
from evolvekit.harness.manifest import Harness, load_harness
from evolvekit.harness.probe import probe_application
from evolvekit.harness.sdk import evk_harness as evk

__all__ = ["Check", "check_harness", "exit_code", "render"]

SOLVE_TIMEOUT_S = 180.0


@dataclass
class Check:
    id: str
    ok: bool
    message: str
    fix: str = ""
    severity: str = "pass"
    """`pass`, `warning` or `failure`."""


def _pass(check_id: str, message: str) -> Check:
    return Check(check_id, True, message)


def _fail(check_id: str, message: str, fix: str) -> Check:
    return Check(check_id, False, message, fix, "failure")


def _warn(check_id: str, message: str, fix: str) -> Check:
    return Check(check_id, False, message, fix, "warning")


class _Runner:
    """The harness's runner, in a scratch folder, the way a study runs it."""

    def __init__(self, harness: Harness, app: str, folder: Path) -> None:
        self.harness = harness
        self.app = app
        self.folder = folder
        runner = str(harness.root / "runner.py")
        if harness.application.kind == "python":
            self.base = [app, runner]
        else:
            self.base = [sys.executable, runner, "--app", app]

    def run(self, *args: str, timeout: float = SOLVE_TIMEOUT_S) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [*self.base, *args], cwd=self.folder, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(args, 1, "", f"no answer within {timeout:g} s\n")

    @staticmethod
    def said(done: subprocess.CompletedProcess) -> str:
        lines = (done.stderr or "").strip().splitlines()
        return lines[-1] if lines else f"exit code {done.returncode}"

    @staticmethod
    def printed(done: subprocess.CompletedProcess) -> dict[str, Any] | None:
        for line in reversed((done.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                try:
                    return json.loads(line)
                except ValueError:
                    return None
        return None

    def solve(self, case: Path, study_run: dict[str, Any], values: dict[str, Any], name: str,
              time_limit: float) -> tuple[subprocess.CompletedProcess, Path]:
        study = self.folder / f"{name}.study.json"
        study.write_text(json.dumps(study_run), encoding="utf-8")
        values_path = self.folder / f"{name}.values.json"
        values_path.write_text(json.dumps(values), encoding="utf-8")
        tables = self.folder / f"{name}.sqlite"
        args = ["solve", "--case", str(case), "--seed", "0", "--values", values_path.name, "--study", study.name,
                "--tables-out", tables.name]
        if self.harness.time_limit.accepts:
            args += ["--time-limit", f"{time_limit:g}"]
        return self.run(*args), tables


def _defaults(harness: Harness) -> dict[str, Any]:
    return {name: s.parameter.coerce(s.parameter.default) for name, s in harness.settings.items()}


def _extra_values(harness: Harness) -> dict[str, Any]:
    """The tuned values of the defaults solve: the first numeric setting, at
    its default, so that an expression constraint has something to judge."""
    numeric = [s.parameter for s in harness.settings.values() if s.parameter.numeric]
    return {numeric[0].name: numeric[0].coerce(numeric[0].default)} if numeric else {}


def _study_run(harness: Harness, *, levers: dict[str, Any] | None = None, extras: bool = False,
               settings: dict[str, Any] | None = None) -> dict[str, Any]:
    base = _defaults(harness)
    base.update(settings or {})
    kpis = {name: ({"measure": True} if k.measure else {"sql": k.sql}) | {"direction": k.direction}
            for name, k in harness.kpis.items()}
    constraints, guardrails = [], []
    tuned = list(settings or {})
    if extras:
        for name, value in _extra_values(harness).items():
            constraints.append({"says": "check", "expr": f"{name} >= {harness.settings[name].parameter.low!r}"})
            tuned.append(name)
        if harness.request_tables:
            constraints.append({"says": "check", "sql": f'SELECT COUNT(*) >= 0 FROM "{harness.request_tables[0]}"'})
        first = next((name for name, k in harness.kpis.items() if not k.measure), None)
        if first:
            guardrails.append({"kpi": first, "max": 1e300})
    return {
        "study_run": 1, "harness": {"id": harness.id, "version": harness.version}, "name": "harness check",
        "time_limit_s": harness.time_limit.default_s,
        "settings": {"base": base, "tuned": tuned},
        "levers": levers or {}, "constraints": constraints, "kpis": kpis, "guardrails": guardrails,
        "inputs": {}, "tables": harness.declared(),
    }


def _drift(harness: Harness, discovered: Any) -> list[Check]:
    if not isinstance(discovered, dict) or not isinstance(discovered.get("settings"), dict):
        return [_pass("drift", "the runner's discover() does not list settings: no drift to check")]
    found = discovered["settings"]
    if not found:
        return [_warn("drift", "discover() lists no settings at all, so drift cannot be checked",
                      "make discover() return the settings the installed application offers")]
    checks = []
    for name in sorted(set(found) - set(harness.settings)):
        checks.append(_warn(
            f"drift.{name}", f"settings.{name}: the application has a setting {name!r} the harness does not declare",
            "add it under `settings:` in harness.yaml if it is worth tuning; otherwise nothing to do",
        ))
    for name in sorted(set(harness.settings) - set(found)):
        checks.append(_fail(
            f"drift.{name}", f"settings.{name}: the harness declares {name!r}, and the installed application no longer has it",
            f"remove or rename settings.{name} in harness.yaml",
        ))
    for name in sorted(set(found) & set(harness.settings)):
        spec = found[name] if isinstance(found[name], dict) else {}
        if "default" in spec and spec["default"] != harness.settings[name].parameter.default:
            checks.append(_warn(
                f"drift.{name}.default",
                f"settings.{name}.default: the application's default is now {spec['default']!r}, the harness says "
                f"{harness.settings[name].parameter.default!r}",
                f"set settings.{name}.default to {spec['default']!r}",
            ))
    return checks or [_pass("drift", "the harness's settings match what the installed application offers")]


def _trap(harness: Harness) -> list[Check]:
    checks = []
    for template_id, template in harness.templates.items():
        vary = template.get("vary") or {}
        levels = ((template.get("goal") or {}).get("levels") or [])
        if not vary.get("data"):
            continue
        for level in levels:
            kpi = harness.kpis.get(str(level.get("kpi")))
            if kpi is not None and kpi.changes_with_levers:
                checks.append(_warn(
                    f"trap.{template_id}",
                    f"templates/{template_id}.yaml: its goal is {kpi.name}, which changes with the data changes it "
                    "tries -- a better number there may only mean the solver saw different data",
                    "make the goal a KPI measured on the untouched request (not marked changes_with_levers)",
                ))
    return checks or [_pass("trap", "no template aims at a KPI its own data changes distort")]


def _tables(harness: Harness, columns_file: Path, where: str) -> list[Check]:
    try:
        produced = json.loads(columns_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [_fail(f"{where}.tables", "the runner's tables could not be read back", "check solution_tables()")]
    checks = []
    for table in sorted(set(produced) - set(harness.tables)):
        checks.append(_fail(f"{where}.tables.{table}", f"tables.{table}: the runner produces a table the harness does not declare",
                            f"declare tables.{table} in harness.yaml, or stop producing it"))
    for table, spec in harness.tables.items():
        if table not in produced:
            checks.append(_fail(f"{where}.tables.{table}", f"tables.{table}: declared, and the runner produces no such table",
                                "produce it in read_case() (request) or solution_tables() (solution), or remove it"))
            continue
        seen = produced[table]
        if not seen:
            continue  # an empty table has no columns to compare
        for column in sorted(set(seen) - set(spec.columns)):
            checks.append(_fail(f"{where}.tables.{table}.{column}", f"tables.{table}: the runner produces a column {column!r} that is not declared",
                                f"declare tables.{table}.columns.{column}, or drop it from the rows"))
        for column in sorted(set(spec.columns) - set(seen)):
            checks.append(_fail(f"{where}.tables.{table}.{column}", f"tables.{table}.columns.{column}: declared, and the runner's rows have no such key",
                                "add it to the rows, or remove it from harness.yaml"))
    return checks or [_pass(f"{where}.tables", "the runner's tables match the declaration")]


def _rows(db_path: Path, table: str) -> list[tuple]:
    db = sqlite3.connect(db_path)
    try:
        return db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
    finally:
        db.close()


def _check_levers(harness: Harness, runner: _Runner, case: Path, where: str, limit: float, baseline: Path) -> list[Check]:
    checks = []
    for name, lever in harness.levers.items():
        table = lever.table
        before = _rows(baseline, table)
        columns = list(harness.tables[table].columns)
        targets = [(column, lever.modes[0]) for column in lever.columns] if not lever.code else [(None, lever.modes[0])]
        for column, mode in targets:
            label = f"{where}.lever.{name}" + (f".{column}" if column else "")
            index = columns.index(column) if column else None
            if column is not None and mode == "set":
                sample = next((row[index] for row in before if row[index] is not None), 1.0)
                value = float(sample) * 1.1 or 1.0
            else:
                value = {"scale": 1.1, "add": 1.0, "set": 1.0}[mode]
            instance = {"check": {"lever": name, "table": table, "column": column, "where": "rowid % 2 = 1",
                                  "mode": mode, "code": lever.code,
                                  "integer": bool(column) and harness.tables[table].columns[column].type == "int"}}
            done, tables = runner.solve(case, _study_run(harness, levers=instance), {"check": value}, label, limit)
            if done.returncode != 0:
                checks.append(_fail(label, f"levers.{name}: a solve with it applied failed: {runner.said(done)}",
                                    "the application must accept every value in the lever's range; fix solve() or narrow the lever"))
                continue
            after = _rows(tables, table)
            if lever.code:
                changed = sum(1 for a, b in zip(before, after) if a != b)
                checks.append(_pass(label, f"levers.{name} (in code) changed {changed} row(s)") if changed else _fail(
                    label, f"levers.{name}: apply_lever() changed nothing on the rows it was given", "check apply_lever()"))
                continue
            wrong = []
            for position, (old, new) in enumerate(zip(before, after)):
                selected = position % 2 == 0  # rowid 1, 3, 5, ...
                for c, (x, y) in enumerate(zip(old, new)):
                    if c == index and selected and x is not None:
                        expected = {"scale": x * value, "add": x + value, "set": value}[mode]
                        if instance["check"]["integer"]:
                            expected = int(round(expected))
                        if not (isinstance(y, (int, float)) and math.isclose(y, expected, rel_tol=1e-9, abs_tol=1e-9)):
                            wrong.append(f"row {position}: {column} is {y!r}, expected {expected!r}")
                    elif x != y:
                        wrong.append(f"row {position}: {columns[c]} changed from {x!r} to {y!r}")
            if len(after) != len(before):
                wrong.append(f"the table has {len(after)} rows after the change, {len(before)} before")
            checks.append(_fail(label, f"levers.{name}.{column}: the change touched other cells: {wrong[0]}",
                                "a lever may change its column on the selected rows only; see read_case() and solve()")
                          if wrong else _pass(label, f"levers.{name}.{column} changes exactly the selected cells"))
    return checks


def _check_templates(harness: Harness, tables: Path, where: str) -> list[Check]:
    checks = []
    for name, template in harness.kpi_templates.items():
        db = sqlite3.connect(tables)
        clock = evk.guard(db)
        params: dict[str, Any] = {}
        try:
            for param in template.params.values():
                if param.source:
                    row = db.execute(param.source).fetchone()
                    params[param.name] = row[0] if row else None
                elif param.choices:
                    params[param.name] = param.choices[0]
                else:
                    params[param.name] = {"number": 1.0, "int": 1, "text": ""}[param.type]
            value, _ = evk.query_value(db, template.sql, params, clock=clock)
            checks.append(_pass(f"{where}.template.{name}", f"kpi_templates.{name} gives {value:g} with {params}"))
        except (evk.HarnessStop, sqlite3.Error) as exc:
            checks.append(_fail(f"{where}.template.{name}", f"kpi_templates.{name}: {exc}", "fix its SQL, or its params' `from`"))
        finally:
            db.close()
    return checks


def _check_exports(harness: Harness, runner: _Runner, case: Path, where: str) -> list[Check]:
    checks = []
    study = runner.folder / "export.study.json"
    study.write_text(json.dumps(_study_run(harness)), encoding="utf-8")
    values = runner.folder / "export.values.json"
    values.write_text("{}", encoding="utf-8")
    for name in harness.exports:
        out = runner.folder / f"export-{name}.out"
        args = ["export", "--format", name, "--values", values.name, "--study", study.name, "--out", out.name]
        if name == "requests":
            args += ["--case", str(case)]
        done = runner.run(*args)
        checks.append(_pass(f"{where}.export.{name}", f"exports.{name} works") if done.returncode == 0 and out.exists()
                      else _fail(f"{where}.export.{name}", f"exports.{name}: {runner.said(done)}",
                                 "implement it in runner.py (write_case for `requests`, export() for your own)"))
    return checks


def check_harness(folder: str | Path, app: str | None = None, *, samples: int = 3) -> list[Check]:
    """Every check, in order; see the module docstring."""
    folder = Path(folder)
    try:
        harness = load_harness(folder)
    except HarnessError as exc:
        return [_fail("manifest", str(exc), "edit harness.yaml (or the template) at the key it names")]
    checks = [_pass("manifest", f"harness.yaml is valid: {len(harness.settings)} settings, {len(harness.tables)} tables, "
                                f"{len(harness.levers)} levers, {len(harness.kpis)} KPIs, {len(harness.templates)} templates")]
    checks += _trap(harness)
    if app is None:
        if harness.application.kind == "program":
            return checks + [_fail("probe", "a program harness is checked against its program", "give --app PATH")]
        app = sys.executable
    if Path(app).exists():
        app = str(Path(app).resolve())  # the runner works in a scratch folder: a relative path would not reach it
    probe = probe_application(harness, app)
    if not probe.ok:
        return checks + [_fail("probe", probe.message, f"give --app the {harness.application.label}")]
    checks.append(_pass("probe", probe.message))
    with tempfile.TemporaryDirectory(prefix="evolvekit-check-") as scratch:
        runner = _Runner(harness, app, Path(scratch))
        described = runner.run("describe", timeout=60)
        answer = runner.printed(described)
        if described.returncode != 0 or answer is None:
            return checks + [_fail("describe", f"runner.py describe failed: {runner.said(described)}", "run it by hand and fix it")]
        checks.append(_pass("describe", f"runner.py describe answers (SDK {answer.get('sdk')}, Python {answer.get('python')})"))
        checks += _drift(harness, answer.get("discover"))
        cases = sorted(p for p in (harness.root / "samples").glob("*") if p.suffix.lower() in harness.cases.formats)[:samples]
        if not cases:
            return checks + [_warn("samples", "the harness has no sample cases, so nothing was solved",
                                   f"put a few small cases ({', '.join(harness.cases.formats)}) in samples/")]
        limit = max(harness.time_limit.min_s, min(1.0, harness.time_limit.default_s))
        for number, case in enumerate(cases):
            where = f"sample.{case.name}"
            inspected = runner.run("inspect", "--case", str(case), timeout=60)
            if inspected.returncode != 0:
                checks.append(_fail(f"{where}.inspect", f"{case.name} cannot be read: {runner.said(inspected)}", "fix read_case()"))
                continue
            checks.append(_pass(f"{where}.inspect", f"{case.name}: {(runner.printed(inspected) or {}).get('summary')}"))
            done, tables = runner.solve(case, _study_run(harness, extras=True), _extra_values(harness),
                                        f"defaults-{number}", limit)
            if done.returncode != 0:
                checks.append(_fail(f"{where}.solve", f"{case.name}: a solve at the defaults failed: {runner.said(done)}",
                                    "run runner.py solve by hand on the sample and fix what it says"))
                continue
            result = runner.printed(done) or {}
            kpis = result.get("kpis") or {}
            bad = sorted(name for name in harness.kpis if not isinstance(kpis.get(name), (int, float)) or not math.isfinite(kpis[name]))
            checks.append(_fail(f"{where}.kpis", f"{case.name}: KPI(s) {bad} came back missing or not finite", "fix their SQL or measure()")
                          if bad else _pass(f"{where}.kpis", f"{case.name}: every KPI is finite ({len(harness.kpis)}), guardrail {kpis.get('guardrail_violation')}"))
            checks += _tables(harness, Path(str(tables) + ".columns.json"), where)
            checks += _check_templates(harness, tables, where)
            if number == 0:
                checks += _check_levers(harness, runner, case, where, limit, tables)
                checks += _check_exports(harness, runner, case, where)
        rng = random.Random(7)
        failed = []
        for attempt in range(3):
            values = {name: s.parameter.from_unit(rng.random()) for name, s in harness.settings.items()}
            done, _ = runner.solve(cases[0], _study_run(harness, settings=values), values, f"random-{attempt}", limit)
            if done.returncode != 0:
                failed.append(f"{values}: {runner.said(done)}")
        checks.append(_fail("random", f"a random settings vector failed: {failed[0]}",
                            "parametrise so that every value in range is valid (constraints by construction first, "
                            "declared constraints second), or narrow the ranges")
                      if failed else _pass("random", "three random settings vectors solve"))
        shutil.rmtree(scratch, ignore_errors=True)
    return checks


def exit_code(checks: list[Check]) -> int:
    if any(c.severity == "failure" for c in checks):
        return 2
    return 1 if any(c.severity == "warning" for c in checks) else 0


def render(checks: list[Check], *, as_json: bool = False) -> str:
    if as_json:
        return json.dumps({"ok": exit_code(checks) == 0, "checks": [asdict(c) for c in checks]}, indent=2)
    marks = {"pass": "ok  ", "warning": "warn", "failure": "FAIL"}
    lines = []
    for check in checks:
        lines.append(f"{marks[check.severity]} {check.id}: {check.message}")
        if check.fix:
            lines.append(f"       fix: {check.fix}")
    failures = sum(c.severity == "failure" for c in checks)
    warnings = sum(c.severity == "warning" for c in checks)
    lines.append(f"\n{len(checks)} check(s): {failures} failure(s), {warnings} warning(s)")
    return "\n".join(lines)
