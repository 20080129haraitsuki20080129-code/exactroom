"""API の一連の流れ (部屋作成 → 出題 → 参加 → 提出 → 確認) のテスト。"""

from __future__ import annotations

import pytest


def make_problem(host, **overrides):
    payload = {
        "title": "問1",
        "statement_latex": r"x^2-1 を因数分解せよ。",
        "answer_latex": r"(x-1)(x+1)",
        "parse_options": {"euler_e": True, "imaginary_i": True, "log_base": "e",
                          "assume_real": True, "assume_positive": False},
        "ordered_list": False,
        "is_published": True,
    }
    payload.update(overrides)
    response = host.post("/api/host/problems", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_room_creation_and_public_info(app_client, room):
    assert len(room["code"]) == 6
    response = app_client.get(f"/api/rooms/{room['code']}")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "テスト部屋"
    assert body["problem_count"] == 0
    assert "secret" not in response.text


def test_room_code_is_case_insensitive(app_client, room):
    assert app_client.get(f"/api/rooms/{room['code'].lower()}").status_code == 200
    assert app_client.get(f"/api/rooms/{room['code']} ").status_code == 200


def test_host_login(app_client, room):
    ok = app_client.post(
        f"/api/rooms/{room['code']}/host/login", json={"secret": "super-secret-key"}
    )
    assert ok.status_code == 200
    assert ok.json()["host_token"]

    ng = app_client.post(
        f"/api/rooms/{room['code']}/host/login", json={"secret": "wrong-key"}
    )
    assert ng.status_code == 401


def test_join_returns_recovery_code_once(app_client, room):
    first = app_client.post(
        f"/api/rooms/{room['code']}/join", json={"display_name": "はなこ"}
    )
    assert first.status_code == 200
    assert first.json()["recovery_code"]

    again = app_client.post(
        f"/api/rooms/{room['code']}/join", json={"display_name": "はなこ"}
    )
    assert again.status_code == 200
    assert again.json()["recovery_code"] is None
    assert again.json()["participant_id"] == first.json()["participant_id"]


def test_rejoin_policy_code(app_client, room, host):
    first = app_client.post(
        f"/api/rooms/{room['code']}/join", json={"display_name": "じろう"}
    ).json()
    host.patch("/api/host/settings", json={"rejoin_policy": "code"})

    blocked = app_client.post(
        f"/api/rooms/{room['code']}/join", json={"display_name": "じろう"}
    )
    assert blocked.status_code == 401

    allowed = app_client.post(
        f"/api/rooms/{room['code']}/join",
        json={"display_name": "じろう", "recovery_code": first["recovery_code"]},
    )
    assert allowed.status_code == 200


def test_full_flow(app_client, host, solver):
    problem = make_problem(host)

    listing = solver.get("/api/solve/problems")
    assert listing.status_code == 200
    problems = listing.json()
    assert len(problems) == 1
    assert problems[0]["title"] == "問1"
    assert "answer_latex" not in problems[0]

    ac = solver.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": r"x^2-1"},
    )
    assert ac.status_code == 201, ac.text
    assert ac.json()["verdict"] == "AC"

    host.patch("/api/host/settings", json={"submission_cooldown_sec": 0})

    wa = solver.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": r"x^2+1"},
    )
    assert wa.json()["verdict"] == "WA"

    pending = solver.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": r"\text{わかりません}"},
    )
    assert pending.json()["verdict"] == "PENDING"

    mine = solver.get("/api/solve/submissions").json()
    assert len(mine) == 3

    host_view = host.get("/api/host/submissions").json()
    assert len(host_view) == 3
    row = host_view[-1]
    assert row["participant_name"] == "たろう"
    assert row["answer_latex"] == r"x^2-1"
    assert row["verdict"] == "AC"
    assert row["created_at"]

    participants = host.get("/api/host/participants").json()
    assert participants[0]["display_name"] == "たろう"
    assert participants[0]["submission_count"] == 3
    assert participants[0]["ac_count"] == 1

    csv_response = host.get("/api/host/submissions.csv")
    assert csv_response.status_code == 200
    assert "たろう" in csv_response.text
    assert "x^2-1" in csv_response.text


def test_unpublished_problem_is_hidden(app_client, host, solver):
    problem = make_problem(host, is_published=False)
    assert solver.get("/api/solve/problems").json() == []
    blocked = solver.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": "1"},
    )
    assert blocked.status_code == 404


def test_problem_update_and_delete(host):
    problem = make_problem(host)
    updated = host.patch(
        f"/api/host/problems/{problem['id']}", json={"answer_latex": r"x^2-1"}
    )
    assert updated.status_code == 200
    detail = host.get(f"/api/host/problems/{problem['id']}").json()
    assert detail["answer_latex"] == r"x^2-1"

    assert host.delete(f"/api/host/problems/{problem['id']}").status_code == 204
    assert host.get(f"/api/host/problems/{problem['id']}").status_code == 404


