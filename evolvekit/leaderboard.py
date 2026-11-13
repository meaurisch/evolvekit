"""Views over `runs.jsonl` and `archive.json`: markdown and a single-file HTML.

Both are read-only projections -- nothing here is part of the search. Three
things they must show that a flat "score, descending" table cannot:

* **lineage** -- who improved whom, which is the question v1's leaderboard
  could not answer at all;
* **cells** -- where in the MAP-Elites grid a candidate landed, so a run that
  has quietly collapsed onto one lineage is visible at a glance;
* **the hold-out** -- ranking is by the discounted score, and any candidate
  whose private score is below the *seed's* private score is flagged, because
  that is the one failure the first real run shipped without noticing.

No-op and duplicate counts sit in the footer: they are the price of the
mutations that bought nothing, and they should be small.
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from evolvekit.economics import DEFAULT_WINDOW, GenerationPoint, series
from evolvekit.ledger import read_jsonl

__all__ = [
    "REEVALUATIONS",
    "REEVALUATION_SEED_BASE",
    "pool_rows",
    "read_reevaluations",
    "read_rows",
    "run_holdout_penalty",
    "view_rows",
    "rank",
    "render_markdown",
    "render_html",
    "fitness_of",
    "lineage_of",
    "novelty_counts",
    "unfinished_count",
    "competes",
    "seed_private",
    "seed_public",
    "replay_veto",
    "veto_reason",
    "vetoed_count",
    "render_economics",
    "economics_svg",
]

HOLDOUT_FLAG = "!"

REEVALUATIONS = "reevaluations.jsonl"
"""One row per re-evaluation of the best (`search.reevaluate_best_every`),
beside `runs.jsonl`: `{"id", "generation", "seed", "stage", "prior_runs",
"kpis", "score", "at"}`. Every view reads `runs.jsonl` pooled with it
(`read_rows`)."""
REEVALUATION_SEED_BASE = 2000
"""The k-th re-evaluation of a run (from 0) runs on seed `2000 + k`: above the
final check's `CHECK_SEEDS` (1001 ... 1012), so the search never selects on
the seeds the final check judges on."""


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _pooled(row: dict[str, Any], extra: Sequence[Mapping[str, Any]], holdout_penalty: float) -> dict[str, Any]:
    from evolvekit.evaluate.scoring import ranking_score  # here: `evaluate` imports this module

    kpis = dict(row.get("kpis") or {})
    score = float(row["score"])
    replicates = None
    for entry in extra:
        n = entry.get("prior_runs")
        new_score = _finite(entry.get("score"))
        if isinstance(n, bool) or not isinstance(n, int) or n < 1 or new_score is None:
            continue  # a malformed row pools nothing
        for name, value in (entry.get("kpis") or {}).items():
            new, old = _finite(value), _finite(kpis.get(name))
            if new is not None and old is not None:
                kpis[name] = (n * old + new) / (n + 1)
        score = (n * score + new_score) / (n + 1)
        replicates = n + 1
    if replicates is None:
        return row
    pooled = {**row, "kpis": kpis, "score": score, "replicates": replicates}
    if row.get("public_score") is not None:
        pooled["public_score"] = score
    public, private = pooled.get("public_score"), row.get("private_score")
    pooled["ranking_score"] = ranking_score(
        score, None if public is None else float(public), None if private is None else float(private),
        holdout_penalty,
    )
    if public is not None and private is not None:
        pooled["generalization_gap"] = float(public) - float(private)
    return pooled


def pool_rows(
    rows: Sequence[dict[str, Any]],
    reevaluations: Sequence[Mapping[str, Any]],
    *,
    holdout_penalty: float = 0.0,
) -> list[dict[str, Any]]:
    """`rows` with each re-evaluated row's runs pooled, in file order: with
    `n = prior_runs`, every KPI both carry and the score become
    `(n·old + new) / (n + 1)`, the public score follows the score,
    `replicates` is `n + 1`, and the ranking score is recomputed from the
    pooled score with `holdout_penalty` (`scoring.ranking_score`; the private
    score is never re-run). The pooled score is the mean of the runs' scores,
    not the score of the pooled KPIs. The veto is the caller's, after this
    (`read_rows`). Rows without a re-evaluation are returned as they are."""
    if not reevaluations:
        return list(rows)
    by_id: dict[str, list[Mapping[str, Any]]] = {}
    for entry in reevaluations:
        if entry.get("id") is not None:
            by_id.setdefault(str(entry["id"]), []).append(entry)
    result = []
    for row in rows:
        extra = by_id.get(str(row.get("id")))
        if not extra or _finite(row.get("score")) is None:
            result.append(row)
            continue
        result.append(_pooled(row, extra, holdout_penalty))
    return result


def read_reevaluations(run_dir: str | Path) -> list[dict[str, Any]]:
    """`reevaluations.jsonl`, oldest first; none for a run without the file."""
    return [row for row in read_jsonl(Path(run_dir) / REEVALUATIONS) if isinstance(row, dict)]


def run_holdout_penalty(run_dir: str | Path) -> float:
    """The run's `evaluate.holdout_penalty`: from the compiled `evolvekit.yaml`
    in the run directory (a study's run), else the config the last session
    started from (`run_started`'s `config_path`), else 0."""
    from evolvekit.config import load_config

    directory = Path(run_dir)
    paths = [directory / "evolvekit.yaml"]
    started = [e for e in read_jsonl(directory / "events.jsonl") if e.get("type") == "run_started"]
    if started and started[-1].get("config_path"):
        paths.append(Path(str(started[-1]["config_path"])))
    for path in paths:
        if not path.is_file():
            continue
        try:
            return float(load_config(path).evaluate.holdout_penalty)
        except Exception:  # noqa: BLE001 - a config that no longer loads: try the next
            continue
    return 0.0


def read_rows(
    run_dir: str | Path, *, holdout_penalty: float | None = None, veto: bool = True
) -> list[dict[str, Any]]:
    """The run's rows as every view ranks them: `runs.jsonl`, pooled with its
    re-evaluations (`pool_rows`, the ranking score recomputed with
    `holdout_penalty`, the run's own when not given), then the hold-out veto
    replayed (`replay_veto`; `veto=False` leaves it out, for readers that
    count rows). Without `reevaluations.jsonl` this is `replay_veto` of
    `runs.jsonl`, as every view read it before."""
    directory = Path(run_dir)
    raw = list(read_jsonl(directory / "runs.jsonl"))
    extra = read_reevaluations(directory)
    penalty = 0.0
    if extra:
        penalty = run_holdout_penalty(directory) if holdout_penalty is None else holdout_penalty
    return view_rows(raw, extra, holdout_penalty=penalty, veto=veto)


def view_rows(
    raw: Sequence[dict[str, Any]],
    reevaluations: Sequence[Mapping[str, Any]],
    *,
    holdout_penalty: float = 0.0,
    veto: bool = True,
) -> list[dict[str, Any]]:
    """`read_rows` over rows already read: pooled, then the veto replayed
    against the seed's private score **as written** (`raw`): a starting point
    re-evaluated as the best never moves the reference."""
    rows = pool_rows(raw, reevaluations, holdout_penalty=holdout_penalty)
    return replay_veto(rows, reference=seed_private(raw)) if veto else rows


def fitness_of(row: Mapping[str, Any]) -> float | None:
    """What the search ranked by: the hold-out-aware score when there is one."""
    value = row.get("ranking_score")
    if value is None:
        value = row.get("score")
    return None if value is None else float(value)


def competes(row: Mapping[str, Any]) -> bool:
    """Whether a row's score is comparable with the others: not rejected, all
    the way through the final stage, and not kept out by the hold-out. A row
    from before the `competes` field existed is given the benefit of the doubt,
    as it always was."""
    return not row.get("rejected") and bool(row.get("competes", True))


def veto_reason(reference: float, margin: float = 0.0) -> str:
    """The sentence a vetoed row carries: `evaluate.holdout_veto`'s reason,
    naming the noise margin when the veto allowed one."""
    if margin == 0:
        return f"holdout below the seed's {reference:.6g}"
    return f"holdout below the seed's {reference:.6g} by more than its noise margin {margin:.3g}"


def _margin(row: Mapping[str, Any]) -> float:
    """The noise margin a row was judged with; a row from before the margin
    existed has none and is judged by the literal rule."""
    return float(row.get("holdout_margin") or 0.0)


def _replayed_veto(row: Mapping[str, Any], reference: float | None) -> str | None:
    """The veto a row written before the veto existed would have had.

    A row that carries a `veto` key was judged by a run that knew the rule,
    `holdout_veto: false` included, and keeps that judgement. Without a
    reference (no seed hold-out), nobody is vetoed."""
    if "veto" in row or reference is None:
        return None
    if row.get("rejected") or not row.get("competes", True):
        return None
    private, margin = row.get("private_score"), _margin(row)
    if private is None or float(private) >= reference - margin:
        return None
    return veto_reason(reference, margin)


_FROM_ROWS = object()


def replay_veto(rows: Sequence[dict[str, Any]], *, reference: Any = _FROM_ROWS) -> list[dict[str, Any]]:
    """`rows` with the hold-out veto applied as a resumed run applies it: a row
    written before the veto existed whose private score is below the seed's
    gets `competes: false` and its `veto`. Every view that ranks or counts goes
    through this, so no view names a candidate the search keeps out.
    `reference`: the seed's private score to judge by (`view_rows` passes the
    unpooled seed row's); by default `seed_private(rows)`."""
    if reference is _FROM_ROWS:
        reference = seed_private(rows)
    result = []
    for row in rows:
        veto = _replayed_veto(row, reference)
        result.append(row if veto is None else {**row, "competes": False, "veto": veto})
    return result


def vetoed_count(rows: Sequence[dict[str, Any]]) -> int:
    """Finished the final stage, and kept out by the hold-out."""
    return sum(1 for row in replay_veto(rows) if row.get("veto") and not row.get("rejected"))


def rank(rows: Sequence[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    """Best first by ranking score. Only candidates that compete are ranked,
    and a row the hold-out veto keeps out does not (`replay_veto`)."""
    alive = [r for r in replay_veto(rows) if competes(r) and fitness_of(r) is not None]
    alive.sort(key=lambda r: (-(fitness_of(r) or 0.0), str(r.get("id", ""))))
    return alive[:limit]


def lineage_of(
    rows: Sequence[dict[str, Any]], candidate_id: str, limit: int = 3
) -> list[str]:
    """The parent chain, nearest first, bounded so a cycle cannot hang a render."""
    parents = {str(r.get("id")): r.get("parent_id") for r in rows}
    chain: list[str] = []
    seen = {candidate_id}
    current = parents.get(candidate_id)
    while current and len(chain) < limit and current not in seen:
        chain.append(str(current))
        seen.add(str(current))
        current = parents.get(str(current))
    return chain


def novelty_counts(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    """How many children bought nothing, and which filter said so.

    `no_op` and `duplicate` are the structural filter's, refused before any
    evaluation. `behavioural` is the signature filter's: the candidate was
    evaluated at one stage, reported KPIs identical to an earlier candidate's,
    and was stopped there rather than promoted. `near` is the similarity
    gate's, and is the one that is *not* a rejection: the candidate was
    evaluated normally and merely carries a flag.
    """
    counts = {"no_op": 0, "duplicate": 0, "behavioural": 0, "near": 0, "rejected": 0}
    for row in rows:
        if row.get("rejected"):
            counts["rejected"] += 1
        kind = row.get("novelty")
        if kind in counts:
            counts[kind] += 1
    return counts


def unfinished_count(rows: Sequence[dict[str, Any]]) -> int:
    """Evaluated, not rejected, and still not ranked because it did not finish:
    the evaluation failed or timed out, the candidate was not promoted to the
    final stage, the final stage was skipped by the daily cap, or the hold-out
    run failed. A candidate kept out by the hold-out veto finished, and is
    counted apart (`vetoed_count`)."""
    return sum(
        1
        for row in replay_veto(rows)
        if not row.get("rejected") and not row.get("competes", True) and not row.get("veto")
    )


def seed_private(rows: Sequence[dict[str, Any]]) -> float | None:
    """The seed's private score: what the `!` flag and `evaluate.holdout_veto`
    compare every hold-out with. It is the **last** seed row's (an aborted seed
    is evaluated again, and the attempt that worked is the reference); `None`
    when that row has no private score, or there is no seed row."""
    return _last_seed_score(rows, "private_score")


def seed_public(rows: Sequence[dict[str, Any]]) -> float | None:
    """The same seed row's public score: what the veto's noise margin rescales
    the public run's noise by (`Cascade.holdout_margin`)."""
    return _last_seed_score(rows, "public_score")


def _last_seed_score(rows: Sequence[dict[str, Any]], key: str) -> float | None:
    for row in reversed(rows):
        if row.get("operator") == "human-seed":
            value = row.get(key)
            return None if value is None else float(value)
    return None


def _kept_out_lines(rows: Sequence[dict[str, Any]]) -> list[str]:
    """The candidates the hold-out veto keeps out, each with the `!` flag:
    finished, not ranked, and named, because they are the flag's condition."""
    kept = [r for r in rows if r.get("veto") and not r.get("rejected")]
    if not kept:
        return []
    lines = [
        f"{len(kept)} kept out by the hold-out and not ranked: their hold-out score is "
        "below the starting point's."
    ]
    lines.extend(
        f"- {r.get('id', '?')} {HOLDOUT_FLAG} score {_cell(r.get('score'), 6)}: {r['veto']}"
        for r in kept[:10]
    )
    if len(kept) > 10:
        lines.append(f"- and {len(kept) - 10} more")
    return lines


def _flagged(row: Mapping[str, Any], reference: float | None) -> bool:
    """The `!` flag: the veto's condition, with the row's own noise margin."""
    private = row.get("private_score")
    return (
        reference is not None and private is not None and float(private) < reference - _margin(row)
    )


def _cell(value: Any, digits: int = 4) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def _coord(row: Mapping[str, Any]) -> str:
    cell = row.get("cell")
    if not cell:
        return "-"
    return ",".join(str(int(c)) for c in cell)


def render_economics(points: Sequence[GenerationPoint]) -> str:
    """The compact cost-per-progress table that sits under the leaderboard.

    Six columns and nothing else: the question it answers is "should this run
    keep going", and a wider table would bury it.
    """
    if not points:
        return ""
    window = points[-1].window
    lines = [
        "",
        "### Economics",
        "",
        f"| gen | calls | cum USD | best | d best | USD since improvement "
        f"| USD/unit (window {window}) |",
        "|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for point in points:
        lines.append(
            "| {gen} | {calls} | {usd:.4f} | {best} | {delta} | {since:.4f} "
            "| {rate} |".format(
                gen=point.generation,
                calls=point.cumulative_calls,
                usd=point.cumulative_usd,
                best="-" if point.best is None else f"{point.best:.6g}",
                delta=f"{point.delta_best:+.4g}" if point.delta_best else "-",
                since=point.usd_since_improvement,
                rate=(
                    f"{point.usd_per_gain:.4f}"
                    if point.usd_per_gain is not None
                    else "-"
                ),
            )
        )
    return "\n".join(lines)


def render_markdown(
    rows: Sequence[dict[str, Any]],
    limit: int = 20,
    usage: Sequence[dict[str, Any]] | None = None,
    window: int = DEFAULT_WINDOW,
    *,
    reevaluations: Sequence[Mapping[str, Any]] = (),
    holdout_penalty: float = 0.0,
) -> str:
    """A leaderboard that fits in a terminal.

    `usage` is optional: pass `ledger.usage()` to get the economics table as
    well. Without it the board is exactly the Phase B one. With
    `reevaluations`, `rows` are the rows as written: the board ranks them
    pooled (`view_rows`) and the economics table is the search's own series.
    """
    written = replay_veto(rows)
    rows = view_rows(rows, reevaluations, holdout_penalty=holdout_penalty) if reevaluations else written
    top = rank(rows, limit)
    kept_out = _kept_out_lines(rows)
    if not top:
        return "\n".join(["No scored candidates yet.", *kept_out])
    reference = seed_private(rows)
    header = (
        "| # | id | gen | rank | score | cell | operator | model | lineage | gap | USD |\n"
        "|--:|---|--:|--:|--:|---|---|---|---|--:|--:|"
    )
    lines = [header]
    any_flag = False
    for i, row in enumerate(top, start=1):
        flag = _flagged(row, reference)
        any_flag = any_flag or flag
        chain = lineage_of(rows, str(row.get("id", "")))
        lines.append(
            "| {i} | {id}{flag} | {gen} | {fit} | {score} | {cell} | {op} | {model} "
            "| {lineage} | {gap} | {usd} |".format(
                i=i,
                id=row.get("id", "?"),
                flag=f" {HOLDOUT_FLAG}" if flag else "",
                gen=row.get("generation", "-"),
                fit=_cell(fitness_of(row), 6),
                score=_cell(row.get("score"), 6),
                cell=_coord(row),
                op=row.get("operator", "-"),
                model=row.get("model", "-"),
                lineage=" < ".join(chain) or "-",
                gap=_cell(row.get("generalization_gap")),
                usd=f"{float(row.get('usd', 0.0)):.4f}",
            )
        )
    counts = novelty_counts(rows)
    lines.append("")
    lines.append(
        f"{len(rows)} candidate(s) logged, {counts['rejected']} rejected "
        f"({counts['no_op']} no-op, {counts['duplicate']} duplicate, "
        f"{counts['behavioural']} behavioural), {counts['near']} near-duplicate(s) "
        f"evaluated and flagged, {len(top)} shown."
    )
    unfinished = unfinished_count(rows)
    if unfinished:
        lines.append(
            f"{unfinished} did not finish the final stage (evaluation failed, not "
            "promoted, or skipped) and are not ranked: their scores are not "
            "comparable with a full evaluation's."
        )
    lines.extend(kept_out)
    if any_flag or kept_out:
        lines.append(
            f"{HOLDOUT_FLAG} private hold-out score is below the seed's: the gain "
            "may not generalise."
        )
    if usage is not None:
        economics = render_economics(series(written, usage, window=window, reevaluations=reevaluations,
                                            holdout_penalty=holdout_penalty))
        if economics:
            lines.append(economics)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

_HTML_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<title>evolvekit leaderboard</title>
<style>
 :root {{ color-scheme: light dark; --fg:#111; --bg:#fff; --mut:#666; --line:#d8d8d8; --acc:#0a7; --warn:#c60; --cell:#eef6f3; }}
 @media (prefers-color-scheme: dark) {{
   :root {{ --fg:#e6e6e6; --bg:#14171a; --mut:#9aa; --line:#2c3238; --cell:#18262a; }}
 }}
 body {{ font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        margin: 2rem auto; max-width: 1200px; padding: 0 1rem;
        color: var(--fg); background: var(--bg); }}
 h1 {{ font-size: 1.2rem; }}
 h2 {{ font-size: 1rem; margin-top: 2rem; }}
 .stats {{ display:flex; gap:1.5rem; flex-wrap:wrap; margin: 1rem 0 1.5rem; }}
 .stat b {{ display:block; font-size:1.3rem; color: var(--acc); }}
 .stat span {{ color: var(--mut); font-size: .8rem; text-transform: uppercase; }}
 .wrap {{ overflow-x:auto; }}
 table {{ border-collapse: collapse; width: 100%; }}
 th, td {{ text-align: left; padding: .35rem .6rem; border-bottom: 1px solid var(--line); white-space: nowrap; }}
 th {{ color: var(--mut); font-weight: 600; }}
 td.num {{ text-align: right; }}
 .bar {{ display:inline-block; height:.55rem; background: var(--acc); border-radius:2px; vertical-align: middle; }}
 .flag {{ color: var(--warn); font-weight: 700; }}
 .grid td {{ border: 1px solid var(--line); vertical-align: top; white-space: normal;
             min-width: 8rem; }}
 .grid td.full {{ background: var(--cell); }}
 .grid .id {{ display:block; }}
 .grid .meta {{ color: var(--mut); font-size: .8rem; }}
 h3.lin {{ font-size: .85rem; font-weight: 600; margin: 1.2rem 0 .3rem; }}
 .lin {{ color: var(--mut); }}
 footer {{ color: var(--mut); margin-top: 2rem; font-size: .85rem; }}
 .chart {{ width: 100%; height: auto; max-width: 760px; }}
 .chart .axis {{ stroke: var(--line); stroke-width: 1; }}
 .chart .line {{ fill: none; stroke: var(--acc); stroke-width: 2; }}
 .chart .dot {{ fill: var(--acc); }}
 .chart .dot.up {{ fill: var(--warn); }}
 .chart text {{ fill: var(--mut); font-size: 10px; font-family: ui-monospace, Menlo, Consolas, monospace; }}
</style>
<h1>evolvekit leaderboard</h1>
<div class="stats">
  <div class="stat"><b>{n_total}</b><span>candidates</span></div>
  <div class="stat"><b>{n_rejected}</b><span>rejected</span></div>
  <div class="stat"><b>{n_noop}</b><span>no-ops</span></div>
  <div class="stat"><b>{n_dup}</b><span>duplicates</span></div>
  <div class="stat"><b>{n_behav}</b><span>behavioural twins</span></div>
  <div class="stat"><b>{n_near}</b><span>near-duplicates</span></div>
  <div class="stat"><b>{n_vetoed}</b><span>kept out by the hold-out</span></div>
  <div class="stat"><b>{best}</b><span>best rank score</span></div>
  <div class="stat"><b>{cells}</b><span>archive cells</span></div>
  <div class="stat"><b>{generations}</b><span>generations</span></div>
  <div class="stat"><b>${usd}</b><span>spent</span></div>
</div>
<div class="wrap">
<table>
<thead><tr><th>#</th><th>id</th><th class="num">gen</th><th class="num">rank</th>
<th></th><th class="num">score</th><th>cell</th><th>operator</th><th>model</th>
<th>lineage</th><th>inspirations</th><th class="num">gap</th>
<th class="num">tok in/out</th><th class="num">USD</th></tr></thead>
<tbody>
{body}
</tbody>
</table>
</div>
{kept_out}
{grid}
{economics}
<footer>Rank is the public score, less the hold-out penalty when one is set;
score is the raw public one. <span class="flag">{flag}</span> marks a candidate
whose private hold-out score is below the seed's: the hold-out veto keeps it out
of the ranking, and it is listed under the table. Generated from runs.jsonl and
archive.json.</footer>
<script id="rows" type="application/json">{rows_json}</script>
"""


def _grid_cell(entry: Mapping[str, Any] | None) -> str:
    if entry is None:
        return '<td class="empty">&middot;</td>'
    fitness = entry.get("fitness")
    return (
        '<td class="full">'
        f'<span class="id">{html.escape(str(entry.get("elite", "?")))}</span>'
        f'<span class="meta">rank '
        f'{"-" if fitness is None else f"{float(fitness):.6g}"}<br>'
        f'{entry.get("occupants", 1)} tried, {entry.get("children", 0)} child(ren)'
        "</span></td>"
    )


def _grid_table(
    axes: Sequence[Mapping[str, Any]],
    by_coord: Mapping[tuple[int, ...], Mapping[str, Any]],
    rest: tuple[int, ...] = (),
) -> str:
    """One 2-D table over the first one or two axes, with `rest` held fixed."""
    rows_axis = axes[0]
    row_kpi = html.escape(str(rows_axis.get("kpi")))
    n_rows = int(rows_axis.get("bins", 1))
    if len(axes) == 1:
        header = "".join(f"<th>{row_kpi} bin {i}</th>" for i in range(n_rows))
        body = "<tr>" + "".join(
            _grid_cell(by_coord.get((i,) + rest)) for i in range(n_rows)
        ) + "</tr>"
    else:
        cols_axis = axes[1]
        col_kpi = html.escape(str(cols_axis.get("kpi")))
        n_cols = int(cols_axis.get("bins", 1))
        header = "<th></th>" + "".join(
            f"<th>{col_kpi} {j}</th>" for j in range(n_cols)
        )
        body = "".join(
            f"<tr><th>{row_kpi} {i}</th>"
            + "".join(
                _grid_cell(by_coord.get((i, j) + rest)) for j in range(n_cols)
            )
            + "</tr>"
            for i in range(n_rows)
        )
    return (
        f'<table class="grid"><thead><tr>{header}</tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def _grid_html(archive: Mapping[str, Any] | None) -> str:
    """The MAP-Elites grid, drawn with table cells and no external anything.

    Two axes fit on a page. More than two do not, and Phase B's answer -- draw
    the first two and collapse the rest to bin 0 -- silently hid every elite
    that was not in that slice. So a grid with three or more axes is drawn as a
    *list of 2-D slices*: one table per occupied combination of the trailing
    axes, labelled with the coordinates it holds fixed. Only occupied slices
    are drawn, which is what keeps the page finite when there are four axes of
    four bins each.
    """
    if not archive:
        return ""
    axes = list(archive.get("descriptors") or [])
    if not axes:
        return ""
    by_coord = {
        tuple(int(i) for i in c.get("coord", [])): c
        for c in (archive.get("cells") or [])
    }
    occupancy = html.escape(str(archive.get("occupancy", "")))
    parts = [
        "<h2>Archive grid</h2>",
        f'<p class="lin">{occupancy}</p>',
        '<div class="wrap">',
    ]
    if len(axes) <= 2:
        parts.append(_grid_table(axes, by_coord))
    else:
        trailing = axes[2:]
        slices = sorted({coord[2:] for coord in by_coord if len(coord) == len(axes)})
        parts.append(
            f'<p class="lin">{len(slices)} occupied slice(s) of '
            f'{" x ".join(html.escape(str(a.get("kpi"))) for a in trailing)}.</p>'
        )
        for rest in slices:
            label = ", ".join(
                f"{html.escape(str(axis.get('kpi')))} {index}"
                for axis, index in zip(trailing, rest)
            )
            parts.append(f'<h3 class="lin">{label}</h3>')
            parts.append(_grid_table(axes[:2], by_coord, rest))
    parts.append("</div>")
    return "\n".join(parts)


CHART_W, CHART_H = 760, 260
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 64, 18, 18, 42


def economics_svg(points: Sequence[GenerationPoint]) -> str:
    """Best score against cumulative USD, drawn as inline SVG. No libraries.

    x is money, y is score, one dot per generation and a label under the ones
    that fit. Reading it left to right is reading the run's exchange rate: a
    steep climb is cheap progress, a long flat run to the right is money going
    nowhere. A generation that improved the archive-best is drawn in the
    warning colour so the plateaus between improvements are visible as gaps.
    """
    scored = [p for p in points if p.best is not None]
    if len(scored) < 2:
        return (
            '<h2>Economics</h2>\n<p class="lin">Not enough generations to '
            "plot yet.</p>"
        )
    xs = [p.cumulative_usd for p in scored]
    ys = [float(p.best) for p in scored]  # type: ignore[arg-type]
    x_lo, x_hi = min(xs), max(xs)
    y_lo, y_hi = min(ys), max(ys)
    x_span = (x_hi - x_lo) or 1.0
    y_span = (y_hi - y_lo) or 1.0
    plot_w = CHART_W - _PAD_L - _PAD_R
    plot_h = CHART_H - _PAD_T - _PAD_B

    def px(value: float) -> float:
        return _PAD_L + (value - x_lo) / x_span * plot_w

    def py(value: float) -> float:
        return _PAD_T + plot_h - (value - y_lo) / y_span * plot_h

    path = " ".join(
        f"{'M' if i == 0 else 'L'}{px(x):.1f},{py(y):.1f}"
        for i, (x, y) in enumerate(zip(xs, ys))
    )
    every = max(1, (len(scored) + 7) // 8)
    parts = [
        "<h2>Economics</h2>",
        f'<svg class="chart" viewBox="0 0 {CHART_W} {CHART_H}" role="img"'
        ' aria-label="best score against cumulative USD">',
        f'<line class="axis" x1="{_PAD_L}" y1="{_PAD_T}" x2="{_PAD_L}"'
        f' y2="{_PAD_T + plot_h}"/>',
        f'<line class="axis" x1="{_PAD_L}" y1="{_PAD_T + plot_h}"'
        f' x2="{CHART_W - _PAD_R}" y2="{_PAD_T + plot_h}"/>',
        f'<path class="line" d="{path}"/>',
    ]
    for index, point in enumerate(scored):
        x, y = px(point.cumulative_usd), py(float(point.best))  # type: ignore[arg-type]
        classes = "dot up" if point.improved else "dot"
        parts.append(
            f'<circle class="{classes}" cx="{x:.1f}" cy="{y:.1f}" r="3">'
            f"<title>gen {point.generation}: best "
            f"{float(point.best):.6g} at ${point.cumulative_usd:.4f}"  # type: ignore[arg-type]
            f" ({point.cumulative_calls} call(s))</title></circle>"
        )
        if index % every == 0 or index == len(scored) - 1:
            parts.append(
                f'<text x="{x:.1f}" y="{_PAD_T + plot_h + 14:.0f}"'
                f' text-anchor="middle">g{point.generation}</text>'
            )
    parts.extend(
        [
            f'<text x="{_PAD_L - 6}" y="{_PAD_T + 4}" text-anchor="end">'
            f"{y_hi:.6g}</text>",
            f'<text x="{_PAD_L - 6}" y="{_PAD_T + plot_h + 4}" text-anchor="end">'
            f"{y_lo:.6g}</text>",
            f'<text x="{_PAD_L}" y="{CHART_H - 8}" text-anchor="start">'
            f"${x_lo:.4f}</text>",
            f'<text x="{CHART_W - _PAD_R}" y="{CHART_H - 8}" text-anchor="end">'
            f"${x_hi:.4f}</text>",
            "</svg>",
        ]
    )
    last = points[-1]
    parts.append(
        '<p class="lin">x: cumulative USD &middot; y: best ranking score '
        f"&middot; filled-warning dots are generations that improved it. "
        f"Last window ({last.window} generation(s)): {html.escape(last.gain_line)}; "
        f"${last.usd_since_improvement:.4f} spent since the best last moved.</p>"
    )
    return "\n".join(parts)


def render_html(
    rows: Sequence[dict[str, Any]],
    limit: int = 50,
    archive: Mapping[str, Any] | None = None,
    usage: Sequence[dict[str, Any]] | None = None,
    window: int = DEFAULT_WINDOW,
    *,
    reevaluations: Sequence[Mapping[str, Any]] = (),
    holdout_penalty: float = 0.0,
) -> str:
    """Self-contained dashboard: no CDN, no fetch, works from file://.
    `reevaluations`: as `render_markdown`."""
    written = replay_veto(rows)
    rows = view_rows(rows, reevaluations, holdout_penalty=holdout_penalty) if reevaluations else written
    top = rank(rows, limit)
    scores = [fitness_of(r) or 0.0 for r in top]
    best = max(scores) if scores else None
    worst = min(scores) if scores else None
    span = (
        (best - worst)
        if (best is not None and worst is not None and best > worst)
        else 1.0
    )
    reference = seed_private(rows)

    body_lines = []
    for i, row in enumerate(top, start=1):
        fitness = fitness_of(row) or 0.0
        width = max(2.0, 100.0 * (fitness - (worst or 0.0)) / span) if scores else 2.0
        chain = lineage_of(rows, str(row.get("id", "")))
        flag = (
            f' <span class="flag">{HOLDOUT_FLAG}</span>'
            if _flagged(row, reference)
            else ""
        )
        body_lines.append(
            "<tr>"
            f'<td class="num">{i}</td>'
            f"<td>{html.escape(str(row.get('id', '?')))}{flag}</td>"
            f'<td class="num">{html.escape(str(row.get("generation", "-")))}</td>'
            f'<td class="num">{fitness:.6g}</td>'
            f'<td><span class="bar" style="width:{width:.0f}px"></span></td>'
            f'<td class="num">{_cell(row.get("score"), 6)}</td>'
            f"<td>{html.escape(_coord(row))}</td>"
            f"<td>{html.escape(str(row.get('operator', '-')))}</td>"
            f"<td>{html.escape(str(row.get('model', '-')))}</td>"
            f'<td class="lin">{html.escape(" < ".join(chain) or "-")}</td>'
            f'<td class="lin">{html.escape(",".join(row.get("inspiration_ids") or []) or "-")}</td>'
            f'<td class="num">{_cell(row.get("generalization_gap"))}</td>'
            f'<td class="num">{row.get("tokens_in", 0)}/{row.get("tokens_out", 0)}</td>'
            f'<td class="num">{float(row.get("usd", 0.0)):.4f}</td>'
            "</tr>"
        )

    counts = novelty_counts(rows)
    generations = max((int(r.get("generation", 0)) for r in rows), default=0)
    return _HTML_TEMPLATE.format(
        n_total=len(rows),
        n_rejected=counts["rejected"],
        n_noop=counts["no_op"],
        n_dup=counts["duplicate"],
        n_behav=counts["behavioural"],
        n_near=counts["near"],
        n_vetoed=vetoed_count(rows),
        kept_out="".join(
            f'<p class="kept">{html.escape(line)}</p>' for line in _kept_out_lines(rows)
        ),
        best="-" if best is None else f"{best:.6g}",
        cells=len((archive or {}).get("cells") or []),
        generations=generations,
        usd=f"{sum(float(r.get('usd', 0.0)) for r in rows):.4f}",
        body="\n".join(body_lines)
        or '<tr><td colspan="14">No scored candidates yet.</td></tr>',
        grid=_grid_html(archive),
        economics=(
            economics_svg(series(written, usage, window=window, reevaluations=reevaluations,
                                 holdout_penalty=holdout_penalty))
            if usage is not None
            else ""
        ),
        flag=HOLDOUT_FLAG,
        rows_json=html.escape(json.dumps(top, default=str)),
    )
