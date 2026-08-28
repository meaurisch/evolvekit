"""`python -m evolvekit preflight --config X` -- everything that can be checked
without spending anything.

The loop already refuses to breed children off a seed that cannot get through
the cascade, but it learns that *after* taking the run lock and building a run
directory, and it learns nothing at all about the questions that actually sink
a long run:

* Is a stage's `timeout` anywhere near what the stage really takes? A timeout
  set from a guess, on an evaluator whose real cost is an hour, kills every
  candidate at exactly the moment the run starts to matter.
* Can `budget.max_full_evals_per_day` even be reached in a day, given how long
  a full evaluation takes? A cap of 20 against a 2.5-hour evaluation is not a
  cap, it is a rounding error with a config key.
* Will the run's own settings ask for more full evaluations than a day holds?
* And, only when asked: does each configured model role answer at all, and what
  does one round trip cost?

None of that needs a run directory, a lock or an LLM call (the last question
excepted, and it is opt-in). So this is a separate command, it works in a
temporary directory, and it exits 0 clean / 1 warnings / 2 failures so a
wrapper script can gate on it.
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from evolvekit.candidate import extract_block, splice_block
from evolvekit.config import Config, ModelConfig, StageConfig
from evolvekit.evaluate.stages import run_command_stage, run_static_stage
from evolvekit.evaluate.types import StageOutcome
from evolvekit.ledger import price_completion
from evolvekit.providers import Provider, ProviderError, build_provider
from evolvekit.providers.base import embedding_tokens

__all__ = [
    "preflight",
    "run_preflight",
    "format_report",
    "PreflightReport",
    "StageReport",
    "ProviderReport",
    "PROVIDER_CHECK_PROMPT",
    "TIMEOUT_HEADROOM",
    "SECONDS_PER_DAY",
    "BASELINE_QUIET_MACHINE_S",
]

PROVIDER_CHECK_PROMPT = "Reply with the single word OK"
"""The smallest prompt that still proves a round trip: auth, routing, the
deployment name, and a response body. Deliberately not a question -- an
instruction the model can satisfy in one token keeps the check honest about
cost."""

TIMEOUT_HEADROOM = 1.5
"""A stage's timeout must be at least this multiple of its observed duration.

The seed is one candidate on one machine on one day. A child that does more
work than the seed is the normal case, not the exception, so a timeout with no
headroom is a timeout that will start killing the search's better ideas.
"""

SECONDS_PER_DAY = 24 * 60 * 60

BASELINE_QUIET_MACHINE_S = 60.0
"""A command stage timed out at or above this many seconds gets a reminder to
measure baselines on a quiet machine.

The PyVRP example's first baseline table was measured while a build agent was
running tests on the same machine; PyVRP is wall-clock bounded, so the
contention silently became worse solutions -- EUR 11,563.7 measured busy
against EUR 10,852 the same defaults reach quiet (`examples/pyvrp/README.md`
section 5). A stage timeout this long is the signature of a wall-clock-bounded
evaluator, which is exactly the shape that lesson applies to.
"""


# --------------------------------------------------------------------------
# report types
# --------------------------------------------------------------------------


@dataclass
class StageReport:
    """What one stage did to the seed candidate."""

    stage_id: str
    kind: str
    ok: bool
    duration_s: float
    timeout: float
    runs: int = 1
    private: bool = False
    kpis: dict[str, float] = field(default_factory=dict)
    kpi_cv: dict[str, float] = field(default_factory=dict)
    feedback: str = ""
    failure: str | None = None

    @property
    def per_run_s(self) -> float:
        """The number the timeout is actually compared against."""
        return self.duration_s / max(1, self.runs)

    @property
    def label(self) -> str:
        return f"{self.stage_id} (hold-out)" if self.private else self.stage_id


@dataclass
class ProviderReport:
    """One minimal round trip against one configured model role."""

    role: str
    provider: str
    model: str
    ok: bool
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    reply: str = ""
    error: str | None = None


@dataclass
class PreflightReport:
    stages: list[StageReport] = field(default_factory=list)
    providers: list[ProviderReport] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """0 clean, 1 warnings, 2 failures. Failures win."""
        if self.failures:
            return 2
        return 1 if self.warnings else 0

    @property
    def full_eval_s(self) -> float:
        """Wall clock for one candidate's *final* stage, hold-out included."""
        return sum(s.duration_s for s in self.stages if s.stage_id == self._final_id)

    @property
    def cheap_s(self) -> float:
        """Wall clock for one candidate's stages before the final one."""
        return sum(s.duration_s for s in self.stages if s.stage_id != self._final_id)

    _final_id: str = ""


# --------------------------------------------------------------------------
# running it
# --------------------------------------------------------------------------


