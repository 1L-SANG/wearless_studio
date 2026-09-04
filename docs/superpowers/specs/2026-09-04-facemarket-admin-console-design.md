# FaceMarket 관리자 콘솔 — 권한·감사·대시보드 설계

**작성일:** 2026-09-04
**상태:** 초안 (사용자 리뷰 대기)
**브랜치:** `feat/admin-console` (워크트리 `../wearless_studio-admin-console`)
**범위:** `admin.wearless.kr` 콘솔을 지원서 검토 한 화면에서 **운영 콘솔**로 넓힌다 — 관리자 권한을 콘솔에서 부여·회수하고, 관리자 행위를 감사 원장에 남기고, 대시보드·모델/유저 조회를 더한다. 인증 스택·생성 파이프라인·과금은 건드리지 않는다.

---

## 1. 목표

지금 콘솔은 화면이 하나다(`AdminApplications`, 233줄). 관리자는 DB 를 직접 열어야 만들 수 있고, 관리자가 무엇을 했는지는 `fm_model_applications.reviewed_by` 한 칸 말고는 남지 않는다. 숫자를 보려면 psql 을 켜야 한다.

**성공 기준**

1. 관리자가 콘솔에서 다른 계정을 관리자로 올리고 내릴 수 있다 — 안전장치(자기 강등·최후 관리자·미가입 계정) 셋이 서버에서 강제된다.
2. 관리자의 모든 쓰기가 `admin_audit_log` 에 한 줄씩 남는다. 기존 지원서·환불 라우트도 소급 적용.
3. 대시보드 한 화면에서 ① 손대야 할 일 ② 기간 KPI ③ 추이·분포를 본다. 호출 1회.
4. 모델·유저를 검색해 상세를 보고, 정지/정지 해제를 사유와 함께 처리한다.
5. 콘솔 UI 가 shadcn/ui 로 통일된다 — 다른 앱(seller·facemarket) 스타일에는 영향 0.
6. 5조각으로 쪼개 각각 독립 배포된다.

---

## 2. 확정된 결정 (사용자)

| # | 결정 | 버린 대안 |
|---|---|---|
| 1 | 계정 분리는 **role 승격 UI 만** — 인증 스택은 지금 그대로 | 초대 전용 관리자 계정(admin_invites), 완전 별도 인증 스택(admin_users + 자체 세션) |
| 2 | 대시보드는 **운영 큐 + 비즈니스 지표 + 유저/모델 현황** | 서비스 건강(잡 성공률·큐 적체·p50/p90) — 이번 범위 밖 |
| 3 | 조치는 **조회 + 저위험**(승인/거절·메일 재발송·role 토글·모델 정지) | 풀 어드민(크레딧 수동 지급, 라이선스 강제 해지, 계정 정지, 데이터 삭제) |
| 4 | 범위는 **FaceMarket 중심 + 매출 요약** | FaceMarket 전용, 스튜디오 전체 |
| 5 | **실운영용, 순차 배포** — 정확성·감사·권한 경계 우선 | 발표 데모용 완성도 우선 |
| 6 | 집계는 **라이브 SQL + 짧은 캐시** | 롤업 테이블 + 크론 배치, 외부 BI(Metabase) |
| 7 | UI 는 **admin 번들에만 Tailwind + shadcn/ui**, 기존 지원서 화면도 같이 이관 | 공용 `ui.jsx` 유지, 대시보드만 새 UI |
| 8 | 다크 모드는 **나중**(색을 CSS 변수로 빼두기만) | 처음부터 라이트/다크 병행 |

---

## 3. 현재 상태 (전수조사)

### 이미 있는 것 — 재사용한다

