# 작업지시서: FaceMarket 홈 랜딩 하단 개편 (2026-09-14)

작성 Claude. 실행 Codex. 워크트리 `.worktrees/facemarket-landing-v2`, 브랜치 `codex/facemarket-landing-v2`(origin/main 5c36c17e 기준).

## 0. 제약
- 커밋 금지. 완료 후 Claude 가 diff 리뷰, 테스트, 스크린샷을 하고 커밋한다.
- **홈(`/`)만 바꾼다.** `src/features/facemarket-landing/FacemarketLanding.jsx` 와 그 섹션, `FacemarketLanding.module.css` 만. 지원 페이지, 모델 리스트, 등록 위저드, 상단바(LandingShell), 히어로(HeroSection), 캐러셀 스테이지(CarouselStage, useCarouselController, landingModels.js), 푸터는 건드리지 않는다.
- 시안 정본: `mockups/facemarket_trust_20260913/landing_v2.html`(워크트리 안에 복사해 둠, 이미지는 같은 폴더 `img/`). 레이아웃, 간격, 글자 크기, 문구를 이 파일대로 옮긴다. 문구는 아래 §2에 그대로 적었으니 HTML 을 파싱할 필요는 없다.
- CSS 는 `FacemarketLanding.module.css` 에 CSS Modules 로, 색은 `--fm-ink`, `--fm-muted`, `--fm-line`, `--fm-accent` 토큰만. hex 하드코딩 금지. 본문 폰트는 `var(--font-body)`, 세리프 제목은 히어로 제목이 쓰는 기존 클래스(.heroTitle)의 폰트 설정을 재사용.
- 인라인 스타일 금지. 이미지에는 `loading="lazy"` 와 alt.
- 줄표(—) 금지, "A → B" 표기 금지, 해요체. 답 맨 앞에 실행 모델명.
- 요청받지 않은 리팩터링 금지. 바뀐 줄이 전부 아래 항목으로 추적돼야 한다. 다만 이번 변경으로 안 쓰이게 된 클래스(.rail*, .faqItem 등)와 컴포넌트 코드는 지운다. `.trustPills` 는 계속 쓰이므로 남긴다.

## 1. 페이지 구성 (위에서 아래로)
1. 상단바, 히어로: 그대로.
2. 캐러셀 스테이지, "위 이미지는 전부 가상 모델 예시입니다" 안내, 신뢰 pill 3개(`.trustPills`): **전부 그대로 둔다.** (2026-09-14 오너 수정: pill 삭제 취소)
3. **새 섹션 FoundingSection** (파운딩 모델 7명).
4. **새 섹션 RightsSection** (초상 원칙 5문장).
5. **새 섹션 DemoSection** (등록한 얼굴로 이런 착용컷을 만들어요).
6. **HowItWorksSection 전면 교체** (3단계).
7. **FaqSection 전면 교체** (6문답, 접이식 아님).
8. **새 섹션 ClosingSection** (마무리 CTA).
9. 푸터: 그대로.

첫 화면 그리드(`.screen`: 히어로, 스테이지, 메타 바)는 유지하고, 3~8 은 그 아래 `<main>` 흐름에 순서대로 둔다. 각 섹션의 세로 여백은 시안 값(대략 64~80px, `clamp` 로 폰 축소)을 따른다.

## 2. 섹션별 명세와 문구 (문구는 글자 그대로)

### 2.1 FoundingSection
- 데이터는 `src/features/facemarket-landing/data/foundingModels.js` 에 상수로 둔다. 오너가 이미지와 숫자를 손으로 바꿀 수 있어야 한다.
  ```js
  export const FOUNDING_TOTAL = 7;
  export const FOUNDING_FILLED = [
    '/models/founding/mosaic1.webp', '/models/founding/mosaic2.webp', '/models/founding/mosaic3.webp',
  ];  // 2026-09-14 오너 확정: 등록 완료 3명
  ```
  이미지는 `public/models/founding/` 에 이미 넣어 뒀다(mosaic4 는 안 쓴다, 지워도 된다).
