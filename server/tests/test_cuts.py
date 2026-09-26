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

_SPACE_GROUP_ID = "ssg1__test-set__instance-1"


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
    assert block["sectionRole"] == "hooking"
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
        "fit", "studio", "horizon",
    )
    assert (fit["direction"], fit["shot"]) == ("side", "medium")
    assert retired["taxonomyVersion"] == 3
    assert "kind" not in retired

    stored = content_roles.canonicalize_storyboard_block({
        "kind": "hook", "cutType": "product", "shot": "detail",
    }, for_storage=True)
    assert stored["taxonomyVersion"] == 3
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
    assert mine["taxonomyVersion"] == 3
    assert mine["ownImages"] == ["asset-1"]
    assert custom["contentRole"] == "custom"
    assert custom["sectionRole"] == "hooking"
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


def test_canonical_storyboard_stably_orders_the_four_sections():
    normalized = content_roles.canonicalize_storyboard([
        {"id": "product-1", "source": "ai", "contentRole": "productOverview"},
        {"id": "fit-1", "source": "ai", "contentRole": "fit"},
        {"id": "benefit-1", "source": "ai", "contentRole": "hero"},
        {"id": "fit-2", "source": "ai", "contentRole": "coordination"},
        {"id": "custom", "source": "mine"},
        {"id": "benefit-2", "source": "ai", "contentRole": "benefit"},
    ])

    assert [block["id"] for block in normalized] == [
        "benefit-1", "benefit-2", "fit-2", "custom", "fit-1", "product-1",
    ]
    assert normalized[3]["sectionRole"] == "styling"  # 앞 이웃 섹션을 상속해 원래 위치를 지킨다.


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
    ) == ("hooking", "hero", "horizon")
    assert (
        normalized[1]["sectionRole"], normalized[1]["contentRole"], normalized[1]["cutType"]
    ) == ("product", "productOverview", "product")


def test_canonical_storyboard_studio_section_rejects_styling_recipe():
    normalized = content_roles.canonicalize_storyboard([{
        "id": "studio-with-styling-recipe", "source": "ai", "sectionRole": "studio",
        "contentRole": "coordination", "cutType": "styling", "direction": "side", "shot": "medium",
        "exampleId": "old-example", "thumb": "example.png", "baseThumb": "base.png",
    }])

    block = normalized[0]
    assert (block["sectionRole"], block["contentRole"], block["cutType"]) == (
        "studio", "fit", "horizon",
    )
    assert block["exampleId"] is None
    assert block["thumb"] == "base.png"


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
        "styling", "coordination", "styling",
    )
    assert (normalized["direction"], normalized["shot"]) == ("side", "medium")


def test_example_selection_origin_canonicalization_protects_legacy_and_clears_orphans():
    legacy = content_roles.canonicalize_storyboard_block({
        "source": "ai", "sectionRole": "fit", "contentRole": "fit",
        "cutType": "horizon", "direction": "front", "shot": "full",
        "exampleId": "legacy-id", "thumb": "example.png", "baseThumb": "base.png",
    })
    cleared = content_roles.canonicalize_storyboard_block({
        "source": "ai", "sectionRole": "fit", "contentRole": "fit",
        "cutType": "horizon", "direction": "front", "shot": "full",
        "exampleSelectionOrigin": "auto", "thumb": "example.png", "baseThumb": "base.png",
    })

    assert legacy["exampleId"] == "legacy-id"
    assert legacy["exampleSelectionOrigin"] == "user"
    assert cleared["exampleId"] is None
    assert cleared["exampleSelectionOrigin"] is None
    assert cleared["thumb"] == "base.png"
    assert cleared["baseThumb"] is None


def test_example_selection_origin_rejects_unknown_enum():
    with pytest.raises(ValueError, match="invalid_example_selection_origin"):
        content_roles.canonicalize_storyboard_block({
            "source": "ai", "sectionRole": "fit", "contentRole": "fit",
            "cutType": "horizon", "direction": "front", "shot": "full",
            "exampleId": "example-id", "exampleSelectionOrigin": "system",
        })
    with pytest.raises(ValueError, match="invalid_example_selection_origin"):
        content_roles.canonicalize_storyboard_block({
            "source": "ai", "contentRole": "fit", "exampleSelectionOrigin": "system",
        })


