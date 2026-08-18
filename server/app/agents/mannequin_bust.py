"""여성 마네킹 기본 가슴 볼륨 2패스.

프롬프트 조립·판정 규칙은 순수 함수지만, judge_gate 는 예외다 — 템플릿 파일을 읽고
vision LLM 을 호출한다(2026-08-19 사전 게이트). 동기 컨텍스트에서 부르지 말 것.

왜 2패스인가 (2026-07-30 스파이크, docs/reports/2026-07-30-mannequin-volume-2pass-spike.md):
- 볼륨 있는 베이스를 image 1 로 넣어도 1패스가 몸을 표준으로 정규화한다. 실측으로 베이스끼리
  가슴아래 폭 +12.2% 차이가 옷 입히면 +2.4% 로 죽는다. GPT images/edits 도 동일 실패.
- 1패스 프롬프트에 가슴 지시를 주입해도 안 된다 — 이미지 4장 + 의류 충실도 지시와 경쟁해 희석.
- 옷 입힌 컷 1장에 "가슴만 바꿔라"를 **단독 과제**로 주면 반영된다. 그게 이 모듈이다.

Flash(image_light) 는 쓰지 않는다: 2회 중 1회가 "I cannot modify the physical characteristics
of the mannequin's chest" 로 거부(이미지 없이 텍스트 반환), 거부하지 않은 1회도 변화가 거의
없었다. 티어는 image_high 고정 — 선택지로 두지 않는다.
"""

# 가슴 목표 문구 — 고정 상수만 쓴다. 셀러 문자열은 어떤 경로로도 여기 들어오지 않는다.
#
# 문구 강도는 실측으로 캘리브레이션했다(스파이크 사다리: 원본B → C1.5배 → C~D1.75배 → D2배).
# 사용자가 C컵 1.5배 수준을 선택. 이 상수 한 줄이 결과 크기를 결정하므로, 조정이 필요하면
# 배수(1.5)와 컵 표기를 함께 올리거나 내린다.
#
# "더 크게" 같은 상대 표현은 변화폭이 사실상 0이었다. 컵 사이즈 + 물리적 배수라야 움직인다.
# 크기는 실측 사다리(원본B → C1.5배 → C~D1.75배 → D2배)에서 사용자가 고른 값이다.
#
# 1.3배 실험(2026-07-30, 되돌림): 셔츠 앞섬이 4회 중 1회 벌어지는 것이 "커진 가슴이 셔츠에
# 안 들어가서"라고 보고 크기를 낮춰봤다. 결과는 여전히 3/4 — 같은 샘플(#4)만 계속 실패했고,
# 1.5배 원문구·1.5배 강화문구·1.3배 세 설정에서 실패 패턴이 동일했다. 즉 앞섬 열림은 가슴
# 크기가 아니라 **입력 이미지 의존**이다. 크기를 낮춰 얻은 것은 없고 허리만 과하게 잘록해져
# 되돌린다.
# 배수는 1.5 → 1.3 (2026-08-01). 1.5 는 가슴이 앞으로 너무 나와 전신이 "뚱뚱하게" 읽혔다 —
# 상세페이지는 옷을 소개하는 화면이라 마네킹이 무거워 보이면 상품 인상까지 같이 내려간다.
# 같은 베이스 컷에 배수만 바꿔 뽑은 그리드(scratch/cmp_c13_full.png, 1.5 ×1 vs 1.3 ×4)에서
# 셀러가 1.3 쪽을 골랐다. 컵 표기는 그대로 둔다 — 배수만으로 크기가 움직이는 게 확인됐고,
# 둘을 같이 내리면 무엇이 효과인지 다시 알 수 없어진다.
BUST_TARGET = (
    "a full C CUP — clearly bigger than the B cup in the attached image. The bust must "
    "project forward from the chest wall roughly 1.3 times as far as it does now"
)

# 허리·골반 목표(2026-07-30). 상세페이지에서 마네킹이 상품 인상을 좌우하므로 슬림 아워글래스로
# 잡는다: 몸통은 마르게, 골반은 라인이 드러나게.
#
# **핵심 설계**: 골반 존재감을 "골반을 넓혀서"가 아니라 "허리를 좁혀서" 만든다. 1차 스파이크에서
# torso·hips 를 함께 키우라고 했더니 허리까지 굵어져(+8.7%) 전신이 "뚱뚱하게" 나왔다. 방향을
# 반대로 주면 같은 실패로 돌아갈 수 없다 — 골반 폭 상한을 명시적으로 못 박는다.
WAIST_HIP_TARGET = (
    "a visibly SLIMMER waist than in the attached image, nipped in at its narrowest point, "
    "flowing into hips whose line is clearly defined by a curve rather than a straight drop. "
    "The hips must NOT become wider than they are in the attached image — the shape comes from "
    "the waist coming IN, never from the hips going OUT. The whole body must read as slimmer "
    "than the attached image, never heavier"
)

