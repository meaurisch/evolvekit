# evolvekit

## What & why

This repo is evolvekit: an LLM-driven evolutionary program-search loop with
staged evaluation, a lineage archive, and a hard cost budget. Its design
inverts the obvious way to use a strong model on a hard optimization problem —
one-shotting a whole design a handful of expensive times against a binary
feasibility signal, with no memory of what worked. evolvekit instead takes
hundreds of cheap steps against a dense, never-`None` score, inside an archive
that remembers who improved whom, under a USD cap that stops the run by
itself. The lineage is the same as FunSearch, MAP-Elites and ShinkaEvolve;
the additions are staged (cheap-to-expensive) evaluation, first-class cost
accounting, and a provider layer that runs equally on Azure OpenAI / AI
Foundry, OpenRouter, or a local Claude Code subscription.

## Quickstart

```
python tasks.py run
```

That runs the bin-packing example for six generations against the `fake`
provider — no network, no API keys, about ten seconds — and prints a
leaderboard, an economics table and a spend summary. The seed heuristic (best
fit) scores −5.871; the run improves twice, to −5.620 and then to −5.370,
spreads its candidates across five archive cells, and rejects eleven of its
twenty: one no-op, six duplicates, two behavioural twins, one block that tried
to `import os` (stage 0, the only hard reject) and one response that came back
in neither of the two formats it was asked for. One further child is flagged as
a near-duplicate and evaluated anyway. Along the way the breadth grows from
three children to four on the plateau and shrinks back to three once the search
starts climbing again.

```
python tasks.py test          # 548 offline tests, about two minutes
python tasks.py check         # same as test; the CI gate

python -m evolvekit init my-problem/                     # scaffold a config
python -m evolvekit preflight --config examples/binpacking/evolvekit.yaml
python -m evolvekit run --config examples/binpacking/evolvekit.yaml
python -m evolvekit run --config examples/circlepacking/evolvekit.yaml \
    --run-dir runs/circles                               # the second example
python -m evolvekit status --run-dir runs/demo
python -m evolvekit leaderboard --run-dir runs/demo --html board.html
```

