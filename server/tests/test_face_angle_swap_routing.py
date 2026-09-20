"""옆·뒷모습 컷은 Qwen 얼굴 패스 대신 각도 교체(face_angle_swap)로 간다.

왜 분기가 필요한가: v7 LoRA 는 정면·3/4 만 배웠다. 옆모습은 yaw>0.65 라 건너뛰고(= 컷 실패),
뒷모습은 얼굴이 없어 시작도 못 한다. 대신 등록자 각도 실사진으로 머리만 바꾼다(2026-09-20 실측).
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.agents import cut_generator, face_angle_swap, face_identity


def settings(**overrides):
    base = dict(
        model_image_light="gemini-3-flash-image",
        model_image_high="gemini-3-pro-image",
        model_image_signature="gpt-image-2",
        model_text="gemini-3.7-flash",
        mannequin_image_size="2K",
        mannequin_aspect_ratio="2:3",
        face_identity_enabled=True,
        face_angle_swap_enabled=True,
        face_angle_backend_url="https://pod-8000.example/",
        face_angle_backend_token="t",
        face_angle_seed=42,
        fm_face_qc_dir=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeGemini:
    def __init__(self):
        self.prompts: list[str] = []

    async def generate_content_image(self, model, prompt, images, size, **kwargs):
        self.prompts.append(prompt)
        return SimpleNamespace(image=b"PROVIDER", mime="image/png")


IDENTITY = face_identity.FaceIdentitySpec("lora", token="ohwx man")
PRODUCT = {"name": "니트", "clothingType": "top", "colors": []}


def spec(direction="side", **overrides):
    out = {"cutType": "horizon", "shot": "full", "direction": direction, "faceExposure": "same",
           "modelId": "11111111-1111-1111-1111-111111111111", "pose": "auto"}
    out.update(overrides)
    return out


def angle_spec(photos=None):
    return face_angle_swap.AngleSwapSpec(
        photos=photos or face_angle_swap.AnglePhotos(side_nose_right=b"r", side_nose_left=b"l", back=b"b"),
        backend=object(), seed=42)


@pytest.mark.parametrize("direction,expected", [("side", "side"), ("back", "back"), ("front", None)])
def test_only_side_and_back_cuts_take_the_angle_path(direction, expected):
    assert cut_generator._angle_swap_direction(spec(direction), "top", angle_spec()) == expected


def test_no_angle_path_without_a_spec_or_for_product_cuts():
    assert cut_generator._angle_swap_direction(spec("side"), "top", None) is None
    assert cut_generator._angle_swap_direction(spec("back", cutType="product"), "top", angle_spec()) is None


def test_side_cut_runs_the_angle_swap_instead_of_the_face_pass(monkeypatch):
    seen: dict = {}

    async def fake_swap(image, mime, **kwargs):
        seen.update(kwargs, image=image)
        return b"SWAPPED", "image/png"

    async def fail_face_pass(*_a, **_k):
        raise AssertionError("옆모습 컷은 얼굴 패스로 가면 안 된다")

    monkeypatch.setattr(face_angle_swap, "swap", fake_swap)
    monkeypatch.setattr(face_identity, "apply_face_pass", fail_face_pass)
    gemini = FakeGemini()
    outcome: dict = {}
    image, mime = asyncio.run(cut_generator.generate(
        settings(), gemini, spec("side"), PRODUCT, [], face_identity_spec=IDENTITY,
        face_pass_outcome=outcome, angle_swap=angle_spec()))
    assert (image, mime) == (b"SWAPPED", "image/png")
    assert seen["direction"] == "side" and seen["image"] == b"PROVIDER" and seen["seed"] == 42
    # 옆모습을 3/4 로 바꾸는 지시(DIR:side_identity)는 이 경로에 들어가면 안 된다.
    assert "three-quarter view" not in gemini.prompts[0]
    assert "clear side profile" in gemini.prompts[0]


def test_back_cut_takes_the_angle_path_too(monkeypatch):
    seen: dict = {}

    async def fake_swap(image, mime, **kwargs):
        seen.update(kwargs)
        return b"SWAPPED", "image/png"

    monkeypatch.setattr(face_angle_swap, "swap", fake_swap)
    asyncio.run(cut_generator.generate(
        settings(), FakeGemini(), spec("back"), PRODUCT, [], face_identity_spec=IDENTITY,
        angle_swap=angle_spec()))
    assert seen["direction"] == "back"


def test_front_cut_still_uses_the_face_pass(monkeypatch):
    called: dict = {}

    async def fake_face_pass(settings_, image, mime, identity, **kwargs):
        called["identity"] = identity
        return b"FACE", "image/png"

    async def fail_swap(*_a, **_k):
        raise AssertionError("정면 컷은 각도 교체로 가면 안 된다")

    monkeypatch.setattr(face_identity, "apply_face_pass", fake_face_pass)
    monkeypatch.setattr(face_angle_swap, "swap", fail_swap)
    image, _mime = asyncio.run(cut_generator.generate(
        settings(), FakeGemini(), spec("front"), PRODUCT, [], face_identity_spec=IDENTITY,
        angle_swap=angle_spec()))
    assert image == b"FACE" and called["identity"] is IDENTITY


def test_side_cut_without_the_angle_path_keeps_the_three_quarter_order(monkeypatch):
    async def fake_face_pass(settings_, image, mime, identity, **kwargs):
        return b"FACE", "image/png"

    monkeypatch.setattr(face_identity, "apply_face_pass", fake_face_pass)
    gemini = FakeGemini()
    asyncio.run(cut_generator.generate(
        settings(face_angle_swap_enabled=False), gemini, spec("side"), PRODUCT, [],
        face_identity_spec=IDENTITY))
    assert "three-quarter view" in gemini.prompts[0]


def test_spec_from_needs_the_flag_and_a_pod_url():
    photos = face_angle_swap.AnglePhotos(back=b"b")
    assert face_angle_swap.spec_from(settings(face_angle_backend_url=None), photos) is None
    assert face_angle_swap.spec_from(settings(face_angle_swap_enabled=False), photos) is None
    built = face_angle_swap.spec_from(settings(), photos)
    assert built is not None and built.photos is photos and built.seed == 42
