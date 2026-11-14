# A3 What the Model Is Told Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The model sees what was already tried on the parent it is asked to improve — the last five children, what each changed (computed locally, never the model's own docstring) and what came of it, in words that are true of A2's search — and a plateau buys one strong-model big step per `big_step_every` generations, whose hint names its trigger and what the previous big step tried and scored.

**Architecture:** `deltas.change_summary` replaces `delta_summary`: a diff stat plus the names of the functions and constants that changed (AST, no docstring). A new pure module `evolvekit/search/tried.py` turns archive rows into `prompts.LedgerEntry` values (operator, change, outcome words) and picks a parent's last five children; the driver passes them through `run_operator` into `prompts.user_prompt` and `tree_prompts.tree_messages`, where "## What was tried on this parent" replaces the parent's failure section. `Driver._plan_operators` gains a spacing rule rebuilt on resume by the stop-policy replay that already exists; `prompts.big_step_reason` gains the previous big step's ledger line.

**Tech Stack:** Python 3.11 standard library (`ast`, `difflib`, `re`); pytest; ruff.

**Spec:** `docs/superpowers/specs/2026-11-08-algorithms-design.md` §6 (A3.1, A3.2), closing §2.3 Q8. Read it with §8–§10. The base this plan builds on: the A0 plan's Decisions 3 and 8 (`docs/superpowers/plans/2026-11-08-algo-0-correctness.md`: constants-only near gate; two big-step counts, tried vs applied), the A2 plan's Decisions (`docs/superpowers/plans/2026-11-10-algo-2-noise.md`: noise twins of an earlier, at-least-as-good candidate; race looks; promotion band; re-evaluation pools the best in place), and the build log `docs/superpowers/reviews/2026-11-08-algorithms-build-log.md`. §7 (A4) and programme B are out of scope.

**Base branch:** `algo/3-prompt`, cut from `algo/2-noise` at 1141625. The PR goes against `algo/2-noise` while A2's PR is open, retargeted to `master` once it merges (the stack rule of the memory: merge commits, in order).

```
git switch -c algo/3-prompt 1141625
```

## Global Constraints

