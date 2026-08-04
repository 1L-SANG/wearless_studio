"""Phase 1 — Generation Run + Output lineage (마네킹 경로 한정).

계약:
  · 플래그 off = runs·outputs·프롬프트 객체 전부 0. 기록기는 켜야만 존재한다.
  · shadow = **기록만**. 생성 결과·후보 선택·QC·크레딧에 개입하지 않고 기록 실패도 삼킨다.
  · 입력 스냅샷은 provider 에 실제로 나간 이미지 **전부**를 호출 순서대로 담는다.
  · 편집 계보는 부모 run 을 행에 직접 남긴다 — 최종 바이트 역참조에만 의존하지 않는다.
  · `generation_outputs.generation_run_id` = **최종 결과의 마지막 provider 조상**.
    deterministic 후처리가 바이트를 바꿔도 행은 남고 post_processed 가 True 가 된다.
  · DB·R2 어느 쪽이 죽어도 컷은 나가고, R2 에 고아 프롬프트가 남지 않는다.
  · provider_error 에는 예외 타입 + allowlist 코드만 — URL·응답 원문·프롬프트 금지.
"""

import asyncio
import contextlib
import hashlib
import inspect
import json
import threading
import types
import uuid

import cv2
import numpy as np
import pytest

from app import repo
from app.agents.gemini_image import GeminiError, InlineImage
from app.agents.product_reference import ProductReference
from app.services import generation_run as gr
from app.workers import mannequin_job as mj
from conftest import make_settings

SNAP_PROFILE = {"category": "top", "gender": "women", "source": "seller",
                "axes": {"fit": "slim"}, "version": 1}


def _png(bgr) -> bytes:
    ok, buf = cv2.imencode(".png", bgr)
    assert ok
    return buf.tobytes()


def _plain(v=235, size=(1264, 848)) -> bytes:
    return _png(np.full((size[0], size[1], 3), v, np.uint8))


class _Conn:
    async def commit(self):
        return None


class _Pool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()

        return _cm()


class _Recorder:
    """repo 기록 함수 4종의 fake — 실제로 어떤 값이 DB 로 갔는지 그대로 붙잡는다."""

    def __init__(self):
        self.runs: list[dict] = []
        self.updates: list[dict] = []
        self.prompt_keys: list[dict] = []

    def install(self, monkeypatch, *, insert=None, update_key=None):
        outer_insert, outer_key = insert, update_key

        async def insert_run(conn, **kw):
            if outer_insert:
                await outer_insert(conn, **kw)
            self.runs.append(kw)

        async def update_run(conn, **kw):
            self.updates.append(kw)

        async def set_prompt_key(conn, **kw):
            if outer_key:
                await outer_key(conn, **kw)
            self.prompt_keys.append(kw)

        monkeypatch.setattr(repo, "insert_generation_run", insert_run)
        monkeypatch.setattr(repo, "update_generation_run", update_run)
        monkeypatch.setattr(repo, "update_generation_run_prompt_key", set_prompt_key)


def _run_job(monkeypatch, *, settings_kw=None, gemini_error=False, repo_overrides=None,
             per_candidate_image=None, hybrid_summary=None):
    """워커 1회 실행 → (rec, r2_puts, r2_deletes, calls)."""
    rec = _Recorder()
    r2_puts: list[tuple] = []
    r2_deletes: list[str] = []
    calls = {"success": [], "failure": []}
    cut_png = _plain()
    source_png = _plain(200)
    state = {"n": 0}

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size,
                                         temperature=None, aspect_ratio=None):
            if gemini_error:
                raise GeminiError("Gemini 500: internal error at https://x/y?key=SECRET")
            img = cut_png
            if per_candidate_image is not None:
                img = per_candidate_image(state["n"])
            state["n"] += 1
            return types.SimpleNamespace(image=img, mime="image/png",
                                         latency_ms=1, usage={"totalTokenCount": 42})

    class _R2:
        def get_bytes(self, key):
            return {"bw.png": _plain(240), "front.png": source_png,
                    "back.png": source_png}[key]

        def put_bytes(self, key, data, mime, cache=None):
            r2_puts.append((key, data, mime))

        def delete(self, key):
            r2_deletes.append(key)

    async def get_product(conn, project_id):
        return {"name": "무지 셔츠", "clothing_type": "top",
                "colors": [{"isBase": True, "images": [
                    {"id": "front", "slot": "Front"}, {"id": "back", "slot": "Back"}]}]}

    async def get_analysis(conn, project_id):
        return {"targetGenders": ["women"], "fit": "regular"}

    async def get_asset_for_user(conn, user_id, asset_id):
        return {
            "bw": {"id": "bw", "mime_type": "image/png", "r2_key": "bw.png"},
            "front": {"id": "front", "mime_type": "image/png", "r2_key": "front.png"},
            "back": {"id": "back", "mime_type": "image/png", "r2_key": "back.png"},
        }.get(asset_id)

    async def get_matching_item_asset(conn, item_id):
        return None

    async def finalize_success(conn, **kw):
        calls["success"].append(kw)
        return {"cuts": kw["candidates"], "available": 7}

    async def finalize_failure(conn, **kw):
        calls["failure"].append(kw)
        return True

    async def fake_emit(pool, job_id, event_type, payload):
        return None

    for name, fn in (("get_product", get_product), ("get_analysis", get_analysis),
                     ("get_asset_for_user", get_asset_for_user),
                     ("get_matching_item_asset", get_matching_item_asset),
                     ("finalize_mannequin_success", finalize_success),
                     ("finalize_mannequin_failure", finalize_failure)):
        monkeypatch.setattr(repo, name, (repo_overrides or {}).get(name, fn))
    rec.install(monkeypatch, insert=(repo_overrides or {}).get("insert_generation_run"),
                update_key=(repo_overrides or {}).get("update_generation_run_prompt_key"))
    monkeypatch.setattr(mj, "_emit", fake_emit)
    if hybrid_summary is not None:
        orig = mj._save_cut

        async def save_with_hybrid(*, qc_scores, **kw):
            qs = {**(qc_scores or {}), "hybridComposite": hybrid_summary}
            return await orig(qc_scores=qs, **kw)
        monkeypatch.setattr(mj, "_save_cut", save_with_hybrid)

    settings = make_settings(**{
        "base_mannequin_women_asset_id": "bw", "r2_bucket": "bucket",
        "mannequin_hybrid_composite": "off", "mannequin_max_attempts": 1,
        **(settings_kw or {})})
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=_R2(), gemini=_Gemini()))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "u1:t",
           "credits_reserved": 2, "payload": {}}
    asyncio.run(mj.run_mannequin_job(app, job))
    return rec, r2_puts, r2_deletes, calls


SHADOW = {"generation_run_log": "shadow"}


def _prompt_objects(r2_puts):
    return {k: d for k, d, _m in r2_puts if k.endswith(".txt")}


# ── 플래그 ────────────────────────────────────────────────────────────────────

def test_flag_off_writes_nothing_at_all(monkeypatch):
    """off = runs·outputs·프롬프트 객체 전부 0. 기존 동작과 구분 불가여야 한다."""
    rec, puts, deletes, calls = _run_job(monkeypatch)
    assert rec.runs == [] and rec.updates == [] and rec.prompt_keys == []
    assert _prompt_objects(puts) == {} and deletes == []
    assert calls["success"], "생성 자체는 정상 완료해야 한다"
    for cand in calls["success"][0]["candidates"]:
        assert cand.get("generation_lineage") is None


