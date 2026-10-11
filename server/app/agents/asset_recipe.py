"""사진 자산의 서버 기록으로 생성 설정을 찾는다. 모든 DB 접근은 읽기 전용이다."""
from __future__ import annotations

from copy import deepcopy
from urllib.parse import urlsplit
import uuid

from .. import repo
from . import content_roles


def project_recipe(value: dict | None) -> dict | None:
    """화면용 값은 빼고 공용 필드를 모두 담는다. 없는 값은 null로 남긴다."""
    if not isinstance(value, dict):
        return None
    return {key: deepcopy(value.get(key)) for key in content_roles.GENERATION_RECIPE_FIELDS}


def asset_id_from_file_capability(value: object) -> str | None:
    """자산 파일 경로의 규칙은 라우트의 capability 주소 해석과 같다."""
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value).path.strip("/").split("/")
    except ValueError:
        return None
    if len(parts) != 4 or parts[:2] != ["v1", "assets"] or parts[3] != "file":
        return None
    try:
        return str(uuid.UUID(parts[2]))
    except (TypeError, ValueError):
        return None


def page_asset_map(result: dict | None) -> dict[str, str]:
    """완료한 페이지 작업 결과만 사용한다. 현재 에디터의 복사된 연결값은 읽지 않는다."""
    blocks = result.get("data") if isinstance(result, dict) else None
    mapped = {}
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        for element in block.get("elements") or []:
            if not isinstance(element, dict) or element.get("type") != "image":
                continue
            asset_id = asset_id_from_file_capability(element.get("src"))
            source_block_id = element.get("sourceBlockId")
            if asset_id and isinstance(source_block_id, str) and source_block_id:
                mapped.setdefault(asset_id, source_block_id)
    return mapped


def _payload(job: dict | None) -> dict:
    value = (job or {}).get("payload")
    return value if isinstance(value, dict) else {}


def _source_asset_id(payload: dict) -> str | None:
    if payload.get("mode") != "vary":
        return None
    similar = payload.get("_similarCut")
    selected_id = similar.get("sourceAssetId") if isinstance(similar, dict) else None
    if selected_id:
        try:
            return str(uuid.UUID(str(selected_id)))
        except (TypeError, ValueError):
            return None
    source = payload.get("source")
    return asset_id_from_file_capability(source.get("src")) if isinstance(source, dict) else None


def recipe_for_asset(
    asset_row: dict, *, page_map: dict | None = None, storyboard_by_id: dict | None = None,
    producing_job: dict | None = None, wardrobe_row: dict | None = None,
    anchor_resolved: dict | None = None,
) -> dict:
    """ADR-0019 순서대로 기록, 완료 페이지, 옛 요청, 업로드, 의류 목록을 해석한다."""
    metadata = asset_row.get("metadata") or {}
    metadata = metadata if isinstance(metadata, dict) else {}
    wardrobe_row = wardrobe_row or {}
    payload = _payload(producing_job)
    storyboard_by_id = storyboard_by_id or {}
    real_derived = (
        metadata["facemarket_real_derived"] is True
        if "facemarket_real_derived" in metadata
        else isinstance(payload.get("_facemarket"), dict)
    )
    result = {
        "origin": None, "recipe": None, "recipeFrom": None, "sourceAssetId": None,
        "cutType": wardrobe_row.get("cut_type") or metadata.get("cut_type"),
        "colorId": wardrobe_row.get("color_id"),
        "upload": asset_row.get("source") == "upload", "realDerived": real_derived,
    }

    def with_recipe(origin, recipe, recipe_from="self", source_id=None):
        out = {**result, "origin": origin, "recipe": project_recipe(recipe),
               "recipeFrom": recipe_from, "sourceAssetId": source_id}
        if out["recipe"] is not None:
            out["cutType"] = out["recipe"].get("cutType") or out["cutType"]
            if out["recipe"].get("colorId") is not None:
                out["colorId"] = out["recipe"]["colorId"]
        return out

    generation = metadata.get("generation")
    if isinstance(generation, dict) and generation.get("version") == 1:
        return with_recipe(generation.get("origin"), generation.get("recipe"),
                           generation.get("recipeFrom"), generation.get("sourceAssetId"))

    block = storyboard_by_id.get((page_map or {}).get(str(asset_row.get("id"))))
    if isinstance(block, dict) and block.get("source") == "ai":
        return with_recipe("page", content_roles.canonicalize_storyboard_block(block))

    # 옛 다시 만들기는 요청 필드보다 저장 콘티를 먼저 사용한다.
    retry_id = payload.get("retryBlockId")
    stored = storyboard_by_id.get(retry_id) if retry_id else None
    is_editor_job = (producing_job or {}).get("kind", "editor_image") == "editor_image"
    if is_editor_job and isinstance(stored, dict) and stored.get("source") == "ai":
        return with_recipe("editor_retry", content_roles.canonicalize_storyboard_block(stored),
                           "anchor" if payload.get("mode") == "vary" else "self",
                           _source_asset_id(payload))
    if is_editor_job and payload.get("mode") == "new" and not retry_id:
        return with_recipe("editor_new", content_roles.canonicalize_storyboard_block(payload))
    elif is_editor_job and payload.get("mode") == "vary":
        source_id = _source_asset_id(payload)
        anchor = anchor_resolved or {}
        out = with_recipe("editor_similar" if payload.get("variationMode") == "similar"
                          else "editor_reshoot", anchor.get("recipe"), "anchor", source_id)
        out["cutType"] = anchor.get("cutType") or out["cutType"]
        if anchor.get("colorId") is not None:
            out["colorId"] = anchor["colorId"]
        out["realDerived"] = out["realDerived"] or anchor.get("realDerived") is True
        return out
    if result["upload"]:
        result["origin"] = "upload"
    return result


