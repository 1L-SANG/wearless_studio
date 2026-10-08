"""옷 영역의 지어낸 곡선 잔금(crackle) 후처리. 2026-10-02.

OpenAI gpt-image 계열(gpt-image-2·2.5 flare·sunburst)은 전신·미디엄 착용컷의 원단에 실제
직물과 무관한 곡선 그물 무늬를 그린다(빈티지 워싱 데님·멜란지 티에서 가장 심함). 같은
예시컷으로 만든 Gemini 컷은 깨끗했고, quality=high 는 오히려 더 선명하게 그렸다. 그래서
생성 뒤에 옷 영역의 **약한 진폭 중간 대역**만 깎는다. 글자·솔기·단추처럼 대비가 큰 것은
남는다. 생성 호출을 더 하지 않는 결정적 처리다.
"""

import asyncio
import io
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image

from app.agents import cut_generator, garment_desquiggle as gd


def _band_energy(gray: np.ndarray, lo: float = 1.2, hi: float = 5.0) -> float:
    g = gray.astype(np.float32)
    return float((cv2.GaussianBlur(g, (0, 0), lo) - cv2.GaussianBlur(g, (0, 0), hi)).std())


def _squiggle_field(h: int, w: int, amp: float, seed: int = 0) -> np.ndarray:
    """방향 없는 곡선 잔금 근사. 매끈한 노이즈의 등고선(선 굵기 2~4px 곡선 그물).

    amp=12 에서 중간 대역 표준편차가 약 1.7. 운영 컷 잔금(휘도 1~4)과 같은 크기다."""
    rng = np.random.default_rng(seed)
    n = cv2.GaussianBlur(rng.standard_normal((h, w)).astype(np.float32), (0, 0), 6.0)
    n /= n.std() + 1e-6
    lines = np.exp(-(np.sin(n * 5.0) ** 2) * 3.0)  # 등고선 근처만 밝은 곡선
    return (lines - lines.mean()) * amp


