import json
from importlib import resources

import pytest

from evolvekit import cli, skills
from evolvekit.skills import available, frontmatter, install

NAMES = ["evolvekit-harness", "evolvekit-study"]


def test_available_lists_the_two_skills():
    assert available() == NAMES


def test_install_writes_generic_and_pointer(tmp_path):
    result = install(tmp_path)
    assert len(result["written"]) == 6 and result["skipped"] == []
    for name in NAMES:
        generic = tmp_path / ".agents" / "skills" / name
        assert (generic / "SKILL.md").is_file()
        assert (generic / "references" / "evolvekit.md").is_file()
        pointer = (tmp_path / ".claude" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        own = frontmatter((generic / "SKILL.md").read_text(encoding="utf-8"))
        meta = frontmatter(pointer)
        assert meta["name"] == own["name"] == name
        assert meta["description"] == own["description"] != ""
        assert pointer.rstrip().endswith(
            f"Read and follow `.agents/skills/{name}/SKILL.md` in this repository.")


def test_install_skips_existing_without_force(tmp_path):
    install(tmp_path)
    again = install(tmp_path)
    assert again["written"] == [] and len(again["skipped"]) == 6
    forced = install(tmp_path, force=True)
    assert len(forced["written"]) == 6 and forced["skipped"] == []


def test_shared_reference_identical_in_both(tmp_path):
    install(tmp_path)
    shared = resources.files("evolvekit.skills").joinpath("shared", "evolvekit.md").read_bytes()
    copies = [(tmp_path / ".agents" / "skills" / n / "references" / "evolvekit.md").read_bytes()
              for n in NAMES]
    # the installed copy is the packaged file with LF endings
    assert copies[0] == copies[1] == shared.replace(b"\r\n", b"\n")
    assert b"\r" not in copies[0]


def test_installed_files_are_package_data():
    root = resources.files("evolvekit.skills")
    for name in NAMES:
        assert root.joinpath(name, "SKILL.md").is_file()
    assert root.joinpath("shared", "evolvekit.md").is_file()


def test_cli_skill_list_and_install(tmp_path, capsys):
    assert cli.main(["skill", "list"]) == 0
    assert capsys.readouterr().out.split() == NAMES
    target = tmp_path / "a folder with spaces"
    assert cli.main(["skill", "install", "--dir", str(target)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 6 and all(line.startswith("wrote ") for line in out)
    assert out[0].replace("\\", "/").startswith("wrote .agents/skills/")
    assert cli.main(["skill", "install", "--dir", str(target)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert all(line.startswith("skipped ") and line.endswith(" (exists)") for line in out)
    assert cli.main(["skill", "install", "--dir", str(target), "--force"]) == 0
    assert all(line.startswith("wrote ") for line in capsys.readouterr().out.splitlines())


def _skills_root(tmp_path, monkeypatch, text, name="evolvekit-x"):
    root = tmp_path / "pkg"
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(text, encoding="utf-8")
    (root / "shared").mkdir()
    (root / "shared" / "evolvekit.md").write_text("# ref\n", encoding="utf-8")
    monkeypatch.setattr(skills, "_root", lambda: root)
    return root


def test_missing_description_refuses_and_names_the_skill(tmp_path, monkeypatch):
    _skills_root(tmp_path, monkeypatch, "---\nname: evolvekit-x\n---\n\nBody.\n")
    with pytest.raises(ValueError, match="evolvekit-x"):
        install(tmp_path / "out")


def test_unclosed_frontmatter_and_wrong_name_refuse(tmp_path, monkeypatch):
    root = _skills_root(tmp_path, monkeypatch, "---\nname: evolvekit-x\ndescription: d\n")
    with pytest.raises(ValueError, match="evolvekit-x"):
        install(tmp_path / "out")
    (root / "evolvekit-x" / "SKILL.md").write_text(
        "---\nname: other\ndescription: d\n---\n", encoding="utf-8")
    with pytest.raises(ValueError, match="evolvekit-x"):
        install(tmp_path / "out")


def test_multiline_description_refuses(tmp_path, monkeypatch):
    _skills_root(tmp_path, monkeypatch,
                 "---\nname: evolvekit-x\ndescription: >\n  one\n  two\n---\n")
    with pytest.raises(ValueError, match="one line"):
        install(tmp_path / "out")


def test_description_with_a_colon_round_trips(tmp_path, monkeypatch):
    desc = 'Use when: you set up a study - "quoted" # not a comment'
    _skills_root(tmp_path, monkeypatch,
                 f"---\nname: evolvekit-x\ndescription: {json.dumps(desc)}\n---\n")
    install(tmp_path / "out")
    pointer = (tmp_path / "out" / ".claude" / "skills" / "evolvekit-x" / "SKILL.md").read_text(encoding="utf-8")
    assert frontmatter(pointer) == {"name": "evolvekit-x", "description": desc}
