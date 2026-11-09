# A0 Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the six defects D1–D6 of the algorithms review: a novelty hash that keeps free names, a hold-out that vetoes instead of training, one paired comparison shared by `status` and `confirm`, economics stops that work without big steps, a near gate that compares changes, and the seven small lies. Change no behaviour beyond those fixes.

**Architecture:** Each subsection of the design is one task with its own test cycle and commit. A0.3 adds one new module, `evolvekit/stats.py`. Everything else edits functions in place. Interfaces change in four places: `NearBackend.add/closest` (A0.5), `finished_final_stage` (A0.2), `StopPolicy.__init__` (A0.4) and `Cascade.seed_private` (A0.2). Each task's **Interfaces** block names them.

**Tech Stack:** Python 3 standard library only; pytest; ruff (`python tasks.py lint`).

**Spec:** `docs/superpowers/specs/2026-11-08-algorithms-design.md` §3 (Project A0, A0.1–A0.6). The review rows it fixes are in §2.1 (D1–D6). Read with §8 (decisions), §9 (left to the builder) and §10 (testing standards). Sections 4–7 and programme B are out of scope.

**Base branch:** `algo/0-correctness`, cut from `master` at `68b579e` (PRs #72–#77 merged, so §8 decision 1 resolves to `master`; the build log says so).

```
git switch -c algo/0-correctness 68b579e
```

## Global Constraints

- Windows first (the dev box). Linux runs in CI. Paths go through `pathlib`, and files are written with `encoding="utf-8"`.
- Stdlib only. No scipy and no new dependency (§8.7, §11).
- TDD: write the failing test first, run it, watch it fail, then write the code.
- The gate is `python tasks.py check` (lint, then every test). The only entry points are `python tasks.py test | full | run | check | lint | dist` (AGENTS.md). Never start a demo study or `tasks.py run` while a full gate is running: the gate has timing-sensitive tests.
- Per-task test command: `.venv/Scripts/python.exe -m pytest <files> -q`.
- "The verdict is the only claim." No view may state more than the paired comparison supports.
- No vendor or model names in code, prompts, docs or tests.
- Write files with the Write/Edit tools, never with PowerShell `Set-Content`/`Out-File`.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Docs ship in the same commit as the behaviour they describe (§10). The manual is `docs/reference.md`: it holds the quickstart, "The novelty filters — structural, near, and behavioural" and "The hold-out penalty". `README.md` is the front page.
- Never push to `master`. Push the branch, open a PR, and leave the merge to a maintainer. The PR body lists the six defects by letter, each with its test.

## Review Focus

1. **A block that rebinds a builtin** (`max = 3` and then `return max`). The name is now bound, so it is canonicalised. Two blocks that differ only in which builtin name they shadow must collide. Pinned in A0.1.
2. **A seed whose own hold-out run failed** (no `private_score`). Then there is no reference, so nobody is vetoed, and the seed itself does not compete, as today. Pinned in A0.2.
3. **A per-instance final stage with one instance and several seeds.** After A0.3 that is n = 1 instance, so the verdict is `unknown` with a sentence. It must not crash and must not claim `clear`. Pinned in A0.3.
4. **A child whose parent block cannot be parsed or is not in `by_id`** (a legacy resume, a tree diff). The near gate treats it as parentless and must not raise. Pinned in A0.5.
5. **A big step whose response never applied** (unparseable, or a provider error), seen both live and after a resume. It must not count toward `big_steps_in_window`, and the replay must agree with the live run. Pinned in A0.6b.

---

### Task A0.1: Canonicalise only the names the block binds (D1)

**Files:**
- Modify: `evolvekit/search/novelty.py`: `_Canonicaliser`, new `_bound_names`, `normalise_block`, `node_type_sequence`, and the module docstring step 3.
- Test: `tests/test_novelty.py`

**Interfaces:**
- Produces: `_bound_names(tree: ast.Module, protected: set[str]) -> set[str]` and `_Canonicaliser(protected: set[str], bound: set[str])`. `normalise_block` and `novelty_hash` keep their signatures.

- [ ] **Step 1: Write the failing tests.** Append them to `tests/test_novelty.py` under `# -- normalisation`:

```python
def test_a_builtin_keeps_its_name_so_max_and_min_differ():
    """D1: tightest-first versus emptiest-first is literally max versus min."""
    tightest = "def priority(item, bins):\n    return max(item, bins)\n"
    emptiest = "def priority(item, bins):\n    return min(item, bins)\n"
    assert novelty_hash(tightest) != novelty_hash(emptiest)


def test_sorted_and_reversed_differ():
    a = "def order(items):\n    return list(sorted(items))\n"
    b = "def order(items):\n    return list(reversed(items))\n"
    assert novelty_hash(a) != novelty_hash(b)


def test_a_local_the_block_binds_is_still_canonicalised():
    assert novelty_hash("def f(x):\n    v = x\n    return v\n") == novelty_hash(
        "def f(x):\n    w = x\n    return w\n"
    )


def test_a_skeleton_helper_keeps_its_name():
    repair = "def priority(item, bins):\n    return repair(item, bins)\n"
    deadline = "def priority(item, bins):\n    return deadline(item, bins)\n"
    assert novelty_hash(repair) != novelty_hash(deadline)
    assert "repair" in normalise_block(repair)


def test_a_shadowed_builtin_is_a_local_like_any_other():
    a = "def f(xs):\n    max = len(xs)\n    return max\n"
    b = "def f(xs):\n    min = len(xs)\n    return min\n"
    assert novelty_hash(a) == novelty_hash(b)


@pytest.mark.parametrize(
    "template",
    [
        "def f(xs):\n    return [{n} * 2 for {n} in xs]\n",
        "def f(xs):\n    for {n} in xs:\n        pass\n    return {n}\n",
        "def f(xs):\n    with open(xs) as {n}:\n        return {n}.read()\n",
        "def f(xs):\n    try:\n        return xs[0]\n    except IndexError as {n}:\n        return str({n})\n",
        "def f(xs):\n    if ({n} := len(xs)) > 2:\n        return {n}\n    return 0\n",
        "def f(xs):\n    {n}: int = 0\n    {n} += len(xs)\n    return {n}\n",
        "def f(xs):\n    def {n}(y):\n        return y + 1\n    return {n}(xs)\n",
        "def f(xs):\n    class {n}:\n        pass\n    return {n}()\n",
        "def f(xs):\n    return (lambda {n}: {n} + 1)(xs)\n",
        "def f(xs):\n    {n} = 0\n    def g():\n        nonlocal {n}\n        {n} = 1\n    g()\n    return {n}\n",
        "def f(xs):\n    global {n}\n    {n} = xs\n    return {n}\n",
    ],
)
def test_every_binding_form_is_canonicalised(template):
    assert novelty_hash(template.format(n="alpha")) == novelty_hash(template.format(n="beta"))
```

- [ ] **Step 2: Run the tests and check that they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_novelty.py -q`
Expected: FAIL. `test_a_builtin_keeps_its_name_so_max_and_min_differ`, `test_sorted_and_reversed_differ` and `test_a_skeleton_helper_keeps_its_name` fail, because the names are renamed today. The nested `def`/`class`/`nonlocal`/`global` cases of the parametrised test fail too, because those names are not renamed consistently today.

- [ ] **Step 3: Implement `_bound_names(tree, protected) -> set[str]` in `evolvekit/search/novelty.py`**

Walk the tree once with `ast.walk` and collect these names:
- `ast.Name` with ctx `Store` or `Del`. This covers assignment, augmented and annotated assignment, `for`, `with … as`, comprehension targets and walrus targets.
- `ast.arg.arg`, for function and lambda arguments, including `*args`, `**kw`, positional-only and keyword-only.
- `ExceptHandler.name`.
- `Global.names` and `Nonlocal.names`.
- `FunctionDef/AsyncFunctionDef/ClassDef.name` for every def that is **not** a direct child of the module.
- `MatchAs.name`, `MatchStar.name` and `MatchMapping.rest`, when they are not `None`.

Then subtract `protected` and every dunder name.

Change `_Canonicaliser.__init__(self, protected, bound)`. `_rename` renames only names in `bound`; every other name keeps its text. Add `visit_ExceptHandler`, `visit_Global` and `visit_Nonlocal`, which rename through `_rename`. Change `visit_FunctionDef`, `visit_AsyncFunctionDef` and `visit_ClassDef` so they also rename `node.name` through `_rename`. That is a no-op for the top-level defs, because those are protected. Leave `visit_arg` as it is: an argument is always bound. Both `normalise_block` and `node_type_sequence` build `_Canonicaliser(protected, _bound_names(tree, protected))`, where `protected = _protected_names(tree)`. Rewrite step 3 of the module docstring: "canonicalise the names the block **binds** (arguments, assignment and loop targets, `with`/`except` names, comprehension and walrus targets, nested defs, `global`/`nonlocal`) to `v0`, `v1` … in order of first appearance; every free name (builtins, the skeleton's helpers, imports) keeps its text".

- [ ] **Step 4: Run the tests and check that they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_novelty.py tests/test_near_novelty.py -q`
Expected: PASS, including the existing no-op/duplicate tests and `test_docstrings_comments_and_local_names_do_not_make_a_new_program`. In that test, `remaining` is a comprehension target, so it is bound.

- [ ] **Step 5: Update the manual's description of the structural filter.** In `docs/reference.md`, under "The novelty filters — structural, near, and behavioural", the **Structural** paragraph says "its local names canonicalised to `v0, v1, …`". Change it to: "the names it binds (arguments, assignment and loop targets, nested defs) canonicalised to `v0, v1, …`; builtins, the skeleton's helpers and imports keep their names, so `max` and `min` are different programs".

- [ ] **Step 6: Commit**

```bash
git add evolvekit/search/novelty.py tests/test_novelty.py docs/reference.md
git commit -m "fix(novelty): canonicalise only the names a block binds (D1)

max vs min, sorted vs reversed and two skeleton helpers no longer share a
hash, so semantically different children are evaluated, not written off.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A0.2: The hold-out as a veto, not a fitness term (D2)

**Files:**
- Modify: `evolvekit/config.py`. In `EvaluateConfig`, the `holdout_penalty` default goes from 1.0 to **0.0**, a new `holdout_veto: bool = True` is added, `parse` gains the key and the default, and the docstrings change.
- Modify: `evolvekit/evaluate/cascade.py`. `finished_final_stage` gains `seed_private`. `Cascade` gains a `seed_private` attribute, which `evaluate_generation` sets and uses. The `_run_private` docstring changes.
- Modify: `evolvekit/evaluate/types.py`: `EvalResult.veto: str | None = None`.
- Modify: `evolvekit/candidate.py`: `Candidate.veto: str | None = None`, plus the `ranking_score` docstring.
- Modify: `evolvekit/leaderboard.py`: rename `_seed_private` to public `seed_private`. Keep it on the **last** seed row with a private score, and update its two callers in the module.
- Modify: `evolvekit/search/driver.py`. `resume` computes `seed_private(raw_rows)` before `_settle_competes`, sets `self.cascade.seed_private` and passes the value into `_settle_competes`. The candidate copy block (around `candidate.gated = result.gated`) adds `candidate.veto = result.veto`.
- Modify: `evolvekit/evaluate/scoring.py`, the `ranking_score` docstring only. Say that the default λ is 0 and that the veto lives in `finished_final_stage`.
- Modify: `evolvekit/cli.py`. In the `init` template, replace the `holdout_penalty: 1.0` lines with `holdout_veto: true` and a comment.
- Modify: `examples/binpacking/evolvekit.yaml`, `examples/circlepacking/evolvekit.yaml` and `examples/pyvrp/evolvekit.yaml`. Remove `holdout_penalty: 1.0` and its comment, and say in one comment line that the default veto applies. In `examples/pyvrp/evolvekit.real.yaml`, the stage-2 comment says "charged once through `holdout_penalty`"; change it to "a candidate whose hold-out falls below the seed's does not compete (`holdout_veto`)". This is open question 2.
- Modify: `docs/reference.md` "The hold-out penalty", and `docs/new-experiment.md` (the `holdout_penalty: 1.0` example and the sentence after it).
- Test: `tests/test_ranking.py`, `tests/test_evaluate.py`, `tests/test_status.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `finished_final_stage(config: Config, *, rejected: bool, last_failure: str | None, stages_reached: Sequence[str], private_score: float | None, gated: str | None = None, seed_private: float | None = None) -> bool`
  - `holdout_veto(config: Config, private_score: float | None, seed_private: float | None) -> str | None` (in `cascade.py`, exported). It returns `f"holdout below the seed's {seed_private:.6g}"` when `config.evaluate.holdout_veto` is on, both values are present, and `private_score < seed_private`. Otherwise it returns `None`.
  - `Cascade.seed_private: float | None` (initialised to `None`).
  - `leaderboard.seed_private(rows) -> float | None`.
  - `EvalResult.veto` and `Candidate.veto`, both `str | None`.

- [ ] **Step 1: Write the failing tests.**

In `tests/test_ranking.py`, rename `test_the_default_penalty_is_one` and give it the new defaults:

```python
def test_the_default_penalty_is_zero_and_the_veto_is_on(minimal_raw, tmp_path):
    evaluate = build_config(minimal_raw, base_dir=tmp_path).evaluate
    assert evaluate.holdout_penalty == 0.0
    assert evaluate.holdout_veto is True


def test_the_veto_switch_must_be_a_flag(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["holdout_veto"] = "sometimes"
    with pytest.raises(ConfigError, match="holdout_veto"):
        build_config(raw, base_dir=tmp_path)


def test_with_no_penalty_the_archive_orders_by_the_public_score():
    rows = [_row("overfitter", -5.0, -6.0, penalty=0.0), _row("honest", -5.4, -5.4, penalty=0.0)]
    assert [r["id"] for r in rank(rows)] == ["overfitter", "honest"]
    assert all(fitness_of(r) == r["public_score"] for r in rows)
```

In the same file, add a project whose scores depend on the candidate, together with the veto tests:

```python
VALUE_SKELETON = (
    '__all__ = ["value"]\n\n\n'
    "# EVOLVE-BLOCK-START\n"
    "def value():\n"
    "    return {v}\n"
    "# EVOLVE-BLOCK-END\n"
)


@pytest.fixture
def mirror_project(tmp_path: Path) -> Path:
    """Public score `value()`, hold-out score `-value()`: every public gain is a hold-out loss."""
    (tmp_path / "skeleton.py").write_text(VALUE_SKELETON.format(v=1), encoding="utf-8", newline="\n")
    (tmp_path / "evaluate.py").write_text(
        "import argparse, json, runpy\n"
        "from pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--candidate'); p.add_argument('--inputs'); p.add_argument('--out')\n"
        "a = p.parse_args()\n"
        "v = float(runpy.run_path(a.candidate)['value']())\n"
        "score = v if a.inputs == 'public' else -v\n"
        "out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)\n"
        "out.write_text(json.dumps({'kpis': {'value': score}}), encoding='utf-8')\n",
        encoding="utf-8",
        newline="\n",
    )
    config = {
        "problem": {"skeleton": "skeleton.py", "required_functions": ["value"]},
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static"},
                {"id": "full", "kind": "command", "timeout": 60,
                 "command": "python evaluate.py --candidate {candidate} --inputs {inputs} --out {out}",
                 "inputs": ["public"], "private_inputs": ["holdout"]},
            ],
            "score": {"objective": "value", "direction": "maximize"},
        },
        "models": {
            "small": {"provider": "fake", "model": "s", "options": {"responses": ["x"]}},
            "strong": {"provider": "fake", "model": "b", "options": {"responses": ["x"]}},
        },
    }
    (tmp_path / "evolvekit.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8", newline="\n")
    return tmp_path / "evolvekit.yaml"


def _value_candidate(cid: str, v: float, operator: str = "rewrite") -> Candidate:
    return Candidate(id=cid, generation=0 if operator == "human-seed" else 1,
                     block=f"def value():\n    return {v}\n",
                     source=VALUE_SKELETON.format(v=v), operator=operator)


def test_a_candidate_below_the_seeds_hold_out_does_not_compete(mirror_project, tmp_path):
    config = load_config(mirror_project)
    cascade = Cascade(config, work_dir=tmp_path / "work")
    seed = cascade.evaluate_generation([_value_candidate("g000-c0001", 1, "human-seed")])["g000-c0001"]
    assert seed.competes and cascade.seed_private == pytest.approx(-1.0)

    results = cascade.evaluate_generation([_value_candidate("hi", 2), _value_candidate("lo", 0.5)])
    assert results["hi"].public_score == pytest.approx(2.0), "the best public score..."
    assert results["hi"].competes is False, "...is vetoed by its hold-out"
    assert results["hi"].veto == "holdout below the seed's -1"
    assert results["hi"].ranking_score == pytest.approx(2.0), "lambda = 0: the score itself is untouched"
    assert results["lo"].competes is True and results["lo"].veto is None


def test_the_veto_can_be_switched_off(mirror_project, tmp_path):
    config = load_config(mirror_project)
    config = replace(config, evaluate=replace(config.evaluate, holdout_veto=False))
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.evaluate_generation([_value_candidate("g000-c0001", 1, "human-seed")])
    hi = cascade.evaluate_generation([_value_candidate("hi", 2)])["hi"]
    assert hi.competes is True and hi.veto is None


def test_no_seed_hold_out_means_no_veto(mirror_project, tmp_path):
    config = load_config(mirror_project)
    assert finished_final_stage(config, rejected=False, last_failure=None, stages_reached=["static", "full"],
                                private_score=-5.0, seed_private=None) is True
    assert finished_final_stage(config, rejected=False, last_failure=None, stages_reached=["static", "full"],
                                private_score=-5.0, seed_private=-1.0) is False


def test_a_resumed_run_rebuilds_the_seeds_hold_out_from_runs_jsonl(mirror_project, tmp_path):
    from evolvekit.search.driver import Driver

    config = load_config(mirror_project)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rows = [
        {"id": "g000-c0001", "generation": 0, "operator": "human-seed", "block": "def value():\n    return 1\n",
         "score": 1.0, "public_score": 1.0, "private_score": -1.0, "rejected": False, "competes": True,
         "stages_reached": ["static", "full"]},
        # Written before `competes` existed: settled by today's rule, veto included.
        {"id": "g001-c0002", "generation": 1, "operator": "rewrite", "parent_id": "g000-c0001",
         "block": "def value():\n    return 2\n", "score": 2.0, "public_score": 2.0, "private_score": -2.0,
         "rejected": False, "stages_reached": ["static", "full"]},
    ]
    (run_dir / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    driver = Driver(config, run_dir=run_dir)
    driver.resume()
    assert driver.cascade.seed_private == pytest.approx(-1.0)
    assert driver.by_id["g001-c0002"].competes is False
```

Also import `finished_final_stage` from `evolvekit.evaluate.cascade` at the top of `tests/test_ranking.py`.

In `tests/test_status.py`, add this test under `# -- what is the best`:

```python
def test_a_vetoed_candidate_is_never_the_best(tmp_path):
    run = RunDir(tmp_path)
    run.started(1000)
    run.row("g000-c0001", 0, 200.0)
    run.row("g001-c0002", 1, 150.0, parent_id="g000-c0001", competes=False,
            veto="holdout below the seed's -195")
    run.row("g001-c0003", 1, 190.0, parent_id="g000-c0001")
    assert build_status(tmp_path, now=NOW)["progress"]["best"]["id"] == "g001-c0003"
```

In `tests/test_config.py`, `test_the_base_example_declares_the_phase_b_search` currently asserts `holdout_penalty == 1.0`. Change it to `assert config.evaluate.holdout_penalty == 0.0 and config.evaluate.holdout_veto is True`.

In `tests/test_evaluate.py`, add:

```python
def test_a_stage_without_a_hold_out_is_never_vetoed(tmp_path):
    config = _cascade_config(tmp_path, _echo_script(tmp_path))
    cascade = Cascade(config, work_dir=tmp_path / "work")
    cascade.seed_private = 100.0  # would veto anything, were there a hold-out
    result = cascade.evaluate_generation(
        [_candidate("a", '# KPIS {"excess": 4.0}\ndef priority(item, bins):\n    return []\n')]
    )["a"]
    # `proxy` is the final stage here and has no hold-out: it competes, unvetoed.
    assert result.competes is True and result.veto is None
```

- [ ] **Step 2: Run the tests and check that they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ranking.py tests/test_evaluate.py tests/test_status.py tests/test_config.py -q`
Expected: FAIL with `AttributeError`/`TypeError`, because `holdout_veto`, `seed_private` and `veto` do not exist yet, and the default is still 1.0.

- [ ] **Step 3: Implement.**
  - **Config.** Parse with `_as_flag(data.get("holdout_veto", True), "evaluate.holdout_veto")`. Change the `holdout_penalty` default to `0.0` in both the dataclass and `parse`.
  - **`finished_final_stage`.** After the existing checks, `return holdout_veto(config, private_score, seed_private) is None`.
  - **`evaluate_generation`.** After the stage loop and before the `competes` loop, check every candidate with `operator == SEED_OPERATOR` and `results[cid].private_score is not None`, and set `self.seed_private` to that score. The last seed evaluated wins. In the `competes` loop, pass `seed_private=self.seed_private`. When the result finished the final stage but is vetoed, set `result.veto = holdout_veto(...)`.
  - **`_run_private` docstring.** Replace it, word for word, with: "Scores the hold-out, records the gap, and with `holdout_veto` keeps a candidate below the seed's hold-out from competing. With a non-zero `holdout_penalty` it also enters the ranking score."
  - **Driver.** In `resume`, compute `raw = self.ledger.runs()` and `reference = seed_private(raw)`, then `rows = [self._settle_competes(r, reference) for r in raw]` and `self.cascade.seed_private = reference`. `_settle_competes(self, row, seed_private)` passes the value through. A row that already carries `competes` keeps it. See open question 6.

- [ ] **Step 4: Run the tests and check that they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ranking.py tests/test_evaluate.py tests/test_status.py tests/test_config.py tests/test_leaderboard.py tests/test_competes.py -q`
Expected: PASS. The existing `test_the_cascade_records_the_raw_scores_and_the_discounted_one` still passes: it sets λ = 1 explicitly, and that gives the behaviour from before.

- [ ] **Step 5: Rewrite the docs.** In `docs/reference.md`, rewrite "The hold-out penalty" in the manual's voice. Cover these points:
  1. `evaluate.holdout_veto` (default `true`). A candidate whose private score is below the **seed's** private score does not compete. Its row carries `competes: false` and `veto: "holdout below the seed's …"`, and the leaderboard's `!` flag marks the same condition.
  2. `evaluate.holdout_penalty` (default `0.0`). The formula is unchanged. At 0 the ranking score equals the public score, so the archive, parent sampling and `best` order by the public score.
  3. Why. The 2026-08-22 failure (a winner whose hold-out went backwards) is caught by the veto, which leaks one bit per candidate. At λ = 1 the hold-out was a continuous fitness term: it was selected on for generations until it stopped being a hold-out, and `generalization_gap` stopped meaning what it says.
  4. A non-zero penalty is still available, with that cost stated.

  In `docs/new-experiment.md`, drop `holdout_penalty: 1.0` from the example and rewrite the following sentence around the veto.

- [ ] **Step 6: Commit**

```bash
git add evolvekit/config.py evolvekit/evaluate/cascade.py evolvekit/evaluate/types.py evolvekit/evaluate/scoring.py evolvekit/candidate.py evolvekit/leaderboard.py evolvekit/search/driver.py evolvekit/cli.py examples docs/reference.md docs/new-experiment.md tests/test_ranking.py tests/test_evaluate.py tests/test_status.py tests/test_config.py
git commit -m "fix(holdout): veto below the seed's hold-out instead of ranking by it (D2)

holdout_penalty defaults to 0, so the search ranks by the public score;
holdout_veto (default on) keeps a candidate below the seed's private score
from competing. The private set stops being a training set.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A0.3: One paired comparison (D3)

**Files:**
- Create: `evolvekit/stats.py`, with `_T95`, `_Z975`, `_t95`, `_betacf`, `_betainc`, `t_cdf`, `wilcoxon_signed_rank` and `paired_summary` moved **verbatim** from `confirm.py`, plus the new `per_instance_means`. `__all__ = ["t_cdf", "wilcoxon_signed_rank", "paired_summary", "per_instance_means"]`.
- Modify: `evolvekit/confirm.py`. Delete the moved definitions and the section header. Add `from evolvekit.stats import _betainc, _t95, paired_summary, t_cdf, wilcoxon_signed_rank  # noqa: F401  (re-exported: importers below)`. Keep `__all__` as it is. Drop `import math` and `stdev` if ruff reports them unused after the move. `fmean` is still used by `_analyse`.
- Modify: `evolvekit/status.py`. Delete `_paired_t`. Rewrite `_verdict`, `_verdict_per_instance` and `_noise_note`.
- Test: `tests/test_confirm.py`, `tests/test_status.py`

**Current importers. None of them changes; confirm.py re-exports.**
- `evolvekit/app/verdict.py:55` imports `t_cdf` (plus `BASELINE`, `RULE_WORDS`, `USE_CHANCE`, `decide`, `identical_runs`, which stay in confirm).
- `evolvekit/app/tries.py:261` imports `paired_summary`.
- `evolvekit/harness/execute.py:42` and `evolvekit/cli.py:967` import `confirm`, `decide` and `render_markdown`, which stay.
- `tests/test_confirm.py:13` imports `paired_summary`, `t_cdf` and `wilcoxon_signed_rank`, and `:97` imports `_t95`.
- `tests/test_app_results.py:13` imports `_t95` and `paired_summary`.
- `tests/test_study_run.py:17` and `tests/test_app_assistant.py:388` import `paired_summary`.
- `_paired_t` is defined in `evolvekit/status.py:146` and used only at `status.py:963` and `:991`. No test imports it.
- `_betainc`/`_betacf` are imported by nobody outside confirm. `_betainc` is re-exported anyway, under the name the design gives.

**Interfaces:**
- Produces: `evolvekit.stats.per_instance_means(gains: Mapping[Hashable, float]) -> list[float]`. A tuple key `(instance, seed)` is grouped by `key[0]`; any other key (a bare seed) is its own group. It returns the group means in order of first appearance, and `[]` for an empty mapping.
- Produces: `status.progress.improvement` keeps `verdict`, `why`, `n`, `mean_gain` and `sd_gain`, adds `ci95: [low, high] | None`, and **drops `t`**. No consumer reads `t` (checked: app, dashboard, tests). `n` is the number of compared cases: instances on a per-instance stage, seeds otherwise.

- [ ] **Step 1: Write the failing tests.** In `tests/test_confirm.py`, statistics section:

```python
def test_the_statistics_live_in_one_module_and_confirm_re_exports_them():
    from evolvekit import confirm as confirm_module, stats

    for name in ("paired_summary", "t_cdf", "wilcoxon_signed_rank", "_t95", "_betainc"):
        assert getattr(confirm_module, name) is getattr(stats, name), name


def test_per_instance_means_average_the_seeds_within_each_instance():
    from evolvekit.stats import per_instance_means

    gains = {("a", 0): 1.0, ("a", 1): 3.0, ("b", 0): -2.0, ("b", 1): 0.0, ("c", 0): 5.0}
    assert per_instance_means(gains) == [2.0, -1.0, 5.0]
    assert per_instance_means({0: 1.5, 1: 2.5}) == [1.5, 2.5], "no instances: each seed is its own case"
    assert per_instance_means({}) == []
```

In `tests/test_status.py`, add the agreement test and the one-instance case:

```python
def test_the_live_verdict_and_the_final_check_are_one_test(tmp_path):
    """D3: `status` judged t >= 2 over every (instance, seed) run; `confirm`
    judged a 95 % interval over per-instance means. Same runs, same answer now."""
    from evolvekit.confirm import confirm
    from tests.test_confirm import _write_config, _write_run

    config_path = _write_config(tmp_path, seeds=2)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001, 1003], label="same", log=lambda m: None)
    summary = comparison.per_candidate["g003-c0012"]["summary"]

    run = RunDir(run_dir)
    stages = [STAGES[0], {**STAGES[1], "instances": ["small", "medium", "large"]}]
    run.event("run_started", 1000, objective="cost", direction="minimize", stages=stages,
              first_generation=1, generations_planned=5, budget={}, stop={})
    names = {"baseline": "g000-c0001", "g003-c0012": "g003-c0012"}
    for r in comparison.runs:
        run.event("eval_finished", 900, candidate_id=names[r["configuration"]], stage="full", seed=r["seed"],
                  private=False, instance=r["instance"], ok=r["ok"], duration_s=1.0, kpis=r["kpis"])
    improvement = build_status(run_dir, now=NOW)["progress"]["improvement"]

    assert improvement["n"] == summary["n"] == 3
    assert improvement["mean_gain"] == pytest.approx(summary["mean"])
    assert improvement["ci95"] == pytest.approx(summary["ci95"])
    expected = "clear" if summary["ci95"][0] > 0 else "worse" if summary["ci95"][1] < 0 else "within noise"
    assert improvement["verdict"] == expected == "clear"
    assert "never saw" in improvement["why"], "the selection caveat stays"


def test_one_instance_run_many_times_is_one_case_not_many(tmp_path):
    run = RunDir(tmp_path)
    stages = [STAGES[0], {**STAGES[1], "instances": ["a"]}]
    run.event("run_started", 1000, objective="cost", direction="minimize", stages=stages,
              first_generation=1, generations_planned=5, budget={}, stop={})
    run.row("g000-c0001", 0, 100.0)
    run.row("g001-c0002", 1, 90.0, parent_id="g000-c0001")
    for seed in range(3):
        for cid, cost in (("g000-c0001", 100.0 + seed), ("g001-c0002", 90.0 + seed)):
            run.event("eval_finished", 900, candidate_id=cid, stage="full", seed=seed, private=False,
                      instance="a", ok=True, duration_s=1.0, kpis={"cost": cost})
    improvement = build_status(tmp_path, now=NOW)["progress"]["improvement"]
    assert improvement["verdict"] == "unknown" and improvement["n"] == 1
```

Update the existing `tests/test_status.py` tests whose assertions name `t`:
- `test_a_gain_that_holds_on_every_shared_seed_is_clear`: replace `assert improvement["t"] > 2` with `assert improvement["ci95"][0] > 0`.
- `test_gains_that_do_not_vary_are_judged_in_the_direction_of_their_mean`: replace the `"t = -inf"` assertion with `assert improvement["ci95"] == [pytest.approx(-1.0), pytest.approx(-1.0)] and "within noise" not in improvement["why"]`.
- `test_gains_that_do_not_vary_and_help_are_clear`: replace `"t = inf" in improvement["why"]` with `improvement["ci95"][0] == pytest.approx(1.0)`.

- [ ] **Step 2: Run the tests and check that they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_confirm.py tests/test_status.py -q`
Expected: FAIL. `evolvekit.stats` does not exist, and `improvement` has no `ci95`.

- [ ] **Step 3: Implement.**
  - **`_verdict`.** Compute `gains = {s: sign * (a[s] - b[s]) for s in shared}` and `summary = paired_summary(per_instance_means(gains))`. Units stay as they are today: the objective's own units.
  - **`_verdict_per_instance`.** Compute `gains = {run: 100*sign*(a[run]-b[run])/abs(a[run]) for run in shared if a[run]}`, then `summary = paired_summary(per_instance_means(gains))`. When `summary["n"] < 2`, return `{"verdict": "unknown", "why": "fewer than two instances to compare: one instance run many times is one case", "n": summary["n"]}`.
  - **Both.** The verdict is `clear` iff `ci95[0] > 0`, `worse` iff `ci95[1] < 0`, and `within noise` otherwise. The `why` reads `f"paired over {n} … : mean gain {mean:.6g}, 95 % interval {low:.6g} to {high:.6g}"`, then `_noise_note`, then the unchanged "These are the seeds [instances and seeds] the search selected on, so even a clear result is optimistic until it is confirmed on seeds it never saw".
  - **`_noise_note(clear, worse)`.** The worse text is `" (the interval lies below zero: the best is behind the baseline on these seeds, beyond the noise)"`. The neither text is `" (the interval includes zero: not distinguishable from noise)"`.
  - **Returned keys.** `mean_gain=summary["mean"]`, `sd_gain=summary["sd"]`, `ci95=summary["ci95"]`.

- [ ] **Step 4: Run the tests and check that they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_confirm.py tests/test_status.py tests/test_app_results.py tests/test_study_run.py tests/test_app_assistant.py tests/test_verdict.py tests/test_app_tries.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add evolvekit/stats.py evolvekit/confirm.py evolvekit/status.py tests/test_confirm.py tests/test_status.py
git commit -m "fix(status): one paired comparison, shared with confirm (D3)

New evolvekit/stats.py holds the t quantile, incomplete beta, t cdf,
Wilcoxon and paired_summary (re-exported from confirm), plus
per_instance_means. status judges the 95 % interval over per-instance
means instead of t >= 2 over every run; _paired_t is gone.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A0.4: Economics rules without big steps (D4)

**Files:**
- Modify: `evolvekit/budget.py`: `StopPolicy.__init__` and `economics_reason`, plus the docstring line "Both rules refuse to fire until at least one big step…".
- Modify: `evolvekit/search/driver.py`, the `StopPolicy(...)` construction (around line 168).
- Test: `tests/test_economics.py`

**Interfaces:**
- Produces: `StopPolicy(config: StopConfig, *, takes_big_steps: bool = True)` with the attribute `self.takes_big_steps`. The guard in `economics_reason` becomes `if point is None or (self.takes_big_steps and self.big_steps_in_window < 1): return None`.

- [ ] **Step 1: Write the failing tests.** Append them to `tests/test_economics.py` under the stop-rule tests:

```python
def test_without_big_steps_the_exchange_rate_rule_needs_no_big_step_credit():
    policy = StopPolicy(
        StopConfig(patience=99, min_gain_per_usd=GainPerUsdRule(window=RULE_WINDOW, threshold=1.0)),
        takes_big_steps=False,
    )
    policy.update(1.0)
    decision = policy.update(1.0, _points(0.50, window_gain=0.01)[-1])
    assert decision.stop and "stop.min_gain_per_usd reached" in decision.reason


def test_without_big_steps_the_usd_rule_needs_no_big_step_credit_either():
    policy = StopPolicy(StopConfig(patience=99, max_usd_since_improvement=0.20), takes_big_steps=False)
    assert policy.update(1.0, _points(0.50)[-1]).stop is True


def test_the_driver_stops_a_run_without_big_steps_on_its_exchange_rate(tmp_path):
    """D4: with `big_step_every: 0` both rules were documented and dead."""
    config = load_config(EXAMPLE_CONFIG)
    config = replace(
        config,
        evaluate=replace(config.evaluate, stages=config.evaluate.stages[:2]),
        models=replace(
            config.models,
            small=replace(config.models.small, price_in_per_mtok=1000.0),
            strong=replace(config.models.strong, price_in_per_mtok=1000.0),
        ),
        stop=replace(config.stop, patience=99, min_big_steps=0,
                     min_gain_per_usd=GainPerUsdRule(window=2, threshold=1.0)),
        search=replace(config.search, generations=6, children_per_generation=1, big_step_every=0,
                       scratchpad_every=0, inspirations=0, operators={"rewrite": 1.0}),
    )
    assert config.search.takes_big_steps is False
    flat = (
        "```python\n"
        "def priority(item, bins):\n"
        '    """Worst fit -- worse than the seed, and never improving."""\n'
        "    return [b - item for b in bins]\n"
        "```"
    )
    provider = FakeProvider([flat], cycle=True)
    driver = Driver(config, run_dir=tmp_path / "econ0", providers={"small": provider, "strong": provider})
    summary = driver.run()
    assert "stop.min_gain_per_usd reached" in summary.stop_reason
    assert summary.generations < 6
```

The existing `test_the_usd_rule_holds_until_a_big_step_has_been_tried` must keep passing unchanged, since the default is `takes_big_steps=True`.

- [ ] **Step 2: Run the tests and check that they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_economics.py -q`
Expected: FAIL. The first two raise `TypeError: unexpected keyword 'takes_big_steps'`. The driver test fails its `stop.min_gain_per_usd reached` assertion: the run goes six generations and stops for another reason.

- [ ] **Step 3: Implement.** Add `takes_big_steps` to `StopPolicy`. In the driver, pass `takes_big_steps=config.search.takes_big_steps` next to the existing `min_big_steps=0` relief, and extend the comment there to say the same relief now applies to the economics rules. Rewrite the `economics_reason` docstring: "In a run that takes big steps, both rules refuse to fire until one has been spent since the last improvement…; a run that takes none has nothing to wait for."

- [ ] **Step 4: Run the tests and check that they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_economics.py tests/test_budget_ledger.py tests/test_resume_budget.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add evolvekit/budget.py evolvekit/search/driver.py tests/test_economics.py
git commit -m "fix(stop): economics rules fire in a run without big steps (D4)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A0.5: The near gate compares the change, not the block (D5)

**Files:**
- Modify: `evolvekit/search/novelty.py`: new `delta_grams`, `NearBackend`, `LocalNearIndex`, `EmbeddingNearIndex` (signature only), `NoveltyIndex.add/closest/check`, and the "Near-duplicates (Phase C)" docstring paragraph for `local`.
- Modify: `evolvekit/search/driver.py`. The two `self.novelty.add(candidate.id, candidate.block)` call sites (resume, around line 546, and record, around line 1717) pass `parent_block=self._parent_block(candidate)`. Add the helper `_parent_block(self, candidate: Candidate) -> str | None`, which returns the parent's block when `candidate.parent_id` is in `self.by_id` and `None` otherwise.
- Modify: `docs/reference.md`, the `local` row of the method table in "The novelty filters — structural, near, and behavioural".
- Test: `tests/test_near_novelty.py`

**Interfaces:**
- Produces: `delta_grams(block: str, parent_block: str | None, n: int = NGRAM_SIZE) -> dict[tuple[str, ...], int]`. With `parent_block is None` it returns `node_ngrams(block, n)` unchanged: an untagged full vector, for the seed. Otherwise it computes `c, p = Counter(node_ngrams(block)), Counter(node_ngrams(parent_block))` and returns `{("+", *g): k for g, k in (c - p).items()} | {("-", *g): k for g, k in (p - c).items()}`. The tags keep a delta from ever matching a full vector, so the seed matches no child's delta.
- Produces: `NearBackend.add(candidate_id: str, block: str, parent_block: str | None = None) -> None` and `NearBackend.closest(block: str, parent_block: str | None = None, parent_id: str | None = None) -> tuple[str | None, float]`.
- Produces: `LocalNearIndex`. `add` stores `delta_grams(block, parent_block)` when it is non-empty. `closest` does the following, in order:
  1. When `parent_block is not None` and the delta is empty (the change moved constants only), it returns `(parent_id, 1.0)`. This is the one case in which the parent is a match; see open question 3.
  2. Otherwise it returns the best cosine over the stored vectors, skipping `parent_id`.
- Produces: `EmbeddingNearIndex.add/closest` accept the new parameters and ignore them. The design scopes A0.5 to `local`.
- Produces: `NoveltyIndex.add(candidate_id, block, parent_block: str | None = None) -> str` and `NoveltyIndex.closest(block, parent_block=None, parent_id=None)`. `check(block, parent_block, parent_id, *, near=True)` keeps its signature and passes `parent_block` and `parent_id` to `closest`.

- [ ] **Step 1: Write the failing tests.** Add the fixtures and tests to `tests/test_near_novelty.py` after `# -- the verdict`:

```python
PARENT_30 = '''\
def priority(item, bins):
    """Best fit with a residual bonus, a fullness tie-break and a cap."""
    scores = []
    capacity = 150.0
    for index, b in enumerate(bins):
        residual = b - item
        if residual < 0:
            scores.append(-1e9)
            continue
        fullness = 1.0 - b / capacity
        score = -residual
        score += 20.0 * math.exp(-((residual - 30.0) ** 2) / 128.0)
        score += 0.5 * fullness
        if residual == 0:
            score += 1000.0
        elif residual < 5:
            score -= 50.0
        score -= 0.001 * index
        if score > 1e6:
            score = 1e6
        scores.append(score)
    best = max(scores) if scores else 0.0
    for index in range(len(scores)):
        if scores[index] == best:
            scores[index] += 0.1
            break
    total = sum(scores)
    if total == 0:
        return scores
    return scores
'''

_ANCHOR = "        score -= 0.001 * index\n"
BRANCHED = PARENT_30.replace(
    _ANCHOR, _ANCHOR + "        if item > 0.5 * capacity and residual < 20:\n            score += 40.0 * (20 - residual)\n"
)
BRANCHED_AGAIN = PARENT_30.replace(
    _ANCHOR, _ANCHOR + "        if item > 0.4 * capacity and residual < 25:\n            score += 30.0 * (25 - residual)\n"
)
BRANCHED_TWICE = BRANCHED.replace(
    _ANCHOR, _ANCHOR + "        if item > 0.3 * capacity and residual < 10:\n            score += 10.0 * (10 - residual)\n"
)


def test_adding_a_branch_to_a_thirty_line_parent_is_not_near():
    assert local_similarity(BRANCHED, PARENT_30) > 0.97, "the premise: the whole-block cosine called this a twin"
    index = _index()
    index.add("g000-c0001", PARENT_30)
    assert index.check(BRANCHED, PARENT_30, "g000-c0001").novel


def test_the_same_change_to_the_same_parent_is_near():
    index = _index()
    index.add("g000-c0001", PARENT_30)
    index.add("g001-c0002", BRANCHED, parent_block=PARENT_30)
    verdict = index.check(BRANCHED_AGAIN, PARENT_30, "g000-c0001")
    assert verdict.near and verdict.twin_id == "g001-c0002"
    assert verdict.similarity >= 0.97


def test_the_parent_is_never_its_childs_near_twin():
    index = LocalNearIndex()
    index.add("p", PARENT_30)
    index.add("c", BRANCHED, parent_block=PARENT_30)
    # c's grandchild repeats c's kind of change on c: c's own delta would match it.
    twin, _ = index.closest(BRANCHED_TWICE, parent_block=BRANCHED, parent_id="c")
    assert twin is None


def test_a_constants_only_change_is_near_its_parent():
    index = _index()
    index.add("g000-c0001", BONUS_20)
    verdict = index.check(BONUS_18, BONUS_20, "g000-c0001")
    assert verdict.near and verdict.twin_id == "g000-c0001"
    assert verdict.similarity == pytest.approx(1.0)


def test_a_parent_that_will_not_parse_does_not_break_the_gate():
    index = _index()
    index.add("g001-c0002", BRANCHED, parent_block="def broken(:::")
    assert index.check(BRANCHED_AGAIN, "def broken(:::", "g000-c0001").kind in (None, "near")
```

Three existing tests index a child without its parent. Update them to `index.add("g001-c0002", BONUS_20, parent_block=BEST_FIT)`: `test_a_near_duplicate_is_flagged_but_not_rejected`, `test_the_near_retry_hint_asks_for_a_new_rule_not_new_constants` and `test_near_false_skips_the_gate_entirely`. Their assertions stay. `delta(BONUS_18, BEST_FIT)` equals `delta(BONUS_20, BEST_FIT)`, so the similarity is still 1.0.

- [ ] **Step 2: Run the tests and check that they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_near_novelty.py -q`
Expected: FAIL. `add()` gets an unexpected keyword `parent_block`, and `test_adding_a_branch…` is `near` today.

- [ ] **Step 3: Implement** the Interfaces above in `novelty.py` and `driver.py`. In the "Near-duplicates (Phase C)" docstring, the `local` bullet becomes: "cosine over the 3-grams the change **added and removed** against its parent (the delta multiset, sign-tagged); the parent is never a match, except that a change that moved constants only is near its parent at 1.0."

- [ ] **Step 4: Run the tests and check that they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_near_novelty.py tests/test_novelty.py tests/test_diff_prompts.py -q`
Expected: PASS. That includes the unchanged `stubborn_run` tests (`test_a_near_duplicate_is_re_prompted_once_then_evaluated_anyway`, …): the re-prompt hint is sent once, and then the child is evaluated anyway. They pass whichever parent the run samples for generation 2. If the parent is the seed, the delta equals g001's delta. If the parent is g001, the delta is empty, so the child is near its parent at 1.0.

- [ ] **Step 5: Update the manual's method table.** In `docs/reference.md`, the `local` row of the method table becomes: "cosine over the 3-grams the change added and removed (child versus parent, node types of the canonicalised AST). Adding a branch to a long block is not a near twin of that block; making the same change another child already made to the same parent is. A change that moved nothing but constants is near its parent at **1.00**."

- [ ] **Step 6: Commit**

```bash
git add evolvekit/search/novelty.py evolvekit/search/driver.py tests/test_near_novelty.py docs/reference.md
git commit -m "fix(novelty): the near gate compares a child's change, not its block (D5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A0.6a: The sweep, part 1: config and scoring lies (D6: parent_top_k, inspirations, private KPIs, `_cv`, ragged vectors)

**Files:**
- Modify: `evolvekit/config.py`. Remove `parent_top_k` from `SearchConfig` and its parse. In `SearchConfig.parse`, before `_reject_unknown`, raise `ConfigError("search.parent_top_k: removed -- it never did anything; the elites a parent is drawn from are search.archive.top_k")` when the key is present.
- Modify: `evolvekit/cli.py` (the `init` template line `parent_top_k: 5`) and `examples/{binpacking,circlepacking,pyvrp}/evolvekit.yaml`: remove `parent_top_k`.
- Modify: `evolvekit/search/driver.py`, `_inspirations`. Use `budget = self.config.search.inspirations` with no cap, keep `wanted = 1 if operator == "crossover" else self.rng.randint(1, budget)`, and change the docstring to "1 to `search.inspirations` elites …".
- Modify: `docs/reference.md`. The two "1–2 inspirations" mentions (search loop diagram, operator table) become "1 to `search.inspirations` (default 2)", plus one sentence of cost: each one adds a summary and score line to the prompt, and on a tree problem its whole diff.
- Modify: `evolvekit/evaluate/cascade.py`, `_run_private`. Replace `merged = dict(result.kpis); merged.update(outcome.kpis)` with the private run's own `outcome.kpis`. Before scoring, compute `missing = sorted(k for k in self.config.evaluate.score.weights if k not in outcome.kpis)`. When `missing` is non-empty, count the run as failed (`failed += 1`), set `result.last_failure = f"stage {stage.id}: the hold-out run did not report {', '.join(missing)}; a hold-out score is computed from the hold-out run's own KPIs"` and `continue`. A penalty KPI the private run did not report counts as zero, as on the public run.
- Modify: `evolvekit/evaluate/stages.py`. `_cv(values) -> float | None` returns `None` when the mean is 0 and the spread is not. `_combine` fails on ragged vectors.
- Modify: `evolvekit/evaluate/types.py`. `StageOutcome.kpi_cv` and `EvalResult.kpi_cv` become `dict[str, float | None]`. `max_kpi_cv` takes `max((v for v in self.kpi_cv.values() if v is not None), default=0.0)`.
- Modify: `evolvekit/candidate.py`, where the `kpi_cv` annotation becomes `dict[str, float | None]`.
- Modify: `evolvekit/evaluate/fanout.py`, `_combine`. Average only the non-`None` per-instance CVs; when every one is `None`, store `None`.
- Modify: `evolvekit/preflight.py`. `StageReport.kpi_cv` gets the same annotation. The noisy-stage note's `max` skips `None`. `_kpi_line` renders `" (cv n/a)"` when `cv is None and name in stage.kpi_cv`.
- Test: `tests/test_config.py`, `tests/test_ranking.py`, `tests/test_seeds.py`, new `tests/test_sweep.py`

**Interfaces:**
- Produces: `stages._cv(values: list[float]) -> float | None`.
- Produces: `stages._combine(outcomes) -> StageOutcome`. When two seeds disagree about a vector KPI, it returns `ok=False` with `failure=f"seed {k} reported {len_k} values of `{name}`, seed 0 reported {len_0}"` for the first disagreeing seed `k`. If seed `k` reported no such vector, the failure is `f"seed {k} reported no `{name}`, seed 0 reported {len_0}"`. `duration_s` is the sum and `runs=len(outcomes)`.

- [ ] **Step 1: Write the failing tests.**

In `tests/test_config.py`:

```python
def test_parent_top_k_is_refused_and_points_at_the_archive(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["search"] = {"parent_top_k": 5}
    with pytest.raises(ConfigError, match=r"search\.parent_top_k.*search\.archive\.top_k"):
        build_config(raw, base_dir=tmp_path)
```

If `copy` and `ConfigError` are not already imported in `tests/test_config.py`, add them.

New file `tests/test_sweep.py`:

```python
"""D6, the sweep of small lies: each a promise the code did not keep."""

from __future__ import annotations

from dataclasses import replace

from evolvekit.config import load_config
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.driver import Driver
from tests.conftest import EXAMPLE_CONFIG


def _driver(tmp_path, **search):
    config = load_config(EXAMPLE_CONFIG)
    config = replace(config, search=replace(config.search, scratchpad_every=0, **search))
    provider = FakeProvider(["no code here"], cycle=True)
    return Driver(config, run_dir=tmp_path / "run", providers={"small": provider, "strong": provider}), provider


def test_more_than_two_inspirations_are_drawn_when_asked_for(tmp_path):
    driver, _ = _driver(tmp_path, inspirations=4)
    wanted: list[int] = []
    driver.grid.sample_inspirations = lambda parent, n, rng: wanted.append(n) or []
    seed = driver._seed_candidate()
    for _ in range(200):
        driver._inspirations(seed, "diff")
    assert max(wanted) == 4 and min(wanted) == 1
    wanted.clear()
    for _ in range(50):
        driver._inspirations(seed, "crossover")
    assert set(wanted) == {1}, "crossover is shown one inspiration's code, never more"
```

In `tests/test_ranking.py`, extend the mirror evaluator so the hold-out can report a different KPI set, and add:

```python
def test_the_hold_out_score_uses_the_hold_out_runs_own_kpis(mirror_project, tmp_path):
    """D6: a weighted KPI the private run did not report used to be taken from the public run."""
    config = load_config(mirror_project)
    score = replace(config.evaluate.score, weights={"value": 1.0, "bonus": 1.0})
    config = replace(config, evaluate=replace(config.evaluate, score=score))
    script = (mirror_project.parent / "evaluate.py")
    script.write_text(
        script.read_text(encoding="utf-8").replace(
            "{'value': score}", "({'value': score, 'bonus': 5.0} if a.inputs == 'public' else {'value': score})"
        ),
        encoding="utf-8",
    )
    result = Cascade(config, work_dir=tmp_path / "work").evaluate_generation(
        [_value_candidate("g000-c0001", 1, "human-seed")]
    )["g000-c0001"]
    assert result.private_score is None and result.competes is False
    assert "the hold-out run did not report bonus" in (result.last_failure or "")
```

In `tests/test_seeds.py`, replace `test_ragged_vectors_fall_back_to_the_first_run` and add the `_cv` case:

```python
def test_ragged_vectors_fail_the_stage_and_say_which_seed(tmp_path):
    """A mean over vectors of different lengths would be a fabrication, and so is the first run's alone."""
    body = "payload = {'kpis': {'v': 1.0, 'per': [1.0] * (20 if seed == 1 else 19)}}"
    outcome = _run(tmp_path, body, seeds=2)
    assert outcome.ok is False
    assert outcome.failure == "seed 1 reported 20 values of `per`, seed 0 reported 19"


def test_a_kpi_whose_mean_is_zero_has_no_coefficient_of_variation(tmp_path):
    outcome = _run(tmp_path, "payload = {'kpis': {'v': 1.0, 'centred': float(seed) - 1.0}}", seeds=3)
    assert outcome.kpi_cv["centred"] is None, "no ratio to take; an absolute spread would switch units"
    assert outcome.kpi_cv["v"] == 0.0


def test_a_missing_coefficient_is_shown_as_n_a():
    from evolvekit.evaluate.types import EvalResult
    from evolvekit.preflight import StageReport, _kpi_line

    assert EvalResult(score=0.0, kpi_cv={"a": None, "b": 0.1}).max_kpi_cv == 0.1
    stage = StageReport(stage_id="full", kind="command", ok=True, duration_s=1.0, timeout=60.0,
                        kpis={"centred": 0.0}, kpi_cv={"centred": None})
    assert _kpi_line(stage) == "centred=0 (cv n/a)"
```

- [ ] **Step 2: Run the tests and check that they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py tests/test_sweep.py tests/test_ranking.py tests/test_seeds.py -q`
Expected: FAIL. There is no `parent_top_k` error, `max(wanted) == 2`, the private score is computed with the public `bonus`, the ragged vector falls back, and `kpi_cv["centred"]` is a spread.

- [ ] **Step 3: Implement** the file list above.

- [ ] **Step 4: Run the tests and check that they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py tests/test_sweep.py tests/test_ranking.py tests/test_seeds.py tests/test_instance_fanout.py tests/test_preflight.py tests/test_evaluate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add evolvekit/config.py evolvekit/cli.py examples evolvekit/search/driver.py evolvekit/evaluate/cascade.py evolvekit/evaluate/stages.py evolvekit/evaluate/types.py evolvekit/evaluate/fanout.py evolvekit/candidate.py evolvekit/preflight.py docs/reference.md tests/test_config.py tests/test_sweep.py tests/test_ranking.py tests/test_seeds.py
git commit -m "fix: the sweep of small lies, part 1 (D6)

parent_top_k is refused; inspirations > 2 are honoured; the hold-out
score uses the hold-out run's KPIs; _cv is None at mean 0; ragged seed
vectors fail the stage with the seed named.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A0.6b: The sweep, part 2: big steps say why, and count only when they happened (D6)

**Files:**
- Modify: `evolvekit/prompts.py`. `OPERATOR_INSTRUCTIONS["big_step"]` loses "scheduled … plateaued" and becomes: "This is a big step. Propose a structurally different heuristic rather than a tweak of the parent. Reason about why the parent's scoring rule mis-ranks its options, then write a rule based on a different principle." Add `big_step_reason`.
- Modify: `evolvekit/search/driver.py`:
  - `_plan_operators` stops calling `note_big_step()` and sets `self._big_step_reason`. The value is `big_step_reason(scheduled=scheduled, every=every, stagnant=self.stop_policy.stagnant)` when a big step is planned, and `None` otherwise.
  - `_propose` prefixes `self._big_step_reason` to the hint when `operator == "big_step"`, so the first call gets `"\n\n".join([reason, hint])`. The retry keeps `verdict.retry_hint`.
  - `_breed_one` calls `self.stop_policy.note_big_step()` after `_propose` returns `outcome.ok` for `operator == "big_step"`, meaning the response parsed and applied.
  - `_restore_stop_policy` counts a `big_step*` row only when its response applied. That is every such row except one that is `rejected` with `novelty is None` and whose `block` equals its parent's block, because `_breed_one` writes `parent.block` into a child whose apply failed. See open question 8.
- Test: `tests/test_sweep.py`

**Interfaces:**
- Produces: `prompts.big_step_reason(*, scheduled: bool, every: int, stagnant: int) -> str`. When `scheduled`, it returns `f"This big step is scheduled: one every {every} generation(s)."`. Otherwise it returns `f"This big step is due because the search has not improved for {stagnant} generation(s)."`. Scheduled wins when both triggers hold. Export it in `prompts.__all__`.

- [ ] **Step 1: Write the failing tests.** Append them to `tests/test_sweep.py`:

```python
from evolvekit.prompts import OPERATOR_INSTRUCTIONS, big_step_reason


def test_the_big_step_says_which_trigger_fired():
    assert big_step_reason(scheduled=True, every=3, stagnant=0) == "This big step is scheduled: one every 3 generation(s)."
    assert big_step_reason(scheduled=False, every=0, stagnant=2) == (
        "This big step is due because the search has not improved for 2 generation(s)."
    )
    assert "plateaued" not in OPERATOR_INSTRUCTIONS["big_step"]
    assert "scheduled" not in OPERATOR_INSTRUCTIONS["big_step"]


def test_a_plateau_big_step_is_told_it_is_a_plateau(tmp_path):
    driver, _ = _driver(tmp_path, big_step_every=99, children_per_generation=2)
    driver.stop_policy.stagnant = 2
    plan = driver._plan_operators(1, plateau=True)
    assert plan[0] == "big_step"
    assert driver._big_step_reason == big_step_reason(scheduled=False, every=99, stagnant=2)
    assert driver.stop_policy.big_steps_in_window == 0, "planned is not spent"


def test_the_scheduled_big_step_prompt_names_its_schedule(tmp_path):
    driver, provider = _driver(tmp_path, big_step_every=1, generations=1, children_per_generation=1,
                               operators={"rewrite": 1.0}, inspirations=0)
    driver.run()
    prompts = [call["messages"][1]["content"] for call in provider.calls]
    assert any("This big step is scheduled: one every 1 generation(s)." in p for p in prompts)


def test_a_big_step_that_never_applied_is_not_counted_live_or_on_resume(tmp_path):
    driver, _ = _driver(tmp_path, big_step_every=1, generations=2, children_per_generation=1,
                        operators={"rewrite": 1.0}, inspirations=0)
    driver.run()  # every response is "no code here": nothing ever applies
    assert any(str(c.operator).startswith("big_step") for c in driver.archive)
    assert driver.stop_policy.big_steps_in_window == 0
    again, _ = _driver(tmp_path, big_step_every=1, generations=2, children_per_generation=1,
                       operators={"rewrite": 1.0}, inspirations=0)
    again.resume()
    again._restore_stop_policy()
    assert again.stop_policy.big_steps_in_window == 0
```

- [ ] **Step 2: Run the tests and check that they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sweep.py -q`
Expected: FAIL. `big_step_reason` cannot be imported, and `big_steps_in_window` is 1 after planning.

- [ ] **Step 3: Implement** the file list above.

- [ ] **Step 4: Run the tests and check that they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sweep.py tests/test_economics.py tests/test_diff_prompts.py tests/test_tree.py tests/test_driver_tree.py tests/test_resume_budget.py -q`
Expected: PASS. `tree_prompts.py` reads the same `OPERATOR_INSTRUCTIONS`, so the tree tests cover the new wording.

- [ ] **Step 5: Commit**

```bash
git add evolvekit/prompts.py evolvekit/search/driver.py tests/test_sweep.py
git commit -m "fix(big-step): say which trigger fired; count a big step once it applied (D6)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Final task: quickstart counts, the gate, the PR

A0.1 changes which children are duplicates, A0.2 changes the example's ranking, and A0.5 changes which are near. Any of them can move the quickstart's numbers, so they are measured once, after all six tasks.

**Files:**
- Modify: `docs/reference.md`, the `## Quickstart` paragraph ("The seed heuristic (best fit) scores −5.871; … rejects eleven of its twenty: …"), and the economics table excerpt near line 956 if its row moved.
- Modify: `docs/superpowers/reviews/2026-11-08-algorithms-build-log.md`. Record the base branch (`master` 68b579e), the before and after counts, and the reason for each change.

- [ ] **Step 1: Make sure no full gate is running on the machine.** Then run `python tasks.py run` and note these numbers: the seed score, each improvement, the archive cells, rejected/twenty broken down (no-op, duplicates, behavioural twins, the stage-0 hard reject, unparsable responses), near-duplicates, and the breadth changes.
- [ ] **Step 2: Rewrite the quickstart paragraph with the measured numbers,** keeping its sentence shape. If nothing moved, leave it and say so in the build log.
- [ ] **Step 3: Run the gate.** Run `python tasks.py check`. Expected: lint is clean and every test passes, slow ones included.
- [ ] **Step 4: Commit, push, open the PR.**

```bash
git add docs/reference.md docs/superpowers/reviews/2026-11-08-algorithms-build-log.md
git commit -m "docs: quickstart counts after A0

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin algo/0-correctness
gh pr create --base master --title "A0: correctness (D1-D6)" --body "<D1..D6, each with the test that pins it>

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Decisions

Taken by the controller after the builder check (a fresh reviewer read §3 and this plan
against the code). These override any task text above that disagrees.

1. **The hold-out reference is the last seed row with a private score** (open question 1, as
   proposed): an aborted seed is evaluated again, and `leaderboard._seed_private` becomes the
   shared `seed_private`, so the veto and the `!` flag are one condition. Cost if wrong: none
   when the seed ran once.
2. **Explicit `holdout_penalty: 1.0` lines go** from the examples, the `init` template and
   `docs/new-experiment.md` (open question 2, as proposed).
3. **"Empty delta" means "only constant values changed"** (open question 3, refined). A0.5's
   near gate compares node-type 3-grams, which cannot see a `max` → `min` swap (D1's own
   example: same node types). So: a child whose canonical dump with every `Constant` value
   blanked equals its parent's is `near` its parent at similarity 1.0 (the "change the decision
   rule, not the constants" hint is then true). A child whose node-type delta is empty but whose
   blanked dump differs (a name swap such as `max` → `min`) is **not** near anything: its delta
   vector is empty, cosine with an empty vector is 0. The design's sentence that the exact hash
   catches empty deltas is wrong for constant changes; this is the rule that keeps its intent.
   Tests add: constants-only child is near its parent at 1.0; `max` → `min` child is not near.
   Cost if wrong: a constants-only child costs one re-prompt, as today.
4. **The embedding backend skips `parent_id`** in `closest` (open question 4, refined): it
   accepts the new parent arguments and never returns the parent as a match, so D5's "parent is
   in the index" is closed on both methods.
5. **On a stage without instances each seed is its own case** (open question 5, as proposed).
   The `why` sentence keeps the wording `shared run(s) on N instance(s)` asserted by the slow
   test `tests/test_instance_fanout.py:490`; if the wording must change, that test changes in
   the same task and is run with `-m slow`.
6. **On resume the ranking score is recomputed with the run's current λ.** Rows written before
   A0 stored a `ranking_score` at λ = 1; mixing them with new rows at λ = 0 would put two
   scales in one archive. Wherever fitness is rebuilt from `runs.jsonl` (`fitness_of` /
   `Candidate.fitness` on replay), a row that has both `score` and `private_score` gets
   `ranking_score = score − λ·max(0, score − private_score)` with λ from the current config;
   a row without a private score uses its `score`. Old rows keep their stored `competes`
   (open question 6) — the veto is applied on replay from the rebuilt seed private exactly as
   live, so an old row below the seed's hold-out stops competing after resume too. Test: a
   resumed run whose old rows carry λ = 1 ranking scores orders by the public score. Cost if
   wrong: a resumed pre-A0 run's order changes at resume, which is the fix.
7. **The veto is the literal rule `private < seed_private` in A0**, as design §3 and §8
   decision 4 say. It has no noise margin, so on a stochastic hold-out roughly half of the
   candidates as good as the seed are vetoed. Project A2 (noise-aware search, "every place the
   search compares two numbers knows how noisy they are") adds the margin: the A2 plan must
   make the veto `private < seed_private − 2·σ̂_obj/√seeds` when `σ̂ > 0`. README text in A0
   says the veto compares with the seed's hold-out score and does not claim noise tolerance.
   Cost if wrong: A0 alone is stricter than intended on noisy hold-outs until A2 lands.
8. **Two big-step counts.** `note_big_step()` (attempts) stays where the plan's search for
   patience needs it: `min_big_steps` relief for patience counts **attempted** big steps (its
   message says "tried", which is then true, and a strong model that keeps failing cannot hold
   patience open forever). A new `note_big_step_applied()` counts big steps whose response was
   applied; the economics window guard (A0.4's `big_steps_in_window ≥ 1`, when the run takes big
   steps) and every reported "big steps" count read the applied count. On replay the applied
   count uses open question 8's row shape (a `rejected` row with no `novelty` whose block equals
   its parent's is a failed apply). Cost if wrong: one extra counter.
9. **The private-KPI check covers level KPIs** (open question 7, refined): when `score.levels`
   is set, the hold-out fails with a sentence when it lacks any level KPI; otherwise when it
   lacks any `score.weights` KPI; a missing penalty KPI counts as 0 as on the public run.
10. **The status/confirm agreement holds when no run failed.** `status` does not impute failed
    pairs the way `confirm._analyse` does; A0.3's agreement test uses a run directory with no
    failed pairs and says so in its name and docstring; the build log records the limit.
11. Open questions 9, 10 and 11 as proposed.

## Open questions for the builder check

1. **Which seed row is the hold-out reference.** A0.2 says "the first seed row's `private_score`". `leaderboard._seed_private`, `confirm` and `status` all use the **last** seed row, since an aborted seed is evaluated again and the earlier row is the failed attempt. *Proposed:* use the last seed row with a private score, by promoting `leaderboard._seed_private` to `seed_private`, so that the veto and the `!` flag are the same condition, as the design requires.
2. **The examples pin `holdout_penalty: 1.0` explicitly.** That covers `examples/binpacking`, `circlepacking` and `pyvrp`, plus the `init` template in `cli.py` and `docs/new-experiment.md`. If only the default changes, the quickstart and every new config keep λ = 1. *Proposed:* remove the line from all of them, say in a comment that the veto applies, and update `tests/test_config.py::test_the_base_example_declares_the_phase_b_search`. The quickstart counts are measured afterwards, in the final task.
3. **A0.5 and the empty delta.** The design says the seed matches only children whose delta is empty, "which the exact hash already catches". It does not: a constants-only change has an empty node-type delta but a different AST dump, and that is exactly the case the gate was built for. *Proposed:* a child whose delta against its parent is empty is `near` its parent at 1.0. That is the only case in which the parent is a match. Otherwise the parent is excluded, and the seed (full, untagged vector) never matches a delta. This also keeps the `stubborn_run` driver tests valid whichever parent the RNG picks.
4. **The NearBackend interface.** `add` and `closest` gain the parent arguments. `EmbeddingNearIndex` accepts them and ignores them, because the design scopes the fix to `local`. Three existing unit tests have to index their child with `parent_block=BEST_FIT`. *Proposed:* as planned. Leave the embedding method unchanged for now.
5. **`status` without instances.** The design says both `_verdict` and `_verdict_per_instance` call `paired_summary(per_instance_means(...))`. On a seeds-only stage there are no instances. *Proposed:* `per_instance_means` treats a non-tuple key as its own case, so each seed is a case there, and units stay as they are today (objective units, not percent). The status JSON drops `t` and gains `ci95`. No consumer reads `t`.
6. **Rows that already recorded `competes: true` before the veto existed.** `_settle_competes` judges only rows without `competes`. *Proposed:* do not rewrite recorded verdicts. A resumed older run keeps its earlier winners, and the veto applies from the next candidate on. The build log notes this.
7. **What "a private run that lacks the objective fails the hold-out" means.** It already fails today through `required_kpis`. The real leak is the other weighted KPIs and the penalty KPIs that were taken from the public run. *Proposed:* the hold-out fails when it lacks any `score.weights` KPI, and a missing penalty KPI counts as zero, exactly as on the public run.
8. **"After a big-step response was applied" on resume.** No field records whether the apply succeeded. *Proposed:* live, count on `outcome.ok`. On replay, count every `big_step*` row except one that is `rejected` with no `novelty` and whose `block` equals its parent's block (the shape `_breed_one` writes for a failed apply). The alternative is a new boolean on the row, which is more explicit but a schema change. The builder picks one.
9. **When both big-step triggers fire.** *Proposed:* say "scheduled", because it would have fired anyway.
10. **The design's `progress.verdict`** is `progress.improvement.verdict` in the status document. The tests use the real path.
11. **A0.6 split.** The seven items are split into A0.6a (config, scoring and stage-combination lies) and A0.6b (big-step prompt and counting), because the second touches the driver's loop and its resume replay. *Proposed:* keep two commits under one PR.
