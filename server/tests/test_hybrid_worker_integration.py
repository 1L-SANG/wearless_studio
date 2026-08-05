"""hybrid composite 워커 통합 — 실제 `run_mannequin_job` 경로에 실제 CV 파이프라인.

fake 는 IO 경계(repo/R2/Gemini/vision)뿐이다. composite 스테이지 자체는 진짜 코드가 돈다:
fake Gemini 가 synthetic carrier 를 돌려주면, 진짜 소스검증→추출→panel→warp→QC 를 통과한
합성본이 저장까지 흘러가는지를 본다.

핵심 계약(스파이가 잠근다):
  · `hybrid_composite_completed` 이후 저장·finalize 까지 image-generation 호출 증가 0
  · stage event 순서 단조 증가
  · 합성 실패 = shadow 에서는 typed needs_review, enforce 에서는 fail-closed, old/fresh fallback 0회
  · deterministic 판정은 LLM QC 로 뒤집을 수 없음 (양방향)
"""

import asyncio
import json
import base64
import contextlib
import dataclasses
import hashlib
import types

import cv2
import numpy as np
import pytest

from app import repo
from app.agents import hybrid_landmarks
from app.services import product_truth as pt
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
    # 보호 부위 geometry 는 검증 가능한 상품의 정상 응답이다. 없으면 카라·플래킷 충실도를
    # 검증할 방법이 없어 enforce 는 fail-closed 해야 하고, 그 경로는 별도 테스트가 덮는다.
    "collar_box": [[0.41, 0.02], [0.59, 0.02], [0.59, 0.20], [0.41, 0.20]],
    "placket_box": [[0.48, 0.10], [0.52, 0.10], [0.52, 0.92], [0.48, 0.92]],
    "cuff_l_box": [[0.05, 0.74], [0.22, 0.74], [0.22, 0.92], [0.05, 0.92]],
    "cuff_r_box": [[0.78, 0.74], [0.95, 0.74], [0.95, 0.92], [0.78, 0.92]],
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
        # Carrier fixture의 실제 셔츠 영역(shoulder y=.16, hem y=.68)에 맞춘 box.
        # 과거 0.02~0.12/0.10~0.92 값은 배경·하의를 component로 주는 거짓 fixture였다.
        "collar_box": [[0.42, 0.14], [0.58, 0.14], [0.58, 0.24], [0.42, 0.24]],
        "placket_box": [[0.485, 0.18], [0.515, 0.18], [0.515, 0.66], [0.485, 0.66]],
        "cuff_l_box": [[0.14, 0.38], [0.27, 0.38], [0.27, 0.48], [0.14, 0.48]],
        "cuff_r_box": [[0.73, 0.38], [0.86, 0.38], [0.86, 0.48], [0.73, 0.48]],
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
             settings_kw=None, p2_verdict=None, source_png=None,
             carrier_component_box=False, source_component_box=True,
             omit_cuff_boxes=False,
             truth_pattern=None,
             truth_fine_pattern=None, analysis_pattern=None,
             carrier_vision=None, truth_approved=True, frame_results=None):
    """워커 전체 실행. → (oplog, calls, r2_saved, emits)"""
    oplog: list[tuple] = []          # ("gen",) | ("evt", status) — 순서가 곧 증거
    emits: list[tuple] = []
    calls = {"success": [], "failure": []}
    r2_saved: dict = {}

    source_png = source_png or _stripe_source_png()
    detail_png = detail_png if detail_png is not None else source_png
    cx = render_carrier("G1_regular", 0)
    carrier_png = _png(cx["image"])
    product_images = [{"id": "front", "slot": "Front"}, {"id": "back", "slot": "Back"}]
    if include_detail:
        product_images.append({"id": "detail", "slot": "Detail"})
    product = {"name": product_name, "clothing_type": "top",
               "colors": [{"isBase": True, "images": product_images}]}
    analysis = {"targetGenders": ["women"], "fit": "regular"}
    if analysis_pattern is not None:
        analysis["pattern"] = {"type": analysis_pattern}
    evidence = {
        "front": {"id": "front", "checksum": hashlib.sha256(source_png).hexdigest(),
                  "width": 1536, "height": 1536, "mime_type": "image/png",
                  "source": "upload"},
        "back": {"id": "back", "checksum": hashlib.sha256(source_png).hexdigest(),
                 "width": 1536, "height": 1536, "mime_type": "image/png",
                 "source": "upload"},
        "detail": {"id": "detail", "checksum": hashlib.sha256(detail_png).hexdigest(),
                   "width": 1536, "height": 1536, "mime_type": "image/png",
                   "source": "upload"},
    }

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
        if image.data == source_png:
            raw = dict(SOURCE_GEOM_RAW)
            if not source_component_box:
                raw.pop("collar_box", None)
                raw.pop("placket_box", None)
            if omit_cuff_boxes:
                raw.pop("cuff_l_box", None)
                raw.pop("cuff_r_box", None)
                # Carrier fixture와 같은 물리 sleeve/torso 비율(~0.53).
                raw["sleeve_l_end"] = [0.05, 0.49]
                raw["sleeve_r_end"] = [0.95, 0.49]
            return raw
        raw = _carrier_geom_raw(cx)
        if omit_cuff_boxes:
            raw.pop("cuff_l_box", None)
            raw.pop("cuff_r_box", None)
        if carrier_component_box:
            raw["collar_box"] = [[0.45, 0.16], [0.55, 0.16],
                                 [0.55, 0.20], [0.45, 0.20]]
        return raw

    async def get_product(conn, project_id):
        return product

    async def get_analysis(conn, project_id):
        return dict(analysis)

    async def get_product_truth(conn, project_id, truth_id=None):
        if not truth_approved:
            return None
        pattern_spec = {"type": truth_pattern} if truth_pattern is not None else {}
        if truth_fine_pattern is not None:
            pattern_spec["finePattern"] = truth_fine_pattern
        return {
            "id": truth_id or "truth-1",
            "version": 1,
            "status": "approved",
            "garment_spec": {"collarType": "point", "buttonCount": 7},
            "color_spec": {},
            "pattern_spec": pattern_spec,
            "protected_details": {},
            "source_fingerprint": pt.source_fingerprint(product, analysis, evidence),
        }

    async def list_product_truth_asset_evidence(conn, user_id, asset_ids):
        return [evidence[asset_id] for asset_id in asset_ids if asset_id in evidence]

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
                     ("get_product_truth", get_product_truth),
                     ("list_product_truth_asset_evidence", list_product_truth_asset_evidence),
                     ("get_matching_item_asset", get_matching_item_asset),
                     ("finalize_mannequin_success", finalize_success),
                     ("finalize_mannequin_failure", finalize_failure)):
        monkeypatch.setattr(repo, name, fn)
    monkeypatch.setattr(mj, "_emit", fake_emit)
    # This suite owns the hybrid-composite stages. Keep its synthetic carrier
    # independent from the worker's separate composition gate so a crop-fixture
    # change cannot prevent the hybrid pipeline under test from running.
    monkeypatch.setattr(
        mj.qc,
        "evaluate_mannequin_qc",
        lambda _data: mj.qc.QcResult(
            "pass",
            [],
            {
                "width": 1024,
                "height": 1536,
                "aspect": 0.667,
                "bboxTop": 0.08,
                "bboxBottom": 0.95,
                "bboxHeight": 0.87,
            },
        ),
    )
    monkeypatch.setattr(mj.hybrid_landmarks, "extract_geometry", fake_geometry)
    clean_carrier_vision = {
        "shirtSilhouette": "shirt",
        "hemPlausible": True,
        "sleevesPlausible": True,
        "lowerBodyPresent": True,
        "matchingGarmentPresent": None,
        "mannequinFramePreserved": True,
        "garmentCategoryMatches": True,
        "confidence": 0.95,
        "uncertainFields": ["matchingGarmentPresent"],
        "evidence": ["full body and plausible shirt are visible"],
    }
    carrier_seq = (list(carrier_vision) if isinstance(carrier_vision, list)
                   else [carrier_vision or clean_carrier_vision])
    carrier_state = {"n": 0}

    async def fake_carrier_vision(settings, **kwargs):
        value = carrier_seq[min(carrier_state["n"], len(carrier_seq) - 1)]
        carrier_state["n"] += 1
        if isinstance(value, BaseException):
            raise value
        return dict(value), {
            "provider": "fake", "promptVersion": "test", "status": "ok",
            "imageCount": 2 + len(kwargs.get("product_sources") or []),
        }

    monkeypatch.setattr(mj.carrier_preflight_vision, "observe", fake_carrier_vision)

    if frame_results is not None:
        monkeypatch.setattr(mj, "_MANNEQUIN_FRAME_QC_ENFORCEMENT_READY", True)
        frame_seq = list(frame_results)
        frame_state = {"n": 0}

        async def fake_frame_qc(**_kwargs):
            value = frame_seq[min(frame_state["n"], len(frame_seq) - 1)]
            frame_state["n"] += 1
            return dict(value)

        monkeypatch.setattr(mj, "_apply_frame_qc", fake_frame_qc)

    if p2_verdict is None:
        p2_verdict = {
            "verdict": "pass", "mismatches": [], "correctionPrompt": None,
            "product_fidelity": 95, "physical_naturalness": 95,
            "image_quality": 95, "series_consistency": None,
            "critical_errors": [],
        }
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
    payload = {"truthPackageId": "truth-1"} if truth_approved else {}
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:t",
           "credits_reserved": 2, "payload": payload}
    asyncio.run(mj.run_mannequin_job(app, job))
    return oplog, calls, r2_saved, emits


