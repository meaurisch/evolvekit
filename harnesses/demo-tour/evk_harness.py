"""evk_harness -- the evolvekit harness SDK.

This file is copied into every harness, next to its `runner.py`. It does
everything a harness has in common, so that `runner.py` holds only what is
specific to one application:

    import evk_harness as evk

    def read_case(path): ...                          # -> evk.Case(tables, native)
    def solve(case, settings, time_limit_s, seed): ...  # -> the application's solution
    def solution_tables(case, solution): ...          # -> {table: [row, ...]}
    # optional: write_case, apply_lever, measure, discover, export

    if __name__ == "__main__":
        evk.main(globals())

It is standard library only, and written for Python 3.9 and later, because it
runs under the *application's* Python -- the environment that has the solver
installed -- and never under evolvekit's.

evolvekit runs a harness with one of four subcommands:

    solve    --case F --seed N --values V.json --study S.json [--time-limit T] [--tables-out F]
    inspect  --case F
    describe
    export   --format X --values V.json --study S.json --out F [--case F]

`solve` applies the study's data changes (levers) and settings, checks its
constraints, solves, turns the solution into tables, computes every KPI of the
study with SQL over those tables (or the runner's `measure`), the weighted
sums and the guardrail violation, and prints one JSON object as the last line
of stdout. Exit codes: 0 a result was produced; 2 invalid values, or a
constraint broken on this case, decided before any solving; 3 the case cannot
be read; 1 anything else. An error is one plain sentence as the last line of
stderr; the traceback, when there is one, comes before it.
"""

from __future__ import annotations

import argparse
import ast
import copy
import csv
import json
import math
import os
import sqlite3
import sys
import time
import traceback

SDK_VERSION = 1

EXIT_OK = 0
EXIT_OTHER = 1
EXIT_INVALID = 2
EXIT_CASE = 3

QUERY_LIMIT_S = 2.0
"""How long one SQL query may run before it is stopped."""

APP = sys.executable
"""The application: `--app` for a program harness, this interpreter otherwise."""

SQL_TYPES = {"int": "INTEGER", "float": "REAL", "text": "TEXT", "bool": "INTEGER"}


class HarnessStop(Exception):
    """An error reported as one sentence, with an exit code."""

    code = EXIT_OTHER

    def __init__(self, message, code=None):
        super().__init__(message)
        if code is not None:
            self.code = code


class InvalidValues(HarnessStop):
    """The values, or a constraint on this case: decided before any solving."""

    code = EXIT_INVALID


class CaseError(HarnessStop):
    """The case cannot be read."""

    code = EXIT_CASE


class Case:
    """One case as the harness sees it: named tables of rows (dicts) and
    whatever native object the runner needs to solve it.

    `solve` must build the problem from `tables` for every column a lever
    can change: the SDK changes those cells, never the native object."""

    def __init__(self, tables, native=None, path=None, summary=None):
        self.tables = tables
        self.native = native
        self.path = path
        self.summary = summary
        """One line for `inspect` ("1,200 tasks, 18 vehicle types"); counted
        from the tables when the runner leaves it empty."""


# ---------------------------------------------------------------------------
# tables as a SQLite database
# ---------------------------------------------------------------------------


def _sql_name(name):
    return '"' + str(name).replace('"', '""') + '"'


def _columns_of(rows):
    columns = []
    for row in rows:
        for key in row:
            if key not in columns and not str(key).startswith("_"):
                columns.append(key)
    return columns


def _cell(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)) or value is None:
        return value
    return json.dumps(value)


def add_table(db, name, rows, columns=None):
    """Create table `name` and insert `rows`. `columns` ({column: type}) fixes
    the columns and their types; without it they come from the rows. Keys
    starting with `_` are private to the runner and never become columns."""
    if columns is None:
        columns = {column: "" for column in _columns_of(rows)}
    if not columns:
        columns = {"_empty": ""}
    definition = ", ".join(
        "{} {}".format(_sql_name(column), SQL_TYPES.get(kind, "")) for column, kind in columns.items()
    )
    db.execute("CREATE TABLE {} ({})".format(_sql_name(name), definition))
    names = list(columns)
    placeholders = ", ".join("?" for _ in names)
    db.executemany(
        "INSERT INTO {} VALUES ({})".format(_sql_name(name), placeholders),
        [tuple(_cell(row.get(column)) for column in names) for row in rows],
    )


