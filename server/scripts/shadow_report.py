"""Shadow 데이터 수집 + 리포트 — **읽기 전용** (Phase 3 P0-C 9/N).

    python scripts/shadow_report.py                      # $DATABASE_URL
    python scripts/shadow_report.py --dsn postgres://…   # 명시
    python scripts/shadow_report.py --image-usd 0.12 --vision-usd 0.002

SELECT 만 한다. INSERT/UPDATE/DELETE/DDL 없음 — 세션 자체를 read only 로 열어
실수로도 쓰지 못하게 막는다. provider 원문 응답은 조회하지도 출력하지도 않는다
(edit_qc_result 에서 필요한 필드만 뽑는다).

임계값을 여기서 정하지 않는다. 분포와 표본 수를 보여 줄 뿐이고, enforce 판단은
사람이 한다 — verdict 는 "지금 데이터로는 무엇을 말할 수 없는가"를 알려 주는 용도다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import blinded_audit as ba  # noqa: E402
from app import shadow_verification as sv  # noqa: E402
from app.shadow_report import report  # noqa: E402

# 필요한 컬럼만. edit_qc_result 는 판정 요약이라 provider 원문이 들어 있지 않다
# (6/N 에서 원문 저장을 끊었다). 그래도 출력에는 집계값만 나간다.
QUERY = """
select es.id::text                as id,
       es.edit_type               as edit_type,
       es.status                  as status,
       es.source_kind             as source_kind,
       es.created_at              as created_at,
       es.completed_at            as completed_at,
       es.output_id::text         as output_id,
       es.edit_qc_result          as edit_qc_result,
       (select re.decision
          from public.edit_review_events re
         where re.edit_session_id = es.id
         order by re.created_at desc, re.id desc
         limit 1)                 as review_decision,
       exists (
         select 1 from public.analyses a
          where a.project_id = es.project_id
            and (a.result::text ilike '%%pattern%%' or a.result::text ilike '%%logo%%')
       )                          as has_pattern_or_logo
  from public.edit_sessions es
 order by es.created_at desc
 limit %s
