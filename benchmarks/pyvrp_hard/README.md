# `pyvrp_hard` — an artificial benchmark for tuning PyVRP

Ten synthetic vehicle-routing instances with 1,000–3,000 clients, a
heterogeneous fleet, and **every modelling feature PyVRP 0.14.0 offers switched
on at once**, plus four further "fresh" instances that are never used for
tuning and two tiny smoke instances for tests. The task it poses: *find the
solver configuration that reaches the lowest objective within 10 minutes of
wall clock per instance*; PyVRP's defaults are the baseline.

Nothing here is downloaded and nothing is real-world data. Every number comes
from `random.Random(seed)` and a row of the scenario table in `generate.py`.

## Files

| File | What it is |
|---|---|
| `generate.py` | the deterministic generator and the scenario table. Only `+ - * /` and `sqrt` touch floats and every stored number is an integer, so a regenerated file is **byte-identical** on any machine |
| `instance.py` | `load_instance`, `build_problem_data` (JSON → `pyvrp.ProblemData`; travel matrices are computed here, vectorised, from integer coordinates and per-profile parameters, which keeps a 3,000-client file at ~0.5 MB), `feature_inventory`, `missing_features` |
| `solve.py` | **the only thing a tuner sees**: a plain command-line solver, as if it were written in another language |
| `verify.py` | proves feasibility by solving every instance with pure defaults |
| `manifest.json` | per instance: scenario parameters, seed, counts, feature inventory, file size and **SHA-256** |
| `verification.json` | the last verification run, keyed by instance, each result carrying the hash of the file it solved |
| `smoke/` | the two smoke instances, committed (17 kB and 32 kB) |
| `instances/` | the tuning and fresh instances — **gitignored**, 0.2–0.6 MB each; regenerate them |

```
python generate.py --set tuning --out instances/     # t01 … t10
python generate.py --set fresh  --out instances/     # f01 … f04, never tuned on
python generate.py --set smoke  --out smoke/
python verify.py instances/ --time-limit 120 --out verification.json
```

`tests/test_pyvrp_hard_benchmark.py` regenerates every set and compares it with
the manifest hash by hash.

## The instances

| Instance | Set | Clients | Locations | Depots | Geography | Area | Windows | Classes | Profiles | Load dims | Vehicles / types | Seed |
|---|---|--:|--:|--:|---|--:|---|--:|--:|--:|--:|--:|
| `f01-n1100-multicity-banded` | fresh | 1,100 | 1,027 | 4 | multi city | 115 km | banded | 3 | 3 | 2 | 73 / 19 | 23565 |
| `f02-n1700-uniform-loose` | fresh | 1,700 | 1,557 | 4 | uniform | 90 km | loose | 3 | 2 | 3 | 174 / 22 | 759516 |
| `f03-n2300-coresatellites-mixed` | fresh | 2,300 | 2,096 | 4 | core satellites | 75 km | mixed | 3 | 2 | 2 | 201 / 24 | 459999 |
| `f04-n2900-ring-tight` | fresh | 2,900 | 2,677 | 4 | ring | 60 km | tight | 3 | 2 | 2 | 163 / 23 | 301004 |
| `s01-n60-uniform-mixed` | smoke | 60 | 62 | 2 | uniform | 20 km | mixed | 3 | 2 | 2 | 19 / 9 | 61 |
| `s02-n120-metro-tight` | smoke | 120 | 123 | 3 | core satellites | 30 km | tight | 4 | 3 | 3 | 38 / 17 | 122 |
| `t01-n1000-uniform-city-tight` | tuning | 1,000 | 927 | 2 | uniform | 25 km | tight | 4 | 3 | 3 | 77 / 12 | 1101 |
| `t02-n1200-clustered-banded` | tuning | 1,200 | 1,150 | 3 | clustered | 60 km | banded | 4 | 2 | 3 | 83 / 18 | 1202 |
| `t03-n1400-corridor-loose` | tuning | 1,400 | 1,375 | 3 | corridor | 120 km | loose | 3 | 3 | 2 | 150 / 17 | 1303 |
| `t04-n1600-metro-mixed` | tuning | 1,600 | 1,514 | 4 | core satellites | 40 km | mixed | 5 | 3 | 3 | 132 / 28 | 1404 |
| `t05-n1800-multicity-banded` | tuning | 1,800 | 1,748 | 4 | multi city | 150 km | banded | 4 | 3 | 2 | 192 / 27 | 1505 |
| `t06-n2000-ring-tight` | tuning | 2,000 | 1,912 | 3 | ring | 50 km | tight | 3 | 2 | 2 | 120 / 14 | 1606 |
| `t07-n2200-clustered-region-mixed` | tuning | 2,200 | 2,152 | 5 | clustered | 100 km | mixed | 4 | 2 | 2 | 235 / 30 | 1707 |
| `t08-n2500-uniform-region-loose` | tuning | 2,500 | 2,400 | 4 | uniform | 80 km | loose | 3 | 2 | 2 | 229 / 19 | 1808 |
| `t09-n2800-metro-banded` | tuning | 2,800 | 2,596 | 5 | core satellites | 70 km | banded | 4 | 2 | 2 | 260 / 34 | 1909 |
| `t10-n3000-corridor-mixed` | tuning | 3,000 | 2,912 | 5 | corridor | 90 km | mixed | 4 | 2 | 2 | 134 / 29 | 2010 |

