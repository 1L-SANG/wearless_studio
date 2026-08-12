"""GARMENT-BODY CONTACT 블록 — 있는가, 그리고 **핏을 망가뜨리지 않는가**.

이 파일이 지키는 건 "옷이 몸에 붙었는가"가 아니다. 그건 이미지가 나와야 알 수 있다.
여기서 잠그는 건 그 블록이 **금지된 절대 규칙을 다시 들여오지 않는다**는 것이다:
가슴이 제일 넓어야 한다, 허리는 항상 잘록해야 한다, 여유는 몇 cm 뿐이다 — 이런 문장이
들어오는 순간 boxy/oversized 상품이 전부 슬림하게 렌더된다. 회귀는 조용히 온다.

블록을 확정시킨 A/B 하니스(`scripts/hybrid_*`)는 실험이 끝나 레포에 남기지 않았다. 그
하니스가 검증하던 것 중 **프로덕션에 남는 계약만** 여기로 옮겨 왔다 — 블록의 존재·위치,
금지 문구 부재, 니트 소재 가이드, 그리고 production 패키지가 실험 코드를 참조하지 않는다는 것.
"""
import pathlib
import re

import pytest

from app.agents import mannequin
from app.agents.materials import material_guidance
from app.agents.prompts import render_mannequin_prompt
from app.services import generation_input_strategy as gis

PROMPTS = pathlib.Path(__file__).resolve().parents[1] / "prompts"
EN = PROMPTS / "mannequin_generate_v1.txt"
KO = PROMPTS / "mannequin_generate_v1.ko.txt"

CONTACT_START = "<!-- GARMENT_BODY_CONTACT_BLOCK_START -->"
CONTACT_END = "<!-- GARMENT_BODY_CONTACT_BLOCK_END -->"


def _text(p):
    return p.read_text(encoding="utf-8")


def _has_block(text: str) -> bool:
    return CONTACT_START in text and CONTACT_END in text


def _block(p):
    body = _text(p).partition(CONTACT_START)[2].partition(CONTACT_END)[0]
    assert body.strip(), f"{p.name} has empty contact block"
    return body


# ── 위치 ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [EN, KO], ids=["en", "ko"])
def test_contact_block_is_fenced_in_both_templates(path):
    """마커가 양쪽에 다 있어야 한다 — MANNEQUIN_PROMPT_FILE 로 ko 가 켜져도 동작이 같도록."""
    assert _has_block(_text(path))


@pytest.mark.parametrize("path", [EN, KO], ids=["en", "ko"])
def test_contact_block_sits_inside_instruction_before_the_output_rules(path):
    t = _text(path)
    assert t.index("<instruction>") < t.index(CONTACT_START)
    assert t.index(CONTACT_END) < t.index("</instruction>")
    assert t.index(CONTACT_END) < t.index("<output format>")
    assert t.index(CONTACT_END) < t.index("<critical rules>")


# ── 금지된 절대 규칙 ─────────────────────────────────────────────────────────

#: 이 표현들은 실제 상품의 핏을 덮어쓴다. 하나라도 들어오면 boxy 상품이 슬림해진다.
FORBIDDEN = [
    r"widest at the (?:chest|bust)",
    r"waist must (?:always )?taper",
    r"must (?:always )?taper at the waist",
    r"ease must only be",
    r"only a few centimet",
    r"가슴이 (?:가장|제일) 넓",
    r"허리는 (?:항상|반드시)",
    r"몇 ?(?:cm|센티)",
]


@pytest.mark.parametrize("path", [EN, KO], ids=["en", "ko"])
@pytest.mark.parametrize("pattern", FORBIDDEN)
def test_contact_block_states_no_absolute_body_shape_rule(path, pattern):
    assert not re.search(pattern, _block(path), re.I), \
        f"{path.name} contact block re-introduced an absolute shape rule: {pattern}"


def test_contact_block_subordinates_itself_to_the_real_fit():
    en = _block(EN).lower()
    assert "preserving the product's true fit" in en
    for word in ("relaxed", "straight", "loose", "boxy", "oversized"):
        assert word in en, f"contact block does not protect {word} fit"
    assert "do not slim it" in en


