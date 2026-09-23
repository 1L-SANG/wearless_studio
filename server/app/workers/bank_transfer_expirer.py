"""계좌이체 신청·수동 이용권 만료 — 지시서 docs/superpowers/plans/2026-09-22-bank-transfer-payments.md §10-7.

토스 설정(subscription_billing_enabled)과 **독립**으로 돈다. 판매 스위치를 꺼도 이미 준 이용권은
종료일에 정리해야 한다. 기존 SubscriptionExpirer 와 같은 형태(주기 루프 + advisory lock)이고
그 파일은 손대지 않는다 — 공통 루프만 빌려 쓴다.
"""

import logging

from .. import bank_transfer_service as service
from .subscription_biller import _PeriodicWorker

log = logging.getLogger("wearless.bank_transfer_expirer")

#: 다른 advisory 락과 겹치지 않는 값(subscription_biller 는 (0x5542, 1)·(0x5542, 2)).
_LOCK_KEY = (0x5542, 3)


class BankTransferExpirer(_PeriodicWorker):
    name = "bank-transfer-expirer"

    async def tick(self, conn) -> dict:
        async with conn.cursor() as cur:
            await cur.execute("select pg_try_advisory_lock(%s, %s) as locked", _LOCK_KEY)
            if not (await cur.fetchone())["locked"]:
                return {"skipped": "locked"}
        try:
            expired_requests = await service.expire_stale_requests(conn)
            await conn.commit()
            grants = await service.expire_manual_grants(conn)
        finally:
            async with conn.cursor() as cur:
                await cur.execute("select pg_advisory_unlock(%s, %s)", _LOCK_KEY)
        stats = {"expiredRequests": expired_requests, **grants}
        if expired_requests or grants.get("ended") or grants.get("skipped"):
            log.info("bank transfer expirer %s", stats)
        return stats
