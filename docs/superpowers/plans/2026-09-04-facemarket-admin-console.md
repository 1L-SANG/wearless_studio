# FaceMarket 관리자 콘솔 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `admin.wearless.kr` 콘솔에 감사 원장·관리자 승격 UI·대시보드·모델 조회를 더하고, UI 를 shadcn/ui 로 통일한다.

**Architecture:** 인증 스택은 그대로(`profiles.role`). 관리자의 모든 쓰기가 `admin_audit_log` 에 남고, 권한 판정은 `server/app/admin_guard.py` 한 곳이 한다. 집계는 롤업 없이 라이브 SQL + 30초 프로세스 캐시. Tailwind/shadcn 은 admin 진입 번들에만 들어가고 CSS 레이어로 기존 스타일과 충돌을 막는다.

**Tech Stack:** FastAPI(psycopg3, async) · Postgres(Supabase) · React 18 + Vite 6 (JSX, 다중 진입) · Tailwind v4 + shadcn/ui · pytest · node:test

**Spec:** `docs/superpowers/specs/2026-09-04-facemarket-admin-console-design.md`

## Global Constraints

- **워크트리:** `../wearless_studio-admin-console`, 브랜치 `feat/admin-console` (origin/main 기준). 메인 트리에서 작업 금지.
- **백엔드 테스트:** `cd server && uv run pytest -q`. 새 테스트만 돌릴 땐 `uv run pytest tests/test_x.py -q`.
- **프런트 테스트:** 저장소 루트에서 `pnpm test:frontend` (= `node --test tests/frontend/*.test.mjs`). **vitest 는 이 레포에 없다.** 프런트 테스트는 소스 텍스트 계약 검사다.
- **빌드 확인:** `pnpm build` (rollup 진입 3개: seller·facemarket·admin).
- **마이그레이션 파일명:** `supabase/migrations/YYYYMMDDHHMMSS_<슬러그>.sql`. 기존 마지막은 `20260904000000_facemarket_provenance.sql` 이므로 이 계획의 마이그레이션은 그보다 뒤 타임스탬프를 쓴다.
- **마이그레이션 테스트는 SQL 텍스트 계약 검사**다(실 DB 없이). 선례: `server/tests/test_facemarket_applications_migration.py`.
- **에러 봉투:** `HTTPException(status_code=..., detail={"code": ..., "message": ...})`. 사용자 메시지는 한국어.
- **관리자 403 문구는 한 곳에서만 나온다:** `admin_guard.forbidden()` — `{"code": "forbidden", "message": "관리자만 가능해요."}`.
- **금액 단위:** 원(₩) 정수. `fm_settlements.total_amount` 는 bigint, `payment_history.amount` 는 integer.
- **집계에서 항상 제외:** 시뮬 정산(`fm_settlements.payment_id like 'sim:%'`), 테스트 결제(`payment_history.provider = 'test'`).
- **날짜 경계:** KST(`Asia/Seoul`). SQL 은 `(created_at at time zone 'Asia/Seoul')::date`.
- **커밋 메시지:** 한국어 본문, Conventional Commits 접두. 끝에 `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- **`verified` 배지는 콘솔이 새로 만들지 못한다.** 정지 해제는 감사 원장에 기록된 정지 직전 상태로만 복원한다.

---

## File Structure

**백엔드 (생성)**
- `server/app/admin_guard.py` — 권한 게이트 + 감사 기록 헬퍼. 이 파일 밖에서 `repo.is_admin` 을 직접 부르지 않는다.
- `server/app/facemarket_admin.py` — 콘솔 전용 라우터(`/v1/facemarket/admin/overview|models|staff|audit`). 지원서 라우트는 기존 `facemarket_applications.py` 에 그대로 둔다(지원서 도메인과 함께 산다).
- `supabase/migrations/20260904100000_admin_audit_log.sql`

**백엔드 (수정)**
- `server/app/facemarket_applications.py` — `_require_admin` → `admin_guard.require_admin`, 승인·거절·재발송에 감사 기록
- `server/app/routes.py:696,723` — 환불 승인·반려에 가드 통일 + 감사 기록
- `server/app/facemarket.py:1773`, `server/app/facemarket_cutover.py:371` — 가드 통일
- `server/app/main.py` — `facemarket_admin` 라우터 등록

**프런트 (생성)**
- `src/apps/AppProviders.jsx` — 프로바이더·루프백 정규화(스타일 import 없음)
- `src/apps/admin/admin.css` — Tailwind + 레이어 순서 + shadcn 색 변수
- `src/apps/admin/mountAdminApp.jsx` — admin 전용 부트스트랩
- `src/lib/adminCn.js` — `cn()` (clsx + tailwind-merge)
- `src/components/admin-ui/*` — shadcn 컴포넌트 사본
- `src/features/admin/AdminShell.jsx` — 사이드바 셸
- `src/features/admin/AdminDashboard.jsx`, `AdminModels.jsx`, `AdminStaff.jsx`
- `components.json` — shadcn CLI 설정

**프런트 (수정)**
- `src/apps/mountApp.jsx` — 스타일 import 유지 + `AppProviders` 위임
- `src/apps/admin/main.jsx`, `src/apps/admin/App.jsx` — 새 부트스트랩·라우트 4개
- `src/features/admin/AdminApplications.jsx` — 마크업만 shadcn 으로 (동작 불변), `AdminApplications.module.css` 삭제
- `src/lib/api/facemarket.js` — 콘솔 API 함수 추가
- `vite.config.js`, `package.json`

---

## Task 1: 감사 원장 마이그레이션

**Files:**
- Create: `supabase/migrations/20260904100000_admin_audit_log.sql`
- Test: `server/tests/test_admin_audit_migration.py`

**Interfaces:**
- Consumes: 없음
- Produces: 테이블 `admin_audit_log(id, actor_user_id, action, target_type, target_id, before, after, note, created_at)`. Task 2 의 `write_audit` 가 이 컬럼 이름에 의존한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`server/tests/test_admin_audit_migration.py`:

```python
"""admin_audit_log 마이그레이션 구조 검증 — SQL 텍스트 레벨.

원장은 행위자보다 오래 살아야 한다(actor on delete set null). 관리자 계정을 지웠다고
그 관리자가 무엇을 했는지가 사라지면 원장이 아니다.
"""

from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260904100000_admin_audit_log.sql"
)


def _sql() -> str:
    return " ".join(MIGRATION.read_text().split()).lower()


def test_creates_audit_table():
    assert "create table if not exists public.admin_audit_log" in _sql()


def test_actor_survives_account_deletion():
    sql = _sql()
    actor = sql.split("actor_user_id", 1)[1].split(",", 1)[0]
    assert "references auth.users(id) on delete set null" in actor


def test_records_before_and_after_as_jsonb():
    sql = _sql()
    for column in ("before jsonb", "after jsonb"):
        assert column in sql, column


def test_target_id_is_text_not_uuid():
    """대상이 uuid 가 아닌 액션도 있다(환불 요청 id 는 uuid 지만, 앞으로 늘어난다)."""
    assert "target_id text" in _sql()


def test_listing_and_target_indexes_exist():
    sql = _sql()
    assert "admin_audit_log_created_idx" in sql
    assert "admin_audit_log_target_idx" in sql


def test_overview_aggregation_indexes_exist():
    """대시보드 집계가 순차 스캔으로 떨어지지 않게 — 5.3 절."""
    sql = _sql()
    for index in (
        "fm_model_applications (status)",
        "fm_model_applications (created_at)",
        "fm_licenses (created_at)",
        "fm_settlements (chain_status, created_at)",
        "payment_history (status, created_at)",
    ):
        assert index in sql, index
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd server && uv run pytest tests/test_admin_audit_migration.py -q`
Expected: FAIL — `FileNotFoundError` (마이그레이션 파일 없음)

- [ ] **Step 3: 마이그레이션을 쓴다**

`supabase/migrations/20260904100000_admin_audit_log.sql`:

```sql
-- =============================================================
-- 20260904100000_admin_audit_log.sql
-- 관리자 행위 감사 원장 + 대시보드 집계 인덱스
-- 설계: docs/superpowers/specs/2026-09-04-facemarket-admin-console-design.md §4.3·§5.3
--
-- 원장은 행위자보다 오래 산다 — actor 는 on delete set null 이다. 관리자 계정을 지우면
-- 누가 했는지는 잃어도 무엇이 일어났는지는 남아야 한다.
-- before/after 에는 상태 전이·식별자만 넣는다. 지원자 실명·생년월일·사진 키는 넣지 않는다
-- (그 값들은 30일 PII 스윕 대상이고, 원장에 복사하면 스윕을 우회한다).
-- =============================================================

create table if not exists public.admin_audit_log (
  id            uuid primary key default gen_random_uuid(),
  actor_user_id uuid references auth.users(id) on delete set null,
  action        text not null,
  target_type   text not null,
  target_id     text,
  before        jsonb not null default '{}'::jsonb,
  after         jsonb not null default '{}'::jsonb,
  note          text,
  created_at    timestamptz not null default now()
);

create index if not exists admin_audit_log_created_idx
  on public.admin_audit_log (created_at desc);
create index if not exists admin_audit_log_target_idx
  on public.admin_audit_log (target_type, target_id, created_at desc);

-- ---------- 대시보드 집계 인덱스 ----------
-- 전부 count/sum 이라 필터 컬럼만 있으면 된다. 지금 규모에서는 없어도 도는데,
-- 행이 늘었을 때 대시보드 한 번이 테이블 5개를 순차 스캔하는 걸 막는다.
create index if not exists fm_model_applications_status_idx
  on public.fm_model_applications (status);
create index if not exists fm_model_applications_created_idx
  on public.fm_model_applications (created_at);
create index if not exists fm_licenses_created_idx
  on public.fm_licenses (created_at);
create index if not exists fm_settlements_chain_created_idx
  on public.fm_settlements (chain_status, created_at);
create index if not exists payment_history_status_created_idx
  on public.payment_history (status, created_at);
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd server && uv run pytest tests/test_admin_audit_migration.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add supabase/migrations/20260904100000_admin_audit_log.sql server/tests/test_admin_audit_migration.py
git commit -m "$(cat <<'MSG'
feat(admin): 감사 원장 테이블 + 대시보드 집계 인덱스

관리자가 무엇을 했는지가 reviewed_by 한 칸 말고는 남지 않았다. 환불 승인·메일
재발송은 흔적이 아예 없다. actor 는 on delete set null 이다 — 원장이 행위자보다
오래 살아야 한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
)"
```

---

## Task 2: 권한 게이트·감사 기록 헬퍼

**Files:**
- Create: `server/app/admin_guard.py`
- Test: `server/tests/test_admin_guard.py`

**Interfaces:**
- Consumes: Task 1 의 `admin_audit_log` 컬럼 이름
- Produces:
  - `forbidden() -> HTTPException` (403, `{"code": "forbidden", "message": "관리자만 가능해요."}`)
  - `async require_admin(conn, user_id: str) -> None`
  - `async write_audit(conn, *, actor_user_id: str, action: str, target_type: str, target_id: str | None = None, before: dict | None = None, after: dict | None = None, note: str | None = None) -> None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`server/tests/test_admin_guard.py`:

```python
"""관리자 게이트·감사 기록 헬퍼 단위 테스트."""
import asyncio
import contextlib

import pytest
from fastapi import HTTPException

from app import admin_guard


class FakeCursor:
    def __init__(self, store):
        self.store = store

    async def execute(self, sql, params=None):
        self.store.append((" ".join(sql.split()), params))

    async def fetchone(self):
        return None


class FakeConn:
    def __init__(self):
        self.executed = []

    def cursor(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeCursor(self.executed)

        return _cm()


def test_require_admin_raises_403_for_non_admin(monkeypatch):
    async def is_admin(_conn, _user_id):
        return False

    monkeypatch.setattr(admin_guard.repo, "is_admin", is_admin)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_guard.require_admin(FakeConn(), "u1"))
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "forbidden"
    assert exc.value.detail["message"] == "관리자만 가능해요."


def test_require_admin_passes_for_admin(monkeypatch):
    async def is_admin(_conn, _user_id):
        return True

    monkeypatch.setattr(admin_guard.repo, "is_admin", is_admin)
    asyncio.run(admin_guard.require_admin(FakeConn(), "u1"))  # 예외 없음


def test_write_audit_inserts_one_row_with_all_fields():
    conn = FakeConn()
    asyncio.run(admin_guard.write_audit(
        conn,
        actor_user_id="admin-1",
        action="application.reject",
        target_type="application",
        target_id="app-1",
        before={"status": "under_review"},
        after={"status": "rejected"},
        note="사진 불충분",
    ))
    assert len(conn.executed) == 1
    sql, params = conn.executed[0]
    assert sql.startswith("insert into admin_audit_log")
    assert params[0] == "admin-1"
    assert params[1] == "application.reject"
    assert params[2] == "application"
    assert params[3] == "app-1"
    assert params[6] == "사진 불충분"


def test_write_audit_defaults_before_and_after_to_empty_objects():
    conn = FakeConn()
    asyncio.run(admin_guard.write_audit(
        conn, actor_user_id="admin-1", action="staff.role.grant",
        target_type="user", target_id="u2",
    ))
    _sql, params = conn.executed[0]
    # psycopg Json 래퍼 — 원본 dict 를 들고 있다.
    assert params[4].obj == {}
    assert params[5].obj == {}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd server && uv run pytest tests/test_admin_guard.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.admin_guard'`

- [ ] **Step 3: 헬퍼를 구현한다**

`server/app/admin_guard.py`:

```python
"""관리자 권한 게이트와 감사 원장 기록 — 콘솔의 모든 쓰기가 지나는 문.

`repo.is_admin` 을 직접 부르는 곳은 이 파일 하나여야 한다. 예전에는 같은 판정이 6군데에
흩어져 있어 에러 코드·문구가 제각각이었고, 새 라우트를 추가할 때 가드를 빼먹어도 아무도
몰랐다(테스트가 그걸 못 본다).

write_audit 은 conn.commit() 을 하지 않는다 — 호출자(라우트)의 트랜잭션 안에서 조치와
함께 커밋돼야 한다. 따로 커밋하면 조치는 실패하고 기록만 남는 경우가 생긴다.
"""
from fastapi import HTTPException
from psycopg.types.json import Json

from . import repo


def forbidden() -> HTTPException:
    return HTTPException(
        status_code=403, detail={"code": "forbidden", "message": "관리자만 가능해요."}
    )


async def require_admin(conn, user_id: str) -> None:
    if not await repo.is_admin(conn, user_id):
        raise forbidden()


async def write_audit(
    conn,
    *,
    actor_user_id: str,
    action: str,
    target_type: str,
    target_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    note: str | None = None,
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            "insert into admin_audit_log "
            "(actor_user_id, action, target_type, target_id, before, after, note) "
            "values (%s, %s, %s, %s, %s, %s, %s)",
            (
                actor_user_id, action, target_type, target_id,
                Json(before or {}), Json(after or {}), note,
            ),
        )
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd server && uv run pytest tests/test_admin_guard.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add server/app/admin_guard.py server/tests/test_admin_guard.py
git commit -m "$(cat <<'MSG'
feat(admin): 권한 게이트·감사 기록 헬퍼 단일화

같은 관리자 판정이 6군데에 흩어져 있었다. 새 라우트에서 가드를 빼먹어도 아무도 모른다.
write_audit 은 커밋하지 않는다 — 조치와 같은 트랜잭션에서 커밋돼야 둘이 어긋나지 않는다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
)"
```

---

## Task 3: 기존 관리자 라우트를 가드·원장에 태우기

**Files:**
- Modify: `server/app/facemarket_applications.py` (`_require_admin` 정의부와 승인·거절·재발송 라우트)
- Modify: `server/app/routes.py:696,723` (환불 승인·반려)
- Modify: `server/app/facemarket.py:1773`, `server/app/facemarket_cutover.py:371`
- Test: `server/tests/test_admin_guard_adoption.py`

**Interfaces:**
- Consumes: Task 2 의 `require_admin` / `write_audit`
- Produces: 액션 이름 6종 — `application.approve`, `application.reject`, `application.resend_email`, `refund.approve`, `refund.reject` (Task 11·13 이 같은 규칙으로 `model.*`·`staff.*` 를 더한다)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`server/tests/test_admin_guard_adoption.py`:

```python
"""관리자 판정과 감사 기록이 실제로 배선됐는지 — 소스 계약 검사.

되돌아가면: 새 라우트가 가드를 빼먹어도 테스트가 안 잡고, 관리자가 무엇을 했는지 원장에
남지 않는다. 두 사고 모두 조용해서 배포 뒤에는 발견되지 않는다.
"""
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
APPLICATIONS = (APP / "facemarket_applications.py").read_text()
ROUTES = (APP / "routes.py").read_text()
FACEMARKET = (APP / "facemarket.py").read_text()
CUTOVER = (APP / "facemarket_cutover.py").read_text()


def test_no_module_calls_repo_is_admin_directly():
    """admin_guard 하나만 부른다 — 문구·상태코드가 갈라지지 않게."""
    for name, source in (
        ("facemarket_applications.py", APPLICATIONS),
        ("routes.py", ROUTES),
        ("facemarket.py", FACEMARKET),
        ("facemarket_cutover.py", CUTOVER),
    ):
        assert "repo.is_admin" not in source, f"{name} 가 아직 repo.is_admin 을 직접 부른다"


def test_refund_routes_are_gated_and_audited():
    for route in ("approve_refund", "reject_refund"):
        body = ROUTES.split(f"async def {route}(")[1].split("@router.")[0]
        assert "await admin_guard.require_admin(conn, user_id)" in body, route
        assert "admin_guard.write_audit(" in body, route


def test_refund_audit_actions_are_named():
    assert '"refund.approve"' in ROUTES
    assert '"refund.reject"' in ROUTES


def test_application_decisions_are_audited():
    for route, action in (
        ("admin_approve_application", "application.approve"),
        ("admin_reject_application", "application.reject"),
        ("admin_resend_email", "application.resend_email"),
    ):
        body = APPLICATIONS.split(f"async def {route}(")[1].split("@router.")[0]
        assert "admin_guard.write_audit(" in body, route
        assert f'"{action}"' in body, action


def test_reject_audit_carries_the_reason_as_note():
    body = APPLICATIONS.split("async def admin_reject_application(")[1].split("@router.")[0]
    audit = body.split("write_audit(")[1]
    assert "note=" in audit


def test_audit_write_happens_before_commit():
    """조치와 같은 트랜잭션이어야 한다. 커밋 뒤에 쓰면 원장만 따로 커밋되거나 유실된다."""
    body = APPLICATIONS.split("async def admin_approve_application(")[1].split("@router.")[0]
    audit_at = body.index("write_audit(")
    commit_at = body.index("await conn.commit()")
    assert audit_at < commit_at, "감사 기록이 commit 뒤에 있다"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd server && uv run pytest tests/test_admin_guard_adoption.py -q`
Expected: FAIL — `facemarket_applications.py 가 아직 repo.is_admin 을 직접 부른다`

- [ ] **Step 3: 호출부를 옮긴다**

`server/app/facemarket_applications.py` — import 에 `admin_guard` 를 더하고, 기존 `_require_admin` 정의를 위임으로 바꾼다:

```python
from . import admin_guard, facemarket_notify, repo
```

```python
async def _require_admin(conn, user_id: str) -> None:
    """호출부 이름은 그대로 두고 판정만 admin_guard 로 넘긴다(라우트 diff 최소화)."""
    await admin_guard.require_admin(conn, user_id)
```

승인 라우트: 상태 가드 UPDATE 가 성공한 직후, `await conn.commit()` **앞에** 기록을 넣는다.

```python
        await admin_guard.write_audit(
            conn,
            actor_user_id=user_id,
            action="application.approve",
            target_type="application",
            target_id=application_id,
            before={"status": "under_review"},
            after={"status": "approved"},
        )
```

거절 라우트도 같은 자리에:

```python
        await admin_guard.write_audit(
            conn,
            actor_user_id=user_id,
            action="application.reject",
            target_type="application",
            target_id=application_id,
            before={"status": "under_review"},
            after={"status": "rejected"},
            note=reason,
        )
```

재발송 라우트:

```python
        await admin_guard.write_audit(
            conn,
            actor_user_id=user_id,
            action="application.resend_email",
            target_type="application",
            target_id=application_id,
        )
```

`server/app/routes.py` — 두 환불 라우트. `from . import admin_guard` 를 import 절에 더하고:

```python
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        try:
            result = await repo.approve_refund(conn, request_id=request_id, resolved_by=user_id)
        except repo.CreditError as e:
            raise _credit_error(e)
        await admin_guard.write_audit(
            conn,
            actor_user_id=user_id,
            action="refund.approve",
            target_type="refund",
            target_id=request_id,
            before={"status": "pending"},
            after={"status": "approved"},
        )
        await conn.commit()
```

반려 라우트도 같은 모양(`action="refund.reject"`, `after={"status": "rejected"}`).

`server/app/facemarket.py:1773` 과 `server/app/facemarket_cutover.py:371` 은 판정만 교체한다. `facemarket.py` 는 자체 에러 헬퍼(`_err`)를 쓰고 있었지만 여기서도 문구를 통일한다:

```python
        await admin_guard.require_admin(conn, user_id)
```

`facemarket_cutover.py` 는 `CutoverBlocked("admin_required")` 를 던지던 자리다 — **그 동작은 유지한다**(예외 타입이 호출자 계약이다):

```python
            if not await admin_guard.is_admin_user(conn, admin_user_id):
                raise CutoverBlocked("admin_required", "관리자만 가능해요.")
```

그래서 `admin_guard.py` 에 판정만 돌려주는 얇은 함수를 하나 더 둔다:

```python
async def is_admin_user(conn, user_id: str) -> bool:
    """예외 대신 판정만 필요한 호출자(cutover 는 자체 예외 타입을 쓴다)."""
    return await repo.is_admin(conn, user_id)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd server && uv run pytest tests/test_admin_guard_adoption.py tests/test_facemarket_cutover.py -q`
Expected: PASS — 신규 6개 + cutover 기존 테스트 전부.

> cutover 기존 테스트는 `monkeypatch.setattr(facemarket_cutover.repo, "is_admin", ...)` 로 패치한다. 판정을 `admin_guard.is_admin_user` 로 옮기면 그 패치가 안 먹으므로, 해당 테스트 2곳의 패치 대상을 `facemarket_cutover.admin_guard`·`"is_admin_user"` 로 함께 고친다.

- [ ] **Step 5: 전체 백엔드 테스트**

Run: `cd server && uv run pytest -q`
Expected: PASS (기존 전량 green)

- [ ] **Step 6: 커밋**

```bash
git add server/app/admin_guard.py server/app/facemarket_applications.py server/app/routes.py \
        server/app/facemarket.py server/app/facemarket_cutover.py \
        server/tests/test_admin_guard_adoption.py server/tests/test_facemarket_cutover.py
git commit -m "$(cat <<'MSG'
feat(admin): 기존 관리자 라우트를 공용 가드와 감사 원장에 태움

지원서 승인·거절·메일 재발송과 환불 승인·반려가 원장에 남는다. 기록은 조치와 같은
트랜잭션 안, commit 앞에서 쓴다. cutover 는 자체 예외 타입이 호출자 계약이라 판정만
빌려 쓰는 is_admin_user 를 따로 둔다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
)"
```

---

## Task 4: admin 번들 전용 Tailwind 배선

**Files:**
- Create: `src/apps/AppProviders.jsx`, `src/apps/admin/admin.css`, `src/apps/admin/mountAdminApp.jsx`, `src/lib/adminCn.js`
- Modify: `src/apps/mountApp.jsx`, `src/apps/admin/main.jsx`, `vite.config.js`, `package.json`
- Test: `tests/frontend/admin-style-isolation.test.mjs`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `renderApp(App)` — `src/apps/AppProviders.jsx` (프로바이더 + 루프백 정규화, 스타일 import 없음)
  - `mountAdminApp(App)` — `src/apps/admin/mountAdminApp.jsx`
  - `cn(...inputs)` — `src/lib/adminCn.js` (Task 5 의 shadcn 컴포넌트가 전부 이걸 쓴다)

- [ ] **Step 1: 의존성을 설치한다**

```bash
pnpm add -D tailwindcss @tailwindcss/vite
pnpm add clsx tailwind-merge class-variance-authority
```

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`tests/frontend/admin-style-isolation.test.mjs`:

```javascript
/* Tailwind 는 admin 진입 번들에만 들어간다.

   셀러·facemarket 문서에 Tailwind preflight 가 실리면 전역 리셋이 기존 화면을 통째로
   바꾼다. 반대로 admin 이 스튜디오 CSS 를 JS 로 import 하면 그 규칙들이 레이어 밖(unlayered)
   에 놓여, 레이어 안에 있는 Tailwind 유틸리티를 **명시도와 무관하게** 이긴다.
   그래서 admin 은 CSS 한 파일에서 레이어 순서를 직접 정한다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('admin 부트스트랩은 스튜디오 CSS 를 JS 로 import 하지 않는다', () => {
  const source = read('src/apps/admin/mountAdminApp.jsx');
  assert.ok(!source.includes("@/styles/app.css"), 'app.css 를 JS 로 물면 레이어 밖에 놓인다');
  assert.ok(source.includes("./admin.css"), 'admin.css 를 물어야 한다');
});

test('admin.css 는 preflight → 스튜디오 → 유틸리티 순으로 레이어를 정한다', () => {
  const css = read('src/apps/admin/admin.css');
  const order = css.match(/@layer\s+([^;]+);/);
  assert.ok(order, '@layer 선언이 없다');
  const layers = order[1].split(',').map((s) => s.trim());
  assert.deepEqual(layers, ['theme', 'base', 'studio', 'components', 'utilities']);
});

test('스튜디오 진입은 Tailwind 를 물지 않는다', () => {
  for (const entry of ['src/apps/mountApp.jsx', 'src/apps/seller/App.jsx', 'src/apps/facemarket/App.jsx']) {
    const source = read(entry);
    assert.ok(!source.includes('admin.css'), `${entry} 가 admin.css 를 문다`);
    assert.ok(!source.includes('tailwindcss'), `${entry} 가 tailwind 를 문다`);
  }
});

test('mountApp 은 스튜디오 스타일을 계속 물고 프로바이더는 공유한다', () => {
  const source = read('src/apps/mountApp.jsx');
  assert.ok(source.includes("@/styles/app.css"));
  assert.ok(source.includes("AppProviders.jsx"));
});
```

- [ ] **Step 3: 실패를 확인한다**

Run: `pnpm test:frontend`
Expected: FAIL — `ENOENT: src/apps/admin/mountAdminApp.jsx`

- [ ] **Step 4: 프로바이더를 분리한다**

`src/apps/AppProviders.jsx`:

```jsx
/* 진입점 3개(셀러·facemarket·admin)가 공유하는 프로바이더 구성과 루프백 정규화.

   스타일 import 는 **여기 두지 않는다**. admin 은 Tailwind 레이어 순서를 자기 CSS 에서
   직접 정해야 하는데, JS import 로 들어온 스튜디오 CSS 는 레이어 밖(unlayered)이 되어
   레이어 안의 유틸리티를 명시도와 무관하게 이긴다. 그래서 스타일은 각 진입점이 문다. */
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider } from '@/features/auth/AuthProvider.jsx';
import { ToastProvider } from '@/components/ui.jsx';

