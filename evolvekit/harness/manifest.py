"""`harness.yaml`: what a harness declares, loaded and checked.

A harness is a folder (or a `.zip` of one) that an expert writes once per
application:

    harness.yaml     this declaration
    runner.py        the application-specific hooks, on top of the SDK
    evk_harness.py   the SDK, copied in
    kit/             the runner's helper code
    templates/       study templates, one YAML per template
    samples/         a few small cases for a first try
    AGENTS.md        instructions for AI coding tools
    README.md        optional, shown in the app

Loading refuses, one sentence per mistake naming its key path: unknown keys,
a setting whose default is outside its range, a lever on a table or column the
harness does not declare, KPI and template SQL that does not compile against
the declared tables, and a study template that does not compile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from evolvekit.harness import HarnessError
from evolvekit.harness.sql import placeholders, schema_database, sql_problem
from evolvekit.space import Parameter, SpaceError

__all__ = [
    "FORMAT",
    "Application",
    "CasesSpec",
    "Column",
    "Defaults",
    "Export",
    "Harness",
    "InputSpec",
    "Kpi",
    "KpiTemplate",
    "Lever",
    "Setting",
    "Table",
    "TemplateParam",
    "TimeLimit",
    "load_harness",
]

FORMAT = 1
_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
COLUMN_TYPES = ("int", "float", "text", "bool")
LEVER_MODES = ("scale", "set", "add")
DIRECTIONS = ("lower", "higher")


# ---------------------------------------------------------------------------
# small checked getters, every one naming the key path
# ---------------------------------------------------------------------------


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


def _required(data: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in data or data[key] is None:
        raise HarnessError(f"{path}.{key}: required")
    return data[key]


def _text(value: Any, path: str, *, empty: bool = False) -> str:
    if value is None and empty:
        return ""
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise HarnessError(f"{path}: expected text, got {value!r}")
    return " ".join(value.split()) if "\n" in value else value.strip()


def _flag(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise HarnessError(f"{path}: expected true or false, got {value!r}")
    return value


def _number(value: Any, path: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HarnessError(f"{path}: expected a number, got {value!r}")
    if positive and value <= 0:
        raise HarnessError(f"{path}: must be > 0, got {value}")
    return float(value)


def _name(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _NAME.match(value):
        raise HarnessError(f"{path}: a name must be letters, digits and underscores, got {value!r}")
    return value


# ---------------------------------------------------------------------------
# the parts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Application:
    kind: str
    """`python`: an interpreter with the application importable; `program`: an executable."""
    label: str
    module: str = ""
    """kind python: the module that must import (`pyvrp`)."""
    version: str = ""
    """kind python: a version specifier the module must satisfy (">=0.14,<0.15")."""
    probe: str = ""
    """kind program: a command, `{app}` standing for the program, run with a 10 s timeout."""
    expect: str = ""
    """kind program: a regular expression its output must match; group 1 is the version."""
    help: str = ""
    """Where to find the application, for the app's "Where do I find this?"."""

    @staticmethod
    def parse(raw: Any) -> "Application":
        path = "application"
        data = _mapping(raw, path)
        _known(data, {"kind", "label", "requires", "probe", "expect", "help"}, path)
        kind = _text(_required(data, "kind", path), f"{path}.kind")
        if kind not in ("python", "program"):
            raise HarnessError(f"{path}.kind: must be 'python' or 'program', got {kind!r}")
        label = _text(_required(data, "label", path), f"{path}.label")
        help_text = _text(data.get("help"), f"{path}.help", empty=True)
        if kind == "python":
            for key in ("probe", "expect"):
                if key in data:
                    raise HarnessError(f"{path}.{key}: only a `program` application is probed by a command")
            requires = _mapping(data.get("requires"), f"{path}.requires")
            _known(requires, {"module", "version"}, f"{path}.requires")
            return Application(
                kind=kind, label=label, help=help_text,
                module=_text(requires.get("module", ""), f"{path}.requires.module", empty=True),
                version=_text(requires.get("version", ""), f"{path}.requires.version", empty=True),
            )
        if "requires" in data:
            raise HarnessError(f"{path}.requires: a `program` application is probed, not imported; use `probe`")
        probe = _text(_required(data, "probe", path), f"{path}.probe")
        if "{app}" not in probe:
            raise HarnessError(f"{path}.probe: must contain {{app}}, which stands for the program, got {probe!r}")
        expect = _text(data.get("expect", ""), f"{path}.expect", empty=True)
        if expect:
            try:
                groups = re.compile(expect).groups
            except re.error as exc:
                raise HarnessError(f"{path}.expect: not a valid regular expression: {exc}") from None
            if groups < 1:
                raise HarnessError(f"{path}.expect: needs a group for the version, e.g. 'solver (\\d+\\.\\d+)'")
        return Application(kind=kind, label=label, probe=probe, expect=expect, help=help_text)


