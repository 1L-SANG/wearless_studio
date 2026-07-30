"""자체 호스팅 임베딩 — 검색 증강 Phase 3 (retrieval_upgrade_prd, ADR D2 v1.3).

이미지=SigLIP(transformers), 텍스트=bge-m3(sentence-transformers). CPU/MPS 로컬 추론,
GPU 불요. 오프라인 배치(scripts/embed_corpus.py)·로컬 검증 전용 — prod 기본 이미지 미포함
(pyproject optional group [embeddings], R3 완화). torch/transformers 는 **lazy import** 라
미설치 환경에서도 이 모듈의 import 자체는 안전하다(실제 임베딩 호출 시에만 명확한 에러).

결정성(NFR-1): grad 비활성·eval 모드 추론 + L2 정규화 → 코사인 = 내적. 같은 이미지·모델은
같은 벡터(부동소수 오차 내)를 준다. 모델 id·차원은 config 단일소스이며 반환 벡터 길이가
설정 차원과 다르면 즉시 에러(테이블 vector(dim) 컬럼과의 불일치를 조기 검출)."""

import io
import logging
import time

logger = logging.getLogger(__name__)

# 모델은 로딩 비용이 커서 (프로세스당) 1회만 적재하고 캐시한다. config 모델 id 를 키로.
_IMAGE_CACHE: dict = {}
_TEXT_CACHE: dict = {}


class EmbeddingUnavailable(RuntimeError):
    """torch/transformers 미설치 또는 모델 로드 실패 — 호출부가 graceful 처리(검색 스킵)."""


def _torch():
    try:
        import torch  # lazy: optional group [embeddings]
    except ImportError as e:  # pragma: no cover - 환경 의존
        raise EmbeddingUnavailable(
            "임베딩 의존성 미설치. `uv pip install -e '.[embeddings]'` (torch/transformers)."
        ) from e
    return torch


def _device():
    torch = _torch()
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _load_image_model(model_id: str):
    """SigLIP (model, processor) 를 적재·캐시. eval + grad off."""
    if model_id in _IMAGE_CACHE:
        return _IMAGE_CACHE[model_id]
    torch = _torch()
    try:
        # 이미지 임베딩만 필요 → AutoImageProcessor (텍스트 토크나이저·sentencepiece 불요).
        from transformers import AutoImageProcessor, AutoModel
    except ImportError as e:  # pragma: no cover
        raise EmbeddingUnavailable("transformers 미설치") from e
    t0 = time.monotonic()
    model = AutoModel.from_pretrained(model_id)
    processor = AutoImageProcessor.from_pretrained(model_id)
    model.eval()
    model.to(_device())
    torch.set_grad_enabled(False)
    logger.info(
        "embedding_model_loaded",
        extra={"kind": "image", "model": model_id, "device": _device(),
               "load_ms": int((time.monotonic() - t0) * 1000)},
    )
    _IMAGE_CACHE[model_id] = (model, processor)
    return _IMAGE_CACHE[model_id]


def _l2_normalize(vec):
    torch = _torch()
    return torch.nn.functional.normalize(vec, p=2, dim=-1)


def embed_image(data: bytes, *, model_id: str, expected_dim: int | None = None) -> list[float]:
    """이미지 바이트 → L2 정규화 임베딩(list[float]). expected_dim 불일치 시 에러."""
    return embed_images([data], model_id=model_id, expected_dim=expected_dim)[0]


def embed_images(
    items: list[bytes], *, model_id: str, expected_dim: int | None = None
) -> list[list[float]]:
    """이미지 바이트 배치 → 임베딩 리스트. 결정적(eval·grad off·L2)."""
    if not items:
        return []
    torch = _torch()
    from PIL import Image

    model, processor = _load_image_model(model_id)
    imgs = [Image.open(io.BytesIO(b)).convert("RGB") for b in items]
    t0 = time.monotonic()
    inputs = processor(images=imgs, return_tensors="pt").to(_device())
    with torch.no_grad():
        feats = model.get_image_features(**inputs)
    # transformers 버전에 따라 텐서 또는 output 객체를 반환 → 이미지 임베딩 텐서 추출.
    if not torch.is_tensor(feats):
        if hasattr(feats, "image_embeds"):
            feats = feats.image_embeds
        elif hasattr(feats, "pooler_output"):
            feats = feats.pooler_output
        else:
            raise EmbeddingUnavailable(f"예상 못한 임베딩 반환형: {type(feats)}")
    feats = _l2_normalize(feats).cpu().tolist()
    if expected_dim is not None:
        for v in feats:
            if len(v) != expected_dim:
                raise ValueError(
                    f"임베딩 차원 불일치: 모델 {model_id} → {len(v)}, 설정/컬럼 기대 {expected_dim}. "
                    "config embed_image_dim 과 마이그레이션 vector(dim) 를 모델에 맞춰라."
                )
    logger.info(
        "embedding_batch",
        extra={"kind": "image", "model": model_id, "n": len(items),
               "dim": len(feats[0]) if feats else 0,
               "infer_ms": int((time.monotonic() - t0) * 1000)},
    )
    return feats


def embed_text(text: str, *, model_id: str, expected_dim: int | None = None) -> list[float]:
    """텍스트 → L2 정규화 임베딩(bge-m3 등, sentence-transformers). 2b 챌린저용."""
    if model_id not in _TEXT_CACHE:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:  # pragma: no cover
            raise EmbeddingUnavailable("sentence-transformers 미설치") from e
        _TEXT_CACHE[model_id] = SentenceTransformer(model_id, device=_device())
        logger.info("embedding_model_loaded", extra={"kind": "text", "model": model_id})
    model = _TEXT_CACHE[model_id]
    vec = model.encode(text, normalize_embeddings=True).tolist()
    if expected_dim is not None and len(vec) != expected_dim:
        raise ValueError(
            f"텍스트 임베딩 차원 불일치: {model_id} → {len(vec)}, 기대 {expected_dim}."
        )
    return vec


def to_pgvector(vec: list[float]) -> str:
    """list[float] → pgvector 리터럴 문자열 '[a,b,c]'. `%s::vector` 로 바인딩."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"
