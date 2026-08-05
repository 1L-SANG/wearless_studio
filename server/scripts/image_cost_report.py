"""이미지 생성 실비 리포트 (내부용) — image_usage_events 집계.

"완성본 1장에 얼마 썼나"를 요금표 추정이 아니라 실제 청구 토큰으로 답한다.
QC 재생성·best-of 후보처럼 출고되지 않은 호출도 원장에 있으므로, 잡 1건당 합계가
곧 셀러 눈에 보이는 결과물 한 세트의 원가다.

실행: cd server && .venv/bin/python -m scripts.image_cost_report [--days 7]

기본 접속지는 .env 의 DATABASE_URL(로컬)이다. 실서버 수치를 보려면 접속 문자열을 넘긴다:
  --database-url 'postgresql://...'  또는  PROD_DATABASE_URL 환경변수.
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

from app.config import load_settings  # noqa: E402

KRW = load_settings().image_usage_krw_per_usd


def _won(usd) -> str:
    return f"{float(usd or 0) * KRW:,.0f}원"


def _line(char="─", n=78) -> None:
    print(char * n)


def _dsn(explicit: str | None) -> str:
    dsn = explicit or os.getenv("PROD_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        sys.exit("DB 주소를 못 찾았습니다. --database-url 로 넘기거나 .env 의 DATABASE_URL 을 "
                 "설정해 주세요.")
    return dsn


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="집계 기간(일)")
    ap.add_argument("--database-url", default=None,
                    help="접속 문자열. 없으면 PROD_DATABASE_URL → .env 의 DATABASE_URL 순")
    args = ap.parse_args()
    since = f"{args.days} days"

    try:
        conn = await psycopg.AsyncConnection.connect(_dsn(args.database_url), row_factory=dict_row)
    except psycopg.OperationalError as exc:
        sys.exit(f"DB 에 접속하지 못했습니다 — {str(exc).strip()[:200]}\n"
                 "로컬 DB(127.0.0.1:54322)를 보려면 Docker + `supabase start` 가 떠 있어야 하고, "
                 "실서버 수치는 --database-url 로 운영 DB 주소를 넘겨 주세요.")

    async with conn, conn.cursor() as cur:
        try:
            await cur.execute("select 1 from image_usage_events limit 1")
        except psycopg.errors.UndefinedTable:
            sys.exit("image_usage_events 테이블이 없습니다 — "
                     "supabase/migrations/20260804000000_image_usage_events.sql 을 먼저 실행해 주세요.")
        await cur.execute(
            "select count(*) as calls, coalesce(sum(usd), 0) as usd, "
            "count(*) filter (where cost_source <> 'usage') as estimated, "
            "count(*) filter (where not has_image) as no_image "
            "from image_usage_events where created_at > now() - %s::interval", (since,))
        total = await cur.fetchone()

        print()
        _line("═")
        print(f"이미지 생성 실비 — 최근 {args.days}일 (환산 {KRW:,.0f}원/$)")
        _line("═")
        print(f"총 API 호출 {total['calls']:,}회   총액 ${float(total['usd']):.2f} "
              f"({_won(total['usd'])})")
        if total["calls"]:
            print(f"호출 1회 평균 ${float(total['usd']) / total['calls']:.4f} "
                  f"({_won(float(total['usd']) / total['calls'])})")
        if total["estimated"]:
            print(f"⚠ {total['estimated']}건은 응답에 토큰이 없어 요금표로 추정한 값입니다")
        if total["no_image"]:
            print(f"⚠ {total['no_image']}건은 과금됐지만 이미지가 안 온 호출입니다(순손실)")
        if not total["calls"]:
            print("\n아직 기록이 없습니다 — 마이그레이션 적용 후 생성을 1회 돌려 주세요.")
            return

        print()
        _line()
        print("모델 × 해상도별")
        _line()
        await cur.execute(
            "select model, image_size, count(*) as calls, coalesce(sum(usd), 0) as usd, "
            "coalesce(avg(usd), 0) as avg_usd, coalesce(avg(latency_ms), 0) as ms "
            "from image_usage_events where created_at > now() - %s::interval "
            "group by 1, 2 order by usd desc", (since,))
        print(f"{'모델':28} {'해상도':6} {'호출':>7} {'1회당':>10} {'합계':>12} {'지연':>8}")
        for r in await cur.fetchall():
            print(f"{r['model'][:28]:28} {r['image_size']:6} {r['calls']:>7,} "
                  f"${float(r['avg_usd']):>9.4f} ${float(r['usd']):>11.2f} "
                  f"{float(r['ms']) / 1000:>7.1f}s")

        print()
        _line()
        print("잡 종류별 — 셀러가 결과 1세트를 받을 때 드는 실비")
        _line()
        await cur.execute(
            """
            with per_job as (
              select stage, job_id, sum(usd) as usd, count(*) as calls
              from image_usage_events
              where created_at > now() - %s::interval and job_id is not null
              group by 1, 2
            )
            select stage, count(*) as jobs, avg(usd) as avg_usd, max(usd) as max_usd,
                   avg(calls) as avg_calls, sum(usd) as usd
            from per_job group by 1 order by usd desc
            """, (since,))
        print(f"{'잡 종류':22} {'잡 수':>6} {'잡당 호출':>9} {'잡당 평균':>11} "
              f"{'잡당 최대':>11} {'원화(평균)':>12}")
        for r in await cur.fetchall():
            print(f"{(r['stage'] or '-')[:22]:22} {r['jobs']:>6,} "
                  f"{float(r['avg_calls']):>9.1f} ${float(r['avg_usd']):>10.4f} "
                  f"${float(r['max_usd']):>10.4f} {_won(r['avg_usd']):>12}")

        print()
        _line()
        print("4K 승급 비중 — 패턴 상품 판정이 과탐이면 여기서 돈이 샌다")
        _line()
        await cur.execute(
            "select coalesce(sum(usd) filter (where image_size = '4K'), 0) as usd_4k, "
            "count(*) filter (where image_size = '4K') as calls_4k, "
            "coalesce(sum(usd), 0) as usd_all, count(*) as calls_all "
            "from image_usage_events where created_at > now() - %s::interval", (since,))
        r = await cur.fetchone()
        if r["calls_all"]:
            share_c = r["calls_4k"] / r["calls_all"] * 100
            share_u = float(r["usd_4k"]) / max(float(r["usd_all"]), 1e-9) * 100
            print(f"4K 호출 {r['calls_4k']:,}회 ({share_c:.1f}%) → 비용 ${float(r['usd_4k']):.2f} "
                  f"({share_u:.1f}%)")
            # 같은 호출을 1K 로 냈다면 얼마였을지 — 승급 결정의 기회비용.
            print(f"같은 호출을 1K 로 냈다면 ${r['calls_4k'] * 0.134:.2f} "
                  f"→ 승급 추가분 ${float(r['usd_4k']) - r['calls_4k'] * 0.134:.2f} "
                  f"({_won(float(r['usd_4k']) - r['calls_4k'] * 0.134)})")

        print()
        _line()
        print("일자별")
        _line()
        await cur.execute(
            "select created_at::date as day, count(*) as calls, coalesce(sum(usd), 0) as usd "
            "from image_usage_events where created_at > now() - %s::interval "
            "group by 1 order by 1 desc", (since,))
        for r in await cur.fetchall():
            print(f"{r['day']}  호출 {r['calls']:>6,}회  ${float(r['usd']):>8.2f}  "
                  f"{_won(r['usd']):>12}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