@dataclass(frozen=True)
class CasesSpec:
    label: str
    formats: tuple[str, ...]
    describe: str = ""

    @staticmethod
    def parse(raw: Any, path: str = "cases") -> "CasesSpec":
        data = _mapping(raw, path)
        _known(data, {"label", "formats", "describe"}, path)
        formats = data.get("formats") or []
        if not isinstance(formats, list) or not formats or not all(
            isinstance(f, str) and f.startswith(".") and len(f) > 1 for f in formats
        ):
            raise HarnessError(f"{path}.formats: expected a list of file extensions such as ['.json'], got {formats!r}")
        return CasesSpec(
            label=_text(_required(data, "label", path), f"{path}.label"),
            formats=tuple(f.lower() for f in formats),
            describe=_text(data.get("describe"), f"{path}.describe", empty=True),
        )


@dataclass(frozen=True)
class InputSpec:
    name: str
    label: str
    formats: tuple[str, ...]
    describe: str = ""
    required: bool = False
    provides: str = "file"
    """`settings`: a JSON object of setting values the study starts from (and
    keeps for every setting it neither tunes nor fixes); `file`: anything the
    runner reads itself, from `study-run.json`'s `inputs`."""

    @staticmethod
    def parse(name: str, raw: Any) -> "InputSpec":
        path = f"inputs.{name}"
        _name(name, path)
        data = _mapping(raw, path)
        _known(data, {"label", "formats", "describe", "required", "provides"}, path)
        spec = CasesSpec.parse({k: v for k, v in data.items() if k not in ("required", "provides")}, path)
        provides = _text(data.get("provides", "file"), f"{path}.provides")
        if provides not in ("file", "settings"):
            raise HarnessError(f"{path}.provides: 'settings' (a JSON object of setting values) or 'file', got {provides!r}")
        if provides == "settings" and spec.formats != (".json",):
            raise HarnessError(f"{path}.formats: a settings input is a JSON object: [.json]")
        return InputSpec(
            name=name, label=spec.label, formats=spec.formats, describe=spec.describe,
            required=_flag(data.get("required", False), f"{path}.required"), provides=provides,
        )


@dataclass(frozen=True)
class TimeLimit:
    accepts: bool
    """Whether the application stops itself at a time limit it is given."""
    default_s: float = 60.0
    min_s: float = 1.0

    @staticmethod
    def parse(raw: Any) -> "TimeLimit":
        path = "time_limit"
        data = _mapping(raw, path)
        _known(data, {"accepts", "default_s", "min_s"}, path)
        limit = TimeLimit(
            accepts=_flag(_required(data, "accepts", path), f"{path}.accepts"),
            default_s=_number(data.get("default_s", 60), f"{path}.default_s", positive=True),
            min_s=_number(data.get("min_s", 1), f"{path}.min_s", positive=True),
        )
        if limit.default_s < limit.min_s:
            raise HarnessError(f"{path}.default_s: {limit.default_s:g} is below min_s {limit.min_s:g}")
        return limit


SETTING_META = {"label", "group", "explain", "recommended"}


