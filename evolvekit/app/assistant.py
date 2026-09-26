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
answer's cost is kept in `assistant.jsonl` in the study folder.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evolvekit.app import AppError
from evolvekit.app import work
from evolvekit.config import ModelConfig
from evolvekit.expressions import Expression, ExpressionError
from evolvekit.harness import HarnessError
from evolvekit.harness.compile import base_settings
from evolvekit.harness.manifest import Harness, load_harness
from evolvekit.harness.study import Constraint, DataChange, GoalLevel, Guardrail, SettingChoice, Study, StudyKpi, load_study, parse_within, save_study

__all__ = ["apply_card", "ask", "history"]

HISTORY = "assistant.jsonl"
MAX_CORRECTIONS = 2
MAX_TOKENS = 3000
TURNS = 6
KINDS = ("setting", "data_change", "constraint", "kpi", "weighted", "goal", "guardrail")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
PRICES = {"anthropic/claude-sonnet-5": (2.0, 10.0)}

SYSTEM = """\
You help a consultant set up a study in evolvekit's app. A study tunes an application's settings, or
the data it is given, and judges the result with KPIs. The consultant is not a programmer: keep every
sentence plain.

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
- A goal has at most four levels in order of importance; every level but the last needs
  "equal_within" ("1 %" relative to the starting point, or a number in the KPI's unit).
- When the request cannot be expressed with these tables, say so and return no cards.
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append(root: Path, entry: dict[str, Any]) -> None:
    with open(root / HISTORY, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, allow_nan=False) + "\n")


def _entries(root: Path) -> list[dict[str, Any]]:
    path = root / HISTORY
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def history(root: Path) -> dict[str, Any]:
    entries = _entries(root)
    return {"turns": entries, "total_usd": round(sum(float(e.get("usd") or 0) for e in entries), 6)}


# ---------------------------------------------------------------------------
# what the model is told
# ---------------------------------------------------------------------------


def context(root: Path, study: Study, harness: Harness) -> str:
    """The schema, the catalogues, the study and the preview's summaries."""
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
                              "label": s.label, "explain": s.explain or s.help}
                     for s in harness.settings.values()},
        "levers": {n: {"table": lv.table, "columns": dict(lv.columns), "modes": list(lv.modes), "code": lv.code,
                       "explain": lv.explain or lv.help} for n, lv in harness.levers.items()},
        "kpis": {n: {"label": k.label, "direction": k.direction, "unit": k.unit, "sql": k.sql,
                     "changes_with_levers": k.changes_with_levers, "help": k.help} for n, k in harness.kpis.items()},
        "kpi_templates": {n: {"label": t.label, "sql": t.sql, "params": list(t.params)} for n, t in harness.kpi_templates.items()},
    }
    so_far = study.to_yaml()
    so_far.pop("application", None)  # a path on this machine says nothing about the study
    summary = work.preview_summary(root, harness)
    return (
        f"## The harness: {harness.title}\n{harness.summary}\n\n"
        f"## Tables\n{json.dumps(tables, indent=1)}\n\n"
        f"## Catalogues\n{json.dumps(catalogues, indent=1)}\n\n"
        f"## The study so far\n{json.dumps(so_far, indent=1)}\n\n"
        f"## The preview case, summarised (no rows)\n"
        + (json.dumps(summary, indent=1, default=str) if summary else "(no preview yet)")
    )


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
                return fail(f"the condition selects no row of {lever.table} in the preview case")
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
                return fail("it does not hold on the preview case today")
            return {**out, "holds_today": True, "says": says}
        if kind == "kpi":
            name = str(card.get("name") or "")
            if not _NAME.match(name) or name in study.kpis or name in tunables or name in proposed.get("twice", set()):
                return fail(f"{name!r} cannot name a new KPI: lower case, and not taken")
            if card.get("direction") not in ("lower", "higher"):
                return fail("direction is lower or higher")
            result = work.try_sql(root, str(card.get("sql") or ""), {}, str(card.get("rows_sql") or ""))
            rows = result.get("rows")
            return {**out, "value": result["value"], "note": result.get("note"),
                    "rows": len(rows["rows"]) if rows else None}
        if kind == "weighted":
            name = str(card.get("name") or "")
            if not _NAME.match(name) or name in study.kpis or name in tunables or name in proposed.get("twice", set()):
                return fail(f"{name!r} cannot name a new KPI: lower case, and not taken")
            parts = card.get("weighted")
            if not isinstance(parts, dict) or not parts:
                return fail("name the KPIs and their weights")
            for part, weight in parts.items():
                if part not in kpi_names and part not in harness.kpis:
                    return fail(f"{part!r} is not a KPI of the study")
                if not isinstance(weight, (int, float)) or isinstance(weight, bool):
                    return fail(f"the weight of {part} must be a number")
            return out
        if kind == "goal":
            levels = card.get("levels")
            if not isinstance(levels, list) or not 1 <= len(levels) <= 4:
                return fail("a goal has one to four levels")
            for index, level in enumerate(levels):
                if level.get("kpi") not in kpi_names and level.get("kpi") not in harness.kpis:
                    return fail(f"{level.get('kpi')!r} is not a KPI of the study")
                if level.get("direction") not in ("lower", "higher"):
                    return fail("each level says lower or higher")
                within = parse_within(level.get("equal_within"))
                if within is None and index < len(levels) - 1:
                    return fail(f"level {index + 1} needs equal_within, so the next level can decide")
            if len({level["kpi"] for level in levels}) != len(levels):
                return fail("each level is a different KPI")
            return out
        if kind == "guardrail":
            kpi = card.get("kpi")
            if kpi not in kpi_names and kpi not in harness.kpis:
                return fail(f"{kpi!r} is not a KPI of the study")
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


