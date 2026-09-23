"""마이그레이션에서 만든 public 테이블은 전부 RLS 가 켜져 있어야 한다.

2026-09-23 실측: 프런트 번들의 publishable 키만으로 `public.assets`(122행)와
`public.admin_audit_log`(36행)가 읽히고 PATCH 까지 통과했다. 원인의 절반은 그 테이블을
만든 마이그레이션에 `enable row level security` 를 **안 적은 것**이었다 — 9개가 그랬다.

"Data API 안 쓰니까 괜찮다"가 그 자리의 논리였는데, Data API 는 대시보드 스위치라 언제든
켜져 있을 수 있고 실제로 켜져 있었다. RLS 는 그 스위치와 무관하게 서는 2차 방어선이라
테이블을 만들면 같이 켜는 게 이 레포의 규칙이다. 그 규칙을 사람 기억이 아니라 여기서 잠근다.

서버는 DATABASE_URL 의 소유자 롤로 붙어 RLS 를 우회하므로, 켠다고 앱이 깨지지 않는다.
"""

import pathlib
import re

MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"

CREATE_TABLE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?\"?([a-z_][a-z0-9_]*)\"?",
    re.IGNORECASE,
)
ENABLE_RLS = re.compile(
    r"alter\s+table\s+(?:only\s+)?(?:public\.)?\"?([a-z_][a-z0-9_]*)\"?\s+enable\s+row\s+level\s+security",
    re.IGNORECASE,
)

# 임시 테이블·다른 스키마처럼 정말 예외가 생기면 여기에 **이유와 함께** 적는다.
# 비워 두는 게 기본이다 — 예외가 늘기 시작하면 규칙이 아니라 장식이 된다.
ALLOWED_WITHOUT_RLS: dict[str, str] = {}


LINE_COMMENT = re.compile(r"--[^\n]*")
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _sql_text() -> str:
    """주석을 걷어낸 SQL 만 본다.

    이 레포는 마이그레이션에도 '왜' 를 길게 적는다. 원문 그대로 스캔하면 설명 문장이
    DDL 로 잡힌다 — 실제로 personalization_core.sql 의 주석에 있던
    "`create table if not exists` 가 스킵되는" 이라는 설명이 테이블 생성으로 걸렸다.
    그대로 두면 다음 사람은 테스트를 통과시키려고 **설명을 지우게** 된다. 정확히 반대 방향이라
    프런트 쪽 가드(tests/frontend/kakao-oidc-login.test.mjs)와 같은 처리를 한다.
    """
    raw = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql"))
    )
    return LINE_COMMENT.sub(" ", BLOCK_COMMENT.sub(" ", raw))


def test_every_created_table_enables_rls():
    sql = _sql_text()
    created = {name.lower() for name in CREATE_TABLE.findall(sql)}
    secured = {name.lower() for name in ENABLE_RLS.findall(sql)}

    missing = sorted(created - secured - set(ALLOWED_WITHOUT_RLS))
    assert not missing, (
        "RLS 선언이 없는 테이블: "
        + ", ".join(missing)
        + " — 만든 마이그레이션에 `alter table public.<이름> enable row level security;` 를 "
        "같이 넣어라. 정책은 만들지 않는다(정책 없는 RLS = anon 전면 거부, 서버는 소유자라 우회)."
    )


def test_rls_drift_repair_migration_exists():
    """실측 보정 블록이 남아 있는지.

    선언만으로는 부족했다 — `assets` 는 init.sql 에 선언이 **있는데도** 라이브에서 꺼져 있었다
    (CI 가 옛 DB 를 가리키던 2026-08-29 사고와 같은 뿌리). 그래서 pg_class 를 직접 보고 켜는
    블록을 뒀다. 지우면 드리프트가 다시 조용히 산다.
    """
    repair = MIGRATIONS / "20260923140000_enable_rls_all_public_tables.sql"
    assert repair.exists(), "RLS 드리프트 보정 마이그레이션이 사라졌다"

    text = repair.read_text(encoding="utf-8")
    assert "pg_class" in text and "relrowsecurity" in text, "실측 보정 블록이 빠졌다"
    assert "create policy" not in text.lower(), (
        "이 마이그레이션은 정책을 만들지 않는다 — 여는 순간 anon 에게 문이 생긴다"
    )
