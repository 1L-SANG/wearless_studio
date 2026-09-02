"""지원서 승인/거절 메일(Resend) + 새 지원서 Slack 알림(webhook). 전부 best-effort.

메일 본문에는 신원정보(이름·생년월일)를 담지 않는다(E4): 지원서 이메일은 미검증이라
오타 시 제3자 메일함으로 갈 수 있어 심사 결과·PII 노출이 된다. 거절 사유는 UX 가치가 크고
유출 민감도가 낮아 포함하되, 상세는 앱 상태 화면이 진실이다(2A). 링크는 권한 없는 딥링크다
(로그인 필수, 1A). 발송·알림 실패는 절대 승인/거절 트랜잭션을 막지 않는다 — 이미 커밋된 뒤
호출되고, 대시보드 '미발송' 뱃지·재발송으로 복구한다.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"
_TIMEOUT = httpx.Timeout(5.0, connect=3.0)


def _email_content(email_type: str, *, public_base: str, reject_reason: str | None) -> tuple[str, str]:
    """(subject, html). 신원정보 없음. 딥링크는 로그인 게이트 뒤 등록 상태(/status)로 보낸다 —
    예전 /model 허브는 2026-09-02 부터 /status 로 넘어가므로 처음부터 그리로."""
    hub = f"{public_base}/status"
    apply = f"{public_base}/model/apply"
    if email_type == "approved":
        subject = "[FaceMarket] 모델 지원이 승인됐어요"
        html = (
            "<p>모델 지원이 <b>승인</b>됐어요. 아래 버튼에서 로그인 후 신분증 인증부터 "
            "모델 등록을 이어가 주세요.</p>"
            f'<p><a href="{hub}">모델 등록 계속하기</a></p>'
            "<p>버튼이 열리지 않으면 FaceMarket 에 로그인해 상태를 확인할 수 있어요.</p>"
        )
        return subject, html
    if email_type == "auto_rejected":
        # 신분증 대조 3회 불일치 자동 거절(스펙 7·10). 관리자 거절과 구분되는 별도 메일.
        subject = "[FaceMarket] 신분증 정보 불일치로 지원이 거절됐어요"
        html = (
            "<p>지원서에 적은 이름·생년월일이 신분증과 3회 일치하지 않아 지원이 자동으로 "
            "<b>거절</b>됐어요.</p>"
            f'<p>정보를 수정해 다시 지원할 수 있어요: <a href="{apply}">다시 지원하기</a></p>'
        )
        return subject, html
    # rejected
    subject = "[FaceMarket] 모델 지원 결과 안내"
    reason_html = f"<p>사유: {_escape(reject_reason)}</p>" if reject_reason else ""
    html = (
        "<p>모델 지원이 <b>거절</b>됐어요.</p>"
        f"{reason_html}"
        f'<p>정보를 수정해 다시 지원할 수 있어요: <a href="{apply}">다시 지원하기</a></p>'
    )
    return subject, html


def _escape(text: str | None) -> str:
    if not text:
        return ""
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


async def send_application_email(
    settings,
    *,
    to: str,
    email_type: str,
    reject_reason: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """(ok, provider_message_id, error). 키 미설정이면 (False, None, 'not_configured')."""
    if not settings.resend_api_key:
        return False, None, "not_configured"
    subject, html = _email_content(
        email_type,
        public_base=settings.fm_application_public_base,
        reject_reason=reject_reason,
    )
    payload = {
        "from": settings.fm_application_from_email,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(
                _RESEND_URL,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json=payload,
            )
        if res.status_code >= 400:
            return False, None, f"resend_{res.status_code}"
        message_id = None
        try:
            message_id = res.json().get("id")
        except Exception:
            pass
        return True, message_id, None
    except Exception as exc:  # 네트워크·타임아웃 — best-effort
        logger.warning("resend send failed (%s): %s", email_type, exc)
        return False, None, "send_error"


def _slack_escape(text: str) -> str:
    """Slack mrkdwn 특수문자 이스케이프. 지역(region)은 지원자가 자유입력하는 값이라 그대로
    넣으면 `<https://evil/|검토 콘솔>` 같은 가짜 링크를 알림에 심을 수 있다 — 바로 아래에
    진짜 관리자 콘솔 링크가 붙으므로 관리자가 구분하기 어렵다. Slack 권장대로 & < > 만 바꾼다."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def notify_slack_new_application(
    settings, *, categories: list[str], region: str | None
) -> None:
    """새 지원서 도착 알림. 신원정보 없이 카테고리·지역만(검토 유도용). 실패는 무해."""
    if not settings.fm_slack_webhook_url:
        return
    cats = _slack_escape(", ".join(categories)) if categories else "-"
    text = f":inbox_tray: 새 모델 지원서 · 카테고리: {cats} · 지역: {_slack_escape(region or '-')}"
    admin_link = f"{settings.fm_application_public_base}".replace(
        "facemarket.", "admin."
    )
    text += f"\n<{admin_link}|관리자 검토 콘솔 열기>"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(settings.fm_slack_webhook_url, json={"text": text})
        # 상태를 안 보면 웹훅 폐기(404/410)·레이트리밋(429)이 성공과 구분되지 않는다. 이 알림은
        # 원장이 없어 로그가 유일한 관측점이다 — 조용히 끊기면 관리자는 지원서가 없다고 믿는다.
        if res.status_code >= 400:
            logger.error("slack notify rejected status=%s", res.status_code)
    except Exception as exc:
        logger.warning("slack notify failed: %s", exc)