def test_server_example_validation_rejects_id_applicability_cut_and_gender_but_allows_shot_direction():
    assets = {
        "valid": {
            "all": "all.png", "cutType": "styling", "gender": "women",
            "shot": "full", "direction": "front", "applicableClothingTypes": ["top"],
        },
        "bottom": {
            "all": "all.png", "cutType": "styling", "gender": "women",
            "applicableClothingTypes": ["bottom"],
        },
        "horizon": {
            "all": "all.png", "cutType": "horizon", "gender": "women",
            "applicableClothingTypes": ["top"],
        },
        "men": {
            "all": "all.png", "cutType": "styling", "gender": "men",
            "applicableClothingTypes": ["top"],
        },
        "pose_front": {
            "all": "all.png", "pose": "pose.png", "cutType": "styling", "gender": "women",
            "shot": "full", "direction": "front", "applicableClothingTypes": ["top"],
        },
        "pose_back": {
            "all": "all.png", "pose": "pose.png", "cutType": "styling", "gender": "women",
            "shot": "full", "direction": "back", "applicableClothingTypes": ["top"],
        },
        "no_pose": {
            "all": "all.png", "cutType": "styling", "gender": "women",
            "shot": "full", "direction": "front", "applicableClothingTypes": ["top"],
        },
    }
    compatible = [{
        "exampleId": "valid", "cutType": "styling", "shot": "medium", "direction": "back",
    }]
    assert content_roles.validate_storyboard_example_references(
        compatible, assets=assets, clothing_type="top", gender="women"
    ) is None

    cases = [
        ("missing", "styling", "unknown_example_id"),
        ("bottom", "styling", "example_not_applicable"),
        ("horizon", "styling", "example_cut_mismatch"),
        ("men", "styling", "example_gender_mismatch"),
    ]
    for example_id, cut_type, expected in cases:
        error = content_roles.validate_storyboard_example_references(
            [{"exampleId": example_id, "cutType": cut_type}],
            assets=assets, clothing_type="top", gender="women",
        )
        assert error[0] == expected
        # 카탈로그 회전 복구용 — 클라이언트가 정확히 그 선택만 걷어낼 수 있게 meta에 실어 준다.
        assert error[2] == {"exampleId": example_id}


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


