#!/usr/bin/env python3
"""
Spike: Mannequin wearing custom Tops from Notion folder and custom Bottoms from Downloads.
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import pathlib
import time
from typing import Any

# server/.env 로드
_SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
_ENV_PATH = _SERVER_DIR / ".env"


def _load_env(path: pathlib.Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(_ENV_PATH)

from app.agents.gemini_image import GeminiImageClient, InlineImage
from app.config import load_settings


def load_image(path: pathlib.Path) -> InlineImage:
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "image/jpeg"
    data = path.read_bytes()
    return InlineImage(mime=mime, data=data)


def to_base64_data_uri(path: pathlib.Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


async def main() -> None:
    settings = load_settings()
    client = GeminiImageClient(settings)

    base_dir = pathlib.Path("/Users/nojeong-un/devs/flatlay-spike-inputs")
    out_dir = base_dir / "out_mannequin_outfits"
    out_dir.mkdir(parents=True, exist_ok=True)

    base_mannequin_path = base_dir / "mannequin_base" / "base-women-2K.png"
    if not base_mannequin_path.exists():
        raise FileNotFoundError(f"Base mannequin missing: {base_mannequin_path}")

    # Top & Bottom Pairings
    tops_dir = pathlib.Path("/Users/nojeong-un/Downloads/노션에 있는 의상들/상의")
    pants_dir = pathlib.Path("/Users/nojeong-un/Downloads/ㅂㅏㅈㅣ")

    outfits = [
        {
            "id": "outfit_01_stripe_brown",
            "name": "Look 1: 스트라이프 셔츠 + 브라운 와이드 팬츠",
            "bottom_path": pants_dir / "4.jpeg",
            "bottom_desc": "Brown wide-leg trousers with center front pinch-pleats/crease seams, front slash pockets, and relaxed straight silhouette.",
            "top_paths": [
                tops_dir / "스트라이프셔츠_앞면.jpeg",
                tops_dir / "스트라이프셔츠_디테일컷.jpeg",
            ],
            "top_desc": "Long-sleeved button-up shirt in light beige/cream fabric with subtle blue and beige vertical pinstripes, classic spread collar, front button placket with white buttons, pleated chest detailing, and buttoned cuffs.",
        },
        {
            "id": "outfit_02_greyknit_washeddenim",
            "name": "Look 2: 얇은 그레이 니트 + 라이트 워시드 데님",
            "bottom_path": pants_dir / "다운로드.jpeg",
            "bottom_desc": "Light blue washed wide denim pants with yellow-tinted thigh fade highlights, five-pocket styling, and clean straight hem.",
            "top_paths": [
                tops_dir / "얇은회색니트_앞면.jpeg",
                tops_dir / "얇은회색니트_뒷면.jpeg",
            ],
            "top_desc": "Light heather grey fine-knit crewneck sweater/pullover with ribbed collar, ribbed hem, ribbed cuffs, and soft drapey wool/cashmere-like knit texture.",
        },
        {
            "id": "outfit_03_whiteshirt_brownpleats",
            "name": "Look 3: 화이트 오버 셔츠 + 브라운 셔링 팬츠",
            "bottom_path": pants_dir / "다운로드3.jpeg",
            "bottom_desc": "Deep dark brown cotton wide trousers with distinct front vertical pinch-pleat stitch, side pocket rivets, and elasticized rear waistband.",
            "top_paths": [
                tops_dir / "흰셔츠_아면.jpeg",
                tops_dir / "흰셔츠_뒷면.jpeg",
            ],
            "top_desc": "Clean crisp white cotton relaxed-fit collared button-down shirt with front buttons and dropped shoulder long sleeves, natural cotton poplin folds.",
        },
        {
            "id": "outfit_04_redblouse_vintagedenim",
            "name": "Look 4: 골지 레드 블라우스 + 빈티지 워시드 데님",
            "bottom_path": pants_dir / "바지다른거.jpeg",
            "bottom_desc": "Vintage washed light denim jeans with subtle yellow cast highlights, straight relaxed fit, and authentic faded denim grain.",
            "top_paths": [
                tops_dir / "골지_블라우스_레드_앞면.jpeg",
                tops_dir / "골지_블라우스_레드_디테일.jpeg",
            ],
            "top_desc": "Muted dusty rose/reddish-brown short-sleeve ribbed knit blouse with scoop neckline, delicate ruched/gathered bust detail, patterned textured vertical openwork knit stripes, and cap sleeves.",
        },
    ]

    results = []

    print(f"🚀 Starting Mannequin Outfit Synthesis for {len(outfits)} looks...")

    for i, outfit in enumerate(outfits, 1):
        print(f"\n[{i}/{len(outfits)}] Generating: {outfit['name']}")
        start_t = time.time()

        # Build image input list:
        # Image 1: Base Mannequin
        # Image 2: Bottom Pants
        # Image 3+: Top Front & Detail/Back
        images_to_send = [
            load_image(base_mannequin_path),
            load_image(outfit["bottom_path"]),
        ]
        for tp in outfit["top_paths"]:
            images_to_send.append(load_image(tp))

        prompt = f"""You are an elite fashion e-commerce director and 3D visual specialist.
