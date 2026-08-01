"""hybrid composite 워커 통합 — 실제 `run_mannequin_job` 경로에 실제 CV 파이프라인.

fake 는 IO 경계(repo/R2/Gemini/vision)뿐이다. composite 스테이지 자체는 진짜 코드가 돈다:
fake Gemini 가 synthetic carrier 를 돌려주면, 진짜 소스검증→추출→panel→warp→QC 를 통과한
합성본이 저장까지 흘러가는지를 본다.

핵심 계약(스파이가 잠근다):
  · `hybrid_composite_completed` 이후 저장·finalize 까지 image-generation 호출 증가 0
  · stage event 순서 단조 증가
  · 합성 실패 = typed needs_review, old/fresh fallback 0회
  · deterministic 판정은 LLM QC 로 뒤집을 수 없음 (양방향)
"""

import asyncio
import base64
import contextlib
import hashlib
import types

import cv2
import numpy as np
import pytest

from app import repo
from app.agents import hybrid_landmarks
from app.workers import mannequin_job as mj
from conftest import make_settings
from hybrid_stripe_fixtures import render_carrier, render_negative, render_signal

PROFILE = {"category": "top", "gender": "women", "source": "seller",
           "axes": {"fit": "slim"}, "version": 1}


def _png(bgr) -> bytes:
    ok, buf = cv2.imencode(".png", bgr)
    assert ok
    return buf.tobytes()


def _stripe_source_png() -> bytes:
    """source Front/Detail 겸용 — S1 을 1.6배로 키워 1536² 로 타일(주기 64px, 반복 21.6회)."""
    base = render_signal("S1_blue_brown_fine", "illum")
    big = cv2.resize(base, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_LINEAR)
    tiled = np.tile(big, (2, 2, 1))[:1536, :1536]
    return _png(tiled)


# source fixture 는 1536² 정방형 — 물리 torso aspect 가 carrier fixture(G1, 848×1264,
# 물리 aspect ≈1.89)와 22% 안에서 맞아야 construction gate 를 통과한다(물리 비 계약).
SOURCE_GEOM_RAW = {
    "garment_visible": True, "confidence": 0.9,
    "shoulder_l": [0.25, 0.04], "shoulder_r": [0.75, 0.04],
    "hem_l": [0.25, 0.96], "hem_r": [0.75, 0.96],
    "has_collar": True, "has_placket": True, "has_cuffs": True,
    "visible_button_count": 7,
}


def _carrier_geom_raw(cx) -> dict:
    lm = cx["landmarks"]
    return {
        "garment_visible": True, "confidence": 0.92,
        "shoulder_l": lm["shoulder_l"], "shoulder_r": lm["shoulder_r"],
        "hem_l": lm["hem_l"], "hem_r": lm["hem_r"],
        "sleeve_l_end": lm["sleeve_l_end"], "sleeve_r_end": lm["sleeve_r_end"],
        "has_collar": True, "has_placket": True, "has_cuffs": True,
        "visible_button_count": 7,
    }


class _Conn:
    async def commit(self):
        return None


class _Pool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()

        return _cm()


