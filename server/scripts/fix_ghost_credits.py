"""유령 잔액 복구 — balance는 있는데 active 버킷이 0이라 402가 나는 계정 정상화.

증상: 화면엔 크레딧이 떠도(=credit_accounts.balance) 생성 시 "크레딧이 부족해요"(402).
원인: 예약(reserve)은 active credit_sources 버킷을 보는데, 버킷 없이 balance만 남은 불일치.
처리: ① 유령 balance를 원장 행과 함께 0으로 정산(원장-잔액 일관성 유지)
     ② repo.grant_subscription으로 정식 지급(버킷+잔액+원장 일관 생성) ③ 불변식 검증.

실행: cd server && .venv/bin/python -m scripts.fix_ghost_credits --email daily13y@gmail.com [--plan basic]
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from scripts.smoke_realwire import _load_env  # noqa: E402

_load_env(SERVER / ".env")

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Json  # noqa: E402

from app import repo  # noqa: E402


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--plan", default="basic")
    args = ap.parse_args()

    async with await psycopg.AsyncConnection.connect(
        os.environ["DATABASE_URL"], row_factory=dict_row
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute("select id::text as id from auth.users where email=%s", (args.email,))
            row = await cur.fetchone()
            if not row:
                sys.exit(f"사용자 없음: {args.email}")
            uid = row["id"]
            await cur.execute(
                "insert into credit_accounts (user_id, balance, reserved) values (%s,0,0) "
                "on conflict (user_id) do nothing", (uid,))
            await cur.execute("select balance, reserved from credit_accounts where user_id=%s for update", (uid,))
            acct = await cur.fetchone()
            await cur.execute(
                "select coalesce(sum(remaining_credits),0) as s from credit_sources "
                "where user_id=%s and status='active' and period_end > now()", (uid,))
            bucket_sum = (await cur.fetchone())["s"]
            ghost = acct["balance"] - bucket_sum
            print(f"현재: balance={acct['balance']} reserved={acct['reserved']} bucket_sum={bucket_sum} (유령={ghost})")
            if ghost > 0:
                await cur.execute(
                    "insert into credit_ledger (user_id, action_key, delta, balance_after, available_after, metadata) "
                    "values (%s, 'reconcile.ghost_balance', %s, %s, %s, %s)",
                    (uid, -ghost, bucket_sum, bucket_sum - acct["reserved"],
                     Json({"reason": "active 버킷 합과 balance 불일치 정산", "by": "scripts/fix_ghost_credits"})))
                await cur.execute("update credit_accounts set balance=%s where user_id=%s", (bucket_sum, uid))
                print(f"유령 잔액 {ghost} 정산 ✓ (balance {acct['balance']}→{bucket_sum})")
        granted = await repo.grant_subscription(
            conn, user_id=uid, plan_code=args.plan,
            metadata={"reason": "ghost-fix regrant", "by": "scripts/fix_ghost_credits"})
        await conn.commit()
        print(f"지급 ✓ plan={args.plan}: {granted}")
        async with conn.cursor() as cur:
            await cur.execute(
                "select ca.balance, ca.reserved, (select coalesce(sum(remaining_credits),0) from credit_sources cs "
                "where cs.user_id=%s and cs.status='active' and cs.period_end > now()) as bucket_sum "
                "from credit_accounts ca where ca.user_id=%s", (uid, uid))
            fin = await cur.fetchone()
            ok = fin["balance"] == fin["bucket_sum"]
            print(f"최종: {fin} — 불변식(balance==bucket_sum) {'✓' if ok else '❌'}")


if __name__ == "__main__":
    asyncio.run(main())