from . import edit_gate
from .prompts import load_bust_gate_prompt_template
from .vision_llm import analyze_with_fallback

_TOKENS = {"${bustTarget}": BUST_TARGET, "${waistHipTarget}": WAIST_HIP_TARGET}

# 사전 게이트(2026-08-19 오너 승인) — 편집 콜(45~65초·$0.14) 전에 값싼 판정(~$0.01)으로
# "이미 가슴 볼륨이 충분히 표현돼 있나"를 묻는다. 근거 실측: 보정 적용 66건 중 47% 가 회귀
# 판정으로 폐기(8/1~). 규약은 untuck 게이트와 동일 — 공유 구현 edit_gate 모듈 참조
# (비대칭·스킵은 확신에 찬 adequate 뿐·임계는 두 게이트 공용).
GATE_SKIP_CONFIDENCE = edit_gate.GATE_SKIP_CONFIDENCE

_GATE_VERDICTS = ("adequate", "insufficient", "unclear")


def gate_schema() -> dict:
    return edit_gate.schema(_GATE_VERDICTS)


def validate_gate(raw: dict | None) -> dict:
    return edit_gate.validate(raw, _GATE_VERDICTS)


def gate_skips(result: dict) -> bool:
    """확신에 찬 adequate 만 편집을 건너뛴다 (순수)."""
    return edit_gate.skips(result, "adequate")


async def judge_gate(settings, cut_image) -> dict:
    """생성본 1장만 보고 가슴 볼륨이 이미 충분한지 판정한다. 실패는 호출자가 잡아 편집 실행."""
    prompt = load_bust_gate_prompt_template()
    model = getattr(settings, "mannequin_bust_gate_model", "") or ""
    raw, _provider = await analyze_with_fallback(
        settings, prompt, [cut_image], gate_schema(),
        models={"gemini": model} if model else None)
    return validate_gate(raw)


# 가슴을 덮는 옷. 2패스는 이 카테고리에서만 의미가 있다.
_CHEST_COVERING = {"top", "outer", "dress"}


def should_apply(gender: str, mode: str, clothing_type: str | None = None) -> bool:
    """2패스를 돌릴지. 여성 + 플래그 on + **가슴을 덮는 상품**일 때만.

    남성은 현행과 완전히 동일한 경로를 타야 한다(2패스 없음). mode 는 config 의
    mannequin_bust_pass ('off' | 'on') — 기본 off 로 두고 확인 후 켠다.

    하의(bottom)를 뺀 이유는 이 패스의 전제 그대로다 — 프롬프트가 "마네킹이 옷을 입고 있으니
    **천이 가슴 크기를 보여주는 유일한 수단**"이라고 말하는데, 하의 컷에는 가슴을 덮는 옷이
    없다. 2026-07-31 실 워커 출고본에서 확인: 진·스커트 컷에도 2패스가 돌아 이미지모델 호출을
    쓰고(1건은 등급을 떨어뜨려 되돌려짐) **상품과 무관한 맨상체만 키웠다**.

    카테고리를 모르면(None) 적용한다 — 상의가 대다수라 모를 때 거르는 쪽이 더 자주 틀린다.
    하의에 상의를 함께 입혀 연출하게 되면 이 판단을 다시 봐야 한다.
    """
    if mode != "on" or gender != "women":
        return False
    return clothing_type is None or str(clothing_type).lower() in _CHEST_COVERING


def build_prompt(template: str) -> str:
    """템플릿의 ${bustTarget}·${waistHipTarget} 치환. 미해결 토큰이 남으면 즉시 실패시킨다.

    render_mannequin_prompt 와 같은 규약 — 조용히 토큰이 남은 프롬프트가 모델로 가면
    무슨 일이 일어나는지 알 수 없다.
    """
    prompt = template
    for token, value in _TOKENS.items():
        prompt = prompt.replace(token, value)
    if "${" in prompt:
        leftover = sorted({p.split("}")[0] + "}" for p in prompt.split("${")[1:]})
        raise ValueError(f"가슴 프롬프트 템플릿에 해결되지 않은 토큰: {leftover}")
    return prompt
