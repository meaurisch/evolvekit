# PyVRP

[PyVRP](https://pyvrp.org) 0.14, the vehicle routing solver, behind the
evolvekit harness SDK. A study on it either **tunes PyVRP's settings** — the
lowest cost within your time limit — or **changes the data PyVRP is given**
(vehicle costs, fleet, shifts, time windows, prizes, service times) and judges
the plans that come out.

## The application

The Python in which `import pyvrp` works, PyVRP 0.14. In a terminal where
PyVRP runs, this prints its path:

```
python -c "import sys, pyvrp; print(sys.executable)"
```

## Cases

**JSON requests** in the format of evolvekit's pyvrp_hard benchmark
(`"format": "pyvrp-hard/1"`, see `benchmarks/pyvrp_hard/README.md` in the
evolvekit repository): locations in metres, times in seconds from the start of
the day, loads in kilograms, cubic decimetres and (optionally) cold boxes,
costs in 1/10,000 of a currency unit, vehicle types with a `class`. The same
file with `"format": "pyvrp-request/1"` may also carry:

- `tags` — a list of words on clients, shipments and vehicle types
  (`"tags": ["vip", "frozen"]`), which a study can select by;
- `time_zero` — the clock time of second 0 (`"06:00"`; `"00:00"` if absent);
- `cost_unit` — currency per cost unit (`0.0001` if absent);
- costs with one decimal.

**VRPLIB files** (`.vrp`, `.txt`), read with `pyvrp.read(path,
round_func="round")`. They suit settings studies only: without vehicle
classes or tags there is nothing for a data change to select by, and the
runner refuses one.

`samples/` holds ten requests of 60 to 240 tasks, made by the benchmark's
generator with a seeded pass that adds tags; `samples/make_samples.py`
regenerates them byte for byte.

## Units

The tables speak metres (`_m`), seconds after the request's time zero (`_s`),
kilograms / cubic decimetres / cold boxes (`_kg`, `_dm3`, `_boxes`), and
currency: a vehicle's `fixed_cost` per day used, `unit_distance_cost` per
kilometre, `unit_duration_cost` and `unit_overtime_cost` per hour, prizes.
A van in the sample city-60 costs 100 a day, 0.30 per km and 21.60 per hour.

PyVRP computes with integers, and a van's distance cost is the integer 3 in
the request's unit: a data change that scaled it by 0.9 would get 3 again. So
PyVRP sees every cost **ten times finer** than the request states it, and the
three settings measured in cost units — `min_penalty`, `max_penalty` and
`weight_wait_time` — are scaled with them. Every setting therefore means what
it means for the request as written, and a settings file from a study works
unchanged with PyVRP on the requests as written (with the benchmark's
`solve.py` on `pyvrp-hard/1` files, or with your own PyVRP code).

## Tables

| Table | One row per |
|---|---|
| `request` | the request: name, format, time zero, load dimensions |
| `tasks` | stop to serve: clients keep their number, shipment *j* is its pickup (C + 2j) and delivery (C + 2j + 1); kind, place, window, service time, loads, prize, required, group, shipment |
| `task_tags` | tag on a task |
| `vehicle_types` | vehicle type: class, profile, count, capacities, costs, shift, overtime, range, reloads |
| `vehicle_tags` | tag on a vehicle type |
| `depots` | depot: place, opening hours, loading time |
| `routes` | route of the plan: vehicle type and class, times, distance, overtime, waiting, lateness, trips, `cost` at the untouched request's rates and `solver_cost` at the rates PyVRP saw |
| `visits` | task served, in route order: trip, start and end of service, waiting, lateness, load on board, previous and next task |
| `unassigned` | task left out, with the prize forgone |
| `summary` | the plan: PyVRP's objective, feasibility, required tasks left out, iterations, runtime |

`orig_tasks`, `orig_vehicle_types`, … hold the request before a study's data
changes.

## Settings

All 27 of the benchmark's solver front end: the iterated local search, the
penalty manager, the neighbourhood, the perturbation, and the twelve
local-search moves the model does not depend on. Every default is PyVRP's own,
and every range is the one the benchmark's tuning used.

**Recommended** (what "Tune solver settings" starts with): the settings that
mattered most in the two tuning runs on the pyvrp_hard benchmark — importance
per run from 18 and 9 evaluated configurations, averaged:

| Setting | Run 1 | Run 2 | Mean |
|---|---|---|---|
| `solutions_between_updates` | 0.48 | 0.87 | 0.68 |
| `max_penalty` | 0.37 | 0.87 | 0.62 |
| `target_feasible` | 0.36 | 0.85 | 0.60 |
| `use_swap21` | 0.61 | 0.55 | 0.58 |
| `exhaustive_on_best` | 0.59 | 0.52 | 0.56 |
| `penalty_decrease` | 0.09 | 0.85 | 0.47 |
| `min_perturbations` | 0.85 | 0.00 | 0.42 |
| `num_neighbours` | 0.52 | 0.08 | 0.30 |

`min_penalty` (0.49) and `feas_tolerance` (0.47) rank just above some of
these, but they clamp and damp the same penalty updates that
`solutions_between_updates`, `target_feasible` and `penalty_decrease` already
steer. `num_neighbours` ranks lower and is in anyway: PyVRP's documentation
names the neighbourhood size as the trade between better moves and more
iterations, which a time limit makes decisive. Few configurations stand behind
these numbers: a starting point, not a ranking.

## Data changes

| Lever | Changes | Comparable? |
|---|---|---|
| `vehicle_costs` | `fixed_cost`, `unit_distance_cost`, `unit_duration_cost`, `unit_overtime_cost` | changes what PyVRP minimises: judge by `real_cost`, `deliveries_per_hour`, … never `solver_cost` |
| `fleet_size` | `num_available` (0 takes a type out) | a real change: every KPI |
| `shifts` | `shift_duration_s`, `max_overtime_s` | a real change: every KPI |
| `time_windows` | in code: `scale` widens each window around its middle, `add` moves it (seconds) | a real change: every KPI |
| `prizes` | `prize` (a shipment's sits on its delivery task) | part of what PyVRP minimises: judge by `real_cost` or `served_tasks` |
| `service_times` | `service_s` | a real change: every KPI |

A change that leaves a negative cost, a window that closes before it opens or
no vehicle at all is refused before PyVRP runs, with a sentence that says so.

## KPIs

`solver_cost` (PyVRP's objective; a plan that breaks a rule is priced above
every plan that keeps them, as in the benchmark), `real_cost` (the plan at the
untouched rates, plus the prizes forgone), `distance_km`, `duration_h`,
`routes_used`, `served_tasks`, `deliveries_per_hour` (stops that hand goods
over, per hour on the road, lateness counted at its real length), `late_tasks`,
`late_h`, `wait_h`, `missed_required`, `feasible`.

KPI templates: late tasks with a tag; hours of waiting at tasks whose window
opens between two clock times; the share of tasks a vehicle class serves;
routes longer than a number of hours.

## Study templates

- **Tune solver settings** — the eight recommended settings, goal
  `solver_cost`, no guardrails (the objective already prices broken rules).
- **Route cost sets → deliveries per hour** — distance and time costs of
  vans, box trucks and evening vans, each scaled within 0.5–2; the settings
  stay fixed; goal `deliveries_per_hour`; guardrail `missed_required` ≤ 0.

## When PyVRP changes

`discover()` reads the installed PyVRP's parameter classes and local-search
operators; `evolvekit harness check` names every setting that appeared,
disappeared or changed its default, and the key in `harness.yaml` to edit.
