import asyncio

from app.agents import image_qc as iq
from app.agents.gemini_image import InlineImage
from conftest import make_settings


def run(coro):
    return asyncio.run(coro)


def _img():
    return InlineImage("image/png", b"\x89PNG")


def test_config_image_qc_defaults_off():
    assert make_settings().image_qc == "off"


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
