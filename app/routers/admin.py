"""運営者だけが使う管理画面の API。

なぜ必要か
----------
部屋コードを忘れた、という問い合わせは必ず起きる。サーバの持ち主が
手元の端末から部屋の一覧を確認できないと、そのたびに Mac の前に座って
CLI を叩くことになる。

守っていること
--------------
1. ``ADMIN_SECRET`` が未設定 (または短すぎる) なら、この API は
   **すべて 404** を返す。既定で無効なので、設定しないかぎり存在しない。
2. 模範解答は一切返さない。運営者は部屋の「持ち主」ではないので、
   問題はタイトルと集計までしか見せない。提出された答案の中身も返さない。
3. 秘密キーのハッシュ、復帰コードのハッシュ、IP のハッシュも返さない。
"""

from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import get_db
from ..deps import (
    ROLE_ADMIN,
    enforce_rate,
    get_room_by_code,
    require_admin,
    require_admin_enabled,
)
from ..models import Participant, Problem, Room, Submission
from ..schemas import (
    AdminLogin,
    AdminParticipantSummary,
    AdminProblemSummary,
    AdminRoomDetail,
    AdminRoomSummary,
    AdminSession,
)
from ..security import create_token

router = APIRouter(prefix="/api/admin", tags=["admin"])

#: 一覧で返す部屋の最大数 (画面が重くならないように)
MAX_ROOMS = 500


@router.post("/login", response_model=AdminSession)
def admin_login(
    payload: AdminLogin,
    request: Request,
    settings: Settings = Depends(require_admin_enabled),
) -> AdminSession:
    enforce_rate(request, "admin_login", settings.rate_admin_login_per_minute, 60.0)
    started = time.monotonic()
    # 長さの違いから秘密の長さが漏れないよう、常に一定時間の比較を行う
    ok = hmac.compare_digest(payload.secret, settings.admin_secret)
    if not ok:
        # 総当たりを少しでも遅くするため、失敗時は最低 0.5 秒かける
        remaining = 0.5 - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)
        raise HTTPException(status_code=401, detail="管理用パスワードが違います。")
    ttl = settings.admin_session_ttl_hours * 3600
    token = create_token({"role": ROLE_ADMIN}, settings.secret_key, ttl)
    return AdminSession(admin_token=token, expires_in=ttl)


@router.get("/rooms", response_model=list[AdminRoomSummary])
def list_rooms(
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AdminRoomSummary]:
    """部屋の一覧を新しい順に返す。

    件数は 1 クエリずつではなく、まとめて集計してから突き合わせる
    (部屋ごとに 3 回問い合わせると、部屋が増えたときに一覧が重くなる)。
    """
    rooms = (
        db.execute(select(Room).order_by(Room.created_at.desc()).limit(MAX_ROOMS))
        .scalars()
        .all()
    )
    if not rooms:
        return []
    room_ids = [room.id for room in rooms]

    def counts(column, table, extra=None):
        stmt = select(column, func.count()).where(table.room_id.in_(room_ids))
        if extra is not None:
            stmt = stmt.where(extra)
        return dict(db.execute(stmt.group_by(column)).all())

    problems = counts(Problem.room_id, Problem)
    published = counts(Problem.room_id, Problem, Problem.is_published.is_(True))
    participants = counts(Participant.room_id, Participant)
    submissions = counts(Submission.room_id, Submission)
    latest = dict(
        db.execute(
            select(Submission.room_id, func.max(Submission.created_at))
            .where(Submission.room_id.in_(room_ids))
            .group_by(Submission.room_id)
        ).all()
    )

    return [
        AdminRoomSummary(
            code=room.code,
            title=room.title,
            is_open=room.is_open,
            problem_count=problems.get(room.id, 0),
            published_problem_count=published.get(room.id, 0),
            participant_count=participants.get(room.id, 0),
            submission_count=submissions.get(room.id, 0),
            created_at=room.created_at,
            last_activity_at=latest.get(room.id),
        )
        for room in rooms
    ]


@router.get("/rooms/{code}", response_model=AdminRoomDetail)
def room_detail(
    code: str,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminRoomDetail:
    """どの部屋か見分けるための中身。模範解答と答案の本文は返さない。"""
    room = get_room_by_code(db, code)

    problem_rows = (
        db.execute(
            select(Problem).where(Problem.room_id == room.id).order_by(
                Problem.order_index
            )
        )
        .scalars()
        .all()
    )
    per_problem = dict(
        db.execute(
            select(Submission.problem_id, func.count())
            .where(Submission.room_id == room.id)
            .group_by(Submission.problem_id)
        ).all()
    )
    problems = [
        AdminProblemSummary(
            order_index=p.order_index,
            title=p.title,
            is_published=p.is_published,
            submission_count=per_problem.get(p.id, 0),
            created_at=p.created_at,
        )
        for p in problem_rows
    ]

    participant_rows = (
        db.execute(
            select(Participant)
            .where(Participant.room_id == room.id)
            .order_by(Participant.created_at)
        )
        .scalars()
        .all()
    )
    per_participant = dict(
        db.execute(
            select(Submission.participant_id, func.count())
            .where(Submission.room_id == room.id)
            .group_by(Submission.participant_id)
        ).all()
    )
    participants = [
        AdminParticipantSummary(
            display_name=p.display_name,
            submission_count=per_participant.get(p.id, 0),
            created_at=p.created_at,
            last_seen_at=p.last_seen_at,
        )
        for p in participant_rows
    ]

    return AdminRoomDetail(
        code=room.code,
        title=room.title,
        is_open=room.is_open,
        allow_new_participants=room.allow_new_participants,
        created_at=room.created_at,
        problems=problems,
        participants=participants,
    )
