#!/usr/bin/env bash
# 얼굴 렌더 파드에 올릴 코드 묶음을 만든다 (CI·로컬 공용).
#
# 자동 생성된 파드에는 아무 코드도 없다 — 컨테이너 디스크는 매번 초기화되고, 맥에서 밀어 넣는
# 건 사람 손이다. 그래서 이 묶음을 R2 비공개 버킷에 올려 두고, 어댑터가 파드를 켜거나 만들 때
# **그때그때 presigned GET** 을 env 로 넣어 준다(LoRA 와 같은 방식 — 파드에 R2 자격증명 없음).
#
# 묶는 것: face_render_service.py · 서비스가 import 하는 app 모듈 · deploy/face_render/*.
#   ★ app/__init__.py 는 레포 것을 쓰면 서버 전체를 끌어온다 — 빈 파일로 만든다.
#
# usage: server/scripts/face_render_bundle.sh [OUT_DIR]
#   → OUT_DIR/face_render.tgz 와 그 sha256 을 만들고, sha256 을 표준출력 마지막 줄로 찍는다.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"      # server/
OUT="${1:-$HERE/../dist}"
mkdir -p "$OUT"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/code/app/agents" "$STAGE/deploy"
cp "$HERE/face_render_service.py" "$STAGE/code/"
cp "$HERE/app/agents/face_identity_qwen.py" "$STAGE/code/app/agents/"
: > "$STAGE/code/app/__init__.py"
: > "$STAGE/code/app/agents/__init__.py"
cp "$HERE/deploy/face_render/start.sh" "$HERE/deploy/face_render/pre_start.sh" \
   "$HERE/deploy/face_render/bootstrap.sh" "$STAGE/deploy/"
chmod +x "$STAGE/deploy/"*.sh

# 재현 가능한 tar — 같은 코드면 같은 sha 가 나와야 파드가 "이미 최신"을 판단할 수 있다.
COPYFILE_DISABLE=1 tar --format=ustar --numeric-owner --owner=0 --group=0 \
    -czf "$OUT/face_render.tgz" -C "$STAGE" code deploy
tar tzf "$OUT/face_render.tgz" > /dev/null            # 올리기 전에 아카이브부터 검증

if command -v sha256sum > /dev/null; then
  SHA="$(sha256sum "$OUT/face_render.tgz" | cut -d' ' -f1)"
else
  SHA="$(shasum -a 256 "$OUT/face_render.tgz" | cut -d' ' -f1)"
fi
printf '%s\n' "$SHA" > "$OUT/face_render.tgz.sha256"
echo "bundle: $OUT/face_render.tgz" >&2
echo "$SHA"
