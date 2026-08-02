"""AG-06/AG-07 에디터 이미지 워커 (PL-5/6). 에디터 AI 탭 '새 이미지 추가'(mode:'new')는
cut_generator(AG-06)를, '현재 이미지 수정'(mode:'vary')은 cut_variator(AG-07)를 재사용한다.
mannequin_adjust_job.py의 reserve→generate→finalize 패턴을 단일 이미지(부분 성공 없음)에
맞춰 미러한다. lease 펜스·크레딧 정산은 repo.finalize_editor_image_success/failure.
"""

import asyncio
import hashlib
import logging
import re
import time
import uuid
from io import BytesIO

from PIL import Image

from .. import facemarket, repo
from ..agents import (
    content_roles,
    cut_generator,
    cut_variator,
    identity_source,
    image_qc,
    mannequin,
    space_set_assets,
)
from ..agents.gemini_image import GeminiError, InlineImage
from ..agents.vision_llm import VisionError
from ..r2 import IMMUTABLE_CACHE, ai_key, ext_for_mime
from ..services.generation_run import RunLogger
from ._common import emit_job_event as _emit
from .mannequin_job import _runlog_begin, _runlog_finish

log = logging.getLogger("wearless.editor_image_job")

_EXT_FALLBACK = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_ASSET_FILE_RE = re.compile(r"/v1/assets/([^/]+)/file")


def _image_dims(data: bytes) -> tuple[int | None, int | None]:
    try:
        im = Image.open(BytesIO(data))
        return im.width, im.height
    except Exception:
        return None, None


def _parse_source_asset_id(src: str | None) -> str | None:
    """VaryRequest.source.src(`/v1/assets/{id}/file` 안정 앱 URL)에서 asset id 추출.
    다른 형태(외부 URL 등)는 미상 → None(호출자가 실패 처리)."""
    if not src:
        return None
    m = _ASSET_FILE_RE.search(src)
    return m.group(1) if m else None


# _ASSET_FILE_RE 는 하이픈 36자를 통과시켜 UUID 가 아닌 것도 잡는다. 저장 전에 한 번 더 조인다.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _safe_asset_id(asset_id: str | None) -> dict:
    """실패 metadata 에 넣어도 되는 asset id만 골라낸다 — 아니면 아무것도 넣지 않는다."""
    return {"sourceAssetId": asset_id} if asset_id and _UUID_RE.match(asset_id) else {}


_SAFE_CODE_RE = re.compile(r"^[a-z0-9_]{1,64}$")


def _safe_error_code(exc: BaseException) -> str | None:
    """도메인 예외의 **코드**만 통과시킨다.

    이 코드베이스는 자체 ValueError 의 메시지를 그대로 에러 코드로 쓴다("invalid_color").
    그건 계약이므로 보존한다. 반면 provider·네트워크 예외의 메시지는 URL·토큰·응답 본문이라
    절대 나가면 안 된다 — 소문자 스네이크 한 토큰인 것만 코드로 인정하고 나머지는 버린다.
    """
    if not isinstance(exc, ValueError):
        return None
    msg = str(exc).strip()
    return msg if _SAFE_CODE_RE.match(msg) else None


def _editor_failure_meta(exc: BaseException) -> dict:
    """job metadata 용 실패 요약. 도메인 코드면 그 모양 그대로, 아니면 분류만."""
    code = _safe_error_code(exc)
    if code:
        return {"error": code}
    return {"error": "generation_failed", "category": _provider_category(exc)}


def _provider_category(exc: BaseException) -> str:
    """provider 실패 분류 — 원문은 남기지 않는다(URL·응답 본문이 들어 있다)."""
    msg = str(exc)
    name = type(exc).__name__
    if "timeout" in msg.lower() or "Timeout" in name:
        return "timeout"
    m = re.search(r"\b([45]\d{2})\b", msg)
    return f"http_{m.group(1)}" if m else "provider_error"


async def _r2_cleanup(app, key: str) -> None:
    """best-effort 삭제. 실패해도 진행하고, **키 원문은 로그에 남기지 않는다**."""
    try:
        await asyncio.to_thread(app.state.r2.delete, key)
    except Exception as e:
        log.warning("orphan R2 cleanup failed (error=%s)", type(e).__name__)


