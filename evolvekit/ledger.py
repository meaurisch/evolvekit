"""Append-only run and usage logs, plus per-candidate prompt traces.

Three artefacts under the run directory:

    runs.jsonl    one row per candidate (lineage, stage scores, final score)
    usage.jsonl   one row per LLM call (tokens, USD, candidate, operator)
    traces/<id>.json   the exact messages sent and the response received

USD is computed from the config's per-model price table, except when the
backend reports real spend (`claude-cli` does), in which case the report wins.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from evolvekit.config import ModelConfig
from evolvekit.providers.base import Completion

__all__ = ["UsageRecord", "Ledger", "price_completion", "read_jsonl"]


def price_completion(completion: Completion, model_cfg: ModelConfig) -> float:
    """USD for one call: reported spend if the backend knows it, else the table."""
    if completion.usd is not None:
        return float(completion.usd)
    return (
        completion.input_tokens / 1_000_000.0 * model_cfg.price_in_per_mtok
        + completion.output_tokens / 1_000_000.0 * model_cfg.price_out_per_mtok
    )


@dataclass(frozen=True)
class UsageRecord:
    ts: str
    candidate_id: str
    generation: int
    operator: str
    role: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    usd: float
    priced_from: str  # "provider" or "table"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield every well-formed row, skipping a torn final line."""
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


class Ledger:
    """Owns the run directory and every file written into it."""

    def __init__(self, run_dir: str | Path) -> None:
        # Absolute from the start: evaluator subprocesses run with cwd set to
        # the config's base_dir, so a relative run dir would be resolved there
        # and every candidate path handed to them would dangle.
        self.run_dir = Path(run_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.traces_dir = self.run_dir / "traces"
        self.traces_dir.mkdir(exist_ok=True)
        self.runs_path = self.run_dir / "runs.jsonl"
        self.usage_path = self.run_dir / "usage.jsonl"
        self.archive_path = self.run_dir / "archive.json"
        self.embedding_calls = 0
        """Embedding requests made this process. `usage.jsonl` is the record;
        this is the cheap in-memory counter the summary footer reads."""

    # -- writing ---------------------------------------------------------

    def _append(self, path: Path, record: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, default=str) + "\n")

    def record_run(self, record: dict[str, Any]) -> None:
        self._append(self.runs_path, record)

    def record_usage(
        self,
        completion: Completion,
        model_cfg: ModelConfig,
        *,
        candidate_id: str,
        generation: int,
        operator: str,
    ) -> UsageRecord:
        usage = UsageRecord(
            ts=_now(),
            candidate_id=candidate_id,
            generation=generation,
            operator=operator,
            role=model_cfg.role,
            provider=completion.provider,
            model=completion.model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            usd=price_completion(completion, model_cfg),
            priced_from="provider" if completion.usd is not None else "table",
        )
        self._append(self.usage_path, asdict(usage))
        return usage

    def record_embedding(
        self,
        model_cfg: ModelConfig,
        *,
        tokens: int,
        texts: int,
        candidate_id: str,
        generation: int,
    ) -> UsageRecord:
        """Bill one embedding call from the near-duplicate gate.

        Embeddings emit no output tokens, so only `price_in_per_mtok` is read.
        The row is shaped exactly like a completion row -- `operator: "embed"`
        is the only thing that distinguishes it -- so every existing consumer
        of `usage.jsonl` (totals, the budget guard, the economics series) picks
        the spend up without knowing embeddings exist.
        """
        usage = UsageRecord(
            ts=_now(),
            candidate_id=candidate_id,
            generation=generation,
            operator="embed",
            role=model_cfg.role,
            provider=model_cfg.provider,
            model=model_cfg.model,
            input_tokens=int(tokens),
            output_tokens=0,
            usd=int(tokens) / 1_000_000.0 * model_cfg.price_in_per_mtok,
            priced_from="table",
        )
        self._append(self.usage_path, asdict(usage))
        self.embedding_calls += int(texts)
        return usage

    def write_trace(
        self,
        candidate_id: str,
        *,
        messages: list[dict[str, str]],
        response: str | None,
        error: str | None = None,
        meta: dict[str, Any] | None = None,
        subdir: str | None = None,
    ) -> Path:
        # Framework calls (the scratchpad summariser) go under traces/meta/ so
        # that "one trace per candidate" stays literally true at the top level.
        directory = self.traces_dir if subdir is None else self.traces_dir / subdir
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{candidate_id}.json"
        payload = {
            "ts": _now(),
            "candidate_id": candidate_id,
            "messages": messages,
            "response": response,
            "error": error,
            "meta": meta or {},
        }
        _atomic_write(path, json.dumps(payload, indent=2, default=str))
        return path

    def write_archive(self, payload: dict[str, Any]) -> Path:
        """Snapshot of the MAP-Elites grid: cells, elites, children, lineage.

        A snapshot, not the record: `runs.jsonl` is what the archive is rebuilt
        from on resume. This file exists so `status`, the leaderboard and a
        human can read the grid without replaying the log.
        """
        _atomic_write(
            self.archive_path,
            json.dumps({"updated": _now(), **payload}, indent=2, default=str),
        )
        return self.archive_path

    def read_archive(self) -> dict[str, Any]:
        """The last snapshot, or an empty mapping when there is none."""
        if not self.archive_path.is_file():
            return {}
        try:
            payload = json.loads(self.archive_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return payload if isinstance(payload, dict) else {}

    # -- reading ---------------------------------------------------------

    def runs(self) -> list[dict[str, Any]]:
        return list(read_jsonl(self.runs_path))

    def usage(self) -> list[dict[str, Any]]:
        return list(read_jsonl(self.usage_path))

    def totals(self) -> dict[str, float]:
        """Aggregate spend so far. Cheap enough to call every generation."""
        usd = tokens_in = tokens_out = 0.0
        calls = 0
        for row in self.usage():
            usd += float(row.get("usd", 0.0) or 0.0)
            tokens_in += float(row.get("input_tokens", 0) or 0)
            tokens_out += float(row.get("output_tokens", 0) or 0)
            calls += 1
        return {
            "usd": usd,
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
            "calls": float(calls),
        }


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file in the same directory so readers never see a partial."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