Your task is to generate a studio-grade 3D mannequin product photo showing the mannequin styled in BOTH the TOP garment and the BOTTOM garment provided in the input images.

[INPUT IMAGES CONTRACT]
- Image 1: Base Studio Mannequin (Full body matte white mannequin in studio background).
- Image 2: BOTTOM GARMENT (Pants/Trousers) -> {outfit['bottom_desc']}
- Image 3: TOP GARMENT (Front View) -> {outfit['top_desc']}
{"- Image 4: TOP GARMENT (Detail/Back View)" if len(outfit['top_paths']) > 1 else ""}

[EXACT COMPOSITION & STYLING RULES]
1. MANNEQUIN & STUDIO BASE:
   - Match the exact pose, headless egg-head minimalist form, matte white finish, and lighting of Image 1.
   - Clean neutral grey/off-white studio cyclorama background (RGB 232, 232, 230) with soft ground contact shadow.
   - Vertical full-body framing from head to bare feet (2:3 aspect ratio). No shoes, no wigs, no human skin.

2. TOP GARMENT REPRODUCTION (UPPER BODY):
   - Dress the upper torso and arms in the exact top shown in Image 3 (and Image 4).
   - Accurately reproduce the collar/neckline, button closures, stripe patterns, knit weaves, sleeve cuffs, and garment color tone.
   - Drape naturally over the 3D torso and arms with realistic gravity and cloth folds.
   - Ensure the styling is clean and balanced with the pants (e.g., lightly tucked in front or sitting at the waist so the waistband and pockets of the pants remain clear and defined).

3. BOTTOM GARMENT REPRODUCTION (LOWER BODY):
   - Dress the lower body and legs in the exact bottom pants shown in Image 2.
   - Accurately preserve the authentic fabric texture, wash fading, pinch-pleat crease lines, coin/side pockets, belt loops, and hem width.
   - The fabric must follow the 3D volume of the mannequin legs with natural drape folds around the knees and ankles.

