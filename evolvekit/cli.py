"""`python -m evolvekit init | preflight | run | status | dashboard | leaderboard`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from evolvekit import __version__
from evolvekit.config import ConfigError, load_config
from evolvekit.economics import DEFAULT_WINDOW, format_series, series
from evolvekit.env import LoadedEnv, load_env_files
from evolvekit.leaderboard import (
    fitness_of,
    novelty_counts,
    rank,
    render_html,
    render_markdown,
    unfinished_count,
)
from evolvekit.ledger import Ledger
from evolvekit.lock import RunLockError
from evolvekit.preflight import run_preflight
from evolvekit.scaffold import TUNE_FILES, TUNE_NEXT
from evolvekit.search.driver import Driver
from evolvekit.status import build_status, render_text

__all__ = ["main", "build_parser"]

DEFAULT_CONFIG = "evolvekit.yaml"
DEFAULT_RUN_DIR = "runs/latest"

EXIT_ABORTED = 4
"""`run`: 0 done, 1 an error, 2 a config error, 3 the run directory is locked,
4 aborted -- the seed failed its own evaluation or the model backend kept failing."""

_STARTER_CONFIG = """\
# evolvekit configuration. See README.md and docs/new-experiment.md.

problem:
  skeleton: skeleton.py
  language: python
  block_start: "# EVOLVE-BLOCK-START"
  block_end: "# EVOLVE-BLOCK-END"
  required_functions: [priority]
  description: |
    Describe the problem here. This text is the stable, cacheable half of every
    prompt, so keep it factual and keep it still.

  # The three structured fields below become their own headed sections in the
  # system prompt. They exist because a sentence buried in `description` is a
  # sentence the model skims: a whole real run produced fifteen hand-placed
  # answers against a skeleton that had offered it a local-search helper all
  # along. Name the tools where they cannot be missed.
  tools:
    - Helpers in the fixed part your block may call, one per line, with signatures.
    - Time-budget constants, and what the block is expected to spend.
    - Which imports are allowed.
  constraints:
    - What the block must never do (I/O, unbounded runtime, extra dependencies).
    - The contract it must satisfy (return shape, determinism).
  # One or two sentences: what kind of change actually alters the decisions
  # your evaluator measures. The framework can say "you re-expressed the same
  # rule"; only you can say what a genuinely different rule looks like here.
  what_counts_as_new: >
    Say what a real change is for this problem, and name the cheap
    re-expressions that are not.

evaluate:
  failure_score: -1000.0
  stages:
    - id: static
      kind: builtin-static
      timeout: 30
    - id: proxy
      kind: command
      command: "{python} evaluate.py --candidate {candidate} --inputs {inputs} --out {out} --seed {seed}"
      inputs: [proxy]
      timeout: 120           # per run, not per stage
      seeds: 1               # >1 needs {seed} in the command; KPIs are averaged
      promote:
        top_k_per_generation: 2
    - id: full
      kind: command
      command: "{python} evaluate.py --candidate {candidate} --inputs {inputs} --out {out} --seed {seed}"
      inputs: [full]
      private_inputs: [holdout]
      timeout: 600
      seeds: 1
  score:
    objective: excess_pct
    direction: minimize
    weights:
      excess_pct: 1.0
  penalties:
    - kpi: priority_errors
      weight: 2.0
      scale: log1p
  # Ranking = public - holdout_penalty * max(0, public - private). Raising this
  # makes the search more suspicious of gains that do not survive the hold-out.
  holdout_penalty: 1.0
  # Behavioural duplicates: after every command stage the candidate's KPIs are
  # rounded and hashed, and a candidate whose fingerprint matches one already
  # seen at that stage is stopped there. Defaults shown.
  signature_digits: 9
  # Used instead on a `seeds: N > 1` stage: a stochastic evaluator moves the
  # eighth digit for free, so at nine digits nothing ever looks like a twin.
  signature_digits_stochastic: 3
  signature_ignore: [runtime_s, complexity, static_problems]

models:
  small:
    provider: fake        # azure | openrouter | claude-cli | fake
    model: fake-small
    price_in_per_mtok: 0.0
    price_out_per_mtok: 0.0
    options:
      responses_path: fake_responses.yaml
  strong:
    provider: fake
    model: fake-strong
    price_in_per_mtok: 0.0
    price_out_per_mtok: 0.0
    options:
      responses_path: fake_responses.yaml

