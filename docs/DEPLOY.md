# 公開手順(無料で運用する)

ExactRoom は「標準の ASGI アプリ + ビルド不要の静的ファイル + `DATABASE_URL` だけの DB 依存」
という構成なので、無料枠のある PaaS ならほぼどこでも動きます。
**どのサービスを選んでも、移行に必要なのは `SECRET_KEY` と `DATABASE_URL` を移すことだけ**です。

> **無料枠についての注意**
> 各社の無料枠の条件は頻繁に変わります(例: Fly.io は 2024 年に従来の無料割当を
> 廃止しました)。申し込む前に必ず**その時点の料金ページ**を確認してください。
> 課金を確実に避けたいなら、**支払い方法を登録しなくても始められる選択肢**
> (下の 1. 自宅 PC + Cloudflare Tunnel、2. Render Free、3. Hugging Face Spaces)
> から選ぶのが安全です。

まず全構成に共通の準備から。

---

## 0. 共通の準備

### 0-1. GitHub にコードを置く

```bash
cd exactroom
git init
git add .
git commit -m "Initial commit: ExactRoom"
gh repo create exactroom --public --source=. --push     # gh コマンドを使う場合
# もしくは GitHub で空のリポジトリを作って
# git remote add origin https://github.com/<ユーザ名>/exactroom.git
# git branch -M main && git push -u origin main
```

`.env` と `data/` は `.gitignore` 済みです。**`.env` は絶対にコミットしないでください。**

### 0-2. `SECRET_KEY` を作る

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

これを各サービスの環境変数 `SECRET_KEY` に設定します。
**この値が変わると全員のログイン状態(トークン)が無効になります。**

### 0-3. 最低限の本番設定

| 変数 | 値 |
| --- | --- |
| `SECRET_KEY` | 上で作った文字列 |
| `ENVIRONMENT` | `production` |
| `TRUST_PROXY_HEADERS` | `true`(PaaS のリバースプロキシ配下なら) |
| `DATABASE_URL` | 後述 |
| `ROOM_CREATION_TOKEN` | 任意。設定すると誰でも部屋を作れなくなります |

起動コマンドはどのサービスでも同じです。

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

---

## 1. 自宅 PC / 学校の PC + Cloudflare Tunnel(完全無料・ベンダーロックインなし)

常時起動できる PC があるなら、これが一番自由で確実です。
外部に公開するのに固定 IP もポート開放も要りません。

```bash
# 1) アプリを起動
cd exactroom
source .venv/bin/activate
export SECRET_KEY=... ENVIRONMENT=production TRUST_PROXY_HEADERS=true
uvicorn app.main:app --host 127.0.0.1 --port 8000

# 2) 別のターミナルで Cloudflare Tunnel (cloudflared) を起動
#    macOS:  brew install cloudflared
#    Linux:  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
cloudflared tunnel --url http://127.0.0.1:8000
```

`https://xxxx-yyyy.trycloudflare.com` のような URL が表示されるので、それを配ります
(使い捨ての URL です。固定したい場合は無料の Cloudflare アカウントで
名前付きトンネルを作ってください)。

- DB は SQLite のままで構いません(`data/exactroom.db`)。**バックアップはこのファイルをコピーするだけ**です。
- PC をスリープさせない設定にしてください。

---

## 2. Render(Free)+ Neon(Free PostgreSQL)

クレジットカード不要で始められます。
Render の無料 Web Service は **ディスクが永続化されない**ので、DB は外部の無料
PostgreSQL(Neon)を使います。

### 2-1. Neon で DB を作る

1. <https://neon.tech> でサインアップ(GitHub アカウントで可)。
2. プロジェクトを作成し、接続文字列をコピー。
   `postgresql://user:pass@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`
3. これを `DATABASE_URL` に設定します(`postgresql://` のままで構いません。
   アプリが自動で `postgresql+psycopg://` に読み替えます)。

### 2-2. Render にデプロイ

リポジトリに含まれている [`render.yaml`](../render.yaml) を使う場合:

1. <https://render.com> で「New +」→「Blueprint」→ GitHub リポジトリを選択。
2. `SECRET_KEY` と `DATABASE_URL` を入力(`render.yaml` に `sync: false` で定義済み)。
3. デプロイ完了後、`https://exactroom.onrender.com` のような URL が発行されます。

手動で作る場合の設定:

| 項目 | 値 |
| --- | --- |
| Environment | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/healthz` |

> **注意**: 無料プランは 15 分アクセスがないとスリープし、次のアクセスで
> 30〜60 秒かかります。授業の直前に一度アクセスして起こしておいてください。

---

## 3. Hugging Face Spaces(Docker, Free)

1. <https://huggingface.co/new-space> で Space を作成し、SDK に **Docker** を選択。
2. このリポジトリの中身を push。
3. `README.md` の先頭に Spaces 用のメタデータが必要なので、Space 側の
   `README.md` に次を追記します(このリポジトリの README とは別物です)。

   ```yaml
   ---
   title: ExactRoom
   emoji: 📐
   colorFrom: blue
   colorTo: indigo
   sdk: docker
   app_port: 7860
   ---
   ```

4. Space の Settings → Variables and secrets で `SECRET_KEY` などを設定。
5. Dockerfile の `PORT` は環境変数で上書きできます(`app_port: 7860` に合わせて
   `PORT=7860` を設定)。

> 無料 Space のストレージは永続化されないので、本番運用では Neon などの外部 DB を
> 併用してください。

---

## 4. Fly.io / Koyeb(※ 無料枠の有無は要確認)

同梱の [`Dockerfile`](../Dockerfile) をそのまま使えます。
**Fly.io は 2024 年に従来の無料割当を廃止しており、現在は基本的に有料です。**
「料金を払わない」ことが要件なら 1〜3 を選んでください。ここでは、
すでにアカウントを持っている場合や、将来また無料枠が復活した場合のための
手順として残しています。

### Fly.io

```bash
fly launch --no-deploy            # fly.toml は同梱のものを使う
fly secrets set SECRET_KEY=... DATABASE_URL=... ENVIRONMENT=production TRUST_PROXY_HEADERS=true
fly deploy
```

永続ボリュームを使えば SQLite のままでも運用できます。

```bash
fly volumes create exactroom_data --size 1 --region nrt
fly secrets set DATABASE_URL="sqlite:////data/exactroom.db"
```

> **注意**: 同梱の Dockerfile はコンテナを非 root (uid 10001) で動かします。
> Fly のボリュームは root 所有でマウントされるため、そのままでは
> `/data` に SQLite ファイルを作れません。ボリュームを使う場合は
> 次のいずれかにしてください。
>
> * `DATABASE_URL` を外部 PostgreSQL (Neon など) にしてボリュームを使わない(推奨)
> * Dockerfile の `USER exactroom` をコメントアウトして root で動かす
> * 起動時に `chown 10001:10001 /data` する初期化処理を挟む

### Koyeb

「Create Service」→ GitHub → Dockerfile を自動検出。
環境変数を設定してデプロイするだけです。

> どのサービスでも、移行に必要なのは `SECRET_KEY` と `DATABASE_URL` だけです。

---

## 5. フロントエンドだけを別ホストに置く(任意)

`static/` はビルド不要の静的ファイルなので、GitHub Pages / Netlify /
Cloudflare Pages / Vercel などに単体で置けます。API サーバへの依存を薄くしたい、
あるいは静的配信を高速化したい場合に有効です。

ページ間の移動もアセットの参照も、**`static/` フォルダを丸ごとどこに置いても
動くようになっています**(`index.html` / `solve.html` / `host.html` は
相対リンクで、CSS/JS は `/static/...` を参照します)。

1. `static/js/config.js` を編集して API の場所を指定します:

   ```js
   window.EXACTROOM_API_BASE = "https://exactroom.onrender.com";
   ```

2. **リポジトリのルートをそのまま公開します**(`static/` フォルダごと)。
   CSS と JS は `/static/css/app.css` のような絶対パスを参照するため、
   `static/` の *中身* だけをサイトのルートに置くと 404 になります。

   * GitHub Pages: リポジトリを Pages で公開し、
     入口の URL は `https://<ユーザ名>.github.io/<リポジトリ名>/static/index.html`
   * Netlify / Cloudflare Pages: 公開ディレクトリをリポジトリルート(`.`)にし、
     入口は `/static/index.html`

   `solve.html` / `host.html` へはページ内のリンクで移動するので、
   リライト設定は不要です。

3. API サーバ側で CORS を許可します。

   ```
   CORS_ORIGINS=https://<ユーザ名>.github.io
   ```

4. API サーバは `SERVE_STATIC=false` にすれば静的配信を止められます。

> 動作確認: 公開した `index.html` を開き、ブラウザの開発者ツールで
> CSS/JS が 200 で読めていること、参加リンクのコピーが
> `.../static/index.html?code=XXXXXX` になっていることを確かめてください。

---

## 6. 運用のヒント

### バックアップ

- SQLite: `data/exactroom.db` をコピーするだけ。
  ```bash
  sqlite3 data/exactroom.db ".backup 'backup-$(date +%F).db'"
  ```
- PostgreSQL: `pg_dump "$DATABASE_URL" > backup.sql`

### 複数プロセスで動かす場合の注意

`app/ratelimit.py` のレート制限は**プロセス内メモリ**です。
`uvicorn --workers 4` のように複数プロセスで動かすと、制限が実質ワーカー数倍に
緩みます。教室規模なら問題ありませんが、厳密にしたい場合は `RateLimiter` を
Redis などの共有ストアを使う実装に差し替えてください。

判定用ワーカープロセスは**各サーバプロセスごとに** `JUDGE_WORKERS` 個作られます。
メモリの少ない無料枠では `--workers 1`, `JUDGE_WORKERS=1` から始めてください。

### 監視

- `/healthz` が `{"status":"ok"}` を返します。Render / Fly の Health Check に指定してください。
- 無料の外形監視(UptimeRobot など)で 10 分おきに `/healthz` を叩くと、
  Render のスリープをある程度防げます(サービスの利用規約は各自で確認してください)。

### 授業当日のチェックリスト

1. `/healthz` にアクセスしてサーバを起こす
2. 出題者としてログインし、問題が公開状態か確認
3. 「この問題の判定を試す」で、想定される別解が AC になるか確認
4. 参加リンク(`https://.../?code=XXXXXX`)を配布
5. 終了後、「設定」で部屋を閉じる。必要なら CSV をダウンロード
