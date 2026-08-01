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
import types

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
                     "matching_garment", "style_reference"]


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
    _Recorder().install(monkeypatch)
    """hybrid composite 가 최종 바이트를 바꿔도 행은 남고, 마지막 provider 조상을 가리킨다."""
    runlog = gr.RunLogger(pool=_Pool(), r2=None, job_id="j", project_id="p",
                          user_id="u", enabled=True)
    gen = asyncio.run(runlog.begin(kind="mannequin_generate", prompt="g", candidate="A"))
    asyncio.run(runlog.finish(gen, image=b"carrier", candidate="A"))
    lin = runlog.output_lineage(b"composited-bytes", "A")   # 후처리로 바이트가 달라짐
    assert lin["generation_run_id"] == gen, "조상을 잃으면 output 행이 통째로 사라진다"
    assert lin["post_processed"] is True
    assert lin["output_sha256"] == hashlib.sha256(b"composited-bytes").hexdigest()


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
