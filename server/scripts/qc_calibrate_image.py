"""AG-P2 의류 동일성 QC 캘리브레이션 배치 (플랜 Phase 0 — 판정 정확도 실측).

기존 `mannequin_cuts`(= 확정 출고된 생성물)를 순회하며 상품 원본 + 생성컷으로
`image_qc.verdict()` 를 실행해 판정 분포를 모은다. **읽기 전용** — DB 쓰기 0, R2 쓰기 0.
산출물은 `server/ab_out/imageqc_calib/results.jsonl` 뿐이다.

왜 필요한가: `IMAGE_QC` 가 매니페스트에 없어 프로덕션에서 동일성 QC 가 무측정이었다.
게이팅(enforce)으로 올리기 전에 **판정이 얼마나 자주 발화하는지**부터 알아야 한다 —
`MANNEQUIN_QC_ENABLED` 가 오탐으로 pass율 0% 를 내 전 생성을 막았던 2026-07-07 전례.

**측정 범위 주의**: 이 배치가 내는 것은 pass/retry **발화 분포**이지 정확도가 아니다.
거짓양성률(정상 컷을 retry 로 판정)·거짓음성률을 계산하려면 사람이 붙인 정답 라벨이
있어야 한다. enforce 승격 근거로 쓰려면 여기 산출물 위에 육안 채점을 얹어야 한다.

실행:
    cd server && .venv/bin/python -m scripts.qc_calibrate_image [--limit 30] [--force]

전제: server/.env(DATABASE_URL·R2·GEMINI_API_KEY). 판정 1건당 vision 콜 1회 발생.
"""
import argparse
import asyncio
import json
import pathlib

import psycopg
from psycopg.rows import dict_row

from scripts._env import load_env

load_env()

from app.agents import mannequin  # noqa: E402
from app.agents.gemini_image import InlineImage  # noqa: E402
from app.agents import image_qc  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402

OUT_DIR = pathlib.Path(__file__).resolve().parents[1] / "ab_out/imageqc_calib"
RESULTS = OUT_DIR / "results.jsonl"

# 확정 컷 + 소유자 + 상품(기준색 이미지 선택용 colors) 을 한 번에. asset 은 생성컷 자체.
_CUTS_SQL = """
select mc.id::text        as cut_id,
       mc.project_id::text as project_id,
       mc.candidate, mc.version,
       p.user_id::text    as user_id,
       pr.clothing_type,
       pr.colors,
       a.r2_bucket        as cut_bucket,
       a.r2_key           as cut_key,
       a.mime_type        as cut_mime
from mannequin_cuts mc
join assets a   on a.id = mc.asset_id
join projects p on p.id = mc.project_id
left join products pr on pr.project_id = mc.project_id
where a.deleted_at is null
  and mc.id::text <> all(%s)
order by mc.created_at desc
limit %s
"""


def _load_done() -> set[str]:
    """이미 판정한 cut_id (멱등 — 재실행 시 vision 콜 낭비 방지)."""
    if not RESULTS.exists():
        return set()
    done = set()
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            done.add(json.loads(line)["cut_id"])
        except (json.JSONDecodeError, KeyError):
            continue  # 손상 줄은 건너뛴다 — 배치가 죽는 것보다 재판정이 낫다
    return done


