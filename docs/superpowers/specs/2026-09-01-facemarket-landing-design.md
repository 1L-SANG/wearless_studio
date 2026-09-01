# FaceMarket 랜딩페이지 — 설계

날짜: 2026-09-01
상태: 사용자 리뷰 대기

## 목표

`facemarket.wearless.kr` 루트에 **모델을 대상으로 하는 랜딩페이지**를 만든다. 지금 이 도메인의
루트는 랜딩 없이 곧장 `/model/register` 로 리다이렉트된다(`src/App.jsx:512,520`) — 처음 온
사람이 "이게 뭐고 내 얼굴이 어떻게 되는지" 알기 전에 로그인 벽과 7단계 KYC 위저드를 맞는다.

디자인 원본은 `~/Documents/Codex/2026-09-01/new-chat/outputs/spotlight-webgl-gallery`
(spotlight WebGL 갤러리 프로토타입)이고, 라이선싱 섹션의 정보 구조는 Mirror Mirror AI 의
라이선싱 페이지를 참조한다.

## 성공 기준

- facemarket 도메인 루트가 랜딩을 띄운다. 로그인 여부와 무관하다.
- spotlight 원본의 오목 아크 캐러셀(가운데 카드가 가장 멀고 양끝이 카메라로 밀려나오는)이
  육안으로 같은 인상으로 재현된다.
- 드래그·스와이프·좌우 화살표 키·이전/다음 버튼·점 선택이 모두 동작하고 무한 루프한다.
- 상단바 세 항목(라이선싱 · 모델 등록 · 모델 정보)이 같은 페이지 안 섹션으로 스크롤한다.
  비로그인 방문자가 눌러도 로그인 모달이 뜨지 않는다.
- 신규 npm 의존성이 0개다. `pnpm build` 가 통과한다.
- 캐러셀 이미지가 실제 등록 모델이 아니라는 표기가 화면에 있다.
- `prefers-reduced-motion: reduce` 에서 관성 없이 즉시 스냅한다.

## 범위

### 포함

- 랜딩 라우트 1개와 그 안의 섹션 6개(히어로·캐러셀·라이선싱·모델 등록·모델 정보·푸터).
- spotlight 캐러셀의 CSS 3D 재구현.
- 캐러셀 수학(`carouselMath`·`sceneLayout`) 의 JSX 이식과 그 순수함수 테스트.
- `getJobSettlement` 를 처음으로 화면에 노출(라이선싱 섹션의 정산 카드).

### 제외

- three.js · @react-three/fiber · React 19 · TypeScript 도입.
- `/model/license` 발급 폼, 등록 7단계 위저드, `/model` 허브의 UI 수정.
- 셀러 카탈로그(`listModels`) 실데이터 연동. 캐러셀은 정적 가상모델 이미지만 쓴다.
- 셀러 대상 카피. 셀러는 `ai.wearless.kr` 를 그대로 쓴다.
- 카드에 붙는 메타데이터 설계. 이번엔 번호만 붙이고, 무엇을 어떻게 붙일지는 **사용자가 따로
  지시한다**. 임의로 이름·연도·평점을 지어내지 않는다.

## 결정과 근거

### 왜 CSS 3D 인가

원본은 React 19 + `@react-three/fiber` v9 인데 v9 는 React 19 를 요구한다. 메인 앱은 React
18.3 이고, 올리려면 `react-moveable`(에디터)·`@aws-amplify/ui-react-liveness`(생체등록)·
`@tanstack/react-query` 를 전부 재검증해야 한다. 랜딩 하나 때문에 제품 전체를 흔드는 거래다.

동시에, 원본 셰이더(`ArtworkCardMesh.tsx`)가 하는 일을 뜯어보면 **둥근 모서리 마스크와
opacity 곱하기**뿐이다. 그림자는 별도 plane 에 블러 텍스처, 라벨은 캔버스에 그린 글자.
WebGL 이 아니면 못 하는 연산이 하나도 없다. `layoutForOffset` 이 뱉는 `{x, z, rotationY}` 도
CSS 3D 변환에 1:1로 대응한다.

CSS 3D 가 오히려 나은 지점도 있다. 캔버스 텍스처로 굽던 카드 라벨이 진짜 DOM 텍스트가 되어
선명하고, 스크린리더와 검색엔진이 읽고, 폰트 로딩 타이밍에 안 흔들린다.

### 왜 상단바가 섹션 앵커인가

`/model/license` · `/model/register` · `/model` 세 라우트가 전부 인증 필요다
(`src/App.jsx:592-623`). 랜딩 방문자는 정의상 아직 등록 안 한 사람이라, 상단바를 실제 라우트에
걸면 첫 클릭이 곧바로 로그인 모달이다. 설명을 읽기 전에 가입을 요구하는 순서가 된다.

