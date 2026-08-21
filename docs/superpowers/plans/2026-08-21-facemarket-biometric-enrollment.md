# FaceMarket Biometric Enrollment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 정부 신분증 사진, AWS Face Liveness live selfie, 정면·45도·측면 보정사진이 같은 사람임을 서버가 증명한 enrollment만 private FaceMarket 자산과 FaceLicense 발급의 입력으로 허용한다.

**Architecture:** FaceMarket 전용 enrollment aggregate가 동의, private quarantine 사진, 1회용 AWS session digest, OACX transaction digest, 정책 버전, 일반화된 판정만 보관한다. OACX token은 `POST /complete`에서만 받아 같은 요청 안에서 `/trans` 조회와 SFace 비교를 끝내므로 정부 사진과 token을 요청 사이에 저장하지 않는다. 등록 성공 전 모델과 라이선스는 non-active이며, 기존 `fm_model_asset_build` worker가 승인된 enrollment 사진만 versioned private key로 승격한다.

**Tech Stack:** FastAPI, Pydantic `CamelModel`, async psycopg, PostgreSQL/Supabase RLS, Cloudflare R2 S3 API, boto3 Rekognition/STS, OpenCV YuNet/SFace, React 18, Vite, Amplify UI Face Liveness, Node test runner, pytest

**Spec:** [`docs/superpowers/specs/2026-08-21-facemarket-biometric-runtime-hardening-design.md`](../specs/2026-08-21-facemarket-biometric-runtime-hardening-design.md)

## Global Constraints

- OACX `/trans`가 제공하는 정부 신분증 사진만 인정한다. 사진이 없거나 해석할 수 없으면 `id_portrait_unavailable`로 fail closed하고 사용자 업로드 fallback은 만들지 않는다.
- 안전 계약으로 승인 API를 보정한다. `POST /v1/facemarket/enrollments`는 별도 생체 동의만 받고, OACX token은 `POST /v1/facemarket/enrollments/{id}/complete`에서 받는다.
- OACX 실제 사진 필드명, 인코딩, 최대 크기, TTL이 확인되기 전에는 `dev-mock-v1` 계약만 개발 환경에서 허용하며 production 기능 flag는 켜지 않는다.
- AWS Rekognition Face Liveness는 `us-east-1`만 사용한다. `CreateFaceLivenessSession`은 `Settings={"AuditImagesLimit": 0}`만 전달하고 `OutputConfig`를 설정하지 않는다.
- Amplify 브라우저 자격증명은 STS가 발급하는 15분 세션이며 `rekognition:StartFaceLivenessSession`만 허용한다. role ARN이나 server-side Rekognition 권한이 없으면 startup 또는 session 생성이 fail closed한다.
- Face Liveness session은 AWS 계약대로 한 번만 사용하고 생성 후 3분에 만료한다. 재시도는 항상 새 session을 만든다.
- 동일 device/account에서 3분 이내 5회 실패하면 45분 cooldown한다.
- 정부 ID↔live와 보정사진↔live threshold는 서로 다른 필수 설정이다. 기존 3장 pairwise 기준 `0.363`을 재사용하지 않는다.
- SFace는 이미지마다 정확히 얼굴 한 개만 허용한다. 피부, 색감, 조명, 얼굴형 같은 보정 종류는 분류하지 않는다.
- 클라이언트, 로그, 예외, job payload에는 정부 사진, live reference, embedding, 상세 score, OACX token, R2 key가 들어가지 않는다.
- 정부 사진, live reference, OpenCV crop/embedding, 비교용 임시 buffer는 성공·실패·timeout 모두 `finally`에서 해제하고 가능한 mutable buffer는 0으로 덮는다.
- 보정사진은 검증 전 private quarantine prefix에만 둔다. 성공 시에만 현재 asset set으로 승격하고 실패·취소·만료 시 원본까지 삭제한다.
- 보정사진 추가·교체 등록을 시작하면 기존 모델 자산과 VC를 즉시 non-active로 만들고 전체 OACX, Liveness, SFace 검증을 다시 요구한다.
- 사진 표시와 처리 순서는 `front` → `angle45` → `side`다. 45도와 측면의 라벨·가이드·서버 angle 값이 서로 바뀌지 않아야 한다.
- model은 FaceLicense VC 발급 성공 뒤에만 `verified`, license는 그때만 `active`가 된다. 이 계획의 production activation은 mandatory Holder issue/verify 경로가 배포된 뒤에만 허용한다.
- 실제 모델 enrollment에 기존 `POST /licenses` multipart face/profile 입력을 허용하지 않는다. JSON `{ enrollmentId, allowedUse, forbiddenUse, unitPrice, validDays }`만 받는다.
- 새 범용 biometric framework, 새 policy engine, 새 이미지 분석 dependency는 만들지 않는다. 기존 `cx_identity.py`, `face_qc.py`, `personalization_qc.py`, `R2Client`, `fm_model_asset_build`를 확장한다.
- `virtual model`, 일반 Editor, SAM, personalization의 비-FaceMarket 기능은 기존 동작을 유지한다.
- `boto3>=1.35`는 이미 설치되어 있고 현재 lock은 `1.43.30`이다. backend dependency를 추가하지 않는다.
- frontend에는 공식 설치 조합인 `@aws-amplify/ui-react-liveness`, `@aws-amplify/ui-react`, `aws-amplify`만 추가한다.
- 운영 전 AWS Organizations AI services opt-out, 국외이전·위탁 법무 승인, 실제 OACX fixture, 실제 모바일 브라우저 liveness, 세 threshold 골드셋 보정을 모두 완료한다.

## File Map and Fixed Interfaces

### New files

- `supabase/migrations/20260821000000_facemarket_biometric_runtime.sql` — enrollment, quarantine photo, current-evidence linkage와 상태 제약의 forward migration. Revocation queue, `previous_status`, cutover batch는 별도 계획 소유다.
- `server/app/facemarket_enrollment.py` — 요청/응답 모델, OACX/AWS orchestration, enrollment router, quarantine lifecycle, expiry cleanup.
- `server/tests/test_facemarket_biometric_migration.py` — 정적 migration 계약과 선택적 실제 PostgreSQL 실행 검증.
- `server/tests/test_facemarket_biometrics.py` — OACX parser, SFace 1:1 비교, Rekognition/STS adapter의 순수 계약.
- `server/tests/test_facemarket_biometric_enrollment.py` — route, ownership, replay, cooldown, redaction, cleanup 통합 테스트.
- `server/tests/test_facemarket_biometric_cleanup.py` — terminal/expiry cleanup 재시도와 idempotency 테스트.
- `src/features/model/biometricEnrollment.js` — UI가 공유하는 고정 angle 순서, 상태 전이, 일반화된 사유 문구.
- `src/features/model/FaceLivenessStep.jsx` — custom credential provider를 사용하는 Amplify liveness 화면.
- `tests/frontend/facemarket-biometric-enrollment.test.mjs` — 순수 UI 상태와 정적 배선 계약.

### Modified files

- `server/app/config.py` — enrollment feature flag, AWS/OACX mode, 세 threshold와 policy version 설정.
- `server/app/main.py` — 별도 `us-east-1` Rekognition/STS client, startup credential gate, enrollment router 등록.
- `server/app/cx_identity.py` — 계약 주입형 OACX biometric evidence parser와 mutable buffer wipe.
- `server/app/agents/face_qc.py` — 정확히 한 얼굴, 1:1 similarity, required fail-closed loader.
- `server/app/r2.py` — quarantine, approved-original, versioned-derived key와 private server-side copy.
- `server/app/facemarket.py` — identity-only activation 제거, enrollment 전용 JSON license 생성, pending→VC→active 전이.
- `server/app/workers/fm_model_asset_job.py` — `{modelId,enrollmentId}` payload와 enrollment-bound 자산 승격.
- `server/app/workers/dispatcher.py` — 기존 60초 sweep에 terminal enrollment cleanup 연결.
- `server/app/agents/identity_source.py` — 현재 enrollment와 asset source/evidence version 일치 확인.
- `server/tests/test_face_qc.py` — required mode fail-closed와 단일 얼굴 계약.
- `server/tests/test_facemarket_identity.py` — OACX identity만으로 verified가 되던 기대 제거.
- `server/tests/test_facemarket_licenses.py` — enrollment-only license와 pending activation 계약.
- `server/tests/test_fm_model_asset_job.py` — enrollment 입력, versioned key, atomic promotion 검증.
- `server/tests/test_identity_source.py` — stale enrollment asset 거절 검증.
- `package.json`, `pnpm-lock.yaml` — Amplify liveness dependencies.
- `src/lib/api/facemarket.js` — enrollment CRUD/session/complete/license JSON API.
- `src/features/model/ModelRegister.jsx` — 생체 동의→사진→liveness→OACX→complete wizard.
- `src/features/model/ModelFaceUpload.jsx` — 주입형 photo API 재사용과 `front`→`angle45`→`side` 순서.
- `src/features/model/ModelLicense.jsx` — 승인 enrollment를 참조하는 terms 발급.
- `src/features/model/ModelHub.jsx` — 수동 asset build 제거와 enrollment 상태 표시.
- `src/App.jsx` — pending/reverification 모델은 등록·terms에 접근시키되 생성은 verified만 허용.
- `src/features/model/ModelPersonalization.module.css`, `src/features/model/ModelRegister.module.css` — liveness와 quarantine 상태 레이아웃.
- `copilot/api/manifest.yml` — production enrollment flag `false`, `us-east-1` region, secret role ARN 참조.

### Cross-task interface contract

| Producer | Exact interface | Consumer |
| --- | --- | --- |
| Task 3 | `get_oacx_biometric_contract(settings: Settings) -> OacxBiometricContract` | Task 6 completion |
| Task 3 | `parse_oacx_biometric_evidence(trans: Mapping[str, Any], *, contract: OacxBiometricContract, now: datetime) -> OacxBiometricEvidence` | Task 6 completion |
| Task 3 | `wipe_bytearray(value: bytearray | None) -> None` | Task 6 raw cleanup |
| Task 3 | `load_face_qc(settings, *, required: bool = False) -> FaceQc | None` and `FaceQc.one_to_one_similarity(reference, candidate) -> float` | Task 6 comparison |
| Task 5 | `create_liveness_session(rekognition, *, client_request_token: str) -> str` and `assume_liveness_browser_credentials(sts, *, role_arn: str, session_name: str) -> dict` | Task 5 route |
| Task 6 | `get_liveness_result(rekognition, *, session_id: str, minimum_confidence: float) -> LivenessResult` and `process_enrollment_completion(app, *, enrollment_id: str, user_id: str, session_id: str, oacx_token: str) -> EnrollmentDecision` | Task 6 route |
| Task 4 | `cleanup_terminal_enrollment(app, *, enrollment_id: str) -> bool` | Task 4 cancel, Task 6 failure, Task 7 retry sweep |
| Task 7 | `sweep_terminal_enrollments(app, *, limit: int = 100) -> int` | dispatcher |
| Task 4 | `enrollment_quarantine_key`, `enrollment_original_key`, `model_asset_key`, `R2Client.copy` | Tasks 4 and 8 |
| Task 10 | `ENROLLMENT_ANGLES`, `ENROLLMENT_STEPS`, `enrollmentReasonMessage`, `nextEnrollmentStep` | React enrollment wizard |

---

### Task 1: Persist Enrollment Evidence Without Raw Biometrics

**Files:**
- Create: `supabase/migrations/20260821000000_facemarket_biometric_runtime.sql`
- Create: `server/tests/test_facemarket_biometric_migration.py`

**Interfaces:**
- Consumes: 기존 `fm_models`, `fm_identity_verifications`, `fm_licenses`, `fm_model_assets`, `jobs` 테이블.
- Produces: `fm_biometric_enrollments`, `fm_biometric_enrollment_photos`, `fm_models.current_enrollment_id`, `fm_licenses.enrollment_id`, `fm_model_assets.source_enrollment_id`, `fm_model_assets.evidence_version`.

- [ ] **Step 1: Write the failing static migration contract**

```python
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260821000000_facemarket_biometric_runtime.sql"
)


def test_biometric_tables_are_service_private_and_raw_free():
    sql = " ".join(MIGRATION.read_text().split()).lower()
    assert "create table if not exists public.fm_biometric_enrollments" in sql
    assert "create table if not exists public.fm_biometric_enrollment_photos" in sql
    assert sql.count("enable row level security") >= 2
    enrollment_schema = sql.split("create table if not exists public.fm_biometric_enrollments", 1)[1]
    enrollment_schema = enrollment_schema.split(");", 1)[0]
    for forbidden in ("portrait", "reference_image", "embedding", "raw_token", "confidence", "score"):
        assert forbidden not in enrollment_schema


def test_status_and_current_evidence_links_are_constrained():
    sql = " ".join(MIGRATION.read_text().split()).lower()
    assert "reverification_required" in sql
    assert "current_enrollment_id" in sql
    assert "source_enrollment_id" in sql
    assert "evidence_version" in sql
    assert "primary key (enrollment_id, angle)" in sql
    assert "angle in ('front', 'angle45', 'side')" in sql
    assert "storage_state in ('quarantine', 'approved')" in sql
    assert "device_digest text not null" in sql
    assert "fm_biometric_failure_device_window" in sql
```

