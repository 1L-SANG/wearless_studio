# FaceMarket 생체등록 신분증-먼저 재배치 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 생체등록에서 본인확인(CI)을 맨 앞 fail-fast 게이트로 옮기고(신규 `identity_pending` 상태 + `POST /enrollments/{id}/identity`), 얼굴매칭(SFace)은 끝에 남긴 채, 얼굴 3장에 포즈 예시 일러스트를, 사진 뒤에 비게이팅 대표이미지 업로드를 추가한다.

**Architecture:** 현재 `process_enrollment_completion` 안에서 라이브니스 직후·매칭 직전에 실행되는 CI 검증(fetch_trans→parse_oacx_biometric_evidence)을 신규 앞단 엔드포인트로 분리한다. CI 증거(ci_hash·name_masked·birth_year·tx_digest·contract_version)는 enrollment 행 신규 nullable 컬럼에 저장하고, 매칭 단계는 그 저장값을 읽어 모델을 바인딩한다(재검증 없음, 토큰 만료 무관). 초상(dlphotoimage)은 현행대로 클라 릴레이·메모리 보관·매칭때 제출. 대표이미지는 enrollment 행에 R2 키로 저장 후 바인딩때 `fm_models.cover_image_url`로 승격.

**Tech Stack:** FastAPI + psycopg async(`server/app/facemarket_enrollment.py`, `cx_identity.py`), Postgres/Supabase PG16 migrations, React + Vite(`src/features/model/*`, `src/lib/api/facemarket.js`), pytest(FakeCursor SQL-dispatch 하네스), node:test(modelComponentHarness).

**Spec:** `docs/superpowers/specs/2026-08-24-facemarket-enrollment-identity-first-reorder-design.md`

## Global Constraints

- dev 전용 우회 금지 — 모든 게이트 전 환경 동일 fail-closed. QA=배포될 코드.
- 생체 바이트·초상·임베딩·랜드마크·파일명 저장·로그 금지(§1.4). 관측 = 상태 enum·사유코드·provider 만. 원시 CI(`evidence.ci`)는 HMAC(`ci_hash`)만 장기저장, raw 는 `wipe_bytearray`로 즉시 폐기.
- 모델은 얼굴매칭 성공 후에만 바인딩(중간 실패시 `fm_models` 행 미생성).
- CI/이름/생년은 서버 `trans/{token}` 서버발 조회로만 확보(클라 직접 입력 금지).
- 마이그레이션은 **신규 additive 파일**로만(기존 파일 수정 금지). CHECK 제약은 `alter table drop constraint if exists ... ; add constraint ...` idempotent idiom.
- 생체등록은 prod OFF(`FM_BIOMETRIC_ENROLLMENT_ENABLED=false`) — dark-launch 상태 변경.

## 확정 설계 결정 (구현 중 재논의 불필요)

1. **CI 증거 저장 = enrollment 행 신규 nullable 컬럼** (`identity_ci_hash`, `identity_name_masked`, `identity_birth_year`, `identity_tx_digest`, `identity_contract_version`). 조기 `fm_identity_verifications` 행 아님.
2. **replay 창 닫기**: `/identity`가 enrollment 행에 `identity_tx_digest` 기록 + 기존 pre-check 이 이 digest 를 조회. durable `fm_identity_verifications` insert 는 바인딩 단계 유지.
3. **대표이미지 = `fm_biometric_enrollments.profile_image_r2_key`** → 바인딩때 `fm_models.cover_image_url`로 승격(매칭 전엔 모델 행 없음).
4. **신규 경로 = `POST /v1/facemarket/enrollments/{id}/identity`** (enrollment-scoped). 기존 비-scoped `/v1/facemarket/identity/verify`(레거시)는 건드리지 않는다.
5. **`completeEnrollment` 시그니처: token 제거**, `{sessionId, idPhotoHex}`만.
6. **`create_enrollment` 기본 시작 상태 = `identity_pending`** (테이블 DEFAULT 변경).
7. **테스트 ripple 완화**: 파이썬 `create_enrollment` 헬퍼가 기본으로 `/identity`까지 호출해 `photos_pending` 반환(기존 ~35 테스트 보존). 게이트 전용 테스트는 `verify_identity=False`로 raw-create.

## File Structure

- `supabase/migrations/20260824000000_facemarket_identity_first_reorder.sql` — 신규: status enum + partial index 확장, CI-evidence/profile-image 컬럼, DEFAULT 변경.
- `server/app/facemarket_enrollment.py` — 신규 `/identity` 엔드포인트·`verify_enrollment_identity` 서비스, `process_enrollment_completion` 축소(CI 분리·저장값 읽기), `CompleteEnrollmentBody` token 제거, 상태 리터럴 4곳 갱신, `/profile-image` 엔드포인트, 바인딩때 cover_image 승격.
- `server/app/cx_identity.py` — 변경 없음(기존 헬퍼 재사용). 읽기만.
- `server/tests/test_facemarket_biometric_enrollment.py` — FakeCursor 신규 SQL branch, `ACTIVE_STATUSES` 갱신, 헬퍼(`verify_identity`, `create_enrollment` 확장), 신규 테스트.
- `server/tests/test_facemarket_identity_first_migration.py` — 신규 마이그 정적/라이브 테스트.
- `src/features/model/biometricEnrollment.js` — `ENROLLMENT_STEPS` 재정렬, `nextEnrollmentStep` identity 분기, `ENROLLMENT_ANGLES` exampleImage.
- `src/lib/api/facemarket.js` — `createIdentity`, `uploadProfileImage`, `completeEnrollment` token 제거.
- `src/features/model/ModelRegister.jsx` — 플로우 재배치(OACX 앞단·profile 단계·complete 토큰 제거·초상 유실 재인증).
- `src/features/model/ModelFaceUpload.jsx` — 슬롯 예시 이미지.
- `src/features/model/assets/pose-*.svg` (신규 3개) — 포즈 일러스트.
- `tests/frontend/facemarket-biometric-enrollment.test.mjs` — 스텝/상태/컴포넌트 어서션.

---

## Task 1: 마이그레이션 — identity_pending 상태 + CI-evidence/profile 컬럼

**Files:**
- Create: `supabase/migrations/20260824000000_facemarket_identity_first_reorder.sql`
- Create test: `server/tests/test_facemarket_identity_first_migration.py`

