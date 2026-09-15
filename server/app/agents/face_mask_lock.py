"""얼굴 패스 "6번" — 마스크 밖 latent 고정 렌더 + 벽·그림자·옷 되돌리기.

지금까지 파드는 1024² 크롭 **전체**를 새로 그리고 서버가 타원 알파로 되붙였다
(composite_with_meta). 그림자 있는 벽에서 목 옆 네모 조각과 반원 얼룩이 남았다 — 붙이기 경계다.

2026-09-15 키트 10컷(v7 LoRA · seed 42)에서 사용자가 이 조합("6번")을 골랐다.
  · 운영 방식: 10컷 **전부** 조각.
  · 6번: 조각 없음 · 머리 잘림 없음 · 목 경계 깨끗.
  · 머리 주변 벽 밝기 변화 0.0~0.1, 질감 비율 0.99~1.06.
  · 옷 목둘레 변경 픽셀 prod 5.2%→1.7%, hz_d8 1.2%→0.3%.
  · 신원(크롭 1024 기준) 0.70~0.78 — 운영 방식과 같은 수준.

컷 하나의 흐름(crop = control 을 만든 **그** 1024² 크롭, plan = FacePlan, original = prepare_image 결과):
  1. mask = gen_mask(crop, plan)          → 파드 /render 에 base_png=crop, gen_mask_png=mask
  2. 파드가 마스크 밖 latent 를 매 스텝 되돌리며 렌더(face_identity_qwen._mask_lock)
  3. final = composite(original, gen1024, plan, crop, mask) → 기존처럼 unpad_edges
     링 색보정·keep_mask·grain 은 쓰지 않는다.

각 조각이 있는 이유(키트 실측):
  gen_mask   머리 실루엣 ∪ 예상 LoRA 머리(얼굴박스 ±0.35fw, −0.60fh) + 24px, ∩ control 타원,
             ∩ 사진 안쪽 크롭 변 72px 제외, ∪ 칼라까지의 목 피부.
             타원만 쓰면 칼라 위에서 목이 잘려 꺾이고 8px 격자에 밝은 자국이 남았다.
             실루엣+48px 은 버섯컷을 잘랐다(hz_a3·hz_d3·hz_d7 이 마스크 경계에 ±6px 안으로 닿았다).
  alpha      모델이 바꿀 수 있었던 픽셀만 넘어간다(8px 해제 칸 + 24px 디코더 번짐, σ10).
  give-back  해제 영역 안에서도 렌더는 벽을 다시 칠하고(+0.7~2.6 밝아짐, 입자 0.53~0.94) 칼라 가장자리를
             다시 그린다(prod 후드 옷 픽셀의 5.2%). 그래서 **두 그림이 모두** 벽/그림자라고 보는 자리와
             턱 아래 옷인 자리만 원본으로 되돌린다 — 새 머리·목(+15px)과 **원래 사람의 머리·머리카락**
             (턱 위 +32px)에는 절대 걸지 않는다(그게 없으면 hard_lookbook 에 옛 긴 머리 가닥이,
             sc_5 에 뾰족한 끝이 되살아났다).

★ 정본은 ~/Downloads/comfy_swap_test/reference_code/mask_lock_6_reference.py 다(저장된 6번 결과와
  바이트 동일로 검증됨). 상수는 실측값이라 바꾸려면 키트 10컷을 다시 돌려야 한다.

기각된 변형(같은 키트에서):
  · 큰 타원만 쓰는 lock — 목 꺾임, 벽에 밝은 자국
  · 원본 머리 + 48px 마스크(n48) — 머리 잘림
  · 되돌리기 없는 h24 — 벽 번짐, 옷 목둘레 바뀜
  · 원래 머리 32px 제외 없는 되돌리기(6c) — 원래 모델 머리 가닥이 되살아남
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw

from app.agents import face_identity as fi

CROP = fi.CROP
#: VAE ×8 latent 칸. 파드는 마스크가 닿은 칸을 전부 해제한다(BOX 축소 > 0).
CELL = 8
#: 사진 **안쪽** 크롭 변에서 이만큼 마스크를 뺀다 → 크롭 경계가 잠겨 이음매가 안 생긴다.
EDGE_LOCK_PX = 72
#: 해제 칸 너머로 디코더가 번지는 폭.
SPILL_PX = 24
FEATHER_SIGMA = 10
#: 예상 LoRA 머리(v7 버섯컷 실측 0.28 / 0.49).
HEAD_SIDE, HEAD_TOP, HEAD_BOTTOM = 0.35, 0.60, 1.10
HEAD_MARGIN_PX = 24
NECK_DILATE = 17             # 정사각 커널
PROTECT_NEW_HEAD_PX = 15
OLD_HEAD_PX = 32
OLD_HEAD_CORE_PX = 9
#: 이 값 이상이면 "원래 사람"으로 본다. 일부러 낮다 — 얇은 머리 가닥을 놓치지 않으려고.
OLD_PERSON_T = 0.2
NEW_PERSON_T = 0.4


def _ellipse(r: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def inside_sides(plan) -> tuple[bool, bool, bool, bool]:
    return tuple(not e for e in fi.at_photo_edge(plan))


def edge_lock(plan) -> np.ndarray:
    k = np.ones((CROP, CROP), bool)
    left, top, right, bottom = inside_sides(plan)
    if left:
        k[:, :EDGE_LOCK_PX] = False
    if top:
        k[:EDGE_LOCK_PX, :] = False
    if right:
        k[:, -EDGE_LOCK_PX:] = False
    if bottom:
        k[-EDGE_LOCK_PX:, :] = False
    return k


def control_ellipse(plan) -> np.ndarray:
    return np.asarray(fi.binary_mask(plan)) > 127


def fill_holes(m: np.ndarray) -> np.ndarray:
    inv = (~m).astype(np.uint8)
    _, lab = cv2.connectedComponents(inv)
    border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    return m | (~np.isin(lab, border) & inv.astype(bool))


def head_silhouette(arr: np.ndarray, plan) -> np.ndarray:
    """control 타원 안의 피부 ∪ 머리 → 닫기 → 구멍 메우기 → 얼굴박스 중심이 속한 덩어리."""
    ref = fi._skin_reference(arr, plan)
    ell = control_ellipse(plan)
    if ref is None:
        return ell
    bg = fi._backdrop_color(arr)
    head = fi._skinness(arr, ref) > 0.5
    hair_ref = fi._hair_reference(arr, plan, bg, ref)
    if hair_ref is not None:
        head |= fi._hairness(arr, hair_ref) > 0.5
    head &= ell
    head = cv2.morphologyEx(head.astype(np.uint8), cv2.MORPH_CLOSE, _ellipse(12)).astype(bool)
    head = fill_holes(head)
    _, lab = cv2.connectedComponents(head.astype(np.uint8))
    fx, fy, fw, fh = plan.face_box_crop
    cy, cx = int(min(CROP - 1, fy + fh / 2)), int(min(CROP - 1, fx + fw / 2))
    return (lab == lab[cy, cx]) if lab[cy, cx] != 0 else head


def neck_skin(arr: np.ndarray, plan) -> np.ndarray:
    """턱 아래에서 얼굴과 이어진 피부(칼라까지 보이는 목). 가로로는 얼굴폭 1.2배 안."""
    ref = fi._skin_reference(arr, plan)
    if ref is None:
        return np.zeros((CROP, CROP), bool)
    skin = cv2.morphologyEx((fi._skinness(arr, ref) > 0.5).astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    _, lab = cv2.connectedComponents(skin)
    fx, fy, fw, fh = plan.face_box_crop
    cy, cx = int(min(CROP - 1, fy + 0.55 * fh)), int(min(CROP - 1, fx + fw / 2))
    comp = (lab == lab[cy, cx]) if lab[cy, cx] != 0 else skin.astype(bool)
    yy, xx = np.mgrid[0:CROP, 0:CROP]
    return comp & (yy > fy + fh) & (np.abs(xx - (fx + fw / 2)) < 1.2 * fw)


def expected_head(plan) -> np.ndarray:
    fx, fy, fw, fh = plan.face_box_crop
    m = Image.new("L", (CROP, CROP), 0)
    ImageDraw.Draw(m).ellipse([fx - HEAD_SIDE * fw, fy - HEAD_TOP * fh, fx + fw + HEAD_SIDE * fw, fy + HEAD_BOTTOM * fh], fill=255)
    return np.asarray(m) > 127


def gen_mask(crop: np.ndarray, plan) -> np.ndarray:
    """bool 1024² — True = 파드가 다시 그려도 되는 자리. L PNG(255/0)로 보낸다."""
    head = head_silhouette(crop, plan) | expected_head(plan)
    grown = cv2.dilate(head.astype(np.uint8), _ellipse(HEAD_MARGIN_PX)).astype(bool)
    mask = control_ellipse(plan) & edge_lock(plan) & grown
    neck = cv2.dilate(neck_skin(crop, plan).astype(np.uint8), np.ones((NECK_DILATE, NECK_DILATE), np.uint8)).astype(bool)
    return mask | (neck & edge_lock(plan))


def unlocked_cells(mask: np.ndarray) -> np.ndarray:
    """파드가 실제로 푸는 자리 그대로: BOX 로 128² 축소해 >0 인 칸."""
    cell = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize((CROP // CELL,) * 2, Image.BOX)) > 0
    return np.kron(cell, np.ones((CELL, CELL), bool))


def lock_alpha(mask: np.ndarray, plan) -> np.ndarray:
    un = unlocked_cells(mask)
    a = cv2.dilate(un.astype(np.uint8), np.ones((2 * SPILL_PX + 1,) * 2, np.uint8)).astype(np.float32)
    a = np.maximum(cv2.GaussianBlur(a, (0, 0), FEATHER_SIGMA), un.astype(np.float32))
    return np.clip(a * fi._edge_fade(8, inside_sides(plan)), 0.0, 1.0)


def _person(arr: np.ndarray, plan, bg: np.ndarray, t: float):
    ref = fi._skin_reference(arr, plan)
    if ref is None:                                   # 운영 방어(키트에선 안 걸렸다): 못 재면 전부 사람으로 본다
        return np.ones((CROP, CROP), bool), None
    p = fi._skinness(arr, ref) > t
    hair_ref = fi._hair_reference(arr, plan, bg, ref)
    if hair_ref is not None:
        p |= fi._hairness(arr, hair_ref) > t
    return p, ref


def give_back_keep(crop: np.ndarray, gen: np.ndarray, plan) -> np.ndarray:
    """[0,1] 1024² — 알파에 곱한다. 0 이면 그 자리는 **원본 픽셀을 그대로 둔다**."""
    bg = fi._backdrop_color(crop)
    po, ref_o = _person(crop, plan, bg, OLD_PERSON_T)
    pg, ref_g = _person(gen, plan, bg, NEW_PERSON_T)
    if ref_o is None or ref_g is None:
        return np.ones((CROP, CROP), np.float32)
    fx, fy, fw, fh = plan.face_box_crop
    chin = int(fy + fh)
    protect = cv2.dilate((head_silhouette(gen, plan) | neck_skin(gen, plan)).astype(np.uint8), _ellipse(PROTECT_NEW_HEAD_PX)).astype(bool)
    old_all = cv2.dilate((head_silhouette(crop, plan) | po).astype(np.uint8), _ellipse(OLD_HEAD_PX)).astype(bool)
    old_core = cv2.dilate(head_silhouette(crop, plan).astype(np.uint8), _ellipse(OLD_HEAD_CORE_PX)).astype(bool)
    above = np.zeros((CROP, CROP), bool)
    above[:chin] = True
    old_head = old_core | (old_all & above)
    # (a) 두 그림 모두 피부도 머리도 아닌 자리(벽·그림자). 새 머리·옛 머리에서는 뗀다.
    r_a = fi._soften(((~po) & (~pg) & (~protect) & (~old_head)).astype(np.float32), fi.BACKDROP_ERODE_PX)
    # (b) 두 그림 모두 배경색인 자리. 옛 머리에서는 뗀다.
    r_bg = fi._soften((fi._backdropness(crop, bg) * fi._backdropness(gen, bg) * (~old_head)).astype(np.float32),
                      fi.BACKDROP_ERODE_PX)
    # (c) 턱 아래에서 두 그림 모두 옷인 자리
    below = np.zeros((CROP, CROP), bool)
    below[chin:] = True
    g_o = (fi._skinness(crop, ref_o) < 0.3) & (fi._backdropness(crop, bg) < 0.3) & below
    g_g = (fi._skinness(gen, ref_g) < 0.3) & (fi._backdropness(gen, bg) < 0.3) & below
    r_g = fi._soften((g_o & g_g).astype(np.float32), fi.GARMENT_ERODE_PX)
    return np.clip(1.0 - np.maximum(np.maximum(r_a, r_bg), r_g), 0.0, 1.0)


def put_back(original: Image.Image, gen: Image.Image, plan, alpha: np.ndarray) -> Image.Image:
    x0, y0, side = plan.crop
    small = np.asarray(gen.convert("RGB").resize((side, side), Image.LANCZOS), np.float32)
    a = cv2.resize(alpha, (side, side), interpolation=cv2.INTER_AREA)[..., None]
    out = np.asarray(original.convert("RGB"), np.float32).copy()
    reg = out[y0:y0 + side, x0:x0 + side]
    out[y0:y0 + side, x0:x0 + side] = small * a + reg * (1.0 - a)
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8))


def composite(original: Image.Image, gen1024: Image.Image, plan, crop: Image.Image | np.ndarray,
              mask: np.ndarray) -> Image.Image:
    """최종(덧댄 상태) 사진. `mask` 는 이 렌더에서 파드에 보낸 **그** gen_mask 여야 한다."""
    crop_arr = np.asarray(crop, np.float32) if not isinstance(crop, np.ndarray) else crop.astype(np.float32)
    gen = gen1024.convert("RGB")
    if gen.size != (CROP, CROP):
        gen = gen.resize((CROP, CROP), Image.LANCZOS)
    alpha = lock_alpha(mask, plan) * give_back_keep(crop_arr, np.asarray(gen, np.float32), plan)
    return put_back(original, gen, plan, alpha)