def _product_image_assets(cur, row: dict) -> list[dict]:
    """워커와 동일한 기준색 이미지 선택(mannequin.base_color_images) → asset 행들.

    워커 경로(mannequin_job)가 쓰는 것과 같은 순서·같은 집합이어야 판정이 재현된다.
    """
    pairs = mannequin.base_color_images({"colors": row.get("colors") or []})
    if not pairs:
        return []
    ids = [aid for _slot, aid in pairs]
    cur.execute(
        "select id::text, r2_bucket, r2_key, mime_type from assets "
        "where id = any(%s::uuid[]) and deleted_at is null",
        (ids,),
    )
    by_id = {a["id"]: a for a in cur.fetchall()}
    return [by_id[aid] for aid in ids if aid in by_id]  # 원래 slot 순서 보존


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40, help="판정할 최근 컷 수 (기본 40)")
    ap.add_argument("--force", action="store_true", help="이미 판정한 컷도 재판정")
    args = ap.parse_args()

    s = load_settings()
    assert s.database_url, "DATABASE_URL 필요 (server/.env)"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done = set() if args.force else _load_done()
    r2_cache: dict[str, R2Client] = {}

    def r2_for(bucket: str) -> R2Client:
        if bucket not in r2_cache:
            r2_cache[bucket] = R2Client(s, bucket=bucket)
        return r2_cache[bucket]

    with psycopg.connect(s.database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
        # 판정 완료분을 SQL 에서 제외한 뒤 LIMIT — 파이썬에서 걸러내면 재실행이 같은 N건만
        # 다시 집어와 미처리 컷으로 영영 진행하지 못한다.
        cur.execute(_CUTS_SQL, (list(done) or [""], args.limit))
        rows = cur.fetchall()
        print(f"[qc_calibrate_image] 미판정 대상 {len(rows)}건 (판정완료 {len(done)}건 제외)")
        judged, skipped, failed = 0, 0, 0
        counts: dict[str, int] = {}
        with RESULTS.open("a", encoding="utf-8") as sink:
            for row in rows:
                cid = row["cut_id"]
                if cid in done:
                    skipped += 1
                    continue
                prod_assets = _product_image_assets(cur, row)
                if not prod_assets:
                    # 워커도 prod_imgs 없으면 판정을 스킵한다(`and prod_imgs`) — 동일 규약.
                    print(f"  SKIP {cid[:8]}: 기준색 상품 이미지 없음")
                    skipped += 1
                    continue
                try:
                    prod_imgs = [
                        InlineImage(a["mime_type"], r2_for(a["r2_bucket"]).get_bytes(a["r2_key"]))
                        for a in prod_assets
                    ]
                    cut_img = InlineImage(
                        row["cut_mime"], r2_for(row["cut_bucket"]).get_bytes(row["cut_key"]))
                except Exception as e:  # R2 미스 — 다음 실행에서 재시도되도록 기록 안 함
                    print(f"  SKIP {cid[:8]}: R2 로드 실패 {e!r}")
                    failed += 1
                    continue
                try:
                    p2 = await image_qc.verdict(s, prod_imgs, cut_img)
                except Exception as e:
                    # 판정 실패도 분포의 일부다 — 성공만 기록하면 생존 편향이 생긴다.
                    # jsonl 에도 남긴다: 출력만 하면 누적 결과에서 실패율이 통째로 사라진다.
                    sink.write(json.dumps({
                        "cut_id": cid, "project_id": row["project_id"],
                        "clothing_type": row["clothing_type"], "verdict": "error",
                        "error": type(e).__name__, "message": str(e)[:200],
                    }, ensure_ascii=False) + "\n")
                    sink.flush()
                    print(f"  FAIL {cid[:8]}: {type(e).__name__} {str(e)[:80]}")
                    failed += 1
                    counts["error"] = counts.get("error", 0) + 1
                    continue
                rec = {
                    "cut_id": cid,
                    "project_id": row["project_id"],
                    "candidate": row["candidate"],
                    "version": row["version"],
                    "clothing_type": row["clothing_type"],
                    "product_image_count": len(prod_imgs),
                    "verdict": p2.get("verdict"),
                    "mismatches": p2.get("mismatches") or [],
                    "correctionPrompt": p2.get("correctionPrompt"),
                }
                sink.write(json.dumps(rec, ensure_ascii=False) + "\n")
                sink.flush()  # 중단돼도 진척 보존(멱등)
                judged += 1
                counts[rec["verdict"]] = counts.get(rec["verdict"], 0) + 1
                mm = len(rec["mismatches"])
                print(f"  {rec['verdict']:5} {cid[:8]} {row['clothing_type'] or '-':7} mismatches={mm}")

    total = sum(v for k, v in counts.items() if k != "error")
    print(f"\n[qc_calibrate_image] 판정 {judged} · 스킵 {skipped} · 실패 {failed}")
    print(f"  verdict 분포: {counts}")
    if total:
        pass_n = counts.get("pass", 0)
        print(f"  pass율 {pass_n}/{total} = {pass_n / total:.1%}")
    print(f"  → {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
