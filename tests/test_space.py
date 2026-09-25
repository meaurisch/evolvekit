"""The typed parameter space: declaration, validation, rendering, movement."""

from __future__ import annotations

import ast
import random

import pytest

from evolvekit.space import Parameter, ParameterSpace, SpaceError

RAW = {
    "num_neighbours": {"type": "int", "low": 10, "high": 120, "default": 50, "help": "arcs kept per client"},
    "max_penalty": {"type": "float", "low": 1e3, "high": 1e7, "default": 1e5, "log": True},
    "exhaustive": {"type": "bool", "default": True},
    "init": {"type": "choice", "choices": ["greedy", "random", "savings"], "default": "greedy", "flag": "-I"},
}


@pytest.fixture
def space() -> ParameterSpace:
    return ParameterSpace.parse(RAW)


# -- declaration -----------------------------------------------------------


def test_the_defaults_are_a_valid_configuration_and_the_baseline(space):
    defaults = space.defaults()
    assert defaults == {"num_neighbours": 50, "max_penalty": 100000.0, "exhaustive": True, "init": "greedy"}
    assert space.validate(defaults) == (defaults, [])
    assert isinstance(defaults["num_neighbours"], int) and isinstance(defaults["max_penalty"], float)


@pytest.mark.parametrize(
    "name, spec, fragment",
    [
        ("x", {"type": "integer", "default": 1}, "must be one of"),
        ("x", {"type": "int", "low": 1, "high": 9}, "default: required"),
        ("x", {"type": "int", "low": 9, "high": 1, "default": 3}, "low must be below high"),
        ("x", {"type": "int", "low": 1.5, "high": 9, "default": 3}, "whole-number bounds"),
        ("x", {"type": "int", "low": 1, "high": 9, "default": 30}, "above its maximum"),
        ("x", {"type": "float", "low": 0, "high": 9, "default": 3, "log": True}, "log scale needs low > 0"),
        ("x", {"type": "float", "low": 0, "default": 3}, "needs a finite number"),
        ("x", {"type": "float", "low": "lots", "high": 9, "default": 3}, "needs a finite number"),
        ("x", {"type": "float", "low": ".nan", "high": 9, "default": 3}, "needs a finite number"),
        ("x", {"type": "float", "low": 0, "high": 9, "default": "three"}, "expected a finite float"),
        ("x", {"type": "bool", "default": "yes"}, "expected true or false"),
        ("x", {"type": "bool", "default": True, "low": 0}, "only int and float parameters have a range"),
        ("x", {"type": "choice", "choices": ["a"], "default": "a"}, "at least two distinct values"),
        ("x", {"type": "choice", "choices": ["a", "b"], "default": "c"}, "expected one of"),
        ("x", {"type": "int", "low": 1, "high": 9, "default": 3, "lo": 0}, "unknown key(s) ['lo']"),
        ("not a name", {"type": "bool", "default": True}, "plain identifier"),
        ("x", "int", "expected a mapping"),
    ],
)
def test_a_mistake_in_a_declaration_is_reported_where_it_was_made(name, spec, fragment):
    with pytest.raises(SpaceError) as error:
        ParameterSpace.parse({name: spec})
    assert fragment in str(error.value)
    assert "problem.parameters" in str(error.value), "the key path has to be in the message"


def test_numbers_that_yaml_reads_as_strings_are_numbers():
    # PyYAML follows YAML 1.1, where a float needs a dot and a signed exponent:
    # `1e5` is the *string* "1e5". The example in `evolvekit.space`'s own
    # docstring is written exactly that way, and was refused with "a float
    # parameter needs a finite number".
    import yaml

    import evolvekit.space

    example = evolvekit.space.__doc__.split("parameters:\n", 1)[1].split("\n\n", 1)[0]
    raw = yaml.safe_load(example)
    assert raw["max_penalty"]["low"] == "1e3", "the premise: YAML hands over a string"
    space = ParameterSpace.parse(raw)
    penalty = next(p for p in space if p.name == "max_penalty")
    assert (penalty.low, penalty.high, penalty.default) == (1e3, 1e7, 1e5)
    assert space.defaults()["max_penalty"] == 100000.0
    counted = ParameterSpace.parse({"n": {"type": "int", "low": "1e1", "high": "1e3", "default": "5e1"}})
    assert space.validate(space.defaults())[1] == [] and counted.defaults() == {"n": 50}
    assert isinstance(counted.parameters[0].low, int)


