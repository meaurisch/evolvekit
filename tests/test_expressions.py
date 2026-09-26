"""The expression language behind `problem.parameter_constraints`.

`CASES` is shared on purpose: the harness SDK carries its own copy of the
language (it runs under the application's Python, standard library only), and
both copies are held to the same table.
"""

from __future__ import annotations

import pytest

from evolvekit.expressions import Expression, ExpressionError

CASES = [
    ("a >= b", {"a": 2, "b": 1}, True),
    ("a >= b", {"a": 1, "b": 2}, False),
    ("0 <= a - b <= 5", {"a": 7, "b": 3}, True),
    ("0 <= a - b <= 5", {"a": 9, "b": 3}, False),
    ("a * 2 + b ** 2 - c / 4 >= c % 3", {"a": 1.5, "b": 2, "c": 8}, True),
    ("abs(a - b) < 1 and not flag", {"a": 1.0, "b": 1.5, "flag": False}, True),
    ("min(a, b) > 0 or max(a, b) > 10", {"a": -1, "b": 11}, True),
    ("round(a) == 3", {"a": 2.6}, True),
    ("mode == 'fast' or a > 1", {"mode": "fast", "a": 0}, True),
    ('mode != "fast"', {"mode": "slow"}, True),
    ("-a < +b", {"a": 1, "b": 1}, True),
    ("flag == True", {"flag": True}, True),
    ("a > 1 or b > 1 and c > 1", {"a": 0, "b": 2, "c": 2}, True),
    ("not (a > 1)", {"a": 0}, True),
    ("a + 1", {"a": 1}, 2),
    ("a / b > 1", {"a": 1, "b": 0}, ExpressionError),  # division by zero
    ("a % b > 1", {"a": 1, "b": 0}, ExpressionError),  # modulo by zero
    ("a ** b > 1", {"a": 10.0, "b": 400}, ExpressionError),  # overflow
    ("a ** 0.5 > 1", {"a": -4.0}, ExpressionError),  # a complex number
    ("a < mode", {"a": 1, "mode": "x"}, ExpressionError),  # a number against a string
    ("a > 1", {}, ExpressionError),  # a name without a value
]


@pytest.mark.parametrize("text, values, expected", CASES)
def test_the_shared_cases(text, values, expected):
    expression = Expression.parse(text)
    if isinstance(expected, type) and issubclass(expected, Exception):
        with pytest.raises(expected):
            expression.evaluate(values)
    else:
        assert expression.evaluate(values) == expected


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os')",
        "a.b > 1",
        "a[0] > 1",
        "(lambda: 1)() > 0",
        "f(a) > 1",
        "[a] == [1]",
        "a if b else c",
        "{a: 1}",
        "(a := 1)",
        "open('x')",
        "abs(a, key=b) > 1",
        "abs(*a) > 1",
        "a is None",
        "a in b",
        "a > 1; b",
        "1 < ",
        "",
        "   ",
        "b'x' == a",
        "1j > 0",
    ],
)
def test_anything_else_is_refused_when_it_is_read(text):
    with pytest.raises(ExpressionError):
        Expression.parse(text)


def test_the_names_it_uses_are_known_before_it_is_evaluated():
    expression = Expression.parse("abs(a - b) < c and mode == 'x'")
    assert expression.names == frozenset({"a", "b", "c", "mode"})
    assert expression.text == "abs(a - b) < c and mode == 'x'"


def test_a_condition_is_a_comparison_or_logic_not_a_number():
    with pytest.raises(ExpressionError, match="not a condition"):
        Expression.parse("a + 1").holds({"a": 1})
    assert Expression.parse("a > 0").holds({"a": 1}) is True
    assert Expression.parse("a > 0").holds({"a": -1}) is False


def test_and_or_stop_early_like_python_does():
    # The right-hand side would divide by zero; it is never reached.
    assert Expression.parse("b == 0 or a / b > 1").evaluate({"a": 1, "b": 0}) is True
    assert Expression.parse("b != 0 and a / b > 1").evaluate({"a": 1, "b": 0}) is False


def test_the_errors_say_what_is_wrong():
    with pytest.raises(ExpressionError, match="division by zero"):
        Expression.parse("a / b > 1").evaluate({"a": 1, "b": 0})
    with pytest.raises(ExpressionError, match="'q'"):
        Expression.parse("q > 1").evaluate({})
    with pytest.raises(ExpressionError, match="attribute"):
        Expression.parse("a.b > 1")
    with pytest.raises(ExpressionError, match="only abs, max, min and round"):
        Expression.parse("f(a) > 1")
