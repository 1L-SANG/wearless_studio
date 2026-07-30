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
from ..agents import image_qc, mannequin, mannequin_bust, mannequin_fit_qc, mannequin_series_qc
from ..agents.gemini_image import GeminiError, InlineImage
from ..agents.model_routing import resolve_model
from ..agents.prompts import (
    load_bust_prompt_template,
    load_prompt_template,
    render_mannequin_prompt,
)
from ..r2 import IMMUTABLE_CACHE, ai_key, ext_for_mime
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


def _worst_score(p2, keys=image_qc.SCORE_KEYS) -> int | None:
    """지정 축의 최저 점수. 점수 신호가 하나도 없으면 None."""
    if not isinstance(p2, dict):
        return None
    scores = [v for k in keys
              if isinstance(v := p2.get(k), int) and not isinstance(v, bool)]
    return min(scores) if scores else None


# 후보끼리 비교할 때 쓰는 축. D축(series_consistency)은 **제외**한다 — 사전 게이트 후보는
# 아직 D축 판정을 안 받았고 최종 후보는 받았으므로, 포함하면 축 개수가 달라 비교가
# 불공정해진다(70점 검증본이 D축 10 때문에 20점 후보에게 진다).
_COMPARABLE_KEYS = tuple(k for k in image_qc.SCORE_KEYS if k != "series_consistency")


def _is_better_candidate(s, new_p2, old_p2) -> bool:
    """reject 후보끼리의 우열 — 구제할 '최선본'을 고르기 위한 순수 비교.

    치명 오류 없는 쪽이 무조건 낫다(점수가 낮아도 출고 가능한 결함이라). 그 다음 최저축.
    점수 신호가 없는 후보는 비교 불가라 기존 후보를 유지한다.
    """
    if old_p2 is None:
        return True
    new_critical = bool((new_p2 or {}).get("critical_errors"))
    old_critical = bool((old_p2 or {}).get("critical_errors"))
    if new_critical != old_critical:
        return not new_critical
    # D축 제외 — 사전/최종 후보는 D축 보유 여부가 달라 포함하면 비교가 불공정해진다.
    new_worst, old_worst = (_worst_score(new_p2, _COMPARABLE_KEYS),
                            _worst_score(old_p2, _COMPARABLE_KEYS))
    if new_worst is None:
        return False
    if old_worst is None:
        return True
    return new_worst > old_worst


def score_outcome(s, p2) -> str:
    """4축 점수 → auto_pass | needs_review | regenerate (순수).

    이진 verdict 로는 "얼마나 나쁜지"를 몰라 셀러에게 보일지/자동으로 다시 만들지를 못 가른다.
    점수 신호가 아예 없으면(off·shadow·판정실패·미채점 모델) **auto_pass** 로 눕힌다 —
    신호 부재를 나쁨으로 읽으면 QC 를 켜는 순간 멀쩡한 컷이 재생성된다.

    치명 오류(로고 변형·색 변경·구조 붕괴)는 점수와 무관하게 regenerate. 점수는 평균으로
    희석되지만 이런 결함은 하나만 있어도 출고 불가라 별도 축으로 둔다.
    """
    if not isinstance(p2, dict):
        return "auto_pass"
    if p2.get("critical_errors"):
        return "regenerate"
    worst = _worst_score(p2)  # 평균이 아니라 최저 — 한 축 붕괴가 고득점에 가려지면 안 된다
    if worst is None:
        return "auto_pass"
    if worst >= s.qc_score_auto_pass:
        return "auto_pass"
    if worst >= s.qc_score_review:
        return "needs_review"
    return "regenerate"