def _producing_job_id(asset_row: dict, user_id: str, project_id: str) -> str | None:
    """ai_key가 저장한 프로젝트 소유 경로에서 작업 번호를 찾는다."""
    parts = str(asset_row.get("r2_key") or "").split("/")
    if (len(parts) == 7 and parts[:5] == ["users", user_id, "projects", project_id, "ai"]
            and parts[5] and parts[6]):
        return parts[5]
    return None


async def _read_assets(conn, user_id, project_id, asset_ids):
    if not asset_ids:
        return {}
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, source, metadata, r2_key from assets "
            "where user_id = %s and project_id = %s and id = any(%s::uuid[]) "
            "and deleted_at is null", (user_id, project_id, asset_ids))
        return {str(row["id"]): row for row in await cur.fetchall()}


async def _read_jobs(conn, user_id, project_id, assets):
    job_ids = list(dict.fromkeys(job_id for row in assets.values()
                   if (job_id := _producing_job_id(row, user_id, project_id))))
    if not job_ids:
        return {}
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, kind, payload from jobs where user_id = %s and project_id = %s "
            "and id::text = any(%s) and kind in ('editor_image', 'detail_page')",
            (user_id, project_id, job_ids))
        return {str(row["id"]): row for row in await cur.fetchall()}


async def resolve_asset_recipes(conn, user_id, project_id, asset_ids) -> dict[str, dict]:
    """소유한 자산별 origin, recipe, recipeFrom, sourceAssetId, cutType, colorId,
    upload, realDerived를 돌려준다. 옛 비슷한 컷의 원본 조회는 한 단계만 허용한다."""
    requested = []
    for asset_id in asset_ids or []:
        try:
            normalized = str(uuid.UUID(str(asset_id)))
        except (TypeError, ValueError):
            continue
        if normalized not in requested:
            requested.append(normalized)
    assets = await _read_assets(conn, user_id, project_id, requested)
    if not assets:
        return {}
    storyboard = await repo.get_storyboard(conn, project_id)
    storyboard_by_id = {str(block.get("id")): block for block in storyboard if isinstance(block, dict)}
    wardrobe = await repo.list_wardrobe_images(conn, user_id, project_id)
    wardrobe_by_asset = {str(row["asset_id"]): row for row in wardrobe}
    async with conn.cursor() as cur:
        await cur.execute(
            "select result from jobs where user_id = %s and project_id = %s "
            "and kind = 'detail_page' and status = 'done' "
            "order by finished_at desc nulls last, created_at desc, id desc limit 1",
            (user_id, project_id))
        page_job = await cur.fetchone()
    page_map = page_asset_map((page_job or {}).get("result"))
    jobs = await _read_jobs(conn, user_id, project_id, assets)

    def job_for(row):
        return jobs.get(_producing_job_id(row, user_id, project_id))

    def resolve(row, anchor=None):
        return recipe_for_asset(row, page_map=page_map, storyboard_by_id=storyboard_by_id,
                                producing_job=job_for(row), wardrobe_row=wardrobe_by_asset.get(str(row["id"])),
                                anchor_resolved=anchor)

    preliminary = {asset_id: resolve(row) for asset_id, row in assets.items()}
    anchors = list(dict.fromkeys(record["sourceAssetId"] for key, record in preliminary.items()
                   if record["recipe"] is None and record["sourceAssetId"]
                   and record["origin"] in {"editor_similar", "editor_reshoot"}
                   and not ((generation := (assets[key].get("metadata") or {}).get("generation"))
                            and isinstance(generation, dict) and generation.get("version") == 1)))
    missing = [asset_id for asset_id in anchors if asset_id not in assets]
    anchor_assets = await _read_assets(conn, user_id, project_id, missing)
    jobs.update(await _read_jobs(conn, user_id, project_id, anchor_assets))
    all_assets = {**assets, **anchor_assets}
    anchor_records = {asset_id: resolve(all_assets[asset_id]) for asset_id in anchors if asset_id in all_assets}
    return {asset_id: resolve(row, anchor_records.get(preliminary[asset_id]["sourceAssetId"]))
            for asset_id, row in assets.items()}
