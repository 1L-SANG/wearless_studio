"""컷 생성 (ADR-0004) — 스펙 정규화·프롬프트 렌더 단위 테스트.

구 agents/cut.py 의 계약이 cut_generator 로 이식됨(2026-07-07) — alias import 로 원 테스트를
유지한다. 구 /cuts:generate 라우트는 detail_page 파이프라인(main)으로 대체되어 라우트 검증
테스트는 제거: 스펙 위반은 이제 라우트 400 이 아니라 워커 정규화(ValueError→빈 슬롯/실패)로
드러난다 — 그 계약은 아래 normalize 단위 테스트가 지킨다.
"""

import json

import pytest

from app.agents import content_roles
from app.agents import cut_generator as cut
from conftest import make_settings


@pytest.fixture
def dev_example_registry(tmp_path, monkeypatch):
    """릴리스된 운영 카탈로그와 무관한 레지스트리 해석 테스트 픽스처."""
    registry = {
        "_meta": {"defaultBaseUrl": "https://placehold.co"},
        "assets": {
            "ex_styling_top_full_1": {
                "all": "600x800/png?text=DEV+styling+example+1",
                "pose": "600x800/png?text=DEV+styling+cutout+1",
                "bg": "600x800/png?text=DEV+styling+plate+1",
            },
            "ex_horizon_top_full_1": {
                "all": "600x800/png?text=DEV+horizon+example+1",
            },
        },
    }
    path = tmp_path / "example_assets.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(cut, "_DEFAULT_EXAMPLE_ASSETS", str(path))
    cut.load_example_asset_registry.cache_clear()
    try:
        yield
    finally:
        cut.load_example_asset_registry.cache_clear()


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


# ---------- normalize_spec — 서버측 컷 계약 강제 ----------


def test_canonical_content_role_wins_conflicting_recipe():
    block = content_roles.canonicalize_storyboard_block({
        "id": "hero-1",
        "source": "ai",
        "contentRole": "hero",
        "sectionRole": "product",
        "cutType": "product",
        "direction": "back",
        "shot": "detail",
    })

    assert block["contentRole"] == "hero"
    assert block["sectionRole"] == "benefit"
    assert block["cutType"] == "styling"
    assert block["direction"] == "back"
    assert block["shot"] == "full"


def test_canonical_benefit_template_uses_medium_shot():
    block = content_roles.canonicalize_storyboard_block({
        "source": "ai", "contentRole": "benefit",
    })

    assert (block["cutType"], block["direction"], block["shot"]) == (
        "horizon", "front", "medium",
    )


def test_canonical_ignores_retired_kind_and_infers_from_cut_type():
    retired = content_roles.canonicalize_storyboard_block({
        "kind": "hook", "cutType": "product", "shot": "detail",
    })
    fit = content_roles.canonicalize_storyboard_block({
        "cutType": "horizon", "direction": "side", "shot": "medium",
    })

    assert (retired["contentRole"], retired["sectionRole"], retired["cutType"], retired["shot"]) == (
        "detail", "product", "product", "detail",
    )
    assert (fit["contentRole"], fit["sectionRole"], fit["cutType"]) == (
        "fit", "fit", "horizon",
    )
    assert (fit["direction"], fit["shot"]) == ("side", "medium")
    assert retired["taxonomyVersion"] == 2
    assert "kind" not in retired

    stored = content_roles.canonicalize_storyboard_block({
        "kind": "hook", "cutType": "product", "shot": "detail",
    }, for_storage=True)
    assert stored["taxonomyVersion"] == 2
    assert "kind" not in stored


def test_canonical_mine_and_custom_blocks_do_not_invent_ai_recipe():
    mine = content_roles.canonicalize_storyboard_block({
        "id": "mine-1",
        "source": "mine",
        "cutType": "styling",
        "ownImages": ["asset-1"],
    })
    custom = content_roles.canonicalize_storyboard_block({
        "id": "custom-1",
        "source": "ai",
        "contentRole": "custom",
        "sectionRole": "benefit",
        "cutType": "styling",
        "direction": "side",
        "shot": "close",
    })

    assert mine["contentRole"] == "custom"
    assert mine["cutType"] is None
    assert mine["taxonomyVersion"] == 2
    assert mine["ownImages"] == ["asset-1"]
    assert custom["contentRole"] == "custom"
    assert custom["sectionRole"] == "benefit"
    assert (custom["cutType"], custom["direction"], custom["shot"]) == (
        "styling", "side", "full",
    )


