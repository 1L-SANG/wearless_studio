# FaceMarket 랜딩페이지 구현 계획

> **상태: 구현 완료 (2026-09-01). 이 문서는 착수 시점의 계획이고, 아래 코드 블록 몇 개는
> 실제로 머지된 코드와 다르다.** 구현이 다섯 라운드의 감사·수리를 거치며 갈라졌다. 갈라진
> 자리에는 그 자리에 `> **[구현과 다름]**` 블록을 달아 뒀고, 전체 목록은 문서 끝
> [§Self-Review > 계획과 구현이 갈라진 곳](#계획과-구현이-갈라진-곳-감사-후-갱신)에 있다.
> **이 계획서의 코드 블록을 복사해 되돌리지 마라 — 그중 하나(Task 4 Step 5)는 이 도메인의
> 로그인을 통째로 깨뜨린 critical 버그였다.** 현행 설계의 정본은 스펙 문서다.

> ## ✅ 한때 출시 게이트였던 것 — 해소됨 (2026-09-01)
>
> 이 자리에 "prod api 가 us-east-1 이라 OACX 가 한국 IP 방화벽에 막혀 등록 STEP 2 에서
> 전원이 차단되니 공개하지 마라"는 게이트가 적혀 있었다. **사실이 아니었다.**
> `documents/FACEMARKET_PRD.md` §12 표가 배포 설정보다 오래된 것이었고, 실제로는
> `copilot/api/manifest.yml` 의 `CX_TRANS_BASE_URL` 이 서울 리전 프록시(`fm-cx-proxy`,
> API GW→Lambda)로 이미 우회한다. 2026-08-30·08-31 prod 신원확인 2건 통과로 실증했다.
> 즉 Task 7 의 "일곱 단계면 끝납니다"는 원래부터 맞는 말이었다.
>
> **다만 그 우회는 env 값 하나에 매달려 있다** — 지워지면 기본값(CX 직접 호출)으로 떨어져
> 2단계가 다시 막힌다. 자세한 내용과 교훈은 스펙 문서 맨 위 §해소됨 절에 있다.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `facemarket.wearless.kr` 루트에 모델 대상 랜딩페이지를 만든다. 이 작업 전까지 그 자리는 랜딩 없이 `/model/register` 로 직행했다.

**Architecture:** spotlight-webgl-gallery 프로토타입의 캐러셀을 CSS 3D 로 재구현해(three 없이) 랜딩 한 장에 얹는다. 상단바 세 항목은 실제 라우트가 아니라 같은 페이지 섹션 앵커다. 랜딩은 `ChromeLayout` 밖 독립 surface 이고, 로그인 복귀 경로(`wl_postLogin`) 소비를 `RootRedirect` 에게서 이어받는다.

**Tech Stack:** React 18.3 · JSX · React Router 6 · CSS Modules · Vite 6 · `node:test`

**Spec:** `docs/superpowers/specs/2026-09-01-facemarket-landing-design.md`

## Global Constraints

모든 태스크의 요구사항에 아래가 암묵적으로 포함된다.

- **신규 npm 의존성 0개.** `three` · `@react-three/fiber` · React 19 · TypeScript 를 들이지 않는다. `package.json` 을 수정하지 않는다.
- **JSX 만 쓴다.** 이 앱의 프런트는 `.jsx`/`.js` 다. 새 파일에 `.tsx`/`.ts` 를 만들지 않는다.
- **import alias 는 `@` → `src`** (`vite.config.js`). 기능 간 import 는 `@/lib/...` 형태로 쓴다.
- **테스트는 `node:test` + `node:assert/strict`**, 파일은 `tests/frontend/*.test.mjs`, 실행은 `pnpm test:frontend`. 새 테스트 러너를 들이지 않는다. 렌더 테스트 인프라는 이 레포에 없다 — DOM 상호작용은 브라우저에서 육안 확인한다.
- **패키지 매니저는 pnpm** (`pnpm-lock.yaml`).
- **얼굴 프라이버시 하드룰**(`documents/FACEMARKET_PRD.md` §10): 랜딩은 `public/models` 정적 가상모델 이미지만 쓴다. 실제 등록 모델의 얼굴을 `<img src>` 로 렌더하는 코드를 절대 넣지 않는다. `listModels`·`fetchLicenseFaceUrl` 을 랜딩에서 호출하지 않는다.
- **카드 메타는 번호만.** 이름·연도·평점·카테고리를 지어내지 않는다. 이건 사용자가 따로 지시할 항목이다. (구현: 번호 옆에 '예시' 배지가 함께 박힌다 — 그건 **메타가 아니라 고지**다. 이미지만 잘려 공유돼도 가상 모델이라는 사실이 같이 나가야 해서 카드 안에 둔다. `facemarket-landing-models.test.mjs` 가 데이터 쪽 메타가 `id/src/alt` 밖으로 늘지 않는지 지킨다.)
- **"예시 이미지 — 실제 등록 모델이 아닙니다"** 고지가 캐러셀 근처에 상시 보여야 한다. (구현: **스테이지 위**다. 아래에 두면 폰에서 스테이지 높이 때문에 고지가 뷰포트 밖으로 밀린다.)
- **법정 문구는 그대로.** `생체정보 처리 동의` 라는 표현을 다른 말로 바꾸지 않는다(하드룰 7).
- **브랜치는 `feat/facemarket-landing`.** 이미 만들어져 있고 스펙 커밋 `7700c5e8` 이 올라가 있다.
- 로컬 확인은 `pnpm dev` 후 `http://localhost:5173/?facemarket=1` (호스트 분기 강제, `src/lib/host.js`).

---

## File Structure

**신규**

| 파일 | 책임 |
|---|---|
| `src/features/facemarket-landing/carousel/carouselMath.js` | 루프 인덱스 산술. 순수 |
| `src/features/facemarket-landing/carousel/sceneLayout.js` | offset → 3D 배치값. 순수 |
| `src/features/facemarket-landing/carousel/cssProjection.js` | three 카메라 좌표 → CSS 픽셀 변환. 순수 |
| `src/features/facemarket-landing/carousel/useCarouselController.js` | 포인터 드래그·키보드 입력 → target |
| `src/features/facemarket-landing/carousel/CarouselStage.jsx` | 카드 DOM + rAF 트랜스폼 |
| `src/features/facemarket-landing/carousel/CarouselStage.module.css` | 스테이지·카드 스타일 |
| `src/features/facemarket-landing/data/landingModels.js` | 가상모델 이미지 14장 목록 |
| `src/features/facemarket-landing/facemarketRootTarget.js` | 로그인 복귀 경로 판정. 순수 |
| `src/features/facemarket-landing/registerCta.js` | 등록 상태 → CTA 문구·경로. 순수 |
| `src/features/facemarket-landing/FacemarketRoot.jsx` | `/` 진입점. 복귀 경로면 이동, 아니면 랜딩. **플래그 삭제는 `settled && target` + 값 대조로 좁혀져 있다** — 넓히면 자식이 방금 심은 복귀 목표를 먹는다(아래 갈라진 곳 표) |
| `src/features/facemarket-landing/FacemarketLanding.jsx` | **섹션 조립만이 아니다.** head 교체(마운트 중 title·description, **복원은 `IS_FACEMARKET` 으로 가른다**)·등록 상태별 CTA 조회·부트스트랩 중 클릭 보류함(`pendingPrimary`, **ref 아니라 state**)까지 여기 산다. `onPrimary`·`primaryLabel` 한 쌍을 상단바·히어로·등록 섹션·모델 정보 섹션 **넷**에 내려보낸다 |
| `src/features/facemarket-landing/LandingHeader.jsx` | 상단바(brand · 앵커 3개 · 상태별 CTA — 비로그인이면 이 버튼이 로그인 모달을 연다). **CTA 를 `disabled` 로 잠그지 않는 건 의도다** — 헤더 주석과 스펙 §1 참고 |
| `src/features/facemarket-landing/FacemarketLanding.module.css` | 랜딩 토큰 스코프 + 섹션 스타일. **상단바와 섹션 6개가 전부 이 한 벌을 import 한다**(섹션별 CSS 모듈은 없다). 캐러셀만 자기 것(`CarouselStage.module.css`)을 쓴다 |
| `src/features/facemarket-landing/sections/HeroSection.jsx` | 히어로 |
| `src/features/facemarket-landing/sections/GallerySection.jsx` | 캐러셀 + 컨트롤 + 예시 고지 |
| `src/features/facemarket-landing/sections/LicensingSection.jsx` | 카드 6장 + "확인할 수 있는 기록" 4칸 (계획 원안은 3칸 "검증 가능한 기록") |
| `src/features/facemarket-landing/sections/RegisterSection.jsx` | 7단계 레일 + 상태별 CTA |
| `src/features/facemarket-landing/sections/ModelInfoSection.jsx` | 프라이버시 하드룰 7개 + '그만두고 싶을 때' + **페이지의 마지막 CTA**(prop 은 `RegisterSection` 과 같은 `ctaLabel`·`onPrimary` 둘. 새 이름을 만들지 마라) |
| `src/features/facemarket-landing/sections/FooterSection.jsx` | 푸터 |
| `tests/frontend/facemarket-carousel-math.test.mjs` | Task 1 테스트 |
| `tests/frontend/facemarket-css-projection.test.mjs` | Task 2 테스트 |
| `tests/frontend/facemarket-landing-models.test.mjs` | **계획 밖 추가.** 캐러셀 이미지 목록의 계약 — 가상모델 정적 경로만, 카드 메타는 번호만(프라이버시 하드룰 1이 순수 상수라 런타임 검증이 없다) |
| `tests/frontend/facemarket-landing-routing.test.mjs` | Task 4 테스트 |
| `tests/frontend/facemarket-register-cta.test.mjs` | Task 7 테스트 |

**수정**

계획 시점에는 `src/App.jsx` 하나였다. 감사·수리를 거치며 인증 경로까지 번졌다 —
랜딩이 로그인 복귀 계약을 넘겨받은 이상 그 계약의 반대편(플래그를 심고 지우는 쪽)을
안 건드릴 수 없었다.

| 파일 | 무엇을 |
|---|---|
| `src/App.jsx` | facemarket 루트 라우트 추가, `RootRedirect` 의 facemarket 분기 제거, catch-all 목적지 분기, `FacemarketLoginPrompt` 의 1회성 모달 열기 + 탈출 링크(모달을 닫은 사람이 주소창을 고치는 수밖에 없었다) |
| `src/features/auth/AuthProvider.jsx` | `openLogin` 을 `useCallback` 으로 안정화 — 매 렌더 새 identity 라 이 함수를 effect deps 에 둔 `FacemarketLoginPrompt` 가 "닫으면 즉시 다시 열리는" 모달이 됐다. `closeLogin({ cancelled })` 로 **취소일 때만** 복귀 플래그를 버린다. `sessionStorage` 접근을 전부 try/catch 로 감싼다(접근 자체가 던지는 브라우저가 있다 — 쓰기를 무방비로 두면 랜딩은 뜨는데 CTA 만 예외로 죽는 반쪽 하드닝이 된다) |
| `src/features/auth/Login.jsx` | 닫기에서 **취소와 성공을 가른다.** 로컬 이메일 로그인 성공과 진행 중 Esc 는 `cancelled: false` — 안 그러면 성공한 로그인의 복귀 목표가 지워져 ai 도메인의 콘티 승격이 스킵된다. 세션이 도착하면 모달이 스스로 닫힌다(단, **열릴 때 이미 세션이 있었으면 안 닫는다** — Editor 의 401 '다시 로그인'은 세션이 산 채로 뜨므로 토큰 갱신 한 번에 사라지면 회귀다). **그리고 모달 카피에 `IS_FACEMARKET` 분기가 생겼다** — 이 모달은 셀러 스튜디오 문구("Studio" 접미, "마네킹컷 생성으로 이어가세요")를 달고 있어서, 랜딩의 유일한 전환 버튼이 여는 첫 화면에서 **이 랜딩이 고치려던 실패(모델에게 셀러 제품을 보여주는 것)** 가 CTA 한 번 만에 재현됐다. `host.js` 의 `IS_FACEMARKET` 은 `App.jsx`·`shell.jsx` 가 이미 쓰는 검증된 분기점이다 |
| `src/features/shell/shell.jsx` | `openTopNavLogin` 이 도메인 가드 없이 항상 `'/create/input'` 을 심었다. 그 TopNav 는 facemarket 의 `/model/*` 에서도 렌더되므로 등록하러 온 모델의 복귀 의도가 셀러 경로로 덮이고, 랜딩의 화이트리스트가 그 값을 버려 등록이 아니라 랜딩으로 떨어졌다 → `IS_FACEMARKET ? '/model/register' : '/create/input'` |
| `index.html` | `og:*` · `meta[description]` 추가. **다만 두 도메인 공통(중립) 값이다** — 아래 참고 |

> **`index.html` 의 한계를 오해하지 마라.** 빌드 산출물은 `dist/index.html` 한 벌이고
> (vite 진입점 1개), `vercel.json` 이 전 경로를 그 한 벌로 rewrite 한다. 도메인 분기는
> `src/lib/host.js` 가 브라우저에서 hostname 을 읽는 **런타임** 분기라 정적 head 에 닿지
> 않는다. 그래서 og 값은 두 도메인 공통이고, `<title>` 은 셀러 것으로 남는다(크롤러는
> `og:title` 을 우선하고, facemarket 탭 제목은 `FacemarketLanding` 이 마운트 시 교체한다).
> **호스트별 미리보기는 여전히 미해결**이고, 하려면 (a) `rollupOptions.input` 으로
> `facemarket.html` 을 추가해 산출물을 두 벌 내고 (b) `vercel.json` rewrites 에 host 조건을
> 걸어야 한다 — (a) 없이 (b) 만 넣으면 facemarket 루트가 404 다. 또는 Edge Middleware.

---

### Task 1: 캐러셀 루프 산술과 배치값 이식

원본(`~/Documents/Codex/2026-09-01/new-chat/outputs/spotlight-webgl-gallery/src/features/gallery/`)의 `carouselMath.ts` · `sceneLayout.ts` 를 JS 로 옮긴다. 상수와 프로파일 값은 한 글자도 바꾸지 않는다 — 원본 영상에서 프레임 단위로 역산한 값이라 임의로 만지면 아크 형태가 무너진다.

**Files:**
- Create: `src/features/facemarket-landing/carousel/carouselMath.js`
- Create: `src/features/facemarket-landing/carousel/sceneLayout.js`
- Test: `tests/frontend/facemarket-carousel-math.test.mjs`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces:
  - `modulo(value: number, count: number) => number`
  - `shortestWrappedOffset(index: number, position: number, count: number) => number`
  - `targetForIndex(current: number, index: number, count: number) => number`
  - `snapTarget(position: number, velocityItemsPerSecond: number) => number`
  - ~~`rebaseTarget(position: number, count: number) => number`~~ — **채택하지 않음** (아래 참고)
  - `metricsForAspect(aspect: number) => { cardWidth, cardHeight, spacing, depthScale, edgeFade }`
  - `layoutForOffset(offset: number, metrics) => { x, y, z, rotationY, rotationZ, scale, opacity }`

> **[구현과 다름] `rebaseTarget` 은 이식하지 않았다 — 리베이스 가드 미채택.**
> 이 태스크의 Step 1 테스트 두 줄, Step 3 의 함수 정의, Task 3 의 import·호출이 전부
> 지워졌다. 사유: `CarouselStage` 의 렌더 위치는 `target` 을 감쇠로 쫓아가는 연속값이고
> **리베이스되지 않는다.** `target` 만 705 → 5 로 접으면 화면이 그 700칸(=50바퀴)을
> 실제로 훑고 내려간다 — layout 상 "같은 위치"인 것과 애니메이션이 같은 건 다르다.
> 안 잘라도 되는 이유: 정밀도가 상하려면 `|position|` 이 1e15 근처여야 하는데 한 칸이
> 드래그 170px 이라 1.7e17px 를 한 방향으로 끌어야 나온다. 되살릴 거면 `target` 과
> `CarouselStage` 의 `positionRef` 를 **같은 프레임에 같은 정수배만큼 함께** 접어야 한다
> (둘의 차이가 보존돼야 감쇠가 안 튄다). 현재 사유는 `carouselMath.js` 하단 주석이 정본.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/frontend/facemarket-carousel-math.test.mjs`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  modulo,                 // (실제 구현: rebaseTarget import 는 지워졌다)
  shortestWrappedOffset,
  snapTarget,
  targetForIndex,
} from '../../src/features/facemarket-landing/carousel/carouselMath.js';
import {
  layoutForOffset,
  metricsForAspect,
} from '../../src/features/facemarket-landing/carousel/sceneLayout.js';

const COUNT = 14;   // 랜딩 캐러셀 카드 수(가상모델 14장)

test('음수 위치를 카드 수 안으로 정규화한다', () => {
  assert.equal(modulo(-1, COUNT), 13);
  assert.equal(modulo(14, COUNT), 0);
});

test('루프 경계에서 최단 방향을 고른다', () => {
  // 마지막 카드에서 첫 카드는 앞으로 1칸이지 뒤로 13칸이 아니다.
  assert.equal(shortestWrappedOffset(0, 13, COUNT), 1);
  assert.equal(shortestWrappedOffset(13, 0, COUNT), -1);
});

test('연속 목표도 최단 방향으로 잡는다', () => {
  assert.equal(targetForIndex(13.2, 0, COUNT), 14);
  assert.equal(targetForIndex(0.2, 13, COUNT), -1);
});

test('스냅 전에 속도를 반영한다', () => {
  assert.equal(snapTarget(2.2, 1.4), 3);
  assert.equal(snapTarget(2.2, -1.4), 2);
});

// [구현과 다름] 이 테스트는 쓰지 않았다 — rebaseTarget 미채택(위 블록 참고).
// test('누적 위치가 커지면 한 바퀴 단위로 되돌린다', () => {
//   assert.equal(rebaseTarget(705, COUNT), 5);
//   assert.equal(rebaseTarget(12, COUNT), 12);   // 임계 아래면 그대로
// });

test('가운데 카드는 정면이고 원점이다', () => {
  const layout = layoutForOffset(0, metricsForAspect(1.6));
  assert.equal(layout.x, 0);
  assert.equal(layout.z, 0);
  assert.equal(layout.rotationY, 0);
  assert.equal(layout.scale, 1);
});

test('양옆 카드는 가운데를 향해 돌아선다', () => {
  const metrics = metricsForAspect(1.6);
  assert.ok(layoutForOffset(-2, metrics).rotationY > 0);
  assert.ok(layoutForOffset(2, metrics).rotationY < 0);
});

test('가까운 이웃은 거의 정면이고 바깥 카드가 크게 돌아선다', () => {
  const metrics = metricsForAspect(3.5);
  assert.ok(Math.abs(layoutForOffset(1, metrics).rotationY) < 0.15);
  assert.ok(Math.abs(layoutForOffset(3, metrics).rotationY) > 0.35);
});

test('오목 아크 — 바깥으로 갈수록 카메라 쪽으로 나온다', () => {
  const metrics = metricsForAspect(3.5);
  assert.ok(layoutForOffset(2, metrics).z > 0);
  assert.ok(layoutForOffset(3, metrics).z > layoutForOffset(1, metrics).z);
});

test('세로 화면은 간격이 좁고 가로 화면은 넓다', () => {
  assert.ok(metricsForAspect(0.55).spacing < metricsForAspect(3.5).spacing);
  assert.ok(metricsForAspect(3.5).spacing > 2);
});

test('종횡비 구간 경계값이 원본 그대로다', () => {
  assert.equal(metricsForAspect(1.09).cardWidth, 2.05);
  assert.equal(metricsForAspect(1.1).cardWidth, 1.7);
  assert.equal(metricsForAspect(2.29).cardWidth, 1.7);
  assert.equal(metricsForAspect(2.3).cardWidth, 1.64);
});

test('가장자리 페이드 밖 카드는 불투명도가 0이다', () => {
  const metrics = metricsForAspect(0.8);   // edgeFade 1.9
  assert.equal(layoutForOffset(3, metrics).opacity, 0);
  assert.ok(layoutForOffset(1, metrics).opacity > 0);
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pnpm test:frontend`
Expected: FAIL — `Cannot find module '.../carousel/carouselMath.js'`

- [ ] **Step 3: `carouselMath.js` 를 만든다**

```js
/* =============================================================
   facemarket-landing/carousel/carouselMath.js
   무한 루프 캐러셀의 인덱스 산술. DOM 을 모르는 순수 함수라
   node:test 로 직접 검증한다.
   spotlight 프로토타입(carouselMath.ts)에서 이식 — 상수는 원본 그대로.
   ============================================================= */

export const modulo = (value, count) => ((value % count) + count) % count;

/* 카드 index 가 현재 위치에서 몇 칸 떨어졌는지. 루프라 항상 최단 방향으로 답한다
   (14장에서 13 → 0 은 뒤로 13칸이 아니라 앞으로 1칸). */
export function shortestWrappedOffset(index, position, count) {
  const raw = index - modulo(position, count);
  return raw - Math.round(raw / count) * count;
}

/* 점을 눌러 특정 카드로 갈 때의 연속 목표값. 최단 방향으로 가되 누적 위치는 유지한다. */
export function targetForIndex(current, index, count) {
  return Number((current + shortestWrappedOffset(index, current, count)).toFixed(10));
}

/* 손을 뗄 때 관성을 반영해 가장 가까운 카드로 스냅한다. 0.24 는 원본 계수. */
export function snapTarget(position, velocityItemsPerSecond) {
  return Math.round(position + velocityItemsPerSecond * 0.24);
}

/* [구현과 다름] 이 함수는 만들지 않았다 — 리베이스 가드 미채택(위 블록 참고).
   실제 carouselMath.js 는 이 자리에 "왜 안 자르는지"를 적은 주석만 둔다.
export function rebaseTarget(position, count) {
  return Math.abs(position) < count * 50 ? position : modulo(position, count);
} */
```

- [ ] **Step 4: `sceneLayout.js` 를 만든다**

```js
/* =============================================================
   facemarket-landing/carousel/sceneLayout.js
   카드 offset → 3D 배치값. spotlight 프로토타입(sceneLayout.ts) 이식.

   X_STEPS·Z_STEPS·ROT_STEPS 는 원본 레퍼런스 영상에서 프레임 단위로
   역산한 값이다(카드 크기·화면 위치의 초점거리 역투영). 임의로 만지면
   오목 아크가 무너진다 — 바깥 카드가 카메라 쪽으로 밀려나와 가운데 카드보다
   약 47% 크게 읽히는 게 이 디자인의 정체성이다.
   ============================================================= */

/* 스테이지가 납작하고 넓어서 aspect 가 화면 형태를 그대로 따라간다.
   폰 ~0.8, 태블릿 ~1.9, 데스크톱 3+ */
export function metricsForAspect(aspect) {
  if (aspect < 1.1) return { cardWidth: 2.05, cardHeight: 2.87, spacing: 1.95, depthScale: 0.55, edgeFade: 1.9 };
  if (aspect < 2.3) return { cardWidth: 1.7, cardHeight: 2.38, spacing: 1.98, depthScale: 0.8, edgeFade: 2.6 };
  return { cardWidth: 1.64, cardHeight: 2.3, spacing: 2.25, depthScale: 1, edgeFade: 4 };
}

const X_STEPS = [0, 1, 1.881, 2.3, 2.55];
const Z_STEPS = [0, 0.49, 1.19, 2.75, 3.2];
const ROT_STEPS = [0, 0.08, 0.26, 0.48, 0.55];

/* |offset| 0..4 로 키가 잡힌 프로파일을 선형 보간한다 — 드래그 중 중간값이 필요해서. */
function sampleProfile(steps, distance) {
  const last = steps.length - 1;
  if (distance >= last) return steps[last];

  const index = Math.floor(distance);
  const t = distance - index;
  return steps[index] + (steps[index + 1] - steps[index]) * t;
}

export function layoutForOffset(offset, metrics) {
  const distance = Math.abs(offset);
  const direction = offset === 0 ? 0 : Math.sign(offset);

  return {
    // 오목 아크 — 가운데 카드가 가장 멀어 작게 읽히고, 이웃이 카메라 쪽으로 나오며 커진다.
    // 크기 변화는 원근이 만들므로 scale 은 1로 둔다. x 는 그 깊이를 미리 보정한 값이라
    // 화면상 카드 간격이 고르게 유지된다.
    x: direction * sampleProfile(X_STEPS, distance) * metrics.spacing,
    y: 0,
    z: sampleProfile(Z_STEPS, distance) * metrics.depthScale,
    rotationY: -direction * sampleProfile(ROT_STEPS, distance) || 0,
    rotationZ: -direction * Math.min(distance * 0.014, 0.04) || 0,
    scale: 1,
    opacity: Math.max(0, Math.min(1, metrics.edgeFade - distance)),
  };
}
```

- [ ] **Step 5: 테스트 통과를 확인한다**

Run: `pnpm test:frontend`
Expected: PASS — 이 파일의 12개 테스트 전부, 기존 테스트도 그대로 통과

- [ ] **Step 6: 커밋한다**

```bash
git add src/features/facemarket-landing/carousel/carouselMath.js \
        src/features/facemarket-landing/carousel/sceneLayout.js \
        tests/frontend/facemarket-carousel-math.test.mjs
git commit -m "feat(facemarket): 랜딩 캐러셀의 루프 산술과 배치 프로파일을 옮긴다"
```

---

### Task 2: three 카메라 좌표를 CSS 픽셀로 변환

`sceneLayout` 이 뱉는 값은 three world 단위다. 이걸 CSS 픽셀로 옮기는 변환을 순수 함수로 분리한다. 이 변환이 맞아야 원본과 같은 원근이 나오므로, "three 배율 == CSS 배율" 불변식을 테스트로 못박는다.

**Files:**
- Create: `src/features/facemarket-landing/carousel/cssProjection.js`
- Test: `tests/frontend/facemarket-css-projection.test.mjs`

**Interfaces:**
- Consumes: Task 1 의 `layoutForOffset` 반환 형태 `{ x, y, z, rotationY, rotationZ }`
- Produces:
  - `CAMERA_Z = 8.6` · `CAMERA_FOV_DEG = 24` · `VISIBLE_WORLD_HEIGHT` (상수)
  - `worldToPixelScale(stageHeightPx: number) => number`
  - `perspectivePx(stageHeightPx: number) => number`
  - `cardTransform(layout, k: number) => string`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/frontend/facemarket-css-projection.test.mjs`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  CAMERA_Z,
  VISIBLE_WORLD_HEIGHT,
  cardTransform,
  perspectivePx,
  worldToPixelScale,
} from '../../src/features/facemarket-landing/carousel/cssProjection.js';

test('카메라가 z=0 평면에서 보는 세로 높이를 fov 로 계산한다', () => {
  // 2 · 8.6 · tan(12°)
  assert.ok(Math.abs(VISIBLE_WORLD_HEIGHT - 3.65597) < 0.0001);
});

test('스테이지 높이가 world→px 계수를 결정한다', () => {
  const k = worldToPixelScale(VISIBLE_WORLD_HEIGHT * 100);
  assert.ok(Math.abs(k - 100) < 1e-9);
});

test('높이가 없으면 계수도 0이다 — 첫 렌더에서 NaN 이 나오지 않게', () => {
  assert.equal(worldToPixelScale(0), 0);
  assert.equal(worldToPixelScale(-10), 0);
  assert.equal(worldToPixelScale(Number.NaN), 0);
});

test('perspective 는 카메라 거리를 그대로 픽셀로 옮긴 값이다', () => {
  const height = 500;
  assert.ok(Math.abs(perspectivePx(height) / worldToPixelScale(height) - CAMERA_Z) < 1e-9);
});

test('CSS 배율이 three 배율과 같다 — 이 변환의 존재 이유', () => {
  const height = 640;
  const k = worldToPixelScale(height);
  const P = perspectivePx(height);

  for (const z of [0, 0.49, 1.19, 2.75, 3.2]) {
    const three = CAMERA_Z / (CAMERA_Z - z);      // 원근 카메라 배율
    const css = P / (P - z * k);                  // CSS perspective 배율
    assert.ok(Math.abs(three - css) < 1e-9, `z=${z} 에서 배율이 어긋난다`);
  }
});

test('가운데 카드는 변환이 원점이다', () => {
  const layout = { x: 0, y: 0, z: 0, rotationY: 0, rotationZ: 0 };
  assert.equal(
    cardTransform(layout, 100),
    'translate3d(0.00px, 0.00px, 0.00px) rotateY(0.0000rad) rotateZ(0.0000rad)',
  );
});

test('배치값에 계수를 곱해 픽셀 변환 문자열을 만든다', () => {
  const layout = { x: 2.25, y: 0, z: 1.19, rotationY: -0.26, rotationZ: -0.028 };
  assert.equal(
    cardTransform(layout, 100),
    // [구현과 다름] 실제 기대값은 rotateZ 가 **양수** 0.0280 이다 — 아래 블록 참고.
    'translate3d(225.00px, 0.00px, 119.00px) rotateY(-0.2600rad) rotateZ(-0.0280rad)',
  );
});
```

> **[구현과 다름] 이 태스크는 부호를 빠뜨렸다 — 손대칭(F = diag(1, -1, 1)).**
> three 는 +y 가 위, CSS 는 +y 가 아래라 두 좌표계가 y 축으로 어긋나 있고, CSS 행렬은
> `M_css = F·M·F` 로 옮겨야 한다. 풀면 **이동 y 와 `rotateZ` 는 부호가 뒤집히고,
> `rotateY` 와 이동 x·z 는 그대로**다. 계획대로 그냥 곱하면 아래 "CSS 배율 == three 배율"
> 불변식은 멀쩡히 통과하면서 화면의 부채꼴만 좌우 반전된다(offset +2 카드가 시계방향이어야
> 하는데 반시계로 기운다). `rotateY` 까지 같이 뒤집으면 카드가 반대쪽을 보며 오목 아크가
> 볼록이 된다. 실제 테스트 파일에는 손대칭 단언 넷과 `-0.0000` 표기 방어가 더 들어갔다.

- [ ] **Step 2: 실패를 확인한다**

Run: `pnpm test:frontend`
Expected: FAIL — `Cannot find module '.../carousel/cssProjection.js'`

- [ ] **Step 3: `cssProjection.js` 를 만든다**

```js
/* =============================================================
   facemarket-landing/carousel/cssProjection.js
   three world 좌표 → CSS 픽셀. 원본은 PerspectiveCamera(fov 24, z 8.6)로
   씬을 봤고, 우리는 CSS perspective 로 같은 그림을 만든다.

   두 원근이 같은 이유: 원근 카메라의 배율은 D/(D-z), CSS perspective 의
   배율은 P/(P-zpx) 다. P = D·k, zpx = z·k 로 두면 두 식이 같아진다.
   그래서 변환은 계수 k 하나로 닫힌다 — 테스트가 이 불변식을 지킨다.
   ============================================================= */

export const CAMERA_Z = 8.6;
export const CAMERA_FOV_DEG = 24;

/* 카메라가 z=0 평면에서 세로로 담는 world 높이. 스테이지 픽셀 높이를 이 값으로 나누면
   world→px 계수가 된다. */
export const VISIBLE_WORLD_HEIGHT =
  2 * CAMERA_Z * Math.tan(((CAMERA_FOV_DEG / 2) * Math.PI) / 180);

export function worldToPixelScale(stageHeightPx) {
  // 첫 렌더에는 ResizeObserver 가 아직 크기를 안 줘서 0/NaN 이 들어온다.
  // 여기서 막지 않으면 transform 문자열에 NaN 이 박혀 카드가 통째로 사라진다.
  if (!(stageHeightPx > 0)) return 0;
  return stageHeightPx / VISIBLE_WORLD_HEIGHT;
}

export function perspectivePx(stageHeightPx) {
  return CAMERA_Z * worldToPixelScale(stageHeightPx);
}

// [구현과 다름] 아래는 부호를 빠뜨린 계획 원안이다. 실제 구현은 y 와 rotationZ 를 뒤집고,
// toFixed 가 만드는 "-0.0000" 을 0 으로 정규화한다(stripNegativeZero).
//
//   `translate3d(${px(layout.x)}px, ${px(-layout.y)}px, ${px(layout.z)}px)` +
//   ` rotateY(${rad(layout.rotationY)}rad) rotateZ(${rad(-layout.rotationZ)}rad)`
//
export function cardTransform(layout, k) {
  const px = (value) => (value * k).toFixed(2);
  const rad = (value) => value.toFixed(4);
  return (
    `translate3d(${px(layout.x)}px, ${px(layout.y)}px, ${px(layout.z)}px)` +
    ` rotateY(${rad(layout.rotationY)}rad) rotateZ(${rad(layout.rotationZ)}rad)`
  );
}
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `pnpm test:frontend`
Expected: PASS

- [ ] **Step 5: 커밋한다**

```bash
git add src/features/facemarket-landing/carousel/cssProjection.js \
        tests/frontend/facemarket-css-projection.test.mjs
git commit -m "feat(facemarket): three 카메라 좌표를 CSS 원근으로 옮기는 변환"
```

---

### Task 3: 캐러셀 컴포넌트

입력(드래그·스와이프·키보드)과 렌더(rAF 트랜스폼)를 붙인다. 렌더 테스트 인프라가 없으므로 이 태스크의 검증은 브라우저 육안 확인이다.

**Files:**
- Create: `src/features/facemarket-landing/carousel/useCarouselController.js`
- Create: `src/features/facemarket-landing/carousel/CarouselStage.jsx`
- Create: `src/features/facemarket-landing/carousel/CarouselStage.module.css`
- Create: `src/features/facemarket-landing/data/landingModels.js`

**Interfaces:**
- Consumes: Task 1 의 `shortestWrappedOffset` · `layoutForOffset` · `metricsForAspect` · `targetForIndex` · `snapTarget` · `modulo` (~~`rebaseTarget`~~ 미채택), Task 2 의 `cardTransform` · `perspectivePx` · `worldToPixelScale`
- Produces:
  - `LANDING_MODELS: ReadonlyArray<{ id: string, src: string, alt: string }>` — 14개
  - `useCarouselController(itemCount: number, initialIndex?: number) => { target, activeIndex, isDragging, bind, goBy, goTo, handleKeyDown, consumeDragClick }`
  - `<CarouselStage items={LANDING_MODELS} controller={controller} />`

- [ ] **Step 1: 이미지 목록을 만든다**

`src/features/facemarket-landing/data/landingModels.js`:

```js
/* =============================================================
   facemarket-landing/data/landingModels.js
   랜딩 캐러셀이 도는 이미지 목록.

   전부 public/models 의 **가상 모델**이다. 실제 등록 모델의 얼굴은 여기
   들어올 수 없다 — 얼굴은 공개 URL 을 갖지 않는다(프라이버시 하드룰 1).
   화면에도 예시라는 고지가 함께 붙는다(GallerySection).

   카드에 붙는 건 번호뿐이다. 이름·연도·평점 같은 메타는 아직 정해지지 않았고,
   지어내면 실재하는 모델 정보로 읽힌다.
   ============================================================= */

const FILES = [
  'women/w1.webp',
  'women/w2.webp',
  'women/w3.webp',
  'women/w4.webp',
  'women/w5.webp',
  'women/w6.webp',
  'women/w7.webp',
  'women/w8.webp',
  'women/w9.webp',
  'women/w10.webp',
  'women/w11.webp',
  'men/m1.webp',
  'men/m2.webp',
  'men/m3.webp',
];

export const LANDING_MODELS = Object.freeze(
  FILES.map((file, index) => {
    const id = file.replace(/^.*\//, '').replace(/\.webp$/, '');
    const number = String(index + 1).padStart(2, '0');
    return Object.freeze({
      id,
      src: `/models/${file}`,
      alt: `가상 모델 예시 이미지 ${number}`,
    });
  }),
);
```

- [ ] **Step 2: 컨트롤러를 만든다**

`src/features/facemarket-landing/carousel/useCarouselController.js`:

```js
/* =============================================================
   facemarket-landing/carousel/useCarouselController.js
   포인터 드래그·스와이프·키보드를 연속 목표값(target)으로 바꾼다.
   spotlight 프로토타입(useCarouselController.ts) 이식 — 상수 그대로.

   원본과 다른 점 하나: 드래그로 끝난 포인터가 카드의 click 까지 발화시켜
   엉뚱한 카드로 점프하던 걸 consumeDragClick 으로 막는다.
   ============================================================= */
import { useCallback, useRef, useState } from 'react';
// [구현과 다름] 실제 import 는 `{ modulo, snapTarget, targetForIndex }` — rebaseTarget 미채택.
import { modulo, rebaseTarget, snapTarget, targetForIndex } from './carouselMath.js';

const DRAG_PIXELS_PER_ITEM = 170;
const HORIZONTAL_INTENT_PIXELS = 8;
// [구현과 다름] 상수가 셋 늘었다 — MIN_DELTA_SECONDS(1/240), STALE_VELOCITY_MS(100),
// DRAG_CLICK_WINDOW_MS(500). 사유는 이 Step 끝의 블록 참고.

export function useCarouselController(itemCount, initialIndex = 0) {
  const pointer = useRef({
    id: -1,
    startX: 0,
    startTarget: initialIndex,
    lastX: 0,
    lastTime: 0,
    velocity: 0,
    hasHorizontalIntent: false,
  });
  // 방금 끝난 포인터가 드래그였는지. click 핸들러가 읽고 지운다.
  const dragged = useRef(false);
  const [target, setTarget] = useState(initialIndex);
  const [isDragging, setDragging] = useState(false);
  const activeIndex = itemCount > 0 ? modulo(Math.round(target), itemCount) : 0;

  const goBy = useCallback((delta) => {
    setTarget((current) => current + delta);
  }, []);

  const goTo = useCallback(
    (index) => {
      setTarget((current) => targetForIndex(current, index, itemCount));
    },
    [itemCount],
  );

  const handleKeyDown = useCallback(
    (event) => {
      if (event.key === 'ArrowLeft') { event.preventDefault(); goBy(-1); }
      if (event.key === 'ArrowRight') { event.preventDefault(); goBy(1); }
    },
    [goBy],
  );

  const resetPointer = useCallback(() => {
    pointer.current.id = -1;
    pointer.current.velocity = 0;
    pointer.current.hasHorizontalIntent = false;
  }, []);

  const onPointerDown = useCallback(
    (event) => {
      // 새 포인터가 시작하면 지난 드래그 흔적을 지운다 — 스테이지 밖에서 손을 뗀 뒤
      // 다음에 진짜로 누른 클릭이 삼켜지지 않게.
      dragged.current = false;
      pointer.current = {
        id: event.pointerId,
        startX: event.clientX,
        startTarget: target,
        lastX: event.clientX,
        lastTime: event.timeStamp,
        velocity: 0,
        hasHorizontalIntent: false,
      };
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    [target],
  );

  const onPointerMove = useCallback((event) => {
    if (event.pointerId !== pointer.current.id) return;

    const deltaX = event.clientX - pointer.current.startX;
    // 세로 스크롤 의도를 뺏지 않으려고 8px 넘게 가로로 움직여야 드래그로 친다.
    if (!pointer.current.hasHorizontalIntent) {
      if (Math.abs(deltaX) < HORIZONTAL_INTENT_PIXELS) return;
      pointer.current.hasHorizontalIntent = true;
      setDragging(true);
    }

    const deltaSeconds = Math.max((event.timeStamp - pointer.current.lastTime) / 1000, 1 / 60);
    pointer.current.velocity =
      -(event.clientX - pointer.current.lastX) / DRAG_PIXELS_PER_ITEM / deltaSeconds;
    pointer.current.lastX = event.clientX;
    pointer.current.lastTime = event.timeStamp;
    setTarget(pointer.current.startTarget - deltaX / DRAG_PIXELS_PER_ITEM);
  }, []);

  const releasePointer = useCallback(
    (event) => {
      if (event.pointerId !== pointer.current.id) return;

      const releaseVelocity = pointer.current.velocity;
      dragged.current = pointer.current.hasHorizontalIntent;
      if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
      // [구현과 다름] 실제 구현은 `setTarget((current) => snapTarget(current, releaseVelocity))`
      // 이고 deps 도 `[resetPointer]` 뿐이다 — rebaseTarget 미채택.
      setTarget((current) => rebaseTarget(snapTarget(current, releaseVelocity), itemCount));
      setDragging(false);
      resetPointer();
    },
    [itemCount, resetPointer],
  );

  // 드래그 끝의 click 인지 확인하고 표식을 지운다. true 면 클릭을 무시해야 한다.
  // [구현과 다름] 실제 구현은 `consumeDragClick(event)` 로 click 이벤트를 받고,
  // 표식도 boolean 이 아니라 **시각**(dragEndedAt)이다. 아래 블록 참고.
  const consumeDragClick = useCallback(() => {
    const wasDragged = dragged.current;
    dragged.current = false;
    return wasDragged;
  }, []);

  return {
    target,
    activeIndex,
    isDragging,
    bind: {
      onPointerDown,
      onPointerMove,
      onPointerUp: releasePointer,
      onPointerCancel: releasePointer,
    },
    goBy,
    goTo,
    handleKeyDown,
    consumeDragClick,
  };
}
```

> **[구현과 다름] 이 컨트롤러는 감사 후 일곱 군데가 바뀌었다. 되돌리지 마라.**
>
> 1. **포인터 캡처를 `onPointerDown` 이 아니라 `onPointerMove`(가로 의도 확정 시점)에
>    건다.** `pointerdown` 에서 걸면 `pointerup` 이 캡처 대상(`.stage`)으로 리타깃되고,
>    `click` 은 down/up 타깃의 최근접 공통 조상에 발화하므로 공통 조상이 `.stage` 가 된다
>    → 자식인 카드 `button` 의 `onClick(goTo)` 이 영영 안 불린다. **데스크톱 마우스
>    클릭만 죽고 터치 탭은 살아서 QA 에서 놓치기 쉽다.**
> 2. **드래그 표식이 boolean 이 아니라 시각(`dragEndedAt`)이고 500ms 뒤 스스로 만료된다.**
>    `pointercancel` 이나 스테이지 여백에서 손을 뗀 경우엔 뒤따르는 click 이 없어서, 영구
>    표식은 아무도 소비하지 않고 남아 **다음 카드 활성화를 통째로 삼킨다.**
> 3. **`consumeDragClick(event)` 는 click 이벤트를 받는다** — 키보드 활성화(Enter/Space)를
>    `event.detail === 0` 으로 걸러 드래그 표식과 무관하게 통과시킨다. 호출부(카드 onClick)가
>    이벤트를 안 넘기면 그 예외가 통째로 죽는다.
> 4. **속도 dt 하한 1/60 → 1/240 초.** 원본 값을 그대로 두면 120Hz 기기(`pointermove`
>    간격 ≈8.3ms)의 플릭 속도가 정확히 절반으로 측정돼 같은 손동작이 60Hz 의 절반만
>    넘어간다. 그리고 마지막 move 뒤 100ms 넘게 있다가 뗐으면 속도를 0 으로 본다.
> 5. **`target` 은 반드시 React state 로 남는다.** 스테이지의 rAF 루프는 도착하면 스스로
>    멈추고 `useEffect(…, [controller.target, …])` 로만 깨어나므로, 드래그 중 재렌더를
>    줄이겠다고 `target` 을 ref 로 돌리면 그 wake 이펙트가 영영 발화하지 않아 **캐러셀이
>    통째로 안 움직인다.** 옮기려면 tick state 나 `subscribe(listener)` 를 함께 내보내라.
> 6. **무장 해제 탈출구가 셋이다(1의 대가).** 캡처를 8px 뒤로 미룬 구간에는 `pointerup` 을
>    스테이지로 끌어올 장치가 없어서, 스테이지 밖에서 손을 떼면 `pointer.current.id` 가
>    사장되고 **그 뒤로는 버튼을 안 누른 맨 호버가 드래그가 된다**(마우스는 hover 만으로도
>    `pointermove` 가 온다). 그래서 무장하는 곳은 `onPointerDown` 하나지만 푸는 길은 셋이고,
>    셋 다 `resetPointer` 로 모인다 — ① `onPointerMove` 첫머리의
>    `pointerType === 'mouse' && buttons === 0` 가드, ② `window` 의
>    `pointerup`/`pointercancel`(상시 등록), ③ `.stage` 의 `onLostPointerCapture`.
>    **하나라도 지우기 전에 "그 경로로 무장이 남지 않는가"를 먼저 답해라.** 캡처를
>    `pointerdown` 으로 되돌리는 건 해법이 아니다 — 1 이 그대로 돌아온다.
> 7. **중복 릴리스는 `releasePointer` 첫 줄의 `pointerId` 대조가 막는다.** 같은 릴리스가
>    셋 중 둘 이상으로 도착해도, 먼저 온 쪽이 id 를 -1 로 돌려놓아 뒤따르는 쪽은 거기서
>    끝난다. **그 한 줄을 지우면 한 번의 릴리스가 `snapTarget` 을 두 번 먹인다.**
>    (같은 이유로 ① 가드 안에서는 `releasePointerCapture` 를 부르지 않는다 — 부르면
>    `lostpointercapture` 가 ③ 을 태워 두 번 먹는다.)
>
> 테스트에서 드래그를 흉내 낼 땐 `pointermove` 에 `buttons: 1` 을 반드시 넣어라 —
> 합성 이벤트의 기본값이 0 이라 ① 가드에 걸린다.

- [ ] **Step 3: 스테이지 컴포넌트를 만든다**

`src/features/facemarket-landing/carousel/CarouselStage.jsx`:

```jsx
/* =============================================================
   facemarket-landing/carousel/CarouselStage.jsx
   spotlight 캐러셀의 CSS 3D 재구현. 원본의 Canvas·ArtworkScene·
   ArtworkCardMesh·CssGalleryFallback 네 파일이 여기 하나로 합쳐졌다.

   WebGL 이 필요 없는 이유: 원본 셰이더가 하는 일은 둥근 모서리 마스크와
   opacity 곱하기뿐이고, 그림자는 별도 plane, 라벨은 캔버스에 그린 글자였다.
   각각 border-radius·opacity·box-shadow·진짜 텍스트로 대체된다.

   두 가지 함정:
   1) 매 프레임 setState 하면 카드 14장이 60fps 로 재렌더된다. rAF 안에서
      ref 의 style 을 직접 쓰고, React 는 활성 인덱스가 바뀔 때만 재렌더한다.
   2) perspective 만으로는 브라우저가 카드를 깊이순으로 정렬하지 않는다
      (transform-style: preserve-3d 가 아니면 각 자식이 개별 평탄화된다).
      그래서 z 를 z-index 로 직접 번역한다 — 안 하면 카메라 앞으로 나온 카드가
      뒤 카드에 가린다.
   ============================================================= */
import { useEffect, useRef, useState } from 'react';
import { shortestWrappedOffset } from './carouselMath.js';
import { layoutForOffset, metricsForAspect } from './sceneLayout.js';
import { cardTransform, perspectivePx, worldToPixelScale } from './cssProjection.js';
import s from './CarouselStage.module.css';

const DAMP_IDLE = 9;
const DAMP_DRAG = 18;
const DAMP_REDUCED = 24;

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);

  return reduced;
}

export function CarouselStage({ items, controller }) {
  const stageRef = useRef(null);
  const cardRefs = useRef([]);
  const positionRef = useRef(controller.target);
  const targetRef = useRef(controller.target);
  const draggingRef = useRef(false);
  const reducedRef = useRef(false);
  const [stage, setStage] = useState({ width: 0, height: 0 });
  const reducedMotion = usePrefersReducedMotion();

  // rAF 루프가 읽을 최신값. 렌더마다 갱신하되 루프를 다시 만들지는 않는다.
  targetRef.current = controller.target;
  draggingRef.current = controller.isDragging;
  reducedRef.current = reducedMotion;

  // 원본이 three viewport 로 읽던 값 — 여기선 스테이지 DOM 의 실제 크기.
  useEffect(() => {
    const node = stageRef.current;
    if (!node) return undefined;

    const observer = new ResizeObserver(([entry]) => {
      const box = entry.contentRect;
      setStage({ width: box.width, height: box.height });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!(stage.height > 0)) return undefined;

    const k = worldToPixelScale(stage.height);
    const metrics = metricsForAspect(stage.width / stage.height);
    const count = items.length;
    let frame = 0;
    let previous = 0;

    const paint = () => {
      for (let index = 0; index < count; index += 1) {
        const card = cardRefs.current[index];
        if (!card) continue;

        const offset = shortestWrappedOffset(index, positionRef.current, count);
        const layout = layoutForOffset(offset, metrics);
        card.style.transform = cardTransform(layout, k);
        card.style.opacity = String(layout.opacity);
        card.style.visibility = layout.opacity > 0.01 ? 'visible' : 'hidden';
        card.style.zIndex = String(1000 + Math.round(layout.z * 100));
      }
    };

    const tick = (now) => {
      // 탭이 백그라운드였다가 돌아오면 delta 가 몇 초씩 튄다. 한 프레임에 다 감쇠하지
      // 않게 상한을 둔다(50ms).
      const delta = previous ? Math.min((now - previous) / 1000, 0.05) : 0;
      previous = now;

      const lambda = reducedRef.current ? DAMP_REDUCED : draggingRef.current ? DAMP_DRAG : DAMP_IDLE;
      positionRef.current += (targetRef.current - positionRef.current) * (1 - Math.exp(-lambda * delta));
      paint();
      frame = requestAnimationFrame(tick);
    };

    paint();
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [items.length, stage.height, stage.width]);

  const ready = stage.height > 0;
  const k = worldToPixelScale(stage.height);
  const metrics = ready ? metricsForAspect(stage.width / stage.height) : null;

  return (
    <div
      aria-label="가상 모델 예시 이미지"
      className={s.stage}
      onKeyDown={controller.handleKeyDown}
      ref={stageRef}
      role="region"
      style={{
        cursor: controller.isDragging ? 'grabbing' : 'grab',
        perspective: ready ? `${perspectivePx(stage.height)}px` : undefined,
      }}
      tabIndex={0}
      {...controller.bind}
    >
      {items.map((item, index) => (
        <button
          aria-current={index === controller.activeIndex ? 'true' : undefined}
          className={s.card}
          key={item.id}
          onClick={() => {
            if (controller.consumeDragClick()) return;
            controller.goTo(index);
          }}
          ref={(node) => { cardRefs.current[index] = node; }}
          style={metrics ? { width: `${metrics.cardWidth * k}px`, height: `${metrics.cardHeight * k}px` } : undefined}
          type="button"
        >
          <img alt={item.alt} className={s.photo} draggable="false" src={item.src} />
          <span className={s.badge}>{String(index + 1).padStart(2, '0')}</span>
        </button>
      ))}
    </div>
  );
}
```

> **[구현과 다름] 스테이지도 감사 후 여섯 군데가 바뀌었다.**
>
> 1. **rAF 루프가 목표에 닿으면 스스로 멈춘다.** 위 코드처럼 무조건 다음 프레임을 예약하면
>    아무도 안 만지는 랜딩에서도 초당 14장×스타일 4개를 영원히 덮어쓴다(캐러셀이 화면 밖으로
>    스크롤돼도). 다시 켜는 건 `useEffect(…, [controller.target, controller.isDragging,
>    reducedMotion])` 뿐이고, 그 이펙트는 루프 이펙트보다 **뒤에** 선언돼야 첫 마운트에서
>    `wakeRef` 가 채워져 있다.
> 2. **`prefers-reduced-motion` 은 lambda 를 올리는 게 아니라 그 프레임에 확정한다.**
>    `DAMP_REDUCED = 24` 로도 원근 회전이 160ms 재생된다 — 그건 '동작 줄이기'를 켠 사람이
>    정확히 막으려는 종류의 모션이다.
> 3. **포커스를 쥔 카드는 `visibility: hidden` 으로 만들지 않는다.** 숨기는 순간 브라우저가
>    포커스를 body 로 회수해 방향키 조작이 통째로 죽는다. 대신 `pointer-events: none` 만
>    준다. 카드는 로빙 탭인덱스(활성 카드만 `tabIndex 0`)이고 활성 인덱스가 바뀌면 포커스도
>    따라간다(`focus({ preventScroll: true })` — 카드는 화면 밖까지 밀려나 있어서 기본
>    focus 가 페이지를 옆으로 끌고 간다).
> 4. **카드 onClick 은 `event` 를 넘긴다** — `controller.consumeDragClick(event)`.
>    안 넘기면 키보드 활성화 예외(`detail === 0`)가 도달 불가 데드코드가 된다.
> 5. **ref 콜백을 인덱스별로 캐시한다.** 인라인 화살표는 렌더마다 새 함수라 React 가 카드
>    14개의 ref 를 (null → node)로 다시 붙인다 — 드래그 중에는 그게 프레임마다다.
> 6. **`ready` 전에는 `perspective` 를 걸지 않고**(k=0 이면 `perspective: 0px` 이라 원근이
>    꺼지고 모든 카드가 원점으로 붕괴한다), 배지에는 번호와 함께 **'예시' 고지**가 들어가고
>    `aria-hidden` 이다(`img` 의 alt 가 이미 "가상 모델 예시 이미지 01" 이라 안 가리면
>    스크린리더가 "…01 01 예시"로 더듬는다).

- [ ] **Step 4: 스타일을 만든다**

`src/features/facemarket-landing/carousel/CarouselStage.module.css`:

```css
/* 카드가 모두 같은 그리드 칸에 겹쳐 놓이고, transform 이 각자를 밀어낸다.
   perspective 는 인라인으로 들어온다(스테이지 높이에서 계산). */
.stage {
  position: relative;
  display: grid;
  place-items: center;
  min-height: var(--fm-stage-height);
  overflow: hidden;
  touch-action: pan-y;   /* 세로 스크롤을 뺏지 않는다 — 랜딩은 한 장 스크롤 페이지다 */
  user-select: none;
  outline: none;
}

.stage:focus-visible {
  outline: 2px solid var(--fm-accent);
  outline-offset: -4px;
}

.card {
  grid-area: 1 / 1;
  position: relative;
  padding: 0;
  border: 0;
  border-radius: 5.5%;          /* 원본 셰이더의 uRadius 0.055 */
  background: transparent;
  overflow: hidden;
  cursor: inherit;
  box-shadow: 0 1.6rem 3.4rem rgba(24, 34, 46, 0.34);
  will-change: transform, opacity;
}

.card:focus-visible {
  outline: 2px solid var(--fm-accent);
  outline-offset: 3px;
}

.photo {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  pointer-events: none;   /* 이미지 기본 드래그가 포인터 캡처를 가로채지 않게 */
}

.badge {
  position: absolute;
  top: 0.85rem;
  left: 1rem;
  color: rgba(255, 255, 255, 0.92);
  font: 500 0.8rem/1 var(--font-body);
  letter-spacing: 0.06em;
  text-shadow: 0 2px 10px rgba(0, 0, 0, 0.45);
}
```

- [ ] **Step 5: 테스트가 여전히 통과하는지 확인한다**

Run: `pnpm test:frontend`
Expected: PASS — 이 태스크는 순수 로직을 안 건드렸으니 Task 1·2 테스트가 그대로 통과해야 한다

- [ ] **Step 6: 빌드가 되는지 확인한다**

Run: `pnpm build`
Expected: 성공. 아직 어디서도 import 되지 않는 파일이라 번들에는 안 들어가지만 문법 오류는 잡힌다.

- [ ] **Step 7: 커밋한다**

```bash
git add src/features/facemarket-landing/carousel/ src/features/facemarket-landing/data/
git commit -m "feat(facemarket): 랜딩 캐러셀을 CSS 3D 로 구현한다"
```

---

### Task 4: 랜딩 라우트와 로그인 복귀 경로 인수

`/` 에 랜딩을 놓는다. 여기엔 조용한 함정이 하나 있다: `wl_postLogin`(로그인 후 복귀 경로)의 **유일한 소비자가 `RootRedirect`** 다(`src/App.jsx:510, 525`). OAuth 는 origin 으로 돌아오므로, 랜딩이 `/` 를 가져가면서 그 소비를 이어받지 않으면 "모델 등록 시작" → 로그인 → **다시 랜딩** 이 된다. 사용자는 자기가 누른 곳으로 못 간다.

**Files:**
- Create: `src/features/facemarket-landing/facemarketRootTarget.js`
- Create: `src/features/facemarket-landing/FacemarketRoot.jsx`
- Create: `src/features/facemarket-landing/FacemarketLanding.jsx` (이 태스크에서는 뼈대만)
- Modify: `src/App.jsx`
- Test: `tests/frontend/facemarket-landing-routing.test.mjs`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `facemarketRootTarget(returnIntent: string | null) => string | null`
  - `<FacemarketRoot />` — `/` 의 element
  - `<FacemarketLanding />` — 섹션 조립. Task 5~8 이 채운다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/frontend/facemarket-landing-routing.test.mjs`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';

import { facemarketRootTarget } from '../../src/features/facemarket-landing/facemarketRootTarget.js';

test('복귀 경로가 없으면 랜딩을 그린다', () => {
  assert.equal(facemarketRootTarget(null), null);
  assert.equal(facemarketRootTarget(undefined), null);
  assert.equal(facemarketRootTarget(''), null);
  assert.equal(facemarketRootTarget('   '), null);
});

test('앱 안 경로면 그리로 보낸다', () => {
  assert.equal(facemarketRootTarget('/model/register'), '/model/register');
  assert.equal(facemarketRootTarget('/model/license'), '/model/license');
  assert.equal(facemarketRootTarget('  /model  '), '/model');
});

test('루트 자기 자신이면 랜딩을 그린다 — 리다이렉트 루프 금지', () => {
  assert.equal(facemarketRootTarget('/'), null);
});

test('앱 밖으로 튀는 값은 무시한다', () => {
  // sessionStorage 는 같은 오리진의 다른 스크립트도 쓸 수 있다. 여기서 막지 않으면
  // 로그인 복귀가 외부 사이트로 향하는 통로가 된다.
  assert.equal(facemarketRootTarget('https://example.com/phish'), null);
  assert.equal(facemarketRootTarget('//example.com/phish'), null);
  assert.equal(facemarketRootTarget('model/register'), null);
  assert.equal(facemarketRootTarget('javascript:alert(1)'), null);
  assert.equal(facemarketRootTarget(42), null);
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pnpm test:frontend`
Expected: FAIL — `Cannot find module '.../facemarketRootTarget.js'`

- [ ] **Step 3: 판정 함수를 만든다**

`src/features/facemarket-landing/facemarketRootTarget.js`:

```js
/* =============================================================
   facemarket-landing/facemarketRootTarget.js
   로그인 복귀 경로(sessionStorage 'wl_postLogin')를 어디로 해석할지.

   원래 이 소비는 RootRedirect(App.jsx)가 했다. facemarket 루트를 랜딩이
   가져가면서 그 계약을 여기가 이어받는다 — 안 그러면 "모델 등록 시작"에서
   로그인한 사용자가 등록 위저드가 아니라 랜딩으로 돌아온다.
   ============================================================= */

export function facemarketRootTarget(returnIntent) {
  if (typeof returnIntent !== 'string') return null;

  const path = returnIntent.trim();
  // 앱 안 절대경로만 받는다. sessionStorage 는 같은 오리진의 다른 스크립트도 쓸 수 있어서,
  // 외부 URL 이 들어오면 로그인 복귀가 그대로 열린 리다이렉트가 된다.
  if (!path.startsWith('/')) return null;
  if (path.startsWith('//')) return null;   // protocol-relative
  if (path === '/') return null;            // 자기 자신 — 랜딩을 그린다

  return path;
}
```

> **[구현과 다름] 이 필터는 두 겹으로 늘었다.**
>
> - **오픈 리다이렉트 방어 보강.** 역슬래시(URL 파서는 http(s) 에서 `/\evil.com` 을
>   `//evil.com` 으로 읽는다), 인코딩된 슬래시(`/%2f%2fevil.com`), 제어문자(파서가 조용히
>   지워서 검사한 문자열과 실제 이동 경로가 달라진다), 과대 길이를 추가로 막는다.
> - **도메인 화이트리스트 `['/model', '/verify']` 추가.** 플래그를 *심는* 쪽에는 도메인
>   가드가 없다 — `shell.jsx` 의 TopNav 로그인은 `/create/input` 을, `ProductInput` 은
>   `/create/storyboard` 를 심고, 그 TopNav 는 facemarket 에서도 렌더된다. 통과시키면
>   등록 전용 도메인에 셀러 스튜디오가 열린다. 랜딩 도입 전 `RootRedirect` 는 facemarket
>   복귀 의도를 통째로 무시해서 이 문이 아예 없었다 — 그 봉쇄를 이어받는 것이다.
>   접두사만 겹치는 `/models-evil` 은 막고, `'/model/register?step=2'` 같은 쿼리·해시는
>   통과시킨다(화이트리스트는 pathname 에만 건다).

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `pnpm test:frontend`
Expected: PASS

- [ ] **Step 5: 랜딩 뼈대와 루트 컴포넌트를 만든다**

`src/features/facemarket-landing/FacemarketLanding.jsx` — Task 5~8 이 섹션을 채운다:

```jsx
/* =============================================================
   facemarket-landing/FacemarketLanding.jsx
   facemarket.wearless.kr 랜딩. 앱 크롬(ChromeLayout) 밖 독립 surface 다 —
   TopNav 의 크레딧 배지·플로우 스테퍼는 셀러 스튜디오 물건이고, 이 페이지의
   상단바는 섹션 앵커라 성격이 다르다.
   ============================================================= */
import s from './FacemarketLanding.module.css';

export function FacemarketLanding() {
  return (
    <main className={s.shell} id="top">
      <p className={s.placeholder}>랜딩 섹션은 이후 태스크에서 채운다.</p>
    </main>
  );
}
```

`src/features/facemarket-landing/FacemarketLanding.module.css`:

```css
/* 랜딩 전용 토큰. 앱 전역 토큰(tokens.css)은 건드리지 않고 여기서만 덮는다 —
   spotlight 원본의 차가운 오프화이트와 파랑이 앱의 순백·링크블루와 다르다. */
.shell {
  --fm-page-bg: #edf3f8;
  --fm-ink: #10151a;
  --fm-muted: #727c86;
  --fm-line: rgba(16, 21, 26, 0.12);
  --fm-accent: #2d63ff;
  --fm-stage-height: clamp(19rem, 25vw, 32rem);
  --fm-pad: clamp(1.25rem, 3.1vw, 4.25rem);

  position: relative;
  min-height: 100vh;
  padding: 0 var(--fm-pad) 4rem;
  color: var(--fm-ink);
  background:
    radial-gradient(circle at 50% 42%, rgba(255, 255, 255, 0.72), transparent 32rem),
    linear-gradient(180deg, #f8fbfd 0%, var(--fm-page-bg) 100%);
  font-family: var(--font-body);
}

.placeholder {
  padding: 6rem 0;
  color: var(--fm-muted);
  text-align: center;
}
```

`src/features/facemarket-landing/FacemarketRoot.jsx`:

> ### ⚠️ 아래 코드를 쓰지 마라 — 이 도메인의 로그인을 통째로 깨뜨린 critical 버그다
>
> **`FacemarketRoot` 가 첫 렌더에 `<Navigate>` 를 반환하면 OAuth `?code=` 가 죽는다.**
> `supabase` 는 `detectSessionInUrl: false` 라 code 교환처가 `AuthProvider` 한 곳뿐인데,
> `AuthProvider` 는 `BrowserRouter` 의 **조상**이다(`main.jsx`). React 의 passive effect 는
> **자식 → 부모** 순으로 돌기 때문에, `Navigate` 의 `history.replaceState` 가 code 교환보다
> 먼저 돌아 쿼리스트링을 통째로 지운다 — 읽기도 전에. 그러면 facemarket 도메인에서는
> 아무도 로그인할 수 없다. `RootRedirect` 가 `if (loading && …) return;` 으로 지키던 계약이
> 정확히 이것인데, 이 계획은 그걸 옮겨 적지 않았다.
>
> **실제 구현의 계약:**
> - 이동 조건은 "플래그가 있다"가 아니라 **`!loading && session`** 이다. 세션 없이 이동하면
>   `/model/register` 의 `RequireAuth` 가 로그인 프롬프트를 띄우고 그게 `openLogin` 으로
>   플래그를 **다시 심어서**, 그 탭에서는 랜딩을 영영 못 본다. 세션이 없으면(=로그인을
>   취소했다) 플래그를 버리고 랜딩을 그린다.
> - 플래그 **읽기는 렌더**에서(`useState` 초기화), **지우기는 커밋 뒤 effect** 에서,
>   그것도 `!loading` 인 뒤에만. dev StrictMode 는 마운트 렌더를 두 번 돌리고 두 번째를
>   커밋하므로 렌더에서 지우면 두 번째 렌더가 빈 값을 읽어 복귀 목표가 사라진다.
> - **플래그가 없는 평범한 방문은 인증을 기다리지 않는다.** 스펙의 성공 기준이 "루트는
>   로그인 여부와 무관하게 랜딩"이라, 기다리는 건 플래그를 들고 온 진입뿐이다.

```jsx
/* [구현과 다름 — 위 경고 참고] 아래는 계획 원안이고, 머지된 코드가 아니다.
   =============================================================
   facemarket-landing/FacemarketRoot.jsx
   facemarket 도메인의 '/' 진입점. 로그인 복귀 목표가 있으면 그리로 보내고,
   없으면 랜딩을 그린다.

   복귀 플래그는 마운트 때 한 번 읽고 바로 지운다 — 취소된 이전 로그인의 묵은
   플래그가 다음 진입을 엉뚱한 곳으로 보내지 않게. RootRedirect 와 같은 계약이다.
   ============================================================= */
import { useState } from 'react';
import { Navigate } from 'react-router-dom';
import { FacemarketLanding } from './FacemarketLanding.jsx';
import { facemarketRootTarget } from './facemarketRootTarget.js';

export function FacemarketRoot() {
  const [target] = useState(() => {
    let intent = null;
    try {
      intent = sessionStorage.getItem('wl_postLogin');
      sessionStorage.removeItem('wl_postLogin');   // ← 렌더에서 지우면 StrictMode 에서 유실
    } catch { /* 저장소가 막힌 브라우저에서도 랜딩은 떠야 한다. */ }
    return facemarketRootTarget(intent);
  });

  if (target) return <Navigate replace to={target} />;   // ← 첫 렌더 이동 = OAuth code 파괴
  return <FacemarketLanding />;
}
```

- [ ] **Step 6: `App.jsx` 에 라우트를 붙인다**

import 를 추가한다 (`src/App.jsx` 상단, `PublicVerify` import 근처):

```js
import { FacemarketRoot } from '@/features/facemarket-landing/FacemarketRoot.jsx';
```

`<Routes>` 바로 안쪽을 아래로 바꾼다. `<Routes>` 는 유효하지 않은 자식(`false`)을 건너뛰므로 조건부 `<Route>` 가 안전하다.

```jsx
      <Routes>
        {/* facemarket 루트는 앱 크롬 밖 랜딩이다 — 등록 전 방문자에게 TopNav(크레딧·스테퍼)는
            셀러 스튜디오 잡음이고, 랜딩 상단바는 섹션 앵커라 성격이 겹치지 않는다.
            로그인 복귀(wl_postLogin) 소비는 FacemarketRoot 가 이어받는다. */}
        {IS_FACEMARKET && <Route index element={<FacemarketRoot />} />}
        <Route element={<ChromeLayout />}>
          {!IS_FACEMARKET && <Route index element={<RootRedirect />} />}
```

`RootRedirect` 에서 죽은 facemarket 분기를 걷어낸다. 510행 근처의 주석과 `target` 을:

```js
  // facemarket 루트는 FacemarketRoot 가 가져갔다 — 여기 오는 건 ai 도메인뿐이다.
  const target = returnIntent || '/create/input';
```

로 바꾸고, `useEffect` 첫머리의 조기 반환 두 줄을 삭제한다:

```js
    // (삭제) facemarket(등록 전용 도메인)은 루트에서 곧장 모델 등록으로 — ...
    // (삭제) if (IS_FACEMARKET) { setDest('/model/register'); setPhase('done'); return; }
```

마지막으로 catch-all 목적지를 도메인별로 나눈다. facemarket 에서 모르는 경로가 셀러 앱 입력 화면으로 떨어지면 안 된다:

```jsx
        <Route path="*" element={<Navigate to={IS_FACEMARKET ? '/' : '/create/input'} replace />} />
```

- [ ] **Step 7: 브라우저에서 확인한다**

Run: `pnpm dev`

확인 항목:
1. `http://localhost:5173/?facemarket=1` → 랜딩 뼈대(플레이스홀더 문구)가 뜬다. TopNav 가 **안** 보인다.
2. `http://localhost:5173/` → 기존대로 `/create/input` 으로 간다(셀러 앱 회귀 없음).
3. 브라우저 콘솔에서 `sessionStorage.setItem('wl_postLogin', '/model/register')` 실행 후 `/?facemarket=1` 새로고침 → **로그인 상태면** `/model/register` 로 이동한다. (구현 갱신: 비로그인이면 플래그를 버리고 랜딩을 그린다 — 위 경고 블록 참고.)
4. 같은 방법으로 `'/create/input'` 을 심으면 화이트리스트에 걸려 **랜딩**이 뜬다.
5. `http://localhost:5173/없는경로?facemarket=1` → 랜딩으로 돌아온다.
6. **OAuth 왕복이 살아 있는지 반드시 확인한다** — 랜딩 CTA → 로그인 → `/` 복귀에서 세션이 붙어야 한다. 이 라우트에서 가장 깨지기 쉬운 자리다.

- [ ] **Step 8: 커밋한다**

```bash
git add src/App.jsx src/features/facemarket-landing/ tests/frontend/facemarket-landing-routing.test.mjs
git commit -m "feat(facemarket): 도메인 루트를 랜딩으로 바꾸고 로그인 복귀 경로를 넘겨받는다"
```

---

### Task 5: 상단바·히어로·캐러셀 섹션

랜딩의 위 절반. 상단바 세 항목이 섹션 앵커로 동작하고, 캐러셀이 실제로 돈다.

> **[구현과 다름] 상단바·섹션 배치가 넷 바뀌었다.**
> - **앵커 스크롤도 `prefers-reduced-motion` 을 존중한다.** '모델 정보'는 히어로+캐러셀+
>   라이선싱 아래라 스크롤 거리가 수천 px 인데, 그 전체가 흘러가는 건 '동작 줄이기'를 켠
>   사람이 정확히 막으려는 모션이다. 캐러셀만 조용하고 내비게이션만 움직이면 같은 기능
>   안에서 처리가 어긋난다. 설정은 페이지를 연 뒤에도 바뀌므로 클릭할 때마다 읽는다.
> - **모바일 메뉴를 닫는 길이 셋이다**(바깥 클릭·Escape·데스크톱 폭 진입). 토글 버튼만
>   두면 데스크톱 폭에서 햄버거가 `display:none` 이라 누를 대상 자체가 없어져 갇힌다.
> - **`header`·`footer` 는 `<main>` 밖**이다. HTML-AAM 상 `main`/`section`/`article` 안에
>   중첩된 `header`/`footer` 는 banner·contentinfo 랜드마크를 잃고 generic 이 된다.
> - **점(dot)은 `role="tab"` 이 아니다.** 자기가 여는 tabpanel 이 없으니 스크린리더가
>   "탭 1/14, 선택 안 됨"으로 없는 구조를 안내한다. 카드와 같은 `aria-current` 를 쓴다.

**Files:**
- Create: `src/features/facemarket-landing/LandingHeader.jsx`
- Create: `src/features/facemarket-landing/sections/HeroSection.jsx`
- Create: `src/features/facemarket-landing/sections/GallerySection.jsx`
- Modify: `src/features/facemarket-landing/FacemarketLanding.jsx`
- Modify: `src/features/facemarket-landing/FacemarketLanding.module.css`

**Interfaces:**
- Consumes: Task 3 의 `LANDING_MODELS` · `useCarouselController` · `CarouselStage`
- Produces:
  - `<LandingHeader onPrimary={() => void} primaryLabel={string} />`
  - `<HeroSection onPrimary={() => void} primaryLabel={string} />`
  - `<GallerySection />` — `id="gallery"`

- [ ] **Step 1: 상단바를 만든다**

`src/features/facemarket-landing/LandingHeader.jsx`:

```jsx
/* =============================================================
   facemarket-landing/LandingHeader.jsx
   랜딩 상단바. 세 항목은 라우트가 아니라 같은 페이지 섹션 앵커다 —
   /model/* 은 전부 인증이 필요해서, 라우트로 걸면 첫 클릭이 곧바로
   로그인 모달이 된다(설명을 읽기 전에 가입을 요구하는 순서).
   ============================================================= */
import { useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import s from './FacemarketLanding.module.css';

const NAV = [
  { id: 'licensing', label: '라이선싱' },
  { id: 'register', label: '모델 등록' },
  { id: 'model-info', label: '모델 정보' },
];

function scrollToSection(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

export function LandingHeader({ onPrimary, primaryLabel }) {
  const [menuOpen, setMenuOpen] = useState(false);

  const go = (id) => {
    setMenuOpen(false);
    scrollToSection(id);
  };

  return (
    <header className={s.header}>
      <a className={s.brand} href="#top">
        <img alt="" className={s.brandLogo} src="/assets/brand/logo.svg" />
        <span className={s.brandName}>FaceMarket</span>
      </a>

      <nav aria-label="랜딩 섹션" className={s.nav}>
        {NAV.map((item) => (
          <button className={s.navLink} key={item.id} onClick={() => go(item.id)} type="button">
            {item.label}
          </button>
        ))}
      </nav>

      <div className={s.headerActions}>
        <button className={s.headerCta} onClick={onPrimary} type="button">
          {primaryLabel}
          <Icon name="arrowRight" size={16} stroke={2} />
        </button>
        <button
          aria-expanded={menuOpen}
          aria-label={menuOpen ? '메뉴 닫기' : '메뉴 열기'}
          className={s.menuButton}
          onClick={() => setMenuOpen((open) => !open)}
          type="button"
        >
          <Icon name={menuOpen ? 'x' : 'listBullet'} size={22} stroke={2} />
        </button>
      </div>

      {menuOpen && (
        <nav aria-label="모바일 메뉴" className={s.mobileNav}>
          {NAV.map((item) => (
            <button className={s.navLink} key={item.id} onClick={() => go(item.id)} type="button">
              {item.label}
            </button>
          ))}
        </nav>
      )}
    </header>
  );
}
```

- [ ] **Step 2: 히어로를 만든다**

`src/features/facemarket-landing/sections/HeroSection.jsx`:

```jsx
/* 히어로 — 모델 관점 한 문장. 셀러·브랜드 관점 문구는 여기 들어오지 않는다.
   대형 세리프는 Cormorant(라틴 전용)라 영문이고, 한글 리드문은 Pretendard 다. */
import { Icon } from '@/components/ui.jsx';
import s from '../FacemarketLanding.module.css';

export function HeroSection({ onPrimary, primaryLabel }) {
  return (
    <section className={s.hero}>
      <p className={s.eyebrow}>FACEMARKET</p>
      <h1 className={s.heroTitle}>Your face, your terms.</h1>
      <p className={s.heroLead}>
        얼굴을 등록하고, 어떤 옷에 어떤 기간으로 쓸 수 있는지 직접 정하고,
        누구나 확인할 수 있는 라이선스로 발급받습니다.
      </p>
      <button className={s.heroCta} onClick={onPrimary} type="button">
        {primaryLabel}
        <Icon name="arrowRight" size={18} stroke={2} />
      </button>
    </section>
  );
}
```

- [ ] **Step 3: 캐러셀 섹션을 만든다**

`src/features/facemarket-landing/sections/GallerySection.jsx`:

```jsx
/* 캐러셀 섹션. 이미지는 전부 가상 모델이고, 그 사실이 화면에 상시 보인다 —
   고지가 없으면 이미 등록된 실존 모델 목록으로 읽힌다. */
import { Icon } from '@/components/ui.jsx';
import { CarouselStage } from '../carousel/CarouselStage.jsx';
import { useCarouselController } from '../carousel/useCarouselController.js';
import { LANDING_MODELS } from '../data/landingModels.js';
import s from '../FacemarketLanding.module.css';

export function GallerySection() {
  const controller = useCarouselController(LANDING_MODELS.length, 1);

  return (
    <section aria-label="예시 이미지 갤러리" className={s.gallery} id="gallery">
      <CarouselStage controller={controller} items={LANDING_MODELS} />

      <div className={s.galleryBar}>
        <button
          aria-label="이전 이미지"
          className={s.galleryArrow}
          onClick={() => controller.goBy(-1)}
          type="button"
        >
          <Icon name="chevLeft" size={20} stroke={2} />
        </button>

        <div className={s.dots} role="tablist">
          {LANDING_MODELS.map((item, index) => (
            <button
              aria-label={`${index + 1}번 이미지 보기`}
              aria-selected={index === controller.activeIndex}
              className={index === controller.activeIndex ? s.dotActive : s.dot}
              key={item.id}
              onClick={() => controller.goTo(index)}
              role="tab"
              type="button"
            />
          ))}
        </div>

        <button
          aria-label="다음 이미지"
          className={s.galleryArrow}
          onClick={() => controller.goBy(1)}
          type="button"
        >
          <Icon name="chevRight" size={20} stroke={2} />
        </button>
      </div>

      <p className={s.galleryNotice}>
        <Icon name="info" size={14} stroke={2} />
        예시 이미지입니다. 실제 등록된 모델이 아닙니다.
      </p>
    </section>
  );
}
```

- [ ] **Step 4: 랜딩에 조립하고 스타일을 채운다**

`FacemarketLanding.jsx` 를 아래로 바꾼다. CTA 문구·동작은 Task 7 에서 실제 등록 상태에 연결되므로, 여기서는 고정 문구와 로그인 모달만 붙인다.

```jsx
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { LandingHeader } from './LandingHeader.jsx';
import { HeroSection } from './sections/HeroSection.jsx';
import { GallerySection } from './sections/GallerySection.jsx';
import s from './FacemarketLanding.module.css';

export function FacemarketLanding() {
  const { session, openLogin } = useAuth();
  const navigate = useNavigate();
  const primaryLabel = '모델 등록 시작';

  // 비로그인이면 로그인 모달을 띄우고 복귀 목표를 등록으로 심는다.
  const onPrimary = () => {
    if (session) navigate('/model/register');
    else openLogin('/model/register');
  };

  return (
    <main className={s.shell} id="top">
      <LandingHeader onPrimary={onPrimary} primaryLabel={primaryLabel} />
      <HeroSection onPrimary={onPrimary} primaryLabel={primaryLabel} />
      <GallerySection />
    </main>
  );
}
```

`FacemarketLanding.module.css` 에서 `.placeholder` 를 지우고 아래를 덧붙인다:

```css
/* ---- 상단바 ---- */
.header {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  height: clamp(4.5rem, 8vw, 6.5rem);
}

.brand { display: inline-flex; align-items: center; gap: 0.55rem; color: inherit; text-decoration: none; }
.brandLogo { width: 1.4rem; height: 1.4rem; }
.brandName { font: 600 1.05rem/1 var(--font-body); letter-spacing: -0.01em; }

.nav { display: none; gap: 1.6rem; }
.navLink {
  padding: 0.35rem 0;
  border: 0;
  background: none;
  color: var(--fm-muted);
  font: 500 0.92rem/1 var(--font-body);
  cursor: pointer;
  transition: color 140ms ease;
}
.navLink:hover { color: var(--fm-ink); }

.headerActions { display: inline-flex; align-items: center; gap: 0.6rem; }

.headerCta,
.heroCta {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  border: 0;
  border-radius: 999px;
  background: var(--fm-ink);
  color: #fff;
  font: 500 0.92rem/1 var(--font-body);
  cursor: pointer;
  transition: opacity 140ms ease;
}
.headerCta { padding: 0.68rem 1.1rem; }
.heroCta { padding: 0.95rem 1.6rem; font-size: 1rem; }
.headerCta:hover, .heroCta:hover { opacity: 0.86; }

.menuButton {
  display: inline-flex;
  padding: 0.4rem;
  border: 0;
  background: none;
  color: inherit;
  cursor: pointer;
}

.mobileNav {
  position: absolute;
  inset: 100% 0 auto;
  z-index: 20;
  display: grid;
  gap: 0.4rem;
  padding: 1rem;
  border: 1px solid var(--fm-line);
  border-radius: 1rem;
  background: #fff;
  box-shadow: 0 1.4rem 3rem rgba(16, 21, 26, 0.14);
}

@media (min-width: 48rem) {
  .nav { display: inline-flex; }
  .menuButton { display: none; }
}

/* ---- 히어로 ---- */
.hero { padding: clamp(2rem, 6vw, 4.5rem) 0 clamp(1rem, 3vw, 2rem); text-align: center; }
.eyebrow {
  margin: 0 0 1rem;
  color: var(--fm-muted);
  font: 500 0.76rem/1 var(--font-body);
  letter-spacing: 0.22em;
}
.heroTitle {
  margin: 0 0 1.1rem;
  font-family: var(--font-serif);
  font-size: clamp(2.6rem, 8vw, 5.5rem);
  font-weight: 600;
  line-height: 1.02;
  letter-spacing: -0.015em;
}
.heroLead {
  max-width: 34rem;
  margin: 0 auto 1.8rem;
  color: var(--fm-muted);
  font: 400 clamp(0.95rem, 1.4vw, 1.05rem)/1.7 var(--font-body);
}

/* ---- 캐러셀 ---- */
.gallery { padding: clamp(1rem, 3vw, 2rem) 0 clamp(2rem, 5vw, 4rem); }

.galleryBar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  margin-top: 1.4rem;
}

.galleryArrow {
  display: inline-flex;
  padding: 0.55rem;
  border: 1px solid var(--fm-line);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.7);
  color: var(--fm-ink);
  cursor: pointer;
}
.galleryArrow:hover { background: #fff; }

.dots { display: inline-flex; gap: 0.4rem; }
.dot, .dotActive {
  width: 0.42rem;
  height: 0.42rem;
  padding: 0;
  border: 0;
  border-radius: 999px;
  cursor: pointer;
  transition: background 160ms ease, transform 160ms ease;
}
.dot { background: rgba(16, 21, 26, 0.2); }
.dotActive { background: var(--fm-accent); transform: scale(1.5); }

.galleryNotice {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.35rem;
  margin: 1.1rem 0 0;
  color: var(--fm-muted);
  font: 400 0.8rem/1.5 var(--font-body);
}
```

- [ ] **Step 5: 브라우저에서 캐러셀을 확인한다**

Run: `pnpm dev` → `http://localhost:5173/?facemarket=1`

확인 항목 (하나라도 어긋나면 다음 태스크로 넘어가지 말 것):
1. **오목 아크** — 가운데 카드가 가장 작고, 양옆으로 갈수록 카드가 커지며 앞으로 나온다. 원본 스크린샷(`~/Documents/Codex/.../docs/reference/spotlight-source.png`)과 나란히 놓고 비교한다.
2. **겹침 순서** — 앞으로 나온 카드가 뒤 카드를 가린다. 반대면 `zIndex` 계산이 틀린 것이다.
3. **회전 방향** — 왼쪽 카드는 오른쪽(가운데)을 향하고 오른쪽 카드는 왼쪽을 향한다. 반대면 `rotateY` 부호를 뒤집는다.
4. 드래그·스와이프·좌우 화살표 키·이전/다음 버튼·점 클릭이 모두 동작하고, 손을 뗀 뒤 카드에 딱 맞게 스냅한다.
5. 14 → 1 경계를 지날 때 역주행하지 않는다.
6. 카드를 드래그해서 놓았을 때 그 카드로 점프하지 **않는다**(드래그 클릭 억제).
7. 캐러셀 위에서 세로 스크롤이 정상 동작한다.
8. 좁은 화면(폰 크기)에서 카드 1장 중심 + 양옆이 살짝 보인다.
9. macOS 시스템 설정 → 손쉬운 사용 → 동작 줄이기를 켜면 관성 없이 즉시 스냅한다.
10. 콘솔에 에러가 없다.

- [ ] **Step 6: 커밋한다**

```bash
git add src/features/facemarket-landing/
git commit -m "feat(facemarket): 랜딩 상단바·히어로·캐러셀 섹션"
```

---

### Task 6: 라이선싱 섹션

Mirror Mirror AI 라이선싱 페이지의 정보 구조(카드 6장 + 검증 가능한 기록)를 빌리고 내용은 우리 것으로 채운다. C2PA 로고 자리에 OpenDID VC → OmniOne Chain 앵커 → 공개 검증이 들어간다.

> **[구현과 다름 — 이 Task 의 카피는 전부 하향됐다. 아래 블록의 문장을 그대로 복사하지 마라.]**
> 정본은 `LicensingSection.jsx` 의 헤더 주석이다(각 문장의 코드 근거가 거기 적혀 있다).
> - **"검증 가능한 기록"은 세 칸이 아니라 네 칸**이고, 두 번째 칸 이름은 "체인 앵커"가 아니라
>   **"체인 기록"** 이다 — 체인에 올라가는 건 사용 1건짜리 `recordSettlement` 뿐이고
>   라이선스 자체의 지문을 올리는 코드는 없다.
> - **공개 검증은 `fm_licenses` 화이트리스트 SELECT 한 방**이다. VC 서명 대조도 체인 조회도
>   하지 않는다 — 문장에 "지금은 Wearless 기록 조회"라고 남긴다.
> - **정산 기록의 단위는 컷이 아니라 잡(상세페이지) 1건**이다(`workers/detail_page_job.py`
>   의 `payment_key=f"job:{job_id}"`). "컷마다"로 적으면 모델이 컷 수만큼 곱해 기대한다.
>   그리고 지급 기능이 없으므로 "정산받습니다"가 아니라 "기록됩니다 · 지급은 준비 중"이다.
> - 조건은 **넷뿐**(허용 품목·금지 품목·건당 단가·유효기간) — **채널 조건은 제품에 없다.**
> - 유효기간 선택지는 **90일·1년·2년** 셋이다.
> - 금지 품목을 이어 붙일 땐 구분자가 `', '` 다 — 품목명이 `속옷·란제리` 처럼 `·` 를 품어서
>   `·` 로 이으면 2개가 4개로 읽힌다.
> - **여섯째 카드의 단어는 '폐기'가 아니라 '해지'다.** 제품 화면의 말이 전부 '해지'라
>   (`ModelLicense.jsx` 버튼·확인창·상태, `PublicVerify.jsx`), 랜딩만 '폐기'라고 부르면
>   되돌릴 수 없는 조치를 앞둔 모델이 그 버튼을 못 찾는다. PRD 의 '폐기'는 내부 용어지
>   사용자 대면 문자열이 아니다. **아래 카피 블록에는 '폐기'가 남아 있다 — 원안이다.**
> - **'체인 기록' 칸은 무조건형이 아니라 상한형이다.** `record_license_settlement` 는
>   best-effort 라(체인 미설정·RPC 실패·조회 실패면 `return None`) 실패한 건은 체인에도
>   `fm_settlements` 에도 행이 없다. 그래서 "올리기가 실패한 건은 기록이 남지 않습니다"가
>   문장에 붙는다. 이 상한을 지우려면 카피가 아니라 코드를 바꿔라.

**Files:**
- Create: `src/features/facemarket-landing/sections/LicensingSection.jsx`
- Modify: `src/features/facemarket-landing/FacemarketLanding.jsx`
- Modify: `src/features/facemarket-landing/FacemarketLanding.module.css`

**Interfaces:**
- Consumes: `@/lib/brandUseCategories.js` 의 `ALLOWED_BRAND_USE_CATEGORIES` · `FORBIDDEN_BRAND_USE_CATEGORIES`
- Produces: `<LicensingSection />` — `id="licensing"`

- [ ] **Step 1: 섹션을 만든다**

`src/features/facemarket-landing/sections/LicensingSection.jsx`:

```jsx
/* =============================================================
   라이선싱 설명 섹션.
   여기서 라이선스를 발급하지 않는다 — 발급 폼은 /model/license 그대로다.
   이 섹션은 "무엇을 정할 수 있고, 그게 어떻게 증명되는지"만 설명한다.
   ============================================================= */
import { Icon } from '@/components/ui.jsx';
import {
  ALLOWED_BRAND_USE_CATEGORIES,
  FORBIDDEN_BRAND_USE_CATEGORIES,
} from '@/lib/brandUseCategories.js';
import s from '../FacemarketLanding.module.css';

const CARDS = [
  {
    icon: 'settings',
    title: '조건을 모델이 정한다',
    body: '어떤 품목에, 어떤 채널에, 얼마 동안 쓸 수 있는지 본인이 고릅니다. 브랜드가 따로 계약서를 들이밀지 않습니다.',
  },
  {
    icon: 'checkSquare',
    title: '허용 품목을 고른다',
    body: `상의·아우터·데님 등 ${ALLOWED_BRAND_USE_CATEGORIES.length}개 품목 중에서 고릅니다. ${FORBIDDEN_BRAND_USE_CATEGORIES.join('·')}는 선택지에 없습니다.`,
  },
  {
    icon: 'clock',
    title: '기간이 정해져 있다',
    body: '90일 또는 1년. 기간이 끝나면 그 라이선스로는 더 이상 컷을 만들 수 없습니다.',
  },
  {
    icon: 'lock',
    title: '서명된 자격증명으로 발급된다',
    body: '조건은 W3C Verifiable Credential 로 발급됩니다. 나중에 말을 바꿀 수 없는 형태로 남습니다.',
  },
  {
    icon: 'search',
    title: 'QR 하나로 누구나 확인한다',
    body: '구매자든 심사위원이든 로그인 없이 QR 을 찍어 유효한 라이선스인지 확인합니다. 그 화면에 얼굴은 나오지 않습니다.',
  },
  {
    icon: 'ban',
    title: '폐기하면 즉시 무효가 된다',
    body: '마음이 바뀌면 폐기합니다. 폐기된 라이선스는 검증 화면에서 곧바로 무효로 표시됩니다.',
  },
];

const RECORD = [
  { icon: 'lock', title: 'VC 발급', body: '라이선스 조건이 서명된 자격증명이 됩니다.' },
  { icon: 'layers', title: '체인 앵커', body: 'OmniOne Chain 에 기록해 나중에 바뀌지 않았음을 확인할 수 있게 합니다.' },
  { icon: 'eye', title: '공개 검증', body: 'QR 주소만 알면 인증 없이 유효성을 확인합니다.' },
  { icon: 'coins', title: '사용 기록과 정산', body: '내 얼굴로 만들어진 컷마다 사용 기록이 남고, 그 기록을 근거로 정산됩니다.' },
];

export function LicensingSection() {
  return (
    <section className={s.section} id="licensing">
      <p className={s.eyebrow}>라이선싱</p>
      <h2 className={s.sectionTitle}>내 얼굴, 내가 정한 조건으로만</h2>
      <p className={s.sectionLead}>
        얼굴을 넘기는 게 아니라 조건을 붙여 빌려주는 겁니다.
        무엇을 허용했는지가 문서로 남고, 그 문서를 누구나 확인할 수 있습니다.
      </p>

      <div className={s.cardGrid}>
        {CARDS.map((card) => (
          <article className={s.card} key={card.title}>
            <Icon name={card.icon} size={22} stroke={1.7} />
            <h3 className={s.cardTitle}>{card.title}</h3>
            <p className={s.cardBody}>{card.body}</p>
          </article>
        ))}
      </div>

      <div className={s.record}>
        <div className={s.recordHead}>
          <h3 className={s.recordTitle}>확인할 수 있는 기록으로 남습니다</h3>
          <p className={s.recordLead}>
            발급된 라이선스는 서명된 자격증명이 되고, 그 지문이 체인에 기록됩니다.
            나중에 조건을 두고 다툴 일이 생겨도 무엇을 허용했는지가 남아 있습니다.
          </p>
        </div>
        <ol className={s.recordSteps}>
          {RECORD.map((step, index) => (
            <li className={s.recordStep} key={step.title}>
              <span className={s.recordNumber}>{String(index + 1).padStart(2, '0')}</span>
              <Icon name={step.icon} size={20} stroke={1.7} />
              <h4 className={s.recordStepTitle}>{step.title}</h4>
              <p className={s.recordStepBody}>{step.body}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
```

- [ ] **Step 2: 랜딩에 끼우고 스타일을 더한다**

`FacemarketLanding.jsx` 의 import 와 JSX 에 추가:

```jsx
import { LicensingSection } from './sections/LicensingSection.jsx';
```

```jsx
      <GallerySection />
      <LicensingSection />
```

`FacemarketLanding.module.css` 에 덧붙인다:

```css
/* ---- 공통 섹션 ---- */
.section { padding: clamp(3rem, 8vw, 6.5rem) 0; border-top: 1px solid var(--fm-line); }
.sectionTitle {
  margin: 0 0 0.9rem;
  font-family: var(--font-serif);
  font-size: clamp(1.9rem, 4.5vw, 3.2rem);
  font-weight: 600;
  line-height: 1.1;
  letter-spacing: -0.012em;
}
.sectionLead {
  max-width: 38rem;
  margin: 0 0 2.4rem;
  color: var(--fm-muted);
  font: 400 clamp(0.92rem, 1.3vw, 1rem)/1.75 var(--font-body);
}

.cardGrid {
  display: grid;
  gap: 1rem;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 17rem), 1fr));
}

.card {
  display: grid;
  gap: 0.6rem;
  align-content: start;
  padding: 1.5rem;
  border: 1px solid var(--fm-line);
  border-radius: 1.1rem;
  background: rgba(255, 255, 255, 0.62);
}
.cardTitle { margin: 0; font: 600 1rem/1.4 var(--font-body); }
.cardBody { margin: 0; color: var(--fm-muted); font: 400 0.9rem/1.65 var(--font-body); }

/* ---- 검증 가능한 기록 ---- */
.record {
  display: grid;
  gap: 2rem;
  margin-top: 2.5rem;
  padding: clamp(1.6rem, 4vw, 2.8rem);
  border-radius: 1.4rem;
  background: rgba(45, 99, 255, 0.05);
}
.recordHead { max-width: 40rem; }
.recordTitle { margin: 0 0 0.7rem; font: 600 clamp(1.15rem, 2vw, 1.5rem)/1.35 var(--font-body); }
.recordLead { margin: 0; color: var(--fm-muted); font: 400 0.92rem/1.7 var(--font-body); }

.recordSteps {
  display: grid;
  gap: 1rem;
  margin: 0;
  padding: 0;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 13rem), 1fr));
  list-style: none;
}
.recordStep {
  display: grid;
  gap: 0.45rem;
  align-content: start;
  padding: 1.15rem;
  border-radius: 0.9rem;
  background: rgba(255, 255, 255, 0.8);
}
.recordNumber { color: var(--fm-accent); font: 600 0.72rem/1 var(--font-body); letter-spacing: 0.14em; }
.recordStepTitle { margin: 0; font: 600 0.95rem/1.4 var(--font-body); }
.recordStepBody { margin: 0; color: var(--fm-muted); font: 400 0.85rem/1.6 var(--font-body); }
```

- [ ] **Step 3: 브라우저에서 확인한다**

Run: `pnpm dev` → `http://localhost:5173/?facemarket=1`

확인 항목: 상단바 "라이선싱" 클릭 → 이 섹션으로 부드럽게 스크롤. 카드 6장이 좁은 화면에서 1열, 넓은 화면에서 3열. 허용 품목 개수(11)와 금지 품목 이름이 `brandUseCategories.js` 와 일치. 가로 스크롤바가 생기지 않는다.

- [ ] **Step 4: 커밋한다**

```bash
git add src/features/facemarket-landing/
git commit -m "feat(facemarket): 랜딩 라이선싱 섹션 — 조건 카드와 검증 가능한 기록"
```

---

### Task 7: 모델 등록 섹션과 상태별 CTA

7단계 레일을 미리 보여준다. CTA 문구는 등록 상태에 따라 바뀌되, 상태 조회가 늦거나 실패해도 랜딩 렌더를 막지 않는다.

> **[구현과 다름] 세 가지가 붙었다.**
> - 레일의 각 단계 설명은 **그 단계가 실제로 하는 일**이어야 한다. prod 는
>   `FM_LIVENESS_ENABLED=false` 라 '라이브' 단계에 카메라 라이브니스가 돌지 않으므로,
>   "실제 본인인지 확인"이라고 적으면 하지 않는 검사를 약속하는 셈이다.
> - CTA 핸들러는 **인증 부트스트랩 중(`loading`)에 아무 분기도 태우지 않는다.** 그때의
>   `session === null` 은 '비로그인'이 아니라 '아직 모름'이라, 분기하면 이미 로그인한
>   사람에게 로그인 모달이 뜨고 복귀 플래그까지 심는다.
>
>   > **⚠️ 그 대기를 `disabled` + `aria-busy` 로 드러내라는 앞 판의 요구는 폐기됐다.
>   > 이 자리에서 두 라운드가 반대 방향으로 왕복했으니 여기서 끝낸다.**
>   >
>   > 분기는 미루되 **의도는 버리지 않는다** — `loading` 중에 눌린 클릭은
>   > `FacemarketLanding.jsx` 의 보류함 `pendingPrimary` 에 담겼다가 `loading` 이 내려가면
>   > **한 번만** 실행된다. `disabled` 를 걸면 **그 클릭이 DOM 에 아예 도달하지 않아**
>   > 보류함이 통째로 도달 불가능한 죽은 코드가 되고, 토큰 갱신이 긴 회선에서는 사용자가
>   > 눌리지도 않는 버튼을 몇 초간 마주한다. 즉 "고장 난 버튼처럼 보인다"는 걱정을
>   > `disabled` 로 풀면 걱정하던 그 고장을 **진짜로** 만든다.
>   >
>   > 대기 표시는 **버튼을 잠그지 않는 방법으로만** 한다. 이미 그렇게 돼 있다 — 보류함이
>   > `useState` 라서 눌리면 네 CTA 의 라벨이 함께 `'확인 중이에요…'` 로 바뀐다.
>   > `useRef` 로 되돌리면 눌린 사실이 화면에 하나도 안 남는다.
>   > 이 결정은 세 곳에 못 박혀 있다 — 스펙 §1 히어로의 경고 블록(정본),
>   > `LandingHeader.jsx` 의 헤더 주석, 그리고 이 블록. **한 곳만 고치지 마라.**
> - 상태 조회는 `session` 객체가 아니라 **사용자 id** 로 묶는다. `session` 은 토큰 갱신마다
>   새 객체가 되는데 같은 사람이면 CTA 도 같다.

**Files:**
- Create: `src/features/facemarket-landing/registerCta.js`
- Create: `src/features/facemarket-landing/sections/RegisterSection.jsx`
- Modify: `src/features/facemarket-landing/FacemarketLanding.jsx`
- Modify: `src/features/facemarket-landing/FacemarketLanding.module.css`
- Test: `tests/frontend/facemarket-register-cta.test.mjs`

**Interfaces:**
- Consumes: `@/lib/api/facemarket.js` 의 `listMyModels` · `getCurrentEnrollment`, `@/features/auth/AuthProvider.jsx` 의 `useAuth`
- Produces:
  - `registerCta(ownedModel, enrollment) => { label: string, to: string }`
  - `<RegisterSection ctaLabel to onPrimary />` — `id="register"`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/frontend/facemarket-register-cta.test.mjs`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';

import { registerCta } from '../../src/features/facemarket-landing/registerCta.js';

test('아무것도 없으면 등록을 시작하게 한다', () => {
  assert.deepEqual(registerCta(null, null), { label: '모델 등록 시작', to: '/model/register' });
  assert.deepEqual(registerCta(undefined, undefined), { label: '모델 등록 시작', to: '/model/register' });
});

test('등록이 진행 중이면 이어서 하게 한다', () => {
  assert.deepEqual(
    registerCta(null, { id: 'e1', status: 'photos_pending' }),
    { label: '이어서 등록하기', to: '/model/register' },
  );
  assert.deepEqual(
    registerCta({ id: 'm1', status: 'pending' }, null),
    { label: '이어서 등록하기', to: '/model/register' },
  );
});

test('재검증이 필요한 모델도 등록으로 보낸다', () => {
  assert.deepEqual(
    registerCta({ id: 'm1', status: 'reverification_required' }, null),
    { label: '이어서 등록하기', to: '/model/register' },
  );
});

test('검증된 모델은 자기 정보로 보낸다', () => {
  assert.deepEqual(
    registerCta({ id: 'm1', status: 'verified' }, null),
    { label: '내 모델 정보', to: '/model' },
  );
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pnpm test:frontend`
Expected: FAIL — `Cannot find module '.../registerCta.js'`

- [ ] **Step 3: 판정 함수를 만든다**

`src/features/facemarket-landing/registerCta.js`:

```js
/* =============================================================
   facemarket-landing/registerCta.js
   등록 상태 → CTA 문구·경로. 상태 라벨은 ModelHub 와 같은 어휘를 쓴다
   (model: pending · reverification_required · verified).

   조회 실패는 여기서 다루지 않는다 — 호출부가 null 을 넘기면 기본값인
   '모델 등록 시작'이 나온다. 랜딩이 조회 결과를 기다리다 비어 있으면 안 된다.
   ============================================================= */

export function registerCta(ownedModel, enrollment) {
  if (ownedModel?.status === 'verified') {
    return { label: '내 모델 정보', to: '/model' };
  }
  if (ownedModel || enrollment) {
    return { label: '이어서 등록하기', to: '/model/register' };
  }
  return { label: '모델 등록 시작', to: '/model/register' };
}
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `pnpm test:frontend`
Expected: PASS

- [ ] **Step 5: 섹션을 만든다**

`src/features/facemarket-landing/sections/RegisterSection.jsx`:

```jsx
/* =============================================================
   모델 등록 섹션 — 7단계 레일 미리보기.
   진행 레일을 미리 보여주는 건 장식이 아니다. 순차 KYC 라 몇 단계인지
   모르고 들어가면 중간에 이탈한다. 건너뛸 수 있는 단계도 여기 적는다.
   레일 순서·라벨은 ModelRegister.jsx 의 STEPS 와 같아야 한다.
   ============================================================= */
import { Icon } from '@/components/ui.jsx';
import s from '../FacemarketLanding.module.css';

const STEPS = [
  { label: '동의', note: '생체정보 처리 동의' },
  { label: '신분증', note: '모바일 신분증으로 본인 확인' },
  { label: '사진', note: '얼굴 사진 3장' },
  { label: '체형', note: '선택 — 건너뛸 수 있어요' },
  { label: '대표', note: '선택 — 건너뛸 수 있어요' },
  { label: '라이브', note: '실제 본인인지 확인' },
  { label: '완료', note: '모델 준비' },
];

export function RegisterSection({ ctaLabel, onPrimary }) {
  return (
    <section className={s.section} id="register">
      <p className={s.eyebrow}>모델 등록</p>
      <h2 className={s.sectionTitle}>일곱 단계면 끝납니다</h2>
      <p className={s.sectionLead}>
        본인 확인이 필요한 절차라 순서대로 진행합니다. 체형과 대표 이미지는 건너뛸 수 있고,
        중간에 나갔다가 이어서 할 수 있습니다.
      </p>

      <ol className={s.rail}>
        {STEPS.map((step, index) => (
          <li className={s.railStep} key={step.label}>
            <span className={s.railNumber}>{index + 1}</span>
            <span className={s.railLabel}>{step.label}</span>
            <span className={s.railNote}>{step.note}</span>
          </li>
        ))}
      </ol>

      <button className={s.heroCta} onClick={onPrimary} type="button">
        {ctaLabel}
        <Icon name="arrowRight" size={18} stroke={2} />
      </button>
    </section>
  );
}
```

- [ ] **Step 6: 랜딩에서 상태를 읽어 CTA 에 연결한다**

`FacemarketLanding.jsx` 를 아래로 바꾼다:

```jsx
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { getCurrentEnrollment, listMyModels } from '@/lib/api/facemarket.js';
import { LandingHeader } from './LandingHeader.jsx';
import { registerCta } from './registerCta.js';
import { HeroSection } from './sections/HeroSection.jsx';
import { GallerySection } from './sections/GallerySection.jsx';
import { LicensingSection } from './sections/LicensingSection.jsx';
import { RegisterSection } from './sections/RegisterSection.jsx';
import s from './FacemarketLanding.module.css';

export function FacemarketLanding() {
  const { session, openLogin } = useAuth();
  const navigate = useNavigate();
  const [cta, setCta] = useState(() => registerCta(null, null));

  // 등록 상태로 CTA 문구를 바꾼다. 조회는 랜딩 렌더를 막지 않는다 —
  // 기본 문구로 먼저 그리고, 결과가 오면 교체한다. 실패하면 기본 문구로 남는다.
  useEffect(() => {
    if (!session) { setCta(registerCta(null, null)); return undefined; }

    let alive = true;
    void (async () => {
      const [models, enrollment] = await Promise.all([
        listMyModels().catch(() => null),
        getCurrentEnrollment().catch(() => null),
      ]);
      if (!alive) return;
      setCta(registerCta(models?.[0] || null, enrollment));
    })();
    return () => { alive = false; };
  }, [session]);

  const onPrimary = () => {
    if (session) navigate(cta.to);
    else openLogin('/model/register');
  };

  return (
    <main className={s.shell} id="top">
      <LandingHeader onPrimary={onPrimary} primaryLabel={cta.label} />
      <HeroSection onPrimary={onPrimary} primaryLabel={cta.label} />
      <GallerySection />
      <LicensingSection />
      <RegisterSection ctaLabel={cta.label} onPrimary={onPrimary} />
    </main>
  );
}
```

- [ ] **Step 7: 레일 스타일을 더한다**

`FacemarketLanding.module.css` 에 덧붙인다:

```css
.rail {
  display: grid;
  gap: 0.7rem;
  margin: 0 0 2.2rem;
  padding: 0;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 9.5rem), 1fr));
  list-style: none;
  counter-reset: rail;
}
.railStep {
  display: grid;
  gap: 0.3rem;
  align-content: start;
  padding: 1rem;
  border: 1px solid var(--fm-line);
  border-radius: 0.85rem;
  background: rgba(255, 255, 255, 0.62);
}
.railNumber {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.5rem;
  height: 1.5rem;
  border-radius: 999px;
  background: var(--fm-ink);
  color: #fff;
  font: 600 0.75rem/1 var(--font-body);
}
.railLabel { font: 600 0.95rem/1.4 var(--font-body); }
.railNote { color: var(--fm-muted); font: 400 0.8rem/1.5 var(--font-body); }
```

- [ ] **Step 8: 브라우저에서 확인한다**

Run: `pnpm dev` → `http://localhost:5173/?facemarket=1`

확인 항목: 비로그인 상태에서 CTA 가 "모델 등록 시작"이고, 누르면 로그인 모달이 뜬다. 로그인 후 `/model/register` 로 간다(Task 4 에서 심은 복귀 경로가 동작). 상단바 "모델 등록" 클릭 → 이 섹션으로 스크롤. 7단계 레일의 라벨·순서가 `ModelRegister.jsx:110-116` 과 같다.

- [ ] **Step 9: 커밋한다**

```bash
git add src/features/facemarket-landing/ tests/frontend/facemarket-register-cta.test.mjs
git commit -m "feat(facemarket): 랜딩 등록 섹션과 상태별 CTA"
```

---

### Task 8: 모델 정보 섹션과 푸터

프라이버시 하드룰 7개를 모델이 읽을 언어로 옮긴다. 이 섹션이 랜딩의 신뢰 축이다 — 방문자가 등록 전에 가장 알고 싶은 건 자기 상태가 아니라 얼굴이 어떻게 취급되는지다.

> **[구현과 다름 — 하드룰을 직역하면 거짓이 되는 자리가 둘 있다.]**
> - **하드룰 1(얼굴은 공개 URL 없음)은 등록 사진에만 참이다.** 대표 이미지는 의도적 예외 —
>   셀러 카탈로그에 얼굴이 보여야 모델을 고를 수 있어서 `coverImageUrl` 은 비공개 R2 의
>   presigned GET(1시간)이고, 카드 HTML 에 무인증 주소가 그대로 실린다. 예외를 안 적으면
>   문장이 거짓말이 된다(대표 이미지가 건너뛸 수 있는 선택 단계라는 것도 같이 적는다).
> - **하드룰 2 는 "생체정보 0픽셀"이지 "조건과 유효 여부만"이 아니다.** 공개 검증 응답에는
>   마스킹 이름(`홍*동`)과 만 나이도 실린다.
>
> 푸터도 다르다. **개인정보처리방침·이용약관 링크를 넣지 않았다 — 걸 곳이 없다.** 레포에
> 그 페이지가 없고(`/legal/…` 라우트 부재) 서버 `NOTICE_URIS` 도 "법무 확정 URI 자리"
> 플레이스홀더라 지금 걸면 catch-all 로 튕긴다. 없는 주소를 지어 거는 것보다 없다고 말하는
> 편이 낫다. 문의 창구와 사업자 정보도 확인할 소스가 레포에 없어 비웠다.
>
> **철회 경로도 다르다 — 3라운드 감사의 유일한 critical 이다.** 스펙·계획이 지목한
> `/model/withdraw`(`ModelWithdraw.jsx`)는 **개인화(personalization) 도메인 전용**이다.
> `GET /v1/personalization/status` 를 보고 `personalization_profiles` 행이 없으면 "등록된
> 데이터가 없어요." 한 줄만 그리는데, FaceMarket 7단계 위저드는 그 테이블에 행을 만들지
> 않는다(`facemarket.py` 가 쓰는 건 `personalization_identity_verifications` 뿐).
> FaceMarket 모델에게 그 화면은 빈 화면이고 `POST /v1/personalization:withdraw` 도 404 다.
> facemarket 라우트에도 등록 철회·삭제는 없다(있는 건 `/licenses/{id}/revoke` 와 진행 중
> 등록 `cancel`). 그래서 이 문단은 **라이선스 해지까지만 약속하고 등록 파기 화면은 아직
> 없다고 밝힌다.** 등록 파기 진입점이 생기기 전까지 `/model/withdraw` 를 여기 적지 마라.
>
> **어휘도 다르다 — 랜딩은 '폐기'가 아니라 '해지'라고 쓴다.** 제품 화면의 말이 전부
> '해지'다(`ModelLicense.jsx` 의 버튼 "해지" · 확인창 "이 라이선스를 해지하면…" · 상태
> "해지됨", `PublicVerify.jsx` "해지된 라이선스예요"). PRD 는 '폐기'를 쓰지만 **PRD 는
> 사용자 대면 문자열이 아니다.** 랜딩만 '폐기'라고 부르면, 되돌릴 수 없는 조치를 앞둔
> 모델이 `/model/license` 에서 그 단어를 못 찾고 멈춘다. 아래 코드 블록과 Task 6 의
> 카피 원안에는 '폐기'가 남아 있다 — **그건 계획 원안이지 머지된 문자열이 아니다.**
>
> **푸터 문구는 "약관이 없다"고 단정하지 않는다.** 이 랜딩 CTA 가 여는 로그인 모달이 두
> 클릭 거리에 있고 그쪽은 "계속하면 서비스 약관에 동의하는 것으로 간주됩니다"라고 말한다 —
> 한쪽이 부재를 단정하면 두 화면이 서로를 부정한다. 그래서 푸터는 "공개 문서가 준비되면
> 여기에 겁니다"까지만 적고, **모달의 `IS_FACEMARKET` 분기도 같은 말로 맞췄다**(셀러 쪽
> 문자열은 그대로). 한쪽만 고치지 마라. 그리고 근거로 `ModelConsent.jsx` 를 들지 마라 —
> 그 컴포넌트는 어디에도 마운트되지 않는다(import 하는 파일 0개).
>
> **푸터의 예시 이미지 고지는 지웠다(셋째 사본).** 정본은 캐러셀 바로 위 고지와 카드 안
> '예시' 배지 둘이고, 그 둘은 지우지 마라 — PRD §13-5 를 지키는 게 그 둘이다.
>
> `생체정보 처리 동의` 는 동의문 버전 계약이라 다른 말로 바꾸지 않는다(하드룰 7).

