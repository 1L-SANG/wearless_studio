"""모델 리스트 열람 판정(facemarket_catalog_access)을 **실제 Postgres** 에서 검사한다.

흉내 DB 테스트(test_facemarket_catalog_access.py)는 SQL 문자열만 보므로 칸 이름 오타나 조인
실수를 못 잡는다. 여기서는 마이그레이션이 적용된 로컬 Supabase DB(DATABASE_URL)에 계정과
기록을 심고 판정 함수를 그대로 부른다. 모든 행은 한 트랜잭션 안에서 만들고 끝에 되돌려
DB 에 아무것도 남기지 않는다.

CI 에서는 test-db 잡(supabase db start 뒤)이 이 파일을 돌리고, 일반 test 잡은 제외한다
(.github/workflows/deploy-server.yml). 로컬에 DB 가 없으면 건너뛴다. CI 에서 DB 에 못 붙으면
조용히 사라지지 않게 실패한다.
"""

import asyncio
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from app.facemarket_catalog_access import catalog_access

DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)


def _db_reachable() -> bool:
    try:
        with psycopg.connect(DB_URL, connect_timeout=3):
            return True
    except psycopg.OperationalError:
        return False


if not _db_reachable():
    if os.environ.get("CI"):
        pytest.fail(f"테스트 DB({DB_URL})에 붙지 못했어요.", pytrace=False)
    pytest.skip("로컬 Supabase DB 가 없어 실제 DB 판정 검사를 건너뛰어요.", allow_module_level=True)


async def _new_user(conn) -> str:
    user_id = str(uuid.uuid4())
    await conn.execute("insert into auth.users (id) values (%s)", (user_id,))
    return user_id


async def _new_model(conn, user_id: str, license_status: str | None) -> None:
    model_id = str(uuid.uuid4())
    await conn.execute(
        "insert into fm_models (id, user_id, display_name, status) values (%s, %s, %s, 'pending')",
        (model_id, user_id, "판정 검사 모델"),
    )
    if license_status is not None:
        await conn.execute(
            "insert into fm_licenses (model_id, face_image_uri, status) values (%s, %s, %s)",
            (model_id, "https://example.invalid/face", license_status),
        )


async def _run_scenarios() -> dict:
    """계정마다 기록을 심고 판정 결과를 모은다. 끝나면 전부 되돌린다."""
    results = {}
    async with await psycopg.AsyncConnection.connect(DB_URL, row_factory=dict_row) as conn:
        try:
            stranger = await _new_user(conn)

            seller = await _new_user(conn)
            await conn.execute(
                "insert into seller_consents (user_id, terms_version, privacy_version) values (%s, 'v1.2', 'v1.1')",
                (seller,),
            )

            admin = await _new_user(conn)
            await conn.execute(
                """insert into profiles (user_id, role) values (%s, 'admin')
                   on conflict (user_id) do update set role = 'admin'""",
                (admin,),
            )

            accounts = {"stranger": stranger, "seller": seller, "admin": admin}
            # 2차 등록 완료 = 라이선스 발급(active). 재등록 중(reverification_required)도 이미 마친 모델.
            # 등록 중(라이선스 없음, pending), 철회(revoked), 만료(expired)는 아니다.
            for name, status in [
                ("model_active", "active"),
                ("model_reverifying", "reverification_required"),
                ("registering_no_license", None),
                ("registering_pending", "pending"),
                ("withdrawn_revoked", "revoked"),
                ("expired", "expired"),
            ]:
                user_id = await _new_user(conn)
                await _new_model(conn, user_id, status)
                accounts[name] = user_id

            for name, user_id in accounts.items():
                results[name] = await catalog_access(conn, user_id)
        finally:
            await conn.rollback()
    return results


@pytest.fixture(scope="module")
def results():
    return asyncio.run(_run_scenarios())


@pytest.mark.parametrize("name, role", [
    ("seller", "seller"),
    ("admin", "admin"),
    ("model_active", "model"),
    ("model_reverifying", "model"),
])
def test_members_are_allowed_on_the_real_schema(results, name, role):
    assert results[name] == {"allowed": True, "role": role, "seller": name == "seller"}


@pytest.mark.parametrize("name", [
    "stranger", "registering_no_license", "registering_pending", "withdrawn_revoked", "expired",
])
def test_others_are_turned_away_on_the_real_schema(results, name):
    assert results[name] == {"allowed": False, "role": None, "seller": False}