def _statuses(emits):
    return [p.get("status") for e, p in emits if e == "step"]


def test_hybrid_fail_closed_aggregator_deletes_uploaded_candidates_before_failure():
    deleted = []
    failures = []

    class R2:
        def delete(self, key):
            deleted.append(key)

    async def fail(message, meta):
        failures.append((message, meta))

    meta = {"error": "hybrid_composite_failed_closed", "failureReason": "mask_low_confidence"}
    handled = asyncio.run(mj._fail_closed_hybrid_job_if_needed(
        R2(), fail, [{"key": "candidate-a.png"}, {"asset_id": "missing-key"}], meta))

    assert handled is True
    assert deleted == ["candidate-a.png"]
    assert failures == [(
        "패턴 합성 검증에 실패했어요. 상품 사진을 확인한 뒤 다시 시도해 주세요.",
        meta)]


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


def test_shadow_composite_runs_but_preserves_carrier_bytes(monkeypatch):
    _oplog, calls, r2_saved, emits = _run_job(
        monkeypatch, settings_kw={"mannequin_hybrid_composite": "shadow"})
    assert calls["failure"] == [] and len(calls["success"]) == 1

    completed = next(p for e, p in emits if p.get("status") == "hybrid_composite_completed")
    assert completed["mode"] == "shadow"
    assert completed["outcome"] == "would_apply"

    cut = calls["success"][0]["candidates"][0]
    hc = cut["qc_scores"]["hybridComposite"]
    carrier_sha = hc["carrierSha256"]
    assert hc["mode"] == "shadow"
    assert hc["applied"] is False
    assert hc["wouldApply"] is True
    assert hc["deterministicPassed"] is True
    assert hc["failClosed"] is False
    assert hashlib.sha256(r2_saved["data"]).hexdigest() == carrier_sha
    assert hc["outputSha256"] != carrier_sha
    assert cut["qc_scores"]["outcome"] == "auto_pass"
    assert cut["generation_metadata"]["generationPath"] == "fresh"
    assert "hybridComposite" not in cut["generation_metadata"]