export function renderApp(App) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { staleTime: 60_000, retry: 1, refetchOnWindowFocus: false } },
  });

  // API와 R2의 브라우저 CORS는 개발 origin을 localhost로 고정한다. 127.0.0.1/::1은
  // 같은 컴퓨터여도 브라우저상 다른 origin이라 업로드 전에 `Failed to fetch`로 차단된다.
  const isLoopbackAlias = import.meta.env.DEV
    && ['127.0.0.1', '[::1]'].includes(window.location.hostname);

  if (isLoopbackAlias) {
    const canonicalUrl = new URL(window.location.href);
    canonicalUrl.hostname = 'localhost';
    window.location.replace(canonicalUrl);
    return;
  }

  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <BrowserRouter future={{ v7_startTransition: true }}>
            <ToastProvider>
              <App />
            </ToastProvider>
          </BrowserRouter>
        </AuthProvider>
      </QueryClientProvider>
    </React.StrictMode>
  );
}
```

`src/apps/mountApp.jsx` 는 스타일만 남기고 위임한다:

```jsx
/* 스튜디오 두 진입점(셀러·facemarket)의 부트스트랩 — 전역 스타일 + 공용 프로바이더.
   admin 은 mountAdminApp.jsx 를 쓴다(Tailwind 레이어 때문에 스타일 조립이 다르다). */
import '@/styles/tokens.css';
import '@/styles/app.css';
/* FaceMarket 도메인 테마. 규칙이 전부 `.fm-theme` 하위라 그 클래스를 쓰지 않는
   ai.wearless.kr 화면에는 한 줄도 적용되지 않는다. app.css 뒤에 와야 전역
   레이아웃 클래스(.wizard·.surface 등)를 이 스코프에서 덮을 수 있다. */
import '@/styles/facemarketTheme.css';
import '@/styles/features.css';
import '@/styles/moveable.css';
import { renderApp } from './AppProviders.jsx';

export function mountApp(App) {
  renderApp(App);
}
```

- [ ] **Step 5: admin 스타일 진입을 만든다**

`src/apps/admin/admin.css`:

```css
/* =============================================================
   admin.wearless.kr 스타일 진입 — Tailwind v4 + 스튜디오 CSS 의 레이어 순서를 정한다.

   순서가 이 파일의 존재 이유다:
     theme  … Tailwind 토큰(--color-*, --spacing-*)
     base   … preflight(전역 리셋) — shadcn 컴포넌트가 이걸 전제로 만들어져 있다
     studio … tokens.css·app.css·features.css — 로그인 모달(AuthProvider→LoginGate)이
              이 규칙들로 그려진다. preflight **뒤**에 와야 리셋에 지워지지 않는다.
     utilities … Tailwind 유틸리티 — 관리자 화면 마크업이 마지막에 이긴다.

   스튜디오 CSS 를 JS 로 import 하면(예전 mountApp 방식) 레이어 밖에 놓여 유틸리티를
   전부 이긴다(레이어 판정이 명시도보다 먼저다). 그래서 여기서 @import 로 레이어에 넣는다.
   ============================================================= */
@layer theme, base, studio, components, utilities;

@import "tailwindcss/theme.css" layer(theme);
@import "tailwindcss/preflight.css" layer(base);

@import "../../styles/tokens.css" layer(studio);
@import "../../styles/app.css" layer(studio);
@import "../../styles/features.css" layer(studio);

@import "tailwindcss/utilities.css" layer(utilities);

/* 유틸리티 클래스 스캔 범위 — 관리자 화면만. 다른 앱 파일을 훑으면 쓰지도 않는
   클래스가 admin 번들로 들어온다. */
@source "../../apps/admin";
@source "../../features/admin";
@source "../../components/admin-ui";

/* shadcn 색 토큰(zinc, 라이트 전용). 다크는 나중에 이 블록을 복제해
   `@media (prefers-color-scheme: dark)` 또는 `[data-theme="dark"]` 로 덮으면 끝난다. */
