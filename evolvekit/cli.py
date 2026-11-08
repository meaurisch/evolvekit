"""`python -m evolvekit init | preflight | run | status | dashboard | leaderboard | confirm | export | stop
| harness new/check/pack/install/list/discover | study new/preview/compile/run`."""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from evolvekit import __version__
from evolvekit.app import AppError
from evolvekit.harness import HarnessError
from evolvekit.harness.plan import plan_notes
from evolvekit.config import ConfigError, load_config
from evolvekit.economics import DEFAULT_WINDOW, format_series, series
from evolvekit.env import LoadedEnv, load_env_files
from evolvekit.events import utc_now
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
from evolvekit.stopping import STOP_REQUEST

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
    provider: fake        # azure | openrouter | claude-cli | copilot-cli | fake
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

# --- copilot-cli ---
# No key: sign in with `copilot`, then `/login`, or set COPILOT_GITHUB_TOKEN.
# Set this only if the binary is not on PATH.
# COPILOT_CLI_PATH=
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

    p_stop = sub.add_parser(
        "stop",
        help="ask a running search to stop: what is in flight is stopped within seconds, "
        "and the run can be resumed with the command that started it",
    )
    p_stop.add_argument("--run-dir", default=DEFAULT_RUN_DIR, help=f"default: {DEFAULT_RUN_DIR}")

    p_harness = sub.add_parser("harness", help="write, check, pack, install and list harnesses")
    harness = p_harness.add_subparsers(dest="harness_command", required=True)
    h_new = harness.add_parser("new", help="a new harness: a copy of the closest one, or a runnable skeleton")
    h_new.add_argument("directory")
    h_new.add_argument("--from", dest="source", metavar="HARNESS", help="a harness folder, or the id of a known harness")
    h_new.add_argument("--kind", choices=("python", "program"), default="python",
                       help="for the skeleton: an application importable in Python, or a program (default: python)")
    h_check = harness.add_parser("check", help="does the harness work, with this application? (exit 0 / 1 warnings / 2 failures)")
    h_check.add_argument("directory")
    h_check.add_argument("--app", help="the application: its Python (kind python) or the program (kind program)")
    h_check.add_argument("--json", action="store_true", help='print {"ok", "checks": [...]} for a script or an AI coding tool')
    h_check.add_argument("--samples", type=int, default=3, help="how many sample cases to solve (default 3)")
    h_pack = harness.add_parser("pack", help="check the harness and write <id>-<version>.zip")
    h_pack.add_argument("directory")
    h_pack.add_argument("--out", help="the folder to write the zip to (default: next to the harness)")
    h_install = harness.add_parser("install", help="install a harness .zip into the library home")
    h_install.add_argument("archive")
    h_install.add_argument("--home", help="the library home (default: EVOLVEKIT_HOME, else ~/evolvekit)")
    h_list = harness.add_parser("list", help="the harnesses evolvekit can offer")
    h_list.add_argument("--home", help="the library home (default: EVOLVEKIT_HOME, else ~/evolvekit)")
    h_discover = harness.add_parser(
        "discover", help="what an application exposes, before a harness exists: a first list of candidate settings")
    h_target = h_discover.add_mutually_exclusive_group(required=True)
    h_target.add_argument("--program", metavar="PATH", help="a program: runs PATH --version and PATH --help")
    h_target.add_argument("--python", metavar="PATH", help="a Python interpreter that imports --module")
    h_discover.add_argument("--module", metavar="NAME", help="with --python: the module to import and read")
    h_discover.add_argument("--json", action="store_true",
                            help='print {"application", "settings", "notes"} for a script or an AI coding tool')

    p_app = sub.add_parser("app", help="the app: set up, run and read studies in the browser")
    p_app.add_argument("action", nargs="?", choices=["install-startup", "uninstall-startup", "stop"],
                       help="install-startup: start the app at logon (Windows: the task \"evolvekit app\"; macOS, "
                            "Linux: prints the unit); uninstall-startup: undo it; stop: stop the app of this library")
    p_app.add_argument("--home", help="the library home (default: EVOLVEKIT_HOME, else ~/evolvekit)")
    p_app.add_argument("--port", type=int, default=None, help="first port to try (default 8780)")
    p_app.add_argument("--no-browser", action="store_true", help="print the address; do not open a browser")
    p_app.add_argument("--service", action="store_true",
                       help="no browser and no console: log to <home>/app.log and keep the campaigns' windows")
    p_app.add_argument("--clock", metavar="ISO",
                       help="a rehearsal clock for campaigns, starting at this time (a time without a zone is local)")
    p_app.add_argument("--clock-speed", metavar="N", type=float, default=None,
                       help="with --clock: how many times faster than real time it runs (1 to 3600, default 1)")
    p_app.add_argument("--shortcut", action="store_true",
                       help="write evolvekit.cmd to the Windows desktop, which starts the app with a double-click")

    p_doctor = sub.add_parser("doctor", help="is this machine ready? (exit 0 when every check passes, else 1)")
    p_doctor.add_argument("--home", help="the library home (default: EVOLVEKIT_HOME, else ~/evolvekit)")
    p_doctor.add_argument("--app", metavar="PATH", help="also ask every harness whether this application is its own")
    p_doctor.add_argument("--json", action="store_true", help="print the checks as a JSON list")

    p_skill = sub.add_parser("skill", help="the skills that ship with evolvekit: list and install them")
    skill = p_skill.add_subparsers(dest="skill_command", required=True)
    skill.add_parser("list", help="the skills that ship with evolvekit")
    k_install = skill.add_parser("install", help="write the skills into a project (.agents/skills, with a pointer in .claude/skills)")
    k_install.add_argument("--dir", default=".", help="the project folder (default: the current folder)")
    k_install.add_argument("--force", action="store_true", help="overwrite files that exist")

    p_study = sub.add_parser("study", help="make, preview, compile and run studies")
    study = p_study.add_subparsers(dest="study_command", required=True)
    s_new = study.add_parser("new", help="a new study folder from a harness (and a template)")
    s_new.add_argument("name", help="the study's name; its folder in the library home is made from it")
    s_new.add_argument("--harness", required=True, help="a harness folder, or the id of a known harness")
    s_new.add_argument("--template", help="a template id of the harness (default: a blank study)")
    s_new.add_argument("--app", help="where the application is (a path)")
    s_new.add_argument("--dir", help="write a standalone study folder here instead of into the library home")
    s_new.add_argument("--warm", action="store_true",
                       help="start from what finished studies on this harness learned: the latest `use` winner's "
                            "values as the starting point, and a prior around them for the search")
    s_new.add_argument("--home", help="the library home (default: EVOLVEKIT_HOME, else ~/evolvekit)")
    s_new.add_argument("--json", action="store_true", help='print {"slug", "root"} and nothing else')
    s_app = study.add_parser("application", help="say where a study's application is, or list the ones found here")
    s_app.add_argument("slug", help="the study's name in the library home")
    s_app.add_argument("path", nargs="?", help="the full path to the application")
    s_app.add_argument("--detect", action="store_true", help="list the applications found on this computer")
    s_app.add_argument("--home", help="the library home (default: EVOLVEKIT_HOME, else ~/evolvekit)")
    s_app.add_argument("--json", action="store_true", help="print JSON and nothing else")
    s_code = study.add_parser("code", help="say where a code study's source is, from which base, and what to improve")
    s_code.add_argument("slug", help="the study's name in the library home")
    s_code.add_argument("--repo", help="the source folder (default: the harness's own, while its folder is there)")
    s_code.add_argument("--from", dest="from_ref", metavar="REF",
                        help="the base: a branch, tag or commit, or `working` for the files as they are (default: HEAD)")
    s_code.add_argument("--brief", help="what the search should improve, in a sentence")
    s_code.add_argument("--home", help="the library home (default: EVOLVEKIT_HOME, else ~/evolvekit)")
    s_code.add_argument("--json", action="store_true", help='print {"ok", "repo", "from", "message"} and nothing else')
    s_list = study.add_parser("list", help="the studies in the library home")
    s_list.add_argument("--home", help="the library home (default: EVOLVEKIT_HOME, else ~/evolvekit)")
    s_list.add_argument("--json", action="store_true", help='print a list of {"slug", "name", "harness", "state"}')
    s_check = study.add_parser("check", help="name what is still missing before the study can run")
    s_check.add_argument("slug", help="the study's name in the library home")
    s_check.add_argument("--home", help="the library home (default: EVOLVEKIT_HOME, else ~/evolvekit)")
    s_cases = study.add_parser("cases", help="add cases to a study and hold some back for the final check")
    s_cases.add_argument("slug", help="the study's name in the library home")
    s_cases.add_argument("--add", nargs="+", required=True, metavar="FILE", help="the case files to add")
    s_cases.add_argument("--hold-back", type=int, help="how many cases to hold back (default: the suggestion)")
    s_cases.add_argument("--home", help="the library home (default: EVOLVEKIT_HOME, else ~/evolvekit)")
    s_cases.add_argument("--json", action="store_true", help='print {"added", "refused", "test"} and nothing else')
    home_help = "the library home (default: EVOLVEKIT_HOME, else ~/evolvekit)"
    s_start = study.add_parser("start", help="start the study, as the app's Start does (queued while another study runs; "
                                             "a study with a schedule waits for its first window)")
    s_start.add_argument("slug", help="the study's name in the library home")
    s_start.add_argument("--now", action="store_true",
                         help="a study with a schedule: start at once, whatever the window (no window's end stops "
                              "this session)")
    s_start.add_argument("--more", type=float, metavar="H",
                         help="a study with a schedule whose campaign finished its run: H hours more search time, "
                              "spent in its next window (never now), as the app's Continue does")
    s_start.add_argument("--wait", action="store_true", help="then wait until the run has ended, as study wait does")
    s_start.add_argument("--timeout", type=float, help="with --wait: give up waiting after this many seconds (exit 4)")
    s_start.add_argument("--home", help=home_help)
    s_start.add_argument("--json", action="store_true", help='print {"run", "started"|"queued"} (one JSON document per line)')
    s_bake = study.add_parser("bakeoff", help="check given branches of a code study against its starting point "
                                              "on the held-back cases, with no search")
    s_bake.add_argument("slug", help="the study's name in the library home")
    s_bake.add_argument("--ref", dest="refs", action="append", required=True, metavar="REF",
                        help="a branch, tag or commit of the source to compare (once per branch)")
    s_bake.add_argument("--wait", action="store_true", help="then wait until the run has ended, as study wait does")
    s_bake.add_argument("--timeout", type=float, help="with --wait: give up waiting after this many seconds (exit 4)")
    s_bake.add_argument("--home", help=home_help)
    s_bake.add_argument("--json", action="store_true", help='print {"run", "started"|"queued", "refs"} (one JSON document per line)')
    s_stop = study.add_parser("stop", help="ask the study's running run to stop, or take back a queued one; "
                                           "pauses a campaign")
    s_stop.add_argument("slug", help="the study's name in the library home")
    s_stop.add_argument("--home", help=home_help)
    s_stop.add_argument("--json", action="store_true", help="print JSON and nothing else")
    s_status = study.add_parser("status", help="where the study's run is: the state line, or the app's status document")
    s_status.add_argument("slug", help="the study's name in the library home")
    s_status.add_argument("--run", help="a run's id (default: the latest)")
    s_status.add_argument("--home", help=home_help)
    s_status.add_argument("--json", action="store_true", help="print the document the app's Running screen reads")
    s_watch = study.add_parser("watch", help="the status every few seconds until the run ends; starts a queued run")
    s_watch.add_argument("slug", help="the study's name in the library home")
    s_watch.add_argument("--every", type=float, default=5.0, help="seconds between looks (default 5)")
    s_watch.add_argument("--home", help=home_help)
    s_watch.add_argument("--json", action="store_true", help="print one status document per line")
    s_wait = study.add_parser("wait", help="wait until the run has ended -- for a campaign, until it has its answer "
                                           "(exit 0 done, 1 stopped, failed or paused, 4 timeout)")
    s_wait.add_argument("slug", help="the study's name in the library home")
    s_wait.add_argument("--timeout", type=float, help="give up after this many seconds (exit 4; default: no limit)")
    s_wait.add_argument("--home", help=home_help)
    s_wait.add_argument("--json", action="store_true", help="print the verdict document")
    s_verdict = study.add_parser("verdict", help="the final check's answer: the verdict and what to do about it")
    s_verdict.add_argument("slug", help="the study's name in the library home")
    s_verdict.add_argument("--run", help="a run's id (default: the latest)")
    s_verdict.add_argument("--home", help=home_help)
    s_verdict.add_argument("--json", action="store_true", help="print the verdict document")
    s_report = study.add_parser("report", help="the report of a run: the verdict document, or --markdown for people")
    s_report.add_argument("slug", help="the study's name in the library home")
    s_report.add_argument("--run", help="a run's id (default: the latest)")
    s_report.add_argument("--home", help=home_help)
    form = s_report.add_mutually_exclusive_group()
    form.add_argument("--markdown", action="store_true", help="the report as Markdown")
    form.add_argument("--json", action="store_true", help="the verdict document (the default)")
    s_export = study.add_parser("export", help="a code study's result as a file: --diff writes the best combination's patch")
    s_export.add_argument("slug", help="the study's name in the library home")
    s_export.add_argument("--diff", action="store_true", help="the best combination's change, as runs/<id>/best.patch")
    s_export.add_argument("--run", help="a run's id (default: the latest)")
    s_export.add_argument("--home", help=home_help)
    s_export.add_argument("--json", action="store_true", help='print {"patch"} and nothing else')
    s_pr = study.add_parser("pr", help="a code study's best change as a branch, pushed, with a PR whose body is the "
                                       "report; prints what it will do, and does it only with --yes")
    s_pr.add_argument("slug", help="the study's name in the library home")
    s_pr.add_argument("--run", help="a run's id (default: the latest)")
    s_pr.add_argument("--base", metavar="BRANCH", help="the branch the PR goes into (default: the repository's default)")
    s_pr.add_argument("--draft", action="store_true", help="open the PR as a draft")
    s_pr.add_argument("--yes", action="store_true", help="do it: make the branch, push it, open the PR")
    s_pr.add_argument("--home", help=home_help)
    s_pr.add_argument("--json", action="store_true", help="print the plan (and with --yes what was done) as JSON")
    s_groups = study.add_parser("groups", help="group the cases by their features (or by the tags given in the app's "
                                               "Cases list) and write groups.json")
    s_groups.add_argument("slug", help="the study's name in the library home")
    s_groups.add_argument("--k", type=int, help="this many groups, 2 to 5 (default: 2 or 3, whichever tells the "
                                                "cases apart best)")
    s_groups.add_argument("--home", help=home_help)
    s_groups.add_argument("--json", action="store_true", help="print groups.json")
    s_open = study.add_parser("open", help="print the study's address in the app (starting the app if none runs) and open it")
    s_open.add_argument("slug", help="the study's name in the library home")
    s_open.add_argument("--no-browser", action="store_true", help="only print the address")
    s_open.add_argument("--home", help=home_help)
    for name, text in (("preview", "solve the smallest training case at the starting values"),
                       ("compile", "compile the study into runs/<id>/evolvekit.yaml, with the automatic plan"),
                       ("run", "compile, search, and check the best on the held-back cases")):
        s_cmd = study.add_parser(name, help=text)
        s_cmd.add_argument("directory", help="a study folder, or the name of a study in the library home")
        s_cmd.add_argument("--home", help="the library home (default: EVOLVEKIT_HOME, else ~/evolvekit)")
        if name != "preview":
            s_cmd.add_argument("--run-id", help="the run's folder name (default: the time)")
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


