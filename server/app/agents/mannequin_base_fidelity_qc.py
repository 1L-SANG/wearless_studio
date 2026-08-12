"""베이스 충실도 QC — 생성 컷이 베이스 마네킹의 연출을 유지했는가 + 착장 형상이 하나인가.

기존 QC 가 못 잡는 두 실패 유형을 위한 **별도** 판정기다(2026-08-12 육안 QA 발단):

  1. 포즈·프레임 이탈 — 베이스가 3/4 뷰인데 결과가 정면이 되거나, 프레이밍·스케일이
     같은 갤러리에 못 놓을 만큼 벌어진다.
  2. 옷 형상 중복·돌출 — 앞뒤를 동시에 입은 것처럼 뒷판이 보이거나, 몸통 옆·뒤로
     설명되지 않는 껍데기가 하나 더 생긴다.

왜 기존 QC 로는 안 되는가: AG-P2(`image_qc`)는 **상품 사진 + 생성물**만 받는다. 베이스
마네킹이 입력에 아예 없어서 "베이스와 달라졌다"는 판정 자체가 불가능하다. Pillow QC
(`services/qc.py`)에는 프레이밍 휴리스틱이 있지만 오탐이 상수라 코드에서 강제 shadow 다.
그래서 입력 계약이 다른 판정기를 따로 둔다 — 기존 프롬프트를 건드려 프로덕션 판정을
흔들지 않기 위해서이기도 하다.

**핏 가드레일이 이 모듈의 핵심 위험이다.** "몸보다 넓으면 불량"으로 판정하면 boxy·
oversized 상품이 전부 걸린다. 그래서 셀러/분석 메타데이터를 프롬프트에 넣어, 넓은 실루엣이
선언된 핏인지 아니면 형상이 실제로 깨진 것인지를 구분하게 한다. 새 분류기는 만들지 않는다 —
이미 있는 필드만 읽는다.

오케스트레이션(발화·fail-open·이벤트)은 `workers/mannequin_job.py` 소관. 여기는 순수 코어다.
"""

import json
import os

from ..config import Settings
from .gemini_image import InlineImage
from .vision_llm import VisionError, analyze_with_fallback

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "mannequin_base_fidelity_qc_v1.txt")

#: 판정 계약의 버전. 프롬프트·스키마·정책이 바뀌면 올린다 — 관측 코퍼스에서 "어떤 판정기가
#: 낸 결과인가"를 구분하고, 관측 잡의 멱등키에도 들어간다.
QC_VERSION = "base-fidelity-v2"   # v2: wearGeometry 를 구조 무결성 검사로 재정의(2026-08-12)

MODES = ("off", "shadow", "enforce")
#: 판정값. `skip` 은 모델이 내는 값이 아니라 **판정을 못 한 상태**를 코드가 붙이는 값이다.
DECISIONS = ("pass", "retry", "skip")
AXES = ("poseFrameMatch", "wearGeometry")

#: 판정 자체가 불가능한 사유. 이벤트에서 "불량 0건"과 "측정 못 함"을 구분하기 위해 남긴다.
SKIP_MISSING_BASE = "missing_base_reference"
SKIP_DISABLED = "disabled"
SKIP_FAILED = "qc_failed"


def fit_context(product: dict | None, analysis: dict | None) -> dict:
    """핏 가드레일에 쓸 메타데이터. **이미 있는 필드만** 읽는다(새 분류기 없음).

    비어 있어도 판정은 진행한다 — 프롬프트가 "핏 정보가 없으면 폭만으로 불량 판정하지
    말 것"을 따로 못박는다. 여기서 하는 일은 셀러가 선언한 것을 그대로 전달하는 것뿐이고,
    없는 값을 추론해 채우지 않는다.
    """
    product = product or {}
    analysis = analysis or {}
    profile = analysis.get("fitProfile")
    axes = profile.get("axes") if isinstance(profile, dict) else None
    ctx = {
        "clothingType": (product.get("clothing_type") or product.get("clothingType")
                         or analysis.get("clothingType")),
        "subCategory": analysis.get("subCategory") or product.get("subCategory"),
        "fit": analysis.get("fit"),
        "declaredAxes": axes if isinstance(axes, dict) else None,
        "styleTags": analysis.get("styleTags"),
        "productName": product.get("name") or analysis.get("suggestedName"),
    }
    return {k: v for k, v in ctx.items() if v not in (None, "", [], {})}


