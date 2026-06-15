# 백엔드 연동 계획 (backend_integration_plan.md)

> 상태: 확정 (2026-06-12, 갱신 2026-06-14) · 작성 방식: Claude·Codex 독립 초안 → 근거 기반 상호 반박 → 병합 (충돌 2건은 조건부 합의)
> 역할: 기존 문서가 "무엇"을 정의했다면(엔티티·API 계약 = `common_data_contract.md`, 파이프라인·job 모델 = `ai_pipeline_spec.md`, 에이전트 = `ai_agent_modules.md`, 스택 = `03_기술스택_결정서.md`), 이 문서는 **물리 저장·HTTP 경계·job/credit/export 실행·전환 순서**만 정한다. 엔티티 필드와 파이프라인 단계는 재서술하지 않는다.

---

## 1. 핵심 결정 요약

| 항목 | 결정 | 근거 |
|---|---|---|
| 토폴로지 | SPA → FastAPI(Railway) → Supabase/R2/AI. 프론트는 Supabase에 **인증만** 직접 접근 | 크레딧·멱등·job 규칙이 전부 서버 트랜잭션 — 프론트 직접 DB 접근은 우회로가 됨 |
| DB 형태 | 소유권·job·credit·asset은 relational, 콘티/에디터 문서는 JSONB | 문서형 shape는 변동이 잦고, 차감·권한·진행 상태는 트랜잭션·인덱스가 필요 |
| RLS | 전 사용자 테이블 활성화 + FastAPI도 모든 쿼리에 owner 조건 명시 | RLS는 운영 실수·미래 직접 조회에 대한 2차 방어선 |
| R2 | private 기본 + presigned 업로드(finalize 검증) + 앱 URL → signed redirect 서빙 | 사용자 상품 사진은 공개 자산이 아니고, `src`는 URL이면 계약 충족 — 만료 관리 없는 안정 URL |
| API 전환 | `lib/api` 시그니처 유지, 내부만 mock↔HTTP 어댑터 교체 | 계약 §6이 경계를 함수 단위로 확정 — 화면 수정과 백엔드 전환을 분리 |
| job 실행 | Postgres `jobs` 큐(SKIP LOCKED+lease) + **web 프로세스 내 lifespan dispatcher로 시작**, worker 분리는 신호 후 | 운영 단위 1개로 시작하되 job을 HTTP 요청 수명과 분리 (합의 — §5) |
| 크레딧 | **reserve-then-confirm**: 시작 시 예약, 성공분만 확정, 실패 시 해제 | 동시 job 초과 사용 방지 + 실패 미차감 + 부분 차감을 동시에 만족 (Codex 안 채택) |
| 다운로드 | **클라이언트 렌더 1차**(html-to-image+JSZip), 서버 Playwright는 P2 승격 항목 | 현행 download는 stub — 프론트에서 실패율부터 검증이 더 작은 단위 (합의 — §7) |
| 진행 전달 | SSE 우선(`job_events` 기반 replay) + 폴링 폴백 | 재연결·프로세스 재시작에도 진행 상태 복구 가능 |

## 2. Supabase 스키마

원칙: 컬럼 snake_case ↔ API camelCase 변환은 FastAPI(Pydantic alias) 책임 (계약 §1). enum은 Postgres enum 대신 `text + CHECK`(마이그레이션 유연성). JSONB 컬럼도 저장 시 Pydantic 검증 필수 — 무검증 저장소가 되면 job·렌더가 깨진 shape를 떠안는다.

