"""등록 사진 12장 → LoRA 학습셋. 정본은 v6_kit/build_v7.py 의 규칙이고, 서버는 입출력만 바꿨다.

이 파일이 지키는 것:
  · 장수·파일명이 슬롯과 1:1 (반쪽 학습셋은 조용한 품질 손실이다)
  · 같은 입력 두 번 → **같은 바이트**(증강 시드·tar mtime·gzip 헤더 고정)
  · 캡션 어휘가 원본 규칙 그대로 — 특히 **없는 표정은 지어내지 않는다**
  · 12장이 아니거나 관리자 확인 전이면 **거부**
  · 기준 3장은 학습에 안 들어간다(표본 control 로만)
  · 사진 바이트가 디스크에 안 남는다

GPU·YuNet 가중치는 쓰지 않는다 — 검출을 가짜로 갈아 끼우고 합성 그림으로 돈다.
"""
import io
import json
import tarfile

import numpy as np
import pytest
from PIL import Image

from app.facemarket_photos import REFSET_SLOTS, TRAINING_SLOTS
from app.services import lora_dataset as ld


#: 합성 사진. 검출이 고정이라 크기는 박스(360,300,180,240 → 변 540)가 들어갈 만하면 된다.
#: 잡음 PNG 는 압축이 안 돼 인코딩이 비싸다 — 씨앗별로 한 번만 만들어 돌려 쓴다.
_PHOTOS: dict[int, bytes] = {}


def _face_photo(seed: int, size=(640, 960)) -> bytes:
    if seed not in _PHOTOS:
        rng = np.random.default_rng(seed)
        array = rng.integers(40, 210, (size[1], size[0], 3), dtype=np.uint8)
        buffer = io.BytesIO()
        Image.fromarray(array).save(buffer, "PNG")
        _PHOTOS[seed] = buffer.getvalue()
    return _PHOTOS[seed]


@pytest.fixture(autouse=True)
def fake_detector(monkeypatch):
    """YuNet 대신 고정 박스. 가중치는 gitignore 라 CI 에 없다."""
    def detect(image, model_dir=None):
        return (360.0, 300.0, 180.0, 240.0), 0.05, 60.0

    monkeypatch.setattr(ld, "detect4", detect)


@pytest.fixture(autouse=True)
def small_crop(monkeypatch):
    """크롭 변을 줄인다 — 한 번 빌드가 1024² × 96장이라 그대로 두면 이 파일만 1분을 먹는다.

    줄여도 **보는 것이 안 바뀐다**: 여기서 고정하는 건 장수·파일명 짝·캡션 어휘·결정성·게이트고,
    그 어느 것도 변 길이에 안 달렸다. 기하가 서비스 함수 그대로인지는 같은 변으로 재계산해 비교한다.
    """
    from app.agents import face_identity as fi

    monkeypatch.setattr(fi, "CROP", 128)


def _photos(slots=TRAINING_SLOTS, *, start=0):
    return [ld.SourcePhoto(slot, _face_photo(start + index)) for index, slot in enumerate(slots)]


#: 12장 × 1024² 빌드는 한 번이 몇 초다. 구조를 보는 테스트들이 각자 다시 만들 이유가 없다 —
#: 검출이 고정이라 결과도 고정이므로 한 번 만들어 돌려 쓴다(결정성 테스트만 따로 두 번 만든다).
_CACHE: dict = {}


def _built():
    if "items" not in _CACHE:
        _CACHE["items"] = ld.build_items(_photos())
    members, meta = _CACHE["items"]
    return list(members), meta


# ── 장수 · 파일명 ──────────────────────────────────────────────────────────
def test_every_slot_becomes_one_of_each_file():
    members, meta = _built()
    names = [name for name, _ in members]

    for slot in TRAINING_SLOTS:
        for expected in (f"target/{slot}.png", f"control/{slot}.png", f"mask/{slot}.png",
                         f"target/{slot}.txt", f"target/{slot}_aug.png",
                         f"control/{slot}_aug.png", f"mask/{slot}_aug.png",
                         f"target/{slot}_aug.txt"):
            assert expected in names, expected
    assert len(names) == len(TRAINING_SLOTS) * 8
    assert len(meta["train"]) == len(TRAINING_SLOTS) == 12


