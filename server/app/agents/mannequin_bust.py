"""여성 마네킹 기본 가슴 볼륨 2패스 — 순수 모듈 (DB·네트워크 없음).

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
BUST_TARGET = (
    "a full C CUP — clearly bigger than the B cup in the attached image. The bust must "
    "project forward from the chest wall roughly 1.5 times as far as it does now"
)

_TOKEN = "${bustTarget}"


def should_apply(gender: str, mode: str) -> bool:
    """2패스를 돌릴지. 여성 + 플래그 on 일 때만.

    남성은 현행과 완전히 동일한 경로를 타야 한다(2패스 없음). mode 는 config 의
    mannequin_bust_pass ('off' | 'on') — 기본 off 로 두고 확인 후 켠다.
    """
    return mode == "on" and gender == "women"


def build_prompt(template: str) -> str:
    """템플릿의 ${bustTarget} 치환. 미해결 토큰이 남으면 즉시 실패시킨다(오타 검출).

    render_mannequin_prompt 와 같은 규약 — 조용히 토큰이 남은 프롬프트가 모델로 가면
    무슨 일이 일어나는지 알 수 없다.
    """
    prompt = template.replace(_TOKEN, BUST_TARGET)
    if "${" in prompt:
        leftover = sorted({p.split("}")[0] + "}" for p in prompt.split("${")[1:]})
        raise ValueError(f"가슴 프롬프트 템플릿에 해결되지 않은 토큰: {leftover}")
    return prompt
