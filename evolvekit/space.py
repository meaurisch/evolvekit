"""A typed parameter space: what a configuration *is* when it is not a program.

The `# PARAMS:` line (`search/params.py`) lets a block that is already Python
declare a few numeric constants. That is not enough for the commonest tuning
job there is -- a solver with a command line:

    ./solver --instance x.vrp --seed 3 --neighbours 40 --exhaustive false --init savings

Its parameters are integers, floats that want a log scale, booleans and named
choices; its user has no Python to fence, and should not have to write any. So
`problem.parameters` in the config declares the space once:

    problem:
      parameters:
        neighbours:  {type: int,    low: 10,  high: 120, default: 50}
        max_penalty: {type: float,  low: 1e3, high: 1e7, default: 1e5, log: true}
        exhaustive:  {type: bool,   default: true}
        init:        {type: choice, choices: [greedy, random, savings], default: greedy}

(YAML reads `1e3` as a string -- a YAML 1.1 float needs a dot and a signed
exponent -- so a numeric string is taken as its number for a range or a
default; see `_yaml_number`.) Everything else follows from the declaration:
the skeleton is generated (a `configure()` that returns a dict, which an LLM
operator may still rewrite), a candidate's
configuration is *resolved and validated in the static stage* -- before it can
cost a second of solver time -- the values are substituted into the stage
command as flags, recorded on the candidate as data rather than as code, and
the model-free operators sample, perturb and recombine them with each
parameter's type and scale in mind.

The defaults are the baseline: the seed candidate is exactly `defaults()`.
"""

from __future__ import annotations

import json
import math
import random
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

__all__ = ["Parameter", "ParameterSpace", "SpaceError", "PARAMETER_TYPES"]

PARAMETER_TYPES = ("int", "float", "bool", "choice")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SpaceError(ValueError):
    """A parameter declaration, or a configuration, that cannot be used."""


def _is_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _yaml_number(value: Any, kind: str) -> Any:
    """A bound or default that YAML handed over as a string, as the number it is.

    PyYAML follows YAML 1.1, where a float needs a dot and a signed exponent:
    `1e5` -- the way anybody writes a penalty range -- arrives as the *string*
    "1e5", and was refused as "not a finite number". A string that `float()`
    reads is taken as that number (a whole one as an `int` for an int
    parameter); anything else is passed on unchanged, to be refused with the
    message it always got.
    """
    if not isinstance(value, str):
        return value
    try:
        number = float(value)
    except ValueError:
        return value
    if not math.isfinite(number):
        return value
    return int(number) if kind == "int" and number.is_integer() else number


