"""캐노니컬 컷아웃 파이프라인 — 생산자(sam_preprocess)와 소비자(loader)의 계약.

여기서 지키는 핵심은 세 가지다:

1. **SAM 은 보조 인프라다.** 미설정·다운·타임아웃·한쪽 뷰 실패 — 무엇이든 업로드/분석/생성을
   막으면 안 된다. 그래서 실패 경로가 예외가 아니라 상태로 표현되는지를 본다.
2. **Front 와 Back 은 끝까지 독립이다.** 한쪽 실패가 다른 쪽 성공을 버리지 않는다.
3. **낡은 컷아웃은 절대 로드되지 않는다.** 셀러가 앞면 사진을 갈아끼우면 이전 사진으로 만든
   컷아웃은 그 순간 무효다 — 마네킹에 예전 옷을 입히는 것보다 RAW 로 가는 게 낫다.

무거운 SAM 추론은 여기 없다. HTTP 경계는 가짜로 막는다.
"""
import asyncio

import pytest

from app.services import canonical_reference as cr
from app.services import sam_client


# ── SAM 클라이언트 ───────────────────────────────────────────────────────────

class _S:
    def __init__(self, url="http://sam:8080", token="tok", timeout=90.0):
        self.sam_service_url, self.sam_internal_token = url, token
        self.sam_request_timeout_s = timeout


def test_client_is_inactive_until_both_url_and_token_exist():
    assert sam_client.configured(_S())
    assert not sam_client.configured(_S(url=None))
    assert not sam_client.configured(_S(token=None))


def test_unconfigured_client_raises_recoverable_not_crashes():
    with pytest.raises(sam_client.SamUnavailable):
        asyncio.run(sam_client.segment_garment(_S(url=None), {"Front": "k"}))


def test_view_result_requires_a_cutout_key_to_count_as_ready():
    """status=ready 인데 키가 없으면 저장할 것이 없다 — ready 로 취급하면 안 된다."""
    assert sam_client.SamViewResult.from_payload(
        "Front", {"status": "ready", "cutoutKey": "derived/x.png"}).ready
    assert not sam_client.SamViewResult.from_payload("Front", {"status": "ready"}).ready
    assert not sam_client.SamViewResult.from_payload(
        "Front", {"status": "failed", "code": "source_unavailable"}).ready


def test_client_never_forwards_unknown_views():
    """Detail 은 캐노니컬 대상이 아니다 — 클라이언트에서 걸러진다."""
    assert sam_client.VIEWS == ("Front", "Back")


# ── 캐시 정체성 ──────────────────────────────────────────────────────────────

def test_cutout_identity_covers_source_view_model_and_algorithm():
    from sam_service.segmentation import ALGORITHM_VERSION, cutout_key
    base = cutout_key("h1", "Front")
    assert base != cutout_key("h2", "Front")                       # 소스 교체
    assert base != cutout_key("h1", "Back")                        # 뷰
    assert base != cutout_key("h1", "Front", model_version="m2")   # 모델
    assert base != cutout_key("h1", "Front", algorithm_version="v9")
    assert cutout_key("h1", "Front") == base                       # 결정적
    assert ALGORITHM_VERSION == "sam2-grid8-v2"


# ── 자산 메타데이터 / 신선도 ─────────────────────────────────────────────────

def _result(**kw):
    d = dict(view="Front", ready=True, cutout_key="derived/canonical-cutout/a/b/Front/h1.png",
             source_hash="h1", model_version="m", algorithm_version="sam2-grid8-v2",
             checksum="c", width=10, height=20, area_frac=0.43, byte_size=99, cached=False)
    d.update(kw)
    return sam_client.SamViewResult(**d)


def test_metadata_records_the_full_provenance_chain():
    m = cr.metadata_for("Front", _result(), source_asset_id="src-1")
    assert m["canonicalType"] == cr.CANONICAL_KIND
    assert m["view"] == "Front" and m["sourceAssetId"] == "src-1"
    assert m["sourceHash"] == "h1" and m["producer"] == cr.PRODUCER
    assert m["modelVersion"] == "m" and m["algorithmVersion"] == "sam2-grid8-v2"
    assert m["r2Key"].endswith("h1.png") and m["checksum"] == "c"