@dataclass(frozen=True)
class Setting:
    """One setting of the application: the engine's typed parameter, and what
    people (and the model) are told about it."""

    parameter: Parameter
    label: str
    group: str = "Settings"
    explain: str = ""
    """The longer explanation a model reads; people see `help`."""
    recommended: bool = False

    @property
    def name(self) -> str:
        return self.parameter.name

    @property
    def help(self) -> str:
        return self.parameter.help

    @staticmethod
    def parse(name: Any, raw: Any) -> "Setting":
        path = f"settings.{name}"
        data = _mapping(raw, path)
        try:
            parameter = Parameter.parse(name, {k: v for k, v in data.items() if k not in SETTING_META}, path)
        except SpaceError as exc:
            raise HarnessError(str(exc)) from None
        return Setting(
            parameter=parameter,
            label=_text(data.get("label", str(name).replace("_", " ")), f"{path}.label"),
            group=_text(data.get("group", "Settings"), f"{path}.group"),
            explain=_text(data.get("explain"), f"{path}.explain", empty=True),
            recommended=_flag(data.get("recommended", False), f"{path}.recommended"),
        )


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    unit: str = ""
    describe: str = ""
    categorical: bool = False
    """Only categorical columns' distinct values may be summarised to a model."""
    private: bool = False
    """Never summarised to a model at all, not even its range: coordinates,
    and anything else that would locate a customer."""

    @staticmethod
    def parse(table: str, name: Any, raw: Any) -> "Column":
        path = f"tables.{table}.columns.{name}"
        _name(name, path)
        data = _mapping(raw, path)
        _known(data, {"type", "unit", "describe", "categorical", "private"}, path)
        kind = _text(_required(data, "type", path), f"{path}.type")
        if kind not in COLUMN_TYPES:
            raise HarnessError(f"{path}.type: must be one of {list(COLUMN_TYPES)}, got {kind!r}")
        column = Column(
            name=name, type=kind,
            unit=_text(data.get("unit", ""), f"{path}.unit", empty=True),
            describe=_text(data.get("describe"), f"{path}.describe", empty=True),
            categorical=_flag(data.get("categorical", False), f"{path}.categorical"),
            private=_flag(data.get("private", False), f"{path}.private"),
        )
        if column.categorical and column.private:
            raise HarnessError(f"{path}: a column is categorical (its values may be shown to a model) or private "
                               "(nothing about it is), not both")
        return column


@dataclass(frozen=True)
class Table:
    name: str
    source: str
    """`request`: the case as the solver receives it; `solution`: the plan."""
    columns: dict[str, Column]
    describe: str = ""

    @staticmethod
    def parse(name: Any, raw: Any) -> "Table":
        path = f"tables.{name}"
        _name(name, path)
        if str(name).startswith("orig_"):
            raise HarnessError(f"{path}: `orig_` names the untouched copy of a request table; rename it")
        data = _mapping(raw, path)
        _known(data, {"source", "describe", "columns"}, path)
        source = _text(_required(data, "source", path), f"{path}.source")
        if source not in ("request", "solution"):
            raise HarnessError(f"{path}.source: must be 'request' (the case) or 'solution' (the plan), got {source!r}")
        columns = _mapping(_required(data, "columns", path), f"{path}.columns")
        if not columns:
            raise HarnessError(f"{path}.columns: a table needs at least one column")
        return Table(
            name=name, source=source, describe=_text(data.get("describe"), f"{path}.describe", empty=True),
            columns={c: Column.parse(name, c, spec) for c, spec in columns.items()},
        )


@dataclass(frozen=True)
class Lever:
    name: str
    label: str
    table: str
    columns: dict[str, str]
    """Column -> what people call it ("cost per metre")."""
    modes: tuple[str, ...]
    help: str = ""
    explain: str = ""
    code: bool = False
    """Applied by the runner's `apply_lever` instead of a column edit."""

    @staticmethod
    def parse(name: Any, raw: Any, tables: Mapping[str, Table]) -> "Lever":
        path = f"levers.{name}"
        _name(name, path)
        data = _mapping(raw, path)
        _known(data, {"label", "table", "columns", "modes", "help", "explain", "code"}, path)
        table = _text(_required(data, "table", path), f"{path}.table")
        if table not in tables:
            raise HarnessError(f"{path}.table: {table!r} is not a declared table; the tables are {sorted(tables)}")
        if tables[table].source != "request":
            raise HarnessError(f"{path}.table: {table!r} is a solution table; a lever changes the request")
        code = _flag(data.get("code", False), f"{path}.code")
        columns: dict[str, str] = {}
        for column, spec in _mapping(data.get("columns"), f"{path}.columns").items():
            where = f"{path}.columns.{column}"
            if column not in tables[table].columns:
                raise HarnessError(f"{where}: table {table!r} has no column {column!r}")
            if tables[table].columns[column].type not in ("int", "float"):
                raise HarnessError(f"{where}: a lever changes numbers, and {column!r} is {tables[table].columns[column].type}")
            meta = _mapping(spec, where)
            _known(meta, {"label"}, where)
            columns[column] = _text(meta.get("label", column.replace("_", " ")), f"{where}.label")
        if not columns and not code:
            raise HarnessError(f"{path}.columns: name the column(s) the lever changes, or set `code: true`")
        modes = data.get("modes", ["scale"])
        if not isinstance(modes, list) or not modes or any(m not in LEVER_MODES for m in modes):
            raise HarnessError(f"{path}.modes: a list of {list(LEVER_MODES)}, got {modes!r}")
        return Lever(
            name=name, label=_text(_required(data, "label", path), f"{path}.label"), table=table,
            columns=columns, modes=tuple(modes), code=code,
            help=_text(data.get("help"), f"{path}.help", empty=True),
            explain=_text(data.get("explain"), f"{path}.explain", empty=True),
        )