**Files:**
- Create: `src/features/facemarket-landing/sections/ModelInfoSection.jsx`
- Create: `src/features/facemarket-landing/sections/FooterSection.jsx`
- Modify: `src/features/facemarket-landing/FacemarketLanding.jsx`
- Modify: `src/features/facemarket-landing/FacemarketLanding.module.css`

**Interfaces:**
- Consumes: Task 5 의 섹션 스타일 클래스
- Produces: `<ModelInfoSection />` — `id="model-info"`, `<FooterSection />`

- [ ] **Step 1: 모델 정보 섹션을 만든다**

`src/features/facemarket-landing/sections/ModelInfoSection.jsx`:

```jsx
/* =============================================================
   모델 정보 섹션 — 내 얼굴이 어떻게 취급되는지.
   PRD §10 프라이버시 하드룰 7개를 모델 언어로 옮긴 것이다. 이 문장들은
   마케팅 카피가 아니라 코드가 실제로 지키는 규칙의 번역이라, 구현이
   바뀌면 여기도 같이 바뀌어야 한다.

   '생체정보 처리 동의'는 동의문 버전 계약이라 다른 말로 바꾸지 않는다(하드룰 7).
   ============================================================= */
import { Icon } from '@/components/ui.jsx';
import s from '../FacemarketLanding.module.css';

const RULES = [
  {
    icon: 'lock',
    title: '얼굴 사진 주소를 아무나 열 수 없어요',
    body: '등록한 얼굴은 공개 주소를 갖지 않습니다. 권한이 확인된 요청에만 그때그때 열립니다.',
  },
  {
    icon: 'eyeOff',
    title: 'QR 을 찍은 사람은 얼굴을 못 봐요',
    body: '공개 검증 화면에는 라이선스 조건과 유효 여부만 나옵니다. 얼굴은 한 픽셀도 렌더되지 않습니다.',
  },
  {
    icon: 'checkSquare',
    title: '검사를 통과하기 전엔 아무 데도 안 쓰여요',
    body: '올린 사진은 격리된 상태로 보관되다가, 품질과 본인 일치 검사를 통과한 뒤에야 쓰입니다.',
  },
  {
    icon: 'trash',
    title: '신분증 사진은 저장하지 않아요',
    body: '본인 확인에 쓴 신분증 초상은 처리하는 순간에만 메모리에 있고, 저장하거나 로그에 남기지 않습니다.',
  },
  {
    icon: 'link',
    title: '본인 확인값은 해시로만 남아요',
    body: '신원 식별값은 원본이 아니라 되돌릴 수 없는 형태로 저장됩니다.',
  },
  {
    icon: 'image',
    title: '검사 기록에 사진이 남지 않아요',
    body: '품질 검사 로그에는 통과 여부와 사유만 남습니다. 이미지도 파일명도 남기지 않습니다.',
  },
  {
    icon: 'info',
    title: '동의한 문서가 그대로 보존돼요',
    body: '생체정보 처리 동의는 버전이 관리됩니다. 무엇에 동의했는지가 나중에도 확인됩니다.',
  },
];

export function ModelInfoSection() {
  return (
    <section className={s.section} id="model-info">
      <p className={s.eyebrow}>모델 정보</p>
      <h2 className={s.sectionTitle}>내 얼굴이 어떻게 다뤄지나요</h2>
      <p className={s.sectionLead}>
        얼굴은 생체정보라 일반 사진과 같은 규칙으로 다루지 않습니다.
        아래는 마케팅 문구가 아니라 서비스가 실제로 지키는 규칙입니다.
      </p>

      <ul className={s.ruleList}>
        {RULES.map((rule) => (
          <li className={s.rule} key={rule.title}>
            <Icon name={rule.icon} size={20} stroke={1.7} />
            <div>
              <h3 className={s.ruleTitle}>{rule.title}</h3>
              <p className={s.ruleBody}>{rule.body}</p>
            </div>
          </li>
        ))}
      </ul>

      <div className={s.exit}>
        <h3 className={s.exitTitle}>그만두고 싶을 때</h3>
        {/* [구현과 다름] 아래 둘째 문장은 거짓이고, 첫 문장은 어휘가 틀렸다.
            (1) 등록 자체를 지우는 화면이 레포에 없다(위 경고 블록) — 실제 구현은
                /model/license 까지만 약속하고 "등록 자체를 지우는 화면은 아직 없습니다"
                라고 밝힌다.
            (2) 단어는 '폐기'가 아니라 **'해지'** 다 — 제품 화면의 버튼·확인창·검증
                페이지가 전부 '해지'라서, 랜딩만 다른 말을 쓰면 모델이 그 버튼을 못 찾는다.
            아래 블록을 그대로 복사하지 마라. */}
        <p className={s.exitBody}>
          발급한 라이선스는 언제든 폐기할 수 있고, 폐기하면 그 즉시 무효로 표시됩니다.
          모델 등록 자체를 철회하는 것도 계정에서 직접 할 수 있습니다.
        </p>
      </div>
    </section>
  );
}
```

