"""등록 사진 슬롯 — **LoRA 학습 촬영 스펙 그대로**(17칸).

2026-09-14 결정: 등록 사진을 v7 LoRA 를 학습시킨 촬영과 똑같이 받는다. 그 전 18칸(얼굴8·상반신5·
전신5)은 상반신·전신 10장의 소비처가 코드에 하나도 없었고, 그 사진들로는 LoRA 를 만들 수도 없었다
(v7 은 별도 촬영으로 학습했다 — 조명 4 × 컷 3 = 학습 12, 그늘 기준 4).

  학습 12 = 조명 4(그늘·해가왼쪽·해가오른쪽·역광) × 컷 3(정면 무표정·정면 미소·3/4 무표정)
  기준  4 = 그늘에서 정면 무표정 2·턱 살짝 내리기·시선만 왼쪽·시선만 오른쪽
  자산  1 = 그늘 측면(공개 자산 잡이 front·angle45·side 세 장을 요구한다)
                                                                      → 합 17

슬롯 키는 인테이크 id 접두어(SH/SL/SR/BL)와 맞춘다. 학습 캡션·내보내기 파일 이름은 한국어라
그 매핑을 **여기 한 곳**에 둔다 — 서버와 scripts/fm_export_training_set.py 가 같이 쓴다.

옛 이름(face01~full05, front/angle45/side)은 지우지 않는다. 운영에 legacy 3장 등록과 18칸으로
진행 중인 등록이 있어서, 그 행들이 계속 풀려야 한다(SLOT_CANDIDATES).
"""

import re

#: 조명 키 → (표시 이름, 학습 캡션·파일 이름)
LIGHTING_LABELS: dict[str, tuple[str, str]] = {
    "sh": ("그늘", "그늘"),
    "sl": ("해가 왼쪽", "해가왼쪽"),
    "sr": ("해가 오른쪽", "해가오른쪽"),
    "bl": ("역광", "해등지고"),
}
#: 컷 키 → (표시 이름, 학습 캡션·파일 이름)
CUT_LABELS: dict[str, tuple[str, str]] = {
    "front": ("정면 무표정", "정면_무표정"),
    "smile": ("정면 미소", "정면_미소"),
    "34": ("3/4 무표정", "3:4_무표정"),
    "front2": ("정면 무표정 2", "정면_무표정_2"),
    "chin_down": ("턱 살짝 내리기", "턱_살짝_내리기"),
    "gaze_left": ("시선만 왼쪽", "시선_왼쪽"),
    "gaze_right": ("시선만 오른쪽", "시선_오른쪽"),
    "side": ("측면", "측면"),
}

#: 학습에 들어가는 12장 — 조명 4 × 컷 3. 순서는 촬영 가이드 번호 순서다.
TRAINING_SLOTS: tuple[str, ...] = tuple(
    f"{light}_{cut}" for light in ("sh", "sl", "sr", "bl") for cut in ("front", "smile", "34")
)
#: 동일인 검사 기준 4장 — 전부 그늘·같은 자리에서. 조명이 섞이면 기준끼리 점수가 0.53 까지 떨어진다.
REFSET_SLOTS: tuple[str, ...] = ("sh_front2", "sh_chin_down", "sh_gaze_left", "sh_gaze_right")
#: 공개 자산 잡(fm_model_asset_job)이 요구하는 세 장의 소스. 측면은 학습에 안 쓰지만 자산에 필요하다.
ASSET_SOURCE_SLOTS: tuple[str, ...] = ("sh_front", "sh_34", "sh_side")

#: 전체 17칸. 순서 = 촬영 순서(그늘 8 → 해가왼쪽 3 → 해가오른쪽 3 → 역광 3).
PHOTO_SLOTS: tuple[str, ...] = (
    "sh_front", "sh_smile", "sh_34", "sh_front2", "sh_chin_down", "sh_gaze_left", "sh_gaze_right", "sh_side",
    "sl_front", "sl_smile", "sl_34",
    "sr_front", "sr_smile", "sr_34",
    "bl_front", "bl_smile", "bl_34",
)