Six geographies (uniform, Gaussian clusters, a corridor, a ring, a core with
satellite towns, several distant cities), areas from a 25 km city to a 150 km
region — so `max_distance` and the shift limits bind differently — four
time-window regimes, 2–5 depots, 3–5 vehicle classes, two or three routing
profiles (a third only up to 1,800 clients, to bound memory), two or three load
dimensions. The fresh set draws new seeds **and** new scenario parameters from
the same families.

What varies inside the tuning set, as a share of the clients:

| Instance | pickup+delivery | backhaul only | optional | release time | in groups | shipments | shared locations | tight windows |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| `t01-n1000-uniform-city-tight` | 14.4 % | 4.8 % | 7.7 % | 9.6 % | 4.0 % | 20 | 18.3 % | 57.2 % |
| `t02-n1200-clustered-banded` | 9.7 % | 7.8 % | 4.8 % | 5.8 % | 3.0 % | 12 | 11.0 % | 0.0 % |
| `t03-n1400-corridor-loose` | 4.9 % | 11.8 % | 9.8 % | 3.9 % | 2.0 % | 7 | 5.4 % | 0.0 % |
| `t04-n1600-metro-mixed` | 11.4 % | 5.7 % | 5.7 % | 7.6 % | 5.0 % | 20 | 14.2 % | 23.9 % |
| `t05-n1800-multicity-banded` | 7.8 % | 9.7 % | 11.7 % | 4.8 % | 3.0 % | 15 | 8.3 % | 0.0 % |
| `t06-n2000-ring-tight` | 19.2 % | 3.9 % | 4.8 % | 11.5 % | 4.0 % | 30 | 13.0 % | 59.4 % |
| `t07-n2200-clustered-region-mixed` | 5.9 % | 14.7 % | 14.7 % | 3.0 % | 2.0 % | 14 | 6.6 % | 26.0 % |
| `t08-n2500-uniform-region-loose` | 9.7 % | 9.7 % | 9.7 % | 6.8 % | 3.0 % | 25 | 11.1 % | 0.0 % |
| `t09-n2800-metro-banded` | 13.4 % | 6.7 % | 6.7 % | 8.6 % | 4.0 % | 51 | 18.5 % | 0.0 % |
| `t10-n3000-corridor-mixed` | 11.6 % | 7.8 % | 7.8 % | 5.8 % | 3.0 % | 30 | 9.0 % | 24.2 % |

## Every feature, in every instance

`instance.missing_features()` is empty for all 16 instances (asserted by the
tests and recorded in `verification.json`). The exact counts are in
`manifest.json` under `features`; the ranges below are over the ten tuning
instances.

