"""Screening as a controller: does a cheap stage still earn its place?

A stage with `promote: {top_k_per_generation: k, adapt: true}` (a study's
screen) bets that it ranks candidates as the next stage does. Every promoted
candidate has a score at both, so after each round the run reads the rank
correlation ρ (`stats.spearman`) over every candidate that has both, the
seed included, cumulative over the run:

* `k` at or above the round's number of children (adaptive breadth can shrink
  it): switched off, before any ρ is read -- promoting every child is no screen;
* with fewer than `MIN_PAIRS` pairs, or no ρ (a side that does not vary),
  nothing changes;
* ρ < 0 at `CONTRARY_ROUNDS` checks running: the screen is **switched off**
  for the rest of the run -- the driver passes its stage over;
* else ρ < `WEAK`: one more is promoted a round (`k += 1`); when that makes
  `k` the round's number of children it would promote every child, which is
  no screen at all, so it is switched off instead (same sentence, same event).

ρ is over promoted candidates only, the best by the screen: the range is
restricted, and ρ reads low (a screen at 0.8 shows about 0.65). The thresholds
allow for that: an agreeing screen (0.8) is switched off in about 1 % of runs,
one that does not predict (0) grows `k` in about 91 % (`tests/test_screening.py`).

The state is `work/screening.json`, one entry per adapting stage
(`{"stages": {id: {k, off, below_zero, history}}}`), saved after every check,
so a resumed run keeps the `k` and the off each stage had.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from evolvekit.ledger import _atomic_write
from evolvekit.stats import spearman

__all__ = ["ScreenControl", "SCREENING_FILE", "MIN_PAIRS", "WEAK", "CONTRARY_ROUNDS"]

SCREENING_FILE = "screening.json"
"""Under the run's `work/`."""

MIN_PAIRS = 8
"""Fewer candidates through both stages say too little to act on."""

WEAK = 0.2
"""Below this the screen agrees only weakly: one more is promoted a round."""

CONTRARY_ROUNDS = 2
"""Checks running with ρ < 0 before the screen is switched off."""


def _read_stages(path: Path) -> dict[str, dict[str, Any]]:
    """`{stage id: state}` from the file at `path`; also its first shape, one
    stage's state with the id under `stage`. Empty when missing or unreadable."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("stages"), dict):
        return {str(sid): state for sid, state in payload["stages"].items() if isinstance(state, dict)}
    if isinstance(payload.get("stage"), str):
        return {payload["stage"]: {k: v for k, v in payload.items() if k != "stage"}}
    return {}


class ScreenControl:
    """The `k` and the on/off of one adapting stage, with the checks that set them."""

    def __init__(self, stage_id: str, k: int, cap: int, path: Path | None, *, later: str = "full") -> None:
        self.stage_id = stage_id
        self.later = later
        """The next stage's id: what the sentences say the screen is compared with."""
        self.k = int(k)
        self.cap = int(cap)
        """The round's number of children: promoting that many is no screen.
        The driver sets it before every check (adaptive breadth moves it)."""
        self.path = Path(path) if path is not None else None
        self.off = False
        self.below_zero = 0
        """Consecutive checks with ρ < 0."""
        self.history: list[dict[str, Any]] = []
        """One entry per check that read a ρ: `{generation, n, rho, k, off, said}`."""

    @classmethod
    def load(cls, path: Path, stage_id: str, k: int, cap: int, *, later: str = "full") -> "ScreenControl":
        """This stage's state kept at `path`; the configured `k`, on, when the
        file is missing, unreadable or holds no entry for the stage (a run
        from before the file)."""
        control = cls(stage_id, k, cap, path, later=later)
        payload = _read_stages(Path(path)).get(stage_id)
        if payload is None:
            return control
        try:
            control.k = max(1, int(payload.get("k", k)))
            control.off = bool(payload.get("off", False))
            control.below_zero = max(0, int(payload.get("below_zero", 0)))
        except (TypeError, ValueError):
            return cls(stage_id, k, cap, path, later=later)
        history = payload.get("history")
        control.history = [h for h in history if isinstance(h, dict)] if isinstance(history, list) else []
        return control

    def save(self) -> None:
        """This stage's entry written into the file, every other stage's kept."""
        if self.path is None:
            return
        stages = _read_stages(self.path)
        stages[self.stage_id] = {"k": self.k, "off": self.off, "below_zero": self.below_zero,
                                 "history": self.history}
        _atomic_write(self.path, json.dumps({"stages": stages}, indent=1) + "\n")

    def after_round(self, generation: int, pairs: Sequence[tuple[float, float]]) -> str | None:
        """Read the check after a round over `(screen score, next stage's score)`
        pairs. Returns the sentence that says what changed, or `None`."""
        said = self._check(generation, pairs)
        self.save()
        return said

    def _check(self, generation: int, pairs: Sequence[tuple[float, float]]) -> str | None:
        if self.off:
            return None
        if self.k >= self.cap:
            # Decision 10, whatever the correlation: the round's children have
            # shrunk to k (adaptive breadth), and promoting every child is no screen.
            self.off = True
            said = (
                f"screening off for the rest of the run: it promotes {self.k} a round and the round has "
                f"{self.cap} children ({len(pairs)} candidates through both stages; it would promote every child)"
            )
            self.history.append({"generation": int(generation), "n": len(pairs), "rho": None, "k": self.k,
                                 "off": True, "said": said})
            return said
        if len(pairs) < MIN_PAIRS:
            return None
        rho = spearman([float(a) for a, _ in pairs], [float(b) for _, b in pairs])
        if rho is None:
            return None
        n = len(pairs)
        reading = f"rho = {rho:.2f} over {n} candidates"
        self.below_zero = self.below_zero + 1 if rho < 0 else 0
        said: str | None = None
        if self.below_zero >= CONTRARY_ROUNDS:
            self.off = True
            said = (
                f"screening off for the rest of the run: its ranking disagreed with the {self.later} stage's "
                f"two rounds running ({reading})"
            )
        elif rho < WEAK and self.k < self.cap:
            self.k += 1
            if self.k >= self.cap:
                # Decision 10: promoting every child is no screen at all.
                self.off = True
                said = (
                    f"screening off for the rest of the run: it agrees with the {self.later} stage only weakly "
                    f"({reading}; one more a round and it would promote every child)"
                )
            else:
                said = (
                    f"screening promotes {self.k} a round from now on: it agrees with the {self.later} stage "
                    f"only weakly ({reading})"
                )
        self.history.append({"generation": int(generation), "n": n, "rho": rho, "k": self.k, "off": self.off,
                             "said": said})
        return said