def test_canonical_product_shot_realigns_hidden_detail_role():
    detail = content_roles.canonicalize_storyboard_block({
        "contentRole": "detail", "cutType": "horizon", "shot": "full",
    })
    overview = content_roles.canonicalize_storyboard_block({
        "contentRole": "productOverview", "cutType": "product", "shot": "detail",
        "matchIds": ["pants-1"], "outerClosureState": "closed",
    })

    assert (detail["sectionRole"], detail["cutType"], detail["shot"]) == (
        "product", "product", "detail",
    )
    assert (overview["contentRole"], overview["sectionRole"], overview["cutType"], overview["shot"]) == (
        "detail", "product", "product", "detail",
    )
    assert overview["matchIds"] == []
    assert overview["outerClosureState"] is None


def test_canonical_storyboard_list_returns_normalized_copies():
    raw = [
        {"id": "b1", "kind": "selling", "cutType": "styling", "shot": "full"},
        {"id": "b2", "source": "mine", "ownImages": ["asset-2"]},
    ]

    normalized = content_roles.canonicalize_storyboard(raw)

    assert normalized is not raw
    assert normalized[0] is not raw[0]
    assert normalized[0]["contentRole"] == "coordination"
    assert normalized[0]["cutType"] == "styling"
    assert "kind" not in normalized[0]
    assert normalized[1]["contentRole"] == "custom"
    assert normalized[1]["cutType"] is None
    assert "contentRole" not in raw[0]  # 호출자의 원본 저장본을 제자리에서 바꾸지 않는다.


def test_canonical_storyboard_stably_orders_the_three_sections():
    normalized = content_roles.canonicalize_storyboard([
        {"id": "product-1", "source": "ai", "contentRole": "productOverview"},
        {"id": "fit-1", "source": "ai", "contentRole": "fit"},
        {"id": "benefit-1", "source": "ai", "contentRole": "hero"},
        {"id": "fit-2", "source": "ai", "contentRole": "coordination"},
        {"id": "custom", "source": "mine"},
        {"id": "benefit-2", "source": "ai", "contentRole": "benefit"},
    ])

    assert [block["id"] for block in normalized] == [
        "benefit-1", "benefit-2", "fit-1", "fit-2", "custom", "product-1",
    ]
    assert normalized[4]["sectionRole"] == "fit"  # 앞 이웃 섹션을 상속해 원래 위치를 지킨다.


def test_canonical_storyboard_assigns_hidden_roles_and_only_one_hero():
    normalized = content_roles.canonicalize_storyboard([
        {"id": "mine", "source": "mine", "sectionRole": "benefit"},
        {
            "id": "first-ai", "source": "ai", "sectionRole": "benefit",
            "contentRole": "benefit", "cutType": "horizon", "shot": "medium",
            "exampleId": "old-example",
        },
        {
            "id": "second-ai", "source": "ai", "sectionRole": "benefit",
            "contentRole": "hero", "cutType": "styling", "shot": "full",
        },
        {
            "id": "fit-ai", "source": "ai", "sectionRole": "fit",
            "contentRole": "custom",
        },
    ])

    assert [block["contentRole"] for block in normalized] == [
        "custom", "hero", "benefit", "coordination",
    ]
    assert normalized[1]["cutType"] == "horizon"
    assert normalized[1]["exampleId"] == "old-example"
    assert normalized[2]["cutType"] == "styling"
    assert normalized[3]["cutType"] == "styling"


def test_canonical_storyboard_section_wins_stale_internal_role_and_recipe():
    normalized = content_roles.canonicalize_storyboard([
        {
            "id": "benefit-with-fit-recipe", "source": "ai", "sectionRole": "benefit",
            "cutType": "horizon", "direction": "front", "shot": "medium",
        },
        {
            "id": "product-with-fit-role", "source": "ai", "sectionRole": "product",
            "contentRole": "fit", "cutType": "horizon", "direction": "side", "shot": "full",
        },
    ])

    assert [block["id"] for block in normalized] == [
        "benefit-with-fit-recipe", "product-with-fit-role",
    ]
    assert (
        normalized[0]["sectionRole"], normalized[0]["contentRole"], normalized[0]["cutType"]
    ) == ("benefit", "hero", "horizon")
    assert (
        normalized[1]["sectionRole"], normalized[1]["contentRole"], normalized[1]["cutType"]
    ) == ("product", "productOverview", "product")


