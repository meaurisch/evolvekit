# PyVRP configuration search

Find the PyVRP configuration that routes a 3,000-order metro parcel-delivery
day most cheaply, inside a hard **15-minute solve limit per run**.

This is the third example in evolvekit and the first one where the evolved
artefact is not a heuristic. Bin packing evolves a priority rule; circle
packing evolves a construction. Here the solver is fixed — PyVRP, unmodified —
and what the loop searches is **PyVRP's own `SolveParams`**: its penalty
schedule, its granular neighbourhood, its operator set, its perturbation size
and its restart policy. The instance is frozen above the fence, so two
candidates always solve exactly the same problem, and the only thing that
differs between them is how the solver was told to go about it.

**PyVRP version: 0.14.0** (cp312 wheel, Windows). Pinned as
`pyvrp>=0.14` in the `pyvrp` extra of `pyproject.toml`. The version matters
more than usual here: 0.12 replaced PyVRP's hybrid genetic search with an
iterated local search, so `GeneticAlgorithmParams` and `PopulationParams` no
longer exist and most PyVRP tuning advice on the internet is about a solver
this is not.

---

## 1. What the instance models

`make_instance.py` writes a synthetic metro area. **Nothing is downloaded and
nothing here is customer data**: every number comes out of `random.Random(seed)`,
so `--orders 3000 --seed 7` produces a byte-identical file on any machine.

```
python make_instance.py --orders 3000 --seed 7  --out instance-3000-s7.json
python make_instance.py --orders 3000 --seed 23 --out instance-3000-s23.json
python make_instance.py --orders 300  --seed 7  --out instance-300-s7.json
```

There are no `--features` flags. Everything is on, always: a generator with
switches would need a test matrix of its own, and the point of the example is
the *full* problem shape rather than a ladder of easier ones.

### Units

| Quantity | Unit | Why |
|---|---|---|
| distance | metre (int) | PyVRP's matrices are `int64` |
| duration | second (int) | same |
| load 0 | kilogram (int) | what breaks a van's weight limit |
| load 1 | cubic decimetre (int) | what breaks its volume limit first |
| cost | 1e-4 EUR (a hundredth of a cent) | `unit_distance_cost` is an **integer** |
| time zero | 06:00 local | the horizon runs to 20:00, i.e. 50,400 s |

The cost unit is the one non-obvious choice, and it is squeezed from both
sides. PyVRP's `unit_distance_cost` and `unit_duration_cost` are integers per
metre and per second, so a van at EUR 0.30/km — three hundredths of a cent per
metre — needs a unit finer than a cent. From the other side, PyVRP's penalty
manager clamps its violation penalties at `PenaltyParams.max_penalty`,
100,000 by default: pick too fine a unit and that ceiling becomes a rounding
error next to a vehicle's fixed cost, the search stops caring about a minute
of time warp, and PyVRP raises `PenaltyBoundWarning` to say so. A first draft
of this instance was denominated in micro-euro and did exactly that. At
1e-4 EUR the ceiling is worth EUR 10 per second of time warp against a EUR 35
van, which is ample, and the vehicle tariffs are rounded to figures that are
exact in the unit. `evaluate.py` divides by 10,000 and reports EUR.

### Geography

A 40 x 40 km square. Orders are placed in three populations:

| Population | Share | Placement |
|---|--:|---|
| dense centre | 45 % | Gaussian about (20 km, 20 km), sigma 2.6 km |
| suburban clusters | 40 % | six satellite towns on an 11–16 km ring, sigma 1.4 km |
| rural fringe | 15 % | uniform, but at least 17 km from the centre |

Three depots: a central hub 700 m from the city centre, and two of the
satellite towns. Depot 1 opens half an hour later than the other two, which is
what makes the *vehicle shift windows differ per depot*. Every depot visit
costs ten minutes of loading — and for a vehicle with reload depots that is
once per trip, not once per route.

