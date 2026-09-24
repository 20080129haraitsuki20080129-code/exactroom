"""厳密同値判定のテスト。

* AC      … 厳密に同値と証明できた
* WA      … 厳密に非同値と証明できた
* PENDING … どちらも証明できない (判定保留)
"""

from __future__ import annotations

import pytest
import sympy as sp

from app.mathjudge.equivalence import (
    AC,
    EQUIVALENT,
    NOT_EQUIVALENT,
    PENDING,
    UNDECIDED,
    WA,
    compare_parsed,
    judge,
)
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
    # 高校数学でよくある形
    (r"2\sqrt{3}", r"\sqrt{12}"),
    (r"\frac{3}{\sqrt{3}}", r"\sqrt{3}"),
    (r"\frac{1}{\sqrt{2}-1}", r"\sqrt{2}+1"),
    (r"x^3-1", r"(x-1)(x^2+x+1)"),
    (r"\frac{1}{x}+\frac{1}{y}", r"\frac{x+y}{xy}"),
    (r"\cos 2x", r"1-2\sin^2 x"),
    (r"\cos 2x", r"2\cos^2 x-1"),
    (r"\sin\frac{\pi}{6}", r"\frac{1}{2}"),
    (r"\log_{2}\frac{1}{8}", r"-3"),
    (r"\log_{2}8", r"\log_{3}27"),
    (r"\frac{\ln 8}{\ln 2}", r"3"),
    (r"\ln 2 + \ln 3", r"\ln 6"),
    (r"\frac{\pi}{\sqrt{2}}", r"\frac{\pi\sqrt{2}}{2}"),
    (r"\frac{n(n+1)}{2}", r"\frac{1}{2}n^2+\frac{1}{2}n"),
    (r"e^{2x}", r"(e^x)^2"),
    (r"x=-1,x=3", r"x=3,x=-1"),
]


@pytest.mark.parametrize("model,submitted", AC_CASES)
def test_ac(model, submitted):
    result = judge(model, submitted)
    assert result.status == "judged"
    assert result.verdict == AC, (model, submitted)


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
    # 対数・無理数の反証
    (r"\log_{2}8", r"\log_{3}8"),
    (r"\frac{\ln 8}{\ln 2}", r"4"),
    (r"\ln 2 + \ln 3", r"\ln 5"),
    (r"\frac{1}{\sqrt{2}-1}", r"\sqrt{2}-1"),
    (r"\frac{\pi}{3}", r"60"),
    (r"\cos 2x", r"1-2\cos^2 x"),
    (r"0\le x\le 2", r"0<x\le 2"),
    (r"\frac{1}{3}", r"0.3333333333"),
    (r"n(n+1)", r"n(n-1)"),
]


@pytest.mark.parametrize("model,submitted", WA_CASES)
def test_wa(model, submitted):
    result = judge(model, submitted)
    assert result.status == "judged"
    assert result.verdict == WA, (model, submitted)


def test_equivalence_state_is_separate_from_judge_status(monkeypatch):
    import app.mathjudge.equivalence as equivalence
    from app.mathjudge.parser import ParseOptions, parse_latex_answer

    options = ParseOptions()
    equal = parse_latex_answer("x^2", options)
    same = parse_latex_answer("x*x", options)
    different = parse_latex_answer("x+1", options)
    assert compare_parsed(equal, same)[0] == EQUIVALENT
    assert compare_parsed(equal, different)[0] == NOT_EQUIVALENT
    assert judge("x^2", "x*x").status == "judged"
    monkeypatch.setattr(equivalence, "_compare_items", lambda *_args: PENDING)
    assert compare_parsed(equal, same)[0] == UNDECIDED


# --------------------------------------------------------------------------
# 判定保留 (証明も反証もできない)
# --------------------------------------------------------------------------


def test_pending_on_unparsable_submission():
    result = judge(r"x^2", r"\text{よくわかりません}")
    assert result.status == "input_error"
    assert result.verdict is None
    assert result.reason == "submission_parse_error"


def test_pending_on_broken_model_answer():
    result = judge(r"\text{壊れた模範解答}", r"1")
    assert result.status == "problem_error"
    assert result.verdict is None
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