| 자산 | 위치 | 역할 |
|---|---|---|
| 관리자 판정 | `repo.is_admin()` — `profiles.role = 'admin'` | 그대로 단일 진실 원천 |
| admin 진입 문서·번들 | `admin.html` → `src/apps/admin/{main,App}.jsx`, `vite.config.js` input.admin, `vercel.json` host rewrite | Tailwind 를 여기에만 가두는 격리막 |
| 호스트 되돌림 | `src/lib/host.js redirectToOwnDocumentHost()` | 셸에서 재사용 |
| 지원서 검토 화면 | `src/features/admin/AdminApplications.jsx` (+ module.css) | 기능 유지, 껍데기만 shadcn 으로 |
| admin API 클라이언트 | `src/lib/api/facemarket.js` 관리자 절 | 새 호출 여기에 이어 붙임 |
| 로컬 관리자 승격 | `scripts/qa_grant_admin.sh` | QA 부트스트랩 |
| 아이콘 | `lucide-react` 이미 의존성 | shadcn 기본 아이콘 세트 그대로 |

### 없는 것 — 만든다

- **감사 원장.** 관리자 행위 기록이 없다. `fm_model_applications.reviewed_by/reviewed_at` 만 있고, 환불 승인·메일 재발송은 흔적이 없다.
- **권한 가드 단일 지점.** `is_admin` 호출이 6군데에 흩어져 있다(`routes.py`×2, `facemarket.py`, `facemarket_cutover.py`, `facemarket_applications.py`). 문구·에러 코드도 제각각.
- **집계 엔드포인트.** admin 라우트는 지원서 5 + 환불 2 뿐. 숫자를 주는 것이 하나도 없다.
- **모델·유저 조회.** `fm_models` 를 관리자가 볼 수 있는 경로가 없다.
- **Tailwind.** 레포는 전부 CSS Module. shadcn 전제인 Tailwind 가 없다.

---

## 4. 권한·감사

### 4.1 계정 모델 — 안 바꾼다

`profiles.role in ('admin','user')` 유지. 관리자는 **일반 가입 계정 + role 플래그**다. 별도 로그인·초대·MFA 는 만들지 않는다(결정 1).

**첫 관리자는 계속 수동**이다 — `scripts/qa_grant_admin.sh` 또는 prod DB UPDATE. 콘솔이 자기 자신의 첫 관리자를 만들 수 있으면 그건 권한 경계가 아니다. 문서에만 남긴다.

### 4.2 role 변경 가드 (서버 강제)

`POST /admin/staff/{user_id}/role  {"role": "admin" | "user"}`

한 트랜잭션 안에서:

1. **자기 강등 금지** — `target_user_id == actor_user_id` 이고 `role='user'` 면 400 `cannot_demote_self`. 실수로 본인 권한을 내리면 콘솔로 되돌릴 방법이 없다.
2. **최후 관리자 강등 금지** — `select count(*) from profiles where role='admin' for update` 가 1이면 400 `last_admin`. 잠금 없이 세면 두 관리자가 서로를 동시에 내려 0명이 될 수 있다.
3. **미가입 계정 승격 금지** — `profiles` 행이 없으면 404 `user_not_found`. 초대 흐름을 안 만들기로 했으므로(결정 1), 승격 대상은 이미 가입한 계정뿐이다.

프런트도 같은 버튼을 비활성화하지만 **판정은 서버가 한다**. 프런트 비활성화는 안내일 뿐이다.

### 4.3 감사 원장

```sql
create table public.admin_audit_log (
  id            uuid primary key default gen_random_uuid(),
  actor_user_id uuid not null references auth.users(id) on delete set null,
  action        text not null,          -- 'application.approve' | 'staff.role.grant' | ...
  target_type   text not null,          -- 'application' | 'user' | 'model' | 'refund'
  target_id     text,                   -- uuid 아닌 대상도 있어 text
  before        jsonb not null default '{}'::jsonb,
  after         jsonb not null default '{}'::jsonb,
  note          text,                   -- 거절 사유·정지 사유
  created_at    timestamptz not null default now()
);
create index admin_audit_log_created_idx on public.admin_audit_log (created_at desc);
create index admin_audit_log_target_idx  on public.admin_audit_log (target_type, target_id, created_at desc);
```

