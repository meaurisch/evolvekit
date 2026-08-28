"""Result types for the evaluation cascade.

The central invariant, and the whole reason this package exists: `EvalResult.score`
is always a finite float. A gate violation is a penalty, a crashed command is
`evaluate.failure_score`, and only stage 0 may additionally set `rejected`.
`None` scores are what starved v1's search of gradient.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["StageOutcome", "EvalResult"]


@dataclass
class StageOutcome:
    """What one rung of the cascade produced."""

    stage_id: str
    ok: bool
    kpis: dict[str, float] = field(default_factory=dict)
    vector_kpis: dict[str, list[float]] = field(default_factory=dict)
    """List-valued KPIs, e.g. one entry per evaluated instance. They never
    reach the score (which needs scalars) -- they exist so the behaviour
    signature can be per-instance instead of per-mean."""
    kpi_cv: dict[str, float] = field(default_factory=dict)
    """Per-KPI coefficient of variation across the runs of a `seeds: N` stage.
    Empty for a single run. Zero for a KPI every run agreed on."""
    runs: int = 1
    """How many times this stage's command was executed (`stage.seeds`)."""
    text_feedback: str = ""
    """Optional prose from the evaluator, truncated to `stages.FEEDBACK_LIMIT`.
    The KPIs say what a candidate scored; this is where an evaluator says which
    instance it lost on, or that it used a tenth of its time budget."""
    score: float | None = None
    failure: str | None = None
    stderr: str = ""
    stdout: str = ""
    duration_s: float = 0.0
    skipped: bool = False
    private: bool = False


@dataclass
class EvalResult:
    """The finalised verdict for one candidate."""

    score: float
    rejected: bool = False
    reject_reason: str | None = None
    kpis: dict[str, float] = field(default_factory=dict)
    kpi_cv: dict[str, float] = field(default_factory=dict)
    """The spread of each KPI over a stochastic stage's runs, merged across
    stages the way `kpis` is. Recorded, never scored: the search ranks by the
    mean, and this is here so a human can see when the mean was a coin toss."""
    penalty_total: float = 0.0
    penalty_terms: dict[str, float] = field(default_factory=dict)
    stage_scores: dict[str, float] = field(default_factory=dict)
    stages_reached: list[str] = field(default_factory=list)
    outcomes: list[StageOutcome] = field(default_factory=list)
    public_score: float | None = None
    private_score: float | None = None
    generalization_gap: float | None = None
    ranking_score: float | None = None
    """`score` discounted by the hold-out gap; see `scoring.ranking_score`."""
    last_failure: str | None = None
    feedback: dict[str, str] = field(default_factory=dict)
    """The `text_feedback` each command stage reported, keyed by stage id. Only
    the deepest non-empty one reaches a prompt; the rest are kept for the record
    and for `preflight`."""
    behaviour_signatures: dict[str, str] = field(default_factory=dict)
    """One fingerprint per command stage reached; see `evaluate/signature.py`."""
    behaviour_twin_id: str | None = None
    """The earlier candidate this one behaved identically to, if any. Set
    together with `rejected`: a twin keeps its KPIs and its score for the
    record, but buys no further stages and never enters the archive."""

    @property
    def max_kpi_cv(self) -> float:
        """The noisiest KPI's spread, or 0.0. One number for a summary line."""
        return max(self.kpi_cv.values(), default=0.0)

    @property
    def deepest_stage(self) -> str | None:
        return self.stages_reached[-1] if self.stages_reached else None
