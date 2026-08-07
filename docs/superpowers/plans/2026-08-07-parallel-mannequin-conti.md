# 마네킹 생성·콘티 병렬화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 마네킹컷 생성 job 을 콘티보드 진입 시점에 발사하고 마네킹 단계를 콘티 뒤로 옮겨, 생성 대기 시간이 사용자가 콘티를 짜는 시간과 겹치게 한다.

**Architecture:** 라우팅 순서를 `입력 → 콘티 → 마네킹 → 생성` 으로 뒤집는다. 생성 발사 로직을 `Mannequin.jsx` 에서 별도 모듈로 빼 콘티·마네킹이 같은 in-flight promise 를 공유하게 하고, 진행률은 이미 있는 전역 리본(`ChromeLayout`)이 보여준다. 콘티 시드 컷 수를 정하는 사진 양(`composeMode`) 선택 UI 는 마네킹에서 콘티 상단으로 이동한다.

**Tech Stack:** React 18, react-router-dom, Zustand(`useAppStore`), Vite. 테스트는 `node --test` + `node:assert/strict` (vitest 아님).

## Global Constraints

- 기준 브랜치: `feat/parallel-mannequin-conti` (worktree `../wearless_studio-conti-parallel`, `origin/main` @ 4482b9f)
- 설계 정본: `docs/superpowers/specs/2026-08-07-parallel-mannequin-conti-design.md`
- 실행 모드는 `http` 하나뿐이다 — `.env` 가 `VITE_API_MODE=http`, `.env.local` 은 이 키를 덮지 않는다. 레거시 mock 어댑터(`src/mock/*`, `src/lib/api/mockAdapter.js`)는 **수정하지 않는다.**
- 서버(`server/`)는 **수정하지 않는다.**
- 테스트는 `node --test` 로 돌아가므로 테스트 대상 모듈은 **`@/` 별칭도 `import.meta.env` 도 쓰지 않는 순수 모듈**이어야 한다. 저장소의 기존 패턴 = 순수 코어(`*Core.js` / `*Cache.js`) + 배선 래퍼(api·store 를 import). 예: `src/features/storyboard/storyboardEntryPrefetchCache.js` ↔ `storyboardEntryPrefetch.js`
- JSX 단위의 배선 사실(라우트 목적지 등)은 저장소 관례대로 **소스 텍스트 정규식 단언**으로 검증한다. 예: `tests/frontend/storyboard-prefetch.test.mjs:34-39`
- 전체 테스트: `pnpm test:frontend` · 단일 파일: `node --test tests/frontend/<name>.test.mjs`
- **베이스라인이 green 이 아니다.** `origin/main` @ 4482b9f 에서 `tests/frontend/storyboard-opening-row.test.mjs:83` "mock and server assemblers emit the same opening-row block structure" 1건이 이미 실패한다(`ERR_INVALID_ARG_TYPE` at `:118`). 이 계획의 범위 밖(레거시 mock 어댑터)이므로 **고치지 않는다.** 각 태스크의 "전체 테스트" 기대치는 *이 1건 외 전부 PASS · 실패 총계가 1을 넘지 않음* 이다. 실패가 2건 이상이면 그 태스크가 무언가를 깼다는 뜻이다.
- 실패 개수 확인: `pnpm test:frontend 2>&1 | grep '^ℹ fail'`
- worktree 환경은 이미 준비돼 있다 — `pnpm install` 완료, `pnpm-workspace.yaml`(gitignore 대상)에 `allowBuilds: esbuild: true` 설정됨. `pnpm build` 는 통과한다.
- 주석·UI 문구는 한국어, 코드·커밋 메시지는 영어(저장소 관례).

---

## File Structure

**신규**

| 파일 | 책임 |
|---|---|
| `src/features/mannequin/generationRunnerCore.js` | 마네킹 생성 발사의 순수 코어 — 같은 프로젝트의 중복 호출을 하나의 in-flight promise 로 합류. 의존성 주입, `node --test` 대상 |
| `src/features/mannequin/generationRunner.js` | 위 코어에 `api`·`useAppStore`·sessionStorage 를 배선한 싱글턴 래퍼. 콘티·마네킹이 함께 import |
| `src/features/storyboard/ComposeModePicker.jsx` | 사진 양 선택 UI. 마네킹 하단에서 콘티 상단으로 이동 |
| `src/features/storyboard/ComposeModePicker.css` | 위 컴포넌트 전용 스타일(`Mannequin.css` 에서 이관) |
| `src/lib/wizardSteps.js` | `WIZARD_STEPS` / `STEP_INDEX` 순수 상수. `node --test` 로 순서 회귀 방지 |
| `tests/frontend/mannequin-generation-runner.test.mjs` | 러너 코어 단위 테스트 |
| `tests/frontend/wizard-step-order.test.mjs` | 단계 순서 회귀 테스트 |
| `tests/frontend/parallel-flow-routing.test.mjs` | 라우트 목적지·발사 배선의 소스 텍스트 단언 |

**수정**

| 파일 | 변경 요지 |
|---|---|
| `src/features/mannequin/Mannequin.jsx` | 러너 모듈 사용, 사진 양 UI 제거, 콘티 워밍 제거, `refreshForEdits` → store 플래그, CTA → `/create/generating` + 크레딧 라벨 |
| `src/features/mannequin/Mannequin.css` | `.fit-cmp*` 규칙 이관(삭제) |
| `src/features/storyboard/Storyboard.jsx` | 사진 양 피커 삽입, mount 시 생성 발사, '이전'·CTA 목적지 변경, CTA 문구 변경 |
| `src/features/product-input/ProductInput.jsx` | CTA 목적지 `/create/storyboard`, `routeState` 제거, 콘티 프리페치 워밍 추가 |
| `src/App.jsx` | `RootRedirect` 의 로그인 복귀 목표 `/create/storyboard` |
| `src/features/shell/shell.jsx` | 단계 상수를 `@/lib/wizardSteps.js` 에서 재수출, `resumeWork` 폴백, `onNav` 강제이동 제거 |
| `src/features/shell/ChromeLayout.jsx` | 리본 완료 배지, '마네킹 화면 보기' 버튼 제거 |
| `tests/frontend/storyboard-prefetch.test.mjs` | 워밍 위치 이동에 맞춰 단언 갱신 |

---

## Task 1: 생성 러너 모듈 분리

동작을 바꾸지 않는 순수 리팩터. 콘티가 같은 발사 함수를 쓰려면 `Mannequin.jsx` 모듈 스코프에 갇힌 상태를 밖으로 빼야 한다.

**Files:**
- Create: `src/features/mannequin/generationRunnerCore.js`
- Create: `src/features/mannequin/generationRunner.js`
- Create: `tests/frontend/mannequin-generation-runner.test.mjs`
- Modify: `src/features/mannequin/Mannequin.jsx:191-232` (삭제), import 구역

