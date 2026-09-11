"""모델의 선택 동의 — 동의한 사용처에서만 실제 얼굴을 쓴다.

계약(documents/legal/02 v1)은 스튜디오·무지 배경만 허용하고(제3조 2항), 타인 사진과의 합성을
전면 금지한다(제9조 6항). 그래서 스타일링·미러 컷과 룩북 인물 교체는 **모델이 그 사용처에
따로 동의했을 때만** 열린다. 동의하지 않은 모델은 지금 동작(스튜디오 전용) 그대로여야 한다 —
이 파일의 절반은 그 "그대로"를 고정한다.
"""

import pytest

from app import facemarket
from conftest import make_settings

REAL = "11111111-1111-1111-1111-111111111111"
VIRTUAL = "mA"
CONSENT_LOCATION = {"opt_location_cuts": True, "opt_lookbook_person_replace": False}
CONSENT_LOOKBOOK = {"opt_location_cuts": False, "opt_lookbook_person_replace": True}
NO_CONSENT = {"opt_location_cuts": False, "opt_lookbook_person_replace": False}


# ── 어떤 컷이 동의를 요구하는가 ──
@pytest.mark.parametrize("cut_type,needs", [
    ("horizon", False),      # 스튜디오 — 계약 기본 허용
    ("product", False),      # 사람이 없는 컷
    ("detail", False),
    ("styling", True),
    ("mirror", True),
    ("base_edit", True),
])
def test_cut_needs_opt(cut_type, needs):
    assert facemarket.cut_needs_opt(cut_type) is needs


@pytest.mark.parametrize("cut_type,row,allowed", [
    ("horizon", None, True),                     # 동의와 무관하게 항상 허용
    ("horizon", NO_CONSENT, True),
    ("styling", NO_CONSENT, False),
    ("styling", None, False),                    # 행이 없으면 동의 없음
    ("styling", CONSENT_LOCATION, True),
    ("mirror", CONSENT_LOCATION, True),
    ("mirror", CONSENT_LOOKBOOK, False),         # 다른 항목의 동의는 소용없다
    ("base_edit", CONSENT_LOOKBOOK, True),
    ("base_edit", CONSENT_LOCATION, False),
    ("product", CONSENT_LOCATION, False),        # 사람이 없는 컷은 열리지 않는다
])
def test_real_identity_allowed_cut(cut_type, row, allowed):
    assert facemarket.real_identity_allowed_cut(cut_type, row) is allowed


# ── 게이트: 동의 없음 = 지금 그대로 ──
@pytest.mark.parametrize("cut_type", ["styling", "mirror"])
def test_without_consent_the_old_error_stays(cut_type):
    """셀러 화면 문구와 기존 계약이 이 코드를 쓰고 있다 — 바꾸지 않는다."""
    with pytest.raises(Exception) as exc:
        facemarket.reject_real_model_outside_horizon(cut_type, REAL, NO_CONSENT)
    assert exc.value.detail["code"] == "real_model_horizon_only"
    assert exc.value.status_code == 409


def test_lookbook_without_consent_has_its_own_code():
    with pytest.raises(Exception) as exc:
        facemarket.reject_real_model_outside_horizon("base_edit", REAL, NO_CONSENT)
    assert exc.value.detail["code"] == "real_model_use_not_consented"
    assert exc.value.status_code == 409


@pytest.mark.parametrize("cut_type,row", [
    ("horizon", NO_CONSENT), ("styling", CONSENT_LOCATION),
    ("mirror", CONSENT_LOCATION), ("base_edit", CONSENT_LOOKBOOK),
])
def test_consented_cuts_pass_the_gate(cut_type, row):
    assert facemarket.reject_real_model_outside_horizon(cut_type, REAL, row) is None


def test_virtual_models_are_untouched():
    for cut_type in ("horizon", "styling", "mirror", "base_edit", "product"):
        assert facemarket.reject_real_model_outside_horizon(cut_type, VIRTUAL, None) is None


# ── 스타일링 대역(가상 모델) 필요 여부 ──
def test_styling_needs_a_stand_in_only_without_consent():
    # 동의 없음: 가상 모델을 골라야 한다(기존 계약)
    with pytest.raises(Exception) as exc:
        facemarket.resolve_block_model_id("styling", REAL, None, NO_CONSENT)
    assert exc.value.detail["code"] == "styling_model_required"
    assert facemarket.resolve_block_model_id("styling", REAL, VIRTUAL, NO_CONSENT) == VIRTUAL
    # 동의 있음: 대역 없이 그 모델 그대로 — stylingModelId 를 줘도 무시한다
    assert facemarket.resolve_block_model_id("styling", REAL, None, CONSENT_LOCATION) == REAL
    assert facemarket.resolve_block_model_id("styling", REAL, VIRTUAL, CONSENT_LOCATION) == REAL


def test_horizon_and_virtual_paths_unchanged():
    assert facemarket.resolve_block_model_id("horizon", REAL, None, None) == REAL
    assert facemarket.resolve_block_model_id("styling", VIRTUAL, None, None) == VIRTUAL
    assert facemarket.resolve_block_model_id("product", REAL, VIRTUAL, CONSENT_LOCATION) is None


# ── 배경 변경 변형 ──
def test_scene_variation_follows_location_consent():
    payload = {"refBgAssetId": "a1", "changes": []}
    with pytest.raises(Exception) as exc:
        facemarket.reject_real_model_scene_variation(payload, NO_CONSENT)
    assert exc.value.detail["code"] == "real_model_horizon_only"
    assert facemarket.reject_real_model_scene_variation(payload, CONSENT_LOCATION) is None
    # 인자를 안 주면 예전과 똑같이 막는다(호출부 회귀 방지)
    with pytest.raises(Exception):
        facemarket.reject_real_model_scene_variation(payload)


def test_scene_variation_still_limits_change_values_without_consent():
    payload = {"changes": [{"type": "pose", "value": "거리에서 뛰기"}]}
    with pytest.raises(Exception):
        facemarket.reject_real_model_scene_variation(payload, NO_CONSENT)


# ── 플래그: 법률 검토 전에는 저장도 안 한다 ──
def test_opt_flag_defaults_off():
    assert make_settings(gemini_api_key="x", r2_bucket="b").facemarket_opt_uses_enabled is False


def test_create_license_request_accepts_but_flag_decides():
    body = facemarket.CreateLicenseRequest.model_validate({
        "enrollmentId": "e1", "optLocationCuts": True,
        "optLookbookPersonReplace": True, "optConsentVersion": "v2-draft",
    })
    assert body.opt_location_cuts and body.opt_lookbook_person_replace
    # 저장 여부는 라우트에서 플래그와 AND 된다 — 여기서는 모델이 값을 받아들이는지만 본다.
    source = (facemarket.__file__)
    text = open(source, encoding="utf-8").read()
    assert "facemarket_opt_uses_enabled" in text
    assert "opt_location = bool(body.opt_location_cuts) and opt_enabled" in text


def test_consent_lookup_never_raises():
    """조회가 흔들려도 컷 생성이 죽지 않는다 — None(=동의 없음)으로 떨어진다."""
    import asyncio

    class _Boom:
        def cursor(self):
            raise RuntimeError("db down")

    assert asyncio.run(facemarket.consent_license(_Boom(), REAL)) is None
