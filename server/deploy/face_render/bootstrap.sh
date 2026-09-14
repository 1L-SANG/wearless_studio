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
# ★ HF_HOME 은 **덮어쓴다**(`:-` 아님). 베이스 이미지가 HF_HOME=/workspace/.cache/huggingface/ 를
#   박아 두는데 /workspace 는 MooseFS 네트워크 볼륨(20GB)이라 53.8GiB 가중치가 안 들어간다
#   — 2026-09-13 파드 r5lk3ysffgayyk: `OSError: [Errno 122] Disk quota exceeded`(컨테이너 디스크는
#   46GB 남아 있었다. 받던 곳이 달랐다). 이 파일 위쪽 주석의 "/workspace 금지" 가 env 한 줄로 뚫렸다.
export HF_HOME="$ROOT/hf"
# ★ xet 전송을 끈다. huggingface_hub 1.x 는 `[cli,hf_transfer]` extra 를 더는 제공하지 않아
#   hf_transfer 가 설치되지 않고 기본이 xet 으로 떨어지는데, 그 xet 이
#   `File reconstruction error: Internal Writer Error` 로 35파일 중 29개에서 터졌다(캐시 0바이트).
#   끄면 같은 54GB 를 **114초**에 받는다(실측). HF_HUB_ENABLE_HF_TRANSFER 는 1.x 에 상수 자체가
#   없어(hasattr=False) 값을 줘도 무시되므로 설정하지 않는다.
export HF_HUB_DISABLE_XET=1
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
  # ★ 버전을 고정한다. 파드는 매번 새로 깔리므로 고정하지 않으면 **아무 날에나** 조합이 바뀐다.
  #   2026-09-13 에 실제로 그랬다: transformers 5.17.0 · opencv 5.0.0.93 · huggingface_hub 1.31.0 이
  #   한꺼번에 올라왔고, 그중 hub 1.x 가 다운로드를 깨뜨렸다(extra 소멸 → xet 기본화 → 크래시).
  #
  #   아래 값은 **그날 실제로 끝까지 돌아간 조합 그대로**다 — 가중치 54GB 수신(114초, xet off),
  #   파이프라인 적재 8.5초, 렌더 200 OK(50.6초), /upscale 200 OK 까지 확인했다. hub 를 0.x 로
  #   내리는 것도 고려했지만 diffusers 0.40 · transformers 5.x 와의 조합을 확인하지 않았다 —
  #   검증한 조합을 박는 쪽이 낫다. 올릴 때는 파드 1대로 부팅을 확인하고 올린다.
  #   hf_transfer 는 요청하지 않는다: hub 1.x 에 HF_HUB_ENABLE_HF_TRANSFER 상수 자체가 없다.
  "$ROOT/venv/bin/pip" install -q \
      "diffusers==0.40.0" "transformers==5.17.0" "accelerate==1.15.0" "peft==0.20.0" \
      "safetensors==0.8.0" "huggingface_hub==1.31.0" \
      "fastapi==0.141.1" "uvicorn==0.52.4" "httpx==0.28.1" \
      "opencv-python-headless==5.0.0.93" >>"$LOG" 2>&1
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

# ── 얼굴 크롭 확대기 가중치(RealESRGAN x4plus) ─────────────────────────────
# 얼굴 1024² 크롭만 키우는 데 쓴다(사진 전체는 건드리지 않는다 — 옷 픽셀이 바뀐다).
# ★ 없어도 서비스는 뜬다. 그때는 /upscale 이 503 을 돌려주고 호출자가 Lanczos 로 간다 —
#   그래서 여기서 실패해도 exit 하지 않는다(확대기는 마감 개선이지 필수 경로가 아니다).
ESRGAN_URL="https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"
ESRGAN_SHA256="4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1"
ESRGAN_PATH="$ROOT/weights/RealESRGAN_x4plus.pth"
mkdir -p "$ROOT/weights"
if [ "$(sha256sum "$ESRGAN_PATH" 2>/dev/null | cut -d' ' -f1)" = "$ESRGAN_SHA256" ]; then
  say "esrgan 가중치 있음 — 건너뜀"
else
  say "esrgan 가중치 내려받기"
  if curl -fsSL --max-time 300 -o "$ESRGAN_PATH.part" "$ESRGAN_URL"; then
    got="$(sha256sum "$ESRGAN_PATH.part" | cut -d' ' -f1)"
    if [ "$got" = "$ESRGAN_SHA256" ]; then
      mv -f "$ESRGAN_PATH.part" "$ESRGAN_PATH"
      say "esrgan 가중치 설치 완료($ESRGAN_SHA256)"
    else
      rm -f "$ESRGAN_PATH.part"
      say "esrgan sha256 불일치 — 버린다(확대 없이 간다)"
    fi
  else
    rm -f "$ESRGAN_PATH.part"
    say "esrgan 내려받기 실패 — 확대 없이 간다"
  fi
fi
t3=$(date +%s); say "esrgan $((t3-t2))s"
say "총 $((t3-t0))s"

# MEASURE (2026-09-11, H100 80GB SECURE · 224 vCPU · 컨테이너 디스크 100GB):
#   venv+pip        20초
#   hf_transfer off 38초 (53.8GiB)
#   hf_transfer on  39초 (53.8GiB)
#   → **이 호스트에서는 hf_transfer 이득이 없다.** 앞선 회차의 390초는 라이브러리가 아니라
#     그 파드의 회선이었다(같은 명령, 같은 용량). 켜 두는 비용도 없어 기본 on 으로 둔다.