def preflight(
    config: Config,
    *,
    provider_check: bool = False,
    providers: dict[str, Provider] | None = None,
    work_dir: str | Path | None = None,
    candidate: str | Path | None = None,
) -> PreflightReport:
    """Run a candidate through every stage and check the settings against it.

    `candidate` is the full spliced source of a real candidate (the shape
    `evaluate/cascade.py` writes under a run's `candidates/` directory) --
    pass it to base timeout advice on what a real search step costs rather
    than the seed, which on some problems does near-nothing (see the
    circle-packing seed, which uses ~0% of its stage budgets). Defaults to
    the seed when omitted.
    """
    report = PreflightReport()
    report._final_id = config.final_stage.id
    candidate_path = Path(candidate) if candidate is not None else None

    if work_dir is None:
        with tempfile.TemporaryDirectory(prefix="evolvekit-preflight-") as tmp:
            _run_stages(config, report, Path(tmp), candidate_path)
    else:
        directory = Path(work_dir)
        directory.mkdir(parents=True, exist_ok=True)
        _run_stages(config, report, directory, candidate_path)

    _check_timeouts(config, report)
    _check_budget(config, report)
    if provider_check:
        _check_providers(config, report, providers or {})
    return report


def _run_stages(
    config: Config,
    report: PreflightReport,
    work_dir: Path,
    candidate: Path | None = None,
) -> None:
    if candidate is not None:
        source = candidate.read_text(encoding="utf-8")
    else:
        skeleton = config.problem.skeleton.read_text(encoding="utf-8")
        _, block, _ = extract_block(
            skeleton, config.problem.block_start, config.problem.block_end
        )
        source = splice_block(
            skeleton, block, config.problem.block_start, config.problem.block_end
        )
    candidate_path = work_dir / "seed_candidate.py"
    candidate_path.write_text(source, encoding="utf-8", newline="\n")

    for stage in config.evaluate.stages:
        if stage.kind == "builtin-static":
            outcome = run_static_stage(candidate_path, source, stage, config.problem)
            report.stages.append(_stage_report(stage, outcome))
            if not outcome.ok:
                report.failures.append(
                    f"stage {stage.id}: the seed does not pass the static stage "
                    f"-- {outcome.failure}"
                )
                # Nothing downstream can be trusted once the seed is invalid.
                return
            continue

        outcome = run_command_stage(
            candidate_path,
            stage,
            inputs=stage.inputs,
            out_path=work_dir / f"{stage.id}.json",
            cwd=config.base_dir,
        )
        report.stages.append(_stage_report(stage, outcome))
        if not outcome.ok:
            report.failures.append(f"stage {stage.id}: {outcome.failure}")
            return

        if stage.private_inputs:
            private = run_command_stage(
                candidate_path,
                stage,
                inputs=stage.private_inputs,
                out_path=work_dir / f"{stage.id}.private.json",
                cwd=config.base_dir,
                private=True,
            )
            report.stages.append(_stage_report(stage, private))
            if not private.ok:
                report.failures.append(
                    f"stage {stage.id} hold-out: {private.failure}"
                )
                return


def _stage_report(stage: StageConfig, outcome: StageOutcome) -> StageReport:
    return StageReport(
        stage_id=stage.id,
        kind=stage.kind,
        ok=outcome.ok,
        duration_s=outcome.duration_s,
        timeout=stage.timeout,
        runs=outcome.runs,
        private=outcome.private,
        kpis=dict(outcome.kpis),
        kpi_cv=dict(outcome.kpi_cv),
        feedback=outcome.text_feedback,
        failure=outcome.failure,
    )


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------


def _check_timeouts(config: Config, report: PreflightReport) -> None:
    for stage in report.stages:
        if not stage.ok:
            continue
        needed = stage.per_run_s * TIMEOUT_HEADROOM
        if stage.timeout < needed:
            report.warnings.append(
                f"stage {stage.label}: timeout {stage.timeout:g}s is less than "
                f"{TIMEOUT_HEADROOM:g}x the {stage.per_run_s:.1f}s the seed took "
                f"({needed:.1f}s). A child that does more work than the seed -- "
                "the normal case -- will be killed rather than scored."
            )
        elif stage.timeout > stage.per_run_s * 100 and stage.kind == "command":
            report.notes.append(
                f"stage {stage.label}: timeout {stage.timeout:g}s is {stage.timeout / max(stage.per_run_s, 1e-9):.0f}x "
                f"the seed's {stage.per_run_s:.2f}s. Generous is fine; just know "
                "that a hung candidate costs that long before it is killed."
            )
        if stage.kind == "command" and stage.timeout >= BASELINE_QUIET_MACHINE_S:
            report.notes.append(
                f"stage {stage.label}: timeout {stage.timeout:g}s is at or above "
                f"{BASELINE_QUIET_MACHINE_S:g}s. If this evaluator is wall-clock "
                "bounded (a solver given a time budget rather than an iteration "
                "count), CPU contention on this machine silently becomes solution "
                "quality. Measure baselines on a quiet machine, and never compare "
                "numbers measured under different load."
            )