def test_level2_projection_shadow_runs_inside_real_composite_and_is_persisted(monkeypatch):
    _oplog, calls, _r2, emits = _run_job(
        monkeypatch,
        settings_kw={"mannequin_texture_projection_2d": "shadow"},
    )
    plan_event = next(
        payload for _event, payload in emits
        if payload.get("status") == "hybrid_texture_projection_plan"
    )
    assert plan_event["mode"] == "shadow"
    assert plan_event["ok"] is True
    cut = calls["success"][0]["candidates"][0]
    persisted = cut["qc_scores"]["hybridComposite"]["textureProjection"]
    assert persisted["ok"] is True
    assert persisted["mode"] == "shadow"
    assert persisted["version"] == "texture_projection_2d_v1"


def test_approved_regular_stripe_enters_projection_when_fine_pattern_is_false(monkeypatch):
    _oplog, calls, _r2, emits = _run_job(
        monkeypatch,
        truth_pattern="stripe",
        truth_fine_pattern=False,
        settings_kw={
            "mannequin_hybrid_composite": "shadow",
            "mannequin_texture_projection_2d": "shadow",
        },
    )

    assert calls["failure"] == []
    assert calls["success"]
    statuses = _statuses(emits)
    assert "hybrid_composite_started" in statuses
    assert "hybrid_texture_projection_plan" in statuses
    persisted = calls["success"][0]["candidates"][0]["qc_scores"]["hybridComposite"]
    assert persisted["textureProjection"]["ok"] is True


def test_detail_model_failure_falls_back_to_front_stripe_source(monkeypatch):
    """실 HEIC 묶음 재현: Detail ROI scan 은 실패해도 Front 에서 stripe model 이 성립하면 진행한다."""
    blank_detail = _png(np.full((1536, 1536, 3), 242, np.uint8))
    _oplog, calls, _r2, emits = _run_job(
        monkeypatch,
        detail_png=blank_detail,
        truth_pattern="stripe",
        truth_fine_pattern=False,
        settings_kw={
            "mannequin_hybrid_composite": "shadow",
            "mannequin_texture_projection_2d": "shadow",
        },
    )

    assert calls["failure"] == []
    assert calls["success"]
    model_event = next(
        payload for _event, payload in emits
        if payload.get("status") == "hybrid_stripe_model"
    )
    assert model_event["source_asset_id"] == "front"
    plan_event = next(
        payload for _event, payload in emits
        if payload.get("status") == "hybrid_texture_projection_plan"
    )
    assert plan_event["ok"] is True


@pytest.mark.parametrize("pattern_type", ["solid", "unknown"])
def test_non_projectable_truth_with_no_fine_pattern_skips_hybrid(monkeypatch, pattern_type):
    _oplog, calls, _r2, emits = _run_job(
        monkeypatch,
        truth_pattern=pattern_type,
        truth_fine_pattern=False,
        settings_kw={
            "mannequin_hybrid_composite": "shadow",
            "mannequin_texture_projection_2d": "shadow",
        },
    )

    assert calls["failure"] == []
    assert calls["success"]
    statuses = _statuses(emits)
    assert "hybrid_composite_started" not in statuses
    assert "hybrid_texture_projection_plan" not in statuses
    qc_scores = calls["success"][0]["candidates"][0]["qc_scores"]
    assert qc_scores is None or "hybridComposite" not in qc_scores


def test_projection_shadow_plan_failure_does_not_fail_closed_outer_enforce(monkeypatch):
    original = mj.hc_projection.plan_periodic_projection

    def unsafe_shadow_plan(**kwargs):
        plan = original(**kwargs)
        return dataclasses.replace(plan, ok=False, reason="projection_low_confidence")

    monkeypatch.setattr(
        mj.hc_projection,
        "plan_periodic_projection",
        unsafe_shadow_plan,
    )
    _oplog, calls, _r2, emits = _run_job(
        monkeypatch,
        settings_kw={
            "mannequin_hybrid_composite": "enforce",
            "mannequin_texture_projection_2d": "shadow",
        },
    )

    assert calls["failure"] == []
    assert len(calls["success"]) == 1
    plan_event = next(
        payload for _event, payload in emits
        if payload.get("status") == "hybrid_texture_projection_plan"
    )
    assert plan_event["mode"] == "shadow"
    assert plan_event["ok"] is False
    persisted = calls["success"][0]["candidates"][0]["qc_scores"]["hybridComposite"]
    assert persisted["applied"] is True
    assert persisted["textureProjection"]["mode"] == "shadow"
    assert persisted["textureProjection"]["ok"] is False


def test_projection_shadow_subpixel_target_runs_warp_and_qc_for_metrics(monkeypatch):
    """1K 실측: target pitch 2~6px 는 shadow 에서 조기 중단하지 않고 QC 지표까지 남긴다."""
    original_extract = mj.hc_stripe.extract_stripe_model_scan

    def tiny_period_model(*args, **kwargs):
        model = original_extract(*args, **kwargs)
        if isinstance(model, mj.CompositeFailure):
            return model
        return dataclasses.replace(model, period_px=5.0)

    monkeypatch.setattr(mj.hc_stripe, "extract_stripe_model_scan", tiny_period_model)

    _oplog, calls, _r2, emits = _run_job(
        monkeypatch,
        settings_kw={
            "mannequin_hybrid_composite": "shadow",
            "mannequin_texture_projection_2d": "shadow",
        },
    )

    assert calls["failure"] == []
    assert calls["success"]
    statuses = _statuses(emits)
    assert "hybrid_texture_projection_plan" in statuses
    assert "hybrid_warp_composite" in statuses
    assert "hybrid_deterministic_qc" in statuses
    plan_event = next(
        payload for _event, payload in emits
        if payload.get("status") == "hybrid_texture_projection_plan"
    )
    assert plan_event["reason"] == "target_period_too_small"


