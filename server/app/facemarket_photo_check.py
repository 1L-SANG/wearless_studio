"""등록 사진 업로드 즉시 검사 — 촬영 스펙을 지켰는가.

16칸 스펙(2026-09-14)은 등록 사진을 **그대로 LoRA 학습셋으로 쓴다**. 그래서 학습 인테이크
(`v6_intake.py`)가 촬영본을 거르던 판정을 업로드 시점으로 당겨 왔다 — 나중에 학습 단계에서
"이 사진은 못 쓴다" 를 알게 되면 사용자는 이미 촬영 자리를 떠난 뒤다.

`fm_face_match_enabled`(본인확인 QC)와는 **별개**이고 항상 켜져 있다. 저 QC 는 "신분증과 같은
사람인가", 이 검사는 "학습에 쓸 수 있는 사진인가" 를 본다.

판정은 검출과 분리해 둔다(`measure_photo` → `judge_photo`). 회귀 테스트가 v7 실제 학습 사진
16장의 **측정 숫자**를 픽스처로 고정하는데, 사진 자체는 생체정보라 저장소에 둘 수 없기 때문이다.

눈금은 `v6_intake.py` 와 같다. 3/4 상한만 다르다 — 0.65 로 두면 v7 학습에 실제로 쓴
해가왼쪽 3/4(yaw 0.701)가 거절된다.
"""

import logging
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps

from .facemarket_photos import cut_of

log = logging.getLogger(__name__)

#: 눈간격 / 사진 폭. 정면에서 이보다 작으면 멀리서 찍힌 것이다.
#: 각도가 붙으면 두 눈 간격이 투영으로 줄어들어 각도 사진에서는 의미가 없다 — **실측 yaw 가
#: 정면일 때만** 적용한다(v6_intake.EYE_RATIO_FRONTAL_ONLY 와 같은 규칙).
EYE_RATIO_MIN = 0.08
#: face_identity.YAW_FRONT_MAX 와 같은 값. 이 모듈은 cv2 없이 임포트될 수 있어야 해서 복제한다
#: (같은 값인지는 test_facemarket_photo_check 가 지킨다).
YAW_FRONTAL_MAX = 0.25
#: 최대 얼굴 면적의 이 비율 이상인 얼굴이 2개 이상이면 "여러 사람". 작은 오검출은 통과한다 —
#: v7 학습본 3/4 두 장이 n_faces=2 인데도 정상 사진이었다.
MULTI_AREA_FRAC = 0.25
#: 얼굴폭 하한 = 1024/3. 크롭 변이 3×얼굴폭이라, 이보다 크면 1024² 학습 해상도를 업스케일 없이 채운다.
FACE_W_MIN = 342.0
#: 3/4 컷 창. 하한은 v6_intake 와 같은 0.20, 상한은 v7 실측(해가왼쪽 0.701)을 담도록 0.80.
YAW_34_MIN, YAW_34_MAX = 0.20, 0.80

#: 거절 사유 → 사용자에게 보이는 문구. 사용자는 지금 촬영 자리에 서 있다 — 다음 동작을 준다.
MESSAGES: dict[str, str] = {
    "unreadable": "사진을 읽지 못했어요. 다른 사진으로 다시 시도해 주세요.",
    "no_face": "얼굴이 안 보여요. 얼굴이 잘 나오게 다시 찍어 주세요.",
    "multi_face": "한 사람만 나오게 다시 찍어 주세요.",
    "face_too_far": "얼굴이 작아요. 한 걸음 다가가서 다시 찍어 주세요.",
    "face_too_small": "얼굴이 작아요. 한 걸음 다가가서 다시 찍어 주세요.",
    "turn_more": "3/4 각도가 덜 됐어요. 고개를 조금 더 돌려서 다시 찍어 주세요.",
    "turn_less": "너무 많이 돌았어요. 반대쪽 눈이 보이게 덜 돌려서 다시 찍어 주세요.",
}


#: 기준 3장끼리의 SFace 합의 눈금 — v6_refset_check.py 규칙(중앙값 0.80 · 최저쌍 0.70).
#: **막지 않는다.** 등록이 이 촬영으로 처음 들어오는 중이라 문턱이 실데이터에 맞는지 아직 모른다.
#: 기록만 모아 두고, 차단 여부는 첫 실데이터를 보고 정한다.
REFSET_MEDIAN_MIN = 0.80
REFSET_PAIR_MIN = 0.70


