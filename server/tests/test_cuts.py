"""컷 생성 (ADR-0004) — 스펙 정규화·프롬프트 렌더 단위 테스트.

구 agents/cut.py 의 계약이 cut_generator 로 이식됨(2026-07-07) — alias import 로 원 테스트를
유지한다. 구 /cuts:generate 라우트는 detail_page 파이프라인(main)으로 대체되어 라우트 검증
테스트는 제거: 스펙 위반은 이제 라우트 400 이 아니라 워커 정규화(ValueError→빈 슬롯/실패)로
드러난다 — 그 계약은 아래 normalize 단위 테스트가 지킨다.
"""

import json

import pytest

from app.agents import cut_generator as cut
from conftest import make_settings


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


def test_normalize_model_id_accepts_camel_and_snake_and_sanitizes():
    assert cut.normalize_spec({
        "cutType": "styling", "modelId": "  mA\nignore  "
    })["modelId"] == "mA ignore"
    assert cut.normalize_spec({"cutType": "horizon", "model_id": "mB"})["modelId"] == "mB"
    assert cut.normalize_spec({"cutType": "mirror"})["modelId"] is None


@pytest.mark.parametrize("value", ["open", "partial", "closed"])
def test_normalize_outer_closure_keeps_three_valid_states(value):
    spec = cut.normalize_spec(
        {"cutType": "styling", "outerClosureState": value}, clothing_type="outer")
    assert spec["outerClosureState"] == value


def test_normalize_outer_closure_defaults_open_and_accepts_snake_case():
    assert cut.normalize_spec(
        {"cutType": "horizon", "outerClosureState": "unknown"},
        clothing_type="outer")["outerClosureState"] == "open"
    assert cut.normalize_spec(
        {"cutType": "mirror", "outer_closure_state": "partial"},
        clothing_type="아우터")["outerClosureState"] == "partial"


def test_normalize_outer_closure_ignored_for_non_outer_and_product():
    assert cut.normalize_spec(
        {"cutType": "styling", "outerClosureState": "closed"},
        clothing_type="top")["outerClosureState"] is None
    assert cut.normalize_spec(
        {"cutType": "product", "outerClosureState": "closed"},
        clothing_type="outer")["outerClosureState"] is None


def test_normalize_unknown_cut_raises():
    with pytest.raises(ValueError):
        cut.normalize_spec({"cutType": "daily"})  # ADR-0003에서 폐기된 토큰