### 왜 "모델 정보"가 프라이버시 섹션인가

방문자는 미등록 모델이다. 이 사람이 등록 전에 가장 알고 싶은 건 자기 상태가 아니라 **내 얼굴이
어떻게 취급되는지**다. PRD 프라이버시 하드룰 7개(`documents/FACEMARKET_PRD.md` §10)는 내부
개발 규칙이지만, 모델 입장에서는 그대로 신뢰 근거다.

## 아키텍처

### 배치

```
src/features/facemarket-landing/
  FacemarketLanding.jsx      섹션 조립 + 상단바
  LandingHeader.jsx          brand · 앵커 3개 · 로그인/CTA
  sections/
    HeroSection.jsx
    LicensingSection.jsx     Mirror Mirror 구조 (카드 6장 + 검증 가능한 기록)
    RegisterSection.jsx      7단계 레일 미리보기 + 상태별 CTA
    ModelInfoSection.jsx     프라이버시 하드룰 → 모델 언어로
    FooterSection.jsx
  carousel/
    CarouselStage.jsx        DOM 렌더 + rAF 스무딩
    carouselMath.js          원본 이식 (순수)
    sceneLayout.js           원본 이식 (순수)
    useCarouselController.js 원본 이식 (포인터·키보드)
  data/landingModels.js      public/models 경로 목록
  FacemarketLanding.module.css
```

원본의 `GalleryStage` · `ArtworkScene` · `ArtworkCardMesh` · `CssGalleryFallback` 네 파일은
`CarouselStage.jsx` 하나로 합쳐진다. WebGL 이 없으니 WebGL 감지·에러 바운더리·CSS 폴백 전환이
전부 불필요하다.

### 라우팅

랜딩은 **`ChromeLayout` 밖의 독립 surface** 다. 기존 `TopNav`(`src/features/shell/shell.jsx`)는
크레딧 배지·요금제 배지·플로우 스테퍼 같은 셀러 스튜디오 물건을 달고 있어서, 랜딩에 재사용하면
숨김 분기만 늘어난다. `verify/:licenseId` 와 `editor/:id` 가 이미 chrome 밖 라우트라 선례가 있다.

```
App.jsx
  // 신규 — facemarket 호스트에서만
  <Route path="/" element={<FacemarketLanding />} />
  // 제거 — RootRedirect 의 facemarket 분기 (512행 target 삼항, 520행 조기 반환)
```

`LoginGate` 는 `AuthProvider` 가 직접 렌더하므로(`src/features/auth/AuthProvider.jsx:109`)
chrome 밖에서도 `openLogin()` 이 그대로 동작한다.

`RootRedirect` 의 나머지(draft 승격·프로젝트 부트스트랩)는 create 플로우 전용이고 facemarket
분기는 이미 그 로직을 타지 않게 조기 반환하고 있다. 그 조기 반환 자리를 라우트 분기로 올리는
것이라 create 플로우 동작은 바뀌지 않는다.

### 캐러셀 — 좌표 변환

원본 카메라는 `fov 24°`, `position z = 8.6`. z=0 평면에서 세로로 보이는 world 높이는

```
visibleHeight = 2 · 8.6 · tan(12°) = 3.656 world units
```

스테이지 DOM 의 실제 픽셀 높이를 `H` 라 하면 변환 계수는 `k = H / 3.656` 하나로 닫힌다.

| three | CSS |
|---|---|
| camera z 8.6 | `perspective: 8.6·k px` (스테이지에 지정) |
| `metrics.cardWidth/Height` | 카드 px 크기 = `cardWidth·k` × `cardHeight·k` |
| `layout.x` | `translateX(x·k px)` |
| `layout.z` (양수 = 카메라 쪽) | `translateZ(z·k px)` |
| `layout.rotationY/Z` | `rotateY(θrad)` `rotateZ(θrad)` |
| `layout.opacity` | `opacity` |
| 셰이더 `uRadius 0.055` | `border-radius: 5.5%` |
| 그림자 plane + 블러 텍스처 | `box-shadow` |
| 캔버스 라벨 텍스처 | 카드 위 `<span>` (번호) |

`metricsForAspect(aspect)` 의 aspect 는 원본에서 three viewport 비율이었다. CSS 에서는
스테이지 엘리먼트의 `clientWidth / clientHeight` 를 `ResizeObserver` 로 관측해 넣는다.
브레이크포인트 세 구간(`<1.1`, `<2.3`, 그 이상)은 원본 값을 그대로 쓴다.

