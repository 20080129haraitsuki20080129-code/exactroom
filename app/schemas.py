"""API の入出力スキーマ。

★ 重要 ★
模範解答 (``Problem.answer_latex``) を含むレスポンススキーマは
**このファイルに存在しない**。出題者が自分で登録した内容を編集画面で
読み戻すための ``HostProblemDetail`` だけが例外で、これは出題者トークンを
持つリクエストにしか返さない。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from .config import get_settings
from .mathjudge.parser import ParseOptions


def _limits() -> dict:
    """入力長の上限を設定から読む。

    ここで読まないと ``MAX_ANSWER_CHARS`` などを変えても Pydantic 側の
    上限が固定のままになり、設定が効かない。
    """
    try:
        settings = get_settings()
    except Exception:  # pragma: no cover - 設定不備時は既定値で読み込めるようにする
        return {"answer": 500, "statement": 2000, "title": 200, "name": 24}
    return {
        "answer": settings.max_answer_chars,
        "statement": settings.max_statement_chars,
        "title": settings.max_title_chars,
        "name": settings.max_name_chars,
    }


LIMITS = _limits()


def _ensure_utc(value):
    """SQLite から返る naive な日時に UTC を付ける。

    タイムゾーン付きで返さないと、ブラウザがローカル時刻と誤解して
    提出時刻が何時間もずれてしまう。
    """
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


#: 常にタイムゾーン付きで返される日時
UtcDatetime = Annotated[datetime, BeforeValidator(_ensure_utc)]

# --------------------------------------------------------------------------
# 共通
# --------------------------------------------------------------------------


class ApiError(BaseModel):
    detail: str
    code: str = "error"


class ParseOptionsIn(BaseModel):
    euler_e: bool = True
    imaginary_i: bool = True
    log_base: str = "e"
    assume_real: bool = True
    assume_positive: bool = False

    @field_validator("log_base")
    @classmethod
    def _log_base(cls, v: str) -> str:
        return v if v in ("e", "10") else "e"

    def to_engine(self) -> dict:
        return ParseOptions(**self.model_dump()).to_dict()


# --------------------------------------------------------------------------
# 部屋
# --------------------------------------------------------------------------


class PublicConfig(BaseModel):
    """ログイン前でも取得できるサーバの設定。

    「部屋を作るのに合言葉が要るのか」をブラウザ側が知らないと、
    入力欄を出せずに 403 の理由が分からなくなる。
    """

    allow_room_creation: bool
    requires_creation_token: bool
    room_code_min_length: int
    min_secret_chars: int
    max_answer_chars: int


class RoomCreate(BaseModel):
    title: str = Field(default="", max_length=LIMITS["title"])
    secret: str = Field(min_length=8, max_length=128)
    creation_token: str = Field(default="", max_length=128)
    #: 出題者が自分で決める部屋コード。空ならサーバが自動発行する。
    code: str = Field(default="", max_length=16)


class RoomCreated(BaseModel):
    code: str
    title: str
    host_token: str
    expires_in: int


class RoomPublic(BaseModel):
    """誰でも見られる部屋情報 (模範解答は含まない)。"""

    code: str
    title: str
    is_open: bool
    problem_count: int


class HostLogin(BaseModel):
    secret: str = Field(min_length=1, max_length=128)


class HostSession(BaseModel):
    code: str
    title: str
    host_token: str
    expires_in: int


class JoinRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=LIMITS["name"])
    recovery_code: str = Field(default="", max_length=32)


class JoinResponse(BaseModel):
    code: str
    title: str
    participant_id: int
    display_name: str
    token: str
    expires_in: int
    #: 初回参加時のみ返る。別端末から同じ名前で戻るときに使う。
    recovery_code: str | None = None


class RoomSettingsUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=LIMITS["title"])
    is_open: bool | None = None
    allow_new_participants: bool | None = None
    rejoin_policy: str | None = None
    max_submissions_per_problem: int | None = Field(default=None, ge=0, le=1000)
    submission_cooldown_sec: int | None = Field(default=None, ge=0, le=600)

    @field_validator("rejoin_policy")
    @classmethod
    def _policy(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v if v in ("open", "code") else "open"


# --------------------------------------------------------------------------
# 問題
# --------------------------------------------------------------------------


class ProblemCreate(BaseModel):
    title: str = Field(default="", max_length=LIMITS["title"])
    statement_latex: str = Field(default="", max_length=LIMITS["statement"])
    statement_note: str = Field(default="", max_length=LIMITS["statement"])
    #: 模範解答 (TeX)。登録専用。レスポンスには載らない。
    answer_latex: str = Field(min_length=1, max_length=LIMITS["answer"])
    parse_options: ParseOptionsIn = Field(default_factory=ParseOptionsIn)
    ordered_list: bool = False
    is_published: bool = True
    order_index: int = Field(default=0, ge=0, le=10000)


class ProblemUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=LIMITS["title"])
    statement_latex: str | None = Field(default=None, max_length=LIMITS["statement"])
    statement_note: str | None = Field(default=None, max_length=LIMITS["statement"])
    answer_latex: str | None = Field(
        default=None, min_length=1, max_length=LIMITS["answer"]
    )
    parse_options: ParseOptionsIn | None = None
    ordered_list: bool | None = None
    is_published: bool | None = None
    order_index: int | None = Field(default=None, ge=0, le=10000)


class ProblemPublic(BaseModel):
    """解答者に見せる問題。模範解答に関する情報は一切含まない。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_index: int
    title: str
    statement_latex: str
    statement_note: str
    #: 入力の解釈方法だけは解答者にも伝える必要がある (e を底とみなす等)
    parse_hints: dict


class HostProblemSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_index: int
    title: str
    statement_latex: str
    statement_note: str
    is_published: bool
    ordered_list: bool
    parse_options: dict
    submission_count: int = 0
    ac_count: int = 0
    created_at: UtcDatetime
    updated_at: UtcDatetime


class HostProblemDetail(HostProblemSummary):
    """出題者本人だけが取得できる。模範解答を含む唯一のスキーマ。"""

    answer_latex: str


# --------------------------------------------------------------------------
# 提出
# --------------------------------------------------------------------------


class SubmissionCreate(BaseModel):
    problem_id: int
    answer_latex: str = Field(min_length=1, max_length=LIMITS["answer"])


class SubmissionResult(BaseModel):
    """解答者に返す判定結果。

    模範解答そのものはもちろん、模範解答の形 (kind) や内部の判定理由も
    返さない。返すのは 3 値の判定と、自分の入力についてのメッセージだけ。
    """

    submission_id: int
    problem_id: int
    verdict: str
    message: str
    created_at: UtcDatetime
    remaining_submissions: int | None = None


class MySubmission(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    problem_id: int
    answer_latex: str
    verdict: str
    created_at: UtcDatetime


class HostSubmission(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    problem_id: int
    problem_title: str
    participant_id: int
    participant_name: str
    answer_latex: str
    verdict: str
    reason: str
    detail: str
    elapsed_ms: int
    created_at: UtcDatetime


class HostParticipant(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    created_at: UtcDatetime
    last_seen_at: UtcDatetime
    submission_count: int = 0
    ac_count: int = 0


class RecoveryCodeReset(BaseModel):
    """出題者が再発行した復帰コード (出題者にだけ返す)。"""

    participant_id: int
    display_name: str
    recovery_code: str


class AnswerCheckRequest(BaseModel):
    """出題者が模範解答の書き方を確かめるための試し打ち。"""

    answer_latex: str = Field(min_length=1, max_length=LIMITS["answer"])
    parse_options: ParseOptionsIn = Field(default_factory=ParseOptionsIn)


class AnswerCheckResponse(BaseModel):
    ok: bool
    kind: str | None = None
    normalized: str | None = None
    message: str = ""


class SelfTestRequest(BaseModel):
    """出題者が「この答案なら AC になるか」を試すための機能。"""

    problem_id: int
    candidate_latex: str = Field(min_length=1, max_length=LIMITS["answer"])


class SelfTestResponse(BaseModel):
    verdict: str
    reason: str
    detail: str
    elapsed_ms: int
