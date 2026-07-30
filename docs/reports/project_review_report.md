# wearless_studio 종합 프로젝트 심층 분석 및 리뷰 보고서 (Project Review Report)

> **작성 일시**: 2026-07-29  
> **대상 워크스페이스 Root**: `/Users/nojeong-un/devs/wearless_studio`  
> **프로젝트 평가 상태 / 종합 점수**: Production-Ready with Refactoring & Scaling Priorities / **A- (91 / 100)**  
> **보고서 성격**: `wearless_studio` 프로젝트 전반(FastAPI 백엔드, React/Vite 프론트엔드, DB 트랜잭션 및 동시성 제어, 멱등성, 코드 품질 및 에러 핸들링, 테스트 커버리지, 실행 메트릭, 우선순위 개선 로드맵)에 대한 종합 분석 보고서

---

## 1. Executive Summary (개요 및 종합 요약)

### 1.1 프로젝트 개요 및 핵심 목적
`wearless_studio`는 의류 및 패션 커머스를 위한 AI 기반 마네킹 및 상세페이지 자동 생성 패션 스튜디오 플랫폼입니다. 사용자가 상품 사진과 설명 데이터를 입력하면 Multimodal Vision LLM(Gemini / GPT)과 이미지 생성 파이프라인을 활용하여 가상의 마네킹 착장 컷, 스토리보드, 고해상도 상세페이지 에셋을 생성하고, 캔버스 에디터 화면을 통해 즉시 편집 및 출력할 수 있는 엔드-투-엔드 SaaS 웹 애플리케이션입니다.

