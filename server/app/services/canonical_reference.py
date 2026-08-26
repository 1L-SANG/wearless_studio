"""Canonical (background-removed) garment references: record them, and load them.

Two halves that must not be confused:

  PRODUCER  `sam_preprocess` job -> SAM service -> R2 object -> `record()` writes an asset row
  CONSUMER  `load()` reads asset rows and returns references for generation

`load()` never calls SAM, never enqueues, never waits. If preprocessing has not finished, it
returns nothing and generation proceeds on RAW alone. That separation is why a SAM outage can
never stall or fail a mannequin job.

Storage uses the existing `assets` model — no new table. A canonical cutout is an asset with
`source='derived'` whose metadata records what produced it and, critically, WHICH SOURCE it was
built from. That last part is what makes staleness detectable: when a seller replaces the Front
photo, the old cutout's `sourceAssetId`/`sourceHash` no longer match the current Front, so
`load()` refuses it rather than dressing a mannequin in a cutout of the previous garment.
"""

from __future__ import annotations

import hashlib
import logging
import uuid

from psycopg import errors

from app import repo
from app.services import sam_retry
from app.agents.product_reference import ProductReference
from app.agents.vision_llm import InlineImage

log = logging.getLogger(__name__)

#: Slot names the generation path uses for canonical references.
CANONICAL_FRONT = "CanonicalFront"
CANONICAL_BACK = "CanonicalBack"
SLOT_FOR_VIEW = {"Front": CANONICAL_FRONT, "Back": CANONICAL_BACK}

#: Only these views get a cutout. Detail is a macro close-up with no garment silhouette to
#: isolate, and Fit is a photograph of a person. Lives here rather than in the worker because
#: the enqueue sites need the same answer to build the job's identity.
ELIGIBLE_VIEWS = ("Front", "Back")

#: Marks an asset row as one of ours. Detail is deliberately absent: a macro close-up has no
#: garment silhouette to isolate, so there is no CanonicalDetail.
CANONICAL_KIND = "canonical_cutout"
PRODUCER = "sam2_service"


def preprocess_idempotency_key(project_id: str, product: dict, *,
                               retry: int = 0) -> str | None:
    """The cutout job's identity: this project's *current* base-colour photographs.

    None when there is nothing to segment — an empty job must never take the key, or the real
    photographs arriving a moment later would join a `skipped` job and never be segmented.

    The photo ids are IN the key on purpose. A seller who swaps the front photograph has to get
    a new cutout; a fixed per-project key would keep serving the previous garment's silhouette
    to every consumer that asks "what does the product look like".

    `retry` 도 같은 이유로 키에 들어간다 — 일시 장애로 끝난 잡이 키를 물고 있는 한 재시도는
    그 시체에 합류만 한다(2026-08-21, 톤 마스크의 mask_job_key 와 같은 규칙).
    """
    from app.agents import mannequin      # 서비스→에이전트 단방향 (editor_garment_mask 와 같은 결)
    ids = [aid for slot, aid in mannequin.base_color_images(product)
           if slot in ELIGIBLE_VIEWS and aid]
    if not ids:
        return None
    digest = hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()[:16]
    return sam_retry.generation_key(f"{project_id}:sam_preprocess:{digest}", retry)


def metadata_for(view: str, result, source_asset_id: str) -> dict:
    """The traceability record stored on the derived asset."""
    return {
        "canonicalType": CANONICAL_KIND,
        "view": view,
        "sourceAssetId": source_asset_id,
        "sourceHash": result.source_hash,
        "producer": PRODUCER,
        "modelVersion": result.model_version,
        "algorithmVersion": result.algorithm_version,
        "r2Key": result.cutout_key,
        "checksum": result.checksum,
        "width": result.width,
        "height": result.height,
        "areaFrac": result.area_frac,
    }


def is_current(meta: dict, *, source_asset_id: str, source_hash: str | None) -> bool:
    """Does this derived asset describe the source that is on the product RIGHT NOW?

    Asset id alone is not enough — a replaced upload can reuse an id — so the content hash is
    checked too whenever the caller knows it.
    """
    if not isinstance(meta, dict) or meta.get("canonicalType") != CANONICAL_KIND:
        return False
    if str(meta.get("sourceAssetId") or "") != str(source_asset_id):
        return False
    if source_hash and str(meta.get("sourceHash") or "") != str(source_hash):
        return False
    return True


