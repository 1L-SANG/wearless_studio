# 관리자 콘솔 기기 게이트 — 설계

**작성일:** 2026-09-11
**상태:** 구현 완료 (feat/admin-device-gate, 2026-09-11)
**브랜치:** `feat/admin-device-gate` (워크트리 `.claude/worktrees/admin-device-gate`, `origin/main` 361832fe 기준)
**범위:** `admin.wearless.kr` 콘솔과 그 뒤의 관리자 API 를 **관리자 계정 + 승인된 기기**에서만 쓸 수 있게 한다. 덤으로 prod 에 열려 있던 `/openapi.json` 을 닫는다. 인증 스택(Supabase)·일반 사용자 라우트·생성 파이프라인은 건드리지 않는다.

---

## 1. 목표

지금 콘솔은 "관리자 계정으로 로그인" 이 유일한 관문이다. 비밀번호·세션이 새면 아무 기기에서나 관리자 API 를 부를 수 있다. 관리자는 2명이고 늘어날 수 있다.

**성공 기준**

1. 관리자 라우트(`admin_guard.require_admin` 이 걸린 26곳 전부)가 **승인된 기기의 토큰 없이는 403** 을 준다. 예외는 기기 등록·상태 조회 두 라우트뿐.
2. 새 기기는 콘솔에서 **다른 승인 기기가 승인**해야 열린다. 승인·회수는 감사 원장에 남고 Slack 으로 알림이 간다.
3. 배포 직후 아무도 잠기지 않는다 — shadow 로 시작해 두 관리자가 서로 승인한 뒤 enforce 로 올린다. 락아웃 복구 경로가 코드 변경 없이 존재한다.
4. `https://api.wearless.kr/openapi.json` 이 404 가 된다.
5. 일반 사용자(ai·facemarket) 경험은 0 변화.

---

## 2. 확정된 결정 (사용자, 2026-09-11)

| # | 결정 | 버린 대안 |
|---|---|---|
| 1 | 앱 자체 기기 등록·승인(옵션 2) | Cloudflare Access + WARP(인프라만), IP allowlist |
| 2 | 기기 식별 = **랜덤 토큰 (브라우저 프로필 단위)** | WebAuthn 패스키(하드웨어 묶기, ~3배 비용, iCloud 동기화 함정) |
| 3 | api.wearless.kr 호스트는 그대로 공개 — 잠그는 건 **관리자 라우트** | 호스트 봉쇄(일반 사용자 API 라 불가), 관리자 API 별도 호스트 분리 |
| 4 | Cloudflare 프록시(WAF·레이트리밋) 는 **별도 건** | 이번에 같이 |

---

## 3. 현재 상태 (전수조사 2026-09-11)

- 호스팅: Vercel 단일 프로젝트, `vercel.json` host rewrite → `admin.html`. DNS 는 Cloudflare grey-cloud.
- 인증: Supabase JWT → `auth.require_user` → 관리자 라우트는 `admin_guard.require_admin(conn, user_id)` (`profiles.role = 'admin'`). 호출부 26곳 / 6파일(`facemarket_admin.py` 9, `facemarket_admin_models.py` 7, `facemarket_applications.py` 6, `routes.py` 2, `facemarket.py` 1, `facemarket_cutover.py` 1 — 마지막은 라우트 아닌 내부 함수 `is_admin_user`).
- 프런트: `lib/api/facemarket.js` 의 `admin*` 함수 → 공용 `http()`(`httpAdapter.js`) 가 Bearer 주입. 에러는 `err.status`·`err.code` 를 실어 throw.
- 콘솔: `AdminShell` 사이드바 5화면. `/staff` 에 승격/회수 + 감사 원장 이미 있음. `write_audit` 원장 있음.
- Slack: `facemarket_notify.notify_slack_new_application` 패턴(`settings.fm_slack_webhook_url`).
- 전체 145 라우트 중 무인증 8개는 전부 의도된 것(healthz/readyz, config 2, public/models, verify 2, assets capability URL, toss 웹훅). **`/openapi.json` 만 의도 밖으로 열려 있음**(`main.py:300` 이 docs/redoc 만 껐다).
- 테스트: 백엔드 pytest(admin 계약 검사 `test_admin_guard_adoption.py` 가 소스 정규식으로 가드 배선을 강제), 프런트 `node --test tests/frontend/*.test.mjs`(소스 계약 + 순수 모듈 단위).

