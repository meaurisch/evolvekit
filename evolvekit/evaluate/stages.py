"""The two stage kinds: `builtin-static` and `command`.

`builtin-static` is the only rung allowed to hard-reject: a syntax error, a
forbidden import, a missing required function, or a module that blows up on
import means the candidate never enters the archive. Everything downstream is
a penalty or a `failure_score`, never a `None`.

The static stage never `exec`s candidate code in this process -- v1 did, and
that is one unsandboxed `import os` away from a bad afternoon. The optional
import check runs in a child interpreter with the stage timeout.
"""

from __future__ import annotations

import ast
import json
import math
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, stdev
from typing import Any, Callable

from evolvekit.config import ProblemConfig, StageConfig
from evolvekit.evaluate.process import read_tail, run_bounded
from evolvekit.evaluate.types import StageOutcome

__all__ = [
    "run_static_stage",
    "run_command_stage",
    "static_checks",
    "build_argv",
    "Configuration",
    "STDERR_LIMIT",
    "FEEDBACK_LIMIT",
]

Observer = Callable[..., None]
"""`observer(type, **fields)`: told about every evaluator run as it starts and
as it ends. The cascade supplies one that knows which candidate this is."""

STDERR_LIMIT = 2000
"""Prompt artefacts are truncated here; the post-mortem's context blow-up was
partly verbatim failure dumps."""

FEEDBACK_LIMIT = 2000
"""`text_feedback` is truncated here by the framework, not by the evaluator.

An evaluator that knows something the KPIs cannot say -- which distribution was
worst, how much of the time budget went unused -- should be able to say it in a
sentence. An evaluator that decides to say it in forty kilobytes should not be
able to put forty kilobytes into every subsequent prompt."""

_DANGEROUS_CALLS = frozenset(
    {"eval", "exec", "compile", "__import__", "open", "globals", "input"}
)


def static_checks(
    source: str, problem: ProblemConfig
) -> tuple[list[str], dict[str, float]]:
    """AST-only validation. Returns `(problems, kpis)`; empty problems == pass."""
    problems: list[str] = []
    kpis: dict[str, float] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"SyntaxError: {exc.msg} (line {exc.lineno})"], {"complexity": -1.0}

    kpis["complexity"] = float(sum(1 for _ in ast.walk(tree)))

    forbidden = set(problem.forbidden_imports)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    banned = sorted(imported & forbidden)
    if banned:
        problems.append(f"forbidden import(s): {banned}")

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    dangerous = sorted(called & _DANGEROUS_CALLS)
    if dangerous:
        problems.append(f"forbidden call(s): {dangerous}")

    defined = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = [name for name in problem.required_functions if name not in defined]
    if missing:
        problems.append(
            f"missing required top-level function(s): {missing}; "
            f"the module defines {sorted(defined)}"
        )

    kpis["static_problems"] = float(len(problems))
    return problems, kpis


def run_static_stage(
    candidate_path: Path,
    source: str,
    stage: StageConfig,
    problem: ProblemConfig,
) -> StageOutcome:
    """Stage 0: AST checks plus an optional child-interpreter import check."""
    started = time.perf_counter()
    problems, kpis = static_checks(source, problem)
    stderr = ""
    params: dict[str, Any] | None = None
    if not problems and problem.parameters is not None:
        # The candidate has to be run to know what it configures, so the import
        # check and the resolution are one child interpreter, not two.
        values, crash = _resolve_parameters(candidate_path, stage.timeout)
        if crash is not None:
            problems.append(crash.splitlines()[-1][:200] if crash.strip() else "configure() failed")
            stderr = crash
        else:
            params, invalid = problem.parameters.validate(values)
            problems.extend(f"configure() returned an invalid configuration -- {p}" for p in invalid)
            if invalid:
                params = None
    elif not problems and stage.import_check:
        crash = _import_check(candidate_path, stage.timeout)
        if crash is not None:
            problems.append(crash.splitlines()[-1][:200] if crash.strip() else "import failed")
            stderr = crash
    return StageOutcome(
        stage_id=stage.id,
        ok=not problems,
        kpis=kpis,
        params=params,
        failure="; ".join(problems) if problems else None,
        stderr=stderr[-STDERR_LIMIT:],
        duration_s=time.perf_counter() - started,
    )


