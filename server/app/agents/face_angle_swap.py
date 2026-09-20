"""옆·뒷모습 컷의 머리 교체 — 등록자 각도 사진 1장으로 ComfyUI(BFS Head V5)에서 다시 그린다.

왜 얼굴 패스(face_identity)와 따로인가: v7 LoRA 는 정면·3/4 만 배웠다. 옆얼굴(yaw>0.65)은
건너뛰고, 뒷모습은 얼굴이 아예 안 잡혀 시작도 못 한다. 그래서 그 두 각도는 **등록자 실사진**을
참고로 머리만 바꾼다(2026-09-20 실측: 옆 4/4 머리·귀·볼 피부가 본인, 뒤 4/4 얼굴 안 그려짐,
옷 변화 0~228px).

머리 자리를 찾는 기준은 각도마다 다르다:
  · 옆모습 — 얼굴 박스(YuNet)가 잡힌다. 그 박스를 키운 창 안의 전경이 머리다.
  · 뒷모습 — 얼굴이 없다. 위쪽 절반에서 가장 큰 **어두운 덩어리(머리카락)** 가 머리다.
실루엣 기하(가장 좁은 행 = 목)는 옷 색에 흔들린다 — 흰 나시·연한 줄무늬는 배경과 차이가 10
안팎이라 실루엣에서 빠지고, 그러면 바지가 인물로 뽑힌다(2026-09-20 실측: 8컷 중 4컷 실패).

그 뒤는 확정 워크플로우와 같다: 머리 정사각 크롭 1024 + 머리 마스크 → ComfyUI(2511 + BFS Head V5
+ LanPaint) → 마스크 안만 원본 해상도로 합성, 옷은 원본 픽셀로 되돌림.

계약은 얼굴 패스와 같다 — 결과를 못 만들면 남의 머리를 내보내지 않고 예외를 올린다.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

from . import face_identity

log = logging.getLogger("wearless.face_angle_swap")

PROMPT_VERSION = "face_angle_swap_v1"
#: 2511 은 BFS Head V5 와 같이 쓴다(2509+V4 조합은 깨진다 — 2026-09-18 실측).
UNET = "qwen_image_edit_2511_fp8mixed.safetensors"
CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"
BFS_LORA = "bfs_head_v5_2511_merged_version_rank_16_fp16.safetensors"
STEPS = 20
CFG = 2.5
SEED = 42
#: BFS LoRA 강도. 2026-09-20 스윕(옆모습 2컷)에서 1.2 는 닮음 점수가 높았지만(0.333→0.41) 머리가
#: 부풀고 앞머리가 두꺼워졌다. 1.4 는 더 멀어졌다(0.25). 눈으로 고른 값은 1.0 이다 — 합격 판정을
#: 받은 옆 4컷·뒤 4컷이 전부 이 강도로 나왔다. 점수보다 실제로 본 결과를 따른다.
BFS_STRENGTH = 1.0
CROP_PX = 1024
#: 머리 크롭 = (여유 주기 전) 머리 크기 × 이만큼의 정사각형. 목·칼라가 같이 보여야 모델이 자리를
#: 맞춘다. 2026-09-20 성공 실행의 크롭이 머리 크기의 1.5~1.9 배였다.
CROP_HEAD_WIDTHS = 1.9
#: 머리를 이만큼(머리 너비 비율) 넓혀 머리카락이 새로 날 자리를 준다. 짧은 머리 기준
#: (긴 머리 기본값 0.6/0.9 는 배경까지 크게 잡혀 2026-09-20 에 줄였다).
HAIR_MARGIN_SIDE = 0.25
HAIR_MARGIN_BACK = 0.30
#: 얼굴 박스(옆) · 머리카락 덩어리(뒤) 아래로 더 잡는 높이 — 박스 높이 비율.
FACE_DOWN_SIDE = 0.35
HAIR_DOWN_BACK = 0.12
#: 배경과 사람을 가르는 문턱(채널별 차이의 최댓값, 0~255). 2026-09-20 실측(호리존 컷):
#: 벽 그림자 12(머리 바로 옆은 더 진하다) · 피부 82 · 옷 130 · 머리 206 → 50 이면 그림자만 떨어진다. 낮게 두면 그림자가
#: 사람으로 붙어 머리 크롭이 2배가 된다.
BG_THRESHOLD = 50.0
#: 머리카락 후보 밝기 상한 — 배경 밝기의 이 비율보다 어두운 픽셀(머리 0.13 · 그림자 0.95 ·
#: 진한 데님 0.47).
HAIR_MAX_LUMINANCE = 0.45
#: 머리 아래 목으로 보는 폭 비율 — 가장 넓은 행의 이 비율 밑으로 떨어지면 목이다.
NECK_WIDTH_RATIO = 0.6
#: 사람이 이보다 작으면 사람이 안 담긴 컷으로 본다(이미지 면적 비율).
MIN_PERSON_FRACTION = 0.02

_POS_HEAD = (
    "head_swap: start with Picture 1 as the base image, keeping its lighting, environment, and background. "
    "remove the head from Picture 1 completely and replace it with the head from Picture 2, strictly preserving "
    "the hair, eye color, nose structure of Picture 2. copy the direction of the eye, head rotation, micro "
    "expressions from Picture 1, high quality, sharp details, 4k"
)
_POS_SIDE = (
    " The hair must be exactly the hairstyle of Picture 2: the same length, the same fringe and the same nape. "
    "The person is not bald. Keep the exact side profile of Picture 1. Also copy from Picture 2 the nose shape, "
    "the lip shape and the chin and jaw line, so the profile outline matches Picture 2."
)
_POS_BACK = (
    " Picture 2 shows the back of the head. Draw the back of the head seen from behind with the same hair shape, "
    "crown and nape as Picture 2, and the same ears. No face is visible. The garment must stay exactly as in Picture 1."
)
NEG = ("bad quality, noise, blurry, worst quality, low resolution, blur, distortion, unnatural blending, "
       "cartoon, illustration, painting")

#: 컷 방향 → 등록 사진 칸(facemarket_photos 의 칸 이름). 옆모습은 컷이 보는 쪽에 맞춰 고른다.
SLOT_BACK = "sh_back"
SLOT_SIDE_NOSE_LEFT = "sh_side"        # 코가 화면 왼쪽 = 사람의 왼쪽 옆모습
SLOT_SIDE_NOSE_RIGHT = "sh_side_right"


@dataclass
class AngleSwapSpec:
    """이 컷에 쓸 등록자 각도 사진과 ComfyUI 백엔드. 워커가 만들어 generate() 에 넘긴다."""

    photos: AnglePhotos
    backend: object
    seed: int = SEED


def spec_from(settings, photos: AnglePhotos, *, pod_id: str | None = None) -> AngleSwapSpec | None:
    """파드 주소가 있으면 백엔드를 만든다. 없으면 None(=이 경로를 안 탄다).

    주소의 정본은 **지금 살아 있는 파드**다(pod_id). 자동 기동이 재고를 못 잡아 파드를 새로
    만들면 id 가 바뀌는데, 설정값을 앞에 두면 죽은 주소를 계속 찌른다 — 얼굴 패스가 같은
    순서를 쓴다(face_identity.resolve_backend). 설정값은 파드가 아직 없을 때의 폴백이다.
    """
    if not getattr(settings, "face_angle_swap_enabled", False):
        return None
    url = pod_backend_url(pod_id) or getattr(settings, "face_angle_backend_url", None)
    if not url:
        return None
    token = getattr(settings, "face_angle_backend_token", "") or ""
    return AngleSwapSpec(photos=photos, backend=ComfyBackend(url, token),
                         seed=int(getattr(settings, "face_angle_seed", SEED)))


def pod_backend_url(pod_id: str | None) -> str | None:
    """파드 id → ComfyUI 주소. services/angle_autoscale 의 같은 이름과 한 몸이어야 한다 —
    어댑터는 이 주소로 /healthz 를 보고 워커는 이 주소로 컷을 만든다. 갈리면 둘이 다른 파드를
    본다. 순환 import 를 피하려고 여기서 다시 쓰되, 계약 테스트가 두 값을 맞춰 둔다."""
    pod_id = (pod_id or "").strip()
    return f"https://{pod_id}-8000.proxy.runpod.net" if pod_id else None


def direction_of(spec: dict) -> str | None:
    """이 컷이 각도 교체 대상인가 — 옆·뒷모습만."""
    direction = str((spec or {}).get("direction") or "")
    return direction if direction in ("side", "back") else None


class AngleSwapUnavailable(RuntimeError):
    """머리를 못 바꿨다 — 호출자는 컷을 실패로 끝낸다(남의 머리를 내보내지 않는다)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class AnglePhotos:
    """등록자 각도 사진 바이트. 없는 칸은 None."""

    side_nose_left: bytes | None = None    # sh_side
    side_nose_right: bytes | None = None   # sh_side_right
    back: bytes | None = None              # sh_back

    def for_direction(self, direction: str, nose_right: bool | None) -> tuple[bytes | None, str]:
        if direction == "back":
            return self.back, SLOT_BACK
        if nose_right is None:
            return None, ""
        if nose_right:
            return self.side_nose_right, SLOT_SIDE_NOSE_RIGHT
        return self.side_nose_left, SLOT_SIDE_NOSE_LEFT


