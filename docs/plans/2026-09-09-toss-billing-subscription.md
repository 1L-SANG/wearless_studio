# 토스페이먼츠 정기결제(빌링) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 토스 빌링키로 월 구독을 자동 결제하고, 크레딧은 구독이 살아있는 동안 이월하며, 해지·결제실패를 정책대로 처리한다.

**Architecture:** 빌링키 발급은 SDK 결제창(`requestBillingAuth`) → `authKey` → 서버가 `POST /v1/billing/authorizations/issue` 로 교환해 pgcrypto 로 암호화 저장. 이후 결제는 전부 서버 전용(`POST /v1/billing/{billingKey}`)이라 리다이렉트가 없고, 기존 `payments.py`(1회 충전)와 상태기계가 달라 **별도 모듈**로 만든다. 주기 실행은 기존 워커 패턴(클래스 + asyncio 태스크 + `pg_try_advisory_lock` 단일 러너)을 따른다.

**Tech Stack:** FastAPI · psycopg(async) · httpx · Supabase Postgres(pgcrypto) · React + `@tosspayments/tosspayments-sdk@^2.7.1`

**Spec:** 이 문서 §0 (정책 정본). 2026-09-09 사용자 확정.

---

## §0 정책 정본 (Spec)

### 0.1 결정 사항

| 항목 | 정책 |
|---|---|
| 갱신 시 잔여 크레딧 | **이월**. 구독이 살아있는 동안 남은 구독 크레딧은 소멸하지 않고 새 지급분이 더해진다 |
| 소진 순서 | 구독 버킷 먼저 → topup FIFO. 구독 버킷끼리는 오래된 것부터(= 이월분 우선 소진) |
| 해지 | **예약 해지**. `current_period_end` 까지 그대로 사용, 그 시점에 구독 버킷 **전량 소멸**(이월분 포함) + `profiles.plan = 'free'` |
| 해지 철회 | `current_period_end` 이전이면 가능. 상태만 `active` 로 되돌린다 |
| 결제 실패 | `past_due` 로 전이, 유예 3일. **유예 중에도 크레딧 사용 가능**(이미 결제가 끝난 지난 주기분이므로). D+1·D+2·D+3 하루 1회 재시도 |
| 유예 만료 | 3회 재시도 후에도 실패면 해지와 동일 처리(버킷 전량 소멸 + `free`) |
| 업그레이드 | **즉시**. 남은 일수 비례 차액을 즉시 결제하고 크레딧 차이를 비례 지급. 주기(`current_period_end`)는 유지 |
| 다운그레이드 | **다음 주기부터**. `scheduled_plan_code` 에 적어 두고 갱신 때 적용 |
| 1인 1구독 | `subscriptions.user_id` UNIQUE |

### 0.2 비례배분 공식 (업그레이드)

```
남은일수  = ceil((current_period_end - now) / 1 day)        -- 1 이상
주기일수  = ceil((current_period_end - current_period_start) / 1 day)
차액금액  = floor((new.price   - old.price)   * 남은일수 / 주기일수)   -- 원, 버림
지급크레딧 = floor((new.credits - old.credits) * 남은일수 / 주기일수)   -- 버림
```
- `차액금액 <= 0` 이면 업그레이드가 아니다 → 400 거절(다운그레이드 경로로 유도)
- 지급 크레딧은 **새 버킷 1개**로 추가한다(이월 정책이라 기존 버킷을 건드리지 않는다)
- `current_period_end` 는 바뀌지 않는다. 다음 갱신부터 새 요금제 전액을 청구한다

### 0.3 토스 계약 사실 (문서 확인분)

- 빌링키 발급(결제창 방식): 클라이언트 `payment.requestBillingAuth({method:'CARD', successUrl, failUrl})` → `successUrl?customerKey=..&authKey=..` → 서버 `POST /v1/billing/authorizations/issue` (body: `{authKey, customerKey}`)
- 자동결제 승인: `POST /v1/billing/{billingKey}`, body `{customerKey, amount, orderId, orderName, ...}`. **최대 60초 소요 → 타임아웃 최소 60초**
- 빌링키 삭제: `DELETE /v1/billing/{billingKey}`
- 빌링키 **조회 API 없음**. 발급 응답에서 저장 못 하면 영구 분실 → 재발급만이 복구 수단
- `Idempotency-Key` 헤더는 모든 POST 에서 동작. 최대 300자, **첫 요청일로부터 15일 유효**
- 자동결제 웹훅 `BILLING_DELETED` 는 **서명 헤더가 없다**(일반 결제 웹훅 계열). 본문을 신뢰하면 안 된다
- 자동결제는 별도 계약 MID. **일반결제와 시크릿 키가 다를 수 있다**

### 0.4 이번 계획에서 기본값으로 박은 기술 결정

1. **빌링키 저장 = pgcrypto 대칭 암호화.** `billing_key_enc bytea`, `pgp_sym_encrypt(key, %(kek)s)`. KEK 는 앱 env `TOSS_BILLING_KEK`(SSM). 빌링키 + customerKey 둘만 있으면 무단 결제가 가능하고 customerKey 는 유저 uuid 라 사실상 공개값이므로, **DB 덤프만으로는 결제가 불가능해야 한다**
2. **스케줄러 = 기존 API 태스크 안의 워커.** 별도 ECS 태스크는 인프라 작업이 더 필요하고, 이 워커는 I/O 가 전부 `httpx.AsyncClient` 비동기라 이벤트루프를 막지 않는다(2026-08-26 루프 동결은 동기 이미지 연산이 원인). 다중 태스크 중복 실행은 `pg_try_advisory_lock` 으로 막는다
3. **웹훅 = 랜덤 시크릿 경로.** `POST /v1/webhooks/toss/{secret}` — 서명이 없으므로 경로 지식을 인증 대용으로 쓴다. 본문은 힌트로만 취급해 구독을 `billing_key_invalid` 로 표시할 뿐, 상태를 확정하지 않는다

---

## Global Constraints

- **플래그 뒤에 숨긴다.** 전 기능은 `SUBSCRIPTION_BILLING_ENABLED`(기본 `false`)로 게이트한다. 라우트 등록·워커 기동 모두 이 플래그를 본다
- **키가 없으면 503.** 목 성공 금지. `payments.py` §⑤ 불변식과 동일하게, 빌링 시크릿이 없으면 `503 payment_not_configured`
- **시크릿은 로그·응답에 절대 싣지 않는다.** 빌링키·KEK·시크릿 키 모두. 로그에는 `subscription_id`·`order_id` 만
- **금액의 정본은 서버.** 클라이언트가 보낸 금액은 어떤 경로에서도 결제 근거가 되지 않는다. 구독 금액은 항상 `pricing_plans` 에서 읽는다
- **타임아웃**: 빌링 승인 전용 `TOSS_BILLING_TIMEOUT`(기본 `60.0`). 기존 `toss_confirm_timeout`(15초) 재사용 금지
- **멱등키**: `Idempotency-Key = subscription_invoices.order_id`. 15일 유효라 3일 유예 재시도 전 구간에서 안전
- **원장은 append-only**: `credit_ledger` 는 update/delete 트리거로 막혀 있다. 새 `action_key` 는 자유(CHECK 없음)
- **시간대**: 저장은 UTC(`timestamptz`), 표시는 KST(`src/lib/datetime.js`). SQL 은 `now()` 를 쓰고 파이썬에서 날짜를 만들지 않는다
- **테스트**: DB·네트워크 없이 돈다. `server/tests/test_payments_toss.py` 의 SQL 문자열 분기 스텁 커서 패턴을 그대로 따른다
- **마이그레이션은 append-only**: 기존 파일 수정 금지, 새 파일로 앞으로 굴린다

---

## File Structure

| 파일 | 책임 |
|---|---|
| `supabase/migrations/20260909100000_subscriptions.sql` | `subscriptions`·`subscription_invoices` 테이블, pgcrypto, RLS 잠금 |
| `server/app/toss_billing.py` | 토스 빌링 HTTP 클라이언트만. 빌링키 발급·자동결제 승인·빌링키 삭제. DB 를 모른다 |
| `server/app/subscriptions.py` | `/v1/subscriptions/*` 라우트. 상태기계와 트랜잭션 경계 |
| `server/app/workers/subscription_biller.py` | 주기 청구(갱신·유예 재시도) + 만료 처리 워커 |
| `server/app/repo.py` (수정) | `grant_subscription` 이월 전환, `expire_subscription_buckets`·`subscription_bucket_summary` 추가 |
| `server/app/config.py` (수정) | 빌링 관련 설정 5개 |
| `server/app/main.py` (수정) | 라우터·워커 등록 |
| `src/features/subscription/Subscription.jsx` | 구독 관리 화면(상태·해지·카드 교체) |
| `src/features/pricing/Pricing.jsx` (수정) | 구독 버튼 활성화 + 고지문 교체 |
| `src/lib/api/httpAdapter.js` (수정) | 구독 API 6개 |
| `copilot/api/manifest.yml` (수정) | `TOSS_BILLING_SECRET_KEY`·`TOSS_BILLING_KEK`·`TOSS_WEBHOOK_PATH_SECRET` 시크릿 배선 |

---

## Task 1: 스키마

**Files:**
- Create: `supabase/migrations/20260909100000_subscriptions.sql`
- Test: `server/tests/test_subscription_migration.py`

**Interfaces:**
- Produces: 테이블 `public.subscriptions`, `public.subscription_invoices`. 이후 모든 태스크가 이 컬럼명을 쓴다

- [ ] **Step 1: 마이그레이션 작성**

```sql
-- 토스 자동결제(빌링) 구독 — 계획서 docs/plans/2026-09-09-toss-billing-subscription.md
-- 왜 toss_payment_orders 를 재사용하지 않는가: 그 테이블은 리다이렉트 기반 1회 결제의
-- 주문 인텐트다(결제창이 만든 paymentKey 를 사용자가 들고 돌아온다). 빌링은 서버가 직접
-- 승인하고 주기마다 반복되며 유예·재시도 상태를 갖는다 — 상태기계가 다르다.
create extension if not exists pgcrypto;

create table if not exists public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  -- 1인 1구독. 등급 변경은 이 행을 갱신한다(새 행을 만들지 않는다).
  user_id uuid not null unique references auth.users (id) on delete cascade,
  plan_code text not null,                       -- pricing_plans.code (kind='subscription')
  status text not null default 'active'
    check (status in ('active', 'past_due', 'canceled', 'ended')),
  -- 토스 빌링키. 평문 저장 금지 — customerKey(=user_id)는 사실상 공개값이라
  -- 빌링키만 새면 무단 결제가 가능하다. KEK 는 앱 env(SSM)에만 있다.
  billing_key_enc bytea not null,
  card_brand text,                               -- 표시 전용(카드사명)
  card_last4 text,                               -- 표시 전용(마지막 4자리)
  current_period_start timestamptz not null default now(),
  current_period_end timestamptz not null,
  next_billing_at timestamptz,                   -- null = 청구 대상 아님(canceled/ended)
  scheduled_plan_code text,                      -- 다운그레이드 예약(다음 갱신에 적용)
  fail_count integer not null default 0 check (fail_count >= 0),
  grace_until timestamptz,                       -- past_due 유예 종료(실패시각 + 3일)
  last_failure_code text,
  last_failure_message text,
  billing_key_invalid boolean not null default false,   -- BILLING_DELETED 웹훅 힌트
  canceled_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- 종료 상태는 청구 대상이 아니다(스케줄러가 집지 않게 DB 가 강제한다)
  check ((status in ('canceled', 'ended') and next_billing_at is null)
      or (status in ('active', 'past_due') and next_billing_at is not null)),
  check (current_period_end > current_period_start)
);

-- 스케줄러의 유일한 스캔 축. 부분 인덱스로 종료 구독을 아예 제외한다.
create index if not exists subscriptions_due_idx
  on public.subscriptions (next_billing_at)
  where status in ('active', 'past_due');
create index if not exists subscriptions_period_end_idx
  on public.subscriptions (current_period_end)
  where status in ('canceled', 'past_due');

create table if not exists public.subscription_invoices (
  id uuid primary key default gen_random_uuid(),
  subscription_id uuid not null references public.subscriptions (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  -- 토스 계약: 영문 대소문자·숫자·'-','_','=' 6~64자. Idempotency-Key 로도 쓴다.
  order_id text not null unique check (char_length(order_id) between 6 and 64),
  kind text not null check (kind in ('initial', 'renewal', 'upgrade_proration')),
  plan_code text not null,
  amount integer not null check (amount >= 0),
  credits integer not null check (credits >= 0),
  period_start timestamptz not null,
  period_end timestamptz not null,
  status text not null default 'pending' check (status in ('pending', 'paid', 'failed')),
  payment_key text,
  attempt integer not null default 1 check (attempt >= 1),
  fail_code text,
  fail_message text,
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
-- 같은 paymentKey 가 두 청구서에 붙지 않게(부분 유니크 — null 다수 허용)
create unique index if not exists subscription_invoices_payment_key_idx
  on public.subscription_invoices (payment_key) where payment_key is not null;
-- 한 구독의 같은 주기 갱신 청구서는 하나뿐이다. 스케줄러가 두 번 돌아도 두 번 청구하지
-- 않는 최후 방어선(advisory lock 이 뚫려도 DB 가 막는다). 업그레이드 비례분은 같은 주기에
-- 여러 번 있을 수 있으므로 renewal/initial 만 건다.
create unique index if not exists subscription_invoices_period_idx
  on public.subscription_invoices (subscription_id, period_start)
  where kind in ('initial', 'renewal');
create index if not exists subscription_invoices_user_idx
  on public.subscription_invoices (user_id, created_at desc);

-- RLS 활성 + 정책 없음 = service_role 만. 빌링키·금액 스냅샷을 클라이언트가 못 읽는다.
alter table public.subscriptions enable row level security;
alter table public.subscription_invoices enable row level security;

create trigger subscriptions_updated_at before update on public.subscriptions
  for each row execute function public.set_updated_at();
create trigger subscription_invoices_updated_at before update on public.subscription_invoices
  for each row execute function public.set_updated_at();
```

- [ ] **Step 2: 실패하는 테스트 작성**

```python
"""구독 스키마 회귀 — 마이그레이션 파일의 계약을 문자열로 고정한다.

DB 없이 검증한다(CI 에 Postgres 가 없다). 여기서 지키는 것은 '돈이 새지 않는 구조':
빌링키가 평문 컬럼이 아니고, RLS 가 켜져 있고, 같은 주기를 두 번 청구할 수 없다.
"""

from pathlib import Path

import pytest

MIGRATION = (Path(__file__).resolve().parents[2]
             / "supabase/migrations/20260909100000_subscriptions.sql")


@pytest.fixture(scope="module")
def sql():
    return MIGRATION.read_text(encoding="utf-8")


def test_billing_key_is_encrypted_not_plaintext(sql):
    assert "billing_key_enc bytea not null" in sql
    assert "billing_key text" not in sql


def test_rls_enabled_on_both_tables(sql):
    assert "alter table public.subscriptions enable row level security;" in sql
    assert "alter table public.subscription_invoices enable row level security;" in sql
    assert "create policy" not in sql          # 정책 0개 = service_role 전용


def test_one_subscription_per_user(sql):
    assert "user_id uuid not null unique references auth.users" in sql


def test_same_period_cannot_be_charged_twice(sql):
    assert "subscription_invoices_period_idx" in sql
    assert "(subscription_id, period_start)" in sql


def test_terminal_status_cannot_be_scheduled(sql):
    assert "status in ('canceled', 'ended') and next_billing_at is null" in sql
```

- [ ] **Step 3: 테스트 실행 — 실패 확인**

Run: `cd server && python -m pytest tests/test_subscription_migration.py -v`
Expected: FAIL — `FileNotFoundError` (마이그레이션 파일 없음)

