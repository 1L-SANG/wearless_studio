# 마네킹컷 생성과 콘티보드 작성 병렬화

작성일: 2026-08-07
브랜치: `feat/parallel-mannequin-conti` (worktree `../wearless_studio-conti-parallel`, `origin/main` @ 4482b9f 기준)

## 문제

마네킹컷 생성이 오래 걸린다. 지금 흐름은 입력 → 마네킹 → 콘티 순서라 사용자는 마네킹컷이 나올 때까지 로딩 화면만 본다. 그 시간 동안 할 수 있는 일(콘티 짜기)이 있는데도 막혀 있다.

## 해법

마네킹 단계를 콘티 뒤로 옮기고, 마네킹 생성 job 은 콘티 진입 시점에 발사한다. 사용자가 콘티를 짜는 동안 생성이 백그라운드로 돈다.

```
지금:  입력 →(로그인)→ 마네킹[생성 대기 + 핏 인터뷰 + 사진 양] → 콘티 → 생성 → 에디터
바뀜:  입력 →(로그인)→ 콘티[진입 시 job 발사 · 사진 양 · 상단 진행률] → 마네킹[핏 인터뷰] → 생성 → 에디터
                                                                        └ 미완이면 기존 MannequinLoading
```

## 왜 가능한가 (의존성 검증)

`origin/main` 기준으로 확인한 사실:

- **콘티는 마네킹 산출물을 참조하지 않는다.** `Storyboard.jsx` 안의 마네킹 참조는 '이전' 버튼 한 곳(`:2686`)뿐이다.
- **콘티 시드는 클라이언트가 만든다.** `httpAdapter.getStoryboard`(`src/lib/api/httpAdapter.js:354-375`)가 `defaultStoryboard(colors, mode, ctx)` 를 호출한다. 입력은 `product.colors`, `project.composeMode`, `product.clothingType`, `analysis.targetGenders` — **`fitProfile` 은 쓰지 않는다.** 즉 마네킹 핏 인터뷰 결과는 콘티 시드에 영향이 없다.
- **마네킹 생성은 사용자 입력을 요구하지 않는다.** 지금도 마네킹 페이지 진입 시 컷이 없으면 자동 발사한다(`Mannequin.jsx:750`).
- **최종 생성(`detail_page_job`)만 선택된 마네킹컷을 요구한다.** 마네킹 단계가 `generating` 앞에만 있으면 계약이 유지된다.

유일한 실제 의존성은 **사진 양(`composeMode`)** 이다. 콘티 시드의 컷 수를 결정하는데 선택 UI 가 마네킹 페이지 맨 아래(`Mannequin.jsx:1339-1370`)에 있다. → 콘티 페이지 상단으로 옮긴다.

## 설계

### 1. 사진 양(composeMode) 이동

마네킹 페이지의 "사진 양을 선택해주세요" 블록을 콘티 페이지 상단으로 옮긴다.

`httpAdapter.getStoryboard` 에 이미 모드 변경 재시드 로직이 있다(`:368-372`): 저장된 보드가 **이전 모드의 기본 시드 그대로**면 새 모드로 재시드하고, 사용자가 옵션·순서·레이아웃 중 하나라도 바꿨으면 그대로 둔다. 콘티 페이지에서 토글해도 사용자가 짜놓은 보드는 날아가지 않는다.

콘티 진입 시 `composeMode` 는 store 의 기본값 `'basic'`(`useAppStore.js:57`)으로 시드된다.

### 2. 마네킹 job 발사

콘티 페이지 mount 시 `requestMannequinGeneration(pid)` 를 발사한다. 이 시점이면 로그인·draft sync·`projectPersisted` 가 모두 끝난 상태라 안전하다.

발사 함수 3개(`requestMannequinGeneration` / `updateMannequinJob` / `generationProgressFor`, `Mannequin.jsx:191-232`)를 새 모듈 `src/features/mannequin/generationRunner.js` 로 분리해 콘티·마네킹이 같은 모듈 스코프 inflight promise 를 공유하게 한다.

