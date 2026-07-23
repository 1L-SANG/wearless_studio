"""T3 Step 5 — 의류종류별 baseline 구조 견고성 측정 (실 워커 초기생성).

codex 반영:
- **초기생성 계약**(regenerate 아님): `_fit_profile_snapshot(conn,pid,None)` + mode='generate'
  (routes.py:919 동일). 워커가 select_base_gender·seller canon·material_guidance·base 선택·
  image QC(설정 시)를 실발화. axis QC 는 baseline(선언축 없음)에서 스킵 = 정상.
- **judge = 직접 Gemini**(mannequin_structure_qc, 폴백 금지). 캘리브 게이트 통과 후에만 신뢰.
- **freeze/repro**: RETRIEVAL off, commit SHA, 첨부 이미지 hash, model/backend/judge model,
  settings 스냅샷을 레코드마다 저장. runId·phase 로 baseline/rerun/rejudge 격리.
- 주장 범위: **arm-level per-garment 관찰**(N=reps). 교차 rate·gender 효과·type robustness 일반화 안 함.

실행(LOCAL):
  cd server && DATABASE_URL=...54322 RETRIEVAL_REFIMAGES=off GEMINI_API_KEY=... \
    .venv/bin/python -m scripts.goldenset_clothing_types --reps 3 [--only top-w,pants-w] \
    [--phase baseline] [--run-id <tag>] [--dry-run]
산출: server/ab_out/goldenset_types/<runId>/<arm>_rep<k>.png + results.jsonl + REPORT.md
"""
import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from scripts._env import load_env

load_env()
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
os.environ.setdefault("RETRIEVAL_REFIMAGES", "off")   # freeze — 이동부품 제거(codex)

from app import repo  # noqa: E402
from app.agents import mannequin, mannequin_structure_qc as SQ  # noqa: E402
from app.r2 import R2Client  # noqa: E402
from app.routes import _fit_profile_snapshot  # noqa: E402
from scripts.smoke_realwire import InlineWorker  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "server/ab_out/goldenset_types"
BASE_KEY = {"women": "seed/mannequin/base-women-2K.png", "men": "seed/mannequin/base-men-2K.png"}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"


def _settings_snapshot(s) -> dict:
    keys = ("mannequin_tier", "model_image", "mannequin_image_size", "mannequin_aspect_ratio",
            "retrieval_refimages", "image_qc", "mannequin_axis_qc", "mannequin_qc_enabled")
    return {k: getattr(s, k, None) for k in keys}


async def _newest_cut_key(pool, pid: str) -> str | None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select a.r2_key from mannequin_cuts mc join assets a on a.id=mc.asset_id "
                "where mc.project_id=%s order by mc.created_at desc limit 1", (pid,))
            r = await cur.fetchone()
    return r["r2_key"] if r else None


async def _src_key(pool, pid: str) -> str | None:
    async with pool.connection() as conn:
        prod = await repo.get_product(conn, pid) or {}
        ids = mannequin.base_color_image_ids(prod)
        if not ids:
            return None
        async with conn.cursor() as cur:
            await cur.execute("select r2_key from assets where id=%s", (ids[0],))
            r = await cur.fetchone()
    return r["r2_key"] if r else None


