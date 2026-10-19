# 백엔드 저장·API·작업 실행 계약

> 현재 백엔드 경계와 소유권을 정리한다. 제품·데이터 요구사항은 PRD와 공통 데이터 계약, 운영 정책은 해당 런북을 따른다.
> 역할: 기존 문서가 "무엇"을 정의했다면(엔티티·API 계약 = `common_data_contract.md`, 파이프라인·job 모델 = `ai_pipeline_spec.md`, 에이전트 = `ai_agent_modules.md`, 스택 = `03_기술스택_결정서.md`), 이 문서는 **물리 저장·HTTP 경계·job/credit/export 실행**만 정한다. 엔티티 필드와 파이프라인 단계는 재서술하지 않는다.

---

## 1. 핵심 결정 요약

| 항목 | 결정 | 근거 |
|---|---|---|
| 토폴로지 | SPA(Vercel) → FastAPI(AWS ECS Fargate, `api.wearless.kr`) → Supabase/R2/AI. 프론트는 Supabase에 **인증만** 직접 접근 | 크레딧·멱등·job 규칙이 전부 서버 트랜잭션 — 프론트 직접 DB 접근은 우회로가 됨. (백엔드는 Railway → AWS 이전 완료, 2026-07) |
| DB 형태 | 소유권·job·credit·asset은 relational, 콘티/에디터 문서는 JSONB | 문서형 shape는 변동이 잦고, 차감·권한·진행 상태는 트랜잭션·인덱스가 필요 |
| RLS | 전 사용자 테이블 활성화 + FastAPI도 모든 쿼리에 owner 조건 명시 | RLS는 운영 실수·미래 직접 조회에 대한 2차 방어선 |
| R2 | private 기본 + presigned 업로드(finalize 검증) + 앱 URL → signed redirect 서빙 | 사용자 상품 사진은 공개 자산이 아니고, `src`는 URL이면 계약 충족 — 만료 관리 없는 안정 URL |
| API 전환 | `lib/api` 시그니처 유지, 내부만 mock↔HTTP 어댑터 교체 | 계약 §6이 경계를 함수 단위로 확정 — 화면 수정과 백엔드 전환을 분리 |
| job 실행 | Postgres `jobs` 큐(SKIP LOCKED+lease) + lifespan dispatcher와 `JOB_KINDS`별 실행 프로세스 | job은 HTTP 요청 수명과 분리하고 같은 DB 큐·lease 계약으로 실행 (§5) |
| 크레딧 | **reserve-then-confirm**: 시작 시 예약, 성공분만 확정, 실패 시 해제 | 동시 job 초과 사용 방지 + 실패 미차감 + 부분 차감을 동시에 만족 (Codex 안 채택) |
| 다운로드 | **클라이언트 렌더**(html-to-image+JSZip) | 실제 에디터 다운로드는 `editorExport.js`가 담당 (§7) |
| 진행 전달 | SSE 우선(`job_events` 기반 replay) + 폴링 폴백 | 재연결·프로세스 재시작에도 진행 상태 복구 가능 |

## 2. Supabase 스키마

원칙: 컬럼 snake_case ↔ API camelCase 변환은 FastAPI(Pydantic alias) 책임 (계약 §1). enum은 Postgres enum 대신 `text + CHECK`(마이그레이션 유연성). JSONB 컬럼도 저장 시 Pydantic 검증 필수 — 무검증 저장소가 되면 job·렌더가 깨진 shape를 떠안는다.

