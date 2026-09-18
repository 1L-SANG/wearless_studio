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