def _desktop() -> Path:
    """The Windows desktop, OneDrive's when it has taken the folder over."""
    home = Path.home()
    for candidate in (home / "OneDrive" / "Desktop", home / "Desktop"):
        if candidate.is_dir():
            return candidate
    return home


CLOCK_SPEEDS = (1.0, 3600.0)
"""The rehearsal clock's speed, at least and at most (`app --clock-speed`)."""


def _app_clock(args: argparse.Namespace) -> tuple[datetime | None, float] | None:
    """`--clock` and `--clock-speed`, read: `(start or None, speed)`; None (said on stderr) when wrong."""
    if args.clock is None:
        if args.clock_speed is not None:
            print("error: --clock-speed needs --clock (the rehearsal clock's start)", file=sys.stderr)
            return None
        return None, 1.0
    try:
        start = datetime.fromisoformat(args.clock.strip())
    except ValueError:
        print(f"error: --clock {args.clock!r} is not a time such as 2026-11-13T17:30", file=sys.stderr)
        return None
    speed = 1.0 if args.clock_speed is None else float(args.clock_speed)
    if not CLOCK_SPEEDS[0] <= speed <= CLOCK_SPEEDS[1]:
        print(f"error: --clock-speed {speed:g}: from {CLOCK_SPEEDS[0]:g} to {CLOCK_SPEEDS[1]:g}", file=sys.stderr)
        return None
    return (start if start.tzinfo is not None else start.astimezone()), speed


