"""시그니처(첫 화면) 컷 live smoke — 생성 + 독립 QC 판정까지 한 번에.

목적 두 가지:
① 시그니처 분기(모델 라우팅 + SIGNATURE_DIRECTION)가 실제로 의도한 구도를 만들어내는가
② 그 결과가 독립 QC(cut_output_qc)의 framingDirectionFacePose 게이트를 통과하는가
   — 리뷰가 지적한 '검사관이 되돌린다' 가설의 실측.

실행: cd server && .venv/bin/python -m scripts.smoke_signature_cut --front <의류사진>
비용 주의: 이미지 생성 1콜 + QC 비전 1콜.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"


def _load_env() -> None:
    env = SERVER / ".env"
    if not env.exists():
        return
    import os
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env()
sys.path.insert(0, str(SERVER))

from app.agents import cut_generator as cut  # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.model_routing import resolve_model  # noqa: E402
from app.config import load_settings  # noqa: E402

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--front", required=True, help="의류 정면 사진")
    ap.add_argument("--example", default="sig_women_01", help="시그니처 풀 exampleId")
    ap.add_argument("--cut", default="styling", choices=["styling", "horizon"])
    ap.add_argument("--clothing", default="top")
    ap.add_argument("--gender", default="women")
    ap.add_argument("--name", default="소프트 골지 라운드 니트")
    ap.add_argument("--out", default=str(SERVER / "ab_out" / "signature"))
    ap.add_argument("--qc", action="store_true", help="독립 QC 판정까지 실행")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    s = load_settings()

    front = Path(args.front)
    if not front.exists():
        print(f"의류 사진이 없습니다: {front}", file=sys.stderr)
        return 2
    garment = InlineImage(_MIME.get(front.suffix.lower(), "image/jpeg"), front.read_bytes())

    # 시그니처 풀 자산을 구도 레퍼런스로 붙인다(서버 레지스트리에 등록된 그 자산).
    _base, assets = cut.load_example_asset_registry()
    entry = assets.get(args.example)
    if not entry:
        print(f"레지스트리에 {args.example} 이 없습니다.", file=sys.stderr)
        return 2
    # R2 공개 URL 은 기본 UA 를 막는다 — 같은 이미지의 로컬 원본을 쓴다(내용 동일).
    local = ROOT / "public" / "assets" / "signature" / f"{args.example}.webp"
    if not local.exists():
        print(f"로컬 시그니처 원본이 없습니다: {local}", file=sys.stderr)
        return 2
    example_img = InlineImage("image/webp", local.read_bytes())
    print(f"구도 레퍼런스: {local.name} (등록 URL: {entry['all'].rsplit('/', 1)[-1]})")

    spec = cut.normalize_spec({
        "cutType": args.cut, "shot": "medium", "direction": "front",
        "colorId": "col1", "spaceGroupId": None,
        "exampleId": args.example,          # ← 이 표식이 시그니처 분기를 켠다
    }, clothing_type=args.clothing)

    product = {"name": args.name, "clothing_type": args.clothing}
    analysis = {
        "materials": [{"name": "코튼", "ratio": 60}, {"name": "폴리에스터", "ratio": 40}],
        "fit": "semi_over", "sellingPoints": ["부드러운 촉감"],
        "targetGenders": [args.gender],
    }

    print(f"시그니처 판정: {cut.is_signature_cut(spec)}")
    print(f"모델: image_high={resolve_model(s, 'image_high')} / "
          f"image_signature={resolve_model(s, 'image_signature')} / "
          f"OPENAI_API_KEY={'있음' if s.openai_api_key else '없음'}")

    manifest = cut.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False, mood_count=1)
    prompt = cut.build_prompt(spec, product, analysis=analysis, manifest=manifest)
    has_direction = "SIGNATURE OPENING CUT" in prompt
    print(f"프롬프트 {len(prompt)}자 · SIGNATURE_DIRECTION 포함: {has_direction}")

    gemini = GeminiImageClient(s)
    t0 = time.time()
    image, mime = await cut.generate(
        s, gemini, spec, product, [garment, example_img],
        analysis=analysis, manifest=manifest)
    dt = time.time() - t0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
    out = out_dir / f"{args.example}_{args.clothing}.{ext}"
    out.write_bytes(image)
    print(f"생성 완료 — {dt:.1f}s, {len(image)//1024}KB → {out}")

    if args.qc:
        from app.agents import cut_output_qc, cut_plan
        # 실제 생성 경로(build_prompt)와 같은 전처리를 거쳐야 계약이 유효하다.
        plan = cut_plan.compile_cut_plan(cut.apply_reference_compatibility(dict(spec)), args.clothing)
        try:
            verdict = await cut_output_qc.verdict(
                s, plan,
                [
                    cut_output_qc.LabeledReference(role="product", image=garment),
                    cut_output_qc.LabeledReference(role="example", image=example_img),
                ],
                InlineImage(mime, image))
            print("--- 독립 QC 판정 ---")
            gates = verdict.get("gates") or verdict
            print(json.dumps(gates, ensure_ascii=False, indent=1)[:1400])
        except Exception as exc:  # noqa: BLE001
            print(f"QC 실행 실패(참고): {type(exc).__name__}: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