| PyVRP 0.14.0 feature | How it is used |
|---|---|
| `Location`, several clients per location | 5–19 % of clients share a location (apartment blocks, parcel lockers) |
| multiple depots, depot time windows, depot `service_duration` | 2–5 depots with 2–4 distinct opening windows; loading time at the start of every trip |
| multiple vehicle types | 12–34 types per instance (class × depot × variant), 77–260 vehicles |
| capacities in several load dimensions | kilograms and cubic decimetres everywhere; cold boxes as a third dimension on three instances; 19–58 clients per instance are too big for the smallest class |
| `fixed_cost`, `unit_distance_cost`, `unit_duration_cost` | on every vehicle type, different per class |
| shift windows `tw_early` / `tw_late`, `start_late`, `shift_duration`, `max_distance` | on every vehicle type; 6–14 distinct shift windows per instance |
| `max_overtime`, `unit_overtime_cost` | on 5–29 types per instance |
| `reload_depots`, `max_reloads` (multi-trip) | on 11–29 types, 3–29 of them with a choice of reload depots |
| `start_depot != end_depot` (open routes) | 2–10 types per instance |
| `initial_load` | 2–5 types per instance start the day pre-loaded |
| per-vehicle-type routing `profile`, asymmetric matrices | 2–3 profiles with their own speed, detour and congestion zone; **all** are asymmetric; one profile per instance has access restrictions (27–97 locations it may not enter) |
| client time windows, `service_duration`, `release_time` | windows on 34–85 % of clients, tight ones on 0–59 %; service time on all; release times on 3–12 % |
| simultaneous pickup and delivery, pure backhauls | 5–19 % and 4–15 % of clients |
| optional clients with a `prize` | 5–15 %; prizes straddle the marginal cost of service, so some are worth serving and some are not |
| mutually exclusive `ClientGroup`s, required and optional | 12–49 groups of two or three, both kinds in every instance |
| `Shipment`s (pickup → delivery on one route), required and optional, with windows on both stops | 7–51 per instance |

Two things PyVRP 0.14.0 exports are **not** instance features and are therefore
not used: `PiecewiseLinearFunction` (no model class accepts one in this version
— confirmed by searching the installed package) and `minimise_fleet` (a
function, which refuses more than one vehicle type and optional clients). No
two features had to be split across instances: a probe with all of the above in
one 84-client instance solved feasibly before the generator was written.

## Units

| Quantity | Unit |
|---|---|
| distance | metre |
| duration | second (t = 0 is 06:00) |
| load | kilogram · cubic decimetre · cold boxes |
| cost | 1e-4 currency units |

The cost unit is squeezed from two sides. PyVRP's unit costs are integers per
metre and per second, so 0.30 per kilometre needs a unit finer than a cent. And
PyVRP clamps its violation penalties at `PenaltyParams.max_penalty` (100,000 by
default): at 1e-4 that ceiling is 10 currency units per second of time warp
against a van that costs 100 to put on the road, which still bites; at 1e-6 it
would be 0.10 and the search would trade a minute of lateness for a vehicle.
The verification table below divides by 10,000.

## `solve.py`: the interface

```
python solve.py --instance instances/t01-n1000-uniform-city-tight.json \
    --time-limit 600 --seed 1 --num-neighbours 40 --exhaustive-on-best false
```

One flag per tunable, defaults exactly PyVRP 0.14.0's:
`--num-iters-no-improvement`, `--history-length`, `--exhaustive-on-best`,
`--solutions-between-updates`, `--penalty-increase`, `--penalty-decrease`,
`--target-feasible`, `--feas-tolerance`, `--min-penalty`, `--max-penalty`,
`--weight-wait-time`, `--num-neighbours`, `--symmetric-proximity`,
`--min-perturbations`, `--max-perturbations`, and an on/off flag for each
local-search operator that can be switched off without making a feature
unreachable (`--use-relocate2`, `--use-relocate3`, `--use-swap11` …
`--use-swap33`, `--use-swap-tails`, `--use-relocate-alternative`,
`--use-replace-optional-client`, `--use-replace-optional-shipment`). Booleans
are `true` / `false`. `--params-json` takes the same parameters as a JSON
object, as a file or as text.

The time limit covers the whole `pyvrp.solve` call — neighbourhood,
construction and search. Loading the instance and building the matrices is
outside it and reported as `load_s`. The last line of stdout (and `--out`) is
one JSON object: `objective`, `feasible`, `cost`, `penalised_cost`,
`iterations`, `runtime_s`, `time_limit_s`, `load_s`, `num_routes`, `num_trips`,
`distance`, `duration`, `overtime`, the cost split, prizes collected and
uncollected, unserved optional clients and shipments, `vehicle_types_used`,
`convergence` (best feasible objective at 10 %, 20 %, … 100 % of the time
limit), `instance`, `seed`, the resolved `params` and `pyvrp_version`.
`objective` is the cost of the best **feasible** solution; when there is none it
is a penalised cost from a fixed evaluator derived from the instance — a finite
number far above any feasible cost — with `feasible: false`. Invalid parameters
(`--min-perturbations` above `--max-perturbations`, `--penalty-increase` below
1) exit with code **2** and a one-line message, never a traceback.

## Verification

