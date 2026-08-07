# 에디터 대기(Editor-Wait) 개발 지시서

2026-08-03 · 창업자 확정 결정의 구현 계약. 목업 정본: `mockups/editor-wait.html` (승인됨).
배경: 상세페이지 생성이 실측 4~5분, 최대 10분+로 전망됨 → 별도 로딩 페이지를 폐기하고
**에디터 모양의 대기 화면**에서 기다린다. 콘티가 페이지 뼈대를 이미 알므로 첫 화면부터
완성 레이아웃을 보여주고, 컷이 도착하는 대로 그 자리에 채운다.

## 0. 확정 결정 (창업자 승인, 재론 금지)

1. **에디터에서 대기** — 뼈대 먼저, 사진은 도착 순.
2. **카피를 컷보다 먼저 생성** — `copywriter.generate()`는 컷 이미지를 입력으로 쓰지 않는다
   (`detail_page_job.py:439` — product·analysis·role만). 셀러는 대기 중 문구를 다듬는다.
3. **셀러 편집이 항상 이긴다** — 조립(M-02)이 셀러가 고친 텍스트를 덮어쓰지 않는다.
4. **생성 중인 이미지는 편집 잠금(이미지 내용만)** — 잠긴 자리 클릭=튕김. 패널·미니맵은
   잠그지 않는다. v1 대기 화면의 편집 범위는 **카피 텍스트만**(블록 이동·순서는 완료 후).
5. **생성예시는 상시 노출 금지** — 빈 자리 기본은 입력 페이지 HEIC 자리표시 언어
   (회색 `#F3F5F6`+흐린 로고, shimmer는 **생성 중인 칸에만**). 예시는 호버 시에만 잠깐.
6. **정직한 진행** — 가짜 % 크리프 금지. 컷 카운트(n/N)+실측 기반 잔여 추정.
   "창을 닫아도 계속 만들어져요"+[완료되면 알림 받기]가 1급 메시지.

## 1. v1 범위 / 비범위

**포함**: 서버 이벤트 확장(§2), 대기 화면(§3), 카피 오버라이드(§4), 전역 리본(§5).
**제외(v2 백로그)**: 컷 단위 재시도 API(실패 컷은 안내만), Library '생성 중' 카드,
실에디터 안에서의 대기 통합(지금은 에디터 *모양*의 대기 화면), mock 모드의 이벤트 시뮬
(mock은 기존 완주 흐름 유지), SSE 스트림 사용(폴링으로 통일).

## 2. 서버 (server/)

### 2-1. 워커 순서 변경 + 이벤트 확장 — `app/workers/detail_page_job.py`

현재 흐름: inputs(15) → 컷(65) → 카피(85) → 조립 → done.
변경 흐름: inputs(15) → **카피(18, copywriting=on일 때)** → 컷(20→80, 컷 단위) → 조립(85) → done.

새 이벤트 계약 (`job_events`, 기존 `step`/`progress` 타입 재사용):

| event_type | payload | 시점 |
|---|---|---|
| `step` | `{blockId, status:'copy_ready', texts:[{role,text}]}` | AG-02+03 검수 **후** 블록별 1회 |
| `step` | `{blockId, status:'cut_start'}` | 세마포어 획득 직후(실제 생성 시작) |
| `step` | `{blockId, status:'cut_done', previewUrl, width, height}` | R2 업로드 직후 |
| `progress` | `{progress: 20+round(60*done/total), phase:'cut', done, total}` | 컷 1개 종결마다 |
| `progress` | `{progress:18, phase:'copy', blocks:N}` / `{progress:85, phase:'assemble'}` | 국면 전환 |

- `previewUrl` = `r2.public_url(key)` (`r2.py:137` — 커스텀 도메인 or **1h presigned GET**).
  asset DB 행은 지금처럼 finalize에서만 insert — **DB 계약 무변경**. 10분 잡 ≪ 1h TTL이라 안전.
  기존 `cut_passthrough`(assetId 실림 — 셀러 원본이라 행이 이미 존재)와 `cut_failed`는 유지.
