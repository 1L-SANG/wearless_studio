#!/usr/bin/env bash
# 부팅 훅(pre_start.sh) — **복사돼서 실행되는 상황** 그대로 재현한다.
#
# ★ POD_ARGS 는 이 파일을 설치 폴더에서 /pre_start.sh 로 복사한 뒤 실행한다. 즉 $0 은 설치 폴더가
#   아니다. 예전 판은 bootstrap 을 $0 기준으로 찾아(SELF_DIR="/") 통째로 건너뛰었고, venv 없는
#   채로 start.sh 가 시스템 python 으로 떠서 "No module named uvicorn" 으로 죽었다(2026-09-11).
#   그래서 여기서는 **스텁이 아니라 진짜 pre_start.sh** 를 다른 폴더에 복사해 돌리고,
#   고정 설치 폴더의 bootstrap.sh → start.sh 가 그 순서로 불렸는지 본다.
set -uo pipefail
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$HERE/server/deploy/face_render/pre_start.sh"
PASS=0; FAIL=0
ok() { PASS=$((PASS+1)); echo "ok - $1"; }
ng() { FAIL=$((FAIL+1)); echo "not ok - $1"; }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
ROOT="$WORK/root/face_render"          # 설치 폴더(= POD_ARGS 의 R)
mkdir -p "$ROOT" "$WORK/elsewhere"

cat > "$ROOT/bootstrap.sh" <<'B'
#!/usr/bin/env bash
echo "BOOTSTRAP_RAN" >> "$(dirname "$0")/order.txt"
B
cat > "$ROOT/start.sh" <<'S'
#!/usr/bin/env bash
echo "START_RAN" >> "$(dirname "$0")/order.txt"
S
chmod +x "$ROOT/bootstrap.sh" "$ROOT/start.sh"

# 복사해서 실행 — 실제 파드가 하는 그대로($0 이 설치 폴더 밖)
cp "$SRC" "$WORK/elsewhere/pre_start.sh"; chmod +x "$WORK/elsewhere/pre_start.sh"
FACE_RENDER_ROOT="$ROOT" "$WORK/elsewhere/pre_start.sh"
for _ in $(seq 1 50); do [ -f "$ROOT/order.txt" ] && grep -q START_RAN "$ROOT/order.txt" && break; sleep 0.2; done

order="$(tr '\n' ' ' < "$ROOT/order.txt" 2>/dev/null)"
case "$order" in
  "BOOTSTRAP_RAN START_RAN "*) ok "복사 실행돼도 설치 폴더의 bootstrap → start 순서로 돈다" ;;
  *) ng "순서가 틀리다: [$order]" ;;
esac
grep -q "bootstrap=$ROOT/bootstrap.sh" "$ROOT/logs/boot.log" \
  && ok "부팅 로그에 어떤 bootstrap 을 골랐는지 남는다" \
  || ng "부팅 로그에 bootstrap 경로가 없다: $(cat "$ROOT/logs/boot.log" 2>/dev/null)"

# bootstrap 이 없으면 — 조용히 건너뛰지 말고 이유를 남긴다
ROOT2="$WORK/root2/face_render"; mkdir -p "$ROOT2"
cat > "$ROOT2/start.sh" <<'S'
#!/usr/bin/env bash
echo "START_RAN" >> "$(dirname "$0")/order.txt"
S
chmod +x "$ROOT2/start.sh"
FACE_RENDER_ROOT="$ROOT2" "$WORK/elsewhere/pre_start.sh"
sleep 1
grep -q "bootstrap.sh 를 못 찾았다" "$ROOT2/logs/boot.log" \
  && ok "bootstrap 을 못 찾으면 사유가 로그에 남는다" \
  || ng "못 찾은 사유가 없다: $(cat "$ROOT2/logs/boot.log" 2>/dev/null)"

# start.sh: venv 가 없으면 시스템 python 으로 떨어지지 않고 종료한다
START="$HERE/server/deploy/face_render/start.sh"
ROOT3="$WORK/root3/face_render"; mkdir -p "$ROOT3/code"
out="$(FACE_RENDER_ROOT="$ROOT3" FACE_RENDER_TOKEN="$(printf 'x%.0s' {1..40})" bash "$START" 2>&1)"; rc=$?
if [ "$rc" = "78" ] && echo "$out" | grep -q "bootstrap 이 돌지 않았다"; then
  ok "venv 가 없으면 78 로 끝내고 이유를 남긴다(시스템 python 폴백 없음)"
else
  ng "venv 없을 때 동작이 다르다 rc=$rc: $out"
fi

echo "1..$((PASS+FAIL))"
[ "$FAIL" -eq 0 ] || exit 1
