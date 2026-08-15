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


def test_scored_prompt_lists_fit_change_as_critical():
    """핏 변화는 **치명오류 어휘**에 있어야 실효가 있다.

    2026-07-31 인과 측정: fidelity 설명에 핏을 넣은 것만으로는 점수가 노이즈 범위 안에서만
    움직였다(n=5, 개별 [-5, 2, -3, 30, -25] — 같은 판정기가 같은 이미지에 ±30 을 낸다).
    반면 `garment fit changed` 를 치명오류 목록에 넣자 2/5 에서 새로 발화했다.

    치명오류가 실효 경로인 이유: `score_outcome` 이 점수와 무관하게 regenerate 로 보내고,
    `edit_regressed` 가 "없던 치명오류"를 되돌리기 조건으로 쓴다. 즉 이 한 줄이 옷을 조이는
    편집을 자동으로 폐기시킨다.
    """
    p = iq.build_prompt(2, scored=True)
    assert "garment fit changed" in p
    assert "order the wrong size" in p, "왜 출고 불가인지(사이즈 오주문)가 근거로 있어야 한다"


def test_scored_prompt_lists_tuck_as_critical():
    """상의를 하의에 넣어버리는 건 치명오류다 — 생성 프롬프트만으로는 안 잡혔다.

    `mannequin_generate_v1.txt` 는 이미 "COMPLETELY OUTSIDE / never tuck / no French tuck" 까지
    명문화했고 `1d70338` 로 부분 tuck 금지도 넣었는데, 2026-07-31 prod 컷(job 3c6dd251)은
    셔츠를 청바지에 넣은 채 나왔고 QC 는 `mismatches: []`·`critical_errors: []` 로 **통과**시켰다.
    검출이 없으면 재생성 트리거도 없다 — 그래서 판정기 어휘에 올린다.

    치명오류로 두는 이유는 핏 변화와 같다: `score_outcome` 이 점수와 무관하게 regenerate 로
    보내는 유일한 경로다. 대신 오탐이 무한 재생성을 부르지 않도록 선언 의도(DECLARED FIT)에
    tuck 요청이 있으면 예외라는 문장을 함께 둔다.
    """
    p = iq.build_prompt(2, scored=True)
    assert "top tucked into the bottom" in p, "치명오류 어휘에 있어야 재생성이 걸린다"
    assert "French tuck" in p, "부분 tuck 도 결함임을 명시해야 한다"
    assert "declared\nfit instruction" in p or "declared fit instruction" in p, \
        "선언된 의도는 예외로 빠져야 무한 재생성을 막는다"
    # 주입 블록 마커(대문자 DECLARED FIT)는 fit_profile 이 있을 때만 나와야 한다 —
    # 기본 프롬프트가 그 토큰을 쓰면 build_declared_fit_block 유무를 구분할 수 없다.
    assert "DECLARED FIT" not in p


def test_scored_prompt_lists_pattern_scale_as_critical():
    """미세 줄무늬가 굵은 띠로 단순화되는 건 "다른 원단"이다 — 치명오류 어휘에 있어야 잡힌다.

    2026-08-01 실측(prod da1b8101, 남성 스트라이프 셔츠): 원본은 흰 바탕에 하늘색+베이지 얇은
    줄이 페어로 앞판 폭에 40~50줄인데, 생성본은 **하늘색 바탕에 굵은 베이지 줄 12~15개**로
    나왔다. 바탕색이 뒤집히고 줄 간격이 3~4배가 됐는데도 QC 는 fid=82 로 auto_pass 했다.
    DETAIL 클로즈업이 이미 첨부된 상태였다 — 정보 부족이 아니라 고주파 패턴을 저주파로
    단순화하는 생성 모델의 실패 모드다. 무지 상품이 잘 나오는 이유도 같다(재현할 고주파가 없다).

    핏 변화 때와 같은 이유로 치명오류에 둔다: 점수 설명 문장은 판정기 노이즈(±30) 안에서만
    움직였고, critical_errors 어휘에 올렸을 때만 실제로 발화했다.
    """
    p = iq.build_prompt(2, scored=True)
    assert "pattern scale changed" in p, "치명오류 어휘에 있어야 재생성이 걸린다"
    assert "figure-ground" in p, "바탕/줄 반전(흰 바탕 → 색 바탕)도 결함으로 봐야 한다"
    assert "repeats across the garment" in p, "판정 기준이 '반복 개수'로 관측 가능해야 한다"
    # 주입 블록 마커(대문자 DECLARED FIT)는 fit_profile 이 있을 때만 나와야 한다 —
    # 기본 프롬프트가 그 토큰을 쓰면 build_declared_fit_block 유무를 구분할 수 없다.
    assert "DECLARED FIT" not in p


