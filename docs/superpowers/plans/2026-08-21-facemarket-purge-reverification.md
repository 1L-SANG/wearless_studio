# FaceMarket Purge and Reverification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 실물 모델을 재검증 대상으로 안전하게 동결하고, 생체 원본·파생물을 완전하고 재실행 가능하게 삭제하며, VC 폐기와 계정 삭제 전처리를 실패 복구 가능한 흐름으로 만든다.

**Architecture:** API와 worker의 신규 실물 모델 진입을 먼저 닫고, 최초 cutover에서는 provenance가 없는 레거시 작업 때문에 모든 pending `detail_page`/`editor_image` 작업을 환불 취소하고 모든 running 작업을 drain한 뒤 license와 model을 동결한다. 하나의 shared purge engine이 personalization 철회, cutover, 계정 삭제 전처리를 담당하며 R2 strict reconciliation 성공 뒤에만 DB 생체 참조를 제거한다. VC 폐기는 로컬 license 차단과 같은 transaction에서 durable queue에 기록하고 별도 dispatcher loop가 확인될 때까지 재시도한다.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 async connections, PostgreSQL/Supabase migrations, Cloudflare R2 through boto3, httpx, pytest.

**Spec:** `docs/superpowers/specs/2026-08-21-facemarket-biometric-runtime-hardening-design.md`

## Global Constraints

- 이 계획에서는 migration·앱 코드·테스트·dry-run 도구까지만 구현한다. 실제 cutover `--apply`는 실행하지 않는다.
- `fm_models`/`fm_licenses` status check와 `fm_biometric_enrollments` 참조는 biometric enrollment 계획이 소유한다. 이 계획은 해당 schema가 먼저 적용된 상태를 소비하며 재정의하지 않는다.
- `fm_vc_revocation_jobs`, enqueue/claim/retry worker, dispatcher loop는 mandatory VC 계획이 소유한다. 이 계획은 `enqueue_vc_revocation`을 호출하고 cutover/account purge 통합만 검증한다.
- 최초 cutover는 model/license provenance가 없는 레거시 job을 안전하게 구분할 수 없으므로 모든 pending `detail_page`와 `editor_image`를 취소·전액 환불하고, 모든 running `detail_page`와 `editor_image`를 0건까지 drain한다.
- pending/running `fm_model_asset_build`도 각각 취소·drain한다. running drain timeout이면 freeze와 R2 삭제를 시작하지 않는다.
- dry-run 출력과 audit에는 counts와 SHA-256 digest만 포함한다. raw R2 key, CI, 얼굴 digest, 전체 VC ID를 출력하지 않는다.
- license는 model보다 먼저 `reverification_required`로 바꾼다. 첫 freeze 상태만 `previous_status`에 보존하며 재실행이 이를 덮어쓰지 않는다.
- missing R2 object는 성공이다. 403, 5xx, timeout, prefix listing 실패는 성공이 아니며 DB key와 asset row를 남긴다.
- R2 strict reconciliation이 끝나기 전에는 purge/profile/batch를 완료 처리하지 않는다.
- local license revoke/freeze가 생성 차단의 authoritative lever다. remote Holder 장애가 local 차단을 되돌리지 않는다.
- account deletion은 shared purge engine을 호출하는 pre-delete 단계다. 이 저장소에 없는 Supabase Auth Admin 삭제를 성공한 것처럼 표시하지 않는다.
- record-only settlement audit는 남기되 `fm_models.user_id`, `ci_hash`, `did`, 얼굴 key/digest와 개인화 식별 자료를 제거해 사용자와 다시 연결할 수 없게 한다.
- 새 dependency를 추가하지 않는다.

Execution prerequisite order: first complete `2026-08-21-facemarket-biometric-enrollment.md` through its runtime migration, `2026-08-21-facemarket-mandatory-vc-cutover.md` through its durable revoke reconciler, and `2026-08-21-facemarket-runtime-authorization.md` through its `_facemarket` snapshot/worker gate. Then execute this plan. This plan's tests run those producer suites but do not edit their owned migrations, queue worker, reconciler lifecycle, route snapshot producer, or worker verifier.

## File Structure

| File | Responsibility |
| --- | --- |
| `supabase/migrations/20260821020000_facemarket_cutover_lifecycle.sql` | previous status, cutover batch linkage, nullable purged digest와 private RLS를 정의한다. |
| `server/app/r2.py` | 404만 absent로 취급하는 strict HEAD를 제공한다. |
| `server/app/services/biometric_purge.py` | 알려진 key와 prefix orphan을 두 bucket에서 삭제·reconcile하고 DB 정리를 수행하는 단일 엔진이다. |
| `server/app/facemarket_cutover.py` | manifest/digest, enqueue close, refund cancel, drain, freeze, 승인된 apply orchestration을 제공한다. |
| `server/app/repo.py` | pending job 하나를 lock하고 예약 credit을 idempotent하게 환불·error 종결한다. |
| `server/app/facemarket.py` | local revoke와 durable VC enqueue를 같은 transaction으로 묶고 worker용 local snapshot gate를 제공한다. |
| `server/app/routes.py` | Runtime authorization 계획이 `_facemarket` provenance를 소유한다. 이 계획은 account pre-delete endpoint와 cutover enqueue close만 추가한다. |
| `server/app/workers/detail_page_job.py` | Runtime authorization 계획이 snapshot 재검증·환불을 소유한다. 이 계획에서는 수정하지 않는다. |
| `server/app/workers/editor_image_job.py` | Runtime authorization 계획이 snapshot 재검증·환불을 소유한다. 이 계획에서는 수정하지 않는다. |
| `server/app/workers/fm_model_asset_job.py` | freeze 이후 asset build가 시작·완료되지 않게 한다. |
| `server/app/workers/personalization_purge_job.py` | 기존 별도 삭제 코드를 shared purge engine 호출로 축소한다. |
| `server/app/workers/fm_vc_revocation_reconciler.py` | Mandatory VC 계획이 소유한다. 이 계획은 `facemarket.enqueue_vc_revocation` 인터페이스만 소비한다. |
| `server/app/main.py` | Mandatory VC 계획이 소유하는 reconciler lifecycle을 소비하며 이 계획에서는 수정하지 않는다. |
| `server/scripts/facemarket_security_cutover.py` | 기본 read-only dry-run, batch 생성, 승인, apply guard CLI를 제공한다. |
| `server/tests/test_facemarket_cutover_migration.py` | migration shape와 선택적 실제 PostgreSQL 적용을 검증한다. |
| `server/tests/test_r2.py` | strict HEAD 오류 분류를 검증한다. |
| `server/tests/test_biometric_purge.py` | missing object, orphan, 부분 실패 resume, account anonymization을 검증한다. |
| `server/tests/test_facemarket_cutover.py` | refund cancel, global drain, manifest, approval/apply gate, idempotency를 검증한다. |
| `server/tests/test_facemarket_vc_revocation.py` | Mandatory VC 계획이 소유한다. 이 계획의 통합 검증에서 해당 contract suite를 함께 실행한다. |
| `server/tests/test_personalization.py` | personalization 철회가 FaceMarket 생체 자료까지 같은 엔진으로 파기하는 계약으로 갱신한다. |
| `server/tests/test_facemarket_licenses.py` | freeze/revoke/purge/account deletion 뒤 얼굴 endpoint가 404인지 검증한다. |

---

### Task 1: Add Cutover Linkage State

**Files:**
- Create: `supabase/migrations/20260821020000_facemarket_cutover_lifecycle.sql`
- Create: `server/tests/test_facemarket_cutover_migration.py`

**Interfaces:**
- Consumes: biometric plan status checks including `fm_models.status='reverification_required'` and `fm_licenses.status in ('pending','reverification_required')`; mandatory VC plan's `fm_vc_revocation_jobs`; existing `public.fm_models`, `public.fm_licenses`, `public.set_updated_at()`.
- Produces: `fm_models.previous_status`, `fm_models.reverification_batch_id`, `fm_licenses.previous_status`, `fm_licenses.reverification_batch_id`, `fm_cutover_batches`, nullable `fm_licenses.face_image_digest`.

- [ ] **Step 1: Write the failing static migration contract test**

Create `server/tests/test_facemarket_cutover_migration.py` with this exact static contract and the same `FACEMARKET_TEST_DATABASE_URL` opt-in convention used by `test_facemarket_cx_digest_migration.py`:

```python
import os
from pathlib import Path

import pytest
from psycopg import AsyncConnection
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20260821020000_facemarket_cutover_lifecycle.sql"
TEST_DATABASE_URL = os.getenv("FACEMARKET_TEST_DATABASE_URL")


def test_cutover_migration_declares_private_durable_state():
    sql = MIGRATION.read_text().lower()
    assert "previous_status" in sql
    assert "reverification_batch_id" in sql
    assert "create table if not exists public.fm_cutover_batches" in sql
    assert "alter table public.fm_cutover_batches enable row level security" in sql
    assert "drop not null" in sql
    assert "fm_models_status_check" not in sql
    assert "fm_licenses_status_check" not in sql
    assert "fm_vc_revocation_jobs" not in sql


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="FACEMARKET_TEST_DATABASE_URL is not configured",
)
@pytest.mark.asyncio
async def test_cutover_migration_applies_and_exposes_expected_columns():
    sql = MIGRATION.read_text()
    conn = await AsyncConnection.connect(TEST_DATABASE_URL, row_factory=dict_row)
    try:
        await conn.execute(sql)
        rows = await conn.execute(
            """
            select table_name, column_name, is_nullable
              from information_schema.columns
             where table_schema = 'public'
               and table_name in ('fm_models','fm_licenses','fm_cutover_batches')
            """
        )
        columns = {
            (row["table_name"], row["column_name"]): row["is_nullable"]
            async for row in rows
        }
        assert ("fm_models", "previous_status") in columns
        assert ("fm_licenses", "previous_status") in columns
        assert columns[("fm_licenses", "face_image_digest")] == "YES"
        assert ("fm_cutover_batches", "target_digest") in columns
    finally:
        await conn.rollback()
        await conn.close()
```

- [ ] **Step 2: Run the migration test to verify it fails**

Run:

```bash
cd server
.venv/bin/pytest tests/test_facemarket_cutover_migration.py -q
```

Expected: FAIL because `20260821020000_facemarket_cutover_lifecycle.sql` does not exist.

- [ ] **Step 3: Write the migration**

Create the migration with these definitions. Keep the named constraints because later checks and roll-forward migrations need stable names.

```sql
create table if not exists public.fm_cutover_batches (
  id uuid primary key default gen_random_uuid(),
  status text not null default 'planned'
    constraint fm_cutover_batches_status_check
    check (status in ('planned','approved','draining','applying','reconciling','completed','failed')),
  target_digest text not null,
  model_count integer not null check (model_count >= 0),
  license_count integer not null check (license_count >= 0),
  job_count integer not null check (job_count >= 0),
  asset_count integer not null check (asset_count >= 0),
  approved_by uuid references auth.users(id) on delete set null,
  approved_at timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  last_error_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.fm_models add column if not exists previous_status text;
alter table public.fm_models add column if not exists reverification_batch_id uuid
  references public.fm_cutover_batches(id) on delete set null;

alter table public.fm_licenses alter column face_image_digest drop not null;
alter table public.fm_licenses add column if not exists previous_status text;
alter table public.fm_licenses add column if not exists reverification_batch_id uuid
  references public.fm_cutover_batches(id) on delete set null;

create index if not exists fm_models_reverification_batch_idx
  on public.fm_models(reverification_batch_id);
create index if not exists fm_licenses_reverification_batch_idx
  on public.fm_licenses(reverification_batch_id);
drop trigger if exists fm_cutover_batches_set_updated_at on public.fm_cutover_batches;
create trigger fm_cutover_batches_set_updated_at
  before update on public.fm_cutover_batches
  for each row execute function public.set_updated_at();
alter table public.fm_cutover_batches enable row level security;
revoke all on public.fm_cutover_batches from anon, authenticated;
```

- [ ] **Step 4: Run the migration tests**

Run:

```bash
cd server
.venv/bin/pytest tests/test_facemarket_cutover_migration.py -q
```

Expected: static test PASS; PostgreSQL integration test PASS when `FACEMARKET_TEST_DATABASE_URL` is present and SKIP otherwise.

- [ ] **Step 5: Commit the schema boundary**

```bash
git add supabase/migrations/20260821020000_facemarket_cutover_lifecycle.sql server/tests/test_facemarket_cutover_migration.py
git commit -m "Make biometric cutover state durable and reversible" \
  -m "Constraint: Existing model and license status must be preserved before immediate reverification freeze." \
  -m "Rejected: Encoding cutover state in jobs payloads | jobs are user-cascaded and cannot own durable revocation state." \
  -m "Confidence: high" \
  -m "Scope-risk: moderate" \
  -m "Tested: pytest tests/test_facemarket_cutover_migration.py -q"
```

### Task 2: Add Strict R2 Existence Checks

**Files:**
- Modify: `server/app/r2.py:107-115`
- Modify: `server/tests/test_r2.py`

**Interfaces:**
- Consumes: boto3 `head_object`, `botocore.exceptions.ClientError`.
- Produces: `R2Client.head_strict(key: str) -> dict | None`; only confirmed not-found returns `None`.

- [ ] **Step 1: Write failing strict HEAD tests**

Extend `_StubS3` so it accepts a `head_result` or raises a supplied exception, then add:

```python
from botocore.exceptions import ClientError


def _client_for_head(result=None, error=None) -> R2Client:
    class HeadS3:
        def head_object(self, **kwargs):
            if error is not None:
                raise error
            return result

    client = object.__new__(R2Client)
    client._s3 = HeadS3()
    client._bucket = "test-bucket"
    return client


def _client_error(code: str, status: int) -> ClientError:
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "HeadObject",
    )


def test_head_strict_returns_none_only_for_not_found():
    client = _client_for_head(error=_client_error("NoSuchKey", 404))
    assert client.head_strict("facemarket/models/missing/front.png") is None


@pytest.mark.parametrize("code,status", [("AccessDenied", 403), ("InternalError", 500)])
def test_head_strict_raises_for_unconfirmed_absence(code, status):
    client = _client_for_head(error=_client_error(code, status))
    with pytest.raises(ClientError):
        client.head_strict("facemarket/models/model/front.png")


def test_head_strict_returns_normalized_metadata():
    client = _client_for_head(
        result={"ContentLength": 42, "ContentType": "image/png"}
    )
    assert client.head_strict("facemarket/models/model/front.png") == {
        "size": 42,
        "mime": "image/png",
    }
```

Add `import pytest` at the top of `test_r2.py`.

- [ ] **Step 2: Run strict HEAD tests to verify failure**

Run:

```bash
cd server
.venv/bin/pytest tests/test_r2.py -q
```

Expected: FAIL with `AttributeError: 'R2Client' object has no attribute 'head_strict'`.

- [ ] **Step 3: Implement strict HEAD without changing legacy `head` behavior**

Add this method next to `head`:

```python
    def head_strict(self, key: str) -> dict | None:
        """Return metadata, or None only when R2 confirms the object is absent."""
        try:
            result = self._s3.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            response = exc.response or {}
            code = str((response.get("Error") or {}).get("Code") or "")
            status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return {
            "size": result["ContentLength"],
            "mime": result.get("ContentType"),
        }
```

Do not rewrite existing `head`; upload completion and migration callers retain their current compatibility behavior while purge explicitly opts into `head_strict`.

- [ ] **Step 4: Run R2 tests**

Run:

```bash
cd server
.venv/bin/pytest tests/test_r2.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit strict reconciliation evidence**

```bash
git add server/app/r2.py server/tests/test_r2.py
git commit -m "Require positive R2 absence evidence before biometric cleanup" \
  -m "Constraint: Missing objects are success but storage authorization and server failures must remain retryable." \
  -m "Rejected: Reusing fail-open head() | it collapses 403 and 5xx into false absence." \
  -m "Confidence: high" \
  -m "Scope-risk: narrow" \
  -m "Tested: pytest tests/test_r2.py -q"
```

### Task 3: Build the Shared Complete Biometric Purge Engine

**Files:**
- Create: `server/app/services/biometric_purge.py`
- Create: `server/tests/test_biometric_purge.py`
- Modify: `server/app/workers/personalization_purge_job.py:1-260`
- Modify: `server/tests/test_personalization.py`

**Interfaces:**
- Consumes: `R2Client.delete`, `R2Client.list_prefix`, `R2Client.head_strict`, existing FaceMarket/personalization tables.
- Produces: `purge_biometric_scope(app, *, user_id: str | None, profile_ids: tuple[str, ...], model_ids: tuple[str, ...], reason: Literal["withdrawal", "reverification", "account_delete"], batch_id: str | None) -> PurgeResult`.
- Produces: `PurgeIncomplete(code: str)`; callers must leave their job/batch retryable when raised.

- [ ] **Step 1: Write failing engine tests with two independent fake buckets**

Create `server/tests/test_biometric_purge.py`. Its fake storage must distinguish confirmed absence from injected failures:

```python
import pytest