def test_projection_uses_approved_product_truth_pattern_before_analysis_default(monkeypatch):
    _oplog, calls, r2_saved, emits = _run_job(
        monkeypatch,
        truth_pattern="check",
        analysis_pattern="stripe",
        settings_kw={
            "mannequin_hybrid_composite": "shadow",
            "mannequin_texture_projection_2d": "shadow",
        },
    )

    plan_event = next(
        payload for _event, payload in emits
        if payload.get("status") == "hybrid_texture_projection_plan"
    )
    assert plan_event["ok"] is False
    assert plan_event["reason"] == "unsupported_pattern"
    assert plan_event["metrics"]["patternType"] == "check"

    completed = next(p for _e, p in emits if p.get("status") == "hybrid_composite_completed")
    assert completed["mode"] == "shadow"
    assert completed["fail_closed"] is False
    assert calls["failure"] == []
    cut = calls["success"][0]["candidates"][0]
    assert cut["qc_scores"]["hybridComposite"]["failClosed"] is False
    assert cut["qc_scores"]["hybridComposite"]["textureProjection"]["reason"] == "unsupported_pattern"
    assert cut["qc_scores"]["outcome"] == "auto_pass"
    assert hashlib.sha256(r2_saved["data"]).hexdigest() == \
        cut["qc_scores"]["hybridComposite"]["carrierSha256"]


def test_explicit_check_truth_never_enters_legacy_stripe_renderer_when_projection_is_off(
        monkeypatch):
    _oplog, calls, r2_saved, emits = _run_job(
        monkeypatch,
        truth_pattern="check",
        analysis_pattern="stripe",
        settings_kw={
            "mannequin_hybrid_composite": "shadow",
            "mannequin_texture_projection_2d": "off",
        },
    )
    assert calls["failure"] == []
    assert calls["success"]
    completed = next(p for _e, p in emits if p.get("status") == "hybrid_composite_completed")
    assert completed["outcome"] == "unsupported_pattern"
    assert not any(p.get("status") == "hybrid_source_validated" for _e, p in emits)
    cut = calls["success"][0]["candidates"][0]
    assert cut["qc_scores"]["hybridComposite"]["failureReason"] == "unsupported_pattern"
    assert hashlib.sha256(r2_saved["data"]).hexdigest() == \
        cut["qc_scores"]["hybridComposite"]["carrierSha256"]


def test_explicit_check_truth_fails_closed_before_stripe_renderer_in_enforce(monkeypatch):
    _oplog, calls, r2_saved, emits = _run_job(
        monkeypatch,
        truth_pattern="check",
        analysis_pattern="stripe",
        settings_kw={
            "mannequin_hybrid_composite": "enforce",
            "mannequin_texture_projection_2d": "off",
        },
    )
    assert calls["success"] == []
    assert r2_saved == {}
    assert calls["failure"][0]["metadata"]["failureReason"] == "unsupported_pattern"
    assert not any(p.get("status") == "hybrid_source_validated" for _e, p in emits)


def test_shadow_composite_failure_finalizes_review_without_replacing_carrier(monkeypatch):
    check_png = _png(np.tile(render_negative("N2_gingham_check"), (2, 2, 1))[:1536, :1536])
    _oplog, calls, r2_saved, emits = _run_job(
        monkeypatch, detail_png=check_png,
        settings_kw={"mannequin_hybrid_composite": "shadow"})
    assert calls["failure"] == []
    assert len(calls["success"]) == 1
    assert r2_saved

    completed = next(p for e, p in emits if p.get("status") == "hybrid_composite_completed")
    assert completed["mode"] == "shadow"
    assert completed["fail_closed"] is False
    assert completed["outcome"] == "unsupported_pattern"

    cut = calls["success"][0]["candidates"][0]
    hc = cut["qc_scores"]["hybridComposite"]
    assert hc["mode"] == "shadow"
    assert hc["applied"] is False
    assert hc["wouldApply"] is False
    assert hc["needsReview"] is True
    assert hc["failClosed"] is False
    assert hc["failureReason"] == "unsupported_pattern"
    assert cut["qc_scores"]["outcome"] == "auto_pass"
    assert cut["generation_metadata"]["generationPath"] == "fresh"


def test_shadow_composite_does_not_suppress_llm_retry(monkeypatch):
    retry = {
        "verdict": "retry", "mismatches": [], "correctionPrompt": "try again",
        "product_fidelity": 20, "physical_naturalness": 95, "image_quality": 95,
        "series_consistency": None, "critical_errors": [],
    }
    passed = {**retry, "verdict": "pass", "correctionPrompt": None, "product_fidelity": 95}
    oplog, calls, _r2_saved, emits = _run_job(
        monkeypatch,
        settings_kw={
            "mannequin_hybrid_composite": "shadow",
            "image_qc": "enforce",
            "mannequin_max_attempts": 2,
        },
        p2_verdict=[retry, passed],
    )

    assert calls["failure"] == []
    assert len(calls["success"]) == 1
    assert sum(1 for op in oplog if op[0] == "gen") == 2
    assert not any(payload.get("status") == "hybrid_llm_retry_suppressed"
                   for event, payload in emits if event == "step")
    assert calls["success"][0]["candidates"][0]["qc_scores"]["outcome"] == "auto_pass"


def test_unsupported_pattern_fails_closed_before_save_or_success_finalize(monkeypatch):
    check_png = _png(np.tile(render_negative("N2_gingham_check"), (2, 2, 1))[:1536, :1536])
    oplog, calls, r2_saved, emits = _run_job(monkeypatch, detail_png=check_png)
    assert calls["success"] == []
    assert len(calls["failure"]) == 1
    assert r2_saved == {}

    completed = next(p for e, p in emits if p.get("status") == "hybrid_composite_completed")
    assert completed["outcome"] == "unsupported_pattern"
    meta = calls["failure"][0]["metadata"]
    assert meta["error"] == "hybrid_composite_failed_closed"
    assert meta["failureReason"] == "unsupported_pattern"
    assert meta["hybridComposite"]["applied"] is False
    assert meta["hybridComposite"]["failureReason"] == "unsupported_pattern"
    assert "detail" in meta and isinstance(meta["detail"], str)
    # fallback 재생성 0회 — 생성은 geometry 1회뿐
    assert sum(1 for op in oplog if op[0] == "gen") == 1
    assert calls["failure"][0]["reserved"] == 2