_FIT_PROFILE = {"category": "top", "gender": "women", "source": "seller", "version": 2,
                "axes": {"fit": "slim", "length": "crop"}}


def test_declared_fit_moves_the_judgment_from_photos_to_intent():
    """셀러가 조정한 핏은 결함이 아니다 — QC 가 그걸 알아야 한다.

    2026-07-31 실측: `fit: slim`·`length: crop` 으로 선언된 오버사이즈 티에서, 생성은 선언대로
    슬림·크롭으로 냈는데 QC 가 **상품 사진**과 비교해 매 시도마다 `garment fit changed from
    oversized to tight crop` 을 붙였다. 치명오류는 점수와 무관하게 regenerate 이므로 예산이
    소진될 때까지 재생성하다 구제 출고(regenerate)로 끝났다 — 핏 조정 기능을 쓰는 상품마다.
    """
    p = iq.build_prompt(2, scored=True, fit_profile=_FIT_PROFILE)
    assert "DECLARED FIT" in p
    assert "NOT the fit seen in the product photos" in p
    assert "must not be reported as a mismatch or as a critical error" in p
    # 선언 축이 관측 문구로 풀려야 판정기가 무엇을 볼지 안다
    assert "follows chest and waist closely" in p

    # 선언이 없으면 블록 자체가 없다 — 멀쩡한 상품에 "핏이 달라도 된다"를 흘리면 안 된다.
    assert "DECLARED FIT" not in iq.build_prompt(2, scored=True)
    assert "DECLARED FIT" not in iq.build_prompt(2, scored=True, fit_profile={})
    # scored 격리 — scene/best_of 가 쓰는 기본 경로는 바이트 단위로 불변이어야 한다.
    assert "DECLARED FIT" not in iq.build_prompt(2, fit_profile=_FIT_PROFILE)


def test_declared_fit_ignores_unknown_axes():
    """카탈로그에 없는 축·값은 조용히 스킵 — 판정기에 빈 지시가 가면 안 된다."""
    p = iq.build_prompt(2, scored=True,
                        fit_profile={"category": "top", "axes": {"fit": "made_up_value"}})
    assert "DECLARED FIT" not in p


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



# ---------- 매칭 하의(코디 바지) 정체성 — 같은 1콜 안 ----------

def test_matching_schema_adds_fields_only_on_scored_and_matching():
    """matching 필드는 scored 위에, matching=True 일 때만. 그 밖 경로는 불변."""
    s = iq.qc_schema(scored=True, matching=True)
    assert s["properties"]["matching_fidelity"] == {"type": ["integer", "null"]}
    assert s["properties"]["matching_critical_errors"]["type"] == "array"
    # GPT strict: 전 키가 required 여야 400 이 안 난다.
    assert set(s["required"]) == set(s["properties"])
    # scored 인데 matching 아니면 필드가 없어야 한다(매칭 하의 없는 마네킹 컷).
    scored_only = iq.qc_schema(scored=True)
    assert "matching_fidelity" not in scored_only["properties"]
    assert "matching_critical_errors" not in scored_only["properties"]
    # matching 만 켜도 scored 아니면 아무 것도 안 붙는다(scene/best_of 보호).
    assert set(iq.qc_schema(matching=True)["properties"]) == {
        "verdict", "mismatches", "correctionPrompt"}


def test_matching_fidelity_is_not_a_product_score_key():
    """matching_fidelity 는 SCORE_KEYS 밖 — score_outcome/등급에 절대 안 섞인다(오탐·비용 방지)."""
    assert "matching_fidelity" not in iq.SCORE_KEYS
    assert iq.MATCHING_KEYS == ("matching_fidelity",)


def test_matching_prompt_block_gated_on_matching():
    """MATCHING BOTTOM 블록·하드 게이트 어휘는 matching 일 때만. scored-only 엔 안 샌다."""
    p = iq.build_prompt(2, scored=True, matching=True)
    assert "MATCHING BOTTOM" in p
    for token in ("matching bottom colour changed", "matching bottom type changed",
                  "matching bottom leg width changed", "matching bottom structure changed"):
        assert token in p, token
    # 주름·미세광택은 점수만, critical 아님이 명시돼야 한다(요구 3).
    assert "NEVER critical errors" in p
    # 격리: matching 없는 scored 프롬프트엔 이 블록이 없어야 한다.
    assert "MATCHING BOTTOM" not in iq.build_prompt(2, scored=True)
    # 미채점 경로(scene/best_of)는 더더욱 불변.
    assert "MATCHING BOTTOM" not in iq.build_prompt(2)


