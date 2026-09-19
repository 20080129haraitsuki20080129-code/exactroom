"""環境変数による設定。

変数名はフィールド名と同じ (大文字小文字は区別しない)。
``.env`` ファイルからも読み込む。``.env.example`` も参照。
"""

from __future__ import annotations

import os
import secrets
import warnings
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 読み込む .env ファイル。``EXACTROOM_ENV_FILE=""`` で無効化できる。
#: (テストや CI で、開発機の .env に引きずられないようにするため)
ENV_FILE = os.getenv("EXACTROOM_ENV_FILE", ".env") or None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    # ---- 基本 ----
    app_name: str = "ExactRoom"
    environment: str = Field(default="development")  # development | production
    #: 署名鍵。本番では必ず設定すること (未設定なら起動時に警告し、毎回変わる)
    secret_key: str = Field(default="")
    base_url: str = ""

    # ---- データベース ----
    #: 例) sqlite:///./data/exactroom.db
    #:     postgresql+psycopg://user:pass@host/dbname
    database_url: str = "sqlite:///./data/exactroom.db"

    # ---- CORS ----
    #: フロントを別ホスト (GitHub Pages など) に置く場合に設定
    cors_origins: str = ""

    # ---- 判定エンジン ----
    judge_timeout_sec: float = 8.0
    judge_workers: int = 2
    judge_isolation: str = "process"  # process | inline

    # ---- 入力制限 ----
    max_answer_chars: int = 500
    max_statement_chars: int = 2000
    max_title_chars: int = 120
    max_name_chars: int = 24
    max_problems_per_room: int = 200

    # ---- 部屋 ----
    room_code_length: int = 6
    #: 出題者が自分で部屋コードを決める場合の最短の長さ。
    #: 短いほど推測されやすいが、"101" のような教室番号を使えるようにしてある。
    room_code_min_length: int = 3
    #: 空でなければ、部屋作成時にこの合言葉が必要 (公開サーバの荒らし対策)
    room_creation_token: str = ""
    allow_room_creation: bool = True
    min_secret_chars: int = 8

    # ---- セッション ----
    session_ttl_hours: int = 12
    host_session_ttl_hours: int = 12

    # ---- レート制限 (メモリ内。単一プロセス前提) ----
    rate_limit_enabled: bool = True
    rate_join_per_minute: int = 20
    rate_host_login_per_minute: int = 10
    rate_submit_per_minute: int = 30
    rate_create_room_per_hour: int = 10
    rate_general_per_minute: int = 240
    submission_cooldown_sec: int = 3

    # ---- リバースプロキシ ----
    trust_proxy_headers: bool = False

    # ---- CSP で許可する CDN (MathLive / KaTeX) ----
    cdn_hosts: str = "https://cdn.jsdelivr.net https://unpkg.com"
    #: 静的ファイルを同一オリジンで配信するか
    serve_static: bool = True

    @field_validator("environment")
    @classmethod
    def _env(cls, v: str) -> str:
        v = (v or "development").lower()
        return v if v in ("development", "production", "test") else "development"

    @field_validator("judge_isolation")
    @classmethod
    def _iso(cls, v: str) -> str:
        v = (v or "process").lower()
        return v if v in ("process", "inline") else "process"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def cdn_host_list(self) -> list[str]:
        return [h.strip() for h in self.cdn_hosts.split() if h.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.secret_key:
        if settings.is_production:
            raise RuntimeError(
                "SECRET_KEY が設定されていません。本番環境では必須です。"
            )
        settings.secret_key = secrets.token_urlsafe(48)
        warnings.warn(
            "SECRET_KEY 未設定のため一時鍵を生成しました。"
            "再起動するとログイン状態が失われます。",
            RuntimeWarning,
            stacklevel=2,
        )
    return settings
