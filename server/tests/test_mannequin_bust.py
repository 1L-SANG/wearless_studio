"""여성 기본 가슴 볼륨 2패스 — 순수 함수 + 워커 fail-open 회귀.

가장 중요한 건 fail-open 이다. 2패스 프롬프트("마네킹 가슴을 바꿔라")는 콘텐츠 필터를
건드릴 수 있고, 실제로 Flash 는 2회 중 1회를 "I cannot modify the physical characteristics
of the mannequin's chest" 로 거부했다(이미지 없이 텍스트 → GeminiError). 필터 한 번에
셀러 잡이 죽고 크레딧 분쟁이 나면 안 된다.
"""

import asyncio
from types import SimpleNamespace

from app.agents import mannequin_bust
from app.agents.gemini_image import GeminiError
from app.agents.prompts import load_bust_prompt_template
from app.workers import mannequin_job
from tests.conftest import make_settings

_ORIG = SimpleNamespace(image=b"pass1-bytes", mime="image/jpeg")
_EDITED = SimpleNamespace(image=b"pass2-bytes", mime="image/jpeg")


# ---------- 순수 함수 ----------

def test_should_apply_only_for_women_with_flag_on():
    assert mannequin_bust.should_apply("women", "on") is True
    assert mannequin_bust.should_apply("women", "off") is False
    # 남성은 현행 경로와 완전히 동일해야 한다 — 플래그가 켜져도 2패스 없음.
    assert mannequin_bust.should_apply("men", "on") is False
    assert mannequin_bust.should_apply("men", "off") is False


def test_skips_when_garment_does_not_cover_the_chest():
    """하의 컷에는 가슴을 덮는 옷이 없다 — 2패스의 전제가 성립하지 않는다.

    프롬프트가 "마네킹이 옷을 입고 있으니 **천이 가슴 크기를 보여주는 유일한 수단**"이라고
    말하는데, 진·스커트 컷은 상체가 맨몸이다. 2026-07-31 실 워커 출고본에서 하의 컷에도
    2패스가 돌아 이미지모델 호출을 쓰고(1건은 등급을 떨어뜨려 되돌려짐) 상품과 무관한
    맨상체만 키우는 것이 확인됐다.
    """
    assert mannequin_bust.should_apply("women", "on", "bottom") is False
    for covered in ("top", "outer", "dress", "TOP", "Outer"):
        assert mannequin_bust.should_apply("women", "on", covered) is True, covered
    # 카테고리를 모르면 적용한다 — 상의가 대다수라 모를 때 거르는 쪽이 더 자주 틀린다.
    assert mannequin_bust.should_apply("women", "on", None) is True
    # 기존 조건은 그대로 우선한다.
    assert mannequin_bust.should_apply("men", "on", "top") is False
    assert mannequin_bust.should_apply("women", "off", "top") is False


def test_worker_passes_clothing_type_to_bust_gate(monkeypatch):
    """워커가 카테고리를 실제로 넘겨야 게이트가 동작한다 — 안 넘기면 조용히 전건 적용된다."""
    import types

    import test_mannequin_axis_qc as harness
    from app.workers import mannequin_job

    seen = {}

    def fake_should_apply(gender, mode, clothing_type=None):
        seen["clothing_type"] = clothing_type
        return False

    monkeypatch.setattr(mannequin_job.mannequin_bust, "should_apply", fake_should_apply)

    async def fake_series(app, pool, s, job_id, project_id, candidate, attempt, res, anchor_img=None):
        return None

    monkeypatch.setattr(mannequin_job, "_apply_series_qc", fake_series)
    harness._run(monkeypatch, mode="off", guard=True, max_attempts=1, verdicts=[],
                 image_qc="off", mannequin_bust_pass="on")
    assert seen.get("clothing_type") == "top", seen