### 캐러셀 — 애니메이션

원본은 `useFrame` 안에서 `MathUtils.damp(current, target, lambda, delta)` 로 감쇠하고 그 값을
React state 에 매 프레임 넣는다. 이건 지수감쇠 한 줄이라 그대로 옮긴다.

```
current += (target - current) · (1 - exp(-lambda · delta))
lambda = reducedMotion ? 24 : isDragging ? 18 : 9
```

다만 매 프레임 `setState` 로 14장을 재렌더하는 대신, `requestAnimationFrame` 루프에서 카드
ref 의 `style.transform` / `style.opacity` 를 직접 쓴다. React 는 활성 인덱스가 실제로 바뀔
때만 재렌더한다(카드 하이라이트·점 표시·"현재 n/14" 표기용).

`prefers-reduced-motion: reduce` 면 lambda 24 로 사실상 즉시 스냅한다. 원본과 같은 동작이다.

### 캐러셀 — 입력

`useCarouselController` 를 JSX 로 그대로 옮긴다. 포인터 캡처, 8px 수평 의도 임계값,
`DRAG_PIXELS_PER_ITEM = 170`, 속도 기반 스냅(`snapTarget`)까지 원본 상수를 유지한다.

`touch-action: pan-y` 를 스테이지에 유지해 세로 스크롤을 뺏지 않는다 — 랜딩은 한 장 스크롤
페이지라 캐러셀이 페이지 스크롤을 삼키면 그 아래 섹션에 도달할 수 없다.

## 섹션 내용

### 1. 히어로

Cormorant 대형 헤드라인 + Pretendard 부제 + 주 CTA 하나. 카피는 "내 얼굴을 내 조건으로
빌려준다"는 모델 관점. 셀러·브랜드 관점 문구는 넣지 않는다.

### 2. 캐러셀 (`#gallery`)

`public/models` 의 가상모델 14장(women `w1`~`w11`, men `m1`~`m3`)을 카드로 돌린다.
카드에는 **번호(01~14)만** 붙는다. 캐러셀 근처에 "예시 이미지 — 실제 등록 모델이 아닙니다"
고지를 상시 노출한다. 이 고지가 없으면 방문자가 이미 등록된 실존 모델 목록으로 읽는다.

`-face` 접미 파일과 `pose/` · `physique/` 는 쓰지 않는다. 등록 위저드 안내용 소재라
랜딩의 편집 톤과 맞지 않는다.

### 3. 라이선싱 (`#licensing`)

Mirror Mirror AI 라이선싱 페이지의 정보 구조를 빌린다: 카드 6장 그리드 + 그 아래 "검증 가능한
기록" 블록. 내용은 우리 것으로 채운다.

카드 6장:

| 카드 | 근거 |
|---|---|
| 사용 조건을 모델이 직접 정한다 | `ModelLicense.jsx` 조건 항목 |
| 허용·금지 카테고리를 명시한다 | `src/lib/brandUseCategories.js` |
| 유효기간이 있다 (90일 / 1년) | `ModelLicense.jsx` `VALIDITY` |
| 서명된 자격증명으로 발급된다 (W3C VC) | PRD §7.2 |
| QR 하나로 누구나 검증한다 | `/verify/:licenseId`, 무인증 |
| 폐기하면 즉시 무효가 된다 | PRD §7.5, 폐기 파이프라인 |

"검증 가능한 기록" 블록은 Mirror Mirror 가 C2PA 로고를 놓은 자리다. 우리는 **OpenDID VC →
OmniOne Chain 앵커 → 공개 검증**의 세 칸 흐름을 그린다. 정산 칸에서 `getJobSettlement`
(`src/lib/api/facemarket.js:173`)를 처음으로 화면에 붙인다 — API 는 있는데 UI 가 없던 것이다.

라이선스 발급 자체는 이 섹션에서 하지 않는다. CTA 가 `/model/license` 로 보낼 뿐이고, 그 폼은
건드리지 않는다.

### 4. 모델 등록 (`#register`)

7단계 레일(`동의 · 신분증 · 사진 · 체형 · 대표 · 라이브 · 완료`)을 미리 보여준다. PRD 리뉴얼
유의점 1번이 "진행 레일은 장식이 아니다" 인데, 랜딩에서 미리 보여주는 것도 같은 이유다 —
순차 KYC 라 몇 단계인지 미리 알면 이탈이 준다. 체형·대표 이미지가 건너뛸 수 있는 선택 단계라는
것도 여기 표기한다.

