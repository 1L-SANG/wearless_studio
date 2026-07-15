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

_ANGLE_DESC = {
    "front": "정면(카메라를 똑바로 응시, 얼굴 좌우 대칭)",
    "side": "측면(얼굴을 옆으로 약 90도 돌린 프로필)",
    "angle45": "45도 반측면(정면과 측면 사이, 얼굴을 약 45도 돌림)",
}

# GPT strict json_schema + Gemini responseSchema 양쪽 호환(소문자 type; vision_llm 이 변환).
FACE_QC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["pass", "reject"],
            "description": "얼굴 사진이 개인화 등록에 적합하면 pass, 아니면 reject",
        },
        "reasons": {
            "type": "array",
            "description": "reject 사유코드(pass 면 빈 배열). 해당하는 코드만 나열.",
            "items": {"type": "string", "enum": list(QC_CODES)},
        },
    },
    "required": ["verdict", "reasons"],
}

_QC_PROMPT = (
    "당신은 개인화 아바타 생성을 위한 얼굴 사진 품질검사기입니다. 아래 이미지가 사용자 본인의 "
    "얼굴 등록 사진으로 적합한지 판정하세요. 요청된 각도 슬롯은 '{angle}' = {angle_desc} 입니다.\n\n"
    "다음 결함이 있으면 reject 하고 해당 사유코드만 나열하세요:\n"
    "- occlusion: 마스크·선글라스·손·머리카락 등으로 얼굴 주요부(눈·코·입)가 가려짐. "
    "얼굴이 아예 검출되지 않는 경우도 occlusion 으로 처리.\n"
    "- low_resolution: 흐림·초점흐트러짐·저해상도로 이목구비가 불선명.\n"
    "- multiple_faces: 배경 포함 2인 이상의 얼굴이 보임(본인 1인만 허용).\n"
    "- angle_mismatch: 실제 촬영 각도가 요청 슬롯('{angle}')과 다름.\n\n"
    "단일 인물의 선명하고 가림 없는 얼굴이 요청 각도로 촬영됐으면 verdict=pass, reasons=[] 로 "
    "판정하세요. 결함이 여러 개면 모두 나열하세요. 오직 위 스키마의 JSON 만 출력하세요."
)


class FaceQcUnavailable(RuntimeError):
    """비전 QC provider 미설정/불통/비순응 — 라우트가 503 으로 매핑(fail-safe, 저장 안 함)."""


@dataclass
class FaceQcResult:
    verdict: str  # 'pass' | 'reject'
    reasons: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"


async def evaluate_face_qc(
    settings: Settings, *, image_bytes: bytes, mime: str, angle: str
) -> FaceQcResult:
    """얼굴 바이트 → pass/reject 판정. 실패 시 FaceQcUnavailable(라우트 503).

    §1.4: 이미지·임베딩·랜드마크·파일명 로그 금지 — verdict·reasons·provider 만 관측.
    """
    if not (settings.gemini_api_key or settings.openai_api_key):
        # 비전 키 전무 = 판정 불가 → fail-safe 503 (검증 안 된 얼굴 저장 금지).
        raise FaceQcUnavailable("qc_provider_unconfigured")

    prompt = _QC_PROMPT.format(angle=angle, angle_desc=_ANGLE_DESC.get(angle, angle))
    images = [InlineImage(mime=mime, data=image_bytes)]
    try:
        raw, provider = await analyze_with_fallback(settings, prompt, images, FACE_QC_SCHEMA)
    except VisionError as e:
        # provider 불통/비순응(파싱 실패 포함) → fail-safe 503. 에러 문자열에 얼굴 바이트 없음.
        raise FaceQcUnavailable(str(e)[:200]) from e

    verdict = "pass" if raw.get("verdict") == "pass" else "reject"
    reasons: list[str] = []
    for r in raw.get("reasons") or []:
        if r in QC_CODES and r not in reasons:
            reasons.append(r)

    if verdict == "pass":
        reasons = []
    elif not reasons:
        reasons = ["occlusion"]  # reject 인데 사유 미상 → occlusion 수렴(no_face 흡수, §3.2 비고)

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
    "angle_mismatch": "선택한 각도와 달라요. 안내에 맞춰 정면/측면/45도로 찍어주세요.",
}


def qc_reason_message(reasons: list[str]) -> str:
    """reject 사유코드 → 사용자 안내 카피(api-spec §3.2 카피 초안)."""
    seen = [_QC_MESSAGES[r] for r in reasons if r in _QC_MESSAGES]
    return " ".join(seen) or "얼굴 사진을 사용할 수 없어요. 다른 사진으로 다시 시도해 주세요."
