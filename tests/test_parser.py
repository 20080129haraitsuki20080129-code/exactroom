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
        r"1 \pm 2",
        r"x @ y",
        r"\\",
        r"\frac{1}",
        r"x +",
        r"",
        r"   ",
        r"\vec{a}",
        r"\int x dx",
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