"""

# 이 테이블들이 없으면 Phase 3 migration 이 아직 안 간 DB 다 — 조용히 0건으로 넘기지 않는다.
# events 테이블이 아직 없는 DB 에서도 나머지 집계는 돌아야 한다.
QUERY_NO_EVENTS = QUERY.replace(
    """(select re.decision
          from public.edit_review_events re
         where re.edit_session_id = es.id
         order by re.created_at desc, re.id desc
         limit 1)""", "null::text")

PRECHECK = "select to_regclass('public.edit_sessions'), to_regclass('public.edit_review_events')"


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 3 shadow 평가 리포트 (read-only)")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--jsonl", help="shadow_collect 가 남긴 samples.jsonl (DB 없이 집계)")
    ap.add_argument("--labels", help="blinded_label 이 남긴 labels.jsonl")
    ap.add_argument("--manifest", help="데이터셋 manifest.json")
    ap.add_argument("--dataset-id", help="라벨 결합 키. 없으면 manifest.datasetId 를 쓴다")
    ap.add_argument("--source-dir",
                    help="원본 이미지 디렉터리(기본: public/assets/fit-examples). "
                         "테스트가 사본을 쓸 때만 지정한다.")
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--image-usd", type=float, default=0.0,
                    help="이미지 1회 단가(USD). 안 주면 비용은 0 으로 두고 호출 수만 센다.")
    ap.add_argument("--vision-usd", type=float, default=0.0)
    ap.add_argument("--json", action="store_true", help="원 JSON 출력")
    args = ap.parse_args()

    if args.jsonl:
        # 수집 하네스 산출물 직접 집계 — DB 를 세우지 않고도 분포를 볼 수 있게.
        rows = []
        with open(args.jsonl, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                for k in ("created_at", "completed_at"):
                    if isinstance(r.get(k), (int, float)):
                        r[k] = datetime.fromtimestamp(r[k])
                r.setdefault("review_decision", None)
                r.setdefault("has_pattern_or_logo", False)
                rows.append(r)
        manifest = None
        manifest_load_error = None
        if args.manifest:
            # 읽기·파싱·타입 확인이 전부 실패할 수 있다. 어느 쪽이든 traceback 을
            # 사용자에게 던지지 않는다 — 원문에는 로컬 절대 경로가 들어 있다.
            try:
                raw_manifest = pathlib.Path(args.manifest).read_text(encoding="utf-8")
            except OSError:
                manifest_load_error = "manifest_unreadable"
            else:
                try:
                    parsed = json.loads(raw_manifest)
                except ValueError:
                    manifest_load_error = "manifest_not_json"
                else:
                    if isinstance(parsed, dict):
                        manifest = parsed
                    else:
                        manifest_load_error = "manifest_not_object"
        blocked = False
        binding_reasons: list[str] = []
        samples_file = pathlib.Path(args.jsonl).resolve()
        # 운영 기본값은 정본 한 곳(shadow_verification.DEFAULT_SOURCE_DIR). 테스트가
        # 사본을 실제로 변조해 볼 수 있도록 주입만 연다.
        source_dir = (pathlib.Path(args.source_dir).resolve() if args.source_dir
                      else sv.DEFAULT_SOURCE_DIR)

        if manifest_load_error:
            dataset = sv.unverified_dataset(rows, None, [manifest_load_error])
        else:
            # 검사는 전부 중앙 verifier 안에서 순서대로 일어난다 — CLI 가 따로
            # 조각조각 검사하면 그 조각이 곧 우회로가 된다. 반환값이 rows 까지
            # 소유하므로 다른 표본과 섞일 수 없다.
            dataset = sv.verify_dataset(
                manifest=manifest, rows=rows, samples_path=samples_file,
                source_dir=source_dir, cli_dataset_id=args.dataset_id)
        dataset_id = args.dataset_id or dataset.dataset_id
        binding_reasons = [] if dataset.trusted else list(dataset.blocked_reasons)
        if binding_reasons:
            print(f"manifest 를 신뢰할 수 없어요: {binding_reasons}", file=sys.stderr)
            blocked = True

        quarantined = []
        if args.labels and not binding_reasons:
            if not dataset_id:
                print("--labels 를 쓰려면 --dataset-id 또는 --manifest 가 필요해요.",
                      file=sys.stderr)
                return 2
            try:
                records = ba.load_labels(args.labels)
            except ba.LabelChainError as e:
                print(f"라벨 파일이 손상됐어요(append-only 체인 불일치): {e}",
                      file=sys.stderr)
                return 4
            eff = ba.effective_labels(records)
            # 라벨도 typed 변환이다 — 임의 rows 를 "라벨 결과"라고 주장해 넣을 수 없다.
            try:
                dataset, quarantined = sv.bind_verified_labels(dataset, eff)
            except sv.LabelBindingError as e:
                print(f"라벨 결합이 정본을 바꿨어요: {e}", file=sys.stderr)
                return 5
            if quarantined:
                by_reason = Counter(q.get("reason") for q in quarantined)
                print(f"라벨을 표본에 붙일 수 없어요 — 격리 {len(quarantined)}건: "
                      f"{dict(by_reason)}", file=sys.stderr)
                blocked = True

        out = report(dataset, image_usd=args.image_usd, vision_usd=args.vision_usd,
                     quarantined=quarantined)
        if args.json:
            print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        else:
            _render(out)
        # 라벨 결합이 깨졌으면 리포트는 만들되 성공으로 끝내지 않는다.
        return 5 if blocked else 0

    if not args.dsn:
        print("DATABASE_URL 이 없어요. --dsn 또는 --jsonl 을 주세요.", file=sys.stderr)
        return 2

    import psycopg
    from psycopg.rows import dict_row

    # read only 세션 — 이 스크립트가 무엇을 하든 DB 는 바뀌지 않는다.
    with psycopg.connect(args.dsn, row_factory=dict_row) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(PRECHECK)
            sessions, events = cur.fetchone().values()
            if sessions is None:
                print("edit_sessions 테이블이 없어요 — Phase 3 migration 미적용 DB 입니다.",
                      file=sys.stderr)
                return 3
            if events is None:
                print("경고: edit_review_events 가 없어 사용자 판단은 전부 null 로 집계돼요.",
                      file=sys.stderr)
            cur.execute(QUERY if events is not None else QUERY_NO_EVENTS,
                        (args.limit,))
            rows = cur.fetchall()

    # DB 조회는 검증 대상이 아니라 분포다 — 명시적 factory 로 감싼다.
    out = report(sv.distribution_dataset(rows), image_usd=args.image_usd,
                 vision_usd=args.vision_usd)
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        return 0
    _render(out)
    return 0


def _render(out) -> None:
    print(f"표본 {out['total']}건  ({out['samplesByPipeline']})")
    if out.get("calibrationUsable") is False:
        print(f"  ** 이 데이터셋은 캘리브레이션에 쓸 수 없어요: "
              f"{out.get('calibrationBlockedReasons')}")
    if out.get("labelQuarantine"):
        q = out["labelQuarantine"]
        print(f"  ** 격리된 라벨 {q['count']}건: {q['byReason']}")
    for name, p in out["pipelines"].items():
        print(f"\n{'=' * 62}\n{name}  —  {p['samples']}건")
        if not p["samples"]:
            print("  표본 없음 (insufficient_data)")
            continue
        print(f"  edit type      : {p['byEditType']}")
        print(f"  machine 판정   : {p['byMachineDecision']}")
        print(f"  사용자 판단    : {p['byUserDecision']}")
        print(f"  Vision 상태    : {p['byVisionStatus']}")
        print(f"  pattern/logo   : {p['byPatternOrLogo']}")
        cal = p["calibrationConfusion"]
        print(f"  calibration    : graded={cal['graded']} falsePass={cal['falsePass']} "
              f"(measured={cal['falsePassMeasured']})")
        c = p["confusion"]
        print(f"  confusion      : {c['matrix']}  (판단된 표본 {c['graded']})")
        print(f"    false pass 후보 {c['falsePassCandidates']} / 과잉검수 {c['overReview']}")
        print(f"  검수율         : {p['userReview']}")
        print(f"  충돌(측정≠비전): {p['measurementVisionConflict']}")
        print(f"  Vision 미가용  : {p['visionAvailability']['unavailableRate']}")
        print(f"  latency(s)     : {p['latencySeconds']}")
        print(f"  provider       : {p['provider']}")
        md = p["metricDistributions"]
        if md:
            print("  지표 분포(축: n p05/p50/p95):")
            for axis, d in md.items():
                mark = "" if d["sufficient"] else "  ← insufficient_data"
                print(f"    {axis:<18} n={d['n']:<5} "
                      f"{d['p05']:+.4f} / {d['p50']:+.4f} / {d['p95']:+.4f}{mark}")
        else:
            print("  지표 분포: 없음 (insufficient_data)")
        v = p["verdict"]
        print(f"  판정           : {v['status']}")
        for b in v["blockers"]:
            print(f"    - {b}")


if __name__ == "__main__":
    raise SystemExit(main())
