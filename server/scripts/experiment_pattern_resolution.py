"""미세 패턴 × 출력 해상도 실측 (1K / 2K / 4K) — 로컬 산출물 전용.

왜: `mannequin_pattern_image_size=4K`(config.py:90) 의 근거는 2K 실측 1건 + 산술 추정이다
("4K 면 주기 ~18px"). 4K 실물을 뽑아 비교한 적이 없어서, 패턴 상품마다 장당 $0.134→$0.24
(+79%) 를 쓰는 판단이 검증되지 않은 채로 남아 있었다.

무엇을 보나: 같은 프롬프트·같은 입력으로 해상도만 바꿔 생성한 뒤,
  (1) 원본 해상도에서 줄무늬가 몇 px 주기로, 얼마나 또렷하게 재현됐는지
  (2) 상세페이지 표시 크기(기본 1200px 폭)로 줄인 뒤에도 그 이점이 남는지
를 잰다. (2) 가 핵심이다 — 4K 가 원본에서만 좋고 표시 크기로 줄이면 1K 와 같아진다면
셀러 눈에 닿지 않는 곳에 돈을 쓰는 것이다.

지표: 옷 몸통 영역의 세로 방향 밝기 프로파일을 FFT 로 분해해
  period_px    = 지배 주파수의 주기(픽셀). 줄 간격.
  peak_ratio   = 그 주파수가 전체 고주파 에너지에서 차지하는 비율. 높을수록 줄이 또렷하다.
  band_contrast= 지배 주기 대역의 진폭. 두 색 줄이 한 색으로 뭉개지면 급락한다.

실행:
  cd server && .venv/bin/python -m scripts.experiment_pattern_resolution --generate
  cd server && .venv/bin/python -m scripts.experiment_pattern_resolution   # 분석·리포트만
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from scripts.smoke_realwire import _load_env  # noqa: E402

_load_env(SERVER / ".env")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from scripts import experiment_runtime_cut_pipeline as base  # noqa: E402
from scripts import experiment_runtime_cut_pipeline_diverse as diverse  # noqa: E402

from app.agents import cut_generator  # noqa: E402
from app.agents.gemini_image import GeminiImageClient  # noqa: E402
from app.agents.image_cost import estimate_cost  # noqa: E402
from app.config import load_settings  # noqa: E402

OUT = base.OUT / "pattern_resolution"
FIXTURE = "striped_polo_top"   # 실제 핀 스트라이프 상품 트루스 + 마네킹 입력 (diverse 픽스처)
SIZES = ("1K", "2K", "4K")
REPS = 2
DISPLAY_WIDTH = 1200           # 상세페이지에서 실제로 보이는 폭 가정


# 가슴 크롭 박스(상대좌표). 생성물마다 프레이밍이 달라 자동 검출이 흔들려서, 산출물을 눈으로
# 확인하고 해상도별로 고정했다. 픽스처를 바꾸면 여기도 다시 잡아야 한다.
CHEST_BOX = {"1K": (0.33, 0.09, 0.63, 0.29),
             "2K": (0.33, 0.09, 0.63, 0.29),
             "4K": (0.40, 0.14, 0.62, 0.30)}


# ---------- 측정 (순수) ----------

def _torso(gray: np.ndarray) -> np.ndarray:
    """몸통 근사 크롭. 전신 세로 컷이라 상단 25~50%, 가로 중앙 30% 가 상의 몸통에 해당한다."""
    h, w = gray.shape
    return gray[int(h * 0.25):int(h * 0.50), int(w * 0.35):int(w * 0.65)]


# ---------- 업스케일 판별 (핵심 검정) ----------
#
# "4K 는 그냥 크게 뽑아준 것 아니냐"를 가리는 유일한 방법: 이미지 스스로의 고주파 함량을
# **같은 이미지를 factor 배 줄였다 되키운 것**과 비교한다. 순수 확대본은 되키워도 잃을 게 없어
# 비율이 1 에 붙고, 진짜로 더 그린 이미지는 되키우는 순간 디테일을 잃어 비율이 커진다.
# JPEG 잡음이 비율을 부풀리므로, 1K 생성물을 확대해 같은 화질로 다시 저장한 **대조군**을
# 같은 파이프라인에 통과시켜 잡음 바닥을 함께 잰다.

def _high_band_share(gray: np.ndarray) -> float:
    """행 방향 스펙트럼에서 1/4 나이키스트 위 대역이 차지하는 비중."""
    a = gray - gray.mean()
    spectrum = np.abs(np.fft.rfft(a, axis=1)).mean(axis=0)
    freqs = np.fft.rfftfreq(a.shape[1])
    total = spectrum[1:].sum()
    return float(spectrum[freqs > 0.125].sum() / total) if total else 0.0


def _gray(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.float64) / 255.0


def detail_ratio(img: Image.Image, factor: int) -> float:
    small = img.convert("L").resize(
        (max(img.width // factor, 8), max(img.height // factor, 8)), Image.LANCZOS)
    up = small.resize(img.size, Image.LANCZOS)
    base = _high_band_share(np.asarray(up, dtype=np.float64) / 255.0)
    return round(_high_band_share(_gray(img)) / base, 3) if base else 0.0


def _crop(path: Path, box) -> Image.Image:
    with Image.open(path) as im:
        w, h = im.size
        return im.crop((int(w * box[0]), int(h * box[1]), int(w * box[2]), int(h * box[3])))


def upscale_control(base_1k: Image.Image, factor: int, quality: int = 92) -> Image.Image:
    """1K 생성물을 factor 배 확대해 같은 화질로 다시 저장 = '순수 확대본'의 잡음 바닥."""
    up = base_1k.resize((base_1k.width * factor, base_1k.height * factor), Image.LANCZOS)
    buf = io.BytesIO()
    up.save(buf, "JPEG", quality=quality)
    return Image.open(buf)


def stripe_metrics(path: Path, resize_width: int | None = None) -> dict:
    img = Image.open(path).convert("L")
    if resize_width and img.width != resize_width:
        ratio = resize_width / img.width
        img = img.resize((resize_width, round(img.height * ratio)), Image.LANCZOS)
    gray = np.asarray(img, dtype=np.float64) / 255.0
    crop = _torso(gray)
    if crop.size == 0:
        return {"error": "empty_crop"}
    # 세로 방향 프로파일 — 가로 줄무늬는 y 축에서 주기 신호로 나타난다.
    profile = crop.mean(axis=1)
    profile = profile - profile.mean()
    n = len(profile)
    if n < 32:
        return {"error": "too_small"}
    window = np.hanning(n)
    spectrum = np.abs(np.fft.rfft(profile * window))
    freqs = np.fft.rfftfreq(n, d=1.0)
    # 주기 3~60px 대역만 본다(그보다 길면 몸통 명암, 짧으면 노이즈).
    band = (freqs > 1 / 60) & (freqs < 1 / 3)
    if not band.any() or spectrum[band].sum() == 0:
        return {"error": "no_band"}
    idx = np.argmax(np.where(band, spectrum, 0))
    total = spectrum[band].sum()
    return {
        "cropHeight": int(crop.shape[0]),
        "periodPx": round(float(1.0 / freqs[idx]), 2),
        "peakRatio": round(float(spectrum[idx] / total), 4),
        "bandContrast": round(float(spectrum[idx] / n), 5),
        # 인접 행 차이의 평균 = 고주파 에너지 총량(줄이 살아있으면 커진다).
        "highFreqEnergy": round(float(np.abs(np.diff(crop, axis=0)).mean()), 5),
    }


# ---------- 생성 ----------

async def generate(force: bool) -> list[dict]:
    OUT.mkdir(parents=True, exist_ok=True)
    fixture = diverse.FIXTURES[FIXTURE]
    spec = diverse._spec(fixture)
    records: list[dict] = []
    for size in SIZES:
        settings = replace(load_settings(), mannequin_image_size=size)
        client = GeminiImageClient(settings)
        for rep in range(1, REPS + 1):
            rid = f"{FIXTURE}__{size}__r{rep}"
            existing = list(OUT.glob(f"{rid}.*"))
            if existing and not force:
                records.append({"id": rid, "size": size, "rep": rep,
                                "status": "skipped_existing", "output": str(existing[0])})
                continue
            images, manifest, _paths = diverse._inputs(fixture)
            started = time.perf_counter()
            try:
                data, mime = await cut_generator.generate(
                    settings, client, spec, fixture["product"], images,
                    analysis=fixture["analysis"], manifest=manifest,
                )
                out = OUT / f"{rid}{base._suffix(mime)}"
                out.write_bytes(data)
                cost = estimate_cost(settings.model_image_high, size, None)
                records.append({
                    "id": rid, "size": size, "rep": rep, "status": "generated",
                    "output": str(out), "model": settings.model_image_high,
                    "latencyMs": round((time.perf_counter() - started) * 1000),
                    "tableUsd": cost.usd,
                })
                print(f"  {rid}: {out.name} ({len(data) / 1024:.0f}KB, "
                      f"{records[-1]['latencyMs'] / 1000:.1f}s)", flush=True)
            except Exception as exc:
                records.append({"id": rid, "size": size, "rep": rep, "status": "error",
                                "error": f"{type(exc).__name__}: {exc}"[:400]})
                print(f"  {rid}: ERROR {exc}", flush=True)
            await asyncio.sleep(1)
    return records


# ---------- 리포트 ----------

def _find(size: str, rep: int) -> Path | None:
    found = [p for p in OUT.glob(f"{FIXTURE}__{size}__r{rep}.*")
             if p.suffix in {".png", ".jpg", ".jpeg", ".webp"}]
    return found[0] if found else None


def analyse() -> list[dict]:
    rows = []
    for size in SIZES:
        factor = {"1K": 1, "2K": 2, "4K": 4}[size]
        for rep in range(1, REPS + 1):
            path = _find(size, rep)
            base_1k = _find("1K", rep)
            if path is None:
                continue
            with Image.open(path) as im:
                w, h = im.size
            chest = _crop(path, CHEST_BOX[size])
            control = None
            if factor > 1 and base_1k is not None:
                control = detail_ratio(
                    upscale_control(_crop(base_1k, CHEST_BOX["1K"]), factor), factor)
            rows.append({
                "size": size, "rep": rep, "file": path.name, "px": f"{w}×{h}",
                "bytes": path.stat().st_size,
                # factor=1(1K)은 자기 자신이 기준이라 2배로 잰다(참고값).
                "detailRatio": detail_ratio(chest, max(factor, 2)),
                "upscaleControl": control,
                "native": stripe_metrics(path),
                "display": stripe_metrics(path, resize_width=DISPLAY_WIDTH),
            })
    return rows


def report(rows: list[dict]) -> None:
    if not rows:
        print("측정할 산출물이 없습니다 — --generate 를 먼저 실행하세요.")
        return
    print()
    print("═" * 88)
    print(f"미세 패턴 재현 × 해상도 — {FIXTURE} (핀 스트라이프 폴로)")
    print("═" * 88)
    print("① 진짜로 더 그렸나 (업스케일 판별) — 대조군보다 확실히 커야 '진짜'다")
    print("─" * 88)
    print(f"{'해상도':6} {'rep':>3} {'출력px':>12} {'용량':>9} "
          f"{'디테일 배수':>12} {'순수확대 대조군':>16}")
    for r in rows:
        ctl = "—" if r["upscaleControl"] is None else f"{r['upscaleControl']:.2f}x"
        print(f"{r['size']:6} {r['rep']:>3} {r['px']:>12} {r['bytes'] / 1024:>8.0f}K "
              f"{r['detailRatio']:>11.2f}x {ctl:>16}")
    print()
    print("② 줄무늬 신호 (참고 — 컷마다 프레이밍이 달라 표본 2장으로는 분리 안 됨)")
    print("─" * 88)
    print(f"{'해상도':6} {'rep':>3} | {'원본 주기':>9} {'또렷함':>8} | "
          f"{'표시 1200px 주기':>15} {'또렷함':>8}")
    for r in rows:
        n, d = r["native"], r["display"]
        print(f"{r['size']:6} {r['rep']:>3} | {n.get('periodPx', 0):>8.1f}p "
              f"{n.get('peakRatio', 0):>8.3f} | {d.get('periodPx', 0):>14.1f}p "
              f"{d.get('peakRatio', 0):>8.3f}")
    print()
    print("읽는 법: ①에서 '디테일 배수'가 '순수확대 대조군'보다 크면 그 해상도는 픽셀만 늘린 게")
    print("아니라 실제로 더 그린 것이다. ②는 표본이 적어 해상도 효과와 생성 편차가 안 갈린다 —")
    print("판단은 report.html 을 눈으로 보고 한다.")
    (OUT / "metrics.json").write_text(
        json.dumps({"fixture": FIXTURE, "displayWidth": DISPLAY_WIDTH, "rows": rows},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n측정치: {OUT / 'metrics.json'}")


def write_html(rows: list[dict]) -> Path:
    """육안 비교용 — 원본 100% 크롭(몸통)과 표시 크기 전신을 나란히."""
    cards = []
    for r in rows:
        path = OUT / r["file"]
        # 가슴 100% 크롭을 따로 저장해 육안 비교를 정확히 — 브라우저 리사이즈로 흐려지지 않게.
        chest_name = f"chest__{r['size']}__r{r['rep']}.png"
        _crop(path, CHEST_BOX[r["size"]]).save(OUT / chest_name)
        ctl = "—" if r["upscaleControl"] is None else f"{r['upscaleControl']:.2f}x"
        cards.append(f"""
        <figure>
          <figcaption><b>{r['size']} · rep{r['rep']}</b> <span class="px">{r['px']}</span><br>
            <span class="m">디테일 {r['detailRatio']:.2f}x · 순수확대 대조군 {ctl}
            · {r['bytes'] / 1024:.0f}KB</span></figcaption>
          <div class="zoom"><img src="{path.name}"></div>
          <div class="crop"><img src="{chest_name}"></div>
        </figure>""")
    html = f"""<!doctype html><meta charset="utf-8">
