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
    assert doc["format"] == 2 and doc["assigned"] == "stump"
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


def test_one_case_to_learn_from_is_one_group_without_saying_the_cases_are_alike(tmp_path):
    """Pre-merge testing: the groups are made from the cases to learn from alone, so one such case
    has no spread -- which says nothing about the held-back cases, which differ here."""
    sizes = [40, 90, 150]
    root = study_with_cases(tmp_path, sizes=sizes, held=2)
    doc = groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    assert doc["method"] == "one" and list(doc["groups"]) == ["all"]
    assert doc["notes"] == [groups.ONE_GROUP]


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


def test_cli_groups_says_why_the_cases_are_one_group(tmp_path, capsys, monkeypatch):
    from evolvekit.cli import main

    sizes = [30, 40, 50, 60]  # the demo tour's samples: 0.455, below 0.5
    root = study_with_cases(tmp_path, sizes=sizes, held=0)
    monkeypatch.setattr(groups, "case_tables", fake_tables(sizes_of(sizes)))
    assert main(["study", "groups", root.name, "--home", str(tmp_path / "home")]) == 0
    said = capsys.readouterr()
    assert said.out.splitlines()[:2] == ["1 group:", "  all: 4 cases (4 to learn from, 0 held back)"]
    assert ("note: the cases do not fall into clear kinds (how well they separate: 0.45 of 1, at least 0.5 "
            "needed); all cases are one group") in said.err.splitlines()


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


# ---------------------------------------------------------------------------
# A4.2: one group unless the cases separate; held-back cases by the rule
# ---------------------------------------------------------------------------

DEMO_SAMPLES = ROOT / "harnesses" / "demo-tour" / "samples"
PYVRP_SAMPLES = ROOT / "harnesses" / "pyvrp" / "samples"


def samples_study(tmp_path: Path, harness: str, samples: list[Path], held: list[str]) -> Path:
    """A study of `harness` with the sample files copied into `cases/` (no solve); the names in `held` held back."""
    from evolvekit.app.store import Home

    home = Home(tmp_path / "home")
    slug = home.create_study(harness, "tune", "Samples")
    root = home.study_root(slug)
    (root / "cases").mkdir(exist_ok=True)
    names = []
    for sample in samples:
        (root / "cases" / sample.name).write_bytes(sample.read_bytes())
        names.append(f"cases/{sample.name}")
    study = load_study(root)
    study.training = [c for c in names if Path(c).name not in held]
    study.test = [c for c in names if Path(c).name in held]
    save_study(study, root)
    return root


def test_the_demo_tour_samples_make_one_group(tmp_path):
    """Acceptance (§7 A4.2): the four demo-tour samples (30, 40, 50, 60 stops, all learned from).
    Fast: fake_tables with the samples' own stop counts read from harnesses/demo-tour/samples/*.json
    (the fake's features equal the real tables': rows_stops, mean_stops_stop_id, mean_stops_weight).
    Best silhouette 0.455 (k = 2) < 0.5 -> method "one"."""
    samples = sorted(DEMO_SAMPLES.glob("*.json"))
    stops = {f"cases/{p.name}": len(json.loads(p.read_text(encoding="utf-8"))["stops"]) for p in samples}
    assert sorted(stops.values()) == [30, 40, 50, 60]
    root = samples_study(tmp_path / "all", "demo-tour", samples, held=[])
    doc = groups.compute(root, tables=fake_tables(stops))
    assert doc["method"] == "one" and list(doc["groups"]) == ["all"] and doc["format"] == 2
    assert doc["assigned"] == "one" and "stump" not in doc and doc["min_silhouette"] == 0.5
    assert doc["silhouette"] == pytest.approx(0.455, abs=0.0005), doc["silhouette"]
    assert any(n.startswith("the cases do not fall into clear kinds (how well they separate: 0.4")
               and n.endswith(" of 1, at least 0.5 needed); all cases are one group") for n in doc["notes"]), doc["notes"]
    # One held back: two groups of two to learn from cannot be made from three.
    root = samples_study(tmp_path / "held", "demo-tour", samples, held=["town-40.json"])
    doc = groups.compute(root, tables=fake_tables(stops))
    assert doc["method"] == "one" and doc["groups"]["all"]["test"] == ["cases/town-40.json"]
    assert groups.ONE_GROUP in doc["notes"]


