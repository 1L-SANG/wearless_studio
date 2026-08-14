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
from test_matching_cutout_job import _cut_png, _job_dict, _run, _wire_worker

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


def _fingerprint():
    """_wire_worker 의 가짜 SAM 이 내는 소스 해시로 만든 파생 지문."""
    return mc.source_fingerprint(
        ["h" + k for k in _job_dict()["payload"]["sourceKeys"]])


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

def test_no_generation_when_the_cutout_failed(monkeypatch):
    gemini = _FakeGemini(image=_gen_png())
    app, _r2, calls = _wire_flatlay(monkeypatch, gemini=gemini)

    async def not_ready(settings, views):
        (v, _k), = views.items()
        return {v: SamViewResult(view=v, ready=False)}

    monkeypatch.setattr(job.sam_client, "segment_garment", not_ready)
    _run(app, _job_dict())

    assert gemini.calls == [], "누끼 없는 원본을 재렌더하지 않는다"
    assert calls["swap"] is None
    assert calls["finalize"] == ("done", {"state": "failed", "reason": "no_cutout",
                                          "matchingItemId": "custom_x"})


def test_no_generation_when_sam_is_unavailable(monkeypatch):
    from app.services.sam_client import SamUnavailable
    gemini = _FakeGemini(image=_gen_png())
    app, _r2, calls = _wire_flatlay(monkeypatch, gemini=gemini)

    async def down(settings, views):
        raise SamUnavailable("down")

    monkeypatch.setattr(job.sam_client, "segment_garment", down)
    _run(app, _job_dict())

    assert gemini.calls == []
    assert calls["swap"] is None


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

    fingerprint = mc.source_fingerprint(
        ["h" + k for k in _job_dict()["payload"]["sourceKeys"]])
    flat_id = mf.derived_asset_id(matching_item_id="custom_x", source_hash=fingerprint)
    assert calls["swap"][1] == flat_id
    assert thumb_key.endswith(f"{flat_id}.jpg")
    meta = {a["asset_id"]: a["metadata"] for a in calls["assets"]}
    assert meta[flat_id]["purpose"] == mc.CUTOUT_PURPOSE, "삭제 경로가 회수할 수 있어야"
    assert meta[flat_id]["algorithmVersion"] == mf.ALGORITHM_VERSION
    assert meta[flat_id]["model"] == "gemini-3.1-flash-image"
    assert calls["finalize"][0] == "done"
    assert calls["finalize"][1]["state"] == "ready"
    assert calls["finalize"][1]["flatlay"] is True


def test_generation_input_is_the_cutout_and_the_grid_is_untouched(monkeypatch):
    gemini = _FakeGemini(image=_gen_png())
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=gemini)
    _run(app, _job_dict())

    call = gemini.calls[0]
    assert call["model"] == "gemini-3.1-flash-image", "settings.model_image_light"
    assert (call["image_size"], call["aspect_ratio"]) == (mf.IMAGE_SIZE, mf.ASPECT_RATIO)
    assert len(call["images"]) == 1, "레퍼런스 이미지 없음 — 스파이크 승자 구성"
    assert call["images"][0].mime == "image/png"
    assert call["images"][0].data == mc.flatten_on_bg(_cut_png()), "첫 누끼 합성본"

    # grid(=생성 입력 asset)은 누끼 합성본 그대로다 — 재렌더는 카드 1장에만 든다.
    baseline_app, baseline_r2, baseline_calls = _wire_flatlay(
        monkeypatch, gemini=_ExplodingGemini(), flag="off")
    _run(baseline_app, _job_dict())
    assert _grid_put(r2) == _grid_put(baseline_r2)
    assert calls["swap"][2] == baseline_calls["swap"][2]


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
        mc.flatten_on_bg(_cut_png())] * (len(captured["inputs"]) - 1), "나머지 칸 = 누끼본"
    # 썸네일도 여전히 재렌더본(512 카드 계약)이다.
    _k, thumb_bytes, _m = _thumb_put(r2)
    assert _close(_avg_color(thumb_bytes), GEN_COLOR)
    meta = {a["asset_id"]: a["metadata"] for a in calls["assets"]}
    grid_meta = meta[calls["swap"][2]]
    assert grid_meta["flatlayFront"] is True, "provenance — 생성이 뭘 봤는지 asset 이 말해야"
    assert grid_meta["flatlayModel"] == "gemini-3.1-flash-image"
    assert calls["finalize"][1]["flatlayGrid"] is True