<title>미세 패턴 × 해상도 실측</title>
<style>
 body{{font:14px/1.6 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",sans-serif;
   margin:32px;background:#fafafa;color:#111}}
 h1{{font-size:20px}} .lead{{max-width:820px;color:#444}}
 .grid{{display:flex;gap:24px;overflow-x:auto;padding-bottom:16px}}
 figure{{margin:0;background:#fff;border:1px solid #e4e4e4;border-radius:10px;padding:12px}}
 figcaption{{margin-bottom:8px}} .px{{color:#888}} .m{{color:#666;font-size:12px}}
 .zoom img{{display:block;width:320px}}
 .crop{{width:320px;margin-top:10px;padding-top:10px;border-top:1px dashed #ddd}}
 .crop img{{display:block;width:320px;image-rendering:auto}}
</style>
<h1>미세 패턴 × 출력 해상도 — {FIXTURE}</h1>
<p class="lead">위 칸은 전체 컷, 아래 칸은 <b>가슴 부분을 원본 픽셀에서 잘라</b> 같은 폭으로 놓은
것입니다. 아래 칸에서 줄이 더 또렷하면 그 해상도가 실제로 값을 하는 것입니다.
(생성물마다 프레이밍이 달라 인물 크기는 다를 수 있습니다 — 줄의 <i>선명함</i>만 보세요.)</p>
<div class="grid">{''.join(cards)}</div>
"""
    out = OUT / "report.html"
    out.write_text(html, encoding="utf-8")
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", action="store_true", help="Gemini 호출 (6장, 약 $1)")
    ap.add_argument("--force", action="store_true", help="기존 산출물 덮어쓰기")
    args = ap.parse_args()
    if args.generate:
        print(f"생성 시작 — {len(SIZES)}해상도 × {REPS}회 = {len(SIZES) * REPS}장", flush=True)
        records = await generate(args.force)
        (OUT / "run.json").write_text(
            json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    rows = analyse()
    report(rows)
    if rows:
        print(f"육안 비교: {write_html(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