def build_database(tables, declared=None, originals=None):
    """An in-memory database with every table of `tables`, typed by `declared`
    ({table: {column: type}}) where it declares them, and an `orig_<name>`
    copy of every table in `originals` -- the untouched request, which a KPI
    compares the changed one with."""
    db = sqlite3.connect(":memory:")
    declared = declared or {}
    for name, rows in tables.items():
        add_table(db, name, rows, declared.get(name))
    for name, rows in (originals or {}).items():
        add_table(db, "orig_" + name, rows, declared.get(name))
    db.commit()
    return db


_ALLOWED_ACTIONS = {
    getattr(sqlite3, "SQLITE_SELECT", 21),
    getattr(sqlite3, "SQLITE_READ", 20),
    getattr(sqlite3, "SQLITE_FUNCTION", 31),
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}


def _read_only(action, arg1, arg2, database, source):
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def guard(db, limit_s=QUERY_LIMIT_S):
    """From now on `db` only reads, and a statement that runs longer than
    `limit_s` is stopped. There is no way back for this connection."""
    db.set_authorizer(_read_only)
    started = {"at": time.monotonic()}

    def progress():
        return 1 if time.monotonic() - started["at"] > limit_s else 0

    db.set_progress_handler(progress, 1000)
    return started


def query_value(db, sql, params=None, clock=None, limit_s=QUERY_LIMIT_S):
    """The first column of the first row of `sql`, as a float, and a note.

    `NULL` (or no row at all) counts as 0, and the note says so. A query
    that writes, attaches, or runs longer than `limit_s` is refused."""
    if clock is not None:
        clock["at"] = time.monotonic()
    try:
        row = db.execute(sql, params or {}).fetchone()
    except sqlite3.OperationalError as exc:
        if "interrupt" in str(exc).lower():
            raise HarnessStop("the query ran longer than {:g} s and was stopped".format(limit_s))
        raise HarnessStop("the query failed: {}".format(exc))
    except sqlite3.DatabaseError as exc:
        text = str(exc)
        if "not authorized" in text or "prohibited" in text:
            raise HarnessStop("the query may only read the tables: {}".format(text))
        raise HarnessStop("the query failed: {}".format(text))
    if row is None or row[0] is None:
        return 0.0, "no value (NULL), counted as 0"
    value = row[0]
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            raise HarnessStop("the query returned the text {!r}, not a number".format(value[:40]))
    if isinstance(value, bytes):
        raise HarnessStop("the query returned bytes, not a number")
    value = float(value)
    if not math.isfinite(value):
        raise HarnessStop("the query returned {!r}, not a finite number".format(value))
    return value, None


