"""층③ 앵커 워커 — 상한 없는 재시도가 고아 잡 하나를 880회 돌린 전례가 있다(2026-09-01).

검증 4개:
  1. 성공하면 chain_status='confirmed' 로 미러된다.
  2. 이미 체인에 있으면(중복 revert) 재기록 없이 화해한다.
  3. 진짜로 체인에 없으면(중복이 아닌 실패) retry 로 빠진다 — anchored 로 오판하지 않는다.
     (fix round 1: 이 테스트가 없으면 화해 검사를 통째로 지운 뮤턴트도 나머지 3개를 통과한다.)
  4. attempts 상한을 넘으면 dead 로 빠진다 — 무한 재시도 금지.
"""
import asyncio

import pytest

from app.workers import fm_publication_anchor as anchor


class FakeChain:
    def __init__(self, fail_record=False, already=False):
        self.chain_id = 1337
        self.provenance_enabled = True
        self.record_calls = []
        self.fail_record = fail_record
        self._store = {}
        if already:
            self._store["p1"] = {
                "image_hash": "aa" * 32, "license_ref": "0x" + "bb" * 32,
                "block": 42, "exists": True,
            }

    def record_publication(self, *, publication_id, image_sha256, license_id):
        self.record_calls.append(publication_id)
        if self.fail_record:
            raise RuntimeError("duplicate publication id")
        self._store[publication_id] = {
            "image_hash": image_sha256, "license_ref": "0x" + "bb" * 32,
            "block": 42, "exists": True,
        }
        return {"tx_hash": "0x" + "cd" * 32, "block": 42, "chain_id": self.chain_id,
                "image_hash": image_sha256, "license_ref": "0x" + "bb" * 32}

    def wait_for_publication(self, publication_id, timeout=None):
        return self._store.get(publication_id)

    def get_publication(self, publication_id):
        return self._store.get(publication_id, {"exists": False})


class Cur:
    def __init__(self, job):
        self.job = job
        self.statements = []
        self._last = job

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))
        self._last = self.job

    async def fetchone(self):
        return self._last


class Conn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    async def commit(self):
        return None


JOB = {
    "publication_id": "p1", "attempts": 0,
    "image_sha256": "aa" * 32, "license_ref": "l1",
}


def test_anchor_one_confirms_on_success():
    chain = FakeChain()
    cur = Cur(JOB)
    status = asyncio.run(anchor.anchor_one(Conn(cur), chain, dict(JOB)))
    assert status == "anchored"
    assert chain.record_calls == ["p1"]
    assert any("chain_status" in s[0] and "confirmed" in str(s[1]) for s in cur.statements)


def test_anchor_one_reconciles_when_already_on_chain():
    """중복 revert = 이미 기록됨. 재기록하지 않고 미러만 한다."""
    chain = FakeChain(fail_record=True, already=True)
    cur = Cur(JOB)
    status = asyncio.run(anchor.anchor_one(Conn(cur), chain, dict(JOB)))
    assert status == "anchored"


def test_anchor_one_retries_when_genuinely_not_on_chain():
    """실패가 중복 revert 가 아니라 진짜 실패면 retry — 재기록도, anchored 오판도 하지 않는다.

    화해 분기(wait_for_publication/get_publication)를 통째로 지우고 attempts 만으로
    분기하는 뮤턴트를 넣으면: attempts=0 이라 무조건 'anchored' 를 반환해 이 테스트만
    깨진다(나머지 3개는 그대로 통과). task-6-report.md Fix round 1 참고.
    """
    chain = FakeChain(fail_record=True, already=False)
    cur = Cur(JOB)
    status = asyncio.run(anchor.anchor_one(Conn(cur), chain, dict(JOB)))
    assert status == "retry"
    job_update = next(
        s for s in cur.statements if "fm_publication_anchor_jobs" in s[0]
    )
    assert "attempts" in job_update[0] and "last_error" in job_update[0]
    assert job_update[1] == ("retry", 1, "record_failed", "p1")
    # 재기록도 anchored 미러도 없어야 한다 — fm_publication_records 는 손대지 않는다.
    assert not any("chain_status" in s[0] for s in cur.statements)


def test_anchor_one_goes_dead_past_max_attempts():
    chain = FakeChain(fail_record=True)
    job = dict(JOB, attempts=anchor._MAX_ATTEMPTS)
    cur = Cur(job)
    status = asyncio.run(anchor.anchor_one(Conn(cur), chain, job))
    assert status == "dead"
