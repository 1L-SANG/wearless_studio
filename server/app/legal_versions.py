"""셀러 법무 문서의 발행 버전과 동의 게이트 적용 시점.

`public/legal/manifest.json`(발행 스크립트 tools/legal_publish.py 산출물)의 version 과
같아야 한다. 서버는 프론트 public 디렉터리를 갖지 않으므로 여기 상수로 둔다.
tests/test_seller_consents.py 가 발행본과 버전 일치를 검사한다. 개정본은 미리 공개할 수
있지만 기존 셀러의 재동의는 문서 시행일부터 요구한다.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

# 공개된 최신 개정본. 신규 회원은 이 버전에 동의한다.
SELLER_TERMS_VERSION = "v1.2"
SELLER_PRIVACY_VERSION = "v1.1"
PREVIOUS_SELLER_TERMS_VERSION = "v1.1"
SELLER_TERMS_EFFECTIVE_DATE = date(2026, 9, 29)
_SEOUL = ZoneInfo("Asia/Seoul")

# 선택 의류 협찬 동의(04 동의서 E-2). 2026-09-23 시행. required_versions()나 등록 게이트에 연결하지 않아요
# (선택 동의라 등록 필수가 아니에요). E-2d 배송 동의 2종은 요청 기능이 열릴 때 쓰기 시작해요.
SPONSORSHIP_CONSENT_VERSION = "2026-09-sponsorship-v1"
SPONSORSHIP_CONSENTS = {
    "sponsorship_participation": {
        "section": "E-2a", "required_for_enrollment": False,
        "version": SPONSORSHIP_CONSENT_VERSION,
    },
    "sponsorship_profile_collection": {
        "section": "E-2b", "required_for_enrollment": False,
        "version": SPONSORSHIP_CONSENT_VERSION,
    },
    "sponsorship_shipping_collection": {
        "section": "E-2d-1", "required_for_enrollment": False,
        "version": SPONSORSHIP_CONSENT_VERSION,
    },
    "sponsorship_shipping_disclosure": {
        "section": "E-2d-2", "required_for_enrollment": False,
        "version": SPONSORSHIP_CONSENT_VERSION,
    },
}
# 협찬 개정 시행본(2026-09-23). tests/test_facemarket_sponsorship_legal_draft.py 가 manifest 와 대조해요.
SPONSORSHIP_DOCUMENT_VERSIONS = {
    "terms-model": "v1.2", "privacy-model": "v1.6", "answers": "v1.3",
    "sponsorship-consent": SPONSORSHIP_CONSENT_VERSION,
}
# 요청·배송 기능용 델타는 아직 초안(요청 기능 개시 때 승격).
DRAFT_SPONSORSHIP_DOCUMENT_VERSIONS = {
    "license-agreement-sponsorship-draft": "v3-draft",
    "seller-license-terms-sponsorship-draft": "v3-draft",
}


def required_versions() -> dict[str, str]:
    return {"terms": SELLER_TERMS_VERSION, "privacy": SELLER_PRIVACY_VERSION}


def terms_consent_required(accepted_version: str | None, *, today: date | None = None) -> bool:
    """기존 v1.1 회원은 공지기간 동안 유지하고 시행일부터 v1.2 재동의를 받는다."""
    if accepted_version == SELLER_TERMS_VERSION:
        return False
    if accepted_version != PREVIOUS_SELLER_TERMS_VERSION:
        return True
    current = today or datetime.now(_SEOUL).date()
    return current >= SELLER_TERMS_EFFECTIVE_DATE
