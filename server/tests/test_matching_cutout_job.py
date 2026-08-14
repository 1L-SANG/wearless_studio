import pathlib
from app.workers.dispatcher import _WORKERS

SERVER = pathlib.Path(__file__).resolve().parents[1]


def test_kind_registered_and_in_db_constraint():
    assert "matching_cutout" in _WORKERS
    migrations = sorted((SERVER.parent / "supabase" / "migrations").glob("*.sql"))
    latest = ""
    for p in migrations:
        text = p.read_text(encoding="utf-8")
        if "jobs_kind_check" in text and "add constraint" in text:
            latest = text
    assert "'matching_cutout'" in latest


def test_flag_defaults_off():
    from app.config import Settings
    assert Settings.__dataclass_fields__["matching_cutout"].default == "off"


import asyncio
import types
from app.services.sam_client import SamViewResult
from app.workers import matching_cutout_job as job


class _FakeR2:
    def __init__(self, cut_png): self._cut = cut_png; self.puts = []
    def get_bytes(self, key): return self._cut  # 컷아웃 PNG 반환
    def put_bytes(self, key, data, mime, cache=None): self.puts.append((key, data, mime))


def _settings(**over):
    base = dict(matching_cutout="on", sam_service_url="http://sam", sam_internal_token="t",
                r2_bucket="b")
    base.update(over)
    return types.SimpleNamespace(**base)


def _run(app, job_dict):
    return asyncio.run(job.run_matching_cutout_job(app, job_dict))


def test_worker_cutouts_each_source_and_swaps_assets(monkeypatch):
    import io
    from PIL import Image
    # 투명 컷아웃 PNG 하나를 SAM 결과 R2 객체로 돌려준다
    rgba = Image.new("RGBA", (30, 40), (10, 120, 200, 255))
    buf = io.BytesIO(); rgba.save(buf, "PNG"); cut_png = buf.getvalue()

    calls = {"segment": [], "swap": None, "finalize": None, "assets": []}

    async def fake_segment(settings, views):
        calls["segment"].append(views)
        # cutout_key = 소스키 기반 가짜
        (v, k), = views.items()
        return {v: SamViewResult(view=v, ready=True, cutout_key=f"cut/{k}",
                                 source_hash="h"+k, width=30, height=40)}

    monkeypatch.setattr(job.sam_client, "segment_garment", fake_segment)

    async def fake_swap(conn, *, matching_item_id, project_id, thumbnail_asset_id, image_asset_id):
        calls["swap"] = (matching_item_id, thumbnail_asset_id, image_asset_id)

    monkeypatch.setattr(job.repo, "swap_matching_item_assets", fake_swap)

    # 실 repo.create_asset 은 DB 커넥션(cursor)이 필요 — 이 테스트는 no-op _Conn 을 쓰므로
    # 가짜로 대체한다(브리프 원본 테스트엔 없었지만, create_asset 이 실제 인터페이스라
    # 여기서 목킹하지 않으면 AttributeError 로 죽는다).
    async def fake_create_asset(conn, *, asset_id, user_id, project_id, source, bucket, key,
                                 mime, size, original_filename, metadata=None):
        calls["assets"].append({"asset_id": asset_id, "key": key, "mime": mime,
                                 "metadata": metadata})
        return {"id": asset_id}

    monkeypatch.setattr(job.repo, "create_asset", fake_create_asset)

    async def fake_finalize(conn, *, job_id, lease_token, status, result):
        calls["finalize"] = (status, result.get("state"))

    monkeypatch.setattr(job.repo, "finalize_uncharged_job", fake_finalize)

    # DB 커넥션·이벤트는 no-op
    class _Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def commit(self): pass
    class _Pool:
        def connection(self): return _Conn()
    r2 = _FakeR2(cut_png)
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=_settings(), pool=_Pool(), r2=r2))
    job_dict = {"id": "j1", "project_id": "p1", "user_id": "u1", "lease_token": "lt",
                "payload": {"matchingItemId": "custom_x",
                            "sourceAssetIds": ["a1", "a2"],
                            "sourceKeys": ["users/u/projects/p/uploads/a1.jpg",
                                           "users/u/projects/p/uploads/a2.jpg"]}}
    _run(app, job_dict)

    assert len(calls["segment"]) == 2, "원본 2장 각각 누끼"
    assert all("Front" in v for v in calls["segment"]), "view=Front 로 우회"
    assert calls["swap"] is not None, "asset 스왑됨"
    assert calls["finalize"][0] == "done" and calls["finalize"][1] == "ready"
    # 회색배경 합성본 + grid = R2 put 최소 3회(장2 + grid1)
    assert len(r2.puts) >= 3
    # grid(=image_asset_id) 는 매칭 컷아웃 메타데이터가 붙어야 Task 5 가 ready 를 읽는다
    grid_asset_id = calls["swap"][2]
    grid_meta = next(a["metadata"] for a in calls["assets"] if a["asset_id"] == grid_asset_id)
    assert grid_meta is not None and grid_meta.get("type") == "matchingCutout"


def test_worker_skips_when_flag_off(monkeypatch):
    calls = {"finalize": None}
    async def fake_finalize(conn, *, job_id, lease_token, status, result):
        calls["finalize"] = (status, result.get("state"))
    monkeypatch.setattr(job.repo, "finalize_uncharged_job", fake_finalize)
    class _Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def commit(self): pass
    class _Pool:
        def connection(self): return _Conn()
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=_settings(matching_cutout="off"), pool=_Pool(), r2=_FakeR2(b"")))
    _run(app, {"id": "j", "project_id": "p", "user_id": "u", "lease_token": "lt",
               "payload": {"matchingItemId": "x", "sourceAssetIds": [], "sourceKeys": []}})
    assert calls["finalize"][1] == "skipped"


def test_worker_keeps_original_when_sam_fails(monkeypatch):
    from app.services.sam_client import SamUnavailable
    calls = {"swap": False, "finalize": None}
    async def fake_segment(settings, views):
        raise SamUnavailable("down")
    monkeypatch.setattr(job.sam_client, "segment_garment", fake_segment)
    async def fake_swap(conn, **k): calls["swap"] = True
    monkeypatch.setattr(job.repo, "swap_matching_item_assets", fake_swap)
    async def fake_finalize(conn, *, job_id, lease_token, status, result):
        calls["finalize"] = (status, result.get("state"))
    monkeypatch.setattr(job.repo, "finalize_uncharged_job", fake_finalize)
    class _Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def commit(self): pass
    class _Pool:
        def connection(self): return _Conn()
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=_settings(), pool=_Pool(), r2=_FakeR2(b"")))
    _run(app, {"id": "j", "project_id": "p", "user_id": "u", "lease_token": "lt",
               "payload": {"matchingItemId": "x", "sourceAssetIds": ["a1"],
                           "sourceKeys": ["users/u/projects/p/uploads/a1.jpg"]}})
    assert calls["swap"] is False, "실패 시 스왑 안 함 = 원본 유지"
    assert calls["finalize"][0] in ("error", "done")
    assert calls["finalize"][1] in ("unavailable", "failed")
