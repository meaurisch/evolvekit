"""The assistant: the consultant says in plain words what they want; the
assistant proposes parts of the study as cards -- settings to tune, data
changes, constraints, KPIs, weighted sums, goals, guardrails -- and every card
is checked here, on the preview's tables, before anybody sees it.

What goes to the model: the tables' declared schema (names, types, units,
descriptions), the harness's catalogues, the study so far, and summaries of the
preview case -- row counts, min / mean / max of numeric columns, and at most 50
distinct values of the columns the harness declares categorical. Never a row,
a name, an address or a coordinate.

A card that fails its check goes back to the model with the reason, at most
twice; after that it says honestly that it could not be expressed. Every
answer's cost is kept in `assistant.jsonl` in the study folder: what the
provider reported, else what the price table says, else `None` -- the page
then says the cost is not known rather than showing a made-up figure.

`assistant.jsonl` holds one JSON object per line, each with an `id`:

    {"role": "user", "id": <turn>, "text": ...}
    {"role": "assistant", "turn": <turn>, "say": ..., "raw_cards": [...], "cards": [...], "usd": ...}
    {"role": "assistant", "turn": <turn>, "cancelled": true, "usd": ...}   the consultant cancelled it
    {"role": "assistant", "turn": <turn>, "failed": "why"}                 the model gave no answer
    {"role": "card", "turn": <turn>, "index": n, "state": "applied" | "dismissed"}

A turn is a question and its answer, named by the question's id. The card
events are folded into the answer's cards as their `state` (`proposed`,
`applied` or `dismissed`, the last event winning) by `history`, and the
model is shown which of its proposals were applied when the turn is replayed.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evolvekit.app import AppError
from evolvekit.app import jobs, work
from evolvekit.app.prices import price_of
from evolvekit.app.store import as_json
from evolvekit.config import ModelConfig
from evolvekit.expressions import Expression, ExpressionError
from evolvekit.harness import HarnessError
from evolvekit.harness.compile import base_settings
from evolvekit.harness.manifest import Harness, load_harness
from evolvekit.harness.study import Constraint, DataChange, GoalLevel, Guardrail, SettingChoice, Study, StudyKpi, load_study, parse_within, save_study

__all__ = ["apply_card", "apply_cards", "ask", "history", "record_card"]

HISTORY = "assistant.jsonl"
CARD_STATES = ("proposed", "applied", "dismissed")
APPLY_ORDER = ("kpi", "weighted", "setting", "data_change", "constraint", "goal", "guardrail")
"""The order a whole answer goes into the study: measures first, so a goal,
a guardrail or a combination may name one proposed in the same answer; the
things tuned before the rules that hold them."""
MAX_CORRECTIONS = 2
MAX_TOKENS = 16000
"""The model thinks before it answers, and its thinking counts against this:
the data-study review saw every answer spent on thinking at 3000 (empty,
finish_reason=length). An answer is some 500 tokens; the thinking up to 4000."""
TURNS = 6
KINDS = ("setting", "data_change", "constraint", "kpi", "weighted", "goal", "guardrail")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
JSON_MODE = ("openrouter", "azure")
"""The providers whose API takes `response_format={"type": "json_object"}`;
`_parse` stays the fallback for the others, and for a model that ignores it."""
STATES = ("missing", "not_signed_in", "policy_blocked", "failed")
"""Why a CLI provider gave no answer (`CopilotCliError.kind`): `check_model`
passes it on as `state`, so the page can say what to do."""

SYSTEM = """\
You help a consultant set up a study in evolvekit's app. A study tunes an application's settings, or
the data it is given, and judges the result with measures (KPIs). The consultant is not a programmer:
keep every sentence plain, say "measure" rather than "KPI", and remember that your cards are
proposals the consultant applies or dismisses -- say "I propose", never "I added" or "I changed".

Answer with ONE JSON object and nothing else:
{"say": "one to three plain sentences", "cards": [ ... ]}

Cards, one per proposal (every card has "says": one plain sentence saying EXACTLY what is counted or
changed):
- {"kind": "setting", "name": <a setting>, "mode": "tune" | "fixed" | "default", "low": n, "high": n,
   "start": n, "value": v, "says": "..."}                      (low/high/start optional; value for fixed)
- {"kind": "data_change", "name": <new name>, "lever": <a lever>, "column": <one of its columns>,
   "where": <SQL condition over the lever's table, or "">, "mode": <one of its modes>, "low": n,
   "high": n, "start": n, "says": "..."}
- {"kind": "constraint", "says": "...", "expr": "<expression over tuned names>"} or
  {"kind": "constraint", "says": "...", "sql": "<SELECT returning 1 when it holds>"}
- {"kind": "kpi", "name": <new name>, "says": "...", "direction": "lower" | "higher", "unit": "...",
   "sql": "<SELECT returning one number>", "rows_sql": "<SELECT listing the rows it counts>"}
- {"kind": "weighted", "name": <new name>, "says": "...", "weighted": {<kpi>: weight, ...}}
- {"kind": "goal", "levels": [{"kpi": <kpi>, "direction": "lower" | "higher", "equal_within": "1 %"}, ...]}
- {"kind": "guardrail", "kpi": <kpi>, "max": n} or {"kind": "guardrail", "kpi": <kpi>, "min": n}