4. PHOTOREALISM & IDENTITY PRESERVATION:
   - Zero hallucination of random logos or patterns.
   - Both garments must look like the real physical items from the input photos fitted onto the 3D mannequin in a premium luxury studio shoot."""

        try:
            res = await client.generate_content_image(
                model="gemini-3-pro-image",
                prompt=prompt,
                images=images_to_send,
                image_size="2K",
                aspect_ratio="2:3",
            )
            elapsed = time.time() - start_t
            out_img_path = out_dir / f"{outfit['id']}.jpg"
            out_img_path.write_bytes(res.image)
            print(f"  ✅ Saved: {out_img_path.name} ({elapsed:.1f}s, {len(res.image):,} bytes)")

            results.append({
                "id": outfit["id"],
                "name": outfit["name"],
                "bottom_file": outfit["bottom_path"].name,
                "bottom_path": str(outfit["bottom_path"]),
                "top_files": [p.name for p in outfit["top_paths"]],
                "top_paths": [str(p) for p in outfit["top_paths"]],
                "out_img_file": out_img_path.name,
                "out_img_path": str(out_img_path),
                "elapsed": elapsed,
                "status": "success",
            })
        except Exception as e:
            elapsed = time.time() - start_t
            print(f"  ❌ Error for {outfit['id']}: {e}")
            results.append({
                "id": outfit["id"],
                "name": outfit["name"],
                "bottom_file": outfit["bottom_path"].name,
                "bottom_path": str(outfit["bottom_path"]),
                "top_files": [p.name for p in outfit["top_paths"]],
                "top_paths": [str(p) for p in outfit["top_paths"]],
                "error": str(e),
                "elapsed": elapsed,
                "status": "failed",
            })

    # Save JSON results
    results_json_path = out_dir / "results_outfits.json"
    results_json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    # Generate Self-Contained HTML Report
    print("\n📊 Generating Self-Contained HTML Report...")
    base_mannequin_b64 = to_base64_data_uri(base_mannequin_path)

    cards_html = []
    for r in results:
        if r["status"] != "success":
            continue
        
        out_img_b64 = to_base64_data_uri(pathlib.Path(r["out_img_path"]))
        bottom_b64 = to_base64_data_uri(pathlib.Path(r["bottom_path"]))
        
        top_thumbs_html = []
        for tp in r["top_paths"]:
            p = pathlib.Path(tp)
            top_b64 = to_base64_data_uri(p)
            top_thumbs_html.append(f"""
                <div class="thumb-box">
                    <img src="{top_b64}" alt="{p.name}" />
                    <div class="thumb-lbl">{p.name}</div>
                </div>
            """)
        
        top_thumbs_joined = "".join(top_thumbs_html)

        cards_html.append(f"""
        <div class="outfit-card">
            <div class="outfit-header">
                <h2>{r['name']}</h2>
                <div class="meta-tag">Latency: {r['elapsed']:.1f}s | Gemini 3 Pro (2K)</div>
            </div>
            
            <div class="outfit-body">
                <!-- Left: Source Components -->
                <div class="sources-col">
                    <div class="source-group">
                        <div class="group-title">1. 베이스 마네킹</div>
                        <div class="thumb-box base-thumb">
                            <img src="{base_mannequin_b64}" alt="Base Mannequin" />
                            <div class="thumb-lbl">Studio Base Mannequin (Women 2K)</div>
                        </div>
                    </div>
                    
                    <div class="source-group">
                        <div class="group-title">2. 상의 원본 (노션 의상 세트)</div>
                        <div class="thumbs-row">
                            {top_thumbs_joined}
                        </div>
                    </div>

                    <div class="source-group">
                        <div class="group-title">3. 하의 원본 (다운로드 바지)</div>
                        <div class="thumb-box bottom-thumb">
                            <img src="{bottom_b64}" alt="{r['bottom_file']}" />
                            <div class="thumb-lbl">{r['bottom_file']}</div>
                        </div>
                    </div>
                </div>

                <!-- Right: Generated 3D Mannequin Fit -->
                <div class="result-col">
                    <div class="result-title">🌟 3D 마네킹 착용 합성 결과 (Top + Bottom)</div>
                    <div class="result-img-wrap">
                        <img src="{out_img_b64}" alt="{r['name']} Result" />
                    </div>
                </div>
            </div>
        </div>
        """)

    all_cards_joined = "\n".join(cards_html)

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>3D 마네킹 코디 착용컷 합성 리포트 (상의 + 하의 실물)</title>
    <style>
        :root {{
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --border-color: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #38bdf8;
            --accent-green: #4ade80;
        }}
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            padding: 32px 20px;
            line-height: 1.5;
        }}
        .header-container {{
            max-width: 1400px;
            margin: 0 auto 32px auto;
            text-align: center;
        }}
        .header-container h1 {{
            font-size: 2.2rem;
            font-weight: 700;
            margin-bottom: 12px;
            background: linear-gradient(135deg, #38bdf8 0%, #818cf8 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .header-container p {{
            color: var(--text-muted);
            font-size: 1.05rem;
            max-width: 800px;
            margin: 0 auto;
        }}
        .grid-container {{
            max-width: 1400px;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 40px;
        }}
        .outfit-card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
        }}
        .outfit-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 16px;
            margin-bottom: 20px;
        }}
        .outfit-header h2 {{
            font-size: 1.4rem;
            font-weight: 600;
            color: #f1f5f9;
        }}
        .meta-tag {{
            background: rgba(56, 189, 248, 0.15);
            color: var(--accent);
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: 600;
            border: 1px solid rgba(56, 189, 248, 0.3);
        }}
        .outfit-body {{
            display: grid;
            grid-template-columns: 480px 1fr;
            gap: 28px;
        }}
        @media (max-width: 1024px) {{
            .outfit-body {{
                grid-template-columns: 1fr;
            }}
        }}
        .sources-col {{
            display: flex;
            flex-direction: column;
            gap: 20px;
            background: #151f30;
            padding: 18px;
            border-radius: 12px;
            border: 1px solid #293548;
        }}
        .source-group {{
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .group-title {{
            font-size: 0.9rem;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .thumbs-row {{
            display: flex;
            gap: 12px;
            overflow-x: auto;
        }}
        .thumb-box {{
            background: #0b1120;
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 8px;
            display: flex;
            flex-direction: column;
            align-items: center;
            max-width: 200px;
        }}
        .thumb-box img {{
            width: 100%;
            height: 140px;
            object-fit: contain;
            border-radius: 4px;
            background: #000;
        }}
        .base-thumb img {{
            height: 160px;
        }}
        .bottom-thumb img {{
            height: 160px;
        }}
        .thumb-lbl {{
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-top: 6px;
            text-align: center;
            word-break: break-all;
        }}
        .result-col {{
            display: flex;
            flex-direction: column;
            align-items: center;
        }}
        .result-title {{
            font-size: 1.1rem;
            font-weight: 600;
            color: var(--accent-green);
            margin-bottom: 12px;
            width: 100%;
            text-align: center;
        }}
        .result-img-wrap {{
            width: 100%;
            max-width: 580px;
            background: #0b1120;
            border: 2px solid #38bdf8;
            border-radius: 12px;
            padding: 12px;
            box-shadow: 0 12px 30px rgba(0, 0, 0, 0.5);
        }}
        .result-img-wrap img {{
            width: 100%;
            height: auto;
            border-radius: 8px;
            display: block;
        }}
    </style>
</head>
<body>
    <div class="header-container">
        <h1>✨ 3D 마네킹 풀 착장 코디 합성 리포트 (상의 + 하의)</h1>
        <p>노션 상의 세트(앞면, 뒷면, 디테일)와 다운로드 바지를 베이스 마네킹에 3D 피팅한 프로덕션급 마네킹 착용컷 스파이크 결과입니다.</p>
    </div>

    <div class="grid-container">
        {all_cards_joined}
    </div>
</body>
</html>"""

    report_path = out_dir / "report_outfits.html"
    report_path.write_text(html_content, encoding="utf-8")
    print(f"🎉 All Done! HTML Report saved to: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