def _import_check(candidate_path: Path, timeout: float) -> str | None:
    """Import the candidate in a child interpreter. Returns stderr on failure."""
    snippet = (
        "import importlib.util as u, sys;"
        "spec = u.spec_from_file_location('evolvekit_candidate', sys.argv[1]);"
        "mod = u.module_from_spec(spec);"
        "spec.loader.exec_module(mod)"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", snippet, str(candidate_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"import timed out after {timeout:g}s"
    except OSError as exc:  # pragma: no cover - interpreter is always present
        return f"could not start interpreter: {exc}"
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout or "import failed").strip()
    return None


@dataclass(frozen=True)
class Configuration:
    """A candidate's validated parameters, in the two shapes a command can take
    them: `--flag value` arguments for `{params}`, a JSON file for
    `{params_json}`."""

    flags: tuple[str, ...] = ()
    json_path: Path | None = None


def _substitutions(configuration: "Configuration | None") -> dict[str, Any]:
    if configuration is None:
        return {}
    return {"params_flags": list(configuration.flags), "params_json": configuration.json_path}


_PARAMS_MARKER = "__EVOLVEKIT_PARAMS__"


def _resolve_parameters(
    candidate_path: Path, timeout: float
) -> tuple[Any, str | None]:
    """Import the candidate in a child interpreter and call `configure()`.

    Returns `(values, None)` or `(None, what went wrong)`. The values come back
    as JSON on a marked line, so anything the candidate prints is harmless.
    """
    snippet = (
        "import importlib.util as u, json, sys;"
        "spec = u.spec_from_file_location('evolvekit_candidate', sys.argv[1]);"
        "mod = u.module_from_spec(spec);"
        "spec.loader.exec_module(mod);"
        f"print('\\n{_PARAMS_MARKER}' + json.dumps(mod.configure()))"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", snippet, str(candidate_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"configure() timed out after {timeout:g}s"
    except OSError as exc:  # pragma: no cover - interpreter is always present
        return None, f"could not start interpreter: {exc}"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or "configure() failed").strip()
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith(_PARAMS_MARKER):
            try:
                return json.loads(line[len(_PARAMS_MARKER):]), None
            except json.JSONDecodeError as exc:
                return None, f"configure() returned something that is not JSON: {exc}"
    return None, "configure() returned nothing"


def build_argv(
    command: str,
    *,
    candidate: Path,
    inputs: tuple[str, ...] | list[str],
    out: Path,
    seed: int = 0,
    params_flags: list[str] | None = None,
    params_json: Path | None = None,
) -> list[str]:
    """Split the template first, substitute second.

    Splitting after substitution would let a Windows path's backslashes be eaten
    by POSIX shlex, and would let a filename with a space become two arguments.

    `{params}` on its own expands to *several* arguments -- one `--flag value`
    pair per declared parameter -- which is the other reason substitution has
    to happen after the split. `{params_json}` is the path of a JSON file with
    the same values, for a solver that would rather read a file.
    """
    tokens = shlex.split(command, posix=True)
    if not tokens:
        raise ValueError("stage command is empty")
    flags = list(params_flags or [])
    mapping = {
        "candidate": str(candidate),
        "inputs": ",".join(inputs),
        "out": str(out),
        "seed": str(seed),
        "params": " ".join(flags),
        "params_json": str(params_json) if params_json is not None else "",
    }
    argv: list[str] = []
    for token in tokens:
        if token == "{params}":
            argv.extend(flags)
        else:
            argv.append(token.format(**mapping))
    return argv