def test_virtual_model_loader_resolves_c_pair_and_excludes_product(tmp_path, monkeypatch, caplog):
    manifest_path = tmp_path / "virtual_models.json"
    manifest_path.write_text(json.dumps({
        "models": {
            "mA": {"views": {
                "face_front": {"key": "seed/mA/face.webp", "mime": "image/webp"},
                "grid_sedcard": {"key": "seed/mA/grid.png", "mime": "image/jpeg"},
            }},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(cut, "_DEFAULT_VIRTUAL_MODELS", str(manifest_path))
    cut.load_virtual_model_registry.cache_clear()
    try:
        assert set(cut.load_virtual_model_registry()) == {"mA"}
        person = cut.normalize_spec({"cutType": "styling", "modelId": "mA"})
        assert cut.resolve_virtual_model_assets(person) == (
            {"key": "seed/mA/face.webp", "mime": "image/webp", "bucket": "public"},
            {"key": "seed/mA/grid.png", "mime": "image/jpeg", "bucket": "public"},
        )
        product = cut.normalize_spec({"cutType": "product", "modelId": "mA"})
        assert cut.resolve_virtual_model_assets(product) is None
        unknown = cut.normalize_spec({"cutType": "mirror", "modelId": "missing"})
        assert cut.resolve_virtual_model_assets(unknown) is None
        assert "unknown virtual model missing" in caplog.text
    finally:
        cut.load_virtual_model_registry.cache_clear()


def test_normalize_space_variation_default_subtle():
    spec = cut.normalize_spec({"cutType": "horizon", "spaceGroupId": "sg1", "spaceVariation": "weird"})
    assert spec["spaceVariation"] == "subtle"


def test_normalize_ref_scope_defaults_and_clamps():
    # 미지정·구버전 저장분 → 'all' (콘티 refScope, 2026-07 섹션 개편)
    assert cut.normalize_spec({"cutType": "styling"})["refScope"] == "all"
    assert cut.normalize_spec({"cutType": "styling", "refScope": "weird"})["refScope"] == "all"  # 미지 값
    assert cut.normalize_spec({"cutType": "styling", "refScope": "pose"})["refScope"] == "pose"
    # 'bg'(배경만) 출시 — 스파이크 2026-07-12 로 성립 확인
    assert cut.normalize_spec({"cutType": "styling", "refScope": "bg"})["refScope"] == "bg"


def test_normalize_ref_scope_product_downgrades_to_all():
    # 제품컷은 사람·포즈가 없어 '배경만/포즈만'이 성립하지 않는다 → 통째 참조로 강등
    for scope in ("pose", "bg"):
        spec = cut.normalize_spec({"cutType": "product", "exampleId": "ex_1", "refScope": scope})
        assert spec["refScope"] == "all"


def test_render_ref_scope_bg_uses_plate_and_blocks_pose_garment_transfer():
    template = cut.load_cut_template()
    spec = cut.normalize_spec({
        "cutType": "styling", "direction": "front", "shot": "full",
        "exampleId": "ex_styling_top_full_1", "refScope": "bg",
    })
    manifest = cut.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False, mood_count=0, example_scope="bg")
    p = cut.render_cut_prompt(template, spec, product={}, analysis={}, clothing_type="top",
                              image_manifest=manifest)
    assert "EXAMPLE REFERENCE (scope: bg)" in manifest and "EMPTY SET plate" in manifest
    assert "follow ONLY the background" in p
    assert "Do NOT copy the example's pose" in p            # 포즈 유출 차단
    assert "shoes or accessories" in p                       # 의류·신발 유출 차단(실험서 관찰된 실패)
    assert "FRAMING OVERRIDE" in p                           # 예시 크롭보다 요청 샷이 우선
    # bg 자산 = 빈 무대 플레이트(전용 variant) 우선
    base = "https://assets.example.test/generated-examples"
    assert cut.resolve_example_asset("ex_styling_top_full_1", base, scope="bg").endswith("plate+1")


def test_normalize_ref_scope_in_space_forces_pose():
    # 같은 장소 세트 + 예시 = '포즈 예시' 계약 — 레거시(refScope 부재)·'all' 저장분도 서버가 강제
    legacy = cut.normalize_spec({"cutType": "styling", "spaceGroupId": "sg1", "exampleId": "ex_1"})
    assert legacy["refScope"] == "pose"
    explicit = cut.normalize_spec({"cutType": "styling", "spaceGroupId": "sg1", "exampleId": "ex_1", "refScope": "all"})
    assert explicit["refScope"] == "pose"
    # 예시가 없으면 강제 없음 — 배경 연속성([[SPACE]])만 작동
    no_example = cut.normalize_spec({"cutType": "styling", "spaceGroupId": "sg1"})
    assert no_example["refScope"] == "all"


def test_render_named_pose_overrides_pose_scope_example():
    # 포즈 직접 지정 + '포즈만' 예시 = 지시 충돌 → 예시 라인 전체 미적용 (레거시 in-space 포함)
    template = cut.load_cut_template()
    spec = cut.normalize_spec({
        "cutType": "styling", "direction": "front", "exampleId": "ex_1",
        "spaceGroupId": "sg1", "pose": "walk",   # in-space라 refScope는 'pose'로 강제됨
    })
    p = cut.render_cut_prompt(template, spec, product={}, analysis={}, clothing_type="top", image_manifest="x")
    assert "Composition nuance" not in p and "REFERENCE SCOPE" not in p
    resolved_manifest = cut.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope="pose")
    resolved = cut.render_cut_prompt(
        template, spec, product={}, analysis={}, clothing_type="top",
        image_manifest=resolved_manifest)
    assert "Composition nuance" not in resolved and "REFERENCE SCOPE" not in resolved


def test_render_ref_scope_pose_appends_guard():
    template = cut.load_cut_template()
    base = {"cutType": "styling", "direction": "front", "exampleId": "ex_1"}
    kw = dict(product={}, analysis={}, clothing_type="top", image_manifest="(no images)")
    pose_prompt = cut.render_cut_prompt(template, cut.normalize_spec({**base, "refScope": "pose"}), **kw)
    all_prompt = cut.render_cut_prompt(template, cut.normalize_spec(base), **kw)
    assert "REFERENCE SCOPE" in pose_prompt          # 포즈만 — 배경 미전이 가드 포함
    assert "REFERENCE SCOPE" not in all_prompt       # 전부 — 가드 없음(기존 동작 불변)
    assert "Composition nuance" in pose_prompt       # 예시 뉘앙스는 그대로 유지


def test_example_asset_pose_scope_prefers_cutout_variant():
    # 스파이크(2026-07-12) 확정: pose 스코프는 누끼(pose variant) 우선, 없으면 all 폴백
    base = "https://assets.example.test/generated-examples"
    all_url = cut.resolve_example_asset("ex_styling_top_full_1", base, scope="all")
    pose_url = cut.resolve_example_asset("ex_styling_top_full_1", base, scope="pose")
    assert all_url and all_url.endswith("DEV+styling+example+1")
    assert pose_url and pose_url.endswith("DEV+styling+cutout+1")
    # pose variant 미등록 예시는 all 로 폴백 (문자열 등록 형태)
    fallback = cut.resolve_example_asset("ex_horizon_top_full_1", base, scope="pose")
    assert fallback and fallback.endswith("DEV+horizon+example+1")


def test_example_asset_resolution_uses_registry_and_base_override():
    resolved = cut.resolve_example_asset(
        "ex_styling_top_full_1", "https://assets.example.test/generated-examples")
    assert resolved == (
        "https://assets.example.test/generated-examples/"
        "600x800/png?text=DEV+styling+example+1"
    )
    assert cut.resolve_example_asset("ex_not_registered") is None


def test_resolved_example_manifest_and_prompt_apply_all_scope():
    manifest = cut.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope="all")
    p = _render(
        {"cutType": "styling", "shot": "full", "exampleId": "ex_styling_top_full_1",
         "refScope": "all"},
        manifest=manifest,
    )
    assert "EXAMPLE REFERENCE (scope: all)" in manifest
    assert "source of background, lighting, mood, pose and composition" in manifest
    assert "follow the attached EXAMPLE REFERENCE's background/location" in p
    assert "Swap in the exact garment from PRODUCT references" in p
    assert "camera direction" in p and "remain\nfixed requirements" in p


def test_resolved_example_manifest_and_prompt_apply_pose_only_scope():
    manifest = cut.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope="pose")
    p = _render(
        {"cutType": "styling", "shot": "full", "exampleId": "ex_styling_top_full_1",
         "refScope": "pose"},
        manifest=manifest,
    )
    assert "EXAMPLE REFERENCE (scope: pose)" in manifest
    assert "source of pose and framing ONLY" in manifest
    assert "follow ONLY the pose and\nframing nuance" in p
    assert "Do NOT carry over the example's background, location or props" in p
    assert "follow the attached EXAMPLE REFERENCE's background/location" not in p


