# DB スキーマ

SQLAlchemy 2.0 の宣言的マッピングで定義しています(`app/models.py`)。
起動時に `Base.metadata.create_all()` が走るため、マイグレーションツールは不要です。
SQLite と PostgreSQL のどちらでも同じ定義がそのまま使えます。

`create_all` は**既存テーブルに列を足しません**。後から追加した列は
`app/db.py` の `_ADDED_COLUMNS` に登録してあり、起動時に不足分だけを
`ALTER TABLE ... ADD COLUMN` します(Alembic を入れるほどの規模ではないため)。
列を追加したら、この表に 1 行足してください。

## ER 図

```
rooms 1 ──< problems 1 ──< submissions >── 1 participants
  │                              │
  └──────────< participants      │
  └──────────────────────────────┘
```

すべての子テーブルは `ON DELETE CASCADE` です。部屋を消せば関連データも消えます。

---

## `rooms` — 部屋

| 列 | 型 | 制約 / 既定 | 説明 |
| --- | --- | --- | --- |
| `id` | INTEGER | PK | |
| `code` | VARCHAR(16) | UNIQUE, INDEX | 部屋コード。`23456789ABCDEFGHJKMNPQRSTUVWXYZ` から生成(0/O/1/I/L を含まない) |
| `title` | VARCHAR(120) | `''` | 部屋の名前 |
| `secret_hash` | VARCHAR(255) | NOT NULL | 出題者用秘密キーの PBKDF2-HMAC-SHA256 ハッシュ。**平文は保存しない** |
| `is_open` | BOOLEAN | `true` | 閉じると参加・提出ができなくなる |
| `allow_new_participants` | BOOLEAN | `true` | `false` にすると**新しい名前**での参加を受け付けない(既存の参加者は入り直せる)。別名で提出上限をリセットされるのを防ぐ |
| `rejoin_policy` | VARCHAR(8) | `'open'` | `open`=名前だけで再参加 / `code`=復帰コードが必要 |
| `max_submissions_per_problem` | INTEGER | `0` | 1 問あたりの提出上限(0 で無制限) |
| `submission_cooldown_sec` | INTEGER | `3` | 連続提出の最小間隔(秒) |
| `created_at` | TIMESTAMPTZ | now | |
| `updated_at` | TIMESTAMPTZ | now / onupdate | |
| `closed_at` | TIMESTAMPTZ | NULL | 閉じた時刻 |

## `problems` — 問題

| 列 | 型 | 制約 / 既定 | 説明 |
| --- | --- | --- | --- |
| `id` | INTEGER | PK | |
| `room_id` | INTEGER | FK → `rooms.id` CASCADE, INDEX | |
| `order_index` | INTEGER | `0` | 表示順 |
| `title` | VARCHAR(200) | `''` | |
| `statement_latex` | TEXT | `''` | 問題文 (TeX)。**解答者に公開** |
| `statement_note` | TEXT | `''` | 補足 (プレーンテキスト)。**解答者に公開** |
| `answer_latex` | TEXT | NOT NULL | **★ 秘密の模範解答 (TeX)。解答者には絶対に送信しない** |
| `parse_options` | JSON | `{}` | `euler_e` / `imaginary_i` / `log_base` / `assume_real` / `assume_positive` |
| `ordered_list` | BOOLEAN | `false` | カンマ区切りの複数解を順序込みで比較するか |
| `is_published` | BOOLEAN | `true` | 非公開なら解答者から見えない |
| `created_at` / `updated_at` | TIMESTAMPTZ | | |

インデックス: `ix_problems_room_order (room_id, order_index)`

> `answer_latex` を読むのは `app/routers/host.py`(出題者トークン必須)と
> `app/routers/solve.py` 内の判定呼び出しだけです。後者は判定結果しか返しません。

## `participants` — 解答者

| 列 | 型 | 制約 / 既定 | 説明 |
| --- | --- | --- | --- |
| `id` | INTEGER | PK | |
| `room_id` | INTEGER | FK → `rooms.id` CASCADE, INDEX | |
| `display_name` | VARCHAR(64) | NOT NULL | 表示名 |
| `name_key` | VARCHAR(64) | NOT NULL | 正規化した名前(NFKC + 小文字化 + 空白除去) |
| `recovery_hash` | VARCHAR(255) | NULL | 復帰コードのハッシュ |
| `ip_hash` | VARCHAR(32) | `''` | 鍵付き IP ハッシュ先頭 16 桁。**生の IP は保存しない** |
| `created_at` | TIMESTAMPTZ | now | |
| `last_seen_at` | TIMESTAMPTZ | now | |

制約: `UNIQUE (room_id, name_key)` — 同じ部屋に同じ名前は 1 人だけ。

## `submissions` — 提出履歴

