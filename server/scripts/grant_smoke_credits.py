"""스모크 계정(qa-smoke@wearless.kr) 크레딧 지급 — repo.grant_subscription 재사용(원장 불변식 유지).

실작동 스모크(P1 유료 구간) 전용. 실사용자 계정은 건드리지 않는다.
실행: cd server && .venv/bin/python -m scripts.grant_smoke_credits [--plan basic] [--email qa-smoke@wearless.kr]
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from scripts.smoke_realwire import _load_env  # noqa: E402 (동일 .env 로더)

_load_env(SERVER / ".env")

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app import repo  # noqa: E402


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="qa-smoke@wearless.kr")
    ap.add_argument("--plan", default="basic")
    args = ap.parse_args()

    async with await psycopg.AsyncConnection.connect(
        os.environ["DATABASE_URL"], row_factory=dict_row
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute("select id::text as id from auth.users where email = %s", (args.email,))
            row = await cur.fetchone()
            if not row:
                sys.exit(f"사용자 없음: {args.email} — smoke_realwire 를 먼저 1회 실행해 계정을 만들어 주세요.")
            uid = row["id"]
            # grant_subscription 은 credit_accounts 행을 전제(FOR UPDATE) — 없으면 0/0 로 보장
            await cur.execute(
                "insert into credit_accounts (user_id, balance, reserved) values (%s, 0, 0) "
                "on conflict (user_id) do nothing",
                (uid,),
            )
        granted = await repo.grant_subscription(
            conn, user_id=uid, plan_code=args.plan,
            metadata={"reason": "realwire-smoke", "by": "scripts/grant_smoke_credits"},
        )
        await conn.commit()
        sources = await repo.list_credit_sources(conn, uid)
        active = sum(s.get("remainingCredits", 0) for s in sources if s.get("status") == "active")
        print(f"지급 완료: user={args.email} plan={args.plan} → active 합={active}")
        print(f"grant 결과: {granted}")


if __name__ == "__main__":
    asyncio.run(main())
