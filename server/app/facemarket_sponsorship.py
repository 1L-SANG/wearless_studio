"""Model-owned sponsorship fields, independent of license and VC claims."""

import re
from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from .models import CamelModel


SPONSORSHIP_COLUMNS = (
    "sponsorship_enabled", "instagram_handle", "instagram_followers",
    "instagram_followers_reported_at", "size_top", "size_bottom_waist",
    "sponsorship_profile_consent_at",
)
# 셀러에게 보이는 상세. 동의 시각은 본인에게만 돌려줘요.
SPONSORSHIP_DETAIL_COLUMNS = SPONSORSHIP_COLUMNS[1:-1]


class SponsorshipFields(CamelModel):
    sponsorship_enabled: bool = False
    instagram_handle: str | None = None
    instagram_followers: int | None = None
    instagram_followers_reported_at: datetime | None = None
    size_top: str | None = None
    size_bottom_waist: int | None = None
    sponsorship_profile_consent_at: datetime | None = None


class SponsorshipResult(SponsorshipFields):
    id: str


class SponsorshipPatch(CamelModel):
    model_config = ConfigDict(extra="forbid")

    sponsorship_enabled: StrictBool = False
    instagram_handle: StrictStr | None = None
    instagram_followers: StrictInt | None = Field(default=None, ge=0, le=2147483647)
    size_top: Literal["XS", "S", "M", "L", "XL", "FREE"] | None = None
    size_bottom_waist: StrictInt | None = Field(default=None, ge=24, le=34)
    # 프로필 정보 수집 동의(E-2b). 켤 때 True 를 보내야 하고, 기록은 서버가 시각으로 남겨요.
    profile_consent: StrictBool | None = None

    @field_validator("instagram_handle")
    @classmethod
    def instagram_username(cls, value):
        if value is None:
            return None
        value = value.strip().removeprefix("@")
        if (
            not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", value)
            or value.startswith(".") or value.endswith(".") or ".." in value
        ):
            raise ValueError("올바른 인스타 계정을 입력해 주세요.")
        return value


def sponsorship_view(row: dict, *, owner: bool = False, details: bool = True,
                     credential_gate: bool = False) -> dict:
    """협찬 필드의 노출 규칙.

    owner: 본인은 켜짐과 무관하게 전부 본다(동의 시각 포함).
    details: 로그인한 셀러만 계정·팔로워·사이즈를 본다. 비로그인 공개 화면은 켜짐 여부만.
    credential_gate: FM_SPONSORSHIP_VC=on — 남에게는 협찬 동의 VC 가 유효(active)한 모델만
      협찬으로 보인다. row 에 sponsorship_credential_active 가 있어야 한다(없으면 막힌 것으로 본다).
    """
    enabled = bool(row.get("sponsorship_enabled"))
    if credential_gate and not owner and not row.get("sponsorship_credential_active"):
        enabled = False
    show = owner or (enabled and details)
    return {
        "sponsorship_enabled": enabled,
        **{field: row.get(field) if show else None for field in SPONSORSHIP_DETAIL_COLUMNS},
        "sponsorship_profile_consent_at": row.get("sponsorship_profile_consent_at") if owner else None,
    }


# 동의 이력에 남기는 고지 문구. 화면(src/features/model/SponsorshipSettings.jsx, sponsorshipOptions.js)이
# 보여 주는 문장과 글자 그대로 같아야 해요 — 기록된 해시가 "모델이 본 문구"의 증빙이니까.
# tests/test_facemarket_sponsorship.py 가 화면 소스와 대조해요. 한쪽만 고치면 그 테스트가 잡아요.
SPONSORSHIP_NOTICES = {
    'sponsorship_participation': {
        'title': '의류 협찬 받기',
        'notice': '켜 두면 셀러에게 협찬 요청까지 받을 수 있어요.',
        'scope': '협찬 옷도 위에서 정한 허용 품목 안에서만 와요.',
        'rules': ['옷을 받은 뒤 기본적으로 7일 이내, SNS 피드에 착용컷을 올리면 돼요.', '게시물은 90일간만 유지하면 돼요.'],
    },
    'sponsorship_profile_collection': {
        'title': '프로필 정보 수집에 동의합니다',
        # 보유 기간(끄거나 탈퇴할 때까지)은 동의를 받을 때 알려야 하는 항목이라 문장에 넣어요(04 동의서 E-2b).
        'notice': '협찬 모델을 찾는 로그인 셀러에게 인스타 계정, 팔로워 수, 사이즈가 보여요. 배송지는 요청이 온 뒤에 받아요. 협찬을 끄거나 탈퇴하면 이 정보는 바로 지워요.',
        'refusal': '동의하지 않아도 모델 등록과 얼굴 사용료 정산은 이용할 수 있어요.',
    },
}


async def record_sponsorship_consents(cur, request, user_id, model_id, *, enabled, previous_enabled,
                                      profile_consent, client_ip=None):
    """설정과 같은 트랜잭션에 실제 고지 문구의 버전·해시를 기록해요.

    client_ip: 호출자가 ALB 의 X-Forwarded-For 를 푼 주소(facemarket._request_client_ip). 없으면 ASGI peer 를
    쓰는데, 운영에서는 그게 ALB 내부 주소(10.x)라 증빙이 안 돼요.
    """
    import hashlib
    import ipaddress
    import json
    import uuid
    from .legal_versions import SPONSORSHIP_CONSENTS

    if enabled == previous_enabled and profile_consent is not True:
        return
    notices = SPONSORSHIP_NOTICES
    try:
        raw_ip = client_ip or (request.client.host if request.client else None)
        address = str(ipaddress.ip_address(raw_ip)) if raw_ip else None
    except ValueError:
        address = None
    screen = request.headers.get('x-facemarket-screen', 'sponsorship_settings')
    if screen not in {'model_register', 'model_mypage', 'sponsorship_settings'}:
        screen = 'sponsorship_settings'
    event_key = str(uuid.uuid4())
    for consent_type, notice in notices.items():
        snapshot = json.dumps(notice, ensure_ascii=False, sort_keys=True)
        await cur.execute(
            """insert into fm_sponsorship_consent_events
               (user_id, model_id, actor_user_id, consent_type, action, doc_version,
                document_sha256, ip_address, screen_id, notice_snapshot, idempotency_key, reason)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, 'model_toggle')""",
            (user_id, model_id, user_id, consent_type, 'granted' if enabled else 'withdrawn',
             SPONSORSHIP_CONSENTS[consent_type]['version'],
             hashlib.sha256(snapshot.encode()).hexdigest(), address, screen, snapshot, event_key),
        )