def test_target_control_and_mask_line_up_one_to_one():
    """ai-toolkit 은 **파일 이름**으로 셋을 짝짓는다 — 하나라도 어긋나면 엉뚱한 마스크로 학습한다."""
    members, _meta = _built()
    by_dir: dict[str, set[str]] = {}
    for name, _data in members:
        folder, _, leaf = name.partition("/")
        if leaf.endswith(".png"):
            by_dir.setdefault(folder, set()).add(leaf)

    assert by_dir["target"] == by_dir["control"] == by_dir["mask"]


def test_every_target_has_its_caption():
    members, _meta = _built()
    pngs = {name[len("target/"):-4] for name, _ in members if name.startswith("target/") and name.endswith(".png")}
    txts = {name[len("target/"):-4] for name, _ in members if name.endswith(".txt")}
    assert pngs == txts


def test_the_crop_is_the_service_geometry_not_a_copy():
    """추론이 쓰는 그 함수여야 한다 — 학습과 추론의 크롭이 다르면 그 차이가 얼굴로 나온다."""
    from app.agents import face_identity as fi

    photo = _face_photo(0)
    with Image.open(io.BytesIO(photo)) as opened:
        image = opened.convert("RGB")
    plan = fi.plan_from_box(image.size[0], image.size[1], (360.0, 300.0, 180.0, 240.0),
                            yaw_proxy=0.05, eye_dist=60.0)
    expected = fi.crop_1024(image, plan)

    members, _meta = ld.build_items([ld.SourcePhoto("sh_front", photo)] + _photos(TRAINING_SLOTS[1:], start=1))
    built = dict(members)["target/sh_front.png"]
    with Image.open(io.BytesIO(built)) as got:
        assert got.convert("RGB").tobytes() == expected.tobytes()


# ── 결정성 ────────────────────────────────────────────────────────────────
def test_the_same_photos_produce_the_same_bytes():
    """★ 같은 입력이면 같은 sha 여야 '이미 만든 학습셋' 을 판단할 수 있다.

    증강 시드뿐 아니라 tar mtime·소유자·gzip 헤더 시각까지 고정해야 실제로 같아진다.
    """
    first, _meta = ld.build_dataset(_photos())
    second, _meta2 = ld.build_dataset(_photos())
    assert first == second


def test_the_augmentation_seed_follows_the_slot_position():
    """자리 번호가 시드다 — 같은 사진이 같은 자리에 있으면 같은 색온도가 나온다."""
    assert ld.color_temp_factor(0, 1.0) == ld.color_temp_factor(0, 1.0)
    assert ld.color_temp_factor(0, 1.0)[0] != ld.color_temp_factor(1, 1.0)[0]


def test_the_colour_factor_is_capped_both_ways():
    """원본이 이미 아주 붉거나 아주 푸르면 배율이 1.35 배를 넘지 않는다(원본 규칙)."""
    _target, low = ld.color_temp_factor(0, 0.01)
    _target2, high = ld.color_temp_factor(0, 100.0)
    assert low == pytest.approx(ld.AUG_FACTOR_CAP)
    assert high == pytest.approx(1 / ld.AUG_FACTOR_CAP)


def test_the_augmented_copy_shares_the_mask_and_the_caption():
    members, _meta = _built()
    lookup = dict(members)
    assert lookup["mask/sh_front_aug.png"] == lookup["mask/sh_front.png"]
    assert lookup["target/sh_front_aug.txt"] == lookup["target/sh_front.txt"]
    assert lookup["target/sh_front_aug.png"] != lookup["target/sh_front.png"]


