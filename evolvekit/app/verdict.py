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
round for 2 rounds gives 5. The page (`jobs.results`), the
command line and the report all render from it, so they cannot disagree.

A bake-off's document (a run of `study bakeoff`) also holds `candidates`:
`[{"ref", "commit", "decision", "line", "mean_pct", "ci95_pct", "worst_pct",
"chance"}]`, one per branch in the order given, each against the starting
point by the same rule (numbers absent while there is no answer). Its own
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

from evolvekit.confirm import BASELINE, RULE_WORDS, USE_CHANCE, decide, t_cdf

__all__ = ["DECISIONS", "SCHEMA", "build_verdict"]

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
DOWNLOADS = "downloads"
VALUES = "values.json"
"""The values the exports in `downloads/` were written from: an input, not an export."""


def _pct(value: float | None, digits: int = 1) -> str:
    return "" if value is None else f"{abs(value):.{digits}f} %"


def _better_word(direction: str) -> str:
    return "lower" if direction == "lower" else "higher"


def _span(low: float, high: float) -> str:
    """A 95 % interval of an improvement in percent, in words."""
    if low > 0:
        return f"likely between {low:.1f} % and {high:.1f} % better"
    if high < 0:
        return f"likely between {-high:.1f} % and {-low:.1f} % worse"
    return f"somewhere between {-low:.1f} % worse and {high:.1f} % better"


def _chance_words(p: float | None) -> str:
    """A chance of a gain in twentieths: "about 19 in 20", with "almost
    certain" and "1 in 20 or less" at the ends; nothing for no chance."""
    if p is None:
        return ""
    if p >= 0.995:
        return "almost certain"
    if p <= 0.05:
        return "1 in 20 or less"
    if p < USE_CHANCE and round(p * 20) >= round(USE_CHANCE * 20):
        # 0.897 would round to "about 18 in 20", which reads like the rule's
        # 9 in 10 when the rule said no: say which side of it the chance is.
        return f"under {round(USE_CHANCE * 10)} in 10"
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
    return jobs._read(path), jobs._read_list(path.parent / "results.json")


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
    return {"unclear": "keep", "missing": "pending", "stopped": "stopped", "single": "single",
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
              facts: dict[str, Any], learned_pct: float | None, *, baseline: str = "") -> dict[str, Any]:
    """The verdict document of a run from what the page computes too:
    `words` (`_final_words`), the held-back rows of `_by_case`, `_changed`
    and `_run_facts`. A bake-off's document also lists its `candidates`
    (`baseline` is the starting point in words, for their sentences)."""
    from evolvekit.app import jobs

    final = job.get("final") or {}
    goal = jobs._goal(study, harness)
    decision = _decision_word(words, final)
    document: dict[str, Any] = {"schema": SCHEMA, "study": root.name, "run": run_dir.name, "phase": job.get("phase"),
                                "decision": decision}
    if decision in ("skipped", "none"):
        document["reason"] = str(final.get("skipped") or "")
    document["line"], document["advice"] = words.get("line", ""), words.get("advice", "")
    candidates = (_candidates(job, comparison, goal, jobs._what(study), baseline)
                  if job.get("bakeoff") else None)
    named = True
    if candidates:
        decision, named = _bakeoff_headline(document, job, candidates)
    candidate = final.get("candidate") or next(iter((comparison or {}).get("per_candidate") or {}), None)
    result = ((comparison or {}).get("per_candidate") or {}).get(candidate) or {}
    levels = _levels(study, harness, result["levels"]) if isinstance(result.get("levels"), dict) else []
    deciding = next((level for level in levels if level["deciding"]), levels[0] if levels else None)
    document["measure"] = ({k: deciding[k] for k in ("kpi", "label", "unit", "direction")} if deciding else
                           _measure_of(study, harness, goal["kpi"], goal["direction"]))
    seeds = len((comparison or {}).get("seeds") or []) or final.get("seeds") or None
    document["held_back"] = {"cases": len(study.test), "runs_per_case": seeds}
    if decision not in _NO_ANSWER and named:
        document.update({"mean_pct": words.get("mean"), "ci95_pct": words.get("ci95"), "worst_pct": words.get("worst_pct"),
                         "chance": words.get("chance"), "chance_words": words.get("chance_words", "")})
    if levels and named:
        document["levels"] = levels
    document["learned_from"] = {"cases": len(study.training), "pct": learned_pct}
    document["rule"] = RULE_WORDS
    # A case's change is the comparison's own: the paired mean over the final check's runs, the
    # statistic the line is made of -- not the change of the means, which can differ by a few tenths.
    paired = {} if "levels" in result else {
        str(r.get("instance")): r["improvement_pct"] for r in result.get("per_instance") or []
        if isinstance(r, dict) and _number(r.get("improvement_pct")) is not None}
    held = [{**row, "change_pct": paired.get(row["case"], row["change_pct"])} for row in held]
    document["by_case"] = [{"name": row["case"], "start": row["start"], "best": row["best"],
                            "change_pct": row["change_pct"], "outcome": _outcome(row)} for row in held]
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
    if candidates is not None:
        document["candidates"] = candidates
    return _clean(document)


def _candidates(job: dict[str, Any], comparison: dict[str, Any] | None, goal: dict[str, Any], what: str,
                baseline: str) -> list[dict[str, Any]]:
    """A bake-off's branches in the order they were given, each with its own
    decision and line against the starting point: `_final_words` as if the
    final check had checked that one alone."""
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
        if decision not in _NO_ANSWER:
            row.update({"mean_pct": words.get("mean"), "ci95_pct": words.get("ci95"),
                        "worst_pct": words.get("worst_pct"), "chance": words.get("chance")})
        out.append(row)
    return out


def _bakeoff_headline(document: dict[str, Any], job: dict[str, Any],
                      candidates: list[dict[str, Any]]) -> tuple[str, bool]:
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


def _learned_pct(seed: dict[str, Any] | None, best: dict[str, Any] | None, goal: dict[str, Any]) -> float | None:
    """By how many percent the best beat the starting point on the cases the search learned from."""
    from evolvekit.app import jobs

    def value(row: dict[str, Any] | None) -> Any:
        measured = (row or {}).get("kpis") or {}
        return measured.get(f"{goal['kpi']}_raw", measured.get(goal["kpi"]))

    if seed is None or best is None or not goal["kpi"]:
        return None
    return jobs._improvement(value(seed), value(best), goal["direction"])


def build_verdict(root: Path, run: str | None = None) -> dict[str, Any]:
    """The verdict document of `run` (the latest run when None): see the
    module's docstring for its keys. Reads files only."""
    from evolvekit.app import AppError, jobs
    from evolvekit.harness.execute import run_study_of
    from evolvekit.harness.manifest import load_harness

    root = Path(root)
    run = run or jobs.latest_run(root)
    if run is None:
        raise AppError("the study has no run yet", 404)
    run_dir = jobs._run_dir(root, run)
    study, harness = run_study_of(root, run_dir), load_harness(root / "harness")
    job = jobs.phase(run_dir)
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
                     baseline=baseline)