Rules:
- SQL is SQLite and read-only. A KPI returns one number (first column of the first row; NULL counts
  as 0). Use only the tables and columns listed below; `orig_<table>` holds the untouched request.
- Prefer what is valid by construction: tune a premium >= 0 instead of two numbers that must stay
  ordered; scale within a range instead of a constraint on it. Otherwise an expression over tuned
  names ("truck_km >= van_km"); SQL constraints only for rules that depend on the data.
- A data change that alters what the solver sees (a lever over costs, weights or prizes) makes every
  KPI marked "changes_with_levers" meaningless: judge such a study by a KPI of the untouched request.
- Names: lower case letters, digits and underscores; new names must not clash with existing ones.
- A KPI with a "guard" does not count broken rules: when you propose it as the goal, also propose
  its guard as a guardrail, unless the study has it already.
- A goal has at most four levels in order of importance; every level but the last needs
  "equal_within" ("1 %" relative to the starting point, or a number in the KPI's unit).
- When the request cannot be expressed with these tables, say so and return no cards.
- When the consultant asks a question rather than for a change, answer it in "say" in two to five
  plain sentences and return no cards. Explain a verdict with its numbers: the chance, the worst case,
  which cases went which way.
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id() -> str:
    """A short random token naming an entry of the history."""
    return secrets.token_urlsafe(6)


def _append(root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    entry = {"ts": _now(), "id": _id(), **entry}
    with open(root / HISTORY, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, allow_nan=False) + "\n")
    return entry


def _entries(root: Path) -> list[dict[str, Any]]:
    path = root / HISTORY
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            loaded = json.loads(line)
        except ValueError:
            continue
        if isinstance(loaded, dict):
            entries.append(loaded)
    return entries


def _folded(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The turns in question order, each question followed by its answer --
    matched by the turn's id, or, for a history from before ids, by
    adjacency -- with the card events folded into the answers' cards as
    their `state`. The events themselves are not listed; an answer whose
    question is gone is kept where it is."""
    by_turn = {e["turn"]: e for e in entries if e.get("role") == "assistant" and e.get("turn")}
    states: dict[str, dict[int, str]] = {}
    for event in entries:
        if event.get("role") == "card" and isinstance(event.get("index"), int):
            states.setdefault(str(event.get("turn")), {})[event["index"]] = str(event.get("state"))
    folded: list[dict[str, Any]] = []
    placed: set[int] = set()

    def answer(entry: dict[str, Any]) -> dict[str, Any]:
        placed.add(id(entry))
        if "cards" not in entry:
            return entry
        fate = states.get(str(entry.get("turn")), {})
        return {**entry, "cards": [{**card, "state": fate.get(i) if fate.get(i) in CARD_STATES else "proposed"}
                                   for i, card in enumerate(entry["cards"])]}

    for at, entry in enumerate(entries):
        role = entry.get("role")
        if role == "card" or id(entry) in placed:
            continue
        if role == "user":
            folded.append(entry)
            reply = by_turn.get(entry.get("id")) if entry.get("id") else None
            if reply is None and at + 1 < len(entries):
                following = entries[at + 1]
                if following.get("role") == "assistant" and not following.get("turn"):
                    reply = following
            if reply is not None and id(reply) not in placed:
                folded.append(answer(reply))
        elif role == "assistant":
            folded.append(answer(entry))
    return folded


def _answered(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation the model is shown again: each question with its
    answer. A question that got none (the model failed, or the consultant
    cancelled it) is left out, or the next answer would have to deal with
    every one of them at once."""
    turns = _folded(entries)
    kept = []
    for at, entry in enumerate(turns):
        reply = turns[at + 1] if at + 1 < len(turns) else None
        if (entry.get("role") == "user" and reply and reply.get("role") == "assistant"
                and not reply.get("failed") and not reply.get("cancelled")):
            kept += [entry, reply]
    return kept


def _replayed_cards(entry: dict[str, Any]) -> list[Any]:
    """The answer's cards as the model wrote them, each one the consultant
    applied marked `"applied": true` so the model knows what was taken."""
    states = [card.get("state") for card in entry.get("cards") or []]
    return [{**card, "applied": True} if isinstance(card, dict) and i < len(states) and states[i] == "applied" else card
            for i, card in enumerate(entry.get("raw_cards") or [])]


def record_card(root: Path, turn: str, index: Any, state: Any) -> dict[str, Any]:
    """What became of a card: `applied` or `dismissed`, kept in the history
    after the answer it belongs to."""
    if state not in ("applied", "dismissed"):
        raise AppError("state: applied or dismissed")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise AppError("index: which card of the answer, counted from 0")
    answer = next((e for e in _entries(root) if e.get("role") == "assistant" and e.get("turn") == turn), None)
    if answer is None or index >= len(answer.get("cards") or []):
        raise AppError("there is no such card in this study's conversation", 404)
    _append(root, {"role": "card", "turn": turn, "index": index, "state": state})
    return {"turn": turn, "index": index, "state": state}


def failure_words(exc: BaseException) -> str:
    """Why the model gave no answer, in words people can act on."""
    text = str(exc)
    low = text.lower()
    if getattr(exc, "kind", None) in STATES:
        return text  # the provider's own sentence already says what to do
    if "finish_reason=length" in low:
        return "The model thought for too long and gave no answer. Ask again, perhaps in fewer words."
    if "401" in low or "unauthori" in low or "invalid api key" in low or "authentication" in low:
        return "The model's service refused the API key. Check the key in Settings."
    if "402" in low or "credit" in low or "insufficient" in low:
        return "The model's service says the account has no credit left."
    if "timeout" in low or "timed out" in low or "connection" in low:
        return "The model did not answer in time. Check the internet connection and ask again."
    return f"The model did not answer: {text[:200]}"


def check_model(home: Any) -> dict[str, Any]:
    """Settings' test: one tiny question to the assistant's model.

    `{"ok": True, "model", "seconds", "usd"}`, plus `"requests"` for a
    provider that counts requests instead of dollars (`copilot-cli`); or
    `{"ok": False, "reason"}`, plus `"state"` (one of `STATES`) when the
    provider says why: not installed, not signed in, blocked by a policy."""
    import time

    settings, keys = home.settings(), home.keys()
    needed = {"openrouter": ["OPENROUTER_API_KEY"], "azure": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"]}.get(settings["provider"], [])
    if any(not keys.get(k) for k in needed):
        return {"ok": False, "reason": "There is no key for this provider yet: paste it above and save."}
    started = time.perf_counter()
    try:
        provider, config = _provider(home)
        completion = provider.complete([{"role": "user", "content": 'Answer with the JSON {"ready": true} and nothing else.'}],
                                       model=config.model, max_tokens=MAX_TOKENS, temperature=0.0)
    except Exception as exc:  # noqa: BLE001 - the test says what went wrong
        failed: dict[str, Any] = {"ok": False, "reason": failure_words(exc)}
        if getattr(exc, "kind", None) in STATES:
            failed["state"] = exc.kind  # type: ignore[attr-defined]
        return failed
    usd = _cost(completion, config)
    result = {"ok": True, "model": config.model, "seconds": round(time.perf_counter() - started, 1),
              "usd": None if usd is None else round(usd, 6)}
    if completion.requests is not None:
        result["requests"] = _count(completion.requests)
    return result


def history(root: Path) -> dict[str, Any]:
    """The turns so far, each question followed by its answer and each card
    carrying its `state` (`proposed`, `applied` or `dismissed`); `total_usd`
    sums the costs that are known, and `unknown` counts the answers whose
    cost nobody knew. `total_requests` sums the Copilot requests, when any
    answer counted them (`copilot-cli`)."""
    turns = _folded(_entries(root))
    answers = [e for e in turns if e.get("role") == "assistant"]
    out = {"turns": turns, "total_usd": round(sum(float(e["usd"]) for e in answers if e.get("usd") is not None), 6),
           "unknown": sum(1 for e in answers if "usd" in e and e["usd"] is None)}
    counted = [float(e["requests"]) for e in answers if isinstance(e.get("requests"), (int, float))]
    if counted:
        out["total_requests"] = _count(sum(counted))
    return out


# ---------------------------------------------------------------------------
# what the model is told
# ---------------------------------------------------------------------------


def context(root: Path, study: Study, harness: Harness, *, screen: str = "", run: str | None = None, home: Any = None,
            document_of: Any = None) -> str:
    """The schema, the catalogues, the study, the preview's summaries, and
    where the consultant is: the screen, and the run's words and numbers
    when a run is in view. `document_of` is the server's cached status
    document; without one the status is built afresh."""
    # The keys and settings are the provider's business, not the model's: only the plan line reads the
    # models (which provider writes the changes, so how long each takes), never a key.
    models = jobs.ai_models(home) if home is not None and study.budget.ai_enabled else None
    del home
    tables = {
        name: {"source": t.source, "describe": t.describe,
               "columns": {c: {"type": col.type, "unit": col.unit, "describe": col.describe,
                               **({"categorical": True} if col.categorical else {})}
                           for c, col in t.columns.items()}}
        for name, t in harness.tables.items()
    }
    catalogues = {
        "settings": {s.name: {"type": s.parameter.type, "low": s.parameter.low, "high": s.parameter.high,
                              "default": s.parameter.default, "choices": list(s.parameter.choices),
                              "label": s.label, "explain": s.explain or s.help,
                              **({"log": True} if s.parameter.log else {})}
                     for s in harness.settings.values()},
        "levers": {n: {"table": lv.table, "columns": dict(lv.columns), "modes": list(lv.modes), "code": lv.code,
                       "explain": lv.explain or lv.help} for n, lv in harness.levers.items()},
        "kpis": {n: {"label": k.label, "direction": k.direction, "unit": k.unit, "sql": k.sql,
                     "changes_with_levers": k.changes_with_levers, "help": k.help,
                     **({"guard": k.guard} if k.guard else {})} for n, k in harness.kpis.items()},
        "kpi_templates": {n: {"label": t.label, "sql": t.sql, "params": list(t.params)} for n, t in harness.kpi_templates.items()},
    }
    so_far = as_json(study.to_yaml())  # a schedule's `until` is a date: JSON carries it as text
    so_far.pop("application", None)  # a path on this machine says nothing about the study
    summary = work.preview_summary(root, harness)
    return (
        f"## The harness: {harness.title}\n{harness.summary}\n\n"
        f"## Tables\n{json.dumps(tables, indent=1)}\n\n"
        f"## Catalogues\n{json.dumps(catalogues, indent=1)}\n\n"
        f"## The study so far\n{json.dumps(so_far, indent=1)}\n\n"
        f"## The preview case, summarised (no rows)\n"
        + (json.dumps(summary, indent=1, default=str) if summary else "(no preview yet)")
        + "\n\n## Where the consultant is\n" + _where(root, study, harness, screen, run, document_of, models)
    )


def _status_document(run_dir: Path) -> Any:
    from evolvekit.status import build_status

    return build_status(run_dir)


def _where(root: Path, study: Study, harness: Harness, screen: str, run: str | None, document_of: Any,
           models: dict[str, Any] | None = None) -> str:
    """The screen, and what it shows: a run's status and results in the words
    and numbers of the Running and Results pages (per case: the case's file
    name and its numbers, never a row), the plan's words, the test run's
    failures and the preview's error when there is one."""
    lines = [f"Screen: {screen or 'unknown'}" + (f", run {run}" if run else "")]
    plan_line = ""
    if run:
        document_of = document_of or _status_document
        try:
            status, results = jobs.status(root, run, document_of), jobs.results(root, run, document_of)
        except AppError as exc:
            lines.append(f"The run could not be read: {exc}")
        else:
            plan = jobs._plan(root / "runs" / run, jobs.phase(root / "runs" / run))  # noqa: SLF001 - the run's own plan
            plan_line = str(plan.get("summary") or "") or _plan_numbers(plan)
            lines.append("The run:\n" + json.dumps(_run_words(status, results), indent=1, default=str))
    if not plan_line:
        plan_line = _plan_line(root, study, harness, models)
    if plan_line:
        lines.append(f"The plan: {plan_line}")
    test = work.test_run_state(root)
    if test.get("state") == "failed":
        failures = (test.get("result") or {}).get("failures") or [test.get("error") or "it failed"]
        lines.append("The test run failed: " + "; ".join(str(f) for f in failures))
    preview = work.preview_state(root)
    error = preview.get("error") if preview.get("state") in ("failed", "interrupted") else (preview.get("result") or {}).get("error")
    if error:
        lines.append(f"The preview's error: {error}")
    return "\n".join(lines)


def _run_words(status: dict[str, Any], results: dict[str, Any]) -> dict[str, Any]:
    """The words and numbers of the Running and Results screens, nothing else."""
    final = results.get("final") or {}
    goal = results.get("goal") or {}

    def rows(kind: str) -> list[str]:
        return [f"{c['case']}: {_num(c['start'])} -> {_num(c['best'])}, {_change(c.get('change_pct'))}"
                + (f" ({c['failed']} run(s) did not finish)" if c.get("failed") else "")
                for c in (results.get("by_case") or {}).get(kind) or []]

    changed = [f"{c['label']} ({c['name']}): {c['old_text']} -> {c['new_text']}" + ("" if c["changed"] else " (kept)")
               for c in results.get("changed") or [] if c.get("old") is not None or c.get("new") is not None]
    words: dict[str, Any] = {
        "status": _drop_empty({
            "sentence": status.get("sentence"), "best so far": (status.get("best") or {}).get("line"),
            "runs that failed": [f"{f['count']}x {f['message']}" for f in status.get("failures") or []],
            "note": status.get("busy_line"), "gated": status.get("gated_line"),
        }),
        "results": _drop_empty({
            "headline": results.get("headline"),
            "goal": f"{goal.get('says')} ({goal.get('direction')} is better)" if goal.get("kpi") else "",
            "final check": _drop_empty({
                "line": final.get("line"), "advice": final.get("advice"), "state": final.get("state"),
                "chance of a gain": final.get("chance_words") or final.get("chance"),
                "at worst (5 % bound) %": final.get("worst_pct"), "mean %": final.get("mean"),
                # The range the page shows: the 90 % range, whose low end is "at worst" (decision 15); a run
                # written before A1 has only its 95 % range.
                **({"90 % range %": final["range90"]} if final.get("range90") else {"95 % range %": final.get("ci95")}),
                "rule": final.get("rule"), "next": final.get("next"),
            }),
            "held-back cases (case: start -> best, change)": rows("held"),
            "cases the search learned from (case: start -> best, change)": rows("learned"),
            "what changed (before -> now)": changed if not results.get("is_seed") else "nothing: no combination beat the starting point",
        }),
    }
    return words


def _drop_empty(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v not in (None, "", [], {})}


def _num(value: Any) -> str:
    return jobs._number(value)  # noqa: SLF001 - the results page's own figures


def _change(pct: Any) -> str:
    if not isinstance(pct, (int, float)):
        return "no comparison"
    if abs(pct) < 0.05:
        return "the same"
    return f"{abs(pct):.1f} % {'better' if pct > 0 else 'worse'}"


def _plan_numbers(plan: dict[str, Any]) -> str:
    parts = [f"{plan['rounds']} rounds" if plan.get("rounds") else "",
             f"{plan['combinations']} combinations" if plan.get("combinations") else "",
             f"{plan['hours']:g} h in total" if plan.get("hours") else ""]
    return ", ".join(p for p in parts if p)


def _plan_line(root: Path, study: Study, harness: Harness, models: dict[str, Any] | None = None) -> str:
    """The plan in words, as the Limits step shows it (`models`: the ones the run is compiled with)."""
    from evolvekit.harness.execute import plan_for, read_preview

    measured = read_preview(root)
    timing = measured if measured and measured.get("ok") else {"overhead_s": 0.0, "wall_s": study.limits.time_per_case_s}
    try:
        return plan_for(study, harness, timing, models=models).summary()
    except (HarnessError, ValueError, ZeroDivisionError):
        return ""


# ---------------------------------------------------------------------------
# checking cards
# ---------------------------------------------------------------------------


def _starting_values(study: Study, harness: Harness, root: Path) -> dict[str, Any]:
    try:
        base = base_settings(study, harness, root)
    except HarnessError:
        base = {name: s.parameter.default for name, s in harness.settings.items()}
    values = {}
    for name in study.tuned_settings():
        start = study.settings[name].start
        values[name] = base.get(name) if start is None else start
    for name, change in study.data.items():
        values[name] = change.start
    return values


def _count_rows(root: Path, table: str, where: str) -> int:
    path = root / "preview" / "tables.sqlite"
    if not path.is_file():
        raise AppError("there is no preview yet to check it on", 409)
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        from evolvekit.harness.sdk import evk_harness as evk

        evk.guard(db)
        condition = f" WHERE {where}" if where.strip() else ""
        return int(db.execute(f'SELECT COUNT(*) FROM "{table}"{condition}').fetchone()[0])
    finally:
        db.close()


def check_card(card: Any, study: Study, harness: Harness, root: Path, proposed: dict[str, Any]) -> dict[str, Any]:
    """`card` with `ok`, and either what it shows on the preview (`value`,
    `rows`, `applies_to`) or the `problem`. `proposed` holds the names the
    same answer introduces, so cards may refer to each other."""
    if not isinstance(card, dict) or card.get("kind") not in KINDS:
        return {"kind": "unknown", "ok": False, "problem": f"a card must be one of {', '.join(KINDS)}"}
    kind = card["kind"]
    out = {**card, "ok": True, "problem": ""}

    def fail(problem: str) -> dict[str, Any]:
        return {**out, "ok": False, "problem": problem}

    kpi_names = set(study.kpis) | set(proposed.get("kpis", {}))
    tunables = set(study.tunables()) | set(proposed.get("tunables", {}))
    try:
        if kind == "setting":
            name = card.get("name")
            setting = harness.settings.get(str(name))
            if setting is None:
                return fail(f"{name!r} is not a setting of {harness.title}")
            mode = card.get("mode", "tune")
            if mode == "fixed":
                problem = setting.parameter.problem_with(card.get("value"))
                if problem:
                    return fail(problem)
            elif mode == "tune" and setting.parameter.numeric:
                low = card.get("low", setting.parameter.low)
                high = card.get("high", setting.parameter.high)
                if not (setting.parameter.low <= low < high <= setting.parameter.high):
                    return fail(f"the range must lie within [{setting.parameter.low:g}, {setting.parameter.high:g}]")
            elif mode not in ("tune", "default"):
                return fail("mode is tune, fixed or default")
            return out
        if kind == "data_change":
            name = str(card.get("name") or "")
            if not _NAME.match(name) or name in harness.settings or name in (set(study.data) | set(study.kpis)):
                return fail(f"{name!r} cannot name a new data change: lower case, and not taken")
            lever = harness.levers.get(str(card.get("lever")))
            if lever is None:
                return fail(f"there is no lever {card.get('lever')!r}; there are {sorted(harness.levers)}")
            if not lever.code and card.get("column") not in lever.columns:
                return fail(f"{lever.label} changes {sorted(lever.columns)}")
            if card.get("mode") not in lever.modes:
                return fail(f"{lever.label} can {list(lever.modes)}")
            low, high = float(card["low"]), float(card["high"])
            start = float(card.get("start", 1.0 if card["mode"] == "scale" and low <= 1 <= high else low))
            if not low < high or not low <= start <= high:
                return fail("low < high, and the start between them")
            try:
                count = _count_rows(root, lever.table, str(card.get("where") or ""))
            except sqlite3.Error as exc:
                return fail(f"the rows condition does not run on {lever.table}: {exc}")
            if count == 0:
                return fail(f"the condition selects no row of {lever.table} in the smallest case")
            return {**out, "start": start, "applies_to": count, "table": lever.table}
        if kind == "constraint":
            says = str(card.get("says") or "").strip()
            if bool(card.get("expr")) == bool(card.get("sql")):
                return fail("a constraint has an expr or an sql, one of them")
            if card.get("expr"):
                expression = Expression.parse(str(card["expr"]))
                unknown = sorted(expression.names - tunables)
                if unknown:
                    return fail(f"{unknown[0]!r} is not something the study tunes")
                values = {**_starting_values(study, harness, root), **proposed.get("tunables", {})}
                if not expression.holds({k: v for k, v in values.items() if k in expression.names}):
                    return fail("it does not hold at the starting values: the start must be allowed")
                return {**out, "holds_today": True}
            result = work.try_sql(root, str(card["sql"]), {})
            if not result["value"]:
                return fail("it does not hold today, on the smallest case")
            return {**out, "holds_today": True, "says": says}
        if kind == "kpi":
            name = str(card.get("name") or "")
            if not _NAME.match(name) or name in study.kpis or name in tunables or name in proposed.get("twice", set()):
                return fail(f"{name!r} cannot name a new measure: lower case, and not taken")
            if card.get("direction") not in ("lower", "higher"):
                return fail("direction is lower or higher")
            result = work.try_sql(root, str(card.get("sql") or ""), {}, str(card.get("rows_sql") or ""))
            rows = result.get("rows")
            return {**out, "value": result["value"], "note": result.get("note"),
                    "rows": len(rows["rows"]) if rows else None}
        if kind == "weighted":
            name = str(card.get("name") or "")
            if not _NAME.match(name) or name in study.kpis or name in tunables or name in proposed.get("twice", set()):
                return fail(f"{name!r} cannot name a new measure: lower case, and not taken")
            parts = card.get("weighted")
            if not isinstance(parts, dict) or not parts:
                return fail("name the measures and their weights")
            for part, weight in parts.items():
                if part not in kpi_names and part not in harness.kpis:
                    return fail(f"{part!r} is not a measure of the study")
                if not isinstance(weight, (int, float)) or isinstance(weight, bool):
                    return fail(f"the weight of {part} must be a number")
            return out
        if kind == "goal":
            levels = card.get("levels")
            if not isinstance(levels, list) or not 1 <= len(levels) <= 4:
                return fail("a goal has one to four levels")
            for index, level in enumerate(levels):
                if level.get("kpi") not in kpi_names and level.get("kpi") not in harness.kpis:
                    return fail(f"{level.get('kpi')!r} is not a measure of the study")
                if level.get("direction") not in ("lower", "higher"):
                    return fail("each level says lower or higher")
                within = parse_within(level.get("equal_within"))
                if within is None and index < len(levels) - 1:
                    return fail(f"level {index + 1} needs equal_within, so the next level can decide")
            if len({level["kpi"] for level in levels}) != len(levels):
                return fail("each level is a different measure")
            return out
        if kind == "guardrail":
            kpi = card.get("kpi")
            if kpi not in kpi_names and kpi not in harness.kpis:
                return fail(f"{kpi!r} is not a measure of the study")
            if (card.get("max") is None) == (card.get("min") is None):
                return fail("a guardrail has max or min, one of them")
            return out
    except (ExpressionError, ValueError, TypeError, KeyError) as exc:
        return fail(f"{type(exc).__name__}: {exc}")
    except AppError as exc:
        return fail(str(exc))
    return fail("not checked")


def _proposed(cards: list[Any]) -> dict[str, Any]:
    kpis, tunables, twice = {}, {}, set()
    for card in cards:
        if not isinstance(card, dict):
            continue
        if card.get("kind") in ("kpi", "weighted") and card.get("name"):
            if str(card["name"]) in kpis:
                twice.add(str(card["name"]))
            kpis[str(card["name"])] = card
        if card.get("kind") == "data_change" and card.get("name"):
            tunables[str(card["name"])] = card.get("start", 1.0)
        if card.get("kind") == "setting" and card.get("mode", "tune") == "tune" and card.get("name"):
            tunables[str(card["name"])] = card.get("start")
    return {"kpis": kpis, "twice": twice, "tunables": {k: v for k, v in tunables.items() if v is not None}}


def _parse(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.S)
    if fenced:
        stripped = fenced.group(1)
    elif not stripped.startswith("{"):
        start, end = stripped.find("{"), stripped.rfind("}")
        stripped = stripped[start:end + 1] if start >= 0 and end > start else stripped
    answer = json.loads(stripped)
    if not isinstance(answer, dict):
        raise ValueError("the answer is not a JSON object")
    if not isinstance(answer.get("cards", []), list):
        raise ValueError("cards must be a list")
    return answer


# ---------------------------------------------------------------------------
# asking
# ---------------------------------------------------------------------------


def _config(provider: str, model: str, prices: dict[str, Any] | None) -> ModelConfig:
    """The assistant's model slot. A model nobody knows the price of gets
    0 / 0, which `_cost` reads as "not known": no model is paid for at 0."""
    price_in, price_out = price_of(model, prices) or (0.0, 0.0)
    return ModelConfig(role="strong", provider=provider, model=model, price_in_per_mtok=price_in,
                       price_out_per_mtok=price_out, max_tokens=MAX_TOKENS, temperature=0.2)


def _provider(home: Any) -> tuple[Any, ModelConfig]:
    """The provider from Settings, with the app's keys put into the
    environment first (`Home.export_keys`), so a key changed or removed in
    Settings counts from this question on."""
    from evolvekit.providers import build_provider

    settings = home.settings()
    home.export_keys()
    config = _config(settings["provider"], settings["models"]["assistant"], settings.get("prices"))
    return build_provider(config), config


def _cost(completion: Any, config: ModelConfig | None) -> float | None:
    """What the completion cost: what the provider reported, else the price
    table's figure, else `None` -- never a guess."""
    if completion.usd is not None:
        return float(completion.usd)
    if config is None or not (config.price_in_per_mtok or config.price_out_per_mtok):
        return None
    return (completion.input_tokens * config.price_in_per_mtok + completion.output_tokens * config.price_out_per_mtok) / 1e6


def ask(home: Any, root: Path, message: str, provider: Any = None, *, screen: str = "", run: str | None = None,
        document_of: Any = None, turn: str | None = None, cancelled: threading.Event | None = None) -> dict[str, Any]:
    """The assistant's answer to `message`, asked from `screen` (`step-4`,
    `running`, `results`...) with `run` in view: what it says, and its cards,
    each checked on the preview. `turn` names the question in the history
    (one is made up when none is given); `cancelled`, set by the consultant
    while the model thinks, makes the model's answer be thrown away: the
    history then says the turn was cancelled and the answer is
    `{"cancelled": True}`."""
    message = message.strip()
    if not message:
        raise AppError("say what you would like the study to do")
    turn = turn or _id()
    study, harness = load_study(root), load_harness(root / "harness")
    config: ModelConfig | None = None
    if provider is None:
        settings, keys = home.settings(), home.keys()
        needed = {"openrouter": ["OPENROUTER_API_KEY"], "azure": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"]}.get(settings["provider"], [])
        if any(not keys.get(k) for k in needed):
            return {"needs_key": True, "say": "The assistant needs an API key. Open Settings, choose the provider and paste "
                    "the key; it is kept on this computer only. Templates, the data-change form and the SQL box work without one.",
                    "cards": [], "usd": 0.0, "total_usd": history(root)["total_usd"], "turn": turn}
        try:
            provider, config = _provider(home)
        except Exception as exc:  # noqa: BLE001 - a provider that cannot be built is a sentence, not a crash
            raise AppError(f"the assistant cannot reach its model: {exc}", 502) from None
    past = _answered(_entries(root))[-2 * TURNS:]
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": context(root, study, harness, screen=screen, run=run, home=home, document_of=document_of)}]
    messages.append({"role": "assistant", "content": '{"say": "I have read the tables, the catalogues and the study.", "cards": []}'})
    for entry in past:
        if entry["role"] == "user":
            messages.append({"role": "user", "content": entry.get("text", "")})
        else:
            messages.append({"role": "assistant", "content": json.dumps({"say": entry.get("say", ""), "cards": _replayed_cards(entry)})})
    messages.append({"role": "user", "content": message})
    _append(root, {"id": turn, "role": "user", "text": message})

    usd: float | None = 0.0
    requests: float | None = None  # Copilot requests, for a provider that counts them instead of dollars
    tokens, checked, say, raw_cards = 0, [], "", []

    def counted(entry: dict[str, Any]) -> dict[str, Any]:
        if requests is not None:
            entry["requests"] = _count(requests)
        return entry
    json_mode = {"response_format": {"type": "json_object"}} if getattr(provider, "name", "") in JSON_MODE else {}

    def thrown_away() -> dict[str, Any]:
        # The consultant cancelled while the model thought: its answer is not kept, what it cost is.
        _append(root, counted({"role": "assistant", "turn": turn, "cancelled": True, "usd": _round(usd)}))
        return counted({"cancelled": True, "usd": _round(usd), "total_usd": history(root)["total_usd"], "turn": turn})

    for attempt in range(1 + MAX_CORRECTIONS):
        try:
            completion = provider.complete(messages, model=config.model if config else "fake",
                                           max_tokens=MAX_TOKENS, temperature=0.2, **json_mode)
        except Exception as exc:  # noqa: BLE001 - the model's failure is said in the chat, and kept there
            if cancelled is not None and cancelled.is_set():
                return thrown_away()
            reason = failure_words(exc)
            _append(root, counted({"role": "assistant", "turn": turn, "failed": reason, "usd": _round(usd)}))
            return counted({"failed": reason, "say": "", "cards": [], "usd": _round(usd),
                            "total_usd": history(root)["total_usd"], "turn": turn})
        if completion.requests is not None:
            requests = (requests or 0.0) + float(completion.requests)
        cost = _cost(completion, config)
        usd = None if cost is None or usd is None else usd + cost  # one unknown call makes the answer's cost unknown
        tokens += completion.input_tokens + completion.output_tokens
        if cancelled is not None and cancelled.is_set():
            return thrown_away()
        try:
            answer = _parse(completion.text)
        except ValueError as exc:
            messages += [{"role": "assistant", "content": completion.text},
                         {"role": "user", "content": f"That was not one JSON object ({exc}). Answer again with the JSON only."}]
            continue
        say, raw_cards = str(answer.get("say") or ""), list(answer.get("cards") or [])
        proposed = _proposed(raw_cards)
        checked = [check_card(card, study, harness, root, proposed) for card in raw_cards]
        failed = [c for c in checked if not c["ok"]]
        if not failed or attempt == MAX_CORRECTIONS:
            break
        report = "\n".join(f"- card {i + 1} ({c.get('kind')}): {c['problem']}" for i, c in enumerate(checked) if not c["ok"])
        messages += [{"role": "assistant", "content": completion.text},
                     {"role": "user", "content": "These cards failed the check on the preview case:\n" + report
                      + "\nFix them and answer again with the complete JSON (every card, fixed or not)."}]
    else:
        say = say or "I could not put that into a form the app understands."
    for card in checked:
        if not card["ok"]:
            card["problem"] = (f"It could not be expressed: {card['problem']}. Try saying it differently, "
                               "or use a template or the forms.")
    _append(root, counted({"role": "assistant", "turn": turn, "say": say, "raw_cards": raw_cards, "cards": checked,
                           "usd": _round(usd), "tokens": tokens}))
    return counted({"say": say, "cards": checked, "usd": _round(usd), "total_usd": history(root)["total_usd"], "turn": turn})


def _round(usd: float | None) -> float | None:
    return None if usd is None else round(usd, 6)


def _count(requests: float) -> int | float:
    """A request count as the page shows it: whole numbers without a `.0`."""
    rounded = round(float(requests), 2)
    return int(rounded) if rounded == int(rounded) else rounded


# ---------------------------------------------------------------------------
# applying a card
# ---------------------------------------------------------------------------


def apply_card(root: Path, card: dict[str, Any]) -> None:
    """Add a checked card to the study -- the assistant's, or one the page's
    forms made in the same shape."""
    study, harness = load_study(root), load_harness(root / "harness")
    updated = copy.deepcopy(study)
    kind = card.get("kind")
    try:
        if kind == "setting":
            mode = card.get("mode", "tune")
            if mode == "fixed":
                updated.settings[card["name"]] = SettingChoice("fixed", value=card.get("value"))
            elif mode == "default":
                updated.settings.pop(card["name"], None)
            else:
                updated.settings[card["name"]] = SettingChoice("tune", low=card.get("low"), high=card.get("high"), start=card.get("start"))
        elif kind == "data_change":
            updated.data[card["name"]] = DataChange.parse(
                {k: card[k] for k in ("lever", "column", "where", "mode", "low", "high", "start", "says") if k in card},
                f"vary.data.{card['name']}")
        elif kind == "constraint":
            updated.constraints.append(Constraint.parse(
                {k: card[k] for k in ("says", "expr", "sql") if card.get(k)}, "constraints"))
        elif kind == "kpi":
            updated.kpis[card["name"]] = StudyKpi.parse(
                {k: card[k] for k in ("says", "direction", "unit", "sql", "rows_sql") if card.get(k) is not None}, f"kpis.{card['name']}")
        elif kind == "weighted":
            for part in card["weighted"]:
                if part not in updated.kpis and part in harness.kpis:
                    updated.kpis[part] = StudyKpi(kind="harness")
            updated.kpis[card["name"]] = StudyKpi.parse({"says": card.get("says", ""), "weighted": card["weighted"]}, f"kpis.{card['name']}")
        elif kind == "goal":
            for level in card["levels"]:
                if level["kpi"] not in updated.kpis and level["kpi"] in harness.kpis:
                    updated.kpis[level["kpi"]] = StudyKpi(kind="harness")
            updated.goal = [GoalLevel(kpi=level["kpi"], direction=level["direction"], equal_within=level.get("equal_within"))
                            for level in card["levels"]]
            if updated.goal:
                updated.goal[-1].equal_within = None if len(updated.goal) > 1 else updated.goal[-1].equal_within
        elif kind == "guardrail":
            if card["kpi"] not in updated.kpis and card["kpi"] in harness.kpis:
                updated.kpis[card["kpi"]] = StudyKpi(kind="harness")
            updated.guardrails = [g for g in updated.guardrails if g.kpi != card["kpi"]]
            updated.guardrails.append(Guardrail(kpi=card["kpi"], max=card.get("max"), min=card.get("min")))
        else:
            raise AppError(f"a card must be one of {', '.join(KINDS)}")
    except (KeyError, HarnessError) as exc:
        raise AppError(f"the card cannot be added: {exc}") from None
    save_study(updated, root)


def apply_cards(root: Path, cards: list[Any]) -> list[dict[str, Any]]:
    """A whole answer into the study: every card checked and added in
    `APPLY_ORDER` (stable within a kind), each against the study as the ones
    before it left it, so a goal sees the measure added a moment ago. Returns
    the cards in the order given, each with `ok` and `problem`; one that fails
    is left out and the others still go in."""
    harness = load_harness(root / "harness")
    rank = {kind: i for i, kind in enumerate(APPLY_ORDER)}
    order = sorted(range(len(cards)), key=lambda i: rank.get(cards[i].get("kind") if isinstance(cards[i], dict) else None, len(rank)))
    done: list[dict[str, Any] | None] = [None] * len(cards)
    for i in order:
        # The names the answer still promises: the cards not yet found wanting.
        promised = [card for j, card in enumerate(cards) if done[j] is None or done[j]["ok"]]
        checked = check_card(cards[i], load_study(root), harness, root, _proposed(promised))
        if checked["ok"]:
            try:
                apply_card(root, checked)
            except AppError as exc:
                checked = {**checked, "ok": False, "problem": str(exc)}
        done[i] = checked
    return [card for card in done if card is not None]
