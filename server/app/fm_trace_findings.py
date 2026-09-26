"""자동 출처 추적 발견 원장(fm_trace_findings) 헬퍼 — 순찰·모델 제보·관리자 판정(2026-09-27).

기록 규칙:
  - 순찰: (플랫폼, 상품, 매칭 대상) 하나에 1행. 다시 보이면 last_seen_at·seen_count 만 갱신한다.
    관리자가 이미 "셀러 본인 판매처"로 확인한 (셀러, 플랫폼, 판매처)면 seller_own 으로 자동 분류하고
    슬랙은 건너뛴다(alert_status='skipped').
  - 제보: 제보 1건 = 1행. 매칭이 없어도 남긴다 — 워터마크·지문이 못 잡는 무단 사용(재촬영·딥페이크
    등)일 수 있어 관리자가 직접 봐야 한다.

🔴 네이버 검색 API 특약(2026-09-07 개정) 2.3·2.4: 검색 결과 데이터(가공·파생물 포함)는 "서버 이력
   조회 목적 최대 21일"만 보관한다. 네이버 행은 external_purge_at = last_seen + 21일을 달고,
   purge_expired_external() 이 원문 필드(상품 주소·이미지 주소·상품명·판매처명)를 지운다. 멱등·판매처
   대조 키는 sha256 해시로만 남긴다(원문 복원 불가).
이미지 바이트는 저장하지 않는다(관리자 추적과 같은 정책) — image_sha256 만.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import datetime

from .facemarket import _mask_name

NAVER_RETENTION_DAYS = 21
STATUSES = ("new", "seller_own", "misuse", "dismissed")
SOURCES = ("patrol", "model_report")
_RANK = {"high": 0, "medium": 1, "low": 2}
MAX_CANDIDATES_KEPT = 5


class FindingError(ValueError):
    """관리자 판정 입력 오류. code 는 API 오류 코드로 그대로 쓴다."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _sha(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def dedupe_key(platform: str, product_id: str, target: str, target_id: str) -> str:
    """순찰 멱등 키 — 상품 원문 id 를 남기지 않으려고 해시로(네이버 21일 규칙)."""
    return _sha("finding", platform, str(product_id), target, str(target_id))


def store_key(platform: str, store_id: str | None) -> str | None:
    if store_id is None or not str(store_id).strip():
        return None
    return _sha("store", platform, str(store_id).strip())


def _target_id(c: dict) -> str | None:
    return c.get("publicationId") or c.get("outputRecordId")


def best_candidate(candidates: list[dict], prefer_model_id: str | None = None) -> dict | None:
    """대표 후보 하나. 제보는 제보한 모델의 원장을 먼저 고른다(남의 얼굴 후보보다 의미 있다).
    그 안에서는 trace_image 가 이미 매긴 순위(워터마크 > 거리)를 따른다."""
    if not candidates:
        return None
    if prefer_model_id:
        mine = [c for c in candidates if (c.get("model") or {}).get("id") == prefer_model_id]
        if mine:
            return mine[0]
    return candidates[0]


def candidate_summary(candidates: list[dict]) -> list[dict]:
    """관리자 재확인용 상위 후보 요약(마스킹 전 원문 없음 — id·신뢰도·거리만)."""
    out = []
    for c in candidates[:MAX_CANDIDATES_KEPT]:
        ev = c.get("evidence") or {}
        out.append({
            "target": c.get("target"), "id": _target_id(c), "confidence": c.get("confidence"),
            "watermark": bool(ev.get("watermark")), "phashDistance": ev.get("phashDistance"),
            "modelId": (c.get("model") or {}).get("id"),
            "sellerId": (c.get("seller") or {}).get("id"),
        })
    return out


