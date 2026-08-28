"""YAML -> validated dataclasses.

One public entry point, `load_config(path)`. Every validation failure raises
`ConfigError` naming the offending key path, because a config typo that only
shows up three hours into a run is exactly the kind of waste this project
exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "ConfigError",
    "DEFAULT_SIGNATURE_DIGITS",
    "DEFAULT_SIGNATURE_DIGITS_STOCHASTIC",
    "DEFAULT_SIGNATURE_IGNORE",
    "Config",
    "ProblemConfig",
    "StageConfig",
    "PromoteRule",
    "ScoreConfig",
    "PenaltyConfig",
    "EvaluateConfig",
    "DescriptorConfig",
    "ArchiveConfig",
    "ModelConfig",
    "ModelsConfig",
    "BudgetConfig",
    "StopConfig",
    "GainPerUsdRule",
    "SearchConfig",
    "AdaptiveChildrenConfig",
    "NearDuplicateConfig",
    "NoveltyConfig",
    "NEAR_METHODS",
    "load_config",
    "build_config",
    "deep_merge",
]


class ConfigError(ValueError):
    """Raised for any malformed or missing configuration value."""


# --------------------------------------------------------------------------
# small typed getters -- every one reports the full key path on failure
# --------------------------------------------------------------------------


def _require(mapping: dict[str, Any], key: str, path: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"{path}.{key}: required key is missing")
    return mapping[key]


def _as_mapping(value: Any, path: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: expected a mapping, got {type(value).__name__}")
    return value


def _as_list(value: Any, path: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{path}: expected a list, got {type(value).__name__}")
    return value


def _as_str(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: expected a non-empty string, got {value!r}")
    return value


def _as_float(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path}: expected a number, got {value!r}")
    return float(value)


def _as_int(value: Any, path: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path}: expected an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{path}: must be >= {minimum}, got {value}")
    return value


def _as_positive(value: Any, path: str) -> float:
    number = _as_float(value, path)
    if number <= 0:
        raise ConfigError(f"{path}: must be > 0, got {number}")
    return number


def _as_bullets(value: Any, path: str) -> tuple[str, ...]:
    """A list of non-empty one-line strings, or a block of text split on lines.

    Accepting both shapes is deliberate: `tools:` reads naturally as a YAML
    list, but a hand-written config that used a `|` block should not be a
    validation error three keys into a file that is otherwise fine.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(
            line.strip().lstrip("-").strip()
            for line in value.splitlines()
            if line.strip()
        )
    items = _as_list(value, path)
    return tuple(
        " ".join(_as_str(item, f"{path}[{i}]").split())
        for i, item in enumerate(items)
    )


def _reject_unknown(mapping: dict[str, Any], known: set[str], path: str) -> None:
    extra = sorted(set(mapping) - known)
    if extra:
        raise ConfigError(
            f"{path}: unknown key(s) {extra}; known keys are {sorted(known)}"
        )


DEFAULT_FORBIDDEN_IMPORTS = (
    "os",
    "sys",
    "subprocess",
    "socket",
    "shutil",
    "importlib",
    "ctypes",
    "pathlib",
    "requests",
    "urllib",
    "http",
    "pickle",
)

DEFAULT_SIGNATURE_DIGITS = 9
"""Significant digits every KPI is rounded to before it is fingerprinted.
Enough that two genuinely different behaviours never collide, few enough that
the last bits of a float sum cannot separate two identical ones."""

DEFAULT_SIGNATURE_DIGITS_STOCHASTIC = 3
"""Significant digits used instead, on a stage with `seeds: N > 1`.

A stochastic evaluator never fingerprints identically. Two runs of the same
program on the same instances differ in the eighth digit for free -- a
different random restart, a different tie broken by hash order, a solver that
stopped a microsecond later -- so at nine digits every candidate is novel and
the behavioural filter, the one thing standing between an hour-long evaluator
and a re-expression of its own seed, silently stops working. Three digits says
"the same to a tenth of a percent", which on a mean over N runs is a claim
about the program rather than about the dice."""

DEFAULT_SIGNATURE_IGNORE: tuple[str, ...] = (
    "runtime_s",
    "complexity",
    "static_problems",
)
"""KPIs left out of the behaviour signature: anything timing- or size-related.
How long a candidate took and how big its block is are not part of what it
*did* -- and a re-expression of the parent usually differs in exactly those
two numbers and in nothing else."""