@dataclass(frozen=True)
class Parameter:
    """One dimension of the space."""

    name: str
    type: str
    default: Any
    low: float | None = None
    high: float | None = None
    log: bool = False
    choices: tuple[Any, ...] = ()
    flag: str = ""
    help: str = ""

    # -- declaration -----------------------------------------------------

    @staticmethod
    def parse(name: Any, raw: Any, path: str) -> "Parameter":
        """Build one parameter from its YAML mapping. `path` is the key path,
        so that a mistake is reported where it was made."""
        if not isinstance(name, str) or not _NAME.match(name):
            raise SpaceError(
                f"{path}: a parameter name must be a plain identifier "
                f"(letters, digits, underscores), got {name!r}"
            )
        if not isinstance(raw, Mapping):
            raise SpaceError(
                f"{path}: expected a mapping such as {{type: int, low: 1, high: 9, "
                f"default: 3}}, got {type(raw).__name__}"
            )
        known = {"type", "low", "high", "log", "choices", "default", "flag", "help"}
        unknown = sorted(set(raw) - known)
        if unknown:
            raise SpaceError(f"{path}: unknown key(s) {unknown}; known keys are {sorted(known)}")
        kind = raw.get("type")
        if kind not in PARAMETER_TYPES:
            raise SpaceError(
                f"{path}.type: must be one of {list(PARAMETER_TYPES)}, got {kind!r}"
            )
        if "default" not in raw:
            raise SpaceError(
                f"{path}.default: required. The defaults are the baseline every "
                "candidate is compared with, so each parameter needs one"
            )
        if kind in ("int", "float"):
            raw = {**raw, **{k: _yaml_number(raw[k], kind) for k in ("low", "high", "default") if k in raw}}
        flag = raw.get("flag", "--" + name.replace("_", "-"))
        if not isinstance(flag, str) or not flag:
            raise SpaceError(f"{path}.flag: must be a non-empty string, got {flag!r}")
        parameter = Parameter(
            name=name,
            type=kind,
            default=raw["default"],
            low=raw.get("low"),
            high=raw.get("high"),
            log=bool(raw.get("log", False)),
            choices=tuple(raw.get("choices") or ()),
            flag=flag,
            help=str(raw.get("help", "")),
        )
        parameter._check_declaration(raw, path)
        problem = parameter.problem_with(parameter.default)
        if problem:
            raise SpaceError(f"{path}.default: {problem}")
        return parameter

    def _check_declaration(self, raw: Mapping[str, Any], path: str) -> None:
        if self.type in ("int", "float"):
            for key in ("low", "high"):
                if not _is_number(raw.get(key)):
                    raise SpaceError(f"{path}.{key}: a {self.type} parameter needs a finite number")
            if not self.low < self.high:  # type: ignore[operator]
                raise SpaceError(f"{path}: low must be below high, got [{self.low}, {self.high}]")
            if self.type == "int" and not (float(self.low).is_integer() and float(self.high).is_integer()):  # type: ignore[arg-type]
                raise SpaceError(f"{path}: an int parameter needs whole-number bounds")
            if self.log and self.low <= 0:  # type: ignore[operator]
                raise SpaceError(f"{path}.log: a log scale needs low > 0, got {self.low}")
            if raw.get("choices"):
                raise SpaceError(f"{path}.choices: only a `choice` parameter has choices")
        else:
            for key in ("low", "high", "log"):
                # `is`, not `in (None, False)`: 0 == False, and `low: 0` is a mistake here.
                if raw.get(key) is not None and raw.get(key) is not False:
                    raise SpaceError(f"{path}.{key}: only int and float parameters have a range")
            if self.type == "choice":
                if len(self.choices) < 2 or len(set(map(repr, self.choices))) != len(self.choices):
                    raise SpaceError(f"{path}.choices: needs at least two distinct values")
                if not all(isinstance(c, (str, int, float)) and not isinstance(c, bool) for c in self.choices):
                    raise SpaceError(f"{path}.choices: values must be strings or numbers")
            elif raw.get("choices"):
                raise SpaceError(f"{path}.choices: only a `choice` parameter has choices")

    # -- values ----------------------------------------------------------

    @property
    def numeric(self) -> bool:
        return self.type in ("int", "float")

    @property
    def categories(self) -> tuple[Any, ...]:
        """The values of a non-numeric parameter, in declaration order."""
        if self.type == "bool":
            return (False, True)
        return self.choices

    def problem_with(self, value: Any) -> str | None:
        """Why `value` is not a legal value, in words a user can act on."""
        if self.type == "bool":
            return None if isinstance(value, bool) else f"expected true or false, got {value!r}"
        if self.type == "choice":
            return None if value in self.choices and not isinstance(value, bool) else (
                f"expected one of {list(self.choices)}, got {value!r}"
            )
        if not _is_number(value):
            return f"expected a finite {self.type}, got {value!r}"
        if self.type == "int" and not float(value).is_integer():
            return f"expected a whole number, got {value!r}"
        if value < self.low:  # type: ignore[operator]
            return f"{value!r} is below its minimum {self.low!r}"
        if value > self.high:  # type: ignore[operator]
            return f"{value!r} is above its maximum {self.high!r}"
        return None

    def coerce(self, value: Any) -> Any:
        if self.type == "int":
            return int(round(float(value)))
        if self.type == "float":
            return float(value)
        return value

    def to_unit(self, value: Any) -> float:
        """Where a value sits in its range, 0..1, on the parameter's own scale."""
        if not self.numeric:
            cats = self.categories
            return cats.index(value) / (len(cats) - 1) if value in cats and len(cats) > 1 else 0.0
        low, high = float(self.low), float(self.high)  # type: ignore[arg-type]
        if self.log:
            return (math.log(float(value)) - math.log(low)) / (math.log(high) - math.log(low))
        return (float(value) - low) / (high - low)

    def from_unit(self, unit: float) -> Any:
        unit = min(1.0, max(0.0, unit))
        if not self.numeric:
            cats = self.categories
            return cats[min(len(cats) - 1, int(unit * len(cats)))]
        low, high = float(self.low), float(self.high)  # type: ignore[arg-type]
        raw = math.exp(math.log(low) + unit * (math.log(high) - math.log(low))) if self.log else low + unit * (high - low)
        if self.type == "int":
            return int(min(high, max(low, round(raw))))
        return float(f"{min(high, max(low, raw)):.6g}")

    def render(self, value: Any) -> str:
        """The value as one command-line token."""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def describe(self) -> dict[str, Any]:
        """For the event log, the status document and the prompt."""
        return {
            "name": self.name,
            "type": self.type,
            "default": self.default,
            "low": self.low,
            "high": self.high,
            "log": self.log,
            "choices": list(self.choices),
            "help": self.help,
        }