def _run_job(monkeypatch, *, detail_png=None, include_detail=True, product_name="스트라이프 셔츠",
             settings_kw=None, p2_verdict=None, source_png=None):
    """워커 전체 실행. → (oplog, calls, r2_saved, emits)"""
    oplog: list[tuple] = []          # ("gen",) | ("evt", status) — 순서가 곧 증거
    emits: list[tuple] = []
    calls = {"success": [], "failure": []}
    r2_saved: dict = {}

    source_png = source_png or _stripe_source_png()
    detail_png = detail_png if detail_png is not None else source_png
    cx = render_carrier("G1_regular", 0)
    carrier_png = _png(cx["image"])

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size,
                                         temperature=None, aspect_ratio=None):
            oplog.append(("gen",))
            return types.SimpleNamespace(image=carrier_png, mime="image/png",
                                         latency_ms=1, usage=None)

    class _R2:
        def get_bytes(self, key):
            return {"bw.png": _png(np.full((64, 64, 3), 240, np.uint8)),
                    "front.png": source_png, "back.png": source_png,
                    "detail.png": detail_png}[key]

        def put_bytes(self, key, data, mime, cache=None):
            r2_saved["key"], r2_saved["data"], r2_saved["mime"] = key, data, mime

    async def fake_geometry(settings, image):
        # source(front)와 carrier 를 바이트로 구분 — vision 은 좌표만 준다는 계약의 fake
        return SOURCE_GEOM_RAW if image.data == source_png else _carrier_geom_raw(cx)

    async def get_product(conn, project_id):
        images = [{"id": "front", "slot": "Front"}, {"id": "back", "slot": "Back"}]
        if include_detail:
            images.append({"id": "detail", "slot": "Detail"})
        return {"name": product_name, "clothing_type": "top",
                "colors": [{"isBase": True, "images": images}]}

    async def get_analysis(conn, project_id):
        return {"targetGenders": ["women"], "fit": "regular"}

    async def get_asset_for_user(conn, user_id, asset_id):
        return {
            "bw": {"id": "bw", "mime_type": "image/png", "r2_key": "bw.png"},
            "front": {"id": "front", "mime_type": "image/png", "r2_key": "front.png"},
            "back": {"id": "back", "mime_type": "image/png", "r2_key": "back.png"},
            "detail": {"id": "detail", "mime_type": "image/png", "r2_key": "detail.png"},
        }.get(asset_id)

    async def get_matching_item_asset(conn, item_id):
        return None

    async def finalize_success(conn, **kwargs):
        calls["success"].append(kwargs)
        return {"cuts": kwargs["candidates"], "available": 7}

    async def finalize_failure(conn, **kwargs):
        calls["failure"].append(kwargs)
        return True

    async def fake_emit(pool, job_id, event_type, payload):
        oplog.append(("evt", payload.get("status") or payload.get("phase") or event_type))
        emits.append((event_type, dict(payload)))

    for name, fn in (("get_product", get_product), ("get_analysis", get_analysis),
                     ("get_asset_for_user", get_asset_for_user),
                     ("get_matching_item_asset", get_matching_item_asset),
                     ("finalize_mannequin_success", finalize_success),
                     ("finalize_mannequin_failure", finalize_failure)):
        monkeypatch.setattr(repo, name, fn)
    monkeypatch.setattr(mj, "_emit", fake_emit)
    monkeypatch.setattr(mj.hybrid_landmarks, "extract_geometry", fake_geometry)
    if p2_verdict is not None:
        seq = list(p2_verdict) if isinstance(p2_verdict, list) else [p2_verdict]
        state = {"n": 0}

        async def fake_p2(settings, prods, gen_img, scored=True, fit_profile=None):
            v = seq[min(state["n"], len(seq) - 1)]
            state["n"] += 1
            return dict(v)
        monkeypatch.setattr(mj.image_qc, "verdict", fake_p2)

    kw = {"base_mannequin_women_asset_id": "bw", "r2_bucket": "bucket",
          "mannequin_hybrid_composite": "on", "mannequin_max_attempts": 3,
          **(settings_kw or {})}
    settings = make_settings(**kw)
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=_R2(), gemini=_Gemini()))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:t",
           "credits_reserved": 2, "payload": {}}
    asyncio.run(mj.run_mannequin_job(app, job))
    return oplog, calls, r2_saved, emits


def _statuses(emits):
    return [p.get("status") for e, p in emits if e == "step"]


