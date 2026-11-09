"""The statistics of a paired comparison: small, exact, standard library.

`confirm` judges a candidate against its baseline with these, and so does the
live verdict in `status`: per run, the gain is a log ratio (`log_gain`), so
half as much and twice as much are one step either way and the noisy baseline
run is no denominator; per case, the gain is averaged over the seeds
(`per_instance_means`); over cases, `paired_summary` gives the mean, its 95 %
interval (Student's t) and an exact Wilcoxon signed-rank test. A percentage
shown to a person is of the starting point's value, from the log quantity
(`pct_from_log`), and a margin in percent has its log threshold (`margin_log`).
The final check decides on the posterior of the mean gain instead
(`posterior_mean_t`, read by `confirm.verdict_summary`); `paired_summary`
stays beside it as the frequentist test, for engineers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from statistics import fmean, stdev
from typing import Any, Hashable, Mapping, Sequence

__all__ = [
    "t_cdf", "wilcoxon_signed_rank", "paired_summary", "per_instance_means",
    "log_gain", "pct_from_log", "margin_log", "Posterior", "posterior_mean_t",
]

# Two-sided 95 % critical values of Student's t, by degrees of freedom.
_T95 = (
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
)


_Z975 = 1.959963984540054
"""The normal quantile the t quantile falls towards as the samples grow."""


def _t95(df: int) -> float:
    """The two-sided 95 % critical value of Student's t with `df` degrees of
    freedom. The table up to 30; beyond it the Cornish-Fisher expansion around
    the normal quantile, which is within 0.0005 of the exact value from 30 on.
    1.96 is the limit, not the value: at 31 it is 2.040, and an interval on
    32 instances drawn with 1.96 is about 4 % narrower than it should be."""
    if df < 1:
        raise ValueError(f"degrees of freedom must be >= 1, got {df}")
    if df <= len(_T95):
        return _T95[df - 1]
    z = _Z975
    return (
        z
        + (z**3 + z) / (4 * df)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)
        + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * df**3)
        + (79 * z**9 + 776 * z**7 + 1482 * z**5 - 1920 * z**3 - 945 * z) / (92160 * df**4)
    )


def _betacf(a: float, b: float, x: float) -> float:
    """The continued fraction of the incomplete beta function at `x`, by the
    modified Lentz method (Numerical Recipes `betacf`): 200 iterations at
    most, stopped when a step changes the value by less than 1e-12."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) >= tiny else tiny)
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) >= tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) >= tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) >= tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) >= tiny else tiny
        step = d * c
        h *= step
        if abs(step - 1.0) < 1e-12:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """The regularised incomplete beta function I_x(a, b), in [0, 1]. The
    continued fraction converges fast for x below (a + 1) / (a + b + 2); past
    it, the symmetry I_x(a, b) = 1 - I_{1-x}(b, a) brings it back."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(x: float, df: int) -> float:
    """The probability that Student's t with `df` degrees of freedom is below
    `x`: through I_z(df/2, 1/2) with z = df / (df + x²), no table and no
    SciPy. It is what turns a mean and its spread into the chance that the
    true mean gain is above zero."""
    if df < 1:
        raise ValueError(f"degrees of freedom must be >= 1, got {df}")
    z = df / (df + x * x)
    tail = 0.5 * _betainc(df / 2.0, 0.5, z)
    return 1.0 - tail if x >= 0 else tail


def wilcoxon_signed_rank(differences: Sequence[float]) -> dict[str, Any]:
    """The exact two-sided Wilcoxon signed-rank test of "the median difference
    is zero". Zeros are dropped, ties share their mean rank; the null
    distribution is enumerated, so there is no normal approximation to distrust
    at n = 10."""
    values = [d for d in differences if d != 0]
    n = len(values)
    if n == 0:
        return {"n": 0, "w_plus": 0.0, "p": None}
    order = sorted(range(n), key=lambda i: abs(values[i]))
    ranks2 = [0] * n  # twice the rank, so that a shared mean rank stays an integer
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(values[order[j + 1]]) == abs(values[order[i]]):
            j += 1
        for k in range(i, j + 1):
            ranks2[order[k]] = (i + 1) + (j + 1)
        i = j + 1
    w_plus2 = sum(r for r, v in zip(ranks2, values) if v > 0)
    total2 = sum(ranks2)
    counts = {0: 1}  # how many sign assignments give each (doubled) rank sum
    for r in ranks2:
        step: dict[int, int] = {}
        for s, c in counts.items():
            step[s] = step.get(s, 0) + c
            step[s + r] = step.get(s + r, 0) + c
        counts = step
    extreme = min(w_plus2, total2 - w_plus2)
    tail = sum(c for s, c in counts.items() if s <= extreme)
    p = min(1.0, 2.0 * tail / (2 ** n))
    return {"n": n, "w_plus": w_plus2 / 2.0, "p": p}


def paired_summary(differences: Sequence[float]) -> dict[str, Any]:
    """Mean, 95 % confidence interval and Wilcoxon test of per-instance
    differences -- and, from two instances on, `chance`, the probability that
    the true mean difference is above zero (one-sided, Student's t), and
    `worst`, the lower end of the interval: the worst plausible case."""
    n = len(differences)
    if n == 0:
        return {"n": 0, "mean": None, "ci95": None, "sd": None, "chance": None, "worst": None,
                "wilcoxon": wilcoxon_signed_rank([]), "wins": 0, "losses": 0, "verdict": "nothing to compare"}
    mean = fmean(differences)
    wins, losses = sum(1 for d in differences if d > 0), sum(1 for d in differences if d < 0)
    if n < 2:
        return {"n": n, "mean": mean, "ci95": None, "sd": None, "chance": None, "worst": None,
                "wilcoxon": wilcoxon_signed_rank(differences), "wins": wins, "losses": losses,
                "verdict": "one instance: no interval can be given"}
    sd = stdev(differences)
    half = _t95(n - 1) * sd / math.sqrt(n)
    low, high = mean - half, mean + half
    if sd > 0:
        chance = t_cdf(mean / (sd / math.sqrt(n)), n - 1)
    else:  # every instance moved by the same amount: there is nothing to be unsure about
        chance = 1.0 if mean > 0 else 0.0 if mean < 0 else 0.5
    if low > 0:
        verdict = "better than the baseline: the 95 % interval lies above zero"
    elif high < 0:
        verdict = "WORSE than the baseline: the 95 % interval lies below zero"
    else:
        verdict = "not distinguishable from the baseline: the 95 % interval includes zero"
    return {"n": n, "mean": mean, "sd": sd, "ci95": [low, high], "chance": chance, "worst": low,
            "wilcoxon": wilcoxon_signed_rank(differences), "wins": wins, "losses": losses, "verdict": verdict}


def per_instance_means(gains: Mapping[Hashable, float]) -> list[float]:
    """The gains averaged over the seeds within each case, in order of first
    appearance. A tuple key `(instance, seed)` belongs to the case `key[0]`;
    any other key -- a bare seed, on a stage without instances -- is a case of
    its own. The case, not the run, is what a paired comparison counts: one
    instance run on many seeds is one case."""
    groups: dict[Hashable, list[float]] = {}
    for key, gain in gains.items():
        group = key[0] if isinstance(key, tuple) else (key,)
        groups.setdefault(group, []).append(gain)
    return [fmean(values) for values in groups.values()]


@dataclass(frozen=True)
class Posterior:
    """The posterior of the true mean gain: Student's t with `df` degrees of
    freedom, location `mu` and scale `scale` (log units), from `n` cases.
    `scale == 0` only when every case moved by exactly the same amount."""

    n: int
    mu: float
    scale: float
    df: int

    def p_above(self, x: float) -> float:
        """The probability that the true mean gain is above `x`."""
        if self.scale == 0:
            return 1.0 if self.mu > x else 0.0 if self.mu < x else 0.5
        return 1.0 - t_cdf((x - self.mu) / self.scale, self.df)

    def quantile_low(self, tail: float) -> float:
        """The value the true mean gain is above with probability `1 - tail`;
        `tail` is 0.025 or 0.05 (the t quantiles `stats` tabulates)."""
        if self.scale == 0:
            return self.mu
        if tail == 0.025:
            return self.mu - _t95(self.df) * self.scale
        if tail == 0.05:
            return self.mu - _t90(self.df) * self.scale
        raise ValueError(f"tail must be 0.025 or 0.05, got {tail}")

    def interval95(self) -> tuple[float, float]:
        """The central 95 % credible interval of the true mean gain."""
        half = _t95(self.df) * self.scale
        return self.mu - half, self.mu + half


@lru_cache(maxsize=None)
def _t90(df: int) -> float:
    """The one-sided 95 % (two-sided 90 %) quantile of Student's t with `df`
    degrees of freedom, by bisection on `t_cdf` (no table to trust)."""
    low, high = 0.0, 10.0
    while t_cdf(high, df) < 0.95:
        high *= 2.0
    for _ in range(60):
        mid = (low + high) / 2.0
        if t_cdf(mid, df) < 0.95:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def posterior_mean_t(d: Sequence[float]) -> Posterior:
    """The posterior of the true mean of the per-case gains `d` (log units)
    under the reference prior p(mu, sigma^2) ~ 1 / sigma^2 -- no prior
    information on the mean or the spread, whatever the spread's source:

        mu | d ~ Student-t(n - 1, location dbar, scale s / sqrt(n))

    with dbar the mean and s the sample sd of `d`. Its chance of a gain is
    the one-sided t level, read as a posterior under this stated prior. With
    no spread at all (s = 0) the mean is known: scale 0, never a division by
    0. Raises `ValueError` with fewer than two gains: one case gives no spread."""
    n = len(d)
    if n < 2:
        raise ValueError(f"posterior_mean_t: needs at least two gains, got {n}")
    dbar = fmean(d)
    s = stdev(d)
    return Posterior(n=n, mu=dbar, scale=s / math.sqrt(n), df=n - 1)


def _sign(direction: str) -> float:
    """+1 when lower is better, -1 when higher is."""
    if direction == "minimize":
        return 1.0
    if direction == "maximize":
        return -1.0
    raise ValueError(f"direction must be 'minimize' or 'maximize', got {direction!r}")


def log_gain(base: float, mine: float, direction: str) -> float | None:
    """The gain of `mine` over `base` as a log ratio, positive when better:
    `s * (log base - log mine)`, s = +1 to minimise and -1 to maximise. None
    when either value is 0 or below: their ratio is no number."""
    sign = _sign(direction)
    if base <= 0 or mine <= 0:
        return None
    return sign * (math.log(base) - math.log(mine))


def pct_from_log(d: float, direction: str) -> float:
    """A log gain in percent of the starting point's value, positive when
    better: `100 * (exp(d) - 1)` higher when maximising, `100 * (1 - exp(-d))`
    lower when minimising (a cost cut from 100 to 90 is 10 %). Monotone in
    `d`, so an interval's ends and quantiles carry over."""
    if _sign(direction) > 0:
        return 100.0 * (1.0 - math.exp(-d))
    return 100.0 * (math.exp(d) - 1.0)


def margin_log(margin_pct: float, direction: str) -> float:
    """The log gain whose percentage is `-margin_pct`: the threshold "lose at
    most m %" on the log scale -- `log(1 - m/100)` when maximising and
    `-log(1 + m/100)` when minimising."""
    if _sign(direction) > 0:
        return -math.log(1.0 + margin_pct / 100.0)
    return math.log(1.0 - margin_pct / 100.0)