---

## 4. 데이터

### 4.1 `admin_devices` (마이그레이션 1장, `supabase/migrations/20260911150000_admin_devices.sql`)

```sql
create table if not exists public.admin_devices (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  token_hash    text not null unique,          -- sha256(hex) — 원문 토큰은 어디에도 저장하지 않는다
  label         text not null,                 -- "MacBook · Chrome" (요청자가 고칠 수 있음, 1~60자)
  status        text not null default 'pending'
                check (status in ('pending','approved','revoked')),
  user_agent    text,
  created_at    timestamptz not null default now(),
  last_seen_at  timestamptz,
  approved_by   uuid references auth.users(id) on delete set null,
  approved_at   timestamptz,
  revoked_by    uuid references auth.users(id) on delete set null,
  revoked_at    timestamptz
);
create index if not exists admin_devices_user_status_idx on public.admin_devices (user_id, status);
create index if not exists admin_devices_status_created_idx on public.admin_devices (status, created_at desc);
alter table public.admin_devices enable row level security;   -- 정책 없음 = anon/PostgREST 차단. 서버는 직결 DB 롤.
```

- 상태 전이: `pending → approved`, `pending → revoked`, `approved → revoked`. **revoked 는 종점**(부활 없음 — 다시 쓰려면 새 등록).
- `on delete cascade`: 계정 삭제(30일 PII 스윕 등) 때 기기도 같이 간다. 감사 원장에는 남는다.

### 4.2 감사 원장 액션 (기존 `admin_audit_log`)

| action | target_type | target_id | before → after | note |
|---|---|---|---|---|
| `device.approve` | `admin_device` | device id | `{status: pending}` → `{status: approved}` | 기기 label·주인 user_id |
| `device.revoke` | `admin_device` | device id | `{status: <이전>}` → `{status: revoked}` | 기기 label·주인 user_id |
| `staff.role`(기존) | — | — | 기존 그대로 | 강등 시 `revokedDevices: N` 을 after 에 추가 |

등록(`register`) 은 원장에 안 남긴다 — 관리자 행위가 아니라 요청이고, 스팸이 원장을 더럽힌다. 대신 Slack 으로 간다.

---

## 5. 서버

### 5.1 설정 (`config.py`)

```python
# 관리자 콘솔 기기 게이트. off=검사 안 함 / shadow=검사하고 실패해도 통과(로그만) /
# enforce=실패 시 403. 기본 shadow — 배포 직후 아무도 잠기지 않게. 배포 순서 §9.
admin_device_gate: str = "shadow"   # off | shadow | enforce
admin_device_max_pending_per_user: int = 5
```

`load_settings` 는 레포 관례(`_flag`)대로 허용값 밖이면 **shadow 로 폴백**한다 — 오타로 게이트가 꺼지거나(off) 잠기면(enforce) 안 되니 중간값이 안전하다.

### 5.2 가드 (`admin_guard.py`)

시그니처가 바뀐다: `require_admin(conn, user_id, request)`. 호출부 26곳(라우트 25 + `_require_admin` 래퍼 1)은 `request` 를 그대로 넘긴다 — 전부 이미 `request: Request` 를 받고 있다.

```
require_admin(conn, user_id, request):
  1. is_admin(user_id) 아니면 403 forbidden  (기존 그대로, 문구 동일)
  2. verdict = check_device(conn, user_id, request)
  3. gate == off      → 통과
     gate == shadow   → verdict 가 ok 아니면 logger.warning("admin_device_gate shadow reject user=%s code=%s") 후 통과
     gate == enforce  → verdict 가 ok 아니면 403 {code: verdict.code, message}
```

`check_device`:

