"""일시 장애는 재시도한다 — 톤 마스크의 unavailable 이 영구 "지원 안 함"이 되지 않게.

2026-08-18 실서버 사고 2호. 상품 등록→바로 생성 흐름에서 SAM 잡 3종(업로드 누끼·코디 누끼·
톤 마스크)이 단일 추론 슬롯에 몰렸고, 톤 마스크 잡이 `SamUnavailable` 로 **error 종결**됐다.
error 잡은 디스패처가 재시도하지 않고(주석만 그렇게 믿고 있었다), 멱등키까지 그 잡이 물고
있어서 — 셀러 화면은 그 컷에 대해 영원히 "이 컷은 색감 조정을 지원하지 않아요"가 됐다.

원칙: **일시 장애는 판정이 아니다.** 판정 실패(no_garment)만 failed 로 내리고, 인프라
장애(unavailable·unverified)는 톤 에디터 폴링이 유한 재시도를 몰고 간다 — 재시도는 셀러가
실제로 보고 있는 동안에만 일어나고, 예산(3회)이 다하면 그때 실패를 인정한다.
"""

import asyncio
import inspect
from datetime import datetime, timedelta, timezone

import pytest

import app.routes as routes
import app.workers.editor_garment_mask_job as job
from app.services import editor_garment_mask as egm
from app.services import sam_client

from conftest import patch_route_db


# ── ① 워커: 일시 장애는 error 종결이 아니다 ─────────────────────────────────

class _Settings:
    mannequin_tone_editor = "on"
    sam_service_url = "http://sam2:8080"
    sam_internal_token = "t"
    base_mannequin_men_asset_id = "base-m"
    base_mannequin_women_asset_id = "base-w"


def _async(value):
    async def _coro():
        return value
    return _coro()


def _run_outage_job(monkeypatch, *, raise_exc=None, result=None):
    """SAM 이 죽어 있는 동안 마스크 잡 한 번. DB 는 대역, 종결 판단은 진짜."""
    finished = {}

    async def segment(_s, **_kw):
        if raise_exc is not None:
            raise raise_exc
        return result

    async def finalize(_conn, *, job_id, lease_token, status, result):
        finished.update({"status": status, "result": result})

    monkeypatch.setattr(job, "load_settings", lambda: _Settings())
    monkeypatch.setattr(job.sam_client, "configured", lambda _s: True)
    monkeypatch.setattr(job.sam_client, "segment_worn_garment", segment)
    monkeypatch.setattr(job.repo, "finalize_uncharged_job", finalize)
    monkeypatch.setattr(job.repo, "get_mannequin_cut_asset",
                        lambda *_a, **_k: _async({"id": "cut-1", "r2_key": "cuts/a.jpg"}))
    monkeypatch.setattr(job.repo, "get_product",
                        lambda *_a, **_k: _async({"clothing_type": "top"}))
    monkeypatch.setattr(job.repo, "get_analysis", lambda *_a, **_k: _async({}))
    monkeypatch.setattr(egm.repo, "get_analysis", lambda *_a, **_k: _async({}))
    monkeypatch.setattr(job.repo, "get_asset_for_user",
                        lambda *_a, **_k: _async({"id": "base-w", "r2_key": "base/w.png"}))

    class _Conn:
        async def commit(self):
            return None

        async def rollback(self):
            return None

    class _Ctx:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *_exc):
            return False

    class _Pool:
        def connection(self):
            return _Ctx()

    class _App:
        class state:
            pool = _Pool()
            r2 = None

    asyncio.run(job.run_editor_garment_mask_job(
        _App(), {"id": "j1", "project_id": "p1", "user_id": "u1",
                 "lease_token": "lt", "payload": {"cutId": "A-1"}}))
    return finished


def test_a_transient_sam_outage_is_not_a_permanent_verdict(monkeypatch):
    """unavailable 은 done+state 로 끝나며 라우트가 state 로 재시도를 판정한다."""
    finished = _run_outage_job(
        monkeypatch, raise_exc=sam_client.SamUnavailable("connect timeout"))

    assert finished["status"] == "done", "error 종결은 이 잡을 영구히 죽인다"
    assert finished["result"]["state"] == "unavailable"
    assert "retryable" not in finished["result"]
    assert "retry" not in finished["result"]


@pytest.mark.parametrize("code", egm.TONE_MASK_RETRYABLE_CODES)
def test_sam_200_infrastructure_failures_are_retryable(monkeypatch, code):
    """전송은 성공했어도 모델·R2·추론 장애면 영구 판정으로 내리지 않는다."""
    finished = _run_outage_job(
        monkeypatch,
        result=sam_client.WornGarmentResult(ready=False, code=code, message="temporary"),
    )
    assert finished["status"] == "done"
    assert finished["result"]["state"] == "unavailable"
    assert finished["result"]["code"] == code


