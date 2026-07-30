"""소재 → 렌더링 가이드 블록 characterization(회귀) 테스트.

app.agents.materials 는 순수 함수 모듈(IO 없음). 이 테스트는 현재 동작을 그대로 고정해
향후 프롬프트 튜닝이 조용히 회귀하지 못하게 잠근다. '버그처럼 보이는' 동작도 현재 동작
그대로 단언한다(수정 금지) — 별도 리포트만 남긴다.
"""

from app.agents.materials import (
    COMBO_BLOCK,
    FIBER_BLOCK,
    OVERRIDE_BLOCK,
    UNKNOWN_BLOCK,
    _ELASTANE_FIBER,
    _GUARD,
    _SUITING,
    _canonical,
    _construction_keys,
    _detect_construction,
    _elastane_mod,
    material_guidance,
    normalize,
)


# ─────────────────────────── _canonical ───────────────────────────

def test_canonical_exact_alias_hit_korean_and_english():
    assert _canonical("코튼") == "cotton"
    assert _canonical("면") == "cotton"
    assert _canonical("순면") == "cotton"
    assert _canonical("cotton") == "cotton"
    assert _canonical("폴리") == "polyester"


def test_canonical_is_case_and_whitespace_insensitive():
    assert _canonical("  COTTON  ") == "cotton"
    assert _canonical("Nylon") == "nylon"


def test_canonical_token_exact_ignores_label_words():
    # 라벨이 섞여도 토큰 단위 exact — '면'⊂'겉면' 오분류 없이 폴리에스터 토큰이 잡힌다.
    assert _canonical("겉면 폴리에스터") == "polyester"
    assert _canonical("면 혼방") == "cotton"


def test_canonical_safe_substring_reaches_loop():
    # 비-exact 토큰에 alias가 박혀 있으면 substring 매칭으로 canonical 해석한다.
    # (주의: '캐시미어울'·'pu레더'는 그 자체가 exact alias라 이 분기를 타지 않는다 →
    #  여기선 반드시 exact 가 아닌 합성 토큰을 써야 substring 루프(materials.py:130-133)에 도달)
    assert _canonical("코튼혼방") == "cotton"
    assert _canonical("스판혼용") == "elastane"


def test_canonical_substring_prefers_longest_alias():
    # 긴 alias 우선(materials.py:111 _SUBSTR_ALIASES 정렬): '폴리우레탄'(elastane)이
    # '폴리'(polyester)보다 먼저 매칭돼야 한다. 짧은 것 우선이면 polyester 로 오분류 —
    # 이 순서가 소재 오분류 방어의 핵심이다.
    assert _canonical("겉감폴리우레탄") == "elastane"


def test_canonical_one_char_syllable_only_matches_as_exact_token():
    # 면·견·청 은 1글자라 부분일치 금지 — exact 일 때만 인정.
    assert _canonical("면") == "cotton"
    assert _canonical("견") == "silk"
    assert _canonical("청") == "denim"
    # 긴 단어의 부분으로는 매칭되지 않는다.
    assert _canonical("겉면") is None
    assert _canonical("청바지") is None


def test_canonical_pe_does_not_match_as_substring():
    # 'pe'는 라틴 2글자라 substring alias 에서 제외 — 'peach'가 polyester 로 새면 안 됨.
    assert _canonical("pe") == "polyester"   # exact 은 인정
    assert _canonical("peach") is None       # substring 은 거부


def test_canonical_full_string_alias_beats_tokenization():
    # '썸머 니트'는 전체 문자열이 summerknit alias → 토큰 '니트'(knit) 보다 우선.
    assert _canonical("summer knit") == "summerknit"
    assert _canonical("썸머 니트") == "summerknit"


def test_canonical_unknown_and_empty_return_none():
    assert _canonical("unknownfiber") is None
    assert _canonical("") is None
    assert _canonical("   ") is None
    assert _canonical(None) is None


# ─────────────────────────── _elastane_mod ───────────────────────────

def test_elastane_mod_below_2_is_empty():
    assert _elastane_mod(1.9) == ""
    assert _elastane_mod(0) == ""


def test_elastane_mod_subtle_band_2_to_4():
    assert "subtle stretch recovery" in _elastane_mod(2)
    assert "subtle stretch recovery" in _elastane_mod(4)