def select_rows(table, rows, where, columns=None):
    """Indices of the `rows` of `table` that the SQL condition `where` selects
    (all of them without one)."""
    if not where or not str(where).strip():
        return list(range(len(rows)))
    db = build_database({table: rows}, {table: columns} if columns else None)
    guard(db)
    try:
        found = db.execute(
            "SELECT rowid FROM {} WHERE {} ORDER BY rowid".format(_sql_name(table), where)
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise HarnessStop("the selection {!r} failed on table {}: {}".format(where, table, exc))
    finally:
        db.close()
    return [int(rowid) - 1 for (rowid,) in found]


# ---------------------------------------------------------------------------
# data changes (levers)
# ---------------------------------------------------------------------------


def _changed(old, value, mode):
    if mode == "set":
        return value
    if old is None:
        return None
    if mode == "scale":
        return old * value
    if mode == "add":
        return old + value
    raise HarnessStop("unknown lever mode {!r}".format(mode))


def apply_levers(case, levers, values, hooks, declared=None):
    """Change the case as the study's lever instances say, for `values`.

    Returns notes: a lever instance that selects no row of this case changes
    nothing here, and says so. A lever with `code: true` is applied by the
    runner's `apply_lever(case, lever, rows, value)` instead of a column edit."""
    declared = declared or {}
    notes = []
    for name, spec in levers.items():
        if name not in values:
            continue
        value = values[name]
        table = spec["table"]
        rows = case.tables.get(table)
        if rows is None:
            raise HarnessStop("the data change {!r} needs table {!r}, and this case has none".format(name, table))
        indices = select_rows(table, rows, spec.get("where"), declared.get(table))
        if not indices:
            notes.append("{} selects no row of {} in this case".format(name, table))
            continue
        if spec.get("code"):
            hook = hooks.get("apply_lever")
            if hook is None:
                raise HarnessStop("the lever {!r} is applied in code, and runner.py has no apply_lever()".format(spec["lever"]))
            hook(case, spec["lever"], [rows[i] for i in indices], value)
            continue
        column = spec["column"]
        for index in indices:
            new = _changed(rows[index].get(column), value, spec.get("mode", "set"))
            if new is not None and spec.get("integer"):
                new = int(round(new))
            rows[index][column] = new
    return notes


# ---------------------------------------------------------------------------
# the goal's arithmetic
# ---------------------------------------------------------------------------


def weighted_sum(weights, values, directions):
    """A weighted sum in natural units, lower is better: a lower-is-better
    component adds `weight * value`, a higher-is-better one subtracts it."""
    total = 0.0
    for name, weight in weights.items():
        sign = 1.0 if directions.get(name, "lower") == "lower" else -1.0
        total += sign * float(weight) * float(values[name])
    return total


def guardrail_violation(guardrails, values):
    """The sum of every guardrail's relative violation on this case: for
    `max: x`, max(0, value - x) / max(|x|, 1); for `min: x`, the mirror image.
    0 when every guardrail holds."""
    total = 0.0
    for rail in guardrails:
        value = float(values[rail["kpi"]])
        if rail.get("max") is not None:
            bound = float(rail["max"])
            total += max(0.0, value - bound) / max(abs(bound), 1.0)
        if rail.get("min") is not None:
            bound = float(rail["min"])
            total += max(0.0, bound - value) / max(abs(bound), 1.0)
    return total


def broken_guardrails(guardrails, values):
    """One phrase per guardrail that does not hold: "missed_required = 2 > 0"."""
    broken = []
    for rail in guardrails:
        value = float(values[rail["kpi"]])
        if rail.get("max") is not None and value > float(rail["max"]):
            broken.append("{} = {:g} > {:g}".format(rail["kpi"], value, float(rail["max"])))
        if rail.get("min") is not None and value < float(rail["min"]):
            broken.append("{} = {:g} < {:g}".format(rail["kpi"], value, float(rail["min"])))
    return broken


# ---------------------------------------------------------------------------
# the expression language (a copy of evolvekit/expressions.py)
# ---------------------------------------------------------------------------

FUNCTIONS = ("abs", "max", "min", "round")


class ExpressionError(ValueError):
    """An expression that cannot be read, or cannot be computed."""


_EXPR_ALLOWED = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Call, ast.Name, ast.Load, ast.Constant,
)


class Expression:
    """A condition over named values: numbers, 'text', + - * / ** %, chained
    comparisons, and / or / not, abs / min / max / round. Read with `ast`
    against a whitelist and computed by walking the tree -- never `eval`."""

    def __init__(self, text, names, tree):
        self.text = text
        self.names = names
        self._tree = tree

    @staticmethod
    def parse(text):
        if not isinstance(text, str) or not text.strip():
            raise ExpressionError("the expression is empty")
        source = text.strip()
        try:
            tree = ast.parse(source, mode="eval")
        except SyntaxError as exc:
            raise ExpressionError("not a valid expression: {}".format(exc.msg))
        functions = set()
        for node in ast.walk(tree):
            if not isinstance(node, _EXPR_ALLOWED):
                raise ExpressionError("{} is not allowed in a condition".format(type(node).__name__))
            if isinstance(node, ast.Constant) and not isinstance(node.value, (bool, int, float, str)):
                raise ExpressionError("the literal {!r} is not allowed".format(node.value))
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                    raise ExpressionError("only abs, max, min and round can be called")
                if node.keywords:
                    raise ExpressionError("{}() takes no keyword arguments here".format(node.func.id))
                functions.add(id(node.func))
        names = frozenset(
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and id(node) not in functions
        )
        return Expression(source, names, tree)

    def evaluate(self, values):
        return _evaluate(self._tree.body, values)

    def holds(self, values):
        result = self.evaluate(values)
        if not isinstance(result, bool):
            raise ExpressionError("is not a condition: it gives {!r}, not true or false".format(result))
        return result


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, complex)


def _number(value, what):
    if not _is_number(value):
        raise ExpressionError("{} needs a number, got {!r}".format(what, value))
    return value


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ExpressionError("the arithmetic overflowed")
    return value


