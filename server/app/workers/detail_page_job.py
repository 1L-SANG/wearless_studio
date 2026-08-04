"""PL-4 상세페이지 생성 워커. AG-06 컷 → AG-02 카피 → AG-03 검수 → M-02 조립 → EditorBlock[].

저장 콘티(projects.storyboard)의 source='ai' 블록별로 AG-06 컷 이미지를 생성(실패 컷은 빈 슬롯,
전체 중단 없음·미차감), copywriting 이면 블록별 AG-02 카피 + 묶음 AG-03 검수, page_assembler(M-02)
로 EditorBlock[] 조립. 크레딧: 성공 컷 수 × storyboardPerCut 만 confirm(부분 성공). lease 펜스.
"""

import asyncio
import json
import logging
import uuid
from io import BytesIO

from PIL import Image

from .. import repo
from ..agents import (
    content_roles,
    copy_qc,
    copywriter,
    cut_generator,
    image_qc,
    mannequin,
    page_assembler,
    space_set_assets,
)
from ..agents import page_source_assets
from ..agents.gemini_image import InlineImage
from ..agents.vision_llm import VisionError
from ..r2 import IMMUTABLE_CACHE, ai_key, ext_for_mime
from ._common import emit_job_event as _emit

log = logging.getLogger("wearless.detail_page_job")

_EXT_FALLBACK = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
# 컷·카피 동시 생성 상한. 순차(블록 수 × ~40s)면 4컷에 2~3분 → 병렬로 단축. gemini 버스트
# 제한을 감안해 무제한이 아닌 소폭 동시성(429 시 이 값을 낮춘다).
_GEN_CONCURRENCY = 3
_WORN_CUT_TYPES = ("styling", "horizon", "mirror")
_BASELINE_FAILURES = {
    "no_approved_baseline": "정면 마네킹 이미지를 먼저 확정해 주세요.",
    "baseline_changed": "승인된 기준 이미지가 바뀌었어요. 상세페이지 생성을 다시 시작해 주세요.",
    "baseline_asset_missing": "승인된 기준 이미지를 불러오지 못했어요. 다시 확정해 주세요.",
}


def _cut_type(block: dict) -> str | None:
    value = block.get("cutType") or block.get("cut_type")
    return str(value) if value is not None else None


def _dims(data: bytes):
    try:
        im = Image.open(BytesIO(data))
        return im.width, im.height
    except Exception:
        return None, None


async def _load_license_face(app, conn, project: dict) -> dict | None:
    """프로젝트에 잠긴 얼굴 라이선스의 얼굴 이미지 → {image, license_id, model_name}. 없으면 None.

    FM-31 "라이선스 얼굴이 실제 상세컷에 나오게" 의 입력 로더. **잠금이 없으면 쿼리조차 돌지
    않는다** — 라이선스 없는 기존 셀러 경로는 이 함수가 즉시 None 이라 완전 무변경.

    verify-before-use 재확인: 게이트(routes.generate_detail_page)는 **요청 시점**에만 검증하므로
    그 뒤 해지·만료된 라이선스가 큐에 남을 수 있다. 얼굴은 한 번 생성되면 공개 URL 로 나가
    회수가 불가능하므로 워커에서 status/만료를 한 번 더 본다(게이트와 같은 판정 함수 _is_expired).

    실패(r2_face 미설정·해지·만료·dangling 키)는 잡을 죽이지 않고 **얼굴 없이 생성**으로 강등한다:
    상세페이지는 부분 성공 계약이고, 얼굴 게이트(get_license_face)도 같은 상황을 404 로
    우아하게 강등한다. 강등 시 AI 고지도 기본 문구로 돌아가므로 허위 고지가 생기지 않는다.
    로그에 얼굴 바이트·R2 키·digest 를 남기지 않는다(PII 룰).
    """
    s = app.state.settings
    lic_id = project.get("facemarket_license_id") or project.get("facemarketLicenseId")
    if not s.facemarket_enabled or not lic_id:
        return None
    r2_face = getattr(app.state, "r2_face", None)
    if r2_face is None:  # 얼굴=생체 PII → 공개 버킷 폴백 금지(개인화 워커 선례)
        log.warning("facemarket face skipped (no face storage) license %s", lic_id)
        return None
    # 지연 import — facemarket 모듈과의 순환 참조 회피(정산 훅 선례와 동일).
    from ..facemarket import _EXT_TO_MIME, _is_expired, _mask_name

    async with conn.cursor() as cur:
        await cur.execute(
            """select l.face_image_key, l.status, l.license_valid_until, m.display_name
               from fm_licenses l join fm_models m on m.id = l.model_id
               where l.id = %s""",
            (str(lic_id),),
        )
        lic = await cur.fetchone()
    if not lic or not lic["face_image_key"]:
        return None
    if lic["status"] != "active" or _is_expired(lic):
        log.warning("facemarket face skipped (license %s status=%s)", lic_id, lic["status"])
        return None
    key = lic["face_image_key"]
    mime = _EXT_TO_MIME.get(key.rsplit(".", 1)[-1].lower())
    if not mime:  # 키 확장자 역매핑 실패(fm_licenses 에 mime 컬럼 부재) — 얼굴 없이 생성
        return None
    try:
        data = await asyncio.to_thread(r2_face.get_bytes, key)
    except Exception:  # 개인화 파기로 얼굴 객체만 지워진 dangling 키 등
        log.warning("facemarket face skipped (object unavailable) license %s", lic_id)
        return None
    return {
        "image": InlineImage(mime, data),
        "license_id": str(lic_id),
        "model_name": _mask_name(lic["display_name"] or ""),
    }


async def _load_license_row(app, conn, project) -> dict | None:
    """프로젝트에 잠긴 라이선스의 게이트용 메타(id·model_id·상태·마스킹 이름). 얼굴 바이트는 로드하지
    않는다 — 실존 모델(REAL) 소스 선택·검증 배지 근거로만 쓴다. 활성·미만료가 아니면 None."""
    s = app.state.settings
    lic_id = project.get("facemarket_license_id") or project.get("facemarketLicenseId")
    if not s.facemarket_enabled or not lic_id:
        return None
    from ..facemarket import _is_expired, _mask_name
    async with conn.cursor() as cur:
        await cur.execute(
            """select l.id::text as id, l.model_id::text as model_id, l.status,
                      l.license_valid_until, m.display_name
               from fm_licenses l join fm_models m on m.id = l.model_id
               where l.id = %s""",
            (str(lic_id),))
        lic = await cur.fetchone()
    if not lic or lic["status"] != "active" or _is_expired(lic):
        return None
    return {"id": lic["id"], "model_id": lic["model_id"], "status": lic["status"],
            "model_name": _mask_name(lic["display_name"] or "")}