def test_shadow_records_one_row_per_provider_call(monkeypatch):
    rec, _p, _d, _c = _run_job(monkeypatch, settings_kw=SHADOW)
    assert rec.runs, "shadow 인데 provider 호출 기록이 없다"
    assert all(r["kind"] == "mannequin_generate" for r in rec.runs)
    assert all(r["job_id"] == "j1" and r["project_id"] == "p1" and r["user_id"] == "u1"
               for r in rec.runs)
    assert len(rec.updates) == len(rec.runs)
    assert all(u["status"] == "succeeded" for u in rec.updates)
    assert all(isinstance(u["latency_ms"], int) for u in rec.updates)
    assert all(u["usage"] == {"totalTokenCount": 42} for u in rec.updates)


def test_shadow_does_not_change_the_shipped_cut_or_credits(monkeypatch):
    """기록 유무가 산출물·크레딧을 바꾸면 그건 관측기가 아니다."""
    _r, puts_off, _d, calls_off = _run_job(monkeypatch)
    _r2, puts_on, _d2, calls_on = _run_job(monkeypatch, settings_kw=SHADOW)
    img_off = [d for k, d, _m in puts_off if not k.endswith(".txt")]
    img_on = [d for k, d, _m in puts_on if not k.endswith(".txt")]
    assert img_off == img_on
    a, b = calls_off["success"][0], calls_on["success"][0]
    assert (a["charge"], a["reserved"]) == (b["charge"], b["reserved"])
    drop = ("asset_id", "key", "generation_lineage")
    assert [{k: v for k, v in c.items() if k not in drop} for c in a["candidates"]] == \
           [{k: v for k, v in c.items() if k not in drop} for c in b["candidates"]]


# ── 입력 스냅샷: provider 에 나간 이미지 전부 ────────────────────────────────

def _gen_row(rec):
    rows = [r for r in rec.runs if r["kind"] == "mannequin_generate"]
    assert rows, "생성 호출 기록이 없다"
    return rows[0]


def test_fresh_generate_snapshots_every_provider_input_in_order(monkeypatch):
    rec, _p, _d, _c = _run_job(monkeypatch, settings_kw=SHADOW)
    assets = _gen_row(rec)["input_assets"]
    assert assets, "입력 스냅샷이 비었다"
    assert [a["position"] for a in assets] == list(range(len(assets)))
    assert assets[0]["role"] == "base_mannequin", "베이스 마네킹이 첫 장이어야 한다"
    products = [a for a in assets if a["role"] == "product_reference"]
    assert products, "상품 참조가 스냅샷에 없다"
    assert {a["slot"] for a in products} == {"Front", "Back"}
    assert all(a["assetId"] for a in products)
    for a in assets:
        assert isinstance(a["sha256"], str) and len(a["sha256"]) == 64
        assert a["role"] in gr.INPUT_ROLES


def test_snapshot_order_matches_the_bytes_actually_sent(monkeypatch):
    """스냅샷 sha 순서 = gemini 에 넘어간 images 순서. 어긋나면 매니페스트 계약이 깨진다."""
    seen = {}

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size,
                                         temperature=None, aspect_ratio=None):
            seen["shas"] = [hashlib.sha256(i.data).hexdigest() for i in images]
            return types.SimpleNamespace(image=_plain(), mime="image/png",
                                         latency_ms=1, usage=None)

    rec = _Recorder()
    rec.install(monkeypatch)
    monkeypatch.setattr(mj, "_emit", lambda *a, **k: _noop())
    settings = make_settings(r2_bucket="bucket", generation_run_log="shadow")
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=_FakeR2(), gemini=_Gemini()))
    refs = [ProductReference(slot="Front", asset_id="a1",
                             image=InlineImage("image/png", _plain(10))),
            ProductReference(slot="Detail", asset_id="a2",
                             image=InlineImage("image/png", _plain(20)))]
    runlog = gr.RunLogger(pool=_Pool(), r2=_FakeR2(), job_id="j1", project_id="p1",
                          user_id="u1", enabled=True)
    asyncio.run(mj._run_candidate(
        app=app, job={"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "t"},
        candidate="A", base_fit="regular", base_gender="women",
        base_img=InlineImage("image/png", _plain(30)), prod_refs=refs,
        match_img=InlineImage("image/png", _plain(40)), product_count=1,
        template="T ${baseGender} ${clothingType}.\n${imageManifest}",
        product={"name": "티"}, analysis={}, clothing_type="top",
        image_manifest="1. base", fit_profile=SNAP_PROFILE,
        ref_imgs=[InlineImage("image/png", _plain(50))], runlog=runlog))
    row = _gen_row(rec)
    assert [a["sha256"] for a in row["input_assets"]] == seen["shas"]
    roles = [a["role"] for a in row["input_assets"]]
    assert roles == ["base_mannequin", "product_reference", "product_reference",
                     "matching_garment"]


def test_adjust_edit_snapshots_the_parent_cut_first(monkeypatch):
    """조정 편집의 image 1 = 이전 채택 컷. 무엇을 편집했는지 행만으로 복원돼야 한다."""
    rec = _Recorder()
    rec.install(monkeypatch)
    monkeypatch.setattr(mj, "_emit", lambda *a, **k: _noop())
    parent = InlineImage("image/png", _plain(77))

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size,
                                         temperature=None, aspect_ratio=None):
            return types.SimpleNamespace(image=_plain(88), mime="image/png",
                                         latency_ms=1, usage=None)

    settings = make_settings(r2_bucket="bucket", generation_run_log="shadow")
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=_FakeR2(), gemini=_Gemini()))
    runlog = gr.RunLogger(pool=_Pool(), r2=_FakeR2(), job_id="j1", project_id="p1",
                          user_id="u1", enabled=True)
    asyncio.run(mj._run_candidate(
        app=app, job={"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "t"},
        candidate="A", base_fit="regular", base_gender="women",
        base_img=InlineImage("image/png", _plain(30)),
        prod_refs=[ProductReference(slot="Front", asset_id="a1",
                                    image=InlineImage("image/png", _plain(10)))],
        match_img=None, product_count=1,
        template="T ${baseGender} ${clothingType}.\n${imageManifest}",
        product={"name": "티"}, analysis={}, clothing_type="top",
        image_manifest="1. base", fit_profile=SNAP_PROFILE,
        generation_path="edit", parent_cut_img=parent,
        adjust_directives="MAIN PRODUCT: make it shorter", runlog=runlog))
    rows = [r for r in rec.runs if r["kind"] == "mannequin_adjust_edit"]
    assert rows, "조정 편집이 mannequin_adjust_edit 로 기록되지 않았다"
    snap = rows[0]["input_assets"]
    assert snap[0]["role"] == "parent_cut"
    assert snap[0]["sha256"] == hashlib.sha256(parent.data).hexdigest()
    assert rows[0]["input_image_sha256"] == snap[0]["sha256"]


@pytest.mark.parametrize("pass_name,kind,setting", [
    ("bust", "mannequin_bust_edit", {"mannequin_bust_pass": "on"}),
    ("untuck", "mannequin_untuck_edit", {"mannequin_untuck_pass": "on"}),
])
def test_edit_passes_snapshot_their_edit_source_and_parent(monkeypatch, pass_name, kind,
                                                           setting):
    """편집 패스는 '무엇을 편집했는가'(edit_source)와 그 이미지를 만든 run 을 남긴다."""
    rec = _Recorder()
    rec.install(monkeypatch)
    monkeypatch.setattr(mj, "_emit", lambda *a, **k: _noop())
    src = _plain(60)
    out = _plain(61)

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size,
                                         temperature=None, aspect_ratio=None):
            return types.SimpleNamespace(image=out, mime="image/png", latency_ms=1,
                                         usage=None)

    settings = make_settings(r2_bucket="bucket", generation_run_log="shadow", **setting)
    runlog = gr.RunLogger(pool=_Pool(), r2=_FakeR2(), job_id="j1", project_id="p1",
                          user_id="u1", enabled=True)
    # 부모: 이 바이트를 만든 생성 호출이 이미 기록돼 있다고 둔다
    parent_id = asyncio.run(runlog.begin(kind="mannequin_generate", prompt="p",
                                         candidate="A"))
    asyncio.run(runlog.finish(parent_id, image=src, candidate="A"))
    res = types.SimpleNamespace(image=src, mime="image/png")
    fn = mj._apply_bust_pass if pass_name == "bust" else mj._apply_untuck_pass
    kw = dict(pool=_Pool(), gemini=_Gemini(), s=settings, job_id="j1", candidate="A",
              attempt=1, res=res, calls_spent=0, clothing_type="top", runlog=runlog)
    if pass_name == "bust":
        kw["base_gender"] = "women"
    else:
        kw["match_img"] = InlineImage("image/png", _plain(5))
    asyncio.run(fn(**kw))
    rows = [r for r in rec.runs if r["kind"] == kind]
    assert rows, f"{pass_name} 패스가 기록되지 않았다"
    snap = rows[0]["input_assets"]
    assert snap and snap[0]["role"] == "edit_source"
    assert snap[0]["sha256"] == hashlib.sha256(src).hexdigest()
    assert rows[0]["input_image_sha256"] == hashlib.sha256(src).hexdigest()
    assert rows[0]["parent_generation_run_id"] == parent_id, "부모 run 이 연결되지 않았다"