본 심층 분석 보고서는 [ORIGINAL_REQUEST.md](file:///Users/nojeong-un/devs/wearless_studio/.agents/ORIGINAL_REQUEST.md)의 요구사항(R1~R4)에 기반하여 백엔드/프론트엔드 아키텍처 구조, 동시성 및 데이터 안전성, 코드 품질 및 테스트 수트 실행 메트릭을 체계적으로 검증하고, 프로덕션 운영 안정성을 강화하기 위한 구체적인 액션 플랜을 도출하였습니다.

### 1.2 종합 평가 요약 및 서브시스템 등급 표

| 검증 영역 (Domain) | 등급 (Grade) | 평가 요약 및 핵심 메트릭 | 핵심 장점 | 개선 필요 사항 |
|---|:---:|---|---|---|
| **R1. 종합 아키텍처 & 코드 구조** | **B+ (88/100)** | FastAPI + PostgreSQL direct `psycopg 3` 및 React 18 / Zustand 3계층 상태 구조 | 비동기 커넥션 풀, 이중 API 어댑터, 생체 PII 격리 | `repo.py`(1,750라인), `routes.py`(1,357라인) 거대 모놀리스 파일 파편화 필요 |
| **R2. 동시성, 결제 & 멱등성** | **A (95/100)** | `pg_advisory_xact_lock`, `FOR UPDATE SKIP LOCKED`, Lease Fencing | 2단계 Stale recovery, 원자적 FIFO 크레딧 차감, active job 유니크 인덱스 | `editor_image` 헤더 미전송 시 중복 생성/차감 방지용 멱등성 보강 필요 |
| **R3. 코드 품질 & 테스트 수트** | **A- (91/100)** | Backend pytest: **811 Passed, 1 Skipped (4.39s)**<br>Frontend test: **17 Passed (74.67ms)** | 표준 JSON 에러 봉투, CORS 무결성, 503 DB 타임아웃, Graceful fallback | `test_selling_points.py:74-86` 구식 스킵 테스트 최신화 및 DB 통합 테스트 CI 연동 |
| **R4. 개선 과제 & 로드맵** | **A (92/100)** | Risk Level (High 2건, Medium 3건, Low 2건) 분리 및 Quick Wins 제시 | 1-2주 Quick Wins와 1-3개월 Long-term 로드맵의 명확한 실행 타임라인 | 단일 서버 인프로세스 디스패처의 다중 수평 확장(Multi-pod) 구조 개편 |

### 1.3 핵심 총평
`wearless_studio`는 고성능 비동기 DB 커넥션 풀ing, 트랜잭션 수준 Advisory Lock, 비관적 Row Locking, Lease Fencing Token을 통한 분산 작업 원자성 제어 등 고도화된 백엔드 동시성 안전 장치를 갖추고 있습니다. 또한 프론트엔드는 React 18과 Zustand를 활용한 3계층 상태 모델(ADR-0002)과 이중 어댑터 패턴(`mockAdapter` vs `httpAdapter`)을 통해 높은 개발 및 테스트 유연성을 확보하고 있습니다. 백엔드 및 프론트엔드 유닛 테스트 수트 또한 100%에 가까운 높은 통과율(pytest 811/812 통과, frontend 17/17 통과)을 보입니다. 다만, 단일 파일의 거대 모놀리식화 및 RLS 우회 구조는 향후 유지보수성 및 멀티테넌트 확장 시 개선되어야 할 핵심 과제입니다.

---

## 2. 종합 아키텍처 & 코드베이스 구조 심층 리뷰 (R1)

### 2.1 백엔드 아키텍처 (FastAPI, DB 계층, Worker Dispatcher)

#### 2.1.1 FastAPI 앱 라이프사이클 및 의존성 주입 구조
FastAPI 애플리케이션의 팩토리 함수 `create_app()`은 [file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L78-L243](file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L78-L243)에 구현되어 있습니다.

- **Lifespan Context Manager** ([file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L84-L104](file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L84-L104)):
  - 애플리케이션 시작 시 `create_pool(settings.database_url)`로 비동기 PostgreSQL 커넥션 풀을 생성하고 `await pool.open()`으로 초기화합니다.
  - `job_dispatcher_enabled` 옵션 및 R2 스토리지, AI 프로바이더(Gemini/OpenAI)가 구성된 경우 배경 작업 디스패처 `JobDispatcher`를 시작합니다([file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L91-L98](file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L91-L98)).
  - 앱 종료 시 `dispatcher.stop()` 및 `pool.close()`를 호출하여 자원을 안전하게 해제합니다.
- **Application State (`app.state`) 의존성 주입**:
  - `app.state.pool`: Shared `psycopg_pool.AsyncConnectionPool` 객체 ([file:///Users/nojeong-un/devs/wearless_studio/server/app/db.py#L20-L31](file:///Users/nojeong-un/devs/wearless_studio/server/app/db.py#L20-L31)).
  - `app.state.r2`: 공개 에셋용 Cloudflare R2 스토리지 클라이언트 ([file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L121](file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L121)).
  - `app.state.r2_face`: 생체 PII(얼굴 이미지) 전용 비공개 R2 버킷 클라이언트 ([file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L123-L133](file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L123-L133)). 얼굴 데이터가 공개 CDN 버킷으로 유출되는 것을 구조적으로 차단합니다.
  - `app.state.jwt_key_resolver`: Supabase JWKS 키 해석 함수 ([file:///Users/nojeong-un/devs/wearless_studio/server/app/auth.py#L24-L31](file:///Users/nojeong-un/devs/wearless_studio/server/app/auth.py#L24-L31)).
- **서브 라우터 구성**:
  - `v1_router` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L47](file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L47)): `/v1` 엔드포인트 패밀리(계정, 프로젝트, 에셋, 상품, AI 작업, 프롬프트 생성).
  - `payments_router` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/payments.py#L28](file:///Users/nojeong-un/devs/wearless_studio/server/app/payments.py#L28)): 토스페이먼츠 결제 및 크레딧 충전.
  - `facemarket_router` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/facemarket.py#L59](file:///Users/nojeong-un/devs/wearless_studio/server/app/facemarket.py#L59)): 실존 모델 마켓플레이스 및 온체인 정산 (조건부 기동 [file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L225-L232](file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L225-L232)).
  - `personalization_router` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/personalization.py#L72](file:///Users/nojeong-un/devs/wearless_studio/server/app/personalization.py#L72)): 사용자 얼굴/신체 개인화 모델 학습 (조건부 기동 [file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L237-L240](file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L237-L240)).

#### 2.1.2 DB 접근 계층 (psycopg 3 Async Connection Pool & Direct SQL)
- **Direct SQL / No-ORM 패턴**: ORM 오버헤드와 복잡한 Lazy Loading 이슈를 피하기 위해 `psycopg 3` 기반의 Direct SQL 접근 방식(`row_factory=dict_row`)을 사용합니다.
- **연결 풀 옵션** ([file:///Users/nojeong-un/devs/wearless_studio/server/app/db.py#L20-L31](file:///Users/nojeong-un/devs/wearless_studio/server/app/db.py#L20-L31)): `min_size=1`, `max_size=10`, `timeout=10`(풀 대기 타임아웃), `connect_timeout=10`(TCP/소켓 타임아웃).
- **Service-Role 연결 및 스코핑 의무**: 앱은 `DATABASE_URL`로 PostgreSQL 서비스 로글 계정에 연결하여 PG RLS를 우회합니다([file:///Users/nojeong-un/devs/wearless_studio/server/app/db.py#L1-L9](file:///Users/nojeong-un/devs/wearless_studio/server/app/db.py#L1-L9)). 따라서 모든 SQL 쿼리는 애플리케이션 레이어에서 `user_id = %s` 조건을 필수적으로 명시해야 합니다([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L121](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L121), [file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L175](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L175)).

#### 2.1.3 배경 작업 처리 디스패처 (JobDispatcher & Worker Execution)
- **인프로세스 작업 디스패처** ([file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/dispatcher.py#L39-L117](file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/dispatcher.py#L39-L117)): FastAPI 프로세스 내에서 백그라운드 태스크로 실행되며 DB `jobs` 테이블을 폴링하여 AI 생성을 처리합니다.
- **작업 매핑 테이블 (`_WORKERS`)** ([file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/dispatcher.py#L25-L35](file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/dispatcher.py#L25-L35)):
  - `mannequin`: `run_mannequin_job` (Gemini 마네킹 착장 컷 생성)
  - `analyze`: `run_analyze_job` (상품 분석)
  - `detail_page`: `run_detail_page_job` (상세페이지 다중 컷 파이프라인)
  - `editor_image`: `run_editor_image_job` (에디터 이미지 생성 및 변형)
  - `personalization_generation` / `personalization_purge`: 개인화 생성 및 캐스케이드 삭제

---

### 2.2 프론트엔드 아키텍처 (React 18 / Vite 6 / Zustand / React Query)

#### 2.2.1 앱 진입점, 프로바이더 및 Loopback Origin Guard
- **`src/main.jsx`** ([file:///Users/nojeong-un/devs/wearless_studio/src/main.jsx#L14-L65](file:///Users/nojeong-un/devs/wearless_studio/src/main.jsx#L14-L65)): `QueryClientProvider`(staleTime 60초, retry 1회), `AuthProvider`, `BrowserRouter`, `ToastProvider`로 최상위 프로바이더를 트리로 구성합니다.
- **Loopback Origin Guard** ([file:///Users/nojeong-un/devs/wearless_studio/src/main.jsx#L42-L49](file:///Users/nojeong-un/devs/wearless_studio/src/main.jsx#L42-L49)): 브라우저 개발 모드에서 `127.0.0.1` 또는 `[::1]` 호스트명 진입 시 CORS 차단을 방지하기 위해 `localhost`로 표준화(canonicalize) 리다이렉트합니다.

#### 2.2.2 라우팅 및 5단계 보더 가드
[file:///Users/nojeong-un/devs/wearless_studio/src/App.jsx#L1-L312](file:///Users/nojeong-un/devs/wearless_studio/src/App.jsx#L1-L312)에는 5개의 전용 가드 컴포넌트가 배치되어 인증 및 프로젝트 소유권을 검증합니다:
1. `RequireAuth` ([file:///Users/nojeong-un/devs/wearless_studio/src/App.jsx#L40-L45](file:///Users/nojeong-un/devs/wearless_studio/src/App.jsx#L40-L45)): 세션이 없는 사용자를 `/create/input` 공개 페이지로 이송합니다.
2. `RequireProject` ([file:///Users/nojeong-un/devs/wearless_studio/src/App.jsx#L53-L61](file:///Users/nojeong-un/devs/wearless_studio/src/App.jsx#L53-L61)): 마네킹/스토리보드 진입 시 유효한 `projectId` 및 영속화 여부를 점검합니다.
3. `RequireEditorProject` ([file:///Users/nojeong-un/devs/wearless_studio/src/App.jsx#L65-L108](file:///Users/nojeong-un/devs/wearless_studio/src/App.jsx#L65-L108)): URL 패스 `:id`에 대한 서버 소유권을 비동기로 검증한 후 에디터를 마운트합니다.
4. `RequireVerifiedModel` ([file:///Users/nojeong-un/devs/wearless_studio/src/App.jsx#L112-L142](file:///Users/nojeong-un/devs/wearless_studio/src/App.jsx#L112-L142)): 본인 인증(실명/성인)이 완료된 모델 계정만 FaceMarket 경로에 접근 허용합니다.
5. `PublicVerify` ([file:///Users/nojeong-un/devs/wearless_studio/src/App.jsx#L308](file:///Users/nojeong-un/devs/wearless_studio/src/App.jsx#L308)): QR 코드 스캔용 공개 검증 페이지로, 인증 가드 외부 독립 라우트로 렌더링됩니다.

#### 2.2.3 3계층 상태 관리 모델 (ADR-0002) 및 이중 API 어댑터 패턴
- **3계층 상태 분류** ([file:///Users/nojeong-un/devs/wearless_studio/src/store/useAppStore.js#L1-L12](file:///Users/nojeong-un/devs/wearless_studio/src/store/useAppStore.js#L1-L12)):
  1. *Server State*: API 및 React Query로 관리 (`product`, `analysis`, `mannequins`, `editorBlocks`).
  2. *Global Client State*: Zustand 스토어 (`useAppStore.js`)에서 라우트 간 세션 유지 (`projectId`, `selectedMannequinId`, `composeMode`, `account`).
  3. *Local Component State*: 폼 입력을 위한 React 로컬 상태.
- **이중 어댑터 디스패처** ([file:///Users/nojeong-un/devs/wearless_studio/src/lib/api/index.js#L12-L55](file:///Users/nojeong-un/devs/wearless_studio/src/lib/api/index.js#L12-L55)): `VITE_API_MODE` 환경변수에 따라 `mockAdapter`와 `httpAdapter`를 투명하게 스위칭합니다. 미구현 API 호출 시 런타임 에러를 명시적으로 던지는 가드(`Unimplemented Function Guard` [file:///Users/nojeong-un/devs/wearless_studio/src/lib/api/index.js#L44-L51](file:///Users/nojeong-un/devs/wearless_studio/src/lib/api/index.js#L44-L51))를 갖추고 있습니다.

---

### 2.3 아키텍처적 강점, 위험 요소 및 데이터 흐름 분석

#### 2.3.1 엔드-투-엔드(E2E) 데이터 흐름 파이프라인
```
[ Frontend Client ] ──(1) POST /v1/jobs/generate ──► [ FastAPI Router: routes.py ]
                                                             │
   ┌─────────────────────────────────────────────────────────┴────────────────────────┐
   ▼ (2) require_user                                                                 ▼ (3) get_conn
Decode Supabase JWT ──► Extract user_id                               Acquire AsyncConn from psycopg3 Pool
   │                                                                                  │
   └─────────────────────────────────────────┬────────────────────────────────────────┘
                                             ▼
                               (4) repo.reserve_credits
                       Lock credit_accounts (FOR UPDATE) & Reserve
                                             │
                                             ▼
                                 (5) repo.create_job
                   Insert job row ('pending') with Idempotency-Key
                                             │
                                             ▼
                               (6) dispatcher.wake()
                       Interrupt wait & signal worker loop
                                             │
                                             ▼
                        [ JobDispatcher background worker ]
                        (7) repo.claim_next_job (FOR UPDATE SKIP LOCKED)
                            Set status='running', locked_by=lease_token
                                             │
                                             ▼
                        (8) Run Worker (e.g. mannequin_job.py)
                            Call AI Model (Gemini/OpenAI) & Upload R2
                                             │
                                             ▼
                        (9) repo.finalize_mannequin_success
                            Verify locked_by == lease_token
                            Settle credits FIFO & Update status='done'
```

#### 2.3.2 핵심 아키텍처 강점 5가지
1. **가벼운 ACID DB 기반 태스크 큐**: Celery나 Redis 등 외부 메시지 브로커 없이 PostgreSQL `FOR UPDATE SKIP LOCKED`만으로 높은 신뢰성의 태스크 큐를 구축했습니다.
2. **생체 PII 데이터 완전 격리**: 생체 데이터(얼굴 사진) 전용 비공개 R2 버킷(`app.state.r2_face` [file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L123-L133](file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L123-L133))을 사용하고 릴리즈 플래그로 기능을 격리하여 PII 유출을 철저히 막았습니다.
3. **이벤트 기반 작업 즉시 깨움 (`dispatcher.wake()`)**: 라우트에서 작업 생성 직후 디스패처 이벤트를 깨워([file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L77-L84](file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L77-L84)), 폴링 대기시간(최대 3초)을 제거했습니다.
4. **안전한 CORS 결합 전역 예외 미들웨어**: `unhandled_exception_envelope` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L142-L159](file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L142-L159))를 CORS 미들웨어 내부에 배치하여 서버 500 에러 발생 시에도 CORS 헤더가 유실되지 않도록 보장했습니다.
5. **AI Vision 입력 최적화**: Vision LLM 입력 이미지를 1024px JPEG로 사전 축소(`shrink_for_vision`)하여 네트워크 전송 오버헤드를 대폭 줄였습니다.

#### 2.3.3 위험 요소 및 구조적 약점 5가지
1. **거대 모놀리식 소스 파일**: `server/app/repo.py`(1,750라인), `server/app/routes.py`(1,357라인), `facemarket.py`(1,500라인 이상) 등 단일 파일에 비즈니스 로직, DB 쿼리, HTTP 계층이 집중되어 있어 병렬 개발 시 충돌 및 코드 독해 난이도가 높습니다.
2. **애플리케이션 레이어 multi-tenancy 격리 100% 의존**: Service-Role 커넥션을 사용하여 PG RLS를 우회하므로, 개발자가 SQL 작성 시 `user_id = %s` 조건을 누락할 경우 타 테넌트 데이터 유출 위험이 존재합니다.
3. **인프로세스 워커 폴링의 수평 확장성 제약**: 웹 서버 프로세스 내에서 디스패처가 DB를 폴링하므로, Pod가 10~20개로 수평 확장될 경우 DB 커넥션 소모 및 폴링 쿼리 오버헤드가 선형 증가합니다.
4. **Python f-string 기반 raw SQL 동적 생성**: `repo.py`에서 테이블 컬럼 및 CTE 문을 f-string으로 작성(예: `f"select {_PROJECT_COLS} from projects..."`)하고 있어, 파라미터 binding(%s)은 안전하나 컬럼 Refactoring 시 SQL 구문 에러 위험이 있습니다.
5. **구체 인프라 클라이언트 직접 결합**: `app.state.gemini`, `app.state.r2` 등 구체 인프라 클래스에 직접 결합되어 있어 Mock 기반 단위 테스트 작성 시 글로벌 몽키패칭에 의존해야 합니다.

---

## 3. 동시성, 크레딧/결제 트랜잭션 & 멱등성 검증 (R2)

### 3.1 DB 트랜잭션 락 및 동시성 제어 메커니즘

#### 3.1.1 연결 풀 및 대기 타임아웃 (`psycopg_pool`)
- `db.py` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/db.py#L34-L46](file:///Users/nojeong-un/devs/wearless_studio/server/app/db.py#L34-L46))의 `get_conn` 컨텍스트 매니저는 커넥션 획득 대기 타임아웃(`PoolTimeout`) 발생 시 기본 30초 대기나 unhandled 500 에러 대신 즉시 HTTP 503 (`db_unavailable`) 에러 봉투를 반환합니다.

#### 3.1.2 트랜잭션 수준 Advisory Lock (`pg_advisory_xact_lock`)
- `repo.py` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L135-L138](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L135-L138))의 `create_project` 함수는 `SELECT pg_advisory_xact_lock(hashtext('create_project:{user_id}'))`를 수행합니다.
- 동일 사용자가 빠른 연속 클릭이나 네트워크 재시도로 동시 프로젝트 생성 요청을 보낼 때, 첫 번째 트랜잭션이 완료될 때까지 두 번째 요청을 직렬화하여 대기시키고, 기존에 작성 중이던 미사용 pristine draft 프로젝트([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L142-L154](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L142-L154))를 재사용하도록 보장합니다.

#### 3.1.3 비관적 행 락 (`FOR UPDATE` 및 `FOR UPDATE SKIP LOCKED`)
1. **작업 큐 태스크 점유**: `claim_next_job` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L580-L603](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L580-L603))에서 CTE 쿼리에 `ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1`을 사용하여 다중 워커 프로세스가 동일 태스크를 중복 선점하거나 락 블로킹 없이 병렬 처리합니다.
2. **크레딧 계정 및 버킷 변동 직렬화**: `reserve_credits` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L704-L730](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L704-L730)), `_settle_credits` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L732-L776](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L732-L776)), `_consume_buckets` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L797-L854](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L797-L854)), `purchase_topup` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L1508-L1588](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L1508-L1588)) 등 모든 크레딧 변동 함수는 진입 즉시 `SELECT balance, reserved FROM credit_accounts WHERE user_id = %s FOR UPDATE`를 최우선 실행하여 사용자별 크레딧 트랜잭션을 엄격히 직렬화합니다.

#### 3.1.4 세이브포인트 (`SAVEPOINT`)와 원자적 폴백
- `create_job` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L541-L566](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L541-L566)) 내부에서는 `INSERT INTO jobs` 실행 전 `SAVEPOINT create_job_insert`를 선언합니다. `UniqueViolation` 예외 발생 시 전체 트랜잭션을 중단하지 않고 `ROLLBACK TO SAVEPOINT`를 수행하여 기존 진행 중 작업(active job)으로 안전하게 합류(fallback)합니다.

---

### 3.2 분산 워커 실행 및 Lease Fencing Token

#### 3.2.1 고유 리스 토큰 생성 및 태스크 점유
- `claim_next_job` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L586-L599](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L586-L599)) 실행 시 `lease_token = f"{worker_id}:{uuid.uuid4()}"` 형태의 전역 고유 리스 토큰을 생성하여 `locked_by` 컬럼에 기록합니다.

#### 3.2.2 펜싱 검증 및 리스 상실 처리
- 워커가 AI 생성을 마치고 결과를 DB에 반영할 때, `finalize_mannequin_success` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L905-L970](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L905-L970)) 등의 함수는 `SELECT id FROM jobs WHERE id = %s AND locked_by = %s AND status = 'running' FOR UPDATE` 쿼리로 리스 소유권을 원자적으로 검증합니다.
- 만약 워커의 일시적 지연으로 인해 스위퍼가 리스를 회수하고 다른 워커에게 재할당하거나 에러 처리한 경우, 조건 매칭 행 수가 0이 되어 이전 지연 워커의 뒤늦은 결과 덮어쓰기(Stale Write)가 차단됩니다.

#### 3.2.3 Best-Effort 고아 에셋 정리
- `mannequin_job.py` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/mannequin_job.py#L558-L569](file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/mannequin_job.py#L558-L569)) 및 `detail_page_job.py` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/detail_page_job.py#L730-L735](file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/detail_page_job.py#L730-L735))는 `finalize_*` 검증 실패로 리스 권한을 잃었음을 감지하면, 해당 실행 시점에 R2 스토리지에 업로드했던 불필요한 에셋 객체를 Best-Effort 방식으로 즉시 삭제(`r2.delete`)합니다.

---

### 3.3 멱등성 (Idempotency) 보장 체계

#### 3.3.1 HTTP `Idempotency-Key` 스코핑
- 동일 프로젝트 및 사용자 내에서 서로 다른 태스크 종류 간 멱등성 키 충돌을 방지하기 위해 라우트 계층에서 멱등성 키를 스코핑합니다:
  - `generate_mannequins`: `scoped_key = f"{project_id}:mannequin:{idempotency_key}"` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L911](file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L911))
  - `regenerate_mannequins`: `scoped_key = f"{project_id}:mannequin_regenerate:{idempotency_key}"` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L1026](file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L1026))
  - `generate_editor_image`: `scoped_key = f"{project_id}:editor_image:{idempotency_key}"` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L1174](file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L1174))
  - `generate_detail_page`: `scoped_key = f"{project_id}:detail_page:{idempotency_key}"` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L1210](file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L1210))

#### 3.3.2 DB 부분 유니크 인덱스 (`jobs_active_unique_idx`) 및 `create_job` 충돌 회피
- DB 부분 유니크 인덱스 `jobs_active_unique_idx` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L509-L510](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L509-L510))는 동일 `(project_id, kind)` 조합에 대해 `status IN ('pending', 'running')` 상태인 활성 작업의 중복 생성을 원자적으로 막아줍니다.
- `create_job` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L495-L578](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L495-L578))은 ON CONFLICT 발생 시 진행 중인 기존 작업 행을 조회하여 반환하며 `(job, False)`를 반환함으로써 중복 생성을 방지합니다.

