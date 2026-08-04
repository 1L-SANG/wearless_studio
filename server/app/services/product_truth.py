"""Product Truth Package 순수 도메인 로직 (Phase 4).

Product Truth 는 생성 모델이 새로 상상하면 안 되는 상품 사실의 승인 revision 이다.
이 모듈은 DB/외부 API 에 의존하지 않는다. 라우트·repo·worker 는 여기서 만든 draft 와
fingerprint 를 저장하고, 승인된 revision 만 생성/QC 에 전달한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Mapping

SCHEMA_VERSION = "product_truth_v1"

TRUTH_ASSET_ROLES = (
    "FRONT",
    "BACK",
    "DETAIL",
    "FIT",
    "FABRIC_MACRO",
    "LOGO",
    "PRINT",
    "EMBROIDERY",
    "COLLAR",
    "SLEEVE",
    "CUFF",
    "BUTTON",
    "POCKET",
    "CARE_LABEL",
    "OTHER",
)

_SLOT_ROLE = {
    "front": "FRONT",
    "back": "BACK",
    "fit": "FIT",
}

_DETAIL_HINTS = (
    ("FABRIC_MACRO", ("fabric", "macro", "texture", "weave", "원단", "소재", "직조")),
    ("LOGO", ("logo", "brand", "로고", "브랜드")),
    ("PRINT", ("print", "graphic", "text", "프린트", "나염", "그래픽", "문구")),
    ("EMBROIDERY", ("embroidery", "embroidered", "자수")),
    ("COLLAR", ("collar", "카라", "깃")),
    ("SLEEVE", ("sleeve", "소매")),
    ("CUFF", ("cuff", "커프스")),
    ("BUTTON", ("button", "단추")),
    ("POCKET", ("pocket", "주머니", "포켓")),
    ("CARE_LABEL", ("care", "label", "tag", "세탁", "라벨", "택")),
)

_CHECK_WORDS = (
    "check",
    "checked",
    "checker",
    "gingham",
    "tartan",
    "plaid",
    "체크",
    "깅엄",
)
_STRIPE_WORDS = ("stripe", "striped", "pinstripe", "스트라이프", "줄무늬", "세로줄", "가로줄")
_LOGO_WORDS = ("logo", "brand", "로고", "브랜드")
_PRINT_WORDS = ("print", "graphic", "lettering", "프린트", "그래픽", "레터링", "나염")
_EMBROIDERY_WORDS = ("embroidery", "embroidered", "자수")
_BUTTON_WORDS = ("button", "buttons", "단추")
_POCKET_WORDS = ("pocket", "pockets", "포켓", "주머니")
_COLLAR_WORDS = ("collar", "카라", "깃")
_CUFF_WORDS = ("cuff", "커프스")
_COMPLEX_MATERIAL_WORDS = (
    "lace", "sheer", "transparent", "openwork", "sequins", "sequin",
    "레이스", "시스루", "투명", "망사", "스팽글",
)


class ProductTruthError(ValueError):
    """Product Truth 계약 위반. `code` 는 API 에서 그대로 error code 로 매핑 가능하다."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TruthValidationIssue:
    code: str
    severity: str  # error | warning
    message: str

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "message": self.message}


def _canon_default(value):
    # psycopg는 timestamptz를 datetime으로 돌려준다. Product Truth 승인 스냅샷은
    # DB row를 그대로 불변 복사하므로 날짜만 ISO 8601로 정규화하고, 모르는 타입은
    # 계속 실패시켜 계약 밖 값이 조용히 문자열로 눕지 않게 한다.
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _canon(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_canon_default,
    )


def _sha(value) -> str:
    return hashlib.sha256(_canon(value).encode("utf-8")).hexdigest()


def _flatten_text(*values) -> str:
    parts: list[str] = []

    def walk(v):
        if v is None:
            return
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, Mapping):
            for vv in v.values():
                walk(vv)
        elif isinstance(v, Iterable) and not isinstance(v, (bytes, bytearray)):
            for vv in v:
                walk(vv)
        else:
            parts.append(str(v))

    for value in values:
        walk(value)
    return " ".join(parts).lower()


def _has_any(text: str, words: Iterable[str]) -> bool:
    return any(w.lower() in text for w in words)


