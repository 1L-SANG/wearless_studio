"""AG-04 마네킹 생성 워커 (요리사). dispatcher가 claim한 job 1건을 실행한다.

흐름: 입력 로드(베이스+상품사진+하의) → 단일 tier(기본 image_high=Gemini 3 Pro,
Flash·승격 없음) 생성 → QC(기본 shadow: 판정 로그만, 게이팅 시 같은 모델 재시도) → 통과본 R2 저장
→ finalize(에셋·컷·크레딧·done/error, 원자·lease 펜스). 생성/네트워크는 to_thread·async로 격리.
"""

import asyncio
import hashlib
import json
import logging
import uuid
from contextlib import suppress
from io import BytesIO

from PIL import Image

log = logging.getLogger("wearless.mannequin_job")

from .. import repo
from ..agents import image_qc, mannequin, mannequin_fit_qc
from ..agents.gemini_image import GeminiError, InlineImage
from ..agents.model_routing import resolve_model
from ..agents.prompts import load_prompt_template, render_mannequin_prompt
from ..r2 import ai_key, ext_for_mime
from ..services import qc
from ._common import emit_job_event as _emit  # 공용 헬퍼 (analyze_job과 공유)

_EXT_FALLBACK = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def _canonical_profile_hash(profile) -> str:
    """렌더러 입력 프로필의 canonical JSON(sort_keys·compact·null 포함) SHA-256 (fidelity D3)."""
    payload = json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fit_profile_for_match_image(profile: dict | None, has_match_image: bool) -> dict | None:
    """화면에 매칭 의류가 없으면 v1/v2 매칭 축 지시를 모두 제거한다."""
    if not profile or has_match_image:
        return profile
    return {k: v for k, v in profile.items() if k not in ("matchCut", "matchingFit")}


_GENERATION_PROGRESS_INTERVAL_SECONDS = 7.0
_GENERATION_PROGRESS_MAX = 84


def _image_dims(data: bytes) -> tuple[int | None, int | None]:
    try:
        im = Image.open(BytesIO(data))
        return im.width, im.height
    except Exception:
        return None, None


# 첨부 이미지 슬롯 → 모델용 라벨. prompt ${imageManifest} 가 이 목록을 받는다.
_SLOT_LABEL = {
    "Front": "front view of the garment",
    "Back": "back view of the garment",
    "Detail": "detail close-up of the garment (texture, stitching, trims, print)",
    "Fit": "fit reference — the garment worn on a real person (true length & how it sits)",
}


def _build_manifest(prod_assets: list[dict], has_match: bool) -> str:
    """images=[base, *prod(slot순), match]와 동일 순서의 역할 목록 (모델이 어느 이미지가 무엇인지 알게).
    내용은 전부 고정 라벨(_SLOT_LABEL 룩업) — 셀러 데이터를 직접 끼우지 않는다(프롬프트 인젝션 방지).
    의류 종류는 sanitize된 ${clothingType}·PRODUCT CONTEXT로 따로 전달되므로 여기엔 넣지 않는다."""
    lines = ["1. Base mannequin — the canvas to dress (keep it identical)"]
    i = 2
    for a in prod_assets:
        lines.append(f"{i}. {_SLOT_LABEL.get(a.get('slot'), 'view of the garment')}")
        i += 1
    if has_match:
        lines.append(f"{i}. matching BOTTOM garment — also dress the mannequin in this, coordinated with the top")
    return "\n".join(lines)


# 검색 증강 Phase 3 (retrieval_upgrade_prd FR-C): 유사한 '성공 스튜디오 컷'을 STYLE REFERENCE 로
# 첨부해 컷 간 톤·조명·프레이밍·마감 일관성을 끌어올린다. 최대 리스크 = 레퍼런스의 '다른 옷'이
# 결과에 새는 오염 → 아래 가드로 look-only 를 강하게 못박고, image_qc(①동일성)로 계측한다.
_STYLE_REF_GUARD = (
    "STYLE REFERENCE images (labeled in the manifest) are provided ONLY as examples of the target "
    "studio look — lighting, background tone, camera framing and finish. They show DIFFERENT garments. "
    "NEVER copy any garment, color, pattern, print, logo, or detail from a STYLE REFERENCE; the garment "
    "identity comes exclusively from the product photos and the PRODUCT CONTEXT."
)
_REF_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