Travel distance is **Euclidean x 1.3**, the usual detour-index rule of thumb
for a European metro, rounded to whole metres. Travel *time* is that distance
divided by the profile's door-to-door speed.

### Two routing profiles

| Profile | Speed | Distance | Used by |
|---|--:|---|---|
| `light` | 8.5 m/s (30.6 km/h) | Euclidean x 1.3 | vans, evening vans |
| `heavy` | 7.0 m/s (25.2 km/h) | + 10 % on any edge touching the inner 6 km | small and large trucks |

The second profile is not just a slower copy of the first: a 12-tonne truck
does not take the streets a van takes, so it pays a detour on inner-city edges
as well as being slower everywhere.

### The orders

* **Parcels, two dimensions.** Weight is lognormal about 3 kg, capped at 32 kg.
  Volume is weight times a density factor drawn from 6–14 dm³/kg, capped at
  180 dm³. The two dimensions bind at different times on purpose: a round of
  small dense parcels runs out of kilograms, a round of bulky light ones runs
  out of litres, and a fleet with one capacity number cannot tell them apart.
* **Service durations** of 2–6 minutes: `120 s + 5 s/kg + U(0, 120) s`,
  clipped. A return adds another minute of scanning.
* **Delivery slots** as time windows: 25 % morning (08–12), 25 % afternoon
  (12–16), 15 % evening (16–20), 25 % all-day, and 10 % on a **tight one-hour
  slot** starting at a random whole hour between 08:00 and 19:00. The depots
  and the vehicle shifts stay open until **21:00**, an hour past the last
  delivery slot. Without that hour a route that serves a customer at 19:55 is
  back at the depot after closing time and is charged time warp for it, which
  is a modelling artefact rather than a routing problem — and it was enough to
  make the 3,000-order instance infeasible by 81 seconds.
* **Returns, ~10 %.** A parcel handed back at the delivery address:
  simultaneous pickup and delivery, in both load dimensions.
* **Optional orders, ~5 %.** `required=False` with a prize. The prize is
  anchored on the marginal cost of serving the order ourselves — its service
  time at a van's duration cost plus a 4 km round-trip detour — and then
  scaled by a factor drawn from 0.5 to 1.8. That spread is the point: some
  optional orders are worth taking and some are not, so *which* ones a
  configuration drops is a real decision rather than an on/off switch.
* **Release times, ~8 %.** Goods that are not on the first trailer of the day:
  a vehicle may not leave the depot for them before 09:00, 10:00 or 11:00. An
  order that gets a release time and had a morning slot is moved to a slot it
  can actually make.
* **Alternative addresses, ~2 % of orders.** Home *or* a parcel shop within
  800 m, as a **mutually exclusive client group** with `required=True`:
  exactly one of the two is visited. PyVRP requires the members of such a
  group to be optional clients, which is why both carry `required=False` while
  the group itself is required. The parcel shop is open all day and is quicker
  to serve, so choosing it is a real trade against the customer's own slot.
* **Same-day courier jobs, ~1 % of the order count.** `Shipment`s: pick up at
  A between 09:00 and noon, deliver at B later the same day, both on one
  route.

### The fleet

Four classes at each of the three depots — twelve vehicle types.

| Class | Capacity | Fixed | Distance | Duration | Shift | Max distance | Profile | Reloads | Overtime |
|---|---|--:|--:|--:|--:|--:|---|--:|---|
| van | 900 kg / 7,000 dm³ | EUR 35 | 0.30 EUR/km | 25.20 EUR/h | 9 h | 220 km | light | 2 | 1 h @ +50 % |
| small truck | 3,200 kg / 22,000 dm³ | EUR 60 | 0.50 EUR/km | 28.80 EUR/h | 9.5 h | 320 km | heavy | 1 | 30 min |
| large truck | 8,000 kg / 48,000 dm³ | EUR 95 | 0.70 EUR/km | 32.40 EUR/h | 10 h | 400 km | heavy | 0 | none |
| evening van | 900 kg / 7,000 dm³ | EUR 38 | 0.30 EUR/km | 28.80 EUR/h | 7 h | 180 km | light | 1 | 30 min |

