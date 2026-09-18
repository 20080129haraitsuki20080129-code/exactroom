"""TeX 字句解析。

設計方針
--------
* ``eval`` / ``exec`` / ``sympify(文字列)`` を一切使わない。
* 認識できる文字・コマンドはすべてホワイトリスト。未知のものは例外。
* 入力長・トークン数に上限を設け、長大入力による DoS を防ぐ。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import InputTooLargeError, LatexSyntaxError

MAX_INPUT_CHARS = 2000
MAX_TOKENS = 1500

#: 単独の記号として扱う ASCII 文字
PUNCT_CHARS = "+-*/^_(){}[]|,!<>=."

_TOKEN_RE = re.compile(
    r"""
      (?P<space>\s+)
    | (?P<command>\\[A-Za-z]+|\\[^A-Za-z])
    | (?P<number>[0-9]+(?:\.[0-9]+)?|\.[0-9]+)
    | (?P<letter>[A-Za-z])
    | (?P<punct>[+\-*/^_(){}\[\]|,!<>=])
    """,
    re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class Token:
    kind: str  # "command" | "number" | "letter" | "punct" | "eof"
    value: str
    pos: int

    def __repr__(self) -> str:  # pragma: no cover - デバッグ用
        return f"Token({self.kind}, {self.value!r}, @{self.pos})"


#: 完全に無視してよい「空白・装飾」コマンド
IGNORABLE_COMMANDS = frozenset(
    {
        r"\,",
        r"\;",
        r"\:",
        r"\!",
        r"\ ",
        r"\quad",
        r"\qquad",
        r"\thinspace",
        r"\medspace",
        r"\thickspace",
        r"\displaystyle",
        r"\textstyle",
        r"\scriptstyle",
        r"\limits",
        r"\nolimits",
        r"\phantom",  # 引数付きだが parser 側で読み捨てる
    }
)


def tokenize(source: str) -> list[Token]:
    """TeX 文字列をトークン列に変換する。

    Raises:
        InputTooLargeError: 入力が長すぎる場合。
        LatexSyntaxError: 未知の文字が含まれる場合。
    """
    if not isinstance(source, str):
        raise LatexSyntaxError("入力が文字列ではありません。")
    if len(source) > MAX_INPUT_CHARS:
        raise InputTooLargeError(
            f"入力が長すぎます (最大 {MAX_INPUT_CHARS} 文字)。"
        )
    # 制御文字 (改行・タブ以外) を拒否
    for ch in source:
        if ord(ch) < 0x20 and ch not in "\t\n\r":
            raise LatexSyntaxError("制御文字は使用できません。")

    tokens: list[Token] = []
    i = 0
    n = len(source)
    while i < n:
        m = _TOKEN_RE.match(source, i)
        if m is None:
            raise LatexSyntaxError(
                f"使用できない文字 {source[i]!r} があります。", position=i
            )
        i = m.end()
        kind = m.lastgroup
        assert kind is not None
        value = m.group()
        if kind == "space":
            continue
        if kind == "command" and value in IGNORABLE_COMMANDS:
            continue
        if kind == "command" and value == "\\\\":
            raise LatexSyntaxError(r"改行 (\\) は使用できません。", position=m.start())
        if kind == "punct" and value == ".":
            # 単独のピリオド。\left. / \right. の直後でのみ parser が許可する。
            tokens.append(Token("punct", ".", m.start()))
            continue
        tokens.append(Token(kind, value, m.start()))
        if len(tokens) > MAX_TOKENS:
            raise InputTooLargeError(f"式が複雑すぎます (最大 {MAX_TOKENS} トークン)。")

    tokens.append(Token("eof", "", n))
    return tokens
