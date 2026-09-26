"""FaceMarket 지각 해시 지문 — 추적 층의 두 번째 줄(2026-09-26).

워터마크가 지워져도(강한 재압축·필터·재촬영) 발견 이미지를 원장에 이어 붙이기 위해, 배포본
전체·세로 띠(strip)·REAL 컷 1장마다 64비트 pHash + dHash 를 원장에 남긴다.

imagehash 와의 관계: 값은 **imagehash 4.3.x 의 phash()/dhash() 와 비트 단위로 같다**
(tests/test_fm_fingerprint.py 가 imagehash 가 깔린 환경에서 직접 대조한다). 라이브러리 자체를
prod 에 넣지 않은 이유는 크기다 — imagehash 는 scipy(휠 35MB, 설치 ~110MB)와 PyWavelets 를
필수로 끌어오는데, 우리가 쓰는 건 32×32 DCT 하나라 numpy 행렬곱 몇 줄이면 된다. 알고리즘을
바꾸고 싶으면 imagehash.phash 로 한 줄 교체해도 저장값은 그대로 호환된다.

띠 설계(실측, 합성 상세페이지): pHash 는 세로 이동에 민감하다 — 25px 어긋나면 평균 6비트,
50px 면 11비트, 100px 면 23비트가 뒤집힌다(남의 페이지와의 거리 최소 14~16). 그래서 원장에는
높이 700 띠를 100 간격으로 촘촘히 두고(~86% 겹침), 조회 때는 발견 이미지를 20px 간격으로
훑는다 — 발견 조각이 800px 이상이면 어긋남이 10px 이내인 짝이 반드시 생긴다(거리 0~4).
오너 지정 "1000px·50% 겹침"은 800~1000px 조각에서 최대 250px 어긋나 매칭이 안 돼서 바꿨다
(800·100 간격은 800~900px 조각에서 최대 거리 10~12, 700·100 간격은 최대 4 — 행 수는 같다).
좌표는 전부 폭 860 정규화 기준이다.

동기 함수만 노출한다. 호출부가 asyncio.to_thread 로 감싼다.
"""

from __future__ import annotations

import io
import logging

import numpy as np
from PIL import Image

logger = logging.getLogger("facemarket.fingerprint")

NORM_W = 860
STRIP = 700
STRIP_STEP = 100
QUERY_STEP = 20
QUERY_STEP_LONG = 40         # 아주 긴 발견 이미지(>4000px)는 창 수를 줄인다
QUERY_LONG = 4000
#: 32×32 썸네일 표준편차가 이보다 작으면(거의 빈 흰 띠) 저장·조회하지 않는다 — 서로 다른
#: 페이지의 빈 여백끼리 가깝게 나와 헛후보가 되는 것을 막는다.
LOW_INFO_STD = 3.0
#: 매칭 한도 — 합성 페이지 실측에서 같은 페이지(재압축·축소·어긋남 ≤10px)는 pHash 0~2,
#: 다른 페이지는 최소 14. 둘 다 넘으면 버린다.
MAX_PHASH = 10
MAX_DHASH = 16
STRONG_PHASH = 4
MAX_PIXELS = 80_000_000

_U64 = 1 << 64


def _dct_matrix(n: int) -> np.ndarray:
    """scipy.fftpack.dct(type=2, norm=None) 와 같은 비정규화 DCT-II 행렬."""
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    return 2.0 * np.cos(np.pi * k * (2 * i + 1) / (2 * n))


_D32 = _dct_matrix(32)


def _bits_to_int(bits: np.ndarray) -> int:
    v = 0
    for b in bits.ravel():
        v = (v << 1) | int(b)
    return v


