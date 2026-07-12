#!/usr/bin/env bash
# =============================================================
# 전체 dev 환경 한 번에 기동.
#   사용: ./scripts/up.sh [all|web]   (기본 all)
#     all = 웹 스택(로컬DB+백엔드+프론트+터널) + OpenDID + CX 샘플
#     web = 웹 스택만 (S1 작업 — 빠름, OpenDID 불필요할 때)
#   중지: ./scripts/down.sh
# =============================================================
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"
MODE="${1:-all}"
CX_SAMPLE_DIR="${CX_SAMPLE_DIR:-$HOME/Downloads/hackerton}"
CX_PORT=8099
LOGDIR="$ROOT/.dev/logs"; mkdir -p "$LOGDIR"

"$DIR/dev-up.sh"

if [ "$MODE" = "all" ]; then
  echo ""
  "$DIR/orchestrator-up.sh" || echo "  (OpenDID 건너뜀 — 필요 시 scripts/orchestrator-up.sh 단독 실행)"

  echo ""
  echo "5) CX 샘플 (격리 위젯 디버그 · localhost 전용)"
  if lsof -nP -iTCP:$CX_PORT -sTCP:LISTEN >/dev/null 2>&1; then
    echo "  ✓ 이미 :$CX_PORT"
  elif [ -d "$CX_SAMPLE_DIR" ]; then
    ( cd "$CX_SAMPLE_DIR"; nohup python3 -m http.server $CX_PORT >"$LOGDIR/cx-sample.log" 2>&1 & )
    echo "  ✓ http://localhost:$CX_PORT/index.html  (ENT_MID · QR로 폰 인증)"
  else
    echo "  - 샘플 폴더 없음: $CX_SAMPLE_DIR  (CX_SAMPLE_DIR 로 지정 가능)"
  fi
fi

cat <<EOF

✅ dev 환경 up ($MODE)
   앱      http://localhost:5173/model/register   (실제 검증은 여기서)
   터널    https://facemarket.wearless.kr
   Studio  http://localhost:54323                 (fm_ 테이블 확인)
$([ "$MODE" = "all" ] && echo "   OpenDID http://localhost:9001                    (→ Start All 클릭)")
$([ "$MODE" = "all" ] && echo "   CX샘플  http://localhost:$CX_PORT/index.html         (위젯만 격리 디버그)")
   중지    ./scripts/down.sh
EOF
