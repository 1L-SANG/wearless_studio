# 크레딧 시스템 설계 (구독제 + 추가구매)

> 역할: `backend_integration_plan.md §6`(reserve-then-confirm + append-only ledger)을 **구독 월충전·추가구매·환불 정책**으로 확장한다. 단가(op별 cost)·PG 연동은 **상용 출시 직전 단계**(§같은 문서 §10 과금 순서 원칙)로 분리 — 본 문서는 *장치와 회계 규칙*만 확정한다.
>
> 결정 출처: 2026-06-20 사용자 확정 + Codex 독립 검증(불변식 4개). 메모리 `credit-system-design`.

---

## 1. 모델 (확정)

크레딧 출처는 **2종**이며, 각각 "버킷"으로 관리한다(`credit_sources`).

| 출처 | 충전 | 미사용분 | 환불 | 추적 단위 |
|---|---|---|---|---|
| **구독**(Basic/Plus/Seller) | 매 결제주기 200/600/1400 | **리셋(소멸)** | ✗ (구독 해지로 처리, 본 문서 밖) | 주기당 1 버킷 |
| **추가구매(top-up)** | 다 쓰면 구매 | 소멸 안 함 | **미사용 건만 7일 내** | 구매 1건 = 1 버킷 |

- **소진 순서**: **구독 버킷 먼저(소멸성이라)** → 그다음 **추가구매 FIFO(오래된 구매부터)**.
- 응답 `credits` = `balance − reserved` (기존 §6 불변, 프론트 선차감 금지).
- `balance` = **active 버킷들의 remaining 합**(pending_refund·expired 제외).

---

## 2. 스키마 (전부 새 포워드 마이그레이션)

기존 재사용: `credit_accounts(user_id, balance, reserved)`, `credit_ledger`(append-only, idempotency_key), `profiles.plan`.
**만들지 않음(중복)**: `profiles.credits`, `credit_transactions`, `credit_consumption`.

### 2.1 `pricing_plans` (요금제/상품 카탈로그, user_id 없음)
| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | uuid pk | |
| code | text uq | 'basic'/'plus'/'seller'/topup SKU |
| kind | text check (subscription\|topup) | |
| name | text | 표시명 |
| credits | int | 지급 크레딧 |
| price | int | 원(₩) |
| billing_period | text check (monthly\|once) | 구독=monthly, topup=once |
| is_active | bool default true | 판매중 |
| sort_order | int | |

시드: Basic(200/₩19,900)·Plus(600/₩49,900)·Seller(1400/₩99,900) = subscription/monthly. **topup SKU·가격은 TBD**(단가 확정 후).

### 2.2 `credit_sources` (버킷 = 잔액의 정본)
| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | uuid pk | |
| user_id | uuid → auth.users.id | |
| source_type | text check (subscription\|topup) | |
| plan_id | uuid → pricing_plans.id null | 어떤 상품에서 왔는지 |
| initial_credits | int | 지급 시 |
| remaining_credits | int check 0..initial | 남은 |
| status | text check (active\|pending_refund\|refunded\|expired) | |
| period_end | timestamptz null | 구독 버킷 만료(topup=null) |
| payment_id | uuid → payment_history.id null | 결제 연결 |
| created_at | timestamptz default now() | FIFO 정렬 키 |

인덱스: `(user_id, status, created_at)` (FIFO 소진·가용합 계산용).

### 2.3 `payment_history` (결제 — 테스트용, PG 나중)
id, user_id, plan_id, amount, kind(subscription\|topup), provider(text default 'test'), provider_ref text null, status(text: paid\|refunded), created_at.

### 2.4 `refund_requests`
id, user_id, credit_source_id, status check (pending\|approved\|rejected), reason text null, created_at, resolved_at null, resolved_by null.
**DB 무결성**(앱 asserts 외 2차 방어): ① 부분 uq `(credit_source_id) where status='pending'`(중복 요청 차단) · ② 복합 FK `(credit_source_id, user_id) → credit_sources(id, user_id)`(교차-유저 환불 차단 — credit_sources에 `unique(id, user_id)` 추가) · ③ `check (status='pending' or resolved_at is not null)`(종결 요청 resolved_at 필수).

