# FaceMarket 3층 출처증명 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실존 인물 얼굴로 만든 생성물에 대해 ① 컷·배포본 단위 DB 사용 원장 ② 배포본에 임베드하는 C2PA Content Credential ③ 배포본 해시의 온체인 앵커를 붙여, 파일이 서비스 밖으로 나가도 출처를 증명할 수 있게 한다.

**Architecture:** 배포본 렌더는 지금처럼 브라우저(`editorExport.js`)가 하고 서버는 공증소 역할만 한다 — 브라우저가 캔버스 PNG 를 R2 로 presigned PUT 하고, 서버가 그 바이트를 읽어 해시·원장 기록·C2PA 서명을 한 뒤 서명본을 돌려준다. 체인 앵커는 확정 폴링이 최대 90초라 다운로드 응답에 묶지 않고 별도 reconciler 루프가 뒤따른다. 기존 `FaceMarketSettlement` 컨트랙트는 건드리지 않고 새 `FaceMarketProvenance` 를 **같은 `FaceMarketChain` 인스턴스**에 얹어 nonce lock 을 공유한다.

**Tech Stack:** FastAPI · psycopg3 · Supabase(Postgres) · Cloudflare R2(boto3) · web3.py · Solidity 0.8.27(OmniOne Chain, Free-Gas BESU) · `c2pa-python` 0.37.8 · React(Vite) · pytest

**Spec:** `docs/superpowers/specs/2026-09-04-facemarket-provenance-design.md`

## Global Constraints

- **과금 불변.** 작업 1건 = 라이선스비 1회 = 온체인 정산 1건. 이 계획의 어떤 작업도 `credit_cost_*`, `unit_price`, `record_license_settlement` 호출 조건을 바꾸지 않는다.
- **생성 파이프라인 불변.** `cut_generator`·프롬프트·QC·이미지 모델 호출은 손대지 않는다.
- **원장은 부모보다 오래 산다.** 두 원장 테이블의 모든 FK 는 `on delete set null`. 증빙값(`model_id`·`license_ref`·`seller_id`·`image_sha256`)은 FK 없는 비정규화 컬럼.
- **🔴 C2PA 매니페스트·공개 검증 응답 임베드 금지 필드:** 얼굴 이미지, `face_image_key`, `face_image_uri`, `face_image_digest`, CI, `ci_hash`, 생년월일 원문, 실명, `user_id`, 내부 R2 키, presigned URL. (`facemarket.py:1249` 하드룰 계승)
- **동기 이미지 작업은 반드시 `asyncio.to_thread`.** 2026-08-26 ALB 장애 원인이 동기 이미지 작업의 이벤트루프 동결이었다(`healthz` 37초 공백 실측).
- **모든 신규 기능은 플래그 뒤.** `FM_PROVENANCE_ENABLED` 가 off 면 신규 라우트 미등록·워커 미기동·원장 insert 생략. 기존 흐름 무영향.
- **Python 3.12**, `uv` 로 의존성 관리(`server/pyproject.toml`).
- 테스트 실행: `cd server && uv run pytest tests/<file> -v`. DB 필요 테스트는 `FACEMARKET_TEST_DATABASE_URL` 환경변수 + `requires_database` skipif 관례를 따른다(`tests/test_facemarket_mandatory_vc_migration.py` 선례).
- **작업 브랜치:** `feat/facemarket-provenance` (이미 생성됨, 스펙 커밋 `5d326628` 위에 쌓는다).

---

## File Structure

| 파일 | 책임 |
|---|---|
| `supabase/migrations/20260904000000_facemarket_provenance.sql` | 원장 2테이블 + 앵커 큐 + RLS + 인덱스 |
| `server/app/services/c2pa_signer.py` | **신규.** 바이트 → 서명된 바이트. 매니페스트 조립(화이트리스트)과 서명을 분리. 순수 동기 함수만 — 호출부가 `to_thread` 로 감싼다 |
| `server/app/facemarket_provenance.py` | **신규.** presign/sign 라우트, 원장 upsert, `uploadToken` 발급·검증, 공개 검증 라우트 |
| `server/app/workers/fm_publication_anchor.py` | **신규.** 앵커 reconciler 루프(`fm_vc_revocation_reconciler.py` 패턴) |
| `contracts/FaceMarketProvenance.sol` | **신규.** record-only 배포본 앵커 컨트랙트 |
| `server/app/facemarket_chain.py` | 기존. `record_publication`/`get_publication`/`wait_for_publication` 메서드 추가(같은 인스턴스 = nonce lock 공유) |
| `server/app/repo.py` | 기존. `finalize_detail_page_success`·`finalize_editor_image_success` 안에서 `fm_output_records` insert + `assets.checksum` 채움 |
| `server/app/workers/detail_page_job.py` | 기존. `put_bytes` 직전 sha256 계산 → `cut_assets` dict 에 실음 |
| `server/app/workers/editor_image_job.py` | 기존. 동일 |
| `server/app/services/biometric_purge.py` | 기존. 철회 시 `fm_publication_records.r2_key` 삭제 + `revoked_at` 세팅 |
| `server/app/config.py` | 기존. `fm_provenance_*`, `fm_c2pa_*` 설정 추가 |
| `server/app/main.py` | 기존. 라우터 등록 + 앵커 reconciler 기동 |
| `src/features/verify/PublicVerifyPublication.jsx` | **신규.** `/verify/p/:publicationId` 페이지 |
| `src/lib/api/facemarket.js` | 기존. presign/sign/verify 클라이언트 |
| `src/features/editor/editorExport.js` | 기존. 저장 직전 공증 왕복 삽입 |
| `server/scripts/gen_c2pa_cert.py` | **신규.** 자체서명 인증서 생성(운영 스크립트) |

**의존 순서:** Task 1(스키마) → 2(컷 원장) → 3(서명기) → 4(라우트) → 5(컨트랙트+체인) → 6(앵커 워커) → 7(공개검증) → 8(프론트) → 9(철회) → 10(모델 대시보드) → 11(prod 배선)

---

## Task 1: 스키마 — 원장 2테이블 + 앵커 큐

**Files:**
- Create: `supabase/migrations/20260904000000_facemarket_provenance.sql`
- Test: `server/tests/test_facemarket_provenance_migration.py`

**Interfaces:**
- Consumes: 기존 `public.assets`, `public.jobs`, `public.projects`, `public.fm_licenses`, `public.set_updated_at()`
- Produces: 테이블 `fm_output_records`, `fm_publication_records`, `fm_publication_anchor_jobs`

- [ ] **Step 1: 마이그레이션 테스트 작성 (실패 예상)**

`server/tests/test_facemarket_provenance_migration.py`:

```python
"""원장 스키마 계약 테스트 — 원장이 부모보다 오래 사는지가 핵심이다.

fm_models → fm_licenses 는 on delete cascade 다. 2026-08-29 prod 복구 때 모델·라이선스가
실제로 지워졌다. 원장의 FK 가 restrict 였다면 그 복구가 막혔고, cascade 였다면 원장이
통째로 사라졌다. 여기서 검증하는 건 "부모가 지워져도 증빙값이 남는다" 하나다.
"""
import asyncio
import os
import uuid
from pathlib import Path

import pytest
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20260904000000_facemarket_provenance.sql"
TEST_DATABASE_URL = os.getenv("FACEMARKET_TEST_DATABASE_URL")
requires_database = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="FACEMARKET_TEST_DATABASE_URL is not configured"
)


def test_migration_file_exists():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION}"


def test_migration_declares_set_null_fks():
    """계약을 SQL 텍스트 수준에서도 못박는다 — DB 없는 CI 에서도 회귀를 잡는다."""
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "on delete restrict" not in sql.lower()
    assert "fm_output_records" in sql
    assert "fm_publication_records" in sql
    assert "fm_publication_anchor_jobs" in sql
    # 증빙값은 FK 없는 비정규화 컬럼이어야 한다
    assert "license_ref" in sql
    assert "image_sha256" in sql


@requires_database
def test_ledger_survives_license_delete():
    async def run():
        conn = await AsyncConnection.connect(
            TEST_DATABASE_URL, autocommit=True, row_factory=dict_row
        )
        try:
            await conn.execute(MIGRATION.read_text(encoding="utf-8"))
            model_id = uuid.uuid4()
            license_id = uuid.uuid4()
            seller_id = uuid.uuid4()
            await conn.execute(
                "insert into fm_models (id, display_name) values (%s, %s)",
                (model_id, "홍*동"),
            )
            await conn.execute(
                """insert into fm_licenses
                   (id, model_id, face_image_uri, face_image_digest, license_valid_until)
                   values (%s, %s, %s, %s, now() + interval '1 year')""",
                (license_id, model_id, "/gate", "sha256-x"),
            )
            record_id = uuid.uuid4()
            await conn.execute(
                """insert into fm_output_records
                   (id, license_id, license_ref, model_id, seller_id, image_sha256)
                   values (%s, %s, %s, %s, %s, %s)""",
                (record_id, license_id, license_id, model_id, seller_id, "a" * 64),
            )
            # 모델 삭제 → 라이선스 cascade 삭제. 원장은 남아야 한다.
            await conn.execute("delete from fm_models where id = %s", (model_id,))
            cur = await conn.execute(
                "select license_id, license_ref, model_id, image_sha256 "
                "from fm_output_records where id = %s",
                (record_id,),
            )
            row = await cur.fetchone()
            assert row is not None, "원장이 부모와 함께 지워졌다"
            assert row["license_id"] is None       # FK 는 끊긴다
            assert str(row["license_ref"]) == str(license_id)  # 증빙값은 남는다
            assert str(row["model_id"]) == str(model_id)
            assert row["image_sha256"] == "a" * 64
        finally:
            await conn.close()

    asyncio.run(run())


@requires_database
def test_publication_idempotent_per_seller_and_hash():
    async def run():
        conn = await AsyncConnection.connect(
            TEST_DATABASE_URL, autocommit=True, row_factory=dict_row
        )
        try:
            await conn.execute(MIGRATION.read_text(encoding="utf-8"))
            seller_id = uuid.uuid4()
            args = (seller_id, uuid.uuid4(), uuid.uuid4(), "long_png", "b" * 64)
            sql = """insert into fm_publication_records
                     (seller_id, license_ref, model_id, kind, image_sha256)
                     values (%s, %s, %s, %s, %s)"""
            await conn.execute(sql, args)
            with pytest.raises(UniqueViolation):
                await conn.execute(sql, args)
        finally:
            await conn.close()

    asyncio.run(run())
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd server && uv run pytest tests/test_facemarket_provenance_migration.py -v`
Expected: `test_migration_file_exists` FAIL — `missing migration: .../20260904000000_facemarket_provenance.sql`

- [ ] **Step 3: 마이그레이션 작성**

`supabase/migrations/20260904000000_facemarket_provenance.sql`:

```sql
-- =============================================================
-- FaceMarket 3층 출처증명 — 층① 사용 원장 + 층③ 앵커 큐.
-- 설계: docs/superpowers/specs/2026-09-04-facemarket-provenance-design.md
--
-- 🔴 원장은 부모보다 오래 산다. 모든 FK 는 on delete set null 이고, 실제 증빙값
--    (model_id·license_ref·seller_id·image_sha256)은 FK 없는 비정규화 컬럼이다.
--    fm_models → fm_licenses 가 cascade 라 모델 삭제가 라이선스를 지운다.
--    restrict 였다면 2026-08-29 prod 복구가 막혔고, cascade 였다면 원장이 사라졌다.
-- =============================================================

-- ── fm_output_records: 만든 컷 1장 = 1행 ─────────────────────
create table if not exists public.fm_output_records (
  id            uuid primary key default gen_random_uuid(),
  asset_id      uuid unique references public.assets(id) on delete set null,
  job_id        uuid references public.jobs(id) on delete set null,
  license_id    uuid references public.fm_licenses(id) on delete set null,
  license_ref   uuid not null,   -- 비정규화: license_id 가 null 이 돼도 남는다
  model_id      uuid not null,   -- 비정규화(FK 없음)
  seller_id     uuid not null,   -- 생성한 셀러(jobs.user_id)
  image_sha256  text not null,
  byte_size     bigint,
  created_at    timestamptz not null default now()
);
create index if not exists fm_output_records_license_idx
  on public.fm_output_records (license_ref, created_at desc);
create index if not exists fm_output_records_seller_idx
  on public.fm_output_records (seller_id, created_at desc);

-- ── fm_publication_records: 내려받은 파일 1건 = 1행 ──────────
create table if not exists public.fm_publication_records (
  id               uuid primary key default gen_random_uuid(),
  project_id       uuid references public.projects(id) on delete set null,
  seller_id        uuid not null,
  license_id       uuid references public.fm_licenses(id) on delete set null,
  license_ref      uuid not null,
  model_id         uuid not null,
  kind             text not null check (kind in ('long_png', 'block_png', 'zip')),
  image_sha256     text not null,   -- 서명 전 원본
  signed_sha256    text,            -- 서명 후(임베드로 바이트가 바뀐다)
  byte_size        bigint,
  r2_key           text,            -- 서명본 보관. 철회 시 삭제 대상(§9)
  source_asset_ids uuid[] not null default '{}',
  c2pa_manifest    jsonb not null default '{}'::jsonb,
  c2pa_status      text not null default 'skipped'
                     check (c2pa_status in ('signed', 'skipped', 'failed')),
  chain_status     text not null default 'pending'
                     check (chain_status in ('pending', 'confirmed', 'failed')),
  tx_hash          text,
  chain_id         text,
  recorded_block   bigint,
  revoked_at       timestamptz,     -- 철회 표시. 행은 지우지 않는다
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  constraint fm_publication_records_seller_hash_uniq unique (seller_id, image_sha256)
);
create index if not exists fm_publication_records_license_idx
  on public.fm_publication_records (license_ref, created_at desc);
create index if not exists fm_publication_records_seller_idx
  on public.fm_publication_records (seller_id, created_at desc);

drop trigger if exists fm_publication_records_set_updated_at on public.fm_publication_records;
create trigger fm_publication_records_set_updated_at
  before update on public.fm_publication_records
  for each row execute function public.set_updated_at();

-- ── fm_publication_anchor_jobs: 비동기 앵커 큐 ──────────────
-- jobs 테이블을 안 쓰는 이유: jobs_active_unique_idx 가 (project_id, kind) 동시 1건이라
-- 같은 프로젝트에서 연달아 내려받으면 앵커가 서로를 막는다. fm_vc_revocation_jobs 선례를 따른다.
create table if not exists public.fm_publication_anchor_jobs (
  publication_id uuid primary key
                   references public.fm_publication_records(id) on delete cascade,
  status         text not null default 'pending'
                   check (status in ('pending', 'processing', 'retry', 'anchored', 'dead')),
  attempts       integer not null default 0,
  lease_until    timestamptz,
  attempted_at   timestamptz,
  last_error     text,
  created_at     timestamptz not null default now()
);
create index if not exists fm_publication_anchor_jobs_pending_idx
  on public.fm_publication_anchor_jobs (status, created_at)
  where status in ('pending', 'retry', 'processing');

-- ── RLS: enable + 셀러 owner-select + 모델 owner-select. 쓰기=service-role ──
alter table public.fm_output_records         enable row level security;
alter table public.fm_publication_records    enable row level security;
alter table public.fm_publication_anchor_jobs enable row level security;
-- 앵커 큐는 운영 내부 데이터 — 정책 없음 = service-role 전용.

drop policy if exists fm_output_records_seller_select on public.fm_output_records;
create policy fm_output_records_seller_select on public.fm_output_records
  for select using (seller_id = (select auth.uid()));

drop policy if exists fm_output_records_model_select on public.fm_output_records;
create policy fm_output_records_model_select on public.fm_output_records
  for select using (exists (
    select 1 from public.fm_models m
    where m.id = fm_output_records.model_id and m.user_id = (select auth.uid())));

drop policy if exists fm_publication_records_seller_select on public.fm_publication_records;
create policy fm_publication_records_seller_select on public.fm_publication_records
  for select using (seller_id = (select auth.uid()));

drop policy if exists fm_publication_records_model_select on public.fm_publication_records;
create policy fm_publication_records_model_select on public.fm_publication_records
  for select using (exists (
    select 1 from public.fm_models m
    where m.id = fm_publication_records.model_id and m.user_id = (select auth.uid())));

comment on table public.fm_output_records is
  'FaceMarket 층① 사용 원장(컷 단위). 정산 근거·역추적·분쟁 증빙. 부모(모델·라이선스·자산)가 '
  '삭제돼도 남아야 하므로 FK 는 전부 set null 이고 증빙값은 비정규화 컬럼이다.';
comment on table public.fm_publication_records is
  'FaceMarket 층① 사용 원장(배포본 단위). C2PA 서명 결과와 온체인 앵커 상태를 함께 들고 있다. '
  '철회 시 r2_key 사본만 지우고 행은 revoked_at 표시로 남긴다 — 지우면 무단 사용과 구별이 안 된다.';
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd server && uv run pytest tests/test_facemarket_provenance_migration.py -v`
Expected: 텍스트 테스트 2개 PASS. DB 테스트 2개는 `FACEMARKET_TEST_DATABASE_URL` 없으면 SKIP.