def test_full_mode_falls_back_to_cutout_grid_when_render_fails(monkeypatch):
    """full 인데 재렌더 실패 → grid 는 기존 누끼 합성본 그대로(fail-open, #131 규율 유지)."""
    gemini = _FakeGemini(error=gemini_image.GeminiError("boom"))
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=gemini, flag="full")
    _run(app, _job_dict())

    baseline_app, baseline_r2, _bc = _wire_flatlay(
        monkeypatch, gemini=_ExplodingGemini(), flag="off")
    _run(baseline_app, _job_dict())
    assert _grid_put(r2) == _grid_put(baseline_r2), "실패 시 grid 는 누끼본과 동일"
    meta = {a["asset_id"]: a["metadata"] for a in calls["assets"]}
    assert "flatlayFront" not in meta[calls["swap"][2]]
    assert calls["finalize"][1]["flatlay"] is False
    assert calls["finalize"][1]["flatlayGrid"] is False


def test_on_mode_grid_contract_is_byte_identical_to_131(monkeypatch):
    """on 은 #131 계약 그대로 — full 이 추가돼도 grid 는 한 바이트도 안 바뀐다."""
    gemini = _FakeGemini(image=_gen_png())
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=gemini, flag="on")
    _run(app, _job_dict())
    baseline_app, baseline_r2, _bc = _wire_flatlay(
        monkeypatch, gemini=_ExplodingGemini(), flag="off")
    _run(baseline_app, _job_dict())
    assert _grid_put(r2) == _grid_put(baseline_r2)
    assert calls["finalize"][1]["flatlayGrid"] is False


def test_prompt_noun_follows_the_items_clothing_type(monkeypatch):
    bottom = _FakeGemini(image=_gen_png())
    app, _r2, calls = _wire_flatlay(monkeypatch, gemini=bottom, clothing_type="bottom")
    _run(app, _job_dict())
    assert calls["meta_lookups"] == [("custom_x", "u1", "p1")]
    assert bottom.calls[0]["prompt"] == mf.build_prompt("bottom")
    assert "pair of pants" in bottom.calls[0]["prompt"]

    top = _FakeGemini(image=_gen_png())
    app2, _r2b, _c2 = _wire_flatlay(monkeypatch, gemini=top, clothing_type="top")
    _run(app2, _job_dict())
    assert top.calls[0]["prompt"] == mf.build_prompt("top")
    assert "pair of pants" not in top.calls[0]["prompt"]

    # 공통 몸통(연출 지시)은 명사와 무관하게 동일 — 정체성 절(2026-08-15)은 명사가 들어가므로
    # "no distortion." 까지만 비교한다.
    tail, end = "Direct overhead top-down view", "no distortion."
    bp, tp = bottom.calls[0]["prompt"], top.calls[0]["prompt"]
    assert (bp[bp.index(tail):bp.index(end) + len(end)]
            == tp[tp.index(tail):tp.index(end) + len(end)])
    assert "identity is fixed" in bp and "identity is fixed" in tp


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

def _cutout_thumb_id():
    return mc.derived_asset_id(
        role="thumb", matching_item_id="custom_x",
        source_hash=mc.source_fingerprint(
            ["h" + k for k in _job_dict()["payload"]["sourceKeys"]]))


@pytest.mark.parametrize("failure", [
    gemini_image.GeminiError("Gemini 500: boom"),
    gemini_image.GeminiError("GEMINI_API_KEY 미설정"),
    asyncio.TimeoutError(),
    RuntimeError("unexpected"),
])
def test_generation_failure_keeps_the_cutout_thumbnail(monkeypatch, failure):
    gemini = _FakeGemini(error=failure)
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=gemini)
    _run(app, _job_dict())

    assert calls["swap"][1] == _cutout_thumb_id(), "누끼 썸네일이 남는다"
    assert calls["finalize"][0] == "done", "잡은 반드시 종결된다"
    assert calls["finalize"][1]["state"] == "ready"
    assert calls["finalize"][1].get("flatlay") is False
    assert _close(_avg_color(_thumb_put(r2)[1]), (10, 120, 200)), "누끼 컷 색"


