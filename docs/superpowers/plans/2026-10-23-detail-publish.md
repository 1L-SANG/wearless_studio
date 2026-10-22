# 상세페이지 게시(0단계) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 이 저장소에서는 구현을 Codex가 맡고 Claude가 검증한다(`CLAUDE.md`). Codex는 커밋하지 않는다. 커밋은 Claude가 작업 단위로 한다.

**Goal:** 에디터 상세페이지를 조각 JPEG로 R2 공개 도메인에 올리고, 몰 상세 편집기에 붙여넣을 HTML과 이미지 주소를 주며, 보관 규칙(구독 중 무제한, 구독 없으면 6개월, 탈퇴 시 즉시 삭제)을 워커가 집행한다.

**Architecture:** 브라우저가 기존 `renderStorefrontFiles`로 조각을 만들고 presigned PUT으로 R2에 직접 올린다. 서버는 행 생성과 presign, 업로드 검증(complete), 목록, 삭제, 만료 계산 워커, 탈퇴 파기 훅을 맡는다. 게시는 불변 버전이고 R2 키는 `pub/<user_id>/<publication_id>/<NN>.jpg`다.

**Tech Stack:** Vite + React(JSX), Zustand, CSS Modules, FastAPI, psycopg, Supabase Postgres(append-only 마이그레이션), Cloudflare R2(boto3), node:test + vite ssrLoadModule(프론트 테스트), pytest(서버 테스트).

**Spec:** `docs/superpowers/specs/2026-10-23-detail-publish-design.md` (이 계획은 그 설계서를 구현한다. 둘 다 읽는다.)

## Global Constraints

- Vite + React SPA다. Next.js 관례를 들이지 않는다. 컴포넌트는 `src/lib/api/`를 거쳐서만 서버를 부른다.
- CSS는 CSS 변수와 CSS Modules. 인라인 스타일과 하드코딩 hex 금지. 한국어는 단어 중간에서 줄이 바뀌지 않게 `word-break`를 덮어쓰지 않는다. 나란한 버튼은 `.modal-actions` 규칙으로 같은 알약 모양. 모바일 입력칸 글자 16px 이상. 문구는 핵심만 짧게, 줄표(—) 금지.
- 마이그레이션은 새 파일 1개만 추가한다. 기존 `supabase/migrations/*.sql`은 절대 고치지 않는다.
- 브라우저(Playwright, Chromium, WebKit) 실행 금지. 화면 검사는 Claude가 한다. 유료 AI API 호출 금지.
- 상수(설계서 값): 조각 최대 40개, 조각당 3MB(`MAX_JPEG_BYTES` 재사용), 합계 60MB, presign 유효 5분, 사용자당 시간당 20회, pending 정리 30분, 보관 6개월, `Cache-Control: public, max-age=31536000, immutable`, 삭제 경고 14일 전.
- 환경 플래그 `DETAIL_PUBLISH_ENABLED`(기본 false), 토큰 비밀 `DETAIL_PUBLISH_TOKEN_SECRET`(미설정이면 503 `detail_publish_unconfigured`).
- 실존 모델(REAL) 프로젝트는 게시 금지. 프론트는 `isRealModelSelection(analysis?.selectedModelId)`, 서버는 `fm_output_records` 조회로 판정한다.

## Review Focus

1. 조각 업로드가 일부만 성공한 채 complete를 부르면 실패한 번호만 돌려주고 행은 `pending`으로 남아야 한다(Task 2 테스트 `test_complete_reports_missing_files`).
2. 같은 complete를 두 번 부르면 두 번째는 같은 결과를 돌려주고 `assets` 행이 중복되지 않아야 한다(Task 2 `test_complete_is_idempotent`).
3. 구독이 다시 생긴 사용자의 `expires_at`이 null로 돌아와야 한다(Task 3 `test_recompute_clears_expiry_when_plan_returns`).
4. 상품명에 `<`, `>`, `"`가 들어가도 HTML 조각의 `alt`가 깨지지 않아야 한다(Task 5 `escapes product name in alt`).
5. 게시 중 모달을 닫아도(ESC, 배경 클릭) 진행은 계속되고 결과가 유령 완료가 되지 않아야 한다. 다운로드 모달의 `dlBusy` 게이트와 같은 방식(Task 6 `publish modal cannot close while busy`).

---

### Task 1: 마이그레이션과 계약 문서