async def _gen_cuts(app, job, prepared, product, analysis):
    """준비된 블록별
    (block, images, manifest, has_face, product_images,
    space_set_plate, strict_space_scene_qc)로 AG-06 컷 생성
    → (cut_results, cut_assets, face_cuts, garment_qcs, garment_warnings).
    face_cuts = 라이선스 얼굴이 실제로 들어가고
    **성공까지 한** 컷 수 — AI 고지 문구 분기의 사실 근거(주입 0건이면 기본 문구).
    실패 컷은 건너뛴다(빈 슬롯은 assemble 이 처리) — 부분 성공. 스펙 위반(unknown cutType)도
    같은 경로(빈 슬롯) — 조용한 styling 대체 렌더는 하지 않는다(ADR-0004)."""
    s, gemini, r2 = app.state.settings, app.state.gemini, app.state.r2
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    sem = asyncio.Semaphore(_GEN_CONCURRENCY)

    async def _one(item):
        """컷 1개 생성+저장. 실패(빈 슬롯)면 None. 각 블록 독립이라 동시 실행 가능."""
        b, images, manifest, has_face, product_images = item[:5]
        space_set_plate = item[5] if len(item) > 5 else None
        strict_space_scene_qc = bool(item[6]) if len(item) > 6 else False
        passthrough = item[7] if len(item) > 7 else None
        # 원본 패스스루 — 미세 패턴(스트라이프·체크) 상품의 디테일 컷은 **생성하지 않고**
        # 셀러가 찍은 그 색상의 Detail 사진을 그대로 쓴다. 원단 매크로는 전신 컷 해상도로는
        # 재현이 불가능하다(2026-08-01 측정: 4K 에서도 줄 한 주기당 14px → 파란 2가닥을 그리려면
        # 가닥당 1px 미만). 있는 원본을 다시 그려 정보를 버리는 대신 그대로 싣는다.
        # 생성 호출도 그만큼 줄어든다. 여기서 하는 이유: gather 순서가 곧 블록 순서라
        # 나중에 합치면 컷 배열이 어긋난다.
        if passthrough is not None:
            passthrough_id = passthrough.get("id") or passthrough.get("asset_id")
            await _emit(app.state.pool, job_id, "step",
                        {"blockId": b.get("id"), "status": "cut_passthrough",
                         "assetId": passthrough_id})
            return (
                {"blockId": b.get("id"), "imageUrl": f"/v1/assets/{passthrough_id}/file",
                 "width": passthrough.get("width"), "height": passthrough.get("height")},
                None,          # 새 asset 을 만들지 않는다 — 이미 존재하는 셀러 자산이다
                False, None, [],
            )
        async with sem:
            if not images:  # 옷 근거(상품/마네킹) 없음 — 무드만으로는 동일성 보장 불가, 생성하지 않는다
                log.warning("AG-06 cut skipped (no garment-truth references) job %s block %s", job_id, b.get("id"))
                await _emit(app.state.pool, job_id, "step",
                            {"blockId": b.get("id"), "status": "cut_failed"})
                return None
            try:
                generate_kwargs = {"analysis": analysis, "manifest": manifest}
                if has_face:
                    generate_kwargs["has_face"] = True
                img, mime = await cut_generator.generate(
                    s, gemini, b, product, images, **generate_kwargs)
            except Exception as e:  # GeminiError·ValueError 포함 — 실패 컷 = 빈 슬롯, 미차감(부분 성공)
                log.warning("AG-06 cut failed for job %s block %s: %r", job_id, b.get("id"), e)
                await _emit(app.state.pool, job_id, "step",
                            {"blockId": b.get("id"), "status": "cut_failed"})
                return None
            plate = space_set_plate
            # bg 편집 컷은 첫 첨부, 공간 세트는 별도 전달된 대표 plate를 같은 장소 QC 기준으로 쓴다.
            if (
                plate is None
                and b.get("refScope") == "bg"
                and manifest.startswith("1. EXAMPLE REFERENCE (scope: bg)")
            ):
                plate = images[0]
            # 장소일치 QC 게이트. 불일치면 재생성, 상한 초과면 이 컷만 빈 슬롯(부분 성공).
            # 일반 bg 편집은 기존 fail-open, 발행 공간세트는 QC 불능도 fail-closed다.
            if plate is not None:
                attempt = 1
                while True:
                    try:
                        scene_qc = await image_qc.scene_verdict(
                            s, plate, InlineImage(mime, img))
                    except VisionError as e:
                        if strict_space_scene_qc:
                            log.warning(
                                "AG-06 production space-set scene QC unavailable "
                                "job %s block %s: %r — fail-closed",
                                job_id,
                                b.get("id"),
                                e,
                            )
                            await _emit(
                                app.state.pool,
                                job_id,
                                "step",
                                {
                                    "blockId": b.get("id"),
                                    "status": "cut_failed",
                                },
                            )
                            return None
                        log.warning(
                            "AG-06 scene QC unavailable job %s block %s: %r — fail-open",
                            job_id,
                            b.get("id"),
                            e,
                        )
                        break
                    if scene_qc["verdict"] == "pass":
                        break
                    if attempt >= max(1, s.bg_scene_qc_attempts):
                        log.warning("AG-06 scene mismatch after %d attempts job %s block %s: %s",
                                    attempt, job_id, b.get("id"), scene_qc["mismatches"][:3])
                        await _emit(app.state.pool, job_id, "step",
                                    {"blockId": b.get("id"), "status": "cut_failed"})
                        return None
                    attempt += 1
                    try:
                        img, mime = await cut_generator.generate(
                            s, gemini, b, product, images, **generate_kwargs)
                    except Exception as e:
                        log.warning("AG-06 scene retry generate failed job %s block %s: %r",
                                    job_id, b.get("id"), e)
                        await _emit(app.state.pool, job_id, "step",
                                    {"blockId": b.get("id"), "status": "cut_failed"})
                        return None
            candidate_scene_warnings = []

            async def _generate_candidate():
                candidate_img, candidate_mime = await cut_generator.generate(
                    s, gemini, b, product, images, **generate_kwargs)
                if plate is None:
                    return InlineImage(candidate_mime, candidate_img)

                candidate_attempt = 1
                while True:
                    try:
                        scene_qc = await image_qc.scene_verdict(
                            s, plate, InlineImage(candidate_mime, candidate_img))
                    except VisionError as e:
                        if strict_space_scene_qc:
                            raise RuntimeError(
                                "production space-set scene QC unavailable"
                            ) from e
                        log.warning(
                            "AG-06 candidate scene QC unavailable job %s block %s: %r — fail-open",
                            job_id, b.get("id"), e)
                        candidate_scene_warnings.append({"code": "scene_qc_unavailable"})
                        break
                    if scene_qc["verdict"] == "pass":
                        break
                    if candidate_attempt >= max(1, s.bg_scene_qc_attempts):
                        raise RuntimeError("candidate scene mismatch")
                    candidate_attempt += 1
                    candidate_img, candidate_mime = await cut_generator.generate(
                        s, gemini, b, product, images, **generate_kwargs)
                return InlineImage(candidate_mime, candidate_img)

            chosen, garment_qc, garment_warnings = await image_qc.best_of(
                s,
                product_images,
                InlineImage(mime, img),
                _generate_candidate,
            )
            img, mime = chosen.data, chosen.mime
            garment_warnings = [*candidate_scene_warnings, *garment_warnings]

            ext = ext_for_mime(mime) or _EXT_FALLBACK.get(mime, "png")
            asset_id = str(uuid.uuid4())
            key = ai_key(user_id, project_id, job_id, asset_id, ext)
            await asyncio.to_thread(r2.put_bytes, key, img, mime, cache=IMMUTABLE_CACHE)
            w, h = _dims(img)
            return (
                # width/height 는 조립(M-02)이 요소 박스를 **이미지 비율대로** 잡는 근거다.
                # 없으면 page_assembler 가 기본 비율로 폴백한다(생성 실패·구 데이터 안전).
                {"blockId": b.get("id"), "imageUrl": f"/v1/assets/{asset_id}/file",
                 "width": w, "height": h},
                {"asset_id": asset_id, "bucket": s.r2_bucket, "key": key, "mime": mime,
                 "size": len(img), "width": w, "height": h,
                 "metadata": ({"anchorBaselineId": (job.get("payload") or {}).get("baselineId"),
                               "anchorRole": "approved_front_baseline"}
                              if (job.get("payload") or {}).get("baselineId")
                              and _cut_type(b) in _WORN_CUT_TYPES else {})},
                has_face,
                garment_qc,
                garment_warnings,
            )

    # gather 는 입력 순서를 보존 — 콘티 블록 순서대로 컷을 배열한다.
    cut_results, cut_assets, face_cuts = [], [], 0
    garment_qcs, garment_warnings = [], []
    for r in await asyncio.gather(*[_one(item) for item in prepared]):
        if r:
            cut_results.append(r[0])
            # 패스스루는 새 asset 이 없다(None). 이 목록의 길이가 **과금 단위**라 여기 넣으면
            # 이미지 모델을 부르지도 않은 컷에 크레딧이 붙는다.
            if r[1] is not None:
                cut_assets.append(r[1])
            face_cuts += 1 if r[2] else 0
            if r[3] is not None:
                garment_qcs.append({"blockId": r[0]["blockId"], **r[3]})
            garment_warnings.extend(
                {"blockId": r[0]["blockId"], **warning} for warning in r[4])
    return cut_results, cut_assets, face_cuts, garment_qcs, garment_warnings


