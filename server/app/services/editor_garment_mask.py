"""Editor garment masks: record them, and resolve them for the Tone Editor.

The mask says which pixels of a GENERATED mannequin cut are the sold garment. It exists so the
seller can nudge saturation and exposure on the garment alone; nothing else in the frame may
move. It is produced once, asynchronously, after the cut is already on screen.

Two halves, deliberately separate — the same split canonical references use:

  PRODUCER  `editor_garment_mask` job -> SAM service -> R2 object -> `record()` writes an asset
  CONSUMER  `resolve()` reads asset rows and answers ready / processing / failed

`resolve()` never calls SAM and never enqueues. A missing mask is a normal state (preprocessing
may still be running), and the editor shows a disabled control rather than an error.

Storage reuses the existing `assets` model. A mask is a derived asset whose metadata records the
cut it belongs to AND that cut's content hash — which is what makes staleness detectable. A
regenerated cut produces different bytes, so the old mask no longer matches and is refused
instead of being painted onto a different garment.
"""

from __future__ import annotations

import logging
import uuid

from app import repo

log = logging.getLogger(__name__)

#: Marks an asset row as one of ours. Never mixed with `canonical_cutout`: that one is a cutout
#: of a RAW product photograph, this one is a mask over a generated image.
MASK_KIND = "editorGarmentMask"
PRODUCER = "sam2-worn-garment"
# 생성 직후 큐와 기존 컷 lazy backfill 큐가 공유하는 알고리즘 신원. torch 없는 API
# 모듈에 둬서 두 경로가 같은 멱등키 버전을 사용하게 한다.
ALGORITHM_VERSION = "editor-worn-garment-sam2-v3"

STATUS_READY = "ready"
STATUS_PROCESSING = "processing"
STATUS_FAILED = "failed"

# ── 매칭 의류(코디 옷) 위에는 절대 마스크를 두지 않는다 ──────────────────────────────
#
# 마네킹컷은 주상품과 코디 의류를 함께 착장한다. 파는 옷은 주상품 하나뿐이므로 색감·밝기가
# 움직일 수 있는 픽셀도 주상품뿐이다 — 코디 바지 색을 바꿔 발행하면 구매자가 살 수 없는 색을
# 보여주게 된다.
#
# 아래 값은 `sam_service/worn_garment.py` 의 같은 이름 상수와 **반드시 같은 값**이다. API
# 이미지는 그 모듈(SAM 런타임)을 임포트하지 않는다는 경계 때문에 복제하고, 동일성은 테스트가
# 고정한다(ALGORITHM_VERSION 이 이미 같은 이유로 복제되어 있다).
MATCHING_CORE = {"bottom": (0.60, 1.00), "top": (0.00, 0.35)}
MATCHING_SEPARABLE = {"top": ("bottom",), "outer": ("bottom",), "bottom": ("top",)}
MATCH_ZONE_MAX = 0.25
#: 종류를 모르면 상의로 본다 — `worn_garment.category_of` 와 같은 폴백이어야 한다. 여기서
#: 갈리면 종류가 비어 있는 컷에서 서비스는 밴드를 쓰는데 API 검증은 건너뛴다(2026-08-17 실측).
KNOWN_CATEGORIES = ("top", "outer", "bottom", "dress")

#: 이 값이 메타에 찍힌 마스크만 "매칭 의류 위가 아님"이 확인된 마스크다. SAM 서비스 배포가
#: 늦어도 보장이 성립하도록 **API 가 직접** 픽셀을 재보고 찍는다. 값이 다르면(=보장 이전에
#: 만들어진 마스크) 매칭 의류가 있는 컷에서는 다시 만든다.
MATCH_GUARD_VERSION = "main-garment-guard-v1"


async def matching_side_for_project(conn, *, user_id: str, project_id: str) -> str | None:
    """이 프로젝트의 마네킹컷이 함께 입은 코디 의류의 쪽(top|bottom). 없으면 None.

    상태 판정(라우트)과 생성 판정(마스크 잡)이 **반드시 같은 답**을 봐야 한다 — 한쪽만
    "코디 있음"으로 보면 "만들면 stale, 조회하면 없음"으로 무한히 다시 큐에 들어간다.
    그래서 두 경로가 이 함수 하나만 부른다.

    쪽은 셀러가 고른 매칭 아이템 자신의 `clothing_type` 이다. 주상품에서 역산하지 않는다 —
    커스텀 업로드는 자기 종류를 들고 있고, 잘못 태깅된 경우 밴드가 반대편에 걸린다.
    """
    from app.agents import mannequin  # 서비스→에이전트 단방향 (matching_flatlay 와 같은 결)
    analysis = await repo.get_analysis(conn, project_id) or {}
    match_id = mannequin.main_match_item_id(analysis)
    if not match_id:
        return None
    meta = await repo.get_matching_item_metadata(conn, match_id, user_id, project_id)
    side = str((meta or {}).get("clothing_type") or "").strip().lower()
    return side if side in ("top", "bottom") else None