def run_command_stage(
    candidate_path: Path,
    stage: StageConfig,
    *,
    inputs: tuple[str, ...] | list[str],
    out_path: Path,
    cwd: Path,
    private: bool = False,
    required_kpis: tuple[str, ...] = (),
    observer: Observer | None = None,
    configuration: "Configuration | None" = None,
) -> StageOutcome:
    """Run the stage's evaluator `stage.seeds` times and combine the results.

    With the default `seeds: 1` this is one subprocess and one JSON file, as it
    always was. With more, `{seed}` takes 0, 1, ... N-1, each run gets its own
    output file and its own `stage.timeout`, the scalar KPIs are averaged, and
    the spread of each one is recorded as a coefficient of variation.

    The first failure ends the stage. Averaging over the runs that happened to
    survive would report a mean the candidate never achieved, and the failure
    is the more interesting fact anyway.

    `required_kpis` names scalar KPIs every run must report -- the caller passes
    `evaluate.score.objective`. A run that leaves one out is a failed run, for
    the reason spelled out in `_missing_required`.
    """
    if stage.seeds <= 1:
        return _run_once(
            candidate_path,
            stage,
            inputs=inputs,
            out_path=out_path,
            cwd=cwd,
            private=private,
            seed=0,
            required_kpis=required_kpis,
            observer=observer,
            configuration=configuration,
        )
    outcomes: list[StageOutcome] = []
    for seed in range(stage.seeds):
        outcome = _run_once(
            candidate_path,
            stage,
            inputs=inputs,
            out_path=_seeded_path(out_path, seed),
            cwd=cwd,
            private=private,
            seed=seed,
            required_kpis=required_kpis,
            observer=observer,
            configuration=configuration,
        )
        outcomes.append(outcome)
        if not outcome.ok:
            failed = _combine_failure(outcomes)
            return failed
    return _combine(outcomes)


def _seeded_path(out_path: Path, seed: int) -> Path:
    return out_path.with_name(f"{out_path.stem}.seed{seed}{out_path.suffix}")


def _combine_failure(outcomes: list[StageOutcome]) -> StageOutcome:
    """The failing run, carrying the total time every run had already cost."""
    failed = outcomes[-1]
    failed.duration_s = sum(o.duration_s for o in outcomes)
    failed.runs = len(outcomes)
    if len(outcomes) > 1:
        failed.failure = (
            f"{failed.failure} (on seed {len(outcomes) - 1} of {len(outcomes)} run)"
        )
    return failed


def _cv(values: list[float]) -> float:
    """Coefficient of variation: spread relative to the mean.

    Zero when every run agreed. When the mean is zero there is no ratio to
    take, so the absolute spread is reported instead -- a number that is still
    zero for a deterministic KPI, which is the property this is read for.
    """
    if len(values) < 2:
        return 0.0
    spread = stdev(values)
    if spread == 0.0:
        return 0.0
    mean_value = fmean(values)
    return spread / abs(mean_value) if mean_value != 0.0 else spread


def _combine(outcomes: list[StageOutcome]) -> StageOutcome:
    """Average N successful runs of one stage into a single outcome."""
    first = outcomes[0]
    keys = set(first.kpis)
    for outcome in outcomes[1:]:
        keys &= set(outcome.kpis)
    kpis = {k: fmean([o.kpis[k] for o in outcomes]) for k in sorted(keys)}
    kpi_cv = {k: _cv([o.kpis[k] for o in outcomes]) for k in sorted(keys)}

    vectors: dict[str, list[float]] = {}
    for name in sorted(set(first.vector_kpis)):
        series = [o.vector_kpis.get(name) for o in outcomes]
        if any(v is None for v in series) or len({len(v) for v in series}) != 1:  # type: ignore[arg-type]
            # Runs that disagree about how many instances there were cannot be
            # averaged element by element. The first run's vector is a truthful
            # sample; a ragged mean would not be.
            vectors[name] = list(first.vector_kpis[name])
            continue
        vectors[name] = [fmean(column) for column in zip(*series)]  # type: ignore[arg-type]

    return StageOutcome(
        stage_id=first.stage_id,
        ok=True,
        kpis=kpis,
        vector_kpis=vectors,
        kpi_cv=kpi_cv,
        # The first run's, not a concatenation: N notes about the same program
        # is N times the prompt for no extra information.
        text_feedback=first.text_feedback,
        stderr=first.stderr,
        stdout=first.stdout,
        duration_s=sum(o.duration_s for o in outcomes),
        runs=len(outcomes),
        private=first.private,
    )


