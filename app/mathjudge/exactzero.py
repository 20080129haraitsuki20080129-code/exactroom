"""「この式は厳密に 0 か?」を数値近似なしで判定する。

このモジュールは本システムの心臓部である。次を絶対に守る。

* ``evalf`` / ``N`` / ``float`` / ``complex`` / 数値近似を **判定に使わない**。
* ``Float`` を含む式は判定対象にしない (パーサ側で ``Rational`` に固定済み)。
* 「0 である」「0 でない」はどちらも *証明できたときだけ* 返す。
  証明できなければ ``UNKNOWN`` を返し、上位で「判定保留」にする。

SymPy の仮定システムのうち ``is_zero is False`` や ``is_positive`` は
内部で数値評価にフォールバックすることがあるため **使用しない**。
使ってよいのは、定理に基づく記号的な導出だけ:

* ``is_zero is True``            … 記号的な相殺からのみ導かれる
* ``is_irrational is True``      … 「無理数 ≠ 0」(0 は有理数)
* ``is_rational is False``       … 同上
* ``is_real is False``           … 「実数でない ⇒ 0 でない」
"""

from __future__ import annotations

import sympy as sp
from sympy import S

ZERO = "zero"
NONZERO = "nonzero"
UNKNOWN = "unknown"

#: 代入に使う厳密値 (すべて有理数か π の有理数倍。浮動小数点は使わない)
SAMPLE_VALUES: tuple = (
    S(2),
    S(3),
    sp.Rational(1, 2),
    S(-1),
    S(5),
    sp.Rational(2, 3),
    S(0),
    S(1),
    S(-2),
    sp.Rational(3, 5),
    sp.Rational(-4, 7),
    S(7),
    sp.pi / 4,
    sp.pi / 6,
    sp.pi / 3,
    sp.pi,
    sp.Rational(5, 2),
    S(-3),
    sp.Rational(1, 3),
    S(11),
)

MAX_SAMPLES = 24

#: 変形の重さを制御するためのコスト上限
COST_MEDIUM = 400
COST_HEAVY = 120
#: 代入結果がこれより大きくなったら、その標本は諦める
COST_SAMPLE_LIMIT = 5000

_BAD = (sp.zoo, sp.nan, sp.oo, -sp.oo)


# --------------------------------------------------------------------------
# 小道具
# --------------------------------------------------------------------------


def has_float(expr) -> bool:
    try:
        return bool(expr.atoms(sp.Float))
    except AttributeError:
        return False


def _is_structural_zero(expr) -> bool:
    return expr is S.Zero or (isinstance(expr, sp.Expr) and expr == S.Zero)


def cost(expr) -> int:
    """式の「重さ」の目安。大きな冪指数や巨大整数も加味する。

    ``x^400`` のような式は ``count_ops`` では小さく見えるが、
    ``factor`` や ``simplify`` にかけると爆発するのでここで弾く。
    """
    try:
        total = int(sp.count_ops(expr))
    except Exception:
        return 10**9
    try:
        for power in expr.atoms(sp.Pow):
            exponent = power.exp
            if exponent.is_Integer:
                total += min(abs(int(exponent)), 10**6)
        for number in expr.atoms(sp.Integer):
            digits = len(str(abs(int(number))))
            if digits > 20:
                total += digits
    except Exception:
        return 10**9
    return total


def _safe(func, expr):
    try:
        out = func(expr)
    except Exception:
        return None
    if out is None or has_float(out):
        return None
    return out


#: 0 を暴き出すための厳密な変形 (どれも数値近似を含まない)。
#: (上限コスト, 変形) の組。コストが上限を超える式には適用しない。
_SIMPLIFIERS: tuple[tuple[int, object], ...] = (
    (10**9, lambda e: sp.cancel(sp.together(e))),
    (COST_MEDIUM, lambda e: sp.expand(e)),
    (COST_MEDIUM, lambda e: sp.radsimp(e)),
    (COST_MEDIUM, lambda e: sp.sqrtdenest(sp.radsimp(e))),
    (COST_HEAVY, lambda e: sp.simplify(e)),
    (COST_HEAVY, lambda e: sp.simplify(sp.sqrtdenest(sp.expand(e)))),
    (COST_HEAVY, lambda e: sp.trigsimp(sp.expand_trig(e))),
    (COST_HEAVY, lambda e: sp.simplify(sp.expand_trig(e))),
    (COST_HEAVY, lambda e: sp.logcombine(sp.expand_log(e, force=False), force=False)),
    (COST_HEAVY, lambda e: sp.powsimp(sp.expand_power_exp(e), force=False)),
    (COST_HEAVY, lambda e: sp.factor(e)),
    (COST_HEAVY, lambda e: sp.combsimp(sp.expand_func(e))),
    (COST_HEAVY, lambda e: sp.simplify(sp.expand_func(e))),
)


