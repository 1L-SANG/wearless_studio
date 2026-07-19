#!/usr/bin/env bash
# =============================================================
# 로컬 dev 스택 전부 한 번에 기동 (멱등·재현 가능).
#   Supabase(:54321) + OpenDID(orchestrator:9001 + Besu/PG + 엔티티 8090-8095)
#   + Holder(:8100) + 백엔드(:8000) + 프론트(:5173, 실백엔드) + cloudflared 터널.
# 이미 떠있는 건 건너뛴다. 로그=.dev/logs/, 중지=scripts/stop.sh.
#
# 전제:
#   - server/.env.local (로컬 DB·FACEMARKET_ENABLED=true 등, gitignore)
#   - .env.local (루트, VITE_API_BASE_URL= 비움 → 프론트가 /v1 를 localhost:8000 프록시)
#   - OpenDID 레포 clone+build: ~/devs/did-orchestrator-server (OPENDID_DIR 로 재지정)
#   - openjdk@21, supabase, cloudflared(wearless-fm 터널) 사전 설정
#
# 옵션: ./scripts/start.sh --core   (OpenDID 빼고 코어만: Supabase+백+프론트+터널)
# =============================================================
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH"
LOGDIR="$ROOT/.dev/logs"; PIDDIR="$ROOT/.dev/pids"; mkdir -p "$LOGDIR" "$PIDDIR"
CORE_ONLY=false; [ "${1:-}" = "--core" ] && CORE_ONLY=true

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

if [ "$CORE_ONLY" = false ]; then
  echo "2) OpenDID 오케스트레이터 :9001 (+ Besu/PG + 엔티티)"
  ORCH_DIR="${OPENDID_DIR:-$HOME/devs/did-orchestrator-server/source/did-orchestrator-server}"
  JAR="$ORCH_DIR/build/libs/did-orchestrator-server-2.0.0.jar"
  export JAVA_HOME="/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"
  export PATH="/opt/homebrew/opt/openjdk@21/bin:$PATH"
  jver=$(java -version 2>&1 | head -1)
  if ! echo "$jver" | grep -q '"21'; then echo "  ✗ Java 21 아님($jver) — OpenDID 건너뜀(코어는 계속)";
  elif [ ! -f "$JAR" ]; then echo "  ✗ jar 없음: $JAR — clone+build 먼저. OpenDID 건너뜀";
  else
    if listening 9001; then echo "  ✓ orchestrator 이미 :9001"; else
      ( cd "$ORCH_DIR"; nohup java -jar "$JAR" >"$LOGDIR/orchestrator.log" 2>&1 & echo $! >"$PIDDIR/orchestrator.pid" )
      for _ in $(seq 1 40); do curl -sS -m3 -o /dev/null http://localhost:9001/ 2>/dev/null && break; sleep 1; done
      listening 9001 && echo "  ✓ orchestrator :9001 up" || echo "  ✗ orchestrator 기동 실패 — $LOGDIR/orchestrator.log"
    fi
    green() { local n=0 p; for p in 8090 8091 8092 8093 8094 8095; do
      [ "$(curl -sS -m2 -o /dev/null -w '%{http_code}' http://localhost:$p/actuator/health 2>/dev/null)" = "200" ] && n=$((n+1)); done; echo "$n"; }
    if [ "$(green)" = "6" ]; then echo "  ✓ 엔티티 6/6 green"; else
      echo "  리포지토리(Besu+PG) 기동…"; curl -sS -m120 "http://localhost:9001/startup/besu" >/dev/null 2>&1; sleep 4
      echo "  엔티티 기동(Start All)…"; curl -sS -m200 "http://localhost:9001/startup/all" >/dev/null 2>&1
      for _ in $(seq 1 45); do g=$(green); echo "    green: $g/6"; [ "$g" = "6" ] && break; sleep 4; done
      [ "$(green)" = "6" ] || echo "  ⚠️ 6/6 아님 — 첫 실행이면 http://localhost:9001 에서 Generate All 먼저(1회)"
    fi
  fi

  echo "3) OpenDID Holder :8100"
  HOLDER_ENABLED=$( ( set -a; . server/.env.local; set +a; [ -n "${OPENDID_HOLDER_URL:-}" ] && echo true || echo false ) )
  if [ "$HOLDER_ENABLED" != "true" ]; then echo "  - OPENDID_HOLDER_URL 미설정, 건너뜀"
  elif listening 8100; then echo "  ✓ 이미 실행 중"
  else
    export JAVA_HOME="/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"
    ( cd services/fm-holder; nohup ./gradlew bootRun >"$LOGDIR/fm-holder.log" 2>&1 & echo $! >"$PIDDIR/fm-holder.pid" )
    wait_http http://localhost:8100/holder/health "OpenDID Holder" 80
  fi
else
  echo "2-3) OpenDID 건너뜀(--core)"
fi

echo "4) 백엔드 :8000"
if listening 8000; then echo "  ✓ 이미 실행 중"; else
  ( cd server; set -a; . ./.env.local; set +a
    nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >"$LOGDIR/backend.log" 2>&1 &
    echo $! >"$PIDDIR/backend.pid" )
  wait_http http://localhost:8000/healthz "백엔드"
fi

echo "5) 프론트 :5173 (실백엔드 모드)"
if listening 5173; then echo "  ✓ 이미 실행 중"; else
  # dev:http = vite --mode http (실백엔드). vite proxy 가 /v1 → localhost:8000. (mock 아님)
  nohup pnpm dev:http >"$LOGDIR/vite.log" 2>&1 & echo $! >"$PIDDIR/vite.pid"
  wait_http http://localhost:5173/ "프론트"
fi

echo "6) cloudflared 터널"
if pgrep -f "cloudflared tunnel run wearless-fm" >/dev/null; then echo "  ✓ 이미 실행 중"; else
  nohup cloudflared tunnel run wearless-fm >"$LOGDIR/cloudflared.log" 2>&1 & echo $! >"$PIDDIR/cloudflared.pid"
  sleep 5; echo "  ✓ started"
fi

cat <<EOF

✅ dev 스택 up
   프론트   http://localhost:5173        터널     https://facemarket.wearless.kr
   백엔드   http://localhost:8000        Studio   http://localhost:54323
   OpenDID  http://localhost:9001        Holder   http://localhost:8100/holder/health
   로그     .dev/logs/                   중지     scripts/stop.sh   (전부: --all)
EOF
