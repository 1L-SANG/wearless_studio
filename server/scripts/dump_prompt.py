"""OFFLINE 마네킹 프롬프트 인스펙션 하니스 (Gemini·R2·DB·네트워크 전부 없음).

워커(app/workers/mannequin_job.py)는 실제 생성 프롬프트의 SHA256 해시만 이벤트로 남긴다
(step "prompt_rendered" · prompt_hash). 사람이 "material 블록 / knowledge 블록 / FIT PROFILE 이
정말 모델까지 도달하는가"를 눈으로 확인할 수단이 없다. 이 스크립트는 그 프롬프트 텍스트를
그대로 렌더해서 보여준다.

렌더 경로는 워커와 동일한 REAL 함수만 쓴다:
  load_prompt_template(settings)  → 실제 템플릿 파일(server/prompts/mannequin_generate_v1.txt)
  mannequin.prompt_context(...)   → MannequinPromptContext (워커가 쓰는 헬퍼)
  render_mannequin_prompt(...)    → ${토큰} 치환 + FIT PROFILE + PRODUCT CONTEXT 조립

의도적으로 임포트하지 않는 것: gemini/vision 클라이언트, boto3/R2, psycopg. 순수 렌더 경로만.

실행:
  cd server && .venv/bin/python scripts/dump_prompt.py            # 6개 픽스처 전부
  cd server && .venv/bin/python scripts/dump_prompt.py knit       # 하나만
  cd server && .venv/bin/python scripts/dump_prompt.py --list     # 픽스처 이름만
  cd server && .venv/bin/python scripts/dump_prompt.py silk --knowledge static --seller-canon shadow
"""

import argparse
import sys
from pathlib import Path

# `python scripts/dump_prompt.py` 로 파일 실행 시 sys.path[0]=scripts/ 라서 `app` 미탐지.
# server/ 를 명시적으로 얹어 CWD·실행형식(-m 여부)과 무관하게 임포트되게 한다.
SERVER = Path(__file__).resolve().parents[1]  # server/
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from app.agents import mannequin  # noqa: E402
from app.agents.prompts import load_prompt_template, render_mannequin_prompt  # noqa: E402
from app.config import load_settings  # noqa: E402

# 워커 _SLOT_LABEL 미러 (app/workers/mannequin_job.py). 워커 모듈을 임포트하면 gemini/PIL
# 의존이 딸려오므로, 프롬프트 렌더에 필요한 라벨만 여기 복제한다(값 표류 시 워커가 정본).
_SLOT_LABEL = {
    "Front": "front view of the garment",
    "Back": "back view of the garment",
    "Detail": "detail close-up of the garment (texture, stitching, trims, print)",
    "Fit": "fit reference — the garment worn on a real person (true length & how it sits)",
}


def _build_manifest(slots: list[str], has_match: bool) -> str:
    """[base, *prod(slot순), match] 순서의 역할 목록 — 워커 _build_manifest 와 동일 형식."""
    lines = ["1. Base mannequin — the canvas to dress (keep it identical)"]
    i = 2
    for s in slots:
        lines.append(f"{i}. {_SLOT_LABEL.get(s, 'view of the garment')}")
        i += 1
    if has_match:
        lines.append(
            f"{i}. matching BOTTOM garment — also dress the mannequin in this, "
            "coordinated with the top"
        )
    return "\n".join(lines)