def test_invalid_model_answer_rejected_at_registration(host):
    response = host.post(
        "/api/host/problems",
        json={"title": "x", "answer_latex": r"\text{これは数式ではない}"},
    )
    assert response.status_code == 400
    assert "模範解答" in response.json()["detail"]


def test_answer_check_and_self_test(host):
    check = host.post(
        "/api/host/answer-check", json={"answer_latex": r"\frac{1}{2}"}
    ).json()
    assert check["ok"] is True
    assert check["kind"] == "expr"
    assert check["normalized"] == "1/2"

    bad = host.post("/api/host/answer-check", json={"answer_latex": r"\zzz"}).json()
    assert bad["ok"] is False

    problem = make_problem(host)
    result = host.post(
        "/api/host/self-test",
        json={"problem_id": problem["id"], "candidate_latex": r"x^2-1"},
    ).json()
    assert result["verdict"] == "AC"


def test_closed_room_blocks_submission(app_client, host, solver):
    problem = make_problem(host)
    host.patch("/api/host/settings", json={"is_open": False})
    response = solver.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": "1"},
    )
    assert response.status_code == 403


def test_submission_limit(app_client, host, solver):
    problem = make_problem(host)
    host.patch(
        "/api/host/settings",
        json={"max_submissions_per_problem": 2, "submission_cooldown_sec": 0},
    )
    for _ in range(2):
        assert (
            solver.post(
                "/api/solve/submissions",
                json={"problem_id": problem["id"], "answer_latex": "1"},
            ).status_code
            == 201
        )
    blocked = solver.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": "1"},
    )
    assert blocked.status_code == 429


def test_cooldown(app_client, host, solver):
    problem = make_problem(host)
    host.patch("/api/host/settings", json={"submission_cooldown_sec": 60})
    first = solver.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": "1"},
    )
    assert first.status_code == 201
    second = solver.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": "2"},
    )
    assert second.status_code == 429


def test_healthz(app_client):
    assert app_client.get("/healthz").json() == {"status": "ok"}


def test_host_can_reissue_recovery_code(app_client, host, room):
    """復帰コードを控え損ねた解答者を出題者が救済できること。"""
    joined = app_client.post(
        f"/api/rooms/{room['code']}/join", json={"display_name": "さぶろう"}
    ).json()
    host.patch("/api/host/settings", json={"rejoin_policy": "code"})

    # 古いコードでは入れる
    assert (
        app_client.post(
            f"/api/rooms/{room['code']}/join",
            json={"display_name": "さぶろう", "recovery_code": joined["recovery_code"]},
        ).status_code
        == 200
    )

    reissued = host.post(
        f"/api/host/participants/{joined['participant_id']}/recovery-code", json={}
    )
    assert reissued.status_code == 200, reissued.text
    new_code = reissued.json()["recovery_code"]
    assert new_code and new_code != joined["recovery_code"]

    # 古いコードは無効になり、新しいコードで入れる
    assert (
        app_client.post(
            f"/api/rooms/{room['code']}/join",
            json={"display_name": "さぶろう", "recovery_code": joined["recovery_code"]},
        ).status_code
        == 401
    )
    assert (
        app_client.post(
            f"/api/rooms/{room['code']}/join",
            json={"display_name": "さぶろう", "recovery_code": new_code},
        ).status_code
        == 200
    )


def test_recovery_reset_requires_host_and_same_room(app_client, host, room, solver):
    joined = app_client.post(
        f"/api/rooms/{room['code']}/join", json={"display_name": "しろう"}
    ).json()
    assert (
        solver.post(
            f"/api/host/participants/{joined['participant_id']}/recovery-code", json={}
        ).status_code
        == 403
    )

    other = app_client.post(
        "/api/rooms", json={"title": "別室", "secret": "another-secret-key"}
    ).json()
    from tests.conftest import Actor

    intruder = Actor(app_client, other["host_token"])
    assert (
        intruder.post(
            f"/api/host/participants/{joined['participant_id']}/recovery-code", json={}
        ).status_code
        == 404
    )


def test_input_limits_come_from_settings():
    """MAX_ANSWER_CHARS などがスキーマに実際に反映されること。"""
    from app.schemas import LIMITS, SubmissionCreate

    metadata = SubmissionCreate.model_fields["answer_latex"].metadata
    max_lengths = [m.max_length for m in metadata if hasattr(m, "max_length")]
    assert max_lengths == [LIMITS["answer"]]


@pytest.mark.parametrize(
    "path",
    ["/", "/index.html", "/solve", "/solve.html", "/host", "/host.html",
     "/static/index.html", "/static/solve.html", "/static/host.html",
     "/static/css/app.css", "/static/js/home.js",
     "/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png",
     "/robots.txt", "/manifest.json"],
)
def test_pages_are_served_under_both_layouts(app_client, path):
    """同一オリジン配信でも、静的ホスティング配下の相対リンクでも開けること。"""
    response = app_client.get(path)
    assert response.status_code == 200, path