def test_parent_chain_is_recorded_even_when_the_edit_is_reverted(monkeypatch):
    _Recorder().install(monkeypatch)
    """회귀로 편집이 되돌려져도 편집 run 의 부모는 그대로다 — 계보는 채택 여부와 무관하다."""
    runlog = gr.RunLogger(pool=_Pool(), r2=None, job_id="j", project_id="p",
                          user_id="u", enabled=True)
    gen = asyncio.run(runlog.begin(kind="mannequin_generate", prompt="g", candidate="A"))
    asyncio.run(runlog.finish(gen, image=b"first", candidate="A"))
    edit = asyncio.run(runlog.begin(kind="mannequin_axis_edit", prompt="e", candidate="A",
                                    input_image=b"first"))
    asyncio.run(runlog.finish(edit, image=b"edited", candidate="A"))
    # 채택은 되돌아간 원본 — 그래도 편집 run 의 부모는 생성 run 이다
    assert runlog.output_lineage(b"first", "A")["generation_run_id"] == gen
    assert runlog.output_lineage(b"edited", "A")["generation_run_id"] == edit


def test_same_bytes_from_different_candidates_do_not_collide(monkeypatch):
    _Recorder().install(monkeypatch)
    """후보 A/B 가 같은 바이트를 내도 서로의 run 에 붙으면 안 된다."""
    runlog = gr.RunLogger(pool=_Pool(), r2=None, job_id="j", project_id="p",
                          user_id="u", enabled=True)
    a = asyncio.run(runlog.begin(kind="mannequin_generate", prompt="g", candidate="A"))
    asyncio.run(runlog.finish(a, image=b"same", candidate="A"))
    b = asyncio.run(runlog.begin(kind="mannequin_generate", prompt="g", candidate="B"))
    asyncio.run(runlog.finish(b, image=b"same", candidate="B"))
    assert a != b
    assert runlog.run_id_for_image(b"same", "A") == a
    assert runlog.run_id_for_image(b"same", "B") == b


def test_worker_links_each_candidate_to_its_own_run(monkeypatch):
    rec, _p, _d, calls = _run_job(monkeypatch, settings_kw=SHADOW)
    cands = calls["success"][0]["candidates"]
    ids = [c["generation_lineage"]["generation_run_id"] for c in cands]
    assert all(i is not None for i in ids)
    if len(cands) > 1:
        assert len(set(ids)) == len(ids), "후보들이 같은 run 에 묶였다"


# ── 산출물 계보: 후처리가 있어도 행이 남는다 ─────────────────────────────────

def test_output_lineage_is_exact_when_no_post_processing(monkeypatch):
    rec, _p, _d, calls = _run_job(monkeypatch, settings_kw=SHADOW)
    cut_sha = hashlib.sha256(_plain()).hexdigest()
    for cand in calls["success"][0]["candidates"]:
        lin = cand["generation_lineage"]
        assert lin["post_processed"] is False
        assert lin["output_sha256"] == cut_sha
        assert lin["generation_run_id"] in {r["run_id"] for r in rec.runs}


def test_deterministic_post_processing_keeps_the_output_row(monkeypatch):
    """hybrid 가 바이트를 바꿔도 행은 남는다 — 단 조상은 **캡처된 carrier** 여야 한다."""
    _Recorder().install(monkeypatch)
    runlog = gr.RunLogger(pool=_Pool(), r2=None, job_id="j", project_id="p",
                          user_id="u", enabled=True)
    gen = asyncio.run(runlog.begin(kind="mannequin_generate", prompt="g", candidate="A"))
    asyncio.run(runlog.finish(gen, image=b"carrier", candidate="A"))
    carrier = runlog.run_id_for_image(b"carrier", "A")      # 후처리 직전 캡처
    lin = runlog.output_lineage(b"composited-bytes", "A", carrier_run_id=carrier)
    assert lin["generation_run_id"] == gen, "조상을 잃으면 output 행이 통째로 사라진다"
    assert lin["post_processed"] is True
    assert lin["output_sha256"] == hashlib.sha256(b"composited-bytes").hexdigest()


def test_post_processing_without_a_captured_carrier_leaves_lineage_null(monkeypatch):
    """carrier 를 못 잡았으면 **추정하지 않는다** — 틀린 계보보다 빈 계보가 낫다."""
    _Recorder().install(monkeypatch)
    runlog = gr.RunLogger(pool=_Pool(), r2=None, job_id="j", project_id="p",
                          user_id="u", enabled=True)
    gen = asyncio.run(runlog.begin(kind="mannequin_generate", prompt="g", candidate="A"))
    asyncio.run(runlog.finish(gen, image=b"carrier", candidate="A"))
    lin = runlog.output_lineage(b"composited", "A")         # carrier 미전달
    assert lin["generation_run_id"] is None
    assert lin["post_processed"] is True


def test_rollback_then_hybrid_points_at_the_restored_run(monkeypatch):
    """G → E → (회귀로) G 복구 → Hybrid. 조상은 **G**다. 폐기된 E 가 아니다."""
    _Recorder().install(monkeypatch)
    runlog = gr.RunLogger(pool=_Pool(), r2=None, job_id="j", project_id="p",
                          user_id="u", enabled=True)
    g = asyncio.run(runlog.begin(kind="mannequin_generate", prompt="g", candidate="A"))
    asyncio.run(runlog.finish(g, image=b"G", candidate="A"))
    e = asyncio.run(runlog.begin(kind="mannequin_axis_edit", prompt="e", candidate="A",
                                 input_image=b"G"))
    asyncio.run(runlog.finish(e, image=b"E", candidate="A"))
    # 회귀 판정으로 G 로 되돌아간 상태에서 후처리 직전 캡처
    carrier = runlog.run_id_for_image(b"G", "A")
    assert carrier == g
    lin = runlog.output_lineage(b"H", "A", carrier_run_id=carrier)
    assert lin["generation_run_id"] == g
    assert lin["generation_run_id"] != e, "폐기된 편집이 조상으로 기록됐다"
    assert runlog.last_provider_run("A") == e, "전제: 마지막 성공 run 은 폐기된 E 다"


