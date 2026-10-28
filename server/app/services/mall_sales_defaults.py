"""Shared sales defaults, independent of any mall connection or old mall columns."""

from copy import deepcopy


_FIELDS = {
    "shipping": {"feeType", "baseFee", "jejuFee", "isolatedFee", "conditionalAmount"},
    "returns": {"totalFee", "partialFee", "exchangeFee"},
    "disclosure": {"country", "manufacturer", "asManager", "phone", "warrantyText"},
}
_SCALARS = {
    "taxType": ("tax_type", "TAX", {"TAX", "FREE"}),
    "productOrigin": ("product_origin", "domestic", {"domestic", "imported"}),
    "listingMode": ("listing_mode", "HIDDEN", {"HIDDEN", "ON_SALE"}),
}
_MAX_MONEY = 2_147_483_647


def normalize_sales_defaults(previous: dict, patch: dict) -> dict:
    """Merge an API patch into a database row and return database column names.

    Missing fields stay missing. Empty disclosure strings clear a field; null is
    invalid. Partial forms can be saved and missingItems reports their omissions.
    """
    if not isinstance(patch, dict) or patch.keys() - (_FIELDS.keys() | _SCALARS.keys() | {"shippingDays"}):
        raise ValueError("Unknown sales defaults field")
    values = {}
    for section, allowed in _FIELDS.items():
        changes = patch.get(section, {})
        if not isinstance(changes, dict) or changes.keys() - allowed:
            raise ValueError(f"Invalid {section} fields")
        merged = {**deepcopy(previous.get(section) or {}), **changes}
        for key, value in list(merged.items()):
            if section == "disclosure":
                if not isinstance(value, str):
                    raise ValueError(f"{key} must be text")
                if value.strip():
                    merged[key] = value.strip()
                else:
                    del merged[key]
            elif key == "feeType":
                if value not in ("FREE", "CHARGED", "CONDITIONAL_FREE"):
                    raise ValueError("Invalid shipping fee type")
            elif type(value) is not int or not (int(key == "conditionalAmount") <= value <= _MAX_MONEY):
                raise ValueError(f"{key} must be an integer amount in range")
        values[section] = merged
    shipping = values["shipping"]
    if shipping.get("feeType") == "FREE":
        shipping["baseFee"] = 0
    if shipping.get("feeType") != "CONDITIONAL_FREE":
        shipping.pop("conditionalAmount", None)
    for field, (column, default, allowed) in _SCALARS.items():
        value = patch.get(field, previous.get(column, default))
        if not isinstance(value, str) or value not in allowed:
            raise ValueError(f"Invalid {field}")
        values[column] = value
    days = patch.get("shippingDays", previous.get("shipping_days", 2))
    if type(days) is not int or not 1 <= days <= 30:
        raise ValueError("shippingDays must be an integer between 1 and 30")
    values["shipping_days"] = days
    return values


def sales_defaults_view(row: dict | None) -> dict:
    """Public defaults without connection identifiers or deprecated mall data."""
    row = row or {}
    values = normalize_sales_defaults(row, {})
    missing = []
    shipping = values["shipping"]
    required_shipping = {"baseFee", "jejuFee", "isolatedFee"}
    if shipping.get("feeType") == "CONDITIONAL_FREE":
        required_shipping.add("conditionalAmount")
    if not shipping.get("feeType") or not required_shipping <= shipping.keys():
        missing.append("shipping")
    if not _FIELDS["returns"] <= values["returns"].keys():
        missing.append("returns")
    if not _FIELDS["disclosure"] <= values["disclosure"].keys():
        missing.append("disclosure")
    return {
        "revision": row.get("revision", 0),
        **{section: values[section] for section in _FIELDS},
        **{field: values[column] for field, (column, _, _) in _SCALARS.items()},
        "shippingDays": values["shipping_days"],
        "missingItems": missing,
    }
