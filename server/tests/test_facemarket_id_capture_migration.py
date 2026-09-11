from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260911120000_facemarket_id_capture_review.sql"
)


def _sql():
    return " ".join(MIGRATION.read_text().split()).lower()


def test_status_check_includes_new_states():
    sql = _sql()
    assert "id_capture_pending" in sql
    assert "review_pending" in sql
    assert "drop constraint if exists" in sql


def test_active_index_includes_new_states():
    sql = _sql()
    # partial unique index 를 재생성하며 두 신규 상태를 활성 집합에 넣어야
    # 심사 대기 중인 사용자가 등록을 하나 더 만들지 못한다.
    idx = sql[sql.index("fm_biometric_active_per_user"):]
    assert "id_capture_pending" in idx
    assert "review_pending" in idx


def test_adds_method_document_and_review_columns():
    sql = _sql()
    for col in (
        "identity_method", "id_document_r2_key", "id_document_type",
        "id_document_uploaded_at", "id_document_purged_at",
        "review_status", "reviewed_by", "reviewed_at", "review_reason",
        "match_scores",
    ):
        assert col in sql, col


def test_identity_method_defaults_to_mid():
    sql = _sql()
    assert "identity_method text default 'mid'" in sql


def test_default_status_unchanged():
    # 기본 시작 상태는 여전히 identity_pending 이다(mid 경로 불변).
    # simple_auth 는 라우트가 명시적으로 id_capture_pending 을 넣는다.
    sql = _sql()
    assert "alter column status set default" not in sql


def test_no_raw_pii_columns():
    sql = _sql()
    for forbidden in ("portrait", "embedding", "raw_ci", "rrn", "ssn"):
        assert forbidden not in sql, forbidden
