# 셀러 빛을 팔레트 C로 통일하고 랜딩의 마지막 그림을 앱 첫 그림에 잇는다

상태: Accepted
결정일: 2026-10-15, 오너 결정
정본: `.scratch/palette-c-20261015/OWNER_DECISIONS_1015.md`, 승인 시안 `mockups/landing_palette_c_20261015/orb-c.css`, 같은 폴더의 `orb-runtime.js`, `handoff.css`, `handoff.js`, `app.html`, `app-phone.html`

## 배경과 근거

랜딩과 셀러 앱이 다른 배경 색, 크기, 회전 시계를 쓰면 랜딩에서 시작하기를 누른 순간 오브가 다른 자리로 뛴다. 팔레트 C 감사에서 확인한 마지막 자세와 앱 첫 자세를 제품 코드에 함께 옮긴다.

- `mockups/palette_c_audit_20261015/landing/REPORT.md:22`는 전환 13경우의 위치, 크기, 세기가 일치함을 확인했다. 실제 도메인 간 첫 그림 시간은 아직 확인하지 못했다.
- `create-flow/REPORT.md:27`은 어두운 막 아래 따뜻한 색 픽셀이 18%였고, 뿌리를 .22로 낮춘 뒤 .4%로 줄었음을 기록했다. 같은 보고서 31줄은 생성 시작의 흰 판이 오브를 덮는 문제다.
- `account/REPORT.md:29`은 요금제 체크와 칩의 무채색 값을 제시했다. 31줄은 휴대폰 오브 위 회색 글자 대비 저하, 36줄은 휴대폰 목록 .62, 37줄은 크레딧 행 사이 빛 줄무늬의 근거다.
- `editor-accents/REPORT.md:9`은 공용 빛 토큰을 바꾸면 FaceMarket과 공용 토스트도 함께 바뀌는 경계를 지적했다. 구슬은 오너가 별도로 고른 `.scratch/palette-c-20261015/sky-lilac-orb.css`를 따른다.

감사 초안은 흰 면 테두리에 더스티 블루 유지를 권했지만 오너가 파스텔 테두리를 골랐다. 초안의 추천보다 2026-10-15 오너 결정을 우선한다.

## 결정

### 1. 셀러 빛과 기존 공용 빛을 분리한다

새 원색은 `--wl-sky #8FC6F6`, `--wl-lav #CFB8F4`, `--wl-peach #FFCDB2`, `--wl-mint #C6ECD6`, `--wl-peri #9FB6FF`, `--wl-white #FFFFFF`다. `src/styles/wl-light.css`에 모으고 `src/apps/seller/main.jsx`에서만 읽는다.

ChromeLayout은 `.wl-aurora`의 왼쪽과 오른쪽 리본 각 세 개, `.wl-orb-root > .wl-orb` 안의 세 겹과 흰 하이라이트를 그린다. 리본 자리값은 `shared-markup.mjs`의 LEFT와 RIGHT를 리본별 CSS 클래스에 옮긴다. React 인라인 style은 추가하지 않는다.

PC 중심 높이는 `clamp(170px, 23vh, 250px)`, 배율 1.14, 뿌리 .64다. 768px 미만에서는 `clamp(210px, 28vh, 260px)`, 1.2, .82다. 지름과 흐림, 색 구간, 겹 불투명도와 시작 각도는 승인 시안 그대로다. 세 겹 모두 16.2초에 같은 방향으로 돈다.

오로라는 .52이며 휴대폰에서는 숨긴다. 헤더 하단선 61px 아래 통과율 .12부터 10vh 동안 smoothstep으로 1이 된다. 통과율 `--wl-mask-pass`는 등록한 숫자 속성이다.

`/library`, `/credits/history`, `/subscription` 계열의 휴대폰 뿌리만 .62다. PC는 .64를 유지한다. 모달이나 콘티 전환 막이 열리면 오브 뿌리와 오로라는 200ms 동안 .22로 내려간다. 조건은 React의 `data-glow`와 CSS `:has()`로 표현한다. 크레딧 행은 하나의 흰 판 안에 `--ring` 1px 선으로 나누고 `.route-loading` 배경은 투명하게 한다.

