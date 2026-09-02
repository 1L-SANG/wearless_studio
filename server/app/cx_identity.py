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
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal
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


@dataclass(frozen=True, slots=True)
class OacxBiometricContract:
    """정체성(CI·이름·생년월일)은 trans/{token}, 초상은 클라 릴레이 HEX — 출처가 다르다(D1).

    D1 이전에는 이 계약이 trans 응답 안의 `idPortraitBase64`(발명된, 실존하지 않는 필드)를
    가리켰다 — 실 OACX 응답에는 그 필드가 없어 초상을 영영 얻을 수 없었다. 실측(라이브 콜백)
    결과 진짜 신분증 얼굴 사진은 `OACX.LOAD_MODULE(...) → res.data.dlphotoimage` (RESULT
    스텝, `useConvertor:true` 필요) 로 프론트 콜백에 직접 온다 — trans 필드가 아니다.
    그래서 이 계약은 이제 trans 필드 경로(`birth_path`)만 갖고, 초상 포맷(encoding·크기 상한)은
    별도로 `parse_oacx_portrait_hex` 가 클라가 넘긴 HEX 문자열에 적용한다.
    """

    version: str
    birth_path: tuple[str, ...]
    portrait_encoding: Literal["hex"]
    max_portrait_bytes: int


@dataclass(slots=True)
class OacxBiometricEvidence:
    """trans/{token} 에서만 유래 — 초상은 여기 없다(별도로 `parse_oacx_portrait_hex` 참조)."""

    ci: bytearray
    birth: str
    name_masked: str
    transaction_id: str | None
    contract_version: str


class OacxBiometricError(CxIdentityError):
    def __init__(self, reason: str = "id_portrait_unavailable"):
        self.reason = reason
        super().__init__(reason)


# dev 전용 — get_oacx_biometric_contract() 가 app_env=='dev' 일 때만 선택한다(아래).
DEV_MOCK_OACX_BIOMETRIC_CONTRACT = OacxBiometricContract(
    version="dev-mock-v1",
    birth_path=("birth",),
    portrait_encoding="hex",
    max_portrait_bytes=5 * 1024 * 1024,
)

# D1: 실 프로덕션 계약 — OmniOne 매뉴얼 실측(dlphotoimage, HEX JPEG, RESULT 스텝) 기반.
# dev-mock-v1 과 달리 app_env=='dev' 게이트가 없다 — prod 에서도 유효해야 하는 계약이다.
PROD_DLPHOTO_OACX_BIOMETRIC_CONTRACT = OacxBiometricContract(
    version="prod-dlphoto-v1",
    birth_path=("birth",),
    portrait_encoding="hex",
    max_portrait_bytes=5 * 1024 * 1024,
)

_JPEG_MAGIC = b"\xff\xd8\xff"


def _nested_value(data: dict, path: tuple[str, ...]):
    value = data
    for key in path:
        if not isinstance(value, dict):
            raise KeyError
        value = value[key]
    return value


def _mask_name(name: str) -> str:
    name = name.strip()
    if len(name) <= 1:
        return name or "익명"
    if len(name) == 2:
        return name[0] + "*"
    return name[0] + "*" * (len(name) - 2) + name[-1]


def parse_oacx_biometric_evidence(
    trans: dict,
    *,
    contract: OacxBiometricContract,
) -> OacxBiometricEvidence:
    """trans/{token} 에서 CI·이름·생년월일만 파싱한다 — 초상은 여기 없다(D1 이후).

    실패는 전부 `OacxBiometricError`(기본 사유 `id_portrait_unavailable`)로 정규화해
    CI·원문 생년월일이 예외 메시지·로그로 새지 않게 한다. 미성년만 별도 사유(`minor_blocked`).
    """
    try:
        # 실 OACX trans/{token} 응답은 신원 필드를 data/result 아래에 중첩하고 이름은 name,
        # 테스트 목은 flat 에 nm 을 쓴다 — dig() 로 두 구조·두 키 이름을 모두 흡수한다.
        ci = dig(trans, "ci")
        name = dig(trans, "name", "nm")
        transaction_id = dig(trans, "txId")
        if not all(isinstance(value, str) and value for value in (ci, name)):
            raise ValueError
        if transaction_id is not None and not isinstance(transaction_id, str):
            raise ValueError

        birth = dig_path(trans, contract.birth_path)
        if not isinstance(birth, str) or not birth:
            raise ValueError
        try:
            adult = is_adult_from_birth(birth)
        except CxIdentityError:
            raise ValueError from None
        if not adult:
            raise OacxBiometricError("minor_blocked")

        return OacxBiometricEvidence(
            ci=bytearray(ci.encode()),
            birth=birth,
            name_masked=_mask_name(name),
            transaction_id=transaction_id,
            contract_version=contract.version,
        )
    except OacxBiometricError:
        raise
    except Exception:
        raise OacxBiometricError() from None


@dataclass(frozen=True, slots=True)
class IdentityClaimMatch:
    """지원서 이름·생년월일 대조 결과 — raw 값은 담지 않는다(불리언·정밀도만, E13)."""

    matched: bool
    name_matched: bool
    birth_precision: Literal["full", "year", "none"]


def _normalize_name(name: str) -> str:
    """대조용 이름 정규화: 모든 공백 제거 + casefold(라틴 대소문자 무시).

    'KIM MIN SU' == 'KimMinsu' (E13). 한글은 공백만 제거되고 casefold 는 무영향.
    """
    return "".join(ch for ch in str(name or "") if not ch.isspace()).casefold()


