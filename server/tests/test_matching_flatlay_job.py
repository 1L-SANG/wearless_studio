"""`matching_cutout` 워커의 플랫레이 재렌더 단계 배선.

누끼는 배경만 지운다 — 옷걸이에 걸린 채 찍힌 사진은 각도가 그대로다. 이 단계는
누끼 성공본을 시드 카탈로그와 같은 정면 flat-lay 로 다시 렌더해 카드 썸네일만 바꾼다.
생성 실패는 어떤 형태든 누끼 썸네일(그리고 그 폴백인 셀러 원본)을 그대로 둔다.

누끼 워커의 가짜 배선(_wire_worker)을 그대로 빌려 쓴다 — 플랫레이는 그 위에 얹히는
단계이므로, 부모 계약이 바뀌면 여기도 같이 깨지는 게 맞다.
"""
import asyncio
import base64
import io
import hashlib
import types
from types import SimpleNamespace

import pytest
from PIL import Image

from app import image_usage
from app.agents import gemini_image
from app.services import matching_cutout as mc
from app.services import matching_flatlay as mf
from app.services.sam_client import SamViewResult
from app.workers import matching_cutout_job as job
from test_matching_cutout_job import _source_png, _cut_png, _job_dict, _run, _wire_worker

GEN_COLOR = (17, 200, 90)


def _gen_png(size=(1024, 1024), color=GEN_COLOR):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


class _FakeGemini:
    """호출 인자를 그대로 붙잡아 두는 가짜 클라이언트. 네트워크·키 없음."""

    def __init__(self, *, image=None, error=None):
        self.calls = []
        self._image = image
        self._error = error

    async def generate_content_image(self, model, prompt, images, image_size,
                                     temperature=None, aspect_ratio=None, timeout=180.0):
        self.calls.append({"model": model, "prompt": prompt, "images": images,
                           "image_size": image_size, "aspect_ratio": aspect_ratio,
                           "timeout": timeout,
                           "stage": image_usage._ctx.get().stage})
        if self._error is not None:
            raise self._error
        return gemini_image.GeminiImageResult(
            image=self._image, mime="image/png", latency_ms=10_000,
            usage={"promptTokenCount": 369, "candidatesTokenCount": 1483})


class _ExplodingGemini:
    async def generate_content_image(self, *a, **kw):
        raise AssertionError("플래그 off 인데 생성이 불렸다")


def _wire_flatlay(monkeypatch, *, gemini, flag="on", clothing_type="bottom",
                  lookup_boom=False, **wire_kw):
    app, r2, calls = _wire_worker(monkeypatch, **wire_kw)
    app.state.settings.matching_flatlay = flag
    app.state.settings.model_image_light = "gemini-3.1-flash-image"
    app.state.settings.model_image_high = "gemini-3-pro-image"
    app.state.settings.model_text = "gpt-5.4-mini"
    app.state.gemini = gemini
    calls["meta_lookups"] = []

    async def fake_metadata(conn, item_id, user_id, project_id):
        calls["meta_lookups"].append((item_id, user_id, project_id))
        if lookup_boom:
            raise RuntimeError("db down")
        return {"clothing_type": clothing_type}

    monkeypatch.setattr(job.repo, "get_matching_item_metadata", fake_metadata)
    return app, r2, calls


def _fingerprint(*, direct=True):
    hashes = ([hashlib.sha256(_source_png()).hexdigest()] * len(_job_dict()["payload"]["sourceKeys"])
              if direct else ["h" + key for key in _job_dict()["payload"]["sourceKeys"]])
    return mc.source_fingerprint(hashes)


def _thumb_put(r2):
    return r2.puts[0]


def _grid_put(r2):
    return r2.puts[1]


def _avg_color(jpeg_bytes):
    with Image.open(io.BytesIO(jpeg_bytes)) as img:
        return img.convert("RGB").resize((1, 1), Image.LANCZOS).getpixel((0, 0))


def _close(a, b, tol=25):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# ── 플래그 off ─────────────────────────────────────────────────────────────────

def test_flag_off_is_a_complete_no_op(monkeypatch):
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=_ExplodingGemini(), flag="off")
    _run(app, _job_dict())

    assert calls["meta_lookups"] == [], "플래그 off 면 DB 조회조차 없다"
    thumb_id = mc.derived_asset_id(
        role="thumb", matching_item_id="custom_x",
        source_hash=mc.source_fingerprint(["h" + k for k in _job_dict()["payload"]["sourceKeys"]]))
    assert calls["swap"][1] == thumb_id, "누끼 썸네일 그대로"
    assert calls["finalize"][0] == "done"
    assert "flatlay" not in calls["finalize"][1], "결과 payload 도 오늘과 동일"


