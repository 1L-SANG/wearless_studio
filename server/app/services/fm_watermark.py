"""FaceMarket 배포본 비가시 워터마크 — 추적 층(2026-09-26).

무엇: 셀러가 내려받는 배포본(긴 PNG·블록 PNG·ZIP 속 PNG) 픽셀에 짧은 배포 코드(32비트)를
눈에 안 보이게 페이지 전체에 반복해서 박는다. 쇼핑몰에서 발견한 이미지 한 조각(세로 약
800~1200px)만 있어도 코드를 읽어 "어느 배포본 → 셀러·모델·라이선스"를 찾는다. 셀러에게
스토어 주소 등록을 요구하지 않는 게 이 층의 전제다(오너 2026-09-26).

왜 라이브러리가 아니라 직접 구현인가(2026-09-26 실측):
  - invisible-watermark 0.2.0(MIT): dwtDct 만 써도 패키지 import 가 torch 를 끌어온다. 요구
    목록에 torch·onnxruntime·opencv-python(비 headless)이 있다 — 비 headless 는 우리
    opencv-contrib-python-headless 와 같은 `cv2` 자리를 덮어쓰고, python:3.12-slim 에는 libGL 도
    없다. 게다가 dwtDct 는 비트 위상이 래스터 위치에 묶여 있어 합성 상세페이지 실측에서
    1000px 조각 0/10 · 860폭 축소 0/10 · JPEG85 0/10 — "조각 단독 판독" 요구를 못 맞춘다.
  - blind-watermark 0.4.4(MIT): opencv-python(비 headless) 의존 — 같은 cv2 충돌.
  그래서 새 의존성 없이(numpy + 이미 prod 이미지에 있는 opencv-contrib-python-headless) 고전
  기법인 주기 타일 확산 스펙트럼(Kutter 1998 자기참조 타일 + Cox 1997 확산 스펙트럼)을 쓴다.

방식:
  1. 기준 좌표 = 폭 860(쇼핑몰 상세 기준 폭). 배포본(보통 2000폭)은 이 좌표로 패턴을 샘플링해
     밝기에 더한다 — R·G·B 에 같은 값을 더하면 색차는 그대로고 밝기(Y)만 움직인다.
  2. 126×126 타일(3×3 칩 42×42개)을 페이지 전체에 반복한다. 칩 25%는 동기(부호 고정), 75%는
     48비트(코드 32 + CRC-16)를 나눠 싣는다. 부호·배치는 SHA-256 스트림에서 나온다 — numpy
     난수는 버전 간 재현이 보장되지 않아, 업그레이드 한 번에 이미 나간 파일을 못 읽게 된다.
  3. 세기는 지각 마스크 — 평탄한 곳(흰 배경) ±1 레벨, 질감 있는 곳(사진) 최대 ±3 레벨.
  4. 판독: 폭을 860으로 맞춤 → 고역 잔차 국소 정규화 → 타일 주기로 접어(fold) 합산 →
     동기 템플릿과 FFT 순환상관으로 타일 위상(dy, dx) 탐색 → 비트 복원 → CRC 확인.
     그래서 페이지 어느 높이에서 잘라도 조각이 스스로 위상을 찾는다.

한계: 가로로 잘리거나 여백이 붙어 폭 기준이 바뀌면(배율 불일치) 못 읽는다 — 관리자 화면이
"이미지 영역만, 전체 폭으로" 올리라고 안내한다. 좌우반전·회전·강한 필터도 대상 밖이다.

🔴 레이아웃(_SEED·TILE·CHIP·NBITS·_SYNC_CHIPS)을 바꾸면 이미 나간 파일을 못 읽는다. 바꿔야 하면
   VERSION 을 올리고 판독기가 옛 버전도 시도하게 한다. tests/test_fm_watermark.py 의 골든
   해시가 무심코 바꾸는 걸 먼저 막는다.

동기 함수만 노출한다. 호출부가 asyncio.to_thread 로 감싼다(2026-08-26 이벤트루프 동결).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

VERSION = 1
CANON_W = 860            # 판독·패턴 좌표계의 폭(쇼핑몰 상세 기준 폭)
TILE = 126               # 기준 좌표에서의 타일 한 변(px)
CHIP = 3                 # 칩 한 변(px) — 640폭 축소·JPEG50 을 견디는 가장 작은 크기(실측)
NCHIP = TILE // CHIP     # 42
CODE_BITS = 32
NBITS = CODE_BITS + 16   # 코드 + CRC-16
_SYNC_CHIPS = (NCHIP * NCHIP) // 4
_SEED = b"wearless/facemarket/watermark/v1"

ALPHA_FLAT = 1.0         # 평탄 영역 세기(8비트 레벨). 흰 배경에서 255→254 수준
ALPHA_TEXTURE = 3.0      # 질감 영역 최대 세기
_ACTIVITY_REF = 12.0     # 국소 표준편차가 이 값 이상이면 질감으로 본다
_BAND_ROWS = 1024        # 풀해상도 처리 띠 높이 — 2000×30000 에서도 메모리를 띠 단위로 묶는다

MAX_PIXELS = 80_000_000  # 2000×30000(에디터 한계 60M px) 여유. 그 이상은 거부
READ_STRIP = 1000        # 판독 창 높이(기준 좌표). 전체 이미지 창과 함께 시도한다
READ_STEP = 500
_PEAKS = 4
#: 동기 상관 피크의 z 점수 하한. 실측(2026-09-26, 합성 상세페이지 800~1200px 조각 60개):
#: 워터마크 없는 조각 최대 5.5(p90 4.6), 박힌 조각은 JPEG40@640·WebP80@640 에서도 최소 7.4
#: (p10 10.3). 진짜 판별은 CRC-16(우연 통과 2^-16)이 하고, 이 하한은 헛시도를 줄이는 1차 거름이다.
MIN_SYNC_Z = 5.0


def _stream(label: bytes, n: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(_SEED + b"/" + label + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(out[:n])


def _build_layout():
    n = NCHIP * NCHIP
    signs = np.where(np.frombuffer(_stream(b"signs", n), dtype=np.uint8) & 1, 1.0, -1.0)
    signs = signs.astype(np.float32).reshape(NCHIP, NCHIP)
    raw = np.frombuffer(_stream(b"perm", 4 * n), dtype=">u4")
    perm = list(range(n))
    for i in range(n - 1, 0, -1):          # Fisher–Yates, 결정적
        j = int(raw[i]) % (i + 1)
        perm[i], perm[j] = perm[j], perm[i]
    perm = np.array(perm, dtype=np.int64)
    sync_idx = perm[:_SYNC_CHIPS]
    pay_idx = perm[_SYNC_CHIPS:]
    pay_bit = np.arange(len(pay_idx), dtype=np.int64) % NBITS
    whiten = (np.frombuffer(_stream(b"whiten", NBITS), dtype=np.uint8) & 1).astype(np.int64)
    sync = np.zeros(n, dtype=np.float32)
    sync[sync_idx] = 1.0
    sync_tile = np.kron(sync.reshape(NCHIP, NCHIP) * signs, np.ones((CHIP, CHIP), np.float32))
    return {
        "signs": signs, "sync_idx": sync_idx, "pay_idx": pay_idx, "pay_bit": pay_bit,
        "whiten": whiten, "sync_fft_conj": np.conj(np.fft.fft2(sync_tile)),
    }


_L = _build_layout()


def layout_fingerprint() -> str:
    """레이아웃 골든 해시 — 이 값이 바뀌면 이미 나간 파일을 못 읽는다(테스트가 고정한다)."""
    h = hashlib.sha256()
    for key in ("signs", "sync_idx", "pay_idx", "pay_bit", "whiten"):
        h.update(np.ascontiguousarray(_L[key]).tobytes())
    return h.hexdigest()


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def new_code() -> int:
    """1 ≤ code < 2^32. 원장 unique 인덱스가 충돌을 막고, 호출부가 충돌 시 다시 뽑는다."""
    while True:
        code = secrets.randbits(CODE_BITS)
        if code:
            return code


def _code_bits(code: int) -> np.ndarray:
    if not 0 < code < (1 << CODE_BITS):
        raise ValueError("code out of range")
    val = (code << 16) | crc16(code.to_bytes(4, "big"))
    bits = np.array([(val >> (NBITS - 1 - i)) & 1 for i in range(NBITS)], dtype=np.int64)
    return bits ^ _L["whiten"]


def _bits_code(bits: np.ndarray) -> tuple[int, bool]:
    bits = np.asarray(bits, dtype=np.int64) ^ _L["whiten"]
    val = 0
    for b in bits:
        val = (val << 1) | int(b)
    code, crc = val >> 16, val & 0xFFFF
    return code, code != 0 and crc16(code.to_bytes(4, "big")) == crc


def _tile_pattern(code: int) -> np.ndarray:
    bits = _code_bits(code)
    flat = np.ones(NCHIP * NCHIP, dtype=np.float32)
    flat[_L["pay_idx"]] = np.where(bits[_L["pay_bit"]] == 1, 1.0, -1.0)
    chips = flat.reshape(NCHIP, NCHIP) * _L["signs"]
    return np.kron(chips, np.ones((CHIP, CHIP), np.float32)).astype(np.float32)


def _luma(rgb: np.ndarray) -> np.ndarray:
    rgb = rgb.astype(np.float32)
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def _alpha_mask(rgb: np.ndarray, win: int) -> np.ndarray:
    """지각 마스크 — 평탄한 곳은 약하게, 질감 있는 곳은 강하게."""
    y = _luma(rgb)
    m = cv2.blur(y, (win, win))
    m2 = cv2.blur(y * y, (win, win))
    sd = np.sqrt(np.maximum(m2 - m * m, 0.0))
    act = np.clip(sd / _ACTIVITY_REF, 0.0, 1.0)
    return (ALPHA_FLAT + (ALPHA_TEXTURE - ALPHA_FLAT) * act).astype(np.float32)


def embed_array(rgb: np.ndarray, code: int) -> np.ndarray:
    """(H, W, 3) uint8 RGB 에 코드를 박는다 — **제자리 수정**(메모리 절약) 후 같은 배열을 돌려준다.

    풀해상도 픽셀 중심을 기준 좌표로 옮겨 타일 패턴을 쌍선형 샘플링한다(BORDER_WRAP = 주기).
    마스크는 원본 픽셀로 계산해야 하므로 띠 경계 위쪽 여백 행은 수정 전 사본을 쓴다.
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError("expected HxWx3 uint8")
    H, W = rgb.shape[:2]
    pat = _tile_pattern(code)
    k = CANON_W / W
    xs = (((np.arange(W, dtype=np.float64) + 0.5) * k - 0.5) % TILE).astype(np.float32)
    win = max(3, int(round(4 * W / CANON_W)) | 1)   # 기준 좌표 약 4px 창
    margin = win
    prev_tail = None                                 # 직전 띠의 수정 전 꼬리 행
    for y0 in range(0, H, _BAND_ROWS):
        y1 = min(H, y0 + _BAND_ROWS)
        rows = y1 - y0
        ys = (((np.arange(y0, y1, dtype=np.float64) + 0.5) * k - 0.5) % TILE).astype(np.float32)
        mx = np.ascontiguousarray(np.broadcast_to(xs[None, :], (rows, W)))
        my = np.ascontiguousarray(np.broadcast_to(ys[:, None], (rows, W)))
        delta = cv2.remap(pat, mx, my, interpolation=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_WRAP)
        below = rgb[y1:min(H, y1 + margin)]
        ctx = [part for part in (prev_tail, rgb[y0:y1], below) if part is not None and len(part)]
        ctx_arr = np.concatenate(ctx, axis=0) if len(ctx) > 1 else ctx[0]
        top = 0 if prev_tail is None else len(prev_tail)
        delta *= _alpha_mask(ctx_arr, win)[top:top + rows]
        prev_tail = rgb[max(y0, y1 - margin):y1].copy()
        band = rgb[y0:y1].astype(np.float32)
        band += delta[:, :, None]
        np.rint(band, out=band)
        np.clip(band, 0, 255, out=band)
        rgb[y0:y1] = band.astype(np.uint8)
    return rgb


