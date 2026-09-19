"""FastAPI の共通依存性 (認証・レート制限・部屋の取得)。"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .models import Participant, Room
from .ratelimit import limiter
from .security import TokenError, normalize_room_code, verify_token

ROLE_HOST = "host"
ROLE_SOLVER = "solver"


def client_ip(request: Request) -> str:
    settings = get_settings()
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()[:64]
    if request.client is None:
        return "unknown"
    return request.client.host or "unknown"


def enforce_rate(request: Request, bucket: str, limit: int, window: float) -> None:
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    key = f"{bucket}:{client_ip(request)}"
    if not limiter.hit(key, limit, window):
        wait = limiter.retry_after(key, window)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"アクセスが集中しています。{wait} 秒ほどおいてからお試しください。",
            headers={"Retry-After": str(wait)},
        )


def general_rate_limit(request: Request, settings: Settings = Depends(get_settings)):
    enforce_rate(request, "general", settings.rate_general_per_minute, 60.0)


def get_room_by_code(db: Session, code: str) -> Room:
    normalized = normalize_room_code(code)
    if not normalized:
        raise HTTPException(status_code=404, detail="部屋が見つかりません。")
    room = db.execute(select(Room).where(Room.code == normalized)).scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="部屋が見つかりません。")
    return room


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="認証が必要です。")
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="認証形式が不正です。")
    return parts[1].strip()


def _decode(token: str, settings: Settings) -> dict:
    try:
        return verify_token(token, settings.secret_key)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


class HostContext:
    def __init__(self, room: Room) -> None:
        self.room = room


class SolverContext:
    def __init__(self, room: Room, participant: Participant) -> None:
        self.room = room
        self.participant = participant


def require_host(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HostContext:
    payload = _decode(_bearer_token(authorization), settings)
    if payload.get("role") != ROLE_HOST:
        raise HTTPException(status_code=403, detail="出題者の権限が必要です。")
    room = db.get(Room, int(payload.get("room_id", 0)))
    if room is None:
        raise HTTPException(status_code=404, detail="部屋が見つかりません。")
    if payload.get("code") != room.code:
        raise HTTPException(status_code=401, detail="トークンが部屋と一致しません。")
    return HostContext(room)


def require_solver(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SolverContext:
    payload = _decode(_bearer_token(authorization), settings)
    if payload.get("role") != ROLE_SOLVER:
        raise HTTPException(status_code=403, detail="解答者の権限が必要です。")
    room = db.get(Room, int(payload.get("room_id", 0)))
    if room is None:
        raise HTTPException(status_code=404, detail="部屋が見つかりません。")
    participant = db.get(Participant, int(payload.get("participant_id", 0)))
    if participant is None or participant.room_id != room.id:
        raise HTTPException(status_code=401, detail="参加者が見つかりません。")
    if payload.get("code") != room.code:
        raise HTTPException(status_code=401, detail="トークンが部屋と一致しません。")
    return SolverContext(room, participant)