def test_composite_applies_end_to_end_and_freezes_generation_after_completion(monkeypatch):
    oplog, calls, r2_saved, emits = _run_job(monkeypatch)
    assert calls["failure"] == [] and len(calls["success"]) == 1

    st = _statuses(emits)
    order = ["hybrid_composite_started", "hybrid_source_validated", "hybrid_stripe_model",
             "hybrid_panel_map", "hybrid_warp_composite", "hybrid_deterministic_qc",
             "hybrid_composite_completed"]
    idxs = [st.index(s) for s in order]
    assert idxs == sorted(idxs), f"stage 순서 비단조: {list(zip(order, idxs))}"

    # 완료 이후 어떤 image-generation 호출도 없다 — 저장·finalize 까지.
    completed_at = next(i for i, op in enumerate(oplog)
                        if op[0] == "evt" and op[1] == "hybrid_composite_completed")
    gens_after = [op for op in oplog[completed_at:] if op[0] == "gen"]
    assert gens_after == [], "post-composite generation 호출 발생"
    assert sum(1 for op in oplog if op[0] == "gen") == 1, "생성은 geometry 1회여야 한다"

    cut = calls["success"][0]["candidates"][0]
    hc = cut["qc_scores"]["hybridComposite"]
    assert hc["applied"] is True and hc["deterministicPassed"] is True
    assert hc["sourceAssets"]["detail"]["assetId"] == "detail"
    assert hc["sourceCoverage"] >= 0.90
    md = cut["generation_metadata"]
    assert md["generationPath"] == "hybrid_stripe_composite"
    assert md["hybridComposite"]["outputSha256"] == hc["outputSha256"]
    # 저장본 = 합성본 (carrier 가 아니라)
    assert hashlib.sha256(r2_saved["data"]).hexdigest() == hc["outputSha256"]
    assert cut["qc_scores"]["outcome"] == "auto_pass"

    # Detail 이 패턴 정본으로 쓰였다 — P0 slot 권위 계약의 composite 종점
    sm = next(p for e, p in emits if p.get("status") == "hybrid_stripe_model")
    assert sm["source_asset_id"] == "detail"


def test_unsupported_pattern_becomes_typed_needs_review_without_fallback(monkeypatch):
    check_png = _png(np.tile(render_negative("N2_gingham_check"), (2, 2, 1))[:1536, :1536])
    oplog, calls, r2_saved, emits = _run_job(monkeypatch, detail_png=check_png)
    assert calls["failure"] == [] and len(calls["success"]) == 1

    completed = next(p for e, p in emits if p.get("status") == "hybrid_composite_completed")
    assert completed["outcome"] == "unsupported_pattern"
    cut = calls["success"][0]["candidates"][0]
    hc = cut["qc_scores"]["hybridComposite"]
    assert hc["applied"] is False and hc["needsReview"] is True
    assert hc["failureReason"] == "unsupported_pattern"
    assert cut["qc_scores"]["outcome"] == "needs_review", "auto_pass 로 미화되면 안 된다"
    assert cut["generation_metadata"]["generationPath"] == "fresh", "합성 안 됐는데 합성 표기 금지"
    # fallback 재생성 0회 — 생성은 geometry 1회뿐
    assert sum(1 for op in oplog if op[0] == "gen") == 1
    # 저장본 = carrier 그대로 (실패를 숨기는 재칠 없음)
    carrier_png = _png(render_carrier("G1_regular", 0)["image"])
    assert hashlib.sha256(r2_saved["data"]).hexdigest() == hashlib.sha256(
        carrier_png).hexdigest()


def test_missing_detail_slot_is_reference_insufficient(monkeypatch):
    _oplog, calls, _r2, emits = _run_job(monkeypatch, include_detail=False)
    completed = next(p for e, p in emits if p.get("status") == "hybrid_composite_completed")
    assert completed["outcome"] == "reference_insufficient"
    cut = calls["success"][0]["candidates"][0]
    assert cut["qc_scores"]["outcome"] == "needs_review"


def test_plain_product_skips_composite_entirely(monkeypatch):
    _oplog, calls, _r2, emits = _run_job(monkeypatch, product_name="무지 티")
    assert not [s for s in _statuses(emits) if s and s.startswith("hybrid_")]
    cut = calls["success"][0]["candidates"][0]
    assert cut.get("qc_scores") in (None, {}) or "hybridComposite" not in (
        cut.get("qc_scores") or {})
    assert cut["generation_metadata"]["generationPath"] == "fresh"


def test_flag_off_runs_legacy_path_untouched(monkeypatch):
    _oplog, calls, _r2, emits = _run_job(
        monkeypatch, settings_kw={"mannequin_hybrid_composite": "off"})
    assert not [s for s in _statuses(emits) if s and s.startswith("hybrid_")]
    assert calls["success"], "off 면 기존 경로 그대로 성공해야 한다"