def test_elastane_mod_noticeable_band_above_4_to_8():
    assert "noticeable stretch" in _elastane_mod(4.1)
    assert "noticeable stretch" in _elastane_mod(8)


def test_elastane_mod_high_stretch_above_8():
    assert "high-stretch with firm recovery" in _elastane_mod(8.1)
    assert "high-stretch with firm recovery" in _elastane_mod(50)


# ─────────────────────────── normalize ───────────────────────────

def test_normalize_merges_canonical_and_sorts_desc():
    out = normalize([{"name": "면", "ratio": 30}, {"name": "코튼", "ratio": 20},
                     {"name": "폴리", "ratio": 50}])
    # 면+코튼 병합(cotton 50), polyester 50 — 동률이므로 두 키가 다 존재.
    assert {m["key"] for m in out} == {"cotton", "polyester"}
    cotton = next(m for m in out if m["key"] == "cotton")
    assert cotton["ratio"] == 50.0
    # 정렬은 ratio 내림차순.
    assert all(out[i]["ratio"] >= out[i + 1]["ratio"] for i in range(len(out) - 1))


def test_normalize_renormalizes_sum_within_80_120_band():
    out = normalize([{"name": "면", "ratio": 45}, {"name": "폴리", "ratio": 45}])  # 합 90
    ratios = {m["key"]: m["ratio"] for m in out}
    assert ratios == {"cotton": 50.0, "polyester": 50.0}


def test_normalize_leaves_sum_outside_band_unnormalized():
    # 합 160 은 80~120 밖 → 재정규화 없음. 값 그대로 유지.
    out = normalize([{"name": "면", "ratio": 80}, {"name": "폴리", "ratio": 80}])
    assert {m["key"]: m["ratio"] for m in out} == {"cotton": 80.0, "polyester": 80.0}


def test_normalize_exactly_100_is_untouched():
    out = normalize([{"name": "면", "ratio": 70}, {"name": "폴리", "ratio": 30}])
    assert {m["key"]: m["ratio"] for m in out} == {"cotton": 70.0, "polyester": 30.0}


def test_normalize_no_ratios_gives_equal_split():
    out = normalize([{"name": "면"}, {"name": "폴리"}])
    assert {m["key"]: m["ratio"] for m in out} == {"cotton": 50.0, "polyester": 50.0}


def test_normalize_drops_sub_3pct_non_elastane_trace():
    # cotton 98 / rayon 2 (합 100, 재정규화 없음): rayon 2 < 3 → 제거.
    out = normalize([{"name": "면", "ratio": 98}, {"name": "레이온", "ratio": 2}])
    assert [m["key"] for m in out] == ["cotton"]


def test_normalize_keeps_elastane_at_2pct():
    # cotton 96 / spandex 2 (합 98 재정규화) → elastane ≈2.04 ≥2 유지.
    out = normalize([{"name": "면", "ratio": 96}, {"name": "스판", "ratio": 2}])
    keys = [m["key"] for m in out]
    assert "elastane" in keys and "cotton" in keys


def test_normalize_renorm_band_edges_are_inclusive():
    # 80·120 경계는 재정규화 발동(포함), 79·121 은 미발동 (materials.py:158 `80 <= total <= 120`).
    def r(a, b):
        return {m["key"]: m["ratio"] for m in normalize(
            [{"name": "면", "ratio": a}, {"name": "폴리", "ratio": b}])}
    assert r(40, 40) == {"cotton": 50.0, "polyester": 50.0}      # 합 80 → 재정규화
    assert r(60, 60) == {"cotton": 50.0, "polyester": 50.0}      # 합 120 → 재정규화
    assert r(39.5, 39.5) == {"cotton": 39.5, "polyester": 39.5}  # 합 79 → 그대로
    assert r(60.5, 60.5) == {"cotton": 60.5, "polyester": 60.5}  # 합 121 → 그대로


