"""Conservative one-variable solution-set comparison for relations."""

from __future__ import annotations

import sympy as sp
from sympy import S
from sympy.core.relational import Relational


def _symbol_domain(symbol):
    domain = S.Integers if symbol.is_integer is True else (
        S.Reals if symbol.is_real is not False else S.Complexes
    )
    if symbol.is_positive is True:
        domain = sp.Intersection(domain, sp.Interval.open(0, sp.oo))
    elif symbol.is_nonnegative is True:
        domain = sp.Intersection(domain, sp.Interval(0, sp.oo))
    return domain


def _relation_set(value, symbol, domain):
    """Return an exact solution set when SymPy can determine one safely."""
    if value is S.true:
        return domain
    if value is S.false:
        return S.EmptySet
    if isinstance(value, sp.Equality):
        return sp.solveset(value.lhs - value.rhs, symbol, domain=domain)
    if isinstance(value, sp.Unequality):
        roots = sp.solveset(value.lhs - value.rhs, symbol, domain=domain)
        return sp.Complement(domain, roots)
    if isinstance(value, sp.And):
        parts = [_relation_set(part, symbol, domain) for part in value.args]
        if any(part is None for part in parts):
            return None
        return sp.Intersection(*parts)
    if isinstance(value, sp.Or):
        parts = [_relation_set(part, symbol, domain) for part in value.args]
        if any(part is None for part in parts):
            return None
        return sp.Union(*parts)
    if isinstance(value, Relational):
        if not value.free_symbols:
            resolved = sp.simplify(value)
            if resolved is S.true:
                return domain
            if resolved is S.false:
                return S.EmptySet
            return None
        try:
            return value.as_set()
        except (NotImplementedError, ValueError, TypeError):
            return None
    return None


def compare_relation_sets(
    left,
    right,
    left_domain_conditions=(),
    right_domain_conditions=(),
) -> str | None:
    """Compare one-variable relation solution sets.

    ``None`` means the specialized route did not apply. ``UNDECIDED`` means
    it applied, but exact set equality or inequality could not be established.
    """
    from .equivalence import EQUIVALENT, NOT_EQUIVALENT, UNDECIDED

    conditions = list(left_domain_conditions) + list(right_domain_conditions)
    symbols = sorted(
        left.free_symbols
        | right.free_symbols
        | set().union(*(condition.free_symbols for condition in conditions)),
        key=lambda s: s.name,
    )
    if len(symbols) > 1:
        return None
    if symbols:
        symbol = symbols[0]
        domain = _symbol_domain(symbol)
    else:
        symbol = sp.Symbol("_exactroom_dummy", real=True)
        domain = S.Reals

    try:
        left_set = _relation_set(left, symbol, domain)
        right_set = _relation_set(right, symbol, domain)
        if left_set is None or right_set is None:
            return UNDECIDED
        for condition in left_domain_conditions:
            condition_set = _relation_set(condition, symbol, domain)
            if condition_set is None:
                return UNDECIDED
            left_set = sp.Intersection(left_set, condition_set)
        for condition in right_domain_conditions:
            condition_set = _relation_set(condition, symbol, domain)
            if condition_set is None:
                return UNDECIDED
            right_set = sp.Intersection(right_set, condition_set)
        difference = sp.SymmetricDifference(left_set, right_set)
    except Exception:
        return UNDECIDED

    if difference is S.EmptySet or difference.is_empty is True:
        return EQUIVALENT
    if difference.is_empty is False:
        return NOT_EQUIVALENT
    return UNDECIDED


def compare_expression_domains(left_conditions, right_conditions) -> str:
    """Compare explicitly tracked real-domain constraints for expressions.

    Nonzero conditions introduced only by division are ignored here to keep
    the legacy algebraic-expression mode (for example, removable factors).
    Logarithm, even-root, and inverse-trig restrictions remain significant.
    """
    from .equivalence import EQUIVALENT, NOT_EQUIVALENT, UNDECIDED

    left_conditions = [
        item for item in left_conditions if not isinstance(item, sp.Unequality)
    ]
    right_conditions = [
        item for item in right_conditions if not isinstance(item, sp.Unequality)
    ]
    symbols = sorted(
        set().union(
            *(item.free_symbols for item in left_conditions + right_conditions)
        ),
        key=lambda s: s.name,
    )
    if not symbols:
        left_set = S.Reals
        right_set = S.Reals
        for conditions, side in (
            (left_conditions, "left"),
            (right_conditions, "right"),
        ):
            target = S.Reals
            for condition in conditions:
                subset = _relation_set(condition, sp.Symbol("_domain_dummy"), S.Reals)
                if subset is None:
                    return UNDECIDED
                target = sp.Intersection(target, subset)
            if side == "left":
                left_set = target
            else:
                right_set = target
        return EQUIVALENT if left_set == right_set else NOT_EQUIVALENT
    if len(symbols) != 1:
        return UNDECIDED

    symbol = symbols[0]
    domain = _symbol_domain(symbol)

    def allowed_set(conditions):
        result = domain
        for condition in conditions:
            subset = _relation_set(condition, symbol, domain)
            if subset is None:
                return None
            result = sp.Intersection(result, subset)
        return result

    try:
        left_set = allowed_set(left_conditions)
        right_set = allowed_set(right_conditions)
        if left_set is None or right_set is None:
            return UNDECIDED
        difference = sp.SymmetricDifference(left_set, right_set)
    except Exception:
        return UNDECIDED
    if difference is S.EmptySet or difference.is_empty is True:
        return EQUIVALENT
    if difference.is_empty is False:
        return NOT_EQUIVALENT
    return UNDECIDED