@dataclass
class Plan:
    base: np.ndarray                 # 원본 RGB
    head: np.ndarray                 # 머리 마스크(bool, 머리카락 여유 포함) — 이 안만 다시 그린다
    head_core: np.ndarray            # 여유 주기 전 머리 — 크롭 기준
    garment: np.ndarray              # 옷·몸 보호 마스크(bool) — 원본 픽셀로 되돌린다
    person: np.ndarray               # 원본 전경(사람) — 톤 표본을 여기 안에서만 고른다
    box: tuple[int, int, int]        # (left, top, side) 정사각 크롭
    crop1k: np.ndarray               # 1024 크롭 RGB
    mask1k: np.ndarray               # 1024 머리 마스크(bool)
    nose_right: bool | None          # 옆모습이 보는 쪽(뒷모습이면 None)
    metadata: dict = field(default_factory=dict)


def background_color(image: np.ndarray) -> np.ndarray:
    """배경색 = 위·좌·우 테두리의 중앙값. **전체 이미지**에서만 구한다 —
    머리 크롭 테두리로 구하면 그 테두리가 사람·옷이라 판정이 통째로 뒤집힌다."""
    a = image.astype(np.float32)
    border = np.concatenate([a[:6].reshape(-1, 3), a[:, :6].reshape(-1, 3), a[:, -6:].reshape(-1, 3)])
    return np.median(border, axis=0)