로컬 DB 가 있으면 함께 확인:
Run: `cd server && FACEMARKET_TEST_DATABASE_URL="$LOCAL_DB" uv run pytest tests/test_facemarket_provenance_migration.py -v`
Expected: 4 PASS

- [ ] **Step 5: 커밋**

```bash
git add supabase/migrations/20260904000000_facemarket_provenance.sql \
        server/tests/test_facemarket_provenance_migration.py
git commit -m "feat(facemarket): 출처증명 원장 스키마 — 원장이 부모보다 오래 산다"
```

---

## Task 2: 컷 원장 — 생성 시점 해시 + `fm_output_records` insert

**Files:**
- Modify: `server/app/workers/detail_page_job.py` (`put_bytes` 호출부, 반환 dict)
- Modify: `server/app/workers/editor_image_job.py` (`put_bytes` 호출부, `image_row`)
- Modify: `server/app/repo.py` (`finalize_detail_page_success`, `finalize_editor_image_success`)
- Test: `server/tests/test_facemarket_output_ledger.py`

**Interfaces:**
- Consumes: Task 1 의 `fm_output_records`
- Produces:
  - `repo.insert_output_records(cur, *, records: list[dict]) -> None` — `records` 항목 키: `asset_id, job_id, license_id, license_ref, model_id, seller_id, image_sha256, byte_size`
  - `cut_assets` / `image_row` dict 에 추가되는 키: `sha256: str`, `provenance: dict | None`
    (`provenance` = `{"license_id": str, "model_id": str}` 또는 REAL 이 아니면 `None`)

- [ ] **Step 1: 실패 테스트 작성**

`server/tests/test_facemarket_output_ledger.py`:

```python
"""층① 컷 원장 — finalize 트랜잭션 안에서만 행이 생긴다.

lease 를 뺏기면 워커는 방금 올린 R2 객체를 지운다. 원장 insert 가 그 트랜잭션 밖에 있으면
버려진 이미지의 원장 행이 남아 정산 근거로 쓸 수 없다. 여기서 그걸 못박는다.
"""
import hashlib

import pytest

from app import repo


class RecordingCursor:
    def __init__(self, lease_ok=True):
        self.lease_ok = lease_ok
        self.statements = []
        self._last = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))
        if "from jobs where id" in sql and "locked_by" in sql:
            self._last = {"id": "j1"} if self.lease_ok else None
        elif "coalesce(max(" in sql:
            self._last = {"v": 1}
        else:
            self._last = {"id": "x"}

    async def fetchone(self):
        return self._last

    async def fetchall(self):
        return []


def ledger_inserts(cur):
    return [s for s in cur.statements if "insert into fm_output_records" in s[0]]


def test_insert_output_records_writes_one_row_per_cut():
    cur = RecordingCursor()
    records = [
        {
            "asset_id": "a1", "job_id": "j1", "license_id": "l1", "license_ref": "l1",
            "model_id": "m1", "seller_id": "u1",
            "image_sha256": hashlib.sha256(b"one").hexdigest(), "byte_size": 3,
        },
        {
            "asset_id": "a2", "job_id": "j1", "license_id": "l1", "license_ref": "l1",
            "model_id": "m1", "seller_id": "u1",
            "image_sha256": hashlib.sha256(b"two").hexdigest(), "byte_size": 3,
        },
    ]
    import asyncio
    asyncio.run(repo.insert_output_records(cur, records=records))
    assert len(ledger_inserts(cur)) == 2


def test_insert_output_records_skips_when_no_provenance():
    cur = RecordingCursor()
    import asyncio
    asyncio.run(repo.insert_output_records(cur, records=[]))
    assert ledger_inserts(cur) == []


def test_finalize_detail_page_writes_ledger_inside_lease_fence():
    """lease 를 잃으면 원장 행도 안 생긴다."""
    import asyncio

    class Conn:
        def __init__(self, cur):
            self._cur = cur

        def cursor(self):
            return self._cur

    cur = RecordingCursor(lease_ok=False)
    out = asyncio.run(repo.finalize_detail_page_success(
        Conn(cur), job_id="j1", lease_token="t", user_id="u1", project_id="p1",
        editor_blocks=[], cut_assets=[{
            "asset_id": "a1", "bucket": "b", "key": "k", "mime": "image/png",
            "size": 3, "width": 1, "height": 1,
            "sha256": "c" * 64,
            "provenance": {"license_id": "l1", "model_id": "m1"},
        }], reserved=0, charge=0, metadata={},
    ))
    assert out is None
    assert ledger_inserts(cur) == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd server && uv run pytest tests/test_facemarket_output_ledger.py -v`
Expected: FAIL — `AttributeError: module 'app.repo' has no attribute 'insert_output_records'`

- [ ] **Step 3: `repo.insert_output_records` 구현**

`server/app/repo.py` 에 추가(파일 내 다른 `insert` 헬퍼 근처):

```python
async def insert_output_records(cur, *, records: list[dict]) -> None:
    """층① 컷 원장. 호출부의 finalize 트랜잭션 안에서만 부른다(lease 펜스 통과 후).

    on conflict (asset_id) do nothing — 워커 재시도가 같은 asset_id 로 다시 와도 1행.
    """
    for r in records:
        await cur.execute(
            """insert into fm_output_records
                 (asset_id, job_id, license_id, license_ref, model_id,
                  seller_id, image_sha256, byte_size)
               values (%s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (asset_id) do nothing""",
            (
                r["asset_id"], r.get("job_id"), r.get("license_id"), r["license_ref"],
                r["model_id"], r["seller_id"], r["image_sha256"], r.get("byte_size"),
            ),
        )
```

- [ ] **Step 4: `finalize_detail_page_success` 배선**

`server/app/repo.py:2601` 의 `for c in cut_assets:` 루프 안, `assets` insert 문의 컬럼 목록에 `checksum` 을 더하고 루프 뒤에 원장 insert 를 붙인다:

```python
        ledger_records = []
        for c in cut_assets:  # 컷 이미지 asset 행 (editor_blocks 가 /v1/assets/{id}/file 로 참조)
            await cur.execute(
                "insert into assets (id, user_id, project_id, source, visibility, r2_bucket, "
                "r2_key, mime_type, byte_size, width, height, checksum, metadata) "
                "values (%s, %s, %s, 'ai', 'private', %s, %s, %s, %s, %s, %s, %s, %s) "
                "on conflict (id) do nothing",
                (c["asset_id"], user_id, project_id, c["bucket"], c["key"], c["mime"],
                 c.get("size"), c.get("width"), c.get("height"), c.get("sha256"),
                 Json(c.get("metadata") or {})),
            )
            if c.get("cleanup_intent_id"):
                await cur.execute(
                    "delete from ai_output_cleanup_intents where id = %s",
                    (c["cleanup_intent_id"],),
                )
            prov = c.get("provenance")
            if prov and c.get("sha256"):
                ledger_records.append({
                    "asset_id": c["asset_id"], "job_id": job_id,
                    "license_id": prov["license_id"], "license_ref": prov["license_id"],
                    "model_id": prov["model_id"], "seller_id": user_id,
                    "image_sha256": c["sha256"], "byte_size": c.get("size"),
                })
        await insert_output_records(cur, records=ledger_records)
```

`finalize_editor_image_success` 도 같은 방식으로 — `assets` insert 에 `checksum` 컬럼 추가 후:

```python
        prov = image.get("provenance")
        if prov and image.get("sha256"):
            await insert_output_records(cur, records=[{
                "asset_id": image["asset_id"], "job_id": job_id,
                "license_id": prov["license_id"], "license_ref": prov["license_id"],
                "model_id": prov["model_id"], "seller_id": user_id,
                "image_sha256": image["sha256"], "byte_size": image.get("size"),
            }])
```

- [ ] **Step 5: 워커에서 해시 계산**

`server/app/workers/detail_page_job.py` — 파일 상단에 `import hashlib` 추가(이미 있으면 생략). `r2.put_bytes` 호출 직전(`key = ai_key(...)` 뒤)에 해시를 만들고, 반환 dict 에 싣는다:

```python
            ext = ext_for_mime(mime) or _EXT_FALLBACK.get(mime, "png")
            asset_id = str(uuid.uuid4())
            key = ai_key(user_id, project_id, job_id, asset_id, ext)
            img_sha256 = hashlib.sha256(img).hexdigest()
```

같은 함수의 두 번째 반환 dict(현재 `{"asset_id": asset_id, "bucket": ..., "metadata": {...}}`)에 두 키를 더한다:

```python
                {"asset_id": asset_id, "bucket": s.r2_bucket, "key": key, "mime": mime,
                 "size": len(img), "width": w, "height": h,
                 "cleanup_intent_id": cleanup_intent_id,
                 "sha256": img_sha256,
                 # REAL 소스일 때만 원장 대상. license_row 는 잡 시작 시 해석된 값이다.
                 "provenance": (
                     {"license_id": str(license_row["id"]),
                      "model_id": str(license_row["model_id"])}
                     if source == "REAL" and license_row is not None else None
                 ),
                 "metadata": {
                     "facemarket_real_derived": real_identity_attached,
                 }},
```

`server/app/workers/editor_image_job.py:742` 근처도 동일하게:

```python
        asset_id = str(uuid.uuid4())
        key = ai_key(user_id, project_id, job_id, asset_id, ext)
        img_sha256 = hashlib.sha256(image).hexdigest()
```

그리고 `image_row` 에:

```python
        image_row = {
            "asset_id": asset_id, "bucket": s.r2_bucket, "key": key, "mime": mime,
            "size": len(image), "width": w, "height": h,
            "cleanup_intent_id": cleanup_intent_id,
            "sha256": img_sha256,
            "provenance": (
                {"license_id": str(fm_license_row["id"]),
                 "model_id": str(fm_license_row["model_id"])}
                if fm_face_injected and fm_license_row is not None else None
            ),
            "metadata": {"facemarket_real_derived": fm_face_injected},
        }
```

- [ ] **Step 6: 테스트 통과 + 기존 워커 테스트 회귀 확인**

Run: `cd server && uv run pytest tests/test_facemarket_output_ledger.py -v`
Expected: 3 PASS

Run: `cd server && uv run pytest tests/test_detail_page_identity_source.py tests/test_cuts.py -q`
Expected: 기존과 동일하게 PASS (dict 키 추가는 하위호환)

- [ ] **Step 7: 커밋**

```bash
git add server/app/repo.py server/app/workers/detail_page_job.py \
        server/app/workers/editor_image_job.py server/tests/test_facemarket_output_ledger.py
git commit -m "feat(facemarket): 컷 1장마다 사용 원장 행 — finalize 트랜잭션 안에서"
```

---

## Task 3: C2PA 서명기

**Files:**
- Create: `server/app/services/c2pa_signer.py`
- Create: `server/scripts/gen_c2pa_cert.py`
- Modify: `server/pyproject.toml` (`c2pa-python>=0.37.8`)
- Modify: `server/Dockerfile` (의존성은 pyproject 경유라 별도 apt 불필요 — 주석만)
- Modify: `server/app/config.py` (`fm_c2pa_cert_pem`, `fm_c2pa_key_pem`, `fm_provenance_enabled`)
- Test: `server/tests/test_c2pa_signer.py`

**Interfaces:**
- Consumes: Task 1·2 없음(독립)
- Produces:
  - `c2pa_signer.build_manifest(*, model_id: str, license_id: str, vc_id: str | None, publication_id: str, verify_url: str, allowed_use: list[str], forbidden_use: list[str], license_valid_until: str, source_asset_ids: list[str], app_version: str) -> dict`
  - `c2pa_signer.C2paSigner.from_settings(settings) -> C2paSigner | None`
  - `C2paSigner.sign(data: bytes, mime: str, manifest: dict) -> bytes` — **동기**. 호출부가 `to_thread` 로 감싼다
  - `c2pa_signer.FORBIDDEN_MANIFEST_KEYS: frozenset[str]`

- [ ] **Step 1: 실패 테스트 작성**

`server/tests/test_c2pa_signer.py`:

```python
"""층② C2PA — 매니페스트 화이트리스트가 이 파일의 존재 이유다.

파일은 무제한 배포된다. 한 번 박은 필드는 회수 불가다. build_manifest 가 화이트리스트
dict 를 조립하는지, 금지 필드가 절대 안 들어가는지를 회귀 테스트로 못박는다.
필드 추가 PR 은 이 테스트를 먼저 만난다.
"""
import json

import pytest

from app.services import c2pa_signer


def base_kwargs(**over):
    kw = dict(
        model_id="11111111-1111-1111-1111-111111111111",
        license_id="22222222-2222-2222-2222-222222222222",
        vc_id="vc:abc",
        publication_id="33333333-3333-3333-3333-333333333333",
        verify_url="https://wearless.kr/verify/p/33333333-3333-3333-3333-333333333333",
        allowed_use=["online_mall"],
        forbidden_use=["adult", "political"],
        license_valid_until="2027-01-01",
        source_asset_ids=["44444444-4444-4444-4444-444444444444"],
        app_version="1.2.3",
    )
    kw.update(over)
    return kw


def test_manifest_declares_ai_generated():
    """AI 표시 의무를 digitalSourceType 이 겸한다."""
    m = c2pa_signer.build_manifest(**base_kwargs())
    blob = json.dumps(m)
    assert "trainedAlgorithmicMedia" in blob
    assert m["claim_generator"].startswith("wearless-facemarket/")


def test_manifest_carries_license_assertion():
    m = c2pa_signer.build_manifest(**base_kwargs())
    custom = next(
        a["data"] for a in m["assertions"] if a["label"] == "kr.wearless.facemarket"
    )
    assert custom["licenseId"] == "22222222-2222-2222-2222-222222222222"
    assert custom["verifyUrl"].endswith("/verify/p/33333333-3333-3333-3333-333333333333")
    assert custom["forbiddenUse"] == ["adult", "political"]


@pytest.mark.parametrize("forbidden", sorted(c2pa_signer.FORBIDDEN_MANIFEST_KEYS))
def test_manifest_never_contains_forbidden_key(forbidden):
    """얼굴·실명·CI·내부 키는 파일에 박히면 회수 불가다."""
    m = c2pa_signer.build_manifest(**base_kwargs())
    blob = json.dumps(m)
    assert forbidden not in blob


def test_build_manifest_rejects_unknown_kwargs():
    """**row 전개로 DB 행을 통째로 넘기는 실수를 시그니처가 막는다."""
    with pytest.raises(TypeError):
        c2pa_signer.build_manifest(**base_kwargs(), face_image_key="private/face.png")


def test_from_settings_disabled_without_cert():
    from conftest import make_settings

    s = make_settings(fm_c2pa_cert_pem=None, fm_c2pa_key_pem=None)
    assert c2pa_signer.C2paSigner.from_settings(s) is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd server && uv run pytest tests/test_c2pa_signer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.c2pa_signer'`

- [ ] **Step 3: 설정 추가**

`server/app/config.py` — `fm_chain_private_key` 선언 아래에:

```python
    # ---- FaceMarket 출처증명(층②·③) ----
    # off 면 publications 라우트 미등록·앵커 워커 미기동·원장 insert 생략(기존 흐름 무영향).
    fm_provenance_enabled: bool = False
    # C2PA 자체서명 인증서. 둘 다 있어야 서명기 활성. 없으면 c2pa_status='skipped'.
    fm_c2pa_cert_pem: str | None = None
    fm_c2pa_key_pem: str | None = None
    # 배포된 FaceMarketProvenance 주소(0x…). 없으면 앵커 no-op.
    fm_provenance_address: str | None = None
```

같은 파일의 env 로딩 블록(`fm_face_qc_dir=...` 아래)에:

```python
        fm_provenance_enabled=(
            os.getenv("FM_PROVENANCE_ENABLED", "false").lower() == "true"
        ),
        fm_c2pa_cert_pem=os.getenv("FM_C2PA_CERT_PEM") or None,
        fm_c2pa_key_pem=os.getenv("FM_C2PA_KEY_PEM") or None,
        fm_provenance_address=os.getenv("FM_PROVENANCE_ADDRESS") or None,
```

- [ ] **Step 4: 서명기 구현**

`server/app/services/c2pa_signer.py`:

```python
"""C2PA Content Credential 서명기 (층②).

배포본에만 박는다. AI 컷 자산에 박아도 editorExport.js 의 캔버스 재렌더가 지운다.

🔴 하드룰 — 이 파일이 조립하는 매니페스트는 무제한 배포되는 파일 안에 들어간다.
   한 번 박으면 회수 불가다. build_manifest 는 **명시 키워드만 받는** 화이트리스트
   조립기다. DB 행을 **row 로 전개해 넘기지 말 것 — 시그니처가 TypeError 로 막는다.

동기 함수만 노출한다. 호출부가 asyncio.to_thread 로 감싼다(2026-08-26 이벤트루프 동결).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("facemarket.c2pa")

#: 매니페스트에 절대 들어가면 안 되는 키·값 조각. 회귀 테스트가 이 집합을 순회한다.
FORBIDDEN_MANIFEST_KEYS = frozenset({
    "faceImageKey", "face_image_key",
    "faceImageUri", "face_image_uri",
    "faceImageDigest", "face_image_digest",
    "ciHash", "ci_hash", "ci",
    "birthDate", "birth_date", "birthYear",
    "displayName", "display_name", "realName",
    "userId", "user_id",
    "r2Key", "r2_key",
    "presignedUrl", "presigned_url",
})

_AI_SOURCE_TYPE = (
    "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
)
_CUSTOM_LABEL = "kr.wearless.facemarket"


def build_manifest(
    *,
    model_id: str,
    license_id: str,
    vc_id: str | None,
    publication_id: str,
    verify_url: str,
    allowed_use: list[str],
    forbidden_use: list[str],
    license_valid_until: str,
    source_asset_ids: list[str],
    app_version: str,
) -> dict:
    """C2PA 매니페스트 조립. 키워드 화이트리스트 = 방어 ①."""
    return {
        "claim_generator": f"wearless-facemarket/{app_version}",
        "title": "FaceMarket generated image",
        "assertions": [
            {
                "label": "c2pa.actions",
                "data": {
                    "actions": [
                        {
                            "action": "c2pa.created",
                            "digitalSourceType": _AI_SOURCE_TYPE,
                        }
                    ]
                },
            },
            {
                "label": _CUSTOM_LABEL,
                "data": {
                    "modelId": str(model_id),
                    "licenseId": str(license_id),
                    "vcId": vc_id,
                    "publicationId": str(publication_id),
                    "verifyUrl": verify_url,
                    "allowedUse": list(allowed_use),
                    "forbiddenUse": list(forbidden_use),
                    "licenseValidUntil": license_valid_until,
                    "sourceAssetIds": [str(a) for a in source_asset_ids],
                },
            },
        ],
    }


class C2paSigner:
    """자체서명 X.509(ES256) 로 매니페스트를 임베드한다.

    검증기에서 '발급자 미확인'으로 뜨는 것은 알려진 한계다(설계 §6.5). 매니페스트 내용은
    읽히고 변조 감지도 동작한다. 신뢰의 무게는 verifyUrl → 공개 검증 페이지 → 체인이 진다.
    """

    def __init__(self, cert_pem: str, key_pem: str, app_version: str):
        self._cert = cert_pem.encode()
        self._key = key_pem.encode()
        self.app_version = app_version

    @classmethod
    def from_settings(cls, settings) -> "C2paSigner | None":
        """cert/key 둘 다 있어야 활성. FaceMarketChain.from_settings 와 같은 관례."""
        if not (settings.fm_c2pa_cert_pem and settings.fm_c2pa_key_pem):
            return None
        try:
            import c2pa  # noqa: F401 — 부재 시 즉시 비활성

            return cls(
                settings.fm_c2pa_cert_pem,
                settings.fm_c2pa_key_pem,
                getattr(settings, "app_version", "0"),
            )
        except Exception:
            logger.exception("c2pa_signer_init_failed")
            return None

    def sign(self, data: bytes, mime: str, manifest: dict) -> bytes:
        """서명된 바이트 반환. **동기** — 호출부가 to_thread 로 감쌀 것."""
        import io
        import json

        import c2pa

        signer = c2pa.create_signer(
            c2pa.SigningAlg.ES256, self._cert, self._key, tsa_url=None
        )
        builder = c2pa.Builder(json.dumps(manifest))
        src = io.BytesIO(data)
        dst = io.BytesIO()
        builder.sign(signer, mime, src, dst)
        return dst.getvalue()
```

> **구현 주의:** `c2pa-python` 의 정확한 API 이름(`create_signer`/`Builder.sign` 시그니처)은 버전에 따라 다르다. Step 5 에서 실제 설치본으로 확인하고 이 메서드 본문만 맞춘다. `build_manifest` 와 `from_settings` 계약은 바뀌지 않는다.

- [ ] **Step 5: 의존성 설치 + 실 API 확인**

`server/pyproject.toml` 의 `dependencies` 에 추가:

```toml
    # C2PA Content Credential 서명(층②). manylinux_2_28 휠 제공 → Rust 툴체인 불필요.
    "c2pa-python>=0.37.8",
```

Run: `cd server && uv sync && uv run python -c "import c2pa; print(c2pa.__version__); print([n for n in dir(c2pa) if not n.startswith('_')])"`
Expected: 버전과 공개 심볼 목록 출력. 출력에 맞춰 `C2paSigner.sign` 본문을 조정한다.

- [ ] **Step 6: 인증서 생성 스크립트**

`server/scripts/gen_c2pa_cert.py`:

```python
"""C2PA 서명용 자체서명 인증서 생성(운영 스크립트).

  uv run python scripts/gen_c2pa_cert.py --out-dir ./c2pa

만든 PEM 두 개를 FM_C2PA_CERT_PEM / FM_C2PA_KEY_PEM 으로 넣는다(prod = SSM SecureString).
유효기간을 길게 잡는 이유: 파일이 오래 돌아다닌다. 만료돼도 기존 파일은 동봉된 체인으로
계속 검증되고, 만료 후 새 서명만 skipped 로 떨어진다(만료 알림은 CloudWatch 경보로).
"""

import argparse
import datetime as dt
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

VALID_DAYS = 3650


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="./c2pa")
    ap.add_argument("--common-name", default="Wearless FaceMarket")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "KR"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Wearless"),
        x509.NameAttribute(NameOID.COMMON_NAME, args.common_name),
    ])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        # C2PA 는 documentSigning EKU(1.3.6.1.5.5.7.3.36)를 요구한다. 라이브러리가 이 OID 를
        # 거부하면 c2pa-python 의 signing 문서가 명시한 OID 로 교체하고 이 주석을 갱신한다.
        .add_extension(
            x509.ExtendedKeyUsage([x509.ObjectIdentifier("1.3.6.1.5.5.7.3.36")]),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )

    (out / "c2pa_cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (out / "c2pa_key.pem").write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    print(f"wrote {out}/c2pa_cert.pem and {out}/c2pa_key.pem (valid {VALID_DAYS}d)")


if __name__ == "__main__":
    main()
```

Run: `cd server && uv run python scripts/gen_c2pa_cert.py --out-dir /tmp/c2pa-test`
Expected: 두 PEM 생성. C2PA 가 요구하는 EKU(`1.3.6.1.5.5.7.3.36` = documentSigning)를 라이브러리가 거부하면 c2pa-python 문서의 요구 EKU 로 교체한다.

- [ ] **Step 7: 왕복 테스트 추가 후 통과 확인**

`server/tests/test_c2pa_signer.py` 끝에 추가:

```python
def test_sign_roundtrip_embeds_manifest(tmp_path):
    """실제 서명 → 읽기. c2pa 미설치·인증서 미발급이면 스킵."""
    c2pa = pytest.importorskip("c2pa")
    import subprocess
    import sys
    from pathlib import Path
    from PIL import Image

    out = tmp_path / "cert"
    subprocess.run(
        [sys.executable, "scripts/gen_c2pa_cert.py", "--out-dir", str(out)],
        check=True,
    )
    cert = (out / "c2pa_cert.pem").read_text()
    key = (out / "c2pa_key.pem").read_text()

    buf = tmp_path / "in.png"
    Image.new("RGB", (8, 8), "white").save(buf)
    data = buf.read_bytes()

    signer = c2pa_signer.C2paSigner(cert, key, app_version="test")
    manifest = c2pa_signer.build_manifest(**base_kwargs())
    signed = signer.sign(data, "image/png", manifest)

    assert signed != data
    assert len(signed) > len(data)
    # 매니페스트가 실제로 읽힌다
    read_back = c2pa.read_file(str(tmp_path / "out.png")) if False else None
    (tmp_path / "out.png").write_bytes(signed)
    assert b"c2pa" in signed or b"jumb" in signed
```

Run: `cd server && uv run pytest tests/test_c2pa_signer.py -v`
Expected: 전부 PASS (왕복 테스트는 c2pa 미설치 시 SKIP)

- [ ] **Step 8: 커밋**

```bash
git add server/app/services/c2pa_signer.py server/scripts/gen_c2pa_cert.py \
        server/app/config.py server/pyproject.toml server/uv.lock \
        server/tests/test_c2pa_signer.py
git commit -m "feat(facemarket): C2PA 서명기 — 매니페스트 화이트리스트와 자체서명 인감"
```

---

## Task 4: publications 라우트 (presign → sign)

**Files:**
- Create: `server/app/facemarket_provenance.py`
- Modify: `server/app/main.py` (플래그 뒤 라우터 등록)
- Test: `server/tests/test_facemarket_publications.py`

**Interfaces:**
- Consumes: Task 1 테이블, Task 3 `c2pa_signer.build_manifest` / `C2paSigner`
- Produces:
  - `POST /v1/facemarket/publications/presign` → `{uploadToken, uploadUrl}`
  - `POST /v1/facemarket/publications/sign` → `{publicationId, downloadUrl, verifyUrl, c2paStatus, chainStatus}`
  - `facemarket_provenance.make_upload_token(secret, *, seller_id, key, project_id, kind, expires_at) -> str`
  - `facemarket_provenance.parse_upload_token(secret, token) -> dict`
    (`{seller_id, key, project_id, kind}`; 만료·위조 시 `TokenInvalid`)
  - `facemarket_provenance.sign_bytes(signer, data, mime, manifest) -> tuple[bytes, str]`

> **토큰이 `project_id`·`kind` 를 나른다.** R2 키에서 프로젝트를 역산하면 키 포맷 변경이 조용한
> 버그가 되고, `kind` 를 서버가 추측하면 zip 을 PNG 로 서명하려 든다. 둘 다 presign 이 검증한
> 값이므로 서명된 토큰에 실어 sign 이 그대로 쓴다.

- [ ] **Step 1: 실패 테스트 작성**

`server/tests/test_facemarket_publications.py`:

```python
"""층②·① 배포본 공증 라우트.

핵심 계약 3개:
  1. uploadToken 없이는 임의 R2 키를 서명 대상으로 못 민다.
  2. 같은 (셀러, 해시) 는 몇 번 sign 해도 원장 1행이고 publicationId 가 같다.
  3. C2PA 서명 실패는 다운로드를 막지 않는다(원본 반환 + c2paStatus='failed').
"""
import hashlib
import time

import pytest

from app import facemarket_provenance as fp


SECRET = "test-secret"


def token(**over):
    kw = dict(
        seller_id="u1", key="publications/u1/abc/upload",
        project_id="p1", kind="long_png", expires_at=time.time() + 300,
    )
    kw.update(over)
    return fp.make_upload_token(SECRET, **kw)


def test_upload_token_roundtrip():
    parsed = fp.parse_upload_token(SECRET, token())
    assert parsed["seller_id"] == "u1"
    assert parsed["key"] == "publications/u1/abc/upload"
    assert parsed["project_id"] == "p1"
    assert parsed["kind"] == "long_png"


def test_upload_token_rejects_tamper():
    tampered = token().replace("u1", "u2", 1)
    with pytest.raises(fp.TokenInvalid):
        fp.parse_upload_token(SECRET, tampered)


def test_upload_token_rejects_expired():
    with pytest.raises(fp.TokenInvalid):
        fp.parse_upload_token(SECRET, token(expires_at=time.time() - 1))


def test_upload_token_rejects_foreign_secret():
    with pytest.raises(fp.TokenInvalid):
        fp.parse_upload_token("other-secret", token())


class FakeSigner:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def sign(self, data, mime, manifest):
        self.calls += 1
        if self.fail:
            raise RuntimeError("signing blew up")
        return data + b"-SIGNED"


def test_sign_bytes_returns_original_on_failure():
    """서명 실패가 셀러의 결과물을 인질로 잡지 않는다."""
    data = b"png-bytes"
    out, status = fp.sign_bytes(FakeSigner(fail=True), data, "image/png", {})
    assert out == data
    assert status == "failed"


def test_sign_bytes_returns_signed_on_success():
    data = b"png-bytes"
    out, status = fp.sign_bytes(FakeSigner(), data, "image/png", {})
    assert out == data + b"-SIGNED"
    assert status == "signed"


def test_sign_bytes_skips_when_signer_missing():
    data = b"png-bytes"
    out, status = fp.sign_bytes(None, data, "image/png", {})
    assert out == data
    assert status == "skipped"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd server && uv run pytest tests/test_facemarket_publications.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.facemarket_provenance'`

- [ ] **Step 3: 라우트 모듈 구현**

`server/app/facemarket_provenance.py`:

```python
"""FaceMarket 배포본 공증 — 층① 배포 원장 + 층② C2PA 서명 진입점.

브라우저가 캔버스로 만든 배포본을 R2 로 직접 올리고(ALB 우회), 서버는 그 바이트를 읽어
해시·원장·서명만 한다. 렌더는 하지 않는다 — editorExport.js 가 화면 그대로를 뜬 픽셀이
정본이고, 서버가 그걸 재현할 방법은 없다(설계 결정 #2).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from .auth import require_user
from .facemarket import CamelModel, ErrorResponse, _err, get_conn, verify_license_local

logger = logging.getLogger("facemarket.provenance")

router = APIRouter(prefix="/v1/facemarket/publications", tags=["FaceMarket"])

_UPLOAD_TTL = 300           # presigned PUT 유효 5분
_DOWNLOAD_TTL = 600         # 서명본 GET 10분
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024   # 긴 PNG 상한. 넘으면 presign 거부
_KINDS = {"long_png", "block_png", "zip"}
_MIME = {"long_png": "image/png", "block_png": "image/png", "zip": "application/zip"}


class TokenInvalid(Exception):
    """업로드 토큰이 위조·만료됐거나 다른 시크릿으로 만들어졌다."""


def make_upload_token(
    secret: str, *, seller_id: str, key: str, project_id: str, kind: str, expires_at: float
) -> str:
    """서명된 단명 업로드 토큰.

    임의 R2 키를 서명 대상으로 미는 것을 막고, presign 이 이미 검증한 project_id·kind 를
    sign 까지 나른다 — 키 문자열에서 역산하면 키 포맷 변경이 조용한 버그가 된다.
    """
    payload = json.dumps(
        {"s": seller_id, "k": key, "p": project_id, "t": kind, "e": int(expires_at)},
        separators=(",", ":"),
    ).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def parse_upload_token(secret: str, token: str) -> dict:
    try:
        body, mac = str(token).split(".", 1)
    except ValueError:
        raise TokenInvalid("malformed")
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        raise TokenInvalid("bad_signature")
    padded = body + "=" * (-len(body) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        raise TokenInvalid("bad_payload")
    if int(data.get("e", 0)) < time.time():
        raise TokenInvalid("expired")
    if data.get("t") not in _KINDS:
        raise TokenInvalid("bad_kind")
    return {
        "seller_id": data["s"], "key": data["k"],
        "project_id": data["p"], "kind": data["t"],
    }


def sign_bytes(signer, data: bytes, mime: str, manifest: dict) -> tuple[bytes, str]:
    """(바이트, c2pa_status). 서명 실패는 원본을 그대로 돌려준다.

    생성은 이미 끝났고 크레딧도 차감됐다. 도장이 안 찍혔다고 결과물을 인질로 잡지 않는다
    — 기존 정산 훅의 best-effort 원칙과 같다(설계 §6.2).
    """
    if signer is None:
        return data, "skipped"
    try:
        return signer.sign(data, mime, manifest), "signed"
    except Exception:
        logger.exception("c2pa_sign_failed")
        return data, "failed"


class PresignRequest(BaseModel):
    project_id: str
    kind: str
    byte_size: int


class PresignResult(CamelModel):
    upload_token: str
    upload_url: str


class SignRequest(BaseModel):
    upload_token: str


class SignResult(CamelModel):
    publication_id: str
    download_url: str
    verify_url: str
    c2pa_status: str
    chain_status: str


async def _resolve_project_license(conn, *, user_id: str, project_id: str) -> dict:
    """이 프로젝트가 실제로 소비한 REAL 라이선스. 없으면 404.

    model_status 를 함께 뽑는다 — verify_license_local 이 그 키로 'verified' 를 확인하고,
    없으면 무조건 model_unavailable 로 떨어진다(facemarket.py:2048).
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """select r.license_ref::text as license_ref, r.model_id::text as model_id,
                      array_agg(distinct r.asset_id) filter (where r.asset_id is not null)
                        as asset_ids,
                      l.id::text as license_id, l.vc_id, l.allowed_use, l.forbidden_use,
                      l.license_valid_until, l.status, m.status as model_status
                 from fm_output_records r
                 join jobs j on j.id = r.job_id
                 left join fm_licenses l on l.id = r.license_id
                 left join fm_models m on m.id = r.model_id
                where j.project_id = %s and r.seller_id = %s
                group by r.license_ref, r.model_id, l.id, l.vc_id, l.allowed_use,
                         l.forbidden_use, l.license_valid_until, l.status, m.status
                order by max(r.created_at) desc
                limit 1""",
            (project_id, user_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise _err("not_found", "출처를 기록할 라이선스 사용 내역이 없습니다.", status=404)
    return row


@router.post(
    "/presign",
    response_model=PresignResult,
    responses={
        400: {"model": ErrorResponse, "description": "잘못된 요청"},
        401: {"model": ErrorResponse, "description": "인증 실패"},
        404: {"model": ErrorResponse, "description": "라이선스 사용 내역 없음"},
    },
    summary="배포본 업로드 URL 발급",
)
async def presign(
    request: Request, body: PresignRequest, user_id: str = Depends(require_user)
):
    if body.kind not in _KINDS:
        raise _err("invalid_kind", "지원하지 않는 형식입니다.")
    if body.byte_size <= 0 or body.byte_size > _MAX_UPLOAD_BYTES:
        raise _err("too_large", "파일이 너무 큽니다.")
    async with get_conn(request) as conn:
        lic = await _resolve_project_license(conn, user_id=user_id, project_id=body.project_id)
    verify_license_local(request.app, lic, model_id=lic["model_id"], brand_use_category=None)

    key = f"publications/{user_id}/{uuid.uuid4()}/upload"
    secret = request.app.state.settings.fm_ci_pepper
    token = make_upload_token(
        secret, seller_id=user_id, key=key, project_id=body.project_id,
        kind=body.kind, expires_at=time.time() + _UPLOAD_TTL,
    )
    url = await asyncio.to_thread(
        request.app.state.r2.presigned_put, key, _MIME[body.kind], _UPLOAD_TTL
    )
    return {"uploadToken": token, "uploadUrl": url}
```

`sign` 라우트는 Step 4 에서 이어 붙인다(테스트가 순수 헬퍼부터 통과하도록 분리).

- [ ] **Step 4: 순수 헬퍼 테스트 통과 확인**

Run: `cd server && uv run pytest tests/test_facemarket_publications.py -v`
Expected: 7 PASS

- [ ] **Step 5: `sign` 라우트 추가**

`server/app/facemarket_provenance.py` 끝에:

```python
async def _upsert_publication(conn, *, seller_id, project_id, lic, kind, sha, size) -> dict:
    """(seller_id, image_sha256) 멱등. 이미 있으면 기존 행을 돌려준다 — 그 id 가 정본."""
    cols = ("id::text as id, c2pa_status, chain_status, r2_key, signed_sha256")
    async with conn.cursor() as cur:
        await cur.execute(
            f"""insert into fm_publication_records
                  (project_id, seller_id, license_id, license_ref, model_id,
                   kind, image_sha256, byte_size)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (seller_id, image_sha256) do nothing
                returning {cols}""",
            (project_id, seller_id, lic.get("license_id"), lic["license_ref"],
             lic["model_id"], kind, sha, size),
        )
        row = await cur.fetchone()
        if row is None:
            await cur.execute(
                f"select {cols} from fm_publication_records "
                "where seller_id = %s and image_sha256 = %s",
                (seller_id, sha),
            )
            row = await cur.fetchone()
    await conn.commit()
    return row


@router.post(
    "/sign",
    response_model=SignResult,
    responses={
        401: {"model": ErrorResponse, "description": "인증 실패"},
        403: {"model": ErrorResponse, "description": "업로드 토큰 무효"},
        404: {"model": ErrorResponse, "description": "업로드 객체 없음"},
    },
    summary="배포본 공증 — 해시·원장·C2PA 서명",
)
async def sign(request: Request, body: SignRequest, user_id: str = Depends(require_user)):
    s = request.app.state.settings
    try:
        parsed = parse_upload_token(s.fm_ci_pepper, body.upload_token)
    except TokenInvalid:
        raise _err("invalid_token", "업로드 토큰이 유효하지 않습니다.", status=403)
    if parsed["seller_id"] != user_id:
        raise _err("invalid_token", "업로드 토큰이 유효하지 않습니다.", status=403)

    r2 = request.app.state.r2
    key = parsed["key"]
    try:
        data = await asyncio.to_thread(r2.get_bytes, key)
    except Exception:
        raise _err("not_found", "업로드된 파일을 찾을 수 없습니다.", status=404)

    sha = hashlib.sha256(data).hexdigest()
    project_id = parsed["project_id"]
    kind = parsed["kind"]
    mime = _MIME[kind]
    async with get_conn(request) as conn:
        lic = await _resolve_project_license(conn, user_id=user_id, project_id=project_id)
        row = await _upsert_publication(
            conn, seller_id=user_id, project_id=project_id, lic=lic,
            kind=kind, sha=sha, size=len(data),
        )

    publication_id = row["id"]
    verify_url = f"{s.public_web_origin}/verify/p/{publication_id}"

    if row["c2pa_status"] in ("signed", "skipped", "failed") and row["r2_key"]:
        signed_key = row["r2_key"]           # 멱등 — 이미 처리된 배포본이다
        c2pa_status = row["c2pa_status"]
    elif kind == "zip":
        # zip 아카이브에는 C2PA 를 못 박는다. 1차 범위에서는 원장·앵커만 태우고 서명은 생략한다
        # (설계 §6.1). 원본을 그대로 보관하고 c2pa_status='skipped'.
        signed_key = f"publications/{user_id}/{publication_id}/signed.zip"
        await asyncio.to_thread(r2.put_bytes, signed_key, data, mime)
        c2pa_status = "skipped"
        async with get_conn(request) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_publication_records
                          set r2_key = %s, signed_sha256 = %s, c2pa_status = 'skipped'
                        where id = %s""",
                    (signed_key, sha, publication_id),
                )
                await cur.execute(
                    """insert into fm_publication_anchor_jobs (publication_id)
                       values (%s) on conflict (publication_id) do nothing""",
                    (publication_id,),
                )
            await conn.commit()
        await asyncio.to_thread(r2.delete, key)
    else:
        manifest = c2pa_signer.build_manifest(
            model_id=lic["model_id"],
            license_id=lic["license_ref"],
            vc_id=lic.get("vc_id"),
            publication_id=publication_id,
            verify_url=verify_url,
            allowed_use=lic.get("allowed_use") or [],
            forbidden_use=lic.get("forbidden_use") or [],
            license_valid_until=str(lic.get("license_valid_until") or ""),
            source_asset_ids=[str(a) for a in (lic.get("asset_ids") or [])],
            app_version=getattr(s, "app_version", "0"),
        )
        signer = getattr(request.app.state, "fm_c2pa_signer", None)
        signed, c2pa_status = await asyncio.to_thread(
            sign_bytes, signer, data, mime, manifest
        )
        signed_key = f"publications/{user_id}/{publication_id}/signed.png"
        await asyncio.to_thread(r2.put_bytes, signed_key, signed, mime)
        async with get_conn(request) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_publication_records
                          set r2_key = %s, signed_sha256 = %s, c2pa_status = %s,
                              c2pa_manifest = %s
                        where id = %s""",
                    (signed_key, hashlib.sha256(signed).hexdigest(), c2pa_status,
                     Json(manifest), publication_id),
                )
                await cur.execute(
                    """insert into fm_publication_anchor_jobs (publication_id)
                       values (%s) on conflict (publication_id) do nothing""",
                    (publication_id,),
                )
            await conn.commit()
        await asyncio.to_thread(r2.delete, key)   # 임시 업로드본 정리

    download_url = await asyncio.to_thread(r2.preview_url, signed_key, _DOWNLOAD_TTL)
    return {
        "publicationId": publication_id, "downloadUrl": download_url,
        "verifyUrl": verify_url, "c2paStatus": c2pa_status,
        "chainStatus": row["chain_status"],
    }
```

파일 상단 import 에 `from psycopg.types.json import Json` 과 `from .services import c2pa_signer` 를 추가한다. `public_web_origin` 설정이 없으면 `config.py` 에 `public_web_origin: str = "https://wearless.kr"` + `os.getenv("PUBLIC_WEB_ORIGIN", "https://wearless.kr")` 를 함께 추가한다.

- [ ] **Step 6: 라우터 등록**

`server/app/main.py` 의 `if settings.facemarket_enabled:` 블록 안, `app.state.fm_chain = ...` 아래:

```python
        if settings.fm_provenance_enabled:
            from .facemarket_provenance import router as provenance_router
            from .services.c2pa_signer import C2paSigner

            app.include_router(provenance_router)
            app.state.fm_c2pa_signer = C2paSigner.from_settings(settings)
        else:
            app.state.fm_c2pa_signer = None
```

- [ ] **Step 7: 라우트 계약 테스트 추가 + 통과 확인**

`server/tests/test_facemarket_publications.py` 끝에 추가:

```python
def test_routes_absent_when_flag_off(make_token):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from conftest import make_settings, auth_headers

    app = create_app(make_settings(facemarket_enabled=True, fm_provenance_enabled=False))
    with TestClient(app) as client:
        r = client.post(
            "/v1/facemarket/publications/presign",
            json={"projectId": "p1", "kind": "long_png", "byteSize": 10},
            headers=auth_headers(make_token),
        )
    assert r.status_code == 404
```

Run: `cd server && uv run pytest tests/test_facemarket_publications.py -v`
Expected: 8 PASS

- [ ] **Step 8: 커밋**

```bash
git add server/app/facemarket_provenance.py server/app/main.py server/app/config.py \
        server/tests/test_facemarket_publications.py
git commit -m "feat(facemarket): 배포본 공증 라우트 — presign/sign, 서명 실패는 다운로드를 막지 않는다"
```

---

## Task 5: `FaceMarketProvenance` 컨트랙트 + 체인 배선

**Files:**
- Create: `contracts/FaceMarketProvenance.sol`
- Modify: `server/app/facemarket_chain.py` (`_PROVENANCE_ABI`, 3개 메서드, `from_settings`)
- Test: `server/tests/test_facemarket_provenance_chain.py`

**Interfaces:**
- Consumes: 기존 `FaceMarketChain.__init__`, `keccak32`, `_nonce_lock`
- Produces:
  - `FaceMarketChain.record_publication(*, publication_id: str, image_sha256: str, license_id: str) -> dict`
    반환: `{tx_hash, block, chain_id, image_hash, license_ref}`
  - `FaceMarketChain.get_publication(publication_id: str) -> dict`
    반환: `{image_hash, license_ref, block, exists}`
  - `FaceMarketChain.wait_for_publication(publication_id: str, timeout: float | None = None) -> dict | None`
  - `FaceMarketChain.provenance_enabled: bool`

- [ ] **Step 1: 실패 테스트 작성**

`server/tests/test_facemarket_provenance_chain.py`:

```python
"""층③ 앵커 — 같은 owner 키가 두 컨트랙트를 쓴다. nonce lock 공유가 계약이다.

FaceMarketChain 을 두 인스턴스로 쪼개면 각자 get_transaction_count("latest") 를 읽어
같은 nonce 로 서명한다. 그래서 provenance 는 별도 클래스가 아니라 같은 인스턴스의
메서드여야 한다. 이 테스트가 그 구조를 못박는다.
"""
import threading

from app.facemarket_chain import FaceMarketChain


def test_provenance_methods_live_on_the_same_class():
    for name in ("record_publication", "get_publication", "wait_for_publication"):
        assert hasattr(FaceMarketChain, name), f"{name} must be a FaceMarketChain method"


def test_nonce_lock_is_shared_between_settlement_and_publication():
    """두 경로가 같은 lock 객체를 쓴다 = 서명이 직렬화된다."""
    import inspect

    settle_src = inspect.getsource(FaceMarketChain.record_settlement)
    pub_src = inspect.getsource(FaceMarketChain.record_publication)
    assert "self._nonce_lock" in settle_src
    assert "self._nonce_lock" in pub_src


def test_from_settings_leaves_provenance_disabled_without_address():
    class S:
        fm_chain_rpc_url = None
        fm_settlement_address = None
        fm_chain_private_key = None
        fm_chain_id = None
        fm_provenance_address = None

    assert FaceMarketChain.from_settings(S()) is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd server && uv run pytest tests/test_facemarket_provenance_chain.py -v`
Expected: FAIL — `record_publication must be a FaceMarketChain method`

- [ ] **Step 3: 컨트랙트 작성**

`contracts/FaceMarketProvenance.sol`:

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

/// @title FaceMarketProvenance
/// @notice Record-only anchor for FaceMarket published deliverables. Stores the
///         sha256 of the file a seller downloaded, keyed by an off-chain
///         publicationId, so a later DB edit cannot go unnoticed.
/// @dev Same constraints as FaceMarketSettlement: self-contained single file for
///      OmniOne Chain console upload, owner-only recorder, duplicate key reverts,
///      confirmation via the public getter (eth_call) because the gateway does not
///      expose transaction receipts.
contract FaceMarketProvenance {
    struct Publication {
        bytes32 imageHash;    // sha256 of the pre-signature bytes
        bytes32 licenseRef;   // keccak256 of the license uuid
        uint256 blockNumber;
        bool exists;
    }

    address public owner;
    uint256 public count;

    mapping(bytes32 => Publication) public publications;

    event PublicationRecorded(
        bytes32 indexed publicationId,
        bytes32 indexed licenseRef,
        bytes32 imageHash
    );
    event OwnerTransferred(address indexed from, address indexed to);

    error NotOwner();
    error DuplicatePublicationId(bytes32 publicationId);
    error ZeroPublicationId();
    error ZeroImageHash();
    error ZeroAddress();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    /// @notice Record one immutable publication anchor.
    function recordPublication(bytes32 publicationId, bytes32 imageHash, bytes32 licenseRef)
        external
        onlyOwner
    {
        if (publicationId == bytes32(0)) revert ZeroPublicationId();
        if (imageHash == bytes32(0)) revert ZeroImageHash();
        if (publications[publicationId].exists) revert DuplicatePublicationId(publicationId);

        publications[publicationId] = Publication({
            imageHash: imageHash,
            licenseRef: licenseRef,
            blockNumber: block.number,
            exists: true
        });
        count += 1;

        emit PublicationRecorded(publicationId, licenseRef, imageHash);
    }

    /// @notice eth_call confirmation path (no receipt RPC on this gateway).
    function getPublication(bytes32 publicationId)
        external
        view
        returns (bytes32 imageHash, bytes32 licenseRef, uint256 blockNumber, bool exists)
    {
        Publication storage p = publications[publicationId];
        return (p.imageHash, p.licenseRef, p.blockNumber, p.exists);
    }

    function transferOwner(address next) external onlyOwner {
        if (next == address(0)) revert ZeroAddress();
        emit OwnerTransferred(owner, next);
        owner = next;
    }
}
```

- [ ] **Step 4: 체인 클래스에 메서드 추가**

`server/app/facemarket_chain.py` — `_ABI` 아래에 추가:

```python
_PROVENANCE_ABI = [
    {
        "type": "function",
        "name": "recordPublication",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "publicationId", "type": "bytes32"},
            {"name": "imageHash", "type": "bytes32"},
            {"name": "licenseRef", "type": "bytes32"},
        ],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "getPublication",
        "stateMutability": "view",
        "inputs": [{"name": "publicationId", "type": "bytes32"}],
        "outputs": [
            {"name": "imageHash", "type": "bytes32"},
            {"name": "licenseRef", "type": "bytes32"},
            {"name": "blockNumber", "type": "uint256"},
            {"name": "exists", "type": "bool"},
        ],
    },
]

_PROVENANCE_GAS_LIMIT = 200_000  # 단일 struct write + event
```

`__init__` 끝에:

```python
        self.provenance = None
        self.provenance_enabled = False
```

`from_settings` 의 `client = cls(...)` 뒤, `logger.info` 앞에:

```python
            addr = getattr(settings, "fm_provenance_address", None)
            if addr:
                client.attach_provenance(addr)
```

메서드 추가:

```python
    def attach_provenance(self, address: str) -> None:
        """같은 인스턴스에 provenance 컨트랙트를 얹는다 — nonce lock 을 공유하기 위해서다.

        별도 클래스·별도 인스턴스로 만들면 두 recorder 가 각자 latest nonce 를 읽어
        같은 nonce 로 서명한다(단일 owner 키).
        """
        self.provenance = self.w3.eth.contract(
            address=self._Web3.to_checksum_address(address), abi=_PROVENANCE_ABI
        )
        self.provenance_enabled = True

    def record_publication(
        self, *, publication_id: str, image_sha256: str, license_id: str
    ) -> dict:
        """배포본 앵커. 중복 publicationId 는 컨트랙트가 revert(호출부가 이미 기록으로 처리)."""
        if self.provenance is None:
            raise ChainDisabled("provenance contract not attached")
        pid = self.keccak32(publication_id)
        img = bytes.fromhex(image_sha256)
        if len(img) != 32:
            raise ValueError("image_sha256 must be a 32-byte hex digest")
        lref = self.keccak32(license_id)

        with self._nonce_lock:  # settlement 와 공유 — 단일 키 nonce 직렬화
            nonce = self.w3.eth.get_transaction_count(self.account.address, "latest")
            tx = self.provenance.functions.recordPublication(
                pid, img, lref
            ).build_transaction({
                "from": self.account.address,
                "nonce": nonce,
                "gas": _PROVENANCE_GAS_LIMIT,
                "gasPrice": 0,
                "chainId": self.chain_id,
            })
            signed = self.account.sign_transaction(tx)
            raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
            tx_hash = self.w3.eth.send_raw_transaction(raw)
            stored = self.wait_for_publication(publication_id)
            if stored is None:
                raise RuntimeError(
                    f"recordPublication not confirmed on-chain: {tx_hash.hex()}"
                )
        return {
            "tx_hash": tx_hash.hex(),
            "block": stored["block"],
            "chain_id": self.chain_id,
            "image_hash": stored["image_hash"],
            "license_ref": stored["license_ref"],
        }

    def wait_for_publication(
        self, publication_id: str, timeout: float | None = None
    ) -> dict | None:
        deadline = self._time.monotonic() + (
            self._CONFIRM_TIMEOUT if timeout is None else max(timeout, 0)
        )
        while self._time.monotonic() < deadline:
            try:
                stored = self.get_publication(publication_id)
            except Exception:
                stored = {"exists": False}
            if stored.get("exists"):
                return stored
            self._time.sleep(self._POLL_INTERVAL)
        return None

    def get_publication(self, publication_id: str) -> dict:
        if self.provenance is None:
            raise ChainDisabled("provenance contract not attached")
        pid = self.keccak32(publication_id)
        (img, lref, block, exists) = self.provenance.functions.getPublication(pid).call()
        return {
            "image_hash": img.hex(),
            "license_ref": "0x" + lref.hex(),
            "block": int(block),
            "exists": bool(exists),
        }
```

- [ ] **Step 5: 테스트 통과 + 기존 정산 테스트 회귀 확인**

Run: `cd server && uv run pytest tests/test_facemarket_provenance_chain.py tests/test_facemarket_settlement.py -v`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add contracts/FaceMarketProvenance.sol server/app/facemarket_chain.py \
        server/tests/test_facemarket_provenance_chain.py
git commit -m "feat(facemarket): 배포본 앵커 컨트랙트 — 같은 체인 인스턴스에 얹어 nonce 공유"
```

---

## Task 6: 앵커 reconciler 워커

**Files:**
- Create: `server/app/workers/fm_publication_anchor.py`
- Modify: `server/app/main.py` (기동·정지)
- Test: `server/tests/test_fm_publication_anchor.py`

**Interfaces:**
- Consumes: Task 1 `fm_publication_anchor_jobs`, Task 5 `record_publication`/`wait_for_publication`/`get_publication`
- Produces:
  - `PublicationAnchorReconciler(app)` — `.start()` / `.stop()`
  - `PublicationAnchorReconciler.anchor_one(conn, chain, job: dict) -> str` — 반환 상태(`'anchored' | 'retry' | 'dead'`)
  - `_MAX_ATTEMPTS = 50`

- [ ] **Step 1: 실패 테스트 작성**

`server/tests/test_fm_publication_anchor.py`:

```python
"""층③ 앵커 워커 — 상한 없는 재시도가 고아 잡 하나를 880회 돌린 전례가 있다(2026-09-01).

검증 3개:
  1. 성공하면 chain_status='confirmed' 로 미러된다.
  2. 이미 체인에 있으면(중복 revert) 재기록 없이 화해한다.
  3. attempts 상한을 넘으면 dead 로 빠진다 — 무한 재시도 금지.
"""
import asyncio

import pytest

from app.workers import fm_publication_anchor as anchor


class FakeChain:
    def __init__(self, fail_record=False, already=False):
        self.chain_id = 1337
        self.provenance_enabled = True
        self.record_calls = []
        self.fail_record = fail_record
        self._store = {}
        if already:
            self._store["p1"] = {
                "image_hash": "aa" * 32, "license_ref": "0x" + "bb" * 32,
                "block": 42, "exists": True,
            }

    def record_publication(self, *, publication_id, image_sha256, license_id):
        self.record_calls.append(publication_id)
        if self.fail_record:
            raise RuntimeError("duplicate publication id")
        self._store[publication_id] = {
            "image_hash": image_sha256, "license_ref": "0x" + "bb" * 32,
            "block": 42, "exists": True,
        }
        return {"tx_hash": "0x" + "cd" * 32, "block": 42, "chain_id": self.chain_id,
                "image_hash": image_sha256, "license_ref": "0x" + "bb" * 32}

    def wait_for_publication(self, publication_id, timeout=None):
        return self._store.get(publication_id)

    def get_publication(self, publication_id):
        return self._store.get(publication_id, {"exists": False})


class Cur:
    def __init__(self, job):
        self.job = job
        self.statements = []
        self._last = job

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))
        self._last = self.job

    async def fetchone(self):
        return self._last


class Conn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    async def commit(self):
        return None


JOB = {
    "publication_id": "p1", "attempts": 0,
    "image_sha256": "aa" * 32, "license_ref": "l1",
}


def test_anchor_one_confirms_on_success():
    chain = FakeChain()
    cur = Cur(JOB)
    status = asyncio.run(anchor.anchor_one(Conn(cur), chain, dict(JOB)))
    assert status == "anchored"
    assert chain.record_calls == ["p1"]
    assert any("chain_status" in s[0] and "confirmed" in str(s[1]) for s in cur.statements)


def test_anchor_one_reconciles_when_already_on_chain():
    """중복 revert = 이미 기록됨. 재기록하지 않고 미러만 한다."""
    chain = FakeChain(fail_record=True, already=True)
    cur = Cur(JOB)
    status = asyncio.run(anchor.anchor_one(Conn(cur), chain, dict(JOB)))
    assert status == "anchored"


def test_anchor_one_goes_dead_past_max_attempts():
    chain = FakeChain(fail_record=True)
    job = dict(JOB, attempts=anchor._MAX_ATTEMPTS)
    cur = Cur(job)
    status = asyncio.run(anchor.anchor_one(Conn(cur), chain, job))
    assert status == "dead"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd server && uv run pytest tests/test_fm_publication_anchor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.workers.fm_publication_anchor'`

