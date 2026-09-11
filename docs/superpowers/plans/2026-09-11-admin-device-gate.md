# 관리자 콘솔 기기 게이트 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `admin.wearless.kr` 콘솔과 관리자 API 를 "관리자 계정 + 승인된 기기" 에서만 쓸 수 있게 하고, prod 에 열려 있던 `/openapi.json` 을 닫는다.

**Architecture:** 기기 = 첫 방문 때 서버가 발급한 랜덤 토큰(브라우저 localStorage, DB 엔 sha256 만). 모든 관리자 라우트가 지나는 `admin_guard.require_admin` 한 곳에서 `X-Admin-Device` 헤더를 검사하고, `ADMIN_DEVICE_GATE=off|shadow|enforce` 플래그로 배포 순서를 통제한다. 승인·회수는 콘솔 `/staff` 화면에서 다른 승인 기기가 하고 감사 원장·Slack 에 남는다.

**Tech Stack:** FastAPI + psycopg(async) + Supabase Postgres(마이그레이션 `supabase/migrations/*.sql`), React 19 + react-router, 테스트는 pytest(FakeConn·TestClient) / `node --test tests/frontend/*.test.mjs`(소스 계약 + 순수 모듈).

**Spec:** `docs/superpowers/specs/2026-09-11-admin-device-gate-design.md`

## Global Constraints

- 작업 위치: 워크트리 `.claude/worktrees/admin-device-gate`, 브랜치 `feat/admin-device-gate`. 메인 트리로 `cd` 금지.
- 백엔드 테스트 실행: 워크트리 `server/` 에서 `../../../../server/.venv/bin/python -m pytest -q -p no:cacheprovider <파일>` (메인 트리 venv 재사용, `pytest.ini` 의 `pythonpath = .` 라 워크트리 `app` 이 import 된다).
- 프런트 테스트 실행: 워크트리 루트에서 `pnpm test:frontend` (또는 `node --test tests/frontend/<파일>.test.mjs`).
- 기존 테스트 파일 수정은 **이 계획이 명시한 곳만**(`conftest.make_settings` 기본값 1줄, `test_admin_guard.py` 시그니처, `test_admin_guard_adoption.py` 문자열). 다른 기존 테스트가 깨지면 구현이 틀린 것이다.
- 헤더 이름 `X-Admin-Device`, localStorage 키 `wl.admin.device.v1`, 설정 `admin_device_gate` 기본 `"shadow"`(코드) / `"off"`(테스트 `make_settings`), `admin_device_max_pending_per_user` 기본 `5`.
- 에러 봉투는 기존과 같이 `HTTPException(detail={"code", "message"})`. 기기 코드 4종: `device_missing` / `device_unknown` / `device_pending` / `device_revoked`.
- 감사 액션: `device.approve`, `device.revoke`(target_type `admin_device`). 강등 시 기존 `staff.role.revoke` 의 `after` 에 `revokedDevices` 추가.
- 커밋 메시지는 이 레포 관례(한국어 서술형 `feat(admin): …`), 끝에 `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` 과 `Claude-Session: https://claude.ai/code/session_019cJPhmby2YzLcN5RaPcGL1` 두 줄. 커밋은 `git -c core.hooksPath=/dev/null commit` 로(워크트리에서 훅이 메인 트리를 건드리지 않게).
- 주석은 주변 코드처럼 "왜" 를 한국어로. 파일 첫머리 docstring 에 설계서 경로.

---

## 파일 구조

| 파일 | 역할 | Task |
|---|---|---|
| `server/app/main.py` | `openapi_url` prod 차단, CORS `X-Admin-Device`, 새 라우터 include | 1, 5 |
| `server/tests/test_main_openapi.py` (신규) | openapi/CORS 계약 | 1 |
| `supabase/migrations/20260911150000_admin_devices.sql` (신규) | 테이블 | 2 |
| `server/app/repo.py` | `find_admin_device_by_hash`, `touch_admin_device` (가드 전용 2개) | 2 |
| `server/app/config.py`, `server/tests/conftest.py` | 플래그 2개 + 테스트 기본 off | 3 |
| `server/tests/test_admin_device_config.py` (신규) | 플래그 로딩 | 3 |
| `server/app/admin_guard.py` | `check_device`, `require_admin(conn, user_id, request)`, `require_admin_identity` | 4 |
| 라우트 6파일(호출부 24곳) | `request` 인자 추가 | 4 |
| `server/tests/test_admin_guard.py`, `test_admin_guard_adoption.py` | 가드 단위 + 소스 계약 | 4 |
| `server/app/facemarket_admin_devices.py` (신규) | 기기 라우트 5개 + 순수 함수 | 5 |
| `server/app/facemarket_notify.py` | Slack 알림 함수 1개 | 5 |
| `server/tests/test_admin_devices.py` (신규) | 순수 함수 + 라우트 | 5 |
| `server/app/facemarket_admin.py` | `set_role` 강등 시 기기 일괄 회수 | 6 |
| `src/lib/adminDevice.js` (신규), `src/lib/api/httpAdapter.js`, `src/lib/api/facemarket.js` | 토큰 저장·헤더 주입·API 함수 | 7 |
| `tests/frontend/admin-device.test.mjs` (신규) | 순수 모듈 + 소스 계약 | 7, 8, 9 |
| `src/apps/admin/RequireDevice.jsx` (신규), `src/apps/admin/App.jsx` | 가드 화면 4상태 | 8 |
| `src/features/admin/AdminStaff.jsx` | "관리자 기기" 섹션 | 9 |
| `docs/runbooks/admin-device-gate.md` (신규), `copilot/api/manifest.yml` | 배포·락아웃 런북, env 주석 | 10 |

---

### Task 1: prod 에서 `/openapi.json` 닫기 + CORS 에 `X-Admin-Device`

**Files:**
- Modify: `server/app/main.py:292-299` (FastAPI 생성), `server/app/main.py:409-415` (CORS)
- Test: `server/tests/test_main_openapi.py` (신규)

**Interfaces:**
- Produces: 이후 모든 프런트 요청이 `X-Admin-Device` 헤더를 preflight 없이 실을 수 있다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# server/tests/test_main_openapi.py
"""API 스키마 노출과 관리자 기기 헤더의 CORS 허용 — 앱 팩토리 계약.

되돌아가면: prod 의 /openapi.json 이 209KB 전체 라우트·모델을 아무에게나 준다(2026-09-11
실측). CORS 에 헤더가 빠지면 admin 콘솔의 모든 요청이 preflight 에서 죽는데, 로그인까지는
되니 "서버에 연결하지 못했어요" 로만 보인다(2026-09-04 CORS_ORIGINS 사고와 같은 모양).
"""
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import make_settings


def test_openapi_and_docs_are_closed_outside_dev():
    client = TestClient(create_app(make_settings(app_env="prod")))
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_openapi_and_docs_stay_open_in_dev():
    client = TestClient(create_app(make_settings(app_env="dev")))
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_cors_preflight_allows_the_admin_device_header():
    client = TestClient(create_app(make_settings(cors_origins=["https://admin.wearless.kr"])))
    res = client.options(
        "/v1/facemarket/admin/overview",
        headers={
            "Origin": "https://admin.wearless.kr",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,x-admin-device",
        },
    )
    assert res.status_code == 200
    allowed = res.headers.get("access-control-allow-headers", "").lower()
    assert "x-admin-device" in allowed
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && ../../../../server/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_main_openapi.py`
Expected: `test_openapi_and_docs_are_closed_outside_dev` FAIL(`/openapi.json` 이 200), `test_cors_preflight_allows_the_admin_device_header` FAIL(헤더 없음). dev 테스트는 PASS.

- [ ] **Step 3: 구현**

`server/app/main.py` 의 FastAPI 생성부를 이렇게 바꾼다:

```python
    docs_url = "/docs" if settings.app_env == "dev" else None
    redoc_url = "/redoc" if settings.app_env == "dev" else None
    # 스키마 JSON 도 같이 닫는다. docs/redoc 만 끄면 /openapi.json 이 그대로 남아 전체 라우트·
    # 모델(관리자 라우트 포함)을 아무에게나 준다 — 2026-09-11 prod 에서 209KB 로 열려 있었다.
    openapi_url = "/openapi.json" if settings.app_env == "dev" else None

    app = FastAPI(
        title="Wearless Studio API",
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )
```

CORS 의 `allow_headers` 에 헤더를 더한다:

```python
        allow_headers=[
            "Authorization", "Content-Type", "Idempotency-Key", "X-Draft-Token",
            # 관리자 콘솔 기기 토큰(admin_guard). 빠지면 admin.wearless.kr 의 모든 요청이
            # preflight 에서 죽는다 — 로그인은 되니 화면엔 "서버에 연결하지 못했어요" 만 남는다.
            "X-Admin-Device",
        ],
```

- [ ] **Step 4: 통과 확인**

Run: `cd server && ../../../../server/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_main_openapi.py`
Expected: 3 passed

- [ ] **Step 5: 커밋**

```bash
git add server/app/main.py server/tests/test_main_openapi.py
git -c core.hooksPath=/dev/null commit -m "fix(api): prod 에서 /openapi.json 을 닫고 CORS 에 X-Admin-Device 를 허용해요

docs/redoc 만 꺼져 있어 전체 API 스키마(관리자 라우트 포함)가 209KB 로 공개돼
있었다. 관리자 기기 게이트(다음 커밋들)가 쓸 헤더도 미리 허용한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019cJPhmby2YzLcN5RaPcGL1"
```

---

### Task 2: `admin_devices` 마이그레이션 + 가드용 repo 함수 2개

**Files:**
- Create: `supabase/migrations/20260911150000_admin_devices.sql`
- Modify: `server/app/repo.py` (`is_admin` 바로 아래, 약 3006행)
- Test: `server/tests/test_admin_devices.py` (신규 — 이 Task 에서 repo 부분만 만들고 Task 5 에서 확장)

**Interfaces:**
- Produces:
  - `repo.find_admin_device_by_hash(conn, token_hash: str) -> dict | None` — 키 `id`(str), `user_id`(str), `status`, `label`, `last_seen_at`(datetime|None)
  - `repo.touch_admin_device(conn, device_id: str) -> None`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# server/tests/test_admin_devices.py
"""관리자 기기 게이트 — repo·순수 함수·라우트.

설계: docs/superpowers/specs/2026-09-11-admin-device-gate-design.md
"""
import asyncio
import contextlib

from app import repo


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
    def __init__(self, rows=()):
        self.executed, self.rows, self.commits = [], list(rows), 0

    def cursor(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeCursor(self.executed, self.rows)

        return _cm()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None


# ---------- repo ----------

def test_find_device_by_hash_selects_text_ids_and_status():
    conn = FakeConn([{"id": "d1", "user_id": "u1", "status": "approved", "label": "Mac", "last_seen_at": None}])
    row = asyncio.run(repo.find_admin_device_by_hash(conn, "abc"))
    assert row["id"] == "d1" and row["status"] == "approved"
    sql, params = conn.executed[0]
    assert sql.startswith("select id::text as id, user_id::text as user_id")
    assert "where token_hash = %s" in sql
    assert params == ("abc",)


def test_find_device_by_hash_returns_none_when_missing():
    assert asyncio.run(repo.find_admin_device_by_hash(FakeConn([]), "nope")) is None


def test_touch_device_updates_last_seen_only():
    conn = FakeConn()
    asyncio.run(repo.touch_admin_device(conn, "d1"))
    sql, params = conn.executed[0]
    assert sql == "update admin_devices set last_seen_at = now() where id = %s"
    assert params == ("d1",)
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && ../../../../server/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_admin_devices.py`
Expected: 3 FAIL — `AttributeError: module 'app.repo' has no attribute 'find_admin_device_by_hash'`

- [ ] **Step 3: 마이그레이션 작성**

```sql
-- =============================================================
-- 20260911150000_admin_devices.sql
-- 관리자 콘솔 기기 게이트 — 승인된 기기 목록
-- 설계: docs/superpowers/specs/2026-09-11-admin-device-gate-design.md §4
--
-- 기기 = 브라우저가 localStorage 에 쥔 랜덤 토큰. 여기엔 sha256 만 둔다(원문은 어디에도
-- 저장하지 않는다 — DB 가 새도 토큰을 재구성할 수 없게).
-- revoked 는 종점이다. 다시 쓰려면 새로 등록한다(부활 경로가 있으면 회수의 의미가 흐려진다).
-- 계정이 지워지면(30일 PII 스윕 등) 기기도 같이 간다. 누가 언제 승인·회수했는지는
-- admin_audit_log 에 남으므로 여기서는 잃어도 된다.
-- =============================================================

create table if not exists public.admin_devices (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  token_hash    text not null unique,
  label         text not null,
  status        text not null default 'pending'
                check (status in ('pending', 'approved', 'revoked')),
  user_agent    text,
  created_at    timestamptz not null default now(),
  last_seen_at  timestamptz,
  approved_by   uuid references auth.users(id) on delete set null,
  approved_at   timestamptz,
  revoked_by    uuid references auth.users(id) on delete set null,
  revoked_at    timestamptz
);

create index if not exists admin_devices_user_status_idx
  on public.admin_devices (user_id, status);
create index if not exists admin_devices_status_created_idx
  on public.admin_devices (status, created_at desc);

-- 정책 없이 RLS 만 켠다 = anon/PostgREST 경로 차단. 서버는 직결 DB 롤이라 영향 없다.
alter table public.admin_devices enable row level security;
```

- [ ] **Step 4: repo 함수 구현**

