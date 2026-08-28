"""Adaptive breadth: how many children a generation gets, decided by the run.

TurboEvolve (Apr 2026) adapts the number of weighted candidates per call on a
three-iteration stagnation window and reports roughly half the cost of
OpenEvolve at a matched budget. The mechanism it is really exploiting is that
breadth and depth are worth different amounts at different times: while the
search is climbing, one good child per generation is enough and the rest of the
budget is wasted; once it stalls, the only way out is more shots at once.

So:

* `grow_after` consecutive generations without an improvement adds one child,
  up to `max`;
* `shrink_after` consecutive generations *with* an improvement removes one,
  down to `min`.

The two counters are mutually exclusive -- a generation is either an
improvement or it is not -- and each resets the other. That is deliberately
simpler than a decay schedule: the number is a lever, not a model, and a lever
whose behaviour cannot be predicted from one line of the log is worse than a
slightly cruder one.

Off unless `search.adaptive_children` is configured, in which case
`search.children_per_generation` is the starting point, clamped into
`[min, max]`.
"""

from __future__ import annotations

from dataclasses import dataclass

from evolvekit.config import AdaptiveChildrenConfig, SearchConfig

__all__ = ["AdaptiveBreadth", "BreadthChange"]


@dataclass(frozen=True)
class BreadthChange:
    """What one generation did to the breadth, if anything."""

    previous: int
    current: int
    direction: str  # "grow", "shrink" or "hold"
    reason: str

    @property
    def moved(self) -> bool:
        return self.current != self.previous

    def __str__(self) -> str:
        if not self.moved:
            return f"breadth {self.current} held ({self.reason})"
        return (
            f"breadth {self.previous} -> {self.current} "
            f"({self.direction}: {self.reason})"
        )


@dataclass
class AdaptiveBreadth:
    """The current children-per-generation, and the rule that moves it."""

    config: AdaptiveChildrenConfig
    current: int
    stagnant: int = 0
    improving: int = 0

    @staticmethod
    def from_config(search: SearchConfig) -> "AdaptiveBreadth | None":
        """`None` when `search.adaptive_children` is absent -- the default."""
        rule = search.adaptive_children
        if rule is None:
            return None
        start = max(rule.min, min(rule.max, search.children_per_generation))
        return AdaptiveBreadth(config=rule, current=start)

    def update(self, improved: bool) -> BreadthChange:
        """Feed one generation's verdict; get the breadth for the next one."""
        previous = self.current
        if improved:
            self.stagnant = 0
            self.improving += 1
            if self.improving >= self.config.shrink_after and self.current > self.config.min:
                self.current -= 1
                self.improving = 0
                return BreadthChange(
                    previous,
                    self.current,
                    "shrink",
                    f"{self.config.shrink_after} improving generation(s); "
                    f"floor {self.config.min}",
                )
            return BreadthChange(
                previous,
                self.current,
                "hold",
                f"improving, {self.improving} of {self.config.shrink_after} "
                "toward a shrink"
                if self.current > self.config.min
                else f"improving, already at the floor of {self.config.min}",
            )

        self.improving = 0
        self.stagnant += 1
        if self.stagnant >= self.config.grow_after and self.current < self.config.max:
            self.current += 1
            self.stagnant = 0
            return BreadthChange(
                previous,
                self.current,
                "grow",
                f"{self.config.grow_after} generation(s) without improvement; "
                f"ceiling {self.config.max}",
            )
        return BreadthChange(
            previous,
            self.current,
            "hold",
            f"flat, {self.stagnant} of {self.config.grow_after} toward a grow"
            if self.current < self.config.max
            else f"flat, already at the ceiling of {self.config.max}",
        )