budget:
  max_usd: 1.0
  max_tokens: 500000
  max_full_evals_per_day: 20

stop:
  patience: 4
  epsilon: 0.001
  target: null
  # Economics stops, both off by default. Either one only fires after at least
  # one big step has been spent since the last improvement.
  # max_usd_since_improvement: 0.25    # patience, denominated in money
  # min_gain_per_usd:                  # score units per USD over a window
  #   window: 3
  #   threshold: 0.5

search:
  children_per_generation: 3
  generations: 6
  big_step_every: 3
  parent_top_k: 5
  seed: 0
  operators:
    diff: 0.5
    rewrite: 0.3
    crossover: 0.2
    # param_lhs: 0.2   # costs no tokens; needs a `# PARAMS: {...}` line in the block
  inspirations: 2       # 1-2 elites from other cells, shown as delta summaries
  scratchpad_every: 5   # meta-scratchpad refresh, in generations; 0 turns it off
  novelty_retry: true   # re-prompt once when the model repeats itself
  # Let the run decide how wide to search: two flat generations add a child,
  # an improving one gives it back. Absent by default.
  # adaptive_children: {min: 3, max: 6, grow_after: 2, shrink_after: 1}
  novelty:
    # The near-duplicate gate, between the exact structure hash and the
    # evaluator. `local` is free: cosine over canonical-AST node-type n-grams,
    # so a block that only moves constants scores 1.0. `embedding` needs a
    # `models.embed` slot; `off` is the Phase B behaviour. A near-duplicate is
    # re-prompted once and then evaluated anyway, flagged, never rejected.
    near:
      method: local     # local | embedding | off
      threshold: 0.97
      # model: text-embedding-3-small   # defaults to models.embed.model
  archive:
    top_k: 20
    descriptors:
      # One elite per cell. Any KPI works; `range: auto` widens as the run goes.
      - kpi: complexity
        bins: 4
        range: auto
"""

_ENV_EXAMPLE = """\
# Copy to .env and fill in. .env is gitignored; never commit real keys.

# --- azure ---
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_API_VERSION=2024-10-21