def test_normalize_renorm_can_drop_declared_2pct_elastane():
    # SUSPECTED BUG (report-only, 현재 동작 고정): cotton97/rayon3/elastane2 합=102 →
    # 재정규화(80~120)로 elastane 1.96(<2) 이 trace 제거 → 선언된 2% 스판이 조용히 사라진다.
    out = normalize([{"name": "면", "ratio": 97}, {"name": "레이온", "ratio": 3},
                     {"name": "스판", "ratio": 2}])
    assert "elastane" not in [m["key"] for m in out]  # 2% 스판이 소실되는 현재 동작


def test_normalize_all_trace_keeps_top1():
    # 전부 <3% (합 2, 재정규화 안 됨) → 최상위 1개만 남긴다.
    out = normalize([{"name": "면", "ratio": 1}, {"name": "폴리", "ratio": 1}])
    assert len(out) == 1
    assert out[0]["key"] == "cotton"


def test_normalize_excludes_construction_keys():
    # 데님은 construction → 섬유 정규화에서 제외(별도 신호로만).
    assert normalize([{"name": "데님", "ratio": 100}]) == []


def test_normalize_skips_non_dict_entries():
    out = normalize(["garbage", 42, None, {"name": "면", "ratio": 100}])
    assert out == [{"key": "cotton", "ratio": 100.0}]


def test_normalize_unknown_only_is_empty():
    assert normalize([{"name": "xyz", "ratio": 100}]) == []


def test_normalize_empty_list_is_empty():
    assert normalize([]) == []
    assert normalize(None) == []


def test_normalize_clamps_ratio_and_coerces_non_numeric():
    assert normalize([{"name": "면", "ratio": 150}]) == [{"key": "cotton", "ratio": 100.0}]
    # 음수는 0 으로 클램프 → cotton 0, poly 100 → cotton trace 제거.
    assert normalize([{"name": "면", "ratio": -5}, {"name": "폴리", "ratio": 100}]) == [
        {"key": "polyester", "ratio": 100.0}]
    # 문자열 ratio → 0.0.
    assert normalize([{"name": "면", "ratio": "lots"}, {"name": "폴리", "ratio": 50}]) == [
        {"key": "polyester", "ratio": 50.0}]


# ─────────────────────────── _construction_keys ───────────────────────────

def test_construction_keys_counts_regardless_of_ratio():
    # ratio 0/미입력이어도 신호로 잡고, 섬유(면)는 무시한다.
    keys = _construction_keys([{"name": "데님", "ratio": 0}, {"name": "니트"},
                               {"name": "면", "ratio": 100}])
    assert keys == {"denim", "knit"}


def test_construction_keys_ignores_non_dict_and_unknown():
    assert _construction_keys(["x", {"name": "xyz"}, None]) == set()
    assert _construction_keys([]) == set()


# ─────────────────────────── _detect_construction ───────────────────────────

def test_detect_construction_denim_via_ctx_or_key():
    assert _detect_construction("", "청바지 청", set()) == "denim"
    assert _detect_construction("denim jacket", "", set()) == "denim"
    assert _detect_construction("", "", {"denim"}) == "denim"


def test_detect_construction_leather():
    assert _detect_construction("가죽 자켓", "", set()) == "leather"
    assert _detect_construction("", "", {"leather"}) == "leather"


def test_detect_construction_brushed():
    assert _detect_construction("기모 후드", "", set()) == "brushed"
    assert _detect_construction("", "fleece", set()) == "brushed"
    assert _detect_construction("", "", {"brushed"}) == "brushed"


def test_detect_construction_none_when_no_hint():
    assert _detect_construction("shirt", "top", set()) is None
    assert _detect_construction(None, None, set()) is None


def test_detect_construction_precedence_denim_over_leather():
    # denim 이 leather 보다 먼저 검사됨.
    assert _detect_construction("데님 가죽", "", {"leather"}) == "denim"


# ─────────────────────────── material_guidance: construction override ───────────────────────────

def test_guidance_override_denim_wins_via_ctx():
    block = material_guidance([{"name": "면", "ratio": 100}], "denim jacket", "")
    assert OVERRIDE_BLOCK["denim"] in block


def test_guidance_override_denim_wins_via_material_key():
    block = material_guidance([{"name": "데님"}, {"name": "면", "ratio": 100}], "", "")
    assert OVERRIDE_BLOCK["denim"] in block


def test_guidance_override_leather():
    block = material_guidance([{"name": "가죽"}], "jacket", "")
    assert OVERRIDE_BLOCK["leather"] in block