def test_unresolved_example_keeps_v0_nuance_only_fallback():
    spec = {"cutType": "styling", "shot": "full", "direction": "front",
            "exampleId": "ex_not_registered", "refScope": "all"}
    manifest = cut.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False, mood_count=0)
    p = _render(spec, manifest=manifest)
    assert cut.resolve_example_asset(spec["exampleId"]) is None
    assert "Composition nuance" in p
    assert "EXAMPLE REFERENCE" not in manifest
    assert "follow the attached EXAMPLE REFERENCE" not in p


def test_in_space_resolved_example_forces_pose_scope_prompt():
    spec = cut.normalize_spec({
        "cutType": "styling", "shot": "full", "exampleId": "ex_styling_top_full_1",
        "spaceGroupId": "sg1", "refScope": "all",
    })
    manifest = cut.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope=spec["refScope"])
    p = cut.render_cut_prompt(
        cut.load_cut_template(), spec, {}, {}, "top", manifest)
    assert spec["refScope"] == "pose"
    assert "EXAMPLE REFERENCE (scope: pose)" in manifest
    assert "SPACE CONTINUITY" in p
    assert "Do NOT carry over the example's background" in p
    assert "follow the attached EXAMPLE REFERENCE's background/location" not in p


def test_resolved_product_example_keeps_product_cut_invariants():
    spec = {"cutType": "product", "shot": "ghost", "exampleId": "ex_product_future",
            "refScope": "all"}
    manifest = cut.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope="all", example_is_product=True)
    p = _render(spec, manifest=manifest)
    assert "source of background, lighting, mood, framing and composition" in manifest
    assert "mood, pose and composition" not in manifest
    assert "never copy its garment, person, model identity or pose" in manifest
    assert "still a PRODUCT CUT" in p
    assert "do not add a person" in p
    assert "follow the attached EXAMPLE REFERENCE's background/location" in p