**Interfaces:**
- Produces: `fm_biometric_enrollments` 에 status `'identity_pending'` 허용 + 컬럼 `identity_ci_hash text`, `identity_name_masked text`, `identity_birth_year text`, `identity_tx_digest text`, `identity_contract_version text`, `profile_image_r2_key text` (전부 nullable). 컬럼 DEFAULT status = `'identity_pending'`. 이후 태스크가 이 컬럼/상태에 의존.

- [ ] **Step 1: 마이그레이션 정적 테스트 작성(항상 실행)**

`server/tests/test_facemarket_identity_first_migration.py`:
```python
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260824000000_facemarket_identity_first_reorder.sql"
)


def _sql():
    return " ".join(MIGRATION.read_text().split()).lower()


def test_status_check_includes_identity_pending():
    sql = _sql()
    assert "identity_pending" in sql
    # drop/re-add idempotent idiom
    assert "drop constraint if exists" in sql
    # active partial index widened
    assert "fm_biometric_active_per_user" in sql


def test_adds_identity_evidence_and_profile_columns():
    sql = _sql()
    for col in (
        "identity_ci_hash", "identity_name_masked", "identity_birth_year",
        "identity_tx_digest", "identity_contract_version", "profile_image_r2_key",
    ):
        assert col in sql, col


def test_default_status_is_identity_pending():
    sql = _sql()
    assert "default 'identity_pending'" in sql


def test_no_raw_biometric_columns():
    sql = _sql()
    for forbidden in ("portrait", "embedding", "dlphotoimage", "raw_ci"):
        assert forbidden not in sql, forbidden
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `cd server && .venv/bin/python -m pytest tests/test_facemarket_identity_first_migration.py -q`
Expected: FAIL — 마이그 파일 없음(FileNotFoundError).

- [ ] **Step 3: 마이그레이션 작성**

`supabase/migrations/20260824000000_facemarket_identity_first_reorder.sql`:
```sql
-- FaceMarket 생체등록 신분증-먼저 재배치: identity_pending 상태 + CI 증거/대표이미지 컬럼.
-- Additive·PG16-safe. 상태 enum 은 기존 fm_models_status_check 패턴(drop if exists→add)으로 확장.

-- 1) status CHECK 제약에 identity_pending 추가.
alter table public.fm_biometric_enrollments
  drop constraint if exists fm_biometric_enrollments_status_check;
alter table public.fm_biometric_enrollments
  add constraint fm_biometric_enrollments_status_check
  check (status in (
    'identity_pending', 'photos_pending', 'liveness_pending', 'processing',
    'asset_building', 'license_pending', 'vc_pending', 'passed', 'failed',
    'cancelled', 'expired'
  ));

-- 2) 신규 등록은 identity_pending 부터 시작.
alter table public.fm_biometric_enrollments
  alter column status set default 'identity_pending';

-- 3) "유저당 활성 등록 1개" partial unique index 에 identity_pending 포함(재생성).
drop index if exists public.fm_biometric_active_per_user;
create unique index if not exists fm_biometric_active_per_user
  on public.fm_biometric_enrollments(user_id)
  where status in ('identity_pending', 'photos_pending', 'liveness_pending',
                   'processing', 'asset_building', 'license_pending', 'vc_pending');

-- 4) CI 증거(원시 CI 아님 — HMAC ci_hash 만) + 대표이미지 R2 키 컬럼.
alter table public.fm_biometric_enrollments
  add column if not exists identity_ci_hash text,
  add column if not exists identity_name_masked text,
  add column if not exists identity_birth_year text,
  add column if not exists identity_tx_digest text,
  add column if not exists identity_contract_version text,
  add column if not exists profile_image_r2_key text;
```
주의: 실제 제약명(`fm_biometric_enrollments_status_check`)은 원 마이그(`20260821010100`)의 인라인 CHECK 자동생성명과 일치해야 함. 구현자는 실행 전 `\d+ fm_biometric_enrollments` 또는 원 마이그의 제약 표기를 확인해 `drop constraint if exists` 대상명을 맞춘다(불일치해도 `if exists`라 무해, 단 재확인).

- [ ] **Step 4: 정적 테스트 통과 확인**

Run: `cd server && .venv/bin/python -m pytest tests/test_facemarket_identity_first_migration.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: (선택) 라이브 DB 테스트 추가 — skip-gated**

`test_facemarket_identity_first_migration.py` 하단에 추가:
```python
import os
import pytest

TEST_DATABASE_URL = os.getenv("FACEMARKET_TEST_DATABASE_URL")
requires_database = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="FACEMARKET_TEST_DATABASE_URL is not configured"
)


@requires_database
@pytest.mark.asyncio
async def test_identity_pending_status_accepted_on_real_pg():
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    base = (Path(__file__).resolve().parents[2]
            / "supabase/migrations/20260821010100_facemarket_biometric_runtime.sql")
    conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
    try:
        async with conn.transaction(force_rollback=True):
            await conn.execute(base.read_text())
            await conn.execute(MIGRATION.read_text())
            # identity_pending 이 CHECK 를 통과해 insert 되는지
            await conn.execute(
                "insert into fm_biometric_enrollments (user_id, device_digest, "
                "consent_version, expires_at) values (gen_random_uuid(), 'd', 'v', now())"
            )
    finally:
        await conn.close()
```
Run: `cd server && .venv/bin/python -m pytest tests/test_facemarket_identity_first_migration.py -q` (라이브 테스트는 env 없으면 skip).

- [ ] **Step 6: 커밋**

```bash
git add supabase/migrations/20260824000000_facemarket_identity_first_reorder.sql server/tests/test_facemarket_identity_first_migration.py
git commit -m "feat(facemarket): migration for identity-first enrollment (identity_pending + evidence cols)"
```

---

## Task 2: 백엔드 — POST /enrollments/{id}/identity (CI fail-fast 게이트)

**Files:**
- Modify: `server/app/facemarket_enrollment.py` (신규 body model·라우트·서비스; 상태 리터럴 4곳; create_enrollment DEFAULT 의존)
- Test: `server/tests/test_facemarket_biometric_enrollment.py` (FakeCursor branch, ACTIVE_STATUSES, 헬퍼, 신규 테스트)

