"""The leaderboard projections: lineage, cells, counts and the archive grid."""

from __future__ import annotations

from evolvekit.leaderboard import (
    lineage_of,
    novelty_counts,
    rank,
    render_html,
    render_markdown,
)


def row(cid: str, **overrides) -> dict:
    base = {
        "id": cid,
        "generation": 1,
        "operator": "rewrite",
        "model": "fake-small",
        "score": -5.0,
        "ranking_score": -5.0,
        "rejected": False,
        "novelty": None,
        "parent_id": None,
        "inspiration_ids": [],
        "cell": [1, 2],
        "generalization_gap": None,
        "public_score": None,
        "private_score": None,
        "tokens_in": 10,
        "tokens_out": 5,
        "usd": 0.001,
    }
    base.update(overrides)
    return base


ARCHIVE = {
    "occupancy": "2/8 cells occupied (complexity[2] x behaviour[4])",
    "descriptors": [
        {"kpi": "complexity", "bins": 2, "range": [0, 40], "auto": False},
        {"kpi": "behaviour", "bins": 4, "range": [0, 1], "auto": False},
    ],
    "cells": [
        {"coord": [0, 0], "elite": "a", "fitness": -5.0, "occupants": 2, "children": 3},
        {"coord": [1, 2], "elite": "b", "fitness": -4.0, "occupants": 1, "children": 0},
    ],
}


# -- ranking and counts ----------------------------------------------------


def test_rejected_rows_never_appear():
    rows = [row("a"), row("b", rejected=True, score=-1000.0)]
    assert [r["id"] for r in rank(rows)] == ["a"]


def test_ties_break_on_id_so_the_order_is_stable():
    rows = [row("b"), row("a")]
    assert [r["id"] for r in rank(rows)] == ["a", "b"]


def test_novelty_counts_split_the_rejections():
    rows = [
        row("a"),
        row("b", rejected=True, novelty="no_op"),
        row("c", rejected=True, novelty="duplicate"),
        row("d", rejected=True, novelty="behavioural"),
        row("e", rejected=True),
        # A near-duplicate is counted but is *not* a rejection.
        row("f", novelty="near", near_twin_id="a", similarity=0.98),
    ]
    assert novelty_counts(rows) == {
        "no_op": 1,
        "duplicate": 1,
        "behavioural": 1,
        "near": 1,
        "rejected": 4,
    }


def test_the_footer_reports_what_the_filter_refused():
    rows = [
        row("a"),
        row("b", rejected=True, novelty="duplicate"),
        row("c", rejected=True, novelty="behavioural"),
    ]
    text = render_markdown(rows)
    assert (
        "3 candidate(s) logged, 2 rejected (0 no-op, 1 duplicate, 1 behavioural), "
        "0 near-duplicate(s) evaluated and flagged, 1 shown." in text
    )


def test_the_html_dashboard_counts_behavioural_twins():
    rows = [row("a"), row("b", rejected=True, novelty="behavioural")]
    page = render_html(rows)
    assert "behavioural twins" in page


def test_an_empty_board_says_so():
    assert render_markdown([]) == "No scored candidates yet."
    assert "No scored candidates yet" in render_html([])


# -- lineage ---------------------------------------------------------------


def test_lineage_walks_back_up_the_chain():
    rows = [row("a"), row("b", parent_id="a"), row("c", parent_id="b")]
    assert lineage_of(rows, "c") == ["b", "a"]
    assert lineage_of(rows, "a") == []


def test_lineage_is_bounded_and_survives_a_cycle():
    rows = [row("a", parent_id="b"), row("b", parent_id="a")]
    assert len(lineage_of(rows, "a", limit=4)) <= 4


def test_lineage_and_cells_reach_the_markdown():
    rows = [row("a"), row("b", parent_id="a", cell=[0, 3], score=-4.0, ranking_score=-4.0)]
    text = render_markdown(rows)
    assert "| 0,3 |" in text
    assert "| a |" in text  # b's lineage column
    assert "cell" in text and "lineage" in text


# -- HTML ------------------------------------------------------------------


def test_the_page_is_self_contained():
    page = render_html([row("a")], archive=ARCHIVE)
    assert page.startswith("<!doctype html>")
    assert "http://" not in page and "https://" not in page
    assert "<script id=\"rows\"" in page


