"""The automatic plan: how many combinations, how many rounds, on how many
workers, for the hours a study is given.

Inputs: n training cases, m test cases, r runs per case, T the wall time of
one run (the preview's, scaled to the chosen time limit), H the hours, and w
workers (physical cores but one, at most n * r; `evolvekit/cpus.py`).

    final check      C = ceil(2 * m * k / w) * T          k check seeds, see below
    search           S = 0.95 * H - C
    starting point   B = ceil(n * r / w) * T
    one round        R = ceil(c * q * r / w) * T_q + ceil(2 * n * r / w) * T + c * A    with screening
                     R = ceil(c * n * r / w) * T + c * A                                without

A is the time the AI takes per child, on average: the children of a round are
written one after another before any of them runs, so a study with AI pays c
times A every round (`ai_seconds`: AI_CHANGE_S for a reply to the diff or the
rewrite prompt, AGENT_CHANGE_S for the coding agent, 0 for the operators that
call no model). A study without AI has A = 0: its plan is the plan it always had.

Screening -- every combination first on q = min(4, ceil(n / 3)) cases at a
quarter of the time limit, the best two on everything -- needs n >= 6 and a
time limit of at least 10 s that the application keeps. The final check gets
up to CHECK_SHARE = 15 % of the hours: one check seed costs ceil(2 * m / w)
* T, and k starts at kmax = max(3, min(12, floor(0.15 * H / that))) and
falls to 3 while the search cannot get 6 rounds, then 3 at 3 rounds, then 2
at 6 and at 3. c, the combinations per round, is the largest value in 4..12
that leaves at least 6 rounds, else at least 3; with fewer than 3 the plan
is blocked, with three concrete ways out. The engine gets `generations = ceil((S - B) / R) + 2`, `budget.max_hours
= S / 3600` -- which is what stops the search -- and `stop.patience` equal to
the generations, so the run uses the time it is given.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Mapping

from evolvekit.cpus import plan_workers
# The AI's time per change is the engine's too: the run's first round is projected with it (`stopping`).
from evolvekit.stopping import AGENT_CHANGE_S, AI_CHANGE_S, ai_seconds

__all__ = ["Plan", "make_plan", "ai_seconds", "plan_notes", "AI_CHANGE_S", "AGENT_CHANGE_S", "CHECK_SEEDS",
           "CHECK_SHARE", "DEFAULT_CHECK_SEEDS", "THIN_CHECK_CASES"]

CHECK_SEEDS = tuple(range(1001, 1013))
"""The seeds of the final check: never among the search's own (0 .. r-1).
A plan uses the first k -- as many as CHECK_SHARE of the hours pays for, at
least DEFAULT_CHECK_SEEDS, or 2 if those do not fit; a final check run again
with more takes the next ones."""
DEFAULT_CHECK_SEEDS = 3
CHECK_SHARE = 0.15
"""The share of the hours the final check may take."""

MIN_ROUNDS, GOOD_ROUNDS = 3, 6
CHILDREN = range(4, 13)

BLOCKED_REASON = "With these choices the search gets fewer than 3 rounds, too few to learn anything."
"""The first sentence of a blocked plan; its ways out follow it (`Plan.blocked`)."""

THIN_CHECK_CASES = 3
"""Below this many held-back cases a "use" is unlikely even for a real gain:
examples/cli-solver/README.md, step 4 -- with only two held-back cases, "not
distinguishable" is the likely outcome even for a real gain; "Hold back more
instances than that for a claim"."""


def plan_notes(test_cases: int) -> list[str]:
    """What a study should hear before it runs, besides the plan: with fewer
    than THIN_CHECK_CASES held-back cases (but some), that a "use" is unlikely.
    A note, not a warning: the study may run as it is."""
    if not 0 < test_cases < THIN_CHECK_CASES:
        return []
    return [f"With {test_cases} held-back case{'s' if test_cases != 1 else ''} a 'use' is unlikely: the worst case "
            "stays wide. Hold back more cases for a firmer answer."]


