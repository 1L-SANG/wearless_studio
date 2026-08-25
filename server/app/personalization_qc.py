"""개인화 얼굴 사진 동기 품질검사(QC) — Gemini/GPT 멀티모달 비전 판정 (api-spec §3.2·§1.4).

CPU-only 제약(insightface/cv2 미설치)에서 얼굴 QC를 **멀티모달 비전 LLM 구조화 판정**으로
구현한다. 업로드 얼굴 바이트를 `vision_llm.analyze_with_fallback`(= gemini_image.py 와 동일
인증·httpx 경로, Gemini→GPT 폴백)로 보내 occlusion/low_resolution/multiple_faces/angle_mismatch
를 각도 슬롯과 대조해 판정한다.

PII 하드 룰(§1.4): 이 모듈은 **판정 결과(verdict·사유코드)만** 반환한다. 얼굴 바이트·임베딩·
랜드마크·검출박스·파일명은 저장·로그·반환 어디에도 남기지 않는다(로깅 허용 = 상태 enum·QC
사유코드·provider 이름뿐). cross_border_transfer 동의가 업로드 라우트의 전제라 국외(미국) 비전
API 전송은 이미 정합(라우트 코드 게이트가 보장 — 이 모듈은 동의 확인 이후에만 호출됨).

폴백(§QC 스파이크): 비전 provider 미설정/불통/비순응이면 **얼굴을 저장하지 않고** 명확한
503(`FaceQcUnavailable`)로 실패한다 — 검증 안 된 생체정보를 조용히 통과시키지도(unsafe),
정상 사진을 임의 reject 하지도 않는 보수적 fail-safe.
"""

import logging
from dataclasses import dataclass, field

from .agents.gemini_image import InlineImage
from .agents.vision_llm import VisionError, analyze_with_fallback
from .config import Settings

logger = logging.getLogger("wearless.personalization_qc")

# 허용 QC 사유코드(api-spec §3.2). 'no_face' 는 확정 전까지 occlusion 으로 수렴(스펙 비고).
QC_CODES = ("occlusion", "low_resolution", "multiple_faces", "angle_mismatch")

# 전부 차단(blocking). 예전엔 angle_mismatch 를 advisory 로 뒀는데, 그러면 각도 검증이 사실상
# 사라져 '측면 슬롯에 정면 사진'도 통과하는 문제가 있었다. 대신 각도 판정을 신뢰 가능한 수준으로
# 거칠게 바꾼다 — 45˚ vs 90˚ 같은 애매한 구분은 아예 안 하고 front vs turned(정면 vs 돌린 얼굴)
# 두 버킷만 판정한다. 45˚/측면은 같은 'turned' 라 서로 넣어도 통과(오탐 없음), 정면↔돌림 불일치만
# 차단한다. QC_CODES 전체를 정확히 분할(advisory 는 비움).
BLOCKING_QC_CODES = ("occlusion", "low_resolution", "multiple_faces", "angle_mismatch")
ADVISORY_QC_CODES = ()

# 각도 슬롯 → 기대 방향 버킷. front 는 정면, angle45·side 는 둘 다 turned(돌린 얼굴)로 취급.
_ANGLE_BUCKET = {"front": "front", "angle45": "turned", "side": "turned"}

# GPT strict json_schema + Gemini responseSchema 양쪽 호환(소문자 type; vision_llm 이 변환).
# 각도는 LLM 에게 슬롯 대조를 시키지 않고 '실제 방향(front/turned)'만 물은 뒤, 슬롯과의 일치
# 여부는 파이썬에서 결정론적으로 판정한다(45˚ vs 90˚ 같은 애매한 구분을 LLM 에 맡기지 않음).
FACE_QC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "orientation": {
            "type": "string",
            "enum": ["front", "turned"],
            "description": "얼굴 실제 방향: 카메라를 거의 정면으로 응시하면 front, 옆/반측면 등 "
            "고개를 돌린 상태면 turned. 45도인지 90도인지는 구분하지 말 것.",
        },
        "defects": {
            "type": "array",
            "description": "품질 결함 코드(없으면 빈 배열). 해당하는 것만 나열.",
            "items": {
                "type": "string",
                "enum": ["occlusion", "low_resolution", "multiple_faces"],
            },
        },
    },
    "required": ["orientation", "defects"],
}