def test_deterministic_pass_suppresses_llm_retry_and_records_it(monkeypatch):
    """LLM 이 regenerate 라 해도 deterministic 통과 컷은 재생성하지 않는다 — 보조 신호 계약.

    사전(identity) 게이트는 **합성 전** geometry 단계의 정당한 re-roll 이므로 여기 대상이
    아니다 — 그래서 1차 판정(사전 게이트)은 pass, 합성 후 rescore 만 regenerate 를 주는
    시퀀스로 구성한다.
    """
    good_p2 = {"verdict": "pass", "product_fidelity": 95, "physical_naturalness": 95,
               "image_quality": 95, "critical_errors": [], "mismatches": [],
               "correctionPrompt": ""}
    bad_p2 = {"verdict": "retry", "product_fidelity": 20, "physical_naturalness": 20,
              "image_quality": 20, "critical_errors": [], "mismatches": [],
              "correctionPrompt": ""}
    oplog, calls, _r2, emits = _run_job(
        monkeypatch, settings_kw={"image_qc": "enforce", "qc_score_auto_pass": 80,
                                  "qc_score_review": 65},
        p2_verdict=[good_p2, bad_p2])
    assert sum(1 for op in oplog if op[0] == "gen") == 1, "낮은 LLM 점수가 재생성을 태움"
    assert "hybrid_llm_retry_suppressed" in _statuses(emits)
    cut = calls["success"][0]["candidates"][0]
    assert cut["qc_scores"]["outcome"] == "needs_review", (
        "deterministic 통과 + LLM regenerate → 자동통과 미화도, 재생성도 아닌 needs_review")


def test_composite_failure_cannot_be_overridden_by_llm_auto_pass(monkeypatch):
    """반대 방향 — LLM 만점이라도 composite typed 실패면 auto_pass 로 나갈 수 없다."""
    check_png = _png(np.tile(render_negative("N2_gingham_check"), (2, 2, 1))[:1536, :1536])
    good_p2 = {"verdict": "pass", "product_fidelity": 95, "physical_naturalness": 95,
               "image_quality": 95, "critical_errors": [], "mismatches": [],
               "correctionPrompt": ""}
    _oplog, calls, _r2, _emits = _run_job(
        monkeypatch, detail_png=check_png,
        settings_kw={"image_qc": "enforce", "qc_score_auto_pass": 80,
                     "qc_score_review": 65},
        p2_verdict=good_p2)
    cut = calls["success"][0]["candidates"][0]
    assert cut["qc_scores"]["outcome"] == "needs_review"
    assert cut["qc_scores"]["hybridComposite"]["failureReason"] == "unsupported_pattern"


def test_events_carry_no_image_bytes_base64_or_urls(monkeypatch):
    source_png = _stripe_source_png()
    _oplog, _calls, _r2, emits = _run_job(monkeypatch, source_png=source_png)
    blob = str([p for _e, p in emits])
    assert "http://" not in blob and "https://" not in blob
    assert base64.b64encode(source_png[:60]).decode() not in blob
    assert ".png" not in blob and "r2_key" not in blob


def test_worker_carries_detail_slot_all_the_way_into_the_composite(monkeypatch):
    """P0 e2e provenance 이관 — 업로드 슬롯 → prod_refs → composite 소스 선택까지.

    Detail 이 있으면 composite 의 패턴 정본은 반드시 Detail asset 이어야 하고,
    그 사실이 provenance(qc_scores·metadata·event)에 일치되게 남아야 한다.
    """
    _oplog, calls, _r2, emits = _run_job(monkeypatch)
    cut = calls["success"][0]["candidates"][0]
    hc = cut["qc_scores"]["hybridComposite"]
    detail_sha = hashlib.sha256(_stripe_source_png()).hexdigest()
    assert hc["sourceAssets"]["detail"] == {
        "assetId": "detail", "sha256": detail_sha,
        "roi": hc["sourceAssets"]["detail"]["roi"]}
    assert hc["stripeModel"]["source_asset_id"] == "detail"
    ev = next(p for _e, p in emits if p.get("status") == "hybrid_stripe_model")
    assert ev["source_asset_id"] == "detail"