def foreground(image: np.ndarray) -> np.ndarray:
    """배경(위·좌·우 테두리 색)과 다른 픽셀 전부. 벽 그림자는 BG_THRESHOLD 로 떨어진다."""
    a = image.astype(np.float32)
    bg = background_color(image)
    m = (np.abs(a - bg).max(axis=2) > BG_THRESHOLD).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8)).astype(bool)


def trim_at_neck(mask: np.ndarray) -> np.ndarray:
    """덩어리가 목 아래로 이어지면(어두운 옷과 붙었으면) 목에서 자른다.

    머리에서 가장 넓은 행 아래로 내려가다 폭이 그 60% 밑으로 떨어지는 첫 행이 목이다.
    머리카락만 있는 덩어리는 그 밑으로 안 떨어지므로 아무것도 안 자른다.
    """
    widths = mask.sum(axis=1)
    ys = np.where(mask.any(axis=1))[0]
    top, bottom = int(ys.min()), int(ys.max())
    upper = max(top + 1, top + int(0.5 * (bottom - top)) + 1)
    widest = top + int(np.argmax(widths[top:upper]))
    tail = widths[widest:bottom + 1]
    narrow = np.where(tail < NECK_WIDTH_RATIO * float(widths[widest]))[0]
    for i in narrow:
        below = tail[i + 1:]
        # 목이라면 그 아래에 옷이 다시 넓게 나온다. 그냥 끝으로 갈수록 좁아지는 머리는 안 자른다.
        if below.size and below.max() > 1.2 * max(1.0, float(tail[i])):
            out = mask.copy()
            out[widest + int(i) + 1:] = False
            return out
    return mask


