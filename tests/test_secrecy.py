"""模範解答が解答者側に漏れないことの検証。

このテストがこのプロジェクトの最重要要件を守っている。
"""

from __future__ import annotations

import json

SECRET_ANSWER = r"\frac{-1+\sqrt{17}}{4}"
SECRET_FRAGMENTS = [SECRET_ANSWER, r"\sqrt{17}", "17", "-1+", r"\frac{-1+\sqrt{17}}{4}"]


def _make_secret_problem(host):
    response = host.post(
        "/api/host/problems",
        json={
            "title": "秘密の問題",
            "statement_latex": r"2x^2+x-2=0 の正の解を求めよ。",
            "answer_latex": SECRET_ANSWER,
            "is_published": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _assert_no_leak(text: str) -> None:
    assert SECRET_ANSWER not in text
    assert r"\sqrt{17}" not in text
    assert "sqrt(17)" not in text


def test_problem_list_does_not_contain_answer(host, solver):
    _make_secret_problem(host)
    response = solver.get("/api/solve/problems")
    _assert_no_leak(response.text)
    for item in response.json():
        assert "answer_latex" not in item
        assert "answer" not in json.dumps(item)


def test_submission_response_does_not_contain_answer(app_client, host, solver):
    problem = _make_secret_problem(host)
    host.patch("/api/host/settings", json={"submission_cooldown_sec": 0})
    for candidate in [
        r"\frac{-1+\sqrt{17}}{4}",   # 正解
        r"0",                        # 不正解
        r"\text{こわす}",             # パースエラー
        r"\sqrt{x}",                 # 変数を含む
        r"\frac{1}{0}",              # ゼロ除算
        "x" * 400,                   # 長い入力
    ]:
        response = solver.post(
            "/api/solve/submissions",
            json={"problem_id": problem["id"], "answer_latex": candidate},
        )
        _assert_no_leak(response.text)
        body = response.json()
        if response.status_code == 201:
            assert set(body) <= {
                "submission_id",
                "problem_id",
                "status",
                "verdict",
                "message",
                "created_at",
                "remaining_submissions",
            }
            # 内部の判定理由も返さない
            assert "reason" not in body
            assert "detail" not in body


def test_my_submissions_only_contain_own_answers(app_client, host, room):
    problem = _make_secret_problem(host)
    host.patch("/api/host/settings", json={"submission_cooldown_sec": 0})

    from tests.conftest import Actor

    a = Actor(
        app_client,
        app_client.post(
            f"/api/rooms/{room['code']}/join", json={"display_name": "A"}
        ).json()["token"],
    )
    b = Actor(
        app_client,
        app_client.post(
            f"/api/rooms/{room['code']}/join", json={"display_name": "B"}
        ).json()["token"],
    )
    a.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": r"A\cdot x"},
    )
    b.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": r"B\cdot x"},
    )
    a_view = a.get("/api/solve/submissions").text
    assert "A" in a_view
    assert r"B\cdot x" not in a_view
    _assert_no_leak(a_view)


def test_error_paths_do_not_leak(app_client, host, solver):
    """壊れた模範解答でも、解答者に中身を見せない。"""
    response = host.post(
        "/api/host/problems",
        json={"title": "ok", "answer_latex": r"\frac{1}{2}", "is_published": True},
    )
    problem = response.json()

    # 出題者だけが使える経路で、模範解答をパースできない状態に差し替える
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Problem

    with session_scope() as session:
        row = session.execute(
            select(Problem).where(Problem.id == problem["id"])
        ).scalar_one()
        row.answer_latex = r"\text{壊れた秘密の模範解答}"

    result = solver.post(
        "/api/solve/submissions",
        json={"problem_id": problem["id"], "answer_latex": "1"},
    )
    assert result.status_code == 201
    assert result.json()["status"] == "problem_error"
    assert result.json()["verdict"] is None
    assert "壊れた秘密" not in result.text


def test_host_endpoints_require_host_token(solver):
    for method, url in [
        ("get", "/api/host/problems"),
        ("get", "/api/host/submissions"),
        ("get", "/api/host/participants"),
        ("get", "/api/host/settings"),
        ("get", "/api/host/submissions.csv"),
    ]:
        response = getattr(solver, method)(url)
        assert response.status_code == 403, url


def test_host_token_of_other_room_cannot_read(app_client, host):
    _make_secret_problem(host)
    other = app_client.post(
        "/api/rooms", json={"title": "別の部屋", "secret": "another-secret-key"}
    ).json()

    from tests.conftest import Actor

    intruder = Actor(app_client, other["host_token"])
    response = intruder.get("/api/host/problems")
    assert response.status_code == 200
    assert response.json() == []
    _assert_no_leak(response.text)


def test_unauthenticated_cannot_read_anything(app_client, host, room):
    _make_secret_problem(host)
    for url in [
        "/api/solve/problems",
        "/api/host/problems",
        "/api/host/submissions",
    ]:
        response = app_client.get(url)
        assert response.status_code in (401, 403)
        _assert_no_leak(response.text)


def test_openapi_schema_has_no_answer_field_for_solvers(app_client):
    schema = app_client.get("/openapi.json")
    if schema.status_code != 200:
        return
    body = schema.json()
    solver_schemas = ["ProblemPublic", "SubmissionResult", "MySubmission"]
    for name in solver_schemas:
        definition = body["components"]["schemas"].get(name, {})
        assert "answer_latex" not in definition.get("properties", {}) or name == "MySubmission"
