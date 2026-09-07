"""원장 스키마 계약 테스트 — 원장이 부모보다 오래 사는지가 핵심이다.

fm_models → fm_licenses 는 on delete cascade 다. 2026-08-29 prod 복구 때 모델·라이선스가
실제로 지워졌다. 원장의 FK 가 restrict 였다면 그 복구가 막혔고, cascade 였다면 원장이 통째로
사라졌다. 여기서 검증하는 건 "부모가 지워져도 증빙값이 남는다" 하나다.
"""
import asyncio
import os
import re
import uuid
from pathlib import Path

import pytest
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20260904000000_facemarket_provenance.sql"
TEST_DATABASE_URL = os.getenv("FACEMARKET_TEST_DATABASE_URL")
requires_database = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="FACEMARKET_TEST_DATABASE_URL is not configured"
)

# fm_output_records: asset_id·job_id·license_id / fm_publication_records: project_id·license_id
LEDGER_SET_NULL_FKS = {
    "fm_output_records": ("asset_id", "job_id", "license_id"),
    "fm_publication_records": ("project_id", "license_id"),
}
# 증빙값 — FK(references) 가 붙으면 안 되는 비정규화 컬럼
DENORMALIZED_EVIDENCE_COLUMNS = ("license_ref", "model_id", "seller_id", "image_sha256")


def _executable_sql(path: Path) -> str:
    """주석·공백 제거 — 주석 안 문자열이 assert 를 오염시키지 않게(선례:
    test_facemarket_mandatory_vc_migration.py 의 _executable_sql)."""
    sql = path.read_text(encoding="utf-8")
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return " ".join(sql.split()).lower()


def _table_body(sql: str, table: str) -> str:
    """comment-stripped SQL 에서 create table 본문만 잘라낸다(컬럼 단위 검사용)."""
    marker = f"create table if not exists public.{table} ("
    assert marker in sql, f"{table} 테이블 정의를 찾을 수 없다"
    return sql.split(marker, 1)[1].split(");", 1)[0]


def _column_clause(schema: str, table: str, column: str) -> str:
    """컬럼 선언부(다음 콤마까지)만 떼어낸다 — references 여부를 그 컬럼 선언에만 물으려고."""
    match = re.search(rf"\b{re.escape(column)}\b[^,]*,?", schema)
    assert match, f"{table}.{column} 컬럼 선언을 찾을 수 없다"
    return match.group(0)


def _assert_ledger_fk_contract(sql: str) -> None:
    """원장 FK 계약 — 순수 함수로 분리해서 깨진 SQL 문자열에도 바로 걸 수 있게 했다.

    on delete set null 절이 통째로 빠지면 Postgres 기본값은 NO ACTION 이고, 이는 RESTRICT 와
    똑같이 부모 삭제를 막는다 — 2026-08-29 prod 복구를 막았을 바로 그 동작이다. 문자열
    "on delete restrict" 부재 하나만으로는 이 회귀를 못 잡으므로, set null/cascade 절 개수를
    정확히 세고 증빙 컬럼에 references 절이 없는지까지 확인한다.
    """
    assert "on delete restrict" not in sql
    for table in (
        "fm_output_records",
        "fm_publication_records",
        "fm_publication_anchor_jobs",
    ):
        assert table in sql

    expected_set_null = sum(len(cols) for cols in LEDGER_SET_NULL_FKS.values())
    total_set_null = sql.count("on delete set null")
    assert total_set_null == expected_set_null, (
        f"on delete set null 개수가 {expected_set_null}개(fm_output_records: asset_id·job_id·"
        f"license_id, fm_publication_records: project_id·license_id)여야 하는데 "
        f"{total_set_null}개다 — FK 절이 빠지면 기본값 NO ACTION 이 RESTRICT 와 동일하게 부모 "
        "삭제를 막는다"
    )

    total_cascade = sql.count("on delete cascade")
    assert total_cascade == 1, (
        "on delete cascade 는 fm_publication_anchor_jobs.publication_id 하나뿐이어야 하는데 "
        f"{total_cascade}개다"
    )

    bodies = {
        table: _table_body(sql, table)
        for table in (
            "fm_output_records",
            "fm_publication_records",
            "fm_publication_anchor_jobs",
        )
    }

    for table, columns in LEDGER_SET_NULL_FKS.items():
        found = bodies[table].count("on delete set null")
        assert found == len(columns), (
            f"{table} 는 {columns} {len(columns)}개 컬럼이 on delete set null 이어야 하는데 "
            f"본문에서 {found}개만 발견됐다"
        )

    assert bodies["fm_publication_anchor_jobs"].count("on delete cascade") == 1, (
        "fm_publication_anchor_jobs.publication_id 의 on delete cascade 가 없다"
    )
    assert bodies["fm_publication_anchor_jobs"].count("on delete set null") == 0, (
        "fm_publication_anchor_jobs 에 예상 밖의 set null FK 가 있다 — 이 테이블은 cascade 하나뿐이어야 한다"
    )

    # 증빙값(license_ref·model_id·seller_id·image_sha256)은 FK 없는 비정규화 컬럼이어야 한다.
    # fm_models→fm_licenses 가 cascade 라 이 컬럼에 references 가 붙으면 원장이 부모와 함께 죽는다.
    for table in ("fm_output_records", "fm_publication_records"):
        body = bodies[table]
        for column in DENORMALIZED_EVIDENCE_COLUMNS:
            clause = _column_clause(body, table, column)
            assert "references" not in clause, (
                f"{table}.{column} 은 비정규화 증빙 컬럼이어야 하는데 FK(references) 가 붙었다: "
                f"{clause!r}"
            )