def test_missing_flag_attribute_is_treated_as_off(monkeypatch):
    app, _r2, calls = _wire_flatlay(monkeypatch, gemini=_ExplodingGemini(), flag="off")
    del app.state.settings.matching_flatlay
    _run(app, _job_dict())
    assert calls["finalize"][0] == "done" and calls["finalize"][1]["state"] == "ready"


# ── 누끼가 없으면 생성도 없다 ──────────────────────────────────────────────────

def test_sam_rejection_does_not_block_direct_generation(monkeypatch):
    gemini = _FakeGemini(image=_gen_png())
    app, _, calls = _wire_flatlay(monkeypatch, gemini=gemini)
    async def not_ready(settings, views):
        raise AssertionError("Gemini 직접 처리에서 SAM이 호출됐다")
    monkeypatch.setattr(job.sam_client, "segment_garment", not_ready)
    _run(app, _job_dict())
    assert len(gemini.calls) == 1
    assert calls["finalize"][1]["state"] == "ready"


def test_sam_outage_does_not_block_direct_generation(monkeypatch):
    from app.services.sam_client import SamUnavailable
    gemini = _FakeGemini(image=_gen_png())
    app, _, calls = _wire_flatlay(monkeypatch, gemini=gemini)
    async def down(settings, views):
        raise SamUnavailable("down")
    monkeypatch.setattr(job.sam_client, "segment_garment", down)
    _run(app, _job_dict())
    assert len(gemini.calls) == 1
    assert calls["segment"] == []
    assert calls["finalize"][1]["state"] == "ready"


# ── 성공 경로 ──────────────────────────────────────────────────────────────────

def test_generated_flatlay_becomes_the_card_thumbnail(monkeypatch):
    gemini = _FakeGemini(image=_gen_png())
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=gemini)
    _run(app, _job_dict())

    assert len(gemini.calls) == 1, "커스텀 아이템당 생성 1회"
    thumb_key, thumb_bytes, thumb_mime = _thumb_put(r2)
    assert thumb_mime == "image/jpeg"
    assert _close(_avg_color(thumb_bytes), GEN_COLOR), "저장된 썸네일이 재렌더본이다"
    img = Image.open(io.BytesIO(thumb_bytes))
    assert img.format == "JPEG" and max(img.size) == mc.THUMBNAIL_MAX_PX

    fingerprint = _fingerprint()
    flat_id = mf.derived_asset_id(matching_item_id="custom_x", source_hash=fingerprint)
    assert calls["swap"][1] == flat_id
    assert thumb_key.endswith(f"{flat_id}.jpg")
    meta = {a["asset_id"]: a["metadata"] for a in calls["assets"]}
    assert meta[flat_id]["purpose"] == mc.CUTOUT_PURPOSE, "삭제 경로가 회수할 수 있어야"
    assert meta[flat_id]["algorithmVersion"] == mf.ALGORITHM_VERSION
    assert meta[flat_id]["model"] == "gemini-3-pro-image", "리포트 추천 = Pro"
    assert calls["finalize"][0] == "done"
    assert calls["finalize"][1]["state"] == "ready"
    assert calls["finalize"][1]["flatlay"] is True


def test_generation_input_and_on_mode_grid_use_original_photos(monkeypatch):
    captured = {}
    compose = job.garment_grid.compose_garment_grid
    def capture(images):
        captured["images"] = list(images)
        return compose(images)
    monkeypatch.setattr(job.garment_grid, "compose_garment_grid", capture)
    gemini = _FakeGemini(image=_gen_png())
    app, _, calls = _wire_flatlay(monkeypatch, gemini=gemini)
    _run(app, _job_dict())
    call = gemini.calls[0]
    assert call["model"] == "gemini-3-pro-image"
    assert (call["image_size"], call["aspect_ratio"]) == ("1K", "1:1")
    assert len(call["images"]) == 1
    assert call["images"][0].mime == "image/png"
    assert call["images"][0].data == _source_png()
    assert captured["images"] == [_source_png()] * 2
    assert calls["finalize"][1]["flatlayGrid"] is False