def qc_schema() -> dict:
    """축 2개 × (decision, reason). `overall` 은 모델이 아니라 코드가 만든다(아래 `validate`)."""
    axis = {
        "type": "object",
        "additionalProperties": False,
        "required": ["decision", "reason"],
        "properties": {
            # 모델에는 pass/retry 만 허용한다. skip 은 "판정 못 함"이라 모델이 고를 값이 아니다.
            "decision": {"type": "string", "enum": ["pass", "retry"]},
            "reason": {"type": "string"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(AXES),
        "properties": {a: axis for a in AXES},
    }


def build_prompt(ctx: dict | None) -> str:
    with open(_PROMPT_FILE, encoding="utf-8") as f:
        template = f.read()
    payload = json.dumps(ctx or {}, ensure_ascii=False, sort_keys=True)
    return template.replace("${fitContext}", payload)


def _axis(raw: dict | None, name: str) -> dict:
    """축 하나를 계약 shape 으로 정규화. 허용값 밖·형식 위반은 skip 으로 떨어뜨린다.

    여기서 예외를 올리지 않는 이유: 이 QC 는 shadow 관측이 목적이라, 판정기가 이상한 값을
    돌려줬다는 사실 자체가 기록돼야 한다. 통째로 터뜨리면 그 정보가 사라진다.
    """
    item = (raw or {}).get(name)
    if not isinstance(item, dict):
        return {"decision": "skip", "reason": "malformed_axis"}
    decision = item.get("decision")
    if decision not in ("pass", "retry"):
        return {"decision": "skip", "reason": "malformed_decision"}
    reason = item.get("reason")
    reason = reason.strip()[:300] if isinstance(reason, str) else ""
    return {"decision": decision, "reason": reason}


def overall_decision(axes: dict) -> dict:
    """두 축 → 종합 판정 (순수·결정적).

    모델에게 종합을 시키지 않는다. 축 판정과 종합이 어긋나는 응답이 실제로 나오면 어느 쪽을
    믿을지 사람이 정해야 하는데, 그건 판정기가 아니라 정책이다. 정책은 코드에 둔다.
    """
    decisions = [axes[a]["decision"] for a in AXES]
    if "retry" in decisions:
        failed = [a for a in AXES if axes[a]["decision"] == "retry"]
        return {"decision": "retry", "reason": "+".join(failed)}
    if all(d == "skip" for d in decisions):
        return {"decision": "skip", "reason": "no_axis_judged"}
    return {"decision": "pass", "reason": "all_axes_pass"}


def validate(raw: dict | None) -> dict:
    axes = {a: _axis(raw, a) for a in AXES}
    return {**axes, "overall": overall_decision(axes)}


def skipped(reason: str) -> dict:
    """판정을 못 한 결과. 성공 결과와 **같은 shape** 이어야 소비자가 분기하지 않는다."""
    axes = {a: {"decision": "skip", "reason": reason} for a in AXES}
    return {**axes, "overall": {"decision": "skip", "reason": reason}}


async def verdict(
    settings: Settings,
    base_image: InlineImage | None,
    generated_image: InlineImage,
    *,
    product: dict | None = None,
    analysis: dict | None = None,
) -> dict:
    """베이스 대비 생성 컷 판정. 베이스가 없으면 판정하지 않고 skip 을 돌려준다.

    이미지 순서는 프롬프트가 명시한 대로 **베이스 먼저, 생성물 나중**이다.
    provider 실패는 VisionError 로 전파한다 — fail-open 은 호출부(워커) 소관이다.
    """
    if base_image is None:
        return skipped(SKIP_MISSING_BASE)
    ctx = fit_context(product, analysis)
    raw, _provider = await analyze_with_fallback(
        settings, build_prompt(ctx), [base_image, generated_image], qc_schema())
    if not isinstance(raw, dict):
        raise VisionError("base_fidelity_qc: 응답이 객체가 아님")
    return validate(raw)
