"""`.env` is read, as the README always said it was.

"Copy `.env.example` to `.env` and fill in what you need" -- and then nothing
read the file: a key placed there never reached a provider, and the error
message told the user to do what they had just done.
"""

from __future__ import annotations

import os

import pytest

from evolvekit.cli import main
from evolvekit.env import load_env_files, parse_env

SECRET = "sk-or-THIS-MUST-NEVER-BE-PRINTED"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("EK_TEST_KEY", "EK_TEST_OTHER", "EK_TEST_QUOTED", "OPENROUTER_API_KEY", "EVOLVEKIT_NO_DOTENV"):
        # Set, then delete: the loader writes to `os.environ` directly, and only a
        # name monkeypatch has *recorded* is put back the way it was afterwards.
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)


def test_the_file_format_is_the_usual_one():
    parsed = parse_env(
        "# a comment\n"
        "\n"
        "EK_TEST_KEY=abc=def\n"
        "export EK_TEST_OTHER = spaced \n"
        "EK_TEST_QUOTED=\"two words\"   # trailing comment\n"
        "SINGLE='it is # not a comment'\n"
        "EMPTY=\n"
        "not a line\n"
        "9BAD=1\n"
    )
    assert parsed == {
        "EK_TEST_KEY": "abc=def", "EK_TEST_OTHER": "spaced", "EK_TEST_QUOTED": "two words",
        "SINGLE": "it is # not a comment", "EMPTY": "",
    }


def test_the_nearest_file_above_the_config_is_loaded_and_the_environment_wins(tmp_path, monkeypatch):
    project = tmp_path / "project"
    deep = project / "benchmarks" / "solver"
    deep.mkdir(parents=True)
    (tmp_path / ".env").write_text("EK_TEST_KEY=from-far-away\n", encoding="utf-8")
    (project / ".env").write_text("EK_TEST_KEY=from-the-project\nEK_TEST_OTHER=file\n", encoding="utf-8")
    monkeypatch.setenv("EK_TEST_OTHER", "already-set")

    loaded = load_env_files([deep])
    assert os.environ["EK_TEST_KEY"] == "from-the-project", "the nearest .env going up from the config"
    assert os.environ["EK_TEST_OTHER"] == "already-set", "a real environment variable is never overridden"
    assert loaded == [((project / ".env").resolve(), ["EK_TEST_KEY"])], "names only, and only what was actually set"


def test_an_empty_value_does_not_set_anything(tmp_path):
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=\n", encoding="utf-8")  # the untouched .env.example
    assert load_env_files([tmp_path]) == []
    assert "OPENROUTER_API_KEY" not in os.environ


def test_a_file_that_cannot_be_read_is_not_an_error(tmp_path):
    (tmp_path / ".env").mkdir()  # a directory of that name
    assert load_env_files([tmp_path]) == []


def test_every_command_loads_it_and_no_command_ever_prints_a_value(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={SECRET}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["status", "--run-dir", str(tmp_path / "no-such-run")]) == 1
    assert os.environ["OPENROUTER_API_KEY"] == SECRET
    printed = capsys.readouterr()
    assert SECRET not in printed.out and SECRET not in printed.err


def test_preflight_says_where_a_key_came_from_by_name(tmp_path, monkeypatch, capsys, minimal_raw):
    import yaml

    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={SECRET}\nEK_TEST_KEY=1\n", encoding="utf-8")
    config_path = tmp_path / "evolvekit.yaml"
    config_path.write_text(yaml.safe_dump(minimal_raw), encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)  # not the config's directory: found through the config
    main(["preflight", "--config", str(config_path)])
    printed = capsys.readouterr()
    assert "OPENROUTER_API_KEY" in printed.out and str((tmp_path / ".env").resolve()) in printed.out
    assert SECRET not in printed.out and SECRET not in printed.err


def test_it_can_be_switched_off(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("EK_TEST_KEY=1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVOLVEKIT_NO_DOTENV", "1")
    main(["status", "--run-dir", str(tmp_path / "no-such-run")])
    assert "EK_TEST_KEY" not in os.environ