- [ ] **Step 4: Step 1 의 마이그레이션 파일을 실제로 생성하고 테스트 통과 확인**

Run: `cd server && python -m pytest tests/test_subscription_migration.py -v`
Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
git add supabase/migrations/20260909100000_subscriptions.sql server/tests/test_subscription_migration.py
git commit -m "feat(subscription): 구독·청구서 스키마 — 빌링키 암호화 저장, 주기 중복청구 차단"
```

---

## Task 2: 크레딧 이월 전환 (repo)

**Files:**
- Modify: `server/app/repo.py:2896-2954` (`grant_subscription`)
- Test: `server/tests/test_subscription_credits.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `grant_subscription(conn, *, user_id, plan_code, metadata=None, credits=None, period_end_sql="now() + interval '1 month'") -> {"creditSourceId": str, "credits": int, "available": int}` — **기존 버킷을 만료시키지 않는다**
  - `expire_subscription_buckets(conn, *, user_id, reason: str) -> {"expired": int, "available": int}`
  - `subscription_bucket_summary(conn, user_id) -> {"credits": int, "expiresAt": datetime | None}`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""구독 크레딧 이월 정책 — repo 계층 회귀.

정책(계획서 §0.1): 구독이 살아있는 동안 남은 구독 크레딧은 소멸하지 않는다.
소멸은 해지·유예만료라는 '사건'에서만 일어난다. 이 구분이 무너지면 사용자는
매달 산 크레딧을 조용히 잃는다.
"""

import pytest

import app.repo as repo


class _Cur:
    """SQL 문자열로 분기하는 최소 커서. 실행된 SQL 을 전부 기록해 단언에 쓴다."""

    def __init__(self, state):
        self.s = state
        self._rows = []

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.s["sql"].append(q)
        if "from pricing_plans" in q:
            self._rows = [{"id": "plan-1", "credits": 600}] if self.s["plan_ok"] else []
        elif "from credit_accounts" in q:
            self._rows = [{"balance": self.s["balance"], "reserved": 0}]
        elif "from credit_sources" in q:
            self._rows = list(self.s["buckets"])
        elif "insert into credit_sources" in q:
            self.s["inserted"].append(params)
            self._rows = [{"id": "src-new"}]
        elif "update credit_sources set status = 'expired'" in q:
            self.s["expired"].append(params)
            self._rows = []
        elif "insert into credit_ledger" in q:
            self.s["ledger"].append({"action_key": params[2], "delta": params[3]})
            self._rows = []
        elif "update credit_accounts" in q:
            self.s["balance"] = params[0]
            self._rows = []
        else:
            self._rows = []

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, state):
        self.s = state

    def cursor(self):
        return _Cur(self.s)


@pytest.fixture()
def state():
    return {"sql": [], "plan_ok": True, "balance": 500, "inserted": [], "expired": [],
            "ledger": [], "buckets": [{"id": "src-old", "remaining_credits": 500}]}


@pytest.mark.asyncio
async def test_renewal_keeps_existing_subscription_buckets(state):
    """갱신은 이월이다 — 기존 버킷을 만료시키지 않고 새 버킷만 더한다."""
    result = await repo.grant_subscription(_Conn(state), user_id="u1", plan_code="seller")
    assert state["expired"] == []                                   # 아무것도 소멸하지 않았다
    assert [l["action_key"] for l in state["ledger"]] == ["grant_subscription"]
    assert result["available"] == 500 + 600                          # 이월분 + 신규


@pytest.mark.asyncio
async def test_expire_buckets_zeroes_all_subscription_credits(state):
    """해지·유예만료에서만 소멸한다. 이월분까지 전부."""
    result = await repo.expire_subscription_buckets(_Conn(state), user_id="u1", reason="canceled")
    assert len(state["expired"]) == 1
    assert result["expired"] == 500
    assert result["available"] == 0
    assert [l["action_key"] for l in state["ledger"]] == ["expire_subscription"]
    assert state["ledger"][0]["delta"] == -500


@pytest.mark.asyncio
async def test_grant_accepts_prorated_credits_override(state):
    """업그레이드 비례 지급 — 요금제 정가가 아니라 계산된 양을 지급한다."""
    result = await repo.grant_subscription(
        _Conn(state), user_id="u1", plan_code="pro", credits=137)
    assert result["credits"] == 137
    assert result["available"] == 500 + 137
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd server && python -m pytest tests/test_subscription_credits.py -v`
Expected: FAIL — `test_renewal_keeps_existing_subscription_buckets` 에서 `state["expired"]` 가 비어있지 않음(현 코드가 만료시킴), `expire_subscription_buckets` 는 `AttributeError`

- [ ] **Step 3: `grant_subscription` 을 이월판으로 바꾸고 두 함수를 추가**

`server/app/repo.py` 의 `grant_subscription` 전체를 아래로 교체한다:

```python
async def grant_subscription(
    conn: AsyncConnection, *, user_id: str, plan_code: str, metadata: dict | None = None,
    credits: int | None = None,
    period_end_sql: str = "now() + interval '1 month'",
) -> dict:
    """구독 크레딧 지급 — **이월**(계획서 §0.1).

    2026-09-09 정책 변경: 예전에는 갱신 때 기존 구독 버킷을 만료시키고 새로 줬다(소멸).
    이제 소멸은 해지·유예만료라는 사건에서만 일어난다(expire_subscription_buckets).
    갱신은 버킷을 하나 더 얹을 뿐이다. FIFO 정렬이 (source_type, created_at) 이라
    이월분이 자연히 먼저 소진된다 — 오래된 크레딧부터 쓰는 게 사용자에게 유리하다.

    credits: 지급량 override(업그레이드 비례분). None 이면 요금제 정가.
    period_end_sql: 버킷 만료 시각 SQL. 업그레이드 비례 버킷은 현재 주기 끝에 맞춘다.
    """
    metadata = metadata or {}
    async with conn.cursor() as cur:
        await cur.execute(
            "select id::text as id, credits from pricing_plans "
            "where code = %s and kind = 'subscription' and is_active",
            (plan_code,),
        )
        plan = await cur.fetchone()
        if plan is None:
            raise CreditError("unknown_plan", f"요금제를 찾을 수 없어요: {plan_code}", 404)
        grant = plan["credits"] if credits is None else int(credits)
        if grant < 0:
            raise CreditError("invalid_grant", "지급 크레딧이 음수예요.", 400)
        await cur.execute(
            "select balance, reserved from credit_accounts where user_id = %s for update",
            (user_id,),
        )
        acct = await cur.fetchone()
        if acct is None:
            raise CreditError("account_missing", "크레딧 계정이 없어요.", 404)
        running = acct["balance"] + grant
        await cur.execute(
            "insert into credit_sources (user_id, source_type, plan_id, initial_credits, "
            f"remaining_credits, status, period_end) "
            f"values (%s, 'subscription', %s, %s, %s, 'active', {period_end_sql}) "
            "returning id::text as id",
            (user_id, plan["id"], grant, grant),
        )
        src_id = (await cur.fetchone())["id"]
        await cur.execute(
            "insert into credit_ledger (user_id, credit_source_id, action_key, delta, "
            "balance_after, available_after, metadata) values (%s,%s,'grant_subscription',%s,%s,%s,%s)",
            (user_id, src_id, grant, running, running - acct["reserved"], Json(metadata)),
        )
        await cur.execute(
            "update credit_accounts set balance = %s where user_id = %s", (running, user_id)
        )
    return {"creditSourceId": src_id, "credits": grant, "available": running - acct["reserved"]}


async def expire_subscription_buckets(
    conn: AsyncConnection, *, user_id: str, reason: str
) -> dict:
    """구독 버킷 전량 소멸 — 해지 주기 종료·유예 만료에서만 부른다(계획서 §0.1).

    이월분까지 전부 지운다. 사용자에게는 큰 금액이 한 번에 사라지는 사건이므로,
    호출 전에 화면이 소멸 예정 수량·날짜를 이미 보여줬어야 한다(Task 10).
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "select balance, reserved from credit_accounts where user_id = %s for update",
            (user_id,),
        )
        acct = await cur.fetchone()
        if acct is None:
            raise CreditError("account_missing", "크레딧 계정이 없어요.", 404)
        running = acct["balance"]
        await cur.execute(
            "select id::text as id, remaining_credits from credit_sources "
            "where user_id = %s and source_type = 'subscription' and status = 'active' "
            "for update",
            (user_id,),
        )
        buckets = await cur.fetchall()
        expired = 0
        for bucket in buckets:
            await cur.execute(
                "update credit_sources set status = 'expired', remaining_credits = 0 "
                "where id = %s",
                (bucket["id"],),
            )
            running -= bucket["remaining_credits"]
            expired += bucket["remaining_credits"]
            await cur.execute(
                "insert into credit_ledger (user_id, credit_source_id, action_key, delta, "
                "balance_after, available_after, metadata) "
                "values (%s,%s,'expire_subscription',%s,%s,%s,%s)",
                (user_id, bucket["id"], -bucket["remaining_credits"], running,
                 running - acct["reserved"], Json({"reason": reason})),
            )
        if buckets:
            await cur.execute(
                "update credit_accounts set balance = %s where user_id = %s", (running, user_id)
            )
    return {"expired": expired, "available": running - acct["reserved"]}


async def subscription_bucket_summary(conn: AsyncConnection, user_id: str) -> dict:
    """해지 화면이 '무엇이 언제 사라지는지' 를 숫자로 보여주기 위한 조회."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select coalesce(sum(remaining_credits), 0) as credits, max(period_end) as expires_at "
            "from credit_sources "
            "where user_id = %s and source_type = 'subscription' and status = 'active'",
            (user_id,),
        )
        row = await cur.fetchone() or {}
    return {"credits": int(row.get("credits") or 0), "expiresAt": row.get("expires_at")}
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `cd server && python -m pytest tests/test_subscription_credits.py tests/test_credits.py -v`
Expected: 신규 3 passed. `test_credits.py` 도 통과해야 한다 — 깨지면 그 테스트가 옛 소멸 정책을 고정하고 있는 것이므로, 해당 테스트를 이월 정책으로 고쳐 쓴다(삭제 금지)

- [ ] **Step 5: 커밋**

```bash
git add server/app/repo.py server/tests/test_subscription_credits.py server/tests/test_credits.py
git commit -m "feat(credits): 구독 크레딧을 이월로 전환 — 소멸은 해지·유예만료에서만"
```

---

## Task 3: 토스 빌링 HTTP 클라이언트

**Files:**
- Create: `server/app/toss_billing.py`
- Modify: `server/app/config.py:327-329` (설정 추가), `server/app/config.py:642-644` (env 로드)
- Test: `server/tests/test_toss_billing_client.py`

**Interfaces:**
- Consumes: 없음(DB 를 모른다)
- Produces:
  - `class TossBillingError(Exception)`: `.code: str`, `.message: str`, `.retryable: bool`
  - `async issue_billing_key(settings, *, auth_key: str, customer_key: str) -> dict` — `{"billingKey", "cardBrand", "cardLast4"}`
  - `async charge(settings, *, billing_key: str, customer_key: str, order_id: str, order_name: str, amount: int) -> dict` — 토스 Payment 객체
  - `async delete_billing_key(settings, *, billing_key: str) -> None`

- [ ] **Step 1: config 에 설정 추가**

`server/app/config.py` 의 `toss_confirm_timeout` 아래에 추가:

```python
    # ---- 토스 자동결제(빌링) — 계획서 docs/plans/2026-09-09-toss-billing-subscription.md
    # 자동결제는 별도 계약 MID 라 일반결제와 시크릿 키가 다를 수 있다. 비어 있으면
    # toss_secret_key 로 떨어진다(한 MID 로 계약한 상점).
    toss_billing_secret_key: str | None = None
    # 토스 문서: 자동결제 승인은 최대 60초 소요, 타임아웃 최소 60초.
    # toss_confirm_timeout(15초) 을 재사용하면 정상 승인이 타임아웃으로 뒤집힌다.
    toss_billing_timeout: float = 60.0
    # 빌링키 컬럼 암호화 KEK(pgcrypto pgp_sym_encrypt). 없으면 구독 라우트가 503.
    toss_billing_kek: str | None = None
    # 웹훅 경로 시크릿 — 토스 일반 웹훅은 서명 헤더가 없어 경로 지식이 인증 대용이다.
    toss_webhook_path_secret: str | None = None
    subscription_billing_enabled: bool = False
```

`build_settings` 쪽 `toss_confirm_timeout=...` 아래에 추가:

```python
        toss_billing_secret_key=os.getenv("TOSS_BILLING_SECRET_KEY") or None,
        toss_billing_timeout=float(os.getenv("TOSS_BILLING_TIMEOUT", "60")),
        toss_billing_kek=os.getenv("TOSS_BILLING_KEK") or None,
        toss_webhook_path_secret=os.getenv("TOSS_WEBHOOK_PATH_SECRET") or None,
        subscription_billing_enabled=(
            os.getenv("SUBSCRIPTION_BILLING_ENABLED", "false").lower() == "true"),
```

- [ ] **Step 2: 실패하는 테스트 작성**

```python
"""토스 빌링 클라이언트 — HTTP 계약 회귀.

여기서 지키는 것: ① 승인 실패의 '확정 거절'과 '결과 미상'을 구분한다(미상을 실패로
굳히면 돈만 받고 크레딧을 안 주는 상태가 생긴다) ② 시크릿·빌링키가 예외 메시지로
새지 않는다 ③ 빌링 전용 타임아웃(60초)을 쓴다.
"""

import httpx
import pytest

import app.toss_billing as tb
from conftest import make_settings

SETTINGS = make_settings(toss_billing_secret_key="test_sk_billing",
                         toss_api_base="https://toss.test", toss_billing_timeout=60.0)


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_issue_billing_key_returns_key_and_card_display(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={
            "billingKey": "bk-1", "cardCompany": "현대", "cardNumber": "43301234****123*",
            "card": {"number": "43301234****123*"},
        })

    monkeypatch.setattr(tb, "_transport_for", lambda s: _transport(handler))
    out = await tb.issue_billing_key(SETTINGS, auth_key="ak-1", customer_key="cus-1")
    assert out["billingKey"] == "bk-1"
    assert out["cardBrand"] == "현대"
    assert out["cardLast4"] == "123*"
    assert seen["url"] == "https://toss.test/v1/billing/authorizations/issue"
    assert seen["auth"].startswith("Basic ")


@pytest.mark.asyncio
async def test_charge_posts_to_billing_key_path_with_idempotency_key(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["idem"] = request.headers.get("idempotency-key")
        return httpx.Response(200, json={"status": "DONE", "totalAmount": 79900,
                                         "paymentKey": "pk-1", "method": "카드"})

    monkeypatch.setattr(tb, "_transport_for", lambda s: _transport(handler))
    out = await tb.charge(SETTINGS, billing_key="bk-1", customer_key="cus-1",
                          order_id="wl-sub-abc123", order_name="Seller 구독", amount=79900)
    assert out["status"] == "DONE"
    assert seen["url"] == "https://toss.test/v1/billing/bk-1"
    assert seen["idem"] == "wl-sub-abc123"


@pytest.mark.asyncio
async def test_card_rejection_is_not_retryable(monkeypatch):
    def handler(request):
        return httpx.Response(400, json={"code": "REJECT_CARD_COMPANY",
                                         "message": "한도초과"})

    monkeypatch.setattr(tb, "_transport_for", lambda s: _transport(handler))
    with pytest.raises(tb.TossBillingError) as e:
        await tb.charge(SETTINGS, billing_key="bk-1", customer_key="cus-1",
                        order_id="wl-sub-abc123", order_name="x", amount=100)
    assert e.value.code == "REJECT_CARD_COMPANY"
    assert e.value.retryable is False


@pytest.mark.asyncio
async def test_gateway_5xx_and_transport_failure_are_retryable(monkeypatch):
    def five_hundred(request):
        return httpx.Response(500, json={"code": "INTERNAL", "message": "x"})

    monkeypatch.setattr(tb, "_transport_for", lambda s: _transport(five_hundred))
    with pytest.raises(tb.TossBillingError) as e:
        await tb.charge(SETTINGS, billing_key="bk-1", customer_key="cus-1",
                        order_id="wl-sub-abc123", order_name="x", amount=100)
    assert e.value.retryable is True

    def boom(request):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(tb, "_transport_for", lambda s: _transport(boom))
    with pytest.raises(tb.TossBillingError) as e2:
        await tb.charge(SETTINGS, billing_key="bk-1", customer_key="cus-1",
                        order_id="wl-sub-abc123", order_name="x", amount=100)
    assert e2.value.retryable is True


@pytest.mark.asyncio
async def test_secret_and_billing_key_never_appear_in_error(monkeypatch):
    def handler(request):
        return httpx.Response(403, json={"code": "UNAUTHORIZED_KEY", "message": "bad key"})

    monkeypatch.setattr(tb, "_transport_for", lambda s: _transport(handler))
    with pytest.raises(tb.TossBillingError) as e:
        await tb.charge(SETTINGS, billing_key="bk-SECRET", customer_key="cus-1",
                        order_id="wl-sub-abc123", order_name="x", amount=100)
    blob = f"{e.value.code} {e.value.message} {e.value!r}"
    assert "test_sk_billing" not in blob
    assert "bk-SECRET" not in blob


def test_billing_secret_falls_back_to_general_secret():
    s = make_settings(toss_secret_key="general", toss_billing_secret_key=None)
    assert tb.billing_secret(s) == "general"
    s2 = make_settings(toss_secret_key="general", toss_billing_secret_key="billing")
    assert tb.billing_secret(s2) == "billing"
```

- [ ] **Step 3: 테스트 실행 — 실패 확인**

Run: `cd server && python -m pytest tests/test_toss_billing_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.toss_billing'`

- [ ] **Step 4: 클라이언트 구현**

`server/app/toss_billing.py`:

```python
"""토스페이먼츠 자동결제(빌링) HTTP 클라이언트 — 계획서 §0.3.