def test_guidance_override_brushed_via_ctx():
    block = material_guidance([{"name": "폴리", "ratio": 100}], "기모 후드", "")
    assert OVERRIDE_BLOCK["brushed"] in block


def test_guidance_override_nonhard_nonknit_seersucker():
    # seersucker 는 hard(denim/leather/brushed) 도 knit 도 아니지만 construction override 로 라우팅.
    block = material_guidance([{"name": "시어서커"}, {"name": "면", "ratio": 100}], "", "")
    assert OVERRIDE_BLOCK["seersucker"] in block


def test_guidance_override_chiffon_and_mesh_and_gauze():
    assert OVERRIDE_BLOCK["chiffon"] in material_guidance([{"name": "시폰"}], "", "")
    assert OVERRIDE_BLOCK["mesh"] in material_guidance([{"name": "메쉬"}], "", "")
    assert OVERRIDE_BLOCK["gauze"] in material_guidance([{"name": "거즈"}], "", "")


# ─────────────────────────── material_guidance: no solid fibers ───────────────────────────

def test_guidance_knit_only_construction():
    block = material_guidance([{"name": "니트"}], "", "")
    assert OVERRIDE_BLOCK["knit"] in block


def test_guidance_elastane_only():
    block = material_guidance([{"name": "스판", "ratio": 100}], "", "")
    assert "Render as a stretch fabric." in block
    assert _ELASTANE_FIBER in block


def test_guidance_nothing_known_returns_unknown_block():
    block = material_guidance([{"name": "xyz", "ratio": 100}], "", "")
    assert UNKNOWN_BLOCK in block
    assert "Render as" not in block  # _unknown()은 'Render as' 접두를 쓰지 않는다.


# ─────────────────────────── material_guidance: suiting / combo / dominant / blend ───────────────────────────

def test_guidance_suiting_poly_rayon_elastane():
    block = material_guidance([{"name": "폴리", "ratio": 50}, {"name": "레이온", "ratio": 45},
                               {"name": "스판", "ratio": 5}], "", "")
    assert _SUITING in block


def test_guidance_combo_cotton_elastane_low_ratio():
    # elastane combo 는 저비율(3%)도 핏에 의미 → combo 경로.
    block = material_guidance([{"name": "면", "ratio": 97}, {"name": "스판", "ratio": 3}], "", "")
    assert COMBO_BLOCK[frozenset(["cotton", "elastane"])] in block


def test_guidance_combo_linen_cotton_60_40():
    # 비-elastane combo 는 cov≥85 & 2번째≥20 일 때만 → linen60/cotton40 통과.
    block = material_guidance([{"name": "린넨", "ratio": 60}, {"name": "면", "ratio": 40}], "", "")
    assert COMBO_BLOCK[frozenset(["linen", "cotton"])] in block


def test_guidance_combo_negative_second_fiber_below_20_falls_to_dominant():
    # cotton90/rayon10: 2번째 섬유 10<20 → combo 아님 → dominant cotton.
    block = material_guidance([{"name": "면", "ratio": 90}, {"name": "레이온", "ratio": 10}], "", "")
    assert FIBER_BLOCK["cotton"] in block
    assert COMBO_BLOCK[frozenset(["rayon", "cotton"])] not in block


def test_guidance_dominant_at_70_boundary():
    # cotton70/silk30 (silk 와 cotton 은 combo 아님) → dominant FIBER_BLOCK[cotton].
    block = material_guidance([{"name": "면", "ratio": 70}, {"name": "실크", "ratio": 30}], "", "")
    assert FIBER_BLOCK["cotton"] in block
    assert "blend" not in block


def test_guidance_below_70_boundary_falls_to_blend():
    # cotton69/silk31 → dominant 실패 → blend(리드 = cotton).
    block = material_guidance([{"name": "면", "ratio": 69}, {"name": "실크", "ratio": 31}], "", "")
    assert "cotton, silk blend" in block
    assert "let cotton lead the surface" in block


def test_guidance_blend_three_equal_fibers():
    block = material_guidance([{"name": "면", "ratio": 34}, {"name": "폴리", "ratio": 33},
                               {"name": "레이온", "ratio": 33}], "", "")
    assert "cotton, polyester, rayon blend" in block
    assert "let cotton lead the surface" in block