### 2.5 `credit_ledger` 확장
컬럼 추가: **`credit_source_id` uuid → credit_sources.id null**. 충전/차감/환불을 **버킷별 1행**으로 기록(다중 버킷 차감 = 행 여러 개). `action_key`: `grant_subscription`/`grant_topup`/소비키(예 `mannequin:generate`)/`refund_topup`/`expire_subscription`.

---

## 3. 알고리즘 (의사코드)

모든 쓰기는 단일 트랜잭션 + `credit_accounts FOR UPDATE`로 직렬화(기존 §6).

### 3.1 충전 — 구독 월 리셋
```
grant_subscription(user, plan):  # 결제주기 경계에서 (자동화는 §5 TBD)
  tx:
    lock account
    # 기존 구독 버킷 소멸 (진행중 예약은 새 버킷서 confirm 정산 — 불변식 5/§5 cross-cycle)
    for b in active subscription buckets of user:
        ledger(delta = -b.remaining, action='expire_subscription', source=b)
        b.status = 'expired'; b.remaining = 0
    # 새 버킷 — full plan.credits라 진행중 reserved(job당 ~수 크레딧)를 항상 상회
    src = credit_sources(source_type='subscription', plan_id=plan.id,
                         initial=plan.credits, remaining=plan.credits,
                         status='active', period_end=now()+1 cycle)
    ledger(delta = +plan.credits, action='grant_subscription', source=src)
    recompute_balance(account)   # balance = Σ active remaining
    assert reserved <= balance   # backstop: credit_accounts CHECK가 강제 — 위반 시 reset tx 실패→다음 정산까지 보류
```

### 3.2 충전 — 추가구매(top-up)
```
purchase_topup(user, sku):       # 지금은 테스트, 나중 PG
  tx:
    pay = payment_history(kind='topup', amount=sku.price, status='paid', provider='test')
    src = credit_sources(source_type='topup', plan_id=sku.id, initial=sku.credits,
                         remaining=sku.credits, status='active', payment_id=pay.id)
    ledger(delta=+sku.credits, action='grant_topup', source=src)
    recompute_balance(account)
```

### 3.3 차감 — reserve / confirm / release (버킷 인지)
```
reserve(user, cost):             # job 시작 tx (기존 §6 + 가용 정의만 교체)
  lock account FOR UPDATE                             # ★ 먼저 account 잠그고(다른 버킷 경로와 동일 순서)
  available = (Σ active 버킷 remaining) − reserved    #   그 락 아래서 버킷 합 읽기 → 동시 confirm의
  if available < cost: 402                            #   버킷 변경과 직렬화(stale-high 과예약 race 차단)
  reserved += cost
  # 버킷엔 아직 안 댐 — 어느 버킷서 깎일지는 confirm 때 FIFO로 결정

confirm(user, job, actual):      # 성공 tx — 전체가 단일 DB tx (부분 적용 없음)
  lock account; SELECT job FOR UPDATE
  if job.status in (done, error): return   # 종결 job → no-op (멱등 마커 = job.status, NEW-1)
  remain = actual
  for b in active buckets ORDER BY (source_type='topup'), created_at:   # 구독 먼저, 그다음 topup FIFO
      take = min(b.remaining, remain)
      if take>0:
          b.remaining -= take
          ledger(delta=-take, action=job.cost_key, source=b, job=job,
                 idempotency_key = f"credit:job:{job.id}:settle:{b.id}")   # 버킷별 멱등 (Codex ③)
          remain -= take
      if remain==0: break
  assert remain == 0            # 커버 실패 시 hard error → tx rollback (무음 미달차감 금지, Codex#2)
  reserved -= job.reserved      # 예약 해제
  recompute_balance(account)
  job.credits_charged = actual; job.status = 'done'   # 종결 마커

release(user, job):              # 실패 tx
  lock account; SELECT job FOR UPDATE
  if job.status in (done, error): return   # 종결 job → no-op (중복 release 차단, NEW-1)
  reserved -= job.reserved      # 버킷 불변 → 환불권 유지
  job.status = 'error'          # 종결 마커 (credits_charged는 None 유지 = 미차감)
```
> **멱등**: confirm/release는 단일 tx + **`job.status`(done/error) 종결 마커** 선검사로 재시도 시 no-op. `credits_charged`는 성공시에만 set돼 release 가드로 못 씀(NEW-1). 단일 tx라 ledger 행과 `remaining` 차감이 함께 commit/rollback → 부분상태 불가. 버킷별 `settle:{bucket_id}` 키는 다중 버킷 차감 ledger 중복 삽입까지 방어(Codex③). **구현체 = 기존 `finalize_mannequin_success/failure`**(jobs row FOR UPDATE + lease 펜스 + 종결 job_events).

