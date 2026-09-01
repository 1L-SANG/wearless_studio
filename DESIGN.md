# Wearless — design.md

> 이 파일은 Wearless 프로젝트의 **단일 디자인 기준**이다.
> 에이전트·개발자 모두 UI를 만들 때 이 파일을 먼저 읽고 따른다.
>
> **토큰 정본: `src/styles/tokens.css`.** 이 문서와 그 파일이 어긋나면 파일이 이긴다.
> 2026-09-01 갱신 — 실제 코드와 대조해 글로우 4색·CTA·glass·폰트를 정정하고,
> FaceMarket 화면군에서 굳은 패턴을 §13 으로 추가했다.

---

## 0. 핵심 모델 — 읽기 전에 반드시 이해할 것

Wearless는 **Cal.com의 흑백 시스템을 100% 채택**하고, 단 하나만 추가한다:
**부드러운 배경 글로우 (aurora + orb).**

글로우를 "흑백 화면 속 유일한 색 사건"으로 정의하기 때문에, **나머지가 100% 절제돼 있어야**
이 모델이 성립한다. 화면이 컬러풀하게 느껴지면 무조건 잘못된 것이다.

---

## 1. 색 시스템

### 기본 잉크 & 서피스

| 토큰 | 값 | 용도 |
|---|---|---|
| `--fg-1` | `#0e0d14` | 제목·primary 텍스트 |
| `--fg-2` | `#898989` | 보조 텍스트·라벨·설명 |
| `--fg-3` | `#b4b4b4` | faint·비활성·플레이스홀더 |
| `--bg-1` | `#ffffff` | 기본 캔버스·카드 서피스 |
| `--bg-2` | `#f5f5f5` | 섹션 구분용 미묘한 배경 |
| `--ink-overlay` | `rgba(17,17,17,0.5)` | 스크림·오버레이 |
| `--ring` | `rgba(34,42,53,0.08)` | 링 섀도우 (CSS border 대신) |
| `--ring-strong` | `rgba(34,42,53,0.12)` | 강조가 필요한 링 |

### CTA — 별도 토큰 계열을 갖는다

CTA 는 `--fg-1` 이 아니라 **자기 토큰**을 쓴다. 따뜻한 근사-검정 + 완전 pill 이고,
호버가 **투명도가 아니라 색**으로 간다.

| 토큰 | 값 | 비고 |
|---|---|---|
| `--cta-bg` | `#2C2C2C` | 따뜻한 근사-검정 (Cal 의 순검정을 살짝 덥힌 값) |
| `--cta-bg-hover` | `#1B1B1B` | 호버 — 더 진하게, 150ms |
| `--cta-fg` | `#ffffff` | |
| `--cta-height` | `44px` | 최소 터치 높이 |
| `--cta-weight` | `540` | |
| `--cta-radius` | `--r-pill` | 완전 pill |
| `--cta-transition` | `150ms` | |
| `--cta-focus-ring` | `0 0 0 2px #fff, 0 0 0 4px var(--cta-bg)` | 흰 간격을 둔 더블 링 |

### 액센트 색 (순백 화면 전용)

> **글로우가 있는 화면에서는 아래 모든 액센트 금지. 순백 화면에서만.**

| 토큰 | 값 | 용도 |
|---|---|---|
| `--link` | `#0099ff` | 인텍스트 하이퍼링크 **전용** |
| `--accent-error` | `#d92d20` | 폼 에러·파괴적 액션 |
| `--accent-error-ring` | `rgba(217,45,32,0.22)` | 에러 인풋 ring |
| `--accent-success` | `#067647` | 성공·확인 상태 |
| `--accent-success-ring` | `rgba(6,118,71,0.20)` | 성공 ring |
| `--focus` | `rgba(59,130,246,0.5)` | 키보드 포커스 링 (a11y 전용) |

**에러/성공 허용 용도:** 텍스트·아이콘·ring
**에러/성공 금지 용도:** 대형 fill, pill 배경, 글로우 화면

