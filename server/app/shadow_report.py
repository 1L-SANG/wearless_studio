"""Shadow 평가 집계 — **순수 함수만**. DB·네트워크에 손대지 않는다 (Phase 3 P0-C 9/N).

임계값을 바꾸려면 먼저 지금 무슨 일이 벌어지고 있는지 알아야 한다. 이 모듈은 이미
저장된 edit_session 행들을 받아 "무엇을 몇 건 봤고, 기계와 사람의 판단이 어디서
갈렸는지"를 센다. 새로 만드는 데이터는 없다 — provider 를 부르지 않는다.

두 파이프라인(마네킹 승인컷 편집 / 에디터 vary)은 **절대 섞지 않는다**. 같은 지표
이름을 쓰지만 입력 분포도 실패 모드도 다르다. 섞은 평균으로 임계값을 정하면 둘 다
틀린 값이 된다.

표본이 모자라면 숫자를 만들어 내지 않고 insufficient_data 로 표시한다. 임계값
후보를 낼 자격은 표본이 정하지, 우리 조바심이 정하지 않는다.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

# 이 밑으로는 분포를 말할 수 없다고 본다. 축별로 따로 적용한다.
MIN_SAMPLES = 30

PIPELINES = ("mannequin_edit", "editor_vary", "unknown")

# 정책상 자동 통과 대상이 아닌 edit type — enforce 후보 계산에서 뺀다.
# 여기 있는 표본을 섞으면 "review 율이 높다"가 정책 때문인지 품질 때문인지 못 가른다.
NEVER_AUTO_PASS_TYPES = ("CUSTOM_REVIEW_REQUIRED",)
DECISIONS = ("pass", "review_required", "reject", "failed")
USER_DECISIONS = ("accepted", "rejected")


def pipeline_of(row) -> str:
    """source_kind 가 파이프라인을 가른다 — edit_type 은 두 쪽에 다 나온다.

    미상은 mannequin 으로 떨어뜨리지 않는다. 모르는 표본을 한쪽 분포에 섞으면 그
    분포가 조용히 오염되고, 오염된 분포로 임계값을 정하면 그게 운영에 나간다.
    """
    kind = row.get("source_kind")
    if kind == "editor_asset":
        return "editor_vary"
    if kind == "approved_baseline":
        return "mannequin_edit"
    return "unknown"


def machine_decision(row) -> str:
    """기계 판정의 정본은 edit_qc_result.decision 이다 — status 가 아니다.

    status 는 워크플로 상태(잡이 어디까지 갔나)고, decision 은 결과 판정이다.
    섞으면 provider 실패가 reject 로 둔갑해 false pass/reject 통계가 전부 틀어진다.
    """
    from app.services.edit_qc_scope import machine_decision as _md
    return _md(row.get("edit_qc_result"), had_output=bool(row.get("output_id")))


def workflow_status(row) -> str:
    """잡/세션 상태. 판정과 **다른 축**으로만 집계한다."""
    return str(row.get("status") or "unknown")


# analyses.result 에 패턴·로고를 뜻하는 구조화 필드가 있으면 그것만 본다.
# 문자열 검색(ILIKE '%pattern%')은 styleTags 같은 무관한 텍스트에 걸려 거짓 양성을 만든다.
_PATTERN_KEYS = ("pattern", "patternType", "hasPattern")
_LOGO_KEYS = ("logo", "hasLogo", "logoAssets", "graphics")
_EMPTY_PATTERN_VALUES = {"none", "solid", "plain", "no", "false", ""}


def pattern_or_logo(row) -> str:
    """true | false | unknown.

    현재 analyses.result 스키마에는 이 신호가 **없다**(clothingType·materials·styleTags…).
    없는 걸 있다고 말하지 않는다 — 키가 아예 없으면 unknown 이다. false 로 적으면
    "패턴 없는 표본 30건"이라는 존재하지 않는 사실이 리포트에 남는다.
    """
    src = row.get("analysis") if isinstance(row.get("analysis"), dict) else None
    if src is None:
        explicit = row.get("has_pattern_or_logo")
        return "true" if explicit is True else (
            "false" if explicit is False and row.get("pattern_source") == "structured"
            else "unknown")
    present = False
    for key in (*_PATTERN_KEYS, *_LOGO_KEYS):
        if key not in src:
            continue
        present = True
        v = src[key]
        if v is None or v is False:
            continue
        if isinstance(v, str) and v.strip().lower() in _EMPTY_PATTERN_VALUES:
            continue
        if isinstance(v, (list, tuple, dict)) and not v:
            continue
        return "true"
    return "false" if present else "unknown"


def _qc(row) -> dict:
    qc = row.get("edit_qc_result")
    return qc if isinstance(qc, dict) else {}


def _vision(row) -> dict:
    v = _qc(row).get("vision")
    return v if isinstance(v, dict) else {}


def vision_status(row) -> str:
    """ok | timeout | provider_error | skipped | unavailable(기록 자체가 없음)."""
    meta = _vision(row).get("meta")
    if not isinstance(meta, dict):
        return "unavailable"
    return str(meta.get("status") or "unavailable")


def _quantiles(values: list[float]) -> dict:
    """p05/p50/p95 + 평균. 표본이 적으면 그대로 적되 신뢰하지 말라고 n 을 함께 준다."""
    if not values:
        return {"n": 0}
    s = sorted(values)

    def at(p):
        if len(s) == 1:
            return s[0]
        idx = min(len(s) - 1, max(0, int(math.ceil(p * len(s)) - 1)))
        return s[idx]

    return {"n": len(s), "min": s[0], "p05": at(0.05), "p50": at(0.50),
            "p95": at(0.95), "max": s[-1],
            "mean": sum(s) / len(s)}


def _sufficient(n: int, minimum: int = MIN_SAMPLES) -> bool:
    return n >= minimum


def metric_distributions(rows) -> dict:
    """정량 지표(delta)의 분포. 축 이름은 edit_intent_qc 가 쓰는 것 그대로."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        delta = _qc(r).get("metrics", {}).get("delta")
        if not isinstance(delta, dict):
            continue
        for axis, value in delta.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                buckets[axis].append(float(value))
    out = {}
    for axis, values in sorted(buckets.items()):
        dist = _quantiles(values)
        dist["sufficient"] = _sufficient(dist["n"])
        out[axis] = dist
    return out