def _evaluate(node, values):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in values:
            raise ExpressionError("{!r} has no value".format(node.id))
        return values[node.id]
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for operand in node.values:
                if not _evaluate(operand, values):
                    return False
            return True
        for operand in node.values:
            if _evaluate(operand, values):
                return True
        return False
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate(node.operand, values)
        if isinstance(node.op, ast.Not):
            return not operand
        number = _number(operand, "a sign")
        return -number if isinstance(node.op, ast.USub) else +number
    if isinstance(node, ast.BinOp):
        return _arithmetic(node.op, _evaluate(node.left, values), _evaluate(node.right, values))
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, values)
        for op, comparator in zip(node.ops, node.comparators):
            right = _evaluate(comparator, values)
            if not _compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        return _call(node.func.id, [_evaluate(arg, values) for arg in node.args])
    raise ExpressionError("{} is not allowed in a condition".format(type(node).__name__))


def _arithmetic(op, left, right):
    a, b = _number(left, "arithmetic"), _number(right, "arithmetic")
    try:
        if isinstance(op, ast.Add):
            return _finite(a + b)
        if isinstance(op, ast.Sub):
            return _finite(a - b)
        if isinstance(op, ast.Mult):
            return _finite(a * b)
        if isinstance(op, ast.Div):
            return _finite(a / b)
        if isinstance(op, ast.Mod):
            return _finite(a % b)
        result = float(a) ** float(b)
    except ZeroDivisionError:
        raise ExpressionError("division by zero")
    except OverflowError:
        raise ExpressionError("a number too large to compute")
    if isinstance(result, complex):
        raise ExpressionError("a negative number to a fractional power has no real value")
    return _finite(result)


def _compare(op, left, right):
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if not ((_is_number(left) and _is_number(right)) or (isinstance(left, str) and isinstance(right, str))):
        raise ExpressionError("cannot order {!r} and {!r}".format(left, right))
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    return left >= right


def _call(name, args):
    if name == "abs":
        if len(args) != 1:
            raise ExpressionError("abs() takes one number")
        return abs(_number(args[0], "abs()"))
    if name in ("min", "max"):
        if len(args) < 2:
            raise ExpressionError("{}() takes two or more numbers".format(name))
        numbers = [_number(arg, name + "()") for arg in args]
        return min(numbers) if name == "min" else max(numbers)
    if len(args) not in (1, 2):
        raise ExpressionError("round() takes a number and, optionally, how many digits")
    number = _number(args[0], "round()")
    if len(args) == 1:
        return round(number)
    if isinstance(args[1], bool) or not isinstance(args[1], int):
        raise ExpressionError("round()'s digits must be a whole number")
    return round(number, args[1])


# ---------------------------------------------------------------------------
# the subcommands
# ---------------------------------------------------------------------------


def _load_json(path, what):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise HarnessStop("cannot read the {} {}: {}".format(what, path, exc.strerror or exc))
    except ValueError as exc:
        raise HarnessStop("the {} {} is not valid JSON: {}".format(what, path, exc))


def _hook(hooks, name):
    hook = hooks.get(name)
    if hook is None or not callable(hook):
        raise HarnessStop("runner.py defines no {}()".format(name))
    return hook


def read(hooks, path):
    """The runner's `read_case`, with every failure as a sentence (exit 3)."""
    if not os.path.isfile(path):
        raise CaseError("there is no case file at {}".format(path))
    try:
        case = _hook(hooks, "read_case")(path)
    except HarnessStop:
        raise
    except Exception as exc:  # noqa: BLE001 - any failure to read is "cannot be read"
        traceback.print_exc(file=sys.stderr)
        raise CaseError("cannot read {}: {}".format(os.path.basename(path), exc))
    if not isinstance(case, Case):
        raise HarnessStop("read_case() must return an evk.Case, got {}".format(type(case).__name__))
    if case.path is None:
        case.path = path
    return case


def _summary(case):
    if case.summary:
        return str(case.summary)
    parts = []
    for name, rows in case.tables.items():
        if rows:
            parts.append("{:,} {}".format(len(rows), name.replace("_", " ")))
    return ", ".join(parts) or "an empty case"


def _settings(study, values):
    settings = dict(study.get("settings", {}).get("base") or {})
    for name in study.get("settings", {}).get("tuned") or []:
        if name in values:
            settings[name] = values[name]
    return settings


