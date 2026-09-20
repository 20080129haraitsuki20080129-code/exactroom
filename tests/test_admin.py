"""管理画面 (運営者専用) のテスト。

守るべき約束:
  1. ``ADMIN_SECRET`` が無ければ、管理画面は「存在しない」(すべて 404)。
  2. パスワード無しでは何も見えない。
  3. 模範解答は絶対に出てこない。
"""

from __future__ import annotations

import pytest

ADMIN_SECRET = "admin-password-for-tests-0123456789"
MODEL_ANSWER = r"\frac{-1+\sqrt{17}}{4}"


@pytest.fixture()
def extra_env() -> dict:
    """このモジュールのテストでは管理画面を有効にする。"""
    return {"ADMIN_SECRET": ADMIN_SECRET}


@pytest.fixture()
def admin_token(app_client):
    response = app_client.post("/api/admin/login", json={"secret": ADMIN_SECRET})
    assert response.status_code == 200, response.text
    return response.json()["admin_token"]


@pytest.fixture()
def admin(app_client, admin_token):
    from tests.conftest import Actor

    return Actor(app_client, admin_token)


# --------------------------------------------------------------------------
# 既定では存在しない
# --------------------------------------------------------------------------


class TestDisabledByDefault:
    """``ADMIN_SECRET`` を設定していない状態のテスト。"""

    @pytest.fixture()
    def extra_env(self) -> dict:
        return {}

    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/api/admin/login"),
            ("get", "/api/admin/rooms"),
            ("get", "/api/admin/rooms/ABC123"),
            ("get", "/admin"),
            ("get", "/admin.html"),
        ],
    )
    def test_everything_is_404(self, app_client, method, path):
        """設定していなければ、管理画面の存在すら分からないようにする。"""
        kwargs = {"json": {"secret": "whatever"}} if method == "post" else {}
        response = getattr(app_client, method)(path, **kwargs)
        assert response.status_code == 404

    def test_short_secret_does_not_enable_it(self, tmp_path, monkeypatch):
        """短すぎる秘密は「未設定」として扱い、有効にしない。"""
        from app import config

        monkeypatch.setenv("ADMIN_SECRET", "short")
        config.get_settings.cache_clear()
        try:
            with pytest.warns(RuntimeWarning, match="短すぎる"):
                settings = config.get_settings()
            assert settings.admin_enabled is False
        finally:
            config.get_settings.cache_clear()


# --------------------------------------------------------------------------
# 認証
# --------------------------------------------------------------------------


def test_login_with_the_right_password(app_client):
    response = app_client.post("/api/admin/login", json={"secret": ADMIN_SECRET})
    assert response.status_code == 200
    assert response.json()["admin_token"]


def test_login_with_a_wrong_password_is_rejected(app_client):
    response = app_client.post("/api/admin/login", json={"secret": "wrong-password-xx"})
    assert response.status_code == 401
    assert "admin_token" not in response.text


@pytest.mark.parametrize("path", ["/api/admin/rooms", "/api/admin/rooms/ABC123"])
def test_no_token_means_no_access(app_client, path):
    assert app_client.get(path).status_code == 401


def test_a_host_token_cannot_read_the_admin_api(app_client, host):
    """出題者トークンで管理 API を覗けないこと。"""
    response = host.get("/api/admin/rooms")
    assert response.status_code == 403


def test_a_solver_token_cannot_read_the_admin_api(app_client, solver):
    response = solver.get("/api/admin/rooms")
    assert response.status_code == 403


# --------------------------------------------------------------------------
# 部屋の一覧 (本来の目的: 部屋コードを思い出す)
# --------------------------------------------------------------------------


def test_room_list_shows_the_code_and_counts(admin, room, host, solver):
    created = host.post(
        "/api/host/problems",
        json={"title": "問1", "statement_latex": "解け", "answer_latex": MODEL_ANSWER},
    )
    assert created.status_code == 201, created.text

    rows = admin.get("/api/admin/rooms").json()
    row = next(r for r in rows if r["code"] == room["code"])
    assert row["title"] == "テスト部屋"
    assert row["problem_count"] == 1
    assert row["published_problem_count"] == 1
    assert row["participant_count"] == 1
    assert row["is_open"] is True


def test_room_list_counts_unpublished_problems_separately(admin, room, host):
    host.post(
        "/api/host/problems",
        json={"title": "下書き", "answer_latex": "1", "is_published": False},
    )
    rows = admin.get("/api/admin/rooms").json()
    row = next(r for r in rows if r["code"] == room["code"])
    assert row["problem_count"] == 1
    assert row["published_problem_count"] == 0


def test_room_detail_lists_problem_titles_and_participants(admin, room, host, solver):
    host.post(
        "/api/host/problems",
        json={"title": "問1", "answer_latex": MODEL_ANSWER},
    )
    detail = admin.get(f"/api/admin/rooms/{room['code']}").json()
    assert detail["code"] == room["code"]
    assert [p["title"] for p in detail["problems"]] == ["問1"]
    assert [p["display_name"] for p in detail["participants"]] == ["たろう"]


def test_unknown_room_is_404(admin):
    assert admin.get("/api/admin/rooms/NOPE99").status_code == 404


# --------------------------------------------------------------------------
# 模範解答が出てこないこと (このアプリの最重要の約束)
# --------------------------------------------------------------------------


def test_the_admin_api_never_reveals_the_model_answer(admin, room, host, solver):
    host.post(
        "/api/host/problems",
        json={
            "title": "問1",
            "statement_latex": "解け",
            "answer_latex": MODEL_ANSWER,
        },
    )
    solver.post(
        "/api/solve/submissions",
        json={"problem_id": 1, "answer_latex": MODEL_ANSWER},
    )

    for path in ("/api/admin/rooms", f"/api/admin/rooms/{room['code']}"):
        body = admin.get(path).text
        assert MODEL_ANSWER not in body
        assert "answer_latex" not in body
        assert "sqrt" not in body


def test_the_admin_api_never_reveals_secret_hashes(admin, room, host):
    for path in ("/api/admin/rooms", f"/api/admin/rooms/{room['code']}"):
        body = admin.get(path).text
        assert "secret_hash" not in body
        assert "recovery_hash" not in body
        assert "ip_hash" not in body
        assert "super-secret-key" not in body
