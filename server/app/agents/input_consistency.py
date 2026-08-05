"""AG-IC 입력 사진 동일성 — 셀러가 올린 사진들이 **같은 옷**인가.

왜 필요한가: 입력 화면은 Front/Back/Detail/Fit 슬롯에 자유 업로드를 받는다. 여기서 다른
상품 사진이 한 장 섞여 들어가도 파이프라인은 아무 말 없이 끝까지 돈다. 셀러는 마네킹 생성이
다 끝난 뒤에야 결과물이 이상하다는 걸 알게 되고, 그때는 이미 시간과 호출을 다 태운 뒤다.

설계 규율(비용 비대칭이 전부를 지배한다):
- **미탐은 손해가 0**이다. 이 판정이 없던 어제와 같을 뿐이다.
- **오탐은 마이너스**다. 멀쩡한 사진을 의심하게 만들어 셀러가 지우고 다시 올리게 한다.
  → 확신할 때만 mismatch. 애매하면 unclear. 프롬프트도 스키마도 그쪽으로 기울여 둔다.
- **게이트 아님**. 어떤 잡도 막지 않는다. 프론트는 경고를 띄우되 '계속 진행'이 항상 열려 있다.
- **판정 실패 = 스킵**(None). 관찰용 축이 분석 잡을 죽이면 안 된다 — mannequin_pattern_qc 와 같다.

왜 색상 비교(OpenCV)가 아니라 VLM 인가: 검은 후드와 검은 맨투맨은 색이 같다. 색상 거리로는
원리적으로 구분되지 않는다. 반대로 같은 옷의 앞/뒤, 확대 디테일 컷, 조명이 다른 착용컷은
색상 거리가 크게 벌어져 오탐이 된다 — 위 비용 비대칭에서 가장 피해야 할 방향이다.
"""

import asyncio

from .gemini_image import InlineImage
from .prompts import clean_text
from .vision_llm import VisionError, analyze_with_fallback

# 슬롯 → 이 사진이 왜 앞면과 달라 보일 수 있는지. 이 설명이 없으면 뒷면 컷이 통째로
# mismatch 로 쏟아진다(뒤태는 앞과 다르게 생긴 것이 정상이다).
_SLOT_NOTE = {
    "Front": "front view of the garment",
    "Back": "BACK view — it is normal for this to look different from the front: "
            "no front print, no buttons or zip, a different neckline shape",
    "Detail": "DETAIL close-up at a much higher magnification — it may show only a "
              "small part such as a care label, a button, a zip, or the cuff, and may "
              "not show the garment's overall shape at all",
    "Fit": "worn-fit photo — a person is wearing the garment, so skin, hair, background "
           "and folds are expected, and the colour may shift under different lighting",
}

# 1:1 비교는 판정 하나에 사진 2장뿐이라 medium 을 써도 콜당 비용·지연이 작다. low 에서는
# 모델이 "둘 다 상의니까 같은 옷" 수준의 얕은 대조로 끝내는 회차가 있었다.
_THINKING = "medium"

# 이 값 미만의 확신은 mismatch 로 취급하지 않는다 — 2차 방어선.
# 경고가 차단이 아니라 배너·모달(무시 가능)이라 미탐이 오탐보다 비싸다고 판단해 0.7 → 0.6.
MIN_MISMATCH_CONFIDENCE = 0.6


def build_prompt(slot: str) -> str:
    """1:1 비교 프롬프트. 사진 2장만 놓고 묻는다.

    한 콜에 4장을 넣고 "어느 게 다른지 골라라"로 물었을 때는 대조가 흐릿해져, 판정이 맞아도
    사유가 엉뚱한 옷을 지목했다(2026-07-31 실측: 꽃무늬 블라우스를 "스트라이프 셔츠"로 서술).
    비교 대상이 둘뿐이면 모델이 각 사진을 실제로 기술해야 하므로 근거가 검증 가능해진다.
    """
    return (
        "A seller uploaded these two photos claiming both show the SAME single garment they "
        "are selling. Photo 1 is the front view. Photo 2 is the "
        f"{_SLOT_NOTE.get(slot, 'product view')}.\n\n"
        "Decide whether photo 2 really shows the same garment as photo 1, or a DIFFERENT "
        "garment mixed in by mistake.\n\n"
        "First, describe what garment you see in each photo (this forces you to actually look). "
        "Then compare.\n\n"
        "IGNORE COMPLETELY — none of these mean a different garment:\n"
        "- lighting, white balance, exposure, colour cast, sharpness, camera or phone\n"
        "- magnification and cropping: a close-up of one cuff is the same garment\n"
        "- viewing angle, whether it is laid flat, hung, or worn by a person\n"
        "- how the cloth is folded, draped, or wrinkled\n"
        "- background, props, hangers, and any person in the photo\n"
        "- front versus back: a back view legitimately lacks the front's prints and openings\n\n"
        "JUDGE ONLY the garment's own identity: the category (hoodie vs crewneck vs shirt vs "
        "blouse vs knit), sleeve length, collar or neckline construction, the fabric and its "
        "knit or weave, the colour of the cloth, printed or embroidered artwork and logos, the "
        "fastening (zip vs buttons vs pullover), and distinctive trims such as a bow or ruffle.\n\n"
        "COLOUR NEEDS CARE. The same garment photographed twice often comes out warmer in one "
        "shot and cooler in the other, so it reads as brown in one photo and grey in the other, "
        "or as navy in one and black in the other. That is the camera, not the garment. A shift "
        "in warmth or brightness within the same tone is NEVER a mismatch on its own. Only treat "
        "colour as evidence when the hues are plainly different — red versus black, white versus "
        "navy, patterned versus plain.\n\n"
        "So: answer 'mismatch' when the garments differ in a STRUCTURAL property (category, "
        "sleeve length, neckline, fastening, fabric knit or weave, print or logo, trim), or when "
        "their colours are plainly different in the sense just described. If the only difference "
        "you can name is a shade or warmth shift, answer 'match'.\n"
        "Answer 'unclear' only when photo 2 is too dark, too blurred, or too tightly cropped to "
        "identify anything. Otherwise answer 'match'.\n\n"
        "Do not stretch to explain a real difference away either. If photo 1 is a patterned "
        "blouse and photo 2 is a plain dark knit, that is a mismatch even though both are tops.\n\n"
        "Fill garment1 and garment2 with a short Korean noun phrase for what each photo shows "
        "(for example \"꽃무늬 리본 블라우스\", \"네이비 라운드넥 니트\"). Set reason to one short "
        "Korean sentence naming the concrete difference, built from those two descriptions "
        "(for example \"앞면은 꽃무늬 블라우스인데 이 사진은 네이비 니트예요\"). Leave reason "
        "empty when the verdict is not 'mismatch'. Never comment on photo quality or background."
    )


def schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            # 두 옷을 각각 기술하게 만든다 — 기술 없이 판정만 뽑으면 모델이 대조를 건너뛰고
            # 사유를 지어낸다. 이 두 필드는 사유 문구의 재료이자 판정의 검산이다.
            "garment1": {"type": "string"},
            "garment2": {"type": "string"},
            "verdict": {"type": "string", "enum": ["match", "mismatch", "unclear"]},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["garment1", "garment2", "verdict", "confidence", "reason"],
    }


def validate_pair(raw: dict) -> dict:
    """1:1 판정 1건 정리. mismatch 는 (a) 확신이 임계 이상이고 (b) 사유가 실제로 있을 때만
    살아남는다 — 근거 없는 경고는 셀러가 무엇을 고쳐야 할지 알 수 없어 불안만 남긴다."""
    raw = raw or {}
    verdict = raw.get("verdict")
    if verdict not in ("match", "mismatch", "unclear"):
        verdict = "unclear"

    value = raw.get("confidence")
    confidence = 0.0 if isinstance(value, bool) or not isinstance(value, (int, float)) \
        else max(0.0, min(1.0, float(value)))

    reason = clean_text(raw.get("reason"), 120)
    g1, g2 = clean_text(raw.get("garment1"), 60), clean_text(raw.get("garment2"), 60)
    # 모델이 사유를 비웠지만 두 옷을 기술했으면 그걸로 만든다 — 판정을 근거 없다고 버리는 것보다
    # 셀러가 읽을 수 있는 문장을 조립하는 편이 낫다.
    if verdict == "mismatch" and not reason and g1 and g2:
        reason = f"앞면은 {g1}인데 이 사진은 {g2}예요"

    if verdict == "mismatch" and (confidence < MIN_MISMATCH_CONFIDENCE or not reason):
        verdict = "match"
    return {"verdict": verdict, "confidence": confidence,
            "reason": reason if verdict == "mismatch" else "",
            "garment1": g1, "garment2": g2}


async def _judge_pair(settings, reference: InlineImage, candidate: InlineImage,
                      slot: str) -> dict:
    raw, _provider = await analyze_with_fallback(
        settings, build_prompt(slot), [reference, candidate], schema(),
        thinking_level=_THINKING)
    return validate_pair(raw)


async def judge(settings, images: list[InlineImage], slots: list[str]) -> dict | None:
    """앞면 vs 나머지 각 사진을 **1:1 로 따로** 묻고 합친다. 사진 1장이면 None(판정 스킵).

    한 콜에 전부 넣지 않는 이유는 build_prompt 주석에 있다. 호출은 병렬이라 지연은 가장 느린
    1건과 같고, 한 건이 실패해도 나머지 판정은 살아남는다(전부 실패면 예외가 올라간다).
    """
    if len(images) < 2 or len(slots) != len(images):
        return None
    results = await asyncio.gather(
        *(_judge_pair(settings, images[0], images[i], slots[i])
          for i in range(1, len(images))),
        return_exceptions=True,
    )
    offending, confidences, ok = [], [], False
    for i, res in enumerate(results, start=1):
        if isinstance(res, BaseException):
            continue          # 이 사진만 판정 실패 — 나머지로 계속한다
        ok = True
        confidences.append(res["confidence"])
        if res["verdict"] == "mismatch":
            offending.append({"index": i + 1, "slot": slots[i], "reason": res["reason"]})
    if not ok:
        raise VisionError("input_consistency: 모든 사진 판정 실패")
    if offending:
        return {"verdict": "mismatch",
                "confidence": max(c for c in confidences) if confidences else 0.0,
                "offending": offending}
    return {"verdict": "match",
            "confidence": min(confidences) if confidences else 0.0, "offending": []}
