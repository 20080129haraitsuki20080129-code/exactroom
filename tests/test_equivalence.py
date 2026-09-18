"""厳密同値判定のテスト。

* AC      … 厳密に同値と証明できた
* WA      … 厳密に非同値と証明できた
* PENDING … どちらも証明できない (判定保留)
"""

from __future__ import annotations

import pytest
import sympy as sp

from app.mathjudge.equivalence import AC, PENDING, WA, judge
from app.mathjudge.exactzero import NONZERO, UNKNOWN, ZERO, decide_zero


def verdict(model: str, submitted: str, **options) -> str:
    return judge(model, submitted, options).verdict


# --------------------------------------------------------------------------
# AC になるべきもの
# --------------------------------------------------------------------------

AC_CASES = [
    (r"\frac{1}{2}", r"0.5"),
    (r"\frac{1}{2}", r"\frac{2}{4}"),
    (r"0.25", r"\frac{1}{4}"),
    (r"x^2-1", r"(x-1)(x+1)"),
    (r"(a+b)^2", r"a^2+2ab+b^2"),
    (r"\frac{1}{\sqrt{2}}", r"\frac{\sqrt{2}}{2}"),
    (r"\sqrt{8}", r"2\sqrt{2}"),
    (r"\sqrt{5+2\sqrt{6}}", r"\sqrt{2}+\sqrt{3}"),
    (r"\sin^2 x+\cos^2 x", r"1"),
    (r"\sin 2x", r"2\sin x\cos x"),
    (r"\frac{x^2-1}{x-1}", r"x+1"),
    (r"\log_{2}8", r"3"),
    (r"\ln e^2", r"2"),
    (r"e^{i\pi}", r"-1"),
    (r"\frac{-1+\sqrt{5}}{2}", r"\frac{\sqrt{5}-1}{2}"),
    (r"x=1", r"2x=2"),
    (r"x=1", r"x-1=0"),
    (r"x \ge 2", r"2 \le x"),
    (r"\{1,2\}", r"\{2,1\}"),
    (r"\{1,2\}", r"1,2"),
    (r"1,2", r"2,1"),
    (r"\frac{1}{2}x+\frac{1}{3}", r"\frac{3x+2}{6}"),
    (r"2^{10}", r"1024"),
    (r"|{-3}|", r"3"),
    (r"5!", r"120"),
    (r"\frac{d}{2}", r"0.5d"),
    (r"\tan x", r"\frac{\sin x}{\cos x}"),
]


@pytest.mark.parametrize("model,submitted", AC_CASES)
def test_ac(model, submitted):
    assert verdict(model, submitted) == AC, (model, submitted)


# --------------------------------------------------------------------------
# WA になるべきもの
# --------------------------------------------------------------------------

WA_CASES = [
    (r"\frac{1}{2}", r"0.6"),
    (r"\sqrt{2}", r"1.414"),
    (r"\sqrt{2}", r"1.41421356"),
    (r"2\pi", r"6.28"),
    (r"\frac{1}{3}", r"0.333"),
    (r"x^2-1", r"(x-1)(x+2)"),
    (r"x+1", r"x+2"),
    (r"\sin x", r"\cos x"),
    (r"x=1", r"x=2"),
    (r"x>2", r"x \ge 2"),
    (r"\{1,2\}", r"\{1,3\}"),
    (r"\{1,2\}", r"\{1,2,3\}"),
    (r"1,2", r"1,3"),
    (r"x=1", r"1"),          # 種類が違う (関係式 vs 式)
    (r"\{1,2\}", r"1"),       # 種類が違う (集合 vs 式)
    (r"\frac{\sqrt{5}-1}{2}", r"\frac{\sqrt{5}+1}{2}"),
    (r"\sqrt{2}+\sqrt{3}", r"\sqrt{5}"),
    (r"e", r"2.718"),
]


@pytest.mark.parametrize("model,submitted", WA_CASES)
def test_wa(model, submitted):
    assert verdict(model, submitted) == WA, (model, submitted)


# --------------------------------------------------------------------------
# 判定保留 (証明も反証もできない)
# --------------------------------------------------------------------------


def test_pending_on_unparsable_submission():
    result = judge(r"x^2", r"\text{よくわかりません}")
    assert result.verdict == PENDING
    assert result.reason == "submission_parse_error"


def test_pending_on_broken_model_answer():
    result = judge(r"\text{壊れた模範解答}", r"1")
    assert result.verdict == PENDING
    assert result.reason == "model_parse_error"
    # 提出者に返すメッセージに模範解答の中身を含めない
    assert "壊れた模範解答" not in result.student_message


# --------------------------------------------------------------------------
# 厳密ゼロ判定そのもの
# --------------------------------------------------------------------------


def test_decide_zero_exact():
    x = sp.Symbol("x", real=True)
    assert decide_zero(sp.Integer(0)) == ZERO
    assert decide_zero(sp.sqrt(2) - sp.Rational(3, 2)) == NONZERO
    assert decide_zero(sp.sqrt(2) + sp.sqrt(3) - sp.sqrt(5)) == NONZERO
    assert decide_zero(sp.sqrt(2) + sp.sqrt(3) - sp.sqrt(5 + 2 * sp.sqrt(6))) == ZERO
    assert decide_zero(sp.pi - sp.Rational(22, 7)) == NONZERO
    assert decide_zero(x + 1 - (x + 1)) == ZERO
    assert decide_zero(x + 1 - (x + 2)) == NONZERO


def test_no_float_used_in_decision():
    """判定過程で Float が混入しないこと。"""
    value = sp.Rational("0.1") + sp.Rational("0.2") - sp.Rational("0.3")
    assert not value.atoms(sp.Float)
    assert decide_zero(value) == ZERO


def test_decide_zero_never_guesses():
    """判定できないものは UNKNOWN を返す (誤って NONZERO にしない)。"""
    # 超越数どうしの差は一般に決定できない
    result = decide_zero(sp.pi - sp.E - sp.Symbol("k", real=True) * 0 - (sp.pi - sp.E))
    assert result == ZERO
    hard = sp.cos(sp.Integer(1)) - sp.sin(sp.Integer(1))
    assert decide_zero(hard) in (NONZERO, UNKNOWN)


# --------------------------------------------------------------------------
# パースオプション
# --------------------------------------------------------------------------


def test_ordered_list_option():
    assert judge(r"1,2", r"2,1", {"ordered_list": True}).verdict == WA
    assert judge(r"1,2", r"2,1", {"ordered_list": False}).verdict == AC


def test_euler_e_option():
    assert judge(r"e", r"2.718", {"euler_e": True}).verdict == WA
    # e を変数として扱うと、2.718 とは別物であることが示せる
    assert judge(r"e", r"2.718", {"euler_e": False}).verdict == WA


def test_assume_positive_helps_log():
    model = r"\ln(x^2)"
    submitted = r"2\ln x"
    assert judge(model, submitted, {"assume_positive": True}).verdict == AC
    assert judge(model, submitted, {"assume_positive": False}).verdict == WA


def test_verdicts_are_only_three_values():
    for model, submitted in AC_CASES[:5] + WA_CASES[:5]:
        assert judge(model, submitted).verdict in (AC, WA, PENDING)