- [ ] **Step 2: 푸터를 만든다**

`src/features/facemarket-landing/sections/FooterSection.jsx`:

```jsx
import s from '../FacemarketLanding.module.css';

export function FooterSection() {
  return (
    <footer className={s.footer}>
      <p className={s.footerBrand}>FaceMarket · Wearless</p>
      <p className={s.footerNote}>
        캐러셀에 쓰인 이미지는 모두 예시입니다. 실제 등록된 모델이 아닙니다.
      </p>
      <p className={s.footerNote}>
        상품 상세페이지를 만드는 셀러라면 <a className={s.footerLink} href="https://ai.wearless.kr">ai.wearless.kr</a> 로 오세요.
      </p>
    </footer>
  );
}
```

- [ ] **Step 3: 랜딩에 끼우고 스타일을 마무리한다**

`FacemarketLanding.jsx` 에 import 와 JSX 추가:

```jsx
import { ModelInfoSection } from './sections/ModelInfoSection.jsx';
import { FooterSection } from './sections/FooterSection.jsx';
```

```jsx
      <RegisterSection ctaLabel={cta.label} onPrimary={onPrimary} />
      <ModelInfoSection />
      <FooterSection />
```

`FacemarketLanding.module.css` 에 덧붙인다:

```css
.ruleList {
  display: grid;
  gap: 0.9rem;
  margin: 0 0 2.2rem;
  padding: 0;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 21rem), 1fr));
  list-style: none;
}
.rule {
  display: grid;
  gap: 0.85rem;
  grid-template-columns: auto 1fr;
  align-items: start;
  padding: 1.2rem;
  border: 1px solid var(--fm-line);
  border-radius: 1rem;
  background: rgba(255, 255, 255, 0.62);
}
.ruleTitle { margin: 0 0 0.3rem; font: 600 0.95rem/1.45 var(--font-body); }
.ruleBody { margin: 0; color: var(--fm-muted); font: 400 0.87rem/1.65 var(--font-body); }

.exit { padding: 1.5rem; border-radius: 1rem; background: rgba(45, 99, 255, 0.05); }
.exitTitle { margin: 0 0 0.5rem; font: 600 1rem/1.4 var(--font-body); }
.exitBody { margin: 0; color: var(--fm-muted); font: 400 0.9rem/1.7 var(--font-body); }

.footer {
  display: grid;
  gap: 0.4rem;
  padding: clamp(2.5rem, 6vw, 4rem) 0 0;
  border-top: 1px solid var(--fm-line);
}
.footerBrand { margin: 0 0 0.4rem; font: 600 0.95rem/1.4 var(--font-body); }
.footerNote { margin: 0; color: var(--fm-muted); font: 400 0.85rem/1.6 var(--font-body); }
.footerLink { color: var(--fm-accent); }
```