def vision_confidence(rows) -> dict:
    vals = []
    for r in rows:
        obs = _vision(r).get("observation")
        if isinstance(obs, dict):
            c = obs.get("confidence")
            if isinstance(c, (int, float)) and not isinstance(c, bool):
                vals.append(float(c))
    dist = _quantiles(vals)
    dist["sufficient"] = _sufficient(dist["n"])
    return dist


def decision_rates(rows) -> dict:
    counts = Counter(machine_decision(r) for r in rows)
    total = sum(counts[d] for d in DECISIONS)
    return {"n": total,
            "counts": {d: counts.get(d, 0) for d in DECISIONS},
            "rates": ({d: counts.get(d, 0) / total for d in DECISIONS} if total else {}),
            "sufficient": _sufficient(total)}


def user_review_rates(rows) -> dict:
    """검수가 필요했던 결과 중 사람이 실제로 판단한 비율."""
    needed = [r for r in rows if machine_decision(r) == "review_required"]
    decided = [r for r in needed if r.get("review_decision") in USER_DECISIONS]
    counts = Counter(r.get("review_decision") for r in decided)
    return {"reviewRequired": len(needed),
            "reviewed": len(decided),
            "unreviewed": len(needed) - len(decided),
            "counts": {d: counts.get(d, 0) for d in USER_DECISIONS},
            "acceptedRate": (counts.get("accepted", 0) / len(decided)) if decided else None,
            "sufficient": _sufficient(len(decided))}


def confusion(rows) -> dict:
    """machine 판정 × 사용자 판단.

    가장 중요한 칸은 `pass × rejected` 다 — 기계가 통과시킨 걸 사람이 거절했다는 뜻이라
    **false pass 후보**다. 반대 칸(review_required × accepted)은 과잉 검수 신호다.
    """
    cell: dict[str, Counter] = {d: Counter() for d in DECISIONS}
    for r in rows:
        ud = r.get("review_decision")
        d = machine_decision(r)
        if ud in USER_DECISIONS and d in cell:
            cell[d][ud] += 1
    matrix = {d: {u: cell[d].get(u, 0) for u in USER_DECISIONS} for d in DECISIONS}
    graded = sum(v for row in matrix.values() for v in row.values())
    return {"matrix": matrix,
            "graded": graded,
            "falsePassCandidates": matrix["pass"]["rejected"],
            "overReview": matrix["review_required"]["accepted"],
            "sufficient": _sufficient(graded)}


