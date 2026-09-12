#!/usr/bin/env bash
# 얼굴 렌더 파드 시작 스크립트 — 네트워크 볼륨에 있는 venv·가중치·코드로 서비스를 띄운다.
#
# 구조(모두 /root/face_render 아래 — bootstrap.sh 가 만든다):
#   venv/   파이썬 가상환경(torch·diffusers 포함, 재시작해도 유지)
#   hf/     HF_HOME — Qwen 가중치 53.8GiB
#   code/   face_render_service.py + app/agents/*  (scripts/face_render_sync.sh 가 올린다)
#   loras/  FACE_RENDER_CACHE_DIR — 가중치 캐시(+ 옆에 .key 사이드카)
#   logs/   uvicorn 로그
#
# ★ 컨테이너 디스크(/root)는 재시작 때 초기화된다(2026-09-10 실측) — 유지돼야 하는 건 전부 볼륨에.
# ★ FACE_RENDER_TOKEN 은 파드 env 로 주입한다(운영에서는 RunPod Secret 참조).
#   토큰이 없으면 서비스가 fail-closed 로 뜬다(요청마다 503).
set -euo pipefail

# 볼륨 없음(2026-09-10 실측) — 컨테이너 디스크. bootstrap.sh 가 같은 경로를 만든다.
ROOT="${FACE_RENDER_ROOT:-/root/face_render}"
export HF_HOME="${HF_HOME:-$ROOT/hf}"
export FACE_RENDER_CACHE_DIR="${FACE_RENDER_CACHE_DIR:-$ROOT/loras}"
# 얼굴 크롭 확대기 가중치(bootstrap 이 받아 sha256 을 맞춰 둔다). 없으면 /upscale 이 503 →
# 호출자가 Lanczos 로 간다. FACE_RENDER_ROOT 를 옮겨도 따라가게 여기서 정해 준다.
export FACE_RENDER_ESRGAN_WEIGHTS="${FACE_RENDER_ESRGAN_WEIGHTS:-$ROOT/weights/RealESRGAN_x4plus.pth}"
PORT="${FACE_RENDER_PORT:-8000}"
mkdir -p "$ROOT"/{hf,loras,logs,weights}
LOG="$ROOT/logs/service-$(date -u +%Y%m%dT%H%M%SZ).log"

# 토큰은 **파드 env 하나뿐**이다(운영: {{ RUNPOD_SECRET_face_render_token }}).
# 볼륨 파일 폴백은 두지 않는다 — 볼륨에 토큰을 두면 그 파일이 곧 유출 지점이 된다.
# 비었거나 자리표시자면 서비스를 띄우지 않는다: 그대로 뜨면 그 고정 문자열이 토큰이 되고,
# 프록시 URL 은 이미 매니페스트에 커밋돼 있어 누구나 때릴 수 있다.
case "${FACE_RENDER_TOKEN:-}" in
  "" )
    echo "start.sh: FACE_RENDER_TOKEN 이 비어 있다 — 서비스를 띄우지 않는다" | tee -a "$LOG" >&2
    exit 78 ;;   # EX_CONFIG
  PLACEHOLDER* )
    echo "start.sh: FACE_RENDER_TOKEN 이 자리표시자다 — 서비스를 띄우지 않는다" | tee -a "$LOG" >&2
    exit 78 ;;
esac

if [ -f "$ROOT/VERSION" ]; then
  export FACE_RENDER_CODE_VERSION="$(cat "$ROOT/VERSION")"
fi

# 캐시에 가중치가 있으면 그 키로 미리 적재한다(기동 즉시 첫 컷을 받기 위해).
# 캐시가 비면 PRELOAD 없이 뜬다 — 기동 시점에는 presigned URL 이 없어 받을 방법이 없다.
if [ -z "${FACE_RENDER_PRELOAD_LORA:-}" ]; then
  newest_key="$(ls -t "$FACE_RENDER_CACHE_DIR"/*.key 2>/dev/null | head -1 || true)"
  if [ -n "$newest_key" ]; then
    export FACE_RENDER_PRELOAD_LORA="$(cat "$newest_key")"
  fi
fi

# ★ 시스템 python 으로 떨어지지 않는다. bootstrap 이 안 돌았으면 uvicorn·diffusers 가 없어서
#   "No module named uvicorn" 으로 죽는데, 그 오류는 원인(bootstrap 미실행)을 가린다.
#   이유를 부팅 로그에 남기고 종료한다(2026-09-11 실측).
if [ ! -x "$ROOT/venv/bin/python" ]; then
  echo "start.sh: venv 가 없다($ROOT/venv) — bootstrap 이 돌지 않았다. 서비스를 띄우지 않는다" \
    | tee -a "$LOG" >&2
  exit 78   # EX_CONFIG
fi
PY="$ROOT/venv/bin/python"

cd "$ROOT/code"
echo "start.sh: version=${FACE_RENDER_CODE_VERSION:-unknown} preload=${FACE_RENDER_PRELOAD_LORA:-none}" | tee -a "$LOG"
exec "$PY" -m uvicorn face_render_service:app --host 0.0.0.0 --port "$PORT" >>"$LOG" 2>&1