async def current_mask_for_cut(conn, *, user_id: str, project_id: str,
                               cut_id: str) -> tuple[dict | None, str | None]:
    """지금 **써도 되는** 마스크와 이 컷의 코디 쪽. 보장 이전 마스크는 없는 것으로 본다.

    상태 조회·마스크 픽셀 전송·적용이 모두 이 함수를 지난다. 한 곳이라도 `find_for_cut` 을
    직접 부르면 그 경로로 검증되지 않은 마스크가 새고, 배포 순간 에디터를 열어 둔 셀러가
    바로 그 경로를 탄다(2026-08-17 리뷰).
    """
    mask = await find_for_cut(conn, project_id=project_id, cut_id=cut_id)
    side = await matching_side_for_project(conn, user_id=user_id, project_id=project_id)
    if mask is not None and needs_match_guard(mask.get("metadata") or {}, matching_side=side):
        return None, side
    return mask, side


def matching_core_band(clothing_type: str | None, matching_side: str | None) -> tuple:
    """이 컷에서 코디 의류만 있을 수 있는 세로 밴드. 가를 수 없는 조합이면 ().

    빈 값은 "예전과 똑같이 판단한다"는 뜻이다 — 매칭 없음, 원피스(종아리까지 내려와 밴드가
    성립하지 않음), 주상품과 같은 쪽으로 잘못 태깅된 커스텀 업로드가 모두 여기 걸린다.
    """
    category = str(clothing_type or "").strip().lower()
    if category not in KNOWN_CATEGORIES:
        category = "top"
    side = str(matching_side or "").strip().lower()
    if side not in MATCHING_SEPARABLE.get(category, ()):
        return ()
    return MATCHING_CORE[side]


def band_mass_fraction(png: bytes, band: tuple) -> float | None:
    """마스크 질량 중 밴드 안에 있는 비율. 판정 불가(디코드 실패·빈 마스크)면 None.

    None 은 fail-open 이다 — 마스크를 읽지 못한 것을 "코디 옷 위에 있다"로 단정하지 않는다.
    """
    if not band or not png:
        return None
    try:
        import numpy as np
        from PIL import Image
        from io import BytesIO
        img = Image.open(BytesIO(png)).convert("L")
        img.load()
        m = np.asarray(img) > 127
    except Exception:  # noqa: BLE001 - 손상된 PNG 는 판정 불가로 흘린다
        return None
    total = float(m.sum())
    if total <= 0:
        return None
    h = m.shape[0]
    y0, y1 = int(h * band[0]), int(h * band[1])
    return round(float(m[y0:y1].sum() / total), 4)


def needs_match_guard(meta: dict, *, matching_side: str | None) -> bool:
    """이 마스크를 다시 만들어야 하나 — 코디 의류가 있는 컷인데 보장 스탬프가 없거나 옛것.

    코디 의류가 없는 컷은 대상이 아니다(스탬프가 없어도 그대로 쓴다) — 그 컷의 마스크는
    처음부터 주상품 하나만 두고 고른 것이라 다시 만들 이유가 없다.
    """
    if not str(matching_side or "").strip():
        return False
    if not isinstance(meta, dict):
        return True
    return str(meta.get("matchGuardVersion") or "") != MATCH_GUARD_VERSION


def metadata_for(result, *, cut_id: str, source_asset_id: str, category: str | None,
                 sub_category: str | None, matching_side: str | None = None,
                 match_share: float | None = None) -> dict:
    """Provenance stored on the derived asset. Enough to answer 'what made this, from what'."""
    return {
        "type": MASK_KIND,
        "sourceCutId": cut_id,
        "sourceAssetId": source_asset_id,
        "sourceHash": result.source_hash,
        "producer": PRODUCER,
        "modelVersion": result.model_version,
        "algorithmVersion": result.algorithm_version,
        "selectorVersion": result.selector_version,
        "grid": result.grid,
        "m2m": result.m2m,
        "r2Key": result.mask_key,
        "checksum": result.checksum,
        "width": result.width,
        "height": result.height,
        "areaFrac": result.area_frac,
        "category": category,
        "subCategory": sub_category,
        # 코디 의류 쪽과, 이 마스크가 그 밴드에 걸친 비율(API 가 픽셀로 직접 확인한 값).
        "matchingSide": matching_side,
        "matchShare": match_share,
        "matchGuardVersion": MATCH_GUARD_VERSION,
        "status": STATUS_READY,
    }