- **actor 는 `on delete set null`** — 관리자 계정이 지워져도 기록은 남아야 한다. 원장이 행위자보다 오래 산다.
- **PII 최소화.** `before/after` 에 상태 전이·식별자만 넣는다. 지원자 이름·생년월일·사진 키는 넣지 않는다(그건 원본 테이블에 있고, 30일 PII 스윕 대상이다 — 원장에 복사하면 스윕을 우회한다).
- **기록은 조치와 같은 트랜잭션.** 조치가 커밋되면 기록도 커밋된다. 별도 커밋이면 둘이 어긋난다.
- **읽기 전용.** 콘솔에 수정·삭제 경로를 만들지 않는다.

기록 대상 액션:

| action | target_type | 비고 |
|---|---|---|
| `application.approve` / `application.reject` | application | reject 는 `note` = 사유 |
| `application.resend_email` | application | |
| `staff.role.grant` / `staff.role.revoke` | user | |
| `model.suspend` / `model.unsuspend` | model | `note` = 사유(필수) |
| `refund.approve` / `refund.reject` | refund | 기존 `routes.py` 라우트에 소급 |

### 4.4 가드 단일화

`server/app/admin_guard.py` 신설:

```python
async def require_admin(conn, user_id: str) -> None: ...
async def write_audit(conn, *, actor_user_id, action, target_type, target_id,
                      before=None, after=None, note=None) -> None: ...
```

기존 6개 호출부를 이걸로 갈아끼운다. 에러 코드·문구가 한 곳에서 나온다(`403 forbidden` / "관리자만 가능해요."). `facemarket_cutover.py` 의 호출부도 포함한다.

---

## 5. 대시보드

### 5.1 데이터 계약

`GET /v1/facemarket/admin/overview?days=30` (허용값 7·30·90)

```jsonc
{
  "queue": {                       // 기간 무관 — 지금 쌓여 있는 일
    "applicationsUnderReview": 3,
    "identityMismatch": 1,         // under_review 이면서 대조 실패 이력 있음
    "emailFailed": 0,
    "refundsPending": 2
  },
  "period": { "days": 30, "from": "2026-08-05T00:00:00Z", "to": "2026-09-04T…" },
  "kpi": {
    "applicationsSubmitted": 12, "applicationsApproved": 7, "applicationsRejected": 3,
    "licensesIssued": 5,
    "settlementAmountKrw": 120000, "settlementFailed": 1,
    "creditRevenueKrw": 350000
  },
  "series": [                      // 일별, 빈 날짜도 0 으로 채워 보냄
    { "date": "2026-08-06", "applications": 1, "licenses": 0, "settlementAmountKrw": 0 }
  ],
  "distribution": {
    "models": { "pending": 2, "verified": 9, "suspended": 1 },
    "enrollments": { "passed": 9, "inFlight": 2, "failed": 3 }
  }
}
```

### 5.2 숫자의 정의 (못 박는다)

| 지표 | 정의 | 왜 이렇게 |
|---|---|---|
| `applicationsUnderReview` | `fm_model_applications` `status='under_review'` | |
| `identityMismatch` | 위 + `identity_mismatch_count > 0` | 사람 눈이 필요한 건 대기 중인 것뿐 |
| `emailFailed` | `fm_model_application_emails` `status='failed'` 인 application 수(중복 제거) | 화면의 '메일 미발송' 배지와 같은 원천 |
| `refundsPending` | `refund_requests` `status='pending'` | |
| `licensesIssued` | `fm_licenses` `created_at` 기간 내 | revoked 도 "발급됐던" 사실이라 센다 |
| `settlementAmountKrw` | `fm_settlements` `chain_status='confirmed'` 의 `total_amount` 합, **`payment_id like 'sim:%'` 제외** | 데모/부하용 시뮬 정산(`facemarket.py` simulate_settlement)이 실 TX 라 그대로 두면 매출로 섞인다 |
| `settlementFailed` | `chain_status='failed'` (같은 sim 제외) | |
| `creditRevenueKrw` | `payment_history` `status='paid'` 의 `amount` 합, **`provider='test'` 제외** | `'test'` 는 테스트 구매 경로 기본값(`repo.py:2936` 주석). 실 결제는 `'toss'` |
| `models.*` | `fm_models.status` 분포 | 전체 기준(기간 무관) |
| `enrollments.*` | `fm_biometric_enrollments.status` → passed / failed·cancelled·expired / 그 외 in-flight | |