- [ ] **Step 4: 전체 검증**

Run: `pnpm test:frontend`
Expected: PASS — 신규 **5개** 파일의 테스트와 기존 테스트 전부

Run: `pnpm build`
Expected: 성공

Run: `pnpm dev` → `http://localhost:5173/?facemarket=1`

최종 확인 항목:
1. 상단바 세 항목이 각각 `#licensing` · `#register` · `#model-info` 로 스크롤한다.
2. 비로그인 상태에서 nav 를 눌러도 로그인 모달이 **뜨지 않는다**.
3. 페이지 어디에서도 가로 스크롤바가 생기지 않는다(320px 폭까지).
4. `http://localhost:5173/` (facemarket 아님) → `/create/input`. 셀러 앱 회귀 없음.
5. 콘솔 에러·경고 없음.
6. 캐러셀이 Task 5 의 육안 항목 10개를 여전히 만족한다.

감사 후 추가된 필수 항목 — 자동 테스트가 못 잡는 자리다:

7. **OAuth 왕복.** 랜딩 CTA → 로그인 → `/` 복귀에서 세션이 붙는다. (`FacemarketRoot` 가
   첫 렌더에 이동하면 여기서 죽는다.)
8. **데스크톱 마우스로 옆 카드를 클릭하면 그 카드로 이동한다.** 터치만 확인하면 못 잡는다.
9. **바깥 카드의 기울기 방향이 원본과 좌우 같다.** 부호를 뒤집어도 배율 테스트는 통과한다.
10. `prefers-reduced-motion: reduce` 를 켠 상태에서 캐러셀도 **앵커 스크롤도** 즉시 확정된다.
11. 드래그로 넘긴 직후 Enter/Space 가 삼켜지지 않는다.

