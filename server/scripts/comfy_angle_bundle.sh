#!/usr/bin/env bash
# 각도 교체(ComfyUI) 파드에 올릴 코드 묶음을 만든다 (CI·로컬 공용).
#
# 얼굴 렌더 묶음(face_render_bundle.sh)과 같은 규약이다: R2 비공개 버킷에 올려 두고, 어댑터가
# 파드를 켜거나 만들 때 그때그때 presigned GET 을 env 로 넣어 준다(파드에 R2 자격증명 없음).
#
# ★ 묶음 안의 **자리**가 파드 부팅 계약이다(face_autoscale.POD_BOOT_SCRIPT):
#     deploy/*.sh  → 풀린 뒤 $R/ 로 복사되고, 그중 pre_start.sh 가 /pre_start.sh 가 된다
#     그 외        → $R/ 아래 그대로
#   comfy_setup.sh 와 auth_proxy.py 는 $R/ 에서 절대경로로 불리므로(pre_start.sh 참고)
#   **묶음 최상단**에 둔다. 2026-09-20 옆·뒤 실행이 통과한 배치가 이것이다 — 바꾸지 않는다.
#
# usage: server/scripts/comfy_angle_bundle.sh [OUT_DIR]
#   → OUT_DIR/comfy_angle.tgz 와 그 sha256 을 만들고, sha256 을 표준출력 마지막 줄로 찍는다.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"      # server/
SRC="$HERE/deploy/comfy_angle"
OUT="${1:-$HERE/../dist}"
mkdir -p "$OUT"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/deploy"
cp "$SRC/comfy_setup.sh" "$SRC/auth_proxy.py" "$STAGE/"
cp "$SRC/pre_start.sh" "$STAGE/deploy/"
chmod +x "$STAGE/comfy_setup.sh" "$STAGE/deploy/pre_start.sh"

# 재현 가능한 tar — 같은 코드면 같은 sha 가 나와야 파드가 "이미 최신"을 판단할 수 있다.
# (mtime·gzip 헤더까지 고정하는 이유는 face_render_bundle.sh 주석에 있다.)
find "$STAGE" -exec touch -t 200001010000 {} +
COPYFILE_DISABLE=1 tar --format=ustar --numeric-owner --owner=0 --group=0 \
    -cf - -C "$STAGE" auth_proxy.py comfy_setup.sh deploy | gzip -n > "$OUT/comfy_angle.tgz"
tar tzf "$OUT/comfy_angle.tgz" > /dev/null            # 올리기 전에 아카이브부터 검증

if command -v sha256sum > /dev/null; then
  SHA="$(sha256sum "$OUT/comfy_angle.tgz" | cut -d' ' -f1)"
else
  SHA="$(shasum -a 256 "$OUT/comfy_angle.tgz" | cut -d' ' -f1)"
fi
printf '%s\n' "$SHA" > "$OUT/comfy_angle.tgz.sha256"
echo "bundle: $OUT/comfy_angle.tgz" >&2
echo "$SHA"