CTA 문구는 상태에 따라 바뀐다. 비로그인/미등록은 "모델 등록 시작", 등록 진행 중은 "이어서
등록하기", 완료된 모델은 "내 모델 정보". 상태 조회는 `listMyModels` · `getCurrentEnrollment`
로 하되, **조회 실패나 지연이 랜딩 렌더를 막지 않는다** — 기본 문구로 먼저 그리고 결과가 오면
교체한다.

### 5. 모델 정보 (`#model-info`)

PRD 프라이버시 하드룰 7개를 모델이 읽을 언어로 옮긴다.

| 하드룰 | 모델에게 |
|---|---|
| 얼굴은 공개 URL 을 갖지 않는다 | 내 얼굴 사진 주소를 아무나 열 수 없다 |
| 공개 검증 페이지에 생체정보 0픽셀 | QR 을 스캔한 사람은 조건만 보고 내 얼굴은 못 본다 |
| 업로드 사진은 격리 상태로 저장 | 검사를 통과하기 전에는 어디에도 쓰이지 않는다 |
| 신분증 초상은 메모리에만 | 신분증 사진은 저장하지 않는다 |
| CI 는 HMAC 해시로만 저장 | 본인 확인값은 원본이 아니라 해시로만 남는다 |
| QC 로그에 이미지·파일명·랜드마크 금지 | 검사 기록에 내 사진이 남지 않는다 |
| 동의문은 버전 계약 | 동의한 문서가 무엇이었는지 그대로 보존된다 |

여기에 철회 경로(`/model/withdraw`)와 라이선스 폐기 권리를 함께 적는다. 하드룰 7번(동의문
용어는 임의로 못 바꾼다)에 따라 `생체정보 처리 동의` 라는 표현은 그대로 쓴다.

### 6. 푸터

법적 고지, 문의, `ai.wearless.kr`(셀러) 링크.

## 스타일

기존 토큰(`src/styles/tokens.css`)을 베이스로 쓰고, 랜딩 루트에만 스코프 오버라이드를 얹는다.
spotlight 의 `#edf3f8` 배경과 `#2d63ff` 블루는 앱 토큰(`--bg-1: #ffffff`,
`--link: #0099ff`)과 다르므로 `.fm-landing { --page-bg: … }` 처럼 랜딩 안에서만 재정의한다.
앱 전역 토큰은 건드리지 않는다.

폰트는 신규 추가가 없다. `Cormorant`(디스플레이 세리프)와 `Pretendard`(한글 본문)가 이미
셀프호스팅되어 있다(`src/fonts/`). 원본의 DM Sans 자리는 Pretendard 가 받는다 — 한글 카피가
대부분이라 어차피 DM Sans 로는 못 그린다.

CSS Modules 를 쓴다. `ModelLicense.module.css` · `ModelPersonalization.module.css` 처럼
features 폴더의 기존 관례다.

## 테스트

`carouselMath.js` 와 `sceneLayout.js` 는 DOM 없는 순수함수다. 원본의 vitest 테스트
(`carouselMath.test.ts` · `sceneLayout.test.ts`)를 `node:test` 로 옮겨
`tests/frontend/facemarket-landing-carousel.test.mjs` 에 넣고 `pnpm test:frontend` 에 편승한다.
신규 테스트 러너를 들이지 않는다.

덮는 것: 랩 오프셋이 항상 최단 방향인지, 루프 경계(13 → 0)에서 역주행하지 않는지, 속도 스냅이
정수로 떨어지는지, `layoutForOffset` 의 x/z/rotation 이 offset 0에서 정확히 0이고 부호가
대칭인지, `metricsForAspect` 의 세 구간 경계값.

DOM 상호작용(드래그·키보드)은 자동 테스트 없이 브라우저에서 직접 확인한다 — 이 레포의 프런트
테스트는 전부 순수 로직이고 렌더 테스트 인프라가 없다.

## 확인 방법

로컬은 `?facemarket=1` 로 호스트 분기를 강제한다(`src/lib/host.js`). `pnpm dev` 후
`http://localhost:5173/?facemarket=1`.

육안 확인 항목: 오목 아크 형태(가운데가 가장 작고 양끝이 커짐), 드래그 관성과 스냅, 루프 경계
통과, 앵커 3개 스크롤, 좁은 화면에서 카드 1장 중심 + 양옆 살짝, 비로그인 상태에서 로그인 모달이
안 뜨는 것, `prefers-reduced-motion` 켠 상태.

## 열린 항목

- **카드 메타데이터.** 이번엔 번호만. 무엇을 어떻게 붙일지는 사용자 지시 대기.
- **히어로·섹션 카피 최종본.** 구조는 이 문서가 고정하고 문구는 구현 중 조율한다.
