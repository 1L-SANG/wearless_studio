"""일시 장애를 error 로 적으면 그 잡이 멱등키를 문 채 종착한다 — 영구 누락의 원인.

톤 마스크가 2026-08-18 사고로 이미 배운 것을 sam_preprocess·matching_cutout 에 적용한다.
`error` 종결은 디스패처가 재시도하지 않고(재시도 코드가 존재하지 않는다 — 워커 주석은 있다고
믿고 있었다), 같은 키로 다시 걸면 그 실패 잡에 합류만 한다. `done` + state:unavailable 로
적어야 푸셔가 다음 세대를 걸 수 있다.
"""

import asyncio

import app.workers.matching_cutout_job as mcj
import app.workers.sam_preprocess_job as spj
from app.services import sam_client, sam_retry


class _Settings:
    matching_cutout = "on"
    sam_service_url = "http://sam2:8080"
    sam_internal_token = "t"
    r2_bucket = "b"


def _async(value):
    async def _coro():
        return value
    return _coro()


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


def _capture(monkeypatch, module):
    seen = {}

    async def finalize(_conn, *, job_id, lease_token, status, result):
        seen.update({"status": status, "result": result})

    monkeypatch.setattr(module.repo, "finalize_uncharged_job", finalize)
    return seen


async def _outage(*_a, **_k):
    raise sam_client.SamUnavailable("connection refused")


def test_sam_preprocess_records_an_outage_as_retryable(monkeypatch):
    seen = _capture(monkeypatch, spj)
    monkeypatch.setattr(spj, "load_settings", lambda: _Settings())
    monkeypatch.setattr(spj.sam_client, "configured", lambda _s: True)
    monkeypatch.setattr(spj.sam_client, "segment_garment", _outage)
    monkeypatch.setattr(spj.repo, "get_product", lambda *_a, **_k: _async(
        {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}))
    monkeypatch.setattr(spj.repo, "get_asset_for_user", lambda *_a, **_k: _async(
        {"id": "a1", "r2_key": "users/u/p/front.jpg"}))

    class _App:
        class state:
            pool = _Pool()

    asyncio.run(spj.run_sam_preprocess_job(
        _App(), {"id": "j1", "project_id": "p", "user_id": "u", "lease_token": "t"}))

    assert seen["status"] == "done", "error 로 적으면 멱등키가 시체에 묶인다"
    assert seen["result"]["state"] in sam_retry.RETRYABLE_STATES


def test_matching_cutout_records_an_outage_as_retryable(monkeypatch):
    seen = _capture(monkeypatch, mcj)
    monkeypatch.setattr(mcj.sam_client, "configured", lambda _s: True)
    monkeypatch.setattr(mcj.sam_client, "segment_garment", _outage)

    class _App:
        class state:
            pool = _Pool()
            settings = _Settings()
            r2 = None

    asyncio.run(mcj.run_matching_cutout_job(
        _App(), {"id": "j2", "project_id": "p", "user_id": "u", "lease_token": "t",
                 "payload": {"matchingItemId": "i1", "sourceKeys": ["k1"],
                             "sourceAssetIds": ["a1"]}}))

    assert seen["status"] == "done", "error 로 적으면 그 옷은 영영 누끼가 없다"
    assert seen["result"]["state"] in sam_retry.RETRYABLE_STATES
    assert seen["result"]["matchingItemId"] == "i1"


def test_a_real_cutout_verdict_is_still_not_retryable(monkeypatch):
    """판정 실패(누끼가 안 나옴)는 그대로 failed 다 — 다시 돌려도 같은 답이다."""
    seen = _capture(monkeypatch, mcj)
    monkeypatch.setattr(mcj.sam_client, "configured", lambda _s: True)

    class _View:
        ready = False
        cutout_key = None
        code = "no_garment_candidate"

    monkeypatch.setattr(mcj.sam_client, "segment_garment",
                        lambda *_a, **_k: _async({"Front": _View()}))

    class _App:
        class state:
            pool = _Pool()
            settings = _Settings()
            r2 = None

    asyncio.run(mcj.run_matching_cutout_job(
        _App(), {"id": "j3", "project_id": "p", "user_id": "u", "lease_token": "t",
                 "payload": {"matchingItemId": "i1", "sourceKeys": ["k1"],
                             "sourceAssetIds": ["a1"]}}))

    assert seen["status"] == "done"
    assert seen["result"]["state"] == "failed"
    assert sam_retry.job_is_retryable({"status": "done", "result": seen["result"]}) is False
