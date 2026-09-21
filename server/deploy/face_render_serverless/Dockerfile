# 인물 LoRA 얼굴 렌더 Serverless 워커.
#
# 각도 교체(deploy/comfy_angle)와 같은 규율이다 — **가중치는 굽지 않는다.** Qwen-Image-Edit-2509
# bf16 이 53.8GiB 라 이미지에 넣으면 GitHub Actions 러너 디스크에 안 들어가고, 모델을 바꿀
# 때마다 다시 굽는다. 가중치는 네트워크 볼륨에 두고 HF_HOME 을 거기로 돌린다(handler.py).
#
#   /runpod-volume/hf/           HF 캐시 — Qwen/Qwen-Image-Edit-2509 (53.8GiB)
#   /runpod-volume/esrgan/       RealESRGAN x4plus (선택 — 없으면 Lanczos 로 간다)
#
# 각도 워커와 다른 점: 저쪽은 RunPod 공식 worker-comfyui 를 그대로 쓸 수 있었다(ComfyUI
# 워크플로라서). 얼굴 패스는 diffusers 파이프라인이라 핸들러를 우리가 싣는다. 대신 렌더
# 구현은 파드와 **같은 파일**(face_render_service.py)이다 — 복제하지 않는다.
#
# 빌드 컨텍스트는 `server/` 다. 파드가 R2 묶음으로 받던 코드를 여기서는 이미지에 굽는다.
#
#   docker build -f server/deploy/face_render_serverless/Dockerfile -t <레지스트리>/face-render:<태그> server
#
# 그 이미지로 Serverless 엔드포인트를 만들고 볼륨을 붙인 뒤, 엔드포인트 id 를
# FACE_IDENTITY_ENDPOINT_ID 로 넣는다. 카드는 80GB 이상 — A40(48GB)은 bf16 전체가 안 올라간다.

FROM runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404

# ★ 버전을 고정한다. 파드 bootstrap.sh 가 쓰는 조합 **그대로** — 2026-09-13 에 가중치 54GB
#   수신(114초) · 파이프라인 적재 8.5초 · 렌더 200 OK 까지 확인한 조합이다. 고정하지 않으면
#   아무 날에나 바뀐다(그날 transformers 5.17 · hub 1.31 이 한꺼번에 올라와 다운로드가 깨졌다).
#   올릴 때는 워커 1대로 콜드스타트를 확인하고 올린다.
RUN pip install --no-cache-dir \
        "diffusers==0.40.0" "transformers==5.17.0" "accelerate==1.15.0" "peft==0.20.0" \
        "safetensors==0.8.0" "huggingface_hub==1.31.0" \
        "fastapi==0.141.1" "httpx==0.28.1" "opencv-python-headless==5.0.0.93" \
        "runpod==1.7.7"

# xet 전송은 기본으로 꺼 둔다 — hub 1.x 의 기본 경로인데 54GB 수신에서 35파일 중 29개가
# `File reconstruction error` 로 터졌다(파드 실측). handler.py 도 같은 값을 넣지만, 볼륨을
# 채우러 이 이미지로 들어오는 사람이 핸들러를 안 거칠 수 있어 여기에도 둔다.
ENV HF_HUB_DISABLE_XET=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
# 렌더에 필요한 것만. app/ 은 face_identity_qwen 하나 때문에 들어가지만 순수 파이썬이라 가볍다.
COPY face_render_service.py face_esrgan.py /app/
COPY app /app/app
COPY deploy/face_render_serverless/handler.py /app/

# ★ CMD 가 아니라 이 한 줄이 워커다. handler.py 는 import 시점에 베이스 파이프라인을 올리고
#   그 뒤에 runpod.serverless.start() 로 잡을 받는다 — 순서가 바뀌면 "준비됨"으로 잡을 받아
#   놓고 적재를 시작하게 되고, 그 시간이 셀러 대기로 들어간다.
CMD ["python", "-u", "handler.py"]