- 컷 성공 경로는 현재 이벤트를 **안** 쏜다(`:355` 성공 return) → `cut_done` 추가가 핵심.
- 카피 선행: `copy_results = await _gen_copy(...)` 호출을 `_gen_cuts` **앞으로** 이동
  (`:1083` → `:1057` 부근). 조립부는 변수 그대로 사용, 다른 변경 없음.
- 검수(AG-03) 통과본만 `copy_ready`로 내보낸다(선emit 후revise 금지).

### 2-2. 이벤트 폴링 분기 — `app/routes.py:1467` `GET /v1/jobs/{id}/events`

기존 SSE 엔드포인트에 `?poll=1&after=N` 분기 추가: 소유권 확인(기존 로직) 후
`repo.list_job_events` 1회 조회를 JSON으로 반환 `{"events":[{id,type,payload}...]}`.
프론트는 마네킹과 동일하게 **폴링으로 통일**(EventSource는 Bearer 헤더 불가).

### 2-3. 조립기 추적 필드 — `app/agents/page_assembler.py`

EditorBlock의 **요소(element)**에 추적 필드 추가(추가 필드만, 기존 키 불변):
- 이미지 el: `sourceBlockId` = 콘티 블록 id
- 카피 텍스트 el: `sourceBlockId` + `copyRole`('headline'|'body'|'subtitle')

셀러 오버라이드(§4)와 향후 컷 단위 재생성의 매칭 키. 자동 블록 3종(size/care/ai-notice)은 무관.
**mock 패리티**: `src/mock/db.js buildEditorBlocksFromStoryboard`에도 같은 필드 추가.

### 2-4. 테스트

- 기존: progress 65/85 순서를 단언하는 테스트가 있으면 새 국면(18/20~80/85)으로 갱신.
- 신규: ① copywriting=on 잡에서 `copy_ready`가 **모든 `cut_done`보다 앞** ② `cut_done`에
  previewUrl·width·height 존재 ③ 컷 2개 잡의 progress가 50/80(=20+60×n/2) ④ events?poll=1
  JSON 응답+after 커서 ⑤ 조립 결과 el의 sourceBlockId/copyRole.
- 게이트: `cd server && .venv/bin/pytest -q` 전부 통과.

## 3. 프론트 — 대기 화면 (기존 라우트 `/create/generating` 재사용)

`src/features/generating/Generating.jsx` 전면 교체(라우트·게이트 유지):

- **잡 수명은 스토어로 이동** — `useAppStore`에 `detailPageJob` 슬라이스
  (mannequinJob 패턴 미러): `{status idle|running|done|blocked|error, jobId, projectId,
  progress, phase, cutsDone, cutsTotal, cuts:{sbId:{url,w,h}}, live:[], copy:{sbId:texts},
  failed:[], errorMessage, result}` + `startDetailPageGeneration(projectId)`(중복 시작 가드,
  1.2s 간격으로 `getJob`+`getJobEvents` 병렬 폴링, 15분 상한, done 시 `syncCredits`).
  화면을 떠나도 폴링이 살아있는 게 "떠날 권리"의 전제.
- **어댑터 추가** — `httpAdapter`: `startDetailPage(pid)`(POST만, 202 {jobId} | 완료재호출 {data}),
  `getJob(jobId)`, `getJobEvents(jobId, after)`. mock: `startDetailPage` → `{legacy:true}` 반환,
  스토어가 기존 `generateDetailPage` 인라인 완주로 폴백(진행바만, 이벤트 없음 — v1 허용).
- **스켈레톤** = `buildEditorBlocksFromStoryboard(storyboard, product, copywriting)`
  (mock 생성기를 순수 함수로 재사용 — 콘티→블록 배치 규칙의 단일 소스). 이미지 src는 무시하고
  자리표시로 렌더, `cut_done` 도착 시 해당 `sourceBlockId` 자리에 previewUrl 표시.