def hair_blob(image: np.ndarray) -> np.ndarray:
    """뒷모습용 — 맨 위에서 시작하는 가장 큰 어두운 덩어리 = 머리카락. 목 아래는 잘라낸다.

    어두운 옷(데님)은 머리카락과 같은 밝기라 목덜미에서 붙는다(2026-09-20 실측: 붙으면 크롭이
    사진 전체가 된다). 그래서 덩어리를 고른 뒤 목에서 자른다.
    """
    a = image.astype(np.float32)
    border = np.concatenate([a[:6].reshape(-1, 3), a[:, :6].reshape(-1, 3), a[:, -6:].reshape(-1, 3)])
    background_luminance = float(np.median(border, axis=0).mean())
    dark = (a.mean(axis=2) < HAIR_MAX_LUMINANCE * background_luminance).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(dark)
    if count <= 1:
        return np.zeros(dark.shape, bool)
    tops = stats[1:, cv2.CC_STAT_TOP]
    areas = stats[1:, cv2.CC_STAT_AREA]
    near_top = np.where(tops <= tops.min() + 0.03 * dark.shape[0])[0]
    if not near_top.size:
        return np.zeros(dark.shape, bool)
    pick = int(near_top[int(np.argmax(areas[near_top]))]) + 1
    return trim_at_neck(labels == pick)


def _connected_to(mask: np.ndarray, seed: np.ndarray) -> np.ndarray:
    """mask 에서 seed 와 이어진 덩어리만 남긴다 — 옆에 떨어져 있는 벽 그림자 조각을 떨군다."""
    count, labels = cv2.connectedComponents(mask.astype(np.uint8))
    if count <= 1:
        return mask
    keep = {int(v) for v in np.unique(labels[seed & mask]) if v}
    if not keep:
        return mask
    return np.isin(labels, list(keep))


def window(box, shape, *, left: float, right: float, up: float, down: float) -> np.ndarray:
    """박스(x, y, w, h)를 비율만큼 넓힌 직사각 창."""
    bx, by, bw, bh = box
    mask = np.zeros(shape, bool)
    x0 = max(0, int(bx - left * bw))
    x1 = min(shape[1], int(bx + bw + right * bw))
    y0 = max(0, int(by - up * bh))
    y1 = min(shape[0], int(by + bh + down * bh))
    mask[y0:y1, x0:x1] = True
    return mask


def head_region(image: np.ndarray, *, direction: str, model_dir=None) -> tuple[np.ndarray, bool | None]:
    """(여유 주기 전 머리 마스크, 옆모습이 보는 쪽)."""
    fg = foreground(image)
    shape = image.shape[:2]
    if direction != "back":
        detection = face_identity.detect_face(Image.fromarray(image), model_dir)
        if detection is None:
            raise AngleSwapUnavailable("no_face")
        box = tuple(float(v) for v in detection.box)
        core = fg & window(box, shape, left=1.0, right=0.45, up=1.5, down=FACE_DOWN_SIDE)
        # 얼굴 박스와 이어진 덩어리만 — 머리 옆 벽 그림자가 네모로 딸려오지 않게.
        core = _connected_to(core, window(box, shape, left=0.0, right=0.0, up=0.0, down=0.0))
        if not core.any():
            raise AngleSwapUnavailable("no_head")
        bx, _by, bw, _bh = box
        xs = np.where(core.any(axis=0))[0]
        return core, float(bx + bw / 2) > float(int(xs.min()) + int(xs.max())) / 2.0
    hair = hair_blob(image)
    if not hair.any():
        raise AngleSwapUnavailable("no_head")
    ys, xs = np.where(hair)
    box = (float(xs.min()), float(ys.min()), float(xs.max() - xs.min()), float(ys.max() - ys.min()))
    near = fg & window(box, shape, left=0.12, right=0.12, up=0.15, down=HAIR_DOWN_BACK)
    return _connected_to(near | hair, hair), None


def garment_region(image: np.ndarray, core: np.ndarray) -> np.ndarray:
    """머리 아래 전경 전부 = 옷·몸 보호 영역."""
    garment = foreground(image)
    ys = np.where(core.any(axis=1))[0]
    garment[:int(ys.max()) + 1] = False
    return garment


def grow_hair(core: np.ndarray, direction: str) -> np.ndarray:
    """머리카락이 새로 날 자리만큼 넓힌다(짧은 머리 기준)."""
    xs = np.where(core.any(axis=0))[0]
    width = max(1, int(xs.max()) - int(xs.min()))
    margin = int((HAIR_MARGIN_BACK if direction == "back" else HAIR_MARGIN_SIDE) * width)
    if margin <= 0:
        return core
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin + 1,) * 2)
    return cv2.dilate(core.astype(np.uint8), kernel).astype(bool)