def test_virtual_model_loader_resolves_c_pack_and_excludes_product(tmp_path, monkeypatch, caplog):
    manifest_path = tmp_path / "virtual_models.json"
    manifest_path.write_text(json.dumps({
        "models": {
            "mA": {"views": {
                "face_front": {"key": "seed/mA/face.webp", "mime": "image/webp"},
                "grid_sedcard": {"key": "seed/mA/grid.png", "mime": "image/jpeg"},
                "body_front": {"key": "seed/mA/body.png", "mime": "image/jpeg"},
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
        assert cut.resolve_virtual_model_assets(person, require_full_body=True) == (
            {"key": "seed/mA/face.webp", "mime": "image/webp", "bucket": "public"},
            {"key": "seed/mA/body.png", "mime": "image/jpeg", "bucket": "public"},
        )
        product = cut.normalize_spec({"cutType": "product", "modelId": "mA"})
        assert cut.resolve_virtual_model_assets(product) is None
        unknown = cut.normalize_spec({"cutType": "mirror", "modelId": "missing"})
        assert cut.resolve_virtual_model_assets(unknown) is None
        assert "unknown virtual model missing" in caplog.text
    finally:
        cut.load_virtual_model_registry.cache_clear()


def test_normalize_space_variation_default_subtle():
    spec = cut.normalize_spec({
        "cutType": "horizon",
        "spaceGroupId": _SPACE_GROUP_ID,
        "spaceVariation": "weird",
    })
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
    assert "garments, shoes or props" in p                         # 의류·신발 유출 차단(실험서 관찰된 실패)
    assert "FRAMING OVERRIDE" in p                             # 캔버스 크롭보다 요청 샷이 우선
    assert "Pose: natural and unforced" in p                   # 빈 배경은 포즈를 제어하지 않음
    # bg 자산 = 빈 무대 플레이트(전용 variant) 우선
    base = "https://assets.example.test/generated-examples"
    assert cut.resolve_example_asset("ex_styling_top_full_1", base, scope="bg").endswith("plate+1")


def test_normalize_ref_scope_in_space_forces_pose():
    # 촬영 세트 + 예시 = '포즈 예시' 계약 — refScope 부재·'all' 입력도 서버가 강제
    implicit = cut.normalize_spec({
        "cutType": "styling",
        "spaceGroupId": _SPACE_GROUP_ID,
        "exampleId": "ex_1",
    })
    assert implicit["refScope"] == "pose"
    explicit = cut.normalize_spec({
        "cutType": "styling",
        "spaceGroupId": _SPACE_GROUP_ID,
        "exampleId": "ex_1",
        "refScope": "all",
    })
    assert explicit["refScope"] == "pose"
    # 예시가 없으면 강제 없음 — 배경 연속성([[SPACE]])만 작동
    no_example = cut.normalize_spec({
        "cutType": "styling",
        "spaceGroupId": _SPACE_GROUP_ID,
    })
    assert no_example["refScope"] == "all"


def test_render_named_pose_overrides_pose_scope_example():
    # 포즈 직접 지정 + '포즈만' 예시 = 지시 충돌 → 예시 라인 전체 미적용
    template = cut.load_cut_template()
    spec = cut.normalize_spec({
        "cutType": "styling", "direction": "front", "exampleId": "ex_1",
        "spaceGroupId": _SPACE_GROUP_ID,
        "pose": "walk",   # 촬영 세트 안이라 refScope는 'pose'로 강제됨
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
                "faceVisibility": "hidden",
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
        assert assets["v2"]["faceVisibility"] == "hidden"
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


def test_side_example_must_match_the_side_sub_kind_not_just_the_direction(
    tmp_path, monkeypatch
):
    """사선 카드가 90도 옆모습 사진을 "같은 방향"으로 물면 안 된다.

    둘 다 direction='side' 라 direction 만 보면 양립으로 판정되고, 프롬프트가 그 사진의
    body-direction family 를 보존하라고 지시한다. 그러면 베이스가 90도로 나오는데
    사선은 각도 교체를 건너뛰므로 얼굴 패스가 90도 머리에 얼굴을 그리게 된다.
    """
    registry = {
        "_meta": {"defaultBaseUrl": "https://images.example.test"},
        "assets": {
            "profile": {
                "all": "profile.png", "pose": "profile_pose.png",
                "cutType": "horizon", "direction": "side", "sideStyle": "profile",
            },
            "threeQuarter": {
                "all": "tq.png", "pose": "tq_pose.png",
                "cutType": "horizon", "direction": "side", "sideStyle": "threeQuarter",
            },
            # sideStyle 이 생기기 전에 발행된 자산 — 실제로는 전부 완전 옆모습이다.
            "legacySide": {
                "all": "legacy.png", "pose": "legacy_pose.png",
                "cutType": "horizon", "direction": "side",
            },
        },
    }
    path = tmp_path / "example_assets.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(cut, "_DEFAULT_EXAMPLE_ASSETS", str(path))
    cut.load_example_asset_registry.cache_clear()

    def compat(example_id, side_style):
        spec = cut.normalize_spec({
            "cutType": "horizon", "direction": "side", "sideStyle": side_style,
            "refScope": "all", "exampleId": example_id,
        })
        return cut.apply_reference_compatibility(spec)["_referenceDirectionCompatible"]

    try:
        assert compat("threeQuarter", "threeQuarter") is True
        assert compat("profile", "profile") is True
        # 핵심: 갈래가 다르면 양립이 아니다.
        assert compat("profile", "threeQuarter") is False
        assert compat("threeQuarter", "profile") is False
        # 미기재 레거시는 옆모습으로 읽는다.
        assert compat("legacySide", "profile") is True
        assert compat("legacySide", "threeQuarter") is False

        # pose 전용 자산의 사전 게이트도 같은 규칙을 쓴다.
        assert not cut.pose_direction_compatible("profile", cut.normalize_spec({
            "cutType": "horizon", "direction": "side", "sideStyle": "threeQuarter",
        }))
        assert cut.pose_direction_compatible("threeQuarter", cut.normalize_spec({
            "cutType": "horizon", "direction": "side", "sideStyle": "threeQuarter",
        }))
    finally:
        cut.load_example_asset_registry.cache_clear()


def test_non_side_directions_ignore_side_style(tmp_path, monkeypatch):
    """front·back 은 하위 갈래가 없다 — sideStyle 때문에 갈라지면 안 된다."""
    registry = {
        "_meta": {"defaultBaseUrl": "https://images.example.test"},
        "assets": {
            "front": {
                "all": "front.png", "cutType": "horizon",
                "direction": "front", "sideStyle": None,
            },
        },
    }
    path = tmp_path / "example_assets.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(cut, "_DEFAULT_EXAMPLE_ASSETS", str(path))
    cut.load_example_asset_registry.cache_clear()
    try:
        spec = cut.normalize_spec({
            "cutType": "horizon", "direction": "front",
            "refScope": "all", "exampleId": "front",
        })
        assert cut.apply_reference_compatibility(spec)["_referenceDirectionCompatible"] is True
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
    assert "EXAMPLE REFERENCE as art direction" in p
    assert "Swap in the exact garment from PRODUCT references" in p
    assert "garments, shoes, accessories" in p
    assert "PRODUCT and MATCHING are the ONLY product-specific garment identity" in p
    assert "camera direction" in p and "remain fixed requirements" in p
    assert "POSE FROM EXAMPLE: preserve the semantic backbone" in p
    assert "small natural changes in joint angles" in p
    assert "coherent different specific place" in p
    assert "do not use a structural/prop change quota" in p
    assert "Never add, move, duplicate or awkwardly stage" in p
    assert "environmental text or signage is not automatically erased" in p
    assert "at least two placements" not in p
    assert "Pose: natural and unforced" not in p


def test_horizon_all_scope_uses_neutral_studio_contract():
    manifest = cut.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope="all")
    p = _render(
        {"cutType": "horizon", "shot": "full", "exampleId": "ex_horizon_top_full_1",
         "refScope": "all"},
        manifest=manifest,
    )
    assert "REFERENCE SCOPE — HORIZON STUDIO" in p
    assert "Create a recognizably different specific place" not in p


def test_styling_all_scope_keeps_requested_framing_when_example_shot_differs():
    manifest = cut.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, example_scope="all")
    p = _render(
        {"cutType": "styling", "shot": "medium", "exampleId": "ex_styling_top_full_1",
         "refScope": "all"},
        manifest=manifest,
    )
    assert "current FRAMING wins" in p
    assert "no more, no less" not in p


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
    assert "EXAMPLE REFERENCE as art direction" not in p
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
        "spaceGroupId": _SPACE_GROUP_ID, "refScope": "all",
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
    assert "EXAMPLE REFERENCE as art direction" not in p


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
    assert "camera direction" in p and "remain fixed requirements" in p


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
    assert "head to the hip" in p           # medium × top 크롭


def test_render_bottom_medium_uses_lower_crop():
    p = _render({"cutType": "horizon", "shot": "medium", "direction": "front"}, clothing_type="bottom")
    assert "waist through the feet" in p        # medium × bottom = 하체 중간샷
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


# ---------- 디테일 컷 2모드 (2026-08-07 스펙 §5: 정밀 / 구조 확대) ----------


def test_detail_cut_back_uses_backdetail_label_gate():
    # 매니페스트에 BackDetail 라벨이 있으면 정밀 모드 문구가 실린다
    manifest = f"1. {cut._SLOT_LABEL['Back']}\n2. {cut._SLOT_LABEL['BackDetail']}"
    p = _render({"cutType": "product", "shot": "detail", "direction": "back"},
                manifest=manifest)
    assert "detail close-up reference" in p          # SHOT:detail 정밀 모드
    assert "structural element" not in p             # 구조 모드 아님
    assert "Show the back side" in p                 # DIR:back_product 방향 지시


def test_detail_cut_back_falls_to_zoom_mode_with_back_original_only():
    manifest = f"1. {cut._SLOT_LABEL['Front']}\n2. {cut._SLOT_LABEL['Back']}"
    p = _render({"cutType": "product", "shot": "detail", "direction": "back"},
                manifest=manifest)
    assert "structural element" in p                 # SHOT:detail_zoom
    assert "do NOT invent" in p


def test_detail_cut_back_fails_without_back_side_evidence():
    # 앞면 디테일만 있어도 뒷면 컷 근거가 아니다 — 스펙 §5 금지열
    manifest = f"1. {cut._SLOT_LABEL['Front']}\n2. {cut._SLOT_LABEL['Detail']}"
    with pytest.raises(ValueError, match="detail_reference_required"):
        _render({"cutType": "product", "shot": "detail", "direction": "back"},
                manifest=manifest)


def test_detail_cut_front_zoom_mode_with_front_original_only():
    manifest = f"1. {cut._SLOT_LABEL['Front']}"
    p = _render({"cutType": "product", "shot": "detail"}, manifest=manifest)
    assert "structural element" in p
    assert "fine fabric weave" in p                  # 저해상 확대 금지 지시


def test_detail_cut_zoom_mode_suppresses_color_transfer_line():
    # 타색 디테일 자산이 유실돼 zoom 으로 떨어졌으면, 존재하지 않는 첨부를 전제하는
    # 색전환 지시를 넣지 않는다 (2026-08-07 Codex 리뷰 P2).
    spec = cut.normalize_spec({"cutType": "product", "shot": "detail"})
    spec["_detailColorTransfer"] = {"targetName": "그린", "targetHex": None, "referenceName": "레드"}
    manifest = f"1. {cut._SLOT_LABEL['Front']}"
    p = cut.render_cut_prompt(cut.load_cut_template(), spec, {}, {}, "top", manifest)
    assert "structural element" in p                 # zoom 모드
    assert "DETAIL COLORWAY TRANSFER" not in p       # 전환 지시 억제
    # 정밀 모드(타색 디테일이 실제 첨부됨)에서는 그대로 나간다
    manifest_ok = f"1. {cut._SLOT_LABEL['Front']}\n2. {cut._SLOT_LABEL['Detail']}"
    p_ok = cut.render_cut_prompt(cut.load_cut_template(), spec, {}, {}, "top", manifest_ok)
    assert "DETAIL COLORWAY TRANSFER" in p_ok


# ---------- 슬롯 계약 (2026-08-07 개편: Fit 폐기 · BackDetail 신설) ----------


def test_slot_order_backdetail_last_no_fit():
    from app.agents.mannequin import _SLOT_ORDER
    assert _SLOT_ORDER == {"Front": 0, "Back": 1, "Detail": 2, "BackDetail": 3}


def test_slot_labels_have_backdetail_and_no_fit():
    from app.agents import feature_extractor
    from app.workers import mannequin_job
    for labels in (cut._SLOT_LABEL, feature_extractor._SLOT_LABEL,
                   mannequin_job._SLOT_LABEL):
        assert "BackDetail" in labels
        assert "Fit" not in labels
    # 뒷면 전용 못박기 — 스펙 §6 (앞면 배치 금지 문구)
    assert "never place it on the front" in cut._SLOT_LABEL["BackDetail"]


@pytest.mark.parametrize("state, phrase, inner_phrase", [
    ("open", "FULLY OPEN", "naturally visible through the open front"),
    ("partial", "PARTIALLY OPEN", "partially visible through the open portion"),
    ("closed", "FULLY CLOSED", "almost entirely hidden"),
])
def test_render_outer_closure_states_and_hardware_guard(state, phrase, inner_phrase):
    p = _render({
        "cutType": "styling", "shot": "full", "direction": "front",
        "outerClosureState": state, "exampleId": "ex_styling_outer_full_1",
    }, clothing_type="outer")
    assert phrase in p
    assert "overrides any different open/closed styling shown in EXAMPLE or MOOD images" in p
    assert "NEVER invent, remove, relocate or redesign closure hardware" in p
    assert "garment fidelity wins" in p
    assert "keep the inner T-shirt exactly the same as in the attached MANNEQUIN" in p
    assert inner_phrase in p
    assert p.index("Composition nuance") < p.index("OUTER FRONT OPENING")


def test_render_outer_closure_defaults_open_and_applies_to_mirror():
    p = _render({"cutType": "mirror", "shot": "full"}, clothing_type="outer")
    assert "FULLY OPEN" in p
    assert "keep the inner T-shirt exactly the same as in the attached MANNEQUIN" in p


def test_render_outer_closure_does_not_turn_side_or_back_to_show_front_hardware():
    for direction in ("side", "back"):
        p = _render({"cutType": "horizon", "direction": direction}, clothing_type="outer")
        assert "FULLY OPEN" in p
        assert "never change direction or framing to expose it" in p
    assert "clear side profile" in _render(
        {"cutType": "horizon", "direction": "side"}, clothing_type="outer")
    assert "from behind" in _render(
        {"cutType": "horizon", "direction": "back"}, clothing_type="outer")


@pytest.mark.parametrize("non_outer_type", ["top", "bottom", "dress"])
def test_render_outer_closure_absent_for_non_outer_and_product(non_outer_type):
    non_outer = _render(
        {"cutType": "horizon", "outerClosureState": "closed"}, clothing_type=non_outer_type)
    product = _render({"cutType": "product", "outerClosureState": "closed"}, clothing_type="outer")
    assert "OUTER FRONT OPENING" not in non_outer
    assert "OUTER FRONT OPENING" not in product
    assert "OUTERWEAR INNER" not in non_outer
    assert "OUTERWEAR INNER" not in product
    assert "inner T-shirt from the MANNEQUIN" not in non_outer
    assert "inner T-shirt from the MANNEQUIN" not in product


@pytest.mark.parametrize("category_key", ["clothingType", "clothing_type"])
def test_build_prompt_passes_product_category_to_outer_closure_normalization(category_key):
    p = cut.build_prompt(
        {"cutType": "horizon", "outerClosureState": "partial"},
        {category_key: "outer", "colors": []},
    )
    assert "PARTIALLY OPEN" in p


def test_render_space_group_line_only_when_grouped():
    grouped = _render({
        "cutType": "styling",
        "shot": "full",
        "spaceGroupId": _SPACE_GROUP_ID,
    })
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


# ── 핏 확인 섹션의 방향 배분 (2026-09-21) ──────────────────────────────────
#
# 2026-09-21 첫 운영 QA: studio 7컷이 **전부 정면**으로 나왔다(요각 -4.0~+0.6).
# 옆모습 0장, 뒷모습 1장. 옆·뒷모습 머리 교체를 다 배선해 놓고도 쓰이질 않았다.
# 원인은 _CONTENT_ROLE_RECIPES 의 일곱 역할이 전부 direction="front" 라, 콘티가
# 블록마다 명시하지 않으면 상세페이지가 통째로 정면이 되는 것이었다.


def _studio(n, **extra):
    return [{"sectionRole": "studio", "cutType": "horizon", **extra} for _ in range(n)]


def test_the_fit_section_gets_a_profile_a_three_quarter_and_a_back():
    """옆이 두 칸인 건 같은 "옆모습" 주문이 두 가지 다른 그림이기 때문이다.

    profile      = 진짜 옆모습(각도 교체가 실사진 머리를 붙인다)
    threeQuarter = 사선 3/4(얼굴 패스가 그린다) — 09-17 에 잘 나왔던 그 컷
    """
    out = content_roles.canonicalize_storyboard(_studio(6))
    plan = [(b["direction"], b.get("sideStyle")) for b in out]
    assert plan[0] == ("front", None), "기준 컷은 정면으로 남는다"
    assert plan[1:4] == [("side", "profile"), ("side", "threeQuarter"), ("back", None)]
    assert plan[4:] == [("front", None), ("front", None)]


def test_the_side_style_is_dropped_when_the_cut_is_not_a_side():
    """방향을 바꾼 카드에 옛 값이 따라다니면 안 된다."""
    out = content_roles.canonicalize_storyboard(
        [{"sectionRole": "studio", "cutType": "horizon",
          "direction": "front", "sideStyle": "threeQuarter"}])
    assert out[0]["sideStyle"] is None


def test_both_modules_agree_on_the_side_styles():
    """cut_generator 와 content_roles 가 갈리면 한쪽이 조용히 값을 버린다."""
    assert content_roles._SIDE_STYLES == cut.SIDE_STYLES


def test_a_direction_the_storyboard_chose_always_wins():
    """★ 콘티가 정한 값을 덮으면 연출 의도가 조용히 사라진다."""
    blocks = _studio(1) + [
        {"sectionRole": "studio", "cutType": "horizon", "direction": "front"},
        {"sectionRole": "studio", "cutType": "horizon", "direction": "front"},
        {"sectionRole": "studio", "cutType": "horizon", "direction": "back"},
    ]
    out = content_roles.canonicalize_storyboard(blocks)
    assert [b["direction"] for b in out] == ["front", "front", "front", "back"]


def test_a_short_fit_section_keeps_every_cut_frontal():
    """짧은 섹션을 옆·뒤로 채우면 정면이 사라진다. 기준 1장 + 배분 3장이 최소다."""
    for n in (1, 2, 3):
        out = content_roles.canonicalize_storyboard(_studio(n))
        assert {b["direction"] for b in out} == {"front"}, n


def test_seller_cards_are_never_reassigned():
    out = content_roles.canonicalize_storyboard(_studio(5, source="mine"))
    assert all(b.get("direction") in (None, "front") for b in out)


def test_changing_the_direction_drops_an_auto_picked_example():
    """정면 예시 포즈는 옆 컷에 못 쓴다(cut_generator.pose_direction_compatible)."""
    blocks = _studio(4)
    for b in blocks:
        b["exampleId"] = "ex_front_1"
        b["exampleSelectionOrigin"] = "auto"
    out = content_roles.canonicalize_storyboard(blocks)
    turned = [b for b in out if b["direction"] != "front"]
    assert len(turned) == 3
    assert all(b["exampleId"] is None for b in turned)
    assert out[0]["exampleId"] == "ex_front_1", "안 돌린 컷의 예시는 그대로 둔다"


def test_an_example_the_seller_picked_pins_the_direction():
    blocks = _studio(4)
    for b in blocks:
        b["exampleId"] = "ex_front_1"
        b["exampleSelectionOrigin"] = "user"
    out = content_roles.canonicalize_storyboard(blocks)
    assert {b["direction"] for b in out} == {"front"}
    assert all(b["exampleId"] == "ex_front_1" for b in out)


def test_the_caller_list_is_not_mutated():
    blocks = _studio(4)
    content_roles.canonicalize_storyboard(blocks)
    assert all("direction" not in b for b in blocks)


def test_other_sections_are_left_alone():
    blocks = [{"sectionRole": "styling", "cutType": "styling"} for _ in range(5)]
    out = content_roles.canonicalize_storyboard(blocks)
    assert {b["direction"] for b in out} == {"front"}


# ── 옆모습 두 갈래가 실제로 다른 경로로 가는가 ─────────────────────────────
def _side_spec(style):
    return cut.normalize_spec(
        {"cutType": "horizon", "direction": "side", "shot": "full",
         "faceExposure": "same", "sideStyle": style, "modelId": "m1"},
        clothing_type="top")


def test_a_profile_side_goes_to_the_angle_swap():
    spec = _side_spec("profile")
    assert spec["sideStyle"] == "profile"
    assert cut._angle_swap_direction(spec, "top", object()) == "side"


def test_a_three_quarter_side_skips_the_angle_swap():
    """★ 이게 사선을 되살리는 자리다. 각도 교체를 피해야 face_pass 가 참이 되고,
    그래야 프롬프트가 DIR:side_identity(3/4)로 간다."""
    spec = _side_spec("threeQuarter")
    assert spec["sideStyle"] == "threeQuarter"
    assert cut._angle_swap_direction(spec, "top", object()) is None


def test_the_side_style_is_ignored_on_a_front_cut():
    spec = cut.normalize_spec(
        {"cutType": "horizon", "direction": "front", "shot": "full",
         "faceExposure": "same", "sideStyle": "threeQuarter", "modelId": "m1"},
        clothing_type="top")
    assert spec["sideStyle"] is None


def test_an_unknown_side_style_falls_back_to_the_swap_path():
    """콘티가 오타를 주면 조용히 사선으로 새지 않는다 — 기본은 진짜 옆모습이다."""
    spec = _side_spec("diagonal")
    assert spec["sideStyle"] is None
    assert cut._angle_swap_direction(spec, "top", object()) == "side"


# ── 핏 확인 섹션 포즈 변주 (2026-09-21) ────────────────────────────────────
#
# 운영 QA 에서 studio 7컷이 거의 같은 그림으로 나왔다. 콘티가 pose 를 안 주면
# POSE:auto 한 줄로 가는데, 같은 상품·같은 모델·같은 프롬프트면 같은 포즈가 나온다.
def test_the_fit_section_varies_the_pose():
    out = content_roles.canonicalize_storyboard(_studio(6))
    poses = [b.get("pose") for b in out]
    assert not poses[0], "기준 컷은 POSE:auto 로 남는다"
    assert all(poses[1:]), "나머지는 포즈가 붙는다"
    assert len(set(poses[1:])) == len(poses[1:]), "서로 달라야 변주다"


def test_a_pose_the_storyboard_chose_always_wins():
    blocks = _studio(1) + [
        {"sectionRole": "studio", "cutType": "horizon", "pose": "leaning on a wall"},
        {"sectionRole": "studio", "cutType": "horizon"},
        {"sectionRole": "studio", "cutType": "horizon"},
    ]
    out = content_roles.canonicalize_storyboard(blocks)
    assert out[1]["pose"] == "leaning on a wall"


def test_the_frontend_auto_default_is_not_a_choice():
    """★ 프런트가 카드마다 pose:'auto' 를 써 넣는다(Storyboard.jsx). 그걸 선택으로 읽으면
    셀러가 콘티보드를 한 번 저장하는 순간 변주가 통째로 사라진다."""
    blocks = [{"sectionRole": "studio", "cutType": "horizon", "pose": "auto"} for _ in range(5)]
    out = content_roles.canonicalize_storyboard(blocks)
    poses = [b.get("pose") for b in out]
    assert poses[0] in (None, "", "auto"), "기준 컷은 그대로"
    assert all(p and p != "auto" for p in poses[1:]), poses
    assert len(set(poses[1:])) == len(poses[1:])


def test_horizon_set_members_keep_the_example_pose():
    """호리존 세트 컷은 완성 예시의 포즈를 따른다(ADR-0013). 돌려 넣은 포즈가 셀러의 명시 포즈처럼
    예시를 덮으면 2번째 컷부터 예시와 다른 자세가 나온다(2026-09-26 리뷰). 낱장 컷 변주는 그대로다."""
    grouped = _studio(4, pose="auto", spaceGroupId="ssg1__set__one", exampleId="ss_example")
    out = content_roles.canonicalize_storyboard(grouped)
    assert all(b.get("pose") in (None, "", "auto") for b in out), [b.get("pose") for b in out]
    loose = content_roles.canonicalize_storyboard(_studio(4, pose="auto"))
    assert all(b.get("pose") not in (None, "", "auto") for b in loose[1:])


_HZ_REGISTRY = {
    "hz_front_full": {"cutType": "horizon", "direction": "front", "shot": "full"},
    "hz_front_medium": {"cutType": "horizon", "direction": "front", "shot": "medium"},
}


@pytest.fixture
def hz_registry(monkeypatch):
    monkeypatch.setattr(cut, "load_example_asset_registry", lambda: (None, _HZ_REGISTRY))
    monkeypatch.setattr(cut.space_set_assets, "load_space_set_registry", lambda: (None, {
        "set1": {"members": [{"exampleId": "ss_front", "direction": "front"}]},
    }))


def _poses(blocks):
    return [b.get("pose") for b in content_roles.canonicalize_storyboard(blocks)]


def test_example_governed_horizon_cuts_keep_the_example_pose(hz_registry):
    """완성 예시가 포즈를 정하는 낱장 호리존 컷에는 자동 포즈를 넣지 않는다(2026-09-26 오너 결정).
    넣으면 워커가 셀러의 명시 포즈로 읽어 예시 포즈를 덮고, 컬러웨이 변주도 꺼진다."""
    blocks = [
        {"sectionRole": "studio", "cutType": "horizon", "direction": "front", "pose": "auto"},
        {"sectionRole": "studio", "cutType": "horizon", "direction": "front", "pose": "auto",
         "exampleId": "hz_front_full", "refScope": "all"},
        {"sectionRole": "studio", "cutType": "horizon", "direction": "front", "pose": "auto",
         "exampleId": "hz_front_medium", "refScope": "pose"},
        {"sectionRole": "studio", "cutType": "horizon", "direction": "front", "pose": "auto"},
        {"sectionRole": "studio", "cutType": "horizon", "direction": "front", "pose": "auto"},
    ]
    poses = _poses(blocks)
    assert poses[:3] == ["auto", "auto", "auto"], poses
    assert poses[3:] == list(content_roles._STUDIO_POSE_ROTATION[:2])


@pytest.mark.parametrize("direction", ["side", "back"])
def test_a_front_example_on_a_side_or_back_cut_still_rotates(hz_registry, direction):
    """방향이 안 맞는 예시는 포즈 권한이 없다. 이런 컷은 계속 자동 포즈로 변주한다."""
    blocks = [
        {"sectionRole": "studio", "cutType": "horizon", "direction": "front", "pose": "auto"},
        {"sectionRole": "studio", "cutType": "horizon", "direction": direction, "pose": "auto",
         "exampleId": "hz_front_full", "refScope": "all"},
    ]
    assert _poses(blocks)[1] == content_roles._STUDIO_POSE_ROTATION[0]


def test_a_saved_auto_pose_on_a_governed_cut_resets_but_a_chosen_pose_stays(hz_registry):
    """옛 서버가 저장한 자동 포즈는 셀러 선택이 아니라 auto 로 되돌린다. 셀러가 고른 포즈는 이긴다."""
    governed = {"sectionRole": "studio", "cutType": "horizon", "direction": "front",
                "exampleId": "hz_front_full", "refScope": "all"}
    blocks = [
        {**governed, "pose": content_roles._STUDIO_POSE_ROTATION[2]},
        {**governed, "pose": "leaning on a wall"},
    ]
    assert _poses(blocks) == ["auto", "leaning on a wall"]


def test_standalone_set_member_with_another_direction_still_rotates(hz_registry, monkeypatch):
    base = {"sectionRole": "studio", "cutType": "horizon", "pose": "auto", "exampleId": "ss_front"}
    blocks = [
        {"sectionRole": "studio", "cutType": "horizon", "direction": "front", "pose": "auto"},
        {**base, "direction": "front"},
        {**base, "direction": "back"},
    ]
    assert _poses(blocks) == ["auto", "auto", content_roles._STUDIO_POSE_ROTATION[0]]

    def broken():
        raise OSError("registry unavailable")
    monkeypatch.setattr(cut.space_set_assets, "load_space_set_registry", broken)
    assert not cut.horizon_example_governs_pose({**base, "direction": "front"})


def test_default_colorway_pairs_reach_the_worker_as_repeat_indexes(hz_registry):
    """확장 구성의 추가 색상 쌍(프런트 defaultStoryboard)이 저장·작업 정규화를 거쳐도 예시 포즈가
    남아, 워커가 색상 순서대로 0, 1, 2 변주를 준다. 앞의 세트 컷은 대상이 아니다."""
    from app.workers import detail_page_job as dpj

    def card(color, shot, **extra):
        return {"source": "ai", "sectionId": "studio-1", "sectionRole": "studio",
                "contentRole": "fit", "cutType": "horizon", "direction": "front", "shot": shot,
                "colorId": color, "pose": "auto", "refScope": "all",
                "exampleId": f"hz_front_{shot}", "exampleSelectionOrigin": "auto", **extra}

    blocks = [card("base", "full", spaceGroupId="ssg1__set__one", exampleId="ss_front")]
    for color in ("c2", "c3", "c4"):
        pair = {"colorwayGroupId": f"colorway__{color}", "sectionLayout": "twoColumn",
                "layoutRowId": f"row__colorway__{color}"}
        blocks += [card(color, "full", **pair), card(color, "medium", **pair)]

    out = content_roles.canonicalize_storyboard(blocks)
    assert all(b.get("pose") == "auto" for b in out[1:]), [b.get("pose") for b in out]
    assert dpj._example_repeat_indexes(out, "top") == [None, 0, 0, 1, 1, 2, 2]


def test_seller_cards_keep_their_pose():
    out = content_roles.canonicalize_storyboard(_studio(5, source="mine"))
    assert all(not b.get("pose") for b in out)


def test_the_poses_survive_normalize_and_reach_the_prompt():
    """★ 실제로 POSE:named 로 가는지 — 여기서 끊기면 변주가 조용히 사라진다."""
    out = content_roles.canonicalize_storyboard(_studio(4))
    turned = out[1]
    spec = cut.normalize_spec({**turned, "modelId": "m1"}, clothing_type="top")
    assert spec["pose"] == turned["pose"] != "auto"


def test_every_pose_keeps_the_garment_visible_and_fits_the_limit():
    """핏 확인 섹션이라 앞섶을 가리는 포즈는 안 쓴다. 문구는 40자에서 잘린다."""
    for pose in content_roles._STUDIO_POSE_ROTATION:
        assert len(pose) <= 40, pose
        assert "arms crossed" not in pose and "팔짱" not in pose, pose