### 3.4 환불 — 요청 / 승인 / 거부 (topup 버킷만)
```
request_refund(user, source_id):
  lock account
  b = credit_sources[source_id]  (소유·source_type='topup' 확인)
  # 적격 (Codex 불변식 ①②):
  assert b.status=='active'
  assert b.remaining == b.initial                 # 한 푼도 confirmed 차감 안 됨
  assert now() - b.created_at <= 7 days
  assert reserved == 0   # MVP: 진행중 예약 있으면 거부(과보수적·안전). ※정밀화는 §5
  b.status = 'pending_refund'                      # 가용서 즉시 제외 (불변식 ①)
  balance -= b.remaining                           # ★ 가용 변화 = 반드시 원장 행 동반(원장-잔액 일관성)
  ledger(delta=-b.remaining, action='refund_request', source=b)
  refund_requests(credit_source_id=b.id, status='pending')

approve_refund(admin, req):
  lock account
  assert req.status=='pending'        # 종결 요청 비가역 — 재처리 차단 (Codex#4)
  b = req.source
  assert b.status=='pending_refund'   # 요청 시 이미 가용서 빠짐 → balance 재차감 금지(불변식 ④)
  b.status='refunded'
  ledger(delta=0, action='refund_approved', source=b)   # delta=0 마커(가용 영향 없음 — 이중차감 방지)
  payment_history[b.payment_id].status='refunded'
  req.status='approved'; req.resolved_*
  # 실제 환불은 PG 단계(테스트는 기록만)

reject_refund(admin, req):
  lock account
  assert req.status=='pending'        # 종결 요청 비가역 (Codex#4)
  b = req.source
  assert b.status=='pending_refund'   # pending_refund 버킷만 복귀 — refunded 재활성 차단
  b.status='active'                   # 복귀 → 다시 가용
  balance += b.remaining              # ★ 복원 = 원장 행 동반
  ledger(delta=+b.remaining, action='refund_rejected', source=b)
  req.status='rejected'; req.resolved_*
```

---

## 4. 불변식 (Codex 검증, 구현·테스트 필수)