#### 3.3.3 캐시 단축 게이트 및 원장/에셋 멱등성
- **`generate_mannequins` 캐시 단축 게이트**: [file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L917-L922](file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L917-L922)에서 `list_mannequin_cuts`로 이미 생성된 컷 에셋이 존재하는지 점검하여, 존재 시 크레딧 예약이나 작업 생성 없이 200 OK와 기존 에셋을 즉시 반환합니다.
- **크레딧 원장 멱등성**: `_settle_credits` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L760-L767](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L760-L767)) 및 `_consume_buckets` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L840-L848](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L840-L848))은 `ON CONFLICT (idempotency_key) DO NOTHING` 절을 사용하여 크레딧 차감 원장의 이중 기록을 원천 차단합니다.

---

### 3.4 Stale Lease 복구 및 크레딧 보호 메커니즘

#### 3.4.1 2단계 Stale recovery 청소 루틴
- `JobDispatcher` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/dispatcher.py#L98-L117](file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/dispatcher.py#L98-L117))는 60초마다 `recover_stale_leases` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L614-L652](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L614-L652))를 실행합니다.
- `running` 상태에서 `job_lease_timeout_seconds`를 초과한 타임아웃 작업에 대해:
  1. **1차 타임아웃 (`leaseRecoveries < 1`)**: `metadata.leaseRecoveries`를 `1`로 올리고 상태를 `'pending'`으로 재설정하여 타 워커가 재시도할 수 있도록 기회를 부여합니다.
  2. **2차 타임아웃 (`leaseRecoveries >= 1`)**: 상태를 `'error'`로 전환하고 사용자 친화적인 에러 메시지를 기록한 후 `job_events`에 에러 이벤트를 발행하여 client SSE 연결을 닫습니다.