# --- openrouter ---
OPENROUTER_API_KEY=
# OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# --- claude-cli ---
# No key: the `claude` CLI carries its own subscription auth.
# Set this only if the binary is not on PATH.
# CLAUDE_CLI_PATH=
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evolvekit",
        description="LLM-driven evolutionary program search with a hard cost budget.",
    )
    parser.add_argument("--version", action="version", version=f"evolvekit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="scaffold evolvekit.yaml and .env.example")
    p_init.add_argument("directory", nargs="?", default=".", help="target directory")
    p_init.add_argument("--force", action="store_true", help="overwrite existing files")
    p_init.add_argument(
        "--template", choices=("program", "tune"), default="program",
        help="program (default): a config for evolving a block of code, to be pointed at your "
        "skeleton and evaluator. tune: a complete, runnable setup for tuning the parameters of "
        "a command-line program -- a stand-in solver, three instances, no model needed",
    )

    p_pre = sub.add_parser(
        "preflight",
        help="dry-run the seed through every stage and sanity-check the settings",
    )
    p_pre.add_argument("--config", default=DEFAULT_CONFIG)
    p_pre.add_argument(
        "--provider-check",
        action="store_true",
        help="also make one minimal call per configured model role. This is the "
        "only part of preflight that spends money.",
    )
    p_pre.add_argument(
        "--candidate",
        metavar="PATH",
        help="dry-run this candidate's full source (as saved under a run's "
        "candidates/ directory) instead of the seed -- timeout and budget "
        "advice grounded in a realistic candidate rather than a seed that may "
        "barely exercise the evaluator.",
    )

    p_run = sub.add_parser("run", help="run the evolutionary loop")
    p_run.add_argument("--config", default=DEFAULT_CONFIG)
    p_run.add_argument("--run-dir", default=DEFAULT_RUN_DIR)
    p_run.add_argument(
        "--generations", type=int, default=None,
        help="run this many *more* generations. Without it the run directory is taken to "
        "`search.generations` in total: a resumed run finishes its plan",
    )
    p_run.add_argument("--quiet", action="store_true")
    p_run.add_argument(
        "--allow-changed-problem", action="store_true",
        help="continue a run directory although the skeleton, the parameters, the objective or a "
        "stage's command or inputs differ from what it was started with (refused otherwise: its "
        "scores were measured under the old definition)",
    )
    p_run.add_argument(
        "--dashboard",
        action="store_true",
        help="serve the live dashboard for this run on localhost while it runs "
        "(afterwards: `evolvekit dashboard --run-dir ...`)",
    )
    p_run.add_argument(
        "--port",
        type=int,
        default=None,
        help="first port to try for --dashboard (default 8765; the next free one is used)",
    )

    p_status = sub.add_parser(
        "status",
        help="how a run is doing: alive or not, progress, best, failures, spend",
    )
    p_status.add_argument(
        "--run-dir", default=DEFAULT_RUN_DIR, help=f"default: {DEFAULT_RUN_DIR}"
    )
    p_status.add_argument(
        "--json",
        action="store_true",
        help="print the whole status document as JSON: the same document the "
        "text view and the dashboard are rendered from",
    )

    p_dash = sub.add_parser(
        "dashboard",
        help="the live dashboard for a run directory: running, finished or dead",
    )
    p_dash.add_argument(
        "--run-dir", default=DEFAULT_RUN_DIR, help=f"default: {DEFAULT_RUN_DIR}"
    )
    p_dash.add_argument("--host", default="127.0.0.1", help="default: 127.0.0.1 (this machine only)")
    p_dash.add_argument(
        "--port", type=int, default=None, help="first port to try (default 8765)"
    )
    p_dash.add_argument(
        "--no-browser", action="store_true", help="print the URL; do not open a browser"
    )
    p_dash.add_argument(
        "--export",
        metavar="FILE",
        help="write the dashboard as one self-contained HTML file and exit: "
        "opens from disk, works offline, can be attached to a ticket",
    )

    p_confirm = sub.add_parser(
        "confirm",
        help="is the improvement real? the run's best against its baseline, paired, "
        "on seeds (and instances) the search never saw",
    )
    p_confirm.add_argument("--config", default=DEFAULT_CONFIG)
    p_confirm.add_argument("--run-dir", default=DEFAULT_RUN_DIR, help=f"default: {DEFAULT_RUN_DIR}")
    p_confirm.add_argument(
        "--seeds", required=True,
        help="comma-separated seeds the search never used, e.g. 1001,1002,1003",
    )
    p_confirm.add_argument(
        "--candidates", default="best",
        help="`best` (default), `top:N`, or comma-separated candidate ids; "
        "`ID@OTHER_RUN_DIR` takes a candidate of another run of the same problem",
    )
    p_confirm.add_argument(
        "--against", metavar="ID", default=None,
        help="compare with this candidate instead of the run's seed (`ID` or `ID@OTHER_RUN_DIR`) "
        "-- e.g. the winner of one search against the winner of another",
    )
    p_confirm.add_argument(
        "--instances", action="append", metavar="ENTRY",
        help="compare on these instead of the final stage's own instances "
        "(a file, a pattern or a name; repeatable) -- e.g. instances held back from the search",
    )
    p_confirm.add_argument(
        "--label", default="confirm",
        help="the comparison lands in <run-dir>/confirm/<label>/ (default: confirm)",
    )

    p_export = sub.add_parser(
        "export",
        help="the winning configuration in a form another program can use",
    )
    p_export.add_argument("--run-dir", default=DEFAULT_RUN_DIR, help=f"default: {DEFAULT_RUN_DIR}")
    p_export.add_argument("--candidate", default="best", help="a candidate id (default: the run's best)")
    p_export.add_argument(
        "--format", choices=("json", "yaml", "flags", "code"), default="json",
        help="json / yaml: the parameter values; flags: `--name value` as a stage command "
        "receives them; code: the candidate's block",
    )
    p_export.add_argument("--config", default=None, help="needed for --format flags (per-parameter flag names)")
    p_export.add_argument("--out", metavar="FILE", help="write here instead of stdout")

    p_board = sub.add_parser("leaderboard", help="render the leaderboard")
    p_board.add_argument("--run-dir", default=DEFAULT_RUN_DIR)
    p_board.add_argument("--limit", type=int, default=20)
    p_board.add_argument("--html", metavar="PATH", help="also write an HTML dashboard")
    return parser


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.directory).resolve()
    target.mkdir(parents=True, exist_ok=True)
    tune = args.template == "tune"
    existing = target / DEFAULT_CONFIG
    if tune and existing.exists() and not args.force:
        # The scaffold is one piece: its solver and instances beside someone
        # else's config would be neither, and "runs as it is" would point at a
        # config that is not the scaffold's.
        print(
            f"error: {target} already holds {DEFAULT_CONFIG}; nothing written. Use another "
            "directory, or --force to replace it (and solver.py, instances/) with the scaffold",
            file=sys.stderr,
        )
        return 1
    files = (
        tuple(TUNE_FILES.items())
        if tune
        else ((DEFAULT_CONFIG, _STARTER_CONFIG), (".env.example", _ENV_EXAMPLE))
    )
    written = []
    for name, content in files:
        path = target / name
        if path.exists() and not args.force:
            print(f"exists, not overwritten: {path}  (use --force)")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(path)
    for path in written:
        print(f"wrote {path}")
    if written and tune:
        shown = Path(args.directory) / DEFAULT_CONFIG
        print(TUNE_NEXT.format(config=shown.as_posix(), run_dir=(Path(args.directory) / "runs" / "first").as_posix()))
    elif written:
        print("\nNext: point problem.skeleton at your skeleton file, then")
        print("  python -m evolvekit run --config evolvekit.yaml")
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    """0 clean, 1 warnings, 2 failures -- so a wrapper script can gate on it."""
    for loaded in _ENV_LOADED:  # where a key came from is the first question when one is wrong
        if loaded.names:
            print(f"environment : {loaded.path} set {', '.join(loaded.names)}")
    return run_preflight(
        load_config(args.config),
        provider_check=args.provider_check,
        candidate=args.candidate,
    )


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    log = (lambda _m: None) if args.quiet else print
    driver = Driver(config, run_dir=args.run_dir, log=log, allow_changed_problem=args.allow_changed_problem)
    server = None
    if args.dashboard:
        from evolvekit.dashboard import DEFAULT_PORT, DashboardServer

        server = DashboardServer(driver.ledger.run_dir, port=args.port or DEFAULT_PORT)
        print(f"dashboard   : {server.start()}   (agents: {server.url}api/status)")
    try:
        summary = driver.run(args.generations)
    finally:
        if server is not None:
            server.stop()

    print()
    print(
        render_markdown(
            driver.ledger.runs(),
            limit=10,
            usage=driver.ledger.usage(),
            window=config.stop.economics_window,
        )
    )
    print()
    print(format_series(summary.economics))
    print()
    print(f"stop reason : {summary.stop_reason}")
    print(f"seed score  : {_fmt(summary.seed_score)}")
    print(f"best score  : {_fmt(summary.best.score if summary.best else None)}")
    print(f"best rank   : {_fmt(summary.best.fitness if summary.best else None)}")
    print(f"improvement : {_fmt(summary.improvement)}")
    print(f"archive     : {summary.occupancy}")
    print(f"rejected    : {summary.rejection_breakdown}")
    print(f"near-dupes  : {summary.near_breakdown}")
    print(f"unfinished  : {summary.unfinished} (evaluated, never ranked)")
    totals = summary.totals
    print(
        f"spend       : ${totals.get('usd', 0.0):.4f} over "
        f"{int(totals.get('calls', 0))} call(s), "
        f"{int(totals.get('total_tokens', 0))} token(s)"
    )
    print(f"run dir     : {driver.ledger.run_dir}")
    if args.dashboard:
        print(
            "dashboard   : stopped with the run. To look again:\n"
            f"              python -m evolvekit dashboard --run-dir {driver.ledger.run_dir}"
        )
    # 4: the run could not work (the seed failed, the backend died). A wrapper
    # script must not mistake that for a finished search.
    return EXIT_ABORTED if summary.aborted else 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Serve (or export) the dashboard for a run directory. Reads only."""
    from evolvekit.dashboard import (
        DEFAULT_PORT,
        DashboardServer,
        export_html,
        open_in_browser,
    )

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"error: there is no run directory at {run_dir.resolve()}", file=sys.stderr)
        return 1
    if args.export:
        print(f"wrote {export_html(run_dir, args.export).resolve()}")
        return 0
    server = DashboardServer(run_dir, host=args.host, port=args.port or DEFAULT_PORT)
    print(f"dashboard   : {server.url}")
    print(f"for agents  : {server.url}api/status   (the same document as `status --json`)")
    print("Ctrl+C to stop. The run, if there is one, is not affected.")
    if not args.no_browser:
        open_in_browser(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.stop()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Exit 0 when there is a run to report on, 1 when there is none.

    Reads only. It used to build a `Ledger`, which creates the directory it is
    given -- so `status` on a mistyped path made the path exist and reported an
    empty run with exit code 0.
    """
    document = build_status(args.run_dir)
    state = (document.get("health") or {}).get("state")
    if args.json:
        print(json.dumps(document, indent=2, allow_nan=False))
        return 1 if state == "missing" else 0
    print(render_text(document))
    if state in ("missing", "empty"):
        return 1 if state == "missing" else 0

    ledger = Ledger(args.run_dir)
    rows = ledger.runs()
    if not rows:
        return 0
    print()
    totals = ledger.totals()
    ranked = rank(rows, 1)
    best = ranked[0] if ranked else None
    counts = novelty_counts(rows)
    archive = ledger.read_archive()
    print(f"candidates   : {len(rows)} ({counts['rejected']} rejected)")
    print(
        f"novelty      : {counts['no_op']} no-op, {counts['duplicate']} duplicate "
        "(never evaluated)"
    )
    print(
        f"behavioural  : {counts['behavioural']} twin(s) "
        "(evaluated once, never promoted)"
    )
    print(
        f"near-dupes   : {counts['near']} flagged by the similarity gate "
        "(evaluated anyway)"
    )
    print(
        f"unfinished   : {unfinished_count(rows)} did not finish the final stage "
        "(failed, not promoted or skipped; never ranked)"
    )
    print(f"generations  : {max(int(r.get('generation', 0)) for r in rows)}")
    print(
        f"archive      : {archive.get('occupancy', 'no archive.json snapshot yet')}"
    )
    print(
        f"best         : {best['id']} rank={fitness_of(best):.6g} "
        f"score={float(best['score']):.6g} cell={best.get('cell')}"
        if best
        else "best         : none"
    )
    print(f"llm calls    : {int(totals['calls'])}")
    print(f"tokens       : {int(totals['input_tokens'])} in / {int(totals['output_tokens'])} out")
    print(f"spend        : ${totals['usd']:.4f}")
    window = archive.get("economics_window", DEFAULT_WINDOW)
    points = series(rows, ledger.usage(), window=window)
    if points:
        print()
        print(format_series(points))
    return 0


