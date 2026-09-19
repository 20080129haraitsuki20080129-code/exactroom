"""TeX パーサのテスト。"""

from __future__ import annotations

import pytest
import sympy as sp

from app.mathjudge.errors import (
    InputTooLargeError,
    LatexSyntaxError,
    UnsupportedLatexError,
)
from app.mathjudge.parser import ParseOptions, parse_latex_answer, parse_latex_expr


@pytest.mark.parametrize(
    "source,expected",
    [
        (r"1+1", sp.Integer(2)),
        (r"0.25", sp.Rational(1, 4)),
        (r"0.1", sp.Rational(1, 10)),
        (r"\frac{1}{2}", sp.Rational(1, 2)),
        (r"\sqrt{9}", sp.Integer(3)),
        (r"\sqrt[3]{27}", sp.Integer(3)),
        (r"2^{-1}", sp.Rational(1, 2)),
        (r"\log_{2}8", sp.Integer(3)),
        (r"|-5|", sp.Integer(5)),
        (r"\left|-5\right|", sp.Integer(5)),
        (r"5!", sp.Integer(120)),
        (r"\binom{5}{2}", sp.Integer(10)),
        (r"e^{i\pi}", sp.Integer(-1)),
    ],
)
def test_numeric_expressions(source, expected):
    assert parse_latex_expr(source) == expected


def test_decimal_is_exact_rational():
    """0.1 + 0.2 が厳密に 0.3 になる (浮動小数点誤差が出ない)。"""
    value = parse_latex_expr(r"0.1+0.2")
    assert value == sp.Rational(3, 10)
    assert not value.atoms(sp.Float)


def test_no_float_anywhere():
    for src in (r"0.333", r"\frac{1}{3}", r"\sqrt{2}", r"1.4142135"):
        assert not parse_latex_expr(src).atoms(sp.Float)


def test_implicit_multiplication():
    x, y = sp.Symbol("x", real=True), sp.Symbol("y", real=True)
    assert parse_latex_expr(r"2x") == 2 * x
    assert parse_latex_expr(r"x y") == x * y
    assert parse_latex_expr(r"2\pi") == 2 * sp.pi
    assert parse_latex_expr(r"(x+1)(x-1)") == (x + 1) * (x - 1)


def test_function_argument_rule():
    x = sp.Symbol("x", real=True)
    assert parse_latex_expr(r"\sin 2x") == sp.sin(2 * x)
    assert parse_latex_expr(r"\sin(x)") == sp.sin(x)
    assert parse_latex_expr(r"\sin^2 x") == sp.sin(x) ** 2
    assert parse_latex_expr(r"\sin^{-1}x") == sp.asin(x)
    assert parse_latex_expr(r"\sin x\cos x") == sp.sin(x) * sp.cos(x)


def test_subscript_is_order_independent():
    assert parse_latex_expr(r"a_{n+1}") == parse_latex_expr(r"a_{1+n}")
    assert parse_latex_expr(r"x_1") != parse_latex_expr(r"x_2")


def test_kinds():
    assert parse_latex_answer(r"x^2").kind == "expr"
    assert parse_latex_answer(r"x = 1").kind == "rel"
    assert parse_latex_answer(r"\{1,2\}").kind == "set"
    assert parse_latex_answer(r"1, 2").kind == "list"


def test_options_euler_and_imaginary():
    plain = ParseOptions(euler_e=False, imaginary_i=False)
    assert parse_latex_expr("e", plain) == sp.Symbol("e", real=True)
    assert parse_latex_expr("i", plain) == sp.Symbol("i", real=True)
    assert parse_latex_expr("e") == sp.E
    assert parse_latex_expr("i") == sp.I


def test_log_base_option():
    x = sp.Symbol("x", real=True)
    assert parse_latex_expr(r"\log x", ParseOptions(log_base="e")) == sp.log(x)
    assert parse_latex_expr(r"\log x", ParseOptions(log_base="10")) == sp.log(x, 10)
    assert parse_latex_expr(r"\lg x") == sp.log(x, 10)


