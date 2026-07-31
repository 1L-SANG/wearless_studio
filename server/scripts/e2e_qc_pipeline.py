"""QC 파이프라인 E2E — 실 워커 경로로 생성해 4축 점수·D축이 실제로 산출되는지 확인.

단위 테스트는 배선이 **연결됐는지**만 본다. 이 스크립트는 실제 Gemini 생성을 돌려
`mannequin_cuts.qc_scores` 에 값이 실려 나오는지, D축이 첫 컷에서는 null·기존 컷이 있을
때는 채워지는지를 실물로 확인한다(플랜 Phase 0~3 수용기준의 마지막 관문).

`run_mannequin_job` 을 인프로세스로 셀프-클레임 실행한다 — 다른 dispatcher 가 job 을
가로채면 검증 대상 코드가 아예 안 돌기 때문(smoke_realwire.InlineWorker 와 같은 이유).

실행:
    cd server && DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
      IMAGE_QC=shadow .venv/bin/python -m scripts.e2e_qc_pipeline --projects 2

비용: 프로젝트당 생성 1~2콜 + image_qc 1콜 + series_qc 1콜.
"""
import argparse
import asyncio
import json
import pathlib
import uuid

from scripts._env import load_env

load_env()

from app import repo  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.workers.mannequin_job import run_mannequin_job  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "ab_out/e2e_qc"

# 분석·상품 이미지가 있고 생성이 가능한 프로젝트. 기존 컷 수를 함께 뽑아 D축 기대값을 판정한다.
_PICK_SQL = """
select p.id::text as project_id, p.user_id::text as user_id, pr.clothing_type,
       (select count(*) from mannequin_cuts mc where mc.project_id = p.id) as existing_cuts
from projects p
join products pr on pr.project_id = p.id
where exists (select 1 from analyses a where a.project_id = p.id)
  and jsonb_array_length(coalesce(pr.colors, '[]'::jsonb)) > 0
  and p.deleted_at is null
order by existing_cuts desc, p.created_at desc
limit %s
"""


async def _run_one(app, pool, row: dict) -> dict:
    pid, uid = row["project_id"], row["user_id"]
    async with pool.connection() as conn:
        before = await repo.list_mannequin_cuts(conn, uid, pid)
        job, created = await repo.create_job(
            conn, user_id=uid, project_id=pid, kind="mannequin", payload={},
            idempotency_key=None, credits_reserved=0, metadata={"e2e": "qc_pipeline"})
        await conn.commit()
    if not created:
        return {"project_id": pid, "status": "skipped", "reason": "활성 잡 중복"}

    lease = f"e2e-qc:{uuid.uuid4()}"
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"update jobs set status='running', locked_by=%s, locked_at=now(), "
                f"started_at=coalesce(started_at, now()) "
                f"where id=%s and status='pending' returning {repo._JOB_COLS}, "
                f"locked_by as lease_token",
                (lease, job["id"]))
            claimed = await cur.fetchone()
        await conn.commit()
    if claimed is None:
        return {"project_id": pid, "status": "stolen", "reason": "타 dispatcher 선점"}

    await run_mannequin_job(app, claimed)

    async with pool.connection() as conn:
        after = await repo.list_mannequin_cuts(conn, uid, pid)
        job_row = await repo.get_job(conn, uid, job["id"])
    fresh = [c for c in after if len(after) > len(before)][-1:] if after else []
    return {
        "project_id": pid,
        "clothing_type": row["clothing_type"],
        "existing_cuts_before": row["existing_cuts"],
        "status": job_row.get("status"),
        "error": job_row.get("error_message"),
        "cut": {
            "id": f"{fresh[0]['candidate']}-{fresh[0]['version']}",
            "qc_scores": fresh[0].get("qc_scores"),
            "r2_key": fresh[0].get("r2_key"),
        } if fresh else None,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", type=int, default=2, help="생성할 프로젝트 수")
    args = ap.parse_args()

    import psycopg
    from psycopg.rows import dict_row
    from types import SimpleNamespace

    from app.agents.gemini_image import GeminiImageClient
    from app.db import create_pool
    from app.r2 import R2Client

    s = load_settings()
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"[e2e] IMAGE_QC={s.image_qc} · 임계 auto_pass={s.qc_score_auto_pass} "
          f"review={s.qc_score_review}")

    with psycopg.connect(s.database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(_PICK_SQL, (args.projects,))
        rows = cur.fetchall()
    if not rows:
        print("[e2e] 대상 프로젝트 없음")
        return 1
    print(f"[e2e] 대상 {len(rows)}건: "
          + ", ".join(f"{r['project_id'][:8]}({r['clothing_type']},컷{r['existing_cuts']})"
                      for r in rows))

    pool = create_pool(s.database_url)
    await pool.open()
    app = SimpleNamespace(state=SimpleNamespace(
        settings=s, pool=pool, r2=R2Client(s), gemini=GeminiImageClient(s)))
    results = []
    try:
        for row in rows:
            print(f"\n[e2e] {row['project_id'][:8]} 생성 시작 "
                  f"(기존 컷 {row['existing_cuts']}장)")
            res = await _run_one(app, pool, row)
            results.append(res)
            print(f"  status={res['status']}")
            if res.get("cut"):
                print(f"  qc_scores={json.dumps(res['cut']['qc_scores'], ensure_ascii=False)}")
            elif res.get("error"):
                print(f"  error={res['error']}")
    finally:
        await pool.close()

    (OUT / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 수용기준 판정 ────────────────────────────────────────────────────────
    print("\n[e2e] 수용기준 판정")
    scored = [r for r in results if (r.get("cut") or {}).get("qc_scores")]
    print(f"  4축 점수 산출: {len(scored)}/{len(results)}")
    for r in scored:
        q = r["cut"]["qc_scores"]
        d, expected = q.get("series_consistency"), r["existing_cuts_before"] > 0
        mark = "OK" if (d is not None) == expected else "MISMATCH"
        print(f"  {r['project_id'][:8]} 기존컷={r['existing_cuts_before']} "
              f"D축={d} 기대={'채워짐' if expected else 'null'} [{mark}] outcome={q.get('outcome')}")
    print(f"  → {OUT / 'results.json'}")
    return 0 if scored else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