- [ ] **Step 5: 커밋한다**

```bash
git add src/features/facemarket-landing/
git commit -m "feat(facemarket): 랜딩 모델 정보 섹션과 푸터"
```

---

## Self-Review

**스펙 커버리지**

| 스펙 항목 | 태스크 |
|---|---|
| 배치 `src/features/facemarket-landing/` | 1~8 |
| `/` 라우팅, `RootRedirect` 분기 제거 | 4 |
| 캐러셀 CSS 3D, 좌표 변환 | 1·2·3 |
| rAF 감쇠, ref 직접 write | 3 |
| 포인터·키보드 입력 | 3 |
| 히어로 | 5 |
| 캐러셀 섹션 + 예시 고지 | 5 |
| 라이선싱 카드 6장 + 검증 가능한 기록 | 6 |
| 모델 등록 7단계 레일 + 상태별 CTA | 7 |
| 모델 정보(하드룰 7개) | 8 |
| 푸터 | 8 |
| 테스트 (순수함수) | 1·2·4·7 (+ 계획 밖 `facemarket-landing-models.test.mjs`) |
| 신규 의존성 0 | Global Constraints |
| 확인 방법 `?facemarket=1` | 4·5·6·7·8 |

**스펙과 어긋난 곳 하나 — 의도적**

