#!/usr/bin/env bash
# =============================================================
# 전체 dev 환경 중지 (up.sh 짝).
#   사용: ./scripts/down.sh [--all]
#     기본 = 웹 + OpenDID + CX 샘플 중지, 로컬 Supabase는 데이터 보존
#     --all = 로컬 Supabase(supabase stop)까지 완전 종료
# =============================================================
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
export PATH="/opt/homebrew/bin:$PATH"

echo "CX 샘플 중지:"
pkill -f "http.server 8099" 2>/dev/null && echo "  ✓ :8099" || echo "  - :8099 없음"

echo ""
"$DIR/orchestrator-down.sh" || true
echo ""
"$DIR/dev-down.sh" "${1:-}"