def cmd_app(args: argparse.Namespace) -> int:
    """Serve the app for a library home until Ctrl+C (or `app stop`). Runs started from it carry
    on when it stops. One app per home: a second start opens the first. `--service`: no browser and
    no console, a log in the home. `install-startup`, `uninstall-startup`, `stop`: `app.service`."""
    from evolvekit.app import scheduler, service
    from evolvekit.app.running import read_running
    from evolvekit.app.server import DEFAULT_PORT, AppServer
    from evolvekit.dashboard import open_in_browser
    from evolvekit.harness.library import default_home

    home = Path(args.home).expanduser().resolve() if args.home else default_home()
    if args.action == "stop":
        return service.stop_app(home)
    if args.action == "install-startup":
        return service.install_startup(home)
    if args.action == "uninstall-startup":
        return service.uninstall_startup(home)
    if args.shortcut:
        if os.name != "nt":
            print("error: --shortcut writes a Windows .cmd file; elsewhere, start the app with "
                  "`python -m evolvekit app`", file=sys.stderr)
            return 1
        from evolvekit.resources import APP_EXE, bundle_dir, is_frozen, python_argv

        target = _desktop() / "evolvekit.cmd"
        if is_frozen():  # the app exe is windowless: start it, and this window closes
            start = f'start "" "{bundle_dir() / APP_EXE}" --home "{home}"\r\n'
            said = "double-click it to start the app"
        else:
            start = f'"{python_argv()[0]}" -m evolvekit app --home "{home}"\r\npause\r\n'
            said = "double-click it to start the app, and keep its window open while you work"
        # UTF-8 and CRLF as written (newline=""): cmd.exe reads a .cmd in the OEM code page unless told (D9)
        target.write_text("@echo off\r\nchcp 65001 >nul\r\ntitle evolvekit\r\n" + start, encoding="utf-8", newline="")
        print(f"wrote {target}: {said}")
        return 0
    clock = _app_clock(args)
    if clock is None:
        return 2
    running = read_running(home)
    if running is not None:  # one app per home: two tickers never share one
        unset = ("the rehearsal clock was not set: stop that app first (evolvekit app stop), then start it with --clock"
                 if clock[0] is not None else None)
        if args.service:
            logger = service.start_logging(home)
            logger.info("already running at %s (the key is in app.json)", service.address(str(running.get("url"))))
            if unset:
                logger.info("%s", unset)
            service.stop_logging(logger)
            return 0
        print(f"the app is already running for this library: {running.get('url')}", flush=True)
        if unset:
            print(unset, file=sys.stderr, flush=True)
        if not args.no_browser:
            open_in_browser(str(running.get("url")))
        return 0
    if args.service:
        return _serve_service(home, args.port or DEFAULT_PORT, clock)
    server = AppServer(home, port=args.port or DEFAULT_PORT)
    try:
        _set_clock(home, clock)
        print(f"evolvekit app : {server.url_with_token}  (open it in your browser, as printed: the address carries this launch's key)", flush=True)
        print(f"library home  : {server.home.root}", flush=True)
        if clock[0] is not None:
            print(scheduler.clock_of(home).words(), flush=True)
        print("Keep this window open while you work. Runs carry on when it closes; Ctrl+C stops the app.", flush=True)
        if not args.no_browser:
            open_in_browser(server.url_with_token)
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        scheduler.clear_clock(home)
        server.stop()
    return 0


def _set_clock(home: Path, clock: tuple[datetime | None, float]) -> None:
    """The rehearsal clock of this app, or none: a `clock.json` left by an app that ended without
    its `finally` is not this one's."""
    from evolvekit.app import scheduler

    start, speed = clock
    if start is None:
        scheduler.clear_clock(home)
    else:
        scheduler.write_clock(home, start, speed)


def _serve_service(home: Path, port: int, clock: tuple[datetime | None, float]) -> int:
    """`app --service`: what it says, and the ticker's lines and tracebacks, go to `<home>/app.log`;
    no browser, no console. It ends on `app stop` (or a service manager's SIGTERM), cleanly."""
    import signal

    from evolvekit.app import scheduler, service
    from evolvekit.app.server import AppServer

    logger = service.start_logging(home, redirect=True)
    try:
        server = AppServer(home, port=port)
    except Exception:  # noqa: BLE001 - said in the log: there is no console to say it on
        logger.exception("the app could not start")
        service.stop_logging(logger)
        return 1
    code = 0
    try:
        _set_clock(home, clock)
        logger.info("evolvekit app : %s  (the key is in app.json)", server.url)
        logger.info("library home  : %s", server.home.root)
        if clock[0] is not None:
            logger.info("%s", scheduler.clock_of(home).words())
        try:  # systemd's or launchd's stop ends it as `app stop` does
            signal.signal(signal.SIGTERM, lambda *_: server.request_shutdown())
        except (ValueError, OSError, AttributeError):
            pass
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception:  # noqa: BLE001 - said in the log before the streams are given back
        logger.exception("the app stopped on an error")
        code = 1
    finally:
        scheduler.clear_clock(home)
        server.stop()
        logger.info("stopped")
        service.stop_logging(logger)
    return code


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
    # With evaluate.score.levels the claim is the lexicographic one: better,
    # and clear, on the first level that is not equal.
    confirmed = all(
        result["levels"]["confirmed"] if "levels" in result else (result["summary"].get("ci95") or [0.0])[0] > 0
        for result in comparison.per_candidate.values()
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


def cmd_stop(args: argparse.Namespace) -> int:
    """Write the run directory's `stop-request` (`evolvekit/stopping.py`)."""
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"error: there is no run directory at {run_dir.resolve()}", file=sys.stderr)
        return 1
    (run_dir / STOP_REQUEST).write_text(utc_now() + "\n", encoding="utf-8")
    print(
        f"asked the run in {run_dir} to stop. Evaluations in flight are stopped within seconds and "
        "the run ends with `stopped on request`; the command that started it resumes it"
    )
    return 0


def _harness_folder(value: str, home: str | None) -> Path:
    """A harness given as a folder, or by the id of one evolvekit knows."""
    from evolvekit.harness.library import find_harnesses

    if Path(value).is_dir():
        return Path(value)
    known = [e for e in find_harnesses(Path(home) if home else None) if e.harness and e.harness.id == value]
    if not known:
        raise ValueError(f"{value!r} is neither a harness folder nor the id of a known harness; "
                         "`python -m evolvekit harness list` shows them")
    return max(known, key=lambda e: [int(p) for p in e.harness.version.split(".")]).path  # type: ignore[union-attr]


