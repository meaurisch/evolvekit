"""The three stage kinds: `builtin-static`, `builtin-build` and `command`.

`builtin-static` is the only rung allowed to hard-reject: a syntax error, a
forbidden import, a missing required function, or a module that blows up on
import means the candidate never enters the archive. Everything downstream is
a penalty or a `failure_score`, never a `None` -- except `builtin-build`, which
a tree problem with `problem.tree.build` has right after it: a change that
does not build is refused too, with the build's own last words as the feedback.

The static stage never `exec`s candidate code in this process -- v1 did, and
that is one unsandboxed `import os` away from a bad afternoon. The optional
import check runs in a child interpreter with the stage timeout.
"""

from __future__ import annotations

import ast
import json
import os
import re
import math
import shlex
import string
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, stdev, variance
from typing import TYPE_CHECKING, Any, Callable, Sequence

from evolvekit import resources
from evolvekit.config import ProblemConfig, StageConfig, embedded_params
from evolvekit.app.detach import no_window_flags
from evolvekit.evaluate.cache import EvalCache
from evolvekit.evaluate.hostload import HostLoad
from evolvekit.evaluate.process import TAIL_BYTES, read_tail, run_bounded
from evolvekit.evaluate.types import StageOutcome
from evolvekit.stopping import AnyEvent

if TYPE_CHECKING:  # `evolvekit.tree` is imported only for a tree problem
    from evolvekit.tree import Tree

