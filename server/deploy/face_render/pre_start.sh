#!/usr/bin/env bash
# 파드 부팅 훅 — RunPod 기본 이미지의 /start.sh 가 sshd 를 세우기 **전에** 실행한다.
# 이미지의 start.sh 는 `/pre_start.sh` 가 있으면 동기로 실행하므로, 여기서는
# 얼굴 렌더 서비스를 **백그라운드로** 띄우고 즉시 반환한다(막으면 ssh 가 8분 늦게 뜬다).
#
# 파드 args(POD_ARGS)가 이 파일을 **/pre_start.sh 로 복사해** 실행한다 — 즉 실행 시점의 $0 은
# 설치 폴더가 아니라 "/" 다.
#
# ★ 그래서 bootstrap 을 $0 기준으로 찾으면 안 된다. 예전 판은 SELF_DIR=$(dirname $0)="/" 로 잡아
#   /bootstrap.sh 를 찾다 없으니 **bootstrap 을 통째로 건너뛰었고**, venv 가 없는 채로 start.sh 가
#   시스템 python 으로 떠서 "No module named uvicorn" 으로 죽었다(2026-09-11 실측).
#   설치 폴더는 POD_ARGS 의 R 과 같은 고정값을 쓰고, 거기 bootstrap.sh 가 없을 때만 $0 옆을 본다.
#
# FACE_RENDER_TOKEN 은 파드 env(운영에서는 RunPod Secret 참조)로 들어와 있어야 한다.
set -u
# 볼륨을 쓰지 않는다(2026-09-10 실측: 볼륨 적재 472초로 이득 없음) → 컨테이너 디스크 /root.
ROOT="${FACE_RENDER_ROOT:-/root/face_render}"
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
# 설치 폴더가 정본. 거기 없을 때만 $0 옆(개발용 수동 실행)을 대안으로 쓴다.
if [ -x "$ROOT/bootstrap.sh" ]; then
  BOOTSTRAP="$ROOT/bootstrap.sh"
elif [ -x "$SELF_DIR/bootstrap.sh" ]; then
  BOOTSTRAP="$SELF_DIR/bootstrap.sh"
else
  BOOTSTRAP=""
fi
mkdir -p "$ROOT/logs"
BOOT="$ROOT/logs/boot.log"

# bootstrap → start 순서. 둘 다 백그라운드 한 줄기로 — pre_start 가 막으면 ssh 가 그만큼 늦게 뜬다.
if [ -z "$BOOTSTRAP" ]; then
  echo "pre_start: bootstrap.sh 를 못 찾았다(ROOT=$ROOT, SELF_DIR=$SELF_DIR)" >> "$BOOT"
fi
# ★ bootstrap 이 죽으면 start.sh 로 넘어가지 않는다(그게 맞다 — 가중치 없이 띄울 이유가 없다).
#   문제는 **그 사실이 아무 데도 안 남던 것**이다: 2026-09-13 파드는 34분 동안 desiredStatus=RUNNING
#   인 채로 uvicorn 이 없었고, boot.log 마지막 줄은 "가중치 내려받기" 였다. 종료 코드와 로그 꼬리를
#   남겨 다음 사람이 ssh 없이 원인을 본다.
nohup bash -c "
  if [ -n '$BOOTSTRAP' ]; then
    # ★ \$? 는 \`if ! cmd\` 안에서 0 으로 리셋된다 — 따로 받는다.
    '$BOOTSTRAP' >> '$BOOT' 2>&1
    code=\$?
    if [ \$code -ne 0 ]; then
      tail -n 8 '$BOOT' | sed 's/^/pre_start:   /' > '$BOOT.tail'
      echo \"pre_start: bootstrap 실패(exit \$code) — 서비스를 띄우지 않는다\" >> '$BOOT'
      cat '$BOOT.tail' >> '$BOOT'; rm -f '$BOOT.tail'
      exit \$code
    fi
  fi
  if [ -x '$ROOT/start.sh' ]; then exec '$ROOT/start.sh' >> '$BOOT' 2>&1; fi
  echo 'pre_start: start.sh 없음 — 서비스는 뜨지 않는다' >> '$BOOT'
" >/dev/null 2>&1 &
echo "pre_start: bootstrap=${BOOTSTRAP:-none} root=$ROOT launched (pid $!)" >> "$BOOT"
