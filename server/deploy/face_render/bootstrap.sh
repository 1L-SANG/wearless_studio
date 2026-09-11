#!/usr/bin/env bash
# 빈 파드를 얼굴 렌더 서비스가 돌 수 있는 상태로 만든다 (멱등).
#
# 컨테이너 디스크는 재시작마다 초기화되므로 파드를 켤 때마다 이 스크립트가 돈다.
# 이미 갖춰져 있으면(venv·가중치·코드) 각 단계를 건너뛴다.
#
# ★ 다운로드는 hf_transfer 로 한다(HF_HUB_ENABLE_HF_TRANSFER=1). 파이썬 HTTP 한 줄기로 받으면
#   53.8GiB 가 6분 넘게 걸린다 — 실측 근거는 아래 MEASURE 주석.
# ★ 토큰은 파드 env 하나뿐이다. 여기서 파일로 만들지 않는다.
set -euo pipefail

ROOT="${FACE_RENDER_ROOT:-/root/face_render}"
MODEL_ID="${FACE_RENDER_MODEL_ID:-Qwen/Qwen-Image-Edit-2509}"
export HF_HOME="${HF_HOME:-$ROOT/hf}"
export HF_HUB_ENABLE_HF_TRANSFER="${HF_HUB_ENABLE_HF_TRANSFER:-1}"
mkdir -p "$ROOT"/{hf,code,loras,logs}
LOG="$ROOT/logs/bootstrap-$(date -u +%Y%m%dT%H%M%SZ).log"
say() { echo "bootstrap: $*" | tee -a "$LOG"; }

t0=$(date +%s)

# ── 코드 받기 ─────────────────────────────────────────────────────────────
# 자동 생성된 파드에는 코드가 없다. 어댑터가 파드를 켜거나 만들 때 CODE_TARBALL_URL(만료 15분
# presigned GET)과 CODE_SHA256 을 env 로 넣어 준다 — 파드에 R2 자격증명을 두지 않기 위해서다.
# 이미 같은 sha 의 코드가 있으면 건너뛴다. 값이 없으면(개발용 수동 동기화) 그대로 진행한다.
if [ -n "${CODE_TARBALL_URL:-}" ] && [ -n "${CODE_SHA256:-}" ]; then
  if [ "$(cat "$ROOT/CODE_SHA" 2>/dev/null || true)" = "$CODE_SHA256" ] && [ -d "$ROOT/code" ]; then
    say "코드 최신($CODE_SHA256) — 건너뜀"
  else
    case "$CODE_TARBALL_URL" in
      https://*.r2.cloudflarestorage.com/*) : ;;
      *) say "CODE_TARBALL_URL 이 허용 호스트가 아니다 — 코드를 받지 않는다"; exit 78 ;;
    esac
    say "코드 내려받기"
    curl -fsSL --max-time 120 -o "$ROOT/code.tgz.part" "$CODE_TARBALL_URL"
    got="$(sha256sum "$ROOT/code.tgz.part" | cut -d' ' -f1)"
    if [ "$got" != "$CODE_SHA256" ]; then
      rm -f "$ROOT/code.tgz.part"
      say "코드 sha256 불일치 — 받은 것을 버린다"
      exit 78
    fi
    rm -rf "$ROOT/code" "$ROOT/deploy"
    tar --no-same-owner -xzf "$ROOT/code.tgz.part" -C "$ROOT"
    rm -f "$ROOT/code.tgz.part"
    # 묶음 안의 deploy/*.sh 가 이 스크립트들의 정본이다(다음 부팅부터 그게 돈다).
    cp -f "$ROOT/deploy/start.sh" "$ROOT/deploy/pre_start.sh" "$ROOT/deploy/bootstrap.sh" "$ROOT/" 2>/dev/null || true
    chmod +x "$ROOT"/*.sh 2>/dev/null || true
    printf '%s\n' "$CODE_SHA256" > "$ROOT/CODE_SHA"
    printf '%s\n' "$CODE_SHA256" > "$ROOT/VERSION"
    say "코드 설치 완료($CODE_SHA256)"
  fi
else
  say "CODE_TARBALL_URL 없음 — 이미 올라와 있는 코드를 쓴다(개발용 수동 동기화)"
fi

if [ ! -x "$ROOT/venv/bin/python" ]; then
  say "venv 생성"
  # --system-site-packages: 베이스 이미지의 torch(2.8+cu128)를 그대로 쓴다. 다시 받으면 3GB·수 분.
  python3 -m venv --system-site-packages "$ROOT/venv"
  "$ROOT/venv/bin/pip" install -q --upgrade pip >>"$LOG" 2>&1
  "$ROOT/venv/bin/pip" install -q diffusers transformers accelerate peft safetensors \
      fastapi uvicorn httpx opencv-python-headless "huggingface_hub[cli,hf_transfer]" >>"$LOG" 2>&1
else
  say "venv 있음 — 건너뜀"
fi
t1=$(date +%s); say "venv $((t1-t0))s"

# 가중치: hf CLI 가 이미 받은 건 건너뛴다(파일 단위 재개).
if [ -z "$("$ROOT/venv/bin/python" - <<'PY' 2>/dev/null
import os
from huggingface_hub import scan_cache_dir
try:
    hit = any("Qwen-Image-Edit-2509" in r.repo_id for r in scan_cache_dir().repos)
except Exception:
    hit = False
print("ok" if hit else "")
PY
)" ]; then
  say "가중치 내려받기 (hf_transfer=$HF_HUB_ENABLE_HF_TRANSFER)"
  "$ROOT/venv/bin/hf" download "$MODEL_ID" >>"$LOG" 2>&1
else
  say "가중치 있음 — 건너뜀"
fi
t2=$(date +%s); say "weights $((t2-t1))s"
say "총 $((t2-t0))s"

# MEASURE (2026-09-11, H100 80GB SECURE · 224 vCPU · 컨테이너 디스크 100GB):
#   venv+pip        20초
#   hf_transfer off 38초 (53.8GiB)
#   hf_transfer on  39초 (53.8GiB)
#   → **이 호스트에서는 hf_transfer 이득이 없다.** 앞선 회차의 390초는 라이브러리가 아니라
#     그 파드의 회선이었다(같은 명령, 같은 용량). 켜 두는 비용도 없어 기본 on 으로 둔다.