스펙은 `getJobSettlement` 를 "처음으로 화면에 붙인다"고 썼다. 이 계획은 라이선싱 섹션에 **정산 설명 칸**을 넣되 실제 API 호출은 하지 않는다. 랜딩 방문자는 미등록 상태라 조회할 `jobId` 가 없고, 로그인한 모델에게도 랜딩은 설명 화면이지 조회 화면이 아니다. 실제 정산 영수증 UI 는 `/model` 허브나 라이선스 화면에 붙는 게 맞고, 그건 이 랜딩의 범위 밖이다. 계획 승인 시 이 축소를 함께 승인하는 것으로 본다.

> **(2026-09-01 갱신)** 이 축소는 그대로 유효하고, 이제 스펙 문서에도 §범위>제외 로 반영돼
> 있다. `LicensingSection.jsx` 는 `@/lib/brandUseCategories.js` 외에 어떤 API 도 import 하지
> 않는다. **랜딩에 정산 조회를 다시 붙이지 마라 — 없는 회귀다.**

## 계획과 구현이 갈라진 곳 (감사 후 갱신)

구현 뒤 다섯 라운드의 감사가 돌았다. 갈라진 자리는 전부 해당 Task 에 `[구현과 다름]`
블록으로 표시해 뒀고, 여기 목록이 색인이다. **원인의 절반은 앞 라운드의 수리가 만든 새
결함이다** — 특히 카피를 사실에 맞게 낮추는 과정에서 다른 문장이 새로 어긋났고, 뒤로 갈수록
"옳은 수리 둘이 같은 커밋에서 서로를 상쇄하는" 종류가 늘었다. 되풀이 금지 함정의 전체
목록은 스펙 문서 §감사에서 나온 것에 있다(열하나).