@dataclass(frozen=True)
class ParameterSpace:
    parameters: tuple[Parameter, ...]

    @staticmethod
    def parse(raw: Any, path: str = "problem.parameters") -> "ParameterSpace":
        if not isinstance(raw, Mapping) or not raw:
            raise SpaceError(
                f"{path}: expected a non-empty mapping of parameter name to declaration"
            )
        return ParameterSpace(
            tuple(Parameter.parse(name, spec, f"{path}.{name}") for name, spec in raw.items())
        )

    def __iter__(self):
        return iter(self.parameters)

    def __len__(self) -> int:
        return len(self.parameters)

    @property
    def names(self) -> list[str]:
        return [p.name for p in self.parameters]

    def defaults(self) -> dict[str, Any]:
        return {p.name: p.coerce(p.default) for p in self.parameters}

    def describe(self) -> list[dict[str, Any]]:
        return [p.describe() for p in self.parameters]

    # -- a configuration, checked ----------------------------------------

    def validate(self, values: Any) -> tuple[dict[str, Any], list[str]]:
        """`(configuration, problems)`. A parameter that is left out takes its
        default -- a partial answer from a model is an answer -- but an unknown
        key, a wrong type or a value outside its range is a problem, because a
        silently clamped value is a configuration nobody asked for."""
        if not isinstance(values, Mapping):
            return {}, [f"configure() must return a dict, got {type(values).__name__}"]
        problems = [
            f"unknown parameter {key!r}; the parameters are {self.names}"
            for key in values
            if key not in self.names
        ]
        resolved: dict[str, Any] = {}
        for parameter in self.parameters:
            value = values.get(parameter.name, parameter.default)
            problem = parameter.problem_with(value)
            if problem:
                problems.append(f"{parameter.name}: {problem}")
            else:
                resolved[parameter.name] = parameter.coerce(value)
        return resolved, problems

    # -- a configuration, written down -----------------------------------

    def render_block(self, values: Mapping[str, Any]) -> str:
        """The evolve block for `values`: a `configure()` that returns a dict."""
        lines = [
            "def configure():",
            # Byte for byte what the old global quote replacement made of
            # "solver's": a block's text is part of its evaluation cache key,
            # and an unchanged configuration must stay a cache hit.
            '    """The solver"s parameters. Change the values, keep the keys."""',
            "    return {",
        ]
        lines += [f'        "{p.name}": {_literal(values[p.name])},' for p in self.parameters]
        lines += ["    }", ""]
        return "\n".join(lines)

    def render_skeleton(self, block_start: str, block_end: str) -> str:
        return (
            '"""Generated by evolvekit from `problem.parameters`: the configuration\n'
            'of an external command, as the one function a candidate may change."""\n\n'
            f"{block_start}\n{self.render_block(self.defaults())}{block_end}\n"
        )

    def render_flags(self, values: Mapping[str, Any]) -> list[str]:
        """`--flag value` pairs, in declaration order."""
        tokens: list[str] = []
        for parameter in self.parameters:
            tokens += [parameter.flag, parameter.render(values[parameter.name])]
        return tokens

    # -- moving through the space ----------------------------------------

    def latin_hypercube(self, count: int, rng: random.Random) -> list[dict[str, Any]]:
        """`count` configurations, one per stratum per parameter, on each
        parameter's own scale (so a log parameter is covered decade by decade)."""
        columns: dict[str, list[Any]] = {}
        for parameter in self.parameters:
            strata = [(i + rng.random()) / count for i in range(count)]
            rng.shuffle(strata)
            columns[parameter.name] = [parameter.from_unit(u) for u in strata]
        return [{name: column[i] for name, column in columns.items()} for i in range(count)]

    def perturb(
        self,
        values: Mapping[str, Any],
        rng: random.Random,
        *,
        scale: float = 0.15,
        moves: int = 3,
    ) -> dict[str, Any]:
        """A neighbour of `values`: one to `moves` parameters moved a little.

        How many is drawn first -- one twice as often as two, two twice as often
        as three -- and never more than a quarter of the space (one, in a space
        of seven or fewer). A step that moves eight of thirty parameters at once
        is a jump, and says nothing about any one of them; a step that moves one
        says exactly what that one is worth. A number takes a Gaussian step of
        `scale` of its range on its own scale (an integer by at least one), a
        boolean flips, a choice is redrawn. At least one parameter always
        changes, because a child identical to its parent is a wasted evaluation.
        """
        limit = max(1, min(int(moves), len(self.parameters) // 4))
        child = dict(values)
        for _ in range(50):
            count = rng.choices(range(1, limit + 1), weights=[0.5 ** i for i in range(limit)])[0]
            for parameter in rng.sample(list(self.parameters), count):
                child[parameter.name] = self._moved(parameter, child[parameter.name], rng, scale)
            if child != dict(values):
                return child
        return child

    @staticmethod
    def _moved(parameter: Parameter, current: Any, rng: random.Random, scale: float) -> Any:
        if not parameter.numeric:
            return rng.choice([c for c in parameter.categories if c != current])
        step = rng.gauss(0.0, scale)
        moved = parameter.from_unit(parameter.to_unit(current) + step)
        if moved == current and parameter.type == "int":
            # The step rounded away. One whole unit in its direction, or the
            # other way at the edge of the range.
            for delta in ((1, -1) if step >= 0 else (-1, 1)):
                if parameter.low <= current + delta <= parameter.high:  # type: ignore[operator]
                    return current + delta
        return moved

    def crossover(
        self, first: Mapping[str, Any], second: Mapping[str, Any], rng: random.Random
    ) -> dict[str, Any]:
        """Each parameter from one parent or the other."""
        return {
            p.name: (first if rng.random() < 0.5 else second)[p.name] for p in self.parameters
        }

    def distance(self, a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
        """Mean per-parameter distance, 0..1, on each parameter's own scale."""
        total = 0.0
        for parameter in self.parameters:
            if parameter.numeric:
                total += abs(parameter.to_unit(a[parameter.name]) - parameter.to_unit(b[parameter.name]))
            else:
                total += 0.0 if a[parameter.name] == b[parameter.name] else 1.0
        return total / len(self.parameters)


def _literal(value: Any) -> str:
    """`value` as Python source, a string in double quotes.

    Per value, not by replacing every `'` in the finished block with `"`:
    that turned the choice `it's` into `"it"s"`, a syntax error in every
    candidate that picked it. A JSON string is a valid Python string literal
    (the same escapes, and `ensure_ascii=False` keeps a non-ASCII character
    itself rather than splitting it into surrogates); numbers and booleans
    are their `repr`. A parameter name needs no quoting: it is an identifier.
    """
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return repr(value)


def _unused(_: Sequence[Any]) -> None:  # pragma: no cover - keeps the import honest
    return None