> 예전 문서에 있던 `--sky` 는 전역 토큰이 아니다. 스토리보드 화면이 자기 스코프에서
> `--sb-accent: #8fbfee` 로 갖고 있을 뿐이다. 전역 액센트로 끌어 쓰지 않는다.

### 글로우 전용 토큰 (UI 사용 완전 금지)

현재 팔레트는 **블루–라벤더 계열**이다. 예전 문서의 파스텔 4색(하늘/세이지/썬/모브)은
더 이상 코드에 없다.

| 토큰 | 값 | 성격 |
|---|---|---|
| `--glow-sky` | `#7fd0f0` | 밝은 블루 |
| `--glow-sage` | `#b9a5e8` | 라벤더 |
| `--glow-sun` | `#e3ddf3` | 연보라 힌트 |
| `--glow-mauve` | `#5fa7dd` | 딥 블루 |

**절대 버튼·텍스트·아이콘·테두리·카드에 쓰지 않는다.** aurora/orb 자산 내부에서만 산다.

### 색 예산 규칙 — 가장 중요한 디시플린

```
글로우 있는 화면 → UI는 무채색만 (#0e0d14, #898989, #ffffff, CTA #2C2C2C)
글로우 없는 순백 화면 → 액센트 허용
둘 다 풀로 쓰지 않는다. 절제가 무너지면 시스템 전체가 무너진다.
```

---

## 2. 타이포그래피

### 폰트 패밀리

| 토큰 | 패밀리 | 용도 |
|---|---|---|
| `--font-display` | Cal Sans → system-ui | 헤딩·디스플레이 |
| `--font-body` | Pretendard Variable → Inter → system-ui | 본문·UI 전체 |
| `--font-mono` | Roboto Mono → ui-monospace | 코드·수치 |
| `--font-serif` | Cormorant → Georgia | **에디터 텍스트 도구 전용** (Latin only) |
| `--font-soft` | Gowun Dodum → Pretendard | **에디터 텍스트 도구 전용** (한글 전체) |

**Cal Sans는 절대 본문에 쓰지 않는다. Pretendard는 절대 헤딩에 쓰지 않는다.**

serif·soft 두 종은 **셀러가 상세페이지에서 고르는 옵션**이지 UI 폰트가 아니다.
둘 다 단일 굵기라(Cal Sans 와 같은 상황) 굵기를 올리면 브라우저 합성 볼드가 걸린다.

Pretendard·Gowun Dodum·Cormorant 는 self-host(woff2). Cal Sans 만 Google Fonts.

### 타입 위계

| 클래스 | 패밀리 | 크기 | 굵기 | 행간 | 자간 |
|---|---|---|---|---|---|
| `.display-hero` | Cal Sans | 64px | 600 | 1.10 | 0 |
| `.h1` | Cal Sans | 48px | 600 | 1.10 | 0 |
| `.h2` | Cal Sans | 24px | 600 | 1.30 | 0 |
| `.h3` | Cal Sans | 20px | 600 | 1.20 | +0.2px |
| `.card-title` | Cal Sans | 16px | 600 | 1.10 | +0.2px |
| `.eyebrow` | Cal Sans | 12px | 600 | 1.50 | +0.2px |
| `.lead` | Pretendard | 18px | 300 | 1.50 | -0.2px |
| `.body` | Pretendard | 16px | 400 | 1.50 | -0.1px |
| `.caption` | Pretendard | 14px | 400 | 1.45 | -0.1px |
| `.ui-label` | Pretendard | 15px | 500 | 1.0 | 0 |
| `.micro` | Pretendard | 12px | 500 | 1.0 | 0 |
| `.code` | Roboto Mono | 14px | 500 | 1.0 | 0 |

**Cal Sans 24px 미만은 반드시 +0.2px 자간.** 작은 크기에서 자간이 없으면 글자가 뭉친다.

숫자가 열로 정렬되는 곳(가격·수치 표)은 `font-variant-numeric: tabular-nums`.

---

## 3. 스페이싱

8px 기본 단위. 28px에서 80px으로의 의도적인 점프가 섹션 리듬을 만든다.

