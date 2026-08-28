"""Stage evaluator for the bin-packing example.

    python evaluate.py --candidate <file.py> --inputs proxy,full --out kpis.json

Loads the candidate module, packs every instance in the named input sets, and
writes the KPI JSON the cascade reads. The headline KPI is mean excess over the
L1 lower bound, in percent -- the same quantity FunSearch and the classic
best-fit results are quoted in.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from instances import build_instances, lower_bound  # noqa: E402


def family_of(instance_name: str) -> str:
    """`full-or-3` -> `or_like`. The instance names encode their distribution."""
    return "weibull" if "-wb-" in instance_name else "or_like"


def _feedback(
    per_family: dict[str, list[float]],
    worst: tuple[str, float],
    diag: dict,
    instances: int,
) -> str:
    """Prose the KPIs cannot carry, for the next prompt's "Evaluator notes".

    Two things the mean hides and a heuristic's author would want: whether the
    rule is losing on one distribution while winning on the other, and which
    single instance it did worst on. A rule that is 4 % on uniform sizes and
    8 % on Weibull ones has a different next move from a rule that is 6 % on
    both, and `excess_pct` alone cannot tell those two apart.
    """
    lines = []
    for family in sorted(per_family):
        values = per_family[family]
        lines.append(
            f"{family}: {sum(values) / len(values):.3f}% excess over L1 across "
            f"{len(values)} instance(s), best {min(values):.3f}%, "
            f"worst {max(values):.3f}%"
        )
    if worst[0]:
        lines.append(f"worst single instance: {worst[0]} at {worst[1]:.3f}% excess")
    choices = float(diag.get("choices", 0) or 0)
    if choices:
        openness = float(diag.get("openness_sum", 0.0)) / choices
        lines.append(
            f"of {int(choices)} genuine choices (more than one feasible bin), the "
            f"rule sat {openness:.3f} of the way from the tightest feasible bin "
            "(0.0 = pure best fit) towards the emptiest (1.0 = worst fit)"
        )
    errors = int(diag.get("priority_errors", 0) or 0)
    if errors:
        lines.append(
            f"{errors} priority() call(s) raised, returned the wrong length or "
            "returned a non-finite score; those steps fell back to best fit and "
            "are charged as a penalty"
        )
    lines.append(f"{instances} instance(s) packed.")
    return "\n".join(lines)


def load_candidate(path: Path):
    spec = importlib.util.spec_from_file_location("evolvekit_candidate", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load candidate module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("pack", "priority", "new_diagnostics"):
        if not hasattr(module, name):
            raise AttributeError(f"candidate does not define {name}()")
    return module


def evaluate(module, set_names: list[str], seed: int = 0) -> dict[str, object]:
    """Pack every instance in `set_names`. `seed` re-draws the instance set.

    Deterministic for a given `(set_names, seed)`, which is what lets the
    framework average several seeds and report the spread: see `seeds:` in
    evolvekit.yaml and `instances.build_instances`.
    """
    instances = build_instances(set_names, seed)
    if not instances:
        raise ValueError(f"input sets {set_names} produced no instances")
    started = time.perf_counter()
    diag = module.new_diagnostics()
    excesses = []
    per_instance: list[float] = []
    per_family: dict[str, list[float]] = {}
    worst = ("", -1.0)
    total_bins = 0
    total_lb = 0
    overfull = 0
    for instance in instances:
        bins = module.pack(instance.items, instance.capacity, diag)
        used = len(bins)
        bound = lower_bound(instance)
        per_instance.append(float(used))
        total_bins += used
        total_lb += bound
        excess = 100.0 * (used - bound) / bound
        excesses.append(excess)
        per_family.setdefault(family_of(instance.name), []).append(excess)
        if excess > worst[1]:
            worst = (instance.name, excess)
        # The skeleton cannot overfill a bin, but a rewritten skeleton could;
        # count it rather than trust it.
        overfull += sum(1 for remaining in bins if remaining < 0)
    choices = float(diag.get("choices", 0) or 0)
    return {
        # Prose beside the numbers. `main()` lifts it out of the KPI mapping
        # before writing the JSON, because KPIs are numbers.
        "text_feedback": _feedback(per_family, worst, diag, len(instances)),
        "excess_pct": sum(excesses) / len(excesses),
        # Behaviour, not quality: 0 = always the tightest feasible bin,
        # 1 = always the emptiest. The archive uses it as a descriptor axis.
        "mean_openness": (
            float(diag.get("openness_sum", 0.0)) / choices if choices else 0.0
        ),
        "excess_pct_pooled": 100.0 * (total_bins - total_lb) / total_lb,
        "bins_used": float(total_bins),
        # A list KPI: bins used on each instance, in order. The score ignores
        # it (only scalars are scored); the behaviour signature reads it, so
        # two rules that merely agree on the *mean* are still told apart.
        "bins_per_instance": per_instance,
        "lower_bound": float(total_lb),
        "instances": float(len(instances)),
        "priority_errors": float(diag.get("priority_errors", 0)),
        "overfull_bins": float(overfull),
        "runtime_s": time.perf_counter() - started,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--inputs", required=True, help="comma-separated input set names")
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="re-draw the instance sets from a disjoint seed range; 0 is the "
        "published set. The framework passes {seed} on a `seeds: N` stage.",
    )
    args = parser.parse_args(argv)

    module = load_candidate(Path(args.candidate))
    set_names = [name for name in args.inputs.split(",") if name.strip()]
    kpis = evaluate(module, set_names, args.seed)
    # `text_feedback` rides beside `kpis`, not inside it: KPIs are numbers.
    feedback = str(kpis.pop("text_feedback", ""))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"kpis": kpis, "text_feedback": feedback}, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"{args.inputs} (seed {args.seed}): excess {kpis['excess_pct']:.3f}% over L1 "
        f"({int(kpis['bins_used'])} bins vs {int(kpis['lower_bound'])} bound), "
        f"{int(kpis['priority_errors'])} priority error(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