**Files:**
- Create: `supabase/migrations/20261023120000_detail_publications.sql`
- Modify: `documents/common_data_contract.md` (새 절 "§ 게시(detail publication)")
- Test: `server/tests/test_detail_publications_migration.py`

**Interfaces:**
- Produces: 테이블 `public.detail_publications` (설계서 §7 컬럼 그대로), unique `(project_id, version)`, 인덱스 `(user_id, created_at desc)`와 `(expires_at) where status = 'ready'`, RLS 켜고 소유자 select/insert/update 정책, delete 정책 없음(서버 service role만).

- [ ] **Step 1: 실패하는 테스트 작성.** 기존 `server/tests/test_facemarket_publication_jpeg_migration.py`와 같은 방식으로 SQL 파일을 읽어 `create table if not exists public.detail_publications`, `check (status in ('pending','ready','deleted'))`, `enable row level security`, `unique (project_id, version)` 문자열이 있는지와, 같은 타임스탬프 파일이 하나뿐인지 검사한다.
- [ ] **Step 2: 실행해서 실패 확인.** `cd server && .venv/bin/pytest -q tests/test_detail_publications_migration.py`
- [ ] **Step 3: 마이그레이션 작성.** 설계서 §7의 DDL에 RLS와 인덱스를 더한다. RLS 문장은 `supabase/migrations/20260612090000_init.sql`의 `projects` 정책 형식을 따른다.
- [ ] **Step 4: 테스트 통과 확인.**
- [ ] **Step 5: 계약 문서에 절 추가.** 필드 표, 상태 전이(`pending → ready → deleted`), R2 키 규칙, 만료 규칙(설계서 §9) 네 가지만 적는다.

### Task 2: 서버 라우터 `detail_publications`

**Files:**
- Create: `server/app/detail_publications.py`
- Modify: `server/app/config.py` (`detail_publish_enabled: bool`, `detail_publish_token_secret: str | None` 추가와 env 읽기), `server/app/main.py` (플래그가 켜져 있으면 `include_router`), `server/app/repo.py` (아래 함수들)
- Test: `server/tests/test_detail_publications.py`

**Interfaces:**
- Produces(서버 내부, 순수 함수):
  - `publication_key(user_id: str, publication_id: str, index: int) -> str` → `pub/{user_id}/{publication_id}/{index:02d}.jpg`
  - `make_file_token(secret, *, user_id, publication_id, index, key, byte_size, expires_at) -> str`, `parse_file_token(secret, token) -> dict`, 예외 `TokenInvalid` (`facemarket_provenance`의 HMAC 방식을 따르되 코드는 복제, 공유하지 않는다)
  - `compute_expires_at(plan: str, *, created_at: datetime, now: datetime, current: datetime | None) -> datetime | None` (설계서 §9 규칙: plan이 free가 아니면 None, free이고 current가 None이면 now + 6개월, free이고 current가 있으면 current 유지)
  - `MAX_FILES = 40`, `MAX_FILE_BYTES = 3 * 1024 * 1024`, `MAX_TOTAL_BYTES = 60 * 1024 * 1024`, `UPLOAD_TTL = 300`, `RATE_LIMIT_PER_HOUR = 20`, `CACHE_CONTROL = "public, max-age=31536000, immutable"`
- Produces(repo):
  - `project_uses_real_model(conn, *, user_id, project_id) -> bool` (`fm_output_records` join `jobs`로 행 존재 여부. `facemarket_provenance._resolve_project_license`의 SQL과 같은 조건)
  - `count_detail_publications_last_hour(conn, user_id) -> int`
  - `create_detail_publication(conn, *, user_id, project_id, preset, width, product_name, editor_revision, files) -> dict` (version은 `coalesce(max(version),0)+1`를 같은 트랜잭션에서 계산)
  - `get_detail_publication(conn, *, user_id, publication_id) -> dict | None`
  - `mark_detail_publication_ready(conn, *, publication_id, files, total_bytes, expires_at) -> dict`
  - `list_detail_publications(conn, *, user_id, project_id) -> list[dict]` (ready만, 최신순)
  - `soft_delete_detail_publication(conn, *, user_id, publication_id) -> dict | None`
