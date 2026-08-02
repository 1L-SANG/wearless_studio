"""AG-07 cut-variator — 현재 이미지 수정. ai_agent_modules §3 AG-07.

기존 컷(에디터 '현재 이미지 수정') 1장을 입력으로 받아 changes[]에 지시된 항목만 바꾸고 나머지
(인물·의류 동일성)는 동결한다. cut_generator.py/mannequin_adjuster.py와 동일한 배관 패턴
(외부 템플릿 + resolve_model image_high + generate_content_image) — 워커(editor_image_job)가
이 generate()를 호출한다. changes=[] 는 '비슷한 컷 만들기'(계약 §6 VaryRequest).
"""

import hashlib
import os
from dataclasses import dataclass

from ..config import Settings
from .gemini_image import GeminiImageClient, InlineImage
from .model_routing import resolve_model
from .prompts import _sanitize

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


def template_sha256() -> str:
    """생성 프롬프트 **템플릿**의 해시.

    별도 버전 상수를 두면 템플릿을 고치고 상수를 안 올리는 순간 거짓말이 된다.
    해시는 파일이 바뀌면 반드시 바뀌므로 그럴 여지가 없다. 이것이 정본이고,
    "버전"이라는 이름은 쓰지 않는다 — 해시는 순서를 뜻하지 않기 때문이다.
    """
    with open(_TEMPLATE_FILE, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


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


@dataclass(frozen=True)
class PreparedVary:
    """provider 에 **실제로 나갈** 요청. 계측은 이 객체에서 뜬다 — 기록용 프롬프트를 따로
    재조립하면 그 순간부터 기록과 요청이 갈라진다(같은 문자열이라는 보장이 없다)."""

    model: str
    prompt: str
    images: list           # [source] 또는 [source, ref_bg] — 순서가 계약이다
    image_size: str
    aspect_ratio: str
    has_ref_bg: bool


def prepare(
    settings: Settings,
    source_image: InlineImage,
    changes: list,
    cut_type: str | None,
    *,
    ref_bg: InlineImage | None = None,
) -> PreparedVary:
    """요청 조립만. 호출은 하지 않는다 — 계측이 호출 **전에** 스냅샷을 뜰 수 있어야 한다."""
    return PreparedVary(
        model=resolve_model(settings, "image_high"),
        prompt=build_prompt({"changes": changes, "cutType": cut_type,
                             "hasRefBg": ref_bg is not None}),
        images=[source_image] if ref_bg is None else [source_image, ref_bg],
        image_size=settings.mannequin_image_size,
        aspect_ratio=settings.mannequin_aspect_ratio,
        has_ref_bg=ref_bg is not None,
    )


async def execute(gemini: GeminiImageClient, prepared: PreparedVary):
    """준비된 요청 1회 실행. 실패 시 GeminiError 전파(호출자가 job 실패 처리)."""
    return await gemini.generate_content_image(
        prepared.model, prepared.prompt, prepared.images, prepared.image_size,
        aspect_ratio=prepared.aspect_ratio,
    )


async def generate(
    settings: Settings,
    gemini: GeminiImageClient,
    source_image: InlineImage,
    changes: list,
    cut_type: str | None,
    *,
    ref_bg: InlineImage | None = None,
) -> tuple[bytes, str]:
    """변형 컷 1장 생성 — 기존 호출자용 wrapper(바이트 단위로 동작 불변).
    ref_bg 는 배경 레퍼런스(첨부 2번) — 배경·조명·무드만 반영(ADR-0004)."""
    res = await execute(gemini, prepare(settings, source_image, changes, cut_type,
                                        ref_bg=ref_bg))
    return res.image, res.mime