# ── 캡션 규칙 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("slot,yaw,expected", [
    ("sh_front", 0.05,
     "ohwx man, neutral expression, head and shoulders portrait, facing the camera, outdoors,"
     " soft shaded daylight, photograph"),
    ("sl_smile", 0.10,
     "ohwx man, smiling, head and shoulders portrait, facing the camera, outdoors,"
     " directional sunlight, photograph"),
    ("sr_34", 0.30,
     "ohwx man, neutral expression, head and shoulders portrait, turned three-quarters, outdoors,"
     " directional sunlight, photograph"),
    ("bl_34", 0.60,
     "ohwx man, neutral expression, head and shoulders portrait, in profile, outdoors,"
     " backlit daylight, photograph"),
])
def test_the_caption_follows_the_original_rule(slot, yaw, expected):
    assert ld.caption_for_slot(slot, yaw)[0] == expected


def test_an_unknown_expression_is_omitted_not_guessed():
    """★ 추정 금지. 없는 표정을 지어내면 그 어휘가 그대로 가중치에 들어간다."""
    text, _cond, expression, _angle = ld.caption_for_slot("sh_gaze_left", 0.05)
    assert expression is None
    assert "expression" not in text and "smil" not in text


def test_the_side_word_comes_from_the_name():
    assert ld.caption("그늘__3:4_무표정_왼쪽", 0.30)[0].endswith(
        "turned three-quarters to the left, outdoors, soft shaded daylight, photograph")
    assert ", facing right" in ld.caption("그늘__측면_오른쪽", 0.80)[0]


def test_a_name_with_an_unknown_lighting_is_refused():
    with pytest.raises(ld.DatasetBuildError):
        ld.caption("실내조명__정면_무표정", 0.05)


# ── 게이트 ────────────────────────────────────────────────────────────────
def test_eleven_photos_are_refused():
    with pytest.raises(ld.DatasetBuildError, match="빠진 칸"):
        ld.assert_ready("approved", _photos(TRAINING_SLOTS[:-1]))


@pytest.mark.parametrize("status", ["pending", "reshoot_requested", None])
def test_photos_before_the_admin_check_are_refused(status):
    """확인 전 사진으로 학습하면 반려될 사진이 가중치에 들어간다 — 되돌릴 방법은 재학습뿐이다."""
    with pytest.raises(ld.DatasetBuildError, match="사진 확인"):
        ld.assert_ready(status, _photos())


def test_a_missing_face_stops_the_whole_build(monkeypatch):
    """한 장이라도 못 찾으면 만들다 만 것을 내놓지 않는다."""
    def detect(image, model_dir=None):
        return None

    monkeypatch.setattr(ld, "detect4", detect)
    with pytest.raises(ld.DatasetBuildError, match="얼굴을 못 찾았다"):
        ld.build_items(_photos())


def test_a_photo_that_still_carries_exif_orientation_is_refused():
    """정규화본은 EXIF 를 픽셀에 적용해 둔 것이다 — 태그가 남아 있으면 정규화본이 아니다."""
    buffer = io.BytesIO()
    image = Image.new("RGB", (1024, 1536), (90, 120, 200))
    exif = image.getexif()
    exif[274] = 6
    image.save(buffer, "JPEG", exif=exif)
    photos = [ld.SourcePhoto("sh_front", buffer.getvalue())] + _photos(TRAINING_SLOTS[1:], start=1)

    with pytest.raises(ld.DatasetBuildError, match="EXIF"):
        ld.build_items(photos)


# ── 기준 3장 · 묶음 ───────────────────────────────────────────────────────
def test_the_reference_photos_are_samples_never_training_data():
    """★ 기준이 학습에 섞이면 동일인 채점이 자기 자신을 보게 된다."""
    photos = _photos() + _photos(REFSET_SLOTS, start=100)
    payload, meta = ld.build_dataset(photos)

    names = set(tarfile.open(fileobj=io.BytesIO(payload)).getnames())
    for slot in REFSET_SLOTS:
        assert f"{ld.DATASET_ROOT}/samples_ctrl/{slot}.png" in names
        assert f"{ld.DATASET_ROOT}/target/{slot}.png" not in names
    assert set(meta["train"]) == set(TRAINING_SLOTS)


