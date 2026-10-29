"""Per-mall registration defaults and their public field allowlists."""

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


def normalize_mall_defaults(mall: str, previous: dict, patch: dict) -> dict:
    """Merge an API patch into a database row and return database column names.

    Missing fields stay missing. Empty disclosure strings clear a field; null is
    invalid. Partial forms can be saved and missingItems reports their omissions.
    """
    allowed = mall_default_fields(mall)
    if not isinstance(patch, dict) or patch.keys() - allowed:
        raise ValueError("Unknown mall defaults field")
    values = {}
    for section, fields in _FIELDS.items():
        if section not in allowed:
            continue
        fields = {"country"} if mall == "cafe24" else fields
        changes = patch.get(section, {})
        if not isinstance(changes, dict) or changes.keys() - fields:
            raise ValueError(f"Invalid {section} fields")
        merged = {**deepcopy({k: v for k, v in (previous.get(section) or {}).items() if k in fields}), **changes}
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
    shipping = values.get("shipping", {})
    if shipping.get("feeType") == "FREE":
        shipping["baseFee"] = 0
    if shipping.get("feeType") != "CONDITIONAL_FREE":
        shipping.pop("conditionalAmount", None)
    for field, (column, default, choices) in _SCALARS.items():
        if field not in allowed:
            continue
        value = patch.get(field, previous.get(column, default))
        if not isinstance(value, str) or value not in choices:
            raise ValueError(f"Invalid {field}")
        values[column] = value
    if mall != "zigzag":
        return values
    addresses = {**(previous.get("addresses") or {}), **patch.get("addresses", {})} if isinstance(patch.get("addresses", {}), dict) else None
    if not isinstance(addresses, dict) or addresses.keys() - {"returnAddressId"} or any(not isinstance(v, str) for v in addresses.values()):
        raise ValueError("Invalid return address")
    values["addresses"] = {k: v.strip() for k, v in addresses.items() if v.strip()}
    days = patch.get("shippingDays", previous.get("shipping_days", 2))
    if type(days) is not int or not 1 <= days <= 30:
        raise ValueError("shippingDays must be an integer between 1 and 30")
    values["shipping_days"] = days
    return values


def mall_default_fields(mall: str) -> set[str]:
    if mall == "cafe24":
        return {"taxType", "disclosure", "listingMode"}
    if mall == "zigzag":
        return set(_FIELDS) | set(_SCALARS) | {"shippingDays", "addresses"}
    if mall == "ably":
        return set()
    raise ValueError("Unknown mall")


def mall_defaults_view(mall: str, row: dict | None) -> dict:
    row = row or {}
    values = normalize_mall_defaults(mall, row, {})
    missing = []
    if mall == "zigzag":
        shipping = values["shipping"]
        for key in ("feeType", "baseFee", "jejuFee", "isolatedFee", *(["conditionalAmount"] if shipping.get("feeType") == "CONDITIONAL_FREE" else [])):
            if key not in shipping:
                missing.append("shipping." + key)
        for section in ("returns", "disclosure"):
            missing.extend(section + "." + key for key in sorted(_FIELDS[section]) if key not in values[section])
        if not values["addresses"].get("returnAddressId"):
            missing.append("addresses.returnAddressId")
    elif mall == "cafe24" and not values["disclosure"].get("country"):
        missing.append("disclosure.country")
    return {
        "revision": row.get("revision", 0),
        **{section: values[section] for section in _FIELDS if section in values},
        **{field: values[column] for field, (column, _, _) in _SCALARS.items() if column in values},
        **({"shippingDays": values["shipping_days"], "addresses": values["addresses"]} if mall == "zigzag" else {}),
        "missingItems": missing,
    }