`--sp-1·2·3·4·6·8·12·16·20·24·28` 그리고 섹션 단위:

| 토큰 | 값 |
|---|---|
| `--sp-section` | 80px (데스크탑) |
| `--sp-section-lg` | 96px (넉넉) |
| `--sp-section-mobile` | 48px |

섹션 간격은 절대 48px 미만으로 줄이지 않는다. 여백이 프리미엄함을 만든다.

---

## 4. 보더 라디우스

| 토큰 | 값 | 용도 |
|---|---|---|
| `--r-1` | 2px | 인라인 소형 |
| `--r-2` | 4px | 소형 UI |
| `--r-3` | 6px | 버튼·이미지 |
| `--r-4` | 8px | **기본 인터랙티브** |
| `--r-5` | 12px | **카드** |
| `--r-6` | 16px | 대형 컨테이너 |
| `--r-7` | 29px | 특수 |
| `--r-8` | 100px | 거의 원형 |
| `--r-pill` | 9999px | **뱃지·태그·CTA** |

---

## 5. 그림자 & 깊이

**CSS `border`는 쓰지 않는다.** 모든 경계는 ring-shadow로 표현한다.

| 토큰 | 용도 |
|---|---|
| `--elev-inset` | 눌린/recessed 요소, 인풋 안쪽 |
| `--elev-card` | **카드 기본 (워크호스)** — ring + diffuse + contact 3레이어 |
| `--elev-card-alt` | ring 없는 카드 변형 |
| `--elev-btn-hi` | 버튼 상단 하이라이트 |
| `--elev-soft` | 미묘한 ambient |
| `--elev-lift` | 호버·메뉴·플로팅 |

```css
--elev-card:
  0 1px 5px -4px rgba(19,19,22,0.7),   /* contact */
  0 0 0 1px rgba(34,42,53,0.08),        /* ring (= border 역할) */
  0 4px 8px 0 rgba(34,42,53,0.05);      /* diffuse */
```

그림자는 항상 미묘하다. 진하고 무거운 그림자는 이 시스템에 없다.

---

## 6. 글로우 시스템

| 자산 | 허용 위치 | 금지 위치 |
|---|---|---|
| `aurora.html` | 마케팅 히어로 (1곳) | 그 외 전부 |
| `orb.html` | 사인인 셸·빈 상태·로딩·auth (최대 1곳/화면) | 본문·카드·폼·표·설정·데이터 뒤 |

**자산 코드는 verbatim.** 색·blur·opacity·애니메이션 수치 변경 금지.
**한 화면에 글로우 존은 최대 1개.**

### 글로우 위에 UI가 올 때: white glass plate

```css
background: var(--glass-bg);        /* rgba(255,255,255,0.92) */
backdrop-filter: var(--glass-blur); /* blur(8px) */
-webkit-backdrop-filter: blur(8px);
border-radius: var(--r-6);
box-shadow: var(--elev-lift);
```

글로우 위 텍스트는 항상 glass plate 위 또는 충분한 흰 여백 위에. **글로우 위 직접 텍스트 금지.**

---

## 7. 컴포넌트 가이드

### 버튼

```css
/* Primary CTA */
background: var(--cta-bg);          /* #2C2C2C */
color: var(--cta-fg);
min-height: var(--cta-height);      /* 44px */
border-radius: var(--cta-radius);   /* pill */
font-weight: var(--cta-weight);     /* 540 */
transition: background var(--cta-transition);
/* hover: background → var(--cta-bg-hover) — 투명도가 아니라 색 */
/* focus-visible: box-shadow: var(--cta-focus-ring) */

/* Ghost */
background: #ffffff;
color: var(--fg-1);
box-shadow: var(--elev-card);
/* hover: box-shadow → var(--elev-lift) */
```

- CTA는 항상 근사-검정. 글로우 화면에서도 동일
- **컬러 버튼 없음.** 액센트를 버튼 fill 에 쓰지 않는다

### 카드

