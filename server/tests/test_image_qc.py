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


def test_qc_schema_default_stays_three_fields():
    """기본 스키마는 3필드 고정 — scene_verdict·best_of 가 공유하기 때문.

    공간세트 scene QC 는 fail-closed(PR#62)라 스키마 오류가 경고 강등이 아니라 셀러 컷
    전멸로 이어진다. 점수 확장이 기본값으로 새면 그 경로가 같이 흔들린다.
    """
    assert set(iq.qc_schema()["properties"]) == {"verdict", "mismatches", "correctionPrompt"}
    assert "${" not in iq.build_prompt(2)
    assert "SCORING" not in iq.build_prompt(2)


def test_qc_schema_scored_adds_axes_and_keeps_strict_contract():
    s = iq.qc_schema(scored=True)
    for key in iq.SCORE_KEYS:
        assert s["properties"][key] == {"type": ["integer", "null"]}
    assert s["properties"]["critical_errors"]["type"] == "array"
    # GPT strict: properties 전 키가 required 여야 400 이 안 난다.
    assert set(s["required"]) == set(s["properties"])
    assert "SCORING" in iq.build_prompt(2, scored=True)


def test_validate_scored_clamps_and_preserves():
    out = iq.validate({
        "verdict": "retry", "mismatches": ["로고 뭉개짐"], "correctionPrompt": "고쳐",
        "product_fidelity": 140, "physical_naturalness": -5,
        "image_quality": 87.6, "series_consistency": None,
        "critical_errors": ["logo altered", "  "],
    }, scored=True)
    assert out["product_fidelity"] == 100   # 상한 클램핑
    assert out["physical_naturalness"] == 0  # 하한 클램핑
    assert out["image_quality"] == 87        # 정수화
    assert out["series_consistency"] is None  # Phase 3 이 채운다
    assert out["critical_errors"] == ["logo altered"]


def test_validate_scored_unparseable_is_none_not_zero():
    """판독 불가는 신호 없음(None)이지 최악(0)이 아니다 — 0 이면 멀쩡한 컷이 재생성된다."""
    out = iq.validate({"verdict": "pass", "product_fidelity": "high",
                       "physical_naturalness": True}, scored=True)
    assert out["product_fidelity"] is None
    assert out["physical_naturalness"] is None  # bool 은 int 지만 점수가 아니다


def test_validate_scored_keeps_critical_errors_on_pass():
    """pass 판정이어도 치명 오류는 살린다 — 'pass 인데 로고가 바뀐' 케이스를 놓치지 않으려고."""
    out = iq.validate({"verdict": "pass", "critical_errors": ["garment color changed"]},
                      scored=True)
    assert out["verdict"] == "pass"
    assert out["mismatches"] == []
    assert out["critical_errors"] == ["garment color changed"]


def test_validate_default_shape_unchanged_for_shared_callers():
    """기본 경로 반환 키는 3개 그대로 — scene/best_of 소비처가 키 추가를 전제하지 않는다."""
    out = iq.validate({"verdict": "retry", "mismatches": ["x"], "correctionPrompt": "y",
                       "product_fidelity": 50})
    assert set(out) == {"verdict", "mismatches", "correctionPrompt"}


def test_build_prompt_injects_count():
    p = iq.build_prompt(3)
    assert "FIRST 3 image" in p and "${productCount}" not in p


def test_build_prompt_has_mirrored_source_precedence():
    """거울 셀카 원본에서 '정방향 교정 = 일치' 규칙이 프롬프트에 있어야 한다.

    2026-07-30 A/B 실측: 셀러 원본이 거울 셀카라 로고·숫자가 반전돼 있었는데, QC 가
    '원본과 일치'만 보고 **반전 보존본에 pass** 를 줬고 정방향 교정본에는 retry 를 줬다
    (correctionPrompt 가 "exact mirrored numbers '201' 을 유지하라"). 판정기가 교정을
    처벌하는 구조였다. 이 규칙이 빠지면 그 회귀다.
    """
    p = iq.build_prompt(2)
    assert "MIRRORED SOURCE PHOTOS" in p
    # 기존 letter-order 규칙과의 우선순위가 명시돼야 모순 판정이 안 난다.
    assert "takes precedence" in p
    # 판정 기준이 사진의 광학이 아니라 의류의 실제 디자인임을 못박았는가.
    assert "TRUE DESIGN" in p
    # 반전 보존을 mismatch 로 판정하라는 지시가 있는가.
    assert "REPRODUCES the reversal" in p


def test_scored_prompt_makes_fidelity_cover_fit():
    """`product_fidelity` 는 핏도 봐야 한다 — 실측으로 확인된 구멍.

    2026-07-31: 오버사이즈 티가 몸에 붙는 미니원피스로 바뀐 컷의 fidelity 가 83→78 이었다.
    실루엣이 통째로 바뀌었는데 5점이다. 채점 설명에 색·프린트·길이는 있어도 **핏이 없었기**
    때문이다. 쇼퍼는 프린트만큼이나 핏으로 상품을 설명한다.

    이 문장이 빠지면 가슴 2패스·축 편집이 옷을 조여도 QC 가 통과시킨다.
    """
    p = iq.build_prompt(2, scored=True)
    assert "product_fidelity" in p
    for token in ("ease between garment and body", "where the hem falls", "oversized tee"):
        assert token in p, token
    # 미채점 경로에는 새 문장이 새어 들어가면 안 된다(scored 격리).
    assert "ease between garment and body" not in iq.build_prompt(2)


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