_QC_PROMPT = (
    "당신은 얼굴 등록 사진 품질검사기입니다. 아래 이미지를 보고 두 가지만 판정해 JSON 으로 답하세요.\n\n"
    "1) orientation — 얼굴 방향:\n"
    "   - front: 카메라를 거의 정면으로 바라봄(두 눈·양쪽 볼이 고르게 보임).\n"
    "   - turned: 고개를 옆으로 돌림(반측면이든 완전 측면이든 모두 turned). "
    "45도인지 90도인지는 구분하지 마세요.\n"
    "2) defects — 품질 결함(있으면 코드 나열, 없으면 []):\n"
    "   - occlusion: 마스크·선글라스·손·머리카락 등으로 눈·코·입이 가려짐. 얼굴 미검출도 occlusion.\n"
    "   - low_resolution: 흐림·초점흐트러짐·저해상도로 이목구비 불선명.\n"
    "   - multiple_faces: 2인 이상의 얼굴이 보임(본인 1인만 허용).\n\n"
    "오직 위 스키마의 JSON 만 출력하세요."
)


class FaceQcUnavailable(RuntimeError):
    """비전 QC provider 미설정/불통/비순응 — 라우트가 503 으로 매핑(fail-safe, 저장 안 함)."""


@dataclass
class FaceQcResult:
    verdict: str  # 'pass' | 'reject' (원본 LLM 판정 — 관측/로그용으로 보존)
    reasons: list[str] = field(default_factory=list)

    @property
    def blocking_reasons(self) -> list[str]:
        """저장을 막는 실제 품질 결함(occlusion/low_resolution/multiple_faces)."""
        return [r for r in self.reasons if r in BLOCKING_QC_CODES]

    @property
    def advisory_reasons(self) -> list[str]:
        """차단하지 않고 경고만 하는 사유(angle_mismatch)."""
        return [r for r in self.reasons if r in ADVISORY_QC_CODES]

    @property
    def passed(self) -> bool:
        # 차단 사유가 하나도 없으면 통과. verdict=='pass' 는 reasons=[] 이므로 자동 포함되고,
        # verdict=='reject' 라도 사유가 angle_mismatch 뿐이면 통과시킨다(advisory).
        return not self.blocking_reasons


async def evaluate_face_qc(
    settings: Settings, *, image_bytes: bytes, mime: str, angle: str
) -> FaceQcResult:
    """얼굴 바이트 → pass/reject 판정. 실패 시 FaceQcUnavailable(라우트 503).

    §1.4: 이미지·임베딩·랜드마크·파일명 로그 금지 — verdict·reasons·provider 만 관측.
    """
    if not (settings.gemini_api_key or settings.openai_api_key):
        # 비전 키 전무 = 판정 불가 → fail-safe 503 (검증 안 된 얼굴 저장 금지).
        raise FaceQcUnavailable("qc_provider_unconfigured")

    images = [InlineImage(mime=mime, data=image_bytes)]
    try:
        raw, provider = await analyze_with_fallback(settings, _QC_PROMPT, images, FACE_QC_SCHEMA)
    except VisionError as e:
        # provider 불통/비순응(파싱 실패 포함) → fail-safe 503. 에러 문자열에 얼굴 바이트 없음.
        raise FaceQcUnavailable(str(e)[:200]) from e

    # 품질 결함(LLM) + 각도 불일치(파이썬 결정론). front↔turned 불일치만 차단, 45˚↔측면은 통과.
    reasons: list[str] = []
    for d in raw.get("defects") or []:
        if d in ("occlusion", "low_resolution", "multiple_faces") and d not in reasons:
            reasons.append(d)
    orientation = "front" if raw.get("orientation") == "front" else "turned"
    if orientation != _ANGLE_BUCKET.get(angle, "turned"):
        reasons.append("angle_mismatch")
    verdict = "pass" if not reasons else "reject"

    # 관측 로그 — 상태·사유코드·provider 만(§1.4 허용 범위). 이미지/파일명/랜드마크 절대 금지.
    logger.info(
        "personalization_face_qc",
        extra={"angle": angle, "verdict": verdict, "reasons": reasons, "provider": provider},
    )
    return FaceQcResult(verdict, reasons)


_QC_MESSAGES = {
    "occlusion": "얼굴이 가려져 있어요. 얼굴 전체가 보이게 다시 찍어주세요.",
    "low_resolution": "사진이 흐리거나 작아요. 더 선명한 사진으로 올려주세요.",
    "multiple_faces": "사진에 여러 명이 있어요. 본인만 나온 사진으로 올려주세요.",
    "angle_mismatch": "선택한 칸과 얼굴 방향이 달라요. 정면 칸엔 정면, 45도·측면 칸엔 고개를 돌린 사진을 올려주세요.",
}


def qc_reason_message(reasons: list[str]) -> str:
    """reject 사유코드 → 사용자 안내 카피(api-spec §3.2 카피 초안)."""
    seen = [_QC_MESSAGES[r] for r in reasons if r in _QC_MESSAGES]
    return " ".join(seen) or "얼굴 사진을 사용할 수 없어요. 다른 사진으로 다시 시도해 주세요."
