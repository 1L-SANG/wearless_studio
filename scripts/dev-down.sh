#!/usr/bin/env bash
# =============================================================
# FaceMarket 로컬 dev 스택 중지.
#   기본: 터널 + 프론트 + 백엔드 중지. 로컬 Supabase는 유지(데이터 보존).
#   --all: 로컬 Supabase(supabase stop)까지 중지.
# =============================================================
set -uo pipefail
export PATH="/opt/homebrew/bin:$PATH"

stop() { pkill -f "$1" 2>/dev/null && echo "  ✓ $2" || echo "  - $2 (없음)"; }

echo "중지:"
stop "cloudflared tunnel run wearless-fm" "터널"
stop "vite/bin/vite" "프론트"
stop "uvicorn app.main:app" "백엔드"

if [ "${1:-}" = "--all" ]; then
  echo "  로컬 Supabase 중지…"; supabase stop >/dev/null 2>&1 && echo "  ✓ Supabase" || echo "  - Supabase"
else
  echo "  · 로컬 Supabase 유지(데이터 보존). 완전 종료: scripts/dev-down.sh --all"
fi
echo "✅ dev 스택 down"
