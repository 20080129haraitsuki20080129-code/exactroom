"""模範解答と提出答案を exact-first / numerical-fingerprint で判定する。

数学Verdictは AC / WA の2値。比較内部では次の状態を使う:

* ``EQUIVALENT`` … exact proof または規定数値fingerprintで一致
* ``NOT_EQUIVALENT`` … exact counterexample または規定数値fingerprintで不一致
* ``UNDECIDED`` … 数値fallbackへ進む前の内部状態

``reason`` には判定の根拠コードが入る。提出者に返してよいのは
``verdict`` と、提出者自身の入力に関する ``student_message`` だけであり、
``reason`` / ``detail`` は出題者だけが見る。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

import sympy as sp
from sympy import S

from .errors import MathError
from .exactzero import NONZERO, ZERO, decide_zero, prove_equal
from .numerical import numerical_fingerprint
from .parser import ParsedAnswer, ParseOptions, parse_latex_answer

AC = "AC"
WA = "WA"
UNKNOWN = "unknown"
# Backward-compatible symbol only; judge() never returns this internal state.
PENDING = UNKNOWN

# A mathematical comparison is independent from the request/worker lifecycle.
EQUIVALENT = "equivalent"
NOT_EQUIVALENT = "not_equivalent"
UNDECIDED = "undecided"

JUDGED = "judged"
INPUT_ERROR = "input_error"
PROBLEM_ERROR = "problem_error"
INTERNAL_ERROR = "internal_error"

#: 集合・リストの要素数の上限 (総当たり比較の爆発を防ぐ)
MAX_ELEMENTS = 12


@dataclass(slots=True)
class JudgeResult:
    verdict: str | None
    reason: str
    status: str | None = None
    #: 出題者向けの補足 (提出者には返さない)
    detail: str = ""
    #: 提出者に見せてよいメッセージ (自分の入力に関するものだけ)
    student_message: str = ""
    elapsed_ms: int = 0
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status or (JUDGED if self.verdict in (AC, WA) else UNDECIDED),
            "verdict": self.verdict,
            "reason": self.reason,
            "detail": self.detail,
            "student_message": self.student_message,
            "elapsed_ms": self.elapsed_ms,
            "meta": dict(self.meta),
        }


# --------------------------------------------------------------------------
# 個々の要素の比較
# --------------------------------------------------------------------------


def _relation_family(rel) -> str:
    if isinstance(rel, sp.Equality):
        return "eq"
    if isinstance(rel, sp.Unequality):
        return "ne"
    if isinstance(rel, sp.StrictLessThan | sp.StrictGreaterThan):
        return "strict"
    if isinstance(rel, sp.LessThan | sp.GreaterThan):
        return "weak"
    return "other"


def _normalize_relation(rel):
    """``lhs op rhs`` を ``d op' 0`` の形に正規化する。

    Returns:
        ``(family, direction, d)``
        family: "eq" / "ne" / "strict" / "weak"
        direction: 0 (=, ≠) / +1 (d > 0, d >= 0) / -1 は使わず符号反転で吸収
    """
    family = _relation_family(rel)
    lhs, rhs = rel.args[0], rel.args[1]
    diff = sp.Add(lhs, sp.Mul(S(-1), rhs))
    if family in ("eq", "ne"):
        return family, 0, diff
    if isinstance(rel, sp.StrictLessThan | sp.LessThan):
        # lhs < rhs  <=>  rhs - lhs > 0
        return family, 1, sp.Mul(S(-1), diff)
    return family, 1, diff


def _compare_relations(a, b, domain_conditions=()) -> str:
    r"""2 つの関係式が同値かを判定する。

    WA を返すのは「一方だけを満たす厳密な点」を実際に見つけられたときだけ。
    関係の種類 (= / ≠ / < / ≦) が違うだけでは非同値の証明にならない
    (``x^2 \ge 0`` と ``x^2+1 > 0`` はどちらも恒真で同値)。
    """
    fam_a, _, da = _normalize_relation(a)
    fam_b, _, db = _normalize_relation(b)

    if fam_a == fam_b:
        # d_a と d_b が「0 でない定数倍」の関係にあれば同値
        ratio = None
        try:
            ratio = sp.cancel(sp.together(sp.Mul(da, sp.Pow(db, S(-1)))))
        except Exception:
            ratio = None
        if ratio is not None and not ratio.free_symbols and not ratio.has(sp.zoo, sp.nan):
            status = decide_zero(ratio)
            if status == NONZERO:
                if fam_a in ("eq", "ne"):
                    return AC
                # 不等号は正の定数倍のときだけ向きが保たれる
                simplified = sp.simplify(ratio)
                if simplified.is_Rational and simplified > 0:
                    return AC
                if simplified.is_Rational and simplified < 0:
                    return WA
                return UNKNOWN

    # 反例探索: 真偽が食い違う厳密な点が見つかれば非同値が確定する
    if _relation_witness(fam_a, da, fam_b, db, domain_conditions):
        return WA
    if fam_a == fam_b and prove_equal(da, db) == ZERO:
        return AC
    return UNKNOWN


def _truth_at(family: str, value):
    """``value ▷ 0`` の真偽を厳密に判定する。決定できなければ ``None``。

    等式・不等号で「満たす」の意味が違うので分けて扱う。
    不等号は符号が厳密な有理数として確定する点だけを採用する
    (符号決定に数値近似を使わないため)。
    """
    if value is None or not isinstance(value, sp.Expr):
        return None
    if value.has(sp.zoo, sp.nan):
        return None
    if family in ("eq", "ne"):
        status = decide_zero(value)
        if status == ZERO:
            return family == "eq"
        if status == NONZERO:
            return family == "ne"
        return None
    sign = _rational_sign(value)
    if sign is None:
        return None
    return sign > 0 if family == "strict" else sign >= 0


def _relation_witness(
    fam_a: str, da, fam_b: str, db, domain_conditions=()
) -> bool:
    """一方の関係式だけを満たす厳密な点を探す。

    見つかればその点が非同値の証明になる。見つからなくても
    「同値である」ことにはならない (判定保留になる)。
    """
    from .exactzero import _conditions_hold

    symbols, candidates = _witness_candidates(da, db)
    if not symbols:
        if not _conditions_hold(domain_conditions, {}):
            return False
        ta, tb = _truth_at(fam_a, da), _truth_at(fam_b, db)
        if ta is None or tb is None:
            return False
        return ta != tb
    for subs in candidates:
        if not _conditions_hold(domain_conditions, subs):
            continue
        try:
            va = da.subs(subs, simultaneous=True)
            vb = db.subs(subs, simultaneous=True)
        except Exception:
            continue
        ta, tb = _truth_at(fam_a, va), _truth_at(fam_b, vb)
        if ta is None or tb is None:
            continue
        if ta != tb:
            return True
    return False


def _witness_candidates(da, db) -> tuple[list, list[dict]]:
    """反例候補となる厳密な代入の一覧を作る。

    記号に付けた仮定 (正・実数など) を満たす値だけを使う。
    """
    from .exactzero import substitution_candidates

    symbols = sorted(da.free_symbols | db.free_symbols, key=lambda s: s.name)
    if not symbols:
        return symbols, []
    candidates = substitution_candidates(symbols, limit=16)
    # 片方の根を反例候補に加える (1 変数の低次多項式のみ、厳密解)
    if len(symbols) == 1:
        sym = symbols[0]
        for expr in (da, db):
            try:
                poly = sp.Poly(sp.cancel(sp.together(expr)).as_numer_denom()[0], sym)
            except Exception:
                continue
            if poly.degree() > 4:
                continue
            try:
                roots = sp.roots(poly)
            except Exception:
                continue
            for root in roots:
                if root.free_symbols or root.has(sp.zoo, sp.nan):
                    continue
                if not _respects_assumptions(sym, root):
                    continue
                candidates.append({sym: root})
    return symbols, candidates


def _respects_assumptions(symbol, value) -> bool:
    """代入値が記号の仮定を破っていないか。"""
    violations = (
        symbol.is_positive is True and value.is_positive is not True,
        symbol.is_nonnegative is True and value.is_negative is True,
        symbol.is_real is True and value.is_real is not True,
        symbol.is_integer is True and value.is_integer is not True,
    )
    return not any(violations)


def _rational_sign(value) -> int | None:
    """厳密な有理数としての符号。有理数に落ちなければ ``None``。

    不等式の真偽判定に数値近似を使わないため、符号が厳密に確定する
    (有理数になる) 点だけを反例候補として採用する。
    """
    if not isinstance(value, sp.Expr) or value.free_symbols:
        return None
    if value.has(sp.zoo, sp.nan, sp.oo, -sp.oo):
        return None
    candidate = value
    if not candidate.is_Rational:
        candidate = _safe_simplify(candidate)
        if candidate is None or not candidate.is_Rational:
            return None
    if candidate > 0:
        return 1
    if candidate < 0:
        return -1
    return 0


def _safe_simplify(value):
    try:
        out = sp.cancel(sp.together(value))
        if not out.is_Rational:
            out = sp.simplify(out)
    except Exception:
        return None
    if out is None or out.atoms(sp.Float):
        return None
    return out


def _compare_sets(a: sp.Set, b: sp.Set, domain_conditions=()) -> str:
    if a is S.EmptySet or b is S.EmptySet:
        if a is S.EmptySet and b is S.EmptySet:
            return AC
        return WA
    if not (isinstance(a, sp.FiniteSet) and isinstance(b, sp.FiniteSet)):
        return UNKNOWN
    ea, eb = list(a.args), list(b.args)
    if len(ea) > MAX_ELEMENTS or len(eb) > MAX_ELEMENTS:
        return UNKNOWN
    return _compare_collections(
        ea, eb, ordered=False, domain_conditions=domain_conditions
    )


def _compare_collections(
    ea: list, eb: list, ordered: bool, domain_conditions=()
) -> str:
    if ordered:
        if len(ea) != len(eb):
            return WA
        statuses = [
            _compare_items(x, y, domain_conditions)
            for x, y in zip(ea, eb, strict=False)
        ]
        if all(s == AC for s in statuses):
            return AC
        if any(s == WA for s in statuses):
            return WA
        return UNKNOWN

    ea = list(ea)
    eb = list(eb)
    if len(ea) > MAX_ELEMENTS or len(eb) > MAX_ELEMENTS:
        return UNKNOWN

    matrix = [
        [_compare_items(x, y, domain_conditions) for y in eb] for x in ea
    ]

    # 「一方にしか現れない要素」が確定すれば非同値
    for row in matrix:
        if row and all(s == WA for s in row):
            return WA
    for col in range(len(eb)):
        column = [matrix[r][col] for r in range(len(ea))]
        if column and all(s == WA for s in column):
            return WA
    if any(UNKNOWN in row for row in matrix):
        return UNKNOWN

    # ここまで来れば全ペアの等価判定が確定している。
    # 重複を潰したうえで集合として一致するかを見る。
    def dedup(elements: list) -> list:
        out: list = []
        for e in elements:
            if not any(
                _compare_items(e, o, domain_conditions) == AC for o in out
            ):
                out.append(e)
        return out

    ua, ub = dedup(ea), dedup(eb)
    if len(ua) != len(ub):
        return WA
    for e in ua:
        if not any(_compare_items(e, o, domain_conditions) == AC for o in ub):
            return WA
    return AC


def _compare_items(a, b, domain_conditions=()) -> str:
    """式・関係式・集合の 1 要素どうしを比較する。"""
    a_is_rel = isinstance(a, sp.core.relational.Relational)
    b_is_rel = isinstance(b, sp.core.relational.Relational)
    a_is_and = isinstance(a, sp.And)
    b_is_and = isinstance(b, sp.And)
    a_is_set = isinstance(a, sp.Set)
    b_is_set = isinstance(b, sp.Set)

    if a_is_set or b_is_set:
        if a_is_set and b_is_set:
            return _compare_sets(a, b, domain_conditions)
        return WA
    if a_is_and or b_is_and:
        if a_is_and and b_is_and:
            return _compare_collections(
                list(a.args),
                list(b.args),
                ordered=False,
                domain_conditions=domain_conditions,
            )
        return WA
    if a_is_rel or b_is_rel:
        if a_is_rel and b_is_rel:
            return _compare_relations(a, b, domain_conditions)
        return WA

    status = prove_equal(a, b, domain_conditions=domain_conditions)
    if status == ZERO:
        return AC
    if status == NONZERO:
        return WA
    return UNKNOWN


# --------------------------------------------------------------------------
# 公開 API
# --------------------------------------------------------------------------

_KIND_ORDER = {"expr": 0, "rel": 1, "set": 2, "list": 2}


def compare_parsed(
    model: ParsedAnswer, submitted: ParsedAnswer, ordered_list: bool = False
) -> tuple[str, str]:
    """解析済みの解答どうしを比較する。

    Returns:
        ``(equivalence_state, reason)``. This is deliberately separate from
        request status and the legacy AC/WA/UNKNOWN verdict field.
    """
    mk, sk = model.kind, submitted.kind
    # 集合とカンマ区切りリストは同じ「複数解」とみなす
    if _KIND_ORDER[mk] != _KIND_ORDER[sk]:
        return NOT_EQUIVALENT, "kind_mismatch"

    if _KIND_ORDER[mk] == 2:
        ma = model.single if mk == "set" else None
        sa = submitted.single if sk == "set" else None
        melems = list(ma.args) if isinstance(ma, sp.FiniteSet) else (
            [] if ma is S.EmptySet else list(model.items)
        )
        selems = list(sa.args) if isinstance(sa, sp.FiniteSet) else (
            [] if sa is S.EmptySet else list(submitted.items)
        )
        if mk == "set" and ma is S.EmptySet:
            melems = []
        if sk == "set" and sa is S.EmptySet:
            selems = []
        if not melems and not selems:
            return EQUIVALENT, "empty_sets"
        if bool(melems) != bool(selems):
            return NOT_EQUIVALENT, "cardinality"
        verdict = _compare_collections(
            melems,
            selems,
            ordered=ordered_list,
            domain_conditions=model.domain_conditions + submitted.domain_conditions,
        )
        state = {AC: EQUIVALENT, WA: NOT_EQUIVALENT}.get(verdict, UNDECIDED)
        reason = {
            EQUIVALENT: "collection_equal",
            NOT_EQUIVALENT: "collection_differs",
        }.get(state, "collection_undecided")
        return state, reason

    verdict = _compare_items(
        model.single,
        submitted.single,
        model.domain_conditions + submitted.domain_conditions,
    )
    if mk == "rel":
        from .relations import compare_relation_sets

        state = compare_relation_sets(
            model.single,
            submitted.single,
            model.domain_conditions,
            submitted.domain_conditions,
        )
        family = "equation" if (
            isinstance(model.single, sp.Equality)
            or isinstance(submitted.single, sp.Equality)
        ) else "inequality"
        if state is None:
            state = {AC: EQUIVALENT, WA: NOT_EQUIVALENT}.get(verdict, UNDECIDED)
            family = "relation"
        reason = {
            EQUIVALENT: f"{family}_solution_equal",
            NOT_EQUIVALENT: f"{family}_solution_differs",
        }.get(state, f"{family}_undecided")
    else:
        from .relations import compare_expression_domains

        domain_state = compare_expression_domains(
            model.domain_conditions, submitted.domain_conditions
        )
        if domain_state == NOT_EQUIVALENT:
            return NOT_EQUIVALENT, "domain_mismatch"
        if domain_state == UNDECIDED:
            return UNDECIDED, "domain_undecided"
        state = {AC: EQUIVALENT, WA: NOT_EQUIVALENT}.get(verdict, UNDECIDED)
        reason = {
            EQUIVALENT: "expression_equal",
            NOT_EQUIVALENT: "expression_differs",
        }.get(state, "expression_undecided")
    return state, reason


def judge(
    model_latex: str,
    submitted_latex: str,
    options: dict | None = None,
) -> JudgeResult:
    """模範解答 (TeX) と提出答案 (TeX) を突き合わせる。

    この関数は **別プロセス** で呼ばれることを想定している
    (``app/mathjudge/runner.py`` を参照)。
    """
    started = time.monotonic()
    opts = dict(options or {})
    ordered_list = bool(opts.pop("ordered_list", False))
    sampling_seed = opts.pop("sampling_seed", None)
    if not isinstance(sampling_seed, bytes) or len(sampling_seed) < 16:
        sampling_seed = hashlib.sha256(
            (model_latex + "\0" + submitted_latex).encode("utf-8")
        ).digest()
    parse_opts = ParseOptions.from_dict(opts)

    def finish(result: JudgeResult) -> JudgeResult:
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result

    # --- 提出答案のパース (エラー内容は提出者に返してよい) ---
    try:
        submitted = parse_latex_answer(submitted_latex, parse_opts)
    except MathError as exc:
        return finish(
            JudgeResult(
                status=INPUT_ERROR,
                verdict=None,
                reason="submission_parse_error",
                detail=f"提出答案を解釈できません: {exc.message}",
                student_message=exc.message,
                meta={"error_code": exc.code, "position": exc.position},
            )
        )
    except Exception:
        return finish(
            JudgeResult(
                status=INPUT_ERROR,
                verdict=None,
                reason="submission_parse_error",
                detail="提出答案を解釈できません。",
                student_message="解答を数式として解釈できませんでした。",
            )
        )

    # --- 模範解答のパース (エラー内容は絶対に提出者へ返さない) ---
    try:
        model = parse_latex_answer(model_latex, parse_opts)
    except MathError as exc:
        return finish(
            JudgeResult(
                status=PROBLEM_ERROR,
                verdict=None,
                reason="model_parse_error",
                detail=f"模範解答を解釈できません: {exc.message}",
                student_message="この問題は現在採点できません。出題者にお問い合わせください。",
                meta={"error_code": exc.code},
            )
        )
    except Exception:
        return finish(
            JudgeResult(
                status=PROBLEM_ERROR,
                verdict=None,
                reason="model_parse_error",
                detail="模範解答を解釈できません。",
                student_message="この問題は現在採点できません。出題者にお問い合わせください。",
            )
        )

    try:
        state, reason = compare_parsed(model, submitted, ordered_list=ordered_list)
    except Exception as exc:  # pragma: no cover - 防御的
        return finish(
            JudgeResult(
                status=INTERNAL_ERROR,
                verdict=None,
                reason="internal_error",
                detail=f"判定中に例外が発生しました: {type(exc).__name__}",
                student_message="判定に失敗しました。時間をおいて再送してください。",
            )
        )

    numeric_meta = {}
    has_variables = any(
        bool(getattr(item, "free_symbols", set()))
        for parsed in (model, submitted)
        for item in parsed.items
    )
    # The documented 10,000-digit policy treats a smaller constant difference
    # as equal, even when exact arithmetic can exhibit that nonzero difference.
    # Variable expressions retain exact WA evidence before fingerprinting.
    numeric_fallback = state == UNDECIDED or (
        state == NOT_EQUIVALENT and not has_variables
    )
    if numeric_fallback:
        evidence = numerical_fingerprint(
            model,
            submitted,
            seed=sampling_seed,
            ordered_list=ordered_list,
        )
        if evidence is None:
            return finish(
                JudgeResult(
                    status=INTERNAL_ERROR,
                    verdict=None,
                    reason="numeric_fingerprint_failed",
                    detail="No sufficient valid samples could be evaluated.",
                    student_message="採点処理に失敗しました。時間をおいて再送してください。",
                    meta={"model_kind": model.kind, "submitted_kind": submitted.kind},
                )
            )
        if state == UNDECIDED or evidence.equivalent:
            state = EQUIVALENT if evidence.equivalent else NOT_EQUIVALENT
            reason = evidence.reason
            numeric_meta = {
                "judge_method": "numeric_10000d",
                "judge_precision": evidence.precision,
                "sample_count": evidence.valid_samples,
            }

    verdict = {EQUIVALENT: AC, NOT_EQUIVALENT: WA}.get(state)
    messages = {
        AC: "あなたの答えは、採点基準上、模範解答と同じ内容です。",
        WA: "あなたの答えは、模範解答とは違う内容です。",
        None: "採点処理に失敗しました。時間をおいて再送してください。",
    }
    return finish(
        JudgeResult(
            status=JUDGED if verdict is not None else UNDECIDED,
            verdict=verdict,
            reason=reason,
            detail=(
                f"kind={model.kind}/{submitted.kind} reason={reason} "
                f"method={numeric_meta.get('judge_method', 'exact_symbolic')}"
            ),
            student_message=messages[verdict],
            meta={"model_kind": model.kind, "submitted_kind": submitted.kind, **numeric_meta},
        )
    )
