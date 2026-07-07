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
