"""pytest 共通設定。

テストではジャッジをインラインで動かし (プロセス起動コストを避ける)、
DB はテストごとに使い捨ての SQLite ファイルを使う。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 開発機に .env があってもテストに影響させない
os.environ["EXACTROOM_ENV_FILE"] = ""
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only-000000")
os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault("JUDGE_ISOLATION", "inline")
os.environ.setdefault("JUDGE_TIMEOUT_SEC", "20")
os.environ.pop("ROOM_CREATION_TOKEN", None)
os.environ.pop("DATABASE_URL", None)


#: PostgreSQL でも同じテストを回したいときに指定する。
#:   TEST_DATABASE_URL=postgresql://user@host/db pytest
#: 指定が無ければテストごとに使い捨ての SQLite ファイルを使う。
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture()
def extra_env() -> dict:
    """アプリ生成前に足したい環境変数。

    テストモジュール側でこのフィクスチャを上書きすると、
    ``app_client`` がその設定でアプリを組み立てる
    (例: 管理画面を有効にする ``ADMIN_SECRET``)。
    """
    return {}


@pytest.fixture()
def app_client(tmp_path, monkeypatch, extra_env):
    from fastapi.testclient import TestClient

    if TEST_DATABASE_URL:
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    else:
        db_path = tmp_path / "test.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    for key, value in extra_env.items():
        monkeypatch.setenv(key, value)

    from app import config
    from app import db as db_module
    from app.mathjudge import runner
    from app.ratelimit import limiter

    config.get_settings.cache_clear()
    db_module.reset_engine()
    runner.reset_runner()
    limiter.reset()

    if TEST_DATABASE_URL:
        # 共有 DB なので、テストごとにテーブルを作り直して独立させる
        from app.models import Base

        Base.metadata.drop_all(bind=db_module.get_engine())
        db_module.reset_engine()

    from app.main import create_app

    application = create_app()
    with TestClient(application) as client:
        yield client

    db_module.reset_engine()
    config.get_settings.cache_clear()
    limiter.reset()


class Actor:
    """テスト用の簡易クライアントラッパー。"""

    def __init__(self, client, token: str):
        self.client = client
        self.token = token

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, url, **kw):
        return self.client.get(url, headers=self.headers, **kw)

    def post(self, url, **kw):
        return self.client.post(url, headers=self.headers, **kw)

    def patch(self, url, **kw):
        return self.client.patch(url, headers=self.headers, **kw)

    def delete(self, url, **kw):
        return self.client.delete(url, headers=self.headers, **kw)


@pytest.fixture()
def room(app_client):
    response = app_client.post(
        "/api/rooms", json={"title": "テスト部屋", "secret": "super-secret-key"}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def host(app_client, room):
    return Actor(app_client, room["host_token"])


@pytest.fixture()
def solver(app_client, room):
    response = app_client.post(
        f"/api/rooms/{room['code']}/join", json={"display_name": "たろう"}
    )
    assert response.status_code == 200, response.text
    return Actor(app_client, response.json()["token"])