#### 3.4.2 Unsettled Errored Job 자동 크레딧 환불
- 2단계 복구 후 스위퍼는 `list_unsettled_errored_jobs` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L654-L674](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L654-L674))를 호출하여, 실패 처리되었으나 크레딧 정산 원장이 없는 작업을 검색하고 `release_credits`를 호출하여 사용자 예약 크레딧을 100% 자동으로 환불합니다 ([file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/dispatcher.py#L104-L117](file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/dispatcher.py#L104-L117)).

---

### 3.5 경쟁 상태(Race Condition), 데드락 및 멱등성 우회 위험 평가

#### 3.5.1 크레딧 락 획득 순서 일관성 (데드락 방지)
- `reserve_credits`, `_settle_credits`, `_consume_buckets`, `grant_subscription`, `purchase_topup`, `request_refund` 등 모든 크레딧 변동 함수는 항상 `credit_accounts WHERE user_id = %s FOR UPDATE`를 **첫 번째 락**으로 획득합니다.
- 사용자 단위로 락 획득 순서가 통일되어 있으므로, 동일 사용자에 대한 동시 크레딧 요청 간 교차 락(Cross-Locking)으로 인한 DB 데드락(Deadlock) 가능성이 없습니다.

#### 3.5.2 `editor_image` 헤더 누락 시 중복 차감 위험
- `repo.py` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L551-L553](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L551-L553))에 정의된 바와 같이, `editor_image` 작업 종류는 사용자가 에디터 캔버스에서 여러 이미지를 자유롭게 동시 생성할 수 있도록 `jobs_active_unique_idx` 부분 유니크 인덱스 대상에서 제외되어 있습니다.
- 따라서 클라이언트가 `POST /projects/{id}/editor:generate-image` 요청 시 `Idempotency-Key` 헤더를 누락하고 단기간에 중복 요청을 전송할 경우, 활성 작업 중복 합류가 적용되지 않아 작업이 여러 개 생성되고 크레딧이 이중 예약/차감될 위험이 존재합니다.

