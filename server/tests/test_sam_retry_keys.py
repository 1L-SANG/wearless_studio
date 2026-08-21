"""세대 키 — 같은 키로 다시 걸면 끝난 잡에 합류만 하고 아무것도 안 돈다.

톤 마스크의 mask_job_key 와 같은 규칙을 sam_preprocess·matching_cutout 이 쓴다.
"""

from app.services import canonical_reference, matching_cutout


def _product():
    # ImageAsset 계약(mannequin.base_color_images): images 는 [{slot, id}] 리스트.
    return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "asset-front"},
                                                   {"slot": "Back", "id": "asset-back"}]}]}


def test_preprocess_key_is_stable_at_generation_zero():
    a = canonical_reference.preprocess_idempotency_key("proj", _product())
    b = canonical_reference.preprocess_idempotency_key("proj", _product(), retry=0)
    assert a is not None
    assert a == b
    assert not a.endswith(":r0")


def test_preprocess_key_changes_per_generation():
    base = canonical_reference.preprocess_idempotency_key("proj", _product())
    gen2 = canonical_reference.preprocess_idempotency_key("proj", _product(), retry=2)
    assert gen2 == f"{base}:r2"


def test_preprocess_key_is_still_none_without_photos():
    assert canonical_reference.preprocess_idempotency_key("proj", {}, retry=3) is None


def test_cutout_key_is_stable_at_generation_zero():
    key = matching_cutout.cutout_job_key("proj", "item")
    assert key == f"proj:matching_cutout:item:{matching_cutout.ALGORITHM_VERSION}"


def test_cutout_key_changes_per_generation():
    base = matching_cutout.cutout_job_key("proj", "item")
    assert matching_cutout.cutout_job_key("proj", "item", retry=1) == f"{base}:r1"
