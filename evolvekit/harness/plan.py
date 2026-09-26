"""The automatic plan: how many combinations, how many rounds, on how many
workers, for the hours a study is given.

Inputs: n training cases, m test cases, r runs per case, T the wall time of
one run (the preview's, scaled to the chosen time limit), H the hours, and w
workers (physical cores but one, at most n * r; `evolvekit/cpus.py`).

    final check      C = ceil(2 * m * k / w) * T          k = 3 check seeds (2 if 3 do not fit)
    search           S = 0.95 * H - C
    starting point   B = ceil(n * r / w) * T
    one round        R = ceil(c * q * r / w) * T_q + ceil(2 * n * r / w) * T    with screening
                     R = ceil(c * n * r / w) * T                                without

Screening -- every combination first on q = min(4, ceil(n / 3)) cases at a
quarter of the time limit, the best two on everything -- needs n >= 6 and a
time limit of at least 10 s that the application keeps. c, the combinations
per round, is the largest value in 4..12 that leaves at least 6 rounds, else
at least 3; with fewer than 3 the plan is blocked, with three concrete ways
out. The engine gets `generations = ceil((S - B) / R) + 2`, `budget.max_hours
= S / 3600` -- which is what stops the search -- and `stop.patience` equal to
the generations, so the run uses the time it is given.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Mapping

from evolvekit.cpus import plan_workers

__all__ = ["Plan", "make_plan", "CHECK_SEEDS"]

CHECK_SEEDS = (1001, 1002, 1003)
"""The seeds of the final check: never among the search's own (0 .. r-1)."""

MIN_ROUNDS, GOOD_ROUNDS = 3, 6
CHILDREN = range(4, 13)


@dataclass(frozen=True)
class Plan:
    workers: int
    pin_cpus: tuple[int, ...]
    children: int
    generations: int
    rounds: int
    """How many rounds (generations after the starting point) the hours buy."""
    screening: bool
    screen_cases: int
    screen_time_limit_s: float
    run_s: float
    screen_run_s: float
    final_check_s: float
    search_s: float
    seed_round_s: float
    round_s: float
    check_seeds: int
    hours: float
    test_cases: int = 0
    blocked: str = ""
    """Why the study cannot start as it stands, with the ways out; empty when it can."""
    ways_out: tuple[str, ...] = ()
    adjusted: tuple[str, ...] = ()
    """The numbers an expert set by hand under "Adjust"."""

    @property
    def max_hours(self) -> float:
        return self.search_s / 3600.0

    @property
    def combinations(self) -> int:
        return self.rounds * self.children

    @staticmethod
    def fixed(children: int = 4, generations: int = 3, workers: int = 1, pin: tuple[int, ...] = ()) -> "Plan":
        """A plan with given numbers and no timing: for checks and tests."""
        return Plan(
            workers=workers, pin_cpus=tuple(pin), children=children, generations=generations,
            rounds=max(0, generations - 2), screening=False, screen_cases=0, screen_time_limit_s=0.0,
            run_s=1.0, screen_run_s=1.0, final_check_s=0.0, search_s=3600.0, seed_round_s=1.0,
            round_s=1.0, check_seeds=3, hours=1.0,
        )

    def summary(self, now: datetime | None = None) -> str:
        """The plan in words, for the study's Limits step."""
        if self.blocked:
            return self.blocked
        moment = (now or datetime.now()) + timedelta(hours=self.hours)
        check = (
            f"a final check on {self.test_cases} held-back case{'s' if self.test_cases != 1 else ''} "
            f"(≈ {_minutes(self.final_check_s)})"
            if self.test_cases else "no final check (no test cases)"
        )
        return (
            f"about {self.rounds} round{'s' if self.rounds != 1 else ''}, about {self.combinations} combinations "
            f"tried, {check}; done around {moment:%H:%M}."
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "workers": self.workers, "pin_cpus": list(self.pin_cpus), "children": self.children,
            "generations": self.generations, "rounds": self.rounds, "combinations": self.combinations,
            "screening": self.screening, "screen_cases": self.screen_cases,
            "screen_time_limit_s": self.screen_time_limit_s, "run_s": self.run_s,
            "final_check_s": self.final_check_s, "search_s": self.search_s, "max_hours": self.max_hours,
            "check_seeds": self.check_seeds, "hours": self.hours, "test_cases": self.test_cases,
            "blocked": self.blocked, "ways_out": list(self.ways_out), "adjusted": list(self.adjusted),
        }


def _minutes(seconds: float) -> str:
    if seconds < 90:
        return f"{max(1, round(seconds))} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h"


@dataclass
class _Shape:
    n: int
    m: int
    r: int
    time_limit_s: float
    overhead_s: float
    accepts_time_limit: bool
    measured_s: float
    hours: float
    workers: int
    pin: tuple[int, ...] = field(default_factory=tuple)

    def run_s(self, time_limit: float | None = None) -> float:
        limit = self.time_limit_s if time_limit is None else time_limit
        if self.accepts_time_limit:
            return limit + self.overhead_s
        # An application that does not stop itself runs as long as it runs:
        # what the preview measured, never more than the kill limit.
        return min(self.measured_s, limit)


def _layout(shape: _Shape, children: int, check_seeds: int, screening: bool) -> dict[str, float]:
    n, m, r, w = shape.n, shape.m, shape.r, shape.workers
    run = shape.run_s()
    final = math.ceil(2 * m * check_seeds / w) * run if m else 0.0
    search = 0.95 * shape.hours * 3600.0 - final
    seed = math.ceil(n * r / w) * run
    q = min(4, math.ceil(n / 3))
    screen_run = shape.run_s(shape.time_limit_s / 4)
    if screening:
        round_s = math.ceil(children * q * r / w) * screen_run + math.ceil(2 * n * r / w) * run
    else:
        round_s = math.ceil(children * n * r / w) * run
    return {"final": final, "search": search, "seed": seed, "round": round_s, "q": q, "screen_run": screen_run,
            "rounds": (search - seed) / round_s if round_s > 0 else 0.0}