@pytest.mark.slow
def test_the_demo_tour_samples_make_one_group_from_a_real_solve(tmp_path):
    """The same through `Home.use_samples` and `case_tables` (the cli-solver, the preview's time limit)."""
    from evolvekit.app.store import Home

    home = Home(tmp_path / "home")
    slug = home.create_study("demo-tour", "tune", "Samples")
    root = home.study_root(slug)
    study = load_study(root)
    study.application_path = str(SOLVER)
    save_study(study, root)
    assert sorted(home.use_samples(slug)) == sorted(p.name for p in DEMO_SAMPLES.glob("*.json"))
    doc = groups.compute(root)
    assert doc["method"] == "one" and list(doc["groups"]) == ["all"], doc
    assert sorted(row["rows_stops"] for row in doc["features"].values()) == [30, 40, 50, 60]
    assert doc["silhouette"] == pytest.approx(0.455, abs=0.0005), doc["silhouette"]


def test_the_pyvrp_samples_keep_their_tag_groups(tmp_path):
    """Acceptance: the ten pyvrp samples, tagged by the test from each file's meta.scenario.depots
    ("two depots": city, lake, suburbs, towns, valley; "three depots": the other five), two of each
    held back. compute(features=False) solves nothing (pyvrp is not installed here): method "tags",
    groups exactly the tags, held-back cases by their tags, assigned "tags", no stump, no accuracy;
    merge_small_groups leaves it as it is."""
    samples = sorted(PYVRP_SAMPLES.glob("*.json"))
    depots = {p.name: json.loads(p.read_text(encoding="utf-8"))["meta"]["scenario"]["depots"] for p in samples}
    tag_of = {name: {2: "two depots", 3: "three depots"}[n] for name, n in depots.items()}
    assert sorted(n for n, t in tag_of.items() if t == "two depots") == [
        "city-60.json", "lake-140.json", "suburbs-180.json", "towns-80.json", "valley-120.json"]
    held = [n for t in ("two depots", "three depots") for n in [m for m in sorted(tag_of) if tag_of[m] == t][-2:]]
    root = samples_study(tmp_path, "pyvrp", samples, held=held)
    for name, tag in tag_of.items():
        groups.write_tag(root, f"cases/{name}", tag)

    def no_solve(_root, case, say=None):
        raise AssertionError(f"{case} was solved")

    doc = groups.compute(root, tables=no_solve, features=False)
    assert doc["method"] == "tags" and doc["assigned"] == "tags" and doc["format"] == 2
    assert "stump" not in doc and "accuracy" not in doc
    assert list(doc["groups"]) == ["three depots", "two depots"]
    for name, part in doc["groups"].items():
        assert {Path(c).name for c in part["training"] + part["test"]} == {n for n, t in tag_of.items() if t == name}
        assert {Path(c).name for c in part["test"]} == {n for n in held if tag_of[n] == name}
    assert groups.merge_small_groups(doc) == doc
    assert groups.read_groups(root, load_study(root)) == doc


def test_clustering_uses_the_training_cases_only(tmp_path):
    training = [20, 22, 24, 80, 82, 84]
    plain = groups.compute(study_with_cases(tmp_path / "plain", training + [83, 21], held=2),
                           tables=fake_tables(sizes_of(training + [83, 21])))
    sizes = training + [500, 21]
    doc = groups.compute(study_with_cases(tmp_path / "outlier", sizes, held=2), tables=fake_tables(sizes_of(sizes)))
    assert doc["method"] == "features" and doc["silhouette"] == plain["silhouette"]
    assert [g["training"] for g in doc["groups"].values()] == [g["training"] for g in plain["groups"].values()]
    assert doc["stump"]["feature"] == "rows_stops" and doc["stump"]["thresholds"] == [52.0]
    assert doc["groups"]["group 2"]["test"] == ["cases/c6.json"], "500 stops: above 52, so group 2 by the rule"
    assert doc["groups"]["group 1"]["test"] == ["cases/c7.json"]


