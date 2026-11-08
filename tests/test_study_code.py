"""A study varies code (design §5.2): `vary.code` in study.yaml, its problems,
`study code`, the compile into `problem.tree` with the base pinned to a commit,
and the proposal the app's Set up makes for where the source is."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from evolvekit.app.store import Home
from evolvekit.cli import main
from evolvekit.config import build_config
from evolvekit.harness import HarnessError
from evolvekit.harness.compile import OPERATORS_CODE, compile_study, write_run
from evolvekit.harness.library import ORIGIN
from evolvekit.harness.manifest import load_harness
from evolvekit.harness.plan import Plan
from evolvekit.harness.study import (
    CodeVary,
    SettingChoice,
    Study,
    load_study,
    save_study,
    study_from_template,
    study_problems,
)
from evolvekit.tree import git
from tree_helpers import git as tree_git
from tree_helpers import git_init_commit, head, make_repo

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "harnesses" / "demo-tour"
SOURCE = (ROOT / "examples" / "cli-solver").resolve()
MINIMAL = {"study": 1, "name": "M", "harness": {"id": "demo-tour", "version": "1.1.0"}}

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def test_study_parse_vary_code():
    study = Study.parse({**MINIMAL, "vary": {"code": {"repo": str(SOURCE), "from": "working", "open": ["solver.py"],
                                                       "brief": "Shorter tours."}}})
    assert study.code == CodeVary(repo=str(SOURCE), from_ref="working", open=["solver.py"], brief="Shorter tours.")
    assert Study.parse(study.to_yaml()).code == study.code
    assert "code" not in Study.parse(MINIMAL).to_yaml()["vary"]
    with pytest.raises(HarnessError, match=re.escape("vary.code: unknown key(s) ['branch']")):
        Study.parse({**MINIMAL, "vary": {"code": {"branch": "x"}}})


def test_study_code_without_repo_is_a_problem():
    harness = load_harness(DEMO)
    study = study_from_template(harness, "faster", "F")
    problems = study_problems(study, harness)
    assert problems[0] == "vary.code.repo: say where the source is"
    assert "budget.ai: a code study searches with an AI model; turn AI search help on, or compare branches with study bakeoff" in problems
    assert not any(p.startswith("vary: choose at least one") for p in problems)
    study.code.repo, study.code.brief = "cli-solver", ""
    problems = study_problems(study, harness, for_search=False)
    assert "vary.code.repo: give the full path to the source; 'cli-solver' is relative" in problems
    assert "vary.code.brief: the brief is empty; say in a sentence what the search should improve" in problems
    assert not any(p.startswith("budget.ai") for p in problems)


def test_study_code_problems_name_the_folder_and_the_files(tmp_path):
    harness = load_harness(DEMO)
    study = study_from_template(harness, "faster", "F")
    study.code.repo, study.code.open = str(tmp_path / "gone"), ["solver.py", "README.md"]
    problems = study_problems(study, harness, root=tmp_path, for_search=False)
    assert f"vary.code.repo: there is nothing at {tmp_path / 'gone'} any more; point the study at the source again" in problems
    assert "vary.code.open: README.md is not among the files the harness lets change" in problems
    assert not any(p.startswith("vary.code.open: solver.py") for p in problems)


def test_study_code_without_harness_code_is_a_problem():
    harness = replace(load_harness(DEMO), code=None)
    study = study_from_template(load_harness(DEMO), "faster", "F")
    study.code.repo = str(SOURCE)
    assert "vary.code: the harness has no code section, so this study cannot change code" in study_problems(study, harness)
    study.settings = {"neighbours": SettingChoice.parse("tune", "x")}
    assert "vary: a study changes code or tunes settings, not both yet; fix the settings, or vary code alone" in \
        study_problems(study, load_harness(DEMO))


def test_cli_study_code_writes_repo(tmp_path, capsys, monkeypatch):
    home = str(tmp_path)
    assert main(["study", "new", "Faster", "--harness", "demo-tour", "--template", "faster", "--home", home]) == 0
    assert main(["study", "code", "faster", "--repo", str(SOURCE), "--brief", "Shorter tours in the same time.",
                 "--home", home]) == 0
    study = load_study(tmp_path / "studies" / "faster")
    assert (study.code.repo, study.code.from_ref, study.code.brief) == (str(SOURCE), "HEAD", "Shorter tours in the same time.")
    assert study.application_path == str(SOURCE / "solver.py"), "the program in the source, for the preview"
    said = capsys.readouterr()
    assert f"source: {SOURCE} (from HEAD); files: solver.py" in said.out
    assert "next: evolvekit study check faster" in said.err
    monkeypatch.chdir(ROOT / "examples")
    assert main(["study", "code", "faster", "--repo", "cli-solver", "--home", home]) == 0
    assert load_study(tmp_path / "studies" / "faster").code.repo == str(SOURCE), "a relative path is made whole"
    assert main(["study", "code", "faster", "--home", home]) == 0, "no --repo: the harness's own, when its folder is there"
    assert load_study(tmp_path / "studies" / "faster").code.brief == "Shorter tours in the same time.", "the brief stays"
    (tmp_path / "plain").mkdir()
    assert main(["study", "code", "faster", "--repo", str(tmp_path / "plain"), "--home", home]) == 1
    assert "is not inside a git repository" in capsys.readouterr().err
    assert main(["study", "code", "faster", "--from", "no-such-branch-here", "--home", home, "--json"]) == 1
    answer = json.loads(capsys.readouterr().out)
    assert answer["ok"] is False and answer["message"].startswith("vary.code.from: no-such-branch-here: no such branch")
    assert load_study(tmp_path / "studies" / "faster").code.from_ref == "HEAD", "a refused answer writes nothing"


def test_compile_emits_problem_tree_with_absolute_repo(tmp_path):
    harness = load_harness(DEMO)
    study = study_from_template(harness, "faster", "F")
    study.code.repo, study.training = str(SOURCE), ["cases/a.json", "cases/b.json"]
    config, _ = compile_study(study, harness, Plan.fixed(), bakeoff=True)
    assert config["problem"]["tree"] == {"repo": str(SOURCE), "base": "HEAD", "open": ["solver.py"], "app": "{tree}/solver.py"}
    assert "parameters" not in config["problem"] and "Shorter tours" in config["problem"]["description"]
    stages = config["evaluate"]["stages"]
    assert [s["id"] for s in stages] == ["static", "full"]
    assert "--app {app}" in stages[-1]["command"] and "{params_json}" not in stages[-1]["command"]
    assert config["search"]["operators"] == {"param_lhs": 1.0}
    assert config["search"]["novelty"]["near"] == {"method": "off"}
    built = replace(harness, code=replace(harness.code, build="{python} -m pip install -q -e {tree}"))
    config, _ = compile_study(study, built, Plan.fixed(), bakeoff=True)
    assert [s["id"] for s in config["evaluate"]["stages"]] == ["static", "build", "full"]
    assert config["evaluate"]["stages"][1] == {"id": "build", "kind": "builtin-build"}
    assert config["problem"]["tree"]["build"] == "{python} -m pip install -q -e {tree}"
    py = replace(built, application=replace(built.application, kind="python"), code=replace(built.code, app=""))
    study.application_path = "C:/Py 3/python.exe"
    config, _ = compile_study(study, py, Plan.fixed(), bakeoff=True)
    # Task 6's ruling: `{python}` stays in the template, and the tree says which interpreter it is.
    assert config["problem"]["tree"]["build"] == "{python} -m pip install -q -e {tree}"
    assert config["problem"]["tree"]["python"] == "C:/Py 3/python.exe" and "app" not in config["problem"]["tree"]
    assert config["evaluate"]["stages"][-1]["command"].startswith('"C:/Py 3/python.exe" ../../harness/runner.py solve')
    study.application_path = ""
    run_dir = write_run(study, harness, Plan.fixed(generations=0), tmp_path, "r1", bakeoff=True)
    pinned = yaml.safe_load((run_dir / "evolvekit.yaml").read_text(encoding="utf-8"))["problem"]["tree"]["base"]
    assert re.fullmatch(r"[0-9a-f]{40}", pinned) and pinned == git(ROOT, "rev-parse", "HEAD").strip()


def test_a_code_study_compiles_to_a_config_that_loads(tmp_path):
    harness = load_harness(DEMO)
    study = study_from_template(harness, "faster", "F")
    study.code.repo, study.training = str(SOURCE), ["cases/a.json"]
    study.application_path = str(SOURCE / "solver.py")
    study.budget.ai_enabled = True
    models = {"small": {"provider": "fake", "model": "fake"}, "strong": {"provider": "fake", "model": "fake"}}
    config, _ = compile_study(study, harness, Plan.fixed(), models=models)
    assert config["search"]["operators"] == OPERATORS_CODE == {"diff": 1.0}
    run_dir = write_run(study, harness, Plan.fixed(), tmp_path, "r1", models=models)
    loaded = build_config(yaml.safe_load((run_dir / "evolvekit.yaml").read_text(encoding="utf-8")), base_dir=run_dir)
    assert loaded.problem.tree is not None and loaded.problem.tree.open == ("solver.py",)
    with pytest.raises(HarnessError, match=re.escape("vary.code.from: nowhere-at-all: no such branch")):
        study.code.from_ref = "nowhere-at-all"
        write_run(study, harness, Plan.fixed(), tmp_path, "r2", models=models)


def test_faster_template_compiles_once_repo_is_set():
    harness = load_harness(DEMO)          # check_templates already compiled it with a stand-in folder
    assert harness.templates["faster"]["title"] == "Shorter tours by changing the solver"
    study = study_from_template(harness, "faster", "F")
    assert study.code.repo == "" and study.code.from_ref == "HEAD" and study.goal[0].kpi == "tour_length"
    study.code.repo, study.training = str(SOURCE), ["cases/a.json"]
    assert compile_study(study, harness, Plan.fixed(), bakeoff=True)[0]["problem"]["tree"]["repo"] == str(SOURCE)


def test_study_document_proposes_the_code_repo(tmp_path):
    home = Home(tmp_path)
    slug = home.create_study("demo-tour", "faster", "F")
    assert home.document(slug)["code_proposal"] == str(SOURCE)
    (home.study_root(slug) / ORIGIN).write_text(str(tmp_path / "gone"), encoding="utf-8")
    assert home.document(slug)["code_proposal"] is None
    tune = home.create_study("demo-tour", "tune", "T")
    assert home.document(tune)["code_proposal"] is None, "a settings study is not offered a source"


def test_set_code_from_the_app_keeps_the_study_whole(tmp_path):
    home = Home(tmp_path)
    slug = home.create_study("demo-tour", "faster", "F")
    answer = home.set_code(slug, "", "working", "Fewer long legs.")
    assert answer == {"ok": True, "repo": str(SOURCE), "from": "working", "message": f"source: {SOURCE} (from working)"}
    study = load_study(home.study_root(slug))
    assert study.code == CodeVary(repo=str(SOURCE), from_ref="working", open=[], brief="Fewer long legs.")
    with pytest.raises(Exception, match="give the full path to the source; 'cli-solver' is relative"):
        home.set_code(slug, "cli-solver")
    copy = home.duplicate_study(slug)
    assert home.document(copy)["code_proposal"] == str(SOURCE), "a copy keeps where its harness came from"


def _source_copy(where: Path) -> Path:
    """examples/cli-solver as a git repository of its own at `where` (a space in the path)."""
    shutil.copytree(SOURCE, where, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    git_init_commit(where)
    return where


def test_a_moved_source_re_points_the_program_it_filled_in(tmp_path):
    home = Home(tmp_path / "home")
    slug = home.create_study("demo-tour", "faster", "F")
    first, second = _source_copy(tmp_path / "source a"), _source_copy(tmp_path / "source b")
    assert home.set_code(slug, str(first))["ok"]
    assert load_study(home.study_root(slug)).application_path == str(first / "solver.py")
    assert home.set_code(slug, str(second))["ok"]
    assert load_study(home.study_root(slug)).application_path == str(second / "solver.py"), \
        "the program D10 filled in follows the source"
    (second / "solver.py").rename(second / "moved.py")
    assert home.set_code(slug, str(first))["ok"] and home.set_code(slug, str(second))["ok"]
    assert load_study(home.study_root(slug)).application_path == "", "no program in the new source: none, not the old one"
    study = load_study(home.study_root(slug))
    study.application_path = str(SOURCE / "solver.py")  # said by hand: the engineer's own choice
    save_study(study, home.study_root(slug))
    assert home.set_code(slug, str(first))["ok"]
    assert load_study(home.study_root(slug)).application_path == str(SOURCE / "solver.py")


def test_working_is_snapshotted_once_when_the_run_is_written(tmp_path):
    top = make_repo(tmp_path)
    (top / "app dir" / "solver.py").write_text("VALUE = 6\n", encoding="utf-8", newline="\n")
    status = tree_git(top, "status", "--porcelain")
    harness = load_harness(DEMO)
    study = study_from_template(harness, "faster", "F")
    study.code.repo, study.code.from_ref, study.training = str(top / "app dir"), "working", ["cases/a.json"]
    run_dir = write_run(study, harness, Plan.fixed(), tmp_path / "study", "r1", bakeoff=True)
    config = yaml.safe_load((run_dir / "evolvekit.yaml").read_text(encoding="utf-8"))
    pinned = config["problem"]["tree"]["base"]
    refs = tree_git(top, "for-each-ref", "--format=%(refname)", "refs/evolvekit/").splitlines()
    assert re.fullmatch(r"[0-9a-f]{40}", pinned) and pinned != head(top) and len(refs) == 1
    assert tree_git(top, "rev-parse", refs[0]) == pinned, "the snapshot is kept by its ref"
    for _ in range(2):  # loaded again (a resume, the final check): the same commit, no new snapshot
        assert build_config(config, base_dir=run_dir).problem.tree.base == pinned
    assert tree_git(top, "for-each-ref", "--format=%(refname)", "refs/evolvekit/").splitlines() == refs
    assert tree_git(top, "status", "--porcelain") == status, "the engineer's working tree is untouched"


@pytest.mark.parametrize("repo", ["", "cli-solver"])
def test_write_run_refuses_a_source_without_a_full_path(tmp_path, repo):
    harness = load_harness(DEMO)
    study = study_from_template(harness, "faster", "F")
    study.code.repo, study.training = repo, ["cases/a.json"]
    with pytest.raises(HarnessError, match=re.escape("vary.code.repo: give the full path to the source")):
        write_run(study, harness, Plan.fixed(), tmp_path, "r1", bakeoff=True)
    assert not (tmp_path / "runs" / "r1").exists(), "nothing is written"


def test_open_files_past_the_prompt_limit_refuse_the_study(tmp_path):
    from evolvekit.tree_prompts import OPEN_LINES

    top = tmp_path / "my repo"
    (top / "src").mkdir(parents=True)
    (top / "src" / "solver.py").write_text("".join(f"X{i} = {i}\n" for i in range(OPEN_LINES + 1)), encoding="utf-8")
    git_init_commit(top)
    harness = load_harness(DEMO)
    study = study_from_template(harness, "faster", "F")
    study.code.repo, study.budget.ai_enabled = str(top / "src"), True
    sentence = (f"vary.code.open: the open files hold {OPEN_LINES + 1} lines; a code study shows them whole, "
                f"up to {OPEN_LINES} — narrow vary.code.open")
    assert sentence in study_problems(study, harness)
    assert not any("open files hold" in p for p in study_problems(study, harness, for_search=False)), \
        "a bake-off shows no files to a model"
    (top / "src" / "solver.py").write_text("X = 1\n", encoding="utf-8")
    assert sentence in study_problems(study, harness), "measured at the base (HEAD), not the working tree"
    study.code.from_ref = "working"
    assert not any("open files hold" in p for p in study_problems(study, harness))
    tree_git(top, "commit", "-qam", "small")
    study.code.from_ref = "HEAD"
    assert not any("open files hold" in p for p in study_problems(study, harness))
