"""이미지 생성 실비 리포트 — 확인된 금액과 미확인 호출을 절대 섞지 않는다.

실행: cd server && .venv/bin/python -m scripts.image_cost_report [--days 7]
실서버: --database-url 'postgresql://...' 또는 PROD_DATABASE_URL 환경변수.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(SERVER / ".env")

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app.agents.image_cost import PRICES  # noqa: E402
from app.config import load_settings  # noqa: E402

KRW = load_settings().image_usage_krw_per_usd


def _won(usd) -> str:
    return "미확인" if usd is None else f"{float(usd) * KRW:,.0f}원"


def _usd(usd, places: int = 2) -> str:
    return "미확인" if usd is None else f"${float(usd):,.{places}f}"


def _one_k_counterfactual(
    model: str, calls: int, actual_usd, output_image_tokens: int,
) -> float | None:
    """입력·텍스트 비용은 유지하고 4K 이미지 토큰만 해당 모델의 1K로 바꾼다."""
    price = PRICES.get(model)
    if price is None or actual_usd is None or "1K" not in price.image_output_tokens:
        return None
    image_rate = price.output_image_usd_per_mtok
    non_image_usd = float(actual_usd) - output_image_tokens * image_rate / 1_000_000
    one_k_image_usd = calls * price.image_output_tokens["1K"] * image_rate / 1_000_000
    return max(non_image_usd, 0.0) + one_k_image_usd


def _line(char: str = "─", n: int = 88) -> None:
    print(char * n)


def _dsn(explicit: str | None) -> str:
    dsn = explicit or os.getenv("PROD_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        sys.exit("DB 주소를 못 찾았습니다. --database-url 또는 DATABASE_URL을 설정해 주세요.")
    return dsn


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="집계 기간(일)")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    since = f"{args.days} days"

    try:
        conn = await psycopg.AsyncConnection.connect(
            _dsn(args.database_url), row_factory=dict_row)
    except psycopg.OperationalError as exc:
        sys.exit(f"DB에 접속하지 못했습니다 — {str(exc).strip()[:200]}\n"
                 "로컬 DB는 Docker와 `supabase start`, 실서버는 --database-url이 필요합니다.")

    async with conn, conn.cursor() as cur:
        try:
            await cur.execute("select 1 from image_usage_events limit 1")
        except psycopg.errors.UndefinedTable:
            sys.exit("image_usage_events 테이블이 없습니다. 마이그레이션을 먼저 적용해 주세요.")

        await cur.execute(
            "select count(*) as calls, count(usd) as priced_calls, sum(usd) as known_usd, "
            "count(*) filter (where usd is null) as unknown_calls, "
            "count(*) filter (where cost_source = 'table') as estimated, "
            "count(*) filter (where not has_image) as no_image "
            "from image_usage_events where created_at > now() - %s::interval", (since,))
        total = await cur.fetchone()

        print()
        _line("═")
        print(f"이미지 생성 실비 — 최근 {args.days}일 (환산 {KRW:,.0f}원/$)")
        _line("═")
        print(f"총 API 호출 {total['calls']:,}회   확인된 금액 "
              f"{_usd(total['known_usd'])} ({_won(total['known_usd'])})")
        if total["priced_calls"]:
            average = float(total["known_usd"]) / total["priced_calls"]
            print(f"금액 확인 호출 평균 {_usd(average, 4)} ({_won(average)}) "
                  f"— {total['priced_calls']:,}건 기준")
        if total["unknown_calls"]:
            print(f"⚠ 금액 미확인 {total['unknown_calls']:,}건 — 확인된 총액에 포함하지 않았습니다")
        if total["estimated"]:
            print(f"⚠ {total['estimated']:,}건은 실제 토큰 대신 요금표로 추정했습니다")
        if total["no_image"]:
            print(f"⚠ {total['no_image']:,}건은 200 응답이었지만 이미지가 오지 않았습니다")
        if not total["calls"]:
            print("\n아직 기록이 없습니다.")
            return

        print()
        _line()
        print("모델 × 해상도별")
        _line()
        await cur.execute(
            "select model, image_size, count(*) as calls, count(usd) as priced_calls, "
            "count(*) filter (where usd is null) as unknown_calls, sum(usd) as known_usd, "
            "avg(usd) as avg_usd, avg(latency_ms) as ms "
            "from image_usage_events where created_at > now() - %s::interval "
            "group by 1, 2 order by known_usd desc nulls last", (since,))
        print(f"{'모델':28} {'크기':5} {'호출':>6} {'확인평균':>11} {'확인합계':>12} "
              f"{'미확인':>7} {'지연':>7}")
        for row in await cur.fetchall():
            latency = "-" if row["ms"] is None else f"{float(row['ms']) / 1000:.1f}s"
            print(f"{row['model'][:28]:28} {row['image_size']:5} {row['calls']:>6,} "
                  f"{_usd(row['avg_usd'], 4):>11} {_usd(row['known_usd']):>12} "
                  f"{row['unknown_calls']:>7,} {latency:>7}")

        print()
        _line()
        print("잡 종류 × 최종 상태별 — 성공(done)과 실패(error)를 분리")
        _line()
        await cur.execute(
            """
            with per_job as (
              select e.stage, e.job_id, coalesce(j.status, 'missing') as job_status,
                     count(*) as calls, sum(e.usd) as known_usd,
                     count(*) filter (where e.usd is null) as unknown_calls
              from image_usage_events e left join jobs j on j.id = e.job_id
              where e.created_at > now() - %s::interval and e.job_id is not null
              group by 1, 2, 3
            )
            select stage, job_status, count(*) as jobs, avg(calls) as avg_calls,
                   avg(known_usd) filter (where unknown_calls = 0) as avg_usd,
                   max(known_usd) filter (where unknown_calls = 0) as max_usd,
                   sum(known_usd) as known_usd, sum(unknown_calls) as unknown_calls
            from per_job group by 1, 2 order by known_usd desc nulls last
            """, (since,))
        print(f"{'잡 종류':20} {'상태':9} {'잡':>5} {'잡당호출':>9} {'완전확인 평균':>13} "
              f"{'확인합계':>11} {'미확인':>7}")
        for row in await cur.fetchall():
            print(f"{(row['stage'] or '-')[:20]:20} {row['job_status'][:9]:9} "
                  f"{row['jobs']:>5,} {float(row['avg_calls']):>9.1f} "
                  f"{_usd(row['avg_usd'], 4):>13} {_usd(row['known_usd']):>11} "
                  f"{row['unknown_calls']:>7,}")

        print()
        _line()
        print("4K 승급 비용 — 모델별 1K 단가로 다시 계산")
        _line()
        await cur.execute(
            "select count(*) filter (where image_size = '4K') as calls_4k, "
            "count(*) as calls_all, sum(usd) filter (where image_size = '4K') as known_usd_4k, "
            "count(*) filter (where image_size = '4K' and usd is null) as unknown_4k "
            "from image_usage_events where created_at > now() - %s::interval", (since,))
        four_k = await cur.fetchone()
        share = four_k["calls_4k"] / four_k["calls_all"] * 100 if four_k["calls_all"] else 0
        print(f"4K 요청 {four_k['calls_4k']:,}회 ({share:.1f}%)   확인된 금액 "
              f"{_usd(four_k['known_usd_4k'])}")
        if four_k["unknown_4k"]:
            print(f"⚠ 4K 금액 미확인 {four_k['unknown_4k']:,}건")

        await cur.execute(
            "select model, count(*) as calls, sum(usd) as actual_usd, "
            "sum(output_image_tokens) as image_tokens "
            "from image_usage_events "
            "where created_at > now() - %s::interval and image_size = '4K' "
            "and usd is not null and output_image_tokens > 0 group by 1 order by 1", (since,))
        actual_total = counterfactual_total = 0.0
        comparable_calls = 0
        for row in await cur.fetchall():
            one_k = _one_k_counterfactual(
                row["model"], row["calls"], row["actual_usd"], row["image_tokens"])
            if one_k is None:
                print(f"{row['model']}: 1K 비교 단가 미확인")
                continue
            actual = float(row["actual_usd"])
            actual_total += actual
            counterfactual_total += one_k
            comparable_calls += row["calls"]
            print(f"{row['model']}: {row['calls']:,}회 실제 {_usd(actual)} → "
                  f"1K였다면 {_usd(one_k)} (추가 {_usd(actual - one_k)})")
        if comparable_calls:
            extra = actual_total - counterfactual_total
            print(f"비교 가능한 {comparable_calls:,}회 승급 추가분 {_usd(extra)} ({_won(extra)})")

        print()
        _line()
        print("일자별")
        _line()
        await cur.execute(
            "select created_at::date as day, count(*) as calls, sum(usd) as known_usd, "
            "count(*) filter (where usd is null) as unknown_calls "
            "from image_usage_events where created_at > now() - %s::interval "
            "group by 1 order by 1 desc", (since,))
        for row in await cur.fetchall():
            print(f"{row['day']}  호출 {row['calls']:>6,}회  확인 {_usd(row['known_usd']):>10}  "
                  f"미확인 {row['unknown_calls']:>5,}건")
        print()


if __name__ == "__main__":
    asyncio.run(main())