# --------------------------------------------------------------------------
# problem
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProblemConfig:
    """The human-written skeleton and the fenced region the loop may rewrite.

    `description` is prose. The three fields under it are structure, and they
    exist because the second real Phase C circle-packing run produced fifteen
    hand-placed arrangements and not one time-bounded local search: the
    skeleton offered `deadline()`, `expired()` and `TIME_BUDGET_S`, and nothing
    in the prompt said so in a place the model could not skim past. Prose in
    the middle of a paragraph is skimmable; a section headed "Tools available
    to your block" is not.
    """

    skeleton: Path
    language: str = "python"
    block_start: str = "# EVOLVE-BLOCK-START"
    block_end: str = "# EVOLVE-BLOCK-END"
    description: str = ""
    tools: tuple[str, ...] = ()
    """What the evolve block may call or rely on: helpers in the fixed part,
    time-budget constants, allowed imports. One bullet each."""
    constraints: tuple[str, ...] = ()
    """What the block must not do. One bullet each."""
    what_counts_as_new: str = ""
    """One or two sentences saying what kind of change actually alters the
    decisions this problem's evaluator measures -- the problem-specific half of
    the framework's problem-agnostic "you re-expressed the same rule" note."""
    required_functions: tuple[str, ...] = ()
    forbidden_imports: tuple[str, ...] = DEFAULT_FORBIDDEN_IMPORTS

    @staticmethod
    def parse(raw: Any, base_dir: Path) -> "ProblemConfig":
        data = _as_mapping(raw, "problem")
        known = {
            "skeleton",
            "language",
            "block_start",
            "block_end",
            "description",
            "tools",
            "constraints",
            "what_counts_as_new",
            "required_functions",
            "forbidden_imports",
        }
        _reject_unknown(data, known, "problem")
        skeleton = base_dir / _as_str(
            _require(data, "skeleton", "problem"), "problem.skeleton"
        )
        if not skeleton.is_file():
            raise ConfigError(f"problem.skeleton: no such file: {skeleton}")
        language = data.get("language", "python")
        if language != "python":
            raise ConfigError(
                "problem.language: only 'python' is supported in Phase A, "
                f"got {language!r}"
            )
        forbidden = data.get("forbidden_imports")
        return ProblemConfig(
            skeleton=skeleton,
            language=language,
            block_start=_as_str(
                data.get("block_start", "# EVOLVE-BLOCK-START"), "problem.block_start"
            ),
            block_end=_as_str(
                data.get("block_end", "# EVOLVE-BLOCK-END"), "problem.block_end"
            ),
            description=str(data.get("description", "")),
            tools=_as_bullets(data.get("tools"), "problem.tools"),
            constraints=_as_bullets(data.get("constraints"), "problem.constraints"),
            what_counts_as_new=str(data.get("what_counts_as_new", "") or "").strip(),
            required_functions=tuple(
                _as_str(v, f"problem.required_functions[{i}]")
                for i, v in enumerate(
                    _as_list(
                        data.get("required_functions"), "problem.required_functions"
                    )
                )
            ),
            forbidden_imports=(
                DEFAULT_FORBIDDEN_IMPORTS
                if forbidden is None
                else tuple(
                    _as_str(v, f"problem.forbidden_imports[{i}]")
                    for i, v in enumerate(
                        _as_list(forbidden, "problem.forbidden_imports")
                    )
                )
            ),
        )


# --------------------------------------------------------------------------
# evaluate
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PromoteRule:
    """How many of a generation's candidates move on to the next stage."""

    top_k_per_generation: int | None = None
    archive_percentile: float | None = None

    @staticmethod
    def parse(raw: Any, path: str) -> "PromoteRule":
        data = _as_mapping(raw, path)
        if not data:
            return PromoteRule()
        _reject_unknown(data, {"top_k_per_generation", "archive_percentile"}, path)
        top_k = data.get("top_k_per_generation")
        percentile = data.get("archive_percentile")
        if top_k is None and percentile is None:
            raise ConfigError(
                f"{path}: set top_k_per_generation or archive_percentile "
                "(or omit `promote` to promote everything)"
            )
        rule = PromoteRule(
            top_k_per_generation=(
                None
                if top_k is None
                else _as_int(top_k, f"{path}.top_k_per_generation", minimum=1)
            ),
            archive_percentile=(
                None
                if percentile is None
                else _as_float(percentile, f"{path}.archive_percentile")
            ),
        )
        if rule.archive_percentile is not None and not (
            0.0 <= rule.archive_percentile <= 100.0
        ):
            raise ConfigError(f"{path}.archive_percentile: must be within [0, 100]")
        return rule

    def describe(self) -> str:
        parts = []
        if self.top_k_per_generation is not None:
            parts.append(f"top {self.top_k_per_generation}/generation")
        if self.archive_percentile is not None:
            parts.append(f"score >= archive p{self.archive_percentile:g}")
        return " or ".join(parts) if parts else "all"


STAGE_KINDS = ("builtin-static", "command")