def check_notes(test_cases: int) -> list[str]:
    """`plan_notes` said again after the final check, where the answer is acted
    on (`study pr` and the PR's body): with fewer than THIN_CHECK_CASES
    held-back cases (but some), that the answer is thin. A fact about the check,
    not a claim about the change."""
    if not 0 < test_cases < THIN_CHECK_CASES:
        return []
    return [f"The final check had only {test_cases} held-back case{'s' if test_cases != 1 else ''}: its worst case "
            "stays wide, so this answer is thin. Hold back more cases for a firmer answer."]


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
    way_fixes: tuple[dict[str, Any], ...] = ()
    """The ways out a button can take, each `{"kind", "label", ...}`: `time_per_case`
    with its `seconds` ("Give each case 19 s"), `hours` with its `hours` ("Allow 30
    minutes"). The ways about which cases to learn from or hold back are not here:
    which cases is the person's choice."""
    adjusted: tuple[str, ...] = ()
    """The numbers an expert set by hand under "Adjust"."""
    ai_change_s: float = 0.0
    """The seconds the plan assumes for each AI change (`ai_seconds`); 0 without AI."""
    ai_child_s: float = 0.0
    """The AI's seconds per child of a round, on average: what each round pays for it."""

    @property
    def notes(self) -> list[str]:
        """`plan_notes` for this plan's held-back cases."""
        return plan_notes(self.test_cases)

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

    def summary(self, now: datetime | None = None, *, when: str | None = None) -> str:
        """The plan in words, for the study's Limits step. A study with AI says
        the time it assumes for each AI change, and a study with too few
        held-back cases for a firm answer ends with that note (`notes`).
        `when` replaces "done around 17:34": a study with a schedule says when
        it starts and ends on its windows (`scheduler.plan_window`)."""
        if self.blocked:
            return self.blocked
        moment = (now or datetime.now()) + timedelta(hours=self.hours)
        done = when or f"done around {moment:%H:%M}"
        check = (
            f"a final check on {self.test_cases} held-back case{'s' if self.test_cases != 1 else ''} "
            f"with {self.check_seeds} run{'s' if self.check_seeds != 1 else ''} each (≈ {_minutes(self.final_check_s)})"
            if self.test_cases else "no final check (no case is held back)"
        )
        assumes = f" (assumes about {self.ai_change_s:g} s for each AI change)" if self.ai_change_s else ""
        return " ".join([
            f"about {self.rounds} round{'s' if self.rounds != 1 else ''}, about {self.combinations} combinations "
            f"tried{assumes}, {check}; {done}.", *self.notes])

    def to_json(self) -> dict[str, Any]:
        return {
            "workers": self.workers, "pin_cpus": list(self.pin_cpus), "children": self.children,
            "generations": self.generations, "rounds": self.rounds, "combinations": self.combinations,
            "screening": self.screening, "screen_cases": self.screen_cases,
            "screen_time_limit_s": self.screen_time_limit_s, "run_s": self.run_s,
            "final_check_s": self.final_check_s, "search_s": self.search_s, "max_hours": self.max_hours,
            "check_seeds": self.check_seeds, "hours": self.hours, "test_cases": self.test_cases,
            "blocked": self.blocked, "blocked_reason": BLOCKED_REASON if self.blocked else "",
            "ways_out": list(self.ways_out), "way_fixes": [dict(fix) for fix in self.way_fixes],
            "adjusted": list(self.adjusted),
            "ai_change_s": self.ai_change_s, "notes": self.notes,
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
    ai_s: float = 0.0
    """The AI's seconds per child (`ai_seconds`)."""

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
    round_s += children * shape.ai_s  # the children are written one after another, before any runs
    return {"final": final, "search": search, "seed": seed, "round": round_s, "q": q, "screen_run": screen_run,
            "rounds": (search - seed) / round_s if round_s > 0 else 0.0}


def _check_attempts(shape: _Shape) -> list[tuple[int, int]]:
    """(check seeds, rounds wanted) in the order the plan tries them: as many
    seeds as CHECK_SHARE of the hours pays for, down to 3, each at 6 rounds;
    then 3 at 3 rounds; then 2 at 6 and at 3. With no held-back case the
    check costs nothing and 3 seeds stand."""
    kmax = DEFAULT_CHECK_SEEDS
    if shape.m:
        seed_cost = math.ceil(2 * shape.m / shape.workers) * shape.run_s()
        if seed_cost > 0:
            kmax = max(DEFAULT_CHECK_SEEDS, min(len(CHECK_SEEDS), math.floor(CHECK_SHARE * shape.hours * 3600.0 / seed_cost)))
    attempts = [(k, GOOD_ROUNDS) for k in range(kmax, DEFAULT_CHECK_SEEDS - 1, -1)]
    attempts.append((DEFAULT_CHECK_SEEDS, MIN_ROUNDS))
    attempts.extend([(2, GOOD_ROUNDS), (2, MIN_ROUNDS)])
    return attempts


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
    operators: Mapping[str, float] | None = None,
) -> Plan:
    """The plan for a study (see the module docstring). `overhead_s` is what a
    run takes beyond its time limit (loading the case, starting the
    interpreter), from the preview; `measured_s` is the preview's whole run,
    for an application that does not stop itself; `operators` the search's
    operator shares, whose AI time each round pays (`ai_seconds`)."""
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
        hours=float(hours), workers=workers, pin=tuple(pin[:workers]), ai_s=ai_seconds(operators)[0],
    )
    overrides = dict(overrides or {})
    screening = n >= 6 and accepts_time_limit and time_limit_s >= 10
    if "screening" in overrides:
        screening = bool(overrides["screening"]) and n >= 2 and accepts_time_limit
    chosen = None
    for check_seeds, enough in _check_attempts(shape):
        for children in reversed(CHILDREN):
            layout = _layout(shape, children, check_seeds, screening)
            if layout["rounds"] >= enough:
                chosen = (children, check_seeds, layout)
                break
        if chosen:
            break
    blocked, ways_out, way_fixes = "", (), ()
    if chosen is None:
        chosen = (min(CHILDREN), 3, _layout(shape, min(CHILDREN), 3, screening))
        ways = _ways(shape, screening)
        ways_out = tuple(text for text, _ in ways)
        way_fixes = tuple(fix for _, fix in ways if fix is not None)
        blocked = BLOCKED_REASON + " " + " ".join(ways_out)
    children, check_seeds, layout = chosen
    adjusted = []
    if "children" in overrides:
        children = max(1, int(overrides["children"]))
        layout = _layout(shape, children, check_seeds, screening)
        adjusted.append("children")
    rounds = max(0, math.floor(layout["rounds"]))
    generations = max(1, math.ceil(max(0.0, layout["search"] - layout["seed"]) / layout["round"]) + 2) if layout["round"] > 0 else 3
    if "rounds" in overrides:
        # The rounds people are shown; the engine gets the same headroom as
        # an automatic plan, and the hours still stop it.
        rounds = max(1, int(overrides["rounds"]))
        generations = rounds + 2
        adjusted.append("rounds")
        blocked, ways_out, way_fixes = "", (), ()  # an expert who sets the rounds has decided
    elif "generations" in overrides:
        generations = max(1, int(overrides["generations"]))
        rounds = generations
        adjusted.append("generations")
        blocked, ways_out, way_fixes = "", (), ()
    if "workers" in overrides:
        adjusted.append("workers")
    plan = Plan(
        workers=shape.workers, pin_cpus=shape.pin, children=children, generations=generations, rounds=rounds,
        screening=screening, screen_cases=layout["q"] if screening else 0,
        screen_time_limit_s=shape.time_limit_s / 4 if screening else 0.0,
        run_s=shape.run_s(), screen_run_s=layout["screen_run"], final_check_s=layout["final"],
        search_s=max(0.0, layout["search"]), seed_round_s=layout["seed"], round_s=layout["round"],
        check_seeds=check_seeds, hours=shape.hours, test_cases=m, blocked=blocked, ways_out=ways_out,
        way_fixes=way_fixes,
        adjusted=tuple(adjusted), ai_change_s=ai_seconds(operators)[1], ai_child_s=shape.ai_s,
    )
    if "workers" in overrides:
        workers = max(1, min(int(overrides["workers"]), n * r))
        plan = replace(plan, workers=workers, pin_cpus=plan.pin_cpus[:workers])
    return plan