| 테이블 | 핵심 컬럼 | 비고 |
|---|---|---|
| `profiles` | user_id pk=auth.users, display_name, avatar_asset_id, plan | Account 매핑 (계약 §3.7) |
| `credit_accounts` | user_id pk, **balance, reserved** | 잔액+예약 분리 — §6. 응답 `credits` = balance−reserved |
| `credit_ledger` | id, user_id, project_id, job_id, action_key, delta, balance_after, available_after, idempotency_key uq, metadata, created_at | **append-only** (update/delete 금지). action_key = creditCosts 키 + grant/refund |
| `projects` | id, user_id, status chk, title, compose_mode, copywriting, selected_mannequin_id, adjust_count, **storyboard jsonb, editor_blocks jsonb, editor_revision**, cover_asset_id, created/updated/deleted_at | 계약 §2. 보관함 조회는 컬럼 선택(블록 jsonb 제외). soft delete |
| `products` | id, project_id uq, name, clothing_type, colors jsonb, measurements jsonb, measurements_unknown, upload_complete | 계약 §3.1. colors 내 이미지 src는 asset URL |
| `analyses` | project_id pk, payload jsonb, locked | 계약 §3.2 (Product 소유 필드 제외) |
| `mannequin_cuts` | id, project_id, candidate chk, version, asset_id, base_fit, fit_adjust, length_adjust, match_adjust jsonb | uq(project_id, candidate, version) |
| `wardrobe_images` | id, project_id, color_id, asset_id, ai, cut_type, sort_order, deleted_at | 계약 §3.6 |
| `assets` | id, user_id, project_id null, source(upload/ai/export/seed), visibility, r2_bucket, r2_key uq, mime_type, byte_size, width/height, checksum, original_filename, metadata jsonb, created/deleted_at | R2 키의 단일 레지스트리. ImageAsset.file 메타 보존 |
| `matching_items` | seed shape 그대로 + image/thumbnail_asset_id, is_active, sort_order | `seedMatchingItems.js` 이관 (M-01 데이터) |
| `jobs` | id, user_id, project_id, kind, status(pending/running/done/error), progress, steps jsonb, payload jsonb, result jsonb, error_message, **dedupe_key uq, idempotency_key uq, credits_reserved, credits_charged, locked_by/locked_at**, created/started/updated/finished_at | 계약 §6 멱등의 구현체 = job row. `cancelled` 없음(취소도 error). 화면용 JobStatus 매핑은 ai_pipeline_spec §4 |
| `job_events` | id bigint identity, job_id, event_type(progress/step/done/error), payload, created_at | SSE `Last-Event-ID` replay·폴링 폴백의 원본 |
| `exports` | id, project_id, job_id, format(long/zip), asset_id null, status, **snapshot_revision**, created/finished/expires_at | 클라 렌더 채택 후에도 **이력·실패율·옵션 기록**으로 유지 (§7) |

**인덱스**: `projects(user_id, updated_at desc)` · `jobs(project_id, kind, status)` + partial uq `(project_id, kind) where status in ('pending','running')`(동시 시작 DB 차단) · `credit_ledger(user_id, created_at)`.

**RLS**: 전 테이블 owner 정책(`user_id = auth.uid()`, project 경유 테이블은 exists join). 쓰기는 service-role(FastAPI)만 — 단 FastAPI도 모든 쿼리에 JWT `sub` 조건을 명시한다. `matching_items`는 `is_active` 행 authenticated select. `credit_ledger`는 insert-only.

## 3. R2 자산 파이프라인

**업로드 (presigned + finalize)**
1. `POST /v1/assets/upload-url` { filename, mime, size, projectId, purpose } → { assetId, uploadUrl, headers, expiresAt }
2. 브라우저 → R2 직접 PUT (서버 바이트 프록시 금지 — Railway 메모리/타임아웃)
3. `POST /v1/assets/{assetId}/complete` — 서버가 R2 object 존재·크기·mime 검증 후 row 확정
4. 계약의 `uploadAsset(file)`은 프론트 어댑터가 이 3단계를 감싼다(화면 계약 불변)

**키 규칙** — prefix 단위 삭제·비용 분석·복구:
```
users/{userId}/projects/{projectId}/uploads/{assetId}.{ext}
users/{userId}/projects/{projectId}/ai/{jobId}/{assetId}.{ext}
users/{userId}/projects/{projectId}/exports/{exportId}.{ext}
seed/matching/{matchingItemId}.{ext}
```

**서빙**: `src`는 안정적인 앱 URL `/v1/assets/{assetId}/file` — FastAPI가 권한 확인 후 짧은 만료 R2 signed URL로 302 redirect(+Cache-Control). 근거: raw signed URL을 DB에 저장하면 만료 관리가 계약을 침식(장시간 열린 에디터에서 이미지 깨짐). seed/public은 CDN 직접. AI provider 입력은 job 실행 시 발급한 signed GET. 생성 이미지·export 결과도 전부 assets 등록.

**파생본**: MVP는 원본 보존, 썸네일/리사이즈는 `assets.metadata.variants`로 추후.

## 4. HTTP API 매핑

`lib/api` 함수 시그니처는 불변 — 어댑터가 HTTP를 숨긴다. job형 endpoint는 `202 { jobId }` 반환 → 어댑터가 SSE/폴링을 구독해 `onProgress`/`onStep` 콜백으로 변환 후 `{ data, credits }` resolve.