:root {
  --background: #ffffff;
  --foreground: #18181b;
  --card: #ffffff;
  --card-foreground: #18181b;
  --muted: #f4f4f5;
  --muted-foreground: #71717a;
  --border: #e4e4e7;
  --input: #e4e4e7;
  --ring: #18181b;
  --primary: #18181b;
  --primary-foreground: #fafafa;
  --destructive: #dc2626;
  --destructive-foreground: #fafafa;
  --radius: 0.5rem;
}

@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-destructive: var(--destructive);
  --color-destructive-foreground: var(--destructive-foreground);
  --radius-lg: var(--radius);
}
```

`src/apps/admin/mountAdminApp.jsx`:

```jsx
/* admin.wearless.kr 부트스트랩. 스타일은 admin.css 한 파일이 전부 조립한다
   (레이어 순서 때문에 — admin.css 주석 참조). 프로바이더는 스튜디오와 공유한다. */
import './admin.css';
import { renderApp } from '../AppProviders.jsx';

export function mountAdminApp(App) {
  renderApp(App);
}
```

`src/apps/admin/main.jsx`:

```jsx
/* admin.wearless.kr 의 진입점. 스타일 조립만 스튜디오와 다르다(admin.css → Tailwind 레이어). */
import AppAdmin from './App.jsx';
import { mountAdminApp } from './mountAdminApp.jsx';

mountAdminApp(AppAdmin);
```

`src/lib/adminCn.js`:

```javascript
/* shadcn 컴포넌트가 공유하는 클래스 병합기. tailwind-merge 가 뒤에 온 유틸리티를
   이기게 해 준다(예: 기본 px-4 를 호출부의 px-2 로 덮기). admin 번들 전용이다. */
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}
```

- [ ] **Step 6: Vite 에 Tailwind 플러그인을 단다**

`vite.config.js` — import 에 더하고 `plugins` 에 넣는다:

```javascript
import tailwindcss from '@tailwindcss/vite';
```

```javascript
  // Tailwind 는 CSS 를 import 하는 진입(admin.html → admin.css)에만 실린다.
  // 플러그인 등록 자체는 전역이지만, 셀러·facemarket 은 admin.css 를 물지 않으므로
  // 산출 CSS 에 유틸리티가 한 줄도 들어가지 않는다.
  plugins: [react(), tailwindcss(), facemarketDevDocument],
```

- [ ] **Step 7: 통과를 확인한다**

Run: `pnpm test:frontend`
Expected: PASS (신규 4개 포함 전량)

- [ ] **Step 8: 빌드로 격리를 실측한다**

```bash
pnpm build
node -e "const fs=require('fs');const d='dist/assets';const files=fs.readdirSync(d).filter(f=>f.endsWith('.css'));for(const f of files){const s=fs.readFileSync(d+'/'+f,'utf8');console.log(f, s.length, /\.flex\{|\.grid\{/.test(s)?'TAILWIND':'-')}"
```

Expected: Tailwind 유틸리티가 든 CSS 파일이 **1개뿐**이고, 나머지 CSS 는 종전과 같다.

- [ ] **Step 9: 커밋**

```bash
git add package.json pnpm-lock.yaml vite.config.js src/apps/AppProviders.jsx src/apps/mountApp.jsx \
        src/apps/admin/admin.css src/apps/admin/mountAdminApp.jsx src/apps/admin/main.jsx \
        src/lib/adminCn.js tests/frontend/admin-style-isolation.test.mjs
git commit -m "$(cat <<'MSG'
feat(admin): admin 번들에만 Tailwind — 레이어로 스튜디오 CSS 와 공존

스튜디오 CSS 를 JS 로 물면 레이어 밖에 놓여 Tailwind 유틸리티를 명시도와 무관하게
이긴다. 그래서 admin.css 가 theme→base(preflight)→studio→utilities 순서를 직접 정하고,
mountAdminApp 은 그 파일 하나만 문다. 로그인 모달이 preflight 뒤 studio 레이어에서
그려져 종전과 같이 보인다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
)"
```

---

## Task 5: shadcn 컴포넌트 + 콘솔 셸

**Files:**
- Create: `components.json`, `src/components/admin-ui/button.jsx`, `card.jsx`, `badge.jsx`, `input.jsx`, `table.jsx`, `skeleton.jsx`
- Create: `src/features/admin/AdminShell.jsx`
- Modify: `src/apps/admin/App.jsx`
- Test: `tests/frontend/admin-shell.test.mjs`

**Interfaces:**
- Consumes: Task 4 의 `cn()` (`@/lib/adminCn.js`), `admin.css` 색 토큰
- Produces:
  - `<Button variant="default|outline|ghost|destructive" size="default|sm|icon">`
  - `<Card> <CardHeader> <CardTitle> <CardDescription> <CardContent> <CardFooter>`
  - `<Badge variant="default|secondary|outline|destructive">`
  - `<Input>`
  - `<Table> <TableHeader> <TableBody> <TableRow> <TableHead> <TableCell>`
  - `<Skeleton className>`
  - `<AdminShell>` — 사이드바 + `<Outlet/>`
  - 라우트 4개: `/` (대시보드) · `/applications` · `/models` · `/staff`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/frontend/admin-shell.test.mjs`:

```javascript
/* 관리자 콘솔 셸과 라우트 계약.

   라우트가 늘어난 뒤에도 로그인 가드(RequireAuth) 밖으로 새는 화면이 없어야 한다 —
   콘솔은 전부 보호 대상이다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('admin 라우트 4개가 셸 아래, 로그인 가드 안에 있다', () => {
  const app = read('src/apps/admin/App.jsx');
  assert.ok(app.includes('<RequireAuth />'), '로그인 가드가 없다');
  assert.ok(app.includes('AdminShell'), '셸이 없다');
  for (const path of ['applications', 'models', 'staff']) {
    assert.ok(app.includes(`path="${path}"`), `라우트 누락: ${path}`);
  }
  // 셸 밖(가드 밖)에 화면 라우트를 두면 안 된다 — catch-all 리다이렉트만 허용.
  const outside = app.split('</Route>')[1] || '';
  assert.ok(!outside.includes('element={<Admin'), '가드 밖에 관리자 화면이 있다');
});

test('셸은 네 갈래 내비게이션을 가진다', () => {
  const shell = read('src/features/admin/AdminShell.jsx');
  for (const label of ['대시보드', '지원서', '모델', '관리자']) {
    assert.ok(shell.includes(label), `내비 항목 누락: ${label}`);
  }
});

test('admin-ui 컴포넌트는 공용 ui.jsx 를 물지 않는다', () => {
  for (const name of ['button', 'card', 'badge', 'input', 'table', 'skeleton']) {
    const source = read(`src/components/admin-ui/${name}.jsx`);
    assert.ok(!source.includes('@/components/ui.jsx'), `${name} 이 공용 ui.jsx 를 문다`);
    assert.ok(source.includes('@/lib/adminCn.js'), `${name} 이 cn() 을 안 쓴다`);
  }
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pnpm test:frontend`
Expected: FAIL — `ENOENT: src/components/admin-ui/button.jsx`

- [ ] **Step 3: shadcn 설정 파일을 둔다**

`components.json` (레포 루트) — 앞으로 `pnpm dlx shadcn@latest add <컴포넌트>` 로 더 받을 때 쓰는 좌표다:

```json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "new-york",
  "rsc": false,
  "tsx": false,
  "tailwind": {
    "config": "",
    "css": "src/apps/admin/admin.css",
    "baseColor": "zinc",
    "cssVariables": true
  },
  "aliases": {
    "components": "@/components/admin-ui",
    "utils": "@/lib/adminCn"
  },
  "iconLibrary": "lucide"
}
```

- [ ] **Step 4: 컴포넌트를 만든다**

`src/components/admin-ui/button.jsx`:

```jsx
import { cva } from 'class-variance-authority';
import { cn } from '@/lib/adminCn.js';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-primary/90',
        outline: 'border border-border bg-background hover:bg-muted',
        ghost: 'hover:bg-muted',
        destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive/90',
      },
      size: { default: 'h-9 px-4 py-2', sm: 'h-8 px-3 text-xs', icon: 'h-9 w-9' },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
);

export function Button({ className, variant, size, type = 'button', ...props }) {
  return <button type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}

export { buttonVariants };
```

`src/components/admin-ui/card.jsx`:

```jsx
import { cn } from '@/lib/adminCn.js';

export function Card({ className, ...props }) {
  return <div className={cn('rounded-lg border border-border bg-card text-card-foreground', className)} {...props} />;
}
export function CardHeader({ className, ...props }) {
  return <div className={cn('flex flex-col gap-1 p-5', className)} {...props} />;
}
export function CardTitle({ className, ...props }) {
  return <h3 className={cn('text-sm font-medium tracking-tight', className)} {...props} />;
}
export function CardDescription({ className, ...props }) {
  return <p className={cn('text-xs text-muted-foreground', className)} {...props} />;
}
export function CardContent({ className, ...props }) {
  return <div className={cn('p-5 pt-0', className)} {...props} />;
}
export function CardFooter({ className, ...props }) {
  return <div className={cn('flex items-center gap-2 p-5 pt-0', className)} {...props} />;
}
```

`src/components/admin-ui/badge.jsx`:

```jsx
import { cva } from 'class-variance-authority';
import { cn } from '@/lib/adminCn.js';

const badgeVariants = cva(
  'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary text-primary-foreground',
        secondary: 'border-transparent bg-muted text-muted-foreground',
        outline: 'border-border text-foreground',
        destructive: 'border-transparent bg-destructive text-destructive-foreground',
      },
    },
    defaultVariants: { variant: 'default' },
  },
);

export function Badge({ className, variant, ...props }) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
```

`src/components/admin-ui/input.jsx`:

```jsx
import { cn } from '@/lib/adminCn.js';

export function Input({ className, type = 'text', ...props }) {
  return (
    <input
      type={type}
      className={cn(
        'flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm',
        'placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  );
}
```

`src/components/admin-ui/table.jsx`:

```jsx
import { cn } from '@/lib/adminCn.js';

export function Table({ className, ...props }) {
  // 표는 자기 컨테이너 안에서 가로 스크롤한다 — 페이지 몸통이 옆으로 밀리지 않게.
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn('w-full caption-bottom text-sm', className)} {...props} />
    </div>
  );
}
export function TableHeader({ className, ...props }) {
  return <thead className={cn('[&_tr]:border-b [&_tr]:border-border', className)} {...props} />;
}
export function TableBody({ className, ...props }) {
  return <tbody className={cn('[&_tr:last-child]:border-0', className)} {...props} />;
}
export function TableRow({ className, ...props }) {
  return <tr className={cn('border-b border-border transition-colors hover:bg-muted/50', className)} {...props} />;
}
export function TableHead({ className, ...props }) {
  return <th className={cn('h-10 px-3 text-left align-middle text-xs font-medium text-muted-foreground', className)} {...props} />;
}
export function TableCell({ className, ...props }) {
  return <td className={cn('px-3 py-2.5 align-middle', className)} {...props} />;
}
```

`src/components/admin-ui/skeleton.jsx`:

```jsx
import { cn } from '@/lib/adminCn.js';

export function Skeleton({ className, ...props }) {
  return <div className={cn('animate-pulse rounded-md bg-muted', className)} {...props} />;
}
```

- [ ] **Step 5: 셸을 만든다**

`src/features/admin/AdminShell.jsx`:

```jsx
/* 관리자 콘솔 셸 — 좌측 고정 내비 + 우측 본문.

   콘솔은 화면 수가 적고 오래 열어 두는 도구라 상단 탭보다 사이드바가 맞는다(현재 위치가
   항상 보이고, 화면이 늘어도 세로로 늘어난다). 모바일은 대상이 아니다 — 작은 화면에서는
   내비가 위로 접힌다. */
import { NavLink, Outlet } from 'react-router-dom';
import { FileText, LayoutDashboard, ShieldCheck, Users } from 'lucide-react';
import { cn } from '@/lib/adminCn.js';

const NAV = [
  { to: '/', label: '대시보드', icon: LayoutDashboard, end: true },
  { to: '/applications', label: '지원서 검토', icon: FileText },
  { to: '/models', label: '모델·유저', icon: Users },
  { to: '/staff', label: '관리자 관리', icon: ShieldCheck },
];

export function AdminShell() {
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground sm:flex-row">
      <aside className="shrink-0 border-b border-border sm:w-56 sm:border-b-0 sm:border-r">
        <div className="px-5 py-4 text-sm font-semibold tracking-tight">Wearless 관리자</div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-3 sm:flex-col sm:overflow-visible">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => cn(
                'flex items-center gap-2 whitespace-nowrap rounded-md px-3 py-2 text-sm transition-colors',
                isActive ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/60',
              )}
            >
              <Icon size={16} aria-hidden />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="min-w-0 flex-1 px-5 py-6 sm:px-8">
        <Outlet />
      </main>
    </div>
  );
}
```

- [ ] **Step 6: 라우트를 넓힌다**

`src/apps/admin/App.jsx` 의 `<Routes>` 부분만 교체한다(파일 상단 주석·호스트 되돌림·Supabase 설정 확인은 그대로 둔다):

```jsx
import { AdminShell } from '@/features/admin/AdminShell.jsx';
import { AdminApplications } from '@/features/admin/AdminApplications.jsx';
import { AdminDashboard } from '@/features/admin/AdminDashboard.jsx';
import { AdminModels } from '@/features/admin/AdminModels.jsx';
import { AdminStaff } from '@/features/admin/AdminStaff.jsx';
```

```jsx
    <Routes>
      <Route element={<RequireAuth />}>
        <Route element={<AdminShell />}>
          <Route index element={<AdminDashboard />} />
          <Route path="applications" element={<AdminApplications />} />
          <Route path="models" element={<AdminModels />} />
          <Route path="staff" element={<AdminStaff />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
```

이 태스크에서는 `AdminDashboard`·`AdminModels`·`AdminStaff` 를 **자리표시 화면**으로 만든다(Task 8·12·14 가 내용을 채운다). 자리표시라도 실제로 렌더돼야 라우팅이 검증된다:

```jsx
/* src/features/admin/AdminDashboard.jsx — Task 8 에서 내용을 채운다. */
export function AdminDashboard() {
  return <h1 className="text-lg font-semibold">대시보드</h1>;
}
```

```jsx
/* src/features/admin/AdminModels.jsx — Task 11 에서 내용을 채운다. */
export function AdminModels() {
  return <h1 className="text-lg font-semibold">모델·유저</h1>;
}
```

```jsx
/* src/features/admin/AdminStaff.jsx — Task 13 에서 내용을 채운다. */
export function AdminStaff() {
  return <h1 className="text-lg font-semibold">관리자 관리</h1>;
}
```

- [ ] **Step 7: 통과를 확인한다**

Run: `pnpm test:frontend && pnpm build`
Expected: PASS + 빌드 성공

- [ ] **Step 8: 커밋**

```bash
git add components.json src/components/admin-ui src/features/admin/AdminShell.jsx \
        src/features/admin/AdminDashboard.jsx src/features/admin/AdminModels.jsx \
        src/features/admin/AdminStaff.jsx src/apps/admin/App.jsx tests/frontend/admin-shell.test.mjs
git commit -m "$(cat <<'MSG'
feat(admin): shadcn 컴포넌트 사본 + 사이드바 셸 + 라우트 4개

콘솔은 화면 수가 적고 오래 열어 두는 도구라 상단 탭보다 사이드바가 맞다. 나머지 세
화면은 자리표시로 두고 뒤 태스크가 채운다 — 라우팅은 지금 검증한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
)"
```

---

## Task 6: 지원서 화면을 shadcn 으로 이관

