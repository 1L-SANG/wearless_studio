"""층③ 앵커 워커 — 상한 없는 재시도가 고아 잡 하나를 880회 돌린 전례가 있다(2026-09-01).

검증 6개:
  1. 성공하면 chain_status='confirmed' 로 미러된다.
  2. 이미 체인에 있으면(중복 revert) 재기록 없이 화해한다.
  3. 진짜로 체인에 없으면(중복이 아닌 실패) retry 로 빠진다 — anchored 로 오판하지 않는다.
     (fix round 1: 이 테스트가 없으면 화해 검사를 통째로 지운 뮤턴트도 나머지 3개를 통과한다.)
  4. 화해 읽기(wait_for_publication·get_publication) 자체가 raise 해도 anchor_one 밖으로
     새지 않고 retry 로 흡수된다 — 체인 엔드포인트가 죽어 있을 때의 실제 장애 모드.
     (fix round 2: get_publication 폴백의 try/except 만 지워도 이 테스트만 raise 로 깨진다.)
  5. 정산과 같은 advisory lock 을 다른 프로세스가 쥐고 있으면 서명하지 않는다 — attempts 를
     안 태우고 retry 로 큐에 돌려준다. api 와 detail-worker 가 같은 owner 키로 동시에
     서명하면(threading.Lock 은 프로세스 내부에서만 직렬화) nonce 가 충돌해 하나가 조용히
     유실된다(fix round 3, 2026-09-04 교차-task 리뷰).
  6. attempts 상한을 넘으면 dead 로 빠진다 — 무한 재시도 금지.
"""
import asyncio

import pytest

from app.workers import fm_publication_anchor as anchor


class FakeChain:
    def __init__(self, fail_record=False, already=False, raise_on_read=False):
        self.chain_id = 1337
        self.provenance_enabled = True
        self.record_calls = []
        self.fail_record = fail_record
        # 화해 읽기(wait_for_publication·get_publication) 둘 다를 raise 시킨다 — 체인
        # 엔드포인트 자체가 죽어 있는 장애 모드를 재현한다(fix round 2).
        self.raise_on_read = raise_on_read
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
        if self.raise_on_read:
            raise ConnectionError("rpc endpoint unreachable")
        return self._store.get(publication_id)

    def get_publication(self, publication_id):
        if self.raise_on_read:
            raise ConnectionError("rpc endpoint unreachable")
        return self._store.get(publication_id, {"exists": False})


class Cur:
    def __init__(self, job, lock_result=True):
        self.job = job
        self.statements = []
        self._last = job
        # pg_try_advisory_lock 시뮬레이션 — 기존 5개 테스트는 손대지 않아도 되도록 기본은
        # "잡힘"(True). 경합 테스트만 False 를 넘긴다(fix round 3).
        self.lock_result = lock_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.statements.append((normalized, params))
        if "pg_try_advisory_lock" in normalized:
            self._last = {"locked": self.lock_result}
        else:
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


def test_anchor_one_retries_when_chain_reads_raise():
    """체인 엔드포인트 자체가 죽어 있으면 wait_for_publication·get_publication 둘 다
    raise 할 수 있다 — 그래도 anchor_one 밖으로 새면 안 된다. retry 로 흡수해 attempts 를
    올리고 lease 를 풀어야 다음 스윕이 다시 시도한다.

    fix round 1 에서 추가한 get_publication 폴백의 try/except 만 지우면(wait_for_publication
    가드는 그대로 두고) 이 테스트는 raise 로 깨진다 — assert 실패가 아니라 예외 자체가
    pytest 를 뚫고 나온다. task-6-report.md Fix round 2 참고.
    """
    chain = FakeChain(fail_record=True, raise_on_read=True)
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


def test_anchor_one_yields_without_signing_when_lock_held():
    """다른 프로세스(정산이든 다른 앵커든)가 같은 advisory lock 을 쥐고 있으면 서명하지
    않는다 — attempts 를 태우지 않고 row 를 retry 로 돌려 다음 스윕이 다시 집게 한다.

    api 와 detail-worker 가 같은 owner 개인키로 서명하는 두 프로세스인데
    FaceMarketChain._nonce_lock 은 threading.Lock(프로세스 내부에서만 직렬화)이라, advisory
    lock 없이는 둘이 동시에 같은 nonce 로 서명해 하나가 조용히 유실된다 — 정산 브로드캐스트와
    도 충돌한다(같은 키). fix round 3, 2026-09-04 교차-task 리뷰가 잡은 결함.
    """
    chain = FakeChain()
    cur = Cur(JOB, lock_result=False)
    status = asyncio.run(anchor.anchor_one(Conn(cur), chain, dict(JOB)))
    assert status == "retry"
    # 서명 시도 자체가 없어야 한다 — record_publication 을 호출하지 않는다.
    assert chain.record_calls == []
    job_update = next(
        s for s in cur.statements if "fm_publication_anchor_jobs" in s[0]
    )
    # lock 경합은 실패한 시도가 아니다 — attempts 컬럼을 건드리지 않는다.
    assert "attempts" not in job_update[0]
    assert job_update[1] == ("p1",)
    # 서명도, chain_status 갱신도 없어야 한다.
    assert not any("chain_status" in s[0] for s in cur.statements)


def test_anchor_one_goes_dead_past_max_attempts():
    chain = FakeChain(fail_record=True)
    job = dict(JOB, attempts=anchor._MAX_ATTEMPTS)
    cur = Cur(job)
    status = asyncio.run(anchor.anchor_one(Conn(cur), chain, job))
    assert status == "dead"