def cmd_harness(args: argparse.Namespace) -> int:
    from evolvekit.harness import library
    from evolvekit.harness.check import check_harness, exit_code, render

    command = args.harness_command
    if command == "new":
        source = _harness_folder(args.source, None) if args.source else None
        folder = library.new_harness(args.directory, source=source, kind=args.kind)
        print(f"wrote a new harness in {folder}" + (f", copied from {source}" if source else f" (a {args.kind} skeleton)"))
        print("next: read AGENTS.md, then run  python -m evolvekit harness check "
              f"{folder} --app PATH --json  until it passes")
        return 0
    if command == "check":
        checks = check_harness(args.directory, args.app, samples=args.samples)
        print(render(checks, as_json=args.json))
        return exit_code(checks)
    if command == "pack":
        print(f"wrote {library.pack_harness(args.directory, args.out).resolve()}")
        return 0
    if command == "install":
        harness = library.install_harness(args.archive, Path(args.home) if args.home else None)
        print(f"installed {harness.title} {harness.version} in {harness.root}")
        return 0
    if command == "discover":
        from evolvekit.harness import discover

        if args.python and not args.module:
            raise ValueError("--python needs --module NAME: the module to import and read")
        if args.program and args.module:
            raise ValueError("--module goes with --python, not with --program")
        found = (discover.discover_program(args.program) if args.program
                 else discover.discover_python(args.python, args.module))
        print(json.dumps(found, indent=2) if args.json else discover.render(found))
        return 0
    entries = library.find_harnesses(Path(args.home) if args.home else None)
    if not entries:
        print("no harnesses: install one with  python -m evolvekit harness install FILE.zip")
    for entry in entries:
        where = "built-in" if entry.built_in else "installed"
        if entry.harness is None:
            print(f"{entry.path.name:<24} {where:<9} does not load: {entry.problem}")
        else:
            print(f"{entry.harness.id:<16} {entry.harness.version:<8} {where:<9} {entry.harness.title}  ({entry.path})")
    return 0


def _home(args: argparse.Namespace):
    from evolvekit.app.store import Home
    from evolvekit.harness.library import default_home

    return Home(args.home or default_home())


def _full_path(path: str) -> str:
    """A path given on the command line, relative to where the command runs,
    made whole: the app and its API take full paths only."""
    path = str(path).strip().strip('"')
    return str(Path(path).resolve()) if path else path


def _study_root(value: str, args: argparse.Namespace) -> Path:
    """A study given as a folder on disk, else as a name in the library home
    (the home is only opened when the value is not a folder)."""
    folder = Path(value)
    if folder.is_dir():
        if not folder.is_absolute() and folder.name == value and (_home(args).studies_dir / value / "study.yaml").is_file():
            print(f"using the folder {folder.resolve()}", file=sys.stderr)
        return folder
    return _home(args).study_root(value)


def _study_library_command(command: str, args: argparse.Namespace) -> int:
    from evolvekit.app import jobs
    from evolvekit.app.detect import detect
    from evolvekit.app.store import STEPS

    home = _home(args)
    if command == "list":
        rows = []
        for slug in home.slugs():
            root, study, harness = home.load(slug)
            rows.append({"slug": slug, "name": study.name, "harness": harness.title,
                         "state": jobs.state_line(root, study, build_status)["line"]})  # the round, as the README shows
        if args.json:
            print(json.dumps(rows))
        for row in [] if args.json else rows:
            print(f"{row['slug']}  {row['name']}  {row['harness']}  {row['state']}")
        return 0
    slug = args.slug
    if command == "check":
        problems = home.problems(slug)
        for problem in problems:
            print(f"{problem['key']}: {problem['text']}" if problem["key"] != problem["text"] else problem["text"],
                  file=sys.stderr)
        if problems:
            return 2
        _advance_step(home.study_root(slug), len(STEPS))  # ready: the Set up's last step, Review and start
        print("ready: no problems")
        study = home.load(slug)[1]
        print(_plan_line(study))
        for note in plan_notes(len(study.test)):  # a note, not a problem: the study runs as it is
            print(f"note: {note}")
        _say_next(args, f"study start {slug}")
        return 0
    if command == "application":
        if args.detect:
            _, _, harness = home.load(slug)
            found = detect(harness, recent=home.settings()["recent_applications"])
            if args.json:
                print(json.dumps(found))
            for item in [] if args.json else found:
                print(f"{item['message']}  {item['path']}")
            return 0
        if not args.path:
            raise AppError("path: where the application is (or --detect to look for it)")
        answer = home.set_application(slug, _full_path(args.path))
        if args.json:
            print(json.dumps(answer))
        elif answer["ok"]:
            print(f"{answer['path']}" + (f" (version {answer['version']})" if answer["version"] else ""))
        if not answer["ok"]:
            print(answer["message"], file=sys.stderr)
            return 1
        _advance_step(home.study_root(slug), _STEP_AFTER_APPLICATION)
        return 0
    if command == "code":
        answer = home.set_code(slug, _full_path(args.repo) if args.repo else None, args.from_ref, args.brief)
        if args.json:
            print(json.dumps(answer))
        if not answer["ok"]:
            print(answer["message"], file=sys.stderr)
            return 1
        if not args.json:
            _, study, harness = home.load(slug)
            files = study.code.open or list(harness.code.open)
            print(f"source: {answer['repo']} (from {answer['from']}); files: {', '.join(files)}")
        _say_next(args, f"study check {slug}")
        return 0
    files: list[Path] = []
    for value in args.add:
        if any(mark in value for mark in "*?[") and not Path(value).is_file():
            # PowerShell and cmd hand a pattern over as it is: expand it here.
            matches = sorted(glob.glob(value))
            if not matches:
                print(f"no file matches {value}", file=sys.stderr)
                return 1
            files.extend(Path(m) for m in matches)
        else:
            files.append(Path(value))
    result = home.add_cases(slug, files, hold_back=args.hold_back)
    if args.json:
        print(json.dumps(result))
    else:
        n = len(result["added"])
        print(f"{n} case{'' if n == 1 else 's'} added, {len(result['test'])} held back" if n else "nothing added")
    for refused in result["refused"]:
        print(refused["reason"], file=sys.stderr)
    if result["refused"]:
        return 1
    if result["added"]:
        _advance_step(home.study_root(slug), _STEP_AFTER_CASES)
        _say_next(args, f"study check {slug}")
    return 0


_STEP_AFTER_APPLICATION = 3
"""The Set up's step after Application (3, Cases): where `study application` leaves a study."""
_STEP_AFTER_CASES = 4
"""The step after Cases (4, What may change): where `study cases` leaves a study."""


def _advance_step(root: Path, step: int) -> None:
    """Move the study's Set up step (the app's "draft · step N of 7") on to
    `step`, never back: a command finished the step before it."""
    from evolvekit.harness.study import load_study, save_study

    study = load_study(root)
    if study.step < step:
        study.step = step
        save_study(study, root)


WAIT_EVERY_S = 2.0
"""How often `study wait` looks at the run, and ticks the queue."""
_ENDED = ("done", "stopped", "failed")


def _tick_queue(home) -> None:
    """Launch the run that waits longest when nothing is active, as the app's
    ticker does, so a queued study starts with no app open. A launch that
    fails -- any study's, for any reason -- is said in one line and the wait
    goes on, as the app's ticker does: the next tick tries again. Then the
    scheduler looks at the campaigns, as the app's ticker does, on the
    rehearsal clock while the app that set one runs."""
    from evolvekit.app import scheduler

    try:
        scheduler.launch_queued(home)  # `jobs.launch_queued`, under the scheduler's lock
    except Exception as exc:  # noqa: BLE001 - a fault in another study's launch does not end this wait
        print(f"could not start a queued run: {exc}", file=sys.stderr)
    try:
        clock = scheduler.clock_of(home)
        scheduler.tick(home, now=clock.now(), speed=clock.speed)
    except Exception as exc:  # noqa: BLE001 - nor a fault in a campaign
        print(f"could not look at the campaigns: {exc}", file=sys.stderr)


def _gone(run_dir: Path, state: str | None) -> bool:
    """Whether the run has left: its folder is gone, or it reads as nothing
    -- not queued, no launch under way, no job -- as a dequeue leaves it.
    A run with a `job.json` is never gone, whatever its phase reads as."""
    from evolvekit.app import jobs

    if not run_dir.is_dir():
        return True
    return (state == "unknown" and not (run_dir / jobs.QUEUED).exists() and not (run_dir / jobs.LAUNCH).exists()
            and not (run_dir / "job.json").exists())


def _say_gone(run: str) -> int:
    print(f"run {run} is gone: it was taken out of the queue, or its folder was removed", file=sys.stderr)
    return 1


