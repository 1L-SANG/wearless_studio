"""색감 조정은 **파는 옷** 위에서만 일어난다 — API 쪽 보장.

마네킹컷은 주상품과 코디 의류(매칭 의류)를 함께 입는다. 파는 옷은 주상품 하나뿐이므로 톤
에디터가 움직일 수 있는 픽셀도 주상품뿐이다. SAM 서비스가 그 판단을 하지만 별도 배포이고
캐시가 있으므로, 보장을 배포 순서에 맡기지 않는다: API 가 받은 마스크를 **픽셀로 다시 보고**
코디 의류 위면 기록하지 않는다. 이 파일은 그 두 겹과, 실패한 컷이 "준비 중"에 갇히지 않는
것까지 고정한다.
"""

from __future__ import annotations

import asyncio
import io

import numpy as np
import pytest
from PIL import Image

from app.routes import _tone_state_with_job
from app.services import editor_garment_mask as egm
from app.workers import editor_garment_mask_job as job
from sam_service import worn_garment as W


# ── 두 모듈이 같은 밴드를 본다 (값 복제의 유일한 안전장치) ────────────────────

def test_api_and_sam_service_agree_on_the_algorithm_identity():
    """마스크 캐시 신원과 멱등키 버전이 갈리면 같은 컷이 두 규칙 사이를 왕복한다."""
    assert egm.ALGORITHM_VERSION == W.ALGORITHM_VERSION


def test_api_and_sam_service_agree_on_the_matching_band():
    """API 이미지는 SAM 런타임을 임포트하지 않는다 — 그래서 값을 복제하고, 여기서 고정한다."""
    assert egm.MATCHING_CORE == W.MATCHING_CORE
    assert egm.MATCHING_SEPARABLE == W.MATCHING_SEPARABLE
    assert egm.MATCH_ZONE_MAX == W.MATCH_ZONE_MAX


@pytest.mark.parametrize("clothing_type", ["top", "outer", "bottom", "dress", "nonsense", None])
@pytest.mark.parametrize("side", ["top", "bottom", None, "", "TOP"])
def test_band_selection_matches_the_service_for_every_pair(clothing_type, side):
    assert egm.matching_core_band(clothing_type, side) == W.matching_core_band(clothing_type, side)


# ── 픽셀 확인 ────────────────────────────────────────────────────────────────

