"""The verdict of a run as one document: what the final check found, in the
words the Study page shows, with the numbers behind them.

`build_verdict(root, run)` is the one place the verdict is computed. It reads
the study folder, the run's `job.json` and its `confirm/final/` files -- it
needs no server and starts no process -- and returns a document with the keys
`schema`, `study`, `run`, `phase`, `decision`, `line`, `advice`, `measure`,
`held_back`, the statistics (`mean_pct`, `ci95_pct`, `worst_pct`, `chance`,
`chance_words`; absent while there is no answer: `pending`, `stopped`,
`skipped`), `levels` (goals in order of importance), `learned_from`, `rule`,
`by_case`, `changed`, `files` and how it ran. In `by_case`, `start` and `best`
are a held-back case's means over the final check's runs and `change_pct` is
the paired mean of the runs' changes (the statistic of `mean_pct`), so the two
need not agree with each other. `combinations` counts every combination the
search measured, the starting point among them: a plan of 2 combinations a
round for 2 rounds gives 5. `planned` holds the plan's `rounds` and
`combinations`; `how_it_ran`, only when fewer rounds ran than planned (or all
of them, but some combinations repeated one already tried) or its AI budget
was used up, says so in a sentence (`how_it_ran`). `why_keep`, only on a
`keep` whose held-back cases all moved the goal's way while its range still
allows a loss, says in one sentence why it is still keep (`why_keep`). `spent`, on a run
with AI, is what its AI spent (`ai_spent`): `{usd, cap_usd, line}`, or
`{requests, line}` for a provider that counts requests. The page
(`jobs.results`), the command line and the report all render from it, so they
cannot disagree.

A change every paired run of which gave exactly the starting point's result
(`confirm.identical_runs`) is `keep` with `no_difference_line` and no figures
at all: no range or chance can say anything about a change no run exercised.

A bake-off's document (a run of `study bakeoff`) also holds `candidates`:
`[{"ref", "commit", "decision", "line", "mean_pct", "ci95_pct", "worst_pct",
"chance", "by_case"}]`, one per branch in the order given, each against the
starting point by the same rule (numbers absent while there is no answer, and
for a branch that changed no run, which has `same: true` instead); `by_case`
is that branch's held-back rows, in the keys of the run's own `by_case`. Its own
`decision` and `line` are the chosen branch's -- the largest mean among those
whose decision is `use` -- said as one of N, or `keep` with "None of the N
branches is shown to beat the starting point." and then no figures, held-back
rows or changes of its own: no unnamed branch speaks for the run. The key
exists only on bake-off runs.

The words themselves -- the chance in twentieths, the decision of a run made
before runs decided, and the sentences of the final check -- live here too.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any

from evolvekit.confirm import BASELINE, RULE_WORDS, USE_CHANCE, decide, identical_runs, t_cdf

__all__ = ["DECISIONS", "SCHEMA", "ai_spent", "best_so_far", "build_verdict", "how_it_ran", "no_difference_line",
           "number_text", "why_keep"]

SCHEMA = 1
"""Bumped when a key of the document changes meaning."""
DECISIONS = ("use", "keep", "worse", "none", "single", "pending", "stopped", "skipped")
"""What a run's final check came to: `use`, `keep`, `worse` by the rule;
`none` when nothing beat the starting point; `single` with one held-back
case; `pending` before there is a final check; `stopped` when the check was
stopped; `skipped` for every other skip (no cases held back)."""
NUMBERS = ("mean_pct", "ci95_pct", "worst_pct", "chance", "chance_words")
"""The statistics of the document: absent while there is no answer."""
_NO_ANSWER = ("pending", "stopped", "skipped")
_STAYED_BEST = "starting point stayed the best"
"""What `execute._final_check` records when no combination beat the start."""
_NOT_RUN = "the search did not run"
"""What `execute._final_check` records when the search stopped before its first round (`execute.NOT_RUN`)."""


def _not_run(final: dict[str, Any]) -> bool:
    """Whether the run's search stopped before its first round (`execute.NOT_RUN`)."""
    return bool(final.get("not_run")) or str(final.get("skipped") or "").startswith(_NOT_RUN)
DOWNLOADS = "downloads"
VALUES = "values.json"
"""The values the exports in `downloads/` were written from: an input, not an export."""


def _pct(value: float | None, digits: int = 1) -> str:
    return "" if value is None else f"{abs(value):.{digits}f} %"


def _better_word(direction: str) -> str:
    return "lower" if direction == "lower" else "higher"


def number_text(value: float, digits: int = 1) -> str:
    """`value` to `digits` places, never "-0.0": a value that rounds to zero
    has no sign."""
    text = f"{value:.{digits}f}"
    return text[1:] if text.startswith("-") and float(text) == 0 else text


def _span(low: float, high: float) -> str:
    """A 95 % interval of an improvement in percent, in words."""
    if low > 0:
        return f"likely between {number_text(low)} % and {number_text(high)} % better"
    if high < 0:
        return f"likely between {number_text(-high)} % and {number_text(-low)} % worse"
    return f"somewhere between {number_text(-low)} % worse and {number_text(high)} % better"


def _chance_words(p: float | None) -> str:
    """A chance of a gain in twentieths: "about 19 in 20", never sounding
    certain: "better than 19 in 20" above it, "less than 1 in 20" below 1 in 20,
    and "almost certain" from 0.995; nothing for no chance."""
    if p is None:
        return ""
    if p >= 0.995:
        return "almost certain"
    if p < 0.05:
        return "less than 1 in 20"
    if p < USE_CHANCE and round(p * 20) >= round(USE_CHANCE * 20):
        # 0.897 would round to "about 18 in 20", which reads like the rule's
        # 9 in 10 when the rule said no: say which side of it the chance is.
        return f"under {round(USE_CHANCE * 10)} in 10"
    if round(p * 20) >= 20:
        return "better than 19 in 20"
    return f"about {round(p * 20)} in 20"


def _chance_of(summary: dict[str, Any]) -> float | None:
    """The chance of a gain as the comparison says it -- or, for a comparison
    written before it said so, from its mean and spread as `paired_summary`
    finds it. None without two cases, or without a spread to go by."""
    if summary.get("chance") is not None:
        return float(summary["chance"])
    n, mean, sd = int(summary.get("n") or 0), summary.get("mean"), summary.get("sd")
    if n < 2 or mean is None or sd is None:
        return None
    if sd > 0:
        return t_cdf(mean / (sd / math.sqrt(n)), n - 1)
    return 1.0 if mean > 0 else 0.0 if mean < 0 else 0.5