def _run_once(
    candidate_path: Path,
    stage: StageConfig,
    *,
    inputs: tuple[str, ...] | list[str],
    out_path: Path,
    cwd: Path,
    private: bool = False,
    seed: int = 0,
    required_kpis: tuple[str, ...] = (),
    observer: Observer | None = None,
    configuration: "Configuration | None" = None,
) -> StageOutcome:
    """One evaluator run, announced to `observer` before and after.

    The two events bracket the subprocess itself, so a reader of the event log
    can tell at any moment which run is in flight and for how long it has been.
    """
    if observer is not None:
        observer("eval_started", seed=seed, timeout_s=stage.timeout)
    outcome = _execute_once(
        candidate_path,
        stage,
        inputs=inputs,
        out_path=out_path,
        cwd=cwd,
        private=private,
        seed=seed,
        required_kpis=required_kpis,
        configuration=configuration,
    )
    outcome.stdout_log = str(out_path.with_suffix(".stdout.log"))
    outcome.stderr_log = str(out_path.with_suffix(".stderr.log"))
    try:
        outcome.argv = tuple(
            build_argv(
                stage.command,
                candidate=candidate_path,
                inputs=inputs,
                out=out_path,
                seed=seed,
                **_substitutions(configuration),
            )
        )
    except (ValueError, KeyError, IndexError):
        pass  # a bad template: the outcome's own failure already says so
    if observer is not None:
        failed = (
            {
                "failure": outcome.failure,
                "stderr_tail": outcome.stderr,
                "stdout_tail": outcome.stdout,
            }
            if not outcome.ok
            else {}
        )
        observer(
            "eval_finished",
            seed=seed,
            ok=outcome.ok,
            duration_s=round(outcome.duration_s, 3),
            kpis=outcome.kpis,
            vector_kpis=outcome.vector_kpis,
            argv=list(outcome.argv),
            stdout_log=outcome.stdout_log,
            stderr_log=outcome.stderr_log,
            **failed,
        )
    return outcome


def _execute_once(
    candidate_path: Path,
    stage: StageConfig,
    *,
    inputs: tuple[str, ...] | list[str],
    out_path: Path,
    cwd: Path,
    private: bool = False,
    seed: int = 0,
    required_kpis: tuple[str, ...] = (),
    configuration: "Configuration | None" = None,
) -> StageOutcome:
    """Run one external evaluator and read the KPI JSON it wrote to `{out}`."""
    started = time.perf_counter()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)
    try:
        argv = build_argv(
            stage.command,
            candidate=candidate_path,
            inputs=inputs,
            out=out_path,
            seed=seed,
            **_substitutions(configuration),
        )
    except (ValueError, KeyError, IndexError) as exc:
        return StageOutcome(
            stage_id=stage.id,
            ok=False,
            failure=f"bad command template: {exc}",
            duration_s=time.perf_counter() - started,
            private=private,
        )

    # Output goes to files beside the result, never to pipes: see `process.py`.
    stdout_log = out_path.with_suffix(".stdout.log")
    stderr_log = out_path.with_suffix(".stderr.log")
    run = run_bounded(
        argv,
        timeout=stage.timeout,
        cwd=cwd,
        stdout_path=stdout_log,
        stderr_path=stderr_log,
    )
    duration = time.perf_counter() - started
    if run.error is not None:
        return StageOutcome(
            stage_id=stage.id,
            ok=False,
            failure=f"could not run {argv[0]!r}: {run.error}",
            duration_s=duration,
            private=private,
        )
    stderr = read_tail(stderr_log, STDERR_LIMIT)
    stdout = read_tail(stdout_log, STDERR_LIMIT)
    if run.timed_out:
        return StageOutcome(
            stage_id=stage.id,
            ok=False,
            failure=f"timeout after {stage.timeout:g}s",
            stderr=stderr,
            stdout=stdout,
            duration_s=duration,
            private=private,
        )
    if run.returncode != 0:
        return StageOutcome(
            stage_id=stage.id,
            ok=False,
            failure=f"exit code {run.returncode}",
            stderr=stderr,
            stdout=stdout,
            duration_s=duration,
            private=private,
        )

    kpis, vectors, feedback, problem = _read_kpis(out_path)
    if problem is None:
        problem = _missing_required(kpis, required_kpis)
    if problem is not None:
        return StageOutcome(
            stage_id=stage.id,
            ok=False,
            failure=problem,
            stderr=stderr,
            stdout=stdout,
            duration_s=duration,
            private=private,
        )
    return StageOutcome(
        stage_id=stage.id,
        ok=True,
        kpis=kpis,
        vector_kpis=vectors,
        text_feedback=feedback,
        stderr=stderr,
        stdout=stdout,
        duration_s=duration,
        private=private,
    )


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _is_finite(value: Any) -> bool:
    try:
        return math.isfinite(value)
    except OverflowError:  # an integer literal beyond the range of a float
        return False