**멱등성**: 모듈 스코프 inflight 가드가 같은 프로젝트의 중복 호출을 흡수한다. 새로고침으로 가드가 날아가도 서버가 활성 job 에 합류한다(`httpAdapter.js:602` 주석: "진행 중 재호출은 서버가 활성 job 에 합류(1회만 차감)"). `mannequinJob` 은 persist 대상이 아니라 새로고침 시 진행률이 0 부터 다시 붙지만 서버 job 은 계속 돈다.

### 3. 진행률 리본

`ChromeLayout.jsx:13` `MannequinJobRibbon` 을 재사용한다. 변경 두 가지:

- **완료 배지 추가**: `status === 'idle' && progress === 100` 이면 "마네킹컷 준비 완료" 를 3초간 보여주고 사라진다. 지금은 idle 이면 즉시 사라져 사용자가 끝난 걸 모른다.
- **'마네킹 화면 보기' 버튼 제거**(`:41-43`): 새 순서에서는 콘티를 끝내면 자연히 가는 곳이라 앞지르기 유도가 혼란을 만든다.

숨김 조건(`/create/mannequin` 에서 미표시)은 유지한다.

### 4. 라우팅·단계 재배치

| 파일 | 변경 |
|---|---|
| `shell/shell.jsx:14-24` | `WIZARD_STEPS` 순서를 `input → storyboard → mannequin → editor` 로. `STEP_INDEX` 를 `{input:0, analysis:0, storyboard:1, mannequin:2, generating:3, editor:3}` 로. `STEPPER_STEPS` 는 키 집합이 같아 그대로 |
| `shell/shell.jsx:40` | `resumeWork` 폴백 `/create/mannequin` → `/create/storyboard` |
| `shell/shell.jsx:43` | `onNav` 의 "job running 이면 마네킹으로" 강제 이동 제거 — running 중 사용자가 있어야 할 곳은 콘티다 |
| `product-input/ProductInput.jsx:278` | `goToMannequin` → `goToStoryboard`. navigate 두 곳(`:307`, `:319`)과 `openLogin`(`:322`) 목적지를 `/create/storyboard` 로 |
| `App.jsx:206-221` | `RootRedirect` 의 `wantsMannequin` → `wantsStoryboard`, 비교 대상과 성공 dest 를 `/create/storyboard` 로 |
| `storyboard/Storyboard.jsx:2686` | '이전' 버튼 `/create/mannequin` → `/create/input` |
| `storyboard/Storyboard.jsx:2648` | 다음 CTA `/create/generating` → `/create/mannequin` |
| `mannequin/Mannequin.jsx:1197` | CTA 이동 `/create/storyboard` → `/create/generating`. `setComposeMode` 호출은 콘티로 이동했으므로 제거, `saveAnalysis(fitProfile)` 저장은 유지 |

### 5. `refreshForEdits` 를 store 플래그로

`ProductInput.jsx:303-305` 이 `navigate(..., {state:{refreshForEdits:true}})` 로 넘기고 `Mannequin.jsx:1139` 가 소비한다. 새 순서에서는 입력과 마네킹 사이에 콘티가 끼어 route state 가 증발한다.

store 에 이미 있는 `generationRelevantEditsDirty`(`useAppStore.js:123`, setter `:243-244`)를 마네킹이 직접 읽게 바꾼다. 소비 후 `clearGenerationRelevantEdits()` 호출은 그대로 유지한다. `ProductInput` 의 `routeState` 계산은 삭제한다.

### 6. 콘티 프리페치 이관

마네킹 페이지는 사용자가 핏 질문에 답하는 동안 콘티 진입을 미리 데운다 — `warmStoryboardEntry`(`Mannequin.jsx:847-850`)가 `keepStep`/`changeStep`/사진 양 변경에서 불린다. 순서가 뒤집히면 이 워밍은 이미 지나간 화면을 데우는 셈이라 무의미하다.