@dataclass(frozen=True)
class StageConfig:
    """One rung of the evaluation cascade."""

    id: str
    kind: str
    command: str = ""
    inputs: tuple[str, ...] = ()
    private_inputs: tuple[str, ...] = ()
    timeout: float = 60.0
    """Per *run*, not per stage. With `seeds: 3` a stage may take three times
    this long in total and each individual run is still killed at `timeout`."""
    promote: PromoteRule = field(default_factory=PromoteRule)
    import_check: bool = True
    max_per_day: int | None = None
    seeds: int = 1
    """How many times to run this stage, with `{seed}` set to 0, 1, ... N-1.

    For a deterministic evaluator, 1 -- anything else is paying N times for the
    same answer. For a stochastic one (a solver with a random restart, a
    resampled instance draw), N > 1 turns a single noisy sample into a mean
    plus a per-KPI coefficient of variation, which is the difference between
    "this candidate is better" and "this candidate got a good roll".
    """

    @property
    def stochastic(self) -> bool:
        return self.seeds > 1

    @staticmethod
    def parse(raw: Any, index: int) -> "StageConfig":
        path = f"evaluate.stages[{index}]"
        data = _as_mapping(raw, path)
        known = {
            "id",
            "kind",
            "command",
            "inputs",
            "private_inputs",
            "timeout",
            "promote",
            "import_check",
            "max_per_day",
            "seeds",
        }
        _reject_unknown(data, known, path)
        stage_id = _as_str(_require(data, "id", path), f"{path}.id")
        kind = _as_str(data.get("kind", "command"), f"{path}.kind")
        if kind not in STAGE_KINDS:
            raise ConfigError(
                f"{path}.kind: must be one of {list(STAGE_KINDS)}, got {kind!r}"
            )
        command = str(data.get("command", ""))
        if kind == "command":
            if not command.strip():
                raise ConfigError(f"{path}.command: required when kind is 'command'")
            for placeholder in ("{candidate}", "{out}"):
                if placeholder not in command:
                    raise ConfigError(
                        f"{path}.command: must contain the {placeholder} placeholder"
                    )
        max_per_day = data.get("max_per_day")
        seeds = _as_int(data.get("seeds", 1), f"{path}.seeds", minimum=1)
        if seeds > 1:
            if kind != "command":
                raise ConfigError(
                    f"{path}.seeds: only a 'command' stage can be run more than "
                    f"once; stage {stage_id!r} is {kind!r}"
                )
            if "{seed}" not in command:
                raise ConfigError(
                    f"{path}.seeds: {seeds} runs were asked for but the command "
                    "has no {seed} placeholder, so every run would be identical; "
                    "add {seed} to the command or set seeds: 1"
                )
        return StageConfig(
            id=stage_id,
            kind=kind,
            command=command,
            inputs=tuple(
                _as_str(v, f"{path}.inputs[{i}]")
                for i, v in enumerate(_as_list(data.get("inputs"), f"{path}.inputs"))
            ),
            private_inputs=tuple(
                _as_str(v, f"{path}.private_inputs[{i}]")
                for i, v in enumerate(
                    _as_list(data.get("private_inputs"), f"{path}.private_inputs")
                )
            ),
            timeout=_as_positive(data.get("timeout", 60), f"{path}.timeout"),
            promote=PromoteRule.parse(data.get("promote"), f"{path}.promote"),
            import_check=bool(data.get("import_check", True)),
            max_per_day=(
                None
                if max_per_day is None
                else _as_int(max_per_day, f"{path}.max_per_day", minimum=1)
            ),
            seeds=seeds,
        )


@dataclass(frozen=True)
class ScoreConfig:
    """Objective KPI, direction and per-KPI weights. Higher score is better."""

    objective: str
    direction: str = "maximize"
    weights: dict[str, float] = field(default_factory=dict)

    @staticmethod
    def parse(raw: Any) -> "ScoreConfig":
        data = _as_mapping(raw, "evaluate.score")
        _reject_unknown(
            data, {"objective", "direction", "weights"}, "evaluate.score"
        )
        objective = _as_str(
            _require(data, "objective", "evaluate.score"), "evaluate.score.objective"
        )
        direction = _as_str(
            data.get("direction", "maximize"), "evaluate.score.direction"
        )
        if direction not in ("maximize", "minimize"):
            raise ConfigError(
                "evaluate.score.direction: must be 'maximize' or 'minimize', "
                f"got {direction!r}"
            )
        weights_raw = _as_mapping(data.get("weights"), "evaluate.score.weights")
        weights = {
            _as_str(k, "evaluate.score.weights key"): _as_float(
                v, f"evaluate.score.weights.{k}"
            )
            for k, v in weights_raw.items()
        }
        if not weights:
            weights = {objective: 1.0}
        if objective not in weights:
            raise ConfigError(
                "evaluate.score.weights: must include the objective KPI "
                f"{objective!r}; got {sorted(weights)}"
            )
        return ScoreConfig(
            objective=objective, direction=direction, weights=weights
        )


PENALTY_SCALES = ("linear", "log1p")


@dataclass(frozen=True)
class PenaltyConfig:
    """A gate violation expressed as a subtractive term -- never a None score."""

    kpi: str
    weight: float = 1.0
    scale: str = "linear"

    @staticmethod
    def parse(raw: Any, index: int) -> "PenaltyConfig":
        path = f"evaluate.penalties[{index}]"
        data = _as_mapping(raw, path)
        _reject_unknown(data, {"kpi", "weight", "scale"}, path)
        scale = _as_str(data.get("scale", "linear"), f"{path}.scale")
        if scale not in PENALTY_SCALES:
            raise ConfigError(
                f"{path}.scale: must be one of {list(PENALTY_SCALES)}, got {scale!r}"
            )
        weight = _as_float(data.get("weight", 1.0), f"{path}.weight")
        if weight < 0:
            raise ConfigError(
                f"{path}.weight: must be >= 0 (penalties always subtract), "
                f"got {weight}"
            )
        return PenaltyConfig(
            kpi=_as_str(_require(data, "kpi", path), f"{path}.kpi"),
            weight=weight,
            scale=scale,
        )