**Interfaces:**
- Consumes: `api.generateMannequins(pid, { onProgress })` → `Promise<{ data, credits }>` (`src/lib/api/httpAdapter.js:602`), `useAppStore` 의 `mannequinJob` / `setMannequinJob` / `projectId`, `markInitialGenerationRequested(pid)` (`src/features/mannequin/initialGenerationSession.js`)
- Produces:
  - `createMannequinGenerationRunner({ generate, readProgress, onJobChange, onRequested }) → { request(projectId), isRunning(projectId) }`
  - `requestMannequinGeneration(pid) → Promise<{ data, credits }>`
  - `isMannequinGenerationRunning(pid) → boolean`
  - `updateMannequinJob(pid, patch) → void`
  - `generationProgressFor(pid) → number`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/frontend/mannequin-generation-runner.test.mjs`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';

import { createMannequinGenerationRunner } from '../../src/features/mannequin/generationRunnerCore.js';

function harness({ generate }) {
  const jobs = [];
  const requested = [];
  const runner = createMannequinGenerationRunner({
    generate,
    readProgress: () => 0,
    onJobChange: (pid, patch) => jobs.push({ pid, ...patch }),
    onRequested: (pid) => requested.push(pid),
  });
  return { runner, jobs, requested };
}

test('concurrent requests for one project share a single generate call', async () => {
  let calls = 0;
  let finish;
  const pending = new Promise((resolve) => { finish = resolve; });
  const { runner, requested } = harness({
    generate: () => { calls += 1; return pending; },
  });

  const first = runner.request('p1');
  const second = runner.request('p1');
  assert.equal(first, second);
  assert.equal(calls, 1);
  assert.equal(requested.length, 1);
  assert.equal(runner.isRunning('p1'), true);
  assert.equal(runner.isRunning('p2'), false);

  finish({ data: [], credits: 10 });
  assert.deepEqual(await first, { data: [], credits: 10 });
  assert.equal(runner.isRunning('p1'), false);
});

test('a settled run lets the next request fire again', async () => {
  let calls = 0;
  const { runner } = harness({
    generate: async () => { calls += 1; return { data: [], credits: 0 }; },
  });

  await runner.request('p1');
  await runner.request('p1');
  assert.equal(calls, 2);
});

test('progress callbacks and the initial patch reach the job sink', async () => {
  let emit;
  const { runner, jobs } = harness({
    generate: (pid, { onProgress }) => {
      emit = onProgress;
      return Promise.resolve({ data: [], credits: 0 });
    },
  });

  await runner.request('p1');
  emit(42);
  assert.deepEqual(jobs[0], { pid: 'p1', status: 'running', progress: 0, errorMessage: '' });
  assert.deepEqual(jobs[1], { pid: 'p1', status: 'running', progress: 42, errorMessage: '' });
});

test('a rejected run clears the in-flight slot', async () => {
  let calls = 0;
  const { runner } = harness({
    generate: async () => { calls += 1; throw new Error('generation failed'); },
  });

  await assert.rejects(runner.request('p1'), /generation failed/);
  assert.equal(runner.isRunning('p1'), false);
  await assert.rejects(runner.request('p1'), /generation failed/);
  assert.equal(calls, 2);
});

test('a missing project id never calls generate', async () => {
  let calls = 0;
  const { runner } = harness({ generate: async () => { calls += 1; return null; } });
  assert.equal(await runner.request(null), null);
  assert.equal(calls, 0);
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `node --test tests/frontend/mannequin-generation-runner.test.mjs`
Expected: FAIL — `Cannot find module '.../generationRunnerCore.js'`

- [ ] **Step 3: 순수 코어 작성**

`src/features/mannequin/generationRunnerCore.js`:

```js
/* 마네킹 생성 발사의 순수 코어 — 같은 프로젝트의 중복 호출을 하나의 in-flight 요청으로 합류시킨다.
   콘티(백그라운드 발사)와 마네킹(진입 시 발사)이 같은 러너를 공유해야 유료 생성이 두 번 나가지 않는다.
   api·store 의존은 배선 래퍼(generationRunner.js)가 주입한다 — 이 파일은 node --test 로 직접 검증된다. */
