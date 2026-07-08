"""컷 생성 (ADR-0004) — 스펙 정규화·프롬프트 렌더 단위 테스트.

구 agents/cut.py 의 계약이 cut_generator 로 이식됨(2026-07-07) — alias import 로 원 테스트를
유지한다. 구 /cuts:generate 라우트는 detail_page 파이프라인(main)으로 대체되어 라우트 검증
테스트는 제거: 스펙 위반은 이제 라우트 400 이 아니라 워커 정규화(ValueError→빈 슬롯/실패)로
드러난다 — 그 계약은 아래 normalize 단위 테스트가 지킨다.
"""

import pytest

from app.agents import cut_generator as cut


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


# ---------- normalize_spec — 서버측 컷 계약 강제 ----------


def test_normalize_mirror_strips_direction_and_clamps():
    spec = cut.normalize_spec({
        "cutType": "mirror", "direction": "side", "shot": "medium",
        "faceExposure": "same", "pose": "walk",
    })
    assert spec["direction"] is None          # 거울샷은 방향 개념 없음
    assert spec["shot"] == "full"             # medium은 거울샷에 없음 → full
    assert spec["faceExposure"] == "hide"     # 기본 '폰으로 가림'
    assert spec["pose"] == "auto"             # 셀피 구도 자동 고정


def test_normalize_mirror_keeps_knee_and_show():
    spec = cut.normalize_spec({"cutType": "mirror", "shot": "knee", "faceExposure": "show"})
    assert spec["shot"] == "knee" and spec["faceExposure"] == "show"


def test_normalize_product_fallbacks():
    spec = cut.normalize_spec({"cutType": "product", "direction": "side", "shot": "medium"})
    assert spec["direction"] == "front"       # product는 front/back만
    assert spec["shot"] == "ghost"            # 사람컷 샷은 product에 없음 → ghost
    assert spec["faceExposure"] is None


def test_normalize_person_defaults_and_limits():
    spec = cut.normalize_spec({
        "cutType": "styling", "matchIds": ["a", "b", "c"], "refAssetIds": ["1", "2", "3", "4"],
    })
    assert spec["direction"] == "front" and spec["shot"] == "full"
    assert len(spec["matchIds"]) == 2         # 매칭 최대 2 (메인·서브)
    assert len(spec["refAssetIds"]) == 3      # 무드 레퍼런스 최대 3


def test_normalize_unknown_cut_raises():
    with pytest.raises(ValueError):
        cut.normalize_spec({"cutType": "daily"})  # ADR-0003에서 폐기된 토큰


def test_normalize_space_variation_default_subtle():
    spec = cut.normalize_spec({"cutType": "horizon", "spaceGroupId": "sg1", "spaceVariation": "weird"})
    assert spec["spaceVariation"] == "subtle"


# ---------- render_cut_prompt — 섹션 조립 ----------


def _render(spec_raw, clothing_type="top", product=None, analysis=None, manifest="1. PRODUCT — front view"):
    tpl = cut.load_cut_template()
    spec = cut.normalize_spec(spec_raw)
    return cut.render_cut_prompt(tpl, spec, product or {}, analysis or {}, clothing_type, manifest)


def test_render_mirror_prompt_sections():
    p = _render({"cutType": "mirror", "shot": "knee"},
                product={"name": "골지 니트", "clothing_type": "top"},
                analysis={"materials": [{"name": "코튼", "ratio": 60}]})
    assert "MIRROR SELFIE" in p               # 거울샷 섹션
    assert "smartphone naturally covers" in p  # 얼굴 기본 = 폰으로 가림
    assert "Camera angle" not in p            # 방향 지시 없음
    assert "${" not in p                      # 미해결 토큰 없음
    assert "PRODUCT CONTEXT" in p             # ground-truth 블록 주입
    assert "head to around the knees" in p    # knee × top 크롭


def test_render_bottom_close_uses_lower_crop():
    p = _render({"cutType": "horizon", "shot": "close", "direction": "front"}, clothing_type="bottom")
    assert "hip and thigh" in p               # close × bottom = 하체 클로즈업
    assert "seamless studio backdrop" in p    # 호리존 섹션