@dataclass(frozen=True)
class EvaluateConfig:
    stages: tuple[StageConfig, ...]
    score: ScoreConfig
    penalties: tuple[PenaltyConfig, ...] = ()
    failure_score: float = -1000.0
    holdout_penalty: float = 1.0
    """How hard a private hold-out that does worse than the public set is
    punished in the *ranking* score. The raw public and private scores are
    always kept; only the quantity the search ranks by is discounted."""
    signature_digits: int = DEFAULT_SIGNATURE_DIGITS
    signature_digits_stochastic: int = DEFAULT_SIGNATURE_DIGITS_STOCHASTIC
    """Used in place of `signature_digits` on a stage with `seeds: N > 1`.
    See `DEFAULT_SIGNATURE_DIGITS_STOCHASTIC` for why it has to be coarser."""
    signature_ignore: tuple[str, ...] = DEFAULT_SIGNATURE_IGNORE
    """The behaviour signature: which KPIs it is built from and how precisely.
    See `evolvekit/evaluate/signature.py`."""

    def digits_for(self, stage: StageConfig) -> int:
        """How precisely to fingerprint one stage's KPIs."""
        return (
            self.signature_digits_stochastic
            if stage.stochastic
            else self.signature_digits
        )

    @staticmethod
    def parse(raw: Any) -> "EvaluateConfig":
        data = _as_mapping(raw, "evaluate")
        _reject_unknown(
            data,
            {
                "stages",
                "score",
                "penalties",
                "failure_score",
                "holdout_penalty",
                "signature_digits",
                "signature_digits_stochastic",
                "signature_ignore",
            },
            "evaluate",
        )
        raw_ignore = data.get("signature_ignore")
        signature_ignore = (
            DEFAULT_SIGNATURE_IGNORE
            if raw_ignore is None
            else tuple(
                _as_str(v, f"evaluate.signature_ignore[{i}]")
                for i, v in enumerate(_as_list(raw_ignore, "evaluate.signature_ignore"))
            )
        )
        holdout_penalty = _as_float(
            data.get("holdout_penalty", 1.0), "evaluate.holdout_penalty"
        )
        if holdout_penalty < 0:
            raise ConfigError(
                "evaluate.holdout_penalty: must be >= 0 (it only ever subtracts), "
                f"got {holdout_penalty}"
            )
        stages_raw = _as_list(
            _require(data, "stages", "evaluate"), "evaluate.stages"
        )
        if not stages_raw:
            raise ConfigError("evaluate.stages: at least one stage is required")
        stages = tuple(StageConfig.parse(s, i) for i, s in enumerate(stages_raw))
        ids = [s.id for s in stages]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ConfigError(f"evaluate.stages: duplicate stage id(s) {duplicates}")
        if stages[0].kind != "builtin-static":
            raise ConfigError(
                "evaluate.stages[0].kind: the first stage must be 'builtin-static' "
                "(it is the only stage allowed to hard-reject a candidate)"
            )
        if len(stages) > 1 and stages[0].promote != PromoteRule():
            raise ConfigError(
                "evaluate.stages[0].promote: the static stage has no score to rank "
                "by, so it must promote everything it does not reject; remove the "
                "`promote` key"
            )
        for i, stage in enumerate(stages[:-1]):
            if stage.private_inputs:
                raise ConfigError(
                    f"evaluate.stages[{i}].private_inputs: only the final stage "
                    "may declare a private hold-out"
                )
        return EvaluateConfig(
            stages=stages,
            score=ScoreConfig.parse(_require(data, "score", "evaluate")),
            penalties=tuple(
                PenaltyConfig.parse(p, i)
                for i, p in enumerate(
                    _as_list(data.get("penalties"), "evaluate.penalties")
                )
            ),
            failure_score=_as_float(
                data.get("failure_score", -1000.0), "evaluate.failure_score"
            ),
            holdout_penalty=holdout_penalty,
            signature_digits=_as_int(
                data.get("signature_digits", DEFAULT_SIGNATURE_DIGITS),
                "evaluate.signature_digits",
                minimum=1,
            ),
            signature_digits_stochastic=_as_int(
                data.get(
                    "signature_digits_stochastic",
                    DEFAULT_SIGNATURE_DIGITS_STOCHASTIC,
                ),
                "evaluate.signature_digits_stochastic",
                minimum=1,
            ),
            signature_ignore=signature_ignore,
        )


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------

PROVIDER_NAMES = ("azure", "openrouter", "claude-cli", "fake")


