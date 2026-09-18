"""TeX の厳密な数学的同値判定エンジン。

``eval`` を使わない安全な TeX パーサ (parser.py)、
数値近似を使わない厳密ゼロ判定 (exactzero.py)、
3 値判定 AC / WA / PENDING (equivalence.py) から成る。
"""

from .equivalence import AC, PENDING, WA, JudgeResult, judge  # noqa: F401
from .errors import (  # noqa: F401
    InputTooLargeError,
    LatexSyntaxError,
    MathError,
    UnsupportedLatexError,
)
from .parser import ParseOptions, parse_latex_answer  # noqa: F401
from .runner import judge_isolated, reset_runner  # noqa: F401

__all__ = [
    "AC",
    "WA",
    "PENDING",
    "JudgeResult",
    "judge",
    "judge_isolated",
    "reset_runner",
    "ParseOptions",
    "parse_latex_answer",
    "MathError",
    "LatexSyntaxError",
    "UnsupportedLatexError",
    "InputTooLargeError",
]
