"""What the search costs per unit of progress, generation by generation.

The redesign proposal asks for "USD per unit of best-score improvement per
generation window" as a first-class view, and the first two real runs say why.
Run 1 found -5.526 against a -5.871 seed for $0.83 across 18 paid calls; run 2
spent $0.83 as well and found nothing, then stopped on patience after twelve
novel samples. Neither of those facts was visible while the run was happening.
The only number that answers "is this still worth running?" is the ratio of
spend to gain, and it has to be visible per generation, not at the end.

This module is a pure projection over `runs.jsonl` and `usage.jsonl`. It
computes nothing the ledger does not already record, holds no state, and calls
nothing. `budget.StopPolicy` reads the last point of the series to decide
whether the economics have gone bad enough to stop; `leaderboard` draws it;
`status` prints its tail.

The four quantities per generation:

* **cumulative USD** -- every LLM call charged to that generation or earlier,
  embeddings and scratchpad refreshes included.
* **best** -- the best *ranking* score (hold-out-aware) among all non-rejected
  candidates from that generation or earlier. Monotone by construction.
* **USD since improvement** -- spend since the last generation whose best moved
  by more than `epsilon`. This is the number that says "we have spent 40 cents
  on nothing".
* **gain per USD over a sliding window `W`** -- `(best[g] - best[g-W]) /
  (usd[g] - usd[g-W])`. A window rather than a single generation because one
  flat generation is normal and three in a row is a plateau.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "GenerationPoint",
    "series",
    "format_series",
    "DEFAULT_WINDOW",
]

DEFAULT_WINDOW = 3
"""Generations in the sliding window when nothing configures one. Matches
TurboEvolve's three-iteration stagnation window, which is the same shape of
judgement made for the same reason."""


@dataclass(frozen=True)
class GenerationPoint:
    """One generation's cost and what it bought."""

    generation: int
    calls: int
    cumulative_calls: int
    usd: float
    cumulative_usd: float
    best: float | None
    delta_best: float
    improved: bool
    usd_since_improvement: float
    window: int
    window_gain: float
    window_usd: float
    gain_per_usd: float | None
    """`None` when nothing was spent in the window -- an unpaid generation is
    not evidence of a bad exchange rate."""
    usd_per_gain: float | None
    """The headline the proposal names. `None` when the window gained nothing,
    because "infinite dollars per unit" is a worse answer than "no gain yet"."""

    @property
    def gain_line(self) -> str:
        """One phrase for the whole exchange rate, for a log line or a footer."""
        if self.window_gain <= 0:
            return "no gain"
        if self.window_usd <= 0 or self.usd_per_gain is None:
            return "free"
        return f"${self.usd_per_gain:.4f}/unit"


def _fitness(row: Mapping[str, Any]) -> float | None:
    """What the search ranks by, the same rule the leaderboard uses."""
    if row.get("rejected"):
        return None
    value = row.get("ranking_score")
    if value is None:
        value = row.get("score")
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def series(
    runs: Iterable[Mapping[str, Any]],
    usage: Iterable[Mapping[str, Any]] = (),
    *,
    window: int = DEFAULT_WINDOW,
    epsilon: float = 0.0,
) -> list[GenerationPoint]:
    """One point per generation that has a row in `runs`, oldest first.

    Generations with no candidates are skipped rather than interpolated: the
    series is a record of what happened, and a generation that never ran did
    not happen. Spend is bucketed by the `generation` field on the usage row,
    which is the generation that *provoked* the call -- a re-prompt and the
    child it belongs to are charged to the same bucket.
    """
    width = max(1, int(window))
    best_by_generation: dict[int, float] = {}
    generations: set[int] = set()
    for row in runs:
        try:
            generation = int(row.get("generation", 0))
        except (TypeError, ValueError):
            continue
        generations.add(generation)
        fitness = _fitness(row)
        if fitness is None:
            continue
        current = best_by_generation.get(generation)
        if current is None or fitness > current:
            best_by_generation[generation] = fitness

    usd_by_generation: dict[int, float] = {}
    calls_by_generation: dict[int, int] = {}
    for row in usage:
        try:
            generation = int(row.get("generation", 0))
        except (TypeError, ValueError):
            continue
        generations.add(generation)
        usd_by_generation[generation] = usd_by_generation.get(generation, 0.0) + float(
            row.get("usd", 0.0) or 0.0
        )
        calls_by_generation[generation] = calls_by_generation.get(generation, 0) + 1

    points: list[GenerationPoint] = []
    running_best: float | None = None
    cumulative_usd = 0.0
    cumulative_calls = 0
    usd_at_last_improvement = 0.0
    for generation in sorted(generations):
        usd = usd_by_generation.get(generation, 0.0)
        calls = calls_by_generation.get(generation, 0)
        cumulative_usd += usd
        cumulative_calls += calls

        previous_best = running_best
        candidate_best = best_by_generation.get(generation)
        if candidate_best is not None and (
            running_best is None or candidate_best > running_best
        ):
            running_best = candidate_best

        if previous_best is None:
            # The seed generation establishes the baseline; it improves on
            # nothing, so it is neither an improvement nor a stagnation.
            delta = 0.0
            improved = running_best is not None
        else:
            delta = 0.0 if running_best is None else running_best - previous_best
            improved = delta > epsilon
        if improved:
            usd_at_last_improvement = cumulative_usd

        history = points[-width:]
        anchor = history[0] if len(history) >= width else (points[0] if points else None)
        if anchor is None or running_best is None or anchor.best is None:
            window_gain = 0.0
            window_usd = 0.0
        else:
            window_gain = running_best - anchor.best
            window_usd = cumulative_usd - anchor.cumulative_usd

        gain_per_usd = window_gain / window_usd if window_usd > 0 else None
        usd_per_gain = window_usd / window_gain if window_gain > 0 else None

        points.append(
            GenerationPoint(
                generation=generation,
                calls=calls,
                cumulative_calls=cumulative_calls,
                usd=usd,
                cumulative_usd=cumulative_usd,
                best=running_best,
                delta_best=delta,
                improved=improved,
                usd_since_improvement=cumulative_usd - usd_at_last_improvement,
                window=width,
                window_gain=window_gain,
                window_usd=window_usd,
                gain_per_usd=gain_per_usd,
                usd_per_gain=usd_per_gain,
            )
        )
    return points


def format_series(points: Sequence[GenerationPoint], tail: int = 8) -> str:
    """A fixed-width table of the last `tail` generations, for the terminal."""
    if not points:
        return "no generations recorded yet"
    shown = list(points)[-max(1, tail) :]
    header = (
        f"{'gen':>4}  {'calls':>5}  {'cum $':>9}  {'best':>11}  "
        f"{'d best':>10}  {'$ since imp':>11}  {'$/unit (w%d)' % shown[0].window:>14}"
    )
    lines = [header, "-" * len(header)]
    for point in shown:
        best = "n/a" if point.best is None else f"{point.best:.6g}"
        delta = f"{point.delta_best:+.4g}" if point.delta_best else "."
        lines.append(
            f"{point.generation:>4}  {point.cumulative_calls:>5}  "
            f"{point.cumulative_usd:>9.4f}  {best:>11}  {delta:>10}  "
            f"{point.usd_since_improvement:>11.4f}  {point.gain_line:>14}"
        )
    return "\n".join(lines)