@dataclass(frozen=True)
class ModelConfig:
    """One routing slot (`small` or `strong`): backend, model id and prices."""

    role: str
    provider: str
    model: str
    price_in_per_mtok: float = 0.0
    price_out_per_mtok: float = 0.0
    max_tokens: int = 2048
    temperature: float = 1.0
    options: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def parse(raw: Any, role: str) -> "ModelConfig":
        path = f"models.{role}"
        data = _as_mapping(raw, path)
        known = {
            "provider",
            "model",
            "price_in_per_mtok",
            "price_out_per_mtok",
            "max_tokens",
            "temperature",
            "options",
        }
        _reject_unknown(data, known, path)
        provider = _as_str(_require(data, "provider", path), f"{path}.provider")
        if provider not in PROVIDER_NAMES:
            raise ConfigError(
                f"{path}.provider: must be one of {list(PROVIDER_NAMES)}, "
                f"got {provider!r}"
            )
        price_in = _as_float(
            data.get("price_in_per_mtok", 0.0), f"{path}.price_in_per_mtok"
        )
        price_out = _as_float(
            data.get("price_out_per_mtok", 0.0), f"{path}.price_out_per_mtok"
        )
        for name, value in (
            ("price_in_per_mtok", price_in),
            ("price_out_per_mtok", price_out),
        ):
            if value < 0:
                raise ConfigError(f"{path}.{name}: must be >= 0, got {value}")
        temperature = _as_float(data.get("temperature", 1.0), f"{path}.temperature")
        if not 0.0 <= temperature <= 2.0:
            raise ConfigError(
                f"{path}.temperature: must be within [0, 2], got {temperature}"
            )
        return ModelConfig(
            role=role,
            provider=provider,
            model=_as_str(_require(data, "model", path), f"{path}.model"),
            price_in_per_mtok=price_in,
            price_out_per_mtok=price_out,
            max_tokens=_as_int(
                data.get("max_tokens", 2048), f"{path}.max_tokens", minimum=1
            ),
            temperature=temperature,
            options=_as_mapping(data.get("options"), f"{path}.options"),
        )


@dataclass(frozen=True)
class ModelsConfig:
    small: ModelConfig
    strong: ModelConfig
    embed: ModelConfig | None = None
    """The optional third slot, used only by `search.novelty.near.method:
    embedding`. It is a full `ModelConfig` because the ledger prices an
    embedding call exactly the way it prices a completion: from the table.
    Only `price_in_per_mtok` is ever read -- embeddings emit no output tokens."""

    @staticmethod
    def parse(raw: Any) -> "ModelsConfig":
        data = _as_mapping(raw, "models")
        _reject_unknown(data, {"small", "strong", "embed"}, "models")
        embed = data.get("embed")
        return ModelsConfig(
            small=ModelConfig.parse(_require(data, "small", "models"), "small"),
            strong=ModelConfig.parse(_require(data, "strong", "models"), "strong"),
            embed=None if embed is None else ModelConfig.parse(embed, "embed"),
        )

    def by_role(self, role: str) -> ModelConfig:
        if role == "small":
            return self.small
        if role == "strong":
            return self.strong
        if role == "embed":
            if self.embed is None:
                raise ConfigError("models.embed: no embedding slot is configured")
            return self.embed
        raise ConfigError(f"unknown model role {role!r}")


# --------------------------------------------------------------------------
# budget / stop / search
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetConfig:
    max_usd: float = 1.0
    max_tokens: int = 1_000_000
    max_full_evals_per_day: int = 20
    # Consecutive backend failures (rate limit, session cap, outage) before the
    # run halts instead of breeding children into a dead provider.
    max_consecutive_provider_errors: int = 3

    @staticmethod
    def parse(raw: Any) -> "BudgetConfig":
        data = _as_mapping(raw, "budget")
        _reject_unknown(
            data,
            {
                "max_usd",
                "max_tokens",
                "max_full_evals_per_day",
                "max_consecutive_provider_errors",
            },
            "budget",
        )
        return BudgetConfig(
            max_usd=_as_positive(data.get("max_usd", 1.0), "budget.max_usd"),
            max_tokens=_as_int(
                data.get("max_tokens", 1_000_000), "budget.max_tokens", minimum=1
            ),
            max_full_evals_per_day=_as_int(
                data.get("max_full_evals_per_day", 20),
                "budget.max_full_evals_per_day",
                minimum=1,
            ),
            max_consecutive_provider_errors=_as_int(
                data.get("max_consecutive_provider_errors", 3),
                "budget.max_consecutive_provider_errors",
                minimum=1,
            ),
        )


@dataclass(frozen=True)
class GainPerUsdRule:
    """`stop.min_gain_per_usd`: stop when the exchange rate goes bad.

    `threshold` is in score units per USD over a `window`-generation sliding
    window. Off unless the key is present -- there is no defensible default
    for "how much score is a dollar worth" on a problem the framework has
    never seen.
    """

    window: int
    threshold: float

    @staticmethod
    def parse(raw: Any) -> "GainPerUsdRule | None":
        if raw is None:
            return None
        path = "stop.min_gain_per_usd"
        data = _as_mapping(raw, path)
        if not data:
            return None
        _reject_unknown(data, {"window", "threshold"}, path)
        return GainPerUsdRule(
            window=_as_int(data.get("window", 3), f"{path}.window", minimum=1),
            threshold=_as_float(
                _require(data, "threshold", path), f"{path}.threshold"
            ),
        )