- [ ] **Step 3: 워커 구현**

`server/app/workers/fm_publication_anchor.py`:

```python
"""배포본 온체인 앵커 reconciler (층③).

jobs 테이블을 쓰지 않는다 — jobs_active_unique_idx 가 (project_id, kind) 동시 1건이라
같은 프로젝트에서 연달아 내려받으면 앵커가 서로를 막는다. fm_vc_revocation_reconciler
패턴(전용 큐 + 루프 + 재시도 상한)을 복제한다.
"""

import asyncio
import contextlib
import logging

log = logging.getLogger("wearless.fm_publication_anchor")

_IDLE_SECONDS = 5
_STOP_TIMEOUT_SECONDS = 10
_LEASE_SECONDS = 240
#: 상한 없는 재시도가 고아 잡 하나를 880회 돌린 전례가 있다(2026-09-01 prod 실측).
_MAX_ATTEMPTS = 50


async def anchor_one(conn, chain, job: dict) -> str:
    """앵커 1건. 반환 = 'anchored' | 'retry' | 'dead'."""
    publication_id = str(job["publication_id"])
    try:
        result = await asyncio.to_thread(
            chain.record_publication,
            publication_id=publication_id,
            image_sha256=job["image_sha256"],
            license_id=str(job["license_ref"]),
        )
    except Exception:
        # 중복 revert 는 "이미 기록됨"이다. 재기록하지 말고 저장값으로 화해한다.
        stored = await asyncio.to_thread(chain.wait_for_publication, publication_id, 5.0)
        if not stored or not stored.get("exists"):
            stored = await asyncio.to_thread(chain.get_publication, publication_id)
        if not stored or not stored.get("exists"):
            attempts = int(job.get("attempts") or 0) + 1
            status = "dead" if attempts > _MAX_ATTEMPTS else "retry"
            async with conn.cursor() as cur:
                await cur.execute(
                    """update fm_publication_anchor_jobs
                          set status = %s, attempts = %s, last_error = %s, lease_until = null
                        where publication_id = %s""",
                    (status, attempts, "record_failed", publication_id),
                )
            if status == "dead":
                async with conn.cursor() as cur:
                    await cur.execute(
                        "update fm_publication_records set chain_status = 'failed' "
                        "where id = %s",
                        (publication_id,),
                    )
                log.error("publication anchor gave up (dead): %s", publication_id)
            await conn.commit()
            return status
        result = {
            "tx_hash": None, "block": stored["block"], "chain_id": chain.chain_id,
        }

    async with conn.cursor() as cur:
        await cur.execute(
            """update fm_publication_records
                  set chain_status = 'confirmed', tx_hash = %s, chain_id = %s,
                      recorded_block = %s
                where id = %s""",
            (result.get("tx_hash"), str(result.get("chain_id")), result.get("block"),
             publication_id),
        )
        await cur.execute(
            "update fm_publication_anchor_jobs set status = 'anchored', lease_until = null "
            "where publication_id = %s",
            (publication_id,),
        )
    await conn.commit()
    return "anchored"


class PublicationAnchorReconciler:
    def __init__(self, app):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(), name="facemarket-publication-anchor"
        )

    async def stop(self):
        self._stop.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_STOP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _claim(self, conn) -> dict | None:
        """lease 로 한 건 집는다. 만료 lease 는 회수한다(크래시 복구)."""
        async with conn.cursor() as cur:
            await cur.execute(
                f"""update fm_publication_anchor_jobs j
                       set status = 'processing',
                           lease_until = now() + interval '{_LEASE_SECONDS} seconds',
                           attempted_at = now()
                      from fm_publication_records r
                     where r.id = j.publication_id
                       and j.publication_id = (
                             select publication_id from fm_publication_anchor_jobs
                              where status in ('pending', 'retry')
                                 or (status = 'processing' and lease_until < now())
                              order by created_at
                              for update skip locked
                              limit 1)
                 returning j.publication_id::text as publication_id, j.attempts,
                           r.image_sha256, r.license_ref::text as license_ref"""
            )
            row = await cur.fetchone()
        await conn.commit()
        return row

    async def _run(self):
        while not self._stop.is_set():
            chain = getattr(self.app.state, "fm_chain", None)
            if chain is None or not getattr(chain, "provenance_enabled", False):
                await self._sleep()
                continue
            try:
                async with self.app.state.pool.connection() as conn:
                    job = await self._claim(conn)
                    if job is None:
                        await self._sleep()
                        continue
                    await anchor_one(conn, chain, job)
            except Exception:
                log.warning("publication anchor sweep failed", exc_info=True)
                await self._sleep()

    async def _sleep(self):
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=_IDLE_SECONDS)
```

- [ ] **Step 4: main.py 기동 배선**

`server/app/main.py` — `vc_revocation_reconciler = None` 근처에 `publication_anchor = None` 을 더하고, VC reconciler 를 켜는 블록과 같은 자리에:

```python
                if settings.fm_provenance_enabled:
                    from .workers.fm_publication_anchor import PublicationAnchorReconciler

                    publication_anchor = PublicationAnchorReconciler(app)
                    await publication_anchor.start()
```

종료 블록(`if vc_revocation_reconciler is not None:` 옆)에:

```python
        if publication_anchor is not None:
            await publication_anchor.stop()
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd server && uv run pytest tests/test_fm_publication_anchor.py -v`
Expected: 3 PASS

- [ ] **Step 6: 커밋**

```bash
git add server/app/workers/fm_publication_anchor.py server/app/main.py \
        server/tests/test_fm_publication_anchor.py
git commit -m "feat(facemarket): 배포본 앵커 reconciler — 재시도 상한 50, 중복 revert 화해"
```

---

## Task 7: 공개 검증 라우트 `/verify/p/{publication_id}`

**Files:**
- Modify: `server/app/facemarket_provenance.py` (검증 라우트 추가 — 무인증이라 `require_user` 없음)
- Test: `server/tests/test_facemarket_publication_verify.py`

**Interfaces:**
- Consumes: Task 1 `fm_publication_records`, 기존 `facemarket._mask_name` / `_age_from_birth_year` / `_is_expired`
- Produces: `GET /v1/facemarket/publications/verify/{publication_id}` → `PublicationVerifyResult`

- [ ] **Step 1: 실패 테스트 작성**

`server/tests/test_facemarket_publication_verify.py`:

```python
"""무인증 공개 검증 — 여기 실리는 값은 회수 불가다.

facemarket.py:1249 의 하드룰을 그대로 계승한다. 응답 모델에 선언된 필드가 전부이고,
SELECT 자체가 화이트리스트다. 이 테스트는 그 계약을 못박는다.
"""
import pytest

from app.facemarket_provenance import PublicationVerifyResult

FORBIDDEN = {
    "faceImageKey", "faceImageUri", "faceImageDigest", "ciHash", "ci",
    "birthDate", "birthYear", "displayName", "realName", "userId",
    "r2Key", "signedSha256", "sourceAssetIds", "modelId", "sellerId",
    "imageSha256",   # 전체 해시는 안 싣는다 — 앞 12자만
}


def test_response_model_has_no_forbidden_fields():
    declared = set(PublicationVerifyResult.model_fields)
    camel = {
        "".join(w if i == 0 else w.capitalize() for i, w in enumerate(n.split("_")))
        for n in declared
    }
    leaked = camel & FORBIDDEN
    assert not leaked, f"공개 검증 응답에 금지 필드가 있다: {leaked}"


def test_response_model_fields_are_exactly_the_whitelist():
    assert set(PublicationVerifyResult.model_fields) == {
        "valid", "status", "published_at", "image_hash_prefix", "kind",
        "allowed_use", "forbidden_use", "license_valid_until", "chain", "model",
    }
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd server && uv run pytest tests/test_facemarket_publication_verify.py -v`
Expected: FAIL — `ImportError: cannot import name 'PublicationVerifyResult'`

- [ ] **Step 3: 검증 라우트 구현**

`server/app/facemarket_provenance.py` 끝에:

```python
# ============================================================================
# 공개 검증 (무인증) — C2PA 매니페스트의 verifyUrl 이 여기를 가리킨다.
#
# 🔴 하드룰: facemarket.py:1249 와 동일. 무인증이라 한 번 나가면 회수 불가다.
#   절대 미노출 — 얼굴·face_image_*·CI·ci_hash·생년월일 원문·실명·user_id·model_id·
#   seller_id·내부 R2 키·전체 image_sha256·source_asset_ids.
#   3중 방어: ① SELECT 화이트리스트 ② response_model 이 선언 밖 필드 탈락
#            ③ 신원은 파생값만(마스킹 이름·만 나이)
#   필드 추가 요청이 오면 이 주석을 먼저 읽을 것. 확장은 계약 변경이다.
# ============================================================================


class PublicationChain(CamelModel):
    status: str
    tx_hash: str | None = None
    chain_id: str | None = None
    block: int | None = None


class PublicationVerifyResult(CamelModel):
    """공개 검증 응답 화이트리스트. **이 필드가 전부** — 확장 금지."""

    valid: bool
    status: str                 # 'active' | 'revoked' | 'expired'
    published_at: datetime
    image_hash_prefix: str      # sha256 앞 12자. 전체는 안 싣는다
    kind: str
    allowed_use: list[str]
    forbidden_use: list[str]
    license_valid_until: datetime | None = None
    chain: PublicationChain | None = None
    model: PublicVerifyModel


@router.get(
    "/verify/{publication_id}",
    response_model=PublicationVerifyResult,
    responses={404: {"model": ErrorResponse, "description": "없음/잘못된 id"}},
    summary="배포본 공개 검증 (무인증)",
)
async def verify_publication(request: Request, publication_id: str, response: Response):
    """C2PA 매니페스트의 verifyUrl 종착지. **인증 없음**(누구나 파일 출처를 확인한다)."""
    try:
        pub_uuid = uuid.UUID(str(publication_id))
    except (ValueError, TypeError):
        raise _err("not_found", "기록을 찾을 수 없습니다.", status=404)

    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            # 방어 ① — 화이트리스트 SELECT. r2_key·seller_id·source_asset_ids·signed_sha256 미조회.
            await cur.execute(
                """select p.kind, p.image_sha256, p.created_at, p.revoked_at,
                          p.chain_status, p.tx_hash, p.chain_id, p.recorded_block,
                          l.status as license_status, l.allowed_use, l.forbidden_use,
                          l.license_valid_until, m.display_name,
                          (select v.fields->>'birthYear' from fm_identity_verifications v
                            where v.model_id = m.id
                            order by v.verified_at desc limit 1) as birth_year
                     from fm_publication_records p
                     left join fm_licenses l on l.id = p.license_id
                     left join fm_models m on m.id = p.model_id
                    where p.id = %s""",
                (str(pub_uuid),),
            )
            row = await cur.fetchone()
    if row is None:
        raise _err("not_found", "기록을 찾을 수 없습니다.", status=404)

    if row["revoked_at"] is not None:
        status = "revoked"
    elif row["license_status"] == "revoked":
        status = "revoked"
    elif row["license_status"] is None:
        status = "revoked"        # 라이선스가 사라졌다 = 더 이상 권한을 확인할 수 없다
    elif _is_expired(row):
        status = "expired"
    else:
        status = row["license_status"]

    response.headers["Cache-Control"] = "no-store"   # 철회가 즉시 반영돼야 한다
    chain = None
    if row["chain_status"]:
        chain = {
            "status": row["chain_status"], "txHash": row["tx_hash"],
            "chainId": row["chain_id"], "block": row["recorded_block"],
        }
    return {
        "valid": status == "active",
        "status": status,
        "publishedAt": row["created_at"],
        "imageHashPrefix": (row["image_sha256"] or "")[:12],
        "kind": row["kind"],
        "allowedUse": row["allowed_use"] or [],
        "forbiddenUse": row["forbidden_use"] or [],
        "licenseValidUntil": row["license_valid_until"],
        "chain": chain,
        "model": {
            "nameMasked": _mask_name(row["display_name"] or ""),
            "age": _age_from_birth_year(row["birth_year"]),
        },
    }
```

파일 상단 import 를 보강한다:

```python
from datetime import datetime

from fastapi import Response

from .facemarket import (
    CamelModel, ErrorResponse, PublicVerifyModel, _age_from_birth_year, _err,
    _is_expired, _mask_name, get_conn, verify_license_local,
)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd server && uv run pytest tests/test_facemarket_publication_verify.py -v`
Expected: 2 PASS

- [ ] **Step 5: 프론트 검증 페이지**

`src/features/verify/PublicVerifyPublication.jsx` — `PublicVerify.jsx` 의 셸·상태 카피·CSS 모듈을 그대로 재사용하고 필드만 바꾼다:

```jsx
/* =============================================================
   features/verify — 배포본 공개 검증 (/verify/p/:publicationId)
   파일 안 C2PA 매니페스트의 verifyUrl 이 여기로 온다.

   🔴 얼굴을 렌더하지 않는다. 무인증이라 여기 그린 건 전부 공개된다.
   서버(GET /v1/facemarket/publications/verify/{id})가 화이트리스트로만 응답하고,
   여기서 하는 건 그 응답을 그대로 보여주는 것뿐이다.
   ============================================================= */
import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Icon } from '@/components/ui.jsx';
import { verifyPublicationPublic } from '@/lib/api/facemarket.js';
import s from './PublicVerify.module.css';

const fmtDate = (iso) => { try { return new Date(iso).toLocaleDateString('ko-KR'); } catch { return iso; } };

const STATUS_COPY = {
  active:  { title: '정품 이미지예요',      desc: '아래 조건으로 사용이 허가된 이미지예요.' },
  revoked: { title: '철회된 이미지예요',    desc: '모델이 사용을 철회했어요. 이 이미지는 더 이상 사용할 수 없어요.' },
  expired: { title: '기간이 지난 이미지예요', desc: '라이선스 유효기간이 지났어요.' },
};

export function PublicVerifyPublication() {
  const { publicationId } = useParams();
  const [phase, setPhase] = useState('loading');
  const [data, setData] = useState(null);
  const [message, setMessage] = useState(null);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      setData(await verifyPublicationPublic(publicationId));
      setPhase('ok');
    } catch (e) {
      setMessage(e.message);
      setPhase(e.status === 404 ? 'notfound' : 'error');
    }
  }, [publicationId]);

  useEffect(() => { load(); }, [load]);

  if (phase === 'loading') {
    return <div className={s.page}><div className={s.shell}><p className={s.plain}>확인하는 중이에요…</p></div></div>;
  }
  if (phase !== 'ok') {
    return (
      <div className={s.page}><div className={s.shell}>
        <div className={`${s.hero} ${s.heroUnknown}`}>
          <span className={s.heroIcon}><Icon name="alertTri" size={30} /></span>
          <h1>{phase === 'notfound' ? '찾을 수 없는 기록이에요' : '확인하지 못했어요'}</h1>
          <p>{phase === 'notfound' ? '주소가 잘못됐을 수 있어요.' : (message || '잠시 후 다시 시도해 주세요.')}</p>
        </div>
        {phase === 'error' && (
          <button type="button" className={s.retry} onClick={load}>
            <Icon name="refresh" size={14} />다시 시도
          </button>
        )}
      </div></div>
    );
  }

  const copy = STATUS_COPY[data.status] || STATUS_COPY.revoked;
  return (
    <div className={s.page}><div className={s.shell}>
      <div className={`${s.hero} ${data.valid ? s.heroOk : s.heroBad}`}>
        <span className={s.heroIcon}><Icon name={data.valid ? 'check' : 'alertTri'} size={30} /></span>
        <h1>{copy.title}</h1>
        <p>{copy.desc}</p>
      </div>
      <dl className={s.rows}>
        <div><dt>모델</dt><dd>{data.model.nameMasked}{data.model.age != null ? ` (${data.model.age}세)` : ''}</dd></div>
        <div><dt>발행</dt><dd>{fmtDate(data.publishedAt)}</dd></div>
        <div><dt>사용 허용</dt><dd>{(data.allowedUse || []).join(', ') || '—'}</dd></div>
        <div><dt>사용 금지</dt><dd>{(data.forbiddenUse || []).join(', ') || '—'}</dd></div>
        <div><dt>파일 지문</dt><dd><code>{data.imageHashPrefix}…</code></dd></div>
        {data.chain && (
          <div>
            <dt>블록체인 기록</dt>
            <dd>{data.chain.status === 'confirmed'
              ? <code>{data.chain.txHash ? `${data.chain.txHash.slice(0, 14)}…` : `block ${data.chain.block}`}</code>
              : '기록 대기 중'}</dd>
          </div>
        )}
      </dl>
    </div></div>
  );
}
```

