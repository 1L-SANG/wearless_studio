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

# 스파이크 전략 B(Identity Lock) 계약 — 리포트가 PASS 판정한 그 프롬프트인지 검증한다.
# 문자열 전체를 테스트에 복제하지 않는다(정본은 서비스 상수 하나여야 한다). 대신 그 전략을
# 전략이게 하는 요소들을 항목으로 고정한다.
IDENTITY_MARKERS = (
    "EXACT same garment",
    "CRITICAL IDENTITY LOCK",
    "Garment Identity:",
    "Construction Details:",
    "Silhouette:",
    "TRANSFORMATION INSTRUCTIONS:",
    "top-down 90-degree overhead angle",
    "Remove any hanger hooks",
)

_TAIL = "Direct overhead top-down view"


def test_prompt_is_the_identity_lock_strategy():
    """전략 B 원문 — 리포트 판정표에서 두 의류 × 두 모델 모두 PASS 한 구성."""
    p = mf.build_prompt("bottom")
    for marker in IDENTITY_MARKERS:
        assert marker in p, marker
    assert "pants completely flat" in p, "하의 명사가 지시문에 반영된다"



def test_top_prompt_swaps_noun_and_silhouette_line():
    """상의엔 'leg cut' 이 가면 안 된다 — 스파이크는 하의 2벌만 검증했고, 그 문장을 그대로
    상의에 보내면 다리 지시가 재렌더에 흘러간다(2026-08-15 전수조사)."""
    top, bottom = mf.build_prompt("top"), mf.build_prompt("bottom")
    assert "garment completely flat" in top and "pants completely flat" in bottom
    assert "leg cut" in bottom, "하의는 스파이크 원문 유지"
    assert "leg cut" not in top, "상의엔 다리 지시 누수 금지"
    assert "sleeve shape" in top and "neckline" in top
    # 그 두 군데(명사·실루엣) 말고는 동일해야 한다 — 전략 문구를 의류별로 갈라 쓰지 않는다.
    norm = lambda p: (p.replace("garment completely flat", "X")
                       .replace("pants completely flat", "X")
                       .replace(mf._TOP_SILHOUETTE, "S")
                       .replace(mf._BOTTOM_SILHOUETTE, "S"))
    assert norm(top) == norm(bottom)




def test_clothing_type_is_case_insensitive():
    assert mf.build_prompt("Bottom") == mf.build_prompt("bottom")
    assert mf.build_prompt(" bottom ") == mf.build_prompt("bottom")



def test_prompt_grey_is_the_same_grey_the_cutout_flattens_onto():
    r, g, b = mc.MATCHING_CUTOUT_BG
    assert f"RGB: {r}, {g}, {b}" in mf.build_prompt("bottom"), "시드 배경색 상수와 한 몸"


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

    assert mf.ALGORITHM_VERSION == "matching-flatlay-v2"
    assert mf.flatlay_fingerprint("h1") == mf.flatlay_fingerprint("h1")
    assert mf.flatlay_fingerprint("h1") != mf.flatlay_fingerprint("h2")
    # 소스 지문을 그대로 다시 해싱한 값이면 안 된다 — 버전이 섞여 있어야 한다.
    assert mf.flatlay_fingerprint("h1") != hashlib.sha256(b"h1").hexdigest()

    before = mf.flatlay_fingerprint("h1")
    before_id = mf.derived_asset_id(matching_item_id="custom_x", source_hash="h1")
    monkeypatch.setattr(mf, "ALGORITHM_VERSION", "matching-flatlay-v9")
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


def test_algorithm_version_bumped_for_strategy_b():
    """v1 프롬프트로 구운 flat-lay 와 파생 신원이 섞이면 안 된다."""
    assert mf.ALGORITHM_VERSION == "matching-flatlay-v2"



def test_default_tier_is_pro_per_spike_report():
    """리포트 추천 조합은 전략 B × Gemini 3 Pro Image — 기본값이 그것이어야 한다."""
    from conftest import make_settings
    from app.agents.model_routing import resolve_model
    s = make_settings()
    assert s.matching_flatlay_tier == "image_high"
    assert resolve_model(s, s.matching_flatlay_tier) == s.model_image_high
    # flash 는 비용 노브로 남는다.
    light = make_settings(matching_flatlay_tier="image_light")
    assert resolve_model(light, light.matching_flatlay_tier) == light.model_image_light
