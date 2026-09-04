"""배포본 온체인 앵커 reconciler (층③).

jobs 테이블을 쓰지 않는다 — jobs_active_unique_idx 가 (project_id, kind) 동시 1건이라
같은 프로젝트에서 연달아 내려받으면 앵커가 서로를 막는다. fm_vc_revocation_reconciler
패턴(전용 큐 + 루프 + 재시도 상한)을 복제한다.
"""

import asyncio
import contextlib
import logging

log = logging.getLogger("wearless.fm_publication_anchor")

_IDLE_SECONDS = 5
_STOP_TIMEOUT_SECONDS = 10
_LEASE_SECONDS = 240
#: 상한 없는 재시도가 고아 잡 하나를 880회 돌린 전례가 있다(2026-09-01 prod 실측).
_MAX_ATTEMPTS = 50
#: record_publication 실패(체인 미확인) 자체는 보통 체인 클라이언트 안에서 최대 90초를
#: 태우므로 스스로 절제되지만, 연결 거부·DNS 실패 같은 즉시-실패 전송 오류는 그 시간을
#: 전혀 안 태우고 곧바로 retry 로 돌아온다 — 백오프가 없으면 짧은 장애 하나가 초 단위로
#: 50번을 다 태우고 dead 로 떨어진다. 전용 next_attempt_at 컬럼(마이그레이션 소유권 밖)
#: 없이, _claim 이 이미 processing 전환 시점에 쓰는 attempted_at 을 재사용한다.
_RETRY_BACKOFF_SECONDS = 15