날짜 경계는 **UTC 가 아니라 KST(Asia/Seoul)** 로 자른다. 운영자가 "오늘"이라고 할 때의 오늘이다. SQL 은 `(created_at at time zone 'Asia/Seoul')::date` 로 묶는다.

### 5.3 성능

- 한 요청에서 쿼리 8~10개를 순차 실행한다(모두 count/sum, 인덱스 스캔).
- **응답 캐시 30초**, 프로세스 메모리(`{days: (expires_at, payload)}`). 새로고침 연타가 DB 를 두드리지 않게. 다중 태스크(ECS)면 태스크마다 따로 캐시 — 30초짜리라 불일치는 무해하다.
- 필요한 인덱스: `fm_model_applications(status)`, `fm_model_applications(created_at)`, `fm_licenses(created_at)`, `fm_settlements(chain_status, created_at)`, `payment_history(status, created_at)`. 마이그레이션에서 `if not exists` 로 추가.
- 지금 데이터 규모(각 테이블 수백~수천 행)에서는 라이브 SQL 로 충분하다. 느려지면 화면 계약을 그대로 둔 채 뒤를 롤업 테이블로 바꾼다.

### 5.4 화면

위에서 아래로:

1. **큐 줄** — 4칸 카드. 숫자 클릭 → 해당 목록으로 이동(`/applications?filter=…`, `/refunds`). 0 이면 중립 회색, 0 초과면 강조.
2. **KPI 카드** — 기간 토글(7/30/90). 지원서(제출/승인/거절), 라이선스 발급, 정산액·실패, 크레딧 매출.
3. **추이·분포** — 일별 꺾은선(지원서·라이선스·정산액), 모델 상태 분포, 생체등록 완료율.

차트는 의존성을 새로 넣지 않고 **인라인 SVG**로 그린다(꺾은선 1종·막대 1종). Recharts 를 admin 번들에 넣을 만큼 차트 종류가 많지 않다.

---

## 6. 모델·유저 화면

**목록** `GET /admin/models?q=&status=&limit=&cursor=`
표: 모델명 · 상태 · 계정 이메일 · 라이선스 수 · 최근 정산일 · 생성일. `q` 는 `display_name` 부분일치 + 계정 이메일 정확일치.

**상세** `GET /admin/models/{id}`
모델 기본 정보 / 라이선스 목록(상태·단가·만료·VC id) / 최근 정산 10건 / 생체등록 상태·시각 / 지원서 이력(있으면).

**조치**
- `POST /admin/models/{id}/suspend  {"reason": "..."}` → `status='suspended'`. 사유 필수(빈 문자열 400).
- `POST /admin/models/{id}/unsuspend` → **정지 직전 상태로 되돌린다.**

**`verified` 는 콘솔이 새로 만들지 못한다.** 그 배지는 생체등록 통과가 붙이는 것이다. 관리자가 손으로 올릴 수 있으면 검증 배지의 뜻이 사라진다.