**Interfaces:**
- Consumes: `cx_identity.get_oacx_biometric_contract(settings) -> OacxBiometricContract`; `cx_identity.fetch_trans(base_url, token) -> dict`; `cx_identity.parse_oacx_biometric_evidence(trans, *, contract) -> OacxBiometricEvidence{ci: bytearray, birth: str, name_masked: str, transaction_id, contract_version}` (미성년→`OacxBiometricError("minor_blocked")`); `cx_identity.wipe_bytearray`. Task 1 컬럼.
- Produces: `POST /v1/facemarket/enrollments/{id}/identity` body `{token}` → enrollment `identity_pending → photos_pending`, evidence 컬럼 채움. 반환 `EnrollmentView`. Task 3(complete)가 저장된 evidence 컬럼을 읽음. Task 5(프론트 `createIdentity`)가 이 라우트 호출.

- [ ] **Step 1: 실패 테스트 작성 — 게이트 동작**

`test_facemarket_biometric_enrollment.py`에 추가(헬퍼 근처). 먼저 신규 헬퍼:
```python
IDENTITY_TOKEN = "oacx-identity-token-1"


def verify_identity(client, auth, enrollment_id, *, token=IDENTITY_TOKEN):
    return client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/identity",
        json={"token": token}, headers=auth(),
    )
```
신규 테스트:
```python
def test_identity_verify_advances_to_photos_pending(enrollment_client, auth, enrollment_store, completion_fakes):
    eid = create_enrollment(enrollment_client, auth, verify_identity=False)
    assert enrollment_store.enrollments[0]["status"] == "identity_pending"
    res = verify_identity(enrollment_client, auth, eid)
    assert res.status_code == 200, res.text
    assert enrollment_store.enrollments[0]["status"] == "photos_pending"
    # ci_hash 저장, 원시 CI 미저장
    row = enrollment_store.enrollments[0]
    assert row["identity_ci_hash"] and "dev-ci-value" not in enrollment_store.serialized()


def test_identity_verify_blocks_minor(enrollment_client, auth, enrollment_store, monkeypatch):
    async def minor_trans(*_a, **_k):
        return {"ci": "dev-ci-value", "birth": "20200101", "nm": "홍길동", "txId": "tx-m"}
    monkeypatch.setattr(facemarket_enrollment.cx_identity, "fetch_trans", minor_trans)
    eid = create_enrollment(enrollment_client, auth, verify_identity=False)
    res = verify_identity(enrollment_client, auth, eid)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "minor_blocked"
    assert enrollment_store.enrollments[0]["status"] == "identity_pending"


def test_photos_require_identity_first(enrollment_client, auth, enrollment_store, monkeypatch):
    # QC 를 통과시켜, 거절이 QC 가 아니라 상태 게이트(identity_pending)에서 나옴을 확인한다.
    # (upload_enrollment_photo 는 QC 를 먼저 돌리고 그 뒤 _validate_photo_mutation_enrollment 로 상태를 본다.)
    stub_qc(monkeypatch)
    eid = create_enrollment(enrollment_client, auth, verify_identity=False)
    res = enrollment_client.post(
        f"/v1/facemarket/enrollments/{eid}/photos",
        data={"angle": "front"},
        files={"photo": ("front.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "invalid_enrollment_state"
```
그리고 기존 `create_enrollment` 헬퍼를 확장:
```python
def create_enrollment(client, auth, *, device_id=DEVICE_ID, verify_identity=True):
    response = client.post("/v1/facemarket/enrollments", json={
        "deviceId": device_id,
        "biometricConsent": {"accepted": True, "documentVersion": "2026-08-v1"},
    }, headers=auth())
    assert response.status_code == 201, response.text
    eid = response.json()["id"]
    if verify_identity:
        r = client.post(f"/v1/facemarket/enrollments/{eid}/identity",
                        json={"token": IDENTITY_TOKEN}, headers=auth())
        assert r.status_code == 200, r.text
    return eid
```
주의: `verify_identity=True` 경로가 `cx_identity.fetch_trans` 스텁을 필요로 함 → 기존 `completion_fakes` fixture 가 fetch_trans 를 스텁하므로, 사진/complete 를 도는 테스트들은 이미 completion_fakes 를 받거나, `create_ready_enrollment`/`create_complete_ready_enrollment`가 store 직접변이로 우회. `create_ready_enrollment`는 store 를 직접 `photos_pending`으로 만들므로 /identity HTTP 를 안 타도 됨 — 단 evidence 컬럼도 함께 채워 넣도록 갱신(아래 Step 3의 헬퍼 갱신 참조).

- [ ] **Step 2: 실행 → 실패 확인**

Run: `cd server && .venv/bin/python -m pytest tests/test_facemarket_biometric_enrollment.py -k "identity_verify or require_identity" -q`
Expected: FAIL — `/identity` 라우트 없음(404) + FakeCursor `unexpected SQL`.

- [ ] **Step 3: 구현 — body model·라우트·서비스**

`facemarket_enrollment.py`:

(a) body model (`CompleteEnrollmentBody` 근처, ~178 뒤):
```python
class IdentityVerifyBody(CamelModel):
    token: str
```

