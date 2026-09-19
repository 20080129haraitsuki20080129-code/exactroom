"""解答者向け API。

★ このファイルのレスポンスに模範解答が漏れないことが最重要 ★
* 問題一覧は ``ProblemPublic`` のみを返す (answer_latex を持たない)。
* 判定結果は 3 値 + 自分の入力に関するメッセージのみ。
* 判定エンジンの内部理由 (reason/detail) は返さない。
"""

from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..deps import SolverContext, client_ip, enforce_rate, require_solver
from ..mathjudge.runner import judge_isolated
from ..models import Problem, Submission, utcnow
from ..schemas import (
    MySubmission,
    ProblemPublic,
    SubmissionCreate,
    SubmissionResult,
)
from ..security import hash_ip

router = APIRouter(prefix="/api/solve", tags=["solve"])

VERDICT_MESSAGE = {
    "AC": "正解です (模範解答と厳密に同値であることを確認しました)。",
    "WA": "不正解です (模範解答と厳密に同値でないことを確認しました)。",
    "PENDING": "判定保留です。同値かどうかを厳密に判定できませんでした。",
}


def _parse_hints(problem: Problem) -> dict:
    options = dict(problem.parse_options or {})
    return {
        "euler_e": bool(options.get("euler_e", True)),
        "imaginary_i": bool(options.get("imaginary_i", True)),
        "log_base": options.get("log_base", "e"),
        "assume_real": bool(options.get("assume_real", True)),
        "assume_positive": bool(options.get("assume_positive", False)),
        "ordered_list": bool(problem.ordered_list),
    }


@router.get("/problems", response_model=list[ProblemPublic])
def list_problems(
    ctx: SolverContext = Depends(require_solver),
    db: Session = Depends(get_db),
) -> list[ProblemPublic]:
    rows = (
        db.execute(
            select(Problem)
            .where(Problem.room_id == ctx.room.id, Problem.is_published.is_(True))
            .order_by(Problem.order_index, Problem.id)
        )
        .scalars()
        .all()
    )
    ctx.participant.last_seen_at = utcnow()
    db.commit()
    return [
        ProblemPublic(
            id=p.id,
            order_index=p.order_index,
            title=p.title,
            statement_latex=p.statement_latex,
            statement_note=p.statement_note,
            parse_hints=_parse_hints(p),
        )
        for p in rows
    ]


@router.post(
    "/submissions", response_model=SubmissionResult, status_code=status.HTTP_201_CREATED
)
def create_submission(
    payload: SubmissionCreate,
    request: Request,
    ctx: SolverContext = Depends(require_solver),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SubmissionResult:
    enforce_rate(request, "submit", settings.rate_submit_per_minute, 60.0)
    room = ctx.room
    if not room.is_open:
        raise HTTPException(status_code=403, detail="この部屋は現在閉じています。")

    problem = db.get(Problem, payload.problem_id)
    if problem is None or problem.room_id != room.id or not problem.is_published:
        raise HTTPException(status_code=404, detail="問題が見つかりません。")

    answer = (payload.answer_latex or "").strip()
    if not answer:
        raise HTTPException(status_code=400, detail="解答が空です。")
    if len(answer) > settings.max_answer_chars:
        raise HTTPException(
            status_code=400,
            detail=f"解答は {settings.max_answer_chars} 文字以内にしてください。",
        )

    # 同一参加者の連続提出クールダウン
    if room.submission_cooldown_sec > 0:
        last = db.execute(
            select(Submission.created_at)
            .where(
                Submission.participant_id == ctx.participant.id,
                Submission.room_id == room.id,
            )
            .order_by(Submission.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if last is not None:
            if last.tzinfo is None:

                last = last.replace(tzinfo=UTC)
            elapsed = (utcnow() - last).total_seconds()
            if elapsed < room.submission_cooldown_sec:
                raise HTTPException(
                    status_code=429,
                    detail=f"{int(room.submission_cooldown_sec - elapsed) + 1} 秒待ってから提出してください。",
                    headers={
                        "Retry-After": str(
                            int(room.submission_cooldown_sec - elapsed) + 1
                        )
                    },
                )

    used = db.execute(
        select(func.count(Submission.id)).where(
            Submission.problem_id == problem.id,
            Submission.participant_id == ctx.participant.id,
        )
    ).scalar_one()
    limit = room.max_submissions_per_problem
    if limit and used >= limit:
        raise HTTPException(status_code=429, detail="この問題の提出回数の上限に達しました。")

    options = dict(problem.parse_options or {})
    options["ordered_list"] = bool(problem.ordered_list)
    # ★ 模範解答はここでだけ使う。結果オブジェクトからは取り出さない。
    result = judge_isolated(
        problem.answer_latex,
        answer,
        options,
        timeout=settings.judge_timeout_sec,
    )

    verdict = result.get("verdict", "PENDING")
    if verdict not in ("AC", "WA", "PENDING"):
        verdict = "PENDING"

    submission = Submission(
        room_id=room.id,
        problem_id=problem.id,
        participant_id=ctx.participant.id,
        answer_latex=answer,
        verdict=verdict,
        reason=str(result.get("reason", ""))[:48],
        detail=str(result.get("detail", ""))[:2000],
        elapsed_ms=int(result.get("elapsed_ms", 0)),
        ip_hash=hash_ip(client_ip(request), settings.secret_key),
    )
    db.add(submission)
    ctx.participant.last_seen_at = utcnow()
    db.commit()
    db.refresh(submission)

    # 上限の最終確認。最初のカウントと挿入の間に別のリクエストが入り込むと
    # 上限を超えられてしまうので、採番済みの id で自分の順位を数え直す。
    # id は一意かつ単調なので、同時に何件来てもちょうど limit 件だけが残る。
    if limit:
        rank = db.execute(
            select(func.count(Submission.id)).where(
                Submission.problem_id == problem.id,
                Submission.participant_id == ctx.participant.id,
                Submission.id <= submission.id,
            )
        ).scalar_one()
        if rank > limit:
            db.delete(submission)
            db.commit()
            raise HTTPException(
                status_code=429, detail="この問題の提出回数の上限に達しました。"
            )
        used = rank - 1

    message = VERDICT_MESSAGE[verdict]
    student_message = str(result.get("student_message", ""))
    if verdict == "PENDING" and student_message:
        # 提出者自身の入力に関する説明だけを付け足す (模範解答の情報は含まない)
        message = f"{message} {student_message}"

    remaining = None if not limit else max(0, limit - used - 1)
    return SubmissionResult(
        submission_id=submission.id,
        problem_id=problem.id,
        verdict=verdict,
        message=message,
        created_at=submission.created_at,
        remaining_submissions=remaining,
    )


@router.get("/submissions", response_model=list[MySubmission])
def my_submissions(
    problem_id: int | None = None,
    limit: int = 100,
    ctx: SolverContext = Depends(require_solver),
    db: Session = Depends(get_db),
) -> list[MySubmission]:
    limit = max(1, min(limit, 200))
    stmt = (
        select(Submission)
        .where(
            Submission.room_id == ctx.room.id,
            Submission.participant_id == ctx.participant.id,
        )
        .order_by(Submission.created_at.desc(), Submission.id.desc())
        .limit(limit)
    )
    if problem_id is not None:
        stmt = stmt.where(Submission.problem_id == problem_id)
    rows = db.execute(stmt).scalars().all()
    return [MySubmission.model_validate(r) for r in rows]
