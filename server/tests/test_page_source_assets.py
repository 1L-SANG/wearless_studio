from app.agents.page_source_assets import raw_image_provenance, select_source_asset


ASSETS = [
    {"id": "f", "slot": "Front"},
    {"id": "b", "slot": "Back"},
    {"id": "d", "slot": "Detail"},
    {"id": "l", "slot": "Logo"},
]


def test_detail_uses_exact_source_asset():
    block = {"source": "ai", "cutType": "product", "shot": "detail"}
    assert select_source_asset(block, ASSETS)["id"] == "d"


def test_source_detail_roles_use_matching_raw_assets():
    block = {"source": "ai", "cutType": "product", "contentRole": "logo"}
    assert select_source_asset(block, ASSETS)["id"] == "l"

    block = {"source": "ai", "cutType": "product", "shot": "fabric_macro"}
    assert select_source_asset(block, [{"asset_id": "fm", "slot": "Fabric Macro"}])["asset_id"] == "fm"


def test_product_overview_uses_exact_front_or_back_source():
    front = {"source": "ai", "cutType": "product", "shot": "ghost", "direction": "front"}
    back = {"source": "ai", "cutType": "product", "shot": "ghost", "direction": "back"}
    assert select_source_asset(front, ASSETS)["id"] == "f"
    assert select_source_asset(back, ASSETS)["id"] == "b"


def test_product_overview_never_uses_the_wrong_view():
    block = {"source": "ai", "cutType": "product", "shot": "ghost", "direction": "back"}
    assert select_source_asset(block, [{"id": "f", "slot": "Front"}]) is None


def test_cross_color_detail_is_never_reused_as_truth():
    block = {"source": "ai", "cutType": "product", "shot": "detail"}
    assert select_source_asset(block, ASSETS, color_transfer={"target": "red"}) is None


def test_styled_and_worn_images_remain_generated():
    for cut_type in ("styling", "horizon", "mirror", "background"):
        block = {"source": "ai", "cutType": cut_type, "direction": "front"}
        assert select_source_asset(block, ASSETS) is None


def test_raw_image_provenance_uses_stable_asset_url():
    block = {"id": "b1", "source": "ai", "cutType": "product", "shot": "detail"}
    assert raw_image_provenance(block, {"asset_id": "a1", "slot": "Detail"}) == {
        "blockId": "b1",
        "source": "raw_image",
        "assetId": "a1",
        "slot": "Detail",
        "imageUrl": "/v1/assets/a1/file",
    }