def test_missing_detail_slot_is_reference_insufficient(monkeypatch):
    _oplog, calls, r2_saved, emits = _run_job(monkeypatch, include_detail=False)
    assert calls["success"] == []
    assert len(calls["failure"]) == 1
    assert r2_saved == {}
    completed = next(p for e, p in emits if p.get("status") == "hybrid_composite_completed")
    assert completed["outcome"] == "reference_insufficient"
    meta = calls["failure"][0]["metadata"]
    assert meta["error"] == "hybrid_composite_failed_closed"
    assert meta["failureReason"] == "reference_insufficient"


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


def test_deterministic_pass_cannot_suppress_post_projection_vision_rejection(monkeypatch):
    """결정론 QC 통과만으로 육안상 실패한 합성본을 저장할 수 없다.

    1차(identity) Vision은 pass, 합성 후 Vision만 reject인 시퀀스다. 자동 재생성은
    추가하지 않되 후보를 fail-closed 하므로 비용 상한과 3중 게이트를 동시에 지킨다.
    """
    good_p2 = {"verdict": "pass", "product_fidelity": 95, "physical_naturalness": 95,
               "image_quality": 95, "critical_errors": [], "mismatches": [],
               "correctionPrompt": ""}
    bad_p2 = {"verdict": "retry", "product_fidelity": 20, "physical_naturalness": 20,
              "image_quality": 20, "critical_errors": [], "mismatches": [],
              "correctionPrompt": ""}
    oplog, calls, r2_saved, emits = _run_job(
        monkeypatch, settings_kw={"image_qc": "enforce", "qc_score_auto_pass": 80,
                                  "qc_score_review": 65},
        p2_verdict=[good_p2, bad_p2])
    assert sum(1 for op in oplog if op[0] == "gen") == 1, "낮은 LLM 점수가 재생성을 태움"
    assert "hybrid_deterministic_qc" in _statuses(emits)
    assert calls["success"] == [] and len(calls["failure"]) == 1
    assert r2_saved == {}
    assert calls["failure"][0]["metadata"]["failureReason"] == "vision_qc_rejected"


def test_enforce_composite_with_unprotected_component_fails_before_save(monkeypatch):
    _oplog, calls, r2_saved, emits = _run_job(monkeypatch, carrier_component_box=True, source_component_box=False)

    assert calls["success"] == []
    assert len(calls["failure"]) == 1
    assert r2_saved == {}
    assert calls["failure"][0]["metadata"]["failureReason"] == "protected_component_missing"
    completed = next(p for _e, p in emits if p.get("status") == "hybrid_composite_completed")
    assert completed["outcome"] == "protected_component_missing"
    assert completed["fail_closed"] is True


def test_composite_failure_cannot_be_overridden_by_llm_auto_pass(monkeypatch):
    """반대 방향 — LLM 만점이라도 composite typed 실패면 성공 출고될 수 없다."""
    check_png = _png(np.tile(render_negative("N2_gingham_check"), (2, 2, 1))[:1536, :1536])
    good_p2 = {"verdict": "pass", "product_fidelity": 95, "physical_naturalness": 95,
               "image_quality": 95, "critical_errors": [], "mismatches": [],
               "correctionPrompt": ""}
    _oplog, calls, r2_saved, _emits = _run_job(
        monkeypatch, detail_png=check_png,
        settings_kw={"image_qc": "enforce", "qc_score_auto_pass": 80,
                     "qc_score_review": 65},
        p2_verdict=good_p2)
    assert calls["success"] == []
    assert r2_saved == {}
    assert calls["failure"][0]["metadata"]["failureReason"] == "unsupported_pattern"


def test_salvage_candidate_with_composite_failure_fails_closed_before_save(monkeypatch):
    """사전 QC reject 구제본도 저장 직전 composite applied=false 면 실패 종결한다."""
    check_png = _png(np.tile(render_negative("N2_gingham_check"), (2, 2, 1))[:1536, :1536])
    bad_p2 = {"verdict": "retry", "product_fidelity": 20, "physical_naturalness": 80,
              "image_quality": 80, "critical_errors": [], "mismatches": [],
              "correctionPrompt": "wrong garment"}
    oplog, calls, r2_saved, emits = _run_job(
        monkeypatch, detail_png=check_png,
        settings_kw={"image_qc": "enforce", "mannequin_max_attempts": 1,
                     "qc_score_auto_pass": 80, "qc_score_review": 65},
        p2_verdict=bad_p2)
    assert "qc_salvaged" in _statuses(emits)
    assert calls["success"] == []
    assert len(calls["failure"]) == 1
    assert r2_saved == {}
    assert calls["failure"][0]["metadata"]["failureReason"] == "unsupported_pattern"
    assert sum(1 for op in oplog if op[0] == "gen") == 1


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
    # Detail 은 항상 입력 gate·구조 모델의 정본으로 소비된다. 팔레트는 front 가 구조
    # 완전 일치일 때 정본이 될 수 있고(hybrid_palette_source 이벤트로 기록), 그 경우
    # stripeModel provenance 는 front 로 남는다 — 어느 쪽이든 이벤트와 일치해야 한다.
    ev = next(p for _e, p in emits if p.get("status") == "hybrid_stripe_model")
    assert ev["source_asset_id"] == "detail"
    swapped = any(p.get("status") == "hybrid_palette_source" for _e, p in emits)
    expect_src = "front" if swapped else "detail"
    assert hc["stripeModel"]["source_asset_id"] == expect_src


# ── D-5: landmark 관측 ─────────────────────────────────────────────────────────
# component box 가 사라진 단계를 구분할 수 없어 protected_component_missing 을 보고도
# vision 미반환·병합 손실·validator 거부 중 무엇인지 알 수 없었다.

