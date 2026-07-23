"""T3 Step 2 — 종류별 프로젝트 시드 + 실 AG-01 분석 (실 워커 경로).

codex 반영: analysis payload 를 **손으로 만들지 않는다**. 실 상품이미지를 업로드하고
`run_analyze_job`(prod 워커)을 인프로세스로 돌려 `finalize_analyze_success` 가
`distributed["analysis"]` + `product.clothingType` 를 **원자 영속**하게 한다(AG-01 영속 블로커 해소).
→ 이후 마네킹 생성이 select_base_gender·material_guidance 를 **실 파생값**으로 발화한다.

크레딧: 로컬 펀딩 유저 재사용(analyze 는 무과금이라 여기선 무관, 생성은 baseline 러너에서).

실행(LOCAL):
  cd server && DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
    .venv/bin/python -m scripts.goldenset_seed --user <uuid> [--only top,pants] [--dry-run]
산출: server/ab_out/goldenset_types/_seed_manifest.json (arm→project_id) + _inputs/analysis/<armId>.json
"""
import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from scripts._env import load_env

load_env()

# 기본 로컬 DB (스크립트 전용 — prod DB 오염 방지). 명시 env 가 있으면 존중.
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")

from app import repo  # noqa: E402
from app.r2 import R2Client, upload_key  # noqa: E402
from app.workers.analyze_job import run_analyze_job  # noqa: E402
from scripts.smoke_realwire import InlineWorker  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIT = ROOT / "public/assets/fit-examples"
OUT = ROOT / "server/ab_out/goldenset_types"
INP = OUT / "_inputs"   # 웹소싱(Wikimedia CC) subcat 입력

# 구조 5종 (fit-examples 로컬 입력, 웹소싱 불필요). family = 구조 judge 기대값,
# clothing_type_hint 는 참고용(실제 종류는 AG-01 이 파생 — 우리는 강제 안 함).
ARMS = [
    {"id": "top-w", "family": "top", "gender_hint": "women", "img": FIT / "top-women-length-basic.jpg"},
    {"id": "top-m", "family": "top", "gender_hint": "men", "img": FIT / "top-men-fit-regular.jpg"},
    {"id": "pants-w", "family": "pants", "gender_hint": "women", "img": FIT / "pants-women-cut-straight.jpg"},
    {"id": "pants-m", "family": "pants", "gender_hint": "men", "img": FIT / "pants-men-cut-tapered.jpg"},
    {"id": "skirt-w", "family": "skirt", "gender_hint": "women", "img": FIT / "skirt-women-silhouette-a_line.jpg"},
    {"id": "dress-w", "family": "dress", "gender_hint": "women", "img": FIT / "dress-women-length-long.jpg"},
    {"id": "outer-w", "family": "outer", "gender_hint": "women", "img": FIT / "outer-any-length-long.jpg"},
    # subcat 확장 (Wikimedia CC garment-only, 전부 top-family)
    {"id": "knit-w", "family": "top", "gender_hint": "women", "img": INP / "knit.jpg"},
    {"id": "hood-w", "family": "top", "gender_hint": "women", "img": INP / "hoodie.jpg"},
    {"id": "shirt-m", "family": "top", "gender_hint": "men", "img": INP / "shirt.jpg"},
]


def _sniff(data: bytes) -> tuple[str, str]:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg"
    if data[:4] == b"\x89PNG":
        return "image/png", "png"
    return "image/jpeg", "jpg"


async def _claim_and_run_analyze(worker: InlineWorker, job_id: str) -> str:
    """analyze job 을 인프로세스로 셀프-클레임 실행(InlineWorker.claim_and_run 의 analyze 판)."""
    lease = f"goldenset-seed:{uuid.uuid4()}"
    async with worker.pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"update jobs set status='running', locked_by=%s, locked_at=now(), "
                f"started_at=coalesce(started_at, now()), progress=greatest(progress,5) "
                f"where id=%s and status='pending' returning {repo._JOB_COLS}, locked_by as lease_token",
                (lease, job_id))
            job = await cur.fetchone()
        await conn.commit()
    if job is None:
        return "stolen"
    await run_analyze_job(worker.app, job)
    return "claimed"