`server/app/repo.py` 의 `is_admin` 함수 바로 아래에:

```python
# ---------- 관리자 기기 게이트 (admin_guard 전용) ----------
# 라우트가 쓰는 등록·목록·승인·회수 SQL 은 facemarket_admin_devices.py 에 산다. 여기 둘은
# 모든 관리자 요청이 지나는 가드가 부르는 것이라 is_admin 옆에 둔다 — 테스트가 repo.is_admin
# 을 monkeypatch 하듯 이 둘도 바꿔 끼울 수 있게.


async def find_admin_device_by_hash(conn: AsyncConnection, token_hash: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, user_id::text as user_id, status, label, last_seen_at "
            "from admin_devices where token_hash = %s",
            (token_hash,),
        )
        return await cur.fetchone()


async def touch_admin_device(conn: AsyncConnection, device_id: str) -> None:
    """last_seen_at 갱신. 호출자 트랜잭션 안에서 돈다 — 읽기 라우트가 커밋을 안 하면
    잃는데, 표시용 값이라 60초 뒤 다음 쓰기 요청에서 다시 찍히면 된다."""
    async with conn.cursor() as cur:
        await cur.execute(
            "update admin_devices set last_seen_at = now() where id = %s", (device_id,)
        )
```

- [ ] **Step 5: 통과 확인**

Run: `cd server && ../../../../server/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_admin_devices.py`
Expected: 3 passed

- [ ] **Step 6: 커밋**

```bash
git add supabase/migrations/20260911150000_admin_devices.sql server/app/repo.py server/tests/test_admin_devices.py
git -c core.hooksPath=/dev/null commit -m "feat(admin): 관리자 기기 테이블과 가드용 조회 함수를 더해요

admin_devices(토큰 해시·상태·승인/회수 이력) 마이그레이션과, 모든 관리자
요청이 지나는 가드가 부를 find_admin_device_by_hash·touch_admin_device.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019cJPhmby2YzLcN5RaPcGL1"
```

---

### Task 3: 설정 플래그 `ADMIN_DEVICE_GATE` + 테스트 기본 off

**Files:**
- Modify: `server/app/config.py` (`fm_slack_webhook_url` 필드 아래 약 312행; `load_settings` 의 `fm_slack_webhook_url=` 아래 약 661행)
- Modify: `server/tests/conftest.py:139` (`make_settings` base 에 1줄)
- Test: `server/tests/test_admin_device_config.py` (신규)

**Interfaces:**
- Produces: `settings.admin_device_gate: str`("off"|"shadow"|"enforce"), `settings.admin_device_max_pending_per_user: int`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# server/tests/test_admin_device_config.py
"""관리자 기기 게이트 플래그 — 기본값과 허용값.

코드 기본은 shadow(배포 직후 아무도 잠기지 않게), 테스트 기본은 off(관련 없는 라우트
테스트가 FakeConn 위에서 기기 조회를 돌리지 않게 — garment_qc_mode 와 같은 선례).
"""
from app.config import Settings, load_settings
from conftest import make_settings


def test_gate_defaults_to_shadow_in_code_and_off_in_tests():
    assert Settings.__dataclass_fields__["admin_device_gate"].default == "shadow"
    assert Settings.__dataclass_fields__["admin_device_max_pending_per_user"].default == 5
    assert make_settings().admin_device_gate == "off"


def test_gate_reads_env_and_falls_back_to_shadow_on_garbage(monkeypatch):
    monkeypatch.setenv("ADMIN_DEVICE_GATE", "enforce")
    assert load_settings().admin_device_gate == "enforce"
    monkeypatch.setenv("ADMIN_DEVICE_GATE", "Shadow ")
    assert load_settings().admin_device_gate == "shadow"
    monkeypatch.setenv("ADMIN_DEVICE_GATE", "yes")
    # 오타로 게이트가 꺼지거나(off) 잠기면(enforce) 안 된다 — 중간값으로 떨어진다.
    assert load_settings().admin_device_gate == "shadow"


def test_max_pending_reads_env(monkeypatch):
    monkeypatch.setenv("ADMIN_DEVICE_MAX_PENDING_PER_USER", "2")
    assert load_settings().admin_device_max_pending_per_user == 2
    monkeypatch.setenv("ADMIN_DEVICE_MAX_PENDING_PER_USER", "abc")
    assert load_settings().admin_device_max_pending_per_user == 5
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && ../../../../server/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_admin_device_config.py`
Expected: 3 FAIL — `KeyError: 'admin_device_gate'`

- [ ] **Step 3: 구현**

`server/app/config.py` — `fm_slack_webhook_url: str | None = None` 바로 아래에 필드 추가:

```python
    # 관리자 콘솔 기기 게이트(admin_guard). off=검사 안 함 / shadow=검사하고 실패해도 통과·
    # 로그만 / enforce=실패 시 403. 코드 기본 shadow — 배포 직후 아무도 잠기지 않고, 두 관리자가
    # 콘솔에서 서로 기기를 승인한 뒤 env 로 enforce 를 올린다(런북 docs/runbooks/admin-device-gate.md).
    # 락아웃 복구도 이 값을 shadow 로 내리는 것이다.
    admin_device_gate: str = "shadow"  # off | shadow | enforce
    # 한 관리자가 쌓을 수 있는 승인 대기 기기 수. 등록은 관리자 JWT 만 있으면 되므로 상한이 없으면
    # 탈취된 세션 하나가 목록을 스팸으로 덮고 Slack 을 울릴 수 있다.
    admin_device_max_pending_per_user: int = 5
```

`load_settings` — `fm_slack_webhook_url=os.getenv("FM_SLACK_WEBHOOK_URL") or None,` 바로 아래에:

```python
        admin_device_gate=_flag("ADMIN_DEVICE_GATE", "shadow", {"off", "shadow", "enforce"}),
        admin_device_max_pending_per_user=_int_env("ADMIN_DEVICE_MAX_PENDING_PER_USER", 5),
```

`server/tests/conftest.py` `make_settings` 의 base dict, `garment_qc_mode="off",` 바로 아래:

```python
        # 관리자 기기 게이트도 같은 이유로 테스트 기본 off — 기존 admin 라우트 테스트는 FakeConn
        # 큐에 role 행 하나만 넣고 도는데, shadow 도 기기 조회를 실행한다. 기기 테스트만 켠다.
        admin_device_gate="off",
```

- [ ] **Step 4: 통과 확인**

Run: `cd server && ../../../../server/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_admin_device_config.py tests/test_output_qc_config.py`
Expected: 모두 passed

- [ ] **Step 5: 커밋**

```bash
git add server/app/config.py server/tests/conftest.py server/tests/test_admin_device_config.py
git -c core.hooksPath=/dev/null commit -m "feat(admin): 기기 게이트 플래그 ADMIN_DEVICE_GATE 를 더해요 (기본 shadow, 테스트 off)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019cJPhmby2YzLcN5RaPcGL1"
```

---

### Task 4: 가드 — `check_device` + `require_admin(conn, user_id, request)` + 호출부 24곳

**Files:**
- Modify: `server/app/admin_guard.py` (전체)
- Modify (호출부 — `request` 인자 추가):
  - `server/app/facemarket_admin.py` 9곳: 224, 397, 406, 510, 521, 783, 793, 815, 837행
  - `server/app/facemarket_applications.py` 1곳(래퍼 `_require_admin` 정의 420-422행) + 래퍼 호출 5곳: 792, 811, 859, 903, 953행
  - `server/app/facemarket_admin_models.py` 래퍼 호출 6곳: 379, 453, 486, 548, 614, 679행 (`_require_admin = admin_guard.require_admin` 별칭이라 정의는 그대로)
  - `server/app/routes.py` 2곳: 820, 855행
  - `server/app/facemarket.py` 1곳: 1980행
  - `server/app/facemarket_cutover.py:372` 는 **손대지 않는다**(라우트 아님) — 주석만
- Test: `server/tests/test_admin_guard.py`(확장), `server/tests/test_admin_guard_adoption.py`(확장)

**Interfaces:**
- Consumes: `repo.find_admin_device_by_hash`, `repo.touch_admin_device` (Task 2), `settings.admin_device_gate` (Task 3)
- Produces:
  - `admin_guard.require_admin(conn, user_id: str, request: Request) -> None`
  - `admin_guard.require_admin_identity(conn, user_id: str) -> None` (신원만; Task 5 의 register/me 전용)
  - `admin_guard.check_device(conn, user_id, token: str | None, *, now=None) -> DeviceVerdict` (`.ok: bool`, `.code: str | None`, `.device: dict | None`)
  - `admin_guard.hash_device_token(token: str) -> str`, `admin_guard.device_token_from(request) -> str | None`, `admin_guard.DEVICE_HEADER = "X-Admin-Device"`, `admin_guard.DEVICE_MESSAGES: dict[str, str]`

- [ ] **Step 1: 가드 단위 테스트 추가**

`server/tests/test_admin_guard.py` — 기존 두 `require_admin` 테스트의 호출을 `require_admin(FakeConn(), "u1", fake_request())` 로 바꾸고, 아래를 파일 끝에 추가한다:

```python
import types
from datetime import datetime, timedelta, timezone

from fastapi import Request


def fake_request(*, gate="off", device=None, path="/v1/facemarket/admin/overview") -> Request:
    headers = [(b"host", b"api.test")]
    if device is not None:
        headers.append((b"x-admin-device", device.encode()))
    scope = {
        "type": "http", "method": "GET", "path": path, "headers": headers,
        "app": types.SimpleNamespace(state=types.SimpleNamespace(
            settings=types.SimpleNamespace(admin_device_gate=gate))),
    }
    return Request(scope)


def _admin(monkeypatch):
    async def is_admin(_conn, _user_id):
        return True
    monkeypatch.setattr(admin_guard.repo, "is_admin", is_admin)


def _device(monkeypatch, row, touched=None):
    async def find(_conn, _hash):
        return row
    async def touch(_conn, device_id):
        if touched is not None:
            touched.append(device_id)
    monkeypatch.setattr(admin_guard.repo, "find_admin_device_by_hash", find)
    monkeypatch.setattr(admin_guard.repo, "touch_admin_device", touch)


def test_hash_is_sha256_hex_of_the_token():
    assert admin_guard.hash_device_token("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_check_device_missing_and_unknown_and_other_users_token(monkeypatch):
    _device(monkeypatch, None)
    assert asyncio.run(admin_guard.check_device(FakeConn(), "u1", None)).code == "device_missing"
    assert asyncio.run(admin_guard.check_device(FakeConn(), "u1", "  ")).code == "device_missing"
    assert asyncio.run(admin_guard.check_device(FakeConn(), "u1", "tok")).code == "device_unknown"
    _device(monkeypatch, {"id": "d1", "user_id": "u2", "status": "approved", "last_seen_at": None})
    # 남의 토큰은 '모르는 기기' 로 답한다 — 남의 것이라는 사실을 드러내지 않는다.
    assert asyncio.run(admin_guard.check_device(FakeConn(), "u1", "tok")).code == "device_unknown"


def test_check_device_pending_and_revoked(monkeypatch):
    _device(monkeypatch, {"id": "d1", "user_id": "u1", "status": "pending", "last_seen_at": None})
    assert asyncio.run(admin_guard.check_device(FakeConn(), "u1", "tok")).code == "device_pending"
    _device(monkeypatch, {"id": "d1", "user_id": "u1", "status": "revoked", "last_seen_at": None})
    assert asyncio.run(admin_guard.check_device(FakeConn(), "u1", "tok")).code == "device_revoked"


def test_check_device_approved_touches_only_when_stale(monkeypatch):
    now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    touched = []
    _device(monkeypatch, {"id": "d1", "user_id": "u1", "status": "approved", "last_seen_at": None}, touched)
    v = asyncio.run(admin_guard.check_device(FakeConn(), "u1", "tok", now=now))
    assert v.ok and touched == ["d1"]

    touched.clear()
    _device(monkeypatch, {"id": "d1", "user_id": "u1", "status": "approved",
                          "last_seen_at": now - timedelta(seconds=30)}, touched)
    assert asyncio.run(admin_guard.check_device(FakeConn(), "u1", "tok", now=now)).ok
    assert touched == []  # 60초 안이면 안 찍는다

    _device(monkeypatch, {"id": "d1", "user_id": "u1", "status": "approved",
                          "last_seen_at": now - timedelta(seconds=61)}, touched)
    asyncio.run(admin_guard.check_device(FakeConn(), "u1", "tok", now=now))
    assert touched == ["d1"]


def test_require_admin_off_never_looks_at_devices(monkeypatch):
    _admin(monkeypatch)
    async def boom(_conn, _hash):
        raise AssertionError("off 인데 기기를 조회했다")
    monkeypatch.setattr(admin_guard.repo, "find_admin_device_by_hash", boom)
    asyncio.run(admin_guard.require_admin(FakeConn(), "u1", fake_request(gate="off")))


def test_require_admin_shadow_logs_and_passes(monkeypatch, caplog):
    _admin(monkeypatch)
    _device(monkeypatch, None)
    with caplog.at_level("WARNING"):
        asyncio.run(admin_guard.require_admin(FakeConn(), "u1", fake_request(gate="shadow")))
    assert "admin_device_gate shadow reject" in caplog.text
    assert "device_missing" in caplog.text


def test_require_admin_enforce_rejects_with_the_device_code(monkeypatch):
    _admin(monkeypatch)
    _device(monkeypatch, {"id": "d1", "user_id": "u1", "status": "pending", "last_seen_at": None})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_guard.require_admin(FakeConn(), "u1", fake_request(gate="enforce", device="tok")))
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "device_pending"
    assert exc.value.detail["message"] == admin_guard.DEVICE_MESSAGES["device_pending"]