def test_submission_limit_survives_concurrent_requests(app_client, host, solver):
    """同時提出で提出回数の上限を突破できないこと。

    以前は「数えてから挿入する」構造だったため、同時に 10 件投げると
    上限 3 でも 10 件すべて受理されていた。
    """
    import threading

    problem = make_problem(host)
    host.patch(
        "/api/host/settings",
        json={"max_submissions_per_problem": 3, "submission_cooldown_sec": 0},
    )

    codes: list[int] = []
    lock = threading.Lock()

    def submit(index: int) -> None:
        response = solver.post(
            "/api/solve/submissions",
            json={"problem_id": problem["id"], "answer_latex": str(index)},
        )
        with lock:
            codes.append(response.status_code)

    threads = [threading.Thread(target=submit, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert codes.count(201) == 3, codes
    stored = host.get("/api/host/submissions").json()
    assert len(stored) == 3


def test_allow_new_participants_can_be_closed(app_client, host, room):
    """新規参加を締め切れること (別名で提出上限をリセットされるのを防ぐ)。"""
    app_client.post(f"/api/rooms/{room['code']}/join", json={"display_name": "先客"})
    host.patch("/api/host/settings", json={"allow_new_participants": False})

    blocked = app_client.post(
        f"/api/rooms/{room['code']}/join", json={"display_name": "あとから来た人"}
    )
    assert blocked.status_code == 403
    assert "締め切" in blocked.json()["detail"]

    # すでに参加した名前なら入り直せる
    assert (
        app_client.post(
            f"/api/rooms/{room['code']}/join", json={"display_name": "先客"}
        ).status_code
        == 200
    )

    host.patch("/api/host/settings", json={"allow_new_participants": True})
    assert (
        app_client.post(
            f"/api/rooms/{room['code']}/join", json={"display_name": "あとから来た人"}
        ).status_code
        == 200
    )


def test_submission_paging(app_client, host, solver):
    """古い提出まで辿れること / since_id に取りこぼしが無いこと。"""
    problem = make_problem(host)
    host.patch("/api/host/settings", json={"submission_cooldown_sec": 0})
    for i in range(6):
        solver.post(
            "/api/solve/submissions",
            json={"problem_id": problem["id"], "answer_latex": str(i)},
        )

    page1 = host.get("/api/host/submissions?limit=4").json()
    ids1 = [row["id"] for row in page1]
    assert len(ids1) == 4
    assert ids1 == sorted(ids1, reverse=True), "既定は新しい順"

    page2 = host.get(f"/api/host/submissions?limit=4&before_id={ids1[-1]}").json()
    ids2 = [row["id"] for row in page2]
    assert ids2, "古い提出を辿れない"
    assert max(ids2) < min(ids1)

    ascending = [
        row["id"] for row in host.get("/api/host/submissions?since_id=2&limit=3").json()
    ]
    assert ascending == sorted(ascending), "since_id は古い順でないと取りこぼす"
    assert all(i > 2 for i in ascending)


def test_light_migration_adds_missing_columns(tmp_path, monkeypatch):
    """後から足した列を既存 DB に追加できること (Alembic を使わない運用)。"""
    import sqlalchemy as sa

    from app import config
    from app import db as db_module

    db_path = tmp_path / "legacy.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    config.get_settings.cache_clear()
    db_module.reset_engine()

    engine = db_module.get_engine()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """CREATE TABLE rooms (
                    id INTEGER PRIMARY KEY, code VARCHAR(16), title VARCHAR(120),
                    secret_hash VARCHAR(255), is_open BOOLEAN, rejoin_policy VARCHAR(8),
                    max_submissions_per_problem INTEGER, submission_cooldown_sec INTEGER,
                    created_at TIMESTAMP, updated_at TIMESTAMP, closed_at TIMESTAMP)"""
            )
        )
        connection.execute(
            sa.text("INSERT INTO rooms (id, code, title) VALUES (1, 'OLDROM', '旧部屋')")
        )

    db_module.init_db()

    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(sa.text("PRAGMA table_info(rooms)"))}
        assert "allow_new_participants" in columns
        value = connection.execute(
            sa.text("SELECT allow_new_participants FROM rooms WHERE id = 1")
        ).scalar()
        assert value in (1, True)

    db_module.reset_engine()
    config.get_settings.cache_clear()


@pytest.mark.parametrize("path", ["/static/js/home.js", "/static/css/app.css", "/", "/solve"])
def test_static_assets_are_revalidated(app_client, path):
    """更新後に「古い JS + 新しい HTML」にならないよう、毎回 ETag で確認させる。"""
    response = app_client.get(path)
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-cache", path