def _run_of(root: Path, run: str | None = None) -> str:
    from evolvekit.app import jobs

    run = run or jobs.latest_run(root)
    if not run:
        raise AppError("the study has not been started yet", 404)
    return run


def _home_flag(home: str | None) -> str:
    """` --home "<home>"` as the user wrote it, quoted for any shell; nothing without one."""
    return f' --home "{home}"' if home else ""


def _say_next(args: argparse.Namespace, *commands: str) -> None:
    """The next step for people, on stderr (`next: evolvekit study start depot-a`), with the
    `--home` the user passed: never on stdout and never with `--json`, so scripts read what they did."""
    if getattr(args, "json", False):
        return
    sys.stdout.flush()  # the command's own result first: a piped stdout is buffered, stderr is not
    flag = _home_flag(getattr(args, "home", None))
    said = ", or ".join("evolvekit " + command + flag for command in commands)
    print(f"next: {said}", file=sys.stderr)


def _plan_line(study) -> str:
    """The plan `study check` says the study will run, as study.yaml writes it
    (no solving): "plan: 2 rounds × 2 combinations, budget 0.05 h", or
    "plan: automatic (budget 0.1 h)"."""
    plan, budget = dict(study.plan or {}), f"budget {float(study.budget.hours):g} h"
    if plan.get("auto", True):
        return f"plan: automatic ({budget})"
    rounds, children = plan.get("rounds", plan.get("generations")), plan.get("children")
    shape = " × ".join(part for part in (
        f"{rounds} round{'' if rounds == 1 else 's'}" if rounds else "",
        f"{children} combination{'' if children == 1 else 's'}" if children else "") if part)
    return f"plan: {shape}, {budget}" if shape else f"plan: set by hand ({budget})"


def _say_verdict(document: dict) -> None:
    """The verdict's sentence (and, on a keep whose held-back cases all
    improved, why it is still keep), then what to do about it, one per line;
    for a bake-off each branch's own line by its ref; then how much of the
    plan ran when the run ran less of it than planned."""
    print(document["line"])
    if document.get("why_keep"):
        print(document["why_keep"])  # why a keep whose held-back cases all improved is still keep
    headline = str(document["line"])
    for candidate in document.get("candidates") or []:
        words = str(candidate.get("line", ""))
        if words and (words in headline or words[:1].lower() + words[1:] in headline):
            continue  # the headline already says this branch's line (a bake-off of one branch)
        print(f"  {candidate.get('ref')}: {words}")
    for name, own in (document.get("groups") or {}).items():  # a run per kind of case: each group's own line
        print(f"  {name}: {own.get('line', '')}")
    if document.get("advice"):
        print(document["advice"])
    if document.get("how_it_ran"):
        print(document["how_it_ran"])
    if (document.get("spent") or {}).get("line"):
        print(document["spent"]["line"])  # "AI: $0.87 of $1.00": a fact, as the report says it


OPEN_WAIT_S = 15.0
"""How long `study open` waits for an app it started to say it is running."""
APP_START_LOG = "app-start.log"
"""Where the launcher's output of the app that `study open` starts goes, in the home: the service
writes `app.log` itself, and rotation cannot rename a file another handle holds on Windows."""


def _study_open(args: argparse.Namespace) -> int:
    """Print the study's address in the running app -- starting the app,
    detached, when none runs -- and open it unless `--no-browser`."""
    import subprocess

    from evolvekit.app import detach
    from evolvekit.app.running import read_running
    from evolvekit.dashboard import open_in_browser
    from evolvekit.resources import app_argv

    home = _home(args)
    home.study_root(args.slug)  # no app is started for a study that is not there
    running = read_running(home.root)
    if running is None:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        argv = [*app_argv(), "--home", str(home.root), "--service"]
        try:
            detach.spawn_detached(argv, cwd=home.root, env=env, log=home.root / APP_START_LOG)
        except (detach.DetachError, subprocess.TimeoutExpired):
            pass  # the wait below says so
        deadline = time.monotonic() + OPEN_WAIT_S
        while running is None and time.monotonic() < deadline:
            time.sleep(0.2)
            running = read_running(home.root)
        if running is None:
            print("the app did not start; run `evolvekit app` and look at its output", file=sys.stderr)
            return 1
    url = f"{running['url']}#/study/{args.slug}"
    print(url, flush=True)
    if not args.no_browser:
        open_in_browser(url)
    return 0


def _verdict_command(command: str, args: argparse.Namespace) -> int:
    """`study verdict` and `study report`: from the verdict document."""
    from evolvekit.app import jobs
    from evolvekit.app.report import render_report
    from evolvekit.app.verdict import build_verdict

    root = _home(args).study_root(args.slug)
    document = build_verdict(root, args.run)
    if command == "verdict":
        if args.json:
            print(json.dumps(document))
        else:
            _say_verdict(document)
    elif args.markdown:
        print(render_report(document, jobs.results(root, document["run"], build_status), root=root), end="")
    else:
        print(json.dumps(document))
    return 0


def _change_command(command: str, args: argparse.Namespace) -> int:
    """`study export --diff` and `study pr` (design §5.6): a code study's best change as a
    patch, or as a branch, a push and a PR -- the last two only with `--yes`. A refusal
    before anything is done exits 2; a failure while doing it exits 1."""
    from evolvekit.harness import pr

    if command == "export" and not args.diff:
        print("name what to export: --diff (the best combination's change as a patch)", file=sys.stderr)
        return 2
    def failed(exc: HarnessError, code: int, extra: dict | None = None) -> int:
        """The sentence on stderr; with `--json` also `{"ok": false, "error", "state"}` on stdout."""
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc), "state": getattr(exc, "state", "none"), **(extra or {})}))
        print(exc, file=sys.stderr)
        return code

    root = _home(args).study_root(args.slug)
    try:
        if command == "export":
            patch = pr.export_diff(root, args.run)
        else:
            plan = pr.plan_pr(root, args.run, base=args.base, draft=args.draft)
    except HarnessError as exc:
        return failed(exc, 2)
    if command == "export":
        print(json.dumps({"ok": True, "patch": str(patch)}) if args.json else patch)
        return 0
    if not args.yes:
        if args.json:
            print(json.dumps({"ok": True, **plan.to_json(), "done": False}))
            return 0
        for line in pr.describe(plan):
            print(line)
        print("nothing was done: run it again with --yes to do this")
        return 0
    if not args.json:
        for line in pr.describe(plan):
            print(line)
        sys.stdout.flush()
    try:
        done = pr.open_pr(plan)
    except HarnessError as exc:
        return failed(exc, 1, {"branch": getattr(exc, "branch", plan.branch), "commit": getattr(exc, "commit", None)})
    if args.json:
        print(json.dumps({"ok": True, **plan.to_json(), **done, "done": True}))
        return 0
    print(f"made the branch {done['branch']} at {done['commit'][:12]}")
    print(f"pushed it to {plan.remote}" if done["pushed"] else "not pushed: there is no remote")
    print(f"PR: {done['url']}" if done["url"] else done["note"])
    return 0