from app.services.biometric_purge import PurgeIncomplete, purge_biometric_scope


class StrictFakeR2:
    def __init__(self, keys):
        self.keys = set(keys)
        self.fail_delete = set()
        self.fail_list = set()

    def delete(self, key):
        if key in self.fail_delete:
            raise RuntimeError("delete_failed")
        self.keys.discard(key)

    def list_prefix(self, prefix):
        if prefix in self.fail_list:
            raise RuntimeError("list_failed")
        return sorted(key for key in self.keys if key.startswith(prefix))

    def head_strict(self, key):
        return {"size": 1, "mime": "image/png"} if key in self.keys else None


@pytest.mark.asyncio
async def test_purge_deletes_known_and_orphan_keys_from_both_buckets(purge_app, purge_db):
    result = await purge_biometric_scope(
        purge_app,
        user_id=purge_db.user_id,
        profile_ids=(purge_db.profile_id,),
        model_ids=(purge_db.model_id,),
        reason="withdrawal",
        batch_id=None,
    )
    assert result.complete is True
    assert result.key_count >= 9
    assert result.deleted_count == result.key_count
    assert purge_app.state.r2.keys == set()
    assert purge_app.state.r2_face.keys == set()
    assert purge_db.biometric_reference_count() == 0


@pytest.mark.asyncio
async def test_purge_partial_failure_preserves_db_references_for_resume(purge_app, purge_db):
    failing_key = f"facemarket/models/{purge_db.model_id}/face_front.png"
    purge_app.state.r2_face.fail_delete.add(failing_key)
    with pytest.raises(PurgeIncomplete) as exc:
        await purge_biometric_scope(
            purge_app,
            user_id=purge_db.user_id,
            profile_ids=(purge_db.profile_id,),
            model_ids=(purge_db.model_id,),
            reason="reverification",
            batch_id=purge_db.batch_id,
        )
    assert exc.value.code == "r2_delete_failed"
    assert purge_db.biometric_reference_count() > 0

    purge_app.state.r2_face.fail_delete.clear()
    result = await purge_biometric_scope(
        purge_app,
        user_id=purge_db.user_id,
        profile_ids=(purge_db.profile_id,),
        model_ids=(purge_db.model_id,),
        reason="reverification",
        batch_id=purge_db.batch_id,
    )
    assert result.complete is True
    assert purge_db.biometric_reference_count() == 0


@pytest.mark.asyncio
async def test_missing_objects_are_idempotent_success(purge_app, purge_db):
    purge_app.state.r2.keys.clear()
    purge_app.state.r2_face.keys.clear()
    first = await purge_biometric_scope(
        purge_app,
        user_id=purge_db.user_id,
        profile_ids=(purge_db.profile_id,),
        model_ids=(purge_db.model_id,),
        reason="withdrawal",
        batch_id=None,
    )
    second = await purge_biometric_scope(
        purge_app,
        user_id=purge_db.user_id,
        profile_ids=(purge_db.profile_id,),
        model_ids=(purge_db.model_id,),
        reason="withdrawal",
        batch_id=None,
    )
    assert first.complete is True
    assert second.complete is True
    assert second.deleted_count == 0
```

Use the real local PostgreSQL adapter pattern from `test_personalization.py` for `purge_db`. Seed exactly one user, model, license, `face_front`, `grid_sedcard`, three personalization photos, one personalization generation result, one completed real-model detail/editor job output, and one orphan per bucket. The fixture's `biometric_reference_count()` must count non-null face keys/digests, FaceMarket asset rows, personalization biometric rows, and `assets` rows created under the affected real-model job prefixes.

- [ ] **Step 2: Run the purge engine tests to verify failure**

Run:

```bash
cd server
.venv/bin/pytest tests/test_biometric_purge.py -q
```

Expected: FAIL because `app.services.biometric_purge` does not exist.

- [ ] **Step 3: Implement the engine with a PII-free result type**

Create `server/app/services/biometric_purge.py` with these public types and deterministic prefixes:

```python
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Literal


PurgeReason = Literal["withdrawal", "reverification", "account_delete"]


class PurgeIncomplete(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, repr=False)
class PurgeResult:
    complete: bool
    key_count: int
    deleted_count: int
    model_count: int
    profile_count: int
    key_digest: str


def _prefixes(
    *,
    user_id: str | None,
    profile_ids: tuple[str, ...],
    model_ids: tuple[str, ...],
    enrollment_ids: tuple[str, ...],
) -> tuple[str, ...]:
    values = [f"facemarket/models/{model_id}/" for model_id in model_ids]
    values.extend(
        f"facemarket/enrollments/{enrollment_id}/"
        for enrollment_id in enrollment_ids
    )
    values.extend(f"personalization/profiles/{profile_id}/faces/" for profile_id in profile_ids)
    if user_id is not None:
        values.append(f"personalization/{user_id}/generations/")
    return tuple(sorted(set(values)))


def _digest(keys: set[tuple[str, str]]) -> str:
    material = "\n".join(f"{bucket}:{key}" for bucket, key in sorted(keys))
    return hashlib.sha256(material.encode()).hexdigest()
```

The implementation must perform these exact phases:

1. Query known keys from `fm_licenses.face_image_key`, `fm_model_assets.r2_key`, `personalization_face_photos.r2_key`, and every `personalization_generations.result_keys` entry for the supplied IDs. Query `fm_biometric_enrollments.id` for the affected models and pass those IDs to `_prefixes`; never substitute a model ID for an enrollment ID.
2. Resolve every derived detail/editor job tied to an affected model by any of: `payload #>> '{_facemarket,modelId}'`, `fm_settlements.job_id` through an affected license, or `jobs.project_id` through `projects.facemarket_license_id`. Add `users/{job.user_id}/projects/{job.project_id}/ai/{job.id}/` to the R2 prefixes and include matching `assets.r2_key` rows as known targets. This legacy fallback is required because pre-snapshot jobs do not carry `_facemarket`.
3. Enumerate every `_prefixes` and derived-job prefix against both `app.state.r2_face` and `app.state.r2`; deduplicate targets as `(bucket_label, key)` tuples in memory.
4. Delete all targets with `asyncio.to_thread`. Record only counts; never log keys or `PurgeResult` internals.
5. Re-list every prefix and call `head_strict` for every originally known target. A delete/list/head exception raises `PurgeIncomplete("r2_delete_failed")` or `PurgeIncomplete("r2_reconcile_failed")` before DB cleanup.
6. In one DB transaction after confirmed absence:
   - delete `fm_model_assets` for model IDs;
   - set `fm_models.assets_status='none', qc_score=null, assets_source_hash=null`;
   - set matching licenses `face_image_key=null, face_image_digest=null`;
   - delete matching `personalization_face_photos`, `personalization_generations`, and `personalization_identity_verifications`;
   - set `wardrobe_images.source_asset_id=null` where it points at a derived target, delete target `wardrobe_images`, delete target `assets`, and set `projects.editor_blocks=null` for affected detail-page projects;
   - clear personalization body columns and set profiles to `purged`, with `purged_at=now()`;
   - for `account_delete`, delete `fm_identity_verifications`, personalization consent/audit rows, set license status `revoked`, and anonymize model/profile identifiers as specified in Task 7.
7. Return counts and `_digest(targets)` only.

Use `app.state.pool.connection()` inside the service so all three callers share the same behavior. Do not accept raw R2 keys as a public argument.

- [ ] **Step 4: Replace the personalization worker deletion loop with the shared engine**

In `run_personalization_purge_job`, keep pending personalization generation cancellation and lease-fenced job finalization, but replace its R2/DB deletion block with:

```python
from ..services.biometric_purge import PurgeIncomplete, purge_biometric_scope


        try:
            result = await purge_biometric_scope(
                app,
                user_id=user_id,
                profile_ids=(str(profile_id),),
                model_ids=(),
                reason=str(payload.get("reason") or "withdrawal"),
                batch_id=payload.get("batchId"),
            )
        except PurgeIncomplete as exc:
            await _fail(
                "생체정보 삭제 확인이 완료되지 않았어요. 자동 재시도가 필요해요.",
                {"error": exc.code},
            )
            return
```