def test_earlier_candidate_selected_then_hybrid_points_at_that_run(monkeypatch):
    """G1, G2 생성 후 G1 채택 → Hybrid. 조상은 G1 — 마지막 성공 run(G2)이 아니다."""
    _Recorder().install(monkeypatch)
    runlog = gr.RunLogger(pool=_Pool(), r2=None, job_id="j", project_id="p",
                          user_id="u", enabled=True)
    g1 = asyncio.run(runlog.begin(kind="mannequin_generate", prompt="g", candidate="A"))
    asyncio.run(runlog.finish(g1, image=b"G1", candidate="A"))
    g2 = asyncio.run(runlog.begin(kind="mannequin_generate", prompt="g", candidate="A"))
    asyncio.run(runlog.finish(g2, image=b"G2", candidate="A"))
    carrier = runlog.run_id_for_image(b"G1", "A")
    lin = runlog.output_lineage(b"H", "A", carrier_run_id=carrier)
    assert lin["generation_run_id"] == g1
    assert runlog.last_provider_run("A") == g2, "전제: 마지막 성공 run 은 미채택 G2 다"


def test_worker_records_hybrid_transformation_metadata(monkeypatch):
    hybrid = {"applied": True, "needsReview": False, "pipelineVersion": "hc-v1"}
    _rec, _p, _d, calls = _run_job(monkeypatch, settings_kw=SHADOW, hybrid_summary=hybrid)
    lin = calls["success"][0]["candidates"][0]["generation_lineage"]
    assert lin["transformation"]["hybridComposite"] == {
        "applied": True, "needsReview": False, "pipelineVersion": "hc-v1"}


# ── 프롬프트: DB 에 전문 없음 / R2 와 해시 일치 / 고아 없음 ────────────────────

def test_prompt_body_never_reaches_the_database(monkeypatch):
    rec, puts, _d, _c = _run_job(monkeypatch, settings_kw=SHADOW)
    bodies = [d.decode("utf-8") for d in _prompt_objects(puts).values()]
    assert bodies, "프롬프트가 R2 에 올라가지 않았다"
    body = bodies[0]
    assert len(body) > 50, "프롬프트가 비정상적으로 짧다 — 전제 확인"
    for row in rec.runs:
        flat = repr(row)
        assert body not in flat and body[:80] not in flat


def test_prompt_sha256_matches_the_r2_object_bytes(monkeypatch):
    rec, puts, _d, _c = _run_job(monkeypatch, settings_kw=SHADOW)
    objects = _prompt_objects(puts)
    keyed = {k["run_id"]: k["key"] for k in rec.prompt_keys}
    assert keyed, "프롬프트 키가 행에 채워지지 않았다"
    for row in rec.runs:
        key = keyed[row["run_id"]]
        assert key in objects
        assert row["prompt_sha256"] == hashlib.sha256(objects[key]).hexdigest()
        assert key.startswith("users/u1/projects/p1/genruns/j1/")


def test_row_is_created_before_the_prompt_is_uploaded(monkeypatch):
    """insert 가 먼저다 — 반대면 insert 실패 때마다 R2 에 고아 프롬프트가 쌓인다."""
    order: list[str] = []

    async def insert(conn, **kw):
        order.append("insert")

    rec = _Recorder()
    rec.install(monkeypatch, insert=insert)

    class R2:
        def put_bytes(self, key, data, mime, cache=None):
            order.append("upload")

        def delete(self, key):
            order.append("delete")

    logger = gr.RunLogger(pool=_Pool(), r2=R2(), job_id="j", project_id="p",
                          user_id="u", enabled=True)
    asyncio.run(logger.begin(kind="mannequin_generate", prompt="hello"))
    assert order[:2] == ["insert", "upload"]


def test_db_insert_failure_leaves_no_orphan_prompt_object(monkeypatch):
    """migration 미적용 상태에서 shadow 를 켜도 R2 에 프롬프트가 쌓이면 안 된다."""
    uploads: list[str] = []

    async def boom(conn, **kw):
        raise RuntimeError("relation does not exist")

    monkeypatch.setattr(repo, "insert_generation_run", boom)

    class R2:
        def put_bytes(self, key, data, mime, cache=None):
            uploads.append(key)

        def delete(self, key):
            uploads.remove(key)

    logger = gr.RunLogger(pool=_Pool(), r2=R2(), job_id="j", project_id="p",
                          user_id="u", enabled=True)
    assert asyncio.run(logger.begin(kind="mannequin_generate", prompt="x")) is None
    assert uploads == [], "insert 실패인데 R2 에 객체가 남았다"


def test_prompt_key_update_failure_deletes_the_uploaded_object(monkeypatch):
    """행이 키를 모르는 객체 = 고아. 키 갱신이 실패하면 지운다."""
    live: list[str] = []

    async def key_boom(conn, **kw):
        raise RuntimeError("db down")

    rec = _Recorder()
    rec.install(monkeypatch, update_key=key_boom)

    class R2:
        def put_bytes(self, key, data, mime, cache=None):
            live.append(key)

        def delete(self, key):
            live.remove(key)

    logger = gr.RunLogger(pool=_Pool(), r2=R2(), job_id="j", project_id="p",
                          user_id="u", enabled=True)
    run_id = asyncio.run(logger.begin(kind="mannequin_generate", prompt="x"))
    assert run_id and live == []


def test_prompt_upload_failure_keeps_the_row(monkeypatch):
    """R2 가 죽어도 행과 sha256 은 남고 생성은 계속된다."""
    rec = _Recorder()
    rec.install(monkeypatch)

    class BoomR2:
        def put_bytes(self, key, data, mime, cache=None):
            raise RuntimeError("r2 down")

    logger = gr.RunLogger(pool=_Pool(), r2=BoomR2(), job_id="j", project_id="p",
                          user_id="u", enabled=True)
    run_id = asyncio.run(logger.begin(kind="mannequin_generate", prompt="hello"))
    assert run_id and rec.runs and rec.runs[0]["prompt_r2_key"] is None
    assert rec.runs[0]["prompt_sha256"] == hashlib.sha256(b"hello").hexdigest()


# ── 입력/설정 스냅샷 순수 함수 ────────────────────────────────────────────────

def test_input_snapshot_hashes_the_bytes_that_actually_went_out():
    entries = [("product_reference", InlineImage("image/png", b"abc"), "a1", "Front"),
               ("style_reference", b"raw", None, None)]
    out = gr.input_snapshot(entries)
    assert out[0] == {"role": "product_reference", "assetId": "a1", "outputId": None,
                      "slot": "Front", "sha256": hashlib.sha256(b"abc").hexdigest(),
                      "position": 0}
    assert out[1]["sha256"] == hashlib.sha256(b"raw").hexdigest()
    assert out[1]["position"] == 1


def test_input_snapshot_rejects_unknown_roles():
    out = gr.input_snapshot([("mystery", b"x", None, None)])
    assert out[0]["role"] == "unknown"