- 제목(h2): `첫 파운딩 모델 7명을 모집하고 있어요` 에서 "파운딩 모델 7명" 만 `--fm-accent` 색. 숫자 7은 `FOUNDING_TOTAL` 에서.
- 제목 아래 한 줄(muted 14px): `지원 이후, 등록까지 완료된 모델 기준입니다.`
- 오른쪽 끝(제목과 같은 줄, 아래 정렬): 큰 숫자 `4자리` + 작은 글씨 `남았어요`. 4 = TOTAL - FILLED.length.
- 칸 7개, 가로 한 줄(`grid-template-columns: repeat(7, 1fr)`, gap 12px, 4:5 비율, radius 14px).
  - 채워진 칸: 이미지 cover + 왼쪽 아래 흰 알약 `등록 완료`(11.5px 600).
  - 첫 빈 칸: 점선 테두리와 글자를 `--fm-accent`, 옅은 파랑 배경, 글자 `지원하기`. **버튼이다.** 누르면 상단 CTA 와 같은 동작(`onPrimary`, 즉 registerCta 가 보내는 곳). FacemarketLanding.jsx 가 LandingShell 에서 받는 `onPrimary` 를 내려준다.
  - 나머지 빈 칸: 점선 테두리, 가운데 번호(5, 6, 7) 회색. 첫 빈 칸(4번째)이 '지원하기' 버튼이다.
- 폰(≤767px): 칸을 4열로 감싸고 gap 8px, 오른쪽 숫자는 제목 아래로 내려온다.
- 서버 호출 없음. 정적.

### 2.2 RightsSection
- `<ol>` 다섯 항목, 위쪽 1px 잉크 선, 항목 사이 `--fm-line` 선, 각 항목 padding 30px 0.
- 각 항목은 2열(`minmax(0,1.15fr) minmax(0,1fr)`, gap 32px, baseline 정렬): 왼쪽 h3(34px 600, letter-spacing -.025em, `text-wrap: balance`), 오른쪽 p(16px/1.7 muted). 폰에서는 1열.
- h3 안의 강조 단어만 `--fm-accent` 색(굵기는 같음).
  1. h3 `내 얼굴을 **어떤 옷에 쓸지**, 내가 정해요.` / p `일반 의류는 기본으로 허용되고, 속옷이나 수영복 같은 품목은 내가 켜기 전엔 절대 쓰이지 않아요. 정한 범위 밖의 요청은 시스템이 막아요.`
  2. h3 `쓰일 때마다 결제 금액의 **70%**가 내 몫이에요.` / p `셀러가 착용컷 한 장에 14,900원을 내면 10,430원이 내 몫으로 쌓여요. 10,000원이 넘으면 매월 10일에 보내드려요.`
     숫자는 하드코딩하지 말고 `facemarketPricing.js` 의 `FACEMARKET_PRICING.perCut`, `facemarketTerms.js` 의 `MODEL_SHARE`, `MIN_PAYOUT_KRW`, `SETTLEMENT_DAY`, `formatKrw` 로 만든다(ApplyStartPage.jsx 가 같은 방식).
  3. h3 `어디에 쓰였는지 **전부** 볼 수 있어요.` / p `어느 셀러가 언제 어떤 상품에 썼는지, 완성된 착용컷과 라이선스 번호까지 마이페이지에서 확인해요.`
  4. h3 `착용컷마다 **위조할 수 없는 기록**이 남아요.` / p `만들어진 이미지에는 출처 서명(C2PA)이 들어가고, 정산 내역은 블록체인에 기록돼요. 누구나 라이선스 번호로 진짜인지 확인할 수 있어요.`
  5. h3 `언제든 **철회**할 수 있어요.` / p `이유 없이, 위약금 없이요. 철회하면 새 사용이 바로 멈추고, 얼굴 정보는 30일 안에 지워져요.`