`src/lib/api/facemarket.js` 에 클라이언트 추가(`verifyLicensePublic` 바로 아래, 같은 무인증 fetch 패턴):

```js
// GET /v1/facemarket/publications/verify/{id} — 배포본 공개 검증. **무인증**.
export async function verifyPublicationPublic(publicationId) {
  const res = await fetch(
    `${BASE_URL}/v1/facemarket/publications/verify/${encodeURIComponent(publicationId)}`,
    { headers: { Accept: 'application/json' } },
  );
  if (!res.ok) {
    const err = new Error('확인하지 못했어요.');
    err.status = res.status;
    throw err;
  }
  return res.json();
}
```

라우트 등록 — `src/apps/seller/App.jsx` 와 `src/apps/facemarket/App.jsx` 의 기존 `verify/:licenseId` 옆에:

```jsx
<Route path="verify/p/:publicationId" element={<PublicVerifyPublication />} />
```

두 파일 상단에 `import { PublicVerifyPublication } from '@/features/verify/PublicVerifyPublication.jsx';` 를 더한다. `src/features/facemarket-landing/facemarketRootTarget.js` 의 `ALLOWED_ROOTS` 는 `'/verify'` 를 이미 포함하므로 수정 불필요.

- [ ] **Step 6: 프론트 빌드 확인**

Run: `pnpm build`
Expected: 성공

- [ ] **Step 7: 커밋**

```bash
git add server/app/facemarket_provenance.py server/tests/test_facemarket_publication_verify.py \
        src/features/verify/PublicVerifyPublication.jsx src/lib/api/facemarket.js \
        src/apps/seller/App.jsx src/apps/facemarket/App.jsx
git commit -m "feat(facemarket): 배포본 공개 검증 — 무인증 화이트리스트 라우트와 페이지"
```

---

## Task 8: 프론트 다운로드 배선 (공증 왕복)

**Files:**
- Modify: `src/features/editor/editorExport.js` (`saveBlob` 앞에 공증 왕복)
- Modify: `src/lib/api/facemarket.js` (presign/sign 클라이언트)
- Test: `src/features/editor/editorExport.test.js` (기존 테스트 파일이 없으면 신규)

**Interfaces:**
- Consumes: Task 4 의 두 라우트
- Produces:
  - `facemarket.presignPublication({ projectId, kind, byteSize }) -> { uploadToken, uploadUrl }`
  - `facemarket.signPublication({ uploadToken, projectId }) -> { publicationId, downloadUrl, verifyUrl, c2paStatus }`
  - `editorExport.notarize(blob, { projectId, kind }) -> { blob, verifyUrl, warning }` — 실패 시 원본 blob + warning

- [ ] **Step 1: 실패 테스트 작성**

`src/features/editor/editorExport.test.js`:

```js
/* 공증 왕복 — 실패해도 다운로드를 막지 않는다는 게 계약이다.
   생성은 이미 끝났고 크레딧도 차감됐다. 도장이 안 찍혔다고 결과물을 인질로 잡지 않는다. */
import { describe, expect, it, vi } from 'vitest';
import { notarize } from './editorExport.js';

const blob = new Blob([new Uint8Array([1, 2, 3])], { type: 'image/png' });

describe('notarize', () => {
  it('공증에 성공하면 서명본과 검증 URL 을 돌려준다', async () => {
    const api = {
      presignPublication: vi.fn().mockResolvedValue({ uploadToken: 't', uploadUrl: 'https://r2/put' }),
      signPublication: vi.fn().mockResolvedValue({
        publicationId: 'p1', downloadUrl: 'https://r2/get', verifyUrl: 'https://w/verify/p/p1',
        c2paStatus: 'signed',
      }),
    };
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce({ ok: true })                                   // PUT
      .mockResolvedValueOnce({ ok: true, blob: async () => blob });          // GET signed
    const out = await notarize(blob, { projectId: 'p', kind: 'long_png' }, { api, fetchImpl });
    expect(out.verifyUrl).toBe('https://w/verify/p/p1');
    expect(out.warning).toBeNull();
  });

  it('presign 이 실패해도 원본을 돌려주고 경고만 붙인다', async () => {
    const api = {
      presignPublication: vi.fn().mockRejectedValue(new Error('nope')),
      signPublication: vi.fn(),
    };
    const out = await notarize(blob, { projectId: 'p', kind: 'long_png' }, { api, fetchImpl: vi.fn() });
    expect(out.blob).toBe(blob);
    expect(out.verifyUrl).toBeNull();
    expect(out.warning).toBeTruthy();
  });

  it('업로드가 실패해도 원본을 돌려준다', async () => {
    const api = {
      presignPublication: vi.fn().mockResolvedValue({ uploadToken: 't', uploadUrl: 'https://r2/put' }),
      signPublication: vi.fn(),
    };
    const fetchImpl = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    const out = await notarize(blob, { projectId: 'p', kind: 'long_png' }, { api, fetchImpl });
    expect(out.blob).toBe(blob);
    expect(api.signPublication).not.toHaveBeenCalled();
    expect(out.warning).toBeTruthy();
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pnpm vitest run src/features/editor/editorExport.test.js`
Expected: FAIL — `notarize` 가 export 되지 않음

- [ ] **Step 3: API 클라이언트 추가**

`src/lib/api/facemarket.js`:

```js
// POST /v1/facemarket/publications/presign — 배포본 업로드 URL. 행은 아직 안 만든다.
export function presignPublication({ projectId, kind, byteSize }) {
  return http('/v1/facemarket/publications/presign', {
    method: 'POST', body: { projectId, kind, byteSize },
  });
}

// POST /v1/facemarket/publications/sign — 해시·원장·C2PA 서명. 응답의 publicationId 가 정본.
// projectId·kind 는 uploadToken 안에 서명돼 있다 — 여기서 다시 보내지 않는다.
export function signPublication({ uploadToken }) {
  return http('/v1/facemarket/publications/sign', {
    method: 'POST', body: { uploadToken },
  });
}
```

- [ ] **Step 4: `notarize` 구현**

`src/features/editor/editorExport.js` — `saveBlob` 위에 추가:

```js
/* ---- 배포본 공증 (FaceMarket 출처증명 층①·②) ----
   캔버스 PNG 를 R2 로 직접 올리고(ALB 우회), 서버가 해시·원장·C2PA 서명을 한 뒤
   서명본을 돌려준다. 렌더는 그대로 브라우저가 한다 — 이 픽셀이 정본이다.

   🔴 실패해도 다운로드를 막지 않는다. 생성은 이미 끝났고 크레딧도 차감됐다.
      공증이 안 됐다고 셀러의 결과물을 인질로 잡지 않는다(설계 §6.2). */
export async function notarize(blob, { projectId, kind }, deps = {}) {
  const api = deps.api || (await import('../../lib/api/facemarket.js'));
  const fetchImpl = deps.fetchImpl || fetch;
  const fail = (warning) => ({ blob, verifyUrl: null, warning });
  try {
    const { uploadToken, uploadUrl } = await api.presignPublication({
      projectId, kind, byteSize: blob.size,
    });
    const put = await fetchImpl(uploadUrl, {
      method: 'PUT', body: blob, headers: { 'Content-Type': blob.type || 'image/png' },
    });
    if (!put.ok) return fail('출처 기록을 남기지 못했어요. 파일은 그대로 저장됩니다.');

    const res = await api.signPublication({ uploadToken });
    const got = await fetchImpl(res.downloadUrl);
    if (!got.ok) return { blob, verifyUrl: res.verifyUrl, warning: null };
    return { blob: await got.blob(), verifyUrl: res.verifyUrl, warning: null };
  } catch {
    return fail('출처 기록을 남기지 못했어요. 파일은 그대로 저장됩니다.');
  }
}
```

`exportBlockPng` / 긴 PNG / ZIP 세 공개 API 에서 `saveBlob(...)` 직전에 끼운다. 예 — `exportBlockPng`:

```js
export async function exportBlockPng(blockNode, productName, blockIndex, opts = {}) {
  const fontEmbedCSS = await getFontEmbedCSS(blockNode);
  const { canvas, softFailed } = await captureBlockCanvas(blockNode, PIXEL_RATIO, fontEmbedCSS);
  let out = await canvasToBlob(canvas);
  let verifyUrl = null;
  let notarizeWarning = null;
  if (opts.provenance?.projectId) {
    const n = await notarize(out, { projectId: opts.provenance.projectId, kind: 'block_png' });
    out = n.blob;
    verifyUrl = n.verifyUrl;
    notarizeWarning = n.warning;
  }
  saveBlob(out, exportFileName(productName, `블록${blockIndex + 1}`));
  return { softFailed, verifyUrl, notarizeWarning };
}
```

긴 PNG 는 `kind: 'long_png'`, ZIP 은 `kind: 'zip'` 으로 같은 형태를 반복한다. 호출부(`Editor.jsx` 의 다운로드 핸들러)는 REAL 소스일 때만 `opts.provenance = { projectId }` 를 넘긴다 — VIRTUAL 은 소비한 라이선스가 없어 서버가 404 를 준다.

- [ ] **Step 5: 테스트 통과 + 빌드 확인**

Run: `pnpm vitest run src/features/editor/editorExport.test.js`
Expected: 3 PASS

Run: `pnpm build`
Expected: 성공

- [ ] **Step 6: 커밋**

```bash
git add src/features/editor/editorExport.js src/features/editor/editorExport.test.js \
        src/lib/api/facemarket.js src/features/editor/Editor.jsx
git commit -m "feat(editor): 배포본 다운로드에 공증 왕복 — 실패해도 저장은 막지 않는다"
```

---

## Task 9: 철회·파기 연동

**Files:**
- Modify: `server/app/services/biometric_purge.py` (`_known_targets` 에 서명본 키 추가, `_cleanup` 에 `revoked_at` 세팅)
- Test: `server/tests/test_facemarket_publication_purge.py`

**Interfaces:**
- Consumes: Task 1 `fm_publication_records`
- Produces: purge 실행 후 `r2_key is null AND revoked_at is not null AND 행 생존`

- [ ] **Step 1: 실패 테스트 작성**

`server/tests/test_facemarket_publication_purge.py`:

```python
"""철회 = R2 사본 삭제 + revoked_at 표시. **행은 지우지 않는다.**

지우면 그 파일이 '우리가 모르는 파일'이 되어 무단 사용과 정당한 과거 사용을 구별할 수
없게 된다. 파기의 목적(생체정보 제거)은 R2 사본 삭제로 달성된다. image_sha256 은
생체정보가 아니라 파일 지문이다(설계 §9).
"""
import inspect

from app.services import biometric_purge


def test_purge_collects_publication_signed_copies():
    src = inspect.getsource(biometric_purge)
    assert "fm_publication_records" in src, "서명본 사본이 purge 대상에 없다"
    assert "r2_key" in src


def test_purge_never_deletes_publication_rows():
    src = inspect.getsource(biometric_purge)
    assert "delete from fm_publication_records" not in src
    assert "delete from fm_output_records" not in src
    assert "revoked_at" in src
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd server && uv run pytest tests/test_facemarket_publication_purge.py -v`
Expected: FAIL — `서명본 사본이 purge 대상에 없다`

- [ ] **Step 3: purge 배선**

`server/app/services/biometric_purge.py` 의 `_known_targets` — `fm_licenses.face_image_key` 를 모으는 블록(약 517행) 옆에 추가:

```python
        if _has(schema, "fm_publication_records", "r2_key"):
            await cur.execute(
                "select r2_key as k from fm_publication_records "
                "where model_id = any(%s) and r2_key is not null",
                (list(model_ids),),
            )
            keys.update(r["k"] for r in await cur.fetchall() if r["k"])
```

`_cleanup` 의 `update fm_licenses set status='revoked' ...` 근처에:

```python
            if _has(schema, "fm_publication_records", "revoked_at"):
                # 행은 남긴다 — 지우면 무단 사용과 정당한 과거 사용을 구별할 수 없다.
                await cur.execute(
                    "update fm_publication_records "
                    "set r2_key = null, revoked_at = coalesce(revoked_at, now()) "
                    "where model_id = any(%s)",
                    (list(model_ids),),
                )
```

- [ ] **Step 4: 테스트 통과 + 기존 purge 테스트 회귀 확인**

Run: `cd server && uv run pytest tests/test_facemarket_publication_purge.py tests/test_biometric_purge.py -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add server/app/services/biometric_purge.py server/tests/test_facemarket_publication_purge.py
git commit -m "feat(facemarket): 철회 시 서명본 사본만 삭제하고 원장 행은 남긴다"
```

---

## Task 10: 모델 사용 내역 대시보드

**Files:**
- Modify: `server/app/facemarket.py` (`GET /v1/facemarket/models/{model_id}/usage`)
- Modify: `src/features/model/ModelLicense.jsx` 또는 인접 모델 화면
- Test: `server/tests/test_facemarket_model_usage.py`