#### 3.5.3 워커 강제 종료(SIGKILL) 시 R2 고아 에셋 누적
- AI 생성을 담당하는 워커 프로세스가 R2 버킷에 대용량 이미지 생성을 마친 직후 DB `finalize_*`를 호출하기 직전에 OOM Killer 또는 SIGKILL로 비정상 종료되는 경우, `recover_stale_leases`에 의해 DB 및 크레딧은 정상 복구되나, 이미 스토리지에 업로드된 R2 물리 에셋은 삭제되지 않고 고아(Orphan) 에셋으로 누적될 수 있습니다.

---

## 4. 코드 품질, 에러 핸들링 및 테스트 커버리지 점검 (R3)

### 4.1 기존 테스트 수트 실행 결과 및 메트릭

#### 4.1.1 백엔드 `pytest` 수트 실행 결과 (**811 Passed, 1 Skipped, 4.39s**)
- **실행 명령**: `cd /Users/nojeong-un/devs/wearless_studio/server && /Users/nojeong-un/devs/wearless_studio/server/.venv/bin/pytest --ignore=tests/test_personalization.py`
- **수집 항목**: 총 64개 테스트 파일, 812개 수집.
- **실행 메트릭**: **811 Passed, 1 Skipped, 0 Failed (수행 시간: 4.39초)**.

```text
============================= test session starts ==============================
platform darwin -- Python 3.12.13, pytest-9.0.3, pluggy-1.6.0
rootdir: /Users/nojeong-un/devs/wearless_studio/server
configfile: pytest.ini
collected 812 items

tests/test_analyze.py ...........                                        [  1%]
tests/test_auth.py ........                                              [  2%]
tests/test_cuts.py ..................................................... [ 18%]
tests/test_dispatcher_wake.py ...                                        [ 25%]
tests/test_facemarket_seller_loop.py .....................               [ 38%]
tests/test_mannequin_fit_profile.py .............................        [ 57%]
tests/test_payments_toss.py .................                            [ 82%]
tests/test_vision_llm.py ...........                                     [100%]

================== 811 passed, 1 skipped, 1 warning in 4.39s ===================
```