@pytest.mark.parametrize("bad", [b"", b"not-an-image", _gen_png()[:40]])
def test_corrupt_or_empty_generation_keeps_the_cutout_thumbnail(monkeypatch, bad):
    gemini = _FakeGemini(image=bad)
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=gemini)
    _run(app, _job_dict())

    assert calls["swap"][1] == _cutout_thumb_id()
    assert calls["finalize"][0] == "done" and calls["finalize"][1]["state"] == "ready"
    assert _close(_avg_color(_thumb_put(r2)[1]), (10, 120, 200))


def test_missing_gemini_client_keeps_the_cutout_thumbnail(monkeypatch):
    app, _r2, calls = _wire_flatlay(monkeypatch, gemini=None)
    _run(app, _job_dict())

    assert calls["swap"][1] == _cutout_thumb_id()
    assert calls["finalize"][0] == "done" and calls["finalize"][1]["state"] == "ready"


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
    assert recorded[0]["model"] == "gemini-3.1-flash-image"
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


def test_model_routing_failure_does_not_discard_a_successful_cutout(monkeypatch):
    """라우팅 설정이 비어도 누끼 결과는 살아남는다 (리뷰 I1).

    resolve_model 이 fail-open try 밖에 있으면 그 예외가 워커의 광의 except 로 올라가
    성공한 컷아웃까지 폐기된다 — R2 put 0, 스왑 0, 잡은 failed. 재렌더 실패는
    누끼 썸네일로 떨어지는 것이지 누끼 자체를 버리는 게 아니다.
    """
    gemini = _FakeGemini(image=_gen_png())
    app, r2, calls = _wire_flatlay(monkeypatch, gemini=gemini)
    app.state.settings.model_image_light = ""  # 라우팅 미설정

    _run(app, _job_dict())

    assert gemini.calls == [], "모델을 못 고르면 생성 호출 자체가 없다"
    assert calls["finalize"][0] == "done"
    assert calls["finalize"][1]["state"] == "ready", "누끼는 성공했다"
    assert calls["finalize"][1]["flatlay"] is False
    assert calls["swap"] is not None, "스왑이 일어나야 한다"
    assert calls["swap"][1] == mc.derived_asset_id(
        role="thumb", matching_item_id="custom_x",
        source_hash=_fingerprint()), "누끼 썸네일로 폴백"
    assert len(r2.puts) == 2, "썸네일 + grid 가 그대로 올라간다"


def test_both_thumbnail_candidates_stay_reclaimable_by_delete(monkeypatch):
    """재실행이 다른 분기를 타도 이전 썸네일이 삭제 경로에서 사라지지 않는다 (리뷰 I2).

    삭제 라우트는 현재 thumbnail_asset_id 와 grid metadata 의 sourceAssetIds 만 훑는다
    (routes.py:1379-1381). 썸네일 신원이 재렌더 성패로 갈리므로 두 후보를 모두
    실어 두지 않으면, 성공 → 실패(또는 그 반대) 재실행 뒤 한쪽이 영구 고아가 된다.
    """
    fp = _fingerprint()
    flat_id = mf.derived_asset_id(matching_item_id="custom_x", source_hash=fp)
    cut_id = mc.derived_asset_id(role="thumb", matching_item_id="custom_x", source_hash=fp)

    for gemini, live, orphan in ((_FakeGemini(image=_gen_png()), flat_id, cut_id),
                                 (_FakeGemini(error=gemini_image.GeminiError("boom")),
                                  cut_id, flat_id)):
        app, _r2, calls = _wire_flatlay(monkeypatch, gemini=gemini)
        _run(app, _job_dict())

        assert calls["swap"][1] == live
        grid_meta = {a["asset_id"]: a["metadata"] for a in calls["assets"]}[calls["swap"][2]]
        reachable = set(grid_meta["sourceAssetIds"]) | {calls["swap"][1], calls["swap"][2]}
        assert orphan in reachable, "다른 분기 썸네일도 회수 대상이어야 한다"
        assert live in reachable


def test_flag_off_does_not_widen_the_cleanup_list(monkeypatch):
    """플래그 off 면 정리 목록도 부모 PR 그대로다 — 존재하지 않는 id 를 싣지 않는다."""
    app, _r2, calls = _wire_flatlay(monkeypatch, gemini=_ExplodingGemini(), flag="off")
    _run(app, _job_dict())

    grid_meta = {a["asset_id"]: a["metadata"] for a in calls["assets"]}[calls["swap"][2]]
    fp = _fingerprint()
    assert mf.derived_asset_id(
        matching_item_id="custom_x", source_hash=fp) not in grid_meta["sourceAssetIds"]