| 자리 | 계획 | 구현 | 왜 |
|---|---|---|---|
| Task 4 Step 5 `FacemarketRoot` | 첫 렌더에 `<Navigate>`, 렌더에서 플래그 삭제 | `!loading && session` 게이트, 삭제는 커밋 뒤 effect | **critical.** 첫 렌더 이동이 OAuth `?code=` 를 지운다(effect 는 자식→부모). 렌더 삭제는 StrictMode 에서 플래그를 잃는다 |
| Task 4 Step 3 `facemarketRootTarget` | 절대경로 + `//` 만 검사 | 역슬래시·인코딩 슬래시·제어문자·길이 + `/model`·`/verify` 화이트리스트 | 플래그를 심는 쪽에 도메인 가드가 없다 — 통과시키면 등록 도메인에 셀러 스튜디오가 열린다 |
| Task 1·3 `rebaseTarget` | 정의·테스트·호출 | **미채택**, 잔재까지 제거 | 렌더 위치는 리베이스되지 않아 화면이 700칸을 실제로 훑는다 |
| Task 2 `cardTransform` | 부호 그대로 | `y`·`rotationZ` 부호 반전 + `-0.0000` 정규화 | three(+y 위) ↔ CSS(+y 아래) 손대칭. 배율 테스트는 통과하면서 부채꼴만 거울이 된다 |
| Task 3 Step 2 포인터 캡처 | `pointerdown` | `pointermove`(가로 의도 확정 시) | 캡처가 `click` 타깃을 `.stage` 로 올려 **데스크톱 카드 클릭이 죽는다**(터치는 살아서 놓치기 쉽다) |
| Task 3 Step 2 `consumeDragClick` | 무인자, boolean 표식 | `(event)` 를 받고 표식은 시각 + 500ms 만료 | 키보드 활성화(`detail === 0`)를 통과시키고, click 이 안 오는 `pointercancel` 경로에서 표식이 굳지 않게 |
| Task 3 Step 2 속도 dt 하한 | 1/60 | 1/240 + 100ms idle 시 속도 0 | 120Hz 기기에서 플릭이 절반으로 측정된다 |
| Task 3 Step 3 rAF 루프 | 무조건 계속 | 도착하면 정지, `target`·드래그·모션설정 변화로만 기상 | 아무도 안 만지는 랜딩에서 프레임을 영원히 태운다. **그 대가로 `target` 은 state 로 남아야 한다** |
| Task 3 Step 3 reduced-motion | `DAMP_REDUCED = 24` | 그 프레임에 즉시 확정 | lambda 24 로도 원근 회전이 160ms 재생된다 |
| Task 3 Step 3 포커스 | 없음 | 로빙 탭인덱스 + 포커스 이동, 포커스 쥔 카드는 안 숨김 | 숨기는 순간 포커스가 body 로 회수돼 방향키가 죽는다 |
| Task 6 라이선싱 카피 | 계획 문장 그대로 | 구현 눈금으로 하향 | 채널 조건 없음 / 유효기간 90일·1년·2년 / "체인 앵커"→"체인 기록" / 공개 검증은 DB 조회 / 정산 단위는 컷이 아니라 **잡 1건** |
| Task 8 모델 정보 카피 | 하드룰 직역 | 예외 두 개를 문장에 명시 | 대표 이미지는 presigned 공개 주소이고, 공개 검증에는 마스킹 이름·나이가 함께 뜬다 |
| Task 8 철회 경로 | `/model/withdraw` 를 탈출구로 안내 | **라이선스 해지(`/model/license`)까지만 약속** | **critical.** `ModelWithdraw.jsx` 는 개인화 도메인 전용이라 FaceMarket 모델에게는 빈 화면이고 withdraw API 도 404 다. 등록 파기 진입점이 레포에 없다. (단어가 '폐기'에서 '해지'로 바뀐 사유는 아래 마감 라운드 표) |
| Task 8 푸터 | "법적 고지, 문의" | 걸 링크가 **아직 없다**는 사실까지만 | 레포에 그 페이지가 없고 서버 `NOTICE_URIS` 도 플레이스홀더다 — 없는 주소를 지어 걸지 않는다. **부재를 단정하지도 않는다**(아래 마감 라운드 표) |
| File Structure **수정** | `src/App.jsx` 하나 | `AuthProvider.jsx` · `Login.jsx` · `shell.jsx` · `index.html` | 랜딩이 로그인 복귀 계약을 넘겨받으면 플래그를 심고 지우는 반대편도 같이 봐야 한다 |
| File Structure **신규**의 소유 구조 | `FacemarketLanding.jsx` = "섹션 조립" | head 교체 · 상태별 CTA 조회 · 보류 클릭까지 여기 | CTA 핸들러가 **넷**(상단바·히어로·등록·모델 정보)에 공유돼야 해서 한 곳으로 모였다. CSS 도 섹션별로 안 갈리고 `FacemarketLanding.module.css` 한 벌을 7개 파일이 공유한다 |
| File Structure **수정**의 `Login.jsx` | 계획에 없음 | 취소/성공 분리 + 세션 도착 시 자동 닫기 + **`IS_FACEMARKET` 카피 분기 세 곳** | 랜딩이 복귀 계약을 넘겨받으니 플래그를 심고 지우는 반대편이 따라왔고, 랜딩의 유일한 전환 버튼이 여는 첫 화면이라 카피까지 도메인을 가르게 됐다 |
| 테스트 | 4개 | 5개 | `facemarket-landing-models.test.mjs` — 이미지 목록이 순수 상수라 하드룰 1 에 런타임 검증이 없다 |

