"""등록 사진 업로드 검사 — v7 학습에 실제로 쓴 16장이 한 장도 안 걸려야 한다.

사진 자체는 생체정보라 저장소에 둘 수 없다. 대신 인테이크가 잰 **숫자**를 픽스처로 고정한다
(~/Downloads/lora_runs/v7_intake/manifest_draft.tsv, 원본 6048×8064). 이 16장이 v7 LoRA 를 만든
그 사진들이고, 등록 사진은 이제 그 촬영을 그대로 받는다 — 검사가 이 중 하나라도 거절하면
"스펙대로 찍었는데 안 올라간다" 가 된다.

두 배율로 본다: 원본과, 프론트(imageTranscode)가 긴 변을 4000px 으로 줄인 뒤의 배율.
"""

import pytest

from app import facemarket_photo_check as check

V7_WIDTH = 6048
V7_HEIGHT = 8064
#: 프론트가 긴 변 4000px 으로 줄였을 때의 배율
FRONT_SCALE = 4000 / V7_HEIGHT

#: (슬롯, face_w, eye_ratio, yaw_proxy, n_faces) — manifest_draft.tsv 그대로.
#: 3/4 두 장은 n_faces=2 지만 작은 쪽이 최대 얼굴의 25% 미만이라 n_big=1 로 통과했다.
V7_ROWS = (
    ("sh_front",      1825.4, 0.1422, 0.033, 1),
    ("sh_smile",      1816.9, 0.1398, 0.020, 1),
    ("sh_34",         1464.0, 0.0900, 0.520, 2),
    ("sl_front",      1313.7, 0.1011, 0.019, 1),
    ("sl_smile",      1258.6, 0.0966, 0.015, 1),
    ("sl_34",         1149.1, 0.0552, 0.701, 1),
    ("sr_front",      1487.5, 0.1174, 0.028, 1),
    ("sr_smile",      1349.2, 0.1058, 0.049, 1),
    ("sr_34",         1390.6, 0.0927, 0.397, 2),
    ("bl_front",      1838.0, 0.1437, 0.011, 1),
    ("bl_smile",      1729.3, 0.1383, 0.018, 1),
    ("bl_34",         1511.3, 0.1060, 0.272, 1),
    ("sh_front2",     1773.0, 0.1360, 0.045, 1),
    ("sh_chin_down",  1649.3, 0.1315, 0.076, 1),
    ("sh_gaze_left",  1774.0, 0.1318, 0.091, 1),
    ("sh_gaze_right", 1794.6, 0.1371, 0.013, 1),
)


def _metrics(row, scale=1.0):
    """배율을 먹인 측정값. eye_ratio·yaw_proxy 는 비율이라 배율과 무관하다."""
    _slot, face_w, eye_ratio, yaw, n_faces = row
    return check.PhotoMetrics(
        width=round(V7_WIDTH * scale), height=round(V7_HEIGHT * scale),
        n_faces=n_faces, n_big=1,
        face_w=face_w * scale, eye_ratio=eye_ratio, yaw_proxy=yaw,
    )


def test_the_fixture_covers_every_slot_but_the_side_one():
    """측면 한 장은 학습에 안 써서 인테이크 매니페스트에 없다 — 나머지 16칸은 전부 여기 있다."""
    from app import facemarket_photos as fp

    # 순서는 다르다 — 매니페스트는 조명×컷 순, PHOTO_SLOTS 는 촬영 순서다.
    assert sorted(row[0] for row in V7_ROWS) == sorted(s for s in fp.PHOTO_SLOTS if s != "sh_side")


@pytest.mark.parametrize("scale,label", [(1.0, "원본"), (FRONT_SCALE, "프론트 4000px 축소")])
def test_every_v7_training_photo_passes(scale, label):
    for row in V7_ROWS:
        reason = check.judge_photo(row[0], _metrics(row, scale))
        assert reason is None, f"{label}: {row[0]} 가 {reason} 로 막혔다 (face_w={row[1] * scale:.0f})"


