#!/bin/bash
# ════════════════════════════════════════════════════════════════════
#  내 프롬프트 테스트 러너
#  · 프롬프트는 → spike/my-prompt.txt  에 작성 (이 파일 말고!)
#  · 아래 "조정 노브" 값만 바꾸고 실행:   bash spike/run-mine.sh
#  · 빠른 테스트:  RUNS=1 MODEL=gemini-3.1-flash-image bash spike/run-mine.sh
#  · 첨부 순서: [1]베이스 [2,3]상의(INPUT 폴더, 파일명 정렬순) [4]하의(MATCH)
# ════════════════════════════════════════════════════════════════════
cd "$(dirname "$0")/.." || { echo "❌ 디렉터리 이동 실패"; exit 1; }

# ───────────────── 조정 노브 (지금은 더미값 — 자유롭게 변경) ─────────────────
#  ${VAR:-기본값} 형태라, 임시로 'VAR=값 bash spike/run-mine.sh' 로도 덮어쓸 수 있음
MODEL="${MODEL:-gemini-3-pro-image}"     # gemini-3-pro-image (Pro) | gemini-3.1-flash-image (Flash)
TEMP="${TEMP-0.4}"                        # temperature 0.0~1.0. 끄려면(모델 기본값) TEMP="" 로 실행. (':-' 아닌 '-' 라 빈값 유지)
RES="${RES:-1K}"                          # 1K | 2K | 4K  (※2K 서버 경로가 일시 저하(유령)라 1K 권장. 복구되면 2K로)
RUNS="${RUNS:-10}"                        # 반복 횟수
TYPE="${TYPE:-티셔츠}"                     # 의류 종류
BASE="${BASE:-spike/base/base-female-2K.png}"          # [1] 베이스 마네킹 (여성)
INPUT="${INPUT:-spike/input/tee-fb}"                   # [2,3] 상의 폴더 (파일명 정렬순: 1_front, 2_back)
MATCH="${MATCH:-outputs/coor_matching/generated_v2/women_bottom/05_무난_릴랙스드_플루이드_와이드_레그_트라우저_(그레이)_cos.png}"  # [4] 하의. 상의만 입히려면 MATCH=""
# ──────────────────────────────────────────────────────────────────────────

fail() { echo "❌ $1"; exit 1; }

# ── preflight: 셋업 문제를 미리, 명확하게 ──
grep -Eq '^[[:space:]]*GEMINI_API_KEY[[:space:]]*=[[:space:]]*\S' spike/.env 2>/dev/null \
  || fail "spike/.env 에 GEMINI_API_KEY 가 없습니다 (AI Studio AIza... 키)."
[ -s spike/my-prompt.txt ] || fail "spike/my-prompt.txt 가 비었거나 없습니다 — 프롬프트를 먼저 작성하세요."
[ -f "$BASE" ]  || fail "베이스 이미지가 없습니다: $BASE"
[ -d "$INPUT" ] || fail "상의 폴더가 없습니다: $INPUT"

# 하의(MATCH) 있으면 마지막 첨부로 추가 (없으면 상의만)
MATCH_ARG=()
if [ -n "$MATCH" ]; then
  [ -f "$MATCH" ] || fail "하의 이미지가 없습니다: $MATCH"
  MATCH_ARG=(--match "$MATCH")
fi

TEMP_ARG=(); [ -n "$TEMP" ] && TEMP_ARG=(--temp "$TEMP")
echo "모델=$MODEL · temp=${TEMP:-기본} · res=$RES · runs=$RUNS · 하의=${MATCH:+있음}${MATCH:-없음(상의만)}"
echo "──────────────────────────────────────────"

ok=0
for i in $(seq 1 "$RUNS"); do
  echo "=== run $i/$RUNS ==="
  out=$(node spike/spike.js "$INPUT" \
    --prompt-file spike/my-prompt.txt --base "$BASE" --type "$TYPE" \
    --product spike/product-ami-tee.json --model "$MODEL" --res "$RES" "${TEMP_ARG[@]}" "${MATCH_ARG[@]}" 2>&1)
  code=$?
  [ $code -eq 0 ] || { echo "$out"; fail "run $i 실행 실패 (exit $code) — 위 메시지를 확인하세요."; }
  echo "$out" | grep -q "dry-run" && { echo "$out"; fail "dry-run으로 돌았습니다 (키 미인식) — 실제 이미지가 생성되지 않았습니다."; }
  line=$(echo "$out" | grep -E "완료|실패|result-")
  [ -n "$line" ] || { echo "$out"; fail "run $i: 결과 라인이 없습니다 — 위 전체 출력을 확인하세요."; }
  echo "$line"
  echo "$out" | grep -q "완료" && ok=$((ok+1))
done

echo "──────────────────────────────────────────"
fails=$((RUNS - ok))
echo "결과: 성공 $ok · 실패 $fails · 총 $RUNS  (결과 이미지 → spike/runs/)"
if [ "$ok" -eq 0 ]; then
  fail "성공 0건 — 전부 실패했습니다. 프롬프트/입력/모델 설정을 점검하세요."
elif [ "$fails" -gt 0 ]; then
  echo "⚠️  일부 실패 ${fails}건 — 위 '실패' 라인을 확인하세요. (스파이크에선 정상: 성공률 측정용)"
else
  echo "✅ 전부 성공 ($ok/$RUNS)."
fi