def test_migration_file_exists():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION}"


def test_migration_declares_set_null_fks():
    """계약을 SQL 텍스트 수준에서도 못박는다 — DB 없는 CI 에서도 회귀를 잡는다.

    FACEMARKET_TEST_DATABASE_URL 은 어떤 GitHub Actions 워크플로도 설정하지 않으므로 DB 테스트
    2개는 CI 에서 절대 실행되지 않는다. 이 텍스트 검사가 유일한 자동 방어선이다.
    """
    _assert_ledger_fk_contract(_executable_sql(MIGRATION))


@requires_database
def test_ledger_survives_license_delete():
    async def run():
        conn = await AsyncConnection.connect(
            TEST_DATABASE_URL, autocommit=True, row_factory=dict_row
        )
        try:
            await conn.execute(MIGRATION.read_text(encoding="utf-8"))
            model_id = uuid.uuid4()
            license_id = uuid.uuid4()
            seller_id = uuid.uuid4()
            await conn.execute(
                "insert into fm_models (id, display_name) values (%s, %s)",
                (model_id, "홍*동"),
            )
            await conn.execute(
                """insert into fm_licenses
                   (id, model_id, face_image_uri, face_image_digest, license_valid_until)
                   values (%s, %s, %s, %s, now() + interval '1 year')""",
                (license_id, model_id, "/gate", "sha256-x"),
            )
            record_id = uuid.uuid4()
            await conn.execute(
                """insert into fm_output_records
                   (id, license_id, license_ref, model_id, seller_id, image_sha256)
                   values (%s, %s, %s, %s, %s, %s)""",
                (record_id, license_id, license_id, model_id, seller_id, "a" * 64),
            )
            # 모델 삭제 → 라이선스 cascade 삭제. 원장은 남아야 한다.
            await conn.execute("delete from fm_models where id = %s", (model_id,))
            cur = await conn.execute(
                "select license_id, license_ref, model_id, image_sha256 "
                "from fm_output_records where id = %s",
                (record_id,),
            )
            row = await cur.fetchone()
            assert row is not None, "원장이 부모와 함께 지워졌다"
            assert row["license_id"] is None       # FK 는 끊긴다
            assert str(row["license_ref"]) == str(license_id)  # 증빙값은 남는다
            assert str(row["model_id"]) == str(model_id)
            assert row["image_sha256"] == "a" * 64
        finally:
            await conn.close()

    asyncio.run(run())


@requires_database
def test_publication_idempotent_per_seller_and_hash():
    async def run():
        conn = await AsyncConnection.connect(
            TEST_DATABASE_URL, autocommit=True, row_factory=dict_row
        )
        try:
            await conn.execute(MIGRATION.read_text(encoding="utf-8"))
            seller_id = uuid.uuid4()
            args = (seller_id, uuid.uuid4(), uuid.uuid4(), "long_png", "b" * 64)
            sql = """insert into fm_publication_records
                     (seller_id, license_ref, model_id, kind, image_sha256)
                     values (%s, %s, %s, %s, %s)"""
            await conn.execute(sql, args)
            with pytest.raises(UniqueViolation):
                await conn.execute(sql, args)
        finally:
            await conn.close()

    asyncio.run(run())