def _try_prove_zero(expr, budget: int | None = None) -> bool:
    """厳密変形で 0 に落ちるかを試す。"""
    if _is_structural_zero(expr):
        return True
    if expr.is_zero is True:  # 記号的な相殺からのみ True になる
        return True
    weight = cost(expr) if budget is None else budget
    for limit, func in _SIMPLIFIERS:
        if weight > limit:
            continue
        out = _safe(func, expr)
        if out is None:
            continue
        if _is_structural_zero(out):
            return True
        if out.is_zero is True:
            return True
    return False


# --------------------------------------------------------------------------
# 「0 でない」ことの証明
# --------------------------------------------------------------------------


def _squarefree_radical(expr):
    """``sqrt(n)`` の積を ``(有理係数, 平方因子のない整数)`` に正規化する。

    対応できない形なら ``None``。
    """
    radicand = S.One
    factor = S.One
    factors = expr.args if expr.is_Mul else (expr,)
    for part in factors:
        if part.is_Rational:
            factor *= part
            continue
        if isinstance(part, sp.Pow) and part.exp == sp.Rational(1, 2):
            base = part.base
            if not (base.is_Integer and base > 0 and base < 10**12):
                return None
            radicand *= base
            continue
        return None
    n = int(radicand)
    coeff = 1
    d = 2
    while d * d <= n:
        while n % (d * d) == 0:
            n //= d * d
            coeff *= d
        d += 1
    return factor * coeff, n


def _radical_linear_certificate(expr) -> str | None:
    """``Σ q_k √(m_k)`` 型の式を、√ の Q 上一次独立性で判定する。

    相異なる平方因子のない正整数 m に対し ``{√m}`` は Q 上一次独立
    (よく知られた定理) なので、係数がすべて 0 のときだけ全体が 0 になる。
    虚数単位 ``I`` は実部・虚部が独立なので基底のラベルに含める。
    """
    if cost(expr) > COST_MEDIUM:
        return None
    expr = _safe(lambda e: sp.expand(sp.sqrtdenest(sp.expand(e))), expr)
    if expr is None:
        return None
    terms = expr.args if expr.is_Add else (expr,)
    if len(terms) > 64:
        return None
    table: dict[tuple[bool, int], sp.Expr] = {}
    for term in terms:
        imaginary = False
        parts = term.args if term.is_Mul else (term,)
        cleaned = []
        for part in parts:
            if part is S.ImaginaryUnit:
                imaginary = True
                continue
            cleaned.append(part)
        base = sp.Mul(*cleaned) if cleaned else S.One
        info = _squarefree_radical(base)
        if info is None:
            return None
        coeff, radicand = info
        if not coeff.is_Rational:
            return None
        key = (imaginary, radicand)
        table[key] = table.get(key, S.Zero) + coeff
    for value in table.values():
        if not value.is_Rational:
            return None
        if value != 0:
            return NONZERO
    return ZERO


def _rational_function_certificate(expr) -> str | None:
    """有理関数として恒等的に 0 でないことを示す。

    自由変数を生成元とする多項式に直したとき、係数のうち 1 つでも
    「0 でないと証明できる定数」があれば、その多項式は零多項式ではない。
    体は無限なので、恒等的に 0 の関数にはなり得ない。完全に記号的な議論。
    """
    symbols = sorted(expr.free_symbols, key=lambda s: s.name)
    if not symbols:
        return None
    reduced = _safe(lambda e: sp.cancel(sp.together(e)), expr)
    if reduced is None:
        return None
    if _is_structural_zero(reduced):
        return ZERO
    numer, _denom = sp.fraction(reduced)
    try:
        poly = sp.Poly(numer, *symbols)
    except Exception:
        return None
    try:
        coeffs = list(poly.coeffs())
    except Exception:
        return None
    if not coeffs:
        return None
    if any(has_float(c) for c in coeffs):
        return None
    for coeff in coeffs:
        if coeff.is_Rational:
            if coeff != 0:
                return NONZERO
            continue
        if _certify_nonzero_number(coeff):
            return NONZERO
    return None