def _target_columns(c: dict | None) -> dict:
    if not c:
        return {"target": None, "publication_id": None, "output_record_id": None,
                "model_id": None, "seller_id": None, "method": None, "confidence": None,
                "phash_distance": None, "dhash_distance": None}
    ev = c.get("evidence") or {}
    return {
        "target": c.get("target"),
        "publication_id": c.get("publicationId"),
        "output_record_id": c.get("outputRecordId"),
        "model_id": (c.get("model") or {}).get("id"),
        "seller_id": (c.get("seller") or {}).get("id"),
        "method": "watermark" if ev.get("watermark") else "phash",
        "confidence": c.get("confidence"),
        "phash_distance": ev.get("phashDistance"),
        "dhash_distance": ev.get("dhashDistance"),
    }


def _json(v) -> str:
    return json.dumps(v, ensure_ascii=False)


async def _is_known_store(conn, seller_id: str | None, platform: str, skey: str | None) -> bool:
    if not seller_id or not skey:
        return False
    async with conn.cursor() as cur:
        await cur.execute(
            "select 1 from fm_trace_known_stores "
            "where seller_id = %s and platform = %s and store_key = %s",
            (seller_id, platform, skey),
        )
        return await cur.fetchone() is not None


async def record_patrol_finding(
    conn, *, platform: str, product_id: str, product_url: str | None, image_url: str | None,
    title: str | None, store_name: str | None, store_id: str | None, candidate: dict,
    candidates: list[dict], image_sha256: str,
) -> dict:
    """순찰 발견 1건 upsert. 반환 {"id", "inserted", "status"}. 커밋은 호출부 몫."""
    cols = _target_columns(candidate)
    skey = store_key(platform, store_id)
    known = await _is_known_store(conn, cols["seller_id"], platform, skey)
    status = "seller_own" if known else "new"
    # 알리지 않는 것: 이미 아는 셀러 판매처, 그리고 순찰의 '유사도 참고'(low) — 목록엔 남지만 슬랙은
    # 조용히 둔다. 썸네일 수천 장을 대조하는 순찰에서 약한 유사도까지 울리면 알림이 소음이 된다.
    alert_status = "skipped" if known or cols["confidence"] == "low" else "pending"
    purge = (f"now() + interval '{NAVER_RETENTION_DAYS} days'" if platform == "naver"
             else "null")
    async with conn.cursor() as cur:
        await cur.execute(
            f"""insert into fm_trace_findings
                  (source, platform, dedupe_key, product_url, image_url, product_title,
                   store_name, store_key, external_purge_at, target, publication_id,
                   output_record_id, model_id, seller_id, method, confidence, phash_distance,
                   dhash_distance, candidates, image_sha256, status, alert_status)
                values ('patrol', %s, %s, %s, %s, %s, %s, %s, {purge}, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s::jsonb, %s, %s, %s)
                on conflict (dedupe_key) where dedupe_key is not null do update
                   set last_seen_at = now(),
                       seen_count = fm_trace_findings.seen_count + 1,
                       product_url = excluded.product_url,
                       image_url = excluded.image_url,
                       product_title = excluded.product_title,
                       store_name = excluded.store_name,
                       external_purge_at = excluded.external_purge_at,
                       phash_distance = least(fm_trace_findings.phash_distance,
                                              excluded.phash_distance),
                       image_sha256 = excluded.image_sha256
                returning id::text as id, (xmax = 0) as inserted, status""",
            (platform, dedupe_key(platform, product_id, cols["target"], _target_id(candidate)),
             product_url, image_url, title, store_name, skey, cols["target"],
             cols["publication_id"], cols["output_record_id"], cols["model_id"],
             cols["seller_id"], cols["method"], cols["confidence"], cols["phash_distance"],
             cols["dhash_distance"], _json(candidate_summary(candidates)), image_sha256,
             status, alert_status),
        )
        row = await cur.fetchone()
    return {"id": row["id"], "inserted": bool(row["inserted"]), "status": row["status"]}