@pytest.mark.parametrize(
    "source",
    [
        r"\text{hello}",
        r"\eval{1}",
        r"__import__('os')",
        r"\sum_{i=1}^{n}i",
        r"\begin{pmatrix}1\end{pmatrix}",
        r"x @ y",
        r"\\",
        r"\frac{1}",
        r"x +",
        r"",
        r"   ",
        r"\int x dx",
        r"\cup",
        r"\in",
        r"\mathbb{R}",
        r"\approx",
        r"\equiv",
        r"\ldots",
        r"\partial x",
        r"\vec{a+b}",
        r"\gcd(6)",
        r"\gcd 6, 9",
        "x ≒ y",
        "x ∈ A",
        "1 … 9",
        "あ",
    ],
)
def test_rejects_dangerous_or_unsupported(source):
    with pytest.raises((LatexSyntaxError, UnsupportedLatexError, InputTooLargeError)):
        parse_latex_answer(source)


def test_rejects_long_input():
    with pytest.raises(InputTooLargeError):
        parse_latex_answer("1+" * 2000)


def test_rejects_deep_nesting():
    with pytest.raises((InputTooLargeError, LatexSyntaxError)):
        parse_latex_answer("(" * 100 + "1" + ")" * 100)


def test_control_characters_rejected():
    with pytest.raises(LatexSyntaxError):
        parse_latex_answer("1+\x001")


@pytest.mark.parametrize(
    "source",
    [
        r"a_{(x+y)^{1000}}",
        r"a_{(x+y+z+w)^{200}}",
        r"a_{(x+y+z+w+v)^{1000}}",
    ],
)
def test_subscript_expansion_is_bounded(source):
    """添字の多項式展開でメモリ・CPU を食い潰さないこと。

    以前は展開前に大きさを見ていなかったため、20 文字程度の入力で
    ワーカーが OOM で落ちていた。
    """
    import time

    started = time.monotonic()
    with pytest.raises(InputTooLargeError):
        parse_latex_answer(source)
    assert time.monotonic() - started < 3.0


@pytest.mark.parametrize("source", [r"a_{n+1}", r"x_{12}", r"a_{2n}", r"x_1"])
def test_normal_subscripts_still_work(source):
    assert parse_latex_answer(source).kind == "expr"


@pytest.mark.parametrize(
    "source",
    [
        r"\{1,2\}+1",
        r"\{1,2\} \cdot 3",
        r"\emptyset + 1",
        r"2\{1,2\}",
        r"\sqrt{\{1,2\}}",
        r"\left\{1,2\right\}^2",
        r"\{1,2\}=\{1,2\}",
    ],
)
def test_sets_cannot_be_used_inside_expressions(source):
    r"""集合を式の一部に書けないこと。

    以前は ``\{1,2\}+1`` が Add(FiniteSet, 1) という無意味な式になっていた。
    SymPy はこの使い方を非推奨としており、将来は実行時エラーになる。
    """
    with pytest.raises((UnsupportedLatexError, LatexSyntaxError)):
        parse_latex_answer(source)


@pytest.mark.parametrize(
    "source,kind",
    [
        (r"\{1,2\}", "set"),
        (r"\{1,2,3\}", "set"),
        (r"\left\{1,2\right\}", "set"),
        (r"\emptyset", "set"),
        (r"\varnothing", "set"),
        (r"\{1,2\},\{3\}", "list"),
    ],
)
def test_sets_are_fine_on_their_own(source, kind):
    assert parse_latex_answer(source).kind == kind


def test_parsing_never_produces_non_expr_arithmetic():
    """算術の引数に Expr 以外 (集合など) が入らないこと。"""
    import warnings

    import sympy as sp

    sources = [r"\{1,2\}", r"1,2", r"x+1", r"\emptyset", r"x=1", r"0 \le x \le 1"]
    with warnings.catch_warnings():
        warnings.simplefilter("error", sp.utilities.exceptions.SymPyDeprecationWarning)
        for source in sources:
            answer = parse_latex_answer(source)
            for item in answer.items:
                assert item is not None