@dataclass(frozen=True)
class Kpi:
    name: str
    label: str
    direction: str
    sql: str = ""
    measure: bool = False
    """Computed by the runner's `measure` instead of SQL."""
    unit: str = ""
    help: str = ""
    positive: bool = False
    """Above 0 on every sensible solution: may be normalised per case."""
    changes_with_levers: bool = False
    """Measured with what the solver saw: not comparable once a lever changes it."""

    @staticmethod
    def parse(name: Any, raw: Any) -> "Kpi":
        path = f"kpis.{name}"
        _name(name, path)
        data = _mapping(raw, path)
        _known(data, {"label", "direction", "sql", "measure", "unit", "help", "positive", "changes_with_levers"}, path)
        direction = _text(_required(data, "direction", path), f"{path}.direction")
        if direction not in DIRECTIONS:
            raise HarnessError(f"{path}.direction: must be 'lower' or 'higher' (which is better), got {direction!r}")
        sql = _text(data.get("sql", ""), f"{path}.sql", empty=True)
        measure = _flag(data.get("measure", False), f"{path}.measure")
        if bool(sql) == measure:
            raise HarnessError(f"{path}: give either `sql` or `measure: true`, not {'both' if sql else 'neither'}")
        return Kpi(
            name=name, label=_text(data.get("label", name.replace("_", " ")), f"{path}.label"),
            direction=direction, sql=sql, measure=measure,
            unit=_text(data.get("unit", ""), f"{path}.unit", empty=True),
            help=_text(data.get("help"), f"{path}.help", empty=True),
            positive=_flag(data.get("positive", False), f"{path}.positive"),
            changes_with_levers=_flag(data.get("changes_with_levers", False), f"{path}.changes_with_levers"),
        )


@dataclass(frozen=True)
class TemplateParam:
    name: str
    type: str
    """`choice` (from a query, or listed), `number`, `int` or `text`."""
    source: str = ""
    """For a choice: SQL whose first column lists the choices."""
    choices: tuple[Any, ...] = ()
    label: str = ""

    @staticmethod
    def parse(template: str, name: Any, raw: Any) -> "TemplateParam":
        path = f"kpi_templates.{template}.params.{name}"
        _name(name, path)
        data = _mapping(raw, path)
        _known(data, {"type", "from", "choices", "label"}, path)
        kind = _text(_required(data, "type", path), f"{path}.type")
        if kind not in ("choice", "number", "int", "text"):
            raise HarnessError(f"{path}.type: must be choice, number, int or text, got {kind!r}")
        source = _text(data.get("from", ""), f"{path}.from", empty=True)
        choices = tuple(data.get("choices") or ())
        if kind == "choice" and not (source or choices):
            raise HarnessError(f"{path}: a choice needs `from` (SQL listing the choices) or `choices`")
        return TemplateParam(name=name, type=kind, source=source, choices=choices,
                             label=_text(data.get("label", name), f"{path}.label"))