async def record(conn, *, user_id: str, project_id: str, view: str, result,
                 source_asset_id: str) -> dict | None:
    """Create — or reuse — the asset row for one produced cutout. Idempotent.

    Idempotency is by lookup-before-create on (owner, r2Key): the SAM service derives that
    key deterministically from source content + view + model + algorithm, so a retried job that
    re-produces the same cutout finds the same key and reuses the existing row. Without this, a
    dispatcher retry after a timeout would leave duplicate rows pointing at one object.

    The lookup is scoped to the **owner, not the project**, because `assets.r2_key` is globally
    unique while the key itself is content-derived and carries no project. The same photograph
    re-uploaded into a second project produces the same cutout key, so a project-scoped lookup
    missed the existing row and the INSERT died on `assets_r2_key_key` — taking the whole
    dispatcher iteration with it (2026-08-26, prod). Another seller's identical photograph is a
    different matter: we must not pull their asset row into this project, so that collision
    gives up this one cutout (generation falls back to RAW) instead of raising.
    """
    if not result.ready or not result.cutout_key:
        return None
    existing = await find_by_key(conn, user_id=user_id, r2_key=result.cutout_key)
    if existing:
        return existing

    asset_id = str(uuid.uuid4())
    # 직접 SAVEPOINT — 충돌로 tx 가 abort 되면 같은 커넥션의 **다음 슬롯**(Front 실패 →
    # Back)까지 "current transaction is aborted" 로 끌려간다. 앞뒤 뷰의 독립성이 이 워커의
    # 계약이므로(sam_preprocess_job 주석) 충돌은 이 슬롯 안에서 끝내야 한다.
    # conn.transaction() 은 쓰지 않는다 — 열린 tx 가 없으면 스스로 커밋해 호출자의 커밋
    # 제어를 빼앗는다(repo.create_job 의 같은 이유).
    async with conn.cursor() as cur:
        await cur.execute("savepoint canonical_record")
    try:
        row = await repo.create_asset(
            conn, asset_id=asset_id, user_id=user_id, project_id=project_id,
            source="derived", bucket="", key=result.cutout_key, mime="image/png",
            size=result.byte_size, original_filename=None)
        await set_metadata(conn, asset_id=asset_id,
                           metadata=metadata_for(view, result, source_asset_id))
    except errors.UniqueViolation:
        async with conn.cursor() as cur:
            await cur.execute("rollback to savepoint canonical_record")
        log.warning(
            "canonical cutout key already owned by another seller — skipping record "
            "project=%s view=%s key=%s", project_id, view, result.cutout_key)
        return None
    async with conn.cursor() as cur:
        await cur.execute("release savepoint canonical_record")
    log.info("canonical cutout recorded project=%s view=%s asset=%s key=%s",
             project_id, view, asset_id, result.cutout_key)
    return row


async def find_by_key(conn, *, user_id: str, r2_key: str) -> dict | None:
    """소유자 범위로 찾는다 — r2_key 는 전역 unique 이고 내용 해시라 프로젝트를 안 담는다."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, r2_key, mime_type, byte_size, metadata from assets "
            "where user_id = %s and r2_key = %s and deleted_at is null limit 1",
            (user_id, r2_key))
        return await cur.fetchone()


async def set_metadata(conn, *, asset_id: str, metadata: dict) -> None:
    from psycopg.types.json import Json
    async with conn.cursor() as cur:
        await cur.execute("update assets set metadata = %s where id = %s",
                          (Json(metadata), asset_id))


async def list_for_project(conn, *, project_id: str) -> list[dict]:
    """Every canonical cutout row for a project, newest first."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, r2_key, mime_type, byte_size, metadata from assets "
            "where project_id = %s and source = 'derived' and deleted_at is null "
            "and metadata->>'canonicalType' = %s order by created_at desc",
            (project_id, CANONICAL_KIND))
        return list(await cur.fetchall())


async def current_key(conn, *, project_id: str, view: str, source: dict) -> str | None:
    """R2 key of the cutout made from *this exact* photograph. None when absent or stale.

    A key, not bytes: the SAM boundary only ever takes trusted R2 keys (`sam_client`), and the
    service resolves them with its own credentials. Same staleness rule as `load` — a cutout
    naming a photograph the product no longer has is a picture of the previous garment.
    """
    for row in await list_for_project(conn, project_id=project_id):   # newest first
        meta = row.get("metadata") or {}
        if meta.get("view") != view:
            continue
        if is_current(meta, source_asset_id=source.get("id"), source_hash=source.get("hash")):
            return row.get("r2_key") or None
    return None


async def load(conn, r2, *, project_id: str,
               sources: dict[str, dict]) -> dict[str, ProductReference]:
    """Current canonical references, keyed by slot. Loads only — never produces.

    `sources` is {view: {"id": asset_id, "hash": source_hash | None}} describing the RAW
    photographs the product has right now. A stored cutout is returned only when it names one
    of those exact sources; anything else is stale and is skipped silently, because a missing
    canonical reference is a normal state (preprocessing may simply not have run yet).
    """
    if not sources:
        return {}
    rows = await list_for_project(conn, project_id=project_id)
    out: dict[str, ProductReference] = {}
    for row in rows:
        meta = row.get("metadata") or {}
        view = meta.get("view")
        slot = SLOT_FOR_VIEW.get(view or "")
        if not slot or slot in out:              # newest row per view wins
            continue
        src = sources.get(view)
        if not src or not is_current(meta, source_asset_id=src.get("id"),
                                     source_hash=src.get("hash")):
            continue
        try:
            data = await _get_bytes(r2, row["r2_key"])
        except Exception as exc:                 # noqa: BLE001 - RAW is always safe
            log.warning("canonical cutout unreadable project=%s slot=%s: %r",
                        project_id, slot, exc)
            continue
        if not data:
            continue
        out[slot] = ProductReference(slot=slot, asset_id=row["id"],
                                     image=InlineImage(row.get("mime_type") or "image/png",
                                                       data))
    return out


async def _get_bytes(r2, key: str) -> bytes:
    import asyncio
    return await asyncio.to_thread(r2.get_bytes, key)