| 테이블 | 핵심 컬럼 | 비고 |
|---|---|---|
| `profiles` | user_id pk=auth.users, display_name, avatar_asset_id, plan | Account 매핑 (계약 §3.7) |
| `credit_accounts` | user_id pk, **balance, reserved** | 잔액+예약 분리 — §6. 응답 `credits` = balance−reserved |
| `credit_ledger` | id, user_id, project_id, job_id, action_key, delta, balance_after, available_after, idempotency_key uq, metadata, created_at | **append-only** (update/delete 금지). action_key = creditCosts 키 + grant/refund |
| `projects` | id, user_id, status chk, title, compose_mode, copywriting, selected_mannequin_id, **storyboard jsonb, editor_blocks jsonb, editor_revision**, cover_asset_id, created/updated/deleted_at | 계약 §2. 보관함 조회는 컬럼 선택(블록 jsonb 제외). soft delete. 과거 `adjust_count`는 읽기 호환용이며 새 조정 제한에 사용하지 않음 |
| `products` | id, project_id uq, name, clothing_type, colors jsonb, measurements jsonb, measurements_unknown, upload_complete | 계약 §3.1. colors 내 이미지 src는 asset URL |
| `analyses` | project_id pk, payload jsonb, locked | 계약 §3.2 (Product 소유 필드 제외) |
| `mannequin_cuts` | id, project_id, candidate chk, version, asset_id, base_fit, fit_adjust, length_adjust, match_adjust jsonb | uq(project_id, candidate, version). candidate는 저장 ID 호환용이며 신규 단일컷은 A. 과거 fit_adjust·length_adjust·match_adjust는 읽기 호환용 |
| `wardrobe_images` | id, project_id, color_id, asset_id, ai, cut_type, sort_order, deleted_at | 계약 §3.6 |
| `assets` | id, user_id, project_id null, source(upload/ai/export/seed), visibility, r2_bucket, r2_key uq, mime_type, byte_size, width/height, checksum, original_filename, metadata jsonb, created/deleted_at | R2 키의 단일 레지스트리. ImageAsset.file 메타 보존 |
| `matching_items` | seed shape 그대로 + image/thumbnail_asset_id, is_active, sort_order | `seedMatchingItems.js` 이관 (M-01 데이터) |
| `jobs` | id, user_id, project_id, kind, status(pending/running/done/error), progress, steps jsonb, payload jsonb, result jsonb, error_message, **dedupe_key uq, idempotency_key uq, credits_reserved, credits_charged, locked_by/locked_at**, created/started/updated/finished_at | 계약 §6 멱등의 구현체 = job row. 화면용 JobStatus 매핑은 ai_pipeline_spec §4 |
| `job_events` | id bigint identity, job_id, event_type(progress/step/done/error), payload, created_at | SSE `Last-Event-ID` replay·폴링 폴백의 원본 |
| `exports` | id, project_id, job_id, format(long/zip), asset_id null, status, **snapshot_revision**, created/finished/expires_at | 클라 렌더 채택 후에도 **이력·실패율·옵션 기록**으로 유지 (§7) |

**인덱스**: `projects(user_id, updated_at desc)` · `jobs(project_id, kind, status)` + partial uq `(project_id, kind) where status in ('pending','running')`(동시 시작 DB 차단) · `credit_ledger(user_id, created_at)`.

`mannequin_cuts.asset_id`는 원본 컷 자산이다. 톤 에디터 적용본은 새 컬럼으로 덮어쓰지 않고
`assets.metadata.type='mannequinToneAdjusted'`와 `metadata.sourceCutId='<candidate>-<version>'`로
연결한다. `GET /mannequins`, 상세페이지 생성, 에디터 새 이미지 생성은 같은 컷 id에 연결된 최신
톤 조정본을 우선하고, 없을 때만 원본 `asset_id`를 사용한다.

**톤 에디터는 주상품 위에서만 움직인다.** 마네킹컷은 코디 의류(매칭 의류)를 함께 착장하지만
파는 옷은 주상품 하나다 — 코디 옷의 색을 바꿔 발행하면 구매자가 살 수 없는 색을 보여준다.
그래서 `editorGarmentMask`는 두 겹으로 보장된다: ① fal SAM 3 후보를 고르는 `tone_mask`가 코디 쪽(top|bottom)을 받아
증거 ROI에서 그 밴드를 빼고 후보 점수에 `matchZone` 벌점을 주고, 최종 마스크가 그 밴드에
25%를 넘게 걸치면 마스크를 내주지 않는다(`editor-worn-garment-fal-sam3-v2`) ② API가 받은 마스크를
픽셀로 다시 재어 같은 판정을 하고, 확인된 마스크에만 `metadata.matchGuardVersion`을 찍는다 —
기존 R2 마스크도 재사용하므로 코디 보호 검증을 생성 시점에만 맡기지 않는다.
누끼·톤 마스크는 fal SAM 3 고정이며, SAM2 서비스와 provider 전환 설정은 철거했다.
코디 쪽 판정은 셀러가 고른 `matching_items.clothing_type` 하나만 읽으며, 라우트와 잡이 같은
함수(`editor_garment_mask.matching_side_for_project`)를 쓴다. 마스크 없이 종결한 잡은
`GET .../tone-editor`가 `failed`로 내려 화면이 "준비 중"에 갇히지 않는다.

**RLS**: 전 테이블 owner 정책(`user_id = auth.uid()`, project 경유 테이블은 exists join). 쓰기는 service-role(FastAPI)만 — 단 FastAPI도 모든 쿼리에 JWT `sub` 조건을 명시한다. `matching_items`는 `is_active` 행 authenticated select. `credit_ledger`는 insert-only.