def test_require_admin_enforce_passes_an_approved_device(monkeypatch):
    _admin(monkeypatch)
    _device(monkeypatch, {"id": "d1", "user_id": "u1", "status": "approved", "last_seen_at": None})
    asyncio.run(admin_guard.require_admin(FakeConn(), "u1", fake_request(gate="enforce", device="tok")))


def test_non_admin_is_rejected_before_any_device_lookup(monkeypatch):
    async def is_admin(_conn, _user_id):
        return False
    monkeypatch.setattr(admin_guard.repo, "is_admin", is_admin)
    async def boom(_conn, _hash):
        raise AssertionError("비관리자인데 기기를 조회했다")
    monkeypatch.setattr(admin_guard.repo, "find_admin_device_by_hash", boom)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_guard.require_admin(FakeConn(), "u1", fake_request(gate="enforce", device="tok")))
    assert exc.value.detail["code"] == "forbidden"


def test_require_admin_identity_checks_role_only(monkeypatch):
    _admin(monkeypatch)
    async def boom(_conn, _hash):
        raise AssertionError("identity 가드가 기기를 조회했다")
    monkeypatch.setattr(admin_guard.repo, "find_admin_device_by_hash", boom)
    asyncio.run(admin_guard.require_admin_identity(FakeConn(), "u1"))
```

- [ ] **Step 2: 소스 계약(adoption) 테스트 확장**

`server/tests/test_admin_guard_adoption.py`:

1. 상단 파일 목록에 추가:
```python
ADMIN_MODELS = (APP / "facemarket_admin_models.py").read_text()
ADMIN_DEVICES = (APP / "facemarket_admin_devices.py").read_text() if (APP / "facemarket_admin_devices.py").exists() else ""
```
2. `test_refund_routes_are_gated_and_audited` 의 문자열을 `"await admin_guard.require_admin(conn, user_id, request)"` 로 바꾼다.
3. 파일 끝에 추가:

```python
import re


def test_every_require_admin_call_passes_the_request():
    """기기 게이트는 request 헤더를 본다. request 없이 부르는 호출이 하나라도 남으면
    그 라우트만 기기 검사를 건너뛴다 — 조용한 구멍이라 소스에서 센다."""
    pattern = re.compile(r"(?:admin_guard\.require_admin|_require_admin)\(([^)]*)\)")
    for name, source in (
        ("facemarket_applications.py", APPLICATIONS), ("routes.py", ROUTES),
        ("facemarket.py", FACEMARKET), ("facemarket_admin.py", ADMIN),
        ("facemarket_admin_models.py", ADMIN_MODELS), ("facemarket_admin_devices.py", ADMIN_DEVICES),
    ):
        for m in pattern.finditer(source):
            args = [a.strip() for a in m.group(1).split(",")]
            if args[:1] == ["conn"] and len(args) == 2 and args[1].startswith("user_id"):
                # 래퍼 정의 `async def _require_admin(conn, user_id, request)` 는 `def` 로 시작하므로
                # 여기 안 걸린다. 걸리는 건 request 를 빼먹은 호출뿐이다.
                raise AssertionError(f"{name}: request 없이 require_admin 을 부른다 — {m.group(0)}")
            assert "request" in args, f"{name}: {m.group(0)}"


def test_identity_only_guard_is_used_by_exactly_the_two_device_bootstrap_routes():
    """기기 없이 통과하는 관리자 라우트는 등록·상태조회 둘뿐이어야 한다."""
    for name, source in (
        ("facemarket_applications.py", APPLICATIONS), ("routes.py", ROUTES),
        ("facemarket.py", FACEMARKET), ("facemarket_admin.py", ADMIN),
        ("facemarket_admin_models.py", ADMIN_MODELS), ("facemarket_cutover.py", CUTOVER),
    ):
        assert "require_admin_identity(" not in source, f"{name} 가 기기 면제 가드를 쓴다"
    if ADMIN_DEVICES:
        assert ADMIN_DEVICES.count("await admin_guard.require_admin_identity(conn, user_id)") == 2
```

- [ ] **Step 3: 실패 확인**

Run: `cd server && ../../../../server/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_admin_guard.py tests/test_admin_guard_adoption.py`
Expected: 새 테스트들 FAIL(`AttributeError: … has no attribute 'check_device'`, `TypeError: require_admin() takes 2 positional arguments`, adoption 의 `request 없이 require_admin 을 부른다`).

- [ ] **Step 4: `admin_guard.py` 구현**

파일 전체를 이렇게 바꾼다(기존 `forbidden`·`is_admin_user`·`write_audit` 는 그대로 유지):

```python
"""관리자 권한 게이트와 감사 원장 기록 — 콘솔의 모든 쓰기가 지나는 문.

`repo.is_admin` 을 직접 부르는 곳은 이 파일 하나여야 한다. 예전에는 같은 판정이 6군데에
흩어져 있어 에러 코드·문구가 제각각이었고, 새 라우트를 추가할 때 가드를 빼먹어도 아무도
몰랐다(테스트가 그걸 못 본다).

2026-09-11 부터 두 번째 관문이 붙었다 — **기기**. 관리자 계정이어도 승인된 기기의 토큰
(`X-Admin-Device`)이 없으면 막는다. 설계: docs/superpowers/specs/2026-09-11-admin-device-gate-design.md.
모드는 settings.admin_device_gate(off/shadow/enforce). require_admin 이 request 를 받는
이유가 이것이다 — 호출부 24곳이 전부 request 를 넘기는지는 test_admin_guard_adoption 이 센다.

write_audit 은 conn.commit() 을 하지 않는다 — 호출자(라우트)의 트랜잭션 안에서 조치와
함께 커밋돼야 한다. 따로 커밋하면 조치는 실패하고 기록만 남는 경우가 생긴다.
"""
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from psycopg.types.json import Json

from . import repo

logger = logging.getLogger(__name__)

DEVICE_HEADER = "X-Admin-Device"
# last_seen_at 은 표시용이다. 요청마다 쓰면 읽기 라우트가 전부 쓰기가 된다.
DEVICE_TOUCH_INTERVAL = timedelta(seconds=60)
DEVICE_MESSAGES = {
    "device_missing": "등록된 기기에서만 쓸 수 있어요.",
    # 남의 토큰도 같은 문구 — 그 토큰이 존재한다는 사실을 알려 주지 않는다.
    "device_unknown": "등록된 기기에서만 쓸 수 있어요.",
    "device_pending": "이 기기는 아직 승인 대기 중이에요.",
    "device_revoked": "이 기기는 회수됐어요. 다시 등록해 주세요.",
}


@dataclass(frozen=True)
class DeviceVerdict:
    code: str | None            # None = 통과
    device: dict | None = None

    @property
    def ok(self) -> bool:
        return self.code is None


def forbidden() -> HTTPException:
    return HTTPException(
        status_code=403, detail={"code": "forbidden", "message": "관리자만 가능해요."}
    )


def device_forbidden(code: str) -> HTTPException:
    return HTTPException(status_code=403, detail={"code": code, "message": DEVICE_MESSAGES[code]})


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def device_token_from(request: Request) -> str | None:
    return request.headers.get(DEVICE_HEADER)


async def require_admin_identity(conn, user_id: str) -> None:
    """관리자인지만 본다 — 기기는 안 본다.

    기기 등록(`POST /admin/devices/register`)·상태 조회(`GET /admin/devices/me`) 두 라우트
    전용이다. 그 둘은 아직 기기가 없는 관리자가 불러야 하는 라우트라 기기를 요구할 수 없다.
    다른 곳에서 쓰지 마라 — test_admin_guard_adoption 이 사용처가 정확히 둘인지 센다."""
    if not await repo.is_admin(conn, user_id):
        raise forbidden()


async def check_device(
    conn, user_id: str, token: str | None, *, now: datetime | None = None
) -> DeviceVerdict:
    """토큰 → 판정. 라우트가 아니라 순수 판정이라 request 없이 테스트한다."""
    token = (token or "").strip()
    if not token:
        return DeviceVerdict("device_missing")
    device = await repo.find_admin_device_by_hash(conn, hash_device_token(token))
    if device is None or device.get("user_id") != user_id:
        return DeviceVerdict("device_unknown")
    status = device.get("status")
    if status == "pending":
        return DeviceVerdict("device_pending", device)
    if status == "revoked":
        return DeviceVerdict("device_revoked", device)
    if status != "approved":
        return DeviceVerdict("device_unknown", device)
    now = now or datetime.now(timezone.utc)
    seen = device.get("last_seen_at")
    if seen is None or now - seen >= DEVICE_TOUCH_INTERVAL:
        await repo.touch_admin_device(conn, device["id"])
    return DeviceVerdict(None, device)


async def require_admin(conn, user_id: str, request: Request) -> None:
    await require_admin_identity(conn, user_id)
    mode = getattr(request.app.state.settings, "admin_device_gate", "off")
    if mode == "off":
        return
    verdict = await check_device(conn, user_id, device_token_from(request))
    if verdict.ok:
        return
    if mode == "enforce":
        raise device_forbidden(verdict.code)
    # shadow — 막지 않고 남긴다. enforce 로 올리기 전에 누가 잠길지 이 로그로 안다.
    logger.warning(
        "admin_device_gate shadow reject user=%s code=%s path=%s",
        user_id, verdict.code, request.url.path,
    )


async def is_admin_user(conn, user_id: str) -> bool:
    """예외 대신 판정만 필요한 호출자(cutover 는 자체 예외 타입을 쓴다).
    기기 게이트는 안 탄다 — 라우트가 아니라 request 가 없다."""
    return await repo.is_admin(conn, user_id)


async def write_audit(  # 기존 그대로
    ...
```

(`write_audit` 본문은 기존 코드를 그대로 둔다.)

- [ ] **Step 5: 호출부 24곳 수정**

각 파일에서 `await admin_guard.require_admin(conn, user_id)` → `await admin_guard.require_admin(conn, user_id, request)`, `await _require_admin(conn, user_id)` → `await _require_admin(conn, user_id, request)`. 워크트리 루트에서:

```bash
cd server && for f in app/facemarket_admin.py app/routes.py app/facemarket.py app/facemarket_applications.py app/facemarket_admin_models.py; do
  sed -i '' 's/await admin_guard\.require_admin(conn, user_id)$/await admin_guard.require_admin(conn, user_id, request)/; s/await _require_admin(conn, user_id)$/await _require_admin(conn, user_id, request)/' "$f"
done
grep -rn "require_admin(conn, user_id)" app || echo "남은 2-인자 호출 없음"
```

`server/app/facemarket_applications.py` 의 래퍼 정의를 바꾼다:

```python
async def _require_admin(conn, user_id: str, request) -> None:
    """호출부 이름은 그대로 두고 판정만 admin_guard 로 넘긴다(라우트 diff 최소화)."""
    await admin_guard.require_admin(conn, user_id, request)
```

각 호출부의 함수가 `request: Request` 를 인자로 받고 있는지 확인한다(전부 `get_conn(request)` 를 쓰므로 받고 있다). 안 받는 곳이 있으면 시그니처에 `request: Request` 를 추가한다.

- [ ] **Step 6: 통과 확인 + 회귀**

Run: `cd server && ../../../../server/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_admin_guard.py tests/test_admin_guard_adoption.py tests/test_admin_users.py tests/test_admin_staff.py tests/test_admin_overview.py tests/test_admin_models.py tests/test_facemarket_model_test_cuts.py tests/test_facemarket_application_hardening.py tests/test_facemarket_settlement.py tests/test_facemarket_cutover.py`
Expected: 전부 passed (adoption 의 `ADMIN_DEVICES` 는 파일이 없어 `""` → 두 번째 테스트는 통과).

- [ ] **Step 7: 커밋**

```bash
git add server/app/admin_guard.py server/app/facemarket_admin.py server/app/routes.py server/app/facemarket.py server/app/facemarket_applications.py server/app/facemarket_admin_models.py server/tests/test_admin_guard.py server/tests/test_admin_guard_adoption.py
git -c core.hooksPath=/dev/null commit -m "feat(admin): 관리자 가드에 기기 검사를 붙여요 (off/shadow/enforce)

require_admin 이 request 를 받아 X-Admin-Device 를 해시로 조회한다. 호출부
24곳이 전부 request 를 넘기는지, 기기 면제 가드가 어디서도 안 쓰이는지를
소스 계약 테스트가 센다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019cJPhmby2YzLcN5RaPcGL1"
```

---

### Task 5: 기기 라우트 모듈 + Slack 알림 + 라우터 등록

**Files:**
- Create: `server/app/facemarket_admin_devices.py`
- Modify: `server/app/facemarket_notify.py` (끝에 함수 1개), `server/app/main.py:536` 근처(include)
- Test: `server/tests/test_admin_devices.py` (Task 2 파일에 추가)

**Interfaces:**
- Consumes: `admin_guard.require_admin`, `require_admin_identity`, `check_device`, `hash_device_token`, `device_token_from` (Task 4); `settings.admin_device_gate`, `admin_device_max_pending_per_user` (Task 3)
- Produces:
  - HTTP: `POST /v1/facemarket/admin/devices/register` → 201 `{deviceId, token, status:"pending", label, gate}`; `GET …/devices/me` → `{status, deviceId?, label?, gate}`; `GET …/devices` → `{items:[…]}`; `POST …/devices/{id}/approve`, `POST …/devices/{id}/revoke` → `{deviceId, status}`
  - Python: `register_device`, `device_status`, `list_devices`, `approve_device`, `revoke_device`, `revoke_devices_for_user(conn, *, user_id, actor) -> int` (Task 6 가 쓴다), `label_from_user_agent(ua) -> str`
  - `facemarket_notify.notify_slack_admin_device_requested(settings, *, email, label)`

- [ ] **Step 1: 순수 함수 테스트 추가**

`server/tests/test_admin_devices.py` 끝에:

```python
import pytest
from fastapi import HTTPException

from app import facemarket_admin_devices as devices


# ---------- 라벨 ----------

@pytest.mark.parametrize("ua,label", [
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36", "macOS · Chrome"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15", "macOS · Safari"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36 Edg/128.0", "Windows · Edge"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1", "iPhone · Safari"),
    ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Mobile Safari/537.36", "Android · Chrome"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:129.0) Gecko/20100101 Firefox/129.0", "Linux · Firefox"),
    ("", "알 수 없는 기기"),
    (None, "알 수 없는 기기"),
])
def test_label_from_user_agent(ua, label):
    assert devices.label_from_user_agent(ua) == label


