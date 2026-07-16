"""CX 표준인증창(본인확인) 연령 판별 — 개인화 연령 게이트(T2-1) 전용 헬퍼.

CX 표준인증창은 통신사 본인확인 기반이라 **검증된 생년월일**을 주는 유일한 실동작 수단이다
(라이브니스는 '살아있는 사람인가'라 연령 소스가 아니고, OpenDID 배선은 얼굴 라이선스 VC 전용).
프론트가 위젯 성공 콜백 token 만 넘기고, 서버가 CX `trans/{token}` 을 서버발 호출해 원문을 받는다.

개인정보 최소수집 (api-spec §1.4 정신):
  · 이 모듈은 birth 원문을 **반환하지 않는다** — `is_adult` 불리언만 돌려주고 원문은 호출 스택에서
    소멸한다. DB(`personalization_identity_verifications`)에도 불리언만 남는다.
  · CI·이름·생년월일 미저장. 로그에도 birth 미기록(판별 실패 시 형식 종류만).
  · FaceMarket 은 `fm_identity_verifications` 에 birthYear(연도)를 남기지만, 개인화는 연령 게이트에
    연도조차 불필요하므로 저장하지 않는다.

facemarket.py 의 동명 헬퍼와 소폭 중복되나 의도적이다 — 해커톤 필수 경로(FaceMarket)를 건드리지
않기 위함. 추후 통합 시 이 모듈을 단일 원천으로 삼는다.
"""

import logging
from datetime import date, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger("wearless.cx_identity")

CX_TRANS_TIMEOUT = 10.0

# 만 나이 기준 시각 = KST(한국 법 기준). 배포 컨테이너 TZ 에 의존하지 않는다.
_KST = ZoneInfo("Asia/Seoul")

# 민법상 성년 = 만 19세. 개인화는 '성인 본인 동의' 전제(phase0-license-check.md 정책 게이트)라
# 미성년은 법정대리인 동의 플로우 없이 차단한다(PRD N2, consent-architecture §6).
ADULT_MIN_AGE = 19


class CxIdentityError(RuntimeError):
    """CX 조회·판별 실패. 라우트가 사용자 안내 에러로 매핑한다(원문·birth 미포함)."""


async def fetch_trans(base_url: str, token: str) -> dict:
    """CX `trans/{token}` 서버발 조회 → 원문 신원 dict. 테스트 monkeypatch 지점.

    응답 원문에는 CI·생년월일 등 민감 신원이 들어 있다 — **로그 금지**(status 만 기록).

    token 은 **URL 인코딩 후 보간**한다(`safe=""` → `/`·`?`·`#`·`.` 전부 이스케이프). 미인코딩
    보간은 `x/../../evil`(경로 이탈)·`x?a=b`(쿼리 주입)로 CX 호스트 내 임의 엔드포인트를 때릴 수
    있다(httpx 가 dot-segment 를 정규화). 문자 화이트리스트 대신 인코딩을 쓰는 이유 = CX 토큰의
    실제 문자셋이 미확정이라, 정규식이 틀리면 정상 인증이 전부 깨지는 반면 인코딩은 오탐 0이다.
    """
    url = f"{base_url}/oacx/api/v1.0/trans/{quote(token, safe='')}"
    async with httpx.AsyncClient(timeout=CX_TRANS_TIMEOUT) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        logger.warning("cx_trans_failed", extra={"status": resp.status_code})
        raise CxIdentityError("cx_verify_failed")
    try:
        return resp.json()
    except ValueError:
        raise CxIdentityError("cx_parse_failed")


def dig(data: dict, *keys):
    """flat 또는 result/data 중첩 응답 모두 대응 — 첫 존재 키 값 반환."""
    scopes = [data]
    for wrap in ("result", "data"):
        inner = data.get(wrap)
        if isinstance(inner, dict):
            scopes.append(inner)
    for scope in scopes:
        for k in keys:
            v = scope.get(k)
            if v not in (None, ""):
                return v
    return None


def is_adult_from_birth(birth, *, today: date | None = None, min_age: int = ADULT_MIN_AGE) -> bool:
    """CX birth → 만 `min_age` 세 이상 여부. 판별 불가면 CxIdentityError.

    반환은 **불리언뿐** — 생년월일은 호출자에게 넘기지 않는다(최소수집).

    형식 대응:
      · YYYYMMDD(8자리) / 'YYYY-MM-DD' 등 구분자 포함 → 만 나이 정확 계산.
      · YYYY(4자리)만 → 생일 미상이라 정확 계산 불가 → **보수적 판정**: 연도차가 min_age+1 이상일
        때만 성인(생일 전이어도 만 min_age 를 보장). 경계 연도는 미성년 취급 = 안전측 오류.
    """
    # 만 나이는 한국 법(민법 §158) 기준이므로 기준일도 KST 로 고정한다. date.today() 는 컨테이너
    # 로컬 TZ(핀 없음 → UTC) 의존이라 배포 환경에 따라 하루 밀린다.
    today = today or datetime.now(_KST).date()
    digits = "".join(ch for ch in str(birth or "") if ch.isdigit())

    if len(digits) == 8:
        year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
        # 연도 범위 검증은 8자리 경로에도 필요하다 — 'MMDDHHMM'(예 '01011225')처럼 생년월일이
        # 아닌 8자리가 오면 year=101 → 연도차 1900+ → **성인 오통과**. 4자리 경로만 막으면 샌다.
        if not (1900 <= year <= today.year):
            raise CxIdentityError("birth_year_out_of_range")
        try:
            date(year, month, day)  # 실존 날짜 검증(0229 등)
        except ValueError:
            raise CxIdentityError("birth_invalid")
        # 만 나이 = 연도차 - (올해 생일이 아직 안 지났으면 1)
        age = today.year - year - ((today.month, today.day) < (month, day))
        return age >= min_age

    if len(digits) == 4:
        year = int(digits)
        # 연도 범위 검증 — 없으면 'MMDD'(예 '0101')가 4자리로 들어올 때 year=101 → 연도차 1900+ →
        # **성인 오통과**. FaceMarket 이 birthYear 를 str(birth)[:4] 로 무검증 절단해 채우므로
        # CX 스키마가 바뀌면 그 값이 그대로 폴백에 들어온다. 안전측(미인증 취급)으로만 이동한다.
        if not (1900 <= year <= today.year):
            raise CxIdentityError("birth_year_out_of_range")
        # 생일 미상 → 연도차 >= min_age+1 이어야 생일 전후 무관하게 만 min_age 확정.
        logger.info("cx_birth_year_only")  # 연도값 미기록 — 형식 종류만 관측
        return (today.year - year) >= (min_age + 1)

    raise CxIdentityError("birth_unparsable")