def test_a_cutout_from_the_current_source_is_current():
    m = cr.metadata_for("Front", _result(), source_asset_id="src-1")
    assert cr.is_current(m, source_asset_id="src-1", source_hash="h1")
    assert cr.is_current(m, source_asset_id="src-1", source_hash=None)   # 해시 미상이면 id 로


def test_a_cutout_from_a_replaced_source_is_stale():
    """앞면 사진 교체 = 이전 컷아웃 무효. 이게 깨지면 예전 옷이 마네킹에 입혀진다."""
    m = cr.metadata_for("Front", _result(), source_asset_id="src-1")
    assert not cr.is_current(m, source_asset_id="src-2", source_hash="h1")   # 다른 asset
    assert not cr.is_current(m, source_asset_id="src-1", source_hash="h2")   # 같은 id, 다른 내용


def test_non_canonical_assets_are_never_treated_as_cutouts():
    assert not cr.is_current({"view": "Front", "sourceAssetId": "src-1"},
                             source_asset_id="src-1", source_hash=None)
    assert not cr.is_current({}, source_asset_id="src-1", source_hash=None)


def test_only_front_and_back_have_canonical_slots():
    assert cr.SLOT_FOR_VIEW == {"Front": "CanonicalFront", "Back": "CanonicalBack"}
    assert "Detail" not in cr.SLOT_FOR_VIEW


# ── 로더 ─────────────────────────────────────────────────────────────────────

class _Cur:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *_a, **_k):
        return None

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _Cur(self._rows)


class _R2:
    def __init__(self, data=b"PNGBYTES", fail=False):
        self.data, self.fail = data, fail

    def get_bytes(self, _key):
        if self.fail:
            raise RuntimeError("r2 down")
        return self.data


def _row(view, source_asset_id, key="derived/x.png", asset_id="a1", source_hash="h1"):
    return {"id": asset_id, "r2_key": key, "mime_type": "image/png", "byte_size": 9,
            "metadata": cr.metadata_for(
                view, _result(view=view, source_hash=source_hash, cutout_key=key),
                source_asset_id=source_asset_id)}


def test_loader_returns_both_canonical_slots_when_current():
    conn = _Conn([_row("Front", "src-f", asset_id="af"), _row("Back", "src-b", asset_id="ab")])
    out = asyncio.run(cr.load(conn, _R2(), project_id="p",
                        sources={"Front": {"id": "src-f", "hash": "h1"},
                                 "Back": {"id": "src-b", "hash": "h1"}}))
    assert set(out) == {"CanonicalFront", "CanonicalBack"}
    assert out["CanonicalFront"].slot == "CanonicalFront"
    assert out["CanonicalBack"].image.data == b"PNGBYTES"


def test_loader_returns_front_only_when_back_was_never_produced():
    conn = _Conn([_row("Front", "src-f")])
    out = asyncio.run(cr.load(conn, _R2(), project_id="p",
                        sources={"Front": {"id": "src-f", "hash": "h1"},
                                 "Back": {"id": "src-b", "hash": "h1"}}))
    assert set(out) == {"CanonicalFront"}


def test_loader_returns_back_only_when_front_failed():
    conn = _Conn([_row("Back", "src-b")])
    out = asyncio.run(cr.load(conn, _R2(), project_id="p",
                        sources={"Front": {"id": "src-f", "hash": "h1"},
                                 "Back": {"id": "src-b", "hash": "h1"}}))
    assert set(out) == {"CanonicalBack"}


def test_loader_skips_a_cutout_built_from_a_replaced_source():
    conn = _Conn([_row("Front", "src-OLD")])
    out = asyncio.run(cr.load(conn, _R2(), project_id="p",
                        sources={"Front": {"id": "src-NEW", "hash": "h1"}}))
    assert out == {}


def test_loader_skips_a_cutout_whose_source_content_changed():
    conn = _Conn([_row("Front", "src-f", source_hash="OLDHASH")])
    out = asyncio.run(cr.load(conn, _R2(), project_id="p",
                        sources={"Front": {"id": "src-f", "hash": "NEWHASH"}}))
    assert out == {}