(b) 서비스 + 라우트 (`create_enrollment` 뒤, upload 앞 적당 위치):
```python
@router.post("/enrollments/{enrollment_id}/identity", response_model=EnrollmentView)
async def verify_enrollment_identity(
    request: Request,
    enrollment_id: str,
    body: IdentityVerifyBody,
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    token = (body.token or "").strip()
    if not token:
        raise _err("token_required", "인증 토큰이 없습니다.")
    settings: Settings = request.app.state.settings
    token_digest = f"cxsha256:{hashlib.sha256(token.encode()).hexdigest()}"
    contract = cx_identity.get_oacx_biometric_contract(settings)
    try:
        trans = await cx_identity.fetch_trans(settings.cx_trans_base_url, token)
        evidence = cx_identity.parse_oacx_biometric_evidence(trans, contract=contract)
    except cx_identity.OacxBiometricError as exc:
        raise _err(exc.reason, enrollment_reason_copy(exc.reason))
    except cx_identity.CxIdentityError:
        raise _err("id_portrait_unavailable", "신분증 확인에 실패했어요. 다시 시도해 주세요.")
    try:
        ci_hash = hmac.new(
            settings.fm_ci_pepper.encode(), evidence.ci, hashlib.sha256
        ).hexdigest()
    finally:
        cx_identity.wipe_bytearray(evidence.ci)
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        await _reject_cutover_closed(conn)
        async with conn.cursor() as cur:
            # 소유·상태 검사(identity_pending 만 허용)
            await cur.execute(
                "select status from fm_biometric_enrollments "
                "where id = %s and user_id = %s for update",
                (enrollment_id, user_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
            if row["status"] != "identity_pending":
                raise _err("invalid_enrollment_state",
                           "이미 본인확인이 완료됐거나 진행할 수 없는 상태입니다.", status=409)
            # replay(토큰 재사용) 차단
            await cur.execute(
                """
                select exists(
                  select 1 from fm_identity_verifications
                  where cx_tx_id = %s and cx_tx_id_format = 'sha256-v1'
                  union all
                  select 1 from fm_biometric_enrollments where identity_tx_digest = %s
                ) as replayed
                """,
                (token_digest, token_digest),
            )
            if (await cur.fetchone())["replayed"]:
                await conn.commit()
                raise _err("identity_replay", "이미 사용된 인증입니다. 새로 시작해 주세요.")
            # 교차유저 CI 충돌(다른 유저 모델이면 소유권 확인 필요)
            await cur.execute(
                "select user_id::text as user_id from fm_models where ci_hash = %s",
                (ci_hash,),
            )
            owner = await cur.fetchone()
            if owner is not None and owner["user_id"] != user_id:
                raise _err("identity_recovery_required", "기존 모델 소유권 확인이 필요해요.")
            # 증거 저장 + 상태 전이
            await cur.execute(
                """
                update fm_biometric_enrollments
                set status = 'photos_pending',
                    identity_ci_hash = %s, identity_name_masked = %s,
                    identity_birth_year = %s, identity_tx_digest = %s,
                    identity_contract_version = %s
                where id = %s and user_id = %s and status = 'identity_pending'
                """,
                (ci_hash, evidence.name_masked, evidence.birth[:4], token_digest,
                 evidence.contract_version, enrollment_id, user_id),
            )
        await conn.commit()
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        return await _enrollment_view(conn, row)
```
`enrollment_reason_copy(reason)` — 기존 사유→카피 매핑이 없으면 최소 헬퍼 추가(또는 `_err(exc.reason, "본인확인에 실패했어요.")`로 단순화). `_reject_cutover_closed`는 기존 사용 패턴(upload_enrollment_photo 참조) 따름. `hashlib`/`hmac` import 존재 확인(파일 상단, 이미 사용 중).

(c) 상태 리터럴 4곳에 `identity_pending` 추가(recon 확인 위치):
- `_load_current_enrollment` (~596-599) `status in (...)` 목록.
- `create_enrollment` on-conflict (~708-711) 및 fallback re-select (~723-726).

(d) 테이블 DEFAULT 가 이제 `identity_pending`(Task1) 이므로 `create_enrollment` 는 코드 변경 없이 identity_pending 으로 시작.

- [ ] **Step 4: FakeCursor branch + ACTIVE_STATUSES 갱신 + 헬퍼**

`test_facemarket_biometric_enrollment.py`:
- `ACTIVE_STATUSES`(32-39)에 `"identity_pending"` 추가.
- `FakeCursor.execute`에 신규 SQL 대응 branch 추가: (i) `select status from fm_biometric_enrollments where id = %s and user_id = %s for update` — enrollment row status 반환; (ii) replay `select exists(...) as replayed` — `store.identities`(cx_tx_id) + `store.enrollments`(identity_tx_digest) 조회해 bool; (iii) `select user_id ... from fm_models where ci_hash = %s`; (iv) `update fm_biometric_enrollments set status='photos_pending', identity_ci_hash=...` — 해당 enrollment dict 필드 세팅. 각 branch 는 기존 dispatch 스타일(소문자·공백정규화 startswith/substring) 따름.
- `create_ready_enrollment`/`create_complete_ready_enrollment`가 store 를 직접 photos_pending 로 세팅할 때 `identity_ci_hash`/`identity_name_masked`/`identity_birth_year`/`identity_tx_digest`/`identity_contract_version` 도 더미값으로 채워 넣도록 갱신(Task3의 complete 가 이 컬럼을 읽으므로).
- `completion_fakes` fixture 의 `fetch_trans` 스텁이 `IDENTITY_TOKEN`에 대해 `dev_trans()` 반환하도록(기존 magic-token 분기에 정상 토큰 경로 확인/추가).

- [ ] **Step 5: 실행 → 통과 확인**

Run: `cd server && .venv/bin/python -m pytest tests/test_facemarket_biometric_enrollment.py -q`
Expected: 신규 identity 테스트 PASS + 기존 스위트 그린(create_enrollment 헬퍼 확장으로 photos/complete 테스트 보존). 실패시 FakeCursor branch/헬퍼 evidence 채움 보강.

- [ ] **Step 6: 커밋**

```bash
git add server/app/facemarket_enrollment.py server/tests/test_facemarket_biometric_enrollment.py
git commit -m "feat(facemarket): front-loaded identity CI gate (POST /enrollments/{id}/identity)"
```

---

## Task 3: 백엔드 — complete_enrollment 매칭만(저장된 CI 읽기), token 제거

**Files:**
- Modify: `server/app/facemarket_enrollment.py` (`CompleteEnrollmentBody`, `complete_enrollment`, `process_enrollment_completion`, `_initial_completion_checks`)
- Test: `server/tests/test_facemarket_biometric_enrollment.py`

**Interfaces:**
- Consumes: Task1 컬럼(`identity_ci_hash` 등), Task2 가 채운 evidence.
- Produces: `POST /enrollments/{id}/complete` body `{sessionId, idPhotoHex}` (token 없음). 매칭 통과시 저장된 evidence 로 모델 바인딩.

- [ ] **Step 1: 실패 테스트 — token 없이 complete 성공**

