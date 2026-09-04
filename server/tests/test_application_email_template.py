"""지원서 결과 메일 템플릿 — 이메일 클라이언트 제약과 PII 규칙의 회귀 가드.

되돌아가면: (1) Gmail·아웃룩이 <style> 을 지우거나 flex 를 못 써서 레이아웃이 무너지고,
(2) 텍스트 파트가 없으면 스팸 점수가 올라가며, (3) 이름·생년월일이 본문에 들어가면
오타난 미검증 이메일 주소로 제3자에게 심사 결과와 PII 가 함께 날아간다(설계 E4).
"""
import re

from app import facemarket_notify as notify

TYPES = ("approved", "rejected", "auto_rejected")
BASE = "https://facemarket.wearless.kr"


def _content(email_type, reason=None):
    return notify._email_content(email_type, public_base=BASE, reject_reason=reason)


def test_every_type_returns_subject_html_and_text():
    """텍스트 파트 없이 HTML 만 보내면 스팸 필터가 점수를 올린다."""
    for email_type in TYPES:
        subject, html, text = _content(email_type)
        assert subject.startswith("[FaceMarket]"), email_type
        assert html.strip().startswith("<"), email_type
        assert text.strip(), f"{email_type}: 텍스트 파트가 비었다"
        assert "<" not in text, f"{email_type}: 텍스트 파트에 태그가 섞였다"


def test_layout_is_table_based_with_inline_styles():
    """Gmail 은 <style> 블록을, 아웃룩은 flex/grid 를 신뢰할 수 없다."""
    for email_type in TYPES:
        _subject, html, _text = _content(email_type)
        assert "<table" in html, email_type
        assert "style=" in html, email_type
        assert "<style" not in html, f"{email_type}: <style> 블록은 지워질 수 있다"
        assert "display:flex" not in html.replace(" ", ""), email_type


def test_colors_are_explicit_so_dark_mode_cannot_invert_into_unreadable():
    for email_type in TYPES:
        _subject, html, _text = _content(email_type)
        assert "#ffffff" in html.lower(), email_type
        assert "#0e0d14" in html.lower(), email_type


def test_logo_is_a_retina_png_with_alt_text_and_absolute_url():
    """이미지를 막아 두고 여는 사람이 많다 — alt 가 없으면 머리가 빈 칸이 된다.
    SVG 로 되돌리면 Gmail 등에서 로고가 통째로 사라진다."""
    for email_type in TYPES:
        _subject, html, _text = _content(email_type)
        img = re.search(r"<img[^>]*>", html)
        assert img, email_type
        assert 'alt="FaceMarket"' in img.group(0), email_type
        assert ".svg" not in img.group(0), f"{email_type}: 메일 로고는 SVG 를 쓸 수 없다"
        assert f'src="{BASE}/assets/brand/facemarket-logo@2x.png"' in img.group(0), email_type


def test_cta_is_a_button_and_the_url_also_appears_as_text():
    """버튼이 안 눌리는 클라이언트(이미지·스타일 차단)에서도 주소로 갈 수 있어야 한다."""
    _subject, html, text = _content("approved")
    assert f'href="{BASE}/status"' in html
    assert "padding" in html
    assert f"{BASE}/status" in text


def test_rejected_carries_the_reason_escaped():
    _subject, html, text = _content("rejected", reason='사진 <b>부족</b> & 흐림')
    assert "&lt;b&gt;" in html, "거절 사유가 이스케이프되지 않았다"
    assert "<b>부족</b>" not in html
    assert "사진 <b>부족</b> & 흐림" in text, "텍스트 파트는 원문 그대로"


def test_rejected_without_reason_has_no_empty_reason_block():
    _subject, html, _text = _content("rejected", reason=None)
    assert "사유:" not in html


def test_no_identity_fields_reach_the_body():
    """E4 — 지원서 이메일은 미검증이라 오타 시 제3자에게 간다."""
    for email_type in TYPES:
        _subject, html, text = _content(email_type)
        for banned in ("생년월일", "birthdate", "applicant_name", "이름:"):
            assert banned not in html, f"{email_type}/{banned}"
            assert banned not in text, f"{email_type}/{banned}"


def test_send_passes_both_parts_to_resend(monkeypatch):
    """HTML 만 보내던 시절로 돌아가면 텍스트 파트가 조용히 사라진다."""
    import asyncio
    import types

    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "msg-1"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, _url, headers=None, json=None):
            captured.update(json or {})
            return FakeResponse()

    monkeypatch.setattr(notify.httpx, "AsyncClient", lambda **_: FakeClient())
    settings = types.SimpleNamespace(
        resend_api_key="key",
        fm_application_from_email="FaceMarket <noreply@wearless.kr>",
        fm_application_public_base=BASE,
    )
    ok, message_id, error = asyncio.run(
        notify.send_application_email(settings, to="a@example.com", email_type="approved")
    )
    assert (ok, message_id, error) == (True, "msg-1", None)
    assert captured["html"].strip().startswith("<")
    assert captured["text"].strip()
    assert captured["to"] == ["a@example.com"]
