#!/usr/bin/env bash
# 파드 부팅 훅 — RunPod 기본 이미지의 /start.sh 가 sshd 를 세우기 **전에** 실행한다.
# 이미지의 start.sh 는 `/pre_start.sh` 가 있으면 동기로 실행하므로, 여기서는
# 얼굴 렌더 서비스를 **백그라운드로** 띄우고 즉시 반환한다(막으면 ssh 가 8분 늦게 뜬다).
#
# 파드 args 에 이 줄을 넣어 볼륨의 이 파일을 컨테이너로 복사한다(컨테이너 디스크는 매번 초기화):
#   bash -c 'cp -f /workspace/face_render/pre_start.sh /pre_start.sh 2>/dev/null || true; exec /start.sh'
#
# FACE_RENDER_TOKEN 은 파드 env(운영에서는 RunPod Secret 참조)로 들어와 있어야 한다.
set -u
# 볼륨을 쓰지 않는다(2026-09-10 실측: 볼륨 적재 472초로 이득 없음) → 컨테이너 디스크 /root.
ROOT="${FACE_RENDER_ROOT:-/root/face_render}"
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$ROOT/logs"
BOOT="$ROOT/logs/boot.log"

# bootstrap → start 순서. 둘 다 백그라운드 한 줄기로 — pre_start 가 막으면 ssh 가 그만큼 늦게 뜬다.
nohup bash -c "
  set -e
  if [ -x '$SELF_DIR/bootstrap.sh' ]; then '$SELF_DIR/bootstrap.sh' >> '$BOOT' 2>&1; fi
  if [ -x '$ROOT/start.sh' ]; then exec '$ROOT/start.sh' >> '$BOOT' 2>&1; fi
  echo 'pre_start: start.sh 없음 — 서비스는 뜨지 않는다' >> '$BOOT'
" >/dev/null 2>&1 &
echo "pre_start: bootstrap+service launched (pid $!)" >> "$BOOT"
