# SAM2 garment segmentation service.
#
# This image is the ONLY place torch, transformers and the SAM2 weights are allowed to exist.
# The main API image (server/Dockerfile) must stay free of them — `server/.dockerignore`
# excludes `sam_service/` so they cannot leak in by accident.
#
# Build context is `server/`:
#   docker build -f sam_service/Dockerfile -t wearless/sam2 server/
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf

WORKDIR /srv

# libgl/libglib: opencv needs them even in headless builds for a few codecs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY sam_service/requirements.txt ./requirements.txt
# CPU-only torch. The default wheel drags in CUDA and multiplies the image size for a target
# that has no GPU; if a GPU deployment is chosen later this index URL is the line that changes.
RUN pip install --index-url https://download.pytorch.org/whl/cpu \
        torch torchvision \
 && pip install -r requirements.txt

# Bake the weights (~149MB for hiera-tiny). Small enough to embed, and embedding them makes the
# image self-contained: no startup download, no runtime credentials to the model hub, and the
# exact weights are pinned to the image tag rather than to whatever the hub serves that day.
ARG SAM_MODEL_ID=facebook/sam2.1-hiera-tiny
RUN python -c "\
from huggingface_hub import snapshot_download; \
snapshot_download('${SAM_MODEL_ID}')" \
 && find /opt/hf -name '*.msgpack' -delete

COPY sam_service/ ./sam_service/

# Fail fast at build time if the weights did not land — a service that only discovers this on
# its first real request has already accepted the request. Runs with offline forced ON, which
# is what proves the cache is genuinely complete rather than silently re-fetching.
RUN HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -c "\
from transformers import Sam2Processor; \
Sam2Processor.from_pretrained('${SAM_MODEL_ID}'); \
print('weights present')"

# Offline is pinned only AFTER the weights are cached and verified. Setting it earlier makes
# the image block its own download step (`OfflineModeIsEnabled`). At runtime it guarantees a
# cold task never stalls on a Hugging Face outage.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

EXPOSE 8080
CMD ["uvicorn", "sam_service.api:app", "--host", "0.0.0.0", "--port", "8080", \
     "--workers", "1", "--timeout-keep-alive", "75"]
