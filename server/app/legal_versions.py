"""셀러 법무 문서의 현재 버전 — 동의 게이트의 기준값.

`public/legal/manifest.json`(발행 스크립트 tools/legal_publish.py 산출물)의 version 과
같아야 한다. 서버는 프론트 public 디렉터리를 갖지 않으므로 여기 상수로 둔다.
tests/test_seller_consents.py 가 두 값의 일치를 검사한다 — 문서를 개정해 발행하면
여기도 함께 올려야 하고, 그러면 기존 셀러에게 재동의 게이트가 한 번 뜬다.
"""

SELLER_TERMS_VERSION = "v1.0"
SELLER_PRIVACY_VERSION = "v1.0"


def required_versions() -> dict[str, str]:
    return {"terms": SELLER_TERMS_VERSION, "privacy": SELLER_PRIVACY_VERSION}
