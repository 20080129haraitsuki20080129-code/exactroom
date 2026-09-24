from __future__ import annotations

import app.mathjudge.equivalence as equivalence
from app.mathjudge.equivalence import AC, UNDECIDED, WA, judge
from app.mathjudge.numerical import _sample_points
from app.mathjudge.parser import ParseOptions, parse_latex_answer


def test_unknown_constant_can_finish_with_numeric_fingerprint(monkeypatch):
    monkeypatch.setattr(
        equivalence, "compare_parsed", lambda *_args, **_kwargs: (UNDECIDED, "forced_unknown")
    )
    result = judge(
        r"\arctan 2 + \arctan \frac{1}{2}",
        r"\frac{\pi}{2}",
        {"sampling_seed": b"a" * 32},
    )
    assert result.status == "judged"
    assert result.verdict == AC
    assert result.meta["judge_method"] == "numeric_10000d"
    assert result.meta["sample_count"] == 1


def test_unknown_constant_difference_is_numeric_wa(monkeypatch):
    monkeypatch.setattr(
        equivalence, "compare_parsed", lambda *_args, **_kwargs: (UNDECIDED, "forced_unknown")
    )
    result = judge(
        r"\arctan 2 + \arctan \frac{1}{3}",
        r"\frac{\pi}{2}",
        {"sampling_seed": b"b" * 32},
    )
    assert result.status == "judged"
    assert result.verdict == WA
    assert result.reason == "numeric_fingerprint"


def test_constant_beyond_precision_threshold_is_accepted():
    result = judge(r"1", r"1+10^{-12000}", {"sampling_seed": b"c" * 32})
    assert result.status == "judged"
    assert result.verdict == AC
    assert result.reason == "numeric_fingerprint"


def test_constant_difference_inside_first_10000_digits_is_rejected():
    result = judge(r"1", r"1+10^{-9000}", {"sampling_seed": b"d" * 32})
    assert result.status == "judged"
    assert result.verdict == WA


def test_variable_fingerprint_uses_independent_multi_points(monkeypatch):
    monkeypatch.setattr(
        equivalence, "compare_parsed", lambda *_args, **_kwargs: (UNDECIDED, "forced_unknown")
    )
    result = judge(r"x+y", r"2x", {"sampling_seed": b"e" * 32})
    assert result.status == "judged"
    assert result.verdict == WA
    assert result.reason == "numeric_counterexample"
    assert result.meta["sample_count"] >= 1


def test_variable_fingerprint_accepts_all_matching_points(monkeypatch):
    monkeypatch.setattr(
        equivalence, "compare_parsed", lambda *_args, **_kwargs: (UNDECIDED, "forced_unknown")
    )
    result = judge(r"x+x", r"2x", {"sampling_seed": b"f" * 32})
    assert result.status == "judged"
    assert result.verdict == AC
    assert result.meta["sample_count"] == 64


def test_seeded_points_repeat_and_change_with_seed():
    parsed = parse_latex_answer("x+y", ParseOptions())
    symbols = parsed.single.free_symbols

    def points(seed):
        return [
            tuple(point[symbol] for symbol in sorted(symbols, key=lambda item: item.name))
            for point in _sample_points(symbols, seed)
        ][:64]

    assert points(b"g" * 32) == points(b"g" * 32)
    assert points(b"g" * 32) != points(b"h" * 32)


def test_domain_mismatch_is_detected_by_validity_sampling(monkeypatch):
    monkeypatch.setattr(
        equivalence, "compare_parsed", lambda *_args, **_kwargs: (UNDECIDED, "forced_unknown")
    )
    result = judge(r"x", r"\sqrt{x}^2", {"sampling_seed": b"i" * 32})
    assert result.status == "judged"
    assert result.verdict == WA
    assert result.reason == "domain_counterexample"


def test_relation_fingerprint_compares_truth_values(monkeypatch):
    monkeypatch.setattr(
        equivalence, "compare_parsed", lambda *_args, **_kwargs: (UNDECIDED, "forced_unknown")
    )
    result = judge(r"x^2\ge0", r"(x-1)^2\ge0", {"sampling_seed": b"j" * 32})
    assert result.status == "judged"
    assert result.verdict == AC
    assert result.meta["sample_count"] == 64


def test_relation_fingerprint_finds_truth_value_counterexample(monkeypatch):
    monkeypatch.setattr(
        equivalence, "compare_parsed", lambda *_args, **_kwargs: (UNDECIDED, "forced_unknown")
    )
    result = judge(r"x>0", r"x\ge0", {"sampling_seed": b"k" * 32})
    assert result.status == "judged"
    assert result.verdict == WA
    assert result.reason == "numeric_counterexample"
