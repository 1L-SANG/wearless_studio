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
    assert ALGORITHM_VERSION == "sam2-grid8-v3"


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
    """SAM 다운이 예외로 새면 디스패처가 잡을 죽인다 — 상태로 표현되어야 한다.

    그리고 그 상태는 `error` 가 아니라 `done`+unavailable 이다(2026-08-21). error 로 적으면
    이 잡이 멱등키를 문 채 종착해 같은 상품을 다시 저장해도 컷아웃이 영영 안 생긴다 —
    sam_retry_pusher 가 다음 세대를 걸 수 있으려면 done 이어야 한다.
    """
    from app.workers import sam_preprocess_job as spj

    async def _down(*_a, **_k):
        raise spj.sam_client.SamUnavailable("connection refused")

    app, finished = _job_app(monkeypatch, segment=_down)
    monkeypatch.setattr(spj.mannequin, "base_color_images",
                        lambda _p: [("Front", "src-f")])
    monkeypatch.setattr(spj.repo, "get_asset_for_user",
                        lambda *_a, **_k: _async({"r2_key": "users/u/projects/p/uploads/f.jpg"}))
    asyncio.run(spj.run_sam_preprocess_job(app, _job()))
    assert finished["status"] == "done"
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


def test_analysis_route_enqueues_sam_preprocess_independently(client, make_token, monkeypatch):
    """같은 소스-준비 지점에서 둘 다 뜬다. 서로의 완료를 기다리지 않는다.

    소스 문자열이 아니라 실제로 생긴 잡으로 본다 — 큐잉이 헬퍼로 빠져도 계약은 그대로다.
    """
    import app.routes as routes
    from conftest import patch_route_db

    jobs = []

    async def fake_create_job(_conn, **kwargs):
        jobs.append(kwargs["kind"])
        return {"id": f"job-{len(jobs)}"}, True

    monkeypatch.setattr(routes.repo, "get_project",
                        lambda _c, _u, pid: _done({"id": pid}))
    monkeypatch.setattr(routes.repo, "get_product",
                        lambda _c, _pid: _done({"colors": [{"isBase": True, "images": [
                            {"slot": "Front", "id": "img-front"}]}]}))
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    patch_route_db(monkeypatch, routes)

    res = client.post("/v1/projects/p1/analyze",
                      headers={"Authorization": f"Bearer {make_token()}"}, json={})

    assert res.status_code == 202, res.text
    assert jobs == ["analyze", "sam_preprocess"], "분석이 먼저 뜨고 전처리가 뒤따른다"


def _done(value):
    async def _coro():
        return value
    return _coro()


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


class _FakeConn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _app_with_loader(loader):
    import types
    pool = types.SimpleNamespace(connection=lambda: _FakeConn())
    return types.SimpleNamespace(state=types.SimpleNamespace(
        canonical_reference_loader=loader, pool=pool, r2=object()))


def test_the_loader_seam_accepts_a_legacy_single_front_reference():
    """로더가 슬롯 dict 대신 ProductReference 하나만 돌려줘도 깨지지 않는다."""
    from app.workers import mannequin_job as mj
    ref = _refs("CanonicalFront")["CanonicalFront"]

    async def loader(_conn, _r2, *, project_id, sources):
        return ref

    out = asyncio.run(mj._load_canonical_references(_app_with_loader(loader), project_id="p"))
    assert set(out) == {"CanonicalFront"}


def test_a_loader_failure_degrades_to_raw_rather_than_raising():
    from app.workers import mannequin_job as mj

    async def boom(_conn, _r2, *, project_id, sources):
        raise RuntimeError("db down")

    assert asyncio.run(
        mj._load_canonical_references(_app_with_loader(boom), project_id="p")) == {}


def test_no_loader_configured_degrades_to_raw():
    """오늘의 기본 상태 — SAM 미배선 환경에서는 캐노니컬이 아예 없다."""
    import types
    from app.workers import mannequin_job as mj
    app = types.SimpleNamespace(state=types.SimpleNamespace())
    assert asyncio.run(mj._load_canonical_references(app, project_id="p")) == {}


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


# ── 워커 등록 ↔ DB 제약 ──────────────────────────────────────────────────────

