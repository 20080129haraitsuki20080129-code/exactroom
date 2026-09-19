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
    r"\sinh": sp.asinh,
    r"\cosh": sp.acosh,
    r"\tanh": sp.atanh,
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
                    raise UnsupportedLatexError(
                        r"\pm / \mp には対応していません。答えを分けて書いてください。",
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
            position = self.cur.pos
            exponent = self.parse_unary()
            return safe_pow(base, exponent, position)
        return base

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
            return (
                v in GREEK
                or v in UNARY_FUNCTIONS
                or v in ATOM_COMMANDS
                or v in (r"\left", r"\lvert", r"\vert")
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
            # 複数桁の数字 (x_{12}) は連結された数値として読まれるのでそのままでよい
            expr = self.parse_expr()
            self.expect("punct", "}")
            return _subscript_label(expr)
        tok = self.cur
        if tok.kind in ("number", "letter"):
            self.advance()
            return tok.value
        if tok.kind == "command" and tok.value in GREEK:
            self.advance()
            return GREEK[tok.value]
        raise UnsupportedLatexError("添字には英数字のみ使用できます。", position=tok.pos)

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
            num = self.parse_group()
            den = self.parse_group()
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
            return safe_pow(radicand, safe_pow(index, sp.Integer(-1), tok.pos), tok.pos)

        if cmd == r"\binom" or cmd == r"\dbinom":
            self.advance()
            n = self.parse_group()
            k = self.parse_group()
            return safe_binomial(n, k, tok.pos)

        if cmd == r"\overline":
            self.advance()
            return sp.conjugate(self.parse_group())

        if cmd == r"\mathrm" or cmd == r"\mathit" or cmd == r"\mathbf":
            self.advance()
            return self._parse_mathrm_group()

        if cmd == r"\text" or cmd == r"\textrm":
            raise UnsupportedLatexError(r"\text{} は使用できません。",
                                        position=tok.pos)

        if cmd == r"\operatorname":
            self.advance()
            name = self._read_plain_name()
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

        if cmd in (r"\lvert", r"\vert"):
            self.advance()
            self.abs_depth += 1
            try:
                node = self.parse_expr()
            finally:
                self.abs_depth -= 1
            if not (
                self.accept("command", r"\rvert")
                or self.accept("command", r"\vert")
                or self.accept("punct", "|")
            ):
                raise LatexSyntaxError("絶対値の閉じ記号がありません。",
                                       position=self.cur.pos)
            return sp.Abs(node)

        if cmd in (r"\sum", r"\prod", r"\int", r"\lim", r"\oint", r"\iint"):
            raise UnsupportedLatexError(
                f"{cmd} には対応していません。", position=tok.pos
            )

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

    def parse_group(self):
        """``{...}`` を読む。``{`` が無い場合は 1 つの atom を読む。"""
        if self.accept("punct", "{"):
            node = self.parse_expr()
            self.expect("punct", "}")
            return node
        return self.parse_power()

    def parse_group_or_atom(self):
        if self.cur.kind == "punct" and self.cur.value == "{":
            return self.parse_group()
        return self.parse_atom()

    def _parse_left_right(self):
        self.expect("command", r"\left")
        opener = self.advance()
        opener_value = opener.value
        if opener_value == "." :
            opener_value = "."
        if opener_value not in ("(", "[", "|", ".", r"\{", r"\lbrace", r"\lvert",
                                r"\vert", r"\lVert", r"\langle"):
            raise UnsupportedLatexError(
                rf"\left{opener_value} には対応していません。", position=opener.pos
            )
        if opener_value in (r"\{", r"\lbrace"):
            if not self._set_ok:
                raise UnsupportedLatexError(
                    "集合を式の一部として使うことはできません。", position=opener.pos
                )
            return self._parse_set_body(left_right=True)

        inside_abs = opener_value in ("|", r"\lvert", r"\vert", r"\lVert")
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
            "|": ("|", r"\rvert", r"\vert", "."),
            r"\lvert": ("|", r"\rvert", r"\vert", "."),
            r"\vert": ("|", r"\rvert", r"\vert", "."),
            r"\langle": (r"\rangle", "."),
            ".": (")", "]", "|", ".", r"\rvert", r"\}", r"\rbrace"),
        }
        allowed = pairs.get(opener_value, (".",))
        if closer.value not in allowed:
            raise LatexSyntaxError(
                f"括弧の対応が取れていません ({opener_value} … {closer.value})。",
                position=closer.pos,
            )
        if opener_value in ("|", r"\lvert", r"\vert"):
            return sp.Abs(node)
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
        r"\overline",
        r"\mathrm",
        r"\mathit",
        r"\mathbf",
        r"\operatorname",
        r"\log",
        r"\lg",
    }
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


def parse_latex_answer(source: str, options: ParseOptions | None = None) -> ParsedAnswer:
    """TeX 文字列を ``ParsedAnswer`` に変換する。

    ``eval`` 等は一切使用しない。
    """
    options = options or ParseOptions()
    if source is None or not source.strip():
        raise LatexSyntaxError("解答が空です。")
    tokens = tokenize(source)
    parser = LatexParser(tokens, options)
    answer = parser.parse_answer()
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
