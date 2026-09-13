"""시각은 UTC 로 저장하고 **한국 날짜로 읽는다** — 그 경계가 실제로 지켜지는지.

되돌아가면: KST 00:00~08:59 에 생긴 것이 전부 전날로 보인다. 콘솔 대시보드는 KST 로
집계하므로 같은 화면 안에서 목록과 숫자가 어긋나고, 라이선스 VC 에는 하루 이른 날짜가
박힌다(발급 후 되돌릴 수 없는 값이다).
"""
import asyncio
import contextlib
from datetime import date, datetime, timedelta, timezone

from app import db, facemarket

KST = timezone(timedelta(hours=9))


# ---------- VC 클레임 날짜 ----------

def test_vc_issue_time_preserves_the_instant_across_korean_calendar_boundary():
    # UTC 2027-09-07 23:00 == KST 2027-09-08 08:00
    issued_at = datetime(2027, 9, 7, 23, 0, tzinfo=timezone.utc)
    claims = facemarket.build_face_vc_claims(
        model_did="did:omn:model", license_id="44444444-4444-4444-4444-444444444444",
        issued_at=issued_at, digest="d", consent_doc_version="v1.1",
    )
    assert claims["issuedAt"] == "2027-09-07T23:00:00Z"


def test_vc_issue_time_normalizes_stored_kst_timezone_to_utc():
    issued_at = datetime(2027, 9, 7, 12, 0, tzinfo=KST)
    claims = facemarket.build_face_vc_claims(
        model_did="did:omn:model", license_id="44444444-4444-4444-4444-444444444444",
        issued_at=issued_at, digest="d", consent_doc_version="v1.1",
    )
    assert claims["issuedAt"] == "2027-09-07T03:00:00Z"


def test_naive_datetime_is_read_as_utc_not_as_container_local():
    """`astimezone()` 에 naive 를 그냥 넘기면 시스템 TZ 를 가정한다 — 배포 환경에 따라
    같은 코드가 다른 날짜를 내면 안 된다."""
    assert facemarket._kst_date_str(datetime(2027, 9, 7, 23, 0)) == "2027-09-08"


def test_plain_date_passes_through():
    """이미 달력 날짜인 값에는 시간대 변환이 없다 — 있으면 그게 하루를 옮긴다."""
    assert facemarket._kst_date_str(date(2027, 9, 7)) == "2027-09-07"


# ---------- 커넥션 세션 시간대 ----------

class FakeCursor:
    def __init__(self, store):
        self.store = store

    async def execute(self, sql, params=None):
        self.store.append(" ".join(sql.split()))


class FakeConn:
    """트랜잭션 상태를 흉내 낸다 — 그게 이 콜백의 계약이기 때문이다.

    psycopg3 커넥션은 autocommit=False 라 statement 하나만 실행해도 트랜잭션이 열린다.
    커밋 여부를 안 보는 가짜 커넥션은 이 파일이 막으려는 사고를 못 잡는다(실제로 못 잡아서
    프로덕션이 나갔다 — 2026-09-08).
    """

    def __init__(self):
        self.executed = []
        self.in_transaction = False
        self.commits = 0

    def cursor(self):
        conn = self

        @contextlib.asynccontextmanager
        async def _cm():
            cursor = FakeCursor(self.executed)
            original = cursor.execute

            async def execute(sql, params=None):
                conn.in_transaction = True
                await original(sql, params)

            cursor.execute = execute
            yield cursor

        return _cm()

    async def commit(self):
        self.in_transaction = False
        self.commits += 1


def test_new_connections_are_set_to_kst():
    conn = FakeConn()
    asyncio.run(db._configure(conn))
    assert conn.executed == ["set time zone 'Asia/Seoul'"]


def test_configure_leaves_the_connection_idle():
    """psycopg_pool 은 configure 가 트랜잭션을 열어 둔 커넥션을 **버린다**
    ("connection left in status INTRANS by configure function: discarded"). 그러면 풀이
    영영 차지 않고 모든 워커·요청이 PoolTimeout 으로 죽는다 — 2026-09-08 프로덕션 장애.

    `set time zone` 한 줄이 트랜잭션을 여는 것이 함정이라, 커밋을 지우면 여기서 걸린다.
    """
    conn = FakeConn()
    asyncio.run(db._configure(conn))
    assert conn.commits == 1, "configure 가 커밋하지 않는다 — 풀이 커넥션을 전부 버린다"
    assert not conn.in_transaction, "커넥션이 트랜잭션 안에 남았다"


def test_pool_wires_the_configure_hook():
    """훅을 등록하지 않으면 위 테스트가 통과해도 실제 커넥션은 UTC 로 남는다."""
    pool = db.create_pool("postgresql://u:p@localhost:5432/postgres")
    try:
        assert pool._configure is db._configure
    finally:
        # open=False 라 커넥션은 안 열렸다 — 풀 객체만 정리한다.
        pass


def _executable_source(module) -> str:
    """주석·docstring 을 걷어낸 소스. 이 파일이 왜 커넥션 단위인지 **설명하는 주석**에
    'ALTER DATABASE' 가 들어 있어서, 텍스트를 그냥 훑으면 자기 설명에 걸려 넘어진다."""
    import io
    import tokenize

    with open(module.__file__, encoding="utf-8") as handle:
        tokens = list(tokenize.generate_tokens(handle.readline))
    kept = [
        t.string for t in tokens
        if t.type not in (tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE)
    ]
    return " ".join(kept).lower()


def test_session_timezone_is_not_applied_database_wide():
    """DB/역할 전역에 걸면 Supabase auth·realtime·storage 의 naive timestamp 컬럼이
    9시간 밀린다(auth.one_time_tokens 는 매직링크 만료 판정에 쓰인다). 커넥션 단위여야 한다."""
    code = _executable_source(db)
    assert "alter database" not in code
    assert "alter role" not in code
