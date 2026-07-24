"""AG-06 상세페이지 인물 일관성 — 폴백 해석과 워커 fail-open 회귀 테스트.

버그: 실존 모델을 골랐는데 facemarket off 라 해석 불가(select_source=VIRTUAL 이지만 가상 registry
밖) → 참조 0장 → 컷마다 인물 랜덤. 폴백으로 전 컷 동일 인물 보장하는지 검증."""

import asyncio

from app.agents.cut_generator import (
    needs_identity_fallback,
    real_identity_plan,
    resolve_effective_model_id,
)
from app.workers import detail_page_job as dpj
from conftest import fake_worker_app, make_settings, worker_job

VIRT = {"mA", "mB", "mC"}


def test_virtual_selection_honored():
    # 진짜 가상모델을 고르면 그대로, 폴백/치환 없음
    assert resolve_effective_model_id("mA", fallback_model_id="mB", virtual_ids=VIRT) == ("mA", False)
    assert resolve_effective_model_id("mC", fallback_model_id="mB", virtual_ids=VIRT) == ("mC", False)


def test_real_uuid_dropped_falls_back_and_warns():
    # 실존 UUID(가상 밖) → 폴백 + substituted=True(경고 대상) = 버그의 핵심 케이스
    eff, sub = resolve_effective_model_id(
        "d2e74c66-1ea9-4113-a260-eafec115f36f", fallback_model_id="mB", virtual_ids=VIRT)
    assert eff == "mB" and sub is True


def test_none_selection_falls_back_silently():
    # 미선택 → 일관성 위해 폴백하되 경고 없음(치환할 선택이 없었음)
    assert resolve_effective_model_id(None, fallback_model_id="mB", virtual_ids=VIRT) == ("mB", False)


def test_empty_fallback_keeps_existing_behavior():
    # 폴백 비활성(빈 문자열) → 기존 동작 유지(치환 안 함)
    assert resolve_effective_model_id("real-uuid", fallback_model_id="", virtual_ids=VIRT) == ("real-uuid", False)
    assert resolve_effective_model_id(None, fallback_model_id="", virtual_ids=VIRT) == (None, False)


def test_invalid_fallback_id_no_substitution():
    # 폴백 id 가 registry 밖이면 폴백 불가 → 기존 동작
    assert resolve_effective_model_id("real-uuid", fallback_model_id="mZ", virtual_ids=VIRT) == ("real-uuid", False)


def _patch_worker(monkeypatch, captured):
    async def fake_project(conn, user_id, project_id):
        return {"copywriting": False}

    async def fake_storyboard(conn, project_id):
        return [{"id": "b1", "source": "ai", "cutType": "styling", "shot": "full"}]

    async def fake_product(conn, project_id):
        return {
            "clothing_type": "top",
            "colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}],
        }

    async def fake_analysis(conn, project_id):
        return {"selectedModelId": "real-uuid"}

    async def fake_asset(conn, user_id, asset_id):
        return {"mime_type": "image/png", "r2_key": "products/front.png"}

    async def fake_generate(
        settings, gemini, cut_spec, product, images, *,
        analysis=None, manifest=None, has_face=False,
    ):
        captured["cut_spec"] = cut_spec
        return b"IMG", "image/png"

    def fake_assemble(storyboard, cut_results, copy_results, product, copywriting, **kwargs):
        return [{"id": "page", "kind": "benefit", "elements": []}]

    async def fake_success(conn, **kwargs):
        captured["success"] = kwargs
        return {"editor_blocks": kwargs["editor_blocks"], "available": 99}

    async def fake_failure(conn, **kwargs):
        captured["failure"] = kwargs
        return {"status": "failed"}

    async def fake_emit(pool, job_id, event_type, payload):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", fake_product)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_success)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_failure)
    monkeypatch.setattr(dpj, "_emit", fake_emit)


def test_worker_disabled_fallback_survives_unavailable_manifest(monkeypatch):
    captured = {"registry_reads": 0}
    _patch_worker(monkeypatch, captured)

    def unavailable_registry():
        captured["registry_reads"] += 1
        raise OSError("manifest unavailable")

    monkeypatch.setattr(dpj.cut_generator, "load_virtual_model_registry", unavailable_registry)
    app = fake_worker_app(make_settings(
        gemini_api_key="x",
        r2_bucket="b",
        facemarket_enabled=False,
        detailpage_fallback_model_id="",
    ))

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    # 폴백 판정용 선행 로드는 하지 않는다. 이후 기존 가상 자산 resolver가 한 번 읽고
    # 자체 fail-open 처리하는 것은 정상 경로다.
    assert captured["registry_reads"] == 1
    assert "failure" not in captured
    assert captured["success"]["charge"] == 1
    assert captured["cut_spec"]["modelId"] == "real-uuid"


