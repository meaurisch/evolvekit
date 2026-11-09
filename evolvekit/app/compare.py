"""Two studies, or two runs, side by side: each side is one run's results
(`jobs.results`) cut down to what the compare page lines up by label -- the
verdict, the final check's line and interval, the held-back cases, what
changed, and the set-up. A side is named `<slug>` (its latest run) or
`<slug>/<run>`; a side that does not exist is a 404, and a run without a
final check keeps its place with `final.state == "missing"`.
"""

from __future__ import annotations

from typing import Any

from evolvekit.app import AppError, jobs

__all__ = ["PER_GROUP_COMPARE", "compare"]

PER_GROUP_COMPARE = "a run tuned per kind of case is compared group by group on its own page"
"""Why a per-group run is not one side of a compare (409)."""


def compare(home: Any, a: str, b: str, document_of: jobs.DocumentOf | None = None) -> dict[str, Any]:
    """`{"a": side, "b": side, "same_goal": bool}`; `same_goal` says whether
    the two runs chase the same measure in the same direction, so their
    numbers can be read against each other."""
    if document_of is None:
        from evolvekit.status import build_status

        document_of = build_status
    first, second = _side(home, a, document_of), _side(home, b, document_of)
    same = first.pop("_goal_kpi") == second.pop("_goal_kpi") and first["goal"]["direction"] == second["goal"]["direction"]
    return {"a": first, "b": second, "same_goal": same}


def _side(home: Any, ref: str, document_of: jobs.DocumentOf) -> dict[str, Any]:
    slug, _, run = str(ref or "").strip("/").partition("/")
    if not slug:
        raise AppError("name a study to compare, as <slug> or <slug>/<run>", 404)
    root, _, _ = home.load(slug)  # an unknown study is a 404 here
    run = run or jobs.latest_run(root)
    if not run:
        raise AppError(f"the study {slug!r} has not been run yet: there is nothing to compare", 404)
    if jobs.is_per_group(jobs._run_dir(root, run)):  # noqa: SLF001 - an unknown run is a 404 here
        # One group's results under the run's name would claim what only the run's own verdict may.
        raise AppError(PER_GROUP_COMPARE, 409)
    data = jobs.results(root, run, document_of)
    goal, final, context = data["goal"], data["final"], data["context"]
    what = jobs._kind_of(jobs.run_study_of(root, jobs._run_dir(root, run)))  # noqa: SLF001 - settings, data, both or code
    return {
        "slug": slug, "name": data["study"], "run": run, "harness": data["harness"], "what": what,
        "goal": {"says": goal["says"], "direction": goal["direction"]},
        "_goal_kpi": goal.get("kpi", ""),
        "headline": data["headline"],
        "final": {key: final.get(key) for key in ("state", "decision", "line", "advice", "chance", "ci95", "range90", "mean", "n", "worst_pct",
                                                    "posterior")},
        "improvement_pct": data.get("improvement_pct"), "phase": data.get("phase"),
        "held": [{key: row.get(key) for key in ("case", "start", "best", "change_pct")} for row in data["by_case"]["held"]],
        "changes": [{key: change.get(key) for key in ("name", "label", "old_text", "new_text", "changed")}
                    for change in data["changed"]],
        "setup": {"time_per_case_s": context["time_per_case_s"], "hours": context["hours"],
                  "combinations": context["tried"]},
    }