def test_canonical_storyboard_keeps_example_when_auto_role_changes_but_selected_cut_does_not():
    normalized = content_roles.canonicalize_storyboard([{
        "id": "first-benefit", "source": "ai", "sectionRole": "benefit",
        "contentRole": "benefit", "cutType": "horizon", "direction": "front", "shot": "medium",
        "exampleId": "example-1", "baseThumb": "base.png", "thumb": "example.png",
    }])

    assert normalized[0]["contentRole"] == "hero"
    assert normalized[0]["cutType"] == "horizon"
    assert normalized[0]["exampleId"] == "example-1"
    assert normalized[0]["baseThumb"] == "base.png"
    assert normalized[0]["thumb"] == "example.png"


def test_canonical_selected_fit_cut_realigns_hidden_role():
    normalized = content_roles.canonicalize_storyboard_block({
        "source": "ai", "sectionRole": "fit", "contentRole": "fit",
        "cutType": "styling", "direction": "side", "shot": "medium",
    })

    assert (normalized["sectionRole"], normalized["contentRole"], normalized["cutType"]) == (
        "fit", "coordination", "styling",
    )
    assert (normalized["direction"], normalized["shot"]) == ("side", "medium")


def test_normalize_cut_type_only_request_uses_defensive_inference():
    spec = cut.normalize_spec({
        "cutType": "horizon", "direction": "side", "shot": "medium",
    })

    assert (spec["cutType"], spec["direction"], spec["shot"]) == (
        "horizon", "side", "medium",
    )


def test_normalize_spec_uses_content_role_before_conflicting_cut_fields():
    spec = cut.normalize_spec({
        "contentRole": "realWear",
        "cutType": "product",
        "direction": "front",
        "shot": "ghost",
    })

    assert (spec["cutType"], spec["direction"], spec["shot"]) == (
        "mirror", None, "full",
    )


def test_normalize_mirror_strips_direction_and_clamps():
    spec = cut.normalize_spec({
        "cutType": "mirror", "direction": "side", "shot": "close",
        "faceExposure": "same", "pose": "walk",
    })
    assert spec["direction"] is None          # 거울샷은 방향 개념 없음
    assert spec["shot"] == "full"             # 폐기 샷은 역할 기본값으로 복귀
    assert spec["faceExposure"] == "hide"     # 기본 '폰으로 가림'
    assert spec["pose"] == "auto"             # 셀피 구도 자동 고정


def test_normalize_mirror_keeps_medium_and_show():
    spec = cut.normalize_spec({"cutType": "mirror", "shot": "medium", "faceExposure": "show"})
    assert spec["shot"] == "medium" and spec["faceExposure"] == "show"


@pytest.mark.parametrize(("content_role", "retired_shot", "expected"), [
    ("benefit", "close", "medium"),
    ("fit", "knee", "full"),
    ("coordination", "close", "full"),
    ("realWear", "knee", "full"),
])
def test_normalize_retired_worn_shots_to_role_default(content_role, retired_shot, expected):
    spec = cut.normalize_spec({"contentRole": content_role, "shot": retired_shot})
    assert spec["shot"] == expected


def test_normalize_product_fallbacks():
    spec = cut.normalize_spec({"cutType": "product", "direction": "side", "shot": "medium"})
    assert spec["direction"] == "front"       # product는 front/back만
    assert spec["shot"] == "ghost"            # 사람컷 샷은 product에 없음 → ghost
    assert spec["faceExposure"] is None


def test_normalize_product_keeps_detail_and_normalizes_retired_shots():
    detail = cut.normalize_spec({"cutType": "product", "shot": "detail"})
    retired = cut.normalize_spec({"cutType": "product", "shot": "hanger"})
    merged = cut.normalize_spec({"cutType": "product", "shot": "flatlay"})
    assert detail["shot"] == "detail"
    assert retired["shot"] == "ghost"
    assert merged["shot"] == "ghost"


