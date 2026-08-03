"""상세페이지 블록을 원본 자산으로 채울 수 있는지 결정하는 순수 정책.

상품의 원단·부자재·로고처럼 "있는 그대로" 보여줘야 하는 디테일 컷만 원본을
재사용한다. 착용·라이프스타일·배경·타색 전환은 생성 의미가 있으므로 passthrough
하지 않는다.
"""

from __future__ import annotations


_DETAIL_ROLES = {
    "detail",
    "fabric",
    "fabric_macro",
    "fabricmacro",
    "texture",
    "material",
    "logo",
    "collar",
    "sleeve",
    "button",
    "buttons",
    "pocket",
    "pockets",
}
_DETAIL_SLOTS = {
    "detail",
    "fabric",
    "fabric_macro",
    "fabricmacro",
    "texture",
    "material",
    "logo",
    "collar",
    "sleeve",
    "button",
    "buttons",
    "pocket",
    "pockets",
}
def _norm(value) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _slot(asset: dict) -> str:
    return _norm(asset.get("slot"))


def _asset_id(asset: dict) -> str | None:
    value = asset.get("id") or asset.get("asset_id")
    return str(value) if value else None


def _requests_source_asset(block: dict) -> bool:
    if block.get("cutType") != "product":
        return False
    if _norm(block.get("shot")) == "ghost":
        return _norm(block.get("direction")) in {"front", "back"}
    fields = (
        block.get("shot"),
        block.get("contentRole"),
        block.get("sectionRole"),
        block.get("detailType"),
        block.get("detail_type"),
    )
    return any(_norm(value) in _DETAIL_ROLES for value in fields)


def _requested_roles(block: dict) -> list[str]:
    roles = []
    for value in (
        block.get("shot"),
        block.get("contentRole"),
        block.get("sectionRole"),
        block.get("detailType"),
        block.get("detail_type"),
    ):
        role = _norm(value)
        if role in _DETAIL_ROLES and role not in roles:
            roles.append(role)
    return roles


def select_source_asset(
    block: dict,
    assets: list[dict],
    *,
    color_transfer: dict | None = None,
) -> dict | None:
    if block.get("source") != "ai" or not _requests_source_asset(block):
        return None
    # 다른 색상의 Detail을 목표색처럼 쓰면 Product Truth를 오염시킨다.
    if color_transfer is not None:
        return None
    ordered = [asset for asset in assets if _asset_id(asset)]
    if _norm(block.get("shot")) == "ghost":
        requested_view = _norm(block.get("direction"))
        return next((asset for asset in ordered if _slot(asset) == requested_view), None)
    requested = _requested_roles(block)
    exact = next(
        (asset for asset in ordered if _slot(asset) in requested and _slot(asset) in _DETAIL_SLOTS),
        None,
    )
    if exact is not None:
        return exact
    return next((asset for asset in ordered if _slot(asset) in _DETAIL_SLOTS), None)


def raw_image_provenance(block: dict, asset: dict) -> dict:
    """Metadata record for a source image reused without AI generation."""
    return {
        "blockId": block.get("id"),
        "source": "raw_image",
        "assetId": _asset_id(asset),
        "slot": asset.get("slot"),
        "imageUrl": f"/v1/assets/{_asset_id(asset)}/file",
    }