async def record_model_report(
    conn, *, reporter_model_id: str, reporter_user_id: str, page_url: str | None,
    note: str | None, image_sha256: str, candidates: list[dict],
) -> dict:
    """모델 제보 1건. 매칭이 없어도 행을 남긴다. 반환 {"id", "matched"}. 커밋은 호출부 몫."""
    best = best_candidate(candidates, prefer_model_id=reporter_model_id)
    cols = _target_columns(best)
    async with conn.cursor() as cur:
        await cur.execute(
            """insert into fm_trace_findings
                  (source, platform, target, publication_id, output_record_id, model_id,
                   seller_id, method, confidence, phash_distance, dhash_distance, candidates,
                   image_sha256, reporter_model_id, reporter_user_id, report_page_url,
                   report_note)
                values ('model_report', 'report', %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb, %s, %s, %s, %s, %s)
                returning id::text as id""",
            (cols["target"], cols["publication_id"], cols["output_record_id"],
             cols["model_id"], cols["seller_id"], cols["method"], cols["confidence"],
             cols["phash_distance"], cols["dhash_distance"], _json(candidate_summary(candidates)),
             image_sha256, reporter_model_id, reporter_user_id, page_url, note),
        )
        row = await cur.fetchone()
    return {"id": row["id"], "matched": best is not None}


async def purge_expired_external(conn) -> int:
    """네이버 원문 필드 21일 삭제(특약 2.4). 지운 행 수. 커밋은 호출부 몫."""
    async with conn.cursor() as cur:
        await cur.execute(
            """update fm_trace_findings
                  set product_url = null, image_url = null, product_title = null,
                      store_name = null, external_purge_at = null
                where external_purge_at is not null and external_purge_at <= now()"""
        )
        return cur.rowcount or 0


# ---------- 관리자 목록·판정 ----------

_LIST_SQL = """
select f.id::text as id, f.source, f.platform, f.product_url, f.image_url, f.product_title,
       f.store_name, f.external_purge_at, f.target, f.publication_id::text as publication_id,
       f.output_record_id::text as output_record_id, f.model_id::text as model_id,
       f.seller_id::text as seller_id, f.method, f.confidence, f.phash_distance,
       f.dhash_distance, f.candidates, f.report_page_url, f.report_note, f.status,
       f.first_seen_at, f.last_seen_at, f.seen_count, f.alert_status,
       (f.store_key is not null) as has_store,
       m.display_name as model_name, rm.display_name as reporter_model_name,
       u.email as seller_email, pf.display_name as seller_name,
       l.status as license_status, p.revoked_at as publication_revoked_at
  from fm_trace_findings f
  left join fm_models m on m.id = f.model_id
  left join fm_models rm on rm.id = f.reporter_model_id
  left join auth.users u on u.id = f.seller_id
  left join profiles pf on pf.user_id = f.seller_id
  left join fm_publication_records p on p.id = f.publication_id
  left join fm_output_records o on o.id = f.output_record_id
  left join fm_licenses l on l.id = coalesce(p.license_id, o.license_id)
 where (%(status)s::text is null or f.status = %(status)s)
   and (%(source)s::text is null or f.source = %(source)s)
   and (%(cursor_seen)s::timestamptz is null
        or (f.last_seen_at, f.id) < (%(cursor_seen)s::timestamptz, %(cursor_id)s::uuid))
 order by f.last_seen_at desc, f.id desc
 limit %(limit)s
"""


def _mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, domain = email.split("@", 1)
    return f"{local[:2]}***@{domain}"


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _row_view(r: dict) -> dict:
    return {
        "id": r["id"], "source": r["source"], "platform": r["platform"],
        "productUrl": r.get("product_url"), "imageUrl": r.get("image_url"),
        "productTitle": r.get("product_title"), "storeName": r.get("store_name"),
        "externalPurgeAt": _iso(r.get("external_purge_at")),
        "target": r.get("target"), "publicationId": r.get("publication_id"),
        "outputRecordId": r.get("output_record_id"),
        "model": {"id": r.get("model_id"), "displayName": r.get("model_name")},
        "reporterModelName": r.get("reporter_model_name"),
        "seller": {
            "id": r.get("seller_id"),
            "emailMasked": _mask_email(r.get("seller_email")),
            "nameMasked": _mask_name(r["seller_name"]) if r.get("seller_name") else None,
        },
        "license": {"status": r.get("license_status")},
        "publicationRevokedAt": _iso(r.get("publication_revoked_at")),
        "method": r.get("method"), "confidence": r.get("confidence"),
        "phashDistance": r.get("phash_distance"), "dhashDistance": r.get("dhash_distance"),
        "candidates": r.get("candidates") or [],
        "reportPageUrl": r.get("report_page_url"), "reportNote": r.get("report_note"),
        "status": r["status"], "alertStatus": r.get("alert_status"),
        "canRememberStore": bool(r.get("has_store") and r.get("seller_id")
                                 and r["platform"] in ("naver", "zigzag")),
        "firstSeenAt": _iso(r.get("first_seen_at")), "lastSeenAt": _iso(r.get("last_seen_at")),
        "seenCount": r.get("seen_count"),
    }


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    if not cursor:
        return None, None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        seen_raw, fid = raw.split("|", 1)
        seen = datetime.fromisoformat(seen_raw)
        if seen.utcoffset() is None:
            raise ValueError("timezone required")
        return seen, str(uuid.UUID(fid))
    except (ValueError, UnicodeError):
        raise FindingError("invalid_cursor", "목록 위치 정보가 올바르지 않아요.") from None