def _latest_kind_constraint() -> set[str]:
    """가장 최근 마이그레이션이 정의한 jobs_kind_check 허용 kind 집합."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[2] / "supabase/migrations"
    files = sorted(p for p in root.glob("*.sql")
                   if "jobs_kind_check" in p.read_text(encoding="utf-8"))
    assert files, "jobs_kind_check 를 정의하는 마이그레이션이 없다"
    body = files[-1].read_text(encoding="utf-8")
    m = re.search(r"add constraint jobs_kind_check\s*check\s*\(\s*kind in \((.*?)\)\s*\)",
                  body, re.S)
    assert m, f"{files[-1].name} 에서 kind 목록을 못 읽었다"
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def test_every_registered_worker_kind_is_allowed_by_the_db_constraint():
    """워커 등록과 DB 제약은 같이 움직여야 한다.

    회귀(2026-08-12): `sam_preprocess` 를 _WORKERS 와 라우트에는 넣고 마이그레이션에는 안
    넣었다. 잡 INSERT 가 jobs_kind_check 에 걸렸고, 분석 잡과 같은 트랜잭션이라 함께 롤백돼
    **POST /analyze 가 통째로 500** 이 됐다. 단위 테스트는 전부 통과했다 — 아무도 실제 제약을
    보지 않았기 때문이다. 로컬에서 화면을 눌러서야 드러났다.
    """
    from app.workers.dispatcher import _WORKERS
    allowed = _latest_kind_constraint()
    missing = sorted(set(_WORKERS) - allowed)
    assert not missing, (
        f"_WORKERS 에 있는데 jobs_kind_check 에 없는 kind: {missing} — "
        "마이그레이션으로 제약을 넓혀야 잡 생성이 성공한다")


def test_sam_preprocess_is_in_the_kind_constraint():
    assert "sam_preprocess" in _latest_kind_constraint()


# ── r2_key 는 전역 unique 인데 키는 내용 해시다 ───────────────────────────────────────
#
# 2026-08-26 프로드: 같은 사진이 두 번째 프로젝트에 올라오자 record() 가
# assets_r2_key_key 로 죽고, 그 예외가 dispatcher 루프까지 올라갔다. 조회는 프로젝트
# 범위인데 제약은 전역이라 생긴 어긋남이다.

class _SavepointCursor:
    def __init__(self, owner): self.owner = owner
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def execute(self, sql, params=None): self.owner.sql.append(sql.strip().split()[0].lower())
    async def fetchone(self): return None


class _SavepointConn:
    def __init__(self): self.sql = []
    def cursor(self): return _SavepointCursor(self)


def _ready_result(key="derived/v1/model/abc.png"):
    return sam_client.SamViewResult.from_payload(
        "Front", {"status": "ready", "cutoutKey": key, "byteSize": 10})


def test_record_reuses_the_row_when_the_same_owner_already_has_that_cutout(monkeypatch):
    """같은 셀러가 같은 사진을 다른 프로젝트에 또 올려도 새 행을 만들지 않는다."""
    seen = {}

    async def find_by_key(_conn, *, user_id, r2_key):
        seen["user_id"] = user_id           # 프로젝트가 아니라 소유자로 찾아야 한다
        return {"id": "asset-existing", "r2_key": r2_key}

    async def create_asset(*a, **k):
        raise AssertionError("기존 행이 있으면 INSERT 하면 안 된다")

    monkeypatch.setattr(cr, "find_by_key", find_by_key)
    monkeypatch.setattr(cr.repo, "create_asset", create_asset)
    row = asyncio.run(cr.record(
        _SavepointConn(), user_id="u1", project_id="p2", view="Front",
        result=_ready_result(), source_asset_id="src-1"))
    assert row["id"] == "asset-existing"
    assert seen["user_id"] == "u1"


def test_record_gives_up_one_cutout_instead_of_killing_the_dispatcher(monkeypatch):
    """다른 셀러가 같은 사진의 컷아웃을 이미 가진 경우.

    남의 asset 행을 이 프로젝트로 끌어올 수는 없다(테넌트 격리). 그렇다고 예외를 올리면
    dispatcher 루프가 죽는다 — 이 컷아웃만 포기하고 None 을 돌려준다(생성은 RAW 폴백).
    SAVEPOINT 로 되감아 같은 커넥션의 뒷 슬롯(Back)이 abort 된 tx 에 끌려가지 않게 한다.
    """
    from psycopg import errors

    async def find_by_key(_conn, *, user_id, r2_key):
        return None                          # 내 것 중엔 없다

    async def create_asset(*a, **k):
        raise errors.UniqueViolation("duplicate key ... assets_r2_key_key")

    monkeypatch.setattr(cr, "find_by_key", find_by_key)
    monkeypatch.setattr(cr.repo, "create_asset", create_asset)
    conn = _SavepointConn()
    row = asyncio.run(cr.record(
        conn, user_id="u2", project_id="p1", view="Front",
        result=_ready_result(), source_asset_id="src-1"))
    assert row is None
    assert "rollback" in conn.sql, "SAVEPOINT 를 되감지 않으면 다음 슬롯까지 tx abort 로 죽는다"