async def _apply_series_qc(*, app, pool, s, job_id, project_id, candidate, attempt, res):
    """D축 시리즈 일관성 — 채택본이 같은 프로젝트 기존 컷들과 한 세트로 보이는지 판정.

    **fail-open** — _apply_axis_qc·_apply_bust_pass 와 같은 규율. 판정은 관측이지 게이트가
    아니다. 기존 컷 0장(첫 생성)·모델 오류·R2 미스 어떤 경우에도 None 을 돌려 생성을 통과시킨다.

    호출 위치가 중요하다: bust 2패스 **뒤**여야 측정 대상이 실제 출고본과 같다. 그리고 게이트
    통과 뒤에만 불리므로 reject 된 attempt 에서 기존 컷을 헛되이 로드하지 않는다.

    소유권: `list_series_reference_cuts` 는 user 스코프를 걸지 않는다. 여기 들어오는
    project_id 는 워커가 클레임한 잡의 것이고, 잡은 생성 시점에 소유자 검증을 통과했다.
    비교 대상도 **같은 프로젝트의 과거 버전**이라 크로스테넌트 노출 경로가 없다.
    """
    try:
        async with pool.connection() as conn:
            # SQL 단에서 candidate 별 최신 1장·limit 로 좁힌다 — 전 버전을 끌어와 파이썬에서
            # 자르면 재생성 이력에 비례해 DB 전송·정렬 비용이 계속 늘어난다.
            refs = await repo.list_series_reference_cuts(
                conn, project_id, limit=mannequin_series_qc.MAX_REFERENCE_CUTS)
        if not refs:
            return None  # 첫 컷 — 비교 대상 없음(0점이 아니라 판정 없음)
        ref_imgs = []
        for c in refs:
            data = await asyncio.to_thread(app.state.r2.get_bytes, c["r2_key"])
            ref_imgs.append(InlineImage(_REF_MIME.get(
                c["r2_key"].rsplit(".", 1)[-1].lower(), "image/jpeg"), data))
        out = await mannequin_series_qc.judge(
            s, InlineImage(res.mime, res.image), ref_imgs)
    except Exception as e:
        log.warning("series_qc failed for job %s: %r", job_id, e)
        await _emit(pool, job_id, "step", {
            "candidate": candidate, "attempt": attempt, "status": "series_qc_failed",
            "error": type(e).__name__, "message": str(e)[:200]})
        return None
    if out is not None:
        await _emit(pool, job_id, "step", {
            "candidate": candidate, "attempt": attempt, "status": "series_qc",
            "seriesQc": out, "referenceCount": len(refs)})
    return out


def merge_qc_scores(p2, series, *, salvaged: bool = False, thresholds: tuple | None = None) -> dict | None:
    """A~C(image_qc) + D(series) 를 한 스냅샷으로 합치고 최종 outcome 을 계산한다 (순수).

    판정이 여러 곳에 흩어지면 "API 엔 재생성 필요라 적혀 있는데 성공 컷으로 출고되는" 모순이
    생긴다(codex 2026-07-31). 4축 합류·outcome·salvaged 를 여기 한 곳에서만 만든다.
    """
    if not isinstance(p2, dict) and series is None:
        return None
    p2d = p2 if isinstance(p2, dict) else {}
    out = {k: p2d.get(k) for k in image_qc.SCORE_KEYS}
    if series is not None:
        out["series_consistency"] = series["consistency"]
        out["series_inconsistencies"] = series["inconsistencies"]
    out["critical_errors"] = p2d.get("critical_errors") or []
    out["salvaged"] = salvaged
    # 판정에 쓰인 임계를 함께 남긴다. 임계를 바꾸면 과거 판정은 재계산되지 않으므로, 이게
    # 없으면 나중에 저장된 outcome 을 재계산해봤을 때 불일치가 나와 버그로 오해하게 된다
    # (2026-07-31 실측: 임계를 90/75 → 80/65 로 바꾼 뒤 과거 11건이 불일치로 보였다).
    if thresholds:
        out["thresholds"] = {"auto_pass": thresholds[0], "review": thresholds[1]}
    return out


# 축별 재생성 지시 — 점수만 낮고 텍스트 사유가 없을 때의 폴백. 같은 프롬프트로 다시 만들면
# 같은 결과가 나오므로, 최소한 "무엇이 부족했는지"는 전달해야 재시도가 의미를 갖는다.
_AXIS_FEEDBACK = {
    "product_fidelity": "reproduce the garment exactly as in the product photos — color, "
                        "pattern, print, logo, neckline, sleeve and hem length",
    "physical_naturalness": "make the garment sit on the body like real cloth — correct drape, "
                            "no fabric passing through the body, no impossible asymmetry",
    "image_quality": "deliver a clean e-commerce photo — sharp, correctly exposed, nothing "
                     "important cropped, no generation artifacts",
    "series_consistency": "match the studio setup of this shop's existing cuts",
}