async def _gen_copy(app, job, ai_blocks, product, analysis):
    """copywriting 시 블록별 AG-02 카피 → 묶음 AG-03 검수(revise 채택). 실패 블록은 카피 생략."""
    s = app.state.settings
    sem = asyncio.Semaphore(_GEN_CONCURRENCY)

    async def _one(b):
        """블록 1개 카피 생성. 실패면 None(카피는 게이트 아님, 블록 생략)."""
        async with sem:
            try:
                texts = await copywriter.generate(
                    s, content_role=b.get("contentRole"), section_role=b.get("sectionRole"),
                    cut_type=b.get("cutType"), product=product, analysis=analysis,
                    color_label=b.get("colorId"))
            except Exception as e:  # VisionError 포함 — 카피는 게이트 아님, 실패 블록 생략
                log.warning("AG-02 copy failed for job %s block %s: %r", job["id"], b.get("id"), e)
                return None
            return (b.get("id"), texts) if texts else None

    # gather 는 순서 보존 — drafts 삽입 순서(=콘티 순서)를 유지한다.
    items, drafts = [], {}
    for r in await asyncio.gather(*[_one(b) for b in ai_blocks]):
        if r:
            bid, texts = r
            drafts[bid] = texts
            for t in texts:
                items.append({"blockId": bid, "text": t.get("text", "")})
    if not items:
        return []
    # AG-03 검수 — revise면 수정 텍스트로 교체(첫 항목 role 유지). 실패 시 원문 유지.
    try:
        confirmed = {"materials": analysis.get("materials"),
                     "sellingPoints": analysis.get("sellingPoints"),
                     "measurementsKnown": not analysis.get("measurementsUnknown")}
        results = await copy_qc.review(s, items, confirmed)
        rev = {r["blockId"]: r for r in results if r.get("verdict") == "revise" and r.get("revisedText")}
    except Exception as e:  # VisionError 포함 — 검수 실패 시 원문 유지(게이트 아님)
        log.warning("AG-03 copy-qc failed for job %s: %r", job["id"], e)
        rev = {}
    copy_results = []
    for bid, texts in drafts.items():
        if bid in rev:  # 첫 텍스트를 검수 수정안으로 교체
            texts = [{"role": texts[0].get("role", "body"), "text": rev[bid]["revisedText"]}] + texts[1:]
        copy_results.append({"blockId": bid, "texts": texts})
    return copy_results