def test_counts_enter_the_clustering_as_log1p():
    import math

    assert groups.COUNT_PREFIX == "rows_" and groups.MIN_SILHOUETTE == 0.5
    assert groups.transformed({"rows_a": 99.0, "mean_a_x": 99.0}) == {"rows_a": math.log1p(99), "mean_a_x": 99.0}
    got = groups.transformed({"rows_b": -3.0, "rows_c": float("inf")})
    assert got["rows_b"] == -3.0 and got["rows_c"] == float("inf"), "a value below 0, or not finite, stays raw"


def test_held_back_cases_follow_the_stump_and_the_rule_reports_both_accuracies(tmp_path):
    sizes = [80, 20, 82, 22, 84, 24, 83, 21]
    root = study_with_cases(tmp_path, sizes=sizes, held=2)
    doc = groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    assert doc["format"] == 2 and doc["assigned"] == "stump" and doc["min_silhouette"] == 0.5
    assert doc["stump"] == {"feature": "rows_stops", "thresholds": [52.0], "sides": ["group 1", "group 2"],
                            "misplaced": 0}
    assert doc["accuracy"] == {"training": {"right": 6, "of": 6}, "held_back": {"right": 2, "of": 2}}
    assert groups.read_groups(root, load_study(root)) == doc
    assert groups.assign(doc["stump"], {"rows_stops": 52.0}) == "group 1", "above 52, strictly"
    assert groups.assign(doc["stump"], {"rows_stops": 52.5}) == "group 2"
    assert groups.assign(doc["stump"], {"rows_stops": float("nan")}) is None
    assert groups.assign(doc["stump"], {}) is None
    assert groups.rule_lines(doc, {"group 1": "use", "group 2": "use"}) == [
        "If the number of rows in stops is above 52, use the settings of group 2; otherwise those of group 1.",
        "The rule puts 6 of the 6 cases the search learned from in the group they were found in, "
        "and 2 of the 2 held-back cases in the group they are nearest to."]
    no_held = {**doc, "accuracy": {"training": {"right": 5, "of": 6}, "held_back": {"right": 0, "of": 0}}}
    assert groups.rule_lines(no_held, {})[1] == (
        "The rule puts 5 of the 6 cases the search learned from in the group they were found in.")


def test_a_held_back_case_the_rule_cannot_read_goes_to_the_nearest_group_and_says_so(tmp_path, monkeypatch):
    """Review Focus 3: c6 has no number of rows (not finite), so the rule cannot read it; its other
    clustering feature puts it nearest group 2. c7 has none of the features: the first group.
    c8 the rule reads. The held-back accuracy counts only c8; groups.json keeps no NaN."""
    sizes = [80, 20, 82, 22, 84, 24, 83, 21, 23]
    root = study_with_cases(tmp_path, sizes=sizes, held=3)
    real = groups.features_of

    def features_of(root_, case, *, tables):
        if case == "cases/c6.json":
            return {"rows_stops": float("nan"), "mean_stops_stop_id": 41.0, "mean_stops_weight": 1.0}
        if case == "cases/c7.json":
            return {}
        return real(root_, case, tables=tables)

    monkeypatch.setattr(groups, "features_of", features_of)
    doc = groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    assert doc["method"] == "features" and doc["stump"]["feature"] == "rows_stops"
    assert doc["groups"]["group 2"]["test"] == ["cases/c6.json"]
    assert doc["groups"]["group 1"]["test"] == ["cases/c7.json", "cases/c8.json"]
    assert "cases/c6.json: the rule cannot read it, so it goes to group 2, the nearest" in doc["notes"], doc["notes"]
    assert "cases/c7.json: the rule cannot read it, so it goes to group 1, the first" in doc["notes"], doc["notes"]
    assert doc["accuracy"]["held_back"] == {"right": 1, "of": 1}, "only the case the rule read is counted"
    assert doc["features"]["cases/c6.json"]["rows_stops"] is None, "not a finite number: None, never NaN"
    text = (root / groups.GROUPS).read_text(encoding="utf-8")
    assert "NaN" not in text and json.loads(text) == doc
    assert groups.rule_lines(doc, {})[1].endswith("and 1 of the 1 held-back cases in the group they are nearest to.")


