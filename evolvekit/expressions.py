"""A small, safe language for conditions on tuned values.

`problem.parameter_constraints` states what a configuration must satisfy before
it is worth running: "trucks stay at least as dear per metre as vans" is
`truck_km_cost >= van_km_cost`, checked in milliseconds instead of discovered
after an hour of solving. The expression is read with Python's `ast` and
evaluated by walking that tree -- never with `eval` -- and only a whitelist of
constructs is accepted, so a constraint can name values, do arithmetic and
logic, and nothing else: no calls beyond four harmless functions, no
attributes, no indexing, no imports.

Allowed:

    numbers, True, False, and 'strings' (for a choice parameter)
    names of values
    + - * / ** %, and a leading - or +
    == != < <= > >=, chained as in Python (0 <= a - b <= 5)
    and, or, not (short-circuiting, as in Python)
    abs(x), min(x, y, ...), max(x, y, ...), round(x[, digits])

Arithmetic is on numbers only, and `**` is done in floating point, so no
expression can build a string or an integer large enough to hang the process
that reads it. Anything that cannot be computed -- a division by zero, an
overflow, a number compared with a string, a name without a value -- is an
`ExpressionError` with a sentence saying which.

This module is standard library only on purpose: the harness SDK carries a
copy of the same language, and both are tested against one table of cases.
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = ["Expression", "ExpressionError", "FUNCTIONS"]

FUNCTIONS = ("abs", "max", "min", "round")


class ExpressionError(ValueError):
    """An expression that cannot be read, or cannot be computed."""


_ALLOWED = (
    ast.Expression,
    ast.BoolOp, ast.And, ast.Or,
    ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Call, ast.Name, ast.Load, ast.Constant,
)

_REFUSED = {
    ast.Attribute: "attribute access (`a.b`)",
    ast.Subscript: "indexing (`a[0]`)",
    ast.Lambda: "a lambda",
    ast.IfExp: "a conditional expression (`x if c else y`); use `and` / `or`",
    ast.NamedExpr: "an assignment (`:=`)",
    ast.List: "a list",
    ast.Tuple: "a tuple",
    ast.Set: "a set",
    ast.Dict: "a dict",
    ast.Starred: "unpacking (`*a`)",
    ast.Is: "`is`; compare with == or !=",
    ast.IsNot: "`is not`; compare with == or !=",
    ast.In: "`in`",
    ast.NotIn: "`not in`",
    ast.FloorDiv: "`//`",
    ast.BitAnd: "`&`; use `and`",
    ast.BitOr: "`|`; use `or`",
    ast.BitXor: "`^`",
    ast.Invert: "`~`; use `not`",
    ast.LShift: "`<<`",
    ast.RShift: "`>>`",
    ast.MatMult: "`@`",
}


def _refusal(node: ast.AST) -> str:
    what = _REFUSED.get(type(node))
    if what is None:
        what = type(node).__name__
    return f"{what} is not allowed in a condition"


def _check(tree: ast.Expression) -> frozenset[str]:
    """Refuse anything outside the whitelist; return the names of the values used."""
    functions: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ExpressionError(_refusal(node))
        if isinstance(node, ast.Constant) and not isinstance(node.value, (bool, int, float, str)):
            raise ExpressionError(
                f"the literal {node.value!r} is not allowed; use numbers, True, False or 'text'"
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                raise ExpressionError("only abs, max, min and round can be called")
            if node.keywords:
                raise ExpressionError(f"{node.func.id}() takes no keyword arguments here")
            functions.add(id(node.func))
    # A called function's name is not a value the expression needs.
    return frozenset(
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and id(node) not in functions
    )


@dataclass(frozen=True)
class Expression:
    """One parsed condition (or arithmetic expression) over named values."""

    text: str
    names: frozenset[str]
    _tree: ast.Expression = field(repr=False, compare=False)

    @staticmethod
    def parse(text: str) -> "Expression":
        if not isinstance(text, str) or not text.strip():
            raise ExpressionError("the expression is empty")
        source = text.strip()
        try:
            tree = ast.parse(source, mode="eval")
        except SyntaxError as exc:
            raise ExpressionError(f"not a valid expression: {exc.msg}") from None
        return Expression(text=source, names=_check(tree), _tree=tree)

    def evaluate(self, values: Mapping[str, Any]) -> Any:
        """The expression's value for `values`."""
        return _evaluate(self._tree.body, values)

    def holds(self, values: Mapping[str, Any]) -> bool:
        """Whether the condition is true for `values`. An expression whose
        value is not true or false is not a condition, and says so."""
        result = self.evaluate(values)
        if not isinstance(result, bool):
            raise ExpressionError(
                f"is not a condition: it gives {result!r}, not true or false. "
                "Compare it with something (`a + b <= 10`)"
            )
        return result


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, complex)