def _mask_png(shape, box):
    m = np.zeros(shape, np.uint8)
    y0, y1, x0, x1 = box
    m[y0:y1, x0:x1] = 255
    buf = io.BytesIO()
    Image.fromarray(m, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def test_band_mass_fraction_measures_where_the_mask_actually_is():
    band = egm.MATCHING_CORE["bottom"]                     # (0.60, 1.00)
    pants = _mask_png((100, 60), (62, 96, 20, 40))         # 전부 밴드 안
    top = _mask_png((100, 60), (12, 56, 15, 45))           # 전부 밴드 밖
    outfit = _mask_png((100, 60), (12, 96, 15, 45))        # 두 벌
    assert egm.band_mass_fraction(pants, band) == 1.0
    assert egm.band_mass_fraction(top, band) == 0.0
    assert 0.2 < egm.band_mass_fraction(outfit, band) < 0.8


@pytest.mark.parametrize("png,band", [
    (b"not-a-png", egm.MATCHING_CORE["bottom"]),           # 깨진 바이트
    (b"", egm.MATCHING_CORE["bottom"]),                    # 빈 응답
    (_mask_png((100, 60), (0, 0, 0, 0)), egm.MATCHING_CORE["bottom"]),  # 빈 마스크
    (_mask_png((100, 60), (62, 96, 20, 40)), ()),          # 가를 수 없는 조합
])
def test_band_mass_fraction_is_fail_open(png, band):
    """판정 불가는 None 이다 — 못 읽은 걸 '코디 옷 위'로 단정하면 정상 컷이 막힌다."""
    assert egm.band_mass_fraction(png, band) is None


# ── 보장 스탬프 ──────────────────────────────────────────────────────────────

class _Result:
    ready = True
    mask_key = "derived/editor-garment-mask/x/m/abc.png"
    source_hash = "abc"
    model_version = "facebook/sam2.1-hiera-tiny@grid8"
    algorithm_version = egm.ALGORITHM_VERSION
    selector_version = "basediff-rank-v2"
    checksum = "c"
    width = 848
    height = 1264
    area_frac = 0.1
    byte_size = 100
    grid = 16
    m2m = True
    cached = False
    matching_side = None
    match_share = 0.0
    selected_rank = 0
    vetoed_attempts = 0
    product_match = 0.0


def test_recorded_metadata_states_which_check_ran():
    meta = egm.metadata_for(_Result(), cut_id="A-1", source_asset_id="asset-1",
                            category="top", sub_category="tshirt",
                            matching_side="bottom", match_share=0.04)
    assert meta["matchingSide"] == "bottom"
    assert meta["matchShare"] == 0.04
    assert meta["matchGuardVersion"] == egm.MATCH_GUARD_VERSION


@pytest.mark.parametrize("meta,side,expected", [
    ({}, None, False),                                             # 코디 없는 컷은 대상 아님
    ({"matchGuardVersion": egm.MATCH_GUARD_VERSION}, None, False),
    ({}, "bottom", True),                                          # 보장 이전 마스크
    ({"matchGuardVersion": "old"}, "bottom", True),
    (None, "bottom", True),
    ({"matchGuardVersion": egm.MATCH_GUARD_VERSION}, "bottom", False),
])
def test_masks_made_before_the_guarantee_are_remade(meta, side, expected):
    assert egm.needs_match_guard(meta, matching_side=side) is expected


def test_reusing_an_existing_mask_row_restamps_its_provenance(monkeypatch):
    """같은 픽셀을 재사용해도 스탬프는 이번 실행 기준으로 다시 찍는다.

    안 찍으면 그 행은 영구 stale 이 되고, 상태 조회마다 다시 큐에 넣는 무한 루프가 된다.
    """
    stamped = {}

    async def find_by_key(_conn, *, project_id, r2_key):
        return {"id": "asset-1", "r2_key": r2_key, "metadata": {"stale": True}}

    async def set_metadata(_conn, *, asset_id, metadata):
        stamped[asset_id] = metadata

    monkeypatch.setattr(egm, "find_by_key", find_by_key)
    monkeypatch.setattr(egm, "set_metadata", set_metadata)
    row = asyncio.run(egm.record(
        object(), user_id="u1", project_id="p1", cut_id="A-1", source_asset_id="cut-1",
        result=_Result(), category="top", matching_side="bottom", match_share=0.02))

    assert stamped["asset-1"]["matchGuardVersion"] == egm.MATCH_GUARD_VERSION
    assert row["metadata"]["matchShare"] == 0.02


# ── 잡: 코디 의류 위 마스크는 기록하지 않는다 ────────────────────────────────

class _Conn:
    async def commit(self):
        pass

    async def rollback(self):
        pass


class _Pool:
    def connection(self):
        class _Ctx:
            async def __aenter__(self_inner):
                return _Conn()

            async def __aexit__(self_inner, *_exc):
                return False
        return _Ctx()


class _R2:
    def __init__(self, png):
        self.png = png
        self.fetched = []

    def get_bytes(self, key):
        self.fetched.append(key)
        return self.png


class _App:
    def __init__(self, r2):
        self.state = type("S", (), {"pool": _Pool(), "r2": r2})()


def _run_job(monkeypatch, *, mask_png, matching_side, clothing_type="top", result=_Result,
             product_extra=None, product_cutout_key=None):
    """마스크 잡 한 번. DB·SAM·R2 는 대역, 판정 로직은 진짜."""
    finished = {}
    recorded = {}

    class _Settings:
        mannequin_tone_editor = "on"
        sam_service_url = "http://sam2:8080"
        sam_internal_token = "t"
        base_mannequin_men_asset_id = "base-m"
        base_mannequin_women_asset_id = "base-w"

    async def segment(_s, **kwargs):
        recorded["request"] = kwargs
        return type("R", (result,), {})()

    async def finalize(_conn, *, job_id, lease_token, status, result):
        finished.update({"status": status, "result": result})

    async def record(_conn, **kwargs):
        recorded["record"] = kwargs
        return {"id": "mask-asset"}

    monkeypatch.setattr(job, "load_settings", lambda: _Settings())
    monkeypatch.setattr(job.sam_client, "configured", lambda _s: True)
    monkeypatch.setattr(job.sam_client, "segment_worn_garment", segment)
    monkeypatch.setattr(job.repo, "finalize_uncharged_job", finalize)
    monkeypatch.setattr(job.repo, "get_mannequin_cut_asset",
                        lambda *_a, **_k: _async({"id": "cut-1", "r2_key": "cuts/a.jpg"}))
    monkeypatch.setattr(job.repo, "get_product",
                        lambda *_a, **_k: _async({"clothing_type": clothing_type,
                                                  **(product_extra or {})}))
    monkeypatch.setattr(job.canonical_reference, "current_key",
                        lambda *_a, **_k: _async(product_cutout_key))
    monkeypatch.setattr(job.repo, "get_analysis",
                        lambda *_a, **_k: _async({"matchSelections": [
                            {"clothingId": "m1", "role": "main"}]}))
    monkeypatch.setattr(job.repo, "get_matching_item_metadata",
                        lambda *_a, **_k: _async({"clothing_type": matching_side}
                                                 if matching_side else None))
    monkeypatch.setattr(egm.repo, "get_matching_item_metadata",
                        lambda *_a, **_k: _async({"clothing_type": matching_side}
                                                 if matching_side else None))
    monkeypatch.setattr(egm.repo, "get_analysis",
                        lambda *_a, **_k: _async({"matchSelections": [
                            {"clothingId": "m1", "role": "main"}]}))
    monkeypatch.setattr(job.repo, "get_asset_for_user",
                        lambda *_a, **_k: _async({"id": "base-w", "r2_key": "base/w.png"}))
    monkeypatch.setattr(job, "editor_garment_mask", _RecordingMask(record))

    r2 = _R2(mask_png)
    asyncio.run(job.run_editor_garment_mask_job(
        _App(r2), {"id": "j1", "project_id": "p1", "user_id": "u1",
                   "lease_token": "lt", "payload": {"cutId": "A-1"}}))
    return finished, recorded, r2


def _async(value):
    async def _coro():
        return value
    return _coro()


class _RecordingMask:
    """`editor_garment_mask` 대역 — 밴드 계산·픽셀 판정은 진짜 모듈 함수를 그대로 쓴다."""

    MATCH_ZONE_MAX = egm.MATCH_ZONE_MAX
    matching_core_band = staticmethod(egm.matching_core_band)
    band_mass_fraction = staticmethod(egm.band_mass_fraction)
    matching_side_for_project = staticmethod(egm.matching_side_for_project)

    def __init__(self, record):
        self.record = record


def test_job_refuses_a_mask_that_sits_on_the_coordinating_garment(monkeypatch):
    finished, recorded, _r2 = _run_job(
        monkeypatch, mask_png=_mask_png((100, 60), (62, 96, 20, 40)), matching_side="bottom")
    assert finished["status"] == "done"
    assert finished["result"]["state"] == "failed"
    assert finished["result"]["code"] == job.FAIL_ON_MATCHING
    assert finished["result"]["matchShare"] == 1.0
    assert "record" not in recorded, "코디 의류 위 마스크는 기록되면 안 된다"


def test_job_records_a_product_mask_with_its_measured_share(monkeypatch):
    finished, recorded, _r2 = _run_job(
        monkeypatch, mask_png=_mask_png((100, 60), (12, 56, 15, 45)), matching_side="bottom")
    assert finished["result"]["state"] == "ready"
    assert finished["result"]["matchShare"] == 0.0
    assert recorded["record"]["matching_side"] == "bottom"
    assert recorded["request"]["matching_side"] == "bottom", "서비스에도 알려야 채점이 바뀐다"


def test_a_cut_without_a_coordinating_garment_costs_nothing_extra(monkeypatch):
    """코디가 없으면 밴드도 없다 — 마스크를 내려받아 재검사하지도 않는다."""
    finished, recorded, r2 = _run_job(
        monkeypatch, mask_png=_mask_png((100, 60), (62, 96, 20, 40)), matching_side=None)
    assert finished["result"]["state"] == "ready"
    assert r2.fetched == [], "확인할 밴드가 없으면 R2 를 읽지 않는다"
    assert recorded["request"]["matching_side"] is None


# ── 실패한 컷은 "준비 중"에 갇히지 않는다 ────────────────────────────────────

@pytest.mark.parametrize("job_status,expected", [
    ("pending", "processing"),      # 아직 큐에 있다 — 기다리는 게 맞다
    ("running", "processing"),
    ("done", "failed"),             # 마스크 없이 끝났다(코디 위·의류 못 찾음·SAM 미설정)
    ("error", "failed"),
    ("cancelled", "failed"),
])
def test_a_terminated_mask_job_stops_the_waiting_message(job_status, expected):
    state = {"cutId": "A-1", "status": "processing"}
    assert _tone_state_with_job(state, {"status": job_status})["status"] == expected


def test_a_ready_cut_is_never_downgraded_by_an_old_job_row():
    state = {"cutId": "A-1", "status": "ready"}
    assert _tone_state_with_job(state, {"status": "error"}) == state
    assert _tone_state_with_job(state, None) == state


def test_an_unreadable_mask_is_retried_not_stamped_as_verified(monkeypatch):
    """확인 못 한 마스크에 보장 도장을 찍으면 그 컷은 영구히 검증된 것으로 남는다."""
    finished, recorded, _r2 = _run_job(
        monkeypatch, mask_png=b"corrupted", matching_side="bottom")
    # done+retryable 이다 — error 는 재시도 없는 종착이라(2026-08-18 실측) 일시 장애가
    # 그 컷의 톤 에디터를 영구히 닫는다. 재시도는 톤 상태 라우트가 세대 키로 몰고 간다.
    assert finished["status"] == "done"
    assert finished["result"]["state"] == "unverified"
    assert finished["result"]["retryable"] is True
    assert finished["result"]["code"] == job.FAIL_UNVERIFIED
    assert "record" not in recorded


def test_the_route_and_the_job_read_the_matching_side_from_one_function():
    """두 판정이 갈리면 '만들면 stale, 조회하면 없음' 무한 재큐가 된다."""
    import inspect
    from app import routes
    assert "editor_garment_mask.current_mask_for_cut" in inspect.getsource(routes._tone_state)
    assert "editor_garment_mask.matching_side_for_project" in inspect.getsource(
        job.run_editor_garment_mask_job)


def test_no_route_serves_a_mask_without_passing_the_guard():
    """라우트가 `find_for_cut` 을 직접 부르면 그 경로로 검증 안 된 마스크가 샌다 —
    배포 순간 에디터를 열어 둔 셀러가 정확히 그 경로(마스크 픽셀·적용)를 탄다."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "routes.py").read_text(
        encoding="utf-8")
    assert "editor_garment_mask.find_for_cut" not in src
    # 상태 조회·마스크 픽셀 전송·적용, 세 소비자 모두.
    assert src.count("editor_garment_mask.current_mask_for_cut") >= 3


def test_the_band_covers_every_pair_the_product_can_actually_create():
    """매칭 조합의 정본은 `services/matching.complementary_type` 이다 — 그게 만들 수 있는 모든
    조합에 밴드가 있어야 보장에 구멍이 없다. 새 카테고리가 생기면 이 테스트가 먼저 깨진다.

    원피스는 매칭이 성립하지 않으므로(_NO_MATCH) 밴드가 없는 게 맞다 — 코디 옷이 없는 컷이다.
    """
    from app.agents.product_analyst import CLOTHING_TYPES
    from app.services import matching

    for clothing_type in CLOTHING_TYPES:
        side = matching.complementary_type(clothing_type)
        band = egm.matching_core_band(clothing_type, side)
        if side is None:
            assert band == (), f"{clothing_type} 는 코디 의류가 붙지 않는다"
        else:
            assert band, f"{clothing_type}+{side} 조합에 밴드가 없으면 보장 밖이다"
            assert band == W.matching_core_band(clothing_type, side)


def test_reset_is_always_allowed_even_without_a_usable_mask():
    """보장 이전 마스크로 붙인 조정을 셀러가 되돌릴 수 있어야 한다 — 초기화는 마스크와 무관하다."""
    import inspect
    from app import routes
    src = inspect.getsource(routes.apply_tone_editor)
    neutral_at = src.index("is_neutral(")
    guard_at = src.index('_bad_request("mask_not_ready"')
    assert neutral_at < guard_at, "초기화 분기가 마스크 요구보다 앞에 있어야 한다"


def test_the_ledger_says_how_hard_the_guard_had_to_work(monkeypatch):
    """몇 등 후보를 내줬고 그 전에 몇 장을 거부했는지 잡 기록에 남긴다.

    2026-08-18 사고 때 실서버 기록에는 거부 사실만 있고 "무엇을 시도했는지"가 없어서, 1등이
    원래 바지였는지 정제가 번진 건지 코드를 읽고 추측해야 했다. 다음엔 기록이 답하게 한다.
    """
    class _FellBack(_Result):
        selected_rank = 2
        vetoed_attempts = 3
        product_match = 0.81

    finished, _recorded, _r2 = _run_job(
        monkeypatch, mask_png=_mask_png((100, 60), (12, 56, 15, 45)), matching_side="bottom",
        result=_FellBack)

    assert finished["result"]["state"] == "ready"
    assert finished["result"]["selectedRank"] == 2
    assert finished["result"]["vetoedAttempts"] == 3
    assert finished["result"]["productMatch"] == 0.81, "올린 옷과 얼마나 닮았는지도 남는다"


def test_the_cuts_stranded_by_the_2026_08_18_veto_are_not_stranded_forever():
    """1등 하나 거부하고 포기하던 시절의 알고리즘 신원은 재사용하지 않는다.

    마스크 잡의 멱등키와 SAM 캐시 키가 둘 다 이 값을 물고 있다. 값을 그대로 두면 그때
    `no_garment_candidate` 로 끝난 컷들은 이 수정이 배포돼도 그 done 잡에 계속 합류하고,
    셀러 화면은 영원히 "이 컷은 색감 조정을 지원하지 않아요"로 남는다.
    """
    assert egm.ALGORITHM_VERSION != "editor-worn-garment-sam2-v2"


# ── 주상품 레퍼런스: 올린 사진으로 파는 옷을 짚는다 (2026-08-18) ──────────────
#
# 전체 이야기와 근거는 tests/test_product_reference_mask.py 머리말에 있다. 여기서는 마스크
# 잡이 그 레퍼런스를 실제로 SAM 에 넘기는지만 본다.

_BASE_PHOTOS = {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "img-front"}]}]}


def test_the_mask_job_hands_sam_the_uploaded_product_cutout(monkeypatch):
    """올린 앞면 사진의 컷아웃이 준비돼 있으면 채점 근거로 함께 보낸다."""
    _finished, recorded, _r2 = _run_job(
        monkeypatch, mask_png=_mask_png((100, 60), (12, 56, 15, 45)), matching_side="bottom",
        product_extra=_BASE_PHOTOS, product_cutout_key="derived/canonical/front.png")

    assert recorded["request"]["product_key"] == "derived/canonical/front.png"


def test_a_cut_whose_product_cutout_is_not_ready_still_gets_a_mask(monkeypatch):
    """컷아웃은 보조 근거다 — 없으면 없는 대로 v3 채점으로 간다. 기다리지 않는다."""
    finished, recorded, _r2 = _run_job(
        monkeypatch, mask_png=_mask_png((100, 60), (12, 56, 15, 45)), matching_side="bottom",
        product_extra=_BASE_PHOTOS, product_cutout_key=None)

    assert recorded["request"]["product_key"] is None
    assert finished["result"]["state"] == "ready"