def test_loader_degrades_to_nothing_when_r2_is_unreadable():
    """RAW 는 언제나 안전한 경로 — 로더 실패가 생성 잡을 죽이면 안 된다."""
    conn = _Conn([_row("Front", "src-f")])
    out = asyncio.run(cr.load(conn, _R2(fail=True), project_id="p",
                        sources={"Front": {"id": "src-f", "hash": "h1"}}))
    assert out == {}


def test_loader_with_no_sources_does_no_work():
    out = asyncio.run(cr.load(_Conn([]), _R2(), project_id="p", sources={}))
    assert out == {}


# ── 잡: 실패 경로가 업로드/분석을 막지 않는다 ────────────────────────────────

class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool._conn

            async def __aexit__(self, *a):
                return False
        return _Ctx()


class _JobConn(_Conn):
    def __init__(self):
        super().__init__([])
        self.committed = 0

    async def commit(self):
        self.committed += 1


def _job_app(monkeypatch, *, configured=True, segment=None):
    import types
    from app.workers import sam_preprocess_job as spj

    conn = _JobConn()
    app = types.SimpleNamespace(state=types.SimpleNamespace(pool=_Pool(conn)))
    finished = {}

    async def _finalize(_c, *, job_id, lease_token, status, result):
        finished.update(status=status, result=result)
        return True

    monkeypatch.setattr(spj.repo, "finalize_uncharged_job", _finalize)
    monkeypatch.setattr(spj.repo, "get_product", lambda *_a, **_k: _async({}))
    monkeypatch.setattr(spj, "load_settings", lambda: _S(url=None) if not configured else _S())
    if segment is not None:
        monkeypatch.setattr(spj.sam_client, "segment_garment", segment)
    return app, finished


def _async(value):
    async def _c():
        return value
    return _c()


def _job():
    return {"id": "j1", "project_id": "p1", "user_id": "u1", "lease_token": "lt"}


def test_job_is_a_noop_when_sam_is_not_configured(monkeypatch):
    """SAM 미설정 환경은 정상이다 — 에러가 아니라 skip."""
    from app.workers import sam_preprocess_job as spj
    app, finished = _job_app(monkeypatch, configured=False)
    asyncio.run(spj.run_sam_preprocess_job(app, _job()))
    assert finished["status"] == "done"
    assert finished["result"]["state"] == "skipped"
    assert finished["result"]["reason"] == "sam_not_configured"


def test_job_reports_unavailable_without_raising_when_sam_is_down(monkeypatch):
    """SAM 다운이 예외로 새면 디스패처가 잡을 죽인다 — 상태로 표현되어야 한다."""
    from app.workers import sam_preprocess_job as spj

    async def _down(*_a, **_k):
        raise spj.sam_client.SamUnavailable("connection refused")

    app, finished = _job_app(monkeypatch, segment=_down)
    monkeypatch.setattr(spj.mannequin, "base_color_images",
                        lambda _p: [("Front", "src-f")])
    monkeypatch.setattr(spj.repo, "get_asset_for_user",
                        lambda *_a, **_k: _async({"r2_key": "users/u/projects/p/uploads/f.jpg"}))
    asyncio.run(spj.run_sam_preprocess_job(app, _job()))
    assert finished["status"] == "error"
    assert finished["result"]["state"] == "unavailable"


def test_job_keeps_a_good_front_when_back_fails(monkeypatch):
    from app.workers import sam_preprocess_job as spj

    async def _partial(_s, views):
        return {"Front": _result(view="Front"),
                "Back": sam_client.SamViewResult(view="Back", ready=False,
                                                 code="segmentation_failed")}

    app, finished = _job_app(monkeypatch, segment=_partial)
    monkeypatch.setattr(spj.mannequin, "base_color_images",
                        lambda _p: [("Front", "src-f"), ("Back", "src-b")])
    monkeypatch.setattr(spj.repo, "get_asset_for_user",
                        lambda *_a, **_k: _async({"r2_key": "users/u/projects/p/uploads/f.jpg"}))
    monkeypatch.setattr(spj.canonical_reference, "record",
                        lambda *_a, **k: _async({"id": "asset-front"}))
    asyncio.run(spj.run_sam_preprocess_job(app, _job()))
    assert finished["status"] == "done"
    assert finished["result"]["state"] == "partial"
    assert "Front" in finished["result"]["recorded"]
    assert finished["result"]["failed"] == {"Back": "segmentation_failed"}


