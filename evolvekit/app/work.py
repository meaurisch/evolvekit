"""Background work inside the app: the baseline preview, the test run and a
question to the assistant, and what the page asks of the preview's tables.

Each piece of work runs in a thread of the app. The preview and the test run
keep a state file, so a reload -- or the app opened again meanwhile -- knows
where they are:

    <study>/preview/state.json            the preview: running, done or failed
    <study>/preview/test-run/state.json   the test run

A state that says "running" for a process that is gone (the app was closed
mid-way) reads as "interrupted", and the work can simply be started again.

A question to the assistant keeps its state in `server.work["assistant:<slug>"]`
only: the history file (`assistant.jsonl`) is the record that lasts, and a
question the app was closed in the middle of is simply one without an answer.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from evolvekit.app import AppError
from evolvekit.harness import HarnessError
from evolvekit.harness.execute import PREVIEW, preview, preview_time_limit, read_preview, run_test, study_harness
from evolvekit.harness.study import load_study
from evolvekit.harness.manifest import Harness
from evolvekit.harness.sdk import evk_harness as evk
from evolvekit.ledger import _atomic_write

__all__ = ["ask_state", "cancel_ask", "preview_state", "preview_summary", "start_ask", "start_preview", "start_test_run",
           "template_choices", "test_run_state", "try_sql"]

STATE = "state.json"
ROWS = 50
DISTINCT = 50


def _state_path(root: Path, what: str) -> Path:
    return root / PREVIEW / (STATE if what == "preview" else f"test-run/{STATE}")


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(state, indent=2) + "\n")


def _state(server: Any | None, root: Path, what: str) -> dict[str, Any]:
    state = _read_state(_state_path(root, what)) or {"state": "none"}
    if state.get("state") == "running":
        thread = (server.work.get(f"{what}:{root}") if server is not None else None)
        mine = state.get("pid") == os.getpid()
        if (mine and server is not None and (thread is None or not thread.is_alive())) or (not mine and not _pid_alive(state.get("pid"))):
            state = {**state, "state": "interrupted", "error": "it stopped when the app was closed; start it again"}
    return state


def _pid_alive(pid: Any) -> bool:
    from evolvekit.lock import pid_alive

    try:
        return bool(pid) and pid_alive(int(pid))
    except (TypeError, ValueError):
        return False


def _start(server: Any, root: Path, what: str, work: Any, **extra: Any) -> dict[str, Any]:
    key = f"{what}:{root}"
    path = _state_path(root, what)
    with server.lock:
        current = _state(server, root, what)
        if current.get("state") == "running":
            return current
        state = {"state": "running", "pid": os.getpid(), "started_at": time.time(), **extra}
        _write_state(path, state)

        def body() -> None:
            try:
                result = work()
                ok = bool(result.get("ok"))
                final = {**state, "state": "done" if ok else "failed", "finished_at": time.time(),
                         "error": None if ok else (result.get("error") or "; ".join(result.get("failures") or []) or "it failed")}
                if what == "test-run":
                    final["result"] = result
            except (HarnessError, AppError) as exc:
                final = {**state, "state": "failed", "finished_at": time.time(), "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - the state file must say how it ended
                final = {**state, "state": "failed", "finished_at": time.time(), "error": f"{type(exc).__name__}: {exc}"}
            _write_state(path, final)

        thread = threading.Thread(target=body, name=f"evolvekit-{what}", daemon=True)
        server.work[key] = thread
        thread.start()
    return state


# ---------------------------------------------------------------------------
# the preview
# ---------------------------------------------------------------------------


def start_preview(server: Any, root: Path) -> dict[str, Any]:
    try:
        study, harness = load_study(root), study_harness(root)
        # What it usually takes: the limit, and a few seconds to start and to write the tables.
        expected = preview_time_limit(study, harness) + 5.0 if harness.time_limit.accepts else None
    except Exception:  # noqa: BLE001 - an estimate is a courtesy; the preview itself says what is wrong
        expected = None
    return _start(server, root, "preview", lambda: preview(root), expected_s=expected)


def preview_state(root: Path, server: Any | None = None) -> dict[str, Any]:
    state = _state(server, root, "preview")
    result = read_preview(root)
    if result is not None:
        state["result"] = result
    return state


def _database(root: Path) -> sqlite3.Connection:
    path = root / PREVIEW / "tables.sqlite"
    if not path.is_file():
        raise AppError("there is no preview yet: it runs when the cases are chosen", 409)
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def try_sql(root: Path, sql: str, params: dict[str, Any], rows_sql: str = "") -> dict[str, Any]:
    """A KPI's value on the preview case -- the first column of the first row,
    NULL counted as 0 -- and, with `rows_sql`, the rows it counts (at most 50).
    Read-only, and stopped after 2 s."""
    if not sql.strip():
        raise AppError("sql: the query to try")
    db = _database(root)
    try:
        clock = evk.guard(db)
        try:
            value, note = evk.query_value(db, sql, params or {}, clock=clock)
        except evk.HarnessStop as exc:
            raise AppError(str(exc)) from None
        rows: dict[str, Any] | None = None
        if rows_sql.strip():
            clock["at"] = time.monotonic()
            try:
                cursor = db.execute(rows_sql, params or {})
                rows = {"columns": [d[0] for d in cursor.description or []],
                        "rows": [list(r) for r in cursor.fetchmany(ROWS)]}
            except sqlite3.Error as exc:
                raise AppError(f"the rows query failed: {exc}") from None
        return {"value": value, "note": note, "rows": rows}
    finally:
        db.close()


def template_choices(root: Path, harness: Harness) -> dict[str, dict[str, list[Any]]]:
    """The choices of every KPI template's blanks: listed, or from the
    template's query on the preview's tables (empty before the preview)."""
    out: dict[str, dict[str, list[Any]]] = {}
    db = None
    clock: dict[str, float] = {}
    try:
        try:
            db = _database(root)
            clock = evk.guard(db)
        except AppError:
            db = None
        for name, template in harness.kpi_templates.items():
            params: dict[str, list[Any]] = {}
            for param in template.params.values():
                if param.choices:
                    params[param.name] = list(param.choices)
                elif param.source and db is not None:
                    clock["at"] = time.monotonic()
                    try:
                        params[param.name] = [r[0] for r in db.execute(param.source).fetchmany(200)]
                    except sqlite3.Error:
                        params[param.name] = []
            out[name] = params
    finally:
        if db is not None:
            db.close()
    return out


def preview_summary(root: Path, harness: Harness) -> dict[str, Any]:
    """What the assistant may know about the preview case: per table the row
    count, min / mean / max of each numeric column, and at most 50 distinct
    values of each column the harness declares categorical. Never a row, and
    nothing at all about a column the harness declares private (coordinates)."""
    try:
        db = _database(root)
    except AppError:
        return {}
    summary: dict[str, Any] = {}
    try:
        clock = evk.guard(db, 5.0)
        for name, table in harness.tables.items():
            clock["at"] = time.monotonic()
            try:
                count = db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except sqlite3.Error:
                continue
            columns: dict[str, Any] = {}
            for column, spec in table.columns.items():
                if spec.private:
                    continue
                clock["at"] = time.monotonic()
                quoted = '"' + column.replace('"', '""') + '"'
                if spec.type in ("int", "float", "bool"):
                    low, mean, high = db.execute(f'SELECT MIN({quoted}), AVG({quoted}), MAX({quoted}) FROM "{name}"').fetchone()
                    columns[column] = {"min": low, "mean": round(mean, 4) if isinstance(mean, float) else mean, "max": high}
                if spec.categorical:
                    values = [r[0] for r in db.execute(
                        f'SELECT DISTINCT {quoted} FROM "{name}" ORDER BY 1 LIMIT {DISTINCT + 1}').fetchall()]
                    columns.setdefault(column, {})["values"] = values[:DISTINCT]
                    if len(values) > DISTINCT:
                        columns[column]["more_values"] = True
            summary[name] = {"rows": count, "columns": columns}
    finally:
        db.close()
    return summary


# ---------------------------------------------------------------------------
# the test run
# ---------------------------------------------------------------------------


def start_test_run(server: Any, root: Path) -> dict[str, Any]:
    return _start(server, root, "test-run", lambda: run_test(root))


def test_run_state(root: Path, server: Any | None = None) -> dict[str, Any]:
    return _state(server, root, "test-run")


# ---------------------------------------------------------------------------
# a question to the assistant
# ---------------------------------------------------------------------------


def _ask_key(root: Path) -> str:
    return f"assistant:{root.name}"


def start_ask(server: Any, home: Any, root: Path, message: str, *, screen: str = "", run: str | None = None,
              provider: Any = None, document_of: Any = None) -> dict[str, Any]:
    """Put `message` to the assistant in a thread of the app and answer at
    once with the turn's id: the page polls `ask_state` for the answer, so a
    question outlives a change of screen and can be cancelled. One question
    of a study at a time (409 while the last one is still thought about)."""
    from evolvekit.app import assistant

    if not message.strip():
        raise AppError("say what you would like the study to do")
    key = _ask_key(root)
    with server.lock:
        current = server.work.get(key)
        if isinstance(current, dict) and current.get("state") == "thinking" and current["thread"].is_alive():
            raise AppError("the assistant is still thinking about the last question: wait for it, or cancel it", 409)
        turn = assistant._id()  # noqa: SLF001 - the history's own ids
        cancel = threading.Event()
        state: dict[str, Any] = {"turn": turn, "state": "thinking", "started_at": time.time(), "cancel": cancel, "answer": None}

        def body() -> None:
            try:
                answer = assistant.ask(home, root, message, provider, screen=screen, run=run, document_of=document_of,
                                       turn=turn, cancelled=cancel)
            except AppError as exc:
                answer = {"failed": str(exc), "say": "", "cards": [], "usd": None, "turn": turn}
            except Exception as exc:  # noqa: BLE001 - the page must learn how it ended
                answer = {"failed": f"{type(exc).__name__}: {exc}", "say": "", "cards": [], "usd": None, "turn": turn}
            with server.lock:
                state["answer"] = answer
                state["state"] = "cancelled" if answer.get("cancelled") or cancel.is_set() else "done"
                state["finished_at"] = time.time()

        thread = threading.Thread(target=body, name="evolvekit-assistant", daemon=True)
        state["thread"] = thread
        server.work[key] = state
        thread.start()
    return {"turn": turn, "state": "thinking"}


def ask_state(server: Any, root: Path, turn: str | None = None) -> dict[str, Any]:
    """Where a question stands: `thinking`, `cancelled`, or `done` with the
    answer. Without `turn`, the question being thought about now, if any
    (`{"state": "none"}` otherwise). A turn this app no longer holds is
    looked up in the history; one not there either is a 404."""
    from evolvekit.app import assistant

    with server.lock:
        current = server.work.get(_ask_key(root))
        if not isinstance(current, dict):
            current = None
        elif current.get("state") == "thinking" and not current["thread"].is_alive():
            current = {**current, "state": "done", "answer": {"failed": "the question was lost inside the app; ask it again",
                                                              "say": "", "cards": [], "usd": None, "turn": current["turn"]}}
    if turn is None:
        if current is not None and current["state"] == "thinking":
            return {"state": "thinking", "turn": current["turn"], "started_at": current["started_at"]}
        return {"state": "none"}
    if current is not None and current["turn"] == turn:
        if current["state"] == "thinking":
            return {"state": "thinking", "turn": turn, "started_at": current["started_at"]}
        return {"state": current["state"], **(current.get("answer") or {}), "turn": turn}
    for entry in assistant.history(root)["turns"]:
        if entry.get("role") == "assistant" and entry.get("turn") == turn:
            kept = {k: v for k, v in entry.items() if k not in ("raw_cards", "tokens")}
            return {"state": "cancelled" if entry.get("cancelled") else "done", **kept, "turn": turn}
    raise AppError("that question is not known to this app any more: ask it again", 404)


def cancel_ask(server: Any, root: Path) -> dict[str, Any]:
    """Stop waiting for the question being thought about: whatever the model
    answers is thrown away, and the history says the turn was cancelled.
    `{"cancelled": False}` when nothing is being thought about."""
    with server.lock:
        current = server.work.get(_ask_key(root))
        if not isinstance(current, dict) or current.get("state") != "thinking":
            return {"cancelled": False}
        current["cancel"].set()
        current["state"] = "cancelled"
    return {"cancelled": True}
