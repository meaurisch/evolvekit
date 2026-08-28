"""Hard cost caps and the stop policy. The loop stops itself; nobody babysits it.

Two independent objects:

* `BudgetGuard` -- refuses the next LLM call once USD or tokens are spent, and
  meters full-stage evaluations per calendar day.
* `StopPolicy`  -- patience / epsilon / target on the archive-best score, plus
  the two Phase C economics rules (`max_usd_since_improvement`,
  `min_gain_per_usd`). Per the redesign proposal a plateau may require
  `min_big_steps` strong-model attempts inside the stagnant window before it is
  allowed to stop the run; the economics rules carry the same guard, hard-wired
  to one -- stopping a run for being expensive without ever having tried the
  strong model is how a search gives up on the generation before the one that
  would have worked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from evolvekit.config import BudgetConfig, StopConfig
from evolvekit.economics import GenerationPoint

__all__ = ["BudgetGuard", "BudgetState", "StopPolicy", "StopDecision"]


@dataclass
class BudgetState:
    usd: float = 0.0
    tokens: int = 0
    calls: int = 0
    full_evals: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class BudgetVerdict:
    allowed: bool
    reason: str | None = None


class BudgetGuard:
    """Tracks spend against `budget:` and answers 'may I?' before every call."""

    def __init__(self, config: BudgetConfig, state: BudgetState | None = None) -> None:
        self.config = config
        self.state = state or BudgetState()

    # -- LLM spend -------------------------------------------------------

    def check(self) -> BudgetVerdict:
        if self.state.usd >= self.config.max_usd:
            return BudgetVerdict(
                False,
                f"budget.max_usd reached: ${self.state.usd:.4f} of ${self.config.max_usd:.4f}",
            )
        if self.state.tokens >= self.config.max_tokens:
            return BudgetVerdict(
                False,
                f"budget.max_tokens reached: {self.state.tokens} of {self.config.max_tokens}",
            )
        return BudgetVerdict(True)

    def spend(self, *, usd: float, tokens: int) -> None:
        self.state.usd += float(usd)
        self.state.tokens += int(tokens)
        self.state.calls += 1

    # -- full-stage evaluations -----------------------------------------

    def _today(self) -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def full_evals_today(self, day: str | date | None = None) -> int:
        key = self._key(day)
        return self.state.full_evals.get(key, 0)

    def _key(self, day: str | date | None) -> str:
        if day is None:
            return self._today()
        return day.isoformat() if isinstance(day, date) else str(day)

    def check_full_eval(self, day: str | date | None = None) -> BudgetVerdict:
        used = self.full_evals_today(day)
        cap = self.config.max_full_evals_per_day
        if used >= cap:
            return BudgetVerdict(
                False, f"budget.max_full_evals_per_day reached: {used} of {cap}"
            )
        return BudgetVerdict(True)

    def record_full_eval(self, day: str | date | None = None) -> None:
        key = self._key(day)
        self.state.full_evals[key] = self.state.full_evals.get(key, 0) + 1

    # -- reporting -------------------------------------------------------

    def summary(self) -> dict[str, float]:
        return {
            "usd": self.state.usd,
            "max_usd": self.config.max_usd,
            "tokens": float(self.state.tokens),
            "max_tokens": float(self.config.max_tokens),
            "calls": float(self.state.calls),
            "full_evals_today": float(self.full_evals_today()),
        }


@dataclass(frozen=True)
class StopDecision:
    stop: bool
    reason: str | None = None
    stagnant_generations: int = 0
    plateau: bool = False
    """True once the run has been flat for `patience/2` generations -- the
    driver's cue to spend a big step before the run is allowed to die."""


class StopPolicy:
    """Patience / epsilon / target over the archive-best score."""

    def __init__(self, config: StopConfig) -> None:
        self.config = config
        self.best: float | None = None
        self.stagnant = 0
        self.big_steps_in_window = 0

    def note_big_step(self) -> None:
        self.big_steps_in_window += 1

    @property
    def plateau(self) -> bool:
        """Half-patience: escalate to the strong model before giving up."""
        return self.stagnant >= max(1, self.config.patience // 2)

    def update(
        self,
        best_score: float | None,
        economics: GenerationPoint | None = None,
    ) -> StopDecision:
        """Feed the generation's archive-best score; get the verdict.

        `economics` is the last point of `economics.series()`. Passing it is
        optional so that the score-only policy stays testable on its own and so
        that a caller with no ledger (there are a few, in the tests) does not
        have to invent one.
        """
        if best_score is None:
            self.stagnant += 1
            return self._verdict(economics)

        if self.best is None or best_score > self.best + self.config.epsilon:
            self.best = max(best_score, self.best if self.best is not None else best_score)
            self.stagnant = 0
            self.big_steps_in_window = 0
        else:
            self.best = max(self.best, best_score)
            self.stagnant += 1

        if self.config.target is not None and self.best >= self.config.target:
            return StopDecision(
                True,
                f"stop.target reached: {self.best:.6g} >= {self.config.target:.6g}",
                self.stagnant,
                self.plateau,
            )
        return self._verdict(economics)

    # -- economics -------------------------------------------------------

    def economics_reason(self, point: GenerationPoint | None) -> str | None:
        """Why the money says to stop, or `None`.

        Both rules refuse to fire until at least one big step has been spent
        since the last improvement. A plateau's first answer is the strong
        model, not the exit.
        """
        if point is None or self.big_steps_in_window < 1:
            return None

        cap = self.config.max_usd_since_improvement
        if cap is not None and point.usd_since_improvement >= cap:
            return (
                f"stop.max_usd_since_improvement reached: "
                f"${point.usd_since_improvement:.4f} of ${cap:.4f} spent since "
                "the archive-best last moved"
            )

        rule = self.config.min_gain_per_usd
        if (
            rule is not None
            and point.gain_per_usd is not None
            and point.window_usd > 0
            and point.gain_per_usd < rule.threshold
        ):
            return (
                f"stop.min_gain_per_usd reached: {point.gain_per_usd:.6g} per USD "
                f"over the last {point.window} generation(s), below "
                f"{rule.threshold:.6g}"
            )
        return None

    def _verdict(self, economics: GenerationPoint | None = None) -> StopDecision:
        if self.stagnant >= self.config.patience:
            if self.big_steps_in_window >= self.config.min_big_steps:
                return StopDecision(
                    True,
                    f"stop.patience reached: no improvement > {self.config.epsilon:g} "
                    f"for {self.stagnant} generation(s)",
                    self.stagnant,
                    self.plateau,
                )
            return StopDecision(
                False,
                f"plateau held: {self.big_steps_in_window} of "
                f"{self.config.min_big_steps} required big step(s) tried",
                self.stagnant,
                True,
            )
        reason = self.economics_reason(economics)
        if reason is not None:
            return StopDecision(True, reason, self.stagnant, self.plateau)
        return StopDecision(False, None, self.stagnant, self.plateau)
