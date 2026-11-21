"""끝에서 옷 색 맞추기. 한 색상 옵션의 최종 사진 묶음에서 옷 색이 크게 튄 사진만 옷 화소를 옮겨 맞춘다.

배열을 받아 배열과 기록을 돌려주는 순수 함수 묶음이다. 윤곽을 따거나 저장하지 않는다.
서비스가 다른 색상 결과용 진입점을 생성 파이프라인에 연결한다. 안전망이므로 대부분의 사진이 바이트 그대로 돌아오는 것이 정상이다.
주석의 절 번호는 색 맞추기 모듈 알고리즘 명세(2026-11-12)의 것이다.
지금 상태의 명세는 `documents/garment_color_consistency_spec.md`이고, 주석 절 번호와의 대응은 그 문서 맨 위에 있다.
합성 화소 테스트는 규칙대로 도는지만 확인한다.
기본값은 2026-11-13에 오너가 지금 값 유지로 확정했다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

import cv2
import numpy as np

from . import tone_mask

ALGORITHM_VERSION = "garment-color-consistency-v1"

PHOTO_KINDS = ("studio", "product", "styling")

# ── 기본값. 설정 스위치가 아니다. 값을 바꾸려면 여기 한 곳만 고친다 ──────────────

# 길이는 모두 폭 1536 기준 화소다(1.1절). 사진 폭이 다르면 그 비율만큼 늘리거나 줄인다.
REFERENCE_WIDTH = 1536
# 가중 거리의 밝기 가중. 운영 garment_color_evidence._color_distance 와 같은 식이어야 한다.
LIGHTNESS_DISTANCE_WEIGHT = 0.35

# 윤곽 후보
# 화면의 2% 미만인 후보는 버린다. 운영 tone_mask.plausible 과 같고 표본 최소가 0.028이었다.
CANDIDATE_MIN_AREA_FRAC = 0.02
# 겹침이 이 이상이면 같은 후보다. 운영 tone_mask.DEDUPE_IOU 와 같다(같은 후보 0.993 이상, 다른 후보 0.001 이하).
CANDIDATE_DEDUPE_IOU = 0.90
# 가장 가까운 후보와 둘째의 거리 차가 이보다 작으면 모호하다. 85쌍에서 틀림 0, 틀린 한 쌍의 차이는 0.0011.
CANDIDATE_MARGIN = 0.02
# 올린 옷 색과 후보 1개짜리 사진들의 거리 가운데값이 이를 넘으면 올린 색을 믿지 않는다. 9벌 중 2벌이 넘음. 임시값.
UPLOAD_TRUST_MAX = 0.07

# 윤곽 다듬기
# 윤곽 넓이의 0.05% 미만인 조각만 지운다. 0.05%에서 2% 사이 조각 19개는 모두 진짜 옷이었다.
SPECK_MAX_SHARE = 0.0005

# 윤곽 품질
# 화면의 2% 미만이면 건너뛴다. 운영과 같다.
MASK_AREA_MIN = 0.02
# 화면의 75%를 넘으면 사진 종류와 상관없이 건너뛴다. 근접 9개 중 3개에 문제가 있었고 검사가 못 잡는다.
MASK_AREA_MAX = 0.75
# 침식한 속의 비율과 화소 수(폭 1536 환산) 하한. 표본 최소 0.905와 89,627. 실패 표본이 없는 임시값.
CORE_SHARE_MIN = 0.85
CORE_PX_1536_MIN = 40_000
# 윤곽 넓이의 1% 이상인 덩어리가 3개를 넘으면 건너뛴다. 표본 최대 2. 임시값.
FRAGMENT_MIN_SHARE = 0.01
FRAGMENTS_MAX = 3
# 색을 잴 속 화소와 가장자리 띠 화소의 최소 수. 참고 구현의 값.
QUALITY_MIN_PIXELS = 200
# 속 가운데 색에서 가중 거리가 이보다 먼 화소를 먼 화소로 센다. 품질 숫자의 정의.
FAR_DISTANCE = 0.06
# 먼 화소 비율과 거리 90번째 백분위의 상한. 표본 최대 0.354와 0.1215.
FAR_SHARE_MAX = 0.42
W_SPREAD90_MAX = 0.14
# 가장자리 띠의 먼 화소 비율에서 속 전체의 비율을 뺀 값의 상한. 표본 최대 0.296.
RIM_EXCESS_MAX = 0.33

# 형제 사진과의 대조
# 형제가 2장 미만이면 고치지 않는다. 품질 검사만으로는 틀린 윤곽 7개 중 3개만 걸렸다. 임시값.
SIBLINGS_MIN = 2
# 형제 가운데 색과 밝기, 색 방향이 이보다 멀면 다른 옷을 고른 윤곽으로 본다. 틀린 7개 중 6개가 걸렸다.
SIBLING_LIGHTNESS_MAX = 0.20
SIBLING_CHROMA_MAX = 0.05
# 형제 가운데값보다 품질 숫자가 이만큼 넘게 나쁘면 건너뛴다. 표본 최대 0.243, 0.060, 0.019, 0.298.
SIBLING_FAR_SHARE_MAX = 0.28
SIBLING_W_SPREAD90_MAX = 0.07
SIBLING_AB_SPREAD90_MAX = 0.022
SIBLING_RIM_FAR_MAX = 0.33

# 옷 색 재기와 여러 색 옷 판정
# 윤곽을 9화소(최소 5) 깎은 속에서 잰다. 깎는 폭을 1에서 41로 바꿔도 흔들림이 사진 사이 차이의 2%에서 22%였다.
ERODE_PX = 9
ERODE_MIN_PX = 5
# 같은 천 화소가 이보다 적으면 다듬은 가운데값을 쓰지 않고, 여러 색 옷으로 본다. 참고 구현의 값. 임시값.
REFINE_MIN_PIXELS = 200
# 같은 천 정도가 이 이상이면 같은 천으로 센다. 3.1절과 4.2절의 정의.
SAME_FABRIC_MIN = 0.5
# 판단용 범위. 색 방향 거리와 밝기 차이가 앞 값 이하면 같은 천, 뒤 값 이상이면 다른 천. 실제 한 벌 옷을 안 빼는 쪽.
JUDGE_CHROMA_RANGE = (0.04, 0.08)
JUDGE_LIGHTNESS_RANGE = (0.30, 0.45)
# 같은 천 비중이 이보다 낮으면 여러 색 옷이다. 실제 한 벌 옷 345장의 최소가 0.9203이었다.
SAME_SHARE_MIN = 0.90
# 같은 천 화소의 색 방향 가운데 폭(35번째에서 65번째 백분위)이 이보다 넓으면 여러 색 옷이다. 실제 최대 0.0192.
CENTER_WIDTH_PERCENTILES = (35, 65)
CENTER_WIDTH_CHROMA_MAX = 0.025
# 날아간 화소는 세 채널 모두 250 이상, 뭉개진 화소는 세 채널 모두 5 이하다.
BLOWN_MIN = 250
CRUSHED_MAX = 5

# 기준 색
# 스튜디오 사진이 3장 미만이면 기준을 잡지 않는다. 두 장으로는 튄 한 장을 못 가린다. 임시값.
STUDIO_MIN = 3
# 스튜디오 가운데 색에서 가중 거리가 이를 넘는 사진은 빼고 다시 잡는다. 7벌에서 빠진 사진은 없었다. 임시값.
STUDIO_OUTLIER_DISTANCE = 0.03
# 올린 옷 색과 색 방향 차이가 앞 값 이하면 그대로, 뒤 값 이하면 앞 값 자리까지 끌어오고, 넘으면 기준 없음. 임시값.
UPLOAD_CHROMA_KEEP = 0.02
UPLOAD_CHROMA_MAX = 0.05
# 대조할 때 올린 옷 색의 진하기 배율은 1배와 밝기 비율(스튜디오 가운데 밝기 / 올린 색 밝기) 사이에서 고른다. 그 밝기 비율의 범위. 임시값.
UPLOAD_CHROMA_SCALE_RANGE = (0.5, 2.0)

# 옮길 양. 허용 폭은 오너가 같아 보인다고 한 쌍 차이의 최댓값이다. 임시값.
SHIFT_LIMITS = {
    # 스튜디오끼리 11쌍의 최댓값(색 방향 0.0221, 밝기 0.0531). 밝기를 0.15 옮기면 색역 밖 화소가 뛰어 한도는 0.10.
    "studio": {"chroma_tolerance": 0.022, "lightness_tolerance": 0.053,
               "chroma_cap": 0.02, "lightness_cap": 0.10},
    # 스튜디오와 장면 5쌍의 최댓값(색 방향 0.0350, 밝기 0.1316). 장면 밝기 한도 0.08은 설계 2차의 값.
    "styling": {"chroma_tolerance": 0.035, "lightness_tolerance": 0.13,
                "chroma_cap": 0.02, "lightness_cap": 0.08},
    # 오너가 본 쌍에 제품 사진이 없어 장면과 같은 넓은 폭을 쓴다. 색 방향 한도 0.02는 0.04에서 흠 다섯 가지가 보여서다.
    "product": {"chroma_tolerance": 0.035, "lightness_tolerance": 0.13,
                "chroma_cap": 0.02, "lightness_cap": 0.10},
}
# 세기. 0이면 허용 폭 끝까지만 되돌린다. 오너가 세기를 판단하기 어렵다고 해서 가장 덜 건드리는 쪽. 임시값.
STRENGTH_LAMBDA = 0.0
# 이보다 작은 이동은 재는 오차와 구별되지 않아 옮기지 않는다. 임시값.
MIN_SHIFT = 0.003
# 이미 날아간 화소가 이 비율을 넘으면 밝기를 건드리지 않는다. 근거 사진 2장(2.6%, 1.7%). 임시값.
BLOWN_SKIP_FRAC = 0.01
# 옷 가운데 밝기가 이 범위 안일 때만 밝기를 옮긴다. 밖에서는 식의 분모가 0에 가까워진다. 임시값.
LIGHTNESS_RANGE = (0.05, 0.97)
# 밝힐 때 옷 가운데 밝기는 여기까지만 간다. 검증 코드의 값. 임시값.
BRIGHTEN_CEILING = 0.99
# 날아간 화소와 뭉개진 화소는 1%p까지만 늘어도 된다. 새 193장에서도 통했다.
CLIP_GROWTH_MAX = 0.01
# 한도를 넘으면 절반으로 줄이지 않고 이분법으로 한도까지 연속으로 줄인다. 절반 방식은 37장 중 10장에서 급변했다.
CLIP_GUARD_ITERATIONS = 12

# 화소 적용
# 적용용 범위. 색 방향은 판단용과 같고, 밝기는 차이가 아니라 가운데 밝기에 대한 비율로 잰다.
APPLY_CHROMA_RANGE = (0.04, 0.08)
# 어두운 쪽: 비율 0.45 이상은 다 옮기고 0.30 이하는 그대로 둔다. 깊은 그늘이 빠지는 것은 검정 바지 1장에서만 보였다.
APPLY_DARK_RATIO = (0.30, 0.45)
# 밝은 쪽: 비율 2.2 이하는 다 옮기고 3.0 이상은 그대로 둔다. 워싱 2.6, 은색 단추 2.18에서 3.27. 근거가 얇은 임시값.
APPLY_LIGHT_RATIO = (2.2, 3.0)
# 윤곽 안쪽 2화소에 걸쳐 가중치가 0에서 1로 오르고, 가중치는 1화소 흐린다. 윤곽 밖 변화는 2,186번 적용에서 0이었다.
FEATHER_PX = 2.0
WEIGHT_BLUR_PX = 1.0
# 8비트 반 단계까지는 색역 넘침으로 보지 않는다. 그보다 크면 같은 밝기에서 진하기를 이분법으로 줄인다.
GAMUT_TOLERANCE_8BIT = 0.5
GAMUT_ITERATIONS = 12

# 적용 뒤 자기 검사
# 다시 잰 색이 예상 색에서 옮긴 양의 15%, 최소 0.003 안이어야 한다. 색역에 안 걸린 1,091건의 간 비율 최소가 0.879. 임시값.
RECHECK_RATIO = 0.15
RECHECK_FLOOR = 0.003

# 다른 색상 AI 결과 전용. 같은 색상 묶음의 SHIFT_LIMITS와 분리한다.
TARGET_ALGORITHM_VERSION = "garment-target-color-v2"
# recolor-probe-20261113/tune 실제 13장: 최대 오차 0.032, 이동 0.04까지 흠 없음. 0.06부터 경계와 피부, 단추 번짐.
TARGET_CHROMA_CAP = 0.04
# 실제 13장 재확인(11/15): 운영 방식으로 재면 0.043, 0.049가 나온다. 한도를 넘어도 한도만큼은 옮긴다.
# 이 값을 넘는 오차는 윤곽이나 AI 결과가 틀렸을 가능성이 커서 조금도 옮기지 않는다.
TARGET_CHROMA_GIVE_UP = 0.08
# 시험하지 않은 임시값. 이보다 작은 오차는 건드리지 않는다.
TARGET_CHROMA_TOLERANCE = 0.01
# 거의 무채색인 목표는 색 방향을 맞추지 않는다(2026-11-15 운영 계약).
TARGET_ACHROMATIC_CHROMA = 0.02
# 목표 Front의 조명 밝기를 착용컷에 옮기지 않는다. 색 방향은 허용 폭을 빼지 않고 목표까지 옮긴다.
TARGET_SHIFT_LIMITS = {"lightness_tolerance": 0.0, "lightness_cap": 0.0,
                       "chroma_tolerance": 0.0, "chroma_cap": TARGET_CHROMA_CAP}


def match_to_target_color(image: np.ndarray, mask: np.ndarray, target_ab) -> tuple[np.ndarray, dict]:
    """다른 색상 AI 결과의 색 방향만 목표에 맞춘다. 건너뛰거나 실패하면 입력 배열 그대로다."""
    _check_image(image)
    _check_mask(mask, image.shape[:2])
    target_ab = np.asarray(target_ab, dtype=np.float64)
    if target_ab.shape != (2,) or not np.isfinite(target_ab).all():
        raise ValueError("target_ab must be two finite numbers (a, b)")
    record = {"status": "skipped", "reason": None, "measure": None,
              "target_ab": _floats(target_ab), "needed_shift": None,
              "actual_shift": {"d_l": 0.0, "d_ab": [0.0, 0.0]},
              "mask": None, "plan": None, "result": None,
              "algorithm_version": TARGET_ALGORITHM_VERSION}
    try:
        mask, removed, removed_pixels = trim_specks(mask)
        region = _region(image, mask)
        quality = _quality(region, mask.size)
        codes = judge_mask_quality(quality)
        record["mask"] = {"quality": quality, "quality_codes": codes,
                          "removed_fragments": removed, "removed_pixels": removed_pixels}
        if codes:
            record["reason"] = codes[0]
            return image, record
        measure = _measure(region)
        record["measure"] = measure
        center = np.array(measure["center"])
        delta = target_ab - center[1:]
        distance = float(np.hypot(*delta))
        record["needed_shift"] = {"d_l": 0.0, "d_ab": _floats(delta), "distance_ab": distance}
        if float(np.hypot(*target_ab)) < TARGET_ACHROMATIC_CHROMA:
            record.update(status="untouched", reason="target_achromatic")
        elif distance > TARGET_CHROMA_GIVE_UP:
            record["reason"] = "over_cap"
        elif distance < TARGET_CHROMA_TOLERANCE:
            record.update(status="untouched", reason="within_tolerance")
        else:
            target = np.array([center[0], *target_ab])
            weights = _weights(region, center)
            plan = plan_shift(center, target, "studio", blown_frac=measure["blown_frac"],
                              grow=lambda d_l, d_ab: _growth(region, weights, center, d_l, d_ab),
                              limits=TARGET_SHIFT_LIMITS)
            record["plan"] = plan
            if plan["reason"]:
                record.update(status="untouched", reason=plan["reason"])
                return image, record
            pixels, reduced = _shift(region, weights, center, plan["d_l"], plan["d_ab"])
            result = _paste(image, region, pixels)
            check = self_check(image, result, mask, center, plan["d_l"], plan["d_ab"])
            record["result"] = {**check, **_shift_stats(region, pixels, reduced)}
            if check["reason"]:
                record.update(status="reverted", reason=check["reason"])
            else:
                moved = np.array(check["remeasured_center"]) - center
                record.update(status="corrected", actual_shift={"d_l": float(moved[0]),
                                                               "d_ab": _floats(moved[1:])})
                return result, record
    except Exception:
        # 입력 검증은 위에서 끝났다. 계산 실패는 컷을 막지 않는다.
        record.update(status="skipped", reason="calculation_failed")
    return image, record


# ── 색상 옵션 한 묶음 ───────────────────────────────────────────────────────

@dataclass(frozen=True, eq=False)
class PhotoInput:
    """사진 한 장. image 는 이 모듈이 고친 적 없는 원본이어야 한다."""
    key: str
    image: np.ndarray                       # uint8 (H, W, 3) sRGB
    masks: np.ndarray | Sequence[np.ndarray]  # bool (H, W). 후보가 여럿이면 목록, 없으면 빈 목록
    kind: str                               # studio, product, styling
    color_option_id: str
    clothing_type: str | None = None
    sub_category: str | None = None


@dataclass(frozen=True, eq=False)
class PhotoResult:
    image: np.ndarray       # 고치지 않은 사진은 입력 배열을 그대로 돌려준다
    record: dict


@dataclass(frozen=True, eq=False)
class BatchResult:
    photos: list[PhotoResult]   # 입력과 같은 순서
    record: dict


def correct_color_option(photos: Sequence[PhotoInput], *, upload_color=None,
                         seller_color=None) -> BatchResult:
    """한 색상 옵션의 사진 묶음을 받아 사진별 결과와 묶음 기록을 돌려준다.

    upload_color 는 코디보드가 낸 올린 옷 사진의 색 (L, a, b), seller_color 는 셀러가 조절한 색 (L, a, b)이다.
    어느 단계에서든 조건에 걸린 사진은 원본을 그대로 돌려주고 이유 코드를 남긴다. 예외는 입력이 틀렸을 때만 던진다.
    같은 입력에는 같은 출력이 나온다.
    """
    works = _open_batch(list(photos))
    upload = None if upload_color is None else _input_color(upload_color, "upload_color")
    seller = None if seller_color is None else _input_color(seller_color, "seller_color")

    # 1. 윤곽 정하기. 후보가 하나인 사진부터 재야 올린 옷 색을 믿을지 알 수 있다.
    undecided = []
    for work in works:
        kept = reduce_candidates(work.candidates)
        if not kept:
            _skip(work, "no_candidate")
        elif len(kept) == 1:
            work.record["mask"]["selection"] = "single"
            _settle(work, work.candidates[kept[0]])
        else:
            undecided.append((work, [work.candidates[i] for i in kept]))
    trust = {"median_distance": None, "trusted": None}
    if upload is not None:
        trust = upload_color_trust([w.center for w in works if w.center is not None], upload)
    for work, candidates in undecided:
        if upload is None:
            _skip(work, "ambiguous_no_upload_color")
        elif not trust["trusted"]:
            _skip(work, "upload_color_untrusted")
        else:
            pick = pick_by_upload_color(work.photo.image, candidates, upload)
            work.record["mask"]["candidate_distances"] = pick["distances"]
            if pick["reason"]:
                _skip(work, pick["reason"])
            else:
                work.record["mask"]["selection"] = "closest_to_upload"
                _settle(work, candidates[pick["index"]])

    # 2. 형제 사진과 대조. 형제는 품질 검사를 통과한 다른 사진이다.
    measured = [w for w in works if w.center is not None]
    for work in measured:
        siblings = [w for w in measured if w is not work]
        check = check_against_siblings(work.quality, work.center, [w.quality for w in siblings],
                                       [w.center for w in siblings])
        work.record["measure"].update(sibling_count=check["sibling_count"], sibling_d_l=check["d_l"],
                                      sibling_d_ab=check["d_ab"])
        work.record["mask"]["quality_codes"] += check["codes"]
        if check["reason"]:
            _skip(work, check["reason"], "redraw_candidate" if check["reason"] == "off_siblings" else None)
    eligible = [w for w in measured if w.record["status"] is None]

    # 3. 여러 색 옷과 기준 색. 한 장이라도 여러 색이면 이 색상 옵션은 기준 색을 잡지 않는다.
    # 섞인 색 코드로 먼저 건너뛴 사진도 여러 색으로 센다. 두 색 옷의 반반 사진이 형제 대조에서 빠지면 묶음 판정이 꺼져서
    # 다른 색 사진이 옮겨졌다. 그 사진 자신은 먼저 걸린 이유를 그대로 갖는다.
    mixed_codes = {"mask_mixed_color", "mask_mixed_vs_siblings"}
    multicolor_keys = [w.photo.key for w in works
                       if (w in eligible and w.multicolor)
                       or mixed_codes & set(w.record["mask"]["quality_codes"])]
    if multicolor_keys:
        reference = _no_reference("multicolor_garment")
    else:
        reference = resolve_reference([(w.photo.key, w.photo.kind, w.center) for w in eligible],
                                      upload_color=upload, seller_color=seller)

    # 4. 사진마다 옮길 양을 정하고, 옮길 것이 있는 사진만 옮긴 뒤 자기 검사를 한다. 기준이 없으면 모두 건너뛴다.
    for work in eligible:
        if reference["color"] is None:
            _skip(work, reference["reason"], "redraw_candidate" if reference["redraw_candidate"] else None)
        else:
            _correct(work, np.array(reference["color"]))

    counts = {status: 0 for status in ("corrected", "untouched", "skipped", "reverted")}
    for work in works:
        counts[work.record["status"]] += 1
    record = {
        "color_option_id": works[0].photo.color_option_id, "photo_count": len(works),
        "reference_source": reference["source"], "reference_color": reference["color"],
        "reference_reason": reference["reason"], "redraw_candidate": reference["redraw_candidate"],
        "studio": {"used": reference["studio_used"], "dropped_keys": reference["studio_dropped_keys"],
                   "center": reference["studio_center"]},
        "upload_check": {"d_ab": reference["upload_d_ab"], "pulled": reference["pulled"],
                         "l_diff": reference["upload_l_diff"]},
        "upload_trust": trust,
        "multicolor": {"flagged": bool(multicolor_keys), "keys": multicolor_keys},
        "status_counts": counts,
        "algorithm_version": ALGORITHM_VERSION,
    }
    return BatchResult([PhotoResult(work.image, work.record) for work in works], record)


@dataclass(eq=False)
class _Work:
    photo: PhotoInput
    candidates: list[np.ndarray]
    record: dict
    image: np.ndarray
    mask: np.ndarray | None = None
    quality: dict | None = None
    center: np.ndarray | None = None        # 품질 검사를 통과해야 생긴다
    multicolor: bool = False


def _skip(work: _Work, reason: str, flag: str | None = None) -> None:
    work.record.update(status="skipped", reason=reason)
    if flag:
        work.record["flags"].append(flag)


def _settle(work: _Work, candidate: np.ndarray) -> None:
    """윤곽을 다듬고 품질을 검사한 뒤, 통과하면 옷 색을 잰다."""
    mask, removed, removed_pixels = trim_specks(candidate)
    region = _region(work.photo.image, mask)
    quality = _quality(region, mask.size)
    codes = judge_mask_quality(quality)
    work.mask, work.quality = mask, quality
    work.record["mask"].update(removed_fragments=removed, removed_pixels=removed_pixels,
                               quality=quality, quality_codes=codes)
    if codes:
        _skip(work, codes[0])
        return
    measure = _measure(region)
    work.center = np.array(measure["center"])
    work.multicolor = measure.pop("multicolor")
    work.record["measure"] = {**measure, "sibling_count": None, "sibling_d_l": None, "sibling_d_ab": None}


def _open_batch(photos: Sequence[PhotoInput]) -> list[_Work]:
    if not photos:
        raise ValueError("photos is empty")
    option_ids = {photo.color_option_id for photo in photos}
    if len(option_ids) != 1:
        raise ValueError("every photo in one call must share the same color_option_id")
    if len({photo.key for photo in photos}) != len(photos):
        raise ValueError("photo keys must be unique")
    works = []
    for photo in photos:
        _check_image(photo.image)
        if photo.kind not in PHOTO_KINDS:
            raise ValueError(f"unknown photo kind {photo.kind!r}")
        candidates = [photo.masks] if isinstance(photo.masks, np.ndarray) else list(photo.masks)
        for candidate in candidates:
            _check_mask(candidate, photo.image.shape[:2])
        works.append(_Work(photo, candidates, image=photo.image, record={
            "key": photo.key, "color_option_id": photo.color_option_id, "kind": photo.kind,
            "clothing_type": photo.clothing_type, "sub_category": photo.sub_category,
            "status": None, "reason": None, "flags": [],
            "mask": {"candidates_received": len(candidates), "selection": None, "candidate_distances": None,
                     "removed_fragments": None, "removed_pixels": None, "quality": None, "quality_codes": []},
            "measure": None, "plan": None, "result": None,
            "algorithm_version": ALGORITHM_VERSION,
        }))
    return works


def _correct(work: _Work, target: np.ndarray) -> None:
    center = work.center
    region = weights = None

    def region_and_weights():
        # 측정에 쓴 영역은 보관하지 않는다. 한도 재기나 실제 적용이 필요할 때 이 사진의 배열만 다시 만든다.
        nonlocal region, weights
        if region is None:
            region = _region(work.photo.image, work.mask)
            weights = _weights(region, center)
        return region, weights

    plan = plan_shift(center, target, work.photo.kind, blown_frac=work.record["measure"]["blown_frac"],
                      grow=lambda d_l, d_ab: _growth(*region_and_weights(), center, d_l, d_ab))
    work.record["flags"] += plan.pop("flags")
    reason = plan.pop("reason")
    work.record["plan"] = plan
    if reason:
        work.record.update(status="untouched", reason=reason)
        return
    region, weights = region_and_weights()
    pixels, reduced = _shift(region, weights, center, plan["d_l"], plan["d_ab"])
    result = _paste(work.photo.image, region, pixels)
    check = self_check(work.photo.image, result, work.mask, center, plan["d_l"], plan["d_ab"])
    reason = check.pop("reason")
    work.record["result"] = {**check, **_shift_stats(region, pixels, reduced)}
    if reason:
        work.record.update(status="reverted", reason=reason)
    else:
        work.record["status"] = "corrected"
        work.image = result


# ── 색 공간 (운영 garment_color_evidence._lab 과 같은 상수) ─────────────────────

_RGB_TO_LMS = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                        [0.2119034982, 0.6806995451, 0.1073969566],
                        [0.0883024619, 0.2817188376, 0.6299787005]])
_LMS_TO_LAB = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                        [1.9779984951, -2.4285922050, 0.4505937099],
                        [0.0259040371, 0.7827717662, -0.8086757660]])
_LMS_TO_RGB = np.linalg.inv(_RGB_TO_LMS)
_LAB_TO_LMS = np.linalg.inv(_LMS_TO_LAB)
_BYTE = np.arange(256) / 255.0
_SRGB_DECODE = np.where(_BYTE <= 0.04045, _BYTE / 12.92, ((_BYTE + 0.055) / 1.055) ** 2.4)


def rgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    """sRGB uint8 (..., 3)을 Oklab float64 (..., 3)으로 바꾼다."""
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.shape[-1] != 3:
        raise ValueError("rgb must be a uint8 array whose last axis is 3")
    return linear_to_oklab(_SRGB_DECODE[rgb])


def linear_to_oklab(rgb: np.ndarray) -> np.ndarray:
    return np.cbrt(rgb @ _RGB_TO_LMS.T) @ _LMS_TO_LAB.T


def _oklab_to_linear(lab: np.ndarray) -> np.ndarray:
    return ((lab @ _LAB_TO_LMS.T) ** 3) @ _LMS_TO_RGB.T


def _encode_srgb(linear: np.ndarray) -> np.ndarray:
    """선형광을 sRGB 실수(0에서 255)로. 범위 밖 값은 자르지 않고 부호를 살려 잇는다."""
    size = np.abs(linear)
    return np.sign(linear) * np.where(size <= 0.0031308, 12.92 * size,
                                      1.055 * size ** (1 / 2.4) - 0.055) * 255.0


def oklab_to_srgb(lab) -> np.ndarray:
    """Oklab (..., 3)을 sRGB 실수(0에서 255로 자름)로 바꾼다."""
    return np.clip(_encode_srgb(_oklab_to_linear(np.asarray(lab, dtype=np.float64))), 0.0, 255.0)


def color_distance(a, b) -> np.ndarray:
    """가중 거리. 밝기 차이에 0.35를 곱한다(운영 _color_distance 와 같은 식)."""
    d = np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)
    return np.sqrt((LIGHTNESS_DISTANCE_WEIGHT * d[..., 0]) ** 2 + d[..., 1] ** 2 + d[..., 2] ** 2)


def chroma_distance(a, b) -> np.ndarray:
    """색 방향 차이. 두 색의 (a, b) 사이 거리."""
    d = np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)
    return np.hypot(d[..., 1], d[..., 2])


# ── 입력 검증 (이때만 예외) ──────────────────────────────────────────────────

def _check_image(image) -> None:
    # 높이나 폭이 0이면 cv2 가 예외 없이 프로세스를 죽인다. 여기서 막는다.
    if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
            or image.ndim != 3 or image.shape[2] != 3 or image.size == 0):
        raise ValueError("image must be a non-empty uint8 array shaped (H, W, 3)")


def _check_mask(mask, shape: tuple[int, int] | None = None) -> None:
    if not isinstance(mask, np.ndarray) or mask.dtype != np.bool_ or mask.ndim != 2 or mask.size == 0:
        raise ValueError("mask must be a non-empty bool array shaped (H, W)")
    if shape is not None and mask.shape != shape:
        raise ValueError(f"mask shape {mask.shape} does not match image shape {shape}")


def _color3(value, name: str) -> np.ndarray:
    color = np.asarray(value, dtype=np.float64)
    if color.shape != (3,) or not np.isfinite(color).all():
        raise ValueError(f"{name} must be three finite numbers (L, a, b)")
    return color


def _input_color(value, name: str) -> np.ndarray:
    """묶음 함수가 받는 올린 옷 색과 셀러 색. 밝기가 0 이상 1 이하가 아니면 대조와 기준에 쓸 수 없는 값이다. 순수 검정은 0이다."""
    color = _color3(value, name)
    if not 0.0 <= color[0] <= 1.0:
        raise ValueError(f"{name} lightness must be at least 0 and at most 1")
    return color


def _floats(values) -> list[float]:
    return [float(v) for v in values]


# ── 낱말 (2.1절) ────────────────────────────────────────────────────────────

def mask_prompts(clothing_type: str | None, sub_category: str | None = None) -> tuple[str, str]:
    """옷 종류에서 윤곽을 딸 낱말(종류 낱말, 예비 낱말). 운영 톤 마스크의 표를 그대로 쓴다. 보내지는 않는다."""
    return tone_mask.prompts_for(clothing_type, sub_category)


# ── 윤곽의 경계 상자 안에서 미리 구해 두는 것 ───────────────────────────────

@dataclass(frozen=True, eq=False)
class _Region:
    """화소 단위 배열은 모두 윤곽 안 화소 N개 기준이다. 계산을 경계 상자 안으로 좁혀 큰 사진에서도 빠르다."""
    rows: slice
    cols: slice
    mask: np.ndarray      # bool (h, w). 여백을 둔 경계 상자로 잘라 낸 윤곽
    scale: float          # 폭 비례
    kernel: int           # 속을 낼 때 깎는 네모의 변
    rgb: np.ndarray       # uint8 (N, 3)
    lab: np.ndarray       # float64 (N, 3)
    core: np.ndarray      # bool (N,). 깎은 속에 드는가


def _region(image: np.ndarray, mask: np.ndarray) -> _Region:
    height, width = mask.shape
    scale = width / REFERENCE_WIDTH
    kernel = max(ERODE_MIN_PX, round(ERODE_PX * scale))
    filled_rows = np.flatnonzero(mask.any(axis=1))
    filled_cols = np.flatnonzero(mask.any(axis=0))
    if filled_rows.size == 0:
        rows = cols = slice(0, 1)           # 빈 윤곽. 한 화소짜리 빈 상자로 둔다
    else:
        # 여백은 가장 큰 침식 네모의 반(1.5 * kernel), 가중치 흐림, 경계 거리보다 넓다. 상자 밖을 본 결과와 같아진다.
        pad = 2 * kernel + 2
        rows = slice(max(0, int(filled_rows[0]) - pad), min(height, int(filled_rows[-1]) + 1 + pad))
        cols = slice(max(0, int(filled_cols[0]) - pad), min(width, int(filled_cols[-1]) + 1 + pad))
    crop = mask[rows, cols]
    rgb = image[rows, cols][crop]
    core = cv2.erode(crop.astype(np.uint8), np.ones((kernel, kernel), np.uint8)).astype(bool)
    return _Region(rows, cols, crop, scale, kernel, rgb, rgb_to_oklab(rgb), core[crop])


def _clip_fracs(rgb: np.ndarray) -> tuple[float, float]:
    """날아간 화소 비율과 뭉개진 화소 비율. 분모는 받은 화소 전부다."""
    if rgb.shape[0] == 0:
        return 0.0, 0.0
    return (float((rgb.min(axis=1) >= BLOWN_MIN).mean()),
            float((rgb.max(axis=1) <= CRUSHED_MAX).mean()))


# ── 윤곽 후보 (2.2절) ────────────────────────────────────────────────────────

def reduce_candidates(masks: Sequence[np.ndarray]) -> list[int]:
    """화면의 2% 미만인 후보와 겹치는 후보를 버리고, 남은 후보의 번호를 큰 것부터 돌려준다."""
    areas = [int(m.sum()) for m in masks]
    kept: list[int] = []
    for index in sorted(range(len(masks)), key=lambda i: -areas[i]):
        mask = masks[index]
        if areas[index] < CANDIDATE_MIN_AREA_FRAC * mask.size:
            continue
        if all(_iou(mask, masks[other]) < CANDIDATE_DEDUPE_IOU for other in kept):
            kept.append(index)
    return kept


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    union = int(np.logical_or(a, b).sum())
    return int(np.logical_and(a, b).sum()) / union if union else 0.0


def pick_by_upload_color(image: np.ndarray, masks: Sequence[np.ndarray], upload_color) -> dict:
    """후보가 둘 이상일 때 올린 옷 색과 가장 가까운 후보를 고른다. 둘째와의 차가 작으면 고르지 않는다.

    후보의 색은 속 화소의 가운데값(한 번 구한 값)이다. 속이 비어 못 재는 후보의 거리는 None 이고 가장 먼 것으로 친다.
    """
    _check_image(image)
    upload = _color3(upload_color, "upload_color")
    distances: list[float | None] = []
    for mask in masks:
        _check_mask(mask, image.shape[:2])
        region = _region(image, mask)
        core = region.lab[region.core]
        distances.append(float(color_distance(np.median(core, axis=0), upload)) if core.shape[0] else None)
    ranked = sorted(range(len(masks)), key=lambda i: math.inf if distances[i] is None else distances[i])
    best, second = (math.inf if distances[i] is None else distances[i] for i in ranked[:2])
    if not second - best >= CANDIDATE_MARGIN:       # 둘 다 못 잰 경우(차가 nan)도 모호로 본다
        return {"index": None, "distances": distances, "reason": "ambiguous_candidates"}
    return {"index": ranked[0], "distances": distances, "reason": None}


def upload_color_trust(centers: Sequence, upload_color) -> dict:
    """올린 옷 색을 후보 고르기에 써도 되는가. 후보가 1개뿐이고 품질 검사를 통과한 사진들의 옷 색과 견준다."""
    upload = _color3(upload_color, "upload_color")
    if not len(centers):
        return {"median_distance": None, "trusted": False}
    median = float(np.median(color_distance(np.asarray(centers, dtype=np.float64), upload)))
    return {"median_distance": median, "trusted": median <= UPLOAD_TRUST_MAX}


# ── 윤곽 다듬기 (2.3절) ──────────────────────────────────────────────────────

def trim_specks(mask: np.ndarray) -> tuple[np.ndarray, int, int]:
    """점 같은 조각만 지운다. 돌려주는 것은 다듬은 윤곽, 지운 조각 수, 지운 화소 수.

    구멍을 메우지 않는다. 가장 큰 덩어리만 남기지도 않는다. 윤곽 안에 갇힌 빈 곳은 옷 위의 손이나 배경이었고,
    둘째 덩어리는 팔에 가린 몸통처럼 진짜 옷이었다.
    """
    _check_mask(mask)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    areas = stats[1:, cv2.CC_STAT_AREA]
    drop = areas < SPECK_MAX_SHARE * areas.sum()
    if not drop.any():
        return mask, 0, 0
    drop[int(np.argmax(areas))] = False         # 가장 큰 덩어리는 지우지 않는다
    keep = np.ones(count, dtype=bool)
    keep[1:] = ~drop
    return mask & keep[labels], int(drop.sum()), int(areas[drop].sum())


# ── 윤곽 품질 (2.4절, 2.5절) ─────────────────────────────────────────────────

def measure_mask_quality(image: np.ndarray, mask: np.ndarray) -> dict:
    """윤곽 품질 숫자를 잰다. 판정은 judge_mask_quality 가 한다. 색을 못 잰 숫자는 None 이다."""
    _check_image(image)
    _check_mask(mask, image.shape[:2])
    return _quality(_region(image, mask), mask.size)


def _quality(region: _Region, frame_pixels: int) -> dict:
    area = region.rgb.shape[0]
    _count, _labels, stats, _ = cv2.connectedComponentsWithStats(region.mask.astype(np.uint8), connectivity=8)
    core_pixels = int(region.core.sum())
    quality = {
        "area_frac": area / frame_pixels,
        "fragments": int((stats[1:, cv2.CC_STAT_AREA] >= FRAGMENT_MIN_SHARE * area).sum()) if area else 0,
        "core_share": core_pixels / area if area else 0.0,
        "core_pixels": core_pixels,
        "core_px_1536": core_pixels / region.scale ** 2,
        "far_share": None, "w_spread90": None, "ab_spread90": None, "rim_in_far": None, "rim_excess": None,
    }
    if core_pixels < QUALITY_MIN_PIXELS:
        return quality
    core = region.lab[region.core]
    median = np.median(core, axis=0)
    weighted = color_distance(core, median)
    quality["far_share"] = float((weighted > FAR_DISTANCE).mean())
    quality["w_spread90"] = float(np.percentile(weighted, 90))
    quality["ab_spread90"] = float(np.percentile(chroma_distance(core, median), 90))
    wide = 3 * region.kernel
    inside = cv2.erode(region.mask.astype(np.uint8), np.ones((wide, wide), np.uint8)).astype(bool)
    rim = region.lab[~inside[region.mask]]
    if rim.shape[0] >= QUALITY_MIN_PIXELS:
        quality["rim_in_far"] = float((color_distance(rim, median) > FAR_DISTANCE).mean())
        quality["rim_excess"] = quality["rim_in_far"] - quality["far_share"]
    return quality


def judge_mask_quality(quality: dict) -> list[str]:
    """걸린 품질 코드를 명세의 순서대로 돌려준다. 빈 목록이면 통과다."""
    codes = []
    if quality["area_frac"] < MASK_AREA_MIN:
        codes.append("mask_tiny")
    if quality["area_frac"] > MASK_AREA_MAX:
        codes.append("mask_frame_fill")
    if quality["core_share"] < CORE_SHARE_MIN or quality["core_px_1536"] < CORE_PX_1536_MIN:
        codes.append("mask_thin")
    if quality["fragments"] > FRAGMENTS_MAX:
        codes.append("mask_fragments")
    if quality["far_share"] is None:
        return codes + ["mask_too_few_pixels"]
    if quality["rim_excess"] is not None and quality["rim_excess"] > RIM_EXCESS_MAX:
        codes.append("mask_edge_spill")
    if quality["far_share"] > FAR_SHARE_MAX or quality["w_spread90"] > W_SPREAD90_MAX:
        codes.append("mask_mixed_color")
    return codes


def check_against_siblings(quality: dict, center, sibling_qualities: Sequence[dict],
                           sibling_centers: Sequence) -> dict:
    """같은 묶음에서 품질 검사를 통과한 다른 사진들과 견준다. reason 이 None 이면 통과다."""
    center = _color3(center, "center")
    out = {"sibling_count": len(sibling_centers), "d_l": None, "d_ab": None, "codes": [], "reason": None}
    if len(sibling_centers) < SIBLINGS_MIN:
        out["reason"] = "no_sibling_check"
        return out

    def worse(key: str, limit: float) -> bool:
        """형제들의 가운데값보다 limit 넘게 나쁜가. 못 잰 숫자는 견주지 않는다."""
        values = [q[key] for q in sibling_qualities if q[key] is not None]
        return quality[key] is not None and bool(values) and quality[key] - float(np.median(values)) > limit

    if (worse("far_share", SIBLING_FAR_SHARE_MAX) or worse("w_spread90", SIBLING_W_SPREAD90_MAX)
            or worse("ab_spread90", SIBLING_AB_SPREAD90_MAX)):
        out["codes"].append("mask_mixed_vs_siblings")
    if worse("rim_in_far", SIBLING_RIM_FAR_MAX):
        out["codes"].append("mask_edge_vs_siblings")
    siblings = np.median(np.asarray(sibling_centers, dtype=np.float64), axis=0)
    out["d_l"] = float(center[0] - siblings[0])
    out["d_ab"] = float(chroma_distance(center, siblings))
    if out["codes"]:
        out["reason"] = out["codes"][0]
    elif abs(out["d_l"]) > SIBLING_LIGHTNESS_MAX or out["d_ab"] > SIBLING_CHROMA_MAX:
        out["reason"] = "off_siblings"
    return out


# ── 옷 색 재기와 여러 색 옷 판정 (3절, 4절) ──────────────────────────────────

def _falloff(x, low: float, high: float):
    """low 이하에서 1, high 이상에서 0으로 매끄럽게 내려간다."""
    s = np.clip((x - low) / (high - low), 0.0, 1.0)
    return 1.0 - s * s * (3.0 - 2.0 * s)


def _shade_chroma_distance(lab: np.ndarray, center: np.ndarray, ratio: np.ndarray) -> np.ndarray:
    """그늘을 감안한 색 방향 거리. 가운데 색을 화소의 밝기 비율만큼 줄여서 견준다."""
    t = np.clip(ratio, 0.0, 1.5)
    return np.hypot(lab[:, 1] - t * center[1], lab[:, 2] - t * center[2])


def _same_fabric_judge(lab: np.ndarray, center: np.ndarray) -> np.ndarray:
    """판단용 같은 천 정도(0에서 1). 밝기는 차이로 잰다."""
    ratio = lab[:, 0] / max(center[0], 1e-6)
    return (_falloff(_shade_chroma_distance(lab, center, ratio), *JUDGE_CHROMA_RANGE)
            * _falloff(center[0] - lab[:, 0], *JUDGE_LIGHTNESS_RANGE)
            * _falloff(lab[:, 0] - center[0], *JUDGE_LIGHTNESS_RANGE))


def _same_fabric_apply(lab: np.ndarray, center: np.ndarray) -> np.ndarray:
    """적용용 같은 천 정도(0에서 1). 판단용보다 넓고 밝기는 비율로 잰다. 햇빛을 옆에서 받은 옷이 둘로 갈라지지 않게."""
    ratio = lab[:, 0] / max(center[0], 1e-6)
    return (_falloff(_shade_chroma_distance(lab, center, ratio), *APPLY_CHROMA_RANGE)
            * (1.0 - _falloff(ratio, *APPLY_DARK_RATIO))
            * _falloff(ratio, *APPLY_LIGHT_RATIO))


def measure_garment_color(image: np.ndarray, mask: np.ndarray) -> dict:
    """사진 한 장의 옷 가운데 색과 여러 색 옷 점수를 잰다. 윤곽은 다듬은 것을 넣는다.

    속 화소를 전부 쓴다. 일정 간격으로 뽑지 않는다(2화소 주기 무늬에서 가운데 밝기가 치우친다).
    날아간 화소도 빼지 않는다.
    """
    _check_image(image)
    _check_mask(mask, image.shape[:2])
    return _measure(_region(image, mask))


def _measure(region: _Region) -> dict:
    core = region.lab[region.core]
    if core.shape[0] == 0:
        raise ValueError("mask has no interior pixels to measure")
    first = np.median(core, axis=0)
    keep = _same_fabric_judge(core, first) >= SAME_FABRIC_MIN
    center = np.median(core[keep], axis=0) if int(keep.sum()) >= REFINE_MIN_PIXELS else first
    same = _same_fabric_judge(core, center) >= SAME_FABRIC_MIN
    fabric = core[same]
    width_chroma = width_lightness = None
    if fabric.shape[0] >= REFINE_MIN_PIXELS:
        t = np.clip(fabric[:, 0] / max(center[0], 1e-6), 0.0, 1.5)
        low, high = np.percentile(
            np.column_stack([fabric[:, 1] - t * center[1], fabric[:, 2] - t * center[2], fabric[:, 0]]),
            CENTER_WIDTH_PERCENTILES, axis=0)
        width_chroma = float(np.hypot(high[0] - low[0], high[1] - low[1]))
        width_lightness = float(high[2] - low[2])
    same_share = float(same.mean())
    blown, crushed = _clip_fracs(region.rgb)
    return {
        "center": _floats(center), "first_pass_center": _floats(first),
        "same": same_share, "cw_c": width_chroma, "cw_l": width_lightness,
        "multicolor": (width_chroma is None or same_share < SAME_SHARE_MIN
                       or width_chroma > CENTER_WIDTH_CHROMA_MAX),
        "blown_frac": blown, "crushed_frac": crushed,
    }


# ── 기준 색 (5절) ───────────────────────────────────────────────────────────

def resolve_reference(eligible: Sequence[tuple], *, upload_color=None, seller_color=None) -> dict:
    """색상 옵션의 기준 색을 정한다. eligible 은 윤곽 검사를 모두 통과한 사진의 (열쇠, 사진 종류, 옷 가운데 색)이다.

    셀러가 조절한 색이 있으면 그것이 절대 기준이다. 없으면 스튜디오 사진들의 가운데 색을 올린 옷 색과 대조해서 쓴다.
    제품 사진과 장면 사진은 기준에 쓰지 않는다. color 가 None 이면 reason 에 이유 코드가 있다.
    """
    out = _no_reference(None)
    if seller_color is not None:
        out.update(source="seller", color=_floats(_color3(seller_color, "seller_color")))
        return out
    studio = [(key, _color3(center, "center")) for key, kind, center in eligible if kind == "studio"]
    out["studio_used"] = len(studio)
    if len(studio) < STUDIO_MIN:
        out["reason"] = "no_reference_few_studio"
        return out
    first = np.median([center for _key, center in studio], axis=0)
    far = [bool(color_distance(center, first) > STUDIO_OUTLIER_DISTANCE) for _key, center in studio]
    agreeing = [center for (_key, center), is_far in zip(studio, far) if not is_far]
    out["studio_dropped_keys"] = [key for (key, _center), is_far in zip(studio, far) if is_far]
    out["studio_used"] = len(agreeing)
    if len(agreeing) < STUDIO_MIN:
        out["reason"] = "no_reference_studio_disagree"
        return out
    studio_center = np.median(agreeing, axis=0)
    out["studio_center"] = _floats(studio_center)
    if upload_color is None:
        out["reason"] = "no_reference_no_upload_color"
        return out
    upload = _color3(upload_color, "upload_color")
    # 밝게 찍힐수록 진하기가 밝기 비율만큼 커지는 옷도, 거의 그대로인 옷도 있었다(실제 12벌). 올린 색의 진하기 배율은
    # 1배와 밝기 비율 사이에서 스튜디오 가운데 색에 가장 가까운 값을 쓴다. 색상 각도 차이(수직 성분)는 배율로 줄지 않는다.
    ratio = float(np.clip(studio_center[0] / max(upload[0], 1e-6), *UPLOAD_CHROMA_SCALE_RANGE))
    upload_sq = float(upload[1:] @ upload[1:])
    fit = float(studio_center[1:] @ upload[1:]) / upload_sq if upload_sq >= 1e-12 else 1.0
    upload_ab = upload[1:] * float(np.clip(fit, min(1.0, ratio), max(1.0, ratio)))
    apart = float(np.hypot(*(studio_center[1:] - upload_ab)))
    out["upload_d_ab"] = apart
    # 밝기 차이는 기록만 한다. 올린 사진의 밝기는 찍은 조명을 따라가서(12벌에서 -0.054에서 +0.28) 기준 밝기를 고치지 않는다.
    out["upload_l_diff"] = float(studio_center[0] - upload[0])
    if apart > UPLOAD_CHROMA_MAX:
        out.update(reason="reference_off_upload", redraw_candidate=True)
        return out
    target = studio_center.copy()
    if apart > UPLOAD_CHROMA_KEEP:
        target[1:] = upload_ab + (studio_center[1:] - upload_ab) * UPLOAD_CHROMA_KEEP / apart
        out["pulled"] = True
    out.update(source="studio", color=_floats(target))
    return out


def _no_reference(reason: str | None) -> dict:
    return {"source": None, "color": None, "reason": reason, "redraw_candidate": False,
            "studio_used": 0, "studio_dropped_keys": [], "studio_center": None,
            "upload_d_ab": None, "pulled": False, "upload_l_diff": None}


# ── 옮길 양 (6절) ───────────────────────────────────────────────────────────

def _need(excess: float, tolerance: float) -> float:
    """옮겨야 할 양. 허용 폭 끝에서 0으로 시작하고 끊기지 않는다. 세기가 0이면 허용 폭 끝까지만 되돌린다."""
    if excess <= tolerance:
        return 0.0
    if tolerance == 0:
        return excess
    return excess - tolerance * math.exp(-STRENGTH_LAMBDA * (excess - tolerance) / tolerance)


def plan_shift(center, target, kind: str, *, blown_frac: float,
               grow: Callable[[float, Sequence[float]], float], limits: dict | None = None) -> dict:
    """사진 한 장을 얼마나 옮길지 정한다. 밝기와 색 방향을 따로 판단한다.

    grow(d_l, d_ab)는 밝기를 d_l, 색 방향을 d_ab 만큼 옮겼을 때 날아간 화소와 뭉개진 화소가 늘어나는 비율이다
    (shift_clip_growth). 밝기는 밝기만 옮긴 값으로 줄이고, 색 방향은 둘을 함께 옮긴 값으로 줄인다.
    limits를 생략하면 기존 사진 종류별 SHIFT_LIMITS를 쓴다.
    reason 이 None 이면 옮길 것이 있다. 아니면 옮기지 않는 까닭이다.
    """
    center = _color3(center, "center")
    target = _color3(target, "target")
    limits = SHIFT_LIMITS[kind] if limits is None else limits
    flags: list[str] = []

    error_l = float(center[0] - target[0])
    need_l = _need(abs(error_l), limits["lightness_tolerance"])
    d_l = -math.copysign(min(need_l, limits["lightness_cap"]), error_l) if need_l else 0.0
    if need_l > limits["lightness_cap"]:
        flags.append("capped_lightness")
    before_guard = None
    if d_l:
        if blown_frac > BLOWN_SKIP_FRAC:
            d_l = 0.0
            flags.append("lightness_skipped_blown")
        elif not LIGHTNESS_RANGE[0] < center[0] < LIGHTNESS_RANGE[1]:
            d_l = 0.0
            flags.append("lightness_skipped_extreme")
        else:
            if d_l > 0:
                d_l = min(d_l, BRIGHTEN_CEILING - float(center[0]))
            if abs(d_l) < MIN_SHIFT:
                d_l = 0.0
            elif grow(d_l, (0.0, 0.0)) > CLIP_GROWTH_MAX:
                before_guard = d_l
                safe, unsafe = 0.0, d_l
                for _ in range(CLIP_GUARD_ITERATIONS):
                    middle = (safe + unsafe) / 2
                    if grow(middle, (0.0, 0.0)) <= CLIP_GROWTH_MAX:
                        safe = middle
                    else:
                        unsafe = middle
                d_l = safe
                flags.append("clip_guard_reduced")
                if abs(d_l) < MIN_SHIFT or not d_l:
                    d_l = 0.0
                    flags.append("lightness_skipped_clip_guard")

    # 밝기를 옮기면 진하기가 같은 비율로 따라간다. 색 방향은 그 뒤의 예상 가운데 색에서 판단한다.
    gain = (center[0] + d_l) / center[0] if d_l else 1.0
    offset = gain * center[1:] - target[1:]
    error_c = float(np.hypot(offset[0], offset[1]))
    need_c = _need(error_c, limits["chroma_tolerance"])
    move_c = min(need_c, limits["chroma_cap"])
    if need_c > limits["chroma_cap"]:
        flags.append("capped_chroma")
    d_ab = _floats(0.0 - offset * (move_c / error_c)) if move_c > 0 and move_c >= MIN_SHIFT else [0.0, 0.0]

    # 한도는 실제로 적용할 이동 전체로 다시 잰다. 넘으면 밝기는 두고 색 방향에 곱할 비율을 한도까지 연속으로 줄인다.
    # 비율이 0이면 밝기만 옮기는 것이고, 그 증가는 위에서 이미 한도 안이다.
    ab_before_guard = None
    if any(d_ab) and grow(d_l, d_ab) > CLIP_GROWTH_MAX:
        ab_before_guard = d_ab
        safe, unsafe = 0.0, 1.0
        for _ in range(CLIP_GUARD_ITERATIONS):
            middle = (safe + unsafe) / 2
            if grow(d_l, [middle * v for v in ab_before_guard]) <= CLIP_GROWTH_MAX:
                safe = middle
            else:
                unsafe = middle
        d_ab = [safe * v for v in ab_before_guard] if safe * move_c >= MIN_SHIFT else [0.0, 0.0]
        if "clip_guard_reduced" not in flags:
            flags.append("clip_guard_reduced")

    reason = None
    if not d_l and d_ab == [0.0, 0.0]:
        blocked = [flag for flag in flags if flag.startswith("lightness_skipped_")]
        if blocked:
            reason = blocked[0]
        else:
            reason = "clip_guard_reduced" if ab_before_guard else "within_tolerance"
    return {
        "e_l": error_l, "e_c": error_c,
        "lightness_tolerance": limits["lightness_tolerance"], "chroma_tolerance": limits["chroma_tolerance"],
        "lightness_cap": limits["lightness_cap"], "chroma_cap": limits["chroma_cap"],
        "n_l": need_l, "n_c": need_c, "d_l": float(d_l), "d_ab": d_ab,
        "d_l_before_clip_guard": before_guard, "d_ab_before_clip_guard": ab_before_guard,
        "flags": flags, "reason": reason,
    }


def lightness_clip_growth(image: np.ndarray, mask: np.ndarray, center, d_l: float) -> float:
    """밝기만 d_l 옮겼을 때 날아간 화소 비율과 뭉개진 화소 비율이 원본보다 늘어나는 크기 중 큰 쪽.

    가중치, 색역 처리, 8비트 반올림을 실제 적용과 같게 한다. 분모는 윤곽 안 화소 전부다.
    """
    return shift_clip_growth(image, mask, center, d_l, (0.0, 0.0))


def shift_clip_growth(image: np.ndarray, mask: np.ndarray, center, d_l: float, d_ab) -> float:
    """밝기를 d_l, 색 방향을 d_ab 함께 옮겼을 때의 같은 크기. 자기 검사(8절 2번)가 결과 사진에서 재는 값과 같다."""
    _check_image(image)
    _check_mask(mask, image.shape[:2])
    center = _color3(center, "center")
    region = _region(image, mask)
    return _growth(region, _weights(region, center), center, d_l, d_ab)


def _growth(region: _Region, weights: np.ndarray, center: np.ndarray, d_l: float, d_ab) -> float:
    moved, _reduced = _shift(region, weights, center, d_l, d_ab)
    return _clip_growth(region.rgb, moved)


def _clip_growth(before: np.ndarray, after: np.ndarray) -> float:
    blown_before, crushed_before = _clip_fracs(before)
    blown_after, crushed_after = _clip_fracs(after)
    return max(blown_after - blown_before, crushed_after - crushed_before)


# ── 화소 적용 (7절) ─────────────────────────────────────────────────────────

def move_weight(image: np.ndarray, mask: np.ndarray, center) -> np.ndarray:
    """화소마다 얼마나 옮길지(0에서 1), 사진과 같은 크기. 윤곽 밖은 정확히 0이다.

    같은 천은 모두 1로 똑같이 옮기고 색이 다른 것만 그대로 둔다. 윤곽 안쪽 2화소에 걸쳐 0에서 1로 오른다.
    """
    _check_image(image)
    _check_mask(mask, image.shape[:2])
    region = _region(image, mask)
    full = np.zeros(mask.shape, dtype=np.float64)
    full[region.rows, region.cols][region.mask] = _weights(region, _color3(center, "center"))
    return full


def _weights(region: _Region, center: np.ndarray) -> np.ndarray:
    inside = region.mask
    fabric = np.zeros(inside.shape, dtype=np.float64)
    fabric[inside] = _same_fabric_apply(region.lab, center)
    sigma = WEIGHT_BLUR_PX * region.scale
    blurred = cv2.GaussianBlur(fabric, (0, 0), sigma)
    cover = cv2.GaussianBlur(inside.astype(np.float64), (0, 0), sigma)
    depth = cv2.distanceTransform(inside.astype(np.uint8), cv2.DIST_L2, 3)
    inner = np.clip(depth / max(1.0, FEATHER_PX * region.scale), 0.0, 1.0)
    return (inner * blurred / np.maximum(cover, 1e-6))[inside]


def lightness_ratio(lightness: np.ndarray, center_l: float, d_l: float) -> np.ndarray:
    """화소의 Oklab 세 좌표에 곱할 배율. 진하기가 밝기와 같은 비율로 따라간다.

    어둡게 할 때는 모든 화소에 같은 배율이다(선형광 이득). 밝게 할 때는 옷 가운데보다 어두운 화소는 이득 그대로,
    밝은 화소만 흰색 쪽으로 눌린다. 검은 글자와 그늘이 바탕보다 더 밝아지지 않는다. 밝히는 식은 임시값이다(오너 눈 판정 전).
    """
    if not 0.0 < center_l < 1.0 or center_l + d_l <= 0.0 or (d_l > 0 and center_l >= BRIGHTEN_CEILING):
        raise ValueError("center lightness cannot be shifted by this amount")
    if d_l <= 0:
        return np.full(lightness.shape, (center_l + d_l) / center_l)
    goal = min(center_l + d_l, BRIGHTEN_CEILING)
    gain = goal / center_l
    bend = gain * (1.0 - center_l) / (1.0 - goal)
    headroom = np.clip((1.0 - lightness) / (1.0 - center_l), 0.0, 1.0)
    pressed = 1.0 - (1.0 - goal) * headroom / (bend - (bend - 1.0) * headroom)
    return np.where(lightness <= center_l, gain, pressed / np.maximum(lightness, 1e-9))


def _gamut_excess(encoded: np.ndarray) -> np.ndarray:
    """sRGB 실수가 0에서 255 밖으로 나간 크기(8비트 단계). 0 이하면 색역 안이다."""
    return np.maximum(encoded.max(axis=-1) - 255.0, -encoded.min(axis=-1))


def fit_to_gamut(lab: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """색역 밖 화소를 같은 밝기에서 진하기만 줄여 색역 안으로 넣는다. 돌려주는 것은 고친 Oklab (N, 3)과 줄인 화소 표시.

    채널을 자르지 않으므로 색상각이 유지된다. 계단식으로 줄이지 않고 이분법으로 색역 안에 드는 가장 큰 배율을 찾는다.
    """
    return _fit(lab)[:2]


def _fit(lab: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """fit_to_gamut 의 결과에 그 색의 sRGB 실수(자르기 전)를 더해 돌려준다. 같은 변환을 두 번 하지 않으려고."""
    fitted = np.array(lab, dtype=np.float64)
    fitted[:, 0] = np.clip(fitted[:, 0], 0.0, 1.0)
    encoded = _encode_srgb(_oklab_to_linear(fitted))
    outside = _gamut_excess(encoded) > GAMUT_TOLERANCE_8BIT
    if outside.any():
        pixels = fitted[outside]
        low = np.zeros(pixels.shape[0])
        high = np.ones(pixels.shape[0])
        for _ in range(GAMUT_ITERATIONS):
            middle = (low + high) / 2
            trial = pixels.copy()
            trial[:, 1:] *= middle[:, None]
            fits = _gamut_excess(_encode_srgb(_oklab_to_linear(trial))) <= GAMUT_TOLERANCE_8BIT
            low = np.where(fits, middle, low)
            high = np.where(fits, high, middle)
        pixels[:, 1:] *= low[:, None]
        fitted[outside] = pixels
        encoded[outside] = _encode_srgb(_oklab_to_linear(pixels))
    return fitted, outside, encoded


def _shift(region: _Region, weights: np.ndarray, center: np.ndarray, d_l: float,
           d_ab) -> tuple[np.ndarray, int]:
    """윤곽 안 화소 N개의 새 값(uint8)과 색역 처리로 진하기를 줄인 화소 수."""
    selected = weights > 0
    lab = region.lab[selected]
    lightness = lab[:, 0]
    moved = lab * lightness_ratio(lightness, float(center[0]), d_l)[:, None] if d_l else lab.copy()
    # 색 방향은 화소 밝기에 비례해 옮긴다. 그늘은 덜, 바탕보다 밝은 곳은 바탕만큼. 임시값(연구에서 눈으로 3장만 봤다).
    follow = np.clip(lightness / max(center[0], 1e-6), 0.0, 1.0)
    moved[:, 1] += follow * d_ab[0]
    moved[:, 2] += follow * d_ab[1]
    _fitted, reduced, encoded = _fit(moved)
    weight = weights[selected][:, None]
    out = region.rgb.copy()
    out[selected] = np.clip(np.rint(region.rgb[selected] * (1.0 - weight)
                                    + np.clip(encoded, 0.0, 255.0) * weight), 0, 255).astype(np.uint8)
    return out, int(reduced.sum())


def _paste(image: np.ndarray, region: _Region, pixels: np.ndarray) -> np.ndarray:
    result = image.copy()
    result[region.rows, region.cols][region.mask] = pixels
    return result


def apply_shift(image: np.ndarray, mask: np.ndarray, center, d_l: float, d_ab) -> tuple[np.ndarray, dict]:
    """옷 화소만 옮긴 새 사진과 적용 숫자를 돌려준다. 원본은 바꾸지 않는다. 한 사진에 한 번만 적용한다.

    화소를 목표 색으로 칠하지 않는다. 옷 전체를 같은 규칙으로 옮긴다. 윤곽 밖은 한 화소도 바꾸지 않는다.
    """
    _check_image(image)
    _check_mask(mask, image.shape[:2])
    center = _color3(center, "center")
    region = _region(image, mask)
    pixels, reduced = _shift(region, _weights(region, center), center, d_l, d_ab)
    return _paste(image, region, pixels), _shift_stats(region, pixels, reduced)


def _shift_stats(region: _Region, pixels: np.ndarray, reduced: int) -> dict:
    return {"changed_pixels": int((pixels != region.rgb).any(axis=1).sum()),
            "gamut_reduced_frac": reduced / max(1, region.rgb.shape[0])}


# ── 적용 뒤 자기 검사 (8절) ─────────────────────────────────────────────────

def self_check(original: np.ndarray, result: np.ndarray, mask: np.ndarray, center, d_l: float,
               d_ab) -> dict:
    """옮긴 사진을 검사한다. reason 이 None 이 아니면 부르는 쪽이 원본을 그대로 돌려줘야 한다."""
    _check_image(original)
    _check_image(result)
    _check_mask(mask, original.shape[:2])
    if result.shape != original.shape:
        raise ValueError("result and original must have the same shape")
    center = _color3(center, "center")
    # 윤곽 밖은 가장자리 띠를 빼지 않고 전부 본다.
    outside_changed = int(((result != original).any(axis=2) & ~mask).sum())
    growth = _clip_growth(original[mask], result[mask])
    measured = np.array(measure_garment_color(result, mask)["center"])
    gain = (center[0] + d_l) / center[0] if d_l else 1.0
    predicted = np.array([center[0] + d_l, gain * center[1] + d_ab[0], gain * center[2] + d_ab[1]])
    error_l = float(abs(measured[0] - predicted[0]))
    error_ab = float(chroma_distance(measured, predicted))
    reason = None
    if outside_changed:
        reason = "reverted_outside_changed"
    elif growth > CLIP_GROWTH_MAX:
        reason = "reverted_clip_guard"
    elif (error_l > max(RECHECK_FLOOR, RECHECK_RATIO * abs(d_l))
          or error_ab > max(RECHECK_FLOOR, RECHECK_RATIO * float(chroma_distance(predicted, center)))):
        reason = "reverted_shortfall"
    return {"outside_changed_pixels": outside_changed, "clip_growth": growth,
            "remeasured_center": _floats(measured), "error_l": error_l, "error_ab": error_ab,
            "reason": reason}
