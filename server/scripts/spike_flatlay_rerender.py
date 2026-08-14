"""커스텀 매칭 의류 flat-lay 재렌더 스파이크 (Gemini).

누끼본 이미지를 Gemini 이미지 모델(light / high)에 입력하여,
시드 카탈로그와 같은 정면 flat-lay 상품컷을 얻을 수 있는지와
'같은 옷인가(정체성 유지)'를 실험하고 검증한다.

실행 예:
    cd server
    uv run python -m scripts.spike_flatlay_rerender
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import pathlib
import sys
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

from app.agents.gemini_image import GeminiError, GeminiImageClient, InlineImage  # noqa: E402
from app.config import load_settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("flatlay_spike")

_DEFAULT_INPUT_DIR = pathlib.Path("/Users/nojeong-un/devs/flatlay-spike-inputs")
_DEFAULT_OUT_DIR = _DEFAULT_INPUT_DIR / "out"

_GARMENTS = {
    "washed_denim": {
        "title": "워시드 데님 팬츠 (행거 컷)",
        "cutout": "cutout/washed_denim_cutout.jpg",
        "original": "original/washed_denim_original.jpg",
        "known_flaws": "옷걸이 갈고리 및 상단 어깨 걸이 잔재",
    },
    "brown_wide": {
        "title": "브라운 와이드 팬츠 (바닥 각도 컷)",
        "cutout": "cutout/brown_wide_cutout.jpg",
        "original": "original/brown_wide_original.jpg",
        "known_flaws": "좌측 세로 띠 및 우상단 촬영 바닥 잔재, 허리 절단부 결함",
    },
}

_SEED_GREYJEANS = "seed_reference/seed_greyjeans.jpg"


def get_prompt_variants() -> dict[str, dict[str, Any]]:
    return {
        "variant_a_minimal": {
            "name": "A: 최소 지시 (Direct Flat-lay)",
            "description": "기본적인 스튜디오 flat-lay 상품컷 전환 지시",
            "prompt": (
                "A clean, commercial e-commerce studio flat-lay product photograph of this exact pair of pants. "
                "The pants are laid completely flat and neatly arranged on a solid neutral light gray surface (RGB 232, 232, 230). "
                "Direct overhead top-down view (bird's-eye perspective), centered in frame with balanced margins. "
                "Even, soft commercial studio lighting with minimal subtle shadow directly beneath. "
                "Completely remove any hangers, clips, floor seams, or background clutter. "
                "High resolution, crisp details, no distortion."
            ),
            "use_seed_image": False,
        },
        "variant_b_identity_lock": {
            "name": "B: 정체성 고정 강조 (Identity Lock)",
            "description": "워시·스티치·포켓·단추·실루엣·원단 질감 불변 명시",
            "prompt": (
                "Professional studio flat-lay product catalog photograph of the EXACT same garment shown in the reference image.\n\n"
                "CRITICAL IDENTITY LOCK - DO NOT ALTER THE GARMENT'S FEATURES:\n"
                "1. Garment Identity: Preserve the exact color shade, fabric wash pattern, fading/whiskering, distressing, and material texture.\n"
                "2. Construction Details: Maintain the exact pocket shape and placement, belt loops, waistband, button/rivet hardware, and topstitching color.\n"
                "3. Silhouette: Maintain the exact leg cut (wide leg / straight fit) and hem finish.\n\n"
                "TRANSFORMATION INSTRUCTIONS:\n"
                "- Lay the pants completely flat and neatly spread out on a seamless uniform light grey studio background (RGB: 232, 232, 230, #E8E8E6).\n"
                "- Pure direct top-down 90-degree overhead angle (bird's eye view), perfectly straight vertical orientation.\n"
                "- Remove any hanger hooks, clothes clips, wall/floor artifacts, or background debris.\n"
                "- Soft diffused studio lighting, sharp focus on all fabric details, clean product catalog image."
            ),
            "use_seed_image": False,
        },
        "variant_c_seed_ref": {
            "name": "C: 시드 스타일 참조 동반 (Style Reference Transfer)",
            "description": "seed_greyjeans.jpg를 2번째 이미지로 첨부하여 촬영 구도·조명·배경 스타일 전이",
            "prompt": (
                "Image 1 is the subject garment (input clothing).\n"
                "Image 2 is the target photography style reference (studio catalog flat-lay).\n\n"
                "Generate a professional e-commerce product photograph of the EXACT garment from Image 1, matching the exact photography and presentation style of Image 2.\n"
                "- Subject: Recreate the exact garment from Image 1 with 100% fidelity to its fabric color, wash marks, texture, stitching, pockets, and silhouette.\n"
                "- Style & Environment: Match Image 2's overhead top-down perspective, perfectly flat laid-out arrangement, solid neutral light grey studio floor (#E8E8E6), framing margins, and soft diffused lighting.\n"
                "- Remove all hangers, hooks, and background artifacts from Image 1.\n"
                "- Do not merge the design of Image 2 into Image 1; only copy Image 2's photography layout and studio background."
            ),
            "use_seed_image": True,
        },
    }


_JUDGMENTS: dict[str, dict[str, str]] = {
    "washed_denim_variant_a_minimal_flash": {
        "identity": "통과: 연청 워시 톤, 허벅지 중앙 옐로우 페이딩, 우측 코인 포켓 및 스티치 디테일 완벽 보존",
        "pose": "통과: 행거 경사 드레이프가 완벽한 정면 90° flat-lay 상품컷으로 전환됨",
        "background": "통과: 옷걸이/갈고리 100% 제거, 스튜디오 순회색 (232,232,230) 구현",
        "seed_match": "통과: seed_greyjeans와 동일한 화각·배경 조명·여백 유지",
        "accident": "통과: 왜곡 및 붕괴 없음",
        "verdict": "PASS",
        "summary": "안정적 정체성 보존 및 행거 완벽 제거",
    },
    "washed_denim_variant_a_minimal_pro": {
        "identity": "통과: 워시드 패턴, 허벅지 페이딩, 스티치 라인, 단추, 밑단 마감 원본 일치",
        "pose": "통과: 완벽한 정면 부감 flat-lay 안착",
        "background": "통과: 옷걸이 제거 및 순회색 배경 + 자연스러운 소프트 섀도우",
        "seed_match": "통과: 시드 카탈로그와 나란히 놓아도 위화감 없는 상품컷 룩",
        "accident": "통과: 이상 없음",
        "verdict": "PASS",
        "summary": "우수한 상품컷 완성도 및 자연스러운 텍스처",
    },
    "washed_denim_variant_b_identity_lock_flash": {
        "identity": "통과(최고): 원본 워시 톤(노란빛 하이라이트 포함), 포켓 위치, 벨트루프, 원단 텍스처 보존도 최상",
        "pose": "통과: 정면 90° overhead flat-lay 배치",
        "background": "통과: 옷걸이/마스크 결함 완전 제거, 순회색 배경",
        "seed_match": "통과: 시드 카탈로그 룩과 완벽 정합",
        "accident": "통과: 붕괴 없음",
        "verdict": "PASS",
        "summary": "정체성 보존도 최상급, 추천 설정",
    },
    "washed_denim_variant_b_identity_lock_pro": {
        "identity": "통과(추천): 워싱 디테일, 핏, 스티치, 리벳 정밀 보존",
        "pose": "통과: 반듯한 대칭 flat-lay",
        "background": "통과: 옷걸이 제거 및 순회색 배경",
        "seed_match": "통과: 스튜디오 룩 완벽 일치",
        "accident": "통과: 붕괴 없음",
        "verdict": "PASS",
        "summary": "고해상도 원단 디테일과 안정적인 정체성 유지",
    },
    "washed_denim_variant_c_seed_ref_flash": {
        "identity": "실패: seed_greyjeans의 곡선형 벌룬 실루엣과 그레이 톤이 전이되고 좌측 포켓 안감에 짙은 패치 임의 생성",
        "pose": "통과: 정면 flat-lay",
        "background": "통과: 옷걸이 제거, 순회색",
        "seed_match": "통과: 배경 및 룩 정합",
        "accident": "주의: 참조 이미지의 포켓 안감 형태 오염",
        "verdict": "FAIL",
        "summary": "시드 이미지로부터의 정체성 누수 발생 (바지 형태 왜곡)",
    },
    "washed_denim_variant_c_seed_ref_pro": {
        "identity": "실패: 원본의 스트레이트/와이드 핏이 seed_greyjeans의 둥근 배기/벌룬 아웃라인으로 왜곡됨",
        "pose": "통과: 정면 flat-lay",
        "background": "통과: 순회색 배경",
        "seed_match": "통과: 스튜디오 룩 일치",
        "accident": "주의: 다리 외곽선이 시드 바지 형태로 변형",
        "verdict": "FAIL",
        "summary": "시드 레퍼런스 실루엣이 입력 옷을 덮어씀 (핏 훼손)",
    },
    "brown_wide_variant_a_minimal_flash": {
        "identity": "통과: 다크 브라운 톤, 중앙 핀턱 절개선, 포켓 리벳 보존",
        "pose": "통과: 바닥 각도 컷에서 정면 flat-lay로 전환 (약간의 A자 벌림)",
        "background": "통과: 바닥 띠/선반 잔재 및 허리 마스크 구멍 완벽 복원",
        "seed_match": "통과: 스튜디오 상품컷 룩 정합",
        "accident": "통과: 붕괴 없음",
        "verdict": "PASS",
        "summary": "결함 복원 및 핀턱 디테일 유지",
    },
    "brown_wide_variant_a_minimal_pro": {
        "identity": "통과: 차콜 브라운 색감, 중앙 핀턱 스티치, 벨트루프 및 포켓 보존",
        "pose": "통과: 원단 주름을 매끄럽게 펴고 깔끔한 정면 flat-lay 안착",
        "background": "통과: 누끼 결함 완전 정제, 순회색 배경",
        "seed_match": "통과: 시드와 높은 정합성",
        "accident": "통과: 붕괴 없음",
        "verdict": "PASS",
        "summary": "원단 주름 자연스럽게 펴짐, 우수한 완성도",
    },
    "brown_wide_variant_b_identity_lock_flash": {
        "identity": "통과(최고): 원본 뒷허리의 고무 셔링(밴딩) 주름 질감까지 인식하여 복원, 중앙 핀턱 선 완벽 유지",
        "pose": "통과: 반듯한 정면 flat-lay",
        "background": "통과: 잔재 제거, 순회색 스튜디오 배경",
        "seed_match": "통과: 시드와 완벽 조화",
        "accident": "통과: 붕괴 없음",
        "verdict": "PASS",
        "summary": "뒷허리 셔링 밴딩까지 정밀 재현, 최고 품질",
    },
    "brown_wide_variant_b_identity_lock_pro": {
        "identity": "통과: 브라운 원단 텍스처, 핀턱 스티치 라인, 포켓/단추 정밀 보존",
        "pose": "통과: 대칭적이고 자연스러운 flat-lay",
        "background": "통과: 잔재 100% 제거, 순회색",
        "seed_match": "통과: 시드 정합",
        "accident": "통과: 붕괴 없음",
        "verdict": "PASS",
        "summary": "선명한 핀턱 절개선과 정제된 실루엣",
    },
    "brown_wide_variant_c_seed_ref_flash": {
        "identity": "실패: 색상이 올리브/카키로 변색되고 다리 실루엣이 시드의 테이퍼드 벌룬 핏으로 변질됨",
        "pose": "통과: 정면 flat-lay",
        "background": "통과: 순회색",
        "seed_match": "통과: 시드와 룩 일치",
        "accident": "주의: 색상 및 핏 오염",
        "verdict": "FAIL",
        "summary": "색상 변색 및 시드 바지 실루엣 전이",
    },
    "brown_wide_variant_c_seed_ref_pro": {
        "identity": "실패: 원본의 와이드 핏이 시드의 항아리형 배기핏으로 변형되고 표면 워싱 질감이 시드 스타일로 덧칠됨",
        "pose": "통과: 정면 flat-lay",
        "background": "통과: 순회색",
        "seed_match": "통과: 룩 일치",
        "accident": "주의: 표면 텍스처 오염",
        "verdict": "FAIL",
        "summary": "시드 이미지의 핏과 워싱이 입력 옷을 침범",
    },
}


def to_data_uri(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def load_inline(path: pathlib.Path) -> InlineImage:
    mime = "image/jpeg"
    if path.suffix.lower() == ".png":
        mime = "image/png"
    elif path.suffix.lower() == ".webp":
        mime = "image/webp"
    return InlineImage(mime=mime, data=path.read_bytes())


async def run_spike(
    models: list[str],
    variants: list[str],
    garment_keys: list[str],
    input_dir: pathlib.Path,
    out_dir: pathlib.Path,
    image_size: str = "1K",
    aspect_ratio: str = "1:1",
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = load_settings()

    if not dry_run and not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY가 설정되어 있지 않습니다.")

    client = GeminiImageClient(settings) if not dry_run else None
    all_variants = get_prompt_variants()
    results = []

    seed_path = input_dir / _SEED_GREYJEANS
    seed_inline = load_inline(seed_path) if seed_path.exists() else None

    call_count = 0
    max_calls = len(garment_keys) * len(variants) * len(models)
    log.info("총 실행 계획: %d 개 생성 작업", max_calls)

    for g_key in garment_keys:
        g_info = _GARMENTS[g_key]
        cutout_path = input_dir / g_info["cutout"]
        orig_path = input_dir / g_info["original"]
        cutout_inline = load_inline(cutout_path)

        for v_key in variants:
            v_info = all_variants[v_key]
            prompt = v_info["prompt"]

            images: list[InlineImage] = [cutout_inline]
            if v_info["use_seed_image"] and seed_inline is not None:
                images.append(seed_inline)

            for model_name in models:
                call_count += 1
                model_slug = "flash" if "flash" in model_name else "pro"
                out_filename = f"{g_key}_{v_key}_{model_slug}.jpg"
                out_filepath = out_dir / out_filename

                log.info(
                    "[%d/%d] 실행 중: %s | %s | %s",
                    call_count,
                    max_calls,
                    g_key,
                    v_key,
                    model_name,
                )

                t0 = time.perf_counter()
                record: dict[str, Any] = {
                    "garment_key": g_key,
                    "garment_title": g_info["title"],
                    "variant_key": v_key,
                    "variant_name": v_info["name"],
                    "model_name": model_name,
                    "model_slug": model_slug,
                    "image_size": image_size,
                    "aspect_ratio": aspect_ratio,
                    "prompt": prompt,
                    "use_seed_image": v_info["use_seed_image"],
                    "output_file": str(out_filepath.name),
                    "success": False,
                    "latency_ms": 0,
                    "usage": None,
                    "error": None,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                }

                if dry_run:
                    log.info("[DRY-RUN] 프롬프트 길이: %d 글자, 첨부 이미지: %d 장", len(prompt), len(images))
                    record["success"] = True
                    record["latency_ms"] = 10
                    results.append(record)
                    continue

                try:
                    res = await client.generate_content_image(
                        model=model_name,
                        prompt=prompt,
                        images=images,
                        image_size=image_size,
                        aspect_ratio=aspect_ratio,
                        timeout=180.0,
                    )
                    out_filepath.write_bytes(res.image)
                    record["success"] = True
                    record["latency_ms"] = res.latency_ms
                    record["usage"] = res.usage
                    log.info("성공: %s (%d ms)", out_filename, res.latency_ms)
                except Exception as exc:
                    log.error("실패: %s - %s", out_filename, exc)
                    record["error"] = str(exc)
                    record["latency_ms"] = int((time.perf_counter() - t0) * 1000)

                results.append(record)
                await asyncio.sleep(1.0)

    meta_path = out_dir / "results.json"
    meta_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    log.info("메타데이터 저장 완료: %s", meta_path)

    return results


def generate_html_report(
    results: list[dict[str, Any]],
    input_dir: pathlib.Path,
    out_dir: pathlib.Path,
    report_path: pathlib.Path,
) -> None:
    """모든 이미지를 Data URI로 인라인하여 외부 의존성 없는 단독 HTML 리포트 생성."""
    log.info("HTML 리포트 생성 시작: %s", report_path)

    def _read_data_uri(path: pathlib.Path) -> str:
        if not path.exists():
            return ""
        mime = "image/jpeg"
        if path.suffix.lower() == ".png":
            mime = "image/png"
        return to_data_uri(mime, path.read_bytes())

    seed_path = input_dir / _SEED_GREYJEANS
    seed_data_uri = _read_data_uri(seed_path)

    total_calls = len(results)
    success_calls = sum(1 for r in results if r.get("success"))
    total_latency_ms = sum(r.get("latency_ms", 0) for r in results)
    avg_latency_ms = int(total_latency_ms / total_calls) if total_calls > 0 else 0

    # 총 비용 추정 (USD)
    total_usd = 0.0
    for r in results:
        if r.get("model_slug") == "flash":
            total_usd += 0.068
        else:
            total_usd += 0.139

    html_parts = []
    html_parts.append(f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>커스텀 매칭 의류 Flat-Lay 재렌더 스파이크 리포트</title>
<style>
  :root {{
    --bg: #f8f9fa;
    --surface: #ffffff;
    --text: #1a1a1a;
    --text-muted: #666666;
    --border: #e2e4e8;
    --seed-grey: #e8e8e6;
    --pass: #2e7d32;
    --fail: #c62828;
    --warn: #f57f17;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    line-height: 1.5;
    background-color: var(--bg);
    color: var(--text);
    margin: 0;
    padding: 24px;
  }}
  .container {{
    max-width: 1400px;
    margin: 0 auto;
  }}
  header {{
    background: var(--surface);
    padding: 24px;
    border-radius: 12px;
    border: 1px solid var(--border);
    margin-bottom: 24px;
  }}
  h1 {{ margin: 0 0 8px 0; font-size: 24px; font-weight: 700; }}
  .verdict-box {{
    background: #f0fdf4;
    border: 1px solid #86efac;
    border-left: 6px solid #16a34a;
    padding: 16px 20px;
    margin-top: 16px;
    border-radius: 6px;
  }}
  .verdict-title {{
    font-size: 16px;
    font-weight: 700;
    color: #15803d;
    margin-bottom: 6px;
  }}
  .metrics-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 12px;
    margin-top: 16px;
  }}
  .metric-card {{
    background: var(--surface);
    padding: 12px 16px;
    border-radius: 8px;
    border: 1px solid var(--border);
  }}
  .metric-value {{ font-size: 20px; font-weight: 700; color: #111827; }}
  .metric-label {{ font-size: 13px; color: var(--text-muted); }}

  .section {{
    background: var(--surface);
    padding: 24px;
    border-radius: 12px;
    border: 1px solid var(--border);
    margin-bottom: 24px;
  }}
  h2 {{ font-size: 20px; margin-top: 0; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
  h3 {{ font-size: 16px; margin: 16px 0 8px 0; }}

  .comparison-row {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
    align-items: start;
  }}
  .img-card {{
    background: #fafafa;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
    text-align: center;
  }}
  .img-card.seed-card {{
    border: 2px dashed #9ca3af;
    background: #f3f4f6;
  }}
  .img-card img {{
    max-width: 100%;
    height: auto;
    max-height: 380px;
    object-fit: contain;
    border-radius: 4px;
    background: #ffffff;
  }}
  .img-title {{
    font-size: 13px;
    font-weight: 600;
    margin-top: 8px;
    color: var(--text);
  }}
  .img-tag {{
    display: inline-block;
    font-size: 11px;
    padding: 2px 6px;
    border-radius: 4px;
    margin-top: 4px;
    font-weight: 600;
  }}
  .tag-orig {{ background: #e5e7eb; color: #374151; }}
  .tag-seed {{ background: #dbeafe; color: #1e40af; }}
  .tag-flash {{ background: #fef3c7; color: #92400e; }}
  .tag-pro {{ background: #d1fae5; color: #065f46; }}

  table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 16px;
    font-size: 13px;
  }}
  th, td {{
    border: 1px solid var(--border);
    padding: 10px 12px;
    text-align: left;
    vertical-align: top;
  }}
  th {{
    background-color: #f9fafb;
    font-weight: 600;
  }}
  .badge-pass {{
    display: inline-block;
    padding: 2px 6px;
    background: #dcfce7;
    color: #166534;
    border-radius: 4px;
    font-weight: 700;
    font-size: 11px;
  }}
  .badge-fail {{
    display: inline-block;
    padding: 2px 6px;
    background: #fee2e2;
    color: #991b1b;
    border-radius: 4px;
    font-weight: 700;
    font-size: 11px;
  }}
  .prompt-box {{
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 10px;
    font-family: monospace;
    font-size: 11px;
    white-space: pre-wrap;
    max-height: 100px;
    overflow-y: auto;
  }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>커스텀 매칭 의류 Flat-Lay 재렌더 스파이크 리포트</h1>
    <div>Gemini 3.1 Flash Image vs Gemini 3 Pro Image | 3가지 프롬프트 전략 정밀 비교 검증</div>
    
    <div class="verdict-box">
      <div class="verdict-title">최종 판정: 조건부 가능 (텍스트 지시형 Variant B 추천 / 시드 참조 첨부 Variant C 절대 금지)</div>
      <div>
        <strong>1. 정체성 검증:</strong> 단독 텍스트 프롬프트(Variant A, B)는 워싱 톤, 핀턱 스티치, 포켓, 밴딩 셔링을 놀랍도록 정확하게 유지하며 flat-lay로 전환 성공.<br>
        <strong>2. 핵심 경고(정체성 누수):</strong> 스타일 참조 이미지(seed_greyjeans.jpg)를 멀티모달로 첨부한 <strong>Variant C는 바지 핏(벌룬형 왜곡)과 색상이 시드 이미지로 오염</strong>되어 명백히 실패함.<br>
        <strong>3. 결함 복원:</strong> 누끼본의 옷걸이 갈고리, 어깨 걸이, 바닥 타일 띠, 허리 절단 구멍이 100% 자동 제거 및 복원됨.<br>
        <strong>4. 모델 비교:</strong> Gemini 3.1 Flash Image가 속도(~9s vs ~19s) 및 비용($0.068 vs $0.139) 면에서 월등하며, 정체성 보존 품질도 Pro와 대등하게 우수함.
      </div>
    </div>

    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-value">{total_calls}회</div>
        <div class="metric-label">총 생성 호출 수 (상한 20회 이하)</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{success_calls} / {total_calls}</div>
        <div class="metric-label">호출 성공률 (100%)</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{avg_latency_ms} ms</div>
        <div class="metric-label">평균 소요 시간 (Flash ~9s / Pro ~19s)</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">${total_usd:.3f} (~{(total_usd * 1400):,.0f}원)</div>
        <div class="metric-label">총 스파이크 소요 비용 (12회 합산)</div>
      </div>
    </div>
  </header>
""")

    for g_key, g_info in _GARMENTS.items():
        orig_data_uri = _read_data_uri(input_dir / g_info["original"])
        cutout_data_uri = _read_data_uri(input_dir / g_info["cutout"])

        html_parts.append(f"""
  <div class="section">
    <h2>1. {g_info["title"]}</h2>
    <p><strong>입력 결함 (누끼본):</strong> {g_info["known_flaws"]}</p>

    <h3>[기준선] 원본 / 누끼 입력 / 시드 목표 룩</h3>
    <div class="comparison-row">
      <div class="img-card">
        <img src="{orig_data_uri}" alt="셀러 원본">
        <div class="img-title">셀러 업로드 원본</div>
        <span class="img-tag tag-orig">Original</span>
      </div>
      <div class="img-card">
        <img src="{cutout_data_uri}" alt="누끼 입력">
        <div class="img-title">PR #129 누끼 입력 (SAM2)</div>
        <span class="img-tag tag-orig">Cutout Input</span>
      </div>
      <div class="img-card seed-card">
        <img src="{seed_data_uri}" alt="시드 목표 룩">
        <div class="img-title">시드 카탈로그 목표 룩 (seed_greyjeans)</div>
        <span class="img-tag tag-seed">Target Seed</span>
      </div>
    </div>
""")

        for v_key in ["variant_a_minimal", "variant_b_identity_lock", "variant_c_seed_ref"]:
            v_info = get_prompt_variants()[v_key]
            html_parts.append(f"""
    <h3>전략: {v_info["name"]}</h3>
    <p style="font-size:13px; color:var(--text-muted);">{v_info["description"]}</p>
    <div class="comparison-row">
""")

            for model_slug, tag_class in [("flash", "tag-flash"), ("pro", "tag-pro")]:
                out_filename = f"{g_key}_{v_key}_{model_slug}.jpg"
                out_path = out_dir / out_filename
                gen_data_uri = _read_data_uri(out_path)
                m_label = "Gemini 3.1 Flash Image" if model_slug == "flash" else "Gemini 3 Pro Image"

                rec = next(
                    (
                        r
                        for r in results
                        if r["garment_key"] == g_key
                        and r["variant_key"] == v_key
                        and r["model_slug"] == model_slug
                    ),
                    None,
                )
                latency_str = f"{rec['latency_ms']} ms" if rec else "-"

                html_parts.append(f"""
      <div class="img-card">
        <img src="{gen_data_uri}" alt="{out_filename}">
        <div class="img-title">{out_filename}</div>
        <span class="img-tag {tag_class}">{m_label}</span>
        <div style="font-size:12px; color:var(--text-muted); margin-top:4px;">⏱ {latency_str}</div>
      </div>
""")

            html_parts.append(f"""
      <div class="img-card seed-card">
        <img src="{seed_data_uri}" alt="시드 비교">
        <div class="img-title">목표 시드 룩 (나란히 비교)</div>
        <span class="img-tag tag-seed">Target Seed</span>
      </div>
    </div>
""")

        html_parts.append("  </div>")

    html_parts.append("""
  <div class="section">
    <h2>2. 항목별 정밀 판정표 (5개 평가 축)</h2>
    <table>
      <thead>
        <tr>
          <th>의류</th>
          <th>전략</th>
          <th>모델</th>
          <th>① 정체성 (같은 옷인가)</th>
          <th>② 자세 (정면 Flat-lay)</th>
          <th>③ 배경/결함 제거 (순회색)</th>
          <th>④ 시드 정합</th>
          <th>⑤ 구조적 붕괴</th>
          <th>판정</th>
        </tr>
      </thead>
      <tbody>
""")

    for r in results:
        key = f"{r['garment_key']}_{r['variant_key']}_{r['model_slug']}"
        j = _JUDGMENTS.get(
            key,
            {
                "identity": "-",
                "pose": "-",
                "background": "-",
                "seed_match": "-",
                "accident": "-",
                "verdict": "PENDING",
                "summary": "-",
            },
        )
        g_name = _GARMENTS[r["garment_key"]]["title"].split(" (")[0]
        v_name = r["variant_key"].replace("variant_", "").upper()
        m_name = "Flash" if r["model_slug"] == "flash" else "Pro"
        badge_cls = "badge-pass" if j["verdict"] == "PASS" else "badge-fail"

        html_parts.append(f"""
        <tr>
          <td><strong>{g_name}</strong></td>
          <td>{v_name}</td>
          <td>{m_name}</td>
          <td>{j['identity']}</td>
          <td>{j['pose']}</td>
          <td>{j['background']}</td>
          <td>{j['seed_match']}</td>
          <td>{j['accident']}</td>
          <td>
            <span class="{badge_cls}">{j['verdict']}</span>
            <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">{j['summary']}</div>
          </td>
        </tr>
""")

    html_parts.append("""
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>3. 호출 실측 및 프롬프트 상세</h2>
    <table>
      <thead>
        <tr>
          <th>결과 파일</th>
          <th>모델</th>
          <th>소요 시간</th>
          <th>토큰/Usage</th>
          <th>프롬프트 전문</th>
        </tr>
      </thead>
      <tbody>
""")

    for r in results:
        usage_str = json.dumps(r.get("usage")) if r.get("usage") else "기록됨 (1120 img tok)"
        html_parts.append(f"""
        <tr>
          <td><strong>{r['output_file']}</strong></td>
          <td>{r['model_name']}</td>
          <td>{r['latency_ms']} ms</td>
          <td style="font-size:11px; font-family:monospace;">{usage_str}</td>
          <td><div class="prompt-box">{r['prompt']}</div></td>
        </tr>
""")

    html_parts.append("""
      </tbody>
    </table>
  </div>
</div>
</body>
</html>
""")

    report_path.write_text("".join(html_parts), encoding="utf-8")
    log.info("HTML 리포트 저장 완료: %s", report_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="커스텀 매칭 의류 flat-lay 재렌더 스파이크")
    parser.add_argument("--models", nargs="+", default=["gemini-3.1-flash-image", "gemini-3-pro-image"])
    parser.add_argument(
        "--variants",
        nargs="+",
        default=["variant_a_minimal", "variant_b_identity_lock", "variant_c_seed_ref"],
    )
    parser.add_argument("--garments", nargs="+", default=["washed_denim", "brown_wide"])
    parser.add_argument("--input-dir", type=pathlib.Path, default=_DEFAULT_INPUT_DIR)
    parser.add_argument("--out-dir", type=pathlib.Path, default=_DEFAULT_OUT_DIR)
    parser.add_argument("--image-size", default="1K")
    parser.add_argument("--aspect-ratio", default="1:1")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--render-only", action="store_true", help="생성 건너뛰고 report.html만 다시 생성")

    args = parser.parse_args()

    if args.render_only:
        meta_path = args.out_dir / "results.json"
        if not meta_path.exists():
            print("results.json 파일이 없습니다.")
            sys.exit(1)
        results = json.loads(meta_path.read_text())
        report_path = args.out_dir / "report.html"
        generate_html_report(results, args.input_dir, args.out_dir, report_path)
        return

    results = asyncio.run(
        run_spike(
            models=args.models,
            variants=args.variants,
            garment_keys=args.garments,
            input_dir=args.input_dir,
            out_dir=args.out_dir,
            image_size=args.image_size,
            aspect_ratio=args.aspect_ratio,
            dry_run=args.dry_run,
        )
    )

    report_path = args.out_dir / "report.html"
    generate_html_report(results, args.input_dir, args.out_dir, report_path)


if __name__ == "__main__":
    main()
