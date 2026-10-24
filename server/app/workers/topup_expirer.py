"""12개월 유효기간이 지난 추가구매 크레딧을 정리한다."""

from .. import repo
from .subscription_biller import _PeriodicWorker

_LOCK_KEY = (0x5542, 4)
_BATCH = 50


class TopupExpirer(_PeriodicWorker):
    name = "topup-expirer"

    async def tick(self, conn) -> dict:
        async with conn.cursor() as cur:
            await cur.execute("select pg_try_advisory_lock(%s, %s) as locked", _LOCK_KEY)
            if not (await cur.fetchone())["locked"]:
                return {"skipped": "locked"}
        users = expired = deferred = 0
        try:
            for row in await repo.list_due_topup_users(conn, _BATCH):
                result = await repo.expire_due_topup_buckets(conn, user_id=row["user_id"])
                if result["deferred"]:
                    deferred += 1
                else:
                    expired += result["expired"]
                await conn.commit()
                users += 1
        except BaseException:
            # SQL 오류 뒤 트랜잭션이 aborted 상태면 session advisory unlock도 실패한다.
            # 먼저 rollback해 연결을 정상화한 뒤 finally에서 락을 푼다.
            await conn.rollback()
            raise
        finally:
            async with conn.cursor() as cur:
                await cur.execute("select pg_advisory_unlock(%s, %s)", _LOCK_KEY)
        return {"users": users, "expired": expired, "deferred": deferred}