def test_color_images_matches_numeric_and_string_color_ids():
    product = {"colors": [{
        "id": 7,
        "images": [{"slot": "Back", "id": "back-7"}, {"slot": "Front", "id": "front-7"}],
    }]}

    assert cut.color_images(product, "7") == [("Front", "front-7"), ("Back", "back-7")]


def test_color_images_selected_missing_or_empty_color_never_uses_base():
    product = {"colors": [
        {"id": "base", "isBase": True, "images": [{"slot": "Detail", "id": "base-detail"}]},
        {"id": "empty", "images": []},
    ]}

    assert cut.color_images(product, "missing") == []
    assert cut.color_images(product, "empty") == []


def test_color_images_without_color_id_uses_base_color():
    product = {"colors": [
        {"id": "other", "images": [{"slot": "Front", "id": "other-front"}]},
        {"id": "base", "isBase": True, "images": [{"slot": "Detail", "id": "base-detail"}]},
    ]}

    assert cut.color_images(product, None) == [("Detail", "base-detail")]


def test_detail_is_product_only():
    spec = cut.normalize_spec({"cutType": "styling", "shot": "detail"})
    assert spec["shot"] == "full"


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


def test_render_ref_scope_bg_uses_plate_and_blocks_pose_garment_transfer(dev_example_registry):
    template = cut.load_cut_template()
    spec = cut.normalize_spec({
        "cutType": "styling", "direction": "front", "shot": "full",
        "exampleId": "ex_styling_top_full_1", "refScope": "bg",
    })
    manifest = cut.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False, mood_count=0, example_scope="bg")
    p = cut.render_cut_prompt(template, spec, product={}, analysis={}, clothing_type="top",
                              image_manifest=manifest)
    assert "EXAMPLE REFERENCE (scope: bg)" in manifest and "THE scene canvas" in manifest
    # bg 플레이트는 첫 첨부·첫 라벨(프라이머시) — 워커의 insert(0)와 매니페스트 재번호가 짝
    assert manifest.splitlines()[0].startswith("1. EXAMPLE REFERENCE (scope: bg)")
    assert manifest.splitlines()[1].startswith("2. PRODUCT")
    # 2026-07-20 야간 실측: '생성하며 플레이트 참고'는 텍스트·순서 개선을 다 해도 ~40%에서 정체
    # (10회 판정) → '플레이트 편집' 과업으로 전환. 테스트는 편집 모드 의미를 고정한다.
    assert "EDIT TASK" in p                                   # 편집 과업 프레이밍
    assert "Insert ONE model wearing the PRODUCT garment" in p
    assert "the scene must stay the SAME" in p                # 인물 밖 장면 불변
    assert "street, cafe, cozy interior" not in p             # 경쟁 배경 나열이 아예 없음(섹션 교체)
    assert "lifestyle setting" not in p
    assert "choose a natural pose" in p                        # 포즈 유출 차단(플레이트는 포즈 미제어)
    assert "COMPLETE outfit" in p                              # 하의·신발 누락 방지(2026-07-20 실측: 맨다리 컷)
    assert "shoes or accessories" in p                         # 의류·신발 유출 차단(실험서 관찰된 실패)
    assert "FRAMING OVERRIDE" in p                             # 캔버스 크롭보다 요청 샷이 우선
    assert "Pose: natural and unforced" in p                   # 빈 배경은 포즈를 제어하지 않음
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


def test_unresolved_pose_and_bg_examples_fail_closed_without_fake_reference():
    template = cut.load_cut_template()
    base = {"cutType": "styling", "direction": "front", "exampleId": "ex_1"}
    kw = dict(product={}, analysis={}, clothing_type="top", image_manifest="(no images)")
    pose_prompt = cut.render_cut_prompt(template, cut.normalize_spec({**base, "refScope": "pose"}), **kw)
    bg_prompt = cut.render_cut_prompt(template, cut.normalize_spec({**base, "refScope": "bg"}), **kw)
    all_prompt = cut.render_cut_prompt(template, cut.normalize_spec(base), **kw)
    assert "REFERENCE SCOPE" not in pose_prompt
    assert "REFERENCE SCOPE" not in bg_prompt
    assert "Composition nuance" not in pose_prompt
    assert "Composition nuance" not in bg_prompt
    assert "Composition nuance" in all_prompt        # v0 공용 예시만 결정적 뉘앙스 유지