def _build_retry_feedback(scores: dict | None, series: dict | None, p2) -> str:
    """거절 사유를 다음 attempt 프롬프트용 지시로 조립 (순수).

    텍스트 사유(critical_errors·불일치·correctionPrompt)가 하나도 없어도 **빈 문자열을
    돌려주지 않는다** — 점수만 낮고 사유가 비는 경우가 실제로 있고(verdict=pass 인데 축이
    낮은 케이스), 그때 빈 피드백이면 다음 attempt 가 같은 프롬프트로 돌아 같은 결과를 낸다.
    """
    parts = []
    if (scores or {}).get("critical_errors"):
        parts.append("CRITICAL: " + "; ".join(scores["critical_errors"][:3]))
    if series and series.get("inconsistencies"):
        parts.append("CONSISTENCY: " + _AXIS_FEEDBACK["series_consistency"] + " — "
                     + "; ".join(series["inconsistencies"][:3]))
    if isinstance(p2, dict) and p2.get("correctionPrompt"):
        parts.append("CORRECTION (generate the SAME garment as the product photos): "
                     + p2["correctionPrompt"])
    if not parts and scores:
        # 폴백: 가장 낮은 축을 집어 그 축의 지시를 준다.
        scored = [(v, k) for k in image_qc.SCORE_KEYS
                  if isinstance(v := scores.get(k), int) and not isinstance(v, bool)]
        if scored:
            _worst, axis = min(scored)
            parts.append(f"IMPROVE ({axis}): {_AXIS_FEEDBACK[axis]}")
    return "\n\n".join(parts)


def final_decision(s, scores: dict | None) -> str:
    """출고 직전 단일 판정 → ship | retry (순수).

    `score_outcome` 이 등급(auto_pass/needs_review/regenerate)이라면 이건 **행동**이다.
    게이팅은 enforce 에서만 — off/shadow 는 관측이므로 무엇이 나와도 출고한다.
    """
    if s.image_qc != "enforce" or not scores:
        return "ship"
    return "retry" if score_outcome(s, scores) == "regenerate" else "ship"


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
    if s.image_qc != "enforce":
        return pillow_reject, False  # off/shadow 는 항상 통과 — 기존 동작 불변
    # 점수 신호가 있으면 그쪽이 정본(3분기). 없으면 기존 이진 verdict 로 폴백한다 —
    # 미채점 응답에서 게이트가 통째로 풀리지 않게.
    if isinstance(p2, dict) and (p2.get("critical_errors") or any(
            isinstance(p2.get(k), int) and not isinstance(p2.get(k), bool)
            for k in image_qc.SCORE_KEYS)):
        return pillow_reject, score_outcome(s, p2) == "regenerate"
    return pillow_reject, isinstance(p2, dict) and p2.get("verdict") == "retry"