@dataclass(frozen=True)
class StopConfig:
    patience: int = 5
    epsilon: float = 1e-6
    target: float | None = None
    min_big_steps: int = 0
    max_usd_since_improvement: float | None = None
    """Stop once this much has been spent since the archive-best last moved.
    `null` (the default) is off. Like `patience` but denominated in money,
    which is the unit the plateau actually costs."""
    min_gain_per_usd: GainPerUsdRule | None = None

    @property
    def economics_window(self) -> int:
        """The sliding window the economics series is computed over."""
        return self.min_gain_per_usd.window if self.min_gain_per_usd else 3

    @staticmethod
    def parse(raw: Any) -> "StopConfig":
        data = _as_mapping(raw, "stop")
        _reject_unknown(
            data,
            {
                "patience",
                "epsilon",
                "target",
                "min_big_steps",
                "max_usd_since_improvement",
                "min_gain_per_usd",
            },
            "stop",
        )
        epsilon = _as_float(data.get("epsilon", 1e-6), "stop.epsilon")
        if epsilon < 0:
            raise ConfigError(f"stop.epsilon: must be >= 0, got {epsilon}")
        target = data.get("target")
        max_usd = data.get("max_usd_since_improvement")
        if max_usd is not None:
            max_usd = _as_positive(max_usd, "stop.max_usd_since_improvement")
        return StopConfig(
            patience=_as_int(data.get("patience", 5), "stop.patience", minimum=1),
            epsilon=epsilon,
            target=None if target is None else _as_float(target, "stop.target"),
            min_big_steps=_as_int(
                data.get("min_big_steps", 0), "stop.min_big_steps", minimum=0
            ),
            max_usd_since_improvement=max_usd,
            min_gain_per_usd=GainPerUsdRule.parse(data.get("min_gain_per_usd")),
        )


@dataclass(frozen=True)
class AdaptiveChildrenConfig:
    """`search.adaptive_children`: let the run decide how wide to search.

    Absent by default. When present, `search.children_per_generation` becomes
    the starting point and this becomes the rule that moves it -- up on
    stagnation, back down once the search is climbing again.
    """

    min: int
    max: int
    grow_after: int = 2
    shrink_after: int = 1

    @staticmethod
    def parse(raw: Any) -> "AdaptiveChildrenConfig | None":
        if raw is None:
            return None
        path = "search.adaptive_children"
        data = _as_mapping(raw, path)
        if not data:
            return None
        _reject_unknown(data, {"min", "max", "grow_after", "shrink_after"}, path)
        low = _as_int(_require(data, "min", path), f"{path}.min", minimum=1)
        high = _as_int(_require(data, "max", path), f"{path}.max", minimum=1)
        if high < low:
            raise ConfigError(f"{path}: max must be >= min, got {high} < {low}")
        return AdaptiveChildrenConfig(
            min=low,
            max=high,
            grow_after=_as_int(
                data.get("grow_after", 2), f"{path}.grow_after", minimum=1
            ),
            shrink_after=_as_int(
                data.get("shrink_after", 1), f"{path}.shrink_after", minimum=1
            ),
        )


NEAR_METHODS = ("local", "embedding", "off")


@dataclass(frozen=True)
class NearDuplicateConfig:
    """The similarity gate between the exact structure hash and the evaluator.

    `local` needs nothing and costs nothing. `embedding` needs a `models.embed`
    slot on a backend that has an embeddings API, and bills every call.
    """

    method: str = "local"
    threshold: float = 0.97
    model: str = ""
    """Embedding model. Empty means "whatever `models.embed.model` says",
    which is the normal case; set it only to use a different model from the
    one the slot names."""

    @property
    def enabled(self) -> bool:
        return self.method != "off"

    @staticmethod
    def parse(raw: Any) -> "NearDuplicateConfig":
        path = "search.novelty.near"
        data = _as_mapping(raw, path)
        _reject_unknown(data, {"method", "threshold", "model"}, path)
        method = _as_str(data.get("method", "local"), f"{path}.method")
        if method not in NEAR_METHODS:
            raise ConfigError(
                f"{path}.method: must be one of {list(NEAR_METHODS)}, got {method!r}"
            )
        threshold = _as_float(data.get("threshold", 0.97), f"{path}.threshold")
        if not 0.0 <= threshold <= 1.0:
            raise ConfigError(
                f"{path}.threshold: must be within [0, 1], got {threshold}"
            )
        return NearDuplicateConfig(
            method=method,
            threshold=threshold,
            model=str(data.get("model", "") or ""),
        )


BEHAVIOURAL_MODES = ("on", "off", "auto")


@dataclass(frozen=True)
class NoveltyConfig:
    """Everything under `search.novelty`. Today that is the near-duplicate gate
    and the behavioural-duplicate switch."""

    near: NearDuplicateConfig = field(default_factory=NearDuplicateConfig)
    behavioural: str = "on"
    """`on` (default): every command stage is fingerprinted and checked against
    every prior signature at that stage, as it always was. `off`: skip the
    fingerprinting and the check entirely. `auto`: skip it per-stage when the
    stage is stochastic (`seeds > 1`) -- on a wall-clock-bounded solver a
    stochastic stage's KPIs vary enough that, even at the coarser
    `signature_digits_stochastic` rounding, two runs of the same candidate
    almost never hash equal (0 hits in the first PyVRP run), so the filter
    pays for a hash on every candidate at every such stage and catches
    nothing. A deterministic stage keeps fingerprinting either way."""

    @staticmethod
    def parse(raw: Any) -> "NoveltyConfig":
        data = _as_mapping(raw, "search.novelty")
        _reject_unknown(data, {"near", "behavioural"}, "search.novelty")
        behavioural = _as_str(
            data.get("behavioural", "on"), "search.novelty.behavioural"
        )
        if behavioural not in BEHAVIOURAL_MODES:
            raise ConfigError(
                "search.novelty.behavioural: must be one of "
                f"{list(BEHAVIOURAL_MODES)}, got {behavioural!r}"
            )
        return NoveltyConfig(
            near=NearDuplicateConfig.parse(data.get("near")),
            behavioural=behavioural,
        )


