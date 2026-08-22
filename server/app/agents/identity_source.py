"""컷당 단일 아이덴티티-소스 선택 + 실존 모델 자산 조회 (codex 리뷰 [P1]).

detail_page/editor 워커가 컷 루프 전 1회 소스를 정한다 — 컷마다 아이덴티티 소스가 정확히
하나여야 얼굴 이중주입(라이선스 단일얼굴 + 그리드 동시)이 발생하지 않는다.

  REAL      실존 모델 자산(그리드+face_front, 비공개 버킷) — 라이선스 활성일 때만
  VIRTUAL   가상모델(virtual_models.json, 공개 버킷) — 라이선스 불요
  LEGACY    모델 미선택 기존 step03 단일 얼굴 호환
  NONE      얼굴 없이 생성
  REJECTED  실존 모델 대상인데 라이선스 실패 → 조용한 폴백 금지, 얼굴 미주입

키·바이트는 여기서 다루지 않는다(private 키 미직렬화) — 상위 워커가 refs 로 r2_face 에서 로드.

assets_source_hash 무결성: fm_model_asset_job 이 자산 빌드 시점에 소스 사진(front/
angle45/side)의 지문을 fm_models.assets_source_hash 에 새긴다. resolve_real_model_assets
는 fm_biometric_enrollment_photos(현재 소스, 승인 후 불변)에서 동일 알고리즘으로 재계산해
비교한다 — 사진 바이트를 다시 읽지 않고 이미 저장된 image_digest 문자열만 사용하는 저비용
비교. 불일치(=DB 변조·데이터 유실 등 진짜 stale)면 fail-closed(None). 두 계산이 동일 함수를
쓰므로 정상 상태에서는 false-reject 가 나올 수 없다.
"""

import hashlib
import uuid


_ANGLES = ("front", "angle45", "side")


def compute_assets_source_hash(faces: list[dict]) -> str:
    """fm_model_asset_job._source_hash 와 동일 알고리즘(단일 진실 원천).

    faces 는 front/angle45/side 순으로 정렬된 리스트, 각 항목은 image_digest
    (우선) 또는 r2_key 를 갖는다. 두 계산부가 다른 구현을 쓰면 false-reject
    위험이 생기므로 이 함수 하나만 공유한다.
    """
    return hashlib.sha256(
        "|".join(
            str(face.get("image_digest") or face.get("r2_key") or "")
            for face in faces
        ).encode()
    ).hexdigest()


def select_source(*, selected_model_id, license_row, has_real_assets: bool,
                  has_license_face: bool) -> str:
    """컷 루프 전 1회 호출. 반환: REAL|VIRTUAL|LEGACY|NONE|REJECTED."""
    try:
        uuid.UUID(str(selected_model_id))
    except (TypeError, ValueError):
        is_real = False
    else:
        is_real = True
    if is_real:
        if (
            has_real_assets
            and license_row
            and str(license_row.get("model_id")) == str(selected_model_id)
            and license_row.get("status") == "active"
            and license_row.get("model_status") == "verified"
        ):
            return "REAL"
        return "REJECTED"
    if selected_model_id:
        return "VIRTUAL"
    if has_license_face:
        return "LEGACY"
    return "NONE"


async def resolve_real_model_assets(
    conn,
    model_id: str,
    *,
    enrollment_id: str,
    evidence_version: str,
) -> list[dict] | None:
    """Return the exact two private refs pinned to current passed evidence."""
    try:
        uuid.UUID(str(model_id))
    except (TypeError, ValueError):
        return None
    async with conn.cursor() as cur:
        await cur.execute(
            "select m.status as model_status, m.assets_status, "
            "m.current_enrollment_id::text as current_enrollment_id, "
            "m.assets_source_hash, "
            "e.status as enrollment_status, e.match_policy_version, "
            "a.view, a.r2_key, a.mime, a.bucket, "
            "a.source_enrollment_id::text as source_enrollment_id, a.evidence_version "
            "from fm_models m "
            "left join fm_biometric_enrollments e "
            "on e.id = m.current_enrollment_id and e.model_id = m.id "
            "left join fm_model_assets a on a.model_id = m.id "
            "where m.id = %s",
            (model_id,))
        rows = await cur.fetchall()
    if not rows:
        return None
    state = rows[0]
    current_enrollment_id = str(state.get("current_enrollment_id") or "")
    policy_version = str(state.get("match_policy_version") or "").strip()
    if (
        state.get("model_status") != "verified"
        or state.get("assets_status") != "ready"
        or state.get("enrollment_status") != "passed"
        or current_enrollment_id != str(enrollment_id)
        or not policy_version
        or policy_version != str(evidence_version)
    ):
        return None
    # assets_source_hash 무결성: 사진 바이트가 아니라 이미 저장된 image_digest 문자열만
    # 읽는 저비용 재계산 — 승인된 사진 행은 이 시점 이후 절대 갱신되지 않으므로(업로드/삭제
    # 는 photos_pending/liveness_pending 단계에서만 허용) 정상 상태에선 항상 일치한다.
    async with conn.cursor() as cur:
        await cur.execute(
            "select angle, image_digest, r2_key "
            "from fm_biometric_enrollment_photos "
            "where enrollment_id = %s",
            (enrollment_id,))
        photo_rows = await cur.fetchall()
    by_angle = {row.get("angle"): row for row in photo_rows}
    if set(by_angle) != set(_ANGLES):
        return None
    current_source_hash = compute_assets_source_hash(
        [by_angle[angle] for angle in _ANGLES]
    )
    if current_source_hash != str(state.get("assets_source_hash") or ""):
        return None
    by_view = {r["view"]: r for r in rows if r.get("view")}
    out = []
    for view in ("face_front", "grid_sedcard"):
        r = by_view.get(view)
        if (
            not r
            or not str(r.get("r2_key") or "").strip()
            or r.get("bucket") != "face"
            or not str(r.get("mime") or "").startswith("image/")
            or str(r.get("source_enrollment_id") or "") != str(enrollment_id)
            or r.get("evidence_version") != evidence_version
            or r.get("evidence_version") != policy_version
        ):
            return None
        out.append({"key": r["r2_key"], "mime": r["mime"], "bucket": r["bucket"]})
    return out