@pytest.mark.parametrize("code", ["source_rejected", "no_garment_candidate"])
def test_sam_input_verdicts_are_not_retried(monkeypatch, code):
    finished = _run_outage_job(
        monkeypatch,
        result=sam_client.WornGarmentResult(ready=False, code=code, message="verdict"),
    )
    assert finished["result"]["state"] == "failed"


# ── ① 서비스: 재시도마다 새 잡 신원 ─────────────────────────────────────────

def test_mask_job_key_carries_the_retry_generation():
    """재시도가 실제로 돌려면 키가 달라야 한다 — 같으면 죽은 잡에 합류만 한다."""
    base = egm.mask_job_key("p1", "A-1")
    assert egm.mask_job_key("p1", "A-1", retry=0) == base
    assert egm.mask_job_key("p1", "A-1", retry=1) != base
    assert egm.mask_job_key("p1", "A-1", retry=1) != egm.mask_job_key("p1", "A-1", retry=2)


# ── ① 라우트: 폴링이 재시도를 몰고 간다 ────────────────────────────────────

def _terminal_job(state, retry, *, code=None, finished_at=None):
    return {"id": f"job-{state}-{retry}", "status": "done",
            "payload": {"cutId": "A-1", "retry": retry},
            "finished_at": finished_at or datetime.now(timezone.utc) - timedelta(minutes=10),
            "result": {"state": state, "cutId": "A-1",
                       **({"retryable": True} if state in ("unavailable", "unverified") else {}),
                       **({"code": code} if code else {})}}


def _tone_query(client, make_token, monkeypatch, *, seeded_jobs):
    """톤 에디터 상태 조회 한 번. 잡 저장소는 멱등 합류를 흉내 낸다."""
    store = dict(seeded_jobs)
    class _Created(list):
        pass

    created = _Created()
    created.attempted = []

    async def fake_create_job(_conn, **kw):
        key = kw["idempotency_key"]
        created.attempted.append(key)
        if key in store:
            return store[key], False
        row = {"id": f"job-new-{len(created)}", "status": "pending",
               "payload": kw["payload"], "result": None}
        store[key] = row
        created.append({"key": key, "payload": kw["payload"]})
        return row, True

    async def fake_latest(_conn, _user_id, base_key):
        candidates = [row for key, row in store.items()
                      if key == base_key or key.startswith(f"{base_key}:r")]
        return max(candidates, key=lambda row: int((row.get("payload") or {}).get("retry") or 0),
                   default=None)

    monkeypatch.setattr(routes.repo, "get_project", lambda _c, _u, pid: _async({"id": pid}))
    monkeypatch.setattr(routes.repo, "get_mannequin_cut_asset",
                        lambda *_a, **_k: _async({"id": "cut-asset-1", "r2_key": "ai/c.jpg"}))
    monkeypatch.setattr(routes.repo, "get_product",
                        lambda *_a, **_k: _async({"clothing_type": "top"}))
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(routes.repo, "get_latest_job_generation", fake_latest)
    monkeypatch.setattr(routes.editor_garment_mask, "find_for_cut",
                        lambda *_a, **_k: _async(None))
    monkeypatch.setattr(routes.editor_garment_mask, "matching_side_for_project",
                        lambda *_a, **_k: _async(None))
    monkeypatch.setattr(routes.mannequin_tone_render, "active_for_cut",
                        lambda *_a, **_k: _async(None))
    monkeypatch.setattr(routes, "_tone_editor_enabled", lambda _req: True)
    monkeypatch.setattr(routes, "_r2", lambda _req: object())
    patch_route_db(monkeypatch, routes)

    res = client.get("/v1/projects/p1/mannequins/A-1/tone-editor",
                     headers={"Authorization": f"Bearer {make_token()}"})
    return res, created


def test_a_transient_outage_keeps_preparing_and_queues_a_retry(client, make_token, monkeypatch):
    """이번 사고의 화면 그 자체 — unavailable 하나로 '지원 안 함'이 뜨면 안 된다."""
    res, created = _tone_query(client, make_token, monkeypatch, seeded_jobs={
        egm.mask_job_key("p1", "A-1"): _terminal_job("unavailable", 0)})

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "processing", "재시도가 남아 있는 동안은 준비 중이다"
    assert [c["key"] for c in created] == [egm.mask_job_key("p1", "A-1", retry=1)]
    assert created[0]["payload"]["retry"] == 1
    assert created[0]["payload"]["cutId"] == "A-1"


def test_retry_generation_waits_for_its_backoff_before_advancing(
        client, make_token, monkeypatch):
    """직전 잡이 막 끝났다면 연속 폴이 재시도 예산을 즉시 태우면 안 된다."""
    res, created = _tone_query(client, make_token, monkeypatch, seeded_jobs={
        egm.mask_job_key("p1", "A-1"): _terminal_job(
            "unavailable", 0, finished_at=datetime.now(timezone.utc))})

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "processing"
    assert created == []