The evening van's shift starts at 13:00 and must start by 15:00; every other
class may start from its depot's opening time and must start by 08:00 or
09:00. Every shift must be back by 21:00. Fleet size is `orders x per-100-orders x 3 depots`, deliberately
generous — 261 vehicles for 3,000 orders — because an instance whose fleet is
the binding constraint measures fleet sizing rather than routing.

### PyVRP features used, and not used

| Feature | Used | How |
|---|---|---|
| multiple depots | yes | 3, with per-depot fleets |
| multi-dimensional capacity | yes | kg and dm³ |
| client time windows | yes | four slot shapes plus tight hours |
| depot time windows | yes | depot 1 opens later |
| vehicle shift windows (`tw_early`/`tw_late`) | yes | per depot and per class |
| latest start (`start_late`) | yes | every class |
| service durations | yes | 2–6 min |
| simultaneous pickup & delivery | yes | ~10 % returns |
| optional clients & prizes | yes | ~5 % |
| release times | yes | ~8 % |
| mutually exclusive client groups | yes | home vs parcel shop |
| shipments (pickup A → delivery B) | yes | same-day courier jobs |
| heterogeneous fleet | yes | 4 classes x 3 depots |
| fixed / distance / duration costs | yes | all three |
| max route duration (`shift_duration`) | yes | 7–10 h |
| max route distance | yes | 180–400 km |
| overtime (`max_overtime`, `unit_overtime_cost`) | yes | vans and small trucks |
| reload depots / multi-trip | yes | vans 2 reloads, small trucks 1 |
| multiple routing profiles | yes | light and heavy |
| **`initial_load`** | no | models a vehicle that starts loaded and must drop at a depot before it can pick up. It interacts with reload depots in a way that would need its own explanation for no extra decision in the search space. |
| **`PiecewiseLinearFunction`** | no | PyVRP 0.14.0 exposes it, but `ProblemData`'s constructor takes plain matrices; there is no seam in the `ProblemData` API this example builds through. |
| **asymmetric matrices** | no | the generator's road model is symmetric. PyVRP supports asymmetry; a one-way system would be a second modelling assumption for no new decision. |
| **`minimise_fleet`** | no | it refuses instances with more than one vehicle type and instances with optional clients — this instance is both. |

---

## 2. The search space

`skeleton.py` is a normal Python file. Above the fence: the loader, the
`ProblemData` builder, the time-limit referee and the measurement. Below it,
one function:

```python
def configure(data, time_limit, seed) -> SolveParams
# or -> (SolveParams, initial_solution | None)
```

PyVRP 0.14.0's solver is an **iterated local search**, not the hybrid genetic
search of releases before 0.12 — there is no `GeneticAlgorithmParams` and no
`PopulationParams` to tune, and any advice that mentions them is about an
older PyVRP. What there is:

| Group | Tunable | Default | Meaning |
|---|---|--:|---|
| `ils` | `num_iters_no_improvement` | 150000 | iterations without improvement before restarting from the best solution |
| | `history_length` | 300 | late-acceptance hill-climbing history; longer accepts more worsening moves |
| | `exhaustive_on_best` | True | run a full local search on every new best. The single most expensive switch at 3,000 clients |
| `penalty` | `solutions_between_updates` | 500 | registrations between penalty updates |
| | `penalty_increase` | 1.5 | multiplier when too few feasible solutions were seen |
| | `penalty_decrease` | 0.9 | multiplier when too many were |
| | `target_feasible` | 0.65 | target share of feasible registrations |
| | `feas_tolerance` | 0.05 | deadband around that share |
| | `min_penalty` | 0.1 | floor |
| | `max_penalty` | 100000.0 | ceiling. Worth EUR 10 per second of time warp in this instance's cost unit; see the units note in section 1 |
| `neighbourhood` | `num_neighbours` | 50 | arcs kept per client. The main iteration-cost/quality trade |
| | `weight_wait_time` | 0.2 | how much waiting time counts towards proximity |
| | `symmetric_proximity` | True | whether (i, j) and (j, i) get equal weight |
| `perturbation` | `min_perturbations` | 1 | smallest ruin step |
| | `max_perturbations` | 25 | largest ruin step |
| `operators` | 19 classes | `pyvrp.search.OPERATORS` | which local-search moves exist at all |
| | | | also available, off by default: `Relocate3`, `Swap31`, `Swap32`, `Swap33` |
| — | `initial_solution` | None | a warm start, returned as the second element of the tuple |