def _number(value: Any, what: str) -> float | int:
    if not _is_number(value):
        raise ExpressionError(f"{what} needs a number, got {value!r}")
    return value


def _finite(value: float | int) -> float | int:
    if isinstance(value, float) and not math.isfinite(value):
        raise ExpressionError("the arithmetic overflowed")
    return value


def _evaluate(node: ast.AST, values: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in values:
            raise ExpressionError(f"{node.id!r} has no value")
        return values[node.id]
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for operand in node.values:
                if not _evaluate(operand, values):
                    return False
            return True
        for operand in node.values:
            if _evaluate(operand, values):
                return True
        return False
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate(node.operand, values)
        if isinstance(node.op, ast.Not):
            return not operand
        number = _number(operand, "a sign")
        return -number if isinstance(node.op, ast.USub) else +number
    if isinstance(node, ast.BinOp):
        return _arithmetic(node.op, _evaluate(node.left, values), _evaluate(node.right, values))
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, values)
        for op, comparator in zip(node.ops, node.comparators):
            right = _evaluate(comparator, values)
            if not _compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        return _call(node.func.id, [_evaluate(arg, values) for arg in node.args])  # type: ignore[attr-defined]
    raise ExpressionError(_refusal(node))  # pragma: no cover - `_check` refused it already


def _arithmetic(op: ast.operator, left: Any, right: Any) -> float | int:
    symbol = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Pow: "**", ast.Mod: "%"}[type(op)]
    a, b = _number(left, f"`{symbol}`"), _number(right, f"`{symbol}`")
    try:
        if isinstance(op, ast.Add):
            return _finite(a + b)
        if isinstance(op, ast.Sub):
            return _finite(a - b)
        if isinstance(op, ast.Mult):
            return _finite(a * b)
        if isinstance(op, ast.Div):
            return _finite(a / b)
        if isinstance(op, ast.Mod):
            return _finite(a % b)
        # Floats, so that `a ** b` with a large whole exponent cannot take the
        # process's afternoon building an integer with a billion digits.
        result = float(a) ** float(b)
    except ZeroDivisionError:
        raise ExpressionError("division by zero") from None
    except OverflowError:
        raise ExpressionError("a number too large to compute") from None
    if isinstance(result, complex):
        raise ExpressionError("a negative number to a fractional power has no real value")
    return _finite(result)


def _compare(op: ast.cmpop, left: Any, right: Any) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if not ((_is_number(left) and _is_number(right)) or (isinstance(left, str) and isinstance(right, str))):
        raise ExpressionError(f"cannot order {left!r} and {right!r}: compare numbers with numbers, text with text")
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    return left >= right


def _call(name: str, args: list[Any]) -> float | int:
    if name == "abs":
        if len(args) != 1:
            raise ExpressionError("abs() takes one number")
        return abs(_number(args[0], "abs()"))
    if name in ("min", "max"):
        if len(args) < 2:
            raise ExpressionError(f"{name}() takes two or more numbers")
        numbers = [_number(arg, f"{name}()") for arg in args]
        return min(numbers) if name == "min" else max(numbers)
    # round
    if len(args) not in (1, 2):
        raise ExpressionError("round() takes a number and, optionally, how many digits")
    number = _number(args[0], "round()")
    if len(args) == 1:
        return round(number)
    digits = args[1]
    if isinstance(digits, bool) or not isinstance(digits, int):
        raise ExpressionError("round()'s digits must be a whole number")
    return round(number, digits)
