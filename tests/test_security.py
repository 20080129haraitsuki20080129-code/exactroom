"""セキュリティ要件の自動検証。"""

from __future__ import annotations

import ast
import re
import time
from pathlib import Path

import pytest

from app.security import (
    TokenError,
    create_token,
    generate_room_code,
    hash_secret,
    normalize_room_code,
    verify_secret,
    verify_token,
)

ROOT = Path(__file__).resolve().parent.parent
JUDGE_DIR = ROOT / "app" / "mathjudge"
DECISION_FILES = ["parser.py", "exactzero.py", "equivalence.py", "tokenizer.py"]


# --------------------------------------------------------------------------
# ソースコードレベルの禁止事項
# --------------------------------------------------------------------------


def _python_sources(directory: Path):
    """AppleDouble (``._xxx.py``) などを除いた .py ファイルを列挙する。"""
    for path in sorted(directory.rglob("*.py")):
        if path.name.startswith("._") or "__pycache__" in path.parts:
            continue
        yield path


def _calls(tree: ast.AST):
    """AST 上の呼び出しを ``(種別, 名前, 行番号)`` で列挙する。

    種別は ``"bare"`` (``eval(...)``) か ``"attr"`` (``re.compile(...)``)。
    文字列 (docstring やコメント) は対象外なので、説明文に禁止語が
    出てきても誤検出しない。
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            yield "bare", func.id, node.lineno
        elif isinstance(func, ast.Attribute):
            yield "attr", func.attr, node.lineno


#: 組み込みとして呼んではいけないもの
FORBIDDEN_BARE = {"eval", "exec", "compile", "__import__", "execfile"}
#: 属性呼び出しとして呼んではいけないもの (re.compile は安全なので含めない)
FORBIDDEN_ATTR = {"eval", "exec", "evalf"}

#: 判定コードで呼んではいけない数値評価系
NUMERIC_BARE = {
    "float",
    "complex",
    "N",
    "nsimplify",
    "sympify",
    "parse_expr",
    "parse_latex",
    "nsolve",
    "nfloat",
    "round",
}
NUMERIC_ATTR = {"evalf", "nsimplify", "sympify", "n", "round"}


def test_no_eval_or_exec_anywhere_in_app():
    """``eval`` / ``exec`` / ``compile`` / ``__import__`` を呼ばない。"""
    for path in _python_sources(ROOT / "app"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for kind, name, lineno in _calls(tree):
            bad = FORBIDDEN_BARE if kind == "bare" else FORBIDDEN_ATTR
            assert name not in bad, f"{path.name}:{lineno} で {name}() を呼んでいる"
        # 名前としての参照 (getattr 経由の迂回) も禁止
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in {"eval", "exec"}:
                raise AssertionError(f"{path.name}:{node.lineno} に {node.id} がある")


def test_no_numeric_evaluation_in_decision_code():
    """判定コードで数値近似 (evalf / N / float / complex) を呼ばない。"""
    for name in DECISION_FILES:
        path = JUDGE_DIR / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for kind, called, lineno in _calls(tree):
            bad = NUMERIC_BARE if kind == "bare" else NUMERIC_ATTR
            assert called not in bad, (
                f"{name}:{lineno} で数値評価関数 {called}() を呼んでいる"
            )


def test_decision_code_does_not_import_sympy_parsing():
    """SymPy の文字列パーサ (内部で eval 相当を行う) を使わない。"""
    for name in DECISION_FILES:
        path = JUDGE_DIR / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "parsing" not in (node.module or ""), f"{name}:{node.lineno}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert "parsing" not in alias.name, f"{name}:{node.lineno}"


def test_runner_does_not_evaluate_numerically():
    """ランナーでも判定に関わる数値評価はしない (タイムアウト秒の float は可)。"""
    path = JUDGE_DIR / "runner.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for _kind, called, lineno in _calls(tree):
        assert called not in {"evalf", "N", "sympify", "eval", "exec"}, (
            f"{path.name}:{lineno}"
        )


def test_no_float_type_used_in_parser_output():
    import sympy as sp

    from app.mathjudge.parser import parse_latex_expr

    for source in [r"0.1", r"3.14159", r"\frac{1}{3}", r"\sqrt{2}", r"1.0"]:
        assert not parse_latex_expr(source).atoms(sp.Float)


# --------------------------------------------------------------------------
# パスワード・トークン
# --------------------------------------------------------------------------


def test_secret_hash_roundtrip():
    stored = hash_secret("correct horse battery staple")
    assert "correct horse" not in stored
    assert verify_secret("correct horse battery staple", stored)
    assert not verify_secret("wrong", stored)
    assert not verify_secret("", stored)
    # 同じ平文でもソルトが違うのでハッシュは変わる
    assert stored != hash_secret("correct horse battery staple")


def test_secret_hash_rejects_broken_records():
    assert not verify_secret("x", "")
    assert not verify_secret("x", "garbage")
    assert not verify_secret("x", "md5$1$a$b")


def test_token_signature_is_verified():
    key = "k" * 32
    token = create_token({"role": "host", "room_id": 1}, key, 60)
    assert verify_token(token, key)["role"] == "host"

    with pytest.raises(TokenError):
        verify_token(token, "another-key")

    head, sig = token.split(".")
    with pytest.raises(TokenError):
        verify_token(f"{head}x.{sig}", key)
    with pytest.raises(TokenError):
        verify_token(f"{head}.{sig[:-2]}AA", key)
    with pytest.raises(TokenError):
        verify_token("not-a-token", key)


def test_token_expiry():
    key = "k" * 32
    token = create_token({"role": "solver"}, key, -1)
    with pytest.raises(TokenError):
        verify_token(token, key)


def test_room_code_entropy():
    codes = {generate_room_code(6) for _ in range(500)}
    assert len(codes) > 480  # ほぼ重複しない
    for code in codes:
        assert re.fullmatch(r"[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{6}", code)


def test_room_code_normalization():
    assert normalize_room_code(" ab-cd ef ") == "ABCDEF"
    assert normalize_room_code("ＡＢＣ２３４") == "ABC234"
    assert normalize_room_code(None) == ""


# --------------------------------------------------------------------------
# HTTP レベル
# --------------------------------------------------------------------------


def test_security_headers(app_client):
    response = app_client.get("/healthz")
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_api_responses_are_not_cached(app_client, room):
    response = app_client.get(f"/api/rooms/{room['code']}")
    assert response.headers["Cache-Control"] == "no-store"


def test_tampered_token_is_rejected(app_client, room):
    token = room["host_token"]
    head, sig = token.split(".")
    bad = f"{head}.{'A' * len(sig)}"
    response = app_client.get(
        "/api/host/problems", headers={"Authorization": f"Bearer {bad}"}
    )
    assert response.status_code == 401


def test_solver_token_cannot_be_used_as_host(app_client, solver):
    assert solver.get("/api/host/problems").status_code == 403


def test_missing_authorization(app_client):
    assert app_client.get("/api/host/problems").status_code == 401
    assert (
        app_client.get(
            "/api/host/problems", headers={"Authorization": "Basic abc"}
        ).status_code
        == 401
    )


def test_sql_injection_in_room_code(app_client):
    response = app_client.get("/api/rooms/' OR 1=1 --")
    assert response.status_code == 404


def test_xss_payload_is_stored_as_text_not_html(app_client, host, room):
    payload = "<script>alert(1)</script>"
    response = host.post(
        "/api/host/problems",
        json={"title": payload, "answer_latex": "1", "is_published": True},
    )
    assert response.status_code == 201
    assert response.headers["content-type"].startswith("application/json")
    # JSON なのでブラウザが HTML として解釈することはない
    assert response.json()["title"] == payload


def test_rate_limit_on_host_login(app_client, room):
    """秘密キーの総当たりが、設定した回数で止まること。"""
    from app.config import get_settings

    limit = get_settings().rate_host_login_per_minute
    codes = []
    for _ in range(limit + 5):
        codes.append(
            app_client.post(
                f"/api/rooms/{room['code']}/host/login", json={"secret": "nope"}
            ).status_code
        )
    assert 429 in codes, f"上限 {limit} を超えても止まらない"
    assert codes.count(401) <= limit


def test_rate_limits_allow_a_whole_class_from_one_ip():
    """教室の全員が同じ IP に見えても参加できる上限になっていること。

    学校の NAT 越しでは 40 人が 1 つの IP に見える。
    ここが小さいと、後半の生徒が参加できなくなる (実際に起きた)。
    """
    from app.config import get_settings

    settings = get_settings()
    assert settings.rate_join_per_minute >= 80, "1 クラス分の同時参加に足りない"
    assert settings.rate_submit_per_minute >= 200, "1 クラス分の同時提出に足りない"
    assert settings.rate_general_per_minute >= 600


def test_oversized_answer_rejected(app_client, host, solver):
    problem = host.post(
        "/api/host/problems",
        json={"title": "x", "answer_latex": "1", "is_published": True},
    ).json()
    response = solver.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": "1" * 5000},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        "2^{999999}",
        "9^{9^{9}}",
        "1000000!",
        r"\binom{999999}{3}",
        "x" * 400,
        "(" * 60 + "1" + ")" * 60,
        r"\frac{1}{0}",
        "1+" * 300 + "1",
    ],
)
def test_pathological_inputs_are_fast(payload):
    """病的な入力でも短時間で終わる (DoS 対策)。"""
    from app.mathjudge.equivalence import judge

    started = time.monotonic()
    result = judge(r"\frac{1}{2}", payload)
    elapsed = time.monotonic() - started
    assert elapsed < 5.0, f"{payload[:20]} に {elapsed:.1f} 秒かかった"
    assert result.status in ("judged", "undecided", "input_error", "problem_error", "timeout", "internal_error")
    assert (result.status == "judged") == (result.verdict in ("AC", "WA"))


def test_judge_never_raises():
    from app.mathjudge.equivalence import judge

    for payload in ["", "   ", "\x00", "\\", "}{", "\\left(", "|", "\\frac{}{}"]:
        result = judge(r"1", payload)
        assert result.status in ("judged", "undecided", "input_error", "problem_error", "timeout", "internal_error")
        assert (result.status == "judged") == (result.verdict in ("AC", "WA"))


def test_non_ascii_token_is_rejected_not_crashed():
    """非 ASCII のトークンで未処理例外 (HTTP 500) にならないこと。"""
    for bad in ["あ.い", "abc.\u3042", "\u00e9.x", "a b.c"]:
        with pytest.raises(TokenError):
            verify_token(bad, "k" * 32)


def test_non_ascii_bearer_header_returns_401(app_client):
    """HTTP ヘッダは latin-1 で解釈されるので、非 ASCII バイトが届きうる。

    以前はこれが ``UnicodeEncodeError`` になり HTTP 500 を返していた
    (未認証のリクエストでサーバ内部エラーを起こせた)。
    """
    for raw in [b"Bearer \xe9.x", b"Bearer \xff\xfe.abc", b"Bearer caf\xe9.sig"]:
        response = app_client.get(
            "/api/host/problems", headers={"Authorization": raw}
        )
        assert response.status_code == 401, (raw, response.status_code, response.text)


def test_token_length_is_bounded():
    with pytest.raises(TokenError):
        verify_token("a" * 9000 + ".b", "k" * 32)
