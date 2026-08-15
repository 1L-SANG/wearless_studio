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


def _cut_png(size=(30, 40)):
    import io
    from PIL import Image
    rgba = Image.new("RGBA", size, (10, 120, 200, 255))
    buf = io.BytesIO(); rgba.save(buf, "PNG"); return buf.getvalue()


class _Conn:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def commit(self): pass


class _Pool:
    def connection(self): return _Conn()


def _wire_worker(monkeypatch, *, r2=None, cut_png=None, swap_rows=1,
                 current_image_asset_id="live-grid"):
    """워커의 DB·SAM 경계를 전부 가짜로 잡고 호출 기록을 돌려준다."""
    calls = {"segment": [], "swap": None, "finalize": None, "assets": [],
             "commits": 0, "item_asset_lookups": []}

    async def fake_segment(settings, views):
        calls["segment"].append(views)
        # cutout_key = 소스키 기반 가짜
        (v, k), = views.items()
        return {v: SamViewResult(view=v, ready=True, cutout_key=f"cut/{k}",
                                 source_hash="h"+k, width=30, height=40)}

    monkeypatch.setattr(job.sam_client, "segment_garment", fake_segment)

    async def fake_swap(conn, *, matching_item_id, project_id, thumbnail_asset_id, image_asset_id):
        calls["swap"] = (matching_item_id, thumbnail_asset_id, image_asset_id)
        return swap_rows  # 실 repo 는 갱신 행 수를 돌려준다(재리뷰 M-4)

    monkeypatch.setattr(job.repo, "swap_matching_item_assets", fake_swap)

    async def fake_item_asset(conn, item_id, user_id, project_id):
        calls["item_asset_lookups"].append((item_id, user_id, project_id))
        return current_image_asset_id

    monkeypatch.setattr(job.repo, "get_matching_item_asset", fake_item_asset)

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
        calls["finalize"] = (status, result)

    monkeypatch.setattr(job.repo, "finalize_uncharged_job", fake_finalize)

    class _CountingConn(_Conn):
        async def commit(self):
            calls["commits"] += 1

    class _CountingPool:
        def connection(self):
            return _CountingConn()

    r2 = r2 or _FakeR2(cut_png if cut_png is not None else _cut_png())
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=_settings(), pool=_CountingPool(), r2=r2))
    return app, r2, calls


def _job_dict(**payload_over):
    payload = {"matchingItemId": "custom_x",
               "sourceAssetIds": ["a1", "a2"],
               "gridAssetId": "old-grid",
               "sourceKeys": ["users/u/projects/p/uploads/a1.jpg",
                              "users/u/projects/p/uploads/a2.jpg"]}
    payload.update(payload_over)
    return {"id": "j1", "project_id": "p1", "user_id": "u1", "lease_token": "lt",
            "payload": payload}


def test_worker_cutouts_each_source_and_swaps_assets(monkeypatch):
    app, r2, calls = _wire_worker(monkeypatch)
    _run(app, _job_dict())

    assert len(calls["segment"]) == 2, "원본 2장 각각 누끼"
    assert all("Front" in v for v in calls["segment"]), "view=Front 로 우회"
    assert calls["swap"] is not None, "asset 스왑됨"
    assert calls["commits"] == 2, "파생 asset 트랜잭션 + 종결"
    assert calls["item_asset_lookups"] == [], "payload 에 gridAssetId 가 있으면 조회 안 함"
    assert calls["finalize"][0] == "done" and calls["finalize"][1]["state"] == "ready"
    # 저장하는 건 카드용 썸네일 + grid 둘뿐이다 — 원본 해상도 컷은 grid 입력으로만 쓴다(I6).
    assert [mime for _key, _data, mime in r2.puts] == ["image/jpeg", "image/jpeg"]
    thumb_asset_id, grid_asset_id = calls["swap"][1], calls["swap"][2]
    meta = {a["asset_id"]: a["metadata"] for a in calls["assets"]}
    assert set(meta) == {thumb_asset_id, grid_asset_id}
    # grid(=image_asset_id) 는 매칭 컷아웃 메타데이터가 붙어야 Task 5 가 ready 를 읽는다
    assert meta[grid_asset_id].get("type") == "matchingCutout"
    # I4 — 삭제 경로가 읽는 정리 손잡이(purpose + 원본 id 이월)
    assert meta[grid_asset_id]["purpose"] == "custom_match_grid"
    assert meta[grid_asset_id]["sourceAssetIds"] == ["a1", "a2", "old-grid"]
    assert meta[thumb_asset_id]["purpose"] == "custom_match_cutout"
    # I5 — provenance 가 비어 있지 않고 자기 자신을 가리키지 않는다
    assert meta[grid_asset_id]["sourceHash"]
    assert meta[grid_asset_id]["sourceAssetId"] != grid_asset_id