def check_size(img: Image.Image) -> None:
    if img.width <= 0 or img.height <= 0 or img.width * img.height > MAX_PIXELS:
        raise ValueError("image too large")


def embed_image(img: Image.Image, code: int) -> Image.Image:
    """PIL 이미지 → 워터마크가 든 새 이미지. 알파가 있으면 알파는 그대로 붙여 돌려준다."""
    check_size(img)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    alpha = img.convert("RGBA").getchannel("A") if has_alpha else None
    if alpha is not None and alpha.getextrema() == (255, 255):
        # 브라우저 canvas.toBlob PNG 는 전부 불투명인 RGBA 다 — 알파를 버려 메모리·파일을 줄인다.
        alpha = None
    rgb = np.array(img.convert("RGB"), dtype=np.uint8)   # 쓰기 가능한 사본
    embed_array(rgb, code)
    out = Image.fromarray(rgb)
    if alpha is not None:
        out.putalpha(alpha)
    return out


# ── 판독 ─────────────────────────────────────────────────────────────────────


@dataclass
class WatermarkRead:
    code: int | None = None          # 표를 가장 많이 받은 코드(CRC·동기 z 통과분만)
    votes: int = 0
    windows: int = 0                 # 시도한 창 수
    best_z: float = 0.0
    codes: dict[int, int] = field(default_factory=dict)


