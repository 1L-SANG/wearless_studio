"""커스텀 매칭 의류 플랫레이 재렌더의 순수 로직 계약.

프롬프트는 스파이크에서 실물로 검증된 문자열이다 — 의류 명사(와 그에 맞춘 동사)를
빼면 한 바이트도 달라지면 안 된다. 여기서 리터럴로 못박아 둔다.
"""
import io
import uuid

import pytest
from PIL import Image

from app.services import matching_cutout as mc
from app.services import matching_flatlay as mf

# 정체성 고정 절(2026-08-15 오너 결정) — 명사만 갈아끼운다. 프롬프트 핀의 일부다.
def _identity(short):
    return (
        f" This must remain the EXACT same {short} as in the photo — identity is fixed. "
        "Preserve the original colour and fabric sheen, the silhouette and proportions, "
        "every seam, stitch, pocket, waistband, closure, label and construction detail "
        "exactly as photographed. Do not redesign, simplify, restyle, or substitute any "
        f"part; only change the pose of the {short} to a flat, front-facing lay."
    )


# 스파이크 승자 프롬프트(하의) + 정체성 고정 절. 이 리터럴이 정본이다.
BOTTOM_PROMPT = (
    "A clean, commercial e-commerce studio flat-lay product photograph of this exact "
    "pair of pants. The pants are laid completely flat and neatly arranged on a solid "
    "neutral light gray surface (RGB 232, 232, 230). Direct overhead top-down view "
    "(bird's-eye perspective), centered in frame with balanced margins. Even, soft "
    "commercial studio lighting with minimal subtle shadow directly beneath. Completely "
    "remove any hangers, clips, floor seams, or background clutter. High resolution, "
    "crisp details, no distortion." + _identity("pants")
)
# 상의는 명사만 중립형으로 바꾸고 단복수(is/are)를 맞춘다. 나머지는 위와 동일.
TOP_PROMPT = (
    "A clean, commercial e-commerce studio flat-lay product photograph of this exact "
    "garment. The garment is laid completely flat and neatly arranged on a solid "
    "neutral light gray surface (RGB 232, 232, 230). Direct overhead top-down view "
    "(bird's-eye perspective), centered in frame with balanced margins. Even, soft "
    "commercial studio lighting with minimal subtle shadow directly beneath. Completely "
    "remove any hangers, clips, floor seams, or background clutter. High resolution, "
    "crisp details, no distortion." + _identity("garment")
)

_TAIL = "Direct overhead top-down view"


def test_bottom_prompt_is_the_spike_string_byte_for_byte():
    assert mf.build_prompt("bottom") == BOTTOM_PROMPT


def test_top_prompt_swaps_only_the_noun_and_keeps_singular_grammar():
    assert mf.build_prompt("top") == TOP_PROMPT
    # 두 프롬프트가 다른 건 명사·동사뿐이다 — 공통 몸통(연출 지시)은 바이트 동일해야 한다.
    _END = "no distortion."
    assert (BOTTOM_PROMPT[BOTTOM_PROMPT.index(_TAIL):BOTTOM_PROMPT.index(_END) + len(_END)]
            == TOP_PROMPT[TOP_PROMPT.index(_TAIL):TOP_PROMPT.index(_END) + len(_END)])
    # 정체성 절도 명사만 다르고 구조는 같다.
    assert "identity is fixed" in BOTTOM_PROMPT and "identity is fixed" in TOP_PROMPT
    assert "The garment is laid" in TOP_PROMPT, "단수엔 is"
    assert "The pants are laid" in BOTTOM_PROMPT, "복수엔 are"
    # "top-down view" 와 부딪히지 않게 상의 명사는 중립형을 쓴다.
    assert "this exact top." not in TOP_PROMPT


@pytest.mark.parametrize("clothing_type", [None, "", "outer", "unknown", "dress"])
def test_unknown_clothing_type_falls_back_to_the_neutral_singular_form(clothing_type):
    # 하의라는 근거가 없으면 중립형이다 — 상의에 "pair of pants" 를 던지지 않는다.
    assert mf.build_prompt(clothing_type) == TOP_PROMPT


def test_clothing_type_is_case_insensitive():
    assert mf.build_prompt("Bottom") == BOTTOM_PROMPT
    assert mf.build_prompt(" bottom ") == BOTTOM_PROMPT


def test_prompt_grey_is_the_same_grey_the_cutout_flattens_onto():
    r, g, b = mc.MATCHING_CUTOUT_BG
    assert f"(RGB {r}, {g}, {b})" in mf.build_prompt("bottom"), "시드 배경색 상수와 한 몸"


