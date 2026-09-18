"""パスワードハッシュ・署名付きトークン・部屋コード生成。

外部ライブラリに依存せず、Python 標準ライブラリだけで実装する
(無料ホスティングでの依存トラブルを避けるため)。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import unicodedata

PBKDF2_ITERATIONS = 200_000
PBKDF2_ALGO = "sha256"
SALT_BYTES = 16

#: 紛らわしい文字 (0/O, 1/I/L) を除いた部屋コード用アルファベット
ROOM_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


# --------------------------------------------------------------------------
# パスワード (部屋の秘密キー・復帰コード)
# --------------------------------------------------------------------------


def hash_secret(secret: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    """PBKDF2-HMAC-SHA256 でハッシュ化し、``algo$iter$salt$hash`` 形式で返す。"""
    if not isinstance(secret, str) or not secret:
        raise ValueError("secret が空です。")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(PBKDF2_ALGO, secret.encode("utf-8"), salt, iterations)
    return "$".join(
        [
            f"pbkdf2_{PBKDF2_ALGO}",
            str(iterations),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ]
    )


def verify_secret(secret: str, stored: str) -> bool:
    """定数時間で照合する。"""
    if not stored or not isinstance(secret, str):
        return False
    try:
        algo, iterations_s, salt_b64, hash_b64 = stored.split("$")
        if not algo.startswith("pbkdf2_"):
            return False
        iterations = int(iterations_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except Exception:
        return False
    digest = hashlib.pbkdf2_hmac(
        algo.removeprefix("pbkdf2_"), secret.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(digest, expected)


# --------------------------------------------------------------------------
# 署名付きトークン (ログイン不要なセッション)
# --------------------------------------------------------------------------


class TokenError(Exception):
    pass


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def create_token(payload: dict, secret_key: str, ttl_seconds: int) -> str:
    """HMAC-SHA256 で署名したトークンを作る (JWT 互換ではない独自形式)。"""
    body = dict(payload)
    now = int(time.time())
    body["iat"] = now
    body["exp"] = now + int(ttl_seconds)
    body["jti"] = secrets.token_urlsafe(8)
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = _b64encode(raw)
    signature = hmac.new(
        secret_key.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{encoded}.{_b64encode(signature)}"


def verify_token(token: str, secret_key: str) -> dict:
    """トークンを検証して payload を返す。失敗時は ``TokenError``。"""
    if not token or not isinstance(token, str) or token.count(".") != 1:
        raise TokenError("トークンの形式が不正です。")
    encoded, signature_b64 = token.split(".")
    if len(encoded) > 4096:
        raise TokenError("トークンが長すぎます。")
    expected = hmac.new(
        secret_key.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    try:
        given = _b64decode(signature_b64)
    except Exception as exc:
        raise TokenError("トークンの署名を読めません。") from exc
    if not hmac.compare_digest(expected, given):
        raise TokenError("署名が一致しません。")
    try:
        payload = json.loads(_b64decode(encoded))
    except Exception as exc:
        raise TokenError("トークンの内容を読めません。") from exc
    if not isinstance(payload, dict):
        raise TokenError("トークンの内容が不正です。")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise TokenError("トークンの有効期限が切れています。")
    return payload


# --------------------------------------------------------------------------
# 部屋コード・名前の正規化
# --------------------------------------------------------------------------


def generate_room_code(length: int = 6) -> str:
    return "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(length))


def normalize_room_code(code: str) -> str:
    """入力ゆれ (小文字・全角・空白・ハイフン) を吸収する。

    部屋コードのアルファベットは 0/O, 1/I/L を最初から含まないので、
    紛らわしい文字の置換は行わない (誤変換で別の部屋に入るのを防ぐ)。
    """
    if not isinstance(code, str):
        return ""
    text = unicodedata.normalize("NFKC", code).upper()
    text = "".join(ch for ch in text if ch.isalnum())
    return text[:16]


def normalize_display_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", name or "").strip()
    text = " ".join(text.split())
    return text


def name_key(name: str) -> str:
    """同一人物判定に使うキー (大文字小文字・空白を無視)。"""
    return normalize_display_name(name).casefold().replace(" ", "")


def generate_recovery_code() -> str:
    return "-".join(
        "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(4)) for _ in range(2)
    )


def hash_ip(ip: str, secret_key: str) -> str:
    """個人情報を残さないための、鍵付き IP ハッシュ (先頭 16 桁)。"""
    if not ip:
        return ""
    return hmac.new(
        secret_key.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:16]