**A different decision means structure, not a nudged constant.** `num_neighbours`
50 → 49 is the same configuration and the near-duplicate gate says so. These
are different configurations: dropping the wide-exchange operators to buy
iterations; turning `exhaustive_on_best` off and spending the time on
perturbation; running the penalty schedule so hard that the search lives in
the infeasible region, or so softly that it never leaves feasibility; scaling
the neighbourhood off `data.num_clients` so one block covers the 300-order
proxy and the 3,000-order instance; building a warm start.

The block also carries a `# PARAMS:` line over its ten numeric tunables, so
`param_lhs` — the operator that costs no tokens at all — is real on this
example. On a problem where one evaluation is a quarter of an hour, a free
child is worth having.

### The referee

* `MAX_TIME_LIMIT_S = 900.0`, in the fixed part. The budget is
  `min(requested, 900)` and the block cannot reach the constant.
* Whatever `configure()` itself spends is **deducted from the solve budget**,
  so an expensive warm start pays for itself out of its own pocket.
* A block that raises, returns something that is not a `SolveParams`, or
  hands PyVRP a configuration PyVRP itself refuses — an operator list holding
  something that is not an operator only fails once `solve()` looks at it — is
  counted as `config_errors` and re-run on PyVRP's defaults. It scores; it
  just scores badly. That is the penalties-not-`None` rule on the mistakes a
  model is most likely to actually make here.
* The reported objective is measured by the *fixed* part, with a
  `CostEvaluator` whose penalties are derived from the instance's own fleet:
  a unit of excess load costs what the most expensive vehicle charges to carry
  a unit of capacity, a second of time warp costs the most expensive
  driver-second, a metre over the limit costs the most expensive metre.

---

## 3. KPIs and the score

`objective` is the PyVRP cost of the best **feasible** solution, in EUR, and
lower is better. When no feasible solution was found it is the **penalised**
cost instead — a real number rather than the `inf` PyVRP reports — and
`infeasible` is 1, which the config prices at 100,000 EUR so that an
infeasible candidate can never outrank a feasible one while still keeping a
gradient underneath.

| KPI | What it is |
|---|---|
| `objective` | cost of the best feasible solution, EUR (or penalised cost) |
| `infeasible` | 1 when no feasible solution was found |
| `distance`, `duration` | metres and seconds over all routes |
| `num_routes`, `num_trips` | routes, and trips once reloads are counted |
| `fixed_cost`, `distance_cost`, `duration_cost` | the objective, split |
| `unassigned_clients` | optional prize-collecting orders skipped |
| `missing_required_clients`, `missing_groups`, `unassigned_shipments` | contract violations; zero in any feasible solution |
| `time_used_s`, `budget_s`, `configure_s`, `iterations` | effort |
| `mean_route_length` | **behaviour**: clients per route |
| `fleet_mix_entropy` | **behaviour**: bits of spread over the twelve vehicle types. 0 = one type does everything; log2(12) ≈ 3.58 = spread evenly. The archive's second axis |
| `convergence` | list KPI: best cost at 25/50/75/100 % of the budget |