async def _vary_preflight(app, job, *, session_id, src_asset, changes, fail):
    """provider 호출 **전** 관문. 통과하면 컨텍스트, 아니면 None(이미 실패 종결).

    여기서 막는 것은 전부 "한 번 요청하고 두 번 과금되는" 경로다: 종결된 세션 재진입,
    다른 job 의 세션, source 가 바뀐 요청.
    """
    from ..services import editor_vary as ev

    s, pool = app.state.settings, app.state.pool
    job_id = job["id"]

    async def _blocked(reason):
        await _emit(pool, job_id, "step",
                    {"status": "vary_preflight_blocked", "reason": reason})
        await fail("이미 처리된 편집 요청이에요.", {"error": reason})
        return None

    if s.generation_run_log == "off":
        return await _blocked("misconfigured_feature")
    async with pool.connection() as conn:
        session = await repo.get_edit_session(conn, session_id)
    if session is None:
        return await _blocked("edit_session_missing")
    if session.get("job_id") not in (None, job_id):
        return await _blocked("edit_session_job_mismatch")
    if session.get("source_kind") != "editor_asset" or session.get("baseline_id"):
        return await _blocked("edit_session_source_mismatch")
    if session.get("source_asset_id") != src_asset["id"]:
        return await _blocked("edit_session_source_mismatch")
    if session.get("status") not in ("queued", "running"):
        return await _blocked("edit_session_not_runnable")
    try:
        async with pool.connection() as conn:
            await repo.update_edit_session(conn, session_id=session_id, status="running")
            await conn.commit()
    except repo.InvalidEditTransition:
        return await _blocked("edit_session_not_runnable")
    except Exception as e:
        log.warning("vary preflight failed (job=%s error=%s)", job_id, type(e).__name__)
        return await _blocked("edit_session_unavailable")
    norm = ev.validate_changes(changes)
    return {
        "session_id": session_id, "changes": norm,
        "edit_type": ev.edit_type_for(norm),
        "semantic_scope": ev.semantic_scope(norm),
        "entailed": ev.entailed_metrics(norm),
        "parent_output_id": session.get("parent_output_id"),
        "runlog": RunLogger(pool=pool, r2=app.state.r2, job_id=job_id,
                            project_id=job["project_id"], user_id=job["user_id"],
                            enabled=(s.generation_run_log == "shadow")),
    }


async def _vary_run_begin(app, job, prepared, ctx, *, src_asset, ref_bg_asset):
    """provider 에 **실제로 나갈** prepared 객체에서 스냅샷을 뜬다(재조립 금지)."""
    inputs = [("edit_source", prepared.images[0], src_asset["id"], None,
               ctx.get("parent_output_id"))]
    if prepared.has_ref_bg and len(prepared.images) > 1:
        inputs.append(("style_reference", prepared.images[1],
                       (ref_bg_asset or {}).get("id"), "background_reference"))
    run_id = await _runlog_begin(
        ctx["runlog"], kind="editor_vary", prompt=prepared.prompt,
        model=prepared.model, candidate="A", attempt=1,
        image_size=prepared.image_size, aspect_ratio=prepared.aspect_ratio,
        inputs=inputs, input_image=prepared.images[0],
        explicit_parent_generation_run_id=ctx.get("parent_run_id"),
        settings=app.state.settings)
    ctx["run_id"] = run_id
    return run_id


async def _vary_run_finish(app, job, run_id, ctx, *, started=None, result=None,
                           error=None):
    await _runlog_finish(ctx["runlog"], run_id, started=started, result=result,
                         error=error, candidate="A")
    if error is not None:
        ctx["run_id"] = None


async def _vary_qc(app, job, ctx, src_img, result, prepared):
    """결과 1개당 Vision 1회 + 정량. 판정은 서버 정책이 만든다."""
    from ..agents import edit_intent_vision
    from ..services import edit_intent_qc, edit_qc_scope

    s, pool = app.state.settings, app.state.pool
    observation, meta = None, None
    try:
        observation, meta = await edit_intent_vision.observe(
            s, baseline=src_img, edited=InlineImage(result.mime, result.image),
            # vary 요청을 그대로 넘긴다 — 빈 dict 를 넘기면 Vision 이 "요청대로 됐는가"를
            # 요청이 뭔지 모른 채 답한다.
            edit_type=ctx["edit_type"],
            adjustments={"changes": ctx.get("changes") or []},
            # 수집기와 **같은 helper** 로 변환한다 — 각자 조립하면 키 하나가 어긋난 채
            # 조용히 빈 범위로 도는 사고가 또 난다(9/N 30건이 그렇게 무효가 됐다).
            allowed_scope=edit_qc_scope.vision_scope(ctx["semantic_scope"]),
            source_refs=None)
    except Exception as e:
        meta = edit_intent_vision.failure_meta(e)
        log.warning("editor vary vision failed (job=%s status=%s)",
                    job["id"], meta["status"])
    decision = await asyncio.to_thread(
        edit_intent_qc.evaluate,
        baseline_bgr=_decode_bgr(src_img.data), edited_bgr=_decode_bgr(result.image),
        edit_type=ctx["edit_type"],
        allowed_scope=edit_qc_scope.qc_allowed_scope(),
        target_ratio=None, vision=observation, require_vision=True,
        semantic_scope=ctx["semantic_scope"], extra_entailed=ctx["entailed"])
    decision["vision"] = {"observation": observation, "meta": meta}
    await _emit(pool, job["id"], "step", {
        "status": "edit_intent_qc", "decision": decision["decision"],
        "unexpectedChanges": decision["unexpectedChanges"],
        "lockedInvariantViolations": decision["lockedInvariantViolations"],
        "requestedChangeSatisfied": decision["requestedChangeSatisfied"],
        "visionStatus": (meta or {}).get("status", "not_called")})
    return decision