# ─────────────────────────── elastane modifier append + knit cue ───────────────────────────

def test_guidance_elastane_modifier_appended_in_dominant_path():
    # cotton80/poly14/ela6: poly 14<20 → combo 아님 → dominant cotton + elastane(6→noticeable).
    block = material_guidance([{"name": "면", "ratio": 80}, {"name": "폴리", "ratio": 14},
                               {"name": "스판", "ratio": 6}], "", "")
    assert FIBER_BLOCK["cotton"] in block
    assert "Spandex modifier: noticeable stretch" in block


def test_guidance_elastane_modifier_appended_in_combo_path():
    # cotton60/poly30/ela10: cotton+poly combo + elastane(10→high-stretch) 3섬유째로 보강.
    block = material_guidance([{"name": "면", "ratio": 60}, {"name": "폴리", "ratio": 30},
                               {"name": "스판", "ratio": 10}], "", "")
    assert COMBO_BLOCK[frozenset(["cotton", "polyester"])] in block
    assert "high-stretch with firm recovery" in block


def test_guidance_no_elastane_modifier_when_below_2pct():
    # cotton98/spandex1.9: elastane<2 → normalize 에서 제거 → modifier 없음.
    block = material_guidance([{"name": "면", "ratio": 98}, {"name": "스판", "ratio": 1.9}], "", "")
    assert FIBER_BLOCK["cotton"] in block
    assert "Spandex modifier" not in block


def test_guidance_knit_cue_added_in_dominant_path():
    block = material_guidance([{"name": "면", "ratio": 100}], "knit top", "")
    assert FIBER_BLOCK["cotton"] in block
    assert "Visible knit stitch structure faithful to the reference" in block


def test_guidance_knit_cue_added_in_blend_path_via_korean_subcategory():
    block = material_guidance([{"name": "면", "ratio": 34}, {"name": "폴리", "ratio": 33},
                               {"name": "레이온", "ratio": 33}], "", "니트")
    assert "cotton, polyester, rayon blend" in block
    assert "Visible knit stitch structure faithful to the reference" in block


def test_guidance_knit_cue_added_in_suiting_path():
    # 슈팅(poly+rayon+elastane) 경로도 knit_ctx 시 knit cue 를 붙인다(materials.py:228).
    block = material_guidance([{"name": "폴리", "ratio": 50}, {"name": "레이온", "ratio": 45},
                               {"name": "스판", "ratio": 5}], "knit", "")
    assert _SUITING in block
    assert "Visible knit stitch structure faithful to the reference" in block


def test_guidance_knit_cue_added_in_combo_path():
    # combo(cotton+poly) 경로도 knit_ctx 시 knit cue 를 붙인다(materials.py:240).
    block = material_guidance([{"name": "면", "ratio": 60}, {"name": "폴리", "ratio": 40}], "", "니트")
    assert COMBO_BLOCK[frozenset(["cotton", "polyester"])] in block
    assert "Visible knit stitch structure faithful to the reference" in block


def test_guidance_override_summerknit():
    # summerknit 은 hard 도 knit 도 아닌 construction → non-hard-non-knit 분기로 자기 블록 라우팅.
    block = material_guidance([{"name": "썸머니트"}], "", "")
    assert OVERRIDE_BLOCK["summerknit"] in block


# ─────────────────────────── returned block structure ───────────────────────────

def test_block_starts_with_label_and_ends_with_guard():
    block = material_guidance([{"name": "면", "ratio": 100}], "", "")
    assert block.startswith("- Material rendering guidance:")
    assert block.endswith(_GUARD)
    assert "Render as" in block


def test_material_guidance_empty_returns_unknown_block_not_none():
    # CHARACTERIZATION: docstring/타입힌트는 '없으면 None' 이라 하지만 실제로는 절대 None 을
    # 반환하지 않는다 — 빈 입력도 UNKNOWN_BLOCK 을 돌려준다. (suspected_source_bugs 참조)
    block = material_guidance([])
    assert block is not None
    assert UNKNOWN_BLOCK in block
    assert block.endswith(_GUARD)