### 2. 문서가 바뀌어도 같은 벽시계를 쓴다

`seller.html` head 맨 앞에서 2026-01-01T00:00Z부터 `performance.timeOrigin`까지의 초를 `--wl-t`로 설정한다. 오브 delay는 `calc(var(--wl-t) * -1s)`, 리본 delay는 `calc(var(--aurora-delay) - var(--wl-t) * 1s)`다.

배경 마운트의 `useLayoutEffect`와 bfcache 복귀에서 레이어 애니메이션의 `currentTime`을 `Date.now() - performance.timeOrigin`으로 맞춘다. 동작 줄이기에서는 오브와 리본을 애니메이션하지 않는다.

### 3. 랜딩 도착은 페인트 전에 연결한다

head 스크립트는 `from=landing`을 읽어 `html[data-entry="landing"]`을 붙이고 `history.replaceState`로 `from`만 지운다. 경로, 나머지 쿼리의 인코딩과 순서, 해시와 history state는 유지한다. OAuth나 요금제의 다른 매개변수를 소비하지 않는다.

`#root` 앞의 `#seller-boot-bg`는 ChromeLayout과 같은 마크업, 같은 클래스를 쓰는 정적 사본이며 `aria-hidden`이다. 랜딩 도착에서만 보이고, Vite가 head에 만드는 셀러 CSS link로 앱 JS 실행 전에 그린다. 마크업 바로 뒤 동기 스크립트가 부트 레이어의 `currentTime`도 현재 벽시계로 맞춰 CSS를 늦게 받아도 앱 교대 순간 위상이 뛰지 않게 한다. ChromeLayout은 layout effect에서 사본을 지우고 위상을 맞춘다. App의 layout effect도 사본을 지워 에디터, 공개 확인, 인증 콜백처럼 크롬 밖에서 첫 렌더가 끝나는 경우를 처리한다. 두 배경이 한 프레임도 함께 그려지지 않아야 한다.

부트에는 내비가 없으므로 마스크 통과율을 1로 유지한다. 내비 마운트 후에는 40ms 지연, 320ms, `cubic-bezier(.22, 1, .36, 1)`로 내비가 나타나며 마스크 통과율도 1에서 .12로 간다. 본문은 기존 `fxPageIn`을 한 번만 쓴다. 약 600ms 뒤 진입 속성을 지우며 React의 `page-enter` 클래스는 직접 고치지 않는다. 동작 줄이기에서는 이동 없이 100ms 투명도만 쓴다.

### 4. 오너가 고른 액센트를 셀러에만 적용한다

`sellerDesign.css`의 `:root`에서 `--link`를 토스 메인 파랑 `#3182F6`으로 덮어쓴다. 셀러의 기존 `#0099ff`, `rgba(0,153,255,a)`, `#6bb6ff` 사본은 `--link`와 `color-mix`로 바꾼다. 진행 파랑, 선택 하늘선, 성공과 오류, 포커스, 구글 로고, 셀러가 고르는 콘텐츠 색은 유지한다.

`--ring-flow-1`부터 `6`까지는 `#8FC6F6`, `#CFB8F4`, `#FFCDB2`, `#C6ECD6`, `#9FB6FF`, `#B9D9F7`이다. 검정 버튼과 흰 알림, AI 칩, 새 결과 등 셀러의 모든 기존 소비처가 같은 파스텔 테두리를 쓴다. 요금제 체크는 검정 원과 흰 체크, 증정 칩은 `--bg-2` 면, `--fg-1` 글자, `--ring-strong` 테두리다.

오브 위 맨 부제, 404와 빈 화면 설명, 눈썹 글자는 새 `--fg-on-glow #5C5C5C`다. 앱 전체 `--fg-2` 변경은 기본값에 포함하지 않고 비교용 한 줄 주석만 남긴다.

