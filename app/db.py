"""データベース接続。

SQLite (ローカル / 単一サーバ) と PostgreSQL (Neon, Supabase など無料枠) の
どちらでもそのまま動くようにしてある。``DATABASE_URL`` を差し替えるだけで
移行できるので、特定サービスに縛られない。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base


def _normalize_url(url: str) -> str:
    # Heroku 系 / 一部サービスが出す postgres:// を SQLAlchemy 用に直す
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def build_engine(url: str | None = None):
    settings = get_settings()
    url = _normalize_url(url or settings.database_url)
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        # SQLite ファイルの置き場所を作っておく
        if ":memory:" not in url:
            path = url.split("///", 1)[-1]
            if path and path != ":memory:":
                Path(path).expanduser().resolve().parent.mkdir(
                    parents=True, exist_ok=True
                )
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragma(dbapi_connection, _record):  # pragma: no cover - 接続時
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()

    return engine


_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False
        )
    return _SessionLocal


def init_db() -> None:
    """テーブルを作成する (存在すれば何もしない)。"""
    Base.metadata.create_all(bind=get_engine())


def reset_engine() -> None:
    """テスト用: エンジンを作り直す。"""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_db() -> Iterator[Session]:
    """FastAPI 依存性注入用。"""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def database_kind() -> str:
    url = _normalize_url(os.getenv("DATABASE_URL", get_settings().database_url))
    return "postgresql" if url.startswith("postgresql") else "sqlite"