그래서 unsuspend 의 착지점은 이렇게 정한다: 같은 모델의 가장 최근 `model.suspend` 감사 행에서 `before.status` 를 읽어 그 값으로 복원하고, 그 행이 없으면(콘솔 밖에서 정지된 경우) `pending` 으로 내린다. 검증됐던 모델이 정지 한 번에 배지를 영구히 잃지 않으면서도, 콘솔이 `verified` 를 **창조**하지는 못한다 — 되돌릴 뿐이다. 이 복원 자체도 `model.unsuspend` 로 기록된다.

**유저 조회**는 이 화면에 흡수한다. 별도 유저 목록 화면은 만들지 않는다(FaceMarket 중심 — 결정 4). 계정 단위로 필요한 정보(이메일·가입일·역할)는 모델 상세와 관리자 관리 화면에서 다 나온다.

이메일은 `auth.users.email` 조인으로 읽는다. **서버 DB 롤이 `auth.users` 를 읽을 수 있는지는 1단계 태스크에서 실측 확인한다** — 앱 코드가 지금까지 `auth.users` 를 직접 조회한 적이 없다(트리거만 접근). 막히면 대안은 `profiles` 에 이메일 미러 컬럼을 두고 가입 트리거가 채우게 하는 것이다.

---

## 7. 관리자 관리 화면

- 이메일로 계정 검색 → `역할` 토글. `GET /admin/staff?q=` / `POST /admin/staff/{user_id}/role`.
- 현재 관리자 목록(이메일·표시이름·마지막 조치 시각).
- 하단에 최근 감사 기록 20줄 — `GET /admin/audit?limit=20&target_type=&target_id=`.
- 4.2 의 가드에 걸리면 버튼이 비활성 + 이유 툴팁("마지막 관리자는 내릴 수 없어요").

---

## 8. UI 기술 — Tailwind/shadcn 격리

**격리가 성립하는 이유:** admin 은 진입 문서(`admin.html`)와 번들(rollup input `admin`)이 이미 분리돼 있다. Tailwind CSS 를 `src/apps/admin/` 아래에서만 import 하면 그 CSS 는 admin 청크에만 들어간다. Preflight(리셋)도 admin 페이지에서만 적용된다. seller·facemarket 은 한 바이트도 안 바뀐다.

- **Tailwind v4 + `@tailwindcss/vite`.** `src/apps/admin/admin.css` 하나가 `@import "tailwindcss";` 와 `@source` 로 스캔 범위를 `src/apps/admin`·`src/features/admin`·`src/components/admin-ui` 로 제한한다. 다른 앱 파일을 스캔하면 안 나가는 클래스가 붙는다.
- **shadcn 컴포넌트는 `src/components/admin-ui/` 에 복사.** JSX 모드(`tsx: false`), 별칭은 기존 `@/` 를 쓴다. 필요한 것만: button, card, table, badge, dialog, input, select, tabs, dropdown-menu, toast(sonner), skeleton, tooltip.
- **새 의존성:** `tailwindcss`, `@tailwindcss/vite`, `clsx`, `tailwind-merge`, `class-variance-authority`, 해당 Radix 패키지들. `lucide-react` 는 이미 있다.
- **admin 은 공용 `@/components/ui.jsx` 의 시각 컴포넌트를 더 이상 쓰지 않는다.** 두 디자인 시스템이 한 화면에 섞이면 지금보다 지저분해진다. 예외는 `useToast` 훅 하나다 — `ToastProvider` 는 진입 공통이고 스타일은 studio 레이어가 주므로, 훅 하나를 위해 토스트를 다시 구현하지 않는다. `AdminApplications` 도 함께 이관한다(결정 7) — **동작·API 호출·상태 처리는 그대로 두고 마크업만 교체**한다.
- 색은 CSS 변수(zinc 기반, 라이트 전용)로 정의한다. 다크는 나중에 변수 블록 하나 추가로 끝나게(결정 8).
- `vite.config.js` 의 `optimizeDeps.entries` 에 `admin.html` 은 이미 있다. Tailwind 플러그인은 전역 등록이지만 CSS 를 import 하는 진입만 영향을 받는다.