이 모듈은 **DB 를 모른다**. 토스와의 HTTP 계약만 담고, 상태 전이는 subscriptions.py 가 한다.
그렇게 나눈 이유: 승인 호출은 재시도 경로에서도(워커) 같은 코드를 써야 하는데, 라우트에
묶어 두면 워커가 라우트를 임포트하게 된다.

**핵심 구분 — retryable**: 4xx 는 토스/카드사의 확정 거절이다(재시도해도 같다).
5xx·전송 실패는 **승인 여부를 모르는 상태**다. 카드가 승인됐는데 응답만 유실됐을 수 있으니
실패로 굳히면 안 된다. 같은 Idempotency-Key 로 재시도하면 토스가 원 결과를 그대로 준다.
"""

import logging

import httpx

log = logging.getLogger("wearless.toss_billing")

_ISSUE_PATH = "/v1/billing/authorizations/issue"
_OK_STATUS = "DONE"


class TossBillingError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def __repr__(self) -> str:      # 시크릿이 섞일 여지를 원천 차단(코드·메시지만 노출)
        return f"TossBillingError(code={self.code!r}, retryable={self.retryable})"


def billing_secret(settings) -> str | None:
    """자동결제는 별도 계약 MID 라 키가 다를 수 있다. 없으면 일반결제 키로 떨어진다."""
    return settings.toss_billing_secret_key or settings.toss_secret_key


def _transport_for(settings):
    """테스트에서 MockTransport 로 갈아끼우는 이음매. 운영에서는 None(기본 전송)."""
    return None


async def _post(settings, path: str, payload: dict, *, idempotency_key: str | None = None) -> dict:
    secret = billing_secret(settings)
    headers = {}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        async with httpx.AsyncClient(timeout=settings.toss_billing_timeout,
                                     transport=_transport_for(settings)) as client:
            res = await client.post(
                f"{settings.toss_api_base}{path}", json=payload, headers=headers,
                auth=httpx.BasicAuth(secret, ""),      # Basic base64("{secretKey}:")
            )
    except httpx.HTTPError as e:
        log.warning("toss billing unreachable path=%s: %s", path, type(e).__name__)
        raise TossBillingError("payment_gateway_unreachable",
                               "결제 확인이 지연되고 있어요.", retryable=True) from None
    if res.status_code != 200:
        try:
            body = res.json()
        except Exception:
            body = {}
        code = str(body.get("code") or "billing_request_failed")
        message = str(body.get("message") or "결제 요청에 실패했어요.")
        log.warning("toss billing rejected path=%s status=%s code=%s", path, res.status_code, code)
        # 5xx = 결과 미상 → 재시도 가능. 4xx = 확정 거절.
        raise TossBillingError(code, message, retryable=res.status_code >= 500)
    return res.json()


async def issue_billing_key(settings, *, auth_key: str, customer_key: str) -> dict:
    """authKey → 빌링키. **응답을 저장하지 못하면 빌링키는 영구 분실이다**(조회 API 없음)."""
    body = await _post(settings, _ISSUE_PATH,
                       {"authKey": auth_key, "customerKey": customer_key})
    key = body.get("billingKey")
    if not key:
        raise TossBillingError("billing_key_missing", "빌링키를 받지 못했어요.", retryable=False)
    masked = str(body.get("cardNumber") or (body.get("card") or {}).get("number") or "")
    return {
        "billingKey": key,
        "cardBrand": body.get("cardCompany"),
        "cardLast4": masked[-4:] or None,
    }


async def charge(settings, *, billing_key: str, customer_key: str, order_id: str,
                 order_name: str, amount: int) -> dict:
    """자동결제 승인. orderId 를 Idempotency-Key 로 써 재시도가 이중 결제가 되지 않게 한다."""
    body = await _post(
        settings, f"/v1/billing/{billing_key}",
        {"customerKey": customer_key, "amount": amount,
         "orderId": order_id, "orderName": order_name},
        idempotency_key=order_id,
    )
    if body.get("status") != _OK_STATUS or body.get("totalAmount") != amount:
        # 200 인데 우리 기대와 다르다 — 적립하면 안 된다. 재시도해도 같으므로 확정 실패.
        raise TossBillingError(
            "payment_not_approved",
            f"결제가 승인되지 않았어요(상태 {body.get('status')}).", retryable=False)
    return body


async def delete_billing_key(settings, *, billing_key: str) -> None:
    """카드 교체 시 옛 키 정리. 실패해도 서비스는 진행한다(다음 결제에 쓰지 않으므로)."""
    try:
        async with httpx.AsyncClient(timeout=settings.toss_billing_timeout,
                                     transport=_transport_for(settings)) as client:
            await client.delete(f"{settings.toss_api_base}/v1/billing/{billing_key}",
                                auth=httpx.BasicAuth(billing_secret(settings), ""))
    except httpx.HTTPError as e:
        log.warning("toss billing key delete failed: %s", type(e).__name__)
```

- [ ] **Step 5: 테스트 실행 — 통과 확인**

Run: `cd server && python -m pytest tests/test_toss_billing_client.py -v`
Expected: 6 passed

- [ ] **Step 6: 커밋**

```bash
git add server/app/toss_billing.py server/app/config.py server/tests/test_toss_billing_client.py
git commit -m "feat(subscription): 토스 빌링 클라이언트 — 60초 타임아웃·멱등키·재시도 가능 여부 구분"
```

---

## Task 4: 구독 시작 (빌링키 발급 + 첫 결제)

**Files:**
- Create: `server/app/subscriptions.py`
- Modify: `server/app/main.py:411-413` 근처(라우터 등록)
- Test: `server/tests/test_subscriptions_start.py`

**Interfaces:**
- Consumes: `toss_billing.issue_billing_key`, `toss_billing.charge`, `repo.grant_subscription`
- Produces:
  - `router` (prefix `/v1/subscriptions`)
  - `POST /v1/subscriptions/start` — body `{authKey, customerKey, planCode}` → `{subscriptionId, planCode, credits, available, currentPeriodEnd, card}`
  - `_new_order_id(prefix: str) -> str` — `wl-sub-...` / `wl-up-...`
  - `_err(code, message, status) -> HTTPException`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""구독 시작 — 돈 불변식 회귀.

거절 경로가 본체다: 금액은 서버가 정하고, customerKey 는 반드시 본인이어야 하며,
첫 결제가 실패하면 빌링키만 저장된 유령 구독이 남지 않는다.
"""

import contextlib

import pytest
from fastapi.testclient import TestClient

import app.subscriptions as subs
import app.toss_billing as tb
from app.main import create_app
from conftest import make_settings

PLAN = {"id": "plan-seller", "code": "seller", "name": "Seller",
        "credits": 1800, "price": 79900}


class _Cur:
    def __init__(self, state):
        self.s = state
        self._row = None

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.s["sql"].append(q)
        if "from pricing_plans" in q:
            self._row = dict(PLAN) if params[0] == PLAN["code"] else None
        elif "from subscriptions" in q:
            self._row = self.s["sub"]
        elif "insert into subscriptions" in q:
            self.s["sub"] = {"id": "sub-1", "user_id": params[0], "plan_code": params[1],
                             "status": "active"}
            self.s["inserted_billing_key_param"] = params
            self._row = {"id": "sub-1", "current_period_end": "2026-10-09T00:00:00+00:00"}
        elif "insert into subscription_invoices" in q:
            self.s["invoices"].append(params)
            self._row = {"id": "inv-1"}
        elif "update subscription_invoices" in q:
            self.s["invoice_updates"].append(q)
            self._row = None
        else:
            self._row = None

    async def fetchone(self):
        return self._row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, state):
        self.s = state

    def cursor(self):
        return _Cur(self.s)

    async def commit(self):
        self.s["commits"] += 1

    async def rollback(self):
        self.s["rollbacks"] += 1


@pytest.fixture()
def sub(monkeypatch, keypair):
    _, public_key = keypair
    state = {"sql": [], "sub": None, "invoices": [], "invoice_updates": [], "commits": 0,
             "rollbacks": 0, "issued": [], "charged": [], "deleted": [], "grants": [],
             "charge_error": None, "balance": 0}

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn(state)

    async def fake_issue(settings, *, auth_key, customer_key):
        state["issued"].append({"authKey": auth_key, "customerKey": customer_key})
        return {"billingKey": "bk-1", "cardBrand": "현대", "cardLast4": "1234"}

    async def fake_charge(settings, *, billing_key, customer_key, order_id, order_name, amount):
        state["charged"].append({"orderId": order_id, "amount": amount,
                                 "billingKey": billing_key})
        if state["charge_error"] is not None:
            raise state["charge_error"]
        return {"status": "DONE", "totalAmount": amount, "paymentKey": "pk-1", "method": "카드"}

    async def fake_delete(settings, *, billing_key):
        state["deleted"].append(billing_key)

    async def fake_grant(conn, *, user_id, plan_code, metadata=None, credits=None,
                         period_end_sql=None):
        granted = PLAN["credits"] if credits is None else credits
        state["balance"] += granted
        state["grants"].append({"plan_code": plan_code, "credits": granted})
        return {"creditSourceId": "src-1", "credits": granted, "available": state["balance"]}

    monkeypatch.setattr(subs, "get_conn", fake_conn)
    monkeypatch.setattr(subs.toss_billing, "issue_billing_key", fake_issue)
    monkeypatch.setattr(subs.toss_billing, "charge", fake_charge)
    monkeypatch.setattr(subs.toss_billing, "delete_billing_key", fake_delete)
    monkeypatch.setattr(subs.repo, "grant_subscription", fake_grant)

    app = create_app(make_settings(
        toss_secret_key="sk", toss_billing_kek="kek", subscription_billing_enabled=True))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app), state


def _auth(make_token, sub_id="user-1"):
    return {"Authorization": f"Bearer {make_token(sub=sub_id)}"}


def test_start_charges_server_side_price_and_grants(sub, make_token):
    client, state = sub
    res = client.post("/v1/subscriptions/start", headers=_auth(make_token),
                      json={"authKey": "ak", "customerKey": "user-1", "planCode": "seller"})
    assert res.status_code == 200, res.text
    assert state["charged"][0]["amount"] == PLAN["price"]      # 서버 정본 금액
    assert state["grants"][0]["credits"] == PLAN["credits"]
    assert res.json()["available"] == PLAN["credits"]


def test_start_rejects_customer_key_of_another_user(sub, make_token):
    """customerKey 는 우리가 발급한 값(=user_id)이다. 남의 것을 들고 오면 빌링키가
    엉뚱한 사람에게 묶인다 — 토스를 부르기 전에 막는다."""
    client, state = sub
    res = client.post("/v1/subscriptions/start", headers=_auth(make_token),
                      json={"authKey": "ak", "customerKey": "user-2", "planCode": "seller"})
    assert res.status_code == 403
    assert state["issued"] == []


def test_start_with_unknown_plan_is_404(sub, make_token):
    client, state = sub
    res = client.post("/v1/subscriptions/start", headers=_auth(make_token),
                      json={"authKey": "ak", "customerKey": "user-1", "planCode": "nope"})
    assert res.status_code == 404
    assert state["issued"] == []


def test_first_charge_failure_leaves_no_ghost_subscription(sub, make_token):
    """첫 결제가 확정 거절되면 구독을 만들지 않는다. 빌링키만 남은 구독이 있으면
    다음 달 스케줄러가 돈을 못 받은 사람에게 크레딧을 준다."""
    client, state = sub
    state["charge_error"] = tb.TossBillingError("REJECT_CARD_COMPANY", "한도초과",
                                                retryable=False)
    res = client.post("/v1/subscriptions/start", headers=_auth(make_token),
                      json={"authKey": "ak", "customerKey": "user-1", "planCode": "seller"})
    assert res.status_code == 402
    assert state["rollbacks"] >= 1
    assert state["grants"] == []
    assert state["deleted"] == ["bk-1"]        # 쓸모없어진 빌링키는 토스에서도 지운다


def test_duplicate_subscription_is_409(sub, make_token):
    client, state = sub
    state["sub"] = {"id": "sub-existing", "status": "active", "user_id": "user-1"}
    res = client.post("/v1/subscriptions/start", headers=_auth(make_token),
                      json={"authKey": "ak", "customerKey": "user-1", "planCode": "seller"})
    assert res.status_code == 409
    assert state["issued"] == []


def test_routes_absent_when_flag_off(keypair, make_token):
    _, public_key = keypair
    app = create_app(make_settings(toss_secret_key="sk", toss_billing_kek="kek",
                                   subscription_billing_enabled=False))
    app.state.jwt_key_resolver = lambda token: public_key
    res = TestClient(app).post("/v1/subscriptions/start", headers=_auth(make_token),
                               json={"authKey": "a", "customerKey": "user-1",
                                     "planCode": "seller"})
    assert res.status_code == 404


def test_missing_kek_is_503_not_plaintext_storage(keypair, make_token):
    _, public_key = keypair
    app = create_app(make_settings(toss_secret_key="sk", toss_billing_kek=None,
                                   subscription_billing_enabled=True))
    app.state.jwt_key_resolver = lambda token: public_key
    res = TestClient(app).post("/v1/subscriptions/start", headers=_auth(make_token),
                               json={"authKey": "a", "customerKey": "user-1",
                                     "planCode": "seller"})
    assert res.status_code == 503
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd server && python -m pytest tests/test_subscriptions_start.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.subscriptions'`

- [ ] **Step 3: 라우터 구현**

`server/app/subscriptions.py`:

```python
"""토스 자동결제(빌링) 구독 — 계획서 docs/plans/2026-09-09-toss-billing-subscription.md.

**돈을 다루므로 아래 불변식이 이 모듈의 존재 이유다:**
  ① 금액의 정본은 pricing_plans. 클라이언트가 보낸 금액은 어떤 경로에서도 근거가 아니다.
  ② customerKey 는 우리가 발급한 값(=user_id)이다. 토큰 주체와 다르면 토스를 부르기 전에 막는다.
  ③ 첫 결제가 확정 거절되면 구독 행을 남기지 않는다 — 결제 안 된 구독이 다음 달에 크레딧을 준다.
  ④ 빌링키는 pgcrypto 로만 저장한다. KEK 가 없으면 503(평문 폴백 금지).
  ⑤ 승인 결과 미상(5xx·전송실패)은 실패로 굳히지 않는다. 청구서를 pending 으로 두고 워커가 잇는다.
"""

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import repo, toss_billing
from .auth import require_user
from .db import get_conn

log = logging.getLogger("wearless.subscriptions")

router = APIRouter(prefix="/v1/subscriptions", tags=["Subscriptions"])

_ORDER_ID_BYTES = 18       # "wl-sub-" + token_urlsafe(18) ≈ 31자 (토스 6~64자 안)


class StartBody(BaseModel):
    auth_key: str = Field(alias="authKey", min_length=1, max_length=300)
    customer_key: str = Field(alias="customerKey", min_length=2, max_length=300)
    plan_code: str = Field(alias="planCode", min_length=1, max_length=64)

    model_config = {"populate_by_name": True}


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _settings(request: Request):
    return request.app.state.settings


def _require_billing_config(request: Request) -> str:
    """키가 없으면 거절한다. 목 성공이나 평문 저장으로 떨어지면 안 된다(불변식 ④·⑤)."""
    s = _settings(request)
    if not toss_billing.billing_secret(s):
        raise _err("payment_not_configured", "결제가 아직 설정되지 않았어요.", 503)
    if not s.toss_billing_kek:
        raise _err("payment_not_configured", "결제가 아직 설정되지 않았어요.", 503)
    return s.toss_billing_kek


def _new_order_id(prefix: str = "sub") -> str:
    """토스 계약: 영문 대소문자·숫자·'-','_','=' 6~64자. token_urlsafe 가 그 안에 있다."""
    return f"wl-{prefix}-{secrets.token_urlsafe(_ORDER_ID_BYTES)}"[:64]


async def _load_plan(cur, plan_code: str) -> dict:
    await cur.execute(
        "select id::text as id, code, name, credits, price from pricing_plans "
        "where code = %s and kind = 'subscription' and is_active",
        (plan_code,),
    )
    plan = await cur.fetchone()
    if plan is None:
        raise _err("unknown_plan", "요금제를 찾을 수 없어요.", 404)
    return plan


@router.post("/start", summary="구독 시작 — 빌링키 발급 + 첫 결제")
async def start_subscription(
    request: Request, body: StartBody, user_id: str = Depends(require_user),
):
    """`requestBillingAuth` 성공 리다이렉트에서 받은 authKey 로 구독을 연다.

    - **Bearer Token**: 필수
    - **에지 케이스**: `403 customer_key_mismatch` · `404 unknown_plan` ·
      `409 subscription_exists` · `402`(카드 거절) · `503 payment_not_configured`
    """
    kek = _require_billing_config(request)
    settings = _settings(request)
    # 불변식 ② — 토스를 부르기 전에 막는다.
    if body.customer_key != user_id:
        raise _err("customer_key_mismatch", "결제 요청 정보가 계정과 달라요.", 403)

    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select id::text as id, status from subscriptions where user_id = %s", (user_id,)
            )
            existing = await cur.fetchone()
            if existing is not None and existing["status"] in ("active", "past_due", "canceled"):
                raise _err("subscription_exists", "이미 구독 중이에요.", 409)
            plan = await _load_plan(cur, body.plan_code)

        # 빌링키 발급 — 이 응답을 잃으면 영구 분실이므로 즉시 저장 트랜잭션으로 넘긴다.
        try:
            issued = await toss_billing.issue_billing_key(
                settings, auth_key=body.auth_key, customer_key=user_id)
        except toss_billing.TossBillingError as e:
            raise _err(e.code, e.message, 503 if e.retryable else 402)

        order_id = _new_order_id("sub")
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "insert into subscriptions (user_id, plan_code, billing_key_enc, "
                    "card_brand, card_last4, current_period_start, current_period_end, "
                    "next_billing_at) values (%s, %s, pgp_sym_encrypt(%s, %s), %s, %s, "
                    "now(), now() + interval '1 month', now() + interval '1 month') "
                    "returning id::text as id, current_period_start, current_period_end",
                    (user_id, plan["code"], issued["billingKey"], kek,
                     issued.get("cardBrand"), issued.get("cardLast4")),
                )
                sub_row = await cur.fetchone()
                await cur.execute(
                    "insert into subscription_invoices (subscription_id, user_id, order_id, "
                    "kind, plan_code, amount, credits, period_start, period_end) "
                    "values (%s, %s, %s, 'initial', %s, %s, %s, %s, %s)",
                    (sub_row["id"], user_id, order_id, plan["code"], plan["price"],
                     plan["credits"], sub_row["current_period_start"],
                     sub_row["current_period_end"]),
                )

            charged = await toss_billing.charge(
                settings, billing_key=issued["billingKey"], customer_key=user_id,
                order_id=order_id, order_name=f"{plan['name']} 구독", amount=plan["price"])

            async with conn.cursor() as cur:
                await cur.execute(
                    "update subscription_invoices set status = 'paid', payment_key = %s, "
                    "approved_at = now() where order_id = %s",
                    (charged.get("paymentKey"), order_id),
                )
            granted = await repo.grant_subscription(
                conn, user_id=user_id, plan_code=plan["code"],
                metadata={"orderId": order_id, "kind": "initial"},
                period_end_sql="now() + interval '1 month'")
            async with conn.cursor() as cur:
                await cur.execute(
                    "update profiles set plan = %s where user_id = %s", (plan["code"], user_id)
                )
        except toss_billing.TossBillingError as e:
            # 불변식 ③ — 유령 구독을 남기지 않는다. 결과 미상(retryable)이면 빌링키는
            # 남겨 둘 근거가 없다(구독 자체를 안 만들었으므로) → 양쪽 다 롤백 + 키 삭제.
            await conn.rollback()
            await toss_billing.delete_billing_key(settings, billing_key=issued["billingKey"])
            raise _err(e.code, e.message, 503 if e.retryable else 402)
        except repo.CreditError as e:
            await conn.rollback()
            raise _err(e.code, e.message, e.status)
        # 청구서·구독·크레딧·등급을 한 트랜잭션으로 커밋 — 결제만 되고 크레딧이 없는 상태 금지.
        await conn.commit()

    return JSONResponse({
        "subscriptionId": sub_row["id"],
        "planCode": plan["code"],
        "credits": granted["credits"],
        "available": granted["available"],
        "currentPeriodEnd": str(sub_row["current_period_end"]),
        "card": {"brand": issued.get("cardBrand"), "last4": issued.get("cardLast4")},
    })
```

`server/app/main.py` 에서 `payments_router` 등록 아래에 추가:

```python
    if app.state.settings.subscription_billing_enabled:
        from .subscriptions import router as subscriptions_router

        app.include_router(subscriptions_router)
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `cd server && python -m pytest tests/test_subscriptions_start.py -v`
Expected: 7 passed

- [ ] **Step 5: 커밋**

```bash
git add server/app/subscriptions.py server/app/main.py server/tests/test_subscriptions_start.py
git commit -m "feat(subscription): 구독 시작 — 빌링키 발급·첫 결제·크레딧 지급 원자화"
```

---

## Task 5: 조회 · 해지 · 해지 철회

**Files:**
- Modify: `server/app/subscriptions.py` (라우트 3개 추가)
- Test: `server/tests/test_subscriptions_cancel.py`

**Interfaces:**
- Consumes: `repo.subscription_bucket_summary`
- Produces:
  - `GET /v1/subscriptions/me` → `{status, planCode, currentPeriodEnd, nextBillingAt, scheduledPlanCode, card, expiring: {credits, expiresAt}, graceUntil}` (구독 없으면 `{"status": "none"}`)
  - `POST /v1/subscriptions/cancel` → `{status: "canceled", accessUntil, expiring: {credits, expiresAt}}`
  - `POST /v1/subscriptions/resume` → `{status: "active", nextBillingAt}`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""해지·철회 — '언제까지 쓰고 무엇이 사라지는가' 계약.

해지는 즉시 차단이 아니다(결제한 주기 끝까지). 그리고 소멸 예정 수량·날짜를
응답에 반드시 담는다 — 이월분까지 한 번에 사라지므로 화면이 숫자를 보여줘야
환불 분쟁이 안 난다(계획서 §0.1).
"""

import contextlib

import pytest
from fastapi.testclient import TestClient

import app.subscriptions as subs
from app.main import create_app
from conftest import make_settings

ACTIVE = {"id": "sub-1", "user_id": "user-1", "plan_code": "seller", "status": "active",
          "current_period_end": "2026-10-09T00:00:00+00:00",
          "next_billing_at": "2026-10-09T00:00:00+00:00", "scheduled_plan_code": None,
          "card_brand": "현대", "card_last4": "1234", "grace_until": None}


class _Cur:
    def __init__(self, state):
        self.s = state
        self._row = None

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.s["sql"].append(q)
        if "from subscriptions" in q:
            self._row = self.s["sub"]
        elif "update subscriptions set status = 'canceled'" in q:
            if self.s["sub"] and self.s["sub"]["status"] in ("active", "past_due"):
                self.s["sub"] = {**self.s["sub"], "status": "canceled", "next_billing_at": None}
                self._row = self.s["sub"]
            else:
                self._row = None
        elif "update subscriptions set status = 'active'" in q:
            if self.s["sub"] and self.s["sub"]["status"] == "canceled":
                self.s["sub"] = {**self.s["sub"], "status": "active",
                                 "next_billing_at": self.s["sub"]["current_period_end"]}
                self._row = self.s["sub"]
            else:
                self._row = None
        else:
            self._row = None

    async def fetchone(self):
        return self._row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, state):
        self.s = state

    def cursor(self):
        return _Cur(self.s)

    async def commit(self):
        self.s["commits"] += 1


@pytest.fixture()
def sub(monkeypatch, keypair):
    _, public_key = keypair
    state = {"sql": [], "sub": dict(ACTIVE), "commits": 0}

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn(state)

    async def fake_summary(conn, user_id):
        return {"credits": 24000, "expiresAt": "2026-10-09T00:00:00+00:00"}

    monkeypatch.setattr(subs, "get_conn", fake_conn)
    monkeypatch.setattr(subs.repo, "subscription_bucket_summary", fake_summary)
    app = create_app(make_settings(toss_secret_key="sk", toss_billing_kek="kek",
                                   subscription_billing_enabled=True))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app), state


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token(sub='user-1')}"}


def test_cancel_keeps_access_until_period_end(sub, make_token):
    client, state = sub
    res = client.post("/v1/subscriptions/cancel", headers=_auth(make_token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "canceled"
    assert body["accessUntil"] == ACTIVE["current_period_end"]
    assert state["sub"]["next_billing_at"] is None       # 다음 청구는 걸리지 않는다


def test_cancel_reports_what_will_be_lost(sub, make_token):
    """이월분 포함 소멸 예정 수량·날짜를 숫자로 돌려준다."""
    client, _ = sub
    body = client.post("/v1/subscriptions/cancel", headers=_auth(make_token)).json()
    assert body["expiring"]["credits"] == 24000
    assert body["expiring"]["expiresAt"] == ACTIVE["current_period_end"]


def test_cancel_twice_is_409(sub, make_token):
    client, state = sub
    state["sub"] = {**ACTIVE, "status": "canceled", "next_billing_at": None}
    res = client.post("/v1/subscriptions/cancel", headers=_auth(make_token))
    assert res.status_code == 409


def test_resume_restores_billing_schedule(sub, make_token):
    client, state = sub
    state["sub"] = {**ACTIVE, "status": "canceled", "next_billing_at": None}
    res = client.post("/v1/subscriptions/resume", headers=_auth(make_token))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "active"
    assert state["sub"]["next_billing_at"] == ACTIVE["current_period_end"]


def test_me_without_subscription_is_none_not_404(sub, make_token):
    """구독이 없는 것은 오류가 아니다. 화면이 분기 없이 렌더할 수 있어야 한다."""
    client, state = sub
    state["sub"] = None
    res = client.get("/v1/subscriptions/me", headers=_auth(make_token))
    assert res.status_code == 200
    assert res.json() == {"status": "none"}


def test_me_never_leaks_billing_key(sub, make_token):
    client, _ = sub
    body = client.get("/v1/subscriptions/me", headers=_auth(make_token)).text
    assert "billing" not in body.lower() or "billingKey" not in body
    assert "bk-" not in body
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd server && python -m pytest tests/test_subscriptions_cancel.py -v`
Expected: FAIL — 404 (라우트 없음)

- [ ] **Step 3: 라우트 3개 추가**

`server/app/subscriptions.py` 끝에 추가:

```python
_ME_COLUMNS = (
    "id::text as id, plan_code, status, current_period_end, next_billing_at, "
    "scheduled_plan_code, card_brand, card_last4, grace_until, billing_key_invalid"
)


@router.get("/me", summary="내 구독 상태")
async def get_my_subscription(request: Request, user_id: str = Depends(require_user)):
    """구독이 없으면 `{"status": "none"}`. 404 가 아니다 — 화면이 분기 없이 렌더한다.

    빌링키는 어떤 경우에도 응답에 넣지 않는다(카드사·끝 4자리만 표시용).
    """
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"select {_ME_COLUMNS} from subscriptions where user_id = %s", (user_id,)
            )
            sub = await cur.fetchone()
            if sub is None or sub["status"] == "ended":
                return JSONResponse({"status": "none"})
        expiring = await repo.subscription_bucket_summary(conn, user_id)
    return JSONResponse({
        "status": sub["status"],
        "planCode": sub["plan_code"],
        "currentPeriodEnd": str(sub["current_period_end"]),
        "nextBillingAt": str(sub["next_billing_at"]) if sub["next_billing_at"] else None,
        "scheduledPlanCode": sub["scheduled_plan_code"],
        "graceUntil": str(sub["grace_until"]) if sub["grace_until"] else None,
        "cardNeedsUpdate": bool(sub["billing_key_invalid"]),
        "card": {"brand": sub["card_brand"], "last4": sub["card_last4"]},
        "expiring": {"credits": expiring["credits"],
                     "expiresAt": str(expiring["expiresAt"]) if expiring["expiresAt"] else None},
    })


@router.post("/cancel", summary="구독 해지 예약")
async def cancel_subscription(request: Request, user_id: str = Depends(require_user)):
    """즉시 차단이 아니다 — `current_period_end` 까지 그대로 쓰고 그때 크레딧이 소멸한다.

    응답의 `expiring` 은 **이월분을 포함한** 소멸 예정 수량이다. 화면은 이 숫자를
    확인 모달에 반드시 노출한다(계획서 §0.1 — 큰 금액이 한 번에 사라지는 사건이다).
    """
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            # 조건부 UPDATE 로 상태 전이와 판정을 한 문장에 담는다 — 읽고 나서 쓰면
            # 두 탭이 동시에 해지를 눌렀을 때 늦은 쪽이 이미 해지된 구독을 또 해지한다.
            await cur.execute(
                "update subscriptions set status = 'canceled', next_billing_at = null, "
                "canceled_at = now() where user_id = %s and status in ('active', 'past_due') "
                f"returning {_ME_COLUMNS}",
                (user_id,),
            )
            sub = await cur.fetchone()
            if sub is None:
                raise _err("not_cancelable", "해지할 수 있는 구독이 없어요.", 409)
        expiring = await repo.subscription_bucket_summary(conn, user_id)
        await conn.commit()
    return JSONResponse({
        "status": "canceled",
        "accessUntil": str(sub["current_period_end"]),
        "expiring": {"credits": expiring["credits"],
                     "expiresAt": str(expiring["expiresAt"]) if expiring["expiresAt"] else None},
    })


@router.post("/resume", summary="해지 철회")
async def resume_subscription(request: Request, user_id: str = Depends(require_user)):
    """주기 종료 전이면 되돌릴 수 있다. 다음 청구를 `current_period_end` 로 되살린다."""
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "update subscriptions set status = 'active', canceled_at = null, "
                "next_billing_at = current_period_end "
                "where user_id = %s and status = 'canceled' and current_period_end > now() "
                f"returning {_ME_COLUMNS}",
                (user_id,),
            )
            sub = await cur.fetchone()
            if sub is None:
                raise _err("not_resumable", "되돌릴 수 있는 구독이 없어요.", 409)
        await conn.commit()
    return JSONResponse({"status": "active", "nextBillingAt": str(sub["next_billing_at"])})
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `cd server && python -m pytest tests/test_subscriptions_cancel.py -v`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add server/app/subscriptions.py server/tests/test_subscriptions_cancel.py
git commit -m "feat(subscription): 조회·예약해지·철회 — 소멸 예정 수량을 응답에 담는다"
```

