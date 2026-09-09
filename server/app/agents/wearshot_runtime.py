"""Server-owned v2 binding and shared final-candidate release enforcement."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from .. import repo

from . import cut_identity_review, wearshot_qc
from .cut_output_qc import LabeledReference
from .wearshot_contract import WearshotContract, RepairPlan, image_sha256, make_repair_plan
from .wearshot_contract import BoundReference, GarmentBinding, EssentialDetail, FrameLock, bind_contract


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def selection_fingerprint(project, product, analysis, storyboard):
    from . import cut_generator
    clothing = product.get("clothing_type") or product.get("clothingType") or "top"
    blocks = [{"id": b.get("id"), "spec": cut_generator.normalize_spec(b, clothing_type=clothing)}
              for b in storyboard if isinstance(b, dict) and b.get("source") == "ai"]
    return _digest({"selectedMannequin": project.get("selected_mannequin_id") or project.get("selectedMannequinId"),
        "product": {k: product.get(k) for k in ("clothing_type", "clothingType", "colors")},
        "analysis": {k: analysis.get(k) for k in ("selectedModelId", "selected_model_id", "stylingModelId", "styling_model_id",
            "brandUseCategory", "fitProfile", "confirmedGptProductEvidence")}, "blocks": blocks})


def _asset_projection(asset):
    if not isinstance(asset, dict):
        raise ValueError("wearshot_v2_owned_mannequin_required")
    fields = ("id", "project_id", "r2_key", "mime_type", "metadata", "source_asset_id", "source_cut_id",
              "source_r2_key", "source_mime_type", "source_metadata")
    if any(not asset.get(k) for k in fields if k not in {"metadata", "source_metadata"}):
        raise ValueError("wearshot_v2_lineage_unproven")
    metadata = asset.get("metadata") or {}
    if asset["id"] != asset["source_asset_id"] and (
        metadata.get("type") != "mannequinToneAdjusted" or metadata.get("sourceAssetId") != asset["source_asset_id"]
        or metadata.get("sourceCutId") != asset["source_cut_id"] or not metadata.get("sourceHash")
    ):
        raise ValueError("wearshot_v2_tone_lineage_mismatch")
    return deepcopy({k: asset.get(k) for k in fields})


def _matching_ids(storyboard, clothing):
    from . import cut_generator
    result = set()
    for block in storyboard:
        if isinstance(block, dict) and block.get("source") == "ai" and block.get("cutType") in {"styling", "horizon", "mirror"}:
            ids = cut_generator.normalize_spec(block, clothing_type=clothing)["matchIds"]
            if len(ids) != len(set(ids)):
                raise ValueError("wearshot_v2_duplicate_matching_selection")
            result.update(ids)
    return result


def catalog_projection(product, analysis, storyboard):
    """Narrow opt-in pilot scope, from server catalogs; no filename or prose inference."""
    from dataclasses import asdict
    from pathlib import Path
    from . import cut_generator, product_evidence_contract
    from .confirmed_gpt_directing import load_confirmed_gpt_directing_catalog
    from .. import facemarket
    clothing = product.get("clothing_type") or product.get("clothingType") or "top"
    catalog = json.loads(Path(cut_generator._DEFAULT_EXAMPLE_ASSETS).read_text())["assets"]
    directing = load_confirmed_gpt_directing_catalog()
    result = {}
    for block in storyboard:
        if not isinstance(block, dict) or block.get("source") != "ai" or block.get("cutType") == "product":
            continue
        spec = cut_generator.normalize_spec(block, clothing_type=clothing)
        model = facemarket.resolve_block_model_id(spec["cutType"], analysis.get("selectedModelId") or analysis.get("selected_model_id"),
            analysis.get("stylingModelId") or analysis.get("styling_model_id"))
        base = cut_generator._base_color(product.get("colors") or [])
        if (spec["cutType"] != "styling" or spec["direction"] != "front" or spec["refScope"] != "all"
                or spec["pose"] != "auto" or spec.get("spaceGroupId") or not model or facemarket.is_real_model_id(model)
                or base is None or (spec.get("colorId") is not None and str(spec["colorId"]) != str(base.get("id")))):
            raise ValueError("wearshot_v2_unsupported_cut_scope")
        entry = catalog.get(spec.get("exampleId"), {})
        scope = entry.get("wearshotScopeV2")
        direction = directing.get(spec.get("exampleId"))
        if (not isinstance(scope, dict) or set(scope) != {"allSha256", "faceVisibility", "garmentScopes"}
                or direction is None or scope["allSha256"] != direction.all_sha256 or direction.shot != spec["shot"]
                or clothing not in direction.applicable_clothing_types or clothing not in scope["garmentScopes"]
                or scope["faceVisibility"] not in {"full", "partial", "hidden"}):
            raise ValueError("wearshot_v2_example_scope_unavailable")
        sheets = cut_generator.resolve_confirmed_gpt_direction_sheets({**spec, "modelId": model})
        product_evidence_contract.validate_persisted(analysis.get("confirmedGptProductEvidence"))
        result[str(block["id"])] = {"scope": deepcopy(scope), "directing": asdict(direction), "modelId": model,
                                   "modelSheets": list(sheets)}
    if not result:
        raise ValueError("wearshot_v2_worn_block_required")
    return result


async def _matching_catalog(conn, user_id, project_id, ids):
    result = {}
    for mid in sorted(ids):
        aid = await repo.get_matching_item_asset(conn, mid, user_id, project_id)
        metadata = await repo.get_matching_item_metadata(conn, mid, user_id, project_id)
        asset = await repo.get_asset_for_user(conn, user_id, aid) if aid else None
        if not asset or not metadata or metadata.get("clothing_type") not in {"top", "bottom", "outer", "dress"}:
            raise ValueError("wearshot_v2_matching_seller_required")
        result[mid] = {"metadata": deepcopy(metadata), "asset": deepcopy(asset)}
    return result


async def _seller_catalog(conn, user_id, product, analysis):
    from . import cut_generator, product_evidence_contract
    evidence = product_evidence_contract.validate_persisted(analysis.get("confirmedGptProductEvidence"))
    refs = cut_generator.color_images(product, None)
    if len(refs) != len(evidence["inputBinding"]["images"]):
        raise ValueError("wearshot_v2_seller_selection_mismatch")
    result = []
    for slot, aid in refs:
        asset = await repo.get_asset_for_user(conn, user_id, aid)
        if not asset:
            raise ValueError("wearshot_v2_target_seller_missing")
        result.append({"slot": slot, "asset": deepcopy(asset)})
    return result


async def snapshot_request(conn, user_id, project_id, project, product, analysis, storyboard, request):
    from ..models import WearshotGenerateRequest
    request = WearshotGenerateRequest.model_validate(request).model_dump(mode="json")
    clothing = product.get("clothing_type") or product.get("clothingType") or "top"
    ids = _matching_ids(storyboard, clothing)
    catalog = catalog_projection(product, analysis, storyboard)
    if set(request["matchingMannequinAssets"]) - ids:
        raise ValueError("wearshot_v2_unused_matching_selection")
    selected = project.get("selected_mannequin_id") or project.get("selectedMannequinId")
    cuts = await repo.list_mannequin_cuts(conn, user_id, project_id)
    cut = next((c for c in cuts if f"{c.get('candidate')}-{c.get('version')}" == selected), None)
    if cut is None:
        raise ValueError("wearshot_v2_target_required")
    target = _asset_projection(await repo.get_owned_mannequin_asset(conn, user_id, cut.get("active_asset_id") or cut["asset_id"]))
    if target["project_id"] != project_id or target["source_cut_id"] != selected:
        raise ValueError("wearshot_v2_target_selection_mismatch")
    matching = {}
    for mid in sorted(ids):
        aid = request["matchingMannequinAssets"].get(mid)
        if aid:
            matching[mid] = _asset_projection(await repo.get_owned_mannequin_asset(conn, user_id, aid))
        elif (target.get("source_metadata") or {}).get("matchItemId") == mid:
            matching[mid] = deepcopy(target)
        else:
            raise ValueError("wearshot_v2_matching_anchor_required")
    matching_catalog = await _matching_catalog(conn, user_id, project_id, ids)
    for block in storyboard:
        scope = catalog.get(str(block.get("id")), {}).get("scope") if isinstance(block, dict) else None
        if scope:
            for mid in _matching_ids([block], clothing):
                if matching_catalog[mid]["metadata"]["clothing_type"] not in scope["garmentScopes"]:
                    raise ValueError("wearshot_v2_matching_scope_unavailable")
    seller_catalog = await _seller_catalog(conn, user_id, product, analysis)
    return {"request": request, "target": target, "matching": matching, "catalog": catalog, "matchingCatalog": matching_catalog,
            "sellerCatalog": seller_catalog,
            "selectionFingerprint": selection_fingerprint(project, product, analysis, storyboard)}


async def verify_snapshot(conn, user_id, project_id, project, product, analysis, storyboard, snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("selectionFingerprint") != selection_fingerprint(project, product, analysis, storyboard):
        raise ValueError("wearshot_v2_queued_selection_changed")
    if snapshot.get("catalog") != catalog_projection(product, analysis, storyboard):
        raise ValueError("wearshot_v2_queued_catalog_changed")
    clothing = product.get("clothing_type") or product.get("clothingType") or "top"
    if snapshot.get("matchingCatalog") != await _matching_catalog(conn, user_id, project_id, _matching_ids(storyboard, clothing)):
        raise ValueError("wearshot_v2_queued_matching_changed")
    if snapshot.get("sellerCatalog") != await _seller_catalog(conn, user_id, product, analysis):
        raise ValueError("wearshot_v2_queued_seller_changed")
    target = snapshot["target"]
    if target["project_id"] != project_id:
        raise ValueError("wearshot_v2_target_project_mismatch")
    for expected in [target, *snapshot["matching"].values()]:
        observed = _asset_projection(await repo.get_owned_mannequin_asset(conn, user_id, expected["id"]))
        if observed != expected:
            raise ValueError("wearshot_v2_queued_anchor_changed")
    return snapshot


async def review_candidate(settings, contract: WearshotContract, candidate, *, repair_plan: RepairPlan | None = None) -> dict:
    primary = await wearshot_qc.verdict(settings, contract, candidate, repair_plan=repair_plan)
    result = dict(primary)
    identity = primary.get("attributes", {}).get("identity", {})
    if contract.frame_lock.face_visibility != "hidden" and identity.get("status") == "PASS":
        try:
            refs = [LabeledReference(ref.role, ref.image) for ref in contract.references
                    if ref.key in (contract.model_face_key, contract.example_key)]
            review = await cut_identity_review.verdict(settings, refs, candidate)
            if not isinstance(review, dict) or review.get("candidateSha256") != image_sha256(candidate):
                raise ValueError("wearshot_v2_identity_candidate_mismatch")
        except Exception:
            review = {"status": "UNJUDGEABLE", "candidateSha256": image_sha256(candidate),
                      "evidence": "Independent visible-face evidence unavailable."}
        result["identityReview"] = review
    return result


def release_allowed(result, contract, candidate, *, repair_plan=None) -> bool:
    if not wearshot_qc.release_allowed(result, contract, candidate, repair_plan=repair_plan):
        return False
    if contract.frame_lock.face_visibility == "hidden":
        return True
    focus = result.get("identityReview")
    return (isinstance(focus, dict) and focus.get("status") == "PASS"
            and focus.get("candidateSha256") == image_sha256(candidate))


def derive_repair_plan(contract, candidate, result) -> RepairPlan:
    # Revalidate trusted server observations; never trust a caller's PASS summaries.
    checked = wearshot_qc.validate(result.get("observations"), contract, candidate)
    if not checked.get("valid") or result.get("candidateSha256") != image_sha256(candidate):
        raise ValueError("wearshot_v2_unrepairable_receipt")
    failed = set(checked["failedAttributes"])
    passed = set(checked["passedAttributes"])
    if contract.frame_lock.face_visibility != "hidden" and "identity" in passed:
        focus = result.get("identityReview", {})
        if focus.get("status") != "PASS" or focus.get("candidateSha256") != image_sha256(candidate):
            failed.add("identity")
            passed.discard("identity")
    if "variation" in failed:
        failed.add(contract.variation_axis)
        passed.discard(contract.variation_axis)
    return make_repair_plan(contract, candidate, tuple(sorted(failed)), tuple(sorted(passed)))


def source_output_size(image) -> str:
    """2K long edge, nearest provider 16px unit, at most 0.5% aspect deviation."""
    import io
    from PIL import Image
    with Image.open(io.BytesIO(image.data)) as decoded:
        width, height = decoded.size
    scale = 2048 / max(width, height)
    value = f"{round(width * scale / 16) * 16}x{round(height * scale / 16) * 16}"
    return validate_output_size(image, value)


def validate_output_size(image, output_size: str) -> str:
    """Validate a requested canvas against bound source/base; never resample the reference."""
    import io
    from PIL import Image
    from .gemini_image import validate_openai_output_size
    validate_openai_output_size(output_size)
    if output_size == "auto":
        raise ValueError("wearshot_v2_exact_output_size_required")
    with Image.open(io.BytesIO(image.data)) as decoded:
        width, height = decoded.size
    out_w, out_h = map(int, output_size.split("x"))
    if max(out_w, out_h) != 2048:
        raise ValueError("wearshot_v2_native_2k_required")
    if abs((out_w / out_h) / (width / height) - 1) > 0.005:
        raise ValueError("wearshot_v2_output_aspect_mismatch")
    return output_size


def build_contract(*, target_id, target_asset_id, target_image, seller_images, evidence_contract,
                   matching, face_image, body_image, model_id, example_image, directing, scope,
                   clothing_type, variation_axis="pose", capture_profile="soft"):
    """Bind exact original seller ordinals and reviewed category/crop scope; no grid flattening."""
    from . import product_evidence_contract
    if (not isinstance(scope, dict) or set(scope) != {"allSha256", "faceVisibility", "garmentScopes"}
            or scope["allSha256"] != image_sha256(example_image)):
        raise ValueError("wearshot_v2_scope_binding_mismatch")
    categories = scope["garmentScopes"]
    if not isinstance(categories, dict) or clothing_type not in categories:
        raise ValueError("wearshot_v2_category_scope_unavailable")
    evidence = product_evidence_contract.validate_persisted(evidence_contract)
    if not product_evidence_contract.source_binding_matches(evidence,
            [(image.data, image.mime) for slot, image, aid in seller_images], [slot for slot, image, aid in seller_images]):
        raise ValueError("wearshot_v2_seller_binding_changed")
    refs = [BoundReference("target-anchor", "approvedMannequin", target_image, target_id, asset_id=target_asset_id)]
    seller_keys = []
    slots = {}
    for ordinal, (slot, image, aid) in enumerate(seller_images, 1):
        key = f"target-seller-{ordinal}"
        seller_keys.append(key)
        slots[ordinal] = slot
        refs.append(BoundReference(key, "targetSeller", image, target_id, ordinal, aid))
    essentials = tuple(EssentialDetail(fact["code"], fact["value"],
        tuple(seller_keys[ordinal - 1] for ordinal in fact["evidenceOrdinals"]),
        visible=not all(slots[ordinal] in {"Back", "BackDetail"} for ordinal in fact["evidenceOrdinals"]))
        for fact in evidence["hardFacts"])
    target = GarmentBinding(target_id, "target-anchor", tuple(seller_keys), essentials, tuple(categories[clothing_type]))
    matches = []
    for index, (mid, category, anchor, anchor_id, seller, seller_id) in enumerate(matching, 1):
        if category not in categories:
            raise ValueError("wearshot_v2_matching_category_scope_unavailable")
        anchor_key, seller_key = f"matching-{index}-anchor", f"matching-{index}-seller-1"
        refs.extend((BoundReference(anchor_key, "approvedMannequin", anchor, mid, asset_id=anchor_id),
                     BoundReference(seller_key, "matchingSeller", seller, mid, 1, seller_id)))
        matches.append(GarmentBinding(mid, anchor_key, (seller_key,), out_of_frame_axes=tuple(categories[category])))
    refs.append(BoundReference("model-face", "modelFace", face_image, asset_id=model_id))
    if body_image is not None:
        refs.append(BoundReference("model-body", "modelBody", body_image, asset_id=model_id))
    refs.append(BoundReference("example", "example", example_image))
    frame = {"requestedFraming": directing.requested_framing, "faceExposure": directing.face_exposure}
    for key in ("direction_description", "pose_semantics", "fixed_inner", "fixed_footwear"):
        value = getattr(directing, key, None)
        if value is not None:
            from dataclasses import asdict, is_dataclass
            frame[key] = asdict(value) if is_dataclass(value) else value
    return bind_contract(target=target, matching=tuple(matches), expected_matching_ids=tuple(g.garment_id for g in matches),
        references=tuple(refs), example_key="example", model_face_key="model-face",
        model_body_key="model-body" if body_image is not None else None,
        frame_lock=FrameLock(directing.shot, scope["faceVisibility"], json.dumps(frame, ensure_ascii=False, sort_keys=True)),
        variation_axis=variation_axis, capture_profile=capture_profile)


async def prepare_block(settings, conn, user_id, project_id, block, product, analysis, snapshot,
                        *, load_asset, load_model_image):
    """Resolve bytes only from the queued, reverified selections. No virtual fallback."""
    from types import SimpleNamespace
    from . import cut_generator
    clothing = product.get("clothing_type") or product.get("clothingType") or "top"
    spec = cut_generator.normalize_spec(block, clothing_type=clothing)
    entry = snapshot["catalog"].get(str(block.get("id")))
    if entry is None:
        raise ValueError("wearshot_v2_block_scope_unavailable")
    spec = {**spec, "id": block.get("id"), "source": block.get("source"), "modelId": entry["modelId"]}
    async def anchor(row):
        image = await load_asset(row)
        if row["id"] != row["source_asset_id"]:
            source = await load_asset({"id": row["source_asset_id"], "r2_key": row["source_r2_key"],
                                       "mime_type": row["source_mime_type"]})
            if image_sha256(source) != row["metadata"].get("sourceHash"):
                raise ValueError("wearshot_v2_tone_source_hash_mismatch")
        return image
    target = snapshot["target"]
    target_image = await anchor(target)
    sellers = []
    for row in snapshot["sellerCatalog"]:
        asset = row["asset"]
        sellers.append((row["slot"], await load_asset(asset), asset["id"]))
    if not sellers:
        raise ValueError("wearshot_v2_target_seller_missing")
    matches = []
    for mid in spec["matchIds"]:
        row = snapshot["matching"][mid]
        catalog = snapshot["matchingCatalog"][mid]
        matches.append((mid, catalog["metadata"]["clothing_type"], await anchor(row), row["id"],
                        await load_asset(catalog["asset"]), catalog["asset"]["id"]))
    model_images = []
    for ref in entry["modelSheets"]:
        image = await load_model_image(ref["key"], ref["mime"], ref.get("bucket", "public"))
        if len(image.data) != ref["byteLength"] or image_sha256(image) != ref["sha256"]:
            raise ValueError("wearshot_v2_model_sheet_hash_mismatch")
        model_images.append(image)
    if len(model_images) != 2:
        raise ValueError("wearshot_v2_model_sheet_pair_required")
    example = await cut_generator.load_example_image(settings, spec["exampleId"], scope="all", clothing_type=clothing)
    if example is None:
        raise ValueError("wearshot_v2_example_missing")
    contract = build_contract(target_id=project_id, target_asset_id=target["id"], target_image=target_image,
        seller_images=tuple(sellers), evidence_contract=analysis.get("confirmedGptProductEvidence"), matching=tuple(matches),
        face_image=model_images[0], body_image=model_images[1], model_id=entry["modelId"], example_image=example,
        directing=SimpleNamespace(**entry["directing"]), scope=entry["scope"], clothing_type=clothing,
        variation_axis=snapshot["request"]["variationAxis"], capture_profile=snapshot["request"]["captureProfile"])
    source_output_size(example)
    images = [ref.image for ref in contract.references]
    return (spec, images, "", False, [image for slot, image, aid in sellers], None, False, None, None, False, images, contract)
