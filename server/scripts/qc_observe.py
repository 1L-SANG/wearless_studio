"""shadow 관측 리포트 — job_events 에 쌓인 QC 판정을 사람이 읽을 수 있게 집계.

`IMAGE_QC=shadow` 로 배포하는 목적이 "실 프로덕션 분포를 모으는 것"인데, 판정 결과가
`job_events` 에만 쌓이고 그걸 읽는 도구가 없었다. 쌓기만 하고 안 보면 관측이 아니다.

enforce 승격 판단에 필요한 것만 낸다: 4축 분포 · outcome 분포 · 판정 실패율 ·
critical_errors 빈도 · 구제 발생률.

실행:
    cd server && DATABASE_URL=... .venv/bin/python -m scripts.qc_observe [--days 7]
읽기 전용.
"""
import argparse
import collections
import json
import statistics

import psycopg
from psycopg.rows import dict_row

from scripts._env import load_env

load_env()

from app.config import load_settings  # noqa: E402

# QC 관련 step 이벤트만. payload shape 은 mannequin_job 의 _emit 호출부와 짝이다.
_SQL = """
select je.job_id, je.payload, je.created_at
from job_events je
join jobs j on j.id = je.job_id
where je.event_type = 'step'
  and j.kind = 'mannequin'
  and je.created_at > now() - make_interval(days => %s)
order by je.created_at
"""


