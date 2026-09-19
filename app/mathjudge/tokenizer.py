"""TeX 字句解析。

設計方針
--------
* ``eval`` / ``exec`` / ``sympify(文字列)`` を一切使わない。
* 認識できる文字・コマンドはすべてホワイトリスト。未知のものは例外。
* 入力長・トークン数に上限を設け、長大入力による DoS を防ぐ。
* Unicode の数学記号 (π, √, ≦, ×, x², ½ など) は、字句解析の前に
  対応する TeX 表記へ機械的に置き換える。LuaLaTeX / XeLaTeX で
  そのまま書ける書き方を受け付けるためで、意味づけは一切変えない。
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
    | (?P<punct>[+\-*/^_(){}\[\]|,!<>=.])
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
        # 括弧の大きさ指定 (\bigl( ... \bigr) など)。括弧そのものは後続の
        # トークンなので、サイズ指定だけを捨てれば普通の括弧として読める。
        r"\big",
        r"\Big",
        r"\bigg",
        r"\Bigg",
        r"\bigl",
        r"\Bigl",
        r"\biggl",
        r"\Biggl",
        r"\bigr",
        r"\Bigr",
        r"\biggr",
        r"\Biggr",
        r"\bigm",
        r"\Bigm",
        r"\biggm",
        r"\Biggm",
    }
)


# --------------------------------------------------------------------------
# Unicode → TeX の正規化
# --------------------------------------------------------------------------
#
# ここに載せるのは「TeX の綴りと 1 対 1 に対応し、意味が一意に決まる」文字だけ。
# 迷う余地のある文字 (∴ ≒ ∽ など) は意図的に載せず、明示的なエラーにする。

#: 1 文字 → 置き換え後の TeX 断片
UNICODE_ALIASES: dict[str, str] = {
    # -- ギリシャ文字 (小文字) --
    "α": r"\alpha ", "β": r"\beta ", "γ": r"\gamma ", "δ": r"\delta ",
    "ε": r"\epsilon ", "ϵ": r"\epsilon ", "ζ": r"\zeta ", "η": r"\eta ",
    "θ": r"\theta ", "ϑ": r"\vartheta ", "ι": r"\iota ", "κ": r"\kappa ",
    "λ": r"\lambda ", "μ": r"\mu ", "ν": r"\nu ", "ξ": r"\xi ",
    "π": r"\pi ", "ϖ": r"\varpi ", "ρ": r"\rho ", "ϱ": r"\varrho ",
    "σ": r"\sigma ", "ς": r"\varsigma ", "τ": r"\tau ", "υ": r"\upsilon ",
    "φ": r"\phi ", "ϕ": r"\varphi ", "χ": r"\chi ", "ψ": r"\psi ",
    "ω": r"\omega ",
    # -- ギリシャ文字 (大文字) --
    "Γ": r"\Gamma ", "Δ": r"\Delta ", "Θ": r"\Theta ", "Λ": r"\Lambda ",
    "Ξ": r"\Xi ", "Π": r"\Pi ", "Σ": r"\Sigma ", "Υ": r"\Upsilon ",
    "Φ": r"\Phi ", "Ψ": r"\Psi ", "\u03a9": r"\Omega ", "\u2126": r"\Omega ",
    # -- 演算子 --
    "×": r"\times ", "⋅": r"\cdot ", "∙": r"\cdot ", "·": r"\cdot ",
    "÷": r"\div ", "∕": "/", "⁄": "/",
    "±": r"\pm ", "∓": r"\mp ",
    "−": "-", "－": "-", "‐": "-", "‑": "-", "–": "-", "—": "-", "ー": "-",
    "＋": "+", "＝": "=", "／": "/", "＊": "*", "＾": "^",
    # -- 関係記号 --
    "≠": r"\ne ", "≤": r"\le ", "≦": r"\le ", "≥": r"\ge ", "≧": r"\ge ",
    "＜": "<", "＞": ">",
    # -- 括弧 --
    "（": "(", "）": ")", "［": "[", "］": "]", "｛": r"\{ ", "｝": r"\} ",
    "〈": r"\langle ", "〉": r"\rangle ",
    "⟨": r"\langle ", "⟩": r"\rangle ",
    "⌊": r"\lfloor ", "⌋": r"\rfloor ",
    "⌈": r"\lceil ", "⌉": r"\rceil ",
    "｜": "|", "‖": r"\Vert ",
    "，": ",", "、": ",", "！": "!",
    # -- その他の記号 --
    "√": r"\sqrt ", "∞": r"\infty ", "∅": r"\emptyset ", "∘": r"\circ ",
    "°": r"^\circ ", "∣": r"\mid ",
    # -- 非対応だが「未知の文字」ではなく正しい理由を返したいもの --
    "∑": r"\sum ", "∏": r"\prod ", "∫": r"\int ", "∮": r"\oint ",
    "∪": r"\cup ", "∩": r"\cap ", "∈": r"\in ", "∋": r"\ni ",
    "⊂": r"\subset ", "⊃": r"\supset ", "⊆": r"\subseteq ",
    "≡": r"\equiv ", "≈": r"\approx ", "≒": r"\approx ", "∽": r"\sim ",
    "→": r"\to ", "⇒": r"\Rightarrow ", "⇔": r"\Leftrightarrow ",
    "∀": r"\forall ", "∃": r"\exists ", "∂": r"\partial ", "∇": r"\nabla ",
    "ℝ": r"\mathbb{R}", "ℤ": r"\mathbb{Z}", "ℚ": r"\mathbb{Q}",
    "ℕ": r"\mathbb{N}", "ℂ": r"\mathbb{C}",
    "…": r"\ldots ", "⋯": r"\cdots ", "∴": r"\therefore ",
    "％": r"\% ",
}

#: 全角英数字 → 半角
for _code in range(10):
    UNICODE_ALIASES[chr(0xFF10 + _code)] = str(_code)
for _code in range(26):
    UNICODE_ALIASES[chr(0xFF21 + _code)] = chr(ord("A") + _code)
    UNICODE_ALIASES[chr(0xFF41 + _code)] = chr(ord("a") + _code)
del _code

#: 上付き文字 (x² → x^{2})
SUPERSCRIPT_CHARS: dict[str, str] = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "⁺": "+", "⁻": "-", "ⁿ": "n", "ⁱ": "i",
}

#: 下付き文字 (a₁ → a_{1})
SUBSCRIPT_CHARS: dict[str, str] = {
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    "₊": "+", "₋": "-", "ₙ": "n", "ᵢ": "i", "ₖ": "k", "ₘ": "m",
}

#: 分数記号 (½ → (1/2))
VULGAR_FRACTIONS: dict[str, str] = {
    "½": "(1/2)", "⅓": "(1/3)", "⅔": "(2/3)", "¼": "(1/4)", "¾": "(3/4)",
    "⅕": "(1/5)", "⅖": "(2/5)", "⅗": "(3/5)", "⅘": "(4/5)",
    "⅙": "(1/6)", "⅚": "(5/6)", "⅐": "(1/7)", "⅛": "(1/8)",
    "⅜": "(3/8)", "⅝": "(5/8)", "⅞": "(7/8)", "⅑": "(1/9)", "⅒": "(1/10)",
}


def normalize_unicode(source: str) -> tuple[str, list[int]]:
    """Unicode の数学記号を TeX 表記へ置き換える。

    Returns:
        ``(正規化後の文字列, 位置対応表)``。位置対応表 ``m`` は
        ``m[i]`` = 正規化後の ``i`` 文字目が元の何文字目に由来するか。
        エラー位置を元の入力の位置で報告するために使う。
    """
    out: list[str] = []
    origin: list[int] = []

    def emit(text: str, at: int) -> None:
        out.append(text)
        origin.extend([at] * len(text))

    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch.isascii():
            emit(ch, i)
            i += 1
            continue
        if ch in SUPERSCRIPT_CHARS:
            start = i
            digits: list[str] = []
            while i < n and source[i] in SUPERSCRIPT_CHARS:
                digits.append(SUPERSCRIPT_CHARS[source[i]])
                i += 1
            emit("^{" + "".join(digits) + "}", start)
            continue
        if ch in SUBSCRIPT_CHARS:
            start = i
            digits = []
            while i < n and source[i] in SUBSCRIPT_CHARS:
                digits.append(SUBSCRIPT_CHARS[source[i]])
                i += 1
            emit("_{" + "".join(digits) + "}", start)
            continue
        if ch in VULGAR_FRACTIONS:
            emit(VULGAR_FRACTIONS[ch], i)
            i += 1
            continue
        alias = UNICODE_ALIASES.get(ch)
        if alias is not None:
            emit(alias, i)
            i += 1
            continue
        raise LatexSyntaxError(f"使用できない文字 {ch!r} があります。", position=i)

    return "".join(out), origin


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

    text, origin = normalize_unicode(source)
    # 1 文字が最長 16 文字に膨らむので、正規化後の長さにも上限を置く。
    # (実際の防御は下の MAX_TOKENS。ここは正規表現の作業量を抑えるため。)
    if len(text) > MAX_INPUT_CHARS * 16:
        raise InputTooLargeError(f"入力が長すぎます (最大 {MAX_INPUT_CHARS} 文字)。")

    def original_pos(index: int) -> int:
        if index < len(origin):
            return origin[index]
        return len(source)

    tokens: list[Token] = []
    i = 0
    n = len(text)
    while i < n:
        m = _TOKEN_RE.match(text, i)
        if m is None:
            raise LatexSyntaxError(
                f"使用できない文字 {text[i]!r} があります。",
                position=original_pos(i),
            )
        i = m.end()
        kind = m.lastgroup
        assert kind is not None
        value = m.group()
        pos = original_pos(m.start())
        if kind == "space":
            continue
        if kind == "command" and value in IGNORABLE_COMMANDS:
            continue
        if kind == "command" and value == "\\\\":
            raise LatexSyntaxError(r"改行 (\\) は使用できません。", position=pos)
        tokens.append(Token(kind, value, pos))
        if len(tokens) > MAX_TOKENS:
            raise InputTooLargeError(f"式が複雑すぎます (最大 {MAX_TOKENS} トークン)。")

    tokens.append(Token("eof", "", len(source)))
    return tokens