def crop_box(core: np.ndarray, size: tuple[int, int]) -> tuple[int, int, int]:
    """머리를 담는 정사각 크롭 (left, top, side). 이미지 밖으로 나가면 안으로 민다."""
    width, height = size
    ys, xs = np.where(core)
    cx = (int(xs.min()) + int(xs.max())) // 2
    cy = (int(ys.min()) + int(ys.max())) // 2
    span = max(int(xs.max()) - int(xs.min()), int(ys.max()) - int(ys.min()))
    side = int(min(max(CROP_HEAD_WIDTHS * span, 64), width, height))
    left = max(0, min(width - side, cx - side // 2))
    top = max(0, min(height - side, cy - side // 2))
    return left, top, side


def plan(image: np.ndarray, *, direction: str, model_dir=None) -> Plan:
    """원본 → 크롭·마스크. 사람·머리를 못 찾으면 AngleSwapUnavailable."""
    height, width = image.shape[:2]
    person = foreground(image)
    if person.sum() < MIN_PERSON_FRACTION * height * width:
        raise AngleSwapUnavailable("no_person")
    core, nose_right = head_region(image, direction=direction, model_dir=model_dir)
    garment = garment_region(image, core)
    head = grow_hair(core, direction) & ~garment
    left, top, side = crop_box(core, (width, height))
    crop = np.asarray(Image.fromarray(image[top:top + side, left:left + side])
                      .resize((CROP_PX, CROP_PX), Image.LANCZOS))
    mask = cv2.resize(head[top:top + side, left:left + side].astype(np.uint8), (CROP_PX, CROP_PX),
                      interpolation=cv2.INTER_NEAREST) > 0
    if not mask.any():
        raise AngleSwapUnavailable("no_head")
    return Plan(base=image, head=head, head_core=core, garment=garment, person=person,
                box=(left, top, side), crop1k=crop, mask1k=mask, nose_right=nose_right,
                metadata={"headPx": int(head.sum()), "garmentPx": int(garment.sum()),
                          "crop": [left, top, side], "noseRight": nose_right,
                          "maskCropPct": round(100.0 * float(mask.mean()), 1)})


def prompt_for(direction: str) -> str:
    return _POS_HEAD + (_POS_BACK if direction == "back" else _POS_SIDE)


def graph(crop_name: str, ref_name: str, mask_name: str, prompt: str, *, seed: int = SEED) -> dict:
    """ComfyUI API 형식 그래프 — 2026-09-18 확정본과 같은 노드·값."""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "LoadImage", "inputs": {"image": crop_name}},
        "15": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
        "5": {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}},
        "16": {"class_type": "LoraLoaderModelOnly",
               "inputs": {"model": ["1", 0], "lora_name": BFS_LORA, "strength_model": BFS_STRENGTH}},
        "6": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["16", 0], "shift": 1.0}},
        "7": {"class_type": "CFGNorm", "inputs": {"model": ["6", 0], "strength": 1.0}},
        "8": {"class_type": "TextEncodeQwenImageEditPlus",
              "inputs": {"clip": ["2", 0], "vae": ["3", 0], "image1": ["4", 0], "image2": ["15", 0],
                         "prompt": prompt}},
        "9": {"class_type": "TextEncodeQwenImageEditPlus",
              "inputs": {"clip": ["2", 0], "vae": ["3", 0], "image1": ["4", 0], "image2": ["15", 0],
                         "prompt": NEG}},
        "18": {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["8", 0], "reference_latents_method": "index_timestep_zero"}},
        "19": {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["9", 0], "reference_latents_method": "index_timestep_zero"}},
        "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["4", 0], "vae": ["3", 0]}},
        "11": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["10", 0], "mask": ["5", 0]}},
        "12": {"class_type": "LanPaint_KSampler", "inputs": {
            "model": ["7", 0], "seed": int(seed), "steps": STEPS, "cfg": CFG, "sampler_name": "euler",
            "scheduler": "simple", "positive": ["18", 0], "negative": ["19", 0], "latent_image": ["11", 0],
            "denoise": 1.0, "LanPaint_NumSteps": 5, "LanPaint_PromptMode": "Image First",
            "LanPaint_Info": "angle_swap", "Inpainting_mode": "🖼️ Image Inpainting"}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
        "14": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": "angle"}},
    }


