"""톤 에디터 계약 — 마스크 전처리와 조정본이 마네킹 생성을 절대 위험에 빠뜨리지 않는다.

이 기능의 위험은 화질이 아니라 **결합**이다. 마스크는 유료 생성이 끝난 뒤에 돌고, 실패해도
셀러가 이미 받은 컷에는 아무 일도 일어나면 안 된다. 여기 테스트는 그 경계를 문서가 아니라
코드로 못 박는다.
"""

from __future__ import annotations

import inspect
import pathlib
import re

import pytest

from app.services import editor_garment_mask as egm
from app.services import mannequin_tone_render as tone
from app.workers import editor_garment_mask_job as job
from app.workers.dispatcher import _WORKERS

SERVER = pathlib.Path(__file__).resolve().parents[1]


def test_worker_is_registered_and_kind_is_in_the_db_constraint():
    """등록만 하고 마이그레이션을 빼먹으면 CheckViolation 으로 라우트가 500 이 된다
    (2026-08-12 sam_preprocess 에서 실제로 일어난 사고)."""
    assert "editor_garment_mask" in _WORKERS
    migrations = sorted((SERVER.parent / "supabase" / "migrations").glob("*.sql"))
    latest = ""
    for path in migrations:
        text = path.read_text(encoding="utf-8")
        if "jobs_kind_check" in text and "add constraint" in text:
            latest = text
    assert "'editor_garment_mask'" in latest, "job kind 가 jobs_kind_check 에 없다"


def test_every_registered_worker_kind_is_allowed_by_the_constraint():
    migrations = sorted((SERVER.parent / "supabase" / "migrations").glob("*.sql"))
    latest = ""
    for path in migrations:
        text = path.read_text(encoding="utf-8")
        if "jobs_kind_check" in text and "add constraint" in text:
            latest = text
    for kind in _WORKERS:
        assert f"'{kind}'" in latest, f"{kind} 가 jobs_kind_check 에 없다"


def test_mask_job_reserves_no_credits():
    """무과금이 아니라면 이 기능은 존재 자체가 셀러에게 비용이다."""
    src = inspect.getsource(
        __import__("app.workers.mannequin_job", fromlist=["mannequin_job"])
        ._enqueue_editor_garment_mask)
    assert "credits_reserved=0" in src
    assert "editor_garment_mask" in src


def test_mask_enqueue_cannot_fail_the_mannequin_job():
    """큐잉 실패는 삼킨다. 마스크 때문에 유료 생성이 되돌아가면 본말전도다."""
    src = inspect.getsource(
        __import__("app.workers.mannequin_job", fromlist=["mannequin_job"])
        ._enqueue_editor_garment_mask)
    assert "except Exception" in src, "예외를 삼키지 않으면 생성이 함께 죽는다"
    assert "raise" not in src.split("except Exception", 1)[1]


def test_mask_enqueue_runs_after_the_finalize_commit():
    """같은 트랜잭션에서 큐잉하면 실패 한 번이 방금 확정된 컷을 통째로 롤백한다."""
    src = (SERVER / "app" / "workers" / "mannequin_job.py").read_text(encoding="utf-8")
    finalize = src.index("finalize_mannequin_success(")
    commit = src.index("await conn.commit()", finalize)
    enqueue = src.index("await _enqueue_editor_garment_mask(", commit)
    assert commit < enqueue, "enqueue 가 커밋보다 앞서면 안 된다"


def test_mask_job_never_calls_a_generation_provider():
    """생성 크레딧도, Gemini 도, VLM 도 쓰지 않는다."""
    src = (SERVER / "app" / "workers" / "editor_garment_mask_job.py").read_text(encoding="utf-8")
    for forbidden in ("gemini_image", "vision_llm", "analyze_with_fallback", "GeminiImageClient"):
        assert forbidden not in src, f"{forbidden} 를 부르면 무과금 계약이 깨진다"


def test_mask_job_is_flag_gated_and_fails_closed():
    src = (SERVER / "app" / "workers" / "editor_garment_mask_job.py").read_text(encoding="utf-8")
    assert 'getattr(s, "mannequin_tone_editor", "off") != "on"' in src
    assert job.SKIP_DISABLED in src


def test_mask_idempotency_binds_cut_and_algorithm_version():
    """같은 컷·같은 알고리즘은 한 번만. 재생성 컷은 새 신원이라 다시 돈다."""
    src = inspect.getsource(
        __import__("app.workers.mannequin_job", fromlist=["mannequin_job"])
        ._enqueue_editor_garment_mask)
    key = re.search(r"idempotency_key=f\"([^\"]+)\"", src)
    assert key, "멱등키가 없다"
    assert "{cut_id}" in key.group(1) and "{EDITOR_MASK_VERSION}" in key.group(1)