**Files:**
- Modify: `src/features/admin/AdminApplications.jsx`
- Delete: `src/features/admin/AdminApplications.module.css`
- Test: `tests/frontend/admin-applications-parity.test.mjs`

**Interfaces:**
- Consumes: Task 5 의 `admin-ui` 컴포넌트
- Produces: 없음(외부 계약 불변 — 같은 API 함수, 같은 상태 처리)

**동작은 한 줄도 바뀌지 않는다.** `useState`/`useEffect`/`useCallback` 훅, `adminListApplications`·`adminApproveApplication`·`adminRejectApplication`·`adminResendEmail`·`adminFetchApplicationPhotoUrl` 호출, objectURL 해제, 409 처리, 필터 상태 — 전부 그대로 둔다. 바뀌는 것은 마크업과 클래스뿐이다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/frontend/admin-applications-parity.test.mjs`:

```javascript
/* 지원서 화면 이관 — 껍데기만 바꾸고 동작은 그대로인지.

   되돌아가면: 스타일을 갈아엎다가 사진 objectURL 해제나 409 재조회 같은 "안 보이는 동작"이
   함께 사라진다. 그 손실은 화면을 봐서는 모른다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');
const source = read('src/features/admin/AdminApplications.jsx');

test('CSS 모듈을 버리고 admin-ui 를 쓴다 — ui.jsx 에서는 토스트 훅만 빌린다', () => {
  assert.ok(!source.includes('AdminApplications.module.css'));
  assert.ok(source.includes('@/components/admin-ui/'));
  // ToastProvider 는 AppProviders 에 남아 있고 스타일은 studio 레이어가 준다.
  // 훅 하나를 위해 토스트를 다시 구현하지 않는다. 대신 **시각 컴포넌트는** 가져오지 않는다.
  const uiImport = source.match(/import\s*\{([^}]*)\}\s*from\s*'@\/components\/ui\.jsx';/);
  if (uiImport) {
    const named = uiImport[1].split(',').map((s) => s.trim()).filter(Boolean);
    assert.deepEqual(named, ['useToast'], `ui.jsx 에서 토스트 훅 말고 더 가져온다: ${named}`);
  }
});

test('관리자 API 다섯 개를 그대로 호출한다', () => {
  for (const fn of [
    'adminListApplications', 'adminApproveApplication', 'adminRejectApplication',
    'adminResendEmail', 'adminFetchApplicationPhotoUrl',
  ]) {
    assert.ok(source.includes(fn), `호출이 사라졌다: ${fn}`);
  }
});

test('사진 objectURL 을 계속 해제한다', () => {
  assert.ok(source.includes('URL.revokeObjectURL'), 'objectURL 누수');
});

test('거절은 사유 입력을 요구한다', () => {
  assert.ok(source.includes('reason'), '거절 사유 상태가 사라졌다');
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pnpm test:frontend`
Expected: FAIL — `AdminApplications.module.css` 를 아직 문다

- [ ] **Step 3: 마크업을 교체한다**

교체 대응표(로직은 손대지 않는다):

| 지금 | 바뀜 |
|---|---|
| `<li className={s.card}>` | `<Card className="flex gap-5 p-5">` |
| `<span className={s.name}>` | `<CardTitle className="text-base">` |
| `<span className={s.badge} …>` | `<Badge variant={…}>` (승인=`default`, 거절=`destructive`, 검토중=`secondary`, 취소=`outline`) |
| `<dl className={s.fields}>` | `<dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm sm:grid-cols-3">` |
| `<Chips …>` (상태 필터) | `<Button variant={active ? 'default' : 'outline'} size="sm">` 묶음 |
| `<Button variant="ghost" size="sm">` (공용) | `admin-ui` 의 같은 이름 컴포넌트 |
| `<ErrorState …>` | `<Card>` + 제목·설명 + 재시도 `<Button variant="outline">` |
| `useToast()` | 그대로 둔다 — `ToastProvider` 는 `AppProviders` 에 남아 있고 스타일은 studio 레이어가 준다. **ui.jsx 에서 가져오는 것은 이 훅 하나뿐**이고, 시각 컴포넌트는 전부 `admin-ui` 를 쓴다 |

거절 폼(대표 블록) — 사유 입력과 두 버튼:

```jsx
{rejecting && (
  <div className="mt-3 flex flex-col gap-2 sm:flex-row">
    <Input
      value={reason}
      onChange={(e) => setReason(e.target.value)}
      placeholder="거절 사유 (지원자에게 메일로 전달돼요)"
      className="sm:flex-1"
    />
    <div className="flex gap-2">
      <Button
        variant="destructive"
        size="sm"
        disabled={busy || !reason.trim()}
        onClick={() => onReject(app, reason.trim())}
      >
        거절 확정
      </Button>
      <Button variant="ghost" size="sm" disabled={busy} onClick={() => setRejecting(false)}>
        취소
      </Button>
    </div>
  </div>
)}
```

사진 슬롯:

```jsx
<figure className="flex w-24 shrink-0 flex-col gap-1">
  {!hasPhoto && <div className="flex h-32 items-center justify-center rounded-md bg-muted text-muted-foreground">—</div>}
  {hasPhoto && !url && <Skeleton className="h-32 w-24" />}
  {hasPhoto && url && <img className="h-32 w-24 rounded-md object-cover" src={url} alt={`지원자 ${label} 사진`} />}
  <figcaption className="text-center text-xs text-muted-foreground">{label}</figcaption>
</figure>
```

- [ ] **Step 4: CSS 모듈을 지운다**

```bash
git rm src/features/admin/AdminApplications.module.css
```

- [ ] **Step 5: 통과를 확인한다**

Run: `pnpm test:frontend && pnpm build`
Expected: PASS + 빌드 성공

- [ ] **Step 6: 눈으로 확인한다**

```bash
pnpm dev
```
브라우저에서 `http://localhost:5173/applications?admin=1` — 카드·필터·승인/거절/재발송이 종전과 같이 동작하는지. (관리자 계정이 없으면 `scripts/qa_grant_admin.sh` 로 승격)

- [ ] **Step 7: 커밋**

```bash
git add -A src/features/admin tests/frontend/admin-applications-parity.test.mjs
git commit -m "$(cat <<'MSG'
refactor(admin): 지원서 검토 화면을 shadcn 마크업으로 이관

훅·API 호출·objectURL 해제·409 처리는 한 줄도 건드리지 않았다. 스타일을 갈아엎다가
"안 보이는 동작"이 함께 사라지는 걸 막으려고 그 계약을 테스트로 먼저 못박았다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
)"
```

---

## Task 7: 대시보드 집계 API

**Files:**
- Create: `server/app/facemarket_admin.py`
- Modify: `server/app/main.py` (라우터 등록)
- Test: `server/tests/test_admin_overview.py`

**Interfaces:**
- Consumes: Task 2 의 `require_admin`
- Produces:
  - `GET /v1/facemarket/admin/overview?days=7|30|90` → 설계 §5.1 의 JSON
  - 모듈 전역 `_OVERVIEW_CACHE: dict[int, tuple[float, dict]]`, `OVERVIEW_TTL_SECONDS = 30`
  - `_period_start(days: int) -> datetime` (KST 기준 시작 시각, UTC aware)
  - Task 9·10 이 이 라우터에 라우트를 더한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`server/tests/test_admin_overview.py`:

```python
"""대시보드 집계 — 숫자의 정의가 설계와 일치하는지.

되돌아가면: 데모용 시뮬 정산(payment_id 'sim:')과 테스트 결제(provider 'test')가 매출로
섞여 들어간다. 그 숫자를 보고 의사결정을 하면 틀린 결정을 한다.
"""
import contextlib
from datetime import datetime, timedelta, timezone

import pytest

from app import facemarket_admin


class FakeCursor:
    def __init__(self, store, rows):
        self.store = store
        self.rows = rows
        self._row = None

    async def execute(self, sql, params=None):
        self.store.append((" ".join(sql.split()), params))
        self._row = self.rows.pop(0) if self.rows else {}

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._row if isinstance(self._row, list) else []


class FakeConn:
    def __init__(self, rows):
        self.executed = []
        self.rows = list(rows)

    def cursor(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeCursor(self.executed, self.rows)

        return _cm()


QUEUE_ROW = {
    "applications_under_review": 3, "identity_mismatch": 1,
    "email_failed": 0, "refunds_pending": 2,
}
KPI_ROW = {
    "applications_submitted": 12, "applications_approved": 7, "applications_rejected": 3,
    "licenses_issued": 5, "settlement_amount_krw": 120000, "settlement_failed": 1,
    "credit_revenue_krw": 350000,
}
SERIES_ROWS = [
    {"date": "2026-09-03", "applications": 1, "licenses": 0, "settlement_amount_krw": 0},
    {"date": "2026-09-04", "applications": 2, "licenses": 1, "settlement_amount_krw": 20000},
]
DIST_ROW = {
    "models_pending": 2, "models_verified": 9, "models_suspended": 1,
    "enrollments_passed": 9, "enrollments_failed": 3, "enrollments_in_flight": 2,
}


def _conn():
    return FakeConn([QUEUE_ROW, KPI_ROW, SERIES_ROWS, DIST_ROW])


def test_rejects_unsupported_period():
    with pytest.raises(Exception) as exc:
        facemarket_admin.validate_days(45)
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "invalid_period"


def test_accepts_supported_periods():
    for days in (7, 30, 90):
        assert facemarket_admin.validate_days(days) == days


def test_simulation_settlements_are_excluded():
    import asyncio
    conn = _conn()
    asyncio.run(facemarket_admin.build_overview(conn, days=30))
    sql = " ".join(s for s, _ in conn.executed)
    assert "payment_id not like 'sim:%%'" in sql, "시뮬 정산이 매출에 섞인다"


def test_test_payments_are_excluded():
    import asyncio
    conn = _conn()
    asyncio.run(facemarket_admin.build_overview(conn, days=30))
    sql = " ".join(s for s, _ in conn.executed)
    assert "provider <> 'test'" in sql, "테스트 결제가 매출에 섞인다"


def test_series_uses_kst_day_boundaries():
    import asyncio
    conn = _conn()
    asyncio.run(facemarket_admin.build_overview(conn, days=30))
    sql = " ".join(s for s, _ in conn.executed)
    assert "at time zone 'Asia/Seoul'" in sql, "UTC 로 날짜를 자르면 운영자의 '오늘'과 다르다"
    assert "generate_series" in sql, "빈 날짜가 0 으로 채워지지 않는다"


def test_payload_shape_is_camel_case():
    import asyncio
    payload = asyncio.run(facemarket_admin.build_overview(_conn(), days=30))
    assert payload["queue"]["applicationsUnderReview"] == 3
    assert payload["kpi"]["settlementAmountKrw"] == 120000
    assert payload["kpi"]["creditRevenueKrw"] == 350000
    assert payload["distribution"]["models"]["verified"] == 9
    assert payload["distribution"]["enrollments"]["inFlight"] == 2
    assert payload["series"][1]["settlementAmountKrw"] == 20000
    assert payload["period"]["days"] == 30


def test_period_start_is_kst_midnight():
    start = facemarket_admin._period_start(7)
    kst = timezone(timedelta(hours=9))
    local = start.astimezone(kst)
    assert (local.hour, local.minute, local.second) == (0, 0, 0)
    today = datetime.now(kst).date()
    assert local.date() == today - timedelta(days=6)


def test_cache_serves_second_call_without_touching_db():
    import asyncio
    facemarket_admin.clear_overview_cache()
    first = _conn()
    asyncio.run(facemarket_admin.overview_payload(first, days=30))
    second = _conn()
    asyncio.run(facemarket_admin.overview_payload(second, days=30))
    assert second.executed == [], "캐시가 안 먹어 새로고침마다 DB 를 친다"


def test_cache_is_per_period():
    import asyncio
    facemarket_admin.clear_overview_cache()
    asyncio.run(facemarket_admin.overview_payload(_conn(), days=30))
    other = _conn()
    asyncio.run(facemarket_admin.overview_payload(other, days=7))
    assert other.executed, "기간이 다른데 30일치 캐시를 돌려준다"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd server && uv run pytest tests/test_admin_overview.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.facemarket_admin'`

- [ ] **Step 3: 라우터를 구현한다**

`server/app/facemarket_admin.py`:

```python
"""관리자 콘솔 전용 라우터 — 집계·모델 조회·권한 관리.

지원서 라우트는 facemarket_applications.py 에 그대로 둔다(그 도메인과 함께 산다).
여기 있는 것은 "콘솔이라서 필요한" 것들이다.

집계는 롤업 테이블 없이 라이브 SQL 이다. 지금 규모(각 테이블 수백~수천 행)에서는 충분하고,
숫자가 항상 진짜다. 대신 새로고침 연타가 DB 를 두드리지 않게 30초 프로세스 캐시를 둔다 —
태스크가 여러 개면 태스크마다 따로 캐시되지만, 30초짜리 불일치는 무해하다.
"""
import time
from datetime import datetime, time as dtime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import admin_guard
from .auth import require_user
from .db import get_conn

router = APIRouter(prefix="/v1/facemarket/admin", tags=["FaceMarket admin console"])

KST = timezone(timedelta(hours=9))
ALLOWED_PERIODS = (7, 30, 90)
OVERVIEW_TTL_SECONDS = 30

_OVERVIEW_CACHE: dict[int, tuple[float, dict]] = {}


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def validate_days(days: int) -> int:
    if days not in ALLOWED_PERIODS:
        raise _err("invalid_period", "기간은 7·30·90일만 볼 수 있어요.")
    return days


def _period_start(days: int) -> datetime:
    """KST 오늘을 포함해 days 일 전 자정(KST) — UTC aware 로 돌려준다.

    운영자가 말하는 '오늘'은 서울 기준이다. UTC 로 자르면 오전 9시 전에 만든 지원서가
    어제로 잡혀 대시보드와 사람의 기억이 어긋난다.
    """
    today = datetime.now(KST).date()
    start = datetime.combine(today - timedelta(days=days - 1), dtime.min, tzinfo=KST)
    return start.astimezone(timezone.utc)


QUEUE_SQL = """
select
  (select count(*) from fm_model_applications where status = 'under_review')
    as applications_under_review,
  (select count(*) from fm_model_applications
     where status = 'under_review' and identity_mismatch_count > 0) as identity_mismatch,
  -- 'pending 인 채 2분 넘은 행'을 미발송으로 보는 규칙은 목록 라우트(admin_list_applications)의
  -- lateral 과 같아야 한다. 두 곳이 갈라지면 대시보드 숫자와 배지가 서로 다른 말을 한다.
  (select count(distinct application_id) from fm_model_application_emails
     where status = 'failed'
        or (status = 'pending' and created_at < now() - interval '2 minutes')) as email_failed,
  (select count(*) from refund_requests where status = 'pending') as refunds_pending
"""

KPI_SQL = """
select
  (select count(*) from fm_model_applications where created_at >= %(from_ts)s)
    as applications_submitted,
  (select count(*) from fm_model_applications
     where status = 'approved' and reviewed_at >= %(from_ts)s) as applications_approved,
  (select count(*) from fm_model_applications
     where status = 'rejected' and reviewed_at >= %(from_ts)s) as applications_rejected,
  (select count(*) from fm_licenses where created_at >= %(from_ts)s) as licenses_issued,
  (select coalesce(sum(total_amount), 0) from fm_settlements
     where chain_status = 'confirmed' and created_at >= %(from_ts)s
       and payment_id not like 'sim:%%') as settlement_amount_krw,
  (select count(*) from fm_settlements
     where chain_status = 'failed' and created_at >= %(from_ts)s
       and payment_id not like 'sim:%%') as settlement_failed,
  (select coalesce(sum(amount), 0) from payment_history
     where status = 'paid' and provider <> 'test'
       and created_at >= %(from_ts)s) as credit_revenue_krw
"""

SERIES_SQL = """
with days as (
  select generate_series(
    (now() at time zone 'Asia/Seoul')::date - (%(days)s::int - 1),
    (now() at time zone 'Asia/Seoul')::date,
    interval '1 day'
  )::date as d
)
select d::text as date,
  (select count(*) from fm_model_applications a
     where (a.created_at at time zone 'Asia/Seoul')::date = d) as applications,
  (select count(*) from fm_licenses l
     where (l.created_at at time zone 'Asia/Seoul')::date = d) as licenses,
  (select coalesce(sum(s.total_amount), 0) from fm_settlements s
     where s.chain_status = 'confirmed' and s.payment_id not like 'sim:%%'
       and (s.created_at at time zone 'Asia/Seoul')::date = d) as settlement_amount_krw
from days order by d
"""

DISTRIBUTION_SQL = """
select
  (select count(*) from fm_models where status = 'pending') as models_pending,
  (select count(*) from fm_models where status = 'verified') as models_verified,
  (select count(*) from fm_models where status = 'suspended') as models_suspended,
  (select count(*) from fm_biometric_enrollments where status = 'passed') as enrollments_passed,
  (select count(*) from fm_biometric_enrollments
     where status in ('failed', 'cancelled', 'expired')) as enrollments_failed,
  (select count(*) from fm_biometric_enrollments
     where status not in ('passed', 'failed', 'cancelled', 'expired')) as enrollments_in_flight
"""


async def build_overview(conn, *, days: int) -> dict:
    from_ts = _period_start(days)
    async with conn.cursor() as cur:
        await cur.execute(QUEUE_SQL)
        queue = await cur.fetchone() or {}
        await cur.execute(KPI_SQL, {"from_ts": from_ts})
        kpi = await cur.fetchone() or {}
        await cur.execute(SERIES_SQL, {"days": days})
        series = await cur.fetchall() or []
        await cur.execute(DISTRIBUTION_SQL)
        dist = await cur.fetchone() or {}

    return {
        "queue": {
            "applicationsUnderReview": queue.get("applications_under_review", 0),
            "identityMismatch": queue.get("identity_mismatch", 0),
            "emailFailed": queue.get("email_failed", 0),
            "refundsPending": queue.get("refunds_pending", 0),
        },
        "period": {"days": days, "from": from_ts.isoformat()},
        "kpi": {
            "applicationsSubmitted": kpi.get("applications_submitted", 0),
            "applicationsApproved": kpi.get("applications_approved", 0),
            "applicationsRejected": kpi.get("applications_rejected", 0),
            "licensesIssued": kpi.get("licenses_issued", 0),
            "settlementAmountKrw": int(kpi.get("settlement_amount_krw", 0)),
            "settlementFailed": kpi.get("settlement_failed", 0),
            "creditRevenueKrw": int(kpi.get("credit_revenue_krw", 0)),
        },
        "series": [
            {
                "date": row["date"],
                "applications": row["applications"],
                "licenses": row["licenses"],
                "settlementAmountKrw": int(row["settlement_amount_krw"]),
            }
            for row in series
        ],
        "distribution": {
            "models": {
                "pending": dist.get("models_pending", 0),
                "verified": dist.get("models_verified", 0),
                "suspended": dist.get("models_suspended", 0),
            },
            "enrollments": {
                "passed": dist.get("enrollments_passed", 0),
                "failed": dist.get("enrollments_failed", 0),
                "inFlight": dist.get("enrollments_in_flight", 0),
            },
        },
    }


def clear_overview_cache() -> None:
    _OVERVIEW_CACHE.clear()


async def overview_payload(conn, *, days: int) -> dict:
    hit = _OVERVIEW_CACHE.get(days)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    payload = await build_overview(conn, days=days)
    _OVERVIEW_CACHE[days] = (now + OVERVIEW_TTL_SECONDS, payload)
    return payload


@router.get("/overview")
async def admin_overview(
    request: Request,
    days: int = Query(30),
    user_id: str = Depends(require_user),
):
    """콘솔 첫 화면 한 벌 — 운영 큐 + 기간 KPI + 추이 + 분포. 호출 1회."""
    validate_days(days)
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        payload = await overview_payload(conn, days=days)
    return JSONResponse(payload)
```

- [ ] **Step 4: 라우터를 등록한다**

`server/app/main.py` 의 `if settings.facemarket_enabled:` 블록 안, `applications_router` 등록 바로 뒤:

```python
        # 관리자 콘솔(집계·모델 조회·권한 관리). 지원서 라우트와 같은 플래그 아래 산다.
        from .facemarket_admin import router as admin_console_router

        app.include_router(admin_console_router)
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd server && uv run pytest tests/test_admin_overview.py -q`
Expected: PASS (10 passed)

- [ ] **Step 6: 라우트가 실제로 붙었는지 확인한다**

```bash
cd server && uv run python -c "
from app.main import create_app
import os
os.environ.setdefault('FACEMARKET_ENABLED','1')
app = create_app()
print([r.path for r in app.routes if '/admin/' in getattr(r,'path','')])
"
```
Expected: `/v1/facemarket/admin/overview` 가 목록에 있다.

- [ ] **Step 7: 커밋**

```bash
git add server/app/facemarket_admin.py server/app/main.py server/tests/test_admin_overview.py
git commit -m "$(cat <<'MSG'
feat(admin): 대시보드 집계 API — 라이브 SQL + 30초 캐시

숫자의 정의를 코드에 못박았다. 시뮬 정산(payment_id 'sim:')과 테스트 결제(provider
'test')는 매출에서 뺀다 — 데모 TX 가 매출로 섞이면 그 숫자로 내린 결정이 틀린다.
날짜는 KST 로 자른다. 운영자가 말하는 '오늘'은 서울 기준이다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
)"
```

---

## Task 8: 대시보드 화면

**Files:**
- Modify: `src/features/admin/AdminDashboard.jsx` (자리표시 → 본체)
- Create: `src/features/admin/Sparkline.jsx`
- Modify: `src/lib/api/facemarket.js`
- Test: `tests/frontend/admin-dashboard.test.mjs`

**Interfaces:**
- Consumes: Task 7 의 `GET /admin/overview`, Task 5 의 `admin-ui`
- Produces: `adminOverview(days)` — `src/lib/api/facemarket.js`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/frontend/admin-dashboard.test.mjs`:

```javascript
/* 대시보드 계약 — 큐 숫자가 목록으로 이어지는지, 기간 토글이 서버 허용값만 쓰는지. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('api 클라이언트에 adminOverview 가 있다', () => {
  const api = read('src/lib/api/facemarket.js');
  assert.ok(api.includes('export function adminOverview'));
  assert.ok(api.includes('/v1/facemarket/admin/overview'));
});

test('기간 토글은 서버 허용값(7·30·90)만 낸다', () => {
  const source = read('src/features/admin/AdminDashboard.jsx');
  const periods = source.match(/PERIODS\s*=\s*\[([^\]]+)\]/);
  assert.ok(periods, 'PERIODS 상수가 없다');
  assert.deepEqual(
    periods[1].match(/\d+/g).map(Number).sort((a, b) => a - b),
    [7, 30, 90],
  );
});

test('큐 카드는 처리 화면으로 이어진다', () => {
  const source = read('src/features/admin/AdminDashboard.jsx');
  assert.ok(source.includes('/applications?status=under_review'), '검토 대기가 목록으로 안 이어진다');
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pnpm test:frontend`
Expected: FAIL — `adminOverview` 없음

- [ ] **Step 3: API 클라이언트를 넓힌다**

`src/lib/api/facemarket.js` 의 관리자 절 끝에:

```javascript
// ── 관리자 콘솔: 집계·모델·권한 ─────────────────────────────────────────────
// 전부 서버가 admin_guard.require_admin 을 강제한다(비관리자는 403).

export function adminOverview(days = 30) {
  return http(`/v1/facemarket/admin/overview?days=${encodeURIComponent(days)}`);
}
```

- [ ] **Step 4: 꺾은선을 만든다**

`src/features/admin/Sparkline.jsx`:

```jsx
/* 의존성 없는 꺾은선. 차트가 두 종류뿐이라 Recharts 를 admin 번들에 넣지 않는다.

   값이 전부 0 이면(초기 서비스에서 흔하다) 바닥에 붙은 직선을 그린다 — 0으로 나누지 않게. */