#: 톤 보정에 쓰는 링 폭(px, 마스크 바깥). 목이 들어올 만큼은 넓고 배경까지 가지는 않을 만큼 좁다.
TONE_RING_PX = 6
#: 사람 안에서 **피부만** 고르는 밝기 하한. 머리카락(어둡다)을 뺀다. 위쪽 한계는 두지 않는다 —
#: 벽·그림자는 전경 마스크가 이미 걸러 주고, 밝은 피부를 상한으로 자르면 표본이 편향된다.
#: 2026-09-20 실측 3컷의 목 평균 밝기 117~184, 머리카락 40 안팎.
TONE_SKIN_MIN = 90.0
#: 이만큼은 표본이 있어야 보정한다. 적으면 그 평균이 목이 아니라 잡음이다.
TONE_MIN_SAMPLES = 200
#: 보정량 |RGB| 최대가 이 값을 넘으면 **컷을 버린다**(tone_off). 조명이 근본적으로 어긋난
#: 참고 사진이라는 뜻이고, 억지로 맞추면 목만 물든다. 얼굴 패스의 GATE_COLOR_MAX(35.0)와
#: 같은 자리·같은 값으로 둔다 — 두 경로가 다른 기준을 쓰면 같은 사진이 한쪽만 통과한다.
TONE_MAX_SHIFT = 35.0


def tone_shift(p: Plan, out: np.ndarray, box: tuple[int, int, int]) -> np.ndarray | None:
    """새로 그린 머리의 톤을 **원본 목**에 맞추는 전역 RGB 이동량. 표본이 없으면 None.

    ★ 얼굴 패스처럼 타원 안쪽 링을 쓸 수 없다. 저쪽 마스크는 얼굴이라 링이 곧 얼굴 주변
      피부지만, 여기 마스크는 **머리 전체**라 바깥 링에 배경 벽·그림자·옷이 같이 들어온다.
      벽을 표본에 넣으면 목이 벽 색으로 끌려간다 — 얼굴 패스에서 국소 보정이 실패한 것과
      같은 이유다(face_identity.paste_back 주석: 링이 밝은 벽을 표본해 목·턱이 노랗게 과보정).

    ★ 그래서 **전경 마스크로 먼저 자르고**(벽·그림자가 통째로 빠진다) 그 안에서 밝기로
      머리카락만 뺀다. 밝기 창만으로 고르면 벽 그림자가 피부로 들어온다 — 2026-09-20
      합성 컷에서 바깥 링 평균이 199(그림자)로 나왔다. 실사진에서 안 걸린 건 그 벽이
      우연히 상한 위였기 때문이다.

    ★ 전역 이동만 한다. 국소 차분 필드는 얼굴 패스에서 이미 되돌렸다(신원 붕괴·과보정).

    2026-09-20 실측(보정 전): 오른쪽 옆 9.2 · 왼쪽 옆 12.3 · 뒤 7.1. 세 컷 다 RGB 가 거의
    같은 양으로 어긋난 **밝기** 차이였다 — 참고 사진이 베이스 컷보다 밝았다.
    """
    left, top, side = box
    head = p.head[top:top + side, left:left + side]
    if not head.any():
        return None
    k = 2 * TONE_RING_PX + 1
    ring_out = cv2.dilate(head.astype(np.uint8), np.ones((k, k), np.uint8)).astype(bool) & ~head
    ring_in = head & ~cv2.erode(head.astype(np.uint8), np.ones((k, k), np.uint8)).astype(bool)
    base = p.base[top:top + side, left:left + side].astype(np.float32)
    person = p.person[top:top + side, left:left + side]
    bg = background_color(p.base)

    def skin(mask, image, is_person):
        return mask & is_person & (image.mean(axis=2) > TONE_SKIN_MIN)

    # 바깥 = 원본의 목(전경). 안쪽 = 새로 그린 얼굴·목 — 원본에서 배경이던 자리로 나올 수
    # 있으므로 **생성 결과**로 사람을 판정한다.
    outside = skin(ring_out, base, person)
    inside = skin(ring_in, out, np.abs(out - bg).max(axis=2) > BG_THRESHOLD)
    if outside.sum() < TONE_MIN_SAMPLES or inside.sum() < TONE_MIN_SAMPLES:
        return None
    return base[outside].mean(axis=0) - out[inside].mean(axis=0)


