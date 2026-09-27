"""추적 층 견고성 하네스 — 워터마크 판독률 · pHash 거리 · PSNR · 삽입 시간(2026-09-26).

prod 에 의존하지 않는다: 합성 상세페이지(2000폭, 글·사진·표·색 띠)를 만들어 실제 sign 경로와
같은 함수(fm_publication_mark.mark_publication)로 표식한 뒤, 쇼핑몰에서 흔한 변형을 가하고
**임의 높이의 세로 조각(기준 폭 860에서 800~1200px)** 을 여러 개 잘라 각각 단독으로 추적한다.

  - 워터마크: 조각마다 fm_watermark.read_image → 박은 코드와 같으면 성공.
  - pHash: 조각의 조회 해시(fm_fingerprint.query_fingerprints)를 이 배포본 지문과, 그리고 다른
    합성 페이지 3장의 지문(음성 대조군)과 fm_fingerprint.search 로 비교 — 가장 가까운 거리.

실행:
    cd server && .venv/bin/python -m scripts.fm_trace_robustness            # 절차 생성 사진
    cd server && .venv/bin/python -m scripts.fm_trace_robustness --photos   # 레포 public/assets 사진
pytest(tests/test_fm_trace_robustness.py)는 더 작은 페이지·변형 일부로 같은 함수를 돈다.
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.services import fm_fingerprint, fm_publication_mark, fm_watermark

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ── 합성 상세페이지 ───────────────────────────────────────────────────────────


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:   # 아주 옛 Pillow
        return ImageFont.load_default()


def procedural_photo(w: int, h: int, seed: int) -> Image.Image:
    """사진 대용 — 1/f 잡음(자연 이미지 스펙트럼) + 큰 타원 몇 개(인물·옷 덩어리)."""
    rng = np.random.default_rng(seed)
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.rfftfreq(w)[None, :]
    f = np.sqrt(fx ** 2 + fy ** 2)
    f[0, 0] = 1.0
    chans = []
    for _ in range(3):
        spec = (rng.normal(size=f.shape) + 1j * rng.normal(size=f.shape)) / f ** 1.1
        img = np.fft.irfft2(spec, s=(h, w))
        chans.append((img - img.mean()) / (img.std() + 1e-9))
    arr = rng.uniform(80, 200, size=3) + np.stack(chans, -1) * rng.uniform(18, 40)
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    for _ in range(4):
        cx, cy = rng.uniform(0, w), rng.uniform(0, h)
        rx, ry = rng.uniform(w * 0.05, w * 0.3), rng.uniform(h * 0.05, h * 0.3)
        d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry],
                  fill=tuple(int(v) for v in rng.uniform(30, 230, size=3)))
    return im


def _photos(n: int, seed: int, real: bool) -> list[Image.Image]:
    if real:
        files = sorted(glob.glob(os.path.join(REPO, "public/assets/signature/*.webp")))
        files += sorted(glob.glob(os.path.join(REPO, "public/assets/colorway/*.png")))
        if files:
            rng = np.random.default_rng(seed)
            order = rng.permutation(len(files))
            return [Image.open(files[order[i % len(files)]]).convert("RGB") for i in range(n)]
    return [procedural_photo(1024, 1536, seed * 100 + i) for i in range(n)]


def synthetic_page(width: int = 2000, *, seed: int = 0, real: bool = False,
                   blocks: int | None = None, photo_scale: float = 1.0) -> Image.Image:
    """에디터 긴 PNG 흉내 — 흰 글 블록·전폭 사진·2열 사진·표·어두운 띠를 세로로 잇는다."""
    photos = _photos(10, seed, real)
    if photo_scale != 1.0:
        photos = [p.resize((int(p.width * photo_scale), int(p.height * photo_scale)))
                  for p in photos]
    s = width / 1000
    big, mid = _font(int(44 * s)), _font(int(26 * s))
    lines = ["Soft rib knit / ivory", "Relaxed fit with dropped shoulder.",
             "Fabric: cotton 60% acrylic 40%", "Model 183cm wearing size L",
             "Wash cold, dry flat. Do not bleach."]

    def text_block(h, title, n, bg=(255, 255, 255), fg=(20, 20, 20)):
        im = Image.new("RGB", (width, int(h * s)), bg)
        d = ImageDraw.Draw(im)
        d.text((int(80 * s), int(60 * s)), title, font=big, fill=fg)
        for i, ln in enumerate(lines[:n]):
            d.text((int(80 * s), int((150 + 44 * i) * s)), ln, font=mid, fill=fg)
        return im

    def full(p):
        return p.resize((width, int(p.height * width / p.width)), Image.LANCZOS)

    def grid(a, b):
        half = (width - int(30 * s)) // 2
        a = a.resize((half, int(a.height * half / a.width)), Image.LANCZOS)
        b = b.resize((half, int(b.height * half / b.width)), Image.LANCZOS)
        im = Image.new("RGB", (width, max(a.height, b.height) + int(40 * s)), (255, 255, 255))
        im.paste(a, (0, int(20 * s)))
        im.paste(b, (half + int(30 * s), int(20 * s)))
        return im

    def chart():
        im = Image.new("RGB", (width, int(520 * s)), (250, 248, 244))
        d = ImageDraw.Draw(im)
        d.text((int(80 * s), int(40 * s)), "SIZE CHART", font=big, fill=(30, 30, 30))
        for r in range(6):
            y = int((140 + r * 60) * s)
            d.line([(int(80 * s), y), (width - int(80 * s), y)], fill=(180, 180, 180),
                   width=max(1, int(2 * s)))
            for c in range(5):
                d.text((int((100 + c * 170) * s), y + int(12 * s)), f"{(r + 1) * 10 + c + seed}",
                       font=mid, fill=(60, 60, 60))
        return im

    parts = [text_block(420, "NEW ARRIVAL", 5), full(photos[0]),
             text_block(300, "DETAIL", 3, bg=(242, 238, 232)), grid(photos[1], photos[2]),
             full(photos[3]), chart(), grid(photos[4], photos[5]),
             text_block(360, "FIT GUIDE", 5, bg=(30, 30, 34), fg=(235, 235, 235)),
             full(photos[6]), text_block(260, "CARE", 2)]
    if blocks is not None:
        parts = parts[:blocks]
    page = Image.new("RGB", (width, sum(p.height for p in parts)), (255, 255, 255))
    y = 0
    for p in parts:
        page.paste(p, (0, y))
        y += p.height
    return page


# ── 변형 ─────────────────────────────────────────────────────────────────────


def _resize_w(im: Image.Image, w: int) -> Image.Image:
    return im.resize((w, int(round(im.height * w / im.width))), Image.LANCZOS)


def _codec(im: Image.Image, fmt: str, **kw) -> Image.Image:
    buf = io.BytesIO()
    im.save(buf, fmt, **kw)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


TRANSFORMS = {
    # 스크린샷류: 860 표시 → 레티나 2배 캡처(1720) → PNG 재저장
    "screenshot_png(860→1720)": lambda im: _codec(_resize_w(_resize_w(im, 860), 1720), "PNG"),
    "png_resave(원본폭)": lambda im: _codec(im, "PNG"),
    "resize_860": lambda im: _resize_w(im, 860),
    "resize_640": lambda im: _resize_w(im, 640),
    "jpeg_q85@860": lambda im: _codec(_resize_w(im, 860), "JPEG", quality=85),
    "jpeg_q70@860": lambda im: _codec(_resize_w(im, 860), "JPEG", quality=70),
    "jpeg_q50@860": lambda im: _codec(_resize_w(im, 860), "JPEG", quality=50),
    "webp_q80@860": lambda im: _codec(_resize_w(im, 860), "WEBP", quality=80),
    # 추가 스트레스(요구 목록 밖)
    "jpeg_q70@640": lambda im: _codec(_resize_w(im, 640), "JPEG", quality=70),
    "sharpen+jpeg_q75@860": lambda im: _codec(
        _resize_w(im, 860).filter(ImageFilter.UnsharpMask(2, 80, 2)), "JPEG", quality=75),
}
CORE_TRANSFORMS = ("screenshot_png(860→1720)", "png_resave(원본폭)", "resize_860", "resize_640",
                   "jpeg_q85@860", "jpeg_q70@860", "jpeg_q50@860", "webp_q80@860")


def psnr(a: Image.Image, b: Image.Image) -> float:
    x = np.asarray(a.convert("RGB"), dtype=np.float64)
    y = np.asarray(b.convert("RGB"), dtype=np.float64)
    mse = float(((x - y) ** 2).mean())
    return float("inf") if mse == 0 else 10 * np.log10(255 ** 2 / mse)


def _png_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", compress_level=1)
    return buf.getvalue()


def _publication_rows(fps: list[dict], tag: str) -> list[dict]:
    return [{**fp, "publication_id": tag, "output_record_id": None} for fp in fps]


def evaluate(page: Image.Image, *, negatives: list[Image.Image], transforms=None,
             strips: int = 12, seed: int = 7, code: int = 0x5A17C0DE) -> dict:
    """page 를 표식 → 변형별로 조각 판독률과 pHash 거리를 잰다."""
    raw = _png_bytes(page)
    t0 = time.monotonic()
    marked = fm_publication_mark.mark_publication(raw, "long_png", code)
    mark_s = time.monotonic() - t0
    assert marked.wm_status == "embedded", marked.error
    wm_page = Image.open(io.BytesIO(marked.data)).convert("RGB")
    rows = _publication_rows(marked.fingerprints, "self")
    for i, neg in enumerate(negatives):
        norm = fm_fingerprint.normalize(neg)
        rows += _publication_rows(fm_fingerprint.publication_fingerprints([norm]), f"neg{i}")
    rng = np.random.default_rng(seed)
    out = {"size": page.size, "psnr": psnr(page, wm_page), "mark_seconds": mark_s,
           "embed_ms": marked.embed_ms, "fingerprints": len(marked.fingerprints),
           "png_bytes": (len(raw), len(marked.data)), "rows": {}}
    for name in transforms or TRANSFORMS:
        found = TRANSFORMS[name](wm_page)
        k = found.width / fm_watermark.CANON_W
        ok = hits = false_hits = 0
        own, neg = [], []
        for _ in range(strips):
            hh = int(rng.uniform(800, 1200) * k)
            y0 = int(rng.uniform(0, max(1, found.height - hh)))
            crop = found.crop((0, y0, found.width, y0 + hh))
            ok += fm_watermark.read_image(crop).code == code
            # 거리는 한도 없이 모으고, "실제로 후보로 뜨는가"는 운영 한도(MAX_PHASH·MAX_DHASH)로 센다.
            matches = fm_fingerprint.search(fm_fingerprint.query_fingerprints(crop), rows,
                                            max_phash=64, max_dhash=64)
            mine = [m for m in matches if m["row"]["publication_id"] == "self"]
            others = [m for m in matches if m["row"]["publication_id"] != "self"]
            shown = lambda m: (m["phash_distance"] <= fm_fingerprint.MAX_PHASH  # noqa: E731
                               and m["dhash_distance"] <= fm_fingerprint.MAX_DHASH)
            hits += any(shown(m) for m in mine)
            false_hits += any(shown(m) for m in others)
            own.append(min((m["phash_distance"] for m in mine), default=64))
            neg.append(min((m["phash_distance"] for m in others), default=64))
        full = fm_watermark.read_image(found).code == code
        out["rows"][name] = {"strip_ok": ok, "strips": strips, "full_ok": full,
                             "phash_hits": hits, "phash_false": false_hits,
                             "phash_own_max": max(own), "phash_own_median": float(np.median(own)),
                             "phash_neg_min": min(neg)}
    return out


def timing(width: int, height: int, seed: int = 3) -> dict:
    """삽입 시간 — mark_publication(디코드+삽입+PNG 인코드+지문) 전체와 삽입만."""
    page = synthetic_page(width, seed=seed)
    if page.height < height:   # 원하는 높이까지 반복
        reps = -(-height // page.height)
        tall = Image.new("RGB", (width, page.height * reps), (255, 255, 255))
        for i in range(reps):
            tall.paste(page, (0, i * page.height))
        page = tall
    page = page.crop((0, 0, width, height))
    t0 = time.monotonic()
    fm_watermark.embed_image(page, 0x13572468)
    embed_s = time.monotonic() - t0
    raw = _png_bytes(page)
    t0 = time.monotonic()
    res = fm_publication_mark.mark_publication(raw, "long_png", 0x13572468)
    total = time.monotonic() - t0
    return {"size": (width, height), "total_s": total, "embed_s": embed_s,
            "status": res.wm_status}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", action="store_true", help="레포 public/assets 사진으로 페이지 구성")
    ap.add_argument("--strips", type=int, default=16)
    args = ap.parse_args(argv)
    page = synthetic_page(2000, seed=1, real=args.photos)
    negatives = [synthetic_page(2000, seed=s, real=False) for s in (11, 12, 13)]
    r = evaluate(page, negatives=negatives, strips=args.strips)
    print(f"page {r['size'][0]}x{r['size'][1]}  PSNR {r['psnr']:.2f} dB  "
          f"mark(decode+embed+PNG+fingerprints) {r['mark_seconds']:.2f}s  "
          f"fingerprints {r['fingerprints']}  PNG {r['png_bytes'][0]/1e6:.1f}→"
          f"{r['png_bytes'][1]/1e6:.1f} MB")
    print("| 변형 | 워터마크 조각 판독 | 전체 판독 | pHash 조각 매치(운영 한도) | pHash 자기 거리 "
          "중앙/최대 | 남 페이지 헛후보 | 남 최소 거리(참고) |")
    print("|---|---|---|---|---|---|---|")
    for name, row in r["rows"].items():
        n = row["strips"]
        print(f"| {name} | {row['strip_ok']}/{n} ({100 * row['strip_ok'] / n:.0f}%) | "
              f"{'OK' if row['full_ok'] else 'FAIL'} | {row['phash_hits']}/{n} "
              f"({100 * row['phash_hits'] / n:.0f}%) | {row['phash_own_median']:.0f}/"
              f"{row['phash_own_max']} | {row['phash_false']}/{n} | {row['phash_neg_min']} |")
    for w, h in ((860, 15000), (2000, 15000), (2000, 30000)):
        t = timing(w, h)
        print(f"timing {w}x{h}: 삽입만 {t['embed_s']:.2f}s · sign 단계 전체(디코드+삽입+PNG+지문) "
              f"{t['total_s']:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
