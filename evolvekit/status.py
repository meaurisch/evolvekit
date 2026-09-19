"""One document that says how a run is doing. Every view renders it.

`build_status(run_dir)` is a pure function of the files in a run directory:
`runs.jsonl`, `usage.jsonl`, `events.jsonl`, `heartbeat.json`, `.lock` and
`archive.json`. It starts nothing, writes nothing and needs no config file --
`run_started` events make a run directory describe itself. `status` prints it
as text, `status --json` dumps it, and the dashboard serves the same document
and draws it, so a human, a script and an agent can never be told three
different things about one run.

The document is organised around the questions somebody supervising an
expensive run actually asks:

    health      is it alive, stalled, finished or dead -- and if it stopped, why?
                which generation of how many, how many evaluations are done, in
                flight and failed, how long has it run and how long will it take?
    progress    the baseline, the best so far, the improvement in percent, and
                how much of that is noise
    best        what the best candidate *is*: its code, its diff against the
                seed, its parameters against the defaults
    parameters  which declared parameters the search has actually moved, where
                in their ranges it has been, and how strongly each one goes
                with the score
    instances   where the best candidate wins against the baseline and where
                it loses
    failures    what went wrong: the command line, the instance and seed, the
                tail of stderr, and where the full logs are

Everything in it is plain JSON: no `NaN`, no `Infinity`, nothing a browser's
`JSON.parse` would refuse. A section that cannot be computed says why instead
of raising, and a bug in one section is reported under `errors` rather than
allowed to blank the rest.
"""

from __future__ import annotations

import difflib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median, stdev
from typing import Any, Callable, Mapping, Sequence

from evolvekit.economics import DEFAULT_WINDOW, series
from evolvekit.events import TERMINAL_EVENTS, _jsonable, read_events, read_heartbeat
from evolvekit.leaderboard import competes, fitness_of, rank
from evolvekit.ledger import read_jsonl
from evolvekit.lock import pid_alive
from evolvekit.search.params import current_values, declared_ranges

__all__ = ["SCHEMA", "build_status", "candidate_detail", "render_text"]

SCHEMA = 1
"""Bumped when a key changes meaning or disappears. Adding keys does not."""

STALL_AFTER_BEATS = 4
STALL_AFTER_MIN_S = 20.0
"""A heartbeat this many intervals old (and at least this many seconds) while
its process is alive means the process is not getting to run."""

OVERDUE_GRACE_S = 60.0
"""An evaluation still in flight this long after its own timeout is stuck."""