@pytest.mark.parametrize(
    "first,second,same",
    [
        (r"a_{n+1}", r"a_{1+n}", True),    # 演算子があるので式として正規化する
        (r"a_{2n}", r"a_{n2}", False),     # 並びが違えば別のラベル
        (r"x_{ab}", r"x_{ba}", False),
        (r"x_{12}", r"x_{21}", False),
        (r"x_{i}", r"x_i", True),
        (r"a_{2n}", r"a_{2n}", True),
    ],
)
def test_subscripts_are_labels_unless_they_contain_operators(first, second, same):
    """添字の同一視。

    式として読むと x_{ab} と x_{ba} が a*b で同一視されてしまうため、
    演算子を含まない添字は書かれた順のラベルとして扱う。
    """
    assert (parse_latex_expr(first) == parse_latex_expr(second)) is same


# --------------------------------------------------------------------------
# Unicode の数学記号 (LuaLaTeX / XeLaTeX でそのまま書ける書き方)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unicode_source,tex_source",
    [
        ("π", r"\pi"),
        ("2π", r"2\pi"),
        ("α+β", r"\alpha+\beta"),
        ("θ", r"\theta"),
        ("Ω", r"\Omega"),
        ("√2", r"\sqrt{2}"),
        ("√(x+1)", r"\sqrt{x+1}"),
        ("2×3", r"2\times 3"),
        ("6÷2", r"6\div 2"),
        ("x²", r"x^{2}"),
        ("x²³", r"x^{23}"),
        ("x⁻¹", r"x^{-1}"),
        ("a₁", r"a_{1}"),
        ("½", r"\frac{1}{2}"),
        ("∞", r"\infty"),
        ("−5", r"-5"),
        ("（x+1）", r"(x+1)"),
        ("１２３", r"123"),
        ("⌊x⌋", r"\lfloor x\rfloor"),
        ("⌈x⌉", r"\lceil x\rceil"),
        ("90°", r"\frac{\pi}{2}"),
    ],
)
def test_unicode_is_equivalent_to_tex(unicode_source, tex_source):
    assert parse_latex_expr(unicode_source) == parse_latex_expr(tex_source)


@pytest.mark.parametrize(
    "unicode_source,tex_source",
    [
        ("x≦1", r"x\le 1"),
        ("x≧1", r"x\ge 1"),
        ("x≠1", r"x\ne 1"),
        ("x≤1", r"x\le 1"),
    ],
)
def test_unicode_relations(unicode_source, tex_source):
    a = parse_latex_answer(unicode_source)
    b = parse_latex_answer(tex_source)
    assert a.kind == b.kind == "rel"
    assert a.single == b.single


def test_unicode_error_position_points_at_the_original_string():
    """正規化で文字列が伸びても、エラー位置は元の入力の位置で返す。"""
    with pytest.raises(UnsupportedLatexError) as info:
        parse_latex_answer("π+∫")
    assert info.value.position == 2


# --------------------------------------------------------------------------
# 複号 (\pm, \mp)
# --------------------------------------------------------------------------


def test_plus_minus_expands_to_two_answers():
    answer = parse_latex_answer(r"\pm 2")
    assert answer.kind == "list"
    assert set(answer.items) == {sp.Integer(2), sp.Integer(-2)}


def test_plus_minus_in_a_relation():
    answer = parse_latex_answer(r"x = \pm 2")
    assert answer.kind == "list"
    x = sp.Symbol("x", real=True)
    assert set(answer.items) == {sp.Eq(x, 2, evaluate=False),
                                 sp.Eq(x, -2, evaluate=False)}


def test_plus_minus_follows_the_same_order():
    r"""複号同順: ``a \pm b`` と ``c \mp d`` は逆の符号で対応する。"""
    answer = parse_latex_answer(r"1 \pm 2 \mp 4")
    assert answer.kind == "list"
    assert set(answer.items) == {sp.Integer(1 + 2 - 4), sp.Integer(1 - 2 + 4)}


def test_plus_minus_collapses_when_both_branches_agree():
    answer = parse_latex_answer(r"x \pm 0")
    assert answer.kind == "expr"
    assert answer.single == sp.Symbol("x", real=True)


def test_plus_minus_inside_a_set():
    answer = parse_latex_answer(r"\{\pm 1\}")
    assert answer.kind == "set"
    assert answer.single == sp.FiniteSet(1, -1)


def test_unicode_plus_minus():
    assert parse_latex_answer("±2").items == parse_latex_answer(r"\pm 2").items


# --------------------------------------------------------------------------
# 追加した TeX コマンド
# --------------------------------------------------------------------------