def is_current(meta: dict, *, cut_id: str, source_hash: str | None,
               algorithm_version: str | None = None) -> bool:
    """Does this mask describe the cut being edited RIGHT NOW?

    The cut id alone is not enough. A seller who regenerates gets a new image under a new cut
    id, but an id can also be reused by an edit path, so the content hash is checked whenever
    the caller knows it. Painting a previous garment's mask onto a new cut would recolour
    whatever happens to be under those pixels.
    """
    if not isinstance(meta, dict) or meta.get("type") != MASK_KIND:
        return False
    if str(meta.get("sourceCutId") or "") != str(cut_id):
        return False
    if source_hash and str(meta.get("sourceHash") or "") != str(source_hash):
        return False
    if algorithm_version and str(meta.get("algorithmVersion") or "") != str(algorithm_version):
        return False
    return True


async def record(conn, *, user_id: str, project_id: str, cut_id: str, source_asset_id: str,
                 result, category: str | None = None,
                 sub_category: str | None = None, matching_side: str | None = None,
                 match_share: float | None = None) -> dict | None:
    """Create — or reuse — the asset row for one produced mask. Idempotent.

    Idempotency is lookup-before-create on (project, r2Key): the service derives that key
    deterministically from the cut's content hash plus the model and algorithm versions, so a
    dispatcher retry that re-produces the same mask finds the same key and reuses the row.

    A reused row gets its metadata rewritten rather than left alone: the object is the same
    pixels, but the provenance has to say which checks THIS run made. Without that, a mask
    produced before the matching-garment guard would keep its old stamp, be judged stale forever,
    and re-enqueue on every poll.
    """
    if not result.ready or not result.mask_key:
        return None
    meta = metadata_for(
        result, cut_id=cut_id, source_asset_id=source_asset_id,
        category=category, sub_category=sub_category,
        matching_side=matching_side, match_share=match_share)
    existing = await find_by_key(conn, project_id=project_id, r2_key=result.mask_key)
    if existing:
        if (existing.get("metadata") or {}) != meta:
            await set_metadata(conn, asset_id=existing["id"], metadata=meta)
            existing = {**existing, "metadata": meta}
        return existing

    asset_id = str(uuid.uuid4())
    row = await repo.create_asset(
        conn, asset_id=asset_id, user_id=user_id, project_id=project_id,
        source="derived", bucket="", key=result.mask_key, mime="image/png",
        size=result.byte_size, original_filename=None)
    await set_metadata(conn, asset_id=asset_id, metadata=meta)
    log.info("editor garment mask recorded project=%s cut=%s asset=%s key=%s",
             project_id, cut_id, asset_id, result.mask_key)
    return row


async def find_by_key(conn, *, project_id: str, r2_key: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, r2_key, mime_type, byte_size, metadata from assets "
            "where project_id = %s and r2_key = %s and deleted_at is null limit 1",
            (project_id, r2_key))
        return await cur.fetchone()


async def set_metadata(conn, *, asset_id: str, metadata: dict) -> None:
    from psycopg.types.json import Json
    async with conn.cursor() as cur:
        await cur.execute("update assets set metadata = %s where id = %s",
                          (Json(metadata), asset_id))


async def list_for_project(conn, *, project_id: str) -> list[dict]:
    """Every editor mask row for a project, newest first."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, r2_key, mime_type, byte_size, metadata, created_at "
            "from assets where project_id = %s and source = 'derived' and deleted_at is null "
            "and metadata->>'type' = %s order by created_at desc",
            (project_id, MASK_KIND))
        return list(await cur.fetchall())


async def find_for_cut(conn, *, project_id: str, cut_id: str,
                       source_hash: str | None = None) -> dict | None:
    """The current mask row for one cut, or None. Stale rows are skipped, never returned."""
    for row in await list_for_project(conn, project_id=project_id):
        if is_current(row.get("metadata") or {}, cut_id=cut_id, source_hash=source_hash):
            return row
    return None