**회귀 방지:** `tests/frontend/bundle-separation.test.mjs` 에 "seller·facemarket 번들에 tailwind/admin-ui 가 섞이지 않는다" 단언을 더한다.

---

## 9. 테스트

**백엔드 (pytest, `server/tests/`)**
- `test_admin_audit_migration.py` — 테이블·인덱스·FK(`on delete set null`) 계약.
- `test_admin_guard.py` — 비관리자 403(모든 신설 라우트), 감사 기록이 조치와 같은 트랜잭션에서 롤백되는지.
- `test_admin_staff.py` — 가드 3종(자기 강등·최후 관리자·미가입) + 성공 경로 + 감사 행 생성.
- `test_admin_overview.py` — 시뮬 정산(`sim:` 접두) 제외, `provider='test'` 결제 제외, KST 날짜 경계, 빈 날짜 0 채움, `days` 허용값 밖 400.
- `test_admin_models.py` — 검색·필터·상세, suspend 사유 필수, `verified` 로 직접 못 올림.

**프런트 (`node --test tests/frontend/*.test.mjs`)**
- 번들 분리 회귀(위 8절).
- admin 라우트 트리(신규 4 라우트 + 비로그인 가드).

레포에 vitest 는 없다 — 프런트 테스트는 `node --test` + 정적 소스 단언 방식이다.

---

## 10. 배포 단계

각 단계가 독립 배포 가능하다. 앞 단계가 뒤 단계의 전제다.

| # | 내용 | 사용자 눈에 보이는 변화 |
|---|---|---|
| 1 | `admin_audit_log` 마이그 + 집계 인덱스 + `admin_guard.py` + 기존 6 호출부 이관 + 기존 5 라우트 감사 기록 | 없음(내부) |
| 2 | Tailwind/shadcn 셋업 + 사이드바 셸 + `AdminApplications` 이관 | 콘솔 외형 전면 교체, 기능 동일 |
| 3 | `GET /admin/overview` + 대시보드 화면 | 대시보드 신설 |
| 4 | `GET /admin/models`·상세 + suspend/unsuspend | 모델·유저 화면 신설 |
| 5 | `/admin/staff` + `/admin/audit` + 관리자 관리 화면 | 관리자 승격 UI·기록 뷰 |

---

## 11. 안 하는 것 (YAGNI)

- 초대 메일·관리자 자가가입·MFA·별도 인증 스택 (결정 1)
- 역할 세분화(심사자/조회전용) — `admin` 하나로 시작. 필요해지면 `role` 값이 늘어나는 확장이다.
- 크레딧 수동 지급/차감, 라이선스 강제 해지, 계정 정지, 데이터 삭제 (결정 3)
- 잡·큐·에러율 모니터링 (결정 2) — 필요하면 대시보드에 섹션 하나 더 붙이는 확장이다.
- 롤업 테이블·크론 배치 (결정 6)
- 다크 모드 (결정 8)
- CSV 내보내기, 알림, 실시간 갱신(웹소켓)

---

## 12. 구현 전 확인할 것

1. **서버 DB 롤이 `auth.users` 를 읽는가.** 앱 코드에 선례가 없다. 막히면 `profiles` 이메일 미러 + 가입 트리거 수정으로 우회(6절).
2. **`fm_models.user_id` 가 null 인 모델**(플랫폼 대행 온보딩)이 실제로 있는지. 있으면 목록의 '계정 이메일' 칸이 비고, 이메일 검색에 안 걸린다 — 표시 규칙을 정해야 한다.
3. **prod 관리자 계정이 현재 몇 개인지.** 1개뿐이면 '최후 관리자 강등 금지' 가드가 배포 직후 곧바로 발동한다(정상 동작이지만, 두 번째 관리자를 먼저 만들어 두는 편이 낫다).