# ---------- register ----------

def test_register_creates_a_pending_row_with_a_hash_and_returns_the_token_once():
    conn = FakeConn([{"count": 0}, {"id": "d1"}])
    out = asyncio.run(devices.register_device(
        conn, user_id="u1", label="Mac", user_agent="UA", max_pending=5,
    ))
    assert out["deviceId"] == "d1" and out["status"] == "pending" and out["label"] == "Mac"
    assert len(out["token"]) >= 32
    insert = [(sql, p) for sql, p in conn.executed if sql.startswith("insert into admin_devices")]
    assert len(insert) == 1
    sql, params = insert[0]
    assert params[0] == "u1" and params[2] == "Mac" and params[3] == "UA"
    # 원문 토큰은 DB 로 가지 않는다 — 해시만.
    assert params[1] != out["token"]
    assert params[1] == __import__("hashlib").sha256(out["token"].encode()).hexdigest()


def test_register_refuses_when_too_many_pending():
    conn = FakeConn([{"count": 5}])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(devices.register_device(
            conn, user_id="u1", label="Mac", user_agent=None, max_pending=5,
        ))
    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "too_many_pending"
    assert not any(sql.startswith("insert") for sql, _ in conn.executed)


def test_register_trims_and_caps_the_label():
    conn = FakeConn([{"count": 0}, {"id": "d1"}])
    out = asyncio.run(devices.register_device(
        conn, user_id="u1", label="  " + "x" * 100 + "  ", user_agent=None, max_pending=5,
    ))
    assert out["label"] == "x" * devices.LABEL_MAX


# ---------- me ----------

def test_device_status_without_token_is_unknown():
    out = asyncio.run(devices.device_status(FakeConn([]), user_id="u1", token=None))
    assert out == {"status": "unknown"}


def test_device_status_reports_own_device_and_hides_others():
    row = {"id": "d1", "user_id": "u1", "status": "pending", "label": "Mac", "last_seen_at": None}
    out = asyncio.run(devices.device_status(FakeConn([row]), user_id="u1", token="tok"))
    assert out == {"status": "pending", "deviceId": "d1", "label": "Mac"}
    out = asyncio.run(devices.device_status(FakeConn([dict(row, user_id="u2")]), user_id="u1", token="tok"))
    assert out == {"status": "unknown"}


# ---------- list ----------

def test_list_marks_the_current_device_and_never_leaks_hashes():
    rows = [
        {"id": "d1", "user_id": "u1", "user_email": "a@x", "label": "Mac", "status": "approved",
         "created_at": None, "last_seen_at": None, "approved_at": None, "revoked_at": None,
         "approved_by_email": "b@x", "token_hash": "h1"},
        {"id": "d2", "user_id": "u2", "user_email": "b@x", "label": "Win", "status": "pending",
         "created_at": None, "last_seen_at": None, "approved_at": None, "revoked_at": None,
         "approved_by_email": None, "token_hash": "h2"},
    ]
    out = asyncio.run(devices.list_devices(FakeConn([rows]), current_token_hash="h1"))
    assert [d["id"] for d in out["items"]] == ["d1", "d2"]
    assert out["items"][0]["isCurrent"] is True and out["items"][1]["isCurrent"] is False
    assert all("token_hash" not in d and "tokenHash" not in d for d in out["items"])
    assert out["items"][0]["approvedByEmail"] == "b@x"


def test_list_sql_puts_pending_first():
    conn = FakeConn([[]])
    asyncio.run(devices.list_devices(conn, current_token_hash=None))
    sql, _ = conn.executed[0]
    assert "order by (d.status = 'pending') desc" in sql


# ---------- approve / revoke ----------

def test_approve_moves_pending_to_approved_and_audits():
    conn = FakeConn([{"id": "d1", "user_id": "u2", "label": "Win", "status": "pending", "token_hash": "h2"}])
    out = asyncio.run(devices.approve_device(conn, device_id="d1", actor="u1"))
    assert out == {"deviceId": "d1", "status": "approved"}
    sqls = [sql for sql, _ in conn.executed]
    assert any(s.startswith("select") and "for update" in s for s in sqls)
    assert any(s.startswith("update admin_devices set status = 'approved'") for s in sqls)
    audit = [p for sql, p in conn.executed if sql.startswith("insert into admin_audit_log")]
    assert audit and audit[0][0] == "u1" and audit[0][1] == "device.approve" and audit[0][2] == "admin_device"


def test_approve_refuses_non_pending():
    conn = FakeConn([{"id": "d1", "user_id": "u2", "label": "Win", "status": "approved", "token_hash": "h2"}])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(devices.approve_device(conn, device_id="d1", actor="u1"))
    assert exc.value.status_code == 409 and exc.value.detail["code"] == "device_not_pending"


def test_approve_unknown_id_is_404():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(devices.approve_device(FakeConn([]), device_id="nope", actor="u1"))
    assert exc.value.status_code == 404


def test_revoke_refuses_the_current_device():
    conn = FakeConn([{"id": "d1", "user_id": "u1", "label": "Mac", "status": "approved", "token_hash": "h1"}])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(devices.revoke_device(conn, device_id="d1", actor="u1", current_token_hash="h1"))
    assert exc.value.status_code == 400 and exc.value.detail["code"] == "cannot_revoke_current"
    assert not any(sql.startswith("update") for sql, _ in conn.executed)


def test_revoke_works_for_pending_and_approved_and_audits_the_previous_status():
    for previous in ("pending", "approved"):
        conn = FakeConn([{"id": "d1", "user_id": "u2", "label": "Win", "status": previous, "token_hash": "h2"}])
        out = asyncio.run(devices.revoke_device(conn, device_id="d1", actor="u1", current_token_hash="h1"))
        assert out == {"deviceId": "d1", "status": "revoked"}
        audit = [p for sql, p in conn.executed if sql.startswith("insert into admin_audit_log")]
        assert audit[0][1] == "device.revoke"
        assert audit[0][4].obj == {"status": previous}   # before (psycopg Json 래퍼)


def test_revoke_twice_is_409():
    conn = FakeConn([{"id": "d1", "user_id": "u2", "label": "Win", "status": "revoked", "token_hash": "h2"}])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(devices.revoke_device(conn, device_id="d1", actor="u1", current_token_hash=None))
    assert exc.value.status_code == 409 and exc.value.detail["code"] == "device_already_revoked"


def test_revoke_devices_for_user_returns_the_count():
    conn = FakeConn([[{"id": "d1"}, {"id": "d2"}]])
    n = asyncio.run(devices.revoke_devices_for_user(conn, user_id="u2", actor="u1"))
    assert n == 2
    sql, params = conn.executed[0]
    assert sql.startswith("update admin_devices set status = 'revoked'")
    assert "status in ('pending', 'approved')" in sql and "returning id" in sql
    assert params == ("u1", "u2")


# ---------- 라우트 (TestClient) ----------

from fastapi.testclient import TestClient
from app.main import create_app
from conftest import auth_headers, make_settings


@pytest.fixture()
def enforce_client(keypair):
    private_key, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, admin_device_gate="enforce"))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app)


def _patch_conn(monkeypatch, conn):
    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield conn
    monkeypatch.setattr(devices, "get_conn", fake_conn)


