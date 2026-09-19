"""Prove that every instance is feasible -- by solving it with pure defaults.

    python verify.py smoke/ --time-limit 20
    python verify.py instances/ --time-limit 120 --out verification.json

Why solve instead of argue
--------------------------
`generate.py` makes every single stop servable by construction, but whether
the FLEET can serve them all at once is a bin-packing question with time
windows, and no generator-side argument settles it. So each instance is run
through `solve.py` -- the same command line a tuner uses, PyVRP's default
parameters, seed 1 -- and the result is written down: feasible or not, the
objective, and whether the features that are supposed to matter actually do
(reloads, overtime, several vehicle classes, optional stops both served and
skipped). An instance that defaults cannot make feasible inside the limit has
no business in a tuning benchmark, because every configuration would be
compared on the infeasibility price instead of on cost.

One solver process at a time, always: the numbers are timing-sensitive and
the large instances need most of a gigabyte each. Results are merged into the
output file after every instance, so an interrupted run loses one instance,
and `--only` re-verifies a single one after a generator fix.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOT_INSTANCES = ("verification.json", "manifest.json")

KEPT = (
    "feasible", "objective", "iterations", "num_routes", "num_trips", "num_vehicles",
    "overtime", "routes_with_overtime", "vehicle_types_used", "vehicle_classes_used",
    "routes_per_class", "served_optional_clients", "unserved_optional_clients",
    "served_optional_groups", "unserved_optional_groups", "served_optional_shipments",
    "unserved_optional_shipments", "missing_required", "time_warp", "excess_load",
    "excess_distance", "prizes_collected", "prizes_uncollected", "distance", "duration",
    "fixed_cost", "distance_cost", "duration_cost", "time_to_first_feasible_s",
    "convergence", "load_s", "runtime_s", "time_limit_s", "peak_rss_mb", "seed",
    "pyvrp_version",
)


def verify_one(path: Path, time_limit: float, seed: int) -> dict:
    """Run `solve.py` on one instance and boil its JSON down to a record."""
    command = [
        sys.executable, str(HERE / "solve.py"), "--instance", str(path),
        "--time-limit", str(time_limit), "--seed", str(seed), "--diagnostics",
    ]
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    payload = path.read_bytes()
    record: dict = {
        "file": path.name,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "exit_code": done.returncode,
    }
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    if done.returncode != 0 or not lines:
        record["feasible"] = False
        record["error"] = (done.stderr.strip().splitlines() or ["no output"])[-1]
        return record
    result = json.loads(lines[-1])
    record.update({key: result[key] for key in KEPT})
    record["reloads_used"] = result["num_trips"] - result["num_routes"]
    record["num_vehicle_types_used"] = len(result["vehicle_types_used"])
    record["num_vehicle_classes_used"] = len(result["vehicle_classes_used"])
    record["missing_features"] = result["diagnostics"]["missing_features"]
    record["unservable_stops"] = result["diagnostics"]["unservable_stops"]
    record["penalty_bound_warning"] = "PenaltyBoundWarning" in done.stderr
    return record


def passed(record: dict) -> bool:
    return bool(
        record.get("feasible")
        and not record.get("missing_features")
        and not record.get("unservable_stops")
    )


def _summary(name: str, record: dict) -> str:
    if "error" in record:
        return f"{name}: ERROR {record['error']}"
    return (
        f"{name}: {'feasible' if record['feasible'] else 'INFEASIBLE'} "
        f"obj={record['objective']} it={record['iterations']} "
        f"routes={record['num_routes']}/{record['num_vehicles']} "
        f"reloads={record['reloads_used']} "
        f"overtime={record['overtime']}s/{record['routes_with_overtime']}r "
        f"classes={record['num_vehicle_classes_used']} types={record['num_vehicle_types_used']} "
        f"opt={record['served_optional_clients']}+/{record['unserved_optional_clients']}- "
        f"first={record['time_to_first_feasible_s']}s load={record['load_s']}s "
        f"rss={record['peak_rss_mb']}MB"
        + (f" MISSING={record['missing_features']}" if record["missing_features"] else "")
        + (f" UNSERVABLE={len(record['unservable_stops'])}" if record["unservable_stops"] else "")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dirs", nargs="+", type=Path, help="directories of instance files")
    parser.add_argument("--time-limit", type=float, default=120.0,
                        help="seconds per instance (default: 120)")
    parser.add_argument("--large-time-limit", type=float, default=None,
                        help="seconds for instances with at least --large-from clients")
    parser.add_argument("--large-from", type=int, default=2500)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--only", default="", help="verify only files whose name contains this")
    parser.add_argument("--out", type=Path, default=None,
                        help="result file (default: verification.json in the first directory)")
    args = parser.parse_args(argv)

    out = args.out or args.dirs[0] / "verification.json"
    document = {"results": {}}
    if out.exists():
        document = json.loads(out.read_text(encoding="utf-8"))
    document["command"] = "python verify.py " + " ".join(
        sys.argv[1:] if argv is None else argv)
    document["python"] = platform.python_version()
    document["platform"] = platform.platform()
    document["machine"] = platform.processor() or platform.machine()

    paths = sorted(
        path for folder in args.dirs for path in folder.glob("*.json")
        if path.name not in NOT_INSTANCES and args.only in path.name
    )
    if not paths:
        print("verify.py: no instance files found", file=sys.stderr)
        return 2
    failures = 0
    for path in paths:
        clients = len(json.loads(path.read_text(encoding="utf-8")).get("clients", []))
        limit = args.time_limit
        if args.large_time_limit is not None and clients >= args.large_from:
            limit = args.large_time_limit
        record = verify_one(path, limit, args.seed)
        name = path.name[: -len(".json")]
        document["results"][name] = record
        document["results"] = dict(sorted(document["results"].items()))
        text = json.dumps(document, sort_keys=True, indent=1) + "\n"
        out.write_bytes(text.encode("utf-8"))
        failures += int(not passed(record))
        print(_summary(name, record), flush=True)
    print(f"{len(paths) - failures}/{len(paths)} instances verified; results in {out}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
