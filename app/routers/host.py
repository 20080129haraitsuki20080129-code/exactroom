"""出題者向け API。

出題者トークン (部屋コード + 秘密キーで取得) が必要。
模範解答を読み書きできるのはここだけ。
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..deps import HostContext, require_host
from ..mathjudge.errors import MathError
from ..mathjudge.parser import ParseOptions, parse_latex_answer
from ..mathjudge.runner import judge_isolated
from ..models import Participant, Problem, Submission, utcnow
from ..schemas import (
    AnswerCheckRequest,
    AnswerCheckResponse,
    HostParticipant,
    HostProblemDetail,
    HostProblemSummary,
    HostSubmission,
    ProblemCreate,
    ProblemUpdate,
    RoomPublic,
    RoomSettingsUpdate,
    SelfTestRequest,
    SelfTestResponse,
)

router = APIRouter(prefix="/api/host", tags=["host"])


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _problem_stats(db: Session, room_id: int) -> dict[int, tuple[int, int]]:
    rows = db.execute(
        select(
            Submission.problem_id,
            func.count(Submission.id),
            func.sum(case((Submission.verdict == "AC", 1), else_=0)),
        )
        .where(Submission.room_id == room_id)
        .group_by(Submission.problem_id)
    ).all()
    return {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in rows}


def _summary(problem: Problem, stats: dict[int, tuple[int, int]]) -> HostProblemSummary:
    total, ac = stats.get(problem.id, (0, 0))
    return HostProblemSummary(
        id=problem.id,
        order_index=problem.order_index,
        title=problem.title,
        statement_latex=problem.statement_latex,
        statement_note=problem.statement_note,
        is_published=problem.is_published,
        ordered_list=problem.ordered_list,
        parse_options=dict(problem.parse_options or {}),
        submission_count=total,
        ac_count=ac,
        created_at=problem.created_at,
        updated_at=problem.updated_at,
    )


# --------------------------------------------------------------------------
# 部屋
# --------------------------------------------------------------------------


@router.get("/room", response_model=RoomPublic)
def room_info(
    ctx: HostContext = Depends(require_host), db: Session = Depends(get_db)
) -> RoomPublic:
    count = db.execute(
        select(func.count(Problem.id)).where(Problem.room_id == ctx.room.id)
    ).scalar_one()
    return RoomPublic(
        code=ctx.room.code,
        title=ctx.room.title,
        is_open=ctx.room.is_open,
        problem_count=count,
    )


@router.get("/settings")
def get_settings_view(ctx: HostContext = Depends(require_host)) -> dict:
    room = ctx.room
    return {
        "code": room.code,
        "title": room.title,
        "is_open": room.is_open,
        "rejoin_policy": room.rejoin_policy,
        "max_submissions_per_problem": room.max_submissions_per_problem,
        "submission_cooldown_sec": room.submission_cooldown_sec,
    }


@router.patch("/settings")
def update_settings(
    payload: RoomSettingsUpdate,
    ctx: HostContext = Depends(require_host),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    room = ctx.room
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "title" in data:
        room.title = data["title"].strip()[: settings.max_title_chars]
    if "is_open" in data:
        room.is_open = bool(data["is_open"])
        room.closed_at = None if room.is_open else utcnow()
    if "rejoin_policy" in data:
        room.rejoin_policy = data["rejoin_policy"]
    if "max_submissions_per_problem" in data:
        room.max_submissions_per_problem = int(data["max_submissions_per_problem"])
    if "submission_cooldown_sec" in data:
        room.submission_cooldown_sec = int(data["submission_cooldown_sec"])
    db.commit()
    return get_settings_view(ctx)


# --------------------------------------------------------------------------
# 問題
# --------------------------------------------------------------------------


@router.get("/problems", response_model=list[HostProblemSummary])
def list_problems(
    ctx: HostContext = Depends(require_host), db: Session = Depends(get_db)
) -> list[HostProblemSummary]:
    rows = (
        db.execute(
            select(Problem)
            .where(Problem.room_id == ctx.room.id)
            .order_by(Problem.order_index, Problem.id)
        )
        .scalars()
        .all()
    )
    stats = _problem_stats(db, ctx.room.id)
    return [_summary(p, stats) for p in rows]


@router.get("/problems/{problem_id}", response_model=HostProblemDetail)
def get_problem(
    problem_id: int,
    ctx: HostContext = Depends(require_host),
    db: Session = Depends(get_db),
) -> HostProblemDetail:
    problem = db.get(Problem, problem_id)
    if problem is None or problem.room_id != ctx.room.id:
        raise HTTPException(status_code=404, detail="問題が見つかりません。")
    stats = _problem_stats(db, ctx.room.id)
    base = _summary(problem, stats)
    return HostProblemDetail(**base.model_dump(), answer_latex=problem.answer_latex)


def _validate_answer(answer: str, options: dict) -> str:
    """模範解答が確実にパースできることを登録時に確認する。"""
    try:
        parsed = parse_latex_answer(answer, ParseOptions.from_dict(options))
    except MathError as exc:
        raise HTTPException(
            status_code=400, detail=f"模範解答を解釈できません: {exc.message}"
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail="模範解答を解釈できません。"
        ) from exc
    return parsed.kind


@router.post(
    "/problems", response_model=HostProblemSummary, status_code=status.HTTP_201_CREATED
)
def create_problem(
    payload: ProblemCreate,
    ctx: HostContext = Depends(require_host),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HostProblemSummary:
    count = db.execute(
        select(func.count(Problem.id)).where(Problem.room_id == ctx.room.id)
    ).scalar_one()
    if count >= settings.max_problems_per_room:
        raise HTTPException(status_code=400, detail="問題数の上限に達しました。")

    options = payload.parse_options.to_engine()
    _validate_answer(payload.answer_latex, options)

    order_index = payload.order_index
    if not order_index:
        max_order = db.execute(
            select(func.max(Problem.order_index)).where(Problem.room_id == ctx.room.id)
        ).scalar_one()
        order_index = int(max_order or 0) + 1

    problem = Problem(
        room_id=ctx.room.id,
        order_index=order_index,
        title=payload.title.strip(),
        statement_latex=payload.statement_latex.strip(),
        statement_note=payload.statement_note.strip(),
        answer_latex=payload.answer_latex.strip(),
        parse_options=options,
        ordered_list=payload.ordered_list,
        is_published=payload.is_published,
    )
    db.add(problem)
    db.commit()
    db.refresh(problem)
    return _summary(problem, {})


@router.patch("/problems/{problem_id}", response_model=HostProblemSummary)
def update_problem(
    problem_id: int,
    payload: ProblemUpdate,
    ctx: HostContext = Depends(require_host),
    db: Session = Depends(get_db),
) -> HostProblemSummary:
    problem = db.get(Problem, problem_id)
    if problem is None or problem.room_id != ctx.room.id:
        raise HTTPException(status_code=404, detail="問題が見つかりません。")

    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "parse_options" in data:
        problem.parse_options = payload.parse_options.to_engine()
    if "answer_latex" in data:
        _validate_answer(data["answer_latex"], dict(problem.parse_options or {}))
        problem.answer_latex = data["answer_latex"].strip()
    elif "parse_options" in data:
        _validate_answer(problem.answer_latex, dict(problem.parse_options or {}))
    for field in ("title", "statement_latex", "statement_note"):
        if field in data:
            setattr(problem, field, str(data[field]).strip())
    for field in ("ordered_list", "is_published"):
        if field in data:
            setattr(problem, field, bool(data[field]))
    if "order_index" in data:
        problem.order_index = int(data["order_index"])
    db.commit()
    db.refresh(problem)
    stats = _problem_stats(db, ctx.room.id)
    return _summary(problem, stats)


@router.delete("/problems/{problem_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_problem(
    problem_id: int,
    ctx: HostContext = Depends(require_host),
    db: Session = Depends(get_db),
) -> Response:
    problem = db.get(Problem, problem_id)
    if problem is None or problem.room_id != ctx.room.id:
        raise HTTPException(status_code=404, detail="問題が見つかりません。")
    db.delete(problem)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/answer-check", response_model=AnswerCheckResponse)
def answer_check(payload: AnswerCheckRequest,
                 ctx: HostContext = Depends(require_host)) -> AnswerCheckResponse:
    """模範解答の書き方を登録前に試すためのプレビュー。"""
    options = payload.parse_options.to_engine()
    try:
        parsed = parse_latex_answer(payload.answer_latex, ParseOptions.from_dict(options))
    except MathError as exc:
        return AnswerCheckResponse(ok=False, message=exc.message)
    except Exception:
        return AnswerCheckResponse(ok=False, message="解釈できませんでした。")
    import sympy as sp

    try:
        normalized = ", ".join(sp.sstr(item) for item in parsed.items)
    except Exception:  # pragma: no cover - 防御的
        normalized = ""
    return AnswerCheckResponse(
        ok=True,
        kind=parsed.kind,
        normalized=normalized[:400],
        message="解釈できました。",
    )


@router.post("/self-test", response_model=SelfTestResponse)
def self_test(
    payload: SelfTestRequest,
    ctx: HostContext = Depends(require_host),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SelfTestResponse:
    """出題者が「この書き方でも AC になるか」を試す。"""
    problem = db.get(Problem, payload.problem_id)
    if problem is None or problem.room_id != ctx.room.id:
        raise HTTPException(status_code=404, detail="問題が見つかりません。")
    options = dict(problem.parse_options or {})
    options["ordered_list"] = bool(problem.ordered_list)
    result = judge_isolated(
        problem.answer_latex,
        payload.candidate_latex,
        options,
        timeout=settings.judge_timeout_sec,
    )
    return SelfTestResponse(
        verdict=result.get("verdict", "PENDING"),
        reason=result.get("reason", ""),
        detail=result.get("detail", ""),
        elapsed_ms=int(result.get("elapsed_ms", 0)),
    )


# --------------------------------------------------------------------------
# 提出・参加者
# --------------------------------------------------------------------------


@router.get("/submissions", response_model=list[HostSubmission])
def list_submissions(
    problem_id: int | None = None,
    participant_id: int | None = None,
    verdict: str | None = None,
    since_id: int = 0,
    limit: int = 200,
    ctx: HostContext = Depends(require_host),
    db: Session = Depends(get_db),
) -> list[HostSubmission]:
    limit = max(1, min(limit, 500))
    stmt = (
        select(Submission, Problem.title, Participant.display_name)
        .join(Problem, Problem.id == Submission.problem_id)
        .join(Participant, Participant.id == Submission.participant_id)
        .where(Submission.room_id == ctx.room.id)
        .order_by(Submission.id.desc())
        .limit(limit)
    )
    if problem_id:
        stmt = stmt.where(Submission.problem_id == problem_id)
    if participant_id:
        stmt = stmt.where(Submission.participant_id == participant_id)
    if verdict in ("AC", "WA", "PENDING"):
        stmt = stmt.where(Submission.verdict == verdict)
    if since_id:
        stmt = stmt.where(Submission.id > since_id)
    rows = db.execute(stmt).all()
    return [
        HostSubmission(
            id=s.id,
            problem_id=s.problem_id,
            problem_title=title,
            participant_id=s.participant_id,
            participant_name=name,
            answer_latex=s.answer_latex,
            verdict=s.verdict,
            reason=s.reason,
            detail=s.detail,
            elapsed_ms=s.elapsed_ms,
            created_at=s.created_at,
        )
        for s, title, name in rows
    ]


@router.get("/submissions.csv")
def export_submissions_csv(
    ctx: HostContext = Depends(require_host), db: Session = Depends(get_db)
) -> Response:
    rows = db.execute(
        select(Submission, Problem.title, Participant.display_name)
        .join(Problem, Problem.id == Submission.problem_id)
        .join(Participant, Participant.id == Submission.participant_id)
        .where(Submission.room_id == ctx.room.id)
        .order_by(Submission.id)
    ).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "submission_id",
            "submitted_at_utc",
            "participant",
            "problem_id",
            "problem_title",
            "answer_latex",
            "verdict",
            "reason",
            "elapsed_ms",
        ]
    )
    for s, title, name in rows:
        created = _aware(s.created_at)
        writer.writerow(
            [
                s.id,
                created.isoformat() if created else "",
                name,
                s.problem_id,
                title,
                s.answer_latex,
                s.verdict,
                s.reason,
                s.elapsed_ms,
            ]
        )
    # Excel で文字化けしないように BOM 付き UTF-8
    data = "﻿" + buffer.getvalue()
    return Response(
        content=data.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="exactroom_{ctx.room.code}.csv"'
        },
    )


@router.get("/participants", response_model=list[HostParticipant])
def list_participants(
    ctx: HostContext = Depends(require_host), db: Session = Depends(get_db)
) -> list[HostParticipant]:
    stats = {
        r[0]: (int(r[1] or 0), int(r[2] or 0))
        for r in db.execute(
            select(
                Submission.participant_id,
                func.count(Submission.id),
                func.sum(case((Submission.verdict == "AC", 1), else_=0)),
            )
            .where(Submission.room_id == ctx.room.id)
            .group_by(Submission.participant_id)
        ).all()
    }
    rows = (
        db.execute(
            select(Participant)
            .where(Participant.room_id == ctx.room.id)
            .order_by(Participant.id)
        )
        .scalars()
        .all()
    )
    out = []
    for p in rows:
        total, ac = stats.get(p.id, (0, 0))
        out.append(
            HostParticipant(
                id=p.id,
                display_name=p.display_name,
                created_at=p.created_at,
                last_seen_at=p.last_seen_at,
                submission_count=total,
                ac_count=ac,
            )
        )
    return out