def test_full_mode_puts_the_flatlay_raw_in_the_grid_front_cell(monkeypatch):
    """full: 생성 입력 grid 의 front 칸 = flat-lay **1K 원본**(카드용 512 아님). 나머지 칸은 누끼본.

    접힌 채 찍힌 하의가 착장 생성에서 실루엣을 잃는 문제의 해법이 이 배선이다 — front
    한 칸만 바꾸고 호출은 늘지 않는다(썸네일용 flat 재사용, 생성 1회 그대로).
    """
    captured = {}
    real_compose = job.garment_grid.compose_garment_grid

    def spy_compose(images):
        captured["inputs"] = list(images)
        return real_compose(images)

    monkeypatch.setattr(job.garment_grid, "compose_garment_grid", spy_compose)
    gemini = _FakeGemini(image=_gen_png())
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=gemini, flag="full")
    _run(app, _job_dict())

    assert len(gemini.calls) == 1, "full 이어도 생성은 아이템당 1회"
    assert captured["inputs"][0] == _gen_png(), "front 칸 = 재렌더 1K 원본 바이트"
    assert captured["inputs"][1:] == [
        _source_png()] * (len(captured["inputs"]) - 1), "나머지 칸 = 원본"
    # 썸네일도 여전히 재렌더본(512 카드 계약)이다.
    _k, thumb_bytes, _m = _thumb_put(r2)
    assert _close(_avg_color(thumb_bytes), GEN_COLOR)
    meta = {a["asset_id"]: a["metadata"] for a in calls["assets"]}
    grid_meta = meta[calls["swap"][2]]
    assert grid_meta["flatlayFront"] is True, "provenance — 생성이 뭘 봤는지 asset 이 말해야"
    assert grid_meta["flatlayModel"] == "gemini-3-pro-image"
    assert calls["finalize"][1]["flatlayGrid"] is True


def test_full_mode_keeps_original_assets_when_render_fails(monkeypatch):
    gemini = _FakeGemini(error=gemini_image.GeminiError("boom"))
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=gemini, flag="full")
    _run(app, _job_dict())
    assert calls["swap"] is None
    assert calls["assets"] == []
    assert r2.puts == []
    assert calls["finalize"][1]["state"] == "failed"


def test_on_mode_does_not_use_the_generated_photo_in_the_grid(monkeypatch):
    captured = {}
    compose = job.garment_grid.compose_garment_grid
    def capture(images):
        captured["images"] = list(images)
        return compose(images)
    monkeypatch.setattr(job.garment_grid, "compose_garment_grid", capture)
    gemini = _FakeGemini(image=_gen_png())
    app, _, calls = _wire_flatlay(monkeypatch, gemini=gemini, flag="on")
    _run(app, _job_dict())
    assert captured["images"] == [_source_png()] * 2
    assert calls["finalize"][1]["flatlayGrid"] is False


def test_prompt_noun_follows_the_items_clothing_type(monkeypatch):
    bottom = _FakeGemini(image=_gen_png())
    app, _r2, calls = _wire_flatlay(monkeypatch, gemini=bottom, clothing_type="bottom")
    _run(app, _job_dict())
    assert calls["meta_lookups"] == [("custom_x", "u1", "p1")]
    assert bottom.calls[0]["prompt"] == mf.build_prompt("bottom")
    assert "pants completely flat" in bottom.calls[0]["prompt"]

    top = _FakeGemini(image=_gen_png())
    app2, _r2b, _c2 = _wire_flatlay(monkeypatch, gemini=top, clothing_type="top")
    _run(app2, _job_dict())
    assert top.calls[0]["prompt"] == mf.build_prompt("top")
    assert "pants completely flat" not in top.calls[0]["prompt"]

    # 전략 B 문구는 의류와 무관하게 동일 — 명사·실루엣 항목 두 군데만 다르다.
    bp, tp = bottom.calls[0]["prompt"], top.calls[0]["prompt"]
    norm = lambda p: (p.replace("pants completely flat", "X")
                       .replace("garment completely flat", "X")
                       .replace(mf._BOTTOM_SILHOUETTE, "S")
                       .replace(mf._TOP_SILHOUETTE, "S"))
    assert norm(bp) == norm(tp)
    assert "leg cut" in bp and "leg cut" not in tp, "다리 지시는 하의에만"
    assert "CRITICAL IDENTITY LOCK" in bp and "CRITICAL IDENTITY LOCK" in tp


