"""The evolvekit app: studies on harnesses, for people who are not programmers.

    python -m evolvekit app [--home DIR] [--port N] [--no-browser] [--shortcut]

A local web app (docs/app.md): a standard-library HTTP server bound to this
machine, one self-contained page, and a JSON API over the library home. It
sets up a study step by step, starts it as a detached run, and explains the
result. Everything it knows is on disk, so it can be closed and opened again
at any time -- runs carry on without it.
"""

from __future__ import annotations

__all__ = ["AppError"]


class AppError(Exception):
    """A request the app refuses or cannot serve: one sentence, and the HTTP
    status that goes with it."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