KNOWN_OPERATORS = ("diff", "rewrite", "crossover", "param_lhs")


@dataclass(frozen=True)
class DescriptorConfig:
    """One axis of the MAP-Elites grid: a KPI, a bin count and a range.

    `range: auto` (or an omitted range) lets the archive widen the axis as it
    sees values, re-binning what it already holds. Any KPI the evaluator emits
    works; `complexity` is emitted by the static stage for free.
    """

    kpi: str
    bins: int = 4
    lo: float | None = None
    hi: float | None = None

    @property
    def auto(self) -> bool:
        return self.lo is None or self.hi is None

    @staticmethod
    def parse(raw: Any, index: int) -> "DescriptorConfig":
        path = f"search.archive.descriptors[{index}]"
        data = _as_mapping(raw, path)
        _reject_unknown(data, {"kpi", "bins", "range"}, path)
        bins = _as_int(data.get("bins", 4), f"{path}.bins", minimum=1)
        raw_range = data.get("range", "auto")
        lo = hi = None
        if raw_range not in (None, "auto"):
            bounds = _as_list(raw_range, f"{path}.range")
            if len(bounds) != 2:
                raise ConfigError(
                    f"{path}.range: expected [lo, hi] or 'auto', got {raw_range!r}"
                )
            lo = _as_float(bounds[0], f"{path}.range[0]")
            hi = _as_float(bounds[1], f"{path}.range[1]")
            if not lo < hi:
                raise ConfigError(f"{path}.range: must satisfy lo < hi, got {bounds}")
        return DescriptorConfig(
            kpi=_as_str(_require(data, "kpi", path), f"{path}.kpi"),
            bins=bins,
            lo=lo,
            hi=hi,
        )


DEFAULT_DESCRIPTORS = (DescriptorConfig(kpi="complexity", bins=4),)


@dataclass(frozen=True)
class ArchiveConfig:
    """The MAP-Elites grid: descriptor axes plus a global top-k."""

    descriptors: tuple[DescriptorConfig, ...] = DEFAULT_DESCRIPTORS
    top_k: int = 20

    @staticmethod
    def parse(raw: Any) -> "ArchiveConfig":
        data = _as_mapping(raw, "search.archive")
        _reject_unknown(data, {"descriptors", "top_k"}, "search.archive")
        raw_descriptors = _as_list(
            data.get("descriptors"), "search.archive.descriptors"
        )
        descriptors = tuple(
            DescriptorConfig.parse(d, i) for i, d in enumerate(raw_descriptors)
        ) or DEFAULT_DESCRIPTORS
        names = [d.kpi for d in descriptors]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ConfigError(
                f"search.archive.descriptors: duplicate kpi(s) {duplicates}"
            )
        return ArchiveConfig(
            descriptors=descriptors,
            top_k=_as_int(data.get("top_k", 20), "search.archive.top_k", minimum=1),
        )


@dataclass(frozen=True)
class SearchConfig:
    children_per_generation: int = 4
    generations: int = 10
    big_step_every: int = 5
    parent_top_k: int = 5
    seed: int = 0
    operators: dict[str, float] = field(
        default_factory=lambda: {"diff": 0.6, "rewrite": 0.4}
    )
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    parents_per_generation: int = 0
    """0 means "one sampled parent per child"."""
    inspirations: int = 2
    scratchpad_every: int = 5
    novelty_retry: bool = True
    novelty: NoveltyConfig = field(default_factory=NoveltyConfig)
    adaptive_children: AdaptiveChildrenConfig | None = None

    def parents_wanted(self, children: int) -> int:
        return self.parents_per_generation or children

    @staticmethod
    def parse(raw: Any) -> "SearchConfig":
        data = _as_mapping(raw, "search")
        known = {
            "children_per_generation",
            "generations",
            "big_step_every",
            "parent_top_k",
            "seed",
            "operators",
            "archive",
            "parents_per_generation",
            "inspirations",
            "scratchpad_every",
            "novelty_retry",
            "novelty",
            "adaptive_children",
        }
        _reject_unknown(data, known, "search")
        ops_raw = _as_mapping(data.get("operators"), "search.operators")
        operators = {
            _as_str(k, "search.operators key"): _as_float(v, f"search.operators.{k}")
            for k, v in ops_raw.items()
        }
        if not operators:
            operators = {"diff": 0.6, "rewrite": 0.4}
        unknown_ops = sorted(set(operators) - set(KNOWN_OPERATORS))
        if unknown_ops:
            raise ConfigError(
                f"search.operators: unknown operator(s) {unknown_ops}; "
                f"known operators are {list(KNOWN_OPERATORS)}"
            )
        if any(v < 0 for v in operators.values()):
            raise ConfigError("search.operators: shares must be >= 0")
        if sum(operators.values()) <= 0:
            raise ConfigError(
                "search.operators: at least one operator must have a share > 0"
            )
        return SearchConfig(
            children_per_generation=_as_int(
                data.get("children_per_generation", 4),
                "search.children_per_generation",
                minimum=1,
            ),
            generations=_as_int(
                data.get("generations", 10), "search.generations", minimum=1
            ),
            big_step_every=_as_int(
                data.get("big_step_every", 5), "search.big_step_every", minimum=1
            ),
            parent_top_k=_as_int(
                data.get("parent_top_k", 5), "search.parent_top_k", minimum=1
            ),
            seed=_as_int(data.get("seed", 0), "search.seed"),
            operators=operators,
            archive=ArchiveConfig.parse(data.get("archive")),
            parents_per_generation=_as_int(
                data.get("parents_per_generation", 0),
                "search.parents_per_generation",
                minimum=0,
            ),
            inspirations=_as_int(
                data.get("inspirations", 2), "search.inspirations", minimum=0
            ),
            scratchpad_every=_as_int(
                data.get("scratchpad_every", 5), "search.scratchpad_every", minimum=0
            ),
            novelty_retry=bool(data.get("novelty_retry", True)),
            novelty=NoveltyConfig.parse(data.get("novelty")),
            adaptive_children=AdaptiveChildrenConfig.parse(
                data.get("adaptive_children")
            ),
        )