async def run_detail_page_job(app, job: dict) -> None:
    s, pool = app.state.settings, app.state.pool
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    lease_token = job["lease_token"]
    reserved = job.get("credits_reserved") or 0
    settle_key = f"credit:job:{job_id}:settle"

    async def _fail(message: str, meta: dict, code: str = "generation_failed"):
        try:
            async with pool.connection() as conn:
                await repo.finalize_detail_page_failure(
                    conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                    project_id=project_id, reserved=reserved, settle_key=settle_key,
                    message=message, metadata=meta, code=code)
                await conn.commit()
        except Exception:
            log.exception("detail_page finalize_failure error for job %s", job_id)

    try:
        # 1) 입력 로드 — 옷 레퍼런스 = 승인된 front baseline(Identity Lock)
        #    + 블록 색상별 상품 슬롯 이미지 + 모든 착용컷의 매칭 의류 + 무드 레퍼런스
        async with pool.connection() as conn:
            project = await repo.get_project(conn, user_id, project_id) or {}
            storyboard = await repo.get_storyboard(conn, project_id)
            if not getattr(s, "genexample_bg_enabled", False) and any(
                isinstance(block, dict)
                and (block.get("refScope") or block.get("ref_scope")) == "bg"
                and bool(block.get("exampleId") or block.get("example_id"))
                and space_set_assets.parse_space_set_group_id(
                    block.get("spaceGroupId") or block.get("space_group_id")
                )
                is None
                for block in storyboard
            ):
                raise ValueError("genexample_bg_disabled")
            product = await repo.get_product(conn, project_id) or {}
            analysis = await repo.get_analysis(conn, project_id) or {}
            # contentRole가 사용자 선택의 정본이다. 저장 입력을 여기서도 방어적으로
            # 정규화해 매칭 첨부·컷·카피·조립이 모두 같은 역할/레시피를 읽게 한다.
            storyboard = content_roles.canonicalize_storyboard(storyboard)
            clothing_type = (
                product.get("clothing_type")
                or product.get("clothingType")
                or "top"
            )
            space_set_bindings = space_set_assets.bind_storyboard_space_sets(
                storyboard,
                clothing_type=clothing_type,
                gender=mannequin.select_base_gender(
                    analysis, clothing_type
                ),
            )
            ai_blocks = [
                b
                for b in storyboard
                if isinstance(b, dict) and b.get("source") == "ai"
            ]
            requested_baseline_id = (job.get("payload") or {}).get("baselineId")
            # StoryboardBlock에는 modelId가 없다(계약 §3.4). 상세페이지의 프로젝트 단위 선택값은
            # Analysis.selectedModelId가 정본이며, 아래 prep에서 저장 블록을 바꾸지 않고 런타임 주입한다.
            selected_model_id = analysis.get("selectedModelId") or analysis.get("selected_model_id")

            # FaceMarket 라이선스 얼굴(FM-31) — 프로젝트에 잠긴 라이선스가 있을 때만.
            # 잠금 없음 = 기존 마네킹 경로 → None, 아래 첨부·고지 분기 전부 미진입.
            face_ref = await _load_license_face(app, conn, project)

            # 컷당 단일 아이덴티티-소스 선택(codex [P1]) — 실존 모델 그리드/라이선스 얼굴/가상모델 중 1개.
            # 실존 모델(REAL)은 라이선스 활성일 때만. 실자산 있는데 라이선스 실패면 REJECTED → 얼굴 미주입.
            from ..agents import identity_source
            license_row = await _load_license_row(app, conn, project)
            # 실존 자산 조회는 facemarket 켜졌고 선택 모델이 있을 때만 — off(기존/가상 경로)면
            # 쿼리조차 돌지 않아 완전 무영향.
            real_refs = (await identity_source.resolve_real_model_assets(conn, selected_model_id)
                         if selected_model_id and s.facemarket_enabled else None)
            source = identity_source.select_source(
                selected_model_id=selected_model_id, license_row=license_row,
                has_real_assets=real_refs is not None, has_license_face=face_ref is not None)
            # 관측 로그(PII 없음 — 소스 enum·플래그만). 데모·검증에서 REAL 주입 확인용.
            log.info("AG-06 identity source=%s job=%s hasReal=%s hasLicenseFace=%s",
                     source, job_id, real_refs is not None, face_ref is not None)
            if source == "REJECTED":
                log.warning("AG-06 real model selected without active license; skipping face (job %s)", job_id)
                face_ref = None
                real_refs = None
            if source == "REAL" and license_row is not None:
                notice_ctx = {"model_name": license_row["model_name"], "license_id": license_row["id"]}
            elif source == "LEGACY" and face_ref is not None:
                notice_ctx = {"model_name": face_ref["model_name"], "license_id": face_ref["license_id"]}
            else:
                notice_ctx = None

            # 새 라우트가 만든 착용컷 job 은 baselineId 를 반드시 스냅샷한다. 실행 사이에 사용자가
            # 다른 컷을 승인하면 옛 앵커로 생성하지 않고 provider 호출 전에 실패한다. payload 가
            # 없는 것은 migration 이전에 이미 큐에 있던 legacy job 뿐이라 기존 선택 포인터 경로를
            # 유지한다(새 요청은 이 폴백에 도달하지 않는다).
            mannequin_asset = None
            anchor_baseline_id = None
            if requested_baseline_id:
                active_baseline = await repo.get_active_baseline(conn, project_id)
                if active_baseline is None:
                    raise ValueError("no_approved_baseline")
                if active_baseline["id"] != requested_baseline_id:
                    raise ValueError("baseline_changed")
                mannequin_asset = {
                    "id": active_baseline.get("asset_id"),
                    "asset_id": active_baseline.get("asset_id"),
                    "r2_bucket": active_baseline.get("r2_bucket"),
                    "r2_key": active_baseline.get("r2_key"),
                    "mime_type": active_baseline.get("mime_type"),
                }
                if not all(mannequin_asset.get(k) for k in ("asset_id", "r2_key", "mime_type")):
                    raise ValueError("baseline_asset_missing")
                anchor_baseline_id = active_baseline["id"]
            else:
                sel = project.get("selected_mannequin_id") or project.get("selectedMannequinId")
                if sel:
                    for c in await repo.list_mannequin_cuts(conn, user_id, project_id):
                        if f"{c.get('candidate')}-{c.get('version')}" == sel and c.get("asset_id"):
                            mannequin_asset = await repo.get_asset_for_user(
                                conn, user_id, str(c["asset_id"]))
                            break
            color_assets: dict = {}   # (colorId, detail 여부) → [asset(slot 포함)] — 블록 간 재사용
            detail_color_transfers: dict = {}  # 위 키 → 타색 Detail의 목표색 전환 정보|None
            match_assets: dict = {}   # matchingItemId → asset|None
            mood_assets: dict = {}    # refAssetId → asset|None (소유 검증 겸함)
            def _color_key(block: dict) -> str | None:
                value = block.get("colorId")
                return None if value is None else str(value)

            def _is_detail(block: dict) -> bool:
                return block.get("cutType") == "product" and block.get("shot") == "detail"

            for b in ai_blocks:
                ckey = _color_key(b)
                asset_key = (ckey, _is_detail(b))
                if asset_key not in color_assets:
                    rows = []
                    if asset_key[1]:
                        image_refs, transfer = cut_generator.detail_reference_images(product, ckey)
                    else:
                        image_refs, transfer = cut_generator.color_images(product, ckey), None
                    for slot, aid in image_refs:
                        a = await repo.get_asset_for_user(conn, user_id, aid)
                        if a:
                            a["slot"] = slot
                            a.setdefault("asset_id", aid)
                            rows.append(a)
                    color_assets[asset_key] = rows
                    detail_color_transfers[asset_key] = transfer
                mids = b.get("matchIds") or []
                if mids and _cut_type(b) in _WORN_CUT_TYPES and str(mids[0]) not in match_assets:
                    m_aid = await repo.get_matching_item_asset(conn, str(mids[0]))
                    match_assets[str(mids[0])] = (
                        await repo.get_asset_for_user(conn, user_id, m_aid) if m_aid else None)
                for rid in (b.get("refAssetIds") or [])[:3]:
                    if str(rid) not in mood_assets:
                        mood_assets[str(rid)] = await repo.get_asset_for_user(conn, user_id, str(rid))

        # R2 바이트는 r2_key 캐시로 1회만 — 같은 색상 이미지가 블록마다 재다운로드되지 않게
        _img_cache: dict = {}

        async def _r2_img(k: str, mime: str, bucket: str = "public") -> InlineImage:
            # 실존 모델 그리드는 bucket='face' → 비공개 r2_face 에서 로드(공개 버킷 하드코딩 금지).
            cache_key = (bucket, k)
            if cache_key not in _img_cache:
                client = app.state.r2_face if bucket == "face" else app.state.r2
                if client is None:
                    raise RuntimeError("bucket client unavailable")
                _img_cache[cache_key] = InlineImage(
                    mime, await asyncio.to_thread(client.get_bytes, k))
            return _img_cache[cache_key]

        async def _img(a: dict) -> InlineImage:
            return await _r2_img(a["r2_key"], a["mime_type"])

        # C방식 두 장은 원자적인 한 쌍이다. 하나라도 manifest/R2 로드에 실패하면 둘 다 빼고
        # 기존 옷 레퍼런스만으로 계속 생성한다(상세페이지 부분 실패 정책과 같은 fail-open).
        _model_cache: dict[str, list[InlineImage] | None] = {}

        async def _model_images(spec: dict | None) -> list[InlineImage]:
            if not spec or _cut_type(spec) not in _WORN_CUT_TYPES:
                return []
            model_id = spec.get("modelId")
            if not model_id:
                return []
            if model_id not in _model_cache:
                try:
                    refs = cut_generator.resolve_virtual_model_assets(spec)
                    if refs is not None:
                        _model_cache[model_id] = [
                            await _r2_img(ref["key"], ref["mime"]) for ref in refs
                        ]
                    else:
                        _model_cache[model_id] = None
                except Exception as e:
                    log.warning(
                        "AG-06 virtual model assets unavailable for job %s model %s; "
                        "continuing without model references: %r", job_id, model_id, e)
                    _model_cache[model_id] = None
            return _model_cache[model_id] or []

        # 실존 모델(REAL) 그리드 — 비공개 r2_face 에서 로드(bucket 인지). 잡당 1회 캐시.
        # 로드 실패는 얼굴 없이 생성으로 강등(가상 모델 경로와 같은 fail-open).
        _real_cache: dict[str, list[InlineImage] | None] = {}

        async def _real_model_images() -> list[InlineImage]:
            if not real_refs:
                return []
            if "refs" not in _real_cache:
                try:
                    _real_cache["refs"] = [
                        await _r2_img(r["key"], r["mime"], r.get("bucket", "face"))
                        for r in real_refs
                    ]
                except Exception as e:
                    log.warning("AG-06 real model assets unavailable job %s: %r", job_id, e)
                    _real_cache["refs"] = None
            return _real_cache["refs"] or []

        # (runtime block, images, manifest, has_face, product_images,
        #  space_set_plate, strict_space_scene_qc)
        # — images 순서는 manifest 계약과 동일. 대표 plate는 같은 세트에서 1회만 로드해 공유한다.
        prepared = []
        _example_cache: dict[str, InlineImage | None] = {}
        _space_plate_cache: dict[str, InlineImage] = {}
        _space_example_cache: dict[str, InlineImage] = {}
        example_warnings: list[dict] = []
        _virtual_ids: set[str] = set()
        fallback_model_id = s.detailpage_fallback_model_id
        # 폴백 registry 는 폴백이 실제로 가능한 소스에서만 지연 로드(파일 없으면 fail-open, 잡 안 죽임):
        #  VIRTUAL           → Phase B 결정적 치환(resolve_effective_model_id)
        #  REAL·REJECTED     → prod 안전망(needs_identity_fallback → mB). 이 둘도 _virtual_ids 필요.
        if fallback_model_id and source in ("VIRTUAL", "REAL", "REJECTED"):
            try:
                _virtual_ids = set(cut_generator.load_virtual_model_registry())
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
                # 파일 부재/권한(OSError)·JSON 파손(JSONDecodeError)·깨진 인코딩(UnicodeDecodeError)
                # 모두 fail-open — 폴백만 건너뛰고 잡은 계속(폴백은 선택적 보정이지 잡 필수 입력 아님).
                log.warning(
                    "AG-06 virtual model manifest unavailable; skipping fallback substitution "
                    "for job %s: %r", job_id, e)
        _fallback_warned = False
        for b in ai_blocks:
            cut_spec = dict(b)
            space_binding = space_set_bindings.get(id(b))
            # 저장/클라이언트가 런타임 전용 지시를 주입하지 못하게 매번 실제 선택 결과로 재구성한다.
            cut_spec.pop("_detailColorTransfer", None)
            cut_spec.pop("_spaceSetContinuity", None)
            if space_binding is not None:
                # 공간 세트의 pose/범위/변주 강도는 저장 payload가 아니라 발행 레지스트리가
                # 정본이다. 오래된 값이나 우회 클라이언트가 전용 pose·plate 계약을 바꾸지 못한다.
                cut_spec["refScope"] = "pose"
                cut_spec["pose"] = "auto"
                cut_spec["spaceVariation"] = space_binding["set"]["spaceVariation"]
            # 저장 콘티에 우연히 남은 비계약 필드가 프로젝트 선택 모델을 덮지 못하게 제거 후 주입한다.
            cut_spec.pop("modelId", None)
            cut_spec.pop("model_id", None)
            # 인물 일관성(AG-06): VIRTUAL 소스에서 선택 id 가 가상 registry 밖(facemarket off 상태의
            # 실존 UUID)이면 resolve_virtual_model_assets 가 None → 참조 0장 → 컷마다 인물 랜덤.
            # 결정적 가상모델로 폴백해 전 컷 동일 인물 보장. REAL/LEGACY 는 얼굴을 별도 경로로
            # 붙이므로 건드리지 않는다(인물 이중 첨부 방지).
            if source == "VIRTUAL":
                eff_model_id, _subbed = cut_generator.resolve_effective_model_id(
                    selected_model_id, fallback_model_id=fallback_model_id,
                    virtual_ids=_virtual_ids)
                if _subbed and not _fallback_warned:
                    log.warning(
                        "AG-06 selected model %s unresolvable as virtual (facemarket=%s) → fallback %s "
                        "for identity consistency (job %s)",
                        selected_model_id, s.facemarket_enabled, eff_model_id, job_id)
                    _fallback_warned = True
            else:
                eff_model_id = selected_model_id
            if eff_model_id:
                cut_spec["modelId"] = eff_model_id
            try:
                normalized = cut_generator.normalize_spec(cut_spec, clothing_type=clothing_type)
            except ValueError:
                normalized = None  # generate()가 블록 단위 실패로 처리하는 기존 경로 유지
            asset_key = (_color_key(b), _is_detail(b))
            prods = color_assets.get(asset_key, [])
            is_worn_cut = normalized is not None and normalized.get("cutType") in _WORN_CUT_TYPES
            if detail_color_transfers.get(asset_key):
                cut_spec["_detailColorTransfer"] = detail_color_transfers[asset_key]
            # 옷 근거(상품 사진 또는 마네킹컷)가 없으면 생성 불가 — 무드/매칭만으로 진행하면
            # 모델이 레퍼런스 속 옷을 지어내거나 베낀다(ADR-0004 정확성 최우선). 스킵 표식.
            # 얼굴은 이 가드 **뒤에서만** 붙는다 — 여기 얼굴을 넣으면 images 가 비지 않아
            # _gen_cuts 의 `if not images` 스킵이 무력화되고 옷 근거 0으로 생성이 돌아간다.
            if (mannequin_asset is None or not is_worn_cut) and not prods:
                prepared.append((cut_spec, [], "", False, [], None, False))
                continue
            mids = b.get("matchIds") or []
            match_a = match_assets.get(str(mids[0])) if mids and _cut_type(b) in _WORN_CUT_TYPES else None
            moods = [mood_assets[str(r)] for r in (b.get("refAssetIds") or [])[:3] if mood_assets.get(str(r))]
            # 얼굴이 실제로 담기는 컷에만 첨부 — product(사람 금지)·거울샷 기본(폰이 가림)·
            # 뒷모습·머리가 프레임 밖인 샷은 제외(cut_generator.wants_face 가 단일 규칙).
            wants = cut_generator.wants_face(cut_spec, clothing_type)
            # 컷당 아이덴티티 소스 1개(codex [P1]) — 셋 중 하나만 컷에 들어간다:
            #  REAL    실존 모델 그리드(비공개 face 버킷) — 단일 라이선스 얼굴 미첨부
            #  LEGACY  라이선스 단일 얼굴(비공개) — 어떤 그리드도 미첨부
            #  VIRTUAL 가상모델 그리드(공개 버킷) — 라이선스 불요
            # face_slot=단일 얼굴 슬롯(LEGACY만). has_identity=검증 얼굴이 실제 담기는 컷(REAL·LEGACY)
            # → face_cuts·검증 배지 근거. 세 소스가 한 컷에 겹치지 않아 인물 혼합·이중주입이 없다.
            if source == "REAL":
                # 실존 모델 그리드는 얼굴 노출과 무관하게 모든 착용컷에 identity 앵커로 붙인다(A4).
                # wants(얼굴 노출)로만 게이트하면 mirror/back 이 참조 0장 → 그 컷만 인물 랜덤이 된다
                # (REAL 은 VIRTUAL 과 달리 mB 폴백도 없음). 배지(has_identity)만 wants 로 준다.
                attach_grid, _badge = cut_generator.real_identity_plan(
                    normalized.get("cutType") if normalized else None, wants_face=wants)
                model_images = await _real_model_images() if attach_grid else []
                has_identity = _badge and len(model_images) == 2
                face_slot = False
            elif source == "LEGACY":
                model_images = []
                has_identity = wants
                face_slot = wants
            elif source == "VIRTUAL":
                model_images = await _model_images(normalized)
                has_identity = False
                face_slot = False
            else:  # NONE / REJECTED — 얼굴 없이 생성
                model_images = []
                has_identity = False
                face_slot = False
            # 안전망(prod facemarket ON): **실존 모델을 골랐는데** 착용컷 인물 참조가 0장이면
            # (REJECTED=무라이선스 실모델, REAL grid 로드 실패) 결정적 가상모델로 폴백 — 랜덤 인물
            # 원천 차단. Phase B 의 mB 폴백은 VIRTUAL 전용이라 REAL/REJECTED 를 못 막는다. 무모델
            # 선택(NONE)은 기존 동작 유지(모델을 요청하지 않은 프로젝트에 인물을 강요하지 않는다).
            # 무라이선스 실 얼굴은 재사용 안 함(대체 인물 mB → 생체 라이선스 위반 없음). 검증 배지
            # (has_identity)는 주지 않는다(실 얼굴 아님).
            if source in ("REAL", "REJECTED") and cut_generator.needs_identity_fallback(
                    cut_type=normalized.get("cutType") if normalized else None,
                    has_model_images=bool(model_images), face_slot=face_slot):
                _fb_id = s.detailpage_fallback_model_id
                if _fb_id and _fb_id in _virtual_ids:
                    model_images = await _model_images(
                        {"cutType": normalized["cutType"], "modelId": _fb_id})
                    if model_images and not _fallback_warned:
                        log.warning(
                            "AG-06 worn cut identity empty (source=%s model=%s) → deterministic %s "
                            "fallback for consistency (job %s)",
                            source, selected_model_id, _fb_id, job_id)
                        _fallback_warned = True
            imgs = []
            product_images = []
            if mannequin_asset is not None and is_worn_cut:
                imgs.append(await _img(mannequin_asset))
            imgs.extend(model_images)
            for a in prods:
                product_image = await _img(a)
                imgs.append(product_image)
                product_images.append(product_image)
            if match_a is not None:
                imgs.append(await _img(match_a))
            if face_slot:
                # 비공개 r2_face 바이트(LEGACY 단일 얼굴) — _img()(공개 버킷 하드코딩) 를 태우지 않는다.
                imgs.append(face_ref["image"])
            for a in moods:
                imgs.append(await _img(a))
            example_scope = None
            space_set_plate = None
            has_space_set_plate = False
            example_id = b.get("exampleId") or b.get("example_id")
            if space_binding is not None:
                set_entry = space_binding["set"]
                pose_reference = space_binding["poseReference"]
                set_id = set_entry["setId"]
                plate_asset = set_entry["representativePlate"]
                if plate_asset is not None and set_id not in _space_plate_cache:
                    _space_plate_cache[set_id] = (
                        await space_set_assets.load_space_set_image(
                            s, plate_asset, role="대표 배경"
                        )
                    )
                pose_cache_key = (
                    f"{pose_reference['source']}:{pose_reference['exampleId']}"
                )
                if pose_cache_key not in _space_example_cache:
                    if pose_reference["source"] == "space-set":
                        pose_image = await space_set_assets.load_space_set_image(
                            s, pose_reference["asset"], role="포즈"
                        )
                    else:
                        pose_image = await cut_generator.load_example_image(
                            s,
                            pose_reference["exampleId"],
                            scope="pose",
                            clothing_type=clothing_type,
                        )
                        if pose_image is None:
                            raise space_set_assets.SpaceSetBindingError(
                                "space_set_pose_unavailable",
                                "공간 세트의 포즈 예시를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.",
                            )
                    _space_example_cache[pose_cache_key] = pose_image
                space_set_plate = _space_plate_cache.get(set_id)
                if space_set_plate is not None:
                    imgs.append(space_set_plate)
                imgs.append(_space_example_cache[pose_cache_key])
                example_scope = "pose"
                has_space_set_plate = space_set_plate is not None
                cut_spec["_spaceSetContinuity"] = has_space_set_plate
            elif example_id:
                # 직접 포즈가 pose-scope 예시보다 우선한다는 기존 계약: 이미지 자체도 첨부하지 않아
                # 픽셀 조건이 텍스트 가드를 우회해 포즈를 되살리지 못하게 한다.
                pose_overrides_example = normalized is not None \
                    and normalized["pose"] != "auto" and normalized["refScope"] == "pose"
                if normalized is not None and not pose_overrides_example:
                    scope = normalized["refScope"]
                    if example_id.startswith("ss_") and not (
                        b.get("spaceGroupId") or b.get("space_group_id")
                    ):
                        example_reference = (
                            space_set_assets.resolve_published_example_reference(
                                normalized,
                                clothing_type=clothing_type,
                                gender=mannequin.select_base_gender(
                                    analysis, clothing_type
                                ),
                                scope=scope,
                            )
                        )
                        cache_key = (
                            f"space-set:{example_reference['exampleId']}:{scope}"
                        )
                        if cache_key not in _space_example_cache:
                            _space_example_cache[cache_key] = (
                                await space_set_assets.load_space_set_image(
                                    s,
                                    example_reference["asset"],
                                    role="전체 예시" if scope == "all" else "포즈",
                                )
                            )
                        imgs.append(_space_example_cache[cache_key])
                        example_scope = scope
                    else:
                        status = cut_generator.example_asset_status(
                            example_id, clothing_type, scope)
                        if status in ("not_applicable", "variant_unpublished"):
                            example_warnings.append({
                                "code": "example_not_applicable"
                                if status == "not_applicable" else "example_variant_unpublished",
                                "blockId": b.get("id"),
                                "exampleId": example_id,
                                "clothingType": clothing_type,
                                "refScope": scope,
                            })
                            # 이미지 미첨부만으로는 all 범위의 레거시 EXNUANCE 해시가 남는다.
                            # 부적합/미발행 예시가 텍스트로도 영향을 주지 않게 런타임 사본에서 해제한다.
                            cut_spec["exampleId"] = None
                        elif scope == "pose" and not cut_generator.pose_direction_compatible(
                            example_id, normalized
                        ):
                            # v2 preflight: 호환되지 않는 포즈는 이미지 모델 호출 전에 이 컷만
                            # 빈 슬롯으로 닫는다. 배치의 다른 컷은 계속 생성한다.
                            example_warnings.append({
                                "code": "pose_direction_incompatible",
                                "blockId": b.get("id"),
                                "exampleId": example_id,
                                "direction": normalized.get("direction"),
                            })
                            prepared.append((cut_spec, [], "", False, [], None, False))
                            continue
                        else:
                            # 캐시 키에 scope 포함 — pose는 누끼 variant, all은 원본이라 자산이 다르다
                            cache_key = f"{example_id}:{scope}"
                            if cache_key not in _example_cache:
                                _example_cache[cache_key] = await cut_generator.load_example_image(
                                    s, example_id, scope=scope, clothing_type=clothing_type)
                            example_img = _example_cache[cache_key]
                            if example_img is None and scope in ("pose", "bg"):
                                # 전용 자산 로드 실패 — 무음 강등 대신 이 컷만 빈 슬롯(ADR-0009 §2,
                                # 2026-07-20 실측: 강등이 '참고 안 된 bg 컷'을 조용히 만들었다)
                                log.warning("AG-06 %s example unavailable — cut fail-closed job %s block %s",
                                            scope, job_id, b.get("id"))
                                prepared.append((cut_spec, [], "", False, [], None, False))
                                continue
                            if example_img is not None:
                                # bg 플레이트는 첫 첨부(에디터 경로와 동일) — 마지막 첨부는 컷 섹션의
                                # 배경 나열에 밀려 무시된다(2026-07-20 파일럿 실측).
                                if scope == "bg":
                                    imgs.insert(0, example_img)
                                else:
                                    imgs.append(example_img)
                                example_scope = scope
            manifest = cut_generator.build_manifest(
                prods, has_mannequin=mannequin_asset is not None and is_worn_cut,
                has_match=match_a is not None, mood_count=len(moods),
                has_model_face=len(model_images) == 2, has_model_sheet=len(model_images) == 2,
                has_face=face_slot,
                example_scope=example_scope,
                example_is_product=normalized is not None and normalized["cutType"] == "product",
                has_space_set_plate=has_space_set_plate)
            # 4번째 = has_identity: 검증 얼굴(REAL 그리드·LEGACY 단일)이 실제 담긴 컷 → face_cuts 계수·
            # generate has_face·검증 배지 근거. VIRTUAL 그리드는 검증 얼굴이 아니므로 False.
            prepared.append(
                (
                    cut_spec,
                    imgs,
                    manifest,
                    has_identity,
                    product_images,
                    space_set_plate,
                    space_binding is not None and space_set_plate is not None,
                    page_source_assets.select_source_asset(
                        b, color_assets.get(asset_key, []),
                        color_transfer=detail_color_transfers.get(asset_key)),
                )
            )

        copywriting = bool(project.get("copywriting"))
        await _emit(pool, job_id, "progress", {"progress": 15, "phase": "inputs_loaded",
                                               "aiCuts": len(ai_blocks)})

        # 2) 컷 생성 (부분 성공)
        raw_image_provenance = [
            page_source_assets.raw_image_provenance(item[0], item[7])
            for item in prepared
            if len(item) > 7 and item[7] is not None
        ]
        cut_results, cut_assets, face_cuts, garment_qcs, garment_warnings = await _gen_cuts(
            app, job, prepared, product, analysis)
        example_warnings.extend(garment_warnings)
        await _emit(pool, job_id, "progress", {"progress": 65, "phase": "cuts",
                                               "generated": len(cut_assets)})
        # 판정 기준은 **컷이 하나라도 나왔는가**(cut_results)다. cut_assets 로 보면 전 블록이
        # 원본 패스스루인 상세페이지가 "전멸"로 오인된다 — 그 경우 컷은 멀쩡히 있다.
        if ai_blocks and not cut_results:
            # AI 컷이 하나도 없는데 done으로 종결하면 빈 상세페이지가 완성본처럼 보이고
            # 완료 화면 가드와도 충돌한다. 예약 크레딧을 환불하는 실패 종결로 보낸다.
            await _fail(
                "이미지를 만들지 못했어요. 상품 사진과 컷 설정을 확인한 뒤 다시 시도해 주세요.",
                {"error": "all_cuts_failed", "requestedCuts": len(ai_blocks)},
                code="all_cuts_failed",
            )
            return

        # 3) 카피(선택) + 검수
        copy_results = await _gen_copy(app, job, ai_blocks, product, analysis) if copywriting else []
        await _emit(pool, job_id, "progress", {"progress": 85, "phase": "copy"})

        # 4) 조립(M-02) — 실패 컷은 빈 슬롯으로.
        # AI 고지 분기는 **얼굴이 실제로 들어간 컷이 성공했을 때만**(face_cuts > 0) —
        # 라이선스만 잠기고 주입이 실패(전 컷 실패·얼굴 로드 강등)했는데 '실제 모델' 이라
        # 쓰면 허위 고지가 된다. 라이선스 없는 경로는 face_ref=None → 항상 기본 문구.
        # 범위 주장 근거: totalCuts = **성공한 컷 수**(실패 컷은 빈 슬롯이라 인물이 없다).
        # face_cuts < totalCuts 면 얼굴 미첨부 컷(거울샷·뒷모습·하반신·상품컷)이 섞였다는 뜻이라
        # 페이지 전체를 '가상인물 아님' 으로 주장할 수 없다 → assembler 가 '일부 컷' 문구로 내린다.
        license_notice = None
        if notice_ctx is not None and face_cuts > 0:
            license_notice = {"modelName": notice_ctx["model_name"],
                              "licenseId": notice_ctx["license_id"],
                              "faceCuts": face_cuts,
                              "totalCuts": len(cut_assets)}
        assemble_kwargs = {"license_notice": license_notice} if license_notice is not None else {}
        editor_blocks = page_assembler.assemble(
            storyboard, cut_results, copy_results, product, copywriting, **assemble_kwargs)

        # 5) 성공 종결 (원자·lease 펜스). charge = 성공 컷 수 × **예약 시점 단가 스냅샷**
        # (job.metadata.perCutCost — routes.py가 예약과 같은 tx에서 기록). 실행 시점 설정을 쓰면
        # 배포 사이 단가 변경이 낀 잡이 견적과 다르게 정산되고, 예약액÷현재 블록 수 역산은 예약 후
        # 콘티 재저장으로 블록이 늘면 단가가 0으로 떨어져 무과금 생성이 된다 — 둘 다 금지.
        # 스냅샷 없는 legacy 잡만 실행 시점 단가로 폴백. min 캡 = 예약 초과 차감 최종 가드.
        per_cut = (job.get("metadata") or {}).get("perCutCost")
        if per_cut is None:  # legacy 잡(스냅샷 도입 전 큐 잔여분)
            per_cut = s.credit_cost_storyboard_per_cut
        charge = min(len(cut_assets) * per_cut, reserved)
        success_metadata = {
            "creditCostVersion": s.credit_cost_version,
            "generatedCuts": len(cut_assets),
        }
        if anchor_baseline_id:
            success_metadata["anchorBaselineId"] = anchor_baseline_id
        if garment_qcs:
            success_metadata["garmentQc"] = garment_qcs
        if raw_image_provenance:
            success_metadata["rawImageProvenance"] = raw_image_provenance
        if example_warnings:
            success_metadata["warnings"] = example_warnings
        async with pool.connection() as conn:
            out = await repo.finalize_detail_page_success(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id, project_id=project_id,
                editor_blocks=editor_blocks, cut_assets=cut_assets, reserved=reserved, charge=charge,
                metadata=success_metadata)
            await conn.commit()
        if out is None:  # lease 상실 → 방금 올린 R2 객체 best-effort 정리
            for c in cut_assets:
                try:
                    await asyncio.to_thread(app.state.r2.delete, c["key"])
                except Exception:
                    log.warning("orphan R2 cleanup failed: %s", c["key"])
        else:
            # FaceMarket 온체인 정산 훅(선택과제2). 이 잡이 얼굴 라이선스를 소비했으면
            # 성공 종결 지점에서 70/20/10 을 온체인 기록. FM-30(verify-before-use)이
            # project 에 facemarket_license_id 를 실으면 활성 — 그전엔 lic_id None → no-op.
            # best-effort: 정산 실패가 이미 완료된 상세페이지 생성을 되돌리지 않는다.
            if s.facemarket_enabled and getattr(app.state, "fm_chain", None) is not None:
                lic_id = project.get("facemarket_license_id") or project.get("facemarketLicenseId")
                if lic_id:
                    try:
                        async with pool.connection() as conn:
                            async with conn.cursor() as cur:
                                await cur.execute(
                                    "select model_id::text as model_id, unit_price "
                                    "from fm_licenses where id = %s and status = 'active'",
                                    (lic_id,),
                                )
                                lic = await cur.fetchone()
                        if lic:
                            from ..facemarket import record_license_settlement
                            await record_license_settlement(
                                app, payment_key=f"job:{job_id}", license_id=str(lic_id),
                                model_id=lic["model_id"], total=int(lic["unit_price"]),
                                job_id=str(job_id))
                    except Exception:
                        log.warning("facemarket settlement hook failed for job %s", job_id)
    except Exception as e:
        is_space_set_error = isinstance(e, space_set_assets.SpaceSetBindingError)
        code = (
            e.code
            if is_space_set_error
            else e.args[0]
            if isinstance(e, ValueError)
            and e.args
            and isinstance(e.args[0], str)
            and e.args[0] in _BASELINE_FAILURES
            else "genexample_bg_disabled"
            if isinstance(e, ValueError) and e.args[:1] == ("genexample_bg_disabled",)
            else "generation_failed"
        )
        await _fail(
            (
                e.message
                if is_space_set_error
                else _BASELINE_FAILURES[code]
                if code in _BASELINE_FAILURES
                else "배경만 생성예시는 현재 사용할 수 없어요. 콘티에서 해당 예시를 제거해 주세요."
                if code == "genexample_bg_disabled"
                else "상세페이지 생성에 실패했어요. 다시 시도해 주세요."
            ),
            {"error": code},
            code=code,
        )
