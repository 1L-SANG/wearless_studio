"""옆모습 좌·우 + 뒷모습 칸 (2026-09-15) — 무엇이 늘었고, 무엇이 **안 늘었는가**.

늘어난 것: 등록이 받는 칸(16 → 18), 관리자가 보는 칸, 내보내기의 angles/ 묶음.
안 늘어난 것: **학습 12장**(TRAINING_SLOTS). 옆모습·뒷모습은 지금은 모아 두기만 한다 —
여기에 섞이면 v8 실험 전에 학습 구성이 조용히 바뀐다.

그리고 ★ 이미 통과한 등록을 미완료로 되돌리지 않는다. 칸이 늘었다고 옛 동의로 시작한
등록에 18칸을 요구하면 그 모델이 카탈로그에서 사라진다(운영 05caa497).
"""

import pathlib
import re

from app import facemarket_photos as fp
from app.facemarket_enrollment import (
    ACCEPTED_BIOMETRIC_CONSENT_VERSIONS,
    ACCEPTED_CONSENT_VERSIONS,
    BIOMETRIC_CONSENT_VERSION,
    required_slots_for_consent,
)

MIGRATION = (pathlib.Path(__file__).resolve().parents[2]
             / "supabase/migrations/20260915120000_fm_enrollment_photo_review.sql")


# ── 칸 정본 ────────────────────────────────────────────────────────────────
def test_the_canon_has_eighteen_slots_and_the_side_key_is_unchanged():
    assert len(fp.PHOTO_SLOTS) == 18
    assert len(set(fp.PHOTO_SLOTS)) == 18
    # sh_side 에는 이미 올라온 사진이 있다 — 키를 바꾸면 그 사진들이 길을 잃는다.
    assert "sh_side" in fp.PHOTO_SLOTS
    assert fp.ANGLE_SLOTS == ("sh_side_right", "sh_back")
    assert set(fp.PHOTO_SLOTS) - set(fp.PHOTO_SLOTS_V2) == set(fp.ANGLE_SLOTS)


def test_the_angle_slots_are_shot_in_the_shade_group():
    """조명 네 곳을 옮겨 다니는 촬영이다 — 각도 3장은 첫 자리(그늘)에서 몸만 돌려 찍는다."""
    assert fp.lighting_of("sh_side_right") == fp.lighting_of("sh_side") == "sh"
    assert fp.lighting_of("sh_back") == "sh"
    assert sum(1 for slot in fp.PHOTO_SLOTS if fp.lighting_of(slot) == "sh") == 9


def test_training_and_reference_sets_did_not_change():
    """★ 이 PR 은 수집만 넓힌다. 학습 구성은 v8 실험 결과를 보고 따로 정한다."""
    assert len(fp.TRAINING_SLOTS) == 12
    assert not set(fp.TRAINING_SLOTS) & {"sh_side", *fp.ANGLE_SLOTS}
    assert not set(fp.REFSET_SLOTS) & {"sh_side", *fp.ANGLE_SLOTS}


def test_export_names_stay_unique_and_readable():
    names = {slot: fp.export_name(slot) for slot in fp.PHOTO_SLOTS}
    assert names["sh_side"] == "그늘__측면_왼쪽"
    assert names["sh_side_right"] == "그늘__측면_오른쪽"
    assert names["sh_back"] == "그늘__뒷모습"
    assert len(set(names.values())) == len(names), "이름이 겹치면 파일이 덮인다"


# ── 동의 버전별 완료 조건 ──────────────────────────────────────────────────
def test_an_older_consent_still_completes_at_sixteen():
    """★ 운영 05caa497 이 걸린 자리다 — 칸이 늘었다고 통과한 등록을 미완료로 만들지 않는다."""
    for version in ("2026-09-v2", "2026-09-v1", "2026-08-v2", None, "", "모르는-값"):
        assert required_slots_for_consent(version) == fp.PHOTO_SLOTS_V2
        assert len(required_slots_for_consent(version)) == 16


def test_the_new_consent_requires_all_eighteen():
    assert required_slots_for_consent(BIOMETRIC_CONSENT_VERSION) == fp.PHOTO_SLOTS
    assert len(required_slots_for_consent("2026-09-v3")) == 18
    assert len(required_slots_for_consent("2026-09-v4")) == 18  # 동의문만 바뀌었다. 칸 수는 그대로 18.


def test_old_consent_versions_are_never_dropped_from_the_accept_lists():
    """목록에서 옛 값을 빼면 라이브 카탈로그가 비고 기존 모델이 파기 대상으로 분류된다."""
    for version in ("2026-09-v1", "2026-09-v2"):
        assert version in ACCEPTED_BIOMETRIC_CONSENT_VERSIONS
        assert version in ACCEPTED_CONSENT_VERSIONS
    assert BIOMETRIC_CONSENT_VERSION in ACCEPTED_BIOMETRIC_CONSENT_VERSIONS
    assert BIOMETRIC_CONSENT_VERSION in ACCEPTED_CONSENT_VERSIONS


# ── 마이그레이션 ───────────────────────────────────────────────────────────
def test_the_migration_backfills_passed_enrollments_as_approved():
    """★ 백필이 없으면 운영 중인 모델의 재학습·내보내기가 그날부터 막힌다."""
    sql = MIGRATION.read_text()
    assert "add column if not exists photo_review_status" in sql
    assert "add column if not exists reshoot_slots jsonb" in sql
    backfill = re.search(
        r"update public\.fm_biometric_enrollments\s+set photo_review_status = 'approved'.*?;",
        sql, re.S,
    )
    assert backfill, "통과한 등록을 approved 로 채우는 구문이 없다"
    assert "decision = 'passed'" in backfill.group(0)
    assert "set default 'pending'" in sql
    # 체크 제약이 세 값만 허용한다 — 오타 상태가 들어오면 범위 술어가 조용히 열린다.
    assert "check (photo_review_status in ('pending', 'approved', 'reshoot_requested'))" in sql


