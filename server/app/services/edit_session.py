"""Edit Session 계약 — edit type·구조화 adjustment·허용 범위·상태 전이 (순수 함수).

여기서 결정하는 것은 **서버의 몫**이다. 클라이언트가 "이건 바꿔도 된다"고 말해서 잠금이
풀리면 잠금이 아니다. allowed scope 도 locked invariants 도 edit type 하나에서 유도한다.

자유 텍스트를 계약으로 쓰지 않는다: 편집 요청은 step(-2..2)과 그 파생 비율로만 들어온다.
지금 단계에서 cm 정확도를 보장하지 않으며, 그렇게 주장하지도 않는다 — step 은 방향과
정도의 단계값이다.
"""

# ── edit type ────────────────────────────────────────────────────────────────
# UI 가 실제로 지원하는 것과 계약이 아는 것을 구분한다. 지원하지 않는 타입을 지원하는 척
# 통과시키면 "요청은 받았는데 아무것도 안 바뀐" 결과가 정상 출고된다.
EDIT_TYPES = (
    "GARMENT_LENGTH_ONLY",
    "BODY_WIDTH_ONLY",
    "SLEEVE_LENGTH_ONLY",
    "SHOULDER_WIDTH_ONLY",
    "TUCK_STATE_ONLY",
    "MANNEQUIN_VOLUME_ONLY",
    "BACKGROUND_ONLY",
    "LIGHTING_ONLY",
    "CUSTOM_REVIEW_REQUIRED",
)

# 이번 릴리스에서 **실제로 편집 지시를 만들 수 있는** 타입. 나머지는 요청되면
# CUSTOM_REVIEW_REQUIRED 로 강등한다(자동 PASS 불가) — 조용히 성공시키지 않는다.
SUPPORTED_EDIT_TYPES = (
    "GARMENT_LENGTH_ONLY",
    "SLEEVE_LENGTH_ONLY",
    "BODY_WIDTH_ONLY",
    "SHOULDER_WIDTH_ONLY",
    "MANNEQUIN_VOLUME_ONLY",
    "TUCK_STATE_ONLY",
)

# edit type → 그 타입이 건드릴 수 있는 **유일한** step 필드.
_TYPE_FIELD = {
    "GARMENT_LENGTH_ONLY": "garmentLengthStep",
    "SLEEVE_LENGTH_ONLY": "sleeveLengthStep",
    "BODY_WIDTH_ONLY": "bodyWidthStep",
    "SHOULDER_WIDTH_ONLY": "shoulderWidthStep",
    "MANNEQUIN_VOLUME_ONLY": "mannequinVolumeStep",
    "TUCK_STATE_ONLY": "tuckStateStep",
}

STEP_FIELDS = (
    "garmentLengthStep", "sleeveLengthStep", "bodyWidthStep",
    "shoulderWidthStep", "mannequinVolumeStep", "tuckStateStep",
)
ALLOWED_STEPS = (-2, -1, 0, 1, 2)

# step → 목표 변화 비율. 측정 QC 가 "요청한 변화가 실제로 일어났는가"를 재는 기준이 된다.
# 정확한 cm 이 아니라 **관측 가능한 방향과 대략의 크기**다.
_STEP_RATIO = {-2: -0.16, -1: -0.08, 0: 0.0, 1: 0.08, 2: 0.16}

# ── 허용/금지 범위 ───────────────────────────────────────────────────────────
# 어떤 edit type 에서도 절대 바뀌면 안 되는 것들(기획서 12.4).
ALWAYS_FORBIDDEN = (
    "mannequinIdentity", "pose", "camera", "framing", "garmentCategory",
    "pattern", "logo", "collarType", "buttonCount", "pocketCount",
)

_TYPE_ALLOWED = {
    "GARMENT_LENGTH_ONLY": ("garmentLength",),
    "SLEEVE_LENGTH_ONLY": ("sleeveLength",),
    "BODY_WIDTH_ONLY": ("bodyWidth",),
    "SHOULDER_WIDTH_ONLY": ("shoulderWidth",),
    "MANNEQUIN_VOLUME_ONLY": ("mannequinVolume",),
    "TUCK_STATE_ONLY": ("tuckState",),
    "BACKGROUND_ONLY": ("background",),
    "LIGHTING_ONLY": ("lighting",),
    "CUSTOM_REVIEW_REQUIRED": (),
}

# 조정 가능한 전체 축 — 허용 목록에 없으면 전부 금지다(allowlist 방식).
_ALL_MUTABLE = (
    "garmentLength", "sleeveLength", "bodyWidth", "shoulderWidth",
    "mannequinVolume", "tuckState", "background", "lighting",
)

STATUSES = ("queued", "running", "pass", "review_required", "reject", "failed")
TERMINAL = ("pass", "review_required", "reject", "failed")
_TRANSITIONS = {
    "queued": ("running", "failed"),
    # `running → running` 은 **재진입**이다. 워커가 running 을 커밋한 직후 죽으면 잡이
    # requeue 되고, 이어받은 워커는 같은 세션을 running 으로 다시 표시하려 한다. 그것을
    # 금지하면 복구 가능한 크래시가 사용자에게 "이미 처리된 편집 요청이에요" 라는 종결
    # 오류로 나가고 세션은 영원히 running 에 남는다(2026-08-10 확인).
    #
    # provider 를 두 번 부르는 것은 이 전이가 막는 것이 아니라 **잡 행에 영속된 이미지
    # 예산**이 막는다 — 그쪽이 requeue 를 건너 살아남는 방어다.
    "running": ("pass", "review_required", "reject", "failed", "running"),
    "pass": (),
    "review_required": (),
    "reject": (),
    "failed": (),
}