def test_clothing_type_lookup_failure_still_renders_with_the_neutral_prompt(monkeypatch):
    gemini = _FakeGemini(image=_gen_png())
    app, _r2, _calls = _wire_flatlay(monkeypatch, gemini=gemini, lookup_boom=True)
    _run(app, _job_dict())

    assert len(gemini.calls) == 1
    assert gemini.calls[0]["prompt"] == mf.build_prompt(None)


def test_derived_identity_is_stable_across_reruns(monkeypatch):
    first_app, first_r2, first_calls = _wire_flatlay(
        monkeypatch, gemini=_FakeGemini(image=_gen_png()))
    _run(first_app, _job_dict())
    second_app, second_r2, second_calls = _wire_flatlay(
        monkeypatch, gemini=_FakeGemini(image=_gen_png(color=(3, 3, 250))))
    _run(second_app, _job_dict())

    assert first_calls["swap"] == second_calls["swap"], "재실행이 같은 asset id 로 수렴"
    assert ([k for k, _d, _m in first_r2.puts]
            == [k for k, _d, _m in second_r2.puts]), "같은 R2 키"
    # 누끼 썸네일 신원과 절대 겹치지 않는다(같은 소스, 다른 알고리즘).
    cutout_thumb = mc.derived_asset_id(
        role="thumb", matching_item_id="custom_x",
        source_hash=mc.source_fingerprint(
            ["h" + k for k in _job_dict()["payload"]["sourceKeys"]]))
    assert first_calls["swap"][1] != cutout_thumb


# ── fail-open ──────────────────────────────────────────────────────────────────



@pytest.mark.parametrize("failure", [
    gemini_image.GeminiError("Gemini 500: boom"),
    gemini_image.GeminiError("GEMINI_API_KEY 미설정"),
    asyncio.TimeoutError(),
    RuntimeError("unexpected"),
])
def test_generation_failure_keeps_original_assets(monkeypatch, failure):
    gemini = _FakeGemini(error=failure)
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=gemini)
    _run(app, _job_dict())
    assert len(gemini.calls) == 1
    assert calls["swap"] is None
    assert r2.puts == []
    assert calls["finalize"][0] == "done"
    assert calls["finalize"][1]["state"] == "failed"


@pytest.mark.parametrize("bad", [b"", b"not-an-image", _gen_png()[:40]])
def test_corrupt_or_empty_generation_keeps_original_assets(monkeypatch, bad):
    gemini = _FakeGemini(image=bad)
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=gemini)
    _run(app, _job_dict())
    assert calls["swap"] is None
    assert r2.puts == []
    assert calls["finalize"][0] == "done" and calls["finalize"][1]["state"] == "failed"


def test_missing_gemini_client_keeps_original_assets(monkeypatch):
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=None)
    _run(app, _job_dict())
    assert calls["swap"] is None
    assert r2.puts == []
    assert calls["finalize"][0] == "done" and calls["finalize"][1]["state"] == "failed"


def test_a_slow_generation_cannot_wedge_the_job_forever(monkeypatch):
    assert mf.TIMEOUT_S <= 120, "리스(900s) 안에서 반드시 끝나는 상한"
    gemini = _FakeGemini(image=_gen_png())
    app, _r2, _calls = _wire_flatlay(monkeypatch, gemini=gemini)
    _run(app, _job_dict())
    assert gemini.calls[0]["timeout"] == mf.TIMEOUT_S


# ── 실비 계측 ──────────────────────────────────────────────────────────────────

def test_generation_spend_is_recorded_under_the_flatlay_stage(monkeypatch):
    """실 클라이언트 + 가짜 트랜스포트(네트워크·API 키 없음)로 record 배선까지 확인."""
    recorded = []

    def spy(**kw):
        recorded.append({**kw, "stage": image_usage._ctx.get().stage,
                         "job": image_usage._ctx.get().job_id})

    monkeypatch.setattr(image_usage, "record", spy)

    payload = {
        "usageMetadata": {"promptTokenCount": 369, "candidatesTokenCount": 1483},
        "candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": "image/png",
                            "data": base64.b64encode(_gen_png()).decode()}}]}}],
    }
    response = SimpleNamespace(status_code=200, json=lambda: payload, text="")

    class _Transport:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw): return response

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", _Transport)
    client = gemini_image.GeminiImageClient(types.SimpleNamespace(
        gemini_api_key="test-key", vertex_project=None, vertex_location="global"))

    app, r2, calls = _wire_flatlay(monkeypatch, gemini=client)
    with image_usage.job_scope(job_id="j1", user_id="u1", stage="matching_cutout"):
        _run(app, _job_dict())

    assert len(recorded) == 1
    assert recorded[0]["model"] == "gemini-3-pro-image"
    assert recorded[0]["image_size"] == "1K"
    assert recorded[0]["usage"] == {"promptTokenCount": 369, "candidatesTokenCount": 1483}
    assert recorded[0]["has_image"] is True
    assert recorded[0]["stage"] == mf.STAGE == "matching_flatlay", "무과금 잡의 실비가 보인다"
    assert recorded[0]["job"] == "j1", "디스패처가 붙인 잡 문맥은 유지"
    assert _close(_avg_color(_thumb_put(r2)[1]), GEN_COLOR)
    assert calls["finalize"][1]["flatlay"] is True