#: 정면 계열 — 눈간격 검사를 적용하고 3/4 각도 검사는 하지 않는 슬롯.
FRONTAL_CUTS: frozenset[str] = frozenset({"front", "smile", "front2", "chin_down", "gaze_left", "gaze_right"})

#: 옛 이름 → 새 슬롯. 한 슬롯의 후보는 **선호 순서**다(새 이름 먼저, 그다음 18칸, 그다음 3장 시절).
#: 여기 없는 옛 슬롯(face02·face04·torso*·full* 등)은 새 스펙에 자리가 없다 — 완료 판정에서 무시되고
#: 파기(biometric purge)는 angle 과 무관하게 등록 전체를 쓸어 담는다.
SLOT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "sh_front": ("sh_front", "face01", "front"),
    "sh_34": ("sh_34", "face03", "angle45"),
    "sh_side": ("sh_side", "face05", "side"),
}
LEGACY_SLOT_ALIASES: dict[str, str] = {
    old: canonical for canonical, names in SLOT_CANDIDATES.items() for old in names[1:]
}


def lighting_of(slot: str) -> str | None:
    key = str(slot or "").split("_", 1)[0]
    return key if key in LIGHTING_LABELS else None


def cut_of(slot: str) -> str | None:
    parts = str(slot or "").split("_", 1)
    return parts[1] if len(parts) == 2 and parts[1] in CUT_LABELS else None


def export_name(slot: str) -> str | None:
    """학습 내보내기 파일 이름(<조명>__<컷>). 새 슬롯이 아니면 None."""
    light, cut = lighting_of(slot), cut_of(slot)
    if not light or not cut:
        return None
    return f"{LIGHTING_LABELS[light][1]}__{CUT_LABELS[cut][1]}"


def is_frontal_slot(slot: str) -> bool:
    return cut_of(slot) in FRONTAL_CUTS


def canonical_photo_slot(slot: str) -> str:
    return LEGACY_SLOT_ALIASES.get(slot, slot)


def photo_slot_candidates(slot: str) -> tuple[str, ...]:
    canonical = canonical_photo_slot(slot)
    return SLOT_CANDIDATES.get(canonical, (canonical,))


def resolve_photo_rows(rows: list[dict], slots) -> list[dict]:
    by_angle = {row.get("angle"): row for row in rows}
    selected = []
    for slot in dict.fromkeys(canonical_photo_slot(slot) for slot in slots):
        row = next((by_angle[name] for name in photo_slot_candidates(slot) if name in by_angle), None)
        if row is not None:
            selected.append(row)
    return selected


def preferred_photo_predicate(photo_alias: str, enrollment_expression: str, slot: str = "sh_front") -> str:
    """JOIN predicate selecting at most one physical photo, including legacy rows.

    후보가 여럿이면 **앞선 후보가 있으면 뒤 후보는 못 나온다** — 정식 행이 못 쓰는 상태여도
    옛 행으로 새면 안 된다(그 등록은 승인된 적 없는 사진을 노출하게 된다).
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", photo_alias):
        raise ValueError("invalid photo alias")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", enrollment_expression):
        raise ValueError("invalid enrollment expression")
    names = photo_slot_candidates(slot)
    if not all(re.fullmatch(r"[a-z0-9_]+", name) for name in names):
        raise ValueError("invalid photo slot")
    candidates = ", ".join(f"'{name}'" for name in names)
    clauses = []
    for index, name in enumerate(names):
        earlier = names[:index]
        if not earlier:
            clauses.append(f"{photo_alias}.angle = '{name}'")
            continue
        earlier_list = ", ".join(f"'{e}'" for e in earlier)
        clauses.append(
            f"({photo_alias}.angle = '{name}' and not exists ("
            "select 1 from fm_biometric_enrollment_photos earlier_photo "
            f"where earlier_photo.enrollment_id = {enrollment_expression} "
            f"and earlier_photo.angle in ({earlier_list})))"
        )
    return (
        f"{photo_alias}.enrollment_id = {enrollment_expression} "
        f"and {photo_alias}.angle in ({candidates}) "
        f"and ({' or '.join(clauses)})"
    )