__all__ = [
    "run_static_stage",
    "run_tree_static_stage",
    "run_build_stage",
    "restore_build",
    "build_command",
    "run_command_stage",
    "static_checks",
    "build_argv",
    "render_app",
    "on_tree",
    "Configuration",
    "UnitContext",
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
            if not invalid:
                # `problem.parameter_constraints`: refused here, before the
                # configuration can cost a second of solver time.
                problems.extend(f"the configuration {b}" for b in problem.parameters.violations(params))
            if problems:
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


def run_tree_static_stage(
    tree_path: Path,
    diff: str,
    error: str | None,
    stage: StageConfig,
    problem: ProblemConfig,
    *,
    prefix: str,
) -> StageOutcome:
    """Stage 0 for a tree problem: the change applied, it changes only the open
    files, and every Python file it changes still parses. Nothing is imported.

    `tree_path` is `{tree}`, the repo's folder inside the candidate's worktree;
    `error` what materialising it said, when it could not be made. `prefix` is
    that folder relative to the repository (`Repo.prefix`).
    """
    from evolvekit.tree import changed_files, touches_only

    started = time.perf_counter()
    assert problem.tree is not None
    problems: list[str] = []
    if error is not None:
        problems.append(error)
    outside = touches_only(diff, problem.tree.open, prefix=prefix)
    if outside:
        problems.append(
            f"changes files outside the open files: {', '.join(outside)} "
            f"(only {', '.join(problem.tree.open)} may change)"
        )
    if error is None:
        for name in changed_files(diff):
            if not name.endswith(".py") or not name.startswith(prefix):
                continue
            relative = name[len(prefix):]
            path = Path(tree_path) / relative
            if not path.is_file():
                continue  # deleted by the change
            try:
                ast.parse(path.read_bytes(), filename=relative)
            except SyntaxError as exc:
                problems.append(f"SyntaxError in {relative}: {exc.msg} (line {exc.lineno})")
            except ValueError as exc:  # a null byte
                problems.append(f"SyntaxError in {relative}: {exc}")
    return StageOutcome(
        stage_id=stage.id,
        ok=not problems,
        kpis={"static_problems": float(len(problems))},
        failure="; ".join(problems) if problems else None,
        duration_s=time.perf_counter() - started,
    )


# --- builtin-build ---------------------------------------------------------------

_BUILT: tuple[str, str, str, str] | None = None
"""What the shared interpreter holds now, as `(build, interpreter, tree folder, commit)`, or
`None` when nobody knows. In memory and for this process only (decision 31):
nothing on disk claims a build, so a resumed run builds again. One entry, not a
set: a build that installs into one interpreter replaces the one before."""
_BUILT_LOCK = threading.Lock()


def build_command(template: str, *, tree: Path, python: str | None = None) -> list[str]:
    """`problem.tree.build` as argv: split first, then `{tree}` and `{python}`
    substituted (as `build_argv` does, so a Windows path keeps its backslashes
    and a folder with a space stays one argument). `{python}` is `python`
    (`problem.tree.python`: the application's Python for a python harness),
    evolvekit's own interpreter when it is not given. Other braces are the
    command's own."""
    tokens = shlex.split(template, posix=True)
    if not tokens:
        raise ValueError("the build command is empty")
    interpreter = python or resources.python_argv()[0]
    return [token.replace("{tree}", str(tree)).replace("{python}", interpreter) for token in tokens]


def render_app(template: str, tree: Path) -> str:
    """`problem.tree.app` for one candidate: `{tree}` is the candidate's tree
    folder, and the result is what a command stage's `{app}` becomes."""
    return str(Path(template.replace("{tree}", str(tree))))


def on_tree(problem: ProblemConfig, made: "Tree | None") -> dict[str, str | None]:
    """What a command stage on a candidate's tree takes beyond its folder:
    `app` (what `{app}` is, `None` when `problem.tree.app` names nothing) and
    `source_id` (the tree's commit, which keys the cache). Empty for a
    skeleton or parameters problem, or a candidate without a tree; spread
    into `run_command_stage` or a `Job`."""
    if made is None or problem.tree is None:
        return {}
    app = render_app(problem.tree.app, made.path) if problem.tree.app else None
    return {"app": app, "source_id": made.commit}


def _tree_source(source_id: str | None, app: str | None, candidate: Path) -> str | None:
    """What stands for a tree candidate in a cache key: its commit, and the
    program inside the tree relative to the tree's folder -- so the same
    commit in two folders is one entry, and another `problem.tree.app` is not."""
    if source_id is None or app is None:
        return source_id
    try:
        inside = Path(app).relative_to(candidate).as_posix()
    except ValueError:  # a program outside the tree is the same file in every folder
        inside = app
    return f"{source_id}:{inside}"


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


def _forget_build() -> None:
    global _BUILT
    with _BUILT_LOCK:
        _BUILT = None


def run_build_stage(
    tree: "Tree",
    stage: StageConfig,
    problem: ProblemConfig,
    *,
    cancel: "threading.Event | AnyEvent | None" = None,
    time_left_s: float | None = None,
) -> StageOutcome:
    """Run `problem.tree.build` in a candidate's tree (design §5.1, §5.3).

    Once per commit: when the shared interpreter already holds this tree's
    build, nothing runs and the outcome is `cached`. A failure is `build: exit
    code N: <its last stderr line>` (or `build: no answer within T s`) with the
    stderr tail; the cascade makes it a hard reject. The bound is the stage's
    timeout (`problem.tree.build_timeout_s`), or `time_left_s` when the study
    has less time left than that; a build the study's time or `cancel` ends is
    abandoned, not failed, and its whole process tree is killed (`run_bounded`).
    """
    global _BUILT
    assert problem.tree is not None and problem.tree.build
    key = (problem.tree.build, problem.tree.python, os.path.normcase(os.path.abspath(tree.path)), tree.commit)
    with _BUILT_LOCK:
        if _BUILT == key:
            return StageOutcome(stage_id=stage.id, ok=True, cached=True)
        _BUILT = None  # from here on the interpreter holds whatever this build leaves
    limit, short = stage.timeout, False
    if time_left_s is not None and time_left_s < limit:
        limit, short = max(0.0, time_left_s), True
    if cancel is not None and cancel.is_set():
        return StageOutcome(stage_id=stage.id, ok=False, abandoned=True,
                            failure="called off before it started: the run was stopped")
    if limit <= 0:
        return StageOutcome(stage_id=stage.id, ok=False, abandoned=True,
                            failure="the study's time ran out before this built")
    started = time.perf_counter()
    try:
        argv = build_command(problem.tree.build, tree=tree.path, python=problem.tree.python)
    except ValueError as exc:
        return StageOutcome(stage_id=stage.id, ok=False, failure=f"build: bad command: {exc}")
    # Beside the command stages' logs (`<work>/stage_out/`), not in `trees/`.
    logs = tree.worktree.parent.parent / "stage_out"
    stdout_path = logs / f"{tree.worktree.name}.{stage.id}.stdout.log"
    stderr_path = logs / f"{tree.worktree.name}.{stage.id}.stderr.log"
    run = run_bounded(argv, timeout=limit, cwd=tree.path, stdout_path=stdout_path, stderr_path=stderr_path,
                      cancel=cancel)  # type: ignore[arg-type]  # anything with `is_set()`
    stderr, stdout = read_tail(stderr_path, STDERR_LIMIT), read_tail(stdout_path, STDERR_LIMIT)
    fields = dict(stage_id=stage.id, stderr=stderr, stdout=stdout, duration_s=time.perf_counter() - started,
                  argv=tuple(argv), stdout_log=str(stdout_path), stderr_log=str(stderr_path))
    if run.cancelled:
        return StageOutcome(ok=False, abandoned=True, failure="called off: the run was stopped while this built",
                            **fields)
    if run.timed_out and short:
        return StageOutcome(ok=False, abandoned=True, failure="the study's time ran out while this built", **fields)
    if run.error is not None:
        return StageOutcome(ok=False, failure=f"build: could not run {argv[0]!r}: {run.error}", **fields)
    if run.timed_out:
        return StageOutcome(ok=False, failure=f"build: no answer within {limit:g} s", **fields)
    if run.returncode != 0:
        said = _last_line(stderr) or _last_line(stdout)
        return StageOutcome(ok=False, failure=f"build: exit code {run.returncode}" + (f": {said}" if said else ""),
                            **fields)
    with _BUILT_LOCK:
        _BUILT = key
    return StageOutcome(ok=True, **fields)


def restore_build(problem: ProblemConfig, *, log: Callable[[str], None]) -> None:
    """Build the engineer's own checkout (the folder `problem.tree.repo` names)
    back into the shared interpreter (decision 31), so it never points at a
    candidate's tree once that tree is removed.

    Called in a `finally` wherever candidates were built: the search, the final
    check, a preflight. Only a build that names `{python}` installs into the
    shared interpreter (`problem.tree.python`); any other (a program's `make -C {tree}`) builds inside
    each tree, and is never run in the engineer's own folder. A failure is one
    warning through `log`, never raised: it must not hide what the run itself
    ended with.
    """
    tree = problem.tree
    if tree is None or not tree.build or "{python}" not in tree.build:
        return
    _forget_build()
    checkout = Path(tree.repo)
    log(f"building {checkout} back into the application's Python")
    try:
        argv = build_command(tree.build, tree=checkout, python=tree.python)
        with tempfile.TemporaryDirectory(prefix="evolvekit-build-", ignore_cleanup_errors=True) as scratch:
            out, err = Path(scratch) / "build.stdout.log", Path(scratch) / "build.stderr.log"
            run = run_bounded(argv, timeout=tree.build_timeout_s, cwd=checkout, stdout_path=out, stderr_path=err)
            said = _last_line(read_tail(err, STDERR_LIMIT)) or _last_line(read_tail(out, STDERR_LIMIT))
        if run.error is not None:
            reason: str | None = f"could not run {argv[0]!r}: {run.error}"
        elif run.timed_out:
            reason = f"no answer within {tree.build_timeout_s:g} s"
        elif run.returncode != 0:
            reason = f"exit code {run.returncode}" + (f": {said}" if said else "")
        else:
            reason = None
    except Exception as exc:  # noqa: BLE001 - a warning, whatever it was
        reason = str(exc) or type(exc).__name__
    if reason is not None:
        log(f"warning: {checkout} could not be built back into the application's Python (build: {reason}); "
            f"it may still run a removed combination's code. Run the build there yourself: {tree.build}")


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
            [*resources.python_code_argv(snippet), str(candidate_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=no_window_flags(),
        )
    except subprocess.TimeoutExpired:
        return f"import timed out after {timeout:g}s"
    except OSError as exc:  # pragma: no cover - interpreter is always present
        return f"could not start interpreter: {exc}"
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout or "import failed").strip()
    return None


@dataclass(frozen=True)
class UnitContext:
    """What one run of a per-instance stage knows beyond its seed."""

    instance: str
    """As the command receives it in `{instance}`."""
    name: str
    """As people read it: `StageConfig.instance_names`."""
    attempt: int = 0
    cpus: tuple[int, ...] = ()
    cancel: "threading.Event | AnyEvent | None" = None
    """Set when the run is called off: Ctrl+C, or the run's own stop."""

    def labels(self) -> dict[str, Any]:
        return {"instance": self.name, "attempt": self.attempt}


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
            [*resources.python_code_argv(snippet), str(candidate_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=no_window_flags(),
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
    instance: str | None = None,
    app: str | None = None,
) -> list[str]:
    """Split the template first, substitute second.

    Splitting after substitution would let a Windows path's backslashes be eaten
    by POSIX shlex, and would let a filename with a space become two arguments.

    `{python}` is the interpreter evolvekit itself is running on. A bare
    `python` is whatever the operating system finds first -- inside a virtual
    environment on Windows that is the *base* interpreter beside the launcher,
    with another set of installed packages and, silently, another version of
    the solver.

    `{params}` on its own expands to *several* arguments -- one `--flag value`
    pair per declared parameter -- which is the other reason substitution has
    to happen after the split. `{params_json}` is the path of a JSON file with
    the same values, for a solver that would rather read a file. Only as a
    whole token, though: see `embedded_params`, which the config check shares.

    For a tree problem, `{candidate}` is the candidate's tree folder and
    `{app}` the program inside it (`render_app` of `problem.tree.app`); a
    command that names `{app}` when there is none is refused here.
    """
    tokens = shlex.split(command, posix=True)
    if not tokens:
        raise ValueError("stage command is empty")
    embedded = embedded_params(command)
    if embedded is not None:
        raise ValueError(embedded)
    if app is None and any(field == "app" for token in tokens for field in _fields(token)):
        raise ValueError("{app}: the problem names no program in the tree (problem.tree.app)")
    flags = list(params_flags or [])
    mapping = {
        "candidate": str(candidate),
        "inputs": ",".join(inputs),
        "out": str(out),
        "seed": str(seed),
        "python": resources.python_argv()[0],  # one token; frozen the cli exe, which runs the .py after it (D3)
        "instance": instance or "",
        "params_json": str(params_json) if params_json is not None else "",
        "app": app or "",
    }
    argv: list[str] = []
    for token in tokens:
        if token == "{params}":
            argv.extend(flags)
        else:
            argv.append(token.format(**mapping))
    return argv


def _fields(token: str) -> list[str]:
    """The placeholders a token names (`{{` and `}}` are literal braces)."""
    try:
        return [field for _, field, _, _ in string.Formatter().parse(token) if field is not None]
    except ValueError:  # an unbalanced brace: `format` says so with the token itself
        return []


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
    cache: "EvalCache | None" = None,
    cancel: "threading.Event | None" = None,
    app: str | None = None,
    source_id: str | None = None,
    seeds: Sequence[int] | None = None,
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
    `EvaluateConfig.required_kpis`. A run that leaves one out is a failed run,
    for the reason spelled out in `_missing_required`.

    `cancel`, once set, stops the run in flight and starts no other: the
    outcome is then *abandoned*, not failed (`evolvekit/stopping.py`).

    For a tree problem, `app` is what `{app}` becomes and `source_id` the
    tree's commit, which keys the cache instead of the folder.

    `seeds` replaces the stage's own `0 .. seeds-1`, as in
    `fanout.run_instance_stage`: the noise check runs the seed again on seeds
    the stage itself may not run (`evaluate/noise.py`).
    """
    seed_values = list(seeds) if seeds is not None else list(range(max(1, stage.seeds)))
    if not seed_values:
        raise ValueError("run_command_stage: no seed to run")
    if len(seed_values) == 1:
        seed = seed_values[0]
        single = _run_once(
            candidate_path,
            stage,
            inputs=inputs,
            out_path=out_path,
            cwd=cwd,
            private=private,
            seed=seed,
            required_kpis=required_kpis,
            observer=observer,
            configuration=configuration,
            cache=cache,
            cancel=cancel,
            app=app,
            source_id=source_id,
        )
        if single.ok:
            # One run has no variance, but it is a run on its seed (0 in a
            # search): the reference a later candidate's paired difference is
            # taken against.
            single.seed_runs = {k: [v] for k, v in single.kpis.items()}
            single.seed_vectors = {k: [list(v)] for k, v in single.vector_kpis.items()}
            single.seed_list = (seed,)
        return single
    outcomes: list[StageOutcome] = []
    for seed in seed_values:
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
            cache=cache,
            cancel=cancel,
            app=app,
            source_id=source_id,
        )
        outcomes.append(outcome)
        if not outcome.ok:
            failed = _combine_failure(outcomes)
            return failed
    return _combine(outcomes, seed_values)


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


def _cv(values: list[float]) -> float | None:
    """Coefficient of variation: spread relative to the mean.

    Zero when every run agreed. When the mean is zero and the runs did not
    agree there is no ratio to take, so the answer is `None`: an absolute spread
    would silently switch units next to the ratios of the other KPIs.
    """
    if len(values) < 2:
        return 0.0
    spread = stdev(values)
    if spread == 0.0:
        return 0.0
    mean_value = fmean(values)
    return spread / abs(mean_value) if mean_value != 0.0 else None


def _combine(outcomes: list[StageOutcome], seeds: Sequence[int] | None = None) -> StageOutcome:
    """Average N successful runs of one stage into a single outcome, or fail
    when they disagree about the length of a vector KPI. `seeds` are the
    seeds the runs were made on, in order (default `0 .. N-1`)."""
    first = outcomes[0]
    keys = set(first.kpis)
    for outcome in outcomes[1:]:
        keys &= set(outcome.kpis)
    kpis = {k: fmean([o.kpis[k] for o in outcomes]) for k in sorted(keys)}
    kpi_cv = {k: _cv([o.kpis[k] for o in outcomes]) for k in sorted(keys)}

    vectors: dict[str, list[float]] = {}
    # Every name any seed reported: one only a later seed reports is ragged too.
    for name in sorted(set().union(*(o.vector_kpis for o in outcomes))):
        j = next(k for k, o in enumerate(outcomes) if name in o.vector_kpis)
        expected = len(outcomes[j].vector_kpis[name])
        for k, other in enumerate(outcomes):
            got = other.vector_kpis.get(name)
            if got is None:
                problem = f"seed {k} reported no `{name}`, seed {j} reported {expected}"
            elif len(got) != expected:
                problem = f"seed {k} reported {len(got)} values of `{name}`, seed {j} reported {expected}"
            else:
                continue
            # Runs that disagree about how many instances there were cannot be
            # averaged element by element, and the first run's vector alone
            # would pass off one run as the mean of all.
            return StageOutcome(
                stage_id=first.stage_id,
                ok=False,
                failure=problem,
                stderr=first.stderr,
                stdout=first.stdout,
                duration_s=sum(o.duration_s for o in outcomes),
                runs=len(outcomes),
                private=first.private,
            )
        vectors[name] = [fmean(column) for column in zip(*(o.vector_kpis[name] for o in outcomes))]

    # The noise of one run (`evaluate/noise.py` pools it): there are always two
    # or more runs here, `seeds: 1` never combines.
    kpi_var = {k: variance([o.kpis[k] for o in outcomes]) for k in sorted(keys)}
    vector_var = {
        name: [variance(column) for column in zip(*(o.vector_kpis[name] for o in outcomes))]
        for name in vectors
    }
    return StageOutcome(
        stage_id=first.stage_id,
        ok=True,
        kpis=kpis,
        vector_kpis=vectors,
        kpi_cv=kpi_cv,
        kpi_var=kpi_var,
        vector_var=vector_var,
        seed_runs={k: [o.kpis[k] for o in outcomes] for k in sorted(keys)},
        seed_vectors={name: [list(o.vector_kpis[name]) for o in outcomes] for name in vectors},
        seed_list=tuple(seeds) if seeds is not None else tuple(range(len(outcomes))),
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
    unit: "UnitContext | None" = None,
    cache: "EvalCache | None" = None,
    cancel: "threading.Event | AnyEvent | None" = None,
    app: str | None = None,
    source_id: str | None = None,
) -> StageOutcome:
    """One evaluator run, announced to `observer` before and after.

    The two events bracket the subprocess itself, so a reader of the event log
    can tell at any moment which run is in flight and for how long it has been.
    `unit` is what a per-instance stage adds: which instance this run is for,
    which attempt it is, where it is pinned and how it can be called off.
    A run that is called off before it starts is not started, and says so
    with an abandoned outcome and no events.
    """
    halt = cancel if cancel is not None else (unit.cancel if unit is not None else None)
    if halt is not None and halt.is_set():
        return StageOutcome(
            stage_id=stage.id,
            ok=False,
            failure="called off before it started: the run was stopped",
            abandoned=True,
            private=private,
        )
    labels = unit.labels() if unit is not None else {}
    if observer is not None:
        observer("eval_started", seed=seed, timeout_s=stage.timeout, **labels)
    key = known = None
    if cache is not None:
        key = cache.key(
            stage_id=stage.id,
            command=stage.command,
            reading=(stage.kpis_from, stage.kpi_patterns),
            flags=configuration.flags if configuration is not None else (),
            candidate_path=candidate_path,
            inputs=inputs,
            instance=unit.instance if unit is not None else None,
            seed=seed,
            private=private,
            cwd=cwd,
            source_id=_tree_source(source_id, app, candidate_path),
        )
        known = cache.load(key, stage.id, private, timeout=stage.timeout)
        if known is not None and _missing_required(known.kpis, required_kpis) is not None:
            known = None  # kept under another objective: not an answer to this question
    if known is not None:
        outcome = known
    else:
        load = HostLoad().start()
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
            unit=unit,
            cancel=halt,
            app=app,
        )
        outcome.host_busy = load.stop()
    if cache is not None and key is not None and not outcome.cached:
        # Before anything else can go wrong: a result that was paid for is kept.
        cache.store(key, outcome)
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
                instance=unit.instance if unit is not None else None,
                app=app,
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
                # Stopped with the run, not broken: `status` counts it apart.
                **({"abandoned": True} if outcome.abandoned else {}),
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
            cached=outcome.cached,
            host_busy=outcome.host_busy,
            **labels,
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
    unit: "UnitContext | None" = None,
    cancel: "threading.Event | AnyEvent | None" = None,
    app: str | None = None,
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
            instance=unit.instance if unit is not None else None,
            app=app,
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
        cpus=unit.cpus if unit is not None else (),
        cancel=cancel,
    )
    duration = time.perf_counter() - started
    if run.cancelled:
        return StageOutcome(
            stage_id=stage.id,
            ok=False,
            failure="called off: the run was stopped while this ran",
            abandoned=True,
            duration_s=duration,
            private=private,
        )
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

    kpis, vectors, feedback, problem = _collect_kpis(stage, out_path, stdout_log)
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
        f"evaluator reported no {wanted}, which the score or a gate is computed from "
        f"(evaluate.score, evaluate.gates); it reported: {', '.join(sorted(kpis))}"
    )


Kpis = tuple[dict[str, float], dict[str, list[float]], str, "str | None"]
"""Scalars, vectors, the evaluator's note, and what was wrong (or `None`)."""


def _collect_kpis(stage: StageConfig, out_path: Path, stdout_log: Path) -> Kpis:
    """The run's KPIs from wherever the stage says the command reports them:
    the `{out}` file, the last JSON object on stdout, patterns over its text."""
    kpis: dict[str, float] = {}
    vectors: dict[str, list[float]] = {}
    note = ""
    if stage.kpis_from == "stdout":
        payload = _last_json_object(stdout_log)
        if payload is None:
            return {}, {}, "", "the program printed no JSON object on stdout"
        kpis, vectors, note, problem = _parse_kpis(payload)
        if problem is not None:
            return {}, {}, "", problem
    elif "{out}" in stage.command:
        kpis, vectors, note, problem = _read_kpis(out_path)
        if problem is not None:
            return {}, {}, "", problem
    if stage.kpi_patterns:
        # The patterns see the last `TAIL_BYTES` of the log, not all of it: a
        # regular expression over fifty megabytes of progress lines costs what
        # it costs, and a text result is a short line at the end.
        printed = read_tail(stdout_log, TAIL_BYTES)
        for name, pattern in stage.kpi_patterns:
            # MULTILINE: `^` and `$` are the start and end of a *line*. What a
            # program prints is lines, and `^Cost: (\d+)` is how anybody writes
            # "the line that reports the cost"; without the flag `^` meant the
            # start of the whole output and such a pattern never matched.
            found = re.findall(pattern, printed, re.MULTILINE)
            if not found:
                return {}, {}, "", (
                    f"`kpi_patterns.{name}` matched nothing in what the program printed"
                )
            try:
                value = float(found[-1])  # a solver logs its progress before its result
            except ValueError:
                return {}, {}, "", f"`kpi_patterns.{name}` captured {found[-1]!r}, which is not a number"
            if not _is_finite(value):
                return {}, {}, "", _non_finite(name, value)
            kpis[name] = value
    return kpis, vectors, note, None


def _last_json_object(stdout_log: Path) -> dict[str, Any] | None:
    """The last line of the log that is a JSON object, from anywhere in it.

    Not from the log's tail: a solver that prints its solution along with its
    cost prints one line of a few hundred kilobytes, and the last 64 KB of that
    is the second half of a line -- the run was reported as having printed no
    JSON object at all. So the whole log is read, a line at a time: memory is
    bounded by the longest line, not by the log, and only a line that starts
    with `{` and ends with `}` is handed to the JSON parser.
    """
    found: dict[str, Any] | None = None
    try:
        with open(stdout_log, "rb") as handle:
            for raw in handle:
                line = raw.strip()
                if not (line.startswith(b"{") and line.endswith(b"}")):
                    continue
                try:
                    payload = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    found = payload
    except OSError:
        return None
    return found


def _read_kpis(out_path: Path) -> Kpis:
    """The JSON object the command wrote to `{out}`."""
    if not out_path.is_file():
        return {}, {}, "", f"evaluator wrote no output file at {out_path.name}"
    try:
        payload: Any = json.loads(out_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {}, {}, "", f"evaluator output was not readable JSON: {exc}"
    if not isinstance(payload, dict):
        return {}, {}, "", "evaluator output must be a JSON object"
    return _parse_kpis(payload)


def _parse_kpis(payload: dict[str, Any]) -> Kpis:
    """Split the evaluator's output into scalars, vectors and prose.

    Scalars are the contract every consumer already relies on -- the score, the
    penalties, the archive descriptors. A list value is accepted too and kept
    aside: only the behaviour signature reads it, so an evaluator that emits
    nothing but scalars behaves exactly as it did before. `text_feedback` sits
    beside `kpis` rather than inside it, is optional, and is truncated here.

    Two shapes. Under an explicit `"kpis"` key every value has to be a KPI --
    whoever wrote that key knows the contract, and a string there is a mistake
    worth hearing about. Without the key the object is the program's *own*
    result (a solver's `{"cost": ..., "instance": "...", "params": {...}}`):
    its numbers and booleans are the KPIs and the rest is metadata, left alone.
    """

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
    foreign = raw is None
    if foreign:
        # The flat shape: the whole document is the KPI mapping. `text_feedback`
        # is the framework's key, not a KPI, so it never counts as one.
        raw = {k: v for k, v in payload.items() if k != "text_feedback"}
    if not isinstance(raw, dict) or not raw:
        return {}, {}, "", "evaluator output contained no 'kpis' mapping"
    kpis: dict[str, float] = {}
    vectors: dict[str, list[float]] = {}
    for key, value in raw.items():
        if isinstance(value, bool):  # `"feasible": true` is a KPI worth having
            kpis[str(key)] = 1.0 if value else 0.0
            continue
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
        if foreign:
            continue  # a name, a nested object, a list with a hole in it: not a KPI
        return (
            {},
            {},
            "",
            f"KPI {key!r} was {value!r}; KPIs must be numbers or lists of numbers",
        )
    if not kpis:
        return {}, {}, "", "evaluator output contained no scalar KPI"
    return kpis, vectors, note, None