- 마네킹의 `warmStoryboardEntry` 호출부(`:853`, `:854`, `:1355`)와 `storyboardPrefetchProjectRef` 를 제거한다.
- 대신 `ProductInput` 이 분석 결과를 사용자가 검토하는 동안 `prefetchStoryboardEntry(projectId, …)` 를 호출한다. 서버 project 가 확정된 뒤(=`analysisProjectId` 존재)에만.
- 무효화 호출(`invalidateStoryboardEntryPrefetch`)은 콘티 시드 입력이 바뀌는 지점 — 사진 양 토글(콘티로 이관) — 에 남긴다. `Mannequin.jsx:1114`, `:1181` 의 무효화는 `fitProfile` 변경에 걸려 있는데, §"왜 가능한가" 에서 확인했듯 콘티 시드는 `fitProfile` 을 쓰지 않으므로 함께 제거한다.

### 7. 마네킹 페이지의 두 상태

페이지 진입 시 job 상태에 따라:

- **완료** → 기존 핏 인터뷰 화면 (`phase === 'ready'`)
- **진행 중** → 기존 `MannequinLoading`(`:1247`)이 리본과 같은 진행률을 이어받아 표시. 사용자가 콘티를 건너뛰고 바로 눌러도 지금과 동일한 대기 경험이라 손해가 없다
- **실패** → 기존 `MannequinError` + 재시도(`:1248`)

새 컴포넌트 없이 기존 세 갈래를 그대로 쓴다.

## 엣지 케이스

| 상황 | 동작 |
|---|---|
| 마네킹이 콘티보다 먼저 끝남 | 리본에 완료 배지 3초 → 콘티 CTA 누르면 마네킹이 즉시 ready |
| 콘티가 마네킹보다 먼저 끝남 | 마네킹 페이지가 `MannequinLoading` 으로 대기 |
| 콘티를 안 짜고 바로 다음 | 허용. 서버 자동배치 기본값이 이미 들어있다 |
| job 실패 | 리본 error → 마네킹 페이지 `MannequinError` + 재시도 |
| 콘티 중 새로고침 | 서버 job 계속 진행. 재진입 시 재호출 → 서버가 활성 job 합류(중복 차감 없음) |
| 콘티에서 입력으로 되돌아가 분석 수정 | 기존 `refreshForEdits` 재생성 경로 유지(§5 로 전달 수단만 변경). 선발 job 은 버려지고 재생성 — 크레딧 2회 차감 가능성은 현재와 동일 |
| 사진 양을 콘티 짜는 중 변경 | 손 안 댄 기본 시드면 재시드, 손댄 보드면 유지(`httpAdapter.js:368-372` 기존 동작) |

## 범위 밖

- 서버 `mannequin_job.py` 자체의 생성 속도 최적화 (성격이 다르고 품질 회귀 위험이 있어 별도 브랜치)
- 레거시 mock 어댑터(`src/mock/*`, `mockAdapter.js`) 대응 — `.env` 가 `VITE_API_MODE=http` 이고 `.env.local` 이 이를 덮지 않아 실행되지 않는 경로
- job 취소·환불 엔드포인트

## 테스트

**Vitest**
- `WIZARD_STEPS` / `STEP_INDEX` 순서 회귀
- `ProductInput` 분석 CTA 목적지가 `/create/storyboard` (로그인·비로그인 두 경로)
- 콘티 mount 발사 멱등: 같은 pid 로 2회 호출 → `api.generateMannequins` 1회
- 마네킹 CTA 가 `/create/generating` 으로 이동
- `generationRelevantEditsDirty` 가 true 면 마네킹이 재생성 트리거, 소비 후 false

**수동 1회** — 로컬 서버로 입력 → 콘티(리본 진행률 확인) → 마네킹 → 생성 전체 관통. worktree 에는 `.env`/`.env.local` 과 `node_modules` 가 없으므로 복사 + `pnpm install` 선행.