def test_example_asset_pose_scope_prefers_cutout_variant(dev_example_registry):
    # pose 스코프는 전용 누끼만 허용한다. 일반 사진으로 폴백하면 배경·옷이 유출된다.
    base = "https://assets.example.test/generated-examples"
    all_url = cut.resolve_example_asset("ex_styling_top_full_1", base, scope="all")
    pose_url = cut.resolve_example_asset("ex_styling_top_full_1", base, scope="pose")
    assert all_url and all_url.endswith("DEV+styling+example+1")
    assert pose_url and pose_url.endswith("DEV+styling+cutout+1")
    assert cut.resolve_example_asset("ex_horizon_top_full_1", base, scope="pose") is None


def test_example_asset_registry_v2_preserves_metadata_and_legacy_shapes(tmp_path, monkeypatch):
    registry = {
        "_meta": {"defaultBaseUrl": "https://images.example.test"},
        "assets": {
            "v2": {
                "all": "releases/r1/all/v2.png",
                "thumb": "releases/r1/thumb/v2.webp",
                "applicableClothingTypes": ["top", "outer"],
                "cutType": "styling", "shot": "full", "gender": "women",
                "direction": "side",
            },
            "product": {
                "all": "releases/r1/all/product.png",
                "thumb": "releases/r1/thumb/product.webp",
                "applicableClothingTypes": ["top"],
                "cutType": "product", "shot": "ghost", "gender": None,
                "direction": None,
            },
            "legacy-string": "legacy/all.png",
            "legacy-dict": {"all": "legacy/dict.png", "pose": "legacy/pose.png"},
        },
    }
    path = tmp_path / "example_assets.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(cut, "_DEFAULT_EXAMPLE_ASSETS", str(path))
    cut.load_example_asset_registry.cache_clear()
    try:
        base, assets = cut.load_example_asset_registry()
        assert base == "https://images.example.test"
        assert assets["v2"]["applicableClothingTypes"] == ["top", "outer"]
        assert assets["v2"]["thumb"].endswith("v2.webp")
        assert assets["v2"]["direction"] == "side"
        assert "direction" in assets["product"] and assets["product"]["direction"] is None
        assert "gender" in assets["product"] and assets["product"]["gender"] is None
        assert assets["legacy-string"] == {"all": "legacy/all.png"}
        assert assets["legacy-dict"] == {
            "all": "legacy/dict.png", "pose": "legacy/pose.png",
        }
        assert cut.example_asset_status("v2", "bottom", "all") == "not_applicable"
        assert cut.example_asset_status("v2", "top", "pose") == "variant_unpublished"
        assert cut.resolve_example_asset("v2", clothing_type="bottom") is None
        assert cut.resolve_example_asset("v2", clothing_type="top") == (
            "https://images.example.test/releases/r1/all/v2.png"
        )
        assert cut.example_asset_status("legacy-string", "dress", "all") == "available"
    finally:
        cut.load_example_asset_registry.cache_clear()


def test_pose_direction_preflight_matches_worn_and_mirror_rules(tmp_path, monkeypatch):
    registry = {
        "_meta": {"defaultBaseUrl": "https://images.example.test"},
        "assets": {
            "front": {"pose": "front.png", "cutType": "styling", "direction": "front"},
            "back": {"pose": "back.png", "cutType": "horizon", "direction": "back"},
            "mirror": {"pose": "mirror.png", "cutType": "mirror", "direction": "front"},
            "legacy": {"pose": "legacy.png"},
        },
    }
    path = tmp_path / "example_assets.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(cut, "_DEFAULT_EXAMPLE_ASSETS", str(path))
    cut.load_example_asset_registry.cache_clear()
    try:
        assert cut.pose_direction_compatible(
            "front", cut.normalize_spec({"cutType": "styling", "direction": "front"}))
        assert not cut.pose_direction_compatible(
            "back", cut.normalize_spec({"cutType": "styling", "direction": "front"}))
        assert cut.pose_direction_compatible(
            "mirror", cut.normalize_spec({"cutType": "mirror"}))
        assert not cut.pose_direction_compatible(
            "front", cut.normalize_spec({"cutType": "mirror"}))
        assert not cut.pose_direction_compatible(
            "mirror", cut.normalize_spec({"cutType": "horizon", "direction": "front"}))
        assert not cut.pose_direction_compatible(
            "legacy", cut.normalize_spec({"cutType": "styling", "direction": "front"}))
    finally:
        cut.load_example_asset_registry.cache_clear()


