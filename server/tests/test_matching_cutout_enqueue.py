"""커스텀 매칭 등록 커밋 후 matching_cutout 잡 enqueue 계약.

헬퍼 자체(무과금·플래그 off no-op·payload·실패 삼킴)는 직접 돌려서 검증하고,
"커밋 뒤에 부른다"는 호출 순서만 소스 구조로 확인한다(라우트 전체 통합은 스토리지·DB·
AI 목킹이 과해 test_custom_match_item.py 쪽에 있다).
"""
import asyncio
import pathlib
import types

import app.routes as routes
from app.services import matching_cutout

SERVER = pathlib.Path(__file__).resolve().parents[1]


class _Conn:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _enqueue(monkeypatch, flag, *, boom=False):
    created = []

    async def fake_create_job(conn, **kwargs):
        created.append(kwargs)
        if boom:
            raise RuntimeError("queue down")

    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    conn = _Conn()
    asyncio.run(routes._enqueue_matching_cutout(
        conn,
        settings=types.SimpleNamespace(matching_cutout=flag),
        user_id="u1", project_id="p1", matching_item_id="custom_x",
        source_asset_ids=["a1", "a2"],
        source_keys=["users/u1/projects/p1/uploads/a1.jpg",
                     "users/u1/projects/p1/uploads/a2.jpg"],
        grid_asset_id="grid-1",
    ))
    return created, conn


def test_enqueue_is_uncharged_and_carries_cleanup_handles(monkeypatch):
    created, conn = _enqueue(monkeypatch, "on")

    assert len(created) == 1
    job = created[0]
    assert job["kind"] == "matching_cutout"
    assert job["credits_reserved"] == 0, "무과금"
    assert job["idempotency_key"].endswith(matching_cutout.ALGORITHM_VERSION)
    assert job["payload"] == {
        "matchingItemId": "custom_x",
        "sourceAssetIds": ["a1", "a2"],
        "sourceKeys": ["users/u1/projects/p1/uploads/a1.jpg",
                       "users/u1/projects/p1/uploads/a2.jpg"],
        # 스왑 뒤 삭제가 원본 grid 까지 회수하려면 워커가 그 id 를 알아야 한다(리뷰 I4)
        "gridAssetId": "grid-1",
    }
    assert conn.commits == 1


# 리뷰 I3 — 플래그 off 프로덕션에서 잡 행이 쌓이면 안 된다. enqueue 와 워커 skip 사이에
# match-candidates 가 'processing' 을 돌려 셀러 이미지를 스켈레톤으로 가리는 창도 없앤다.
def test_enqueue_is_a_complete_no_op_when_flag_is_off(monkeypatch):
    created, conn = _enqueue(monkeypatch, "off")
    assert created == []
    assert (conn.commits, conn.rollbacks) == (0, 0)

    missing_flag, _ = _enqueue(monkeypatch, None)
    assert missing_flag == []


def test_enqueue_failure_never_reaches_the_caller(monkeypatch):
    created, conn = _enqueue(monkeypatch, "on", boom=True)
    assert len(created) == 1, "시도는 했다"
    assert conn.rollbacks == 1, "실패한 트랜잭션은 되돌린다"


def test_enqueue_call_site_is_after_the_registration_commit():
    src = (SERVER / "app" / "routes.py").read_text(encoding="utf-8")
    call = src.index("await _enqueue_matching_cutout(")
    commit_before = src.rfind("await conn.commit()", 0, call)
    insert = src.rfind("insert_custom_matching_item", 0, call)
    assert insert < commit_before < call, "enqueue 는 insert·커밋 뒤"
