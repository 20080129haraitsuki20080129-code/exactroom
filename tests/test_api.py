"""API の一連の流れ (部屋作成 → 出題 → 参加 → 提出 → 確認) のテスト。"""

from __future__ import annotations

import pytest

ROOT_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent


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
    assert pending.json()["status"] == "input_error"
    assert pending.json()["verdict"] is None

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


def test_problem_error_does_not_consume_attempt(app_client, host, solver, monkeypatch):
    problem = make_problem(host)
    from app.routers import solve as solve_router

    real_judge = solve_router.judge_isolated
    calls = {"count": 0}

    def fail_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "status": "problem_error",
                "verdict": None,
                "reason": "model_parse_error",
                "student_message": "This problem cannot be judged.",
            }
        return real_judge(*args, **kwargs)

    monkeypatch.setattr(solve_router, "judge_isolated", fail_once)
    host.patch(
        "/api/host/settings",
        json={"max_submissions_per_problem": 1, "submission_cooldown_sec": 0},
    )
    unavailable = solver.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": "1"},
    )
    assert unavailable.status_code == 201
    assert unavailable.json()["status"] == "problem_error"
    assert unavailable.json()["verdict"] is None
    assert unavailable.json()["remaining_submissions"] == 1

    invalid_input = solver.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": r"\text{unknown}"},
    )
    assert invalid_input.status_code == 201
    assert invalid_input.json()["status"] == "input_error"
    assert invalid_input.json()["verdict"] is None
    assert invalid_input.json()["remaining_submissions"] == 1

    judged = solver.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": r"x^2-1"},
    )
    assert judged.status_code == 201
    assert judged.json()["status"] == "judged"
    assert judged.json()["verdict"] == "AC"
    assert judged.json()["remaining_submissions"] == 0


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


# --------------------------------------------------------------------------
# 部屋コードを自分で決める / 複数の部屋を同時に使う
# --------------------------------------------------------------------------


def test_create_room_with_chosen_code(app_client):
    """出題者が部屋コードを指定できること。"""
    response = app_client.post(
        "/api/rooms",
        json={"title": "1 組", "secret": "kumi-ichi-secret", "code": "1234"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["code"] == "1234"
    assert app_client.get("/api/rooms/1234").json()["title"] == "1 組"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("sugaku1", "SUGAKU1"),
        (" a-b c1 ", "ABC1"),
        ("ｓｕｕｇａｋｕ", "SUUGAKU"),
    ],
)
def test_chosen_code_is_normalized(app_client, raw, expected):
    """小文字・全角・記号を吸収して同じコードとして扱うこと。"""
    response = app_client.post(
        "/api/rooms", json={"secret": "secret-key-12345", "code": raw}
    )
    assert response.status_code == 201, response.text
    assert response.json()["code"] == expected
    assert app_client.get(f"/api/rooms/{raw}").status_code == 200


def test_chosen_code_must_be_free(app_client):
    app_client.post("/api/rooms", json={"secret": "secret-key-12345", "code": "MATH1"})
    duplicate = app_client.post(
        "/api/rooms", json={"secret": "another-secret-1", "code": "math1"}
    )
    assert duplicate.status_code == 409
    assert "すでに使われて" in duplicate.json()["detail"]


@pytest.mark.parametrize("code", ["12", "A", "---", "！！！"])
def test_chosen_code_is_validated(app_client, code):
    response = app_client.post(
        "/api/rooms", json={"secret": "secret-key-12345", "code": code}
    )
    assert response.status_code == 400, (code, response.text)


def test_many_people_share_one_room(app_client):
    """同じ部屋コードなら何人でも同じ部屋に入れること。"""
    from tests.conftest import Actor

    room = app_client.post(
        "/api/rooms", json={"title": "合同", "secret": "goudou-secret-1", "code": "5000"}
    ).json()
    host = Actor(app_client, room["host_token"])
    problem = make_problem(host)
    host.patch("/api/host/settings", json={"submission_cooldown_sec": 0})

    solvers = []
    for i in range(12):
        joined = app_client.post(
            "/api/rooms/5000/join", json={"display_name": f"生徒{i:02d}"}
        )
        assert joined.status_code == 200, joined.text
        solvers.append(Actor(app_client, joined.json()["token"]))

    for i, solver in enumerate(solvers):
        answer = r"x^2-1" if i % 2 == 0 else r"x^2+1"
        result = solver.post(
            "/api/solve/submissions",
            json={"problem_id": problem["id"], "answer_latex": answer},
        )
        assert result.status_code == 201, result.text
        assert result.json()["verdict"] == ("AC" if i % 2 == 0 else "WA")

    participants = host.get("/api/host/participants").json()
    assert len(participants) == 12
    submissions = host.get("/api/host/submissions").json()
    assert len(submissions) == 12
    assert sum(1 for s in submissions if s["verdict"] == "AC") == 6


