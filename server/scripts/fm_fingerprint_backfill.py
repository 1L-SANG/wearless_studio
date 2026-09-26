"""추적 층 지문 백필 — 기존 REAL 컷(fm_output_records)·배포본(fm_publication_records)에 지문을 채운다.

2026-09-26 추적 층이 들어오기 전에 만들어진 컷·배포본은 지문이 없다(prod 기준 컷 105 · 배포본 1).
새 컷은 워커가 종결 뒤에 채우지만(베스트에포트), 그때 빠진 것도 이 스크립트가 채운다.

- **기본은 dry-run** — R2 에서 바이트를 읽어 지문을 계산하고 "넣을 행 수"만 출력한다. DB 쓰기 없음.
- `--apply` 를 줘야 insert 한다(on conflict do nothing — 몇 번 돌려도 같은 결과).
- 워터마크는 **소급하지 않는다** — 이미 셀러에게 나간 파일은 바꿀 수 없다. 기존 배포본은 지문만.
- 이미지 바이트·해시값은 출력하지 않는다(개수·id 만).

실행:
    cd server && .venv/bin/python -m scripts.fm_fingerprint_backfill              # dry-run
    cd server && .venv/bin/python -m scripts.fm_fingerprint_backfill --apply      # 실제 기록
전제: server/.env(DATABASE_URL·R2). **prod 쓰기(--apply)는 사용자 승인 후.**
"""
import argparse
import sys

import psycopg
from psycopg.rows import dict_row

from scripts._env import load_env

load_env()

from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402
from app.services import fm_fingerprint, fm_publication_mark  # noqa: E402

CUTS_SQL = """
select r.id::text as id, a.r2_bucket, a.r2_key
  from fm_output_records r
  join assets a on a.id = r.asset_id
 where a.r2_key is not null
   and not exists (select 1 from fm_image_fingerprints f
                    where f.output_record_id = r.id and f.kind = 'cut')
 order by r.created_at
 limit %s
"""

PUBLICATIONS_SQL = """
select p.id::text as id, p.kind, p.r2_key
  from fm_publication_records p
 where p.r2_key is not null
   and not exists (select 1 from fm_image_fingerprints f where f.publication_id = p.id)
 order by p.created_at
 limit %s
"""

INSERT_CUT = (
    "insert into fm_image_fingerprints (output_record_id, kind, phash, dhash) "
    "values (%s, 'cut', %s, %s) on conflict do nothing"
)
INSERT_PUBLICATION = (
    "insert into fm_image_fingerprints "
    "(publication_id, kind, region_y0, region_y1, phash, dhash) "
    "values (%s, %s, %s, %s, %s, %s) on conflict do nothing"
)


def backfill_cuts(conn, r2_for, *, limit: int, apply: bool) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute(CUTS_SQL, (limit,))
        todo = cur.fetchall()
    ok = failed = 0
    for row in todo:
        try:
            data = r2_for(row["r2_bucket"]).get_bytes(row["r2_key"])
        except Exception as e:  # noqa: BLE001 — 한 장 실패로 전체를 멈추지 않는다
            print(f"  컷 {row['id']}: R2 읽기 실패 ({type(e).__name__})")
            failed += 1
            continue
        fp = fm_fingerprint.safe_image_hashes(data)
        if fp is None:
            print(f"  컷 {row['id']}: 지문 계산 실패")
            failed += 1
            continue
        ok += 1
        if apply:
            with conn.cursor() as cur:
                cur.execute(INSERT_CUT, (row["id"], fm_fingerprint.to_signed(fp["phash"]),
                                         fm_fingerprint.to_signed(fp["dhash"])))
            conn.commit()
    print(f"[cuts] 대상 {len(todo)} · 계산 {ok} · 실패 {failed}")
    return ok, failed


def backfill_publications(conn, r2, *, limit: int, apply: bool) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute(PUBLICATIONS_SQL, (limit,))
        todo = cur.fetchall()
    ok = failed = 0
    rows_total = 0
    for row in todo:
        try:
            data = r2.get_bytes(row["r2_key"])
        except Exception as e:  # noqa: BLE001
            print(f"  배포본 {row['id']}: R2 읽기 실패 ({type(e).__name__})")
            failed += 1
            continue
        # code=None → 워터마크 없이 지문만(소급 워터마크 금지 — 이미 나간 파일과 달라진다).
        result = fm_publication_mark.mark_publication(data, row["kind"], None)
        if not result.fingerprints:
            print(f"  배포본 {row['id']}: 지문 계산 실패")
            failed += 1
            continue
        ok += 1
        rows_total += len(result.fingerprints)
        print(f"  배포본 {row['id']}: 지문 {len(result.fingerprints)}행")
        if apply:
            with conn.cursor() as cur:
                cur.executemany(INSERT_PUBLICATION, [
                    (row["id"], fp["kind"], fp["region_y0"], fp["region_y1"],
                     fm_fingerprint.to_signed(fp["phash"]), fm_fingerprint.to_signed(fp["dhash"]))
                    for fp in result.fingerprints
                ])
            conn.commit()
    print(f"[publications] 대상 {len(todo)} · 계산 {ok} (지문 {rows_total}행) · 실패 {failed}")
    return ok, failed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--apply", action="store_true", help="실제로 insert 한다(기본 dry-run)")
    ap.add_argument("--only", choices=("all", "cuts", "publications"), default="all")
    ap.add_argument("--limit", type=int, default=1000)
    args = ap.parse_args(argv)

    s = load_settings()
    if not s.database_url:
        print("DATABASE_URL 필요", file=sys.stderr)
        return 2
    r2 = R2Client(s)
    clients = {s.r2_bucket: r2}

    def r2_for(bucket):   # 자산 행이 가리키는 버킷을 그대로 따른다(컷은 보통 기본 버킷)
        bucket = bucket or s.r2_bucket
        if bucket not in clients:
            clients[bucket] = R2Client(s, bucket=bucket)
        return clients[bucket]

    mode = "APPLY" if args.apply else "dry-run"
    print(f"[fm_fingerprint_backfill] mode={mode} only={args.only} limit={args.limit}")
    failed = 0
    with psycopg.connect(s.database_url, row_factory=dict_row) as conn:
        if args.only in ("all", "cuts"):
            failed += backfill_cuts(conn, r2_for, limit=args.limit, apply=args.apply)[1]
        if args.only in ("all", "publications"):
            failed += backfill_publications(conn, r2, limit=args.limit, apply=args.apply)[1]
    if not args.apply:
        print("[fm_fingerprint_backfill] dry-run — DB 미변경. 기록하려면 --apply")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
