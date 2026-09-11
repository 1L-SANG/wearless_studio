import re
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260911140000_facemarket_id_capture_review.sql"
)


def _sql():
    text = MIGRATION.read_text()
    # `--` 주석을 먼저 걷어낸다. 안 걷으면 이 파일의 단언이 전부 주석까지 훑는
    # 통짜 substring 검색이 되어, CHECK 절에서 값이 빠져도 주석에 이름만 있으면
    # 통과한다 — 안전망 흉내만 내는 테스트가 된다.
    text = re.sub(r"--[^\n]*", " ", text)
    return " ".join(text.split()).lower()


def test_status_check_includes_new_states():
    sql = _sql()
    clause = sql[sql.index("fm_biometric_enrollments_status_check"):sql.index("fm_biometric_active_per_user")]
    assert "id_capture_pending" in clause
    assert "review_pending" in clause
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


# ── fix round 2: 코드가 하드코딩한 활성-상태 목록이 이 인덱스와 갈라지는 것을 잠근다 ──────
# Task1 마이그레이션이 fm_biometric_active_per_user 부분 유니크 인덱스를 9개 상태로
# 넓혔는데, facemarket_enrollment.py 의 여러 "status in (...)" 하드코딩 목록이 갱신되지
# 않아 id_capture_pending/review_pending 인 등록이 재조회·취소·중복생성차단에서 조용히
# 빠졌었다(fix round 2 에서 실제로 발견·수정). 다음에 마이그레이션이 상태를 또 늘리면
# 이 테스트가 코드 쪽을 잊었을 때 실패한다.

APP_FILE = Path(__file__).resolve().parents[1] / "app" / "facemarket_enrollment.py"
_STATUS_LITERAL = re.compile(r"'([a-z_]+)'")


def _migration_active_states() -> frozenset:
    sql = _sql()
    # "fm_biometric_active_per_user" 는 두 번 나온다 — 첫 번째는 `drop index if exists`
    # (바로 세미콜론, 절이 비어 있다), 두 번째가 실제 `create unique index ... where
    # status in (...)` 정의다. 두 번째를 잡는다.
    marker = "create unique index if not exists fm_biometric_active_per_user"
    idx = sql.index(marker)
    clause = sql[idx: sql.index(";", idx)]
    return frozenset(_STATUS_LITERAL.findall(clause))


def _extract_status_in(source: str, marker: str) -> frozenset:
    """`marker` 뒤에 나오는 첫 `status in (...)` 절의 상태 집합."""
    start = source.index(marker)
    in_idx = source.index("status in (", start)
    close_idx = source.index(")", in_idx)
    return frozenset(_STATUS_LITERAL.findall(source[in_idx:close_idx]))


def test_active_status_lists_match_migration_index():
    active = _migration_active_states()
    # 인덱스 자체가 두 신규 상태를 담고 있는지는 test_active_index_includes_new_states 가
    # 이미 보지만, 여기선 그 정확한 9개 집합을 코드 쪽 목록들의 기준값으로 고정해 둔다.
    assert active == {
        "id_capture_pending", "identity_pending", "photos_pending", "review_pending",
        "liveness_pending", "processing", "asset_building", "license_pending", "vc_pending",
    }

    source = APP_FILE.read_text()

    # GET /enrollments/current 가 소유자의 활성 등록을 찾는 WHERE.
    current = _extract_status_in(source, "async def _load_current_enrollment")
    assert current == active

    # create_enrollment 의 on conflict(user_id) 추론 predicate — mid/simple_auth 두
    # 분기 모두 실제 부분 유니크 인덱스와 정확히 같아야 PG 가 그 인덱스를 arbiter 로 잡는다.
    create_start = source.index("async def create_enrollment")
    create_end = source.index("async def verify_enrollment_identity")
    create_body = source[create_start:create_end]
    conflict_clauses = re.findall(
        r"on conflict \(user_id\) where status in \(([^)]*)\)", create_body
    )
    assert len(conflict_clauses) == 2, "mid/simple_auth 분기 각각 하나씩 있어야 한다"
    for clause in conflict_clauses:
        assert frozenset(_STATUS_LITERAL.findall(clause)) == active

    # INSERT 가 충돌해서 기존 활성 enrollment 를 재조회하는 폴백 SELECT.
    fallback = _extract_status_in(
        create_body, "select id::text as id from fm_biometric_enrollments"
    )
    assert fallback == active

    # cancel_enrollment: 취소는 활성 상태 전부 + 이미 취소된 상태(멱등 재호출)까지 허용한다.
    cancel = _extract_status_in(source, "async def cancel_enrollment")
    assert cancel == active | {"cancelled"}
