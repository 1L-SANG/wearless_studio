"""모델 본인이 자기 얼굴 사용 내역을 본다. 셀러 신원은 안 보인다.

모델에게 필요한 건 '몇 번 쓰였나'와 '체인에 기록됐나'다. 어느 셀러가 썼는지는
계약상 필요 없고, 노출하면 셀러 영업정보가 모델에게 새는 것이다.
"""
from app.facemarket import UsageCard

FORBIDDEN = {"sellerId", "userId", "projectId", "r2Key", "imageSha256"}


def test_usage_card_hides_seller_identity():
    camel = {
        "".join(w if i == 0 else w.capitalize() for i, w in enumerate(n.split("_")))
        for n in UsageCard.model_fields
    }
    assert not (camel & FORBIDDEN)


def test_usage_card_whitelist():
    assert set(UsageCard.model_fields) == {
        "kind", "created_at", "image_hash_prefix", "chain_status"
    }
