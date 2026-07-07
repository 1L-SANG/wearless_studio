"""QC 임계값 캘리브레이션 (D2 — retrieval_upgrade_prd §4.4 FR-D2).

정상(positive) 표본과 실패모드 합성(negative) 표본으로 `app.services.qc`의
판정을 실측한다. 게이트: 정상 차단(false positive) 0건 + 실패모드 검출률 리포트.
통과하면 MANNEQUIN_QC_ENABLED=true 로 게이팅을 켠다 (qc.py 주석의 '워커 결정').

사용:
    uv run python -m scripts.qc_calibrate --positives <dir> [--out <dir>]

- positives: 정상 마네킹컷 형상의 이미지 디렉토리 (베이스 마네킹·모델 프리셋·실생성물).
- negatives는 각 positive에서 실패모드별로 합성한다(유령·크롭·하반신 소실·비율·저해상).
  qc.py가 겨냥하는 실패모드를 정확히 재현하는 것이 목적이라 합성이 타당하다.
- --out 주면 합성 표본을 저장해 눈으로 검수할 수 있다.

알려진 한계 (2026-07-04 실측): crop_zoom(프레임을 꽉 채운 상반신 줌)은 bbox·비율·하단 대역이
전부 정상 범위라 픽셀 기하로 구분 불가 — 의미 판정(AG-P2 image-qc)의 몫. 그 외 실패모드
(유령·하반신 소실·비율·저해상)는 20/20 검출, 정상 차단(FP) 0/15.
"""

import argparse
import io
import os
import sys

from PIL import Image, ImageEnhance

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services import qc  # noqa: E402


def _bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _bg_color(img: Image.Image) -> tuple:
    return qc._bg_color(img.convert("RGB"))


# ── positive 변형 (여전히 pass 해야 함) ──────────────────────────────────────
def _production_size(img: Image.Image) -> Image.Image:
    """실생성물은 항상 ≥1K (config MANNEQUIN_IMAGE_SIZE) — 소형 표본은 선업스케일해 동일 조건으로."""
    w, h = img.size
    if min(w, h) >= 1024:
        return img
    scale = 1024 / min(w, h)
    return img.resize((int(w * scale), int(h * scale)))


def positive_variants(img: Image.Image) -> dict:
    img = _production_size(img)
    return {
        "orig": img,
        "bright+15": ImageEnhance.Brightness(img).enhance(1.15),
        "bright-15": ImageEnhance.Brightness(img).enhance(0.85),
    }


# ── negative 합성 (retry 해야 함) — qc.py의 실패모드 재현 ────────────────────
def negative_variants(img: Image.Image) -> dict:
    img = _production_size(img.convert("RGB"))
    w, h = img.size
    bg = Image.new("RGB", img.size, _bg_color(img))
    out = {}
    # ghost: 배경과 저대비 블렌드 (반투명 마네킹)
    out["ghost"] = Image.blend(bg, img, 0.22)
    # full_body_crop: 상반신 줌 (다리 잘림)
    out["crop_zoom"] = img.crop((int(w * 0.1), 0, int(w * 0.9), int(h * 0.55))).resize((w, h))
    # missing_lower_body: 하단 18% 잘라내고 배경으로 패딩 (발 소실)
    cut = img.crop((0, 0, w, int(h * 0.82)))
    padded = bg.copy()
    padded.paste(cut, (0, 0))
    out["missing_feet"] = padded
    # bad_aspect: 정사각 센터 크롭
    side = min(w, h)
    out["square"] = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    # too_small: 저해상
    out["tiny"] = img.resize((480, int(480 * h / w)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--positives", required=True)
    ap.add_argument("--out", default=None, help="합성 표본 저장 디렉토리(검수용)")
    args = ap.parse_args()

    files = sorted(
        f for f in os.listdir(args.positives)
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    )
    if not files:
        print("positive 표본 없음:", args.positives)
        return 2

    fp = []  # 정상인데 retry (차단) — 게이트: 0건
    fn = []  # 실패모드인데 pass (놓침)
    n_pos = n_neg = 0

    print(f"{'표본':<40} {'판정':<7} 사유")
    for name in files:
        img = Image.open(os.path.join(args.positives, name))
        stem = os.path.splitext(name)[0]
        for tag, variant in positive_variants(img).items():
            n_pos += 1
            r = qc.evaluate_mannequin_qc(_bytes(variant))
            label = f"POS {stem}:{tag}"
            print(f"{label:<40} {r.verdict:<7} {','.join(r.reasons) or '-'}")
            if r.verdict != "pass":
                fp.append((label, r.reasons, r.metrics))
        for tag, variant in negative_variants(img).items():
            n_neg += 1
            r = qc.evaluate_mannequin_qc(_bytes(variant))
            label = f"NEG {stem}:{tag}"
            print(f"{label:<40} {r.verdict:<7} {','.join(r.reasons) or '-'}")
            if r.verdict == "pass":
                fn.append((label, r.metrics))
            if args.out:
                os.makedirs(args.out, exist_ok=True)
                variant.convert("RGB").save(os.path.join(args.out, f"{stem}_{tag}.png"))

    print("\n== 요약 ==")
    print(f"positive {n_pos}건 중 차단(FP) {len(fp)}건 — 게이트 기준 0건")
    print(f"negative {n_neg}건 중 놓침(FN) {len(fn)}건")
    for label, reasons, metrics in fp:
        print(f"  FP {label}: {reasons} {metrics}")
    for label, metrics in fn:
        print(f"  FN {label}: {metrics}")

    gate_ok = not fp
    print("게이트:", "통과 — MANNEQUIN_QC_ENABLED=true 켜도 됨" if gate_ok else "실패 — 임계값 조정 필요")
    return 0 if gate_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
