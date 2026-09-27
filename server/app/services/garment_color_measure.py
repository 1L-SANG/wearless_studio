"""가로 세트 벽 색('옷 색에 맞춤')용 옷 색 지연 측정.

상품 분석이 아니라 셀러가 스토리보드를 떠날 때, 필요한 색만 잰다. 상세 생성 잡도 그
측정이 끝나지 않았으면 한 번 백스톱으로 부른다. 측정 소스는 생성 검증과 같은 규칙
(색마다 첫 Front·첫 Back 원본)이라 horizon_background.resolve 의 바이트 대조를 그대로 통과한다.
"""
import asyncio
import logging

from .. import repo
from ..agents import content_roles, cut_generator, garment_color_evidence, garment_color_observer, horizon_background
from ..agents.gemini_image import InlineImage, run_cpu_bound

log = logging.getLogger("wearless.garment_color_measure")

# 같은 프로세스에서 겹친 호출(두 번 누르기 등)은 한 줄로 세운다. 프로세스 사이는 CAS 가 막는다.
# 값은 [잠금, 잡았거나 기다리는 호출 수]. 수가 0 이 되면 지워 프로젝트마다 쌓이지 않게 한다.
_locks: dict[str, list] = {}


def _clothing_type(product: dict) -> str:
    # horizon_background.resolve 와 같은 식이어야 계약의 clothingType 이 맞는다.
    return product.get("clothingType") or product.get("clothing_type") or "top"


def measurable_color_ids(blocks: list, product: dict) -> list[str]:
    """측정이 필요한 색 중 정면·뒷면 사진이 있는 색만. 사진이 없는 색은 재도 행이 안 생긴다."""
    return [cid for cid in horizon_background.garment_tone_color_ids(blocks, product)
            if any(slot in {"Front", "Back"} for slot, _ in cut_generator.color_images(product, cid))]


def is_covered(contract, color_ids: list[str], clothing_type: str) -> bool:
    """저장된 근거가 필요한 색을 전부 덮는가. 저장된 행은 준비·불가 모두 입력이 바뀔 때까지 최종이다."""
    try:
        contract = garment_color_evidence.validate_contract(contract)
    except (ValueError, TypeError):
        return False
    if contract["clothingType"] != clothing_type:
        return False
    rows = {row["colorId"] for row in contract["colors"]}
    bound = {row["colorId"] for row in contract["sourceBindings"]}
    return all(cid in rows and cid in bound for cid in color_ids)


async def ensure_garment_color_evidence(
    settings, pool, r2, *, user_id: str, project_id: str,
    blocks: list | None = None, product: dict | None = None, analysis: dict | None = None,
) -> tuple[str, dict | None]:
    """(status, 계약 또는 None). status 는 skipped, measured, unavailable 중 하나.

    관찰기·R2·이미지 처리 실패는 예외 대신 unavailable 이고, 이때는 아무것도 저장하지
    않아 다음 이탈이나 생성 잡이 다시 잰다.
    """
    entry = _locks.setdefault(project_id, [asyncio.Lock(), 0])
    entry[1] += 1
    try:
        async with entry[0]:
            return await _ensure(settings, pool, r2, user_id=user_id, project_id=project_id,
                                 blocks=blocks, product=product, analysis=analysis)
    finally:
        entry[1] -= 1
        if not entry[1] and _locks.get(project_id) is entry:
            del _locks[project_id]


async def _ensure(settings, pool, r2, *, user_id, project_id, blocks, product, analysis):
    if blocks is None or product is None or analysis is None:
        async with pool.connection() as conn:
            if product is None:
                product = await repo.get_product(conn, project_id) or {}
            if analysis is None:
                analysis = await repo.get_analysis(conn, project_id) or {}
            if blocks is None:
                blocks = content_roles.canonicalize_storyboard(await repo.get_storyboard(conn, project_id))
    existing = analysis.get(garment_color_evidence.PERSISTED_KEY)
    clothing_type = _clothing_type(product)
    needed = measurable_color_ids(blocks, product)
    if not needed or is_covered(existing, needed, clothing_type):
        return "skipped", existing

    # 소스 선택은 생성 쪽 검증(color_images, get_asset_for_user, loaded_sources)과 똑같이 한다.
    selected = []
    async with pool.connection() as conn:
        for cid in needed:
            firsts, seen = [], set()
            for slot, aid in cut_generator.color_images(product, cid):
                if slot not in {"Front", "Back"} or slot in seen:
                    continue
                asset = await repo.get_asset_for_user(conn, user_id, aid)
                if asset:
                    seen.add(slot)
                    firsts.append({**asset, "slot": slot})
            if firsts:
                selected.append((cid, firsts))
    # 원본이 하나도 안 남은 색(지워졌거나 남의 자산)은 재도 행이 안 생긴다. 빼고 다시 덮였는지
    # 본다. 안 빼면 그 색 때문에 이탈·생성 잡마다 나머지 색을 다시 잰다.
    needed = [cid for cid, _ in selected]
    if not needed or is_covered(existing, needed, clothing_type):
        return "skipped", existing
    # 커넥션을 놓은 채 원본을 받는다. 바인딩 해시는 원본 바이트여야 생성이 다시 맞춰 본다.
    try:
        downloaded = await asyncio.gather(*(
            asyncio.to_thread(r2.get_bytes, asset["r2_key"]) for _, rows in selected for asset in rows))
    except Exception as exc:
        log.warning("garment color sources unavailable for %s: %s", project_id, type(exc).__name__)
        return "unavailable", existing
    sources, offset = [], 0
    for cid, rows in selected:
        images = [InlineImage(asset["mime_type"], data)
                  for asset, data in zip(rows, downloaded[offset:offset + len(rows)], strict=True)]
        offset += len(rows)
        for row in horizon_background.loaded_sources(product, cid, rows, images):
            sources.append({**row, "sourceIndex": len(sources)})
    if not sources:
        return "unavailable", existing

    try:
        regions = await asyncio.wait_for(
            garment_color_observer.observe(settings, sources, product=product),
            timeout=settings.analysis_aux_timeout_seconds)
    except Exception as exc:
        log.warning("garment color observer unavailable for %s: %s", project_id, type(exc).__name__)
        return "unavailable", existing
    try:
        contract = await run_cpu_bound(
            garment_color_evidence.build_contract, regions, sources, clothing_type=clothing_type)
    except (ValueError, TypeError) as exc:
        log.warning("garment color contract unavailable for %s: %s", project_id, type(exc).__name__)
        return "unavailable", existing

    async with pool.connection() as conn:
        saved = await repo.replace_garment_color_evidence(
            conn, project_id, contract, expected=existing,
            identity=repo.garment_color_input_identity(product))
        await conn.commit()
        if saved:
            return "measured", contract
        # CAS 에서 졌으면 다른 프로세스(API 이탈 측정과 워커 백스톱)가 먼저 썼을 수 있다.
        # 지금 값이 필요한 색을 덮으면 그걸 쓴다.
        current = (await repo.get_analysis(conn, project_id)).get(garment_color_evidence.PERSISTED_KEY)
    return ("skipped", current) if is_covered(current, needed, clothing_type) else ("unavailable", current)