def test_the_narrowest_margins_are_recorded():
    """어느 눈금이 얼마나 아슬아슬한지 — 이 값을 조이면 v7 촬영본이 막힌다."""
    smallest = min(row[1] for row in V7_ROWS) * FRONT_SCALE      # 축소 후 최소 얼굴폭
    frontal_eye = min(row[2] for row in V7_ROWS if row[3] < check.YAW_FRONTAL_MAX)
    widest_34 = max(row[3] for row in V7_ROWS if row[0].endswith("_34"))
    assert smallest == pytest.approx(570.1, abs=0.5) and smallest > check.FACE_W_MIN
    assert frontal_eye == pytest.approx(0.0966) and frontal_eye > check.EYE_RATIO_MIN
    assert widest_34 == 0.701, "해가왼쪽 3/4"
    assert check.YAW_34_MAX > widest_34, "옛 상한 0.65 는 이 사진을 거절했다"


def test_the_turned_photo_is_not_judged_by_eye_distance():
    """해가왼쪽 3/4 는 eye_ratio 0.0552 로 하한 아래다 — 멀어서가 아니라 돌아서다.

    눈간격은 yaw 가 커지면 투영으로 줄어든다. 슬롯 이름이 아니라 **실측 yaw** 로 가른다.
    """
    row = ("sl_34", 1149.1, 0.0552, 0.701, 1)
    assert check.judge_photo("sl_34", _metrics(row)) is None
    # 같은 눈간격인데 정면으로 찍혔다면 그건 정말 멀리서 찍은 것이다
    turned_front = check.PhotoMetrics(V7_WIDTH, V7_HEIGHT, 1, 1, 1149.1, 0.0552, 0.03)
    assert check.judge_photo("sl_front", turned_front) == "face_too_far"


# ── 거절 ────────────────────────────────────────────────────────────────────
def _ok(**over):
    base = dict(width=4000, height=3000, n_faces=1, n_big=1,
                face_w=800.0, eye_ratio=0.12, yaw_proxy=0.03)
    return check.PhotoMetrics(**{**base, **over})


def test_no_face_is_rejected():
    assert check.judge_photo("sh_front", None) == "no_face"


def test_two_people_are_rejected():
    assert check.judge_photo("sh_front", _ok(n_faces=2, n_big=2)) == "multi_face"


def test_a_small_extra_detection_is_not_two_people():
    """v7 3/4 두 장이 n_faces=2 였다 — 최대 얼굴의 25% 미만이면 오검출로 본다."""
    assert check.judge_photo("sh_34", _ok(n_faces=2, n_big=1, yaw_proxy=0.52)) is None


def test_a_face_smaller_than_the_training_crop_is_rejected():
    """342 = 1024/3. 이보다 작으면 1024² 학습 해상도를 업스케일 없이 못 채운다."""
    assert check.judge_photo("sh_front", _ok(face_w=341.0)) == "face_too_small"
    assert check.judge_photo("sh_front", _ok(face_w=342.0)) is None


def test_a_distant_frontal_photo_is_rejected():
    assert check.judge_photo("sh_front", _ok(eye_ratio=0.079)) == "face_too_far"


@pytest.mark.parametrize("yaw,reason", [
    (0.19, "turn_more"), (0.20, None), (0.80, None), (0.81, "turn_less"),
])
def test_the_three_quarter_window(yaw, reason):
    assert check.judge_photo("sh_34", _ok(yaw_proxy=yaw, eye_ratio=0.09)) == reason


def test_the_three_quarter_window_only_applies_to_34_slots():
    assert check.judge_photo("sh_front", _ok(yaw_proxy=0.9, eye_ratio=0.09)) is None


# ── 측면 ────────────────────────────────────────────────────────────────────
def test_a_profile_that_yunet_misses_is_not_rejected():
    """옆얼굴은 YuNet 이 자주 놓친다. 그 한 장은 학습이 아니라 공개 자산용이라 막지 않는다."""
    assert check.judge_photo("sh_side", None) is None
    assert check.judge_photo("sh_side", _ok(face_w=100.0, eye_ratio=0.01)) is None


def test_a_profile_with_two_people_is_still_rejected():
    assert check.judge_photo("sh_side", _ok(n_faces=2, n_big=2)) == "multi_face"


# ── 눈금이 서비스 모듈과 같은가 ──────────────────────────────────────────────
def test_the_frontal_window_matches_the_service_constant():
    """cv2 없이 임포트되려고 값을 복제했다 — 갈라지면 여기서 잡는다."""
    from app.agents import face_identity as fi

    assert check.YAW_FRONTAL_MAX == fi.YAW_FRONT_MAX


def test_every_reason_has_copy():
    for reason in ("unreadable", "no_face", "multi_face", "face_too_far",
                   "face_too_small", "turn_more", "turn_less"):
        assert check.MESSAGES[reason].endswith("주세요.")
    assert check.reject_message(None) == "사진을 다시 찍어 주세요."


