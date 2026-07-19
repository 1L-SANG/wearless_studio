#!/usr/bin/env bash
# =============================================================
# 로컬 dev 스택 전부 한 번에 중지 (start.sh 짝).
#   터널 + 프론트 + 백엔드 + Holder + OpenDID(오케스트레이터·엔티티·Besu/PG 도커) 중지.
#   기본: 로컬 Supabase 는 유지(데이터 보존).
#   --all: Supabase(supabase stop)까지 중지.
#
# OpenDID Generate 상태(월렛·DID)는 postgre-opendid 볼륨에 보존 → 다음 기동 시 재사용.
# =============================================================
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"

stop_pat() { pkill -f "$1" 2>/dev/null && echo "  ✓ $2" || echo "  - $2 (없음)"; }
stop_port() { local pid; pid=$(lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null)
  [ -n "$pid" ] && kill $pid 2>/dev/null && echo "  ✓ $2 (:$1)" || echo "  - $2 (:$1 없음)"; }

echo "코어 중지:"
stop_pat "cloudflared tunnel run wearless-fm" "터널"
stop_pat "vite" "프론트"
stop_pat "uvicorn app.main:app" "백엔드"
stop_pat "kr.wearless.fmholder.FmHolderApplication" "OpenDID Holder"

echo "OpenDID 중지:"
for p in 9001 8090 8091 8092 8093 8094 8095; do stop_port "$p" "JVM"; done
for c in opendid-besu-node postgre-opendid; do
  docker stop "$c" >/dev/null 2>&1 && echo "  ✓ docker $c" || echo "  - docker $c (없음)"
done

if [ "${1:-}" = "--all" ]; then
  echo "Supabase 중지…"; supabase stop >/dev/null 2>&1 && echo "  ✓ Supabase" || echo "  - Supabase"
else
  echo "  · 로컬 Supabase 유지(데이터 보존). 완전 종료: scripts/stop.sh --all"
fi
echo "✅ dev 스택 down"