def _as_upper(value, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip()
    return re.sub(r"[^A-Z0-9_]+", "_", text.upper()).strip("_") or default


def _asset_map(asset_rows: Iterable[dict] | Mapping[str, dict] | None) -> dict[str, dict]:
    if asset_rows is None:
        return {}
    if isinstance(asset_rows, Mapping):
        return {str(k): dict(v or {}) for k, v in asset_rows.items()}
    out = {}
    for row in asset_rows:
        if row and row.get("id") is not None:
            out[str(row["id"])] = dict(row)
    return out


def truth_role_for_image(image: Mapping) -> str:
    """현재 슬롯/라벨/메타데이터를 Product Truth role 로 매핑한다."""
    explicit = image.get("truthRole") or image.get("truth_role") or image.get("role")
    if explicit:
        role = _as_upper(explicit)
        return role if role in TRUTH_ASSET_ROLES else "OTHER"
    slot = str(image.get("slot") or "").strip()
    base = _SLOT_ROLE.get(slot.lower())
    if base:
        return base
    text = _flatten_text(slot, image.get("label"), image.get("part"), image.get("purpose"), image.get("metadata"))
    for role, hints in _DETAIL_HINTS:
        if _has_any(text, hints):
            return role
    if slot.lower() == "detail":
        return "DETAIL"
    return "OTHER"


def source_assets(product: Mapping, asset_rows: Iterable[dict] | Mapping[str, dict] | None = None) -> list[dict]:
    """products.colors JSONB 에서 Product Truth Asset snapshot 을 만든다.

    checksum/width/height 는 assets row 가 있으면 그 값을 쓰고, 없으면 이미지 메타를 방어적으로
    읽는다. 원본 픽셀·URL 은 저장하지 않는다.
    """
    by_id = _asset_map(asset_rows)
    rows: list[dict] = []
    for color_pos, color in enumerate(product.get("colors") or []):
        color_id = color.get("id") or color.get("colorId")
        for image_pos, image in enumerate(color.get("images") or []):
            asset_id = image.get("id") or image.get("assetId") or image.get("asset_id")
            if not asset_id:
                continue
            asset_id = str(asset_id)
            meta = by_id.get(asset_id, {})
            role = truth_role_for_image(image)
            rows.append({
                "assetId": asset_id,
                "role": role,
                "view": image.get("slot"),
                "colorId": color_id,
                "part": image.get("part") or image.get("label"),
                "sortOrder": len(rows),
                "checksum": (
                    meta.get("checksum")
                    or meta.get("sha256")
                    or meta.get("content_sha256")
                    or image.get("checksum")
                    or image.get("sha256")
                ),
                "width": meta.get("width") or image.get("width"),
                "height": meta.get("height") or image.get("height"),
                "metadata": {
                    "colorIndex": color_pos,
                    "imageIndex": image_pos,
                    "swatchId": color.get("swatchId"),
                    "colorName": color.get("name"),
                    "mimeType": meta.get("mime_type") or meta.get("mimeType") or image.get("mimeType"),
                    "source": meta.get("source"),
                },
            })
    return rows


def source_fingerprint(product: Mapping, analysis: Mapping | None = None,
                       asset_rows: Iterable[dict] | Mapping[str, dict] | None = None) -> str:
    """상품 원본/분석/asset checksum 의 변경 감지 fingerprint.

    승인 Product Truth 가 이후 생성에 쓰일 때 이 값이 달라지면 stale 로 봐야 한다.
    """
    assets = source_assets(product, asset_rows)
    # 매칭 선택·마네킹 fitProfile·UI 중간 상태는 상품 사실이 아니다. 전부 넣으면 사용자가
    # 마네킹 체형만 조정해도 승인 truth가 즉시 stale이 된다. 구조/색/패턴/보호대상 facts만 봉인한다.
    truth_analysis_keys = {
        "subCategory", "customCategory", "fit", "materials", "materialPresetIndex",
        "sourceMirrored", "collarType", "sleeveType", "sleeveLengthClass", "cuffType",
        "buttonCount", "pocketCount", "hemType", "shapeComplexity", "secondaryColors",
        "repeatWidthPx", "repeatWidthMm", "stripeWidths", "colorSequence",
        "patternConfidence", "analysisConfidence", "logo", "textPrint", "graphicPrint",
        "embroidery",
    }
    relevant_analysis = {k: v for k, v in dict(analysis or {}).items()
                         if k in truth_analysis_keys}
    payload = {
        "product": {
            "id": product.get("id"),
            "projectId": product.get("projectId") or product.get("project_id"),
            "name": product.get("name"),
            "clothingType": product.get("clothingType") or product.get("clothing_type"),
            "colors": [
                {
                    "id": c.get("id") or c.get("colorId"),
                    "name": c.get("name"),
                    "swatchId": c.get("swatchId"),
                    "isBase": bool(c.get("isBase")),
                    "images": [
                        {
                            "id": i.get("id") or i.get("assetId") or i.get("asset_id"),
                            "slot": i.get("slot"),
                            "label": i.get("label"),
                            "truthRole": i.get("truthRole") or i.get("truth_role") or i.get("role"),
                            "part": i.get("part"),
                        }
                        for i in (c.get("images") or [])
                    ],
                }
                for c in (product.get("colors") or [])
            ],
            "measurements": product.get("measurements") or [],
            "measurementsUnknown": product.get("measurementsUnknown")
            if "measurementsUnknown" in product else product.get("measurements_unknown"),
        },
        "analysis": relevant_analysis,
        "assetEvidence": [
            {
                "assetId": a["assetId"],
                "role": a["role"],
                "view": a.get("view"),
                "checksum": a.get("checksum"),
                "width": a.get("width"),
                "height": a.get("height"),
            }
            for a in assets
        ],
    }
    return _sha(payload)


def assert_source_assets_current(
    truth: Mapping,
    product: Mapping,
    asset_rows: Iterable[dict] | Mapping[str, dict] | None = None,
) -> None:
    """승인 직전 draft와 현재 상품이 같은 원본 자산을 가리키는지 검증한다.

    분석 완료 뒤 프론트가 상품명·실측·분석 필드를 저장하는 것은 원본 교체가 아니다. 이
    동기화 때문에 draft fingerprint가 달라질 수 있으므로 승인 시 fingerprint는 새로 봉인할
    수 있다. 단 asset id·역할·view·checksum·크기가 하나라도 달라졌다면 오래된 draft를 새
    원본에 승인하면 안 되므로 ``truth_stale``로 막는다.
    """

    def signature(items: Iterable[Mapping]) -> list[tuple]:
        out = []
        for item in items or []:
            asset_id = item.get("assetId") or item.get("asset_id")
            out.append((
                str(asset_id or ""),
                str(item.get("role") or "").strip().upper(),
                str(item.get("view") or "").strip().upper(),
                item.get("checksum"),
                item.get("width"),
                item.get("height"),
            ))
        return sorted(out)

    sealed = truth.get("sourceAssets") or truth.get("source_assets") or []
    current = source_assets(product, asset_rows)
    if signature(sealed) != signature(current):
        raise ProductTruthError(
            "truth_stale",
            "상품 원본 이미지가 바뀌어 Product Truth를 다시 생성하고 승인해야 합니다.",
        )


def garment_spec(product: Mapping, analysis: Mapping | None = None) -> dict:
    analysis = analysis or {}
    text = _flatten_text(product, analysis)
    structure = []
    if _has_any(text, _COLLAR_WORDS):
        structure.append("COLLAR")
    if _has_any(text, _BUTTON_WORDS):
        structure.append("BUTTONS")
    if _has_any(text, _CUFF_WORDS):
        structure.append("CUFFS")
    if _has_any(text, _POCKET_WORDS):
        structure.append("POCKET")
    material_traits = []
    for value in list(analysis.get("materialTraits") or []) + list(analysis.get("materials") or []):
        normalized = str(value or "").strip().lower()
        if normalized and normalized not in material_traits:
            material_traits.append(normalized)
    for keyword in _COMPLEX_MATERIAL_WORDS:
        if keyword in text and keyword not in material_traits:
            material_traits.append(keyword)
    return {
        "category": _as_upper(product.get("clothingType") or product.get("clothing_type")),
        "subcategory": _as_upper(analysis.get("subCategory") or analysis.get("customCategory"), "UNKNOWN"),
        "fit": _as_upper(analysis.get("fit"), "UNKNOWN"),
        "shapeComplexity": analysis.get("shapeComplexity") or ("MEDIUM" if structure else "LOW"),
        "collarType": analysis.get("collarType"),
        "sleeveType": analysis.get("sleeveType"),
        "sleeveLengthClass": analysis.get("sleeveLengthClass"),
        "cuffType": analysis.get("cuffType"),
        "buttonCount": analysis.get("buttonCount"),
        "pocketCount": analysis.get("pocketCount"),
        "hemType": analysis.get("hemType"),
        "structureFlags": structure,
        "materialTraits": material_traits,
    }


def color_spec(product: Mapping, analysis: Mapping | None = None) -> dict:
    base_colors = []
    for color in product.get("colors") or []:
        if color.get("isBase") or not base_colors:
            base_colors.append({
                "name": color.get("name") or color.get("swatchId") or "unknown",
                "swatchId": color.get("swatchId"),
                "lab": color.get("lab"),
                "confidence": color.get("confidence"),
            })
    return {
        "colorSpace": "Lab",
        "baseColors": base_colors[:3],
        "secondaryColors": list((analysis or {}).get("secondaryColors") or []),
        "whiteBalanceReferenceAssetId": None,
    }


def pattern_spec(product: Mapping, analysis: Mapping | None = None) -> dict:
    text = _flatten_text(product, analysis)
    if _has_any(text, _CHECK_WORDS):
        kind = "CHECK"
        direction = "BIDIRECTIONAL"
    elif _has_any(text, _STRIPE_WORDS):
        kind = "STRIPE"
        direction = "VERTICAL" if ("vertical" in text or "세로" in text) else "UNKNOWN"
    elif "solid" in text or "plain" in text or "무지" in text:
        kind = "SOLID"
        direction = "NONE"
    else:
        kind = "UNKNOWN"
        direction = "UNKNOWN"
    fine = kind in {"CHECK", "STRIPE"} and any(
        w in text for w in ("fine", "pin", "thin", "small", "잔", "얇", "촘촘")
    )
    return {
        "type": kind,
        "direction": direction,
        "repeatWidthPx": analysis.get("repeatWidthPx") if analysis else None,
        "repeatWidthMm": analysis.get("repeatWidthMm") if analysis else None,
        "stripeWidths": list((analysis or {}).get("stripeWidths") or []),
        "colorSequence": list((analysis or {}).get("colorSequence") or []),
        "finePattern": bool(fine),
        "confidence": (analysis or {}).get("patternConfidence"),
    }


def protected_details(product: Mapping, analysis: Mapping | None = None) -> dict:
    text = _flatten_text(product, analysis)
    pat = pattern_spec(product, analysis)["type"]
    return {
        "logo": bool((analysis or {}).get("logo")) or _has_any(text, _LOGO_WORDS),
        "textPrint": bool((analysis or {}).get("textPrint")) or _has_any(text, ("lettering", "text", "레터링", "문구")),
        "graphicPrint": bool((analysis or {}).get("graphicPrint")) or _has_any(text, _PRINT_WORDS),
        "embroidery": bool((analysis or {}).get("embroidery")) or _has_any(text, _EMBROIDERY_WORDS),
        "pattern": pat in {"CHECK", "STRIPE"},
        "buttonCount": (
            isinstance((analysis or {}).get("buttonCount"), int)
            and not isinstance((analysis or {}).get("buttonCount"), bool)
            and (analysis or {}).get("buttonCount") > 0
        ) or _has_any(text, _BUTTON_WORDS),
        "pocketCount": (
            isinstance((analysis or {}).get("pocketCount"), int)
            and not isinstance((analysis or {}).get("pocketCount"), bool)
            and (analysis or {}).get("pocketCount") > 0
        ) or _has_any(text, _POCKET_WORDS),
    }


def build_truth_draft(product: Mapping, analysis: Mapping | None = None,
                      asset_rows: Iterable[dict] | Mapping[str, dict] | None = None,
                      *, version: int = 1) -> dict:
    assets = source_assets(product, asset_rows)
    truth = {
        "projectId": product.get("projectId") or product.get("project_id"),
        "productId": product.get("id"),
        "version": int(version),
        "status": "draft",
        "schemaVersion": SCHEMA_VERSION,
        "garmentSpec": garment_spec(product, analysis),
        "colorSpec": color_spec(product, analysis),
        "patternSpec": pattern_spec(product, analysis),
        "protectedDetails": protected_details(product, analysis),
        "analysisConfidence": (analysis or {}).get("analysisConfidence"),
        "sourceFingerprint": source_fingerprint(product, analysis, asset_rows),
        "sourceAssets": assets,
        "sourceEvidence": {
            "assetCount": len(assets),
            "roles": sorted({a["role"] for a in assets}),
        },
        "uncertainFields": [],
    }
    return truth


def garment_profile(truth: Mapping) -> dict:
    garment = truth.get("garmentSpec") or {}
    pattern = truth.get("patternSpec") or {}
    protected = truth.get("protectedDetails") or {}
    protected_list = [
        name.upper()
        for name, enabled in protected.items()
        if enabled and name in {"pattern", "buttonCount", "pocketCount", "logo", "textPrint", "graphicPrint", "embroidery"}
    ]
    risk_flags = []
    if pattern.get("finePattern"):
        risk_flags.append("FINE_PATTERN")
    if pattern.get("type") in {"CHECK", "STRIPE"}:
        risk_flags.append(f"PATTERN_{pattern.get('type')}")
    for key in ("logo", "textPrint", "graphicPrint", "embroidery"):
        if protected.get(key):
            risk_flags.append(_as_upper(key))
    material_risk = "HIGH" if any(k in risk_flags for k in ("FINE_PATTERN", "EMBROIDERY")) else "NORMAL"
    return {
        "category": garment.get("category") or "UNKNOWN",
        "subcategory": garment.get("subcategory") or "UNKNOWN",
        "shapeComplexity": garment.get("shapeComplexity") or "UNKNOWN",
        "patternType": pattern.get("type") or "UNKNOWN",
        "materialRisk": material_risk,
        "structureFlags": list(garment.get("structureFlags") or []),
        "protectedDetails": protected_list,
        "riskFlags": risk_flags,
        "analysisConfidence": truth.get("analysisConfidence"),
    }


def validation_issues(truth: Mapping) -> list[TruthValidationIssue]:
    roles = {a.get("role") for a in (truth.get("sourceAssets") or [])}
    issues: list[TruthValidationIssue] = []
    garment = truth.get("garmentSpec") or {}
    for field, maximum, label in (("buttonCount", 30, "단추"), ("pocketCount", 12, "주머니")):
        value = garment.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum
        ):
            issues.append(TruthValidationIssue(
                f"invalid_{field}", "error", f"{label} 수는 0~{maximum} 사이 정수로 입력해 주세요."))
    if "FRONT" not in roles:
        issues.append(TruthValidationIssue(
            "missing_front_asset", "error", "기준 정면 원본이 없어 Product Truth 를 승인할 수 없습니다."))
    pattern = (truth.get("patternSpec") or {}).get("type")
    if pattern in {"CHECK", "STRIPE"} and not (roles & {"DETAIL", "FABRIC_MACRO"}):
        issues.append(TruthValidationIssue(
            "missing_pattern_evidence", "error", "체크/스트라이프 상품은 Detail 또는 FabricMacro 원본이 필요합니다."))
    protected = truth.get("protectedDetails") or {}
    if (protected.get("logo") or protected.get("graphicPrint") or protected.get("textPrint") or protected.get("embroidery")) \
            and not (roles & {"DETAIL", "LOGO", "PRINT", "EMBROIDERY"}):
        issues.append(TruthValidationIssue(
            "missing_protected_detail_evidence", "error", "로고/프린팅/자수 보호 대상은 원본 Detail 근거가 필요합니다."))
    if "BACK" not in roles:
        issues.append(TruthValidationIssue(
            "missing_back_asset", "warning", "후면 원본이 없어 후면 구조 검증 신뢰도가 낮습니다."))
    return issues