def test_render_product_flatlay_has_no_person_lines():
    p = _render({"cutType": "product", "shot": "flatlay", "direction": "back"})
    assert "laid flat" in p and "Show the back side" in p
    assert "Face handling" not in p           # 제품컷엔 얼굴 지시 없음


def test_render_space_group_line_only_when_grouped():
    grouped = _render({"cutType": "styling", "shot": "full", "spaceGroupId": "sg1"})
    solo = _render({"cutType": "styling", "shot": "full"})
    assert "SPACE CONTINUITY" in grouped and "subtle" in grouped
    assert "SPACE CONTINUITY" not in solo


def test_render_front_direction_declares_band():
    p = _render({"cutType": "styling", "shot": "full", "direction": "front"})
    assert "slight natural turn" in p         # front = 대역(band) — 살짝 비틈 허용 (ADR-0004)


def test_render_leaves_no_section_markers():
    for spec in ({"cutType": "mirror"}, {"cutType": "product", "shot": "hanger"},
                 {"cutType": "horizon", "shot": "medium"}):
        p = _render(spec)
        assert "[[" not in p                  # 섹션 마커가 모델 프롬프트에 새지 않는다


def test_render_example_selection_changes_prompt_deterministically():
    # 예시 선택은 무음 드롭되지 않는다 — v0: id 해시로 구도 뉘앙스를 결정적으로 반영 (ADR-0004 과도기)
    base = _render({"cutType": "styling", "shot": "full"})
    picked = _render({"cutType": "styling", "shot": "full", "exampleId": "ex_styling_top_full_2"})
    again = _render({"cutType": "styling", "shot": "full", "exampleId": "ex_styling_top_full_2"})
    assert "Composition nuance" not in base       # 미선택 → 뉘앙스 없음
    assert "Composition nuance" in picked         # 선택 → 반영
    assert picked == again                        # 같은 예시 = 같은 프롬프트 (결정적)


def test_render_example_ignored_for_product():
    p = _render({"cutType": "product", "shot": "ghost", "exampleId": "ex_product_top_ghost_1"})
    assert "Composition nuance" not in p          # 제품컷엔 사람 구도 뉘앙스 미적용


def test_render_example_mood_only_for_side_and_back():
    # band 규칙(ADR-0004): 사이드/뒷면 + 정면 계열 예시 = 분위기만 — 정면 구도 뉘앙스가 방향과 충돌하면 안 됨
    for direction in ("side", "back"):
        p = _render({"cutType": "horizon", "shot": "full", "direction": direction,
                     "exampleId": "ex_horizon_top_full_1"})
        assert "Composition nuance" not in p
    mirror = _render({"cutType": "mirror", "shot": "full", "exampleId": "ex_mirror_top_full_1"})
    assert "Composition nuance" in mirror         # 거울샷은 정면 대역 — 뉘앙스 적용


def test_render_raises_on_stray_section_marker():
    # 섹션 본문에 마커가 남은 오염 템플릿 — 가드가 잡아 잡 실패(예약 해제)로 이어져야 한다
    tpl = (
        "[[BASE]]\n${cutLabel} ${cutSection} ${shotLine} ${directionLine} ${faceLine} ${poseLine} ${spaceLine} ${imageManifest}\n"
        "[[CUT:styling]]\nstyling body with leftover [[UNRESOLVED]] marker\n"
        "[[SHOT:full]]full\n[[DIR:front]]front\n[[FACE:same]]neutral\n[[POSE:auto]]auto\n"
    )
    spec = cut.normalize_spec({"cutType": "styling"})
    with pytest.raises(ValueError):
        cut.render_cut_prompt(tpl, spec, {}, {}, "top", "")


# ---------- 라우트 검증 레벨 ----------
# (구 /cuts:generate 라우트 테스트는 라우트 폐기로 제거 — 헤더 주석 참고)


def test_wardrobe_route_requires_db(client, make_token):
    res = client.get("/v1/projects/any-id/wardrobe", headers=_auth(make_token))
    assert res.status_code == 503
