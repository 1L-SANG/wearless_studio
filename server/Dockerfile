# Wearless Studio API — FastAPI (Python 3.12, uv). Build context = server/.
FROM python:3.12-slim

# uv 바이너리 (astral 공식 이미지에서 복사)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

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
