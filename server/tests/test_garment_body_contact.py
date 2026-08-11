"""GARMENT-BODY CONTACT 블록 — 있는가, 그리고 **핏을 망가뜨리지 않는가**.

이 파일이 지키는 건 "옷이 몸에 붙었는가"가 아니다. 그건 이미지가 나와야 알 수 있다.
여기서 잠그는 건 그 블록이 **금지된 절대 규칙을 다시 들여오지 않는다**는 것이다:
가슴이 제일 넓어야 한다, 허리는 항상 잘록해야 한다, 여유는 몇 cm 뿐이다 — 이런 문장이
들어오는 순간 boxy/oversized 상품이 전부 슬림하게 렌더된다. 회귀는 조용히 온다.

그리고 control arm(A1/B1)이 treatment arm(A2/B2)과 같아지지 않는지도 잠근다. 같아지면
A/B 는 "효과 없음"을 보고하는데, 사실은 아무것도 테스트하지 않은 것이다.
"""
import pathlib
import re

import pytest

from app.agents import mannequin
from app.agents.materials import material_guidance
from app.agents.prompts import render_mannequin_prompt
from app.services import generation_input_strategy as gis
from scripts import hybrid_branches as hb

PROMPTS = pathlib.Path(__file__).resolve().parents[1] / "prompts"
EN = PROMPTS / "mannequin_generate_v1.txt"
KO = PROMPTS / "mannequin_generate_v1.ko.txt"


def _text(p):
    return p.read_text(encoding="utf-8")


def _block(p):
    body = _text(p).partition(hb.CONTACT_START)[2].partition(hb.CONTACT_END)[0]
    assert body.strip(), f"{p.name} has empty contact block"
    return body


# ── 위치 ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [EN, KO], ids=["en", "ko"])
def test_contact_block_is_fenced_in_both_templates(path):
    """마커가 양쪽에 다 있어야 한다 — MANNEQUIN_PROMPT_FILE 로 ko 가 켜져도 동작이 같도록."""
    assert hb.has_contact_block(_text(path))


@pytest.mark.parametrize("path", [EN, KO], ids=["en", "ko"])
def test_contact_block_sits_inside_instruction_before_the_output_rules(path):
    t = _text(path)
    assert t.index("<instruction>") < t.index(hb.CONTACT_START)
    assert t.index(hb.CONTACT_END) < t.index("</instruction>")
    assert t.index(hb.CONTACT_END) < t.index("<output format>")
    assert t.index(hb.CONTACT_END) < t.index("<critical rules>")


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
    assert hb.has_contact_block(_render())


# ── control arm ──────────────────────────────────────────────────────────────

def test_strip_contact_block_removes_only_the_block():
    full = _render()
    control = hb.strip_contact_block(full)
    assert not hb.has_contact_block(control)
    assert "flat panel" not in control
    assert len(control) < len(full)
    # 나머지 지시는 한 줄도 잃지 않는다
    for keep in ("IMAGE 1 IS THE IMMUTABLE CANVAS", "PATTERN SCALE", "MATCHING BOTTOM",
                 "DETAIL view", "<output format>", "<critical rules>",
                 "- Product name: 니트"):
        assert keep in control


def test_control_arm_refuses_to_be_built_from_a_prompt_without_the_block():
    """조용히 같은 프롬프트를 돌려주면 A/B 가 '효과 없음'을 위조한다."""
    with pytest.raises(ValueError):
        hb.strip_contact_block("a prompt with no markers")


def test_four_arms_map_onto_two_evidence_sets_and_two_prompts():
    from scripts.hybrid_run import CONTACT_AB_ARMS
    arms = CONTACT_AB_ARMS
    assert set(arms) == {"A1", "A2", "B1", "B2"}
    assert arms["A1"].endswith("_nocontact") and not arms["A2"].endswith("_nocontact")
    assert arms["B1"].endswith("_nocontact") and not arms["B2"].endswith("_nocontact")
    assert not arms["A1"].startswith("sam2") and arms["B1"].startswith("sam2")


def test_control_arms_never_enter_the_production_selection():
    """control 도 legacy 도 측정 대상이지 출고 후보가 아니다."""
    from scripts.hybrid_run import CONTACT_AB_ARMS, LEGACY_AB_ARMS, PRODUCTION_BRANCHES
    experimental = ({b for b in CONTACT_AB_ARMS.values()}
                    | {b for b in LEGACY_AB_ARMS.values()}) - {"baseline", "sam2"}
    assert experimental and not (experimental & PRODUCTION_BRANCHES)
    assert {"baseline", "sam2"} <= PRODUCTION_BRANCHES