def compare_identity_claim(
    trans: dict,
    *,
    contract: OacxBiometricContract,
    expected_name: str,
    expected_birthdate: date,
) -> IdentityClaimMatch:
    """OACX trans 의 이름·생년월일을 지원서 주장과 대조한다 — raw 는 이 함수 밖으로 나가지 않는다.

    E13(4A 의 안전한 구현): 기대값(지원서 이름·생년월일)을 파서에 넘기고 match 결과만 반환.
    - 이름: 공백 제거 + casefold 후 완전일치(라틴 대소문자·공백 무시).
    - 생년월일: 8자리면 연·월·일 전체 비교. 4자리(year-only, `cx_birth_year_only` 실존)면
      **이름 일치 + 연도 일치**만으로 통과(약한 보장, precision='year'). 미성년 차단은 파서가 담당.
    실패·형식오류는 matched=False 로 흡수(원문·birth 를 예외·로그로 흘리지 않는다).
    """
    try:
        raw_name = dig(trans, "name", "nm")
        raw_birth = dig_path(trans, contract.birth_path)
        if not isinstance(raw_name, str) or not raw_name:
            return IdentityClaimMatch(False, False, "none")
        if not isinstance(raw_birth, str) or not raw_birth:
            return IdentityClaimMatch(False, False, "none")

        name_matched = _normalize_name(raw_name) == _normalize_name(expected_name)

        digits = "".join(ch for ch in raw_birth if ch.isdigit())
        expected = expected_birthdate
        if len(digits) == 8:
            birth_matched = (
                int(digits[:4]) == expected.year
                and int(digits[4:6]) == expected.month
                and int(digits[6:8]) == expected.day
            )
            precision: Literal["full", "year", "none"] = "full"
        elif len(digits) == 4:
            birth_matched = int(digits) == expected.year
            precision = "year"
        else:
            return IdentityClaimMatch(False, name_matched, "none")

        return IdentityClaimMatch(name_matched and birth_matched, name_matched, precision)
    except Exception:
        return IdentityClaimMatch(False, False, "none")


def parse_oacx_portrait_hex(
    hex_value: str | None, *, contract: OacxBiometricContract
) -> bytearray:
    """OACX RESULT-step 신분증 초상(`data.dlphotoimage`) — 클라가 릴레이한 HEX 를 검증한다(D1).

    보안 메모(고치지 않고 기록만 — out of scope): 이 초상은 OACX 가 발급했지만 클라가
    "중계"한다 — 즉 클라가 자기 자신의(서버검증된) CI 아래 자기-일관적인 가짜 초상을 넣을
    여지가 이론상 있다. 위험은 제한적이다 — 그러려면 (a) 진짜 모바일 신분증으로 token→CI
    까지 서버검증을 통과해야 하고, (b) AWS 라이브니스로 살아있는 사람 얼굴까지 통과해야
    한다. 근본적으로 막으려면 서버가 trans 응답의 `uncommitted.caInfo.faceAccessToken` 으로
    초상을 직접 재조회하거나, RESULT 스텝을 서버에서 한 번 더 부르는 방법이 있다 — 둘 다
    D1 범위 밖(후속 하드닝)이라 여기서는 구현하지 않는다.

    HEX 대소문자는 관용(`bytes.fromhex` 자체가 대소문자 무관) — 디코드 후 JPEG 매직바이트
    (`ffd8ff`)와 크기 상한만 검사한다. 얼굴 존재/일치 자체는 호출자가 SFace 로 확인한다.
    """
    portrait = None
    try:
        if contract.portrait_encoding != "hex" or not isinstance(hex_value, str):
            raise ValueError
        cleaned = hex_value.strip()
        if not cleaned:
            raise ValueError
        # 디코드 전에 길이로 상한을 걸어 과대 입력이 fromhex 까지 가지 않게 한다.
        if len(cleaned) > contract.max_portrait_bytes * 2:
            raise ValueError

        portrait = bytearray(bytes.fromhex(cleaned))
        if not portrait or len(portrait) > contract.max_portrait_bytes:
            raise ValueError
        if bytes(portrait[: len(_JPEG_MAGIC)]) != _JPEG_MAGIC:
            raise ValueError

        return portrait
    except OacxBiometricError:
        wipe_bytearray(portrait)
        raise
    except Exception:
        wipe_bytearray(portrait)
        raise OacxBiometricError("id_portrait_unavailable") from None


def get_oacx_biometric_contract(settings) -> OacxBiometricContract:
    if settings.fm_oacx_contract_mode == "prod-dlphoto-v1":
        return PROD_DLPHOTO_OACX_BIOMETRIC_CONTRACT
    if settings.app_env == "dev" and settings.fm_oacx_contract_mode == "dev-mock-v1":
        return DEV_MOCK_OACX_BIOMETRIC_CONTRACT
    raise OacxBiometricError("oacx_contract_unavailable")


def wipe_bytearray(value: bytearray | None) -> None:
    if value is not None:
        value[:] = b"\x00" * len(value)


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


def dig_path(data: dict, path: tuple[str, ...]):
    """계약이 소유한 '경로'(contract.birth_path)를 판다 — dig() 의 대체키 의미와 달리
    다단계 중첩을 따라간다. flat/result/data 세 스코프에서 경로를 시도해 첫 비어있지 않은
    str 을 반환한다. 실 계약은 단일 키(``("birth",)``)라 dig() 와 동일하게 동작하고,
    계약이 중첩 경로(``("identity","birth")``)를 지정하면 그 경로대로 파고든다."""
    if not path:
        return None
    scopes = [data]
    for wrap in ("result", "data"):
        inner = data.get(wrap)
        if isinstance(inner, dict):
            scopes.append(inner)
    for scope in scopes:
        cur = scope
        for key in path:
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(key)
        if isinstance(cur, str) and cur:
            return cur
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