def _wait(home, slug: str, timeout: float | None, as_json: bool, args: argparse.Namespace | None = None) -> int:
    """`study wait`: until the latest run has ended -- 0 done (the verdict),
    1 stopped or failed, 4 when `timeout` seconds pass first; on an armed
    campaign, until the campaign has its answer (`_wait_campaign`). For people (not
    `as_json`), the state line on stderr whenever it changes, as `study watch`
    prints it. With `args`, a run that is done says the next step on stderr."""
    from evolvekit.app import jobs
    from evolvekit.app.verdict import build_verdict

    root, study, _ = home.load(slug)
    if study.schedule is not None:
        from evolvekit.app import scheduler

        if (scheduler.read_campaign(root) or {}).get("state") == "armed":
            return _wait_campaign(home, slug, root, study, timeout, as_json, args)
    run = _run_of(root)
    run_dir = jobs._run_dir(root, run)  # noqa: SLF001 - the one check of a run's name
    deadline = None if timeout is None else time.monotonic() + timeout
    said = None
    try:
        while True:
            _tick_queue(home)
            state = jobs.phase(run_dir).get("phase")
            if state in _ENDED:
                break
            if _gone(run_dir, state):
                return _say_gone(run)
            if not as_json:
                # What `study watch` prints, on stderr and once per change: a long wait is not a hang.
                # The progress is cosmetic: a file half-written by the job must never end the wait.
                try:
                    line = jobs.run_line(root, study, run_dir, build_status)["line"]
                except Exception:  # noqa: BLE001 - progress only
                    line = said
                if line != said:
                    print(line, file=sys.stderr, flush=True)
                    said = line
            if deadline is not None and time.monotonic() >= deadline:
                print(f"run {run} is still {state} after {timeout:g} s; it goes on "
                      f"(evolvekit study wait {slug} waits again)", file=sys.stderr)
                return 4
            pause = WAIT_EVERY_S if deadline is None else min(WAIT_EVERY_S, deadline - time.monotonic())
            time.sleep(max(0.0, pause))
    except KeyboardInterrupt:
        print(f"stopped waiting; run {run} goes on", file=sys.stderr)
        return 130
    job = jobs.phase(run_dir)
    try:
        document = build_verdict(root, run)
    except (AppError, HarnessError):
        if state == "done":
            raise
        document = {"study": slug, "run": run, "phase": state, "error": job.get("error")}
    if as_json:
        print(json.dumps(document))
    elif state == "done":
        _say_verdict(document)
    else:
        print(jobs.run_line(root, study, run_dir, build_status)["line"])
    if state != "done" and job.get("error"):
        print(job["error"], file=sys.stderr)
    if state == "done" and args is not None:
        _say_next(args, f"study report {slug} --markdown", f"study open {slug}")
    return 0 if state == "done" else 1


def _watch(home, slug: str, every: float, as_json: bool) -> int:
    """`study watch`: the status every `every` seconds until the run ends --
    a document per line with `as_json`, else the state line when it changes."""
    from evolvekit.app import jobs

    root, study, _ = home.load(slug)
    run = _run_of(root)
    run_dir = jobs._run_dir(root, run)  # noqa: SLF001 - the one check of a run's name
    said = None
    try:
        while True:
            _tick_queue(home)
            if _gone(run_dir, jobs.phase(run_dir).get("phase")):
                return _say_gone(run)
            if as_json:
                document = jobs.status(root, run, build_status)
                state = document.get("phase")
                print(json.dumps(document), flush=True)
            else:
                state = jobs.phase(run_dir).get("phase")
                line = jobs.run_line(root, study, run_dir, build_status)["line"]
                if line != said:
                    print(line, flush=True)
                    said = line
            if state in _ENDED:
                return 0 if state == "done" else 1
            time.sleep(max(0.0, every))
    except KeyboardInterrupt:
        print(f"stopped watching; run {run} goes on", file=sys.stderr)
        return 130


SERVICE_NOTE = ("note: windows are kept while the app runs: evolvekit app --service, or evolvekit app "
                "install-startup to start it at logon")
"""Said on stderr after `study start` schedules a study and no app runs for the home."""


def _start_campaign(home, root: Path, args: argparse.Namespace) -> int:
    """`study start` on a study with a schedule: the campaign armed, its first window to start it
    (D3) -- or with `--now` a session at once, by hand, which no window's end stops (D4)."""
    from evolvekit.app import jobs, scheduler
    from evolvekit.app.running import read_running

    now = scheduler.clock_of(home).now()
    slug = root.name
    if args.more is not None:
        if args.now:
            raise AppError("study start: --more or --now, not both (--more waits for the next window)")
        run = jobs.latest_run(root)
        if run is None:
            raise AppError("the study has no run to give more time to: evolvekit study start schedules its first")
        more = scheduler.continue_campaign(home, root, run, args.more, now=now)
        if args.json:
            print(json.dumps({key: more[key] for key in ("scheduled", "run", "hours", "next", "edge", "words")}), flush=True)
        else:
            print(more["words"], flush=True)
        if read_running(home.root) is None:
            print(SERVICE_NOTE, file=sys.stderr)
        if args.wait:
            return _wait(home, slug, args.timeout, args.json, args)
        _say_next(args, f"study wait {slug}", f"study status {slug}")
        return 0
    answer = scheduler.start_campaign(home, root, now=now, at_once=bool(args.now))
    renewed = answer.get("renewed")
    anew = f"a new campaign (the last one {renewed}); " if renewed else ""
    if answer.get("scheduled"):
        campaign = answer["campaign"] or {}
        if args.json:
            print(json.dumps({"scheduled": True, "next": campaign.get("next"), "edge": campaign.get("edge"),
                              **({"new_campaign": True} if renewed else {})}), flush=True)
        else:
            then = "now" if campaign.get("edge") == "end" else "then"
            print(f"scheduled: {anew}{answer['words']} (the app, or evolvekit study wait, starts it {then})", flush=True)
        if read_running(home.root) is None:
            print(SERVICE_NOTE, file=sys.stderr)
    else:
        run = answer["run"]
        if answer.get("queued"):
            other = jobs._waiting_for(root) or "another study"  # noqa: SLF001 - the name the app's card shows
            document = {"run": run, "queued": True}
            line = (f"queued behind {other}; it starts when that study finishes "
                    "(the app or `evolvekit study wait` starts it)")
        else:
            document, line = {"run": run, "started": True}, f"started run {run}"
        if renewed:
            document["new_campaign"] = True
            line += f": {anew.rstrip('; ')}"
        print(json.dumps(document) if args.json else line, flush=True)
    if args.wait:
        return _wait(home, slug, args.timeout, args.json, args)
    _say_next(args, f"study wait {slug}", f"study status {slug}")
    return 0


def _stop_campaign(home, root: Path, slug: str, args: argparse.Namespace) -> int:
    """`study stop` on an armed campaign: a person's stop pauses it (D4), and its run is asked to
    stop, or taken out of the queue."""
    from evolvekit.app import jobs, scheduler

    run = jobs.latest_run(root)
    answer = scheduler.stop_by_person(home, root, run)
    resumes = "evolvekit study start resumes it"
    if args.json:
        print(json.dumps(answer))
    elif answer.get("dequeued"):
        print(f"paused the campaign and took run {run} out of the queue; {resumes}")
    elif answer.get("stopping"):
        print(f"paused the campaign and asked run {run} to stop (evolvekit study wait {slug} returns when it has); "
              f"{resumes}")
    else:
        print(f"paused the campaign (no run was running); {resumes}")
    return 0


def _wait_campaign(home, slug: str, root: Path, study, timeout: float | None, as_json: bool,
                   args: argparse.Namespace | None) -> int:
    """`study wait` on an armed campaign (D22): until it has its answer (`scheduler.campaign_done`:
    complete, ended or paused, no run active) -- a window's stop is not an end, and no run need exist
    yet. For people, the campaign's line on stderr whenever it changes. 0 when the latest run is
    done (its verdict), 1 when the campaign paused or ended without one, 4 on timeout."""
    from evolvekit.app import jobs, scheduler
    from evolvekit.app.verdict import build_verdict
    from evolvekit.harness.study import load_study

    deadline = None if timeout is None else time.monotonic() + timeout
    said = None
    try:
        while True:
            _tick_queue(home)
            try:
                study = load_study(root)  # the windows may change between sessions (D1)
            except (HarnessError, OSError, ValueError):
                pass  # being edited: the last one read
            now = scheduler.clock_of(home).now()
            if scheduler.campaign_done(root, study, now=now):
                break
            if not as_json:
                try:
                    line = (scheduler.campaign_state(root, study, now=now) or {}).get("line") or said
                except Exception:  # noqa: BLE001 - progress only
                    line = said
                if line != said:
                    print(line, file=sys.stderr, flush=True)
                    said = line
            if deadline is not None and time.monotonic() >= deadline:
                print(f"the campaign of {slug} goes on after {timeout:g} s "
                      f"(evolvekit study wait {slug} waits again)", file=sys.stderr)
                return 4
            pause = WAIT_EVERY_S if deadline is None else min(WAIT_EVERY_S, deadline - time.monotonic())
            time.sleep(max(0.0, pause))
    except KeyboardInterrupt:
        print(f"stopped waiting; the campaign of {slug} goes on", file=sys.stderr)
        return 130
    run = scheduler.campaign_run(root)  # the campaign's own: a new campaign's, not the last one's
    job = jobs.phase(jobs._run_dir(root, run)) if run else {}  # noqa: SLF001 - the one check of a run's name
    state = job.get("phase")
    campaign = scheduler.campaign_state(root, study, now=scheduler.clock_of(home).now())
    if campaign is None:  # the schedule was taken out while it waited: the tick paused the campaign
        stored = scheduler.read_campaign(root) or {}
        campaign = {"state": stored.get("state"), "line": f"Campaign · paused: {stored.get('reason') or scheduler.NO_SCHEDULE}"}
    if state == "done":
        document = build_verdict(root, run)
        if as_json:
            print(json.dumps({**document, "campaign": campaign}))
        else:
            _say_verdict(document)
        if args is not None:
            _say_next(args, f"study report {slug} --markdown", f"study open {slug}")
        return 0
    if as_json:
        print(json.dumps({"study": slug, "run": run, "phase": state, "error": job.get("error"), "campaign": campaign}))
    else:
        print(campaign.get("line") or "the campaign ended without a finished run")
        if job.get("error"):
            print(job["error"], file=sys.stderr)
    return 1