# --------------------------------------------------------------------------
# root
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    problem: ProblemConfig
    evaluate: EvaluateConfig
    models: ModelsConfig
    budget: BudgetConfig
    stop: StopConfig
    search: SearchConfig
    base_dir: Path
    source: Path | None = None

    @property
    def final_stage(self) -> StageConfig:
        return self.evaluate.stages[-1]


def build_config(raw: Any, *, base_dir: Path, source: Path | None = None) -> Config:
    """Validate an already-parsed YAML document. Split out so tests can skip I/O."""
    data = _as_mapping(raw, "<root>")
    if not data:
        raise ConfigError("<root>: config is empty")
    _reject_unknown(
        data,
        {"extends", "problem", "evaluate", "models", "budget", "stop", "search"},
        "<root>",
    )
    data = {k: v for k, v in data.items() if k != "extends"}
    config = Config(
        problem=ProblemConfig.parse(_require(data, "problem", "<root>"), base_dir),
        evaluate=EvaluateConfig.parse(_require(data, "evaluate", "<root>")),
        models=ModelsConfig.parse(_require(data, "models", "<root>")),
        budget=BudgetConfig.parse(data.get("budget")),
        stop=StopConfig.parse(data.get("stop")),
        search=SearchConfig.parse(data.get("search")),
        base_dir=base_dir,
        source=source,
    )
    _check_embedding_route(config)
    return config


def _check_embedding_route(config: Config) -> None:
    """Reject an embedding novelty gate that cannot possibly work.

    Both failures here would otherwise surface as an exception from inside the
    search loop, after the seed had been evaluated and the first children paid
    for. A config error costs nothing.
    """
    near = config.search.novelty.near
    if near.method != "embedding":
        return
    slot = config.models.embed
    if slot is None:
        raise ConfigError(
            "search.novelty.near.method: 'embedding' requires a models.embed "
            "slot naming a backend with an embeddings API"
        )
    if slot.provider == "claude-cli":
        raise ConfigError(
            "models.embed.provider: 'claude-cli' has no embeddings API; use "
            "'azure' or 'openrouter', or set search.novelty.near.method to "
            "'local'"
        )


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    return _as_mapping(raw, "<root>")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """`override` on top of `base`. Two mappings merge key by key; anything
    else replaces outright -- including a `null`, which is how a child config
    clears an inherited mapping (`options: null`)."""
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _resolve_document(path: Path, seen: tuple[Path, ...] = ()) -> dict[str, Any]:
    """Read one config file and fold it onto whatever it `extends`.

    A variant config (a real-provider run of an example, say) should differ
    from its base in the handful of keys it actually changes, not by being a
    copy that drifts. `extends` is a path relative to the file that declares
    it; every other relative path -- `problem.skeleton`, a stage command's cwd
    -- still resolves against the file that was *loaded*, so a base and its
    variant belong in the same directory.
    """
    if path in seen:
        chain = " -> ".join(p.name for p in (*seen, path))
        raise ConfigError(f"extends: circular chain {chain}")
    data = _read_yaml(path)
    parent = data.get("extends")
    if parent is None:
        return data
    parent_path = (path.parent / _as_str(parent, "extends")).resolve()
    if not parent_path.is_file():
        raise ConfigError(f"extends: no such file: {parent_path}")
    return deep_merge(_resolve_document(parent_path, (*seen, path)), data)


def load_config(path: str | Path) -> Config:
    """Read `path` (and anything it `extends`) and return a validated `Config`."""
    config_path = Path(path).resolve()
    raw = _resolve_document(config_path)
    return build_config(raw, base_dir=config_path.parent, source=config_path)