| 列 | 型 | 制約 / 既定 | 説明 |
| --- | --- | --- | --- |
| `id` | INTEGER | PK | |
| `room_id` | INTEGER | FK → `rooms.id` CASCADE, INDEX | |
| `problem_id` | INTEGER | FK → `problems.id` CASCADE, INDEX | |
| `participant_id` | INTEGER | FK → `participants.id` CASCADE, INDEX | |
| `answer_latex` | TEXT | NOT NULL | 提出された答案。本人と出題者が見られる |
| `status` | VARCHAR(24) | INDEX | `judged` / `undecided` / `input_error` / `problem_error` / `timeout` / `internal_error` |
| `verdict` | VARCHAR(8) | INDEX | `AC` / `WA`。既存DBとの互換用に未確定状態は内部保存時 `PENDING`、APIでは `null` |
| `reason` | VARCHAR(48) | `''` | 判定根拠コード(**出題者のみ**) |
| `detail` | TEXT | `''` | 判定の詳細(**出題者のみ**) |
| `elapsed_ms` | INTEGER | `0` | 判定にかかった時間 |
| `ip_hash` | VARCHAR(32) | `''` | 監査用 |
| `created_at` | TIMESTAMPTZ | now, INDEX | 提出時刻 |

インデックス:
`ix_submissions_room_created (room_id, created_at)` /
`ix_submissions_problem_participant (problem_id, participant_id)`

### `reason` の値

| 値 | 意味 |
| --- | --- |
| `expression_equal` / `expression_differs` / `expression_undecided` | 式の比較 |
| `relation_equivalent` / `relation_differs` / `relation_undecided` | 関係式の比較 |
| `collection_equal` / `collection_differs` / `collection_undecided` | 集合・複数解の比較 |
| `kind_mismatch` | 解答の種類が違う(式 vs 関係式 など) |
| `cardinality` | 要素数が違う |
| `submission_parse_error` | 提出答案を TeX として解釈できない (`input_error`) |
| `model_parse_error` | 模範解答を解釈できない(出題者が直す必要あり) |
| `timeout` | 判定がタイムアウトした |
| `worker_error` / `internal_error` | 判定プロセスの異常 |

---

## 保存されないもの

- 模範解答の平文以外の形(正規化後の SymPy 式などはキャッシュしません)
- 出題者の秘密キーの平文
- 解答者の復帰コードの平文
- 生の IP アドレス・User-Agent・Cookie

---

## 手動でスキーマを作る場合(PostgreSQL)

通常は不要ですが、参考として:

```sql
CREATE TABLE rooms (
  id                          SERIAL PRIMARY KEY,
  code                        VARCHAR(16) NOT NULL UNIQUE,
  title                       VARCHAR(120) NOT NULL DEFAULT '',
  secret_hash                 VARCHAR(255) NOT NULL,
  is_open                     BOOLEAN NOT NULL DEFAULT TRUE,
  allow_new_participants      BOOLEAN NOT NULL DEFAULT TRUE,
  rejoin_policy               VARCHAR(8) NOT NULL DEFAULT 'open',
  max_submissions_per_problem INTEGER NOT NULL DEFAULT 0,
  submission_cooldown_sec     INTEGER NOT NULL DEFAULT 3,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at                   TIMESTAMPTZ
);
CREATE INDEX ix_rooms_code ON rooms (code);

CREATE TABLE problems (
  id              SERIAL PRIMARY KEY,
  room_id         INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
  order_index     INTEGER NOT NULL DEFAULT 0,
  title           VARCHAR(200) NOT NULL DEFAULT '',
  statement_latex TEXT NOT NULL DEFAULT '',
  statement_note  TEXT NOT NULL DEFAULT '',
  answer_latex    TEXT NOT NULL,
  parse_options   JSONB NOT NULL DEFAULT '{}'::jsonb,
  ordered_list    BOOLEAN NOT NULL DEFAULT FALSE,
  is_published    BOOLEAN NOT NULL DEFAULT TRUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_problems_room_id ON problems (room_id);
CREATE INDEX ix_problems_room_order ON problems (room_id, order_index);

CREATE TABLE participants (
  id            SERIAL PRIMARY KEY,
  room_id       INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
  display_name  VARCHAR(64) NOT NULL,
  name_key      VARCHAR(64) NOT NULL,
  recovery_hash VARCHAR(255),
  ip_hash       VARCHAR(32) NOT NULL DEFAULT '',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_participant_room_name UNIQUE (room_id, name_key)
);
CREATE INDEX ix_participants_room_id ON participants (room_id);

CREATE TABLE submissions (
  id             SERIAL PRIMARY KEY,
  room_id        INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
  problem_id     INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
  participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  answer_latex   TEXT NOT NULL,
  status         VARCHAR(24) NOT NULL DEFAULT 'judged',
  verdict        VARCHAR(8) NOT NULL,
  reason         VARCHAR(48) NOT NULL DEFAULT '',
  detail         TEXT NOT NULL DEFAULT '',
  elapsed_ms     INTEGER NOT NULL DEFAULT 0,
  ip_hash        VARCHAR(32) NOT NULL DEFAULT '',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_submissions_room_created ON submissions (room_id, created_at);
CREATE INDEX ix_submissions_problem_participant ON submissions (problem_id, participant_id);
CREATE INDEX ix_submissions_verdict ON submissions (verdict);
```
