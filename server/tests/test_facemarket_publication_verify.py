"""무인증 공개 검증 — 여기 실리는 값은 회수 불가다.

facemarket.py:1249 의 하드룰을 그대로 계승한다. 응답 모델에 선언된 필드가 전부이고,
SELECT 자체가 화이트리스트다. 이 테스트는 그 계약을 못박는다.
"""
import pytest

from app.facemarket_provenance import PublicationVerifyResult

FORBIDDEN = {
    "faceImageKey", "faceImageUri", "faceImageDigest", "ciHash", "ci",
    "birthDate", "birthYear", "displayName", "realName", "userId",
    "r2Key", "signedSha256", "sourceAssetIds", "modelId", "sellerId",
    "imageSha256",   # 전체 해시는 안 싣는다 — 앞 12자만
}


def test_response_model_has_no_forbidden_fields():
    declared = set(PublicationVerifyResult.model_fields)
    camel = {
        "".join(w if i == 0 else w.capitalize() for i, w in enumerate(n.split("_")))
        for n in declared
    }
    leaked = camel & FORBIDDEN
    assert not leaked, f"공개 검증 응답에 금지 필드가 있다: {leaked}"


def test_response_model_fields_are_exactly_the_whitelist():
    assert set(PublicationVerifyResult.model_fields) == {
        "valid", "status", "published_at", "image_hash_prefix", "kind",
        "allowed_use", "forbidden_use", "license_valid_until", "chain", "model",
    }