When `payload.reason == "withdrawal"`, query all `fm_models.id` owned by `user_id` and pass them as `model_ids`. Remove the duplicate `purge_started` audit insertion from the worker because `_start_purge` already records it.

- [ ] **Step 5: Update personalization contract tests**

Replace the assertion in the existing test whose name contains `preserves_facemarket_records` with assertions that record-only rows remain but are unusable:

```python
assert license_row["status"] == "revoked"
assert license_row["face_image_key"] is None
assert license_row["face_image_digest"] is None
assert model_row["assets_status"] == "none"
assert model_asset_count == 0
```

Retain the existing assertions that the personalization profile is `purged`, its photo/generation/identity rows are gone, and the worker can run twice.

- [ ] **Step 6: Run purge and personalization tests**

Run:

```bash
cd server
.venv/bin/pytest tests/test_biometric_purge.py tests/test_personalization.py -q
```

Expected: PASS with no external R2 or Holder calls.

- [ ] **Step 7: Commit the single purge engine**

```bash
git add server/app/services/biometric_purge.py server/app/workers/personalization_purge_job.py server/tests/test_biometric_purge.py server/tests/test_personalization.py
git commit -m "Make biometric purge complete before database cleanup" \
  -m "Constraint: R2 absence must be reconciled across private and legacy buckets before deleting discovery references." \
  -m "Rejected: Separate cutover and personalization deletion loops | divergence would reintroduce partial-success data residue." \
  -m "Confidence: high" \
  -m "Scope-risk: moderate" \
  -m "Directive: All future biometric deletion entry points must call purge_biometric_scope." \
  -m "Tested: pytest tests/test_biometric_purge.py tests/test_personalization.py -q"
```

### Task 4: Consume Server-Owned Job Provenance for Purge and Cutover

**Files:**
- Modify: `server/app/repo.py`
- Modify: `server/tests/test_facemarket_cutover.py`
- Modify: `server/tests/test_biometric_purge.py`

**Interfaces:**
- Consumes: runtime authorization plan's server-owned payload `{"brandUseCategory": str | None, "_facemarket": {"modelId": str, "licenseId": str}}`.
- Consumes: runtime authorization plan's snapshot-pinned worker recheck, full credit release, zero result/settlement denial contract.
- Produces: `repo.find_pending_facemarket_jobs(conn, *, model_ids: tuple[str, ...]) -> list[dict]` and `repo.count_running_facemarket_jobs(conn, *, model_ids: tuple[str, ...]) -> int`; no route or worker implementation is duplicated here.

- [ ] **Step 1: Write failing provenance-consumer tests**

Add to `test_facemarket_cutover.py`:

```python
@pytest.mark.asyncio
async def test_user_purge_finds_jobs_by_server_owned_facemarket_snapshot(db_conn):
    await seed_job(
        db_conn,
        kind="detail_page",
        status="pending",
        payload={
            "brandUseCategory": "일반 여성 의류",
            "_facemarket": {"modelId": MODEL_ID, "licenseId": LICENSE_ID},
        },
    )
    await seed_job(
        db_conn,
        kind="detail_page",
        status="pending",
        payload={"mode": "generate"},
    )
    rows = await repo.find_pending_facemarket_jobs(db_conn, model_ids=(MODEL_ID,))
    assert [row["payload"]["_facemarket"]["licenseId"] for row in rows] == [LICENSE_ID]
```

Add an account purge test asserting only snapshot-matched future jobs are targeted after the initial global cutover has completed.

- [ ] **Step 2: Run the consumer tests to verify failure**

Run:

```bash
cd server
.venv/bin/pytest tests/test_facemarket_cutover.py tests/test_biometric_purge.py -k "facemarket_snapshot" -q
```

Expected: FAIL because `repo.find_pending_facemarket_jobs` does not exist.

- [ ] **Step 3: Implement the snapshot query without trusting caller payloads**

Add to `repo.py`:

```python
async def find_pending_facemarket_jobs(
    conn: AsyncConnection, *, model_ids: tuple[str, ...]
) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            """select id::text as id, user_id::text as user_id, kind, payload
                 from jobs
                where status='pending'
                  and kind in ('detail_page','editor_image')
                  and payload #>> '{_facemarket,modelId}' = any(%s)
                order by created_at
                for update skip locked""",
            (list(model_ids),),
        )
        return await cur.fetchall()


async def count_running_facemarket_jobs(
    conn: AsyncConnection, *, model_ids: tuple[str, ...]
) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            """select count(*)::int as count
                 from jobs
                where status='running'
                  and kind in ('detail_page','editor_image')
                  and payload #>> '{_facemarket,modelId}' = any(%s)""",
            (list(model_ids),),
        )
        return int((await cur.fetchone())["count"])
```

This query is for later user/account purge only. The first production cutover still cancels every pending detail/editor job and drains every running detail/editor job because legacy payloads lack `_facemarket`.

- [ ] **Step 4: Run the runtime producer and purge consumer contracts together**

Run:

```bash
cd server
.venv/bin/pytest tests/test_routes.py tests/test_detail_page.py tests/test_cut_input_authority.py tests/test_facemarket_cutover.py tests/test_biometric_purge.py -k "facemarket or snapshot or purge" -q
```

Expected: PASS. Runtime authorization owns payload production and worker recheck; this task verifies the purge consumer uses the same names.

- [ ] **Step 5: Commit provenance consumption only**

```bash
git add server/app/repo.py server/tests/test_facemarket_cutover.py server/tests/test_biometric_purge.py
git commit -m "Use the canonical real-model job snapshot for later purges" \
  -m "Constraint: Runtime authorization owns payload production and worker denial behavior." \
  -m "Rejected: A second flat model/license payload | it would diverge from the server-owned _facemarket contract." \
  -m "Confidence: high" \
  -m "Scope-risk: narrow" \
  -m "Tested: runtime snapshot producer suites plus purge consumer tests"
```

### Task 5: Add Idempotent Pending Cancellation, Global Drain, and Refund

**Files:**
- Modify: `server/app/repo.py:1464-1656`
- Create: `server/app/facemarket_cutover.py`
- Create: `server/tests/test_facemarket_cutover.py`

**Interfaces:**
- Consumes: `repo.release_credits`, `credit:job:{job_id}:settle` idempotency key.
- Produces: `repo.cancel_pending_job_with_refund(conn, *, job_id: str, code: str, message: str) -> bool`.
- Produces: `cancel_initial_cutover_jobs(pool, *, batch_id: str) -> int` and `drain_initial_cutover_jobs(pool, *, timeout_seconds: float, poll_interval_seconds: float = 0.25) -> None`.

- [ ] **Step 1: Write failing cancellation/refund tests**

Use the real local PostgreSQL fixture pattern so ledger idempotency is tested rather than mocked:

```python
@pytest.mark.asyncio
async def test_cancel_pending_job_refunds_once(db_conn, seeded_reserved_job):
    first = await repo.cancel_pending_job_with_refund(
        db_conn,
        job_id=seeded_reserved_job.id,
        code="facemarket_cutover",
        message="실물 모델 보안 전환으로 작업을 취소하고 크레딧을 돌려드렸어요.",
    )
    second = await repo.cancel_pending_job_with_refund(
        db_conn,
        job_id=seeded_reserved_job.id,
        code="facemarket_cutover",
        message="실물 모델 보안 전환으로 작업을 취소하고 크레딧을 돌려드렸어요.",
    )
    await db_conn.commit()
    assert first is True
    assert second is False
    assert await reserved_amount(db_conn, seeded_reserved_job.user_id) == 0
    assert await release_ledger_count(db_conn, seeded_reserved_job.id) == 1
    assert await job_status(db_conn, seeded_reserved_job.id) == "error"


@pytest.mark.asyncio
async def test_initial_cutover_cancels_all_pending_detail_and_editor_jobs(cutover_pool):
    await seed_jobs(
        cutover_pool,
        pending=("detail_page", "editor_image", "fm_model_asset_build", "analyze"),
    )
    cancelled = await cancel_initial_cutover_jobs(cutover_pool, batch_id=BATCH_ID)
    assert cancelled == 3
    assert await statuses(cutover_pool, "detail_page") == ["error"]
    assert await statuses(cutover_pool, "editor_image") == ["error"]
    assert await statuses(cutover_pool, "fm_model_asset_build") == ["error"]
    assert await statuses(cutover_pool, "analyze") == ["pending"]


@pytest.mark.asyncio
async def test_drain_timeout_aborts_before_freeze(cutover_pool):
    await seed_jobs(cutover_pool, running=("detail_page",))
    with pytest.raises(CutoverBlocked) as exc:
        await drain_initial_cutover_jobs(
            cutover_pool, timeout_seconds=0.01, poll_interval_seconds=0.001
        )
    assert exc.value.code == "running_jobs_not_drained"
    assert await frozen_model_count(cutover_pool) == 0
```

