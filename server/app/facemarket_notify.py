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


# 메일 로고는 SPA 호스트가 아니라 CDN 에서 받는다. facemarket.wearless.kr 은 없는 경로에도
# 200 + SPA HTML 을 돌려줘서, 이미지가 빠져도 "정상"처럼 보인다(2026-09-04 실제로 그렇게
# 배포 전 경로를 정상으로 오판했다). images.wearless.kr 은 없으면 404 를 준다.
LOGO_URL = "https://images.wearless.kr/brand/facemarket-logo@2x.png"

INK = "#0e0d14"
MUTED = "#898989"
LINE = "#eceef1"
PAGE = "#f6f7f9"
CARD = "#ffffff"


def _shell(*, public_base: str, heading: str, body_html: str, cta: tuple[str, str] | None,
           footnote: str) -> str:
    """메일 한 통의 껍데기. 표(table) + 인라인 스타일만 쓴다.

    Gmail 은 <style> 블록을 지우는 경로가 있고 아웃룩(Word 렌더러)은 flex·grid 를 모른다 —
    그래서 레이아웃은 중첩 table, 스타일은 전부 인라인이다. 배경·글자색을 명시하는 것도
    같은 이유다: 색을 비워 두면 다크모드 클라이언트가 제멋대로 반전시켜 읽을 수 없게 만든다.
    로고는 CDN(LOGO_URL)의 PNG 다(SVG 는 대부분의 메일 클라이언트가 렌더하지 않는다) — 원본
    SVG 를 표시 폭의 2배(560px)로 구워 레티나에서 뭉개지지 않게 하고, width 로 140px 에 앉힌다. 이미지를
    막아 두고 여는 사람이 많아 alt 를 반드시 남긴다.
    """
    button = ""
    if cta:
        label, href = cta
        button = (
            f'<tr><td style="padding:28px 0 0 0;">'
            f'<a href="{href}" style="display:inline-block;background:{INK};color:#ffffff;'
            f'text-decoration:none;font-size:15px;font-weight:600;padding:13px 22px;'
            f'border-radius:8px;">{label}</a>'
            f"</td></tr>"
        )
    return (
        f'<div style="background:{PAGE};padding:32px 16px;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="max-width:520px;margin:0 auto;background:{CARD};border:1px solid {LINE};'
        f'border-radius:14px;">'
        f'<tr><td style="padding:32px 32px 0 32px;">'
        f'<img src="{LOGO_URL}" alt="FaceMarket" '
        f'width="140" style="display:block;border:0;height:auto;width:140px;" />'
        f"</td></tr>"
        f'<tr><td style="padding:24px 32px 0 32px;font-family:-apple-system,BlinkMacSystemFont,'
        f'\'Segoe UI\',Roboto,\'Helvetica Neue\',Arial,sans-serif;">'
        f'<h1 style="margin:0;font-size:22px;line-height:1.35;font-weight:600;color:{INK};">'
        f"{heading}</h1>"
        f"</td></tr>"
        f'<tr><td style="padding:14px 32px 0 32px;font-family:-apple-system,BlinkMacSystemFont,'
        f'\'Segoe UI\',Roboto,\'Helvetica Neue\',Arial,sans-serif;font-size:15px;'
        f'line-height:1.7;color:{INK};">{body_html}</td></tr>'
        f'<tr><td style="padding:0 32px;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0">{button}</table>'
        f"</td></tr>"
        f'<tr><td style="padding:26px 32px 0 32px;font-family:-apple-system,BlinkMacSystemFont,'
        f'\'Segoe UI\',Roboto,\'Helvetica Neue\',Arial,sans-serif;font-size:13px;'
        f'line-height:1.7;color:{MUTED};">{footnote}</td></tr>'
        f'<tr><td style="padding:24px 32px 30px 32px;">'
        f'<div style="border-top:1px solid {LINE};padding-top:16px;font-family:-apple-system,'
        f'BlinkMacSystemFont,\'Segoe UI\',Roboto,\'Helvetica Neue\',Arial,sans-serif;'
        f'font-size:12px;line-height:1.6;color:{MUTED};">'
        f"이 메일은 발신 전용이에요. 문의는 FaceMarket 안에서 남겨 주세요."
        f"</div></td></tr>"
        f"</table></div>"
    )