def test_example_asset_resolution_uses_registry_and_base_override(dev_example_registry):
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
    assert "source of background, lighting, mood, pose and framing/composition" in manifest
    assert "follow the attached EXAMPLE REFERENCE's background/location" in p
    assert "Swap in the exact garment from PRODUCT references" in p
    assert "garments, shoes, accessories" in p
    assert "PRODUCT and MATCHING are the ONLY clothing sources" in p
    assert "camera direction" in p and "remain\nfixed requirements" in p
    assert "Pose: natural and unforced" not in p


def test_resolved_example_manifest_and_prompt_apply_pose_only_scope():
    manifest = cut.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope="pose")
    p = _render(
        {"cutType": "styling", "shot": "full", "exampleId": "ex_styling_top_full_1",
         "refScope": "pose"},
        manifest=manifest,
    )
    assert "POSE CONTROL" in manifest
    assert "used ONLY as a kinematic control" in manifest
    assert "CUT SPEC controls" in manifest
    assert "POSE-ONLY RETARGETING CONTRACT" in p
    assert "screen-left versus screen-right limb placement" in p
    assert "Do not mirror the pose, swap left and right" in p
    assert "The requested direction is already compatible with this pose" in p
    assert "The current CUT SPEC alone controls camera distance" in p
    assert "canvas padding or subject size" in p
    assert "hidden lower-body landmarks do not control the crop" in p
    assert "Do not copy the mannequin's body shape" in p
    assert "without inventing that pocket" in p
    assert "one plain unbranded phone" in p
    assert "do not copy a case, logo, screen content" in p
    assert "adjust it naturally" not in p
    assert "render it from the requested" not in p
    assert "Do not transfer any background, lighting, color grade, clothing" in p
    assert "follow the attached EXAMPLE REFERENCE's background/location" not in p
    assert "Composition nuance" not in p
    assert "Pose: natural and unforced" not in p


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
    assert "POSE CONTROL" in manifest
    assert "SPACE CONTINUITY" in p
    assert "Do not transfer any background" in p
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
    assert "never copy its garments, shoes, accessories, person, model identity or pose" in manifest
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


def test_dummy_example_base_is_dev_only_without_override(dev_example_registry):
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
    p = _render({"cutType": "mirror", "shot": "medium"},
                product={"name": "골지 니트", "clothing_type": "top"},
                analysis={"materials": [{"name": "코튼", "ratio": 60}]})
    assert "MIRROR SELFIE" in p               # 거울샷 섹션
    assert "smartphone naturally covers" in p  # 얼굴 기본 = 폰으로 가림
    assert "Camera angle" not in p            # 방향 지시 없음
    assert "${" not in p                      # 미해결 토큰 없음
    assert "PRODUCT CONTEXT" in p             # ground-truth 블록 주입
    assert "head to the waist" in p           # medium × top 크롭


def test_render_bottom_medium_uses_lower_crop():
    p = _render({"cutType": "horizon", "shot": "medium", "direction": "front"}, clothing_type="bottom")
    assert "legs up to the waist" in p        # medium × bottom = 하체 중간샷
    assert "seamless studio backdrop" in p    # 호리존 섹션


def test_render_retired_product_flatlay_uses_ghost_presentation_contract():
    p = _render({"cutType": "product", "shot": "flatlay", "direction": "back"})
    assert "ghost-mannequin volume" in p
    assert "laid-flat, top-down presentation" in p
    assert "Show the back side" in p
    assert "Face handling" not in p           # 제품컷엔 얼굴 지시 없음


def test_render_product_detail_requires_loaded_detail_reference():
    with pytest.raises(ValueError, match="detail_reference_required"):
        _render({"cutType": "product", "shot": "detail"})


def test_render_product_detail_is_grounded_and_has_no_person_lines():
    manifest = f"1. {cut._SLOT_LABEL['Detail']}"
    p = _render({"cutType": "product", "shot": "detail"}, manifest=manifest)
    assert "tight product-only close-up" in p
    assert "never invent lining, hardware" in p
    assert "No model and no visible human body parts" in p
    assert "Face handling" not in p


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
    for spec in ({"cutType": "mirror"}, {"cutType": "product", "shot": "ghost"},
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
