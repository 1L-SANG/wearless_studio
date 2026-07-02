"""PL-1 SQL 물리 검증 (pl1_analysis_agent_spec §11-4 · §10.2 비고 ⓐ~ⓓ) — 1회성.

실 DB에 스크래치 project/product/asset을 만들고 **실제 워커**(run_analyze_job —
Gemini·R2 실호출)를 돌려 SQL 레벨 동작을 검증한 뒤 스크래치를 전부 삭제한다:
  ⓓ create_job 활성 중복 합류   ⓒ finalize의 현재 colors 스와치 null-fill 병합
  ⓑ save_analysis 부분 patch가 다른 키 보존   ⓐ 지문 가드 — stale 결과 폐기(무변경)

실행: cd server && .venv/bin/python -m scripts.verify_analysis_db
전제: server/.env (DATABASE_URL·R2·GEMINI_API_KEY). prod 쓰기 — 사용자 승인 후 1회.
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"


def _load_env(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(SERVER / ".env")

from app import repo  # noqa: E402
from app.agents import analysis  # noqa: E402
from app.agents.gemini_text import GeminiTextClient  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.db import create_pool  # noqa: E402
from app.r2 import R2Client, upload_key  # noqa: E402
from app.workers.analyze_job import run_analyze_job  # noqa: E402

USER_EMAIL = "dlftkd3269@gmail.com"
FRONT_IMG = ROOT / "Test image_front.jpeg"
BACK_IMG = ROOT / "Test image_back.jpeg"

_checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    _checks.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


async def main():
    s = load_settings()
    if not (s.database_url and s.gemini_api_key and s.r2_bucket):
        sys.exit("env 미비 (DATABASE_URL / GEMINI_API_KEY / R2_*)")
    r2 = R2Client(s)
    pool = create_pool(s.database_url)
    await pool.open()
    app = SimpleNamespace(state=SimpleNamespace(
        settings=s, pool=pool, r2=r2, gemini_text=GeminiTextClient(s)))

    project_id = None
    asset_ids: list[str] = []
    r2_keys: list[str] = []
    try:
        # ── 0) 스크래치 준비: 사용자·프로젝트·에셋(R2 실업로드)·product ──
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("select id::text as id from auth.users where email = %s",
                                  (USER_EMAIL,))
                row = await cur.fetchone()
            if not row:
                sys.exit(f"사용자 없음: {USER_EMAIL}")
            user_id = row["id"]
            project = await repo.create_project(conn, user_id)
            project_id = project["id"]
            images = []
            for slot, path in (("Front", FRONT_IMG), ("Back", BACK_IMG)):
                data = path.read_bytes()
                aid = str(uuid.uuid4())
                key = upload_key(user_id, project_id, aid, "jpg")
                await asyncio.to_thread(r2.put_bytes, key, data, "image/jpeg")
                await repo.create_asset(
                    conn, asset_id=aid, user_id=user_id, project_id=project_id,
                    source="upload", bucket=s.r2_bucket, key=key, mime="image/jpeg",
                    size=len(data), original_filename=path.name)
                asset_ids.append(aid)
                r2_keys.append(key)
                images.append({"id": aid, "slot": slot, "src": f"/v1/assets/{aid}/file"})
            colors = [
                {"id": "col_v1", "isBase": True, "swatchId": None, "images": images},
                {"id": "col_v2", "isBase": False, "swatchId": "black", "images": []},
            ]
            await repo.save_product(conn, project_id, user_id, {
                "name": "물리검증 티셔츠", "colors": colors, "upload_complete": True})
            await conn.commit()
        print(f"스크래치 준비 완료: project={project_id}")

        # ── ⓓ 활성 중복 합류 ──
        async with pool.connection() as conn:
            product_row = await repo.get_product(conn, project_id)
            fp = analysis.input_fingerprint(product_row)
            job1, created1 = await repo.create_job(
                conn, user_id=user_id, project_id=project_id, kind="analyze",
                payload={}, idempotency_key=None, credits_reserved=0,
                metadata={"agentId": "AG-01", "fingerprint": fp})
            job2, created2 = await repo.create_job(
                conn, user_id=user_id, project_id=project_id, kind="analyze",
                payload={}, idempotency_key=None, credits_reserved=0,
                metadata={"agentId": "AG-01", "fingerprint": fp})
            await conn.commit()
        check("ⓓ 활성 중복 → 합류(같은 job, created=False)",
              created1 and (not created2) and job1["id"] == job2["id"])

        # ── 실제 워커 1회 (claim → Gemini → finalize) ──
        async with pool.connection() as conn:
            claimed = await repo.claim_next_job(conn, ("analyze",), "verify")
            await conn.commit()
        if not claimed or claimed["project_id"] != project_id:
            sys.exit(f"claim 실패/타 프로젝트: {claimed and claimed['project_id']}")
        await run_analyze_job(app, claimed)

        async with pool.connection() as conn:
            job = await repo.get_job(conn, user_id, job1["id"])
            payload = await repo.get_analysis(conn, project_id)
            product_after = await repo.get_product(conn, project_id)
            async with conn.cursor() as cur:
                await cur.execute(
                    "select event_type from job_events where job_id = %s order by id",
                    (job1["id"],))
                events = [r["event_type"] for r in await cur.fetchall()]
        check("워커 성공 종결 (jobs done·progress 100·creditsCharged 0)",
              job["status"] == "done" and job["progress"] == 100
              and (job["result"] or {}).get("creditsCharged") == 0,
              f"status={job['status']}")
        check("지문 실측 기록 (metadata.fingerprint == 입력 지문)",
              (job["metadata"] or {}).get("fingerprint") == fp)
        need = {"subCategory", "targetGenders", "fit", "materials", "sellingPoints",
                "aiSuggestedPoints", "suggestedName", "selectedModelId",
                "matchCandidates", "matchSelections", "locked"}
        check("analyses.payload 전 필드 (§3.5)", need <= set(payload),
              f"missing={sorted(need - set(payload))}" if not need <= set(payload) else
              f"fit={payload.get('fit')} match={len(payload.get('matchCandidates', []))}건")
        check("products.clothing_type 분배 (Product 소유)",
              bool(product_after.get("clothing_type")),
              f"= {product_after.get('clothing_type')}")
        c1 = next(c for c in product_after["colors"] if c["id"] == "col_v1")
        c2 = next(c for c in product_after["colors"] if c["id"] == "col_v2")
        check("ⓒ 스와치 null-fill 병합 (null 그룹만 채움·기지정 불변)",
              c1.get("swatchId") is not None and c2.get("swatchId") == "black",
              f"col_v1={c1.get('swatchId')} col_v2={c2.get('swatchId')}")
        check("done 이벤트 (SSE replay 원본)", "done" in events, f"events={events}")

        # ── ⓑ 부분 patch 병합 (다른 키 보존) ──
        async with pool.connection() as conn:
            await repo.save_analysis(conn, project_id, {"fit": "slim"})
            await conn.commit()
            merged = await repo.get_analysis(conn, project_id)
        check("ⓑ save_analysis 병합 upsert ({fit}만 patch → 나머지 보존)",
              merged.get("fit") == "slim"
              and merged.get("matchCandidates") == payload.get("matchCandidates")
              and merged.get("suggestedName") == payload.get("suggestedName"))

        # ── ⓐ 지문 가드 — stale 결과 폐기 ──
        async with pool.connection() as conn:
            j2, _ = await repo.create_job(
                conn, user_id=user_id, project_id=project_id, kind="analyze",
                payload={}, idempotency_key=None, credits_reserved=0,
                metadata={"agentId": "AG-01", "fingerprint": "stale"})
            await conn.commit()
        async with pool.connection() as conn:
            claimed2 = await repo.claim_next_job(conn, ("analyze",), "verify")
            await conn.commit()
        async with pool.connection() as conn:
            out = await repo.finalize_analyze_success(
                conn, job_id=claimed2["id"], lease_token=claimed2["lease_token"],
                user_id=user_id, project_id=project_id,
                clothing_type="bottom", swatch_suggestions=[],
                payload={"fit": "over"}, metadata={"fingerprint": "stale-actual"},
                actual_fingerprint="stale-actual")  # 현재 product 지문과 불일치 → 폐기돼야 함
            await conn.commit()
        async with pool.connection() as conn:
            j2_after = await repo.get_job(conn, user_id, j2["id"])
            payload_after = await repo.get_analysis(conn, project_id)
            product_guard = await repo.get_product(conn, project_id)
            last_fp = await repo.get_last_analyze_fingerprint(conn, project_id)
        check("ⓐ 지문 가드 — 결과 폐기 (None 반환·job error·superseded)",
              out is None and j2_after["status"] == "error",
              f"error_message={j2_after.get('error_message')}")
        check("ⓐ 가드 폐기 시 analyses·products 무변경",
              payload_after.get("fit") == "slim"
              and product_guard.get("clothing_type") == product_after.get("clothing_type"))
        check("get_last_analyze_fingerprint = done job 지문만 (error 제외)", last_fp == fp)
    finally:
        # ── 정리: project 삭제(products·analyses·jobs·job_events cascade) + asset·R2 ──
        if project_id:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("delete from public.projects where id = %s", (project_id,))
                    if asset_ids:
                        await cur.execute("delete from public.assets where id = any(%s::uuid[])",
                                          (asset_ids,))
                await conn.commit()
            for key in r2_keys:
                try:
                    await asyncio.to_thread(r2.delete, key)
                except Exception as e:
                    print(f"⚠️ R2 정리 실패(수동 삭제 필요): {key} — {e}")
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "select (select count(*) from projects where id = %s) as p, "
                        "(select count(*) from assets where id = any(%s::uuid[])) as a",
                        (project_id, asset_ids or [str(uuid.uuid4())]))
                    left = await cur.fetchone()
            print(f"정리 완료: 잔여 project={left['p']} assets={left['a']}")
        await pool.close()

    failed = [n for n, ok, _ in _checks if not ok]
    print(f"\n{'PASS' if not failed else 'FAIL'} — {len(_checks) - len(failed)}/{len(_checks)} 통과")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
