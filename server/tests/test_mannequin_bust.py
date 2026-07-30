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


def test_build_prompt_substitutes_target_and_keeps_calibrated_wording():
    prompt = mannequin_bust.build_prompt(load_bust_prompt_template())
    assert "${" not in prompt
    # 실측 캘리브레이션으로 고른 크기 — 이 배수가 결과 크기를 결정한다.
    assert "a full C CUP" in prompt
    assert "1.5 times" in prompt
    # 스파이크에서 실제로 효과를 만든 요소들. 빠지면 변화폭이 0에 가까워진다(2026-07-30 실측).
    assert "FAILURE of this task" in prompt          # 과소 변화 = 실패 선언
    assert "go FURTHER" in prompt
    assert "TENTS over the bust apex" in prompt      # 천이 몸 변화를 보고해야 보인다
    assert "falls AWAY from the stomach" in prompt
    # 실루엣 — 슬림 아워글래스. 골반 존재감을 "골반을 넓혀서"가 아니라 "허리를 좁혀서" 만든다.
    # 1차 스파이크에서 torso·hips 를 함께 키웠더니 허리가 +8.7% 굵어져 전신이 "뚱뚱하게"
    # 나왔으므로, 방향을 반대로 주고 골반 폭 상한을 명시한다(2026-07-30).
    assert "SLIMMER waist" in prompt
    assert "HIP WIDTH MUST NOT INCREASE" in prompt
    assert "never from the hips spreading out" in prompt
    # 전신 비대화 방지 — 1차 실패 모드의 직접 가드.
    assert "NOT read as heavier" in prompt
    # 앞섬 잠금 — 2패스가 가슴 공간을 만드느라 단추를 풀어버린다(1패스 4/4 잠김 vs 2패스 풀림).
    assert "stays done up" in prompt
    assert "Do NOT open, unbutton, unzip" in prompt
    assert "STAYING FASTENED" in prompt
    # "잠긴 건 잠긴 채로" 만으로는 4회 중 1회가 목 부분을 더 벌렸다. 원본 셔츠 사진은 맨 위
    # 단추가 풀린 상태(사진 관례)라 어디까지가 원래 열림인지 판단 여지가 있었다. 판정 가능한
    # 기준(잠긴 단추 수·개구부 깊이가 입력보다 나빠지지 않을 것)으로 대체한다(2026-07-30).
    assert "THE SAME OR MORE" in prompt
    assert "NO LOWER on the body" in prompt
    # untuck 교정 — 1패스의 untuck 지시는 실측 2/4 만 지켜진다(확률적). 지시를 더 세게 쓰는 건
    # 수확 체감이라, 이미 여성 컷마다 도는 이 패스에서 결과를 교정한다. 보존이 아니라 강제다:
    # 들어와 있으면 빼내고, 이미 나와 있으면 그대로 둔다(2026-07-30).
    assert "PULL IT OUT" in prompt
    assert "COMPLETELY OUTSIDE" in prompt
    assert "unbroken visible line" in prompt
    # 밑단 모양·길이는 건드리지 않는다 — 빼내는 것이지 늘리는 게 아니다.
    assert "curved shirttail hem stays" in prompt


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
