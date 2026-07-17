# Wearless Studio API — FastAPI (Python 3.12, uv). Build context = server/.
FROM python:3.12-slim

# uv 바이너리 (astral 공식 이미지에서 복사)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# OpenCV(headless) 런타임 공유 라이브러리 + weights 다운로드 도구. slim 엔 없어 명시 설치.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libglib2.0-0 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 실존 모델 얼굴 대조 QC weights (OpenCV Zoo, Apache-2.0) — pin + sha256 검증.
# 런타임 네트워크 의존 제거(빌드타임 번들). 상업 재배포 가능 라이선스.
RUN mkdir -p /app/app/data/face_models && \
    curl -fL -o /app/app/data/face_models/face_recognition_sface_2021dec.onnx \
      https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx && \
    curl -fL -o /app/app/data/face_models/face_detection_yunet_2023mar.onnx \
      https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx && \
    echo "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79  /app/app/data/face_models/face_recognition_sface_2021dec.onnx" | sha256sum -c - && \
    echo "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4  /app/app/data/face_models/face_detection_yunet_2023mar.onnx" | sha256sum -c -

# 의존성만 먼저 설치 — 레이어 캐시 (소스 변경해도 재설치 안 함)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 소스 복사 후 프로젝트 설치
COPY . ./
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8080

# Railway는 $PORT였음 → Fargate는 고정 8080 (ALB target group과 일치)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