def test_rooms_run_independently_at_the_same_time(app_client):
    """別の部屋コードなら、同時に使っても互いに影響しないこと。"""
    from tests.conftest import Actor

    rooms = {}
    for code, title, answer in [
        ("101", "1 年 1 組", r"(x-1)(x+1)"),
        ("102", "1 年 2 組", r"\sin x"),
    ]:
        created = app_client.post(
            "/api/rooms",
            json={"title": title, "secret": f"secret-for-{code}", "code": code},
        )
        assert created.status_code == 201, created.text
        host = Actor(app_client, created.json()["host_token"])
        problem = make_problem(host, answer_latex=answer, title=f"{title} の問題")
        host.patch("/api/host/settings", json={"submission_cooldown_sec": 0})
        joined = app_client.post(
            f"/api/rooms/{code}/join", json={"display_name": "同姓同名"}
        ).json()
        rooms[code] = {
            "host": host,
            "problem": problem,
            "solver": Actor(app_client, joined["token"]),
        }

    # 同じ名前でも部屋が違えば別人として扱われる
    assert (
        rooms["101"]["solver"].headers["Authorization"]
        != rooms["102"]["solver"].headers["Authorization"]
    )

    # それぞれの部屋の模範解答で判定される
    r1 = rooms["101"]["solver"].post(
        "/api/solve/submissions",
        json={"problem_id": rooms["101"]["problem"]["id"], "answer_latex": r"x^2-1"},
    ).json()
    r2 = rooms["102"]["solver"].post(
        "/api/solve/submissions",
        json={"problem_id": rooms["102"]["problem"]["id"], "answer_latex": r"x^2-1"},
    ).json()
    assert r1["verdict"] == "AC"
    assert r2["verdict"] == "WA"

    # 相手の部屋の問題は見えないし、提出もできない
    for me, other in (("101", "102"), ("102", "101")):
        visible = rooms[me]["solver"].get("/api/solve/problems").json()
        assert [p["id"] for p in visible] == [rooms[me]["problem"]["id"]]
        blocked = rooms[me]["solver"].post(
            "/api/solve/submissions",
            json={"problem_id": rooms[other]["problem"]["id"], "answer_latex": "1"},
        )
        assert blocked.status_code == 404

        # 出題者から見える提出も自分の部屋のものだけ
        mine = rooms[me]["host"].get("/api/host/submissions").json()
        assert len(mine) == 1
        assert mine[0]["problem_id"] == rooms[me]["problem"]["id"]


def test_public_config_tells_client_what_is_required(app_client, monkeypatch):
    """合言葉が要るサーバかどうかを、ログイン前のブラウザが知れること。

    これが無いと入力欄を出せず、403 の理由が利用者に分からない
    (実際に「部屋が作れない」という形で表面化した)。
    """
    response = app_client.get("/api/rooms/-/config")
    assert response.status_code == 200
    config = response.json()
    # 返すのは「画面を組み立てるのに要る事実」だけ。秘密の値そのものは返さない。
    assert set(config) == {
        "allow_room_creation",
        "requires_creation_token",
        "room_code_min_length",
        "min_secret_chars",
        "max_answer_chars",
    }
    assert config["requires_creation_token"] is False
    assert config["allow_room_creation"] is True
    assert config["room_code_min_length"] >= 1
    assert "SECRET_KEY" not in response.text


def test_missing_creation_token_explains_itself(app_client, monkeypatch):
    from app import config as config_module

    monkeypatch.setenv("ROOM_CREATION_TOKEN", "aikotoba-123")
    config_module.get_settings.cache_clear()
    try:
        public = app_client.get("/api/rooms/-/config").json()
        assert public["requires_creation_token"] is True

        blank = app_client.post(
            "/api/rooms", json={"secret": "secret-key-12345", "title": "x"}
        )
        assert blank.status_code == 403
        assert "合言葉" in blank.json()["detail"]
        assert "必要" in blank.json()["detail"]

        wrong = app_client.post(
            "/api/rooms",
            json={"secret": "secret-key-12345", "creation_token": "chigau"},
        )
        assert wrong.status_code == 403
        assert "違います" in wrong.json()["detail"]

        ok = app_client.post(
            "/api/rooms",
            json={"secret": "secret-key-12345", "creation_token": "aikotoba-123"},
        )
        assert ok.status_code == 201
    finally:
        monkeypatch.delenv("ROOM_CREATION_TOKEN", raising=False)
        config_module.get_settings.cache_clear()


# --------------------------------------------------------------------------
# 同時アクセス・混雑時のふるまい
# --------------------------------------------------------------------------