def canonical_luma(img: Image.Image) -> np.ndarray:
    """알파는 흰 바탕에 합성(쇼핑몰 표시와 같다) → 밝기 → 폭 860."""
    check_size(img)
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, (255, 255, 255))
        base.paste(rgba, mask=rgba.getchannel("A"))
        img = base
    y = _luma(np.asarray(img.convert("RGB")))
    H, W = y.shape
    h = max(1, int(round(H * CANON_W / W)))
    interp = cv2.INTER_AREA if W > CANON_W else cv2.INTER_CUBIC
    return cv2.resize(y, (CANON_W, h), interpolation=interp)


def _residuals(y: np.ndarray):
    """고역 잔차 두 가지 — 국소 정규화(기본)와 진폭 클립(대안). 앞의 것으로 못 읽을 때만 뒤를 쓴다."""
    r = y - cv2.GaussianBlur(y, (0, 0), 1.5)
    yield np.clip(r / np.sqrt(cv2.blur(r * r, (15, 15)) + 1.0), -3.0, 3.0)
    yield np.clip(r, -4.0, 4.0)


def _fold(r: np.ndarray) -> np.ndarray:
    h, w = r.shape
    hp, wp = -(-h // TILE) * TILE, -(-w // TILE) * TILE
    pad = np.zeros((hp, wp), np.float32)
    cnt = np.zeros((hp, wp), np.float32)
    pad[:h, :w] = r
    cnt[:h, :w] = 1.0
    F = pad.reshape(hp // TILE, TILE, wp // TILE, TILE).sum(axis=(0, 2))
    N = cnt.reshape(hp // TILE, TILE, wp // TILE, TILE).sum(axis=(0, 2))
    return F / np.maximum(N, 1.0)


def _decode_fold(F: np.ndarray) -> tuple[int | None, float]:
    """접힌 타일 → (CRC 통과 코드 | None, 최고 동기 z)."""
    C = np.real(np.fft.ifft2(np.fft.fft2(F) * _L["sync_fft_conj"]))
    flat = C.ravel()
    mean, std = float(flat.mean()), float(flat.std()) + 1e-12
    order = np.argsort(flat)[::-1]
    tried: list[tuple[int, int]] = []
    best_z = (float(flat[order[0]]) - mean) / std
    for idx in order[:64]:
        dy, dx = divmod(int(idx), TILE)
        if any(min((dy - a) % TILE, (a - dy) % TILE) < 2
               and min((dx - b) % TILE, (b - dx) % TILE) < 2 for a, b in tried):
            continue
        tried.append((dy, dx))
        z = (float(flat[idx]) - mean) / std
        if z < MIN_SYNC_Z:
            break
        A = np.roll(F, (-dy, -dx), axis=(0, 1))
        chips = A.reshape(NCHIP, CHIP, NCHIP, CHIP).sum(axis=(1, 3)) * _L["signs"]
        soft = np.bincount(_L["pay_bit"], weights=chips.ravel()[_L["pay_idx"]], minlength=NBITS)
        code, ok = _bits_code((soft > 0).astype(np.int64))
        if ok:
            return code, best_z
        if len(tried) >= _PEAKS:
            break
    return None, best_z


def _windows(h: int) -> list[tuple[int, int]]:
    out = [(0, h)]
    if h > READ_STRIP * 1.2:
        for y0 in range(0, h - READ_STRIP + 1, READ_STEP):
            out.append((y0, y0 + READ_STRIP))
        if out[-1][1] < h:
            out.append((h - READ_STRIP, h))
    return out


def read_luma(y: np.ndarray) -> WatermarkRead:
    result = WatermarkRead()
    wins = _windows(y.shape[0])
    for r in _residuals(y):
        for a, b in wins:
            code, z = _decode_fold(_fold(r[a:b]))
            result.windows += 1
            result.best_z = max(result.best_z, z)
            if code is not None:
                result.codes[code] = result.codes.get(code, 0) + 1
        if result.codes:
            break
    if result.codes:
        result.code, result.votes = max(result.codes.items(), key=lambda kv: kv[1])
    return result


def read_image(img: Image.Image) -> WatermarkRead:
    return read_luma(canonical_luma(img))