| 상황 | code | message |
|---|---|---|
| 헤더 `X-Admin-Device` 없음/빈값 | `device_missing` | "등록된 기기에서만 쓸 수 있어요." |
| 해시 불일치, 또는 행은 있는데 `user_id` 가 다름 | `device_unknown` | 위와 같은 문구 (타인 토큰인지 드러내지 않는다) |
| `status = pending` | `device_pending` | "이 기기는 아직 승인 대기 중이에요." |
| `status = revoked` | `device_revoked` | "이 기기는 회수됐어요. 다시 등록해 주세요." |
| `approved` | ok | — |

- 조회는 `token_hash` 로 한 번(unique). 토큰 비교는 해시 동등 비교라 타이밍 문제 없음.
- SQL 은 `repo.find_admin_device_by_hash(conn, token_hash)` · `repo.touch_admin_device(conn, device_id)` 두 함수로 뺀다 — 기존 테스트가 `repo.is_admin` 을 monkeypatch 하는 것과 같은 결로, 라우트 테스트가 `repo` 만 바꿔 enforce 경로를 돌릴 수 있게. `gate == off` 면 조회 자체를 하지 않는다.
- ok 일 때 `last_seen_at` 이 null 이거나 60초보다 오래됐으면 `repo.touch_admin_device` 를 실행하고 **가드가 바로 `conn.commit()` 한다**. psycopg_pool 은 반환되는 INTRANS 커넥션을 롤백하므로 커밋 없이는 읽기 라우트의 touch 가 항상 유실돼 '마지막 사용' 이 쓰기 요청 때만 움직인다. 가드는 모든 라우트에서 첫 문장이라 이 커밋이 다른 것을 확정하지 않는다. shadow/off 에서 기기 조회가 예외를 내면(테이블 부재 등) 로그 후 롤백하고 통과한다 — enforce 는 그대로 실패(닫힘).
- `is_admin_user`(cutover 내부 함수, 라우트 아님) 는 그대로 신원만 본다. 주석에 "기기 게이트 안 탐 — 라우트가 아니라서 request 가 없다" 명시.
- 가드가 `request` 를 받게 되므로 `check_device` 는 순수 함수로 분리해 단위 테스트한다(`(conn, user_id, token: str | None, now)`).

### 5.3 라우트 (`facemarket_admin_devices.py`, prefix `/v1/facemarket/admin/devices`)

| 메서드·경로 | 요구 | 동작 |
|---|---|---|
| `POST /register` | JWT + `is_admin` (**기기 불요** — 유일한 예외적 쓰기) | body `{label?: string, userAgent?: string}`. `label` 없으면 서버가 UA 로 만든다(`_label_from_ua`: OS · 브라우저, 실패 시 "알 수 없는 기기"). 이 user 의 pending 수 ≥ `max_pending_per_user` 면 429 `too_many_pending`. 32바이트 `secrets.token_urlsafe` 생성 → 해시 저장 → `{deviceId, token, status: "pending", gate}` 반환(토큰은 이 응답 한 번뿐). Slack 알림(§5.5). |
| `GET /me` | JWT + `is_admin` (**기기 불요**) | 헤더의 토큰으로 `{status: "approved"\|"pending"\|"revoked"\|"unknown", deviceId?, label?, gate}` 반환. 403 을 내지 않는다 — 대기 화면이 폴링한다. |
| `GET /` | `require_admin`(기기 포함) | 전 관리자의 기기 목록. pending 먼저, 그 다음 최근 사용순. 항목: `id, userId, userEmail, label, status, createdAt, lastSeenAt, approvedByEmail, approvedAt, revokedAt, isCurrent`(요청 기기면 true). |
| `POST /{id}/approve` | `require_admin` | `pending` → `approved`(approved_by/at). 아니면 409 `device_not_pending`. `write_audit(device.approve)`. |
| `POST /{id}/revoke` | `require_admin` | `pending`/`approved` → `revoked`. 이미 revoked 면 409. **요청 기기 자신이면 400 `cannot_revoke_current`**("지금 쓰는 기기는 회수할 수 없어요 — 다른 기기에서 회수해 주세요."). `write_audit(device.revoke)`. |