---

## Task 6: 등급 변경 (업그레이드 즉시 / 다운그레이드 예약)

**Files:**
- Modify: `server/app/subscriptions.py`
- Test: `server/tests/test_subscriptions_change_plan.py`

**Interfaces:**
- Consumes: `toss_billing.charge`, `repo.grant_subscription`
- Produces:
  - `_proration(old_price, new_price, old_credits, new_credits, remaining_days, period_days) -> {"amount": int, "credits": int}`
  - `POST /v1/subscriptions/change-plan` — body `{planCode}` → 업그레이드 `{applied:"immediate", charged, credits, available}` / 다운그레이드 `{applied:"next_period", scheduledPlanCode, effectiveAt}`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""등급 변경 — 비례배분 산식과 적용 시점.

업그레이드는 즉시(차액 결제 + 비례 크레딧), 다운그레이드는 다음 주기(계획서 §0.1·§0.2).
산식을 순수 함수로 분리해 경계값(반올림·1일 남음·같은 요금제)을 DB 없이 고정한다.
"""

import contextlib

import pytest
from fastapi.testclient import TestClient

import app.subscriptions as subs
from app.main import create_app
from conftest import make_settings

PLANS = {
    "starter": {"id": "p1", "code": "starter", "name": "Starter", "credits": 600, "price": 29900},
    "seller": {"id": "p2", "code": "seller", "name": "Seller", "credits": 1800, "price": 79900},
    "pro": {"id": "p3", "code": "pro", "name": "Pro", "credits": 3800, "price": 159000},
}


def test_proration_is_floored_on_both_money_and_credits():
    out = subs._proration(old_price=29900, new_price=79900,
                          old_credits=600, new_credits=1800,
                          remaining_days=15, period_days=30)
    assert out["amount"] == 25000        # floor(50000 * 15/30)
    assert out["credits"] == 600        # floor(1200 * 15/30)


def test_proration_on_last_day_is_tiny_but_not_negative():
    out = subs._proration(old_price=29900, new_price=79900,
                          old_credits=600, new_credits=1800,
                          remaining_days=1, period_days=30)
    assert out["amount"] == 1666         # floor(50000/30)
    assert out["credits"] == 40
    assert out["amount"] > 0


def test_proration_for_cheaper_plan_is_not_an_upgrade():
    out = subs._proration(old_price=79900, new_price=29900,
                          old_credits=1800, new_credits=600,
                          remaining_days=15, period_days=30)
    assert out["amount"] <= 0            # 라우트가 이걸 보고 다운그레이드로 라우팅한다


class _Cur:
    def __init__(self, state):
        self.s = state
        self._row = None

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.s["sql"].append(q)
        if "from pricing_plans" in q:
            self._row = PLANS.get(params[0])
        elif "from subscriptions" in q:
            self._row = self.s["sub"]
        elif "update subscriptions set plan_code" in q:
            self.s["sub"] = {**self.s["sub"], "plan_code": params[0]}
            self._row = self.s["sub"]
        elif "update subscriptions set scheduled_plan_code" in q:
            self.s["sub"] = {**self.s["sub"], "scheduled_plan_code": params[0]}
            self._row = self.s["sub"]
        elif "insert into subscription_invoices" in q:
            self.s["invoices"].append(params)
            self._row = {"id": "inv-2"}
        elif "select pgp_sym_decrypt" in q:
            self._row = {"billing_key": "bk-1"}
        elif "remaining_days" in q:
            self._row = {"remaining_days": 15, "period_days": 30}
        else:
            self._row = None

    async def fetchone(self):
        return self._row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, state):
        self.s = state

    def cursor(self):
        return _Cur(self.s)

    async def commit(self):
        self.s["commits"] += 1

    async def rollback(self):
        self.s["rollbacks"] += 1


@pytest.fixture()
def sub(monkeypatch, keypair):
    _, public_key = keypair
    state = {"sql": [], "commits": 0, "rollbacks": 0, "invoices": [], "charged": [],
             "grants": [], "balance": 1000,
             "sub": {"id": "sub-1", "user_id": "user-1", "plan_code": "starter",
                     "status": "active", "current_period_end": "2026-10-09T00:00:00+00:00",
                     "scheduled_plan_code": None}}

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn(state)

    async def fake_charge(settings, *, billing_key, customer_key, order_id, order_name, amount):
        state["charged"].append({"amount": amount, "orderId": order_id})
        return {"status": "DONE", "totalAmount": amount, "paymentKey": "pk-2"}

    async def fake_grant(conn, *, user_id, plan_code, metadata=None, credits=None,
                         period_end_sql=None):
        state["grants"].append({"credits": credits, "period_end_sql": period_end_sql})
        state["balance"] += credits
        return {"creditSourceId": "src-2", "credits": credits, "available": state["balance"]}

    monkeypatch.setattr(subs, "get_conn", fake_conn)
    monkeypatch.setattr(subs.toss_billing, "charge", fake_charge)
    monkeypatch.setattr(subs.repo, "grant_subscription", fake_grant)
    app = create_app(make_settings(toss_secret_key="sk", toss_billing_kek="kek",
                                   subscription_billing_enabled=True))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app), state


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token(sub='user-1')}"}


def test_upgrade_charges_difference_and_grants_now(sub, make_token):
    client, state = sub
    res = client.post("/v1/subscriptions/change-plan", headers=_auth(make_token),
                      json={"planCode": "seller"})
    assert res.status_code == 200, res.text
    assert res.json()["applied"] == "immediate"
    assert state["charged"][0]["amount"] == 25000
    assert state["grants"][0]["credits"] == 600
    assert state["sub"]["plan_code"] == "seller"


def test_upgrade_bucket_expires_with_current_period_not_a_new_month(sub, make_token):
    """비례 지급분은 현재 주기 끝에 맞춘다 — 한 달을 새로 주면 주기가 어긋난다."""
    client, state = sub
    client.post("/v1/subscriptions/change-plan", headers=_auth(make_token),
                json={"planCode": "seller"})
    assert "current_period_end" in state["grants"][0]["period_end_sql"]


def test_downgrade_is_scheduled_not_charged(sub, make_token):
    client, state = sub
    state["sub"] = {**state["sub"], "plan_code": "pro"}
    res = client.post("/v1/subscriptions/change-plan", headers=_auth(make_token),
                      json={"planCode": "starter"})
    assert res.status_code == 200, res.text
    assert res.json()["applied"] == "next_period"
    assert state["charged"] == []
    assert state["grants"] == []
    assert state["sub"]["scheduled_plan_code"] == "starter"


def test_same_plan_is_400(sub, make_token):
    client, _ = sub
    res = client.post("/v1/subscriptions/change-plan", headers=_auth(make_token),
                      json={"planCode": "starter"})
    assert res.status_code == 400


def test_change_plan_while_past_due_is_409(sub, make_token):
    """미납 상태에서 등급을 올리면 못 받은 돈 위에 크레딧을 더 준다."""
    client, state = sub
    state["sub"] = {**state["sub"], "status": "past_due"}
    res = client.post("/v1/subscriptions/change-plan", headers=_auth(make_token),
                      json={"planCode": "seller"})
    assert res.status_code == 409
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd server && python -m pytest tests/test_subscriptions_change_plan.py -v`
Expected: FAIL — `AttributeError: module 'app.subscriptions' has no attribute '_proration'`

- [ ] **Step 3: 산식 + 라우트 구현**

`server/app/subscriptions.py` 에 추가:

```python
class ChangePlanBody(BaseModel):
    plan_code: str = Field(alias="planCode", min_length=1, max_length=64)

    model_config = {"populate_by_name": True}


def _proration(*, old_price: int, new_price: int, old_credits: int, new_credits: int,
               remaining_days: int, period_days: int) -> dict:
    """업그레이드 비례배분(계획서 §0.2). 금액·크레딧 모두 **버림**.

    버림으로 통일한 이유: 금액을 올림하면 사용자가 더 내고, 크레딧을 올림하면 우리가
    더 준다. 둘 다 버림이면 오차가 항상 사용자 쪽으로 1원·1크레딧 유리하게 떨어진다.
    """
    period_days = max(int(period_days), 1)
    remaining_days = max(min(int(remaining_days), period_days), 0)
    return {
        "amount": (new_price - old_price) * remaining_days // period_days,
        "credits": max((new_credits - old_credits) * remaining_days // period_days, 0),
    }


@router.post("/change-plan", summary="요금제 변경")
async def change_plan(
    request: Request, body: ChangePlanBody, user_id: str = Depends(require_user),
):
    """업그레이드는 즉시(비례 차액 결제 + 비례 크레딧), 다운그레이드는 다음 주기.

    - **Bearer Token**: 필수
    - **에지 케이스**: `400 same_plan` · `404 unknown_plan` · `409 subscription_not_active`
      (미납·해지 상태에서는 변경 불가 — 못 받은 돈 위에 크레딧을 얹지 않는다) · `402`(카드 거절)
    """
    kek = _require_billing_config(request)
    settings = _settings(request)
    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select id::text as id, plan_code, status, current_period_end "
                "from subscriptions where user_id = %s for update",
                (user_id,),
            )
            sub = await cur.fetchone()
            if sub is None:
                raise _err("subscription_not_found", "구독이 없어요.", 404)
            if sub["status"] != "active":
                raise _err("subscription_not_active", "지금은 요금제를 바꿀 수 없어요.", 409)
            if sub["plan_code"] == body.plan_code:
                raise _err("same_plan", "이미 사용 중인 요금제예요.", 400)
            new_plan = await _load_plan(cur, body.plan_code)
            old_plan = await _load_plan(cur, sub["plan_code"])
            # 남은 일수는 DB 가 계산한다 — 파이썬에서 만들면 시간대가 섞인다.
            await cur.execute(
                "select greatest(ceil(extract(epoch from (current_period_end - now())) / 86400), 1)"
                "::int as remaining_days, "
                "greatest(ceil(extract(epoch from (current_period_end - current_period_start))"
                " / 86400), 1)::int as period_days "
                "from subscriptions where user_id = %s",
                (user_id,),
            )
            span = await cur.fetchone()

        prorated = _proration(
            old_price=old_plan["price"], new_price=new_plan["price"],
            old_credits=old_plan["credits"], new_credits=new_plan["credits"],
            remaining_days=span["remaining_days"], period_days=span["period_days"])

        if prorated["amount"] <= 0:
            # 다운그레이드 — 이번 주기는 이미 비싼 요금제로 결제됐으므로 건드리지 않는다.
            async with conn.cursor() as cur:
                await cur.execute(
                    "update subscriptions set scheduled_plan_code = %s where user_id = %s",
                    (new_plan["code"], user_id),
                )
            await conn.commit()
            return JSONResponse({
                "applied": "next_period",
                "scheduledPlanCode": new_plan["code"],
                "effectiveAt": str(sub["current_period_end"]),
            })

        order_id = _new_order_id("up")
        async with conn.cursor() as cur:
            await cur.execute(
                "select pgp_sym_decrypt(billing_key_enc, %s)::text as billing_key "
                "from subscriptions where user_id = %s",
                (kek, user_id),
            )
            billing_key = (await cur.fetchone())["billing_key"]
            await cur.execute(
                "insert into subscription_invoices (subscription_id, user_id, order_id, kind, "
                "plan_code, amount, credits, period_start, period_end) "
                "select %s, %s, %s, 'upgrade_proration', %s, %s, %s, now(), current_period_end "
                "from subscriptions where user_id = %s",
                (sub["id"], user_id, order_id, new_plan["code"], prorated["amount"],
                 prorated["credits"], user_id),
            )
        try:
            charged = await toss_billing.charge(
                settings, billing_key=billing_key, customer_key=user_id, order_id=order_id,
                order_name=f"{new_plan['name']} 업그레이드", amount=prorated["amount"])
        except toss_billing.TossBillingError as e:
            await conn.rollback()
            raise _err(e.code, e.message, 503 if e.retryable else 402)

        async with conn.cursor() as cur:
            await cur.execute(
                "update subscription_invoices set status = 'paid', payment_key = %s, "
                "approved_at = now() where order_id = %s",
                (charged.get("paymentKey"), order_id),
            )
            await cur.execute(
                "update subscriptions set plan_code = %s, scheduled_plan_code = null "
                "where user_id = %s",
                (new_plan["code"], user_id),
            )
            await cur.execute(
                "update profiles set plan = %s where user_id = %s", (new_plan["code"], user_id)
            )
        try:
            granted = await repo.grant_subscription(
                conn, user_id=user_id, plan_code=new_plan["code"], credits=prorated["credits"],
                metadata={"orderId": order_id, "kind": "upgrade_proration"},
                # 비례분은 현재 주기 끝에 맞춘다 — 한 달을 새로 주면 주기가 어긋난다.
                period_end_sql=("(select current_period_end from subscriptions "
                                "where user_id = credit_sources.user_id)"))
        except repo.CreditError as e:
            await conn.rollback()
            raise _err(e.code, e.message, e.status)
        await conn.commit()

    return JSONResponse({
        "applied": "immediate",
        "planCode": new_plan["code"],
        "charged": prorated["amount"],
        "credits": granted["credits"],
        "available": granted["available"],
    })
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `cd server && python -m pytest tests/test_subscriptions_change_plan.py -v`
Expected: 8 passed

- [ ] **Step 5: 커밋**

```bash
git add server/app/subscriptions.py server/tests/test_subscriptions_change_plan.py
git commit -m "feat(subscription): 요금제 변경 — 업그레이드 즉시 비례결제, 다운그레이드 예약"
```

---

## Task 7: 카드 교체 + 웹훅

**Files:**
- Modify: `server/app/subscriptions.py`
- Test: `server/tests/test_subscriptions_card_webhook.py`

**Interfaces:**
- Produces:
  - `PUT /v1/subscriptions/card` — body `{authKey, customerKey}` → `{card: {brand, last4}}`
  - `POST /v1/webhooks/toss/{secret}` (인증 없음) → 항상 `200 {"ok": true}`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""카드 교체 · 웹훅 — 신뢰 경계 테스트.

웹훅은 서명이 없다(토스 일반 웹훅 계열). 그래서 ① 경로 시크릿이 틀리면 404,
② 맞아도 본문으로 상태를 확정하지 않고 '카드 재등록 필요' 표시만 남긴다.
"""

import contextlib

import pytest
from fastapi.testclient import TestClient

import app.subscriptions as subs
from app.main import create_app
from conftest import make_settings

WEBHOOK_SECRET = "whs-abc"


