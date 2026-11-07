from importlib import resources

from evolvekit import cli
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
    assert copies[0] == copies[1] == shared
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