async def anchor_one(conn, chain, job: dict) -> str:
    """앵커 1건. 반환 = 'anchored' | 'retry' | 'dead'."""
    publication_id = str(job["publication_id"])
    try:
        result = await asyncio.to_thread(
            chain.record_publication,
            publication_id=publication_id,
            image_sha256=job["image_sha256"],
            license_id=str(job["license_ref"]),
        )
    except Exception:
        # 중복 revert 는 "이미 기록됨"이다. 재기록하지 말고 저장값으로 화해한다.
        # 실서비스 wait_for_publication(facemarket_chain.py)은 폴링 중 RPC 예외를 자체적으로
        # {"exists": False} 로 삼켜 raise 하지 않는다고 문서화돼 있지만, 그 보장을 이 함수가
        # 신뢰하고 자기 방어를 안 하면 chain 구현이 바뀌거나 그 내부 가드가 깨지는 순간
        # 예외가 그대로 밖으로 새어나가 _run 의 바깥 except 에 먹히고, 잡은 attempts 증가도
        # last_error 도 없이 lease_until 만료(240s)까지 조용히 processing 에 멈춘다.
        # get_publication 폴백도 같은 장애(체인 엔드포인트 다운)로 raise 할 수 있다. 두 읽기
        # 모두 같은 fail-safe 로 감싼다 — 어느 쪽이 raise 하든 결과는 항상 하나(존재 안 함)로
        # 수렴해야 attempts/retry/dead 회계가 절대 우회되지 않는다.
        try:
            stored = await asyncio.to_thread(chain.wait_for_publication, publication_id, 5.0)
        except Exception:
            stored = None
        if not stored or not stored.get("exists"):
            try:
                stored = await asyncio.to_thread(chain.get_publication, publication_id)
            except Exception:
                stored = {"exists": False}
        if not stored or not stored.get("exists"):
            attempts = int(job.get("attempts") or 0) + 1
            # fm_vc_revocation_reconciler 와 같은 경계: job["attempts"] + 1 >= _MAX_ATTEMPTS.
            # 두 큐에서 "상한"이 같은 총 시도 횟수(50)를 뜻하게 맞춘다.
            status = "dead" if attempts >= _MAX_ATTEMPTS else "retry"
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_publication_anchor_jobs
                          set status = %s, attempts = %s, last_error = %s, lease_until = null
                        where publication_id = %s""",
                    (status, attempts, "record_failed", publication_id),
                )
            if status == "dead":
                async with conn.cursor() as cur:
                    await cur.execute(
                        "update fm_publication_records set chain_status = 'failed' "
                        "where id = %s",
                        (publication_id,),
                    )
                log.error("publication anchor gave up (dead): %s", publication_id)
            await conn.commit()
            return status
        result = {
            "tx_hash": None, "block": stored["block"], "chain_id": chain.chain_id,
        }

    async with conn.cursor() as cur:
        await cur.execute(
            """update fm_publication_records
                  set chain_status = %s, tx_hash = %s, chain_id = %s,
                      recorded_block = %s
                where id = %s""",
            ("confirmed", result.get("tx_hash"), str(result.get("chain_id")),
             result.get("block"), publication_id),
        )
        await cur.execute(
            "update fm_publication_anchor_jobs set status = 'anchored', lease_until = null "
            "where publication_id = %s",
            (publication_id,),
        )
    await conn.commit()
    return "anchored"


class PublicationAnchorReconciler:
    def __init__(self, app):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(), name="facemarket-publication-anchor"
        )

    async def stop(self):
        self._stop.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_STOP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        except asyncio.CancelledError:
            # stop() 자체가 취소된 것 — sibling(fm_vc_revocation_reconciler) 처럼 task 도
            # 취소하고 정리한 뒤, 이 코루틴의 취소는 삼키지 않고 그대로 전파한다.
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            raise
        finally:
            if task.done():
                self._task = None

    async def _claim(self, conn) -> dict | None:
        """lease 로 한 건 집는다. 만료 lease 는 회수한다(크래시 복구).

        결정: 만료 lease 회수는 attempts 를 올리지 않는다 — sibling(fm_vc_revocation_reconciler)
        은 별도 스윕에서 만료 lease 를 명시적 실패 시도로 센다. 여기서는 다르게 간다: 이
        재수집은 "체인이 그 잡을 거절했다"가 아니라 "이 프로세스가 죽었다"는 신호라, 잡의
        잘못이 아닌 인프라 사고로 attempts 예산을 태우지 않는다. 다만 반복 크래시 루프에는
        이 큐만으로는 상한이 없다는 뜻이므로, 그런 패턴은 lease_until 회수 로그(_run 의
        warning)로 드러나야 한다.
        """
        async with conn.cursor() as cur:
            await cur.execute(
                f"""update fm_publication_anchor_jobs j
                       set status = 'processing',
                           lease_until = now() + interval '{_LEASE_SECONDS} seconds',
                           attempted_at = now()
                      from fm_publication_records r
                     where r.id = j.publication_id
                       and j.publication_id = (
                             select publication_id from fm_publication_anchor_jobs
                              where status = 'pending'
                                 or (status = 'retry' and (
                                       attempted_at is null
                                       or attempted_at < now()
                                            - interval '{_RETRY_BACKOFF_SECONDS} seconds'))
                                 or (status = 'processing' and lease_until < now())
                              order by created_at
                              for update skip locked
                              limit 1)
                 returning j.publication_id::text as publication_id, j.attempts,
                           r.image_sha256, r.license_ref::text as license_ref"""
            )
            row = await cur.fetchone()
        await conn.commit()
        return row

    async def _run(self):
        while not self._stop.is_set():
            chain = getattr(self.app.state, "fm_chain", None)
            if chain is None or not getattr(chain, "provenance_enabled", False):
                await self._sleep()
                continue
            try:
                async with self.app.state.pool.connection() as conn:
                    job = await self._claim(conn)
                    if job is None:
                        await self._sleep()
                        continue
                    await anchor_one(conn, chain, job)
            except Exception:
                log.warning("publication anchor sweep failed", exc_info=True)
                await self._sleep()

    async def _sleep(self):
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=_IDLE_SECONDS)