- Produces(HTTP, 모두 `require_user`):
  - `POST /v1/projects/{project_id}/detail-publications` body `{preset, width, productName, files:[{index, byteSize, width, height}]}` → `{publicationId, version, uploads:[{index, uploadUrl, uploadToken}]}`
  - `POST /v1/detail-publications/{publication_id}/complete` body `{uploads:[{index, uploadToken}]}` → 성공 `{publicationId, version, files:[{index, url, width, height, bytes}], expiresAt, createdAt}`; 일부 누락이면 409 `{error:{code:'files_missing', missing:[index...]}}`
  - `GET /v1/projects/{project_id}/detail-publications` → `{items:[{publicationId, version, preset, width, sliceCount, totalBytes, files:[{index,url,width,height,bytes}], expiresAt, createdAt}]}`
  - `DELETE /v1/detail-publications/{publication_id}` → 204. R2 삭제와 `purge_public_cache`를 바로 시도하고 실패는 로그만(워커가 재시도).
  - 오류 코드: `real_model_not_allowed`(403), `too_many_files`, `file_too_large`, `total_too_large`(400), `rate_limited`(429), `project_not_found`(404), `files_missing`(409), `detail_publish_unconfigured`(503).

- [ ] **Step 1: 순수 함수 테스트 작성.** `test_publication_key_pads_index`, `test_file_token_roundtrip`, `test_file_token_rejects_tamper`, `test_compute_expires_at_*` 세 경우(구독자 None, free 신규 now+6개월, free 기존 유지).
- [ ] **Step 2: 실패 확인 후 순수 함수 구현. 통과 확인.**
- [ ] **Step 3: 라우트 테스트 작성.** `server/tests/test_facemarket_publications.py`의 FakeConn/FakeR2 방식으로 `create_app(make_settings(detail_publish_enabled=True, detail_publish_token_secret="s"))`를 띄우고: `test_create_rejects_real_model_project`(403), `test_create_rejects_too_many_files`, `test_create_rate_limited`, `test_create_returns_presigned_uploads`(uploads 길이와 키 형식), `test_complete_reports_missing_files`(FakeR2.head가 None인 번호가 missing에 들어가고 status가 pending 유지), `test_complete_marks_ready_and_registers_assets`(assets 행 수 == 조각 수, visibility public, metadata.publication_id), `test_complete_is_idempotent`, `test_list_only_ready_and_owner`, `test_delete_soft_deletes_and_calls_r2_delete`, `test_unconfigured_secret_returns_503`.
- [ ] **Step 4: 실패 확인 후 라우터, repo, config, main.py 구현. 통과 확인.** complete에서 `assets` 등록은 `repo.create_asset`를 쓰되 visibility는 `public`이어야 하므로 `create_asset`에 `visibility: str = 'private'` 키워드 인자를 추가한다(기존 호출은 그대로). Cache-Control은 complete 때 `r2.copy`로 자기 키에 다시 써서 고정한다(`copy`가 메타데이터를 받지 못하면 `put_object` 기반 `copy_with_cache` 메서드를 `r2.py`에 추가).
- [ ] **Step 5: 전체 서버 테스트.** `cd server && .venv/bin/pytest -q` 전부 통과.

### Task 3: 만료 계산과 정리 워커

**Files:**
- Create: `server/app/workers/detail_publication_reaper.py`
- Modify: `server/app/repo.py`, `server/app/main.py` (플래그가 켜져 있고 `app.state.r2`가 있으면 `DraftAssetReclaimer` 옆에서 시작과 종료)
- Test: `server/tests/test_detail_publication_reaper.py`

**Interfaces:**
- Produces(repo): `recompute_detail_publication_expiry(conn, *, now) -> int`(갱신 행 수. `profiles.plan`을 join해 Task 2의 `compute_expires_at` 규칙을 SQL 한 번으로 적용), `list_expired_detail_publications(conn, *, now, limit=100) -> list[dict]`, `list_stale_pending_detail_publications(conn, *, older_than_minutes=30) -> list[dict]`, `finalize_detail_publication_deletion(conn, *, publication_id) -> None`(status deleted, deleted_at, 연결 assets.deleted_at)
- Produces(worker): `DetailPublicationReaper(app)` with `start()`, `stop()`, `_sweep_once()`. 주기 1시간, 만료 재계산은 마지막 계산 후 24시간이 지났을 때만.

- [ ] **Step 1: 테스트 작성.** `test_recompute_sets_expiry_for_free_user`, `test_recompute_clears_expiry_when_plan_returns`, `test_sweep_deletes_expired_and_purges`(FakeR2.delete 호출 키 목록과 상태 전이), `test_sweep_keeps_row_when_r2_delete_fails`, `test_sweep_cleans_stale_pending`.
- [ ] **Step 2: 실패 확인 후 구현. 통과 확인.** 구조는 `draft_asset_reclaimer.py`를 그대로 따른다.