def test_the_archive_carries_the_meta_and_nothing_else():
    payload, meta = ld.build_dataset(_photos())
    tar = tarfile.open(fileobj=io.BytesIO(payload))
    names = tar.getnames()

    assert all(name.startswith(f"{ld.DATASET_ROOT}/") for name in names)
    assert f"{ld.DATASET_ROOT}/meta.json" in names
    assert len(names) == meta["files"]
    loaded = json.loads(tar.extractfile(f"{ld.DATASET_ROOT}/meta.json").read())
    assert loaded["captions"]["sh_front"].startswith("ohwx man, neutral expression")
    assert 0.0 < loaded["train"]["sh_front"]["mask_area"] <= 1.0


def test_the_tar_has_no_wall_clock_in_it():
    payload, _meta = ld.build_dataset(_photos())
    for info in tarfile.open(fileobj=io.BytesIO(payload)).getmembers():
        assert info.mtime == 0 and info.uid == 0 and info.gid == 0


# ── R2 왕복 ───────────────────────────────────────────────────────────────
class _FakeR2:
    def __init__(self, objects):
        self.objects = objects
        self.puts = []

    def get_bytes(self, key):
        return self.objects[key]

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append((key, len(data), mime))


def _rows(slots=TRAINING_SLOTS):
    return [{"angle": slot, "r2_key": f"orig/{slot}.jpg",
             "normalized_r2_key": f"norm/{slot}.png",
             "qc_status": "passed", "storage_state": "quarantine"} for slot in slots]


def _objects(slots=TRAINING_SLOTS):
    return {f"norm/{slot}.png": _face_photo(index) for index, slot in enumerate(slots)}


def test_it_reads_the_normalized_copy_not_the_original():
    """원본은 사용자가 올린 그대로라 HEIC 일 수 있다 — 여기서 읽으면 통째로 못 연다."""
    r2 = _FakeR2(_objects())
    photos = ld.load_photos(r2, _rows(), TRAINING_SLOTS)
    assert len(photos) == len(TRAINING_SLOTS)


def test_dry_run_uploads_nothing():
    r2 = _FakeR2(_objects())
    result = ld.build_and_upload(r2, model_id="m1", run_id="r1", rows=_rows(),
                                 review_status="approved", apply=False)
    assert r2.puts == []
    assert result["applied"] is False
    assert result["key"] == "facemarket/models/m1/training/r1/dataset.tgz"


def test_apply_uploads_once_to_the_run_key():
    r2 = _FakeR2(_objects())
    result = ld.build_and_upload(r2, model_id="m1", run_id="r1", rows=_rows(),
                                 review_status="approved", apply=True)
    assert len(r2.puts) == 1
    key, size, mime = r2.puts[0]
    assert key == result["key"] and size == result["bytes"] and mime == "application/gzip"


def test_nothing_is_uploaded_when_the_gate_says_no():
    r2 = _FakeR2(_objects())
    with pytest.raises(ld.DatasetBuildError):
        ld.build_and_upload(r2, model_id="m1", run_id="r1", rows=_rows(),
                            review_status="pending", apply=True)
    assert r2.puts == []


def test_the_result_never_carries_bytes_or_urls():
    """반환값은 로그·API 로 흐른다 — 사진 바이트도 presigned URL 도 실리면 안 된다."""
    r2 = _FakeR2(_objects())
    result = ld.build_and_upload(r2, model_id="m1", run_id="r1", rows=_rows(),
                                 review_status="approved", apply=False)
    text = json.dumps(result)
    assert "http" not in text and "X-Amz" not in text
    assert set(result) == {"key", "bytes", "sha256", "files", "train", "samples", "applied"}
