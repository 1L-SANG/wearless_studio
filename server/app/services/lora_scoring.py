"""후보 체크포인트 채점·선정. 순위 규칙의 정본은 v6_kit/score_v7_verify.py 다.

  1순위 **컷 간 일관성 중앙값** — 한 체크포인트로 여러 컷을 그렸을 때 그 얼굴들이 서로 같은가.
  2순위 **개별 중앙값** — 각 렌더가 기준셋(등록자의 held-out 기준 3장)과 얼마나 닮았나.

왜 일관성이 1순위인가: 개별 점수는 기준셋과 같은 원본에서 온 컨트롤로 그린 렌더라 조금 후하다.
일관성은 서로 다른 렌더끼리 비교라 그 편향이 없다 — "이 사람으로 일관되게 그려지는가"가
실제로 팔 수 있는가를 가른다.

합격선은 **개별 중앙 0.70** 이다. 그 밑이면 이 학습은 실패로 본다(관리자 알림) — 그 가중치로
착용컷을 만들면 셀러가 산 적 없는 얼굴이 나간다.

이 모듈은 **순수 함수**다. 파일도 GPU 도 안 만진다 — 파드가 올린 result.json 한 벌을 받는다.
"""
from __future__ import annotations

import itertools
import logging
import math

log = logging.getLogger("wearless.lora_scoring")

#: 합격선 — 개별 중앙(기준셋 대비). 이 밑은 실패다.
PASS_MEDIAN = 0.70
#: 채점 창. 원본과 같은 값 — 창 밖 렌더는 미채점이다(정면 아닌 컷은 점수가 의미 없다).
YAW_MAX = 0.16
EYE_MIN, EYE_MAX = 40.0, 200.0


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def median(values):
    kept = sorted(value for value in values if value is not None)
    if not kept:
        return None
    middle = len(kept) // 2
    return kept[middle] if len(kept) % 2 else (kept[middle - 1] + kept[middle]) / 2


def consistency(features) -> dict:
    """컷 간 pairwise 코사인의 중앙/최저/표준편차. 원본 cons() 와 같은 값."""
    kept = [f for f in features if f]
    pairs = [cosine(a, b) for a, b in itertools.combinations(kept, 2)]
    if not pairs:
        return {"median": None, "min": None, "pairs": 0}
    mean = sum(pairs) / len(pairs)
    std = math.sqrt(sum((p - mean) ** 2 for p in pairs) / len(pairs))
    return {"median": round(median(pairs), 3), "min": round(min(pairs), 3),
            "std": round(std, 3), "pairs": len(pairs)}


def score_checkpoint(renders, references) -> dict:
    """한 체크포인트의 점수.

    renders: [{"id": str, "adopted": bool, "embedding": [..] | None, "why": str}]
    references: 기준셋 임베딩들(등록자의 held-out 기준 사진).
    """
    kept = [r for r in renders if r.get("adopted") and r.get("embedding")]
    individual = {r["id"]: round(median([cosine(r["embedding"], ref) for ref in references]), 3)
                  for r in kept}
    return {
        "median": median(list(individual.values())),
        "n": len(individual),
        "individual": individual,
        "consistency": consistency([r["embedding"] for r in kept]),
        "gate_fail": [r["id"] for r in renders if not r.get("adopted")],
    }


def rank(scores: dict) -> list[str]:
    """1순위 일관성 중앙 ↓, 2순위 개별 중앙 ↓. 원본 rank() 와 같은 키다."""
    return sorted(scores, key=lambda step: (-(scores[step]["consistency"]["median"] or 0),
                                            -(scores[step]["median"] or 0)))


def select(scores: dict) -> tuple[str | None, str | None]:
    """(고른 체크포인트, 실패 사유). 합격선 미달이면 (None, 사유)."""
    if not scores:
        return None, "채점할 후보가 없다"
    order = rank(scores)
    best = order[0]
    value = scores[best]["median"]
    if value is None:
        return None, "게이트를 통과한 렌더가 없어 개별 중앙을 낼 수 없다"
    if value < PASS_MEDIAN:
        return None, f"개별 중앙 {value:.3f} < 합격선 {PASS_MEDIAN:.2f}"
    return best, None


def evaluate(result: dict) -> dict:
    """파드 result.json → {scores, rank, selected, reason}. 이게 이 모듈의 유일한 진입점이다."""
    references = [ref for ref in (result.get("references") or []) if ref]
    if len(references) < 3:
        return {"scores": {}, "rank": [], "selected": None,
                "reason": f"기준셋 {len(references)}장 < 3 — 채점 기준이 없다"}
    scores = {step: score_checkpoint(renders, references)
              for step, renders in (result.get("checkpoints") or {}).items()}
    selected, reason = select(scores)
    log.info("lora scoring: 후보 %d · 선정 %s (%s)", len(scores), selected, reason or "합격")
    return {"scores": scores, "rank": rank(scores), "selected": selected, "reason": reason}