def test_build_prompt_substitutes_target_and_keeps_calibrated_wording():
    """v3(핏 인식본)의 계약. **v1 의 최대치 압박 문구는 의도적으로 빠졌다.**

    v1 은 "과소 변화는 FAILURE", "확신 없으면 go FURTHER", "허리를 눈에 띄게 SLIMMER" 로
    최대한 밀어붙이게 했다. 짝 비교 n=10 실측: 그 문구들이 오버사이즈 티를 몸에 붙는
    미니원피스로 바꿔 **핏 깨짐 7/10**, 옷을 조여서 이긴 편익 7건을 만들었다. v3 는 0/10.
    그래서 여기서 잠그는 것은 "얼마나 세게 미느냐"가 아니라 **무엇을 지키느냐**다.
    """
    prompt = mannequin_bust.build_prompt(load_bust_prompt_template())
    assert "${" not in prompt
    # 실측 캘리브레이션으로 고른 크기 — 이 배수가 결과 크기를 결정한다.
    # 1.5 → 1.3 (2026-08-01): 1.5 는 전신이 "뚱뚱하게" 읽혀 상품 인상을 깎았다. 같은 베이스 컷에
    # 배수만 바꾼 그리드(1.5 ×1 vs 1.3 ×4)에서 셀러가 1.3 을 골랐다.
    assert "a full C CUP" in prompt
    assert "1.3 times" in prompt
    # 변화가 보이게 만드는 메커니즘(2026-07-30 스파이크) — 이건 유지한다.
    assert "TENTS over the bust apex" in prompt
    assert "falls AWAY from the stomach" in prompt
    # 핏 보존이 크기보다 위다. 이 우선순위가 v3 의 핵심이고, 빠지면 v1 회귀다.
    assert "WHAT OUTRANKS WHAT" in prompt
    assert "The garment's FIT is untouchable" in prompt
    assert "A drawn-in waist on a loose garment is WRONG" in prompt
    assert "GIVE UP THE VOLUME" in prompt
    # 프린트·레터링 보존 — v1 에서 'text or logo altered' 가 실제로 발생했다.
    assert "letter for letter" in prompt
    # 전신 비대화·골반 확대 방지 — 1차 스파이크 실패 모드의 직접 가드(유지).
    assert "HIP WIDTH MUST NOT INCREASE" in prompt
    assert "NOT read as heavier" in prompt
    # 프레이밍 — v2 가 한 건에서 전신 프레임을 잘랐다. v3 에서 하드 룰로 승격.
    assert "The FRAMING is unchanged" in prompt
    # 앞섬 잠금 — 2패스가 가슴 공간을 만드느라 단추를 풀어버린다(1패스 4/4 잠김 vs 2패스 풀림).
    assert "stays done up" in prompt
    assert "Do NOT open, unbutton, unzip" in prompt
    assert "STAYING FASTENED" in prompt
    # "잠긴 건 잠긴 채로" 만으로는 4회 중 1회가 목 부분을 더 벌렸다. 판정 가능한 기준
    # (잠긴 단추 수·개구부 깊이가 입력보다 나빠지지 않을 것)으로 대체한다(2026-07-30).
    assert "THE SAME OR MORE" in prompt
    assert "NO LOWER on the body" in prompt
    # untuck 교정 — 들어와 있으면 빼내고, 이미 나와 있으면 그대로 둔다(2026-07-30).
    assert "PULL IT OUT" in prompt
    assert "COMPLETELY OUTSIDE" in prompt
    assert "unbroken visible line" in prompt
    # 밑단 모양·길이는 건드리지 않는다 — 빼내는 것이지 늘리는 게 아니다.
    assert "curved shirttail hem stays" in prompt


def test_retired_maximal_push_wording_stays_out():
    """v1 의 과편집 유발 문구가 되살아나면 안 된다 — 회귀 알람.

    이 문구들은 취향이 아니라 **측정으로 기각됐다**(핏 깨짐 7/10). 누가 "변화가 약한데?"
    라고 되돌리기 쉬운 자리라 명시적으로 막는다. 크기를 키우려면 BUST_TARGET 배수를 올리고
    다시 재라 — 압박 문구를 되살리는 게 아니라.
    """
    prompt = mannequin_bust.build_prompt(load_bust_prompt_template())
    for retired in ("FAILURE of this task", "go FURTHER", "Err on the side of too much"):
        assert retired not in prompt, retired


