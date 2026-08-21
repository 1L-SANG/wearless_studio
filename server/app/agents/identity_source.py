"""컷당 단일 아이덴티티-소스 선택 + 실존 모델 자산 조회 (codex 리뷰 [P1]).

detail_page/editor 워커가 컷 루프 전 1회 소스를 정한다 — 컷마다 아이덴티티 소스가 정확히
하나여야 얼굴 이중주입(라이선스 단일얼굴 + 그리드 동시)이 발생하지 않는다.

  REAL      실존 모델 자산(그리드+face_front, 비공개 버킷) — 라이선스 활성일 때만
  VIRTUAL   가상모델(virtual_models.json, 공개 버킷) — 라이선스 불요
  LEGACY    모델 미선택 기존 step03 단일 얼굴 호환
  NONE      얼굴 없이 생성
  REJECTED  실존 모델 대상인데 라이선스 실패 → 조용한 폴백 금지, 얼굴 미주입

키·바이트는 여기서 다루지 않는다(private 키 미직렬화) — 상위 워커가 refs 로 r2_face 에서 로드.
"""

import uuid


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
