"""지원서 승인/거절 메일(Resend) + FaceMarket Slack 알림(webhook).

메일 본문에는 신원정보(이름·생년월일)를 담지 않는다(E4): 지원서 이메일은 미검증이라
오타 시 제3자 메일함으로 갈 수 있어 심사 결과·PII 노출이 된다. 거절 사유는 UX 가치가 크고
유출 민감도가 낮아 포함하되, 상세는 앱 상태 화면이 진실이다(2A). 링크는 권한 없는 딥링크다
(로그인 필수, 1A). 발송·알림 실패는 절대 승인/거절 트랜잭션을 막지 않는다 — 이미 커밋된 뒤
호출되고, 대시보드 '미발송' 뱃지·재발송으로 복구한다. 라이선스 해지 알림은
fm_license_revoke_alerts 작업이 성공할 때까지 재시도한다.
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

# 모델에게 가는 메일 하단 문의처. 인스타그램 계정은 @facemarket_official(2026-09-25 오너 확인).
_CONTACT_LINE = "문의가 있다면 010-9592-0333 또는 @facemarket_official로 부탁드려요."


def _shell(*, public_base: str, heading: str, body_html: str, cta: tuple[str, str] | None,
           footnote: str, model_facing: bool = True) -> str:
    """메일 한 통의 껍데기. 표(table) + 인라인 스타일만 쓴다.

    Gmail 은 <style> 블록을 지우는 경로가 있고 아웃룩(Word 렌더러)은 flex·grid 를 모른다 —
    그래서 레이아웃은 중첩 table, 스타일은 전부 인라인이다. 배경·글자색을 명시하는 것도
    같은 이유다: 색을 비워 두면 다크모드 클라이언트가 제멋대로 반전시켜 읽을 수 없게 만든다.
    로고는 CDN(LOGO_URL)의 PNG 다(SVG 는 대부분의 메일 클라이언트가 렌더하지 않는다) — 원본
    SVG 를 표시 폭의 2배(560px)로 구워 레티나에서 뭉개지지 않게 하고, width 로 140px 에 앉힌다. 이미지를
    막아 두고 여는 사람이 많아 alt 를 반드시 남긴다.
    """
    footer = (
        f"이 메일은 발신 전용이에요.<br>{_CONTACT_LINE}"
        if model_facing else "이 메일은 발신 전용이에요. 문의는 FaceMarket 안에서 남겨 주세요."
    )
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
        f"{footer}"
        f"</div></td></tr>"
        f"</table></div>"
    )


# 지원 승인 메일의 "최종 등록까지 진행될 과정". (단계, 단계 아래 작은 안내) 순서 그대로 번호가 붙는다.
_APPROVED_STEPS = (
    ("본인확인", None),
    ("AI 모델 생성을 위한 18장 사진 찍기 (낮 시간대)", "* 촬영을 도와줄 사람이 있어야 편해요"),
    ("사용 조건 정하기", None),
    ("블록체인 기반 라이선스 증서 발급", None),
    ("사진 검수 후, 프로필에 쓰일 테스트컷 선정 진행", None),
    ("최종 등록 완료", None),
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
    if email_type == "test_cuts_ready":
        confirm = f"{public_base}/model/confirm"
        subject = "[FaceMarket] 테스트컷이 도착했어요"
        # 다른 메일과 같은 껍데기(_shell) + 텍스트 파트. 예전엔 (subject, html) 두 값만 돌려줘
        # 호출부(send_application_email)의 3값 언패킹이 TypeError 로 터졌고, 그 예외를 보내기
        # 핸들러가 삼켜서 Resend 가 켜진 실서버에서 이 메일만 한 번도 안 나갔다(2026-09-07 발견).
        html = _shell(
            public_base=public_base,
            heading="테스트컷이 도착했어요",
            body_html="등록이 끝나 테스트컷을 준비했어요. 확대샷 1장, 전신샷 1장을 골라 "
                      "프로필로 공개해 주세요. 확정하기 전에는 아무에게도 공개되지 않아요.",
            cta=("테스트컷 확인하기", confirm),
            footnote=f"버튼이 열리지 않으면 이 주소를 직접 열어 주세요: {confirm}",
        )
        text = (
            "테스트컷이 도착했어요.\n\n"
            "확대샷 1장, 전신샷 1장을 골라 프로필로 공개해 주세요. "
            "확정하기 전에는 아무에게도 공개되지 않아요.\n"
            f"{confirm}\n\n"
            f"이 메일은 발신 전용이에요.\n{_CONTACT_LINE}"
        )
        return subject, html, text
    if email_type == "approved":
        subject = "[FaceMarket] 모델 지원이 승인됐어요"
        html = _shell(
            public_base=public_base,
            heading="모델 지원이 승인됐어요",
            # 문구는 2026-09-25 오너 확정본이다. 단계 목록은 _APPROVED_STEPS 하나를 HTML·텍스트가 같이 쓴다.
            body_html=(
                "앞으로 최종 등록까지 진행될 과정을 알려드릴게요."
                f'<ol style="margin:12px 0 0 0;padding-left:22px;">'
                + "".join(
                    f'<li style="margin:0 0 6px 0;">{step}'
                    + (f'<br><span style="font-size:13px;color:{MUTED};">{note}</span>' if note else "")
                    + "</li>"
                    for step, note in _APPROVED_STEPS
                )
                + "</ol>"
                '<p style="margin:14px 0 0 0;">서둘러 2차 등록을 하여 1차 기수에 선정돼보세요!</p>'
            ),
            cta=("모델 등록 계속하기", hub),
            footnote=f"버튼이 열리지 않으면 이 주소를 직접 열어 주세요: {hub}",
        )
        steps = "".join(
            f"{index}. {step}\n" + (f"{note}\n" if note else "")
            for index, (step, note) in enumerate(_APPROVED_STEPS, start=1)
        )
        text = (
            "모델 지원이 승인됐어요.\n\n"
            "앞으로 최종 등록까지 진행될 과정을 알려드릴게요.\n"
            f"{steps}\n"
            "서둘러 2차 등록을 하여 1차 기수에 선정돼보세요!\n"
            f"{hub}\n\n"
            f"이 메일은 발신 전용이에요.\n{_CONTACT_LINE}"
        )
        return subject, html, text
    if email_type == "enrollment_review_approved":
        # 간편인증 경로 육안 심사 승인. 지원서 승인(approved)과 다른 단계다 — 이 시점엔
        # 이미 사진까지 다 냈고, 모델 이미지 생성만 남았다.
        subject = "[FaceMarket] 본인 확인이 완료됐어요"
        html = _shell(
            public_base=public_base,
            heading="본인 확인이 완료됐어요",
            body_html="신분증과 등록 사진 확인을 마쳤어요. 마이페이지에서 남은 등록 절차를 "
                      "이어가 주세요. 테스트컷을 직접 확인·확정한 뒤에 모델이 공개돼요.",
            cta=("등록 상태 보기", hub),
            footnote=f"버튼이 열리지 않으면 이 주소를 직접 열어 주세요: {hub}",
        )
        text = (
            "본인 확인이 완료됐어요.\n\n"
            "신분증과 등록 사진 확인을 마쳤어요. 마이페이지에서 남은 등록 절차를 이어가 주세요.\n"
            "테스트컷을 직접 확인·확정한 뒤에 모델이 공개돼요.\n"
            f"{hub}\n\n"
            f"이 메일은 발신 전용이에요.\n{_CONTACT_LINE}"
        )
        return subject, html, text
    if email_type == "enrollment_review_rejected":
        subject = "[FaceMarket] 본인 확인이 완료되지 않았어요"
        reason_html = (
            f'<div style="margin-top:14px;padding:12px 14px;background:{PAGE};'
            f'border-radius:8px;color:{INK};">사유: {_escape(reject_reason)}</div>'
            if reject_reason else ""
        )
        html = _shell(
            public_base=public_base,
            heading="본인 확인이 완료되지 않았어요",
            body_html="제출하신 신분증과 얼굴 사진으로는 본인 확인을 마치지 못했어요. "
                      "아래에서 다시 시도할 수 있어요." + reason_html,
            cta=("다시 시도하기", hub),
            footnote=f"버튼이 열리지 않으면 이 주소를 직접 열어 주세요: {hub}",
        )
        reason_text = f"사유: {reject_reason}\n\n" if reject_reason else ""
        text = (
            "제출하신 신분증과 얼굴 사진으로는 본인 확인을 마치지 못했어요.\n\n"
            f"{reason_text}"
            "아래에서 다시 시도할 수 있어요.\n"
            f"{hub}\n\n"
            f"이 메일은 발신 전용이에요.\n{_CONTACT_LINE}"
        )
        return subject, html, text
    if email_type == "enrollment_review_timeout":
        # 심사 기한(5일)을 넘겨 자동 종료. 신분증 촬영본은 7일 배치 스윕이 지우므로 그 전에
        # 끝내야 하고, 그대로 두면 이 사용자의 단일 활성 등록 슬롯이 영구히 묶인다.
        subject = "[FaceMarket] 본인 확인이 기한 내에 끝나지 않았어요"
        html = _shell(
            public_base=public_base,
            heading="본인 확인이 기한 내에 끝나지 않았어요",
            body_html="확인에 시간이 너무 오래 걸려 이번 등록은 자동으로 종료됐어요. "
                      "불편을 드려 죄송해요 — 아래에서 바로 다시 시작할 수 있어요.",
            cta=("다시 등록하기", hub),
            footnote=f"버튼이 열리지 않으면 이 주소를 직접 열어 주세요: {hub}",
        )
        text = (
            "확인에 시간이 너무 오래 걸려 이번 등록은 자동으로 종료됐어요.\n\n"
            "아래에서 바로 다시 시작할 수 있어요.\n"
            f"{hub}\n\n"
            f"이 메일은 발신 전용이에요.\n{_CONTACT_LINE}"
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
            f"이 메일은 발신 전용이에요.\n{_CONTACT_LINE}"
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
        f"이 메일은 발신 전용이에요.\n{_CONTACT_LINE}"
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
    return await _send_email(settings, to=to, subject=subject, html=html, text=text)


async def send_registration_completed_email(
    settings, *, to: str, display_name: str
) -> tuple[bool, str | None, str | None]:
    """첫 테스트컷 확정 뒤 공개와 증서 발급 완료를 함께 알려요."""
    if not settings.resend_api_key:
        return False, None, "not_configured"
    status_url = f"{settings.fm_application_public_base}/status"
    safe_name = _escape(display_name)
    subject = "[FaceMarket] 등록이 최종 완료됐어요"
    html = _shell(
        public_base=settings.fm_application_public_base,
        heading="등록이 최종 완료됐어요",
        body_html=(
            f"{safe_name}님의 프로필이 공개됐어요. 라이선스 증서도 발급됐어요. "
            "발급한 라이선스 증서는 모델님이 철회하기 전까지 유효해요. "
            "마이페이지에서 증서를 확인할 수 있어요."
        ),
        cta=("마이페이지 열기", status_url),
        footnote=f"버튼이 열리지 않으면 이 주소를 직접 열어 주세요: {status_url}",
    )
    text = (
        f"{display_name}님의 프로필이 공개됐어요. 라이선스 증서도 발급됐어요. "
        "발급한 라이선스 증서는 모델님이 철회하기 전까지 유효해요. "
        "마이페이지에서 증서를 확인할 수 있어요.\n"
        f"{status_url}\n\n"
        f"이 메일은 발신 전용이에요.\n{_CONTACT_LINE}"
    )
    return await _send_email(settings, to=to, subject=subject, html=html, text=text)


async def send_usage_report_email(
    settings,
    *,
    to: str,
    payment_id: str,
    reason: str | None,
) -> tuple[bool, str | None, str | None]:
    """운영자에게 새 사용 신고를 알린다. 신고 저장은 이미 커밋된 뒤라 발송은 best-effort다."""
    safe_payment = _escape(payment_id)
    safe_reason = _escape(reason) or "사유 미입력"
    subject = "[FaceMarket] 새 사용 기록 신고가 접수됐어요"
    html = _shell(
        public_base=settings.fm_application_public_base,
        heading="새 사용 기록 신고가 접수됐어요",
        body_html=(
            f'<div style="margin-top:8px;color:{INK};">정산 기록: {safe_payment}</div>'
            f'<div style="margin-top:12px;padding:12px 14px;background:{PAGE};'
            f'border-radius:8px;color:{INK};">사유: {safe_reason}</div>'
        ),
        cta=None,
        footnote="관리자 도구에서 해당 정산 기록과 사용 범위를 확인해 주세요.",
        model_facing=False,
    )
    text = (
        "새 사용 기록 신고가 접수됐어요.\n\n"
        f"정산 기록: {payment_id}\n"
        f"사유: {reason or '사유 미입력'}\n"
    )
    return await _send_email(settings, to=to, subject=subject, html=html, text=text)


async def _send_email(
    settings, *, to: str, subject: str, html: str, text: str, sender: str | None = None,
) -> tuple[bool, str | None, str | None]:
    if not settings.resend_api_key:
        return False, None, "not_configured"
    payload = {
        # sender 는 FaceMarket 이 아닌 이름으로 보내야 하는 메일(계좌이체 결제 안내)용.
        "from": sender or settings.fm_application_from_email,
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
        logger.warning("resend send failed (%s): %s", subject, exc)
        return False, None, "send_error"


def _slack_escape(text: str) -> str:
    """Slack mrkdwn 특수문자 이스케이프. 지역(region)은 지원자가 자유입력하는 값이라 그대로
    넣으면 `<https://evil/|검토 콘솔>` 같은 가짜 링크를 알림에 심을 수 있다 — 바로 아래에
    진짜 관리자 콘솔 링크가 붙으므로 관리자가 구분하기 어렵다. Slack 권장대로 & < > 만 바꾼다."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _post_slack(settings, text: str) -> bool:
    """incoming webhook 전송 결과를 반환한다. 해지 알림은 실패를 작업 큐에 남긴다."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(settings.fm_slack_webhook_url, json={"text": text})
        if not 200 <= res.status_code < 300:
            logger.error("slack notify rejected status=%s", res.status_code)
            return False
        return True
    except Exception as exc:
        logger.warning("slack notify failed: %s", exc)
        return False


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
    await _post_slack(settings, text)


async def notify_slack_enrollment_completed(
    settings, *, display_name: str | None, identity_method: str, admin_link: str
) -> None:
    """사용 조건 제출로 등록을 마쳤다는 알림. 본인확인에서 받은 가린 이름(예: 홍*동)과 인증 방법만 싣는다."""
    if not settings.fm_slack_webhook_url:
        return
    # 가린 이름 규칙(cx_identity._mask_name)은 한 글자 이름을 그대로 두므로 여기서 가린다.
    if display_name and len(display_name.strip()) == 1:
        display_name = "*"
    if identity_method == "simple_auth":
        method_label = "간편인증"
        next_step = "신원 확인을 승인한 뒤 사진 18장을 확인해 주세요."
    else:
        method_label = "모바일 신분증"
        next_step = "사진 18장을 확인해 주세요."
    text = (
        ":camera_with_flash: 2차 등록 완료 · 이름: "
        f"{_slack_escape(display_name or '-')} · 인증: {method_label}\n"
        f"{next_step}\n"
        f"<{admin_link}|관리자 등록 심사 열기>"
    )
    await _post_slack(settings, text)


async def notify_slack_model_confirmed(
    settings, *, display_name: str, admin_link: str
) -> None:
    """모델이 공개 프로필을 확정했다는 알림. 활동명만 싣고 실패는 무해하게 끝낸다."""
    if not settings.fm_slack_webhook_url:
        return
    text = (
        ":white_check_mark: 모델 공개 확정 · 활동명: "
        f"{_slack_escape(display_name)}\n"
        f"<{admin_link}|관리자 모델 콘솔 열기>"
    )
    await _post_slack(settings, text)


async def notify_slack_license_revoked(
    settings,
    *,
    model_id: str,
    display_name: str,
    revoked_on: str,
    purge_due_on: str,
    other_active_licenses: int,
    admin_link: str,
) -> bool:
    """모델 라이선스 해지와 수동 파기 기한을 관리자에게 알린다.

    display_name 은 본인확인에서 받은 가려진 실명(예: 홍*동)이라 같은 이름이 여럿일 수 있다.
    파기할 모델을 정확히 찾도록 내부 모델 ID 를 함께 싣는다."""
    if not settings.fm_slack_webhook_url:
        return False
    text = (
        f":warning: 모델 라이선스 해지 · 모델: {_slack_escape(display_name)} · ID {model_id}\n"
        f"해지일 {revoked_on} · 파기 기한 {purge_due_on}(30일)\n"
        "원본 사진·특징정보·얼굴 참조 자산(학습 가중치·GPU 서버 사본·개발자 PC 학습 "
        "사본 포함)·테스트컷을 지우고, 백업은 90일 안에 지운 뒤 모델에게 알려야 해요."
    )
    if other_active_licenses > 0:
        text += (
            f"\n주의: 이 모델에게 아직 유효한 라이선스가 {other_active_licenses}건 있어요. "
            "파기 전에 확인하세요."
        )
    text += f"\n<{admin_link}|관리자 모델 콘솔 열기>"
    return await _post_slack(settings, text)


async def notify_slack_admin_device_requested(settings, *, email: str | None, label: str) -> None:
    """관리자 콘솔에 새 기기가 승인을 요청했다. 승인은 다른 관리자가 콘솔에서 한다 — 이 알림이
    없으면 상대는 요청이 있는지도 모른다. 탈취된 계정의 요청도 이 알림으로 드러난다(요청한 적
    없는 기기가 뜬다). 실패는 무해 — 등록 자체는 이미 커밋됐다."""
    if not settings.fm_slack_webhook_url:
        return
    admin_link = f"{settings.fm_application_public_base}".replace("facemarket.", "admin.") + "/staff"
    text = (
        f":closed_lock_with_key: 관리자 기기 승인 요청 · {_slack_escape(email or '-')} · "
        f"{_slack_escape(label)}\n<{admin_link}|관리자 관리 열기>"
    )
    await _post_slack(settings, text)


async def notify_slack_payout_simulated(
    settings, *, period_month: str, amount: int, count: int, paid: bool,
    failure_reason: str | None = None, reference: str | None = None,
) -> None:
    """지급 **시뮬레이션** 결과 알림(데모). 돈은 움직이지 않았다.

    🔴 배지를 빼지 마라. 이 알림을 보는 사람은 우리 팀이고, 배지가 없으면 실제로 돈이 나갔다고
    읽는다 — 목이 만드는 가장 큰 사고가 바로 그거다. 모델에게 가는 알림은 여기서 만들지 않는다
    (돈이 안 갔는데 "입금됐다"고 말하는 순간 목이 거짓말이 된다).

    관리자가 버튼을 누를 때마다 한 건씩 보낸다 — 자동 스윕이 아니라 사람의 동작이라 도배되지
    않는다. 실패는 무해: 상태 전이는 이미 커밋됐다."""
    if not settings.fm_slack_webhook_url:
        return
    head = ":test_tube: [시뮬레이션] 지급 " + ("완료" if paid else "실패")
    text = (
        f"{head} · {_slack_escape(period_month)} · {amount:,}원 · {count}건"
        "\n:warning: 실제 이체 없음(데모용 스텁)"
    )
    if paid and reference:
        text += f" · 참조 {_slack_escape(reference)}"
    if not paid:
        text += f"\n사유: {_slack_escape(failure_reason or '-')}"
    await _post_slack(settings, text)
