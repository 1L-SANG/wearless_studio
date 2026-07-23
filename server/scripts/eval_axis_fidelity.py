"""T2 P2 — 실경로 축 A/B (treatment vs control) 파일럿 하니스.

codex 요구 반영: eval_refimages(우회) 아닌 **실 생성 경로**를 재연한다.
HTTP `:regenerate` 의 서버측 로직을 그대로 태운다 —
  routes._fit_profile_snapshot(정규화 스냅샷 + adjustedAxes)
  → repo.create_job(mode='regenerate', fitProfileSnapshot)
  → run_mannequin_job(Gemini worker, axis QC 편집교정 포함)
  → 저장된 mannequin_cut 조회.
(HTTP/auth/credit-reserve 껍데기만 생략 — 생성 충실도엔 무관. codex 관심사인 snapshot·CHANGES·
 worker·최종 저장컷은 전부 재연.)

인과-라이트(codex [P1]):
- replicate마다 project analysis.fitProfile 을 **baseline 으로 리셋** → arm 간 profile 상태 누수 차단.
- 생성 순서 고정. evaluator 좌우 배치만 무작위(운영자 수동, 여기선 고정 LEFT=먼저생성).
- treatment(A→B)와 control(A→A, B→B) 를 **동일 signed directional** 채점.
- 인과효과 = treatment directional율 − control directional율. directional/absolute(axis_qc) 분리.

⚠️ 실 생성 = Gemini 쿼터 필요(파일럿 소량). `--dry-run` 은 배선만(생성/판정 없음).
실행: cd server && DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \\
        .venv/bin/python -m scripts.eval_axis_fidelity --project <uuid> \\
        --axis length --value-a crop --value-b long --reps 3
"""
import argparse
import asyncio
import sys
from pathlib import Path
from urllib.parse import unquote

from scripts._env import load_env

load_env()

from app import repo  # noqa: E402
from app.agents import mannequin, mannequin_pairwise_qc as PQ  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.routes import _fit_profile_snapshot  # noqa: E402  (서버 스냅샷 로직 재사용)

_FIT_CATS = ("top", "outer", "pants", "skirt", "dress")


def _profile(clothing_type, gender, axis, value):
    cat = clothing_type if clothing_type in _FIT_CATS else "top"
    return {"category": cat, "gender": gender, "source": "seller", "version": 1,
            "axes": {axis: value}}


def _result_asset_id(job: dict | None) -> str:
    if not job or job.get("status") != "done":
        status = (job or {}).get("status") or "missing"
        message = (job or {}).get("error_message") or ""
        raise RuntimeError(f"mannequin job {status}: {message}"[:300])
    cuts = ((job.get("result") or {}).get("data") or [])
    if len(cuts) != 1:
        raise RuntimeError(f"mannequin job 결과 컷 수 비정상: {len(cuts)}")
    src = cuts[0].get("src") or ""
    prefix, suffix = "/v1/assets/", "/file"
    if not src.startswith(prefix) or not src.endswith(suffix):
        raise RuntimeError(f"mannequin job 결과 asset 경로 비정상: {src!r}")
    return unquote(src[len(prefix):-len(suffix)])


def _directional_rate(results: list[dict]) -> float | None:
    visible = [r for r in results if r.get("observed") != "unclear"]
    if not visible:
        return None
    return sum(r.get("directionalPass") is True for r in visible) / len(visible)


