"""컷당 단일 아이덴티티-소스 선택 + 실존 모델 자산 조회 (codex 리뷰 [P1]).

detail_page/editor 워커가 컷 루프 전 1회 소스를 정한다 — 컷마다 아이덴티티 소스가 정확히
하나여야 얼굴 이중주입(라이선스 단일얼굴 + 그리드 동시)이 발생하지 않는다.

  REAL      실존 모델 자산(그리드+face_front, 비공개 버킷) — 라이선스 활성일 때만
  VIRTUAL   가상모델(virtual_models.json, 공개 버킷) — 라이선스 불요
  LEGACY    기존 step03 단일 라이선스 얼굴(그리드 자산 없는 모델 폴백)
  NONE      얼굴 없이 생성
  REJECTED  실존 모델 대상인데 라이선스 실패 → 조용한 폴백 금지, 얼굴 미주입

키·바이트는 여기서 다루지 않는다(private 키 미직렬화) — 상위 워커가 refs 로 r2_face 에서 로드.
"""


def select_source(*, selected_model_id, license_row, has_real_assets: bool,
                  has_license_face: bool) -> str:
    """컷 루프 전 1회 호출. 반환: REAL|VIRTUAL|LEGACY|NONE|REJECTED."""
    if has_real_assets:
        if (license_row
                and str(license_row.get("model_id")) == str(selected_model_id)
                and license_row.get("status") == "active"):
            return "REAL"
        return "REJECTED"  # 무라이선스 실얼굴 차단 — 다른 소스로 폴백하지 않는다
    if selected_model_id:
        return "VIRTUAL"
    if has_license_face:
        return "LEGACY"
    return "NONE"


async def resolve_real_model_assets(conn, model_id: str):
    """등록된 실존 모델 자산을 계약 순서(face_front, grid_sedcard)로 반환.

    assets_status='ready' 이고 두 뷰가 모두 유효할 때만 refs 리스트. 아니면 None(→ VIRTUAL/폴백).
    각 ref = {key, mime, bucket}. bucket='face' 면 워커가 r2_face(비공개)에서 로드한다.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "select m.assets_status, a.view, a.r2_key, a.mime, a.bucket "
            "from fm_models m left join fm_model_assets a on a.model_id = m.id "
            "where m.id = %s",
            (model_id,))
        rows = await cur.fetchall()
    if not rows or rows[0]["assets_status"] != "ready":
        return None
    by_view = {r["view"]: r for r in rows if r.get("view")}
    out = []
    for view in ("face_front", "grid_sedcard"):
        r = by_view.get(view)
        if not r or not r.get("r2_key") or not str(r.get("mime") or "").startswith("image/"):
            return None
        out.append({"key": r["r2_key"], "mime": r["mime"], "bucket": r.get("bucket") or "face"})
    return out