def violation_rates(rows) -> dict:
    """locked invariant 위반이 무엇 때문에 몇 번 잡혔나."""
    counts: Counter = Counter()
    unexpected: Counter = Counter()
    for r in rows:
        qc = _qc(r)
        for v in qc.get("lockedInvariantViolations") or ():
            counts[str(v)] += 1
        for u in qc.get("unexpectedChanges") or ():
            unexpected[str(u)] += 1
    n = len(rows)
    return {"n": n,
            "lockedInvariantViolations": {k: {"count": c, "rate": c / n if n else None}
                                          for k, c in counts.most_common()},
            "unexpectedChanges": {k: {"count": c, "rate": c / n if n else None}
                                  for k, c in unexpected.most_common()}}


def conflict_rate(rows) -> dict:
    """정량은 통과인데 Vision 은 '요청이 반영 안 됐다'고 한 경우 — 자동 통과 금지 대상."""
    conflicts = 0
    considered = 0
    for r in rows:
        qc = _qc(r)
        obs = _vision(r).get("observation")
        if not isinstance(obs, dict) or vision_status(r) != "ok":
            continue
        considered += 1
        satisfied = qc.get("requestedChangeSatisfied")
        applied = obs.get("requestedChangeApplied")
        if satisfied is True and applied is False:
            conflicts += 1
    return {"considered": considered, "conflicts": conflicts,
            "rate": conflicts / considered if considered else None,
            "sufficient": _sufficient(considered)}


def vision_availability(rows) -> dict:
    counts = Counter(vision_status(r) for r in rows)
    n = len(rows)
    unavailable = sum(c for s, c in counts.items() if s != "ok")
    return {"n": n, "counts": dict(counts),
            "unavailableRate": unavailable / n if n else None,
            "sufficient": _sufficient(n)}


def latency(rows) -> dict:
    """created_at → completed_at (초). tz 계산은 호출자가 이미 datetime 으로 준 값 기준."""
    secs = []
    for r in rows:
        a, b = r.get("created_at"), r.get("completed_at")
        if a is None or b is None:
            continue
        try:
            secs.append((b - a).total_seconds())
        except (TypeError, AttributeError):
            continue
    dist = _quantiles(secs)
    dist["sufficient"] = _sufficient(dist["n"])
    return dist


def provider_cost(rows, *, image_usd: float = 0.0, vision_usd: float = 0.0) -> dict:
    """호출 수와 **추정** 비용. 단가는 인자로 받는다 — 코드에 박아 두면 곧 거짓말이 된다."""
    # 시도 횟수를 그대로 센다 — 성공 여부로 역산하면 실패·재시도 비용이 빠진다.
    image_calls = sum(int(r.get("image_calls") or 0) for r in rows)
    vision_calls = sum(int(r.get("vision_calls") or 0) for r in rows)
    return {"imageCalls": image_calls, "visionCalls": vision_calls,
            "estimatedUsd": round(image_calls * image_usd + vision_calls * vision_usd, 4),
            "unitPricesProvided": bool(image_usd or vision_usd)}


def by_axis(rows, key) -> dict:
    """축별 표본 수 — 어디가 비어 있는지 먼저 보이게."""
    counts = Counter(key(r) for r in rows)
    return {str(k): counts[k] for k in sorted(counts, key=lambda x: (x is None, str(x)))}


def _axis_block(subset, *, image_usd, vision_usd) -> dict:
    """한 묶음(파이프라인 또는 edit type)에 대한 지표 한 벌."""
    return {
        "samples": len(subset),
        "byMachineDecision": by_axis(subset, machine_decision),
        "byWorkflowStatus": by_axis(subset, workflow_status),
        "byUserDecision": by_axis(subset, lambda r: r.get("review_decision")),
        "byVisionStatus": by_axis(subset, vision_status),
        "byPatternOrLogo": by_axis(subset, pattern_or_logo),
        "decisionRates": decision_rates(subset),
        "userReview": user_review_rates(subset),
        "humanLabels": human_label_coverage(subset),
        "confusion": confusion(subset),
        "metricDistributions": metric_distributions(subset),
        "visionConfidence": vision_confidence(subset),
        "violations": violation_rates(subset),
        "measurementVisionConflict": conflict_rate(subset),
        "visionAvailability": vision_availability(subset),
        "latencySeconds": latency(subset),
        "provider": provider_cost(subset, image_usd=image_usd, vision_usd=vision_usd),
    }


def human_label_coverage(rows) -> dict:
    """사람이 라벨한 비율 — **pass 표본**이 핵심이다.

    false pass 는 기계가 통과시킨 것을 사람이 봐야만 드러난다. pass 표본에 라벨이
    없으면 false pass 율은 0 이 아니라 **미측정**이다. 그 둘을 같게 취급하면
    "false pass 0건"이라는 없는 근거로 enforce 를 켜게 된다.
    """
    passes = [r for r in rows if machine_decision(r) == "pass"]
    labeled_pass = [r for r in passes if r.get("human_label") or r.get("review_decision")]
    labeled_all = [r for r in rows if r.get("human_label") or r.get("review_decision")]
    return {"passSamples": len(passes),
            "passLabeled": len(labeled_pass),
            "passCoverage": (len(labeled_pass) / len(passes)) if passes else None,
            "labeledTotal": len(labeled_all),
            "sufficient": _sufficient(len(labeled_pass))}


