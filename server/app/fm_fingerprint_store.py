"""추적 층 DB 헬퍼 — 워터마크 코드 발급·배포본 표식 기록·컷 지문 기록·지문 적재(2026-09-26).

전부 **베스트에포트**다. 이 모듈이 실패해도 배포본 서명·컷 종결은 그대로 끝나야 한다 — 추적은
나중에 붙이는 증빙이지 셀러의 결과물을 붙잡는 게이트가 아니다. 마이그레이션이 아직 안 붙은
배포(컬럼·테이블 없음)에서도 조용히 빠진다.

지문 값: 부호 없는 64비트를 Postgres bigint 에 넣으려고 to_signed 로 변환해 저장한다.
"""

from __future__ import annotations

import logging

from psycopg.errors import UniqueViolation

from .services import fm_fingerprint, fm_watermark

log = logging.getLogger("facemarket.fingerprint_store")

_CODE_ATTEMPTS = 5

_INSERT_PUBLICATION_FP = (
    "insert into fm_image_fingerprints "
    "(publication_id, kind, region_y0, region_y1, phash, dhash) "
    "values (%s, %s, %s, %s, %s, %s) on conflict do nothing"
)
_INSERT_CUT_FP = (
    "insert into fm_image_fingerprints (output_record_id, kind, phash, dhash) "
    "select r.id, 'cut', %s, %s from fm_output_records r where r.asset_id = %s "
    "on conflict do nothing"
)
# 쇼핑몰 썸네일 크롭 변형(2026-09-27) — fm_fingerprint.cut_fingerprints 참고.
_INSERT_CUT_CROP_FP = (
    "insert into fm_image_fingerprints (output_record_id, kind, region_y0, region_y1, phash, dhash) "
    "select r.id, 'cut_crop', %s, %s, %s, %s from fm_output_records r where r.asset_id = %s "
    "on conflict do nothing"
)


async def allocate_wm_code(conn, publication_id: str) -> int | None:
    """배포본 워터마크 코드(32비트) 발급. 이미 있으면 그 값(재시도 멱등). 실패하면 None.

    unique 인덱스(fm_publication_records_wm_code_key)가 충돌을 잡고, 충돌이면 새로 뽑는다 —
    2^32 공간이라 수만 건에서도 재시도가 거의 안 일어난다.
    """
    for _ in range(_CODE_ATTEMPTS):
        candidate = fm_watermark.new_code()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "update fm_publication_records set wm_code = coalesce(wm_code, %s) "
                    "where id = %s returning wm_code",
                    (candidate, publication_id),
                )
                row = await cur.fetchone()
            await conn.commit()
        except UniqueViolation:
            await conn.rollback()
            continue
        except Exception:
            log.warning("wm code allocation failed publication=%s", publication_id, exc_info=True)
            await conn.rollback()
            return None
        if row is None or row.get("wm_code") is None:
            return None
        return int(row["wm_code"])
    log.warning("wm code allocation exhausted publication=%s", publication_id)
    return None


async def save_publication_mark(
    conn, publication_id: str, *, wm_status: str, wm_sha256: str | None, fingerprints: list[dict]
) -> bool:
    """표식 결과(wm_status·wm_sha256) + 배포본 지문을 한 트랜잭션으로. 실패하면 False.

    호출부는 이걸 **앵커 큐 insert 보다 먼저** 커밋한다 — 앵커 워커가 coalesce(wm_sha256,
    image_sha256) 를 읽으므로, 순서가 뒤집히면 워터마크 전 해시가 체인에 올라갈 수 있다.
    """
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "update fm_publication_records set wm_status = %s, wm_sha256 = %s where id = %s",
                (wm_status, wm_sha256, publication_id),
            )
            if fingerprints:
                await cur.executemany(_INSERT_PUBLICATION_FP, [
                    (publication_id, fp["kind"], fp.get("region_y0"), fp.get("region_y1"),
                     fm_fingerprint.to_signed(fp["phash"]), fm_fingerprint.to_signed(fp["dhash"]))
                    for fp in fingerprints
                ])
        await conn.commit()
        return True
    except Exception:
        log.warning("publication mark save failed publication=%s", publication_id, exc_info=True)
        await conn.rollback()
        return False


async def record_cut_fingerprints(pool, items: list[dict]) -> int:
    """REAL 컷 지문 기록 — finalize 커밋 **뒤에** 부른다(컷 종결의 임계 경로 밖).

    items = [{"asset_id", "phash", "dhash"}]. fm_output_records 행이 있는 컷만 들어간다(select
    조인) — lease 를 잃어 원장 행이 안 생긴 컷은 자연히 빠진다. 절대 raise 하지 않는다. 빠진 것은
    scripts/fm_fingerprint_backfill.py 가 채운다.
    """
    rows = [
        (fm_fingerprint.to_signed(i["phash"]), fm_fingerprint.to_signed(i["dhash"]), i["asset_id"])
        for i in items if i.get("asset_id") and i.get("phash") is not None
    ]
    crop_rows = [
        (c["region_y0"], c["region_y1"], fm_fingerprint.to_signed(c["phash"]),
         fm_fingerprint.to_signed(c["dhash"]), i["asset_id"])
        for i in items if i.get("asset_id") and i.get("phash") is not None
        for c in i.get("crops") or []
    ]
    if not rows or pool is None:
        return 0
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(_INSERT_CUT_FP, rows)
                if crop_rows:
                    await cur.executemany(_INSERT_CUT_CROP_FP, crop_rows)
            await conn.commit()
        return len(rows)
    except Exception:
        log.warning("cut fingerprint record failed (%d rows)", len(rows), exc_info=True)
        return 0


def cut_fingerprint_items(cut_assets: list[dict]) -> list[dict]:
    """finalize 에 넘긴 컷 dict 중 원장(provenance)이 붙고 지문이 계산된 것만."""
    return [
        {"asset_id": c["asset_id"], **c["fingerprint"]}
        for c in cut_assets
        if c.get("provenance") and c.get("fingerprint") and c.get("asset_id")
    ]


async def load_fingerprints(conn) -> list[dict]:
    """전수 적재(관리자 추적 전용). 규모 메모는 fm_fingerprint.search 참고."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, publication_id::text as publication_id, "
            "output_record_id::text as output_record_id, kind, region_y0, region_y1, "
            "phash, dhash from fm_image_fingerprints"
        )
        return list(await cur.fetchall() or [])