def test_job_only_ever_asks_for_front_and_back(monkeypatch):
    """Detail 은 캐노니컬 대상이 아니다 — 잡이 그걸 SAM 에 보내면 안 된다."""
    from app.workers import sam_preprocess_job as spj
    seen = {}

    async def _capture(_s, views):
        seen.update(views)
        return {}

    app, finished = _job_app(monkeypatch, segment=_capture)
    monkeypatch.setattr(spj.mannequin, "base_color_images",
                        lambda _p: [("Front", "f"), ("Back", "b"), ("Detail", "d"),
                                    ("Fit", "x")])
    monkeypatch.setattr(spj.repo, "get_asset_for_user",
                        lambda *_a, **_k: _async({"r2_key": "users/u/projects/p/uploads/f.jpg"}))
    asyncio.run(spj.run_sam_preprocess_job(app, _job()))
    assert set(seen) == {"Front", "Back"}
    assert spj.ELIGIBLE_VIEWS == ("Front", "Back")


def test_job_sends_only_trusted_r2_keys(monkeypatch):
    """소스는 asset 행의 r2_key 에서만 나온다 — 임의 URL 이 SAM 에 갈 수 없다."""
    from app.workers import sam_preprocess_job as spj
    seen = {}

    async def _capture(_s, views):
        seen.update(views)
        return {}

    app, _ = _job_app(monkeypatch, segment=_capture)
    monkeypatch.setattr(spj.mannequin, "base_color_images", lambda _p: [("Front", "src-f")])
    monkeypatch.setattr(spj.repo, "get_asset_for_user", lambda *_a, **_k: _async(
        {"r2_key": "users/u/projects/p/uploads/f.jpg"}))
    asyncio.run(spj.run_sam_preprocess_job(app, _job()))
    assert list(seen.values()) == ["users/u/projects/p/uploads/f.jpg"]
    for key in seen.values():
        assert "://" not in key and not key.startswith("/")


# ── 등록 / 병렬 enqueue ──────────────────────────────────────────────────────

def test_sam_preprocess_is_a_registered_job_kind():
    from app.workers.dispatcher import _KINDS, _WORKERS
    assert "sam_preprocess" in _WORKERS and "sam_preprocess" in _KINDS


def test_analysis_route_enqueues_sam_preprocess_independently():
    """같은 소스-준비 지점에서 둘 다 뜬다. 서로의 완료를 기다리지 않는다."""
    import pathlib
    src = pathlib.Path("app/routes.py").read_text(encoding="utf-8")
    block = src[src.index('kind="analyze"'):src.index('kind="analyze"') + 1800]
    assert 'kind="sam_preprocess"' in block
    assert block.index('kind="sam_preprocess"') > block.index('kind="analyze"')
    assert "await conn.commit()" in block        # 커밋 후 디스패처 wake


# ── 생성 입력 구성 ───────────────────────────────────────────────────────────

def _refs(*slots):
    from app.agents.product_reference import ProductReference
    from app.agents.vision_llm import InlineImage
    return {s: ProductReference(slot=s, asset_id=f"a-{s}",
                                image=InlineImage("image/png", b"x")) for s in slots}


def _attach(raw_slots, canonical):
    """mannequin_job 의 캐노니컬 첨부 규칙을 그대로 재현 — RAW 는 절대 대체되지 않는다."""
    from app.workers.mannequin_job import _CANONICAL_SLOTS
    prod_assets = [{"slot": s, "id": f"raw-{s}"} for s in raw_slots]
    for slot in _CANONICAL_SLOTS:
        ref = canonical.get(slot)
        if ref is not None:
            prod_assets.append({"slot": slot, "id": ref.asset_id})
    return [a["slot"] for a in prod_assets]