# ── 측정(검출기 스텁) ────────────────────────────────────────────────────────
def _jpeg(width, height, orientation=None):
    import io

    from PIL import Image

    buf = io.BytesIO()
    image = Image.new("RGB", (width, height), (128, 128, 128))
    if orientation is None:
        image.save(buf, "JPEG")
    else:
        exif = image.getexif()
        exif[274] = orientation
        image.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def test_measure_applies_exif_orientation(monkeypatch):
    """프론트는 4000px 이하 사진을 원본 그대로 올린다 — 아이폰 세로 사진엔 회전 태그가 남는다."""
    from app.agents import face_identity as fi

    seen = {}

    def fake(image, model_dir=None, **kw):
        seen["size"] = image.size
        return []

    monkeypatch.setattr(fi, "detect_faces", fake)
    assert check.measure_photo(_jpeg(1200, 900, orientation=6)) is None
    assert seen["size"] == (900, 1200), "태그를 픽셀에 적용해야 한다"


def test_measure_reports_the_largest_face(monkeypatch):
    from app.agents import face_identity as fi

    def fake(image, model_dir=None, **kw):
        return [
            fi.FaceDetection(box=(0, 0, 100, 100), yaw_proxy=0.9, eye_dist=30.0, score=0.9,
                             landmarks=()),
            fi.FaceDetection(box=(0, 0, 400, 400), yaw_proxy=0.05, eye_dist=200.0, score=0.9,
                             landmarks=()),
        ]

    monkeypatch.setattr(fi, "detect_faces", fake)
    metrics = check.measure_photo(_jpeg(1000, 1000))
    assert metrics.face_w == 400.0 and metrics.yaw_proxy == 0.05
    assert metrics.eye_ratio == pytest.approx(0.2)
    # 작은 쪽은 큰 얼굴 면적의 6.25% 라 "여러 사람"이 아니다
    assert metrics.n_faces == 2 and metrics.n_big == 1


def test_a_broken_file_is_the_users_problem_not_a_503():
    reason, shot = check.check_enrollment_photo(b"not an image", "sh_front")
    assert reason == "unreadable" and shot == {}


def test_a_detector_failure_is_service_side(monkeypatch):
    from app.agents import face_identity as fi

    def boom(image, model_dir=None, **kw):
        raise FileNotFoundError("face identity weights missing")

    monkeypatch.setattr(fi, "detect_faces", boom)
    with pytest.raises(check.PhotoCheckUnavailable):
        check.measure_photo(_jpeg(800, 600))


# ── 기준셋 합의도(기록만) ────────────────────────────────────────────────────
def test_the_refset_rule_matches_v6_refset_check():
    """중앙값 0.80 AND 최저쌍 0.70 — v6_refset_check.py 와 같은 규칙."""
    assert (check.REFSET_MEDIAN_MIN, check.REFSET_PAIR_MIN) == (0.80, 0.70)


@pytest.mark.parametrize("scores,status", [
    ([0.86, 0.84, 0.83, 0.81, 0.80, 0.75], "ok"),
    ([0.86, 0.84, 0.83, 0.81, 0.80, 0.69], "weak"),      # 한 쌍이 최저선 아래
    ([0.79, 0.78, 0.77, 0.76, 0.75, 0.74], "weak"),      # 중앙값이 낮다
    ([0.9, 0.9], "insufficient"),                        # 표본이 모자란다
])
def test_the_refset_summary_is_a_record_not_a_gate(scores, status):
    summary = check.judge_refset(scores)
    assert summary["status"] == status
    # 어떤 입력에도 예외를 던지지 않는다 — 이 판정은 등록을 막지 않는다
    assert "rule" in summary or status == "insufficient"


def test_the_refset_summary_keeps_the_numbers():
    summary = check.judge_refset([0.9, 0.8, 0.7, 0.6])
    assert summary["pairs"] == 4
    assert summary["median"] == pytest.approx(0.75)
    assert summary["min"] == pytest.approx(0.6)


def test_unscored_pairs_are_dropped_not_counted_as_zero():
    """못 잰 쌍을 0 으로 세면 멀쩡한 기준셋이 weak 으로 보인다."""
    assert check.judge_refset([0.86, 0.84, 0.83, None])["pairs"] == 3
