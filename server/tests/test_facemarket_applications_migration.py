"""fm_model_applications 마이그레이션(T1) 구조 검증 — SQL 텍스트 레벨.

실 DB 통합은 FACEMARKET_TEST_DATABASE_URL 환경에서만. 여기서는 스키마 계약(테이블·컬럼·
인덱스·FK)이 설계 결정(E2/E3/E5/E9/E11)과 일치하는지 텍스트로 확인한다.
"""

from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260902120000_facemarket_model_applications.sql"
)


def _sql() -> str:
    return " ".join(MIGRATION.read_text().split()).lower()


def test_creates_applications_table():
    sql = _sql()
    assert "create table if not exists public.fm_model_applications" in sql


def test_status_check_values():
    sql = _sql()
    for status in ("under_review", "approved", "rejected", "cancelled"):
        assert status in sql, status


def test_applicant_fields_present():
    sql = _sql()
    for col in (
        "contact_email", "applicant_name", "birthdate", "region", "gender",
        "height_cm", "agency_contracted", "categories", "portfolio_url",
        "sns_url", "bio", "profile_image_r2_key",
    ):
        assert col in sql, col


def test_privacy_consent_columns_E3():
    sql = _sql()
    assert "privacy_consent_version" in sql
    assert "privacy_consented_at" in sql


def test_identity_mismatch_counter_E2():
    sql = _sql()
    assert "identity_mismatch_count" in sql


def test_active_unique_covers_under_review_and_approved_E9():
    sql = _sql()
    assert "fm_model_applications_active_per_user" in sql
    # 활성 partial index 는 반드시 두 상태만 포함(터미널 제외 → 재지원 허용).
    idx = sql.split("fm_model_applications_active_per_user", 1)[1]
    where = idx.split("where", 1)[1].split(";", 1)[0]
    assert "under_review" in where and "approved" in where
    assert "rejected" not in where and "cancelled" not in where


def test_enrollment_application_id_fk_E5():
    sql = _sql()
    assert "add column if not exists application_id" in sql
    assert "references public.fm_model_applications" in sql


def test_photo_staging_table_E11():
    sql = _sql()
    assert "fm_model_application_photo_staging" in sql


def test_terminated_at_for_pii_sweep_3A():
    sql = _sql()
    assert "terminated_at" in sql


def test_updated_at_trigger():
    sql = _sql()
    assert "fm_model_applications_set_updated_at" in sql
    assert "execute function public.set_updated_at()" in sql