def test_settings_snapshot_contains_no_secrets():
    s = make_settings()
    snap = gr.settings_snapshot(s)
    assert snap, "allowlist 스냅샷이 비어 있다"
    banned = ("key", "secret", "token", "password", "credential", "url", "dsn")
    for name, value in snap.items():
        assert not any(b in name.lower() for b in banned), f"의심 필드 유출: {name}"
        assert isinstance(value, (str, int, float, bool)) or value is None
    for attr in dir(s):
        if any(b in attr.lower() for b in banned):
            v = getattr(s, attr, None)
            if isinstance(v, str) and len(v) >= 8:
                assert v not in snap.values(), f"시크릿 값 유출: {attr}"


@pytest.mark.parametrize("name", gr.SETTINGS_ALLOWLIST)
def test_allowlisted_setting_exists_on_settings(name):
    assert hasattr(make_settings(), name), f"Settings 에 없는 필드: {name}"


# ── provider 실패: 타입 + allowlist 코드만 ────────────────────────────────────

def test_provider_error_marks_the_run_failed(monkeypatch):
    rec, _p, _d, calls = _run_job(monkeypatch, settings_kw=SHADOW, gemini_error=True)
    assert rec.runs, "호출 직전 기록이 없으면 실패 원인을 재현할 수 없다"
    assert rec.updates and all(u["status"] == "failed" for u in rec.updates)
    assert all(u["provider_error"] == "GeminiError:http_500" for u in rec.updates)
    assert calls["failure"], "provider 실패 잡은 실패로 종결돼야 한다"


def test_provider_error_never_carries_urls_tokens_or_response_text(monkeypatch):
    rec, _p, _d, _c = _run_job(monkeypatch, settings_kw=SHADOW, gemini_error=True)
    for u in rec.updates:
        pe = u["provider_error"]
        assert "http" not in pe.lower().replace("http_", "")  # http_500 토큰만 허용
        assert "://" not in pe and "SECRET" not in pe and "key=" not in pe
        assert "internal error" not in pe


@pytest.mark.parametrize("exc,expected", [
    (GeminiError("GEMINI_API_KEY 미설정"), "GeminiError:api_key_missing"),
    (GeminiError("Gemini 429: rate"), "GeminiError:http_429"),
    (GeminiError("Gemini 418: teapot"), "GeminiError:http_other"),
    (GeminiError("응답에 이미지 없음. 텍스트: blocked"), "GeminiError:no_image_in_response"),
    (GeminiError("Gemini 요청 실패: https://host/path?key=abc"), "GeminiError:request_failed"),
    (RuntimeError("something odd"), "RuntimeError:unknown"),
])
def test_sanitize_provider_error_maps_to_allowlisted_codes(exc, expected):
    out = gr.sanitize_provider_error(exc)
    assert out == expected
    assert out.split(":", 1)[1] in gr.PROVIDER_ERROR_CODES
    assert str(exc) not in out


def test_sanitize_drops_the_prompt_and_response_body():
    exc = GeminiError("Gemini 500: " + "PROMPT-LEAK " * 40)
    out = gr.sanitize_provider_error(exc)
    assert "PROMPT-LEAK" not in out and len(out) < 40


# ── 기록 실패가 생성을 죽이지 않는다 ──────────────────────────────────────────

def test_recorder_failures_never_break_generation(monkeypatch):
    async def boom(conn, **kw):
        raise RuntimeError("db down")

    _rec, _p, _d, calls = _run_job(
        monkeypatch, settings_kw=SHADOW,
        repo_overrides={"insert_generation_run": boom})
    assert calls["success"], "기록 실패가 생성을 죽였다"
    assert calls["success"][0]["charge"] > 0, "크레딧 확정 경로가 영향받았다"
    for cand in calls["success"][0]["candidates"]:
        assert cand.get("generation_lineage") is None


def test_disabled_logger_is_inert(monkeypatch):
    _Recorder().install(monkeypatch)
    logger = gr.RunLogger(pool=_Pool(), r2=None, job_id="j", project_id="p",
                          user_id="u", enabled=False)
    assert asyncio.run(logger.begin(kind="mannequin_generate", prompt="x")) is None
    assert logger.run_id_for_image(b"x") is None
    assert logger.last_provider_run("A") is None


# ── finalize: 같은 tx 안에서 cut ↔ run 연결 ───────────────────────────────────

class _FakeCursor:
    def __init__(self, sink, fail_outputs=False):
        self.sink = sink
        self.fail_outputs = fail_outputs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.sink.append((flat, params))
        self._last = sql
        if self.fail_outputs and flat.startswith("insert into generation_outputs"):
            from psycopg import errors
            raise errors.UndefinedTable("relation does not exist")

    async def fetchone(self):
        low = self._last.lower()
        if "for update" in low:
            return {"id": "job-1"}
        if "max(version)" in low:
            return {"v": 3}
        if "returning id" in low:
            return {"id": "cut-uuid-1"}
        return None


class _FakeConn:
    def __init__(self, sink, fail_outputs=False):
        self.sink = sink
        self.fail_outputs = fail_outputs

    def cursor(self):
        return _FakeCursor(self.sink, self.fail_outputs)


def _finalize(monkeypatch, candidate_extra: dict, *, fail_outputs=False, candidates=None):
    sink: list[tuple] = []

    async def consume(conn, **kw):
        return 10

    monkeypatch.setattr(repo, "_consume_buckets", consume)
    base = {"asset_id": "a-1", "bucket": "b", "key": "k", "mime": "image/png",
            "size": 1, "width": 2, "height": 3, "candidate": "A", "base_fit": "regular",
            "qc_scores": None}
    cands = candidates or [{**base, **candidate_extra}]
    out = asyncio.run(repo.finalize_mannequin_success(
        _FakeConn(sink, fail_outputs), job_id="j1", lease_token="t", user_id="u1",
        project_id="p1", candidates=cands, reserved=2, charge=2, metadata={}))
    return sink, out


LINEAGE = {"generation_run_id": "run-9", "output_sha256": "abc", "post_processed": False}


def test_finalize_links_output_to_run_and_cut(monkeypatch):
    sink, out = _finalize(monkeypatch, {"generation_lineage": LINEAGE})
    inserts = [(s, p) for s, p in sink if s.startswith("insert into generation_outputs")]
    assert len(inserts) == 1
    _sql, params = inserts[0]
    assert params[0] == "run-9"
    assert params[1] == "p1"
    assert params[2] == "cut-uuid-1"     # returning id 로 받은 실제 컷 uuid
    assert params[3] == "a-1"
    assert params[4] == "abc"            # 최종 output sha256
    assert params[5] is False            # post_processed
    assert "generation_lineage" not in out["cuts"][0]
    assert set(out["cuts"][0]) == {"id", "src", "candidate", "version", "baseFit",
                                   "fitAdjust", "lengthAdjust", "matchAdjust", "qcScores"}


def test_finalize_records_post_processed_outputs(monkeypatch):
    lin = {"generation_run_id": "run-9", "output_sha256": "zzz", "post_processed": True,
           "transformation": {"hybridComposite": {"applied": True}}}
    sink, _out = _finalize(monkeypatch, {"generation_lineage": lin})
    params = [p for s, p in sink if s.startswith("insert into generation_outputs")][0]
    assert params[5] is True and params[6] is not None


def test_finalize_writes_no_output_row_without_lineage(monkeypatch):
    sink, _out = _finalize(monkeypatch, {})
    assert not [s for s, _p in sink if s.startswith("insert into generation_outputs")]
    assert not [s for s, _p in sink if "savepoint" in s]