## 3. R2 자산 파이프라인

**업로드 (presigned + finalize)**
1. `POST /v1/assets/upload-url` { filename, mime, size, projectId, purpose } → { assetId, uploadUrl, headers, expiresAt }
2. 브라우저 → R2 직접 PUT (서버 바이트 프록시 금지 — 컨테이너 메모리/타임아웃)
3. `POST /v1/assets/{assetId}/complete` — 서버가 R2 object 존재·크기·mime 검증 후 row 확정
4. 계약의 `uploadAsset(file)`은 프론트 어댑터가 이 3단계를 감싼다(화면 계약 불변)

**키 규칙** — prefix 단위 삭제·비용 분석·복구:
```
users/{userId}/projects/{projectId}/uploads/{assetId}.{ext}
users/{userId}/projects/{projectId}/ai/{jobId}/{assetId}.{ext}
users/{userId}/projects/{projectId}/exports/{exportId}.{ext}
seed/matching/{matchingItemId}.{ext}
```

**서빙**: `src`는 안정적인 앱 URL `/v1/assets/{assetId}/file` — FastAPI가 R2 public URL로 302 redirect. **무인증 capability URL** (2026-07-11 전환): 브라우저 `<img>`는 Bearer를 못 붙여 인증 라우트로는 렌더가 아예 불가(실버그) — asset id(UUIDv4)가 능력 토큰이고 R2 객체 자체가 public base로 이미 공개라 새 노출 없음. 프론트는 어댑터 경계에서 상대 src를 `VITE_API_BASE_URL` 기준 절대화(`httpAdapter.absolutize` — 오리진이 달라 상대 URL은 404). 근거: raw signed URL을 DB에 저장하면 만료 관리가 계약을 침식(장시간 열린 에디터에서 이미지 깨짐). seed/public은 CDN 직접. AI provider 입력은 job 실행 시 발급한 signed GET. 생성 이미지·export 결과도 전부 assets 등록.

**파생본**: MVP는 원본 보존, 썸네일/리사이즈는 `assets.metadata.variants`로 추후.

## 4. HTTP API 매핑

`lib/api` 함수 시그니처는 불변 — 어댑터가 HTTP를 숨긴다. job형 endpoint는 `202 { jobId }`를 반환한다. 서버의 SSE·폴링 경로는 모두 구현됐지만, **현재 프론트 어댑터는 폴링으로 `onProgress`만 전달**한다. SSE 구독과 `onStep` 변환은 후속 작업이며, 완료 뒤 `{ data, credits }`를 resolve하는 경계는 유지한다.

| 계약 함수 (§6) | HTTP |
|---|---|
| createProject / getProject / patchProject | POST `/v1/projects` · GET/PATCH `/v1/projects/{id}` |
| getLibrary | GET `/v1/projects?view=library` |
| getAccount / getCatalogs | GET `/v1/me/account` · GET `/v1/catalogs` |
| getProduct / saveProduct | GET/PATCH `/v1/projects/{id}/product` |
| uploadAsset | §3 업로드 3단계 |
| analyzeProduct | POST `/v1/projects/{id}/analysis:analyze` (job) |
| saveAnalysis | PATCH `/v1/projects/{id}/analysis` |
| getMatchClothing | GET `/v1/projects/{id}/analysis/match-candidates` (분석·마네킹·콘티의 공통 매칭 선택 조회) |
| getMannequins | GET `/v1/projects/{id}/mannequins` |
| generateMannequins / regenerateMannequin | POST `…/mannequins:generate` · `:regenerate` (job — regenerate는 `{fitProfile}` 바디) |
| getStoryboard / saveStoryboard | GET/PUT `/v1/projects/{id}/storyboard` |
| generateDetailPage | POST `/v1/projects/{id}/detail-page:generate` (job) |
| getEditorBlocks / saveEditorBlocks | GET/PUT `/v1/projects/{id}/editor-blocks` (PUT은 editor_revision 증가) |
| getWardrobe / generateImage | GET `…/wardrobe` · POST `…/wardrobe/images:generate` (job) |
| download | 에디터의 클라이언트 내보내기 (§7) |
| job 공통 | GET `/v1/jobs/{id}` (폴링) · GET `/v1/jobs/{id}/events` (SSE) |
| pickAnyImage | 없음 — mock 전용 헬퍼, HTTP 어댑터에서 제거 |

