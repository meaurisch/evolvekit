"""KPIs -> one finite, maximised score.

    raw     = sum(weight_k * kpi_k) over evaluate.score.weights
    signed  = raw when direction is maximize, -raw when minimize
    score   = signed - sum(penalty terms)

Penalty terms are always subtractive, so a violating candidate ranks below an
otherwise identical clean one while still carrying a usable gradient. A missing
KPI counts as 0 rather than blowing up the run three hours in.
"""

from __future__ import annotations

import math

from evolvekit.config import PenaltyConfig, ScoreConfig

__all__ = ["compute_score", "penalty_terms", "scale_value", "ranking_score"]


def scale_value(value: float, scale: str) -> float:
    """Apply a penalty's shaping. `log1p` tames runaway violation counts."""
    magnitude = abs(float(value))
    if scale == "log1p":
        return math.log1p(magnitude)
    return magnitude


def penalty_terms(
    kpis: dict[str, float], penalties: tuple[PenaltyConfig, ...] | list[PenaltyConfig]
) -> dict[str, float]:
    """Per-penalty subtractive contributions, keyed by KPI name."""
    terms: dict[str, float] = {}
    for penalty in penalties:
        value = _finite(kpis.get(penalty.kpi, 0.0))
        terms[penalty.kpi] = penalty.weight * scale_value(value, penalty.scale)
    return terms


def compute_score(
    kpis: dict[str, float],
    score_cfg: ScoreConfig,
    penalties: tuple[PenaltyConfig, ...] | list[PenaltyConfig] = (),
) -> tuple[float, float, dict[str, float]]:
    """Return `(score, penalty_total, penalty_terms)`. Never returns `None`."""
    raw = sum(
        weight * _finite(kpis.get(kpi, 0.0)) for kpi, weight in score_cfg.weights.items()
    )
    signed = raw if score_cfg.direction == "maximize" else -raw
    terms = penalty_terms(kpis, penalties)
    total = sum(terms.values())
    score = signed - total
    if not math.isfinite(score):
        # NaN/inf from a pathological KPI must not poison the archive ordering.
        score = -1e12
    return score, total, terms


def ranking_score(
    score: float,
    public: float | None,
    private: float | None,
    holdout_penalty: float,
) -> float:
    """The score the search ranks by, discounted by the hold-out gap.

        ranking = public - penalty * max(0, public - private)

    A positive gap means the private hold-out scored *below* the public set,
    which is the signature of a candidate that fitted the instances it could
    see. A hold-out that did better is never a bonus, and a candidate that
    never reached the hold-out keeps its plain score: absent evidence is not
    evidence of overfitting.

    The default `evaluate.holdout_penalty` is 0, so by default this is the
    public score and the hold-out does not enter what the search ranks by: a
    hold-out that is a fitness term is selected on until it is no longer held
    out. What the hold-out does by default is a veto -- a candidate whose
    hold-out *regressed against the seed's*, which is what the 2026-08-22 run
    promoted to best, does not compete. That lives in
    `cascade.finished_final_stage` (`evaluate.holdout_veto`), not here, and the
    leaderboard's hold-out flag shows the same condition.
    """
    if public is None or private is None:
        return score
    gap = public - private
    return public - holdout_penalty * max(0.0, gap)


def _finite(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0