- `/register`·`/me` 는 `require_admin` 을 부르지 않고 `admin_guard.require_admin_identity(conn, user_id)`(= 1단계만) 를 부른다. 이름을 따로 둬서 adoption 테스트가 "기기 면제 라우트는 이 둘뿐" 을 소스에서 셀 수 있게 한다.
- 비관리자가 `/register` 를 치면 403 forbidden — 기기 행이 생기지 않는다(테이블 스팸 방지).
- 자기 기기 승인: 승인 기기 A 에서 자기 새 기기 B 를 승인하는 건 **허용**(A 가 신뢰 앵커). 막을 이유가 없고, 막으면 관리자 1명 시절에 아무것도 못 한다.

### 5.4 강등 시 일괄 회수 (`facemarket_admin.set_role`)

`role == "user"` 로 바꿀 때 같은 트랜잭션에서
`update admin_devices set status='revoked', revoked_by=actor, revoked_at=now() where user_id=target and status in ('pending','approved')`
를 실행하고 회수 건수를 기존 `staff.role` 감사 행의 `after.revokedDevices` 에 넣는다. 안 하면 재승격 때 옛 승인 기기가 그대로 살아난다.

### 5.5 Slack (`facemarket_notify.notify_slack_admin_device_requested`)

`:closed_lock_with_key: 관리자 기기 승인 요청 · {email} · {label}` + `<https://admin.wearless.kr/staff|관리자 관리 열기>`. 기존 함수와 같은 실패 무해 규칙(웹훅 없으면 no-op, 4xx 는 error 로그). 라우트 응답을 막지 않게 `asyncio.create_task` 가 아니라 **기존 패턴대로 await** 한다(타임아웃 `_TIMEOUT` 이 짧다).

### 5.6 `main.py`

- CORS `allow_headers` 에 `"X-Admin-Device"` 추가(없으면 preflight 에서 죽는다 — 프런트 테스트 `cors-origins-cover-hosts` 옆에 헤더 검사도 하나 둔다).
- `openapi_url = "/openapi.json" if settings.app_env == "dev" else None`. `docs_url`·`redoc_url` 과 같은 줄에 나란히.

---

## 6. 프런트

### 6.1 `src/lib/adminDevice.js` (순수 모듈)

```js
const KEY = 'wl.admin.device.v1';
export function readDeviceToken(storage = window.localStorage) → string | null
export function writeDeviceToken(token, storage) / clearDeviceToken(storage)
export function defaultDeviceLabel(ua = navigator.userAgent) → "macOS · Chrome" 류(서버 폴백과 같은 규칙, 입력칸 초기값용)
```

`storage` 주입으로 node 테스트 가능. localStorage 는 오리진 단위라 이 토큰은 `admin.wearless.kr` 문서에서만 보인다. `?admin=1` 로컬 오버라이드 오리진에서도 그 오리진의 스토리지에만 산다.

### 6.2 헤더 주입 (`httpAdapter.js`)

`http()` 가 `readDeviceToken()` 이 truthy 면 `X-Admin-Device` 를 헤더에 넣는다. 조건은 **토큰 존재** 하나 — `IS_ADMIN` 을 보지 않는다(스토리지가 이미 오리진으로 갈라져 있고, 오버라이드 오리진에서도 동작해야 한다). `try/catch` 로 감싼다(스토리지 접근이 던지는 환경).

### 6.3 API 함수 (`lib/api/facemarket.js`)

`adminRegisterDevice({label, userAgent})`, `adminDeviceMe()`, `adminListDevices()`, `adminApproveDevice(id)`, `adminRevokeDevice(id)`.

### 6.4 `RequireDevice` 가드 (`src/apps/admin/RequireDevice.jsx`)

`App.jsx` 라우트 트리에서 `RequireAuth` 안쪽, `AdminShell` 바깥에 한 겹.