### Task 4: 탈퇴 파기 훅

**Files:**
- Modify: `server/app/services/biometric_purge.py` (`purge_biometric_scope`에서 `reason == "account_delete"`이고 `user_id`가 있을 때 마지막에 호출), `server/app/detail_publications.py` (`purge_user_detail_publications(app, conn, *, user_id) -> int`)
- Test: `server/tests/test_detail_publications.py`에 `test_purge_user_detail_publications_deletes_all`

**Interfaces:**
- Produces: `purge_user_detail_publications`는 사용자의 `ready`와 `pending` 게시를 모두 R2 삭제, `purge_public_cache`, `finalize_detail_publication_deletion` 순으로 처리하고 개수를 돌려준다. R2 삭제 실패는 예외를 삼키고 로그를 남기며 행은 그대로 둔다(워커가 다음 주기에 다시 시도하도록 `expires_at = now()`로 당긴다).

- [ ] **Step 1: 테스트 작성과 실패 확인.**
- [ ] **Step 2: 구현과 통과 확인.** `purge_biometric_scope`의 기존 흐름과 반환값은 바꾸지 않는다. 호출 실패가 파기 결과를 실패로 만들지 않게 try로 감싼다.

### Task 5: 프론트 API 어댑터와 순수 로직

**Files:**
- Create: `src/lib/api/detailPublications.js`, `src/features/editor/detailPublish.js`
- Modify: `src/lib/api/httpAdapter.js`(네 함수 노출), `src/mock/api.js`(인메모리 목), `src/lib/api/index.js`(필요하면 통과)
- Test: `tests/frontend/detail-publish.test.mjs`

**Interfaces:**
- Produces(api): `createDetailPublication(projectId, {preset, width, productName, files})`, `completeDetailPublication(publicationId, {uploads})`, `listDetailPublications(projectId)`(서버 404면 `{items: [], disabled: true}`를 돌려준다), `deleteDetailPublication(publicationId)`. 목은 `DB`에 `detailPublications` 배열을 두고 `url`은 `https://images.example.test/pub/...`로 만든다.
- Produces(detailPublish.js):
  - `PUBLISH_PRESETS` = `EXPORT_PRESETS`에서 `original` 제외
  - `buildPublicationHtml(files, {productName, width}) -> string` (설계서 §6 형식. `alt`는 HTML 이스케이프)
  - `buildPublicationUrlList(files) -> string` (줄바꿈 구분)
  - `uploadPublicationFiles(blobs, uploads, {fetchImpl, concurrency = 3, onProgress, timeoutMs = 60000}) -> {failed: number[]}`
  - `publishDetailPage(blockNodes, {projectId, productName, preset}, deps) -> {publicationId, version, files, expiresAt, html}`: `renderStorefrontFiles(blockNodes, {preset, format:'jpeg', delivery:'split'}, deps)` → create → upload(실패 조각 최대 3회 재시도) → complete(409 `files_missing`이면 해당 조각 재업로드 후 재시도 1회). 모든 왕복은 `publicationNotarize.js`의 `withTimeout`과 같은 방식으로 타이머와 경합(JSON 8초, PUT 60초).
  - `downloadPublicationZip(files, productName, {fetchImpl}) -> Blob` (JSZip, 파일명 `01.jpg`… 영문 숫자만)

- [ ] **Step 1: 테스트 작성.** `builds html with one img per file`, `escapes product name in alt`, `url list is newline separated`, `upload retries failed slice and reports remaining`, `publishDetailPage retries complete once after files_missing`, `listDetailPublications maps 404 to disabled` (목 어댑터와 주입한 fetchImpl로).
- [ ] **Step 2: 실패 확인 후 구현. `pnpm test:frontend:files tests/frontend/detail-publish.test.mjs` 통과.**

### Task 6: 게시 모달과 에디터 버튼

**Files:**
- Create: `src/features/editor/EditorPublishModal.jsx`, `src/features/editor/EditorPublishModal.module.css`
- Modify: `src/features/editor/Editor.jsx` (버튼, 상태 `publish`, `pubBusy`, `pubProg`, `pubError`, `pubResult`, 실행 함수 `runPublish`)
- Test: `tests/frontend/editor-publish-modal.test.mjs`