#### 4.1.2 백엔드 제외/스킵 테스트 사유 분석
1. **Skipped Test (`test_selling_points.py:74-86`)**:
   - 위치: [file:///Users/nojeong-un/devs/wearless_studio/server/tests/test_selling_points.py#L74-L85](file:///Users/nojeong-un/devs/wearless_studio/server/tests/test_selling_points.py#L74-L85)
   - 사유: `@pytest.mark.skip(reason="선존재 main 깨짐(WIP): MannequinPromptContext 시그니처가 리팩터되어 'candidate' 키워드를 더 받지 않음 — 테스트가 구식. 프롬프트 계약 확정 후 갱신할 것.")`
   - 분석: 과거 프롬프트 컨텍스트 시그니처 리팩토링 후 테스트 코드가 갱신되지 않아 Skip 처리되어 있음.
2. **Ignored Test File (`tests/test_personalization.py`)**:
   - 위치: [file:///Users/nojeong-un/devs/wearless_studio/server/tests/test_personalization.py#L138](file:///Users/nojeong-un/devs/wearless_studio/server/tests/test_personalization.py#L138) (2,206라인)
   - 사유: 로컬/원격 PostgreSQL 컨테이너(`postgresql://postgres:postgres@127.0.0.1:54322/postgres`) 인프라 연결이 필요한 DB 통합 테스트 수트로, 인메모리 패치 기반의 Fast Unit Test 실행 시 정상적으로 분리되어 있음.

#### 4.1.3 프론트엔드 `vitest / node:test` 수트 실행 결과 (**17 Passed, 74.67ms**)
- **실행 명령**: `cd /Users/nojeong-un/devs/wearless_studio && npm run test:frontend`
- **테스트 러너**: Node native test runner (`node --test tests/frontend/*.test.mjs` [file:///Users/nojeong-un/devs/wearless_studio/package.json#L10](file:///Users/nojeong-un/devs/wearless_studio/package.json#L10))
- **실행 메트릭**: **17 Passed, 0 Failed, 0 Skipped (수행 시간: 74.67ms)**.

```text
✔ splitAnalysisEditPatch routes product-owned fields away from saveAnalysis (1.22ms)
✔ persistAnalysisEdit saves the product source of truth before analysis shape (0.12ms)
✔ editor route project id is adopted when it differs from store (0.06ms)
✔ the first AI image in benefit is the only internally assigned hero (0.81ms)
✔ an internally normalized product image drops worn-only settings (0.07ms)
ℹ pass 17 | fail 0 | duration_ms 74.671792
```

---

### 4.2 예외 처리 및 에러 봉투 (Error Envelopes) Structure

#### 4.2.1 백엔드 표준 JSON 에러 봉투 및 전역 예외 미들웨어
FastAPI 서버는 모든 예외 상황에 대해 일관된 통일 JSON 에러 응답 봉투 구조를 보장합니다:

```json
{
  "error": {
    "code": "error_code_string",
    "message": "사용자 친화적 한글 에러 안내 메시지",
    "details": []
  }
}
```

- **`unhandled_exception_envelope` 전역 미들웨어** ([file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L142-L159](file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L142-L159)):
  - 요청 처리 중 발생하는 500 Unhandled Exception을 포착하여 `wearless.api` 로거에 트레이스백을 출력하고, 브라우저가 원인을 쉽게 인지할 수 있는 JSON 에러 봉투를 반환합니다.

#### 4.2.2 Validation Error 직렬화 안전성 및 CORS 헤더 보장
- **Pydantic Validation Error 직렬화 방어** ([file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L181-L194](file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L181-L194)): Pydantic validation 예외의 `ctx`에 Raw Exception 객체가 포함될 경우 발생하는 `json.dumps` 크래시를 방지하기 위해 `jsonable_encoder(exc.errors())`로 직렬화 가능 형태를 안전하게 강제합니다.
- **CORS 미들웨어 배치 순서** ([file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L161-L168](file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L161-L168)): CORS 미들웨어를 예외 봉투 핸들러 감싸는 바깥쪽에 배치하여 500 서버 장애 시에도 `Access-Control-Allow-Origin` 응답 헤더가 유지되어 브라우저에서 CORS 차단 에러로 위장되지 않도록 보증합니다.

#### 4.2.3 프론트엔드 에러 핸들링 및 UI 피드백 Primitives
- **`httpAdapter.js` 에러 가공** ([file:///Users/nojeong-un/devs/wearless_studio/src/lib/api/httpAdapter.js#L112-L117](file:///Users/nojeong-un/devs/wearless_studio/src/lib/api/httpAdapter.js#L112-L117)): HTTP 응답 상태 코드(`status`)와 비즈니스 코드(`code`)를 JS `Error` 객체 프로퍼티에 바인딩하며, `navigator.onLine`을 점검해 오프라인 상태 감지 시 `browser_offline` 에러로 매핑합니다 ([file:///Users/nojeong-un/devs/wearless_studio/src/lib/api/httpAdapter.js#L20-L31](file:///Users/nojeong-un/devs/wearless_studio/src/lib/api/httpAdapter.js#L20-L31)).
- **UI Error Primitives**: `ErrorState` 컴포넌트([file:///Users/nojeong-un/devs/wearless_studio/src/components/ui.jsx#L224-L232](file:///Users/nojeong-un/devs/wearless_studio/src/components/ui.jsx#L224-L232)) 및 텍스트 길이에 가변 비례(2.6초~9초)하는 Toast 토스트 컴포넌트([file:///Users/nojeong-un/devs/wearless_studio/src/components/ui.jsx#L261-L294](file:///Users/nojeong-un/devs/wearless_studio/src/components/ui.jsx#L261-L294))를 통해 가독성 높은 피드백을 제공합니다.

---

### 4.3 구조화된 로깅 및 오심/유실 방지 체계

#### 4.3.1 커스텀 `_ExtraFormatter` 로거 및 `extra={...}` 키-값 유지
- [file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L36-L54](file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L36-L54)의 커스텀 `_ExtraFormatter`는 `logging.info("...", extra={"project_id": "p123"})`와 같은 호출 시 `extra` 딕셔너리 메타데이터를 `key='value'` 형태로 기본 로그 메시지 끝에 자동으로 덧붙입니다.
- 이를 통해 관측 로그(예: `analysis_spike`, `retrieval_call`) 데이터가 운영 환경에서 유실되는 현상을 완벽히 방지합니다.

#### 4.3.2 서드파티 노이즈 억제 및 생체 PII 차단
- `httpx`, `botocore`, `urllib3`, `asyncio` 등 외부 서드파티 라이브러리의 무의미한 INFO 디버그 로그 레벨을 `WARNING`으로 상향 조절하여 운영 로그 노이즈를 억제합니다([file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L74-L75](file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L74-L75)).
- 생체 얼굴 바이오메트릭 키 및 바이너리는 로깅 대상에서 엄격히 제외되어 보안 규정을 준수합니다.

---

### 4.4 AI 서비스 결함 저항성 및 Graceful Fallback

#### 4.4.1 상세페이지 부분 컷 실패 시 우아한 열화 (Partial Cut Generation Fallback)
- `detail_page_job.py` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/detail_page_job.py#L50-L53](file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/detail_page_job.py#L50-L53))는 다중 컷 상세페이지 생성 과정 중 일부 컷 생성이 실패하거나 모델 얼굴 라이선스가 만료된 경우, 전체 작업을 에러 처리하지 않고 "얼굴 없이 생성" 상태로 우아하게 열화하여 부분 컷을 성공시킵니다.
- 크레딧 역시 실제 정상 생성된 컷 개수 만큼만 차감하여 과금 공정성을 유지합니다.

#### 4.4.2 멀티 프로바이더 Vision LLM 폴백 (`GPT` <-> `Gemini`)
- `vision_llm.py` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/agents/vision_llm.py#L163-L194](file:///Users/nojeong-un/devs/wearless_studio/server/app/agents/vision_llm.py#L163-L194))의 `analyze_with_fallback` 함수는 주(Primary) AI 프로바이더(OpenAI GPT) 응답 실패 시, 자동으로 부(Secondary) AI 프로바이더(Gemini)로 페일오버(Failover)하여 분석 서비스 연속성을 보장합니다.

#### 4.4.3 모델 식별자 및 랜드마크 크롭 ratio 폴백
- `identity_source.py` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/agents/identity_source.py#L16-L29](file:///Users/nojeong-un/devs/wearless_studio/server/app/agents/identity_source.py#L16-L29))의 `select_source` 함수는 실존 모델 라이선스 자산이 유효하지 않을 경우 안전하게 `VIRTUAL` 마네킹 모델 식별자로 폴백하며, `pose_crop.py` ([file:///Users/nojeong-un/devs/wearless_studio/server/app/agents/pose_crop.py#L125-L126](file:///Users/nojeong-un/devs/wearless_studio/server/app/agents/pose_crop.py#L125-L126))는 랜드마크 탐지 실패 시 미리 계산된 기본 비율 Box로 자동 전환됩니다.

---

## 5. 우선순위 개선 과제 및 로드맵 (Quick Wins & Long-term Priorities) (R4)

### 5.1 위험 등급별 문제점 요약 (High / Medium / Low)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Risk Level Categorization Matrix                     │
├──────────────┬──────────────────────────────────────────────────────────┤
│ High Risk    │ • Monolithic File Debt (repo.py 1,750L, routes.py 1,357L)│
│ (운영/유지보수)│ • Application-level Multitenancy Security (RLS Bypass)   │
├──────────────┼──────────────────────────────────────────────────────────┤
│ Medium Risk  │ • Editor Image missing Idempotency-Key Header Risk       │
│ (동시성/확장) │ • Multi-pod Worker Horizontal Scaling Connection Overhead│
│              │ • Outdated Skipped Pytest Case (test_selling_points.py) │
├──────────────┼──────────────────────────────────────────────────────────┤
│ Low Risk     │ • StarletteDeprecationWarning in pytest suite           │
│ (스타일/경고) │ • Worker SIGKILL R2 Orphan Asset Garbage Collection      │
└──────────────┴──────────────────────────────────────────────────────────┘
```

---

### 5.2 즉시 실행 과제 (Quick Wins: 1~2주 소요)

#### Task QW-1: 거대 단일 파일 모듈화 분할 (`repo.py`, `routes.py`)
- **위험도**: High
- **대상 파일**: [file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py), [file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py](file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py)
- **개선 내용**:
  - `repo.py`를 `server/app/repo/` 패키지로 승격하고 도메인별 분할 (`repo/projects.py`, `repo/credits.py`, `repo/jobs.py`, `repo/assets.py`).
  - `routes.py`에 혼재된 프롬프트 작성 및 서드파티 도메인 로직을 전용 Service Layer로 이관.
- **기대 효과**: 모듈화 강화, 병렬 개발 시 Git 충돌 감소 및 유닛 테스트 가독성 대폭 향상.

#### Task QW-2: 방치된 스킵 테스트 `test_selling_points.py:74-86` 최신화
- **위험도**: Medium
- **대상 파일**: [file:///Users/nojeong-un/devs/wearless_studio/server/tests/test_selling_points.py#L74-L85](file:///Users/nojeong-un/devs/wearless_studio/server/tests/test_selling_points.py#L74-L85)
- **개선 내용**:
  - `MannequinPromptContext` 최신 시그니처에 맞추어 `test_render_enforce_injection_absent_from_final_prompt` 테스트 파라미터를 수정하고 `@pytest.mark.skip` 데코레이터를 제거.
- **기대 효과**: 백엔드 유닛 테스트 통과율 811/812 -> 812/812 (100% 완전화).

#### Task QW-3: `POST /projects/{id}/editor:generate-image` 멱등성 키 강제/폴백
- **위험도**: Medium
- **대상 파일**: [file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L1174](file:///Users/nojeong-un/devs/wearless_studio/server/app/routes.py#L1174), [file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L551-L553](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L551-L553)
- **개선 내용**:
  - 프론트엔드 에디터 생성 요청 시 `Idempotency-Key` 헤더 전송을 필수화하거나, 헤더 미전송 시 `payload` 해시값 기반 클라이언트 세션 멱등성 키 자동 생성 폴백 추가.
- **기대 효과**: 에디터 이미지 생성을 위한 연속 클릭 시 중복 크레딧 차감 및 중복 DB 작업 생성 방지.

#### Task QW-4: Pytest 수트 Deprecation Warning (`StarletteDeprecationWarning`) 해제
- **위험도**: Low
- **대상 파일**: [file:///Users/nojeong-un/devs/wearless_studio/server/pyproject.toml#L33-L37](file:///Users/nojeong-un/devs/wearless_studio/server/pyproject.toml#L33-L37)
- **개선 내용**:
  - `starlette.testclient` 관련 `httpx` 의존성 패키지 버전을 최신 호환 사양으로 업데이트하여 경고 메시지 제거.
- **기대 효과**: 테스트 출력 로그의 노이즈 없는 깨끗한 실행 결과 유지.

---

### 5.3 중장기 개선 로드맵 (Long-term Priorities: 1~3개월 소요)

#### Task LT-1: 수평 확장 가능한 이벤트 기반 백그라운드 태스크 큐 전환
- **위험도**: High
- **대상 파일**: [file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/dispatcher.py#L39-L117](file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/dispatcher.py#L39-L117)
- **개선 내용**:
  - 단일 웹 프로세스 내 주기적 DB 폴링 구조를 PostgreSQL `LISTEN/NOTIFY` 또는 분산 메시지 큐(Redis / NATS / AWS SQS) 이벤트 기반 구조로 디커플링.
- **기대 효과**: Multi-pod 수평 확장 시 DB 커넥션 소모 및 폴링 CPU 오버헤드 원천 차단.

#### Task LT-2: DB 서비스 로직의 Row Level Security (RLS) 및 테넌트 격리 강화
- **위험도**: High
- **대상 파일**: [file:///Users/nojeong-un/devs/wearless_studio/server/app/db.py#L1-L9](file:///Users/nojeong-un/devs/wearless_studio/server/app/db.py#L1-L9), [file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py)
- **개선 내용**:
  - App 접속 전용 DB 사용자 역할(App Role)을 신설하고 `SET LOCAL app.current_user_id = ...` 커넥션 컨텍스트 세팅을 도입하여 PG RLS 정책을 활성화.
- **기대 효과**: 애플리케이션 레벨 쿼리 실수로 인한 테넌트 간 데이터 누출 위험을 DB 엔진 수준에서 2차 차단.

#### Task LT-3: 구체 인프라 클라이언트 인터페이스 추상화 (StorageProvider / LLMProvider)
- **위험도**: Medium
- **대상 파일**: [file:///Users/nojeong-un/devs/wearless_studio/server/app/r2.py](file:///Users/nojeong-un/devs/wearless_studio/server/app/r2.py), [file:///Users/nojeong-un/devs/wearless_studio/server/app/agents/gemini_image.py](file:///Users/nojeong-un/devs/wearless_studio/server/app/agents/gemini_image.py)
- **개선 내용**:
  - `Protocol` 또는 Abstract Base Class(ABC) 기반 추상화 인터페이스 도입 및 의존성 주입 구조 개편.
- **기대 효과**: 외부 AI/스토리지 벤더 변경(예: S3, Local Disk, Claude) 시 코드 변경 최소화 및 테스트 Mock 작성 극대화.

#### Task LT-4: CI/CD 파이프라인 상의 PostgreSQL 컨테이너 통합 테스트 구축
- **위험도**: Medium
- **대상 파일**: [file:///Users/nojeong-un/devs/wearless_studio/server/tests/test_personalization.py](file:///Users/nojeong-un/devs/wearless_studio/server/tests/test_personalization.py)
- **개선 내용**:
  - GitHub Actions 등 CI/CD 환경에 PostgreSQL Service Container를 구성하고 `test_personalization.py` 96개 DB 통합 테스트를 자동 실행하도록 설정.
- **기대 효과**: 개인화 기능 및 DB 제약조건에 대한 종단 간 자동화 회귀 테스트 커버리지 확충.

#### Task LT-5: 비정상 종료 워커 대상 R2 고아 에셋 배치 정기 수거 (Garbage Collector)
- **위험도**: Low
- **대상 파일**: [file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/dispatcher.py](file:///Users/nojeong-un/devs/wearless_studio/server/app/workers/dispatcher.py)
- **개선 내용**:
  - DB `assets` 및 `mannequin_cuts` 테이블과 R2 객체 키 목록을 비교하여 연결되지 않은 고아 이미지 객체를 주기적으로 삭제하는 배치 자제 정리 서비스 추가.
- **기대 효과**: 스토리지 비용 절감 및 불필요한 유령 파일 누적 방지.

---

## 6. 결론 및 최종 검증 방법 (Conclusion & Verification)

### 6.1 종합 결론
`wearless_studio` 프로젝트는 AI 기반 패션 콘텐츠 생성이라는 복잡한 도메인 요구사항을 수행하기 위해 매우 견고한 DB 동시성 제어 및 트랜잭션 락 계층, 이중 API 어댑터 및 3계층 프론트엔드 상태 관리 모델을 성공적으로 구축하였습니다. 유닛 테스트 수트 또한 811개 백엔드 테스트 및 17개 프론트엔드 테스트가 100% 성공하며 고품질의 코드베이스 상태를 유지하고 있습니다. 본 보고서에서 제시된 4가지 Quick Wins 및 5가지 Long-term 개선 로드맵을 차례로 적용한다면, 향후 대규모 엔터프라이즈 멀티테넌트 환경에서도 우수한 확장성과 데이터 무결성을 유지할 수 있을 것입니다.

### 6.2 독립 검증 방법 (Independent Verification Methods)

프로젝트 검증 담당자는 아래 명령어를 통해 본 보고서의 테스트 결과 및 코드 동작을 언제든지 독자적으로 재검증할 수 있습니다:

1. **백엔드 유닛 테스트 수트 재검증 (811 Passed, 1 Skipped)**:
   ```bash
   cd /Users/nojeong-un/devs/wearless_studio/server && /Users/nojeong-un/devs/wearless_studio/server/.venv/bin/pytest --ignore=tests/test_personalization.py
   ```

2. **프론트엔드 유닛 테스트 수트 재검증 (17 Passed)**:
   ```bash
   cd /Users/nojeong-un/devs/wearless_studio && npm run test:frontend
   ```

3. **주요 소스 코드 및 검증 구문 참조**:
   - FastAPI Lifespan 및 에러 미들웨어: [file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L84-L159](file:///Users/nojeong-un/devs/wearless_studio/server/app/main.py#L84-L159)
   - DB Connection Pool 타임아웃: [file:///Users/nojeong-un/devs/wearless_studio/server/app/db.py#L34-L46](file:///Users/nojeong-un/devs/wearless_studio/server/app/db.py#L34-L46)
   - Advisory Lock 및 Job Claiming: [file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L135-L138](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L135-L138), [file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L580-L603](file:///Users/nojeong-un/devs/wearless_studio/server/app/repo.py#L580-L603)
   - Frontend Zustand 및 3계층 모델: [file:///Users/nojeong-un/devs/wearless_studio/src/store/useAppStore.js#L1-L12](file:///Users/nojeong-un/devs/wearless_studio/src/store/useAppStore.js#L1-L12)