def test_an_empty_space_is_refused():
    with pytest.raises(SpaceError, match="non-empty mapping"):
        ParameterSpace.parse({})


# -- a configuration, checked ----------------------------------------------


def test_a_parameter_left_out_takes_its_default(space):
    resolved, problems = space.validate({"num_neighbours": 30})
    assert problems == []
    assert resolved == {**space.defaults(), "num_neighbours": 30}


@pytest.mark.parametrize(
    "values, fragment",
    [
        ({"num_neighbours": 500}, "num_neighbours: 500 is above its maximum 120"),
        ({"num_neighbours": 3}, "is below its minimum 10"),
        ({"num_neighbours": 30.5}, "expected a whole number"),
        ({"num_neighbours": "many"}, "expected a finite int"),
        ({"max_penalty": float("nan")}, "expected a finite float"),
        ({"exhaustive": 1}, "expected true or false"),
        ({"init": "nearest"}, "expected one of ['greedy', 'random', 'savings']"),
        ({"neighbours": 30}, "unknown parameter 'neighbours'"),
    ],
)
def test_an_illegal_value_is_a_problem_never_a_silent_clamp(space, values, fragment):
    _resolved, problems = space.validate(values)
    assert len(problems) == 1 and fragment in problems[0]


def test_something_that_is_not_a_dict_is_said_to_be_so(space):
    assert space.validate([1, 2])[1] == ["configure() must return a dict, got list"]


def test_a_whole_number_given_as_a_float_is_accepted_as_the_int_it_is(space):
    resolved, problems = space.validate({"num_neighbours": 40.0})
    assert problems == [] and resolved["num_neighbours"] == 40 and isinstance(resolved["num_neighbours"], int)


# -- a configuration, written down -----------------------------------------


def test_the_block_is_python_that_returns_exactly_the_configuration(space):
    values = {"num_neighbours": 33, "max_penalty": 2500.0, "exhaustive": False, "init": "savings"}
    block = space.render_block(values)
    namespace: dict = {}
    exec(compile(ast.parse(block), "<block>", "exec"), namespace)  # noqa: S102 - our own text
    assert namespace["configure"]() == values
    assert block.endswith("\n")


def test_a_choice_with_quotes_in_it_is_still_python_and_still_itself():
    # The block used to be rendered with repr() and then every ' replaced by ",
    # which turned `it's` into "it"s" -- a syntax error in every candidate.
    odd = ["it's", 'say "hi"', "back\\slash", "tab\there", "naïve"]
    space = ParameterSpace.parse({"mode": {"type": "choice", "choices": odd, "default": "it's"}})
    for value in odd:
        block = space.render_block({"mode": value})
        namespace: dict = {}
        exec(compile(ast.parse(block), "<block>", "exec"), namespace)  # noqa: S102 - our own text
        assert namespace["configure"]() == {"mode": value}
    assert "\"mode\": \"it's\"," in space.render_block({"mode": "it's"}), "the house style: double quotes"


def test_the_generated_skeleton_has_one_fence_around_the_defaults(space):
    skeleton = space.render_skeleton("# EVOLVE-BLOCK-START", "# EVOLVE-BLOCK-END")
    assert skeleton.count("# EVOLVE-BLOCK-START") == 1 and skeleton.count("# EVOLVE-BLOCK-END") == 1
    namespace: dict = {}
    exec(compile(ast.parse(skeleton), "<skeleton>", "exec"), namespace)  # noqa: S102
    assert namespace["configure"]() == space.defaults()


def test_flags_follow_the_declaration_order_and_booleans_are_words(space):
    values = {"num_neighbours": 33, "max_penalty": 2500.0, "exhaustive": False, "init": "savings"}
    assert space.render_flags(values) == [
        "--num-neighbours", "33", "--max-penalty", "2500.0", "--exhaustive", "false", "-I", "savings",
    ]


# -- moving through the space ----------------------------------------------