**Interfaces:**
- Consumes: Task 5의 `publishDetailPage`, `buildPublicationHtml`, `buildPublicationUrlList`, `downloadPublicationZip`, `PUBLISH_PRESETS`; `buildExportSummary`.
- Produces: `EditorPublishModal({ settings, summary, busy, progress, error, result, previousCount, realModelBlocked, onChange, onCancel, onPublish, onCopyHtml, onCopyUrls, onDownloadZip })`. 결과 전에는 폭 선택(라디오 카드, `EditorDownloadModal`의 `RadioCard`를 공용 모듈로 빼서 함께 쓴다), 요약, 보관 안내 한 줄, 버튼 "취소"와 "올리기". 결과 후에는 조각 썸네일 가로 스크롤, 버튼 "HTML 복사", "이미지 주소 복사", "ZIP 받기", 몰별 안내 `<details>` 3줄, `previousCount > 0`이면 이전 버전 안내 한 줄. 문구는 설계서 §5 그대로.
- 에디터 버튼: 다운로드 옆 `Button variant="ghost" size="sm" icon="upload"` "몰에 올리기". `realModelBlocked`면 disabled와 `title="실존 모델 컷은 다운로드로 받아 몰에 직접 올려 주세요"`. `listDetailPublications`가 `disabled: true`면 버튼을 숨긴다(에디터 진입 때 한 번 조회, 결과를 `previousCount`로도 쓴다).
- 복사는 `navigator.clipboard.writeText`, 성공 토스트 "복사했어요", 실패 토스트 "복사하지 못했어요. 텍스트를 길게 눌러 복사해 주세요"와 함께 결과 화면의 `<textarea readOnly>`에 HTML을 보여 준다.

- [ ] **Step 1: 렌더 테스트 작성.** `tests/frontend/editor-download-modal.test.mjs` 방식으로 `renderToStaticMarkup`: `renders width options without original`, `shows retention line`, `result view has three pill buttons inside modal-actions`, `publish modal cannot close while busy`(Editor의 `onClose` 게이트는 모달 props로 `busy`가 넘어가는지로 검증), `real model shows blocked notice`.
- [ ] **Step 2: 실패 확인 후 구현. 통과 확인.** 모달 CSS는 `EditorDownloadModal.module.css`의 변수와 간격을 따른다.

### Task 7: 보관함 게시 패널

**Files:**
- Modify: `server/app/repo.py` (`list_library`에 `publication_count` 서브쿼리), `server/app/routes.py` (`get_library` 응답에 `publicationCount`), `src/mock/api.js`(`getLibrary` 항목에 `publicationCount`), `src/features/library/Library.jsx`
- Create: `src/features/library/PublicationsPanel.jsx`, `src/features/library/PublicationsPanel.module.css`
- Test: `tests/frontend/editor-library.test.mjs`에 항목 추가 또는 `tests/frontend/publications-panel.test.mjs`, 서버는 `server/tests/test_routes.py`의 library 테스트에 `publicationCount` 단언 추가

**Interfaces:**
- Consumes: Task 5 api 네 함수와 `buildPublicationHtml`, `downloadPublicationZip`.
- Produces: 완료 탭 카드 메타에 `게시 n개`(0이면 숨김). 카드의 그 글자를 누르면(카드 열기와 이벤트 분리) `PublicationsPanel({ projectId, productName, onClose })`가 `Modal`로 열린다. 행마다 `버전 n · 2026.10.23 · 860px · 조각 7개`, 만료 표시(`expiresAt` null이면 "구독 중 보관", 14일 이내면 빨간 글씨 "n일 뒤 지워져요"), 버튼 "HTML 복사", "ZIP 받기", "삭제". 삭제 확인 모달 문구: "몰에 붙여 둔 상세페이지가 깨져요. 몰에서 먼저 바꾼 뒤 지우세요." 버튼 "취소", "지우기".

- [ ] **Step 1: 테스트 작성과 실패 확인.** `renders expiry warning within 14 days`, `renders subscription keep label when expiresAt null`, `delete confirm copy present`.
- [ ] **Step 2: 구현과 통과 확인.**

### Task 8: 전체 검증

- [ ] `pnpm build` 성공.
- [ ] `pnpm test:frontend` 전부 통과(기존 테스트 포함).
- [ ] `cd server && .venv/bin/pytest -q` 전부 통과.
- [ ] 변경 파일 목록과 각 Task의 테스트 이름을 보고서에 적는다. 커밋은 하지 않는다.
