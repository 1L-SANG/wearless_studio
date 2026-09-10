#!/usr/bin/env bash
# 얼굴 렌더 파드 시작 스크립트 — 네트워크 볼륨에 있는 venv·가중치·코드로 서비스를 띄운다.
#
# 볼륨 구조(모두 /workspace/face_render 아래):
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

ROOT="${FACE_RENDER_ROOT:-/workspace/face_render}"
export HF_HOME="${HF_HOME:-$ROOT/hf}"
export FACE_RENDER_CACHE_DIR="${FACE_RENDER_CACHE_DIR:-$ROOT/loras}"
PORT="${FACE_RENDER_PORT:-8000}"
mkdir -p "$ROOT"/{hf,loras,logs}
LOG="$ROOT/logs/service-$(date -u +%Y%m%dT%H%M%SZ).log"

# 토큰: 운영은 파드 env(RunPod Secret 참조)가 정본. 없으면 볼륨의 .token 파일을 쓴다
# (측정·개발용 — 값이 명령줄이나 API 응답에 실리지 않게 하려는 것).
if [ -z "${FACE_RENDER_TOKEN:-}" ] && [ -f "$ROOT/.token" ]; then
  FACE_RENDER_TOKEN="$(cat "$ROOT/.token")"
  export FACE_RENDER_TOKEN
fi

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

if [ -x "$ROOT/venv/bin/python" ]; then
  PY="$ROOT/venv/bin/python"
else
  echo "볼륨 venv 가 없다: $ROOT/venv — 먼저 만들어야 한다" >&2
  PY="$(command -v python3)"
fi

cd "$ROOT/code"
echo "start.sh: version=${FACE_RENDER_CODE_VERSION:-unknown} preload=${FACE_RENDER_PRELOAD_LORA:-none}" | tee -a "$LOG"
exec "$PY" -m uvicorn face_render_service:app --host 0.0.0.0 --port "$PORT" >>"$LOG" 2>&1