class _Cur:
    def __init__(self, state):
        self.s = state
        self._row = None

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.s["sql"].append(q)
        if "select pgp_sym_decrypt" in q:
            self._row = {"billing_key": "bk-old"}
        elif "update subscriptions set billing_key_enc" in q:
            self.s["card"] = {"brand": params[2], "last4": params[3]}
            self._row = {"id": "sub-1"}
        elif "update subscriptions set billing_key_invalid" in q:
            self.s["invalidated"].append(params)
            self._row = {"id": "sub-1"}
        elif "from subscriptions" in q:
            self._row = self.s["sub"]
        else:
            self._row = None

    async def fetchone(self):
        return self._row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, state):
        self.s = state

    def cursor(self):
        return _Cur(self.s)

    async def commit(self):
        self.s["commits"] += 1


@pytest.fixture()
def sub(monkeypatch, keypair):
    _, public_key = keypair
    state = {"sql": [], "commits": 0, "issued": [], "deleted": [], "invalidated": [],
             "card": None, "sub": {"id": "sub-1", "user_id": "user-1", "status": "active"}}

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn(state)

    async def fake_issue(settings, *, auth_key, customer_key):
        state["issued"].append(customer_key)
        return {"billingKey": "bk-new", "cardBrand": "신한", "cardLast4": "9876"}

    async def fake_delete(settings, *, billing_key):
        state["deleted"].append(billing_key)

    monkeypatch.setattr(subs, "get_conn", fake_conn)
    monkeypatch.setattr(subs.toss_billing, "issue_billing_key", fake_issue)
    monkeypatch.setattr(subs.toss_billing, "delete_billing_key", fake_delete)
    app = create_app(make_settings(toss_secret_key="sk", toss_billing_kek="kek",
                                   toss_webhook_path_secret=WEBHOOK_SECRET,
                                   subscription_billing_enabled=True))
    app.state.jwt_key_resolver = lambda token: public_key
    return TestClient(app), state


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token(sub='user-1')}"}


def test_card_swap_replaces_key_and_deletes_old_one(sub, make_token):
    client, state = sub
    res = client.put("/v1/subscriptions/card", headers=_auth(make_token),
                     json={"authKey": "ak-2", "customerKey": "user-1"})
    assert res.status_code == 200, res.text
    assert res.json()["card"] == {"brand": "신한", "last4": "9876"}
    assert state["deleted"] == ["bk-old"]


def test_card_swap_clears_invalid_flag(sub, make_token):
    client, state = sub
    client.put("/v1/subscriptions/card", headers=_auth(make_token),
               json={"authKey": "ak-2", "customerKey": "user-1"})
    assert any("billing_key_invalid = false" in q for q in state["sql"])


def test_webhook_with_wrong_secret_is_404(sub):
    client, state = sub
    res = client.post("/v1/webhooks/toss/wrong",
                      json={"eventType": "BILLING_DELETED", "data": {"billingKey": "bk-old"}})
    assert res.status_code == 404
    assert state["invalidated"] == []


def test_webhook_marks_card_needs_update_but_does_not_cancel(sub):
    """서명이 없으므로 웹훅은 힌트다. 구독을 끊지 않는다 — 끊으면 위조 요청 하나로
    남의 구독을 종료시킬 수 있다."""
    client, state = sub
    res = client.post(f"/v1/webhooks/toss/{WEBHOOK_SECRET}",
                      json={"eventType": "BILLING_DELETED", "data": {"billingKey": "bk-old"}})
    assert res.status_code == 200
    assert len(state["invalidated"]) == 1
    assert not any("status = 'ended'" in q for q in state["sql"])


def test_unknown_event_type_is_accepted_and_ignored(sub):
    client, state = sub
    res = client.post(f"/v1/webhooks/toss/{WEBHOOK_SECRET}",
                      json={"eventType": "PAYMENT_STATUS_CHANGED", "data": {}})
    assert res.status_code == 200        # 재전송 폭주를 부르지 않게 항상 200
    assert state["invalidated"] == []
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd server && python -m pytest tests/test_subscriptions_card_webhook.py -v`
Expected: FAIL — 404/405 (라우트 없음)

- [ ] **Step 3: 구현**

`server/app/subscriptions.py` 에 추가:

```python
class CardBody(BaseModel):
    auth_key: str = Field(alias="authKey", min_length=1, max_length=300)
    customer_key: str = Field(alias="customerKey", min_length=2, max_length=300)

    model_config = {"populate_by_name": True}