`complete_enrollment` 헬퍼에서 token 제거:
```python
def complete_enrollment(client, auth, enrollment_id, session_id, id_photo_hex=PORTRAIT_HEX):
    body = {"sessionId": session_id}
    if id_photo_hex is not None:
        body["idPhotoHex"] = id_photo_hex
    return client.post(f"/v1/facemarket/enrollments/{enrollment_id}/complete",
                       json=body, headers=auth())
```
신규 어서션(기존 `test_complete_uses_distinct_thresholds_...` 흐름 재사용):
```python
def test_complete_binds_using_stored_identity_without_token(
    enrollment_client, auth, enrollment_store, fake_r2, fake_rekognition, completion_fakes
):
    eid = create_complete_ready_enrollment(enrollment_client, auth, enrollment_store,
                                           fake_r2, fake_rekognition)
    res = complete_enrollment(enrollment_client, auth, eid, fake_rekognition.session_id)
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["passed"] is True and body["status"] == "asset_building"
    # 바인딩된 모델 display_name 이 저장된 name_masked 에서 옴
    assert enrollment_store.models[0]["display_name"]
```

- [ ] **Step 2: 실행 → 실패 확인**

Run: `cd server && .venv/bin/python -m pytest tests/test_facemarket_biometric_enrollment.py -k "without_token or distinct_thresholds" -q`
Expected: FAIL — complete 가 아직 token 요구(`token_required`) 또는 fetch_trans 재호출.

- [ ] **Step 3: 구현 — complete 축소**

`facemarket_enrollment.py`:
- `CompleteEnrollmentBody`(172-178): `token: str` 필드 제거. → `{session_id, id_photo_hex}`.
- `complete_enrollment`(1888-1925): `token = (body.token...)` 검증 삭제; `process_enrollment_completion(...)` 호출에서 `token=token` 제거.
- `process_enrollment_completion`(1657-): 시그니처에서 `token` 제거. CI-verify 블록(1691-1695)에서 `fetch_trans`/`parse_oacx_biometric_evidence` **삭제**, `contract = get_oacx_biometric_contract(settings)` + `portrait = parse_oacx_portrait_hex(id_photo_hex, contract=contract)`(1699) 유지. 매칭 블록(1714-1731) 그대로.
- 바인딩 블록(1733-1849): `evidence.ci`/`evidence.name_masked`/`evidence.birth[:4]`/`evidence.contract_version`/`token_digest` 참조를 **enrollment 행 저장 컬럼 읽기**로 교체 — 처리 시작부에서 row 로드시 `identity_ci_hash, identity_name_masked, identity_birth_year, identity_tx_digest, identity_contract_version` 를 함께 select 해 로컬 변수로. `ci_hash = identity_ci_hash`(재계산 안 함), `display_name = identity_name_masked`, `fields.birthYear = identity_birth_year`, `oacx_tx_digest = identity_tx_digest`, `provider_versions.oacx = identity_contract_version`. `evidence` 지역변수/그 wipe 는 제거(원시 CI 는 Task2 에서 이미 폐기).
- `_initial_completion_checks`(1571-1654): CI-replay pre-check(1616-1630)는 이제 Task2 에서 처리됨 → 제거하거나, `identity_tx_digest is not null` 확인만 남김(방어). photo-completeness·expiry·liveness-digest·`liveness_pending→processing` 전이는 그대로.
- `_load_owned_enrollment` select 목록에 신규 identity 컬럼 추가(바인딩이 읽도록).

- [ ] **Step 4: FakeCursor/뷰 갱신 + 실행**

`_load_owned_enrollment`류 select 가 신규 컬럼 반환하도록 FakeCursor 대응 branch/뷰(`_enrollment_db_view` 1047) 갱신. `create_complete_ready_enrollment`가 evidence 컬럼을 채우므로(Task2 Step4) 바인딩이 읽을 값 존재.
Run: `cd server && .venv/bin/python -m pytest tests/test_facemarket_biometric_enrollment.py -q`
Expected: 전체 PASS(기존 completion 테스트가 token 제거 반영됐는지 함께 갱신 — grep `"token"` 잔존 어서션 정리).

- [ ] **Step 5: 커밋**

```bash
git add server/app/facemarket_enrollment.py server/tests/test_facemarket_biometric_enrollment.py
git commit -m "refactor(facemarket): complete does SFace match only, reads stored CI (drops token)"
```

---

## Task 4: 백엔드 — 대표이미지 업로드(비게이팅) + 바인딩때 승격

**Files:**
- Modify: `server/app/facemarket_enrollment.py` (`/profile-image` 라우트, 바인딩때 cover_image 승격)
- Test: `server/tests/test_facemarket_biometric_enrollment.py`

**Interfaces:**
- Consumes: Task1 `profile_image_r2_key` 컬럼. `_r2_face(request)`, `ext_for_mime`, `sha256_sri`.
- Produces: `POST /enrollments/{id}/profile-image` (multipart `image`) → enrollment.profile_image_r2_key 저장, 상태 불변. 바인딩때 `fm_models.cover_image_url` 승격.

- [ ] **Step 1: 실패 테스트**

```python
def test_profile_image_upload_is_non_gating(enrollment_client, auth, enrollment_store, fake_r2, completion_fakes):
    eid = create_enrollment(enrollment_client, auth)  # photos_pending
    res = enrollment_client.post(
        f"/v1/facemarket/enrollments/{eid}/profile-image",
        files={"image": ("cover.jpg", b"cover-bytes", "image/jpeg")},
        headers=auth(),
    )
    assert res.status_code == 201, res.text
    assert enrollment_store.enrollments[0]["status"] == "photos_pending"  # 불변
    assert enrollment_store.enrollments[0]["profile_image_r2_key"]
    assert len(fake_r2.objects) >= 1


def test_profile_image_rejects_bad_mime(enrollment_client, auth, completion_fakes):
    eid = create_enrollment(enrollment_client, auth)
    res = enrollment_client.post(
        f"/v1/facemarket/enrollments/{eid}/profile-image",
        files={"image": ("x.txt", b"nope", "text/plain")},
        headers=auth(),
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "unsupported_type"
```

- [ ] **Step 2: 실행 → 실패 확인**

Run: `cd server && .venv/bin/python -m pytest tests/test_facemarket_biometric_enrollment.py -k "profile_image" -q`
Expected: FAIL — 라우트 없음(404).

- [ ] **Step 3: 구현**