The evaluator also writes a `text_feedback` string alongside `kpis`: the
convergence quarters, the feasibility verdict and its violations, the fleet
actually used, and what the run dropped. The framework reads `payload["kpis"]`
and ignores the rest today, so the key is free; it is what the prompt will
quote once `text_feedback` lands.

**Note on determinism.** A PyVRP solve is wall-clock bounded, so two runs of
the same configuration on the same machine differ in iteration count and
therefore in objective by a few tenths of a percent. The behavioural-duplicate
filter is consequently close to inert on this example. `seeds: 2` on the full
stage is the cheapest halving of that noise and the command is already
`--seed`-shaped; see the comment in `evolvekit.real.yaml`.

---

## 4. The cascade, and what it costs in wall-clock

`evolvekit.yaml` — offline, `fake` provider, no network:

| Stage | Instance | Budget | Timeout |
|---|---|--:|--:|
| 0 `static` | — | — | 120 s |
| 1 `full` | 300 orders | 20 s | 90 s |

Two generations, two children: about **two minutes**.

`evolvekit.real.yaml` — `extends` the above, so the skeleton, the fence and
the description are identical and cannot drift:

| Stage | Instance | Budget | Timeout | Promote |
|---|---|--:|--:|---|
| 0 `static` | — | — | 120 s | all |
| 1 `proxy` | 3,000 orders, seed 7 | 120 s | 300 s | top 2 per generation |
| 2 `full` | 3,000 orders, seed 7 | 900 s | 1400 s | — |
| 2 `full` (private) | 3,000 orders, **seed 23** | 900 s | 1400 s | hold-out |

The proxy shortens the **clock**, not the instance. That is deliberate: a
configuration is being chosen for a 15-minute run on 3,000 clients, and what
works at 300 clients — a big neighbourhood, expensive operators — is exactly
what does not work at 3,000. The 300-order instance exists for the offline
demo and the tests, not as the real cascade's first rung.

Cost per generation: 3–6 candidates x 120 s of proxy, then 2 x 30 minutes of
full stage — roughly **70–80 minutes**. `budget.max_full_evals_per_day: 6` is
what stops that becoming three days: six full evaluations is three hours of
solver, which is one afternoon.

---

## 5. Baselines: PyVRP's own defaults

PyVRP's own defaults — the seed block, unchanged — on this machine (Windows 11,
Python 3.12, PyVRP 0.14.0, `--seed 42`, single-threaded). These are the
yardstick the loop has to beat, and they are the numbers to re-measure on any
other machine before comparing anything to them.

> **Caveat, learned the expensive way:** the table below was measured while the
> build agent was still running tests on the same machine, and PyVRP is
> wall-clock bounded — CPU contention silently becomes solution quality. On a
> quiet machine the same defaults at 3,000 orders / 900 s reach **EUR 10,852**
> (mean of seeds 0–1, objective CV 0.45 %, ≈142,500 iterations), not 11,563.7.
> The first real search run (§8 below) used the quiet-machine seed as its
> baseline. Never compare numbers measured under different load.

| Instance | Budget | Objective (EUR) | Feasible | Routes / trips | Iterations | Wall | Peak RSS |
|---|--:|--:|:-:|--:|--:|--:|--:|
| 300 orders | 20 s | **2,075.5** | yes | 9 / 13 | 3,238 | 20.8 s | 60 MB |
| 300 orders | 60 s | **2,021.1** | yes | 9 / 12 | 9,768 | 60.7 s | 60 MB |
| 3,000 orders | 120 s | **12,813.5** | yes | 56 / 84 | 5,820 | 128.3 s | 747 MB |
| 3,000 orders | 900 s | **11,563.7** | yes | 48 / 68 | 55,800 | 913.5 s | 747 MB |

Best cost at 25 / 50 / 75 / 100 % of the budget, in EUR:

| Run | 25 % | 50 % | 75 % | 100 % |
|---|--:|--:|--:|--:|
| 300 @ 20 s | 2,179.8 | 2,179.8 | 2,075.5 | 2,075.5 |
| 300 @ 60 s | 2,150.4 | 2,048.0 | 2,024.6 | 2,021.1 |
| 3,000 @ 120 s | 14,380.5 | 13,692.2 | 13,095.2 | 12,813.5 |
| 3,000 @ 900 s | 12,326.0 | 11,879.4 | 11,688.7 | 11,563.7 |

Three things worth reading off that table.

**The curve is still falling at 100 % in every single run.** The defaults are
not converging inside fifteen minutes on 3,000 clients — they are still
improving when the clock stops. That is exactly the regime where configuration
matters, and it is why the example is set at 3,000 orders rather than at a
size PyVRP finishes.

**Iterations are the currency.** 120 s buys 5,820 iterations and 900 s buys
55,800 — nearly ten times as many for seven and a half times the clock,
because the fixed set-up (building the 3,003 x 3,003 matrices, computing the
granular neighbourhood, constructing the first solution) is amortised. Any
configuration change that makes an iteration cheaper — a smaller
`num_neighbours`, a shorter operator list, `exhaustive_on_best=False` — buys
iterations directly, and whether that trade pays is the question the search
exists to answer.

**A solve overruns its budget by a few seconds.** `MaxRuntime` is only checked
between iterations, and the matrices and neighbourhood are built before the
loop starts: 900 s of budget is 913 s of wall clock. That is why every stage
timeout is at least 1.5x its budget.

An earlier draft of this instance was **infeasible** at 3,000 orders with 81
seconds of time warp, and it took two fixes to make the defaults feasible: the
cost unit (see section 1) and an hour of depot opening past the last delivery
slot (see "the orders"). Both were modelling artefacts rather than routing
difficulty, and both are called out where they live because "PyVRP says
infeasible" is a much weaker signal than it looks until you have ruled them
out.

---

## 6. How to run it

### Set up

```
python -m venv .venv-pyvrp
.venv-pyvrp/Scripts/python -m pip install -e ".[dev,pyvrp]"
```

`.venv-pyvrp/` is gitignored. The core test suite never imports PyVRP:
`tests/test_pyvrp_example.py` opens with `pytest.importorskip("pyvrp")` and a
version gate, so `python tasks.py check` in the base interpreter skips the
whole file — and skips it too if that interpreter happens to carry a PyVRP
older than 0.14.

**Run evolvekit itself from `.venv-pyvrp`.** Evolvekit's static stage imports
each candidate in a child `sys.executable`, which is whichever interpreter is
running the loop, so that one has to have PyVRP.

The *stage command* is a different problem, and the evaluator solves it
itself. A config cannot carry a machine-specific interpreter path, so the
command says `python` — and on Windows `subprocess` resolves a bare `python`
to the **system** interpreter even when a virtual environment is active and
first on `PATH`. Activating the venv does not help; this was measured, not
assumed. So `evaluate.py` checks the interpreter it was started under, and if
it has no PyVRP ≥ 0.14 it looks for `.venv-pyvrp` beside or above itself and
re-runs the job there, passing on that run's exit status.
`EVOLVEKIT_PYVRP_RELAUNCHED` stops it doing that twice.

### Offline, free, about two minutes

```
.venv-pyvrp/Scripts/python -m evolvekit run \
    --config examples/pyvrp/evolvekit.yaml --run-dir runs/pyvrp
```

### For real

```
cd examples/pyvrp
../../.venv-pyvrp/Scripts/python make_instance.py --orders 3000 --seed 7  --out instance-3000-s7.json
../../.venv-pyvrp/Scripts/python make_instance.py --orders 3000 --seed 23 --out instance-3000-s23.json
cd ../..
.venv-pyvrp/Scripts/python -m evolvekit run \
    --config examples/pyvrp/evolvekit.real.yaml --run-dir runs/pyvrp-real
```

The two 3,000-order instances are gitignored — a megabyte each, and the
generator is deterministic, so the file is reproducible rather than precious.
The 300-order pair *is* committed, because the tests and the offline demo need
it and it is a hundred kilobytes.