# ── production render, real product metadata ─────────────────────────────────

def test_production_prompt_for_the_real_grey_knit_carries_all_three_changes():
    """production 경로 그대로 렌더한 프롬프트에 세 가지가 동시에 들어간다.

    분석값 → materials/subCategory → prompt builder → material_guidance() 까지 실제로
    이어지는지를 본다. 코드 읽기가 아니라 렌더 결과로 확인하는 게 요점이다.
    """
    p = _knit_prompt(GREY_KNIT_MATERIALS)

    # 1) contact block
    assert hb.has_contact_block(p)
    assert "GARMENT-BODY CONTACT (mandatory)" in p
    assert "never appear as a flat panel positioned in front of it" in p

    # 2) knit guidance — 소재 줄과 가이드가 둘 다 있어야 한다
    assert "- Material: 아크릴 50%, 폴리에스터 30%, 나일론 20%" in p
    assert "- Material rendering guidance:" in p
    assert "Knit yields to the body underneath" in p
    assert "takes their volume" in p

    # 3) fit / ease 보존
    assert "WHILE PRESERVING THE PRODUCT'S TRUE FIT" in p
    assert "do not slim it, do not narrow it, do not add a waist it does not have" in p
    assert "keeping the product's own fit and ease exactly as they are" in p


def test_the_real_grey_knit_prompt_states_no_absolute_shape_rule():
    p = _knit_prompt(GREY_KNIT_MATERIALS)
    for pattern in FORBIDDEN:
        assert not re.search(pattern, p, re.I), f"rendered prompt carries {pattern}"


def test_a_non_knit_product_gets_the_contact_block_but_no_knit_wording():
    """회귀 방어: 셔츠는 contact block 만 받고 니트 문장은 한 줄도 받지 않는다."""
    ctx = mannequin.prompt_context(clothing_type="셔츠", product_count=1,
                                   base_gender="women", image_manifest="1. BASE MANNEQUIN")
    p = render_mannequin_prompt(_text(EN), ctx, {"name": "셔츠", "clothing_type": "셔츠"},
                                {"materials": [{"name": "면", "ratio": 100}],
                                 "subCategory": "shirt"})
    assert hb.has_contact_block(p)
    assert "Knit yields to the body underneath" not in p
    assert "Its thickness is the thickness of the yarn" not in p


# ── legacy replay ────────────────────────────────────────────────────────────

#: 두 경로가 다른 문장을 낳는다: 섬유가 잡히면 FIBER_BLOCK + _knit_cue,
#: 섬유가 불명이고 조직만 니트면 OVERRIDE_BLOCK["knit"]. 둘 다 되돌려져야 한다.
#: `grey_knit_real` 은 실제 production 분석값(analyses.payload, project efb48e5b-…)이다 —
#: floating 리포트의 그 상품. 검증 픽스처일 뿐 production 코드에는 없다.
GREY_KNIT_MATERIALS = [{"name": "아크릴", "ratio": 50},
                       {"name": "폴리에스터", "ratio": 30},
                       {"name": "나일론", "ratio": 20}]
GREY_KNIT_SUB_CATEGORY = "knit"

KNIT_CASES = {"fiber+knit_cue": [{"name": "울", "ratio": 100}],
              "construction_override": [{"name": "니트"}],
              "grey_knit_real": GREY_KNIT_MATERIALS}


def _knit_prompt(mats):
    ctx = mannequin.prompt_context(clothing_type="니트", product_count=1,
                                   base_gender="women", image_manifest="1. BASE MANNEQUIN")
    return render_mannequin_prompt(
        _text(EN), ctx, {"name": "울 니트", "clothing_type": "니트"},
        {"materials": mats, "subCategory": "니트"})


@pytest.mark.parametrize("mats", KNIT_CASES.values(), ids=list(KNIT_CASES))
def test_legacy_replay_reverts_all_three_axes_on_a_knit_prompt(mats):
    from scripts import hybrid_legacy as hlg
    new = _knit_prompt(mats)
    assert hlg.KNIT_OVERRIDE_ADDITION in new or hlg.KNIT_CUE_ADDITION in new, \
        "knit guidance never reached the prompt — the A/B would measure nothing"
    old, applied = hlg.legacy_prompt(new)
    assert applied["contactBlock"] is True
    assert applied["knitOverride"] or applied["knitCue"]
    assert not hb.has_contact_block(old)
    assert hlg.KNIT_OVERRIDE_ADDITION not in old
    assert hlg.KNIT_CUE_ADDITION not in old
    assert len(old) < len(new)