@dataclass(frozen=True)
class KpiTemplate:
    name: str
    label: str
    """With the blanks in braces: "Late tasks with tag {tag}"."""
    direction: str
    sql: str
    params: dict[str, TemplateParam]
    unit: str = ""
    help: str = ""

    @staticmethod
    def parse(name: Any, raw: Any) -> "KpiTemplate":
        path = f"kpi_templates.{name}"
        _name(name, path)
        data = _mapping(raw, path)
        _known(data, {"label", "direction", "sql", "params", "unit", "help"}, path)
        direction = _text(_required(data, "direction", path), f"{path}.direction")
        if direction not in DIRECTIONS:
            raise HarnessError(f"{path}.direction: must be 'lower' or 'higher', got {direction!r}")
        params = {
            p: TemplateParam.parse(name, p, spec)
            for p, spec in _mapping(_required(data, "params", path), f"{path}.params").items()
        }
        sql = _text(_required(data, "sql", path), f"{path}.sql")
        used = placeholders(sql)
        if used != set(params):
            missing, unused = sorted(used - set(params)), sorted(set(params) - used)
            raise HarnessError(
                f"{path}: the SQL's blanks and `params` must match"
                + (f"; undeclared: {missing}" if missing else "") + (f"; unused: {unused}" if unused else "")
            )
        label = _text(_required(data, "label", path), f"{path}.label")
        return KpiTemplate(name=name, label=label, direction=direction, sql=sql, params=params,
                           unit=_text(data.get("unit", ""), f"{path}.unit", empty=True),
                           help=_text(data.get("help"), f"{path}.help", empty=True))


@dataclass(frozen=True)
class Export:
    name: str
    label: str
    applies_to: str
    """`settings` or `data`: which kind of study offers it."""

    @staticmethod
    def parse(name: Any, raw: Any) -> "Export":
        path = f"exports.{name}"
        _name(name, path)
        data = _mapping(raw, path)
        _known(data, {"label", "for"}, path)
        applies = _text(_required(data, "for", path), f"{path}.for")
        if applies not in ("settings", "data"):
            raise HarnessError(f"{path}.for: must be 'settings' or 'data', got {applies!r}")
        return Export(name=name, label=_text(_required(data, "label", path), f"{path}.label"), applies_to=applies)


@dataclass(frozen=True)
class Defaults:
    time_per_case_s: float = 60.0
    retries: int = 1
    budget_hours: float = 2.0
    test_share: float = 0.3
    runs_per_case: int = 1

    @staticmethod
    def parse(raw: Any) -> "Defaults":
        path = "defaults"
        data = _mapping(raw, path)
        _known(data, {"time_per_case_s", "retries", "budget_hours", "test_share", "runs_per_case"}, path)
        defaults = Defaults(
            time_per_case_s=_number(data.get("time_per_case_s", 60), f"{path}.time_per_case_s", positive=True),
            retries=int(_number(data.get("retries", 1), f"{path}.retries")),
            budget_hours=_number(data.get("budget_hours", 2), f"{path}.budget_hours", positive=True),
            test_share=_number(data.get("test_share", 0.3), f"{path}.test_share"),
            runs_per_case=int(_number(data.get("runs_per_case", 1), f"{path}.runs_per_case", positive=True)),
        )
        if not 0 <= defaults.test_share < 1:
            raise HarnessError(f"{path}.test_share: a share in [0, 1), got {defaults.test_share}")
        if defaults.retries < 0:
            raise HarnessError(f"{path}.retries: must be >= 0, got {defaults.retries}")
        return defaults