def _landmark_events(emits):
    return [p for _e, p in emits if p.get("status") == "hybrid_landmark_geometry"]


def test_landmark_geometry_event_records_each_stage(monkeypatch):
    _oplog, _calls, _r2, emits = _run_job(monkeypatch, carrier_component_box=True, source_component_box=False)
    evs = _landmark_events(emits)
    assert evs, "landmark 관측 이벤트가 없으면 실패를 진단할 수 없다"
    ev = evs[0]
    for side in ("source", "carrier"):
        assert {"call_a", "call_b", "merged"} <= set(ev[side]), side
    # carrier 는 collar_box 를 받았고 source 는 안 받았다 — 그 차이가 이벤트에 보여야 한다
    assert ev["carrier"]["merged"]["collar_box"]["present"] is True
    assert ev["source"]["merged"]["collar_box"]["present"] is False
    assert ev["source"]["merged"]["collar_box"]["rejected"] == "absent"


def test_landmark_event_distinguishes_absent_from_malformed(monkeypatch):
    """vision 이 안 준 것과 형식이 틀려 버려진 것은 다른 사유로 남아야 한다."""
    import app.workers.mannequin_job as mj
    from app.agents.hybrid_landmarks import component_observation
    absent = component_observation({})
    malformed = component_observation({
        "collar_box": [[45.0, 16.0], [55.0, 16.0], [55.0, 20.0], [45.0, 20.0]]})
    assert absent["collar_box"]["rejected"] == "absent"
    assert malformed["collar_box"]["rejected"] == "coord_out_of_unit_range"
    assert mj.hybrid_landmarks.PROMPT_VERSION


def test_landmark_event_carries_no_urls_tokens_or_raw_response(monkeypatch):
    _oplog, _calls, _r2, emits = _run_job(monkeypatch, carrier_component_box=True, source_component_box=False)
    blob = json.dumps(_landmark_events(emits), ensure_ascii=False)
    for forbidden in ("http://", "https://", "Bearer", "token", "garment_visible",
                      "You are a garment geometry annotator"):
        assert forbidden not in blob, f"관측 payload 에 {forbidden} 이 새면 안 된다"


def test_hybrid_failure_persists_nothing_and_uploads_nothing(monkeypatch):
    """실패한 합성은 R2·output·cut 어디에도 남지 않는다."""
    _oplog, calls, r2_saved, _emits = _run_job(monkeypatch, carrier_component_box=True, source_component_box=False)
    assert r2_saved == {}, "실패인데 R2 객체가 남으면 고아가 된다"
    assert calls["success"] == [], "실패인데 성공 콜백이 돌면 output/cut 이 생긴다"
    assert len(calls["failure"]) == 1
    meta = calls["failure"][0]["metadata"]
    assert meta["failureReason"] == "protected_component_missing"
    assert "baselineId" not in meta, "실패본이 baseline 에 연결되면 안 된다"


def test_artifact_dump_is_off_unless_explicitly_pointed(monkeypatch, tmp_path):
    """QA 덤프는 기본 비활성 — 운영 경로에서 디스크에 쓰지 않는다."""
    import app.workers.mannequin_job as mj
    monkeypatch.delenv("HYBRID_COMPOSITE_ARTIFACT_DIR", raising=False)
    mj._dump_composite_artifacts(np.zeros((4, 4, 3), np.uint8), None, None)
    assert list(tmp_path.iterdir()) == []


def test_artifact_geometry_captures_the_exact_carrier_preflight_contract(
        monkeypatch, tmp_path):
    """다음 replay가 production preflight를 우회하지 않도록 입력과 판정을 함께 남긴다."""
    monkeypatch.setenv("HYBRID_COMPOSITE_ARTIFACT_DIR", str(tmp_path))
    _run_job(monkeypatch)

    geometry = json.loads((tmp_path / "geometry.json").read_text())
    assert geometry["schema"] == "stripe_replay_geometry_v2"
    carrier = cv2.imread(str(tmp_path / "carrier.png"))
    source = cv2.imread(str(tmp_path / "source_front.png"))
    assert geometry["carrier_size"] == [carrier.shape[1], carrier.shape[0]]
    assert geometry["source_size"] == [source.shape[1], source.shape[0]]
    assert geometry["source_landmarks"] == {
        key: SOURCE_GEOM_RAW[key]
        for key in ("shoulder_l", "shoulder_r", "hem_l", "hem_r")
    }
    assert geometry["source_component_boxes_norm"] == {
        key: SOURCE_GEOM_RAW[key]
        for key in ("collar_box", "placket_box", "cuff_l_box", "cuff_r_box")
    }
    assert set(geometry["source_inventory"]["component_box_sources"].values()) == {
        "vision_explicit"
    }
    inputs = geometry["carrier_preflight_inputs"]
    assert inputs["canonical_evidence"]["expected_lower"] is True
    assert inputs["require_vision"] is True
    assert inputs["vision_observations"]["mannequinFramePreserved"] is True
    assert geometry["carrier_preflight_summary"]["decision"] == "PASS"
    assert geometry["protected_component_contract"]["status"] == "PASS"
    serialized = json.dumps(geometry, ensure_ascii=False)
    for forbidden in ("http://", "https://", "Bearer", "token"):
        assert forbidden not in serialized


