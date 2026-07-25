"""T2 고도화 — 카테고리별 **조정 축 반영** 실측 (실 regenerate 경로).

축마다 서열 양 극단 A(low)/B(high) 를 **교차 순서**(A₀,B₀,A₁,B₁)로 생성하고, 같은 4컷에서
두 관점을 뽑는다:
  · 절대 준수 ×4 — `mannequin_fit_qc.verdict`(prod 정본 심판)로 관측목표 충족 여부
  · treatment 방향 ×4 — (A₀,B₀)·(A₁,B₁) 를 **양 배치** 각 1회
  · control 방향 ×2 — (A₀,A₁)·(B₀,B₁) 동일값 → 기대 'similar', 방향 답이면 오검출

codex 2라운드 반영:
- **복합 점수 없음**: treatment−control 뺄셈 금지(같은 이미지 재사용·종속·기권 분모 상이).
  축당 **원시 블록**만 보고. control 은 감산항이 아니라 "같은 값인데 방향을 본 빈도" 기준선.
- **전역 Gemini 핀**: `ANALYSIS_MODEL_ORDER=gemini` 를 **InlineWorker 생성 전** 주입 →
  하니스 심판뿐 아니라 **워커 내부 axis QC** 까지 동일 provider(폴백 무성 전환 불가).
- **값-배치 동반 스왑**: 비교 레코드가 (컷, 그 자리의 값)을 함께 들고 있어 채점 인자 불일치 불가.
- **assert**: baseline `analysis.fitProfile` None · 스냅샷 `adjustedAxes` 에 대상 축 포함.
- **auditability**(재생 불가): commit·이미지 hash·settings·배치 방향 기록. 시드/모델 리비전은 미통제.

실행(LOCAL):
  cd server && DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
    .venv/bin/python -m scripts.eval_axis_reflection --reps 2 [--only top:fit,pants:cut] \
    [--axis-qc off|enforce] [--allow-partial] [--dry-run]
산출: server/ab_out/axis_reflection/<runId>/ PNG + results.jsonl + REPORT.md
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
# 전역 provider 핀 — _order() 가 gemini 단독으로 필터되어 폴백이 존재하지 않는다(codex P1).
os.environ["ANALYSIS_MODEL_ORDER"] = "gemini"
os.environ.setdefault("RETRIEVAL_REFIMAGES", "off")

from app import repo  # noqa: E402
from app.agents import fit_axis_matrix as FM  # noqa: E402
from app.agents import mannequin_fit_qc as FITQC  # noqa: E402
from app.agents import mannequin_pairwise_qc as PQ  # noqa: E402
from app.agents.gemini_image import InlineImage  # noqa: E402
from app.agents.mannequin import base_color_image_ids  # noqa: E402
from app.r2 import R2Client  # noqa: E402
from app.routes import _fit_profile_snapshot  # noqa: E402

GOLDEN = Path(__file__).resolve().parents[2] / "server/ab_out/goldenset_types"
OUT = Path(__file__).resolve().parents[2] / "server/ab_out/axis_reflection"
# family(측정 축 카테고리) → 시드 arm. dev 셋(진단·튜닝)과 holdout 셋(수정 확증)을 분리한다.
FAMILY_ARM = {"top": "top-w", "pants": "pants-w", "skirt": "skirt-w",
              "dress": "dress-w", "outer": "outer-w"}
# holdout = 진짜 다른 옷(과적합 배제). pants 는 T3 의 남성 데님(pants-m) 재사용,
# skirt/outer 는 사용자 제공 상품컷. dev 셋에서 튜닝한 프롬프트를 여기서 확증만 한다.
HOLDOUT_ARM = {"pants": "pants-m", "skirt": "holdout-skirt", "outer": "holdout-outer"}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"


def _profile(category: str, gender: str, axis: str, value: str) -> dict:
    return {"category": category, "gender": gender, "source": "seller", "version": 1,
            "axes": {axis: value}}


async def _reset_baseline(pool, pid: str) -> None:
    """arm 간 상태 누수 차단 — analysis.fitProfile 제거(선언 전 baseline 을 None 으로)."""
    async with pool.connection() as conn:
        an = await repo.get_analysis(conn, pid)
        if an is not None and an.get("fitProfile") is not None:
            an.pop("fitProfile", None)
            await repo.save_analysis(conn, pid, an)
        await conn.commit()


async def _completed_job_cut_key(pool, user_id: str, job_id: str) -> str:
    """완료된 해당 job이 생성한 단일 컷의 R2 key를 반환한다."""
    async with pool.connection() as conn:
        job = await repo.get_job(conn, user_id, job_id)
        if job is None:
            raise RuntimeError(f"job {job_id[:8]} 조회 실패")
        if job.get("status") != "done":
            detail = job.get("error_message") or f"status={job.get('status')}"
            raise RuntimeError(f"job {job_id[:8]} 생성 실패: {detail}")
        result = job.get("result") or {}
        cuts = result.get("data") if isinstance(result, dict) else None
        if (not isinstance(cuts, list) or len(cuts) != 1
                or not isinstance(cuts[0], dict) or not cuts[0].get("id")):
            raise RuntimeError(f"job {job_id[:8]} 단일 컷 결과 없음")
        asset = await repo.get_mannequin_cut_asset(
            conn, user_id, job["project_id"], cuts[0]["id"])
    if not asset or not asset.get("r2_key"):
        raise RuntimeError(f"job {job_id[:8]} 컷 에셋 조회 실패")
    return asset["r2_key"]


async def _product_image_keys(pool, pid: str) -> list[str]:
    async with pool.connection() as conn:
        prod = await repo.get_product(conn, pid) or {}
        ids = base_color_image_ids(prod)
        keys = []
        async with conn.cursor() as cur:
            for aid in ids:
                await cur.execute("select r2_key from assets where id=%s", (aid,))
                r = await cur.fetchone()
                if r:
                    keys.append(r["r2_key"])
    return keys


async def _gen_one(worker, user_id: str, pid: str, profile: dict, axis: str) -> tuple[str, dict]:
    """조정 1회 = 실 regenerate 경로. 반환 (저장컷 r2_key, snapshot)."""
    await _reset_baseline(worker.pool, pid)
    async with worker.pool.connection() as conn:
        an = await repo.get_analysis(conn, pid) or {}
        assert an.get("fitProfile") is None, f"{pid[:8]}: baseline fitProfile 이 None 이 아님"
        snapshot = await _fit_profile_snapshot(conn, pid, profile, validate_matching_fit=True)
        adjusted = snapshot.get("adjustedAxes") or []
        assert axis in adjusted, f"{pid[:8]}: adjustedAxes 에 {axis} 없음 → CHANGES 미발화 ({adjusted})"
        job, created = await repo.create_job(
            conn, user_id=user_id, project_id=pid, kind="mannequin",
            payload={"mode": "regenerate", "fitProfile": profile, "fitProfileSnapshot": snapshot},
            idempotency_key=None, credits_reserved=0, metadata={"t2b": axis})
        await conn.commit()
    assert created, f"{pid[:8]}: job 미생성(활성 중복)"
    who = await worker.claim_and_run(job["id"])
    assert who == "claimed", f"{pid[:8]}: job {who} — 로컬 dispatcher 꺼야 함"
    key = await _completed_job_cut_key(worker.pool, user_id, job["id"])
    return key, snapshot


def _summarize_block(block: dict, *, planned_absolute: int, planned_directional: int) -> None:
    """유효 판정 수를 계획과 대조해 원시 집계와 불완전 상태를 기록한다."""
    directional_ok = [d for d in block["directional"] if "error" not in d]
    treat = [d for d in directional_ok if d.get("kind") == "treatment"]
    ctrl = [d for d in directional_ok if d.get("kind") == "control"]
    t_scored = [d for d in treat if not d.get("abstain")]
    abs_ok = [a for a in block["absolute"] if "error" not in a]
    block["raw"] = {
        "treatmentPass": sum(1 for d in t_scored if d.get("directionalPass")),
        "treatmentScored": len(t_scored),
        "treatmentAbstain": len(treat) - len(t_scored),
        "controlFalseDirection": sum(
            1 for d in ctrl if d.get("observed") in ("left", "right")),
        "controlTotal": len(ctrl),
        "absolutePass": sum(1 for a in abs_ok if a.get("pass")),
        "absoluteFail": sum(1 for a in abs_ok if not a.get("pass")),
        "absoluteTotal": len(abs_ok),
        "absolutePlanned": planned_absolute,
        "directionalTotal": len(directional_ok),
        "directionalPlanned": planned_directional,
    }
    block["incomplete"] = (
        len(abs_ok) < planned_absolute or len(directional_ok) < planned_directional)
    block["suspect"] = block["incomplete"] or FM.is_suspect(
        block["raw"]["treatmentPass"],
        block["raw"]["treatmentScored"],
        block["raw"]["absoluteFail"],
    )
    # 절대 통과인데 방향 무변화 → 심판 관대 의심(면죄 아님).
    # 불완전 실행은 수치 비교 자체가 성립하지 않으므로 별도 incomplete 로만 표시한다.
    block["metricDisagreement"] = (
        not block["incomplete"]
        and block["raw"]["absoluteFail"] == 0
        and block["raw"]["treatmentScored"] > 0
        and block["raw"]["treatmentPass"] == 0
    )


def _result_exit_code(records: list[dict], *, allow_partial: bool) -> int:
    return 0 if allow_partial or not any(r.get("incomplete") for r in records) else 1


async def _owner(pool, pid: str) -> str:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("select user_id::text from projects where id=%s", (pid,))
            r = await cur.fetchone()
    return r["user_id"]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--gender", default="women")
    ap.add_argument("--only", help="쉼표구분 category:axis (예: top:fit,pants:cut)")
    ap.add_argument("--holdout", action="store_true", help="dev arm 대신 holdout(진짜 다른 옷) 사용 — 수정 확증")
    ap.add_argument("--axis-qc", default="off", choices=["off", "shadow", "enforce"])
    ap.add_argument(
        "--allow-partial",
        action="store_true",
        help="생성·판정 오류로 불완전한 결과가 있어도 보고서 작성 후 종료 코드 0 허용",
    )
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--phase", default="stage1")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # ★ InlineWorker 생성 **전** 주입 — __init__ 이 즉시 load_settings() 호출(codex P2)
    os.environ["MANNEQUIN_AXIS_QC"] = args.axis_qc

    pairs = FM.all_pairs(args.gender)
    if args.only:
        want = {tuple(x.split(":")) for x in args.only.split(",")}
        pairs = [p for p in pairs if (p["category"], p["axis"]) in want]
    manifest = {m["arm"]: m for m in json.loads((GOLDEN / "_seed_manifest.json").read_text())}
    run_id = args.run_id or f"{args.phase}-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir = OUT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    sha = _git_sha()

    from scripts.smoke_realwire import InlineWorker  # env 주입 후 import·생성
    worker = InlineWorker()
    s = worker._s
    settings_snap = {k: getattr(s, k, None) for k in
                     ("mannequin_axis_qc", "image_qc", "retrieval_refimages",
                      "analysis_model_order", "model_text_gemini", "mannequin_image_size")}
    print(f"[axis_reflection] runId={run_id} phase={args.phase} axisQC={args.axis_qc} "
          f"reps={args.reps} gender={args.gender} sha={sha}")
    print(f"  provider pin: ANALYSIS_MODEL_ORDER={settings_snap['analysis_model_order']} "
          f"(폴백 없음) · retrieval={settings_snap['retrieval_refimages']}")
    print(f"  축 {len(pairs)}쌍: {[(p['category'], p['axis'], p['low'], p['high']) for p in pairs]}")

    if args.dry_run:
        await worker.open()
        try:
            for p in pairs:
                arm = (HOLDOUT_ARM if args.holdout else FAMILY_ARM).get(p["category"])
                m = manifest.get(arm)
                if not m:
                    print(f"  ✗ {p['category']}:{p['axis']} — 시드 arm {arm} 없음"); continue
                pid = m["project_id"]
                await _reset_baseline(worker.pool, pid)
                async with worker.pool.connection() as conn:
                    snap = await _fit_profile_snapshot(
                        conn, pid, _profile(p["category"], p["gender"], p["axis"], p["low"]),
                        validate_matching_fit=True)
                ok = p["axis"] in (snap.get("adjustedAxes") or [])
                plan = FM.comparison_plan(p["low"], p["high"], args.reps)
                print(f"  [dry] {p['category']}:{p['axis']} {p['low']}↔{p['high']} pid={pid[:8]} "
                      f"adjustedAxes={snap.get('adjustedAxes')} {'OK' if ok else '✗축누락'} "
                      f"gen={len(FM.cut_labels(args.reps))} judge={len(plan)}+{len(FM.cut_labels(args.reps))}abs")
        finally:
            await worker.close()
        return 0

    await worker.open()
    r2 = R2Client(s)
    records = []
    try:
        for p in pairs:
            cat, axis, gender = p["category"], p["axis"], p["gender"]
            arm = (HOLDOUT_ARM if args.holdout else FAMILY_ARM).get(cat)
            m = manifest.get(arm)
            if not m:
                print(f"  ✗ {cat}:{axis} — 시드 arm {arm} 없음"); continue
            pid = m["project_id"]
            user_id = await _owner(worker.pool, pid)
            src_keys = await _product_image_keys(worker.pool, pid)
            prod_imgs = [InlineImage("image/jpeg", await asyncio.to_thread(r2.get_bytes, k))
                         for k in src_keys]
            values = {"low": p["low"], "high": p["high"]}
            cuts: dict[str, bytes] = {}
            block = {"runId": run_id, "phase": args.phase, "commit": sha, "category": cat,
                     "axis": axis, "gender": gender, "low": p["low"], "high": p["high"],
                     "projectId": pid, "axisQC": args.axis_qc, "providerPin": "gemini",
                     "allowPartial": args.allow_partial, "settings": settings_snap,
                     "absolute": [], "directional": []}
            cut_plan = FM.cut_labels(args.reps)
            comparison_plan = FM.comparison_plan(p["low"], p["high"], args.reps)
            # ── 교차 생성 A0,B0,A1,B1 (시간 드리프트 ⊥ 값) ──
            for label, side in cut_plan:
                value = values[side]
                prof = _profile(cat, gender, axis, value)
                try:
                    key, snap = await _gen_one(worker, user_id, pid, prof, axis)
                    data = await asyncio.to_thread(r2.get_bytes, key)
                    cuts[label] = data
                    (run_dir / f"{cat}-{axis}_{label}_{value}.png").write_bytes(data)
                    # 절대 준수 판정 (prod 정본 심판, gemini 핀)
                    v = await FITQC.verdict(s, prod_imgs, InlineImage("image/png", data), prof)
                    ap_ = v.get("axisPass") or []
                    hit = next((x for x in ap_ if x.get("axis") == axis), None)
                    block["absolute"].append({
                        "cut": label, "value": value, "outputHash": _sha(data),
                        "identityPass": v.get("identityPass"),
                        "pass": bool(hit and hit.get("pass")), "visible": bool(hit and hit.get("visible")),
                        "observedLandmark": (hit or {}).get("observedLandmark", "")[:160],
                        "adjustedAxes": snap.get("adjustedAxes")})
                    print(f"  {cat}:{axis} {label}={value} abs pass={block['absolute'][-1]['pass']} "
                          f"visible={block['absolute'][-1]['visible']}")
                except Exception as e:
                    block["absolute"].append({"cut": label, "value": value,
                                              "error": f"{type(e).__name__}: {e}"[:200]})
                    print(f"  ✗ {cat}:{axis} {label}={value}: {type(e).__name__}: {e}")
            # ── 방향 판정: treatment 4(양 배치) + control 2 ──
            for comp in comparison_plan:
                lc, rc = comp["leftCut"], comp["rightCut"]
                if lc not in cuts or rc not in cuts:
                    continue
                try:
                    v = await PQ.judge(s, cuts[lc], cuts[rc], axis)
                    sc = PQ.score_pair(v, cat, axis, comp["valueLeft"], comp["valueRight"])
                    block["directional"].append({**comp, "moreSide": v.get("moreSide"),
                                                 "reason": (v.get("reason") or "")[:160], **sc})
                    print(f"    {comp['kind']}[{comp['orientation']}] {lc}v{rc}: "
                          f"observed={sc['observed']} expected={sc['expected']} "
                          f"pass={sc['directionalPass']} abstain={sc['abstain']}")
                except Exception as e:
                    block["directional"].append({**comp, "error": f"{type(e).__name__}: {e}"[:200]})
            # ── 원시 집계 + 사전 등록 의심 규칙 (복합 점수 없음) ──
            _summarize_block(
                block,
                planned_absolute=len(cut_plan),
                planned_directional=len(comparison_plan),
            )
            print(f"  ▶ {cat}:{axis} raw={block['raw']} suspect={block['suspect']} "
                  f"incomplete={block['incomplete']} "
                  f"metricDisagreement={block['metricDisagreement']}")
            records.append(block)
            with open(run_dir / "results.jsonl", "a") as f:
                f.write(json.dumps(block, ensure_ascii=False) + "\n")
    finally:
        await worker.close()

    _write_report(run_dir, run_id, args, sha, records)
    susp = [f"{r['category']}:{r['axis']}" for r in records if r.get("suspect")]
    incomplete = [f"{r['category']}:{r['axis']}" for r in records if r.get("incomplete")]
    exit_code = _result_exit_code(records, allow_partial=args.allow_partial)
    print(f"\n[결과] 축 {len(records)}개 · 의심 축 {susp or '없음'} · "
          f"불완전 축 {incomplete or '없음'} → {run_dir}/REPORT.md")
    if incomplete and not args.allow_partial:
        print("  ✗ 불완전한 생성·판정이 있어 종료 코드 1 "
              "(부분 결과를 의도적으로 허용하려면 --allow-partial)")
    return exit_code


def _write_report(run_dir: Path, run_id: str, args, sha: str, records: list) -> None:
    L = [f"# T2 고도화 — 축 반영 원시 블록 ({run_id})", "",
         f"phase={args.phase} · axisQC={args.axis_qc} · reps={args.reps} · commit={sha} · "
         f"allowPartial={args.allow_partial} · "
         f"provider=**gemini 핀**(ANALYSIS_MODEL_ORDER, 폴백 없음)", "",
         "> **원시 카운트만.** treatment−control 뺄셈·비율 추정·등급 없음(codex).",
         "> control 은 감산항이 아니라 *같은 값인데 심판이 방향을 본 빈도* 기준선.",
         "> 기권(similar/unclear)은 통과도 실패도 아님 — 별도 카운트.",
         "> 의심 규칙(사전 등록): treatment 정답 <1/2 **또는** 절대 fail ≥1.",
         "> 계획보다 유효 판정이 적으면 **불완전**이며 항상 의심. 기본 종료 코드 1.",
         "> auditability 만 보장(시드·모델 리비전 미통제 → 재생 불가).", "",
         "| 카테고리 | 축 | 극단쌍 | 절대 P/F/완료/계획 | 방향 완료/계획 | treat 정답/채점(기권) | control 오검출/총 | 불완전 | 의심 | 지표불일치 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in records:
        raw = r.get("raw", {})
        L.append(f"| {r['category']} | {r['axis']} | {r['low']}↔{r['high']} | "
                 f"{raw.get('absolutePass')}/{raw.get('absoluteFail')}/"
                 f"{raw.get('absoluteTotal')}/{raw.get('absolutePlanned')} | "
                 f"{raw.get('directionalTotal')}/{raw.get('directionalPlanned')} | "
                 f"{raw.get('treatmentPass')}/{raw.get('treatmentScored')}({raw.get('treatmentAbstain')}) | "
                 f"{raw.get('controlFalseDirection')}/{raw.get('controlTotal')} | "
                 f"{'**불완전**' if r.get('incomplete') else '완료'} | "
                 f"{'**의심**' if r.get('suspect') else 'ok'} | "
                 f"{'⚠️' if r.get('metricDisagreement') else '—'} |")
    L += ["", "## 의심 축 상세(육안 병기 필수)", ""]
    for r in [x for x in records if x.get("suspect")]:
        L.append(f"### {r['category']}:{r['axis']} ({r['low']}↔{r['high']})")
        for a in r["absolute"]:
            L.append(f"- abs {a.get('cut')}={a.get('value')}: pass={a.get('pass')} "
                     f"visible={a.get('visible')} · {a.get('observedLandmark') or a.get('error','')}")
        for d in r["directional"]:
            L.append(f"- {d.get('kind')}[{d.get('orientation')}] {d.get('leftCut')}v{d.get('rightCut')}: "
                     f"observed={d.get('observed')} expected={d.get('expected')} "
                     f"pass={d.get('directionalPass')} · {d.get('reason','')}")
        L.append("")
    if not any(x.get("suspect") for x in records):
        L.append("(없음)")
    (run_dir / "REPORT.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
