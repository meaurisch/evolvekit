"""`study.yaml`: one analysis on top of a harness.

A study says what may change (settings to tune or keep fixed, data changes to
try), under which constraints, what counts as success (KPIs, a goal in one of
three forms, guardrails), and how much time to spend. It lives in one folder
with relative paths only -- the application's path is the one exception, and
is checked again whenever the study is opened:

    study.yaml   harness/   cases/   inputs/   preview/   assistant.jsonl   runs/

`Study.parse` checks the document's shape; `study_problems` checks it against
its harness and says, in sentences naming the key, what is missing or wrong
-- the app shows them step by step, and a study with any is not run.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

import yaml

from evolvekit.config import MAX_LEVELS
from evolvekit.expressions import Expression, ExpressionError
from evolvekit.harness import HarnessError
from evolvekit.harness.manifest import Harness
from evolvekit.harness.sql import sql_problem, where_problem

__all__ = [
    "STUDY_FORMAT",
    "Budget",
    "Constraint",
    "DataChange",
    "GoalLevel",
    "Guardrail",
    "Limits",
    "SettingChoice",
    "Study",
    "StudyKpi",
    "check_templates",
    "load_study",
    "parse_within",
    "save_study",
    "study_from_template",
    "study_problems",
]

STUDY_FORMAT = 1
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise HarnessError(f"{path}: expected a mapping, got {type(value).__name__}")
    return dict(value)


def _known(data: Mapping[str, Any], known: set[str], path: str) -> None:
    unknown = sorted(set(data) - known)
    if unknown:
        raise HarnessError(f"{path}: unknown key(s) {unknown}; known keys are {sorted(known)}")


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HarnessError(f"{path}: expected a number, got {value!r}")
    return float(value)


def _name(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _NAME.match(value):
        raise HarnessError(f"{path}: a name must be letters, digits and underscores, got {value!r}")
    return value


def parse_within(text: Any) -> tuple[float, bool] | None:
    """`"1 %"` -> (0.01, relative); `0.5` or `"0.5"` -> (0.5, absolute); `None`
    when there is no tolerance. Raises `ValueError` on anything else."""
    if text is None:
        return None
    if isinstance(text, bool):
        raise ValueError(f"expected a tolerance such as '1 %' or 0.5, got {text!r}")
    if isinstance(text, (int, float)):
        value, relative = float(text), False
    else:
        raw = str(text).strip()
        relative = raw.endswith("%")
        try:
            value = float(raw.rstrip("%").strip())
        except ValueError:
            raise ValueError(f"expected a tolerance such as '1 %' or 0.5, got {text!r}") from None
        if relative:
            value /= 100.0
    if not value > 0:
        raise ValueError(f"a tolerance must be above 0, got {text!r}")
    return value, relative


# ---------------------------------------------------------------------------
# the parts
# ---------------------------------------------------------------------------


@dataclass
class SettingChoice:
    """What a study does with one setting: `tune` it (within the harness's
    range, or a narrower one, from a start), keep it `fixed` at a value, or
    leave it at its `default` (the input settings file's value, else the
    harness's)."""

    mode: str
    low: float | None = None
    high: float | None = None
    start: Any = None
    value: Any = None

    def to_yaml(self) -> Any:
        if self.mode == "tune" and self.low is None and self.high is None and self.start is None:
            return "tune"
        if self.mode == "tune":
            return {"tune": True, **{k: v for k, v in (("low", self.low), ("high", self.high), ("start", self.start)) if v is not None}}
        if self.mode == "fixed":
            return {"fixed": self.value}
        return "default"

    @staticmethod
    def parse(raw: Any, path: str) -> "SettingChoice":
        if raw in ("tune", True):
            return SettingChoice("tune")
        if raw in ("default", None):
            return SettingChoice("default")
        data = _mapping(raw, path)
        _known(data, {"tune", "low", "high", "start", "fixed"}, path)
        if "fixed" in data:
            if data.get("tune"):
                raise HarnessError(f"{path}: a setting is tuned or fixed, not both")
            return SettingChoice("fixed", value=data["fixed"])
        if data.get("tune") is not True:
            raise HarnessError(f"{path}: expected `tune`, `default`, {{fixed: value}} or {{tune: true, low, high, start}}")
        return SettingChoice(
            "tune",
            low=None if data.get("low") is None else _number(data["low"], f"{path}.low"),
            high=None if data.get("high") is None else _number(data["high"], f"{path}.high"),
            start=data.get("start"),
        )


@dataclass
class DataChange:
    """A lever instance: which lever, which column, which rows (an SQL
    condition over the lever's table), how (scale / set / add), and the range
    the search may try, from a start."""

    lever: str
    mode: str
    low: float
    high: float
    start: float
    column: str = ""
    where: str = ""
    says: str = ""

    @staticmethod
    def parse(raw: Any, path: str) -> "DataChange":
        data = _mapping(raw, path)
        _known(data, {"lever", "column", "where", "mode", "low", "high", "start", "says"}, path)
        for key in ("lever", "mode", "low", "high"):
            if data.get(key) is None:
                raise HarnessError(f"{path}.{key}: required")
        low, high = _number(data["low"], f"{path}.low"), _number(data["high"], f"{path}.high")
        start = data.get("start")
        return DataChange(
            lever=str(data["lever"]), mode=str(data["mode"]), low=low, high=high,
            start=_number(start, f"{path}.start") if start is not None else (1.0 if data["mode"] == "scale" and low <= 1 <= high else low),
            column=str(data.get("column") or ""), where=str(data.get("where") or "").strip(),
            says=str(data.get("says") or ""),
        )

    def to_yaml(self) -> dict[str, Any]:
        out: dict[str, Any] = {"lever": self.lever}
        if self.column:
            out["column"] = self.column
        if self.where:
            out["where"] = self.where
        out.update({"mode": self.mode, "low": self.low, "high": self.high, "start": self.start})
        if self.says:
            out["says"] = self.says
        return out


@dataclass
class Constraint:
    says: str
    expr: str = ""
    sql: str = ""

    @staticmethod
    def parse(raw: Any, path: str) -> "Constraint":
        data = _mapping(raw, path)
        _known(data, {"says", "expr", "sql"}, path)
        expr, sql = str(data.get("expr") or "").strip(), str(data.get("sql") or "").strip()
        if bool(expr) == bool(sql):
            raise HarnessError(f"{path}: give `expr` (over the tuned values) or `sql` (over the changed request), one of them")
        says = str(data.get("says") or expr or sql).strip()
        return Constraint(says=" ".join(says.split()), expr=expr, sql=sql)

    def to_yaml(self) -> dict[str, Any]:
        return {"says": self.says, **({"expr": self.expr} if self.expr else {"sql": self.sql})}


@dataclass
class StudyKpi:
    """A KPI of the study: ready-made (`from: harness`), from a KPI template,
    written in SQL, or a weighted sum of other KPIs of the study."""

    kind: str
    """`harness`, `template`, `sql` or `weighted`."""
    says: str = ""
    direction: str = "lower"
    unit: str = ""
    sql: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    template: str = ""
    weighted: dict[str, float] = field(default_factory=dict)
    rows_sql: str = ""
    """For "show which rows count": SQL listing the rows the KPI counts."""

    @staticmethod
    def parse(raw: Any, path: str) -> "StudyKpi":
        data = _mapping(raw, path)
        _known(data, {"from", "template", "params", "says", "direction", "unit", "sql", "weighted", "rows_sql"}, path)
        kinds = [k for k in ("from", "template", "sql", "weighted") if data.get(k) is not None]
        if len(kinds) != 1:
            raise HarnessError(f"{path}: give one of `from: harness`, `template`, `sql` or `weighted`, got {kinds or 'none'}")
        direction = str(data.get("direction") or "lower")
        if direction not in ("lower", "higher"):
            raise HarnessError(f"{path}.direction: must be 'lower' or 'higher', got {direction!r}")
        if kinds[0] == "from":
            if data["from"] != "harness":
                raise HarnessError(f"{path}.from: the only source is `harness`, got {data['from']!r}")
            return StudyKpi(kind="harness")
        if kinds[0] == "template":
            return StudyKpi(kind="template", template=str(data["template"]), params=_mapping(data.get("params"), f"{path}.params"))
        if kinds[0] == "weighted":
            weights = _mapping(data["weighted"], f"{path}.weighted")
            if not weights:
                raise HarnessError(f"{path}.weighted: name at least one KPI and its weight")
            return StudyKpi(
                kind="weighted", says=str(data.get("says") or ""), direction="lower",
                weighted={str(k): _number(v, f"{path}.weighted.{k}") for k, v in weights.items()},
                unit=str(data.get("unit") or ""),
            )
        return StudyKpi(
            kind="sql", sql=str(data["sql"]).strip(), says=str(data.get("says") or ""), direction=direction,
            unit=str(data.get("unit") or ""), rows_sql=str(data.get("rows_sql") or "").strip(),
        )

    def to_yaml(self) -> dict[str, Any]:
        if self.kind == "harness":
            return {"from": "harness"}
        if self.kind == "template":
            return {"template": self.template, "params": dict(self.params)}
        if self.kind == "weighted":
            return {"says": self.says, "direction": "lower", "weighted": dict(self.weighted), **({"unit": self.unit} if self.unit else {})}
        out = {"says": self.says, "direction": self.direction, "sql": self.sql}
        if self.unit:
            out["unit"] = self.unit
        if self.rows_sql:
            out["rows_sql"] = self.rows_sql
        return out


@dataclass
class GoalLevel:
    kpi: str
    direction: str
    equal_within: Any = None
    """`"1 %"` (relative to the starting point) or a number in the KPI's units;
    required on every level but the last."""


@dataclass
class Guardrail:
    kpi: str
    max: float | None = None
    min: float | None = None


@dataclass
class Limits:
    time_per_case_s: float = 60.0
    retries: int = 1
    runs_per_case: int = 1


@dataclass
class Budget:
    hours: float = 2.0
    ai_enabled: bool = False
    ai_max_usd: float = 2.0


@dataclass
class Study:
    name: str
    harness_id: str
    harness_version: str
    template: str = ""
    application_path: str = ""
    application_version: str = ""
    training: list[str] = field(default_factory=list)
    test: list[str] = field(default_factory=list)
    inputs: dict[str, str] = field(default_factory=dict)
    settings: dict[str, SettingChoice] = field(default_factory=dict)
    data: dict[str, DataChange] = field(default_factory=dict)
    constraints: list[Constraint] = field(default_factory=list)
    kpis: dict[str, StudyKpi] = field(default_factory=dict)
    goal: list[GoalLevel] = field(default_factory=list)
    guardrails: list[Guardrail] = field(default_factory=list)
    limits: Limits = field(default_factory=Limits)
    budget: Budget = field(default_factory=Budget)
    plan: dict[str, Any] = field(default_factory=lambda: {"auto": True})
    step: int = 1
    """The wizard step the study was last saved on (the app's "draft · step 4 of 7")."""

    # -- reading and writing -----------------------------------------------

    @staticmethod
    def parse(raw: Any) -> "Study":
        data = _mapping(raw, "<study>")
        _known(
            data,
            {"study", "name", "harness", "template", "application", "cases", "inputs", "vary", "constraints",
             "kpis", "goal", "guardrails", "limits", "budget", "plan", "step"},
            "<study>",
        )
        if data.get("study") != STUDY_FORMAT:
            raise HarnessError(f"study: the format version must be {STUDY_FORMAT}, got {data.get('study')!r}")
        harness = _mapping(data.get("harness"), "harness")
        _known(harness, {"id", "version"}, "harness")
        application = _mapping(data.get("application"), "application")
        _known(application, {"path", "version"}, "application")
        cases = _mapping(data.get("cases"), "cases")
        _known(cases, {"training", "test"}, "cases")
        vary = _mapping(data.get("vary"), "vary")
        _known(vary, {"settings", "data"}, "vary")
        goal = _mapping(data.get("goal"), "goal")
        _known(goal, {"levels"}, "goal")
        limits = _mapping(data.get("limits"), "limits")
        _known(limits, {"time_per_case_s", "retries", "runs_per_case"}, "limits")
        budget = _mapping(data.get("budget"), "budget")
        _known(budget, {"hours", "ai"}, "budget")
        ai = _mapping(budget.get("ai"), "budget.ai")
        _known(ai, {"enabled", "max_usd"}, "budget.ai")
        levels = []
        for index, raw_level in enumerate(goal.get("levels") or []):
            path = f"goal.levels[{index}]"
            level = _mapping(raw_level, path)
            _known(level, {"kpi", "direction", "equal_within"}, path)
            levels.append(GoalLevel(kpi=str(level.get("kpi") or ""), direction=str(level.get("direction") or "lower"),
                                    equal_within=level.get("equal_within")))
        guardrails = []
        for index, raw_rail in enumerate(data.get("guardrails") or []):
            path = f"guardrails[{index}]"
            rail = _mapping(raw_rail, path)
            _known(rail, {"kpi", "max", "min"}, path)
            guardrails.append(Guardrail(
                kpi=str(rail.get("kpi") or ""),
                max=None if rail.get("max") is None else _number(rail["max"], f"{path}.max"),
                min=None if rail.get("min") is None else _number(rail["min"], f"{path}.min"),
            ))
        return Study(
            name=str(data.get("name") or "").strip(),
            harness_id=str(harness.get("id") or ""),
            harness_version=str(harness.get("version") or ""),
            template=str(data.get("template") or ""),
            application_path=str(application.get("path") or ""),
            application_version=str(application.get("version") or ""),
            training=[str(p) for p in cases.get("training") or []],
            test=[str(p) for p in cases.get("test") or []],
            inputs={str(k): str(v) for k, v in _mapping(data.get("inputs"), "inputs").items() if v},
            settings={
                _name(k, f"vary.settings.{k}"): SettingChoice.parse(v, f"vary.settings.{k}")
                for k, v in _mapping(vary.get("settings"), "vary.settings").items()
            },
            data={
                _name(k, f"vary.data.{k}"): DataChange.parse(v, f"vary.data.{k}")
                for k, v in _mapping(vary.get("data"), "vary.data").items()
            },
            constraints=[Constraint.parse(c, f"constraints[{i}]") for i, c in enumerate(data.get("constraints") or [])],
            kpis={_name(k, f"kpis.{k}"): StudyKpi.parse(v, f"kpis.{k}") for k, v in _mapping(data.get("kpis"), "kpis").items()},
            goal=levels,
            guardrails=guardrails,
            limits=Limits(
                time_per_case_s=_number(limits.get("time_per_case_s", 60), "limits.time_per_case_s"),
                retries=int(_number(limits.get("retries", 1), "limits.retries")),
                runs_per_case=int(_number(limits.get("runs_per_case", 1), "limits.runs_per_case")),
            ),
            budget=Budget(
                hours=_number(budget.get("hours", 2.0), "budget.hours"),
                ai_enabled=bool(ai.get("enabled", False)),
                ai_max_usd=_number(ai.get("max_usd", 2.0), "budget.ai.max_usd"),
            ),
            plan=_mapping(data.get("plan"), "plan") or {"auto": True},
            step=int(_number(data.get("step", 1), "step")),
        )

    def to_yaml(self) -> dict[str, Any]:
        return {
            "study": STUDY_FORMAT,
            "name": self.name,
            "harness": {"id": self.harness_id, "version": self.harness_version},
            **({"template": self.template} if self.template else {}),
            "application": {"path": self.application_path, "version": self.application_version},
            "cases": {"training": list(self.training), "test": list(self.test)},
            "inputs": dict(self.inputs),
            "vary": {
                "settings": {k: v.to_yaml() for k, v in self.settings.items()},
                "data": {k: v.to_yaml() for k, v in self.data.items()},
            },
            "constraints": [c.to_yaml() for c in self.constraints],
            "kpis": {k: v.to_yaml() for k, v in self.kpis.items()},
            "goal": {"levels": [
                {"kpi": level.kpi, "direction": level.direction,
                 **({"equal_within": level.equal_within} if level.equal_within is not None else {})}
                for level in self.goal
            ]},
            "guardrails": [
                {"kpi": rail.kpi, **({"max": rail.max} if rail.max is not None else {}),
                 **({"min": rail.min} if rail.min is not None else {})}
                for rail in self.guardrails
            ],
            "limits": asdict(self.limits),
            "budget": {"hours": self.budget.hours, "ai": {"enabled": self.budget.ai_enabled, "max_usd": self.budget.ai_max_usd}},
            "plan": dict(self.plan),
            "step": self.step,
        }

    # -- what the study tunes ------------------------------------------------

    def tuned_settings(self) -> list[str]:
        return [name for name, choice in self.settings.items() if choice.mode == "tune"]

    def tunables(self) -> list[str]:
        """Every name the search varies: tuned settings, then data changes."""
        return self.tuned_settings() + list(self.data)


def load_study(folder: str | Path) -> Study:
    path = Path(folder) / "study.yaml"
    if not path.is_file():
        raise HarnessError(f"{folder}: there is no study.yaml in this folder")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise HarnessError(f"study.yaml: not valid YAML: {exc}") from None
    return Study.parse(raw)


def save_study(study: Study, folder: str | Path) -> Path:
    """Write `study.yaml` (atomically: a half-written study is worse than the last one)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "study.yaml"
    scratch = target.with_suffix(".yaml.tmp")
    scratch.write_text(
        yaml.safe_dump(study.to_yaml(), sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8", newline="\n",
    )
    scratch.replace(target)
    return target


# ---------------------------------------------------------------------------
# a study against its harness
# ---------------------------------------------------------------------------


def _relative_problem(path: str, where: str) -> str | None:
    posix = PurePosixPath(path.replace("\\", "/"))
    if posix.is_absolute() or re.match(r"^[A-Za-z]:", path) or ".." in posix.parts:
        return f"{where}: {path!r} must be a path inside the study folder, written relative to it"
    return None


def resolve_kpi(study: Study, harness: Harness, name: str) -> dict[str, Any]:
    """What `study-run.json` says about KPI `name`: its SQL (or `measure`),
    parameters and direction, and the words people read."""
    kpi = study.kpis[name]
    if kpi.kind == "harness":
        base = harness.kpis[name]
        return {"sql": base.sql, "measure": base.measure, "direction": base.direction, "says": base.label,
                "unit": base.unit, "positive": base.positive, "changes_with_levers": base.changes_with_levers}
    if kpi.kind == "template":
        template = harness.kpi_templates[kpi.template]
        says = template.label
        for param, value in kpi.params.items():
            says = says.replace("{" + param + "}", str(value))
        return {"sql": template.sql, "params": dict(kpi.params), "direction": template.direction, "says": says,
                "unit": template.unit, "positive": False, "changes_with_levers": False}
    if kpi.kind == "weighted":
        return {"weighted": dict(kpi.weighted), "direction": "lower", "says": kpi.says or name, "unit": kpi.unit,
                "positive": False, "changes_with_levers": False}
    return {"sql": kpi.sql, "direction": kpi.direction, "says": kpi.says or name, "unit": kpi.unit,
            "positive": False, "changes_with_levers": False, **({"rows_sql": kpi.rows_sql} if kpi.rows_sql else {})}


def study_problems(study: Study, harness: Harness, *, root: Path | None = None) -> list[str]:
    """Everything that keeps `study` from running, one sentence each, naming
    the key. Empty for a study that can be compiled. `root` (the study folder)
    also checks that the cases and inputs are there."""
    problems: list[str] = []
    add = problems.append
    if not study.name:
        add("name: give the study a name")
    if (study.harness_id, study.harness_version) != (harness.id, harness.version):
        add(f"harness: the study is for {study.harness_id} {study.harness_version}, this harness is {harness.id} {harness.version}")
    if harness.application.kind and not study.application_path:
        add("application: say where the application is")
    elif study.application_path:
        where = Path(study.application_path)
        if not (PurePosixPath(study.application_path).is_absolute() or PureWindowsPath(study.application_path).is_absolute()):
            add(f"application: give the full path to the application; {study.application_path!r} is relative, "
                "and a run starts it from its own folder")
        elif root is not None and not where.exists():
            add(f"application: there is nothing at {study.application_path} any more; point the study at the application again")

    # cases and inputs
    if not study.training:
        add("cases.training: the search needs at least one case to learn from")
    for where, paths in (("cases.training", study.training), ("cases.test", study.test)):
        for path in paths:
            problem = _relative_problem(path, where)
            if problem:
                add(problem)
            elif root is not None and not (root / path).is_file():
                add(f"{where}: {path} is not in the study folder")
    overlap = sorted(set(study.training) & set(study.test))
    if overlap:
        add(f"cases.test: {overlap[0]} is in both sets; a case the search learned from says nothing about the result")
    for name, path in study.inputs.items():
        if name not in harness.inputs:
            add(f"inputs.{name}: the harness takes no input called {name!r}; it takes {sorted(harness.inputs)}")
        elif _relative_problem(path, f"inputs.{name}"):
            add(_relative_problem(path, f"inputs.{name}"))  # type: ignore[arg-type]
        elif root is not None and not (root / path).is_file():
            add(f"inputs.{name}: {path} is not in the study folder")
    for name, spec in harness.inputs.items():
        if spec.required and name not in study.inputs:
            add(f"inputs.{name}: required: {spec.label}")

    # what may change
    db = harness.schema()
    try:
        for name, choice in study.settings.items():
            where = f"vary.settings.{name}"
            setting = harness.settings.get(name)
            if setting is None:
                add(f"{where}: the harness has no setting {name!r}")
                continue
            parameter = setting.parameter
            if choice.mode == "fixed":
                problem = parameter.problem_with(choice.value)
                if problem:
                    add(f"{where}.fixed: {problem}")
            if choice.mode == "tune":
                low = parameter.low if choice.low is None else choice.low
                high = parameter.high if choice.high is None else choice.high
                if not parameter.numeric and (choice.low is not None or choice.high is not None):
                    add(f"{where}: only a number has a range; {name} is {parameter.type}")
                elif parameter.numeric and not (parameter.low <= low < high <= parameter.high):  # type: ignore[operator]
                    add(f"{where}: the range must lie within [{parameter.low:g}, {parameter.high:g}] and be at least a step wide, got [{low:g}, {high:g}]")
                if choice.start is not None:
                    problem = parameter.problem_with(choice.start)
                    if problem:
                        add(f"{where}.start: {problem}")
                    elif parameter.numeric and not (low <= choice.start <= high):
                        add(f"{where}.start: {choice.start!r} lies outside the range [{low:g}, {high:g}]")
        for name, change in study.data.items():
            where = f"vary.data.{name}"
            if name in harness.settings:
                add(f"{where}: {name!r} is the name of a setting; name the data change differently")
            lever = harness.levers.get(change.lever)
            if lever is None:
                add(f"{where}.lever: the harness has no data change {change.lever!r}; it has {sorted(harness.levers)}")
                continue
            if not lever.code and change.column not in lever.columns:
                add(f"{where}.column: {change.lever} changes {sorted(lever.columns)}, not {change.column!r}")
            if change.mode not in lever.modes:
                add(f"{where}.mode: {change.lever} can {list(lever.modes)}, not {change.mode!r}")
            if not change.low < change.high:
                add(f"{where}: low must be below high, got [{change.low:g}, {change.high:g}]")
            elif not change.low <= change.start <= change.high:
                add(f"{where}.start: {change.start:g} lies outside [{change.low:g}, {change.high:g}]")
            if change.where:
                problem = where_problem(db, lever.table, change.where)
                if problem:
                    add(f"{where}.where: does not run on table {lever.table}: {problem}")
        if not study.tunables():
            add("vary: choose at least one setting to tune or one data change to try")
        tunables = set(study.tunables())
        for index, constraint in enumerate(study.constraints):
            where = f"constraints[{index}]"
            if constraint.expr:
                try:
                    unknown = sorted(Expression.parse(constraint.expr).names - tunables)
                except ExpressionError as exc:
                    add(f"{where}.expr: {exc}")
                    continue
                if unknown:
                    add(f"{where}.expr: {unknown[0]!r} is not something the study tunes; it tunes {sorted(tunables)}")
            else:
                problem = sql_problem(db, constraint.sql)
                if problem:
                    add(f"{where}.sql: does not run against the tables: {problem}")

        # what counts
        for name, kpi in study.kpis.items():
            where = f"kpis.{name}"
            if kpi.kind == "harness" and name not in harness.kpis:
                add(f"{where}: the harness has no ready-made KPI {name!r}")
            elif kpi.kind == "template":
                template = harness.kpi_templates.get(kpi.template)
                if template is None:
                    add(f"{where}.template: the harness has no KPI template {kpi.template!r}")
                elif set(kpi.params) != set(template.params):
                    add(f"{where}.params: fill in {sorted(template.params)}")
            elif kpi.kind == "sql":
                problem = sql_problem(db, kpi.sql)
                if problem:
                    add(f"{where}.sql: does not run against the tables: {problem}")
                if kpi.rows_sql and sql_problem(db, kpi.rows_sql):
                    add(f"{where}.rows_sql: does not run against the tables: {sql_problem(db, kpi.rows_sql)}")
            elif kpi.kind == "weighted":
                for part in kpi.weighted:
                    if part not in study.kpis or study.kpis[part].kind == "weighted":
                        add(f"{where}.weighted.{part}: a weighted sum adds up KPIs of the study that are not sums themselves")
            if name in tunables:
                add(f"{where}: {name!r} is also the name of something the study tunes")
    finally:
        db.close()

    if not study.goal:
        add("goal: say what the search should improve")
    if len(study.goal) > MAX_LEVELS:
        add(f"goal.levels: at most {MAX_LEVELS} levels in order of importance")
    for index, level in enumerate(study.goal):
        where = f"goal.levels[{index}]"
        if level.kpi not in study.kpis:
            add(f"{where}.kpi: {level.kpi!r} is not a KPI of the study")
        if level.direction not in ("lower", "higher"):
            add(f"{where}.direction: must be 'lower' or 'higher'")
        last = index == len(study.goal) - 1
        try:
            within = parse_within(level.equal_within)
        except ValueError as exc:
            add(f"{where}.equal_within: {exc}")
            continue
        if within is None and not last:
            add(f"{where}.equal_within: say when two results count as equal on this level (e.g. '1 %'), so the next level can decide")
        if within is not None and last and len(study.goal) > 1:
            add(f"{where}.equal_within: the last level decides whatever is left, so it has no tolerance")
    if len({level.kpi for level in study.goal}) != len(study.goal):
        add("goal.levels: each level is a different KPI")
    for index, rail in enumerate(study.guardrails):
        where = f"guardrails[{index}]"
        if rail.kpi not in study.kpis:
            add(f"{where}.kpi: {rail.kpi!r} is not a KPI of the study")
        if (rail.max is None) == (rail.min is None):
            add(f"{where}: give `max` or `min`, one of them")

    # limits and budget
    if study.limits.time_per_case_s < harness.time_limit.min_s:
        add(f"limits.time_per_case_s: at least {harness.time_limit.min_s:g} s")
    if study.limits.retries < 0:
        add("limits.retries: 0 or more")
    if study.limits.runs_per_case < 1:
        add("limits.runs_per_case: 1 or more")
    elif study.limits.runs_per_case > 1 and not harness.seeds:
        add("limits.runs_per_case: the application takes no seed, so a second run of a case repeats the first")
    if not study.budget.hours > 0:
        add("budget.hours: more than 0")
    if study.budget.ai_max_usd < 0:
        add("budget.ai.max_usd: 0 or more")
    return problems


def require_valid(study: Study, harness: Harness, *, root: Path | None = None) -> None:
    problems = study_problems(study, harness, root=root)
    if problems:
        raise HarnessError(problems[0] + (f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""))


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------

TEMPLATE_KEYS = {"title", "summary", "vary", "constraints", "kpis", "goal", "guardrails", "limits", "budget"}


def study_from_template(harness: Harness, template: str | None, name: str) -> Study:
    """A new study: the harness's defaults, and the template's choices (a
    blank study without one). `vary.settings: recommended` tunes the settings
    the harness recommends."""
    raw: dict[str, Any] = {}
    if template:
        if template not in harness.templates:
            raise HarnessError(f"template: the harness has no template {template!r}; it has {sorted(harness.templates)}")
        raw = dict(harness.templates[template])
        _known(raw, TEMPLATE_KEYS, f"templates/{template}.yaml")
    vary = _mapping(raw.get("vary"), "vary")
    settings = vary.get("settings")
    if settings == "recommended":
        vary = {**vary, "settings": {name: "tune" for name in harness.recommended}}
    defaults = harness.defaults
    document = {
        "study": STUDY_FORMAT,
        "name": name,
        "harness": {"id": harness.id, "version": harness.version},
        "template": template or "",
        "vary": vary,
        "constraints": raw.get("constraints") or [],
        "kpis": raw.get("kpis") or {},
        "goal": raw.get("goal") or {},
        "guardrails": raw.get("guardrails") or [],
        "limits": {
            "time_per_case_s": defaults.time_per_case_s, "retries": defaults.retries,
            "runs_per_case": defaults.runs_per_case, **_mapping(raw.get("limits"), "limits"),
        },
        "budget": {"hours": defaults.budget_hours, "ai": {"enabled": False, "max_usd": 2.0},
                   **_mapping(raw.get("budget"), "budget")},
    }
    return Study.parse(document)


def check_templates(harness: Harness) -> None:
    """Every study template of the harness has to make a study that compiles:
    a template that does not is found by the expert, not by a consultant."""
    from evolvekit.harness.compile import compile_study
    from evolvekit.harness.plan import Plan

    for template in harness.templates:
        where = f"templates/{template}.yaml"
        try:
            study = study_from_template(harness, template, "template check")
        except HarnessError as exc:
            raise HarnessError(f"{where}: {exc}") from None
        study.application_path = str(Path.home() / "application")  # never started: only its form counts here
        study.training = ["cases/a.case", "cases/b.case"]
        problems = study_problems(study, harness)
        if problems:
            raise HarnessError(f"{where}: {problems[0]}")
        try:
            compile_study(study, harness, Plan.fixed())
        except HarnessError as exc:
            raise HarnessError(f"{where}: does not compile: {exc}") from None