```
mount →
  token 없음 ─→ [등록 화면] 기기명 입력(초기값 defaultDeviceLabel) + "승인 요청" 버튼
                  → adminRegisterDevice → writeDeviceToken → status 로
  token 있음 ─→ adminDeviceMe()
     gate !== 'enforce'          → 통과(<Outlet/>)  ※ shadow/off 는 서버가 안 막으니 화면도 안 막는다
     status approved             → 통과
     status pending              → [대기 화면] "다른 관리자가 승인해야 해요" + 기기명 + 10초 폴링(adminDeviceMe)
                                    승인되면 폴링이 approved 를 보고 그대로 통과 — 새로고침 없음
     status revoked              → [회수 화면] "이 기기는 회수됐어요" + "새로 등록 요청" 버튼(clearDeviceToken → 등록 화면)
     status unknown              → 스토리지에 쓰레기 — clearDeviceToken → 등록 화면
  adminDeviceMe 가 403 forbidden → 기존 "관리자만 가능해요" 화면(비관리자). 등록 화면으로 보내지 않는다.
  네트워크 에러                  → 에러 상태 + 재시도 버튼(전체 게이팅 금지 — 기존 staff 화면 회고와 같은 규칙)
```

- 폴링은 화면이 보일 때만(`document.visibilityState`), 언마운트에 정리.
- 등록 화면에서 `too_many_pending`(429) 이면 "대기 중인 요청이 너무 많아요 — 관리자에게 기존 요청을 정리해 달라고 하세요".
- 콘솔 안에서 admin API 가 enforce 403 `device_*` 를 돌려주는 경우(예: 열어 둔 탭에서 회수됨) — 각 화면이 이미 `e.status === 403` 을 처리한다. 추가로 `RequireDevice` 가 `window` 이벤트 `admin-device-rejected` 를 듣고 상태를 다시 조회하게 `http()` 에서 `device_*` 코드를 만나면 그 이벤트를 쏜다. 화면마다 손대지 않아도 된다.

### 6.5 Staff 화면 (`AdminStaff.jsx`) — "관리자 기기" 섹션

기존 관리자 목록 카드 아래, 감사 원장 위.

- 표: 기기명 · 관리자(이메일) · 상태 배지 · 마지막 사용 · 등록일 · 동작.
- pending 행은 맨 위 + 강조, 동작 = **승인 / 거절(=회수)**. approved 행 동작 = **회수**. revoked 는 회색, 동작 없음.
- `isCurrent` 행은 "이 기기" 표시 + 회수 버튼 비활성(서버도 막지만 UI 가 먼저 안내 — 기존 `isSelf` 규칙과 같은 결).
- 승인/회수 뒤 목록·감사 원장 재조회. 실패는 그 카드 안 에러 상태(전체 게이팅 금지).

---

## 7. 테스트

### 백엔드

- `test_admin_guard.py`(실제 파일명, 확장) — `check_device` 순수 함수: 헤더 5상태 × 타인 토큰 × last_seen 갱신 조건(60초). `require_admin` 을 gate 3모드로: off 는 조회조차 안 함, shadow 는 warning 로그 후 통과, enforce 는 403 코드 매핑. shadow/enforce 의 조회 실패(테이블 부재) 처리, touch 시 커밋 여부.
- `test_admin_devices.py`(실제 파일명) — TestClient + FakeConn(기존 admin 테스트 방식): register(라벨 폴백·pending 상한 429·Slack 호출·비관리자 403·행 미생성), me(4상태·gate 동봉, off 는 조회 생략), list(정렬·isCurrent), approve(원장·409), revoke(원장·현재기기 400·409), 강등 시 일괄 revoke + 감사 after.
- `test_admin_guard_adoption.py` 확장 — (a) `require_admin(` 호출은 전부 `(conn, user_id, request)` 형태, (b) `require_admin_identity(` 는 `facemarket_admin_devices.py` 의 register·me 둘뿐, (c) `repo.is_admin` 직접 호출 금지 목록에 새 파일 추가.
- `test_main_openapi.py` — `app_env=prod` 에서 `/openapi.json` 404·`/docs` 404, `dev` 에서 둘 다 200. CORS preflight 에 `X-Admin-Device` 허용.
- `test_admin_device_config.py` — 게이트 플래그 기본값(코드 shadow·테스트 off)과 허용값 밖 입력의 shadow 폴백.
- 기존 admin 라우트 테스트: `conftest.make_settings` 기본에 `admin_device_gate="off"` 를 둔다(`garment_qc_mode="off"` 와 같은 선례·같은 이유 — 관련 없는 테스트가 FakeConn 위에서 기기 조회를 돌리지 않게). 기기 테스트만 `shadow`/`enforce` 를 명시적으로 켠다. 기존 테스트 파일은 **수정 0** 이 제약.