- [ ] **Step 2: Run cancellation tests to verify failure**

Run:

```bash
cd server
.venv/bin/pytest tests/test_facemarket_cutover.py -k "cancel or drain" -q
```

Expected: FAIL because the repository and cutover functions do not exist.

- [ ] **Step 3: Implement one-job refund cancellation**

Add to `repo.py`:

```python
async def cancel_pending_job_with_refund(
    conn: AsyncConnection, *, job_id: str, code: str, message: str
) -> bool:
    async with conn.cursor() as cur:
        await cur.execute(
            """select id::text as id, user_id::text as user_id,
                      project_id::text as project_id, credits_reserved
                 from jobs
                where id = %s and status = 'pending'
                for update""",
            (job_id,),
        )
        job = await cur.fetchone()
    if job is None:
        return False
    await release_credits(
        conn,
        user_id=job["user_id"],
        project_id=job["project_id"],
        job_id=job_id,
        reserved=int(job["credits_reserved"] or 0),
        settle_key=f"credit:job:{job_id}:settle",
        metadata={"reason": code},
    )
    async with conn.cursor() as cur:
        await cur.execute(
            """update jobs
                  set status='error', error_message=%s, locked_by=null,
                      locked_at=null, finished_at=now()
                where id=%s""",
            (message, job_id),
        )
        await cur.execute(
            "insert into job_events(job_id,event_type,payload) values (%s,'error',%s)",
            (job_id, Json({"code": code, "message": message})),
        )
    return True
```

Use `error`, not `cancelled`, because the current `job_events` constraint does not consistently accept a `cancelled` event in all deployed schemas.

- [ ] **Step 4: Implement global initial cancellation and drain**

Create the first part of `facemarket_cutover.py`:

```python
import asyncio

from psycopg.types.json import Json

from . import repo

INITIAL_CUTOVER_KINDS = ("detail_page", "editor_image", "fm_model_asset_build")


class CutoverBlocked(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


async def cancel_initial_cutover_jobs(pool, *, batch_id: str) -> int:
    cancelled = 0
    while True:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """select id::text as id
                         from jobs
                        where status='pending' and kind = any(%s)
                        order by created_at
                        for update skip locked
                        limit 50""",
                    (list(INITIAL_CUTOVER_KINDS),),
                )
                rows = await cur.fetchall()
            for row in rows:
                changed = await repo.cancel_pending_job_with_refund(
                    conn,
                    job_id=row["id"],
                    code="facemarket_cutover",
                    message="실물 모델 보안 전환으로 작업을 취소하고 크레딧을 돌려드렸어요.",
                )
                cancelled += int(changed)
                if changed:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "update jobs set metadata=metadata || %s where id=%s",
                            (Json({"cutoverBatchId": batch_id}), row["id"]),
                        )
            await conn.commit()
        if not rows:
            return cancelled


async def drain_initial_cutover_jobs(
    pool, *, timeout_seconds: float, poll_interval_seconds: float = 0.25
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select count(*)::int as count from jobs where status='running' and kind = any(%s)",
                    (list(INITIAL_CUTOVER_KINDS),),
                )
                count = int((await cur.fetchone())["count"])
        if count == 0:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise CutoverBlocked("running_jobs_not_drained")
        await asyncio.sleep(poll_interval_seconds)
```

The cutover orchestrator must call both functions before any status freeze. It must not forcibly terminalize running jobs because provider calls can still return and write late objects.

- [ ] **Step 5: Run cancellation and drain tests**

Run:

```bash
cd server
.venv/bin/pytest tests/test_facemarket_cutover.py -k "cancel or drain" -q
```

Expected: PASS.

- [ ] **Step 6: Commit cancellation and drain primitives**

```bash
git add server/app/repo.py server/app/facemarket_cutover.py server/tests/test_facemarket_cutover.py
git commit -m "Drain legacy generation work before biometric freeze" \
  -m "Constraint: Legacy detail and editor jobs do not carry reliable FaceMarket provenance." \
  -m "Rejected: Force-cancelling running providers | late responses can recreate deleted biometric derivatives." \
  -m "Confidence: high" \
  -m "Scope-risk: moderate" \
  -m "Directive: Initial cutover always cancels every pending detail/editor job and drains every running detail/editor job." \
  -m "Tested: pytest tests/test_facemarket_cutover.py -k 'cancel or drain' -q"
```

### Task 6: Integrate the Mandatory Durable VC Queue

**Files:**
- Modify: `server/app/facemarket.py:1560-1640`
- Modify: `server/app/services/biometric_purge.py`
- Modify: `server/app/facemarket_cutover.py`
- Modify: `server/tests/test_facemarket_licenses.py`
- Modify: `server/tests/test_facemarket_cutover.py`
- Modify: `server/tests/test_biometric_purge.py`

**Interfaces:**
- Consumes: mandatory VC plan's `server/app/facemarket.py` function `enqueue_vc_revocation(conn, *, license_id: str, model_id: str, vc_id: str) -> None` and `server/app/workers/fm_vc_revocation_reconciler.py`.
- Consumes: mandatory VC plan's durable `fm_vc_revocation_jobs` table, retry worker, stale-lease recovery, Holder revoke/verify behavior, and separate dispatcher polling loop.
- Produces: atomic enqueue integration for owner revoke, cutover freeze, withdrawal purge, and account pre-delete. This task does not create or modify the queue schema/worker/dispatcher.

- [ ] **Step 1: Write failing atomic integration tests**

Add one assertion per local transition:

```python
def test_owner_revoke_commits_local_block_and_durable_enqueue_together(fm, monkeypatch):
    calls = []

    async def enqueue(cur, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(facemarket, "enqueue_vc_revocation", enqueue)
    response = fm.client.post(
        f"/v1/facemarket/licenses/{fm.license_id}/revoke",
        headers=fm.owner_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "revoked"
    assert calls == [{
        "vc_id": fm.vc_id,
        "model_id": fm.model_id,
        "license_id": fm.license_id,
    }]


@pytest.mark.asyncio
async def test_cutover_enqueues_every_vc_once(cutover_app, approved_batch):
    await apply_cutover(
        cutover_app,
        batch_id=approved_batch,
        confirmation=approved_batch,
        drain_timeout_seconds=1.0,
    )
    await apply_cutover(
        cutover_app,
        batch_id=approved_batch,
        confirmation=approved_batch,
        drain_timeout_seconds=1.0,
    )
    assert await revoke_queue_count(cutover_app.pool) == 1


@pytest.mark.asyncio
async def test_account_purge_enqueues_before_anonymizing_license(purge_app, purge_db):
    await purge_biometric_scope(
        purge_app,
        user_id=purge_db.user_id,
        profile_ids=(purge_db.profile_id,),
        model_ids=(purge_db.model_id,),
        reason="account_delete",
        batch_id=None,
    )
    queued = purge_db.revocation_job()
    assert queued["license_id"] == purge_db.license_id
```

Add a rollback test where the mandatory enqueue function raises and assert the local license status remains active after the request transaction rolls back.

- [ ] **Step 2: Run integration tests to verify failure**

Run:

```bash
cd server
.venv/bin/pytest tests/test_facemarket_licenses.py tests/test_facemarket_cutover.py tests/test_biometric_purge.py -k "enqueue or revoke_queue" -q
```

Expected: FAIL because current owner revoke still performs a best-effort remote call and purge/cutover do not enqueue the durable job.

- [ ] **Step 3: Make owner revoke atomic with mandatory enqueue**

Use the mandatory interface already defined in `facemarket.py`. In `revoke_license`, remove `_revoke_holder_vc`. After updating local status and before `conn.commit()`, call:

```python
await enqueue_vc_revocation(
    conn,
    license_id=license_id,
    model_id=lic["model_id"],
    vc_id=lic["vc_id"],
)
```

Only call when `lic["vc_id"]` is present. Repeated owner revoke remains 200 and relies on the mandatory queue's `vc_id` conflict contract for idempotency.