def test_resolved_all_side_keeps_camera_direction_invariant():
    manifest = cut.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope="all")
    p = _render(
        {"cutType": "styling", "shot": "full", "direction": "side",
         "exampleId": "ex_styling_top_full_1", "refScope": "all"},
        manifest=manifest)
    assert "Camera angle: a clear side profile" in p
    assert "camera direction" in p and "remain\nfixed requirements" in p


def test_dummy_example_base_is_dev_only_without_override():
    prod = make_settings(app_env="prod", example_asset_base_url=None)
    dev = make_settings(app_env="dev", example_asset_base_url=None)
    assert cut.resolve_example_asset("ex_styling_top_full_1")
    assert cut.resolve_example_asset(
        "ex_styling_top_full_1", prod.example_asset_base_url,
        allow_default_base=False) is None
    # Runtime loader's environment gate is covered without making an HTTP request in prod.
    import asyncio
    assert asyncio.run(cut.load_example_image(prod, "ex_styling_top_full_1")) is None
    assert dev.app_env == "dev"


# ---------- render_cut_prompt — 섹션 조립 ----------


def _render(spec_raw, clothing_type="top", product=None, analysis=None, manifest="1. PRODUCT — front view"):
    tpl = cut.load_cut_template()
    spec = cut.normalize_spec(spec_raw, clothing_type=clothing_type)
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


@pytest.mark.parametrize("state, phrase", [
    ("open", "FULLY OPEN"),
    ("partial", "PARTIALLY OPEN"),
    ("closed", "FULLY CLOSED"),
])
def test_render_outer_closure_states_and_hardware_guard(state, phrase):
    p = _render({
        "cutType": "styling", "shot": "full", "direction": "front",
        "outerClosureState": state, "exampleId": "ex_styling_outer_full_1",
    }, clothing_type="outer")
    assert phrase in p
    assert "overrides any different open/closed styling shown in EXAMPLE or MOOD images" in p
    assert "NEVER invent, remove, relocate or redesign closure hardware" in p
    assert "garment fidelity wins" in p
    assert p.index("Composition nuance") < p.index("OUTER FRONT OPENING")


def test_render_outer_closure_defaults_open_and_applies_to_mirror():
    p = _render({"cutType": "mirror", "shot": "full"}, clothing_type="outer")
    assert "FULLY OPEN" in p


def test_render_outer_closure_does_not_turn_side_or_back_to_show_front_hardware():
    for direction in ("side", "back"):
        p = _render({"cutType": "horizon", "direction": direction}, clothing_type="outer")
        assert "FULLY OPEN" in p
        assert "never change direction or framing to expose it" in p
    assert "clear side profile" in _render(
        {"cutType": "horizon", "direction": "side"}, clothing_type="outer")
    assert "from behind" in _render(
        {"cutType": "horizon", "direction": "back"}, clothing_type="outer")


def test_render_outer_closure_absent_for_non_outer_and_product():
    top = _render({"cutType": "horizon", "outerClosureState": "closed"}, clothing_type="top")
    product = _render({"cutType": "product", "outerClosureState": "closed"}, clothing_type="outer")
    assert "OUTER FRONT OPENING" not in top
    assert "OUTER FRONT OPENING" not in product


@pytest.mark.parametrize("category_key", ["clothingType", "clothing_type"])
def test_build_prompt_passes_product_category_to_outer_closure_normalization(category_key):
    p = cut.build_prompt(
        {"cutType": "horizon", "outerClosureState": "partial"},
        {category_key: "outer", "colors": []},
    )
    assert "PARTIALLY OPEN" in p


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