### 프런트 (`tests/frontend/`)

- `admin-device.test.mjs` — `adminDevice.js` 순수 단위(읽기/쓰기/지우기, 라벨 규칙 3~4 UA), `httpAdapter.js` 소스 계약(`X-Admin-Device` 주입·try/catch), `facemarket.js` 함수 5개 존재, `App.jsx` 에 `RequireDevice` 가 `RequireAuth` 안·`AdminShell` 밖, `RequireDevice.jsx` 4상태 문자열·gate 분기·폴링 정리, `AdminStaff.jsx` 에 기기 섹션·`isCurrent` 비활성.
- `cors-origins-cover-hosts.test.mjs` 옆 — 매니페스트/`main.py` 의 allow_headers 에 `X-Admin-Device`.

---

## 8. 안 하는 것 (YAGNI)

- 기기 라벨 사후 수정, 기기 만료(미사용 N일), 기기별 권한 차등.
- WebAuthn/패스키(결정 #2). 스키마에 `kind` 컬럼도 **안 둔다** — 필요해지면 그때 마이그 한 장.
- 복구 스크립트 — 플래그 shadow 내림으로 대체(§9).
- `is_admin_user`(cutover) 기기 검사.
- Cloudflare 프록시/WAF(결정 #4).
- 이메일 알림(Slack 으로 충분).

---

## 9. 배포 순서

1. **마이그레이션이 앱 DB 에 붙는지 먼저 확인** — CI 의 `SUPABASE_DB_URL` 이 옛 DB 를 가리켰던 사고(2026-08-29) 이후 시크릿을 갱신했는지 배포 전에 `select 1 from admin_devices` 로 확인. 안 붙었으면 그 사고의 런북대로 앱 DB 에 직접 적용.
2. 머지 → CI 배포. env 는 손대지 않는다(기본 shadow). 이 시점부터 `/openapi.json` 404.
3. 관리자 2명이 각자 콘솔을 연다 → `RequireDevice` 가 등록 화면을 띄운다(gate 가 shadow 라도 토큰이 없으면 등록은 시킨다 — 이게 부트스트랩) → 등록 → shadow 라 바로 통과 → `/staff` 에서 **서로의 기기를 승인**.
4. `copilot/api/manifest.yml` 에 `ADMIN_DEVICE_GATE: enforce` 추가 → PR → CI 배포. 이때부터 진짜 잠금.
5. **락아웃 복구**: 4 를 `shadow` 로 되돌려 배포 → 들어가서 승인 → 다시 `enforce`. 코드 변경 없음. `docs/runbooks/` 에 한 단락.

shadow 기간에 `admin_device_gate shadow reject` 로그가 계속 찍히면 enforce 를 올리기 전에 누가 잠길지 미리 안다.

---

## 10. 구현 전 확인한 것

- [x] FakeConn 기반 admin 테스트 — 전부 `repo.is_admin` 을 monkeypatch 한다. 기기 조회도 `repo.*` 로 빼고 테스트 기본 gate 는 `off`(§7) 로 두면 기존 파일 수정 0.
- [x] `RequireAuth`(guards.jsx) 는 세션 있으면 `<Outlet/>`, 없으면 admin/facemarket 은 `FacemarketLoginPrompt`. `RequireDevice` 는 그 Outlet 안에 선다.
- [x] 콘솔 링크는 `settings.fm_application_public_base` 의 `facemarket.` → `admin.` 치환(기존 `notify_slack_new_application`) 재사용.
- [x] 베이스라인: 백엔드 3601 passed / 1 failed(`test_personalization.py::test_purge_deletes_model_test_cuts_and_cover` — 로컬 dev 서버 디스패처가 test DB 잡을 가로채는 알려진 flake, 이 작업과 무관) / 55 skipped. 프런트 1386 passed.