def cmd_leaderboard(args: argparse.Namespace) -> int:
    ledger = Ledger(args.run_dir)
    rows = ledger.runs()
    usage = ledger.usage()
    archive = ledger.read_archive()
    print(render_markdown(rows, limit=args.limit, usage=usage))
    if archive.get("occupancy"):
        print(f"\narchive: {archive['occupancy']}")
    if args.html:
        path = Path(args.html)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render_html(rows, archive=archive), encoding="utf-8", newline="\n"
        )
        print(f"\nwrote {path.resolve()}")
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    """Exit 0 when every candidate's 95 % interval lies above zero, 1 otherwise
    -- so a script can gate on "the improvement is real"."""
    from evolvekit.confirm import confirm, render_markdown

    try:
        seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    except ValueError:
        raise ValueError(f"--seeds: expected comma-separated integers, got {args.seeds!r}") from None
    if not Path(args.run_dir).is_dir():
        raise ValueError(f"there is no run directory at {args.run_dir}")
    comparison = confirm(
        load_config(args.config), args.run_dir, seeds=seeds, candidates=args.candidates,
        instances=args.instances, label=args.label, against=args.against,
    )
    print(render_markdown(comparison))
    print(f"written to {Path(args.run_dir) / 'confirm' / args.label}")
    confirmed = all(
        (result["summary"].get("ci95") or [0.0])[0] > 0 for result in comparison.per_candidate.values()
    )
    return 0 if confirmed else 1