LOG_TAIL = 40
SEED_OPERATOR = "human-seed"


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def build_status(
    run_dir: str | Path, *, now: datetime | None = None, log_tail: int = LOG_TAIL
) -> dict[str, Any]:
    """The status document for `run_dir`. Never raises, never creates anything."""
    directory = Path(run_dir)
    moment = now or datetime.now(timezone.utc)
    document: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": moment.isoformat(timespec="milliseconds"),
        "run_dir": str(directory.resolve()) if directory.is_dir() else str(directory),
        "errors": [],
    }
    if not directory.is_dir():
        document["health"] = {
            "state": "missing",
            "detail": f"there is no run directory at {directory}",
        }
        return document

    rows = list(read_jsonl(directory / "runs.jsonl"))
    usage = list(read_jsonl(directory / "usage.jsonl"))
    events = read_events(directory)
    run = _Run(directory, rows, usage, events, moment)

    sections: tuple[tuple[str, Callable[[], Any]], ...] = (
        ("objective", run.objective),
        ("health", run.health),
        ("progress", run.progress),
        ("best", run.best),
        ("parameters", run.parameters),
        ("instances", run.instances),
        ("failures", run.failures),
        ("candidates", run.candidates),
        ("archive", run.archive),
        ("spend", run.spend),
        ("log_tail", lambda: run.log_tail(log_tail)),
    )
    for name, build in sections:
        try:
            document[name] = build()
        except Exception as exc:  # noqa: BLE001 - one broken view must not blank the rest
            document[name] = None
            document["errors"].append(f"{name}: {type(exc).__name__}: {exc}")
    return _jsonable(document)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _parse_ts(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _spread(values: Sequence[float]) -> dict[str, Any]:
    """n, mean, sd and standard error. `sd` needs two samples; one sample is a
    number with no error bar, and the document says so rather than printing 0."""
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "sem": None}
    mean = fmean(values)
    if n < 2:
        return {"n": n, "mean": mean, "sd": None, "sem": None}
    sd = stdev(values)
    return {"n": n, "mean": mean, "sd": sd, "sem": sd / math.sqrt(n)}


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Rank correlation. `None` when either side does not vary."""
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = fmean(rx), fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def _improvement_pct(baseline: float | None, value: float | None, direction: str) -> float | None:
    """Positive means better, whichever way the objective points."""
    if baseline is None or value is None or baseline == 0:
        return None
    gain = (baseline - value) if direction == "minimize" else (value - baseline)
    return 100.0 * gain / abs(baseline)


# --------------------------------------------------------------------------
# the projection
# --------------------------------------------------------------------------


class _Run:
    """The files of one run directory, read once, and the views over them."""

    def __init__(
        self,
        directory: Path,
        rows: list[dict[str, Any]],
        usage: list[dict[str, Any]],
        events: list[dict[str, Any]],
        now: datetime,
    ) -> None:
        self.directory = directory
        self.rows = rows
        self.usage = usage
        self.events = events
        self.now = now
        self.by_id = {str(r.get("id")): r for r in rows}
        self.started = [e for e in events if e.get("type") == "run_started"]
        self.described = self.started[-1] if self.started else {}
        self.seed = next((r for r in rows if r.get("operator") == SEED_OPERATOR), None)
        ranked = rank(rows, 1)
        self.best_row = ranked[0] if ranked else None
        self._samples: dict[str, dict[int, float]] | None = None

    # -- objective ---------------------------------------------------------

    def objective(self) -> dict[str, Any]:
        return {
            "name": self.described.get("objective"),
            "direction": self.described.get("direction"),
            "known": bool(self.described),
        }

    @property
    def _objective_name(self) -> str | None:
        return self.described.get("objective")

    @property
    def _direction(self) -> str:
        return self.described.get("direction") or "maximize"

    @property
    def _final_stage(self) -> str | None:
        for stage in self.described.get("stages") or []:
            if stage.get("final"):
                return stage.get("id")
        return None

    def _objective_of(self, row: Mapping[str, Any] | None) -> float | None:
        if row is None or not self._objective_name:
            return None
        return _number((row.get("kpis") or {}).get(self._objective_name))

    # -- health ------------------------------------------------------------

    def _session_events(self) -> dict[str, list[dict[str, Any]]]:
        sessions: dict[str, list[dict[str, Any]]] = {}
        for event in self.events:
            sessions.setdefault(str(event.get("session")), []).append(event)
        return sessions

    def _elapsed(self, sessions: Mapping[str, list[dict[str, Any]]], live: str | None) -> float:
        """Seconds the run has actually been running, all sessions added up. A
        session that died is counted up to the last thing it managed to write."""
        total = 0.0
        for session, items in sessions.items():
            begin = _parse_ts(items[0].get("ts"))
            end = self.now if session == live else _parse_ts(items[-1].get("ts"))
            if begin and end:
                total += max(0.0, (end - begin).total_seconds())
        return total

    def health(self) -> dict[str, Any]:
        sessions = self._session_events()
        last_session = str(self.events[-1].get("session")) if self.events else None
        last_items = sessions.get(last_session or "", [])
        closing = next(
            (e for e in reversed(last_items) if e.get("type") in TERMINAL_EVENTS), None
        )
        heartbeat = read_heartbeat(self.directory) or {}
        # A run holds `.lock` for as long as it lives and removes it on the way
        # out, so "no lock" is "not running" whatever else is true -- including
        # when the old pid has since been handed to an unrelated process.
        lock = _read_json(self.directory / ".lock") or {}
        lock_pid = int(lock.get("pid") or 0)
        alive = bool(lock_pid) and pid_alive(lock_pid)
        pid = lock_pid or int(heartbeat.get("pid") or self.described.get("pid") or 0)

        beat_at = _parse_ts(heartbeat.get("ts"))
        beat_age = (self.now - beat_at).total_seconds() if beat_at else None
        interval = _number(heartbeat.get("interval_s")) or 5.0

        in_flight = self._in_flight(last_items) if closing is None and alive else []
        overdue = [
            e for e in in_flight
            if e["timeout_s"] is not None and e["running_s"] > e["timeout_s"] + OVERDUE_GRACE_S
        ]

        if not self.events and not self.rows:
            state, detail = "empty", "nothing has been recorded in this directory yet"
        elif not self.events:
            state = "running" if alive else "unknown"
            detail = (
                "this directory has no event log (written by an older evolvekit); "
                + ("its lock is held by a live process" if state == "running"
                   else "it cannot be told from here how the run ended")
            )
        elif closing is not None:
            kind = closing["type"]
            state = {"run_finished": "finished", "run_interrupted": "interrupted"}.get(
                kind, "crashed"
            )
            detail = {
                "finished": f"stopped: {closing.get('stop_reason')}",
                "interrupted": "interrupted by the user (Ctrl+C); run the same command to resume",
                "crashed": f"crashed: {closing.get('error')}",
            }[state]
        elif not alive:
            state = "crashed"
            detail = (
                f"process {pid or '?'} is gone and left no closing event: it was killed, "
                "or the machine went down. Run the same command to resume"
            )
        elif beat_age is not None and beat_age > max(
            STALL_AFTER_MIN_S, STALL_AFTER_BEATS * interval
        ):
            state = "stalled"
            detail = (
                f"process {pid} is alive but its last heartbeat is {beat_age:.0f} s old: it is "
                "suspended, or the machine slept. A wall-clock-limited evaluation that "
                "was in flight cannot be trusted"
            )
        elif overdue:
            worst = max(overdue, key=lambda e: e["running_s"])
            state = "stalled"
            detail = (
                f"{worst['candidate_id']} has been in stage {worst['stage']} for "
                f"{worst['running_s']:.0f} s against a timeout of {worst['timeout_s']:.0f} s"
            )
        else:
            state = "running"
            detail = f"process {pid} is alive, phase: {heartbeat.get('phase') or 'unknown'}"

        live = last_session if state in ("running", "stalled") else None
        finished = [e for e in self.events if e.get("type") == "eval_finished"]
        by_stage: dict[str, dict[str, int]] = {}
        for event in finished:
            slot = by_stage.setdefault(str(event.get("stage")), {"done": 0, "failed": 0})
            slot["failed" if not event.get("ok") else "done"] += 1

        first = self.described.get("first_generation")
        planned = self.described.get("generations_planned")
        last_planned = (first + planned - 1) if isinstance(first, int) and isinstance(planned, int) else None
        done_generations = [
            int(e["generation"]) for e in self.events
            if e.get("type") == "generation_finished" and isinstance(e.get("generation"), int)
        ]
        current = heartbeat.get("generation") if live else None
        if current is None:
            current = max(
                [int(r.get("generation", 0)) for r in self.rows if isinstance(r.get("generation"), int)]
                + done_generations,
                default=None,
            )

        return {
            "state": state,
            "detail": detail,
            "pid": pid or None,
            "pid_alive": alive,
            "phase": heartbeat.get("phase"),
            "heartbeat_age_s": beat_age,
            "heartbeat_interval_s": interval if heartbeat else None,
            "stop_reason": closing.get("stop_reason") if closing else None,
            "sessions": len(sessions),
            "resumed": len(sessions) > 1 or bool(self.described.get("resumed")),
            "started_at": self.events[0].get("ts") if self.events else None,
            "last_event_at": self.events[-1].get("ts") if self.events else None,
            "elapsed_s": self._elapsed(sessions, live),
            "generation": {
                "current": current,
                "last_finished": max(done_generations, default=None),
                "last_planned": last_planned,
            },
            "evaluations": {
                "done": sum(1 for e in finished if e.get("ok")),
                "failed": sum(1 for e in finished if not e.get("ok")),
                "in_flight": len(in_flight),
                "abandoned": self._abandoned(sessions, live),
                "by_stage": by_stage,
                "failure_reasons": self._failure_reasons(finished),
            },
            "in_flight": in_flight,
            "eta": self._eta(last_planned, done_generations, live is not None),
            "limits": self._limits(last_planned, done_generations),
        }

    @staticmethod
    def _failure_reasons(finished: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Failures counted by stage and message, most frequent first: ten
        crashes with one exit code are one problem, not ten."""
        counts: dict[tuple[str, str], int] = {}
        for event in finished:
            if not event.get("ok"):
                key = (str(event.get("stage")), str(event.get("failure") or "failed"))
                counts[key] = counts.get(key, 0) + 1
        return [
            {"stage": stage, "failure": failure, "count": count}
            for (stage, failure), count in sorted(counts.items(), key=lambda kv: -kv[1])
        ]

    def _open_evaluations(self, items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        open_: dict[tuple, dict[str, Any]] = {}
        for event in items:
            key = (event.get("candidate_id"), event.get("stage"), event.get("seed"), event.get("private"))
            if event.get("type") == "eval_started":
                open_[key] = dict(event)
            elif event.get("type") == "eval_finished":
                open_.pop(key, None)
        return list(open_.values())

    def _in_flight(self, items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for event in self._open_evaluations(items):
            since = _parse_ts(event.get("ts"))
            result.append(
                {
                    "candidate_id": event.get("candidate_id"),
                    "stage": event.get("stage"),
                    "seed": event.get("seed"),
                    "private": bool(event.get("private")),
                    "since": event.get("ts"),
                    "running_s": (self.now - since).total_seconds() if since else 0.0,
                    "timeout_s": _number(event.get("timeout_s")),
                }
            )
        return result

    def _abandoned(self, sessions: Mapping[str, list[dict[str, Any]]], live: str | None) -> int:
        """Evaluations that were started and never finished because their
        session ended first. That work was paid for and lost."""
        return sum(
            len(self._open_evaluations(items))
            for session, items in sessions.items()
            if session != live
        )

    def _generation_durations(self) -> list[float]:
        return [
            float(e["duration_s"]) for e in self.events
            if e.get("type") == "generation_finished"
            and int(e.get("generation") or 0) > 0
            and _number(e.get("duration_s")) is not None
        ]

    def _eta(self, last_planned: int | None, done: Sequence[int], live: bool) -> dict[str, Any]:
        if not live:
            return {"seconds": None, "at": None, "basis": "the run is not running"}
        if last_planned is None:
            return {"seconds": None, "at": None, "basis": "the plan is unknown"}
        remaining = last_planned - max(done, default=0)
        durations = self._generation_durations()
        if remaining <= 0:
            return {"seconds": 0.0, "at": None, "basis": "the last planned generation has finished"}
        if not durations:
            return {
                "seconds": None,
                "at": None,
                "basis": f"{remaining} generation(s) to go; none has finished yet, so there is nothing to extrapolate from",
            }
        typical = median(durations)
        seconds = typical * remaining
        return {
            "seconds": seconds,
            "at": datetime.fromtimestamp(self.now.timestamp() + seconds, timezone.utc).isoformat(timespec="seconds"),
            "basis": (
                f"{remaining} generation(s) to go x the median of {len(durations)} finished "
                f"({typical:.0f} s). An upper bound on the plan: a stop rule can end it sooner"
            ),
        }

    def _limits(self, last_planned: int | None, done: Sequence[int]) -> list[dict[str, Any]]:
        """How far along each stopping criterion is."""
        limits: list[dict[str, Any]] = []
        if last_planned is not None:
            limits.append({"name": "generations", "used": max(done, default=0), "cap": last_planned, "unit": ""})
        budget = self.described.get("budget") or {}
        usd = sum(float(u.get("usd", 0.0) or 0.0) for u in self.usage)
        tokens = sum(int(u.get("input_tokens", 0) or 0) + int(u.get("output_tokens", 0) or 0) for u in self.usage)
        if _number(budget.get("max_usd")) is not None:
            limits.append({"name": "budget.max_usd", "used": usd, "cap": budget["max_usd"], "unit": "USD"})
        if _number(budget.get("max_tokens")) is not None:
            limits.append({"name": "budget.max_tokens", "used": tokens, "cap": budget["max_tokens"], "unit": "tokens"})
        final = self._final_stage
        if final and _number(budget.get("max_full_evals_per_day")) is not None:
            today = self.now.date().isoformat()
            used = sum(
                1 for e in self.events
                if e.get("type") == "eval_started" and e.get("stage") == final
                and not e.get("private") and int(e.get("seed") or 0) == 0
                and str(e.get("ts", ""))[:10] == today
            )
            limits.append({"name": "budget.max_full_evals_per_day", "used": used, "cap": budget["max_full_evals_per_day"], "unit": "today"})
        stop = self.described.get("stop") or {}
        if _number(stop.get("patience")) is not None:
            limits.append({"name": "stop.patience", "used": self._flat_generations(float(stop.get("epsilon") or 0.0)), "cap": stop["patience"], "unit": "flat generations"})
        return limits

    def _flat_generations(self, epsilon: float) -> int:
        flat = 0
        for point in series(self.rows, self.usage, window=DEFAULT_WINDOW, epsilon=epsilon):
            flat = 0 if point.improved else flat + 1
        return flat

    # -- progress ----------------------------------------------------------

    def _objective_samples(self) -> dict[str, dict[int, float]]:
        """`{candidate: {seed: objective}}` from the final stage's public runs:
        the individual draws behind each mean, which is where the noise shows."""
        if self._samples is not None:
            return self._samples
        samples: dict[str, dict[int, float]] = {}
        name, final = self._objective_name, self._final_stage
        for event in self.events:
            if event.get("type") != "eval_finished" or not event.get("ok") or event.get("private"):
                continue
            if final is not None and event.get("stage") != final:
                continue
            value = _number((event.get("kpis") or {}).get(name)) if name else None
            if value is None:
                continue
            samples.setdefault(str(event.get("candidate_id")), {})[int(event.get("seed") or 0)] = value
        self._samples = samples
        return samples

    def _measured(self, row: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        draws = self._objective_samples().get(str(row.get("id")), {})
        spread = _spread(list(draws.values()))
        return {
            "id": row.get("id"),
            "generation": row.get("generation"),
            "score": _number(row.get("score")),
            "fitness": fitness_of(row),
            "objective": self._objective_of(row),
            "n": spread["n"],
            "sd": spread["sd"],
            "sem": spread["sem"],
        }

    def _verdict(self, baseline: Mapping[str, Any] | None, best: Mapping[str, Any] | None) -> dict[str, Any]:
        """Is the improvement more than the dice? A paired comparison over the
        seeds both candidates were run on -- the same seeds, so most of what
        the instance and the seed contribute cancels out."""
        if not baseline or not best or baseline.get("id") == best.get("id"):
            return {"verdict": "none", "why": "the best candidate is the baseline"}
        samples = self._objective_samples()
        a, b = samples.get(str(baseline["id"]), {}), samples.get(str(best["id"]), {})
        shared = sorted(set(a) & set(b))
        if len(shared) < 2:
            return {
                "verdict": "unknown",
                "why": (
                    "each score is a single evaluator run, so there is no spread to judge it "
                    "against. Set `seeds: N` on the final stage, or confirm on fresh seeds"
                ),
                "n": len(shared),
            }
        sign = 1.0 if self._direction == "minimize" else -1.0
        gains = [sign * (a[s] - b[s]) for s in shared]
        mean, sd = fmean(gains), stdev(gains)
        t = mean / (sd / math.sqrt(len(gains))) if sd > 0 else math.inf
        clear = mean > 0 and t >= 2.0
        return {
            "verdict": "clear" if clear else "within noise",
            "why": (
                f"paired over {len(shared)} shared seed(s): mean gain {mean:.6g}, "
                f"t = {t:.2f}" + ("" if clear else " (below 2: not distinguishable from noise)")
                + ". These are the seeds the search selected on, so even a clear "
                "result is optimistic until it is confirmed on seeds it never saw"
            ),
            "n": len(shared),
            "mean_gain": mean,
            "sd_gain": sd,
            "t": t if math.isfinite(t) else None,
        }

    def progress(self) -> dict[str, Any]:
        baseline, best = self._measured(self.seed), self._measured(self.best_row)
        direction = self._direction
        per_generation = []
        running: Mapping[str, Any] | None = None
        generations = sorted({int(r.get("generation", 0)) for r in self.rows if isinstance(r.get("generation"), int)})
        for generation in generations:
            for row in self.rows:
                if row.get("generation") != generation or not competes(row) or fitness_of(row) is None:
                    continue
                if running is None or fitness_of(row) > fitness_of(running):
                    running = row
            measured = self._measured(running)
            per_generation.append(
                {
                    "generation": generation,
                    "best_id": measured["id"] if measured else None,
                    "best_fitness": measured["fitness"] if measured else None,
                    "best_objective": measured["objective"] if measured else None,
                    "sem": measured["sem"] if measured else None,
                    "improvement_pct": _improvement_pct(
                        baseline["objective"] if baseline else None,
                        measured["objective"] if measured else None,
                        direction,
                    ),
                }
            )
        return {
            "baseline": baseline,
            "best": best,
            "improvement": {
                "pct": _improvement_pct(
                    baseline["objective"] if baseline else None,
                    best["objective"] if best else None,
                    direction,
                ),
                "objective_abs": (
                    None if not baseline or not best or baseline["objective"] is None or best["objective"] is None
                    else (baseline["objective"] - best["objective"]) * (1 if direction == "minimize" else -1)
                ),
                "score_abs": (
                    None if not baseline or not best or baseline["fitness"] is None or best["fitness"] is None
                    else best["fitness"] - baseline["fitness"]
                ),
                **self._verdict(baseline, best),
            },
            "series": per_generation,
            "generations": self._generation_ribbon(per_generation),
        }

    def _generation_ribbon(self, per_generation: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """One entry per generation from 0 to the last planned one: finished,
        in progress or still to come, whether it moved the best, how long it
        took and how many of its evaluations failed."""
        finished = {
            int(e["generation"]): e for e in self.events
            if e.get("type") == "generation_finished" and isinstance(e.get("generation"), int)
        }
        started = {
            int(e["generation"]) for e in self.events
            if e.get("type") == "generation_started" and isinstance(e.get("generation"), int)
        }
        failed: dict[int, int] = {}
        for event in self.events:
            if event.get("type") == "eval_finished" and not event.get("ok"):
                row = self.by_id.get(str(event.get("candidate_id")))
                generation = (row or {}).get("generation")
                if generation is None:  # not recorded yet: its id says which generation
                    text = str(event.get("candidate_id") or "")
                    generation = int(text[1:4]) if text[1:4].isdigit() else None
                if generation is not None:
                    failed[int(generation)] = failed.get(int(generation), 0) + 1
        best_by_generation = {int(p["generation"]): p for p in per_generation}
        first = self.described.get("first_generation")
        planned = self.described.get("generations_planned")
        last_planned = first + planned - 1 if isinstance(first, int) and isinstance(planned, int) else 0
        last = max([last_planned, *finished, *started, *best_by_generation], default=0)
        ribbon, previous_best = [], None
        for generation in range(0, last + 1):
            point = best_by_generation.get(generation)
            best_id = point.get("best_id") if point else None
            event = finished.get(generation)
            state = "done" if event or (generation not in started and point) else (
                "current" if generation in started else "planned"
            )
            ribbon.append(
                {
                    "generation": generation,
                    "state": state,
                    "improved": bool(best_id) and previous_best is not None and best_id != previous_best,
                    "duration_s": _number((event or {}).get("duration_s")),
                    "children": (event or {}).get("children"),
                    "failed": failed.get(generation, 0),
                    "best_objective": point.get("best_objective") if point else None,
                }
            )
            previous_best = best_id or previous_best
        return ribbon

    # -- best --------------------------------------------------------------

    def _lineage(self, candidate_id: str, limit: int = 12) -> list[str]:
        chain, seen = [], {candidate_id}
        current = self.by_id.get(candidate_id)
        while current and current.get("parent_id") and len(chain) < limit:
            parent = str(current["parent_id"])
            chain.append(parent)
            if parent in seen:
                break
            seen.add(parent)
            current = self.by_id.get(parent)
        return chain

    def best(self) -> dict[str, Any] | None:
        row = self.best_row
        if row is None:
            return None
        block = str(row.get("block") or "")
        seed_block = str((self.seed or {}).get("block") or "")
        diff = "".join(
            difflib.unified_diff(
                seed_block.splitlines(keepends=True),
                block.splitlines(keepends=True),
                fromfile=f"seed ({(self.seed or {}).get('id', '?')})",
                tofile=f"best ({row.get('id')})",
                n=2,
            )
        )
        return {
            "id": row.get("id"),
            "generation": row.get("generation"),
            "operator": row.get("operator"),
            "model": row.get("model"),
            "lineage": self._lineage(str(row.get("id"))),
            "is_seed": bool(self.seed) and row.get("id") == self.seed.get("id"),
            "kpis": row.get("kpis") or {},
            "kpi_cv": row.get("kpi_cv") or {},
            "public_score": _number(row.get("public_score")),
            "private_score": _number(row.get("private_score")),
            "generalization_gap": _number(row.get("generalization_gap")),
            "block": block,
            "diff_vs_seed": diff,
            "parameters": self._parameter_diff(seed_block, block),
        }

    def _parameter_diff(self, seed_block: str, block: str) -> list[dict[str, Any]]:
        ranges = declared_ranges(seed_block)
        if not ranges:
            return []
        defaults = current_values(seed_block, set(ranges))
        values = current_values(block, set(ranges))
        return [
            {
                "name": name,
                "default": defaults.get(name),
                "value": values.get(name),
                "changed": name in values and values.get(name) != defaults.get(name),
                "low": ranges[name][0],
                "high": ranges[name][1],
            }
            for name in sorted(ranges)
        ]

    # -- parameters --------------------------------------------------------

    def parameters(self) -> dict[str, Any]:
        seed_block = str((self.seed or {}).get("block") or "")
        ranges = declared_ranges(seed_block)
        if not ranges:
            return {
                "available": False,
                "why": "the seed block declares no `# PARAMS: {...}` line, so there are no named parameters to follow",
                "items": [],
            }
        defaults = current_values(seed_block, set(ranges))
        best_values = current_values(str((self.best_row or {}).get("block") or ""), set(ranges))
        observed = []
        for row in self.rows:
            if row.get("rejected"):
                continue
            values = current_values(str(row.get("block") or ""), set(ranges))
            if values:
                observed.append((row, values))
        items = []
        for name in sorted(ranges):
            low, high = ranges[name]
            points = [
                {
                    "id": r.get("id"),
                    "value": v[name],
                    "fitness": fitness_of(r),
                    "objective": self._objective_of(r),
                    "competes": competes(r),
                }
                for r, v in observed
                if name in v
            ]
            ranked = [p for p in points if p["competes"] and p["fitness"] is not None]
            rho = _spearman([p["value"] for p in ranked], [p["fitness"] for p in ranked])
            bins = [0] * 10
            for point in points:
                position = (float(point["value"]) - low) / (high - low) if high > low else 0.0
                bins[min(9, max(0, int(position * 10)))] += 1
            items.append(
                {
                    "name": name,
                    "low": low,
                    "high": high,
                    "default": defaults.get(name),
                    "best": best_values.get(name),
                    "distinct_values": len({p["value"] for p in points}),
                    "coverage": bins,
                    "importance": {
                        "method": "absolute Spearman rank correlation between the value and the ranking score, fully evaluated candidates only",
                        "value": abs(rho) if rho is not None else None,
                        "sign": (1 if rho > 0 else -1) if rho else 0,
                        "n": len(ranked),
                    },
                    "points": points,
                }
            )
        items.sort(key=lambda item: -(item["importance"]["value"] or 0.0))
        return {
            "available": True,
            "why": (
                "a correlation over a few dozen evaluations says where to look, not what is true: "
                "it misses interactions and non-monotone effects"
            ),
            "items": items,
        }

    # -- instances ---------------------------------------------------------

    def _vector(self, candidate_id: str) -> tuple[str, list[float]] | None:
        """The per-instance list the evaluator reported for the final stage."""
        final, objective = self._final_stage, self._objective_name or ""
        per_seed: dict[str, list[list[float]]] = {}
        for event in self.events:
            if (
                event.get("type") != "eval_finished" or not event.get("ok") or event.get("private")
                or str(event.get("candidate_id")) != candidate_id
                or (final is not None and event.get("stage") != final)
            ):
                continue
            for name, values in (event.get("vector_kpis") or {}).items():
                if isinstance(values, list) and values:
                    per_seed.setdefault(name, []).append([float(v) for v in values if _number(v) is not None])
        if not per_seed:
            return None
        name = next((n for n in per_seed if objective and objective in n), sorted(per_seed)[0])
        runs = [r for r in per_seed[name] if len(r) == len(per_seed[name][0])]
        return name, [fmean(column) for column in zip(*runs)]

    def instances(self) -> dict[str, Any]:
        unavailable = {
            "available": False,
            "rows": [],
            "why": (
                "the evaluator reports no per-instance list KPI at the final stage. Emit one "
                "(e.g. `\"cost_per_instance\": [..]`) and the breakdown appears here"
            ),
        }
        if not self.seed or not self.best_row:
            return unavailable
        base, best = self._vector(str(self.seed.get("id"))), self._vector(str(self.best_row.get("id")))
        if not base or not best or base[0] != best[0] or len(base[1]) != len(best[1]):
            return unavailable
        lower_is_better = self._direction == "minimize"
        rows, wins, losses = [], 0, 0
        for index, (a, b) in enumerate(zip(base[1], best[1])):
            delta = _improvement_pct(a, b, "minimize" if lower_is_better else "maximize")
            wins += 1 if (delta or 0) > 0 else 0
            losses += 1 if (delta or 0) < 0 else 0
            rows.append({"instance": index, "baseline": a, "best": b, "improvement_pct": delta})
        return {
            "available": True,
            "kpi": base[0],
            "assumes": f"`{base[0]}` points the same way as the objective ({self._direction})",
            "best_id": self.best_row.get("id"),
            "baseline_id": self.seed.get("id"),
            "wins": wins,
            "losses": losses,
            "ties": len(rows) - wins - losses,
            "rows": rows,
        }

    # -- failures ----------------------------------------------------------

    def failures(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for event in self.events:
            if event.get("type") == "eval_finished" and not event.get("ok"):
                row = self.by_id.get(str(event.get("candidate_id"))) or {}
                found.append(
                    {
                        "kind": "evaluation",
                        "ts": event.get("ts"),
                        "candidate_id": event.get("candidate_id"),
                        "generation": row.get("generation"),
                        "stage": event.get("stage"),
                        "seed": event.get("seed"),
                        "holdout": bool(event.get("private")),
                        "failure": event.get("failure"),
                        "duration_s": _number(event.get("duration_s")),
                        "argv": event.get("argv") or [],
                        "stderr_tail": event.get("stderr_tail") or "",
                        "stdout_tail": event.get("stdout_tail") or "",
                        "stderr_log": event.get("stderr_log"),
                        "stdout_log": event.get("stdout_log"),
                    }
                )
            elif event.get("type") == "candidate_bred" and not event.get("ok") and not event.get("novelty"):
                found.append(
                    {
                        "kind": "operator",
                        "ts": event.get("ts"),
                        "candidate_id": event.get("candidate_id"),
                        "generation": event.get("generation"),
                        "stage": None,
                        "failure": event.get("reason"),
                        "operator": event.get("operator"),
                    }
                )
        for row in self.rows:
            if row.get("rejected") and not row.get("novelty") and row.get("stages_reached") == [] and row.get("operator") != SEED_OPERATOR:
                if any(f["candidate_id"] == row.get("id") for f in found):
                    continue
                found.append(
                    {
                        "kind": "static",
                        "ts": row.get("created_at"),
                        "candidate_id": row.get("id"),
                        "generation": row.get("generation"),
                        "stage": "static",
                        "failure": row.get("reject_reason"),
                        "stderr_tail": row.get("last_failure") or "",
                    }
                )
        found.sort(key=lambda f: str(f.get("ts") or ""), reverse=True)
        return found

    # -- candidates, archive, spend, log -----------------------------------

    def candidates(self) -> list[dict[str, Any]]:
        durations: dict[str, float] = {}
        for event in self.events:
            if event.get("type") == "eval_finished" and _number(event.get("duration_s")) is not None:
                key = str(event.get("candidate_id"))
                durations[key] = durations.get(key, 0.0) + float(event["duration_s"])
        samples = self._objective_samples()
        result = []
        for row in self.rows:
            cid = str(row.get("id"))
            stages = row.get("stages_reached") or []
            spread = _spread(list(samples.get(cid, {}).values()))
            result.append(
                {
                    "id": cid,
                    "generation": row.get("generation"),
                    "operator": row.get("operator"),
                    "parent_id": row.get("parent_id"),
                    "score": _number(row.get("score")),
                    "fitness": fitness_of(row),
                    "objective": self._objective_of(row),
                    "n": spread["n"],
                    "sd": spread["sd"],
                    "competes": competes(row),
                    "rejected": bool(row.get("rejected")),
                    "novelty": row.get("novelty"),
                    "reason": row.get("reject_reason") or _first_line(row.get("last_failure")),
                    "deepest_stage": stages[-1] if stages else None,
                    "evaluation_s": durations.get(cid),
                    "usd": _number(row.get("usd")),
                    "cell": row.get("cell"),
                    "is_seed": row.get("operator") == SEED_OPERATOR,
                    "is_best": bool(self.best_row) and row.get("id") == self.best_row.get("id"),
                }
            )
        return result

    def archive(self) -> dict[str, Any]:
        snapshot = _read_json(self.directory / "archive.json") or {}
        return {
            "occupancy": snapshot.get("occupancy"),
            "descriptors": snapshot.get("descriptors") or [],
            "cells": [
                {k: cell.get(k) for k in ("coord", "label", "elite_id", "fitness", "occupants")}
                for cell in snapshot.get("cells") or []
            ],
            "updated": snapshot.get("updated"),
        }

    def spend(self) -> dict[str, Any]:
        usd = sum(float(u.get("usd", 0.0) or 0.0) for u in self.usage)
        tokens_in = sum(int(u.get("input_tokens", 0) or 0) for u in self.usage)
        tokens_out = sum(int(u.get("output_tokens", 0) or 0) for u in self.usage)
        evaluator_s = sum(
            float(e["duration_s"]) for e in self.events
            if e.get("type") == "eval_finished" and _number(e.get("duration_s")) is not None
        )
        return {
            "usd": usd,
            "calls": len(self.usage),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "evaluator_s": evaluator_s,
        }

    def log_tail(self, limit: int) -> list[dict[str, Any]]:
        lines = [e for e in self.events if e.get("type") == "log"]
        return [{"ts": e.get("ts"), "message": e.get("message")} for e in lines[-limit:]]


def _unified(before: str, after: str, label_before: str, label_after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=label_before,
            tofile=label_after,
            n=2,
        )
    )


def candidate_detail(run_dir: str | Path, candidate_id: str) -> dict[str, Any] | None:
    """Everything about one candidate: the part of the picture that is too big
    to poll. Its code, its diff against its parent and against the seed, and
    every evaluator run it cost, with the command line and the logs.

    `None` when the run directory holds no such candidate.
    """
    directory = Path(run_dir)
    rows = {str(r.get("id")): r for r in read_jsonl(directory / "runs.jsonl")}
    row = rows.get(candidate_id)
    if row is None:
        return None
    seed = next((r for r in rows.values() if r.get("operator") == SEED_OPERATOR), None)
    parent = rows.get(str(row.get("parent_id")))
    block = str(row.get("block") or "")
    evaluations = [
        {
            key: event.get(key)
            for key in (
                "ts", "stage", "seed", "private", "ok", "duration_s", "kpis", "failure",
                "stderr_tail", "stdout_tail", "argv", "stdout_log", "stderr_log",
            )
        }
        for event in read_events(directory)
        if event.get("type") == "eval_finished"
        and str(event.get("candidate_id")) == candidate_id
    ]
    seed_block = str((seed or {}).get("block") or "")
    ranges = declared_ranges(seed_block)
    defaults = current_values(seed_block, set(ranges))
    values = current_values(block, set(ranges))
    detail = {
        "id": candidate_id,
        "generation": row.get("generation"),
        "operator": row.get("operator"),
        "model": row.get("model"),
        "parent_id": row.get("parent_id"),
        "inspiration_ids": row.get("inspiration_ids") or [],
        "created_at": row.get("created_at"),
        "score": _number(row.get("score")),
        "fitness": fitness_of(row),
        "public_score": _number(row.get("public_score")),
        "private_score": _number(row.get("private_score")),
        "competes": competes(row),
        "rejected": bool(row.get("rejected")),
        "novelty": row.get("novelty"),
        "reject_reason": row.get("reject_reason"),
        "last_failure": row.get("last_failure"),
        "stages_reached": row.get("stages_reached") or [],
        "stage_scores": row.get("stage_scores") or {},
        "kpis": row.get("kpis") or {},
        "kpi_cv": row.get("kpi_cv") or {},
        "feedback": row.get("feedback") or {},
        "block": block,
        "diff_vs_parent": (
            _unified(str(parent.get("block") or ""), block, f"parent ({parent.get('id')})", candidate_id)
            if parent
            else ""
        ),
        "diff_vs_seed": (
            _unified(seed_block, block, f"seed ({seed.get('id')})", candidate_id)
            if seed and seed.get("id") != candidate_id
            else ""
        ),
        "parameters": [
            {
                "name": name,
                "default": defaults.get(name),
                "value": values.get(name),
                "changed": name in values and values.get(name) != defaults.get(name),
            }
            for name in sorted(ranges)
        ],
        "evaluations": evaluations,
    }
    return _jsonable(detail)


def _first_line(text: Any) -> str | None:
    lines = str(text or "").strip().splitlines()
    return lines[0] if lines else None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


# --------------------------------------------------------------------------
# the terminal view
# --------------------------------------------------------------------------


def _duration(seconds: Any) -> str:
    value = _number(seconds)
    if value is None:
        return "n/a"
    if value < 90:
        return f"{value:.0f} s"
    if value < 5400:
        return f"{value / 60:.0f} min"
    return f"{value / 3600:.1f} h"


def _fmt(value: Any, digits: int = 6) -> str:
    number = _number(value)
    return "n/a" if number is None else f"{number:.{digits}g}"


def render_text(document: Mapping[str, Any]) -> str:
    """The same document, for a terminal. Nothing here is computed: a line that
    is not in the JSON is not in the text either."""
    health = document.get("health") or {}
    lines = [
        f"run dir      : {document.get('run_dir')}",
        f"state        : {str(health.get('state', '?')).upper()} -- {health.get('detail', '')}",
    ]
    if health.get("state") in ("missing", "empty"):
        return "\n".join(lines)

    generation = health.get("generation") or {}
    evaluations = health.get("evaluations") or {}
    eta = health.get("eta") or {}
    lines.append(
        f"generation   : {generation.get('current', 'n/a')}"
        + (f" of {generation['last_planned']}" if generation.get("last_planned") is not None else "")
        + f"   sessions: {health.get('sessions', 0)}"
    )
    lines.append(
        f"evaluations  : {evaluations.get('done', 0)} done, {evaluations.get('in_flight', 0)} in flight, "
        f"{evaluations.get('failed', 0)} failed"
        + (f", {evaluations['abandoned']} abandoned by a session that died" if evaluations.get("abandoned") else "")
    )
    lines.append(
        f"elapsed      : {_duration(health.get('elapsed_s'))}   eta: {_duration(eta.get('seconds'))}"
        + (f"  ({eta.get('basis')})" if eta.get("seconds") is None and eta.get("basis") else "")
    )
    for flight in health.get("in_flight") or []:
        lines.append(
            f"  in flight  : {flight.get('candidate_id')} stage {flight.get('stage')} seed {flight.get('seed')}"
            + (" (hold-out)" if flight.get("private") else "")
            + f", {_duration(flight.get('running_s'))} of {_duration(flight.get('timeout_s'))}"
        )
    for limit in health.get("limits") or []:
        lines.append(f"  limit      : {limit.get('name')} {_fmt(limit.get('used'))} of {_fmt(limit.get('cap'))} {limit.get('unit', '')}".rstrip())

    progress = document.get("progress") or {}
    objective = document.get("objective") or {}
    baseline, best = progress.get("baseline") or {}, progress.get("best") or {}
    improvement = progress.get("improvement") or {}
    label = f"{objective.get('name') or 'objective'} ({objective.get('direction') or '?'})"
    lines.append(f"objective    : {label}")
    lines.append(f"baseline     : {baseline.get('id', 'n/a')}  {_fmt(baseline.get('objective'))}  (n={baseline.get('n', 0)})")
    lines.append(f"best         : {best.get('id', 'n/a')}  {_fmt(best.get('objective'))}  (n={best.get('n', 0)}, generation {best.get('generation', 'n/a')})")
    pct = _number(improvement.get("pct"))
    lines.append(
        f"improvement  : {'n/a' if pct is None else f'{pct:+.2f} %'}  [{improvement.get('verdict', 'unknown')}] {improvement.get('why', '')}"
    )
    failures = document.get("failures") or []
    lines.append(f"failures     : {len(failures)}" + ("" if not failures else "  (most recent first)"))
    for failure in failures[:5]:
        lines.append(
            f"  {failure.get('kind')}: {failure.get('candidate_id')} stage {failure.get('stage')}"
            + (f" seed {failure.get('seed')}" if failure.get("seed") is not None else "")
            + f" -- {failure.get('failure')}"
        )
    spend = document.get("spend") or {}
    lines.append(
        f"spend        : ${_fmt(spend.get('usd'), 4)} over {spend.get('calls', 0)} call(s); "
        f"evaluator time {_duration(spend.get('evaluator_s'))}"
    )
    for error in document.get("errors") or []:
        lines.append(f"view error   : {error}")
    return "\n".join(lines)