def _provider(home: Any) -> tuple[Any, ModelConfig]:
    from evolvekit.providers import build_provider

    settings = home.settings()
    name, model = settings["provider"], settings["models"]["assistant"]
    values = home._env_values()  # noqa: SLF001 - the app's own key file
    for key in ("OPENROUTER_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"):
        if values.get(key) and not os.environ.get(key):
            os.environ[key] = values[key]
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    config = ModelConfig(role="strong", provider=name, model=model, price_in_per_mtok=price_in,
                         price_out_per_mtok=price_out, max_tokens=MAX_TOKENS, temperature=0.2)
    return build_provider(config), config


def _cost(completion: Any, config: ModelConfig | None) -> float:
    if completion.usd is not None:
        return float(completion.usd)
    if config is None:
        return 0.0
    return (completion.input_tokens * config.price_in_per_mtok + completion.output_tokens * config.price_out_per_mtok) / 1e6


def ask(home: Any, root: Path, message: str, provider: Any = None) -> dict[str, Any]:
    """The assistant's answer to `message`: what it says, and its cards, each
    checked on the preview."""
    message = message.strip()
    if not message:
        raise AppError("say what you would like the study to do")
    study, harness = load_study(root), load_harness(root / "harness")
    config: ModelConfig | None = None
    if provider is None:
        settings, keys = home.settings(), home.keys()
        needed = {"openrouter": ["OPENROUTER_API_KEY"], "azure": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"]}.get(settings["provider"], [])
        if any(not keys.get(k) for k in needed):
            return {"needs_key": True, "say": "The assistant needs an API key. Open Settings, choose the provider and paste "
                    "the key; it is kept on this computer only. Templates, the data-change form and the SQL box work without one.",
                    "cards": [], "usd": 0.0, "total_usd": history(root)["total_usd"]}
        try:
            provider, config = _provider(home)
        except Exception as exc:  # noqa: BLE001 - a provider that cannot be built is a sentence, not a crash
            raise AppError(f"the assistant cannot reach its model: {exc}", 502) from None
    past = [e for e in _entries(root) if e.get("role") in ("user", "assistant")][-2 * TURNS:]
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": context(root, study, harness)}]
    messages.append({"role": "assistant", "content": '{"say": "I have read the tables, the catalogues and the study.", "cards": []}'})
    for entry in past:
        if entry["role"] == "user":
            messages.append({"role": "user", "content": entry.get("text", "")})
        else:
            messages.append({"role": "assistant", "content": json.dumps({"say": entry.get("say", ""), "cards": entry.get("raw_cards", [])})})
    messages.append({"role": "user", "content": message})
    _append(root, {"ts": _now(), "role": "user", "text": message})

    usd, tokens, checked, say, raw_cards = 0.0, 0, [], "", []
    for attempt in range(1 + MAX_CORRECTIONS):
        try:
            completion = provider.complete(messages, model=config.model if config else "fake",
                                           max_tokens=MAX_TOKENS, temperature=0.2)
        except Exception as exc:  # noqa: BLE001 - the model's failure is said, not raised
            raise AppError(f"the assistant's model did not answer: {exc}", 502) from None
        usd += _cost(completion, config)
        tokens += completion.input_tokens + completion.output_tokens
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
    _append(root, {"ts": _now(), "role": "assistant", "say": say, "raw_cards": raw_cards, "cards": checked,
                   "usd": round(usd, 6), "tokens": tokens})
    return {"say": say, "cards": checked, "usd": round(usd, 6), "total_usd": history(root)["total_usd"]}


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
