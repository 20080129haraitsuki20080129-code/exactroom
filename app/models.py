"""DB スキーマ (SQLAlchemy 2.0 declarative)。

模範解答は ``Problem.answer_latex`` にのみ保存される。
この列は API レスポンス用スキーマ (app/schemas.py) のどこにも現れず、
出題者向けエンドポイントでのみ明示的に扱われる。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Room(Base):
    """出題者と解答者が集まる部屋。"""

    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(120), default="")
    #: 出題者用の秘密キー (PBKDF2 ハッシュ。平文は保存しない)
    secret_hash: Mapped[str] = mapped_column(String(255))
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    #: "open" … 名前だけで再参加できる / "code" … 復帰コードが必要
    rejoin_policy: Mapped[str] = mapped_column(String(8), default="open")
    #: 1 問あたりの提出上限 (0 で無制限)
    max_submissions_per_problem: Mapped[int] = mapped_column(Integer, default=0)
    #: 連続提出の最小間隔 (秒)
    submission_cooldown_sec: Mapped[int] = mapped_column(Integer, default=3)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    problems: Mapped[list[Problem]] = relationship(
        back_populates="room", cascade="all, delete-orphan", order_by="Problem.order_index"
    )
    participants: Mapped[list[Participant]] = relationship(
        back_populates="room", cascade="all, delete-orphan"
    )
    submissions: Mapped[list[Submission]] = relationship(
        back_populates="room", cascade="all, delete-orphan"
    )


class Problem(Base):
    """問題。``answer_latex`` が秘密の模範解答。"""

    __tablename__ = "problems"
    __table_args__ = (
        Index("ix_problems_room_order", "room_id", "order_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), index=True
    )
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200), default="")
    #: 問題文 (TeX)。解答者に公開される。
    statement_latex: Mapped[str] = mapped_column(Text, default="")
    #: 補足説明 (プレーンテキスト)。解答者に公開される。
    statement_note: Mapped[str] = mapped_column(Text, default="")
    #: ★ 秘密の模範解答 (TeX)。API レスポンスには絶対に含めない。
    answer_latex: Mapped[str] = mapped_column(Text)
    #: パーサ設定 (euler_e, imaginary_i, log_base, assume_real, ...)
    parse_options: Mapped[dict] = mapped_column(JSON, default=dict)
    #: カンマ区切りの複数解を順序込みで比較するか
    ordered_list: Mapped[bool] = mapped_column(Boolean, default=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    room: Mapped[Room] = relationship(back_populates="problems")
    submissions: Mapped[list[Submission]] = relationship(
        back_populates="problem", cascade="all, delete-orphan"
    )


class Participant(Base):
    """解答者。ログイン不要、部屋コード + 名前で識別する。"""

    __tablename__ = "participants"
    __table_args__ = (
        UniqueConstraint("room_id", "name_key", name="uq_participant_room_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(64))
    #: 正規化した名前 (重複判定用)
    name_key: Mapped[str] = mapped_column(String(64))
    #: 別端末から同じ名前で戻るための復帰コード (ハッシュ)
    recovery_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 監査用の鍵付き IP ハッシュ (生の IP は保存しない)
    ip_hash: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    room: Mapped[Room] = relationship(back_populates="participants")
    submissions: Mapped[list[Submission]] = relationship(
        back_populates="participant", cascade="all, delete-orphan"
    )


class Submission(Base):
    """提出履歴。出題者はここを見て提出者・答案・判定・時刻を確認する。"""

    __tablename__ = "submissions"
    __table_args__ = (
        Index("ix_submissions_room_created", "room_id", "created_at"),
        Index("ix_submissions_problem_participant", "problem_id", "participant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), index=True
    )
    problem_id: Mapped[int] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), index=True
    )
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), index=True
    )
    #: 提出された答案 (TeX)。提出者本人と出題者が見られる。
    answer_latex: Mapped[str] = mapped_column(Text)
    #: "AC" | "WA" | "PENDING"
    verdict: Mapped[str] = mapped_column(String(8), index=True)
    #: 判定根拠コード (出題者のみ)
    reason: Mapped[str] = mapped_column(String(48), default="")
    #: 判定の詳細 (出題者のみ)
    detail: Mapped[str] = mapped_column(Text, default="")
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    ip_hash: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    room: Mapped[Room] = relationship(back_populates="submissions")
    problem: Mapped[Problem] = relationship(back_populates="submissions")
    participant: Mapped[Participant] = relationship(back_populates="submissions")