# ── 픽스처 ────────────────────────────────────────────────────────────────────
# 각 픽스처 shape = product_analyst.distribute() 산출과 동일:
#   product = {"name", "clothingType"}  (DB 컬럼은 clothing_type — _product_block 은 둘 다 수용)
#   analysis = {materials[{name,ratio}], subCategory, targetGenders, fit, sellingPoints,
#               styleTags, fitProfile}
# fitProfile.category 는 fit_axes 카탈로그 축(top/pants/skirt/dress/outer) — analysis.subCategory
# (계약 enum: knit/jeans/…)와는 별개다. materials 의 한글 소재명은 materials.py alias 로 canon 된다.
FIXTURES: dict[str, dict] = {
    # KNIT — 울 니트. 소재 가이드 = wool fiber 블록 + knit stitch cue (subCategory=knit → knit_ctx).
    # seller 조정 축이 있어 CHANGES 섹션도 함께 렌더된다.
    "knit": {
        "note": "울 100% 니트 (top/knit) · seller 조정(fit) → CHANGES 섹션 시연",
        "product": {"name": "메리노 울 라운드넥 니트", "clothingType": "top"},
        "analysis": {
            "subCategory": "knit",
            "targetGenders": ["women"],
            "fit": "regular",
            "materials": [{"name": "울", "ratio": 100}],
            "sellingPoints": ["부드러운 촉감", "데일리 라운드넥"],
            "styleTags": ["basic", "daily"],
            "fitProfile": {
                "category": "top", "gender": "women",
                "axes": {"fit": "over", "length": "crop"},
                "source": "seller", "version": 1,
            },
        },
        "slots": ["Front", "Detail", "Fit"],
        "has_match": False,
        "adjusted_axes": ("fit",),
        "seller_canon": "off",
        "knowledge": "off",
    },
    # DENIM — 스판 데님 진. category/subCategory "bottom jeans" 의 'jean' 힌트 → construction override.
    "denim": {
        "note": "면98/스판2 데님 진 (bottom/jeans) · denim construction override + 스판 modifier",
        "product": {"name": "레귤러 워시 데님 팬츠", "clothingType": "bottom"},
        "analysis": {
            "subCategory": "jeans",
            "targetGenders": ["women", "men"],
            "fit": "regular",
            "materials": [{"name": "면", "ratio": 98}, {"name": "스판덱스", "ratio": 2}],
            "sellingPoints": ["논페이드 워싱", "미디 웨이스트"],
            "styleTags": ["casual", "daily"],
            "fitProfile": {
                "category": "pants", "gender": "women",
                "axes": {"cut": "wide", "length": "ankle"},
                "source": "auto", "version": 1,
            },
        },
        "slots": ["Front", "Back"],
        "has_match": False,
        "adjusted_axes": (),
        "seller_canon": "off",
        "knowledge": "off",
    },
    # SILK/SATIN — 실크 블라우스. dominant silk fiber 블록(광택/드레이프 언어).
    "silk": {
        "note": "실크 100% 블라우스 (top/shirt) · dominant silk fiber 블록 + knowledge=static 시연",
        "product": {"name": "새틴 실크 블라우스", "clothingType": "top"},
        "analysis": {
            "subCategory": "shirt",
            "targetGenders": ["women"],
            "fit": "regular",
            "materials": [{"name": "실크", "ratio": 100}],
            "sellingPoints": ["자연스러운 광택", "우아한 드레이프"],
            "styleTags": ["formal", "chic"],
            "fitProfile": {
                "category": "top", "gender": "women",
                "axes": {"fit": "regular", "length": "basic"},
                "source": "auto", "version": 1,
            },
        },
        "slots": ["Front", "Detail"],
        "has_match": False,
        "adjusted_axes": (),
        "seller_canon": "off",
        "knowledge": "static",  # COMPOSITION GUIDANCE 블록 시연 (top + formal 태그 매칭)
    },
    # LEATHER — 레더 재킷. gauze/leather 등 construction override 경로(섬유 미인식 → constr 키).
    "leather": {
        "note": "가죽 100% 재킷 (outer/jacket) · leather construction override",
        "product": {"name": "싱글 라이더 레더 재킷", "clothingType": "outer"},
        "analysis": {
            "subCategory": "jacket",
            "targetGenders": ["women", "men"],
            "fit": "regular",
            "materials": [{"name": "가죽", "ratio": 100}],
            "sellingPoints": ["소프트 램스킨 질감", "실버 지퍼 트림"],
            "styleTags": ["street", "trendy"],
            "fitProfile": {
                "category": "outer", "gender": "women",
                "axes": {"fit": "regular", "length": "basic"},
                "source": "auto", "version": 1,
            },
        },
        "slots": ["Front", "Back", "Detail"],
        "has_match": False,
        "adjusted_axes": (),
        "seller_canon": "off",
        "knowledge": "off",
    },
    # BLEND — 면60/폴리40. 상위 2섬유 frozenset → COMBO_BLOCK{cotton,polyester} 경로.
    "blend": {
        "note": "면60/폴리40 셔츠 (top/shirt) · cotton+polyester COMBO 블록 + knowledge=static",
        "product": {"name": "코튼 혼방 데일리 셔츠", "clothingType": "top"},
        "analysis": {
            "subCategory": "shirt",
            "targetGenders": ["men"],
            "fit": "regular",
            "materials": [{"name": "면", "ratio": 60}, {"name": "폴리에스터", "ratio": 40}],
            "sellingPoints": ["구김 적은 혼방", "데일리 베이직"],
            "styleTags": ["basic", "daily"],
            "fitProfile": {
                "category": "top", "gender": "men",
                "axes": {"fit": "regular"},
                "source": "auto", "version": 1,
            },
        },
        "slots": ["Front"],
        "has_match": False,
        "adjusted_axes": (),
        "seller_canon": "off",
        "knowledge": "static",
    },
    # UNKNOWN — 미인식 소재 → UNKNOWN_BLOCK(reference-first fallback). fitProfile 없음 →
    # include_legacy_fit=True 경로로 PRODUCT CONTEXT 에 레거시 '- Fit:' 줄이 나타난다(대조용).
    "unknown": {
        "note": "미인식 소재 · UNKNOWN_BLOCK + fitProfile 없음 → 레거시 '- Fit:' 줄 경로",
        "product": {"name": "신소재 기능성 상의", "clothingType": "top"},
        "analysis": {
            "subCategory": None,
            "targetGenders": [],
            "fit": "regular",
            "materials": [{"name": "특수복합소재", "ratio": 100}],
            "sellingPoints": ["미상 기능성 원단"],
            "styleTags": [],
            "fitProfile": None,
        },
        "slots": ["Front"],
        "has_match": False,
        "adjusted_axes": (),
        "seller_canon": "off",
        "knowledge": "off",
    },
}