| 계약 함수 (§6) | HTTP |
|---|---|
| createProject / getProject / patchProject | POST `/v1/projects` · GET/PATCH `/v1/projects/{id}` |
| getLibrary | GET `/v1/projects?view=library` |
| getAccount / getCatalogs | GET `/v1/me/account` · GET `/v1/catalogs` |
| getProduct / saveProduct | GET/PATCH `/v1/projects/{id}/product` |
| uploadAsset | §3 업로드 3단계 |
| analyzeProduct | POST `/v1/projects/{id}/analysis:analyze` (job) |
| saveAnalysis | PATCH `/v1/projects/{id}/analysis` |
| getMatchClothing | GET `/v1/projects/{id}/analysis/match-candidates` (과도기 — 최종은 analysis 응답 포함) |
| getMannequins | GET `/v1/projects/{id}/mannequins` |
| generate/adjust/regenerateMannequins | POST `…/mannequins:generate` · `:adjust` · `:regenerate` (job) |
| getStoryboard / saveStoryboard | GET/PUT `/v1/projects/{id}/storyboard` |
| generateDetailPage | POST `/v1/projects/{id}/detail-page:generate` (job) |
| getEditorBlocks / saveEditorBlocks | GET/PUT `/v1/projects/{id}/editor-blocks` (PUT은 editor_revision 증가) |
| getWardrobe / generateImage | GET `…/wardrobe` · POST `…/wardrobe/images:generate` (job) |
| download | POST `/v1/projects/{id}/exports` — §7 (기록 + P2 서버 렌더 훅) |
| job 공통 | GET `/v1/jobs/{id}` (폴링) · GET `/v1/jobs/{id}/events` (SSE) |
| pickAnyImage | 없음 — mock 전용 헬퍼, HTTP 어댑터에서 제거 |

- **에러 계약**: `{ error: { code, message, details? } }`, message는 그대로 토스트 가능한 한국어 (계약 §6). 401 미인증 · 403 타인 소유 · **402 크레딧 부족** · 404.
- **멱등을 HTTP로**: 진행 중 중복 시작 = 409가 아니라 **200 + 기존 job 반환**(합류). 완료된 job 재호출도 200 + 기존 결과. **실패(error)로 끝난 job 재호출은 새 job 시작**(재예약·재차감 — 계약 §6 멱등 ③). action endpoint는 `Idempotency-Key` 헤더 허용 — 같은 키는 같은 job.

## 5. job 실행 인프라 (합의안)

**결정**: MVP 초기 job 실행은 별도 Railway worker 없이 **FastAPI web 프로세스의 lifespan dispatcher**로 시작한다. 단 ① `jobs` 테이블이 큐의 원본이고 dispatcher는 `FOR UPDATE SKIP LOCKED` claim + lease(locked_by/locked_at) 후 실행 — **요청 핸들러 안에서 실행 금지**(HTTP 취소·이탈이 job 취소로 이어지지 않게), ② provider 호출은 async client 또는 `to_thread` 격리(이벤트 루프 블로킹 방지), ③ web replica 2개+ 또는 CPU 작업(서버 렌더 등) 도입 시 같은 claim 코드를 별도 worker 프로세스로 분리(엔트리포인트 동일, env 플래그).

근거: 운영 단위 1개의 단순성(Claude) + job/요청 수명 분리·복구 가능성(Codex) — 양쪽 조건부 합의. replica를 늘리는 순간 in-process dispatcher도 자연히 다중 worker가 되므로 claim/lease는 day-1 필수.

- **복구**: lease timeout 초과 `running` job은 startup/주기 점검에서 `pending` 재큐 또는 `error('서버 재시작')` 처리 — 고착되면 멱등 합류 규칙 때문에 사용자가 새 job을 영영 못 연다.
- **진행 전달**: worker가 `job_events` append → SSE는 이 테이블 replay(`Last-Event-ID`), 폴링은 `GET /v1/jobs/{id}`. 파이프라인 단계·진행률 매핑은 ai_pipeline_spec §3이 정본.
- **동시성**: `mannequin`·`detail_page`는 project당 진행 중 1개(partial uq), 완료 후 재호출은 기존 결과 반환 (계약 §6). `editor_image`는 다중 허용 + Idempotency-Key 단위 중복 방지.
- **관측**: agent call 로그(agentId/tier/model/latency/count/assetIds)를 job metadata로 — 모델 배정이 잠정이라 교체 판단 근거 (ai_agent_modules §6-5).

## 6. 크레딧 트랜잭션 (reserve-then-confirm)

