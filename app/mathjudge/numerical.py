"""Secret-seeded high-precision fingerprint used after exact comparison.

This is an explicitly numerical policy: a matching fingerprint is treated as
AC by ExactRoom, even though finite point evaluation is not a mathematical
proof of identity.  The seed must be created server-side and never returned.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import sympy as sp
from sympy import S

COMPARE_PRECISION = 10_000
WORKING_PRECISION = 12_000
REQUIRED_SAMPLES = 64
MAX_ATTEMPTS = 256


@dataclass(frozen=True, slots=True)
class FingerprintResult:
    equivalent: bool
    precision: int
    valid_samples: int
    attempted_samples: int
    reason: str


def _finite_real(value) -> bool:
    return isinstance(value, sp.Expr) and not value.free_symbols and not value.has(
        sp.zoo, sp.nan, sp.oo, -sp.oo
    ) and value.is_real is not False


def _evaluate(value):
    if not _finite_real(value):
        return None
    try:
        result = value.evalf(n=WORKING_PRECISION, strict=True, maxn=WORKING_PRECISION)
    except Exception:
        return None
    if not isinstance(result, sp.Expr) or result.has(sp.zoo, sp.nan, sp.oo, -sp.oo):
        return None
    if result.is_real is False:
        return None
    return result


def _tolerance(left, right):
    scale = max(S.One, abs(left), abs(right))
    return sp.Float(10, WORKING_PRECISION) ** (-COMPARE_PRECISION) * (1 + scale)


def _close(left, right) -> bool | None:
    a, b = _evaluate(left), _evaluate(right)
    if a is None or b is None:
        return None
    try:
        delta = abs((a - b).evalf(n=WORKING_PRECISION, strict=True))
        tolerance = _tolerance(a, b)
        result = delta <= tolerance
        return bool(result) if result in (S.true, S.false, True, False) else None
    except Exception:
        return None


def _relation_truth(value, substitutions: dict) -> bool | None:
    try:
        value = value.subs(substitutions, simultaneous=True)
        if value in (S.true, S.false, True, False):
            return bool(value)
        if isinstance(value, sp.core.relational.Relational):
            left, right = value.lhs, value.rhs
            if isinstance(value, sp.Equality):
                close = _close(left, right)
                return close
            if isinstance(value, sp.Unequality):
                close = _close(left, right)
                return None if close is None else not close
            left_value, right_value = _evaluate(left), _evaluate(right)
            if left_value is None or right_value is None:
                return None
            difference = (left_value - right_value).evalf(
                n=WORKING_PRECISION, strict=True
            )
            tol = _tolerance(left_value, right_value)
            if isinstance(value, sp.StrictGreaterThan):
                return bool(difference > tol)
            if isinstance(value, sp.GreaterThan):
                return bool(difference >= -tol)
            if isinstance(value, sp.StrictLessThan):
                return bool(difference < -tol)
            if isinstance(value, sp.LessThan):
                return bool(difference <= tol)
        if isinstance(value, sp.And):
            values = [_relation_truth(part, {}) for part in value.args]
            return None if any(v is None for v in values) else all(values)
        if isinstance(value, sp.Or):
            values = [_relation_truth(part, {}) for part in value.args]
            return None if any(v is None for v in values) else any(values)
    except Exception:
        return None
    return None


def _point_is_valid(conditions, substitutions: dict) -> bool | None:
    for condition in conditions or ():
        truth = _relation_truth(condition, substitutions)
        if truth is not True:
            return False if truth is False else None
    return True


def _sample_values(symbol, rng: random.Random):
    values = []
    for _ in range(4):
        denominator = rng.randrange(17, 503)
        numerator = rng.randrange(-997, 998)
        if symbol.is_positive is True:
            numerator = abs(numerator) or 1
        elif symbol.is_nonnegative is True:
            numerator = abs(numerator)
        value = sp.Rational(numerator, denominator)
        if symbol.is_integer is True:
            value = sp.Integer(numerator)
        values.append(value)
    return values


def _sample_points(symbols, seed: bytes):
    rng = random.Random(int.from_bytes(seed, "big"))
    symbols = sorted(symbols, key=lambda item: item.name)
    special = (S.Zero, S.One, -S.One, S(2), S(-2), sp.Rational(1, 2), sp.Rational(-1, 2))
    for value in special:
        yield {symbol: value for symbol in symbols}
    per_symbol = {symbol: _sample_values(symbol, rng) for symbol in symbols}
    for _ in range(MAX_ATTEMPTS - len(special)):
        yield {symbol: rng.choice(per_symbol[symbol]) for symbol in symbols}


def _item_value(item, substitutions):
    try:
        if isinstance(item, sp.core.relational.Relational):
            return _relation_truth(item, substitutions)
        value = item.subs(substitutions, simultaneous=True)
        return _evaluate(value)
    except Exception:
        return None


def _compare_values(left, right) -> bool | None:
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right if isinstance(left, bool) and isinstance(right, bool) else False
    return _close(left, right)


def _collections_match(left_items, right_items, substitutions, ordered: bool) -> bool | None:
    if len(left_items) != len(right_items):
        return False
    left = [_item_value(item, substitutions) for item in left_items]
    right = [_item_value(item, substitutions) for item in right_items]
    if any(value is None for value in left + right):
        return None
    if ordered:
        checks = [_compare_values(a, b) for a, b in zip(left, right, strict=True)]
        return None if any(value is None for value in checks) else all(checks)

    edges = []
    for a in left:
        row = [_compare_values(a, b) for b in right]
        edges.append(row)
    if any(value is None for row in edges for value in row):
        return None

    def perfect(index, used):
        if index == len(edges):
            return True
        return any(
            edges[index][j] is True and j not in used and perfect(index + 1, used | {j})
            for j in range(len(edges))
        )

    return perfect(0, set())


def numerical_fingerprint(model, submitted, *, seed: bytes, ordered_list=False):
    """Return numerical evidence or ``None`` if the fallback could not decide."""
    symbols = set()
    for parsed in (model, submitted):
        for item in parsed.items:
            symbols.update(getattr(item, "free_symbols", set()))
        for condition in parsed.domain_conditions:
            symbols.update(condition.free_symbols)

    valid = 0
    attempted = 0
    if not symbols:
        if model.kind == "expr" and submitted.kind == "expr" or model.kind == "rel" and submitted.kind == "rel":
            same = _compare_values(_item_value(model.single, {}), _item_value(submitted.single, {}))
        elif model.kind in ("set", "list") and submitted.kind in ("set", "list"):
            left_items = list(model.single.args) if model.kind == "set" and isinstance(model.single, sp.FiniteSet) else list(model.items)
            right_items = list(submitted.single.args) if submitted.kind == "set" and isinstance(submitted.single, sp.FiniteSet) else list(submitted.items)
            same = _collections_match(
                left_items,
                right_items,
                {},
                ordered=ordered_list or model.kind == "list" or submitted.kind == "list",
            )
        else:
            same = False
        if same is None:
            return None
        return FingerprintResult(bool(same), COMPARE_PRECISION, 1, 1, "numeric_fingerprint")

    for substitutions in _sample_points(symbols, seed):
        attempted += 1
        if symbols and any(
            (symbol.is_positive is True and substitutions[symbol].is_positive is not True)
            or (symbol.is_nonnegative is True and substitutions[symbol].is_negative is True)
            or (symbol.is_real is True and substitutions[symbol].is_real is not True)
            or (symbol.is_integer is True and substitutions[symbol].is_integer is not True)
            for symbol in symbols
        ):
            continue

        left_domain = _point_is_valid(model.domain_conditions, substitutions)
        right_domain = _point_is_valid(submitted.domain_conditions, substitutions)
        if left_domain is False and right_domain is True:
            return FingerprintResult(False, COMPARE_PRECISION, valid, attempted, "domain_counterexample")
        if right_domain is False and left_domain is True:
            return FingerprintResult(False, COMPARE_PRECISION, valid, attempted, "domain_counterexample")
        if left_domain is not True or right_domain is not True:
            continue

        if model.kind == "expr" and submitted.kind == "expr":
            left = _item_value(model.single, substitutions)
            right = _item_value(submitted.single, substitutions)
            same = _compare_values(left, right)
        elif model.kind == "rel" and submitted.kind == "rel":
            same = _compare_values(
                _item_value(model.single, substitutions),
                _item_value(submitted.single, substitutions),
            )
        elif model.kind in ("set", "list") and submitted.kind in ("set", "list"):
            left_items = list(model.single.args) if model.kind == "set" and isinstance(model.single, sp.FiniteSet) else list(model.items)
            right_items = list(submitted.single.args) if submitted.kind == "set" and isinstance(submitted.single, sp.FiniteSet) else list(submitted.items)
            same = _collections_match(
                left_items,
                right_items,
                substitutions,
                ordered=ordered_list or model.kind == "list" or submitted.kind == "list",
            )
        else:
            return FingerprintResult(False, COMPARE_PRECISION, valid, attempted, "kind_mismatch")

        if same is False:
            return FingerprintResult(False, COMPARE_PRECISION, valid + 1, attempted, "numeric_counterexample")
        if same is True:
            valid += 1
            if valid >= REQUIRED_SAMPLES:
                return FingerprintResult(True, COMPARE_PRECISION, valid, attempted, "numeric_fingerprint")

    if not symbols:
        return FingerprintResult(False, COMPARE_PRECISION, valid, attempted, "numeric_evaluation_failed")
    if valid == REQUIRED_SAMPLES:
        return FingerprintResult(True, COMPARE_PRECISION, valid, attempted, "numeric_fingerprint")
    return None
