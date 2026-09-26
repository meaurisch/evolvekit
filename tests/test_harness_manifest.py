"""`harness.yaml`: loaded, checked, refused in one sentence naming the key."""

from __future__ import annotations

import copy

import pytest

from evolvekit.harness import HarnessError
from evolvekit.harness.manifest import load_harness
from harness_toy import MANIFEST, TEMPLATES, write_toy_harness


def test_the_toy_harness_loads(tmp_path):
    harness = load_harness(write_toy_harness(tmp_path / "toy"))
    assert (harness.id, harness.version, harness.key) == ("toy", "1.0.0", "toy-1.0.0")
    assert harness.application.kind == "python" and harness.time_limit.accepts
    assert list(harness.settings) == ["threshold", "factor", "crash"]
    assert harness.settings["threshold"].parameter.high == 10.0 and harness.settings["threshold"].group == "Picking"
    assert harness.recommended == ["threshold", "factor"]
    assert harness.request_tables == ["items"]
    assert harness.declared()["summary"] == {"total": "float", "time_limit": "float"}
    assert harness.levers["item_values"].columns == {"value": "value"} and harness.levers["boost"].code
    assert harness.kpis["picked_count"].measure and harness.kpis["value_moved"].changes_with_levers
    assert set(harness.kpi_templates["kind_value"].params) == {"kind"}
    assert set(harness.templates) == {"tune", "values"}
    assert harness.inputs["start"].provides == "settings"


def _broken(**changes):
    manifest = copy.deepcopy(MANIFEST)
    for dotted, value in changes.items():
        keys = dotted.split("__")
        target = manifest
        for key in keys[:-1]:
            target = target[key]
        if value is DELETE:
            del target[keys[-1]]
        else:
            target[keys[-1]] = value
    return manifest


DELETE = object()


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"colour": "blue"}, r"<root>: unknown key\(s\) \['colour'\]"),
        ({"harness": 2}, r"harness: the format version must be 1"),
        ({"id": "PyVRP"}, r"id: lower-case letters, digits and dashes"),
        ({"version": "1.0"}, r"version: three numbers such as 1.0.0"),
        ({"application__kind": "java"}, r"application\.kind: must be 'python' or 'program'"),
        ({"application": {"kind": "program", "label": "x", "probe": "solver --version"}},
         r"application\.probe: must contain \{app\}"),
        ({"application": {"kind": "program", "label": "x", "probe": "{app} -v", "expect": "no group"}},
         r"application\.expect: needs a group for the version"),
        ({"cases__formats": ["json"]}, r"cases\.formats: expected a list of file extensions"),
        ({"settings__factor__default": 5}, r"settings\.factor\.default: 5 is above its maximum 2"),
        ({"settings__factor__weight": 1}, r"settings\.factor: unknown key\(s\) \['weight'\]"),
        ({"tables__items__source": DELETE}, r"tables\.items\.source: required"),
        ({"tables__items__columns__value__type": "decimal"}, r"tables\.items\.columns\.value\.type: must be one of"),
        ({"tables__items__columns__kind__private": True, "tables__items__columns__kind__categorical": True},
         r"tables\.items\.columns\.kind: a column is categorical .* or private .*, not both"),
        ({"tables__orig_items": {"source": "request", "columns": {"x": {"type": "int"}}}},
         r"tables\.orig_items: `orig_` names the untouched copy"),
        ({"levers__item_values__table": "things"}, r"levers\.item_values\.table: 'things' is not a declared table"),
        ({"levers__item_values__table": "picks"}, r"levers\.item_values\.table: 'picks' is a solution table"),
        ({"levers__item_values__columns": {"kind": {"label": "kind"}}},
         r"levers\.item_values\.columns\.kind: a lever changes numbers, and 'kind' is text"),
        ({"levers__item_values__columns": {"price": {}}}, r"levers\.item_values\.columns\.price: table 'items' has no column 'price'"),
        ({"levers__item_values__modes": ["double"]}, r"levers\.item_values\.modes: a list of"),
        ({"kpis__total__sql": "SELECT totl FROM summary"},
         r"kpis\.total\.sql: does not run against the declared tables: no such column: totl"),
        ({"kpis__total__sql": "DELETE FROM summary"}, r"kpis\.total\.sql: .*it may only read the tables"),
        ({"kpis__total__measure": True}, r"kpis\.total: give either `sql` or `measure: true`, not both"),
        ({"kpis__total__direction": "down"}, r"kpis\.total\.direction: must be 'lower' or 'higher'"),
        ({"kpis__factor": {"label": "x", "direction": "lower", "measure": True}}, r"kpis\.factor: a KPI and a setting share the name"),
        ({"kpi_templates__kind_value__sql": "SELECT TOTAL(value) FROM items WHERE kind = :kind AND id > :size"},
         r"kpi_templates\.kind_value: the SQL's blanks and `params` must match; undeclared: \['size'\]"),
        ({"kpi_templates__kind_value__params__kind__from": "SELECT DISTINCT kinds FROM items"},
         r"kpi_templates\.kind_value\.params\.kind\.from: does not run against the declared tables"),
        ({"inputs__start__provides": "anything"}, r"inputs\.start\.provides: 'settings'"),
        ({"exports__settings_json__for": "everything"}, r"exports\.settings_json\.for: must be 'settings' or 'data'"),
        ({"defaults__test_share": 1.5}, r"defaults\.test_share: a share in \[0, 1\)"),
        ({"time_limit__default_s": 0.5}, r"time_limit\.default_s: 0.5 is below min_s 1"),
    ],
)
def test_a_mistake_is_one_sentence_naming_its_key(tmp_path, changes, message):
    with pytest.raises(HarnessError, match=message):
        load_harness(write_toy_harness(tmp_path / "toy", manifest=_broken(**changes)))


def test_a_study_template_that_does_not_compile_is_refused(tmp_path):
    templates = dict(TEMPLATES, bad={"title": "Bad", "vary": {"settings": "recommended"},
                                     "kpis": {"total": {"from": "harness"}},
                                     "goal": {"levels": [{"kpi": "nothing", "direction": "lower"}]}})
    with pytest.raises(HarnessError, match=r"templates/bad\.yaml: goal\.levels\[0\]\.kpi: 'nothing' is not a KPI of the study"):
        load_harness(write_toy_harness(tmp_path / "toy", templates=templates))


def test_a_template_with_an_unknown_key_is_refused(tmp_path):
    templates = dict(TEMPLATES, odd={"title": "Odd", "colour": "blue"})
    with pytest.raises(HarnessError, match=r"templates/odd\.yaml: unknown key\(s\) \['colour'\]"):
        load_harness(write_toy_harness(tmp_path / "toy", templates=templates))


def test_a_folder_without_a_manifest(tmp_path):
    with pytest.raises(HarnessError, match="there is no harness.yaml in this folder"):
        load_harness(tmp_path)