### 2.3 DemoSection
- 가운데 정렬 h2(30px 600): `등록한 얼굴로 이런 착용컷을 만들어요`. 설명 문장 없음. "가상", "예시" 같은 말 없음.
- 한 줄: 얼굴 1장(300px, 4:5, radius 18px, 그림자 `0 12px 34px rgba(16,21,26,.12)` 는 토큰이 없으면 이 값 그대로 CSS 변수로 모듈 안에 정의) + 화살표(→, 26px) + 착용컷 3장(각 236px, 4:5, radius 16px, gap 12px). 전체 가운데 정렬.
- 이미지 경로는 `src/features/facemarket-landing/data/demoCuts.js` 상수:
  ```js
  export const DEMO_CUTS = ['/models/demo/cut1.webp', '/models/demo/cut2.webp'];
  ```
  실제 구현은 오너가 제공한 착용컷 두 장을 사용하며, 얼굴 입력 이미지는 표시하지 않는다.
- alt: 얼굴 `등록 사진`, 착용컷 `착용컷 1` 처럼 번호만.
- 폰: 얼굴을 위에(가로 60%), 화살표는 아래 방향(↓), 착용컷 3장은 가로 한 줄.

### 2.4 HowItWorksSection (교체)
- eyebrow(13px muted 500): `지원부터 등록까지`
- h2(40px 600, letter-spacing -.03em): `사진 18장으로 내 모델을 만들어요`
- 3열 그리드, 위쪽 1px 잉크 선, 열 사이 `--fm-line` 세로선. 각 열: 번호(13px muted) `01`/`02`/`03`, h3(26px 600) `지원`/`검토`/`등록`, 시간 알약(옅은 파랑 배경 `rgba(45,99,255,.08)` 은 `color-mix(in srgb, var(--fm-accent) 8%, transparent)` 로, 글자 `--fm-accent` 13px 600) `지원서 약 3분`/`24시간 이내`/`촬영 약 15분`, 본문(15.5px/1.7 muted):
  1. `기본 정보와 심사용 프로필을 제출해요. 모델 등록에는 별도로 사진 18장이 필요해요.`
  2. `사람이 직접 보고 이메일로 결과를 알려드려요. 마이페이지에서도 지금 상태를 볼 수 있어요.`
  3. `본인확인 후 사진 18장을 올리고, 사용 조건을 정해 증서를 발급받아요. 이후 테스트컷을 직접 확인·확정한 뒤 모델이 공개돼요.`
  "24시간 이내" 는 `facemarketTerms.js` 의 `REVIEW_SLA_LABEL` 을 쓴다.
- 아래 한 줄(15px muted, 앞 구절만 잉크 600): `**승인은 그대로 남아요.** 등록을 하다 멈춰도 지원서를 다시 낼 필요 없이 이어서 하면 돼요.`
- 폰: 1열, 세로선 대신 항목 사이 가로선.
- 기존 5칸 `.rail` 과 관련 CSS, STEPS 상수 삭제. 파일 머리말 주석의 "아직 코드에 없는 절차" 경고는 이제 사실이 아니므로 지우고, 한 줄로 "지원 절차는 /apply 와 서버에 구현돼 있다" 로 바꾼다.