def _ref_manifest_lines(start_index: int, n: int) -> str:
    """images 끝에 붙는 STYLE REFERENCE 슬롯의 매니페스트 라벨(고정 문자열 — 셀러 데이터 미포함)."""
    return "\n".join(
        f"{start_index + i}. STYLE REFERENCE — target studio look ONLY "
        "(a DIFFERENT garment; never copy its garment)"
        for i in range(n)
    )


async def _load_style_refs(app, s, *, prod_imgs, clothing_type, gender):
    """retrieval_refimages=on 시 프론트 상품 이미지로 유사 레퍼런스 컷 top-k 검색 → 바이트 로드.
    best-effort — 임베딩/검색/로드 실패는 조용히 ([], []) (생성 절대 안 막음, FR-C).
    프리필터(cut_type='mannequin' + clothing_type/gender)로 좁힌 풀에서만 벡터 랭킹(FR-A2 원칙).
    clothing_type 이 코퍼스와 어휘 불일치로 빈 결과면 clothing_type 없이 1회 폴백."""
    if getattr(s, "retrieval_refimages", "off") != "on" or not prod_imgs:
        return [], []
    try:
        from ..services import embeddings as E
        qv = await asyncio.to_thread(
            E.embed_image, prod_imgs[0].data,
            model_id=s.embed_image_model, expected_dim=s.embed_image_dim)
    except Exception as e:  # torch 미설치·모델 로드 실패 등 → 조용히 스킵
        log.warning("style_ref embed 실패: %r", e)
        return [], []
    topk = getattr(s, "ref_images_topk", 2)
    try:
        async with app.state.pool.connection() as conn:
            hits = await repo.search_ref_images(
                conn, qv, cut_type="mannequin", embed_model=s.embed_image_model,
                clothing_type=clothing_type or None, gender=gender or None, k=topk)
            if not hits and clothing_type:  # 어휘 불일치 폴백
                hits = await repo.search_ref_images(
                    conn, qv, cut_type="mannequin", embed_model=s.embed_image_model,
                    gender=gender or None, k=topk)
    except Exception as e:
        log.warning("style_ref 검색 실패: %r", e)
        return [], []
    refs, ids = [], []
    for h in hits:
        try:
            data = await asyncio.to_thread(app.state.r2.get_bytes, h["r2_key"])
        except Exception as e:
            log.warning("style_ref 로드 실패 %s: %r", h.get("id"), e)
            continue
        ext = (h["r2_key"].rsplit(".", 1)[-1] if "." in h["r2_key"] else "").lower()
        refs.append(InlineImage(_REF_MIME.get(ext, "image/jpeg"), data))
        ids.append(h["id"])
    return refs, ids


# P1 축 QC enforce 승격 가드 — env·요청·payload·CLI 어떤 경로로도 우회 불가한 코드 레벨 스위치
# (G9 규율: 설정 실수 하나가 prod 생성을 죽이는 사고 방지). enforce 설정 + 가드 False = 실질 shadow.
# 2026-07-14 True 승격(사용자 결정): 미달 컷 출고 방지 > 오발화 비용(내부 +1콜·지연 수십초).
# 근거 = §I 실증(실패→편집→채택 완주, 통과 시 무개입, 개선 실패 시 원본 유지 — 하방 없음).
# 오발화·판정 정확도는 axis_qc/axis_retry 이벤트로 관측, 골드셋 캘리브레이션은 켠 상태로 병행.
_MANNEQUIN_AXIS_QC_ENFORCEMENT_READY = True


def _effective_axis_qc_mode(s) -> str:
    mode = getattr(s, "mannequin_axis_qc", "off")
    if mode == "enforce" and not _MANNEQUIN_AXIS_QC_ENFORCEMENT_READY:
        return "shadow"
    return mode