def test_a_latin_hypercube_covers_every_stratum_of_every_parameter(space):
    samples = space.latin_hypercube(10, random.Random(3))
    assert len(samples) == 10 and all(space.validate(s)[1] == [] for s in samples)
    neighbours = sorted(s["num_neighbours"] for s in samples)
    assert neighbours[0] <= 21 and neighbours[-1] >= 109, "both ends of the range are reached"
    # On a log scale every decade gets its share: uniform sampling of
    # [1e3, 1e7] would put nine in ten samples above 1e6.
    decades = {int(str(f"{s['max_penalty']:.0e}").split("e+")[1]) for s in samples}
    assert decades >= {3, 4, 5, 6}
    assert {s["init"] for s in samples} == {"greedy", "random", "savings"}


def test_a_perturbation_stays_close_and_always_changes_something(space):
    rng = random.Random(11)
    base = space.defaults()
    children = [space.perturb(base, rng) for _ in range(200)]
    assert all(space.validate(child)[1] == [] for child in children)
    assert all(child != base for child in children), "an unchanged child is a wasted evaluation"
    assert base == space.defaults(), "the parent is not modified"
    mean_distance = sum(space.distance(base, child) for child in children) / len(children)
    far = [space.latin_hypercube(1, rng)[0] for _ in range(200)]
    assert mean_distance < 0.5 * sum(space.distance(base, f) for f in far) / len(far)


def test_a_perturbation_at_the_edge_of_a_range_stays_inside_it(space):
    rng = random.Random(5)
    edge = {**space.defaults(), "num_neighbours": 120, "max_penalty": 1e7}
    for _ in range(100):
        assert space.validate(space.perturb(edge, rng, scale=0.5))[1] == []


def test_crossover_takes_every_parameter_from_one_parent_or_the_other(space):
    a = {"num_neighbours": 10, "max_penalty": 1e3, "exhaustive": False, "init": "random"}
    b = {"num_neighbours": 120, "max_penalty": 1e7, "exhaustive": True, "init": "savings"}
    rng = random.Random(2)
    children = [space.crossover(a, b, rng) for _ in range(50)]
    assert all(child[name] in (a[name], b[name]) for child in children for name in space.names)
    assert any(child not in (a, b) for child in children)


def test_the_unit_scale_is_the_parameters_own(space):
    penalty = next(p for p in space if p.name == "max_penalty")
    assert penalty.to_unit(1e3) == pytest.approx(0.0) and penalty.to_unit(1e7) == pytest.approx(1.0)
    assert penalty.to_unit(1e5) == pytest.approx(0.5), "the middle of a log range is its geometric mean"
    assert penalty.from_unit(0.5) == pytest.approx(1e5)
    flag = next(p for p in space if p.name == "exhaustive")
    assert (flag.from_unit(0.0), flag.from_unit(1.0)) == (False, True)
    assert isinstance(Parameter.parse("n", RAW["num_neighbours"], "p").from_unit(0.31), int)


@pytest.mark.parametrize("log", [False, True])
def test_a_sample_at_the_edge_of_a_range_is_rounded_into_it_not_out_of_it(log):
    # Values are kept to six significant figures. Rounding *after* clamping put
    # a sample at the low end of [0.12345649, 0.98765451] at 0.123456 -- below
    # its own minimum -- and the child was then refused by validation.
    parameter = Parameter.parse(
        "rate", {"type": "float", "low": 0.12345649, "high": 0.98765451, "default": 0.5, "log": log}, "p"
    )
    for unit in (-0.5, 0.0, 1e-12, 0.5, 1.0 - 1e-12, 1.0, 1.5):
        value = parameter.from_unit(unit)
        assert parameter.problem_with(value) is None, (unit, value)


def test_a_numeric_choice_is_the_value_that_was_declared():
    # configure() is read back as JSON, and a model may well write 2.0 for the
    # declared 2. `2.0 in (1, 2, 4)` is true, so the value passed validation --
    # and went to the solver as `--threads 2.0`, which an integer option refuses.
    space = ParameterSpace.parse({"threads": {"type": "choice", "choices": [1, 2, 4], "default": 1}})
    resolved, problems = space.validate({"threads": 2.0})
    assert problems == [] and resolved == {"threads": 2} and isinstance(resolved["threads"], int)
    assert space.render_flags(resolved) == ["--threads", "2"]