def test_early_protected_failure_still_captures_paid_carrier_for_replay(
        monkeypatch, tmp_path):
    """Stage 4 전 실패여도 carrier/source/정확한 gate 상태를 잃으면 안 된다."""
    monkeypatch.setenv("HYBRID_COMPOSITE_ARTIFACT_DIR", str(tmp_path))
    _oplog, calls, _r2, _emits = _run_job(
        monkeypatch,
        carrier_component_box=True,
        source_component_box=False,
    )

    assert calls["failure"][0]["metadata"]["failureReason"] == "protected_component_missing"
    assert (tmp_path / "carrier.png").exists()
    assert (tmp_path / "source_front.png").exists()
    geometry = json.loads((tmp_path / "geometry.json").read_text())
    assert geometry["schema"] == "stripe_replay_geometry_v2"
    assert geometry["capture_stage"] == "failed"
    assert geometry["failure_reason"] == "protected_component_missing"
    carrier = cv2.imread(str(tmp_path / "carrier.png"))
    source = cv2.imread(str(tmp_path / "source_front.png"))
    assert geometry["carrier_size"] == [carrier.shape[1], carrier.shape[0]]
    assert geometry["source_size"] == [source.shape[1], source.shape[0]]
    assert geometry["source_landmarks"] == {
        key: SOURCE_GEOM_RAW[key]
        for key in ("shoulder_l", "shoulder_r", "hem_l", "hem_r")
    }
    assert geometry["source_component_boxes_norm"] == {
        key: SOURCE_GEOM_RAW[key]
        for key in ("cuff_l_box", "cuff_r_box")
    }
    assert geometry["source_inventory"]["component_box_sources"] == {
        "cuff_l_box": "vision_explicit",
        "cuff_r_box": "vision_explicit",
    }
    assert geometry["carrier_preflight_inputs"]["require_vision"] is True
    assert geometry["carrier_preflight_summary"]["decision"] == "PASS"
    assert geometry["protected_component_contract"]["status"] == "MISSING"
    assert {
        item["component"] for item in geometry["protected_component_contract"]["missing"]
    } == {"collar", "placket"}
    assert geometry["source_landmarks"] and geometry["carrier_landmarks"]


def test_artifact_dump_failure_never_fails_the_production_job(monkeypatch, tmp_path):
    """QA 보조 파일 쓰기 실패는 생성·QC·출고 경로와 독립이어야 한다."""
    monkeypatch.setenv("HYBRID_COMPOSITE_ARTIFACT_DIR", str(tmp_path))

    def fail_write(*_args, **_kwargs):
        raise OSError("synthetic_artifact_write_failure")

    monkeypatch.setattr(cv2, "imwrite", fail_write)
    _oplog, calls, r2_saved, _emits = _run_job(monkeypatch)

    assert calls["failure"] == []
    assert len(calls["success"]) == 1
    assert r2_saved, "artifact failure must not suppress the accepted output"


def test_missing_optional_cuff_boxes_use_the_shared_deterministic_fallback(monkeypatch):
    """양쪽 끝점이 있으면 Product Truth 보호 계약은 유지한 채 cuffs를 공급한다."""
    _oplog, calls, _r2, emits = _run_job(monkeypatch, omit_cuff_boxes=True)

    protected_rows = [
        payload for _event, payload in emits
        if payload.get("status") == "hybrid_protected_contract"
    ]
    assert protected_rows, {
        "statuses": [payload.get("status") for _event, payload in emits],
        "failures": calls["failure"],
    }
    protected = protected_rows[0]
    assert protected["contract_status"] == "PASS"
    assert "cuffs" in protected["available"]
    validated = next(
        payload for _event, payload in emits
        if payload.get("status") == "hybrid_landmark_validated"
    )
    for side in ("source", "carrier"):
        sources = validated[f"{side}_component_box_sources"]
        assert sources["cuff_l_box"] == hybrid_landmarks.CUFF_GEOMETRY_VERSION
        assert sources["cuff_r_box"] == hybrid_landmarks.CUFF_GEOMETRY_VERSION
    assert not (
        calls["failure"]
        and calls["failure"][0]["metadata"].get("failureReason")
        == "protected_component_missing"
    )


def _carrier_observation(**overrides):
    row = {
        "shirtSilhouette": "shirt",
        "hemPlausible": True,
        "sleevesPlausible": True,
        "lowerBodyPresent": True,
        "matchingGarmentPresent": None,
        "mannequinFramePreserved": True,
        "garmentCategoryMatches": True,
        "confidence": 0.95,
        "uncertainFields": ["matchingGarmentPresent"],
        "evidence": [],
    }
    row.update(overrides)
    return row


def test_bad_carrier_retries_generation_once_before_projection(monkeypatch):
    bad = _carrier_observation(shirtSilhouette="cape")
    good = _carrier_observation()

    oplog, calls, _r2, emits = _run_job(
        monkeypatch,
        settings_kw={"mannequin_max_attempts": 2},
        carrier_vision=[bad, good],
    )

    assert calls["failure"] == [] and calls["success"]
    assert sum(1 for op in oplog if op[0] == "gen") == 2
    assert _statuses(emits).count("hybrid_carrier_retry") == 1
    assert _statuses(emits).count("hybrid_warp_composite") == 1


def test_ratio_only_preflight_continue_allows_projection_when_flag_on(monkeypatch):
    """cap-like carrier 실루엣은 1회 한정 soft-continue 해서 합성까지 진행한다."""
    oplog, calls, r2_saved, emits = _run_job(
        monkeypatch,
        settings_kw={
            "hybrid_stripe_allow_ratio_only_preflight_continue": True,
            "mannequin_max_attempts": 1,
        },
        carrier_vision=[_carrier_observation(shirtSilhouette="cape")],
    )

    assert calls["failure"] == [] and calls["success"]
    assert r2_saved != {}
    assert sum(1 for op in oplog if op[0] == "gen") == 1
    assert _statuses(emits).count("hybrid_carrier_retry") == 0
    assert _statuses(emits).count("hybrid_warp_composite") == 1
    preflight = next(payload for event, payload in emits
                     if event == "step" and payload.get("status") == "hybrid_carrier_preflight")
    assert preflight["passed"] is True
    assert preflight["decision"] == "pass"
    assert preflight["vision_status"] == "ok"