def test_connection_pool_is_large_enough_for_a_classroom():
    """既定のプールが、教室規模の同時アクセスに耐える大きさであること。

    SQLAlchemy の既定 (5+10) のままだと 100 人規模で枯渇し、
    30 秒待たされた末に失敗していた。
    """
    from app.config import get_settings

    settings = get_settings()
    assert settings.db_pool_size + settings.db_max_overflow >= 40
    # 待たされ続けるより、早めに諦めて 503 を返すほうがよい
    assert settings.db_pool_timeout <= 15


def test_pool_exhaustion_returns_503_not_500(tmp_path, monkeypatch):
    """接続が尽きたときに 500 ではなく 503 (混雑中) を返すこと。"""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'busy.db'}")
    monkeypatch.setenv("DB_POOL_SIZE", "2")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "0")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "1")

    from app import config
    from app import db as db_module

    config.get_settings.cache_clear()
    db_module.reset_engine()
    from app.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        created = client.post(
            "/api/rooms", json={"title": "busy", "secret": "secret-key-1234", "code": "BSY"}
        )
        assert created.status_code == 201
        assert client.get("/api/rooms/BSY").status_code == 200

        engine = db_module.get_engine()
        held = [engine.connect() for _ in range(2)]
        try:
            response = client.get("/api/rooms/BSY")
            assert response.status_code == 503, response.status_code
            assert response.headers.get("Retry-After")
            assert response.json()["code"] == "busy"
            assert "混み合" in response.json()["detail"]
        finally:
            for connection in held:
                connection.close()

        assert client.get("/api/rooms/BSY").status_code == 200

    db_module.reset_engine()
    config.get_settings.cache_clear()


def test_many_participants_can_join_and_submit_at_once(app_client, host, room):
    """同時に大勢が参加・提出しても、失敗や取り違えが起きないこと。

    教室では全員が学校の NAT 越しで同じ IP に見えるため、
    IP 単位のレート制限が小さいとクラスの後半が参加できなくなる
    (実際に 30 人中 20 人しか参加できていなかった)。
    """
    import collections
    import threading

    problem = make_problem(host)
    host.patch(
        "/api/host/settings",
        json={"submission_cooldown_sec": 0, "max_submissions_per_problem": 2},
    )

    statuses = collections.Counter()
    lock = threading.Lock()

    def one(index: int) -> None:
        joined = app_client.post(
            f"/api/rooms/{room['code']}/join", json={"display_name": f"生徒{index:03d}"}
        )
        with lock:
            statuses[("join", joined.status_code)] += 1
        if joined.status_code != 200:
            return
        headers = {"Authorization": f"Bearer {joined.json()['token']}"}
        for _ in range(3):  # 上限 2 を超えて投げる
            response = app_client.post(
                "/api/solve/submissions",
                headers=headers,
                json={"problem_id": problem["id"], "answer_latex": r"x^2-1"},
            )
            with lock:
                statuses[("submit", response.status_code)] += 1

    threads = [threading.Thread(target=one, args=(i,)) for i in range(30)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses[("join", 200)] == 30
    assert statuses[("submit", 201)] == 60  # 30 人 x 上限 2
    assert statuses[("submit", 429)] == 30
    assert not [k for k in statuses if k[1] >= 500], dict(statuses)

    participants = host.get("/api/host/participants").json()
    assert len(participants) == 30
    assert all(p["submission_count"] == 2 for p in participants)


def test_cli_export_matches_web_csv(app_client, host, solver, tmp_path, monkeypatch):
    """scripts/admin.py の CSV が、画面からの CSV と同じ内容になること。

    CLI 側だけタイムゾーンを落としていて、時刻が 9 時間ずれて読めていた。
    """
    import csv
    import io
    import subprocess
    import sys

    problem = make_problem(host)
    solver.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": r"x^2-1"},
    )

    web = host.get("/api/host/submissions.csv").text.lstrip("\ufeff")
    web_rows = list(csv.reader(io.StringIO(web)))

    import os

    from app.config import get_settings

    room_code = get_settings() and host.get("/api/host/room").json()["code"]
    env = dict(os.environ)
    env["EXACTROOM_ENV_FILE"] = ""
    result = subprocess.run(
        [sys.executable, "scripts/admin.py", "export", room_code],
        capture_output=True,
        text=True,
        cwd=str(ROOT_DIR),
        env=env,
    )
    assert result.returncode == 0, result.stderr
    cli_rows = list(csv.reader(io.StringIO(result.stdout)))

    assert cli_rows[0] == web_rows[0], "ヘッダが違う"
    assert len(cli_rows) == len(web_rows)
    for cli_row, web_row in zip(cli_rows[1:], web_rows[1:], strict=False):
        assert cli_row[0] == web_row[0], "提出 ID が違う"
        # 同じ瞬間を指していること (表記ゆれは許容しない)
        assert cli_row[1] == web_row[1], f"日時が違う: CLI={cli_row[1]} Web={web_row[1]}"
        assert cli_row[2:] == web_row[2:]