async def _apply_axis_qc(
    *, pool, gemini, s, job_id, candidate, attempt, model, res,
    prod_imgs, match_img, fit_profile, profile_hash,
):
    """생성 채택본에 축 QC 판정 + (enforce 시) 편집 교정 1회. → (선택 결과, 편집콜 소비 여부).

    모든 인프라 실패는 fail-open(원본 유지·이벤트만) — 축 QC가 생성을 죽이는 일은 없다.
    이벤트에는 해시·판정 결과만(프롬프트/프로필/편집지시 원문 미포함).
    """
    configured = getattr(s, "mannequin_axis_qc", "off")
    if configured == "off":
        return res, False
    axis_spec = mannequin_fit_qc.declared_axis_spec(fit_profile)
    if not axis_spec:
        return res, False
    effective = _effective_axis_qc_mode(s)
    original_hash = hashlib.sha256(res.image).hexdigest()
    base_event = {
        "candidate": candidate, "attempt": attempt,
        "configured_mode": configured, "effective_mode": effective,
        "enforcement_ready": _MANNEQUIN_AXIS_QC_ENFORCEMENT_READY,
        "profile_hash": profile_hash,
    }

    async def _judge(image):
        return await mannequin_fit_qc.verdict(
            s, prod_imgs, InlineImage(image.mime, image.image), fit_profile, match_img)

    async def _emit_qc(subject, image_hash, v, outcome, err=None):
        payload = {**base_event, "status": "axis_qc", "subject": subject,
                   "image_hash": image_hash,
                   "identity_pass": None if v is None else v["identityPass"],
                   "axis_pass": [] if v is None else [
                       {"axis": x["axis"], "target": x["target"], "pass": x["pass"],
                        "visible": x["visible"],
                        "observed_landmark": x["observedLandmark"][:160]}
                       for x in v["axisPass"]],
                   "mismatches": [] if v is None else v["mismatches"],
                   "outcome": outcome,
                   "error_type": type(err).__name__ if err else None,
                   "error_message": str(err)[:200] if err else None}
        await _emit(pool, job_id, "step", payload)

    async def _emit_retry(outcome, *, fired=False, failed=(), edit_hash=None,
                          edited_hash=None, edit_attempt=None):
        await _emit(pool, job_id, "step", {
            **base_event, "status": "axis_retry", "fired": fired,
            "edit_attempt": edit_attempt,
            "failed_axes": [{"axis": e["axis"], "target": e["value"]} for e in failed],
            "edit_hash": edit_hash, "original_image_hash": original_hash,
            "edited_image_hash": edited_hash, "outcome": outcome})

    try:
        v1 = await _judge(res)
    except Exception as e:
        log.warning("axis_qc initial judge failed for job %s: %r", job_id, e)
        await _emit_qc("generated", original_hash, None, "error", e)
        await _emit_retry("original_judge_error")
        return res, False
    failed = mannequin_fit_qc.failed_axis_specs(axis_spec, v1)
    await _emit_qc("generated", original_hash, v1, "fail" if failed else "pass")
    if not failed:
        await _emit_retry("not_needed")
        return res, False
    instruction = mannequin_fit_qc.build_edit_instruction(failed)
    edit_hash = hashlib.sha256(instruction.encode("utf-8")).hexdigest()
    if effective != "enforce":
        await _emit_retry("enforce_guarded" if configured == "enforce" else "shadow_observed",
                          failed=failed, edit_hash=edit_hash)
        return res, False
    if attempt >= s.mannequin_max_attempts:  # 공유 예산: 생성+편집 <= max_attempts
        await _emit_retry("budget_exhausted", failed=failed, edit_hash=edit_hash)
        return res, False
    edit_attempt = attempt + 1
    try:
        edited = await gemini.generate_content_image(
            model, instruction, [InlineImage(res.mime, res.image)],
            s.mannequin_image_size, aspect_ratio=s.mannequin_aspect_ratio)
    except GeminiError as e:
        log.warning("axis_qc edit call failed for job %s: %r", job_id, e)
        await _emit_retry("edit_error", fired=True, failed=failed, edit_hash=edit_hash,
                          edit_attempt=edit_attempt)
        return res, True
    edited_hash = hashlib.sha256(edited.image).hexdigest()
    try:
        v2 = await _judge(edited)
    except Exception as e:
        log.warning("axis_qc edited judge failed for job %s: %r", job_id, e)
        await _emit_qc("edited", edited_hash, None, "error", e)
        await _emit_retry("edit_judge_error", fired=True, failed=failed, edit_hash=edit_hash,
                          edited_hash=edited_hash, edit_attempt=edit_attempt)
        return res, True
    failed2 = mannequin_fit_qc.failed_axis_specs(axis_spec, v2)
    await _emit_qc("edited", edited_hash, v2, "fail" if failed2 else "pass")
    if mannequin_fit_qc.edit_improves(v1, v2):
        await _emit_retry("edited_selected", fired=True, failed=failed, edit_hash=edit_hash,
                          edited_hash=edited_hash, edit_attempt=edit_attempt)
        return edited, True
    await _emit_retry("original_kept", fired=True, failed=failed, edit_hash=edit_hash,
                      edited_hash=edited_hash, edit_attempt=edit_attempt)
    return res, True