def _study_run_command(command: str, args: argparse.Namespace) -> int:
    """`study start | stop | status | watch | wait`: the app's runner
    (`evolvekit.app.jobs`), on disk, whether or not the app is open."""
    from evolvekit.app import jobs

    home = _home(args)
    slug = args.slug
    if command == "wait":
        return _wait(home, slug, args.timeout, args.json, args)
    if command == "watch":
        return _watch(home, slug, args.every, args.json)
    root, study, _ = home.load(slug)
    if command == "bakeoff":
        return _bakeoff(home, root, study, args)
    if command == "start":
        if study.schedule is not None:
            return _start_campaign(home, root, args)
        if args.more is not None:
            raise AppError("study start --more: only on a study with a schedule (its next window spends the time)")
        answer = jobs.start(home, root)
        run = answer["run"]
        if answer.get("queued"):
            other = jobs._waiting_for(root) or "another study"  # noqa: SLF001 - the name the app's card shows
            document = {"run": run, "queued": True}
            line = (f"queued behind {other}; it starts when that study finishes "
                    "(the app or `evolvekit study wait` starts it)")
        else:
            document, line = {"run": run, "started": True}, f"started run {run}"
        print(json.dumps(document) if args.json else line, flush=True)
        if args.wait:
            return _wait(home, slug, args.timeout, args.json, args)
        _say_next(args, f"study wait {slug}", f"study watch {slug}")
        return 0
    if command == "stop":
        from evolvekit.app import scheduler

        if (scheduler.read_campaign(root) or {}).get("state") == "armed":
            return _stop_campaign(home, root, slug, args)
        run = _run_of(root)
        answer = jobs.stop(root, run)
        if args.json:
            print(json.dumps(answer))
        elif answer.get("dequeued"):
            print(f"dequeued run {run}: it never started, and the study is a draft again")
        else:
            print(f"asked run {run} to stop (evolvekit study wait {slug} returns when it has)")
        return 0
    from evolvekit.app import scheduler

    campaign = (scheduler.campaign_state(root, study, now=scheduler.clock_of(home).now())
                if study.schedule is not None else None)
    if args.json:
        run = args.run or jobs.latest_run(root)
        if run is None and campaign is not None:
            print(json.dumps({"run": None, "phase": None, "campaign": campaign}))  # armed, before its first window
            return 0
        document = jobs.status(root, _run_of(root, run), build_status)
        if campaign is not None:
            document["campaign"] = campaign
        print(json.dumps(document))
    else:
        card = (jobs.run_line(root, study, jobs._run_dir(root, args.run), build_status) if args.run  # noqa: SLF001
                else jobs.state_line(root, study, build_status))
        print(card["line"])
        if campaign is not None and card.get("kind") != "campaign":
            print(campaign["line"])  # the campaign beside the run's own line: its next window, or its end
        if campaign is not None and campaign.get("history_line"):
            print(campaign["history_line"])  # when its sessions ran, on the window clock
        if (card.get("pace") or {}).get("line"):
            print(card["pace"]["line"])  # a live search: how many of the planned rounds its pace buys
        if card.get("kind") == "running" and (card.get("spent") or {}).get("line"):
            print(card["spent"]["line"])  # a live search with AI: what it spent so far, of its cap
        verdict = card.get("verdict") or {}
        if verdict.get("line"):
            # A finished run: the verdict's own words under the card's, each branch's for a bake-off.
            _say_verdict(verdict)
        if (card.get("branch") or {}).get("line"):
            print(card["branch"]["line"])  # what `study pr` made for this run
        earlier = card.get("earlier") or {}
        if earlier.get("says"):
            print(earlier["says"])  # a later run must not hide an earlier use
            if (earlier.get("branch") or {}).get("line"):
                print(earlier["branch"]["line"])
    return 0


def _bakeoff(home, root: Path, study, args: argparse.Namespace) -> int:
    """`study bakeoff SLUG --ref A --ref B`: the app's "Compare branches"; a refusal exits 2."""
    from evolvekit.app import jobs

    try:
        answer = jobs.start(home, root, bakeoff=list(args.refs))
    except AppError as exc:
        print(exc, file=sys.stderr)
        return 2
    run, count = answer["run"], len(args.refs)
    if answer.get("queued"):
        other = jobs._waiting_for(root) or "another study"  # noqa: SLF001 - the name the app's card shows
        document = {"run": run, "queued": True, "refs": list(args.refs)}
        line = (f"queued behind {other}; it starts when that study finishes "
                "(the app or `evolvekit study wait` starts it)")
    else:
        document = {"run": run, "started": True, "refs": list(args.refs)}
        line = f"started run {run}: {count} branch{'es' if count != 1 else ''} against {study.code.from_ref}"
    print(json.dumps(document) if args.json else line, flush=True)
    if args.wait:
        return _wait(home, args.slug, args.timeout, args.json, args)
    _say_next(args, f"study wait {args.slug}", f"study open {args.slug}")
    return 0


