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
import shlex
import subprocess
import sys
import time
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from evolvekit.config import ProblemConfig, StageConfig
from evolvekit.evaluate.types import StageOutcome

__all__ = [
    "run_static_stage",
    "run_command_stage",
    "static_checks",
    "build_argv",
    "STDERR_LIMIT",
    "FEEDBACK_LIMIT",
]

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
    if not problems and stage.import_check:
        crash = _import_check(candidate_path, stage.timeout)
        if crash is not None:
            problems.append(crash.splitlines()[-1][:200] if crash.strip() else "import failed")
            stderr = crash
    return StageOutcome(
        stage_id=stage.id,
        ok=not problems,
        kpis=kpis,
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


def build_argv(
    command: str,
    *,
    candidate: Path,
    inputs: tuple[str, ...] | list[str],
    out: Path,
    seed: int = 0,
) -> list[str]:
    """Split the template first, substitute second.

    Splitting after substitution would let a Windows path's backslashes be eaten
    by POSIX shlex, and would let a filename with a space become two arguments.
    """
    tokens = shlex.split(command, posix=True)
    if not tokens:
        raise ValueError("stage command is empty")
    mapping = {
        "candidate": str(candidate),
        "inputs": ",".join(inputs),
        "out": str(out),
        "seed": str(seed),
    }
    return [token.format(**mapping) for token in tokens]


def run_command_stage(
    candidate_path: Path,
    stage: StageConfig,
    *,
    inputs: tuple[str, ...] | list[str],
    out_path: Path,
    cwd: Path,
    private: bool = False,
) -> StageOutcome:
    """Run the stage's evaluator `stage.seeds` times and combine the results.

    With the default `seeds: 1` this is one subprocess and one JSON file, as it
    always was. With more, `{seed}` takes 0, 1, ... N-1, each run gets its own
    output file and its own `stage.timeout`, the scalar KPIs are averaged, and
    the spread of each one is recorded as a coefficient of variation.

    The first failure ends the stage. Averaging over the runs that happened to
    survive would report a mean the candidate never achieved, and the failure
    is the more interesting fact anyway.
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
        )
    except (ValueError, KeyError, IndexError) as exc:
        return StageOutcome(
            stage_id=stage.id,
            ok=False,
            failure=f"bad command template: {exc}",
            duration_s=time.perf_counter() - started,
            private=private,
        )

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=stage.timeout,
            cwd=str(cwd),
        )
    except subprocess.TimeoutExpired:
        return StageOutcome(
            stage_id=stage.id,
            ok=False,
            failure=f"timeout after {stage.timeout:g}s",
            duration_s=time.perf_counter() - started,
            private=private,
        )
    except OSError as exc:
        return StageOutcome(
            stage_id=stage.id,
            ok=False,
            failure=f"could not run {argv[0]!r}: {exc}",
            duration_s=time.perf_counter() - started,
            private=private,
        )

    duration = time.perf_counter() - started
    stderr = (proc.stderr or "")[-STDERR_LIMIT:]
    stdout = (proc.stdout or "")[-STDERR_LIMIT:]
    if proc.returncode != 0:
        return StageOutcome(
            stage_id=stage.id,
            ok=False,
            failure=f"exit code {proc.returncode}",
            stderr=stderr,
            stdout=stdout,
            duration_s=duration,
            private=private,
        )

    kpis, vectors, feedback, problem = _read_kpis(out_path)
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
            kpis[str(key)] = float(value)
            continue
        if isinstance(value, list) and all(_is_number(v) for v in value):
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
