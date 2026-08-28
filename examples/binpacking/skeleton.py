"""Online bin packing: a fixed greedy skeleton around one evolvable rule.

Items arrive one at a time and must be placed immediately and irrevocably. The
skeleton decides *that* the item goes in the highest-scoring feasible open bin,
or in a fresh bin when none fits. `priority()` decides *which* bin that is --
and `priority()` is the only thing the loop may rewrite.

The seed rule is best-fit, whose published excess over the L1 lower bound on
OR-Library OR1 is about 5.8%.
"""

from __future__ import annotations

import math

__all__ = ["pack", "priority", "new_diagnostics"]


def new_diagnostics() -> dict[str, float]:
    """Counters the fixed skeleton fills in while packing.

    `openness_sum` / `choices` is a *behaviour* descriptor rather than a
    quality one: 0 means the rule always took the tightest feasible bin (best
    fit), 1 means it always took the emptiest (worst fit). Two rules can score
    the same and sit at opposite ends of it, which is exactly what the archive
    wants on an axis.
    """
    return {"priority_errors": 0, "items": 0, "choices": 0, "openness_sum": 0.0}


def pack(items, capacity, diagnostics=None):
    """Greedy online packing. Returns the list of remaining capacities per bin.

    A `priority()` that raises, returns the wrong length, or returns a
    non-finite score does not abort the evaluation: the step falls back to
    best-fit and the incident is counted. Broken candidates therefore still
    produce a score -- a worse one -- instead of a `None`.
    """
    diag = diagnostics if diagnostics is not None else new_diagnostics()
    bins: list[int] = []
    for item in items:
        diag["items"] += 1
        feasible = [i for i, remaining in enumerate(bins) if remaining >= item]
        if not feasible:
            bins.append(capacity - item)
            continue
        capacities = [bins[i] for i in feasible]
        scores = _safe_scores(item, capacities, diag)
        best = feasible[max(range(len(feasible)), key=lambda j: scores[j])]
        _note_choice(bins[best], capacities, diag)
        bins[best] -= item
    return bins


def _note_choice(chosen, capacities, diag):
    """Record where the chosen bin sat between the tightest and the emptiest."""
    low, high = min(capacities), max(capacities)
    if high <= low:  # no choice was really made; nothing to learn
        return
    diag["choices"] = diag.get("choices", 0) + 1
    diag["openness_sum"] = diag.get("openness_sum", 0.0) + (chosen - low) / (high - low)


def _safe_scores(item, capacities, diag):
    try:
        scores = priority(item, list(capacities))
        values = [float(s) for s in scores]
    except Exception:  # noqa: BLE001 - any failure is one counted incident
        diag["priority_errors"] += 1
        return [-(c - item) for c in capacities]
    if len(values) != len(capacities) or not all(math.isfinite(v) for v in values):
        diag["priority_errors"] += 1
        return [-(c - item) for c in capacities]
    return values


# EVOLVE-BLOCK-START
def priority(item, bins):
    """Score every feasible open bin; the highest score wins.

    `item` is the size of the arriving item. `bins` is the list of remaining
    capacities of the bins that can still take it, so `b - item >= 0` always.
    Return one float per entry of `bins`, in the same order.

    Seed rule: best fit -- prefer the bin that will have the least space left.
    """
    return [-(b - item) for b in bins]
# EVOLVE-BLOCK-END