def make_plan(
    *,
    training: int,
    test: int,
    runs_per_case: int,
    time_limit_s: float,
    hours: float,
    overhead_s: float = 0.0,
    measured_s: float | None = None,
    accepts_time_limit: bool = True,
    workers: int | None = None,
    pin_cpus: tuple[int, ...] | list[int] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Plan:
    """The plan for a study (see the module docstring). `overhead_s` is what a
    run takes beyond its time limit (loading the case, starting the
    interpreter), from the preview; `measured_s` is the preview's whole run,
    for an application that does not stop itself."""
    n, m, r = max(1, training), max(0, test), max(1, runs_per_case)
    if workers is None:
        workers, pin = plan_workers(n * r)
    else:
        pin = list(pin_cpus or [])
    workers = max(1, min(workers, n * r))
    shape = _Shape(
        n=n, m=m, r=r, time_limit_s=float(time_limit_s), overhead_s=max(0.0, float(overhead_s)),
        accepts_time_limit=accepts_time_limit,
        measured_s=float(measured_s if measured_s is not None else time_limit_s),
        hours=float(hours), workers=workers, pin=tuple(pin[:workers]),
    )
    overrides = dict(overrides or {})
    screening = n >= 6 and accepts_time_limit and time_limit_s >= 10
    if "screening" in overrides:
        screening = bool(overrides["screening"]) and n >= 2 and accepts_time_limit
    chosen = None
    for check_seeds in (3, 2):
        for enough in (GOOD_ROUNDS, MIN_ROUNDS):
            for children in reversed(CHILDREN):
                layout = _layout(shape, children, check_seeds, screening)
                if layout["rounds"] >= enough:
                    chosen = (children, check_seeds, layout)
                    break
            if chosen:
                break
        if chosen:
            break
    blocked, ways_out = "", ()
    if chosen is None:
        chosen = (min(CHILDREN), 3, _layout(shape, min(CHILDREN), 3, screening))
        ways_out = _ways_out(shape, screening)
        blocked = (
            "With these choices the search gets fewer than 3 rounds, too few to learn anything. "
            + " ".join(ways_out)
        )
    children, check_seeds, layout = chosen
    adjusted = []
    if "children" in overrides:
        children = max(1, int(overrides["children"]))
        layout = _layout(shape, children, check_seeds, screening)
        adjusted.append("children")
    rounds = max(0, math.floor(layout["rounds"]))
    generations = max(1, math.ceil(max(0.0, layout["search"] - layout["seed"]) / layout["round"]) + 2) if layout["round"] > 0 else 3
    if "generations" in overrides:
        generations = max(1, int(overrides["generations"]))
        rounds = generations
        adjusted.append("generations")
    if "workers" in overrides:
        adjusted.append("workers")
    plan = Plan(
        workers=shape.workers, pin_cpus=shape.pin, children=children, generations=generations, rounds=rounds,
        screening=screening, screen_cases=layout["q"] if screening else 0,
        screen_time_limit_s=shape.time_limit_s / 4 if screening else 0.0,
        run_s=shape.run_s(), screen_run_s=layout["screen_run"], final_check_s=layout["final"],
        search_s=max(0.0, layout["search"]), seed_round_s=layout["seed"], round_s=layout["round"],
        check_seeds=check_seeds, hours=shape.hours, test_cases=m, blocked=blocked, ways_out=ways_out,
        adjusted=tuple(adjusted),
    )
    if "workers" in overrides:
        workers = max(1, min(int(overrides["workers"]), n * r))
        plan = replace(plan, workers=workers, pin_cpus=plan.pin_cpus[:workers])
    return plan


def _ways_out(shape: _Shape, screening: bool) -> tuple[str, ...]:
    """Three concrete ways to at least 3 rounds, each changing one thing."""
    ways = []
    for n in range(shape.n - 1, 0, -1):
        trial = replace(shape, n=n, workers=max(1, min(shape.workers, n * shape.r)))
        if _layout(trial, min(CHILDREN), 3, screening and n >= 6)["rounds"] >= MIN_ROUNDS:
            ways.append(f"Use at most {n} training case{'s' if n != 1 else ''}.")
            break
    else:
        # No number of training cases helps: the final check alone is too
        # long. Fewer held-back cases shorten it.
        for m in range(shape.m - 1, -1, -1):
            if _layout(replace(shape, m=m), min(CHILDREN), 3, screening)["rounds"] >= MIN_ROUNDS:
                ways.append(f"Hold back at most {m} test case{'s' if m != 1 else ''} for the final check.")
                break
    if shape.accepts_time_limit:
        limit = shape.time_limit_s
        while limit > 1:
            limit = max(1.0, math.floor(limit * 0.8))
            trial = replace(shape, time_limit_s=limit)
            if _layout(trial, min(CHILDREN), 3, screening and limit >= 10)["rounds"] >= MIN_ROUNDS:
                ways.append(f"Give each case at most {limit:g} s.")
                break
    layout = _layout(shape, min(CHILDREN), 3, screening)
    hours = (MIN_ROUNDS * layout["round"] + layout["seed"] + layout["final"]) / 0.95 / 3600.0
    ways.append(f"Allow at least {math.ceil(hours * 4) / 4:g} hours.")
    return tuple(ways)