def _check_constraints(study, values, case, declared):
    """Every constraint of the study, before any solving (exit 2 when one breaks)."""
    sql_checks = []
    for constraint in study.get("constraints") or []:
        says = constraint.get("says") or constraint.get("expr") or constraint.get("sql")
        if constraint.get("expr"):
            try:
                holds = Expression.parse(constraint["expr"]).holds(values)
            except ExpressionError as exc:
                raise InvalidValues('cannot check the constraint "{}": {}'.format(says, exc))
            if not holds:
                raise InvalidValues('the values break the constraint "{}"'.format(says))
        elif constraint.get("sql"):
            sql_checks.append((says, constraint["sql"]))
    if not sql_checks:
        return
    db = build_database(case.tables, declared)
    clock = guard(db)
    try:
        for says, sql in sql_checks:
            value, _ = query_value(db, sql, clock=clock)
            if not value:
                raise InvalidValues('the changed case breaks the constraint "{}"'.format(says))
    finally:
        db.close()


def _kpis(study, db, clock, measured):
    values, notes = {}, []
    kpis = study.get("kpis") or {}
    for name, kpi in kpis.items():
        if "weighted" in kpi:
            continue
        if kpi.get("measure"):
            if name not in measured:
                raise HarnessStop("measure() reported no {!r}".format(name))
            value = float(measured[name])
            if not math.isfinite(value):
                raise HarnessStop("measure() reported {!r} for {!r}, not a finite number".format(value, name))
        else:
            try:
                value, note = query_value(db, kpi["sql"], kpi.get("params"), clock=clock)
            except HarnessStop as exc:
                raise HarnessStop("KPI {!r}: {}".format(name, exc))
            if note:
                notes.append("{}: {}".format(name, note))
        values[name] = value
    directions = {name: kpi.get("direction", "lower") for name, kpi in kpis.items()}
    for name, kpi in kpis.items():
        if "weighted" in kpi:
            values[name] = weighted_sum(kpi["weighted"], values, directions)
    return values, notes


def _feedback(values, study, notes, broken):
    shown = [
        "{} {:.6g}".format(name, values[name])
        for name in (study.get("kpis") or {})
        if name in values
    ][:6]
    parts = ["; ".join(shown)] if shown else []
    if study.get("guardrails"):
        parts.append("guardrails broken: " + ", ".join(broken) if broken else "guardrails hold")
    parts.extend(notes[:3])
    return ". ".join(parts)


def run_solve(hooks, args):
    study = _load_json(args.study, "study file")
    values = _load_json(args.values, "values file") if args.values else {}
    if not isinstance(values, dict):
        raise InvalidValues("the values file must hold a JSON object of tunable names and values")
    declared = study.get("tables") or {}
    case = read(hooks, args.case)
    originals = copy.deepcopy(case.tables)
    notes = apply_levers(case, study.get("levers") or {}, values, hooks, declared)
    _check_constraints(study, values, case, declared)

    settings = _settings(study, values)
    time_limit = float(args.time_limit) if args.time_limit is not None else float(study.get("time_limit_s") or 0) or None
    started = time.perf_counter()
    solution = _hook(hooks, "solve")(case, settings, time_limit, int(args.seed))
    solve_s = time.perf_counter() - started
    solved = _hook(hooks, "solution_tables")(case, solution)
    if not isinstance(solved, dict):
        raise HarnessStop("solution_tables() must return a dict of table name to rows")

    tables = dict(case.tables)
    tables.update(solved)
    db = build_database(tables, declared, originals)
    if args.tables_out:
        target = sqlite3.connect(args.tables_out)
        db.backup(target)
        target.close()
        # The columns the runner produced, before the declaration typed them:
        # `harness check` compares the two.
        with open(args.tables_out + ".columns.json", "w", encoding="utf-8") as handle:
            json.dump({name: _columns_of(rows) for name, rows in tables.items()}, handle)
    measured = {}
    if any(kpi.get("measure") for kpi in (study.get("kpis") or {}).values()):
        measured = _hook(hooks, "measure")(case, solution, tables) or {}
    clock = guard(db)
    try:
        values_out, kpi_notes = _kpis(study, db, clock, measured)
    finally:
        db.close()
    guardrails = study.get("guardrails") or []
    broken = broken_guardrails(guardrails, values_out)
    values_out["guardrail_violation"] = guardrail_violation(guardrails, values_out)
    values_out["solve_s"] = round(solve_s, 3)
    feedback = _feedback(values_out, study, notes + kpi_notes, broken)
    print(json.dumps({"kpis": values_out, "text_feedback": feedback}, allow_nan=False))
    return EXIT_OK