라우트(파일 내 upload_enrollment_photo 근처):
```python
@router.post("/enrollments/{enrollment_id}/profile-image",
             response_model=EnrollmentView, status_code=201)
async def upload_profile_image(
    request: Request,
    enrollment_id: str,
    image: UploadFile = File(...),
    user_id: str = Depends(require_user),
):
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    mime = (image.content_type or "").lower()
    if mime not in ALLOWED_FACE_MIME:
        raise _err("unsupported_type", "PNG, JPEG, WebP 이미지만 사용할 수 있습니다.")
    data = await image.read()
    if not data:
        raise _err("empty_upload", "빈 파일은 사용할 수 없습니다.")
    if len(data) > MAX_FACE_BYTES:
        raise _err("file_too_large", "이미지는 25MB 이하만 가능합니다.", status=413)
    r2 = _r2_face(request)
    ext = ext_for_mime(mime)
    key = f"private/fm-profile/{enrollment_id}.{ext}"
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        if row is None:
            raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
        r2.put_bytes(key, data, mime)
        async with conn.cursor() as cur:
            await cur.execute(
                "update fm_biometric_enrollments set profile_image_r2_key = %s "
                "where id = %s and user_id = %s",
                (key, enrollment_id, user_id),
            )
        await conn.commit()
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        return await _enrollment_view(conn, row)
```
바인딩 승격(Task3 바인딩 블록, `fm_models` update/insert 직후): row 에 `profile_image_r2_key` 있으면 `update fm_models set cover_image_url = %s where id = %s`(R2 공개 URL 규칙은 기존 cover_image_url 세팅 방식 따름 — 없으면 키 그대로 저장하고 후속 자산잡이 URL화). 구현자는 기존 cover_image_url 채우는 코드(있다면)를 참조; 없으면 키 저장까지만 하고 노출 URL 변환은 out-of-scope 주석.

- [ ] **Step 4: FakeCursor branch + 실행**

FakeCursor 에 `update fm_biometric_enrollments set profile_image_r2_key` branch + (승격시) `update fm_models set cover_image_url` branch 추가.
Run: `cd server && .venv/bin/python -m pytest tests/test_facemarket_biometric_enrollment.py -k "profile_image" -q`
Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add server/app/facemarket_enrollment.py server/tests/test_facemarket_biometric_enrollment.py
git commit -m "feat(facemarket): non-gating profile image upload + cover promotion on bind"
```

---

## Task 5: 프론트 — 스텝 재정렬 + api 클라이언트

**Files:**
- Modify: `src/features/model/biometricEnrollment.js`
- Modify: `src/lib/api/facemarket.js`
- Test: `tests/frontend/facemarket-biometric-enrollment.test.mjs`

**Interfaces:**
- Consumes: Task2 `/identity`, Task4 `/profile-image`, Task3 complete(token 없음).
- Produces: `createIdentity(enrollmentId, {token})`, `uploadProfileImage({enrollmentId, fileBlob, filename})`, `completeEnrollment(enrollmentId, {sessionId, idPhotoHex})`; `nextEnrollmentStep(identity_pending)==='identity'`; `ENROLLMENT_STEPS` 재정렬; `ENROLLMENT_ANGLES[i].exampleImage`.

- [ ] **Step 1: 실패 테스트(순수 로직/소스 어서션)**

`facemarket-biometric-enrollment.test.mjs`:
```js
test('ENROLLMENT_STEPS puts identity right after consent, before photos', () => {
  const i = ENROLLMENT_STEPS.indexOf('identity');
  assert.equal(ENROLLMENT_STEPS[0], 'consent');
  assert.equal(ENROLLMENT_STEPS[1], 'identity');
  assert.ok(i < ENROLLMENT_STEPS.indexOf('photos'));
  assert.ok(ENROLLMENT_STEPS.indexOf('profile') > ENROLLMENT_STEPS.indexOf('photos'));
});
test('nextEnrollmentStep maps identity_pending to identity', () => {
  assert.equal(nextEnrollmentStep({ status: 'identity_pending' }), 'identity');
});
test('ENROLLMENT_ANGLES carry pose example images', () => {
  for (const a of ENROLLMENT_ANGLES) assert.ok(a.exampleImage, a.value);
});
// api client source assertions
const apiSrc = read('../../src/lib/api/facemarket.js');
test('createIdentity posts token to enrollment-scoped identity route', () => {
  assert.match(apiSrc, /createIdentity\(\s*enrollmentId\s*,\s*\{\s*token\s*\}\s*\)/);
  assert.match(apiSrc, /enrollments\/\$\{encodeURIComponent\(enrollmentId\)\}\/identity/);
});
test('completeEnrollment no longer sends token', () => {
  assert.match(apiSrc, /completeEnrollment\(enrollmentId,\s*\{\s*sessionId,\s*idPhotoHex\s*\}\)/);
  assert.doesNotMatch(apiSrc, /body:\s*\{\s*sessionId,\s*token,\s*idPhotoHex\s*\}/);
});
test('uploadProfileImage mirrors multipart pattern', () => {
  assert.match(apiSrc, /uploadProfileImage\(\{\s*enrollmentId,\s*fileBlob,\s*filename\s*\}\)/);
  assert.match(apiSrc, /profile-image/);
});
```

- [ ] **Step 2: 실행 → 실패 확인**

Run: `node --test tests/frontend/facemarket-biometric-enrollment.test.mjs`
Expected: FAIL (신규 어서션).

- [ ] **Step 3: 구현 — biometricEnrollment.js**

```js
export const ENROLLMENT_ANGLES = Object.freeze([
  { value: 'front', label: '정면', guide: '정면을 바라보고 얼굴 전체가 나오게 찍어주세요.',
    exampleImage: '/src/features/model/assets/pose-front.svg' },
  { value: 'angle45', label: '45도', guide: '정면에서 약 45도만 돌려 반측면이 보이게 찍어주세요.',
    exampleImage: '/src/features/model/assets/pose-angle45.svg' },
  { value: 'side', label: '측면', guide: '고개를 약 90도 돌려 옆모습 윤곽이 보이게 찍어주세요.',
    exampleImage: '/src/features/model/assets/pose-side.svg' },
]);

export const ENROLLMENT_STEPS = Object.freeze([
  'consent', 'identity', 'photos', 'profile', 'liveness', 'processing', 'terms', 'done',
]);