def test_build_prompt_rejects_unresolved_token():
    try:
        mannequin_bust.build_prompt("hello ${oops} world")
    except ValueError as e:
        assert "oops" in str(e)
    else:
        raise AssertionError("미해결 토큰인데 통과했다")


# ---------- 워커 배선 ----------

def _run(*, gender, mode, generate, calls_spent=0):
    """_apply_bust_pass 를 격리 실행. (결과, 발행된 step 이벤트) 반환."""
    emits = []

    async def fake_emit(pool, job_id, event_type, payload):
        emits.append((event_type, dict(payload)))

    gemini = SimpleNamespace(generate_content_image=generate)
    s = make_settings(mannequin_bust_pass=mode)
    orig_emit = mannequin_job._emit
    mannequin_job._emit = fake_emit
    try:
        out = asyncio.run(mannequin_job._apply_bust_pass(
            pool=None, gemini=gemini, s=s, job_id="j1", candidate="A", attempt=1,
            base_gender=gender, res=_ORIG, calls_spent=calls_spent))[0]
    finally:
        mannequin_job._emit = orig_emit
    return out, emits


def test_bust_pass_skipped_when_flag_off():
    called = []

    async def generate(*a, **k):
        called.append(1)
        return _EDITED

    out, emits = _run(gender="women", mode="off", generate=generate)
    assert out is _ORIG
    assert called == []      # 호출 자체가 없어야 한다 — 기본값 셀러에게 비용이 붙으면 안 된다
    assert emits == []


def test_bust_pass_skipped_for_men():
    called = []

    async def generate(*a, **k):
        called.append(1)
        return _EDITED

    out, emits = _run(gender="men", mode="on", generate=generate)
    assert out is _ORIG
    assert called == []


def test_bust_pass_applies_for_women():
    seen = {}

    async def generate(model, prompt, images, size, aspect_ratio=None):
        seen["model"] = model
        seen["prompt"] = prompt
        seen["n_images"] = len(images)
        return _EDITED

    out, emits = _run(gender="women", mode="on", generate=generate)
    assert out is _EDITED
    assert seen["n_images"] == 1                       # 1패스 결과 한 장만 — 단독 과제여야 먹힌다
    assert "a full C CUP" in seen["prompt"]
    # Flash 는 거부·미반영으로 탈락했다. 티어는 image_high 고정.
    assert seen["model"] == make_settings().model_image_high
    assert [p["outcome"] for t, p in emits if p.get("status") == "bust_pass"] == ["applied"]


def test_bust_pass_fails_open_on_refusal():
    """콘텐츠 필터 거부(GeminiError) — 1패스 컷을 그대로 쓰고 잡은 살아야 한다."""
    async def generate(*a, **k):
        raise GeminiError(
            "응답에 이미지 없음. 텍스트: I cannot modify the physical characteristics "
            "of the mannequin's chest")

    out, emits = _run(gender="women", mode="on", generate=generate)
    assert out is _ORIG                                 # 원본 유지 — 예외가 새어나가면 안 된다
    bust_events = [p for t, p in emits if p.get("status") == "bust_pass"]
    assert [p["outcome"] for p in bust_events] == ["failed_open"]
    assert bust_events[0]["error_type"] == "GeminiError"


def test_bust_pass_fails_open_on_unexpected_error():
    async def generate(*a, **k):
        raise RuntimeError("network down")

    out, emits = _run(gender="women", mode="on", generate=generate)
    assert out is _ORIG
    assert [p["outcome"] for t, p in emits if p.get("status") == "bust_pass"] == ["failed_open"]