def _certify_nonzero_number(expr, depth: int = 0) -> bool:
    """定数式が 0 でないことを記号的に証明する。"""
    if depth > 6:
        return False
    if not isinstance(expr, sp.Basic) or has_float(expr):
        return False
    if expr.free_symbols:
        return False
    if expr.is_Rational:
        return expr != 0
    if expr.is_irrational is True:
        return True  # 0 は有理数なので、無理数は 0 ではない
    if expr.is_rational is False and expr.is_number:
        return True
    if expr.is_real is False and expr.is_number:
        return True  # 0 は実数なので、実数でなければ 0 ではない
    if expr.is_Mul:
        return all(_certify_nonzero_number(a, depth + 1) for a in expr.args)
    if (
        isinstance(expr, sp.Pow)
        and _certify_nonzero_number(expr.base, depth + 1)
        and expr.exp.is_finite is not False
    ):
        return True
    return _radical_linear_certificate(expr) == NONZERO


# --------------------------------------------------------------------------
# 公開 API
# --------------------------------------------------------------------------


def decide_zero(expr) -> str:
    """``expr`` が恒等的に 0 かどうかを厳密に判定する。

    Returns:
        ``ZERO`` / ``NONZERO`` / ``UNKNOWN``

    段階:
        A. 軽い変形で 0 を示す
        B. 軽い証明で 0 でないことを示す (有理関数の係数・定数の性質)
        C. 重い変形で 0 を示す
        D. 厳密値を代入して 0 でないことを示す
    """
    if expr is None:
        return UNKNOWN
    if not isinstance(expr, sp.Basic):
        return UNKNOWN
    if has_float(expr):
        return UNKNOWN
    if expr.has(sp.zoo, sp.nan):
        return UNKNOWN

    weight = cost(expr)

    # --- A. 軽い変形で 0 を示す ---
    if _is_structural_zero(expr):
        return ZERO
    if expr.is_zero is True:
        return ZERO
    cheap = _safe(lambda e: sp.cancel(sp.together(e)), expr)
    if cheap is not None and _is_structural_zero(cheap):
        return ZERO

    free = expr.free_symbols

    # --- B. 軽い「0 でない」証明 ---
    if free:
        cert = _rational_function_certificate(expr)
        if cert == NONZERO:
            return NONZERO
        if cert == ZERO:
            return ZERO
    else:
        if _certify_nonzero_number(expr):
            return NONZERO

    # --- C. 重い変形で 0 を示す ---
    if _try_prove_zero(expr, budget=weight):
        return ZERO

    # --- D. 定数なら性質から、変数があれば代入して 0 でないことを示す ---
    if not free:
        for candidate in (
            _safe(sp.simplify, expr) if weight <= COST_HEAVY else None,
            _safe(sp.radsimp, expr) if weight <= COST_MEDIUM else None,
        ):
            if candidate is not None and _certify_nonzero_number(candidate):
                return NONZERO
        if _radical_linear_certificate(expr) == NONZERO:
            return NONZERO
        return UNKNOWN

    if _sample_nonzero(expr, sorted(free, key=lambda s: s.name)):
        return NONZERO
    return UNKNOWN


def _sample_nonzero(expr, symbols: list) -> bool:
    n = len(SAMPLE_VALUES)
    tried = 0
    for i in range(n):
        if tried >= MAX_SAMPLES:
            break
        subs = {}
        for j, sym in enumerate(symbols):
            subs[sym] = SAMPLE_VALUES[(i + 3 * j) % n]
        tried += 1
        try:
            value = expr.subs(subs, simultaneous=True)
        except Exception:
            continue
        if value is None or has_float(value):
            continue
        if value.has(sp.zoo, sp.nan, sp.oo, -sp.oo):
            continue
        if value.free_symbols:
            continue
        if cost(value) > COST_SAMPLE_LIMIT:
            continue
        if _certify_nonzero_number(value):
            return True
        reduced = _safe(sp.simplify, value) if cost(value) <= COST_HEAVY else None
        if reduced is None:
            continue
        if has_float(reduced) or reduced.has(sp.zoo, sp.nan, sp.oo, -sp.oo):
            continue
        if _certify_nonzero_number(reduced):
            return True
    return False


def prove_equal(a, b) -> str:
    """``a`` と ``b`` が厳密に等しいかを判定する。"""
    if a is None or b is None:
        return UNKNOWN
    try:
        diff = sp.Add(a, sp.Mul(S(-1), b))
    except Exception:
        return UNKNOWN
    return decide_zero(diff)