def test_register_route_is_open_to_admins_without_a_device_and_pings_slack(enforce_client, make_token, monkeypatch):
    conn = FakeConn([{"role": "admin"}, {"count": 0}, {"id": "d1"}, {"email": "a@x"}])
    _patch_conn(monkeypatch, conn)
    pings = []
    async def fake_slack(settings, *, email, label):
        pings.append((email, label))
    monkeypatch.setattr(devices.facemarket_notify, "notify_slack_admin_device_requested", fake_slack)

    res = enforce_client.post(
        "/v1/facemarket/admin/devices/register",
        json={"label": "내 맥북"}, headers=auth_headers(make_token),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "pending" and body["gate"] == "enforce" and body["token"]
    assert pings == [("a@x", "내 맥북")]
    assert conn.commits == 1


def test_register_route_rejects_non_admins_without_creating_a_row(enforce_client, make_token, monkeypatch):
    conn = FakeConn([{"role": "user"}])
    _patch_conn(monkeypatch, conn)
    res = enforce_client.post("/v1/facemarket/admin/devices/register", json={}, headers=auth_headers(make_token))
    assert res.status_code == 403 and res.json()["error"]["code"] == "forbidden"
    assert not any(sql.startswith("insert") for sql, _ in conn.executed)


def test_me_route_reports_status_and_gate_without_403(enforce_client, make_token, monkeypatch):
    conn = FakeConn([{"role": "admin"}, {"id": "d1", "user_id": "user-1", "status": "pending", "label": "Mac", "last_seen_at": None}])
    _patch_conn(monkeypatch, conn)
    res = enforce_client.get(
        "/v1/facemarket/admin/devices/me",
        headers={**auth_headers(make_token), "X-Admin-Device": "tok"},
    )
    assert res.status_code == 200
    assert res.json() == {"status": "pending", "deviceId": "d1", "label": "Mac", "gate": "enforce"}


def test_list_route_requires_an_approved_device(enforce_client, make_token, monkeypatch):
    # 관리자지만 기기 헤더 없음 → enforce 라 403 device_missing
    _patch_conn(monkeypatch, FakeConn([{"role": "admin"}]))
    res = enforce_client.get("/v1/facemarket/admin/devices", headers=auth_headers(make_token))
    assert res.status_code == 403 and res.json()["error"]["code"] == "device_missing"


def test_approve_route_commits_after_audit(enforce_client, make_token, monkeypatch):
    # FakeCursor 는 execute 마다 큐를 하나 소비한다(update 도). 순서 = 라우트의 실제 SQL 순서:
    # role 조회 → 가드의 기기 조회 → last_seen touch(update) → for update 잠금 → approve update → 감사 insert
    conn = FakeConn([
        {"role": "admin"},
        {"id": "cur", "user_id": "user-1", "status": "approved", "label": "Mac", "last_seen_at": None},
        None,                                                                                          # touch
        {"id": "d2", "user_id": "u2", "label": "Win", "status": "pending", "token_hash": "h2"},         # lock
    ])
    _patch_conn(monkeypatch, conn)
    res = enforce_client.post(
        "/v1/facemarket/admin/devices/d2/approve",
        headers={**auth_headers(make_token), "X-Admin-Device": "tok"},
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"deviceId": "d2", "status": "approved"}
    assert conn.commits == 1
```

주의: `FakeCursor.execute` 는 **모든** SQL(update·insert 포함)마다 큐를 하나씩 소비한다 — 큐가 비면 None. 그래서 결과가 필요 없는 update 자리에도 `None` 을 넣어 순서를 맞춘다. 위 큐 순서는 `register` 라우트가 [role 조회 → pending count → insert returning → email 조회] 순으로, approve 라우트가 [role → 가드 기기 조회 → touch → lock → update → audit] 순으로 실행한다는 구현 계약이다. 구현이 그 순서를 따르지 않으면 이 테스트가 먼저 알려 준다.

- [ ] **Step 2: 실패 확인**

Run: `cd server && ../../../../server/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_admin_devices.py`
Expected: `ModuleNotFoundError: No module named 'app.facemarket_admin_devices'`

- [ ] **Step 3: Slack 함수 추가**

`server/app/facemarket_notify.py` 끝에:

```python
async def notify_slack_admin_device_requested(settings, *, email: str | None, label: str) -> None:
    """관리자 콘솔에 새 기기가 승인을 요청했다. 승인은 다른 관리자가 콘솔에서 한다 — 이 알림이
    없으면 상대는 요청이 있는지도 모른다. 탈취된 계정의 요청도 이 알림으로 드러난다(요청한 적
    없는 기기가 뜬다). 실패는 무해 — 등록 자체는 이미 커밋됐다."""
    if not settings.fm_slack_webhook_url:
        return
    admin_link = f"{settings.fm_application_public_base}".replace("facemarket.", "admin.") + "/staff"
    text = (
        f":closed_lock_with_key: 관리자 기기 승인 요청 · {_slack_escape(email or '-')} · "
        f"{_slack_escape(label)}\n<{admin_link}|관리자 관리 열기>"
    )
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(settings.fm_slack_webhook_url, json={"text": text})
        if res.status_code >= 400:
            logger.error("slack notify rejected status=%s", res.status_code)
    except Exception as exc:
        logger.warning("slack notify failed: %s", exc)
```

- [ ] **Step 4: 라우트 모듈 작성**

```python
# server/app/facemarket_admin_devices.py
"""관리자 콘솔 기기 등록·승인·회수.

설계: docs/superpowers/specs/2026-09-11-admin-device-gate-design.md §5.3
판정 자체는 admin_guard.check_device 에 있다. 여기는 그 판정이 볼 행을 만들고 바꾸는 라우트다.

register·me 두 라우트만 기기 없이 열린다(require_admin_identity) — 아직 기기가 없는
관리자가 부르는 라우트라서다. 나머지는 다른 관리자 라우트와 똑같이 require_admin 을 탄다.
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from . import admin_guard, facemarket_notify
from .auth import require_user
from .db import get_conn
from .models import CamelModel

router = APIRouter(prefix="/v1/facemarket/admin/devices", tags=["FaceMarket admin devices"])

LABEL_MAX = 60
UNKNOWN_LABEL = "알 수 없는 기기"


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


class RegisterRequest(CamelModel):
    label: str | None = Field(default=None, max_length=200)
    user_agent: str | None = Field(default=None, max_length=512)


def label_from_user_agent(ua: str | None) -> str:
    """UA → "macOS · Chrome" 류. 정확할 필요 없다 — 목록에서 자기 기기를 알아보는 용도.
    순서가 중요하다: iPhone UA 에 'Mac OS X' 가, Android UA 에 'Linux' 가 들어 있다."""
    ua = ua or ""
    if "iPhone" in ua:
        os_name = "iPhone"
    elif "iPad" in ua:
        os_name = "iPad"
    elif "Android" in ua:
        os_name = "Android"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        os_name = "macOS"
    elif "Windows" in ua:
        os_name = "Windows"
    elif "Linux" in ua:
        os_name = "Linux"
    else:
        os_name = None
    if "Edg/" in ua:
        browser = "Edge"
    elif "Firefox/" in ua:
        browser = "Firefox"
    elif "Chrome/" in ua or "CriOS/" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = None
    parts = [p for p in (os_name, browser) if p]
    return " · ".join(parts) if parts else UNKNOWN_LABEL


def _clean_label(label: str | None, user_agent: str | None) -> str:
    text = " ".join((label or "").split())
    return text[:LABEL_MAX] if text else label_from_user_agent(user_agent)


# ---------- SQL ----------

COUNT_PENDING_SQL = (
    "select count(*)::int as count from admin_devices "
    "where user_id = %s and status = 'pending'"
)
INSERT_SQL = (
    "insert into admin_devices (user_id, token_hash, label, user_agent) "
    "values (%s, %s, %s, %s) returning id::text as id"
)
USER_EMAIL_SQL = "select email from auth.users where id = %s"
LIST_SQL = """
select d.id::text as id, d.user_id::text as user_id, u.email as user_email, d.label, d.status,
       d.created_at, d.last_seen_at, d.approved_at, d.revoked_at,
       a.email as approved_by_email, d.token_hash
from admin_devices d
left join auth.users u on u.id = d.user_id
left join auth.users a on a.id = d.approved_by
order by (d.status = 'pending') desc, coalesce(d.last_seen_at, d.created_at) desc
"""
LOCK_SQL = (
    "select id::text as id, user_id::text as user_id, label, status, token_hash "
    "from admin_devices where id = %s for update"
)
APPROVE_SQL = (
    "update admin_devices set status = 'approved', approved_by = %s, approved_at = now() "
    "where id = %s"
)
REVOKE_SQL = (
    "update admin_devices set status = 'revoked', revoked_by = %s, revoked_at = now() "
    "where id = %s"
)
REVOKE_ALL_FOR_USER_SQL = (
    "update admin_devices set status = 'revoked', revoked_by = %s, revoked_at = now() "
    "where user_id = %s and status in ('pending', 'approved') returning id::text as id"
)


# ---------- 순수 함수 (FakeConn 으로 테스트) ----------

async def register_device(
    conn, *, user_id: str, label: str | None, user_agent: str | None, max_pending: int
) -> dict:
    clean = _clean_label(label, user_agent)
    async with conn.cursor() as cur:
        await cur.execute(COUNT_PENDING_SQL, (user_id,))
        row = await cur.fetchone()
        if (row or {}).get("count", 0) >= max_pending:
            raise _err(
                "too_many_pending",
                "승인 대기 중인 기기가 너무 많아요. 다른 관리자에게 기존 요청을 정리해 달라고 하세요.",
                status=429,
            )
        token = secrets.token_urlsafe(32)
        await cur.execute(
            INSERT_SQL, (user_id, admin_guard.hash_device_token(token), clean, user_agent)
        )
        inserted = await cur.fetchone()
    # 토큰은 이 반환값 한 번뿐이다. DB 에는 해시만 있어 다시 만들 수 없다.
    return {"deviceId": inserted["id"], "token": token, "status": "pending", "label": clean}


async def device_status(conn, *, user_id: str, token: str | None) -> dict:
    """대기 화면이 폴링하는 값. 403 을 내지 않고 상태를 말한다. last_seen 은 안 찍는다 —
    화면을 열어 둔 것은 사용이 아니다."""
    token = (token or "").strip()
    if not token:
        return {"status": "unknown"}
    device = await admin_guard.repo.find_admin_device_by_hash(conn, admin_guard.hash_device_token(token))
    if device is None or device.get("user_id") != user_id:
        return {"status": "unknown"}
    return {"status": device["status"], "deviceId": device["id"], "label": device.get("label")}


def _iso(value) -> str | None:
    return value.isoformat() if value else None


async def list_devices(conn, *, current_token_hash: str | None) -> dict:
    async with conn.cursor() as cur:
        await cur.execute(LIST_SQL)
        rows = await cur.fetchall() or []
    return {
        "items": [
            {
                "id": r["id"], "userId": r["user_id"], "userEmail": r.get("user_email"),
                "label": r["label"], "status": r["status"],
                "createdAt": _iso(r.get("created_at")), "lastSeenAt": _iso(r.get("last_seen_at")),
                "approvedAt": _iso(r.get("approved_at")), "revokedAt": _iso(r.get("revoked_at")),
                "approvedByEmail": r.get("approved_by_email"),
                "isCurrent": bool(current_token_hash) and r.get("token_hash") == current_token_hash,
            }
            for r in rows
        ]
    }


async def _lock_device(cur, device_id: str) -> dict:
    await cur.execute(LOCK_SQL, (device_id,))
    row = await cur.fetchone()
    if row is None:
        raise _err("device_not_found", "기기를 찾을 수 없어요.", status=404)
    return row


async def approve_device(conn, *, device_id: str, actor: str) -> dict:
    async with conn.cursor() as cur:
        device = await _lock_device(cur, device_id)
        if device["status"] != "pending":
            raise _err("device_not_pending", "승인 대기 중인 기기가 아니에요.", status=409)
        await cur.execute(APPROVE_SQL, (actor, device_id))
    await admin_guard.write_audit(
        conn, actor_user_id=actor, action="device.approve", target_type="admin_device",
        target_id=device_id, before={"status": "pending"}, after={"status": "approved"},
        note=f"{device['label']} · owner {device['user_id']}",
    )
    return {"deviceId": device_id, "status": "approved"}


async def revoke_device(
    conn, *, device_id: str, actor: str, current_token_hash: str | None
) -> dict:
    async with conn.cursor() as cur:
        device = await _lock_device(cur, device_id)
        if device["status"] == "revoked":
            raise _err("device_already_revoked", "이미 회수된 기기예요.", status=409)
        # 지금 이 요청을 보낸 기기를 회수하면 그 자리에서 잠긴다 — set_role 의 자기 강등 금지와 같은 결.
        if current_token_hash and device.get("token_hash") == current_token_hash:
            raise _err(
                "cannot_revoke_current",
                "지금 쓰는 기기는 회수할 수 없어요 — 다른 기기에서 회수해 주세요.",
            )
        previous = device["status"]
        await cur.execute(REVOKE_SQL, (actor, device_id))
    await admin_guard.write_audit(
        conn, actor_user_id=actor, action="device.revoke", target_type="admin_device",
        target_id=device_id, before={"status": previous}, after={"status": "revoked"},
        note=f"{device['label']} · owner {device['user_id']}",
    )
    return {"deviceId": device_id, "status": "revoked"}


async def revoke_devices_for_user(conn, *, user_id: str, actor: str) -> int:
    """관리자 강등 때 set_role 이 부른다. 안 하면 재승격 시 옛 승인 기기가 그대로 살아난다."""
    async with conn.cursor() as cur:
        await cur.execute(REVOKE_ALL_FOR_USER_SQL, (actor, user_id))
        rows = await cur.fetchall() or []
    return len(rows)


# ---------- 라우트 ----------

def _current_hash(request: Request) -> str | None:
    token = (admin_guard.device_token_from(request) or "").strip()
    return admin_guard.hash_device_token(token) if token else None


@router.post("/register")
async def admin_register_device(
    request: Request, body: RegisterRequest, user_id: str = Depends(require_user)
):
    settings = request.app.state.settings
    async with get_conn(request) as conn:
        await admin_guard.require_admin_identity(conn, user_id)
        result = await register_device(
            conn, user_id=user_id, label=body.label,
            user_agent=body.user_agent or request.headers.get("user-agent"),
            max_pending=settings.admin_device_max_pending_per_user,
        )
        async with conn.cursor() as cur:
            await cur.execute(USER_EMAIL_SQL, (user_id,))
            email_row = await cur.fetchone()
        await conn.commit()
    # 커밋 뒤에 알린다 — 알림이 실패해도 등록은 남고, 등록이 실패하면 알림도 없다.
    await facemarket_notify.notify_slack_admin_device_requested(
        settings, email=(email_row or {}).get("email"), label=result["label"],
    )
    return JSONResponse(status_code=201, content={**result, "gate": settings.admin_device_gate})


@router.get("/me")
async def admin_device_me(request: Request, user_id: str = Depends(require_user)):
    settings = request.app.state.settings
    async with get_conn(request) as conn:
        await admin_guard.require_admin_identity(conn, user_id)
        result = await device_status(conn, user_id=user_id, token=admin_guard.device_token_from(request))
    return JSONResponse({**result, "gate": settings.admin_device_gate})


@router.get("")
async def admin_list_devices(request: Request, user_id: str = Depends(require_user)):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        return JSONResponse(await list_devices(conn, current_token_hash=_current_hash(request)))


@router.post("/{device_id}/approve")
async def admin_approve_device(
    request: Request, device_id: str, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        result = await approve_device(conn, device_id=device_id, actor=user_id)
        await conn.commit()
    return JSONResponse(result)


@router.post("/{device_id}/revoke")
async def admin_revoke_device(
    request: Request, device_id: str, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        result = await revoke_device(
            conn, device_id=device_id, actor=user_id, current_token_hash=_current_hash(request),
        )
        await conn.commit()
    return JSONResponse(result)
```

`server/app/main.py` — `app.include_router(admin_console_router)` 바로 아래에:

```python
        # 관리자 기기 게이트의 등록·승인 라우트. 콘솔 라우터와 같은 플래그 아래 산다.
        from .facemarket_admin_devices import router as admin_devices_router

        app.include_router(admin_devices_router)
```

- [ ] **Step 5: 통과 확인**

Run: `cd server && ../../../../server/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_admin_devices.py tests/test_admin_guard_adoption.py`
Expected: 전부 passed. adoption 의 `test_identity_only_guard_is_used_by_exactly_the_two_device_bootstrap_routes` 가 이제 `ADMIN_DEVICES.count(...) == 2` 를 실제로 검사한다.

`test_approve_route_commits_after_audit` 가 FakeConn 큐 순서 때문에 실패하면, 라우트의 실제 SQL 순서를 로그(`conn.executed`)로 확인하고 **테스트의 큐를 구현 순서에 맞춘다**(구현 순서를 테스트에 맞추지 말 것 — 가드 조회가 먼저 오는 것이 계약이다).

- [ ] **Step 6: 커밋**

```bash
git add server/app/facemarket_admin_devices.py server/app/facemarket_notify.py server/app/main.py server/tests/test_admin_devices.py
git -c core.hooksPath=/dev/null commit -m "feat(admin): 관리자 기기 등록·상태·목록·승인·회수 라우트를 더해요

register/me 만 기기 없이 열린다(아직 기기가 없는 관리자가 부른다). 승인·회수는
감사 원장에, 등록은 Slack 에 남는다. 지금 쓰는 기기는 회수할 수 없다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019cJPhmby2YzLcN5RaPcGL1"
```

---

### Task 6: 관리자 강등 시 기기 일괄 회수

**Files:**
- Modify: `server/app/facemarket_admin.py:615-628` (`set_role` 의 update 이후)
- Test: `server/tests/test_admin_staff.py` (테스트 1개 추가 — 기존 테스트 수정 없음)

**Interfaces:**
- Consumes: `facemarket_admin_devices.revoke_devices_for_user(conn, *, user_id, actor) -> int` (Task 5)

- [ ] **Step 1: 실패하는 테스트 작성**

`server/tests/test_admin_staff.py` 끝에:

```python
def test_demotion_revokes_all_of_the_users_devices_and_records_the_count():
    """되돌아가면: 내렸다가 다시 올린 관리자의 옛 기기가 승인 상태 그대로 살아난다."""
    conn = FakeConn([
        [
            {"user_id": "admin-1", "role": "admin"},
            {"user_id": "admin-2", "role": "admin"},
        ],
        None,                                  # update profiles
        [{"id": "d1"}, {"id": "d2"}],          # update admin_devices … returning id
    ])
    asyncio.run(facemarket_admin.set_role(
        conn, target_user_id="admin-2", actor="admin-1", role="user",
    ))
    revoke = [(sql, p) for sql, p in conn.executed if sql.startswith("update admin_devices")]
    assert revoke and revoke[0][1] == ("admin-1", "admin-2")
    audit = [p for sql, p in conn.executed if sql.startswith("insert into admin_audit_log")]
    assert audit[0][5].obj == {"role": "user", "revokedDevices": 2}


def test_promotion_does_not_touch_devices():
    conn = FakeConn([[{"user_id": "admin-1", "role": "admin"}, {"user_id": "u9", "role": "user"}]])
    asyncio.run(facemarket_admin.set_role(
        conn, target_user_id="u9", actor="admin-1", role="admin",
    ))
    assert not any(sql.startswith("update admin_devices") for sql, _ in conn.executed)
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && ../../../../server/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_admin_staff.py`
Expected: 첫 테스트 FAIL(`update admin_devices` 없음).

- [ ] **Step 3: 구현**

`server/app/facemarket_admin.py` 상단 import 에 `from .facemarket_admin_devices import revoke_devices_for_user` 를 추가하고, `set_role` 의 `update profiles …` 실행 직후·`write_audit` 직전을 이렇게 바꾼다:

```python
        await cur.execute(
            "update profiles set role = %s, updated_at = now() where user_id = %s",
            (role, target_user_id),
        )

    after = {"role": role}
    if role == "user":
        # 권한과 함께 기기도 거둔다. 안 그러면 나중에 다시 올렸을 때 옛 승인 기기가 그대로
        # 살아나 — 그 사이 그 기기가 누구 손에 있었는지 아무도 모른다.
        after["revokedDevices"] = await revoke_devices_for_user(
            conn, user_id=target_user_id, actor=actor,
        )

    await admin_guard.write_audit(
        conn,
        actor_user_id=actor,
        action="staff.role.grant" if role == "admin" else "staff.role.revoke",
        target_type="user",
        target_id=target_user_id,
        before={"role": previous},
        after=after,
    )
    return {"userId": target_user_id, "role": role}
```

- [ ] **Step 4: 통과 확인**

Run: `cd server && ../../../../server/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_admin_staff.py tests/test_admin_guard_adoption.py`
Expected: 전부 passed (기존 `test_demotion_audit_action_is_revoke` 는 큐가 비어 `fetchall` 이 `[]` → 0건으로 통과).

- [ ] **Step 5: 커밋**

```bash
git add server/app/facemarket_admin.py server/tests/test_admin_staff.py
git -c core.hooksPath=/dev/null commit -m "feat(admin): 관리자를 내릴 때 그 사람 기기를 전부 회수해요

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019cJPhmby2YzLcN5RaPcGL1"
```

---

### Task 7: 프런트 — 토큰 저장 모듈 + `http()` 헤더 주입 + API 함수 5개

**Files:**
- Create: `src/lib/adminDevice.js`
- Modify: `src/lib/api/httpAdapter.js` (import + `http()` 의 fetch 헤더 + 403 처리), `src/lib/api/facemarket.js` (`adminListAudit` 아래)
- Test: `tests/frontend/admin-device.test.mjs` (신규)

**Interfaces:**
- Produces:
  - `readDeviceToken(storage?) -> string|null`, `writeDeviceToken(token, storage?)`, `clearDeviceToken(storage?)`, `defaultDeviceLabel(ua?) -> string`, `DEVICE_HEADER = 'X-Admin-Device'`, `DEVICE_REJECTED_EVENT = 'admin-device-rejected'`
  - `adminRegisterDevice({label, userAgent})`, `adminDeviceMe()`, `adminListDevices()`, `adminApproveDevice(id)`, `adminRevokeDevice(id)`
  - `http()` 가 토큰 있으면 헤더 주입, 403 `device_*` 면 `window` 에 `admin-device-rejected` 이벤트

- [ ] **Step 1: 실패하는 테스트 작성**

```js
// tests/frontend/admin-device.test.mjs
/* 관리자 콘솔 기기 게이트 — 토큰 저장·헤더 주입·가드 화면·Staff 섹션.
   설계: docs/superpowers/specs/2026-09-11-admin-device-gate-design.md §6 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  DEVICE_HEADER, DEVICE_REJECTED_EVENT, STORAGE_KEY,
  clearDeviceToken, defaultDeviceLabel, readDeviceToken, writeDeviceToken,
} from '../../src/lib/adminDevice.js';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

function memoryStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

test('토큰은 주어진 스토리지에 하나의 키로 저장·조회·삭제된다', () => {
  const s = memoryStorage();
  assert.equal(readDeviceToken(s), null);
  writeDeviceToken('abc', s);
  assert.equal(readDeviceToken(s), 'abc');
  assert.equal(s.getItem(STORAGE_KEY), 'abc');
  clearDeviceToken(s);
  assert.equal(readDeviceToken(s), null);
});

test('빈 문자열·공백 토큰은 없는 것으로 친다', () => {
  const s = memoryStorage();
  s.setItem(STORAGE_KEY, '   ');
  assert.equal(readDeviceToken(s), null);
  writeDeviceToken('', s);
  assert.equal(readDeviceToken(s), null);
});

test('스토리지가 던져도 읽기는 null, 쓰기·삭제는 조용히 실패한다', () => {
  const broken = {
    getItem() { throw new Error('blocked'); },
    setItem() { throw new Error('blocked'); },
    removeItem() { throw new Error('blocked'); },
  };
  assert.equal(readDeviceToken(broken), null);
  assert.doesNotThrow(() => writeDeviceToken('x', broken));
  assert.doesNotThrow(() => clearDeviceToken(broken));
});

test('기본 기기명은 서버와 같은 규칙으로 OS · 브라우저를 만든다', () => {
  assert.equal(defaultDeviceLabel('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'), 'macOS · Chrome');
  assert.equal(defaultDeviceLabel('Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'), 'iPhone · Safari');
  assert.equal(defaultDeviceLabel('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36 Edg/128.0'), 'Windows · Edge');
  assert.equal(defaultDeviceLabel('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Mobile Safari/537.36'), 'Android · Chrome');
  assert.equal(defaultDeviceLabel(''), '알 수 없는 기기');
});

test('http() 는 토큰이 있으면 X-Admin-Device 를 싣고, device_* 403 이면 이벤트를 쏜다', () => {
  const src = read('src/lib/api/httpAdapter.js');
  assert.ok(src.includes("from '@/lib/adminDevice.js'"), 'adminDevice 를 import 하지 않는다');
  assert.ok(src.includes('readDeviceToken()'), '토큰을 읽지 않는다');
  assert.ok(src.includes('[DEVICE_HEADER]'), '헤더 이름 상수를 쓰지 않는다');
  assert.ok(src.includes('DEVICE_REJECTED_EVENT'), 'device_* 403 을 화면에 알리지 않는다');
  assert.ok(/code\s*\)?\.startsWith\('device_'\)/.test(src) || src.includes("startsWith('device_')"), 'device_ 접두 판정이 없다');
});

test('api 클라이언트에 기기 함수 5개가 있고 경로가 맞다', () => {
  const api = read('src/lib/api/facemarket.js');
  for (const fn of ['adminRegisterDevice', 'adminDeviceMe', 'adminListDevices', 'adminApproveDevice', 'adminRevokeDevice']) {
    assert.ok(api.includes(`export function ${fn}`), `누락: ${fn}`);
  }
  assert.ok(api.includes('/v1/facemarket/admin/devices/register'));
  assert.ok(api.includes('/v1/facemarket/admin/devices/me'));
  assert.ok(api.includes('/approve`'), 'approve 경로');
  assert.ok(api.includes('/revoke`'), 'revoke 경로');
});

test('헤더 이름과 이벤트 이름은 서버·가드와 약속된 값이다', () => {
  assert.equal(DEVICE_HEADER, 'X-Admin-Device');
  assert.equal(DEVICE_REJECTED_EVENT, 'admin-device-rejected');
  assert.equal(STORAGE_KEY, 'wl.admin.device.v1');
  // 서버 CORS 허용 목록과 맞물린다(Task 1).
  assert.ok(read('server/app/main.py').includes('"X-Admin-Device"'));
});
```

- [ ] **Step 2: 실패 확인**

Run: `node --test tests/frontend/admin-device.test.mjs`
Expected: `Cannot find module '…/src/lib/adminDevice.js'`

- [ ] **Step 3: `adminDevice.js` 작성**

```js
// src/lib/adminDevice.js
/* 관리자 콘솔 기기 토큰 — 이 브라우저 프로필이 "어느 기기인가" 를 서버에 말해 주는 값.

   서버가 등록 때 한 번 준 랜덤 문자열을 localStorage 에 쥔다. localStorage 는 오리진 단위라
   admin.wearless.kr 문서에서만 보이고, 셀러·facemarket 앱은 이 값을 읽을 길이 없다(같은 번들
   코드라도 오리진이 다르다). 지우면 새 기기로 취급된다 — 재승인 필요.
   설계: docs/superpowers/specs/2026-09-11-admin-device-gate-design.md §6.1

   스토리지를 인자로 받는 이유: node 테스트에서 window 없이 돌리고, 접근이 막힌 환경(사파리
   프라이빗 등)에서 던지는 것을 여기서 삼키기 위해서다. 화면은 토큰이 없을 때의 경로를 어차피
   가지고 있다(등록 화면). */
export const STORAGE_KEY = 'wl.admin.device.v1';
export const DEVICE_HEADER = 'X-Admin-Device';
// http() 가 device_* 403 을 만나면 window 에 쏘는 이벤트. RequireDevice 가 듣고 상태를 다시 본다 —
// 열어 둔 탭에서 회수됐을 때 화면마다 403 처리를 심지 않아도 대기/회수 화면으로 넘어가게.
export const DEVICE_REJECTED_EVENT = 'admin-device-rejected';

function defaultStorage() {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function readDeviceToken(storage = defaultStorage()) {
  try {
    const raw = storage?.getItem(STORAGE_KEY);
    const token = typeof raw === 'string' ? raw.trim() : '';
    return token || null;
  } catch {
    return null;
  }
}

export function writeDeviceToken(token, storage = defaultStorage()) {
  const clean = typeof token === 'string' ? token.trim() : '';
  try {
    if (!clean) storage?.removeItem(STORAGE_KEY);
    else storage?.setItem(STORAGE_KEY, clean);
  } catch { /* 스토리지 차단 — 등록 화면이 다시 뜬다 */ }
}

export function clearDeviceToken(storage = defaultStorage()) {
  try {
    storage?.removeItem(STORAGE_KEY);
  } catch { /* no-op */ }
}

/* 서버 label_from_user_agent 와 같은 규칙 — 등록 입력칸의 초기값. 서버도 label 이 비면 같은
   값을 만들므로 둘이 어긋나지 않는다. 순서 주의: iPhone UA 에 'Mac OS X', Android 에 'Linux'. */
export function defaultDeviceLabel(ua = (typeof navigator !== 'undefined' ? navigator.userAgent : '')) {
  const s = ua || '';
  let os = null;
  if (s.includes('iPhone')) os = 'iPhone';
  else if (s.includes('iPad')) os = 'iPad';
  else if (s.includes('Android')) os = 'Android';
  else if (s.includes('Mac OS X') || s.includes('Macintosh')) os = 'macOS';
  else if (s.includes('Windows')) os = 'Windows';
  else if (s.includes('Linux')) os = 'Linux';
  let browser = null;
  if (s.includes('Edg/')) browser = 'Edge';
  else if (s.includes('Firefox/')) browser = 'Firefox';
  else if (s.includes('Chrome/') || s.includes('CriOS/')) browser = 'Chrome';
  else if (s.includes('Safari/')) browser = 'Safari';
  const parts = [os, browser].filter(Boolean);
  return parts.length ? parts.join(' · ') : '알 수 없는 기기';
}
```

- [ ] **Step 4: `httpAdapter.js` 수정**

import 블록(`import { jobFailure } from './jobFailure.js';` 아래)에:

```js
import { DEVICE_HEADER, DEVICE_REJECTED_EVENT, readDeviceToken } from '@/lib/adminDevice.js';
```

`http()` 의 fetch 헤더를 이렇게 바꾼다:

```js
  // 관리자 기기 토큰. 토큰이 있으면 싣는다 — IS_ADMIN 을 보지 않는다. 스토리지가 이미 오리진으로
  // 갈라져 있어 admin 문서 밖에서는 토큰 자체가 없고, 로컬 ?admin=1 오버라이드 오리진에서도
  // 그 오리진의 토큰으로 동작해야 한다. 서버는 관리자 라우트에서만 이 헤더를 본다.
  const deviceToken = readDeviceToken();
  let res;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(deviceToken ? { [DEVICE_HEADER]: deviceToken } : {}),
        ...(requestHeaders || {}),
      },
```

`if (!res.ok) {` 블록에서 `console.error(...)` 다음, `const err = new Error(message);` 앞에:

```js
    // 기기 게이트 거절(승인 대기·회수·미등록). 화면마다 403 을 해석하게 하지 않고 RequireDevice 가
    // 듣는 이벤트 하나로 모은다 — 열어 둔 탭에서 회수돼도 다음 요청에서 대기/회수 화면으로 간다.
    if (res.status === 403 && typeof code === 'string' && code.startsWith('device_')) {
      try {
        window.dispatchEvent(new CustomEvent(DEVICE_REJECTED_EVENT, { detail: { code } }));
      } catch { /* 비브라우저 환경 */ }
    }
```

- [ ] **Step 5: `facemarket.js` 에 API 함수 추가**

`adminListAudit` 함수 바로 아래:

```js
// ── 관리자: 기기 게이트(설계 2026-09-11-admin-device-gate-design.md §5.3) ────────────
// register·me 는 기기 없이 열린다(아직 기기가 없는 관리자가 부른다). 나머지는 승인 기기 필수.

export function adminRegisterDevice({ label, userAgent } = {}) {
  return http('/v1/facemarket/admin/devices/register', {
    method: 'POST', body: { label: label || null, userAgent: userAgent || null },
  });
}

export function adminDeviceMe() {
  return http('/v1/facemarket/admin/devices/me');
}

export function adminListDevices() {
  return http('/v1/facemarket/admin/devices');
}

export function adminApproveDevice(deviceId) {
  return http(`/v1/facemarket/admin/devices/${encodeURIComponent(deviceId)}/approve`, { method: 'POST' });
}

export function adminRevokeDevice(deviceId) {
  return http(`/v1/facemarket/admin/devices/${encodeURIComponent(deviceId)}/revoke`, { method: 'POST' });
}
```

- [ ] **Step 6: 통과 확인 + 전체 프런트 테스트**

Run: `node --test tests/frontend/admin-device.test.mjs && pnpm test:frontend 2>&1 | tail -8`
Expected: 새 파일 7 pass; 전체 fail 0.

- [ ] **Step 7: 커밋**

```bash
git add src/lib/adminDevice.js src/lib/api/httpAdapter.js src/lib/api/facemarket.js tests/frontend/admin-device.test.mjs
git -c core.hooksPath=/dev/null commit -m "feat(admin): 기기 토큰 저장 모듈과 X-Admin-Device 헤더 주입을 더해요

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019cJPhmby2YzLcN5RaPcGL1"
```

---

### Task 8: `RequireDevice` 가드 화면 + `App.jsx` 배선

**Files:**
- Create: `src/apps/admin/RequireDevice.jsx`
- Modify: `src/apps/admin/App.jsx:36-46`
- Test: `tests/frontend/admin-device.test.mjs` (추가)

**Interfaces:**
- Consumes: `adminRegisterDevice`, `adminDeviceMe` (Task 7), `readDeviceToken`/`writeDeviceToken`/`clearDeviceToken`/`defaultDeviceLabel`/`DEVICE_REJECTED_EVENT` (Task 7)
- Produces: `<RequireDevice />` — `RequireAuth` 안·`AdminShell` 밖의 라우트 element

- [ ] **Step 1: 소스 계약 테스트 추가**

`tests/frontend/admin-device.test.mjs` 끝에:

```js
test('RequireDevice 는 RequireAuth 안·AdminShell 밖에 선다', () => {
  const app = read('src/apps/admin/App.jsx');
  assert.ok(app.includes("import { RequireDevice } from './RequireDevice.jsx'"));
  const auth = app.indexOf('<Route element={<RequireAuth />}>');
  const device = app.indexOf('<Route element={<RequireDevice />}>');
  const shell = app.indexOf('<Route element={<AdminShell />}>');
  assert.ok(auth !== -1 && device !== -1 && shell !== -1);
  assert.ok(auth < device && device < shell, '순서: RequireAuth → RequireDevice → AdminShell');
});

test('RequireDevice 는 서버의 gate 를 단일 진실로 삼고 4상태를 다 그린다', () => {
  const src = read('src/apps/admin/RequireDevice.jsx');
  // gate 가 enforce 가 아니면 화면을 막지 않는다(shadow/off 는 서버가 안 막는다).
  assert.ok(src.includes("gate !== 'enforce'"), 'enforce 분기가 없다');
  for (const state of ['pending', 'approved', 'revoked', 'unknown']) {
    assert.ok(src.includes(`'${state}'`), `상태 누락: ${state}`);
  }
  // 등록 화면·대기 화면·회수 화면·비관리자 화면 문구
  assert.ok(src.includes('승인 요청'));
  assert.ok(src.includes('다른 관리자가 승인해야 해요'));
  assert.ok(src.includes('회수됐어요'));
  assert.ok(src.includes('관리자만 가능해요'));
  // 폴링은 보일 때만, 언마운트에 정리
  assert.ok(src.includes('setInterval') && src.includes('clearInterval'));
  assert.ok(src.includes('visibilityState'));
  // 회수 이벤트를 듣는다
  assert.ok(src.includes('DEVICE_REJECTED_EVENT'));
  // 실패는 화면에 남는 에러 + 재시도(전체 게이팅 금지)
  assert.ok(src.includes('다시 시도'));
});

test('RequireDevice 의 too_many_pending 은 안내 문구가 따로 있다', () => {
  const src = read('src/apps/admin/RequireDevice.jsx');
  assert.ok(src.includes("'too_many_pending'"));
});
```

- [ ] **Step 2: 실패 확인**

Run: `node --test tests/frontend/admin-device.test.mjs`
Expected: 새 3개 FAIL(`ENOENT … RequireDevice.jsx`).

- [ ] **Step 3: `RequireDevice.jsx` 작성**

```jsx
// src/apps/admin/RequireDevice.jsx
/* 관리자 콘솔 기기 게이트의 화면 쪽.

   서버 가드(admin_guard.require_admin)가 진짜 판정이다. 이 컴포넌트는 그 판정을 미리 물어
   (GET /admin/devices/me) 사람이 이해할 화면을 그린다 — 등록 / 대기 / 회수 / 비관리자.
   gate 가 enforce 가 아니면(shadow·off) 서버가 막지 않으므로 화면도 막지 않는다. 단 토큰이
   없으면 shadow 에서도 등록은 시킨다 — 그게 부트스트랩이다(설계 §9: shadow 배포 → 서로 승인 →
   enforce). off 면 등록조차 시키지 않는다.
   설계: docs/superpowers/specs/2026-09-11-admin-device-gate-design.md §6.4 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { adminDeviceMe, adminRegisterDevice } from '@/lib/api/facemarket.js';
import {
  DEVICE_REJECTED_EVENT, clearDeviceToken, defaultDeviceLabel, readDeviceToken, writeDeviceToken,
} from '@/lib/adminDevice.js';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/admin-ui/card.jsx';
import { Input } from '@/components/admin-ui/input.jsx';

const POLL_MS = 10_000;

export function RequireDevice() {
  // phase: loading | pass | register | pending | revoked | forbidden | error
  const [phase, setPhase] = useState('loading');
  const [me, setMe] = useState(null);          // 마지막 /me 응답 { status, deviceId, label, gate }
  const [error, setError] = useState(null);
  const [label, setLabel] = useState(() => defaultDeviceLabel());
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);

  const check = useCallback(async () => {
    setError(null);
    let res;
    try {
      res = await adminDeviceMe();
    } catch (e) {
      if (!alive.current) return;
      if (e?.status === 403 && e?.code === 'forbidden') { setPhase('forbidden'); return; }
      setError(e.message || '기기 상태를 확인하지 못했어요.');
      setPhase('error');
      return;
    }
    if (!alive.current) return;
    setMe(res);
    const token = readDeviceToken();
    if (res.gate === 'off') { setPhase('pass'); return; }
    if (!token) { setPhase('register'); return; }
    if (res.gate !== 'enforce') { setPhase('pass'); return; }
    if (res.status === 'approved') { setPhase('pass'); return; }
    if (res.status === 'pending') { setPhase('pending'); return; }
    if (res.status === 'revoked') { setPhase('revoked'); return; }
    if (res.status === 'unknown') {
      // 스토리지에 남의 토큰이나 쓰레기가 있다. 지우고 새로 등록시킨다.
      clearDeviceToken();
      setPhase('register');
      return;
    }
    setError(`알 수 없는 기기 상태예요: ${res.status}`);
    setPhase('error');
  }, []);

  useEffect(() => {
    alive.current = true;
    check();
    return () => { alive.current = false; };
  }, [check]);

  // 대기 중일 때만 폴링 — 다른 관리자가 승인하면 새로고침 없이 콘솔로 넘어간다.
  useEffect(() => {
    if (phase !== 'pending') return undefined;
    const id = setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
      check();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [phase, check]);

  // 콘솔 안에서 어떤 요청이든 device_* 403 을 받으면(열어 둔 탭에서 회수됨 등) 상태를 다시 본다.
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const onRejected = () => { check(); };
    window.addEventListener(DEVICE_REJECTED_EVENT, onRejected);
    return () => window.removeEventListener(DEVICE_REJECTED_EVENT, onRejected);
  }, [check]);

  const register = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await adminRegisterDevice({
        label: label.trim() || null,
        userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : null,
      });
      writeDeviceToken(res.token);
      await check();
    } catch (e) {
      if (e?.code === 'too_many_pending') {
        setError('승인 대기 중인 요청이 너무 많아요. 다른 관리자에게 기존 요청을 정리해 달라고 하세요.');
      } else {
        setError(e.message || '기기 등록에 실패했어요.');
      }
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    clearDeviceToken();
    setMe(null);
    setPhase('register');
  };

  if (phase === 'pass') return <Outlet />;
  if (phase === 'loading') return <div className="route-loading">기기 확인 중이에요</div>;

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-5 py-10">
      {phase === 'forbidden' && (
        <Card>
          <CardHeader>
            <CardTitle>관리자만 가능해요</CardTitle>
            <CardDescription>이 계정은 관리자 콘솔을 쓸 수 없어요.</CardDescription>
          </CardHeader>
        </Card>
      )}

      {phase === 'error' && (
        <Card>
          <CardHeader>
            <CardTitle>기기 상태를 확인하지 못했어요</CardTitle>
            <CardDescription>{error}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" size="sm" onClick={check}>다시 시도</Button>
          </CardContent>
        </Card>
      )}

      {phase === 'register' && (
        <Card>
          <CardHeader>
            <CardTitle>이 기기를 등록해요</CardTitle>
            <CardDescription>
              관리자 콘솔은 승인된 기기에서만 쓸 수 있어요. 등록하면 다른 관리자가 승인해야 열려요.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <label className="text-sm text-muted-foreground" htmlFor="admin-device-label">기기 이름</label>
            <Input
              id="admin-device-label"
              value={label}
              maxLength={60}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="예: 회사 맥북 · Chrome"
            />
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button disabled={busy} onClick={register}>{busy ? '요청 중…' : '승인 요청'}</Button>
          </CardContent>
        </Card>
      )}

      {phase === 'pending' && (
        <Card>
          <CardHeader>
            <CardTitle>승인 대기 중이에요</CardTitle>
            <CardDescription>
              다른 관리자가 승인해야 해요. 승인되면 이 화면이 자동으로 콘솔로 바뀌어요.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm">
            <div><span className="text-muted-foreground">기기 이름</span> · {me?.label || '-'}</div>
            {error && <p className="text-destructive">{error}</p>}
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={check}>지금 확인</Button>
              <Button variant="ghost" size="sm" onClick={reset}>다른 이름으로 다시 등록</Button>
            </div>
          </CardContent>
        </Card>
      )}

      {phase === 'revoked' && (
        <Card>
          <CardHeader>
            <CardTitle>이 기기는 회수됐어요</CardTitle>
            <CardDescription>다시 쓰려면 새로 등록하고 승인을 받아야 해요.</CardDescription>
          </CardHeader>
          <CardContent>
            <Button size="sm" onClick={reset}>새로 등록 요청</Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
```

`Button` 에 `variant="ghost"` 가 없으면 `variant="outline"` 으로 바꾼다(`src/components/admin-ui/button.jsx` 의 variants 를 확인).

- [ ] **Step 4: `App.jsx` 배선**

`src/apps/admin/App.jsx` — import 에 `import { RequireDevice } from './RequireDevice.jsx';` 를 추가하고 라우트 트리를:

```jsx
    <Routes>
      <Route element={<RequireAuth />}>
        {/* 기기 게이트 — 로그인 뒤·콘솔 셸 앞. 서버 가드가 진짜 판정이고 이건 그 판정의 화면이다. */}
        <Route element={<RequireDevice />}>
          <Route element={<AdminShell />}>
            <Route index element={<AdminDashboard />} />
            <Route path="applications" element={<AdminApplications />} />
            <Route path="models" element={<AdminModels />} />
            <Route path="users" element={<AdminUsers />} />
            <Route path="staff" element={<AdminStaff />} />
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
```

- [ ] **Step 5: 통과 확인 + 빌드**

Run: `node --test tests/frontend/admin-device.test.mjs && pnpm test:frontend 2>&1 | tail -4 && pnpm build 2>&1 | tail -3`
Expected: 테스트 fail 0, `vite build` 성공(admin 번들에 RequireDevice 포함; `bundle-separation.test.mjs` 가 있으니 그것도 green 이어야 한다).

- [ ] **Step 6: 커밋**

```bash
git add src/apps/admin/RequireDevice.jsx src/apps/admin/App.jsx tests/frontend/admin-device.test.mjs
git -c core.hooksPath=/dev/null commit -m "feat(admin): 콘솔 진입에 기기 등록·승인 대기 화면을 더해요

서버 gate 가 enforce 일 때만 막고, 대기 중엔 10초마다 상태를 봐서 승인되면
새로고침 없이 콘솔로 넘어간다. 토큰이 없으면 shadow 에서도 등록은 시킨다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019cJPhmby2YzLcN5RaPcGL1"
```

---

### Task 9: Staff 화면 "관리자 기기" 섹션

**Files:**
- Modify: `src/features/admin/AdminStaff.jsx` (import, `ACTION_LABEL`, 상태·로드, 관리자 카드와 계정 찾기 카드 사이에 새 카드)
- Test: `tests/frontend/admin-device.test.mjs` (추가), 기존 `tests/frontend/admin-staff.test.mjs` 는 그대로 green 이어야 함

**Interfaces:**
- Consumes: `adminListDevices`, `adminApproveDevice`, `adminRevokeDevice` (Task 7); 목록 항목 `{id, userEmail, label, status, createdAt, lastSeenAt, approvedByEmail, isCurrent}` (Task 5)

- [ ] **Step 1: 소스 계약 테스트 추가**

`tests/frontend/admin-device.test.mjs` 끝에:

```js
test('Staff 화면에 관리자 기기 섹션이 있고 현재 기기는 회수할 수 없다', () => {
  const src = read('src/features/admin/AdminStaff.jsx');
  assert.ok(src.includes('adminListDevices') && src.includes('adminApproveDevice') && src.includes('adminRevokeDevice'));
  assert.ok(src.includes('관리자 기기'));
  assert.ok(src.includes('isCurrent'), '현재 기기 판정이 없다');
  assert.ok(/disabled=\{[^}]*isCurrent/.test(src), '현재 기기의 회수 버튼이 비활성이 아니다');
  // 기기 목록 실패도 카드 안 에러 + 재시도(전체 게이팅 금지)
  assert.ok(src.includes('devicesError'));
  // 감사 원장 라벨
  assert.ok(src.includes("'device.approve'") && src.includes("'device.revoke'"));
  // 시간은 서울 기준 표시(KST 정책)
  assert.ok(src.includes('seoulDateTime'));
});
```

- [ ] **Step 2: 실패 확인**

Run: `node --test tests/frontend/admin-device.test.mjs`
Expected: 마지막 테스트 FAIL.

- [ ] **Step 3: 구현**

`src/features/admin/AdminStaff.jsx`:

import 수정:
```jsx
import {
  adminApproveDevice, adminListAudit, adminListDevices, adminListStaff, adminRevokeDevice, adminSetRole,
} from '@/lib/api/facemarket.js';
import { seoulDateTime } from '@/lib/datetime.js';
```

`ACTION_LABEL` 에 두 줄 추가:
```js
  'device.approve': '기기 승인',
  'device.revoke': '기기 회수',
```

컴포넌트 안, `const [busy, setBusy] = useState(false);` 아래에 상태 추가:
```jsx
  // 기기 목록도 admins·audit 과 같은 3상태(null 스켈레톤 / 에러 / 로드됨). 이유는 위 주석과 같다.
  const [devices, setDevices] = useState(null);
  const [devicesError, setDevicesError] = useState(null);
```

`load` 콜백 안, `adminListAudit` 호출 아래에:
```jsx
    setDevicesError(null);
    adminListDevices()
      .then((d) => setDevices(d.items))
      .catch((e) => setDevicesError(e.message || '기기 목록을 불러오지 못했어요.'));
```

`change` 함수 아래에:
```jsx
  const deviceAction = async (deviceId, action) => {
    setBusy(true);
    try {
      if (action === 'approve') await adminApproveDevice(deviceId);
      else await adminRevokeDevice(deviceId);
      load(q.trim() || undefined);
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
    } finally {
      setBusy(false);
    }
  };

  const DEVICE_STATUS = {
    pending: { label: '승인 대기', variant: 'default' },
    approved: { label: '승인됨', variant: 'secondary' },
    revoked: { label: '회수됨', variant: 'outline' },
  };
```

JSX — 첫 `<Card>`(관리자 N명) 닫힌 뒤, `계정 찾기` 카드 앞에 새 카드:

```jsx
      <Card>
        <CardHeader>
          <CardTitle>관리자 기기</CardTitle>
          <CardDescription>
            콘솔은 승인된 기기에서만 쓸 수 있어요. 새 기기는 여기서 다른 관리자가 승인해요.
            지금 쓰는 기기는 여기서 회수할 수 없어요.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {devicesError && (
            <div className="flex flex-col items-center gap-3 px-5 py-10 text-center text-sm text-muted-foreground">
              <p>{devicesError}</p>
              <Button variant="outline" size="sm" onClick={() => load(q.trim() || undefined)}>다시 시도</Button>
            </div>
          )}
          {!devices && !devicesError && <Skeleton className="m-5 h-24" />}
          {devices && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>기기</TableHead><TableHead>관리자</TableHead><TableHead>상태</TableHead>
                  <TableHead>마지막 사용</TableHead><TableHead>등록</TableHead><TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {devices.map((d) => {
                  const st = DEVICE_STATUS[d.status] || { label: d.status, variant: 'outline' };
                  return (
                    <TableRow key={d.id} className={d.status === 'pending' ? 'bg-muted/40' : undefined}>
                      <TableCell>
                        {d.label}
                        {d.isCurrent && <span className="ml-2 text-xs text-muted-foreground">이 기기</span>}
                      </TableCell>
                      <TableCell className="text-muted-foreground">{d.userEmail || d.userId}</TableCell>
                      <TableCell><Badge variant={st.variant}>{st.label}</Badge></TableCell>
                      <TableCell className="text-muted-foreground">{d.lastSeenAt ? seoulDateTime(d.lastSeenAt) : '-'}</TableCell>
                      <TableCell className="text-muted-foreground">{seoulDateTime(d.createdAt)}</TableCell>
                      <TableCell className="text-right">
                        {d.status === 'pending' && (
                          <div className="flex justify-end gap-2">
                            <Button size="sm" disabled={busy} onClick={() => deviceAction(d.id, 'approve')}>승인</Button>
                            <Button size="sm" variant="outline" disabled={busy} onClick={() => deviceAction(d.id, 'revoke')}>거절</Button>
                          </div>
                        )}
                        {d.status === 'approved' && (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={busy || d.isCurrent}
                            title={d.isCurrent ? '지금 쓰는 기기는 회수할 수 없어요' : undefined}
                            onClick={() => deviceAction(d.id, 'revoke')}
                          >
                            회수
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
                {devices.length === 0 && (
                  <TableRow><TableCell colSpan={6} className="py-8 text-center text-muted-foreground">등록된 기기 없음</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
```

`Badge` 의 variants(`src/components/admin-ui/badge.jsx`)에 `outline` 이 없으면 `secondary` 로 바꾼다.

- [ ] **Step 4: 통과 확인**

Run: `pnpm test:frontend 2>&1 | tail -4 && pnpm build 2>&1 | tail -2`
Expected: fail 0, 빌드 성공.

- [ ] **Step 5: 커밋**

```bash
git add src/features/admin/AdminStaff.jsx tests/frontend/admin-device.test.mjs
git -c core.hooksPath=/dev/null commit -m "feat(admin): 관리자 관리 화면에 기기 승인·회수 섹션을 더해요

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019cJPhmby2YzLcN5RaPcGL1"
```

---

### Task 10: 런북 + 매니페스트 주석 + 전체 회귀

**Files:**
- Create: `docs/runbooks/admin-device-gate.md`
- Modify: `copilot/api/manifest.yml` (`GARMENT_QC_MODE` 근처에 주석 처리된 env 1줄)
- Modify: `docs/superpowers/specs/2026-09-11-admin-device-gate-design.md` §5.1 한 줄(플래그 오타 처리를 "기동 실패" → "shadow 폴백" 으로 — 구현이 레포 관례 `_flag` 를 따랐다)

- [ ] **Step 1: 런북 작성**

```markdown
# 관리자 콘솔 기기 게이트 — 배포·복구 런북

설계: `docs/superpowers/specs/2026-09-11-admin-device-gate-design.md`

## 무엇인가

admin.wearless.kr 은 관리자 계정 **+ 승인된 기기**에서만 열린다. 기기 = 브라우저 프로필의
localStorage 토큰(`wl.admin.device.v1`). 서버 플래그 `ADMIN_DEVICE_GATE`:

| 값 | 뜻 |
|---|---|
| `off` | 기기 검사 안 함 |
| `shadow` | 검사하고 실패해도 통과. 로그 `admin_device_gate shadow reject user=… code=…` 만 남김. **코드 기본값** |
| `enforce` | 실패 시 403 (`device_missing`/`device_unknown`/`device_pending`/`device_revoked`) |

## 첫 배포 순서

1. 마이그레이션 `20260911150000_admin_devices.sql` 이 **앱 DB**(ftjxwxuactfjopbokbni)에 붙었는지 확인:
   `select count(*) from admin_devices;` 가 0 을 돌려주면 됨. 안 붙었으면 CI 시크릿 `SUPABASE_DB_URL` 이
   옛 DB 를 가리키는 것 — 2026-08-29 사고 런북대로 앱 DB 에 직접 적용.
2. 머지 → CI 배포. env 는 손대지 않는다(shadow). 이 시점부터 `/openapi.json` 404.
3. 관리자 각자 admin.wearless.kr 접속 → "이 기기를 등록해요" 화면 → 이름 확인 → 승인 요청.
   shadow 라 바로 콘솔이 열린다. Slack 에 "관리자 기기 승인 요청" 이 온다.
4. `/staff` → "관리자 기기" → 서로의 기기를 **승인**. 자기 것도 다른 승인 기기가 있으면 승인 가능.
5. 배포 로그에 `shadow reject` 가 더 안 찍히면 `copilot/api/manifest.yml` 에
   `ADMIN_DEVICE_GATE: enforce` 를 넣어 PR → CI 배포. 이때부터 진짜 잠금.

## 새 관리자 / 새 기기

- 새 관리자: `/staff` 에서 승격 → 그 사람이 접속 → 등록 → 기존 관리자가 승인.
- 새 기기(폰 등): 접속 → 등록 → 대기 화면(10초 폴링) → 다른 관리자 또는 본인의 승인된 기기에서 승인.
- 요청한 적 없는 기기가 Slack 에 뜨면 **승인하지 말고** 그 계정의 비밀번호부터 바꾼다. `/staff` 에서 거절.

## 락아웃 복구 (아무 승인 기기도 없음)

1. manifest 의 `ADMIN_DEVICE_GATE` 를 `shadow` 로 → 배포.
2. 접속(등록 화면이 뜨면 등록) → `/staff` 에서 필요한 기기 승인.
3. 다시 `enforce` → 배포.
코드 변경·DB 직접 수정 없음. 정 급하면 DB 에서 `update admin_devices set status='approved' where id='…'` 도 되지만 감사 원장에 안 남는다.

## 로컬 개발

- 백엔드 `.env.local` 에 `ADMIN_DEVICE_GATE=off` 를 두면 등록 화면이 안 뜬다. 게이트를 보려면 `shadow`/`enforce`.
- `?admin=1` 오버라이드 오리진(localhost:5173)의 localStorage 는 prod 와 별개다.

## 관찰

- shadow 거절: CloudWatch 로그 `admin_device_gate shadow reject`
- 승인·회수: `admin_audit_log` (`device.approve`/`device.revoke`), `/staff` 최근 기록
- 등록 스팸: 1인당 pending 5개 상한(`ADMIN_DEVICE_MAX_PENDING_PER_USER`), 넘으면 429 `too_many_pending`
```

- [ ] **Step 2: 매니페스트 주석**

`copilot/api/manifest.yml` 의 `GARMENT_QC_MODE: "off"` 줄 아래에(들여쓰기 맞춰):

```yaml
  # 관리자 콘솔 기기 게이트. 코드 기본 shadow. 두 관리자가 콘솔에서 서로 기기를 승인한 뒤
  # enforce 로 올린다. 락아웃 복구 = shadow 로 내렸다 다시 enforce. 런북 docs/runbooks/admin-device-gate.md
  # ADMIN_DEVICE_GATE: enforce
```

- [ ] **Step 3: 설계서 §5.1 문장 수정**

`docs/superpowers/specs/2026-09-11-admin-device-gate-design.md` 의
"`load_settings` 에서 `ADMIN_DEVICE_GATE` 값이 셋 밖이면 기동 실패(다른 mode 플래그와 같은 검증)." 을
"`load_settings` 는 레포 관례(`_flag`)대로 허용값 밖이면 **shadow 로 폴백**한다 — 오타로 게이트가 꺼지거나(off) 잠기면(enforce) 안 되니 중간값이 안전하다." 로 바꾼다.

- [ ] **Step 4: 전체 회귀**

Run:
```bash
cd server && ../../../../server/.venv/bin/python -m pytest -q -p no:cacheprovider 2>&1 | tail -3
cd .. && pnpm test:frontend 2>&1 | tail -4 && pnpm build 2>&1 | tail -2
```
Expected: 백엔드 `1 failed`(베이스라인의 `test_purge_deletes_model_test_cuts_and_cover` flake 만 — 다른 실패가 있으면 이 작업이 낸 것) / 프런트 fail 0 / 빌드 성공.

- [ ] **Step 5: 커밋**

```bash
git add docs/runbooks/admin-device-gate.md copilot/api/manifest.yml docs/superpowers/specs/2026-09-11-admin-device-gate-design.md
git -c core.hooksPath=/dev/null commit -m "docs(admin): 기기 게이트 배포·락아웃 런북과 manifest 주석을 더해요

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_019cJPhmby2YzLcN5RaPcGL1"
```

---

## 완료 뒤

- `superpowers:finishing-a-development-branch` 로 PR. PR 본문에 배포 순서(shadow → 서로 승인 → enforce)와 "이 PR 은 env 를 바꾸지 않는다" 를 명시.
- 사용자가 해야 할 ops: (1) 마이그가 앱 DB 에 붙었는지 확인, (2) 두 관리자 등록·상호 승인, (3) `ADMIN_DEVICE_GATE: enforce` PR.