응답 `credits` = `balance − reserved` (진행 중 job에 묶인 크레딧은 즉시 재사용 불가). 프론트 선차감·자체 계산 금지(frontend_state_model §6)는 불변.

```
[시작 tx]  idempotency/dedupe 조회(있으면 합류) → credit_accounts FOR UPDATE
           → 예상 최대 비용 계산(creditCosts) → available 검증(부족 시 402)
           → reserved += 비용 → jobs row(credits_reserved) → commit
[성공 tx]  결과 저장 완료 후: 실제 성공분 계산(PL-4는 실패 컷 제외)
           → balance −= 실제, reserved −= 예약 → ledger append(delta, balance_after)
           → jobs.credits_charged, status='done' → commit
[실패 tx]  reserved −= 예약 → jobs.status='error' → commit  (= 사용자 관점 미차감)
```

- 근거: 선검증만으로는 동시 job 2개가 같은 잔액을 통과할 수 있다 (Codex 지적 수용).
- 단가는 서버 config + 버전 필드 → `jobs.metadata.creditCostVersion` 기록(과거 차감 문의 대응).
- 환불·무료 지급·플랜 단가는 ledger action(grant/refund)으로 수용 구조만 준비 — 정책은 PRD §12.2 확정 대기.

## 7. 다운로드 렌더링 (합의안)

**결정**: MVP 다운로드는 **클라이언트 렌더**(html-to-image + JSZip)를 1차 구현으로 한다. 서버 Playwright 렌더는 P2 승격 항목.

근거: 현행 에디터의 다운로드는 토스트 stub이고 export 의존성도 없다 — 프론트에서 실제 다운로드 동작·실패율을 먼저 검증하는 편이 서버 export 인프라(Playwright/Chromium 메모리·렌더 전용 route 이중화)보다 작은 검증 단위다. 03 결정서 §6의 "DOM 절대배치 = 내보내기 쉬움" 방향과 일치하고, PRD §10.16이 MVP 해상도 옵션을 제외해 환경 차이 변수도 적다.

**조건 (Codex 조건부 수용 사항 — 구현 요건)**:
1. 캡처 대상은 편집 UI(.quick 툴바·선택 outline·resize 핸들·crop 오버레이)가 섞인 `.canvas-block` 원본이 아니라 **export 전용 DOM 상태**로 분리 — 기존 미리보기 오버레이(`preview-sheet`, 편집 핸들 없는 렌더)가 출발점.
2. 렌더 전 `document.fonts.ready` 대기(Pretendard self-hosted), R2 자산은 CORS 허용 origin 설정(canvas taint 방지), scale 고정(devicePixelRatio 무시).
3. `exports` 테이블은 "서버가 만든 파일" 전제가 아니라 **내보내기 이력·실패율·옵션 기록**으로 유지 — `POST /v1/projects/{id}/exports`가 format·snapshot_revision·성공/실패를 기록한다.

**P2 승격 트리거**(하나라도 충족 시 서버 렌더 전환): 마켓플레이스별 규격 프리셋(PRD §17) · 고해상도 옵션 요구 · 기록된 클라 렌더 실패율 임계 초과 · 모바일 지원.

## 8. 전환 단계 (mock → 실서버)

원칙: `src/lib/api/index.js` 아래 mockAdapter/httpAdapter 병렬 — `VITE_API_MODE`로 선택, **함수 단위 부분 스왑**(미전환 함수는 mock이 계속 담당). TanStack Query는 Phase 1과 함께 도입(03 §3, TODO.md §1).

| Phase | 범위 | 완료 기준 |
|---|---|---|
| 0 | 어댑터 구조 + FastAPI 골격(healthz, JWT 미들웨어) + 스키마 마이그레이션 | 배포·JWT 검증 통과 |
| 1 | Auth + `/me`·`/catalogs`·projects CRUD·보관함 + TanStack Query | 보관함이 실데이터 |
| 2 | Product + R2 presigned 업로드 (objectURL → asset URL) | 입력 플로우 실서버, 업로드 실패 사유 표면화 |
| 3 | Analysis + matching (M-01 룰베이스 서버 이관, 실측 null·소유권 분리 서버 검증) | 분석 플로우 실서버 |
| 4 | jobs·SSE·dispatcher·크레딧 reserve/confirm — `generateMannequins`부터, 이후 adjust/detail-page/editor-image | 유료 job 멱등·차감 시나리오 통과 |
| 5 | storyboard/editor-blocks/wardrobe 영속화 (editor_revision 충돌 방지) | 에디터 재진입 유지 |
| 6 | export 기록 + 클라 렌더 다운로드 완성 | 긴 PNG/ZIP 실동작 |
| 7 | mock 제거: parity 체크리스트 + `rg "@/mock"` 0건 + 멱등/미차감 수동 시나리오 | mock 의존 0 |