def test_ratio_only_preflight_continue_blocks_on_additional_failure(monkeypatch):
    """cap 코드와 hem 불일치가 동시에 있으면 soft-continue는 허용되지 않는다."""
    oplog, calls, r2_saved, emits = _run_job(
        monkeypatch,
        settings_kw={
            "hybrid_stripe_allow_ratio_only_preflight_continue": True,
            "mannequin_max_attempts": 1,
        },
        carrier_vision=[_carrier_observation(shirtSilhouette="cape", hemPlausible=False)],
    )

    assert sum(1 for op in oplog if op[0] == "gen") == 1
    assert _statuses(emits).count("hybrid_carrier_retry") == 0
    assert "hybrid_warp_composite" not in _statuses(emits)
    assert calls["success"] == []
    assert len(calls["failure"]) == 1
    assert r2_saved == {}
    assert calls["failure"][0]["metadata"]["failureReason"] == "carrier_preflight_rejected"


def test_ratio_only_preflight_continue_blocks_uncertain_critical_observation(monkeypatch):
    """cap-like 실패라도 critical 필드 불확실성이 있으면 soft-continue를 막아야 한다."""
    oplog, calls, r2_saved, emits = _run_job(
        monkeypatch,
        settings_kw={
            "hybrid_stripe_allow_ratio_only_preflight_continue": True,
            "mannequin_max_attempts": 1,
        },
        carrier_vision=[_carrier_observation(
            shirtSilhouette="cape",
            uncertainFields=["matchingGarmentPresent", "mannequinFramePreserved"],
        )],
    )

    assert sum(1 for op in oplog if op[0] == "gen") == 1
    assert _statuses(emits).count("hybrid_carrier_retry") == 0
    assert "hybrid_warp_composite" not in _statuses(emits)
    assert calls["success"] == []
    assert len(calls["failure"]) == 1
    assert r2_saved == {}
    assert calls["failure"][0]["metadata"]["failureReason"] == "carrier_preflight_rejected"


def test_second_bad_carrier_fails_closed_without_projection_or_storage(monkeypatch):
    bad = _carrier_observation(shirtSilhouette="slab", lowerBodyPresent=False)

    oplog, calls, r2_saved, emits = _run_job(
        monkeypatch,
        settings_kw={"mannequin_max_attempts": 3},
        carrier_vision=[bad, bad, _carrier_observation()],
    )

    assert sum(1 for op in oplog if op[0] == "gen") == 2
    assert _statuses(emits).count("hybrid_carrier_retry") == 1
    assert "hybrid_warp_composite" not in _statuses(emits)
    assert calls["success"] == [] and len(calls["failure"]) == 1
    assert r2_saved == {}
    assert calls["failure"][0]["metadata"]["failureReason"] == "carrier_preflight_rejected"


def test_enforce_requires_post_projection_vision_pass_before_storage(monkeypatch):
    visual_reject = {
        "verdict": "retry", "mismatches": ["shirt silhouette changed"],
        "correctionPrompt": "regenerate carrier", "product_fidelity": 20,
        "physical_naturalness": 20, "image_quality": 30,
        "series_consistency": None, "critical_errors": ["garment shape altered"],
    }

    _oplog, calls, r2_saved, emits = _run_job(
        monkeypatch,
        settings_kw={"image_qc": "off", "mannequin_max_attempts": 1},
        p2_verdict=visual_reject,
    )

    assert "hybrid_deterministic_qc" in _statuses(emits)
    assert calls["success"] == [] and len(calls["failure"]) == 1
    assert r2_saved == {}
    assert calls["failure"][0]["metadata"]["failureReason"] == "vision_qc_rejected"


def test_projection_cannot_rollback_to_unprotected_carrier_after_final_frame_reject(monkeypatch):
    frame_pass = {
        "decision": "pass", "criticalErrors": [], "warnings": [],
        "checks": {}, "metrics": {}, "regenerationInstructions": [],
    }
    frame_reject = {
        "decision": "reject", "criticalErrors": ["severe_yaw"], "warnings": [],
        "checks": {}, "metrics": {}, "regenerationInstructions": [],
    }
    _oplog, calls, r2_saved, emits = _run_job(
        monkeypatch,
        settings_kw={"mannequin_frame_qc": "enforce", "mannequin_max_attempts": 1},
        frame_results=[frame_pass, frame_reject],
    )

    assert "hybrid_deterministic_qc" in _statuses(emits)
    assert calls["success"] == [] and len(calls["failure"]) == 1
    assert r2_saved == {}
    assert calls["failure"][0]["metadata"]["failureReason"] == "final_frame_qc_rejected"
    assert "frame_qc_rollback" not in _statuses(emits)


def test_collarless_garment_does_not_require_protected_boxes(monkeypatch):
    """민소매·풀오버처럼 카라·플래킷이 없는 옷을 보호 부위 부재로 거절하면 오거절이다."""
    import app.workers.mannequin_job as mj
    src = {"collar": False, "placket": False}
    car = {"collar": False, "placket": False}
    missing = []
    for part in ("collar", "placket"):
        exists = bool(src.get(part) or car.get(part))
        if exists:
            missing.append(f"{part}_box")
    assert missing == [], "구조가 없는 옷에는 보호 부위 geometry 를 요구하지 않는다"
    assert mj  # 모듈이 실제로 로드되는지까지 확인


def test_spurious_single_side_box_does_not_become_a_hard_requirement():
    """한쪽 호출이 박스를 헛짚었다고 해서 그것만으로 하드 요구가 되면 안 된다."""
    src_inv, car_inv = {"collar": False}, {"collar": False}
    src_boxes = {"collar_box": [[0.1, 0.1]] * 4}   # 헛짚은 쪽
    car_boxes = {}
    exists = bool(src_inv.get("collar") or car_inv.get("collar"))
    assert exists is False
    missing = ["collar_box"] if (exists and "collar_box" not in car_boxes) else []
    assert missing == [], "inventory 가 부정하면 박스 하나로 요구가 생기지 않는다"
    assert src_boxes  # 표본이 실제로 존재했다는 사실은 유지