def _false_positive_rate(results: list[dict]) -> float | None:
    visible = [r for r in results if r.get("observed") != "unclear"]
    if not visible:
        return None
    directional = sum(r.get("observed") in ("left", "right") for r in visible)
    return directional / len(visible)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--axis", default="length")
    ap.add_argument("--value-a", default="crop")
    ap.add_argument("--value-b", default="long")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", default="ab_out/axis_fidelity")
    ap.add_argument("--dry-run", action="store_true", help="배선만 — 생성/판정 없음")
    args = ap.parse_args()
    s = load_settings()

    # InlineWorker 재사용 (smoke_realwire) — pool 열고 job 을 인프로세스로 실행
    from scripts.smoke_realwire import InlineWorker
    worker = InlineWorker()
    await worker.open()
    pool = worker.pool
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    pid = args.project

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("select user_id::text u, (select clothing_type from products p "
                              "where p.project_id=%s) ct from projects where id=%s", (pid, pid))
            row = await cur.fetchone()
            await cur.execute("select payload from analyses where project_id=%s", (pid,))
            arow = await cur.fetchone()
    if not row:
        print("프로젝트 없음", file=sys.stderr); await worker.close(); return 2
    user_id = row["u"]
    clothing_type = row["ct"] or "top"
    analysis = (arow or {}).get("payload") or {}
    gender = mannequin.select_base_gender(analysis)
    cat = clothing_type if clothing_type in _FIT_CATS else "top"
    baseline_fit_profile = analysis.get("fitProfile")  # replicate 리셋 복원용
    try:
        expected = PQ.validated_expected_more_side(
            cat, gender, args.axis, args.value_a, args.value_b)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        await worker.close()
        return 2
    print(f"[axis_fidelity] project={pid[:8]} {cat} {gender} axis={args.axis} "
          f"A={args.value_a} B={args.value_b} reps={args.reps} expected_more={expected}")

    async def _reset_profile(profile):
        async with pool.connection() as conn:
            an = await repo.get_analysis(conn, pid)
            if an is not None:
                if profile is None:
                    an.pop("fitProfile", None)
                else:
                    an["fitProfile"] = profile
                await repo.save_analysis(conn, pid, an)
            await conn.commit()

    async def _gen(value, tag):
        """arm 1회: baseline 리셋 → snapshot → create_job(regenerate) → claim_and_run → 저장컷 bytes."""
        await _reset_profile(baseline_fit_profile)  # arm 간 상태 누수 차단
        prof = _profile(clothing_type, gender, args.axis, value)
        async with pool.connection() as conn:
            snapshot = await _fit_profile_snapshot(conn, pid, prof, validate_matching_fit=True)
            job, created = await repo.create_job(
                conn, user_id=user_id, project_id=pid, kind="mannequin",
                payload={"mode": "regenerate", "fitProfile": prof, "fitProfileSnapshot": snapshot},
                idempotency_key=None, credits_reserved=0, metadata={})
            await conn.commit()
        if not created:
            raise RuntimeError(f"활성 mannequin job 존재: {job['id']}")
        who = await worker.claim_and_run(job["id"])
        if who != "claimed":
            raise RuntimeError(f"job {job['id']} stolen — 로컬 dispatcher 꺼야 함")
        async with pool.connection() as conn:
            completed = await repo.get_job(conn, user_id, job["id"])
            asset_id = _result_asset_id(completed)
            async with conn.cursor() as cur:
                await cur.execute(
                    "select r2_key from assets where id=%s and user_id=%s and project_id=%s",
                    (asset_id, user_id, pid),
                )
                asset = await cur.fetchone()
        if not asset:
            raise RuntimeError(f"job 결과 asset 없음: {asset_id}")
        data = worker.app.state.r2.get_bytes(asset["r2_key"])
        p = out_dir / f"{pid[:8]}_{args.axis}_{value}_{tag}.png"
        p.write_bytes(data)
        return data, p

    if args.dry_run:
        # 배선만: snapshot 이 정규화되어 나오는지 확인(생성 없음)
        async with pool.connection() as conn:
            snap = await _fit_profile_snapshot(conn, pid, _profile(clothing_type, gender, args.axis, args.value_a),
                                               validate_matching_fit=True)
        print("[dry-run] snapshot:", snap)
        await worker.close()
        return 0

    try:
        treat, ctrl_a, ctrl_b = [], [], []
        for i in range(args.reps):
            a1, _ = await _gen(args.value_a, f"treatA{i}")
            b1, _ = await _gen(args.value_b, f"treatB{i}")
            tv = await PQ.judge(s, a1, b1, args.axis)          # LEFT=A, RIGHT=B
            treat.append(PQ.score_pair(tv, cat, args.axis, args.value_a, args.value_b))
            # control: 같은 값 독립 2생성(자연변동), 동일 signed 채점(pseudo A=left,A=right → expected 'equal')
            a2a, _ = await _gen(args.value_a, f"ctrlA{i}a"); a2b, _ = await _gen(args.value_a, f"ctrlA{i}b")
            ca = await PQ.judge(s, a2a, a2b, args.axis)
            ctrl_a.append(PQ.score_pair(ca, cat, args.axis, args.value_a, args.value_a))  # expected equal
            b2a, _ = await _gen(args.value_b, f"ctrlB{i}a"); b2b, _ = await _gen(args.value_b, f"ctrlB{i}b")
            cb = await PQ.judge(s, b2a, b2b, args.axis)
            ctrl_b.append(PQ.score_pair(cb, cat, args.axis, args.value_b, args.value_b))
            print(f"  rep{i}: treat={treat[-1]['directionalPass']} "
                  f"ctrlA={ctrl_a[-1]['observed']} ctrlB={ctrl_b[-1]['observed']}")

        t = _directional_rate(treat)
        # control directional율: control 은 '변화 방향' 오검출률 — expected=equal 이라 'similar' 이 정답,
        # 방향 답이 곧 false-positive. false-positive율 = 방향 답(비-similar) 비율.
        c_fp = _false_positive_rate(ctrl_a + ctrl_b)
        print(f"\n[결과] treatment directional율={t}  control false-positive율(방향오검출)={c_fp}")
        print(f"[인과-라이트] 인과효과 ≈ treatment {t} − control {c_fp} "
              f"= {None if t is None or c_fp is None else round(t - c_fp, 3)}")
        print(f"저장: {out_dir}/  (육안 병기 필수 — 파일럿, 검출률 주장 금지)")
    finally:
        await worker.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