에디터 구슬 안은 하늘 `#B5DDFF`, 연보라 `#DBCAFE`, 살구 `#FEDCC3`, 민트 `#D7EEDC`의 새 `--canvas-ai-*` 토큰을 쓴다. 구조, 12초와 17초 역회전, 캡슐과 기존 고리는 유지한다. 동작 줄이기에서 구슬도 멈춘다.

### 5. FaceMarket은 별도다

공용 `tokens.css`의 기존 값과 `app.css`의 `.orb-bg` 규칙은 바꾸지 않는다. FaceMarket `HeroSection.jsx`의 기존 오브는 그대로다. 셀러 덮어쓰기 파일은 FaceMarket 진입 그래프에 들어가면 안 된다. 공유 컴포넌트는 `--link`를 읽어 각 앱의 값을 사용하고, 새 회색 토큰은 공용 회색으로 되돌아갈 수 있게 한다. 셀러에서 안 쓰게 된 `.edge-glow` CSS만 제거한다.

## 두 저장소 계약

앱 저장소와 랜딩 저장소는 원색, 겹 크기와 흐림, 색 구간과 시작 각도, 16.2초 주기, 리본 자리와 주기, 벽시계 epoch, 앱 자세, 오로라 세기, 도착 시간을 같은 값으로 유지한다. 앱의 계약 사본은 `src/styles/wl-light.css` v1.0.0이다. 런타임 구현은 각 프레임워크에 맞게 감싸되 계산식은 같다. 셀러 전용 import 경계가 바이트 단위 공유 파일보다 우선한다.

- 일반 시작 주소는 `https://ai.wearless.kr/?from=landing`이며 기존 RootRedirect를 거친다.
- 요금제 주소는 `https://ai.wearless.kr/pricing?plan=...&from=landing`이다. 앱은 plan을 보존한다.
- 전환 마지막 그림과 첫 그림은 가로 중심, 세로 중심, 크기, blur, 색 구간, 뿌리와 겹의 실효 불투명도, 회전 위상, 오로라 세기가 같아야 한다. 내비 없는 이음매에서는 마스크 통과율이 1이다.
- 값이나 계산식을 바꿀 때 두 저장소 PR을 함께 리뷰하고 상호 참조한다. **앱을 먼저 배포하고 랜딩을 뒤에 배포한다.** 앱 도착 처리가 준비되기 전에 랜딩이 새 전환을 보내면 안 된다.
- 한쪽 배포만 되돌릴 때도 짝의 값과 도착 표식 호환성을 확인한다. 이번 계약은 `from=landing`이며 초안의 별도 버전 쿼리는 도입하지 않는다.

## 검증과 한계

`tests/frontend/palette-c-handoff.test.mjs`는 head 스크립트를 실행해 표식, 쿼리와 해시 보존, 위상을 확인하고 부트와 React 배경 구조를 비교한다. 기존 bundle-separation과 host-document-split 검사로 셀러와 FaceMarket 경계를 함께 지킨다. `pnpm test:frontend`와 `pnpm build`를 실행한다.

작업지시서에 따라 Codex는 브라우저를 실행하지 않는다. Claude가 실제 두 앱 사이 전환, 부트와 React 교대 프레임, Chromium과 WebKit, 휴대폰 320·360·375·390·414px의 어절과 가로 넘침, 모달 감쇠와 에디터 동작 줄이기를 확인한다.

오너가 고른 `#3182F6`은 흰색 대비 약 3.7:1로 작은 글자 AA 4.5:1에 못 미친다. 흰 면 파스텔 테두리는 1.3~2.0:1이다. 선택과 초점 선을 대신하지 않으며, 두 선택 모두 감사 추천의 차이를 알고 정한 오너 결정으로 기록한다. 팔레트와 무관한 아이콘, 문구, 빈 보관함 그림, 토스트 위치와 크레딧 PC 정렬은 별도 PR 범위다.