def _full_evals_per_generation(config: Config) -> int:
    """Upper bound on candidates reaching the final stage in one generation."""
    children = config.search.children_per_generation
    adaptive = config.search.adaptive_children
    if adaptive is not None:
        children = adaptive.max
    stages = config.evaluate.stages
    if len(stages) < 2:
        return children
    gate = stages[-2].promote.top_k_per_generation
    return min(children, gate) if gate is not None else children


def _check_budget(config: Config, report: PreflightReport) -> None:
    full_s = report.full_eval_s
    if full_s <= 0:
        return
    cap = config.budget.max_full_evals_per_day
    daily_s = cap * full_s
    if daily_s > SECONDS_PER_DAY:
        report.warnings.append(
            f"budget.max_full_evals_per_day is {cap}, but one full evaluation "
            f"took {_duration(full_s)}, so the cap needs {_duration(daily_s)} of "
            "wall clock to reach. The clock is the real limit, not the cap; set "
            f"it to about {max(1, int(SECONDS_PER_DAY // full_s))} so the number "
            "in the config means what it says."
        )

    per_gen = _full_evals_per_generation(config)
    demanded = per_gen * config.search.generations
    if demanded > cap:
        days = -(-demanded // cap)
        report.warnings.append(
            f"search.generations x {per_gen} promoted candidate(s) is up to "
            f"{demanded} full evaluation(s), against a cap of {cap} per day: "
            f"this run spans at least {days} day(s) of calendar time even if "
            "nothing else stops it."
        )

    projected = config.search.generations * (
        _breadth(config) * report.cheap_s + per_gen * full_s
    )
    if projected > SECONDS_PER_DAY:
        report.warnings.append(
            f"projected evaluator wall clock for {config.search.generations} "
            f"generation(s) is about {_duration(projected)} "
            f"({_breadth(config)} child(ren) x {_duration(report.cheap_s)} of "
            f"cheap stages, plus up to {per_gen} x {_duration(full_s)} full). "
            "Budget the calendar, not just the dollars."
        )
    else:
        report.notes.append(
            f"projected evaluator wall clock for the whole run: about "
            f"{_duration(projected)}, excluding model latency."
        )

    noisy = [s for s in report.stages if s.runs > 1]
    for stage in noisy:
        worst = max(stage.kpi_cv.values(), default=0.0)
        report.notes.append(
            f"stage {stage.label} ran {stage.runs} seed(s); the noisiest KPI "
            f"varied by {worst * 100:.2f}% across them. The behaviour signature "
            f"uses {config.evaluate.signature_digits_stochastic} significant "
            "digits on this stage."
        )


def _breadth(config: Config) -> int:
    adaptive = config.search.adaptive_children
    return adaptive.max if adaptive is not None else config.search.children_per_generation


# --------------------------------------------------------------------------
# the optional provider round trip
# --------------------------------------------------------------------------


def _configured_roles(config: Config) -> list[str]:
    roles = ["small", "strong"]
    if config.models.embed is not None:
        roles.append("embed")
    return roles


def _check_providers(
    config: Config, report: PreflightReport, providers: dict[str, Provider]
) -> None:
    for role in _configured_roles(config):
        model_cfg = config.models.by_role(role)
        try:
            provider = providers.get(role) or build_provider(
                model_cfg, base_dir=config.base_dir
            )
        except ProviderError as exc:
            report.providers.append(
                ProviderReport(
                    role=role,
                    provider=model_cfg.provider,
                    model=model_cfg.model,
                    ok=False,
                    error=str(exc),
                )
            )
            report.failures.append(f"models.{role}: {exc}")
            continue
        outcome = (
            _embed_once(role, provider, model_cfg)
            if role == "embed"
            else _complete_once(role, provider, model_cfg)
        )
        report.providers.append(outcome)
        if not outcome.ok:
            report.failures.append(f"models.{role}: {outcome.error}")


def _complete_once(
    role: str, provider: Provider, model_cfg: ModelConfig
) -> ProviderReport:
    started = time.perf_counter()
    try:
        completion = provider.complete(
            [{"role": "user", "content": PROVIDER_CHECK_PROMPT}],
            model=model_cfg.model,
            # One word back. Not the configured ceiling: the point is to prove
            # the round trip, not to buy a paragraph of it.
            max_tokens=min(model_cfg.max_tokens, 64),
            temperature=model_cfg.temperature,
        )
    except ProviderError as exc:
        return ProviderReport(
            role=role,
            provider=model_cfg.provider,
            model=model_cfg.model,
            ok=False,
            latency_s=time.perf_counter() - started,
            error=str(exc),
        )
    return ProviderReport(
        role=role,
        provider=completion.provider,
        model=completion.model,
        ok=True,
        latency_s=time.perf_counter() - started,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        usd=price_completion(completion, model_cfg),
        reply=" ".join(completion.text.split())[:60],
    )


def _embed_once(
    role: str, provider: Provider, model_cfg: ModelConfig
) -> ProviderReport:
    started = time.perf_counter()
    embed = getattr(provider, "embed", None)
    if embed is None:
        return ProviderReport(
            role=role,
            provider=model_cfg.provider,
            model=model_cfg.model,
            ok=False,
            error=f"the {model_cfg.provider!r} backend has no embeddings API",
        )
    try:
        vectors = embed([PROVIDER_CHECK_PROMPT], model=model_cfg.model)
    except ProviderError as exc:
        return ProviderReport(
            role=role,
            provider=model_cfg.provider,
            model=model_cfg.model,
            ok=False,
            latency_s=time.perf_counter() - started,
            error=str(exc),
        )
    tokens = embedding_tokens(provider, [PROVIDER_CHECK_PROMPT])
    return ProviderReport(
        role=role,
        provider=model_cfg.provider,
        model=model_cfg.model,
        ok=True,
        latency_s=time.perf_counter() - started,
        input_tokens=tokens,
        usd=tokens / 1_000_000.0 * model_cfg.price_in_per_mtok,
        reply=f"{len(vectors[0])}-dimensional vector",
    )


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.1f}s"
    if seconds < 5400:
        return f"{seconds / 60:.1f}min"
    return f"{seconds / 3600:.1f}h"