Run `preflight` before any run you are paying for. It puts the seed through
every stage, prints what each one costs and reports, and checks the timeouts
and the budget against those measurements — see
[Before you spend anything](#before-you-spend-anything-preflight).

A run directory holds `runs.jsonl` (one row per candidate, with lineage, cell
and novelty verdict), `usage.jsonl` (one row per LLM call, with tokens and
USD), `archive.json` (the MAP-Elites grid: cells, elites, children counts and
lineage), `scratchpad.md` (the running notes), `.lock` (the owning pid) and
`traces/<candidate-id>.json` (the exact messages sent and the response
received; framework calls live under `traces/meta/`).

### How it works

```
seed = the skeleton's EVOLVE-BLOCK
  │
  ├─ generation g       C children; C adapts on stagnation (optional)
  │    sample P parents from the archive's elites
  │                  weight = sigmoid(z(fitness)) × 1/(1 + children)
  │    draw 1–2 inspirations from *other* cells
  │
  │    operators   diff (SEARCH/REPLACE) · rewrite · crossover → small model
  │                big_step, every K gens or on plateau        → strong model
  │                param_lhs, sweeps the block's # PARAMS      → no LLM call
  │
  ├─ novelty filter   AST-normalised hash, before any evaluation
  │                   = parent → no-op · = any archived block → duplicate
  │                   one re-prompt, then the child is written off
  │                   ≥ threshold similar → near: re-prompted once, then
  │                   evaluated anyway and flagged
  │
  ├─ stage 0  static   AST + import check      ms      the only hard reject
  ├─ stage 1  proxy    small instance subset   seconds top-k promote
  ├─ stage 2  full     everything + hold-out   longer  budget-metered
  │      after each command stage: hash the KPIs it reported
  │      = anything seen at that stage → behavioural duplicate, stop here
  │
  ├─ insert into the archive: one elite per cell, ranked by
  │     public − holdout_penalty × max(0, public − private)
  │
  ├─ economics: cumulative USD, best, USD since the last improvement,
  │             USD per unit of gain over a sliding window
  │
  └─ stop on budget.max_usd / max_tokens, stop.patience, stop.target,
     stop.max_usd_since_improvement or stop.min_gain_per_usd
```

Every score is a finite float. A gate violation is a penalty term; a crash
below stage 0 is `evaluate.failure_score`; only stage 0 and the two novelty
filters reject outright. The structural filter's rejections cost no evaluator
time at all; the behavioural one costs exactly the stage that caught it.
There is one LLM call per child and one attempt at applying its response — the
single exception is the novelty re-prompt, bounded to one extra call, because
v1's nested 3×3 retry was the largest single multiplier on its token bill.

### The archive

`search.archive.descriptors` is a list of axes, each naming a KPI the evaluator
already emits, a bin count, and either an explicit `range: [lo, hi]` or
`range: auto` (which widens and re-bins as the run discovers values):

```yaml
search:
  archive:
    top_k: 20
    descriptors:
      - kpi: complexity          # free: the static stage emits it
        bins: 4
        range: auto
      - kpi: mean_openness       # behaviour, from your evaluator
        bins: 4
        range: [0.0, 0.25]
```

Each cell keeps one elite — the best candidate that landed in it — and the
archive keeps a global top-k besides. A candidate only ever displaces the elite
of its own cell, which is what stops a run collapsing onto a single lineage.

Choose the second axis for *behaviour*, not quality. The bin-packing example
uses `mean_openness`: where the chosen bin sat between the tightest and the
emptiest feasible one, 0 for always-tightest (best fit) and 1 for
always-emptiest. Two rules can score identically and sit at opposite ends of
it. `bins_used` or a mean fill ratio would have been monotone in the objective
and would have made the grid a second copy of the leaderboard.

The grid is persisted to `archive.json` and rebuilt from `runs.jsonl` when a
run resumes — the snapshot is a convenience, the log is the record. `status`
and `leaderboard` print cell occupancy; the HTML dashboard draws the grid.

The `cells=` figure printed each generation is the occupancy *after* that
generation's inserts, and it can go **down**: an axis with `range: auto` widens
when it sees a new value, which re-bins every member the grid already holds and
can merge two cells into one. A falling count is the grid rescaling, not
candidates being lost.

### Operators

| Operator | Model | What it sees |
|---|---|---|
| `diff` | small | the parent block, its score breakdown, delta summaries of 1–2 inspirations |
| `rewrite` | small | the same, and asks for the whole block back |
| `crossover` | small | the parent **and** one inspiration's block, both with score breakdowns |
| `big_step` | strong | scheduled every `big_step_every` generations, or on plateau |
| `param_lhs` | none | a Latin-hypercube variant of the block's own constants |

Shares come from `search.operators`. Inspirations are drawn from cells other
than the parent's and are shown as one-line delta summaries — a unified-diff
stat against their own parent plus the first line of their docstring, both
computed locally. Crossover is the one operator that also sees an inspiration's
code, because it cannot combine two candidates otherwise.

`param_lhs` costs no tokens. It needs a declaration inside the evolve block:

```python
# PARAMS: {"BONUS": [0.0, 40.0], "WIDTH": [4.0, 400.0]}
BONUS = 20.0
WIDTH = 128.0
```

Blocks without that line are never routed to it, so listing it in
`search.operators` against a skeleton that declares nothing is harmless.

### The problem description, in named sections

`problem.description` is prose and stays prose. Three optional fields beside it
are *structure*, and each becomes its own heading in the cacheable half of the
system prompt:

```yaml
problem:
  description: |
    ...what the problem is, what the fixed part does, how it is scored...
  tools:
    - >
      repair(centers, radii) -> (centers, radii): the referee, callable from
      your block. sum_radii(repair(c, r)) is exactly the score that
      arrangement would earn, so you can score your own moves.
    - >
      deadline(seconds) and expired(when): the stop condition for a
      time-bounded loop. Write `until = deadline(TIME_BUDGET_S)` once and
      `while not expired(until):` around your search.
  constraints:
    - Standard library only, and no numpy.
    - Stop at TIME_BUDGET_S. The stage timeout is a hard kill, not a curve.
  what_counts_as_new: >
    A new candidate ends up with a different arrangement of the 26 circles.
    Re-typing the same grid with different literals produces the same 26 radii
    and is worth nothing.
```

`tools` is what the evolve block may call or rely on — helpers in the fixed
part, time-budget constants, allowed imports. `constraints` is what it must
not do. `what_counts_as_new` is the problem-specific half of a sentence the
framework can only say generically: the behavioural filter below can tell a
model that it re-expressed the same rule, but only the problem's author can say
what a genuinely different rule *looks like* here. Bin packing's answer is
"only a change to which bin wins is a change, because the skeleton takes the
argmax"; circle packing's is "a different arrangement of the 26 circles".

Both accept a YAML list or a `|` block. All three come from the config, so the
prompt's stable prefix stays byte-identical across a run and prefix caching
keeps working. An unset field renders nothing at all — no empty heading, no
"none".

**These exist because of the second circle-packing run.** Fifteen candidates,
every one of them a hand-placed arrangement scoring 1.82–2.48 against a 2.54
seed, including the Sonnet big step, which reproduced the seed's own 5×5 grid.
The skeleton had offered `deadline()`, `expired()` and `TIME_BUDGET_S = 2.0`
from the first commit, and the evolve block's docstring even mentioned them.
Nothing in a place the model would land said *use them*. The example now says
so under its own heading, in as many words, with a five-line sketch of the
perturb / repair / accept loop.

### Evaluator notes: what the KPIs cannot say

A command stage's output JSON may carry a `text_feedback` string beside its
`kpis`:

```json
{
  "kpis": {"excess_pct": 5.871, "priority_errors": 0},
  "text_feedback": "or_like: 5.913% excess over L1 across 12 instance(s)\nweibull: 5.808% across 8\nworst single instance: full-or-8 at 8.571%"
}
```

The framework truncates it to 2,000 characters, keeps it per stage on the
candidate (`feedback`), and puts the parent's **deepest non-empty** note into
the next prompt as an `## Evaluator notes` section directly under the score
breakdown, bounded again to twenty lines. Deepest, because the proxy stage's
note is about five instances and the full stage's is about twenty. Both bounds
are the framework's rather than the evaluator's: the size of a run's prompts
must not depend on how chatty somebody's evaluator is feeling.

Stage artefacts are the single most useful thing a prompt can carry, and
before `text_feedback` existed the loop only had them for stages that
*failed*. A vector of numbers is a poor channel for "you are losing on the
Weibull instances and winning on the uniform ones" and a hopeless one for "you
used 0.02 % of your time budget". Both example evaluators emit something worth
reading:

| Example | What it says |
|---|---|
| bin packing | excess split by distribution, the worst single instance by name, where the rule sat between the tightest and emptiest feasible bin, and any `priority()` incidents |
| circle packing | minimum pairwise gap after repair, how many circles are pressed against a wall, and **what fraction of `TIME_BUDGET_S` the candidate actually spent** — with a sentence naming the fix when that fraction is under 5 % |

### Stochastic evaluators: `seeds: N`

A command stage may declare `seeds: N` (default 1) alongside a `{seed}`
placeholder in its command:

```yaml
- id: full
  kind: command
  command: "python evaluate.py --candidate {candidate} --inputs {inputs} --out {out} --seed {seed}"
  inputs: [full]
  timeout: 9000     # per run, not per stage
  seeds: 3
```

The stage then runs three times with `{seed}` as 0, 1, 2 — each under its own
`timeout`, each writing its own output file. Scalar KPIs are averaged, vector
KPIs are averaged element by element, the **first** run's `text_feedback` is
kept (three notes about one program is three times the prompt for one fact),
and the spread of each KPI is recorded as `kpi_cv`, a coefficient of variation,
on the outcome, the result and the candidate row. It is never scored: the
search ranks by the mean, and `kpi_cv` is there so a human can see when the
mean was a coin toss. The first failing run ends the stage and the failure names
the seed it happened on — a mean over whichever runs survived would report a
number the candidate never achieved.

`seeds: N > 1` without a `{seed}` placeholder is a config error, because N
identical runs is N times the bill for one sample.

**The behaviour signature has to give ground, and this is the important part.**
A stochastic evaluator never fingerprints identically. A different random
restart, a tie broken by a different hash order, a solver that stopped a
microsecond later — and the eighth significant digit has already moved. At the
default nine digits every candidate on such a stage looks novel, and the
behavioural filter, the one thing standing between an hour-long evaluator and a
re-expression of its own seed, silently stops filtering. So on a stage with
`seeds > 1` the fingerprint is built from the *means* and rounded to
`evaluate.signature_digits_stochastic` (default `3`) instead. Three significant
digits on a mean of N runs is a claim about the program; nine is a claim about
the dice.

### The novelty filters — structural, near, and behavioural

Three of them, because a child can repeat itself in three different ways.

**Structural**, before a child is evaluated. Its block is parsed, stripped of
docstrings and comments, its local names canonicalised to `v0, v1, …`, and the
result hashed. A child whose hash matches its parent's is a **no-op**; one that
matches any archived candidate is a **duplicate**. Both are written to
`runs.jsonl` with `rejected: true`, a `reject_reason` naming the twin and a
`twin_id`, and neither enters the archive, the parent pool, or the evaluator.

With `search.novelty_retry: true` (the default) the model gets exactly one
re-prompt — "that was identical to `<id>`; propose something different" — before
the child is written off.

**Near-duplicate**, between the structural hash and the evaluator. The exact
hash is all-or-nothing, and the second real run kept paying full evaluations
for children that were an earlier candidate with one constant moved — a
different AST dump, a different hash, and nothing new. So a *similarity* gate
sits in front of the cascade:

```yaml
search:
  novelty:
    near:
      method: local       # local | embedding | off   (default: local)
      threshold: 0.97
      # model: text-embedding-3-small   # embedding only; defaults to models.embed.model
```

| Method | How | Cost |
|---|---|---|
| `local` (default) | cosine over the multiset of node-type 3-grams of the **canonicalised** AST — the same canonicaliser the exact hash uses. Node *types* only, so a block that differs from an earlier one in nothing but its constants scores exactly **1.00**. | nothing: no call, no key, no dependency |
| `embedding` | cosine over vectors from `Provider.embed`, for when a semantic judgement beats a syntactic one | one embedding call per new block, billed |
| `off` | — | the Phase B behaviour |

**A near-duplicate is never rejected.** It gets the same single re-prompt a
structural duplicate gets — "closest to `<id>` at similarity 0.98 — change the
decision rule, not the constants" — and if the retry is *still* near, the child
is **evaluated anyway** and flagged: `novelty: "near"`, `near_twin_id`,
`similarity`. That asymmetry is deliberate. Run 2 found nothing at all in twelve
novel samples; a filter that can starve a twelve-sample search is worse than one
that occasionally pays for a cousin. The counts appear in the run summary,
`status`, and both leaderboards, separately from the rejections, because a
near-duplicate costs an evaluation rather than wasting one.

`param_lhs` skips the gate entirely: varying constants and nothing else is that
operator's whole job, and calling its output a near-duplicate would be a
category error.

**Behavioural twins become dead ends in every later prompt.** A twin first
leaves a one-off note for its own parent; it also adds a one-line fingerprint
(its last line of code, the parent it came from, the program it matched) to an
"Already tried" section that every subsequent prompt carries, capped at the six
most recent. The second real Phase C bin-packing run is why: nine twins of
best-fit from four different parents — `** 0.9`, `sqrt`, `+ 0.01 * b`, tie-break
epsilons — each a re-scaling that cannot change which bin wins. The list
survives a resume (it is rebuilt from `runs.jsonl`).

The `embedding` method needs a third model slot. `openrouter` and `azure`
implement `embed()` through the OpenAI embeddings API; `fake` returns
deterministic hash-seeded vectors; `claude-cli` has no embeddings endpoint and
says so, and `load_config` rejects that combination before a run starts rather
than three generations in:

```yaml
models:
  embed:
    provider: openrouter
    model: text-embedding-3-small
    price_in_per_mtok: 0.02     # only the input price is ever read
```

Every embedding call lands in `usage.jsonl` as an `operator: "embed"` row with
its tokens and its USD, so the budget guard, the totals and the economics
series all pick the spend up without knowing embeddings exist.

**Behavioural**, after every *command* stage. The stage's KPIs are rounded to
`evaluate.signature_digits` significant digits (default `9`), the KPIs named in
`evaluate.signature_ignore` (default `[runtime_s, complexity, static_problems]`
— anything timing- or size-related) are dropped, and what is left is hashed into
a **behaviour signature**. If any candidate in the run, the seed included, has
the same signature at the same stage, the child is a **behavioural duplicate**:
`rejected: true`, a `behaviour_twin_id`, and `reject_reason: "behavioural
duplicate of <id> at stage <stage>"`. Its KPIs and its score stay in
`runs.jsonl` for the record, but it is not promoted to the next stage, it never
enters the archive's elites or the parent pool, and the parent's *next* prompt
carries a one-line artefact saying the previous child re-expressed the same rule
rather than changing it. A list-valued KPI (the example evaluator emits
`bins_per_instance`) is folded into the signature element by element, so the
fingerprint is per-instance rather than per-mean; an evaluator that emits only
scalars is unaffected.

That last filter exists because of the 2026-08-22/23 run: five paid children
were monotone re-expressions of best fit — `-(b - item) ** 1.01`, `item - b`
and friends — which the AST filter passes and which score the seed's score to
the last digit. All five were promoted to the full stage and entered the
archive. On this example that costs milliseconds; on the VRP target problem a
full-stage evaluation costs hours.

`status`, the run summary and both leaderboards report the three rejection
counts as `rejected: N (x no-op, y duplicate, z behavioural)` and the flagged
near-duplicates on a line of their own. In the first real run about 40 % of the
paid calls bought nothing; that is the number these are here to drive down.

### The hold-out penalty

`evaluate.holdout_penalty` (default `1.0`) defines the score everything ranks
by:

```
ranking = public − penalty × max(0, public − private)
```

The archive, parent sampling, the leaderboard and `summary.best` all use it.
The raw `public_score`, `private_score` and `generalization_gap` stay in the
record untouched — the penalty changes what wins, never what is reported.
Separately, the leaderboard flags with `!` any candidate whose private score is
below the **seed's**: that is the 2026-08-22 failure, where the winner improved
the public set and its hold-out went backwards relative to where it started.

### Adaptive breadth

How many children a generation gets can be left to the run:

```yaml
search:
  children_per_generation: 3      # the starting point
  adaptive_children:              # absent by default
    min: 3
    max: 6
    grow_after: 2                 # flat generations before adding a child
    shrink_after: 1               # improving generations before giving one back
```

After `grow_after` consecutive generations without an improvement the breadth
grows by one, up to `max`; after `shrink_after` consecutive improving
generations it shrinks by one, down to `min`. The two counters are mutually
exclusive and each resets the other, and every generation logs the decision and
its reason:

```
gen 3  3 child(ren)  best=-5.61986  cells=4  spent=$0.0036
        breadth 3 -> 4 (grow: 2 generation(s) without improvement; ceiling 6)
gen 4  4 child(ren)  best=-5.37031  cells=5  spent=$0.0124
        breadth 4 -> 3 (shrink: 1 improving generation(s); floor 3)
```

This is TurboEvolve's K-adaptation, reduced to the smallest rule that keeps the
property worth having: breadth is worth little while the search is climbing —
one good child per generation is enough and the rest of the budget is spent on
also-rans — and it is the only way out of a plateau. `search.children_per_generation`
is the starting point, clamped into `[min, max]`; without the block, the
breadth is fixed and nothing is logged.

### Economics: USD per unit of improvement

`evolvekit/economics.py` projects `runs.jsonl` and `usage.jsonl` into one point
per generation. It is a pure view — no state, no calls — and it is what answers
the question neither real run could answer while it was running. Run 1 paid
$0.83 for +0.345; run 2 paid $0.83 for nothing. That difference is a ratio, and
a ratio needs a denominator per generation.

Per generation: cumulative USD and calls, the best *ranking* score so far, the
delta, the USD spent since the best last moved, and the gain per USD over a
sliding window `W`. It appears in three places — a compact table under the
markdown leaderboard, a dependency-free inline-SVG chart of best score against
cumulative USD in the HTML dashboard (one dot per generation, improving ones in
the warning colour, generation ticks under the axis), and the tail printed by
`run` and `status`:

```
 gen  calls      cum $         best      d best  $ since imp     $/unit (w3)
----------------------------------------------------------------------------
   0      0     0.0000       -5.871           .       0.0000         no gain
   1      4     0.0011     -5.61986     +0.2511       0.0000    $0.0044/unit
   2      9     0.0024     -5.61986           .       0.0013    $0.0097/unit
   3     13     0.0036     -5.61986           .       0.0025    $0.0145/unit
   4     18     0.0124     -5.37031     +0.2495       0.0000    $0.0452/unit
```

Two stop rules read the last point of that series. Both are off by default:

```yaml
stop:
  max_usd_since_improvement: 0.30    # patience, denominated in money
  min_gain_per_usd:                  # score units per USD over a window
    window: 3
    threshold: 0.5
```

Neither may fire until at least **one big step has been spent since the last
improvement**. A plateau's first answer is the strong model, not the exit —
the same guard `stop.min_big_steps` puts on `stop.patience`, hard-wired to one
here. `stop.patience` still wins when both would fire, so the reason printed is
always the first one that became true.

`stop.min_gain_per_usd.window` also sets the window the whole series is
computed over; without it the window is three generations, which is
TurboEvolve's stagnation window and the same judgement for the same reason.

### The meta-scratchpad

Every `search.scratchpad_every` generations (default 5, `0` turns it off) the
small model is shown the current elites' delta summaries and score lines — never
their source — and asked for at most 40 lines of lessons. The result is cached
in `<run_dir>/scratchpad.md` and pasted into every prompt body until the next
refresh. It is the only part of the prompt that carries memory across
generations, which is exactly why it is capped in lines.

### Sharing one config between two runs

A config may declare `extends: <path>` — a sibling YAML it folds itself onto.
Mappings merge key by key; anything else replaces outright, and an explicit
`null` clears an inherited mapping (`options: null` drops the base file's fake
provider options). Relative paths such as `problem.skeleton` always resolve
against the file you *loaded*, so a base and its variant belong in the same
directory.

`examples/binpacking/evolvekit.claude.yaml` is the whole point: it is the same
example run through a real backend, so it declares `models` and `budget` and
inherits `problem`, `evaluate`, `stop` and `search` from `evolvekit.yaml`. The
Phase B evidence run was bought against a *stale copy* of that file — no
hold-out penalty, no crossover, no archive descriptors — and the run silently
searched a Phase A landscape. `extends` makes that impossible, and
`tests/test_config.py` asserts the three shared sections parse identically
either way.

### The run lock

`run` takes `<run_dir>/.lock` and holds it for the duration. A second run
against the same directory refuses, naming the pid that owns it and exiting 3.
A lock whose owner is no longer alive is reclaimed automatically, so a crashed
run never needs manual cleanup.

## Before you spend anything: `preflight`

```
python -m evolvekit preflight --config examples/binpacking/evolvekit.yaml
```

It runs the **seed candidate** through every stage — private hold-out included
— in a temporary directory, with no run lock, no run directory and no LLM
calls, and prints each stage's KPIs, its evaluator note and how long it took.
Then it checks the config against what it just measured:

* a stage whose `timeout` is under **1.5×** the seed's own duration is a
  warning. A child that does more work than the seed is the normal case, and a
  timeout with no headroom starts killing the search's better ideas;
* `budget.max_full_evals_per_day` × the observed full-stage duration over 24 h
  is a warning that names the number which *would* fit. Twenty full evaluations
  a day at 2.5 hours each is fifty hours;
* a run whose `generations` × promoted candidates exceeds the daily cap is told
  how many days of calendar time it spans;
* projected evaluator wall clock for the whole run — a note under a day, a
  warning over it;
* on a `seeds: N` stage, how much the noisiest KPI moved between the runs.

`--provider-check` is the only part that spends anything: one minimal call per
configured model role, `embed` included when a slot exists, with the prompt
`Reply with the single word OK`, reporting tokens, USD and latency. Use it once
against a new key or a new deployment, not before every run.

Exit codes are **0 clean / 1 warnings / 2 failures**, so a wrapper script can
gate on it:

```
stage static               ok      0.2s  (timeout 30s per run)
    kpis    : complexity=427, static_problems=0

stage full                 ok      0.3s  (timeout 300s per run)
    kpis    : bins_used=2144, excess_pct=5.871, instances=20, ... 4 more
    | or_like: 5.913% excess over L1 across 12 instance(s), best 3.922%, worst 8.571%
    | weibull: 5.808% excess over L1 across 8 instance(s), best 4.762%, worst 6.931%
    | worst single instance: full-or-8 at 8.571% excess

note    : projected evaluator wall clock for the whole run: about 19.3s, excluding model latency.

verdict : clean (0 failure(s), 0 warning(s))
```

## Configuring a provider

Secrets are read from environment variables only. Copy `.env.example` to `.env`
(gitignored) and fill in what you need. Each `models.small` / `models.strong`
slot names a backend, a model, and its prices, which is what the ledger uses to
turn tokens into USD.

| Backend | Environment | Notes |
|---|---|---|
| `azure` | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` (or `AZURE_OPENAI_KEY`; leave empty for keyless Entra ID via `evolvekit[entra]`), `AZURE_OPENAI_API_VERSION` (optional, defaults `2024-10-21`) | `openai.AzureOpenAI`, against classic Azure OpenAI **and** Azure AI Foundry resources — any of the `openai.azure.com` / `cognitiveservices.azure.com` / `services.ai.azure.com` endpoint forms; pasted portal paths are stripped. `models.<role>.model` is the **deployment** name. Sends `max_completion_tokens`, falling back once to `max_tokens` on older API versions. Implements `embed()`. Contract-tested against recorded responses in `tests/test_azure_contract.py`. |
| `openrouter` | `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL` (optional) | `openai.OpenAI` with `base_url=https://openrouter.ai/api/v1`. Implements `embed()`. |
| `claude-cli` | none; `CLAUDE_CLI_PATH` only if the binary is off `PATH` | Runs `claude -p --output-format json` as a subprocess on the local subscription's own auth. **No `embed()`** — the CLI is completions only, and config validation rejects `search.novelty.near.method: embedding` against it. The prompt goes in on stdin, so a long big-step prompt cannot hit the Windows argv limit. Reports real spend via `total_cost_usd`, which the ledger prefers over the price table. Its own system context costs ~13k cached input tokens per call even with `--system-prompt` set. |
| `fake` | none | Replays a scripted list of responses from `options.responses` or `options.responses_path`. `embed()` returns deterministic hash-seeded vectors. Used by the tests and by `python tasks.py run`. |

```yaml
models:
  small:
    provider: openrouter
    model: anthropic/claude-haiku-4.5
    price_in_per_mtok: 1.00
    price_out_per_mtok: 5.00
  strong:
    provider: claude-cli
    model: sonnet
    price_in_per_mtok: 3.00     # ignored: the CLI reports actual spend
    price_out_per_mtok: 15.00
```

### When a backend says no

The two OpenAI-shaped backends share one client (`providers/openai_shape.py`)
with a **bounded retry: three attempts, sleeping 2 s then 4 s, on HTTP 429 and
5xx only**. Nothing else is retried — a 401 will still be a 401 in eight
seconds, and a content filter will still be a content filter. A `Retry-After`
from the backend wins over the ladder but is capped at its tail (8 s), so a
server asking for five minutes cannot silently park the run; past that the
error surfaces and `budget.max_consecutive_provider_errors` takes over. The
bound is the design: v1's largest single multiplier on its token bill was a
nested 3×3 retry.

Failures are classified before they are raised, so the message names the cause
rather than the exception class:

| What happened | What the run prints |
|---|---|
| HTTP 429 | `rate limited (HTTP 429) after 3 attempt(s); the backend asked for 30s: …` |
| HTTP 5xx | `the backend returned a server error (HTTP 503) after 3 attempt(s): …` |
| bad key | `authentication failed (HTTP 401) — check the API key and endpoint in the environment: …` |
| content filter | `the request was blocked by the content filter (HTTP 400): …` |
| a filtered 200 | `the response was blocked by the content filter (finish_reason=content_filter)` |
| unknown model | `the request was rejected as invalid (HTTP 404) — check the model or deployment name: …` |

## Running for real

Two patterns, both of them worked examples you can copy.

### 1. Cheapest breadth: `openrouter` for small and embed, `claude-cli` for strong

`examples/binpacking/evolvekit.openrouter.yaml` and
`examples/circlepacking/evolvekit.openrouter.yaml`. Both `extends` the offline
config, so the problem, the evaluator and the search are identical and cannot
drift; only the backends and the money differ. Fill in the two model ids —
they are `<openrouter-model-id>` placeholders on purpose — and the real prices,
because the ledger bills from that table and a wrong price silently breaks
`budget.max_usd`.

```
OPENROUTER_API_KEY=...        # models.small and models.embed
                              # claude-cli needs nothing: it carries the
                              # subscription's own auth
```

Roughly 90 % of a run's calls go to `small`, so that slot's price *is* the
run's price. The strong model is called on a schedule and on plateau, which is
few enough calls that the CLI's overhead does not dominate.

> **Never run two `claude-cli` jobs at once.** The subscription's five-hour
> session window is shared, so a second job does not queue behind the first: it
> eats the same allowance, and both runs hit the cap together, halfway through.
> The first Phase C run met the cap after one call and then bred 22 children
> into a dead backend before `budget.max_consecutive_provider_errors` existed.
> One job, one window.

> **`claude-cli` carries ~13k tokens of CLI context per call**, cached, whatever
> your prompt says and even with `--system-prompt` set. On a 6k-token prompt
> that is two thirds of the input bill. It is why the small slot belongs on a
> key and the strong slot on the subscription, and why `budget.max_tokens` on a
> CLI run needs headroom that a token count of your own prompts would not
> suggest.

### 2. Everything on `azure` (Azure OpenAI / Azure AI Foundry)

The all-Azure form for a machine where Azure is the only route out — a
locked-down corporate laptop with an Azure AI Foundry resource, say. Both
slots are Azure deployments; `models.<role>.model` is the **deployment**
name, not the model name.

```
# the resource root — openai.azure.com, cognitiveservices.azure.com and
# services.ai.azure.com all work, pasted portal paths are stripped
AZURE_OPENAI_ENDPOINT=https://<resource>.cognitiveservices.azure.com
AZURE_OPENAI_API_KEY=...               # or AZURE_OPENAI_KEY; leave empty
                                       # for keyless Entra ID auth
AZURE_OPENAI_API_VERSION=2024-10-21    # optional
```

On a Foundry resource with key auth disabled, set only the endpoint and
`pip install 'evolvekit[entra]'`: the provider authenticates through
`DefaultAzureCredential` (`az login`, managed identity, whatever the machine's
policy provides).

```yaml
extends: evolvekit.yaml

models:
  small:
    provider: azure
    model: <your-cheap-deployment>
    price_in_per_mtok: 0.15
    price_out_per_mtok: 0.60
    max_tokens: 2048
    options: null
  strong:
    provider: azure
    model: <your-strong-deployment>
    price_in_per_mtok: 2.50
    price_out_per_mtok: 10.00
    max_tokens: 4096
    options: null
  embed:                      # only for search.novelty.near.method: embedding
    provider: azure
    model: <your-embedding-deployment>
    price_in_per_mtok: 0.02
    options: null
```

Azure reports no cost, so the price table above is what the ledger bills from.
Run `preflight --config … --provider-check` once after setting the deployments
up: a deployment name that does not exist comes back as `the request was
rejected as invalid (HTTP 404)`, and that is much cheaper to find out now.

### Economics for an hour-long evaluator

The defaults are tuned for an evaluator that costs milliseconds. When one full
evaluation is hours rather than milliseconds, the arithmetic changes: LLM spend
stops being the binding constraint and *calendar time* becomes it. A candidate
that buys nothing has cost a slot in a 24-hour day, not a fraction of a cent.

```yaml
budget:
  max_usd: 20.00
  max_tokens: 5000000
  # The real cap. Set it from what `preflight` measured, not from a guess:
  # this many x one full evaluation must fit inside a day.
  max_full_evals_per_day: 8

stop:
  patience: 8            # NOT the default 5. See below.
  epsilon: 0.001
  min_big_steps: 1       # a plateau's first answer is the strong model
  max_usd_since_improvement: 2.00
  # min_gain_per_usd: {window: 3, threshold: 0.5}   # once you know the units

search:
  children_per_generation: 3
  adaptive_children: {min: 3, max: 8, grow_after: 2, shrink_after: 1}

evaluate:
  stages:
    - id: static
      kind: builtin-static
      timeout: 60
    - id: proxy
      kind: command
      command: "... --seed {seed}"
      timeout: 600          # >= 1.5x what preflight measured
      promote: {top_k_per_generation: 2}
    - id: full
      kind: command
      command: "... --seed {seed}"
      timeout: 12000        # per run
      seeds: 2              # a noisy objective needs a mean, not a sample
      private_inputs: [holdout]
  signature_digits_stochastic: 3
```

**`patience ≥ 8`.** The second real Phase C run gave up on `patience: 4` after
twelve novel samples. Twelve samples is not a search, it is a sighting. An
hour-long evaluator gets fewer samples per unit of wall clock than anything
else this framework has been pointed at, so the patience counter has to be
generous and the *money* caps have to be the real stop.

**`adaptive_children`.** Breadth is worth little while the search is climbing —
one good child per generation is enough — and it is the only way out of a
plateau. Letting the run move it means the plateau gets the extra samples and
the climb does not pay for them.

**`max_usd_since_improvement`.** Patience denominated in money, which is the
unit a plateau actually costs. It refuses to fire until at least one big step
has been spent since the last improvement, so it can never end a run on the
generation before the strong model would have been tried.

**`max_full_evals_per_day`.** A hard stop, not a warning, and the one number
`preflight` will argue with you about.

**Stage timeouts.** At least 1.5× what `preflight` measured on the seed, and
remember that a `seeds: N` stage's timeout is **per run**: `seeds: 2` with a
`timeout: 12000` is up to six and a half hours of wall clock for one candidate.

## Adding a problem

1. **Write a skeleton.** A normal Python file with the part you want evolved
   fenced between `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END`. Everything
   outside the fence is frozen and is shown to the model once, in the cacheable
   system prompt. Design the fence narrow: evolve the scoring rule inside a
   fixed algorithm, not the algorithm.
2. **Make violations countable.** Have the fixed part tolerate a bad block —
   catch, fall back, and increment a counter — rather than raising. That
   counter becomes a penalty KPI, which is what gives the search a gradient in
   the region where most candidates land.
3. **Write an evaluator.** A command accepting these placeholders, in any
   order, and writing a JSON object to `{out}`:

   | Placeholder | What it is | Required |
   |---|---|---|
   | `{candidate}` | path to the spliced candidate module | yes |
   | `{out}` | path the KPI JSON must be written to | yes |
   | `{inputs}` | the stage's `inputs` (or `private_inputs`), comma-joined | no |
   | `{seed}` | `0`, or `0 … N-1` on a stage with `seeds: N` | only when `seeds > 1` |

   The document it writes:

   ```json
   {
     "kpis": {"excess_pct": 5.871, "bins_per_instance": [12, 13, 12]},
     "text_feedback": "optional prose, truncated to 2000 chars by the framework"
   }
   ```

   A KPI is a number, or a *list* of numbers — one entry per instance, say.
   Lists never reach the score or the archive descriptors, which need scalars;
   they sharpen the behaviour signature, so two candidates that merely agree on
   the mean are still told apart. `text_feedback` sits **beside** `kpis`, not
   inside it, and reaches the next prompt as
   [Evaluator notes](#evaluator-notes-what-the-kpis-cannot-say). Same evaluator,
   different `inputs`, for each stage.

   Accept `--seed` even if you ignore it today. It costs one argparse line, and
   it is what lets you turn `seeds: N` on later without touching the evaluator.
   Both examples take one: bin packing re-draws its instance sets from a
   disjoint seed range (seed 0 is the published set, so nothing moves), and
   circle packing seeds the module-level `random` generator before calling the
   candidate.
4. **Split the work into stages.** Stage 0 is always `builtin-static`. Put a
   subset that runs in seconds at stage 1 with a `promote` rule, and the real
   thing at stage 2 with `private_inputs` for a hold-out. The gap between the
   public and private score is recorded as `generalization_gap`, charged once
   through `evaluate.holdout_penalty`, and never otherwise optimised against.
   If your problem has one instance and no natural subset, say so in the config
   and lean on step 2 instead — `examples/circlepacking/` runs two stages and
   no hold-out for exactly that reason, and its repair function does the
   anti-gaming work a hold-out would otherwise do.
5. **Pick descriptor axes.** One or two KPIs that describe *what a candidate
   does*, not how well it scores. `complexity` comes free; the second one is
   worth emitting from your evaluator specifically for this.
6. **Set a budget.** `budget.max_usd`, `budget.max_tokens` and
   `budget.max_full_evals_per_day` are hard stops, not warnings. So is
   `budget.max_consecutive_provider_errors` (default 3): a rate limit, a
   subscription session cap or an outage halts the run with the backend's own
   message instead of breeding children into a dead provider. With
   `claude-cli` that message reads like "You've hit your session limit —
   resets 12:30pm"; wait it out and re-run the same directory (the lock and
   the ledger resume cleanly).

7. **Tell the model what it may use.** Fill in `problem.tools`,
   `problem.constraints` and `problem.what_counts_as_new` — see
   [the problem description](#the-problem-description-in-named-sections). If
   your skeleton offers a helper the block is meant to call, name it in
   `tools`, with its signature. The framework cannot infer it, and a model that
   does not know a tool exists will not use it.
8. **Run `preflight`** before you run anything you are paying for.

`python -m evolvekit init <dir>` writes a commented starter config and a
`.env.example`. `examples/binpacking/` is the worked version of all eight
steps.

### About the bin-packing numbers

The example evolves a `priority(item, bins)` function inside a fixed greedy
online packer, FunSearch-style, and reports mean excess of bins used over the
L1 lower bound, in percent. Instances are generated deterministically from
seeds in two published shapes — uniform "OR-like" (capacity 150, sizes 20–100)
and Weibull (capacity 100, Weibull(45, 3)) — and **nothing is downloaded**.

The best-fit seed scores 5.871% on the 20-instance `full` set, which sits right
next to the ~5.81% usually quoted for best fit on OR-Library OR1, and FunSearch's
5.30% on the same file is the yardstick to beat. Those numbers are comparable in
*spirit* only: these are OR-shaped instances, not OR1 itself. Only a manually
downloaded OR-Library file would make the comparison direct.

### The second example: circle packing, n = 26

`examples/circlepacking/` is the AlphaEvolve / OpenEvolve / ShinkaEvolve
benchmark: place 26 non-overlapping circles inside the unit square so that the
**sum of their radii** is as large as possible. It is here because it is not
bin packing — a continuous geometry problem with one instance, no natural
proxy, and no hold-out — and because it has published reference points.

**Reference points**, as reported by their authors. None of them is a claim
about this repository, and none of them has been attempted here yet:

| System | Sum of radii | Budget as reported |
|---|--:|---|
| AlphaEvolve (2025) | 2.635 | n/r |
| OpenEvolve | ≈ 2.634 | ~800 iterations, two config phases |
| ShinkaEvolve | 2.636 | ~150 evaluations |
| ThetaEvolve | 2.63598 | 205 k programs, 8×A100 |
| LoongFlow | matched | 258 calls |

The seed in `skeleton.py` is a 5×5 grid of radius-0.1 circles — which exactly
fills the square — plus one circle dropped into the first interstice, where
four touching circles leave room for `0.1 × (√2 − 1)`. That sums to
**2.5414214**: deterministic, legal, and about 3.6 % below the published
figures. There is no search in it at all.

The fixed part of the skeleton is the anti-gaming boundary, and it is a
*repair function* rather than a hold-out:

* `repair()` clamps every centre into the square, clamps every radius to its
  distance from the nearest wall, and shrinks overlapping pairs until nothing
  overlaps. Radii only ever go **down**, so a cheating proposal cannot buy
  score — and it is never voided either. The objective, `sum_radii`, is
  measured on the repaired packing.
* `violations()` measures the **raw** proposal and reports `overlap_violation`,
  `bounds_violation` and `count_violation`, all continuous and all zero for a
  legal packing (with a 1e-9 tolerance, so touching circles are not charged for
  arithmetic). They are the penalty terms, so a near-miss ranks just below a
  clean packing instead of scoring nothing.
* `descriptors()` reports `radius_variance` and `boundary_fraction` — behaviour,
  not quality. `radius_variance` is the archive's second axis: it separates
  "26 equal circles" from "a few big ones and a lot of filler", two families
  that can score alike and want different mutations.
* `construct_packing()` — the evolve block — may run a time-bounded local
  search. `TIME_BUDGET_S` (2 s) is the budget it should give itself; the
  stage's 30-second timeout is the hard limit, so a candidate that ignores the
  constant is killed rather than rewarded. Standard library only, no numpy.

**Two stages, and no hold-out — on purpose.** A hold-out catches a candidate
that fitted the instances it could see. Here there is exactly one instance and
it *is* the problem, so there is nothing to hold out and nothing a hold-out
could generalise to. Faking one (a smaller `n`, a rectangle) would be a
different problem, not a proxy for this one. The referee does that job instead.

Offline, against the `fake` provider:

```
python -m evolvekit run --config examples/circlepacking/evolvekit.yaml \
    --run-dir runs/circles --generations 2
```

Two generations, about two seconds: the scripted improvement replaces the
frozen grid with a penalty relaxation that lets the centres move, reaching
2.5794 raw and **2.5702** once its residual overlap is charged.

For real, through a Claude Code subscription or an OpenRouter key:

```
python -m evolvekit run --config examples/circlepacking/evolvekit.claude.yaml \
    --run-dir runs/circles-claude
```

That variant `extends` the offline one, so the problem, the evaluator and the
search are identical; it changes the backend (`claude-cli`, haiku + sonnet), a
$5 cap, 30 generations, and `stop.max_usd_since_improvement: 0.50`.
`evolvekit.openrouter.yaml` beside it is the cheaper mix — an OpenRouter key
for the small slot, the subscription for the big steps; see
[Running for real](#running-for-real). **Expected cost:**
on the Phase B bin-packing evidence — 18 paid calls for $0.83, so roughly
$0.05 per call with the CLI's ~13 k cached-input overhead — 30 generations at
2–6 children is 60–200 calls, or **$3–5**, which is what the cap is set to. The
evaluator itself is free: one packing takes under half a second of local CPU
and there is no solver.

This run has not been bought. When it is, the honest comparison is against the
2.5414 seed and against the published figures above — not a claim of having
matched them.

## What's inside

Everything below is implemented and green (548 offline tests, about two
minutes; +30 PyVRP tests in `.venv-pyvrp`), and covers the whole range from
millisecond-fast, deterministic, mocked benchmarks up to hour-long,
stochastic, Azure-backed evaluators.

- **Config** (`evolvekit/config.py`) — the full `problem` / `evaluate` /
  `models` / `budget` / `stop` / `search` schema, validated, with key-path
  error messages and unknown-key rejection. Cross-key validation too: an
  embedding novelty gate without a `models.embed` slot, or with one pointing at
  a backend that has no embeddings API, is refused before the run starts, and
  so is a `seeds: N > 1` stage whose command has no `{seed}` placeholder.
- **The prompt** (`evolvekit/prompts.py`) — a cacheable system half carrying
  the problem's `description`, `tools`, `constraints` and `what_counts_as_new`
  as named sections, and a bounded user half carrying the parent's block, its
  score breakdown, its evaluator's own notes, 1–2 inspiration delta summaries,
  the meta-scratchpad, the "Already tried" dead-end list, and the operator
  instruction. Nothing in it grows with the number of generations.
- **Providers** (`evolvekit/providers/`) — `azure`, `openrouter`, `claude-cli`
  and `fake` behind one `complete()` Protocol, plus an optional second
  capability, `embed()`, that the two OpenAI-shaped backends and `fake`
  implement and `claude-cli` explicitly refuses. The two OpenAI-shaped backends
  share one client with a bounded retry (three attempts, 2 s / 4 s, on 429 and
  5xx only) and a failure classifier whose `ProviderError` messages name the
  cause. `claude-cli` is smoke-tested against the real CLI; `azure` is
  contract-tested against recorded responses, error shapes included.
- **Evaluation** (`evolvekit/evaluate/`) — the staged cascade,
  `top_k_per_generation` and `archive_percentile` promotion, linear and `log1p`
  penalty scales, a private hold-out charged once through
  `evaluate.holdout_penalty`, hard reject confined to stage 0, optional
  `text_feedback` prose per stage, multi-seed stages that average their KPIs
  and record a per-KPI coefficient of variation, and a behaviour signature per
  command stage that stops a candidate the moment it reproduces an earlier
  one's KPIs — at nine significant digits on a deterministic stage, three on a
  stochastic one.
- **Accounting** (`evolvekit/ledger.py`, `evolvekit/budget.py`,
  `evolvekit/economics.py`, `evolvekit/lock.py`) — append-only `runs.jsonl` and
  `usage.jsonl` (embedding calls included), per-candidate traces, USD from a
  price table or from provider-reported spend, USD/token/full-eval caps, a
  per-generation economics series, a stop policy over patience, target, USD
  since the last improvement and gain per USD, and one writer per run
  directory.
- **Archive** (`evolvekit/search/archive.py`) — a MAP-Elites grid over
  configurable KPI descriptors, one elite per cell plus a global top-k,
  children counts, lineage, ShinkaEvolve parent sampling, persisted to
  `archive.json` and rebuildable from `runs.jsonl`.
- **Search** (`evolvekit/search/`) — `diff`, `rewrite`, `crossover`, scheduled
  `big_step` and a real `param_lhs`; inspirations drawn from other cells and
  passed as LLM-free delta summaries; three novelty filters (exact structural
  hash, a similarity gate that flags rather than rejects, and a behavioural
  signature that stops a monotone re-expression at the first stage that scores
  it and tells the parent why); TurboEvolve-style adaptive breadth; and a
  40-line meta-scratchpad refreshed by the small model.
- **Pre-flight** (`evolvekit/preflight.py`) — `python -m evolvekit preflight`
  runs the seed through every stage including the hold-out, prints each stage's
  KPIs, notes and duration, and checks the timeouts, the daily full-evaluation
  cap and the projected wall clock against those measurements. `0` clean, `1`
  warnings, `2` failures. `--provider-check` adds one minimal call per
  configured model role.
- **Views** (`evolvekit/leaderboard.py`) — markdown and single-file HTML with
  lineage, cell coordinates, no-op/duplicate/behavioural/near counts, a
  hold-out flag, an archive grid drawn as 2-D slices when it has more than two
  axes, and an inline-SVG chart of best score against cumulative USD.
- **Examples** — `examples/binpacking/` is a three-stage cascade that runs
  offline in about ten seconds and improves on its seed twice, exercising every
  operator, all three novelty verdicts, the near-duplicate flag and both
  directions of adaptive breadth on the way. `examples/circlepacking/` is the
  n = 26 benchmark: a two-stage cascade with a fixed repair function instead of
  a hold-out, seeded at 2.5414. Both carry three configs — the offline one and
  two paid variants (`.claude.yaml`, `.openrouter.yaml`) folded onto it via
  `extends` so they cannot drift — both name their tools and constraints in the
  prompt, and both evaluators emit `text_feedback` and accept `--seed`.
  `examples/pyvrp/` (see [its README](examples/pyvrp/README.md)) is the
  heavyweight third: a synthetic 3,000-order vehicle-routing instance, a
  skeleton whose evolve block is PyVRP's own `SolveParams` under a hard 900 s
  cap, and a 120 s proxy → 900 s × 2 seeds full → 900 s private hold-out
  cascade, with the defaults' baseline recorded for comparison.

Known gaps, all deliberate:

- **The near-duplicate gate is syntactic by default.** The `local` method
  compares AST node types, which catches "the same rule with different
  constants" exactly and catches "the same idea expressed differently" not at
  all. The `embedding` method exists for the second case but has never been run
  against a real embeddings endpoint — only against mocked clients and `fake`.
- **The behaviour signature is exact** on a deterministic stage. Two rules that
  differ only on instances outside the current input set look like twins. That
  is the trade the filter makes on purpose. On a stochastic stage the trade
  moves the other way: three significant digits will call two genuinely
  different programs twins if their means agree to a tenth of a percent.
- **`preflight` measures the seed, once.** Its timeout advice is as good as the
  assumption that the seed's cost is representative, which on a problem whose
  candidates can pick their own effort it is not. The 1.5× headroom is the
  hedge, not a guarantee.
- **The economics stop rules have no defaults**, because there is no defensible
  answer to "how much score is a dollar worth" on a problem the framework has
  never seen. They are off until someone sets them for their problem.
- **`param_lhs` picks one variant per child** rather than sweeping a batch.
- **The two benchmark targets are still unattempted**: ≤ 5.5 % excess on the
  OR-shaped bin-packing set inside a fixed USD cap, and a circle-packing run
  against the published ~2.635. `examples/*/evolvekit.openrouter.yaml` is the
  cheapest way to settle either. The Azure backend has never spoken to a real
  endpoint from this repository — `tests/test_azure_contract.py` is recorded
  responses, not a live call.

