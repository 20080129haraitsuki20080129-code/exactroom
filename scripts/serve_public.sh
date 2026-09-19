#!/usr/bin/env bash
# ExactRoom を Cloudflare Tunnel で一時公開する (アカウント登録不要・無料)
#
#   ./scripts/serve_public.sh              # ポート 8800 で起動して公開
#   PORT=9000 ./scripts/serve_public.sh
#
# Ctrl-C で両方止まります。URL は毎回変わります。
# 固定の URL が必要なら、無料の Cloudflare アカウントで名前付きトンネルを
# 作ってください (docs/DEPLOY.md 参照)。
set -euo pipefail

cd "$(dirname "$0")/.."
PORT="${PORT:-8800}"
PYTHON="${PYTHON:-.venv/bin/python}"

if [ ! -x "$PYTHON" ]; then
  echo "Python が見つかりません: $PYTHON" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared が必要です:  brew install cloudflared" >&2
  exit 1
fi
if [ ! -f .env ]; then
  echo ".env がありません。cp .env.example .env して SECRET_KEY を設定してください。" >&2
  exit 1
fi

cleanup() { kill "${APP_PID:-}" "${TUNNEL_PID:-}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "アプリを起動します (127.0.0.1:$PORT)"
"$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" &
APP_PID=$!

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then break; fi
  sleep 1
done

LOG="$(mktemp -t exactroom-tunnel)"
echo "トンネルを張ります…"
cloudflared tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate > "$LOG" 2>&1 &
TUNNEL_PID=$!

URL=""
for _ in $(seq 1 30); do
  URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)"
  [ -n "$URL" ] && break
  sleep 2
done

if [ -z "$URL" ]; then
  echo "公開 URL を取得できませんでした。ログ: $LOG" >&2
  exit 1
fi

TOKEN="$(grep -E '^ROOM_CREATION_TOKEN=' .env | cut -d= -f2- || true)"
cat <<INFO

  公開 URL : $URL
  出題者用 : $URL/#host
  部屋作成の合言葉 : ${TOKEN:-(未設定 — 誰でも部屋を作れます)}

  この URL は一時的なものです。Ctrl-C で終了します。

INFO
wait