def _hashes(img: Image.Image) -> tuple[int, int, float]:
    """(phash, dhash, 32×32 썸네일 표준편차). imagehash 와 같은 변환 순서를 지킨다."""
    gray = img.convert("L")
    px = np.asarray(gray.resize((32, 32), Image.LANCZOS), dtype=np.float64)
    # 행렬곱 DCT 는 FFTPACK 과 1e-12 수준 잡음이 다르다 — 단색 이미지처럼 AC 가 전부 0 인 경우에만
    # 그 잡음이 비트를 가른다. 소수 6자리로 반올림해 0 을 정확히 0 으로 만든다(imagehash 와 동일 결과).
    low = np.round((_D32 @ px @ _D32.T)[:8, :8], 6)
    ph = _bits_to_int(low > np.median(low))
    dpx = np.asarray(gray.resize((9, 8), Image.LANCZOS), dtype=np.int16)
    dh = _bits_to_int(dpx[:, 1:] > dpx[:, :-1])
    return ph, dh, float(px.std())


def phash(img: Image.Image) -> int:
    return _hashes(img)[0]


def dhash(img: Image.Image) -> int:
    return _hashes(img)[1]


def to_signed(v: int) -> int:
    """부호 없는 64비트 → Postgres bigint."""
    return v - _U64 if v >= (1 << 63) else v


def to_unsigned(v: int) -> int:
    return v + _U64 if v < 0 else v


def open_image(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    if img.width <= 0 or img.height <= 0 or img.width * img.height > MAX_PIXELS:
        raise ValueError("image too large")
    img.load()
    return img


def safe_image_hashes(data: bytes) -> dict | None:
    """컷 1장 지문(베스트에포트). 실패하면 None — 컷 종결을 절대 막지 않는다."""
    try:
        ph, dh, _std = _hashes(open_image(data))
        return {"phash": ph, "dhash": dh}
    except Exception:
        logger.warning("cut fingerprint failed", exc_info=True)
        return None


def normalize(img: Image.Image) -> Image.Image:
    """폭 860 그레이스케일(알파는 흰 바탕 합성). 저장·조회가 같은 함수를 써야 거리가 맞는다."""
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, (255, 255, 255))
        base.paste(rgba, mask=rgba.getchannel("A"))
        img = base
    gray = img.convert("L")
    h = max(1, int(round(gray.height * NORM_W / gray.width)))
    return gray.resize((NORM_W, h), Image.LANCZOS)


def stack(images: list[Image.Image]) -> tuple[Image.Image, list[tuple[int, int]]]:
    """정규화 이미지들을 세로로 잇는다(ZIP 블록 → 긴 페이지 좌표). (이미지, 각 블록 구간)."""
    total = sum(im.height for im in images)
    out = Image.new("L", (NORM_W, max(1, total)), 255)
    spans = []
    y = 0
    for im in images:
        out.paste(im, (0, y))
        spans.append((y, y + im.height))
        y += im.height
    return out, spans


def _strip_starts(h: int, size: int, step: int) -> list[int]:
    if h <= size:
        return [0]
    starts = list(range(0, h - size + 1, step))
    if starts[-1] != h - size:
        starts.append(h - size)            # 바닥까지 덮는다
    return starts


def publication_fingerprints(normalized: list[Image.Image]) -> list[dict]:
    """배포본 지문. normalized = normalize() 를 거친 이미지들(긴 PNG 는 1장, ZIP 은 블록 순서대로).

    kind='publication' 전체 1행(+ZIP 이면 블록마다 구간 달린 1행), kind='strip' 띠 여러 행.
    값은 부호 없는 64비트 int — DB 에 넣을 때 to_signed.
    """
    page, spans = stack(normalized)
    out = []
    ph, dh, _ = _hashes(page)
    out.append({"kind": "publication", "region_y0": None, "region_y1": None,
                "phash": ph, "dhash": dh})
    if len(normalized) > 1:
        for (y0, y1), im in zip(spans, normalized):
            ph, dh, std = _hashes(im)
            if std >= LOW_INFO_STD:
                out.append({"kind": "publication", "region_y0": y0, "region_y1": y1,
                            "phash": ph, "dhash": dh})
    if page.height > STRIP:
        for y0 in _strip_starts(page.height, STRIP, STRIP_STEP):
            ph, dh, std = _hashes(page.crop((0, y0, NORM_W, y0 + STRIP)))
            if std >= LOW_INFO_STD:
                out.append({"kind": "strip", "region_y0": y0, "region_y1": y0 + STRIP,
                            "phash": ph, "dhash": dh})
    return out