def test_baseline_generation_input_is_raw_only():
    assert _attach(["Front", "Back", "Detail"], {}) == ["Front", "Back", "Detail"]


def test_sam_capable_input_appends_both_canonical_views_after_raw():
    slots = _attach(["Front", "Back", "Detail"],
                    _refs("CanonicalFront", "CanonicalBack"))
    assert slots == ["Front", "Back", "Detail", "CanonicalFront", "CanonicalBack"]
    assert slots[:3] == ["Front", "Back", "Detail"], "RAW must survive untouched"


def test_canonical_front_only_still_keeps_every_raw_view():
    assert _attach(["Front", "Back", "Detail"], _refs("CanonicalFront")) == [
        "Front", "Back", "Detail", "CanonicalFront"]


def test_canonical_back_only_still_keeps_every_raw_view():
    assert _attach(["Front", "Back", "Detail"], _refs("CanonicalBack")) == [
        "Front", "Back", "Detail", "CanonicalBack"]


def test_a_garment_without_detail_is_unaffected():
    assert _attach(["Front", "Back"], _refs("CanonicalFront", "CanonicalBack")) == [
        "Front", "Back", "CanonicalFront", "CanonicalBack"]


def test_both_canonical_slots_have_their_own_manifest_line():
    from app.workers.mannequin_job import _SLOT_LABEL
    front, back = _SLOT_LABEL["CanonicalFront"], _SLOT_LABEL["CanonicalBack"]
    assert front != back
    for line in (front, back):
        assert "PROPORTION AND CONSTRUCTION EVIDENCE, NEVER A SILHOUETTE TEMPLATE" in line
        assert "do NOT trace that contour literally as the final worn silhouette" in line
    assert "CanonicalDetail" not in _SLOT_LABEL


def test_the_loader_seam_accepts_a_legacy_single_front_reference():
    """예전 로더가 ProductReference 하나만 돌려줘도 깨지지 않는다."""
    import types
    from app.workers import mannequin_job as mj
    ref = _refs("CanonicalFront")["CanonicalFront"]
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        canonical_reference_loader=lambda _pid: ref))
    out = asyncio.run(mj._load_canonical_references(app, product_id="p"))
    assert set(out) == {"CanonicalFront"}


def test_a_loader_failure_degrades_to_raw_rather_than_raising():
    import types
    from app.workers import mannequin_job as mj

    def _boom(_pid):
        raise RuntimeError("db down")

    app = types.SimpleNamespace(state=types.SimpleNamespace(canonical_reference_loader=_boom))
    assert asyncio.run(mj._load_canonical_references(app, product_id="p")) == {}


def test_the_loader_never_produces():
    """로더는 읽기 전용이다 — SAM 호출·enqueue 가 들어오면 생성 잡이 전처리를 기다리게 된다."""
    import pathlib
    src = pathlib.Path("app/services/canonical_reference.py").read_text(encoding="utf-8")
    load_fn = src[src.index("async def load("):]
    for forbidden in ("segment_garment", "create_job", "sam_client", "enqueue"):
        assert forbidden not in load_fn


# ── 아키텍처 경계 ────────────────────────────────────────────────────────────

def test_the_main_app_never_imports_the_sam_service_implementation():
    import pathlib
    import re
    app_dir = pathlib.Path("app")
    hits = [str(p) for p in app_dir.rglob("*.py")
            if re.search(r"^\s*(from|import)\s+sam_service\b",
                         p.read_text(encoding="utf-8", errors="ignore"), re.M)]
    assert not hits, f"app imports sam_service: {hits}"


def test_the_main_app_still_has_no_torch():
    import pathlib
    import re
    app_dir = pathlib.Path("app")
    hits = [str(p) for p in app_dir.rglob("*.py")
            if p.name != "embeddings.py"
            and re.search(r"^\s*(from|import)\s+(torch|transformers)\b",
                          p.read_text(encoding="utf-8", errors="ignore"), re.M)]
    assert not hits, f"app imports torch/transformers: {hits}"