### Reproduce the baselines

```
cd examples/pyvrp
../../.venv-pyvrp/Scripts/python evaluate.py --candidate skeleton.py \
    --inputs metro300 --out /tmp/kpis.json --seed 42 --time-limit 20
```

Swap `metro300` for `metro3000` and `--time-limit` for 120 or 900.

---

## 7. Notes for locked-down environments

* **Everything stays local.** The instance generator is synthetic — `random.Random(seed)`
  and nothing else — so there is no customer data anywhere in this directory,
  and no file here needs to be treated as confidential. The solver runs on the
  laptop's own CPU. The only thing that leaves the machine is the LLM call,
  and only when the config names a real backend.
* **The offline config makes no LLM call at all.** `provider: fake` replays
  `fake_responses.yaml`. `python tasks.py check` and the pyvrp test file are
  both entirely offline.
* **Azure.** Swap `models` in `evolvekit.real.yaml` for the `azure` backend
  and set `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` and optionally
  `AZURE_OPENAI_API_VERSION` in `.env` (gitignored, never committed). The
  `models.<slot>.model` value is the **deployment** name, not the model name.
  `claude-cli` and `openrouter` are the alternatives; the commented block at
  the bottom of `evolvekit.real.yaml` is the openrouter form.
* **CPU.** A 900-second solve on 3,000 clients is one core saturated for
  fifteen minutes and about 750 MB of RAM (six int64
  3,003 x 3,003 matrices, three of them PyVRP's own copies). It is not a laptop-killer,
  but a full-stage evaluation is 30 minutes of that, and
  `budget.max_full_evals_per_day: 6` is the setting that keeps a run inside an
  afternoon.
* **Nothing is pushed.** This example lives on `examples/pyvrp` and the
  `.githooks/pre-push` hook refuses `master` anyway.

## 8. The first real search run (2026-08-23/24)

Overnight on this machine: `_run.local.yaml` = `evolvekit.real.yaml` with one
promotion per generation and `generations: 8`; Haiku for mutations, Sonnet for
big steps, via `claude-cli`.

| | |
|---|--:|
| Wall clock | ~10.5 h (8 generations, 9 full evaluations of 3 × 900 s each) |
| LLM spend | **$3.24**, 47 calls, 1.44 M tokens |
| Seed (defaults) | public **10,874.3** / hold-out **10,892.4** (2-seed mean, CV 0.45 %) |
| Best found (`g004-c0015`, gen 4, Haiku rewrite of a crossover) | public **10,811.5** / hold-out **10,723.6** |
| Improvement | public −0.6 % (edge of noise) · **hold-out −1.6 %** (well clear of it) |
| Stop reason | `max_usd_since_improvement` ($1.50 spent after the gen-4 best) |
| Filter activity | 17 near-duplicates flagged (evaluated, as designed); 0 structural/behavioural rejects — the behavioural filter is inert on a wall-clock-bounded solver, exactly as §3 predicts |

The winning configuration (`runs/pyvrp-real-1/winner-g004-c0015.py` in the run
directory) is a coherent strategy, not a constant nudge: ~10 % smaller
neighbourhoods above 2,800 clients to buy iteration throughput, the exhaustive
refinement pass on new best solutions disabled at that size, and
`num_iters_no_improvement` tiered by time budget. The full-stage trajectory of
promoted candidates was monotone until the win (−11,032 → −10,973 → −10,942 →
**−10,811**) and no later candidate beat it in four further generations.

Read this as one sample, not a benchmark: a 1.6 % hold-out gain on one
instance class, bought for $3.24 and a night of CPU. The interesting part is
that the loop's economics worked end to end — the proxy stage killed 20 of 29
candidates for cents, the hold-out confirmed the winner generalises to an
instance it never saw, and the run stopped itself on spend-since-improvement
rather than running to the generation cap.