async def _seed_one(worker: InlineWorker, user_id: str, arm: dict) -> dict:
    s = worker._s
    r2 = R2Client(s)
    data = arm["img"].read_bytes()
    mime, ext = _sniff(data)
    async with worker.pool.connection() as conn:
        proj = await repo.create_project(conn, user_id)
        pid = proj["id"]
        asset_id = str(uuid.uuid4())
        key = upload_key(user_id, pid, asset_id, ext)
        await asyncio.to_thread(r2.put_bytes, key, data, mime)
        await repo.create_asset(conn, asset_id=asset_id, user_id=user_id, project_id=pid,
                                source="upload", bucket=s.r2_bucket, key=key, mime=mime,
                                size=len(data), original_filename=arm["img"].name)
        # colors: 기준 색상 1개 + Front 슬롯(base_color_images 계약). clothing_type 은 AG-01 이 채움.
        await repo.save_product(conn, pid, user_id, {
            "name": f"골드셋 {arm['id']}", "upload_complete": True,
            "colors": [{"id": "col1", "name": "base", "isBase": True, "isMain": True,
                        "images": [{"id": asset_id, "slot": "Front", "label": "Front"}]}]})
        job, created = await repo.create_job(conn, user_id=user_id, project_id=pid, kind="analyze",
                                             payload={}, idempotency_key=None, credits_reserved=0,
                                             metadata={"goldenset": arm["id"]})
        await conn.commit()
    assert created, f"{arm['id']}: analyze job not created (활성 중복?)"
    who = await _claim_and_run_analyze(worker, job["id"])
    assert who == "claimed", f"{arm['id']}: analyze job {who} (로컬 dispatcher 꺼야 함)"
    async with worker.pool.connection() as conn:
        an = await repo.get_analysis(conn, pid)
        prod = await repo.get_product(conn, pid)
    analysis = (an or {})
    tg = analysis.get("targetGenders")
    mats = analysis.get("materials")
    assert tg, f"{arm['id']}: analysis 에 targetGenders 없음 — AG-01 영속 실패"
    (OUT / "_inputs/analysis").mkdir(parents=True, exist_ok=True)
    (OUT / f"_inputs/analysis/{arm['id']}.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2))
    rec = {"arm": arm["id"], "family": arm["family"], "gender_hint": arm["gender_hint"],
           "project_id": pid, "clothing_type": (prod or {}).get("clothing_type"),
           "targetGenders": tg, "has_materials": bool(mats)}
    print(f"  ✅ {arm['id']}: pid={pid[:8]} type={rec['clothing_type']} "
          f"targetGenders={tg} materials={'Y' if mats else 'N'}")
    return rec


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="펀딩된 로컬 테스트 유저 uuid")
    ap.add_argument("--only", help="쉼표구분 arm id (부분 시드)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    arms = ARMS if not args.only else [a for a in ARMS if a["id"] in args.only.split(",")]
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"[goldenset_seed] user={args.user[:8]} arms={[a['id'] for a in arms]} "
          f"db={os.environ['DATABASE_URL'].split('@')[-1]}")
    for a in arms:
        if not a["img"].exists():
            print(f"  ✗ {a['id']}: 입력 없음 {a['img']}", file=sys.stderr); return 2
    if args.dry_run:
        for a in arms:
            print(f"  [dry] {a['id']}: family={a['family']} img={a['img'].name} "
                  f"({a['img'].stat().st_size // 1024}KB)")
        return 0
    worker = InlineWorker()
    await worker.open()
    manifest = []
    try:
        for a in arms:
            manifest.append(await _seed_one(worker, args.user, a))
    finally:
        await worker.close()
    # 기존 매니페스트와 arm id 기준 병합(부분 시드가 전체를 덮지 않도록)
    mf = OUT / "_seed_manifest.json"
    existing = {m["arm"]: m for m in json.loads(mf.read_text())} if mf.exists() else {}
    for m in manifest:
        existing[m["arm"]] = m
    mf.write_text(json.dumps(list(existing.values()), ensure_ascii=False, indent=2))
    print(f"\n시드 완료 {len(manifest)}/{len(arms)} (매니페스트 총 {len(existing)}) → {mf}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