def gate_decision(s, pillow_verdict_str: str, p2) -> tuple[bool, bool]:
    """생성 컷 게이팅 결정 (순수) → (pillow_reject, p2_reject).

    - Pillow QC(휴리스틱): **재캘리브 전까지 코드에서 강제 shadow** — 실측 분포에서
      missing_lower_body 오탐이 상수(다리가 있어도 bboxBottom 0.93 에서 오탐, pass율 0%)라,
      MANNEQUIN_QC_ENABLED=true 인 어떤 배포/체크아웃이 큐를 클레임하든 전 생성이 죽는
      사고가 된다(2026-07-12 prod 실사고 — 공유 DB 를 폴링하던 QC=true env 프로세스가
      사용자 잡을 가로채 전멸). services/qc.py 임계 재캘리브 후 이 가드를 되살릴 것.
    - AG-P2(vision 동일성): image_qc=='enforce' 且 p2.verdict=='retry' → reject.
      off/shadow 는 게이트 안 함(항상 통과 — 기존 동작 불변). p2 없음(키미설정·판정실패)도 통과.
    """
    pillow_reject = False  # 강제 shadow — s.mannequin_qc_enabled 는 재캘리브 전까지 게이트에 미사용
    p2_reject = s.image_qc == "enforce" and isinstance(p2, dict) and p2.get("verdict") == "retry"
    return pillow_reject, p2_reject


