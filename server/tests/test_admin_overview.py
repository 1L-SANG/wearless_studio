"""대시보드 집계 — 숫자의 정의가 설계와 일치하는지.

되돌아가면: 데모용 시뮬 정산(payment_id 'sim:')과 테스트 결제(provider 'test')가 매출로
섞여 들어간다. 그 숫자를 보고 의사결정을 하면 틀린 결정을 한다.
"""
import contextlib
from datetime import datetime, timedelta, timezone

import pytest

from app import facemarket_admin


class FakeCursor:
    def __init__(self, store, rows):
        self.store = store
        self.rows = rows
        self._row = None

    async def execute(self, sql, params=None):
        self.store.append((" ".join(sql.split()), params))
        self._row = self.rows.pop(0) if self.rows else {}

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._row if isinstance(self._row, list) else []


class FakeConn:
    def __init__(self, rows):
        self.executed = []
        self.rows = list(rows)

    def cursor(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeCursor(self.executed, self.rows)

        return _cm()


QUEUE_ROW = {
    "applications_under_review": 3, "identity_mismatch": 1,
    "email_failed": 0, "refunds_pending": 2,
}
KPI_ROW = {
    "applications_submitted": 12, "applications_approved": 7, "applications_rejected": 3,
    "licenses_issued": 5, "settlement_amount_krw": 120000, "settlement_failed": 1,
    "credit_revenue_krw": 350000,
}
SERIES_ROWS = [
    {"date": "2026-09-03", "applications": 1, "licenses": 0, "settlement_amount_krw": 0},
    {"date": "2026-09-04", "applications": 2, "licenses": 1, "settlement_amount_krw": 20000},
]
DIST_ROW = {
    "models_pending": 2, "models_verified": 9, "models_suspended": 1,
    "enrollments_passed": 9, "enrollments_failed": 3, "enrollments_in_flight": 2,
}


def _conn():
    return FakeConn([QUEUE_ROW, KPI_ROW, SERIES_ROWS, DIST_ROW])


def test_rejects_unsupported_period():
    with pytest.raises(Exception) as exc:
        facemarket_admin.validate_days(45)
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "invalid_period"


def test_accepts_supported_periods():
    for days in (7, 30, 90):
        assert facemarket_admin.validate_days(days) == days


def test_simulation_settlements_are_excluded():
    import asyncio
    conn = _conn()
    asyncio.run(facemarket_admin.build_overview(conn, days=30))
    sql = " ".join(s for s, _ in conn.executed)
    assert "payment_id not like 'sim:%%'" in sql, "시뮬 정산이 매출에 섞인다"


def test_test_payments_are_excluded():
    import asyncio
    conn = _conn()
    asyncio.run(facemarket_admin.build_overview(conn, days=30))
    sql = " ".join(s for s, _ in conn.executed)
    assert "provider <> 'test'" in sql, "테스트 결제가 매출에 섞인다"


def test_series_uses_kst_day_boundaries():
    import asyncio
    conn = _conn()
    asyncio.run(facemarket_admin.build_overview(conn, days=30))
    sql = " ".join(s for s, _ in conn.executed)
    assert "at time zone 'Asia/Seoul'" in sql, "UTC 로 날짜를 자르면 운영자의 '오늘'과 다르다"
    assert "generate_series" in sql, "빈 날짜가 0 으로 채워지지 않는다"


def test_email_failed_counts_latest_row_per_application_not_ever_failed():
    """admin_resend_email 은 실패한 행을 고치지 않고 새 행을 INSERT 한다(facemarket_applications.py).

    지원서 목록 배지(admin_list_applications)는 신청서당 최신 메일 행만 lateral 로 본다.
    큐가 "한 번이라도 실패한 적 있으면" count(distinct application_id) 로 세면, 첫 발송이
    실패하고 재발송이 성공한 지원서를 카드는 '발송됨'이라 하는데 큐는 영원히 센다 — 고쳐도
    안 줄어드는 큐는 없는 큐보다 나쁘다. 최신 행 기준 lateral 이어야 두 화면이 같은 말을 한다.
    """
    sql = " ".join(facemarket_admin.QUEUE_SQL.split())
    assert "order by e.created_at desc limit 1" in sql, (
        "최신 메일 행만 보는 lateral 이 없다 — 목록 배지와 큐가 어긋난다"
    )
    assert "count(distinct application_id)" not in sql, (
        "이력 전체를 세는 옛 방식이 남아 있다 — 재발송 성공해도 큐가 안 줄어든다"
    )
    # 여기까지는 "최신 행만 본다"만 확인한다 — 그 최신 행 자체가 아예 없는 지원서(이메일
    # INSERT 가 한 번도 성공한 적 없는 경우)를 어떻게 세는지는 안 본다. join lateral(inner)
    # 이면 그 지원서는 조인 결과에서 통째로 사라져 카운트에 안 잡힌다. admin_list_applications
    # 는 left join lateral 이라 em 이 전부 null 이어도 행이 살아남고, 화면은 "결정됐는데
    # 메일 행이 없다"를 미발송으로 취급한다(:88, applications.py). 큐도 같은 말을 해야 한다.
    assert "left join lateral" in sql, (
        "join lateral 이 inner 다 — 이메일 행이 0개인 지원서가 조인에서 사라져 큐가 "
        "0을 보고한다. 결정 메일 INSERT 자체가 실패했을 때(풀 고갈·DB 블립·태스크 취소)가 "
        "바로 이 경우이고, 그게 이 배지가 잡아야 할 사례다."
    )
    assert (
        "a.status in ('approved', 'rejected') and em.last_status is null" in sql
    ), (
        "이메일 행이 아예 없는 결정된 지원서(em.last_status is null)를 세는 절이 없다 — "
        "목록 배지의 두 번째 조건(admin_list_applications)과 한 글자도 달라선 안 된다"
    )


def test_payload_shape_is_camel_case():
    import asyncio
    payload = asyncio.run(facemarket_admin.build_overview(_conn(), days=30))
    assert payload["queue"]["applicationsUnderReview"] == 3
    assert payload["kpi"]["settlementAmountKrw"] == 120000
    assert payload["kpi"]["creditRevenueKrw"] == 350000
    assert payload["distribution"]["models"]["verified"] == 9
    assert payload["distribution"]["enrollments"]["inFlight"] == 2
    assert payload["series"][1]["settlementAmountKrw"] == 20000
    assert payload["period"]["days"] == 30


def test_period_carries_the_end_instant_the_numbers_describe():
    """30초 캐시로 나가는 응답이라 클라이언트 시계로는 끝 경계를 못 구한다 — 서버가 명시해야 한다."""
    import asyncio
    payload = asyncio.run(facemarket_admin.build_overview(_conn(), days=30))
    period = payload["period"]
    assert "to" in period
    to_dt = datetime.fromisoformat(period["to"])
    from_dt = datetime.fromisoformat(period["from"])
    assert to_dt.tzinfo is not None, "to 도 from 과 같은 UTC-aware ISO 여야 한다"
    assert to_dt > from_dt


def test_period_start_is_kst_midnight():
    start = facemarket_admin._period_start(7)
    kst = timezone(timedelta(hours=9))
    local = start.astimezone(kst)
    assert (local.hour, local.minute, local.second) == (0, 0, 0)
    today = datetime.now(kst).date()
    assert local.date() == today - timedelta(days=6)


def test_cache_serves_second_call_without_touching_db():
    import asyncio
    facemarket_admin.clear_overview_cache()
    first = _conn()
    asyncio.run(facemarket_admin.overview_payload(first, days=30))
    second = _conn()
    asyncio.run(facemarket_admin.overview_payload(second, days=30))
    assert second.executed == [], "캐시가 안 먹어 새로고침마다 DB 를 친다"


def test_cache_is_per_period():
    import asyncio
    facemarket_admin.clear_overview_cache()
    asyncio.run(facemarket_admin.overview_payload(_conn(), days=30))
    other = _conn()
    asyncio.run(facemarket_admin.overview_payload(other, days=7))
    assert other.executed, "기간이 다른데 30일치 캐시를 돌려준다"


def test_distribution_includes_reverification_required():
    """fm_models.status 가 reverification_required 를 허용하지만 분포에서 무시되면,
    생체 재검증 중인 모델이 대시보드에 안 보인다.
    """
    import asyncio
    dist_row_with_reverif = {
        "models_pending": 2, "models_verified": 9, "models_suspended": 1,
        "models_reverification_required": 3,  # 누락됐던 상태
        "enrollments_passed": 9, "enrollments_failed": 3, "enrollments_in_flight": 2,
    }
    conn = FakeConn([{}, {}, [], dist_row_with_reverif])
    payload = asyncio.run(facemarket_admin.build_overview(conn, days=30))
    models_dist = payload["distribution"]["models"]
    # reverificationRequired 가 없으면 이 단언은 실패한다 — 그게 테스트의 포인트다.
    assert "reverificationRequired" in models_dist, (
        "distribution.models 에 reverificationRequired 가 없다"
    )
    assert models_dist["reverificationRequired"] == 3
    # 기존 키들은 변하지 않아야 한다
    assert models_dist["pending"] == 2
    assert models_dist["verified"] == 9
    assert models_dist["suspended"] == 1
