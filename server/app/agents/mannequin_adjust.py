"""편집 기반 마네킹컷 조정 — 프롬프트 조립 (순수 코어, 2026-07-31 설계).

조정(:regenerate)을 "베이스에서 재생성"이 아니라 "현재 컷 편집"으로 수행하기 위한
프롬프트 빌더. v1은 폐기된 AG-05 유물이라 v2로 분리. 지시문은 fit_axes 고정 문구만 사용(셀러 텍스트 비주입), 의류 단위
스코프(MAIN PRODUCT / MATCHING BOTTOM)를 명시해 지시 밖 의류 변경을 금지한다.
모델은 조정 전용 tier(MANNEQUIN_ADJUST_TIER=image_high → Gemini 3 Pro) 사용이 전제.
"""

import os

from .fit_axes import AXIS_OBSERVABLES, FIT_AXES, _axis_entry

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "mannequin_adjust_v2.txt")

ADJUST_PROMPT_VERSION = "adjust_v2"


def _directive_line(scope: str, category: str, axis: str, gender: str, value: str) -> str | None:
    """의류 스코프 명시 지시문 1줄 — promptEn + 관측 목표 (둘 다 카탈로그 고정 문구)."""
    entry = _axis_entry(category, axis, gender, value)
    obs = AXIS_OBSERVABLES.get((category, axis, value))
    if entry is None or obs is None:
        return None
    return f"- {scope} — {axis}: {entry['promptEn']}. Observable target: {obs}."


def build_adjust_directives(profile: dict, adjusted_axes: tuple | list) -> str:
    """조정된 축만 지시문으로. 주상품 축 + (있으면) 매칭 핏(matchingFit)을 의류 스코프로 구분.

    adjusted_axes 는 서버가 diff 로 계산한 주상품 축 목록(스냅샷 계약 그대로).
    matchingFit 은 diff 대상이 아니므로 존재하면 항상 지시문에 포함한다(조정 UI 에서
    선택된 값 = 이번 편집의 목표). 유효하지 않은 값은 조용히 제외 — 빈 문자열이면
    호출측이 편집 경로를 포기하고 재생성 경로로 폴백해야 한다.
    """
    category = profile.get("category")
    gender = profile.get("gender")
    axes = profile.get("axes") or {}
    lines = []
    for axis in FIT_AXES.get(category, {}):
        if axis not in (adjusted_axes or ()):
            continue
        value = axes.get(axis)
        if isinstance(value, str):
            line = _directive_line("MAIN PRODUCT", category, axis, gender, value)
            if line:
                lines.append(line)
    mf = profile.get("matchingFit")
    if isinstance(mf, dict) and isinstance(mf.get("axes"), dict):
        m_cat = mf.get("fitCategory")
        for axis, value in mf["axes"].items():
            if isinstance(value, str):
                line = _directive_line("MATCHING BOTTOM", m_cat, axis, "women", value)
                if line:
                    lines.append(line)
    return "\n".join(lines)


# 슬롯별 역할과 **권위(authority)** — 무엇을 이 사진에서 가져오고 무엇을 가져오면 안 되는지.
# 예전에는 상품 사진 전부가 `product photo — identity reference` 한 줄로 반복돼, 모델 입장에서
# 근접 원단컷과 사람이 입은 핏컷이 같은 무게였다. 패턴이 틀리는 상품일수록 이 구분이 중요하다
# (착용컷은 주름·조명 때문에 색과 줄 간격이 실제와 다르게 보인다).
_ADJUST_ROLE = {
    "Detail": ("DETAIL close-up of the product — AUTHORITATIVE for fabric identity: ground "
               "color, color order, stripe/check pitch, line widths and texture"),
    "Front": ("FRONT view of the product — AUTHORITATIVE for garment shape, construction "
              "(collar, placket, buttons, cuffs, pockets) and overall pattern layout"),
    "Back": ("BACK view of the product — reference for back construction and pattern "
             "continuity"),
    "Fit": ("FIT reference (product worn on a person) — length and drape only; NEVER take "
            "color, pattern or construction from it"),
}
_ADJUST_ROLE_FALLBACK = "product photo — identity reference for the MAIN PRODUCT"

_CURRENT_CUT_LINE = (
    "1. CURRENT CUT — the mannequin photo to edit: it is the authority for scene, pose, "
    "framing and lighting. Its garment pattern is NOT authoritative — match the product "
    "photos below."
)


def build_adjust_manifest(refs, has_match: bool) -> str:
    """편집 입력 순서: 1=현재 컷(캔버스), 2..=상품 참조(역할·권위 명시), 마지막=매칭(있으면).

    `refs` 는 `ProductReference` 시퀀스이며 **실제로 전달할 순서 그대로**여야 한다. 번호와
    이미지가 어긋나면 "2번은 Detail" 이라는 문장이 다른 사진에 적용된다 — 잘못된 매니페스트는
    매니페스트가 없는 것보다 나쁘다.
    """
    lines = [_CURRENT_CUT_LINE]
    i = 2
    for ref in refs:
        lines.append(f"{i}. {_ADJUST_ROLE.get(getattr(ref, 'slot', None), _ADJUST_ROLE_FALLBACK)}")
        i += 1
    if has_match:
        lines.append(f"{i}. matching bottom photo — identity reference for the MATCHING BOTTOM")
    return "\n".join(lines)


def render_adjust_prompt(directives: str, manifest: str) -> str:
    with open(_PROMPT_FILE, encoding="utf-8") as f:
        template = f.read()
    return (template.replace("${imageManifest}", manifest)
            .replace("${adjustmentDirectives}", directives))