- **에러 계약**: `{ error: { code, message, details? } }`, message는 그대로 토스트 가능한 한국어 (계약 §6). 401 미인증 · 403 타인 소유 · **402 크레딧 부족** · 404.
- **멱등을 HTTP로**: 진행 중 중복 시작 = 409가 아니라 **200 + 기존 job 반환**(합류). 완료된 job 재호출도 200 + 기존 결과. **실패(error)로 끝난 job 재호출은 새 job 시작**(재예약·재차감 — 계약 §6 멱등 ③). action endpoint는 `Idempotency-Key` 헤더 허용 — 같은 키는 같은 job.

## 5. job 실행 인프라 (합의안)

`server/app/workers/dispatcher.py`가 lifespan에서 실행되며 `JOB_KINDS`로 맡을 작업을 고른다. `jobs`가 큐의 원본이고 `FOR UPDATE SKIP LOCKED`로 점유한 뒤 lease 토큰으로 결과 저장을 검증한다. 작업은 요청 핸들러 밖에서 실행하므로 HTTP 취소·화면 이탈이 작업 취소를 뜻하지 않는다. 제공자 호출은 비동기 또는 스레드로 격리한다. 종료 시 실행 중 작업을 제한 시간 동안 기다리고 남은 작업은 lease 복구에 맡긴다.

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
[성공 tx]  결과 저장 완료 후: PL-4는 AI 컷 전량 완성 확인 후 확정액 계산
           → balance −= 실제, reserved −= 예약 → ledger append(delta, balance_after)
           → jobs.credits_charged, status='done' → commit
[실패 tx]  PL-4는 AI 컷 1장이라도 미완성이면 reserved 전액 해제
           → jobs.status='error' → commit  (= 사용자 관점 미차감)
```

- 근거: 선검증만으로는 동시 job 2개가 같은 잔액을 통과할 수 있다 (Codex 지적 수용).
- 단가는 서버 config + 버전 필드 → `jobs.metadata.creditCostVersion` 기록(과거 차감 문의 대응).
- 구독·추가 구매 잔액은 `credit_sources`에서 관리한다. 구독 먼저, 이후 추가 구매 FIFO로 차감하며 계정 잔액·예약과 원장을 같은 트랜잭션에서 맞춘다. 정책은 PRD §12.2와 `credit_system_design.md`를 따른다.

## 7. 다운로드 렌더링

에디터는 `editorExport.js`에서 블록별 PNG, 긴 PNG와 블록별 ZIP을 실제로 만든다. 렌더는 기준 캔버스 폭 1000, 고정 픽셀 비율 2를 쓰며 편집 핸들·선택 표시를 제외한다. 핵심 상품 자산은 `/bytes`로 읽어 CORS를 보장하며 하나라도 읽지 못하면 다운로드를 중단한다. 외부 이미지 실패는 해당 자리와 경고를 남긴다. 서버 렌더 작업을 새 요구사항으로 간주하지 않는다.

## 8. 프론트 API 어댑터 경계

`src/lib/api/index.js`가 `VITE_API_MODE`로 mock 또는 http 어댑터를 선택한다. HTTP 모드에서 순수 클라이언트 함수는 명시적 화이트리스트로 유지하며 미구현 서버 함수는 즉시 실패한다. 공개 입력·분석은 로컬 draft를 쓰고 분석만 실제 서버로 보낸다. 로그인 후 서버 프로젝트가 생기면 HTTP 경로로 승격한다. 임의의 함수별 mock 폴백은 허용하지 않는다.

## 9. 보안·운영 결정

- **인증**: Supabase Auth(이메일+OAuth) → 모든 요청 `Authorization: Bearer` → FastAPI JWKS 검증 → `user_id` 주입. service-role 키는 서버만. 공개 입력·분석의 비회원 경계는 PRD와 현재 공개 분석 라우트를 따른다.
- **검증**: Pydantic 모델이 계약 §3~§6을 검증하며 JSONB도 저장 전에 검증.
- **카탈로그**: `getCatalogs`는 서버 config 제공, 운영자 편집 대상(matching_items, 분위기 예시 시드)만 DB화.
- **삭제·보존**: project soft delete → 비동기 cleanup이 R2 private asset·export 삭제. ledger는 보존(감사). 보존 기한은 현재 운영 정책을 따른다.
- **공유 제외**: 공개 상세페이지 URL은 이 계획에 없음 — Next.js 전환 신호와 함께 별도 결정(03 §4). export 다운로드는 인증 사용자 signed URL 한정.