def test_both_knit_paths_are_covered_between_the_two_cases():
    """한 경로만 되돌려도 통과하는 테스트가 되면 안 된다."""
    from scripts import hybrid_legacy as hlg
    fired = set()
    for mats in KNIT_CASES.values():
        _, applied = hlg.legacy_prompt(_knit_prompt(mats))
        fired |= {k for k, v in applied.items() if v and k != "contactBlock"}
    assert fired == {"knitOverride", "knitCue"}


def test_legacy_replay_keeps_everything_it_did_not_revert():
    from scripts import hybrid_legacy as hlg
    old, _ = hlg.legacy_prompt(_knit_prompt(KNIT_CASES["fiber+knit_cue"]))
    for keep in ("IMAGE 1 IS THE IMMUTABLE CANVAS", "PATTERN SCALE", "<critical rules>",
                 "Material rendering guidance",
                 "Visible knit stitch structure faithful to the reference"):
        assert keep in old


def test_legacy_replay_refuses_to_run_when_production_wording_drifts(monkeypatch):
    """production 문구가 바뀌면 legacy arm 은 조용히 new arm 과 같아진다 — 그 전에 죽어야 한다."""
    from app.agents import materials as m
    from scripts import hybrid_legacy as hlg
    monkeypatch.setitem(m.OVERRIDE_BLOCK, "knit", "something else entirely")
    with pytest.raises(RuntimeError, match="out of sync"):
        hlg.assert_in_sync()
    with pytest.raises(RuntimeError):
        hlg.legacy_prompt(_knit_prompt(KNIT_CASES["fiber+knit_cue"]))


def test_legacy_sam_arm_uses_the_pre_change_cutout_wording():
    legacy = hb.sam_augmented_prompt("P", ["Front"], ["Front"], "top", legacy_cutout=True)
    new = hb.sam_augmented_prompt("P", ["Front"], ["Front"], "top")
    assert "DETAIL EVIDENCE ONLY" in legacy
    assert "own nothing about shape" in legacy
    assert "FABRIC AND THE FITTINGS, nothing else" in legacy
    assert "PROPORTION AND CONSTRUCTION EVIDENCE" not in legacy
    assert "What they are NOT is a shape to trace." not in legacy
    assert legacy != new
    # 되돌린 건 문구뿐 — 붙임 금지 규칙은 legacy 에도 그대로 있었다
    assert "Do NOT paste a cutout onto the mannequin." in legacy


def test_every_declared_branch_has_a_wording_profile():
    from scripts import hybrid_legacy as hlg
    from scripts.hybrid_run import CONTACT_AB_ARMS, LEGACY_AB_ARMS
    for b in set(CONTACT_AB_ARMS.values()) | set(LEGACY_AB_ARMS.values()):
        assert b in hlg.BRANCH_WORDING
        p = hlg.wording_profile(b)
        assert set(p) == {"contactBlock", "knitGuidance", "cutoutWording"}


def test_nocontact_arms_are_not_legacy_arms():
    """A1/B1 은 knit·cutout 은 새 문구를 그대로 쓴다 — 라벨이 이걸 잘못 말하면 결론이 틀린다."""
    from scripts import hybrid_legacy as hlg
    for b in ("baseline_nocontact", "sam2_nocontact"):
        contact, knit, cutout = hlg.BRANCH_WORDING[b]
        assert contact == "absent" and knit == "new"
        assert cutout in (None, "new")
    assert hlg.BRANCH_WORDING["legacy_baseline"][1] == "legacy"
    assert hlg.BRANCH_WORDING["legacy_sam2"][2] == "legacy"


#: 실험 전용 이름들. production 런타임이 이 중 하나라도 참조하면 실험 스위치가 출고된다.
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


def test_harness_feeds_material_metadata_so_knit_guidance_can_fire():
    """analysis={} 이면 material_guidance 자체가 안 돌아 knit A/B 가 공전한다."""
    import scripts.hybrid_run as hr
    src = pathlib.Path(hr.__file__).read_text(encoding="utf-8")
    assert 'analysis = {"materials"' in src
    assert "render_mannequin_prompt(template, ctx, product, analysis)" in src


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