export function Sparkline({ points, height = 48, label }) {
  const values = points.map((p) => p.value);
  const max = Math.max(1, ...values);
  const width = Math.max(points.length - 1, 1);
  const d = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${i} ${height - (p.value / max) * height}`)
    .join(' ');
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="h-12 w-full"
      role="img"
      aria-label={label}
    >
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
```

- [ ] **Step 5: 대시보드를 만든다**

`src/features/admin/AdminDashboard.jsx`:

```jsx
/* 콘솔 첫 화면 — 손댈 일 → 기간 지표 → 추이·분포.

   순서가 곧 용도다. 관리자가 이 화면을 여는 첫 이유는 "내가 처리해야 할 게 있나"이고,
   숫자 구경은 그다음이다. 큐 카드는 전부 목록 화면으로 이어진다 — 보여주고 끝나면
   결국 다른 화면을 다시 찾아 들어가야 한다. */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { adminOverview } from '@/lib/api/facemarket.js';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/admin-ui/card.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import { Sparkline } from './Sparkline.jsx';

const PERIODS = [7, 30, 90];

const won = (n) => `${Number(n || 0).toLocaleString('ko-KR')}원`;

function QueueCard({ label, count, to, tone = 'default' }) {
  const idle = !count;
  const body = (
    <Card className={idle ? 'opacity-60' : ''}>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className={`text-2xl ${!idle && tone === 'alert' ? 'text-destructive' : ''}`}>
          {count ?? 0}
        </CardTitle>
      </CardHeader>
    </Card>
  );
  return idle ? body : <Link to={to} className="block">{body}</Link>;
}

function Stat({ label, value }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-xl">{value}</CardTitle>
      </CardHeader>
    </Card>
  );
}

export function AdminDashboard() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    adminOverview(days)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e.message || '불러오지 못했어요.'); });
    return () => { alive = false; };
  }, [days]);

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>대시보드를 불러오지 못했어요</CardTitle>
          <CardDescription>{error}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="outline" onClick={() => setDays((d) => d)}>다시 시도</Button>
        </CardContent>
      </Card>
    );
  }

  if (!data) {
    return (
      <div className="grid gap-3 sm:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-24" />)}
      </div>
    );
  }

  const { queue, kpi, series, distribution } = data;

  return (
    <div className="flex flex-col gap-8">
      <section>
        <h2 className="mb-3 text-sm font-medium text-muted-foreground">손댈 일</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <QueueCard label="검토 대기 지원서" count={queue.applicationsUnderReview} to="/applications?status=under_review" />
          <QueueCard label="신분증 대조 실패" count={queue.identityMismatch} to="/applications?status=under_review" tone="alert" />
          <QueueCard label="결정 메일 미발송" count={queue.emailFailed} to="/applications?status=approved" tone="alert" />
          <QueueCard label="환불 요청 대기" count={queue.refundsPending} to="/applications" />
        </div>
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium text-muted-foreground">지표</h2>
          <div className="flex gap-1">
            {PERIODS.map((p) => (
              <Button key={p} size="sm" variant={p === days ? 'default' : 'outline'} onClick={() => setDays(p)}>
                {p}일
              </Button>
            ))}
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="신규 지원서" value={kpi.applicationsSubmitted} />
          <Stat label="승인 / 거절" value={`${kpi.applicationsApproved} / ${kpi.applicationsRejected}`} />
          <Stat label="라이선스 발급" value={kpi.licensesIssued} />
          <Stat label="크레딧 결제 매출" value={won(kpi.creditRevenueKrw)} />
          <Stat label="정산 금액" value={won(kpi.settlementAmountKrw)} />
          <Stat label="정산 실패" value={kpi.settlementFailed} />
          <Stat label="검증된 모델" value={distribution.models.verified} />
          <Stat label="생체등록 진행 중" value={distribution.enrollments.inFlight} />
        </div>
      </section>

      <section className="grid gap-3 lg:grid-cols-3">
        <Card>
          <CardHeader><CardDescription>일별 지원서</CardDescription></CardHeader>
          <CardContent>
            <Sparkline label="일별 지원서" points={series.map((s) => ({ value: s.applications }))} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardDescription>일별 라이선스 발급</CardDescription></CardHeader>
          <CardContent>
            <Sparkline label="일별 라이선스 발급" points={series.map((s) => ({ value: s.licenses }))} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardDescription>일별 정산액</CardDescription></CardHeader>
          <CardContent>
            <Sparkline label="일별 정산액" points={series.map((s) => ({ value: s.settlementAmountKrw }))} />
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-3 sm:grid-cols-2">
        <Card>
          <CardHeader><CardDescription>모델 상태</CardDescription></CardHeader>
          <CardContent className="flex gap-6 text-sm">
            <span>대기 {distribution.models.pending}</span>
            <span>검증됨 {distribution.models.verified}</span>
            <span>정지 {distribution.models.suspended}</span>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardDescription>생체등록</CardDescription></CardHeader>
          <CardContent className="flex gap-6 text-sm">
            <span>통과 {distribution.enrollments.passed}</span>
            <span>진행 중 {distribution.enrollments.inFlight}</span>
            <span>실패·만료 {distribution.enrollments.failed}</span>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
```

- [ ] **Step 6: 지원서 화면이 status 쿼리를 받게 한다**

`AdminApplications.jsx` 의 필터 초기값을 URL 에서 읽는다(큐 카드가 이어지는 곳):

```jsx
import { useSearchParams } from 'react-router-dom';
```

```jsx
  const [searchParams] = useSearchParams();
  const [status, setStatus] = useState(searchParams.get('status') || 'under_review');
```

- [ ] **Step 7: 통과를 확인한다**

Run: `pnpm test:frontend && pnpm build`
Expected: PASS + 빌드 성공

- [ ] **Step 8: 커밋**

```bash
git add src/features/admin/AdminDashboard.jsx src/features/admin/Sparkline.jsx \
        src/features/admin/AdminApplications.jsx src/lib/api/facemarket.js \
        tests/frontend/admin-dashboard.test.mjs
git commit -m "$(cat <<'MSG'
feat(admin): 대시보드 — 손댈 일·기간 지표·추이

큐 카드는 전부 목록으로 이어진다. 숫자만 보여 주면 결국 다른 화면을 다시 찾아 들어가야
한다. 차트는 종류가 둘뿐이라 Recharts 대신 인라인 SVG 로 그렸다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
)"
```

---

## Task 9: 모델 목록·상세 API

**Files:**
- Modify: `server/app/facemarket_admin.py`
- Test: `server/tests/test_admin_models.py`

**Interfaces:**
- Consumes: Task 7 의 라우터·`_err`
- Produces:
  - `GET /v1/facemarket/admin/models?q=&status=&limit=` → `{"items": [...]}`
  - `GET /v1/facemarket/admin/models/{model_id}` → `{"model": {...}, "licenses": [...], "settlements": [...], "enrollment": {...} | null}`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`server/tests/test_admin_models.py`:

```python
"""관리자 모델 조회 — 검색·필터·상세의 SQL 계약."""
import asyncio
import contextlib

import pytest

from app import facemarket_admin


class FakeCursor:
    def __init__(self, store, rows):
        self.store, self.rows, self._row = store, rows, None

    async def execute(self, sql, params=None):
        self.store.append((" ".join(sql.split()), params))
        self._row = self.rows.pop(0) if self.rows else []

    async def fetchone(self):
        return self._row if isinstance(self._row, dict) else None

    async def fetchall(self):
        return self._row if isinstance(self._row, list) else []


class FakeConn:
    def __init__(self, rows):
        self.executed, self.rows = [], list(rows)

    def cursor(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeCursor(self.executed, self.rows)

        return _cm()


MODEL_ROW = {
    "id": "m1", "display_name": "모델 A", "status": "verified", "email": "a@example.com",
    "license_count": 2, "last_settlement_at": None, "created_at": None,
}


def test_status_filter_rejects_unknown_value():
    with pytest.raises(Exception) as exc:
        facemarket_admin.validate_model_status("deleted")
    assert exc.value.detail["code"] == "invalid_status"


def test_status_filter_accepts_schema_values():
    for status in ("pending", "verified", "suspended"):
        assert facemarket_admin.validate_model_status(status) == status


def test_list_matches_name_partially_and_email_exactly():
    conn = FakeConn([[MODEL_ROW]])
    asyncio.run(facemarket_admin.list_models(conn, q="모델", status=None, limit=50))
    sql, params = conn.executed[0]
    assert "ilike" in sql, "이름 부분일치가 없다"
    assert "u.email = " in sql, "이메일 정확일치가 없다"
    assert any("%모델%" == p for p in params.values()), "부분일치 패턴이 안 붙었다"


def test_list_joins_auth_users_for_email():
    conn = FakeConn([[MODEL_ROW]])
    asyncio.run(facemarket_admin.list_models(conn, q=None, status=None, limit=50))
    sql, _ = conn.executed[0]
    assert "auth.users" in sql
    assert "left join" in sql, "계정 없는 모델(플랫폼 대행 온보딩)이 목록에서 사라지면 안 된다"


def test_list_caps_limit():
    conn = FakeConn([[MODEL_ROW]])
    asyncio.run(facemarket_admin.list_models(conn, q=None, status=None, limit=9999))
    _sql, params = conn.executed[0]
    assert params["limit"] <= facemarket_admin.MAX_LIST_LIMIT


def test_detail_returns_licenses_settlements_and_enrollment():
    conn = FakeConn([
        MODEL_ROW,
        [{"id": "l1", "status": "active", "unit_price": 10000, "license_valid_until": None, "vc_id": None}],
        [{"id": "s1", "total_amount": 10000, "chain_status": "confirmed", "created_at": None, "tx_hash": None}],
        {"id": "e1", "status": "passed", "completed_at": None},
    ])
    payload = asyncio.run(facemarket_admin.model_detail(conn, model_id="m1"))
    assert payload["model"]["displayName"] == "모델 A"
    assert payload["licenses"][0]["unitPrice"] == 10000
    assert payload["settlements"][0]["chainStatus"] == "confirmed"
    assert payload["enrollment"]["status"] == "passed"


def test_detail_404_for_unknown_model():
    conn = FakeConn([None])
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.model_detail(conn, model_id="nope"))
    assert exc.value.status_code == 404
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd server && uv run pytest tests/test_admin_models.py -q`
Expected: FAIL — `AttributeError: module 'app.facemarket_admin' has no attribute 'validate_model_status'`

- [ ] **Step 3: 구현한다**

`server/app/facemarket_admin.py` 에 이어 붙인다:

```python
MODEL_STATUSES = ("pending", "verified", "suspended")
MAX_LIST_LIMIT = 200


def validate_model_status(status: str | None) -> str | None:
    if status is not None and status not in MODEL_STATUSES:
        raise _err("invalid_status", "상태 필터가 올바르지 않습니다.")
    return status


LIST_MODELS_SQL = """
select m.id::text as id, m.display_name, m.status, m.created_at,
       u.email as email,
       (select count(*) from fm_licenses l where l.model_id = m.id) as license_count,
       (select max(s.created_at) from fm_settlements s
          join fm_licenses l2 on l2.id = s.license_id
         where l2.model_id = m.id) as last_settlement_at
from fm_models m
-- left join: 플랫폼 대행 온보딩은 user_id 가 null 이다(fm_models 주석). inner 로 묶으면
-- 그런 모델이 목록에서 통째로 사라져, 없는 걸 없다고 착각한다.
left join auth.users u on u.id = m.user_id
where (%(status)s::text is null or m.status = %(status)s)
  and (
    %(q)s::text is null
    or m.display_name ilike %(q_like)s
    or u.email = %(q)s
  )
order by m.created_at desc
limit %(limit)s
"""


def _model_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "displayName": row["display_name"],
        "status": row["status"],
        "email": row.get("email"),
        "licenseCount": row.get("license_count", 0),
        "lastSettlementAt": row["last_settlement_at"].isoformat() if row.get("last_settlement_at") else None,
        "createdAt": row["created_at"].isoformat() if row.get("created_at") else None,
    }


async def list_models(conn, *, q: str | None, status: str | None, limit: int) -> dict:
    capped = max(1, min(limit, MAX_LIST_LIMIT))
    term = (q or "").strip() or None
    async with conn.cursor() as cur:
        await cur.execute(LIST_MODELS_SQL, {
            "status": status, "q": term, "q_like": f"%{term}%" if term else None, "limit": capped,
        })
        rows = await cur.fetchall() or []
    return {"items": [_model_row(r) for r in rows]}


DETAIL_MODEL_SQL = LIST_MODELS_SQL.split("where")[0] + " where m.id = %(model_id)s"

DETAIL_LICENSES_SQL = """
select id::text as id, status, unit_price, license_valid_until, vc_id
from fm_licenses where model_id = %(model_id)s order by created_at desc
"""

DETAIL_SETTLEMENTS_SQL = """
select s.id::text as id, s.total_amount, s.chain_status, s.tx_hash, s.created_at
from fm_settlements s join fm_licenses l on l.id = s.license_id
where l.model_id = %(model_id)s order by s.created_at desc limit 10
"""

DETAIL_ENROLLMENT_SQL = """
select id::text as id, status, completed_at
from fm_biometric_enrollments where model_id = %(model_id)s
order by created_at desc limit 1
"""


async def model_detail(conn, *, model_id: str) -> dict:
    params = {"model_id": model_id}
    async with conn.cursor() as cur:
        await cur.execute(DETAIL_MODEL_SQL, params)
        model = await cur.fetchone()
        if model is None:
            raise _err("not_found", "모델을 찾을 수 없어요.", status=404)
        await cur.execute(DETAIL_LICENSES_SQL, params)
        licenses = await cur.fetchall() or []
        await cur.execute(DETAIL_SETTLEMENTS_SQL, params)
        settlements = await cur.fetchall() or []
        await cur.execute(DETAIL_ENROLLMENT_SQL, params)
        enrollment = await cur.fetchone()

    return {
        "model": _model_row(model),
        "licenses": [
            {
                "id": r["id"], "status": r["status"], "unitPrice": r["unit_price"],
                "validUntil": r["license_valid_until"].isoformat() if r.get("license_valid_until") else None,
                "vcId": r.get("vc_id"),
            }
            for r in licenses
        ],
        "settlements": [
            {
                "id": r["id"], "totalAmount": int(r["total_amount"]), "chainStatus": r["chain_status"],
                "txHash": r.get("tx_hash"),
                "createdAt": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in settlements
        ],
        "enrollment": (
            {
                "id": enrollment["id"], "status": enrollment["status"],
                "completedAt": enrollment["completed_at"].isoformat() if enrollment.get("completed_at") else None,
            }
            if enrollment else None
        ),
    }


@router.get("/models")
async def admin_list_models(
    request: Request,
    q: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50),
    user_id: str = Depends(require_user),
):
    validate_model_status(status)
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        return JSONResponse(await list_models(conn, q=q, status=status, limit=limit))


@router.get("/models/{model_id}")
async def admin_model_detail(
    request: Request, model_id: str, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        return JSONResponse(await model_detail(conn, model_id=model_id))
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd server && uv run pytest tests/test_admin_models.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: `auth.users` 읽기 권한을 실측한다 (설계 §12-1)**

로컬 백엔드를 띄우고(런북: `docs/` 로컬 dev 절차, `.env.local` export 필수) 관리자 토큰으로:

```bash
curl -s -H "Authorization: Bearer $ADMIN_JWT" 'http://localhost:8000/v1/facemarket/admin/models?limit=5' | head -c 400
```

Expected: 200 + `items` 배열. `permission denied for table users` 가 나오면 **여기서 멈추고** 설계 §6 의 대안(`profiles` 이메일 미러 + `handle_new_user` 트리거 수정)으로 전환한다 — 그 경우 이 태스크에 마이그레이션 1개와 백필 UPDATE 가 추가된다.

- [ ] **Step 6: 커밋**

```bash
git add server/app/facemarket_admin.py server/tests/test_admin_models.py
git commit -m "$(cat <<'MSG'
feat(admin): 모델 목록·상세 조회

계정 이메일은 auth.users 를 left join 해서 읽는다. inner 로 묶으면 플랫폼 대행
온보딩(user_id null)한 모델이 목록에서 통째로 사라진다 — 없는 걸 없다고 착각하게 된다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
)"
```

---

## Task 10: 모델 정지·정지 해제

**Files:**
- Modify: `server/app/facemarket_admin.py`
- Test: `server/tests/test_admin_model_suspend.py`

**Interfaces:**
- Consumes: Task 9 의 `model_detail`, Task 2 의 `write_audit`
- Produces:
  - `POST /v1/facemarket/admin/models/{model_id}/suspend` body `{"reason": "..."}`
  - `POST /v1/facemarket/admin/models/{model_id}/unsuspend`
  - 감사 액션 `model.suspend` / `model.unsuspend`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`server/tests/test_admin_model_suspend.py`:

```python
"""모델 정지·해제 — 사유 필수, 그리고 콘솔이 verified 를 창조하지 못하는지.

되돌아가면: 관리자가 손으로 '검증됨' 배지를 붙일 수 있게 된다. 그 순간 배지는
생체등록을 통과했다는 뜻이 아니라 누군가 눌렀다는 뜻이 되어, 라이선스 신뢰의 근거가 없다.
"""
import asyncio
import contextlib

