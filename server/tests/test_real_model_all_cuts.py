"""실제(REAL) 모델 = 모든 컷. 모델 동의로 컷을 막는 규칙은 없다.

2026-09-11 사용자 결정으로 이전 계약(스튜디오 전용 + 선택 동의로만 확장)이 폐기됐다.
셀러가 실제 모델을 고르면 스타일링·미러·룩북 인물 교체까지 그 얼굴이 들어간다.

fm_licenses.opt_location_cuts · opt_lookbook_person_replace 컬럼과
FACEMARKET_OPT_USES_ENABLED 플래그는 **수집 경로에만** 남는다 — 이 파일은 그 값이
판정으로 되돌아오지 않는다는 것까지 고정한다(되돌아오면 컷이 조용히 막힌다).
"""

import pathlib

import pytest

from app import facemarket
from conftest import make_settings

REAL = "11111111-1111-1111-1111-111111111111"
VIRTUAL = "mA"
NO_CONSENT = {"opt_location_cuts": False, "opt_lookbook_person_replace": False}


@pytest.mark.parametrize("cut_type,allowed", [
    ("horizon", True),
    ("styling", True),
    ("mirror", True),
    ("base_edit", True),      # 룩북 인물 교체 — 셀러 쪽 권리 확인 게이트는 별도로 유지된다
    ("product", False),       # 사람이 없는 컷
    ("detail", False),
    (None, False),
    ("", False),
])
def test_real_identity_allowed_cut(cut_type, allowed):
    assert facemarket.real_identity_allowed_cut(cut_type) is allowed


def test_consent_values_are_not_an_input_anymore():
    """동의 값이 판정에 끼어들 자리가 없다 — 인자 자체를 받지 않는다."""
    import inspect

    assert list(inspect.signature(facemarket.real_identity_allowed_cut).parameters) == ["cut_type"]
    assert list(inspect.signature(facemarket.resolve_block_model_id).parameters) == [
        "cut_type", "model_id"]
    # 동의 false 인 라이선스가 있어도 컷은 열린다(예전엔 여기서 막혔다).
    assert facemarket.real_identity_allowed_cut("styling") is True
    assert NO_CONSENT["opt_location_cuts"] is False


@pytest.mark.parametrize("cut_type", ["horizon", "styling", "mirror"])
def test_every_worn_block_uses_the_selected_model(cut_type):
    assert facemarket.resolve_block_model_id(cut_type, REAL) == REAL
    assert facemarket.resolve_block_model_id(cut_type, VIRTUAL) == VIRTUAL


def test_product_blocks_have_no_model_and_missing_id_stays_none():
    assert facemarket.resolve_block_model_id("product", REAL) is None
    assert facemarket.resolve_block_model_id("horizon", None) is None


def test_the_old_gates_are_gone():
    """되살아나면 컷이 다시 막힌다 — 이름째 없어야 한다."""
    for name in ("reject_real_model_outside_horizon", "reject_real_model_scene_variation",
                 "cut_needs_opt", "model_opt_allows"):
        assert not hasattr(facemarket, name), name


def test_no_caller_still_raises_the_old_errors():
    root = pathlib.Path(facemarket.__file__).resolve().parents[1]
    for rel in ("app/routes.py", "app/workers/editor_image_job.py",
                "app/workers/detail_page_job.py", "app/facemarket.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "real_model_horizon_only" not in text, rel
        assert "real_model_use_not_consented" not in text, rel
        assert "styling_model_required" not in text, rel


def test_styling_blocks_are_real_in_the_detail_worker():
    """컷 소스 판정이 horizon 에만 REAL 을 주던 자리 — 그 규칙이 남아 있으면 안 된다."""
    root = pathlib.Path(facemarket.__file__).resolve().parents[1]
    text = (root / "app/workers/detail_page_job.py").read_text(encoding="utf-8")
    assert 'if source == "REAL" and facemarket.real_identity_allowed_cut(b.get("cutType"))' in text
    assert 'b.get("cutType") == "horizon"' not in text


def test_editor_injects_the_real_face_in_every_worn_cut():
    root = pathlib.Path(facemarket.__file__).resolve().parents[1]
    text = (root / "app/workers/editor_image_job.py").read_text(encoding="utf-8")
    assert 'facemarket.real_identity_allowed_cut(normalized["cutType"])' in text
    assert 'normalized["cutType"] == "horizon"' not in text


# ── 수집 경로는 그대로 남는다(판정에 안 쓸 뿐) ──
def test_opt_columns_are_still_collected_behind_the_flag():
    assert make_settings(gemini_api_key="x", r2_bucket="b").facemarket_opt_uses_enabled is False
    body = facemarket.CreateLicenseRequest.model_validate({
        "enrollmentId": "e1", "optLocationCuts": True,
        "optLookbookPersonReplace": True, "optConsentVersion": "v2-draft",
    })
    assert body.opt_location_cuts and body.opt_lookbook_person_replace
    text = pathlib.Path(facemarket.__file__).read_text(encoding="utf-8")
    assert "opt_location = bool(body.opt_location_cuts) and opt_enabled" in text
    assert "사용 안 함, 2026-09-11 사용자 결정" in text


def test_license_lookup_never_raises():
    """조회가 흔들려도 컷 생성이 500 으로 새지 않는다 — None 은 뒤에서 409 로 막힌다."""
    import asyncio

    class _Boom:
        def cursor(self):
            raise RuntimeError("db down")

    assert asyncio.run(facemarket.consent_license(_Boom(), REAL)) is None