async def _apply_bust_pass(*, pool, gemini, s, job_id, candidate, attempt, base_gender, res):
    """여성 기본 가슴 볼륨 2패스 — 채택본에 "가슴만 바꿔라"를 단독 과제로 한 번 더 돌린다.

    1패스만으로는 안 된다(2026-07-30 스파이크): 베이스를 볼륨 있는 것으로 바꿔도, 1패스
    프롬프트에 가슴 지시를 주입해도 모델이 몸을 표준으로 정규화한다. 이미지 1장·과제 1개일
    때만 반영된다.

    **fail-open** — _apply_axis_qc 와 동일 규율. 거부·오류·빈 응답 어떤 경우에도 1패스
    결과를 그대로 돌려준다. 실제로 Flash 는 "I cannot modify the physical characteristics
    of the mannequin's chest" 로 거부하는 것이 관측됐다. 콘텐츠 필터 한 번에 셀러 잡이
    죽으면 안 된다.
    """
    if not mannequin_bust.should_apply(base_gender, getattr(s, "mannequin_bust_pass", "off")):
        return res
    before = hashlib.sha256(res.image).hexdigest()[:12]
    try:
        prompt = mannequin_bust.build_prompt(load_bust_prompt_template())
        out = await gemini.generate_content_image(
            resolve_model(s, "image_high"),  # Flash 는 거부·미반영으로 탈락 — 티어 고정
            prompt, [InlineImage(res.mime, res.image)],
            s.mannequin_image_size, aspect_ratio=s.mannequin_aspect_ratio)
    except Exception as e:
        log.warning("bust pass failed for job %s (원본 유지): %r", job_id, e)
        await _emit(pool, job_id, "step", {
            "candidate": candidate, "attempt": attempt, "status": "bust_pass",
            "outcome": "failed_open", "image_hash": before,
            "error_type": type(e).__name__, "error_message": str(e)[:200]})
        return res
    await _emit(pool, job_id, "step", {
        "candidate": candidate, "attempt": attempt, "status": "bust_pass",
        "outcome": "applied", "image_hash": before,
        "result_hash": hashlib.sha256(out.image).hexdigest()[:12]})
    return out


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
    # 구제 후보 풀을 **두 단계로 분리**한다(codex 2026-07-31 HIGH).
    #  - pre_reject: 사전 게이트에서 걸린 후보. axis/bust 편집·재판정·D축을 아직 안 거쳤다.
    #  - final_reject: 최종 판정에서 걸린 후보. 편집까지 끝난 출고 가능 상태다.
    # 섞으면 최종 소진 시 "편집도 D축도 안 거친 원본"이 출고될 수 있다. 최종 구제는
    # final_reject 만 쓰고, pre_reject 는 사전 게이트 안에서만 되돌린다.
    # 튜플: (res, merged_scores, series, p2). 두 번째는 **항상 merge_qc_scores 결과** —
    # 저장 shape 이 계약(QcScores)을 벗어나지 않게. 네 번째는 이벤트·correctionPrompt 용.
    pre_reject: tuple | None = None
    final_reject: tuple | None = None
    edits_spent = 0  # axis 편집이 소비한 이미지 모델 호출 누적 — 예산은 생성+편집 총합이다
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
                p2 = await image_qc.verdict(
                    s, prod_imgs, InlineImage(res.mime, res.image), scored=True)
                await _emit(pool, job_id, "step", {
                    "candidate": candidate, "attempt": attempt, "status": "image_qc", "imageQc": p2})
            except Exception as e:
                log.warning("AG-P2 image_qc failed for job %s: %r", job_id, e)
                # 실패도 이벤트로 남긴다 — 로그만 남기면 shadow 관측에서 "판정 실패율" 자체가
                # 안 잡혀 pass/retry 분포가 생존 편향된다(캘리브레이션 근거 오염).
                await _emit(pool, job_id, "step", {
                    "candidate": candidate, "attempt": attempt, "status": "image_qc_failed",
                    "error": type(e).__name__, "message": str(e)[:200]})
        # **사전 게이트** — 잘못된 옷을 axis/bust 편집하면 그 정체성이 보존되므로, 편집 전에
        # 한 번 거른다. 최종 출고 판정은 여기가 아니라 아래 final_decision 하나가 내린다.
        pillow_reject, p2_reject = gate_decision(s, verdict.verdict, p2)
        salvaged = False
        if p2_reject:
            # reject 후보를 점수와 함께 보관 — 예산 소진 시 "마지막 시도"가 아니라 **최선본**을
            # 구제하기 위해서다. 1차 70점 / 2차 20점인데 20점을 내보내면 재시도가 손해가 된다.
            # 두 번째 요소는 **항상 merge 된 shape** 으로 통일한다. 경로마다 p2(verdict·
            # mismatches 포함)와 qc_scores 가 섞이면, 구제 시 API 계약에 없는 키가 저장된다.
            pre_scores = merge_qc_scores(p2, None)
            if _is_better_candidate(s, pre_scores, pre_reject[1] if pre_reject else None):
                pre_reject = (res, pre_scores, None, p2)
            if attempt >= s.mannequin_max_attempts:
                # 구제 대상은 **두 풀을 통틀어 최선**이어야 한다. 이전 attempt 에서 편집·D축까지
                # 통과했다가 최종 게이트에서 걸린 후보(final_reject)가 더 좋으면 그걸 쓴다 —
                # 사전 게이트 후보만 보면 60점 검증본을 두고 20점을 내보낸다(codex 2026-07-31).
                if final_reject and _is_better_candidate(s, final_reject[1], pre_reject[1]):
                    res, qc_override, _series, p2 = final_reject
                    salvaged_scores = qc_override
                else:
                    res, salvaged_scores, _series, p2 = pre_reject
                    # 사전 게이트 후보는 편집·D축을 안 거쳤다 → 아래 본 경로가 그걸 수행한다.
                p2_reject, salvaged = False, True
                await _emit(pool, job_id, "step", {
                    "candidate": candidate, "attempt": attempt, "status": "qc_salvaged",
                    "reason": "budget_exhausted", "outcome": score_outcome(s, salvaged_scores)})
        if not pillow_reject and not p2_reject:
            pre_edit_hash = hashlib.sha256(res.image).hexdigest()  # 편집 여부 판정용
            # P1 축 QC: 채택본이 선언 핏 축을 반영했는지 판정, enforce면 편집 교정 1회
            # (실패 이미지 편집 — §H 실증). fail-open: 어떤 실패도 채택 자체를 막지 않는다.
            res, axis_spent = await _apply_axis_qc(
                pool=pool, gemini=gemini, s=s, job_id=job_id, candidate=candidate,
                attempt=attempt, model=model, res=res, prod_imgs=prod_imgs,
                match_img=match_img, fit_profile=fit_profile, profile_hash=profile_hash)
            # 여성 기본 가슴 볼륨 2패스 — R2 저장 직전, 채택본이 확정된 뒤. fail-open.
            res = await _apply_bust_pass(
                pool=pool, gemini=gemini, s=s, job_id=job_id, candidate=candidate,
                attempt=attempt, base_gender=base_gender, res=res)
            # A~C 점수는 **편집 전** 원본에 매긴 것이다. axis QC 편집·bust 2패스가 이미지를
            # 바꿨다면 저장되는 점수가 실제 출고본의 점수가 아니게 된다(검수자가 다른 이미지의
            # 숫자를 보고 판단하게 됨). 이미지가 실제로 바뀐 경우에만 재판정한다 — 안 바뀌었으면
            # 같은 입력에 vision 콜을 한 번 더 쓰는 낭비다.
            if (isinstance(p2, dict) and prod_imgs
                    and hashlib.sha256(res.image).hexdigest() != pre_edit_hash):
                try:
                    p2 = await image_qc.verdict(
                        s, prod_imgs, InlineImage(res.mime, res.image), scored=True)
                    await _emit(pool, job_id, "step", {
                        "candidate": candidate, "attempt": attempt,
                        "status": "image_qc_rescored", "imageQc": p2})
                except Exception as e:
                    # fail-open: 재판정 실패 시 편집 전 점수를 쓰되, 그 사실을 남긴다.
                    log.warning("image_qc rescore failed for job %s: %r", job_id, e)
                    await _emit(pool, job_id, "step", {
                        "candidate": candidate, "attempt": attempt,
                        "status": "image_qc_rescore_failed", "error": type(e).__name__})
            # D축 시리즈 일관성 — bust 2패스 뒤(측정본=출고본), R2 저장 직전. fail-open.
            series = await _apply_series_qc(
                app=app, pool=pool, s=s, job_id=job_id,
                project_id=project_id, candidate=candidate, attempt=attempt, res=res)
            # ── 최종 판정 (단일 지점) ────────────────────────────────────────
            # A~C·D 를 한 스냅샷으로 합쳐 여기서 한 번만 결정한다. 판정이 흩어지면 "API 엔
            # 재생성 필요라 적혀 있는데 성공 컷으로 나가는" 모순이 생긴다(codex 2026-07-31).
            qc_scores = merge_qc_scores(
                p2, series, salvaged=salvaged,
                thresholds=(s.qc_score_auto_pass, s.qc_score_review))
            # 예산은 **누적 이미지 모델 호출**이다: 생성 attempt 회 + 편집 edits_spent 회.
            # 재생성하면 다음 attempt 가 생성 1회를 쓰고, axis QC 가 켜져 있으면 편집 1회도
            # 쓸 수 있다. 둘 다 미리 세지 않으면 상한을 넘긴다 — 편집은 재생성 판단보다
            # **먼저** 일어나므로 사후에는 막을 수 없다(codex 2026-07-31: 3 예산에 4회 관측).
            if axis_spent:
                edits_spent += 1
            next_cost = 2 if _effective_axis_qc_mode(s) == "enforce" else 1
            budget_left = attempt + edits_spent + next_cost <= s.mannequin_max_attempts
            # **R2 저장 전에** 분기한다: 저장 후 continue 하면 재생성마다 고아 객체가 쌓인다.
            if final_decision(s, qc_scores) == "retry" and budget_left and not salvaged:
                await _emit(pool, job_id, "step", {
                    "candidate": candidate, "attempt": attempt, "status": "final_qc_reject",
                    "outcome": score_outcome(s, qc_scores),
                    "seriesConsistency": (series or {}).get("consistency")})
                # 편집 완료 이미지 + A~D 전체 스냅샷 — 최종 단계 후보 풀에만 담는다.
                if _is_better_candidate(s, qc_scores, final_reject[1] if final_reject else None):
                    final_reject = (res, qc_scores, series, p2)
                feedback = _build_retry_feedback(qc_scores, series, p2)
                continue
            # 예산 소진인데 최종 판정이 retry 라면 최선본으로 되돌려 구제 출고한다.
            # **final_reject 만** 쓴다 — pre_reject 는 편집·재판정·D축을 안 거친 원본이라
            # 그대로 저장하면 검증 안 된 이미지가 출고된다(codex HIGH).
            if final_decision(s, qc_scores) == "retry" and not salvaged:
                if final_reject and _is_better_candidate(s, final_reject[1], qc_scores):
                    res, qc_scores, _series, _p2 = final_reject
                qc_scores = {**(qc_scores or {}), "salvaged": True}
                await _emit(pool, job_id, "step", {
                    "candidate": candidate, "attempt": attempt, "status": "qc_salvaged",
                    "reason": "budget_exhausted", "outcome": score_outcome(s, qc_scores)})
            if qc_scores is not None:
                qc_scores["outcome"] = score_outcome(s, qc_scores)
            ext = ext_for_mime(res.mime) or _EXT_FALLBACK.get(res.mime, "png")
            asset_id = str(uuid.uuid4())
            key = ai_key(user_id, project_id, job_id, asset_id, ext)
            await asyncio.to_thread(r2.put_bytes, key, res.image, res.mime, cache=IMMUTABLE_CACHE)
            w, h = _image_dims(res.image)
            return {
                "asset_id": asset_id, "bucket": s.r2_bucket, "key": key, "mime": res.mime,
                "size": len(res.image), "width": w, "height": h,
                "candidate": candidate, "base_fit": base_fit,
                "qc_scores": qc_scores,
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
        # 사전 게이트도 최종 게이트와 **같은 피드백 조립기**를 쓴다. 여기만 빠뜨리면
        # 점수만 낮고 텍스트 사유가 없는 경우 재시도가 같은 프롬프트로 돌아 같은 결과를 낸다
        # (codex 2026-07-31 — 최종 게이트만 고쳤던 것을 여기로도 확장).
        parts = []
        if pillow_reject:
            parts.append(qc.format_qc_feedback(verdict))
        if p2_reject:
            parts.append(_build_retry_feedback(merge_qc_scores(p2, None), None, p2))
        feedback = "\n\n".join(p for p in parts if p)
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
            product_clothing_type = (
                product.get("clothing_type")
                or product.get("clothingType")
                or "top"
            )
            gender = mannequin.select_base_gender(
                analysis, product_clothing_type
            )
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