### 2.5 FaqSection (교체)
- 머리: 2열(`1fr 2fr`, gap 40px). 왼쪽 h2(40px 600) `궁금한 것들`. 오른쪽 p(15px/1.7 muted): `여기 없는 질문은 지원 페이지의 자주 묻는 질문에 더 있어요. 계약 조건은 초상 라이선스 계약서와 동의서 원문에서 그대로 읽을 수 있어요.` 에서 "초상 라이선스 계약서" 는 `/license-agreement` 링크, "동의서" 는 동의서 문서 경로가 앱에 있으면 그 경로, 없으면 `/license-agreement` 로 같이 건다(있는지 확인해서 보고서에 적을 것). 링크는 잉크색 밑줄(`text-underline-offset: 3px`).
- 문답 6개, 2열 그리드(gap 0 48px), 위쪽 1px 잉크 선, 각 항목 padding 24px 0 과 아래 `--fm-line` 선. **`<details>` 를 쓰지 않는다. 답이 항상 보인다.** h3(18px 600) + p(15px/1.75 muted, 강조는 잉크 600).
  1. `제가 돈 쓰는 부분은 없나요?` / `**없어요.** 지원과 등록은 무료이고, 증서 발급료 20,000원도 지금은 무료예요. 화면에 보이는 14,900원과 49,900원은 셀러가 내는 금액이에요.` (20,000원은 `LICENSE_ISSUE_FEE_KRW`, 14,900/49,900 은 pricing 상수)
  2. `얼마를, 언제 받나요?` / `셀러가 낸 금액의 **70%**예요. 한 건이면 10,430원이고, 쌓인 몫이 10,000원을 넘으면 매월 10일에 등록한 계좌로 보내드려요.` (전부 상수)
  3. `모델 경력이 없어도 되나요?` / `네. 경력, 포트폴리오, SNS 는 선택이에요. 만 19세 이상이고, 소속 에이전시가 없고, 본인이 직접 지원하면 돼요.`
  4. `내 얼굴은 어디에 쓰이나요?` / `스튜디오 배경의 의류 착용컷과 그 상품의 상세페이지에만요. 광고, 인쇄물, 영상, 다른 사람과의 합성에는 쓰이지 않아요.`
  5. `등록할 때 무엇이 필요한가요?` / `본인확인과 등록 사진 18장이 필요해요. 밝은 야외에서 도와줄 사람과 함께 약 15분 동안 촬영해요.`
  6. `조건은 나중에 바꿀 수 있나요?` / `마이페이지에서 언제든요. 바뀐 조건은 그다음 사용 건부터 적용되고, 이미 만들어진 건은 그대로예요.`
- 폰: 1열.
- 기존 ITEMS 8개, `.faqList`/`.faqItem`/`.faqQuestion`/`.faqAnswer` CSS 삭제(다른 곳에서 안 쓰면).

### 2.6 ClosingSection (새로)
- 위쪽 `--fm-line` 선, 가운데 정렬, padding 64px 0 72px.
- 제목: 히어로와 같은 세리프 스타일로 한 줄 `create your own digital DNA`, "digital DNA" 만 이탤릭 500. `lang="en"`. 히어로 제목 클래스를 재사용하되 크기만 40px.
- 버튼: 상단 CTA 와 같은 라벨과 동작(`primaryLabel`, `onPrimary`).
- 아래 한 줄(13px muted): `등록 사진 18장, 지금은 발급료 무료`

## 3. 테스트
- `tests/frontend/facemarket-landing-home.test.mjs` 를 새로 만든다(기존 랜딩 테스트 파일이 있으면 거기에 추가):
  1. 홈 트리에 `.trustPills` 가 그대로 있다(3개).
  2. FoundingSection 이 칸 7개를 그리고, 그중 채워진 칸이 `FOUNDING_FILLED.length` 개, 남은 자리 숫자가 `FOUNDING_TOTAL - FOUNDING_FILLED.length` 이다. "지원하기" 칸을 클릭하면 `onPrimary` 가 불린다.
  3. RightsSection 문장 5개의 h3 텍스트가 §2.2 와 같다.
  4. HowItWorks 에 `지원서 약 3분`, `REVIEW_SLA_LABEL`, `촬영 약 15분` 알약이 있고 `.rail` 이 없다.
  5. FAQ 문답 6개가 `<details>` 없이 렌더되고 질문 텍스트가 §2.5 와 같다.
  6. RightsSection 과 FAQ 의 금액이 pricing 상수에서 나온다(`FACEMARKET_PRICING.perCut` 을 바꿔도 문장이 따라오는지).
- 기존 테스트 중 옛 HowItWorks, FAQ 문구를 참조하는 것이 있으면 새 문구로 맞춘다.
- 통과 기준: `pnpm test:frontend` 전부 통과, `npx vite build` 성공. 샌드박스에서 포트를 못 열면 스크린샷은 Claude 가 찍는다.

## 4. 보고서
`documents/facemarket_landing_v2_impl_report_20260914.md`: 바뀐 파일과 이유, 지운 CSS 클래스 목록, 동의서 경로 확인 결과, 테스트 숫자, 확신이 없는 것.