async def _gen_once(worker, user_id, pid) -> str:
    """1회 초기생성(mode=generate) → 저장컷 r2_key. job 훔침·미생성이면 raise."""
    async with worker.pool.connection() as conn:
        snap = await _fit_profile_snapshot(conn, pid, None)
        job, created = await repo.create_job(
            conn, user_id=user_id, project_id=pid, kind="mannequin",
            payload={"mode": "generate", "fitProfileSnapshot": snap},
            idempotency_key=None, credits_reserved=0, metadata={"goldenset": "baseline"})
        await conn.commit()
    assert created, f"{pid[:8]}: mannequin job 미생성(활성 중복)"
    who = await worker.claim_and_run(job["id"])
    assert who == "claimed", f"{pid[:8]}: job {who} — 로컬 dispatcher 꺼야 함"
    key = await _newest_cut_key(worker.pool, pid)
    assert key, f"{pid[:8]}: 저장컷 없음(생성 실패)"
    return key


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", help="쉼표구분 arm id")
    ap.add_argument("--phase", default="baseline", choices=["baseline", "rerun", "rejudge"])
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    manifest = json.loads((OUT / "_seed_manifest.json").read_text())
    if args.only:
        manifest = [m for m in manifest if m["arm"] in args.only.split(",")]
    run_id = args.run_id or f"{args.phase}-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir = OUT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    sha = _git_sha()

    worker = InlineWorker()
    s = worker._s
    r2 = R2Client(s)
    settings_snap = _settings_snapshot(s)
    print(f"[baseline] runId={run_id} phase={args.phase} arms={[m['arm'] for m in manifest]} "
          f"reps={args.reps} sha={sha} retrieval={settings_snap.get('retrieval_refimages')}")

    if args.dry_run:
        await worker.open()
        for m in manifest:
            async with worker.pool.connection() as conn:
                snap = await _fit_profile_snapshot(conn, m["project_id"], None)
            print(f"  [dry] {m['arm']}: pid={m['project_id'][:8]} family={m['family']} "
                  f"gender={m['targetGenders']} snapshot={snap}")
        await worker.close()
        return 0

    await worker.open()
    results = []
    try:
        for m in manifest:
            pid, arm, fam = m["project_id"], m["arm"], m["family"]
            user_id = await _owner(worker.pool, pid)
            async with worker.pool.connection() as conn:
                analysis = await repo.get_analysis(conn, pid) or {}
            gender = mannequin.select_base_gender(analysis)
            base_key = BASE_KEY[gender]
            base_bytes = await asyncio.to_thread(r2.get_bytes, base_key)
            src_key = await _src_key(worker.pool, pid)
            src_bytes = await asyncio.to_thread(r2.get_bytes, src_key) if src_key else b""
            for k in range(args.reps):
                rec = {"runId": run_id, "phase": args.phase, "arm": arm, "family": fam,
                       "rep": k, "projectId": pid, "gender": gender, "commit": sha,
                       "backend": "gemini", "judgeModel": s.model_text_gemini,
                       "srcKey": src_key, "baseKey": base_key,
                       "srcHash": _sha(src_bytes), "settings": settings_snap}
                try:
                    gen_key = await _gen_once(worker, user_id, pid)
                    gen_bytes = await asyncio.to_thread(r2.get_bytes, gen_key)
                    (run_dir / f"{arm}_rep{k}.png").write_bytes(gen_bytes)
                    rec["genKey"] = gen_key
                    rec["outputHash"] = _sha(gen_bytes)
                    verdict = await SQ.judge(s, gen_bytes, [src_bytes] if src_bytes else [], base_bytes)
                    agg = SQ.aggregate(verdict, fam)
                    rec["verdict"] = verdict
                    rec["aggregate"] = agg
                    print(f"  {arm} rep{k}: overall={agg['overallPass']} "
                          f"typeSeen={verdict['typeSeen'][:20]!r} modes={agg['failureModes']} "
                          f"unjudge={agg['unjudgeable']}")
                except Exception as e:
                    rec["error"] = f"{type(e).__name__}: {e}"[:300]
                    print(f"  ✗ {arm} rep{k}: {rec['error']}")
                results.append(rec)
                with open(run_dir / "results.jsonl", "a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    finally:
        await worker.close()

    _write_report(run_dir, run_id, args.phase, sha, results)
    ok = sum(1 for r in results if r.get("aggregate", {}).get("overallPass") is True)
    fail = sum(1 for r in results if r.get("aggregate", {}).get("overallPass") is False)
    unj = sum(1 for r in results if r.get("aggregate", {}).get("overallPass") is None and "aggregate" in r)
    err = sum(1 for r in results if "error" in r)
    print(f"\n[결과] pass={ok} fail={fail} unjudgeable={unj} error={err} → {run_dir}/REPORT.md")
    return 0


async def _owner(pool, pid: str) -> str:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("select user_id::text from projects where id=%s", (pid,))
            r = await cur.fetchone()
    return r["user_id"]


def _write_report(run_dir: Path, run_id: str, phase: str, sha: str, results: list) -> None:
    lines = [f"# T3 골드셋 구조 견고성 — {run_id}", "",
             f"phase={phase} · commit={sha} · backend=gemini(prod 생성기) · RETRIEVAL=off",
             "",
             "> **파일럿·arm-level per-garment 관찰**. 교차 rate·gender 효과·type robustness 일반화 안 함.",
             "> autoVerdict(judge)는 스크리닝. **육안(humanVerdict)이 정본** — 이미지 병기 검토 필수.",
             "> overallPass: True(전 축 통과)/False(구조 실패)/null(핵심축 판정불가).", "",
             "| arm | family | rep | overallPass | typeSeen | failureModes | unjudgeable |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        agg = r.get("aggregate") or {}
        v = r.get("verdict") or {}
        if "error" in r:
            lines.append(f"| {r['arm']} | {r['family']} | {r['rep']} | ERROR | — | {r['error'][:40]} | — |")
            continue
        lines.append(f"| {r['arm']} | {r['family']} | {r['rep']} | {agg.get('overallPass')} | "
                     f"{(v.get('typeSeen') or '')[:20]} | {','.join(agg.get('failureModes') or []) or '—'} | "
                     f"{','.join(agg.get('unjudgeable') or []) or '—'} |")
    # 실패모드 집계
    modes: dict = {}
    for r in results:
        for mo in (r.get("aggregate") or {}).get("failureModes", []):
            modes[mo] = modes.get(mo, 0) + 1
    lines += ["", "## 실패모드 집계 (arm×rep 전체)", ""]
    lines += [f"- {mo}: {n}" for mo, n in sorted(modes.items(), key=lambda x: -x[1])] or ["- (없음)"]
    lines += ["", "## humanVerdict (오퍼레이터 육안 — 단일 평정자, 신뢰도 주장 안 함)", "",
              "| arm | rep | 육안 pass? | 메모 |", "|---|---|---|---|"]
    for r in results:
        if "error" not in r:
            lines.append(f"| {r['arm']} | {r['rep']} |  |  |")
    (run_dir / "REPORT.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