- **상태별 자리표시**: 대기=회색+로고 / `cut_start`~ = 파란 링+shimmer+`생성 중` 태그 /
  `cut_done`=이미지 / `cut_failed`=차분한 실패 타일("이 컷은 만들지 못했어요 · 크레딧 미차감",
  재시도 버튼 **없음**(v2)). 잠긴 자리 클릭=튕김 애니. 호버=콘티 예시 살짝(선택: exampleId
  썸네일이 카탈로그에 있을 때만) + "생성 중 · 아직 편집할 수 없어요".
- **카피 편집**: `copy_ready` 도착한 텍스트 el을 contenteditable로. 편집분은
  `localStorage['ew-copy-{pid}'] = {sbId:{role:text}}` 디바운스 저장.
- **레이아웃**: 상단 리본(스피너·n/N·잔여 추정·"창 닫아도 계속"·[완료되면 알림 받기]) +
  좌 공정 원장(진행 이벤트 phase로 구동, 단계별 경과 표시) + 중앙 캔버스(1000좌표→화면 스케일)
  + 우 미니맵(진행 지도, v1은 표시 전용). 실패·차단(409)·정산영수증(FaceMarket) 장면은
  기존 Generating의 게이트·JSX를 이관 유지. 에러 시 in-place 카드(+[다시 시도]=재시작,
  [콘티로 돌아가기]) — 토스트 즉시 이탈(구 P2-8) 제거.
- **알림**: 버튼 클릭 시 `Notification.requestPermission()`; done && `document.hidden`이면
  브라우저 알림 1회.
- **완료 전환**: done → (영수증 있으면 영수증 카드 유지) → `/editor/{pid}` 이동.

## 4. 셀러 편집 승리 — `src/features/editor/Editor.jsx`

블록 로드 직후(`:440` withH 시점) 1회 적용:
`ew-copy-{pid}`가 있으면 각 텍스트 el을 `sourceBlockId`+`copyRole`로 매칭해 text 교체 →
스토리지 키 삭제 → setBlocks. 매칭 실패(구 데이터·필드 없음)는 조용히 무시.
서버는 셀러 편집을 모른 채 조립하고, **클라이언트가 로드 시점에 오버라이드**하는 v1 전략.

## 5. 전역 리본 — `src/features/shell/ChromeLayout.jsx`

`MannequinJobRibbon` 옆에 `DetailPageJobRibbon` 추가(같은 `.job-ribbon` CSS 재사용):
store `detailPageJob`이 running/error이고 현재 경로가 `/create/generating`이 아니면 표시.
라벨 "상세페이지를 만들고 있어요 · n/N컷", 버튼 [생성 화면 보기].

## 6. 검증

- 서버: pytest 전체 통과. 프론트: `npx vite build` 통과.
- 수동 스모크(mock): 콘티→생성 진입 시 스켈레톤 즉시 표시→완주→에디터 진입.
- 수동 스모크(http, dev 서버): 컷 도착 순서 무작위 확인, 카피 편집→에디터 반영,
  다른 페이지 이동 시 리본 표시, 새로고침 시 이벤트 재수신(after=0 재생)으로 복원.

## 6.5 Codex 리뷰 반영 (2026-08-03)

**수정 완료(도달 가능성 확인 후):**
- F1 이벤트 커밋 역전 → `repo.append_job_event`에 잡 단위 `pg_advisory_xact_lock` — 병렬 emit의
  커밋 순서를 id 순서와 일치시켜 after 커서 누락 차단. `set_job_progress`는 `greatest()`로 단조화.
  (도달 경로: AI 컷 2+ 생성, Semaphore(3) 병렬 커밋 경합 — 대기 화면 슬롯 미채움)
- F3 previewUrl → `r2.preview_url()`(항상 만료 있는 서명 URL) 신설. public 도메인 배포에서
  영구 bearer URL이 job_events 원장에 남는 문제 차단.