def test_contact_block_never_touches_product_identity():
    """색·패턴·기장·넥라인 사양을 바꾸라는 말이 새로 들어오면 안 된다."""
    en = _block(EN).lower()
    for banned in ("change the colour", "change the color", "change the pattern",
                   "shorten", "lengthen", "add a button", "remove a button"):
        assert banned not in en


# ── 렌더 경로 ────────────────────────────────────────────────────────────────

def _render():
    ctx = mannequin.prompt_context(clothing_type="니트", product_count=1,
                                   base_gender="women", image_manifest="1. BASE MANNEQUIN")
    return render_mannequin_prompt(_text(EN), ctx, {"name": "니트"}, {})


def test_rendered_production_prompt_still_carries_the_block():
    assert _has_block(_render())


# ── 실험 코드가 프로덕션에 새지 않는다 ───────────────────────────────────────

#: 실험 전용 이름들. production 런타임이 이 중 하나라도 참조하면 실험 스위치가 출고된다.
#: 하니스 자체는 레포에 없지만, 되살릴 때 app/ 으로 새어 들어가는 것을 계속 막는다.
EXPERIMENT_SYMBOLS = ("hybrid_run", "hybrid_branches", "hybrid_legacy", "hybrid_metadata",
                      "hybrid_report", "hybrid_repeat_report", "strip_contact_block",
                      "wording_profile", "CONTACT_AB_ARMS", "LEGACY_AB_ARMS",
                      "legacy_prompt", "qa6")


def test_production_package_never_references_the_experiment_harness():
    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    hits = []
    for py in app_dir.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        for sym in EXPERIMENT_SYMBOLS:
            if sym in text:
                hits.append(f"{py.relative_to(app_dir)}: {sym}")
    assert not hits, "production code references experiment-only symbols: " + ", ".join(hits)


def test_production_package_never_imports_from_scripts():
    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    hits = [str(py) for py in app_dir.rglob("*.py")
            if re.search(r"^\s*(from|import)\s+scripts\b",
                         py.read_text(encoding="utf-8", errors="ignore"), re.M)]
    assert not hits, f"production imports scripts/: {hits}"


# ── 니트 소재 조각 ───────────────────────────────────────────────────────────

def test_knit_material_guidance_says_the_fabric_takes_the_body_volume():
    out = material_guidance([{"name": "울", "percent": 100}], "니트", "니트")
    assert out is not None
    low = out.lower()
    assert "takes their volume" in low or "takes that body's three-dimensional volume" in low
    assert "keeping the product's own fit and ease exactly as they are" in low


@pytest.mark.parametrize("pattern", FORBIDDEN)
def test_knit_material_guidance_adds_no_absolute_shape_rule(pattern):
    out = material_guidance([{"name": "울", "percent": 100}], "니트", "니트")
    assert not re.search(pattern, out, re.I)


def test_non_knit_material_guidance_is_untouched_by_this_change():
    """셔츠/티셔츠 회귀 방지 — 니트 맥락이 아니면 새 문장이 붙지 않는다."""
    out = material_guidance([{"name": "면", "percent": 100}], "셔츠", "셔츠")
    assert out is not None
    assert "takes their volume" not in out.lower()
    assert "knit yields to the body" not in out.lower()


# ── SAM 컷아웃 설명 ──────────────────────────────────────────────────────────

def test_canonical_reference_gives_proportions_but_not_the_contour():
    line = gis.CANONICAL_MANIFEST_LINE.lower()
    assert "proportions" in line
    assert "do not trace that contour" in line
    assert "flat-lay contour" in line


def test_canonical_reference_still_names_no_defect():
    """추가 증거지 교정 지시가 아니다 — 기존 계약 유지."""
    line = gis.CANONICAL_MANIFEST_LINE.lower()
    for word in ("fix", "correct", "restore", "wrong", "missing", "improve"):
        assert word not in line