export function createMannequinGenerationRunner({
  generate,
  readProgress,
  onJobChange,
  onRequested = () => {},
}) {
  let inflight = null;
  let inflightProjectId = null;

  return {
    request(projectId) {
      if (!projectId) return Promise.resolve(null);
      if (inflight && inflightProjectId === projectId) return inflight;

      onJobChange(projectId, {
        status: 'running',
        progress: readProgress(projectId),
        errorMessage: '',
      });

      inflightProjectId = projectId;
      onRequested(projectId);
      inflight = generate(projectId, {
        onProgress: (next) => onJobChange(projectId, {
          status: 'running',
          progress: next,
          errorMessage: '',
        }),
      }).finally(() => {
        if (inflightProjectId === projectId) {
          inflight = null;
          inflightProjectId = null;
        }
      });

      return inflight;
    },

    isRunning(projectId) {
      return inflight != null && inflightProjectId === projectId;
    },
  };
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `node --test tests/frontend/mannequin-generation-runner.test.mjs`
Expected: PASS (5 tests)

- [ ] **Step 5: 배선 래퍼 작성**

`src/features/mannequin/generationRunner.js`:

```js
/* 배선 래퍼 — 순수 코어에 실제 api·store·sessionStorage 를 연결한 앱 싱글턴.
   콘티(진입 시 발사)와 마네킹(컷이 없으면 발사)이 이 모듈 하나를 공유한다. */
import { api } from '../../lib/api/index.js';
import { useAppStore } from '../../store/useAppStore.js';
import { markInitialGenerationRequested } from './initialGenerationSession.js';
import { createMannequinGenerationRunner } from './generationRunnerCore.js';

export function updateMannequinJob(pid, patch) {
  const { projectId, setMannequinJob } = useAppStore.getState();
  if (projectId !== pid) return;
  setMannequinJob({ projectId: pid, ...patch });
}

export function generationProgressFor(pid) {
  const job = useAppStore.getState().mannequinJob;
  return job?.projectId === pid ? Number(job.progress) || 0 : 0;
}

const runner = createMannequinGenerationRunner({
  generate: (pid, options) => api.generateMannequins(pid, options),
  readProgress: generationProgressFor,
  onJobChange: updateMannequinJob,
  onRequested: markInitialGenerationRequested,
});

export const requestMannequinGeneration = (pid) => runner.request(pid);
export const isMannequinGenerationRunning = (pid) => runner.isRunning(pid);
```

- [ ] **Step 6: `Mannequin.jsx` 가 새 모듈을 쓰게 변경**

`src/features/mannequin/Mannequin.jsx:191-232` 의 `mannequinGenerationInflight`, `mannequinGenerationProjectId`, `updateMannequinJob`, `generationProgressFor`, `requestMannequinGeneration` 정의를 **모두 삭제**하고 import 로 대체한다. `markInitialGenerationRequested` import 가 이 파일에서 더 쓰이지 않으면 함께 제거한다(`clearInitialGenerationRequested`·`cutsExistedBeforeInitialGeneration` 는 계속 쓰이므로 유지).

파일 상단 import 구역에 추가:

```js
import {
  generationProgressFor,
  requestMannequinGeneration,
  updateMannequinJob,
} from './generationRunner.js';
```

- [ ] **Step 7: 빌드로 회귀 확인**

Run: `pnpm build`
Expected: 성공. 실패하면 대개 `Mannequin.jsx` 에 남은 미사용 import 또는 삭제한 함수의 잔여 참조다.

- [ ] **Step 8: 전체 테스트**

Run: `pnpm test:frontend`
Expected: 신규 5개 PASS · 실패 총계 1 (베이스라인 storyboard-opening-row 뿐)

- [ ] **Step 9: 커밋**

```bash
git add src/features/mannequin/generationRunnerCore.js \
        src/features/mannequin/generationRunner.js \
        src/features/mannequin/Mannequin.jsx \
        tests/frontend/mannequin-generation-runner.test.mjs
git commit -m "refactor(mannequin): extract the generation runner so two screens can share it"
```

---

## Task 2: 사진 양 선택을 콘티 상단으로 이동

`composeMode` 는 콘티 시드의 컷 수를 정한다. 콘티가 먼저 오려면 선택 UI 도 콘티에 있어야 한다.

**Files:**
- Create: `src/features/storyboard/ComposeModePicker.jsx`
- Create: `src/features/storyboard/ComposeModePicker.css`
- Modify: `src/features/mannequin/Mannequin.jsx:1337-1372` (사진 양 블록 제거), `:1250`
- Modify: `src/features/mannequin/Mannequin.css:206-214` (규칙 이관)
- Modify: `src/features/storyboard/Storyboard.jsx` (피커 렌더 + 재시드 배선)

**Interfaces:**
- Consumes: `useAppStore` 의 `composeMode`(`src/store/useAppStore.js:57`)·`setComposeMode(mode) → Promise`(`:251-259`), `catalogs.composeModes` (`{ value, label, desc, count }`), `invalidateStoryboardEntryPrefetch(pid)`
- Produces: `<ComposeModePicker modes onModeChange onError />` — `onModeChange(nextMode)` 는 PATCH 성공 후, `onError()` 는 실패 시 호출

- [ ] **Step 1: 피커 컴포넌트 작성**

`src/features/storyboard/ComposeModePicker.jsx`:

```jsx
/* 사진 양 선택 — 콘티 시드의 컷 수를 정한다. 사용자가 보드를 보면서 고를 수 있게 콘티 상단에 둔다.
   손대지 않은 기본 시드는 모드 변경 시 재시드되고 사용자가 손댄 보드는 유지된다
   (src/lib/api/httpAdapter.js 의 getStoryboard 재시드 규칙). */
import { useAppStore } from '@/store/useAppStore.js';
import './ComposeModePicker.css';

export function ComposeModePicker({ modes, onModeChange, onError }) {
  const composeMode = useAppStore((s) => s.composeMode);
  const setComposeMode = useAppStore((s) => s.setComposeMode);
  if (!modes?.length) return null;

  return (
    <div className="sb-compose">
      <div className="sb-compose-q">사진 양</div>
      <div className="sb-cmp2">
        {modes.map((m) => {
          const on = composeMode === m.value;
          return (
            <button
              type="button"
              key={m.value}
              className={`sb-cmp${on ? ' on' : ''}`}
              aria-pressed={on}
              onClick={() => {
                if (on) return;
                setComposeMode(m.value).then(() => onModeChange(m.value)).catch(() => onError());
              }}
            >
              <b>{m.label}</b>
              <span>{m.desc}</span>
              {m.count && <em>예상 {m.count}컷</em>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default ComposeModePicker;
```

- [ ] **Step 2: 스타일 이관**

`src/features/storyboard/ComposeModePicker.css` 를 만들고 아래를 넣는다 (`Mannequin.css:206-213` 의 `.fit-cmp*` 규칙을 `.sb-cmp*` 로 개명한 것 — 미사용 `.fit-cmp.off`·`.fit-cmp-off` 는 옮기지 않는다):

```css
.sb-compose { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.sb-compose-q { font-size: 14px; font-weight: 700; color: var(--fg-1); white-space: nowrap; }
.sb-cmp2 { display: flex; gap: 8px; flex: 1; }
.sb-cmp { position: relative; flex: 1; border: 2px solid var(--ring-strong); background: var(--bg-1); border-radius: var(--r-5); padding: 10px 8px; cursor: pointer; text-align: center; color: var(--fg-1); font: inherit; transition: border-color .12s, background .12s; }
.sb-cmp:hover { border-color: var(--link); }
.sb-cmp.on { border-color: var(--link); background: color-mix(in srgb, var(--link) 8%, var(--bg-1)); }
.sb-cmp b { display: block; font-size: 15px; font-weight: 600; }
.sb-cmp span { display: block; font-size: 11px; color: var(--fg-2); margin-top: 3px; }
.sb-cmp em { display: block; font-style: normal; font-size: 11px; font-weight: 700; color: var(--link); margin-top: 6px; }
```

`src/features/mannequin/Mannequin.css:206-214` 의 `.fit-cmp2`·`.fit-cmp`·`.fit-cmp:hover`·`.fit-cmp.on`·`.fit-cmp.off`·`.fit-cmp b`·`.fit-cmp span`·`.fit-cmp em`·`.fit-cmp-off` 9줄을 삭제한다.

- [ ] **Step 3: 마네킹에서 사진 양 UI 제거**

`src/features/mannequin/Mannequin.jsx`:

- `:1250` 의 `const modes = catalogs?.composeModes || [];` 삭제
- `:1337-1372` 의 마지막 `else` 분기를 사진 양 블록 없이 CTA 만 남긴다:

```jsx
        ) : (
          <div className="fit-final">
            <Button variant="primary" size="lg" block iconRight="arrowRight" disabled={busy} onClick={onCta}>
              이 핏으로 진행하기
            </Button>
          </div>
        )}
```

- `onCta`(`:1193-1202`)에서 `setComposeMode` 호출과 그 try/catch 를 제거하고 이동만 남긴다. `fitProfile` 저장(`:1179-1192`)은 그대로 둔다:

```jsx
    navigate('/create/storyboard');
```

- `composeMode`·`setComposeMode` 를 읽던 store 셀렉터(`:660` 주변)와 더 이상 쓰이지 않는 import 를 제거한다.

- [ ] **Step 4: 콘티에 피커 배선**

`src/features/storyboard/Storyboard.jsx`:

import 추가:

```jsx
import { ComposeModePicker } from './ComposeModePicker.jsx';
```

`generate` 정의(`:2640`) 위에 핸들러를 추가한다. 모드가 바뀌면 프리페치를 버리고 로드 이펙트(`:1576`, deps `[loadRetry]`)를 다시 돌려 서버 재시드 규칙을 태운다:

```jsx
  // 사진 양이 바뀌면 콘티를 다시 읽는다 — 손대지 않은 기본 시드만 새 모드로 재시드된다(어댑터 규칙).
  const onComposeModeChange = () => {
    invalidateStoryboardEntryPrefetch(projectId);
    setLoadRetry((n) => n + 1);
  };
  const onComposeModeError = () => {
    toast.push('사진 양 선택을 저장하지 못했어요. 다시 선택해 주세요.');
  };
```

`invalidateStoryboardEntryPrefetch` 가 이 파일의 `./storyboardEntryPrefetch.js` import 목록(`:63` 부근)에 없으면 추가한다.

`PageHead` 바로 아래(`:2656` 다음 줄)에 피커를 렌더한다:

```jsx
      <ComposeModePicker
        modes={catalogs?.composeModes || []}
        onModeChange={onComposeModeChange}
        onError={onComposeModeError}
      />
```

- [ ] **Step 5: 빌드 + 테스트**

Run: `pnpm build && pnpm test:frontend`
Expected: 빌드 성공 · 실패 총계 1 (베이스라인 뿐)

- [ ] **Step 6: 커밋**

```bash
git add src/features/storyboard/ComposeModePicker.jsx \
        src/features/storyboard/ComposeModePicker.css \
        src/features/storyboard/Storyboard.jsx \
        src/features/mannequin/Mannequin.jsx \
        src/features/mannequin/Mannequin.css
git commit -m "feat(storyboard): move the compose-mode picker onto the board it seeds"
```

---

## Task 3: 콘티 프리페치를 입력 화면으로 이관

마네킹은 사용자가 핏 질문에 답하는 동안 콘티 진입을 미리 데운다. 순서가 뒤집히면 이미 지나간 화면을 데우는 셈이라 무의미하다.

**Files:**
- Modify: `src/features/mannequin/Mannequin.jsx:668`, `:847-854`, `:1114-1115`, `:1181-1182`, import 구역
- Modify: `src/features/product-input/ProductInput.jsx`
- Modify: `tests/frontend/storyboard-prefetch.test.mjs:34-39`

**Interfaces:**
- Consumes: `prefetchStoryboardEntry(projectId, waitForIdle) → Promise`, `invalidateStoryboardEntryPrefetch(projectId)` (`src/features/storyboard/storyboardEntryPrefetch.js`)
- Produces: 없음 (내부 배선 이동)

- [ ] **Step 1: 테스트를 새 위치로 갱신**

`tests/frontend/storyboard-prefetch.test.mjs` 상단에 소스 로드를 추가한다:

```js
const productInputSource = readFileSync(
  new URL('../../src/features/product-input/ProductInput.jsx', import.meta.url),
  'utf8',
);
```

`:34-39` 의 세 단언을 아래로 교체한다:

```js
  assert.match(
    productInputSource,
    /storyboardPrefetchProjectRef\.current === analysisProjectId[\s\S]*?prefetchStoryboardEntry\(analysisProjectId\)/,
  );
  assert.doesNotMatch(mannequinSource, /warmStoryboardEntry/);
```

`mannequinSource` 는 다른 테스트에서 더 쓰이지 않으면 선언째 삭제한다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `node --test tests/frontend/storyboard-prefetch.test.mjs`
Expected: FAIL — `productInputSource` 에 워밍 코드가 없고 `mannequinSource` 에 `warmStoryboardEntry` 가 남아 있다

- [ ] **Step 3: 마네킹에서 워밍 제거**

`src/features/mannequin/Mannequin.jsx`:

- `:668` `const storyboardPrefetchProjectRef = useRef(null);` 삭제
- `:847-850` `warmStoryboardEntry` 정의 삭제
- `:853-854` 를 워밍 호출 없이 되돌린다:

```jsx
  const keepStep = (key) => { setStep(key, { mode: 'keep', pick: null, pickLb: null }); };
  const changeStep = (key) => { setStep(key, { mode: 'changing' }); };
```

- `:1114-1115`, `:1181-1182` 의 `invalidateStoryboardEntryPrefetch(projectId);` + `storyboardPrefetchProjectRef.current = null;` 두 쌍을 삭제한다. 콘티 시드는 `fitProfile` 을 쓰지 않으므로(설계 문서 "왜 가능한가") 핏 변경으로 콘티 프리페치를 버릴 이유가 없다.
- import 구역(`:33-35`)에서 `invalidateStoryboardEntryPrefetch`·`prefetchStoryboardEntry` 를 제거한다. 남는 import 가 없으면 그 줄 전체를 지운다.

- [ ] **Step 4: 입력 화면에 워밍 추가**

`src/features/product-input/ProductInput.jsx`:

import 추가:

```jsx
import { prefetchStoryboardEntry } from '@/features/storyboard/storyboardEntryPrefetch.js';
```

컴포넌트 훅 구역(`redirectingRef` 선언 근처, `:~281`)에 ref 를 추가한다:

```jsx
  const storyboardPrefetchProjectRef = useRef(null);
```

그리고 서버 project 가 확정된 뒤 한 번만 데우는 이펙트를 추가한다:

```jsx
  // 분석 결과를 사용자가 검토하는 동안 다음 화면(콘티)을 미리 데운다 — 서버 project 가 있을 때만.
  useEffect(() => {
    if (!analysisProjectId) return;
    if (storyboardPrefetchProjectRef.current === analysisProjectId) return;
    storyboardPrefetchProjectRef.current = analysisProjectId;
    void prefetchStoryboardEntry(analysisProjectId);
  }, [analysisProjectId]);
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `node --test tests/frontend/storyboard-prefetch.test.mjs`
Expected: PASS (5 tests)

- [ ] **Step 6: 빌드 + 전체 테스트**

Run: `pnpm build && pnpm test:frontend`
Expected: 실패 총계 1 (베이스라인 뿐)

- [ ] **Step 7: 커밋**

```bash
git add src/features/mannequin/Mannequin.jsx \
        src/features/product-input/ProductInput.jsx \
        tests/frontend/storyboard-prefetch.test.mjs
git commit -m "refactor(storyboard): warm the board from the screen that now precedes it"
```

---

## Task 4: `refreshForEdits` 를 store 플래그로

입력에서 분석을 고친 뒤 마네킹이 재생성해야 하는지를 route state 로 넘기는데, 사이에 콘티가 끼면 state 가 증발한다. store 에 이미 있는 플래그를 직접 읽게 한다.

**Files:**
- Modify: `src/features/product-input/ProductInput.jsx:302-305`
- Modify: `src/features/mannequin/Mannequin.jsx:1137-1148`

**Interfaces:**
- Consumes: `useAppStore` 의 `generationRelevantEditsDirty`(`src/store/useAppStore.js:123`), `clearGenerationRelevantEdits()`(`:244`)
- Produces: 없음

- [ ] **Step 1: 마네킹이 store 플래그를 읽게 변경**

`src/features/mannequin/Mannequin.jsx` 의 `refreshForEdits` 이펙트(`:1137-1148`)를 아래로 교체한다:

```jsx
  useEffect(() => {
    if (phase !== 'ready' || refreshForEditsHandledRef.current) return;
    if (!useAppStore.getState().generationRelevantEditsDirty) return;
    refreshForEditsHandledRef.current = true;
    // 먼저 플래그를 소비해 back/refresh/StrictMode 에서 유료 요청이 재발화하지 않게 한다.
    useAppStore.getState().clearGenerationRelevantEdits();
    if (initialCutsExistedRef.current) {
      regenerate();
    }
  }, [phase]);
```

원래 코드에 있던 `navigate(location.pathname, { replace: true, state: null })` 는 삭제한다. `location`·`useLocation` 이 이 파일에서 더 쓰이지 않으면 import 도 제거한다.

- [ ] **Step 2: 입력에서 route state 제거**

`src/features/product-input/ProductInput.jsx:302-305` 의 `routeState` 계산 3줄을 삭제하고, `navigate('/create/mannequin', { state: routeState })` 두 곳(`:307`, `:319`)에서 두 번째 인자를 뺀다:

```jsx
        navigate('/create/mannequin');
```

- [ ] **Step 3: 배선 테스트 추가**

`tests/frontend/parallel-flow-routing.test.mjs` 를 만든다 (Task 5 에서 같은 파일에 라우트 단언을 더 붙인다):

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

const mannequinSource = read('../../src/features/mannequin/Mannequin.jsx');
const productInputSource = read('../../src/features/product-input/ProductInput.jsx');

test('the regeneration signal travels in the store, not in router state', () => {
  // 입력 → 콘티 → 마네킹 사이에 화면이 하나 끼면 route state 는 증발한다.
  assert.doesNotMatch(productInputSource, /refreshForEdits/);
  assert.doesNotMatch(mannequinSource, /location\.state\?\.refreshForEdits/);
  assert.match(mannequinSource, /generationRelevantEditsDirty/);
  assert.match(mannequinSource, /clearGenerationRelevantEdits\(\)/);
});
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `node --test tests/frontend/parallel-flow-routing.test.mjs`
Expected: PASS (1 test)

- [ ] **Step 5: 빌드 + 전체 테스트**

Run: `pnpm build && pnpm test:frontend`
Expected: 실패 총계 1 (베이스라인 뿐)

- [ ] **Step 6: 커밋**

```bash
git add src/features/mannequin/Mannequin.jsx \
        src/features/product-input/ProductInput.jsx \
        tests/frontend/parallel-flow-routing.test.mjs
git commit -m "refactor(flow): carry the regeneration signal in the store instead of router state"
```

---

## Task 5: 순서 뒤집기 — 라우트·단계·CTA

여기서 실제로 흐름이 `입력 → 콘티 → 마네킹 → 생성` 이 된다.

**Files:**
- Create: `src/lib/wizardSteps.js`
- Create: `tests/frontend/wizard-step-order.test.mjs`
- Modify: `src/features/shell/shell.jsx:14-24`, `:40`, `:41-49`
- Modify: `src/features/product-input/ProductInput.jsx:307`, `:319`, `:322`
- Modify: `src/App.jsx:206-221`
- Modify: `src/features/storyboard/Storyboard.jsx:2640-2649`, `:2686`, `:2693-2698`
- Modify: `src/features/mannequin/Mannequin.jsx:1197`, `:713-726`, CTA 렌더
- Modify: `tests/frontend/parallel-flow-routing.test.mjs`

**Interfaces:**
- Consumes: Task 4 의 store 플래그 배선
- Produces: `WIZARD_STEPS: Array<{key, label}>`, `STEP_INDEX: Record<string, number>` (`src/lib/wizardSteps.js`) — `shell.jsx` 가 재수출해 기존 import 경로를 유지

- [ ] **Step 1: 실패하는 순서 테스트 작성**

`tests/frontend/wizard-step-order.test.mjs`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';

import { WIZARD_STEPS, STEP_INDEX } from '../../src/lib/wizardSteps.js';

test('the wizard walks input, storyboard, mannequin, editor', () => {
  assert.deepEqual(WIZARD_STEPS.map((s) => s.key), ['input', 'storyboard', 'mannequin', 'editor']);
});

test('every route step maps onto its dot', () => {
  assert.equal(STEP_INDEX.input, 0);
  assert.equal(STEP_INDEX.analysis, 0);
  assert.equal(STEP_INDEX.storyboard, 1);
  assert.equal(STEP_INDEX.mannequin, 2);
  assert.equal(STEP_INDEX.generating, 3);
  assert.equal(STEP_INDEX.editor, 3);
});

test('no step index points past the last dot', () => {
  for (const index of Object.values(STEP_INDEX)) {
    assert.ok(index < WIZARD_STEPS.length, `step index ${index} has no dot`);
  }
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `node --test tests/frontend/wizard-step-order.test.mjs`
Expected: FAIL — `Cannot find module '.../src/lib/wizardSteps.js'`

- [ ] **Step 3: 단계 상수 추출**

`src/lib/wizardSteps.js`:

```js
/* 제작 마법사의 단계 정의 — 마네킹 생성이 오래 걸려 콘티보다 뒤에 온다. 사용자는 콘티를 짜는
   동안 생성이 백그라운드로 돌게 두고, 마네킹 화면에서 결과를 확인한다.
   shell.jsx(React 의존) 밖에 두어 node --test 로 순서 회귀를 잡는다. */
export const WIZARD_STEPS = [
  { key: 'input', label: '제품 정보·분석' },
  { key: 'storyboard', label: '콘티보드' },
  { key: 'mannequin', label: '마네킹컷' },
  { key: 'editor', label: '에디터' },
];

/* input+analysis 는 0번으로 합치고, generating 은 editor 단계를 공유한다. */
export const STEP_INDEX = {
  input: 0,
  analysis: 0,
  storyboard: 1,
  mannequin: 2,
  generating: 3,
  editor: 3,
};
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `node --test tests/frontend/wizard-step-order.test.mjs`
Expected: PASS (3 tests)

- [ ] **Step 5: `shell.jsx` 가 재수출하게 변경**

`src/features/shell/shell.jsx:14-22` 의 두 상수 정의를 삭제하고 재수출로 바꾼다 (외부 import 경로 유지):

```jsx
export { WIZARD_STEPS, STEP_INDEX } from '@/lib/wizardSteps.js';
```

`Stepper`(`:159-171`)가 같은 모듈 안에서 두 상수를 쓰므로 값 import 도 함께 넣는다:

```jsx
import { WIZARD_STEPS, STEP_INDEX } from '@/lib/wizardSteps.js';
```

`:24` 의 `STEPPER_STEPS` 는 키 집합이 같으므로 그대로 둔다.

- [ ] **Step 6: 진행 중 강제 이동·재개 폴백 수정**

`src/features/shell/shell.jsx`:

- `:40` `resumeWork` 폴백을 바꾼다:

```jsx
  const resumeWork = () => { setResumeAsk(false); navigate(useAppStore.getState().resumePath || '/create/storyboard'); };
```

- `:43` 의 아래 줄을 삭제한다. 생성이 도는 동안 사용자가 있어야 할 곳은 콘티다:

```jsx
      if (mannequinJob?.status === 'running') { navigate('/create/mannequin'); return; }
```

`mannequinJob` 셀렉터(`:32`)가 더 쓰이지 않으면 함께 제거한다.

- [ ] **Step 7: 입력 CTA 목적지 변경**

`src/features/product-input/ProductInput.jsx`:

- 함수명 `goToMannequin`(`:278`) → `goToStoryboard`. 호출부 `:563`, `:662` 도 함께 바꾼다.
- `:307`, `:319` 의 `navigate('/create/mannequin')` → `navigate('/create/storyboard')`
- `:322` 의 `openLogin('/create/mannequin')` → `openLogin('/create/storyboard')`
- `:336` 부근 토스트 문구 `'상품 사실 검토를 승인한 뒤 마네킹 생성을 시작해 주세요.'` 처럼 마네킹을 가리키는 문구가 있으면 `'…콘티 구성을 시작해 주세요.'` 로 바꾼다.

- [ ] **Step 8: 로그인 복귀 목표 변경**

`src/App.jsx:206-221` 에서 `wantsMannequin` 을 `wantsStoryboard` 로 개명하고 비교 대상과 성공 목적지를 바꾼다:

```jsx
      const wantsStoryboard = target === '/create/storyboard';
      if (!session) { setDest(wantsStoryboard ? '/create/input' : target); setPhase('done'); return; }
```

```jsx
      if (!(wantsStoryboard && mode === 'http' && hasPendingDraft())) {
```

```jsx
        setDest('/create/storyboard'); setPhase('done');
```

- [ ] **Step 9: 콘티 CTA·이전 버튼 변경**

`src/features/storyboard/Storyboard.jsx`:

- `generate`(`:2640-2649`)를 이름과 목적지 모두 바꾼다. 저장은 유지한다 — 마네킹·생성이 서버에 저장된 콘티를 읽는다:

```jsx
  const goToMannequin = async () => {
    if (blocks.length === 0) return;
    if (blocks.some((b) => b.source !== 'mine' && (!b.contentRole || !b.cutType))) { toast.push('생성 설정을 준비하지 못한 이미지가 있어요'); return; }
    // 생성 입력은 서버가 저장된 콘티에서 읽는다 — 다음 단계로 넘기기 전에 반드시 저장.
    await saveNow(projectId);
    navigate('/create/mannequin');
  };
```

- `:2686` 의 '이전' 목적지를 입력으로 바꾼다:

```jsx
          <button className="btn btn-ghost" onClick={() => navigate('/create/input')}><Icon name="arrowLeft" size={17} />이전</button>
```

- `:2693-2698` 의 CTA 를 바꾼다. 이 버튼은 이제 크레딧을 쓰지 않으므로 금액을 옆 카운트 줄로 내린다:

```jsx
          <div className="sb-ab-count">
            AI 생성 {aiCount}컷 · 셀러 사진 {mineCount}컷
            <span className="sb-ab-cost"> · 생성 시 {aiCount * (catalogs.creditCosts?.storyboardPerCut ?? 1)} 크레딧</span>
          </div>
```

```jsx
          <button className="btn btn-primary btn-lg sb-ab-go btn-glowring" onClick={goToMannequin}
            disabled={blocks.length === 0 || blocks.some((b) => b.source !== 'mine' && (!b.contentRole || !b.cutType))}
            title={blocks.length === 0 ? '컷을 1개 이상 구성해주세요'
              : blocks.some((b) => b.source !== 'mine' && (!b.contentRole || !b.cutType)) ? '생성 설정을 준비하지 못한 이미지가 있어요' : undefined}>
            다음 · 마네킹컷 확인하기 <Icon name="arrowRight" size={17} />
          </button>
```

`src/styles/features.css` 에 보조 문구 스타일을 추가한다:

```css
.sb-ab-cost { color: var(--fg-2); }
```

- [ ] **Step 10: 마네킹 CTA 를 생성으로 연결**

`src/features/mannequin/Mannequin.jsx`:

- `loadMannequins`(`:713-726`)의 `Promise.all` 에 콘티를 추가해 CTA 에 붙일 크레딧 수를 구한다:

```jsx
      const [nextProduct, nextAnalysis, nextCatalogs, nextStoryboard] = await Promise.all([
        api.getProduct(pid),
        api.getAnalysis(pid),
        api.getCatalogs(),
        api.getStoryboard(pid).catch(() => []),
      ]);
```

바로 아래에 상태 반영을 추가한다 (`const [aiCutCount, setAiCutCount] = useState(0);` 를 훅 구역에 선언):

```jsx
      setAiCutCount((nextStoryboard || []).filter((b) => b.source !== 'mine').length);
```

- `onCta` 의 마지막 이동(`:1197`)을 생성으로 바꾼다:

```jsx
      navigate('/create/generating');
```

- Task 2 에서 남긴 최종 CTA 문구에 크레딧을 붙인다:

```jsx
            <Button variant="primary" size="lg" block iconRight="arrowRight" disabled={busy} onClick={onCta}>
              상세페이지 생성하기 · {aiCutCount * (catalogs?.creditCosts?.storyboardPerCut ?? 1)} 크레딧
            </Button>
```

- [ ] **Step 11: 라우팅 테스트 확장**

`tests/frontend/parallel-flow-routing.test.mjs` 에 추가한다:

```js
const appSource = read('../../src/App.jsx');
const storyboardSource = read('../../src/features/storyboard/Storyboard.jsx');
const shellSource = read('../../src/features/shell/shell.jsx');

test('the input CTA now opens the storyboard', () => {
  assert.match(productInputSource, /const goToStoryboard = async \(opts\) =>/);
  assert.doesNotMatch(productInputSource, /navigate\('\/create\/mannequin'/);
  assert.match(productInputSource, /openLogin\('\/create\/storyboard'\)/);
});

test('login return lands on the storyboard', () => {
  assert.match(appSource, /const wantsStoryboard = target === '\/create\/storyboard'/);
  assert.match(appSource, /setDest\('\/create\/storyboard'\)/);
});

test('the storyboard hands off to the mannequin, and back to input', () => {
  assert.match(storyboardSource, /const goToMannequin = async \(\) => \{/);
  assert.match(storyboardSource, /await saveNow\(projectId\);\s*\n\s*navigate\('\/create\/mannequin'\)/);
  assert.match(storyboardSource, /이전<\/button>/);
  assert.match(storyboardSource, /navigate\('\/create\/input'\)/);
  assert.doesNotMatch(storyboardSource, /navigate\('\/create\/generating'\)/);
});

test('the mannequin is the last stop before generation', () => {
  assert.match(mannequinSource, /navigate\('\/create\/generating'\)/);
  assert.doesNotMatch(mannequinSource, /navigate\('\/create\/storyboard'\)/);
});

test('a running job no longer yanks the user onto the mannequin screen', () => {
  assert.doesNotMatch(shellSource, /mannequinJob\?\.status === 'running'/);
  assert.match(shellSource, /resumePath \|\| '\/create\/storyboard'/);
});
```

- [ ] **Step 12: 테스트 통과 확인**

Run: `node --test tests/frontend/parallel-flow-routing.test.mjs tests/frontend/wizard-step-order.test.mjs`
Expected: PASS (전부)

- [ ] **Step 13: 빌드 + 전체 테스트**

Run: `pnpm build && pnpm test:frontend`
Expected: 실패 총계 1 (베이스라인 뿐)

- [ ] **Step 14: 커밋**

```bash
git add src/lib/wizardSteps.js src/features/shell/shell.jsx src/App.jsx \
        src/features/product-input/ProductInput.jsx \
        src/features/storyboard/Storyboard.jsx \
        src/features/mannequin/Mannequin.jsx \
        src/styles/features.css \
        tests/frontend/wizard-step-order.test.mjs \
        tests/frontend/parallel-flow-routing.test.mjs
git commit -m "feat(flow): put the storyboard before the mannequin step"
```

---

## Task 6: 콘티 진입 시 생성 발사

여기서 병렬화가 실제로 생긴다.

**Files:**
- Modify: `src/features/storyboard/Storyboard.jsx:1576-1637`
- Modify: `tests/frontend/parallel-flow-routing.test.mjs`

**Interfaces:**
- Consumes: `requestMannequinGeneration(pid)` (Task 1)
- Produces: 없음

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/frontend/parallel-flow-routing.test.mjs` 에 추가한다:

```js
test('the storyboard fires mannequin generation as it loads', () => {
  assert.match(storyboardSource, /import \{ requestMannequinGeneration \} from '@\/features\/mannequin\/generationRunner\.js'/);
  // 발사는 보드 로드를 막지 않는다 — await 하면 병렬화가 사라진다.
  assert.match(storyboardSource, /void requestMannequinGeneration\(pid\)\.catch\(\(\) => \{\}\)/);
  assert.doesNotMatch(storyboardSource, /await requestMannequinGeneration/);
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `node --test tests/frontend/parallel-flow-routing.test.mjs`
Expected: FAIL — 발사 코드 없음

- [ ] **Step 3: 발사 배선**

`src/features/storyboard/Storyboard.jsx` import 추가:

```jsx
import { requestMannequinGeneration } from '@/features/mannequin/generationRunner.js';
```

로드 이펙트(`:1576`)에서 `pidRef.current = pid;` 바로 다음 줄에 발사를 넣는다:

```jsx
        // 마네킹컷 생성은 오래 걸린다 — 사용자가 콘티를 짜는 동안 백그라운드로 돌린다.
        // await 하지 않는다: 보드 로드가 생성 완료를 기다리면 병렬화가 사라진다. 실패는 리본과
        // 마네킹 화면이 각각 보고하므로 여기선 삼킨다. 중복 호출은 러너와 서버가 함께 흡수한다.
        void requestMannequinGeneration(pid).catch(() => {});
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `node --test tests/frontend/parallel-flow-routing.test.mjs`
Expected: PASS

- [ ] **Step 5: 빌드 + 전체 테스트**

Run: `pnpm build && pnpm test:frontend`
Expected: 실패 총계 1 (베이스라인 뿐)

- [ ] **Step 6: 커밋**

```bash
git add src/features/storyboard/Storyboard.jsx tests/frontend/parallel-flow-routing.test.mjs
git commit -m "feat(storyboard): start mannequin generation while the user composes"
```

---

## Task 7: 진행률 리본 완료 배지

지금 리본은 job 이 끝나면 즉시 사라져 사용자가 끝난 걸 모른다. 그리고 '마네킹 화면 보기' 버튼은 새 순서에서 앞지르기를 유도해 혼란을 만든다.

**Files:**
- Modify: `src/features/shell/ChromeLayout.jsx:13-46`
- Modify: `src/styles/app.css:147-150`
- Modify: `tests/frontend/parallel-flow-routing.test.mjs`

**Interfaces:**
- Consumes: `useAppStore` 의 `mannequinJob { status, projectId, progress, errorMessage }`
- Produces: 없음

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/frontend/parallel-flow-routing.test.mjs` 에 추가한다:

```js
const chromeSource = read('../../src/features/shell/ChromeLayout.jsx');

test('the ribbon announces completion and stops steering', () => {
  assert.match(chromeSource, /마네킹컷 준비 완료/);
  assert.match(chromeSource, /DONE_BADGE_MS/);
  assert.doesNotMatch(chromeSource, /마네킹 화면 보기/);
  assert.doesNotMatch(chromeSource, /job-ribbon-btn/);
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `node --test tests/frontend/parallel-flow-routing.test.mjs`
Expected: FAIL — 배지 문구·상수 없음, 버튼이 아직 있음

- [ ] **Step 3: 리본 교체**

`src/features/shell/ChromeLayout.jsx:13-46` 의 `MannequinJobRibbon` 전체를 아래로 교체한다. `useNavigate` 가 이 파일에서 더 쓰이지 않으면 import 에서 제거한다:

```jsx
const DONE_BADGE_MS = 3000;

function MannequinJobRibbon() {
  const { pathname } = useLocation();
  const projectId = useAppStore((s) => s.projectId);
  const job = useAppStore((s) => s.mannequinJob);
  const [doneBadge, setDoneBadge] = useState(false);
  const wasRunningRef = useRef(false);

  // 끝난 순간을 짧게 알린다 — 지금은 idle 로 돌아가며 리본이 즉시 사라져 완료를 놓친다.
  useEffect(() => {
    if (job?.status === 'running') { wasRunningRef.current = true; return undefined; }
    if (job?.status !== 'idle' || !wasRunningRef.current) return undefined;
    wasRunningRef.current = false;
    setDoneBadge(true);
    const timer = setTimeout(() => setDoneBadge(false), DONE_BADGE_MS);
    return () => clearTimeout(timer);
  }, [job?.status]);

  if (!job || pathname.startsWith('/create/mannequin')) return null;
  if (job.projectId && projectId && job.projectId !== projectId) return null;
  if (job.status === 'idle' && !doneBadge) return null;

  if (job.status === 'idle') {
    return (
      <div className="job-ribbon done" role="status" aria-live="polite">
        <div className="job-ribbon-main">
          <span className="job-ribbon-label"><Icon name="check" size={15} />마네킹컷 준비 완료</span>
        </div>
      </div>
    );
  }

  const isError = job.status === 'error';
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  const label = isError ? '마네킹컷 생성에 실패했어요' : '마네킹컷을 만들고 있어요';
  const detail = isError ? (job.errorMessage || '다시 시도할 수 있어요.') : `${progress}%`;

  return (
    <div className={`job-ribbon${isError ? ' error' : ''}`} role={isError ? 'alert' : 'status'} aria-live="polite">
      <div className="job-ribbon-main">
        <span className="job-ribbon-label">
          <Icon name={isError ? 'alertTri' : 'loader'} size={15} className={isError ? '' : 'spin'} />
          {label}
        </span>
        {!isError && (
          <div className="job-ribbon-track" aria-hidden="true">
            <i className="job-ribbon-fill" style={{ width: `${progress}%` }} />
          </div>
        )}
        <span className="job-ribbon-detail">{detail}</span>
      </div>
    </div>
  );
}
```

`:6` 의 React import 를 훅에 맞춰 넓힌다:

```jsx
import { useEffect, useRef, useState } from 'react';
```

- [ ] **Step 4: 완료 배지 스타일 추가, 죽은 버튼 규칙 제거**

`src/styles/app.css` 에서 `.job-ribbon-btn` 과 `.job-ribbon-btn:hover` 두 규칙(`:147-149`)을 삭제하고, `.job-ribbon.error …` 규칙(`:150`) 옆에 완료 배지 규칙을 추가한다:

```css
.job-ribbon.done .job-ribbon-label { color: var(--accent-success); }
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `node --test tests/frontend/parallel-flow-routing.test.mjs`
Expected: PASS

- [ ] **Step 6: 빌드 + 전체 테스트**

Run: `pnpm build && pnpm test:frontend`
Expected: 실패 총계 1 (베이스라인 뿐)

- [ ] **Step 7: 커밋**

```bash
git add src/features/shell/ChromeLayout.jsx src/styles/app.css \
        tests/frontend/parallel-flow-routing.test.mjs
git commit -m "feat(shell): let the progress ribbon report completion"
```

---

## Task 8: 실서버 수동 관통 검증

자동 테스트는 배선을 보지만 크레딧 차감 횟수와 진행률 체감은 못 본다.

**Files:** 없음 (검증만)

- [ ] **Step 1: worktree 환경 준비**

```bash
cd /Users/nojeong-un/devs/wearless_studio-conti-parallel
cp ../wearless_studio/.env ../wearless_studio/.env.local .
pnpm install
```

- [ ] **Step 2: 개발 서버 기동**

Run: `pnpm dev`
백엔드는 `.env.local` 의 `VITE_API_BASE_URL` 이 가리키는 곳을 그대로 쓴다.

- [ ] **Step 3: 관통 시나리오 실행**

계정 크레딧 잔액을 먼저 적어둔다. 그 다음:

1. `/create/input` 에서 사진을 넣고 AI 분석 → 로그인 → 콘티로 진입하는지 확인
2. 콘티 진입 직후 상단 리본에 `마네킹컷을 만들고 있어요 … %` 가 뜨는지, 퍼센트가 오르는지 확인
3. 콘티에서 사진 양을 반대 값으로 토글 → 손대지 않은 보드가 새 컷 수로 재시드되는지 확인
4. 콘티에서 컷 하나를 수정한 뒤 사진 양을 다시 토글 → 이번엔 보드가 **유지**되는지 확인
5. 리본이 `마네킹컷 준비 완료` 로 바뀌고 3초 뒤 사라지는지 확인
6. `다음 · 마네킹컷 확인하기` → 마네킹 화면이 대기 없이 열리는지 확인
7. 핏 질문을 모두 답하고 `상세페이지 생성하기` → `/create/generating` 진입 확인
8. 크레딧 잔액이 `마네킹 생성 1회 + 콘티 컷 수 × 단가` 만큼만 줄었는지 확인 — **마네킹 생성이 2회 차감됐다면 러너 합류가 깨진 것이다**

- [ ] **Step 4: 새로고침 시나리오**

콘티에서 생성이 도는 중 브라우저 새로고침 → 크레딧이 추가로 줄지 않는지 확인한다(서버가 활성 job 에 합류). 리본 진행률은 0 부터 다시 붙어도 정상이다 — `mannequinJob` 은 영속 대상이 아니다.

- [ ] **Step 5: 되돌아가기 시나리오**

콘티에서 `이전` → 입력에서 분석을 생성에 영향 있는 값으로 수정 → 콘티 → 마네킹 순으로 진행했을 때 마네킹이 재생성을 트리거하는지 확인한다(Task 4 의 store 플래그 경로).

- [ ] **Step 6: 결과 기록 후 커밋**

관찰 결과를 `docs/superpowers/specs/2026-08-07-parallel-mannequin-conti-design.md` 하단에 "## 수동 검증 기록 (2026-MM-DD)" 로 덧붙인다. 크레딧 차감 실측값을 반드시 남긴다.

```bash
git add docs/superpowers/specs/2026-08-07-parallel-mannequin-conti-design.md
git commit -m "docs: record the manual walkthrough of the parallel flow"
```

---

## Self-Review

**스펙 커버리지**

| 스펙 절 | 담당 |
|---|---|
| §1 사진 양 이동 | Task 2 |
| §2 마네킹 job 발사 + 멱등성 | Task 1(러너), Task 6(발사), Task 8 Step 4(멱등 실측) |
| §3 진행률 리본 | Task 7 |
| §4 라우팅·단계 재배치 | Task 5 |
| §5 `refreshForEdits` → store | Task 4 |
| §6 콘티 프리페치 이관 | Task 3 |
| §7 마네킹 페이지의 두 상태 | 코드 변경 없음 — 기존 `phase` 세 갈래(`Mannequin.jsx:1247-1248`)를 그대로 쓴다. Task 8 Step 6 에서 육안 확인 |
| 엣지 케이스 표 | Task 8 Step 3-5 |

**타입·이름 일관성**

- `requestMannequinGeneration(pid)` — Task 1 에서 정의, Task 6 에서 사용. 이름 일치
- `WIZARD_STEPS` / `STEP_INDEX` — Task 5 에서 이동, `shell.jsx` 가 같은 이름으로 재수출해 기존 소비자(`Stepper`) 무변경
- `goToStoryboard`(입력) / `goToMannequin`(콘티) — 화면별로 다음 목적지를 이름에 담는다. 서로 다른 파일이라 충돌 없음
- `onModeChange` / `onError` — Task 2 의 `ComposeModePicker` prop, 같은 Task 안에서 소비

**남은 판단**

- 마네킹 CTA 의 크레딧 표기를 위해 마네킹이 콘티를 한 번 더 읽는다(Task 5 Step 10). 요청 1건 추가지만 차감 직전 금액 고지를 유지하는 값이 더 크다고 봤다.
- `.fit-cmp*` 를 옮기며 `.sb-cmp*` 로 개명한다. 마네킹 페이지에 남는 다른 `.fit-*` 규칙과 섞이지 않게 하려는 것이고, 마네킹에는 이 클래스를 쓰는 곳이 더 없다.