def test_a_case_to_learn_from_without_a_number_joins_a_group_without_shaping_it(tmp_path, monkeypatch):
    """c0 (80 stops) has no number of rows: it is left out of the clustering and the rule, and joins
    group 2 by its other feature, with a note; nothing raises."""
    sizes = [80, 20, 82, 22, 84, 24, 83, 21]
    root = study_with_cases(tmp_path, sizes=sizes, held=2)
    real = groups.features_of

    def features_of(root_, case, *, tables):
        if case == "cases/c0.json":
            return {"rows_stops": None, "mean_stops_stop_id": 39.5, "mean_stops_weight": 1.0}
        return real(root_, case, tables=tables)

    monkeypatch.setattr(groups, "features_of", features_of)
    doc = groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    assert doc["method"] == "features" and doc["stump"]["feature"] == "rows_stops"
    assert doc["groups"]["group 2"]["training"] == ["cases/c0.json", "cases/c2.json", "cases/c4.json"]
    assert ("cases/c0.json: some of its features are not numbers, so it did not shape the groups; "
            "it joins group 2, the nearest") in doc["notes"], doc["notes"]
    assert doc["accuracy"]["training"] == {"right": 5, "of": 5}, "the rule was fitted without it"


def test_a_format_1_groups_json_is_computed_again(tmp_path):
    """Decision 10: a features document of format 1 placed held-back cases by k-means, so it is stale;
    a tags document means the same under format 2 and is read as is."""
    sizes = [80, 20, 82, 22, 84, 24, 83, 21]
    root = study_with_cases(tmp_path, sizes=sizes, held=2)
    doc = groups.compute(root, tables=fake_tables(sizes_of(sizes)))
    old = {key: value for key, value in doc.items() if key not in ("assigned", "stump", "accuracy", "min_silhouette")}
    (root / groups.GROUPS).write_text(json.dumps({**old, "format": 1}), encoding="utf-8")
    assert groups.read_groups(root, load_study(root)) is None
    for i in range(8):
        groups.write_tag(root, f"cases/c{i}.json", "small" if sizes[i] < 50 else "large")
    tagged = groups.compute(root, tables=fake_tables(sizes_of(sizes)), features=False)
    legacy = {key: value for key, value in tagged.items() if key != "assigned"}
    (root / groups.GROUPS).write_text(json.dumps({**legacy, "format": 1}), encoding="utf-8")
    assert groups.read_groups(root, load_study(root)) == {**legacy, "format": 1}
    (root / groups.GROUPS).write_text(json.dumps({**legacy, "format": 3}), encoding="utf-8")
    assert groups.read_groups(root, load_study(root)) is None, "a format it does not know"