def can_approve(truth: Mapping) -> bool:
    return not any(i.severity == "error" for i in validation_issues(truth))


def approve_snapshot(truth: Mapping, *, actor_id: str | None = None) -> dict:
    """승인 시 저장할 immutable payload. draft 를 직접 mutate 하지 않는다."""
    issues = validation_issues(truth)
    if any(i.severity == "error" for i in issues):
        raise ProductTruthError("truth_not_approvable", "; ".join(i.code for i in issues if i.severity == "error"))
    out = json.loads(_canon(truth))
    out["status"] = "approved"
    out["approvedBy"] = actor_id
    out["validationIssues"] = [i.as_dict() for i in issues]
    out["garmentProfile"] = garment_profile(out)
    return out


def assert_approved_for_generation(truth: Mapping | None, *, current_fingerprint: str | None = None) -> None:
    if not truth:
        raise ProductTruthError("approved_truth_required", "승인된 Product Truth 가 필요합니다.")
    if truth.get("status") != "approved":
        raise ProductTruthError("approved_truth_required", "draft/rejected truth 는 생성 입력으로 쓸 수 없습니다.")
    if current_fingerprint and truth.get("sourceFingerprint") != current_fingerprint:
        raise ProductTruthError("truth_stale", "상품 원본 또는 분석이 바뀌어 Product Truth 재승인이 필요합니다.")
