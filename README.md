# ExactRoom — TeX 数式の厳密自動判定 Web アプリ

出題者が TeX で登録した**秘密の模範解答**と、解答者がブラウザで入力した答案が
**数学的に同値かどうか**を Python + SymPy で**厳密に**判定します。

- ログイン不要。解答者は「**部屋コード + 名前**」、出題者は「**部屋コード + 秘密キー**」だけ。
- 出題者・解答者とも複数人が、別の端末・別の場所から同時に使えます。
- **模範解答は解答者側の HTML / JavaScript / API 応答に一切送信されません。**
- 判定に**数値近似・浮動小数点・`evalf` / `N` を使いません**。小数も厳密な有理数として扱います。
- TeX のパースに **`eval()` を使いません**(ホワイトリスト方式の自前パーサ)。
- 判定は 3 値:

  | 判定 | 意味 |
  | --- | --- |
  | **AC** | 厳密に同値であることを**証明できた** |
  | **WA** | 厳密に同値でないことを**証明できた** |
  | **判定保留 (PENDING)** | どちらも証明できなかった |

- 外部 AI API・有料 API は一切使いません。無料のまま開発・公開・運用できます。
- PC・スマートフォン両対応。

---

## 目次

1. [30 秒で試す](#30-秒で試す)
2. [使い方](#使い方)
3. [判定アルゴリズム](#判定アルゴリズム)
4. [対応している TeX 記法](#対応している-tex-記法)
5. [アーキテクチャ](#アーキテクチャ)
6. [環境変数](#環境変数)
7. [DB スキーマ](#db-スキーマ)
8. [セキュリティ](#セキュリティ)
9. [テスト](#テスト)
10. [公開手順(無料)](#公開手順無料)
11. [よくある質問](#よくある質問)

---

## 30 秒で試す

```bash
git clone <このリポジトリの URL> exactroom
cd exactroom
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"   # 出力を .env に貼る
uvicorn app.main:app --reload
```

ブラウザで <http://127.0.0.1:8000/> を開きます。

1. 「出題する」→「新しい部屋を作る」で部屋を作成(部屋コードが表示されます)
2. 問題と模範解答(TeX)を登録
3. 別の端末・別のブラウザで同じ URL を開き、「解答する」に部屋コードと名前を入力

Docker でもすぐ動きます。

```bash
docker compose up --build
```

---

## 使い方

### 出題者

1. トップページ →「出題する」→「新しい部屋を作る」。
   部屋の名前と**秘密キー(8 文字以上)**を決めます。秘密キーはハッシュ化して保存され、
   復元できません。忘れると部屋を管理できなくなります。
2. 表示された**部屋コード**(例 `MYC5W4`)と**参加リンク**を解答者に伝えます。
3. 「問題」タブで問題を登録します。
   - **問題文 (TeX)** … 解答者に公開されます。KaTeX で整形表示されます。
   - **模範解答 (TeX)** … **解答者には絶対に送信されません。**
   - 「解釈を確認」で、模範解答がシステムにどう読まれるかを登録前に確認できます。
   - 「この問題の判定を試す」で、解答者が書きそうな答案が AC になるか事前に検証できます。
4. 「提出一覧」タブで、**提出者・提出答案・判定結果・提出時刻**をリアルタイムに確認できます。
   CSV でダウンロードもできます。

### 解答者

1. 部屋コードと名前を入れて参加します(ログイン不要)。
2. 問題を選び、数式エディタ(MathLive)で解答を入力して「提出する」。
3. 判定が **AC / WA / 判定保留** で返ります。自分の提出履歴も見られます。

初回参加時に**復帰コード**が表示されます。別の端末から同じ名前で入り直すときに必要に
なることがあるので、控えておいてください(部屋の設定によります)。

---

## 判定アルゴリズム

`app/mathjudge/` が判定の全てです。**要求仕様どおり、数値近似を一切使いません。**

### 1. TeX → SymPy(`parser.py`)

- `eval` / `exec` / `sympify(文字列)` / `sympy.parsing.latex` は**使いません**。
  自前のトークナイザ(`tokenizer.py`)＋再帰下降パーサが、**ホワイトリストにある
  コマンドだけ**を SymPy のコンストラクタ呼び出しに変換します。
- **小数は厳密な有理数**にします。`0.25` → `Rational(1, 4)`。
  生成物に `Float` が 1 つでも混ざっていればエラーにします。
- 入力長・トークン数・ネスト深さ・指数の大きさ・整数の桁数に上限があり、
  `2^{999999}` や `1000000!` のような爆発を**パースの時点で**弾きます。

### 2. 厳密なゼロ判定(`exactzero.py`)

2 つの式が等しいかは「差が恒等的に 0 か」に帰着します。次の順に**証明**を試みます。

**A. 0 であることの証明**

- 構文的に `0` になるか(`cancel`, `together`, `expand`, `radsimp`, `sqrtdenest`,
  `simplify`, `trigsimp`, `logcombine`, `powsimp`, `factor`, `combsimp` …)。
- SymPy の `is_zero is True`(記号的な相殺からのみ導かれる)。

**B. 0 でないことの証明**(すべて記号的な定理に基づきます)

| 手法 | 根拠 |
| --- | --- |
| 有理数なら直接比較 | 自明 |
| `is_irrational is True` | 0 は有理数 ⇒ 無理数は 0 でない |
| `is_rational is False` | 同上 |
| `is_real is False` | 0 は実数 ⇒ 実数でなければ 0 でない |
| 積は全因子が 0 でなければ 0 でない | 整域の性質 |
| √ の Q 上一次独立性 | 相異なる平方因子のない正整数 m に対し `{√m}` は Q 上一次独立 |
| 有理関数の係数 | 係数が 1 つでも 0 でなければ零多項式ではない(体は無限) |
| 厳密値の代入 | 有理数や π/6 などを代入し、得られた**厳密な定数**が 0 でないことを示す |

**使わないもの**: `evalf` / `N` / `float` / 区間演算 / 乱数サンプリングによる数値一致。
また SymPy の `is_zero is False` や `is_positive` は**内部で数値評価にフォールバック
することがある**ため、判定には使っていません(`is_zero is True` のみ使用)。
この制約は `tests/test_security.py` が AST 解析で機械的に検証しています。

**どちらも証明できなければ `UNKNOWN`** → 上位で「判定保留」になります。

### 3. 種類ごとの比較(`equivalence.py`)

| 解答の種類 | 例 | 比較方法 |
| --- | --- | --- |
| 式 `expr` | `x^2-1` | 差が 0 か |
| 関係式 `rel` | `x = 1`, `0 \le x \le 1` | 同じ種類の関係で、`左辺-右辺` が 0 でない定数倍の関係にあるか。不等号は正の定数倍のみ同値 |
| 集合 `set` | `\{1,2\}` | 要素を総当たりで照合(重複を潰して両包含) |
| 複数解 `list` | `1, 2` | 既定では集合として比較。「順序も一致させる」設定も可 |

- 集合とカンマ区切りは同じ「複数解」として相互に比較できます(`\{1,2\}` と `1,2` は AC)。
- **種類が違うものは WA** です(`x=1` と `1`、`\{1,2\}` と `1` など)。
  これらは異なる数学的対象なので「厳密に非同値」と判断できます。

### 4. プロセス分離とタイムアウト(`runner.py`)

判定は**別プロセス**のワーカープールで実行し、ハードタイムアウト(既定 8 秒)を掛けます。
タイムアウトしたワーカーは強制終了され、プールは自動的に作り直されます。
タイムアウトした提出は「判定保留」になります。

サーバレス等でプロセスを増やせない環境では `JUDGE_ISOLATION=inline` にできます
(強制停止はできなくなります)。

---

## 対応している TeX 記法

| 分類 | 例 |
| --- | --- |
| 四則・冪 | `1+2-3*4/5`, `x^2`, `2^{-1}`, `\cdot`, `\times`, `\div` |
| 分数 | `\frac{1}{2}`, `\dfrac`, `\tfrac`, `\cfrac` |
| 根号 | `\sqrt{2}`, `\sqrt[3]{8}` |
| 絶対値 | `|x|`, `\left|x\right|` |
| 三角・双曲線 | `\sin`, `\cos`, `\tan`, `\cot`, `\sec`, `\csc`, `\arcsin`…, `\sinh`… |
| 指数・対数 | `\exp`, `\ln`, `\log`, `\log_{2}8`, `\lg` |
| 定数 | `\pi`, `e`, `i`, `\infty` |
| ギリシャ文字 | `\alpha` … `\omega`, `\Gamma` … `\Omega` |
| 添字 | `x_1`, `a_{n+1}`(`a_{1+n}` と同一視されます) |
| 階乗・二項係数 | `n!`, `\binom{5}{2}` |
| 共役 | `\overline{z}` |
| 関係式 | `=`, `\ne`, `<`, `>`, `\le`, `\ge`, `0 \le x \le 1` |
| 集合 | `\{1,2,3\}`, `\emptyset` |
| 複数解 | `1, 2` |

### 意図的に対応していないもの

`\sum` `\prod` `\int` `\lim` / 行列環境(`\begin{pmatrix}` など) / `\pm` `\mp` /
`\text{}` / `\vec` `\hat` / 未知のコマンド全般。
いずれも**明示的なエラー**になり、黙って誤判定することはありません。

### 括弧なし関数適用の規則

括弧を付けない関数適用は次のように解釈します(**括弧を付けることを推奨します**)。

- 直後が `(` / `{` / `\left(` → その括弧の中だけが引数。`\sin(x)(x+1)` = `sin(x)·(x+1)`
- そうでなければ、直後に並ぶ「数・文字・ギリシャ文字・定数とその冪」までが引数。
  - `\sin 2x` → `sin(2x)`
  - `\sin x^2` → `sin(x²)`
  - `\sin x\cos x` → `sin(x)·cos(x)`(`\cos` で引数が切れる)

### 問題ごとの解釈設定

| 設定 | 既定 | 説明 |
| --- | --- | --- |
| `euler_e` | ON | `e` を自然対数の底として扱う(OFF なら普通の変数) |
| `imaginary_i` | ON | `i` を虚数単位として扱う |
| `assume_real` | ON | 変数を実数と仮定する |
| `assume_positive` | OFF | 変数を正の実数と仮定する(`\ln(x^2)=2\ln x` を AC にしたいときなど) |
| `log_base` | `e` | 底を省略した `\log` の底。`10` にすれば常用対数 |
| `ordered_list` | OFF | カンマ区切りの複数解の順序も一致させる |

> 複素数の問題(`\overline{z}` など)では `assume_real` を OFF にしてください。
> 実数と仮定していると `\overline{z}` が `z` に簡約されてしまいます。

---

## アーキテクチャ

```
ブラウザ (静的 HTML/CSS/JS, MathLive, KaTeX)
        │  JSON over HTTPS  (Authorization: Bearer <署名トークン>)
        ▼
FastAPI  ─ routers/rooms.py   部屋作成・参加・出題者ログイン
         ─ routers/solve.py   問題一覧・提出・自分の履歴   ← 模範解答を返さない
         ─ routers/host.py    問題 CRUD・提出一覧・CSV     ← 模範解答を扱う唯一の場所
        │
        ├─ mathjudge/  TeX パーサ + 厳密判定 (別プロセスで実行)
        └─ SQLAlchemy ─ SQLite もしくは PostgreSQL
```

```
app/
├── main.py            FastAPI アプリ、セキュリティヘッダ、静的配信
├── config.py          環境変数
├── db.py              DB 接続 (SQLite / PostgreSQL 両対応)
├── models.py          DB スキーマ
├── schemas.py         API の入出力 (模範解答を含むのは出題者用の 1 つだけ)
├── security.py        PBKDF2 ハッシュ・署名トークン・部屋コード
├── ratelimit.py       メモリ内レート制限
├── deps.py            認証・レート制限の依存性
├── routers/           エンドポイント
└── mathjudge/
    ├── tokenizer.py   TeX 字句解析 (ホワイトリスト)
    ├── parser.py      TeX → SymPy (eval 不使用)
    ├── exactzero.py   厳密なゼロ判定 (数値近似不使用)
    ├── equivalence.py AC / WA / 判定保留
    ├── runner.py      プロセス分離 + タイムアウト
    └── errors.py      例外
static/                フロントエンド (ビルド不要のバニラ JS)
tests/                 自動テスト
docs/                  スキーマ・公開手順・セキュリティ・判定仕様
```

### 特定サービスに依存しない設計

- **フロントはビルド不要の静的ファイル**。同一オリジン配信でも、GitHub Pages 等に
  分離しても動きます(`static/js/config.js` の `EXACTROOM_API_BASE` を書き換え、
  サーバ側は `CORS_ORIGINS` を設定するだけ)。
- **DB は `DATABASE_URL` を差し替えるだけ**で SQLite ↔ PostgreSQL を移行できます。
- **CDN も差し替え可能**。`static/js/config.js` に候補 URL を並べてあり、
  全部失敗したら自動的にプレーン TeX 入力にフォールバックします。
- **標準の ASGI アプリ + Dockerfile** なので、Render / Fly.io / Koyeb /
  Hugging Face Spaces / 自宅サーバ + Cloudflare Tunnel のどこでも動きます。
- レート制限は `app/ratelimit.py` の `RateLimiter` を差し替えれば Redis 等に移せます。

---

## 環境変数

`.env.example` をコピーして `.env` を作ってください。主なもの:

| 変数 | 既定 | 説明 |
| --- | --- | --- |
| `SECRET_KEY` | (なし) | **本番では必須**。トークン署名鍵。未設定だと起動ごとに変わります |
| `ENVIRONMENT` | `development` | `production` にすると `/docs` を無効化し HSTS を付けます |
| `DATABASE_URL` | `sqlite:///./data/exactroom.db` | PostgreSQL も可 |
| `CORS_ORIGINS` | 空 | フロントを別ホストに置く場合のみ設定(カンマ区切り) |
| `JUDGE_ISOLATION` | `process` | `inline` にすると同一プロセスで判定 |
| `JUDGE_WORKERS` | `2` | 判定ワーカープロセス数 |
| `JUDGE_TIMEOUT_SEC` | `8` | 判定のハードタイムアウト |
| `MAX_ANSWER_CHARS` | `500` | 答案の最大文字数 |
| `ROOM_CREATION_TOKEN` | 空 | 設定すると部屋作成に合言葉が必要になります |
| `ALLOW_ROOM_CREATION` | `true` | `false` で部屋作成を停止 |
| `SUBMISSION_COOLDOWN_SEC` | `3` | 連続提出の最小間隔(部屋ごとに変更可) |
| `TRUST_PROXY_HEADERS` | `false` | リバースプロキシ配下では `true` |
| `CDN_HOSTS` | jsDelivr / unpkg | CSP で許可する CDN |

全一覧は [`.env.example`](.env.example) を参照してください。

---

## DB スキーマ

詳細は [docs/SCHEMA.md](docs/SCHEMA.md)。概要:

| テーブル | 役割 | 秘密情報 |
| --- | --- | --- |
| `rooms` | 部屋 | `secret_hash`(PBKDF2) |
| `problems` | 問題 | **`answer_latex`(模範解答)** |
| `participants` | 解答者 | `recovery_hash`, `ip_hash` |
| `submissions` | 提出履歴 | `detail`(判定の内部情報、出題者のみ) |

起動時に `create_all` でテーブルが作られます(マイグレーション不要)。

---

## セキュリティ

詳細は [docs/SECURITY.md](docs/SECURITY.md)。要点:

- **模範解答の秘匿**: `answer_latex` を含むレスポンススキーマは出題者専用の 1 つだけ。
  `tests/test_secrecy.py` が、正解・不正解・パースエラー・模範解答が壊れている場合まで
  含めて、解答者が到達できる全ての応答に模範解答が現れないことを検証します。
- **`eval()` 不使用**: `tests/test_security.py` が AST を解析し、`app/` 全体で
  `eval` / `exec` / `compile` / `__import__` を呼んでいないことを確認します。
- **認証**: 秘密キーは PBKDF2-HMAC-SHA256(20 万回)。照合は定数時間、失敗時は遅延。
  セッションは HMAC-SHA256 署名トークン(有効期限付き)。
- **レート制限**: 部屋作成・参加・出題者ログイン・提出にそれぞれ上限。
- **DoS 対策**: 入力長・トークン数・ネスト深さ・指数・整数桁数の上限、
  判定コストに応じた変形の打ち切り、別プロセス実行 + ハードタイムアウト。
- **セキュリティヘッダ**: CSP / `X-Content-Type-Options` / `X-Frame-Options` /
  `Referrer-Policy` / `Permissions-Policy` / HSTS(本番)。
- **XSS 対策**: フロントは `innerHTML` を一切使わず `textContent` のみ。
  数式表示は KaTeX(`trust: false`)。
- **プライバシー**: 生の IP は保存せず、鍵付きハッシュの先頭 16 桁のみ。

---

## テスト

```bash
pip install -r requirements-dev.txt
pytest -q
```

| ファイル | 内容 |
| --- | --- |
| `tests/test_parser.py` | TeX パース、小数の厳密化、危険入力の拒否 |
| `tests/test_equivalence.py` | AC / WA / 判定保留 の網羅、厳密ゼロ判定 |
| `tests/test_api.py` | 部屋作成 → 出題 → 参加 → 提出 → 確認 の一連 |
| `tests/test_secrecy.py` | **模範解答が解答者に漏れないこと** |
| `tests/test_security.py` | eval 不使用・数値評価不使用・認証・レート制限・DoS 耐性 |
| `tests/test_runner.py` | プロセス分離とタイムアウト |

CI(GitHub Actions)では Python 3.11 / 3.12 で lint + テストを実行します。

---

## 運用ツール(サーバ不要 / CLI)

```bash
# 出題前に、別解が AC になるかまとめて確認する
python scripts/judge_cli.py '\frac{1}{2}' '0.5' '\frac{2}{4}' '0.6'
python scripts/judge_cli.py --assume-positive '\ln(x^2)' '2\ln x'
python scripts/judge_cli.py --file cases.tsv     # 模範解答<TAB>答案1<TAB>答案2 ...

# サーバ管理 (DATABASE_URL を見て同じ DB につなぎます)
python scripts/admin.py list-rooms
python scripts/admin.py reset-secret ABC123 --secret '新しい秘密キー'   # 秘密キーを忘れたとき
python scripts/admin.py close-room ABC123
python scripts/admin.py export ABC123 > submissions.csv
python scripts/admin.py delete-room ABC123 --yes
```

---

## 公開手順(無料)

詳細は [docs/DEPLOY.md](docs/DEPLOY.md)。おすすめの無料構成:

| 構成 | 費用 | 備考 |
| --- | --- | --- |
| **自宅 PC + Cloudflare Tunnel** | 0 円 | どこにも縛られない。常時起動できるなら最有力 |
| **Render(Free) + Neon(Free Postgres)** | 0 円 | クレジットカード不要。15 分で自動スリープ |
| **Hugging Face Spaces(Docker, Free)** | 0 円 | Dockerfile をそのまま使えます |
| **Koyeb / Fly.io の無料枠** | 0 円 | 枠の内容は変わるので要確認 |

どれも `Dockerfile` か `requirements.txt` + 起動コマンドだけで動きます。
サービスを乗り換えるときは `DATABASE_URL` と `SECRET_KEY` を移すだけです。

---

## よくある質問

**Q. 「判定保留」が出るのはどんなとき?**
A. (1) 答案を TeX として解釈できない、(2) 同値性も非同値性も証明できない、
(3) 判定がタイムアウトした、のいずれかです。「間違い」という意味ではありません。
出題者は「提出一覧」の「根拠」列で理由を確認できます。

**Q. `\sqrt{2}` に `1.41421356` と答えたら?**
A. **WA** です。小数は厳密な有理数として扱われ、`√2` が無理数であることから
非同値が**証明**できるためです。近似値を正解にしたい用途には向きません。

**Q. 出題者は複数人でもいい?**
A. はい。同じ部屋コードと秘密キーを知っている人は全員が出題者として同時に操作できます。

**Q. 同じ名前で二人が参加したら?**
A. 既定(`rejoin_policy=open`)では同一人物として扱われます。なりすましを防ぐには
設定で「復帰コードが必要」に切り替えてください。

**Q. 模範解答は本当に送られない?**
A. はい。解答者向けの API スキーマに `answer_latex` は存在せず、判定はサーバ内だけで
行われます。エラーメッセージも、模範解答が壊れている場合は
「この問題は現在採点できません」に置き換えられます。
`tests/test_secrecy.py` がこれを機械的に検証しています。

---

## ライセンス

MIT License — [LICENSE](LICENSE) を参照してください。