```css
background: #ffffff;
border-radius: var(--r-5);
box-shadow: var(--elev-card);
padding: 18px 20px;
/* hover: box-shadow → var(--elev-lift); transform: translateY(-2px) */
```

### 인풋 / 필드

```css
background: #ffffff;
border: none;
border-radius: var(--r-4);
padding: 11px 14px;
box-shadow: var(--elev-card);

/* 에러 */ box-shadow: 0 0 0 1px var(--accent-error), 0 0 0 4px var(--accent-error-ring);
/* 포커스 */ outline: 2px solid var(--focus); outline-offset: 1px;
/* recessed well */ box-shadow: var(--elev-inset), 0 0 0 1px var(--ring); background: var(--bg-2);
```

### 뱃지 / pill

```css
/* Solid */ background: var(--fg-1); color: #fff; border-radius: var(--r-pill);
/* Soft   */ background: var(--bg-2); color: var(--fg-1); box-shadow: 0 0 0 1px var(--ring);
```

상태는 색이 아니라 도트(`●`)나 토글로 표현. **컬러 pill 없음.**

### 선택 칩 (chips)

체형·용도·카테고리 같은 다중/단일 선택에 쓴다. 기본은 soft pill, 선택되면 `.on` 으로 반전.
사진이 붙으면 세로 카드가 된다(§13.3).

---

## 8. 레이아웃

- 최대 너비: `--container` 1200px, 가운데 정렬
- 위저드형 화면은 `.wizard`(960px) / `.wizard.narrow`
- 섹션 간격: 80–96px (모바일 48px)
- 상단 nav: sticky + backdrop blur
- 브레이크포인트: 640 / 768 / 1024 / 1200px

---

## 9. 아이코노그래피

- **시스템:** Lucide (stroke 1.5–2px, rounded caps, no fill)
- **색:** `currentColor` → 항상 무채색. 컬러 아이콘 없음
- **크기:** 16 / 20 / 24px
- **Emoji 절대 금지**

---

## 10. 복사 (Copy) 가이드

- **톤:** 평이하고 단호하게. 동사 위주. 느낌표 금지
- **케이스:** sentence case
- **길이:** 헤드라인 2–6단어. 서브카피 1–2문장. 버튼 1–2단어
- **금지 단어:** seamless, effortless, revolutionary, powerful, robust, innovative
- **한국어 규칙 (2026-08-31 정리분):**
  - 개발자 언어를 화면에 노출하지 않는다 — `자산`·`private`·`토큰`·`결속` 대신
    `이미지`·`비공개`·`본인 확인 정보`·`이번 등록에만 쓰이는`
  - 자화자찬 수식어 금지 — "안전한 모델 등록" 이 아니라 "모델 등록"
  - 행동을 정확히 지시한다 — "이 화면을 유지해 주세요"(모호) ✗ / "이 화면을 닫지 말아 주세요" ✓
  - 용어를 한 화면 안에서 통일한다 — `본인 확인` 으로 모으고 `신원 확인`·`생체 확인` 은 쓰지 않는다
  - **예외: 법정 고지·동의문.** `생체정보 처리 동의` 같은 문구는 동의 버전 계약이라
    표기(띄어쓰기) 외에는 건드리지 않는다

---

## 11. 애니메이션

- **글로우 자산:** aurora 19s drift · orb 12–17s rotation — **값 변경 금지**
- **UI 전환:** 짧고 subtle. `opacity`·`box-shadow`·`transform` 위주, 150–200ms
- **금지:** 바운스, springy overshoot, 무한 장식 루프
- `prefers-reduced-motion` 존중

---

## 12. 체크리스트

```
□ 헤딩 Cal Sans / 본문 Pretendard (절대 안 바뀜)
□ Cal Sans 24px 미만 → +0.2px 자간
□ 팔레트 무채색 (글로우 화면 기준)
□ CTA 는 --cta-* 토큰. 호버는 색 변경(투명도 아님)
□ CSS border 없이 ring-shadow
□ 카드에 --elev-card 3레이어
□ 섹션 간격 80px 이상 (모바일 48px)
□ 글로우 자산 verbatim · 화면당 1개 이하
□ 글로우 위 UI 는 glass plate 위
□ 컬러 버튼 없음 · Lucide stroke 아이콘 · Emoji 없음
□ 에러/성공 색은 순백 화면에서 텍스트·아이콘·ring 으로만
```