def run_inspect(hooks, args):
    try:
        case = read(hooks, args.case)
    except HarnessStop as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        raise
    counts = {name: len(rows) for name, rows in case.tables.items()}
    print(json.dumps({"ok": True, "summary": _summary(case), "tables": counts}))
    return EXIT_OK


def run_describe(hooks, args):
    discover = hooks.get("discover")
    found = discover() if callable(discover) else None
    print(json.dumps({
        "sdk": SDK_VERSION,
        "python": sys.version.split()[0],
        "app": APP,
        "discover": found,
    }, default=str))
    return EXIT_OK


def _data_changes(study, values):
    changes = []
    for name, spec in (study.get("levers") or {}).items():
        if name in values:
            changes.append({
                "change": name, "lever": spec["lever"], "table": spec["table"],
                "column": spec.get("column"), "rows": spec.get("where") or "all",
                "mode": spec.get("mode"), "value": values[name],
            })
    return changes


def run_export(hooks, args):
    study = _load_json(args.study, "study file")
    values = _load_json(args.values, "values file")
    fmt = args.format
    if fmt == "settings_json":
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(_settings(study, values), handle, indent=2, sort_keys=True)
            handle.write("\n")
    elif fmt == "settings_flags":
        tokens = []
        for name, value in _settings(study, values).items():
            tokens += ["--" + name.replace("_", "-"), ("true" if value else "false") if isinstance(value, bool) else str(value)]
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(" ".join(tokens) + "\n")
    elif fmt == "data_changes":
        changes = _data_changes(study, values)
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(changes, handle, indent=2)
            handle.write("\n")
        with open(os.path.splitext(args.out)[0] + ".csv", "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["change", "lever", "table", "column", "rows", "mode", "value"])
            writer.writeheader()
            writer.writerows(changes)
    elif fmt == "requests":
        if not args.case:
            raise HarnessStop("--format requests needs --case: the request to change")
        case = read(hooks, args.case)
        apply_levers(case, study.get("levers") or {}, values, hooks, study.get("tables") or {})
        _hook(hooks, "write_case")(case, args.out)
    else:
        hook = hooks.get("export")
        if not callable(hook):
            raise HarnessStop("unknown export format {!r}, and runner.py has no export()".format(fmt))
        hook(fmt, values, args.out)
    return EXIT_OK


def _parser():
    parser = argparse.ArgumentParser(prog="runner.py", description="An evolvekit harness.")
    parser.add_argument("--app", default=None, help="the application, for a program harness")
    sub = parser.add_subparsers(dest="command", required=True)
    solve = sub.add_parser("solve")
    solve.add_argument("--case", required=True)
    solve.add_argument("--seed", default="0")
    solve.add_argument("--values", default=None)
    solve.add_argument("--study", required=True)
    solve.add_argument("--time-limit", dest="time_limit", default=None)
    solve.add_argument("--tables-out", dest="tables_out", default=None)
    solve.add_argument("--app", dest="app_late", default=None)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--case", required=True)
    inspect.add_argument("--app", dest="app_late", default=None)
    describe = sub.add_parser("describe")
    describe.add_argument("--app", dest="app_late", default=None)
    export = sub.add_parser("export")
    export.add_argument("--format", required=True)
    export.add_argument("--values", required=True)
    export.add_argument("--study", required=True)
    export.add_argument("--out", required=True)
    export.add_argument("--case", default=None)
    export.add_argument("--app", dest="app_late", default=None)
    return parser


def main(hooks, argv=None):
    """Run the subcommand on the command line against the runner's `hooks`
    (its `globals()`), and exit with its code."""
    global APP
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        sys.exit(EXIT_OTHER if exc.code else EXIT_OK)
    APP = args.app_late or args.app or sys.executable
    commands = {"solve": run_solve, "inspect": run_inspect, "describe": run_describe, "export": run_export}
    try:
        code = commands[args.command](hooks, args)
    except HarnessStop as exc:
        sys.stdout.flush()
        sys.stderr.write(str(exc) + "\n")
        code = exc.code
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        code = 130
    except Exception as exc:  # noqa: BLE001 - one sentence last, the traceback before it
        traceback.print_exc(file=sys.stderr)
        sys.stderr.write("error: {}: {}\n".format(type(exc).__name__, exc))
        code = EXIT_OTHER
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(code)