**4라운드(마감)에서 더 갈라진 곳** — 전부 **앞 라운드의 옳은 수리가 다른 옳은 수리를
잡아먹은** 종류다. 한쪽을 되돌려 풀지 말고 범위·조건으로 풀어라.

| 자리 | 앞 라운드 | 마감 라운드 | 왜 |
|---|---|---|---|
| 히어로 CTA 의 `loading` 처리 | 스펙이 `disabled` + `aria-busy` 를 요구 | **잠그지 않는다.** 보류함 `pendingPrimary` 에 담았다가 `loading` 이 내려가면 한 번 실행 | **major.** `disabled` 를 걸면 그 클릭이 DOM 에 도달하지 않아 보류함이 통째로 죽은 코드가 된다. **이 자리에서 두 라운드가 반대 방향으로 왕복했다** — 스펙 §1 히어로의 경고 블록이 정본이다 |
| `FacemarketRoot` 의 플래그 정리 | `settled` 되면 무조건 삭제 | `settled && target` + **값 대조** 후 삭제 | **major.** effect 는 자식→부모 순이라, 부트스트랩 중 눌린 CTA 가 심은 복귀 목표를 부모가 나중에 지운다. 로그인 후 등록이 아니라 랜딩으로 되돌아온다 |
| 포인터 캡처 지연의 뒷정리 | 캡처만 `pointermove` 로 미룸 | 사장 상태 무장 해제 가드를 추가 | **major.** 캡처 전 구간에서 스테이지 밖 릴리스가 나면 `pointer.current.id` 가 남아 **맨 호버가 드래그**가 된다. 캡처를 `pointerdown` 으로 되돌리는 건 해법이 아니다(데스크톱 클릭이 다시 죽는다) |
| 로그인 모달 카피 | 셀러 스튜디오 문구 그대로 | `IS_FACEMARKET` 분기 | **major.** 랜딩의 유일한 전환 버튼이 여는 첫 화면이 셀러 제품 설명이면 이 랜딩의 존재 이유가 CTA 한 번에 무너진다 |
| 섹션 카피의 하한 | 사실성 감사로 계속 하향 | 참인 편익은 **복원** | **major(과잉수정).** 편익 문장이 한 줄도 안 남고 마지막 CTA 뒤에 부정문만 이어지는 상태가 됐다. 생체정보를 넘기라고 설득하는 페이지에서 설득이 사라진 것도 회귀다 — **상한은 코드, 하한은 목적** |
| 스펙의 캐러셀 재렌더 서술 | "활성 인덱스가 바뀔 때만 재렌더" | idle 에서만 참, 드래그 중에는 프레임마다 재렌더 | minor. 이 문장을 믿고 `target` 을 ref 로 옮기면 wake 이펙트가 안 발화해 캐러셀이 통째로 멈춘다 |

**마감 라운드에서 더 갈라진 곳**

| 자리 | 앞 라운드 | 마감 라운드 | 왜 |
|---|---|---|---|
| 보류함 `pendingPrimary` | `useRef` | **`useState` + 라벨 '확인 중이에요…'** | 눌린 사실이 화면에 하나도 안 남아 "고장 난 버튼"으로 읽히고 연타를 불렀다. 대기 표시는 **라벨로만** 준다 — `disabled`·`aria-busy` 는 여전히 금지(보류함이 죽는다) |
| 포인터 무장 해제 | 탈출구 하나(`buttons === 0` 가드) | **탈출구 셋** — 가드 + `window` 릴리스 리스너 + `onLostPointerCapture`, 전부 `resetPointer` 로 수렴 | 캡처를 8px 뒤로 미룬 대가. 중복 도착은 `releasePointer` 첫 줄의 `pointerId` 대조가 막는다 — **그 줄을 지우면 한 번의 릴리스가 `snapTarget` 을 두 번 먹인다** |
| `document.title` 복원 | 언마운트에서 무조건 복원 | **`IS_FACEMARKET` 이면 복원하지 않음** | 랜딩을 떠나는 순간 모델 전용 도메인 전체가 셀러 제목이 된다 — 등록 7단계 내내, 북마크까지. 복원의 근거가 이 도메인에선 성립하지 않는다(catch-all·TopNav 가 셀러 화면을 막는다) |
| 라이선스 해지의 어휘 | 랜딩만 '폐기' | **'해지'로 통일** | 제품 화면 버튼·확인창·검증 페이지가 전부 '해지'다. PRD 는 '폐기'를 쓰지만 사용자 대면 문자열이 아니다 — 랜딩만 다르면 모델이 그 버튼을 못 찾는다 |
| 푸터 약관 문구 | "방침·약관이 **없다**" 고 공표 | "공개 문서가 준비되면 **여기에 겁니다**" + 로그인 모달의 facemarket 분기를 같은 말로 | 두 클릭 거리의 모달이 "서비스 약관에 동의한 것으로 간주"라 두 화면이 서로를 부정했다. **한쪽만 고치지 마라** |
| 푸터의 예시 이미지 고지 | 푸터에도 한 벌 | **삭제(셋째 사본)** | 남은 둘 — 캐러셀 상단 고지 + 카드 안 '예시' 배지 — 이 정본이고 PRD §13-5 를 지킨다. 푸터 사본은 캐러셀과 한 화면에 있지도 않았다. **남은 둘은 지우지 마라** |
| 체인 기록 카피 | "사용 1건이 생길 때마다 남습니다" | 상한형 — "올리기가 실패한 건은 기록이 남지 않습니다" | `record_license_settlement` 는 best-effort 다. 실패하면 체인에도 `fm_settlements` 에도 행이 없고, 호출부는 과금·종결 **뒤에** 부르며 예외를 삼킨다 |
| `sessionStorage` 하드닝 | facemarket 쪽만 try/catch | **`AuthProvider` 의 `readPostLogin`/`forgetPostLogin` 로 단일화**(ai·facemarket 둘 다) | 반쪽 하드닝이 매출 도는 셀러 도메인 루트만 흰 화면으로 만들었다(렌더 중 접근 + 레포에 `ErrorBoundary` 0개). 키의 주인은 `AuthProvider` 하나다 |

**아직 안 닫힌 것 (이 브랜치 밖)**

- **호스트별 공유 미리보기.** `og:*` 는 들어갔지만 **두 도메인 공통(중립)** 값이다.
  facemarket 링크를 공유하면 모델용 문구가 아니라 중립 문구가 나간다. 정적 head 는 빌드
  산출물이 한 벌이라 도메인으로 못 가른다(위 `index.html` 블록의 (a)+(b) 또는 Edge
  Middleware 가 필요하다). 정식 1200×630 OG 이미지도 아직 없어 256×256 브랜드 마크를
  쓰는 중이다 — `public/assets/brand/temp-nav-logo.png` 를 지우면 썸네일이 깨진다.
- **FaceMarket 등록 파기 진입점.** `ModelWithdraw.jsx` 는 개인화 도메인 전용이고
  (`personalization_profiles` 행이 없으면 빈 화면 + withdraw API 404), facemarket 라우트에도
  등록 철회·삭제가 없다 — 있는 건 `/licenses/{id}/revoke` 와 진행 중 등록 `cancel` 뿐이다.
  **랜딩에 `/model/withdraw` 를 다시 적지 마라.**
- **모델용 사용 내역·정산 화면**, **법무 문서(방침·약관·사업자 정보)**.

**알려진 제약 (구현 중 확인할 것)**

- `public/models` 이미지가 **360×450** 이다. 데스크톱 최대 카드 크기가 약 230×322 CSS px 이라 1x 에서는 충분하지만 2x DPR 화면에서는 살짝 무를 수 있다. Task 5 육안 확인에서 판단하고, 문제가 되면 이미지 재생성은 별건으로 뺀다.
- Cormorant 는 라틴 전용이라 대형 세리프 헤드라인이 영문이다. 한글 헤드라인을 원하면 Pretendard 로 내려야 하고 에디토리얼 인상이 약해진다. Task 5 에서 실물을 보고 조정한다.
- ~~섹션 카피는 이 계획의 문장을 그대로 쓴다.~~ **결과적으로 그렇게 되지 않았다.** 카피가
  구현보다 세게 말하는 자리가 여러 곳에서 나와 전부 하향됐다(위 표). 이 계획서의 카피
  블록은 참고용이고, 정본은 각 섹션 파일의 헤더 주석이다 — 거기 "왜 이 문장을 못 세게
  쓰는지"의 코드 근거가 적혀 있다.