1. **pending_refund / expired 버킷은 `balance`(가용)·예약풀에서 제외.** `recompute_balance`는 active만 합산.
2. **"미사용" 판정은 confirmed(remaining==initial)뿐 아니라 in-flight 예약도 확인.** MVP=`reserved==0`이면 통과(과보수적). 정밀화(§5)는 "이 버킷이 FIFO상 어떤 예약도 떠받치지 않음"으로 완화.
3. **다중 버킷 차감의 멱등** = 버킷별 ledger 행 + `settle:{bucket_id}` 키. 단일 settle_key 금지.
4. **환불 승인 시 이중차감 금지 + 종결 비가역.** pending_refund 전환에서 이미 가용서 빠졌으므로 승인은 status만 refunded(가용 재차감 안 함). approve/reject는 `req.status=='pending'` AND `bucket.status=='pending_refund'` 가드 — refunded 버킷 재활성 불가(Codex#4).
5. **confirm/release 원자성·완전성·종결멱등.** 둘 다 단일 tx + jobs row FOR UPDATE + **`job.status`(done/error) 종결 마커 선검사 → 재시도 no-op**(NEW-1: `credits_charged`는 release 가드로 부적합). confirm은 추가로 `assert remain==0`(미달차감 금지). 부분/이중 정산 불가(Codex#2·#5). reset가 예약 capacity를 줄이면 assert가 hard error로 노출 → 무음 손실 방지(Codex#1 backstop).

---

## 5. TBD / 미해결 (구현 전 또는 출시 전)

- **op별 단가**: AI 엔진·화질 확정 → 원가 산출 후(현 마네킹=2 placeholder). `creditCosts` config + `credit_cost_version`.
- **topup SKU·가격**: 미정.
- **월 리셋 자동화**: lazy(account 접근 시 period_end<now면 reset) vs 스케줄러 vs **PG 결제 웹훅**. 셋 다 §3.1을 account lock 하에서 호출. PG가 마지막 단계라 베타는 수동 `grant_subscription`. 설계는 모두 수용.
- **cross-cycle 청구 허용**: 지난 주기에 시작된 job이 리셋 후 confirm되면 새 주기 버킷서 정산됨(회계상 무해, full-plan 지급이라 항상 커버). 불변식 5의 assert가 이상 시 노출.
- **per-bucket 예약 모델(Path A, 후속)**: 현재 `reserved`는 aggregate라 reset/refund가 "어느 버킷이 진행중 job을 떠받치나"를 모름(Codex#3). 실 매그니튜드(월 ≥200 vs job ~수 크레딧)에선 race가 무해해 **MVP는 aggregate + assert(불변식 5)로 가드**. 단가가 커지거나 동시성/예약이 복잡해지면 `credit_holds(job_id, source_id, amount)` 테이블로 버킷별 hold를 도입(reserve가 버킷서 즉시 차감·기록, confirm이 정산, release가 복원) — 이때 reset/refund 적격이 정확해짐.
- **환불 적격 정밀화**(불변식 2): MVP `reserved==0`(진행중 job 있으면 환불 거부, 과보수적) → Path A 도입 시 "이 버킷이 어떤 hold도 안 가짐"으로 완화.
- **stuck 예약 복구**(NEW-2): confirm의 `assert remain==0`이 (극단 edge로) 반복 rollback되면 job이 `running`에 머물러 `reserved`가 묶임. **영구 stuck 없음** — lease 만료 시 dispatcher의 `recover_stale_leases`가 그 job을 `error`로 종결하고 예약을 release(기존 AG-04 stale 복구 경로 = `list_unsettled_errored_jobs` 재시도). 운영 런북: stale 복구가 못 푸는 케이스는 해당 job/account 수동 점검.
- **구독 해지/구독료 환불**: 본 문서 밖(PG 단계).

---

## 6. API (백엔드)

| 함수/엔드포인트 | 메서드 | 비고 |
|---|---|---|
| 잔액 | `GET /v1/me/account` | 기존 + plan·creditsBreakdown(subscription/topup remaining) 확장 |
| 요금제 | `GET /v1/pricing-plans` | active 목록(authenticated) |
| 사용내역 | `GET /v1/credits/history` | ledger 행(project_id·action·delta·source·created_at) — 프론트가 project_id로 묶고 펼쳐 세부 표시 |
| 충전(테스트) | `POST /v1/credits/topups:purchase` | payment + topup 버킷 + grant. PG 나중 |
| 환불요청 | `POST /v1/credits/refunds` | {creditSourceId} → 적격검증 → pending |
| 환불승인/거부 | `POST /v1/admin/refunds/{id}:approve\|:reject` | admin(profiles.role) |

소비(reserve/confirm/release)는 job 내부 — 직접 엔드포인트 아님. 크레딧 부족 = **402 insufficient_credits**(기존).

---

## 7. 구현 순서

1. 마이그레이션(pricing_plans·credit_sources·payment_history·refund_requests + credit_ledger.credit_source_id) + 시드.
2. 백엔드: `recompute_balance`/reserve·confirm·release 버킷화 + 충전·환불 API + 마네킹 job confirm 연결.
3. pytest(돈 로직 — 불변식 4개·FIFO·멱등·동시성 시나리오).
4. 프론트(nav·/pricing·/credits/history·환불 모달)는 **auth 에이전트 조율 후 별도**.
