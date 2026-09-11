# FaceMarket 신분증 촬영 + 간편인증 분기 + 관리자 육안 심사 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 모바일 신분증이 없는 사용자도 간편인증 + 신분증 촬영으로 FaceMarket 모델 등록을 마칠 수 있게 하고, 그 경로의 얼굴 대조를 관리자 육안 심사로 차단·승인한다.

**Architecture:** 등록 위저드에 인증 수단 분기(`mid` / `simple_auth`)를 넣는다. `simple_auth` 경로는 본인인증보다 **먼저** 신분증을 촬영·마스킹해 서버에 올리고, 그 얼굴이 기존 OACX 신분증 초상(`dlphotoimage`)의 SFace 앵커 자리를 대신한다. 촬영본은 위조 검증이 없으므로 SFace 점수는 advisory 로만 쓰고, 사진 업로드 직후 `review_pending` 으로 멈춰 관리자가 육안 승인해야 자산 빌드로 넘어간다. 기존 `mid` 경로는 코드·임계·동작 전부 무변경이며, 플래그 한 줄(`FM_IDENTITY_METHODS=mid`)로 신규 경로 전체가 사라진다.

**Tech Stack:** FastAPI + psycopg(async) + Supabase Postgres, Cloudflare R2(boto3), OpenCV YuNet/SFace, React + Vite, OmniOne CX(OACX) 위젯

**Spec:** `docs/superpowers/specs/2026-09-11-facemarket-id-capture-and-admin-review.md`

## Global Constraints

- **작업 위치**: worktree `~/devs/wearless_studio-id-capture`, 브랜치 `feat/fm-id-capture-branch` (origin/main 기준). 메인 트리는 건드리지 않는다.
- **테스트 실행**: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest <path> -q` (worktree 에 `.venv` 가 없으므로 메인 트리 인터프리터를 쓴다. 검증됨)
- **프론트 빌드**: `cd ~/devs/wearless_studio-id-capture && pnpm build`
- **기존 `mid` 경로 동작 불변**: 임계값(`FM_RETOUCHED_LIVE_THRESHOLD=0.15`, `FM_SIDE_LIVE_THRESHOLD=0.10`, `FM_ID_LIVE_THRESHOLD=0.25`), SFace enforce, `prod-dlphoto-v1` 계약을 바꾸지 않는다.
- **`FM_LIVENESS_ENABLED=false` 전제**: prod 는 라이브니스가 꺼져 있다. 새 경로도 라이브니스를 쓰지 않는다.
- **신규 플래그 기본값은 전부 off**: `FM_IDENTITY_METHODS=mid`, `FM_ENROLLMENT_REVIEW=simple_auth_only`, `FM_OACX_SIMPLE_AUTH_CONTRACT=disabled`.
- **마이그레이션은 additive·PG16-safe**: 컬럼은 `add column if not exists`, CHECK 제약은 `drop constraint if exists` → `add constraint` 관용구(선례 `supabase/migrations/20260824000000_facemarket_identity_first_reorder.sql`).
- **원시 PII 미저장**: CI 는 HMAC(`ci_hash`)만, 신분증 촬영본은 마스킹본만, 파기 시각은 `id_document_purged_at` 에 기록. 예외 메시지·로그에 `ci`·`birth` 원문을 싣지 않는다.
- **관리자 라우트는 전부 `_require_admin(conn, user_id, request)`** 를 탄다(기기 게이트 포함). 승인·거절은 `admin_guard.write_audit` 로 감사 기록을 남긴다.
- **상태 문자열**: `id_capture_pending`, `review_pending` (신규) / `identity_pending`, `photos_pending`, `processing`, `asset_building`, `license_pending`, `vc_pending`, `passed`, `failed`, `cancelled`, `expired` (기존).
- **인증 수단 문자열**: `"mid"` | `"simple_auth"`. 계약 버전 문자열: `"prod-dlphoto-v1"` | `"simple-auth-v1"`.
- **커밋 메시지**: 한국어 본문, Conventional Commits 접두어. 말미에 아래 두 줄을 붙인다.
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
  ```

---

## File Structure

**신규 파일**

| 파일 | 책임 |
| --- | --- |
| `supabase/migrations/20260911140000_facemarket_id_capture_review.sql` | 상태 enum 확장, 인증수단·신분증·심사 컬럼 추가, partial unique index 재생성 |
| `server/app/facemarket_id_document.py` | 신분증 촬영본 수용·검증·얼굴 크롭·파기. 업로드 라우트와 파기 헬퍼가 여기 산다 |
| `server/app/facemarket_admin_review.py` | 관리자 심사 API 5종(목록·카드·이미지·승인·거절) |
| `server/tests/test_facemarket_id_capture_migration.py` | 마이그레이션 SQL 검증 |
| `server/tests/test_facemarket_id_document.py` | 신분증 업로드·크롭·파기 |
| `server/tests/test_facemarket_identity_method.py` | 인증수단 분기·간편인증 계약 어댑터 |
| `server/tests/test_facemarket_admin_review.py` | 심사 API·권한·감사 |
| `src/features/model/IdentityMethodStep.jsx` | 인증 수단 선택 화면 |
| `src/features/model/IdDocumentStep.jsx` | 신분증 촬영·마스킹·업로드 화면 |
| `src/features/admin/AdminEnrollmentReview.jsx` | 관리자 심사 콘솔 |

**수정 파일**

| 파일 | 변경 |
| --- | --- |
| `server/app/config.py` | 신규 플래그 3개 |
| `server/app/r2.py` | `enrollment_id_document_key()` |
| `server/app/cx_identity.py` | `SIMPLE_AUTH_CONTRACT`, `get_oacx_biometric_contract(method=)` |
| `server/app/facemarket_enrollment.py` | `identityMethod` 분기, `review_pending` 전이, advisory 매칭 |
| `server/app/main.py` | 신규 라우터 2개 등록 |
| `src/features/model/biometricEnrollment.js` | 신규 상태 → 스텝 매핑 |
| `src/features/model/ModelRegister.jsx` | 분기 스텝 배선 |
| `src/lib/api/facemarket.js` | 신규 엔드포인트 4종 |
| `src/features/admin/AdminShell.jsx` | 심사 탭 추가 |
| `copilot/api/manifest.yml` | 플래그 기본값 |
| `documents/legal/03_facemarket_privacy_policy_v1.md`, `00_facemarket_legal_notice_map_v1.md`, `facemarket_apply_faq.md` | 신분증 촬영 고지 |

---

## Task 1: 마이그레이션 — 상태·컬럼·인덱스

**Files:**
- Create: `supabase/migrations/20260911140000_facemarket_id_capture_review.sql`
- Test: `server/tests/test_facemarket_id_capture_migration.py`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces: `fm_biometric_enrollments` 컬럼 `identity_method`, `id_document_r2_key`, `id_document_type`, `id_document_uploaded_at`, `id_document_purged_at`, `review_status`, `reviewed_by`, `reviewed_at`, `review_reason`, `match_scores`. status 값 `id_capture_pending`, `review_pending`.

- [ ] **Step 1: 실패하는 테스트 작성**

`server/tests/test_facemarket_id_capture_migration.py`:

```python
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260911140000_facemarket_id_capture_review.sql"
)


def _sql():
    return " ".join(MIGRATION.read_text().split()).lower()


def test_status_check_includes_new_states():
    sql = _sql()
    assert "id_capture_pending" in sql
    assert "review_pending" in sql
    assert "drop constraint if exists" in sql


def test_active_index_includes_new_states():
    sql = _sql()
    # partial unique index 를 재생성하며 두 신규 상태를 활성 집합에 넣어야
    # 심사 대기 중인 사용자가 등록을 하나 더 만들지 못한다.
    idx = sql[sql.index("fm_biometric_active_per_user"):]
    assert "id_capture_pending" in idx
    assert "review_pending" in idx


def test_adds_method_document_and_review_columns():
    sql = _sql()
    for col in (
        "identity_method", "id_document_r2_key", "id_document_type",
        "id_document_uploaded_at", "id_document_purged_at",
        "review_status", "reviewed_by", "reviewed_at", "review_reason",
        "match_scores",
    ):
        assert col in sql, col


def test_identity_method_defaults_to_mid():
    sql = _sql()
    assert "identity_method text default 'mid'" in sql


def test_default_status_unchanged():
    # 기본 시작 상태는 여전히 identity_pending 이다(mid 경로 불변).
    # simple_auth 는 라우트가 명시적으로 id_capture_pending 을 넣는다.
    sql = _sql()
    assert "alter column status set default" not in sql


def test_no_raw_pii_columns():
    sql = _sql()
    for forbidden in ("portrait", "embedding", "raw_ci", "rrn", "ssn"):
        assert forbidden not in sql, forbidden
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_id_capture_migration.py -q`
Expected: FAIL — `FileNotFoundError` (마이그레이션 파일 없음)

- [ ] **Step 3: 마이그레이션 작성**

`supabase/migrations/20260911140000_facemarket_id_capture_review.sql`:

```sql
-- FaceMarket 간편인증 경로: 신분증 촬영 수용 + 관리자 육안 심사.
-- Additive·PG16-safe. 기존 mid 경로 동작은 건드리지 않는다(status 기본값 유지).

-- 1) status CHECK 에 id_capture_pending(경로 S 시작) + review_pending(심사 대기) 추가.
alter table public.fm_biometric_enrollments
  drop constraint if exists fm_biometric_enrollments_status_check;
alter table public.fm_biometric_enrollments
  add constraint fm_biometric_enrollments_status_check
  check (status in (
    'id_capture_pending', 'identity_pending', 'photos_pending', 'review_pending',
    'liveness_pending', 'processing', 'asset_building', 'license_pending',
    'vc_pending', 'passed', 'failed', 'cancelled', 'expired'
  ));

-- 2) "유저당 활성 등록 1개" 인덱스에 두 신규 상태를 넣는다. 빠뜨리면 심사 대기 중인
--    사용자가 등록을 하나 더 만들어 같은 CI 로 두 모델이 생긴다.
drop index if exists public.fm_biometric_active_per_user;
create unique index if not exists fm_biometric_active_per_user
  on public.fm_biometric_enrollments(user_id)
  where status in ('id_capture_pending', 'identity_pending', 'photos_pending',
                   'review_pending', 'liveness_pending', 'processing',
                   'asset_building', 'license_pending', 'vc_pending');

-- 3) 인증 수단 + 신분증 촬영본 + 심사 결과. 전부 nullable/기본값이라 기존 행은 무영향.
--    identity_contract_version(기존)이 계약 버전을 이미 담으므로 별도 컬럼을 만들지 않는다.
alter table public.fm_biometric_enrollments
  add column if not exists identity_method text default 'mid',
  add column if not exists id_document_r2_key text,
  add column if not exists id_document_type text,
  add column if not exists id_document_uploaded_at timestamptz,
  add column if not exists id_document_purged_at timestamptz,
  add column if not exists review_status text,
  add column if not exists reviewed_by uuid,
  add column if not exists reviewed_at timestamptz,
  add column if not exists review_reason text,
  add column if not exists match_scores jsonb;

-- 4) 심사 큐 조회용 부분 인덱스.
create index if not exists fm_biometric_review_queue
  on public.fm_biometric_enrollments(created_at desc)
  where review_status = 'pending';
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_id_capture_migration.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
cd ~/devs/wearless_studio-id-capture
git add supabase/migrations/20260911140000_facemarket_id_capture_review.sql server/tests/test_facemarket_id_capture_migration.py
git commit -m "$(cat <<'EOF'
feat(facemarket): 신분증 촬영·관리자 심사용 상태와 컬럼 추가

id_capture_pending(간편인증 경로 시작)과 review_pending(심사 대기)을 상태
enum 과 활성 등록 partial unique index 양쪽에 넣는다. 인덱스를 빠뜨리면 심사
대기 중인 사용자가 등록을 하나 더 만들어 같은 CI 로 모델이 둘 생긴다.

status 기본값은 identity_pending 그대로 둔다 — mid 경로는 무변경이고
simple_auth 만 라우트가 id_capture_pending 을 명시한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 2: 설정 플래그 3개

**Files:**
- Modify: `server/app/config.py:320` 부근(설정 필드), `server/app/config.py:683` 부근(env 파서)
- Test: `server/tests/test_facemarket_identity_method.py`

**Interfaces:**
- Consumes: 없음
- Produces: `Settings.fm_identity_methods: tuple[str, ...]`, `Settings.fm_enrollment_review: str`, `Settings.fm_oacx_simple_auth_contract: str`

- [ ] **Step 1: 실패하는 테스트 작성**

`server/tests/test_facemarket_identity_method.py` (신규 파일, 첫 블록):

```python
import pytest