Every instance solved with PyVRP's defaults, seed 1, one process at a time,
120 s (20 s for the smoke set). Objective in currency units.

| Instance | Feasible | Objective | First feasible | Iterations | Routes / trips | Reloads | Types used | Overtime routes | Optional served / skipped | Peak RSS | Load |
|---|:-:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| `f01-n1100-multicity-banded` | yes | 7,007 | 1 s | 20,097 | 29 / 36 | 7 | 14 of 19 | 2 | 54 / 32 | 136 MB | 0.4 s |
| `f02-n1700-uniform-loose` | yes | 25,355 | 2 s | 11,735 | 91 / 104 | 13 | 20 of 22 | 2 | 67 / 33 | 188 MB | 0.6 s |
| `f03-n2300-coresatellites-mixed` | yes | 23,439 | 4 s | 8,324 | 88 / 117 | 29 | 16 of 24 | 1 | 75 / 56 | 307 MB | 1.1 s |
| `f04-n2900-ring-tight` | yes | 20,699 | 9 s | 5,134 | 71 / 82 | 11 | 18 of 23 | 4 | 244 / 114 | 477 MB | 1.4 s |
| `s01-n60-uniform-mixed` | yes | 935 | 0 s | 6,624 | 3 / 6 | 3 | 3 of 9 | 1 | 5 / 1 | 40 MB | 0.2 s |
| `s02-n120-metro-tight` | yes | 1,760 | 0 s | 4,121 | 7 / 12 | 5 | 6 of 17 | 1 | 5 / 6 | 44 MB | 0.2 s |
| `t01-n1000-uniform-city-tight` | yes | 9,053 | 1 s | 13,623 | 40 / 50 | 10 | 12 of 12 | 0 | 60 / 17 | 122 MB | 0.4 s |
| `t02-n1200-clustered-banded` | yes | 12,984 | 1 s | 14,131 | 42 / 60 | 18 | 14 of 18 | 0 | 31 / 27 | 132 MB | 0.4 s |
| `t03-n1400-corridor-loose` | yes | 18,032 | 2 s | 10,200 | 63 / 80 | 17 | 10 of 17 | 8 | 79 / 58 | 211 MB | 0.6 s |
| `t04-n1600-metro-mixed` | yes | 14,534 | 2 s | 9,303 | 68 / 100 | 32 | 23 of 28 | 2 | 53 / 38 | 248 MB | 0.7 s |
| `t05-n1800-multicity-banded` | yes | 23,440 | 2 s | 13,479 | 72 / 100 | 28 | 14 of 27 | 16 | 124 / 86 | 318 MB | 0.9 s |
| `t06-n2000-ring-tight` | yes | 15,346 | 2 s | 10,633 | 70 / 87 | 17 | 14 of 14 | 2 | 67 / 29 | 262 MB | 0.8 s |
| `t07-n2200-clustered-region-mixed` | yes | 38,837 | 4 s | 6,272 | 123 / 149 | 26 | 23 of 30 | 0 | 167 / 156 | 321 MB | 0.9 s |
| `t08-n2500-uniform-region-loose` | yes | 26,267 | 3 s | 12,744 | 82 / 93 | 11 | 16 of 19 | 13 | 150 / 92 | 390 MB | 1.1 s |
| `t09-n2800-metro-banded` | yes | 37,390 | 5 s | 9,087 | 142 / 176 | 34 | 31 of 34 | 7 | 99 / 89 | 451 MB | 1.3 s |
| `t10-n3000-corridor-mixed` | yes | 20,888 | 7 s | 5,771 | 82 / 93 | 11 | 24 of 29 | 1 | 166 / 67 | 557 MB | 1.5 s |

Feasibility is found within nine seconds everywhere, so the contest is about
cost. The heterogeneous fleet matters: good solutions use 10–31 vehicle types,
reload 7–34 times and use overtime on most instances. Peak memory is 0.6 GB at
worst, so three solver processes fit beside each other in 16 GB.

One modelling flaw was found by this step and fixed in the generator rather
than by picking another seed: the fresh scenario draw could keep a third routing
profile that none of the sampled vehicle classes drives on, which shipped two
dead 1,700 × 1,700 matrices in `f02`. Unused profiles are now dropped, and a
scenario with one is refused. No randomness is consumed by the fix, so every
other instance is byte-identical to what it was.

Machine: Windows 11, Intel Core i7-1185G7 (4 cores / 8 threads), 16 GB,
Python 3.12.10, PyVRP 0.14.0.
