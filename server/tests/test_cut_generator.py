"""AG-06 cut_generator — build_prompt 배관 테스트 (계약 이식 후, 2026-07-07).

스펙 정규화·섹션 렌더의 세부 계약은 test_cuts.py 가 담당한다. 여기는 워커가 쓰는
진입점(build_prompt/generate 경로)의 회귀만 지킨다: 매니페스트 토큰 유출 금지(architect
DEFECT 1), 빈 이미지 폴백 문구, 미상 cutType 은 조용한 styling 폴백이 아니라 ValueError,
mirror 가 정식 컷으로 렌더되는지.
"""

import pytest

from app.agents import cut_generator as cg


def test_cut_types_constant_includes_mirror():
    assert cg.CUT_TYPES == ("styling", "horizon", "product", "mirror")


def test_build_prompt_substitutes_image_manifest():
    # ${imageManifest} 리터럴 토큰이 모델로 유출되면 안 됨 (architect DEFECT 1 회귀 방지)
    product = {"name": "니트", "colors": [{"isBase": True, "images": [
        {"slot": "Front", "id": "a1"}, {"slot": "Back", "id": "a2"}]}]}
    p = cg.build_prompt({"cutType": "styling", "direction": "front", "shot": "full"}, product)
    assert "${imageManifest}" not in p
    assert "front view of the garment" in p and "back view of the garment" in p


def test_build_prompt_manifest_fallback_no_images():
    p = cg.build_prompt({"cutType": "product"}, {"name": "니트", "colors": []})
    assert "${imageManifest}" not in p
    assert "product photos" in p.lower()


def test_build_prompt_unknown_cut_type_raises():
    # 회귀 방지: 미상 cutType(예: 폐기 토큰 'daily')을 styling 으로 조용히 대체 렌더하지 않는다 —
    # 병렬 백엔드 머지에서 mirror 가 styling 으로 무음 폴백되던 사고의 재발 금지.
    with pytest.raises(ValueError):
        cg.build_prompt({"cutType": "daily"}, {"name": "니트"})


def test_build_prompt_mirror_is_first_class():
    p = cg.build_prompt({"cutType": "mirror", "shot": "knee"}, {"name": "골지 니트", "clothing_type": "top"})
    assert "MIRROR SELFIE" in p               # 거울샷 전용 섹션으로 렌더
    assert "${" not in p and "[[" not in p    # 토큰·섹션 마커 유출 없음
    assert "PRODUCT CONTEXT" in p and "골지 니트" in p


def test_build_prompt_respects_given_manifest():
    # 워커가 첨부 순서(마네킹→상품→매칭→무드)에 맞춰 만든 매니페스트를 그대로 쓴다
    product = {"name": "니트", "colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}
    manifest = cg.build_manifest([{"slot": "Front"}], has_mannequin=True, has_match=True, mood_count=1)
    p = cg.build_prompt({"cutType": "styling"}, product, manifest=manifest)
    assert "worn on a mannequin" in p and "MATCH" in p and "MOOD" in p


def test_build_prompt_injects_fit_profile_and_drops_legacy_fit():
    # 확정 fitProfile(마네킹 단계 산출물)을 텍스트 제약으로 이중 전달 — 마네킹 참조와 원본
    # 사진 인상이 충돌할 때 순종률 확보(컷 파이프라인 계약). 프로필 있으면 레거시 '- Fit:' 생략.
    product = {"name": "니트", "clothing_type": "top",
               "colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}
    analysis = {"fit": "regular", "fitProfile": {
        "category": "top", "gender": "women",
        "axes": {"fit": "over", "length": None},
    }}
    p = cg.build_prompt({"cutType": "styling", "direction": "front", "shot": "full"},
                        product, analysis=analysis)
    assert "FIT PROFILE (seller-declared" in p
    assert "- fit: oversized volume" in p
    assert p.index("FIT PROFILE") < p.index("PRODUCT CONTEXT")
    assert "- Fit: regular" not in p


def test_build_prompt_match_cut_requires_bottom_on_screen():
    # matchCut 은 매칭 하의가 화면에 있을 때만(마네킹 참조 or MATCH 첨부) — 없는 옷 지시로
    # 하의를 지어내지 않게 제거(마네킹 워커 effective_fit_profile 과 동일 가드).
    product = {"name": "니트", "clothing_type": "top",
               "colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}
    analysis = {"fitProfile": {
        "category": "top", "gender": "women",
        "axes": {"fit": "regular", "length": None}, "matchCut": "wide",
    }}
    spec = {"cutType": "styling", "direction": "front", "shot": "full"}

    with_mannequin = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=True, has_match=False, mood_count=0)
    p1 = cg.build_prompt(spec, product, analysis=analysis, manifest=with_mannequin)
    assert "- matching bottom" in p1

    with_match = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=True, mood_count=0)
    p2 = cg.build_prompt(spec, product, analysis=analysis, manifest=with_match)
    assert "- matching bottom" in p2

    neither = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False, mood_count=0)
    p3 = cg.build_prompt(spec, product, analysis=analysis, manifest=neither)
    assert "- matching bottom" not in p3
    assert "- fit:" in p3   # 나머지 축은 유지
