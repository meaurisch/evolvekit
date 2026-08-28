"""`python -m evolvekit init | preflight | run | status | leaderboard`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evolvekit import __version__
from evolvekit.config import ConfigError, load_config
from evolvekit.economics import DEFAULT_WINDOW, format_series, series
from evolvekit.leaderboard import (
    fitness_of,
    novelty_counts,
    rank,
    render_html,
    render_markdown,
)
from evolvekit.ledger import Ledger
from evolvekit.lock import RunLockError
from evolvekit.preflight import run_preflight
from evolvekit.search.driver import Driver

__all__ = ["main", "build_parser"]

DEFAULT_CONFIG = "evolvekit.yaml"
DEFAULT_RUN_DIR = "runs/latest"

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
      command: "python evaluate.py --candidate {candidate} --inputs {inputs} --out {out} --seed {seed}"
      inputs: [proxy]
      timeout: 120           # per run, not per stage
      seeds: 1               # >1 needs {seed} in the command; KPIs are averaged
      promote:
        top_k_per_generation: 2
    - id: full
      kind: command
      command: "python evaluate.py --candidate {candidate} --inputs {inputs} --out {out} --seed {seed}"
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
    p_run.add_argument("--generations", type=int, default=None)
    p_run.add_argument("--quiet", action="store_true")

    p_status = sub.add_parser("status", help="spend and progress for a run directory")
    p_status.add_argument("--run-dir", default=DEFAULT_RUN_DIR)

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
    written = []
    for name, content in (
        (DEFAULT_CONFIG, _STARTER_CONFIG),
        (".env.example", _ENV_EXAMPLE),
    ):
        path = target / name
        if path.exists() and not args.force:
            print(f"exists, not overwritten: {path}  (use --force)")
            continue
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(path)
    for path in written:
        print(f"wrote {path}")
    if written:
        print("\nNext: point problem.skeleton at your skeleton file, then")
        print("  python -m evolvekit run --config evolvekit.yaml")
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    """0 clean, 1 warnings, 2 failures -- so a wrapper script can gate on it."""
    return run_preflight(
        load_config(args.config),
        provider_check=args.provider_check,
        candidate=args.candidate,
    )


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    log = (lambda _m: None) if args.quiet else print
    driver = Driver(config, run_dir=args.run_dir, log=log)
    summary = driver.run(args.generations)

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
    totals = summary.totals
    print(
        f"spend       : ${totals.get('usd', 0.0):.4f} over "
        f"{int(totals.get('calls', 0))} call(s), "
        f"{int(totals.get('total_tokens', 0))} token(s)"
    )
    print(f"run dir     : {driver.ledger.run_dir}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    ledger = Ledger(args.run_dir)
    rows = ledger.runs()
    if not rows:
        print(f"no runs recorded in {ledger.run_dir}")
        return 0
    totals = ledger.totals()
    ranked = rank(rows, 1)
    best = ranked[0] if ranked else None
    counts = novelty_counts(rows)
    archive = ledger.read_archive()
    print(f"run dir      : {ledger.run_dir}")
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


_COMMANDS = {
    "init": cmd_init,
    "preflight": cmd_preflight,
    "run": cmd_run,
    "status": cmd_status,
    "leaderboard": cmd_leaderboard,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