def test_the_grid_draws_one_table_cell_per_archive_cell():
    page = render_html([row("a")], archive=ARCHIVE)
    assert "Archive grid" in page
    assert "2/8 cells occupied" in page
    assert page.count('class="full"') == 2
    assert "3 child(ren)" in page
    # 2 x 4 grid: eight body cells, six of them empty.
    assert page.count("&middot;") == 6


def test_a_single_axis_grid_is_one_row():
    archive = {
        "occupancy": "1/3 cells occupied (complexity[3])",
        "descriptors": [{"kpi": "complexity", "bins": 3, "range": [0, 9], "auto": True}],
        "cells": [{"coord": [2], "elite": "a", "fitness": -1.0, "occupants": 1, "children": 0}],
    }
    page = render_html([row("a")], archive=archive)
    assert "complexity bin 0" in page
    assert page.count('class="full"') == 1


def test_no_archive_means_no_grid():
    page = render_html([row("a")])
    assert "Archive grid" not in page
    assert "0" in page  # the archive-cells stat is still rendered


def test_the_html_escapes_whatever_is_in_a_record():
    page = render_html([row("<script>alert(1)</script>")], archive=ARCHIVE)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_the_holdout_flag_marks_the_row_in_both_renderers():
    seed = row("seed", operator="human-seed", public_score=-5.0, private_score=-5.0)
    bad = row("bad", public_score=-4.0, private_score=-9.0, ranking_score=-9.0)
    assert "bad !" in render_markdown([seed, bad])
    assert 'class="flag"' in render_html([seed, bad])


# -- Phase C: grids with more than two axes -------------------------------


def _archive(descriptors, cells):
    return {
        "descriptors": descriptors,
        "occupancy": f"{len(cells)} cells occupied",
        "cells": cells,
    }


THREE_AXES = [
    {"kpi": "complexity", "bins": 2},
    {"kpi": "openness", "bins": 2},
    {"kpi": "depth", "bins": 3},
]
THREE_AXIS_CELLS = [
    {"coord": [0, 0, 0], "elite": "a", "fitness": 1.0, "occupants": 1, "children": 0},
    {"coord": [1, 1, 0], "elite": "b", "fitness": 2.0, "occupants": 2, "children": 1},
    {"coord": [0, 1, 2], "elite": "c", "fitness": 3.0, "occupants": 1, "children": 0},
]


def test_three_axes_are_drawn_as_one_table_per_occupied_slice():
    page = render_html([], archive=_archive(THREE_AXES, THREE_AXIS_CELLS))
    assert page.count('<table class="grid">') == 2, "one table per occupied slice"
    assert "2 occupied slice(s) of depth" in page
    assert ">depth 0<" in page and ">depth 2<" in page
    assert ">depth 1<" not in page, "an empty slice is not drawn"


def test_no_elite_is_hidden_by_the_slicing():
    """Phase B collapsed the trailing axes to bin 0 and lost everything else."""
    page = render_html([], archive=_archive(THREE_AXES, THREE_AXIS_CELLS))
    for cell in THREE_AXIS_CELLS:
        assert f'>{cell["elite"]}<' in page


def test_four_axes_slice_on_both_trailing_ones():
    axes = [{"kpi": name, "bins": 2} for name in ("a", "b", "c", "d")]
    cells = [
        {"coord": [0, 0, 0, 1], "elite": "x", "fitness": 1.0},
        {"coord": [1, 1, 1, 0], "elite": "y", "fitness": 2.0},
    ]
    page = render_html([], archive=_archive(axes, cells))
    assert page.count('<table class="grid">') == 2
    assert "c 0, d 1" in page and "c 1, d 0" in page


def test_two_axes_are_still_one_table():
    axes = THREE_AXES[:2]
    cells = [{"coord": [0, 0], "elite": "a", "fitness": 1.0}]
    page = render_html([], archive=_archive(axes, cells))
    assert page.count('<table class="grid">') == 1
    assert "occupied slice(s)" not in page


def test_a_grid_with_no_occupied_cells_still_renders():
    page = render_html([], archive=_archive(THREE_AXES, []))
    assert "0 occupied slice(s)" in page
    assert '<table class="grid">' not in page
