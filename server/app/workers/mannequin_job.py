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

import cv2
import numpy as np
from PIL import Image

log = logging.getLogger("wearless.mannequin_job")

from .. import repo
from ..agents import (
    image_qc,
    mannequin,
    mannequin_bust,
    mannequin_fit_qc,
    mannequin_series_qc,
    mannequin_untuck,
)
from ..agents.mannequin_adjust import (
    ADJUST_PROMPT_VERSION,
    build_adjust_directives,
    build_adjust_manifest,
    render_adjust_prompt,
)
from ..agents.gemini_image import GeminiError, GeminiImageResult, InlineImage
from ..agents.model_routing import resolve_model
from ..agents import hybrid_landmarks
from ..agents.product_reference import ProductReference, order_by_role, select_pattern_sources
from ..services.hybrid_composite import (
    deterministic_qc as hc_qc,
    panel_map as hc_panel,
    source_validation as hc_source,
    stripe_model as hc_stripe,
    warp_composite as hc_warp,
)
from ..services.hybrid_composite.types import (
    PIPELINE_VERSION as HC_PIPELINE_VERSION,
    CompositeFailure,
)
from ..agents.prompts import (
    load_bust_prompt_template,
    load_prompt_template,
    load_untuck_prompt_template,
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


def _valid_fit_profile_snapshot(snapshot) -> bool:
    return (
        isinstance(snapshot, dict)
        and snapshot.get("version") == 1
        and (snapshot.get("profile") is None or isinstance(snapshot.get("profile"), dict))
        and isinstance(snapshot.get("adjustedAxes"), list)
    )


# 조정 요청이 편집(parent-first)이 아니라 fresh 생성으로 떨어지는 사유 — typed 상수.
# 이유를 남기지 않으면 셀러 입장에선 "조정했는데 패턴이 또 달라졌다"만 보이고, 우리도 그게
# 편집이 안 걸린 탓인지 편집이 실패한 탓인지 구분할 수 없다(계획 §9 silent fresh 리스크).
EDIT_FALLBACK_REASONS = frozenset({
    "invalid_fit_snapshot",       # payload 스냅샷이 없거나 계약 밖 형태
    "invalid_fit_profile",        # 스냅샷은 유효한데 프로필이 dict/축 계약을 못 채움
    "no_adjust_directives",       # 조정된 축이 없어 편집 지시문이 비었다
    "parent_lookup_failed",       # 부모 컷 조회 자체가 실패(DB)
    "no_parent_cut",              # 편집할 기존 컷이 없다(첫 생성)
    "legacy_parent",              # PR #72 이전 자산 — generation metadata 부재
    "incompatible_parent",        # 카테고리/성별/매칭 상품이 지금 요청과 다르다
    "edit_depth_cap",             # 편집의 편집 상한(2세대) 도달 — 화질 누적 열화 방지
    "parent_asset_load_failed",   # R2 에서 부모 컷 바이트를 못 읽었다
    "missing_edit_inputs",        # 편집 경로로 들어왔는데 부모 컷/지시문이 비었다(방어)
    "unclassified",               # 위 어디에도 안 걸림 — 분류 누락 자체를 관측하기 위한 값
})


def classify_parent_edit(
    parent: dict | None, profile: dict | None, match_item_id: str | None
) -> tuple[int | None, str | None]:
    """부모 컷이 편집 자격을 갖췄는지 → (호환 editDepth, 부적격 사유).

    자격이 있으면 `(depth, None)`, 없으면 `(None, reason)`. 사유는 `EDIT_FALLBACK_REASONS`
    의 typed 값이라 이벤트에서 집계할 수 있다 — 자유 문자열이면 오타 하나로 집계가 갈린다.
    """
    if not parent:
        return None, "no_parent_cut"
    if not isinstance(profile, dict):
        return None, "invalid_fit_profile"
    category, gender = profile.get("category"), profile.get("gender")
    if not isinstance(category, str) or not isinstance(gender, str):
        return None, "invalid_fit_profile"
    metadata = parent.get("generation_metadata")
    # 빈 dict 도 legacy 다 — PR #72 이후 워커는 항상 editDepth 를 함께 쓴다. "메타데이터가
    # 아예 없음"과 "지금 요청과 안 맞음"은 대응이 달라서(전자는 새 기준 컷 필요) 나눠 센다.
    if not isinstance(metadata, dict) or not metadata or "editDepth" not in metadata:
        return None, "legacy_parent"
    if (metadata.get("profileCategory") != category
            or metadata.get("profileGender") != gender):
        return None, "incompatible_parent"
    if metadata.get("matchItemId") != match_item_id:
        return None, "incompatible_parent"
    depth = metadata.get("editDepth")
    if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0:
        return None, "legacy_parent"
    if depth >= 2:
        return None, "edit_depth_cap"
    return depth, None


def _compatible_parent_edit_depth(
    parent: dict | None, profile: dict | None, match_item_id: str | None
) -> int | None:
    """부모 메타데이터가 현재 조정 입력과 호환되면 기존 editDepth, 아니면 None."""
    return classify_parent_edit(parent, profile, match_item_id)[0]


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


def _build_manifest(prod_assets: list[dict], has_match: bool, clothing_type: str | None = None) -> str:
    """images=[base, *prod(slot순), match]와 동일 순서의 역할 목록 (모델이 어느 이미지가 무엇인지 알게).
    내용은 전부 고정 라벨(_SLOT_LABEL 룩업) — 셀러 데이터를 직접 끼우지 않는다(프롬프트 인젝션 방지).
    의류 종류는 sanitize된 ${clothingType}·PRODUCT CONTEXT로 따로 전달되므로 여기엔 넣지 않는다.

    매칭 라벨은 주상품 종류에 따라 갈린다(2026-08-01 WS4). 예전엔 무조건 "matching BOTTOM" 이라
    하의 상품에서 첨부된 매칭 '상의' 이미지를 하의라고 잘못 알려줬다 — 모델이 상의를 길게 그려
    상품(바지) 허리를 가리는 원인 중 하나."""
    lines = ["1. Base mannequin — the canvas to dress (keep it identical)"]
    i = 2
    for a in prod_assets:
        lines.append(f"{i}. {_SLOT_LABEL.get(a.get('slot'), 'view of the garment')}")
        i += 1
    if has_match:
        if str(clothing_type or "").lower() == "bottom":
            lines.append(f"{i}. matching TOP garment — also dress the mannequin in this, worn short so the PRODUCT bottom stays fully visible")
        else:
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


def effective_image_size(s, product: dict | None, analysis: dict | None) -> str:
    """이 잡이 쓸 출력 해상도 (순수). 미세 패턴 상품만 승급한다.

    2K 실측(2026-08-01, 스트라이프 셔츠): 줄 주기 8.9px → 한 주기를 이루는 요소(색 선·흰 간격)당
    2px 남짓이라 두 색 줄이 한 색으로 뭉개졌다. 해상도가 곧 재현 한계인 축이라 프롬프트로는
    못 넘는다. 무지 상품은 재현할 고주파가 없어 승급하지 않는다 — 비용만 늘고 결과는 같다.
    """
    upgrade = getattr(s, "mannequin_pattern_image_size", "OFF")
    if upgrade in (None, "", "OFF"):
        return s.mannequin_image_size
    return upgrade if mannequin.has_fine_pattern(product, analysis) else s.mannequin_image_size


_PATTERN_SAFE_TIER = "image_high"


def _guard_pattern_tier(tier: str, has_fine_pattern: bool) -> str:
    """미세 패턴 상품이 낮은 tier 로 내려가는 것을 막는다 (순수).

    2K 실측(2026-08-01)에서 줄 주기가 8.9px 이라 한 주기를 이루는 요소당 2px 남짓이었다.
    그 정도 여유에서는 모델 등급 차이가 곧 두 색 줄이 한 색으로 뭉개지느냐를 가른다.
    조정 tier 는 실험용 스위치(`MANNEQUIN_ADJUST_TIER`)라 설정 하나로 전 조정이 내려가는데,
    무지 상품에서 비용을 아끼려던 설정이 패턴 상품의 결과까지 같이 깎으면 안 된다.
    """
    if has_fine_pattern and tier != _PATTERN_SAFE_TIER:
        return _PATTERN_SAFE_TIER
    return tier


def adjust_edit_tier(s, *, has_fine_pattern: bool = False) -> str:
    """조정 **편집**(parent-first) 1콜이 쓸 tier (순수). 미설정이면 image_high.

    편집은 원본 컷 위에 지시를 얹는 작업이라 Flash 가 지시를 거부·미반영한 기록이 있다
    (untuck·bust 2패스가 image_high 로 고정된 이유와 같다). 여기에 더해 패턴 가드가 걸린다.
    """
    configured = (getattr(s, "mannequin_adjust_tier", "") or "").strip() or _PATTERN_SAFE_TIER
    return _guard_pattern_tier(configured, has_fine_pattern)


def tier_for_job(s, job: dict | None, *, has_fine_pattern: bool = False) -> str:
    """이 잡의 1패스 생성이 쓸 이미지 tier (순수).

    조정(`:regenerate`)과 초기 생성(`:generate`)은 같은 워커·같은 프롬프트를 타므로
    `MANNEQUIN_TIER` 하나로는 둘을 다른 모델로 돌릴 수 없다. `mannequin_adjust_tier` 가
    설정된 경우에만 조정 잡을 그쪽으로 보낸다 — 빈 값이면 분기 자체가 없다(기존 동작).

    조정에서만 모델을 바꿔 보려는 요구가 실제 동기다(2026-07-31). 초기 생성 품질을 고정한 채
    비교해야 결과 차이를 모델 탓으로 돌릴 수 있다.

    편집 패스(untuck·bust)는 여기 해당 없다 — 그쪽은 코드에서 image_high 로 고정돼
    있고, Flash 가 편집 지시를 거부·미반영해 탈락한 기록이 있다.
    """
    adjust = (getattr(s, "mannequin_adjust_tier", "") or "").strip()
    if adjust and ((job or {}).get("payload") or {}).get("mode") == "regenerate":
        return _guard_pattern_tier(adjust, has_fine_pattern)
    return _guard_pattern_tier(s.mannequin_tier, has_fine_pattern)


async def _apply_axis_qc(
    *, pool, gemini, s, job_id, candidate, attempt, model, res,
    prod_imgs, match_img, fit_profile, profile_hash, calls_spent, image_size=None,
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
    if calls_spent >= s.mannequin_max_attempts:  # 공유 예산: 생성+편집 <= max_attempts
        await _emit_retry("budget_exhausted", failed=failed, edit_hash=edit_hash)
        return res, False
    edit_attempt = attempt + 1
    try:
        edited = await gemini.generate_content_image(
            model, instruction, [InlineImage(res.mime, res.image)],
            image_size or s.mannequin_image_size, aspect_ratio=s.mannequin_aspect_ratio)
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
    # 한쪽만 D축을 가졌으면 D 를 빼고 비교한다 — 사전 후보(D 없음)와 최종 후보(D 있음)를
    # 그대로 견주면 D 를 가진 쪽이 그것만으로 불리해진다. 둘 다 가졌으면 **포함한다**:
    # 빼버리면 A~C 95/D10 이 A~C 90/D60 을 이겨 일관성이 무너진 컷이 구제된다
    # (codex 2026-07-31 7차 MEDIUM).
    both_have_series = all(
        isinstance((p or {}).get("series_consistency"), int) for p in (new_p2, old_p2))
    keys = image_qc.SCORE_KEYS if both_have_series else _COMPARABLE_KEYS
    new_worst, old_worst = _worst_score(new_p2, keys), _worst_score(old_p2, keys)
    if new_worst is None:
        return False
    if old_worst is None:
        return True
    return new_worst > old_worst


_GRADE_RANK = {"regenerate": 0, "needs_review": 1, "auto_pass": 2}


def edit_regressed(s, pre_p2, post_p2) -> bool:
    """편집(축 교정·가슴 2패스)이 컷을 **더 나쁘게** 만들었는가 (순수).

    편집은 선언 핏 축·가슴 볼륨을 고치려고 도는데, 그건 A~C 가 재는 축이 아니다. 그래서
    편집이 A~C 를 깎아도 그 대가가 정당한지는 A~C 만 봐서는 알 수 없다 — 작은 하락은
    판정 노이즈로 보고 편집을 살린다.

    다만 편집이 **등급을 떨어뜨리면서 최저점이 마진(qc_edit_regression_margin)보다 크게
    깎이거나**, **없던 치명 오류를 만들면** 얘기가 다르다. 그건 교정의 대가가 아니라 손상이다.
    실측(2026-07-31 관측): 가슴 2패스가 치마 원단을 왜곡해 product_fidelity 85 → 30,
    "breast-like bulges".

    등급 하락만으로 되돌리지 않는 이유: 임계(80)가 판정기 최빈값이라 컷의 23% 가 정확히 80 에
    걸리고, 재현성은 같은 이미지에 ±30 이다. 등급만 보면 4점 노이즈에도 편집이 상시 롤백된다
    (prod 실측 80→76 롤백으로 가슴 볼륨이 한 번도 출고되지 않음).

    신호가 한쪽이라도 없으면 비교 불가 → 되돌리지 않는다(편집 유지가 기존 동작).
    """
    if not isinstance(pre_p2, dict) or not isinstance(post_p2, dict):
        return False
    # 신규 치명오류는 **점수 유무보다 먼저** 본다. 점수 없음 검사를 앞에 두면, 편집 전 판정에
    # 숫자가 없던 경우(미채점 모델·판정 부분실패) 편집이 로고를 망가뜨려도 그냥 나간다
    # (codex 2026-07-31 8차 HIGH). 치명오류는 점수와 무관하게 그 자체로 출고 불가다.
    if post_p2.get("critical_errors") and not pre_p2.get("critical_errors"):
        return True
    pre_worst = _worst_score(pre_p2, _COMPARABLE_KEYS)
    if pre_worst is None:
        return False  # 편집 전 점수가 없으면 등급 하락은 논할 수 없다
    pre_rank = _GRADE_RANK[score_outcome(s, pre_p2)]
    post_rank = _GRADE_RANK[score_outcome(s, post_p2)]
    if post_rank >= pre_rank:
        return False
    # 등급이 내려갔어도 **하락폭이 판정 노이즈 수준이면 편집을 살린다.** 등급만 보면 임계(80)에
    # 몰린 분포 탓에 2패스가 상시 롤백된다 — 2026-07-31 prod 실측: 80/83/85 → 76/78/77(최저
    # 4점 하락)에 auto_pass→needs_review 로 갈려 가슴 볼륨이 통째로 되돌려졌다. 판정기 재현성이
    # 같은 이미지에 ±30 인데 4점 차를 손상으로 읽으면 편집은 영원히 출고되지 않는다.
    # 진짜 손상(실측 85→30)은 마진을 훨씬 넘으므로 그대로 걸러진다.
    post_worst = _worst_score(post_p2, _COMPARABLE_KEYS)
    margin = getattr(s, "qc_edit_regression_margin", 0) or 0
    if post_worst is not None and (pre_worst - post_worst) <= margin:
        return False
    return True


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


def has_budget_for_retry(s, *, calls_spent: int) -> bool:
    """재생성 여유가 있는가 (순수).

    예산은 **실제로 나간 이미지 모델 호출 수**다. 생성·axis 편집·bust 2패스가 모두 같은
    한 통에서 나가고, 호출 직전에 하나씩 소비한다.

    앞선 버전은 "다음 생성 + 다음 편집"을 미리 예약하는 예측형이었다. 세 가지가 어긋났다
    (codex 2026-07-31 7차): 사전 게이트 경로가 이 검사를 안 거쳐 상한을 넘길 수 있었고,
    bust 호출은 아예 안 세어져 계약이 설정 전체에서 성립하지 않았고, 반대로 마지막 attempt
    에서는 쓰지도 않을 편집분을 예약해 안전한 재생성까지 막았다. 실소비만 세면 세 문제가
    같이 사라진다 — 예약은 틀릴 수 있지만 소비는 틀릴 수 없다.
    """
    return calls_spent < s.mannequin_max_attempts


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


async def _apply_bust_pass(
    *, pool, gemini, s, job_id, candidate, attempt, base_gender, res, calls_spent,
    clothing_type=None, image_size=None,
):
    """여성 기본 가슴 볼륨 2패스. → (선택 결과, 편집콜 소비 여부).

    1패스만으로는 안 된다(2026-07-30 스파이크): 베이스를 볼륨 있는 것으로 바꿔도, 1패스
    프롬프트에 가슴 지시를 주입해도 모델이 몸을 표준으로 정규화한다. 이미지 1장·과제 1개일
    때만 반영된다.

    **fail-open** — _apply_axis_qc 와 동일 규율. 거부·오류·빈 응답 어떤 경우에도 1패스
    결과를 그대로 돌려준다. 실제로 Flash 는 "I cannot modify the physical characteristics
    of the mannequin's chest" 로 거부하는 것이 관측됐다. 콘텐츠 필터 한 번에 셀러 잡이
    죽으면 안 된다.
    """
    if not mannequin_bust.should_apply(
            base_gender, getattr(s, "mannequin_bust_pass", "off"), clothing_type):
        return res, False
    if calls_spent >= s.mannequin_max_attempts:
        # axis 편집과 **같은 통**에서 나간다. 여기만 무제한이면 "총 호출 <= max_attempts" 가
        # bust_pass=on 설정에서 성립하지 않는다(codex 2026-07-31 7차 HIGH).
        await _emit(pool, job_id, "step", {
            "candidate": candidate, "attempt": attempt, "status": "bust_pass",
            "outcome": "budget_exhausted",
            "image_hash": hashlib.sha256(res.image).hexdigest()[:12]})
        return res, False
    before = hashlib.sha256(res.image).hexdigest()[:12]
    try:
        prompt = mannequin_bust.build_prompt(load_bust_prompt_template())
        out = await gemini.generate_content_image(
            resolve_model(s, "image_high"),  # Flash 는 거부·미반영으로 탈락 — 티어 고정
            prompt, [InlineImage(res.mime, res.image)],
            image_size or s.mannequin_image_size, aspect_ratio=s.mannequin_aspect_ratio)
    except Exception as e:
        log.warning("bust pass failed for job %s (원본 유지): %r", job_id, e)
        await _emit(pool, job_id, "step", {
            "candidate": candidate, "attempt": attempt, "status": "bust_pass",
            "outcome": "failed_open", "image_hash": before,
            "error_type": type(e).__name__, "error_message": str(e)[:200]})
        return res, True  # 실패해도 호출은 나갔다 — 예산은 소비됐다
    await _emit(pool, job_id, "step", {
        "candidate": candidate, "attempt": attempt, "status": "bust_pass",
        "outcome": "applied", "image_hash": before,
        "result_hash": hashlib.sha256(out.image).hexdigest()[:12]})
    return out, True


async def _apply_untuck_pass(
    *, pool, gemini, s, job_id, candidate, attempt, res, match_img, calls_spent,
    clothing_type=None, image_size=None,
):
    """상의 밑단을 하의 밖으로 빼는 untuck 2패스. → (선택 결과, 편집콜 소비 여부).

    **편집 체인의 맨 앞**에서 돈다 — 구도(밑단 위치)가 먼저 확정돼야 축 QC 가 실제 밑단을
    보고 판정하고, 볼륨·원단 편집이 최종 구도 위에서 이뤄진다. QC 검출을 게이트로 쓰지
    않는 이유는 mannequin_untuck 모듈 주석 참조(검출 불안정 실측).

    fail-open — 거부·오류·빈 응답 어떤 경우에도 이전 결과를 그대로 돌려준다.
    """
    if not mannequin_untuck.should_apply(
            getattr(s, "mannequin_untuck_pass", "off"), clothing_type, match_img is not None):
        return res, False
    if calls_spent >= s.mannequin_max_attempts:
        await _emit(pool, job_id, "step", {
            "candidate": candidate, "attempt": attempt, "status": "untuck_pass",
            "outcome": "budget_exhausted",
            "image_hash": hashlib.sha256(res.image).hexdigest()[:12]})
        return res, False
    before = hashlib.sha256(res.image).hexdigest()[:12]
    try:
        prompt = mannequin_untuck.build_prompt(load_untuck_prompt_template())
        out = await gemini.generate_content_image(
            resolve_model(s, "image_high"),
            prompt, [InlineImage(res.mime, res.image)],
            image_size or s.mannequin_image_size, aspect_ratio=s.mannequin_aspect_ratio)
    except Exception as e:
        log.warning("untuck pass failed for job %s (원본 유지): %r", job_id, e)
        await _emit(pool, job_id, "step", {
            "candidate": candidate, "attempt": attempt, "status": "untuck_pass",
            "outcome": "failed_open", "image_hash": before,
            "error_type": type(e).__name__, "error_message": str(e)[:200]})
        return res, True  # 호출은 나갔다 — 예산 소비
    await _emit(pool, job_id, "step", {
        "candidate": candidate, "attempt": attempt, "status": "untuck_pass",
        "outcome": "applied", "image_hash": before,
        "result_hash": hashlib.sha256(out.image).hexdigest()[:12]})
    return out, True


def _decode_bgr(image_bytes: bytes):
    arr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("이미지 디코드 실패")
    return arr


def _hc_fail_summary(reason: str, detail: str = "", **extra) -> dict:
    """hybrid composite 실패 요약 — qc_scores 에 저장되는 shape (바이트/URL 없음)."""
    return {"applied": False, "needsReview": True, "failureReason": reason,
            "failureDetail": detail[:200], "pipelineVersion": HC_PIPELINE_VERSION, **extra}


class _HybridCompositeFailClosed(Exception):
    """P0 hybrid composite fail-closed signal before R2 save/finalize success."""

    def __init__(self, summary: dict):
        self.summary = dict(summary)
        reason = self.summary.get("failureReason") or "pattern_metric_failed"
        detail = self.summary.get("failureDetail") or ""
        super().__init__(f"{reason}: {detail}"[:300])


def _raise_if_hybrid_failed_closed(summary: dict | None) -> None:
    if isinstance(summary, dict) and summary.get("applied") is False:
        raise _HybridCompositeFailClosed(summary)


async def _delete_uploaded_candidate_keys(r2, candidates: list[dict]) -> None:
    """이미 업로드된 후보를 best-effort 삭제한다. 실패 종결 전 orphan 방지용."""
    for c in candidates:
        key = c.get("key") if isinstance(c, dict) else None
        if not key:
            continue
        try:
            await asyncio.to_thread(r2.delete, key)
        except Exception:
            log.warning("orphan R2 cleanup failed: %s", key)


async def _fail_closed_hybrid_job_if_needed(r2, fail, passed: list[dict], meta: dict | None) -> bool:
    """hybrid fail-closed 가 하나라도 있으면 업로드 후보를 지우고 job 실패로 종결한다."""
    if meta is None:
        return False
    await _delete_uploaded_candidate_keys(r2, passed)
    await fail("패턴 합성 검증에 실패했어요. 상품 사진을 확인한 뒤 다시 시도해 주세요.", meta)
    return True


async def _apply_hybrid_composite(
    *, pool, s, job_id, candidate, attempt, res, prod_refs, product, analysis,
    has_fine_pattern,
):
    """deterministic hybrid stripe composite. → (선택 결과, hybrid 요약 dict|None).

    모든 generative geometry edit(untuck/axis/bust) **뒤**, 최종 QC/R2 저장 **앞**에서만
    호출된다. 여기서부터 출고까지 image-generation/edit 호출은 0회다 — 합성 불가·저신뢰·
    metric 실패는 전부 typed needs_review 로 끝나고, 구 generative 재생성으로 돌아가는
    경로는 존재하지 않는다(코드째 삭제됨).
    """
    if getattr(s, "mannequin_hybrid_composite", "off") != "on":
        return res, None
    if not has_fine_pattern:
        return res, None  # 무지 상품 — 재현할 고주파가 없어 대상 아님(기존 경로 그대로)

    async def emit(status, **payload):
        await _emit(pool, job_id, "step", {
            "candidate": candidate, "attempt": attempt, "status": status, **payload})

    await emit("hybrid_composite_started", pattern_risk=True,
               pipeline_version=HC_PIPELINE_VERSION)

    async def fail(reason, detail="", **extra):
        summary = _hc_fail_summary(reason, detail, **extra)
        await emit("hybrid_composite_completed", outcome=reason, detail=detail[:200])
        return res, summary

    # 소스 선택은 P0 authority 계약 그대로 — `Detail → Front → Back → Fit`, asset dedup.
    sources = select_pattern_sources(prod_refs, limit=2)
    detail_ref = next((r for r in sources if r.slot == "Detail"), None)
    front_ref = next((r for r in sources if r.slot == "Front"), None)
    if detail_ref is None or front_ref is None:
        return await fail("reference_insufficient",
                          f"필수 슬롯 부재 (Detail={detail_ref is not None}, "
                          f"Front={front_ref is not None})",
                          selectedSlots=[r.slot for r in sources])

    try:
        detail_bgr = await asyncio.to_thread(_decode_bgr, detail_ref.image.data)
        front_bgr = await asyncio.to_thread(_decode_bgr, front_ref.image.data)
        carrier_bgr = await asyncio.to_thread(_decode_bgr, res.image)
    except Exception as e:
        return await fail("reference_insufficient", f"디코드 실패: {e}")

    front_sha_early = hashlib.sha256(front_ref.image.data).hexdigest()
    # Stage 1 — 입력 gate (Detail)
    val = await asyncio.to_thread(hc_source.validate_stripe_source, detail_bgr)
    if isinstance(val, CompositeFailure):
        await emit("hybrid_source_validated", ok=False, reason=val.reason,
                   detail=val.detail[:200])
        return await fail(val.reason, val.detail)
    await emit("hybrid_source_validated", ok=True, roi=list(val.roi),
               n_periods=val.n_periods_in_roi, axis=val.axis, **val.metrics)

    # Stage 2 — stripe model (Detail ROI)
    x0, y0, x1, y1 = val.roi
    detail_sha = hashlib.sha256(detail_ref.image.data).hexdigest()
    model_or_fail = await asyncio.to_thread(
        hc_stripe.extract_stripe_model_scan, detail_bgr[y0:y1, x0:x1],
        source_asset_id=detail_ref.asset_id, source_sha256=detail_sha,
        source_roi=val.roi)
    if isinstance(model_or_fail, CompositeFailure):
        await emit("hybrid_stripe_model", ok=False, reason=model_or_fail.reason,
                   detail=model_or_fail.detail[:200])
        return await fail(model_or_fail.reason, model_or_fail.detail)
    model = model_or_fail
    await emit("hybrid_stripe_model", ok=True, **model.summary())

    # Stage 3 준비 — source/carrier 기하 (vision 은 좌표만, 판정은 코드)
    try:
        # 이중 호출 합의 — vision landmark 지터가 결과를 run 마다 굴리는 것을 실측으로
        # 확인(zero-cost 평가). 좌표는 평균, 불일치는 typed 실패.
        src_raw = hybrid_landmarks.merge_geometry_pair(
            await hybrid_landmarks.extract_geometry(s, front_ref.image),
            await hybrid_landmarks.extract_geometry(s, front_ref.image))
        car_img = InlineImage(res.mime, res.image)
        car_raw = hybrid_landmarks.merge_geometry_pair(
            await hybrid_landmarks.extract_geometry(s, car_img),
            await hybrid_landmarks.extract_geometry(s, car_img))
    except Exception as e:
        return await fail("panel_landmarks_invalid", f"기하 추출 실패: {type(e).__name__}")
    if src_raw[1] is not None:
        return await fail("panel_landmarks_invalid", f"source: {src_raw[1]}")
    if car_raw[1] is not None:
        return await fail("panel_landmarks_invalid", f"carrier: {car_raw[1]}")
    src_raw, car_raw = src_raw[0], car_raw[0]
    src_lm, src_inv, src_err = hybrid_landmarks.validate_geometry(
        src_raw, aspect_hw=front_bgr.shape[0] / front_bgr.shape[1])
    if src_err:
        return await fail("panel_landmarks_invalid", f"source: {src_err}")
    car_lm, car_inv, car_err = hybrid_landmarks.validate_geometry(
        car_raw, aspect_hw=carrier_bgr.shape[0] / carrier_bgr.shape[1])
    if car_err:
        return await fail("panel_landmarks_invalid", f"carrier: {car_err}")
    src_boxes = (src_inv or {}).pop("component_boxes", {})
    car_boxes = (car_inv or {}).pop("component_boxes", {})

    # scale anchor — source Front torso 에서 **의류 기준 줄 방향과** 단위 반복 수를 잰다.
    # Detail 근접컷은 원단을 눕혀 찍는 경우가 흔해서(실측: 세로 줄 셔츠의 Detail 이 수평 밴드)
    # model.axis(사진 좌표)는 의류 방향의 근거가 못 된다. 의류에 실제로 입혀진 방향은
    # Front(의류 전체가 보이는 사진)의 torso 측정이 정본이다. 1D 프로파일 모델은 방향과
    # 무관하므로 축만 Front 기준으로 바꿔 끼운다.
    fh, fw = front_bgr.shape[:2]
    fy0 = int(min(src_lm["shoulder_l"][1], src_lm["shoulder_r"][1]) * fh)
    fy1 = int(max(src_lm["hem_l"][1], src_lm["hem_r"][1]) * fh)
    fx0 = int(min(src_lm["shoulder_l"][0], src_lm["hem_l"][0]) * fw)
    fx1 = int(max(src_lm["shoulder_r"][0], src_lm["hem_r"][0]) * fw)
    torso_crop = front_bgr[max(0, fy0):fy1, max(0, fx0):fx1]
    # 스케일 앵커 = Front torso 에서의 **scan 재추출 + Detail 모델과의 구조 대조**.
    # guided 상관 탐색은 sub-line lag 에 잠겼다(실측: 15px/corr 0.54 vs scan 21px/4색 일치).
    # 두 사진에서 독립 추출한 모델의 색 수·폭 비가 일치하면 그 주기는 신뢰할 수 있다.
    front_model = await asyncio.to_thread(
        hc_stripe.extract_stripe_model_scan, torso_crop,
        source_asset_id=front_ref.asset_id, source_sha256=front_sha_early,
        source_roi=(fx0, fy0, fx1, fy1))
    anchor_corr = None
    front_scan_ok = (not isinstance(front_model, CompositeFailure)
                     and front_model.confidence >= 0.5)
    # 주기(스케일 진실)와 팔레트는 별개 판단이다. front 줌에서 잔줄이 2색으로 퇴화해도
    # (선폭 2.7px 해상 한계 실측) scan 의 **주기**는 패치 합의라 신뢰 가능(20.6 vs
    # guided 하모닉 45 — 2.2×). 주기를 guided 에 맡기면 출력 줄 간격이 실물의 ~3배로
    # 성겨지고, QC 는 target 자기일관만 재서 그 스케일 오류를 못 잡는다.
    if front_scan_ok:
        garment_axis = front_model.axis
        front_period_px = float(front_model.period_px)
        anchor_corr = round(front_model.confidence, 3)
        # 색 팔레트의 정본 = front-scan 모델 — 단, **구조 완전 일치**(색 수 동일 + 폭 비
        # 60% 이내)일 때만. 그늘 착용 Detail 은 chroma 가 퇴색된다(실측: beige warm b*
        # 소실 → 민트-그린). 구조 대조 통과 시에만 교체하므로 바꿔치기는 불가능하다.
        if (len(front_model.color_sequence_lab) == len(model.color_sequence_lab)
                and max(abs(a - b) / max(b, 1e-6) for a, b in
                        zip(front_model.line_width_ratios,
                            model.line_width_ratios)) <= 0.6):
            await emit("hybrid_palette_source", chosen="front_scan",
                       detail_colors=len(model.color_sequence_lab),
                       front_colors=len(front_model.color_sequence_lab))
            model = front_model
        else:
            # 구조가 퇴화(front 줌에서 잔줄 미해상)해도 **ground 색**은 양쪽 모두 최광폭
            # run 이라 대응이 확실하다. 그늘 Detail 의 조명 캐스트(민트 방향 ab 편이)를
            # front ground 와의 Δab 로 전 팔레트에 가산 보정 — 상대 색 구조는 Detail 것
            # 그대로, 절대 캐스트만 flat-lay 자연광으로 옮긴다. 색 발명 없음.
            import dataclasses as _dc
            d_ab = np.array(model.ground_color_lab[1:], np.float32)
            f_ab = np.array(front_model.ground_color_lab[1:], np.float32)
            delta = np.clip(f_ab - d_ab, -15.0, 15.0)
            if float(np.abs(delta).max()) >= 1.0:
                prof = model.period_profile_lab.copy()
                prof[:, 1:] += delta
                model = _dc.replace(
                    model,
                    period_profile_lab=prof,
                    ground_color_lab=(model.ground_color_lab[0],
                                      float(d_ab[0] + delta[0]), float(d_ab[1] + delta[1])),
                    color_sequence_lab=tuple(
                        (c[0], float(c[1] + delta[0]), float(c[2] + delta[1]))
                        for c in model.color_sequence_lab),
                )
                await emit("hybrid_palette_source", chosen="detail_plus_front_ground_cast",
                           delta_a=round(float(delta[0]), 2), delta_b=round(float(delta[1]), 2))
    else:
        anchor = await asyncio.to_thread(hc_stripe.find_period_guided, torso_crop, model)
        if anchor is None:
            return await fail(
                "stripe_model_low_confidence",
                "source Front torso 반복 앵커 실패 (scan 구조 불일치 + guided 실패)",
                frontScan=(front_model.reason if isinstance(front_model, CompositeFailure)
                           else {"n_colors": len(front_model.color_sequence_lab)}))
        garment_axis, front_period_px, anchor_corr = anchor
    torso_span_src = (fy1 - fy0) if garment_axis == "horizontal" else (fx1 - fx0)
    repeats_on_torso = torso_span_src / front_period_px
    ch, cw = carrier_bgr.shape[:2]
    t_torso_span = (
        (max(car_lm["hem_l"][1], car_lm["hem_r"][1])
         - min(car_lm["shoulder_l"][1], car_lm["shoulder_r"][1])) * ch
        if garment_axis == "horizontal" else
        (max(car_lm["shoulder_r"][0], car_lm["hem_r"][0])
         - min(car_lm["shoulder_l"][0], car_lm["hem_l"][0])) * cw)
    target_period_px = float(t_torso_span / max(repeats_on_torso, 1e-6))
    if target_period_px < 6.0:
        # 선폭이 1px 대로 떨어지는 피치 — line 단위 합성·검증이 물리적으로 성립하지 않는
        # 영역이다(sub-Nyquist). 평균색 블렌드 합성 모드가 구현되기 전까지 typed 거부가
        # 정직한 동작이다. 성긴 가짜 스케일로 출력하는 것이 최악(같은 상품 아님).
        return await fail("pattern_metric_failed",
                          f"target pitch {target_period_px:.1f}px — line 합성 하한(6px) 미만, "
                          "sub-Nyquist 블렌드 모드 미구현")
    await emit("hybrid_scale_anchor", garment_axis=garment_axis,
               detail_photo_axis=model.axis,
               front_period_px=round(front_period_px, 2),
               anchor_corr=round(anchor_corr, 3),
               repeats_on_torso=round(repeats_on_torso, 2),
               target_period_px=round(target_period_px, 2))

    # construction 비교 입력 보강 — 양쪽 실루엣 mask 에서 같은 연산자로 aspect 유도.
    # vision landmark 지터가 live 에서 torso_aspect 상대오차 0.80 오판을 만들었다(QA).
    # stripe 상품은 양쪽 다 줄무늬 에너지 mask 가 성립한다(source=실물 줄, carrier=생성 줄).
    def _aspect_via_energy(img, lm):
        ih, iw = img.shape[:2]
        quad = np.array([[lm["shoulder_l"][0] * iw, lm["shoulder_l"][1] * ih],
                         [lm["shoulder_r"][0] * iw, lm["shoulder_r"][1] * ih],
                         [lm["hem_r"][0] * iw, lm["hem_r"][1] * ih],
                         [lm["hem_l"][0] * iw, lm["hem_l"][1] * ih]], np.float32)
        m = hc_panel.mask_stripe_energy(img, [quad])
        return hc_panel.mask_aspect_from_silhouette(m)

    try:
        src_aspect_mask = await asyncio.to_thread(_aspect_via_energy, front_bgr, src_lm)
        car_aspect_mask = await asyncio.to_thread(_aspect_via_energy, carrier_bgr, car_lm)
    except Exception:
        src_aspect_mask = car_aspect_mask = None
    # 포즈 불변량 — **줄무늬 수**. mask-aspect 는 소매 포즈(펼침 vs 내림)에 오염된다
    # (실측: 같은 셔츠 flat-lay↔착장 1.76×). 줄은 원단에 붙어 있어 torso 를 가로지르는
    # 반복 수는 포즈·줌·뷰와 무관하다. carrier 의 자체 줄 주기를 재서 source 반복 수와
    # 대조한다 — 이것이 v6(회색 핀스트라이프 = pitch 자체가 다른 재해석)를 잡는 검사다.
    car_repeats = None
    try:
        cy0 = int(min(car_lm["shoulder_l"][1], car_lm["shoulder_r"][1]) * ch)
        cy1 = int(max(car_lm["hem_l"][1], car_lm["hem_r"][1]) * ch)
        cx0 = int(min(car_lm["shoulder_l"][0], car_lm["hem_l"][0]) * cw)
        cx1 = int(max(car_lm["shoulder_r"][0], car_lm["hem_r"][0]) * cw)
        crop_c = carrier_bgr[max(0, cy0):cy1, max(0, cx0):cx1]
        # 줄무늬는 원단 전역 신호, 플래킷 단추는 중앙 국소 신호다 — 무지 fixture 에서
        # 단추 7개가 주기(repeats 4.0)로 오인돼 오차단한 실측. 중앙 20% 를 제외한
        # 좌/우 반쪽이 **둘 다** 의류 스케일에서 유효하고 주기가 15% 내로 합치할 때만
        # carrier 에 줄무늬가 있다고 인정한다.
        cw_c = crop_c.shape[1]
        halves = [crop_c[:, :int(cw_c * 0.40)], crop_c[:, int(cw_c * 0.60):]]
        periods = []
        for half in halves:
            if min(half.shape[:2]) < 64:
                break
            hx = await asyncio.to_thread(hc_stripe.measure_axes, half)
            ax_h = hx.get(garment_axis)
            if (ax_h is None or not ax_h.period_px or ax_h.strength < 0.3
                    or not hx.get(f"{garment_axis}_valid")):
                break
            periods.append(float(ax_h.period_px))
        if (len(periods) == 2
                and abs(periods[0] - periods[1]) / max(periods) <= 0.15):
            car_span = (cy1 - cy0) if garment_axis == "horizontal" else (cx1 - cx0)
            cand = car_span / (sum(periods) / 2)
            if cand >= 4.0:  # 저주파 주름(반복 1~3회) 배제
                car_repeats = cand
    except Exception:
        car_repeats = None
    if car_repeats:
        rel = abs(car_repeats - repeats_on_torso) / max(repeats_on_torso, 1e-6)
        await emit("hybrid_repeat_invariant",
                   source_repeats=round(repeats_on_torso, 1),
                   carrier_repeats=round(car_repeats, 1), rel_err=round(rel, 3))
        if rel > 0.40:
            summary = _hc_fail_summary(
                "geometry_carrier_mismatch",
                f"torso 줄 수 불변량 위반: source {repeats_on_torso:.1f} vs "
                f"carrier {car_repeats:.1f} (rel {rel:.2f} > 0.40)")
            await emit("hybrid_composite_completed", outcome="geometry_carrier_mismatch",
                       detail=summary["failureDetail"])
            return res, summary
        # 줄 수 불변량이 성립하면 aspect(포즈 오염) 비교는 생략 — 관측만 남긴다
        src_inv = {**(src_inv or {}), "torso_aspect_mask": None}
        car_inv = {**(car_inv or {}), "torso_aspect_mask": None}
    elif src_aspect_mask and car_aspect_mask:
        src_inv = {**(src_inv or {}), "torso_aspect_mask": src_aspect_mask}
        car_inv = {**(car_inv or {}), "torso_aspect_mask": car_aspect_mask}
        await emit("hybrid_geometry_anchor",
                   source_aspect_mask=round(src_aspect_mask, 3),
                   carrier_aspect_mask=round(car_aspect_mask, 3),
                   source_aspect_vision=(src_inv or {}).get("torso_aspect"),
                   carrier_aspect_vision=(car_inv or {}).get("torso_aspect"))

    # Stage 3 — panel map (+ construction 대조)
    pm = await asyncio.to_thread(
        hc_panel.build_panel_map, carrier_bgr, car_lm,
        source_inventory=src_inv, carrier_inventory=car_inv, strategy="auto")
    if isinstance(pm, CompositeFailure):
        await emit("hybrid_panel_map", ok=False, reason=pm.reason, detail=pm.detail[:200],
                   metrics=pm.metrics)
        return await fail(pm.reason, pm.detail, constructionMetrics=pm.metrics)
    await emit("hybrid_panel_map", ok=True, confidence=round(pm.confidence, 3),
               strategy=pm.strategy, panels=[p.name for p in pm.panels],
               metrics=pm.metrics)

    # Stage 4 — 결정론 warp/composite
    art = await asyncio.to_thread(
        hc_warp.composite_stripe, carrier_bgr, pm, model,
        target_period_px=target_period_px, target_axis=garment_axis,
        component_boxes=car_boxes, source_bgr=front_bgr,
        source_component_boxes=src_boxes)
    if isinstance(art, CompositeFailure):
        await emit("hybrid_warp_composite", ok=False, reason=art.reason,
                   detail=art.detail[:200], metrics=art.metrics)
        return await fail(art.reason, art.detail)
    await emit("hybrid_warp_composite", ok=True, coverage=round(art.source_coverage, 4),
               panel_metrics=art.panel_metrics,
               components_needing_review=list(art.components_needing_review),
               **art.metrics)

    # Stage 5 — deterministic QC (LLM 이 못 뒤집는 판정)
    qc = await asyncio.to_thread(
        hc_qc.verify_composite, art.image_bgr, carrier_bgr, pm, model,
        target_period_px=target_period_px, target_axis=garment_axis)
    qc_event_metrics = {k: v for k, v in qc.metrics.items() if k != "failure_details"}
    await emit("hybrid_deterministic_qc", passed=qc.passed,
               failures=list(qc.failures), metrics=qc_event_metrics,
               failure_details=[d.get("detail", "")[:160]
                                for d in qc.metrics.get("failure_details", [])][:6])
    if not qc.passed:
        return await fail(qc.failures[0] if qc.failures else "pattern_metric_failed",
                          "; ".join(d.get("detail", "") for d in
                                    qc.metrics.get("failure_details", []))[:200],
                          deterministicMetrics=qc_event_metrics)

    ok, png = cv2.imencode(".png", art.image_bgr)
    if not ok:
        return await fail("pattern_metric_failed", "출력 인코딩 실패")
    out_bytes = png.tobytes()
    needs_review = bool(art.components_needing_review)
    front_sha = front_sha_early
    summary = {
        "applied": True,
        "needsReview": needs_review,
        "componentsNeedingReview": list(art.components_needing_review),
        "deterministicPassed": True,
        "pipelineVersion": HC_PIPELINE_VERSION,
        "versions": {"pipeline": HC_PIPELINE_VERSION,
                     "extractor": model.extractor_version, "panelMap": pm.version,
                     "warp": art.version, "qc": qc.version},
        "stripeModel": model.summary(),
        "sourceAssets": {
            "detail": {"assetId": detail_ref.asset_id, "sha256": detail_sha,
                       "roi": list(val.roi)},
            "front": {"assetId": front_ref.asset_id, "sha256": front_sha},
        },
        "targetPeriodPx": round(target_period_px, 2),
        "targetAxis": garment_axis,
        "sourceCoverage": round(art.source_coverage, 4),
        "panelMetrics": art.panel_metrics,
        "deterministicMetrics": qc_event_metrics,
        "outputSha256": hashlib.sha256(out_bytes).hexdigest(),
        "carrierSha256": hashlib.sha256(res.image).hexdigest(),
    }
    await emit("hybrid_composite_completed", outcome="applied",
               needs_review=needs_review,
               components_needing_review=list(art.components_needing_review),
               coverage=round(art.source_coverage, 4),
               output_hash=summary["outputSha256"][:12])
    new_res = GeminiImageResult(
        image=out_bytes, mime="image/png",
        latency_ms=getattr(res, "latency_ms", 0), usage=getattr(res, "usage", None))
    return new_res, summary


async def _apply_edits(
    *, pool, gemini, s, job_id, candidate, attempt, model, res, p2, prod_refs, match_img,
    fit_profile, profile_hash, base_gender, calls_spent, clothing_type=None, enabled=True,
    image_size=None, has_fine_pattern=False,
):
    """채택본에 편집(축 교정 → 가슴 2패스)을 적용하고, 바뀌었으면 재판정·회귀 시 되돌린다.

    → (선택 이미지, 그 이미지의 판정, 갱신된 calls_spent)

    루프 안 정상 경로와 루프 종료 구제 경로가 **같은 함수**를 쓴다. 이 시퀀스(편집 → 해시
    비교 → 재판정 → 되돌리기)는 미묘해서 두 벌로 두면 반드시 갈린다.

    `enabled=False` 는 이미 편집을 거친 구제본용 — 아무것도 하지 않고 그대로 돌려준다.
    """
    if not enabled:
        return res, p2, calls_spent
    # 판정(axis/image QC)은 상품 사진 **전체**를 그대로 본다 — 기존 동작 불변. 역할 기반
    # 선택은 원단 2패스만의 문제라 거기서만 refs 를 쓴다.
    prod_imgs = [r.image for r in prod_refs]
    pre_hash = hashlib.sha256(res.image).hexdigest()
    pre_res, pre_p2 = res, p2
    # untuck — 편집 체인 **맨 앞**. 밑단 위치(구도)가 먼저 확정돼야 축 QC 가 실제 밑단을 보고
    # 판정하고(특히 length 축), 볼륨·원단 편집이 최종 구도 위에서 이뤄진다.
    res, untuck_spent = await _apply_untuck_pass(
        pool=pool, gemini=gemini, s=s, job_id=job_id, candidate=candidate, attempt=attempt,
        res=res, match_img=match_img, calls_spent=calls_spent,
        clothing_type=clothing_type, image_size=image_size)
    calls_spent += untuck_spent
    # P1 축 QC: 채택본이 선언 핏 축을 반영했는지 판정, enforce면 편집 교정 1회
    # (실패 이미지 편집 — §H 실증). fail-open: 어떤 실패도 채택 자체를 막지 않는다.
    res, axis_spent = await _apply_axis_qc(
        pool=pool, gemini=gemini, s=s, job_id=job_id, candidate=candidate, attempt=attempt,
        model=model, res=res, prod_imgs=prod_imgs, match_img=match_img,
        fit_profile=fit_profile, profile_hash=profile_hash, calls_spent=calls_spent,
        image_size=image_size)
    calls_spent += axis_spent
    post_axis_res = res
    # 여성 기본 가슴 볼륨 2패스 — R2 저장 직전, 채택본이 확정된 뒤. fail-open.
    res, bust_spent = await _apply_bust_pass(
        pool=pool, gemini=gemini, s=s, job_id=job_id, candidate=candidate, attempt=attempt,
        base_gender=base_gender, res=res, calls_spent=calls_spent,
        clothing_type=clothing_type, image_size=image_size)
    calls_spent += bust_spent
    # (2026-08-01) 원단 패턴 2패스는 제거됐다 — whole-image generative 재생성으로는 잔줄
    # 패턴 동일성을 증명할 수 없었고(실측 blind visual 3/3 FAIL), 패턴 동일성은 이제
    # geometry 편집이 모두 끝난 뒤 deterministic hybrid composite 가 담당한다.
    # A~C 점수는 **편집 전** 원본에 매긴 것이다. 편집이 이미지를 바꿨다면 저장되는 점수가
    # 실제 출고본의 점수가 아니게 된다(검수자가 다른 이미지의 숫자를 보고 판단하게 됨).
    # 이미지가 실제로 바뀐 경우에만 재판정한다 — 안 바뀌었으면 vision 콜 낭비다.
    if not (isinstance(p2, dict) and prod_imgs
            and hashlib.sha256(res.image).hexdigest() != pre_hash):
        return res, p2, calls_spent
    try:
        p2 = await image_qc.verdict(
            s, prod_imgs, InlineImage(res.mime, res.image), scored=True, fit_profile=fit_profile)
        await _emit(pool, job_id, "step", {
            "candidate": candidate, "attempt": attempt,
            "status": "image_qc_rescored", "imageQc": p2})
    except Exception as e:
        # fail-open: 재판정 실패 시 편집 전 점수를 쓰되, 그 사실을 남긴다.
        log.warning("image_qc rescore failed for job %s: %r", job_id, e)
        await _emit(pool, job_id, "step", {
            "candidate": candidate, "attempt": attempt,
            "status": "image_qc_rescore_failed", "error": type(e).__name__})
        return res, pre_p2, calls_spent
    # 재판정은 하락을 **기록**할 뿐이라, 망친 편집본이 그대로 출고됐다.
    if edit_regressed(s, pre_p2, p2):
        axis_hash = hashlib.sha256(post_axis_res.image).hexdigest()
        res, p2 = await _rollback_edits(
            pool=pool, s=s, job_id=job_id, candidate=candidate, attempt=attempt,
            prod_imgs=prod_imgs, pre_res=pre_res, pre_p2=pre_p2,
            post_axis_res=post_axis_res, post_p2=p2,
            axis_changed=axis_hash != pre_hash,
            bust_changed=hashlib.sha256(res.image).hexdigest() != axis_hash,
            fit_profile=fit_profile)
    return res, p2, calls_spent


async def _save_cut(*, s, r2, user_id, project_id, job_id, candidate, base_fit, res, qc_scores):
    """채택본을 R2 에 올리고 finalize 용 dict 를 만든다. 출고 지점은 여기 하나뿐이다."""
    if qc_scores is not None:
        qc_scores["outcome"] = score_outcome(s, qc_scores)
        hc = qc_scores.get("hybridComposite")
        if isinstance(hc, dict):
            # deterministic 판정과 LLM 판정의 우선순위를 출고 지점 한 곳에서 강제한다:
            #  · 합성 실패/부분실패(needsReview) → auto_pass 로 나갈 수 없다(강등만, 승격 없음)
            #  · deterministic 통과 → LLM 의 regenerate 가 정상 출고를 막지 못하되,
            #    자동통과로 미화하지도 않는다 → needs_review 로 사람에게 보인다
            if hc.get("needsReview") and qc_scores["outcome"] == "auto_pass":
                qc_scores["outcome"] = "needs_review"
            if hc.get("deterministicPassed") and qc_scores["outcome"] == "regenerate":
                qc_scores["outcome"] = "needs_review"
    ext = ext_for_mime(res.mime) or _EXT_FALLBACK.get(res.mime, "png")
    asset_id = str(uuid.uuid4())
    key = ai_key(user_id, project_id, job_id, asset_id, ext)
    await asyncio.to_thread(r2.put_bytes, key, res.image, res.mime, cache=IMMUTABLE_CACHE)
    w, h = _image_dims(res.image)
    return {
        "asset_id": asset_id, "bucket": s.r2_bucket, "key": key, "mime": res.mime,
        "size": len(res.image), "width": w, "height": h,
        "candidate": candidate, "base_fit": base_fit, "qc_scores": qc_scores,
    }


async def _rollback_edits(
    *, pool, s, job_id, candidate, attempt, prod_imgs,
    pre_res, pre_p2, post_axis_res, post_p2, axis_changed, bust_changed, fit_profile=None,
):
    """회귀한 편집을 되돌린다. → (선택 이미지, 그 이미지의 판정)

    두 편집이 **둘 다 이미지를 바꿨으면** 통째로 버리지 않는다. axis 가 핏을 제대로 고쳐놨는데
    bust 가 형태를 망친 경우, 한 덩어리로 되돌리면 멀쩡한 교정까지 같이 버린다(codex 8차
    MEDIUM). 그래서 bust 만 떼어낸 중간본을 먼저 재판정한다 — 이 추가 vision 콜은 **회귀가
    실제로 일어났고 두 편집이 다 이미지를 바꾼** 경우에만 나간다(관측상 드물다).

    판정 기준은 "호출을 썼는가"가 아니라 **"이미지가 바뀌었는가"**다. bust 는 거부·오류 시
    fail-open 으로 원본을 그대로 돌려주면서도 예산은 소비했다고 보고한다. 소비 기준으로
    분기하면 axis 가 망치고 bust 가 실패한 경우 최종본과 중간본이 **같은 이미지**인데도
    분기를 타서, 같은 손상본을 한 번 더 재판정하고 이번엔 통과하면 그대로 출고한다
    (codex 9차 HIGH — 재현됨).

    중간본 재판정이 실패하면 편집 전으로 되돌린다. fail-open 이 아니라 fail-safe 인 이유:
    여기까지 왔다는 건 이미 손상이 확인됐다는 뜻이라, 판정 불가 시엔 안전한 쪽을 고른다.
    """
    async def _revert_to(res, p2, reason):
        await _emit(pool, job_id, "step", {
            "candidate": candidate, "attempt": attempt, "status": "edit_reverted",
            "reason": reason, "from": score_outcome(s, post_p2), "to": score_outcome(s, p2)})
        return res, p2

    if not (axis_changed and bust_changed and post_axis_res is not None and prod_imgs):
        return await _revert_to(pre_res, pre_p2, "all_edits")
    try:
        mid_p2 = await image_qc.verdict(
            s, prod_imgs, InlineImage(post_axis_res.mime, post_axis_res.image), scored=True,
            fit_profile=fit_profile)
        await _emit(pool, job_id, "step", {
            "candidate": candidate, "attempt": attempt,
            "status": "image_qc_post_axis", "imageQc": mid_p2})
    except Exception as e:
        log.warning("post-axis rescore failed for job %s: %r", job_id, e)
        return await _revert_to(pre_res, pre_p2, "post_axis_rescore_failed")
    if edit_regressed(s, pre_p2, mid_p2):
        return await _revert_to(pre_res, pre_p2, "all_edits")  # axis 도 손상원
    return await _revert_to(post_axis_res, mid_p2, "bust_only")


async def _run_candidate(
    *, app, job, candidate, base_fit, base_gender, base_img, prod_refs, match_img,
    product_count, template, product, analysis, clothing_type, image_manifest="", fit_profile=None,
    adjusted_axes=(), fit_profile_source="legacy_analysis_fallback", ref_imgs=(),
    generation_path="fresh", parent_cut_img=None, adjust_directives="",
) -> dict | None:
    """후보 1개 생성. 통과 시 R2 저장 후 finalize용 dict 반환, 실패 시 None.

    `prod_refs` 는 역할(slot)이 붙은 상품 참조다. 기존 생성·QC 가 쓰는 bare 바이트 목록은
    여기서 파생한다 — 슬롯을 버리는 지점을 하나도 남기지 않으려고 입력 자체를 refs 로 받는다.
    """
    s = app.state.settings
    pool, r2, gemini = app.state.pool, app.state.r2, app.state.gemini
    job_id, user_id, project_id = job["id"], job["user_id"], job["project_id"]
    prod_refs = tuple(prod_refs)
    prod_imgs = [r.image for r in prod_refs]
    # 미세 패턴 상품은 해상도를 올린다. **편집 패스(축 교정·2패스)도 같은 값**을 써야 한다 —
    # 편집이 기본 해상도로 다시 렌더하면 어렵게 올린 4K 가 그 자리에서 깎인다.
    image_size = effective_image_size(s, product, analysis)
    has_fine_pattern = mannequin.has_fine_pattern(product, analysis)
    if generation_path == "edit" and parent_cut_img is not None and adjust_directives:
        # 편집 프롬프트의 image 1 계약: 현재 컷이 반드시 첫 장이고, 상품 정체성 앵커가 뒤따른다.
        # 상품 참조끼리는 역할 우선순위(Detail → Front → Back → Fit)로 정렬한다 — 매니페스트가
        # 슬롯별 권위를 선언하므로 번호와 실제 이미지가 같은 순서여야 그 문장이 유효하다.
        edit_refs = order_by_role(prod_refs)
        images = [parent_cut_img, *(r.image for r in edit_refs)] + (
            [match_img] if match_img else [])
        base_prompt = render_adjust_prompt(
            adjust_directives, build_adjust_manifest(edit_refs, match_img is not None))
        prompt_version = ADJUST_PROMPT_VERSION
        tier = adjust_edit_tier(s, has_fine_pattern=has_fine_pattern)
        pattern_tier_guard = tier != adjust_edit_tier(s)  # 가드가 실제로 발화했는가
        model = resolve_model(s, tier)
        manifest_refs = edit_refs
    else:
        if generation_path == "edit":
            # 여기 오면 워커가 편집 자격을 줬는데 입력이 비어 온 것이다(현재 도달 불가).
            # 조용히 fresh 로 눕히지 않고 사실을 남긴다 — 패턴이 다시 무작위 생성되는 경로다.
            await _emit(pool, job_id, "step", {
                "candidate": candidate, "status": "edit_path_fallback",
                "reason": "missing_edit_inputs", "requested_mode": "regenerate",
                "pattern_risk": has_fine_pattern})
        generation_path = "fresh"
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
        prompt_version = s.mannequin_prompt_version
        # AG-04는 처음부터 단일 tier(기본 image_high=Pro, 사용자 결정 — Flash·승격 없음).
        # QC 게이팅 시 같은 모델로 재시도(re-roll + 교정 피드백). shadow면 첫 결과 채택.
        tier = tier_for_job(s, job, has_fine_pattern=has_fine_pattern)
        pattern_tier_guard = tier != tier_for_job(s, job)  # 가드가 실제로 발화했는가
        model = resolve_model(s, tier)
        manifest_refs = prod_refs
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
    # 이미지 모델 호출 예산은 한 통이다 — 생성·axis 편집·bust 2패스가 전부 여기서 나간다.
    # 호출 **직전**에 소비하고, 재생성 여부는 남은 잔량으로만 판단한다.
    calls_spent = 0
    profile_hash = _canonical_profile_hash(fit_profile)
    for attempt in range(1, s.mannequin_max_attempts + 1):
        if calls_spent >= s.mannequin_max_attempts:
            # 편집이 예산을 다 먹어 생성조차 못 하는 상태. 사전·최종 게이트가 둘 다 잔량을
            # 보므로 **현재는 도달 불가**다(Pillow QC 가 코드상 강제 shadow 라 그 경로도 죽어
            # 있다). 테스트로 못 잡는 건 커버리지 누락이 아니라 이게 백스톱이기 때문이다 —
            # Pillow enforce 를 되살리면 그 경로가 여기로 떨어진다.
            break
        prompt = f"{feedback}\n\n{base_prompt}" if feedback else base_prompt
        # 관측성(fidelity 설계 D3): 이 attempt 가 실제 쓰는 프로필·프롬프트의 다이제스트만 남긴다
        # (원문 미포함 — 이벤트 ~250B). 실패 원인이 되지 않게 기존 step 과 동일 best-effort.
        await _emit(pool, job_id, "step", {
            "status": "prompt_rendered", "candidate": candidate, "attempt": attempt,
            "profile_hash": profile_hash,
            "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "prompt_version": prompt_version,
            "generation_path": generation_path,
            # 관측 계약: "어떤 역할의 asset 이 어떤 모델·해상도로 나갔는가"를 로그만으로 재현
            # 할 수 있어야 한다(계획 P0 완료 기준). 바이트·URL 은 넣지 않는다(id/slot 만).
            "product_refs": [{"slot": r.slot, "asset_id": r.asset_id} for r in manifest_refs],
            "model_tier": tier,
            "pattern_tier_guard": pattern_tier_guard,
            "has_fine_pattern": has_fine_pattern,
            "image_size": image_size,
            "input_source": fit_profile_source})
        calls_spent += 1  # 성공하든 실패하든 호출은 나갔다
        try:
            res = await gemini.generate_content_image(
                model, prompt, images, image_size,
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
                    s, prod_imgs, InlineImage(res.mime, res.image), scored=True,
                    fit_profile=fit_profile)
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
        reprocess = True          # 구제본이 이미 편집·D축을 거쳤으면 False 로 내린다
        salvaged_series = None
        if p2_reject:
            # reject 후보를 점수와 함께 보관 — 예산 소진 시 "마지막 시도"가 아니라 **최선본**을
            # 구제하기 위해서다. 1차 70점 / 2차 20점인데 20점을 내보내면 재시도가 손해가 된다.
            # 두 번째 요소는 **항상 merge 된 shape** 으로 통일한다. 경로마다 p2(verdict·
            # mismatches 포함)와 qc_scores 가 섞이면, 구제 시 API 계약에 없는 키가 저장된다.
            pre_scores = merge_qc_scores(p2, None)
            if _is_better_candidate(s, pre_scores, pre_reject[1] if pre_reject else None):
                pre_reject = (res, pre_scores, None, p2)
            if not has_budget_for_retry(s, calls_spent=calls_spent):
                # 재생성 여력이 없으면 여기서 끝이다. attempt 번호가 아니라 **남은 호출**로
                # 판단해야 한다 — 편집이 예산을 먹은 상태에서 attempt 만 보면 상한을 넘긴다
                # (codex 2026-07-31 7차 HIGH: max=4 에 5콜 경로).
                # 구제 대상은 **두 풀을 통틀어 최선**이어야 한다. 이전 attempt 에서 편집·D축까지
                # 통과했다가 최종 게이트에서 걸린 후보(final_reject)가 더 좋으면 그걸 쓴다 —
                # 사전 게이트 후보만 보면 60점 검증본을 두고 20점을 내보낸다(codex 2026-07-31).
                if final_reject and _is_better_candidate(s, final_reject[1], pre_reject[1]):
                    # 이미 편집·재판정·D축을 다 거친 출고 준비본이다. 본 경로를 다시 태우면
                    # bust 가 두 번 적용되고 D축 스냅샷이 덮어써진다(codex 7차 MEDIUM).
                    res, salvaged_scores, salvaged_series, p2 = final_reject
                    reprocess = False
                else:
                    res, salvaged_scores, salvaged_series, p2 = pre_reject
                    # 사전 게이트 후보는 편집·D축을 안 거쳤다 → 아래 본 경로가 그걸 수행한다.
                p2_reject, salvaged = False, True
                await _emit(pool, job_id, "step", {
                    "candidate": candidate, "attempt": attempt, "status": "qc_salvaged",
                    "reason": "budget_exhausted", "outcome": score_outcome(s, salvaged_scores)})
        if not pillow_reject and not p2_reject:
            res, p2, calls_spent = await _apply_edits(
                pool=pool, gemini=gemini, s=s, job_id=job_id, candidate=candidate,
                attempt=attempt, model=model, res=res, p2=p2, prod_refs=prod_refs,
                match_img=match_img, fit_profile=fit_profile, profile_hash=profile_hash,
                base_gender=base_gender, calls_spent=calls_spent,
                clothing_type=clothing_type, enabled=reprocess, image_size=image_size,
                has_fine_pattern=has_fine_pattern)
            # deterministic hybrid composite — 모든 generative geometry edit 뒤, 저장 앞.
            # 이 지점 이후 출고까지 image-generation/edit 호출은 0회다.
            hybrid_info = None
            if reprocess:
                res, hybrid_info = await _apply_hybrid_composite(
                    pool=pool, s=s, job_id=job_id, candidate=candidate, attempt=attempt,
                    res=res, prod_refs=prod_refs, product=product, analysis=analysis,
                    has_fine_pattern=has_fine_pattern)
                _raise_if_hybrid_failed_closed(hybrid_info)
                if (hybrid_info and hybrid_info.get("applied")
                        and isinstance(p2, dict) and prod_imgs):
                    # 보조 신호 — 출고본(합성본)에 대한 기존 QC 재판정(analyze 호출, 생성 아님).
                    # deterministic 판정을 뒤집을 수 없다(아래 retry 억제·outcome 강등 참조).
                    try:
                        p2 = await image_qc.verdict(
                            s, prod_imgs, InlineImage(res.mime, res.image), scored=True,
                            fit_profile=fit_profile)
                        await _emit(pool, job_id, "step", {
                            "candidate": candidate, "attempt": attempt,
                            "status": "image_qc_rescored", "imageQc": p2,
                            "subject": "hybrid_composite"})
                    except Exception as e:
                        log.warning("post-composite image_qc failed for job %s: %r",
                                    job_id, e)
            # D축 시리즈 일관성 — bust 2패스 뒤(측정본=출고본), R2 저장 직전. fail-open.
            # 재처리 대상이 아니면(=이미 판정을 거친 구제본) 그때의 스냅샷을 그대로 쓴다.
            series = (
                await _apply_series_qc(
                    app=app, pool=pool, s=s, job_id=job_id,
                    project_id=project_id, candidate=candidate, attempt=attempt, res=res)
                if reprocess else salvaged_series)
            # ── 최종 판정 (단일 지점) ────────────────────────────────────────
            # A~C·D 를 한 스냅샷으로 합쳐 여기서 한 번만 결정한다. 판정이 흩어지면 "API 엔
            # 재생성 필요라 적혀 있는데 성공 컷으로 나가는" 모순이 생긴다(codex 2026-07-31).
            qc_scores = merge_qc_scores(
                p2, series, salvaged=salvaged,
                thresholds=(s.qc_score_auto_pass, s.qc_score_review))
            if hybrid_info is not None:
                qc_scores = {**(qc_scores or {}), "hybridComposite": hybrid_info}
            budget_left = has_budget_for_retry(s, calls_spent=calls_spent)
            # deterministic 합성이 통과한 컷은 LLM 점수의 retry 로 재생성하지 않는다 —
            # 기존 QC 는 보조 신호다(같은 이미지에 3회 중 2회 pass 를 주던 판정으로
            # 결정론적으로 옳은 패턴을 다시 뽑으면 비용만 태운다). outcome 강등은
            # _save_cut 에서 수행되므로 판정 기록은 남는다.
            deterministic_ok = bool(hybrid_info and hybrid_info.get("deterministicPassed"))
            if deterministic_ok and final_decision(s, qc_scores) == "retry":
                await _emit(pool, job_id, "step", {
                    "candidate": candidate, "attempt": attempt,
                    "status": "hybrid_llm_retry_suppressed",
                    "outcome": score_outcome(s, qc_scores)})
            # **R2 저장 전에** 분기한다: 저장 후 continue 하면 재생성마다 고아 객체가 쌓인다.
            if (final_decision(s, qc_scores) == "retry" and budget_left and not salvaged
                    and not deterministic_ok):
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
            # deterministic 통과 컷은 구제(salvage) 표기 대상이 아니다 — LLM retry 억제와
            # 같은 이유. 그대로 저장 경로로 떨어진다.
            if final_decision(s, qc_scores) == "retry" and not salvaged and not deterministic_ok:
                if final_reject and _is_better_candidate(s, final_reject[1], qc_scores):
                    res, qc_scores, _series, _p2 = final_reject
                qc_scores = {**(qc_scores or {}), "salvaged": True}
                await _emit(pool, job_id, "step", {
                    "candidate": candidate, "attempt": attempt, "status": "qc_salvaged",
                    "reason": "budget_exhausted", "outcome": score_outcome(s, qc_scores)})
            return await _save_cut(
                s=s, r2=r2, user_id=user_id, project_id=project_id, job_id=job_id,
                candidate=candidate, base_fit=base_fit, res=res, qc_scores=qc_scores)
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
    # 루프가 통과본 없이 끝났다 — 마지막 생성이 GeminiError 로 죽었거나 전 attempt 가 거절.
    # 여기서 그냥 None 을 돌려주면 앞 attempt 에서 편집·D축까지 통과했다가 최종 게이트에만
    # 걸린 후보(final_reject)를 손에 들고도 셀러가 빈손이 된다(codex 9차 HIGH — 마지막
    # 생성 실패 시 재현). 구제 규율은 예산 소진 경로와 같다: **final_reject 만** 쓴다.
    if final_reject or pre_reject:
        if final_reject:
            # 이미 편집·D축까지 끝난 출고 준비본 — 다시 태우지 않는다.
            res, qc_scores, series, p2 = final_reject
            _raise_if_hybrid_failed_closed(
                (qc_scores or {}).get("hybridComposite") if isinstance(qc_scores, dict) else None)
        else:
            # 사전 게이트 후보는 편집·D축을 안 거쳤다. 그대로 저장하면 검증 안 된 이미지가
            # 나간다(codex 4차 HIGH) — 예산 소진 경로와 **같은 처리**를 태운 뒤 구제한다.
            res, _scores, series, p2 = pre_reject
            res, p2, calls_spent = await _apply_edits(
                pool=pool, gemini=gemini, s=s, job_id=job_id, candidate=candidate,
                attempt=s.mannequin_max_attempts, model=model, res=res, p2=p2,
                prod_refs=prod_refs, match_img=match_img, fit_profile=fit_profile,
                profile_hash=profile_hash, base_gender=base_gender, calls_spent=calls_spent,
                clothing_type=clothing_type, image_size=image_size,
                has_fine_pattern=has_fine_pattern)
            # 구제 경로도 같은 규율 — geometry edit 뒤에는 반드시 composite 를 거친다.
            # high-risk 패턴이 구제라는 이유로 생성 결과 그대로 나가면 안 된다.
            res, salvage_hybrid = await _apply_hybrid_composite(
                pool=pool, s=s, job_id=job_id, candidate=candidate,
                attempt=s.mannequin_max_attempts, res=res, prod_refs=prod_refs,
                product=product, analysis=analysis, has_fine_pattern=has_fine_pattern)
            series = await _apply_series_qc(
                app=app, pool=pool, s=s, job_id=job_id, project_id=project_id,
                candidate=candidate, attempt=s.mannequin_max_attempts, res=res)
            qc_scores = merge_qc_scores(
                p2, series, thresholds=(s.qc_score_auto_pass, s.qc_score_review))
            if salvage_hybrid is not None:
                qc_scores = {**(qc_scores or {}), "hybridComposite": salvage_hybrid}
            _raise_if_hybrid_failed_closed(salvage_hybrid)
        qc_scores = {**(qc_scores or {}), "salvaged": True}
        await _emit(pool, job_id, "step", {
            "candidate": candidate, "status": "qc_salvaged",
            "reason": "loop_exhausted", "outcome": score_outcome(s, qc_scores)})
        return await _save_cut(
            s=s, r2=r2, user_id=user_id, project_id=project_id, job_id=job_id,
            candidate=candidate, base_fit=base_fit, res=res, qc_scores=qc_scores)
    return None  # 구제할 후보조차 없음 → 이 후보 드롭(부분 성공 허용)


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
        # 바이트로 납작해지는 **이 지점**이 예전에 슬롯을 잃던 곳이다. 역할을 함께 들고 간다.
        prod_refs = [
            ProductReference(
                slot=a.get("slot") or "Front", asset_id=a["id"],
                image=InlineImage(
                    a["mime_type"], await asyncio.to_thread(app.state.r2.get_bytes, a["r2_key"])))
            for a in prod_assets
        ]
        prod_imgs = [r.image for r in prod_refs]  # 기존 생성/QC 호환 — refs 에서 파생
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
        manifest = _build_manifest(prod_assets, match_img is not None, clothing_type)
        # fit profile 은 잡 생성 시점 스냅샷이 정본(payload.fitProfileSnapshot — fidelity 설계 D3).
        # 워커가 최신 analysis 를 재독하면 잡 생성↔실행 사이의 저장 경합으로 다른 프로필이
        # 조용히 쓰일 수 있다(무음 유실). 키가 없는 legacy 잡과 malformed 스냅샷은 analysis 폴백.
        snap = (job.get("payload") or {}).get("fitProfileSnapshot")
        snap_valid = _valid_fit_profile_snapshot(snap)
        if snap_valid:
            fit_profile = snap.get("profile")
            adjusted_axes = tuple(a for a in snap.get("adjustedAxes") if isinstance(a, str))
            fit_profile_source = "payload_snapshot"
        else:
            # 편집 자격만 잃는다. malformed/legacy payload 때문에 잡 자체를 실패시키지 않고,
            # 신뢰 가능한 persisted analysis 로 fresh generation 을 수행한다.
            fit_profile = mannequin.effective_fit_profile(analysis, match_img is not None)
            adjusted_axes = ()
            fit_profile_source = (
                "legacy_analysis_fallback" if snap is None else "invalid_snapshot_fallback")
        # 방어: 스냅샷 이후 매칭 자산이 사라졌거나 legacy analysis 에 v2 프로필이 남아 있어도
        # 화면에 없는 별도 의류의 지시가 프롬프트로 전달되지 않게 두 버전 키를 함께 제거한다.
        fit_profile = _fit_profile_for_match_image(fit_profile, match_img is not None)
        resolved_match_id = match_id if match_img is not None else None

        # 조정 편집 자격은 전부 best-effort다. 어느 조건이든 빠지면 기존 fresh 경로로 조용히
        # 돌아가며, 부모 조회/R2 로드 실패가 잡 실패로 번지지 않는다.
        generation_path = "fresh"
        parent_cut_id = None
        parent_edit_depth = None
        parent_cut_img = None
        adjust_directives = ""
        fallback_reason = None
        payload_mode = (job.get("payload") or {}).get("mode")
        if payload_mode == "regenerate":
            if not (snap_valid and isinstance(fit_profile, dict)):
                fallback_reason = "invalid_fit_snapshot"
            else:
                adjust_directives = build_adjust_directives(fit_profile, adjusted_axes)
                if not adjust_directives:
                    fallback_reason = "no_adjust_directives"
                else:
                    parent, lookup_failed = None, False
                    try:
                        async with pool.connection() as conn:
                            parent = await repo.get_mannequin_edit_parent(
                                conn, user_id, project_id)
                    except Exception:
                        parent, lookup_failed = None, True
                    parent_edit_depth, fallback_reason = classify_parent_edit(
                        parent, fit_profile, resolved_match_id)
                    if lookup_failed:
                        fallback_reason = "parent_lookup_failed"
                    if parent_edit_depth is not None:
                        try:
                            parent_bytes = await asyncio.to_thread(
                                app.state.r2.get_bytes, parent["r2_key"])
                            parent_cut_img = InlineImage(parent["mime_type"], parent_bytes)
                        except Exception:
                            parent_cut_img = None
                        if parent_cut_img is not None:
                            generation_path = "edit"
                            parent_cut_id = parent["id"]
                        else:
                            # 부모 컷을 못 읽으면 편집 자격도 없다 — depth 를 비워 metadata 가
                            # "edit 인 척"하지 않게 한다.
                            parent_edit_depth = None
                            fallback_reason = "parent_asset_load_failed"
        # 조정 요청이 fresh 로 떨어졌으면 그 사유를 남긴다. 조용히 폴백하면 셀러에겐 "조정했는데
        # 패턴이 또 달라졌다"만 남고, 우리는 그게 편집 미적용 탓인지 편집 실패 탓인지 못 가른다.
        if payload_mode == "regenerate" and generation_path != "edit":
            await _emit(pool, job_id, "step", {
                "status": "edit_path_fallback",
                # 분류에 실패했으면 그 사실을 그대로 남긴다. 그럴듯한 사유를 기본값으로 채우면
                # 집계가 조용히 오염돼, 없는 원인을 고치러 가게 된다.
                "reason": fallback_reason or "unclassified",
                "requested_mode": payload_mode,
                # 패턴 위험도와 함께 집계 — 고위험 상품의 silent fresh 가 가장 아픈 경우다.
                "pattern_risk": mannequin.has_fine_pattern(product, analysis)})

        # Fresh 생성에만 유사 성공 컷을 STYLE REFERENCE 로 첨부한다. Edit 입력은 v2 계약의
        # [parent, products..., match?] 정확한 순서를 유지해야 하므로 검색 자체를 건너뛴다.
        ref_imgs, ref_ids = [], []
        if generation_path == "fresh":
            ref_imgs, ref_ids = await _load_style_refs(
                app, s, prod_imgs=prod_imgs,
                clothing_type=(product.get("clothing_type") or product.get("clothingType")),
                gender=gender)
            if ref_imgs:
                next_i = 2 + len(prod_assets) + (1 if match_img else 0)
                manifest = manifest + "\n" + _ref_manifest_lines(next_i, len(ref_imgs))
                # 이벤트는 잡 소유자에게 전달되므로 타 프로젝트 UUID 는 opaque 해시만 노출한다.
                log.info("job %s style_refs_attached ids=%s", job_id, ref_ids)
                opaque = [hashlib.sha1(i.encode("utf-8")).hexdigest()[:12] for i in ref_ids]
                await _emit(pool, job_id, "step", {
                    "status": "style_refs_attached", "ref_hashes": opaque, "n": len(ref_imgs)})
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
            nonlocal _done, hybrid_fail_closed_meta
            try:
                r = await _run_candidate(
                    app=app, job=job, candidate=letter, base_fit=base_fit, base_gender=gender,
                    base_img=base_img, prod_refs=prod_refs, match_img=match_img,
                    product_count=product_count, template=template, product=product,
                    analysis=analysis, clothing_type=clothing_type, image_manifest=manifest,
                    fit_profile=profile, adjusted_axes=adjusted_axes,
                    fit_profile_source=fit_profile_source, ref_imgs=ref_imgs,
                    generation_path=generation_path, parent_cut_img=parent_cut_img,
                    adjust_directives=adjust_directives)
            except _HybridCompositeFailClosed as e:
                hybrid_fail_closed_meta = {
                    "error": "hybrid_composite_failed_closed",
                    "failureReason": e.summary.get("failureReason"),
                    "detail": e.summary.get("failureDetail", ""),
                    "hybridComposite": e.summary,
                }
                r = None
            except Exception as e:
                log.warning("job %s candidate %s failed: %r", job_id, letter, e)
                r = None
            async with _progress_lock:
                _done += 1
                # 후보 완료 시 35→60 (85 는 아래 finalizing 이 덮음).
                next_progress = min(85, 35 + _done * 25)
            await _emit_generation_progress(next_progress)
            return r

        hybrid_fail_closed_meta = None
        progress_task = asyncio.create_task(_tick_generation_progress())
        try:
            results = [await _cand("A", legacy_base_fit, fit_profile)]
        finally:
            _generation_done.set()
            progress_task.cancel()
            with suppress(asyncio.CancelledError):
                await progress_task
        passed = [r for r in results if isinstance(r, dict)]

        if await _fail_closed_hybrid_job_if_needed(
                app.state.r2, _fail, passed, hybrid_fail_closed_meta):
            return

        if not passed:
            await _fail("마네킹컷 생성에 실패했어요. 다시 시도해 주세요.", {"error": "all_candidates_failed"})
            return
        await _emit(pool, job_id, "progress", {"progress": 85, "phase": "finalizing"})

        cut_generation_metadata = {
            "generationPath": generation_path,
            "editDepth": (parent_edit_depth + 1) if generation_path == "edit" else 0,
            "parentCutId": parent_cut_id if generation_path == "edit" else None,
            "profileCategory": fit_profile.get("category") if isinstance(fit_profile, dict) else None,
            "profileGender": fit_profile.get("gender") if isinstance(fit_profile, dict) else None,
            "matchItemId": resolved_match_id,
            "promptVersion": (ADJUST_PROMPT_VERSION if generation_path == "edit"
                              else s.mannequin_prompt_version),
        }
        for candidate_result in passed:
            md = dict(cut_generation_metadata)
            qs = candidate_result.get("qc_scores")
            hc = qs.get("hybridComposite") if isinstance(qs, dict) else None
            if isinstance(hc, dict) and hc.get("applied"):
                # lineage — 이 컷의 표면이 어느 원본·어느 알고리즘 버전에서 왔는지.
                # 전체 metric 은 qc_scores/job_events 에 있고, 여기는 재현에 필요한 최소만.
                md["generationPath"] = "hybrid_stripe_composite"
                md["hybridComposite"] = {
                    "versions": hc.get("versions"),
                    "sourceAssets": hc.get("sourceAssets"),
                    "targetPeriodPx": hc.get("targetPeriodPx"),
                    "sourceCoverage": hc.get("sourceCoverage"),
                    "outputSha256": hc.get("outputSha256"),
                    "carrierSha256": hc.get("carrierSha256"),
                    "componentsNeedingReview": hc.get("componentsNeedingReview", []),
                }
            candidate_result["generation_metadata"] = md

        # 4) 성공 종결 (원자·lease 펜스). charge = reserved — 예약 시점 견적을 그대로 확정한다
        # (단일컷 전환으로 구 "성공 후보 수 × 1" 폐기. 실행 시점 설정값을 다시 읽으면 배포/env 변경
        # 사이에 낀 잡이 예약액과 다른 금액을 차감하거나 settle 실패할 수 있음). 실패는 _fail(release).
        charge = reserved
        async with pool.connection() as conn:
            out = await repo.finalize_mannequin_success(
                conn, job_id=job_id, lease_token=lease_token, user_id=user_id,
                project_id=project_id, candidates=passed, reserved=reserved, charge=charge,
                metadata={"creditCostVersion": s.credit_cost_version,
                          "promptVersion": cut_generation_metadata["promptVersion"],
                          "gender": gender})
            await conn.commit()
        if out is None:  # lease 상실(복구) → 결과 폐기 + 방금 저장한 R2 객체 best-effort 정리
            await _delete_uploaded_candidate_keys(app.state.r2, passed)
    except Exception as e:  # 예기치 못한 오류도 lease 펜스 종결로
        await _fail("생성 중 오류가 발생했어요. 다시 시도해 주세요.", {"error": str(e)[:300]})