_NOT_REACHED = {
    # A code change: a setting the study fixes can override it, and so can code the cases never run
    # (persona round 2: a restart to the best tour when the current tour always is the best).
    "code change": "a fixed setting, or a code path these cases never reach, can leave a change without effect",
    "settings": "the setting may not reach the program",
    "data changes": "the data change may not reach the program",
}


TODAYS_CODE_LINE = "This branch is today's code (the same commit as the starting point): there is nothing to compare."


def no_difference_line(what: str) -> str:
    """The line of a change every paired run of which gave exactly today's
    result: no chance or range, and why the study may not exercise it --
    for a code study, for settings, for data changes (`jobs._what`)."""
    why = _NOT_REACHED.get(what, "the change may not reach the program")
    return ("No difference in any run: every run of this change gave exactly today's result, "
            f"so the study may not exercise it: {why}.")


def _identical(comparison: dict[str, Any], runs: list[dict[str, Any]]) -> None:
    """Mark each candidate of a comparison written before `confirm` said so
    (`identical`), from the check's own runs (`results.json`)."""
    kpis = [str(comparison.get("objective") or "")] + [str(level.get("kpi")) for level in comparison.get("levels") or []
                                                         if isinstance(level, dict)]
    kpis = [k for k in dict.fromkeys(kpis) if k]
    for cid, result in (comparison.get("per_candidate") or {}).items():
        if isinstance(result, dict) and "identical" not in result:
            result["identical"] = identical_runs(runs, cid, kpis, list(comparison.get("instances") or []),
                                                 list(comparison.get("seeds") or []))


def _decision_of(final: dict[str, Any], summary: dict[str, Any]) -> str:
    """The run's decision; a run made before runs decided gets it from the
    comparison by the same rule, or from the interval when the comparison
    gives no chance to apply the rule to."""
    if final.get("decision"):
        return str(final["decision"])
    n, ci, chance = int(summary.get("n") or 0), summary.get("ci95"), _chance_of(summary)
    if n >= 2 and chance is not None and ci:
        return decide({**summary, "chance": chance, "worst": summary.get("worst", ci[0])})
    if n == 0 or summary.get("mean") is None:
        return "none"
    if n == 1 or not ci:
        return "single"
    return "use" if ci[0] > 0 else "worse" if ci[1] < 0 else "keep"


def _final_words(job: dict[str, Any], comparison: dict[str, Any] | None, goal: dict[str, Any], what: str,
                 baseline: str = "") -> dict[str, Any]:
    """The final check in a sentence, what to do about it in another, and the
    rule that decided in a third: the app says "use it" only by `decide`."""
    final = job.get("final") or {}
    new = f"the new {what}"
    they = "it is" if what.startswith(("combination", "code")) else "they are"
    # Anything but "use" says the action outright, as the page does: keep today's settings, then why.
    keep = {"settings": "Keep today's settings", "data changes": "Keep today's data",
            "code change": "Keep today's code"}.get(what, "Keep today's settings and data")
    if baseline and what not in ("data changes", "code change"):
        keep += f" ({baseline})"
    if final.get("skipped") and _not_run(final):
        # Stopped before its first round (persona p5, J1): nothing was searched, so nothing is said about
        # what a search would find -- only why it did not run, and that Continue gives it its time.
        reason = str(final["skipped"])
        return {"state": "skipped", "not_run": True, "line": reason[0].upper() + reason[1:] + ".",
                "advice": f"{keep} for now: nothing was searched yet. Continue the run to give the search its time."}
    if final.get("skipped"):
        reason = str(final["skipped"])
        advice = (f"{keep}: the search found nothing better." if "starting point stayed the best" in reason else
                  f"{keep}: nothing was held back to check {new} on; a study that holds back a few cases can tell.")
        return {"state": "skipped", "line": reason[0].upper() + reason[1:] + ".", "advice": advice}
    if final.get("stopped"):
        return {"state": "stopped", "line": "The final check was stopped before it finished.",
                "advice": f"{keep}: the final check was stopped before it could tell; run it again for an answer "
                          "(the runs it already did are kept)."}
    if comparison is None:
        return {"state": "missing", "line": "There is no final check for this run.",
                "advice": f"{keep}: there is no final check yet to tell."}
    candidate = final.get("candidate") or next(iter(comparison.get("per_candidate") or {}), None)
    result = (comparison.get("per_candidate") or {}).get(candidate) or {}
    summary = result.get("summary") or {}
    n, mean, ci = summary.get("n") or 0, summary.get("mean"), summary.get("ci95")
    seeds = len(comparison.get("seeds") or [])
    chance = _chance_of(summary)
    worst = summary.get("worst") if summary.get("worst") is not None else (ci[0] if ci else None)
    decision = _decision_of(final, summary)
    better = _better_word(goal["direction"])
    worse = "higher" if better == "lower" else "lower"
    cases = f"{n} held-back case{'s' if n != 1 else ''}"
    base = {"n": n, "seeds": seeds, "mean": mean, "ci95": ci, "decision": decision, "chance": chance,
            "chance_words": _chance_words(chance), "worst_pct": worst, "rule": RULE_WORDS}
    use_advice = f"Use {new}: {they} probably better on cases the search never saw."
    if result.get("identical"):
        # Every paired run gave exactly today's result: no range or chance can say anything about a
        # change that was never exercised, so the line gives none.
        return {**base, "mean": None, "ci95": None, "chance": None, "chance_words": "", "worst_pct": None,
                "decision": "keep", "state": "same", "line": no_difference_line(what),
                "advice": f"{keep} for now: nothing in these runs tells the change from today's."}
    if "levels" in result:
        levels = result["levels"]
        decision = final.get("decision") or ("use" if levels.get("confirmed") else "keep")
        base["decision"] = decision
        if decision == "use":
            return {**base, "state": "confirmed", "levels": levels.get("levels"),
                    "line": f"Probably better on {cases}: {levels.get('verdict')}.", "advice": use_advice}
        return {**base, "state": "unclear", "levels": levels.get("levels"),
                "line": f"Not proven on {cases}: {levels.get('verdict')}.",
                "advice": f"{keep} for now: the gain is not proven."}
    if mean is None:
        return {**base, "state": "unclear", "line": "The final check has no comparable results.",
                "advice": f"{keep} for now: the gain is not proven."}
    word = better if mean >= 0 else worse
    if ci is None or decision == "single":
        # The decision first, as every other line leads with its word; the runs were all on that one case.
        on_it = f" (the {seeds} runs were all on it)" if seeds > 1 else ""
        return {**base, "state": "single",
                "line": f"Not proven, one held-back case: {_pct(mean)} {word} {goal['says']}.",
                "advice": f"{keep}: one held-back case is too few cases to tell{on_it}; "
                          "a study that holds back three or more can."}
    low, high = ci
    words = _chance_words(chance)
    sure = words == "almost certain"
    of_gain = "" if not words else "a gain almost certain" if sure else f"a chance of {words} of a gain"
    if decision == "use":
        start = "your starting point" + (f" ({baseline})" if baseline and what != "data changes" else "")
        edge = (f"at least {_pct(worst)} better than {start}" if worst is not None and worst >= 0.05
                else f"at worst about the same as {start}" if worst is not None and worst > -0.05
                else f"at worst {_pct(worst)} worse than {start}")
        how_sure = "" if not words else ", almost certain" if sure else f", a chance of {words}"
        return {**base, "state": "confirmed",
                "line": f"Probably better: {_pct(mean)} {better} {goal['says']} on {cases}{how_sure}; {edge}.",
                "advice": use_advice}
    if decision == "worse":
        return {**base, "state": "worse",
                "line": f"Worse on {cases}: {_pct(mean)} {worse} {goal['says']}{', ' + of_gain if of_gain else ''} "
                        f"({_span(low, high)}).",
                "advice": f"{keep}: {new} did worse on cases the search never saw."}
    parts = ([of_gain] if of_gain else []) + [f"at worst {_pct(worst)} worse ({_span(low, high)})"]
    return {**base, "state": "unclear", "line": f"Not proven on {cases}: {'; '.join(parts)}.",
            "advice": f"{keep} for now: the gain is not proven."}


