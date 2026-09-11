"""AG-07 cut-variator — 현재 이미지 수정. ai_agent_modules §3 AG-07.

기존 컷(에디터 '현재 이미지 수정') 1장을 입력으로 받아 changes[]에 지시된 항목만 바꾸고 나머지
(인물·의류 동일성)는 동결한다. cut_generator.py와 동일한 배관 패턴
(외부 템플릿 + resolve_model image_high + generate_content_image) — 워커(editor_image_job)가
이 generate()를 호출한다. changes=[] 는 '비슷한 컷 만들기'(계약 §6 VaryRequest).
"""

import os

from ..config import Settings
from . import face_identity
from .gemini_image import GeminiImageClient, InlineImage
from .model_routing import resolve_model
from .prompts import _sanitize

#: cut_generator._WORN_CUTS 와 같은 집합 — 사람이 담기는 컷에만 얼굴 패스를 건다.
_WORN_CUTS = ("styling", "horizon", "mirror")

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_TEMPLATE_FILE = os.path.join(_SERVER_DIR, "prompts", "cut_vary_v1.txt")

_DEFAULT_CUT_TYPE = "styling"  # cutType 미상 소스 → styling으로 가정 (계약 §6 AG-07)

# change.type → 사람이 읽는 라벨 접두. value는 자유 텍스트일 수 있어 sanitize를 거친다.
_TYPE_LABEL = {
    "direction": "camera direction",
    "shot": "shot framing",
    "pose": "pose",
    "face": "facial expression",
    "bg": "background",
}


def _load_template() -> str:
    with open(_TEMPLATE_FILE, encoding="utf-8") as f:
        return f.read()


def _change_line(change: dict) -> str | None:
    """change = {type, value} → 지시 한 줄. 미상 type도 라벨 없이 value만 지시(관대한 처리)."""
    if not isinstance(change, dict):
        return None
    value = _sanitize(change.get("value"))
    if not value:
        return None
    label = _TYPE_LABEL.get(change.get("type"))
    return f"- Change the {label} to: {value}" if label else f"- Change: {value}"


def build_prompt(vary_spec: dict) -> str:
    """vary_spec = {changes: [{type,value}], cutType?, hasRefBg?} → 지시 문단 조립 + 템플릿 치환.
    changes 빈 배열(또는 전부 무효)이면 '비슷한 컷 만들기'로 폴백(계약 §6). 자유 텍스트
    (change.value)는 _sanitize를 거친 뒤에만 프롬프트에 들어간다.
    hasRefBg=True 면 배경은 첨부 2번(레퍼런스) 이미지를 따른다 — bg 칩의 placeholder 값('ref')
    대신 실제 지시문을 넣는다(레퍼런스 기여 계약: 배경·조명·무드만, ADR-0004)."""
    changes = vary_spec.get("changes") or []
    if vary_spec.get("hasRefBg"):
        changes = [c for c in changes if c.get("type") != "bg"]
    lines = [ln for ln in (_change_line(c) for c in changes) if ln]
    if vary_spec.get("hasRefBg"):
        lines.append(
            "- Change the background to match the attached background reference (the second image): "
            "copy its background, lighting and ambience ONLY — never its garment, person or framing."
        )
    instructions = "\n".join(lines) if lines else (
        "- (no specific change requested — make a similar cut: reproduce the same subject and "
        "garment with natural, minor variation)"
    )
    cut_type = _sanitize(vary_spec.get("cutType") or _DEFAULT_CUT_TYPE) or _DEFAULT_CUT_TYPE
    template = _load_template()
    return (
        template
        .replace("${changeInstructions}", instructions)
        .replace("${cutType}", cut_type)
    )


async def generate(
    settings: Settings,
    gemini: GeminiImageClient,
    source_image: InlineImage,
    changes: list,
    cut_type: str | None,
    *,
    ref_bg: InlineImage | None = None,
    face_identity_spec: face_identity.FaceIdentitySpec | None = None,
    face_pass_outcome: dict | None = None,
    face_pass_url_provider=None,
) -> tuple[bytes, str]:
    """변형 컷 1장 생성. 실패 시 GeminiError를 그대로 전파(호출자가 job 실패 처리).
    ref_bg 는 배경 레퍼런스(첨부 2번) — 배경·조명·무드만 반영(ADR-0004).

    face_identity_spec 이 오면 AG-06 과 같은 얼굴 패스를 결과에 건다. 변형은 원본 컷을 다시
    그리는 것이라 얼굴이 흔들릴 수 있고, 그러면 한 상세페이지 안에서 같은 사람이 컷마다 달라진다.
    패스는 **착장 컷일 때만** — product/ghost 류에는 사람이 없다. 실패는 원본 폴백(예외 없음).
    """
    model = resolve_model(settings, "image_high")
    prompt = build_prompt({"changes": changes, "cutType": cut_type, "hasRefBg": ref_bg is not None})
    images = [source_image] if ref_bg is None else [source_image, ref_bg]
    res = await gemini.generate_content_image(
        model, prompt, images,
        getattr(settings, "detail_cut_image_size", None) or settings.mannequin_image_size,
        aspect_ratio=settings.mannequin_aspect_ratio,
    )
    image, mime = res.image, res.mime
    if (face_identity_spec is not None
            and getattr(settings, "face_identity_enabled", False)
            and cut_type in _WORN_CUTS):
        image, mime = await face_identity.apply_face_pass(
            settings, image, mime, face_identity_spec, outcome=face_pass_outcome,
            url_provider=face_pass_url_provider)
    return image, mime
