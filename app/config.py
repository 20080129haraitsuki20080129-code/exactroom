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
    #: 同時アクセスに耐えるための接続プール。
    #: 既定 (5+10) のままだと 100 人規模で枯渇し、30 秒待たされた末に失敗する。
    #: pool_size + max_overflow が、同時に処理できるリクエスト数の上限になる。
    db_pool_size: int = 20
    db_max_overflow: int = 20
    #: 接続が空くのを待つ時間。長いと利用者がただ待たされるので短くする。
    db_pool_timeout: int = 10

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

    # ---- 管理画面 (運営者だけが使う。全部屋のコードが見える) ----
    #
    # ★ 空のときは管理画面を「存在しないもの」として扱う (すべて 404)。
    #   既定で無効なので、設定しないかぎり誰にも見えない。
    #   公開 URL は誰でも叩けるため、短い秘密は許さない。
    admin_secret: str = ""
    admin_min_secret_chars: int = 16
    admin_session_ttl_hours: int = 6

    # ---- セッション ----
    session_ttl_hours: int = 12
    host_session_ttl_hours: int = 12

    # ---- レート制限 (メモリ内。単一プロセス前提) ----
    #
    # ★ 教室では全員が学校の NAT 越しで「同じ IP」に見える。
    #   IP あたりの上限を小さくすると、クラスの後半の生徒が参加できなくなる。
    #   そのため IP 単位の上限はクラス規模を見込んで大きめにし、
    #   乱用の抑止は参加者単位の仕組み (提出クールダウン・1 問あたりの提出上限)
    #   と、出題者による「新規参加の締め切り」で行う。
    rate_limit_enabled: bool = True
    rate_join_per_minute: int = 120
    rate_host_login_per_minute: int = 15
    rate_submit_per_minute: int = 300
    rate_create_room_per_hour: int = 30
    rate_admin_login_per_minute: int = 10
    rate_general_per_minute: int = 900
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

    @property
    def admin_enabled(self) -> bool:
        """管理画面を有効にしてよいか。

        短すぎる秘密は「設定されていない」ものとして扱う。中途半端な
        秘密で全部屋のコードを守るより、無効のままのほうが安全なため。
        """
        return len(self.admin_secret) >= self.admin_min_secret_chars


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
    if settings.admin_secret and not settings.admin_enabled:
        # 起動そのものは止めない。授業中に管理画面の設定ミスで
        # 全員が使えなくなるより、管理画面を閉じたままにするほうがよい。
        warnings.warn(
            f"ADMIN_SECRET が短すぎるため管理画面を無効のままにしました "
            f"({settings.admin_min_secret_chars} 文字以上にしてください)。",
            RuntimeWarning,
            stacklevel=2,
        )
    return settings
