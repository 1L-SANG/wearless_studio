"""등록 사진 12장 → LoRA 학습셋(target·control·mask + 캡션). 맥 경로 없이 R2 에서만 흐른다.

정본은 `~/Downloads/lora_runs/v6_kit/build_v7.py`(규칙 주석은 build_v6_final.py)다. 그 스크립트는
맥에 풀어 둔 정규화 PNG 폴더를 읽었는데, 등록 사진은 생체정보라 맥 디스크에 남기면 안 된다.
그래서 같은 규칙을 서버로 옮기고 **입출력만** 바꿨다 — R2 에서 읽고, 결과 tgz 를 R2 에 올리고,
중간 파일을 디스크에 만들지 않는다(메모리에서 tar 를 쌓는다).

옮기면서 **바뀐 것 하나**(사용자 지시 2026-09-16): 기하는 face_identity 의 살아 있는 함수를
그대로 쓴다. 원본은 build_v4c 고정 사본으로 만들었는데, 그건 "v6-1500 과 같은 조건" 비교를
지키려던 장치였다(그 사이 feather_mask 가 넓어졌다). 새 사람을 새로 학습하는 지금은 **추론이
쓰는 기하와 같아야** 맞다 — crop·control 은 두 판이 어차피 같은 픽셀이고, 다른 건 마스크
(= 손실 가중 영역)뿐이라 meta 에 면적을 남겨 눈에 보이게 한다.

규칙(원본 그대로, 바꾸지 말 것):
  · 학습 12장(TRAINING_SLOTS)만. 기준 3장(REFSET_SLOTS)은 **학습에 넣지 않는다** —
    표본 control 로만 쓴다. 기준이 학습에 섞이면 동일인 채점이 자기 자신을 보게 된다.
  · 검출 = YuNet ×4 축소 → 좌표 ×4 복원.
  · 캡션 "ohwx man, {표정}, head and shoulders portrait, {각도}, outdoors, {조명}, photograph"
      표정: 미소→smiling(입_다문_미소→slight smile), 무표정→neutral expression,
            **없으면 생략**(추정 금지 — 없는 표정을 지어내면 그 어휘가 그대로 학습된다)
      각도: yaw ≤0.16 facing the camera / ≤0.45 turned three-quarters(+ to the left|right)
            / 초과 in profile(+ , facing left|right)
      조명: 그늘 soft shaded daylight · 해가왼쪽·해가오른쪽 directional sunlight · 해등지고 backlit daylight
      garment 절 없음.
  · 증강: 색온도만 — 목표 R/B ~ U[1.15, 1.45](파일별 시드), 배율 = clip(목표/원본 R/B, 1/1.35, 1.35),
    target·control 에 같이 걸고 mask 는 그대로, 캡션도 같다.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import math
import re
import tarfile
import unicodedata as ud
from dataclasses import dataclass

import numpy as np
from PIL import Image

from ..agents import face_identity as fi
from ..facemarket_photos import (
    REFSET_SLOTS, TRAINING_SLOTS, export_name, resolve_photo_rows,
)

log = logging.getLogger(__name__)

#: tgz 안의 최상위 폴더. 학습 설정(armA_v7.yaml)의 /root/<이 이름>/{target,control,mask} 과 짝이다.
DATASET_ROOT = "lora_train"
#: 조명 이름 → 캡션 어휘. 원본 LIGHT 그대로다. 키는 export_name 의 접두어(그늘·해가왼쪽…)라
#: 이름 규칙(facemarket_photos)이 바뀌면 여기서 KeyError 로 바로 드러난다 — 조용히 틀린 캡션이
#: 학습에 들어가는 것보다 낫다.
LIGHT = {
    "그늘": "soft shaded daylight",
    "해가왼쪽": "directional sunlight",
    "해가오른쪽": "directional sunlight",
    "해등지고": "backlit daylight",
}
#: 색온도 증강. 파일별 시드는 4000 + (TRAINING_SLOTS 안에서의 자리).
AUG_SEED_BASE = 4000
AUG_TARGET_RB = (1.15, 1.45)
AUG_FACTOR_CAP = 1.35


class DatasetBuildError(RuntimeError):
    """학습셋을 만들 수 없다 — 반쪽 학습셋은 되돌릴 수 없는 품질 손실이라 **만들다 만 것을 올리지 않는다**."""


# ── 검출 · 캡션 · 증강 (원본 규칙) ──────────────────────────────────────────


def detect4(image: Image.Image, model_dir: str | None = None):
    """YuNet 을 ×4 축소본에서 돌리고 좌표를 ×4 로 되돌린다. 반환 (box, yaw_proxy, eye_dist) 또는 None.

    등록 사진은 길어야 4096px 이라 전체 해상도로 돌리면 느리고, 작은 얼굴을 오히려 놓친다.
    원본 스크립트가 쓰던 그 규칙 그대로다.
    """
    import cv2

    bgr = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    height, width = bgr.shape[:2]
    if width < 8 or height < 8:
        return None
    small = cv2.resize(bgr, (max(1, width // 4), max(1, height // 4)))
    detector = fi._detector(model_dir)
    with fi._DET_LOCK:
        detector.setInputSize((small.shape[1], small.shape[0]))
        _, faces = detector.detect(small)
    if faces is None or len(faces) == 0:
        return None
    face = faces[int(np.argmax(faces[:, 2] * faces[:, 3]))].copy()
    face[:14] *= 4
    eye = float(np.hypot(face[4] - face[6], face[5] - face[7])) or 1.0
    mid = (face[4:6] + face[6:8]) / 2
    return tuple(float(v) for v in face[:4]), abs(float(face[8] - mid[0])) / eye, eye


def caption(stem: str, yaw: float) -> tuple[str, str, str | None, str]:
    """`<조명>__<컷>` 이름 + yaw → (캡션, 조명키, 표정, 각도). 원본 caption() 그대로다.

    파일 이름을 파싱하는 이유: 슬롯 → 이름 → 캡션이 한 규칙이라, 이름이 바뀌면 캡션도 같이 바뀐다
    (facemarket_photos.export_name 이 그 이름의 정본이다).
    """
    cond, pose = ud.normalize("NFC", stem).split("__", 1)
    tokens = re.split(r"[_,()]", pose)
    if "입" in tokens and "다문" in tokens and "미소" in tokens:
        expression = "slight smile"
    elif "미소" in tokens:
        expression = "smiling"
    elif any(token.startswith("무표정") for token in tokens):
        expression = "neutral expression"
    else:
        expression = None                      # ★ 추정 금지 — 없는 표정을 지어내지 않는다
    side = "left" if "왼쪽" in tokens else ("right" if "오른쪽" in tokens else None)
    if yaw <= 0.16:
        angle = "facing the camera"
    elif yaw <= 0.45:
        angle = "turned three-quarters" + (f" to the {side}" if side else "")
    else:
        angle = "in profile" + (f", facing {side}" if side else "")
    try:
        light = LIGHT[cond]
    except KeyError:
        raise DatasetBuildError(f"조명 어휘를 모르는 이름: {stem}") from None
    parts = (["ohwx man"] + ([expression] if expression else [])
             + ["head and shoulders portrait", angle, "outdoors", light, "photograph"])
    return ", ".join(parts), cond, expression, angle


def caption_for_slot(slot: str, yaw: float) -> tuple[str, str, str | None, str]:
    """슬롯 → 캡션. 이름(export_name)을 거쳐 **원본과 같은 파서**를 탄다."""
    name = export_name(slot)
    if not name:
        raise DatasetBuildError(f"이름 없는 슬롯: {slot}")
    return caption(name, yaw)


def red_blue(image: Image.Image) -> float:
    array = np.asarray(image.convert("RGB")).astype(np.float64)
    return array[..., 0].mean() / max(array[..., 2].mean(), 1)


def apply_color_temp(image: Image.Image, factor: float) -> Image.Image:
    array = np.asarray(image.convert("RGB")).astype(np.float32) / 255.0
    array[..., 0] *= math.sqrt(factor)
    array[..., 2] /= math.sqrt(factor)
    return Image.fromarray(np.clip(array * 255 + 0.5, 0, 255).astype(np.uint8))


def color_temp_factor(index: int, rb_original: float) -> tuple[float, float]:
    """(목표 R/B, 배율). 시드는 자리 번호라 같은 입력이면 **같은 바이트**가 나온다."""
    rng = np.random.default_rng(AUG_SEED_BASE + int(index))
    target = float(rng.uniform(*AUG_TARGET_RB))
    return target, float(np.clip(target / max(rb_original, 1e-6), 1 / AUG_FACTOR_CAP, AUG_FACTOR_CAP))


# ── 빌드 ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SourcePhoto:
    """정규화본(EXIF 적용 PNG) 바이트 한 장. 파일 경로가 아니라 **바이트**다 — 디스크에 안 남긴다."""

    slot: str
    data: bytes


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "PNG")
    return buffer.getvalue()


def _gray_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("L").save(buffer, "PNG")
    return buffer.getvalue()


def build_items(photos, *, model_dir: str | None = None) -> tuple[list[tuple[str, bytes]], dict]:
    """학습 12장 → tar 멤버 목록 + meta. 한 장이라도 얼굴을 못 찾으면 **만들다 만 것을 안 내놓는다**.

    반쪽 학습셋은 조용한 품질 손실이다 — 11장으로 학습해도 돌긴 도는데, 왜 닮지 않는지는
    나중에 아무도 못 찾는다.
    """
    by_slot = {photo.slot: photo for photo in photos}
    missing = [slot for slot in TRAINING_SLOTS if slot not in by_slot]
    if missing:
        raise DatasetBuildError(f"학습 사진이 빠졌다: {', '.join(missing)}")

    members: list[tuple[str, bytes]] = []
    meta: dict = {"root": DATASET_ROOT, "train": {}, "captions": {}, "aug": {}}
    for index, slot in enumerate(TRAINING_SLOTS):
        with Image.open(io.BytesIO(by_slot[slot].data)) as opened:
            if opened.getexif().get(274) not in (None, 1):
                # 정규화본은 EXIF 를 픽셀에 적용해 둔 것이다 — 태그가 남아 있으면 그 사진은
                # 정규화본이 아니고, 검출과 크롭이 서로 다른 그림을 본다(2026-08 '칼 사태').
                raise DatasetBuildError(f"EXIF orientation 이 남아 있다: {slot}")
            image = opened.convert("RGB")
        found = detect4(image, model_dir)
        if found is None:
            raise DatasetBuildError(f"얼굴을 못 찾았다: {slot}")
        box, yaw, eye = found
        plan = fi.plan_from_box(image.size[0], image.size[1], box,
                                yaw_proxy=round(yaw, 3), eye_dist=round(eye, 1))
        # ★ 기하는 살아 있는 서비스 함수 그대로다(사본 없음) — 추론이 쓰는 것과 같아야 한다.
        target = fi.crop_1024(image, plan)
        control = fi.build_control(image, plan, crop=target)
        mask = fi.feather_mask(plan)

        text, cond, expression, angle = caption_for_slot(slot, yaw)
        members += [(f"target/{slot}.png", _png(target)),
                    (f"control/{slot}.png", _png(control)),
                    (f"mask/{slot}.png", _gray_png(mask)),
                    (f"target/{slot}.txt", (text + "\n").encode())]

        rb_original = red_blue(target)
        wanted, factor = color_temp_factor(index, rb_original)
        aug_target, aug_control = apply_color_temp(target, factor), apply_color_temp(control, factor)
        members += [(f"target/{slot}_aug.png", _png(aug_target)),
                    (f"control/{slot}_aug.png", _png(aug_control)),
                    # 마스크는 그대로다 — 색만 바꿨으니 얼굴 위치는 같다.
                    (f"mask/{slot}_aug.png", _gray_png(mask)),
                    (f"target/{slot}_aug.txt", (text + "\n").encode())]

        meta["captions"][slot] = text
        meta["train"][slot] = {
            "cond": cond, "expr": expression, "angle": angle, "yaw": round(yaw, 3),
            "box": [round(value) for value in box], "crop": list(plan.crop),
            "scale": round(plan.upscale, 2), "face_w_crop": round(plan.face_box_crop[2], 1),
            # 살아 있는 feather_mask 의 면적. v6 는 고정 사본(≈24%)으로 만들었고 지금 것은 더
            # 넓다 — 손실 가중 영역이 달라지는 유일한 자리라 숫자로 남긴다.
            "mask_area": round(float((np.asarray(mask) > 127).mean()), 4),
        }
        meta["aug"][slot] = {"target_rb": round(wanted, 3), "rb_orig": round(rb_original, 3),
                             "factor": round(factor, 3),
                             "rb_result": round(red_blue(aug_target), 3),
                             "capped": abs(factor - AUG_FACTOR_CAP) < 1e-6
                             or abs(factor - 1 / AUG_FACTOR_CAP) < 1e-6}
    return members, meta


def build_samples(photos, *, model_dir: str | None = None) -> list[tuple[str, bytes]]:
    """표본 control — **기준 3장(REFSET_SLOTS)에서만** 딴다. 학습컷은 쓰지 않는다.

    학습 중 찍히는 표본 이미지는 가중치에 영향이 없다(그냥 눈으로 보는 것). 그래도 학습컷으로
    만들면 "학습한 그림을 학습한 대로 다시 그렸다"만 보게 돼 아무것도 못 읽는다.
    """
    by_slot = {photo.slot: photo for photo in photos}
    out: list[tuple[str, bytes]] = []
    for slot in REFSET_SLOTS:
        photo = by_slot.get(slot)
        if photo is None:
            continue                      # 기준 사진은 없어도 학습은 돈다 — 표본만 줄어든다
        with Image.open(io.BytesIO(photo.data)) as opened:
            image = opened.convert("RGB")
        found = detect4(image, model_dir)
        if found is None:
            log.info("lora_dataset: 표본 기준 사진에서 얼굴을 못 찾았다(%s) — 건너뛴다", slot)
            continue
        box, yaw, eye = found
        plan = fi.plan_from_box(image.size[0], image.size[1], box,
                                yaw_proxy=round(yaw, 3), eye_dist=round(eye, 1))
        out.append((f"samples_ctrl/{slot}.png", _png(fi.build_control(image, plan))))
    return out


def pack(members, meta: dict) -> bytes:
    """tar.gz 를 **메모리에서** 만든다. 같은 입력이면 같은 바이트여야 한다.

    mtime·소유자·gzip 헤더 시각을 전부 고정한다 — 안 하면 같은 사진으로 돌려도 초가 넘어가는
    순간 sha 가 달라져, "이미 만든 학습셋" 을 판단할 수 없다(번들 스크립트에서 겪은 그 문제).
    """
    payload = sorted(list(members) + [("meta.json",
                                       json.dumps(meta, ensure_ascii=False, indent=1).encode())])
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name, data in payload:
            info = tarfile.TarInfo(f"{DATASET_ROOT}/{name}")
            info.size = len(data)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as zipped:
        zipped.write(raw.getvalue())
    return out.getvalue()


def build_dataset(photos, *, model_dir: str | None = None) -> tuple[bytes, dict]:
    """학습셋 tgz 바이트 + meta. 디스크를 안 쓴다."""
    members, meta = build_items(photos, model_dir=model_dir)
    samples = build_samples(photos, model_dir=model_dir)
    meta["samples"] = [name.split("/", 1)[1] for name, _ in samples]
    meta["files"] = len(members) + len(samples) + 1
    return pack(members + samples, meta), meta


# ── R2 ↔ 파드 사이에서만 흐른다 ─────────────────────────────────────────────


def dataset_key(model_id: str, run_id: str) -> str:
    return f"facemarket/models/{model_id}/training/{run_id}/dataset.tgz"


def _readable_key(row: dict) -> str | None:
    """이 사진을 **읽을 때** 쓸 키. 정규화본(EXIF 적용 PNG)이 정본이고, 없는 옛 행만 원본이다."""
    for field in ("normalized_r2_key", "r2_key"):
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return None


def load_photos(r2_face, rows, slots) -> list[SourcePhoto]:
    """R2 에서 바이트만 읽어 온다. 파일로 떨어뜨리지 않는다(생체정보)."""
    out: list[SourcePhoto] = []
    for slot in slots:
        resolved = resolve_photo_rows(rows, (slot,))
        row = resolved[0] if resolved else None
        if row is None or row.get("qc_status") != "passed" \
                or row.get("storage_state") not in ("quarantine", "approved"):
            continue
        key = _readable_key(row)
        if not key:
            continue
        out.append(SourcePhoto(slot, r2_face.get_bytes(key)))
    return out


def assert_ready(review_status: str | None, photos) -> None:
    """학습셋을 만들어도 되는가. **둘 다** 아니면 만들지 않는다.

    · 관리자 사진 확인 전이면 안 된다 — 반려될 사진이 가중치에 들어가면 되돌릴 방법이 재학습뿐이다.
    · 12장이 아니면 안 된다 — 반쪽 학습셋은 조용한 품질 손실이다.
    """
    if review_status != "approved":
        raise DatasetBuildError(
            f"관리자 사진 확인이 끝나지 않았다(photo_review_status={review_status!r})")
    have = {photo.slot for photo in photos}
    missing = [slot for slot in TRAINING_SLOTS if slot not in have]
    if missing:
        raise DatasetBuildError(
            f"학습 사진이 {len(TRAINING_SLOTS) - len(missing)}장뿐이다 — 빠진 칸: {', '.join(missing)}")


def build_and_upload(r2_face, *, model_id: str, run_id: str, rows, review_status: str | None,
                     model_dir: str | None = None, apply: bool = False) -> dict:
    """등록 사진 → 학습셋 tgz → R2. 반환은 **키와 숫자만** — 바이트도 URL 도 안 싣는다."""
    photos = load_photos(r2_face, rows, tuple(TRAINING_SLOTS) + tuple(REFSET_SLOTS))
    assert_ready(review_status, photos)
    payload, meta = build_dataset(photos, model_dir=model_dir)
    key = dataset_key(model_id, run_id)
    digest = hashlib.sha256(payload).hexdigest()
    result = {"key": key, "bytes": len(payload), "sha256": digest,
              "files": meta["files"], "train": len(meta["train"]),
              "samples": len(meta["samples"]), "applied": bool(apply)}
    if apply:
        r2_face.put_bytes(key, payload, "application/gzip")
    log.info("lora_dataset built model=%s run=%s files=%d bytes=%d sha12=%s applied=%s",
             model_id, run_id, meta["files"], len(payload), digest[:12], apply)
    return result
