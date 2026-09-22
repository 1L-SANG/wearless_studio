#!/usr/bin/env bash
# 프론트 테스트를 파일 단위로 병렬(-P) 실행하고 파일마다 벽시계·결과를 찍는다.
#   scripts/ci/run-frontend-tests.sh tests/frontend/a.test.mjs tests/frontend/b.test.mjs …
#   CONC=3 (동시 파일 수)  OUT=/tmp/fe-tests (파일별 로그)
#
# node --test 의 spec 리포터는 파일이 끝나야 출력하고 파일 이름·벽시계를 안 보여줘서,
# CI 4분 40초를 어느 파일이 먹는지 알 수 없었다(2026-09-22). 여기선 파일마다 한 줄:
#   <초>  <ok|FAIL>  <파일>
# 끝나면 느린 순 top 10 을 다시 찍고, 실패한 파일은 spec 출력 전문을 붙여 준다.
set -uo pipefail
CONC=${CONC:-3}
OUT=${OUT:-/tmp/fe-tests}
mkdir -p "$OUT"
: > "$OUT/summary.txt"
export OUT

run_one() {
  local f=$1 log s e status
  log="$OUT/$(basename "$f").log"
  s=$(date +%s.%N)
  if MOCK_API_LATENCY_SCALE=0 node --test "$f" >"$log" 2>&1; then status=ok; else status=FAIL; fi
  e=$(date +%s.%N)
  awk -v s="$s" -v e="$e" -v st="$status" -v f="$f" 'BEGIN { printf "%7.1f  %-4s  %s\n", e - s, st, f }'
}
export -f run_one

printf '%s\n' "$@" | xargs -P "$CONC" -I{} bash -c 'run_one "$1"' _ {} | tee "$OUT/summary.txt"

echo
echo "== slowest files (s) =="
sort -rn "$OUT/summary.txt" | head -10
echo
total=$(wc -l < "$OUT/summary.txt" | tr -d ' ')
failed=$(grep -c '  FAIL  ' "$OUT/summary.txt" || true)
echo "files: $total, failed: $failed"
if [ "$failed" -gt 0 ]; then
  grep '  FAIL  ' "$OUT/summary.txt" | awk '{print $3}' | while read -r f; do
    echo; echo "===== $f ====="; cat "$OUT/$(basename "$f").log"
  done
  exit 1
fi