def _png(bgr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", bgr)
    assert ok
    return buf.tobytes()


def test_low_amplitude_squiggle_inside_mask_is_removed_and_outside_untouched():
    h, w = 400, 400
    base = np.full((h, w), 120, np.float32)
    noisy = base + _squiggle_field(h, w, amp=12.0)
    bgr = cv2.cvtColor(np.clip(noisy, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    mask = np.zeros((h, w), np.float32)
    mask[100:300, 100:300] = 1.0

    out = gd.desquiggle(bgr, mask)

    before = _band_energy(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)[130:270, 130:270])
    after = _band_energy(cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)[130:270, 130:270])
    assert after < before * 0.6  # 실측 약 0.46. 잔금 대역이 절반 넘게 줄어든다
    # 마스크에서 충분히 먼 곳은 바이트 단위로 그대로다(배경·피부는 건드리지 않는다).
    assert np.array_equal(out[:40, :40], bgr[:40, :40])


def test_high_contrast_strokes_like_logo_text_survive():
    h, w = 300, 300
    gray = np.full((h, w), 170, np.uint8)
    cv2.putText(gray, "logo", (40, 170), cv2.FONT_HERSHEY_SIMPLEX, 2.4, 40, 5)
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    mask = np.ones((h, w), np.float32)

    out = gd.desquiggle(bgr, mask)

    stroke = gray < 100
    diff = np.abs(cv2.cvtColor(out, cv2.COLOR_BGR2GRAY).astype(int) - gray.astype(int))
    assert diff[stroke].mean() < 6  # 글자 획은 거의 그대로


def _settings(**overrides):
    base = dict(garment_desquiggle_mode="on")
    base.update(overrides)
    return SimpleNamespace(**base)


def _denim_product():
    return {"name": "데님 셔츠", "clothingType": "top", "colors": []}


def test_should_apply_gates():
    spec = {"cutType": "styling"}
    analysis = {"sellingPoints": ["빈티지 틴 워싱", "무지", "매끈한 표면"],
                "materials": [{"name": "면", "ratio": 100}]}
    protection = gd.build_protection_info(_denim_product(), analysis, [])
    ok = gd.should_apply(_settings(), "gpt-image-2.5-flare", spec, protection)
    assert ok == (True, "eligible")

    assert gd.should_apply(_settings(garment_desquiggle_mode="off"), "gpt-image-2.5-flare",
                           spec, protection) == (False, "off")
    assert gd.should_apply(SimpleNamespace(), "gpt-image-2.5-flare",
                           spec, protection) == (False, "off")
    assert gd.should_apply(_settings(), "gemini-3-pro-image",
                           spec, protection) == (False, "model")
    assert gd.should_apply(_settings(), "gpt-image-2.5-flare", {"cutType": "product"},
                           protection) == (False, "cut_type")
    striped = {"name": "멀티 스트라이프 셔츠", "clothingType": "top"}
    assert gd.should_apply(_settings(), "gpt-image-2.5-flare", spec,
                           gd.build_protection_info(striped, None, [])) == (False, "pattern")
    corduroy = {"name": "코듀로이 셔츠 자켓", "clothingType": "outer"}
    assert gd.should_apply(_settings(), "gpt-image-2.5-flare", spec,
                           gd.build_protection_info(corduroy, None, [])) == (False, "texture")
    knit = {"name": "케이블 니트", "clothingType": "top"}
    assert gd.should_apply(_settings(), "gpt-image-2.5-flare", spec,
                           gd.build_protection_info(knit, None, [])) == (False, "texture")


def _person_like_image(w: int = 512, h: int = 768) -> np.ndarray:
    """벽돌색 배경 + 가운데 데님색 몸통(잔금 포함) + 위쪽 피부색 얼굴. 마스크 추정 시험용."""
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (60, 70, 150)  # BGR 벽돌색
    torso = np.zeros((h, w), bool)
    torso[int(h * 0.25):int(h * 0.95), int(w * 0.3):int(w * 0.7)] = True
    sq = _squiggle_field(h, w, amp=12.0, seed=3)
    for c, v in enumerate((120, 105, 80)):  # 데님 청회색(BGR)
        ch = img[..., c].astype(np.float32)
        ch[torso] = v + sq[torso]
        img[..., c] = np.clip(ch, 0, 255).astype(np.uint8)
    cv2.ellipse(img, (w // 2, int(h * 0.15)), (int(w * 0.07), int(h * 0.07)), 0, 0, 360,
                (120, 150, 200), -1)  # 피부색 얼굴
    return img


def test_apply_returns_same_size_png_and_reports_outcome():
    bgr = _person_like_image()
    out, mime, outcome = gd.apply(_png(bgr), "image/png")
    assert mime == "image/png"
    assert outcome["status"] == "applied"
    decoded = np.array(Image.open(io.BytesIO(out)).convert("RGB"))
    assert decoded.shape[:2] == bgr.shape[:2]
    h, w = bgr.shape[:2]
    torso = (slice(int(h * 0.45), int(h * 0.8)), slice(int(w * 0.38), int(w * 0.62)))
    before = _band_energy(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)[torso])
    after = _band_energy(cv2.cvtColor(cv2.cvtColor(decoded, cv2.COLOR_RGB2BGR),
                                      cv2.COLOR_BGR2GRAY)[torso])
    assert after < before * 0.6


def test_apply_keeps_original_bytes_when_image_is_undecodable_or_maskless():
    raw = b"not an image"
    assert gd.apply(raw, "image/png")[:2] == (raw, "image/png")
    flat = np.full((256, 256, 3), 200, np.uint8)  # 사람·옷이 없는 평면. 마스크 없음
    data = _png(flat)
    out, mime, outcome = gd.apply(data, "image/png")
    assert out == data and outcome["status"].startswith("skipped")


class _FakeGemini:
    def __init__(self, image: bytes):
        self.image = image

    async def generate_content_image(self, model, prompt, images, image_size, **kwargs):
        return SimpleNamespace(image=self.image, mime="image/png")


def _gen_settings(**overrides):
    base = dict(
        model_image_light="gemini-3-flash-image",
        model_image_high="gpt-image-2.5-flare",
        model_image_signature="gpt-image-2",
        model_text="gemini-3.7-flash",
        mannequin_image_size="2K",
        detail_cut_image_size="2K",
        mannequin_aspect_ratio="2:3",
        openai_api_key="sk-test",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_generate_runs_desquiggle_only_when_flag_is_on(monkeypatch):
    calls = []

    def fake_apply(image, mime, **kwargs):
        calls.append(image)
        return b"CLEAN", mime, {"status": "applied"}

    monkeypatch.setattr(gd, "apply", fake_apply)
    spec = {"cutType": "styling", "shot": "full", "direction": "front"}
    gemini = _FakeGemini(b"RAW")

    off = asyncio.run(cut_generator.generate(_gen_settings(), gemini, spec, _denim_product(), []))
    assert off[0] == b"RAW" and calls == []

    on = asyncio.run(cut_generator.generate(
        _gen_settings(garment_desquiggle_mode="on"), gemini, spec, _denim_product(), [],
        garment_protection=gd.build_protection_info(
            _denim_product(), {"sellingPoints": ["무지", "매끈한 표면"]}, [])))
    assert on[0] == b"CLEAN" and calls == [b"RAW"]


def test_base_checkpoint_keeps_the_raw_provider_base(monkeypatch):
    """체크포인트는 후처리 **전** 원본이다(BaseCheckpoint 계약). 필터는 매번 다시 건다."""
    monkeypatch.setattr(gd, "apply", lambda image, mime, **kw: (b"CLEAN", mime, {"status": "applied"}))
    kept = []

    class Ckpt(cut_generator.BaseCheckpoint):
        async def keep(self, image, mime):
            kept.append(image)
            self.image = (image, mime)

    spec = {"cutType": "styling", "shot": "full", "direction": "front"}
    out = asyncio.run(cut_generator.generate(
        _gen_settings(garment_desquiggle_mode="on"), _FakeGemini(b"RAW"), spec, _denim_product(), [],
        base_checkpoint=Ckpt(), garment_protection=gd.build_protection_info(
            _denim_product(), {"sellingPoints": ["무지", "매끈한 표면"]}, [])))
    assert kept == [b"RAW"] and out[0] == b"CLEAN"