# 누끼와 같은 규율 — stale lease 회수로 재실행돼도 같은 asset id·같은 R2 키로 수렴한다.
def test_derived_identity_is_deterministic_and_never_collides_with_the_cutout_thumb():
    first = mf.derived_asset_id(matching_item_id="custom_x", source_hash="h1")
    assert first == mf.derived_asset_id(matching_item_id="custom_x", source_hash="h1")
    assert first != mf.derived_asset_id(matching_item_id="custom_y", source_hash="h1")
    assert first != mf.derived_asset_id(matching_item_id="custom_x", source_hash="h2")
    assert uuid.UUID(first).version == 5
    # 같은 소스 지문으로도 누끼 썸네일과 절대 겹치지 않는다(알고리즘 버전이 지문에 섞인다).
    cutout_thumb = mc.derived_asset_id(role="thumb", matching_item_id="custom_x",
                                       source_hash="h1")
    cutout_grid = mc.derived_asset_id(role="grid", matching_item_id="custom_x",
                                      source_hash="h1")
    assert first not in (cutout_thumb, cutout_grid)


def test_fingerprint_mixes_in_the_flatlay_algorithm_version(monkeypatch):
    import hashlib

    assert mf.ALGORITHM_VERSION == "matching-flatlay-v1"
    assert mf.flatlay_fingerprint("h1") == mf.flatlay_fingerprint("h1")
    assert mf.flatlay_fingerprint("h1") != mf.flatlay_fingerprint("h2")
    # 소스 지문을 그대로 다시 해싱한 값이면 안 된다 — 버전이 섞여 있어야 한다.
    assert mf.flatlay_fingerprint("h1") != hashlib.sha256(b"h1").hexdigest()

    before = mf.flatlay_fingerprint("h1")
    before_id = mf.derived_asset_id(matching_item_id="custom_x", source_hash="h1")
    monkeypatch.setattr(mf, "ALGORITHM_VERSION", "matching-flatlay-v2")
    assert mf.flatlay_fingerprint("h1") != before, "버전이 오르면 신원도 갈린다"
    assert mf.derived_asset_id(matching_item_id="custom_x",
                               source_hash="h1") != before_id


# "내 옷 삭제" 는 purpose 화이트리스트로 파생분을 회수한다. 재렌더본이 그 목록에서
# 벗어나면 셀러가 지워도 R2 객체가 영구히 남는다.
def test_metadata_keeps_the_cleanup_purpose_and_records_the_flatlay_stage():
    meta = mf.metadata_for(source_hash="h", source_asset_id="up-1",
                           matching_item_id="custom_x", model="gemini-3.1-flash-image")
    assert meta["purpose"] == mc.CUTOUT_PURPOSE == "custom_match_cutout"
    assert meta["type"] == mc.CUTOUT_KIND, "커스텀 파생 컷이라는 사실은 그대로"
    assert meta["algorithmVersion"] == mf.ALGORITHM_VERSION
    assert meta["producer"] == mf.PRODUCER
    assert meta["model"] == "gemini-3.1-flash-image"
    assert meta["sourceHash"] == "h" and meta["sourceAssetId"] == "up-1"
    assert "sourceAssetIds" not in meta, "정리 목록은 grid 쪽 한 곳에만"


def test_generation_params_match_the_verified_spike():
    assert (mf.IMAGE_SIZE, mf.ASPECT_RATIO) == ("1K", "1:1")
    assert mf.STAGE == "matching_flatlay"


def _png(size=(64, 64)):
    buf = io.BytesIO()
    Image.new("RGB", size, (120, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def test_encode_uses_the_existing_card_thumbnail_contract():
    thumb = mf.encode_thumbnail(_png((1024, 1024)))
    img = Image.open(io.BytesIO(thumb))
    assert img.format == "JPEG"
    assert max(img.size) == mc.THUMBNAIL_MAX_PX
    assert thumb == mc.encode_thumbnail(_png((1024, 1024))), "누끼와 같은 인코더"


@pytest.mark.parametrize("bad", [b"", b"not-an-image", b"\x89PNG\r\n\x1a\n truncated"])
def test_corrupt_generation_output_is_rejected_not_stored(bad):
    with pytest.raises(Exception):
        mf.encode_thumbnail(bad)


def test_flag_defaults_off():
    from app.config import Settings
    assert Settings.__dataclass_fields__["matching_flatlay"].default == "off"


def test_enabled_reads_the_flag_defensively():
    import types
    assert mf.enabled(types.SimpleNamespace(matching_flatlay="on")) is True
    assert mf.enabled(types.SimpleNamespace(matching_flatlay="off")) is False
    assert mf.enabled(types.SimpleNamespace()) is False, "미설정이면 off"


def test_prompt_carries_identity_lock():
    """정체성 고정 절(2026-08-15 오너 결정) — 재렌더가 옷을 다시 디자인하지 못하게 명문화."""
    p = mf.build_prompt("bottom")
    assert "identity is fixed" in p
    assert "Do not redesign" in p
    assert "only change the pose" in p


def test_model_tier_knob_switches_to_pro(monkeypatch):
    """MATCHING_FLATLAY_TIER=image_high 면 pro 모델로 렌더한다. 기본은 image_light(비용)."""
    from conftest import make_settings
    light = make_settings()
    assert getattr(light, "matching_flatlay_tier", "image_light") == "image_light"
    high = make_settings(matching_flatlay_tier="image_high")
    from app.agents.model_routing import resolve_model
    assert resolve_model(high, high.matching_flatlay_tier) == high.model_image_high