def test_validate_matching_clamps_and_keeps_hard_errors():
    out = iq.validate({
        "verdict": "pass",
        "product_fidelity": 90, "physical_naturalness": 88,
        "image_quality": 84, "series_consistency": None, "critical_errors": [],
        "matching_fidelity": 130, "matching_critical_errors": ["matching bottom colour changed", " "],
    }, scored=True, matching=True)
    assert out["matching_fidelity"] == 100                       # 클램핑
    assert out["matching_critical_errors"] == ["matching bottom colour changed"]
    # pass 판정이어도 바지 하드 게이트는 살린다(주상품 critical_errors 와 같은 규율).
    assert out["verdict"] == "pass"


def test_validate_matching_absent_when_not_matching():
    """매칭 없는 scored 응답엔 matching 키가 없어야 한다 — 주상품 판정과 분리."""
    out = iq.validate({"verdict": "pass", "product_fidelity": 90,
                       "matching_fidelity": 50}, scored=True)
    assert "matching_fidelity" not in out
    assert "matching_critical_errors" not in out


def test_verdict_inserts_match_between_products_and_generated(monkeypatch):
    """첨부 순서 계약: [상품…, 매칭 하의, 생성]. 스키마·validate 도 matching 경로여야 한다."""
    captured = {}

    async def fake_fallback(settings, prompt, images, schema):
        captured["images"] = [i.data for i in images]
        captured["schema"] = schema
        assert "MATCHING BOTTOM" in prompt
        return ({"verdict": "pass", "product_fidelity": 92, "physical_naturalness": 90,
                 "image_quality": 88, "series_consistency": None, "critical_errors": [],
                 "matching_fidelity": 84,
                 "matching_critical_errors": ["matching bottom leg width changed"]}, "gemini")

    monkeypatch.setattr(iq, "analyze_with_fallback", fake_fallback)
    gen = InlineImage("image/png", b"GEN")
    match = InlineImage("image/png", b"MATCH")
    out = run(iq.verdict(make_settings(gemini_api_key="x"), [_img(), _img()], gen,
                         scored=True, match_image=match))
    assert captured["images"] == [b"\x89PNG", b"\x89PNG", b"MATCH", b"GEN"]
    assert "matching_fidelity" in captured["schema"]["properties"]
    assert out["matching_fidelity"] == 84
    assert out["matching_critical_errors"] == ["matching bottom leg width changed"]
    assert out["product_fidelity"] == 92


def test_verdict_without_match_is_byte_identical_to_baseline(monkeypatch):
    """match_image 없으면 요청(이미지 수·프롬프트)·응답 shape 이 기존과 완전히 같다."""
    captured = {}

    async def fake_fallback(settings, prompt, images, schema):
        captured["n"] = len(images)
        captured["matching_in_prompt"] = "MATCHING BOTTOM" in prompt
        captured["matching_in_schema"] = "matching_fidelity" in schema["properties"]
        return ({"verdict": "pass", "product_fidelity": 90, "physical_naturalness": 90,
                 "image_quality": 90, "series_consistency": None, "critical_errors": []}, "gemini")

    monkeypatch.setattr(iq, "analyze_with_fallback", fake_fallback)
    out = run(iq.verdict(make_settings(gemini_api_key="x"), [_img(), _img()],
                         InlineImage("image/png", b"GEN"), scored=True))
    assert captured["n"] == 3 and not captured["matching_in_prompt"]
    assert not captured["matching_in_schema"]
    assert "matching_fidelity" not in out and "matching_critical_errors" not in out


def test_every_catalog_axis_value_has_an_observable():
    """선언 축 커버리지에 구멍이 생기면 **핏 조정 버그가 조용히 재발한다**.

    `build_declared_fit_block` 은 관측 문구가 없는 축·값을 조용히 스킵한다(빈 지시가 판정기로
    가면 안 되므로 그게 맞다). 그런데 카탈로그에 새 값을 추가하면서 `AXIS_OBSERVABLES` 를
    안 채우면, 그 값을 선언한 셀러의 상품만 QC 에 의도가 전달되지 않아 다시
    `garment fit changed` 로 무한 재생성에 걸린다 — 특정 옵션에서만 나는 버그라 찾기 어렵다.
    """
    from app.agents.fit_axes import AXIS_OBSERVABLES, FIT_AXES

    missing = sorted({
        (cat, axis, e["value"])
        for cat, axes in FIT_AXES.items()
        for axis, by_gender in axes.items()
        for entries in by_gender.values()
        for e in entries
        if (cat, axis, e["value"]) not in AXIS_OBSERVABLES
    })
    assert not missing, f"관측 문구 없는 축값: {missing}"