async def _run_candidate(
    *, app, job, candidate, base_fit, base_gender, base_img, prod_imgs, match_img,
    product_count, template, product, analysis, clothing_type, image_manifest="", fit_profile=None,
    adjusted_axes=(), fit_profile_source="legacy_analysis_fallback", ref_imgs=(),
) -> dict | None:
    """후보 1개 생성. 통과 시 R2 저장 후 finalize용 dict 반환, 실패 시 None."""
    s = app.state.settings
    pool, r2, gemini = app.state.pool, app.state.r2, app.state.gemini
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    # STYLE REFERENCE(있으면)는 상품·매칭 뒤 맨 끝에 붙는다 — 매니페스트 번호 순서와 일치.
    images = [base_img, *prod_imgs] + ([match_img] if match_img else []) + list(ref_imgs)
    ctx = mannequin.prompt_context(
        clothing_type=clothing_type, product_count=product_count,
        base_gender=base_gender, image_manifest=image_manifest, fit_profile=fit_profile,
        adjusted_axes=adjusted_axes,
    )
    base_prompt = render_mannequin_prompt(
        template, ctx, product, analysis,
        seller_canon=s.seller_text_canonicalize, knowledge=s.retrieval_knowledge,
    )
    if ref_imgs:  # 레퍼런스 첨부 시에만 오염 가드를 프롬프트 말미에 강조(look-only)
        base_prompt = f"{base_prompt}\n\n{_STYLE_REF_GUARD}"
    # AG-04는 처음부터 단일 tier(기본 image_high=Pro, 사용자 결정 — Flash·승격 없음).
    # QC 게이팅 시 같은 모델로 재시도(re-roll + 교정 피드백). shadow면 첫 결과 채택.
    model = resolve_model(s, s.mannequin_tier)
    feedback = ""
    profile_hash = _canonical_profile_hash(fit_profile)
    for attempt in range(1, s.mannequin_max_attempts + 1):
        prompt = f"{feedback}\n\n{base_prompt}" if feedback else base_prompt
        # 관측성(fidelity 설계 D3): 이 attempt 가 실제 쓰는 프로필·프롬프트의 다이제스트만 남긴다
        # (원문 미포함 — 이벤트 ~250B). 실패 원인이 되지 않게 기존 step 과 동일 best-effort.
        await _emit(pool, job_id, "step", {
            "status": "prompt_rendered", "candidate": candidate, "attempt": attempt,
            "profile_hash": profile_hash,
            "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "prompt_version": s.mannequin_prompt_version,
            "input_source": fit_profile_source})
        try:
            res = await gemini.generate_content_image(
                model, prompt, images, s.mannequin_image_size,
                aspect_ratio=s.mannequin_aspect_ratio)
        except GeminiError as e:
            await _emit(pool, job_id, "step", {
                "candidate": candidate, "model": model, "attempt": attempt,
                "status": "error", "message": str(e)[:200]})
            continue
        verdict = qc.evaluate_mannequin_qc(res.image)
        await _emit(pool, job_id, "step", {
            "candidate": candidate, "model": model, "attempt": attempt, "status": "generated",
            # metrics 도 남긴다 — shadow 재캘리브(임계 튜닝)의 실측 근거. verdict/reasons 만으론
            # 왜 걸렸는지(bboxBottom·aspect·하단비율) 모른다.
            "qc": {"verdict": verdict.verdict, "reasons": verdict.reasons, "metrics": verdict.metrics}})
        # AG-P2 이미지 동일성 검수 — shadow(로그만)·enforce(게이트) 시 판정. off면 skip.
        # vision 실패(키미설정 등)는 삼켜 p2=None → 게이트 미적용(생성 자체 안 막음).
        # STYLE REFERENCE 첨부 시 오염(다른 옷 유출)을 반드시 계측 — image_qc=off 여도 최소 shadow 로
        # 승격해 동일성 판정을 기록한다(게이팅 아님 — enforce 만 reject, gate_decision). off↔측정 결합.
        eff_image_qc = s.image_qc if s.image_qc != "off" else ("shadow" if ref_imgs else "off")
        p2 = None
        if eff_image_qc in ("shadow", "enforce") and prod_imgs:
            try:
                p2 = await image_qc.verdict(s, prod_imgs, InlineImage(res.mime, res.image))
                await _emit(pool, job_id, "step", {
                    "candidate": candidate, "attempt": attempt, "status": "image_qc", "imageQc": p2})
            except Exception as e:
                log.warning("AG-P2 image_qc failed for job %s: %r", job_id, e)
        # 게이팅: Pillow QC + AG-P2. 둘 다 통과면 채택(off/shadow 는 항상 통과 — 기존 동작 불변).
        pillow_reject, p2_reject = gate_decision(s, verdict.verdict, p2)
        if not pillow_reject and not p2_reject:
            # P1 축 QC: 채택본이 선언 핏 축을 반영했는지 판정, enforce면 편집 교정 1회
            # (실패 이미지 편집 — §H 실증). fail-open: 어떤 실패도 채택 자체를 막지 않는다.
            res, _ = await _apply_axis_qc(
                pool=pool, gemini=gemini, s=s, job_id=job_id, candidate=candidate,
                attempt=attempt, model=model, res=res, prod_imgs=prod_imgs,
                match_img=match_img, fit_profile=fit_profile, profile_hash=profile_hash)
            ext = ext_for_mime(res.mime) or _EXT_FALLBACK.get(res.mime, "png")
            asset_id = str(uuid.uuid4())
            key = ai_key(user_id, project_id, job_id, asset_id, ext)
            await asyncio.to_thread(r2.put_bytes, key, res.image, res.mime)
            w, h = _image_dims(res.image)
            return {
                "asset_id": asset_id, "bucket": s.r2_bucket, "key": key, "mime": res.mime,
                "size": len(res.image), "width": w, "height": h,
                "candidate": candidate, "base_fit": base_fit,
            }
        # reject → 재시도 프롬프트에 교정 피드백 주입(Pillow 사유 + AG-P2 correctionPrompt).
        # 정체성 게이트가 선점하면 축 QC/편집은 이 attempt에서 미실행 — 잘못된 옷을 편집하면
        # 그 정체성이 보존되므로 신규 생성(re-roll)이 우선한다(설계 결정 3).
        if (getattr(s, "mannequin_axis_qc", "off") != "off"
                and mannequin_fit_qc.declared_axis_spec(fit_profile)):
            await _emit(pool, job_id, "step", {
                "status": "axis_retry", "candidate": candidate, "attempt": attempt,
                "configured_mode": s.mannequin_axis_qc,
                "effective_mode": _effective_axis_qc_mode(s),
                "enforcement_ready": _MANNEQUIN_AXIS_QC_ENFORCEMENT_READY,
                "profile_hash": profile_hash, "fired": False, "edit_attempt": None,
                "failed_axes": [], "edit_hash": None,
                "original_image_hash": hashlib.sha256(res.image).hexdigest(),
                "edited_image_hash": None, "outcome": "identity_gate_preempted"})
        parts = []
        if pillow_reject:
            parts.append(qc.format_qc_feedback(verdict))
        if p2_reject and isinstance(p2, dict) and p2.get("correctionPrompt"):
            parts.append("CORRECTION (generate the SAME garment as the product photos): "
                         + p2["correctionPrompt"])
        feedback = "\n\n".join(parts)
    return None  # max_attempts 내 통과본 없음 → 이 후보 드롭(부분 성공 허용)