def test_output_insert_failure_still_ships_the_cut_and_releases_savepoint(monkeypatch,
                                                                         caplog):
    """migration 미적용에서도 컷은 나가고, 조용히 넘어가지 않고 warning 이 남는다."""
    with caplog.at_level("WARNING"):
        sink, out = _finalize(monkeypatch, {"generation_lineage": LINEAGE},
                              fail_outputs=True)
    order = [s for s, _p in sink]
    assert any(s.startswith("rollback to savepoint genout_insert") for s in order)
    assert any(s.startswith("release savepoint genout_insert") for s in order), \
        "rollback 뒤 release 가 없으면 savepoint 가 후보마다 쌓인다"
    assert out["cuts"], "컷 출고가 막혔다"
    assert any("generation_outputs insert failed" in r.message for r in caplog.records)
    msg = next(r.getMessage() for r in caplog.records
               if "generation_outputs insert failed" in r.message)
    assert "j1" in msg and "p1" in msg and "run-9" in msg and "UndefinedTable" in msg


def test_output_insert_is_savepointed_before_the_insert(monkeypatch):
    sink, _out = _finalize(monkeypatch, {"generation_lineage": LINEAGE})
    order = [s for s, _p in sink]
    i_save = next(i for i, s in enumerate(order) if s.startswith("savepoint genout_insert"))
    i_ins = next(i for i, s in enumerate(order)
                 if s.startswith("insert into generation_outputs"))
    assert i_save < i_ins, "insert 가 savepoint 밖에 있으면 tx 전체가 abort 된다"


def test_multiple_candidates_each_get_their_own_output_row(monkeypatch):
    base = {"bucket": "b", "key": "k", "mime": "image/png", "size": 1, "width": 2,
            "height": 3, "base_fit": "regular", "qc_scores": None}
    cands = [
        {**base, "asset_id": "a-1", "candidate": "A",
         "generation_lineage": {"generation_run_id": "run-A", "output_sha256": "s1",
                                "post_processed": False}},
        {**base, "asset_id": "a-2", "candidate": "B",
         "generation_lineage": {"generation_run_id": "run-B", "output_sha256": "s2",
                                "post_processed": False}},
    ]
    sink, _out = _finalize(monkeypatch, {}, candidates=cands)
    inserts = [p for s, p in sink if s.startswith("insert into generation_outputs")]
    assert [p[0] for p in inserts] == ["run-A", "run-B"]
    assert [p[3] for p in inserts] == ["a-1", "a-2"]


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

class _FakeR2:
    def put_bytes(self, key, data, mime, cache=None):
        return None

    def delete(self, key):
        return None


async def _noop():
    return None


# ── cross-job 부모 연결 (이전 job 의 채택 컷을 편집) ─────────────────────────

def _run_adjust(monkeypatch, parent_lineage):
    """조정 편집 1회 → (rec, 기록된 run 행들)."""
    rec = _Recorder()
    rec.install(monkeypatch)
    monkeypatch.setattr(mj, "_emit", lambda *a, **k: _noop())
    parent = InlineImage("image/png", _plain(77))

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size,
                                         temperature=None, aspect_ratio=None):
            return types.SimpleNamespace(image=_plain(88), mime="image/png",
                                         latency_ms=1, usage=None)

    settings = make_settings(r2_bucket="bucket", generation_run_log="shadow")
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=_FakeR2(), gemini=_Gemini()))
    runlog = gr.RunLogger(pool=_Pool(), r2=_FakeR2(), job_id="j2", project_id="p1",
                          user_id="u1", enabled=True)
    asyncio.run(mj._run_candidate(
        app=app, job={"id": "j2", "user_id": "u1", "project_id": "p1", "lease_token": "t"},
        candidate="A", base_fit="regular", base_gender="women",
        base_img=InlineImage("image/png", _plain(30)),
        prod_refs=[ProductReference(slot="Front", asset_id="a1",
                                    image=InlineImage("image/png", _plain(10)))],
        match_img=None, product_count=1,
        template="T ${baseGender} ${clothingType}.\n${imageManifest}",
        product={"name": "티"}, analysis={}, clothing_type="top",
        image_manifest="1. base", fit_profile=SNAP_PROFILE,
        generation_path="edit", parent_cut_img=parent,
        adjust_directives="MAIN PRODUCT: shorter",
        parent_lineage=parent_lineage, runlog=runlog))
    rows = [r for r in rec.runs if r["kind"] == "mannequin_adjust_edit"]
    assert rows, "조정 편집이 기록되지 않았다"
    return rows[0], hashlib.sha256(parent.data).hexdigest()


def test_adjust_edit_links_to_the_previous_jobs_output_run(monkeypatch):
    """이전 job 의 컷을 편집하면 그 컷의 run 이 부모다 — 이 job 안엔 그 호출이 없다."""
    row, parent_sha = _run_adjust(monkeypatch, {
        "asset_id": "asset-prev", "generation_output_id": "out-prev",
        "generation_run_id": "run-prev"})
    assert row["parent_generation_run_id"] == "run-prev"
    assert row["input_image_sha256"] == parent_sha
    snap = row["input_assets"][0]
    assert snap["role"] == "parent_cut"
    assert snap["assetId"] == "asset-prev"
    assert snap["outputId"] == "out-prev"
    assert snap["sha256"] == parent_sha


def test_legacy_parent_without_output_row_keeps_asset_and_hash(monkeypatch):
    """flag-off 시기에 만들어진 컷이 부모면 run 은 null — 그래도 asset·sha 는 남는다."""
    row, parent_sha = _run_adjust(monkeypatch, {
        "asset_id": "asset-legacy", "generation_output_id": None,
        "generation_run_id": None})
    assert row["parent_generation_run_id"] is None
    assert row["input_image_sha256"] == parent_sha
    snap = row["input_assets"][0]
    assert snap["assetId"] == "asset-legacy" and snap["outputId"] is None
    assert snap["sha256"] == parent_sha


def test_explicit_parent_wins_over_same_job_reverse_lookup(monkeypatch):
    """명시 부모가 정본. 우연히 같은 바이트가 이 job 안에 있어도 그쪽으로 붙지 않는다."""
    _Recorder().install(monkeypatch)
    runlog = gr.RunLogger(pool=_Pool(), r2=None, job_id="j", project_id="p",
                          user_id="u", enabled=True)
    local = asyncio.run(runlog.begin(kind="mannequin_generate", prompt="g", candidate="A"))
    asyncio.run(runlog.finish(local, image=b"same-bytes", candidate="A"))
    rec = _Recorder()
    rec.install(monkeypatch)
    asyncio.run(runlog.begin(kind="mannequin_adjust_edit", prompt="e", candidate="A",
                             input_image=b"same-bytes",
                             explicit_parent_generation_run_id="run-from-prev-job"))
    assert rec.runs[-1]["parent_generation_run_id"] == "run-from-prev-job"


# ── R2 는 event loop 밖에서 ──────────────────────────────────────────────────

def test_r2_calls_run_off_the_event_loop(monkeypatch):
    """boto3 는 동기 blocking — loop 스레드에서 부르면 워커의 다른 코루틴이 통째로 멈춘다."""
    rec = _Recorder()
    rec.install(monkeypatch)
    seen: dict = {}

    class R2:
        def put_bytes(self, key, data, mime, cache=None):
            seen["put"] = threading.get_ident()

        def delete(self, key):
            seen["delete"] = threading.get_ident()

    async def scenario():
        loop_thread = threading.get_ident()
        logger = gr.RunLogger(pool=_Pool(), r2=R2(), job_id="j", project_id="p",
                              user_id="u", enabled=True)
        await logger.begin(kind="mannequin_generate", prompt="x")
        return loop_thread

    loop_thread = asyncio.run(scenario())
    assert seen["put"] != loop_thread, "put_bytes 가 event loop 스레드에서 실행됐다"


