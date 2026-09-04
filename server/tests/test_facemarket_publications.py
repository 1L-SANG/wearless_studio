"""층②·① 배포본 공증 라우트.

핵심 계약 3개:
  1. uploadToken 없이는 임의 R2 키를 서명 대상으로 못 민다.
  2. 같은 (셀러, 해시) 는 몇 번 sign 해도 원장 1행이고 publicationId 가 같다.
  3. C2PA 서명 실패는 다운로드를 막지 않는다(원본 반환 + c2paStatus='failed').
"""
import hashlib
import time

import pytest

from app import facemarket_provenance as fp


SECRET = "test-secret"


def token(**over):
    kw = dict(
        seller_id="u1", key="publications/u1/abc/upload",
        project_id="p1", kind="long_png", expires_at=time.time() + 300,
    )
    kw.update(over)
    return fp.make_upload_token(SECRET, **kw)


def test_upload_token_roundtrip():
    parsed = fp.parse_upload_token(SECRET, token())
    assert parsed["seller_id"] == "u1"
    assert parsed["key"] == "publications/u1/abc/upload"
    assert parsed["project_id"] == "p1"
    assert parsed["kind"] == "long_png"


def test_upload_token_rejects_tamper():
    # 원 브리핑의 `.replace("u1", "u2", 1)` 은 base64 인코딩된 페이로드 안에서 리터럴 "u1"
    # 부분 문자열이 살아남는다고 가정했는데, 실측(2000회 샘플, 0건 일치)해 보니 이 특정
    # 페이로드 구조에서는 "u1" 이 인코딩 결과에 전혀 나타나지 않아 replace 가 항상 no-op
    # 이었다 — 변조되지 않은 유효한 토큰을 그대로 다시 검증하는 셈이라 통과 중인
    # 구현에서도 예외가 안 난다. mac 마지막 문자를 직접 뒤집어 실제로 서명을 깨뜨린다.
    body, mac = token().split(".", 1)
    flipped = "0" if mac[-1] != "0" else "1"
    tampered = f"{body}.{mac[:-1]}{flipped}"
    with pytest.raises(fp.TokenInvalid):
        fp.parse_upload_token(SECRET, tampered)


def test_upload_token_rejects_expired():
    with pytest.raises(fp.TokenInvalid):
        fp.parse_upload_token(SECRET, token(expires_at=time.time() - 1))


def test_upload_token_rejects_foreign_secret():
    with pytest.raises(fp.TokenInvalid):
        fp.parse_upload_token("other-secret", token())


class FakeSigner:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def sign(self, data, mime, manifest):
        self.calls += 1
        if self.fail:
            raise RuntimeError("signing blew up")
        return data + b"-SIGNED"


def test_sign_bytes_returns_original_on_failure():
    """서명 실패가 셀러의 결과물을 인질로 잡지 않는다."""
    data = b"png-bytes"
    out, status = fp.sign_bytes(FakeSigner(fail=True), data, "image/png", {})
    assert out == data
    assert status == "failed"


def test_sign_bytes_returns_signed_on_success():
    data = b"png-bytes"
    out, status = fp.sign_bytes(FakeSigner(), data, "image/png", {})
    assert out == data + b"-SIGNED"
    assert status == "signed"


def test_sign_bytes_skips_when_signer_missing():
    data = b"png-bytes"
    out, status = fp.sign_bytes(None, data, "image/png", {})
    assert out == data
    assert status == "skipped"


def test_routes_absent_when_flag_off(make_token):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from conftest import make_settings, auth_headers

    app = create_app(make_settings(facemarket_enabled=True, fm_provenance_enabled=False))
    with TestClient(app) as client:
        r = client.post(
            "/v1/facemarket/publications/presign",
            json={"projectId": "p1", "kind": "long_png", "byteSize": 10},
            headers=auth_headers(make_token),
        )
    assert r.status_code == 404