def _verdict(rows, *, is_enforce_eligible: bool = True) -> dict:
    """enforce 로 갈 수 있는지에 대한 **기계적** 판단. 사람의 승인을 대신하지 않는다."""
    reasons = []
    conf = confusion(rows)
    dec = decision_rates(rows)
    vis = vision_availability(rows)
    hl = human_label_coverage(rows)

    if not is_enforce_eligible:
        reasons.append("정책상 자동 통과 대상이 아닌 edit type (enforce 후보 제외)")
    if not _sufficient(dec["n"]):
        reasons.append(f"표본 부족: {dec['n']} < {MIN_SAMPLES}")
    if not conf["sufficient"]:
        reasons.append(f"사람이 판단한 표본 부족: {conf['graded']} < {MIN_SAMPLES}")
    # pass 표본에 사람 라벨이 없으면 false pass 는 '0건'이 아니라 '미측정'이다.
    if hl["passSamples"] and not hl["sufficient"]:
        reasons.append(f"pass 표본 human label 부족: {hl['passLabeled']}/{hl['passSamples']}")
    if not hl["passSamples"]:
        reasons.append("pass 표본 0건 — false pass 를 측정할 대상이 없다")
    if conf["falsePassCandidates"] > 0:
        reasons.append(f"기계 pass 를 사람이 거절한 사례 {conf['falsePassCandidates']}건")
    if vis["unavailableRate"] is not None and vis["unavailableRate"] > 0.2:
        reasons.append(f"Vision 미가용률 {vis['unavailableRate']:.0%}")

    status = "insufficient_data" if (not dec["n"] or not conf["graded"]) else (
        "enforce_candidate" if not reasons else "shadow_only")
    return {"enforceReady": not reasons, "blockers": reasons, "status": status}


def report(rows, *, image_usd: float = 0.0, vision_usd: float = 0.0,
           manifest: dict | None = None) -> dict:
    """파이프라인 → edit type 순으로 **두 번** 쪼갠다.

    파이프라인만 나누면 BACKGROUND_ONLY 6건이 CUSTOM 24건에 묻혀 "editor_vary 30건"
    으로 보인다. 그 30건에는 정책상 애초에 통과할 수 없는 표본이 섞여 있어 임계값
    근거가 못 된다.
    """
    split: dict[str, list] = {p: [] for p in PIPELINES}
    for r in rows:
        split[pipeline_of(r)].append(r)

    out = {"total": len(rows),
           "samplesByPipeline": {p: len(v) for p, v in split.items()},
           "unknownPipelineSamples": [r.get("id") for r in split["unknown"]][:50],
           "pipelines": {}}
    if manifest is not None:
        out["manifest"] = manifest
        if manifest.get("validForCalibration") is False:
            out["calibrationUsable"] = False
            out["calibrationBlockedReasons"] = manifest.get("invalidReasons") or []

    for p, subset in split.items():
        block = _axis_block(subset, image_usd=image_usd, vision_usd=vision_usd)
        block["byEditType"] = by_axis(subset, lambda r: r.get("edit_type"))
        by_type = {}
        for etype in sorted({str(r.get("edit_type")) for r in subset}):
            rows_t = [r for r in subset if str(r.get("edit_type")) == etype]
            eligible = etype not in NEVER_AUTO_PASS_TYPES
            tb = _axis_block(rows_t, image_usd=image_usd, vision_usd=vision_usd)
            tb["enforceEligible"] = eligible
            tb["verdict"] = _verdict(rows_t, is_enforce_eligible=eligible)
            by_type[etype] = tb
        block["byEditTypeDetail"] = by_type
        # 파이프라인 판정은 enforce 후보 edit type 만 모아서 낸다.
        eligible_rows = [r for r in subset
                         if str(r.get("edit_type")) not in NEVER_AUTO_PASS_TYPES]
        block["enforceEligibleSamples"] = len(eligible_rows)
        block["verdict"] = _verdict(eligible_rows) if p != "unknown" else {
            "enforceReady": False, "status": "insufficient_data",
            "blockers": ["source_kind 미상 — 어느 파이프라인인지 모른다"]}
        out["pipelines"][p] = block
    return out