근거: 마네킹 생성(Phase 4 선두)은 유료 멱등·진행률·asset·봉투가 모두 들어간 가장 작은 대표 케이스.

## 9. 보안·운영 결정

- **인증**: Supabase Auth(이메일+OAuth) → 모든 요청 `Authorization: Bearer` → FastAPI JWKS 검증 → `user_id` 주입. service-role 키는 서버만. dev 한정 익명 플래그는 Phase 1까지만.
- **검증**: Pydantic 모델이 계약 §3~§6 mirror — JSONB 포함 전 저장 경로 검증.
- **카탈로그**: `getCatalogs`는 서버 config 제공, 운영자 편집 대상(matching_items, 분위기 예시 시드)만 DB화.
- **삭제·보존**: project soft delete → 비동기 cleanup이 R2 private asset·export 삭제. ledger는 보존(감사). 보존 기한은 §11.
- **공유 제외**: 공개 상세페이지 URL은 이 계획에 없음 — Next.js 전환 신호와 함께 별도 결정(03 §4). export 다운로드는 인증 사용자 signed URL 한정.

## 10. 실행 로드맵 (2026-06-12 확정 — Claude·Codex 독립 자문 수렴)

> 사용자 제안 로드맵(상태 정리 → 뼈대 → AI → PG → 크레딧 → 다운로드)을 검토한 결론. 두 자문이 모든 지점에서 수렴했다.

| 순서 | 작업 | 비고 |
|---|---|---|
| **0. AI 품질 스파이크 (1~2일)** | 버리는 스크립트로 Gemini 이미지 모델에 실제 상품 사진 투입 — 마네킹 핏 재현·의류 동일성·비용·지연 검증 | **인프라 투자 전 최대 리스크 검증.** Phase 0의 완료 기준(배포·JWT)은 핵심 가치를 검증하지 못한다. 필요 연결: GEMINI_API_KEY만 |
| 1. Phase 0~1 | FastAPI 골격·Supabase(스키마+Auth)·Railway 배포·어댑터 분기 + 읽기 전환 + **TanStack Query 도입** | Zustand는 이미 구현 완료 — 추가 작업 없음 |
| 2. Phase 2~3 | R2 presigned 업로드 → 분석·매칭 전환 | |
| 3. Phase 4 | AI job 실체화 + **크레딧 원장(reserve-confirm) 동시 구축** | ⚠️ 게이트: 크레딧 단가·환불 정책 확정(§11-1) 선행 |
| 4. Phase 5~6 | 콘티·에디터 영속화 → 다운로드(클라 렌더, §7) | |
| 5. Phase 7 | mock 제거 (parity 체크리스트) | **mock은 그 전까지 유지** — 실행 가능한 계약(멱등·봉투·소유권 머지)이자 parity 기준. 폐기 시 전환 리스크 증가 |
| 6. PG (결제) | 크레딧 **충전** 기능으로 원장 위에 마지막에 얹음 | 크레딧 시스템과 분리 — 베타는 수동 지급으로 PG 없이 런칭 가능. PG사 선정은 별도 ADR |

스택 연결 시점: GEMINI/OPENAI 키=스파이크(지금) · Supabase=Phase 0~1 · Railway=Phase 0 끝(healthz라도 조기 배포 — CORS·도메인 문제 조기 발견) · TanStack Query=Phase 1 · R2=Phase 2 · TS 점진(types→.ts)=HTTP 어댑터 작성 시 · PG=맨 끝.

## 11. 오픈 이슈 (정책 확정 대기)

| 항목 | 구조 준비 상태 | 막힌 곳 |
|---|---|---|
| 크레딧 단가·플랜·무료 체험·환불 | ledger action + config 버전으로 수용 가능 | PRD §12.2 정책 (00_README §4 Blocking) |
| 품질 불만 환불 기준 | refund action 예약 | 판정 기준 — AG-P2(image-qc)와 연결 |
| 자산 보존 기한·비용 상한 | exports.expires_at + R2 lifecycle | 운영 정책 |
| export 해상도·마켓 프리셋 | §7 P2 트리거로 정의 | P2 |
| 분위기 예시 시드 입력 | matching_items 패턴 재사용 | 운영자 데이터 (사용자 결정: 직접 입력 예정) |
