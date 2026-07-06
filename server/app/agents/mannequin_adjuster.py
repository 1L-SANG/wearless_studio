"""AG-05 mannequin-adjuster — 마네킹 조정 (fit/length/match). ai_agent_modules §3 AG-05.

기존 마네킹컷 1장을 입력으로 받아 지시된 차원(fit/length/match)만 바꾸고 나머지(의류
디테일·구도)는 동결한다. cut_generator.py와 동일한 배관 패턴(외부 템플릿 + resolve_model
image_high + generate_content_image) — 워커(mannequin_adjust_job)가 이 generate()를 호출한다.
"""

import os

from ..config import Settings
from .gemini_image import GeminiImageClient, InlineImage
from .model_routing import resolve_model
from .prompts import _sanitize

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_TEMPLATE_FILE = os.path.join(_SERVER_DIR, "prompts", "mannequin_adjust_v1.txt")

_FIT_LABEL = {"slimmer": "make the fit SLIMMER (more fitted to the body)",
              "looser": "make the fit LOOSER (more relaxed/roomy)"}
_LENGTH_LABEL = {"shorter": "make the garment LENGTH SHORTER",
                 "longer": "make the garment LENGTH LONGER"}


def _load_template() -> str:
    with open(_TEMPLATE_FILE, encoding="utf-8") as f:
        return f.read()


def _match_instruction(match_adjust: dict) -> str | None:
    """matchAdjust = {item, fitAdjust?, lengthAdjust?} — 매칭 하의(item)에 대한 fit/length 지시.
    item(자유 텍스트일 수 있는 필드)은 sanitize. 방향 값이 하나도 없으면 아이템 교체만 지시."""
    if not isinstance(match_adjust, dict):
        return None
    item = _sanitize(match_adjust.get("item") or match_adjust.get("name") or "matching bottom")
    parts = []
    fa = match_adjust.get("fitAdjust") or match_adjust.get("fit_adjust")
    la = match_adjust.get("lengthAdjust") or match_adjust.get("length_adjust")
    if fa in _FIT_LABEL:
        parts.append(_FIT_LABEL[fa])
    if la in _LENGTH_LABEL:
        parts.append(_LENGTH_LABEL[la])
    detail = f" ({'; '.join(parts)})" if parts else ""
    return f"- Adjust the matching bottom garment ({item}){detail}, keep the top garment frozen"


def build_prompt(adjust_spec: dict) -> str:
    """adjust_spec = {fitAdjust?, lengthAdjust?, matchAdjust?} → 지시 문단 조립 + 템플릿 치환.
    지시된 차원만 나열 — 나머지는 템플릿의 <instruction> freeze 문구가 담당한다.
    셀러/사용자 자유 텍스트(matchAdjust.item)는 _sanitize를 거친 뒤에만 프롬프트에 들어간다."""
    lines = []
    fit_adjust = adjust_spec.get("fitAdjust") or adjust_spec.get("fit_adjust")
    length_adjust = adjust_spec.get("lengthAdjust") or adjust_spec.get("length_adjust")
    if fit_adjust in _FIT_LABEL:
        lines.append(f"- {_FIT_LABEL[fit_adjust]}")
    if length_adjust in _LENGTH_LABEL:
        lines.append(f"- {_LENGTH_LABEL[length_adjust]}")
    match_adjust = adjust_spec.get("matchAdjust") or adjust_spec.get("match_adjust")
    match_line = _match_instruction(match_adjust) if match_adjust else None
    if match_line:
        lines.append(match_line)
    instructions = "\n".join(lines) if lines else "- (no dimension change requested — reproduce the image as-is)"
    template = _load_template()
    text = template.replace("${adjustInstructions}", instructions)
    return text


async def generate(
    settings: Settings,
    gemini: GeminiImageClient,
    base_image: InlineImage,
    adjust_spec: dict,
) -> tuple[bytes, str]:
    """조정 컷 1장 생성. 실패 시 GeminiError를 그대로 전파(호출자가 job 실패 처리)."""
    model = resolve_model(settings, "image_high")
    prompt = build_prompt(adjust_spec)
    res = await gemini.generate_content_image(
        model, prompt, [base_image], settings.mannequin_image_size,
        aspect_ratio=settings.mannequin_aspect_ratio,
    )
    return res.image, res.mime