def _extract_material_block(prompt: str) -> str:
    """렌더된 프롬프트에서 '- Material:' 줄 + 그 아래 렌더링 가이드 블록만 뽑아낸다.

    형식(app/agents/prompts._product_block · materials._wrap):
        - Material: <소재명 %>
        - Material rendering guidance:
          Render as <...>
          Keep this subordinate ...
    가이드 줄은 'guidance:' 헤더 또는 2칸 들여쓴 본문뿐이라 그 구간만 이어붙인다.
    """
    lines = prompt.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("- Material:"):
            out = [ln]
            j = i + 1
            while j < len(lines) and (
                lines[j].startswith("- Material rendering guidance:") or lines[j].startswith("  ")
            ):
                out.append(lines[j])
                j += 1
            return "\n".join(out)
    return "(no '- Material:' line — empty/unrecognized materials produced no block)"


def render_one(name: str, *, seller_canon: str | None = None, knowledge: str | None = None) -> str:
    """단일 픽스처를 워커와 동일 경로로 렌더해 리포트 문자열을 반환한다."""
    fx = FIXTURES[name]
    product = fx["product"]
    analysis = fx["analysis"]
    seller_canon = seller_canon or fx["seller_canon"]
    knowledge = knowledge or fx["knowledge"]

    settings = load_settings()
    template = load_prompt_template(settings)

    clothing_type = product.get("clothing_type") or product.get("clothingType") or "상의"
    base_gender = mannequin.select_base_gender(analysis)
    manifest = _build_manifest(fx["slots"], fx["has_match"])
    product_count = len(fx["slots"]) + (1 if fx["has_match"] else 0)

    ctx = mannequin.prompt_context(
        clothing_type=clothing_type,
        product_count=product_count,
        base_gender=base_gender,
        image_manifest=manifest,
        fit_profile=None,  # None → render 가 analysis['fitProfile'] 로 폴백(워커 동일 계약)
        adjusted_axes=fx["adjusted_axes"],
    )
    prompt = render_mannequin_prompt(
        template, ctx, product, analysis, seller_canon=seller_canon, knowledge=knowledge
    )

    bar = "=" * 78
    sub = "-" * 78
    parts = [
        bar,
        f"FIXTURE: {name}",
        f"  {fx['note']}",
        f"  flags: seller_canon={seller_canon}  knowledge={knowledge}"
        f"  base_gender={base_gender}  ${{clothingType}}={clothing_type}"
        f"  fitProfile={'yes' if analysis.get('fitProfile') else 'NONE'}",
        bar,
        "",
        "[MATERIAL BLOCK — the '- Material:' line + rendering guidance that reaches the model]",
        sub,
        _extract_material_block(prompt),
        sub,
        "",
        "[FULL RENDERED PROMPT]",
        sub,
        prompt,
        sub,
        "",
    ]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Offline mannequin prompt inspection harness (no Gemini/DB/network).",
    )
    ap.add_argument(
        "fixture", nargs="?", default=None,
        help=f"one of: {', '.join(FIXTURES)}. omitted → print all.",
    )
    ap.add_argument("--list", action="store_true", help="list fixture names and exit")
    ap.add_argument(
        "--seller-canon", choices=["off", "shadow", "enforce"], default=None,
        help="override seller_text_canonicalize for this run",
    )
    ap.add_argument(
        "--knowledge", choices=["off", "static"], default=None,
        help="override knowledge injection for this run",
    )
    args = ap.parse_args(argv)

    if args.list:
        for n, fx in FIXTURES.items():
            print(f"{n:9s} — {fx['note']}")
        return 0

    if args.fixture is not None and args.fixture not in FIXTURES:
        ap.error(f"unknown fixture {args.fixture!r}. choose from: {', '.join(FIXTURES)}")

    names = [args.fixture] if args.fixture else list(FIXTURES)
    for n in names:
        print(render_one(n, seller_canon=args.seller_canon, knowledge=args.knowledge))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