def test_the_stage_scope_is_restored_after_generation(monkeypatch):
    app, _r2, _calls = _wire_flatlay(monkeypatch, gemini=_FakeGemini(image=_gen_png()))
    with image_usage.job_scope(job_id="j1", user_id="u1", stage="matching_cutout"):
        _run(app, _job_dict())
        assert image_usage._ctx.get().stage == "matching_cutout"


# ── 과금 정책 불변 ─────────────────────────────────────────────────────────────

def test_enqueue_stays_uncharged_and_its_payload_is_unchanged(monkeypatch):
    import app.routes as routes
    created = []

    async def fake_create_job(conn, **kwargs):
        created.append(kwargs)
        return {"id": "job-1"}, True

    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)

    class _Conn:
        async def commit(self): pass
        async def rollback(self): pass

    asyncio.run(routes._enqueue_matching_cutout(
        _Conn(),
        settings=types.SimpleNamespace(matching_cutout="on", matching_flatlay="on"),
        user_id="u1", project_id="p1", matching_item_id="custom_x",
        source_asset_ids=["a1"], source_keys=["k1"], grid_asset_id="grid-1"))

    assert created[0]["credits_reserved"] == 0, "재렌더가 붙어도 무과금"
    assert set(created[0]["payload"]) == {"matchingItemId", "sourceAssetIds",
                                          "sourceKeys", "gridAssetId"}


def test_worker_never_touches_credits():
    import pathlib
    src = (pathlib.Path(job.__file__)).read_text(encoding="utf-8")
    for banned in ("reserve_credits", "charge_credits", "release_credits",
                   "credits_reserved"):
        assert banned not in src, f"무과금 잡이 {banned} 를 부르면 안 된다"


def test_model_routing_failure_keeps_original_without_a_generation_call(monkeypatch):
    gemini = _FakeGemini(image=_gen_png())
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=gemini)
    app.state.settings.model_image_high = ""
    _run(app, _job_dict())
    assert gemini.calls == []
    assert calls["finalize"][0] == "done"
    assert calls["finalize"][1]["state"] == "failed"
    assert calls["swap"] is None
    assert r2.puts == []


def test_derived_assets_stay_reclaimable_by_delete(monkeypatch):
    fp = _fingerprint()
    flat_id = mf.derived_asset_id(matching_item_id="custom_x", source_hash=fp)
    cut_id = mc.derived_asset_id(role="thumb", matching_item_id="custom_x", source_hash=fp)
    app, _, calls = _wire_flatlay(monkeypatch, gemini=_FakeGemini(image=_gen_png()))
    _run(app, _job_dict())
    assert calls["swap"][1] == flat_id
    grid_meta = {a["asset_id"]: a["metadata"] for a in calls["assets"]}[calls["swap"][2]]
    reachable = set(grid_meta["sourceAssetIds"]) | {calls["swap"][1], calls["swap"][2]}
    assert {flat_id, cut_id, "a1", "a2", "old-grid"} <= reachable


def test_flag_off_does_not_widen_the_cleanup_list(monkeypatch):
    """플래그 off 면 정리 목록도 부모 PR 그대로다 — 존재하지 않는 id 를 싣지 않는다."""
    app, _r2, calls = _wire_flatlay(monkeypatch, gemini=_ExplodingGemini(), flag="off")
    _run(app, _job_dict())

    grid_meta = {a["asset_id"]: a["metadata"] for a in calls["assets"]}[calls["swap"][2]]
    fp = _fingerprint(direct=False)
    assert mf.derived_asset_id(
        matching_item_id="custom_x", source_hash=fp) not in grid_meta["sourceAssetIds"]
