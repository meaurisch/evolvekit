# Setting up a new experiment

A checklist for pointing evolvekit at a new iterative-optimization problem.
It compresses the README's [Adding a problem](../README.md#adding-a-problem)
section into the order an agent should actually work in; every step links to
the README section that explains the why. `examples/binpacking/` is the worked
version of everything below; `examples/pyvrp/` is the heavyweight version
(long stochastic evaluator, external solver, multi-seed stages).

## 0. Scaffold

```
python -m evolvekit init my-problem/
```

writes a commented starter `evolvekit.yaml` and a `.env.example`. Or copy the
closest directory under `examples/` — that is usually faster, because the
examples already have a working evaluator shape, a `fake_responses.yaml` and
the three-config `extends` layout.

A finished experiment directory looks like:

```
my-problem/
  evolvekit.yaml            # the offline config: provider fake — the base everything extends
  evolvekit.real.yaml       # extends evolvekit.yaml, swaps models to a paid backend
  skeleton.py               # the program, with one EVOLVE-BLOCK fence
  evaluate.py               # KPI-emitting evaluator command
  fake_responses.yaml       # canned LLM responses for offline runs and tests
```

## 1. The skeleton

A normal Python file with the evolvable part fenced:

```python
# EVOLVE-BLOCK-START
def priority(item, bins):          # the part the LLM rewrites
    ...
# EVOLVE-BLOCK-END
```

Everything outside the fence is frozen and shown to the model once, in the
cacheable system prompt. **Design the fence narrow**: evolve the scoring rule
inside a fixed algorithm, not the algorithm. Have the frozen part *tolerate* a
bad block — catch, fall back, increment a violation counter — instead of
raising; that counter becomes a penalty KPI and gives the search a gradient
where most candidates land.

## 2. The evaluator

Any command. It receives placeholders (any order) and writes JSON to `{out}`:

| Placeholder | Meaning | Required |
|---|---|---|
| `{candidate}` | path to the spliced candidate module | yes |
| `{out}` | path to write the KPI JSON | yes |
| `{inputs}` | the stage's `inputs` / `private_inputs`, comma-joined | no |
| `{seed}` | `0`, or `0…N-1` on a `seeds: N` stage | only when `seeds > 1` |

```json
{
  "kpis": {"excess_pct": 5.871, "bins_per_instance": [12, 13, 12]},
  "text_feedback": "optional prose; reaches the next prompt as evaluator notes"
}
```

KPIs are numbers or lists of numbers (lists sharpen the behaviour signature
but never reach the score). Accept `--seed` even if you ignore it today —
one argparse line, and `seeds: N` becomes available later without touching
the evaluator. Emit `text_feedback`: a sentence like "losing on the Weibull
instances, using 0.02 % of the time budget" is worth more to the next child
than any vector of numbers
([README: evaluator notes](../README.md#evaluator-notes-what-the-kpis-cannot-say)).

## 3. Stages

Stage 0 is always `builtin-static` (AST + import check, the only hard reject).
Then cheap → expensive with a `promote` rule between:

```yaml
evaluate:
  failure_score: -1000.0
  stages:
    - id: static
      kind: builtin-static
      timeout: 30
      import_check: true
    - id: proxy            # seconds; a subset
      kind: command
      command: "python evaluate.py --candidate {candidate} --inputs {inputs} --out {out} --seed {seed}"
      inputs: [small_set]
      timeout: 120
      promote: {top_k_per_generation: 2}
    - id: full             # the real thing, plus a private hold-out
      kind: command
      command: "python evaluate.py --candidate {candidate} --inputs {inputs} --out {out} --seed {seed}"
      inputs: [full_set]
      private_inputs: [holdout_set]
      timeout: 300
  score:
    objective: excess_pct
    direction: minimize
    weights: {excess_pct: 1.0}
  penalties:
    - {kpi: priority_errors, weight: 2.0, scale: log1p}
  holdout_penalty: 1.0
```

The public/private gap is charged once via `evaluate.holdout_penalty` and
never otherwise optimised against. A stochastic evaluator gets `seeds: N`
(KPIs averaged, per-KPI coefficient of variation recorded). No natural
subset? Run two stages and lean on penalty KPIs — that is what
`examples/circlepacking/` does.

## 4. Score, archive, novelty

- `evaluate.score`: a weighted sum over KPIs; **every score is a finite
  float** — gate violations are penalty terms, not `None`s.
- `search.archive.descriptors`: one or two axes describing *what a candidate
  does*, not how well it scores (`complexity` comes free). A quality-monotone
  axis makes the grid a second copy of the leaderboard
  ([README: the archive](../README.md#the-archive)).
- Fill in `problem.description`, `problem.tools`, `problem.constraints`,
  `problem.what_counts_as_new`. If the skeleton offers a helper the block
  should call, name it in `tools` with its signature — the framework cannot
  infer it, and a model that does not know a tool exists will not use it.

## 5. Models and budget

```yaml
models:
  small:                       # the many cheap calls: diff/rewrite/crossover
    provider: azure
    model: gpt-4o-mini         # azure: model = the *deployment* name
    price_in_per_mtok: 0.15
    price_out_per_mtok: 0.60
    max_tokens: 2048
    temperature: 1.0
  strong:                      # the few big steps
    provider: azure
    model: gpt-4o
    price_in_per_mtok: 3.00
    price_out_per_mtok: 15.00
    max_tokens: 4096
    temperature: 1.0
  # embed: only needed for search.novelty.near.method: embedding

budget:
  max_usd: 5.00                # hard stop, not a warning
  max_tokens: 500000
  max_full_evals_per_day: 6
stop:
  patience: 8
```

Provider choices and their environment variables:
[README: configuring a provider](../README.md#configuring-a-provider). Secrets
come from the environment only — copy `.env.example` to `.env` (gitignored).
Keep the offline config (`provider: fake` + `fake_responses.yaml`) as the base
and fold the paid variant on top via `extends`, so the two can never drift.

## 6. Before spending money

```
python -m evolvekit preflight --config my-problem/evolvekit.real.yaml --provider-check
```

Runs the seed through every stage including the hold-out, prints each stage's
KPIs/notes/duration, checks timeouts and projected wall clock, and
`--provider-check` makes one minimal call per configured model role. Exit 0
clean, 1 warnings, 2 failures. Only then:

```
python -m evolvekit run --config my-problem/evolvekit.real.yaml --run-dir runs/my-problem
python -m evolvekit status --run-dir runs/my-problem
python -m evolvekit leaderboard --run-dir runs/my-problem --html board.html
```

Interrupted or halted runs resume cleanly in the same `--run-dir`: the ledger
and lock pick up where they left off.