def _non_finite(key: object, value: Any) -> str:
    """`json.loads` accepts `NaN` and `Infinity`, and a solver that found no
    feasible solution prints exactly those. Scoring used to coerce them to 0.0,
    which under `direction: minimize` is the best score a run can hold."""
    return (
        f"KPI {key!r} was {value!r}; a KPI must be a finite number. Report a run "
        "that produced no usable value with a non-zero exit code, or as a finite "
        "penalty KPI"
    )


def _missing_required(
    kpis: dict[str, float], required: tuple[str, ...]
) -> str | None:
    """A run that did not report the objective has not been scored.

    `compute_score` counts an absent KPI as 0 so that a forgotten *secondary*
    weight cannot blow a run up three hours in. For the objective itself that
    default is wrong in the worst possible direction: minimising a cost, 0
    outranks every candidate that actually ran. So the objective is checked
    here, where the evaluator's output is read, and its absence fails the run.
    """
    missing = [name for name in required if name not in kpis]
    if not missing:
        return None
    wanted = ", ".join(repr(name) for name in missing)
    return (
        f"evaluator reported no {wanted}, the objective KPI named by "
        f"evaluate.score.objective; it reported: {', '.join(sorted(kpis))}"
    )


def _read_kpis(
    out_path: Path,
) -> tuple[dict[str, float], dict[str, list[float]], str, str | None]:
    """Split the evaluator's output into scalars, vectors and prose.

    Scalars are the contract every consumer already relies on -- the score, the
    penalties, the archive descriptors. A list value is accepted too and kept
    aside: only the behaviour signature reads it, so an evaluator that emits
    nothing but scalars behaves exactly as it did before. `text_feedback` sits
    beside `kpis` rather than inside it, is optional, and is truncated here.
    """
    if not out_path.is_file():
        return {}, {}, "", f"evaluator wrote no output file at {out_path.name}"
    try:
        payload: Any = json.loads(out_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {}, {}, "", f"evaluator output was not readable JSON: {exc}"
    if not isinstance(payload, dict):
        return {}, {}, "", "evaluator output must be a JSON object"

    feedback = payload.get("text_feedback")
    if feedback is not None and not isinstance(feedback, str):
        return (
            {},
            {},
            "",
            f"'text_feedback' was {type(feedback).__name__}; it must be a string",
        )
    note = (feedback or "").strip()[:FEEDBACK_LIMIT]

    raw = payload.get("kpis")
    if raw is None:
        # The flat shape: the whole document is the KPI mapping. `text_feedback`
        # is the framework's key, not a KPI, so it never counts as one.
        raw = {k: v for k, v in payload.items() if k != "text_feedback"}
    if not isinstance(raw, dict) or not raw:
        return {}, {}, "", "evaluator output contained no 'kpis' mapping"
    kpis: dict[str, float] = {}
    vectors: dict[str, list[float]] = {}
    for key, value in raw.items():
        if _is_number(value):
            if not _is_finite(value):
                return {}, {}, "", _non_finite(key, value)
            kpis[str(key)] = float(value)
            continue
        if isinstance(value, list) and all(_is_number(v) for v in value):
            if not all(_is_finite(v) for v in value):
                return {}, {}, "", _non_finite(key, value)
            vectors[str(key)] = [float(v) for v in value]
            continue
        return (
            {},
            {},
            "",
            f"KPI {key!r} was {value!r}; KPIs must be numbers or lists of numbers",
        )
    if not kpis:
        return {}, {}, "", "evaluator output contained no scalar KPI"
    return kpis, vectors, note, None
