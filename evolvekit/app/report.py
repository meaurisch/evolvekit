"""The report of a run, written by code from the verdict document.

Only the verdict's own sentences claim anything: the report quotes `line` and
`advice` and states the other numbers in the direction of the goal ("1.3 %
lower"), never as signed percentages and never with a word of its own about
improvement. It generates no sentence of interpretation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from evolvekit.app.verdict import number_text


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
    """A 95 % range in the words of the goal's direction; a bound that rounds to zero has no sign."""
    toward, away = _lower(direction)
    if low > 0:
        return f"between {number_text(low)} % and {number_text(high)} % {toward}"
    if high < 0:
        return f"between {number_text(-high)} % and {number_text(-low)} % {away}"
    return f"between {number_text(-low)} % {away} and {number_text(high)} % {toward}"


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


_NOTHING = ("No cases were checked.", "Nothing changed.", "No files yet.")
"""What a section says when it has nothing to say: a PR's body leaves such a section out."""


def _without_empty(lines: list[str]) -> list[str]:
    """`lines` without the `## ` sections whose only text is one of `_NOTHING`."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("## "):
            end = next((j for j in range(i + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
            body = [line for line in lines[i + 1:end] if line.strip()]
            if len(body) == 1 and body[0] in _NOTHING:
                while out and not out[-1].strip():
                    out.pop()  # the blank line before the section goes with it
                i = end
                continue
        out.append(lines[i])
        i += 1
    return out


def render_report(verdict: dict[str, Any], results: dict[str, Any] | None = None,
                  root: Path | str | None = None, *, notes: list[str] | tuple[str, ...] = (),
                  drop_empty: bool = False) -> str:
    """The report as Markdown: the verdict, the held-back cases, what changed,
    how it ran, the rule and the files. `results` (the page's document) adds
    the study's name and the application; `root`, the study folder, says
    where the files' paths start. `notes` follow the verdict's own sentences,
    each "Note: …" (a PR's body: a thin final check, `plan.check_notes`);
    `drop_empty` leaves out a section that would only say there is nothing
    (a PR's body)."""
    results = results or {}
    measure = verdict.get("measure") or {}
    direction = measure.get("direction") or "lower"
    name, application = results.get("study") or verdict.get("study"), results.get("harness")
    out = [f"# {name}" + (f" - {application}" if application else ""), "", "## Verdict", "", verdict.get("line", "")]
    if verdict.get("advice"):
        out += ["", verdict["advice"]]
    for note in notes:
        out += ["", f"Note: {note}"]
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

    candidates = verdict.get("candidates")
    if candidates:
        # A bake-off: each branch against the starting point, in its own verdict's words.
        out += ["", "## Branches compared", ""]
        # Each held-back case's change for each branch: the paired mean of its runs, as the verdict gives it.
        cases = list(dict.fromkeys(r.get("name") for c in candidates for r in c.get("by_case") or [] if r.get("name")))

        def per_case(c: dict[str, Any]) -> list[str]:
            rows = {r.get("name"): r for r in c.get("by_case") or []}
            return [_change(rows[case].get("change_pct"), direction) if case in rows else "-" for case in cases]

        out += _table(["Branch", "Commit", "Verdict", *cases],
                      [[c.get("ref") or "", str(c.get("commit") or "")[:7], c.get("line") or "", *per_case(c)]
                       for c in candidates])

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
    elif candidates and (verdict.get("held_back") or {}).get("runs_per_case"):
        # A bake-off with no branch shown ahead: each branch's figures are its own row above.
        out.append("No branch speaks for the run: each branch's result is under Branches compared.")
    else:
        out.append("No cases were checked.")

    out += ["", "## What changed (recommended)" if verdict.get("decision") == "use"
            else "## What changed (found, not recommended)", ""]
    changed = verdict.get("changed") or []
    if changed:
        kind = "Code change" if any(c.get("name") == "code" for c in changed) else "Setting or data change"
        out += _table([kind, "From", "To"], [[c["label"], c["from"], c["to"]] for c in changed])
    elif candidates and verdict.get("decision") == "keep":
        out.append("Today's code stays; each branch's change is its own commit, named above.")
    else:
        out.append("Nothing changed.")

    learned, held = verdict.get("learned_from") or {}, verdict.get("held_back") or {}
    ran = [f"Cases learned from: {learned['cases']}" + (f" ({_change(learned['pct'], direction)} on them)" if learned.get("pct") is not None else "")
           ] if learned.get("cases") is not None else []
    tried = verdict.get("combinations")
    if isinstance(tried, int) and tried >= 1:
        # `combinations` counts the starting point too: say the plan's number, and the start beside it.
        tried = f"{tried - 1}, plus today's settings"
    rounds = verdict.get("rounds")
    if verdict.get("how_it_ran"):
        # Fewer rounds ran than the plan said: the sentence says how many of how many, and why.
        ran.append(verdict["how_it_ran"].rstrip("."))
        rounds = tried = None
    if candidates:
        # No search, so nothing was learned from: the branches are the candidates, and the more there are, the
        # likelier one looks ahead by chance.
        count = len(candidates)
        ran = [f"{count} branch{'es were' if count != 1 else ' was'} compared; the more are compared, "
               "the likelier one looks ahead by chance."]
        rounds = tried = None
    if (verdict.get("spent") or {}).get("line"):
        ran.append(verdict["spent"]["line"])  # what the AI spent: a fact, in the verdict's own words
    for label, value in (("Rounds", rounds), ("Combinations tried", tried),
                         ("Time", _duration(verdict.get("elapsed_s"))), ("Started", _when(verdict.get("started_at"))),
                         ("Held-back cases", held.get("cases")),
                         ("Runs per case in the final check", held.get("runs_per_case"))):
        if value not in (None, ""):
            ran.append(f"{label}: {value}")
    out += ["", "## How it ran", ""] + [f"- {line}" for line in ran]

    # The paths stay relative to the study folder; the section's first line says which folder that is.
    out += ["", "## Files" + (f", in the study folder {Path(root).resolve()}" if root is not None else ""), ""]
    files = verdict.get("files") or []
    out += [f"- {path}" for path in files] if files else ["No files yet."]
    if drop_empty:
        out = _without_empty(out)
    return "\n".join(out) + "\n"
