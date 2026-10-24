"""추가구매 크레딧 유효기간 정책의 단일 서버 정본."""

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

_SEOUL = ZoneInfo("Asia/Seoul")
TOPUP_VALIDITY_EFFECTIVE_DATE = date(2026, 11, 23)
TOPUP_VALIDITY_EFFECTIVE_AT = datetime.combine(
    TOPUP_VALIDITY_EFFECTIVE_DATE, time.min, tzinfo=_SEOUL
).astimezone(timezone.utc)


def normalize_purchase_at(value: datetime | str | None, *, now: datetime | None = None) -> datetime:
    """관리자 입력·PG 응답의 결제시각을 UTC aware datetime으로 맞춘다.

    날짜만 있거나 타임존이 없는 관리자 입력은 서비스 기준시각인 KST로 해석한다.
    """
    if value is None:
        parsed = now or datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_SEOUL)
    return parsed.astimezone(timezone.utc)


def validity_applies(purchased_at: datetime) -> bool:
    return purchased_at >= TOPUP_VALIDITY_EFFECTIVE_AT