def cmd_export(args: argparse.Namespace) -> int:
    from evolvekit.leaderboard import rank
    from evolvekit.ledger import read_jsonl

    if not Path(args.run_dir).is_dir():
        raise ValueError(f"there is no run directory at {args.run_dir}")
    rows = list(read_jsonl(Path(args.run_dir) / "runs.jsonl"))
    if args.candidate == "best":
        ranked = rank(rows, 1)
        if not ranked:
            raise ValueError("the run has no fully evaluated candidate yet")
        row = ranked[0]
    else:
        row = next((r for r in rows if r.get("id") == args.candidate), None)
        if row is None:
            raise ValueError(f"the run directory holds no candidate {args.candidate!r}")
    params = row.get("params")
    if args.format == "code":
        text = str(row.get("block") or "")
    elif not isinstance(params, dict):
        raise ValueError(
            f"{row.get('id')} has no parameter values: the run declares no `problem.parameters`. "
            "Use --format code for its block"
        )
    elif args.format == "json":
        text = json.dumps(params, indent=2) + "\n"
    elif args.format == "yaml":
        import yaml

        text = yaml.safe_dump(params, sort_keys=False)
    else:
        if not args.config:
            raise ValueError("--format flags needs --config: a parameter may declare its own flag")
        space = load_config(args.config).problem.parameters
        if space is None:
            raise ValueError(f"{args.config} declares no `problem.parameters`")
        resolved, problems = space.validate(params)
        if problems:
            raise ValueError(f"{row.get('id')} does not fit {args.config}: {problems[0]}")
        text = " ".join(space.render_flags(resolved)) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"{row.get('id')} -> {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