import pytest

from app import facemarket_admin


class FakeCursor:
    def __init__(self, store, rows):
        self.store, self.rows, self._row = store, rows, None

    async def execute(self, sql, params=None):
        self.store.append((" ".join(sql.split()), params))
        self._row = self.rows.pop(0) if self.rows else None

    async def fetchone(self):
        return self._row if isinstance(self._row, dict) else None

    async def fetchall(self):
        return self._row if isinstance(self._row, list) else []


class FakeConn:
    def __init__(self, rows):
        self.executed, self.rows, self.commits = [], list(rows), 0

    def cursor(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeCursor(self.executed, self.rows)

        return _cm()

    async def commit(self):
        self.commits += 1


def test_suspend_requires_a_reason():
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.suspend_model(
            FakeConn([]), model_id="m1", actor="admin-1", reason="   ",
        ))
    assert exc.value.detail["code"] == "reason_required"


def test_suspend_records_previous_status_in_audit():
    conn = FakeConn([{"status": "verified"}, None])
    asyncio.run(facemarket_admin.suspend_model(
        conn, model_id="m1", actor="admin-1", reason="본인 요청",
    ))
    audit = [p for sql, p in conn.executed if sql.startswith("insert into admin_audit_log")]
    assert audit, "감사 기록이 없다"
    params = audit[0]
    assert params[1] == "model.suspend"
    assert params[4].obj == {"status": "verified"}, "정지 직전 상태가 안 남으면 복원할 수 없다"
    assert params[6] == "본인 요청"


def test_suspend_404_for_unknown_model():
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.suspend_model(
            FakeConn([None]), model_id="nope", actor="admin-1", reason="x",
        ))
    assert exc.value.status_code == 404


def test_unsuspend_restores_the_status_recorded_at_suspension():
    # 1) 현재 상태 조회 → suspended, 2) 마지막 suspend 기록 → verified
    conn = FakeConn([{"status": "suspended"}, {"prev": "verified"}, None])
    asyncio.run(facemarket_admin.unsuspend_model(conn, model_id="m1", actor="admin-1"))
    updates = [p for sql, p in conn.executed if sql.startswith("update fm_models")]
    assert updates and updates[0][0] == "verified"


def test_unsuspend_falls_back_to_pending_without_audit_history():
    conn = FakeConn([{"status": "suspended"}, None, None])
    asyncio.run(facemarket_admin.unsuspend_model(conn, model_id="m1", actor="admin-1"))
    updates = [p for sql, p in conn.executed if sql.startswith("update fm_models")]
    assert updates and updates[0][0] == "pending"


def test_unsuspend_never_restores_a_status_outside_the_schema():
    """원장 값이 오염됐어도 스키마 밖 상태를 쓰지 않는다(check 제약 위반 → 500)."""
    conn = FakeConn([{"status": "suspended"}, {"prev": "superadmin"}, None])
    asyncio.run(facemarket_admin.unsuspend_model(conn, model_id="m1", actor="admin-1"))
    updates = [p for sql, p in conn.executed if sql.startswith("update fm_models")]
    assert updates and updates[0][0] == "pending"


def test_unsuspend_rejects_a_model_that_is_not_suspended():
    conn = FakeConn([{"status": "verified"}])
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.unsuspend_model(conn, model_id="m1", actor="admin-1"))
    assert exc.value.detail["code"] == "not_suspended"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd server && uv run pytest tests/test_admin_model_suspend.py -q`
Expected: FAIL — `has no attribute 'suspend_model'`

- [ ] **Step 3: 구현한다**

`server/app/facemarket_admin.py` 에 이어 붙인다:

```python
class SuspendRequest(CamelModel):
    reason: str


async def _model_status(cur, model_id: str) -> str:
    await cur.execute("select status from fm_models where id = %s", (model_id,))
    row = await cur.fetchone()
    if row is None:
        raise _err("not_found", "모델을 찾을 수 없어요.", status=404)
    return row["status"]


async def suspend_model(conn, *, model_id: str, actor: str, reason: str) -> dict:
    note = (reason or "").strip()
    if not note:
        raise _err("reason_required", "정지 사유를 입력해 주세요.")
    async with conn.cursor() as cur:
        previous = await _model_status(cur, model_id)
        await cur.execute(
            "update fm_models set status = 'suspended', updated_at = now() where id = %s",
            (model_id,),
        )
    await admin_guard.write_audit(
        conn,
        actor_user_id=actor,
        action="model.suspend",
        target_type="model",
        target_id=model_id,
        before={"status": previous},
        after={"status": "suspended"},
        note=note,
    )
    return {"id": model_id, "status": "suspended"}


async def unsuspend_model(conn, *, model_id: str, actor: str) -> dict:
    """정지 직전 상태로 되돌린다.

    콘솔은 verified 를 **새로 만들지 못한다** — 그 배지는 생체등록 통과가 붙이는 것이다.
    다만 정지 한 번으로 검증된 모델이 배지를 영구히 잃는 것도 틀렸다. 그래서 원장에 남은
    정지 직전 값만 복원한다. 기록이 없으면(콘솔 밖에서 정지된 경우) pending 으로 내린다.
    """
    async with conn.cursor() as cur:
        current = await _model_status(cur, model_id)
        if current != "suspended":
            raise _err("not_suspended", "정지 상태인 모델만 해제할 수 있어요.")
        await cur.execute(
            "select before->>'status' as prev from admin_audit_log "
            "where action = 'model.suspend' and target_type = 'model' and target_id = %s "
            "order by created_at desc limit 1",
            (model_id,),
        )
        row = await cur.fetchone()
        restored = (row or {}).get("prev")
        # 원장 값이 오염됐거나 기록이 없으면 pending — 스키마 밖 값을 넣으면 check 제약이 터진다.
        if restored not in ("pending", "verified"):
            restored = "pending"
        await cur.execute(
            "update fm_models set status = %s, updated_at = now() where id = %s",
            (restored, model_id),
        )
    await admin_guard.write_audit(
        conn,
        actor_user_id=actor,
        action="model.unsuspend",
        target_type="model",
        target_id=model_id,
        before={"status": "suspended"},
        after={"status": restored},
    )
    return {"id": model_id, "status": restored}


