"""SQL against a harness's declared tables, on evolvekit's side.

Every study definition -- a KPI, a data constraint, a lever's `where` -- is SQL
over the tables a harness declares. A statement is checked by compiling it
(`EXPLAIN`) against an empty database with exactly those tables and their
`orig_*` copies, behind the same read-only guard the SDK runs it behind: a
misspelt column, a table the harness does not have, or a statement that would
write are all found here, before anything runs.

The table and guard code is the SDK's own (`sdk/evk_harness.py`), imported,
so that the two sides cannot disagree about what a table looks like.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Iterable, Mapping

from evolvekit.harness.sdk import evk_harness as evk

__all__ = ["schema_database", "sql_problem", "placeholders", "where_problem"]

_PLACEHOLDER = re.compile(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)")


def placeholders(sql: str) -> set[str]:
    """The named parameters (`:tag`) a statement uses."""
    return set(_PLACEHOLDER.findall(sql))


def schema_database(
    declared: Mapping[str, Mapping[str, str]], request_tables: Iterable[str] = ()
) -> sqlite3.Connection:
    """An empty, read-only database with every declared table (`{table:
    {column: type}}`) and an `orig_` copy of each request table."""
    db = evk.build_database(
        {name: [] for name in declared},
        {name: dict(columns) for name, columns in declared.items()},
        {name: [] for name in request_tables},
    )
    evk.guard(db)
    return db


def sql_problem(db: sqlite3.Connection, sql: str, params: Iterable[str] = ()) -> str | None:
    """Why `sql` cannot run against `db`'s tables, in words, or `None`."""
    if not isinstance(sql, str) or not sql.strip():
        return "the SQL is empty"
    if ";" in sql.strip().rstrip(";"):
        return "one statement only"
    try:
        db.execute("EXPLAIN " + sql.strip().rstrip(";"), {name: None for name in params})
    except sqlite3.Error as exc:
        text = str(exc)
        if "not authorized" in text or "prohibited" in text:
            return "it may only read the tables (SELECT)"
        return text
    return None


def where_problem(db: sqlite3.Connection, table: str, where: str) -> str | None:
    """Why the row selection `where` cannot run on `table`, or `None`."""
    return sql_problem(db, f'SELECT rowid FROM "{table}" WHERE {where}')
