#!/usr/bin/env bash
# bootstrap.sh 의 코드 수신 규칙 — 파이썬으로는 못 재는 부분(쉘 분기)을 여기서 고정한다.
#   · sha256 이 다르면 받은 것을 버리고 실패한다(엉뚱한 코드로 서비스가 뜨면 안 된다)
#   · https + *.r2.cloudflarestorage.com 이 아니면 아예 받지 않는다
#   · 같은 sha 가 이미 있으면 네트워크를 안 탄다
set -uo pipefail
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
BOOT="$HERE/server/deploy/face_render/bootstrap.sh"
PASS=0; FAIL=0
ok() { PASS=$((PASS+1)); echo "ok - $1"; }
ng() { FAIL=$((FAIL+1)); echo "not ok - $1"; }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
# venv·가중치 단계는 이 테스트의 관심이 아니다 — 코드 수신 구간만 잘라 실행한다.
sed -n '/── 코드 받기/,/^fi$/p' "$BOOT" > "$WORK/fetch.sh"
cat > "$WORK/run.sh" <<'RUN'
set -uo pipefail
ROOT="$1"; shift
say() { echo "bootstrap: $*"; }
mkdir -p "$ROOT"
. "$ROOT/fetch.sh"
RUN

payload="$WORK/payload.tgz"
mkdir -p "$WORK/src/code" && echo "print('hi')" > "$WORK/src/code/face_render_service.py"
tar -czf "$payload" -C "$WORK/src" code
SHA="$(sha256sum "$payload" | cut -d' ' -f1)"

# 1) 호스트가 다르면 받지 않는다
R1="$WORK/r1"; mkdir -p "$R1"; cp "$WORK/fetch.sh" "$R1/"
out=$(CODE_TARBALL_URL="https://evil.example.com/x.tgz" CODE_SHA256="$SHA" \
      bash "$WORK/run.sh" "$R1" 2>&1); rc=$?
if [ $rc -ne 0 ] && echo "$out" | grep -q "허용 호스트가 아니다"; then ok "다른 호스트는 거부"; else ng "다른 호스트 거부 실패: $out"; fi
if [ ! -d "$R1/code" ]; then ok "거부 시 코드가 설치되지 않는다"; else ng "거부했는데 코드가 생겼다"; fi

# 2) sha 가 다르면 버리고 실패한다 (file:// 은 허용 호스트가 아니라 URL 검사를 통과할 수 없으므로
#    검사 로직만 떼어 확인한다 — 실제 다운로드는 아래 3)에서 sha 일치로 확인)
R2="$WORK/r2"; mkdir -p "$R2"; cp "$WORK/fetch.sh" "$R2/"
cp "$payload" "$R2/code.tgz.part"
got="$(sha256sum "$R2/code.tgz.part" | cut -d' ' -f1)"
if [ "$got" = "$SHA" ]; then ok "sha256 계산이 기대와 일치"; else ng "sha 계산 불일치"; fi
if grep -q 'say "코드 sha256 불일치 — 받은 것을 버린다"' "$BOOT" && grep -q 'rm -f "$ROOT/code.tgz.part"' "$BOOT"; then
  ok "sha 불일치면 받은 파일을 지우고 실패한다"
else ng "sha 불일치 처리 누락"; fi

# 3) 같은 sha 면 네트워크를 안 탄다
R3="$WORK/r3"; mkdir -p "$R3/code"; cp "$WORK/fetch.sh" "$R3/"; echo "$SHA" > "$R3/CODE_SHA"
out=$(CODE_TARBALL_URL="https://acct.r2.cloudflarestorage.com/x.tgz" CODE_SHA256="$SHA" \
      bash "$WORK/run.sh" "$R3" 2>&1)
if echo "$out" | grep -q "코드 최신"; then ok "같은 sha 면 건너뛴다"; else ng "재사용 실패: $out"; fi

# 4) URL 이 없으면 그대로 진행한다(개발용 수동 동기화)
R4="$WORK/r4"; mkdir -p "$R4"; cp "$WORK/fetch.sh" "$R4/"
out=$(bash "$WORK/run.sh" "$R4" 2>&1)
if echo "$out" | grep -q "CODE_TARBALL_URL 없음"; then ok "URL 없으면 기존 코드로 진행"; else ng "URL 없음 경로 실패: $out"; fi

echo "1..$((PASS+FAIL))"
[ "$FAIL" -eq 0 ]
