#!/usr/bin/env bash
# 자동 생성 파드의 부팅 인자(POD_ARGS) — 파이썬으로는 못 재는 쉘 분기를 여기서 고정한다.
#
# ★ 이게 깨지면 자동 생성된 파드는 켜지기만 하고 서비스가 안 뜬다(요금만 나간다).
#   새 컨테이너에는 pre_start.sh 조차 없어서, 부팅 인자가 **직접** 묶음을 받아 깔아야 한다.
#   · 우리 R2 호스트가 아니면 받지 않는다
#   · sha256 이 다르면 깔지 않는다
#   · 성공하면 pre_start.sh 를 /pre_start.sh 로 놓고 이미지의 start.sh 로 넘어간다
set -uo pipefail
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
PASS=0; FAIL=0
ok() { PASS=$((PASS+1)); echo "ok - $1"; }
ng() { FAIL=$((FAIL+1)); echo "not ok - $1"; }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
# POD_ARGS 는 argv 배열(["bash","-c",<본문>]) — 실행할 셸 본문은 [2] 다.
PY_READ='from app.services.face_autoscale import POD_ARGS
assert isinstance(POD_ARGS, (list, tuple)) and POD_ARGS[0] == "bash" and POD_ARGS[1] == "-c"
print(POD_ARGS[2])'
ARGS_RAW="$("$HERE/server/.venv/bin/python" -c "$PY_READ" 2>/dev/null \
  || (cd "$HERE/server" && python3 -c "import sys; sys.path.insert(0,'.'); $PY_READ"))"
[ -n "$ARGS_RAW" ] || { echo "not ok - POD_ARGS 를 읽지 못했다(배열이 아닐 수 있다)"; exit 1; }

# 실제 경로(/root, /start.sh)는 테스트에서 쓸 수 없으므로 작업 디렉터리로 옮겨 실행한다.
mk_script() {                      # $1 = 실행 루트
  local root="$1"
  printf '%s' "$ARGS_RAW" \
    | sed -e "s#/root/face_render#$root#g" \
          -e "s#/tmp/face_render.tgz#$WORK/dl.tgz#g" \
          -e "s# /pre_start.sh # $root/installed_pre_start.sh #g" \
          -e "s#exec /start.sh#echo STARTED#"
}

# 묶음 = 실제 구조(code/ + deploy/*.sh)
mkdir -p "$WORK/src/code" "$WORK/src/deploy"
echo "print('svc')" > "$WORK/src/code/face_render_service.py"
echo "echo pre_start" > "$WORK/src/deploy/pre_start.sh"
echo "echo boot" > "$WORK/src/deploy/bootstrap.sh"
echo "echo start" > "$WORK/src/deploy/start.sh"
tar -czf "$WORK/payload.tgz" -C "$WORK/src" code deploy
SHA="$(sha256sum "$WORK/payload.tgz" | cut -d' ' -f1)"

# curl 대역 — 무슨 URL 이든 -o 로 지정된 곳에 묶음을 놓는다(네트워크 없이 분기만 본다).
mkdir -p "$WORK/bin"
cat > "$WORK/bin/curl" <<CURL
#!/usr/bin/env bash
out=""
while [ \$# -gt 0 ]; do case "\$1" in -o) out="\$2"; shift 2;; *) shift;; esac; done
[ -n "\$out" ] && cp "$WORK/payload.tgz" "\$out"
CURL
chmod +x "$WORK/bin/curl"
export PATH="$WORK/bin:$PATH"

run_case() {                        # $1 root, $2 url, $3 sha
  local root="$1"; mkdir -p "$root"
  mk_script "$root" > "$root/args.sh"
  CODE_TARBALL_URL="$2" CODE_SHA256="$3" bash "$root/args.sh" 2>&1
}

# 1) 우리 R2 가 아니면 받지 않는다
R1="$WORK/r1"
out="$(run_case "$R1" "https://evil.example.com/x.tgz" "$SHA")"
if [ ! -e "$R1/installed_pre_start.sh" ] && [ ! -d "$R1/code" ]; then
  ok "다른 호스트면 아무것도 깔지 않는다"
else ng "다른 호스트인데 코드가 깔렸다: $out"; fi
echo "$out" | grep -q STARTED && ok "거부해도 이미지 start.sh 로 넘어간다(ssh 는 뜬다)" \
  || ng "거부 시 start.sh 로 안 넘어갔다"

# 2) sha 가 다르면 깔지 않는다
R2="$WORK/r2"
out="$(run_case "$R2" "https://acct.r2.cloudflarestorage.com/b/face_render/x.tgz" "0000")"
if [ ! -e "$R2/installed_pre_start.sh" ]; then ok "sha 불일치면 깔지 않는다"
else ng "sha 가 달라도 깔렸다: $out"; fi

# 3) 정상 — 묶음을 깔고 부팅 훅을 놓고 넘어간다
R3="$WORK/r3"
out="$(run_case "$R3" "https://acct.r2.cloudflarestorage.com/b/face_render/x.tgz" "$SHA")"
[ -e "$R3/installed_pre_start.sh" ] && ok "부팅 훅이 설치된다" || ng "부팅 훅이 없다: $out"
[ -d "$R3/code" ] && ok "서비스 코드가 풀린다" || ng "code/ 가 없다"
[ "$(cat "$R3/CODE_SHA" 2>/dev/null)" = "$SHA" ] && ok "CODE_SHA 를 남긴다(bootstrap 이 재다운로드를 건너뛴다)" \
  || ng "CODE_SHA 가 다르다"
[ "$(cat "$R3/VERSION" 2>/dev/null)" = "$SHA" ] && ok "VERSION 을 남긴다(healthz code_version)" || ng "VERSION 이 없다"
echo "$out" | grep -q STARTED && ok "설치 후 이미지 start.sh 로 넘어간다" || ng "start.sh 로 안 넘어갔다"
[ ! -e "$WORK/dl.tgz" ] && ok "받은 묶음은 지운다" || ng "임시 묶음이 남았다"

echo "1..$((PASS+FAIL))"
[ "$FAIL" -eq 0 ] || exit 1