_COMMANDS = {
    "confirm": cmd_confirm,
    "export": cmd_export,
    "init": cmd_init,
    "preflight": cmd_preflight,
    "run": cmd_run,
    "status": cmd_status,
    "dashboard": cmd_dashboard,
    "leaderboard": cmd_leaderboard,
}


_ENV_LOADED: list[LoadedEnv] = []
"""Which `.env` files set which variables in this process -- names, never values."""


def _load_env(args: argparse.Namespace) -> None:
    """The nearest `.env` above the config file and above the working directory,
    inside the project (`evolvekit/env.py`). `EVOLVEKIT_NO_DOTENV=1` switches it
    off: a test suite must not read a developer's real keys.

    A refused variable is reported by every command, on stderr so `--json`
    output stays clean. `run` also says what it loaded: every evaluator it
    starts inherits it, and a run is where a surprise costs most."""
    if os.environ.get("EVOLVEKIT_NO_DOTENV"):
        return
    config = getattr(args, "config", None)
    starts = ([Path(config).resolve().parent] if config else []) + [Path.cwd()]
    _ENV_LOADED[:] = load_env_files(starts)
    for loaded in _ENV_LOADED:
        if loaded.names and args.command == "run":
            print(f"environment : {loaded.path} set {', '.join(loaded.names)}", file=sys.stderr)
        if loaded.refused:
            print(
                f"environment : {loaded.path} refused {', '.join(loaded.refused)} -- they decide "
                "which program runs or what it loads, and every evaluator would inherit them; "
                "set them in the shell if you mean it",
                file=sys.stderr,
            )


def _tolerant_output() -> None:
    """Never let a character the console cannot encode decide the exit code.

    On Windows a piped or redirected stdout is encoded as cp1252. A failed
    evaluator's last words -- an arrow, a byte that is no UTF-8 -- then raised
    `UnicodeEncodeError` while being printed, a `ValueError` that `main` turns
    into exit 1: `preflight` reported "warnings" for a broken harness, and a
    wrapper that stops only on 2 went ahead. Unencodable characters are
    written as escapes instead."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (ValueError, OSError):  # a stream that cannot be reconfigured is left alone
            pass


def main(argv: list[str] | None = None) -> int:
    _tolerant_output()
    args = build_parser().parse_args(argv)
    _load_env(args)
    try:
        return _COMMANDS[args.command](args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except RunLockError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.6g}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