# --------------------------------------------------------------------------
# 回帰テスト: 偽 WA (同値なのに WA) を出さないこと
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,submitted",
    [
        # どちらも実数全体で恒真なので同値。反例点が存在しないので WA にできない。
        (r"x^2 \ge 0", r"(x-1)^2 \ge 0"),
        (r"x^2+1 > 0", r"x^2+2 > 0"),
        (r"x^2 \ge 0", r"x^2+1 > 0"),
        (r"x^2 \ge 0", r"x^4 \ge 0"),
    ],
)
def test_never_wa_for_equivalent_inequalities(model, submitted):
    """不等式では「d=0 になる点が違う」は非同値の証明にならない。

    以前は零点の違いだけで WA にしていたため、恒真な不等式どうしが
    WA になっていた (要件: WA は厳密に非同値と証明できたときだけ)。
    """
    assert judge(model, submitted).verdict != WA


@pytest.mark.parametrize(
    "model,submitted",
    [
        (r"x > 2", r"x \ge 2"),
        (r"x = 1", r"x \ne 1"),
        (r"x = 1", r"x \ge 1"),
        (r"x \ge 1", r"x \ge 2"),
        (r"x \le 3", r"x \ge 3"),
    ],
)
def test_genuine_relation_differences_still_wa(model, submitted):
    """本物の非同値は、反例点を見つけて WA のままであること。"""
    assert judge(model, submitted).verdict == WA


@pytest.mark.parametrize(
    "model,submitted",
    [
        (r"x \ge 1", r"2x \ge 2"),
        (r"2x+1 > 5", r"x > 2"),
        (r"x = 1", r"2x = 2"),
    ],
)
def test_relation_equivalence_preserved(model, submitted):
    assert judge(model, submitted).verdict == AC


def test_substitution_respects_symbol_assumptions():
    """仮定を破る値を代入しないこと (偽 WA の原因になる)。"""
    from app.mathjudge.exactzero import admissible_values, substitution_candidates

    positive = sp.Symbol("p", positive=True)
    for value in admissible_values(positive):
        assert value.is_positive is True

    plain = sp.Symbol("q", real=True)
    assert len(admissible_values(plain)) > len(admissible_values(positive))

    for subs in substitution_candidates([positive], limit=8):
        assert subs[positive].is_positive is True


def test_assume_positive_cases_are_not_wa():
    """x>0 の仮定の下で等しい式が WA にならないこと。"""
    for model, submitted in [
        (r"\sqrt{x^2}", r"x"),
        (r"\ln(x^2)", r"2\ln x"),
        (r"\sqrt{x}\sqrt{x}", r"x"),
    ]:
        assert judge(model, submitted, {"assume_positive": True}).verdict != WA


# --------------------------------------------------------------------------
# 奇数乗根 (高校では実数の根が期待される)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,submitted",
    [
        (r"\sqrt[3]{-8}", r"-2"),
        (r"\sqrt[3]{-27}", r"-3"),
        (r"\sqrt[5]{-32}", r"-2"),
        (r"\sqrt[3]{x^3}", r"x"),
        (r"\sqrt[5]{x^5}", r"x"),
        (r"\sqrt[3]{8x^3}", r"2x"),
        (r"\sqrt[3]{27}", r"3"),
    ],
)
def test_odd_roots_use_the_real_root(model, submitted):
    r"""奇数乗根は実数の根として扱うこと。

    SymPy の既定は主値 (複素数) なので (-8)^(1/3) = 1+√3i となり、
    \sqrt[3]{-8} に -2 と答えた生徒が WA になっていた。
    \sqrt[3]{x^3} と x も WA (偽 WA) になっていた。
    """
    assert judge(model, submitted).verdict == AC


@pytest.mark.parametrize(
    "model,submitted",
    [
        (r"\sqrt[3]{-8}", r"2"),
        (r"\sqrt[3]{x^3}", r"-x"),
        (r"\sqrt[3]{x^3}", r"x+1"),
    ],
)
def test_odd_roots_still_catch_wrong_answers(model, submitted):
    assert judge(model, submitted).verdict == WA


def test_even_roots_keep_the_principal_value():
    """偶数乗根は主値のまま (√(-4) = 2i)。"""
    assert judge(r"\sqrt{-4}", r"2i").verdict == AC
    assert judge(r"\sqrt[4]{16}", r"2").verdict == AC


def test_piecewise_zero_is_detected():
    """場合分けに落ちる式でも、全ての場合で 0 なら 0 と判定できること。"""
    import sympy as sp

    from app.mathjudge.exactzero import ZERO, decide_zero

    x = sp.Symbol("x", real=True)
    assert decide_zero(sp.real_root(x**3, 3) - x) == ZERO
