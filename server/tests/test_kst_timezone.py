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

def test_vc_date_is_the_korean_calendar_day_not_the_utc_one():
    """KST 오전에 만료되는 라이선스는 UTC 로 자르면 하루 이른 날짜가 된다."""
    # UTC 2027-09-07 23:00 == KST 2027-09-08 08:00
    valid_until = datetime(2027, 9, 7, 23, 0, tzinfo=timezone.utc)
    claims = facemarket.build_face_vc_claims(
        allowed=["a"], forbidden=["b"], unit_price=1000,
        valid_until=valid_until, digest="d",
    )
    assert claims["licenseValidUntil"] == "2027-09-08"


def test_vc_date_unchanged_when_utc_and_kst_agree():
    valid_until = datetime(2027, 9, 7, 3, 0, tzinfo=timezone.utc)  # KST 12:00 같은 날
    claims = facemarket.build_face_vc_claims(
        allowed=[], forbidden=[], unit_price=0, valid_until=valid_until, digest="d",
    )
    assert claims["licenseValidUntil"] == "2027-09-07"


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
    def __init__(self):
        self.executed = []

    def cursor(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeCursor(self.executed)

        return _cm()


def test_new_connections_are_set_to_kst():
    conn = FakeConn()
    asyncio.run(db._configure(conn))
    assert conn.executed == ["set time zone 'Asia/Seoul'"]


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
