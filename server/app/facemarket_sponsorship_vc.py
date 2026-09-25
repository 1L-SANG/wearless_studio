"""협찬 동의 VC(fmsponsorship-v1) — 협찬을 켜면 발급, 끄면 폐기.

라이선스 VC(facelicense-v2)와는 **별개의 증서**다. 라이선스 VC 는 바뀌지 않는 등록 증거만
서명하고, 협찬은 모델이 언제든 켜고 끄므로 켤 때마다 새 증서를 내고 끌 때 폐기한다.
클레임은 **동의 사실만** 싣는다(인스타·팔로워·사이즈는 가변 개인정보라 DB 에만 둔다).

흐름(설계: docs/superpowers/specs/2026-09-25-facemarket-sponsorship-vc-design.md):
  켜기 → open_credential(같은 트랜잭션에 pending 행)
       → FmSponsorshipVcReconciler 가 issue_pending_credential 로 발급 → active
  끄기 → close_open_credential(revoked + 발급분은 fm_vc_revocation_jobs kind='sponsorship')

게이트(셀러 요청·공개 검증)는 **active 행만** 유효로 본다. revoked 로 바뀐 순간부터는
holder 폐기가 끝났는지와 무관하게 무효다.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

import httpx

from . import holder_client

log = logging.getLogger("wearless.fm_sponsorship_vc")

SPONSORSHIP_VC_PLAN = "fmsponsorship-v1"
_HOLDER_TIMEOUT = 180.0
#: 이 횟수부터 매 실패를 경고로 남긴다. 관리자 모델 상세의 lastErrorCode 로도 보인다.
ALERT_ATTEMPTS = 10
#: 이 횟수에 닿으면 더 집지 않는다(폐기 워커의 _MAX_ATTEMPTS 와 같은 값). 영구 실패 한 건이
#: 5분마다 holder 를 깨우고, 수요에서 빠지면 내려가고, 다시 깨우는 진동을 막는다.
MAX_ATTEMPTS = 50
#: 발급 한 건을 잡고 있는 동안 다른 인스턴스가 같은 행을 다시 집지 않게 미뤄 두는 시간.
#: 한 번의 발급은 holder 호출 3단계 × 요청당 180초 = 최대 540초라 그보다 길게 둔다.
#: 겹쳐도 holder 멱등키가 같은 VC 를 주고, record_issued 가 그 경우를 폐기로 오인하지 않는다.
CLAIM_LEASE_SECONDS = 600

_PARTICIPATION = "sponsorship_participation"
_PROFILE = "sponsorship_profile_collection"


def sponsorship_vc_enabled(settings) -> bool:
    return getattr(settings, "fm_sponsorship_vc", "off") == "on"


class SponsorshipVcIssueError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# 증서 열기·닫기 — 협찬 토글과 **같은 트랜잭션**에서 부른다.
# ---------------------------------------------------------------------------


async def open_credential(cur, *, model_id: str, user_id: str) -> str | None:
    """방금 기록된 granted 동의 이벤트로 pending 증서를 만든다. 이미 열린 증서가 있으면 None.

    record_sponsorship_consents 가 **같은 커서로 먼저** 두 행(참여·프로필)을 넣은 뒤 불러야 한다 —
    동의서 버전·해시·시각은 그 행에서 그대로 복사한다(증서와 동의 기록이 어긋날 수 없게).
    """
    await cur.execute(
        """select idempotency_key::text as key, consent_type, doc_version,
                  document_sha256, occurred_at
             from fm_sponsorship_consent_events
            where model_id = %s and user_id = %s and action = 'granted'
              and idempotency_key = (
                  select idempotency_key from fm_sponsorship_consent_events
                   where model_id = %s and user_id = %s and action = 'granted'
                   order by id desc limit 1)""",
        (model_id, user_id, model_id, user_id),
    )
    events = {row["consent_type"]: row for row in await cur.fetchall()}
    participation, profile = events.get(_PARTICIPATION), events.get(_PROFILE)
    if participation is None or profile is None:
        raise RuntimeError("sponsorship consent events missing for credential")
    await cur.execute(
        """insert into fm_sponsorship_credentials
               (model_id, consent_event_key, consent_doc_version,
                participation_doc_sha256, profile_doc_sha256, consented_at)
           values (%s, %s, %s, %s, %s, %s)
           on conflict (model_id) where status in ('pending', 'active') do nothing
           returning id::text as id""",
        (model_id, participation["key"], participation["doc_version"],
         participation["document_sha256"], profile["document_sha256"],
         participation["occurred_at"]),
    )
    row = await cur.fetchone()
    return row["id"] if row else None


async def close_open_credentials(cur, model_ids: list[str]) -> int:
    """열린 증서를 revoked 로 닫고, 발급된 VC 는 폐기 큐에 넣는다. 닫은 개수를 돌려준다."""
    if not model_ids:
        return 0
    await cur.execute(
        """update fm_sponsorship_credentials
              set status = 'revoked', revoked_at = now()
            where model_id = any(%s::uuid[]) and status in ('pending', 'active')
        returning id::text as id, model_id::text as model_id, vc_id""",
        (list(model_ids),),
    )
    closed = await cur.fetchall()
    for row in closed:
        if row.get("vc_id"):
            await enqueue_sponsorship_revocation(cur, model_id=row["model_id"], vc_id=row["vc_id"])
    return len(closed)


async def enqueue_sponsorship_revocation(cur, *, model_id: str, vc_id: str) -> None:
    await cur.execute(
        """insert into fm_vc_revocation_jobs (kind, license_id, model_id, vc_id)
           values ('sponsorship', null, %s, %s)
           on conflict (vc_id) do nothing""",
        (model_id, vc_id),
    )


# ---------------------------------------------------------------------------
# 읽기 — 모델 화면·셀러 게이트·공개 검증·관리자.
# ---------------------------------------------------------------------------


async def owner_status(conn, model_id: str) -> dict:
    """모델 본인 화면용 상태. status: none | waiting_license | pending | active."""
    async with conn.cursor() as cur:
        await cur.execute(
            """select c.status, c.vc_id, c.issued_at, m.did
                 from fm_sponsorship_credentials c
                 join fm_models m on m.id = c.model_id
                where c.model_id = %s and c.status in ('pending', 'active')
                limit 1""",
            (model_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return {"status": "none", "vc_id": None, "issued_at": None}
    status = row["status"]
    if status == "pending" and not (row.get("did") or "").strip():
        status = "waiting_license"
    return {"status": status, "vc_id": row["vc_id"], "issued_at": row["issued_at"]}


#: 카탈로그·셀러 게이트 SQL 에 끼우는 조건 — 모델 별칭 `m` 기준. 증서가 active 인 모델만 협찬 노출.
ACTIVE_CREDENTIAL_SQL = (
    "exists (select 1 from fm_sponsorship_credentials sc "
    "where sc.model_id = m.id and sc.status = 'active')"
)

_KST = timezone(timedelta(hours=9))


def public_sponsorship(row: Mapping, *, license_valid: bool) -> dict | None:
    """공개 검증의 협찬 블록 — **동의 사실만**. 라이선스가 무효면(철회·만료) 협찬도 싣지 않는다.

    row 는 PUBLIC_VERIFY_SQL 의 sponsorship_* 칸. 동의 시각은 날짜(KST)로만 — 공개 화면에
    초 단위 행동 시각을 남길 이유가 없다.
    """
    if not license_valid or not row.get("sponsorship_vc_id"):
        return None
    consented = row.get("sponsorship_consented_at")
    if isinstance(consented, datetime):
        consented = (consented if consented.tzinfo else consented.replace(tzinfo=timezone.utc))
        consented = consented.astimezone(_KST).date()
    return {
        "active": True,
        "vc_id": row["sponsorship_vc_id"],
        "consented_on": consented,
        "consent_doc_version": row.get("sponsorship_consent_doc_version"),
    }


async def admin_view(conn, model_id: str) -> dict | None:
    """관리자 모델 상세 — 가장 최근 증서 하나(닫힌 것 포함)."""
    async with conn.cursor() as cur:
        await cur.execute(
            """select c.status, c.vc_id, c.issued_at, c.attempts, c.last_error_code, m.did
                 from fm_sponsorship_credentials c
                 join fm_models m on m.id = c.model_id
                where c.model_id = %s
                order by c.created_at desc limit 1""",
            (model_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    status = row["status"]
    if status == "pending" and not (row.get("did") or "").strip():
        status = "waiting_license"
    return {
        "status": status, "vc_id": row["vc_id"], "issued_at": row["issued_at"],
        "attempts": row["attempts"], "last_error_code": row["last_error_code"],
    }


# ---------------------------------------------------------------------------
# 발급 — 워커가 부른다.
# ---------------------------------------------------------------------------


def _utc_z(value: datetime) -> str:
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_sponsorship_vc_claims(*, model_did: str, row: Mapping) -> dict:
    """증서 행에서 결정적으로 만든다 — 재시도해도 같은 클레임이라 holder 멱등키가 성립한다."""
    if not isinstance(model_did, str) or not model_did.strip():
        raise ValueError("model DID is required")
    return {
        "modelDid": model_did,
        "credentialId": str(uuid.UUID(str(row["id"]))),
        "consentDocVersion": row["consent_doc_version"],
        "participationDocSha256": row["participation_doc_sha256"],
        "profileDocSha256": row["profile_doc_sha256"],
        "consentedAt": _utc_z(row["consented_at"]),
    }


async def _holder_post(client, settings, path, payload, accepted):
    response = await holder_client.post(
        client,
        base_url=settings.opendid_holder_url,
        secret=settings.opendid_holder_hmac_secret,
        path=path,
        payload=payload,
    )
    if response.status_code not in accepted:
        raise SponsorshipVcIssueError("http_status")
    return response


async def request_holder_issue(settings, row: Mapping) -> str:
    """holder 에 발급을 요청하고 vc_id 를 돌려준다. 라이선스 발급(issue_face_vc)과 같은 3단계."""
    base = getattr(settings, "opendid_holder_url", None)
    secret = getattr(settings, "opendid_holder_hmac_secret", None)
    if not base or not base.strip() or not secret or not secret.strip():
        raise SponsorshipVcIssueError("holder_unavailable")
    model_id = row["model_id"]
    try:
        async with httpx.AsyncClient(timeout=_HOLDER_TIMEOUT) as client:
            await _holder_post(client, settings, f"/holder/models/{model_id}/wallet", {}, {201, 409})
            register = await _holder_post(
                client, settings, f"/holder/models/{model_id}/register-did", {}, {200})
            body = register.json()
            user_did = body.get("userDid") if isinstance(body, Mapping) else None
            if not isinstance(user_did, str) or not user_did.strip():
                raise SponsorshipVcIssueError("invalid_body")
            issue = await _holder_post(
                client, settings, f"/holder/models/{model_id}/issue-vc",
                {
                    "plan": SPONSORSHIP_VC_PLAN,
                    "idempotencyKey": f"fm-sponsorship:{row['id']}",
                    "claims": build_sponsorship_vc_claims(model_did=user_did, row=row),
                },
                {200},
            )
            issue_body = issue.json()
    except SponsorshipVcIssueError:
        raise
    except httpx.TransportError as exc:
        raise SponsorshipVcIssueError("transport") from exc
    except Exception as exc:
        raise SponsorshipVcIssueError("invalid_body") from exc
    vc_id = issue_body.get("vcId") if isinstance(issue_body, Mapping) else None
    if not isinstance(vc_id, str) or not vc_id.strip():
        raise SponsorshipVcIssueError("invalid_body")
    return vc_id.strip()


async def backfill_missing(pool, *, limit: int = 20) -> int:
    """협찬은 켜져 있는데 열린 증서가 없는 모델에 pending 증서를 만든다.

    증서는 off→on **전환**에서만 생긴다. 그래서 스위치(FM_SPONSORSHIP_VC)를 켜기 전에 — 또는
    off 로 롤백해 둔 사이에 — 협찬을 켠 모델은 증서가 없고, 스위치를 켜는 순간 셀러에게서
    조용히 숨겨진다. 워커가 스윕마다 그 틈을 메운다. 동의 증빙은 그 모델의 최신 granted
    이벤트를 그대로 쓴다(open_credential 과 같은 경로).
    """
    opened = 0
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """select m.id::text as model_id, m.user_id::text as user_id
                     from fm_models m
                    where m.sponsorship_enabled and m.user_id is not null
                      and not exists (select 1 from fm_sponsorship_credentials c
                                       where c.model_id = m.id
                                         and c.status in ('pending', 'active'))
                    order by m.id
                    limit %s""",
                (limit,),
            )
            models = await cur.fetchall()
            for model in models:
                try:
                    if await open_credential(cur, **model):
                        opened += 1
                except RuntimeError:
                    # 동의 기록이 없는 켜짐(동의 기록 도입 전 데이터) — 증서를 지어낼 근거가 없다.
                    log.warning("sponsorship on without consent events: model=%s", model["model_id"])
        await conn.commit()
    return opened


async def claim_pending(pool, *, limit: int = 10) -> list[dict]:
    """발급할 pending 행을 잡는다. 모델 DID 가 아직 없으면(라이선스 VC 전) 건너뛴다."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""with candidate as (
                       select c.id from fm_sponsorship_credentials c
                         join fm_models m on m.id = c.model_id
                        where c.status = 'pending' and c.next_attempt_at <= now()
                          and c.attempts < %s
                          and nullif(btrim(m.did), '') is not null
                        order by c.next_attempt_at
                        for update of c skip locked
                        limit %s
                   )
                   update fm_sponsorship_credentials c
                      set next_attempt_at = now() + interval '{CLAIM_LEASE_SECONDS} seconds'
                     from candidate
                    where c.id = candidate.id
                returning c.id::text as id, c.model_id::text as model_id,
                          c.consent_doc_version, c.participation_doc_sha256,
                          c.profile_doc_sha256, c.consented_at, c.attempts""",
                (MAX_ATTEMPTS, limit),
            )
            rows = await cur.fetchall()
        await conn.commit()
    return rows


async def record_issued(pool, row: Mapping, vc_id: str) -> bool:
    """pending 일 때만 active 로. 그새 revoked 됐으면 받은 VC 를 바로 폐기 큐에 넣고 False."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """update fm_sponsorship_credentials
                      set status = 'active', vc_id = %s, issued_at = now(),
                          last_error_code = null
                    where id = %s and status = 'pending'""",
                (vc_id, row["id"]),
            )
            activated = cur.rowcount == 1
            if not activated:
                # pending 이 아니었다 — 두 경우뿐이다.
                #  · 다른 인스턴스가 같은 멱등키로 먼저 active 로 만들었다(같은 vc_id) → 할 일 없음.
                #    여기서 폐기하면 DB 는 유효인데 holder·체인은 폐기된 증서가 된다.
                #  · 발급 도중 협찬이 꺼져 revoked 가 됐다 → 방금 받은 VC 를 폐기 큐로.
                await cur.execute(
                    """update fm_sponsorship_credentials set vc_id = coalesce(vc_id, %s)
                        where id = %s and status = 'revoked'
                    returning status""",
                    (vc_id, row["id"]),
                )
                if await cur.fetchone() is not None:
                    await enqueue_sponsorship_revocation(
                        cur, model_id=row["model_id"], vc_id=vc_id)
        await conn.commit()
    return activated


async def record_failure(pool, row: Mapping, code: str) -> None:
    attempts = int(row.get("attempts") or 0) + 1
    if attempts >= ALERT_ATTEMPTS:
        log.warning(
            "sponsorship VC issue failing: credential=%s model=%s attempts=%s code=%s",
            row["id"], row["model_id"], attempts, code,
        )
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """update fm_sponsorship_credentials
                      set attempts = attempts + 1, last_error_code = %s,
                          next_attempt_at = now() + make_interval(secs => least(
                              300, power(2, least(attempts + 1, 9)))::double precision)
                    where id = %s and status = 'pending'""",
                (code, row["id"]),
            )
        await conn.commit()


async def issue_one(app, row: Mapping) -> bool:
    try:
        vc_id = await request_holder_issue(app.state.settings, row)
    except SponsorshipVcIssueError as error:
        await record_failure(app.state.pool, row, error.code)
        return False
    return await record_issued(app.state.pool, row, vc_id)