export function nextEnrollmentStep(enrollment) {
  if (!enrollment) return 'consent';
  if (enrollment.status === 'identity_pending') return 'identity';
  if (enrollment.status === 'photos_pending') return 'photos';
  if (enrollment.status === 'liveness_pending') return 'liveness';
  if (enrollment.status === 'processing' || enrollment.status === 'asset_building') return 'processing';
  if (enrollment.status === 'license_pending' || enrollment.status === 'vc_pending') return 'terms';
  if (enrollment.status === 'passed') return 'done';
  return 'failed';
}
```
주의: `exampleImage` 경로는 Vite 정적 자산 규칙에 맞춰 조정(예: `import poseFront from './assets/pose-front.svg'` 후 URL 사용). Task7 에서 자산 생성 + 실제 import 방식 확정. 여기선 문자열 필드 존재 + 테스트 통과가 목표(Task7 이 실제 import 로 교체).

- [ ] **Step 4: 구현 — facemarket.js**

`verifyIdentity`(레거시)는 그대로 두고 신규 추가:
```js
export function createIdentity(enrollmentId, { token }) {
  return http(`/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/identity`, {
    method: 'POST', body: { token },
  });
}

export async function uploadProfileImage({ enrollmentId, fileBlob, filename }) {
  const form = new FormData();
  form.append('image', fileBlob, filename || 'cover');
  return checkedJson(await _authFetch(
    `/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/profile-image`,
    { method: 'POST', body: form },
  ), '대표 이미지 업로드에 실패했어요. 잠시 후 다시 시도해 주세요.');
}
```
`completeEnrollment` 수정(106-110): `token` 제거.
```js
export function completeEnrollment(enrollmentId, { sessionId, idPhotoHex }) {
  return http(`/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/complete`, {
    method: 'POST', body: { sessionId, idPhotoHex },
  });
}
```

- [ ] **Step 5: 실행 → 통과 확인**

Run: `node --test tests/frontend/facemarket-biometric-enrollment.test.mjs`
Expected: PASS(신규 로직/소스 어서션). 기존 어서션 중 `completeEnrollment` token 참조하던 것 있으면 갱신.

- [ ] **Step 6: 커밋**

```bash
git add src/features/model/biometricEnrollment.js src/lib/api/facemarket.js tests/frontend/facemarket-biometric-enrollment.test.mjs
git commit -m "feat(facemarket): reorder enrollment steps, add createIdentity/uploadProfileImage APIs"
```

---

## Task 6: 프론트 — ModelRegister.jsx 플로우 재배치

**Files:**
- Modify: `src/features/model/ModelRegister.jsx`
- Test: `tests/frontend/facemarket-biometric-enrollment.test.mjs`

**Interfaces:**
- Consumes: Task5 `createIdentity`, `uploadProfileImage`, `completeEnrollment({sessionId, idPhotoHex})`, 재정렬 스텝.
- Produces: OACX 위젯이 앞단 `identity` 스텝에서 실행(토큰→createIdentity, 초상→ref 보관), `profile` 스텝(사진 뒤), 라이브니스 후 complete(token 없음, ref 초상 제출), 초상 유실시 identity 재인증.

- [ ] **Step 1: 실패 테스트(소스 어서션 중심)**

```js
const reg = read('../../src/features/model/ModelRegister.jsx');
test('identity step runs OACX widget at the FRONT and calls createIdentity with token', () => {
  assert.match(reg, /createIdentity\(/);
  // completeEnrollment 호출은 token 없이 sessionId+idPhotoHex(ref)
  assert.match(reg, /completeEnrollment\(\s*[^,]+,\s*\{\s*sessionId[^}]*idPhotoHex[^}]*\}\s*\)/);
  assert.doesNotMatch(reg, /completeEnrollment\([^)]*token/);
});
test('portrait is held in a ref, never stored/logged', () => {
  assert.match(reg, /useRef\(/);
  assert.doesNotMatch(reg, /localStorage\.setItem\([^)]*dlphoto/i);
});
test('profile step wired between photos and liveness', () => {
  assert.match(reg, /uploadProfileImage\(/);
  assert.match(reg, /step === 'profile'/);
});
test('lost portrait after identity routes back to identity re-auth', () => {
  assert.match(reg, /idPhotoHex.*current|portraitRef\.current/s);
});
```
(컴포넌트 하네스 실행 테스트는 positional useState 인덱스 재배치가 커 소스 어서션 우선. 여력되면 harness 로 identity→photos 전이 1개 추가.)

- [ ] **Step 2: 실행 → 실패 확인**

Run: `node --test tests/frontend/facemarket-biometric-enrollment.test.mjs`
Expected: FAIL.

- [ ] **Step 3: 구현 — 플로우 재배치**

핵심 변경(현 `ModelRegister.jsx` 구조 기준):
- import: `createIdentity`, `uploadProfileImage` 추가; `completeEnrollment` 사용부 token 제거.
- `const portraitRef = useRef(null);` 추가(초상 HEX 메모리 보관).
- **identity 스텝(신규 앞단)**: `step === 'identity'`에서 OACX 위젯(`loadCxWidget` + `OACX.LOAD_MODULE(CX_CONFIG_URL, {contentInfo:{signType:'ENT_MID'}, compareCI:false, isBirth:true, useConvertor:true}, cb)`) 실행 — 현재 `finishIdentity`(287-335)의 위젯 로직을 이 앞단으로 이동. 콜백에서 `token`·`parsed.data.dlphotoimage` 획득 → `portraitRef.current = idPhotoHex` 저장 → `await createIdentity(enrollment.id, { token })` → 성공시 `getEnrollment` → `setStep(nextEnrollmentStep(...))`(=photos). transient/terminal 구분(`isTransientIdentityError`) 재사용.
- **photos 스텝**: 변경 없음(단, 진입 전 identity 통과 필수 — 상태가 보장). `ModelFaceUpload` `onDone={finishPhotos}` 후 다음 스텝 = profile.
- **profile 스텝(신규)**: `step === 'profile'`에서 이미지 선택 → `uploadProfileImage({enrollmentId, fileBlob, filename})` → 성공/건너뛰기 → `setStep('liveness')`. 건너뛰기 버튼 제공(비게이팅).
- **liveness → complete**: `FaceLivenessStep onAnalysisComplete`가 이제 `finishMatch`(신규/개명) 호출 — `completeEnrollment(enrollment.id, { sessionId: session.sessionId, idPhotoHex: portraitRef.current })`(token 없음). `portraitRef.current` 없으면(새로고침 유실) → `setError(...) + setStep('identity')`로 재인증 유도(조용한 실패 금지).
- 언마운트/취소 로직(`issuedLivenessEnrollmentRef` 등)은 유지하되 스텝 순서 반영.

- [ ] **Step 4: 실행 → 통과 확인**

Run: `node --test tests/frontend/facemarket-biometric-enrollment.test.mjs`
Expected: PASS. harness 실행 테스트가 positional state 로 깨지면 `initialStates` 인덱스/`effects[N]` 재정렬.

- [ ] **Step 5: 커밋**

```bash
git add src/features/model/ModelRegister.jsx tests/frontend/facemarket-biometric-enrollment.test.mjs
git commit -m "feat(facemarket): move OACX identity to front step, profile step, token-less complete"
```

---

## Task 7: 프론트 — 얼굴 3장 예시 포즈 일러스트

**Files:**
- Create: `src/features/model/assets/pose-front.svg`, `pose-angle45.svg`, `pose-side.svg`
- Modify: `src/features/model/ModelFaceUpload.jsx` (SlotCard 예시 렌더), `biometricEnrollment.js`(import 방식 확정)
- Test: `tests/frontend/facemarket-biometric-enrollment.test.mjs`

**Interfaces:**
- Consumes: Task5 `ENROLLMENT_ANGLES[i].exampleImage`.
- Produces: 각 슬롯에 포즈 일러스트 표시(실제 사람 얼굴 미사용).

- [ ] **Step 1: 실패 테스트(소스 어서션)**

```js
const upload = read('../../src/features/model/ModelFaceUpload.jsx');
test('SlotCard renders a pose example image from the angle', () => {
  assert.match(upload, /exampleImage|example/);
  assert.match(upload, /<img[^>]+(example|pose)/i);
});
```
+ 자산 존재 확인:
```js
import { existsSync } from 'node:fs';
for (const p of ['pose-front', 'pose-angle45', 'pose-side']) {
  test(`asset ${p}.svg exists`, () => {
    assert.ok(existsSync(new URL(`../../src/features/model/assets/${p}.svg`, import.meta.url)));
  });
}
```

- [ ] **Step 2: 실행 → 실패 확인**

Run: `node --test tests/frontend/facemarket-biometric-enrollment.test.mjs`
Expected: FAIL(자산 없음 + 렌더 없음).

- [ ] **Step 3: 자산 생성 — 포즈 실루엣 SVG (얼굴 없음)**

`src/features/model/assets/pose-front.svg` (정면 머리/어깨 실루엣, 화살표 없음):
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="정면 포즈 예시">
  <circle cx="32" cy="24" r="13" fill="#cbd5e1"/>
  <path d="M12 60c0-11 9-18 20-18s20 7 20 18z" fill="#cbd5e1"/>
</svg>
```
`pose-angle45.svg` (머리를 살짝 돌린 실루엣):
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="45도 포즈 예시">
  <ellipse cx="30" cy="24" rx="11" ry="13" fill="#cbd5e1"/>
  <path d="M12 60c0-11 9-18 20-18s20 7 20 18z" fill="#cbd5e1"/>
  <path d="M40 20l6-4" stroke="#94a3b8" stroke-width="2" fill="none"/>
</svg>
```
`pose-side.svg` (측면 실루엣):
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="측면 포즈 예시">
  <path d="M26 12c9 0 15 6 15 13 0 4-2 7-5 9l2 8H22c-4-3-6-8-6-13 0-9 6-17 10-17z" fill="#cbd5e1"/>
  <path d="M18 60c0-10 8-16 17-16s17 6 17 16z" fill="#cbd5e1"/>
</svg>
```
(디자인 정교화는 후속 — 실루엣/실제사람아님만 충족.)

- [ ] **Step 4: 구현 — SlotCard 예시 렌더 + import**

`biometricEnrollment.js` 에서 Vite 자산 import 로 교체:
```js
import poseFront from './assets/pose-front.svg';
import poseAngle45 from './assets/pose-angle45.svg';
import poseSide from './assets/pose-side.svg';
// ENROLLMENT_ANGLES 의 exampleImage 를 각 import 변수로 지정
```
`ModelFaceUpload.jsx`: `angles.map` 에서 `exampleImage={a.exampleImage}` 를 SlotCard 로 전달; SlotCard 시그니처에 `exampleImage` 추가; `slotGuide`(~97) 옆에 렌더:
```jsx
{exampleImage && <img className={s.slotExample} src={exampleImage} alt="" aria-hidden="true" />}
```
(CSS 클래스 `slotExample` 는 module.css 에 소형 썸네일 스타일 추가.)

- [ ] **Step 5: 실행 → 통과 확인**

Run: `node --test tests/frontend/facemarket-biometric-enrollment.test.mjs`
Expected: PASS.

- [ ] **Step 6: 커밋**

```bash
git add src/features/model/assets/ src/features/model/ModelFaceUpload.jsx src/features/model/biometricEnrollment.js src/features/model/ModelRegister.module.css tests/frontend/facemarket-biometric-enrollment.test.mjs
git commit -m "feat(facemarket): pose-illustration examples on the 3-photo step"
```

---

## 통합 확인(마지막 태스크 후)

- [ ] 백엔드 전체 스위트: `cd server && .venv/bin/python -m pytest tests/test_facemarket_biometric_enrollment.py tests/test_facemarket_identity_first_migration.py tests/test_personalization_qc_blocking.py -q`
- [ ] 프론트: `node --test tests/frontend/facemarket-biometric-enrollment.test.mjs tests/frontend/login-local-password.test.mjs tests/frontend/shell-profile-menu.test.mjs`
- [ ] 흐름 육안(로컬 QA): 동의→신분증(위젯)→사진3장(예시)→대표이미지→라이브니스→완료.

## Out of scope (이 계획 아님)

- census #2 서버측 초상 재fetch(벤더 미지원), 속옷/노출 카테고리 제외(별도 subsystem), prod VC 홀더 배포, deploy-gate #1 manifest.
