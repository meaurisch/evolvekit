"""A study run as a process of its own, started by the app and outliving it.

    python -m evolvekit.app.job STUDY_FOLDER RUN_ID            the search, then the final check
    python -m evolvekit.app.job STUDY_FOLDER RUN_ID --check    the final check again, after a stop
    python -m evolvekit.app.job STUDY_FOLDER RUN_ID --check --seeds 6    ... with 6 runs per case
    python -m evolvekit.app.job STUDY_FOLDER RUN_ID --resume   the search again from where it stopped, then the check
    python -m evolvekit.app.job STUDY_FOLDER RUN_ID --continue --hours 2    a finished search on for 2 hours more, then the check again
    python -m evolvekit.app.job STUDY_FOLDER RUN_ID --bakeoff  the branches in runs/<id>/bakeoff.json against the starting point

Everything it does is `evolvekit.harness.execute`; this module only reads the
models the app chose (`runs/<id>/models.json`, when AI search help is on) and
reports the end in `job.json` -- including a failure before the run began.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from evolvekit.harness.execute import (
    JOB,
    Job,
    continue_study,
    final_check,
    read_job,
    resume_study,
    run_bakeoff,
    run_study,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evolvekit.app.job")
    parser.add_argument("study")
    parser.add_argument("run")
    parser.add_argument("--check", action="store_true", help="run the final check again")
    parser.add_argument("--seeds", type=int, default=None, help="with --check: runs per case")
    parser.add_argument("--resume", action="store_true", help="continue a stopped or dead search where it was")
    parser.add_argument("--continue", dest="more", action="store_true", help="a finished search on, with --hours more")
    parser.add_argument("--hours", type=float, default=None, help="with --continue: how much more time")
    parser.add_argument("--bakeoff", action="store_true", help="compare the branches in bakeoff.json, with no search")
    args = parser.parse_args(argv)
    root = Path(args.study).resolve()
    run_dir = root / "runs" / args.run

    def log(message: str) -> None:
        print(message, flush=True)

    try:
        if args.bakeoff:
            job = run_bakeoff(root, args.run, log=log)
        elif args.check:
            job = final_check(root, args.run, seeds=args.seeds, log=log)
        else:
            models_path = run_dir / "models.json"
            models = json.loads(models_path.read_text(encoding="utf-8")) if models_path.is_file() else None
            if args.more:
                if args.hours is None:
                    parser.error("--continue needs --hours")
                job = continue_study(root, args.run, hours=args.hours, models=models, log=log)
            else:
                run = resume_study if args.resume else run_study
                job = run(root, args.run, models=models, log=log)
    except Exception as exc:  # noqa: BLE001 - job.json must say how it ended, whatever ended it
        traceback.print_exc()
        state = read_job(run_dir)
        if state is None or state.get("phase") not in ("failed", "done", "stopped"):
            # It ended before the run began (compiling, the preview): nothing wrote job.json yet.
            run_dir.mkdir(parents=True, exist_ok=True)
            Job.resume(run_dir, state or {"run_id": args.run}, "failed").finish(
                "failed", error=f"{type(exc).__name__}: {exc}")
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    log(f"{JOB}: {job.get('phase')}")
    return 0 if job.get("phase") in ("done", "stopped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