# ---------------------------------------------------------------------------
# the pieces the page and the document share
# ---------------------------------------------------------------------------


def _check_of(run_dir: Path, final: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The final check's comparison and its runs (`confirm/final/`), as the job names them."""
    from evolvekit.app import jobs

    if not final.get("comparison"):
        return None, []
    path = run_dir / final["comparison"]
    comparison, runs = jobs._read(path), jobs._read_list(path.parent / "results.json")
    if isinstance(comparison, dict):
        _identical(comparison, runs)
    return comparison, runs


def _run_facts(run_dir: Path, job: dict[str, Any]) -> dict[str, Any]:
    """How the run ran: when it started (a datetime), when it finished, the
    seconds in between, and how many combinations it tried."""
    from evolvekit.app import jobs

    started, finished = jobs._started(job, run_dir), job.get("finished_at")
    used_s = None
    if started and finished:
        try:
            used_s = (datetime.fromisoformat(finished) - started).total_seconds()
        except (TypeError, ValueError):
            used_s = None
    return {"started": started, "finished_at": finished, "used_s": used_s,
            "tried": sum(1 for r in jobs._rows(run_dir) if not r.get("rejected"))}


def _by_case(run_dir: Path, goal: dict[str, Any], seed_id: str, best_id: str, candidate: str,
             check_runs: list[dict[str, Any]], *, learned: bool = True) -> dict[str, list[dict[str, Any]]]:
    """The goal case by case: on the cases the search learned from (the
    starting point against the best, from the search's own runs) and on the
    held-back cases (the final check's)."""
    from evolvekit.app import jobs

    by_case: dict[str, list[dict[str, Any]]] = {"learned": [], "held": []}
    if learned and goal["kpi"] and seed_id and best_id and seed_id != best_id:
        by_case["learned"] = jobs._by_case(jobs._search_evals(run_dir, {seed_id, best_id}), seed_id, best_id,
                                           goal["kpi"], goal["direction"])
    if goal["kpi"] and candidate:
        by_case["held"] = jobs._by_case(check_runs, BASELINE, candidate, goal["kpi"], goal["direction"])
    return by_case


def _changed(root: Path, study: Any, harness: Any, seed: dict[str, Any] | None,
             best: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every setting and data change the study tuned: the starting value, the
    best's, both as people write them, and whether it changed. A code study's
    one row is the change to its source: from the base, to the best's diff in lines."""
    from evolvekit.app import jobs

    if getattr(study, "code", None) is not None:
        return [_code_row(study, harness, seed, best)]
    changed = []
    starting = (seed or {}).get("params") or {}
    found = (best or {}).get("params") or {}
    for name in study.tunables():
        old, new = starting.get(name), found.get(name, starting.get(name))
        if name in harness.settings:
            setting = harness.settings[name]
            label, help_text = setting.label, setting.help
        else:
            change = study.data[name]
            lever = harness.levers.get(change.lever)
            column = (lever.columns.get(change.column) if lever and change.column else None) or change.column or "in code"
            label = change.says or f"{lever.label if lever else change.lever}: {column} where {change.where or 'all rows'}"
            help_text = lever.help if lever else ""
            today = jobs._today(root, harness, change)
            factor = {"scale": "×", "add": "+"}.get(change.mode, "=")
            if today and jobs._finite(new):
                # The values it makes, not the factor: "3 → 2.82 (×0.939)".
                changed.append({"name": name, "label": label, "old": old, "new": new, "changed": old != new,
                                "help": f"{help_text} Today's value on the smallest case, and what the change makes of it.".strip(),
                                "old_text": jobs._span_text(today),
                                "new_text": f"{jobs._span_text(jobs._changed_values(today, change.mode, new))} ({factor}{float(new):,.3g})"})
                continue
        changed.append({"name": name, "label": label, "help": help_text, "old": old, "new": new,
                        "old_text": jobs._describe_value(old), "new_text": jobs._describe_value(new),
                        "changed": old != new})
    return changed


def _code_row(study: Any, harness: Any, seed: dict[str, Any] | None, best: dict[str, Any] | None) -> dict[str, Any]:
    """`{"name": "code", "label", "from": "<ref> (<base[:7]>)", "to": "+N −M lines in <file or N files>"}`,
    in the keys `_changed`'s rows have."""
    from evolvekit import tree  # only a code study's verdict reads a diff

    base = str((seed or {}).get("commit") or "")
    old = study.code.from_ref + (f" ({base[:7]})" if base else "")
    block = str((best or {}).get("block") or "")
    stat = tree.diff_stat(block)
    files = stat["files"]
    where = files[0] if len(files) == 1 else f"{len(files)} files"
    new = f"+{stat['added']} −{stat['removed']} lines in {where}" if files else "no change"
    label = harness.code.label if getattr(harness, "code", None) is not None else "The source"
    return {"name": "code", "label": label, "help": "", "old": base or None, "new": (best or {}).get("commit"),
            "old_text": old, "new_text": new, "changed": bool(block.strip())}


# ---------------------------------------------------------------------------
# the document
# ---------------------------------------------------------------------------


def _decision_word(words: dict[str, Any], final: dict[str, Any]) -> str:
    """`_final_words`'s state as one of `DECISIONS`."""
    state = words.get("state")
    if state == "confirmed":
        return str(words.get("decision") or "use")
    if state == "skipped":
        return "none" if _STAYED_BEST in str(final.get("skipped") or "") else "skipped"
    return {"unclear": "keep", "same": "keep", "missing": "pending", "stopped": "stopped", "single": "single",
            "worse": "worse"}.get(str(state), "pending")


def _clean(value: Any) -> Any:
    """`value` with no NaN or infinity in it (None in their place), ready for strict JSON."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _measure_of(study: Any, harness: Any, kpi: str, direction: str) -> dict[str, Any]:
    from evolvekit.harness.study import resolve_kpi

    try:
        resolved = resolve_kpi(study, harness, kpi) if kpi else {}
    except KeyError:
        resolved = {}
    return {"kpi": kpi, "label": resolved.get("says") or kpi or "the goal", "unit": resolved.get("unit") or "",
            "direction": direction}


def _level_direction(row: dict[str, Any]) -> str:
    return "higher" if row.get("direction") in ("maximize", "higher") else "lower"


def _levels(study: Any, harness: Any, levels: dict[str, Any]) -> list[dict[str, Any]]:
    """One entry per goal level, in order of importance, `deciding` on the one that decided."""
    deciding = levels.get("deciding")
    entries = []
    for index, row in enumerate(levels.get("levels") or [], start=1):
        percent = row.get("unit") == "percent"
        ci = row.get("ci95") if percent else None
        entries.append({
            **_measure_of(study, harness, str(row.get("kpi") or ""), _level_direction(row)),
            "mean_pct": _number(row.get("mean_difference")) if percent else None,
            "ci95_pct": list(ci) if ci else None, "worst_pct": _number(ci[0]) if ci else None,
            "chance": None, "chance_words": "",
            "state": row.get("state"), "compared_in": row.get("unit"), "difference": _number(row.get("mean_difference")),
            "deciding": deciding == row.get("level", index),
        })
    return entries


def _outcome(row: dict[str, Any]) -> str:
    """A held-back case's row of `_by_case` in one word."""
    if not row.get("runs"):
        return "failed"
    change = row.get("change_pct")
    if change is None or abs(change) < 0.05:
        return "same"
    return "better" if change > 0 else "worse"


def _files(root: Path, run_dir: Path) -> list[str]:
    """The exports in `runs/<id>/downloads/`, relative to the study folder."""
    folder = run_dir / DOWNLOADS
    if not folder.is_dir():
        return []
    return sorted(p.relative_to(root).as_posix() for p in folder.iterdir() if p.is_file() and p.name != VALUES)


def _document(root: Path, run_dir: Path, study: Any, harness: Any, job: dict[str, Any], words: dict[str, Any],
              comparison: dict[str, Any] | None, held: list[dict[str, Any]], changed: list[dict[str, Any]],
              facts: dict[str, Any], learned_pct: float | None, *, baseline: str = "",
              check_runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The verdict document of a run from what the page computes too:
    `words` (`_final_words`), the held-back rows of `_by_case`, `_changed`
    and `_run_facts`. A bake-off's document also lists its `candidates`
    (`baseline` is the starting point in words, for their sentences; each
    branch's held-back rows come from `check_runs`, the final check's runs)."""
    from evolvekit.app import jobs

    final = job.get("final") or {}
    goal = jobs._goal(study, harness)
    decision = _decision_word(words, final)
    document: dict[str, Any] = {"schema": SCHEMA, "study": root.name, "run": run_dir.name, "phase": job.get("phase"),
                                "decision": decision}
    if decision in ("skipped", "none"):
        document["reason"] = str(final.get("skipped") or "")
    if words.get("not_run"):
        document["not_run"] = True  # the search stopped before its first round: Continue gives it its time
    document["line"], document["advice"] = words.get("line", ""), words.get("advice", "")
    seed_row = next((r for r in jobs._rows(run_dir) if r.get("operator") == "human-seed"), None)
    candidates = (_candidates(job, comparison, goal, jobs._what(study), baseline, str((seed_row or {}).get("commit") or ""),
                              check_runs or [])
                  if job.get("bakeoff") else None)
    named = True
    if candidates:
        decision, named = _bakeoff_headline(document, job, candidates, jobs._what(study))
    candidate = final.get("candidate") or next(iter((comparison or {}).get("per_candidate") or {}), None)
    result = ((comparison or {}).get("per_candidate") or {}).get(candidate) or {}
    levels = _levels(study, harness, result["levels"]) if isinstance(result.get("levels"), dict) else []
    deciding = next((level for level in levels if level["deciding"]), levels[0] if levels else None)
    document["measure"] = ({k: deciding[k] for k in ("kpi", "label", "unit", "direction")} if deciding else
                           _measure_of(study, harness, goal["kpi"], goal["direction"]))
    seeds = len((comparison or {}).get("seeds") or []) or final.get("seeds") or None
    document["held_back"] = {"cases": len(study.test), "runs_per_case": seeds}
    # A change every run of which gave exactly today's result has no figures: no range, no chance.
    same = words.get("state") == "same"
    if decision not in _NO_ANSWER and named and not same:
        document.update({"mean_pct": words.get("mean"), "ci95_pct": words.get("ci95"), "worst_pct": words.get("worst_pct"),
                         "chance": words.get("chance"), "chance_words": words.get("chance_words", "")})
    if levels and named and not same:
        document["levels"] = levels
    # A bake-off with no branch shown ahead names no branch: no figure of an unnamed one here either.
    # A search that did not run learned nothing: no figure, not even "the same" (persona p5, J1).
    document["learned_from"] = {"cases": len(study.training),
                                "pct": learned_pct if named and not words.get("not_run") else None}
    document["rule"] = RULE_WORDS
    document["by_case"] = _held_rows(result, held)
    # Not with goals in order of importance: the range and the rows are the first measure's, while
    # another level may be what kept it (fix round 1, I1).
    why = (why_keep(document["decision"], words, document["by_case"], goal["direction"])
           if named and not candidates and not levels and "levels" not in result else "")
    if why:
        document["why_keep"] = why
    document["changed"] = [{"name": c["name"], "label": c["label"], "from": c["old_text"], "to": c["new_text"]}
                           for c in changed if c["changed"]]
    if not named:
        # A bake-off with no branch shown ahead: no unnamed branch speaks for the run. Each branch's
        # figures are in `candidates`, with its ref.
        document["by_case"], document["changed"] = [], []
    document["files"] = _files(root, run_dir)
    document.update({"started_at": job.get("started_at") or (facts["started"].isoformat() if facts["started"] else None),
                     "finished_at": facts["finished_at"], "elapsed_s": facts["used_s"],
                     "rounds": (job.get("search") or {}).get("generations"), "combinations": facts["tried"]})
    plan = jobs._plan(run_dir, job)
    if not job.get("bakeoff"):
        document["planned"] = {"rounds": plan.get("rounds"), "combinations": plan.get("combinations")}
    ran = how_it_ran(job, plan, facts["tried"], bool(study.budget.ai_enabled), repeated=_repeated(run_dir))
    if ran:
        document["how_it_ran"] = ran
    spent = ai_spent(run_dir, study, job)
    if spent is not None:
        document["spent"] = spent
    if candidates is not None:
        document["candidates"] = candidates
    return _clean(document)


def _held_rows(result: dict[str, Any], held: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The document's `by_case` rows from `_by_case`'s held-back rows of one
    candidate, whose comparison result is `result`. A case's change is the
    comparison's own: the paired mean over the final check's runs, the
    statistic the line is made of -- not the change of the means, which can
    differ by a few tenths."""
    paired = {} if "levels" in result else {
        str(r.get("instance")): r["improvement_pct"] for r in result.get("per_instance") or []
        if isinstance(r, dict) and _number(r.get("improvement_pct")) is not None}
    rows = [{**row, "change_pct": paired.get(row["case"], row["change_pct"])} for row in held]
    return [{"name": row["case"], "start": row["start"], "best": row["best"], "change_pct": row["change_pct"],
             "outcome": _outcome(row)} for row in rows]


def why_keep(decision: str, words: dict[str, Any], rows: list[dict[str, Any]], direction: str) -> str:
    """Persona p4 (H2): a `keep` whose held-back cases ALL moved the goal's way, while its range still
    allows a loss, says why in one plain sentence: "Every held-back case was lower (3.1 % and 8.2 %),
    but with only 2 cases the range is too wide to rule out a loss: hold back more cases for a firmer
    answer." -- up to three cases' figures, else "Every one of the N held-back cases". Never on use,
    worse or same, nor with goals in order of importance (`levels`), and never a gain: it says why there
    is none. Empty otherwise."""
    ci = words.get("ci95")
    if decision != "keep" or words.get("state") != "unclear" or not ci or not ci[0] < 0 or words.get("levels"):
        return ""
    if len(rows) < 2 or any(row.get("outcome") != "better" for row in rows):
        return ""
    word, n = _better_word(direction), len(rows)
    if n <= 3:
        figures = [_pct(row.get("change_pct")) for row in rows]
        listed = figures[0] if n == 1 else ", ".join(figures[:-1]) + " and " + figures[-1]
        said = f"Every held-back case was {word} ({listed})"
    else:
        said = f"Every one of the {n} held-back cases was {word}"
    return (f"{said}, but with only {n} cases the range is too wide to rule out a loss: "
            "hold back more cases for a firmer answer.")


def _repeated(run_dir: Path) -> int:
    """How many of the search's combinations repeated one already tried, so were not measured."""
    from evolvekit.app import jobs

    return sum(1 for r in jobs._rows(run_dir) if r.get("rejected") and str(r.get("reject_reason") or "").startswith("duplicate"))


AI_BUDGET_STOPS = ("the AI budget was used up", "budget.max_usd reached")
"""How a search's stop reason starts when its AI budget ended it (`budget.AI_USED_UP`, `BudgetGuard.check`)."""


def how_it_ran(job: dict[str, Any], plan: dict[str, Any], tried: int | None, ai: bool, *, repeated: int = 0) -> str:
    """Whenever fewer rounds ran than the plan said (persona p4, H7), the
    sentence that says so, with the reason from the stop: "Ran 2 of the 11
    planned rounds (25 combinations, about 132 planned): the time ran out; the
    AI's replies took longer than the plan assumed." -- or "…: the search time
    was used.", "…: stopped.", "…: the search stopped early (<its reason>)."
    When its AI budget ended it: "Ran 2 of the 3 planned rounds (…): the AI
    budget was used up.", or "The AI budget was used up in round 4 (…)." when no
    planned round was left out. Every round ran, but `repeated` combinations
    repeated one already tried: "Ran all 17 planned rounds (176 combinations,
    about 204 planned); 29 of the combinations tried repeated one already tried
    and were not measured again." -- what it knows, not a claim that it is the
    whole shortfall. Empty when the plan happened, for a bake-off (no
    search), and for a run continued with more time (its plan is the
    continuation's)."""
    search = job.get("search") or {}
    done, planned = search.get("generations"), plan.get("rounds")
    if job.get("bakeoff") or job.get("continued_at") or not isinstance(done, int) or not isinstance(planned, int):
        return ""
    reason = str(search.get("stop_reason") or "")
    combinations = []
    if isinstance(tried, int):
        combinations.append(f"{tried} combination{'s' if tried != 1 else ''}")
    if isinstance(plan.get("combinations"), int):
        combinations.append(f"about {plan['combinations']} planned")
    counts = f" ({', '.join(combinations)})" if combinations else ""
    rounds = f"Ran {done} of the {planned} planned round{'s' if planned != 1 else ''}"
    if done == 0 and planned > 0:
        # The search did not run (persona p5, J1): the line says why; this says only what was measured --
        # never that "the rounds took longer than the plan assumed", which no round did.
        return f"None of the {planned} planned round{'s' if planned != 1 else ''} ran: only today's settings were measured."
    if reason.startswith(AI_BUDGET_STOPS):
        if done < planned:
            return f"{rounds}{counts}: the AI budget was used up."
        return f"The AI budget was used up in round {done}{counts}."
    if done >= planned:
        if repeated > 0 and isinstance(tried, int) and isinstance(plan.get("combinations"), int) \
                and tried < plan["combinations"]:
            return (f"Ran all {planned} planned round{'s' if planned != 1 else ''}{counts}; {repeated} of the "
                    f"combinations tried repeated one already tried and {'were' if repeated != 1 else 'was'} "
                    "not measured again.")
        return ""
    if reason.startswith("time limit reached"):
        return f"{rounds}{counts}: the search time was used."
    if reason.startswith("time limit"):
        why = "the AI's replies took longer than the plan assumed" if ai else "the rounds took longer than the plan assumed"
        return f"{rounds}{counts}: the time ran out; {why}."
    if not reason or reason.startswith("stopped"):
        return f"{rounds}{counts}: stopped."
    return f"{rounds}{counts}: the search stopped early ({reason.rstrip('.')})."


COUNTS_REQUESTS = ("copilot-cli",)
"""The providers whose AI counts requests against a plan, not dollars."""


def ai_spent(run_dir: Path, study: Any, job: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """What the run's AI spent, from its own usage records (`usage.jsonl`):
    `{"usd", "cap_usd", "line"}` with the line "AI: $0.87 of $1.00" (the cap is
    the one the run was compiled with, `compile.ai_cap_usd`); for a provider
    that counts requests, `{"requests", "line"}`, "AI: 14 requests" (a record
    from before records said so counts one). None for a run without AI (a study
    without AI help, a bake-off). A fact, not a claim about any result."""
    from evolvekit.harness.compile import ai_cap_usd
    from evolvekit.ledger import read_jsonl

    if not getattr(study.budget, "ai_enabled", False) or (job or {}).get("bakeoff"):
        return None
    rows = list(read_jsonl(run_dir / "usage.jsonl"))
    if any(str(r.get("provider")) in COUNTS_REQUESTS for r in rows):
        requests = sum(float(r["requests"]) if _number(r.get("requests")) is not None else 1.0
                       for r in rows if str(r.get("provider")) in COUNTS_REQUESTS)
        return {"requests": requests, "line": f"AI: {requests:g} request{'s' if requests != 1 else ''}"}
    usd = sum(_number(r.get("usd")) or 0.0 for r in rows)
    cap = ai_cap_usd(study)
    return {"usd": usd, "cap_usd": cap, "line": f"AI: ${usd:.2f} of ${cap:.2f}"}


def _candidates(job: dict[str, Any], comparison: dict[str, Any] | None, goal: dict[str, Any], what: str,
                baseline: str, base_commit: str = "", check_runs: list[dict[str, Any]] | None = None
                ) -> list[dict[str, Any]]:
    """A bake-off's branches in the order they were given, each with its own
    decision and line against the starting point: `_final_words` as if the
    final check had checked that one alone; and its `by_case`, each held-back
    case's figures as the search verdict's `by_case` gives them (`_held_rows`),
    from the final check's runs (`check_runs`). A branch that is the starting
    point itself has no rows: there is nothing to compare."""
    from evolvekit.app import jobs

    final = job.get("final") or {}
    decisions = final.get("decisions") or {}
    out = []
    for entry in job.get("bakeoff") or []:
        cid = str(entry.get("id") or "")
        own = {**final, "candidate": cid}
        own.pop("decision", None)
        if cid in decisions:
            own["decision"] = decisions[cid]
        words = _final_words({**job, "final": own}, comparison, goal, what, baseline)
        decision = _decision_word(words, own)
        row: dict[str, Any] = {"ref": entry.get("ref"), "commit": entry.get("commit"), "decision": decision,
                               "line": words.get("line", "")}
        if base_commit and entry.get("commit") == base_commit:
            # The branch is the starting point itself (e.g. `main` when the study starts from HEAD = main).
            row.update({"decision": "keep", "same": True, "today": True, "line": TODAYS_CODE_LINE})
        elif words.get("state") == "same":
            row["same"] = True  # every run gave exactly today's result: no figures
        elif decision not in _NO_ANSWER:
            row.update({"mean_pct": words.get("mean"), "ci95_pct": words.get("ci95"),
                        "worst_pct": words.get("worst_pct"), "chance": words.get("chance")})
        if not row.get("today") and goal.get("kpi") and cid:
            result = ((comparison or {}).get("per_candidate") or {}).get(cid) or {}
            row["by_case"] = _held_rows(result, jobs._by_case(check_runs or [], BASELINE, cid, goal["kpi"],
                                                              goal["direction"]))
        out.append(row)
    return out


def _bakeoff_headline(document: dict[str, Any], job: dict[str, Any],
                      candidates: list[dict[str, Any]], what: str = "code change") -> tuple[str, bool]:
    """Decision 32: a bake-off's own decision and line are those of the branch
    it chose (the largest mean among those whose own decision is `use`), said
    as one of N ("Of 2 branches, <ref>: ..."; "<ref>: ..." for one branch);
    with one held-back case the chosen branch's own `single` line, named the
    same way. When no branch is `use` the starting point stays (`keep`, "None of
    the N branches is shown to beat the starting point.") and no branch's
    figures speak for the run. A check with no answer yet (stopped, pending)
    keeps its own words. Returns the decision and whether the run's own figures
    are those of a branch the line names."""
    count = len(candidates)
    branches = f"{count} branch{'es' if count != 1 else ''}"
    chosen_id = str((job.get("final") or {}).get("candidate") or "")
    chosen = next((c for e, c in zip(job.get("bakeoff") or [], candidates) if str(e.get("id")) == chosen_id), None)

    def named(c: dict[str, Any]) -> str:
        line = str(c["line"])
        return (f"{c['ref']}: " if count == 1 else f"Of {branches}, {c['ref']}: ") + line[:1].lower() + line[1:]

    if chosen is not None and chosen["decision"] in ("use", "single"):
        document["decision"], document["line"] = chosen["decision"], named(chosen)
        if chosen["decision"] == "use":
            document["advice"] = f"Use the code of {chosen['ref']}: it is probably better on cases no branch was made for."
        return str(document["decision"]), True
    if any(c["decision"] in ("keep", "worse") for c in candidates):
        document["decision"] = "keep"
        document["line"] = (f"None of the {branches} is shown to beat the starting point." if count != 1
                            else f"{candidates[0]['ref']} is not shown to beat the starting point.")
        if all(c.get("same") for c in candidates) and (count == 1 or not any(c.get("today") for c in candidates)):
            # No run of any branch differed from today's: the headline says that, not "not shown to beat".
            document["line"] = (candidates[0]["line"] if count == 1 else
                                f"No difference in any run: every run of each of the {count} branches gave exactly today's "
                                "result, so the study may not exercise their changes: "
                                f"{_NOT_REACHED.get(what, 'the changes may not reach the program')}.")
        if chosen is not None and chosen.get("same") and not all(c.get("same") for c in candidates):
            # The chosen branch changed nothing in any run, the others did: its advice is not the run's.
            document["advice"] = "Keep today's code for now: the gain is not proven."
        return "keep", False
    return str(document["decision"]), True


def _best_of(job: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The run's best combination without the status document: the one the
    final check checked, else the search's best, else the best ranked."""
    from evolvekit.leaderboard import rank

    final, search = job.get("final") or {}, job.get("search") or {}
    best_id = final.get("candidate") or search.get("best_id")
    if not best_id:
        ranked = rank(rows, 1)
        best_id = ranked[0].get("id") if ranked else None
    return next((r for r in rows if r.get("id") == best_id), None) if best_id else None


def best_so_far(run_dir: Path, goal: dict[str, Any], rows: list[dict[str, Any]] | None = None,
                job: dict[str, Any] | None = None) -> dict[str, Any]:
    """The search's best so far as one figure, `{"id", "pct"}`: by how many
    percent the best beats the starting point on the cases the search learns
    from, in the base and the sign of the verdict's `learned_from.pct` -- which
    is this figure once the run is done. `study status`, the status document
    (`jobs.status`'s `best.pct`) and the app's status strip all take it from
    here. While the run is live the best is the search's ranking's first (the
    one the final check will check), else the one the run checked. The figure
    comes from the best's own row in `runs.jsonl`, written once its
    evaluation finished on every case, so it never moves while the best stays
    the same. `pct` is None before there is a starting point and a best to
    compare; 0.0 while the starting point is the best. `goal` is `jobs._goal`'s;
    `rows` and `job` are the run's when the caller read them already."""
    from evolvekit.app import jobs
    from evolvekit.leaderboard import rank

    rows = jobs._rows(run_dir) if rows is None else rows
    job = jobs.phase(run_dir) if job is None else job
    seed = next((r for r in rows if r.get("operator") == "human-seed"), None)
    if job.get("phase") in jobs.ACTIVE:
        ranked = rank(rows, 1)
        best = ranked[0] if ranked else None
    else:
        best = _best_of(job, rows)
    return {"id": (best or {}).get("id"), "pct": _learned_pct(seed, best, goal)}


def _learned_pct(seed: dict[str, Any] | None, best: dict[str, Any] | None, goal: dict[str, Any]) -> float | None:
    """By how many percent the best beat the starting point on the cases the search learned from."""
    from evolvekit.app import jobs

    def value(row: dict[str, Any] | None) -> Any:
        measured = (row or {}).get("kpis") or {}
        return measured.get(f"{goal['kpi']}_raw", measured.get(goal["kpi"]))

    if seed is None or best is None or not goal.get("kpi"):
        return None
    return jobs._improvement(value(seed), value(best), goal.get("direction") or "lower")


def build_verdict(root: Path, run: str | None = None) -> dict[str, Any]:
    """The verdict document of `run` (the latest run when None): see the
    module's docstring for its keys. Reads files only."""
    from evolvekit.app import AppError, jobs
    from evolvekit.harness.execute import run_study_of
    from evolvekit.harness.manifest import load_harness

    from evolvekit.harness.execute import is_per_group

    root = Path(root)
    run = run or jobs.latest_run(root)
    if run is None:
        raise AppError("the study has no run yet", 404)
    run_dir = jobs._run_dir(root, run)
    study, harness = run_study_of(root, run_dir), load_harness(root / "harness")
    job = jobs.phase(run_dir)
    if is_per_group(run_dir, job):
        return _per_group_verdict(root, run_dir, study, harness, job)
    return _verdict_at(root, run_dir, study, harness, job)


def _verdict_at(root: Path, run_dir: Path, study: Any, harness: Any, job: dict[str, Any]) -> dict[str, Any]:
    """The verdict document of the run in `run_dir` -- a run folder, or one
    group's folder of a per-group run -- with its pinned `study` and `job`."""
    from evolvekit.app import jobs

    goal = jobs._goal(study, harness)
    rows = jobs._rows(run_dir)
    seed, best = jobs._seed_and_best(run_dir, {}, rows)[0], _best_of(job, rows)
    final = job.get("final") or {}
    comparison, check_runs = _check_of(run_dir, final)
    baseline = jobs._baseline(study, harness, seed)
    words = _final_words(job, comparison, goal, jobs._what(study), baseline)
    candidate = str(final.get("candidate") or "")
    held = _by_case(run_dir, goal, "", "", candidate, check_runs, learned=False)["held"]
    return _document(root, run_dir, study, harness, job, words, comparison, held,
                     _changed(root, study, harness, seed, best), _run_facts(run_dir, job), _learned_pct(seed, best, goal),
                     baseline=baseline, check_runs=check_runs)


# ---------------------------------------------------------------------------
# a run per kind of case
# ---------------------------------------------------------------------------

PER_GROUP_RULE = (RULE_WORDS + " Per kind of case, each group's settings are used only when every group checked on "
                  "two or more held-back cases says use.")
"""The top-level rule of a per-group run (design §6.4), after each group's own."""
PER_GROUP_WORDS = {
    "use": ("Per kind of case: each group checked on two or more held-back cases is probably better.",
            "Use each group's settings: settings_by_group.json has them, rule.md says which cases get which."),
    "single": ("Per kind of case: no group has two or more held-back cases, so none is proven.",
               "Keep today's settings: a group needs two or more held-back cases to tell; a study that holds back "
               "more cases of each kind can."),
    "keep": ("Per kind of case: not every group is shown to beat today's settings ({name}: {decision}).",
             "Keep today's settings, or use only the groups whose own line says use."),
    "pending": ("Per kind of case: the final check of {name} has not finished.",
                "Keep today's settings for now: not every group has its answer yet."),
    "stopped": ("Per kind of case: the final check of {name} has not finished.",
                "Keep today's settings for now: the run stopped before every group had its answer."),
}
PER_GROUP_NONE_BEGUN = {
    "stopped": ("Per kind of case: the run stopped before any group began.",
                "Keep today's settings: no group has an answer."),
    "pending": ("Per kind of case: no group has begun yet.",
                "Keep today's settings for now: no group has its answer yet."),
}
"""A per-group run with no group of its own yet (stopped before it began, or still starting)."""


def _group_held(doc: dict[str, Any]) -> int:
    return int((doc.get("held_back") or {}).get("cases") or 0)


def per_group_decision(order: list[str], docs: dict[str, dict[str, Any]], phase: Any = None) -> tuple[str, str, str]:
    """`(decision, line, advice)` of a per-group run from its groups' own
    documents (`docs`, in the run's `order`): a group with no answer yet
    (`pending`, `stopped`) says the run has none; else `use` only when every
    group checked on two or more held-back cases says `use` (and there is
    one); `single` when no group has two; else `keep`, naming the first group
    that does not say `use`. The verdict is the only claim: these words say
    only what the groups' own verdicts say."""
    if not order:  # no group began: the run has no answer, whatever groups.json says
        decision = "stopped" if phase in ("stopped", "failed") else "pending"
        return (decision, *PER_GROUP_NONE_BEGUN[decision])
    waiting = next((name for name in order if docs[name].get("decision") in ("pending", "stopped")), None)
    if waiting is not None:
        decision = "stopped" if phase == "stopped" else str(docs[waiting]["decision"])
        name, said = waiting, decision
    else:
        checked = [name for name in order if _group_held(docs[name]) >= 2]
        against = next((name for name in checked if docs[name].get("decision") != "use"), None)
        decision = "single" if not checked else "keep" if against is not None else "use"
        name, said = against or "", str(docs[against]["decision"]) if against else ""
    line, advice = PER_GROUP_WORDS[decision]
    return decision, line.format(name=name, decision=said), advice


def _not_begun(root: Path, run_dir: Path, name: str, part: dict[str, Any], measure: dict[str, Any]) -> dict[str, Any]:
    """The document of a group whose search has not begun: no answer yet."""
    return {"schema": SCHEMA, "study": root.name, "run": run_dir.name, "group": name, "phase": None, "decision": "pending",
            "line": "Its search has not begun.", "advice": "", "measure": measure,
            "held_back": {"cases": len(part.get("test") or []), "runs_per_case": None},
            "learned_from": {"cases": len(part.get("training") or []), "pct": None}, "rule": RULE_WORDS,
            "by_case": [], "changed": [], "files": []}


def _per_group_verdict(root: Path, run_dir: Path, study: Any, harness: Any, job: dict[str, Any]) -> dict[str, Any]:
    """The verdict document of a per-group run: each group's own document
    (`_verdict_at` on its folder and pinned study) under `groups`, and the
    run's decision and words from them (`per_group_decision`), its held-back
    cases (all of them), every group's held-back rows (`by_case`, each with
    its `group`), the rule's sentences (`rule_lines`, as `rule.md` says them),
    the run's files and times and, with AI, what every group spent together.
    No figures of its own: each group's are its own."""
    from evolvekit.app import jobs
    from evolvekit.harness.compile import ai_cap_usd
    from evolvekit.harness.execute import _run_groups, group_dirs, run_study_of
    from evolvekit.harness.groups import rule_lines

    run = run_dir.name
    grouped = _run_groups(root, run_dir)
    dirs = group_dirs(run_dir)
    # The run's own groups (`job.json`'s); `groups.json` names them only for a run made before it said so.
    order = list(dirs) if job.get("per_group") is not None else list(grouped.get("groups") or {})
    goal = jobs._goal(study, harness)
    measure = _measure_of(study, harness, goal["kpi"], goal["direction"])
    docs: dict[str, dict[str, Any]] = {}
    for name in order:
        folder = dirs.get(name)
        part = (grouped.get("groups") or {}).get(name) or {}
        if folder is None or not (folder / "job.json").is_file():
            docs[name] = _not_begun(root, run_dir, name, part, measure)
            continue
        state = jobs.phase(folder)
        own = _verdict_at(root, folder, run_study_of(root, folder), harness, state)
        own["run"], own["group"] = run, name
        if state.get("phase") in jobs.ACTIVE:  # no answer yet: the group's line says what it is doing
            own["line"] = "Its final check is going." if state.get("phase") == "check" else "Its search is going."
            own["advice"] = ""
        docs[name] = own
    decision, line, advice = per_group_decision(order, docs, job.get("phase"))
    runs_per_case = {(d.get("held_back") or {}).get("runs_per_case") for d in docs.values()} - {None}
    document: dict[str, Any] = {
        "schema": SCHEMA, "study": root.name, "run": run, "phase": job.get("phase"), "decision": decision,
        "line": line, "advice": advice,
        "measure": next((d["measure"] for d in docs.values() if d.get("measure")), measure),
        "held_back": {"cases": len(study.test), "runs_per_case": runs_per_case.pop() if len(runs_per_case) == 1 else None},
        "rule": PER_GROUP_RULE, "groups": docs,
        # Said once the run is done: no rule before every group has its answer (and the split is cached then).
        "rule_lines": (rule_lines(grouped, {name: str(d.get("decision") or "") for name, d in docs.items()})
                       if grouped and order and job.get("phase") == "done" else []),
        "by_case": [{**row, "group": name} for name, d in docs.items() for row in d.get("by_case") or []],
        "files": _files(root, run_dir),
        "started_at": job.get("started_at"), "finished_at": job.get("finished_at"),
    }
    if getattr(study.budget, "ai_enabled", False):
        spent = [d["spent"] for d in docs.values() if d.get("spent")]
        if any("requests" in s for s in spent):
            requests = sum(float(s.get("requests") or 0) for s in spent)
            document["spent"] = {"requests": requests, "line": f"AI: {requests:g} request{'s' if requests != 1 else ''}"}
        else:
            usd, cap = sum(float(s.get("usd") or 0.0) for s in spent), ai_cap_usd(study)
            document["spent"] = {"usd": usd, "cap_usd": cap, "line": f"AI: ${usd:.2f} of ${cap:.2f}"}
    return _clean(document)