def judge_refset(scores) -> dict:
    """기준 사진끼리의 점수 → 요약. 순수 함수이고, 아무것도 막지 않는다."""
    values = sorted(float(v) for v in scores if v is not None)
    if len(values) < 3:  # 기준 3장이면 3쌍 — 한 쌍이라도 못 재면 판정할 표본이 아니다
        return {"status": "insufficient", "pairs": len(values)}
    middle = len(values) // 2
    median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    passed = median >= REFSET_MEDIAN_MIN and values[0] >= REFSET_PAIR_MIN
    return {
        "status": "ok" if passed else "weak",
        "pairs": len(values),
        "median": round(median, 4),
        "min": round(values[0], 4),
        "rule": f"median>={REFSET_MEDIAN_MIN} and min>={REFSET_PAIR_MIN}",
    }


class PhotoCheckUnavailable(RuntimeError):
    """검출기를 돌리지 못했다 — 사용자 잘못이 아니므로 503 으로 돌려준다(qc_unavailable 과 같은 규칙)."""


@dataclass(frozen=True, slots=True)
class PhotoMetrics:
    """사진 한 장의 측정값. 판정은 여기 들어 있지 않다."""

    width: int
    height: int
    n_faces: int
    n_big: int
    face_w: float
    eye_ratio: float
    yaw_proxy: float

    def as_log(self) -> dict:
        return {
            "width": self.width, "height": self.height,
            "n_faces": self.n_faces, "n_big": self.n_big,
            "face_w": round(self.face_w, 1),
            "eye_ratio": round(self.eye_ratio, 4),
            "yaw_proxy": round(self.yaw_proxy, 3),
        }


def judge_photo(slot: str, metrics: PhotoMetrics | None) -> str | None:
    """측정값 → 거절 사유(통과면 None). 순수 함수 — 회귀 테스트가 보는 자리다.

    metrics=None 은 "얼굴 미검출". 측면은 YuNet 이 옆얼굴을 놓치는 일이 잦아 미검출로 거절하지
    않는다(그 한 장은 학습이 아니라 공개 자산용이다).
    """
    cut = cut_of(slot)
    if metrics is None:
        return None if cut == "side" else "no_face"
    if metrics.n_big >= 2:
        return "multi_face"
    if cut == "side":
        return None
    if metrics.yaw_proxy < YAW_FRONTAL_MAX and metrics.eye_ratio < EYE_RATIO_MIN:
        return "face_too_far"
    if metrics.face_w < FACE_W_MIN:
        return "face_too_small"
    if cut == "34":
        if metrics.yaw_proxy < YAW_34_MIN:
            return "turn_more"
        if metrics.yaw_proxy > YAW_34_MAX:
            return "turn_less"
    return None


def measure_photo(data: bytes, *, model_dir: str | None = None) -> PhotoMetrics | None:
    """업로드 바이트 → 측정값. 얼굴이 없으면 None.

    EXIF Orientation 을 **픽셀에 실제로 적용한 뒤** 잰다. 프론트가 긴 변 4000px 이하 사진은
    원본 그대로 올리므로(imageTranscode.toUploadableImage) 세로로 찍은 아이폰 사진에는 회전
    태그가 그대로 남아 있다 — 그걸 무시하면 누운 사진을 재게 된다.

    읽지 못하는 파일은 ValueError, 검출기를 못 돌린 경우는 PhotoCheckUnavailable.
    """
    try:
        with Image.open(BytesIO(data)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except Exception as exc:  # noqa: BLE001 — 깨진 파일은 사용자에게 되돌려 준다
        raise ValueError("unreadable") from exc
    try:
        from .agents import face_identity  # 지연 임포트 — cv2 없는 환경에서도 이 모듈은 임포트된다

        faces = face_identity.detect_faces(image, model_dir)
    except Exception as exc:  # noqa: BLE001 — 가중치 부재·cv2 부재·검출 실패는 전부 인프라 문제다
        raise PhotoCheckUnavailable(type(exc).__name__) from exc
    width, height = image.size
    if not faces:
        return None
    areas = [face.box[2] * face.box[3] for face in faces]
    biggest = max(areas)
    n_big = sum(1 for area in areas if area >= MULTI_AREA_FRAC * biggest)
    det = faces[areas.index(biggest)]
    return PhotoMetrics(
        width=width, height=height, n_faces=len(faces), n_big=n_big,
        face_w=float(det.box[2]),
        eye_ratio=(det.eye_dist / width) if width else 0.0,
        yaw_proxy=float(det.yaw_proxy),
    )


def check_enrollment_photo(data: bytes, slot: str, *, model_dir: str | None = None) -> tuple[str | None, dict]:
    """업로드 한 장을 검사한다. 반환 (거절 사유 또는 None, 로그용 측정값)."""
    try:
        metrics = measure_photo(data, model_dir=model_dir)
    except ValueError:
        return "unreadable", {}
    return judge_photo(slot, metrics), (metrics.as_log() if metrics else {"n_faces": 0})


def reject_message(reason: str | None) -> str:
    return MESSAGES.get(reason or "", "사진을 다시 찍어 주세요.")
