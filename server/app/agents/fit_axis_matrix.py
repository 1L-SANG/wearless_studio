"""T2 고도화 — 카테고리별 조정 축 **반영 측정 매트릭스** (순수 함수, DB/네트워크 없음).

목적: (카테고리, 축)마다 서열 **양 극단** 값을 골라, 그 축이 실제로 반영되는지 잴 비교 계획을 만든다.
극단부터 재는 이유 — 반영이 가장 크게 드러나야 할 지점이라 여기서 실패하면 그 축은 확실히 깨진 것.

codex 2라운드 반영:
- **배치·값 동반 스왑**: 좌우 counterbalance 시 이미지만 바꾸고 `value_left/value_right` 를 그대로 두면
  채점이 뒤집힌다. `comparison_plan` 이 (컷, 값)을 **한 레코드로 묶어** 발행해 불일치가 구조적으로 불가능.
- **treatment 는 양 배치 각 1회**, control(동일값)은 방향 대칭이라 1회.
- **복합 점수 없음**: 여기서는 비교 계획만 만든다. treatment−control 뺄셈 같은 추정량은 만들지 않는다
  (같은 이미지 재사용·종속·기권 분모 상이 — 원시 블록으로만 보고).

값 서열은 `mannequin_pairwise_qc._ORDINAL`(방향 심판의 정답 매핑)을 단일 정본으로 재사용하고,
성별별 실제 제공 값은 `fit_axes.FIT_AXES` 카탈로그로 교차 필터한다(카탈로그에 없는 값은 셀러가 못 고름).
"""

from .fit_axes import FIT_AXES
from .mannequin_pairwise_qc import _ORDINAL

# 측정 대상 (카테고리, 축) — FIT_AXES 실사 기준 10쌍.
AXIS_PAIRS: tuple[tuple[str, str], ...] = (
    ("top", "fit"), ("top", "length"),
    ("pants", "cut"), ("pants", "length"),
    ("skirt", "length"), ("skirt", "silhouette"),
    ("dress", "length"), ("dress", "silhouette"),
    ("outer", "fit"), ("outer", "length"),
)


def catalog_values(category: str, axis: str, gender: str) -> list[str]:
    """그 성별에게 실제 제공되는 축 값들. 카탈로그에 없으면 빈 리스트(예: skirt/dress men)."""
    entries = ((FIT_AXES.get(category) or {}).get(axis) or {}).get(gender) or []
    return [e["value"] for e in entries if isinstance(e, dict) and e.get("value")]


def extreme_pair(category: str, axis: str, gender: str) -> tuple[str, str] | None:
    """(low, high) — 서열 최소·최대 값. 카탈로그 교차 필터 후 2개 미만이거나 서열 미정의면 None.

    동순위 타이(pants cut 의 straight/tapered, bootcut/semi_wide)는 값 이름으로 결정적 tie-break.
    """
    order = _ORDINAL.get((category, axis))
    if not order:
        return None
    available = [v for v in catalog_values(category, axis, gender) if v in order]
    if len(available) < 2:
        return None
    ranked = sorted(available, key=lambda v: (order[v], v))  # 결정적
    low, high = ranked[0], ranked[-1]
    if order[low] == order[high]:  # 전 값이 동순위 → 대비 불가
        return None
    return low, high


def all_pairs(gender: str = "women") -> list[dict]:
    """측정 가능한 (카테고리, 축) 전부 → [{category, axis, gender, low, high}]. 불가한 쌍은 제외."""
    out: list[dict] = []
    for category, axis in AXIS_PAIRS:
        pair = extreme_pair(category, axis, gender)
        if pair is None:
            continue
        out.append({"category": category, "axis": axis, "gender": gender,
                    "low": pair[0], "high": pair[1]})
    return out


def cut_labels(reps: int = 2) -> list[tuple[str, str]]:
    """생성 순서 — **교차**(A0,B0,A1,B1)로 시간 드리프트가 값과 정렬되지 않게 한다(codex).
    반환: [(label, side)] where side ∈ {'low','high'}."""
    seq: list[tuple[str, str]] = []
    for i in range(reps):
        seq.append((f"A{i}", "low"))
        seq.append((f"B{i}", "high"))
    return seq


def comparison_plan(low: str, high: str, reps: int = 2) -> list[dict]:
    """축당 방향 비교 계획.

    - treatment: (Aᵢ, Bᵢ) 를 **양 배치 각 1회** — 좌우 배치 편향 상쇄.
    - control: (A₀,A₁), (B₀,B₁) — 동일 값이라 기대='similar'; 방향 답이 나오면 오검출.

    각 레코드가 (컷 라벨, 그 자리의 값)을 **함께** 들고 있어 배치를 바꿔도 채점 인자가 어긋날 수 없다.
    """
    plan: list[dict] = []
    for i in range(reps):
        a, b = f"A{i}", f"B{i}"
        plan.append({"kind": "treatment", "orientation": "ab",
                     "leftCut": a, "rightCut": b, "valueLeft": low, "valueRight": high})
        plan.append({"kind": "treatment", "orientation": "ba",
                     "leftCut": b, "rightCut": a, "valueLeft": high, "valueRight": low})
    if reps >= 2:
        plan.append({"kind": "control", "orientation": "ab",
                     "leftCut": "A0", "rightCut": "A1", "valueLeft": low, "valueRight": low})
        plan.append({"kind": "control", "orientation": "ab",
                     "leftCut": "B0", "rightCut": "B1", "valueLeft": high, "valueRight": high})
    return plan


# ── 사전 등록 의심-축 규칙 (데이터 열기 전 고정 — codex PARTIAL-3) ──────────────
# treatment 방향 정답이 절반 미만이거나, 절대 판정에 fail 이 하나라도 있으면 '의심'.
# 기권(abstain)은 분자·분모 어디에도 안 넣는다(통과도 실패도 아님).
SUSPECT_MIN_TREATMENT_PASS_FRACTION = 0.5


def is_suspect(treatment_pass: int, treatment_scored: int, absolute_fail: int) -> bool:
    """의심 축 판정(순수). treatment_scored 는 기권 제외 채점 수.
    채점 가능한 treatment 가 하나도 없으면 판정 보류가 아니라 **의심**으로 본다(증거 없음 = 반영 미확인)."""
    if absolute_fail > 0:
        return True
    if treatment_scored <= 0:
        return True
    return (treatment_pass / treatment_scored) < SUSPECT_MIN_TREATMENT_PASS_FRACTION