def test_orphan_delete_also_runs_off_the_event_loop_and_warns(monkeypatch, caplog):
    """키 갱신 실패 → 삭제도 to_thread. 삭제까지 실패하면 warning 만 남고 진행한다."""
    seen: dict = {}

    async def key_boom(conn, **kw):
        raise RuntimeError("db down")

    rec = _Recorder()
    rec.install(monkeypatch, update_key=key_boom)

    class R2:
        def put_bytes(self, key, data, mime, cache=None):
            seen["put"] = threading.get_ident()

        def delete(self, key):
            seen["delete"] = threading.get_ident()
            raise RuntimeError("r2 delete down")

    async def scenario():
        logger = gr.RunLogger(pool=_Pool(), r2=R2(), job_id="j", project_id="p",
                              user_id="u", enabled=True)
        return threading.get_ident(), await logger.begin(kind="mannequin_generate",
                                                         prompt="x")

    with caplog.at_level("WARNING"):
        loop_thread, run_id = asyncio.run(scenario())
    assert run_id, "삭제 실패가 기록 자체를 무효화하면 안 된다"
    assert seen["delete"] != loop_thread
    msgs = [r.getMessage() for r in caplog.records]
    assert any("orphan prompt delete failed" in m for m in msgs)
    assert all("users/" not in m for m in msgs), "로그에 R2 키 원문이 있다"


# ── outputs 실패 분류 ────────────────────────────────────────────────────────

def test_output_insert_failure_is_classified_as_schema_missing(monkeypatch, caplog):
    with caplog.at_level("WARNING"):
        _sink, out = _finalize(monkeypatch, {"generation_lineage": LINEAGE},
                               fail_outputs=True)
    msg = next(r.getMessage() for r in caplog.records
               if "generation_outputs insert failed" in r.getMessage())
    assert "category=schema_missing" in msg
    assert "insert into" not in msg, "raw SQL 이 로그에 남았다"
    assert out["cuts"], "컷 출고가 막혔다"


# ── 부모 조회: 계보 컬럼 + migration 미적용 폴백 ─────────────────────────────

class _ParentCursor:
    """get_mannequin_edit_parent 용 — 두 번째(계보) select 만 선택적으로 실패시킨다."""

    def __init__(self, sink, fail_lineage=False):
        self.sink = sink
        self.fail_lineage = fail_lineage
        self._kind = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.sink.append(flat)
        if "generation_outputs" in flat:
            self._kind = "lineage"
            if self.fail_lineage:
                from psycopg import errors
                raise errors.UndefinedTable("relation does not exist")
        elif "mannequin_cuts" in flat:
            self._kind = "parent"
        else:
            self._kind = "other"

    async def fetchone(self):
        if self._kind == "parent":
            return {"id": "A-3", "mannequin_cut_id": "cut-1", "asset_id": "asset-1",
                    "r2_key": "k", "mime_type": "image/png", "generation_metadata": {}}
        if self._kind == "lineage":
            return {"generation_output_id": "out-1", "generation_run_id": "run-1"}
        return None


class _ParentConn:
    def __init__(self, sink, fail_lineage=False):
        self.sink = sink
        self.fail_lineage = fail_lineage

    def cursor(self):
        return _ParentCursor(self.sink, self.fail_lineage)


def test_edit_parent_returns_output_lineage():
    sink: list[str] = []
    out = asyncio.run(repo.get_mannequin_edit_parent(_ParentConn(sink), "u1", "p1"))
    assert out["mannequin_cut_id"] == "cut-1" and out["asset_id"] == "asset-1"
    assert out["generation_output_id"] == "out-1"
    assert out["generation_run_id"] == "run-1"
    assert out["r2_key"] == "k" and out["mime_type"] == "image/png"


def test_edit_parent_survives_missing_generation_tables():
    """migration 미적용 환경에서 계보 조회가 실패해도 **편집 자체는 막히면 안 된다**."""
    sink: list[str] = []
    out = asyncio.run(repo.get_mannequin_edit_parent(
        _ParentConn(sink, fail_lineage=True), "u1", "p1"))
    assert out is not None and out["r2_key"] == "k", "부모 컷을 못 받으면 편집이 죽는다"
    assert out["generation_run_id"] is None and out["generation_output_id"] is None
    assert any(s.startswith("rollback to savepoint edit_parent_lineage") for s in sink)
    assert any(s.startswith("release savepoint edit_parent_lineage") for s in sink)


# ── UUID 직렬화 (드라이버가 uuid.UUID 를 돌려줘도 jsonb 로 나가야 한다) ────────

class _UuidParentCursor(_ParentCursor):
    async def fetchone(self):
        row = await super().fetchone()
        if row is None:
            return None
        out = dict(row)
        for k in ("mannequin_cut_id", "asset_id", "generation_output_id",
                  "generation_run_id"):
            if out.get(k):
                out[k] = uuid.uuid5(uuid.NAMESPACE_DNS, str(out[k]))
        return out


class _UuidParentConn(_ParentConn):
    def cursor(self):
        return _UuidParentCursor(self.sink, self.fail_lineage)


def test_edit_parent_sql_casts_uuid_columns_to_text():
    """드라이버 설정이 바뀌어도 새지 않게 SQL 자체가 ::text 로 못박는다."""
    src = inspect.getsource(repo.get_mannequin_edit_parent)
    for frag in ("mc.id::text as mannequin_cut_id", "mc.asset_id::text as asset_id",
                 "go.id::text as generation_output_id",
                 "go.generation_run_id::text as generation_run_id"):
        assert frag in src, f"SQL 에 캐스트 누락: {frag}"


def test_uuid_objects_from_the_driver_are_normalised_to_str():
    out = asyncio.run(repo.get_mannequin_edit_parent(_UuidParentConn([]), "u1", "p1"))
    for k in ("mannequin_cut_id", "asset_id", "generation_output_id",
              "generation_run_id"):
        assert isinstance(out[k], str), f"{k} 가 uuid.UUID 로 남았다"
    json.dumps(out)  # 직렬화 가능해야 한다 — 실패하면 여기서 TypeError


def test_uuid_parent_lineage_survives_snapshot_serialisation(monkeypatch):
    """부모 값이 UUID 로 흘러들어도 input_assets 스냅샷이 jsonb 로 나갈 수 있어야 한다."""
    parent = asyncio.run(repo.get_mannequin_edit_parent(_UuidParentConn([]), "u1", "p1"))
    row, parent_sha = _run_adjust(monkeypatch, {
        "asset_id": parent["asset_id"],
        "generation_output_id": parent["generation_output_id"],
        "generation_run_id": parent["generation_run_id"]})
    json.dumps(row["input_assets"])            # 실패하면 insert 가 죽는다
    assert row["input_assets"][0]["assetId"] == parent["asset_id"]
    assert row["parent_generation_run_id"] == parent["generation_run_id"]
    assert row["input_image_sha256"] == parent_sha


# ── carrier 가 후보 스냅샷과 함께 복구된다 (워커 실제 경로) ───────────────────