def _two_kinds(rng, n: int, gap: float) -> list[list[float]]:
    return [[rng.gauss(gap if i < n // 2 else 0.0, 1.0)] for i in range(n)]


def test_two_clear_kinds_of_case_split_in_over_95_percent():
    """Eight training cases, one feature, two kinds 4 sd apart (random.Random(20261114), 300 trials),
    through compute's own steps 2-4 (`groups._choose` over the standardised points).
    Estimate 98.9 % +- 0.3 (1000 trials; the builder check 98.8 % +- 0.34); asserts > 95 %."""
    import random

    rng = random.Random(20261114)
    trials = 300
    split = sum(groups._choose(groups.standardise(_two_kinds(rng, 8, 4.0)))[1] is not None  # noqa: SLF001
                for _ in range(trials))
    rate = split / trials
    print(f"two kinds 4 sd apart, 8 cases, one feature: split in {rate:.1%}")
    assert rate > 0.95


def test_cases_of_one_kind_split_in_under_10_percent_on_three_features():
    """Eight training cases from one Gaussian, three features (random.Random(20261114), 500 trials).
    Estimate 2.2 % (builder check, 2000 trials; 2.1 % +- 0.3 through `_choose`); asserts < 10 %.
    (On ONE feature the same cases split in about 80 % -- open question 1.)"""
    import random

    rng = random.Random(20261114)
    trials = 500
    split = 0
    for _ in range(trials):
        points = groups.standardise([[rng.gauss(0, 1) for _ in range(3)] for _ in range(8)])
        split += groups._choose(points)[1] is not None  # noqa: SLF001
    rate = split / trials
    print(f"one kind, 8 cases, three features: split in {rate:.1%}")
    assert rate < 0.10


def test_similarity_is_one_for_alike_cases_and_falls_with_the_rms_distance():
    """Decisions 5 and 14 (A4.3, S11): z standardised by the within-study pooled sd (population form),
    each z² capped at 9, d² = max(0, mean z² - c), c = (1/n_a + 1/n_b) * N / (N - 4), the factor
    exp(-d²/2). Four cases a study: {9, 9, 11, 11} has sd 1 and the correction is (1/4 + 1/4) * 8 / (8 - 4) = 1."""
    import math

    def study(values, shift=0.0, names=("mean_x",), prefix="a"):
        return {f"{prefix}{i}": {n: v + shift for n in names} for i, v in enumerate(values)}

    base, up = [9.0, 9.0, 11.0, 11.0], math.sqrt(2.0)  # z² = 2, d² = 2 - 1 = 1
    a = study(base)
    assert groups.similarity(a, a) == 1.0 and groups.similarity(a, None) == 1.0
    assert groups.similarity(None, a) == 1.0 and groups.similarity({}, a) == 1.0
    assert groups.compare(a, study(base, up, prefix="b")) == (pytest.approx(math.exp(-0.5), abs=1e-9), [])
    three = ("mean_x", "mean_y", "mean_z")
    assert groups.similarity(study(base, names=three), study(base, up, names=three, prefix="b")) == \
        pytest.approx(math.exp(-0.5), abs=1e-9), "the same distance in each of three features: the mean, not the sum"
    assert groups.similarity(a, study(base, 0.5, prefix="b")) == 1.0, \
        "z² = 0.25 is less than sampling alone gives two studies of the same kind: no shrink"
    assert groups.similarity(a, study(base, 4.0, prefix="b")) == pytest.approx(math.exp(-(9 - 1) / 2), abs=1e-12), \
        "four sd apart counts as three (z² capped at 9)"
    # counts are read as log1p, as the clustering reads them
    counts = [math.expm1(v) for v in (1.0, 1.0, 3.0, 3.0)]
    shifted = [math.expm1(v + up) for v in (1.0, 1.0, 3.0, 3.0)]
    assert groups.similarity(study(counts, names=("rows_s",)), study(shifted, names=("rows_s",), prefix="b")) == \
        pytest.approx(math.exp(-0.5), abs=1e-9)
    # a feature with no spread inside either study cannot be standardised: left out, and named
    fixed_a = {c: {**row, "mean_fleet": 3.0} for c, row in a.items()}
    fixed_b = {c: {**row, "mean_fleet": 5.0} for c, row in study(base, up, prefix="b").items()}
    assert groups.compare(fixed_a, fixed_b) == (pytest.approx(math.exp(-0.5), abs=1e-9), ["mean_fleet"]), \
        "a fixed fleet size that differs leaves the factor as the other features make it"
    assert groups.compare({"c1": {"mean_w": 1.0}}, {"c3": {"mean_w": 2.0}}) == (1.0, ["mean_w"])
    # a feature some case lacks, or has as no number, is left out of the shared features
    gap = study(base, up, names=("mean_x", "mean_v"), prefix="b")
    gap["b0"]["mean_v"] = float("nan")
    assert groups.compare(study(base, names=("mean_x", "mean_v")), gap) == \
        (pytest.approx(math.exp(-0.5), abs=1e-9), [])


def test_the_sampling_correction_is_exact_and_there_is_no_shrink_below_five_cases():
    """Decision 14: c = (1/n_a + 1/n_b) * N / (N - 4); at N <= 4 (E[1/chi²(N-2)] is infinite) the
    factor is 1.0 however far apart the cases (the plain 1/n_a + 1/n_b kept only about half the
    weight for alike studies there)."""
    import math

    assert groups._correction(4, 4) == pytest.approx(1.0)  # noqa: SLF001
    assert groups._correction(8, 8) == pytest.approx(0.25 * 16 / 12)  # noqa: SLF001
    assert groups._correction(3, 5) == pytest.approx((1 / 3 + 1 / 5) * 2)  # noqa: SLF001
    # two cases a study (N = 4), or one and three: no shrink, even 10 sd apart; left-out features still named
    two = {"a0": {"mean_x": 10.0, "mean_w": 1.0}, "a1": {"mean_x": 20.0, "mean_w": 1.0}}
    far = {"b0": {"mean_x": 60.0, "mean_w": 2.0}, "b1": {"mean_x": 70.0, "mean_w": 2.0}}
    assert groups.compare(two, far) == (1.0, ["mean_w"])
    assert groups.similarity({"a0": {"mean_x": 10.0}}, {f"b{i}": {"mean_x": 50.0 + i} for i in range(3)}) == 1.0
    # five cases between them: the distance counts again (z² capped at 9, c = (1/2 + 1/3) * 5 / 1)
    five = {f"b{i}": {"mean_x": 60.0 + 10 * i} for i in range(3)}
    assert groups.similarity(two, five) == pytest.approx(math.exp(-(9 - (1 / 2 + 1 / 3) * 5) / 2), abs=1e-12)


@pytest.mark.parametrize("cases, shift, low, high, estimate", [
    (4, 0.0, 0.87, 0.91, 0.895),
    (8, 0.0, 0.94, 0.965, 0.953),
    (12, 0.0, 0.963, 0.978, 0.971),
    (8, 1.0, 0.545, 0.59, 0.568),
    (8, 3.0, 0.0215, 0.025, 0.0231),
])
def test_alike_studies_keep_about_90_95_and_97_percent_and_unlike_ones_far_less(cases, shift, low, high, estimate):
    """Decision 14's figures, three features, standard normal cases, the second study shifted by
    `shift` sd in each, with the exact correction: the mean factor at 4 / 8 / 12 cases a study,
    alike, is about 0.895 / 0.953 / 0.971; at 8 cases, 1 sd apart about 0.568, 3 sd about 0.0231
    (20 000 pairs, two seeds each). Not 1.0 for alike studies: d² is clipped at 0, so a pair that
    happens to lie apart shrinks while a close pair cannot grow. Seeded (`Random(15)`), 2000 pairs,
    measured 0.887 / 0.952 / 0.970 / 0.563 / 0.0230; each band is at least 3 SE (SE of the mean
    0.0045 / 0.0021 / 0.0013 / 0.0049 / 0.0003) from both the estimate and the measured mean."""
    import random

    rng = random.Random(15)

    def study(centre: float) -> dict[str, dict[str, float]]:
        return {f"c{i}": {f"mean_{j}": rng.gauss(centre, 1) for j in "xyz"} for i in range(cases)}

    factors = [groups.similarity(study(0.0), study(shift)) for _ in range(2000)]
    mean = sum(factors) / len(factors)
    print(f"{cases} + {cases} cases, {shift} sd apart, three features: mean factor {mean:.4f} (estimate {estimate})")
    assert low <= mean <= high


def _keep_tables(root: Path, case: str, rows: int) -> None:
    """The kept tables of `case` as `case_tables` leaves them: the SQLite file and its stamp."""
    folder = root / "groups" / "tables"
    folder.mkdir(parents=True, exist_ok=True)
    made = fake_tables({case: rows})(root, case)
    target = folder / f"{Path(case).name}.sqlite"
    target.unlink(missing_ok=True)
    made.replace(target)
    stat = (root / case).stat()
    (folder / f"{Path(case).name}.json").write_text(
        json.dumps([stat.st_size, stat.st_mtime_ns, load_study(root).application_path]) + "\n", encoding="utf-8")


def test_study_features_never_solve_and_need_every_case_cached(tmp_path, monkeypatch):
    from evolvekit.harness import execute

    def no_solve(*_a, **_k):
        raise AssertionError("a warm start never solves a case")

    monkeypatch.setattr(execute, "_solve_once", no_solve)
    sizes = [20, 40, 60]
    root = study_with_cases(tmp_path, sizes, held=1)
    study = load_study(root)
    assert groups.study_features(root, study) is None, "nothing kept yet"
    for case, rows in sizes_of(sizes).items():
        if case != "cases/c2.json":
            _keep_tables(root, case, rows)
    assert groups.study_features(root, study) is None, "the held-back case has none kept"
    _keep_tables(root, "cases/c2.json", 60)
    got = groups.study_features(root, study)
    assert set(got) == {"cases/c0.json", "cases/c1.json", "cases/c2.json"}
    assert got["cases/c2.json"]["rows_stops"] == 60.0 and got["cases/c0.json"]["mean_stops_stop_id"] == 9.5
    (root / "cases" / "c1.json").write_bytes((root / "cases" / "c1.json").read_bytes() + b" ")
    assert groups.study_features(root, study) is None, "a case changed since its tables were kept: stale"
