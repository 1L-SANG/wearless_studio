"""실존 모델(LoRA) 컷은 **옆모습을 주문하지 않는다** — 3/4 로 바꿔 주문한다.

얼굴 패스는 yaw > YAW_APPLY_MAX(0.65) 인 그림을 건너뛴다. 그리고 건너뛴 컷은 이제 원본으로
나가지 않고 실패한다(face_identity.SKIP_REASONS_UNAVAILABLE). 즉 옆모습을 주문하면 빈 컷이 된다.

근거(2026-09-14 실험, 파드 brsz1la99s8vgs): 스튜디오 옆모습 전신을 만들어 yaw 상한을 억지로 올려
태웠더니 입력 yaw 1.114 → 결과 yaw 1.622, 게이트 identity_low, SFace **0.413** (정면 컷은 0.72~0.79).
v7 학습셋에 옆모습이 없어서 모델이 등록자가 아닌 얼굴을 그린다. 그래서 고칠 자리는 합성이 아니라
**주문서**다 — 몸은 옆으로 두고 얼굴만 3/4(yaw 0.25~0.45 구간)로 돌린다.
"""

import pytest

from app.agents import cut_generator as cg
from app.agents import face_identity as fi
from conftest import make_settings

SPEC = fi.FaceIdentitySpec("facemarket/loras/m/v1.safetensors", "ohwx man",
                           backend_url="https://pod-8000.proxy.runpod.net/render")


def _spec(direction: str) -> dict:
    return {"cutType": "horizon", "shot": "full", "direction": direction,
            "modelId": "11111111-1111-4111-8111-111111111111"}


def _prompt(direction: str, *, face_pass: bool) -> str:
    return cg.build_prompt(_spec(direction), {"clothing_type": "top", "name": "t"},
                           face_pass=face_pass)


def test_the_template_has_a_three_quarter_section():
    sections = cg._sections(cg.load_cut_template())
    assert "DIR:side_identity" in sections
    text = sections["DIR:side_identity"]
    assert "three-quarter" in text and "both eyes" in text.lower()
    assert "Not a full side profile" in text


def test_a_licensed_face_cut_never_orders_a_side_profile():
    ordered = _prompt("side", face_pass=True)
    assert "three-quarter" in ordered
    assert "a clear side profile of the model." not in ordered


def test_without_the_face_pass_the_side_cut_is_unchanged():
    """가상 모델·LoRA 없는 실존 모델은 그대로 — 이 PR 은 그 경로를 건드리지 않는다."""
    assert "a clear side profile of the model." in _prompt("side", face_pass=False)


@pytest.mark.parametrize("direction", ["front", "back"])
def test_other_directions_are_untouched(direction):
    assert _prompt(direction, face_pass=True) == _prompt(direction, face_pass=False)


def test_the_switch_follows_the_same_rule_as_the_face_pass():
    """주문서와 얼굴 패스가 **같은 판정**을 써야 한다 — 갈리면 옆모습을 주문해놓고 그 컷을 실패시킨다."""
    settings = make_settings(gemini_api_key="x", r2_bucket="b", face_identity_enabled=True)
    spec = cg.normalize_spec(_spec("side"), clothing_type="top")
    assert cg._face_identity_spec(settings, spec, "top", SPEC) is not None
    # LoRA 근거가 없으면 얼굴 패스도 없고 주문서도 옛날 그대로다
    assert cg._face_identity_spec(settings, spec, "top", None) is None
    off = make_settings(gemini_api_key="x", r2_bucket="b", face_identity_enabled=False)
    assert cg._face_identity_spec(off, spec, "top", SPEC) is None
