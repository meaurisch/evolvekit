"""The report of a run, written by code from the verdict document.

Only the verdict's own sentences claim anything: the report quotes `line` and
`advice` and states the other numbers in the direction of the goal ("1.3 %
lower"), never as signed percentages and never with a word of its own about
improvement. It generates no sentence of interpretation."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _number(value: Any) -> str:
    """A measure value as people write it: 1,248,920 and 12.35, never 1.2e+06."""
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) >= 1000 or number == int(number):
        return f"{number:,.0f}"
    text = f"{number:.4g}"
    return f"{number:.6f}".rstrip("0") if "e" in text else text


def _lower(direction: str) -> tuple[str, str]:
    """The words for a change that moves with the goal, and against it."""
    return ("lower", "higher") if direction == "lower" else ("higher", "lower")


def _change(pct: Any, direction: str) -> str:
    """A change in percent of the starting point, in the words of the goal's direction."""
    if pct is None:
        return "-"
    if abs(pct) < 0.05:
        return "the same"
    toward, away = _lower(direction)
    return f"{abs(pct):.1f} % {toward if pct > 0 else away}"


def _between(low: float, high: float, direction: str) -> str:
    toward, away = _lower(direction)
    if low > 0:
        return f"between {low:.1f} % and {high:.1f} % {toward}"
    if high < 0:
        return f"between {-high:.1f} % and {-low:.1f} % {away}"
    return f"between {-low:.1f} % {away} and {high:.1f} % {toward}"


def _duration(seconds: Any) -> str:
    if seconds is None:
        return ""
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{round(seconds)} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    hours, minutes = divmod(round(seconds / 60), 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def _when(stamp: Any) -> str:
    try:
        return datetime.fromisoformat(str(stamp)).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ""


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(head: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return lines


_OUTCOME = {"better": "toward the goal", "worse": "away from the goal", "same": "the same", "failed": "did not finish"}


def render_report(verdict: dict[str, Any], results: dict[str, Any] | None = None) -> str:
    """The report as Markdown: the verdict, the held-back cases, what changed,
    how it ran, the rule and the files. `results` (the page's document) adds
    the study's name and the application."""
    results = results or {}
    measure = verdict.get("measure") or {}
    direction = measure.get("direction") or "lower"
    name, application = results.get("study") or verdict.get("study"), results.get("harness")
    out = [f"# {name}" + (f" - {application}" if application else ""), "", "## Verdict", "", verdict.get("line", "")]
    if verdict.get("advice"):
        out += ["", verdict["advice"]]
    facts = []
    if verdict.get("ci95_pct"):
        low, high = verdict["ci95_pct"]
        facts.append(f"Change in {measure.get('label') or 'the measure'} on the held-back cases: "
                     f"{_change(verdict.get('mean_pct'), direction)}, {_between(low, high, direction)} (95 % interval).")
    if verdict.get("chance_words"):
        facts.append(f"Chance of a gain: {verdict['chance_words']}.")
    reason = verdict.get("reason") or ""
    if reason and reason.lower() not in verdict.get("line", "").lower():
        facts.append(reason[0].upper() + reason[1:] + ".")
    if facts:
        out += [""] + [f"- {fact}" for fact in facts]
    if verdict.get("rule"):
        out += ["", f"The rule: {verdict['rule']}"]

    out += ["", "## Held-back cases", ""]
    rows = verdict.get("by_case") or []
    if rows:
        # Start and best are means over the final check's runs; the change is the paired mean of the
        # runs' changes -- the verdict's own figure -- so it need not be the change of the two means.
        runs = (verdict.get("held_back") or {}).get("runs_per_case")
        of = f" (mean of {runs} run{'s' if runs != 1 else ''})" if runs else " (mean)"
        out += _table(["Case", f"Start{of}", f"Best{of}",
                       f"Change in {measure.get('label') or 'the measure'} (mean of the paired runs)", "Outcome"],
                      [[r["name"], _number(r.get("start")), _number(r.get("best")), _change(r.get("change_pct"), direction),
                        _OUTCOME.get(r.get("outcome"), r.get("outcome") or "")] for r in rows])
    else:
        out.append("No cases were checked.")

    out += ["", "## What changed (recommended)" if verdict.get("decision") == "use"
            else "## What changed (found, not recommended)", ""]
    changed = verdict.get("changed") or []
    if changed:
        out += _table(["Setting or data change", "From", "To"], [[c["label"], c["from"], c["to"]] for c in changed])
    else:
        out.append("Nothing changed.")

    learned, held = verdict.get("learned_from") or {}, verdict.get("held_back") or {}
    ran = [f"Cases learned from: {learned['cases']}" + (f" ({_change(learned['pct'], direction)} on them)" if learned.get("pct") is not None else "")
           ] if learned.get("cases") is not None else []
    tried = verdict.get("combinations")
    if isinstance(tried, int) and tried >= 1:
        # `combinations` counts the starting point too: say the plan's number, and the start beside it.
        tried = f"{tried - 1}, plus today's settings"
    for label, value in (("Rounds", verdict.get("rounds")), ("Combinations tried", tried),
                         ("Time", _duration(verdict.get("elapsed_s"))), ("Started", _when(verdict.get("started_at"))),
                         ("Held-back cases", held.get("cases")),
                         ("Runs per case in the final check", held.get("runs_per_case"))):
        if value not in (None, ""):
            ran.append(f"{label}: {value}")
    out += ["", "## How it ran", ""] + [f"- {line}" for line in ran]

    out += ["", "## Files", ""]
    files = verdict.get("files") or []
    out += [f"- {path}" for path in files] if files else ["No files yet."]
    return "\n".join(out) + "\n"
