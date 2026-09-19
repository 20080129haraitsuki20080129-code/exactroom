"""TeX -> SymPy 変換 (安全な再帰下降パーサ)。

重要な安全性の約束
------------------
1. ``eval`` / ``exec`` / ``compile`` / ``sympify(文字列)`` を一切使わない。
   SymPy のオブジェクトはすべてコンストラクタ呼び出しで直接組み立てる。
2. 使用できるコマンドはすべてこのファイル内のホワイトリストに載っているものだけ。
3. 小数は ``Rational`` として厳密に解釈する (``Float`` は生成しない)。
   生成物に ``Float`` が混入していないことを最後に検証する。
4. 再帰の深さ・式のノード数に上限を設ける。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import sympy as sp

from .errors import (
    InputTooLargeError,
    LatexSyntaxError,
    UnsupportedLatexError,
)
from .tokenizer import Token, tokenize

MAX_DEPTH = 60
MAX_NODES = 20000
#: 整数冪の指数の絶対値の上限 (2^{999999} のような爆発を防ぐ)
MAX_EXPONENT = 1000
#: 階乗・二項係数の引数の上限
MAX_FACTORIAL = 500
#: 生成される整数の桁数の上限
MAX_INT_DIGITS = 2000
#: 添字の式の複雑さの上限 (a_{(x+y+z+w)^{200}} のような展開爆発を防ぐ)
MAX_SUBSCRIPT_OPS = 16
MAX_SUBSCRIPT_EXPONENT = 8

# --------------------------------------------------------------------------
# パースオプション (問題ごとに出題者が設定できる)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParseOptions:
    #: ``e`` を自然対数の底とみなす (False なら普通の変数)
    euler_e: bool = True
    #: ``i`` を虚数単位とみなす (False なら普通の変数)
    imaginary_i: bool = True
    #: 底を省略した ``\log`` の底。"e" または "10"
    log_base: str = "e"
    #: 変数に「実数」の仮定を付ける
    assume_real: bool = True
    #: 変数に「正」の仮定を付ける (assume_real より強い)
    assume_positive: bool = False

    @classmethod
    def from_dict(cls, data: dict | None) -> ParseOptions:
        data = dict(data or {})
        allowed = set(cls.__slots__)
        unknown = set(data) - allowed
        for key in unknown:
            data.pop(key)
        if data.get("log_base") not in (None, "e", "10"):
            data["log_base"] = "e"
        return cls(**data)

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self.__slots__}


# --------------------------------------------------------------------------
# ホワイトリスト
# --------------------------------------------------------------------------

GREEK = {
    r"\alpha": "alpha",
    r"\beta": "beta",
    r"\gamma": "gamma",
    r"\delta": "delta",
    r"\epsilon": "epsilon",
    r"\varepsilon": "varepsilon",
    r"\zeta": "zeta",
    r"\eta": "eta",
    r"\theta": "theta",
    r"\vartheta": "vartheta",
    r"\iota": "iota",
    r"\kappa": "kappa",
    r"\lambda": "lamda",
    r"\mu": "mu",
    r"\nu": "nu",
    r"\xi": "xi",
    r"\varpi": "varpi",
    r"\rho": "rho",
    r"\varrho": "varrho",
    r"\sigma": "sigma",
    r"\varsigma": "varsigma",
    r"\tau": "tau",
    r"\upsilon": "upsilon",
    r"\phi": "phi",
    r"\varphi": "varphi",
    r"\chi": "chi",
    r"\psi": "psi",
    r"\omega": "omega",
    r"\Gamma": "Gamma_",
    r"\Delta": "Delta_",
    r"\Theta": "Theta_",
    r"\Lambda": "Lambda_",
    r"\Xi": "Xi_",
    r"\Pi": "Pi_",
    r"\Sigma": "Sigma_",
    r"\Upsilon": "Upsilon_",
    r"\Phi": "Phi_",
    r"\Psi": "Psi_",
    r"\Omega": "Omega_",
}

#: 引数 1 個の関数 (TeX コマンド -> SymPy 関数)
UNARY_FUNCTIONS: dict[str, Callable] = {
    r"\sin": sp.sin,
    r"\cos": sp.cos,
    r"\tan": sp.tan,
    r"\cot": sp.cot,
    r"\sec": sp.sec,
    r"\csc": sp.csc,
    r"\arcsin": sp.asin,
    r"\arccos": sp.acos,
    r"\arctan": sp.atan,
    r"\arccot": sp.acot,
    r"\arcsec": sp.asec,
    r"\arccsc": sp.acsc,
    r"\sinh": sp.sinh,
    r"\cosh": sp.cosh,
    r"\tanh": sp.tanh,
    r"\coth": sp.coth,
    r"\ln": sp.log,
    r"\exp": sp.exp,
}

#: ``\sin^{-1}`` のような逆関数表記
INVERSE_FUNCTIONS: dict[str, Callable] = {
    r"\sin": sp.asin,
    r"\cos": sp.acos,
    r"\tan": sp.atan,
    r"\cot": sp.acot,
    r"\sec": sp.asec,
    r"\csc": sp.acsc,
    r"\sinh": sp.asinh,
    r"\cosh": sp.acosh,
    r"\tanh": sp.atanh,
    r"\coth": sp.acoth,
}

#: \operatorname{...} / 素の綴りで許可する関数名
NAMED_FUNCTIONS: dict[str, Callable] = {
    "sin": sp.sin,
    "cos": sp.cos,
    "tan": sp.tan,
    "cot": sp.cot,
    "sec": sp.sec,
    "csc": sp.csc,
    "arcsin": sp.asin,
    "arccos": sp.acos,
    "arctan": sp.atan,
    "sinh": sp.sinh,
    "cosh": sp.cosh,
    "tanh": sp.tanh,
    "ln": sp.log,
    "exp": sp.exp,
    "abs": sp.Abs,
    "sgn": sp.sign,
    "sign": sp.sign,
    "arccot": sp.acot,
    "arcsec": sp.asec,
    "arccsc": sp.acsc,
    "coth": sp.coth,
    "sech": sp.sech,
    "csch": sp.csch,
    "floor": sp.floor,
    "ceil": sp.ceiling,
    "ceiling": sp.ceiling,
    "conj": sp.conjugate,
}

#: 括弧とカンマで複数の引数を取る関数 (``\gcd(12,18)`` など)
MULTI_ARG_FUNCTIONS: dict[str, Callable] = {
    "gcd": sp.gcd,
    "lcm": sp.lcm,
    "max": sp.Max,
    "min": sp.Min,
}

#: ``\gcd`` のようにコマンドとしても書ける複数引数関数
MULTI_ARG_COMMANDS: dict[str, str] = {
    r"\gcd": "gcd",
    r"\max": "max",
    r"\min": "min",
}

#: ``\vec{a}`` のような装飾。装飾した記号は素の記号とは別物として扱う
#: (``\vec{a}`` と ``a`` を同一視しないため)。
DECORATORS: dict[str, str] = {
    r"\vec": "vec",
    r"\hat": "hat",
    r"\tilde": "tilde",
    r"\dot": "dot",
    r"\ddot": "ddot",
    r"\check": "check",
    r"\breve": "breve",
    r"\acute": "acute",
    r"\grave": "grave",
}

#: 書体コマンド。中身をそのまま式として読む (``\mathrm{e}`` だけ特別扱い)。
FONT_COMMANDS = frozenset(
    {
        r"\mathrm",
        r"\mathit",
        r"\mathbf",
        r"\mathsf",
        r"\mathtt",
        r"\mathnormal",
        r"\boldsymbol",
        r"\bm",
    }
)

#: 意図的に対応しないコマンドと、その理由 (「未対応のコマンド」より親切に)
UNSUPPORTED_MESSAGES: dict[str, str] = {
    r"\sum": "総和 \\sum には対応していません。",
    r"\prod": "総乗 \\prod には対応していません。",
    r"\int": "積分 \\int には対応していません。",
    r"\oint": "積分 \\oint には対応していません。",
    r"\iint": "積分 \\iint には対応していません。",
    r"\iiint": "積分 \\iiint には対応していません。",
    r"\lim": "極限 \\lim には対応していません。",
    r"\limsup": "極限には対応していません。",
    r"\liminf": "極限には対応していません。",
    r"\cup": "集合の演算 (∪ ∩ ∖) には対応していません。",
    r"\cap": "集合の演算 (∪ ∩ ∖) には対応していません。",
    r"\setminus": "集合の演算 (∪ ∩ ∖) には対応していません。",
    r"\in": "集合の所属 (∈ ∉ ⊂) には対応していません。",
    r"\notin": "集合の所属 (∈ ∉ ⊂) には対応していません。",
    r"\ni": "集合の所属 (∈ ∉ ⊂) には対応していません。",
    r"\subset": "集合の包含 (⊂ ⊃ ⊆) には対応していません。",
    r"\supset": "集合の包含 (⊂ ⊃ ⊆) には対応していません。",
    r"\subseteq": "集合の包含 (⊂ ⊃ ⊆) には対応していません。",
    r"\supseteq": "集合の包含 (⊂ ⊃ ⊆) には対応していません。",
    r"\mathbb": "数の集合 (ℝ ℤ ℚ ℕ ℂ) には対応していません。",
    r"\mathcal": "\\mathcal には対応していません。",
    r"\mathfrak": "\\mathfrak には対応していません。",
    r"\mathscr": "\\mathscr には対応していません。",
    r"\to": "矢印 (→ ⇒ ⇔) には対応していません。",
    r"\rightarrow": "矢印 (→ ⇒ ⇔) には対応していません。",
    r"\Rightarrow": "矢印 (→ ⇒ ⇔) には対応していません。",
    r"\Leftrightarrow": "矢印 (→ ⇒ ⇔) には対応していません。",
    r"\implies": "矢印 (→ ⇒ ⇔) には対応していません。",
    r"\iff": "矢印 (→ ⇒ ⇔) には対応していません。",
    r"\approx": "近似 (≈ ≒) は厳密判定できないため対応していません。",
    r"\sim": "近似 (∽ ∼) は厳密判定できないため対応していません。",
    r"\equiv": "合同 (≡) には対応していません。",
    r"\propto": "比例 (∝) には対応していません。",
    r"\therefore": "「ゆえに」などの記号は式に含められません。",
    r"\because": "「なぜならば」などの記号は式に含められません。",
    r"\ldots": "「…」は何が続くか確定しないため使えません。",
    r"\cdots": "「…」は何が続くか確定しないため使えません。",
    r"\dots": "「…」は何が続くか確定しないため使えません。",
    r"\vdots": "「…」は何が続くか確定しないため使えません。",
    r"\forall": "論理記号 (∀ ∃) には対応していません。",
    r"\exists": "論理記号 (∀ ∃) には対応していません。",
    r"\partial": "微分 (∂ d/dx) には対応していません。",
    r"\nabla": "微分 (∇) には対応していません。",
    r"\mid": "「｜」を区切りとして使う書き方には対応していません。",
    r"\nmid": "「｜」を区切りとして使う書き方には対応していません。",
    r"\%": "パーセント記号は使えません。100 で割った数を書いてください。",
    r"\degree": "角度は 90^\\circ のように書いてください。",
    r"\circ": "合成写像 (∘) には対応していません。角度なら 90^\\circ と書いてください。",
}

RELATION_OPS = {
    "=": sp.Eq,
    r"\ne": sp.Ne,
    r"\neq": sp.Ne,
    "<": sp.Lt,
    ">": sp.Gt,
    r"\lt": sp.Lt,
    r"\gt": sp.Gt,
    r"\le": sp.Le,
    r"\leq": sp.Le,
    r"\leqq": sp.Le,
    r"\ge": sp.Ge,
    r"\geq": sp.Ge,
    r"\geqq": sp.Ge,
}

#: 開き括弧コマンド -> (閉じ括弧の候補, 包む関数, 絶対値と同じ扱いか)
PAIRED_DELIMS: dict[str, tuple[tuple[str, ...], Callable | None, bool]] = {
    r"\lvert": ((r"\rvert", r"\vert", "|"), sp.Abs, True),
    r"\vert": ((r"\rvert", r"\vert", "|"), sp.Abs, True),
    r"\lVert": ((r"\rVert", r"\Vert"), sp.Abs, True),
    r"\Vert": ((r"\rVert", r"\Vert"), sp.Abs, True),
    r"\lfloor": ((r"\rfloor",), sp.floor, False),
    r"\lceil": ((r"\rceil",), sp.ceiling, False),
    r"\langle": ((r"\rangle",), None, False),
}

#: 開きと閉じが同じ綴りの記号。暗黙の掛け算と紛らわしいので特別扱いする。
AMBIGUOUS_ABS_DELIMS = frozenset({"|", r"\vert", r"\Vert"})

MULTIPLY_OPS = {r"\cdot", r"\times", "*", r"\ast"}
DIVIDE_OPS = {"/", r"\div"}

OPEN_DELIMS = {"(": ")", "[": "]", r"\{": r"\}", r"\lbrace": r"\rbrace",
               r"\lbrack": r"\rbrack", r"\langle": r"\rangle", "|": "|",
               r"\lvert": r"\rvert", r"\lVert": r"\rVert", r"\vert": r"\vert"}


# --------------------------------------------------------------------------
# 巨大な値の生成を防ぐヘルパ
# --------------------------------------------------------------------------


def _check_integer_size(value) -> None:
    """生成された整数・有理数が巨大すぎないか確認する。"""
    try:
        if isinstance(value, sp.Rational):
            for part in (value.p, value.q):
                if len(str(abs(int(part)))) > MAX_INT_DIGITS:
                    raise InputTooLargeError("数値が大きすぎます。")
            return
        for number in value.atoms(sp.Integer):
            if len(str(abs(int(number)))) > MAX_INT_DIGITS:
                raise InputTooLargeError("数値が大きすぎます。")
    except InputTooLargeError:
        raise
    except Exception:
        return


def safe_pow(base, exponent, position: int | None = None):
    """``base ** exponent`` を上限付きで構築する。"""
    if getattr(exponent, "is_Integer", False):
        if abs(int(exponent)) > MAX_EXPONENT:
            raise InputTooLargeError(
                f"指数が大きすぎます (絶対値 {MAX_EXPONENT} まで)。", position=position
            )
    elif getattr(exponent, "is_Rational", False):
        if abs(int(exponent.p)) > MAX_EXPONENT or abs(int(exponent.q)) > MAX_EXPONENT:
            raise InputTooLargeError(
                f"指数が大きすぎます (絶対値 {MAX_EXPONENT} まで)。", position=position
            )
    elif getattr(exponent, "is_number", False) and not exponent.free_symbols:
        # π などの定数冪は許容するが、巨大な数値になるものは弾く
        pass
    value = sp.Pow(base, exponent)
    _check_integer_size(value)
    return value


def safe_factorial(argument, position: int | None = None):
    if getattr(argument, "is_Integer", False) and int(argument) > MAX_FACTORIAL:
        raise InputTooLargeError(
            f"階乗の引数が大きすぎます ({MAX_FACTORIAL} まで)。", position=position
        )
    value = sp.factorial(argument)
    _check_integer_size(value)
    return value


def safe_binomial(n, k, position: int | None = None):
    for part in (n, k):
        if getattr(part, "is_Integer", False) and abs(int(part)) > MAX_FACTORIAL:
            raise InputTooLargeError(
                f"二項係数の引数が大きすぎます ({MAX_FACTORIAL} まで)。",
                position=position,
            )
    value = sp.binomial(n, k)
    _check_integer_size(value)
    return value


# --------------------------------------------------------------------------
# パース結果
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ParsedAnswer:
    """解析済みの解答。

    kind:
        * ``"expr"``  : 単一の式             (例: ``x^2+1``)
        * ``"rel"``   : 関係式               (例: ``x = 1``, ``x \\le 2``)
        * ``"set"``   : 集合                 (例: ``\\{1, 2\\}``)
        * ``"list"``  : カンマ区切りの複数解 (例: ``1, 2``)
    """

    kind: str
    items: list = field(default_factory=list)
    source: str = ""

    @property
    def single(self):
        return self.items[0]


# --------------------------------------------------------------------------
# パーサ本体
# --------------------------------------------------------------------------


class LatexParser:
    def __init__(self, tokens: list[Token], options: ParseOptions) -> None:
        self.tokens = tokens
        self.i = 0
        self.opt = options
        self.depth = 0
        self.abs_depth = 0
        #: いま集合を読んでよい位置か (文の先頭のみ True)
        self._set_ok = False

    # -- トークン操作 ------------------------------------------------------
    @property
    def cur(self) -> Token:
        return self.tokens[self.i]

    def peek(self, offset: int = 0) -> Token:
        idx = min(self.i + offset, len(self.tokens) - 1)
        return self.tokens[idx]

    def advance(self) -> Token:
        tok = self.tokens[self.i]
        if tok.kind != "eof":
            self.i += 1
        return tok

    def accept(self, kind: str, value: str | None = None) -> Token | None:
        tok = self.cur
        if tok.kind == kind and (value is None or tok.value == value):
            return self.advance()
        return None

    def expect(self, kind: str, value: str | None = None) -> Token:
        tok = self.accept(kind, value)
        if tok is None:
            want = value if value is not None else kind
            raise LatexSyntaxError(
                f"{want!r} が必要ですが {self.cur.value!r} がありました。",
                position=self.cur.pos,
            )
        return tok

    def _enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise InputTooLargeError("式のネストが深すぎます。")

    def _leave(self) -> None:
        self.depth -= 1

    # -- シンボル生成 ------------------------------------------------------
    def symbol(self, name: str) -> sp.Symbol:
        if self.opt.assume_positive:
            return sp.Symbol(name, positive=True)
        if self.opt.assume_real:
            return sp.Symbol(name, real=True)
        return sp.Symbol(name)

    # -- トップレベル ------------------------------------------------------
    def parse_answer(self) -> ParsedAnswer:
        items = [self.parse_statement()]
        while self.accept("punct", ","):
            if self.cur.kind == "eof":
                raise LatexSyntaxError("末尾のカンマの後に式がありません。",
                                       position=self.cur.pos)
            items.append(self.parse_statement())
        if self.cur.kind != "eof":
            self._reject_unsupported(self.cur)
            raise LatexSyntaxError(
                f"解釈できない記号 {self.cur.value!r} が残っています。",
                position=self.cur.pos,
            )
        if len(items) == 1:
            item = items[0]
            if isinstance(item, sp.Set):
                return ParsedAnswer("set", [item])
            if isinstance(item, sp.logic.boolalg.Boolean) and not isinstance(
                item, sp.Symbol
            ):
                return ParsedAnswer("rel", [item])
            return ParsedAnswer("expr", [item])
        return ParsedAnswer("list", items)

    def parse_statement(self):
        # 集合は式の一部にはなれない (``\{1,2\}+1`` のような無意味な式を防ぐ)。
        # 文全体が集合のときだけ受け付ける。
        if self._at_set_start():
            value = self.parse_atom_set()
            if not (
                self.cur.kind == "eof"
                or (self.cur.kind == "punct" and self.cur.value == ",")
            ):
                self._reject_unsupported(self.cur)
                raise UnsupportedLatexError(
                    "集合を式の一部として使うことはできません。",
                    position=self.cur.pos,
                )
            return value
        left = self.parse_expr()
        relations: list = []
        while True:
            op = self._peek_relation()
            if op is None:
                break
            self.advance()
            right = self.parse_expr()
            relations.append(RELATION_OPS[op](left, right, evaluate=False))
            left = right
        if not relations:
            return left
        if len(relations) == 1:
            return relations[0]
        return sp.And(*relations)

    @staticmethod
    def _reject_unsupported(tok: Token) -> None:
        """非対応と分かっているコマンドなら、その理由を返して打ち切る。"""
        if tok.kind == "command" and tok.value in UNSUPPORTED_MESSAGES:
            raise UnsupportedLatexError(
                UNSUPPORTED_MESSAGES[tok.value], position=tok.pos
            )

    def _at_set_start(self) -> bool:
        tok = self.cur
        if tok.kind == "command" and tok.value in (
            r"\{", r"\lbrace", r"\emptyset", r"\varnothing"
        ):
            return True
        if tok.kind == "command" and tok.value == r"\left":
            nxt = self.peek(1)
            return nxt.kind == "command" and nxt.value in (r"\{", r"\lbrace")
        return False

    def parse_atom_set(self):
        """文の先頭にある集合を読む。"""
        self._set_ok = True
        try:
            return self._parse_atom_set_inner()
        finally:
            self._set_ok = False

    def _parse_atom_set_inner(self):
        tok = self.cur
        if tok.kind == "command" and tok.value in (r"\emptyset", r"\varnothing"):
            self.advance()
            return sp.EmptySet
        if tok.kind == "command" and tok.value == r"\left":
            return self._parse_left_right()
        return self._parse_set_literal(closing=(r"\}", r"\rbrace"))

    def _peek_relation(self) -> str | None:
        tok = self.cur
        if tok.kind == "punct" and tok.value in RELATION_OPS:
            return tok.value
        if tok.kind == "command" and tok.value in RELATION_OPS:
            return tok.value
        return None

    # -- 式 ----------------------------------------------------------------
    def parse_expr(self):
        self._enter()
        try:
            node = self.parse_term()
            while True:
                if self.accept("punct", "+"):
                    node = sp.Add(node, self.parse_term())
                elif self.accept("punct", "-"):
                    node = sp.Add(node, sp.Mul(sp.Integer(-1), self.parse_term()))
                elif self.cur.kind == "command" and self.cur.value in (r"\pm", r"\mp"):
                    # 通常は parse_latex_answer が符号を展開するので届かない
                    raise UnsupportedLatexError(
                        r"この位置の \pm / \mp は解釈できません。",
                        position=self.cur.pos,
                    )
                else:
                    break
            return node
        finally:
            self._leave()

    def parse_term(self):
        self._enter()
        try:
            node = self.parse_unary()
            while True:
                tok = self.cur
                if (tok.kind == "punct" and tok.value in MULTIPLY_OPS) or (
                    tok.kind == "command" and tok.value in MULTIPLY_OPS
                ):
                    self.advance()
                    node = sp.Mul(node, self.parse_unary())
                elif (tok.kind == "punct" and tok.value in DIVIDE_OPS) or (
                    tok.kind == "command" and tok.value in DIVIDE_OPS
                ):
                    self.advance()
                    node = sp.Mul(node, safe_pow(self.parse_unary(), sp.Integer(-1)))
                elif self._starts_atom(tok):
                    # 暗黙の掛け算 (2x, x y, 2\pi, ...)
                    node = sp.Mul(node, self.parse_unary())
                else:
                    break
            return node
        finally:
            self._leave()

    def parse_unary(self):
        if self.accept("punct", "-"):
            return sp.Mul(sp.Integer(-1), self.parse_unary())
        if self.accept("punct", "+"):
            return self.parse_unary()
        return self.parse_power()

    def parse_power(self):
        base = self.parse_postfix()
        if self.accept("punct", "^"):
            if self._accept_degree():
                # 90^\circ = 90 * pi / 180 (厳密な有理数倍として扱う)
                return sp.Mul(base, sp.pi, sp.Pow(sp.Integer(180), sp.Integer(-1)))
            position = self.cur.pos
            exponent = self.parse_unary()
            return safe_pow(base, exponent, position)
        return base

    def _accept_degree(self) -> bool:
        """``^\\circ`` / ``^{\\circ}`` (度) を読めたら True。"""
        tok = self.cur
        if tok.kind == "command" and tok.value in (r"\circ", r"\degree"):
            self.advance()
            return True
        if (
            tok.kind == "punct"
            and tok.value == "{"
            and self.peek(1).kind == "command"
            and self.peek(1).value in (r"\circ", r"\degree")
            and self.peek(2).kind == "punct"
            and self.peek(2).value == "}"
        ):
            self.advance()
            self.advance()
            self.advance()
            return True
        return False

    def parse_postfix(self):
        node = self.parse_atom()
        while self.cur.kind == "punct" and self.cur.value == "!":
            position = self.advance().pos
            node = safe_factorial(node, position)
        return node

    # -- atom --------------------------------------------------------------
    def _starts_atom(self, tok: Token) -> bool:
        if tok.kind in ("number", "letter"):
            return True
        if tok.kind == "punct":
            if tok.value == "|":
                # 絶対値の内側では、閉じ '|' を新しい atom の開始と誤解しない。
                return self.abs_depth == 0
            return tok.value in ("(", "[", "{")
        if tok.kind == "command":
            v = tok.value
            if v in AMBIGUOUS_ABS_DELIMS:
                # 絶対値・ノルムの内側では、閉じ記号を新しい atom の
                # 開始と誤解しない (|x| と同じ理由)。
                return self.abs_depth == 0
            return (
                v in GREEK
                or v in UNARY_FUNCTIONS
                or v in ATOM_COMMANDS
                or v == r"\left"
            )
        return False

    def parse_atom(self):
        self._enter()
        try:
            return self._parse_atom_inner()
        finally:
            self._leave()

    def _parse_atom_inner(self):
        tok = self.cur
        if tok.kind == "number":
            self.advance()
            value = sp.Rational(tok.value)  # 小数も厳密な有理数として扱う
            return self._maybe_subscript_number(value)
        if tok.kind == "letter":
            self.advance()
            return self._letter_atom(tok)
        if tok.kind == "punct":
            if tok.value == "(":
                self.advance()
                node = self.parse_expr()
                self.expect("punct", ")")
                return node
            if tok.value == "[":
                self.advance()
                node = self.parse_expr()
                self.expect("punct", "]")
                return node
            if tok.value == "{":
                self.advance()
                node = self.parse_expr()
                self.expect("punct", "}")
                return node
            if tok.value == "|":
                self.advance()
                self.abs_depth += 1
                try:
                    node = self.parse_expr()
                finally:
                    self.abs_depth -= 1
                self.expect("punct", "|")
                return sp.Abs(node)
            raise LatexSyntaxError(
                f"式の位置に {tok.value!r} は置けません。", position=tok.pos
            )
        if tok.kind == "command":
            return self._command_atom(tok)
        raise LatexSyntaxError("式が途中で終わっています。", position=tok.pos)

    def _maybe_subscript_number(self, value):
        if self.cur.kind == "punct" and self.cur.value == "_":
            raise UnsupportedLatexError("数値に添字は付けられません。",
                                        position=self.cur.pos)
        return value

    def _letter_atom(self, tok: Token):
        name = tok.value
        # 添字
        if self.cur.kind == "punct" and self.cur.value == "_":
            self.advance()
            name = f"{name}_{self._read_subscript_name()}"
            return self.symbol(name)
        if name == "e" and self.opt.euler_e:
            return sp.E
        if name == "i" and self.opt.imaginary_i:
            return sp.I
        return self.symbol(name)

    def _read_subscript_name(self) -> str:
        """``x_1`` / ``x_{12}`` / ``a_{n+1}`` の添字部分を読む。

        添字は式として解釈したうえで正規形の文字列にする。こうすると
        ``a_{n+1}`` と ``a_{1+n}`` が同じ記号になる。
        """
        if self.accept("punct", "{"):
            if self.accept("punct", "}"):
                raise LatexSyntaxError("添字が空です。", position=self.cur.pos)
            if self._subscript_has_operator():
                # a_{n+1} と a_{1+n} を同じ記号にするため、式として正規化する
                expr = self.parse_expr()
                self.expect("punct", "}")
                return _subscript_label(expr)
            # 演算子が無いならラベルとして literal に扱う。
            # 式として読むと x_{ab} と x_{ba} が a*b で同一視されてしまう。
            return self._read_subscript_literal()
        tok = self.cur
        if tok.kind in ("number", "letter"):
            self.advance()
            return tok.value
        if tok.kind == "command" and tok.value in GREEK:
            self.advance()
            return GREEK[tok.value]
        raise UnsupportedLatexError("添字には英数字のみ使用できます。", position=tok.pos)

    def _subscript_has_operator(self) -> bool:
        """``{`` の次から対応する ``}`` までに演算子があるか覗く。"""
        depth = 1
        i = self.i
        operators = {"+", "-", "*", "/", "^"}
        while i < len(self.tokens):
            tok = self.tokens[i]
            if tok.kind == "eof":
                break
            if tok.kind == "punct":
                if tok.value == "{":
                    depth += 1
                elif tok.value == "}":
                    depth -= 1
                    if depth == 0:
                        break
                elif tok.value in operators:
                    return True
            elif tok.kind == "command" and (
                tok.value in MULTIPLY_OPS or tok.value in DIVIDE_OPS
            ):
                return True
            i += 1
        return False

    def _read_subscript_literal(self) -> str:
        """``x_{ab}`` のような添字を、書かれた順のラベルとして読む。"""
        parts: list[str] = []
        while not (self.cur.kind == "punct" and self.cur.value == "}"):
            tok = self.cur
            if tok.kind in ("number", "letter"):
                parts.append(tok.value)
                self.advance()
            elif tok.kind == "command" and tok.value in GREEK:
                parts.append(GREEK[tok.value])
                self.advance()
            else:
                raise UnsupportedLatexError(
                    "添字には英数字とギリシャ文字のみ使用できます。", position=tok.pos
                )
            if len("".join(parts)) > 32:
                raise InputTooLargeError("添字が長すぎます。")
        self.expect("punct", "}")
        if not parts:
            raise LatexSyntaxError("添字が空です。", position=self.cur.pos)
        return "".join(parts)

    # -- コマンド ----------------------------------------------------------
    def _command_atom(self, tok: Token):
        cmd = tok.value

        if cmd in GREEK:
            self.advance()
            if self.cur.kind == "punct" and self.cur.value == "_":
                self.advance()
                return self.symbol(f"{GREEK[cmd]}_{self._read_subscript_name()}")
            return self.symbol(GREEK[cmd])

        if cmd == r"\pi":
            self.advance()
            return sp.pi

        if cmd == r"\infty":
            self.advance()
            return sp.oo

        if cmd in (r"\emptyset", r"\varnothing"):
            raise UnsupportedLatexError(
                "空集合を式の一部として使うことはできません。", position=tok.pos
            )

        if cmd in (r"\frac", r"\dfrac", r"\tfrac", r"\cfrac"):
            self.advance()
            num = self.parse_group(single_char=True)
            den = self.parse_group(single_char=True)
            return sp.Mul(num, safe_pow(den, sp.Integer(-1), tok.pos))

        if cmd == r"\sqrt":
            self.advance()
            index = None
            if self.accept("punct", "["):
                index = self.parse_expr()
                self.expect("punct", "]")
            radicand = self.parse_group()
            if index is None:
                return sp.sqrt(radicand)
            if index.is_Integer and abs(int(index)) > MAX_EXPONENT:
                raise InputTooLargeError(
                    "根号の指数が大きすぎます。", position=tok.pos
                )
            # 奇数乗根は実数の根を使う。
            # SymPy の既定は主値 (複素数) なので、(-8)^(1/3) は 1+√3i になり、
            # 高校で期待される \sqrt[3]{-8} = -2 が WA になってしまう。
            if (
                self.opt.assume_real
                and index.is_Integer
                and int(index) > 0
                and int(index) % 2 == 1
            ):
                return sp.real_root(radicand, int(index))
            return safe_pow(radicand, safe_pow(index, sp.Integer(-1), tok.pos), tok.pos)

        if cmd in (r"\binom", r"\dbinom", r"\tbinom"):
            self.advance()
            n = self.parse_group(single_char=True)
            k = self.parse_group(single_char=True)
            return safe_binomial(n, k, tok.pos)

        if cmd in (r"\overline", r"\bar"):
            self.advance()
            return sp.conjugate(self.parse_group())

        if cmd in FONT_COMMANDS:
            self.advance()
            return self._parse_mathrm_group()

        if cmd in DECORATORS:
            self.advance()
            return self.symbol(f"{DECORATORS[cmd]}_{self._read_decorated_name()}")

        if cmd in MULTI_ARG_COMMANDS:
            self.advance()
            name = MULTI_ARG_COMMANDS[cmd]
            return self._apply_multi_arg(MULTI_ARG_FUNCTIONS[name], name, tok)

        if cmd in (r"\text", r"\textrm", r"\textit", r"\textbf", r"\mbox"):
            raise UnsupportedLatexError(r"\text{} は使用できません。",
                                        position=tok.pos)

        if cmd in UNSUPPORTED_MESSAGES:
            raise UnsupportedLatexError(UNSUPPORTED_MESSAGES[cmd], position=tok.pos)

        if cmd == r"\operatorname":
            self.advance()
            name = self._read_plain_name()
            if name in MULTI_ARG_FUNCTIONS:
                return self._apply_multi_arg(MULTI_ARG_FUNCTIONS[name], name, tok)
            func = NAMED_FUNCTIONS.get(name)
            if func is None:
                raise UnsupportedLatexError(
                    f"関数 {name!r} には対応していません。", position=tok.pos
                )
            return self._apply_function(func, tok)

        if cmd == r"\log" or cmd == r"\lg":
            self.advance()
            base = None
            if cmd == r"\lg":
                base = sp.Integer(10)
            if self.accept("punct", "_"):
                base = self.parse_group_or_atom()
            if base is None:
                base = sp.Integer(10) if self.opt.log_base == "10" else None
            exponent = self._read_function_exponent()
            arg = self.parse_function_argument()
            value = sp.log(arg) if base is None else sp.log(arg, base)
            if exponent is not None:
                value = safe_pow(value, exponent, tok.pos)
            return value

        if cmd in UNARY_FUNCTIONS:
            self.advance()
            exponent = self._read_function_exponent()
            if exponent is not None and exponent == sp.Integer(-1):
                inverse = INVERSE_FUNCTIONS.get(cmd)
                if inverse is None:
                    raise UnsupportedLatexError(
                        f"{cmd} の逆関数表記には対応していません。", position=tok.pos
                    )
                return inverse(self.parse_function_argument())
            func = UNARY_FUNCTIONS[cmd]
            value = func(self.parse_function_argument())
            if exponent is not None:
                value = safe_pow(value, exponent, tok.pos)
            return value

        if cmd == r"\left":
            return self._parse_left_right()

        if cmd in (r"\{", r"\lbrace"):
            raise UnsupportedLatexError(
                "集合を式の一部として使うことはできません。", position=tok.pos
            )

        if cmd in PAIRED_DELIMS:
            return self._parse_paired_delim(cmd)

        if cmd == r"\begin":
            raise UnsupportedLatexError(
                "行列などの環境には対応していません。", position=tok.pos
            )

        raise UnsupportedLatexError(
            f"未対応のコマンド {cmd} が含まれています。", position=tok.pos
        )

    def _parse_mathrm_group(self):
        """``\\mathrm{e}`` -> E、それ以外は中身を式として解釈する。"""
        if self.cur.kind == "punct" and self.cur.value == "{":
            save = self.i
            self.advance()
            if (
                self.cur.kind == "letter"
                and self.cur.value == "e"
                and self.peek(1).kind == "punct"
                and self.peek(1).value == "}"
            ):
                self.advance()
                self.advance()
                return sp.E
            self.i = save
        return self.parse_group()

    def _read_decorated_name(self) -> str:
        """``\\vec{a}`` / ``\\vec a`` の中身を 1 文字のラベルとして読む。"""
        braced = self.accept("punct", "{") is not None
        tok = self.cur
        if tok.kind == "letter":
            self.advance()
            name = tok.value
        elif tok.kind == "command" and tok.value in GREEK:
            self.advance()
            name = GREEK[tok.value]
        else:
            raise UnsupportedLatexError(
                "装飾記号の中には 1 文字の変数だけ書けます。", position=tok.pos
            )
        if self.cur.kind == "punct" and self.cur.value == "_":
            self.advance()
            name = f"{name}_{self._read_subscript_name()}"
        if braced:
            self.expect("punct", "}")
        return name

    def _apply_multi_arg(self, func: Callable, name: str, tok: Token):
        """``\\gcd(12, 18)`` のように括弧とカンマで引数を取る関数を読む。"""
        if self.cur.kind == "command" and self.cur.value == r"\left":
            self.advance()
            opener = self.advance()
            if opener.value != "(":
                raise LatexSyntaxError(
                    f"{name} の引数は ( ) で囲んでください。", position=opener.pos
                )
            args = self._read_arg_list()
            self.expect("command", r"\right")
            closer = self.advance()
            if closer.value != ")":
                raise LatexSyntaxError(
                    f"{name} の引数は ( ) で囲んでください。", position=closer.pos
                )
        else:
            if not self.accept("punct", "("):
                raise LatexSyntaxError(
                    f"{name} の引数は ( ) で囲んでください。", position=self.cur.pos
                )
            args = self._read_arg_list()
            self.expect("punct", ")")
        if len(args) < 2:
            raise LatexSyntaxError(
                f"{name} には 2 つ以上の引数が必要です。", position=tok.pos
            )
        return func(*args)

    def _read_arg_list(self) -> list:
        args = [self.parse_expr()]
        while self.accept("punct", ","):
            if len(args) >= 8:
                raise InputTooLargeError("関数の引数が多すぎます。")
            args.append(self.parse_expr())
        return args

    def _parse_paired_delim(self, cmd: str):
        """``\\lfloor x \\rfloor`` のような対になる括弧コマンドを読む。"""
        closings, wrapper, is_abs = PAIRED_DELIMS[cmd]
        self.advance()
        if is_abs:
            self.abs_depth += 1
        try:
            node = self.parse_expr()
        finally:
            if is_abs:
                self.abs_depth -= 1
        for closing in closings:
            if closing == "|":
                if self.accept("punct", "|"):
                    break
            elif self.accept("command", closing):
                break
        else:
            raise LatexSyntaxError(
                f"{cmd} に対応する閉じ記号がありません。", position=self.cur.pos
            )
        return node if wrapper is None else wrapper(node)

    def _read_plain_name(self) -> str:
        self.expect("punct", "{")
        parts: list[str] = []
        while self.cur.kind == "letter":
            parts.append(self.advance().value)
            if len(parts) > 16:
                raise InputTooLargeError("関数名が長すぎます。")
        self.expect("punct", "}")
        if not parts:
            raise LatexSyntaxError("関数名が空です。", position=self.cur.pos)
        return "".join(parts)

    def _read_function_exponent(self):
        """``\\sin^2 x`` / ``\\sin^{-1} x`` の指数部分。"""
        if self.cur.kind == "punct" and self.cur.value == "^":
            self.advance()
            return self.parse_unary()
        return None

    def _apply_function(self, func, tok: Token):
        exponent = self._read_function_exponent()
        value = func(self.parse_function_argument())
        if exponent is not None:
            value = safe_pow(value, exponent, tok.pos)
        return value

    def parse_function_argument(self):
        """括弧なし関数適用の引数。

        規則 (README にも明記):
            * 直後が ``(`` / ``{`` / ``\\left(`` ならその括弧の中身だけを引数とする。
            * そうでなければ「数・文字・ギリシャ文字・定数とその冪」が
              並んでいる限りをまとめて引数とする。
              例: ``\\sin 2x`` -> ``sin(2x)`` 、``\\sin x\\cos x`` -> ``sin(x)cos(x)``
        """
        tok = self.cur
        if tok.kind == "punct" and tok.value in ("(", "{"):
            return self.parse_atom()
        if tok.kind == "command" and tok.value == r"\left":
            return self.parse_atom()
        if tok.kind == "command" and tok.value in (r"\frac", r"\dfrac", r"\tfrac",
                                                   r"\sqrt"):
            return self.parse_power()

        factors = []
        while True:
            t = self.cur
            simple = t.kind in ("number", "letter") or (
                t.kind == "command" and (t.value in GREEK or t.value == r"\pi")
            )
            if not simple:
                break
            factors.append(self.parse_power())
            if len(factors) > 8:
                break
        if not factors:
            raise LatexSyntaxError("関数の引数がありません。", position=tok.pos)
        return sp.Mul(*factors)

    def parse_group(self, single_char: bool = False):
        """``{...}`` を読む。``{`` が無い場合は 1 つの atom を読む。

        ``single_char=True`` のときは、括弧を省いた引数を 1 文字だけ取る。
        本来の TeX も ``\\frac12`` を ``\\frac{1}{2}`` と読むため。
        """
        if self.accept("punct", "{"):
            node = self.parse_expr()
            self.expect("punct", "}")
            return node
        if single_char:
            self._split_leading_digit()
        return self.parse_power()

    def _split_leading_digit(self) -> None:
        """``12`` のような数トークンを ``1`` と ``2`` に分ける。

        ``\\frac12`` / ``\\binom52`` / ``\\log_23`` のように括弧を省いた
        書き方を、本来の TeX と同じ意味で読めるようにする。小数点を含む
        トークンは意味が変わってしまうので分けない。
        """
        tok = self.cur
        if tok.kind != "number" or len(tok.value) <= 1 or "." in tok.value:
            return
        self.tokens[self.i] = Token("number", tok.value[0], tok.pos)
        self.tokens.insert(self.i + 1, Token("number", tok.value[1:], tok.pos + 1))

    def parse_group_or_atom(self):
        if self.cur.kind == "punct" and self.cur.value == "{":
            return self.parse_group()
        self._split_leading_digit()
        return self.parse_atom()

    def _parse_left_right(self):
        self.expect("command", r"\left")
        opener = self.advance()
        opener_value = opener.value
        if opener_value == "." :
            opener_value = "."
        if opener_value not in ("(", "[", "|", ".", r"\{", r"\lbrace", r"\lbrack",
                                r"\lvert", r"\vert", r"\lVert", r"\Vert",
                                r"\lfloor", r"\lceil", r"\langle"):
            raise UnsupportedLatexError(
                rf"\left{opener_value} には対応していません。", position=opener.pos
            )
        if opener_value in (r"\{", r"\lbrace"):
            if not self._set_ok:
                raise UnsupportedLatexError(
                    "集合を式の一部として使うことはできません。", position=opener.pos
                )
            return self._parse_set_body(left_right=True)

        inside_abs = opener_value in ("|", r"\lvert", r"\vert", r"\lVert",
                                     r"\Vert")
        if inside_abs:
            self.abs_depth += 1
        try:
            node = self.parse_expr()
        finally:
            if inside_abs:
                self.abs_depth -= 1
        self.expect("command", r"\right")
        closer = self.advance()
        pairs = {
            "(": (")", "."),
            "[": ("]", "."),
            r"\lbrack": (r"\rbrack", "]", "."),
            "|": ("|", r"\rvert", r"\vert", "."),
            r"\lvert": ("|", r"\rvert", r"\vert", "."),
            r"\vert": ("|", r"\rvert", r"\vert", "."),
            r"\lVert": (r"\rVert", r"\Vert", "."),
            r"\Vert": (r"\rVert", r"\Vert", "."),
            r"\lfloor": (r"\rfloor", "."),
            r"\lceil": (r"\rceil", "."),
            r"\langle": (r"\rangle", "."),
            ".": (")", "]", "|", ".", r"\rvert", r"\rVert", r"\rfloor",
                  r"\rceil", r"\rangle", r"\}", r"\rbrace", r"\rbrack"),
        }
        allowed = pairs.get(opener_value, (".",))
        if closer.value not in allowed:
            raise LatexSyntaxError(
                f"括弧の対応が取れていません ({opener_value} … {closer.value})。",
                position=closer.pos,
            )
        if opener_value in ("|", r"\lvert", r"\vert", r"\lVert", r"\Vert"):
            return sp.Abs(node)
        if opener_value == r"\lfloor":
            return sp.floor(node)
        if opener_value == r"\lceil":
            return sp.ceiling(node)
        return node

    def _parse_set_literal(self, closing: tuple[str, ...]):
        self.advance()  # \{
        return self._parse_set_body(left_right=False, closing=closing)

    def _parse_set_body(self, left_right: bool,
                        closing: tuple[str, ...] = (r"\}", r"\rbrace")):
        elements = []
        if not self._at_set_close(left_right, closing):
            elements.append(self.parse_statement())
            while self.accept("punct", ","):
                elements.append(self.parse_statement())
        if left_right:
            self.expect("command", r"\right")
            closer = self.advance()
            if closer.value not in (r"\}", r"\rbrace", "."):
                raise LatexSyntaxError("集合の閉じ括弧がありません。",
                                       position=closer.pos)
        else:
            tok = self.cur
            if not (tok.kind == "command" and tok.value in closing):
                raise LatexSyntaxError("集合の閉じ括弧がありません。", position=tok.pos)
            self.advance()
        if not elements:
            return sp.EmptySet
        return sp.FiniteSet(*elements)

    def _at_set_close(self, left_right: bool, closing: tuple[str, ...]) -> bool:
        tok = self.cur
        if left_right:
            return tok.kind == "command" and tok.value == r"\right"
        return tok.kind == "command" and tok.value in closing


def _subscript_label(expr) -> str:
    """添字の式を、順序に依らない安定した文字列に変換する。

    添字はあくまでラベルなので、複雑な式は受け付けない。
    展開の前に大きさを検査しないと ``a_{(x+y+z+w)^{200}}`` のような入力で
    多項式展開が爆発する (パーサは出題者向けエンドポイントでは
    リクエストスレッド上で同期実行されるため、ここで止める必要がある)。
    """
    _check_subscript_size(expr)
    try:
        text = sp.sstr(sp.expand(expr))
    except Exception:  # pragma: no cover - 防御的
        text = str(expr)
    text = text.replace(" ", "")
    if len(text) > 32:
        raise InputTooLargeError("添字が長すぎます。")
    return text


def _check_subscript_size(expr) -> None:
    """添字の式が展開しても安全な大きさか確認する。"""
    try:
        if sp.count_ops(expr) > MAX_SUBSCRIPT_OPS:
            raise InputTooLargeError("添字の式が複雑すぎます。")
        for power in expr.atoms(sp.Pow):
            exponent = power.exp
            if exponent.is_Integer and abs(int(exponent)) > MAX_SUBSCRIPT_EXPONENT:
                raise InputTooLargeError("添字の指数が大きすぎます。")
            if not exponent.is_Integer and not exponent.is_Rational:
                raise InputTooLargeError("添字の式が複雑すぎます。")
        if len(expr.free_symbols) > 4:
            raise InputTooLargeError("添字の式が複雑すぎます。")
    except InputTooLargeError:
        raise
    except Exception:  # pragma: no cover - 防御的
        raise InputTooLargeError("添字の式を解釈できません。") from None


#: atom を開始できるコマンド (暗黙の掛け算の判定に使う)
ATOM_COMMANDS = frozenset(
    {
        r"\pi",
        r"\infty",
        r"\frac",
        r"\dfrac",
        r"\tfrac",
        r"\cfrac",
        r"\sqrt",
        r"\binom",
        r"\dbinom",
        r"\tbinom",
        r"\overline",
        r"\bar",
        r"\operatorname",
        r"\log",
        r"\lg",
    }
    | FONT_COMMANDS
    | frozenset(DECORATORS)
    | frozenset(MULTI_ARG_COMMANDS)
    | frozenset(PAIRED_DELIMS)
)


# --------------------------------------------------------------------------
# 公開 API
# --------------------------------------------------------------------------


def _check_no_float(expr) -> None:
    """浮動小数点数が紛れ込んでいないことを保証する。"""
    try:
        floats = expr.atoms(sp.Float)
    except AttributeError:
        return
    if floats:
        raise UnsupportedLatexError(
            "内部で浮動小数点数が生成されました (厳密判定できません)。"
        )


def _check_size(expr) -> None:
    try:
        count = sp.count_ops(expr)
    except Exception:  # pragma: no cover - 防御的
        return
    if count > MAX_NODES:
        raise InputTooLargeError("式が大きすぎます。")


#: 複号 (\pm, \mp)
PLUS_MINUS = (r"\pm", r"\mp")


def _has_plus_minus(tokens: list[Token]) -> bool:
    return any(t.kind == "command" and t.value in PLUS_MINUS for t in tokens)


def _apply_plus_minus(tokens: list[Token], sign: int) -> list[Token]:
    r"""複号を通常の ``+`` / ``-`` に置き換えたトークン列を作る。

    ``sign == 1`` で ``\pm -> +``、``sign == -1`` で ``\pm -> -``。
    ``\mp`` は常に ``\pm`` の逆。つまり複号同順で展開する。
    """
    out: list[Token] = []
    for tok in tokens:
        if tok.kind == "command" and tok.value == r"\pm":
            out.append(Token("punct", "+" if sign > 0 else "-", tok.pos))
        elif tok.kind == "command" and tok.value == r"\mp":
            out.append(Token("punct", "-" if sign > 0 else "+", tok.pos))
        else:
            out.append(tok)
    return out


def _dedup_items(items: list) -> list:
    r"""構造的に等しい要素を潰す (``x \pm 0`` が 2 解にならないように)。"""
    unique: list = []
    for item in items:
        if not any(item == other for other in unique):
            unique.append(item)
    return unique


def _set_elements(answer: ParsedAnswer) -> list:
    value = answer.single
    if value is sp.S.EmptySet:
        return []
    return list(value.args)


def _merge_plus_minus(first: ParsedAnswer, second: ParsedAnswer) -> ParsedAnswer:
    """複号を展開した 2 通りの解釈を 1 つの解答にまとめる。"""
    if first.kind == "set" or second.kind == "set":
        if first.kind != second.kind:  # pragma: no cover - 構文が同じなので起きない
            raise UnsupportedLatexError("複号を解釈できません。")
        elements = _dedup_items(_set_elements(first) + _set_elements(second))
        value = sp.FiniteSet(*elements) if elements else sp.S.EmptySet
        return ParsedAnswer("set", [value])
    items = _dedup_items(list(first.items) + list(second.items))
    if len(items) == 1:
        return ParsedAnswer(first.kind, items)
    return ParsedAnswer("list", items)


def parse_latex_answer(source: str, options: ParseOptions | None = None) -> ParsedAnswer:
    """TeX 文字列を ``ParsedAnswer`` に変換する。

    ``eval`` 等は一切使用しない。
    """
    options = options or ParseOptions()
    if source is None or not source.strip():
        raise LatexSyntaxError("解答が空です。")
    tokens = tokenize(source)
    if _has_plus_minus(tokens):
        # 複号は「複号同順」で 2 通りに展開し、複数解として扱う。
        # 例: x = "\\pm 2"  ->  x = 2, x = -2
        first = LatexParser(_apply_plus_minus(tokens, 1), options).parse_answer()
        second = LatexParser(_apply_plus_minus(tokens, -1), options).parse_answer()
        answer = _merge_plus_minus(first, second)
    else:
        answer = LatexParser(tokens, options).parse_answer()
    answer.source = source
    for item in answer.items:
        _check_no_float(item)
        _check_size(item)
        _check_integer_size(item)
    return answer


def parse_latex_expr(source: str, options: ParseOptions | None = None):
    """単一の式としてパースする (テスト・内部用)。"""
    answer = parse_latex_answer(source, options)
    if answer.kind != "expr":
        raise UnsupportedLatexError("単一の式ではありません。")
    return answer.single