def test_mask_metadata_carries_provenance():
    class R:
        ready = True
        mask_key = "derived/editor-garment-mask/editor-worn-garment-sam2-v1/m/abc.png"
        source_hash = "abc"
        model_version = "facebook/sam2.1-hiera-tiny@grid8"
        algorithm_version = "editor-worn-garment-sam2-v1"
        selector_version = "basediff-rank-v1"
        checksum = "c"
        width = 848
        height = 1264
        area_frac = 0.1
        byte_size = 100
        grid = 16
        m2m = True

    meta = egm.metadata_for(R(), cut_id="A-1", source_asset_id="asset-1",
                            category="top", sub_category="tshirt")
    assert meta["type"] == egm.MASK_KIND == "editorGarmentMask"
    assert meta["producer"] == "sam2-worn-garment"
    for field in ("sourceCutId", "sourceAssetId", "sourceHash", "modelVersion",
                  "algorithmVersion", "selectorVersion", "grid", "m2m"):
        assert meta[field] is not None


def test_editor_mask_and_canonical_cutout_can_never_be_confused():
    """캐노니컬 컷아웃과 에디터 마스크는 다른 이미지에 대한 다른 질문이다."""
    from app.services import canonical_reference as canon
    assert egm.MASK_KIND != canon.CANONICAL_KIND
    assert egm.PRODUCER != canon.PRODUCER
    # 캐노니컬 판별자는 `canonicalType`, 에디터는 `type` — 서로의 행을 절대 집지 않는다.
    assert egm.is_current({"canonicalType": canon.CANONICAL_KIND, "sourceCutId": "A-1"},
                          cut_id="A-1", source_hash=None) is False
    assert canon.is_current({"type": egm.MASK_KIND, "sourceAssetId": "x"},
                            source_asset_id="x", source_hash=None) is False


def test_stale_mask_is_refused_when_the_cut_content_changed():
    """재생성하면 컷 바이트가 달라진다. 옛 마스크를 그대로 쓰면 엉뚱한 픽셀이 보정된다."""
    meta = {"type": egm.MASK_KIND, "sourceCutId": "A-1", "sourceHash": "old",
            "algorithmVersion": "editor-worn-garment-sam2-v1"}
    assert egm.is_current(meta, cut_id="A-1", source_hash="old")
    assert not egm.is_current(meta, cut_id="A-1", source_hash="new")
    assert not egm.is_current(meta, cut_id="B-1", source_hash="old")
    assert not egm.is_current(meta, cut_id="A-1", source_hash="old",
                              algorithm_version="editor-worn-garment-sam2-v2")


@pytest.mark.parametrize("saturation,exposure,expected", [
    (0, 0, (0, 0)), (999, 999, (100, 100)), (-999, -999, (-100, -100)),
    (12.4, -7.6, (12, -8)), ("x", None, (0, 0)),
])
def test_apply_clamps_adjustment_to_the_product_range(saturation, exposure, expected):
    """조정값은 클라이언트의 주장이다. 범위 밖 값은 이 제품이 만들 수 없는 결과다."""
    assert tone.clamp_params(saturation, exposure) == expected


def test_neutral_adjustment_means_reset_not_a_new_render():
    assert tone.is_neutral(0, 0)
    assert not tone.is_neutral(1, 0)


def test_tone_render_metadata_reconstructs_from_the_original():
    """재편집 소스는 항상 원본 컷이다. 조정본 위에 조정본을 쌓으면 열화가 누적된다."""
    meta = tone.metadata_for(cut_id="A-1", source_asset_id="asset-1", source_hash="h",
                             mask_asset_id="mask-1", mask_algorithm_version="v1",
                             saturation=-10, exposure=8)
    assert meta["type"] == "mannequinToneAdjusted"
    assert meta["sourceAssetId"] == "asset-1", "원본 컷 자산을 가리켜야 한다"
    assert meta["rendererVersion"] == tone.RENDERER_VERSION
    assert (meta["saturation"], meta["exposure"]) == (-10, 8)


def test_tone_render_is_bound_to_one_cut():
    meta = {"type": "mannequinToneAdjusted", "sourceCutId": "A-1", "sourceHash": "h"}
    assert tone.is_current(meta, cut_id="A-1")
    assert not tone.is_current(meta, cut_id="A-2"), "다른 컷에 재사용되면 안 된다"
    assert not tone.is_current(meta, cut_id="A-1", source_hash="other")


def test_tone_routes_do_not_redirect_to_the_cdn():
    """캔버스가 픽셀을 읽으려면 CORS 가 우리 것이어야 한다 — 302 면 R2 의 것이 된다."""
    src = (SERVER / "app" / "routes.py").read_text(encoding="utf-8")
    block = src[src.index("async def _tone_bytes"):src.index("async def apply_tone_editor")]
    assert "RedirectResponse" not in block
    assert "Response(content=data" in block
