"""계좌이체 신청·지급 알림(Resend). 전부 best-effort — 커밋 뒤에 부르고 실패해도 결과를 뒤집지 않는다."""

import logging
from html import escape

from .facemarket_notify import _send_email

logger = logging.getLogger(__name__)


def _html(lines: list[str]) -> str:
    # 입금자명·메모·상호는 사용자 입력이다. 그대로 이어 붙이면 관리자 메일에 가짜 링크를 심을 수 있다.
    return "<p>" + "<br>".join(escape(line) for line in lines) + "</p>"


async def _send(settings, *, to: str, subject: str, lines: list[str]) -> None:
    # 결제 고객 메일은 Wearless 이름으로 나가야 한다. _send_email 의 기본 발신자는 FaceMarket 이다.
    sender = getattr(settings, "bank_transfer_from_email", None)
    await _send_email(settings, to=to, subject=subject, html=_html(lines), text="\n".join(lines),
                      sender=sender)


def _won(n) -> str:
    return f"{int(n):,}원"


def _product_label(req: dict) -> str:
    unit = "1개월 이용권" if req.get("kind") == "subscription" else "충전"
    return f"{req.get('plan_code')} {unit}"


async def notify_admin_new_request(settings, req: dict, *, user_email: str | None) -> None:
    to = getattr(settings, "bank_transfer_notify_email", None)
    if not to or not getattr(settings, "resend_api_key", None):
        return
    subject = f"[Wearless] 계좌이체 신청 · {_product_label(req)} · {_won(req['amount'])} · {req['payer_name']}"
    lines = [
        f"상품: {_product_label(req)}",
        f"금액: {_won(req['amount'])} / 크레딧 {req['credits']:,}",
        f"입금자명: {req['payer_name']}",
        f"연락처: {req.get('phone') or '-'}",
        f"가입 이메일: {user_email or '-'}",
        f"세금계산서: {'필요' if req.get('tax_invoice') else '불필요'}",
    ]
    if req.get("tax_invoice"):
        lines.append(
            f"사업자: {req.get('business_no')} / {req.get('business_name')} / "
            f"대표 {req.get('representative_name')} / {req.get('invoice_email')}"
        )
    if req.get("note"):
        lines.append(f"메모: {req['note']}")
    try:
        await _send(settings, to=to, subject=subject, lines=lines)
    except Exception:
        logger.warning("bank transfer admin notify failed", exc_info=True)


async def notify_user_confirmed(settings, *, to: str | None, req: dict, ends_at) -> None:
    if not to or not getattr(settings, "resend_api_key", None):
        return
    subject = f"[Wearless] 크레딧이 지급됐어요 · {_product_label(req)}"
    lines = [f"{_product_label(req)} 입금이 확인돼 크레딧 {req['credits']:,}이 지급됐어요."]
    if ends_at is not None:
        lines.append(f"이용권 종료일: {str(ends_at)[:10]} (자동 갱신 없음)")
    try:
        await _send(settings, to=to, subject=subject, lines=lines)
    except Exception:
        logger.warning("bank transfer user notify failed", exc_info=True)