def _email_content(
    email_type: str, *, public_base: str, reject_reason: str | None
) -> tuple[str, str, str]:
    """(subject, html, text). 신원정보 없음(E4). 딥링크는 로그인 게이트 뒤 등록 상태(/status)로
    보낸다 — 예전 /model 허브는 2026-09-02 부터 /status 로 넘어가므로 처음부터 그리로.

    텍스트 파트를 함께 만든다: HTML 단독 발송은 스팸 점수를 올리고, 스타일·이미지를 막아 둔
    클라이언트에서는 본문이 통째로 사라진다. 버튼 주소를 텍스트에도 그대로 적어 두는 이유다.
    """
    hub = f"{public_base}/status"
    apply = f"{public_base}/model/apply"
    if email_type == "approved":
        subject = "[FaceMarket] 모델 지원이 승인됐어요"
        html = _shell(
            public_base=public_base,
            heading="모델 지원이 승인됐어요",
            body_html="신분증 인증부터 모델 등록을 이어가 주세요. 아래 버튼을 누르면 "
                      "로그인 후 지금 단계로 바로 이동해요.",
            cta=("모델 등록 계속하기", hub),
            footnote=f"버튼이 열리지 않으면 이 주소를 직접 열어 주세요: {hub}",
        )
        text = (
            "모델 지원이 승인됐어요.\n\n"
            "신분증 인증부터 모델 등록을 이어가 주세요.\n"
            f"{hub}\n\n"
            "이 메일은 발신 전용이에요."
        )
        return subject, html, text
    if email_type == "auto_rejected":
        # 신분증 대조 3회 불일치 자동 거절(스펙 7·10). 관리자 거절과 구분되는 별도 메일.
        subject = "[FaceMarket] 신분증 정보 불일치로 지원이 거절됐어요"
        html = _shell(
            public_base=public_base,
            heading="신분증 정보가 일치하지 않았어요",
            body_html="지원서에 적은 정보가 신분증과 3회 일치하지 않아 지원이 자동으로 "
                      "거절됐어요. 정보를 수정해 다시 지원할 수 있어요.",
            cta=("다시 지원하기", apply),
            footnote=f"버튼이 열리지 않으면 이 주소를 직접 열어 주세요: {apply}",
        )
        text = (
            "지원서에 적은 정보가 신분증과 3회 일치하지 않아 지원이 자동으로 거절됐어요.\n\n"
            "정보를 수정해 다시 지원할 수 있어요.\n"
            f"{apply}\n\n"
            "이 메일은 발신 전용이에요."
        )
        return subject, html, text
    # rejected
    subject = "[FaceMarket] 모델 지원 결과 안내"
    reason_html = (
        f'<div style="margin-top:14px;padding:12px 14px;background:{PAGE};'
        f'border-radius:8px;color:{INK};">사유: {_escape(reject_reason)}</div>'
        if reject_reason else ""
    )
    html = _shell(
        public_base=public_base,
        heading="이번 지원은 승인되지 않았어요",
        body_html="아쉽게도 이번 지원은 승인되지 않았어요. 정보를 수정해 다시 지원할 수 있어요."
                  + reason_html,
        cta=("다시 지원하기", apply),
        footnote=f"버튼이 열리지 않으면 이 주소를 직접 열어 주세요: {apply}",
    )
    reason_text = f"사유: {reject_reason}\n\n" if reject_reason else ""
    text = (
        "이번 지원은 승인되지 않았어요.\n\n"
        f"{reason_text}"
        "정보를 수정해 다시 지원할 수 있어요.\n"
        f"{apply}\n\n"
        "이 메일은 발신 전용이에요."
    )
    return subject, html, text


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
    subject, html, text = _email_content(
        email_type,
        public_base=settings.fm_application_public_base,
        reject_reason=reject_reason,
    )
    payload = {
        "from": settings.fm_application_from_email,
        "to": [to],
        "subject": subject,
        "html": html,
        # 텍스트 파트 동봉 — HTML 단독은 스팸 점수를 올리고, 스타일·이미지를 막아 둔
        # 클라이언트에서는 본문이 통째로 비어 보인다.
        "text": text,
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