def _fmt(values: list[int]) -> str:
    if not values:
        return "-"
    return (f"n={len(values):3} min={min(values):3} 중앙={statistics.median(values):5.1f} "
            f"max={max(values):3}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    s = load_settings()
    assert s.database_url, "DATABASE_URL 필요"
    with psycopg.connect(s.database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(_SQL, (args.days,))
        rows = cur.fetchall()

    statuses = collections.Counter()
    axes: dict[str, list[int]] = collections.defaultdict(list)
    outcomes = collections.Counter()
    criticals = collections.Counter()
    series_reasons = collections.Counter()
    for r in rows:
        p = r["payload"] or {}
        st = p.get("status")
        if not st:
            continue
        statuses[st] += 1
        if st in ("image_qc", "image_qc_rescored"):
            qc = p.get("imageQc") or {}
            for k in ("product_fidelity", "physical_naturalness", "image_quality"):
                if isinstance(v := qc.get(k), int) and not isinstance(v, bool):
                    axes[k].append(v)
            for e in qc.get("critical_errors") or []:
                criticals[e[:60]] += 1
        elif st == "series_qc":
            sq = p.get("seriesQc") or {}
            if isinstance(v := sq.get("consistency"), int):
                axes["series_consistency"].append(v)
            for e in sq.get("inconsistencies") or []:
                series_reasons[e[:60]] += 1
        elif st in ("final_qc_reject", "qc_salvaged"):
            outcomes[f"{st}:{p.get('outcome')}"] += 1

    print(f"[qc_observe] 최근 {args.days}일 · step 이벤트 {len(rows)}건\n")
    print("이벤트 종류:")
    for st, n in statuses.most_common():
        print(f"  {st:26} {n}")

    print("\n4축 분포:")
    for k in ("product_fidelity", "physical_naturalness", "image_quality", "series_consistency"):
        print(f"  {k:22} {_fmt(axes[k])}")

    judged = statuses["image_qc"] + statuses["image_qc_rescored"]
    failed = statuses["image_qc_failed"] + statuses["image_qc_rescore_failed"]
    total = judged + failed
    if total:
        # 판정 실패율은 shadow 관측의 신뢰도 그 자체다 — 실패가 많으면 분포가 생존 편향된다.
        print(f"\n판정 실패율: {failed}/{total} = {failed / total:.1%}")
    if statuses["series_qc_failed"]:
        print(f"D축 판정 실패: {statuses['series_qc_failed']}건")

    if outcomes:
        print("\n게이팅 결과(enforce 일 때만 발생):")
        for k, n in outcomes.most_common():
            print(f"  {k:34} {n}")

    _report_edit_impact(rows, s)
    _report_stored_outcomes(s)

    if criticals:
        print("\ncritical_errors 빈도:")
        for e, n in criticals.most_common(8):
            print(f"  {n:3}  {e}")
    if series_reasons:
        print("\n일관성 불일치 사유:")
        for e, n in series_reasons.most_common(8):
            print(f"  {n:3}  {e}")

    if not rows:
        print("\n(이벤트 없음 — IMAGE_QC 가 off 이거나 아직 생성이 없다)")
    return 0


def _report_edit_impact(rows: list[dict], s) -> None:
    """편집(축 교정·가슴 2패스)이 컷을 좋게 했는지 나쁘게 했는지.

    `image_qc`(편집 전) ↔ `image_qc_rescored`(편집 후)를 (job, candidate, attempt) 로 짝지어
    비교한다. 4축 분포만 보면 두 판정이 한 통에 섞여서 **편집의 효과가 평균에 묻힌다** —
    실측에서 이 짝짓기로만 "가슴 2패스가 42% 를 망친다"가 보였다.
    """
    from app.workers.mannequin_job import edit_regressed

    pairs: dict[tuple, dict] = {}
    for r in rows:
        p = r["payload"] or {}
        st = p.get("status")
        if st not in ("image_qc", "image_qc_rescored", "axis_retry", "bust_pass"):
            continue
        slot = pairs.setdefault((r["job_id"], p.get("candidate"), p.get("attempt")), {})
        if st == "image_qc":
            slot["pre"] = p.get("imageQc")
        elif st == "image_qc_rescored":
            slot["post"] = p.get("imageQc")
        else:
            slot.setdefault("edits", set()).add("bust" if st == "bust_pass" else "axis")

    judged = [v for v in pairs.values() if v.get("pre") and v.get("post")]
    if not judged:
        return
    drops = reverts = new_critical = 0
    by_edit: collections.Counter = collections.Counter()
    for v in judged:
        pre, post = v["pre"], v["post"]
        if (post.get("product_fidelity") or 0) < (pre.get("product_fidelity") or 0):
            drops += 1
        if edit_regressed(s, pre, post):
            reverts += 1
            by_edit["+".join(sorted(v.get("edits") or ["?"]))] += 1
            if post.get("critical_errors") and not pre.get("critical_errors"):
                new_critical += 1

    print(f"\n편집 전/후 비교 {len(judged)}쌍:")
    print(f"  product_fidelity 하락        {drops}/{len(judged)}")
    print(f"  되돌림 발동(등급하락·신규치명) {reverts}/{len(judged)}")
    print(f"  편집이 없던 치명오류를 만듦   {new_critical}")
    if by_edit:
        print("  회귀 귀속: " + " · ".join(f"{k} {n}" for k, n in by_edit.most_common()))


def _report_stored_outcomes(s) -> None:
    """저장된 판정을 **임계별로 층화**해 집계한다.

    임계를 바꿔도 과거 판정은 재계산되지 않는다. 층화 없이 등급 분포만 보면 임계 변경 전후가
    섞여 "왜 auto_pass 가 이렇게 적지" 같은 오독을 하게 된다(2026-07-31 실측: 90/75 시절
    판정 11건이 80/65 기준으로는 전부 불일치로 보였다 — 정상 이력인데 버그처럼 읽힌다).
    """
    with psycopg.connect(s.database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("select qc_scores from mannequin_cuts where qc_scores is not null")
        rows = [r["qc_scores"] for r in cur.fetchall()]
    if not rows:
        return
    buckets: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for q in rows:
        t = q.get("thresholds")
        key = f"{t['auto_pass']}/{t['review']}" if t else "미기록(임계 저장 이전)"
        buckets[key][q.get("outcome") or "?"] += 1
    print(f"\n저장된 판정 {len(rows)}건 — 임계별 층화:")
    for key in sorted(buckets, key=lambda k: (k.startswith("미기록"), k)):
        c = buckets[key]
        total = sum(c.values())
        parts = " · ".join(f"{k} {v}" for k, v in sorted(c.items()))
        print(f"  임계 {key:22} n={total:3}  {parts}")


if __name__ == "__main__":
    raise SystemExit(main())