from app.config import load_settings


def _settings(monkeypatch, **env):
    for key in (
        "FM_IDENTITY_METHODS", "FM_ENROLLMENT_REVIEW", "FM_OACX_SIMPLE_AUTH_CONTRACT",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return load_settings()


def test_identity_methods_default_is_mid_only(monkeypatch):
    settings = _settings(monkeypatch)
    assert settings.fm_identity_methods == ("mid",)


def test_identity_methods_parses_csv(monkeypatch):
    settings = _settings(monkeypatch, FM_IDENTITY_METHODS="mid,simple_auth")
    assert settings.fm_identity_methods == ("mid", "simple_auth")


def test_identity_methods_rejects_unknown_value(monkeypatch):
    # 오타가 조용히 통과해 인증 수단이 통째로 사라지는 일을 막는다.
    settings = _settings(monkeypatch, FM_IDENTITY_METHODS="mid,bogus")
    assert settings.fm_identity_methods == ("mid",)


def test_review_default_is_simple_auth_only(monkeypatch):
    settings = _settings(monkeypatch)
    assert settings.fm_enrollment_review == "simple_auth_only"


def test_simple_auth_contract_default_is_disabled(monkeypatch):
    settings = _settings(monkeypatch)
    assert settings.fm_oacx_simple_auth_contract == "disabled"
```

> 설정 팩토리는 `server/app/config.py:515` 의 `load_settings()` 다. `_flag(env, default, allowed)` 헬퍼는 `:479` 에 이미 있다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_identity_method.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'fm_identity_methods'`

- [ ] **Step 3: 설정 추가**

`server/app/config.py` 의 `Settings` 데이터클래스에 `fm_oacx_contract_mode` 바로 아래:

```python
    # 등록 위저드에 노출할 본인인증 수단. mid=모바일 신분증(OACX ENT_MID),
    # simple_auth=간편인증(ENT_SIMPLE_AUTH)+신분증 촬영. 기본은 mid 하나 —
    # 해커톤 발표 때 이 한 줄로 신규 경로 전체가 사라진다.
    fm_identity_methods: tuple[str, ...] = ("mid",)
    # 관리자 육안 심사 적용 범위. simple_auth_only = 촬영 신분증 경로만 사람이 본다
    # (mid 는 OACX VC 서명검증이 있어 기계 대조로 충분).
    fm_enrollment_review: str = "simple_auth_only"  # off | simple_auth_only | all
    # 간편인증 응답 스키마가 실거래로 확정되기 전에는 disabled — 호출 자체가 막힌다.
    fm_oacx_simple_auth_contract: str = "disabled"  # disabled | simple-auth-v1
```

env 파서(`fm_oacx_contract_mode=...` 줄 아래):

```python
        fm_identity_methods=_identity_methods_env(),
        fm_enrollment_review=_flag(
            "FM_ENROLLMENT_REVIEW", "simple_auth_only",
            {"off", "simple_auth_only", "all"},
        ),
        fm_oacx_simple_auth_contract=_flag(
            "FM_OACX_SIMPLE_AUTH_CONTRACT", "disabled",
            {"disabled", "simple-auth-v1"},
        ),
```

모듈 레벨 헬퍼 (`_flag` 정의 근처):

```python
_IDENTITY_METHODS = ("mid", "simple_auth")


def _identity_methods_env() -> tuple[str, ...]:
    """FM_IDENTITY_METHODS 를 순서 있는 튜플로. 알 수 없는 값은 버린다.

    오타 하나가 인증 수단을 통째로 지워 등록이 아예 불가능해지는 걸 막는다 —
    남는 게 없으면 ("mid",) 로 되돌린다.
    """
    raw = os.getenv("FM_IDENTITY_METHODS", "mid")
    picked = tuple(
        m for m in _IDENTITY_METHODS
        if m in {part.strip() for part in raw.split(",") if part.strip()}
    )
    return picked or ("mid",)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_identity_method.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add server/app/config.py server/tests/test_facemarket_identity_method.py
git commit -m "$(cat <<'EOF'
feat(facemarket): 인증 수단·심사 범위·간편인증 계약 플래그 추가

FM_IDENTITY_METHODS 는 알 수 없는 값을 버리고 남는 게 없으면 ("mid",) 로
되돌린다. 오타 하나로 인증 수단이 전부 사라져 등록이 불가능해지는 사고를 막는다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 3: 간편인증 계약 어댑터

**Files:**
- Modify: `server/app/cx_identity.py:88-93`(계약 상수 근처), `server/app/cx_identity.py:255`(`get_oacx_biometric_contract`)
- Test: `server/tests/test_facemarket_identity_method.py` (두 번째 블록 추가)

**Interfaces:**
- Consumes: Task 2 의 `settings.fm_oacx_simple_auth_contract`
- Produces:
  - `cx_identity.SIMPLE_AUTH_CONTRACT: OacxBiometricContract` (version `"simple-auth-v1"`, `max_portrait_bytes=0`)
  - `cx_identity.get_oacx_biometric_contract(settings, *, method: str = "mid") -> OacxBiometricContract`
  - `cx_identity.parse_simple_auth_evidence(trans, *, contract) -> OacxBiometricEvidence`

- [ ] **Step 1: 실패하는 테스트 작성**

`server/tests/test_facemarket_identity_method.py` 에 이어 붙인다:

```python
from app import cx_identity
from conftest import make_settings   # make_settings(**overrides) — 필드를 직접 덮어쓴다


def test_simple_auth_contract_blocked_when_disabled():
    settings = make_settings(fm_oacx_simple_auth_contract="disabled")
    with pytest.raises(cx_identity.OacxBiometricError):
        cx_identity.get_oacx_biometric_contract(settings, method="simple_auth")


def test_simple_auth_contract_returned_when_enabled():
    settings = make_settings(fm_oacx_simple_auth_contract="simple-auth-v1")
    contract = cx_identity.get_oacx_biometric_contract(settings, method="simple_auth")
    assert contract.version == "simple-auth-v1"
    # 간편인증은 신분증 초상을 주지 않는다 — 초상 상한이 0이어야 릴레이 시도가 막힌다.
    assert contract.max_portrait_bytes == 0


def test_mid_contract_unchanged_by_new_flag():
    settings = make_settings(
        fm_oacx_contract_mode="prod-dlphoto-v1",
        fm_oacx_simple_auth_contract="simple-auth-v1",
    )
    contract = cx_identity.get_oacx_biometric_contract(settings, method="mid")
    assert contract.version == "prod-dlphoto-v1"
    assert contract.max_portrait_bytes == 5 * 1024 * 1024


def test_method_defaults_to_mid():
    settings = make_settings(fm_oacx_contract_mode="prod-dlphoto-v1")
    assert cx_identity.get_oacx_biometric_contract(settings).version == "prod-dlphoto-v1"


def test_simple_auth_evidence_parses_ci_name_birth():
    contract = cx_identity.SIMPLE_AUTH_CONTRACT
    evidence = cx_identity.parse_simple_auth_evidence(
        {"ci": "CI-VALUE", "name": "홍길동", "birth": "19900101", "txId": "tx-1"},
        contract=contract,
    )
    assert bytes(evidence.ci) == b"CI-VALUE"
    assert evidence.birth == "19900101"
    assert evidence.name_masked == "홍*동"
    assert evidence.contract_version == "simple-auth-v1"


def test_simple_auth_evidence_requires_ci():
    with pytest.raises(cx_identity.OacxBiometricError):
        cx_identity.parse_simple_auth_evidence(
            {"name": "홍길동", "birth": "19900101"},
            contract=cx_identity.SIMPLE_AUTH_CONTRACT,
        )
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_identity_method.py -q`
Expected: FAIL — `AttributeError: module 'app.cx_identity' has no attribute 'SIMPLE_AUTH_CONTRACT'`

- [ ] **Step 3: 어댑터 구현**

`server/app/cx_identity.py`, `PROD_DLPHOTO_OACX_BIOMETRIC_CONTRACT` 아래:

```python
# 간편인증(ENT_SIMPLE_AUTH) — 민간 인증사(PASS·카카오·네이버 등)를 통한 본인확인.
# 신분증 VC 제출이 아니라서 **초상이 없다**(max_portrait_bytes=0). 얼굴 앵커는
# 사용자가 촬영해 올린 신분증(facemarket_id_document)이 대신한다.
#
# ⚠️ 응답 스키마는 2026-09-11 기준 실거래로 미검증이다. config.auth.json 은
# /oacx/api/v1.5/authen/request + /authen/status 를 쓰고 결과는 authen/result
# 또는 parse_token 경로일 수 있다. 그래서 이 계약은 settings.
# fm_oacx_simple_auth_contract 가 'simple-auth-v1' 일 때만 선택되고, 기본값
# 'disabled' 에서는 아래 get_oacx_biometric_contract 가 호출 자체를 막는다.
SIMPLE_AUTH_CONTRACT = OacxBiometricContract(
    version="simple-auth-v1",
    birth_path=("birth",),
    portrait_encoding="hex",
    max_portrait_bytes=0,
)
```

`get_oacx_biometric_contract` 를 교체:

```python
def get_oacx_biometric_contract(settings, *, method: str = "mid") -> OacxBiometricContract:
    if method == "simple_auth":
        if settings.fm_oacx_simple_auth_contract == "simple-auth-v1":
            return SIMPLE_AUTH_CONTRACT
        raise OacxBiometricError("oacx_contract_unavailable")
    if settings.fm_oacx_contract_mode == "prod-dlphoto-v1":
        return PROD_DLPHOTO_OACX_BIOMETRIC_CONTRACT
    if settings.app_env == "dev" and settings.fm_oacx_contract_mode == "dev-mock-v1":
        return DEV_MOCK_OACX_BIOMETRIC_CONTRACT
    raise OacxBiometricError("oacx_contract_unavailable")
```

파서 추가 (`parse_oacx_biometric_evidence` 아래):

```python
def parse_simple_auth_evidence(
    trans: dict,
    *,
    contract: OacxBiometricContract,
) -> OacxBiometricEvidence:
    """간편인증 결과 → CI·이름·생년월일. 초상은 여기 없다(설계상 없음).

    실패는 전부 OacxBiometricError 로 정규화해 CI·생년월일 원문이 예외 메시지나
    로그로 새지 않게 한다 — parse_oacx_biometric_evidence 와 같은 규율이다.
    """
    try:
        ci = dig(trans, "ci")
        if not ci:
            raise OacxBiometricError("identity_ci_unavailable")
        birth = dig(trans, *contract.birth_path, "birthdate", "birthday")
        if not birth:
            raise OacxBiometricError("identity_birth_unavailable")
        name = dig(trans, "utf8Nm", "nm", "name", "userName") or ""
        return OacxBiometricEvidence(
            ci=bytearray(str(ci).encode()),
            birth=str(birth),
            name_masked=_mask_name(str(name)),
            transaction_id=dig(trans, "txId", "txid", "transactionId"),
            contract_version=contract.version,
        )
    except OacxBiometricError:
        raise
    except Exception:
        raise OacxBiometricError("identity_parse_failed") from None
```

> `dig` 의 실제 이름·시그니처는 `cx_identity.py` 안의 기존 헬퍼(`dig(trans, "ci")` 형태로 `parse_oacx_biometric_evidence` 가 이미 사용 중)를 그대로 쓴다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_identity_method.py -q`
Expected: PASS (11 passed)

- [ ] **Step 5: 기존 호출부 회귀 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_biometric_enrollment.py tests/test_facemarket_biometrics.py tests/test_facemarket_identity.py -q`
Expected: PASS — `get_oacx_biometric_contract(settings)` 호출부는 `method` 기본값 `"mid"` 로 동작이 같아야 한다

- [ ] **Step 6: 커밋**

```bash
git add server/app/cx_identity.py server/tests/test_facemarket_identity_method.py
git commit -m "$(cat <<'EOF'
feat(facemarket): 간편인증 계약 어댑터(simple-auth-v1) 추가

응답 스키마가 실거래로 미검증이라 기본값 disabled 에서 계약 선택 자체를
막는다. 켜기 전에는 어떤 가정이 틀려도 프로덕션에 닿지 않는다.

max_portrait_bytes=0 — 간편인증에는 신분증 초상이 없다. 얼굴 앵커는 사용자가
촬영해 올린 신분증이 대신한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 4: R2 키 헬퍼 + 신분증 업로드 라우트

**Files:**
- Modify: `server/app/r2.py:62`(키 헬퍼 근처)
- Modify: `server/app/agents/face_qc.py:45`(`FaceQc` 클래스)
- Create: `server/app/facemarket_id_document.py`
- Modify: `server/app/main.py` (라우터 등록)
- Test: `server/tests/test_facemarket_id_document.py`

**Interfaces:**
- Consumes: Task 1 의 `id_document_*` 컬럼, Task 2 의 `settings.fm_identity_methods`
- Produces:
  - `r2.enrollment_id_document_key(enrollment_id: str, ext: str) -> str`
  - `FaceQc.detect_largest_face(data: bytes | bytearray) -> tuple[int, int, int, int] | None` — (x, y, w, h). 검출 0건이면 `None`
  - `POST /v1/facemarket/enrollments/{id}/id-document` (multipart: `file`, `documentType`, `maskedConfirmed`) → `EnrollmentView`
  - `facemarket_id_document.crop_id_face(image_bytes: bytes, *, settings) -> bytearray` — 신분증에서 얼굴을 잘라 낸다. 검출 실패 시 `IdDocumentError("id_face_not_detected")`
  - `facemarket_id_document.purge_id_document(r2client, conn, enrollment_id: str) -> None`
  - `facemarket_id_document.IdDocumentError` (속성 `.reason`)

**왜 크롭이 필요한가** — `FaceQc._embed`(`agents/face_qc.py:58`)는 검출 얼굴이 정확히 1개가 아니면 `QcFailed("multiple_faces")` 를 던진다. 신분증을 손에 들고 찍으면 배경에 사람이 들어가기 쉽고, 그러면 대조가 통째로 실패한다. 게다가 카드 위 증명사진은 프레임 대비 작아 특징이 약하다. 그래서 **가장 큰 얼굴**을 여백과 함께 잘라 앵커로 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`server/tests/test_facemarket_id_document.py`:

```python
import pytest

from app import r2


def test_id_document_key_has_own_prefix():
    key = r2.enrollment_id_document_key("enr-1", "jpg")
    assert key == "facemarket/enrollments/enr-1/iddoc/masked.jpg"


def test_id_document_key_does_not_collide_with_photo_quarantine():
    # 사진 정리 로직(_drain_photo_cleanup)이 quarantine/ prefix 를 쓸어 가므로
    # 신분증이 같은 prefix 에 있으면 심사 전에 지워진다.
    photo = r2.enrollment_quarantine_key("enr-1", "front", "jpg")
    doc = r2.enrollment_id_document_key("enr-1", "jpg")
    assert "/quarantine/" in photo
    assert "/quarantine/" not in doc
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_id_document.py -q`
Expected: FAIL — `AttributeError: module 'app.r2' has no attribute 'enrollment_id_document_key'`

- [ ] **Step 3: 키 헬퍼 구현**

`server/app/r2.py`, `enrollment_quarantine_key` 아래:

```python
def enrollment_id_document_key(enrollment_id: str, ext: str) -> str:
    """사용자가 촬영해 올린 마스킹 신분증. 사진 quarantine 과 prefix 를 분리한다 —
    _drain_photo_cleanup 이 quarantine/ 를 쓸어 갈 때 심사 전 신분증까지 지우면 안 된다."""
    return f"facemarket/enrollments/{enrollment_id}/iddoc/masked.{ext}"
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_id_document.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: 얼굴 검출·크롭 테스트 추가**

`server/tests/test_facemarket_id_document.py` 에 이어 붙인다:

```python
import numpy as np

from app import facemarket_id_document as iddoc
from app.agents.face_qc import FaceQc


class _FakeDetector:
    """cv2.FaceDetectorYN 대역. detect() 는 (retval, faces) 를 준다."""

    def __init__(self, faces):
        self.faces = faces

    def setInputSize(self, size):
        pass

    def detect(self, image):
        return 1, self.faces


def test_detect_largest_face_returns_none_when_empty():
    qc = FaceQc.__new__(FaceQc)          # __init__ 은 가중치 파일을 요구한다
    qc._det = _FakeDetector(None)
    assert qc.detect_largest_face(_one_pixel_jpeg()) is None


def test_detect_largest_face_picks_biggest_box():
    """배경에 사람이 끼어도 신분증 증명사진(가장 큰 얼굴)을 고른다."""
    qc = FaceQc.__new__(FaceQc)
    qc._det = _FakeDetector(np.array([
        [5.0, 5.0, 10.0, 10.0] + [0.0] * 11,     # 작은 얼굴(배경)
        [20.0, 20.0, 60.0, 60.0] + [0.0] * 11,   # 큰 얼굴(신분증 사진)
    ], dtype=np.float32))
    assert qc.detect_largest_face(_one_pixel_jpeg()) == (20, 20, 60, 60)


class _FakeQc:
    def __init__(self, box):
        self.box = box

    def detect_largest_face(self, data):
        return self.box


def test_crop_id_face_raises_when_no_face(monkeypatch):
    monkeypatch.setattr(iddoc, "load_face_qc", lambda settings, required: _FakeQc(None))
    with pytest.raises(iddoc.IdDocumentError) as exc:
        iddoc.crop_id_face(b"\xff\xd8\xffnot-a-face", settings=object())
    assert exc.value.reason == "id_face_not_detected"


def test_crop_id_face_returns_bytearray(monkeypatch):
    monkeypatch.setattr(iddoc, "load_face_qc", lambda settings, required: _FakeQc((10, 10, 40, 40)))
    monkeypatch.setattr(iddoc, "_crop_jpeg", lambda data, box: bytearray(b"\xff\xd8\xffcropped"))
    out = iddoc.crop_id_face(b"\xff\xd8\xffwhole-card", settings=object())
    assert isinstance(out, bytearray)
    assert bytes(out).startswith(b"\xff\xd8\xff")
```

`_one_pixel_jpeg()` 헬퍼를 파일 상단에 둔다:

```python
def _one_pixel_jpeg() -> bytes:
    import cv2
    ok, buf = cv2.imencode(".jpg", np.zeros((8, 8, 3), dtype=np.uint8))
    assert ok
    return buf.tobytes()
```

- [ ] **Step 6: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_id_document.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.facemarket_id_document'`

- [ ] **Step 7: `FaceQc.detect_largest_face` 구현**

`server/app/agents/face_qc.py`, `_embed` 아래:

```python
    def detect_largest_face(self, data: bytes | bytearray) -> tuple[int, int, int, int] | None:
        """가장 큰 얼굴의 (x, y, w, h). 검출 0건이면 None.

        _embed 와 달리 여러 얼굴을 실패로 보지 않는다 — 신분증을 손에 들고 찍으면
        배경에 사람이 들어오기 때문이다. 신분증 증명사진이 프레임에서 가장 큰
        얼굴이라는 전제로 하나를 고르고, 그 크롭만 대조에 쓴다.
        """
        image = None
        try:
            image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise QcFailed("decode_failed")
            h, w = image.shape[:2]
            self._det.setInputSize((w, h))
            _, faces = self._det.detect(image)
            if faces is None or len(faces) == 0:
                return None
            best = max(faces, key=lambda f: float(f[2]) * float(f[3]))
            return (int(best[0]), int(best[1]), int(best[2]), int(best[3]))
        finally:
            if image is not None:
                image.fill(0)
```

- [ ] **Step 8: 모듈 + 라우트 구현**

`server/app/facemarket_id_document.py` 를 만든다. 뼈대:

```python
"""간편인증 경로의 신분증 촬영본 — 수용·얼굴 크롭·파기.

경로 S(간편인증)에는 OACX 신분증 초상(dlphotoimage)이 없다. 그래서 사용자가
실물 신분증을 찍어 올리고, 그 안의 얼굴이 SFace 앵커 자리를 대신한다.

보관 범위는 **마스킹 전체본**이다(주민번호 뒷자리를 사용자가 가린 상태).
얼굴만 자르지 않는 이유는 관리자가 발급일·이름·사진을 함께 봐야 위조를 판별할
수 있어서다. 마스킹은 클라이언트가 캔버스에 픽셀을 덮어써서 수행하며 서버는
이행 여부를 검증할 수 없다 — 관리자 심사 카드의 확인 항목이 그 자리를 메운다.

파기: 승인·거절 즉시 삭제하고 id_document_purged_at 을 남긴다. 취소·만료
경로와 7일 배치 스윕이 안전망이다.
"""

import asyncio
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from . import r2 as r2keys
from .agents.face_qc import load_face_qc  # signature: load_face_qc(settings, *, required=False)

router = APIRouter(prefix="/v1/facemarket", tags=["FaceMarket ID document"])

ALLOWED_ID_MIME = {"image/jpeg", "image/png", "image/webp"}
MAX_ID_BYTES = 12 * 1024 * 1024
ID_DOCUMENT_TYPES = {"rrc", "dl", "passport", "arc"}
# 얼굴 주변 여백. 신분증 사진은 작아서 타이트하게 자르면 SFace 특징이 뭉개진다.
FACE_CROP_MARGIN = 0.35


class IdDocumentError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _crop_jpeg(data: bytes, box: tuple[int, int, int, int]) -> bytearray:
    """box 주변을 FACE_CROP_MARGIN 만큼 넓혀 잘라 JPEG 로 재인코딩."""
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise IdDocumentError("id_document_unreadable")
    height, width = image.shape[:2]
    x, y, w, h = box
    mx, my = int(w * FACE_CROP_MARGIN), int(h * FACE_CROP_MARGIN)
    x0, y0 = max(0, x - mx), max(0, y - my)
    x1, y1 = min(width, x + w + mx), min(height, y + h + my)
    ok, buf = cv2.imencode(".jpg", image[y0:y1, x0:x1])
    if not ok:
        raise IdDocumentError("id_document_unreadable")
    return bytearray(buf.tobytes())


def crop_id_face(image_bytes: bytes, *, settings) -> bytearray:
    """마스킹 신분증에서 얼굴 영역만 잘라 SFace 앵커로 쓸 바이트를 만든다."""
    qc = load_face_qc(settings, required=True)
    box = qc.detect_largest_face(image_bytes)
    if box is None:
        raise IdDocumentError("id_face_not_detected")
    return _crop_jpeg(image_bytes, box)


async def purge_id_document(r2client, conn, enrollment_id: str) -> None:
    """R2 객체를 지우고 id_document_purged_at 을 남긴다. 두 번 불러도 안전하다."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id_document_r2_key from fm_biometric_enrollments where id = %s",
            (enrollment_id,),
        )
        row = await cur.fetchone()
        key = row["id_document_r2_key"] if row else None
        if key:
            try:
                await asyncio.to_thread(r2client.delete, key)
            except Exception:
                # 객체 삭제 실패는 7일 배치 스윕이 잡는다. 상태는 진실대로 남긴다.
                pass
        await cur.execute(
            "update fm_biometric_enrollments "
            "set id_document_r2_key = null, id_document_purged_at = now() "
            "where id = %s",
            (enrollment_id,),
        )
```

업로드 라우트(같은 파일):

```python
@router.post("/enrollments/{enrollment_id}/id-document")
async def upload_id_document(
    request: Request,
    enrollment_id: str,
    file: UploadFile = File(...),
    document_type: str = Form(..., alias="documentType"),
    masked_confirmed: bool = Form(..., alias="maskedConfirmed"),
    user_id: str = Depends(require_user),
):
    settings = request.app.state.settings
    if "simple_auth" not in settings.fm_identity_methods:
        raise _err("identity_method_unavailable", "지금은 이 방식으로 등록할 수 없어요.", status=409)
    if document_type not in ID_DOCUMENT_TYPES:
        raise _err("invalid_document_type", "신분증 종류를 확인해 주세요.")
    if not masked_confirmed:
        raise _err("masking_required", "주민등록번호 뒷자리를 가린 뒤 올려 주세요.")
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_ID_MIME:
        raise _err("unsupported_type", "PNG, JPEG, WebP 이미지만 사용할 수 있습니다.")
    data = await file.read()
    if not data:
        raise _err("empty_upload", "빈 파일은 사용할 수 없습니다.")
    if len(data) > MAX_ID_BYTES:
        raise _err("file_too_large", "이미지는 12MB 이하만 가능합니다.", status=413)
    # 얼굴이 안 잡히면 심사할 대상이 없다 — 저장하지 말고 재촬영을 요구한다.
    # crop_id_face 는 settings 가 keyword-only 라 to_thread 에도 키워드로 넘긴다.
    try:
        await asyncio.to_thread(crop_id_face, data, settings=settings)
    except IdDocumentError as exc:
        raise _err(exc.reason, "신분증 얼굴이 보이게 다시 찍어 주세요.")
    key = r2keys.enrollment_id_document_key(_canonical_enrollment_id(enrollment_id), "jpg")
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        await asyncio.to_thread(_r2_face(request).put_bytes, key, data, mime)
        async with conn.cursor() as cur:
            await cur.execute(_TRANSITION_SQL, (key, document_type, enrollment_id, user_id))
            if cur.rowcount == 0:
                # 이미 지나간 단계이거나 남의 등록 — 방금 올린 객체를 되돌린다.
                await asyncio.to_thread(_r2_face(request).delete, key)
                raise _err(
                    "invalid_enrollment_state",
                    "신분증을 올릴 수 있는 단계가 아니에요.",
                    status=409,
                )
        await conn.commit()
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        return await _enrollment_view(conn, row)
```

상태 전이 SQL(`_TRANSITION_SQL`):

```sql
update fm_biometric_enrollments
set status = 'identity_pending',
    id_document_r2_key = %s, id_document_type = %s,
    id_document_uploaded_at = now(), id_document_purged_at = null
where id = %s and user_id = %s and status = 'id_capture_pending'
```

`server/app/main.py` 에서 `facemarket_id_document.router` 를 등록한다(기존 `facemarket_enrollment.router` 등록 줄 근처, 같은 패턴).

`_err`, `require_user`, `get_conn`, `_r2_face`, `_canonical_enrollment_id` 는 `facemarket_enrollment.py` 의 것을 import 해 쓴다(중복 정의 금지).

- [ ] **Step 9: 테스트 통과 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_id_document.py -q`
Expected: PASS (6 passed)

- [ ] **Step 10: 앱이 기동하는지 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -c "from app.main import create_app; app = create_app(); print([r.path for r in app.routes if 'id-document' in r.path])"`
Expected: `['/v1/facemarket/enrollments/{enrollment_id}/id-document']`

- [ ] **Step 11: 커밋**

```bash
git add server/app/r2.py server/app/agents/face_qc.py server/app/facemarket_id_document.py server/app/main.py server/tests/test_facemarket_id_document.py
git commit -m "$(cat <<'EOF'
feat(facemarket): 신분증 촬영본 업로드 라우트와 얼굴 크롭

마스킹 전체본을 iddoc/ 전용 prefix 에 저장한다. 사진 quarantine 과 분리해야
_drain_photo_cleanup 이 심사 전 신분증을 쓸어 가지 않는다.

얼굴이 안 잡히면 저장하지 않고 재촬영을 요구한다 — 심사할 대상이 없는 객체를
남기면 파기 책임만 는다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 5: 등록 생성 시 인증 수단 분기

**Files:**
- Modify: `server/app/facemarket_enrollment.py:168`(`CreateEnrollmentBody`), `:705`(`create_enrollment`), `:198`(`EnrollmentView`)
- Test: `server/tests/test_facemarket_identity_method.py` (세 번째 블록)

**Interfaces:**
- Consumes: Task 1 의 `identity_method` 컬럼, Task 2 의 `settings.fm_identity_methods`
- Produces: `CreateEnrollmentBody.identity_method: str = "mid"`, `EnrollmentView.identity_method: str`, `EnrollmentView.review_status: str | None`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_create_rejects_simple_auth_when_flag_off(enrollment_client):
    """FM_IDENTITY_METHODS=mid 인데 simple_auth 를 요청하면 409."""
    client, store, settings = enrollment_client(fm_identity_methods=("mid",))
    response = client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": "d" * 40,
            "biometricConsent": {"accepted": True, "documentVersion": "2026-08-v2"},
            "identityMethod": "simple_auth",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "identity_method_unavailable"


def test_create_simple_auth_starts_at_id_capture_pending(enrollment_client):
    client, store, settings = enrollment_client(
        fm_identity_methods=("mid", "simple_auth")
    )
    response = client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": "d" * 40,
            "biometricConsent": {"accepted": True, "documentVersion": "2026-08-v2"},
            "identityMethod": "simple_auth",
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "id_capture_pending"
    assert response.json()["identityMethod"] == "simple_auth"


def test_create_mid_unchanged(enrollment_client):
    """기본값 경로는 지금과 똑같이 identity_pending 에서 시작한다."""
    client, store, settings = enrollment_client(fm_identity_methods=("mid",))
    response = client.post(
        "/v1/facemarket/enrollments",
        json={
            "deviceId": "d" * 40,
            "biometricConsent": {"accepted": True, "documentVersion": "2026-08-v2"},
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "identity_pending"
    assert response.json()["identityMethod"] == "mid"
```

> `enrollment_client` 픽스처는 `tests/test_facemarket_biometric_enrollment.py` 의 `EnrollmentStore` + `create_app` 조립을 재사용한다. 그 파일에서 클라이언트 조립부를 `tests/conftest.py` 로 올리거나, 같은 패턴을 이 파일에 복제한다. 복제할 경우 `make_settings()` 에 `replace(...)` 로 플래그를 주입한다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_identity_method.py -q`
Expected: FAIL — `identityMethod` 필드가 응답에 없음

- [ ] **Step 3: 구현**

`CreateEnrollmentBody` 에 필드 추가:

```python
class CreateEnrollmentBody(CamelModel):
    device_id: str
    biometric_consent: BiometricConsent
    # 'mid' = OACX 모바일 신분증(기존), 'simple_auth' = 간편인증 + 신분증 촬영.
    identity_method: str = "mid"
```

`EnrollmentView` 에 추가:

```python
    identity_method: str = "mid"
    review_status: str | None = None
```

`create_enrollment` 초입(동의 검사 직후):

```python
    method = (body.identity_method or "mid").strip()
    if method not in settings.fm_identity_methods:
        raise _err(
            "identity_method_unavailable",
            "지금은 이 방식으로 등록할 수 없어요.",
            status=409,
        )
    initial_status = "id_capture_pending" if method == "simple_auth" else "identity_pending"
```

insert 문에 `identity_method`, `status` 를 싣는다. `_enrollment_view` 의 select 에 `identity_method`, `review_status` 를 추가한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_identity_method.py -q`
Expected: PASS (14 passed)

- [ ] **Step 5: 회귀 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_biometric_enrollment.py -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add server/app/facemarket_enrollment.py server/tests/test_facemarket_identity_method.py
git commit -m "$(cat <<'EOF'
feat(facemarket): 등록 생성에 인증 수단 분기 추가

simple_auth 는 id_capture_pending 에서 시작해 신분증 촬영을 먼저 받는다.
과금되는 간편인증 호출 전에 신분증 없는 사용자를 탈락시키고, 사용자가
신분증을 보며 이름·생년월일을 입력해 지원서 대조 실패를 줄인다.

플래그에 없는 수단을 요청하면 409 — 프론트가 낡아도 서버가 진실이다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 6: 본인확인 라우트의 계약 분기

**Files:**
- Modify: `server/app/facemarket_enrollment.py:843-1001`(`verify_enrollment_identity`)
- Test: `server/tests/test_facemarket_identity_method.py` (네 번째 블록)

**Interfaces:**
- Consumes: Task 3 의 `get_oacx_biometric_contract(settings, method=)`, `parse_simple_auth_evidence`
- Produces: 없음 (기존 라우트 동작 확장)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_identity_uses_simple_auth_contract_for_simple_auth_method(
    enrollment_client, monkeypatch
):
    client, store, settings = enrollment_client(
        fm_identity_methods=("mid", "simple_auth"),
        fm_oacx_simple_auth_contract="simple-auth-v1",
    )
    store.add_enrollment(status="identity_pending", identity_method="simple_auth")
    captured = {}

    async def fake_fetch(base_url, token):
        captured["called"] = True
        return {"ci": "CI-1", "name": "홍길동", "birth": "19900101", "txId": "t1"}

    monkeypatch.setattr(cx_identity, "fetch_trans", fake_fetch)
    response = client.post(
        f"/v1/facemarket/enrollments/{store.latest_id}/identity",
        json={"token": "tok-1"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "photos_pending"
    row = store.latest_enrollment
    assert row["identity_contract_version"] == "simple-auth-v1"
    # 초상 없이도 CI·이름·생년월일만으로 게이트가 통과해야 한다.
    assert row["identity_ci_hash"]
    assert row["identity_birth_year"] == "1990"


def test_identity_blocked_when_simple_auth_contract_disabled(enrollment_client):
    client, store, settings = enrollment_client(
        fm_identity_methods=("mid", "simple_auth"),
        fm_oacx_simple_auth_contract="disabled",
    )
    store.add_enrollment(status="identity_pending", identity_method="simple_auth")
    response = client.post(
        f"/v1/facemarket/enrollments/{store.latest_id}/identity",
        json={"token": "tok-1"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "oacx_contract_unavailable"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_identity_method.py -q`
Expected: FAIL — 계약 버전이 `prod-dlphoto-v1` 로 기록됨

- [ ] **Step 3: 구현**

`verify_enrollment_identity` 에서 enrollment 의 `identity_method` 를 **먼저 읽고** 계약을 고른다. 현재는 소유·상태 검사가 `fetch_trans` 뒤에 있으므로, 메서드 조회용 가벼운 select 를 앞에 둔다:

```python
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select identity_method from fm_biometric_enrollments "
                "where id = %s and user_id = %s",
                (enrollment_id, user_id),
            )
            method_row = await cur.fetchone()
    if method_row is None:
        raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
    method = method_row["identity_method"] or "mid"
    try:
        contract = cx_identity.get_oacx_biometric_contract(settings, method=method)
    except cx_identity.OacxBiometricError as exc:
        raise _err(exc.reason, "본인확인을 지금 진행할 수 없어요.")
```

증거 파싱을 분기:

```python
        trans = await cx_identity.fetch_trans(settings.cx_trans_base_url, token)
        if method == "simple_auth":
            evidence = cx_identity.parse_simple_auth_evidence(trans, contract=contract)
        else:
            evidence = cx_identity.parse_oacx_biometric_evidence(trans, contract=contract)
```

나머지(CI HMAC, replay 차단, 교차유저 충돌, 지원서 대조, 상태 전이)는 **손대지 않는다** — 이미 `ci`·`name`·`birth` 만 쓰므로 그대로 동작한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_identity_method.py -q`
Expected: PASS (16 passed)

- [ ] **Step 5: 회귀 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_biometric_enrollment.py tests/test_facemarket_identity_compare.py -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add server/app/facemarket_enrollment.py server/tests/test_facemarket_identity_method.py
git commit -m "$(cat <<'EOF'
feat(facemarket): 본인확인 라우트를 인증 수단별 계약으로 분기

verify_enrollment_identity 는 원래부터 ci·name·birth 세 개만 쓰고 초상을
보지 않았다. 계약 선택만 갈라 주면 CI 중복 차단·지원서 대조·replay 방지가
간편인증에서도 그대로 동작한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 7: 완료 처리 — advisory 매칭 + review_pending 전이

**Files:**
- Modify: `server/app/facemarket_enrollment.py:2010-2120`(`process_enrollment_completion`)
- Test: `server/tests/test_facemarket_admin_review.py` (첫 블록)

**Interfaces:**
- Consumes: Task 4 의 `crop_id_face(image_bytes, *, settings)`, Task 5 의 `identity_method`
- Produces: `match_scores` jsonb 기록, `review_pending` 전이, 모듈 함수 `review_required(settings, method: str) -> bool`

- [ ] **Step 1: 실패하는 테스트 작성**

`server/tests/test_facemarket_admin_review.py`:

```python
def test_simple_auth_stops_at_review_pending(enrollment_client, monkeypatch):
    client, store, settings = enrollment_client(
        fm_identity_methods=("mid", "simple_auth"),
        fm_enrollment_review="simple_auth_only",
    )
    store.add_enrollment(
        status="photos_pending", identity_method="simple_auth",
        id_document_r2_key="facemarket/enrollments/e1/iddoc/masked.jpg",
    )
    store.add_photos(("front", "angle45", "side"))
    response = client.post(f"/v1/facemarket/enrollments/{store.latest_id}/complete")
    assert response.status_code == 200
    row = store.latest_enrollment
    assert row["status"] == "review_pending"
    assert row["review_status"] == "pending"
    # 자산 빌드가 시작되면 안 된다.
    assert store.jobs == []


def test_simple_auth_records_advisory_scores_without_blocking(
    enrollment_client, monkeypatch
):
    """임계 미달이어도 face_match_failed 로 끝나지 않고 심사로 넘어간다."""
    client, store, settings = enrollment_client(
        fm_identity_methods=("mid", "simple_auth"),
        fm_enrollment_review="simple_auth_only",
        fm_retouched_live_threshold=0.15,
        fm_side_live_threshold=0.10,
    )
    store.add_enrollment(
        status="photos_pending", identity_method="simple_auth",
        id_document_r2_key="facemarket/enrollments/e1/iddoc/masked.jpg",
    )
    store.add_photos(("front", "angle45", "side"))
    store.set_similarity({"front": 0.04, "angle45": 0.05, "side": 0.02})
    response = client.post(f"/v1/facemarket/enrollments/{store.latest_id}/complete")
    assert response.status_code == 200
    row = store.latest_enrollment
    assert row["status"] == "review_pending"
    scores = row["match_scores"]
    assert scores["anchor"] == "id_document_crop"
    assert scores["scores"]["front"] == pytest.approx(0.04)
    assert set(scores["belowThreshold"]) == {"front", "angle45", "side"}


def test_mid_path_still_enforces_match(enrollment_client, monkeypatch):
    """경로 M 은 무변경 — 임계 미달이면 여전히 즉시 실패한다."""
    client, store, settings = enrollment_client(
        fm_identity_methods=("mid",), fm_enrollment_review="simple_auth_only",
    )
    store.add_enrollment(status="photos_pending", identity_method="mid")
    store.add_photos(("front", "angle45", "side"))
    store.set_similarity({"front": 0.04, "angle45": 0.05, "side": 0.02})
    response = client.post(f"/v1/facemarket/enrollments/{store.latest_id}/complete")
    assert response.json()["reason"] == "face_match_failed"
    assert store.latest_enrollment["status"] == "failed"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_admin_review.py -q`
Expected: FAIL — `review_pending` 전이 없음, `id_portrait_unavailable` 로 끝남

- [ ] **Step 3: 구현**

`process_enrollment_completion` 의 초상 확보부를 분기한다:

```python
            method = row.get("identity_method") or "mid"
            if method == "simple_auth":
                # 경로 S: 앵커는 사용자가 올린 마스킹 신분증의 얼굴 크롭이다.
                key = row.get("id_document_r2_key")
                if not key:
                    raise EnrollmentMappedError("id_portrait_unavailable")
                try:
                    document = await asyncio.to_thread(r2.get_bytes, key)
                    portrait = await asyncio.to_thread(
                        facemarket_id_document.crop_id_face, document, settings=settings
                    )
                except facemarket_id_document.IdDocumentError as exc:
                    raise EnrollmentMappedError(exc.reason) from None
                except Exception:
                    raise EnrollmentMappedError("id_portrait_unavailable") from None
            else:
                contract = cx_identity.get_oacx_biometric_contract(settings, method="mid")
                portrait = cx_identity.parse_oacx_portrait_hex(id_photo_hex, contract=contract)
```

매칭 루프를 advisory/enforce 로 가른다. 기존 루프의 `_assert_match(score, threshold)` 호출을:

```python
            advisory = method == "simple_auth"
            scores: dict[str, float] = {}
            below: list[str] = []
            skipped: list[str] = []
            matched_any = False
            for _angle, buffer in photo_items:
                try:
                    score = qc.one_to_one_similarity(buffer, match_anchor)
                except QcFailed as exc:
                    if exc.reason == "no_face_detected":
                        skipped.append(_angle)
                        continue
                    raise
                threshold = match_threshold_for_angle(settings, _angle)
                logger.info(
                    "fm_match_photo_anchor angle=%s score=%s threshold=%.4f advisory=%s",
                    _angle, score, threshold, advisory,
                )
                scores[_angle] = float(score)
                if score is None or float(score) < threshold:
                    below.append(_angle)
                    if not advisory:
                        _assert_match(score, threshold)
                else:
                    matched_any = True
            if not advisory and not matched_any:
                raise EnrollmentMappedError("face_match_failed")
            if advisory and not scores:
                # 세 각도 전부 얼굴 미검출 = 심사할 근거가 없다.
                raise EnrollmentMappedError("face_match_failed")
```

`match_scores` 스냅샷을 만든다:

```python
            match_snapshot = {
                # raw 코사인을 그대로 저장한다. 백분율 변환은 표시층에서만 한다 —
                # 임계 재캘리브·사후 분석이 원본을 요구하고, 표시 형식이 바뀐다고
                # 저장 값이 흔들리면 안 된다.
                "policyVersion": settings.fm_match_policy_version,
                "anchor": "id_document_crop" if advisory else "oacx_portrait",
                "thresholds": {
                    angle: match_threshold_for_angle(settings, angle) for angle in ANGLES
                },
                "scores": scores,
                "belowThreshold": below,
                "skipped": skipped,
                "computedAt": datetime.now(timezone.utc).isoformat(),
            }
```

심사 필요 여부:

```python
def review_required(settings, method: str) -> bool:
    mode = settings.fm_enrollment_review
    if mode == "off":
        return False
    if mode == "all":
        return True
    return method == "simple_auth"
```

심사가 필요하면 자산 빌드를 시작하지 않고 전이한다:

```sql
update fm_biometric_enrollments
set status = 'review_pending', review_status = 'pending', match_scores = %s
where id = %s and user_id = %s and status = 'photos_pending'
```

심사가 필요 없으면 `match_scores` 만 기록하고 기존 경로(자산 빌드)를 그대로 탄다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_admin_review.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: 회귀 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_biometric_enrollment.py tests/test_facemarket_biometrics.py -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add server/app/facemarket_enrollment.py server/tests/test_facemarket_admin_review.py
git commit -m "$(cat <<'EOF'
feat(facemarket): 간편인증 경로는 advisory 매칭 후 심사 대기로 멈춘다

촬영 신분증은 위조 검증이 0이라 점수가 높아도 안전을 보장하지 않고, 촬영
품질 편차 때문에 낮아도 본인일 수 있다. 어느 방향으로도 기계가 신뢰구간을
못 만들어서 점수는 관리자에게 정보로 넘기고 판정은 사람이 한다.

세 각도 전부 얼굴 미검출이면 심사할 근거가 없으므로 그때는 차단한다.
경로 M 은 enforce 그대로.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 8: 관리자 심사 API

**Files:**
- Create: `server/app/facemarket_admin_review.py`
- Modify: `server/app/main.py`
- Test: `server/tests/test_facemarket_admin_review.py` (두 번째 블록)

**Interfaces:**
- Consumes: Task 7 의 `review_pending`/`match_scores`, Task 4 의 `purge_id_document`
- Produces:
  - `GET /v1/facemarket/admin/enrollments?review=pending|approved|rejected`
  - `GET /v1/facemarket/admin/enrollments/{id}`
  - `GET /v1/facemarket/admin/enrollments/{id}/images/{kind}` — `kind ∈ {id_document, front, angle45, side}`
  - `POST /v1/facemarket/admin/enrollments/{id}/approve`
  - `POST /v1/facemarket/admin/enrollments/{id}/reject` — body `{"reason": str}`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_review_queue_requires_admin(admin_client):
    client, store = admin_client(is_admin=False)
    assert client.get("/v1/facemarket/admin/enrollments?review=pending").status_code == 403


def test_review_queue_lists_pending(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth")
    rows = client.get("/v1/facemarket/admin/enrollments?review=pending").json()
    assert len(rows) == 1
    assert rows[0]["identityMethod"] == "simple_auth"


def test_review_card_includes_scores_and_application(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(
        status="review_pending", review_status="pending", identity_method="simple_auth",
        match_scores={"scores": {"front": 0.31}, "belowThreshold": []},
        application={"applicant_name": "홍길동", "birthdate": "1990-01-01"},
    )
    card = client.get(f"/v1/facemarket/admin/enrollments/{store.latest_id}").json()
    assert card["matchScores"]["scores"]["front"] == 0.31
    assert card["application"]["applicantName"] == "홍길동"
    assert set(card["images"]) == {"id_document", "front", "angle45", "side"}


def test_image_route_is_no_store(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         id_document_r2_key="k", identity_method="simple_auth")
    response = client.get(
        f"/v1/facemarket/admin/enrollments/{store.latest_id}/images/id_document"
    )
    assert response.headers["cache-control"] == "private, no-store"


def test_approve_transitions_and_purges_document(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth", id_document_r2_key="k")
    response = client.post(f"/v1/facemarket/admin/enrollments/{store.latest_id}/approve")
    assert response.status_code == 200
    row = store.latest_enrollment
    assert row["review_status"] == "approved"
    assert row["status"] == "processing"
    # 심사가 끝나면 신분증은 더 쓸 데가 없다 — 즉시 파기.
    assert row["id_document_r2_key"] is None
    assert row["id_document_purged_at"] is not None
    assert "k" in store.deleted_r2_keys


def test_reject_requires_reason_and_purges(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth", id_document_r2_key="k")
    assert client.post(
        f"/v1/facemarket/admin/enrollments/{store.latest_id}/reject", json={"reason": ""}
    ).status_code == 400
    response = client.post(
        f"/v1/facemarket/admin/enrollments/{store.latest_id}/reject",
        json={"reason": "신분증 얼굴과 등록 사진이 다른 사람"},
    )
    assert response.status_code == 200
    row = store.latest_enrollment
    assert row["review_status"] == "rejected"
    assert row["status"] == "failed"
    assert row["reason"] == "review_rejected"
    assert row["id_document_r2_key"] is None


def test_approve_writes_audit(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth")
    client.post(f"/v1/facemarket/admin/enrollments/{store.latest_id}/approve")
    assert store.audit[-1]["action"] == "enrollment_review_approve"
    assert store.audit[-1]["target_type"] == "enrollment"


def test_approve_rejects_non_review_state(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="photos_pending", identity_method="simple_auth")
    response = client.post(f"/v1/facemarket/admin/enrollments/{store.latest_id}/approve")
    assert response.status_code == 409
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_admin_review.py -q`
Expected: FAIL — 404 (라우트 없음)

- [ ] **Step 3: 구현**

`server/app/facemarket_admin_review.py`. 라우트 5개 전부 `_require_admin(conn, user_id, request)` 를 맨 앞에 둔다(`facemarket_applications.py:790` 패턴).

승인 트랜잭션:

```sql
update fm_biometric_enrollments
set review_status = 'approved', reviewed_by = %s, reviewed_at = now(),
    status = 'processing'
where id = %s and status = 'review_pending' and review_status = 'pending'
returning id::text as id
```

`returning` 이 비면 409(`invalid_review_state`). 커밋 뒤 `purge_id_document` → `write_audit` → 자산 빌드 잡 큐잉(`_wake_dispatcher`) 순서로 진행한다. 파기와 감사는 커밋 이후 best-effort 지만 **파기 실패는 경고 로그**를 남긴다(7일 스윕이 안전망).

거절 트랜잭션:

```sql
update fm_biometric_enrollments
set review_status = 'rejected', reviewed_by = %s, reviewed_at = now(),
    review_reason = %s, status = 'failed', reason = 'review_rejected',
    completed_at = now()
where id = %s and status = 'review_pending' and review_status = 'pending'
returning id::text as id
```

이미지 라우트는 `kind` 를 화이트리스트로 받고, `id_document` 는 `id_document_r2_key`, 나머지는 해당 각도의 quarantine 키를 쓴다. 응답 헤더 `Cache-Control: private, no-store` 고정.

`server/app/main.py` 에 라우터를 등록한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_admin_review.py -q`
Expected: PASS (11 passed)

- [ ] **Step 5: 커밋**

```bash
git add server/app/facemarket_admin_review.py server/app/main.py server/tests/test_facemarket_admin_review.py
git commit -m "$(cat <<'EOF'
feat(facemarket): 관리자 육안 심사 API

심사 큐·카드·이미지 열람·승인·거절. 전부 _require_admin(기기 게이트 포함)을
타고 승인·거절은 admin_audit_log 에 남는다.

심사가 끝나면 신분증 촬영본을 즉시 파기한다. 파기 실패는 경고 로그만 남기고
막지 않는다 — 7일 배치 스윕이 상한이다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 9: 파기 안전망 — 취소·만료 경로 + 배치 스윕

**Files:**
- Modify: `server/app/facemarket_enrollment.py:2348`(`cancel`), `:399-560`(정리 드레인)
- Modify: `server/app/facemarket_id_document.py` (스윕 함수 추가)
- Test: `server/tests/test_facemarket_id_document.py` (세 번째 블록)

**Interfaces:**
- Consumes: Task 4 의 `purge_id_document`
- Produces: `facemarket_id_document.sweep_stale_id_documents(r2client, *, older_than_seconds: int = 7 * 86400) -> int`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
class _FakeR2:
    def __init__(self, keys):
        self.keys = list(keys)
        self.deleted = []

    def list_prefix_aged(self, prefix, *, older_than_seconds):
        assert prefix == "facemarket/enrollments/"
        return list(self.keys)

    def delete(self, key):
        self.deleted.append(key)


def test_sweep_deletes_only_iddoc_objects():
    r2client = _FakeR2([
        "facemarket/enrollments/e1/iddoc/masked.jpg",
        "facemarket/enrollments/e1/quarantine/front.jpg",
        "facemarket/enrollments/e2/iddoc/masked.png",
    ])
    removed = iddoc.sweep_stale_id_documents(r2client)
    assert removed == 2
    assert r2client.deleted == [
        "facemarket/enrollments/e1/iddoc/masked.jpg",
        "facemarket/enrollments/e2/iddoc/masked.png",
    ]


def test_sweep_default_window_is_seven_days():
    captured = {}

    class _R2:
        def list_prefix_aged(self, prefix, *, older_than_seconds):
            captured["window"] = older_than_seconds
            return []

        def delete(self, key):
            pass

    iddoc.sweep_stale_id_documents(_R2())
    assert captured["window"] == 7 * 86400
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_id_document.py -q`
Expected: FAIL — `AttributeError: sweep_stale_id_documents`

- [ ] **Step 3: 구현**

```python
def sweep_stale_id_documents(r2client, *, older_than_seconds: int = 7 * 86400) -> int:
    """승인·거절·취소 경로가 전부 실패해도 7일이 상한이 되게 하는 마지막 방어선.

    사진(quarantine/)은 건드리지 않는다 — 자산 빌드 전 단계에서 정상적으로
    오래 남아 있을 수 있고, 그건 _drain_photo_cleanup 의 책임이다.
    """
    removed = 0
    for key in r2client.list_prefix_aged(
        "facemarket/enrollments/", older_than_seconds=older_than_seconds
    ):
        if "/iddoc/" not in key:
            continue
        try:
            r2client.delete(key)
            removed += 1
        except Exception:
            logger.warning("id_document_sweep_delete_failed key=%s", key)
    return removed
```

취소 라우트(`cancel`)와 만료·실패 정리 경로에서 `purge_id_document` 를 호출하도록 배선한다. 스윕은 기존 정리 스케줄러(`_drain_model_asset_cleanup` 을 돌리는 자리)에 함께 태운다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_id_document.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
git add server/app/facemarket_id_document.py server/app/facemarket_enrollment.py server/tests/test_facemarket_id_document.py
git commit -m "$(cat <<'EOF'
feat(facemarket): 신분증 촬영본 파기 안전망 3겹

승인·거절 즉시 파기, 취소·만료 정리 경로, 7일 배치 스윕. 앞의 둘이 전부
실패해도 7일이 상한이다.

스윕은 iddoc/ 만 지운다 — quarantine/ 사진은 자산 빌드 전까지 정상적으로
남아 있을 수 있고 그건 _drain_photo_cleanup 책임이다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 10: 프론트 — 상태 매핑 + API 클라이언트

**Files:**
- Modify: `src/features/model/biometricEnrollment.js:53`
- Modify: `src/lib/api/facemarket.js`
- Test: `src/features/model/biometricEnrollment.test.js` (없으면 생성)

**Interfaces:**
- Consumes: Task 5·7 의 신규 상태
- Produces:
  - `nextEnrollmentStep()` 이 `'id_capture'`, `'review'` 반환
  - `api.uploadIdDocument(enrollmentId, { file, documentType, maskedConfirmed })`
  - `api.adminListEnrollments(review)`, `api.adminEnrollmentCard(id)`, `api.adminApproveEnrollment(id)`, `api.adminRejectEnrollment(id, reason)`

- [ ] **Step 1: 실패하는 테스트 작성**

`src/features/model/biometricEnrollment.test.js`:

```js
import { describe, expect, it } from 'vitest';
import { nextEnrollmentStep } from './biometricEnrollment.js';

describe('nextEnrollmentStep', () => {
  it('id_capture_pending 은 신분증 촬영 스텝으로', () => {
    expect(nextEnrollmentStep({ status: 'id_capture_pending' })).toBe('id_capture');
  });

  it('review_pending 은 심사 대기 스텝으로', () => {
    expect(nextEnrollmentStep({ status: 'review_pending' })).toBe('review');
  });

  it('기존 상태 매핑은 그대로', () => {
    expect(nextEnrollmentStep({ status: 'identity_pending' })).toBe('identity');
    expect(nextEnrollmentStep({ status: 'photos_pending' })).toBe('photos');
    expect(nextEnrollmentStep({ status: 'passed' })).toBe('done');
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm vitest run src/features/model/biometricEnrollment.test.js`
Expected: FAIL — `'failed'` 를 돌려줌

> 레포에 vitest 가 없으면 이 태스크의 테스트는 건너뛰고 Step 3·4 만 수행한 뒤 `pnpm build` 로 검증한다.

- [ ] **Step 3: 구현**

`src/features/model/biometricEnrollment.js`:

```js
export function nextEnrollmentStep(enrollment) {
  if (!enrollment) return 'consent';
  if (enrollment.status === 'id_capture_pending') return 'id_capture';
  if (enrollment.status === 'identity_pending') return 'identity';
  if (enrollment.status === 'photos_pending') return 'photos';
  if (enrollment.status === 'review_pending') return 'review';
  if (enrollment.status === 'liveness_pending') return 'liveness';
  if (enrollment.status === 'processing' || enrollment.status === 'asset_building') return 'processing';
  if (enrollment.status === 'license_pending' || enrollment.status === 'vc_pending') return 'terms';
  if (enrollment.status === 'passed') return 'done';
  return 'failed';
}
```

`src/lib/api/facemarket.js` 에 엔드포인트 5종을 기존 패턴대로 추가한다. `uploadIdDocument` 는 `FormData` 를 쓴다(사진 업로드 함수가 선례).

- [ ] **Step 4: 확인**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm build`
Expected: 빌드 성공

- [ ] **Step 5: 커밋**

```bash
git add src/features/model/biometricEnrollment.js src/lib/api/facemarket.js src/features/model/biometricEnrollment.test.js
git commit -m "$(cat <<'EOF'
feat(facemarket): 신규 등록 상태 스텝 매핑과 API 클라이언트

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 11: 프론트 — 인증 수단 선택 + 신분증 촬영·마스킹

**Files:**
- Create: `src/features/model/IdentityMethodStep.jsx`
- Create: `src/features/model/IdDocumentStep.jsx`
- Modify: `src/features/model/ModelRegister.jsx`

**Interfaces:**
- Consumes: Task 10 의 `uploadIdDocument`, `nextEnrollmentStep`
- Produces:
  - `<IdentityMethodStep methods={string[]} onPick={(method) => void} />`
  - `<IdDocumentStep onUploaded={() => void} onError={(err) => void} />`

- [ ] **Step 1: 인증 수단 선택 화면**

`IdentityMethodStep.jsx`. `import.meta.env.VITE_FM_IDENTITY_METHODS`(기본 `'mid'`)를 콤마 분리해 버튼을 그린다. **수단이 하나면 화면을 건너뛰고 즉시 그 수단으로 진행한다** — `FM_IDENTITY_METHODS=mid` 인 발표 모드에서 불필요한 클릭이 생기지 않게 한다.

```jsx
export default function IdentityMethodStep({ methods, onPick }) {
  useEffect(() => {
    if (methods.length === 1) onPick(methods[0]);
  }, [methods, onPick]);
  if (methods.length <= 1) return null;
  return (
    <div className="wizard narrow">
      <h2>본인 확인 방법을 골라 주세요</h2>
      <button type="button" onClick={() => onPick('mid')}>
        모바일 신분증으로 확인
        <small>모바일 신분증 앱이 있으면 가장 빠릅니다</small>
      </button>
      <button type="button" onClick={() => onPick('simple_auth')}>
        간편인증으로 확인
        <small>PASS·카카오·네이버 등. 신분증을 찍어 올리는 단계가 있습니다</small>
      </button>
    </div>
  );
}
```

- [ ] **Step 2: 신분증 촬영·마스킹 화면**

`IdDocumentStep.jsx`. 순서:

1. 신분증 종류 선택 (`rrc` 주민등록증 / `dl` 운전면허증 / `passport` 여권 / `arc` 외국인등록번호)
2. 파일 선택 또는 카메라 촬영 (`<input type="file" accept="image/*" capture="environment">`)
3. `<canvas>` 에 그리고, 종류별 기본 마스킹 박스를 오버레이로 얹는다. 사용자가 드래그로 위치·크기를 조정한다
4. "가렸어요" 확인 체크
5. **전송 전 캔버스에 실제로 채운다** — `ctx.fillRect(box)` 로 픽셀을 덮어쓰고 `canvas.toBlob()` 한 결과만 업로드한다. 원본 File 객체는 전송하지 않는다

핵심 코드:

```jsx
  const buildMaskedBlob = useCallback(async () => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(imageRef.current, 0, 0, canvas.width, canvas.height);
    // 원본 위에 불투명 사각형을 얹는다. 이 캔버스에서 뽑은 blob 만 서버로 간다 —
    // 서버는 마스킹 여부를 검증할 수 없으므로 클라이언트가 확실히 덮어써야 한다.
    ctx.fillStyle = '#111';
    ctx.fillRect(mask.x, mask.y, mask.w, mask.h);
    return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92));
  }, [mask]);
```

고지 문구(화면에 고정 표시):

> 주민등록번호 뒷자리를 가린 뒤 올려 주세요. 신분증 사진은 본인 확인 심사에만 쓰고 **심사가 끝나면 바로 지웁니다.** 원본은 서버로 전송되지 않습니다.

- [ ] **Step 3: 위저드 배선**

`ModelRegister.jsx`:
- `step === 'method'` → `<IdentityMethodStep>`. 선택 결과를 `createEnrollment({ identityMethod })` 에 싣는다
- `step === 'id_capture'` → `<IdDocumentStep>`. 업로드 성공 시 `getEnrollment` 로 상태를 다시 읽고 `nextEnrollmentStep` 으로 넘어간다
- `step === 'review'` → 안내 화면. "검수 중이에요. 결과는 메일로 알려 드려요." + 홈으로 가기
- `runCxWidget` 을 인증 수단별로 가른다:

```js
      const options = enrollment?.identityMethod === 'simple_auth'
        ? { contentInfo: { signType: 'ENT_SIMPLE_AUTH' }, compareCI: false, isBirth: true }
        : { contentInfo: { signType: 'ENT_MID' }, compareCI: false, isBirth: true, useConvertor: true };
      const configUrl = enrollment?.identityMethod === 'simple_auth'
        ? CX_AUTH_CONFIG_URL
        : CX_CONFIG_URL;
```

`CX_AUTH_CONFIG_URL` 은 `import.meta.env.VITE_CX_AUTH_CONFIG_URL`. **없으면 간편인증 버튼을 비활성화**한다 — 설정 없이 위젯을 띄우면 v1.0 경로를 타서 조용히 실패한다.

경로 S 에서는 `portraitRef` 를 쓰지 않는다(`/complete` 에 `idPhotoHex` 를 싣지 않는다).

- [ ] **Step 4: 확인**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm build`
Expected: 빌드 성공

수동 확인: `VITE_FM_IDENTITY_METHODS=mid` 로 개발 서버를 띄워 **선택 화면이 안 뜨고 기존 UX 그대로**인지 본다.

- [ ] **Step 5: 커밋**

```bash
git add src/features/model/IdentityMethodStep.jsx src/features/model/IdDocumentStep.jsx src/features/model/ModelRegister.jsx
git commit -m "$(cat <<'EOF'
feat(facemarket): 인증 수단 선택과 신분증 촬영·마스킹 화면

마스킹은 캔버스에 실제 픽셀을 덮어쓴 뒤 그 blob 만 올린다. 원본 File 은
전송하지 않는다 — 서버가 마스킹 여부를 검증할 수 없으므로 클라이언트가
확실히 지워야 한다.

수단이 하나면 선택 화면을 건너뛴다. FM_IDENTITY_METHODS=mid 인 발표 모드에서
불필요한 클릭이 생기지 않는다.

VITE_CX_AUTH_CONFIG_URL 이 없으면 간편인증 버튼을 막는다 — 설정 없이 위젯을
띄우면 v1.0 경로를 타서 조용히 실패한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 12: 프론트 — 관리자 심사 콘솔

**Files:**
- Create: `src/features/admin/AdminEnrollmentReview.jsx`
- Modify: `src/features/admin/AdminShell.jsx`

**Interfaces:**
- Consumes: Task 10 의 관리자 API 4종, Task 8 의 카드 스키마
- Produces: `/admin/review` 라우트, `scoreRow(angle, score, threshold)` 표시 헬퍼(백분율·기준선·배수·배지)

- [ ] **Step 1: 심사 화면 구현**

좌측 큐(대기 목록), 우측 카드. 카드에 실리는 것:

1. **신분증 마스킹 전체본** — `images/id_document`
2. **등록 사진 3장** — `images/front`, `images/angle45`, `images/side`
3. **지원서** — 이름·생년월일·프로필 사진
4. **자동 대조 점수** — 스펙 §6.2.1 규격. **백분율 + 기준선 + 배수 + 배지**를 한 줄에 함께 띄운다:

```jsx
// 0.31 을 "31%" 로만 띄우면 관리자가 "69% 다르다는 뜻인가" 로 읽어 멀쩡한 본인을
// 거절한다. SFace 코사인은 동일인도 0.2~0.4 대라서 기준선을 나란히 박아야 한다.
function scoreRow(angle, score, threshold) {
  if (score == null) return { label: '– 대조 안 됨', tone: 'muted' };
  const ratio = score / threshold;
  const tone = score < threshold ? 'danger' : ratio >= 2 ? 'ok' : 'warn';
  const badge = score < threshold ? '✗ 미달' : ratio >= 2 ? '✓ 통과' : '△ 아슬';
  return {
    percent: `${Math.round(score * 100)}%`,
    baseline: `기준 ${Math.round(threshold * 100)}%`,
    multiple: `기준의 ${ratio.toFixed(1)}배`,
    badge, tone,
  };
}
```

화면 출력 예:

```
정면   31%   기준 15%   ✓ 통과 (기준의 2.1배)
45도   27%   기준 15%   ✓ 통과 (기준의 1.8배)
측면   11%   기준 10%   △ 아슬 (기준의 1.1배)
```

배지는 정보일 뿐 승인 버튼을 막지 않는다 — 빨강이어도 승인할 수 있고 초록이어도 거절할 수 있다. 위조 신분증은 진짜 얼굴이 찍혀 있어 점수가 높게 나오는 게 정상이다.
5. **CI 대조** — `identityMismatchCount`
6. **마스킹 확인 체크박스** — 체크해야 승인 버튼이 활성화된다

```jsx
        <label>
          <input type="checkbox" checked={maskOk}
                 onChange={(e) => setMaskOk(e.target.checked)} />
          주민등록번호 뒷자리가 가려져 있습니다
        </label>
        <button type="button" disabled={!maskOk} onClick={approve}>승인</button>
```

거절은 사유 선택(신분증-사진 불일치 / 신분증 판독 불가 / 마스킹 미이행 / 위조 의심 / 기타) + 자유 입력.

이미지는 `no-store` 라 캐시되지 않는다. 승인·거절 직후 카드를 닫고 큐를 새로고침한다 — 파기된 이미지를 다시 부르면 404 다.

- [ ] **Step 2: 탭 추가**

`AdminShell.jsx` 내비게이션에 "등록 심사" 를 넣고 `/admin/review` 로 라우팅한다(`AdminApplications` 선례).

- [ ] **Step 3: 확인**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm build`
Expected: 빌드 성공

- [ ] **Step 4: 커밋**

```bash
git add src/features/admin/AdminEnrollmentReview.jsx src/features/admin/AdminShell.jsx
git commit -m "$(cat <<'EOF'
feat(admin): 등록 심사 콘솔

신분증·등록 사진·지원서·자동 대조 점수를 한 화면에 놓고 사람이 판정한다.
마스킹 확인 체크를 해야 승인 버튼이 열린다 — 서버가 검증할 수 없는 항목을
사람이 메운다.

승인·거절 직후 카드를 닫는다. 신분증이 파기되므로 다시 부르면 404 다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 13: 법무 문서 개정 + 동의 버전

**Files:**
- Modify: `documents/legal/03_facemarket_privacy_policy_v1.md`
- Modify: `documents/legal/00_facemarket_legal_notice_map_v1.md`
- Modify: `documents/facemarket_apply_faq.md`
- Modify: `server/app/facemarket_enrollment.py:38`(`ACCEPTED_CONSENT_VERSIONS`)
- Modify: `src/features/model/ModelRegister.jsx:48`(`CONSENT_VERSION`)

**Interfaces:**
- Consumes: 없음
- Produces: 동의 버전 `"2026-09-v1"`

- [ ] **Step 1: 동의 버전 테스트**

`server/tests/test_facemarket_identity_method.py` 에 추가:

```python
def test_accepts_new_and_previous_consent_versions():
    from app.facemarket_enrollment import ACCEPTED_CONSENT_VERSIONS
    assert "2026-09-v1" in ACCEPTED_CONSENT_VERSIONS
    # 진행 중인 등록을 깨지 않는다.
    assert "2026-08-v2" in ACCEPTED_CONSENT_VERSIONS
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_identity_method.py -k consent -q`
Expected: FAIL

- [ ] **Step 3: 상수 갱신**

```python
ACCEPTED_CONSENT_VERSIONS = ("2026-09-v1", "2026-08-v2", "2026-08-v1")
```

`ModelRegister.jsx`: `const CONSENT_VERSION = '2026-09-v1';`

- [ ] **Step 4: 법무 문서 개정**

`03_facemarket_privacy_policy_v1.md`:
- §52 표의 "본인확인" 행에 간편인증 경로를 추가한다 — *"간편인증(민간 인증사) 본인확인 결과값 + 이용자가 제출한 신분증 사진(주민등록번호 뒷자리 마스킹). 신분증 사진은 등록 사진과의 동일인 심사 목적으로만 처리하며 심사 종료 즉시 파기(최대 7일)"*
- §72 의 *"회사는 신분증 원본 이미지를 저장하지 않고"* 를 경로별로 가른다 — 모바일 신분증 경로는 종전대로 미저장, 간편인증 경로는 마스킹본을 심사 기간에 한해 보관하고 즉시 파기

`00_facemarket_legal_notice_map_v1.md`:
- B-3 항목을 "신분증 얼굴 대조 보관" → 경로별 2개로 분리. 간편인증 경로에 수집 항목·보관 기간·파기 시점·마스킹 책임을 명시

`facemarket_apply_faq.md`:
- *"한국 모바일 신분증으로 본인확인을 해요"* → 두 경로 안내로 교체. 간편인증 경로에 신분증 촬영 단계와 관리자 심사 대기가 있다는 점, 신분증 사진은 심사 후 지운다는 점을 쓴다

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_identity_method.py -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add documents/legal/03_facemarket_privacy_policy_v1.md documents/legal/00_facemarket_legal_notice_map_v1.md documents/facemarket_apply_faq.md server/app/facemarket_enrollment.py src/features/model/ModelRegister.jsx server/tests/test_facemarket_identity_method.py
git commit -m "$(cat <<'EOF'
docs(legal): 간편인증 경로의 신분증 촬영본 수집·파기 고지

"신분증 원본 이미지를 저장하지 않는다"는 문장을 경로별로 가른다. 모바일
신분증 경로는 종전대로 미저장, 간편인증 경로는 마스킹본을 심사 기간에만
보관하고 즉시 파기한다.

동의 버전을 2026-09-v1 로 올리되 2026-08-v2 를 남겨 진행 중 등록을 깨지 않는다.

이 변경은 출시 전 개인정보 보호책임자 검토가 필요하다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 14: 배포 매니페스트 + 전체 회귀

**Files:**
- Modify: `copilot/api/manifest.yml`
- Modify: `docs/ARCHITECTURE.md` (③ 본인확인 다이어그램)

**Interfaces:**
- Consumes: Task 2 의 플래그 이름
- Produces: prod 기본값 (전부 off)

- [ ] **Step 1: 매니페스트 갱신**

`FM_OACX_CONTRACT_MODE` 줄 근처에 추가:

```yaml
  # 등록 위저드에 노출할 본인인증 수단. mid = 모바일 신분증만(현행 UX 그대로).
  # 간편인증을 켜려면 "mid,simple_auth" + VITE_CX_AUTH_CONFIG_URL(프론트) +
  # FM_OACX_SIMPLE_AUTH_CONTRACT=simple-auth-v1 이 함께 필요하다.
  # 해커톤 발표 모드 = 이 값을 mid 로 되돌리는 것.
  FM_IDENTITY_METHODS: "mid"
  # 관리자 육안 심사 범위. simple_auth_only = 촬영 신분증 경로만 사람이 본다.
  # 심사가 병목이 되면 off 로 즉시 해제할 수 있다.
  FM_ENROLLMENT_REVIEW: "simple_auth_only"
  # 간편인증 응답 스키마가 실거래로 확정되기 전까지 disabled — 호출 자체가 막힌다.
  # 확정 항목: (1) ci 포함 여부 (2) trans/{token} vs authen/result
  # (3) v1.5_enc userInfo 복호화 (4) 라온 auth config 발급·과금
  FM_OACX_SIMPLE_AUTH_CONTRACT: "disabled"
```

- [ ] **Step 2: 아키텍처 문서 갱신**

`docs/ARCHITECTURE.md:534` 의 "③ 본인확인 (cx_identity)" 서브그래프에 경로 S 를 추가한다.

- [ ] **Step 3: 백엔드 전체 회귀**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest -q --ignore=tests/test_personalization.py 2>&1 | tail -5`
Expected: 전부 PASS. 실패가 있으면 이 플랜이 건드린 파일과의 관계를 확인하고 고친 뒤 다시 돌린다

> `test_personalization.py` 는 로컬 dev 서버 JobDispatcher 가 같은 test DB 잡을 가로채 flake 가 나므로 제외한다(알려진 문제).

- [ ] **Step 4: 프론트 빌드**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm build`
Expected: 빌드 성공

- [ ] **Step 5: 플래그 off 동작 확인**

플래그가 전부 기본값일 때 기존 경로가 그대로인지 확인한다.

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_biometric_enrollment.py tests/test_facemarket_biometrics.py tests/test_facemarket_identity.py tests/test_facemarket_identity_compare.py -q`
Expected: PASS — 이 테스트들은 전부 플래그 기본값(mid)에서 돌고, 한 줄도 수정되지 않아야 한다

- [ ] **Step 6: 커밋 + 푸시**

```bash
git add copilot/api/manifest.yml docs/ARCHITECTURE.md
git commit -m "$(cat <<'EOF'
chore(facemarket): 신규 경로 플래그 prod 기본값(전부 off)

FM_IDENTITY_METHODS=mid 한 줄이 해커톤 발표 모드다 — 버튼 분기·신분증 촬영·
심사 대기가 전부 사라지고 현행 UX 로 돌아간다. 배포 없이 env 만 바꾸면 된다.

FM_OACX_SIMPLE_AUTH_CONTRACT 는 실거래 검증 전까지 disabled 를 유지한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
git push -u origin feat/fm-id-capture-branch
```

---

## 실거래 검증 체크리스트 (구현 이후, 별도)

간편인증을 실제로 켜기 전에 아래를 확정한다. 전부 스펙 §10 의 미검증 가정이다.

- [ ] 라온에 우리 서비스용 auth config 발급 요청 (`config.auth.json` 상당)
- [ ] `ENT_SIMPLE_AUTH` 결과에 `ci` 가 포함되는지 서면 확인
- [ ] 결과 취득 경로 확인 — `trans/{token}` 인지 `authen/result`/`parse_token` 인지
- [ ] `v1.5_enc` userInfo 복호화 규격 수령
- [ ] 간편인증 건당 과금 조건 확인
- [ ] 위 4개가 확정되면 `cx_identity.fetch_simple_auth_identity` 를 실제 경로로 구현하고 `FM_OACX_SIMPLE_AUTH_CONTRACT=simple-auth-v1` 로 승격