def test_the_migration_is_idempotent_in_shape():
    """운영에 한 번 더 돌려도 죽지 않아야 한다(재배포·수동 적용)."""
    sql = MIGRATION.read_text()
    assert sql.count("add column if not exists") == 4
    assert "where photo_review_status is null" in sql


# ── 보조 칸 sh_side_left (2026-09-21) ──────────────────────────────────────
#
# 운영 사고의 재발 방지선이다. 2026-09-21 에 왼쪽 90도 옆모습을 sh_side 로 넣었더니
# 자산 소스 해소가 face05 에서 그 행으로 옮겨 가 assets_source_hash 가 바뀌었고,
# 실사 모델 컷이 전부 model_assets_unavailable 로 막혔다(상세페이지 2건 사망).
ALT_MIGRATION = (pathlib.Path(__file__).resolve().parents[2]
                 / "supabase/migrations/20260921170000_facemarket_enrollment_angle_alt_slot.sql")

#: 그날 운영 등록(05caa497)의 모양 — 자산 소스가 옛 이름으로만 있다.
LEGACY_ROWS = [
    {"angle": "face01", "r2_key": "k/face01", "image_digest": "d-face01"},
    {"angle": "face03", "r2_key": "k/face03", "image_digest": "d-face03"},
    {"angle": "face05", "r2_key": "k/face05", "image_digest": "d-face05"},
    {"angle": "sh_side_right", "r2_key": "k/side_right", "image_digest": "d-side_right"},
    {"angle": "sh_back", "r2_key": "k/back", "image_digest": "d-back"},
]


def test_the_alt_slot_is_not_required_and_does_not_move_the_completion_bar():
    """보조 칸은 촬영 목록이 아니다 — 넣는다고 통과한 등록이 미완료가 되면 안 된다."""
    assert fp.ANGLE_ALT_SLOTS == ("sh_side_left",)
    assert "sh_side_left" not in fp.PHOTO_SLOTS
    assert "sh_side_left" not in fp.PHOTO_SLOTS_V2
    assert len(fp.PHOTO_SLOTS) == 18
    for version in (BIOMETRIC_CONSENT_VERSION, "2026-09-v2", None):
        assert "sh_side_left" not in required_slots_for_consent(version)


def test_the_alt_slot_never_touches_the_asset_source_hash():
    """★ 사고 지점. 왼쪽 옆모습이 들어와도 자산 소스 3장은 그대로여야 한다.

    sh_side 로 넣으면 face05 가 밀려나 해시가 바뀐다 — 그래서 보조 칸이 있다.
    """
    from app.agents.identity_source import compute_assets_source_hash

    before = fp.resolve_photo_rows(LEGACY_ROWS, fp.ASSET_SOURCE_SLOTS)
    with_alt = fp.resolve_photo_rows(
        [*LEGACY_ROWS, {"angle": "sh_side_left", "r2_key": "k/profL90",
                        "image_digest": "d-profL90"}],
        fp.ASSET_SOURCE_SLOTS)
    assert [r["angle"] for r in with_alt] == [r["angle"] for r in before] == [
        "face01", "face03", "face05"]
    assert compute_assets_source_hash(with_alt) == compute_assets_source_hash(before)

    # 대조군: sh_side 로 넣으면 실제로 바뀐다(그게 2026-09-21 사고다).
    with_sh_side = fp.resolve_photo_rows(
        [*LEGACY_ROWS, {"angle": "sh_side", "r2_key": "k/profL90",
                        "image_digest": "d-profL90"}],
        fp.ASSET_SOURCE_SLOTS)
    assert compute_assets_source_hash(with_sh_side) != compute_assets_source_hash(before)


def test_the_left_direction_reads_the_alt_slot():
    from app.agents.identity_source import angle_photos_from_rows

    got = angle_photos_from_rows(
        [*LEGACY_ROWS, {"angle": "sh_side_left", "r2_key": "k/profL90"}])
    assert got == {"sh_side": "k/profL90", "sh_side_right": "k/side_right",
                   "sh_back": "k/back"}


def test_the_proper_slot_still_wins_for_new_enrollments():
    """v3 등록은 sh_side 로 들어온다 — 보조 칸이 없어도 왼쪽이 나와야 한다."""
    from app.agents.identity_source import angle_photos_from_rows

    got = angle_photos_from_rows([
        {"angle": "sh_side", "r2_key": "k/v3_left"},
        {"angle": "sh_side_right", "r2_key": "k/v3_right"},
        {"angle": "sh_back", "r2_key": "k/v3_back"},
    ])
    assert got["sh_side"] == "k/v3_left"


def test_the_alt_slot_still_refuses_legacy_aliases():
    """face05 는 얼굴 중심 측면 컷이다 — 남의 머리를 90도 옆 컷에 붙이면 안 된다."""
    from app.agents.identity_source import angle_photos_from_rows

    assert "sh_side" not in angle_photos_from_rows(LEGACY_ROWS)


def test_the_alt_migration_opens_both_angle_checks():
    sql = ALT_MIGRATION.read_text()
    assert sql.count("'sh_side','sh_side_left','sh_side_right','sh_back'") == 2
    assert sql.count("drop constraint if exists") == 2
    for legacy in ("face05", "front", "angle45", "side"):
        assert f"'{legacy}'" in sql, "옛 이름을 빼면 기존 행이 제약을 깬다"