# 2026-08-14 재리뷰 M-3 — 이 변경 이전에 큐잉된 잡에는 gridAssetId 가 없다. 원본 grid 를
# 이월하지 못하면 "내 옷 삭제" 가 그 grid 행과 R2 객체를 영구히 남긴다.
def test_worker_falls_back_to_the_items_current_image_asset_for_legacy_payloads(monkeypatch):
    app, _r2, calls = _wire_worker(monkeypatch, current_image_asset_id="live-grid")
    _run(app, _job_dict(gridAssetId=None))

    assert calls["item_asset_lookups"] == [("custom_x", "u1", "p1")]
    grid_asset_id = calls["swap"][2]
    meta = {a["asset_id"]: a["metadata"] for a in calls["assets"]}
    assert meta[grid_asset_id]["sourceAssetIds"] == ["a1", "a2", "live-grid"]
    assert meta[grid_asset_id]["sourceAssetId"] == "live-grid"


# 2026-08-14 재리뷰 M-4 — 누끼 도중 셀러가 "내 옷"을 지우면 스왑이 0행이다. 그대로
# 커밋하면 삭제 경로가 영원히 도달할 수 없는 파생 asset 2개가 남는다.
def test_worker_discards_derived_assets_when_the_item_vanished_mid_job(monkeypatch):
    app, _r2, calls = _wire_worker(monkeypatch, swap_rows=0)
    _run(app, _job_dict())

    assert calls["swap"] is not None, "시도는 했다"
    assert calls["commits"] == 1, "종결 커밋 1회뿐 — 파생 asset 트랜잭션은 버려졌다"
    assert calls["finalize"] == ("done", {"state": "failed", "reason": "item_gone",
                                          "matchingItemId": "custom_x"})


def test_worker_derived_identity_is_stable_across_reruns(monkeypatch):
    # 리뷰 I5 — stale lease 회수로 같은 잡이 다시 돌아도 R2 객체·asset 행이 늘지 않는다.
    first_app, first_r2, first_calls = _wire_worker(monkeypatch)
    _run(first_app, _job_dict())
    second_app, second_r2, second_calls = _wire_worker(monkeypatch)
    _run(second_app, _job_dict())

    assert first_calls["swap"] == second_calls["swap"]
    assert [key for key, _d, _m in first_r2.puts] == [key for key, _d, _m in second_r2.puts]
    assert len({a["asset_id"] for a in first_calls["assets"]}) == 2


def test_worker_fails_open_on_any_unexpected_error(monkeypatch):
    # 리뷰 I2 — SamUnavailable 아닌 예외(R2·PIL·DB)도 잡을 종결시켜야 한다. 안 그러면
    # 잡이 running 으로 고착돼 카드가 최대 30분 스켈레톤에 갇힌다.
    class _BoomR2(_FakeR2):
        def get_bytes(self, key):
            raise RuntimeError("r2 down")

    app, _r2, calls = _wire_worker(monkeypatch, r2=_BoomR2(_cut_png()))
    _run(app, _job_dict())

    assert calls["swap"] is None, "실패 시 스왑 안 함 = 원본 유지"
    assert calls["finalize"][0] == "done", "잡은 반드시 종결된다"
    assert calls["finalize"][1]["state"] == "failed"
    assert calls["finalize"][1]["error"] == "RuntimeError"


def test_worker_fails_open_when_asset_swap_raises(monkeypatch):
    app, _r2, calls = _wire_worker(monkeypatch)

    async def boom_swap(conn, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(job.repo, "swap_matching_item_assets", boom_swap)
    _run(app, _job_dict())

    assert calls["finalize"][0] == "done"
    assert calls["finalize"][1]["state"] == "failed"


def test_worker_marks_failed_when_sam_has_no_cutout(monkeypatch):
    app, _r2, calls = _wire_worker(monkeypatch)

    async def not_ready(settings, views):
        (v, _k), = views.items()
        return {v: SamViewResult(view=v, ready=False)}

    monkeypatch.setattr(job.sam_client, "segment_garment", not_ready)
    _run(app, _job_dict())

    assert calls["swap"] is None
    assert calls["finalize"] == ("done", {"state": "failed", "reason": "no_cutout",
                                          "matchingItemId": "custom_x"})


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


def test_wrong_color_cutout_falls_back_to_original(monkeypatch):
    """SAM 이 배경 조각을 딴 경우(2026-08-15 실사고: 흰 문틈 → flat-lay 가 칼을 그림) —
    색 게이트가 누끼 전체를 버리고 원본을 유지한다. 스왑·asset 생성·재렌더 전부 없음."""
    import io as _io
    from PIL import Image as _Image

    def _png(mode_color, rgba=False):
        im = _Image.new("RGBA" if rgba else "RGB", (30, 40), mode_color)
        b = _io.BytesIO(); im.save(b, "PNG"); return b.getvalue()

    class _SplitR2:
        def __init__(self):
            self.puts = []
        def get_bytes(self, key):
            if key.startswith("cut/"):
                return _png((246, 246, 244, 255), rgba=True)  # 누끼 = 흰 덩어리
            return _png((24, 26, 30))                          # 원본 = 검정 데님
        def put_bytes(self, key, data, mime, cache=None):
            self.puts.append(key)

    app, r2, calls = _wire_worker(monkeypatch, r2=_SplitR2())
    _run(app, _job_dict())

    status, result = calls["finalize"]
    assert status == "done"
    assert result["state"] == "failed"
    assert result["reason"] == "cutout_color_mismatch"
    assert calls["swap"] is None, "원본 유지 — 스왑 없음"
    assert calls["assets"] == [], "파생 asset 도 만들지 않는다"
    assert r2.puts == [], "R2 에 아무것도 안 올린다"