def cmd_study(args: argparse.Namespace) -> int:
    from evolvekit.harness import execute, library
    from evolvekit.harness.compile import write_run
    from evolvekit.harness.study import load_study, require_valid

    command = args.study_command
    if command == "new":
        if args.dir and args.warm:
            print("--warm reads the library home's finished studies; leave out --dir", file=sys.stderr)
            return 2
        if args.dir:
            folder = Path(args.dir)
            study = library.create_study(folder, _harness_folder(args.harness, args.home), args.name, args.template)
            if args.app:
                from evolvekit.app.store import set_application

                answer = set_application(folder, _full_path(args.app))
                if not answer["ok"]:
                    print(f"{answer['message']} (the study {folder} is made; say where the application is in "
                          "its study.yaml)", file=sys.stderr)
                    return 1
                _advance_step(folder, _STEP_AFTER_APPLICATION)
            if args.json:
                print(json.dumps({"slug": folder.name, "root": str(folder.resolve())}))
                return 0
            print(f"wrote {folder / 'study.yaml'} ({study.harness_id} {study.harness_version}"
                  + (f", template {study.template}" if study.template else "") + ")")
            print("next: put the cases in cases/, fill in study.yaml, then  python -m evolvekit study run "
                  f"{folder}")
            return 0
        home = _home(args)
        given = Path(args.harness)  # a harness folder on disk, else an id the home knows
        said: list[str] = []
        slug = home.create_study(args.harness, args.template, args.name, folder=given if given.is_dir() else None,
                                 warm=args.warm, said=said)
        for line in said:
            print(line, file=sys.stderr)
        answer = None
        if args.app:
            answer = home.set_application(slug, _full_path(args.app))
            if not answer["ok"]:
                print(f"{answer['message']} (the study {slug} is made; say where the application is with "
                      f"study application {slug} PATH)", file=sys.stderr)
                return 1
            _advance_step(home.study_root(slug), _STEP_AFTER_APPLICATION)
        if args.json:
            print(json.dumps({"slug": slug, "root": str(home.study_root(slug))}))
            return 0
        print(slug, flush=True)  # the result before the application's line and the hint on stderr
        if answer is not None:
            print(f"application: {answer['path']}" + (f" (version {answer['version']})" if answer.get("version") else ""),
                  file=sys.stderr)
        _say_next(args, f"study cases {slug} --add <files> --hold-back N")
        return 0
    if command in ("application", "code", "list", "check", "cases"):
        return _study_library_command(command, args)
    if command in ("start", "bakeoff", "stop", "status", "watch", "wait"):
        return _study_run_command(command, args)
    if command in ("verdict", "report"):
        return _verdict_command(command, args)
    if command in ("export", "pr"):
        return _change_command(command, args)
    if command == "open":
        return _study_open(args)
    if command == "groups":
        return _study_groups(args)
    folder = _study_root(args.directory, args)
    if command == "preview":
        result = execute.preview(folder)
        if not result["ok"]:
            print(f"the preview failed on {result['case']}: {result['error']}", file=sys.stderr)
            return 1
        print(f"preview on {result['case']}: {result['wall_s']:.1f} s")
        for name, value in sorted(result["kpis"].items()):
            print(f"  {name:<24} {value:.6g}")
        return 0
    study, harness = load_study(folder), execute.study_harness(folder)
    require_valid(study, harness, root=folder)
    models = _study_models(args, study, keys=command == "run")
    if command == "compile":
        plan = execute.plan_for(study, harness, execute.measured(folder), models=models)
        when = None
        if study.schedule is not None and not plan.blocked:  # it ends on its windows, not "done around 17:34"
            from evolvekit.app import scheduler

            # A library study's home keeps the rehearsal clock; a folder elsewhere reads the real one.
            clock = scheduler.clock_of(folder.parent.parent) if folder.parent.name == "studies" else scheduler.Clock()
            when = scheduler.plan_window(study.schedule, plan.hours, now=clock.now(), speed=clock.speed)["line"]
        if not args.run_id:
            # Nothing was asked to be kept: compile to prove it works, then leave no folder
            # in runs/ -- it holds only runs that were started (persona round 4, F2).
            scratch = f".compile-{os.getpid()}"
            runs_existed = (folder / "runs").is_dir()
            try:
                write_run(study, harness, plan, folder, scratch, models=models)
            finally:
                shutil.rmtree(folder / "runs" / scratch, ignore_errors=True)
                if not runs_existed:
                    try:
                        (folder / "runs").rmdir()
                    except OSError:
                        pass
            print(f"compiled, nothing written (give --run-id to keep the files): {plan.summary(when=when)}")
            if not Path(args.directory).is_dir():
                _say_next(args, f"study start {args.directory}")
            return 0
        run_dir = write_run(study, harness, plan, folder, args.run_id, models=models)
        print(f"compiled into {run_dir}: {plan.summary(when=when)}")
        if not Path(args.directory).is_dir():  # a name in the library home: start it as the skills say
            _say_next(args, f"study start {args.directory}")
            return 0
        print(f"run it with  python -m evolvekit run --config {run_dir / 'evolvekit.yaml'} --run-dir {run_dir}")
        return 0
    job = execute.run_study(folder, args.run_id, models=models)
    print(f"study run {job.get('run_id')}: {job['phase']}"
          + (f" -- {job.get('error')}" if job.get("error") else ""))
    final = job.get("final") or {}
    if final.get("comparison"):
        print(f"final check: {'confirmed' if final.get('confirmed') else 'not confirmed'}; see "
              f"{folder / 'runs' / job['run_id'] / final['comparison']}")
    elif final.get("skipped"):
        print(f"final check: {final['skipped']}")
    return 0 if job["phase"] in ("done", "stopped") else 1


def _study_groups(args: argparse.Namespace) -> int:
    """`study groups SLUG [--k N] [--json]`: the groups of the study's cases, into groups.json.
    Exit 1 when a case cannot be solved for its features (as `study preview`) or a run is going."""
    import functools

    from evolvekit.app import jobs
    from evolvekit.app.store import RUN_GOING
    from evolvekit.harness import groups

    if args.k is not None and not 2 <= args.k <= 5:
        print(f"--k: 2 to 5 groups, got {args.k}", file=sys.stderr)
        return 2
    root = _home(args).study_root(args.slug)
    if jobs.running(root):  # the first time solves every case: never beside a timed run
        print(RUN_GOING, file=sys.stderr)
        return 1

    def say(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    try:
        doc = groups.compute(root, k=args.k, tables=functools.partial(groups.case_tables, say=say))
    except groups.SolveFailed as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(doc))
        return 0

    def plural(n: int, word: str) -> str:
        return f"{n} {word}" + ("" if n == 1 else "s")

    count = len(doc["groups"])
    if doc["method"] == "features":
        print(f"{plural(count, 'group')} by the cases' features (silhouette {doc['silhouette']:.2f}):")
    elif doc["method"] == "tags":
        print(f"{plural(count, 'group')} by the cases' tags:")
    else:
        print("1 group:")
    for name, group in doc["groups"].items():
        training, test = len(group["training"]), len(group["test"])
        print(f"  {name}: {plural(training + test, 'case')} ({training} to learn from, {test} held back)")
    print(f"wrote {groups.GROUPS}", flush=True)
    for note in doc["notes"]:  # why it is one group, a feature that tells nothing apart
        print(f"note: {note}", file=sys.stderr)
    return 0


def _study_models(args: argparse.Namespace, study, *, keys: bool = False) -> dict | None:
    """The models a study with AI search help is compiled with: the ones
    `study start` takes from Settings in the library home; None without AI.
    With `keys` (`study run`, which searches in this process) the home's keys
    go into the environment, where the providers read them. No key for the
    chosen provider is refused in the words `study start` uses."""
    if not study.budget.ai_enabled:
        return None
    from evolvekit.app import jobs

    home = _home(args)
    models = jobs.ai_models(home)
    if models is None:
        raise HarnessError("budget.ai: AI search help is on, and there is no key for it: add one in Settings, "
                           "or turn AI help off")
    if keys:
        home.export_keys()
    return models


def cmd_doctor(args: argparse.Namespace) -> int:
    from evolvekit.doctor import checks
    from evolvekit.harness.library import default_home

    results = checks(Path(args.home or default_home()), app=_full_path(args.app) if args.app else None)
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for item in results:
            print(f"{'✓' if item['ok'] else '✗'} {item['name']}: {item['message']}")
            if not item["ok"]:
                print(f"    fix: {item['fix']}")
    return 0 if all(item["ok"] for item in results) else 1


def cmd_skill(args: argparse.Namespace) -> int:
    from evolvekit import skills

    if args.skill_command == "list":
        for name in skills.available():
            print(name)
        return 0
    target = Path(args.dir)
    result = skills.install(target, force=args.force)
    for verb, key, suffix in (("wrote", "written", ""), ("skipped", "skipped", " (exists)")):
        for path in result[key]:
            print(f"{verb} {path.relative_to(target).as_posix()}{suffix}")
    return 0


_COMMANDS = {
    "skill": cmd_skill,
    "doctor": cmd_doctor,
    "confirm": cmd_confirm,
    "export": cmd_export,
    "init": cmd_init,
    "preflight": cmd_preflight,
    "run": cmd_run,
    "status": cmd_status,
    "dashboard": cmd_dashboard,
    "leaderboard": cmd_leaderboard,
    "stop": cmd_stop,
    "harness": cmd_harness,
    "study": cmd_study,
    "app": cmd_app,
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
    written as escapes instead.

    A pipe is written as UTF-8: a tool (a coding agent) that reads the CLI
    then gets ✓ and · rather than escapes. A console keeps its own encoding
    (Python already writes UTF-8 to the Windows console)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if stream.isatty():
                reconfigure(errors="backslashreplace")
            else:  # one call: encoding alone would reset the errors to strict
                reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError, AttributeError):  # a stream that cannot be reconfigured is left alone
            pass


def main(argv: list[str] | None = None) -> int:
    _tolerant_output()
    args = build_parser().parse_args(argv)
    _load_env(args)
    try:
        return _COMMANDS[args.command](args)
    except AppError as exc:
        print(exc, file=sys.stderr)
        return 1
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except RunLockError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.6g}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