async def list_findings(conn, *, status: str | None, source: str | None, limit: int,
                        cursor: str | None) -> dict:
    if status in ("", "all"):
        status = None
    if status is not None and status not in STATUSES:
        raise FindingError("invalid_status", "발견 상태 값이 올바르지 않아요.")
    if source in ("", "all"):
        source = None
    if source is not None and source not in SOURCES:
        raise FindingError("invalid_source", "발견 출처 값이 올바르지 않아요.")
    seen, fid = _decode_cursor(cursor)
    async with conn.cursor() as cur:
        await cur.execute(_LIST_SQL, {"status": status, "source": source, "cursor_seen": seen,
                                      "cursor_id": fid, "limit": limit + 1})
        rows = await cur.fetchall() or []
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        next_cursor = base64.urlsafe_b64encode(
            f"{last['last_seen_at'].isoformat()}|{last['id']}".encode()).decode()
    return {"items": [_row_view(r) for r in page], "nextCursor": next_cursor}


async def update_finding_status(conn, *, finding_id: str, actor: str, status: str,
                                remember_store: bool, write_audit) -> dict:
    """관리자 판정. seller_own + remember_store 면 (셀러, 플랫폼, 판매처 해시)를 기억한다."""
    try:
        finding_id = str(uuid.UUID(str(finding_id)))
    except ValueError:
        raise FindingError("invalid_finding_id", "발견 번호가 올바르지 않아요.") from None
    if status not in STATUSES:
        raise FindingError("invalid_status", "발견 상태 값이 올바르지 않아요.")
    async with conn.cursor() as cur:
        await cur.execute(
            "select status, platform, seller_id::text as seller_id, store_key "
            "from fm_trace_findings where id = %s for update",
            (finding_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise FindingError("not_found", "발견 기록을 찾을 수 없어요.", status=404)
        remembered = False
        if remember_store:
            if status != "seller_own":
                raise FindingError("remember_requires_seller_own",
                                   "셀러 본인 판매처로 판정할 때만 기억할 수 있어요.")
            if not (row["seller_id"] and row["store_key"]
                    and row["platform"] in ("naver", "zigzag")):
                raise FindingError("store_unknown", "이 발견에는 판매처 정보가 없어요.")
            await cur.execute(
                "insert into fm_trace_known_stores "
                "(seller_id, platform, store_key, source_finding_id, created_by) "
                "values (%s, %s, %s, %s, %s) on conflict do nothing",
                (row["seller_id"], row["platform"], row["store_key"], finding_id, actor),
            )
            remembered = True
        if row["status"] != status:
            await cur.execute("update fm_trace_findings set status = %s where id = %s",
                              (status, finding_id))
    if write_audit is not None:
        await write_audit(
            conn, actor_user_id=actor, action="facemarket.trace_finding.status",
            target_type="trace_finding", target_id=finding_id,
            before={"status": row["status"]},
            after={"status": status, "storeRemembered": remembered},
        )
    return {"id": finding_id, "status": status, "storeRemembered": remembered}
