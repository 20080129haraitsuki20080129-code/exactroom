"""数式処理層で使う例外。

ここで投げる例外メッセージは「提出者自身の入力」についてのみ言及してよい。
模範解答に由来する情報は絶対にメッセージへ含めないこと
(呼び出し側 app/routers/solve.py で最終的なマスキングも行う)。
"""

from __future__ import annotations


class MathError(Exception):
    """数式処理の基底例外。"""

    code = "math_error"

    def __init__(self, message: str, position: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.position = position

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "position": self.position}


class LatexSyntaxError(MathError):
    """TeX として構文が壊れている。"""

    code = "syntax_error"


class UnsupportedLatexError(MathError):
    """構文としては読めるが、このシステムが意図的に対応していない記法。"""

    code = "unsupported"


class InputTooLargeError(MathError):
    """入力が長すぎる / 式が大きすぎる (DoS 対策)。"""

    code = "too_large"


class JudgeTimeoutError(MathError):
    """判定が制限時間内に終わらなかった。"""

    code = "timeout"