def query_fingerprints(img: Image.Image) -> list[dict]:
    """발견 이미지 → 조회용 해시. 'whole'(전체 1개)은 모든 지문과, 'window'(세로 창)는 띠와 비교한다."""
    norm = normalize(img)
    ph, dh, _ = _hashes(norm)
    out = [{"kind": "whole", "y0": 0, "y1": norm.height, "phash": ph, "dhash": dh}]
    if norm.height > STRIP:
        step = QUERY_STEP if norm.height <= QUERY_LONG else QUERY_STEP_LONG
        for y0 in _strip_starts(norm.height, STRIP, step):
            ph, dh, std = _hashes(norm.crop((0, y0, NORM_W, y0 + STRIP)))
            if std >= LOW_INFO_STD:
                out.append({"kind": "window", "y0": y0, "y1": y0 + STRIP,
                            "phash": ph, "dhash": dh})
    return out


def _popcount(x: np.ndarray) -> np.ndarray:
    if hasattr(np, "bitwise_count"):
        return np.bitwise_count(x)
    b = x.view(np.uint8).reshape(*x.shape, 8)       # numpy<2 폴백
    return np.unpackbits(b, axis=-1).sum(axis=-1).astype(np.uint8)


def search(queries: list[dict], rows: list[dict], *, max_phash: int = MAX_PHASH,
           max_dhash: int = MAX_DHASH, chunk: int = 50_000) -> list[dict]:
    """전수 해밍 탐색. rows = 원장 지문({kind, phash, dhash, ...}, 해시는 부호 있든 없든).

    'whole' 조회는 모든 kind 와, 'window' 조회는 kind='strip' 과만 비교한다. 행마다 가장 가까운
    조회 하나를 고르고, pHash ≤ max_phash 이면서 그때의 dHash ≤ max_dhash 인 행만 돌려준다.
    규모 메모: 지문 N × 조회창 Q 를 청크로 나눠 numpy XOR+popcount 한다 — 지금 규모(수백~수만
    행)에선 수십 ms. 수십만 행을 넘기면 pgvector bit(64) + hamming HNSW 인덱스로 옮긴다.
    """
    if not rows or not queries:
        return []
    ph = np.array([to_unsigned(int(r["phash"])) for r in rows], dtype=np.uint64)
    dh = np.array([to_unsigned(int(r["dhash"])) for r in rows], dtype=np.uint64)
    is_strip = np.array([r["kind"] == "strip" for r in rows], dtype=bool)
    qph = np.array([q["phash"] for q in queries], dtype=np.uint64)
    qdh = np.array([q["dhash"] for q in queries], dtype=np.uint64)
    q_window = np.array([q["kind"] == "window" for q in queries], dtype=bool)
    best_d = np.full(len(rows), 255, dtype=np.int32)
    best_q = np.zeros(len(rows), dtype=np.int64)
    for s in range(0, len(rows), chunk):
        e = min(len(rows), s + chunk)
        D = _popcount(qph[:, None] ^ ph[None, s:e]).astype(np.int32)       # (Q, n)
        D[np.ix_(q_window, ~is_strip[s:e])] = 255                           # 창은 띠만
        arg = D.argmin(axis=0)
        best_q[s:e] = arg
        best_d[s:e] = D[arg, np.arange(e - s)]
    dd = _popcount(qdh[best_q] ^ dh).astype(np.int32)
    keep = np.where((best_d <= max_phash) & (dd <= max_dhash))[0]
    out = []
    for i in keep:
        q = queries[int(best_q[i])]
        out.append({"row": rows[int(i)], "phash_distance": int(best_d[i]),
                    "dhash_distance": int(dd[i]), "query": {"kind": q["kind"],
                                                           "y0": q["y0"], "y1": q["y1"]}})
    out.sort(key=lambda m: (m["phash_distance"], m["dhash_distance"]))
    return out
