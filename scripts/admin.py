#!/usr/bin/env python3
"""サーバ管理用のちょっとしたツール。

    python scripts/admin.py list-rooms
    python scripts/admin.py reset-secret ABC123 --secret '新しい秘密キー'
    python scripts/admin.py close-room ABC123
    python scripts/admin.py delete-room ABC123 --yes
    python scripts/admin.py export ABC123 > submissions.csv

``DATABASE_URL`` (または .env) を見て同じ DB につなぎます。
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.models import Participant, Problem, Room, Submission, utcnow  # noqa: E402
from app.security import hash_secret, normalize_room_code  # noqa: E402


def _room(session, code: str) -> Room:
    room = session.execute(
        select(Room).where(Room.code == normalize_room_code(code))
    ).scalar_one_or_none()
    if room is None:
        raise SystemExit(f"部屋 {code} が見つかりません。")
    return room


def cmd_list_rooms(_args) -> None:
    with session_scope() as session:
        rows = session.execute(select(Room).order_by(Room.created_at.desc())).scalars().all()
        if not rows:
            print("部屋がありません。")
            return
        print(f"{'コード':<10}{'状態':<6}{'問題':>4}{'参加':>5}{'提出':>6}  作成日時  名前")
        for room in rows:
            problems = session.execute(
                select(func.count(Problem.id)).where(Problem.room_id == room.id)
            ).scalar_one()
            people = session.execute(
                select(func.count(Participant.id)).where(Participant.room_id == room.id)
            ).scalar_one()
            submissions = session.execute(
                select(func.count(Submission.id)).where(Submission.room_id == room.id)
            ).scalar_one()
            state = "開" if room.is_open else "閉"
            print(
                f"{room.code:<10}{state:<6}{problems:>4}{people:>5}{submissions:>6}  "
                f"{room.created_at:%Y-%m-%d %H:%M}  {room.title}"
            )


def cmd_reset_secret(args) -> None:
    if len(args.secret) < 8:
        raise SystemExit("秘密キーは 8 文字以上にしてください。")
    with session_scope() as session:
        room = _room(session, args.code)
        room.secret_hash = hash_secret(args.secret)
        print(f"部屋 {room.code} の秘密キーを変更しました。")


def cmd_close_room(args) -> None:
    with session_scope() as session:
        room = _room(session, args.code)
        room.is_open = False
        room.closed_at = utcnow()
        print(f"部屋 {room.code} を閉じました。")


def cmd_open_room(args) -> None:
    with session_scope() as session:
        room = _room(session, args.code)
        room.is_open = True
        room.closed_at = None
        print(f"部屋 {room.code} を開きました。")


def cmd_delete_room(args) -> None:
    if not args.yes:
        raise SystemExit("本当に削除する場合は --yes を付けてください。")
    with session_scope() as session:
        room = _room(session, args.code)
        code = room.code
        session.delete(room)
        print(f"部屋 {code} と関連データをすべて削除しました。")


def cmd_export(args) -> None:
    with session_scope() as session:
        room = _room(session, args.code)
        rows = session.execute(
            select(Submission, Problem.title, Participant.display_name)
            .join(Problem, Problem.id == Submission.problem_id)
            .join(Participant, Participant.id == Submission.participant_id)
            .where(Submission.room_id == room.id)
            .order_by(Submission.id)
        ).all()
        writer = csv.writer(sys.stdout)
        writer.writerow(
            ["submission_id", "submitted_at_utc", "participant", "problem_id",
             "problem_title", "answer_latex", "verdict", "reason", "elapsed_ms"]
        )
        for submission, title, name in rows:
            created = submission.created_at
            # SQLite から返る日時はタイムゾーンを失っているので付け直す。
            # 付けないと、画面の CSV (UTC 表記) と時刻がずれて読める。
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            writer.writerow(
                [submission.id, created.isoformat() if created else "", name,
                 submission.problem_id, title, submission.answer_latex,
                 submission.verdict, submission.reason, submission.elapsed_ms]
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="ExactRoom 管理ツール")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-rooms", help="部屋の一覧").set_defaults(func=cmd_list_rooms)

    p = sub.add_parser("reset-secret", help="出題者の秘密キーを再設定")
    p.add_argument("code")
    p.add_argument("--secret", required=True)
    p.set_defaults(func=cmd_reset_secret)

    p = sub.add_parser("close-room", help="部屋を閉じる")
    p.add_argument("code")
    p.set_defaults(func=cmd_close_room)

    p = sub.add_parser("open-room", help="部屋を開く")
    p.add_argument("code")
    p.set_defaults(func=cmd_open_room)

    p = sub.add_parser("delete-room", help="部屋を削除")
    p.add_argument("code")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_delete_room)

    p = sub.add_parser("export", help="提出履歴を CSV で出力")
    p.add_argument("code")
    p.set_defaults(func=cmd_export)

    args = parser.parse_args()
    init_db()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