@router.put("/card", summary="결제 카드 교체")
async def replace_card(
    request: Request, body: CardBody, user_id: str = Depends(require_user),
):
    """새 빌링키를 발급해 갈아끼우고 옛 키는 토스에서 삭제한다.

    빌링키에는 갱신 개념이 없다 — 카드가 바뀌거나 유효기간이 지나면 **재발급만이 답**이다.
    """
    kek = _require_billing_config(request)
    settings = _settings(request)
    if body.customer_key != user_id:
        raise _err("customer_key_mismatch", "결제 요청 정보가 계정과 달라요.", 403)

    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select pgp_sym_decrypt(billing_key_enc, %s)::text as billing_key "
                "from subscriptions where user_id = %s and status <> 'ended'",
                (kek, user_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise _err("subscription_not_found", "구독이 없어요.", 404)
            old_key = row["billing_key"]

        try:
            issued = await toss_billing.issue_billing_key(
                settings, auth_key=body.auth_key, customer_key=user_id)
        except toss_billing.TossBillingError as e:
            raise _err(e.code, e.message, 503 if e.retryable else 402)

        async with conn.cursor() as cur:
            await cur.execute(
                "update subscriptions set billing_key_enc = pgp_sym_encrypt(%s, %s), "
                "card_brand = %s, card_last4 = %s, billing_key_invalid = false "
                "where user_id = %s returning id::text as id",
                (issued["billingKey"], kek, issued.get("cardBrand"), issued.get("cardLast4"),
                 user_id),
            )
        await conn.commit()

    # 새 키가 확정 저장된 뒤에 옛 키를 지운다(순서가 반대면 둘 다 잃는다).
    await toss_billing.delete_billing_key(settings, billing_key=old_key)
    return JSONResponse({"card": {"brand": issued.get("cardBrand"),
                                  "last4": issued.get("cardLast4")}})


webhook_router = APIRouter(prefix="/v1/webhooks", tags=["Subscriptions"])


@webhook_router.post("/toss/{secret}", summary="토스 웹훅 수신", include_in_schema=False)
async def toss_webhook(secret: str, request: Request):
    """토스 일반 웹훅에는 서명 헤더가 없다 → **경로 시크릿이 인증 대용**이다.

    그래도 본문은 신뢰하지 않는다. BILLING_DELETED 를 받아도 구독을 끊지 않고
    '카드 재등록 필요' 표시만 남긴다 — 끊는 동작을 두면 위조 요청 하나로 남의
    구독을 종료시킬 수 있다. 진짜로 죽은 키라면 다음 청구가 어차피 실패한다.
    항상 200 을 돌려준다(4xx 를 주면 토스가 재전송을 반복한다).
    """
    configured = _settings(request).toss_webhook_path_secret
    if not configured or not secrets.compare_digest(secret, configured):
        raise HTTPException(status_code=404, detail="not found")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if payload.get("eventType") == "BILLING_DELETED":
        billing_key = str((payload.get("data") or {}).get("billingKey") or "")
        if billing_key:
            async with get_conn(request) as conn:
                async with conn.cursor() as cur:
                    # 빌링키로 직접 찾을 수 없다(암호문 비교 불가) → KEK 로 풀어 비교한다.
                    await cur.execute(
                        "update subscriptions set billing_key_invalid = true "
                        "where pgp_sym_decrypt(billing_key_enc, %s)::text = %s "
                        "and status in ('active', 'past_due') returning id::text as id",
                        (_settings(request).toss_billing_kek, billing_key),
                    )
                    await cur.fetchone()
                await conn.commit()
            log.warning("toss webhook BILLING_DELETED — 카드 재등록 필요")
    return JSONResponse({"ok": True})
```

`server/app/main.py` 의 구독 라우터 등록 블록에 웹훅 라우터도 추가:

```python
        from .subscriptions import router as subscriptions_router
        from .subscriptions import webhook_router as toss_webhook_router

        app.include_router(subscriptions_router)
        app.include_router(toss_webhook_router)
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `cd server && python -m pytest tests/test_subscriptions_card_webhook.py -v`
Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
git add server/app/subscriptions.py server/app/main.py server/tests/test_subscriptions_card_webhook.py
git commit -m "feat(subscription): 카드 교체 + 토스 웹훅 — 서명 없는 웹훅은 힌트로만 취급"
```

---

## Task 8: 청구 워커 (갱신 · 3일 유예 재시도)

**Files:**
- Create: `server/app/workers/subscription_biller.py`
- Modify: `server/app/main.py` (워커 기동/정지)
- Test: `server/tests/test_subscription_biller.py`

**Interfaces:**
- Consumes: `toss_billing.charge`, `repo.grant_subscription`
- Produces:
  - `class SubscriptionBiller`: `.start()`, `.stop()`, `.tick(conn) -> dict`
  - `MAX_ATTEMPTS = 3`, `GRACE_DAYS = 3`, `_LOCK_KEY = (0x5542, 1)`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""청구 워커 — 갱신·유예·재시도 상태기계.

여기서 지키는 것: ① 성공하면 이월 지급 + 주기 이동 ② 확정 거절은 past_due 로
내리되 크레딧은 그대로 둔다(유예 중 사용 가능) ③ 3회까지만 재시도한다
④ 결과 미상은 실패로 세지 않는다(fail_count 를 올리지 않는다).
"""

import pytest

import app.toss_billing as tb
from app.workers.subscription_biller import GRACE_DAYS, MAX_ATTEMPTS, SubscriptionBiller
from conftest import make_settings


class _Cur:
    def __init__(self, state):
        self.s = state
        self._rows = []

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.s["sql"].append(q)
        if "from subscriptions" in q and "for update skip locked" in q:
            self._rows = list(self.s["due"])
        elif "from pricing_plans" in q:
            self._rows = [{"id": "p2", "code": "seller", "name": "Seller",
                           "credits": 1800, "price": 79900}]
        elif "insert into subscription_invoices" in q:
            self.s["invoices"].append(params)
            self._rows = [{"id": "inv-1"}]
        elif "update subscription_invoices" in q:
            self.s["invoice_updates"].append(q)
            self._rows = []
        elif "update subscriptions set" in q:
            self.s["sub_updates"].append({"sql": q, "params": params})
            self._rows = []
        elif "pg_try_advisory_lock" in q:
            self._rows = [{"locked": self.s["lock"]}]
        else:
            self._rows = []

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, state):
        self.s = state

    def cursor(self):
        return _Cur(self.s)

    async def commit(self):
        self.s["commits"] += 1

    async def rollback(self):
        self.s["rollbacks"] += 1


def _app(state, monkeypatch, *, charge_error=None):
    import app.workers.subscription_biller as biller

    async def fake_charge(settings, *, billing_key, customer_key, order_id, order_name, amount):
        state["charged"].append({"orderId": order_id, "amount": amount})
        if charge_error is not None:
            raise charge_error
        return {"status": "DONE", "totalAmount": amount, "paymentKey": "pk-r"}

    async def fake_grant(conn, *, user_id, plan_code, metadata=None, credits=None,
                         period_end_sql=None):
        state["grants"].append({"user_id": user_id, "plan_code": plan_code})
        return {"creditSourceId": "s", "credits": 1800, "available": 1800}

    monkeypatch.setattr(biller.toss_billing, "charge", fake_charge)
    monkeypatch.setattr(biller.repo, "grant_subscription", fake_grant)

    class _App:
        pass

    app = _App()
    app.state = _App()
    app.state.settings = make_settings(toss_secret_key="sk", toss_billing_kek="kek",
                                       subscription_billing_enabled=True)
    return app


@pytest.fixture()
def state():
    return {"sql": [], "due": [{"id": "sub-1", "user_id": "u1", "plan_code": "seller",
                                "status": "active", "fail_count": 0, "billing_key": "bk-1",
                                "scheduled_plan_code": None}],
            "invoices": [], "invoice_updates": [], "sub_updates": [], "charged": [],
            "grants": [], "commits": 0, "rollbacks": 0, "lock": True}


@pytest.mark.asyncio
async def test_successful_renewal_grants_and_advances_period(state, monkeypatch):
    app = _app(state, monkeypatch)
    out = await SubscriptionBiller(app).tick(_Conn(state))
    assert out["charged"] == 1
    assert state["grants"][0]["plan_code"] == "seller"
    advance = [u for u in state["sub_updates"] if "current_period_start = current_period_end" in u["sql"]]
    assert len(advance) == 1
    assert "fail_count = 0" in advance[0]["sql"]


@pytest.mark.asyncio
async def test_scheduled_downgrade_applies_on_renewal(state, monkeypatch):
    state["due"][0]["scheduled_plan_code"] = "starter"
    app = _app(state, monkeypatch)
    await SubscriptionBiller(app).tick(_Conn(state))
    assert any("scheduled_plan_code = null" in u["sql"] for u in state["sub_updates"])
    assert state["grants"][0]["plan_code"] == "starter"


@pytest.mark.asyncio
async def test_card_rejection_moves_to_past_due_without_touching_credits(state, monkeypatch):
    app = _app(state, monkeypatch,
               charge_error=tb.TossBillingError("REJECT_CARD_COMPANY", "잔액부족",
                                                retryable=False))
    out = await SubscriptionBiller(app).tick(_Conn(state))
    assert out["failed"] == 1
    past_due = [u for u in state["sub_updates"] if "status = 'past_due'" in u["sql"]]
    assert len(past_due) == 1
    assert "grace_until" in past_due[0]["sql"]
    assert state["grants"] == []
    # 유예 중 크레딧은 그대로 쓴다 — 소멸 경로를 부르지 않는다.
    assert not any("expire" in q for q in state["sql"])


@pytest.mark.asyncio
async def test_retry_stops_after_max_attempts(state, monkeypatch):
    state["due"][0].update(status="past_due", fail_count=MAX_ATTEMPTS - 1)
    app = _app(state, monkeypatch,
               charge_error=tb.TossBillingError("REJECT_CARD_COMPANY", "잔액부족",
                                                retryable=False))
    await SubscriptionBiller(app).tick(_Conn(state))
    # 마지막 시도가 실패하면 다음 청구를 걸지 않는다(만료 워커가 정리한다).
    assert any("next_billing_at = null" in u["sql"] for u in state["sub_updates"])


@pytest.mark.asyncio
async def test_unknown_result_does_not_count_as_failure(state, monkeypatch):
    """5xx·전송실패는 승인 여부 미상이다. fail_count 를 올리면 카드가 멀쩡한 사람이
    통신 장애 3번으로 해지된다."""
    app = _app(state, monkeypatch,
               charge_error=tb.TossBillingError("payment_gateway_unreachable", "지연",
                                                retryable=True))
    out = await SubscriptionBiller(app).tick(_Conn(state))
    assert out["deferred"] == 1
    assert not any("status = 'past_due'" in u["sql"] for u in state["sub_updates"])
    assert state["rollbacks"] >= 1


@pytest.mark.asyncio
async def test_tick_is_noop_without_advisory_lock(state, monkeypatch):
    """여러 태스크가 동시에 돌아도 청구는 한 번만."""
    state["lock"] = False
    app = _app(state, monkeypatch)
    out = await SubscriptionBiller(app).tick(_Conn(state))
    assert out == {"skipped": "locked"}
    assert state["charged"] == []


def test_grace_is_three_days_and_three_attempts():
    assert GRACE_DAYS == 3
    assert MAX_ATTEMPTS == 3
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd server && python -m pytest tests/test_subscription_biller.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.workers.subscription_biller'`

- [ ] **Step 3: 워커 구현**

`server/app/workers/subscription_biller.py`:

```python
"""구독 주기 청구 — 갱신·유예 재시도(계획서 §0.1).

토스는 스케줄링을 제공하지 않는다. 주기가 된 구독을 우리가 찾아 승인 API 를 부른다.

**상태기계**
  active   ─ 성공 ─▶ active (주기 +1달, 크레딧 이월 지급, fail_count=0)
  active   ─ 확정거절 ─▶ past_due (grace_until = now + 3일, next_billing_at = +1일)
  past_due ─ 성공 ─▶ active (밀린 주기부터 다시 시작)
  past_due ─ 확정거절 ─▶ fail_count+1. 3회째면 next_billing_at = null
                          → 만료 워커가 grace_until 도달 시 정리한다
  (결과 미상)─▶ 아무 상태도 바꾸지 않는다. 다음 tick 이 같은 멱등키로 재시도한다.

기존 워커(FaceVcRevocationReconciler)와 같은 형태 — 클래스 + asyncio 태스크.
다중 ECS 태스크 중복 실행은 pg_try_advisory_lock 으로 막는다.
"""

import asyncio
import contextlib
import logging
import secrets

from .. import repo, toss_billing
from ..db import get_worker_conn

log = logging.getLogger("wearless.subscription_biller")

#: 유예 3일 · 하루 1회 재시도(D+1·D+2·D+3) — 계획서 §0.1
GRACE_DAYS = 3
MAX_ATTEMPTS = 3
#: 청구 tick 간격. 주기가 '하루 단위'라 분 단위 정밀도는 필요 없다.
_IDLE_SECONDS = 300
_STOP_TIMEOUT_SECONDS = 90          # 진행 중인 승인(최대 60초)이 끝날 시간을 준다
#: 전역 단일 러너 락. (classid, objid) — 다른 advisory 락과 겹치지 않는 값.
_LOCK_KEY = (0x5542, 1)
_BATCH = 50


def _new_order_id() -> str:
    return f"wl-sub-{secrets.token_urlsafe(18)}"[:64]


class SubscriptionBiller:
    def __init__(self, app):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="subscription-biller")

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._task, timeout=_STOP_TIMEOUT_SECONDS)
            self._task = None

    async def _run(self):
        while not self._stop.is_set():
            try:
                async with get_worker_conn(self.app) as conn:
                    await self.tick(conn)
            except Exception:
                log.exception("subscription biller tick failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=_IDLE_SECONDS)

    async def tick(self, conn) -> dict:
        settings = self.app.state.settings
        async with conn.cursor() as cur:
            await cur.execute(
                "select pg_try_advisory_lock(%s, %s) as locked", _LOCK_KEY
            )
            if not (await cur.fetchone())["locked"]:
                return {"skipped": "locked"}

        stats = {"charged": 0, "failed": 0, "deferred": 0}
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select id::text as id, user_id::text as user_id, plan_code, status, "
                    "fail_count, scheduled_plan_code, "
                    "pgp_sym_decrypt(billing_key_enc, %s)::text as billing_key "
                    "from subscriptions "
                    "where status in ('active', 'past_due') and next_billing_at <= now() "
                    "order by next_billing_at limit %s for update skip locked",
                    (settings.toss_billing_kek, _BATCH),
                )
                due = await cur.fetchall()

            for sub in due:
                outcome = await self._charge_one(conn, settings, sub)
                stats[outcome] += 1
        finally:
            async with conn.cursor() as cur:
                await cur.execute("select pg_advisory_unlock(%s, %s)", _LOCK_KEY)
        return stats

    async def _charge_one(self, conn, settings, sub: dict) -> str:
        # 예약된 다운그레이드는 이 갱신부터 적용된다.
        plan_code = sub["scheduled_plan_code"] or sub["plan_code"]
        async with conn.cursor() as cur:
            await cur.execute(
                "select id::text as id, code, name, credits, price from pricing_plans "
                "where code = %s and kind = 'subscription' and is_active",
                (plan_code,),
            )
            plan = await cur.fetchone()
        if plan is None:
            log.error("subscription %s references unknown plan %s", sub["id"], plan_code)
            return "deferred"

        order_id = _new_order_id()
        async with conn.cursor() as cur:
            # 같은 주기를 두 번 청구하면 UNIQUE(subscription_id, period_start) 가 막는다.
            await cur.execute(
                "insert into subscription_invoices (subscription_id, user_id, order_id, kind, "
                "plan_code, amount, credits, period_start, period_end, attempt) "
                "select %s, %s, %s, 'renewal', %s, %s, %s, current_period_end, "
                "current_period_end + interval '1 month', %s "
                "from subscriptions where id = %s "
                "on conflict do nothing returning id::text as id",
                (sub["id"], sub["user_id"], order_id, plan["code"], plan["price"],
                 plan["credits"], sub["fail_count"] + 1, sub["id"]),
            )
            invoice = await cur.fetchone()
        if invoice is None:
            # 이미 이 주기의 청구서가 있다 — 재시도라면 그 order_id 를 그대로 써야
            # 멱등키가 유지된다(다른 키로 재시도하면 이중 결제 위험).
            async with conn.cursor() as cur:
                await cur.execute(
                    "select order_id from subscription_invoices i "
                    "join subscriptions s on s.id = i.subscription_id "
                    "where i.subscription_id = %s and i.kind = 'renewal' "
                    "and i.period_start = s.current_period_end",
                    (sub["id"],),
                )
                row = await cur.fetchone()
            if row is None:
                return "deferred"
            order_id = row["order_id"]

        try:
            charged = await toss_billing.charge(
                settings, billing_key=sub["billing_key"], customer_key=sub["user_id"],
                order_id=order_id, order_name=f"{plan['name']} 구독", amount=plan["price"])
        except toss_billing.TossBillingError as e:
            if e.retryable:
                # 승인 여부 미상 — 아무 상태도 굳히지 않는다. 다음 tick 이 같은 멱등키로 잇는다.
                await conn.rollback()
                log.warning("subscription %s charge deferred code=%s", sub["id"], e.code)
                return "deferred"
            await self._mark_failed(conn, sub, order_id, e)
            await conn.commit()
            return "failed"

        async with conn.cursor() as cur:
            await cur.execute(
                "update subscription_invoices set status = 'paid', payment_key = %s, "
                "approved_at = now() where order_id = %s",
                (charged.get("paymentKey"), order_id),
            )
            await cur.execute(
                "update subscriptions set status = 'active', "
                "plan_code = %s, scheduled_plan_code = null, "
                "current_period_start = current_period_end, "
                "current_period_end = current_period_end + interval '1 month', "
                "next_billing_at = current_period_end + interval '1 month', "
                "fail_count = 0, grace_until = null, "
                "last_failure_code = null, last_failure_message = null "
                "where id = %s",
                (plan["code"], sub["id"]),
            )
            await cur.execute(
                "update profiles set plan = %s where user_id = %s", (plan["code"], sub["user_id"])
            )
        try:
            await repo.grant_subscription(
                conn, user_id=sub["user_id"], plan_code=plan["code"],
                metadata={"orderId": order_id, "kind": "renewal"},
                period_end_sql="now() + interval '1 month'")
        except repo.CreditError:
            log.exception("subscription %s granted charge but credit failed", sub["id"])
            await conn.rollback()
            return "deferred"
        await conn.commit()
        return "charged"

    async def _mark_failed(self, conn, sub: dict, order_id: str, err) -> None:
        """확정 거절 — past_due 로 내리되 **크레딧은 건드리지 않는다**(유예 중 사용 가능).

        3회째 실패면 next_billing_at 을 비워 재시도를 멈춘다. 실제 정리(크레딧 소멸·
        plan=free)는 grace_until 도달 시 만료 워커가 한다.
        """
        attempt = sub["fail_count"] + 1
        exhausted = attempt >= MAX_ATTEMPTS
        async with conn.cursor() as cur:
            await cur.execute(
                "update subscription_invoices set status = 'failed', fail_code = %s, "
                "fail_message = %s where order_id = %s",
                (err.code[:100], err.message[:500], order_id),
            )
            await cur.execute(
                "update subscriptions set status = 'past_due', fail_count = %s, "
                "grace_until = coalesce(grace_until, now() + interval '%s days'), "
                "next_billing_at = %s, last_failure_code = %s, last_failure_message = %s "
                "where id = %s",
                (attempt, GRACE_DAYS,
                 None if exhausted else "__NEXT_DAY__", err.code[:100],
                 err.message[:500], sub["id"]),
            )
```

> **주의(구현자):** 위 `_mark_failed` 의 `next_billing_at` 자리에 파이썬 값을 넣으면 안 된다.
> psycopg 파라미터로는 `interval` 을 못 만든다. 실제로는 SQL 두 갈래로 쓴다 —
> `exhausted` 면 `next_billing_at = null`, 아니면 `next_billing_at = now() + interval '1 day'`.
> `grace_until` 의 `'%s days'` 도 마찬가지라 `GRACE_DAYS` 를 f-string 으로 SQL 에 넣고
> (상수라 인젝션 여지 없음) 파라미터에서 뺀다. 테스트가 `"status = 'past_due'"` 와
> `"grace_until"`, `"next_billing_at = null"` 문자열을 보므로 이 형태로 맞춘다.

`server/app/main.py` lifespan 에 기동/정지 추가:

```python
        subscription_biller = None
        ...
            if app.state.settings.subscription_billing_enabled:
                from .workers.subscription_biller import SubscriptionBiller

                subscription_biller = SubscriptionBiller(app)
                await subscription_biller.start()
        ...
        if subscription_biller is not None:
            await subscription_biller.stop()
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `cd server && python -m pytest tests/test_subscription_biller.py -v`
Expected: 7 passed

- [ ] **Step 5: 커밋**

```bash
git add server/app/workers/subscription_biller.py server/app/main.py server/tests/test_subscription_biller.py
git commit -m "feat(subscription): 청구 워커 — 갱신·3일 유예·하루 1회 재시도, 결과 미상은 실패로 세지 않음"
```

---

## Task 9: 만료 워커 (해지 주기 종료 · 유예 만료)

**Files:**
- Modify: `server/app/workers/subscription_biller.py` (`SubscriptionExpirer` 추가), `server/app/main.py`
- Test: `server/tests/test_subscription_expirer.py`

**Interfaces:**
- Consumes: `repo.expire_subscription_buckets`
- Produces: `class SubscriptionExpirer`: `.start()`, `.stop()`, `.tick(conn) -> dict`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""만료 워커 — 크레딧이 실제로 사라지는 유일한 자리.

지금까지 period_end 는 쓰기만 하고 읽는 코드가 없었다(2026-09-09 감사). 그래서
해지해도 크레딧이 영영 살아 있었다. 이 워커가 그 구멍을 막는다.
"""

import pytest

from app.workers.subscription_biller import SubscriptionExpirer
from conftest import make_settings


class _Cur:
    def __init__(self, state):
        self.s = state
        self._rows = []

    async def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.s["sql"].append(q)
        if "pg_try_advisory_lock" in q:
            self._rows = [{"locked": self.s["lock"]}]
        elif "from subscriptions" in q and "for update skip locked" in q:
            self._rows = list(self.s["expired_due"])
        elif "update subscriptions set status = 'ended'" in q:
            self.s["ended"].append(params)
            self._rows = []
        elif "update profiles set plan" in q:
            self.s["plans"].append(params)
            self._rows = []
        else:
            self._rows = []

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, state):
        self.s = state

    def cursor(self):
        return _Cur(self.s)

    async def commit(self):
        self.s["commits"] += 1


def _app(state, monkeypatch):
    import app.workers.subscription_biller as biller

    async def fake_expire(conn, *, user_id, reason):
        state["expired"].append({"user_id": user_id, "reason": reason})
        return {"expired": 24000, "available": 0}

    monkeypatch.setattr(biller.repo, "expire_subscription_buckets", fake_expire)

    class _App:
        pass

    app = _App()
    app.state = _App()
    app.state.settings = make_settings(subscription_billing_enabled=True)
    return app


@pytest.fixture()
def state():
    return {"sql": [], "lock": True, "commits": 0, "ended": [], "plans": [], "expired": [],
            "expired_due": [{"id": "sub-1", "user_id": "u1", "status": "canceled"}]}


@pytest.mark.asyncio
async def test_canceled_subscription_expires_all_credits_at_period_end(state, monkeypatch):
    app = _app(state, monkeypatch)
    out = await SubscriptionExpirer(app).tick(_Conn(state))
    assert out["ended"] == 1
    assert state["expired"][0] == {"user_id": "u1", "reason": "canceled"}
    assert state["plans"][0][0] == "free"


@pytest.mark.asyncio
async def test_past_due_past_grace_is_ended_with_its_own_reason(state, monkeypatch):
    state["expired_due"] = [{"id": "sub-2", "user_id": "u2", "status": "past_due"}]
    app = _app(state, monkeypatch)
    await SubscriptionExpirer(app).tick(_Conn(state))
    assert state["expired"][0]["reason"] == "past_due_expired"


@pytest.mark.asyncio
async def test_active_subscription_is_never_expired(state, monkeypatch):
    """이월 정책의 핵심 — 살아있는 구독은 절대 소멸시키지 않는다."""
    state["expired_due"] = []
    app = _app(state, monkeypatch)
    out = await SubscriptionExpirer(app).tick(_Conn(state))
    assert out["ended"] == 0
    assert state["expired"] == []
    # 스캔 조건에 active 가 없어야 한다
    scan = [q for q in state["sql"] if "for update skip locked" in q][0]
    assert "'active'" not in scan


@pytest.mark.asyncio
async def test_expirer_respects_advisory_lock(state, monkeypatch):
    state["lock"] = False
    app = _app(state, monkeypatch)
    assert await SubscriptionExpirer(app).tick(_Conn(state)) == {"skipped": "locked"}
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd server && python -m pytest tests/test_subscription_expirer.py -v`
Expected: FAIL — `ImportError: cannot import name 'SubscriptionExpirer'`

- [ ] **Step 3: 구현**

`server/app/workers/subscription_biller.py` 끝에 추가:

```python
_EXPIRE_LOCK_KEY = (0x5542, 2)


class SubscriptionExpirer:
    """구독 종료 정리 — 크레딧이 실제로 사라지는 유일한 자리(계획서 §0.1).

    대상은 두 갈래뿐이다:
      · canceled 이고 current_period_end 도달 → 결제한 주기를 다 썼다
      · past_due 이고 grace_until 도달 → 3일 유예 안에 결제가 안 됐다
    **active 는 절대 대상이 아니다** — 이월 정책의 핵심이다.
    """

    def __init__(self, app):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="subscription-expirer")

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._task, timeout=30)
            self._task = None

    async def _run(self):
        while not self._stop.is_set():
            try:
                async with get_worker_conn(self.app) as conn:
                    await self.tick(conn)
            except Exception:
                log.exception("subscription expirer tick failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=_IDLE_SECONDS)

    async def tick(self, conn) -> dict:
        async with conn.cursor() as cur:
            await cur.execute("select pg_try_advisory_lock(%s, %s) as locked", _EXPIRE_LOCK_KEY)
            if not (await cur.fetchone())["locked"]:
                return {"skipped": "locked"}
        ended = 0
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select id::text as id, user_id::text as user_id, status "
                    "from subscriptions "
                    "where (status = 'canceled' and current_period_end <= now()) "
                    "   or (status = 'past_due' and grace_until is not null "
                    "       and grace_until <= now()) "
                    "limit %s for update skip locked",
                    (_BATCH,),
                )
                rows = await cur.fetchall()

            for row in rows:
                reason = "canceled" if row["status"] == "canceled" else "past_due_expired"
                await repo.expire_subscription_buckets(
                    conn, user_id=row["user_id"], reason=reason)
                async with conn.cursor() as cur:
                    await cur.execute(
                        "update subscriptions set status = 'ended', next_billing_at = null "
                        "where id = %s",
                        (row["id"],),
                    )
                    await cur.execute(
                        "update profiles set plan = %s where user_id = %s",
                        ("free", row["user_id"]),
                    )
                await conn.commit()
                ended += 1
        finally:
            async with conn.cursor() as cur:
                await cur.execute("select pg_advisory_unlock(%s, %s)", _EXPIRE_LOCK_KEY)
        return {"ended": ended}
```

`main.py` 에 `SubscriptionExpirer` 도 같은 플래그로 기동/정지 등록.

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `cd server && python -m pytest tests/test_subscription_expirer.py tests/test_subscription_biller.py -v`
Expected: 11 passed

- [ ] **Step 5: 커밋**

```bash
git add server/app/workers/subscription_biller.py server/app/main.py server/tests/test_subscription_expirer.py
git commit -m "feat(subscription): 만료 워커 — 해지 주기 종료·유예 만료에서만 크레딧 소멸"
```

---

## Task 10: 프론트 — 구독 시작 · 관리 화면

**Files:**
- Create: `src/features/subscription/Subscription.jsx`, `src/features/subscription/Subscription.module.css`
- Modify: `src/features/pricing/Pricing.jsx:186-215`(구독 버튼), `src/lib/api/httpAdapter.js:731-740`, `src/apps/seller/App.jsx`(라우트)
- Test: `tests/frontend/subscription.test.mjs`

**Interfaces:**
- Consumes: `POST /v1/subscriptions/start`, `GET /v1/subscriptions/me`, `POST /v1/subscriptions/cancel|resume`, `PUT /v1/subscriptions/card`
- Produces: `api.startSubscription`, `api.getMySubscription`, `api.cancelSubscription`, `api.resumeSubscription`, `api.replaceSubscriptionCard`

- [ ] **Step 1: API 어댑터 추가**

`src/lib/api/httpAdapter.js` 의 `confirmTossPayment` 아래에 추가:

```javascript
  async startSubscription({ authKey, customerKey, planCode }) {
    return http('/v1/subscriptions/start', {
      method: 'POST', body: { authKey, customerKey, planCode },
    });
  },
  async getMySubscription() {
    return http('/v1/subscriptions/me');
  },
  async cancelSubscription() {
    return http('/v1/subscriptions/cancel', { method: 'POST' });
  },
  async resumeSubscription() {
    return http('/v1/subscriptions/resume', { method: 'POST' });
  },
  async replaceSubscriptionCard({ authKey, customerKey }) {
    return http('/v1/subscriptions/card', { method: 'PUT', body: { authKey, customerKey } });
  },
```

- [ ] **Step 2: 실패하는 테스트 작성**

```javascript
/* 구독 UI 계약 — 해지 확인창이 '무엇이 사라지는지' 를 숫자로 보여주는가.
   이월 정책이라 해지 시 여러 달치가 한 번에 소멸한다. 숫자 없이 확인만 받으면
   환불 분쟁이 난다(계획서 §0.1). */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';

const SUB = readFileSync('src/features/subscription/Subscription.jsx', 'utf8');
const PRICING = readFileSync('src/features/pricing/Pricing.jsx', 'utf8');
const API = readFileSync('src/lib/api/httpAdapter.js', 'utf8');

test('해지 확인창이 소멸 예정 크레딧과 날짜를 보여준다', () => {
  assert.match(SUB, /expiring/);
  assert.match(SUB, /credits/);
  assert.match(SUB, /소멸/);
});

test('요금제 화면의 구독 버튼이 더 이상 준비 중이 아니다', () => {
  assert.doesNotMatch(PRICING, /결제 연동 준비 중/);
  assert.match(PRICING, /requestBillingAuth/);
});

test('구독 고지문이 이월 정책과 일치한다', () => {
  assert.doesNotMatch(PRICING, /소멸하고 이월되지 않아요/);
  assert.match(PRICING, /이월/);
});

test('구독 API 5개가 어댑터에 있다', () => {
  for (const name of ['startSubscription', 'getMySubscription', 'cancelSubscription',
    'resumeSubscription', 'replaceSubscriptionCard']) {
    assert.match(API, new RegExp(`${name}\\(`), `${name} 없음`);
  }
});
```

- [ ] **Step 3: 테스트 실행 — 실패 확인**

Run: `node --test tests/frontend/subscription.test.mjs`
Expected: FAIL — `ENOENT: src/features/subscription/Subscription.jsx`

- [ ] **Step 4: `Pricing.jsx` 구독 버튼 배선**

`buyTopup` 아래에 추가하고, 구독 카드의 `disabled` 버튼을 `subscribe(p.code)` 호출로 교체한다:

```javascript
  // 구독: 토스 결제창에서 카드를 등록(빌링키 인증)하고 successUrl 로 돌아온다.
  // 실제 빌링키 발급·첫 결제는 서버가 authKey 로 처리한다 — 클라이언트는 금액을 모른다.
  async function subscribe(planCode) {
    if (!session) { requireLogin(); return; }
    setPayError('');
    setBuying(planCode);
    try {
      const { loadTossPayments } = await import('@tosspayments/tosspayments-sdk');
      const toss = await loadTossPayments(TOSS_CLIENT_KEY);
      const payment = toss.payment({ customerKey: session.user.id });
      await payment.requestBillingAuth({
        method: 'CARD',
        successUrl: `${window.location.origin}/subscription/success?plan=${encodeURIComponent(planCode)}`,
        failUrl: `${window.location.origin}/subscription/fail`,
      });
    } catch (e) {
      const code = e?.code || '';
      if (code !== 'USER_CANCEL' && code !== 'PAY_PROCESS_CANCELED') {
        setPayError(e?.message || '카드 등록을 시작하지 못했어요.');
      }
      setBuying(null);
    }
  }
```

구독 카드의 CTA 를 교체:

```jsx
                    !session ? (
                      <button type="button" className={`${s.purchaseButton} ${s.subscriptionButton}`} onClick={requireLogin}>
                        로그인하고 시작하기
                      </button>
                    ) : (
                      <button
                        type="button"
                        className={`${s.purchaseButton} ${s.subscriptionButton}`}
                        disabled={isCurrent || !TOSS_CLIENT_KEY || buying !== null}
                        title={TOSS_CLIENT_KEY ? undefined : '결제 키가 설정되지 않았어요'}
                        onClick={() => subscribe(p.code)}
                      >
                        {isCurrent ? '이용 중' : (buying === p.code ? '카드 등록 창 여는 중…' : '구독하기')}
                      </button>
                    )
```

- [ ] **Step 5: 구독 관리 화면 작성**

`src/features/subscription/Subscription.jsx` — 세 화면을 한 파일에 둔다(같이 바뀐다):
- `SubscriptionSuccess` (`/subscription/success`): 쿼리의 `customerKey`·`authKey`·`plan` 을 `api.startSubscription` 에 넘기고 결과 표시. `PaymentResult.jsx` 의 `once` 가드(StrictMode 이중 마운트 방지) 패턴을 그대로 쓴다
- `SubscriptionFail` (`/subscription/fail`): `code`·`message` 표시
- `SubscriptionManage` (`/subscription`): `api.getMySubscription()` 을 `useQuery` 로 읽어 상태·다음 결제일·카드 표시. 해지 버튼은 **확인 모달**을 띄우고 그 안에 `expiring.credits`·`expiring.expiresAt` 를 문장으로 넣는다:

```jsx
        <p className={s.warn}>
          해지하면 <strong>{Number(data.expiring.credits).toLocaleString('ko-KR')} 크레딧</strong>이
          {' '}{formatKst(data.expiring.expiresAt)}에 모두 사라져요. 이월된 크레딧도 함께 소멸해요.
        </p>
```

`past_due` 이거나 `cardNeedsUpdate` 면 상단에 배너와 '카드 변경' 버튼을 띄운다(유예 중에도 크레딧은 쓸 수 있음을 문장으로 밝힌다).

`src/apps/seller/App.jsx` 라우트 3개 추가(`/subscription`, `/subscription/success`, `/subscription/fail`).

- [ ] **Step 6: 테스트 실행 — 통과 확인**

Run: `node --test tests/frontend/subscription.test.mjs`
Expected: 4 passed

- [ ] **Step 7: 커밋**

```bash
git add src/features/subscription src/features/pricing/Pricing.jsx src/lib/api/httpAdapter.js src/apps/seller/App.jsx tests/frontend/subscription.test.mjs
git commit -m "feat(subscription): 구독 시작·관리 화면 — 해지 시 소멸 예정 크레딧을 숫자로 고지"
```

---

## Task 11: 고지문 교체 + 배포 배선

**Files:**
- Modify: `src/features/pricing/Pricing.jsx:116-125`(고지문), `copilot/api/manifest.yml:318-364`(시크릿), `.env.example`
- Create: `docs/runbooks/subscription-billing.md`
- Test: `tests/frontend/legal-pages.test.mjs`(고지문 회귀 추가)

- [ ] **Step 1: 고지문 회귀 테스트 추가**

`tests/frontend/legal-pages.test.mjs` 에 추가:

```javascript
test('구독 고지문이 이월 정책을 정확히 서술한다', () => {
  const pricing = readFileSync('src/features/pricing/Pricing.jsx', 'utf8');
  // 옛 문구(소멸·이월 없음)가 남아 있으면 거짓 고지다 — 전자상거래법 §17⑥ 표시 의무.
  assert.doesNotMatch(pricing, /소멸하고 이월되지 않아요/);
  assert.match(pricing, /구독을 유지하는 동안.*이월/s);
  assert.match(pricing, /해지하면.*소멸/s);
});
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `node --test tests/frontend/legal-pages.test.mjs`
Expected: FAIL — 옛 문구가 그대로 있음

- [ ] **Step 3: 고지문 교체**

`Pricing.jsx` 의 `billingNotice` 문구를 교체:

```jsx
          {recurring
            ? '구독은 해지할 때까지 매달 자동 결제돼요. 구독 크레딧은 구독을 유지하는 동안 다음 달로 이월되지만, 해지하면 결제 주기가 끝나는 날 이월분까지 모두 소멸해요.'
            : '추가 구매 크레딧은 소멸하지 않아요.'}
```

- [ ] **Step 4: 배포 배선**

`copilot/api/manifest.yml` 의 `secrets:` 블록에 `TOSS_SECRET_KEY` 아래로 추가:

```yaml
  # 자동결제(빌링) — 계획서 docs/plans/2026-09-09-toss-billing-subscription.md.
  # ⚠️ 자동결제는 별도 계약 MID 라 일반결제와 시크릿 키가 다를 수 있다. 개발자센터
  #    API 키 메뉴에서 '자동결제로 계약된 MID' 를 고른 뒤의 키를 넣는다. 비우면
  #    TOSS_SECRET_KEY 로 떨어지므로, MID 가 하나인 상점이면 등록하지 않아도 된다.
  #     copilot-aws secret init --name TOSS_BILLING_SECRET_KEY
  TOSS_BILLING_SECRET_KEY: /copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/TOSS_BILLING_SECRET_KEY
  # 빌링키 컬럼 암호화 키(pgcrypto). ⚠️ 이 값을 잃으면 모든 빌링키를 복호화할 수 없고,
  #    빌링키는 재조회가 불가능하므로 전 구독자가 카드를 다시 등록해야 한다. 로테이션은
  #    반드시 재암호화 마이그레이션과 함께 한다.
  TOSS_BILLING_KEK: /copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/TOSS_BILLING_KEK
  # 웹훅 경로 시크릿 — 토스 일반 웹훅에 서명이 없어 경로 지식이 인증 대용이다.
  TOSS_WEBHOOK_PATH_SECRET: /copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/TOSS_WEBHOOK_PATH_SECRET
```

`variables:` 블록에 추가:

```yaml
  SUBSCRIPTION_BILLING_ENABLED: "false"   # 승인·검증 뒤 true 로 켠다
  TOSS_BILLING_TIMEOUT: "60"
```

`.env.example` 에 같은 4개 키를 주석과 함께 추가.

- [ ] **Step 5: 런북 작성**

`docs/runbooks/subscription-billing.md` 에 아래를 적는다:
1. **켜기 전 확인**: 토스 개발자센터에서 자동결제 MID 가 일반결제와 분리돼 있는지 → 분리면 `TOSS_BILLING_SECRET_KEY` 등록 필수
2. **웹훅 등록**: 개발자센터 웹훅 URL 에 `https://api.wearless.kr/v1/webhooks/toss/<TOSS_WEBHOOK_PATH_SECRET>` 등록, 이벤트 `BILLING_DELETED`
3. **KEK 분실 = 전 구독자 카드 재등록**. 로테이션 절차(신 KEK 로 재암호화하는 일회성 스크립트)를 반드시 함께 실행
4. **테스트 키로 e2e**: 테스트 클라이언트 키로 카드 등록 → 본인인증 코드 `000000` → 첫 결제 → `subscription_invoices.status='paid'` 확인
5. **켜기**: `SUBSCRIPTION_BILLING_ENABLED=true` 배포 → `GET /v1/subscriptions/me` 200 확인
6. **관측**: `job_events` 가 아니라 로그 `wearless.subscription_biller` 를 본다. 실패 급증은 `subscription_invoices where status='failed'` 로 센다

- [ ] **Step 6: 전체 테스트 실행**

Run: `cd server && python -m pytest tests/ -q` 그리고 `node --test tests/frontend/`
Expected: 전부 통과. 기존 테스트가 깨지면 옛 소멸 정책을 고정한 테스트이므로 이월 정책으로 고쳐 쓴다(삭제 금지)

- [ ] **Step 7: 커밋**

```bash
git add src/features/pricing/Pricing.jsx copilot/api/manifest.yml .env.example docs/runbooks/subscription-billing.md tests/frontend/legal-pages.test.mjs
git commit -m "feat(subscription): 고지문을 이월 정책으로 교체 + 배포 시크릿·런북"
```

---

## 자체 점검

**Spec 커버리지**

| §0.1 정책 | 구현 태스크 |
|---|---|
| 갱신 이월 | Task 2 (`grant_subscription`), Task 8 (갱신 경로) |
| 소진 순서(이월분 우선) | Task 2 — 기존 FIFO 정렬 `(source_type, created_at)` 을 그대로 쓴다. 새 코드 없음 |
| 예약 해지 | Task 5 (`/cancel`), Task 9 (주기 종료 시 소멸) |
| 해지 철회 | Task 5 (`/resume`) |
| 결제 실패 → 3일 유예 | Task 8 (`_mark_failed`, `GRACE_DAYS`) |
| 유예 중 크레딧 사용 | Task 8 — 실패 경로가 소멸을 부르지 않는 것으로 보장(테스트로 고정) |
| D+1·D+2·D+3 재시도 | Task 8 (`MAX_ATTEMPTS`, `next_billing_at = now() + 1 day`) |
| 유예 만료 처리 | Task 9 (`SubscriptionExpirer`) |
| 업그레이드 즉시 비례 | Task 6 (`_proration`, `/change-plan`) |
| 다운그레이드 예약 | Task 6 (`scheduled_plan_code`), Task 8 (갱신 시 적용) |
| 1인 1구독 | Task 1 (UNIQUE), Task 4 (409) |
| §0.4 빌링키 암호화 | Task 1(bytea) · Task 4(`pgp_sym_encrypt`) · Task 11(KEK 시크릿) |
| §0.4 단일 러너 | Task 8·9 (`pg_try_advisory_lock`) |
| §0.4 웹훅 시크릿 경로 | Task 7 |
| 법적 고지 교체 | Task 11 |

**미해결로 남기는 것(의도적)**
- `profiles.plan` 을 읽는 화면은 `Pricing.jsx` 의 '이용 중' 배지뿐이다. 이제 쓰기 경로가 생겼으므로 배지가 살아난다. 요금제별 기능 게이팅(모델 할인·매칭 업로드 등 `PLAN_DETAILS` 문구)은 **이 계획 범위 밖** — 별도 작업
- 랜딩(`wearless.kr`)의 환불정책·약관 원문은 이 레포 밖이다. Task 11 은 앱 화면 고지문만 고친다. **원문 개정은 사용자가 랜딩 레포에서 해야 한다**
- 연 결제·무료 체험·쿠폰은 정책에 없다