---

## 13. FaceMarket 화면 패턴 (2026-09 추가)

모델 등록·라이선스 화면군에서 굳은 패턴이다. 제품 정의는 `documents/FACEMARKET_PRD.md`.

### 13.1 진행 레일

7단계 순차 KYC 라 현재 위치가 늘 보여야 한다. 번호 마커가 **장식이 아니라 정보**다
(`.rail` / `.railStep` / `rail_done|active|todo`). 완료 단계는 체크 아이콘으로 바뀐다.
`aria-current="step"` 을 붙인다.

### 13.2 사진 슬롯

- 업로드 영역은 `width:100%` + `aspect-ratio: 4/5`
- **예시 사진과 내 사진이 한 카드에 같이 있다.** 예시에는 좌하단 "예시" 뱃지를 반드시 단다 —
  이 구분이 사라지면 사용자가 남의 사진을 자기 것으로 오해한다
- 예시는 실사진 우선, 없으면 라인 일러스트로 폴백(`onError`)
- 상태 라벨: `사진 필요 → 대기 중 → 검사 중 → 확인 완료`

### 13.3 사진 선택 카드 (체형)

- 카드 = 세로형 chip. 사진(4:5) 위, 라벨 아래
- 폭 106px 이상. 그보다 좁으면 체형 차이가 보이지 않는다
- **2축 매트릭스는 행마다 가로 스크롤.** 세로 그리드로 4열을 깔면 좁은 화면에서 카드가
  80px 로 쪼그라든다. 행 제목이 한 축(볼륨)을, 카드 라벨이 다른 축(실루엣)을 맡는다
- 사진 파일이 없는 항목은 자동으로 텍스트 칩으로 남는다 — 섞여 있어도 깨지지 않는다

### 13.4 대기·진행 표현

콜드부트 2분·자산 생성 수 분이 **정상 경로**에 있다. 대기는 예외가 아니라 화면의 일부다.

- 얼마나 걸리는지 숫자로 말한다 — "최대 3분 걸릴 수 있어요"
- 지금 무엇을 하는 중인지 말한다 — "발급 준비 중"(서버 깨우는 중) → "발급 진행 중"
- 사용자가 할 일을 정확히 말한다 — "이 화면을 닫지 말아 주세요"
- 자동 재시도는 조용히 한다. 원시 에러 토스트를 던지지 않는다

### 13.5 되돌아가기

선택 단계(체형·대표이미지)에는 **'이전' 링크**를 둔다. 주 동작보다 약하게(밑줄 텍스트).
파괴적 확정 전에는 프리뷰 + 명시적 확인을 받는다 — 고르는 즉시 업로드하고 다음으로
넘어가면 되돌릴 방법이 없다.

### 13.6 얼굴 표시 규칙 (하드룰)

- 얼굴은 **공개 URL 을 갖지 않는다.** Bearer fetch → objectURL → `<img>`
- objectURL 은 교체·삭제·언마운트에서 `revokeObjectURL`
- **공개 검증 페이지(`/verify/:id`)에는 생체정보를 한 픽셀도 그리지 않는다.** 무인증이라
  거기 그린 건 전부 공개된다

---

## 14. 자산 경로

```
src/styles/tokens.css        — 토큰 정본 + 시맨틱 타입 클래스
src/styles/app.css           — 전역 레이아웃·유틸리티
src/fonts/                   — Pretendard / Gowun Dodum / Cormorant (self-host)
src/features/*/*.module.css  — 화면별 스코프 스타일
public/models/pose/          — 등록 각도 예시 사진 (front·angle45·side)
public/models/physique/      — 체형 선택 사진 ({gender}/{value}.webp)
```