async def _vary_session_fail(app, ctx, *, reason, qc_result=None, status="failed"):
    if not ctx:
        return
    try:
        async with app.state.pool.connection() as conn:
            await repo.update_edit_session(
                conn, session_id=ctx["session_id"], status=status,
                qc_result=qc_result or {"reason": reason})
            await conn.commit()
    except Exception as e:
        log.warning("vary session finalize failed (error=%s)", type(e).__name__)


def _decode_bgr(image_bytes: bytes):
    import cv2
    import numpy as np
    return cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)


async def run_editor_image_job(app, job: dict) -> None:
    s = app.state.settings
    pool = app.state.pool
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    lease_token = job["lease_token"]
    reserved = job.get("credits_reserved") or 0
    settle_key = f"credit:job:{job_id}:settle"
    payload = job.get("payload") or {}
    mode = payload.get("mode")
    example_warnings: list[dict] = []
    scene_qc_attempts: int | None = None  # bg 장소일치 QC 통과까지의 시도 수(관찰용, new 모드 bg만)
    garment_qc_metadata: dict | None = None  # new 모드만; vary 경로는 QC·메타 모두 무변경
    vary_ctx: dict | None = None  # Phase 3 vary 세션 컨텍스트 — new/플래그 off 는 끝까지 None

    async def _fail(message: str, meta: dict):
        try:
            async with pool.connection() as conn:
                await repo.finalize_editor_image_failure(
                    conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                    project_id=project_id, reserved=reserved, settle_key=settle_key,
                    message=message, metadata=meta)
                await conn.commit()
        except Exception:
            log.exception("editor_image finalize_failure error for job %s", job_id)

    try:
        image: bytes
        mime: str
        group: str | None
        cut_type: str | None
        fm_source: str | None = None      # 에디터 컷 아이덴티티 소스 — REAL 이면 성공 시 정산 대상
        fm_license_row: dict | None = None
        fm_face_injected = False          # REAL 자산 2장이 실제 첨부됐을 때만 정산(미첨부 과금 방지)

        if mode == "vary":
            source = payload.get("source") or {}
            changes = payload.get("changes") or []
            # AG-07에는 상품 Detail 원본이 첨부되지 않는다. 일반 제품컷 한 장을 확대해
            # 디테일을 지어내지 않도록 '디테일로 변경'은 새 이미지 추가(AG-06)에서만 허용한다.
            # 라우트가 raw dict를 받으므로 type 대소문자/미상값으로 우회해도, 계약상 detail을
            # 뜻하는 값 자체를 차단한다.
            detail_values = {"detail", "detail shot", "detailshot", "디테일", "디테일샷"}
            wants_detail = any(
                isinstance(change, dict)
                and str(change.get("value") or "").strip().lower() in detail_values
                for change in changes
            )
            if wants_detail:
                await _fail(
                    "현재 이미지 수정으로는 디테일샷을 만들 수 없어요. "
                    "새 이미지 추가에서 디테일샷을 선택해 주세요.",
                    {"error": "detail_variation_unsupported"},
                )
                return
            asset_id = _parse_source_asset_id(source.get("src"))
            src_asset = None
            ref_bg_asset = None
            async with pool.connection() as conn:
                if asset_id:
                    src_asset = await repo.get_asset_for_user(conn, user_id, asset_id)
                # 배경 레퍼런스(refBgAssetId) — 배경·조명·무드만 반영 (소유 검증 겸함, ADR-0004)
                rb = payload.get("refBgAssetId")
                if rb:
                    ref_bg_asset = await repo.get_asset_for_user(conn, user_id, str(rb))
            if src_asset is None:
                # src 원문은 남기지 않는다 — 클라이언트가 준 URL 에는 query·token 이 붙을 수
                # 있고, 어느 컷이었는지 알아내는 데 필요한 건 asset id 뿐이다.
                await _fail("변형할 컷을 찾을 수 없어요. 다시 시도해 주세요.",
                            {"error": "source_asset_missing", **_safe_asset_id(asset_id)})
                return
            src_img = InlineImage(
                src_asset["mime_type"],
                await asyncio.to_thread(app.state.r2.get_bytes, src_asset["r2_key"]))
            ref_bg_img = None
            if ref_bg_asset is not None:
                ref_bg_img = InlineImage(
                    ref_bg_asset["mime_type"],
                    await asyncio.to_thread(app.state.r2.get_bytes, ref_bg_asset["r2_key"]))
            await _emit(pool, job_id, "progress", {"progress": 20, "phase": "inputs_loaded"})

            cut_type = source.get("cutType")
            changes = payload.get("changes") or []
            # ── Phase 3: 세션이 붙어 있을 때만 계측·판정 경로를 탄다 ──
            # 플래그 off 는 session_id 가 없어 아래가 전부 no-op 이고, 호출도 저장도
            # 기존과 바이트 단위로 같다.
            vary_session_id = payload.get("editSessionId") if s.editor_vary_intent_qc != "off" else None
            vary_ctx = None
            if vary_session_id:
                vary_ctx = await _vary_preflight(
                    app, job, session_id=vary_session_id, src_asset=src_asset,
                    changes=changes, fail=_fail)
                if vary_ctx is None:
                    return          # preflight 가 이미 실패 종결했다 — provider 호출 0
            # 플래그 off 는 **기존 호출 그대로** 간다(generate wrapper). prepare/execute 는
            # 계측이 필요한 Phase 3 경로에서만 쓴다 — 계측 때문에 legacy 호출 모양을
            # 바꾸면 "완전 불변" 이 아니게 된다.
            prepared = None
            run_id = None
            t0 = time.monotonic()
            if vary_ctx is None:
                try:
                    image, mime = await cut_variator.generate(
                        s, app.state.gemini, src_img, changes, cut_type,
                        ref_bg=ref_bg_img)
                except GeminiError as e:
                    # provider 메시지에는 요청 URL·쿼리(키 포함 가능)와 응답 본문이 들어
                    # 있다. 그것이 job metadata 에 저장되면 API 응답으로도 나간다 —
                    # 원문 유지가 하위 호환 계약인 적은 없었다(에러 **코드**가 계약이다).
                    await _fail("컷 변형에 실패했어요. 다시 시도해 주세요.",
                                _editor_failure_meta(e))
                    return
                res = None
            else:
                prepared = cut_variator.prepare(
                    s, src_img, changes, cut_type, ref_bg=ref_bg_img)
                run_id = await _vary_run_begin(app, job, prepared, vary_ctx,
                                               src_asset=src_asset,
                                               ref_bg_asset=ref_bg_asset)
                try:
                    res = await cut_variator.execute(app.state.gemini, prepared)
                    image, mime = res.image, res.mime
                except GeminiError as e:
                    # 원문을 저장하지 않는다 — provider 메시지에는 URL·응답 본문이 있다.
                    await _vary_run_finish(app, job, run_id, vary_ctx, started=t0,
                                           error=e)
                    await _vary_session_fail(app, vary_ctx, reason="provider_error")
                    await _fail("컷 변형에 실패했어요. 다시 시도해 주세요.",
                                _editor_failure_meta(e))
                    return
            if vary_ctx:
                await _vary_run_finish(app, job, run_id, vary_ctx, started=t0,
                                       result=res)
                vary_ctx["decision"] = await _vary_qc(app, job, vary_ctx, src_img,
                                                      res, prepared)
                if (s.editor_vary_intent_qc == "enforce"
                        and vary_ctx["decision"]["decision"] == "reject"):
                    # R2 업로드 전에 끊는다 — 고아 객체를 만들지 않는 가장 싼 방법이다.
                    await _vary_session_fail(app, vary_ctx, reason="edit_intent_rejected",
                                             qc_result=vary_ctx["decision"],
                                             status="reject")
                    await _fail("요청한 변경이 반영되지 않았어요. 다시 시도해 주세요.",
                                {"error": "edit_intent_rejected",
                                 "editIntentQc": {
                                     "decision": "reject",
                                     "violations": vary_ctx["decision"][
                                         "lockedInvariantViolations"]}})
                    return
            group = None  # AG-07 결과는 misc 그룹 (계약 §6)
            cut_type = cut_type or "styling"  # cutType 미상 소스 → styling 가정(계약 §6)

        elif mode == "new":
            new_payload = content_roles.canonicalize_storyboard_block(payload)
            requested_color_id = new_payload.get("colorId")
            is_detail = new_payload.get("cutType") == "product" and new_payload.get("shot") == "detail"
            async with pool.connection() as conn:
                product = await repo.get_product(conn, project_id) or {}
                # analysis 도 로드 — 프롬프트 ground truth(소재·강조특징)와 확정 fitProfile
                # 텍스트 제약이 detail_page 경로와 동일하게 반영되도록(컷 파이프라인 계약 정합).
                analysis = await repo.get_analysis(conn, project_id) or {}
                # 일반 컷은 선택 색상을 엄격히 쓴다. 디테일만 목표 색상에 Detail이 없을 때
                # 기준색→첫 Detail 보유 색상의 근거를 붙이고 프롬프트에 색 전환을 명시한다.
                if is_detail:
                    image_refs, detail_color_transfer = cut_generator.detail_reference_images(
                        product, requested_color_id)
                else:
                    image_refs = cut_generator.color_images(product, requested_color_id)
                    detail_color_transfer = None
                assets = []
                for slot, aid in image_refs:
                    a = await repo.get_asset_for_user(conn, user_id, aid)
                    if a:
                        a["slot"] = slot  # 매니페스트 역할 라벨용
                        assets.append(a)
                # 무드 레퍼런스(refAssetIds) — 분위기(조명·색감)만 참고, 최대 3장 (ADR-0004)
                mood_rows = []
                for rid in [str(r) for r in (new_payload.get("refAssetIds") or [])][:3]:
                    ma = await repo.get_asset_for_user(conn, user_id, rid)  # 소유 검증 겸함
                    if ma:
                        mood_rows.append(ma)
            if is_detail and not any(asset.get("slot") == "Detail" for asset in assets):
                await _fail(
                    "디테일 참고 사진을 찾을 수 없어 디테일샷을 만들 수 없어요.",
                    {"error": "detail_reference_required", "colorId": requested_color_id},
                )
                return
            if not assets:
                if requested_color_id is not None:
                    await _fail("선택한 색상 이미지를 찾을 수 없어요. 다시 시도해 주세요.",
                                {"error": "no_selected_color_images",
                                 "colorId": requested_color_id})
                else:
                    # colorId가 없는 레거시 요청은 기존 기준 색상 폴백·에러 계약을 유지한다.
                    await _fail("기준 색상 이미지를 찾을 수 없어요. 다시 시도해 주세요.",
                                {"error": "no_base_color_images"})
                return
            # 컷 계약 필드 통과(ADR-0004) — mirror·얼굴·포즈·생성예시까지 서버 정규화에 맡긴다.
            # 촬영 세트는 콘티보드 전용이며 에디터의 독립 새 이미지에는 그룹을 전달하지 않는다.
            # 에디터 새 이미지 패널은 아직 매칭 의류를 고르는 UI·payload를 제공하지 않으므로
            # matchIds를 의도적으로 제외한다. 후속 배선 시에는 상세페이지와 같은 정책으로
            # styling·horizon·mirror에만 MATCHING을 첨부하고 product에는 적용하지 않는다.
            # colorId는 목표 색상이며, 디테일만 위 정책에 따라 타색 근거가 추가될 수 있다.
            cut_spec = {
                k: new_payload.get(k)
                for k in ("contentRole", "cutType", "direction", "shot", "faceExposure", "pose",
                          "outerClosureState", "exampleId", "modelId", "model_id",
                          "colorId", "refScope")
            }
            if detail_color_transfer:
                cut_spec["_detailColorTransfer"] = detail_color_transfer
            clothing_type = product.get("clothing_type") or product.get("clothingType") or "top"
            try:
                normalized = cut_generator.normalize_spec(cut_spec, clothing_type=clothing_type)
            except ValueError:
                await _fail("컷 설정이 올바르지 않아요. 다시 시도해 주세요.", {"error": "invalid_spec"})
                return

            # 아이덴티티 소스 1회 결정(detail_page 와 동일 계약, codex [P1]) — 실존 모델(UUID)은
            # REAL 로 비공개 자산을 첨부하고, 라이선스 실패면 조용한 폴백 없이 잡 실패(라우트 409
            # 게이트 이후 해지 레이스 방어). 가상모델('mA' 등)은 기존 VIRTUAL 경로 그대로.
            selected_model_id = normalized.get("modelId") or normalized.get("model_id")
            real_refs = None
            if s.facemarket_enabled and selected_model_id:
                async with pool.connection() as conn:
                    real_refs = await identity_source.resolve_real_model_assets(
                        conn, selected_model_id)
                    if real_refs is not None:
                        fm_license_row = await facemarket.resolve_model_license(
                            conn, selected_model_id)
                fm_source = identity_source.select_source(
                    selected_model_id=selected_model_id, license_row=fm_license_row,
                    has_real_assets=real_refs is not None, has_license_face=False)
                log.info("AG-06 identity source=%s job=%s hasReal=%s",
                         fm_source, job_id, real_refs is not None)
                if fm_source == "REJECTED":
                    await _fail("모델의 얼굴 라이선스가 활성 상태가 아니에요. 다시 확인해 주세요.",
                                {"error": "license_rejected", "modelId": str(selected_model_id)})
                    return

            # NewCutRequest.modelId가 이 경로의 정본. C방식 두 장을 원자적으로 로드하며,
            # 모르는 modelId/manifest/R2 실패는 모델 참조만 빼고 기존 상품 참조로 계속한다
            # (단, REAL 은 라이선스 소비 대상이라 자산 로드 실패 시 계속하지 않고 잡 실패).
            model_images: list[InlineImage] = []
            try:
                model_refs = (real_refs if real_refs is not None
                              else cut_generator.resolve_virtual_model_assets(normalized))
                if model_refs is not None:
                    # 버킷 인지 — 실존 모델 그리드(bucket='face')는 비공개 r2_face 에서 로드해
                    # 공개 버킷으로 얼굴 키가 새지 않게 한다(가상 모델은 bucket='public').
                    model_images = []
                    for ref in model_refs:
                        client = (app.state.r2_face if ref.get("bucket") == "face"
                                  else app.state.r2)
                        if client is None:
                            raise RuntimeError("bucket client unavailable")
                        model_images.append(
                            InlineImage(ref["mime"],
                                        await asyncio.to_thread(client.get_bytes, ref["key"])))
            except Exception as e:
                if fm_source == "REAL":
                    await _fail("모델 자산을 불러오지 못했어요. 다시 시도해 주세요.",
                                {"error": "real_model_assets_unavailable",
                                 "detail": repr(e)[:200]})
                    return
                log.warning(
                    "AG-06 virtual model assets unavailable for job %s model %s; "
                    "continuing without model references: %r",
                    job_id, normalized.get("modelId"), e)
                model_images = []
            fm_face_injected = fm_source == "REAL" and len(model_images) == 2
            product_images = [
                InlineImage(a["mime_type"], await asyncio.to_thread(
                    app.state.r2.get_bytes, a["r2_key"]))
                for a in assets
            ]
            mood_images = [
                InlineImage(a["mime_type"], await asyncio.to_thread(
                    app.state.r2.get_bytes, a["r2_key"]))
                for a in mood_rows
            ]
            images = [*model_images, *product_images, *mood_images]
            # 순서 = 매니페스트: MODEL 2장? → 상품 슬롯들 → 무드
            example_scope = None
            example_id = normalized.get("exampleId")
            pose_overrides_example = (
                normalized["pose"] != "auto" and normalized["refScope"] == "pose"
            )
            if example_id and not pose_overrides_example:
                scope = normalized["refScope"]
                if example_id.startswith("ss_"):
                    try:
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
                        example_image = await space_set_assets.load_space_set_image(
                            s,
                            example_reference["asset"],
                            role="전체 예시" if scope == "all" else "포즈",
                        )
                    except space_set_assets.SpaceSetBindingError as exc:
                        await _fail(
                            exc.message,
                            {
                                "error": exc.code,
                                "exampleId": example_id,
                                "refScope": scope,
                            },
                        )
                        return
                else:
                    status = cut_generator.example_asset_status(
                        example_id, clothing_type, scope)
                    if status in ("not_applicable", "variant_unpublished"):
                        example_warnings.append({
                            "code": "example_not_applicable"
                            if status == "not_applicable" else "example_variant_unpublished",
                            "exampleId": example_id,
                            "clothingType": clothing_type,
                            "refScope": scope,
                        })
                        # 미첨부 all 예시의 레거시 EXNUANCE까지 제거해 예시가 완전히 무효가 되게 한다.
                        cut_spec["exampleId"] = None
                        example_image = None
                    elif scope == "pose" and not cut_generator.pose_direction_compatible(
                        example_id, normalized
                    ):
                        # 단건 에디터는 배치의 빈 슬롯 대신 명시적 실패로 닫는다. 이 지점은
                        # 이미지 로드와 Gemini 호출보다 앞이라 불일치 조합의 생성 호출은 0회다.
                        await _fail(
                            "이 예시의 포즈 방향이 현재 컷 방향과 맞지 않아요. 다른 예시를 선택해 주세요.",
                            {
                                "error": "pose_direction_incompatible",
                                "exampleId": example_id,
                                "direction": normalized.get("direction"),
                            },
                        )
                        return
                    else:
                        example_image = await cut_generator.load_example_image(
                            s, example_id, scope=scope, clothing_type=clothing_type)
                        if example_image is None and scope in ("pose", "bg"):
                            # 전용 자산 없이 pose/bg를 생성하면 "참고한 척"이 된다 — 무음 강등 금지
                            # (2026-07-20 실측: 이 강등이 bg 실패의 실제 원인 일부였다. ADR-0009 §2)
                            await _fail("예시 자산을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.",
                                        {"error": "example_asset_unavailable",
                                         "exampleId": example_id, "refScope": scope})
                            return
                if example_image is not None:
                    # bg 플레이트는 시각 앵커가 약해 마지막 첨부로는 무시된다(2026-07-20
                    # 파일럿 실측: 텍스트 강화만으로 2/7) — 첫 첨부로 올려 프라이머시를 준다.
                    if scope == "bg":
                        images.insert(0, example_image)
                    else:
                        images.append(example_image)
                    example_scope = scope
            await _emit(pool, job_id, "progress", {"progress": 20, "phase": "inputs_loaded"})

            manifest = cut_generator.build_manifest(
                assets, has_mannequin=False, has_match=False, mood_count=len(mood_rows),
                has_model_face=len(model_images) == 2, has_model_sheet=len(model_images) == 2,
                example_scope=example_scope,
                example_is_product=normalized["cutType"] == "product")
            try:
                image, mime = await cut_generator.generate(
                    s, app.state.gemini, cut_spec, product, images,
                    analysis=analysis, manifest=manifest)
            except ValueError as e:
                if str(e) == "detail_reference_required":
                    await _fail(
                        "디테일 참고 사진을 찾을 수 없어 디테일샷을 만들 수 없어요.",
                        {"error": "detail_reference_required", "colorId": requested_color_id},
                    )
                else:
                    await _fail("컷 설정이 올바르지 않아요. 다시 시도해 주세요.",
                                {"error": "invalid_spec"})
                return
            except GeminiError as e:
                await _fail("컷 생성에 실패했어요. 다시 시도해 주세요.",
                            _editor_failure_meta(e))
                return

            scene_plate = None
            # bg 편집 컷 — 장소일치 QC 게이트(2026-07-20): 생성은 샘플링이라 편집 프레이밍을
            # 줘도 확률적으로 다른 장소가 나온다. 플레이트(첫 첨부)와 대조해 불일치면 재생성,
            # 상한 초과면 실패 종결(부분 성공 아님 — 에디터는 단건). QC 판정 불능은 fail-open.
            if example_scope == "bg" and example_image is not None:
                attempts_max = max(1, s.bg_scene_qc_attempts)
                scene_plate = example_image
                attempt = 1
                while True:
                    try:
                        scene_qc = await image_qc.scene_verdict(
                            s, scene_plate, InlineImage(mime, image))
                    except VisionError as e:
                        log.warning("AG-06 scene QC unavailable job %s: %r — fail-open", job_id, e)
                        example_warnings.append({"code": "scene_qc_unavailable"})
                        break
                    if scene_qc["verdict"] == "pass":
                        break
                    if attempt >= attempts_max:
                        await _fail("배경 예시의 장소를 재현하지 못했어요. 다시 시도해 주세요.",
                                    {"error": "bg_scene_mismatch",
                                     "attempts": attempt,
                                     "mismatches": scene_qc["mismatches"][:5]})
                        return
                    attempt += 1
                    try:
                        image, mime = await cut_generator.generate(
                            s, app.state.gemini, cut_spec, product, images,
                            analysis=analysis, manifest=manifest)
                    except (GeminiError, ValueError) as e:
                        await _fail("컷 생성에 실패했어요. 다시 시도해 주세요.",
                                    _editor_failure_meta(e))
                        return
                scene_qc_attempts = attempt

            async def _generate_candidate():
                candidate_image, candidate_mime = await cut_generator.generate(
                    s, app.state.gemini, cut_spec, product, images,
                    analysis=analysis, manifest=manifest)
                if scene_plate is None:
                    return InlineImage(candidate_mime, candidate_image)

                candidate_attempt = 1
                while True:
                    try:
                        scene_qc = await image_qc.scene_verdict(
                            s, scene_plate, InlineImage(candidate_mime, candidate_image))
                    except VisionError as e:
                        log.warning(
                            "AG-06 candidate scene QC unavailable job %s: %r — fail-open",
                            job_id, e)
                        example_warnings.append({"code": "scene_qc_unavailable"})
                        break
                    if scene_qc["verdict"] == "pass":
                        break
                    if candidate_attempt >= max(1, s.bg_scene_qc_attempts):
                        raise RuntimeError("bg candidate scene mismatch")
                    candidate_attempt += 1
                    candidate_image, candidate_mime = await cut_generator.generate(
                        s, app.state.gemini, cut_spec, product, images,
                        analysis=analysis, manifest=manifest)
                return InlineImage(candidate_mime, candidate_image)

            chosen, garment_qc_metadata, garment_warnings = await image_qc.best_of(
                s,
                product_images,
                InlineImage(mime, image),
                _generate_candidate,
            )
            image, mime = chosen.data, chosen.mime
            example_warnings.extend(garment_warnings)
            group = normalized["colorId"] or None
            cut_type = normalized["cutType"]

        else:
            await _fail("알 수 없는 요청이에요. 다시 시도해 주세요.", {"error": "unknown_mode", "mode": mode})
            return

        await _emit(pool, job_id, "progress", {"progress": 70, "phase": "generated"})

        # R2 저장
        ext = ext_for_mime(mime) or _EXT_FALLBACK.get(mime, "png")
        asset_id = str(uuid.uuid4())
        key = ai_key(user_id, project_id, job_id, asset_id, ext)
        await asyncio.to_thread(app.state.r2.put_bytes, key, image, mime, cache=IMMUTABLE_CACHE)
        w, h = _image_dims(image)
        image_row = {
            "asset_id": asset_id, "bucket": s.r2_bucket, "key": key, "mime": mime,
            "size": len(image), "width": w, "height": h,
        }

        # 성공 종결 (원자·lease 펜스). charge = reserved — 예약 시점 견적 확정(부분 성공 없음.
        # 실행 시점 설정 재조회 금지 — 단가 변경이 배포 사이에 끼면 예약액과 다른 차감 발생).
        charge = reserved
        success_metadata = {"creditCostVersion": s.credit_cost_version}
        if scene_qc_attempts is not None:
            success_metadata["sceneQc"] = {"attempts": scene_qc_attempts}
        if garment_qc_metadata is not None:
            success_metadata["garmentQc"] = garment_qc_metadata
        if example_warnings:
            success_metadata["warnings"] = example_warnings
        vary_finalize = None
        if vary_ctx and vary_ctx.get("decision"):
            d = vary_ctx["decision"]["decision"]
            # shadow 는 저장 계약을 바꾸지 않는다 — reject 판정이어도 결과는 나가고 사람이
            # 본다. 원래 decision 은 edit_qc_result 에 그대로 보존된다.
            status = "pass" if d == "pass" else "review_required"
            vary_finalize = {
                "id": vary_ctx["session_id"], "status": status, "qc_status": status,
                "qc_result": vary_ctx["decision"],
                "lineage": {"generation_run_id": vary_ctx.get("run_id"),
                            "parent_output_id": vary_ctx.get("parent_output_id"),
                            "output_sha256": hashlib.sha256(image).hexdigest(),
                            "transformation": {"editorVary": {
                                "changes": vary_ctx["changes"],
                                "editType": vary_ctx["edit_type"]}}},
            }
            success_metadata["editIntentQc"] = {
                "decision": d, "status": status,
                "editSessionId": vary_ctx["session_id"]}
        try:
            async with pool.connection() as conn:
                out = await repo.finalize_editor_image_success(
                    conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                    project_id=project_id, image=image_row, group=group, cut_type=cut_type,
                    reserved=reserved, charge=charge,
                    metadata=success_metadata, edit_session=vary_finalize)
                await conn.commit()
        except Exception as e:
            # Phase 3 vary 는 계보 실패에 fail-open 하지 않는다 — 계보 없는 결과를 진열하면
            # "어느 편집의 결과인지 모르는 컷"이 사용자에게 나간다. mode:new·flag-off 는
            # 애초에 vary_finalize 가 None 이라 이 경로를 타지 않는다.
            if vary_finalize is None:
                raise
            log.warning("editor vary finalize failed (job=%s error=%s)",
                        job_id, type(e).__name__)
            await _r2_cleanup(app, key)
            await _vary_session_fail(app, vary_ctx, reason="finalize_failed",
                                     status="failed")
            await _fail("결과를 저장하지 못했어요. 다시 시도해 주세요.",
                        {"error": "finalize_failed"})
            return
        if out is None:  # lease 상실(복구) → 결과 폐기 + 방금 저장한 R2 객체 best-effort 정리
            await _r2_cleanup(app, key)
        elif (s.facemarket_enabled and fm_face_injected and fm_license_row is not None
              and fm_license_row.get("unit_price") is not None
              and getattr(app.state, "fm_chain", None) is not None):
            # FaceMarket 온체인 정산 훅(선택과제2) — 에디터 컷도 얼굴 라이선스 1회 사용으로
            # detail_page 와 동일하게 70/20/10 기록. payment_key=job:{id} 멱등(컨트랙트 중복
            # revert + fm_settlements UNIQUE). best-effort: 정산 실패가 완료된 생성을 안 되돌림.
            try:
                await facemarket.record_license_settlement(
                    app, payment_key=f"job:{job_id}", license_id=str(fm_license_row["id"]),
                    model_id=str(fm_license_row["model_id"]),
                    total=int(fm_license_row["unit_price"]), job_id=job_id)
            except Exception:
                log.exception("editor_image settlement hook failed for job %s", job_id)
    except Exception as e:  # 예기치 못한 오류도 lease 펜스 종결로
        # 최상위 핸들러 — 어떤 예외든 원문을 저장하지 않는다(URL·토큰·프롬프트·SQL 이
        # 섞여 들어올 수 있고, job metadata 는 API 응답으로 나간다).
        # 도메인 코드는 그 자체가 계약이라 **모양도 그대로** 둔다(부가 필드 없음).
        # provider·예상 밖 예외에만 분류를 붙인다.
        code = _safe_error_code(e)
        await _fail("이미지 생성 중 오류가 발생했어요. 다시 시도해 주세요.",
                    {"error": code} if code else
                    {"error": "generation_failed", "category": _provider_category(e),
                     "errorType": type(e).__name__})