def _ways(shape: _Shape, screening: bool) -> list[tuple[str, dict[str, Any] | None]]:
    """Three concrete ways to at least 3 rounds, each changing one thing: its
    sentence (`Plan.ways_out`) and the fix a button makes of it
    (`Plan.way_fixes`), or None."""
    ways: list[tuple[str, dict[str, Any] | None]] = []
    for n in range(shape.n - 1, 0, -1):
        trial = replace(shape, n=n, workers=max(1, min(shape.workers, n * shape.r)))
        if _layout(trial, min(CHILDREN), 3, screening and n >= 6)["rounds"] >= MIN_ROUNDS:
            ways.append((f"Let the search learn from at most {n} case{'s' if n != 1 else ''}.", None))
            break
    else:
        # No number of training cases helps: the final check alone is too
        # long. Fewer held-back cases shorten it.
        for m in range(shape.m - 1, -1, -1):
            if _layout(replace(shape, m=m), min(CHILDREN), 3, screening)["rounds"] >= MIN_ROUNDS:
                ways.append((f"Hold back at most {m} case{'s' if m != 1 else ''} for the final check.", None))
                break
    if shape.accepts_time_limit:
        limit = shape.time_limit_s
        while limit > 1:
            limit = max(1.0, math.floor(limit * 0.8))
            trial = replace(shape, time_limit_s=limit)
            if _layout(trial, min(CHILDREN), 3, screening and limit >= 10)["rounds"] >= MIN_ROUNDS:
                ways.append((f"Give each case at most {limit:g} s.",
                             {"kind": "time_per_case", "seconds": limit, "label": f"Give each case {limit:g} s"}))
                break
    layout = _layout(shape, min(CHILDREN), 3, screening)
    hours = (MIN_ROUNDS * layout["round"] + layout["seed"] + layout["final"]) / 0.95 / 3600.0
    quarters = math.ceil(hours * 4) / 4
    amount = f"{round(quarters * 60)} minutes" if quarters < 1.5 else f"{quarters:g} hours"
    ways.append((f"Allow at least {amount} in total.", {"kind": "hours", "hours": quarters, "label": f"Allow {amount}"}))
    return ways
