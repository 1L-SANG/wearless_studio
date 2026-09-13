"""얼굴 패스 레시피 — 렌더·합성 상수를 한 곳에 모아 해시 하나로 만든다.

왜 필요한가: **GPU 가 다르면 같은 시드로도 다른 그림이 나온다.** 2026-09-11 재현 시험 R — 같은 코드·같은
시드 42·같은 E2 조건으로 다시 뽑았더니 얼굴 영역 RGB MAE 16.95(최대 177)로 머리 실루엣이 눈에 띄게 달랐다.
SFace 차는 0.001 이었다. 즉 픽셀로는 "어떤 레시피로 나왔나"를 되짚을 수 없고, 되짚을 수 있는 건 상수뿐이다.
같은 파드 안에서는 완전 재현된다(같은 컷 재실행이 sface·hf·color 까지 동일) — 갈리는 건 카드다.

그래서 컷 메타와 파드 /healthz 에 같은 계산식의 해시를 찍어, "이 컷은 어떤 상수로 나왔나"와
"지금 이 파드는 어떤 상수로 돌고 있나"를 맞춰 볼 수 있게 한다. 이미지 회귀 비교는 픽셀이 아니라
SFace 로 한다(GATE_IDENTITY_MIN 경로).

레시피가 바뀌면 해시가 바뀐다. 바꿀 때는 RECIPE_SCHEMA 를 올리지 말고 그냥 상수를 바꾸면 된다 —
스키마는 **필드 구성**이 바뀔 때만 올린다(옛 컷의 해시를 지금 계산식으로 다시 만들 수 없게 되므로).
"""

from __future__ import annotations

import hashlib
import json

from . import face_identity as fi
from .face_identity_qwen import RENDER_MODEL_ID

#: 필드 구성 판. 필드를 더하거나 이름을 바꿀 때만 올린다.
RECIPE_SCHEMA = 1
#: 얼굴 크롭 확대 정책. 화면 전체 확대는 쓰지 않는다 — 옷 픽셀이 바뀐다(2026-09-11 G 대 H 실측).
UPSCALE_SCOPE_FACE_CROP = "face_crop"
UPSCALE_SCOPE_OFF = "off"


def recipe_fields(*, model_id: str = RENDER_MODEL_ID, lora_sha256: str | None = None,
                  upscale_scope: str = UPSCALE_SCOPE_FACE_CROP) -> dict:
    """해시에 들어가는 값 전부. 사람이 읽을 수 있는 형태 그대로 남긴다(해시만 남기면 못 되짚는다)."""
    return {
        "schema": RECIPE_SCHEMA,
        "model_id": model_id,
        # 전체 sha 가 아니라 앞 12자리 — 로그·메타에 그대로 실리는 값이라 짧게 둔다.
        "lora_sha12": (lora_sha256 or "")[:12] or None,
        "seeds": list(fi.DEFAULT_SEEDS),
        "steps": fi.RENDER_STEPS,
        "guidance": fi.RENDER_GUIDANCE,
        "negative": fi.RENDER_NEGATIVE,
        "crop": fi.CROP,
        "crop_face_width_mult": 3,          # plan_from_box: side = 3 × 얼굴폭
        "ellipse": list(fi.ELLIPSE),
        "feather": fi.FEATHER_FRAC,
        "control_blur": fi.CONTROL_BLUR_FRAC,
        "edge_fade_px": fi.EDGE_FADE_PX,
        "edge_fade_hard_px": fi.EDGE_FADE_HARD_PX,
        # 페이드를 거는 규칙 자체가 레시피다 — 2026-09-11 에 "사진 안쪽 변에만" 으로 바뀌었다.
        "edge_fade_rule": "crossing_and_interior",
        "color_ring_px": fi.COLOR_RING_PX,
        "grain_min_ratio": fi.GRAIN_MIN_RATIO,
        "upscale_scope": upscale_scope,
        "upscale_max_k": fi.CROP_UPSCALE_MAX,
    }


def recipe_id(fields: dict) -> str:
    """필드 → 12자리 해시. 키 정렬 고정 JSON 이라 파드와 API 가 같은 값을 낸다."""
    blob = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def recipe(*, model_id: str = RENDER_MODEL_ID, lora_sha256: str | None = None,
           upscale_scope: str = UPSCALE_SCOPE_FACE_CROP) -> dict:
    fields = recipe_fields(model_id=model_id, lora_sha256=lora_sha256, upscale_scope=upscale_scope)
    return {"id": recipe_id(fields), "fields": fields}