- [ ] **Step 4: Enqueue scope revocations before purge anonymization**

In the shared purge engine, select `id`, `model_id`, and `vc_id` for every affected license. In the same DB transaction that first makes licenses non-active, call `enqueue_vc_revocation(conn, license_id=row["id"], model_id=row["model_id"], vc_id=row["vc_id"])` for every non-null VC. Only after successful enqueue may the account branch null identifying model fields.

In `_enqueue_batch_vc_revocations`, lock the batch's licenses and call the same mandatory interface. Do not insert directly into `fm_vc_revocation_jobs` from this plan.

- [ ] **Step 5: Run durable queue integration plus mandatory queue contract tests**

Run:

```bash
cd server
.venv/bin/pytest tests/test_facemarket_licenses.py tests/test_facemarket_cutover.py tests/test_biometric_purge.py tests/test_facemarket_vc_revocation.py -q
```

Expected: PASS. The final file is owned by the mandatory VC plan; this command verifies that this plan consumes its contract without duplicating it.

- [ ] **Step 6: Commit queue integration only**

```bash
git add server/app/facemarket.py server/app/services/biometric_purge.py server/app/facemarket_cutover.py server/tests/test_facemarket_licenses.py server/tests/test_facemarket_cutover.py server/tests/test_biometric_purge.py
git commit -m "Route biometric shutdowns through the durable VC queue" \
  -m "Constraint: Queue schema, retry worker, and dispatcher are owned by the mandatory VC implementation plan." \
  -m "Rejected: Direct queue SQL in purge and cutover | it would duplicate the mandatory VC contract." \
  -m "Confidence: high" \
  -m "Scope-risk: moderate" \
  -m "Tested: pytest tests/test_facemarket_licenses.py tests/test_facemarket_cutover.py tests/test_biometric_purge.py tests/test_facemarket_vc_revocation.py -q"
```

### Task 7: Add Idempotent Account Pre-Delete and Anonymization

**Files:**
- Modify: `server/app/routes.py:463-490`
- Modify: `server/app/personalization.py:393-440`
- Modify: `server/app/services/biometric_purge.py`
- Modify: `server/app/workers/personalization_purge_job.py`
- Modify: `server/tests/test_biometric_purge.py`
- Modify: `server/tests/test_facemarket_licenses.py`

**Interfaces:**
- Consumes: `purge_biometric_scope(..., reason="account_delete")`, durable VC enqueue, personalization purge singleton job.
- Produces: `DELETE /v1/me/account -> 202 {jobId: str, status: "purging"}` as a biometric pre-delete hook.
- Produces: `_start_purge(conn, user_id: str, profile: dict | None, *, reason: str) -> str`.

- [ ] **Step 1: Write failing account pre-delete tests**

Add route and completed-worker tests:

```python
def test_delete_account_enqueues_one_account_purge_job(client, db, auth_headers):
    first = client.delete("/v1/me/account", headers=auth_headers)
    second = client.delete("/v1/me/account", headers=auth_headers)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["jobId"] == second.json()["jobId"]
    job = db.one("select payload from jobs where id=%s", (first.json()["jobId"],))
    assert job["payload"]["reason"] == "account_delete"


@pytest.mark.asyncio
async def test_account_purge_anonymizes_all_biometric_links(purge_app, purge_db):
    result = await purge_biometric_scope(
        purge_app,
        user_id=purge_db.user_id,
        profile_ids=(purge_db.profile_id,),
        model_ids=(purge_db.model_id,),
        reason="account_delete",
        batch_id=None,
    )
    assert result.complete is True
    model = purge_db.model()
    assert model["user_id"] is None
    assert model["ci_hash"] is None
    assert model["did"] is None
    assert model["cover_image_url"] is None
    assert model["display_name"] == "삭제된 모델"
    assert model["status"] == "suspended"
    assert purge_db.fm_identity_count() == 0
    assert purge_db.personalization_identity_count() == 0
    assert purge_db.personalization_consent_count() == 0
    assert purge_db.personalization_audit_count() == 0
    assert purge_db.settlement_count() == 1
```

Add API assertions that thumbnail and owner face endpoints return 404 after completion and none of the relevant biometric endpoints return 200.

- [ ] **Step 2: Run account deletion tests to verify failure**

Run:

```bash
cd server
.venv/bin/pytest tests/test_biometric_purge.py tests/test_facemarket_licenses.py -k "account or deletion" -q
```

Expected: FAIL because the DELETE route and account anonymization branch do not exist.

- [ ] **Step 3: Generalize `_start_purge` for withdrawal and account deletion**

Change the signature to accept an optional profile and explicit reason. The queued payload contains identifiers needed by the worker but never keys/digests:

```python
async def _start_purge(
    conn, user_id: str, profile: dict | None, *, reason: str = "withdrawal"
) -> str:
    profile_id = str(profile["id"]) if profile is not None else None
    existing = await _active_purge_job_id(conn, user_id)
    if existing is not None:
        return existing
    payload = {"reason": reason}
    if profile_id is not None:
        payload["profileId"] = profile_id
    job, created = await repo.create_job(
        conn,
        user_id=user_id,
        project_id=None,
        kind="personalization_purge",
        payload=payload,
        idempotency_key=None,
        credits_reserved=0,
        metadata={"reason": reason},
    )
    if created and profile_id is not None:
        async with conn.cursor() as cur:
            await cur.execute(
                """update personalization_profiles
                      set status='purging', withdrawn_at=coalesce(withdrawn_at,now())
                    where id=%s and user_id=%s and status <> 'purged'""",
                (profile_id, user_id),
            )
    return str(job["id"])
```

Retain the existing unique active purge job by `user_id`; repeated withdrawal/account requests return the active job.

- [ ] **Step 4: Add the pre-delete route**

Add immediately after `GET /me/account` in `routes.py`:

```python
@router.delete(
    "/me/account",
    status_code=202,
    tags=["User & Account"],
    summary="계정 삭제 전 생체정보 파기 시작",
)
async def delete_account(request: Request, user_id: str = Depends(require_user)):
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """select id::text as id, status
                     from personalization_profiles
                    where user_id=%s and status <> 'purged'
                    order by created_at desc limit 1
                    for update""",
                (user_id,),
            )
            profile = await cur.fetchone()
        job_id = await personalization._start_purge(
            conn, user_id, profile, reason="account_delete"
        )
        await conn.commit()
    _wake_dispatcher(request)
    return {"jobId": job_id, "status": "purging"}
```

Import the `personalization` module in `routes.py`. This endpoint is a pre-delete hook: it must not claim the Supabase Auth user has been deleted.

- [ ] **Step 5: Implement account anonymization after R2 reconciliation**

Inside the shared engine's final DB transaction for `account_delete`, execute:

```sql
update fm_licenses
   set status='revoked', face_image_key=null, face_image_digest=null
 where model_id = any(%s);
delete from fm_identity_verifications where model_id = any(%s);
update fm_models
   set status='suspended', user_id=null, ci_hash=null, did=null,
       cover_image_url=null, display_name='삭제된 모델',
       assets_status='none', qc_score=null, assets_source_hash=null
 where id = any(%s);
delete from personalization_identity_verifications where user_id=%s;
delete from personalization_consents where user_id=%s;
delete from personalization_audit_log where user_id=%s;
update profiles
   set display_name=null, avatar_asset_id=null
 where user_id=%s;
```

Enqueue VC revocations before the licenses are anonymized. Preserve `fm_settlements`; its nullable job/license links and model reference remain record-only and no longer resolve to a user-owned model.

- [ ] **Step 6: Run account tests**

Run:

```bash
cd server
.venv/bin/pytest tests/test_biometric_purge.py tests/test_facemarket_licenses.py tests/test_personalization.py -k "account or deletion or purge" -q
```

Expected: PASS.

- [ ] **Step 7: Commit account pre-delete**

```bash
git add server/app/routes.py server/app/personalization.py server/app/services/biometric_purge.py server/app/workers/personalization_purge_job.py server/tests/test_biometric_purge.py server/tests/test_facemarket_licenses.py server/tests/test_personalization.py
git commit -m "Remove biometric identity before account deletion" \
  -m "Constraint: This repository has no Supabase Auth Admin deletion client, so the endpoint is a verified pre-delete hook." \
  -m "Rejected: Deleting auth.users first | cascades would erase the rows needed to discover and reconcile R2 objects." \
  -m "Confidence: high" \
  -m "Scope-risk: moderate" \
  -m "Directive: Identity-provider deletion may run only after the account purge job is done and biometric reconciliation reports zero references." \
  -m "Tested: pytest tests/test_biometric_purge.py tests/test_facemarket_licenses.py tests/test_personalization.py -k 'account or deletion or purge' -q"
```

