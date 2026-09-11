#!/usr/bin/env bash
# 얼굴 렌더 코드를 켜져 있는 파드의 **볼륨**으로 올린다 (맥에서 실행).
#
# 올리는 것: face_render_service.py · 서비스가 import 하는 app 모듈만 · start.sh · VERSION(git sha).
# 가중치·venv·HF 캐시는 건드리지 않는다(볼륨에 이미 있다).
#
# ★ app/__init__.py 를 그대로 올리면 안 된다 — 레포의 그것은 서버 전체를 끌어온다.
#   서비스가 필요한 건 `app.agents.face_identity_qwen` 하나뿐이라 **빈 __init__.py** 를 만든다.
# ★ 파드 명령은 직접 SSH 로만 보낸다. 중계(ssh.runpod.io)는 명령 실행이 안 된다(2026-09-10 실측).
#
# usage: POD_HOST=1.2.3.4 POD_PORT=12345 server/scripts/face_render_sync.sh
#        [SSH_KEY=~/.ssh/id_ed25519] [FACE_RENDER_ROOT=/workspace/face_render]
set -euo pipefail

HOST="${POD_HOST:?POD_HOST 가 필요하다 (파드 publicIp)}"
PORT="${POD_PORT:?POD_PORT 가 필요하다 (파드의 22 매핑 포트 — 재시작마다 바뀐다)}"
KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
ROOT="${FACE_RENDER_ROOT:-/workspace/face_render}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"      # server/
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -p "$PORT" "root@$HOST")

VERSION="$(git -C "$HERE/.." rev-parse HEAD)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/code/app/agents"
cp "$HERE/face_render_service.py" "$STAGE/code/"
cp "$HERE/app/agents/face_identity_qwen.py" "$STAGE/code/app/agents/"
: > "$STAGE/code/app/__init__.py"
: > "$STAGE/code/app/agents/__init__.py"
cp "$HERE/deploy/face_render/start.sh" "$STAGE/start.sh"
cp "$HERE/deploy/face_render/pre_start.sh" "$STAGE/pre_start.sh"
printf '%s\n' "$VERSION" > "$STAGE/VERSION"
printf '%s\n' "$VERSION" > "$STAGE/code/VERSION"
chmod +x "$STAGE/start.sh"

# COPYFILE_DISABLE: macOS tar 이 ._AppleDouble 을 같이 넣는 것을 막는다.
COPYFILE_DISABLE=1 tar czf "$STAGE/sync.tgz" -C "$STAGE" code start.sh pre_start.sh VERSION
tar tzf "$STAGE/sync.tgz" > /dev/null           # 올리기 전에 아카이브부터 검증
scp -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -P "$PORT" \
    "$STAGE/sync.tgz" "root@$HOST:/tmp/face_render_sync.tgz"
"${SSH[@]}" "set -e
  mkdir -p '$ROOT'
  tar tzf /tmp/face_render_sync.tgz > /dev/null
  rm -rf '$ROOT/code'
  # --no-same-owner: 네트워크 볼륨(MooseFS)은 맥 tar 의 uid/gid 를 못 준다(chown 거부).
  tar --no-same-owner -xzf /tmp/face_render_sync.tgz -C '$ROOT'
  rm -f /tmp/face_render_sync.tgz
  chmod +x '$ROOT/start.sh' '$ROOT/pre_start.sh'
  ls -la '$ROOT' '$ROOT/code'
  echo VERSION=\$(cat '$ROOT/VERSION')"
echo "synced $VERSION → $HOST:$ROOT"