- F4 스켈레톤-서버 불일치 → `alignSkeletonToServer()`: 단일 AI 컷 2:3(880×1320) 정렬 +
  hero 외 전 역할 body 카피 슬롯 보강. (도달 경로: 기본 콘티+카피 ON — 핏·디테일 카피가
  copy_ready 수신에도 표시 불가였음)
- F5 stale 폴링 루프 → `beginProject`/`adoptProject`에서 `detailJobSeq` 무효화.
- F6 카피 유실 3경로 → 대기 화면 재진입 시 localStorage 복원 + 빈 문자열도 명시적 편집으로 적용
  + Editor 키 삭제를 적용 성공 이후로 이동.
- F7a 완료 알림 → store done 시점 발화로 이동(화면 이탈 시에도 울림).
- F8 리본 겹침 → `.job-ribbon-stack` 컨테이너가 sticky 소유, Storyboard 인스펙터는 스택 높이 측정.

**백로그(수정 안 함 — 사유 명기):**
- F2 이벤트 emit lease 펜스 부재 — **선재 패턴**(`_common.emit_job_event`를 마네킹 등 6개 워커가
  공유). 900초 lease 회수 후 stale 워커가 이벤트를 추가할 수 있으나 정산·저장은 펜스됨.
  수정하려면 emit 시그니처 전파(워커 6 + 테스트 다수) — 별도 사이클로.
- F7b FaceMarket 영수증 404 조기 확정 — **선재 버그**(이번 diff 밖). 정산 훅은 done 커밋 **후**
  best-effort 실행(detail_page_job.py:1169~)인데 tryGetReceipt는 첫 404를 '정산 없음'으로 확정.
  기존 주석("잡 완료 전 정산 기록")이 사실과 다름 — 라이선스 경로에서 영수증 화면이 스킵될 수 있음.
- F9 이벤트 직렬화 통합 테스트 — advisory lock 검증은 실 DB 커밋 경합 재현이 필요(로컬 DB 환경).
- 실패 경로 R2 산출물 잔존 — 선재(업로드는 생성 중, 정리는 lease-loss 경로만).

## 6.6 컷 동시성 개편 (2026-08-03 오너 결정)

**사실 확인:** 429 거절 기록은 코드·문서 어디에도 없다. 구 `_GEN_CONCURRENCY=3`은 실측이 아닌
보수적 추정("gemini 버스트 제한을 감안", 429 시 낮출 계획만 있었음)이고, 429 재시도도 전무해
스로틀 시 곧장 빈 슬롯이 되는 구조였다. 3개 상한은 13컷 기준 약 4.3웨이브 = 병렬 대비 수 분 손해.

**변경:** ① `detail_cut_concurrency`(기본 0=제한 없음 — 컷 수만큼 동시) ② `detail_cut_stagger_ms`
(기본 3000 — i번째 컷을 i×3초 뒤 제출, 순간 버스트 평탄화. 13컷=제출 속도 20건/분)
③ gemini 클라이언트에 429 백오프 재시도(5s→10s, 2회) — 전부-병렬의 안전망.
환경변수 `DETAIL_CUT_CONCURRENCY`/`DETAIL_CUT_STAGGER_MS`로 즉시 되돌릴 수 있다.
카피(_gen_copy)의 동시성 3은 유지(텍스트 호출이라 병목 아님). 테스트 헬퍼는 stagger=0.

**남은 것:** 실서버에서 13컷 병렬 실측(429 발생률·총 소요) — 문제 시 env 로 조정.

## 7. 리스크 메모

- previewUrl은 1h 만료 — 대기 화면 전용. 에디터 진입 후에는 finalize된 `/file` URL 사용.
- 콘티 저장 계약·에디터 blocks 저장 계약은 **무변경**(추가 필드만). ADR 불필요 판단.
- 컷은 `gather+Semaphore(3)` 병렬 — 도착 순서 무작위가 정상이며 **매칭은 blockId로만**.