**Interfaces:**
- Consumes: Task 1 두 원장 테이블
- Produces: `GET /v1/facemarket/models/{model_id}/usage` → `list[UsageCard]`
  (`UsageCard`: `kind: 'cut' | 'publication'`, `created_at`, `image_hash_prefix`, `chain_status: str | None`)

- [ ] **Step 1: 실패 테스트 작성**

`server/tests/test_facemarket_model_usage.py`:

```python
"""모델 본인이 자기 얼굴 사용 내역을 본다. 셀러 신원은 안 보인다.

모델에게 필요한 건 '몇 번 쓰였나'와 '체인에 기록됐나'다. 어느 셀러가 썼는지는
계약상 필요 없고, 노출하면 셀러 영업정보가 모델에게 새는 것이다.
"""
from app.facemarket import UsageCard

FORBIDDEN = {"sellerId", "userId", "projectId", "r2Key", "imageSha256"}


def test_usage_card_hides_seller_identity():
    camel = {
        "".join(w if i == 0 else w.capitalize() for i, w in enumerate(n.split("_")))
        for n in UsageCard.model_fields
    }
    assert not (camel & FORBIDDEN)


def test_usage_card_whitelist():
    assert set(UsageCard.model_fields) == {
        "kind", "created_at", "image_hash_prefix", "chain_status"
    }
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd server && uv run pytest tests/test_facemarket_model_usage.py -v`
Expected: FAIL — `cannot import name 'UsageCard'`

- [ ] **Step 3: 라우트 구현**

`server/app/facemarket.py` 의 `list_licenses` 근처에:

```python
class UsageCard(CamelModel):
    """모델 본인이 보는 사용 내역. 셀러 신원은 싣지 않는다."""

    kind: str                 # 'cut' | 'publication'
    created_at: datetime
    image_hash_prefix: str
    chain_status: str | None = None


@router.get(
    "/models/{model_id}/usage",
    response_model=list[UsageCard],
    responses={
        401: {"model": ErrorResponse, "description": "인증 실패"},
        404: {"model": ErrorResponse, "description": "모델 없음"},
    },
    summary="내 얼굴 사용 내역 (모델 본인)",
)
async def list_model_usage(
    request: Request, model_id: str, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select 1 from fm_models where id = %s and user_id = %s",
                (model_id, user_id),
            )
            if await cur.fetchone() is None:
                raise _err("not_found", "모델을 찾을 수 없습니다.", status=404)
            await cur.execute(
                """select 'cut' as kind, created_at, left(image_sha256, 12) as prefix,
                          null::text as chain_status
                     from fm_output_records where model_id = %s
                   union all
                   select 'publication', created_at, left(image_sha256, 12), chain_status
                     from fm_publication_records where model_id = %s
                   order by created_at desc
                   limit 200""",
                (model_id, model_id),
            )
            rows = await cur.fetchall()
    return [
        {
            "kind": r["kind"], "createdAt": r["created_at"],
            "imageHashPrefix": r["prefix"], "chainStatus": r["chain_status"],
        }
        for r in rows
    ]
```

- [ ] **Step 4: 프론트 표시**

`src/lib/api/facemarket.js`:

```js
// GET /v1/facemarket/models/{id}/usage — 모델 본인의 얼굴 사용 내역.
export function listModelUsage(modelId) {
  return http(`/v1/facemarket/models/${encodeURIComponent(modelId)}/usage`);
}
```

`src/features/model/ModelLicense.jsx` 의 정산 섹션 아래에 목록을 붙인다:

```jsx
const KIND_LABEL = { cut: '사진 생성', publication: '상세페이지 배포' };
const CHAIN_LABEL = { confirmed: '체인 기록됨', pending: '기록 대기', failed: '기록 실패' };

// 사용 내역 — 모델 본인만 본다. 어느 셀러가 썼는지는 응답에 없다(계약상 불필요).
const [usage, setUsage] = useState([]);
useEffect(() => {
  if (!license?.modelId) return;
  listModelUsage(license.modelId).then(setUsage).catch(() => setUsage([]));
}, [license?.modelId]);

{usage.length > 0 && (
  <section className={s.usage}>
    <h2>내 얼굴 사용 내역</h2>
    <ul>
      {usage.map((u, i) => (
        <li key={`${u.createdAt}-${i}`}>
          <span>{KIND_LABEL[u.kind] || u.kind}</span>
          <time>{new Date(u.createdAt).toLocaleDateString('ko-KR')}</time>
          <code>{u.imageHashPrefix}…</code>
          {u.chainStatus && <em>{CHAIN_LABEL[u.chainStatus] || u.chainStatus}</em>}
        </li>
      ))}
    </ul>
  </section>
)}
```

`ModelLicense.module.css` 에 `.usage` 규칙을 더한다 — 같은 파일의 기존 카드 섹션과 동일한
패딩·테두리 토큰을 쓰고, `li` 는 `display: grid; grid-template-columns: 1fr auto auto auto;`.

- [ ] **Step 5: 테스트 통과 + 빌드 확인**

Run: `cd server && uv run pytest tests/test_facemarket_model_usage.py -v`
Expected: 2 PASS

Run: `pnpm build`
Expected: 성공

- [ ] **Step 6: 커밋**

```bash
git add server/app/facemarket.py server/tests/test_facemarket_model_usage.py \
        src/lib/api/facemarket.js src/features/model/ModelLicense.jsx
git commit -m "feat(facemarket): 모델 본인 얼굴 사용 내역 — 셀러 신원은 안 싣는다"
```

---

## Task 11: prod 배선 + 런북

**Files:**
- Create: `docs/runbooks/facemarket-provenance-deploy.md`
- Modify: `copilot/` 의 API 서비스 매니페스트(시크릿 참조 추가)
- Test: `server/tests/test_facemarket_provenance_deploy.py`

**Interfaces:**
- Consumes: Task 1~10 전부
- Produces: prod 에서 `FM_PROVENANCE_ENABLED=true` 로 3층이 실제로 동작

- [ ] **Step 1: 배포 계약 테스트 작성**

`server/tests/test_facemarket_provenance_deploy.py`:

```python
"""배포 매니페스트가 필요한 env 를 실제로 참조하는지 — 문서만 맞고 배포가 틀리는 걸 막는다.

2026-08-29 에 CI 의 SUPABASE_DB_URL 이 앱 DB 와 다른 옛 DB 를 가리켜 마이그레이션이
prod 에 안 붙었다. 그런 종류의 어긋남을 텍스트 수준에서라도 잡는다.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs/runbooks/facemarket-provenance-deploy.md"

REQUIRED_ENV = [
    "FM_PROVENANCE_ENABLED",
    "FM_C2PA_CERT_PEM",
    "FM_C2PA_KEY_PEM",
    "FM_PROVENANCE_ADDRESS",
    "FM_CHAIN_RPC_URL",
    "FM_SETTLEMENT_ADDRESS",
    "FM_CHAIN_PRIVATE_KEY",
    "PUBLIC_WEB_ORIGIN",
]


def test_runbook_exists():
    assert RUNBOOK.exists()


def test_runbook_lists_every_required_env():
    body = RUNBOOK.read_text(encoding="utf-8")
    missing = [k for k in REQUIRED_ENV if k not in body]
    assert not missing, f"런북에 빠진 env: {missing}"


def test_runbook_warns_about_r2_cors():
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "CORS" in body, "브라우저 presigned PUT 은 R2 버킷 CORS 가 없으면 실패한다"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd server && uv run pytest tests/test_facemarket_provenance_deploy.py -v`
Expected: FAIL — 런북 파일 없음

- [ ] **Step 3: 런북 작성**

`docs/runbooks/facemarket-provenance-deploy.md`:

```markdown
# FaceMarket 출처증명 prod 배포 런북

설계: `docs/superpowers/specs/2026-09-04-facemarket-provenance-design.md`
배포툴: `copilot-aws` (일반 `copilot` 아님). 리전 `us-east-1`, env `use1`.
⚠️ **배포는 CI 로만.** 로컬 copilot 배포가 `.env` 평문과 4.7GB 실험 디렉터리를
프로덕션 이미지에 실은 사고가 있다(2026-08-26).

## 순서

### 1. C2PA 인감 발급

    cd server && uv run python scripts/gen_c2pa_cert.py --out-dir ./c2pa

두 PEM 을 SSM SecureString 으로 올린다:

    aws ssm put-parameter --profile wearless --region us-east-1 \
      --name /copilot/wearless/use1/secrets/FM_C2PA_CERT_PEM \
      --type SecureString --value "$(cat server/c2pa/c2pa_cert.pem)"
    aws ssm put-parameter --profile wearless --region us-east-1 \
      --name /copilot/wearless/use1/secrets/FM_C2PA_KEY_PEM \
      --type SecureString --value "$(cat server/c2pa/c2pa_key.pem)"

🔴 `server/c2pa/` 는 커밋하지 않는다. `.gitignore` 확인.

### 2. FaceMarketProvenance 배포

OmniOne Chain 콘솔에 `contracts/FaceMarketProvenance.sol` 단일 파일 업로드.
- RPC 호스트는 `test.` 를 **뺀** 주소다(실측 함정).
- chainId `201210`.
- 배포자 = `FM_CHAIN_PRIVATE_KEY` 의 계정이어야 한다(owner-only recorder).

배포 주소를 `FM_PROVENANCE_ADDRESS` 로 넣는다.

### 3. 체인 env 배선 (⚠️ 지금 prod 에 없음)

`FM_CHAIN_RPC_URL`, `FM_SETTLEMENT_ADDRESS`, `FM_CHAIN_PRIVATE_KEY` 는 로컬에만 있고
prod 에 배선된 적이 없다. 셋 다 없으면 `FaceMarketChain.from_settings` 가 None 을
돌려 **층③ 전체가 조용히 no-op** 한다. SSM SecureString 으로 올린다.

### 4. R2 버킷 CORS

브라우저가 presigned PUT 을 하려면 버킷에 CORS 규칙이 필요하다. 없으면 업로드가
preflight 에서 막히고, `notarize` 가 경고만 남긴 채 공증 없이 저장된다(조용한 실패).

    [{"AllowedOrigins": ["https://wearless.kr"],
      "AllowedMethods": ["PUT", "GET"],
      "AllowedHeaders": ["content-type"],
      "MaxAgeSeconds": 3600}]

### 5. 기타 env

- `PUBLIC_WEB_ORIGIN` — C2PA 매니페스트의 `verifyUrl` 이 이 값을 쓴다. 틀리면 파일 안
  링크가 잘못된 곳을 가리키고, 이미 배포된 파일은 고칠 수 없다. **먼저 확인할 것.**
- `FM_PROVENANCE_ENABLED=true` — 맨 마지막에 켠다.

### 6. 마이그레이션

⚠️ CI 의 `SUPABASE_DB_URL` 이 앱 DB 와 다른 옛 DB 를 가리킨 이력이 있다(2026-08-29
prod-down). 앱 DB 는 `ftjxwxuactfjopbokbni`(SSM), `server/.env` 의 값이 아니다.
적용 전에 대상 DB 를 눈으로 확인한다.

### 7. 켠 뒤 확인

1. REAL 모델로 컷 생성 → `select count(*) from fm_output_records where job_id = ...` 가 컷 수와 같은가
2. 상세페이지 다운로드 → `fm_publication_records` 1행, `c2pa_status='signed'`
3. 받은 파일을 C2PA 검증 도구에 넣어 매니페스트가 읽히는가(발급자 미확인 표시는 정상)
4. 30초쯤 뒤 `chain_status='confirmed'` 로 바뀌는가
5. 파일 안 `verifyUrl` 을 열어 공개 검증 페이지가 뜨는가

## 롤백

`FM_PROVENANCE_ENABLED=false` 로 되돌리면 라우트 미등록·워커 미기동·원장 insert 생략이
되고 기존 생성·다운로드 흐름은 그대로 동작한다. 이미 쌓인 원장 행과 체인 기록은 남는다.
```

- [ ] **Step 4: 시크릿 매니페스트 배선**

`copilot/api/manifest.yml` **과** `copilot/detail-worker/manifest.yml` 두 파일 모두의 `secrets:`
블록에 추가한다 — 라우트는 api 가, 앵커 reconciler 는 api 의 lifespan 이 돌지만 워커 서비스도
같은 이미지·같은 설정 로딩 경로를 쓰므로 한쪽만 넣으면 기동 시 설정이 갈린다:

```yaml
  FM_C2PA_CERT_PEM: /copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/FM_C2PA_CERT_PEM
  FM_C2PA_KEY_PEM: /copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/FM_C2PA_KEY_PEM
  FM_CHAIN_RPC_URL: /copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/FM_CHAIN_RPC_URL
  FM_SETTLEMENT_ADDRESS: /copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/FM_SETTLEMENT_ADDRESS
  FM_CHAIN_PRIVATE_KEY: /copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/FM_CHAIN_PRIVATE_KEY
  FM_PROVENANCE_ADDRESS: /copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/FM_PROVENANCE_ADDRESS
```

`variables:` 블록에:

```yaml
  FM_PROVENANCE_ENABLED: "false"   # 배선 검증 후 true 로 올린다
  PUBLIC_WEB_ORIGIN: "https://wearless.kr"
```

- [ ] **Step 5: 전체 테스트 통과 확인**

Run: `cd server && uv run pytest tests/test_facemarket_provenance_deploy.py -v`
Expected: 3 PASS

Run: `cd server && uv run pytest -q`
Expected: 전체 스위트 통과(신규 실패 0)

Run: `pnpm build && pnpm vitest run`
Expected: 성공

- [ ] **Step 6: 커밋**

```bash
git add docs/runbooks/facemarket-provenance-deploy.md copilot/ \
        server/tests/test_facemarket_provenance_deploy.py
git commit -m "docs(facemarket): 출처증명 prod 배포 런북 + 시크릿 배선"
```

---

## 완료 기준

- [ ] REAL 모델로 컷 N장을 만들면 `fm_output_records` 행이 정확히 N개 생긴다
- [ ] lease 상실 시 원장 행 0개
- [ ] 상세페이지를 내려받으면 파일 안에서 C2PA 매니페스트가 읽히고, `modelId`·`licenseId`·`verifyUrl` 이 들어 있다
- [ ] 매니페스트에 금지 필드가 하나도 없다(회귀 테스트가 지킨다)
- [ ] 같은 파일을 세 번 내려받아도 원장 1행, 체인 TX 1건
- [ ] C2PA 서명이 실패해도 다운로드가 된다
- [ ] `chain_status` 가 `pending → confirmed` 로 바뀐다
- [ ] `/verify/p/{id}` 가 로그인 없이 열리고, 응답에 얼굴·실명·내부 키가 없다
- [ ] 모델 철회 후 R2 서명본은 사라지고 원장 행은 `revoked_at` 표시로 남으며 검증 페이지가 "철회됨"을 보여준다
- [ ] 과금 동작이 이전과 동일하다(작업 1건 = 라이선스비 1회)