async def run_mannequin_job(app, job: dict) -> None:
    s = app.state.settings
    pool = app.state.pool
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    lease_token = job["lease_token"]
    reserved = job.get("credits_reserved") or 0
    settle_key = f"credit:job:{job_id}:settle"

    async def _fail(message: str, meta: dict):
        async with pool.connection() as conn:
            await repo.finalize_mannequin_failure(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                project_id=project_id, reserved=reserved, settle_key=settle_key,
                message=message, metadata=meta)
            await conn.commit()

    try:
        # 1) 입력 로드
        async with pool.connection() as conn:
            product = await repo.get_product(conn, project_id) or {}
            analysis = await repo.get_analysis(conn, project_id) or {}
            gender = mannequin.select_base_gender(analysis)
            base_asset_id = (s.base_mannequin_men_asset_id if gender == "men"
                             else s.base_mannequin_women_asset_id)
            base_asset = (await repo.get_asset_for_user(conn, user_id, base_asset_id)
                          if base_asset_id else None)
            prod_assets = []
            for slot, aid in mannequin.base_color_images(product):
                a = await repo.get_asset_for_user(conn, user_id, aid)
                if a:
                    a["slot"] = slot  # Front/Back/Detail/Fit — 매니페스트 라벨용
                    prod_assets.append(a)
            match_asset = None
            match_id = mannequin.main_match_item_id(analysis)
            if match_id:
                m_aid = await repo.get_matching_item_asset(conn, match_id)
                if m_aid:
                    match_asset = await repo.get_asset_for_user(conn, user_id, m_aid)

        if base_asset is None:
            await _fail("마네킹 베이스가 설정되지 않았어요. 잠시 후 다시 시도해 주세요.",
                        {"error": "base_mannequin_missing", "gender": gender})
            return
        if not prod_assets:
            await _fail("상품 사진을 찾을 수 없어요. 정면 사진을 올렸는지 확인해 주세요.",
                        {"error": "no_product_images"})
            return

        # 2) 바이트 다운로드 (to_thread)
        base_img = InlineImage(base_asset["mime_type"], await asyncio.to_thread(app.state.r2.get_bytes, base_asset["r2_key"]))
        prod_imgs = [InlineImage(a["mime_type"], await asyncio.to_thread(app.state.r2.get_bytes, a["r2_key"])) for a in prod_assets]
        match_img = None
        if match_asset:
            match_img = InlineImage(match_asset["mime_type"], await asyncio.to_thread(app.state.r2.get_bytes, match_asset["r2_key"]))
        product_count = len(prod_imgs) + (1 if match_img else 0)
        template = load_prompt_template(s)
        await _emit(pool, job_id, "progress", {"progress": 15, "phase": "inputs_loaded",
                                               "withBottom": match_img is not None})

        # 3) 단일 후보 생성(2026-07-13 사용자 결정: 한 번에 1컷) — 확정 fit profile 기준.
        #    구 A/B 이원(정핏/슬림 동시 2컷)은 폐기: 셀러가 고른 핏과 무관한 슬림 변형이
        #    함께 떠서 혼란(버전 스트립에 2개) + 재생성마다 2컷씩 쌓이던 문제.
        #    크레딧 단가(2/잡)는 잡 기준이라 불변. 다양화는 핏 조정→재생성 루프가 담당.
        clothing_type = product.get("clothing_type") or "상의"
        manifest = _build_manifest(prod_assets, match_img is not None)
        # Phase 3(retrieval_refimages=on): 유사 성공 컷을 STYLE REFERENCE 로 첨부(컷 톤·조명 일관성).
        # off 면 ([], []) → 매니페스트·images 무변화(행위 변화 0). best-effort.
        ref_imgs, ref_ids = await _load_style_refs(
            app, s, prod_imgs=prod_imgs,
            clothing_type=(product.get("clothing_type") or product.get("clothingType")), gender=gender)
        if ref_imgs:
            next_i = 2 + len(prod_assets) + (1 if match_img else 0)
            manifest = manifest + "\n" + _ref_manifest_lines(next_i, len(ref_imgs))
            # 이벤트는 잡 소유자(다른 셀러)에게 전달되므로 ref id(타 프로젝트 UUID 포함)를 그대로
            # 노출하지 않는다 — opaque 해시만. 실제 id 는 서버 로그로만(운영자 디버깅용).
            log.info("job %s style_refs_attached ids=%s", job_id, ref_ids)
            opaque = [hashlib.sha1(i.encode("utf-8")).hexdigest()[:12] for i in ref_ids]
            await _emit(pool, job_id, "step",
                        {"status": "style_refs_attached", "ref_hashes": opaque, "n": len(ref_imgs)})
        # fit profile 은 잡 생성 시점 스냅샷이 정본(payload.fitProfileSnapshot — fidelity 설계 D3).
        # 워커가 최신 analysis 를 재독하면 잡 생성↔실행 사이의 저장 경합으로 다른 프로필이
        # 조용히 쓰일 수 있다(무음 유실). 키가 없는 legacy 잡만 analysis 폴백.
        snap = (job.get("payload") or {}).get("fitProfileSnapshot")
        if snap is not None:
            valid = (isinstance(snap, dict) and snap.get("version") == 1
                     and (snap.get("profile") is None or isinstance(snap.get("profile"), dict))
                     and isinstance(snap.get("adjustedAxes"), list))
            if not valid:
                await _fail("마네킹컷 생성에 실패했어요. 다시 시도해 주세요.",
                            {"error": "invalid_fit_profile_snapshot"})
                return
            fit_profile = snap.get("profile")
            adjusted_axes = tuple(a for a in snap.get("adjustedAxes") if isinstance(a, str))
            fit_profile_source = "payload_snapshot"
        else:
            fit_profile = mannequin.effective_fit_profile(analysis, match_img is not None)
            adjusted_axes = ()
            fit_profile_source = "legacy_analysis_fallback"
        # 방어: 스냅샷 이후 매칭 자산이 사라졌거나 legacy analysis 에 v2 프로필이 남아 있어도
        # 화면에 없는 별도 의류의 지시가 프롬프트로 전달되지 않게 두 버전 키를 함께 제거한다.
        fit_profile = _fit_profile_for_match_image(fit_profile, match_img is not None)
        legacy_base_fit = analysis.get("fit") or "regular"
        await _emit(pool, job_id, "progress", {"progress": 35, "phase": "generating"})

        # gemini 생성은 이 job 에서 가장 긴 구간(20~60s) — 완료 시 중간 progress(35→60)를 쏘고,
        # 호출이 길어지면 ticker 가 84까지 천천히 올려 폴링 UI 가 "멈춤/실패"처럼 보이지 않게 한다.
        _done = 0
        _reported_generation_progress = 35
        _progress_lock = asyncio.Lock()
        _generation_done = asyncio.Event()

        async def _emit_generation_progress(next_progress: int, *, estimated: bool = False):
            nonlocal _reported_generation_progress
            next_progress = min(85, max(35, int(next_progress)))
            async with _progress_lock:
                if next_progress <= _reported_generation_progress:
                    return
                _reported_generation_progress = next_progress
                payload = {"progress": next_progress, "phase": "generating"}
                if estimated:
                    payload["estimated"] = True
                await _emit(pool, job_id, "progress", payload)

        async def _tick_generation_progress():
            while not _generation_done.is_set():
                try:
                    await asyncio.wait_for(
                        _generation_done.wait(), timeout=_GENERATION_PROGRESS_INTERVAL_SECONDS)
                    return
                except asyncio.TimeoutError:
                    await _emit_generation_progress(
                        min(_GENERATION_PROGRESS_MAX, _reported_generation_progress + 1),
                        estimated=True)

        async def _cand(letter, base_fit, profile):
            nonlocal _done
            try:
                r = await _run_candidate(
                    app=app, job=job, candidate=letter, base_fit=base_fit, base_gender=gender,
                    base_img=base_img, prod_imgs=prod_imgs, match_img=match_img,
                    product_count=product_count, template=template, product=product,
                    analysis=analysis, clothing_type=clothing_type, image_manifest=manifest,
                    fit_profile=profile, adjusted_axes=adjusted_axes,
                    fit_profile_source=fit_profile_source, ref_imgs=ref_imgs)
            except Exception as e:
                log.warning("job %s candidate %s failed: %r", job_id, letter, e)
                r = None
            async with _progress_lock:
                _done += 1
                # 후보 완료 시 35→60 (85 는 아래 finalizing 이 덮음).
                next_progress = min(85, 35 + _done * 25)
            await _emit_generation_progress(next_progress)
            return r

        progress_task = asyncio.create_task(_tick_generation_progress())
        try:
            results = [await _cand("A", legacy_base_fit, fit_profile)]
        finally:
            _generation_done.set()
            progress_task.cancel()
            with suppress(asyncio.CancelledError):
                await progress_task
        passed = [r for r in results if isinstance(r, dict)]

        if not passed:
            await _fail("마네킹컷 생성에 실패했어요. 다시 시도해 주세요.", {"error": "all_candidates_failed"})
            return
        await _emit(pool, job_id, "progress", {"progress": 85, "phase": "finalizing"})

        # 4) 성공 종결 (원자·lease 펜스). charge = reserved — 예약 시점 견적을 그대로 확정한다
        # (단일컷 전환으로 구 "성공 후보 수 × 1" 폐기. 실행 시점 설정값을 다시 읽으면 배포/env 변경
        # 사이에 낀 잡이 예약액과 다른 금액을 차감하거나 settle 실패할 수 있음). 실패는 _fail(release).
        charge = reserved
        async with pool.connection() as conn:
            out = await repo.finalize_mannequin_success(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                project_id=project_id, candidates=passed, reserved=reserved, charge=charge,
                metadata={"creditCostVersion": s.credit_cost_version,
                          "promptVersion": s.mannequin_prompt_version, "gender": gender})
            await conn.commit()
        if out is None:  # lease 상실(복구) → 결과 폐기 + 방금 저장한 R2 객체 best-effort 정리
            for c in passed:
                try:
                    await asyncio.to_thread(app.state.r2.delete, c["key"])
                except Exception:
                    log.warning("orphan R2 cleanup failed: %s", c["key"])
    except Exception as e:  # 예기치 못한 오류도 lease 펜스 종결로
        await _fail("생성 중 오류가 발생했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
