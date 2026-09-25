"""등록사진 촬영 품질 판정과 기준 사진 합의도 유틸리티.

촬영 품질 판정은 학습 인테이크(`v6_intake.py`) 기준을 옮긴 것이다. 2026-09-25부터
등록사진 업로드는 얼굴·구도 추론을 호출하지 않는다. 업로드를 막는 오탐과 추론 대기를
없애고, 촬영 품질은 관리자 사진 심사에서 확인한다. 본인확인 매칭은 별도 경로다.

판정은 검출과 분리해 둔다(`measure_photo` → `judge_photo`). 회귀 테스트가 v7 실제 학습 사진
16장의 **측정 숫자**를 픽스처로 고정하는데, 사진 자체는 생체정보라 저장소에 둘 수 없기 때문이다.

눈금은 `v6_intake.py` 와 같다. 3/4 상한만 다르다 — 0.65 로 두면 v7 학습에 실제로 쓴
해가왼쪽 3/4(yaw 0.701)가 거절된다.
"""

import logging
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps

from .facemarket_photos import PROFILE_CUTS, PROFILE_NOSE_SIDE, cut_of

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
#: 옆모습 하한 = face_identity.YAW_APPLY_MAX. 얼굴 패스가 **건너뛰기 시작하는** 각도이고,
#: 그보다 덜 돌린 사진은 3/4 와 구분이 안 돼 각도 참조로 쓸 수 없다.
YAW_PROFILE_MIN = 0.65
#: 코 방향 판정 최소 크기. 이보다 작으면 좌우가 애매해 **검사하지 않는다**(반려하지 않는다).
NOSE_SIDE_MIN = 0.25
#: 뒷모습에서 "얼굴이 보인다"고 보는 크기. 멀리 걸린 작은 오검출로 반려하지 않는다.
BACK_FACE_W_MAX = 120.0

#: 거절 사유 → 사용자에게 보이는 문구. 사용자는 지금 촬영 자리에 서 있다 — 다음 동작을 준다.
MESSAGES: dict[str, str] = {
    "unreadable": "사진을 읽지 못했어요. 다른 사진으로 다시 시도해 주세요.",
    "no_face": "얼굴이 안 보여요. 얼굴이 잘 나오게 다시 찍어 주세요.",
    "multi_face": "한 사람만 나오게 다시 찍어 주세요.",
    "face_too_far": "얼굴이 작아요. 한 걸음 다가가서 다시 찍어 주세요.",
    "face_too_small": "얼굴이 작아요. 한 걸음 다가가서 다시 찍어 주세요.",
    "turn_more": "3/4 각도가 덜 됐어요. 고개를 조금 더 돌려서 다시 찍어 주세요.",
    "turn_less": "너무 많이 돌았어요. 반대쪽 눈이 보이게 덜 돌려서 다시 찍어 주세요.",
    "profile_turn_more": "고개를 더 돌려 완전한 옆모습으로 다시 찍어 주세요.",
    "profile_wrong_side": "반대 방향이에요. 코가 화면 {side}쪽을 향하게 돌아서 다시 찍어 주세요.",
    "back_face_visible": "얼굴이 보여요. 뒤돌아서 뒤통수를 찍어 주세요.",
}
#: {side} 를 채울 말.
_SIDE_WORDS = {"left": "왼", "right": "오른"}


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
    #: 코가 눈 중점 기준 어느 쪽인가 — "left" | "right" | None(못 재거나 애매함).
    #: yaw_proxy 는 절댓값이라 좌우를 못 가른다. 옆모습 칸의 방향 검사에만 쓴다.
    nose_side: str | None = None

    def as_log(self) -> dict:
        return {
            "width": self.width, "height": self.height,
            "n_faces": self.n_faces, "n_big": self.n_big,
            "face_w": round(self.face_w, 1),
            "eye_ratio": round(self.eye_ratio, 4),
            "yaw_proxy": round(self.yaw_proxy, 3),
            "nose_side": self.nose_side,
        }


def judge_photo(slot: str, metrics: PhotoMetrics | None) -> str | None:
    """측정값 → 거절 사유(통과면 None). 순수 함수 — 회귀 테스트가 보는 자리다.

    metrics=None 은 "얼굴 미검출". 옆모습·뒷모습은 미검출로 거절하지 않는다 — YuNet 은 옆얼굴을
    자주 놓치고, 뒷모습은 얼굴이 **안 보이는 것이 맞다**.
    """
    cut = cut_of(slot)
    if metrics is None:
        return None if cut in PROFILE_CUTS or cut == "back" else "no_face"
    if cut == "back":
        # 뒤통수 사진에 얼굴이 크게 잡혔다 = 안 돌아섰다. 작은 오검출은 통과시킨다.
        return "back_face_visible" if metrics.face_w > BACK_FACE_W_MAX else None
    if metrics.n_big >= 2:
        return "multi_face"
    if cut in PROFILE_CUTS:
        if metrics.yaw_proxy < YAW_PROFILE_MIN:
            return "profile_turn_more"
        # 방향은 **잴 수 있을 때만** 본다. 코 위치가 애매하면(랜드마크 없음·중앙 근처) 통과시킨다 —
        # 못 재는 것을 이유로 셀러를 되돌려 보내지 않는다.
        want = PROFILE_NOSE_SIDE.get(cut)
        got = metrics.nose_side
        if want and got and got != want:
            return f"profile_wrong_side:{want}"
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
        nose_side=_nose_side(det),
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


def _nose_side(det) -> str | None:
    """YuNet 5점(오른눈·왼눈·코끝·…)으로 코가 화면 어느 쪽인지. 애매하면 None.

    화면 좌표라 x 가 작을수록 왼쪽이다. 눈간격으로 나눠 얼굴 크기와 무관하게 만든다.
    """
    marks = getattr(det, "landmarks", ()) or ()
    if len(marks) < 3:
        return None
    (rx, _ry), (lx, _ly), (nx, _ny) = marks[0], marks[1], marks[2]
    span = abs(float(lx) - float(rx)) or 1.0
    offset = (float(nx) - (float(rx) + float(lx)) / 2.0) / span
    if abs(offset) < NOSE_SIDE_MIN:
        return None
    return "left" if offset < 0 else "right"


def reject_message(reason: str | None) -> str:
    """거절 사유 → 문구. `profile_wrong_side:left` 처럼 값이 붙은 사유도 받는다."""
    key, _, arg = str(reason or "").partition(":")
    text = MESSAGES.get(key, "사진을 다시 찍어 주세요.")
    if arg and "{side}" in text:
        return text.format(side=_SIDE_WORDS.get(arg, arg))
    return text