- [ ] **Step 2: Run the migration contract and confirm the missing-file failure**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometric_migration.py`

Expected: FAIL with `FileNotFoundError` for `20260821000000_facemarket_biometric_runtime.sql`.

- [ ] **Step 3: Add the forward-only schema**

Create the migration with these exact states and columns:

```sql
alter table public.fm_models drop constraint if exists fm_models_status_check;
alter table public.fm_models add constraint fm_models_status_check
  check (status in ('pending', 'verified', 'suspended', 'reverification_required'));

alter table public.fm_licenses drop constraint if exists fm_licenses_status_check;
alter table public.fm_licenses add constraint fm_licenses_status_check
  check (status in ('pending', 'active', 'revoked', 'expired', 'reverification_required'));

create table if not exists public.fm_biometric_enrollments (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete set null,
  model_id uuid references public.fm_models(id) on delete set null,
  device_digest text not null,
  consent_version text not null,
  consented_at timestamptz not null default now(),
  oacx_tx_digest text unique,
  liveness_session_digest text unique,
  liveness_nonce_digest text,
  status text not null default 'photos_pending'
    check (status in (
      'photos_pending', 'liveness_pending', 'processing', 'asset_building',
      'license_pending', 'vc_pending', 'passed', 'failed', 'cancelled', 'expired'
    )),
  decision text check (decision is null or decision in ('passed', 'failed')),
  reason text,
  provider_versions jsonb not null default '{}'::jsonb,
  match_policy_version text,
  raw_deletion_evidence jsonb not null default '{}'::jsonb,
  cooldown_until timestamptz,
  vc_id text,
  expires_at timestamptz not null default (now() + interval '24 hours'),
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.fm_biometric_enrollment_photos (
  enrollment_id uuid not null references public.fm_biometric_enrollments(id) on delete cascade,
  angle text not null check (angle in ('front', 'angle45', 'side')),
  r2_key text not null,
  image_digest text not null,
  mime_type text not null check (mime_type in ('image/png', 'image/jpeg', 'image/webp')),
  byte_size integer not null check (byte_size > 0 and byte_size <= 26214400),
  qc_status text not null default 'passed' check (qc_status = 'passed'),
  storage_state text not null default 'quarantine'
    check (storage_state in ('quarantine', 'approved')),
  uploaded_at timestamptz not null default now(),
  approved_at timestamptz,
  primary key (enrollment_id, angle)
);

alter table public.fm_models
  add column if not exists current_enrollment_id uuid
    references public.fm_biometric_enrollments(id) on delete set null;
alter table public.fm_licenses
  add column if not exists enrollment_id uuid
    references public.fm_biometric_enrollments(id) on delete restrict;
alter table public.fm_model_assets
  add column if not exists source_enrollment_id uuid
    references public.fm_biometric_enrollments(id) on delete restrict,
  add column if not exists evidence_version text;

create unique index if not exists fm_biometric_active_per_user
  on public.fm_biometric_enrollments(user_id)
  where status in ('photos_pending', 'liveness_pending', 'processing', 'asset_building',
                   'license_pending', 'vc_pending');
create index if not exists fm_biometric_cleanup_due
  on public.fm_biometric_enrollments(expires_at)
  where status not in ('passed', 'cancelled', 'expired');
create index if not exists fm_biometric_failure_device_window
  on public.fm_biometric_enrollments(device_digest, completed_at desc);
create index if not exists fm_biometric_failure_user_window
  on public.fm_biometric_enrollments(user_id, completed_at desc);
create unique index if not exists fm_licenses_enrollment_unique
  on public.fm_licenses(enrollment_id) where enrollment_id is not null;

alter table public.fm_biometric_enrollments enable row level security;
alter table public.fm_biometric_enrollment_photos enable row level security;

drop trigger if exists fm_biometric_enrollments_set_updated_at
  on public.fm_biometric_enrollments;
create trigger fm_biometric_enrollments_set_updated_at
  before update on public.fm_biometric_enrollments
  for each row execute function public.set_updated_at();

update public.fm_models set qc_score = null where qc_score is not null;
```

Do not add authenticated RLS policies to either new table; backend service-role queries are the only data path.

- [ ] **Step 4: Add the optional execution test**

Follow the existing `FACEMARKET_TEST_DATABASE_URL` pattern and verify inside a rollback transaction that:

```python
@requires_database
def test_biometric_migration_executes_and_enforces_one_photo_per_angle():
    async def scenario():
        conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
        try:
            await conn.execute(MIGRATION.read_text())
            enrollment = await conn.execute(
                "insert into fm_biometric_enrollments "
                "(user_id, device_digest, consent_version) "
                "values (null, 'sha256-device', '2026-08-v1') returning id"
            )
            enrollment_id = (await enrollment.fetchone())["id"]
            values = (enrollment_id, "front", "private/key.jpg", "sha256-test", "image/jpeg", 10)
            await conn.execute(
                "insert into fm_biometric_enrollment_photos "
                "(enrollment_id, angle, r2_key, image_digest, mime_type, byte_size) "
                "values (%s,%s,%s,%s,%s,%s)",
                values,
            )
            with pytest.raises(UniqueViolation):
                async with conn.transaction():
                    await conn.execute(
                        "insert into fm_biometric_enrollment_photos "
                        "(enrollment_id, angle, r2_key, image_digest, mime_type, byte_size) "
                        "values (%s,%s,%s,%s,%s,%s)",
                        values,
                    )
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(scenario())
```

- [ ] **Step 5: Run migration tests**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometric_migration.py`

Expected: PASS; the database test is SKIP only when `FACEMARKET_TEST_DATABASE_URL` is absent.

- [ ] **Step 6: Commit the schema boundary**

```bash
git add supabase/migrations/20260821000000_facemarket_biometric_runtime.sql server/tests/test_facemarket_biometric_migration.py
git commit -m "Preserve enrollment evidence without retaining raw biometrics" \
  -m "Constraint: quarantine media remains service-private and scores are not persisted
Confidence: high
Scope-risk: moderate
Directive: keep new status values and evidence links synchronized with API and workers
Tested: uv run pytest -q tests/test_facemarket_biometric_migration.py
Not-tested: live Supabase migration when FACEMARKET_TEST_DATABASE_URL is absent"
```

### Task 2: Gate Configuration and AWS Clients

**Files:**
- Modify: `server/app/config.py:254-287,484-503`
- Modify: `server/app/main.py:123-142,249-259`
- Create: `server/app/facemarket_enrollment.py`
- Create: `server/tests/test_facemarket_biometrics.py`
- Modify: `copilot/api/manifest.yml:190-220`

**Interfaces:**
- Consumes: `Settings`, existing private `app.state.r2_face`, boto3 default task-role credentials.
- Produces: `app.state.fm_rekognition`, `app.state.fm_sts`, disabled-by-default enrollment router, exact settings listed below.

- [ ] **Step 1: Write failing configuration and startup tests**

```python
def biometric_settings(**overrides):
    return make_settings(
        app_env="dev",
        facemarket_enabled=True,
        fm_biometric_enrollment_enabled=True,
        fm_oacx_contract_mode="dev-mock-v1",
        fm_liveness_region="us-east-1",
        fm_liveness_browser_role_arn="arn:aws:iam::123456789012:role/fm-liveness-browser",
        fm_liveness_confidence_threshold=90.0,
        fm_id_live_threshold=0.45,
        fm_retouched_live_threshold=0.40,
        fm_match_policy_version="dev-gold-v1",
        fm_face_qc_enabled=True,
        opendid_holder_url="http://holder.test",
        **overrides,
    )


def test_biometric_feature_defaults_off():
    settings = make_settings()
    assert settings.fm_biometric_enrollment_enabled is False
    assert settings.fm_liveness_region == "us-east-1"


def test_production_rejects_dev_mock_contract():
    with pytest.raises(RuntimeError, match="verified OACX biometric contract"):
        create_app(biometric_settings(app_env="production"))


def test_enabled_feature_requires_three_calibrated_settings():
    with pytest.raises(RuntimeError, match="biometric thresholds"):
        create_app(biometric_settings(fm_id_live_threshold=None))
```

- [ ] **Step 2: Run the focused tests and observe missing settings**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometrics.py -k 'settings or production'`

Expected: FAIL because the biometric settings and startup gate do not exist.

- [ ] **Step 3: Add exact settings without numeric production defaults**

Add these `Settings` fields and environment mappings:

```python
fm_biometric_enrollment_enabled: bool = False
fm_oacx_contract_mode: str = "disabled"
fm_liveness_region: str = "us-east-1"
fm_liveness_browser_role_arn: str | None = None
fm_liveness_confidence_threshold: float | None = None
fm_id_live_threshold: float | None = None
fm_retouched_live_threshold: float | None = None
fm_match_policy_version: str | None = None
```

```python
fm_biometric_enrollment_enabled=(
    os.getenv("FM_BIOMETRIC_ENROLLMENT_ENABLED", "false").lower() == "true"
),
fm_oacx_contract_mode=os.getenv("FM_OACX_CONTRACT_MODE", "disabled"),
fm_liveness_region=os.getenv("FM_LIVENESS_REGION", "us-east-1"),
fm_liveness_browser_role_arn=os.getenv("FM_LIVENESS_BROWSER_ROLE_ARN") or None,
fm_liveness_confidence_threshold=(
    float(os.environ["FM_LIVENESS_CONFIDENCE_THRESHOLD"])
    if os.getenv("FM_LIVENESS_CONFIDENCE_THRESHOLD") else None
),
fm_id_live_threshold=(
    float(os.environ["FM_ID_LIVE_THRESHOLD"])
    if os.getenv("FM_ID_LIVE_THRESHOLD") else None
),
fm_retouched_live_threshold=(
    float(os.environ["FM_RETOUCHED_LIVE_THRESHOLD"])
    if os.getenv("FM_RETOUCHED_LIVE_THRESHOLD") else None
),
fm_match_policy_version=os.getenv("FM_MATCH_POLICY_VERSION") or None,
```

- [ ] **Step 4: Add startup validation and distinct clients**

In `facemarket_enrollment.py`, add:

```python
def validate_biometric_settings(settings: Settings) -> None:
    if not settings.fm_biometric_enrollment_enabled:
        return
    if not settings.facemarket_enabled:
        raise RuntimeError("FACEMARKET_ENABLED is required for biometric enrollment")
    if settings.fm_liveness_region != "us-east-1":
        raise RuntimeError("Face Liveness region must be us-east-1")
    if not settings.fm_liveness_browser_role_arn:
        raise RuntimeError("FM_LIVENESS_BROWSER_ROLE_ARN is required")
    if not settings.fm_face_qc_enabled:
        raise RuntimeError("FM_FACE_QC_ENABLED is required")
    thresholds = (
        settings.fm_liveness_confidence_threshold,
        settings.fm_id_live_threshold,
        settings.fm_retouched_live_threshold,
        settings.fm_match_policy_version,
    )
    if any(value is None for value in thresholds):
        raise RuntimeError("calibrated biometric thresholds and policy version are required")
    if settings.fm_oacx_contract_mode == "dev-mock-v1" and settings.app_env != "dev":
        raise RuntimeError("verified OACX biometric contract is required outside dev")
    if settings.fm_oacx_contract_mode != "dev-mock-v1":
        raise RuntimeError("verified OACX biometric contract is required")


def build_biometric_aws_clients(settings: Settings):
    rekognition = boto3.client("rekognition", region_name="us-east-1")
    sts = boto3.client("sts", region_name="us-east-1")
    return rekognition, sts
```

Call `validate_biometric_settings(settings)` before router inclusion. When enabled, assign the returned clients to `app.state.fm_rekognition` and `app.state.fm_sts`; otherwise assign both to `None`. Register `facemarket_enrollment.router` only when both FaceMarket and biometric flags are true.

- [ ] **Step 5: Keep production disabled in Copilot**

Add non-secret variables:

```yaml
  FM_BIOMETRIC_ENROLLMENT_ENABLED: "false"
  FM_OACX_CONTRACT_MODE: disabled
  FM_LIVENESS_REGION: us-east-1
```

Add the ARN only as a secret reference after the role exists:

```yaml
  FM_LIVENESS_BROWSER_ROLE_ARN: /copilot/wearless/prod/secrets/FM_LIVENESS_BROWSER_ROLE_ARN
```

Do not add threshold values to the production manifest before calibration artifacts exist.

- [ ] **Step 6: Run startup tests and the existing app-construction suite**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometrics.py tests/test_facemarket_identity.py -k 'settings or production or disabled'`

Expected: PASS, with biometric endpoints absent while the new flag is false.

- [ ] **Step 7: Commit the fail-closed deployment gate**

```bash
git add server/app/config.py server/app/main.py server/app/facemarket_enrollment.py server/tests/test_facemarket_biometrics.py copilot/api/manifest.yml
git commit -m "Keep biometric enrollment dark until every external contract is configured" \
  -m "Constraint: Rekognition runs only in us-east-1 and dev mock OACX is never accepted in production
Rejected: reuse the R2 region-auto boto3 client | Rekognition needs an explicit Face Liveness region
Confidence: high
Scope-risk: moderate
Directive: do not populate production thresholds without signed calibration evidence
Tested: focused startup and feature-flag tests
Not-tested: AWS IAM role assumption"
```

### Task 3: Parse OACX Evidence and Compare Exactly One Face

**Files:**
- Modify: `server/app/cx_identity.py:18-118`
- Modify: `server/app/agents/face_qc.py:28-87`
- Modify: `server/tests/test_face_qc.py`
- Modify: `server/tests/test_facemarket_biometrics.py`

**Interfaces:**
- Consumes: `cx_identity.fetch_trans`, YuNet/SFace model bundle.
- Produces: `OacxBiometricContract`, `OacxBiometricEvidence`, `OacxBiometricError`, `parse_oacx_biometric_evidence`, `wipe_bytearray`, `FaceQc.one_to_one_similarity`, `load_face_qc(required=True)`.

- [ ] **Step 1: Write failing OACX contract tests**

Use a fixed development fixture whose four external properties are explicit:

```python
DEV_TRANS = {
    "ci": "dev-ci-value",
    "birth": "19900102",
    "nm": "홍길동",
    "txId": "tx-dev-1",
    "idPortraitBase64": base64.b64encode(b"portrait-bytes").decode(),
    "idPortraitMime": "image/jpeg",
    "issuedAt": "2026-08-21T03:00:00Z",
}
NOW = datetime(2026, 8, 21, 3, 2, tzinfo=timezone.utc)


def test_dev_contract_extracts_mutable_sensitive_buffers():
    evidence = parse_oacx_biometric_evidence(
        DEV_TRANS, contract=DEV_MOCK_OACX_BIOMETRIC_CONTRACT, now=NOW
    )
    assert evidence.ci == bytearray(b"dev-ci-value")
    assert evidence.portrait == bytearray(b"portrait-bytes")
    assert evidence.name_masked == "홍*동"
    assert evidence.contract_version == "dev-mock-v1"


@pytest.mark.parametrize(
    "patch",
    [
        {"idPortraitBase64": None},
        {"idPortraitBase64": "not-base64"},
        {"idPortraitMime": "application/pdf"},
        {"issuedAt": "2026-08-21T02:54:59Z"},
    ],
)
def test_unusable_portrait_fails_with_one_sanitized_reason(patch):
    trans = {**DEV_TRANS, **patch}
    with pytest.raises(OacxBiometricError) as error:
        parse_oacx_biometric_evidence(
            trans, contract=DEV_MOCK_OACX_BIOMETRIC_CONTRACT, now=NOW
        )
    assert error.value.reason == "id_portrait_unavailable"
    assert "dev-ci-value" not in str(error.value)
    assert "idPortraitBase64" not in str(error.value)
```

Also test decoded data larger than the development contract's `5 * 1024 * 1024` bytes and `get_oacx_biometric_contract(make_settings(app_env="prod", fm_oacx_contract_mode="dev-mock-v1"))` rejection.

- [ ] **Step 2: Run parser tests and confirm missing symbols**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometrics.py -k oacx`

Expected: FAIL on missing contract/parser symbols.

- [ ] **Step 3: Implement the development-only contract and sanitized parser**

Add these concrete types:

```python
@dataclass(frozen=True, slots=True)
class OacxBiometricContract:
    version: str
    portrait_path: tuple[str, ...]
    portrait_mime_path: tuple[str, ...]
    issued_at_path: tuple[str, ...]
    portrait_encoding: Literal["base64"]
    max_portrait_bytes: int
    ttl_seconds: int


@dataclass(slots=True)
class OacxBiometricEvidence:
    ci: bytearray
    birth: str
    name_masked: str
    transaction_id: str | None
    portrait: bytearray
    portrait_mime: str
    contract_version: str


class OacxBiometricError(CxIdentityError):
    def __init__(self, reason: str = "id_portrait_unavailable"):
        self.reason = reason
        super().__init__(reason)


DEV_MOCK_OACX_BIOMETRIC_CONTRACT = OacxBiometricContract(
    version="dev-mock-v1",
    portrait_path=("idPortraitBase64",),
    portrait_mime_path=("idPortraitMime",),
    issued_at_path=("issuedAt",),
    portrait_encoding="base64",
    max_portrait_bytes=5 * 1024 * 1024,
    ttl_seconds=300,
)
```

Implement strict `base64.b64decode(value, validate=True)`, UTC timestamp parsing, MIME whitelist `image/jpeg|image/png|image/webp`, adult validation through `is_adult_from_birth`, and nested path lookup. The parser must catch format/size/time errors and raise only `OacxBiometricError()`.

Implement mutable cleanup:

```python
def wipe_bytearray(value: bytearray | None) -> None:
    if value is not None:
        value[:] = b"\x00" * len(value)
```

`get_oacx_biometric_contract` returns the development contract only when `settings.app_env == "dev"` and mode is `dev-mock-v1`; every other mode raises `OacxBiometricError("oacx_contract_unavailable")`.

- [ ] **Step 4: Write failing exact-one-face and threshold tests**

```python
def test_required_loader_fails_closed_when_weights_are_missing(monkeypatch):
    monkeypatch.setattr(face_qc, "FaceQc", lambda *args: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(QcFailed) as error:
        load_face_qc(make_settings(fm_face_qc_enabled=True), required=True)
    assert error.value.reason == "qc_unavailable"


def test_one_to_one_similarity_wipes_both_embeddings(monkeypatch):
    qc = FaceQc.__new__(FaceQc)
    first = np.array([1.0, 0.0])
    second = np.array([1.0, 0.0])
    values = iter((first, second))
    monkeypatch.setattr(FaceQc, "_embed", lambda self, data: next(values))
    assert qc.one_to_one_similarity(b"id", b"live") == pytest.approx(1.0)
    assert np.count_nonzero(first) == 0
    assert np.count_nonzero(second) == 0


def test_embed_rejects_multiple_faces(monkeypatch):
    qc = FaceQc.__new__(FaceQc)
    qc._det = FakeDetector(face_count=2)
    qc._rec = FakeRecognizer()
    with pytest.raises(QcFailed) as error:
        qc._embed(_blank_png())
    assert error.value.reason == "multiple_faces"
```

- [ ] **Step 5: Run QC tests and observe fail-open behavior**

Run: `cd server && uv run pytest -q tests/test_face_qc.py tests/test_facemarket_biometrics.py -k 'required or one_to_one or multiple_faces'`

Expected: FAIL because `load_face_qc` currently degrades to `None`, `_embed` selects the largest face, and 1:1 comparison is absent.

- [ ] **Step 6: Implement minimal fail-closed comparison**

Change face selection and add the comparison exactly as follows:

```python
if faces is None or len(faces) == 0:
    raise QcFailed("no_face_detected")
if len(faces) != 1:
    raise QcFailed("multiple_faces")
face = faces[0]
```

```python
def one_to_one_similarity(
    self, reference: bytes | bytearray, candidate: bytes | bytearray
) -> float:
    left = right = None
    try:
        left = self._embed(reference)
        right = self._embed(candidate)
        denominator = float(np.linalg.norm(left)) * float(np.linalg.norm(right))
        if denominator <= 0:
            raise QcFailed("embedding_invalid")
        return float(np.dot(left, right)) / denominator
    finally:
        if left is not None:
            left.fill(0)
        if right is not None:
            right.fill(0)


def load_face_qc(settings, *, required: bool = False) -> "FaceQc | None":
    if not getattr(settings, "fm_face_qc_enabled", False):
        if required:
            raise QcFailed("qc_unavailable")
        return None
    try:
        return FaceQc(getattr(settings, "fm_face_qc_dir", None) or _DEFAULT_DIR)
    except Exception:
        log.warning("face QC init failed")
        if required:
            raise QcFailed("qc_unavailable")
        return None
```

Keep optional behavior for legacy non-biometric callers until Task 7 removes the last fail-open FaceMarket use.

- [ ] **Step 7: Run all parser and QC tests**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometrics.py tests/test_face_qc.py`

Expected: PASS; only weight-loading tests may SKIP outside the Docker image.

- [ ] **Step 8: Commit the memory-only evidence boundary**

```bash
git add server/app/cx_identity.py server/app/agents/face_qc.py server/tests/test_facemarket_biometrics.py server/tests/test_face_qc.py
git commit -m "Compare only contract-bound single-face biometric evidence" \
  -m "Constraint: OACX production portrait fields are unverified and raw evidence must remain memory-only
Rejected: largest-face selection | multiple faces make the subject ambiguous
Confidence: high
Scope-risk: moderate
Directive: replace dev-mock-v1 only from a provider fixture reviewed by privacy and security
Tested: OACX parser and SFace unit tests
Not-tested: calibrated real-person image corpus"
```

### Task 4: Create Enrollment and Quarantine Retouched Photos

**Files:**
- Modify: `server/app/facemarket_enrollment.py`
- Modify: `server/app/r2.py:25-62,115-127`
- Create: `server/tests/test_facemarket_biometric_enrollment.py`

**Interfaces:**
- Consumes: `evaluate_face_qc`, `qc_reason_message`, `R2Client`, migration tables.
- Produces: create/current/status/photo/delete/cancel routes, quarantine key helpers, basic `cleanup_terminal_enrollment`, redacted `EnrollmentView`.

- [ ] **Step 1: Write route tests for consent, ownership, and quarantine invisibility**

Create FastAPI tests with a fake async connection and fake `r2_face`. Cover these exact assertions:

```python
def test_create_enrollment_records_consent_without_oacx_token(enrollment_client, auth):
    response = enrollment_client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": "device-id-with-at-least-32-characters",
            "biometricConsent": {"accepted": True, "documentVersion": "2026-08-v1"},
        },
        headers=auth(),
    )
    assert response.status_code == 201
    assert response.json()["status"] == "photos_pending"
    assert response.json()["requiredAngles"] == ["front", "angle45", "side"]
    assert "token" not in response.text
    assert "r2Key" not in response.text
    assert "device-id-with-at-least-32-characters" not in enrollment_store.serialized()


def test_upload_passed_photo_uses_quarantine_prefix(enrollment_client, auth, fake_r2):
    enrollment_id = create_enrollment(enrollment_client, auth)
    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos",
        data={"angle": "angle45"},
        files={"photo": ("face.jpg", b"image", "image/jpeg")},
        headers=auth(),
    )
    assert response.status_code == 201
    assert response.json()["angle"] == "angle45"
    assert response.json()["qcStatus"] == "passed"
    assert fake_r2.puts[0][0] == (
        f"facemarket/enrollments/{enrollment_id}/quarantine/angle45.jpg"
    )
    assert "quarantine" not in response.text


def test_other_user_cannot_read_or_delete_enrollment(enrollment_client, auth):
    enrollment_id = create_enrollment(enrollment_client, auth)
    other = auth(sub="other-user")
    assert enrollment_client.get(
        f"/v1/facemarket/enrollments/{enrollment_id}", headers=other
    ).status_code == 404
    assert enrollment_client.delete(
        f"/v1/facemarket/enrollments/{enrollment_id}/photos/front", headers=other
    ).status_code == 404
```

Also assert stale consent version is 400, invalid angle is 400, MIME other than PNG/JPEG/WebP is 400, empty upload is 400, over 25 MiB is 413, failed basic QC performs no R2 put, and three passed angles transition to `liveness_pending`.

- [ ] **Step 2: Run route tests and confirm 404/missing router failures**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometric_enrollment.py -k 'create or upload or other_user'`

Expected: FAIL because the enrollment routes and R2 key helper do not exist.

- [ ] **Step 3: Add deterministic private keys and server-side copy**

```python
def enrollment_quarantine_key(enrollment_id: str, angle: str, ext: str) -> str:
    return f"facemarket/enrollments/{enrollment_id}/quarantine/{angle}.{ext}"


def enrollment_original_key(
    model_id: str, enrollment_id: str, angle: str, ext: str
) -> str:
    return f"facemarket/models/{model_id}/enrollments/{enrollment_id}/originals/{angle}.{ext}"


def model_asset_key(model_id: str, enrollment_id: str, view: str, ext: str) -> str:
    return f"facemarket/models/{model_id}/enrollments/{enrollment_id}/assets/{view}.{ext}"


def copy(self, source_key: str, destination_key: str, mime: str) -> None:
    self._s3.copy_object(
        Bucket=self._bucket,
        Key=destination_key,
        CopySource={"Bucket": self._bucket, "Key": source_key},
        ContentType=mime,
        MetadataDirective="REPLACE",
    )
```

- [ ] **Step 4: Implement exact request and response models**

```python
BIOMETRIC_CONSENT_VERSION = "2026-08-v1"
ENROLLMENT_TTL = timedelta(hours=24)
ANGLES = ("front", "angle45", "side")
MAX_FACE_BYTES = 25 * 1024 * 1024
ALLOWED_FACE_MIME = {"image/png", "image/jpeg", "image/webp"}


class BiometricConsent(CamelModel):
    accepted: bool
    document_version: str


class CreateEnrollmentBody(CamelModel):
    device_id: str
    biometric_consent: BiometricConsent


class EnrollmentPhotoView(CamelModel):
    angle: str
    qc_status: str
    uploaded_at: datetime


class EnrollmentView(CamelModel):
    id: str
    model_id: str | None = None
    status: str
    photos: list[EnrollmentPhotoView] = []
    required_angles: list[str] = list(ANGLES)
    passed: bool | None = None
    retryable: bool | None = None
    reason: str | None = None
    expires_at: datetime
```

Return only these fields. Do not add `imageUri`, digest, byte size, provider score, transaction/session digest, or R2 key.

- [ ] **Step 5: Implement create/current/status with re-verification freeze**

`POST /enrollments` requires a trimmed `device_id` of at least 32 characters, persists only `sha256(device_id)` as `device_digest`, and never logs or echoes the raw value. Before creating, count failed enrollments completed in the last three minutes for the same `user_id` or `device_digest`; if five exist or the latest matching `cooldown_until` is still active, return `429 liveness_cooldown`. Then lock the user's latest model. If it is `verified`, update its status and all active licenses to `reverification_required`, set `assets_status='none'`, clear `current_enrollment_id`, and create an enrollment linked to that model. If no model exists, create the enrollment with `model_id=NULL`. The partial unique index makes a repeated create return the existing nonterminal enrollment instead of creating a second one.

Add:

```text
POST /v1/facemarket/enrollments                  -> 201 EnrollmentView
GET  /v1/facemarket/enrollments/current          -> 200 EnrollmentView or 404
GET  /v1/facemarket/enrollments/{enrollment_id}  -> 200 EnrollmentView or 404
```

Every lookup includes `where e.id=%s and e.user_id=%s`; foreign ownership returns the same `404 not_found` as absence.

- [ ] **Step 6: Implement photo upload/delete and cancellation**

The upload order is:

```python
data = await photo.read()
try:
    qc = await evaluate_face_qc(settings, image_bytes=data, mime=mime, angle=angle)
    if not qc.passed:
        raise _err("face_quality", qc_reason_message(qc.reasons), reasons=qc.reasons)
    new_key = enrollment_quarantine_key(enrollment_id, angle, ext)
    await asyncio.to_thread(r2.put_bytes, new_key, data, mime)
    # Lock owned nonterminal enrollment, upsert photo metadata, commit.
finally:
    data = b""
```

On database failure delete `new_key`. On replacement with a changed extension delete the old key after commit. `DELETE /photos/{angle}` deletes the R2 object before deleting the metadata row; an R2 error leaves the row intact for retry. Implement the initial `cleanup_terminal_enrollment(app, *, enrollment_id: str) -> bool` here: load only terminal enrollment rows whose photos remain `quarantine`, delete each R2 object, delete metadata only after its object succeeds, merge redacted deletion counts, and return whether none remain. `POST /enrollments/{id}/cancel` first marks the enrollment `cancelled`, then calls that helper; it returns 200 even when a previous call already cancelled it. Task 7 adds retry and expiry sweep coverage without changing this signature.

- [ ] **Step 7: Run the full enrollment route test file**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometric_enrollment.py`

Expected: PASS with no R2 key, digest, score, or image bytes in any response.

- [ ] **Step 8: Commit the quarantine boundary**

```bash
git add server/app/facemarket_enrollment.py server/app/r2.py server/tests/test_facemarket_biometric_enrollment.py
git commit -m "Quarantine retouched photos until biometric approval" \
  -m "Constraint: unverified photos must be private and terminal enrollment states delete originals
Rejected: personalization_face_photos reuse | those rows are immediately visible to generation and cannot represent quarantine
Confidence: high
Scope-risk: moderate
Directive: preserve front-angle45-side values and never expose quarantine keys
Tested: enrollment ownership, upload, replacement, deletion, and redaction tests
Not-tested: real R2 lifecycle behavior"
```

### Task 5: Issue One-use Liveness Sessions With Narrow Browser Credentials

**Files:**
- Modify: `server/app/facemarket_enrollment.py`
- Modify: `server/tests/test_facemarket_biometrics.py`
- Modify: `server/tests/test_facemarket_biometric_enrollment.py`
- Modify: `supabase/migrations/20260821000000_facemarket_biometric_runtime.sql`

**Interfaces:**
- Consumes: `app.state.fm_rekognition`, `app.state.fm_sts`, `Settings.fm_liveness_browser_role_arn`, owned `liveness_pending` enrollment.
- Produces: `create_liveness_session`, `assume_liveness_browser_credentials`, `POST /enrollments/{id}/liveness-session`.

- [ ] **Step 1: Add the nonce uniqueness constraint to the migration test**

```python
def test_liveness_nonce_and_session_digests_are_unique():
    sql = " ".join(MIGRATION.read_text().split()).lower()
    assert "fm_biometric_liveness_nonce_unique" in sql
    assert "fm_biometric_liveness_session_unique" in sql
```

- [ ] **Step 2: Run the focused migration assertion and confirm failure**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometric_migration.py -k liveness_nonce`

Expected: FAIL because the explicit nonce/session indexes are absent.

- [ ] **Step 3: Add explicit partial unique indexes**

```sql
create unique index if not exists fm_biometric_liveness_nonce_unique
  on public.fm_biometric_enrollments(liveness_nonce_digest)
  where liveness_nonce_digest is not null;
create unique index if not exists fm_biometric_liveness_session_unique
  on public.fm_biometric_enrollments(liveness_session_digest)
  where liveness_session_digest is not null;
```

The column-level `unique` on `liveness_session_digest` may remain; the named partial index is the stable test and operational contract.

- [ ] **Step 4: Write failing boto3 adapter tests**

```python
def test_create_session_disables_audit_and_s3_output():
    rekognition = RecordingRekognition(
        create_response={"SessionId": "00000000-0000-0000-0000-000000000001"}
    )
    session_id = create_liveness_session(
        rekognition, client_request_token="a" * 64
    )
    assert session_id == "00000000-0000-0000-0000-000000000001"
    assert rekognition.create_calls == [{
        "ClientRequestToken": "a" * 64,
        "Settings": {"AuditImagesLimit": 0},
    }]
    assert "OutputConfig" not in rekognition.create_calls[0]["Settings"]


def test_sts_credentials_are_fifteen_minutes_and_start_only():
    sts = RecordingSts()
    result = assume_liveness_browser_credentials(
        sts,
        role_arn="arn:aws:iam::123456789012:role/fm-liveness-browser",
        session_name="fm-live-123456789abc",
    )
    call = sts.calls[0]
    assert call["DurationSeconds"] == 900
    policy = json.loads(call["Policy"])
    assert policy["Statement"] == [{
        "Effect": "Allow",
        "Action": "rekognition:StartFaceLivenessSession",
        "Resource": "*",
    }]
    assert set(result) == {"accessKeyId", "secretAccessKey", "sessionToken", "expiration"}
```

- [ ] **Step 5: Run adapter tests and confirm missing functions**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometrics.py -k 'create_session or sts_credentials'`

Expected: FAIL because the adapter functions are absent.

- [ ] **Step 6: Implement strict AWS adapters**

```python
START_LIVENESS_POLICY = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Action": "rekognition:StartFaceLivenessSession",
        "Resource": "*",
    }],
}


def create_liveness_session(rekognition, *, client_request_token: str) -> str:
    response = rekognition.create_face_liveness_session(
        ClientRequestToken=client_request_token,
        Settings={"AuditImagesLimit": 0},
    )
    session_id = response.get("SessionId")
    try:
        uuid.UUID(str(session_id))
    except (TypeError, ValueError):
        raise BiometricProviderError("liveness_unavailable")
    return str(session_id)


def assume_liveness_browser_credentials(
    sts, *, role_arn: str, session_name: str
) -> dict:
    response = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
        DurationSeconds=900,
        Policy=json.dumps(START_LIVENESS_POLICY, separators=(",", ":")),
    )
    credentials = response["Credentials"]
    return {
        "accessKeyId": credentials["AccessKeyId"],
        "secretAccessKey": credentials["SecretAccessKey"],
        "sessionToken": credentials["SessionToken"],
        "expiration": credentials["Expiration"],
    }
```

Catch botocore errors at the route boundary, log only exception type and generalized provider name, and return `503 liveness_unavailable`.

- [ ] **Step 7: Write the failing liveness-session route test**

```python
def test_liveness_session_is_bound_to_owner_nonce_and_three_photos(
    enrollment_client, auth, fake_rekognition, fake_sts
):
    enrollment_id = create_enrollment_with_three_photos(enrollment_client, auth)
    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/liveness-session",
        json={"nonce": "browser-nonce-with-at-least-32-bytes"},
        headers=auth(),
    )
    assert response.status_code == 201
    assert response.json()["region"] == "us-east-1"
    assert response.json()["sessionId"] == fake_rekognition.session_id
    assert set(response.json()["credentials"]) == {
        "accessKeyId", "secretAccessKey", "sessionToken", "expiration"
    }
    stored = enrollment_store[enrollment_id]
    assert stored["liveness_session_digest"] == hashlib.sha256(
        fake_rekognition.session_id.encode()
    ).hexdigest()
    assert fake_rekognition.session_id not in str(stored)
```

Add route tests for fewer than three photos, a nonce shorter than 32 characters, repeated nonce, other owner, active cooldown, STS failure, and Rekognition failure. All errors must omit session ID and temporary credentials.

- [ ] **Step 8: Implement the liveness-session route**

Add:

```python
class LivenessSessionBody(CamelModel):
    nonce: str


@router.post("/enrollments/{enrollment_id}/liveness-session", status_code=201)
async def start_enrollment_liveness(
    request: Request,
    enrollment_id: str,
    body: LivenessSessionBody,
    user_id: str = Depends(require_user),
):
    nonce = body.nonce.strip()
    if len(nonce) < 32:
        raise _err("invalid_nonce", "인증 세션을 시작할 수 없습니다.")
    nonce_digest = hashlib.sha256(nonce.encode()).hexdigest()
    # Lock owned enrollment, require liveness_pending, three photo rows, no cooldown.
    session_id = await asyncio.to_thread(
        create_liveness_session,
        request.app.state.fm_rekognition,
        client_request_token=nonce_digest,
    )
    credentials = await asyncio.to_thread(
        assume_liveness_browser_credentials,
        request.app.state.fm_sts,
        role_arn=request.app.state.settings.fm_liveness_browser_role_arn,
        session_name=f"fm-live-{enrollment_id.replace('-', '')[:12]}",
    )
    # Persist only sha256(session_id), nonce_digest and provider version; commit.
    return {
        "sessionId": session_id,
        "region": "us-east-1",
        "expiresAt": datetime.now(timezone.utc) + timedelta(minutes=3),
        "credentials": credentials,
    }
```

If STS issuance fails after session creation, do not store the session digest; the AWS session expires naturally after three minutes. The next client attempt must use a new nonce.

- [ ] **Step 9: Run migration, adapter, and route tests**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometric_migration.py tests/test_facemarket_biometrics.py tests/test_facemarket_biometric_enrollment.py -k 'liveness or session or nonce or sts'`

Expected: PASS.

- [ ] **Step 10: Commit the credential boundary**

```bash
git add supabase/migrations/20260821000000_facemarket_biometric_runtime.sql server/app/facemarket_enrollment.py server/tests/test_facemarket_biometric_migration.py server/tests/test_facemarket_biometrics.py server/tests/test_facemarket_biometric_enrollment.py
git commit -m "Limit browser liveness access to one short-lived session" \
  -m "Constraint: Amplify must sign StartFaceLivenessSession without receiving backend AWS credentials
Rejected: Cognito migration | the existing Supabase identity remains authoritative and STS is the smaller boundary
Confidence: high
Scope-risk: moderate
Directive: the browser role may grant only rekognition:StartFaceLivenessSession
Tested: exact boto3 calls, digest persistence, ownership, nonce replay, and cooldown gates
Not-tested: deployed IAM trust policy"
```

### Task 6: Complete Enrollment in One Memory-only Request

**Files:**
- Modify: `server/app/facemarket_enrollment.py`
- Modify: `server/app/facemarket.py:99-124,201-294`
- Modify: `server/tests/test_facemarket_biometrics.py`
- Modify: `server/tests/test_facemarket_biometric_enrollment.py`
- Modify: `server/tests/test_facemarket_identity.py`

**Interfaces:**
- Consumes: raw `sessionId` and OACX token from one authenticated request, stored session digest, three quarantine photos, `cx_identity` parser, required `FaceQc`.
- Produces: `LivenessResult`, `EnrollmentDecision`, `get_liveness_result`, `process_enrollment_completion`, queued `fm_model_asset_build` payload `{modelId,enrollmentId}`.

- [ ] **Step 1: Write failing Rekognition result tests**

```python
def test_get_result_requires_success_reference_and_threshold():
    rekognition = RecordingRekognition(result={
        "Status": "SUCCEEDED",
        "Confidence": 94.5,
        "ReferenceImage": {"Bytes": b"live-reference"},
    })
    result = get_liveness_result(
        rekognition,
        session_id="00000000-0000-0000-0000-000000000001",
        minimum_confidence=90.0,
    )
    assert result.reference_image == bytearray(b"live-reference")
    assert result.confidence == 94.5
    assert rekognition.result_calls == [{
        "SessionId": "00000000-0000-0000-0000-000000000001"
    }]


@pytest.mark.parametrize(
    "result,reason",
    [
        ({"Status": "IN_PROGRESS"}, "liveness_retry"),
        ({"Status": "FAILED"}, "liveness_retry"),
        ({"Status": "SUCCEEDED", "Confidence": 89.99,
          "ReferenceImage": {"Bytes": b"live"}}, "liveness_failed"),
        ({"Status": "SUCCEEDED", "Confidence": 99.0}, "liveness_retry"),
    ],
)
def test_get_result_fails_closed(result, reason):
    with pytest.raises(BiometricProviderError) as error:
        get_liveness_result(
            RecordingRekognition(result=result),
            session_id="00000000-0000-0000-0000-000000000001",
            minimum_confidence=90.0,
        )
    assert error.value.reason == reason
```

- [ ] **Step 2: Run result tests and confirm missing adapter**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometrics.py -k get_result`

Expected: FAIL because `LivenessResult` and `get_liveness_result` are absent.

- [ ] **Step 3: Implement result retrieval without persistence**

```python
@dataclass(slots=True)
class LivenessResult:
    reference_image: bytearray
    confidence: float
    provider_version: str = "aws-rekognition-face-liveness"


def get_liveness_result(
    rekognition, *, session_id: str, minimum_confidence: float
) -> LivenessResult:
    response = rekognition.get_face_liveness_session_results(SessionId=session_id)
    if response.get("Status") != "SUCCEEDED":
        raise BiometricProviderError("liveness_retry")
    reference = (response.get("ReferenceImage") or {}).get("Bytes")
    if not reference:
        raise BiometricProviderError("liveness_retry")
    confidence = float(response.get("Confidence") or 0.0)
    if confidence < minimum_confidence:
        raise BiometricProviderError("liveness_failed")
    return LivenessResult(bytearray(reference), confidence)
```

- [ ] **Step 4: Write failing completion orchestration tests**

Build a successful enrollment fixture with three quarantine photos, a stored digest for the supplied session ID, an OACX response using `DEV_TRANS`, and a fake FaceQc whose similarity sequence is `0.46, 0.41, 0.42, 0.43`.

```python
def test_complete_uses_distinct_thresholds_and_queues_bound_asset_job(
    enrollment_client, auth, biometric_fakes
):
    enrollment_id = biometric_fakes.ready_enrollment()
    response = enrollment_client.post(
        f"/v1/facemarket/enrollments/{enrollment_id}/complete",
        json={
            "sessionId": biometric_fakes.session_id,
            "token": "oacx-token-used-only-now",
        },
        headers=auth(),
    )
    assert response.status_code == 202
    assert response.json() == {
        "passed": True,
        "retryable": False,
        "reason": None,
        "status": "asset_building",
        "modelId": "model-1",
    }
    assert biometric_fakes.face_qc.calls == [
        ("id", "live"),
        ("front", "live"),
        ("angle45", "live"),
        ("side", "live"),
    ]
    assert biometric_fakes.jobs == [{
        "kind": "fm_model_asset_build",
        "payload": {"modelId": "model-1", "enrollmentId": enrollment_id},
    }]
    serialized = biometric_fakes.serialized_state()
    for secret in ("oacx-token-used-only-now", "portrait-bytes", "live-reference"):
        assert secret not in serialized
```

Add explicit tests for:

- session digest mismatch and session replay;
- missing/expired/bad OACX portrait → `id_portrait_unavailable`;
- under-19 birth → `minor_blocked`;
- ID/live score exactly below `fm_id_live_threshold`;
- each of the three retouched/live scores exactly below `fm_retouched_live_threshold`;
- same CI owned by another account → `identity_recovery_required`;
- same OACX token digest replay → `identity_replay`;
- provider timeout and FaceQc unavailable;
- every failure keeps model non-verified, creates no job, marks enrollment failed, and deletes all quarantine objects;
- failure responses contain only `passed`, `retryable`, `reason`, `status` and never scores.

- [ ] **Step 5: Run completion tests and observe missing behavior**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometric_enrollment.py -k complete`

Expected: FAIL because `/complete` and its orchestration do not exist.

- [ ] **Step 6: Define the completion request and redacted decision**

```python
class CompleteEnrollmentBody(CamelModel):
    session_id: str
    token: str


@dataclass(frozen=True, slots=True)
class EnrollmentDecision:
    passed: bool
    retryable: bool
    reason: str | None
    status: str
    model_id: str | None
```

Map only these generalized reasons:

```python
RETRYABLE_REASONS = {
    "liveness_retry",
    "liveness_unavailable",
    "qc_unavailable",
    "id_portrait_unavailable",
}
TERMINAL_REASONS = {
    "minor_blocked",
    "liveness_failed",
    "face_match_failed",
    "identity_replay",
    "identity_recovery_required",
}
```

- [ ] **Step 7: Implement completion in the safe order**

`process_enrollment_completion` must perform this sequence:

1. Validate UUIDs and non-empty token without fetching external data.
2. Lock the owned enrollment, require `liveness_pending`, require all three `quarantine` rows, compare `sha256(session_id)` to the stored digest, reject expiry/cooldown, then set `status='processing'` and commit.
3. Fetch AWS results with `asyncio.to_thread(get_liveness_result, ...)`.
4. Fetch OACX through `cx_identity.fetch_trans(settings.cx_trans_base_url, oacx_token)` and parse with the selected contract.
5. Load all three private R2 photos in `front`, `angle45`, `side` order and immediately convert each returned `bytes` to `bytearray`.
6. Call `load_face_qc(settings, required=True)`.
7. Compare ID portrait↔live once against `fm_id_live_threshold`.
8. Compare each retouched photo↔live against `fm_retouched_live_threshold`.
9. In a database transaction, derive `cxsha256:<sha256(token)>`, HMAC the CI with `fm_ci_pepper`, resolve same-owner model or create a pending model, insert `fm_identity_verifications`, bind enrollment/model, record only provider/policy versions and generalized pass, create `fm_model_asset_build` with `{modelId,enrollmentId}`, and set `status='asset_building'`.
10. Return `EnrollmentDecision(True, False, None, "asset_building", model_id)`.

The raw cleanup is non-optional, and deletion evidence must be persisted from the same `finally` path before a result or sanitized exception leaves the function:

```python
evidence = None
liveness = None
photo_buffers: list[bytearray] = []
decision: EnrollmentDecision | None = None
pending_error: BaseException | None = None
try:
    decision = await evaluate_and_commit_redacted_decision()
except BaseException as error:
    pending_error = error
finally:
    if evidence is not None:
        cx_identity.wipe_bytearray(evidence.ci)
        cx_identity.wipe_bytearray(evidence.portrait)
    if liveness is not None:
        cx_identity.wipe_bytearray(liveness.reference_image)
    for buffer in photo_buffers:
        cx_identity.wipe_bytearray(buffer)
    photo_buffers.clear()
    await asyncio.shield(record_raw_release_evidence(
        enrollment_id,
        oacx_portrait_released=True,
        liveness_reference_released=True,
        temporary_embeddings_released=True,
    ))

if pending_error is not None:
    raise pending_error
assert decision is not None
return decision
```

`record_raw_release_evidence` merges only booleans `oacxPortraitReleased`, `livenessReferenceReleased`, and `temporaryEmbeddingsReleased`; it never writes values or sizes.

- [ ] **Step 8: Make failure handling terminal, redacted, and cooldown-aware**

On any mapped comparison/provider failure:

```python
decision = EnrollmentDecision(
    passed=False,
    retryable=reason in RETRYABLE_REASONS,
    reason=reason,
    status="failed",
    model_id=None,
)
```

Set the current enrollment `failed` and `completed_at=now()`, then count failed enrollments completed in the last three minutes where `user_id` or `device_digest` matches the current row. When that count reaches five, set the current row `cooldown_until=now()+interval '45 minutes'`; Task 4's next-create query enforces the account/device cooldown across terminal enrollments. Do not maintain a per-enrollment failure counter because every liveness session is single-use and each failed enrollment is terminal. Call `cleanup_terminal_enrollment` after the failure commit; cleanup failure does not restore a usable state and is retried by Task 7.

- [ ] **Step 9: Remove identity-only activation**

When `FM_BIOMETRIC_ENROLLMENT_ENABLED=true`, change `POST /v1/facemarket/identity/verify` to return `409 biometric_enrollment_required` without calling OACX or updating `fm_models`. Keep the existing route behavior only while the biometric flag is false so rollout remains reversible.

Update the former success test:

```python
def test_identity_only_cannot_activate_model_when_biometrics_are_enabled(fm, make_token):
    client, store, _ = fm
    client.app.state.settings.fm_biometric_enrollment_enabled = True
    response = client.post(
        "/v1/facemarket/identity/verify",
        json={"token": "tok-1"},
        headers=_headers(make_token),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "biometric_enrollment_required"
    assert store["models"] == []
```

- [ ] **Step 10: Run completion and identity regression tests**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometrics.py tests/test_facemarket_biometric_enrollment.py tests/test_facemarket_identity.py`

Expected: PASS; error responses contain no token, raw photo, reference bytes, embedding, score, or R2 key.

- [ ] **Step 11: Commit the atomic biometric decision**

```bash
git add server/app/facemarket_enrollment.py server/app/facemarket.py server/tests/test_facemarket_biometrics.py server/tests/test_facemarket_biometric_enrollment.py server/tests/test_facemarket_identity.py
git commit -m "Activate asset building only from a live government-photo match" \
  -m "Constraint: OACX portrait and live reference may exist only during one complete request
Rejected: persist the portrait or enrollment token | either recreates a durable identity-image retrieval path
Confidence: high
Scope-risk: broad
Directive: keep identity/live and retouched/live thresholds distinct and versioned
Tested: successful completion, threshold boundaries, replay, ownership, cleanup, and identity-only rejection
Not-tested: real OACX and Rekognition responses"
```

### Task 7: Harden Terminal Cleanup and Add Expiry Sweeps

**Files:**
- Modify: `server/app/facemarket_enrollment.py`
- Modify: `server/app/workers/dispatcher.py:77-87,149-168`
- Create: `server/tests/test_facemarket_biometric_cleanup.py`

**Interfaces:**
- Consumes: terminal enrollment rows, quarantine photo metadata, `app.state.r2_face`, dispatcher's existing 60-second sweep.
- Produces: retry-tested `cleanup_terminal_enrollment`, bounded `sweep_terminal_enrollments`, redacted `raw_deletion_evidence`.

- [ ] **Step 1: Write failing cleanup idempotency tests**

```python
def test_terminal_cleanup_deletes_only_quarantine_and_records_counts(cleanup_app):
    enrollment_id = cleanup_app.add_enrollment(
        status="failed",
        photos=[
            ("front", "private/front.jpg", "quarantine"),
            ("angle45", "private/angle45.jpg", "quarantine"),
            ("side", "private/side.jpg", "approved"),
        ],
    )
    assert asyncio.run(
        cleanup_terminal_enrollment(cleanup_app.app, enrollment_id=enrollment_id)
    ) is True
    assert cleanup_app.r2.deletes == ["private/front.jpg", "private/angle45.jpg"]
    evidence = cleanup_app.enrollment(enrollment_id)["raw_deletion_evidence"]
    assert evidence["quarantineDeleted"] is True
    assert evidence["quarantineDeletedCount"] == 2
    assert "private/" not in json.dumps(evidence)


def test_cleanup_retries_only_objects_that_failed(cleanup_app):
    enrollment_id = cleanup_app.add_failed_enrollment_with_three_photos()
    cleanup_app.r2.fail_once_for.add("private/angle45.jpg")
    assert asyncio.run(
        cleanup_terminal_enrollment(cleanup_app.app, enrollment_id=enrollment_id)
    ) is False
    assert cleanup_app.remaining_angles(enrollment_id) == ["angle45"]
    assert asyncio.run(
        cleanup_terminal_enrollment(cleanup_app.app, enrollment_id=enrollment_id)
    ) is True
    assert cleanup_app.remaining_angles(enrollment_id) == []
```

- [ ] **Step 2: Run cleanup tests and confirm missing behavior**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometric_cleanup.py -k cleanup`

Expected: FAIL because Task 4's basic cleanup has no expiry sweep and no simulated partial-failure retry coverage.

- [ ] **Step 3: Implement idempotent per-enrollment cleanup**

The function must:

1. Read only rows whose enrollment status is `failed`, `cancelled`, or `expired` and photo `storage_state='quarantine'`.
2. Call `r2_face.delete` outside a database transaction.
3. Delete each successfully removed metadata row using `(enrollment_id, angle, r2_key)` so a concurrent replacement cannot be erased.
4. Leave failed rows for the next sweep.
5. Merge only counts, timestamp, and `quarantineDeleted` into `raw_deletion_evidence`.

Preserve Task 4's exact public signature and harden it with this explicit terminal/idempotency guard:

```python
async def cleanup_terminal_enrollment(app, *, enrollment_id: str) -> bool:
    terminal = {"failed", "cancelled", "expired"}
    enrollment, photos = await load_cleanup_candidate(app, enrollment_id)
    if enrollment is None or enrollment["status"] not in terminal:
        return False
    for photo in photos:
        deleted = await delete_quarantine_object(app, enrollment_id, photo)
        if deleted:
            await delete_matching_photo_row(app, enrollment_id, photo)
    remaining = await count_quarantine_rows(app, enrollment_id)
    await merge_cleanup_evidence(app, enrollment_id, remaining=remaining)
    return remaining == 0
```

Never log the failed object key. Log `enrollment_id`, `angle`, exception type, and retry count only.

- [ ] **Step 4: Write failing expiry sweep tests**

```python
def test_sweep_expires_preapproval_rows_and_retries_pending_deletes(cleanup_app):
    expired_id = cleanup_app.add_enrollment(
        status="liveness_pending",
        expires_at=UTC_NOW - timedelta(seconds=1),
        photos=[("front", "private/front.jpg", "quarantine")],
    )
    active_id = cleanup_app.add_enrollment(
        status="asset_building",
        expires_at=UTC_NOW - timedelta(seconds=1),
        photos=[("front", "private/approved-front.jpg", "approved")],
    )
    cleaned = asyncio.run(sweep_terminal_enrollments(cleanup_app.app, limit=100))
    assert cleaned == 1
    assert cleanup_app.enrollment(expired_id)["status"] == "expired"
    assert cleanup_app.enrollment(expired_id)["reason"] == "enrollment_expired"
    assert cleanup_app.enrollment(active_id)["status"] == "asset_building"
```

- [ ] **Step 5: Implement the bounded sweep and dispatcher hook**

`sweep_terminal_enrollments` first atomically marks at most 100 due pre-approval rows as expired:

```sql
with due as (
  select id from fm_biometric_enrollments
  where expires_at <= now()
    and status in ('photos_pending', 'liveness_pending', 'processing')
  order by expires_at
  for update skip locked
  limit %s
)
update fm_biometric_enrollments e
set status='expired', decision='failed', reason='enrollment_expired', completed_at=now()
from due where e.id=due.id
returning e.id::text as id
```

Then select terminal rows with incomplete `quarantineDeleted` evidence, call `cleanup_terminal_enrollment` for each, and return the number fully cleaned.

At the end of `JobDispatcher._recover_stale`, add:

```python
if getattr(self.app.state.settings, "fm_biometric_enrollment_enabled", False):
    from ..facemarket_enrollment import sweep_terminal_enrollments
    await sweep_terminal_enrollments(self.app, limit=100)
```

The existing outer dispatcher exception handler provides another attempt on the next 60-second sweep.

- [ ] **Step 6: Run cleanup and dispatcher tests**

Run: `cd server && uv run pytest -q tests/test_facemarket_biometric_cleanup.py tests/test_dispatcher.py`

If `tests/test_dispatcher.py` does not exist, run: `cd server && uv run pytest -q tests -k 'dispatcher or biometric_cleanup'`

Expected: PASS; a transient R2 delete failure leaves a retryable metadata row and never exposes its key.

- [ ] **Step 7: Commit durable deletion retries**

```bash
git add server/app/facemarket_enrollment.py server/app/workers/dispatcher.py server/tests/test_facemarket_biometric_cleanup.py
git commit -m "Finish quarantine deletion despite retries and process restarts" \
  -m "Constraint: failed, cancelled, and expired registrations may not retain retouched originals
Rejected: request-only cleanup | a process crash would permanently orphan biometric objects
Confidence: high
Scope-risk: moderate
Directive: keep cleanup bounded and idempotent; never log object keys
Tested: terminal cleanup, partial failure retry, expiry marking, and dispatcher sweep
Not-tested: multi-instance R2 fault injection"
```

### Task 8: Promote Only Enrollment-bound Assets

**Files:**
- Modify: `server/app/workers/fm_model_asset_job.py:1-171`
- Modify: `server/app/agents/identity_source.py:18-66`
- Modify: `server/tests/test_fm_model_asset_job.py`
- Modify: `server/tests/test_identity_source.py`
- Modify: `server/app/facemarket.py:363-414`

**Interfaces:**
- Consumes: `fm_model_asset_build` payload `{modelId,enrollmentId}`, exactly three `quarantine` photo rows, R2 key/copy helpers.
- Produces: approved originals, versioned `face_front`/`grid_sedcard`, `fm_models.current_enrollment_id`, asset `source_enrollment_id` and `evidence_version`, enrollment `license_pending`.

- [ ] **Step 1: Rewrite the worker fixture around enrollment photos**

Change `_job()` in `test_fm_model_asset_job.py` to:

```python
def _job():
    return {
        "id": "job-1",
        "user_id": "u1",
        "lease_token": "u1:tok",
        "payload": {
            "modelId": "11111111-1111-1111-1111-111111111111",
            "enrollmentId": "22222222-2222-2222-2222-222222222222",
        },
    }
```

Make the fake cursor return the owned enrollment with `status='asset_building'` and three rows in deliberately shuffled order: side, front, angle45.

- [ ] **Step 2: Write failing promotion and ordering tests**

```python
def test_asset_build_reads_only_enrollment_photos_and_promotes_in_contract_order():
    app, log, face_r2 = build_worker_fixture()
    asyncio.run(run_fm_model_asset_job(app, _job()))
    sql = " | ".join(" ".join(statement.split()) for statement, _ in log)
    assert "from fm_biometric_enrollment_photos" in sql
    assert "personalization_face_photos" not in sql
    assert face_r2.get_order == ["front", "angle45", "side"]
    assert [copy.destination for copy in face_r2.copies] == [
        "facemarket/models/11111111-1111-1111-1111-111111111111/"
        "enrollments/22222222-2222-2222-2222-222222222222/originals/front.png",
        "facemarket/models/11111111-1111-1111-1111-111111111111/"
        "enrollments/22222222-2222-2222-2222-222222222222/originals/angle45.png",
        "facemarket/models/11111111-1111-1111-1111-111111111111/"
        "enrollments/22222222-2222-2222-2222-222222222222/originals/side.png",
    ]


def test_asset_swap_is_bound_to_current_enrollment_and_version():
    app, log, face_r2 = build_worker_fixture(old_asset_keys=["old/front.png", "old/grid.png"])
    asyncio.run(run_fm_model_asset_job(app, _job()))
    sql = " | ".join(" ".join(statement.split()) for statement, _ in log)
    assert "source_enrollment_id" in sql
    assert "evidence_version" in sql
    assert "current_enrollment_id" in sql
    assert "status='license_pending'" in sql
    assert face_r2.deletes[-2:] == ["old/front.png", "old/grid.png"]
```

Also assert a missing `enrollmentId`, wrong owner/model binding, non-`asset_building` status, incomplete angles, or lost lease creates no approved object and does not set ready.

- [ ] **Step 3: Run worker tests and confirm legacy-query failures**

Run: `cd server && uv run pytest -q tests/test_fm_model_asset_job.py`

Expected: FAIL because the worker only accepts `modelId`, queries personalization photos, and uses stable overwrite keys.

- [ ] **Step 4: Implement enrollment-bound load and ordered composition**

Replace the initial query with one ownership-bound query:

```sql
select e.status, e.match_policy_version, p.angle, p.r2_key, p.mime_type,
       p.image_digest, p.storage_state
from fm_biometric_enrollments e
join fm_biometric_enrollment_photos p on p.enrollment_id=e.id
where e.id=%s and e.model_id=%s and e.user_id=%s
order by case p.angle when 'front' then 0 when 'angle45' then 1 when 'side' then 2 end
```

Require exactly `front`, `angle45`, `side`, all `storage_state='quarantine'`, and enrollment `asset_building`. Do not call `load_face_qc` here; biometric comparison already passed in Task 6, and repeating it would use a different point-in-time policy.

- [ ] **Step 5: Write approved originals and versioned derived assets before the DB swap**

For each photo call `R2Client.copy` from quarantine to `enrollment_original_key`. Compose with `compose_sedcard(face_bytes)` and write:

```python
derived = [
    ("grid_sedcard", grid, "image/png"),
    ("face_front", face_bytes[0], faces[0]["mime_type"]),
]
for view, data, mime in derived:
    key = model_asset_key(model_id, enrollment_id, view, ext_for_mime(mime) or "png")
    await asyncio.to_thread(r2_face.put_bytes, key, data, mime, cache=IMMUTABLE_CACHE)
```

Track every new approved/derived key in `put_keys` so lease loss or database failure deletes only this attempt's objects.

- [ ] **Step 6: Atomically swap database evidence then remove old objects**

Inside the lease-fenced transaction:

1. Lock job, enrollment, and model.
2. Read old `fm_model_assets.r2_key` values.
3. Upsert both asset rows with `source_enrollment_id=enrollment_id` and `evidence_version=match_policy_version`.
4. Update each photo to its approved key, `storage_state='approved'`, `approved_at=now()`.
5. Update model `assets_status='ready'`, `current_enrollment_id=enrollment_id`, `assets_source_hash=<digest>`, and keep status `pending` or `reverification_required`.
6. Update enrollment `status='license_pending'`, `decision='passed'`, `completed_at=now()`.
7. Mark job done with only `{modelId,enrollmentId,assetsStatus}`.

After commit, delete quarantine keys and prior asset keys. Ignore already-missing objects; if a delete fails, log only view/angle and enrollment ID. Do not clear `put_keys` until the transaction commits.

- [ ] **Step 7: Remove the manual unbound build endpoint**

When biometric enrollment is enabled, `POST /v1/facemarket/models/me/build-assets` returns `409 biometric_enrollment_required`. Keep the old behavior only while the biometric flag is false for rollback safety. The frontend button is removed in Task 10.

- [ ] **Step 8: Write failing stale-source resolver tests**

```python
def test_resolver_requires_asset_source_to_equal_current_enrollment():
    refs = asyncio.run(resolve_real_model_assets(
        FakeConn(
            model_status="verified",
            assets_status="ready",
            current_enrollment_id="enrollment-new",
            asset_source_enrollment_id="enrollment-old",
        ),
        REAL_MODEL_ID,
    ))
    assert refs is None
```

Add passing coverage where both IDs match and `evidence_version` is present.

- [ ] **Step 9: Update resolver query and run worker/resolver tests**

Select `m.status`, `m.current_enrollment_id`, `a.source_enrollment_id`, and `a.evidence_version`. Return refs only when model status is `verified`, assets are ready, both required views share the current enrollment, and both have a non-empty evidence version.

Run: `cd server && uv run pytest -q tests/test_fm_model_asset_job.py tests/test_identity_source.py`

Expected: PASS; job result/events and resolver returns never include R2 keys outside the internal refs contract.

- [ ] **Step 10: Commit evidence-bound promotion**

```bash
git add server/app/workers/fm_model_asset_job.py server/app/agents/identity_source.py server/app/facemarket.py server/tests/test_fm_model_asset_job.py server/tests/test_identity_source.py
git commit -m "Serve model assets only from the current approved enrollment" \
  -m "Constraint: retouched originals become usable only after the biometric decision and lease-fenced promotion
Rejected: stable overwrite keys | a failed rebuild can corrupt the previously approved evidence set
Confidence: high
Scope-risk: broad
Directive: every real-model asset row must carry source enrollment and evidence version
Tested: ordered promotion, rollback cleanup, atomic swap, stale source rejection, and manual build rejection
Not-tested: concurrent worker crash against real R2"
```

### Task 9: Activate Model and License Only After VC Issue

**Files:**
- Modify: `server/app/facemarket.py:417-675,1347-1433`
- Modify: `server/tests/test_facemarket_licenses.py`
- Modify: `server/app/config.py:280-287`
- Modify: `server/app/facemarket_enrollment.py`

**Interfaces:**
- Consumes: owned enrollment in `license_pending`, approved front photo, current enrollment-bound assets, mandatory Holder URL.
- Produces: JSON-only `CreateLicenseRequest`, idempotent Holder issue key, `pending`→`active` license and `pending|reverification_required`→`verified` model transition.

- [ ] **Step 1: Write failing enrollment-only license tests**

```python
def test_license_starts_pending_and_activates_only_after_vc(
    license_client, auth, holder_stub
):
    enrollment_id = license_client.store.add_license_pending_enrollment()
    response = license_client.post(
        "/v1/facemarket/licenses",
        json={
            "enrollmentId": enrollment_id,
            "allowedUse": ["일반 여성 의류"],
            "forbiddenUse": ["성인용품"],
            "unitPrice": 10000,
            "validDays": 365,
        },
        headers=auth(),
    )
    assert response.status_code == 201
    assert response.json()["status"] == "active"
    assert response.json()["vcId"] == "vc:dev:1"
    assert license_client.store.model_status == "verified"
    assert license_client.store.enrollment_status == "passed"
    assert holder_stub.idempotency_key.startswith("fm-license:")


def test_holder_failure_leaves_everything_non_active(license_client, auth, holder_stub):
    holder_stub.fail_with_status = 503
    enrollment_id = license_client.store.add_license_pending_enrollment()
    response = license_client.post(
        "/v1/facemarket/licenses",
        json=valid_license_body(enrollment_id),
        headers=auth(),
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "vc_issue_delayed"
    assert license_client.store.license_status == "pending"
    assert license_client.store.model_status != "verified"
    assert license_client.store.enrollment_status == "vc_pending"
```

Also assert foreign/stale/non-current enrollment is 404 or 409, assets not ready is 409, repeated POST reuses the same pending license ID, multipart face/profile input never creates a license, and response omits approved R2 keys.

- [ ] **Step 2: Run license tests and confirm the direct-face path fails expectations**

Run: `cd server && uv run pytest -q tests/test_facemarket_licenses.py -k 'enrollment or holder_failure or multipart'`

Expected: FAIL because the current route accepts multipart face/profile, creates active immediately, and treats VC as best-effort.

- [ ] **Step 3: Add the JSON contract and enrollment evidence lookup**

```python
class CreateLicenseRequest(CamelModel):
    enrollment_id: str
    allowed_use: list[str] = []
    forbidden_use: list[str] = []
    unit_price: int = 10000
    valid_days: int = 365
```

Validate `unit_price` in `0..100_000_000`, `valid_days` in `1..3650`, and keep `_clean_uses`. Query enrollment, model, approved front photo, and both assets in one owner-scoped statement. Require:

```text
enrollment.status == license_pending or vc_pending
model.current_enrollment_id == enrollment.id
model.assets_status == ready
both asset.source_enrollment_id == enrollment.id
front photo.storage_state == approved
```

Use the approved front key/digest for the owner-gated `face_image_uri` and VC claim; never upload or copy another face in this route.

- [ ] **Step 4: Make Holder issue return a result or raise**

Replace background best-effort scheduling with:

```python
@dataclass(frozen=True, slots=True)
class FaceVcIssueResult:
    vc_id: str
    user_did: str | None


class FaceVcIssueError(RuntimeError):
    pass


async def issue_face_vc(
    app, *, license_id: str, model_id: str, allowed: list[str],
    forbidden: list[str], unit_price: int, valid_until: datetime, digest: str
) -> FaceVcIssueResult:
    base = app.state.settings.opendid_holder_url
    if not base:
        raise FaceVcIssueError("holder_unavailable")
    headers = {"Idempotency-Key": f"fm-license:{license_id}"}
    async with httpx.AsyncClient(timeout=_HOLDER_TIMEOUT) as client:
        wallet = await client.post(
            f"{base}/holder/models/{model_id}/wallet", headers=headers
        )
        if wallet.status_code not in (200, 409):
            raise FaceVcIssueError("holder_wallet_failed")
        register = await client.post(
            f"{base}/holder/models/{model_id}/register-did", headers=headers
        )
        if register.status_code != 200:
            raise FaceVcIssueError("holder_register_failed")
        register_body = register.json()
        user_did = register_body.get("userDid")
        if not register_body.get("flowAComplete") and not user_did:
            raise FaceVcIssueError("holder_register_incomplete")
        issue = await client.post(
            f"{base}/holder/models/{model_id}/issue-vc",
            headers=headers,
            json={"plan": "facelicense", "claims": build_face_vc_claims(
                allowed=allowed,
                forbidden=forbidden,
                unit_price=unit_price,
                valid_until=valid_until,
                digest=digest,
            )},
        )
        if issue.status_code != 200:
            raise FaceVcIssueError("holder_issue_failed")
        issue_body = issue.json()
        vc_id = issue_body.get("vcId")
        if not vc_id:
            raise FaceVcIssueError("holder_issue_incomplete")
        return FaceVcIssueResult(
            vc_id=vc_id,
            user_did=issue_body.get("userDid") or user_did,
        )
```

Send `Idempotency-Key: fm-license:<license_id>` to Holder register/issue calls. Do not log Holder response bodies or VC claims. Missing Holder URL raises `FaceVcIssueError` while biometric enrollment is enabled. Production activation remains blocked until the Holder hardening plan enforces this idempotency key and mandatory verify behavior.

- [ ] **Step 5: Implement pending creation and atomic activation**

Within the first transaction, insert or load the unique `fm_licenses.enrollment_id` row with `status='pending'`, set enrollment `vc_pending`, and commit. Await `issue_face_vc`. On error return `502 vc_issue_delayed` without activating anything.

On success, lock license, enrollment, and model, recheck current enrollment, then atomically:

```sql
update fm_licenses
set status='active', vc_id=%s
where id=%s and status='pending';
update fm_models
set status='verified', did=coalesce(nullif(did, ''), %s)
where id=%s and current_enrollment_id=%s;
update fm_biometric_enrollments
set status='passed', decision='passed', vc_id=%s, completed_at=now()
where id=%s and status='vc_pending';
```

Return the active `LicenseCard`. A retry after Holder success but before database commit uses the same license ID/idempotency key and obtains the same VC.

- [ ] **Step 6: Add Holder to the biometric startup gate**

Extend `validate_biometric_settings`:

```python
if not settings.opendid_holder_url:
    raise RuntimeError("OPENDID_HOLDER_URL is required for biometric enrollment")
```

Keep `FM_BIOMETRIC_ENROLLMENT_ENABLED=false` in production until the separate Holder runtime plan supplies authenticated, idempotent issue and mandatory verify.

- [ ] **Step 7: Run license and enrollment regressions**

Run: `cd server && uv run pytest -q tests/test_facemarket_licenses.py tests/test_facemarket_biometric_enrollment.py tests/test_facemarket_identity.py`

Expected: PASS; no route can create an active real-model license without a VC ID and current enrollment-bound assets.

- [ ] **Step 8: Commit activation ordering**

```bash
git add server/app/facemarket.py server/app/config.py server/app/facemarket_enrollment.py server/tests/test_facemarket_licenses.py
git commit -m "Make VC issuance the final activation boundary" \
  -m "Constraint: model and license must remain non-active when Holder is absent or fails
Rejected: background best-effort issuance | it exposes real models before credential issuance is proven
Confidence: medium
Scope-risk: broad
Directive: production enablement depends on authenticated idempotent Holder issue and mandatory verify
Tested: enrollment-only input, pending failure, idempotent retry, and atomic activation tests
Not-tested: deployed Holder idempotency behavior"
```

### Task 10: Build the Enrollment Wizard and Correct the Angle Order

**Files:**
- Create: `src/features/model/biometricEnrollment.js`
- Create: `src/features/model/FaceLivenessStep.jsx`
- Create: `tests/frontend/facemarket-biometric-enrollment.test.mjs`
- Modify: `package.json`
- Modify: `pnpm-lock.yaml`
- Modify: `src/lib/api/facemarket.js:27-81`
- Modify: `src/features/model/ModelRegister.jsx:13-219`
- Modify: `src/features/model/ModelFaceUpload.jsx:13-224`
- Modify: `src/features/model/ModelLicense.jsx:26-67,368-470,620-638`
- Modify: `src/features/model/ModelHub.jsx:9-113,176-211`
- Modify: `src/App.jsx:184-216,579-594`
- Modify: `src/features/model/ModelPersonalization.module.css`
- Modify: `src/features/model/ModelRegister.module.css`

**Interfaces:**
- Consumes: enrollment APIs, Amplify `FaceLivenessDetectorCore`, temporary credential response, existing OACX widget loader, `toUploadableImage`.
- Produces: resumable consent→photos→liveness→OACX complete→license terms UX with `front`→`angle45`→`side` rendering.

- [ ] **Step 1: Write failing pure flow tests**

```javascript
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ENROLLMENT_ANGLES,
  enrollmentReasonMessage,
  nextEnrollmentStep,
} from '../../src/features/model/biometricEnrollment.js';

test('retouched photos are presented front, 45 degrees, then side', () => {
  assert.deepEqual(ENROLLMENT_ANGLES.map(({ value }) => value), [
    'front', 'angle45', 'side',
  ]);
  assert.equal(ENROLLMENT_ANGLES[1].label, '45도');
  assert.equal(ENROLLMENT_ANGLES[2].label, '측면');
});

test('server status restores the next safe enrollment step', () => {
  assert.equal(nextEnrollmentStep({ status: 'photos_pending', photos: [] }), 'photos');
  assert.equal(nextEnrollmentStep({ status: 'liveness_pending', photos: [{}, {}, {}] }), 'liveness');
  assert.equal(nextEnrollmentStep({ status: 'asset_building', photos: [{}, {}, {}] }), 'processing');
  assert.equal(nextEnrollmentStep({ status: 'license_pending', photos: [{}, {}, {}] }), 'terms');
  assert.equal(nextEnrollmentStep({ status: 'passed', photos: [{}, {}, {}] }), 'done');
});

test('raw biometric reasons collapse to actionable copy', () => {
  assert.equal(enrollmentReasonMessage('id_portrait_unavailable'), '신분증 사진을 확인할 수 없어요.');
  assert.equal(enrollmentReasonMessage('face_match_failed'), '얼굴 일치 확인에 실패했어요.');
  assert.equal(enrollmentReasonMessage('unknown-provider-detail'), '인증을 완료하지 못했어요. 다시 시도해 주세요.');
});
```

- [ ] **Step 2: Run the test and confirm the missing module**

Run: `node --test tests/frontend/facemarket-biometric-enrollment.test.mjs`

Expected: FAIL with module-not-found for `biometricEnrollment.js`.

- [ ] **Step 3: Implement the pure UI contract**

```javascript
export const ENROLLMENT_ANGLES = Object.freeze([
  { value: 'front', label: '정면', guide: '정면을 바라보고 얼굴 전체가 나오게 찍어주세요.' },
  { value: 'angle45', label: '45도', guide: '정면에서 약 45도만 돌려 반측면이 보이게 찍어주세요.' },
  { value: 'side', label: '측면', guide: '고개를 약 90도 돌려 옆모습 윤곽이 보이게 찍어주세요.' },
]);

const REASON_COPY = Object.freeze({
  id_portrait_unavailable: '신분증 사진을 확인할 수 없어요.',
  liveness_retry: '라이브 인증을 새 세션에서 다시 시도해 주세요.',
  liveness_failed: '라이브 인증을 통과하지 못했어요.',
  face_match_failed: '얼굴 일치 확인에 실패했어요.',
  qc_unavailable: '얼굴 검사를 지금 수행할 수 없어요.',
  identity_recovery_required: '기존 모델 소유권 확인이 필요해요.',
  vc_issue_delayed: 'VC 발급이 지연되고 있어요. 잠시 후 다시 시도해 주세요.',
});

export function enrollmentReasonMessage(reason) {
  return REASON_COPY[reason] || '인증을 완료하지 못했어요. 다시 시도해 주세요.';
}

export function nextEnrollmentStep(enrollment) {
  if (!enrollment) return 'consent';
  if (enrollment.status === 'photos_pending') return 'photos';
  if (enrollment.status === 'liveness_pending') return 'liveness';
  if (enrollment.status === 'asset_building') return 'processing';
  if (enrollment.status === 'license_pending' || enrollment.status === 'vc_pending') return 'terms';
  if (enrollment.status === 'passed') return 'done';
  return 'failed';
}
```

- [ ] **Step 4: Install the official liveness packages**

Run: `pnpm add @aws-amplify/ui-react-liveness @aws-amplify/ui-react aws-amplify`

Expected: `package.json` and `pnpm-lock.yaml` contain all three direct dependencies and no backend package changes.

- [ ] **Step 5: Add exact enrollment API functions**

In `src/lib/api/facemarket.js`, add:

```javascript
export function createEnrollment({ documentVersion, deviceId }) {
  return http('/v1/facemarket/enrollments', {
    method: 'POST',
    body: {
      biometricConsent: { accepted: true, documentVersion },
      deviceId,
    },
  });
}

export function getCurrentEnrollment() {
  return http('/v1/facemarket/enrollments/current');
}

export function getEnrollment(id) {
  return http(`/v1/facemarket/enrollments/${id}`);
}

export async function uploadEnrollmentPhoto({ enrollmentId, angle, fileBlob, filename }) {
  const form = new FormData();
  form.append('angle', angle);
  form.append('photo', fileBlob, filename || 'face');
  return checkedJson(await _authFetch(
    `/v1/facemarket/enrollments/${enrollmentId}/photos`,
    { method: 'POST', body: form },
  ));
}

export function deleteEnrollmentPhoto(enrollmentId, angle) {
  return http(`/v1/facemarket/enrollments/${enrollmentId}/photos/${angle}`, { method: 'DELETE' });
}

export function createLivenessSession(enrollmentId, nonce) {
  return http(`/v1/facemarket/enrollments/${enrollmentId}/liveness-session`, {
    method: 'POST', body: { nonce },
  });
}

export function completeEnrollment(enrollmentId, { sessionId, token }) {
  return http(`/v1/facemarket/enrollments/${enrollmentId}/complete`, {
    method: 'POST', body: { sessionId, token },
  });
}

export function createLicense({ enrollmentId, allowedUse, forbiddenUse, unitPrice, validDays }) {
  return http('/v1/facemarket/licenses', {
    method: 'POST',
    body: { enrollmentId, allowedUse, forbiddenUse, unitPrice, validDays },
  });
}
```

Factor the existing error-envelope parsing into `checkedJson(response)` and reuse it for multipart enrollment upload. Do not put session ID, credentials, token, or image bytes in console output.

- [ ] **Step 6: Make `ModelFaceUpload` accept a photo adapter and use the corrected order**

Change the component signature to:

```javascript
export function ModelFaceUpload({
  embedded = false,
  onDone,
  photoApi = personalizationPhotoApi,
  angles = ENROLLMENT_ANGLES,
  nextLabel = '다음 · 신체 정보',
})
```

`photoApi` has exact methods `load()`, `upload({angle,fileBlob,filename})`, `remove(angle)`, and optional `fetchUrl(imageUri)`. Keep HEIC conversion. Replace the local `ANGLES` constant and every iteration/count with `angles`. The default personalization adapter preserves existing APIs; `ModelRegister` supplies the enrollment adapter, whose `load` reads `getEnrollment(id).photos` and has no image fetch.

- [ ] **Step 7: Implement `FaceLivenessStep` with custom credentials**

```jsx
import { useCallback, useMemo } from 'react';
import { FaceLivenessDetectorCore } from '@aws-amplify/ui-react-liveness';
import '@aws-amplify/ui-react/styles.css';

export function FaceLivenessStep({ session, onAnalysisComplete, onCancel, onError }) {
  const credentialProvider = useCallback(async () => ({
    accessKeyId: session.credentials.accessKeyId,
    secretAccessKey: session.credentials.secretAccessKey,
    sessionToken: session.credentials.sessionToken,
    expiration: new Date(session.credentials.expiration),
  }), [session]);
  const config = useMemo(() => ({ credentialProvider }), [credentialProvider]);
  return (
    <FaceLivenessDetectorCore
      sessionId={session.sessionId}
      region="us-east-1"
      config={config}
      onAnalysisComplete={onAnalysisComplete}
      onUserCancel={onCancel}
      onError={onError}
    />
  );
}
```

Never cache `session` in localStorage or a global store. An error/cancel discards it and calls the backend for a new session with a new `crypto.randomUUID()` nonce.

- [ ] **Step 8: Convert `ModelRegister` into the resumable biometric wizard**

On mount, call `getCurrentEnrollment`; 404 shows consent, otherwise restore using `nextEnrollmentStep`. Generate and persist only an opaque browser device identifier under `wearless.fmDeviceId`; never persist OACX token, AWS credentials, or session ID.

The action sequence is exact:

1. Consent button calls `createEnrollment({documentVersion:'2026-08-v1',deviceId})`.
2. Photo step renders `ModelFaceUpload` with enrollment adapter and corrected angle order.
3. Liveness step calls `createLivenessSession(enrollment.id, crypto.randomUUID())` immediately before rendering `FaceLivenessStep`.
4. `onAnalysisComplete` opens the existing OACX `ENT_MID` widget and wraps its callback in a Promise.
5. The callback sends `{sessionId,token}` once to `completeEnrollment`; token remains only in callback scope.
6. On 202, poll `getEnrollment(id)` every 2.5 seconds for at most 120 seconds until `license_pending` or `failed`.
7. `license_pending` links to `/model/license?step=terms&enrollment=<id>`.

Change completion copy from “검증 모델로 등록 완료” to “생체 확인 완료 · 라이선스 발급 대기”. Only a later active license/model may show “검증 완료”.

- [ ] **Step 9: Bind terms to enrollment and remove manual build UI**

In `ModelLicense`, read the `enrollment` query parameter, remove `profileId` from `TermsStep`, and call `createLicense({enrollmentId,...})`. Disable submission without an enrollment in `license_pending|vc_pending` and show the generalized status message.

In `ModelHub`, remove `buildMyModelAssets`, `building`, `pollRef`, `onBuildAssets`, and the manual build button. Display server model/enrollment status only.

- [ ] **Step 10: Adjust route guards without opening generation**

Rename the current wrapper to `RequireOwnedModel` and allow any owned model status into hub/license routes. Add a separate `RequireVerifiedModel` around only `/model/generate`; it still requires `status==='verified'`. `/model/register` remains available for initial and re-verification enrollment.

- [ ] **Step 11: Add source-wiring assertions and run frontend tests**

Extend the Node test to read source files and assert:

```javascript
assert.match(registerSource, /completeEnrollment\(enrollment\.id, \{ sessionId, token \}\)/);
assert.doesNotMatch(registerSource, /localStorage\.setItem\([^)]*(token|session|credentials)/i);
assert.match(livenessSource, /FaceLivenessDetectorCore/);
assert.match(livenessSource, /region="us-east-1"/);
assert.match(livenessSource, /config=\{config\}/);
assert.doesNotMatch(hubSource, /buildMyModelAssets/);
assert.match(licenseSource, /enrollmentId/);
```

Run: `node --test tests/frontend/facemarket-biometric-enrollment.test.mjs`

Expected: PASS.

- [ ] **Step 12: Build the frontend and run all frontend tests**

Run: `pnpm test:frontend`

Expected: PASS.

Run: `pnpm build`

Expected: Vite production build succeeds with Amplify liveness bundled and no unresolved CSS or peer dependency error.

- [ ] **Step 13: Commit the end-to-end browser flow**

```bash
git add package.json pnpm-lock.yaml src/lib/api/facemarket.js src/features/model/biometricEnrollment.js src/features/model/FaceLivenessStep.jsx src/features/model/ModelRegister.jsx src/features/model/ModelFaceUpload.jsx src/features/model/ModelLicense.jsx src/features/model/ModelHub.jsx src/App.jsx src/features/model/ModelPersonalization.module.css src/features/model/ModelRegister.module.css tests/frontend/facemarket-biometric-enrollment.test.mjs
git commit -m "Guide models through one resumable biometric enrollment" \
  -m "Constraint: AWS credentials and OACX token remain in component memory and photo labels follow front-angle45-side
Rejected: reuse the old verified-model route guard | pending models must reach license terms without gaining generation access
Confidence: high
Scope-risk: broad
Directive: every liveness retry creates a fresh AWS session and nonce
Tested: frontend flow unit tests, full frontend test suite, and Vite production build
Not-tested: physical mobile camera flow"
```

### Task 11: Verify the Complete Dark-launched Slice

**Files:**
- Modify only if a verification failure exposes a defect in the files owned by Tasks 1–10.

**Interfaces:**
- Consumes: all preceding task outputs and the approved design.
- Produces: fresh backend/frontend evidence and a written production stop decision.

- [ ] **Step 1: Run the focused biometric backend suite**

Run:

```bash
cd server
uv run pytest -q \
  tests/test_facemarket_biometric_migration.py \
  tests/test_facemarket_biometrics.py \
  tests/test_facemarket_biometric_enrollment.py \
  tests/test_facemarket_biometric_cleanup.py \
  tests/test_face_qc.py \
  tests/test_facemarket_identity.py \
  tests/test_facemarket_licenses.py \
  tests/test_fm_model_asset_job.py \
  tests/test_identity_source.py
```

Expected: PASS; only Docker-weight and optional database cases may SKIP with their existing explicit reasons.

- [ ] **Step 2: Run the complete backend regression suite**

Run: `cd server && uv run pytest -q`

Expected: PASS with zero newly skipped or xfailed biometric cases.

- [ ] **Step 3: Run frontend tests and production build again**

Run: `pnpm test:frontend && pnpm build`

Expected: both commands exit 0.

- [ ] **Step 4: Verify dependency and deployment contracts**

Run:

```bash
rg -n 'boto3|aws-amplify|ui-react-liveness|ui-react' server/pyproject.toml server/uv.lock package.json pnpm-lock.yaml
rg -n 'FM_BIOMETRIC_ENROLLMENT_ENABLED|FM_OACX_CONTRACT_MODE|FM_LIVENESS_REGION|FM_LIVENESS_BROWSER_ROLE_ARN' copilot/api/manifest.yml
```

Expected:

- one existing backend boto3 dependency, no second AWS Python SDK;
- three direct frontend Amplify dependencies locked by pnpm;
- production feature flag is exactly `"false"`;
- OACX contract mode is `disabled`;
- liveness region is `us-east-1`;
- browser role ARN is a secret reference, not a literal credential.

- [ ] **Step 5: Scan for raw-biometric leakage and prohibited fallback**

Run:

```bash
rg -n 'logger\.(info|warning|error|exception).*?(token|portrait|reference|embedding|score|r2_key)' server/app/facemarket_enrollment.py server/app/workers/fm_model_asset_job.py
rg -n 'OutputConfig|AuditImagesLimit|pairwise_min_similarity|personalization_face_photos' server/app/facemarket_enrollment.py server/app/workers/fm_model_asset_job.py
```

Expected:

- first command returns no sensitive logging statement;
- `OutputConfig` is absent from application code;
- `AuditImagesLimit` appears only with value `0`;
- completion/worker code does not use `pairwise_min_similarity` or `personalization_face_photos`.

- [ ] **Step 6: Confirm cross-plan ownership and production stop conditions**

Run:

```bash
rg -n 'fm_vc_revocation_jobs|fm_cutover_batches|previous_status' supabase/migrations/20260821000000_facemarket_biometric_runtime.sql
rg -n 'FM_BIOMETRIC_ENROLLMENT_ENABLED: "false"' copilot/api/manifest.yml
```

Expected: the first command has no matches because revocation queue, previous-status preservation, and cutover batch belong to the runtime/cutover plan; the second has one match.

Do not enable production until all of these external checks have written evidence:

1. provider-reviewed OACX response fixture fixes portrait field path, encoding, maximum bytes, and TTL;
2. IAM task role can call Create/Get and assume the 15-minute Start-only browser role;
3. AWS Organizations AI services opt-out and privacy/legal approvals are recorded;
4. real mobile browsers complete Face Liveness in `us-east-1`;
5. ID/live, retouched/live, and liveness thresholds have a versioned gold-set report;
6. Holder honors issue idempotency and mandatory issue/verify/revoke survives restart;
7. adjacent runtime plan proves no real→virtual/faceless fallback and correct credit refund races.

- [ ] **Step 7: Commit only verification-driven corrections**

If Steps 1–6 required code corrections, stage only those files and use:

```bash
git commit -m "Close biometric enrollment verification gaps" \
  -m "Constraint: dark launch requires fresh backend, frontend, privacy, and deployment evidence
Confidence: high
Scope-risk: narrow
Directive: keep production disabled until every external gate has written evidence
Tested: full pytest, frontend tests, Vite build, leakage scan, and ownership scan
Not-tested: external provider and production infrastructure gates listed in the plan"
```

If no correction was needed, do not create an empty commit.