# ---------------------------------------------------------------------------
# the harness
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Harness:
    root: Path
    id: str
    version: str
    title: str
    summary: str
    application: Application
    cases: CasesSpec
    inputs: dict[str, InputSpec]
    time_limit: TimeLimit
    seeds: bool
    settings: dict[str, Setting]
    tables: dict[str, Table]
    levers: dict[str, Lever]
    kpis: dict[str, Kpi]
    kpi_templates: dict[str, KpiTemplate]
    exports: dict[str, Export]
    defaults: Defaults
    templates: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Study templates, by id (the file's stem): partial `study.yaml` documents."""

    @property
    def key(self) -> str:
        return f"{self.id}-{self.version}"

    def declared(self) -> dict[str, dict[str, str]]:
        """`{table: {column: type}}`, the shape `study-run.json` carries."""
        return {name: {c: col.type for c, col in table.columns.items()} for name, table in self.tables.items()}

    @property
    def request_tables(self) -> list[str]:
        return [name for name, table in self.tables.items() if table.source == "request"]

    def schema(self):
        """An empty, read-only database with the declared tables (see `harness/sql.py`)."""
        return schema_database(self.declared(), self.request_tables)

    @property
    def recommended(self) -> list[str]:
        return [name for name, setting in self.settings.items() if setting.recommended]


def _check_sql(harness: Harness) -> None:
    db = harness.schema()
    try:
        for name, kpi in harness.kpis.items():
            if kpi.sql:
                problem = sql_problem(db, kpi.sql)
                if problem:
                    raise HarnessError(f"kpis.{name}.sql: does not run against the declared tables: {problem}")
        for name, template in harness.kpi_templates.items():
            problem = sql_problem(db, template.sql, template.params)
            if problem:
                raise HarnessError(f"kpi_templates.{name}.sql: does not run against the declared tables: {problem}")
            for param in template.params.values():
                if param.source:
                    problem = sql_problem(db, param.source)
                    if problem:
                        raise HarnessError(
                            f"kpi_templates.{name}.params.{param.name}.from: does not run against the "
                            f"declared tables: {problem}"
                        )
    finally:
        db.close()


def _read_yaml(path: Path, what: str) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HarnessError(f"{what}: cannot be read: {exc}") from None
    except yaml.YAMLError as exc:
        raise HarnessError(f"{what}: not valid YAML: {exc}") from None
    return _mapping(raw, what)


def load_harness(root: str | Path, *, check_templates: bool = True) -> Harness:
    """Read and check the harness in folder `root`."""
    root = Path(root)
    manifest = root / "harness.yaml"
    if not manifest.is_file():
        raise HarnessError(f"{root}: there is no harness.yaml in this folder")
    data = _read_yaml(manifest, "harness.yaml")
    _known(
        data,
        {"harness", "id", "version", "title", "summary", "application", "cases", "inputs", "time_limit",
         "seeds", "settings", "tables", "levers", "kpis", "kpi_templates", "exports", "defaults"},
        "<root>",
    )
    if data.get("harness") != FORMAT:
        raise HarnessError(f"harness: the format version must be {FORMAT}, got {data.get('harness')!r}")
    harness_id = _text(_required(data, "id", "<root>"), "id")
    if not _ID.match(harness_id):
        raise HarnessError(f"id: lower-case letters, digits and dashes, got {harness_id!r}")
    version = str(_required(data, "version", "<root>"))
    if not _VERSION.match(version):
        raise HarnessError(f"version: three numbers such as 1.0.0, got {version!r}")
    tables = {name: Table.parse(name, spec) for name, spec in _mapping(data.get("tables"), "tables").items()}
    settings = {name: Setting.parse(name, spec) for name, spec in _mapping(data.get("settings"), "settings").items()}
    harness = Harness(
        root=root.resolve(),
        id=harness_id,
        version=version,
        title=_text(_required(data, "title", "<root>"), "title"),
        summary=_text(data.get("summary", ""), "summary", empty=True),
        application=Application.parse(_required(data, "application", "<root>")),
        cases=CasesSpec.parse(_required(data, "cases", "<root>")),
        inputs={name: InputSpec.parse(name, spec) for name, spec in _mapping(data.get("inputs"), "inputs").items()},
        time_limit=TimeLimit.parse(data.get("time_limit") or {"accepts": False}),
        seeds=_flag(data.get("seeds", False), "seeds"),
        settings=settings,
        tables=tables,
        levers={name: Lever.parse(name, spec, tables) for name, spec in _mapping(data.get("levers"), "levers").items()},
        kpis={name: Kpi.parse(name, spec) for name, spec in _mapping(data.get("kpis"), "kpis").items()},
        kpi_templates={
            name: KpiTemplate.parse(name, spec)
            for name, spec in _mapping(data.get("kpi_templates"), "kpi_templates").items()
        },
        exports={name: Export.parse(name, spec) for name, spec in _mapping(data.get("exports"), "exports").items()},
        defaults=Defaults.parse(data.get("defaults")),
        templates=_templates(root),
    )
    clash = sorted(set(harness.kpis) & set(harness.settings))
    if clash:
        raise HarnessError(f"kpis.{clash[0]}: a KPI and a setting share the name {clash[0]!r}")
    _check_sql(harness)
    if check_templates:
        # Imported here: a study needs a harness, and a harness checks its templates as studies.
        from evolvekit.harness.study import check_templates as _check

        _check(harness)
    return harness


def _templates(root: Path) -> dict[str, dict[str, Any]]:
    folder = root / "templates"
    if not folder.is_dir():
        return {}
    templates = {}
    for path in sorted(folder.glob("*.yaml")):
        if not _ID.match(path.stem):
            raise HarnessError(f"templates/{path.name}: a template's file name is its id: lower-case letters, digits, dashes")
        templates[path.stem] = _read_yaml(path, f"templates/{path.name}")
    return templates