def test_worker_missing_manifest_keeps_selected_model_without_substitution(monkeypatch):
    captured = {"registry_reads": 0}
    _patch_worker(monkeypatch, captured)

    def unavailable_registry():
        captured["registry_reads"] += 1
        raise OSError("manifest unavailable")

    monkeypatch.setattr(dpj.cut_generator, "load_virtual_model_registry", unavailable_registry)
    app = fake_worker_app(make_settings(
        gemini_api_key="x",
        r2_bucket="b",
        facemarket_enabled=False,
        detailpage_fallback_model_id="mB",
    ))

    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    # 폴백 판정 1회 + 기존 가상 자산 resolver 1회. 둘 다 예외를 전파하지 않는다.
    assert captured["registry_reads"] == 2
    assert "failure" not in captured
    assert captured["success"]["charge"] == 1
    assert captured["cut_spec"]["modelId"] == "real-uuid"
# --- REAL 소스 identity 첨부 계획 (A4: mirror/back 참조 0장 회귀 방지) ---

def test_real_face_shown_cut_attaches_grid_and_badge():
    # 얼굴 노출 착용컷(styling full 등, wants_face=True) → 그리드 첨부 + 검증 배지
    assert real_identity_plan("styling", wants_face=True) == (True, True)
    assert real_identity_plan("horizon", wants_face=True) == (True, True)


def test_real_mirror_attaches_grid_without_badge():
    # mirror(폰이 얼굴 가림, wants_face=False) — 그리드는 붙여 인물 일관성 유지, 배지는 없음.
    # 이게 A4 핵심: 예전엔 wants=False → 참조 0장 → mirror 만 인물 랜덤이었다.
    assert real_identity_plan("mirror", wants_face=False) == (True, False)


def test_real_back_pose_attaches_grid_without_badge():
    # 뒷모습(styling 이지만 얼굴 프레임 밖, wants_face=False) — 그리드로 체형·정체성 고정, 배지 없음
    assert real_identity_plan("styling", wants_face=False) == (True, False)


def test_real_product_cut_no_grid():
    # product 컷(사람 금지) → 그리드 미첨부, 배지 없음
    assert real_identity_plan("product", wants_face=False) == (False, False)


def test_real_unknown_cut_no_grid():
    # 미상/None cutType → 첨부 안 함(빈 슬롯 경로가 처리)
    assert real_identity_plan(None, wants_face=False) == (False, False)
    assert real_identity_plan("bogus", wants_face=True) == (False, False)


# --- prod 안전망: 착용컷 인물참조 0장 → 결정적 폴백 (REJECTED·REAL 로드실패) ---

def test_fallback_when_worn_cut_has_no_identity():
    # REJECTED(무라이선스 실모델) 또는 REAL grid 로드 실패 → 착용컷 참조 0장 → 폴백 필요
    assert needs_identity_fallback(cut_type="styling", has_model_images=False, face_slot=False) is True
    assert needs_identity_fallback(cut_type="mirror", has_model_images=False, face_slot=False) is True
    assert needs_identity_fallback(cut_type="horizon", has_model_images=False, face_slot=False) is True


def test_no_fallback_when_identity_present():
    # 이미 인물 참조 있음(REAL grid·VIRTUAL mB 성공) → 폴백 불필요
    assert needs_identity_fallback(cut_type="styling", has_model_images=True, face_slot=False) is False


def test_no_fallback_for_legacy_face_slot():
    # LEGACY 단일 얼굴은 face_ref 로 별도 첨부 → 그리드 폴백 대상 아님(인물 이중주입 방지)
    assert needs_identity_fallback(cut_type="styling", has_model_images=False, face_slot=True) is False


def test_no_fallback_for_non_worn_cut():
    # product(사람 금지)·미상 컷 → 폴백 안 함
    assert needs_identity_fallback(cut_type="product", has_model_images=False, face_slot=False) is False
    assert needs_identity_fallback(cut_type=None, has_model_images=False, face_slot=False) is False