- Windows first (the dev box); Linux in CI. `pathlib`; files `encoding="utf-8"`, LF endings.
- Stdlib only. No new dependency (§8.7).
- TDD: the failing test first, run it, watch it fail, then the code.
- Per-task test command (written `PYTEST <files>` below), from the worktree root in Git Bash: `PYTHONPATH="$PWD" /c/Users/norri/studio/evolvekit/.venv/Scripts/python.exe -m pytest <files> -q -m "not slow"`. Lint: `PYTHONPATH="$PWD" /c/Users/norri/studio/evolvekit/.venv/Scripts/python.exe -m ruff check --select F,E9 evolvekit tests`. Never bare `python`/`pytest`; never `python tasks.py check`, a benchmark or a demo study inside a task (the controller runs the gate once).
- **The ledger's bound** (§6 A3.1): at most **5** lines, newest first, each at most **160** characters, one physical line each. The prompt must not grow with the run.
- **One local summary.** Ledger lines, inspiration summaries and the meta-scratchpad's entries all use `deltas.change_summary`. No summary anywhere reads a docstring (`first_docstring` is deleted).
- **The system prompt does not change.** `prompts.system_prompt` and the tree system message stay byte-identical across a run (provider prefix caching); everything A3 adds is in the user message, after the scoring, notes and inspirations, before "## Your task".
- **No random draws.** The ledger and the hint read the archive only; they draw nothing from `self.rng`, so the run's random stream is unchanged for the same plan (`tests/test_resume_random_stream.py` keeps passing). Children are ordered by id (the run's counter), never by dict or set order.
- **Resume.** The ledger is a function of `self.archive` (rebuilt from `runs.jsonl`, re-evaluations replayed by `_replayed_rows`) — nothing new is persisted. The spacing state is rebuilt from rows by `_restore_stop_policy`'s replay. No new run file, no new row field; status and verdict `SCHEMA` unchanged.
- **Two big-step counts stay apart** (A0 decision 8). Spacing starts only on an **applied** big step (`outcome.ok`; on replay, every big-step row except the failed-apply shape: `rejected`, `novelty is None`, block equal to its parent's).
- **Words.** Prompts are read by the model, not by people: they keep the engine's words the system prompt already uses (generation, instance, gate, hold-out, KPI, parent). This supersedes, for prompts only, the A2 Global Constraints line that listed prompts among the places for people's words (open question 6). Docs for people (`docs/reference.md` keeps engine words where it already uses them). Chances in percent if any are quoted. No vendor or model names in code, prompts, docs or tests.
- **Nothing claims "better".** The ledger reports signed score changes and the engine's own outcomes; it never says "improved", "better" or "worse".
- Write files with the Write/Edit tools only (no heredocs, no PowerShell `Set-Content`).
- Every commit message ends with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commit on `algo/3-prompt`; never push from a task.
- Docs ship in the commit of the behaviour (§10). The design's "README" rows are `docs/reference.md` "Operators" and "The meta-scratchpad" since #77.

## Review Focus

1. **A child whose failure is enormous or hostile** — a 5,000-character single-line stderr, a traceback, backticks, a lone surrogate. Its ledger line is one line of at most 160 characters, the outcome survives truncation before the change does, and the tree prompt still passes `for_model`. Pinned in A3.1 (`test_a_huge_or_multiline_failure_is_one_bounded_line`).
2. **A resumed run.** The ledger after `resume()` equals the ledger the uninterrupted run would show, and the big steps fall on the same generations. Pinned in A3.1 (`test_the_ledger_is_rebuilt_from_runs_jsonl`) and A3.2 (`test_the_spacing_is_rebuilt_on_resume`).
3. **A parent whose fitness moved** (re-evaluated and pooled, A2.4b) or is unknown. Deltas are against the parent's fitness as it stands; with no parent fitness the line shows the child's score. Pinned in A3.1 (`test_a_parent_without_a_fitness_shows_the_child_s_score`).
4. **Rows of every age.** A row from before A2 (no `behaviour_values`, no `holdout_margin`), a raced-out reason in either wording, a daily-cap skip. Each maps to words without raising. Pinned in A3.1 (`test_every_row_shape_has_its_words`, the parametrised cases).
5. **Blocks the AST cannot read** — a syntax error, a tree candidate's diff, a typed space's rendered `configure()`. The summary degrades to the diff stat (tree: files), never raises. Pinned in A3.1 (`test_an_unparseable_block_is_summarised_by_its_diff_stat`, `test_a_tree_candidate_is_summarised_by_files`, `test_typed_parameters_name_the_keys_that_changed`).

---

### Task A3.1: A per-parent ledger, and one local change summary

**Files:**
- Modify: `evolvekit/deltas.py` (module docstring; `change_summary` replaces `delta_summary`; `first_docstring` deleted; `changed_names`, `failure_line` added)
- Create: `evolvekit/search/tried.py`
- Modify: `evolvekit/prompts.py` (module docstring; `LedgerEntry`, `LEDGER_LINES`, `LEDGER_LINE_LIMIT`, `_ledger_section`; `user_prompt` and `build_messages` take `ledger`; the "Last failure artefact" section removed; `FAILURE_LIMIT` stays — `agent_patch` and `Driver._failures` use it)
- Modify: `evolvekit/tree_prompts.py` `tree_messages` (takes `ledger`; its failure section removed the same way)
- Modify: `evolvekit/search/operators.py` `run_operator`, `_on_tree` (pass `ledger` through)
- Modify: `evolvekit/search/driver.py`: `_summary_of` uses `change_summary`; `_parent_ledger` new; `_propose`/`_invoke` pass it; `_parent_notes` and its use in `_compose_hint` / `_note_behavioural_twin` removed (open question 7; `_dead_ends` stays)
- Test: `tests/test_parent_ledger.py` (new), `tests/test_scratchpad.py` (the delta-summary tests rewritten), `tests/test_diff_prompts.py` (the tree prompt carries the bounded ledger), `tests/test_feedback.py` (unchanged; must pass)

**Interfaces:**
- Produces, in `deltas.py`:
  - `SUMMARY_LIMIT = 160` (kept); `NAMES_SHOWN = 4`.
  - `changed_names(old: str, new: str) -> tuple[list[str], list[str]] | None` — `(functions, constants)` or `None` when either side does not parse. Functions: top-level `def`/`async def`, and methods as `Class.method`, whose `ast.dump` differs, in the child's order, then added ones as `+name` and removed ones as `-name` (parent order); a nested def rolls up into its outer function. Constants: block-level `NAME = <expr>` / `NAME: T = <expr>` with a single `Name` target whose value is a literal (`ast.literal_eval` succeeds, negatives included) and differs, or is added/removed (same `+`/`-` marks).
  - `change_summary(block: str, parent_block: str | None, parent_id: str | None = None, *, params: dict | None = None, parent_params: dict | None = None, tree: bool = False) -> str`. Exact shapes (`vs <id>` only when `parent_id` is given):
    - no parent block: `"no recorded change"`
    - `tree=True`: `"+a/-r diff lines vs <id>; files: a.py, b.py"` — files are the `+++ b/<path>` sections whose text differs between the two diffs; no AST.
    - unparseable side: `"+a/-r lines vs <id>"`
    - same AST: `"+a/-r lines vs <id>; comments or layout only"`
    - otherwise `"+a/-r lines vs <id>; functions: f, g; constants: A, B"`, each part only when non-empty, each list cut after `NAMES_SHOWN` with `" and N more"`; with `params` and `parent_params` both dicts, `constants:` names the keys whose values differ (child's key order) instead of block constants; if both lists are empty: `"; other top-level code"`.
    - cut to `SUMMARY_LIMIT` with a trailing `"..."` (ASCII).
  - `failure_line(last_failure: str) -> str` — from a `last_failure` artefact (`"stage <id>: <failure>\n<stderr tail>"`, `cascade._artefact`): the **last** non-empty line of the stderr part, stripped (a Python traceback's exception line; open question 2); when there is no stderr part, the text after `"stage <id>: "` on the first line; backticks replaced by `'`; never contains a newline.
- Produces, in `prompts.py`:
  - `LEDGER_LINES = 5`, `LEDGER_LINE_LIMIT = 160`.
  - `@dataclass(frozen=True) class LedgerEntry: id: str; operator: str; change: str | None; outcome: str` with `line(self) -> str`: `` f"`{id}` {operator}: {change} -> {outcome}" `` (or `` f"`{id}` {operator} -> {outcome}" `` when `change is None`), at most `LEDGER_LINE_LIMIT` characters: when too long the change is cut first (to `"..."`), then the outcome; whitespace runs collapse to one space.
  - `user_prompt(..., ledger: Sequence[LedgerEntry] = ())` and `build_messages(..., ledger=())`. The section, placed where the failure section was (after the scratchpad, before "## Your task"), empty → nothing at all:

```
## What was tried on this parent
Newest first: child, operator, what it changed -> what came of it (score change against this parent).
- `g003-c0012` diff: +6/-2 lines; functions: priority -> +0.21 (now the best)
- ...
```

  - `tree_messages(..., ledger: Sequence[LedgerEntry] = ())` renders the same section (`prompts._ledger_section`, imported), inside `for_model`.
  - `run_operator(..., ledger: Sequence[LedgerEntry] = ())` and `_on_tree(..., ledger)` pass it on.
- Produces, in `evolvekit/search/tried.py` (imports `Candidate`, `deltas`, `prompts.LedgerEntry`, `re`; nothing from the driver):
  - `outcome_words(child: Candidate, parent: Candidate | None, *, best_id: str | None, twin: Candidate | None, final_stage: str) -> str` — first match wins:

| # | Row shape (fields) | Words |
|---|---|---|
| 1 | failed apply: `rejected`, `novelty is None`, `behaviour_twin_id is None`, `parent` given and `child.block == parent.block` | `` not applied: `<first line of reject_reason>` `` |
| 2 | `novelty == "no_op"` | `rejected: no change from this parent` |
| 3 | `novelty == "duplicate"` | `rejected: same code as <twin_id>` (`an earlier candidate` without one) |
| 4 | `behaviour_twin_id`; exact (the twin's `behaviour_signatures[stage]` equals the child's at `stage = child.stages_reached[-1]`) | `same behaviour as <id>` |
| 5 | `behaviour_twin_id`; otherwise (A2's noise rule: an earlier candidate that scored at least as well) | `same behaviour as <id>, within the noise` |
| 6 | `gated` | `` broke the gate `<gated>` `` (the text already ends `on the hold-out` when it was the hold-out) |
| 7 | `raced_out` | `raced out after <N> instances`, N from `r"raced out after (\d+)"`; `raced out` when it does not match |
| 8 | `veto` | `kept out by the hold-out` |
| 9 | `last_failure` whose first line contains `daily full-evaluation cap reached` | `not evaluated: the daily cap on final evaluations was reached` |
| 10 | `last_failure` | `` failed: `<failure_line(last_failure)>` `` |
| 11 | `competes is False` and `final_stage not in stages_reached` | `not promoted` |
| 12 | `child.fitness` and `parent.fitness` known | `f"{child.fitness - parent.fitness:+.4g}"`, then `" (now the best)"` when `child.id == best_id`, then `", close to <near_twin_id>"` when `novelty == "near"` |
| 13 | `child.fitness` known, parent's not | `f"score {child.fitness:.4g}"` with the same two suffixes |
| 14 | `rejected` with a `reject_reason` | `` rejected: `<first line>` `` |
| 15 | anything else | `no score` |

  - `entry_for(child: Candidate, parent: Candidate | None, *, best_id: str | None, twin: Candidate | None, final_stage: str, tree: bool) -> LedgerEntry` — `change` is `None` for row shape 1 (nothing applied), else `change_summary(child.block, parent.block if parent else None, None, params=child.params, parent_params=parent.params if parent else None, tree=tree)`; `operator` is `child.operator`.
  - `parent_ledger(archive: Sequence[Candidate], parent: Candidate, *, by_id: Mapping[str, Candidate], best_id: str | None, final_stage: str, tree: bool, limit: int = LEDGER_LINES) -> tuple[LedgerEntry, ...]` — the rows with `parent_id == parent.id`, sorted by id descending, the first `limit`.
- Produces, in the driver: `Driver._parent_ledger(self, parent: Candidate) -> tuple[LedgerEntry, ...]` (`best_id = self.best.id if self.best else None`, `final_stage = self.config.final_stage.id`, `tree = self.repo is not None`), computed once in `_propose` and passed to both `_invoke` calls (first attempt and re-prompt); `_invoke(..., ledger)` passes it to `run_operator`. Model-free operators ignore it. `_summary_of` calls `change_summary(c.block, parent.block if parent else None, c.parent_id, params=c.params, parent_params=parent.params if parent else None, tree=self.repo is not None)` — used by `_inspirations` and the scratchpad entries alike.

- [ ] **Step 1: Write the failing tests for the summary** (`tests/test_scratchpad.py`, replacing the four delta-summary tests):

```python
from evolvekit.deltas import change_summary, changed_names, diff_stat, failure_line

PARENT = 'BONUS = 20\ndef priority(item, bins):\n    """Old."""\n    return [b - item for b in bins]\ndef helper():\n    return 1\n'
CHILD = 'BONUS = 25\ndef priority(item, bins):\n    """A brand new principle."""\n    return [item - b for b in bins]\ndef helper():\n    return 1\n'

def test_the_change_summary_names_changed_functions_and_constants_and_no_docstring():
    assert diff_stat(PARENT, CHILD) == (3, 3)
    assert changed_names(PARENT, CHILD) == (["priority"], ["BONUS"])
    summary = change_summary(CHILD, PARENT, "g001-c0002")
    assert summary == "+3/-3 lines vs g001-c0002; functions: priority; constants: BONUS"
    assert "brand new" not in summary

def test_added_and_removed_names_are_marked():
    child = PARENT.replace("def helper():\n    return 1\n", "def other():\n    return 2\nWIDTH = 4\n")
    assert changed_names(PARENT, child) == (["+other", "-helper"], ["+WIDTH"])

def test_a_comment_only_change_says_so():
    assert change_summary(PARENT + "# note\n", PARENT) == "+1/-0 lines; comments or layout only"

def test_a_summary_without_a_parent_still_says_something():
    assert change_summary("x = 1\n", None) == "no recorded change"

def test_an_unparseable_block_is_summarised_by_its_diff_stat():
    assert change_summary("def f(:\n", "def f():\n    return 1\n", "p") == "+1/-2 lines vs p"
    assert changed_names("def f(:\n", PARENT) is None

def test_typed_parameters_name_the_keys_that_changed():
    s = change_summary("def configure():\n    return {'lr': 0.2}\n", "def configure():\n    return {'lr': 0.1}\n",
                       params={"lr": 0.2, "k": 3}, parent_params={"lr": 0.1, "k": 3})
    assert s.endswith("functions: configure; constants: lr")

def test_summaries_are_bounded():
    many = "".join(f"def f{i}():\n    return {i}\n" for i in range(60))
    other = many.replace("return", "return 1 +")
    s = change_summary(other, many, "p")
    assert len(s) <= 160 and "and 56 more" in s

def test_the_failure_line_is_the_exception_not_the_traceback_header():
    artefact = "stage full: exit code 1\nTraceback (most recent call last):\n  File \"x.py\", line 3\nZeroDivisionError: division by `zero`\n"
    assert failure_line(artefact) == "ZeroDivisionError: division by 'zero'"
    assert failure_line("stage static: forbidden import os") == "forbidden import os"
```

- [ ] **Step 2: Write the failing tests for the outcome words and the ledger** (`tests/test_parent_ledger.py`). A helper `_row(**fields) -> Candidate` builds a recorded row (`id`, `generation`, `block`, `source=""`, `operator="diff"`, `parent_id="g001-c0001"`, `stages_reached=["proxy", "full"]`, `competes=True` by default); `PARENT_ROW` has fitness 1.0. Tests:
  - `test_every_row_shape_has_its_words` — parametrised over the table's 15 shapes, one case each, asserting the exact words: `+0.21 (now the best)` (child 1.21, `best_id` = child), `-0.04`, `+0.02, close to g001-c0003`, `` failed: `ZeroDivisionError: division by zero` ``, `same behaviour as g002-c0009` (signatures equal), `same behaviour as g002-c0009, within the noise` (signatures differ), `` broke the gate `missed_required = 2 > 0` ``, `raced out after 4 instances` (reason `"raced out after 4 of 20 instances: 7.10 % behind ..."`), `raced out after 3 instances` (the older wording `"raced out after 3 instances: ..."`), `kept out by the hold-out`, `not promoted` (`stages_reached=["proxy"]`, `competes=False`), `` not applied: `could not apply response: SEARCH text not found` ``, `rejected: no change from this parent`, `rejected: same code as g001-c0004`, `not evaluated: the daily cap on final evaluations was reached`, and a pre-A2 row (no `behaviour_values`, `holdout_margin=None`) that competes.
  - `test_a_parent_without_a_fitness_shows_the_child_s_score` — parent `score=None` → `score 1.21`.
  - `test_the_ledger_is_the_last_five_children_newest_first` — eight children `g00k-c00k` of one parent and two of another: `[e.id for e in parent_ledger(...)]` is the five highest ids of that parent, descending.
  - `test_a_huge_or_multiline_failure_is_one_bounded_line` — `last_failure = "stage full: exit code 1\n" + "x" * 5000`, and one with `"\n"` and backticks: `len(entry.line()) <= 160`, `"\n" not in entry.line()`, and the line ends with `` ...` `` or the outcome's head (`"failed: `"` is in it: the change was cut first).
  - `test_the_prompt_carries_the_bounded_ledger_in_place_of_the_failure_section` — `user_prompt(parent, None, "diff", ledger=entries_of_20_children[:5])`: `"## What was tried on this parent"` precedes `"## Your task"`; the section has exactly 5 lines starting `"- "`, each ≤ 160; `"## Last failure artefact"` is absent even when `parent.last_failure` is set; with `ledger=()` the heading is absent; `system_prompt` is the same string whatever the ledger.
  - `test_the_ledger_is_rebuilt_from_runs_jsonl` — run the `tests/test_sweep.py` `_driver` shape (EXAMPLE_CONFIG, FakeProvider) for 3 generations, take `driver._parent_ledger(p)` for every parent in the archive; a fresh driver on the same run dir after `resume()` gives equal tuples.
  - `test_the_next_prompt_shows_what_the_parent_s_earlier_children_did` — a driver run (the `tests/test_sweep.py` `_driver` shape) of 2 generations with `children_per_generation=1`, `operators={"diff": 1.0}`, `big_step_every=0` (no big step takes the slot), `inspirations=0`; responses: a reply that does not apply, then one that does: the second call's user message contains `"## What was tried on this parent"` and `"not applied:"` with the first child's id.
- [ ] **Step 3: Write the failing tree test** (`tests/test_diff_prompts.py`): `test_the_tree_prompt_carries_the_bounded_ledger` — `run_operator("diff", ..., tree=ctx, ledger=<six entries, one with a 5,000-character outcome>)` through the file's `_run` shape: the last message holds the heading, at most 5 `"- "` lines under it, each ≤ 160, and no `"## Last failure artefact"`.
- [ ] **Step 4: Run them to watch them fail**

Run: `PYTEST tests/test_scratchpad.py tests/test_parent_ledger.py tests/test_diff_prompts.py`
Expected: FAIL (ImportError on `change_summary`, `tried`, `LedgerEntry`).

- [ ] **Step 5: Implement `deltas.py`** (`changed_names`, `change_summary`, `failure_line`; delete `first_docstring` and `delta_summary`; rewrite the module docstring: two local facts — the diff stat and the changed names — and why the docstring is no longer one: a model's docstring can describe the rule it meant, not the one it wrote).
- [ ] **Step 6: Implement `prompts.LedgerEntry`, `_ledger_section`, the `ledger` parameter of `user_prompt` / `build_messages`, and remove the failure section** (and update the module docstring's "last failure" line to the ledger).
- [ ] **Step 7: Implement `tried.py`** (the table above, in that order).
- [ ] **Step 8: Wire it**: `tree_messages`, `run_operator`, `_on_tree`, `Driver._summary_of`, `Driver._parent_ledger`, `_propose` → `_invoke`; remove `_parent_notes` (the ledger now carries "same behaviour as <id>" to the parent's next prompt, and it survives a resume, which the note did not).
- [ ] **Step 9: Run the tests to watch them pass**

Run: `PYTEST tests/test_scratchpad.py tests/test_parent_ledger.py tests/test_diff_prompts.py tests/test_feedback.py tests/test_sweep.py tests/test_driver_tree.py tests/test_zero_llm.py tests/test_problem_context.py tests/test_end_to_end.py tests/test_resume_random_stream.py`
Expected: PASS. Then the whole fast suite once (`PYTEST tests`): a test that asserted a docstring in an inspiration summary or the old failure heading is updated to the new text, not deleted. Lint clean.

- [ ] **Step 10: Docs** (`docs/reference.md`):
  - "Operators" table: `diff` row "the parent block, its score breakdown, change summaries of 1 to `search.inspirations` (default 2) inspirations, and what was tried on this parent".
  - The inspirations paragraph: "a unified-diff stat against their own parent plus the names of the functions and constants they changed, computed locally — never the model's own docstring, which can describe the rule it meant rather than the one it wrote. On a tree problem, the files the diff touched."
  - A new paragraph after it, **What was tried on this parent**: the last five children of the parent, newest first, one line each (operator, change summary, what came of it), at most 160 characters a line, so the prompt does not grow with the run; the outcome words listed (a signed score change against the parent, "now the best", "failed: …" with the error's last line, "same behaviour as …" — "within the noise" on a noisy stage, where the twin scored at least as well — "broke the gate …", "raced out after N instances", "kept out by the hold-out", "not promoted", "not applied", "rejected: …"); it replaces the parent's failure section (a parent always finished, so that section was empty); rebuilt from `runs.jsonl` on resume.
  - "The meta-scratchpad": "the current elites' change summaries and score lines".
- [ ] **Step 11: Commit**

```bash
git add evolvekit/deltas.py evolvekit/search/tried.py evolvekit/prompts.py evolvekit/tree_prompts.py evolvekit/search/operators.py evolvekit/search/driver.py tests/test_scratchpad.py tests/test_parent_ledger.py tests/test_diff_prompts.py docs/reference.md
git commit -m "feat(prompts): a per-parent ledger and one local change summary (Q8, A3.1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A3.2: Big steps with spacing and a true hint

**Files:**
- Modify: `evolvekit/search/driver.py`: `_plan_operators`, `_breed_one` (the applied branch), `_restore_stop_policy`, `__init__` (two attributes), `_previous_big_step`
- Modify: `evolvekit/prompts.py`: `big_step_reason`
- Test: `tests/test_sweep.py` (where the A0.6b big-step tests live); `tests/test_driver_tree.py`, `tests/test_zero_llm.py`, `tests/test_economics.py`, `tests/test_budget_ledger.py` must keep passing unchanged.

**Interfaces:**
- Consumes (A3.1): `tried.entry_for`, `LedgerEntry.line()`.
- Produces, in the driver:
  - `self._plateau_step_at: int | None = None` — the generation of the last **applied** big step that was planned while the plateau held (whichever trigger fired: a scheduled step on a plateau starts the window too; open question 3).
  - `self._big_step_on_plateau: bool = False` — set by `_plan_operators` for this generation's big step.
  - `_plateau_step_allowed(self, generation: int) -> bool`: `True` when `self._plateau_step_at is None`, or `generation - self._plateau_step_at >= self.config.search.big_step_every`, or something improved since it (`generation - 1 - self.stop_policy.stagnant >= self._plateau_step_at`: the last improving generation is at or after it), or the plateau is **held open** (`stagnant >= stop.patience` and `big_steps_in_window < stop.min_big_steps`: the run cannot stop until a big step is tried, so it is not made to wait; open question 4).
  - `_plan_operators(generation, plateau)` keeps its signature and its draws: `scheduled` as today; a big step when `takes_big_steps and (scheduled or (plateau and self._plateau_step_allowed(generation)))`; `self._big_step_on_plateau = plateau`; `note_big_step()` as today; reason from `big_step_reason(..., previous=self._previous_big_step())`.
  - `_breed_one`: where `note_big_step_applied()` is called, also `if self._big_step_on_plateau: self._plateau_step_at = generation`.
  - `_restore_stop_policy`: in its replay loop, for a generation in `applied`, **before** `update(...)` for that point, `if self.stop_policy.plateau: self._plateau_step_at = point.generation` — the policy then stands exactly where it stood when that generation was planned (live, `decision.plateau` comes from the previous update), so no event or row field is needed.
  - `_previous_big_step(self) -> LedgerEntry | None`: the archive row with the highest id whose operator starts with `big_step` (applied or not), as `tried.entry_for(row, self.by_id.get(row.parent_id), best_id=..., twin=..., final_stage=..., tree=...)`.
- Produces, in `prompts.py`: `big_step_reason(*, scheduled: bool, every: int, stagnant: int, previous: LedgerEntry | None = None) -> str` — today's two sentences unchanged when `previous is None` (the A0.6b tests pin them); otherwise the trigger sentence + `f" The previous big step: {previous.line()}."`.

- [ ] **Step 1: Write the failing tests** (`tests/test_sweep.py`, with the file's `_driver` helper; patience and `min_big_steps` set through `replace(config.stop, ...)` — add a `stop=` keyword to the helper):

```python
def _applied(driver, generation):  # what `_breed_one` does when a big step applies
    driver.stop_policy.note_big_step_applied()
    if driver._big_step_on_plateau:
        driver._plateau_step_at = generation

def test_a_plateau_takes_one_big_step_then_waits_big_step_every_generations(tmp_path):
    driver, _ = _driver(tmp_path, big_step_every=4, children_per_generation=2)   # patience 4 via stop=
    taken = []
    for g, stagnant in zip(range(5, 14), range(2, 11)):
        driver.stop_policy.stagnant = stagnant
        if driver._plan_operators(g, plateau=True)[0] == "big_step":
            taken.append(g); _applied(driver, g)
    assert taken == [5, 8, 12], "a scheduled step on the plateau restarts the window"

def test_an_improvement_since_the_last_big_step_lifts_the_spacing(tmp_path):
    # big_step_every=99, patience 2: plateau at stagnant >= 1
    # g5 big step applied; g7 planned with stagnant=1 (g6 flat, g5 improved) -> big step again
    ...

def test_a_big_step_that_did_not_apply_does_not_start_the_spacing(tmp_path):
    # g5 planned (plateau), not applied; g6 on the plateau -> big step again
    ...

def test_a_held_plateau_is_not_kept_waiting(tmp_path):
    # patience 4, min_big_steps 2, stagnant 6, one big step tried in the window, applied at g5:
    # g6 still takes a big step (the run cannot stop before the second)
    ...

def test_the_big_step_hint_names_the_previous_big_step_and_its_outcome(tmp_path):
    # archive holds a big-step row g004-c0013 (operator "big_step/rewrite", parent the seed, fitness seed - 0.04)
    driver._plan_operators(9, plateau=True)
    assert driver._big_step_reason.startswith("This big step is due because the search has not improved for")
    assert "The previous big step: `g004-c0013` big_step/rewrite: +" in driver._big_step_reason
    assert driver._big_step_reason.endswith("-> -0.04.")
```

  and `test_the_spacing_is_rebuilt_on_resume`: two drivers on the EXAMPLE_CONFIG with `big_step_every=3`, `stop` patience 4, `generations=10`, responses that always apply and never beat the seed (the file's `_WORST_FIT` and variants with distinct constants, cycled): one runs 10 generations; the other runs 5, then a fresh driver resumes the same dir for the rest. The generations of `big_step*` rows are equal, and `_plateau_step_at` is equal at the end. Mark it `slow` if it takes over 10 s.
- [ ] **Step 2: Run them to watch them fail**

Run: `PYTEST tests/test_sweep.py`
Expected: FAIL (`_big_step_on_plateau` missing; today every plateau generation takes a big step).

- [ ] **Step 3: Implement** the attributes, `_plateau_step_allowed`, `_plan_operators`, the `_breed_one` line, the replay line in `_restore_stop_policy`, `_previous_big_step`, and `big_step_reason(previous=...)`. The `_plan_operators` docstring states the rule in one sentence (Q8: "on a plateau every generation spent a strong-model call").
- [ ] **Step 4: Run the tests to watch them pass**

Run: `PYTEST tests/test_sweep.py tests/test_driver_tree.py tests/test_zero_llm.py tests/test_economics.py tests/test_budget_ledger.py tests/test_params.py tests/test_resume_random_stream.py tests/test_parent_ledger.py`
Expected: PASS (no model: no big steps, so the zero-LLM tests are untouched). Then `PYTEST tests` once; a test that counted one big step per plateau generation is updated to the spaced count with a comment naming A3.2. Lint clean.

- [ ] **Step 5: Docs** (`docs/reference.md`): the "Operators" table's `big_step` row: "scheduled every `big_step_every` generations, or on plateau — then no plateau big step for `big_step_every` generations unless the search improved"; a short paragraph under it: the spacing (counted from the last big step that applied while the plateau held; a held plateau, `stop.min_big_steps`, is not made to wait), and the hint (it names its trigger and the previous big step's line from the ledger). Check the economics paragraphs near "A plateau's first answer is the strong model" still read true (they do: at least one big step still applies first).
- [ ] **Step 6: Commit**

```bash
git add evolvekit/search/driver.py evolvekit/prompts.py tests/test_sweep.py docs/reference.md
git commit -m "feat(search): one plateau big step per big_step_every generations, with a true hint (Q8, A3.2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A3.3: The gate, the build log, the PR (controller)

**Files:**
- Modify: `docs/superpowers/reviews/2026-11-08-algorithms-build-log.md` (the A3 entry)

- [ ] **Step 1: Docs sweep.** `grep -rn "docstring\|delta summar\|delta_summary\|Last failure" docs evolvekit/skills README.md`: every hit that describes a summary or the failure section is true of A3 (the near-gate and canonicalisation paragraphs that mention docstrings for other reasons stay).
- [ ] **Step 2: The gate**, with nothing else running: `PYTHONPATH="$PWD" /c/Users/norri/studio/evolvekit/.venv/Scripts/python.exe tasks.py check`. Expected: lint clean, every test passes.
- [ ] **Step 3: The build log's A3 entry**: tasks, review rounds, commits; the decisions this plan's builder check ruled; limits logged.
- [ ] **Step 4: Commit, push, PR**

```bash
git add docs/superpowers/reviews/2026-11-08-algorithms-build-log.md
git commit -m "docs(a3): the build log's A3 entry

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin algo/3-prompt
gh pr create --base algo/2-noise --title "A3: what the model is told (Q8)" --body "<the ledger and its bound, the outcome words, the spacing rule, the hint; each with its test>

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Decisions

Taken by the controller after the builder check (a fresh reviewer read §6 and this plan against
the code, read-only; its report is kept with the build log's A3 entry). These override any task
text above that disagrees.

1. **The PR goes against `master`**, stacked on A2's PR (#83, `algo/2-noise`) until that merges; the
   body says so (the session prompt's rule). Supersedes the header's "against `algo/2-noise`".
2. **Open questions 1–11 and 13–15 as proposed**, with the builder check's notes below.
3. **Q12, refined:** keep block-level literal constants; also mark a function whose AST with every
   constant blanked equals the parent's as `f (numbers only)` (A0 decision 3's own definition).
4. **The resume test of A3.2 must exercise the spacing**: e.g. patience 13, 12 generations,
   `big_step_every` 3, split at generation 8, with replies that never beat the seed; assert the
   resumed run plans the same big steps as the uninterrupted one.
5. **Adopted generations** (`pending.json`, `_adopt_pending`) count their big steps: one helper,
   used by the live path, the replay and adoption, updates the tried count, the applied count and
   `_plateau_step_at` — fixing A0's adoption gap for all three counts.
6. **The plateau is recorded** in the `generation_started` event (`plateau: bool`), and the replay
   reads it when present (exact on a noisy run whose noise moved since); legacy events fall back to
   the stop-policy replay. Documented in the events table.
7. **Bounds:** the 160-character limit applies to the whole printed line *including* its `"- "`
   bullet; all whitespace including U+2028, U+2029 and `\x85` collapses to one space; children are
   ordered by their integer counter, not the id string; a cut failure keeps its closing backtick.
8. **Words:** "not applied" only for errors starting `could not apply response`; other errors
   (provider, tree checkout, `param_lhs` found nothing) read "no reply: <first line>"; a duplicate
   reads "same program as <id>"; a near child whose twin is its own parent reads "close to this
   parent"; rows 12–13 (score changes) only for rows that compete; a finished row that does not
   compete reads "not ranked (score X)". Score changes use the breakdown's `.6g`-style formatting
   without exponents for ordinary magnitudes (implementer: one helper, tested on 1e-05 and 1234.5).
   `failure_line` is the last non-empty line of the whole failure text.
9. **Tree summaries** are keyed on `diff --git` lines (deletions and renames counted right).
10. **The ledger is built only when the operator calls a model** (model-free operators never read it).
11. **Prompts are for the model and keep the engine's words** (Q6) — the vocabulary rule binds
    people's text (app, `study` CLI, docs for people), not prompts.

## Open questions for the builder check

1. **The design's test files are the tree tests.** `tests/test_diff_prompts.py` and `tests/test_driver_tree.py` cover tree problems; no file tests `prompts.user_prompt`'s shape except `test_feedback.py`. *Proposed (in the plan):* the bound is asserted for both prompt builders — the skeleton prompt in the new `tests/test_parent_ledger.py`, the tree prompt in `tests/test_diff_prompts.py`; `test_driver_tree.py` and `test_zero_llm.py` keep passing unchanged.
2. **"The first line of stderr" is usually useless.** For a Python solver it is `Traceback (most recent call last):`; the artefact's first line is `stage <id>: exit code 1`; and the artefact holds the stderr's *tail* (2,000 characters), whose first line may be cut. *Proposed (in the plan):* `failure_line` takes the last non-empty stderr line (the exception), falling back to the stage's failure text.
3. **Which big steps start the spacing.** Read literally, only a plateau-*triggered* one does; then, with a schedule, a long plateau takes two big steps per `big_step_every` generations (a plateau step, the scheduled one, a plateau step right after it…). *Proposed (in the plan):* any applied big step planned while the plateau holds starts the window, so a long plateau settles at the schedule's one per window; a scheduled step off the plateau does not. Alternative: the literal rule.
4. **Spacing against `stop.min_big_steps`.** A plateau held open by `min_big_steps` (patience spent, fewer big steps tried) would otherwise wait up to `big_step_every` generations of small-model children it has already judged flat. *Proposed (in the plan):* a held plateau is not made to wait.
5. **Spacing counts applied big steps** (A0 decision 8, as the brief says). A big step whose reply never applies does not start the window, so a strong model that keeps failing to produce a block is asked every plateau generation. *Proposed:* accept — the provider-dead abort and `budget` bound it, and the hint does not lie about a step that did nothing.
6. **Which words a prompt uses.** A2's Global Constraints listed "prompts" among the places for people's words (case, round, measure…), but the system prompt says "Objective KPI", "generation", and the design's own outcome words say "instances" and "gate". *Proposed (in the plan):* prompts are for the model and keep the engine's words, consistently with the system prompt; the people-vocabulary rule stays for the app, the `study` CLI and the docs' people sections.
7. **`_parent_notes`.** The per-parent "previous child produced identical behaviour" note is popped on use and lost on resume; the ledger carries the same fact for the last five children and survives resume. *Proposed (in the plan):* remove the note; keep `_dead_ends` (run-wide twins, every lineage), which the ledger does not replace.
8. **Tree problems.** The design names `user_prompt` only; a tree's block is a cumulative diff against the base, so AST names do not apply. *Proposed (in the plan):* the ledger and the failure-section removal apply to `tree_messages` too; a tree summary is the diff-of-diffs stat plus the files whose sections changed; the run-wide refusals section stays.
9. **"Now the best".** Live state at prompt time (the run's current best, pooled after a re-evaluation), or "set a new best when recorded"? *Proposed (in the plan):* current — it is what the model can act on, and it rebuilds identically on resume.
10. **A delta inside the noise.** "+0.01" on a stage whose noise is 0.5 reads as progress. *Proposed:* show the bare signed number (no "better" is claimed, and the noise twin rule already rejects what behaves alike); alternative: append ", within the noise" when |Δ| < `Noise.score_sd`·√2 — cheap, but a second noise rule in a prompt.
11. **A daily-cap skip looks like a failure.** The row carries `last_failure = "stage <final>: daily full-evaluation cap reached"`. *Proposed (in the plan):* its own words, "not evaluated: the daily cap on final evaluations was reached" — the model did nothing wrong.
12. **What a "changed constant" is.** *Proposed (in the plan):* block-level `NAME = <literal>` only (a number changed inside a function shows as that function changed); on a typed space, the `params` keys whose values differ.
13. **The design's outcome list is not exhaustive.** It omits failed applies, novelty rejects, near-duplicates and the noise twin's meaning since A2. *Proposed (in the plan):* the 15-row table in A3.1, first match wins; a near-duplicate is evaluated, so it gets its score words with ", close to <id>".
14. **The current generation's siblings.** A child is recorded only after its generation is evaluated, so the ledger never shows a sibling bred earlier in the same generation. *Proposed:* accept (it keeps the ledger order-free under parallel breeding, and is what resume rebuilds).
15. **Which "previous big step" the hint names.** *Proposed (in the plan):* the newest big-step row in the archive, applied or not, even if it came before the last improvement — the model is told what the strong model last tried and what came of it, nothing more.