_x = sp.Symbol("x", real=True)


@pytest.mark.parametrize(
    "source,expected",
    [
        (r"\vert x\vert", sp.Abs(_x)),
        (r"\lvert x\rvert", sp.Abs(_x)),
        (r"\lVert x\rVert", sp.Abs(_x)),
        (r"\Vert x\Vert", sp.Abs(_x)),
        (r"\left\lVert x\right\rVert", sp.Abs(_x)),
        (r"\langle x\rangle", _x),
        (r"\lfloor 2.7\rfloor", sp.Integer(2)),
        (r"\lceil 2.1\rceil", sp.Integer(3)),
        (r"\left\lfloor 2.7\right\rfloor", sp.Integer(2)),
        (r"\left.x\right)", _x),
        (r"\bigl(x+1\bigr)", _x + 1),
        (r"\Bigl(x+1\Bigr)", _x + 1),
        (r"\bar{z}", sp.conjugate(_x).subs(_x, sp.Symbol("z", real=True))),
        (r"\tbinom{5}{2}", sp.Integer(10)),
        (r"\gcd(12,18)", sp.Integer(6)),
        (r"\operatorname{lcm}(4,6)", sp.Integer(12)),
        (r"\max(2,5)", sp.Integer(5)),
        (r"\min(2,5)", sp.Integer(2)),
        (r"\operatorname{floor}(2.7)", sp.Integer(2)),
        (r"\cot^{-1}1", sp.acot(1)),
        (r"\arccot 1", sp.acot(1)),
        (r"90^\circ", sp.pi / 2),
        (r"\sin 30^{\circ}", sp.Rational(1, 2)),
        (r"\mathsf{x}", _x),
        (r"\boldsymbol{x}", _x),
    ],
)
def test_new_commands(source, expected):
    assert parse_latex_expr(source) == expected


def test_decorated_symbols_differ_from_plain_ones():
    r"""``\vec{a}`` は ``a`` とは別の記号として扱う。"""
    assert parse_latex_expr(r"\vec{a}") != parse_latex_expr("a")
    assert parse_latex_expr(r"\vec{a}") == parse_latex_expr(r"\vec a")
    assert parse_latex_expr(r"\vec{a}") != parse_latex_expr(r"\hat{a}")


@pytest.mark.parametrize(
    "source,fragment",
    [
        (r"\sum_{k=1}^{n}k", "総和"),
        (r"\int x", "積分"),
        (r"\lim x", "極限"),
        (r"A\cup B", "集合の演算"),
        (r"\mathbb{R}", "数の集合"),
        (r"x\approx 1", "近似"),
        (r"\ldots", "…"),
    ],
)
def test_unsupported_commands_explain_why(source, fragment):
    with pytest.raises(UnsupportedLatexError) as info:
        parse_latex_answer(source)
    assert fragment in info.value.message


@pytest.mark.parametrize(
    "source,expected",
    [
        (r"\frac12", sp.Rational(1, 2)),
        (r"\frac1{2}", sp.Rational(1, 2)),
        (r"\frac{1}2", sp.Rational(1, 2)),
        (r"\tfrac34", sp.Rational(3, 4)),
        (r"\frac123", sp.Rational(3, 2)),
        (r"\binom52", sp.Integer(10)),
        (r"\log_23", sp.log(3) / sp.log(2)),
        # 括弧を付けたときの意味は変わらない
        (r"\frac{12}{5}", sp.Rational(12, 5)),
        (r"\log_{2}8", sp.Integer(3)),
    ],
)
def test_unbraced_argument_takes_one_character(source, expected):
    r"""本来の TeX と同じく ``\frac12`` は ``\frac{1}{2}``。"""
    assert parse_latex_expr(source) == expected


def test_unbraced_sqrt_keeps_the_whole_number():
    r"""``\sqrt12`` はこれまで通り ``\sqrt{12}``。

    ``\frac12`` はもともとエラーだったので本来の TeX に合わせたが、
    ``\sqrt12`` は従来 ``\sqrt{12}`` として通っていた。黙って意味を
    変えると誤判定になるため、こちらは変更しない。
    """
    assert parse_latex_expr(r"\sqrt12") == sp.sqrt(12)