#: 톤 가중치를 부드럽게 만드는 흐림(px). 실루엣에서 뚝 끊기지 않을 만큼만.
TONE_FEATHER = 2.0


def apply_tone(out: np.ndarray, shift: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """톤 보정을 **사람 픽셀에만** 건다. 배경은 건드리지 않는다.

    ★ 머리 마스크는 머리카락이 새로 날 자리까지 포함해 **배경 위로 넘어간다**(grow_hair).
      전역으로 걸면 그 배경도 같이 밝아져서 벽에 머리 모양 자국이 남는다 — 2026-09-20
      실측에서 세 컷 모두 머리 옆 벽에 실루엣이 찍혔다. 얼굴 패스는 타원이 전부 얼굴이라
      이 문제가 없다(face_identity.paste_back).

    ★ 사람 판정은 **생성 결과**에서 한다. 원본 전경으로 가리면 새로 나온 목·턱(원본에서는
      배경이던 자리)이 보정을 못 받아 이음선이 그대로 남는다 — 2026-09-20 왼쪽 옆 컷이
      12.3 → 10.3 밖에 안 줄었던 이유다.
    ★ 배경색은 **전체 원본** 테두리에서 온다(background_color). 크롭 테두리는 사람·옷이다.
    """
    person = (np.abs(out - bg).max(axis=2) > BG_THRESHOLD).astype(np.float32)
    weight = cv2.GaussianBlur(person, (0, 0), TONE_FEATHER)[..., None]
    return np.clip(out + shift * weight, 0, 255)


def composite(p: Plan, out1k: np.ndarray, *, meta: dict | None = None) -> np.ndarray:
    """마스크 안만 원본 해상도에 섞고, 옷은 원본 픽셀로 되돌린다(경계 2px 만 섞음).

    섞기 전에 톤을 원본 목에 맞춘다(tone_shift) — 등록자 사진과 베이스 컷의 조명이 다르면
    목 이음선에서 밝기가 튄다. meta 를 주면 보정량을 적어 둔다(검수·게이트용).
    """
    left, top, side = p.box
    out = np.asarray(Image.fromarray(out1k).resize((side, side), Image.LANCZOS), np.float32)
    shift = tone_shift(p, out, p.box)
    if shift is not None:
        out = apply_tone(out, shift, background_color(p.base))
    if meta is not None:
        meta["toneShift"] = ([round(float(v), 2) for v in shift] if shift is not None else None)
    region = p.head[top:top + side, left:left + side].astype(np.float32)
    blur = cv2.GaussianBlur(region, (0, 0), 1.5 * max(1.0, side / CROP_PX))
    alpha = np.maximum(blur * (cv2.dilate(region, np.ones((3, 3), np.uint8)) > 0), region)[..., None]
    final = p.base.astype(np.float32).copy()
    patch = final[top:top + side, left:left + side]
    final[top:top + side, left:left + side] = out * alpha + patch * (1.0 - alpha)
    garment = p.garment.astype(np.float32)
    core = cv2.erode(p.garment.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(np.float32)
    soft = np.clip(cv2.GaussianBlur(core, (0, 0), 1.6), 0, 1) * garment
    weight = np.maximum(soft, core)[..., None]
    blended = p.base.astype(np.float32) * weight + final * (1.0 - weight)
    return np.clip(blended + 0.5, 0, 255).astype(np.uint8)


def changed_garment_px(p: Plan, final: np.ndarray) -> int:
    """옷 영역에서 원본과 달라진 픽셀 수 — 검수 지표(2026-09-20 실측 0~228)."""
    if not p.garment.any():
        return 0
    diff = np.abs(final[p.garment].astype(np.int16) - p.base[p.garment].astype(np.int16)).max(axis=1)
    return int((diff > 0).sum())


class ComfyBackend:
    """ComfyUI 파드 호출. upload → /prompt → /history → /view."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 900.0, poll_seconds: float = 5.0):
        import httpx

        #: 어느 파드를 보고 있는가. 파드가 재고 때문에 갈릴 수 있어 로그·검증에서 읽는다.
        self.base = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base, timeout=timeout,
                                    headers={"Authorization": f"Bearer {token}"})
        self._poll = poll_seconds

    def upload(self, array: np.ndarray, name: str, mode: str = "RGB") -> str:
        stream = io.BytesIO()
        Image.fromarray(array).convert(mode).save(stream, "PNG")
        r = self._client.post("/upload/image", files={"image": (name, stream.getvalue(), "image/png")},
                              data={"overwrite": "true"})
        r.raise_for_status()
        return r.json()["name"]

    def run(self, workflow: dict) -> np.ndarray:
        r = self._client.post("/prompt", json={"prompt": workflow, "client_id": "angle_swap"})
        if r.status_code != 200:
            raise AngleSwapUnavailable("backend_error")
        prompt_id = r.json()["prompt_id"]
        while True:
            entry = (self._client.get(f"/history/{prompt_id}").json() or {}).get(prompt_id)
            if entry:
                if (entry.get("status") or {}).get("status_str") == "error":
                    log.warning("angle swap graph error: %s", json.dumps(entry.get("status"))[:200])
                    raise AngleSwapUnavailable("graph_error")
                images = (entry.get("outputs") or {}).get("14", {}).get("images")
                if images:
                    view = self._client.get("/view", params={
                        "filename": images[0]["filename"], "subfolder": images[0].get("subfolder", ""),
                        "type": images[0].get("type", "output")})
                    view.raise_for_status()
                    with Image.open(io.BytesIO(view.content)) as opened:
                        return np.asarray(opened.convert("RGB"))
            time.sleep(self._poll)


def _run(backend, p: Plan, reference: np.ndarray, direction: str, seed: int) -> np.ndarray:
    crop_name = backend.upload(p.crop1k, "angle_crop.png")
    ref_name = backend.upload(reference, "angle_ref.png")
    mask_name = backend.upload((p.mask1k * 255).astype(np.uint8), "angle_mask.png", "L")
    return backend.run(graph(crop_name, ref_name, mask_name, prompt_for(direction), seed=seed))


async def swap(image: bytes, mime: str, *, direction: str, photos: AnglePhotos, backend,
               model_dir=None, seed: int = SEED, outcome: dict | None = None) -> tuple[bytes, str]:
    """옆·뒷모습 컷의 머리를 등록자 사진으로 교체한다. 실패는 AngleSwapUnavailable."""
    if direction not in ("side", "back"):
        raise ValueError("angle_swap_direction")
    meta: dict = {"promptVersion": PROMPT_VERSION, "direction": direction, "seed": seed}
    with Image.open(io.BytesIO(image)) as opened:
        base = np.asarray(opened.convert("RGB"))
    p = await asyncio.to_thread(plan, base, direction=direction, model_dir=model_dir)
    meta.update(p.metadata)
    reference_bytes, slot = photos.for_direction(direction, p.nose_right)
    meta["slot"] = slot
    if not reference_bytes:
        raise AngleSwapUnavailable("no_angle_photo")
    with Image.open(io.BytesIO(reference_bytes)) as opened:
        reference = np.asarray(opened.convert("RGB"))
    try:
        out1k = await asyncio.to_thread(_run, backend, p, reference, direction, seed)
    except AngleSwapUnavailable:
        raise
    except Exception as exc:
        log.warning("angle swap backend failed: %s", type(exc).__name__)
        raise AngleSwapUnavailable("backend_error") from exc
    final = await asyncio.to_thread(composite, p, out1k, meta=meta)
    # 톤을 못 맞출 만큼 조명이 어긋난 참고 사진이면 **컷을 버린다**. 억지로 붙이면 목만
    # 물든 사람이 상품 페이지에 실린다 — 얼굴 패스의 lighting_off 와 같은 판단이다.
    shift = meta.get("toneShift")
    if shift and max(abs(v) for v in shift) > TONE_MAX_SHIFT:
        log.warning("angle swap tone off: %s", shift)
        raise AngleSwapUnavailable("tone_off")
    meta["garmentChangedPx"] = changed_garment_px(p, final)
    stream = io.BytesIO()
    Image.fromarray(final).save(stream, "PNG")
    if outcome is not None:
        outcome["angle_swap"] = "applied"
        outcome["angle_swap_meta"] = meta
    return stream.getvalue(), "image/png"