def _run_candidate_pool(monkeypatch, *, images, series_by_attempt, fail_from=None):
    """실제 `_run_candidate` 후보 풀 경로 실행 → (rec, 저장된 컷 dict, 이미지 목록)."""
    rec = _Recorder()
    rec.install(monkeypatch)
    monkeypatch.setattr(mj, "_emit", lambda *a, **k: _noop())
    state = {"n": 0}

    class _Gemini:
        async def generate_content_image(self, model, prompt, imgs, size,
                                         temperature=None, aspect_ratio=None):
            n = state["n"]
            state["n"] += 1
            if fail_from is not None and n >= fail_from:
                raise GeminiError("Gemini 503: unavailable")
            return types.SimpleNamespace(image=images[min(n, len(images) - 1)],
                                         mime="image/png", latency_ms=1, usage=None)

    async def fake_series(*, app, pool, s, job_id, project_id, candidate, attempt, res):
        return series_by_attempt(attempt)

    async def fake_p2(settings, prods, gen_img, scored=True, fit_profile=None):
        # A~C 는 통과선. 최종 게이트는 D축(series)이 끌어내린다 → pre-gate 아닌 final_reject.
        return {"product_fidelity": 90, "physical_naturalness": 90, "image_quality": 90,
                "verdict": "pass"}

    monkeypatch.setattr(mj, "_apply_series_qc", fake_series)
    monkeypatch.setattr(mj.image_qc, "verdict", fake_p2)

    settings = make_settings(r2_bucket="bucket", generation_run_log="shadow",
                             image_qc="enforce", mannequin_max_attempts=2,
                             mannequin_hybrid_composite="off")
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=_FakeR2(), gemini=_Gemini()))
    runlog = gr.RunLogger(pool=_Pool(), r2=_FakeR2(), job_id="j1", project_id="p1",
                          user_id="u1", enabled=True)
    cut = asyncio.run(mj._run_candidate(
        app=app, job={"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "t"},
        candidate="A", base_fit="regular", base_gender="women",
        base_img=InlineImage("image/png", _plain(30)),
        prod_refs=[ProductReference(slot="Front", asset_id="a1",
                                    image=InlineImage("image/png", _plain(10)))],
        match_img=None, product_count=1,
        template="T ${baseGender} ${clothingType}.\n${imageManifest}",
        product={"name": "티"}, analysis={}, clothing_type="top",
        image_manifest="1. base", fit_profile=SNAP_PROFILE, runlog=runlog))
    return rec, cut


def _run_id_for(rec, image_bytes):
    """그 이미지를 만든 run — 기록된 순서와 attempt 로 특정한다."""
    gen_rows = [r for r in rec.runs if r["kind"] == "mannequin_generate"]
    return gen_rows


def test_restored_candidate_keeps_its_own_carrier_not_a_later_one(monkeypatch):
    """시나리오 A: G1 reject 보관 → G2 더 나쁨 → G1 복구. 조상은 G1(≠G2)."""
    g1, g2 = _plain(101), _plain(102)
    rec, cut = _run_candidate_pool(
        monkeypatch, images=[g1, g2],
        # 1차는 낮지만 2차가 더 낮다 → 최선본은 G1
        series_by_attempt=lambda a: {"consistency": 55 if a == 1 else 20,
                                     "inconsistencies": []})
    assert cut is not None, "구제 출고가 안 됐다"
    gen_rows = [r for r in rec.runs if r["kind"] == "mannequin_generate"]
    assert len(gen_rows) == 2, "두 번 생성해야 시나리오가 성립한다"
    run_g1, run_g2 = gen_rows[0]["run_id"], gen_rows[1]["run_id"]
    lin = cut["generation_lineage"]
    assert lin["output_sha256"] == hashlib.sha256(g1).hexdigest(), "복구 이미지가 G1 이 아니다"
    assert lin["generation_run_id"] == run_g1
    assert lin["generation_run_id"] != run_g2, "다음 attempt 의 carrier 가 붙었다"


def test_loop_exhausted_restores_image_and_carrier_together(monkeypatch):
    """시나리오 B: G1 보관 → 다음 호출 실패로 루프 소진 → 이미지·carrier 둘 다 G1."""
    g1 = _plain(111)
    rec, cut = _run_candidate_pool(
        monkeypatch, images=[g1], fail_from=1,
        series_by_attempt=lambda a: {"consistency": 40, "inconsistencies": []})
    assert cut is not None, "final_reject 를 손에 들고 빈손으로 끝났다"
    gen_rows = [r for r in rec.runs if r["kind"] == "mannequin_generate"]
    ok_rows = [r for r in gen_rows
               if any(u["run_id"] == r["run_id"] and u["status"] == "succeeded"
                      for u in rec.updates)]
    assert len(ok_rows) == 1, "성공 호출은 G1 하나여야 한다"
    lin = cut["generation_lineage"]
    assert lin["output_sha256"] == hashlib.sha256(g1).hexdigest()
    assert lin["generation_run_id"] == ok_rows[0]["run_id"]


# ── carrier 를 모를 때: 행은 남기고 run 만 null ───────────────────────────────

def test_unknown_carrier_still_produces_an_output_row(monkeypatch):
    """성공한 run 은 있는데 carrier 를 못 잡은 경우 — 행은 남고 run 만 null 이다."""
    _Recorder().install(monkeypatch)
    runlog = gr.RunLogger(pool=_Pool(), r2=None, job_id="j", project_id="p",
                          user_id="u", enabled=True)
    run_id = asyncio.run(runlog.begin(kind="mannequin_generate", prompt="g",
                                      candidate="A"))
    asyncio.run(runlog.finish(run_id, image=b"carrier", candidate="A"))
    assert runlog.has_recorded_success("A")
    res = types.SimpleNamespace(image=b"post-processed", mime="image/png")
    lin = mj._output_lineage(runlog, res, "A", {"hybridComposite": {"applied": True}})
    assert lin is not None, "계보를 모른다고 행까지 버리면 조사할 방법이 없다"
    assert lin["generation_run_id"] is None
    assert lin["post_processed"] is True
    assert lin["output_sha256"] == hashlib.sha256(b"post-processed").hexdigest()
    assert lin["transformation"]["hybridComposite"]["applied"] is True


def test_no_output_row_when_nothing_was_ever_recorded(monkeypatch):
    """DB 기록이 통째로 실패했으면 이을 대상이 없다 — 행을 만들지 않는다."""
    async def boom(conn, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(repo, "insert_generation_run", boom)
    runlog = gr.RunLogger(pool=_Pool(), r2=None, job_id="j", project_id="p",
                          user_id="u", enabled=True)
    assert asyncio.run(runlog.begin(kind="mannequin_generate", prompt="g",
                                    candidate="A")) is None
    assert not runlog.has_recorded_success("A")
    res = types.SimpleNamespace(image=b"x", mime="image/png")
    assert mj._output_lineage(runlog, res, "A", None) is None


def test_flag_off_produces_no_output_row():
    runlog = gr.RunLogger(pool=_Pool(), r2=None, job_id="j", project_id="p",
                          user_id="u", enabled=False)
    assert not runlog.has_recorded_success("A")
    res = types.SimpleNamespace(image=b"x", mime="image/png")
    assert mj._output_lineage(runlog, res, "A", None) is None


def test_null_run_id_output_row_is_written_as_nullable(monkeypatch):
    """generation_run_id null 이 FK/JSON 문제 없이 그대로 들어간다."""
    lin = {"generation_run_id": None, "output_sha256": "sha-x", "post_processed": True,
           "transformation": {"hybridComposite": {"applied": True}}}
    sink, _out = _finalize(monkeypatch, {"generation_lineage": lin})
    params = [p for s, p in sink if s.startswith("insert into generation_outputs")][0]
    assert params[0] is None and params[4] == "sha-x" and params[5] is True
    json.dumps(params[6].obj if hasattr(params[6], "obj") else lin["transformation"])
