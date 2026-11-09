"""Groups of cases (`evolvekit/harness/groups.py`): the features of a case
(declared SQL over the request tables, else row counts and column means), the
tags people give in the Cases list, k-means with the best silhouette, and
`study groups`. The tables come from a fake that writes a SQLite file, so no
solve runs except in the slow test."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from evolvekit.harness import HarnessError, groups
from evolvekit.harness.manifest import load_harness
from evolvekit.harness.study import load_study, save_study
from test_app_results import study_with_runs

ROOT = Path(__file__).resolve().parents[1]
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"


def fake_tables(sizes: dict[str, int]):
    """`tables=` for `features_of` and `compute`: a SQLite file per case with
    a `stops` table of `sizes[case]` rows (stop_id 0.., weight 1, x and y)."""

    def tables(root: Path, case: str, say=None) -> Path:
        if sizes.get(case) is None:
            raise groups.SolveFailed(f"{case}: the solve that shows its tables failed: exit code 1")
        if say is not None:
            say(f"solving {Path(case).stem} for its features …")
        path = Path(root) / "fake-tables" / (Path(case).name + ".sqlite")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE stops (stop_id INTEGER, x REAL, y REAL, weight REAL, tag TEXT)")
        db.executemany("INSERT INTO stops VALUES (?, ?, ?, ?, ?)",
                       [(i, i * 1.5, -i * 0.5, 1.0, "north") for i in range(sizes[case])])
        db.commit()
        db.close()
        return path

    return tables


def study_with_cases(tmp_path: Path, sizes: list[int], held: int) -> Path:
    """A demo study with cases `cases/c0.json`, ... (`{"seed", "cities"}`); the last `held` are held back."""
    from evolvekit.app.store import Home

    home = Home(tmp_path / "home")
    slug = home.create_study("demo-tour", "tune", "Groups")
    root = home.study_root(slug)
    (root / "cases").mkdir(exist_ok=True)
    names = []
    for i, n in enumerate(sizes):
        (root / "cases" / f"c{i}.json").write_text(json.dumps({"seed": i, "cities": n}), encoding="utf-8")
        names.append(f"cases/c{i}.json")
    study = load_study(root)
    study.training, study.test = names[:len(names) - held], names[len(names) - held:]
    save_study(study, root)
    return root


def sizes_of(sizes: list[int]) -> dict[str, int]:
    return {f"cases/c{i}.json": n for i, n in enumerate(sizes)}


def test_default_features(tmp_path):
    root, _ = study_with_runs(tmp_path)
    got = groups.features_of(root, "cases/town-40.json", tables=fake_tables({"cases/town-40.json": 40}))
    assert got == {"rows_stops": 40.0, "mean_stops_stop_id": 19.5, "mean_stops_weight": 1.0}, "x and y are private"


def _add_features(root: Path, block: str) -> None:
    path = root / "harness" / "harness.yaml"
    path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n\n" + block, encoding="utf-8")


def test_declared_features_sql(tmp_path):
    root, _ = study_with_runs(tmp_path)
    path = root / "harness" / "harness.yaml"
    original = path.read_text(encoding="utf-8")
    _add_features(root, 'features:\n  spread:\n    sql: "SELECT MAX(stop_id) - MIN(stop_id) FROM stops"\n'
                        '    describe: how far the stop numbers spread\n')
    harness = load_harness(root / "harness")
    assert harness.features["spread"].describe == "how far the stop numbers spread"
    got = groups.features_of(root, "cases/town-40.json", tables=fake_tables({"cases/town-40.json": 40}))
    assert got == {"spread": 39.0}
    path.write_text(original, encoding="utf-8")
    _add_features(root, 'features:\n  bad:\n    sql: "SELECT COUNT(*) FROM tour"\n')
    with pytest.raises(HarnessError, match=r"features\.bad\.sql: no such table: tour"):
        load_harness(root / "harness")


def test_a_feature_with_an_unknown_key_is_refused(tmp_path):
    root, _ = study_with_runs(tmp_path)
    _add_features(root, 'features:\n  spread:\n    sql: "SELECT 1"\n    label: Spread\n')
    with pytest.raises(HarnessError, match=r"features\.spread: unknown key"):
        load_harness(root / "harness")


def test_kmeans_recovers_two_clusters():
    points = [[0.0, 0.1], [0.2, 0.0], [0.1, 0.2], [5.0, 5.1], [5.2, 4.9], [4.9, 5.0]]
    labels = groups.kmeans(points, 2, seed=0)
    assert labels == [0, 0, 0, 1, 1, 1] and groups.kmeans(points, 2, seed=0) == labels
    flipped = groups.kmeans(points[3:] + points[:3], 2, seed=0)
    assert flipped == [0, 0, 0, 1, 1, 1], "labels are numbered by first appearance"


def test_silhouette_prefers_right_k():
    points = [[0, 0], [0.1, 0], [0, 0.1], [5, 5], [5.1, 5], [5, 5.1], [10, 0], [10.1, 0], [10, 0.1]]
    s2, s3 = (groups.silhouette(points, groups.kmeans(points, k)) for k in (2, 3))
    assert s3 > s2 > 0


def test_standardise_drops_a_column_with_no_spread():
    got = groups.standardise([[1.0, 5.0, 2.0], [3.0, 5.0, 2.0]])
    assert got == [[-1.0], [1.0]]


def test_tags_win_over_features(tmp_path):
    sizes = [20, 22, 24, 80, 82, 84, 21, 83]
    root = study_with_cases(tmp_path, sizes=sizes, held=2)
    for i in (0, 1, 2, 6):
        groups.write_tag(root, f"cases/c{i}.json", "north")      # c6 and c7 are the held-back ones
    for i in (3, 4, 5, 7):
        groups.write_tag(root, f"cases/c{i}.json", "south")
    doc = groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    assert doc["method"] == "tags" and set(doc["groups"]) == {"north", "south"}
    assert doc["groups"]["north"] == {"training": ["cases/c0.json", "cases/c1.json", "cases/c2.json"],
                                      "test": ["cases/c6.json"]}
    assert doc["k"] == 2 and doc["features"]["cases/c3.json"]["rows_stops"] == 80.0
    assert json.loads((root / groups.GROUPS).read_text(encoding="utf-8")) == doc
    assert groups.read_groups(root, load_study(root)) == doc


def test_untagged_cases_form_their_own_group(tmp_path):
    sizes = [20, 22, 24, 80, 82, 84]
    root = study_with_cases(tmp_path, sizes=sizes, held=0)
    for i in (0, 1):
        groups.write_tag(root, f"cases/c{i}.json", "small")
    doc = groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    assert doc["method"] == "tags" and list(doc["groups"]) == ["small", "untagged"]
    assert doc["groups"]["untagged"]["training"] == ["cases/c2.json", "cases/c3.json", "cases/c4.json", "cases/c5.json"]


def test_features_make_two_groups_named_by_their_first_feature(tmp_path):
    sizes = [80, 20, 82, 22, 84, 24, 83, 21]
    root = study_with_cases(tmp_path, sizes=sizes, held=2)
    doc = groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    assert doc["method"] == "features" and doc["k"] == 2 and doc["silhouette"] > 0.5
    assert doc["groups"]["group 1"] == {"training": ["cases/c1.json", "cases/c3.json", "cases/c5.json"],
                                        "test": ["cases/c7.json"]}, "group 1 has the lower first feature"
    assert doc["groups"]["group 2"]["test"] == ["cases/c6.json"]
    assert doc["cases"] == [f"cases/c{i}.json" for i in range(8)]
    assert doc["describe"]["rows_stops"] and set(doc["describe"]) == {"rows_stops", "mean_stops_stop_id",
                                                                     "mean_stops_weight"}
    forced = groups.compute(root, k=3, tables=fake_tables(sizes_of(sizes)))
    assert forced["method"] == "one", "three groups of these cases leave one with fewer than two to learn from"


def test_small_group_collapses_to_one(tmp_path):
    sizes = [20, 22, 80, 82]
    root = study_with_cases(tmp_path, sizes=sizes, held=1)   # 3 to learn from
    doc = groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    assert doc["method"] == "one" and list(doc["groups"]) == ["all"]
    assert "a group needs at least two cases to learn from; all cases are one group" in doc["notes"]
    assert doc["groups"]["all"] == {"training": ["cases/c0.json", "cases/c1.json", "cases/c2.json"],
                                    "test": ["cases/c3.json"]}
    assert doc["k"] == 1 and doc["silhouette"] is None


def test_tags_that_leave_a_group_too_small_collapse_to_one(tmp_path):
    sizes = [20, 22, 80, 82]
    root = study_with_cases(tmp_path, sizes=sizes, held=0)
    groups.write_tag(root, "cases/c0.json", "alone")
    doc = groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    assert doc["method"] == "one"
    assert "a group needs at least two cases to learn from; all cases are one group" in doc["notes"]


def test_tags_file_with_bom_and_crlf(tmp_path):
    root, _ = study_with_runs(tmp_path)
    (root / "cases" / "town-40.json.tags").write_bytes(b"\xef\xbb\xbfbig \r\n")
    assert groups.read_tag(root, "cases/town-40.json") == "big"
    (root / "cases" / "town-40.json.tags").write_bytes(b"\r\n  \r\n\xef\xbb\xbf")
    assert groups.read_tag(root, "cases/town-40.json") == ""
    assert groups.read_tag(root, "cases/region-60.json") == "", "no file: no tag"


def test_write_tag_one_line_and_removal(tmp_path):
    root, _ = study_with_runs(tmp_path)
    groups.write_tag(root, "cases/town-40.json", "  big  ")
    assert (root / "cases" / "town-40.json.tags").read_bytes() == b"big\n"
    with pytest.raises(HarnessError):
        groups.write_tag(root, "cases/town-40.json", "x" * (groups.TAG_MAX + 1))
    with pytest.raises(HarnessError):
        groups.write_tag(root, "cases/town-40.json", "two\nlines")
    assert groups.read_tag(root, "cases/town-40.json") == "big", "a refused tag leaves the old one"
    groups.write_tag(root, "cases/town-40.json", "")
    assert not (root / "cases" / "town-40.json.tags").exists()


def test_tags_file_is_not_a_case(tmp_path):
    root, home = study_with_runs(tmp_path)
    home.set_tag(root.name, "town-40.json", "big")
    assert "town-40.json.tags" not in home.case_names(root.name)
    assert {c["name"]: c["tag"] for c in home.cases(root.name)}["town-40.json"] == "big"


def test_set_tag_answers_the_document_and_a_removed_case_takes_its_tag(tmp_path):
    from evolvekit.app import AppError

    root, home = study_with_runs(tmp_path)
    doc = home.set_tag(root.name, "town-40.json", "big")
    assert {c["name"]: c["tag"] for c in doc["cases"]}["town-40.json"] == "big"
    assert all(c["group"] == "" for c in doc["cases"]), "no groups.json yet"
    with pytest.raises(AppError):
        home.set_tag(root.name, "nowhere.json", "big")
    with pytest.raises(AppError):
        home.set_tag(root.name, "town-40.json", "x" * 41)
    home.delete_case(root.name, "town-40.json")
    assert not (root / "cases" / "town-40.json.tags").exists()
    home.restore_case(root.name, "town-40.json")
    assert groups.read_tag(root, "cases/town-40.json") == "big", "undoing the removal brings the tag back"


def test_case_rows_show_the_group_of_groups_json(tmp_path):
    from evolvekit.app.store import Home

    sizes = [80, 20, 82, 22, 84, 24, 83, 21]
    root = study_with_cases(tmp_path, sizes=sizes, held=2)
    groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    rows = {c["name"]: c["group"] for c in Home(tmp_path / "home").cases(root.name)}
    assert rows["c1.json"] == "group 1" and rows["c0.json"] == "group 2"


def test_groups_json_is_stale_when_cases_change(tmp_path):
    sizes = [80, 20, 82, 22, 84, 24, 83, 21]
    root = study_with_cases(tmp_path, sizes=sizes, held=2)
    groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    assert groups.read_groups(root, load_study(root)) is not None
    (root / "cases" / "c8.json").write_text('{"seed": 8, "cities": 30}', encoding="utf-8")
    study = load_study(root)
    study.training.append("cases/c8.json")
    save_study(study, root)
    assert groups.read_groups(root, load_study(root)) is None


def test_groups_json_is_stale_when_a_case_changes_sides_or_a_tag_is_given(tmp_path):
    sizes = [80, 20, 82, 22, 84, 24, 83, 21]
    root = study_with_cases(tmp_path, sizes=sizes, held=2)
    groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    study = load_study(root)
    study.training, study.test = study.training[:-1] + study.test[:1], study.training[-1:] + study.test[1:]
    assert groups.read_groups(root, study) is None, "a case held back now that the groups learned from"
    groups.write_tag(root, "cases/c0.json", "big")
    assert groups.read_groups(root, load_study(root)) is None, "a tag now decides"


def test_slug_of_a_group():
    assert groups.slug_of("group 1") == "group-1" and groups.slug_of("North Side") == "north-side"


def test_a_feature_may_not_read_a_private_column(tmp_path):
    root, _ = study_with_runs(tmp_path)
    path = root / "harness" / "harness.yaml"
    original = path.read_text(encoding="utf-8")
    for sql in ("SELECT AVG(x) FROM stops", "SELECT MAX(y) FROM orig_stops", "SELECT COUNT(*) FROM stops WHERE x > 3"):
        path.write_text(original, encoding="utf-8")
        _add_features(root, f'features:\n  near:\n    sql: "{sql}"\n')
        with pytest.raises(HarnessError, match=r"features\.near\.sql: reads the private column stops\.[xy]$"):
            load_harness(root / "harness")
    path.write_text(original, encoding="utf-8")
    _add_features(root, 'features:\n  heavy_share:\n    sql: "SELECT AVG(weight > 1) FROM stops"\n')
    assert list(load_harness(root / "harness").features) == ["heavy_share"], "the docs' example loads"
    path.write_text(original, encoding="utf-8")
    _add_features(root, 'features:\n  sneaky:\n    sql: "DELETE FROM stops"\n')
    with pytest.raises(HarnessError, match=r"features\.sneaky\.sql: it may only read the tables"):
        load_harness(root / "harness")


def test_tags_that_share_a_folder_name_are_one_group(tmp_path):
    sizes = [20, 22, 24, 80, 82, 84, 21, 83]
    root = study_with_cases(tmp_path, sizes=sizes, held=2)
    for i, tag in enumerate(["Big", "big", "BIG", "north side", "north-side", "North Side", "big", "north side"]):
        groups.write_tag(root, f"cases/c{i}.json", tag)
    doc = groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    assert doc["method"] == "tags" and list(doc["groups"]) == ["Big", "north side"], "the first spelling names it"
    assert doc["groups"]["Big"] == {"training": ["cases/c0.json", "cases/c1.json", "cases/c2.json"],
                                    "test": ["cases/c6.json"]}
    assert "'big', 'BIG' counted as 'Big': the same group" in doc["notes"]
    assert "'north-side', 'North Side' counted as 'north side': the same group" in doc["notes"]
    assert len({groups.slug_of(name) for name in doc["groups"]}) == len(doc["groups"]), "one folder per group"


def test_a_tag_needs_a_letter_or_a_digit(tmp_path):
    root, _ = study_with_runs(tmp_path)
    with pytest.raises(HarnessError, match="a tag needs a letter or a digit"):
        groups.write_tag(root, "cases/town-40.json", "!!!")
    assert not (root / "cases" / "town-40.json.tags").exists()
    groups.write_tag(root, "cases/town-40.json", "Ä 1")
    assert groups.read_tag(root, "cases/town-40.json") == "Ä 1"


def test_a_hand_written_tag_without_a_letter_counts_as_untagged(tmp_path):
    sizes = [20, 22, 80, 82]
    root = study_with_cases(tmp_path, sizes=sizes, held=0)
    groups.write_tag(root, "cases/c0.json", "small")
    groups.write_tag(root, "cases/c1.json", "small")
    (root / "cases" / "c2.json.tags").write_text("!!!\n", encoding="utf-8")
    doc = groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    assert doc["groups"]["untagged"]["training"] == ["cases/c2.json", "cases/c3.json"]
    assert "cases/c2.json: its tag '!!!' has no letter or digit, so it counts as untagged" in doc["notes"]


def test_one_tag_for_every_case_is_one_group_by_tags(tmp_path, capsys, monkeypatch):
    from evolvekit.cli import main

    sizes = [20, 22, 80, 82]
    root = study_with_cases(tmp_path, sizes=sizes, held=1)
    for i in range(4):
        groups.write_tag(root, f"cases/c{i}.json", "depot")
    monkeypatch.setattr(groups, "case_tables", fake_tables(sizes_of(sizes)))
    assert main(["study", "groups", root.name, "--home", str(tmp_path / "home")]) == 0
    assert capsys.readouterr().out.splitlines()[:2] == ["1 group by the cases' tags:",
                                                        "  depot: 4 cases (3 to learn from, 1 held back)"]


def test_with_tags_a_case_that_cannot_be_solved_only_lacks_its_features(tmp_path):
    sizes = [20, 22, 24, 80, 82, 84]
    root = study_with_cases(tmp_path, sizes=sizes, held=0)
    for i in range(6):
        groups.write_tag(root, f"cases/c{i}.json", "small" if i < 3 else "large")
    solvable = {case: n for case, n in sizes_of(sizes).items() if case != "cases/c4.json"}
    doc = groups.compute(root, tables=fake_tables(solvable))
    assert doc["method"] == "tags" and doc["groups"]["large"]["training"] == ["cases/c3.json", "cases/c4.json",
                                                                               "cases/c5.json"]
    assert "cases/c4.json" not in doc["features"] and "cases/c5.json" in doc["features"]
    assert any(n.startswith("features left out: cases/c4.json: the solve") for n in doc["notes"]), doc["notes"]


def test_features_that_never_vary_make_one_group(tmp_path):
    sizes = [40, 40, 40, 40, 40]
    root = study_with_cases(tmp_path, sizes=sizes, held=1)
    doc = groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    assert doc["method"] == "one" and list(doc["groups"]) == ["all"]
    assert doc["notes"] == ["the cases' features are the same in every case; all cases are one group"]


def test_a_forced_k_that_the_cases_cannot_make_says_so(tmp_path):
    sizes = [20, 20, 20, 20, 80, 80, 80, 80]
    root = study_with_cases(tmp_path, sizes=sizes, held=2)
    doc = groups.compute(root, k=3, tables=fake_tables(sizes_of(sizes)))
    assert "asked for 3 groups; the cases make only 2" in doc["notes"], doc["notes"]
    assert doc["method"] == "features" and doc["k"] == 2


def test_groups_json_is_stale_when_a_case_file_changes(tmp_path):
    import os

    sizes = [80, 20, 82, 22, 84, 24, 83, 21]
    root = study_with_cases(tmp_path, sizes=sizes, held=2)
    groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    assert groups.read_groups(root, load_study(root)) is not None
    case = root / "cases" / "c0.json"
    stat = case.stat()
    os.utime(case, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    assert groups.read_groups(root, load_study(root)) is None, "the same name, another file"


def test_cli_groups_prints_the_groups(tmp_path, capsys, monkeypatch):
    from evolvekit.cli import main

    sizes = [80, 20, 82, 22, 84, 24, 83, 21]
    root = study_with_cases(tmp_path, sizes=sizes, held=2)
    monkeypatch.setattr(groups, "case_tables", fake_tables(sizes_of(sizes)))
    home = str(tmp_path / "home")
    assert main(["study", "groups", root.name, "--home", home]) == 0
    said = capsys.readouterr()
    lines = said.out.splitlines()
    assert lines[0].startswith("2 groups by the cases' features (silhouette 0.") and lines[0].endswith("):")
    assert lines[1:] == ["  group 1: 4 cases (3 to learn from, 1 held back)",
                         "  group 2: 4 cases (3 to learn from, 1 held back)", "wrote groups.json"]
    err = said.err.splitlines()
    assert err[:8] == [f"solving c{i} for its features …" for i in range(8)], "one line per case, on stderr"
    assert err[8:] == ["note: mean_stops_weight: the same in every case, so not used to tell them apart"]
    assert main(["study", "groups", root.name, "--json", "--home", home]) == 0
    assert json.loads(capsys.readouterr().out)["method"] == "features"
    for k in ("6", "1"):
        assert main(["study", "groups", root.name, "--k", k, "--home", home]) == 2
        assert "--k: 2 to 5 groups" in capsys.readouterr().err
    assert main(["study", "groups", root.name, "--k", "5", "--home", home]) == 0
    said = capsys.readouterr()
    assert said.out.splitlines()[0] == "1 group:"
    assert "note: a group needs at least two cases to learn from; all cases are one group" in said.err


def test_cli_groups_a_case_that_cannot_be_solved_is_exit_1(tmp_path, capsys, monkeypatch):
    from evolvekit.cli import main

    sizes = [80, 20, 82, 22]
    root = study_with_cases(tmp_path, sizes=sizes, held=0)
    monkeypatch.setattr(groups, "case_tables", fake_tables({"cases/c0.json": 80}))
    assert main(["study", "groups", root.name, "--home", str(tmp_path / "home")]) == 1
    assert "error: cases/c1.json: the solve that shows its tables failed" in capsys.readouterr().err


def test_no_tag_and_no_groups_while_a_run_is_going(tmp_path, capsys, monkeypatch):
    from evolvekit.app import AppError, jobs
    from evolvekit.app.store import Home
    from evolvekit.cli import main

    root = study_with_cases(tmp_path, sizes=[80, 20, 82, 22], held=0)
    monkeypatch.setattr(jobs, "running", lambda _root: True)
    assert main(["study", "groups", root.name, "--home", str(tmp_path / "home")]) == 1
    assert "a run is going: tag or group the cases after it ends" in capsys.readouterr().err
    with pytest.raises(AppError) as refused:
        Home(tmp_path / "home").set_tag(root.name, "c0.json", "big")
    assert refused.value.status == 409 and "a run is going" in str(refused.value)
    assert not (root / groups.GROUPS).exists() and groups.read_tag(root, "cases/c0.json") == ""


def test_the_case_tables_are_kept_until_the_case_changes(tmp_path, monkeypatch):
    from evolvekit.harness import execute

    root, home = study_with_runs(tmp_path)
    solves = []

    def solve_once(root_, study, harness, folder, *, case, values, study_run, time_limit, tables_out, up):
        solves.append((case, time_limit, tables_out, up, values))
        folder.mkdir(parents=True, exist_ok=True)
        assert (folder / up / case).resolve() == (root / case).resolve(), "the case is reached from the folder"
        sqlite3.connect(folder / "tables.sqlite").close()
        return {"ok": True}

    monkeypatch.setattr(execute, "_solve_once", solve_once)
    study = load_study(root)
    study.application_path = str(SOLVER)
    save_study(study, root)
    first = groups.case_tables(root, "cases/town-40.json")
    assert first == root / "groups" / "tables" / "town-40.json.sqlite" and first.is_file()
    stamp = json.loads((root / "groups" / "tables" / "town-40.json.json").read_text(encoding="utf-8"))
    assert stamp[2] == str(SOLVER)
    assert groups.case_tables(root, "cases/town-40.json") == first and len(solves) == 1, "kept"
    assert solves[0][1] == 2.0 and solves[0][2] is True, "the preview's time limit, with the tables"
    (root / "cases" / "town-40.json").write_bytes((root / "cases" / "town-40.json").read_bytes() + b" ")
    groups.case_tables(root, "cases/town-40.json")
    assert len(solves) == 2, "solved again once the case changed"


@pytest.mark.slow
def test_cli_groups_json(tmp_path, capsys):
    from evolvekit.cli import main

    home = tmp_path / "home"
    assert main(["study", "new", "Groups", "--harness", "demo-tour", "--template", "tune", "--app", str(SOLVER),
                 "--home", str(home)]) == 0
    slug = capsys.readouterr().out.strip().splitlines()[-1]
    files = []
    for i, cities in enumerate([20, 20, 20, 20, 80, 80, 80, 80]):
        path = tmp_path / "in" / f"c{i}-{cities}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"seed": i, "cities": cities}), encoding="utf-8")
        files.append(str(path))
    assert main(["study", "cases", slug, "--add", *files, "--hold-back", "2", "--home", str(home)]) == 0
    capsys.readouterr()
    assert main(["study", "groups", slug, "--json", "--home", str(home)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["method"] == "features" and doc["k"] == 2, doc
    assert sorted(len(g["training"]) + len(g["test"]) for g in doc["groups"].values()) == [4, 4]
    root = home / "studies" / slug
    assert json.loads((root / groups.GROUPS).read_text(encoding="utf-8")) == doc
    kept = sorted((root / "groups" / "tables").glob("*.sqlite"))
    assert len(kept) == 8
    before = {p.name: p.stat().st_mtime_ns for p in kept}
    assert main(["study", "groups", slug, "--json", "--home", str(home)]) == 0
    assert {p.name: p.stat().st_mtime_ns for p in kept} == before, "a second call solves nothing"


def _doc(groups_, features=None):
    return {"format": 1, "method": "features" if features else "tags", "k": len(groups_), "groups": groups_,
            "features": features or {}, "notes": []}


def test_a_group_with_one_held_back_case_joins_the_nearest_by_its_features():
    features = {c: {"rows": v} for c, v in {"s1": 20, "s2": 21, "s3": 22, "m1": 40, "m2": 41, "m3": 42, "m4": 43,
                                             "l1": 90, "l2": 91, "l3": 92, "l4": 93}.items()}
    doc = _doc({"small": {"training": ["s1", "s2"], "test": ["s3"]},
                "mid": {"training": ["m1", "m2"], "test": ["m3", "m4"]},
                "large": {"training": ["l1", "l2"], "test": ["l3", "l4"]}}, features)
    merged = groups.merge_small_groups(doc)
    assert list(merged["groups"]) == ["mid", "large"] and merged["k"] == 2
    assert merged["groups"]["mid"] == {"training": ["m1", "m2", "s1", "s2"], "test": ["m3", "m4", "s3"],
                                       "merged_from": ["small"]}
    assert "small has 1 held-back case, so it joins mid, the nearest by its features" in merged["notes"]
    assert doc["k"] == 3 and "merged_from" not in doc["groups"]["mid"], "the argument is not changed"
    assert groups.merge_small_groups(merged) == merged


def test_without_features_a_small_group_joins_the_one_with_most_held_back_cases():
    """Review Focus 5: tags only, so no distances."""
    doc = _doc({"north": {"training": ["a", "b"], "test": ["c"]}, "south": {"training": ["d", "e"], "test": ["f", "g"]},
                "east": {"training": ["h", "i"], "test": ["j", "k", "l"]}})
    merged = groups.merge_small_groups(doc)
    assert list(merged["groups"]) == ["south", "east"] and merged["groups"]["east"]["merged_from"] == ["north"]
    assert "north has 1 held-back case, so it joins east, the one with the most held-back cases" in merged["notes"]
    tiny = groups.merge_small_groups(_doc({"x": {"training": ["a", "b"], "test": ["c"]},
                                           "y": {"training": ["d", "e"], "test": []}}))
    assert list(tiny["groups"]) == ["x"] and tiny["k"] == 1, "every group too small: one group"


def test_a_tie_for_the_most_held_back_cases_goes_to_the_first_and_merged_names_are_kept():
    doc = _doc({"a": {"training": ["a1", "a2"], "test": ["a3", "a4"]}, "b": {"training": ["b1", "b2"], "test": ["b3", "b4"]},
                "c": {"training": ["c1", "c2"], "test": ["c3"]}, "d": {"training": ["d1", "d2"], "test": []}})
    merged = groups.merge_small_groups(doc)
    # d (fewest) joins a (tie with b, the first wins); then c joins a too (a has the most).
    assert list(merged["groups"]) == ["a", "b"] and merged["groups"]["a"]["merged_from"] == ["d", "c"]
    assert merged["groups"]["a"]["test"] == ["a3", "a4", "c3"]
    assert groups.merge_small_groups(_doc({"only": {"training": ["a", "b"], "test": ["c"]}}))["k"] == 1


def test_a_merged_tag_still_gets_a_rule_and_so_does_the_all_merged_case():
    merged = groups.merge_small_groups(_doc({"north": {"training": ["a", "b"], "test": ["c"]},
                                             "south": {"training": ["d", "e"], "test": ["f", "g"]}}))
    doc = {**merged, "method": "tags"}
    assert groups.rule_lines(doc, {"south": "use"}) == ["For cases tagged south or north, use the settings of south."]
    every = groups.merge_small_groups(_doc({"x": {"training": ["a", "b"], "test": ["c"]},
                                            "untagged": {"training": ["d", "e"], "test": []}}))
    assert groups.rule_lines(every, {"x": "keep"}) == [
        "For cases tagged x or with no tag, use today's settings (x: not shown to beat today's)."]