### Task 8: Implement the Dry-Run, Approval, Freeze, Purge, and Apply Gate

**Files:**
- Modify: `server/app/facemarket_cutover.py`
- Modify: `server/app/routes.py:2671-2835`
- Create: `server/scripts/facemarket_security_cutover.py`
- Modify: `server/tests/test_facemarket_cutover.py`

**Interfaces:**
- Consumes: Task 3 purge engine, Task 5 cancel/drain primitives, Task 6 VC enqueue, `repo.is_admin`.
- Produces: `build_cutover_manifest(app, *, batch_id: str | None = None) -> CutoverManifest`, `create_cutover_batch(conn, manifest) -> str`, `approve_cutover_batch(conn, *, batch_id: str, admin_user_id: str) -> None`, `real_model_enqueue_closed(conn) -> bool`, `apply_cutover(app, *, batch_id: str, confirmation: str, drain_timeout_seconds: float) -> dict`.
- Produces CLI modes `--dry-run`, `--create-batch`, `--approve`, and guarded `--apply`.

- [ ] **Step 1: Write failing manifest and approval tests**

Add to `test_facemarket_cutover.py`:

```python
@pytest.mark.asyncio
async def test_manifest_exposes_only_counts_and_digest(cutover_app, caplog):
    manifest = await build_cutover_manifest(cutover_app)
    summary = manifest.public_summary()
    assert set(summary) == {
        "targetDigest", "modelCount", "licenseCount", "jobCount", "assetCount"
    }
    assert len(summary["targetDigest"]) == 64
    assert "facemarket/models/" not in str(summary)
    assert "facemarket/models/" not in caplog.text


@pytest.mark.asyncio
async def test_approval_requires_admin_and_matching_planned_batch(cutover_app, db_conn, users):
    manifest = await build_cutover_manifest(cutover_app)
    batch_id = await create_cutover_batch(db_conn, manifest)
    with pytest.raises(CutoverBlocked) as exc:
        await approve_cutover_batch(
            db_conn, batch_id=batch_id, admin_user_id=users.normal
        )
    assert exc.value.code == "admin_required"
    await approve_cutover_batch(
        db_conn, batch_id=batch_id, admin_user_id=users.admin
    )
    assert await batch_status(db_conn, batch_id) == "approved"


@pytest.mark.asyncio
async def test_target_drift_blocks_apply_before_freeze(cutover_app, approved_batch):
    await seed_additional_model(cutover_app.pool)
    with pytest.raises(CutoverBlocked) as exc:
        await apply_cutover(
            cutover_app,
            batch_id=approved_batch,
            confirmation=approved_batch,
            drain_timeout_seconds=0.1,
        )
    assert exc.value.code == "target_digest_changed"
    assert await frozen_model_count(cutover_app.pool) == 0


@pytest.mark.asyncio
async def test_freeze_is_idempotent_and_preserves_first_status(cutover_app, approved_batch):
    await apply_cutover(
        cutover_app,
        batch_id=approved_batch,
        confirmation=approved_batch,
        drain_timeout_seconds=1.0,
    )
    await apply_cutover(
        cutover_app,
        batch_id=approved_batch,
        confirmation=approved_batch,
        drain_timeout_seconds=1.0,
    )
    model = await model_state(cutover_app.pool)
    license_row = await license_state(cutover_app.pool)
    assert model["previous_status"] == "verified"
    assert model["status"] == "reverification_required"
    assert license_row["previous_status"] == "active"
    assert license_row["status"] == "reverification_required"
    assert await revoke_queue_count(cutover_app.pool) == 1
```

The apply test must use fake R2 and a local disposable database. It does not authorize running the production CLI apply mode.

- [ ] **Step 2: Run cutover state tests to verify failure**

Run:

```bash
cd server
.venv/bin/pytest tests/test_facemarket_cutover.py -k "manifest or approval or drift or freeze" -q
```

Expected: FAIL because manifest and apply orchestration are not defined.

- [ ] **Step 3: Implement an in-memory-only target manifest**

Add to `facemarket_cutover.py`:

```python
import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, repr=False)
class CutoverManifest:
    model_ids: tuple[str, ...]
    license_ids: tuple[str, ...]
    job_ids: tuple[str, ...]
    asset_keys: tuple[str, ...]

    @property
    def target_digest(self) -> str:
        values = (
            *(f"model:{value}" for value in self.model_ids),
            *(f"license:{value}" for value in self.license_ids),
            *(f"job:{value}" for value in self.job_ids),
            *(f"asset:{value}" for value in self.asset_keys),
        )
        return hashlib.sha256("\n".join(sorted(values)).encode()).hexdigest()

    def public_summary(self) -> dict:
        return {
            "targetDigest": self.target_digest,
            "modelCount": len(self.model_ids),
            "licenseCount": len(self.license_ids),
            "jobCount": len(self.job_ids),
            "assetCount": len(self.asset_keys),
        }
```

`build_cutover_manifest` queries all non-suspended models, their licenses, all pending/running initial cutover kinds, known license/asset keys, and prefix listings from both buckets when called by the CLI application object. For a resumed batch, include jobs tagged with `metadata.cutoverBatchId == batch_id` in addition to currently pending/running jobs so the refund cancellation performed by the first attempt does not create false target drift. Sort and deduplicate every tuple. Do not print or log the object itself because `repr=False` is a secondary guard, not the primary secrecy mechanism.

- [ ] **Step 4: Implement batch creation, approval, and enqueue close**

`create_cutover_batch` inserts only counts and digest with status `planned`. `approve_cutover_batch` calls `repo.is_admin`, locks the planned batch, and sets `approved_by`, `approved_at`, and `status='approved'`. `real_model_enqueue_closed` returns true when any batch is in `draining`, `applying`, or `reconciling`.

Call `real_model_enqueue_closed` in both real-model enqueue routes before credit reservation. Return HTTP 503 with code `facemarket_cutover_in_progress`; virtual model and editor `mode='vary'` requests remain unaffected.

- [ ] **Step 5: Implement apply in the safe sequence**

`apply_cutover` must use these ordered state transitions and conditional updates:

```python
if confirmation != batch_id:
    raise CutoverBlocked("confirmation_mismatch")

batch = await _lock_batch(pool, batch_id)
if batch["status"] == "completed":
    return _batch_public_summary(batch)
if batch["status"] not in {"approved", "draining", "applying", "reconciling"}:
    raise CutoverBlocked("batch_not_approved")

current = await build_cutover_manifest(app, batch_id=batch_id)
if current.target_digest != batch["target_digest"]:
    raise CutoverBlocked("target_digest_changed")

await _set_batch_status(pool, batch_id, "draining")
await cancel_initial_cutover_jobs(pool, batch_id=batch_id)
await drain_initial_cutover_jobs(
    pool, timeout_seconds=drain_timeout_seconds
)
await _set_batch_status(pool, batch_id, "applying")
await _freeze_licenses_then_models(pool, batch_id=batch_id)
await _enqueue_batch_vc_revocations(pool, batch_id=batch_id)
await _set_batch_status(pool, batch_id, "reconciling")
await _purge_batch_targets(app, batch_id=batch_id)
await _complete_batch(pool, batch_id=batch_id)
```

`_freeze_licenses_then_models` must run in one transaction using:

```sql
update fm_licenses
   set previous_status=coalesce(previous_status,status),
       status='reverification_required', reverification_batch_id=%s
 where status not in ('revoked','expired')
   and (reverification_batch_id is null or reverification_batch_id=%s);

update fm_models
   set previous_status=coalesce(previous_status,status),
       status='reverification_required', reverification_batch_id=%s
 where status <> 'suspended'
   and (reverification_batch_id is null or reverification_batch_id=%s);
```

On a drain failure, set batch `failed` with `last_error_code='running_jobs_not_drained'`; model/license and R2 must remain unchanged. On an R2 reconcile failure, keep frozen local states, set batch `failed` with the bounded error code, retain DB key rows, and allow an admin to approve a new batch or an explicit resume operation to continue from reconciliation.

- [ ] **Step 6: Implement the guarded CLI**

Create `server/scripts/facemarket_security_cutover.py` using `argparse` with a mutually exclusive mode group:

```python
group = parser.add_mutually_exclusive_group()
group.add_argument("--dry-run", action="store_true")
group.add_argument("--create-batch", action="store_true")
group.add_argument("--approve", metavar="BATCH_ID")
group.add_argument("--apply", metavar="BATCH_ID")
parser.add_argument("--admin-user-id")
parser.add_argument("--confirm")
parser.add_argument("--drain-timeout-seconds", type=float, default=900.0)
```

No mode must behave exactly like `--dry-run` and perform SELECT/list operations only. `--create-batch` is the only dry phase that inserts a planned metadata row. `--approve` requires `--admin-user-id`. `--apply` requires `--confirm` equal to the same batch UUID and calls `apply_cutover`.

Print only JSON produced by `CutoverManifest.public_summary()` or batch public summary. Never print exception arguments from storage/Holder clients; map them to bounded codes.

- [ ] **Step 7: Test CLI defaults and guard without destructive execution**

Add tests that monkeypatch the four service entry points:

```python
def test_cli_default_is_read_only_dry_run(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cutover_script, "run_dry_run", lambda: calls.append("dry"))
    monkeypatch.setattr(cutover_script, "run_apply", lambda **kwargs: calls.append("apply"))
    cutover_script.main([])
    assert calls == ["dry"]
    assert "APPLY" not in capsys.readouterr().out


def test_cli_apply_requires_exact_confirmation(monkeypatch):
    with pytest.raises(SystemExit):
        cutover_script.main(["--apply", BATCH_ID, "--confirm", "different"])
```

Do not invoke the real apply branch in shell verification.

- [ ] **Step 8: Run all cutover tests and read-only CLI help**

Run:

```bash
cd server
.venv/bin/pytest tests/test_facemarket_cutover.py -q
.venv/bin/python -m scripts.facemarket_security_cutover --help
```

Expected: tests PASS; help displays the guarded modes. No destructive command is run.

- [ ] **Step 9: Commit the cutover control surface**

```bash
git add server/app/facemarket_cutover.py server/app/routes.py server/scripts/facemarket_security_cutover.py server/tests/test_facemarket_cutover.py
git commit -m "Gate biometric cutover behind stable targets and admin approval" \
  -m "Constraint: Destructive execution requires a reviewed batch, exact confirmation, closed enqueue, and drained workers." \
  -m "Rejected: A single --apply flag | it cannot prove target stability or explicit administrative approval." \
  -m "Confidence: high" \
  -m "Scope-risk: broad" \
  -m "Directive: Do not execute facemarket_security_cutover --apply during code and dry-run validation." \
  -m "Tested: pytest tests/test_facemarket_cutover.py -q; python -m scripts.facemarket_security_cutover --help"
```

### Task 9: Verify End-to-End Idempotency and Produce Dry-Run Evidence

**Files:**
- Modify: `server/tests/test_facemarket_cutover.py`
- Modify: `server/tests/test_biometric_purge.py`

**Interfaces:**
- Consumes: all preceding public interfaces.
- Produces: regression evidence for repeated purge, partial resume, revoke/purge race, R2/DB reconciliation, account pre-delete, global cancellation/drain, and virtual-model non-regression.

- [ ] **Step 1: Add the integrated failure-resume test**

Create a test that runs the approved cutover against fake R2 with one injected failure, then resumes with the failure removed:

```python
@pytest.mark.asyncio
async def test_cutover_resumes_after_partial_r2_failure_without_losing_references(
    cutover_app, approved_batch
):
    cutover_app.state.r2_face.fail_list.add(
        f"facemarket/models/{cutover_app.model_id}/"
    )
    with pytest.raises(PurgeIncomplete):
        await apply_cutover(
            cutover_app,
            batch_id=approved_batch,
            confirmation=approved_batch,
            drain_timeout_seconds=1.0,
        )
    assert await batch_status(cutover_app.pool, approved_batch) == "failed"
    assert await face_reference_count(cutover_app.pool) > 0
    assert await local_real_model_usable(cutover_app.pool) is False

    cutover_app.state.r2_face.fail_list.clear()
    await approve_batch_resume(cutover_app.pool, approved_batch)
    result = await apply_cutover(
        cutover_app,
        batch_id=approved_batch,
        confirmation=approved_batch,
        drain_timeout_seconds=1.0,
    )
    assert result["status"] == "completed"
    assert await face_reference_count(cutover_app.pool) == 0
    assert cutover_app.state.r2_face.keys == set()
```

`approve_batch_resume` is an admin-only state transition from `failed` to `approved` that preserves the same target digest. It must reject resume when current target digest differs.

- [ ] **Step 2: Add the no-output/no-settlement revoke race test**

```python
@pytest.mark.asyncio
async def test_revoke_after_enqueue_refunds_without_output_or_settlement(
    real_model_job_app, queued_detail_job
):
    await revoke_local_license(
        real_model_job_app.pool,
        queued_detail_job["payload"]["facemarketLicenseId"],
    )
    await run_detail_page_job(real_model_job_app, queued_detail_job)
    assert await job_status(real_model_job_app.pool, queued_detail_job["id"]) == "error"
    assert await reserved_amount(real_model_job_app.pool, queued_detail_job["user_id"]) == 0
    assert await settlement_count(real_model_job_app.pool, queued_detail_job["id"]) == 0
    assert real_model_job_app.state.r2.objects == {}
```

- [ ] **Step 3: Run targeted security suites**

Run:

```bash
cd server
.venv/bin/pytest \
  tests/test_facemarket_cutover_migration.py \
  tests/test_r2.py \
  tests/test_biometric_purge.py \
  tests/test_facemarket_cutover.py \
  tests/test_facemarket_vc_revocation.py \
  tests/test_facemarket_licenses.py \
  tests/test_detail_page_license_face.py \
  tests/test_editor_image.py \
  tests/test_fm_model_asset_job.py \
  tests/test_personalization.py -q
```

Expected: PASS, except the documented environment-gated migration integration test may SKIP when its dedicated database URL is absent.

- [ ] **Step 4: Run broader backend regression and syntax checks**

Run:

```bash
cd server
.venv/bin/pytest tests/test_detail_page.py tests/test_editor_image.py tests/test_personalization.py tests/test_facemarket_seller_loop.py -q
.venv/bin/python -m compileall -q app scripts
cd ..
git diff --check
```

Expected: all tests PASS, compileall exits 0, and `git diff --check` produces no output.

- [ ] **Step 5: Run the safe dry-run only**

Against a local or staging database/R2 configuration, run:

```bash
cd server
.venv/bin/python -m scripts.facemarket_security_cutover --dry-run
```

Expected: one JSON object containing only `targetDigest`, `modelCount`, `licenseCount`, `jobCount`, and `assetCount`. Do not run `--create-batch`, `--approve`, or `--apply` as part of this plan's validation.

- [ ] **Step 6: Inspect logs and output for biometric identifiers**

Run:

```bash
cd server
.venv/bin/pytest tests/test_biometric_purge.py tests/test_facemarket_vc_revocation.py tests/test_facemarket_cutover.py -q --log-cli-level=INFO 2>&1 | tee /tmp/facemarket-purge-test.log
! rg -n "facemarket/models/|personalization/profiles/.*/faces/|vc:face:|ci_hash|face_image_digest" /tmp/facemarket-purge-test.log
```

Expected: pytest PASS and the negated `rg` exits successfully because no raw biometric identifier appears.

- [ ] **Step 7: Commit integrated verification**

```bash
git add server/tests/test_facemarket_cutover.py server/tests/test_biometric_purge.py
git commit -m "Prove biometric cutover resumes without leakage or double refund" \
  -m "Constraint: Completion claims require fresh failure-resume, race, reconciliation, and log-redaction evidence." \
  -m "Rejected: Happy-path-only dry-run evidence | it cannot prove retained references survive partial R2 failure." \
  -m "Confidence: high" \
  -m "Scope-risk: moderate" \
  -m "Not-tested: Production destructive apply is intentionally prohibited during this implementation phase." \
  -m "Tested: targeted FaceMarket/personalization suites; compileall; git diff --check; read-only cutover dry-run"
```

## Stop Condition

Implementation is complete only when all targeted tests and broader regressions pass, strict R2 failures preserve DB discovery references, repeated purge/cutover/revoke operations remain idempotent, account pre-delete leaves no biometric API returning 200, log redaction checks pass, and a read-only dry-run emits only counts plus digest. Stop before any real `--apply`, production batch approval, R2 deletion, or status freeze.
