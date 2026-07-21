import asyncio

from app.agents import image_qc as iq
from app.config import Settings, load_settings
from app.agents.gemini_image import InlineImage
from conftest import make_settings


def run(coro):
    return asyncio.run(coro)


def _img():
    return InlineImage("image/png", b"\x89PNG")


def test_config_image_qc_defaults_off():
    assert make_settings().image_qc == "off"

def test_config_garment_qc_defaults_and_env(monkeypatch):
    assert Settings.__dataclass_fields__["garment_qc_mode"].default == "bestof"
    assert Settings.__dataclass_fields__["garment_qc_extra_candidates"].default == 2

    monkeypatch.setenv("GARMENT_QC_MODE", "shadow")
    monkeypatch.setenv("GARMENT_QC_EXTRA_CANDIDATES", "4")
    settings = load_settings()
    assert settings.garment_qc_mode == "shadow"
    assert settings.garment_qc_extra_candidates == 4

    monkeypatch.setenv("GARMENT_QC_MODE", "invalid")
    assert load_settings().garment_qc_mode == "bestof"



def test_qc_schema_shape():
    s = iq.qc_schema()
    assert s["properties"]["verdict"]["enum"] == ["pass", "retry"]
    assert set(s["required"]) == {"verdict", "mismatches", "correctionPrompt"}


def test_build_prompt_injects_count():
    p = iq.build_prompt(3)
    assert "FIRST 3 image" in p and "${productCount}" not in p


def test_validate_pass_clears_fields():
    out = iq.validate({"verdict": "pass", "mismatches": ["x"], "correctionPrompt": "y"})
    assert out == {"verdict": "pass", "mismatches": [], "correctionPrompt": None}


def test_validate_retry_keeps_mismatches():
    out = iq.validate({"verdict": "retry", "mismatches": ["넥라인 다름", "  "],
                       "correctionPrompt": "라운드넥 유지"})
    assert out["verdict"] == "retry"
    assert out["mismatches"] == ["넥라인 다름"]
    assert out["correctionPrompt"] == "라운드넥 유지"


def test_validate_out_of_enum_defaults_pass():
    assert iq.validate({"verdict": "maybe"})["verdict"] == "pass"


def test_verdict_orchestrates(monkeypatch):
    async def fake_fallback(settings, prompt, images, schema):
        assert len(images) == 3            # 상품 2 + 생성 1
        assert images[-1].data == b"GEN"   # 마지막이 생성 이미지
        return ({"verdict": "retry", "mismatches": ["색 다름"], "correctionPrompt": "블랙 유지"}, "gemini")
    monkeypatch.setattr(iq, "analyze_with_fallback", fake_fallback)
    gen = InlineImage("image/png", b"GEN")
    out = run(iq.verdict(make_settings(gemini_api_key="x"), [_img(), _img()], gen))
    assert out["verdict"] == "retry" and out["mismatches"] == ["색 다름"]


def test_pick_schema_and_validate_are_bounded():
    schema = iq.pick_schema(3)
    assert schema["properties"]["chosenIndex"]["maximum"] == 2
    assert iq.validate_pick({"chosenIndex": 2, "reason": " logo "}, 3) == {
        "chosenIndex": 2, "reason": "logo",
    }
    assert iq.validate_pick({"chosenIndex": 3, "reason": "bad"}, 3)["chosenIndex"] == 0
    assert iq.validate_pick({"chosenIndex": True, "reason": "bad"}, 3)["chosenIndex"] == 0


def test_pick_best_orchestrates_product_then_candidates(monkeypatch):
    product = InlineImage("image/png", b"PRODUCT")
    candidates = [
        InlineImage("image/png", b"C0"),
        InlineImage("image/jpeg", b"C1"),
    ]

    async def fake_fallback(settings, prompt, images, schema):
        assert [image.data for image in images] == [b"PRODUCT", b"C0", b"C1"]
        assert "FIRST 1 image" in prompt
        assert "2 image(s) are generated candidates" in prompt
        assert "${productCount}" not in prompt and "${candidateCount}" not in prompt
        assert schema["properties"]["chosenIndex"]["maximum"] == 1
        return {"chosenIndex": 1, "reason": "logo is closest"}, "gemini"

    monkeypatch.setattr(iq, "analyze_with_fallback", fake_fallback)
    out = run(iq.pick_best(
        make_settings(gemini_api_key="x"),
        [product],
        candidates,
    ))
    assert out == {"chosenIndex": 1, "reason": "logo is closest"}
