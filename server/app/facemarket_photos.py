"""등록 사진 슬롯 — LoRA 학습 촬영 스펙(16칸) + 각도 수집 2칸 = **18칸**.

2026-09-14 결정: 등록 사진을 v7 LoRA 를 학습시킨 촬영과 똑같이 받는다. 그 전 18칸(얼굴8·상반신5·
전신5)은 상반신·전신 10장의 소비처가 코드에 하나도 없었고, 그 사진들로는 LoRA 를 만들 수도 없었다
(v7 은 별도 촬영으로 학습했다 — 조명 4 × 컷 3 = 학습 12, 그늘 기준 3).

  학습 12 = 조명 4(그늘·해가왼쪽·해가오른쪽·역광) × 컷 3(정면 무표정·정면 미소·3/4 무표정)
  기준  3 = 그늘에서 정면 무표정 2·시선만 왼쪽·시선만 오른쪽
  자산  1 = 그늘 측면 · 코가 화면 왼쪽(공개 자산 잡이 front·angle45·side 세 장을 요구한다)
  각도  2 = 그늘 측면 · 코가 화면 오른쪽 · 뒷모습                       → 합 18

2026-09-15 추가(각도 2칸): LoRA 는 정면·정면미소·3/4 만 배웠다. 그래서 옆모습 컷은 yaw>0.65 로
건너뛰어 실존 모델 컷이 실패하고, 뒷모습은 no_face 라 **원래 모델의 뒷머리가 그대로 나간다**.
그 두 각도를 지금부터 받아 둔다 — 당분간 gpt-image 참조로 쓰고, 검증 뒤 학습에 넣는다.
★ TRAINING_SLOTS(학습 12장)는 **안 바꿨다**. 학습 편입은 v8 실험 결과를 보고 별도로 한다.

기준에서 **턱 살짝 내리기는 뺐다**(2026-09-11 v7 인테이크 실측). 같은 사람인데 `시선만_왼쪽` 과
SFace 0.664 로 기준선(최저 0.70)을 깬다 — 턱 각도가 바뀐 컷은 기준으로 쓰기에 너무 멀다.
그 한 장을 빼면 평균 0.841·최저 0.788 로 통과하고, v7 채점도 이 3장으로 했다.

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
    "gaze_left": ("시선만 왼쪽", "시선_왼쪽"),
    "gaze_right": ("시선만 오른쪽", "시선_오른쪽"),
    "side": ("옆모습 · 왼쪽", "측면_왼쪽"),
    "side_right": ("옆모습 · 오른쪽", "측면_오른쪽"),
    "back": ("뒷모습", "뒷모습"),
}

#: 학습에 들어가는 12장 — 조명 4 × 컷 3. 순서는 촬영 가이드 번호 순서다.
TRAINING_SLOTS: tuple[str, ...] = tuple(
    f"{light}_{cut}" for light in ("sh", "sl", "sr", "bl") for cut in ("front", "smile", "34")
)
#: 동일인 검사 기준 3장 — 전부 그늘·같은 자리, 같은 턱 각도. 조명이 섞이면 기준끼리 점수가
#: 0.53 까지 떨어지고, 턱을 내린 컷을 섞으면 최저쌍이 0.664 로 기준선을 깬다(위 docstring).
REFSET_SLOTS: tuple[str, ...] = ("sh_front2", "sh_gaze_left", "sh_gaze_right")
#: 공개 자산 잡(fm_model_asset_job)이 요구하는 세 장의 소스. 측면은 학습에 안 쓰지만 자산에 필요하다.
#: sh_side 는 키를 그대로 두고 의미만 "코가 화면 왼쪽"으로 못박았다 — 옛 등록 행(face05·side)이
#: 계속 이 칸으로 풀려야 한다(SLOT_CANDIDATES).
ASSET_SOURCE_SLOTS: tuple[str, ...] = ("sh_front", "sh_34", "sh_side")

#: 각도 수집 2칸(2026-09-15). 학습에도 기준에도 안 들어간다 — 모아 두고 쓰임새는 뒤에 정한다.
ANGLE_SLOTS: tuple[str, ...] = ("sh_side_right", "sh_back")

#: 왼쪽 90도 옆모습을 담는 **보조 칸**(2026-09-21). 정식 등록(동의 2026-09-v3)은 그 사진을
#: sh_side 로 받고 끝낸다 — 그 등록에서 이 칸은 비어 있다.
#:
#: 왜 따로 두는가: sh_side 는 자산 소스 3칸(ASSET_SOURCE_SLOTS)이기도 하다. v3 이전에 통과한
#: 등록은 자산이 옛 이름(face05)으로 이미 만들어져 assets_source_hash 가 그 다이제스트에 묶여
#: 있는데, 뒤늦게 sh_side 행을 넣으면 SLOT_CANDIDATES 가 face05 대신 그 행을 집어 해시가
#: 어긋난다. 그러면 그 모델의 실사 컷이 통째로 model_assets_unavailable 로 막힌다 —
#: 2026-09-21 운영 05caa497 에서 실제로 났고 상세페이지 2건이 죽었다.
#:
#: 그래서 옛 등록의 왼쪽 옆모습은 이 칸에 넣는다. 각도 교체만 읽고 자산 소스 계산에는 안 들어간다.
#: 필수 칸(PHOTO_SLOTS)이 아니라 완료 판정도 건드리지 않는다.
#:
#: 업로드 API 로는 안 받는다(ACCEPTED_PHOTO_SLOTS 에 없다) — 촬영 화면에 없는 칸이라
#: 마법사 9칸과 CUT_LABELS 가 같아야 한다는 계약(tests/frontend/facemarket-register-v3)도
#: 깨진다. 옛 등록 한 건을 메우는 탈출구이고, 넣는 길은 운영 스크립트뿐이다.
ANGLE_ALT_SLOTS: tuple[str, ...] = ("sh_side_left",)

#: 2026-09-14~15 사이에 시작한 등록이 채운 16칸. **이미 통과한 등록을 미완료로 되돌리지 않으려고**
#: 그대로 남긴다(완료 판정은 동의 버전으로 갈린다 — facemarket_enrollment.required_slots_for_consent).
PHOTO_SLOTS_V2: tuple[str, ...] = (
    "sh_front", "sh_smile", "sh_34", "sh_front2", "sh_gaze_left", "sh_gaze_right", "sh_side",
    "sl_front", "sl_smile", "sl_34",
    "sr_front", "sr_smile", "sr_34",
    "bl_front", "bl_smile", "bl_34",
)

#: 전체 18칸. 순서 = 촬영 순서(그늘 9 → 해가왼쪽 3 → 해가오른쪽 3 → 역광 3).
PHOTO_SLOTS: tuple[str, ...] = (
    "sh_front", "sh_smile", "sh_34", "sh_front2", "sh_gaze_left", "sh_gaze_right",
    "sh_side", "sh_side_right", "sh_back",
    "sl_front", "sl_smile", "sl_34",
    "sr_front", "sr_smile", "sr_34",
    "bl_front", "bl_smile", "bl_34",
)

#: 정면 계열 — 눈간격 검사를 적용하고 3/4 각도 검사는 하지 않는 슬롯.
FRONTAL_CUTS: frozenset[str] = frozenset({"front", "smile", "front2", "gaze_left", "gaze_right"})
#: 옆모습 두 칸. 얼굴 미검출을 허용하고(YuNet 이 옆얼굴을 자주 놓친다) 각도·방향만 본다.
PROFILE_CUTS: frozenset[str] = frozenset({"side", "side_right"})
#: 코가 향해야 하는 쪽(화면 기준). None 이면 방향을 안 본다.
PROFILE_NOSE_SIDE: dict[str, str] = {"side": "left", "side_right": "right"}

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