class EditRequestError(ValueError):
    """구조화 요청 계약 위반 — 라우트가 400 으로 바꾼다."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def normalize_edit_type(edit_type) -> str:
    """알 수 없는/미지원 타입은 **CUSTOM_REVIEW_REQUIRED 로 강등**한다(자동 PASS 불가)."""
    if not isinstance(edit_type, str) or edit_type not in EDIT_TYPES:
        raise EditRequestError("unsupported_edit_type",
                               "지원하지 않는 편집 종류예요.")
    if edit_type not in SUPPORTED_EDIT_TYPES:
        return "CUSTOM_REVIEW_REQUIRED"
    return edit_type


def validate_adjustments(edit_type: str, adjustments) -> dict:
    """구조화 adjustment 검증 → 정규화된 dict. 위반은 EditRequestError.

    규칙:
      · 알 수 없는 필드 거부(오타를 조용히 무시하면 "요청했는데 아무 일도 안 일어남"이 된다)
      · step 은 -2..2 정수만
      · **edit type 과 무관한 축의 0 이 아닌 값 거부** — GARMENT_LENGTH_ONLY 로 들어와서
        소매까지 바꾸는 요청은 그 이름이 거짓이 된다
      · 해당 축이 0 이면 거부 — 아무것도 바꾸지 않는 편집은 호출 낭비다
    """
    if not isinstance(adjustments, dict):
        raise EditRequestError("invalid_adjustments", "조정 값 형식이 올바르지 않아요.")
    unknown = set(adjustments) - set(STEP_FIELDS)
    if unknown:
        raise EditRequestError("unknown_adjustment_field",
                               f"알 수 없는 조정 항목: {sorted(unknown)}")
    out = {}
    for field in STEP_FIELDS:
        v = adjustments.get(field, 0)
        if isinstance(v, bool) or not isinstance(v, int):
            raise EditRequestError("invalid_step", f"{field} 는 정수 단계값이어야 해요.")
        if v not in ALLOWED_STEPS:
            raise EditRequestError("step_out_of_range",
                                   f"{field} 는 -2~2 만 가능해요.")
        out[field] = v

    if edit_type == "CUSTOM_REVIEW_REQUIRED":
        return out            # 사람이 볼 것이므로 축 제한을 걸지 않는다(자동 PASS 는 금지)

    own = _TYPE_FIELD.get(edit_type)
    if own is None:            # BACKGROUND_ONLY / LIGHTING_ONLY — step 축이 없다
        if any(out[f] != 0 for f in STEP_FIELDS):
            raise EditRequestError("adjustment_not_allowed_for_type",
                                   "이 편집 종류에는 단계 조정이 없어요.")
        return out
    foreign = [f for f in STEP_FIELDS if f != own and out[f] != 0]
    if foreign:
        raise EditRequestError("adjustment_outside_edit_type",
                               f"{edit_type} 에서는 {sorted(foreign)} 를 바꿀 수 없어요.")
    if out[own] == 0:
        raise EditRequestError("no_change_requested", "변경할 단계를 선택해 주세요.")
    return out


def target_delta_ratio(edit_type: str, adjustments: dict) -> float | None:
    """요청한 변화의 목표 비율. 측정 QC 의 기대치 — 없으면 None(측정 대상 아님)."""
    field = _TYPE_FIELD.get(edit_type)
    if field is None:
        return None
    return _STEP_RATIO.get(adjustments.get(field, 0))


def allowed_scope(edit_type: str) -> dict:
    """서버가 정하는 허용/금지 범위. 클라이언트 입력은 여기 관여하지 않는다."""
    allowed = _TYPE_ALLOWED.get(edit_type, ())
    forbidden = tuple(a for a in _ALL_MUTABLE if a not in allowed) + ALWAYS_FORBIDDEN
    return {"editType": edit_type, "allowed": list(allowed),
            "forbidden": sorted(set(forbidden))}


def locked_invariants_for_edit(baseline_invariants: dict | None,
                               edit_type: str) -> dict:
    """baseline 스냅샷 + 이 편집의 금지 항목.

    baseline 이 "모른다"고 기록한 항목은 계속 모르는 채로 둔다 — 편집 때문에 값이 생기지
    않는다. 다만 **잠겨 있다는 사실**은 별도로 기록한다(값의 유무와 잠금 여부는 다른 축).
    """
    scope = allowed_scope(edit_type)
    base = dict(baseline_invariants or {})
    locks = {}
    for key in scope["forbidden"]:
        snap = base.get(key)
        locks[key] = {
            "locked": True,
            "baselineValue": snap if snap is not None else {"status": "unavailable",
                                                            "reason": "not_in_baseline"},
        }
    for key in scope["allowed"]:
        locks[key] = {"locked": False,
                      "baselineValue": base.get(key) or {"status": "unavailable",
                                                         "reason": "not_in_baseline"}}
    return {"baselineSnapshot": base, "scope": scope, "locks": locks}


def can_transition(current: str, nxt: str) -> bool:
    return nxt in _TRANSITIONS.get(current, ())


def assert_transition(current: str, nxt: str) -> None:
    if not can_transition(current, nxt):
        raise ValueError(f"invalid edit_session transition: {current} → {nxt}")
