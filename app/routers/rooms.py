"""部屋の作成 / 参加 / 出題者ログイン。"""

from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..deps import (
    ROLE_HOST,
    ROLE_SOLVER,
    client_ip,
    enforce_rate,
    get_room_by_code,
)
from ..models import Participant, Problem, Room, utcnow
from ..schemas import (
    HostLogin,
    HostSession,
    JoinRequest,
    JoinResponse,
    RoomCreate,
    RoomCreated,
    RoomPublic,
)
from ..security import (
    create_token,
    generate_recovery_code,
    generate_room_code,
    hash_ip,
    hash_secret,
    name_key,
    normalize_display_name,
    verify_secret,
)

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


def _host_token(room: Room, settings: Settings) -> tuple[str, int]:
    ttl = settings.host_session_ttl_hours * 3600
    token = create_token(
        {"role": ROLE_HOST, "room_id": room.id, "code": room.code},
        settings.secret_key,
        ttl,
    )
    return token, ttl


@router.post("", response_model=RoomCreated, status_code=status.HTTP_201_CREATED)
def create_room(
    payload: RoomCreate,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RoomCreated:
    enforce_rate(request, "create_room", settings.rate_create_room_per_hour, 3600.0)
    if not settings.allow_room_creation:
        raise HTTPException(status_code=403, detail="この環境では部屋を作成できません。")
    if settings.room_creation_token and not _constant_eq(
        payload.creation_token, settings.room_creation_token
    ):
        raise HTTPException(status_code=403, detail="部屋作成の合言葉が違います。")
    if len(payload.secret) < settings.min_secret_chars:
        raise HTTPException(
            status_code=400,
            detail=f"秘密キーは {settings.min_secret_chars} 文字以上にしてください。",
        )

    secret_hash = hash_secret(payload.secret)
    title = (payload.title or "").strip()[: settings.max_title_chars]

    for _ in range(10):
        room = Room(
            code=generate_room_code(settings.room_code_length),
            title=title,
            secret_hash=secret_hash,
            submission_cooldown_sec=settings.submission_cooldown_sec,
        )
        db.add(room)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(room)
        token, ttl = _host_token(room, settings)
        return RoomCreated(
            code=room.code, title=room.title, host_token=token, expires_in=ttl
        )
    raise HTTPException(status_code=500, detail="部屋コードを発行できませんでした。")


def _constant_eq(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


@router.get("/{code}", response_model=RoomPublic)
def get_room(
    code: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RoomPublic:
    enforce_rate(request, "room_lookup", settings.rate_general_per_minute, 60.0)
    room = get_room_by_code(db, code)
    count = db.execute(
        select(func.count(Problem.id)).where(
            Problem.room_id == room.id, Problem.is_published.is_(True)
        )
    ).scalar_one()
    return RoomPublic(
        code=room.code, title=room.title, is_open=room.is_open, problem_count=count
    )


@router.post("/{code}/host/login", response_model=HostSession)
def host_login(
    code: str,
    payload: HostLogin,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HostSession:
    enforce_rate(request, "host_login", settings.rate_host_login_per_minute, 60.0)
    room = get_room_by_code(db, code)
    started = time.monotonic()
    ok = verify_secret(payload.secret, room.secret_hash)
    # 総当たりを少しでも遅くするため、失敗時は最低 0.3 秒かける
    if not ok:
        remaining = 0.3 - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)
        raise HTTPException(status_code=401, detail="秘密キーが違います。")
    token, ttl = _host_token(room, settings)
    return HostSession(
        code=room.code, title=room.title, host_token=token, expires_in=ttl
    )


@router.post("/{code}/join", response_model=JoinResponse)
def join_room(
    code: str,
    payload: JoinRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JoinResponse:
    enforce_rate(request, "join", settings.rate_join_per_minute, 60.0)
    room = get_room_by_code(db, code)
    if not room.is_open:
        raise HTTPException(status_code=403, detail="この部屋は現在閉じています。")

    display_name = normalize_display_name(payload.display_name)
    if not display_name:
        raise HTTPException(status_code=400, detail="名前を入力してください。")
    if len(display_name) > settings.max_name_chars:
        raise HTTPException(
            status_code=400,
            detail=f"名前は {settings.max_name_chars} 文字以内にしてください。",
        )
    key = name_key(display_name)
    if not key:
        raise HTTPException(status_code=400, detail="名前を入力してください。")

    existing = db.execute(
        select(Participant).where(
            Participant.room_id == room.id, Participant.name_key == key
        )
    ).scalar_one_or_none()

    recovery_code: str | None = None
    if existing is None:
        recovery_code = generate_recovery_code()
        participant = Participant(
            room_id=room.id,
            display_name=display_name,
            name_key=key,
            recovery_hash=hash_secret(recovery_code, iterations=60_000),
            ip_hash=hash_ip(client_ip(request), settings.secret_key),
        )
        db.add(participant)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            participant = db.execute(
                select(Participant).where(
                    Participant.room_id == room.id, Participant.name_key == key
                )
            ).scalar_one()
            recovery_code = None
        else:
            db.refresh(participant)
    else:
        participant = existing
        if room.rejoin_policy == "code" and (
            not payload.recovery_code
            or not verify_secret(
                payload.recovery_code.strip().upper(), participant.recovery_hash or ""
            )
        ):
            raise HTTPException(
                status_code=401,
                detail="その名前は使用中です。復帰コードを入力するか別の名前にしてください。",
            )
        participant.display_name = display_name
        participant.last_seen_at = utcnow()
        db.commit()

    ttl = settings.session_ttl_hours * 3600
    token = create_token(
        {
            "role": ROLE_SOLVER,
            "room_id": room.id,
            "code": room.code,
            "participant_id": participant.id,
        },
        settings.secret_key,
        ttl,
    )
    return JoinResponse(
        code=room.code,
        title=room.title,
        participant_id=participant.id,
        display_name=participant.display_name,
        token=token,
        expires_in=ttl,
        recovery_code=recovery_code,
    )