def test_retry_generation_advances_after_its_backoff(client, make_token, monkeypatch):
    """최소 간격이 지난 뒤에는 다음 세대를 정확히 하나 만든다."""
    res, created = _tone_query(client, make_token, monkeypatch, seeded_jobs={
        egm.mask_job_key("p1", "A-1"): _terminal_job(
            "unavailable", 0, finished_at=datetime.now(timezone.utc) - timedelta(seconds=16))})

    assert res.status_code == 200, res.text
    assert [c["key"] for c in created] == [egm.mask_job_key("p1", "A-1", retry=1)]


def test_lease_recovery_error_without_result_advances_generation(
        client, make_token, monkeypatch):
    """서버 재시작으로 result 없이 error 난 잡은 판정 실패가 아니므로 복구한다."""
    dead = {"id": "job-dead", "status": "error", "payload": {"cutId": "A-1"},
            "result": None, "finished_at": datetime.now(timezone.utc) - timedelta(seconds=16)}
    res, created = _tone_query(client, make_token, monkeypatch, seeded_jobs={
        egm.mask_job_key("p1", "A-1"): dead})

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "processing"
    assert [c["key"] for c in created] == [egm.mask_job_key("p1", "A-1", retry=1)]


def test_retries_stop_at_the_budget_and_only_then_admit_failure(client, make_token, monkeypatch):
    """무한 재시도는 SAM 을 다시 눕힌다 — 예산이 다하면 실패를 인정한다."""
    seeded = {egm.mask_job_key("p1", "A-1", retry=n): _terminal_job("unavailable", n)
              for n in range(egm.TONE_MASK_MAX_RETRIES + 1)}
    res, created = _tone_query(client, make_token, monkeypatch, seeded_jobs=seeded)

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "failed"
    assert created == []
    assert len(created.attempted) <= 1, "최신 세대부터 확인해 폴마다 r0부터 다시 읽지 않는다"


def test_a_true_no_garment_verdict_is_not_retried(client, make_token, monkeypatch):
    """판정 실패는 판정이다 — 같은 컷을 다시 돌려도 같은 답이 나온다. 재시도 금지."""
    res, created = _tone_query(client, make_token, monkeypatch, seeded_jobs={
        egm.mask_job_key("p1", "A-1"): _terminal_job("failed", 0,
                                                     code="no_garment_candidate")})

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "failed"
    assert created == []


def test_upgrade_mask_uses_retry_generations_and_keeps_the_existing_mask(monkeypatch):
    """업그레이드 일시 실패도 새 세대로 재시도하되 현재 ready 마스크는 유지한다."""
    product_key = "uploads/front-current.png"
    base = egm.mask_job_key("p1", "A-1", product_key=product_key)
    store = {base: _terminal_job(
        "unverified", 0, finished_at=datetime.now(timezone.utc) - timedelta(seconds=16))}
    created = []

    async def fake_create_job(_conn, **kw):
        key = kw["idempotency_key"]
        if key in store:
            return store[key], False
        row = {"id": "upgrade-r1", "status": "pending", "payload": kw["payload"]}
        store[key] = row
        created.append(kw)
        return row, True

    async def fake_latest(_conn, _user_id, base_key):
        candidates = [row for key, row in store.items()
                      if key == base_key or key.startswith(f"{base_key}:r")]
        return max(candidates, key=lambda row: int((row.get("payload") or {}).get("retry") or 0),
                   default=None)

    class _Conn:
        async def commit(self):
            return None

        async def rollback(self):
            return None

    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(routes.repo, "get_latest_job_generation", fake_latest)
    queued = asyncio.run(routes._enqueue_tone_mask_upgrade(
        _Conn(), user_id="u1", project_id="p1", cut_id="A-1",
        state={"status": "ready", "_upgrade": "product-reference",
               "_productKey": product_key, "maskAssetId": "existing-mask"}))

    assert queued is True
    assert [row["idempotency_key"] for row in created] == [
        egm.mask_job_key("p1", "A-1", product_key=product_key, retry=1)]
    assert created[0]["payload"] == {"cutId": "A-1", "retry": 1}


# ── ③ 워커 큐잉 직후 디스패처 기상 ─────────────────────────────────────────

def test_saving_cuts_relies_on_the_dispatcher_reclaim_after_worker_return():
    """직렬 워커 안의 wake는 효과가 없고, 반환 직후 디스패처가 다시 claim한다."""
    from app.workers import mannequin_job as mj

    created = []

    class _Conn:
        async def commit(self):
            return None

    class _Ctx:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *_exc):
            return False

    class _Pool:
        def connection(self):
            return _Ctx()

    async def fake_create_job(_conn, **kw):
        created.append(kw)
        return {"id": "j"}, True

    class _S:
        mannequin_tone_editor = "on"

    import unittest.mock as um
    with um.patch.object(mj.repo, "create_job", fake_create_job):
        asyncio.run(mj._enqueue_editor_garment_mask(
            _Pool(), _S(), user_id="u1", project_id="p1",
            cuts=[{"id": "A-1"}], cut_metadata={}))

    assert len(created) == 1
    assert "app" not in inspect.signature(mj._enqueue_editor_garment_mask).parameters
