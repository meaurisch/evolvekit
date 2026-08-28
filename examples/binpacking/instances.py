"""Deterministic, synthetic online bin-packing instances. Nothing is downloaded.

Two families, both seeded from a name so a given input set is byte-identical on
every machine and every run:

* `or_like`   -- OR-Library OR1 shape: capacity 150, sizes uniform on [20, 100].
* `weibull`   -- the FunSearch paper's shape: capacity 100, Weibull(3, 45) sizes
                 clipped into [1, capacity].

These are *shaped like* the published benchmarks, not the benchmarks. See the
README on why the excess-over-lower-bound numbers are comparable in spirit
only.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

__all__ = ["Instance", "INPUT_SETS", "build_instances", "lower_bound"]


@dataclass(frozen=True)
class Instance:
    name: str
    capacity: int
    items: tuple[int, ...]


def or_like(name: str, n_items: int, seed: int) -> Instance:
    rng = random.Random(seed)
    return Instance(
        name=name,
        capacity=150,
        items=tuple(rng.randint(20, 100) for _ in range(n_items)),
    )


def weibull(name: str, n_items: int, seed: int, capacity: int = 100) -> Instance:
    rng = random.Random(seed)
    items = []
    for _ in range(n_items):
        raw = rng.weibullvariate(45.0, 3.0)
        items.append(max(1, min(capacity, int(round(raw)))))
    return Instance(name=name, capacity=capacity, items=tuple(items))


# Named input sets referenced from evolvekit.yaml's `inputs:` / `private_inputs:`.
# Seeds are disjoint across sets so the hold-out shares no instance with `full`.
INPUT_SETS: dict[str, list[tuple[str, int, int, str]]] = {
    # name -> [(instance name, n_items, seed, family)]
    "proxy": [
        ("proxy-or-%d" % i, 120, 1000 + i, "or_like") for i in range(3)
    ]
    + [("proxy-wb-%d" % i, 100, 1100 + i, "weibull") for i in range(2)],
    "full": [("full-or-%d" % i, 250, 2000 + i, "or_like") for i in range(12)]
    + [("full-wb-%d" % i, 250, 2100 + i, "weibull") for i in range(8)],
    "holdout": [("hold-or-%d" % i, 250, 9000 + i, "or_like") for i in range(3)]
    + [("hold-wb-%d" % i, 250, 9100 + i, "weibull") for i in range(2)],
}

_FAMILIES = {"or_like": or_like, "weibull": weibull}


SEED_STRIDE = 10_000
"""How far apart two `--seed` values move the instance seeds.

Larger than any input set, so seed 0 and seed 1 draw disjoint instances rather
than overlapping ones with a shift. `--seed 0` reproduces the published
numbers exactly, which is what makes it the default.
"""


def build_instances(set_names: list[str], seed: int = 0) -> list[Instance]:
    """Materialise every instance in the named sets, in a stable order.

    `seed` re-draws the whole set from a disjoint region of the generator's
    seed space. That makes this evaluator *stochastic on demand*: a stage
    configured with `seeds: 3` gets three independent draws from the same two
    distributions and the framework averages them, which is the honest way to
    ask "is this rule better, or did it get a good instance set?".
    """
    unknown = [n for n in set_names if n not in INPUT_SETS]
    if unknown:
        raise KeyError(
            f"unknown input set(s) {unknown}; known sets are {sorted(INPUT_SETS)}"
        )
    offset = int(seed) * SEED_STRIDE
    out: list[Instance] = []
    for set_name in set_names:
        for inst_name, n_items, base_seed, family in INPUT_SETS[set_name]:
            name = inst_name if not offset else f"{inst_name}@s{seed}"
            out.append(_FAMILIES[family](name, n_items, base_seed + offset))
    return out


def lower_bound(instance: Instance) -> int:
    """L1 bound: total size divided by capacity, rounded up. Never optimistic."""
    total = sum(instance.items)
    return -(-total // instance.capacity)
