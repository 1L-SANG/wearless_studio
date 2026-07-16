#!/usr/bin/env bash
# =============================================================
# FaceMarket 로컬 dev 스택 기동 (멱등·재현 가능).
#   로컬 Supabase(:54321) + OpenDID Holder(:8100) + 백엔드(:8000) +
#   프론트(:5173) + cloudflared 터널.
# 이미 떠있는 컴포넌트는 건너뛴다. 로그=.dev/logs/, 중지=scripts/dev-down.sh.
#
# 전제:
#   - server/.env.local 존재(로컬 DB·FACEMARKET_ENABLED=true 등). gitignore.
#   - .env.local(루트, VITE_API_BASE_URL= 비움) 존재.
#   - supabase config.toml + cloudflared 터널(wearless-fm) 사전 설정됨.
# =============================================================
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH"
LOGDIR="$ROOT/.dev/logs"; PIDDIR="$ROOT/.dev/pids"; mkdir -p "$LOGDIR" "$PIDDIR"

listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
wait_http() { # url name tries
  for _ in $(seq 1 "${3:-40}"); do
    [ "$(curl -sS -m3 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null)" = "200" ] && { echo "  ✓ $2 up"; return 0; }
    sleep 1
  done; echo "  ✗ $2 타임아웃 ($1) — 로그 확인"; return 1
}

[ -f server/.env.local ] || { echo "✗ server/.env.local 없음 — 로컬 env 먼저 준비"; exit 1; }

echo "1) 로컬 Supabase"
if listening 54321; then echo "  ✓ 이미 실행 중"; else supabase start >/dev/null && echo "  ✓ started"; fi

echo "2) OpenDID Holder :8100"
HOLDER_ENABLED=$( ( set -a; . server/.env.local; set +a
  [ -n "${OPENDID_HOLDER_URL:-}" ] && echo true || echo false ) )
if [ "$HOLDER_ENABLED" != "true" ]; then echo "  - OPENDID_HOLDER_URL 미설정, 건너뜀"
elif listening 8100; then echo "  ✓ 이미 실행 중"
else
  export JAVA_HOME="/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"
  export PATH="/opt/homebrew/opt/openjdk@21/bin:$PATH"
  ( cd services/fm-holder
    nohup ./gradlew bootRun >"$LOGDIR/fm-holder.log" 2>&1 &
    echo $! >"$PIDDIR/fm-holder.pid" )
  wait_http http://localhost:8100/holder/health "OpenDID Holder" 80
fi

echo "3) 백엔드 :8000"
if listening 8000; then echo "  ✓ 이미 실행 중"; else
  ( cd server; set -a; . ./.env.local; set +a
    nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >"$LOGDIR/backend.log" 2>&1 &
    echo $! >"$PIDDIR/backend.pid" )
  wait_http http://localhost:8000/healthz "백엔드"
fi

echo "4) 프론트 :5173"
if listening 5173; then echo "  ✓ 이미 실행 중"; else
  nohup pnpm dev >"$LOGDIR/vite.log" 2>&1 & echo $! >"$PIDDIR/vite.pid"
  wait_http http://localhost:5173/ "프론트"
fi

echo "5) cloudflared 터널"
if pgrep -f "cloudflared tunnel run wearless-fm" >/dev/null; then echo "  ✓ 이미 실행 중"; else
  nohup cloudflared tunnel run wearless-fm >"$LOGDIR/cloudflared.log" 2>&1 & echo $! >"$PIDDIR/cloudflared.pid"
  sleep 5; echo "  ✓ started"
fi

cat <<EOF

✅ dev 스택 up
   프론트   http://localhost:5173      터널  https://facemarket.wearless.kr
   백엔드   http://localhost:8000      Studio http://localhost:54323
   Holder   http://localhost:8100/holder/health
   로그     .dev/logs/                 중지  scripts/dev-down.sh
EOF