def format_report(
    report: PreflightReport, config: Config, *, candidate: str | Path | None = None
) -> str:
    header = f"preflight: {config.source or '<config>'}"
    if candidate is not None:
        header += f"  (candidate: {candidate})"
    lines = [header, ""]
    for stage in report.stages:
        verdict = "ok" if stage.ok else "FAILED"
        runs = f" x{stage.runs} seed(s)" if stage.runs > 1 else ""
        lines.append(
            f"stage {stage.label:<20} {verdict:<7} {_duration(stage.duration_s)}"
            f"{runs}  (timeout {stage.timeout:g}s per run)"
        )
        if stage.failure:
            lines.append(f"    failure : {stage.failure}")
        if stage.kpis:
            lines.append("    kpis    : " + _kpi_line(stage))
        if stage.feedback:
            head = stage.feedback.splitlines()
            for line in head[:8]:
                lines.append(f"    | {line}")
            if len(head) > 8:
                lines.append(f"    | ... {len(head) - 8} more line(s)")
        lines.append("")

    for check in report.providers:
        if check.ok:
            lines.append(
                f"model {check.role:<7} {check.provider}/{check.model}: ok in "
                f"{check.latency_s:.2f}s, {check.input_tokens} in / "
                f"{check.output_tokens} out, ${check.usd:.6f} -- {check.reply!r}"
            )
        else:
            lines.append(
                f"model {check.role:<7} {check.provider}/{check.model}: FAILED "
                f"-- {check.error}"
            )
    if report.providers:
        lines.append("")

    for note in report.notes:
        lines.append(f"note    : {note}")
    for warning in report.warnings:
        lines.append(f"WARNING : {warning}")
    for failure in report.failures:
        lines.append(f"FAILURE : {failure}")
    if report.notes or report.warnings or report.failures:
        lines.append("")

    verdicts = {0: "clean", 1: "warnings", 2: "failures"}
    lines.append(
        f"verdict : {verdicts[report.exit_code]} "
        f"({len(report.failures)} failure(s), {len(report.warnings)} warning(s))"
    )
    return "\n".join(lines)


def _kpi_line(stage: StageReport, limit: int = 6) -> str:
    items = sorted(stage.kpis.items())[:limit]
    parts = []
    for name, value in items:
        cv = stage.kpi_cv.get(name)
        suffix = f" (cv {cv * 100:.2f}%)" if cv else ""
        parts.append(f"{name}={value:.6g}{suffix}")
    if len(stage.kpis) > limit:
        parts.append(f"... {len(stage.kpis) - limit} more")
    return ", ".join(parts)


def run_preflight(
    config: Config,
    *,
    provider_check: bool = False,
    providers: dict[str, Provider] | None = None,
    candidate: str | Path | None = None,
    log: Callable[[str], None] = print,
) -> int:
    """`preflight` + `format_report`, printed. Returns the exit code."""
    report = preflight(
        config, provider_check=provider_check, providers=providers, candidate=candidate
    )
    log(format_report(report, config, candidate=candidate))
    return report.exit_code