@router.post("/models/{model_id}/suspend")
async def admin_suspend_model(
    request: Request, model_id: str, body: SuspendRequest,
    user_id: str = Depends(require_user),
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        result = await suspend_model(conn, model_id=model_id, actor=user_id, reason=body.reason)
        await conn.commit()
    return JSONResponse(result)


@router.post("/models/{model_id}/unsuspend")
async def admin_unsuspend_model(
    request: Request, model_id: str, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        result = await unsuspend_model(conn, model_id=model_id, actor=user_id)
        await conn.commit()
    return JSONResponse(result)
```

import 절에 `from .models import CamelModel` 을 더한다.

- [ ] **Step 4: 통과를 확인한다**

Run: `cd server && uv run pytest tests/test_admin_model_suspend.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add server/app/facemarket_admin.py server/tests/test_admin_model_suspend.py
git commit -m "$(cat <<'MSG'
feat(admin): 모델 정지·해제 — 해제는 원장의 정지 직전 상태로 복원

콘솔이 verified 를 새로 만들면 그 배지는 "생체등록을 통과했다"가 아니라 "누가 눌렀다"가
된다. 그래서 해제는 창조가 아니라 복원이다 — 원장에 남은 값만, 스키마 안 값일 때만.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
)"
```

---

## Task 11: 모델·유저 화면

**Files:**
- Modify: `src/features/admin/AdminModels.jsx` (자리표시 → 본체)
- Modify: `src/lib/api/facemarket.js`
- Test: `tests/frontend/admin-models.test.mjs`

**Interfaces:**
- Consumes: Task 9·10 의 라우트
- Produces: `adminListModels({q, status})`, `adminModelDetail(id)`, `adminSuspendModel(id, reason)`, `adminUnsuspendModel(id)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/frontend/admin-models.test.mjs`:

```javascript
/* 모델 화면 계약 — 정지에 사유를 강제하는지, 상세가 네 블록을 다 내는지. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('api 클라이언트에 모델 함수 네 개가 있다', () => {
  const api = read('src/lib/api/facemarket.js');
  for (const fn of ['adminListModels', 'adminModelDetail', 'adminSuspendModel', 'adminUnsuspendModel']) {
    assert.ok(api.includes(`export function ${fn}`), `누락: ${fn}`);
  }
});

test('사유가 비면 정지 버튼이 비활성이다', () => {
  const source = read('src/features/admin/AdminModels.jsx');
  assert.ok(/disabled=\{[^}]*!reason\.trim\(\)/.test(source), '빈 사유로 정지가 눌린다');
});

test('상세는 라이선스·정산·생체등록을 모두 보여준다', () => {
  const source = read('src/features/admin/AdminModels.jsx');
  for (const label of ['라이선스', '정산', '생체등록']) {
    assert.ok(source.includes(label), `상세 블록 누락: ${label}`);
  }
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pnpm test:frontend`
Expected: FAIL — `누락: adminListModels`

- [ ] **Step 3: API 클라이언트를 넓힌다**

`src/lib/api/facemarket.js`:

```javascript
export function adminListModels({ q, status, limit = 50 } = {}) {
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  if (status) params.set('status', status);
  params.set('limit', String(limit));
  return http(`/v1/facemarket/admin/models?${params.toString()}`);
}

export function adminModelDetail(modelId) {
  return http(`/v1/facemarket/admin/models/${encodeURIComponent(modelId)}`);
}

export function adminSuspendModel(modelId, reason) {
  return http(`/v1/facemarket/admin/models/${encodeURIComponent(modelId)}/suspend`, {
    method: 'POST', body: { reason },
  });
}

export function adminUnsuspendModel(modelId) {
  return http(`/v1/facemarket/admin/models/${encodeURIComponent(modelId)}/unsuspend`, {
    method: 'POST',
  });
}
```

- [ ] **Step 4: 화면을 만든다**

`src/features/admin/AdminModels.jsx`:

```jsx
/* 모델·유저 — 검색·필터 표 + 선택 행 상세.

   상세를 별도 라우트가 아니라 같은 화면 오른쪽에 붙인다. 운영자는 "이 모델 뭐지"를 확인하고
   목록으로 곧장 돌아온다 — 라우트를 갈면 그 왕복마다 목록이 다시 로드되고 스크롤을 잃는다. */
import { useCallback, useEffect, useState } from 'react';
import {
  adminListModels, adminModelDetail, adminSuspendModel, adminUnsuspendModel,
} from '@/lib/api/facemarket.js';
import { Badge } from '@/components/admin-ui/badge.jsx';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/admin-ui/card.jsx';
import { Input } from '@/components/admin-ui/input.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/admin-ui/table.jsx';
import { useToast } from '@/components/ui.jsx';

const STATUS_FILTERS = [
  { value: '', label: '전체' },
  { value: 'pending', label: '대기' },
  { value: 'verified', label: '검증됨' },
  { value: 'suspended', label: '정지' },
];
const STATUS_LABEL = { pending: '대기', verified: '검증됨', suspended: '정지' };
const STATUS_VARIANT = { pending: 'secondary', verified: 'default', suspended: 'destructive' };
const won = (n) => `${Number(n || 0).toLocaleString('ko-KR')}원`;
const day = (iso) => (iso ? iso.slice(0, 10) : '-');

function Detail({ modelId, onChanged }) {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setData(null);
    adminModelDetail(modelId).then(setData).catch((e) => toast?.show?.(e.message));
  }, [modelId, toast]);

  useEffect(() => { load(); }, [load]);

  if (!data) return <Skeleton className="h-64" />;

  const { model, licenses, settlements, enrollment } = data;
  const suspended = model.status === 'suspended';

  const act = async (fn) => {
    setBusy(true);
    try {
      await fn();
      setReason('');
      load();
      onChanged?.();
    } catch (e) {
      toast?.show?.(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CardTitle className="text-base">{model.displayName}</CardTitle>
          <Badge variant={STATUS_VARIANT[model.status]}>{STATUS_LABEL[model.status]}</Badge>
        </div>
        <CardDescription>{model.email || '연결된 계정 없음 (플랫폼 온보딩)'}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5 text-sm">
        <section>
          <h4 className="mb-1 text-xs font-medium text-muted-foreground">라이선스 {licenses.length}건</h4>
          {licenses.length === 0 && <p className="text-muted-foreground">없음</p>}
          {licenses.map((l) => (
            <div key={l.id} className="flex gap-3">
              <span>{l.status}</span><span>{won(l.unitPrice)}</span><span>~{day(l.validUntil)}</span>
            </div>
          ))}
        </section>
        <section>
          <h4 className="mb-1 text-xs font-medium text-muted-foreground">최근 정산</h4>
          {settlements.length === 0 && <p className="text-muted-foreground">없음</p>}
          {settlements.map((s) => (
            <div key={s.id} className="flex gap-3">
              <span>{day(s.createdAt)}</span><span>{won(s.totalAmount)}</span><span>{s.chainStatus}</span>
            </div>
          ))}
        </section>
        <section>
          <h4 className="mb-1 text-xs font-medium text-muted-foreground">생체등록</h4>
          <p>{enrollment ? `${enrollment.status} · ${day(enrollment.completedAt)}` : '기록 없음'}</p>
        </section>
        <section className="border-t border-border pt-4">
          {suspended ? (
            <Button variant="outline" disabled={busy} onClick={() => act(() => adminUnsuspendModel(model.id))}>
              정지 해제 (정지 직전 상태로 되돌아가요)
            </Button>
          ) : (
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="정지 사유 (기록에 남아요)"
                className="sm:flex-1"
              />
              <Button
                variant="destructive"
                disabled={busy || !reason.trim()}
                onClick={() => act(() => adminSuspendModel(model.id, reason.trim()))}
              >
                정지
              </Button>
            </div>
          )}
        </section>
      </CardContent>
    </Card>
  );
}

export function AdminModels() {
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('');
  const [items, setItems] = useState(null);
  const [selected, setSelected] = useState(null);

  const load = useCallback(() => {
    setItems(null);
    adminListModels({ q: q.trim(), status }).then((d) => setItems(d.items)).catch(() => setItems([]));
  }, [q, status]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="모델명 또는 계정 이메일"
          className="w-64"
        />
        {STATUS_FILTERS.map((f) => (
          <Button key={f.value} size="sm" variant={f.value === status ? 'default' : 'outline'} onClick={() => setStatus(f.value)}>
            {f.label}
          </Button>
        ))}
      </div>

      <div className="grid gap-5 lg:grid-cols-[1fr_24rem]">
        <Card>
          <CardContent className="p-0">
            {!items && <Skeleton className="h-64" />}
            {items && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>모델</TableHead>
                    <TableHead>상태</TableHead>
                    <TableHead>계정</TableHead>
                    <TableHead>라이선스</TableHead>
                    <TableHead>최근 정산</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((m) => (
                    <TableRow
                      key={m.id}
                      onClick={() => setSelected(m.id)}
                      className={`cursor-pointer ${selected === m.id ? 'bg-muted' : ''}`}
                    >
                      <TableCell>{m.displayName}</TableCell>
                      <TableCell>
                        <Badge variant={STATUS_VARIANT[m.status]}>{STATUS_LABEL[m.status]}</Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground">{m.email || '-'}</TableCell>
                      <TableCell>{m.licenseCount}</TableCell>
                      <TableCell className="text-muted-foreground">{day(m.lastSettlementAt)}</TableCell>
                    </TableRow>
                  ))}
                  {items.length === 0 && (
                    <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">결과 없음</TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {selected && <Detail modelId={selected} onChanged={load} />}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: 통과를 확인한다**

Run: `pnpm test:frontend && pnpm build`
Expected: PASS + 빌드 성공

- [ ] **Step 6: 커밋**

```bash
git add src/features/admin/AdminModels.jsx src/lib/api/facemarket.js tests/frontend/admin-models.test.mjs
git commit -m "$(cat <<'MSG'
feat(admin): 모델·유저 목록과 상세, 정지·해제

상세를 별도 라우트가 아니라 목록 옆에 붙였다. 운영자는 확인하고 곧장 목록으로 돌아오는데,
라우트를 갈면 왕복마다 목록이 다시 로드되고 스크롤을 잃는다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
)"
```

---

## Task 12: 관리자 목록·권한 변경 API

**Files:**
- Modify: `server/app/facemarket_admin.py`
- Test: `server/tests/test_admin_staff.py`

**Interfaces:**
- Consumes: Task 2 의 `require_admin`·`write_audit`
- Produces:
  - `GET /v1/facemarket/admin/staff?q=` → `{"admins": [...], "matches": [...]}`
  - `POST /v1/facemarket/admin/staff/{user_id}/role` body `{"role": "admin"|"user"}`
  - `GET /v1/facemarket/admin/audit?limit=&targetType=&targetId=` → `{"items": [...]}`
  - 감사 액션 `staff.role.grant` / `staff.role.revoke`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`server/tests/test_admin_staff.py`:

```python
"""관리자 승격·회수의 안전장치 셋.

되돌아가면: 관리자가 자기 권한을 내려 콘솔에서 영영 잠기거나(복구는 DB 직접 UPDATE 뿐),
서로를 동시에 내려 관리자가 0명이 된다.
"""
import asyncio
import contextlib

import pytest

from app import facemarket_admin


class FakeCursor:
    def __init__(self, store, rows):
        self.store, self.rows, self._row = store, rows, None

    async def execute(self, sql, params=None):
        self.store.append((" ".join(sql.split()), params))
        self._row = self.rows.pop(0) if self.rows else None

    async def fetchone(self):
        return self._row if isinstance(self._row, dict) else None

    async def fetchall(self):
        return self._row if isinstance(self._row, list) else []


class FakeConn:
    def __init__(self, rows):
        self.executed, self.rows = [], list(rows)

    def cursor(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeCursor(self.executed, self.rows)

        return _cm()

    async def commit(self):
        return None


def test_role_value_must_be_admin_or_user():
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.set_role(
            FakeConn([]), target_user_id="u2", actor="admin-1", role="superadmin",
        ))
    assert exc.value.detail["code"] == "invalid_role"


def test_cannot_demote_self():
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.set_role(
            FakeConn([]), target_user_id="admin-1", actor="admin-1", role="user",
        ))
    assert exc.value.detail["code"] == "cannot_demote_self"


def test_cannot_demote_the_last_admin():
    # 1) 대상 조회 → 관리자, 2) 관리자 수 → 1
    conn = FakeConn([{"role": "admin"}, {"count": 1}])
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.set_role(
            conn, target_user_id="admin-2", actor="admin-1", role="user",
        ))
    assert exc.value.detail["code"] == "last_admin"


def test_last_admin_check_locks_the_rows():
    """잠금 없이 세면 두 관리자가 서로를 동시에 내려 0명이 된다."""
    conn = FakeConn([{"role": "admin"}, {"count": 2}, None])
    asyncio.run(facemarket_admin.set_role(
        conn, target_user_id="admin-2", actor="admin-1", role="user",
    ))
    counting = [sql for sql, _ in conn.executed if "count(" in sql]
    assert counting and "for update" in counting[0]


def test_cannot_promote_a_user_without_a_profile():
    conn = FakeConn([None])
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.set_role(
            conn, target_user_id="ghost", actor="admin-1", role="admin",
        ))
    assert exc.value.status_code == 404
    assert exc.value.detail["code"] == "user_not_found"


def test_promotion_updates_role_and_writes_audit():
    conn = FakeConn([{"role": "user"}, None])
    asyncio.run(facemarket_admin.set_role(
        conn, target_user_id="u2", actor="admin-1", role="admin",
    ))
    updates = [(sql, p) for sql, p in conn.executed if sql.startswith("update profiles")]
    assert updates and updates[0][1][0] == "admin"
    audit = [p for sql, p in conn.executed if sql.startswith("insert into admin_audit_log")]
    assert audit and audit[0][1] == "staff.role.grant"
    assert audit[0][4].obj == {"role": "user"}
    assert audit[0][5].obj == {"role": "admin"}


def test_demotion_audit_action_is_revoke():
    conn = FakeConn([{"role": "admin"}, {"count": 3}, None])
    asyncio.run(facemarket_admin.set_role(
        conn, target_user_id="admin-2", actor="admin-1", role="user",
    ))
    audit = [p for sql, p in conn.executed if sql.startswith("insert into admin_audit_log")]
    assert audit and audit[0][1] == "staff.role.revoke"


def test_staff_listing_returns_admins_and_search_matches():
    conn = FakeConn([
        [{"user_id": "admin-1", "email": "a@x.com", "display_name": "A", "role": "admin"}],
        [{"user_id": "u2", "email": "b@x.com", "display_name": "B", "role": "user"}],
    ])
    payload = asyncio.run(facemarket_admin.list_staff(conn, q="b@x.com"))
    assert payload["admins"][0]["email"] == "a@x.com"
    assert payload["matches"][0]["userId"] == "u2"


def test_staff_search_is_exact_email_match():
    """부분일치로 열면 관리자 승격 대상을 훑는 이메일 스캐너가 된다."""
    conn = FakeConn([[], []])
    asyncio.run(facemarket_admin.list_staff(conn, q="b@"))
    search = [sql for sql, _ in conn.executed if "u.email" in sql]
    assert search and "ilike" not in search[-1]


def test_audit_listing_is_newest_first_and_capped():
    conn = FakeConn([[]])
    asyncio.run(facemarket_admin.list_audit(conn, limit=9999, target_type=None, target_id=None))
    sql, params = conn.executed[0]
    assert "order by l.created_at desc" in sql
    assert params["limit"] <= facemarket_admin.MAX_LIST_LIMIT
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd server && uv run pytest tests/test_admin_staff.py -q`
Expected: FAIL — `has no attribute 'set_role'`

- [ ] **Step 3: 구현한다**

`server/app/facemarket_admin.py` 에 이어 붙인다:

```python
ROLES = ("admin", "user")


class RoleRequest(CamelModel):
    role: str


LIST_ADMINS_SQL = """
select p.user_id::text as user_id, p.display_name, p.role, u.email
from profiles p left join auth.users u on u.id = p.user_id
where p.role = 'admin' order by p.created_at
"""

SEARCH_USER_SQL = """
select p.user_id::text as user_id, p.display_name, p.role, u.email
from profiles p join auth.users u on u.id = p.user_id
where u.email = %(email)s limit 5
"""


def _staff_row(row: dict) -> dict:
    return {
        "userId": row["user_id"],
        "email": row.get("email"),
        "displayName": row.get("display_name"),
        "role": row.get("role") or "user",
    }


async def list_staff(conn, *, q: str | None) -> dict:
    """현재 관리자 + (이메일 정확일치) 검색 결과.

    검색을 부분일치로 열면 콘솔이 이메일 스캐너가 된다 — 관리자라도 가입자 목록을
    훑을 이유는 없다. 승격은 이미 이메일을 아는 사람에게 하는 일이다.
    """
    email = (q or "").strip().lower() or None
    async with conn.cursor() as cur:
        await cur.execute(LIST_ADMINS_SQL)
        admins = await cur.fetchall() or []
        matches = []
        if email:
            await cur.execute(SEARCH_USER_SQL, {"email": email})
            matches = await cur.fetchall() or []
    return {
        "admins": [_staff_row(r) for r in admins],
        "matches": [_staff_row(r) for r in matches],
    }


async def set_role(conn, *, target_user_id: str, actor: str, role: str) -> dict:
    if role not in ROLES:
        raise _err("invalid_role", "역할 값이 올바르지 않습니다.")
    # 가드 1: 자기 강등 금지 — 되돌릴 방법이 콘솔에 없다(DB 직접 UPDATE 뿐).
    if role == "user" and target_user_id == actor:
        raise _err("cannot_demote_self", "자기 자신의 관리자 권한은 내릴 수 없어요.")

    async with conn.cursor() as cur:
        await cur.execute(
            "select role from profiles where user_id = %s for update", (target_user_id,)
        )
        row = await cur.fetchone()
        # 가드 3: 미가입 계정 승격 금지 — 초대 흐름을 만들지 않기로 했다(설계 §4.1).
        if row is None:
            raise _err("user_not_found", "가입된 계정을 찾을 수 없어요.", status=404)
        previous = row.get("role") or "user"

        # 가드 2: 최후 관리자 강등 금지. count 를 for update 로 잠그지 않으면 두 관리자가
        # 서로를 동시에 내려 0명이 될 수 있다(둘 다 "나 말고 하나 더 있다"를 읽는다).
        if role == "user" and previous == "admin":
            await cur.execute(
                "select count(*) as count from (select 1 from profiles "
                "where role = 'admin' for update) as locked"
            )
            counted = await cur.fetchone() or {"count": 0}
            if counted["count"] <= 1:
                raise _err("last_admin", "마지막 관리자는 내릴 수 없어요.")

        await cur.execute(
            "update profiles set role = %s, updated_at = now() where user_id = %s",
            (role, target_user_id),
        )

    await admin_guard.write_audit(
        conn,
        actor_user_id=actor,
        action="staff.role.grant" if role == "admin" else "staff.role.revoke",
        target_type="user",
        target_id=target_user_id,
        before={"role": previous},
        after={"role": role},
    )
    return {"userId": target_user_id, "role": role}


LIST_AUDIT_SQL = """
select l.id::text as id, l.action, l.target_type, l.target_id, l.note, l.created_at,
       l.actor_user_id::text as actor_user_id, u.email as actor_email
from admin_audit_log l left join auth.users u on u.id = l.actor_user_id
where (%(target_type)s::text is null or l.target_type = %(target_type)s)
  and (%(target_id)s::text is null or l.target_id = %(target_id)s)
order by l.created_at desc
limit %(limit)s
"""


async def list_audit(conn, *, limit: int, target_type: str | None, target_id: str | None) -> dict:
    capped = max(1, min(limit, MAX_LIST_LIMIT))
    async with conn.cursor() as cur:
        await cur.execute(LIST_AUDIT_SQL, {
            "limit": capped, "target_type": target_type, "target_id": target_id,
        })
        rows = await cur.fetchall() or []
    return {
        "items": [
            {
                "id": r["id"], "action": r["action"], "targetType": r["target_type"],
                "targetId": r.get("target_id"), "note": r.get("note"),
                "actorEmail": r.get("actor_email"),
                "createdAt": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in rows
        ]
    }


@router.get("/staff")
async def admin_list_staff(
    request: Request, q: str | None = Query(None), user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        return JSONResponse(await list_staff(conn, q=q))


@router.post("/staff/{target_user_id}/role")
async def admin_set_role(
    request: Request, target_user_id: str, body: RoleRequest,
    user_id: str = Depends(require_user),
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        result = await set_role(conn, target_user_id=target_user_id, actor=user_id, role=body.role)
        await conn.commit()
    return JSONResponse(result)


@router.get("/audit")
async def admin_list_audit(
    request: Request,
    limit: int = Query(20),
    target_type: str | None = Query(None, alias="targetType"),
    target_id: str | None = Query(None, alias="targetId"),
    user_id: str = Depends(require_user),
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id)
        return JSONResponse(await list_audit(
            conn, limit=limit, target_type=target_type, target_id=target_id,
        ))
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd server && uv run pytest tests/test_admin_staff.py -q`
Expected: PASS (10 passed)

- [ ] **Step 5: 전체 백엔드 테스트**

Run: `cd server && uv run pytest -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add server/app/facemarket_admin.py server/tests/test_admin_staff.py
git commit -m "$(cat <<'MSG'
feat(admin): 관리자 승격·회수 + 감사 기록 조회

안전장치 셋을 서버가 강제한다. 최후 관리자 판정은 count 를 for update 로 잠근 뒤 센다 —
잠그지 않으면 두 관리자가 서로를 동시에 내려 관리자 0명이 된다(둘 다 "나 말고 하나 더
있다"를 읽는다). 계정 검색은 이메일 정확일치다. 부분일치로 열면 콘솔이 가입자 이메일
스캐너가 된다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
)"
```

---

## Task 13: 관리자 관리 화면

**Files:**
- Modify: `src/features/admin/AdminStaff.jsx` (자리표시 → 본체)
- Modify: `src/lib/api/facemarket.js`
- Test: `tests/frontend/admin-staff.test.mjs`
- Modify: `docs/superpowers/specs/2026-09-04-facemarket-admin-console-design.md` (§12 실측 결과 기록)

**Interfaces:**
- Consumes: Task 12 의 라우트
- Produces: `adminListStaff(q)`, `adminSetRole(userId, role)`, `adminListAudit({limit, targetType, targetId})`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/frontend/admin-staff.test.mjs`:

```javascript
/* 관리자 관리 화면 — 서버 가드를 UI 가 안내로 미리 보여주는지. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('api 클라이언트에 staff·audit 함수가 있다', () => {
  const api = read('src/lib/api/facemarket.js');
  for (const fn of ['adminListStaff', 'adminSetRole', 'adminListAudit']) {
    assert.ok(api.includes(`export function ${fn}`), `누락: ${fn}`);
  }
});

test('자기 자신·마지막 관리자는 회수 버튼이 비활성이다', () => {
  const source = read('src/features/admin/AdminStaff.jsx');
  assert.ok(source.includes('isSelf'), '자기 자신 판정이 없다');
  assert.ok(source.includes('admins.length <= 1') || source.includes('lastAdmin'), '최후 관리자 판정이 없다');
});

test('최근 감사 기록을 보여준다', () => {
  const source = read('src/features/admin/AdminStaff.jsx');
  assert.ok(source.includes('adminListAudit'));
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pnpm test:frontend`
Expected: FAIL — `누락: adminListStaff`

- [ ] **Step 3: API 클라이언트를 넓힌다**

```javascript
export function adminListStaff(q) {
  const qs = q ? `?q=${encodeURIComponent(q)}` : '';
  return http(`/v1/facemarket/admin/staff${qs}`);
}

export function adminSetRole(userId, role) {
  return http(`/v1/facemarket/admin/staff/${encodeURIComponent(userId)}/role`, {
    method: 'POST', body: { role },
  });
}

export function adminListAudit({ limit = 20, targetType, targetId } = {}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (targetType) params.set('targetType', targetType);
  if (targetId) params.set('targetId', targetId);
  return http(`/v1/facemarket/admin/audit?${params.toString()}`);
}
```

- [ ] **Step 4: 화면을 만든다**

`src/features/admin/AdminStaff.jsx`:

```jsx
/* 관리자 관리 — 이메일로 찾아 권한을 켜고 끈다 + 최근 기록.

   버튼 비활성은 안내일 뿐이고 판정은 서버가 한다(자기 강등·최후 관리자·미가입). UI 가
   막는 것에 기대면, 두 관리자가 동시에 서로를 내리는 경합을 프런트는 볼 수 없다. */
import { useCallback, useEffect, useState } from 'react';
import { adminListAudit, adminListStaff, adminSetRole } from '@/lib/api/facemarket.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { Badge } from '@/components/admin-ui/badge.jsx';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/admin-ui/card.jsx';
import { Input } from '@/components/admin-ui/input.jsx';
import { Skeleton } from '@/components/admin-ui/skeleton.jsx';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/admin-ui/table.jsx';
import { useToast } from '@/components/ui.jsx';

const ACTION_LABEL = {
  'application.approve': '지원서 승인',
  'application.reject': '지원서 거절',
  'application.resend_email': '결정 메일 재발송',
  'staff.role.grant': '관리자 승격',
  'staff.role.revoke': '관리자 회수',
  'model.suspend': '모델 정지',
  'model.unsuspend': '모델 정지 해제',
  'refund.approve': '환불 승인',
  'refund.reject': '환불 반려',
};

export function AdminStaff() {
  const toast = useToast();
  const { user } = useAuth();
  const [q, setQ] = useState('');
  const [data, setData] = useState(null);
  const [audit, setAudit] = useState([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback((term) => {
    adminListStaff(term).then(setData).catch((e) => toast?.show?.(e.message));
    adminListAudit({ limit: 20 }).then((d) => setAudit(d.items)).catch(() => setAudit([]));
  }, [toast]);

  useEffect(() => { load(undefined); }, [load]);

  const change = async (userId, role) => {
    setBusy(true);
    try {
      await adminSetRole(userId, role);
      load(q.trim() || undefined);
    } catch (e) {
      toast?.show?.(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!data) return <Skeleton className="h-64" />;

  const { admins, matches } = data;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>관리자 {admins.length}명</CardTitle>
          <CardDescription>
            첫 관리자는 DB 에서 직접 지정해요. 여기서는 이미 가입한 계정만 승격할 수 있어요.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow><TableHead>이메일</TableHead><TableHead>이름</TableHead><TableHead /></TableRow>
            </TableHeader>
            <TableBody>
              {admins.map((a) => {
                const isSelf = a.userId === user?.id;
                const lastAdmin = admins.length <= 1;
                return (
                  <TableRow key={a.userId}>
                    <TableCell>{a.email || a.userId}</TableCell>
                    <TableCell className="text-muted-foreground">{a.displayName || '-'}</TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy || isSelf || lastAdmin}
                        title={isSelf ? '자기 자신은 내릴 수 없어요' : lastAdmin ? '마지막 관리자는 내릴 수 없어요' : undefined}
                        onClick={() => change(a.userId, 'user')}
                      >
                        관리자 해제
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>계정 찾기</CardTitle>
          <CardDescription>이메일 전체를 정확히 입력해 주세요.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex gap-2">
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="user@example.com"
              className="w-72"
              onKeyDown={(e) => { if (e.key === 'Enter') load(q.trim() || undefined); }}
            />
            <Button variant="outline" onClick={() => load(q.trim() || undefined)}>검색</Button>
          </div>
          {matches.map((m) => (
            <div key={m.userId} className="flex items-center gap-3">
              <span>{m.email}</span>
              <Badge variant={m.role === 'admin' ? 'default' : 'secondary'}>{m.role}</Badge>
              {m.role !== 'admin' && (
                <Button size="sm" disabled={busy} onClick={() => change(m.userId, 'admin')}>
                  관리자로 올리기
                </Button>
              )}
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>최근 기록</CardTitle></CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>시각</TableHead><TableHead>한 일</TableHead>
                <TableHead>대상</TableHead><TableHead>사람</TableHead><TableHead>메모</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {audit.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="text-muted-foreground">{(row.createdAt || '').slice(0, 16).replace('T', ' ')}</TableCell>
                  <TableCell>{ACTION_LABEL[row.action] || row.action}</TableCell>
                  <TableCell className="text-muted-foreground">{row.targetId || '-'}</TableCell>
                  <TableCell className="text-muted-foreground">{row.actorEmail || '-'}</TableCell>
                  <TableCell className="text-muted-foreground">{row.note || '-'}</TableCell>
                </TableRow>
              ))}
              {audit.length === 0 && (
                <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">기록 없음</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
```

> `useAuth()` 가 `user` 를 주는지 확인한다. 다른 이름(`session` 등)이면 그 필드에서 `id` 를 꺼내 `isSelf` 를 만든다 — **판정 자체는 서버가 하므로 여기서 못 구하면 `isSelf` 는 `false` 로 두고 서버 400 메시지를 토스트로 보여 준다.**

- [ ] **Step 5: 통과를 확인한다**

Run: `pnpm test:frontend && pnpm build`
Expected: PASS + 빌드 성공

- [ ] **Step 6: 설계 문서의 §12 를 실측 결과로 갱신한다**

`docs/superpowers/specs/2026-09-04-facemarket-admin-console-design.md` §12 의 세 항목에 실제로 확인한 값을 적는다 — `auth.users` 접근 가능 여부(Task 9 Step 5), `fm_models.user_id` null 행 수, prod 관리자 수. 확인 못 한 항목은 "미확인"이라고 적는다(빈칸으로 두지 않는다).

- [ ] **Step 7: 커밋**

```bash
git add src/features/admin/AdminStaff.jsx src/lib/api/facemarket.js \
        tests/frontend/admin-staff.test.mjs docs/superpowers/specs/2026-09-04-facemarket-admin-console-design.md
git commit -m "$(cat <<'MSG'
feat(admin): 관리자 관리 화면 + 최근 기록

버튼 비활성은 안내일 뿐 판정은 서버가 한다. UI 가 막는 것에 기대면 두 관리자가 동시에
서로를 내리는 경합을 프런트는 볼 수 없다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
)"
```

---

## 마무리 확인

- [ ] `cd server && uv run pytest -q` — 전량 green
- [ ] `pnpm test:frontend` — 전량 green
- [ ] `pnpm build` — 성공, Tailwind 유틸리티가 든 CSS 는 admin 청크 하나뿐
- [ ] 로컬에서 콘솔 4화면을 직접 눌러 본다(`pnpm dev` → `http://localhost:5173/?admin=1`)
- [ ] PR 을 연다. 단계별로 나눠 머지하려면 Task 1–3 / 4–6 / 7–8 / 9–11 / 12–13 을 각각 PR 로 자른다.
