# Wearless — design.md
> 이 파일은 Wearless 프로젝트의 **단일 디자인 기준**이다.  
> 에이전트·개발자 모두 UI를 만들 때 이 파일을 먼저 읽고 따른다.  
> 디자인 시스템 원본: `colors_and_type.css` (토큰 전체)

---

## 0. 핵심 모델 — 읽기 전에 반드시 이해할 것

Wearless는 **Cal.com의 흑백 시스템을 100% 채택**하고, 단 하나만 추가한다:  
**부드러운 배경 글로우 (aurora + orb).**

글로우를 "흑백 사진 속 유일한 색 사건"으로 정의하기 때문에, **나머지가 100% 절제돼 있어야** 이 모델이 성립한다.  
화면이 컬러풀하게 느껴지면 무조건 잘못된 것이다.

---

## 1. 색 시스템

### 기본 잉크 & 서피스

| 토큰 | 값 | 용도 |
|---|---|---|
| `--fg-1` | `#0e0d14` | 제목·버튼·primary 텍스트 |
| `--fg-2` | `#898989` | 보조 텍스트·라벨·설명 |
| `--fg-3` | `#b4b4b4` | faint·비활성·플레이스홀더 |
| `--bg-1` | `#ffffff` | 기본 캔버스·카드 서피스 |
| `--bg-2` | `#f5f5f5` | 섹션 구분용 미묘한 배경 |
| `--ink-overlay` | `rgba(17,17,17,0.5)` | 스크림·오버레이 |
| `--ring` | `rgba(34,42,53,0.08)` | 링 섀도우 (CSS border 대신) |
| `--ring-strong` | `rgba(34,42,53,0.12)` | 강조가 필요한 링 |

### 액센트 색 (순백 화면 전용)

> **글로우가 있는 화면에서는 아래 모든 액센트 금지. 순백 화면에서만.**

| 토큰 | 값 | 용도 |
|---|---|---|
| `--link` | `#0099ff` | 인텍스트 하이퍼링크 전용 |
| `--sky` | `#8fbfee` | ring·선택·활성 강조 (≈ glow-sky 채도 강화) |
| `--accent-error` | `#d92d20` | 폼 에러·파괴적 액션 |
| `--accent-error-ring` | `rgba(217,45,32,0.22)` | 에러 인풋 ring |
| `--accent-success` | `#067647` | 성공·확인 상태 |
| `--accent-success-ring` | `rgba(6,118,71,0.20)` | 성공 ring |
| `--focus` | `rgba(59,130,246,0.5)` | 키보드 포커스 링 (a11y 전용) |

**--sky 허용 용도:** ring·테두리, 선택·활성 상태, 데코 테두리 (소재 칩, 업로드 점선, 선택 ring)  
**--sky 금지 용도:** 버튼 fill, 배경 채움, 타이포그래피 색상

**에러/성공 허용 용도:** 텍스트·아이콘·ring  
**에러/성공 금지 용도:** 대형 fill, pill 배경, 글로우 화면

### 글로우 전용 토큰 (UI 사용 완전 금지)

| 토큰 | 값 | 위치 |
|---|---|---|
| `--glow-sky` | `#c3dcf6` | aurora/orb 자산 내부 전용 |
| `--glow-sage` | `#d8e8c6` | aurora/orb 자산 내부 전용 |
| `--glow-sun` | `#f8d978` | aurora/orb 자산 내부 전용 |
| `--glow-mauve` | `#ead0e3` | aurora/orb 자산 내부 전용 |

**절대 버튼·텍스트·아이콘·테두리·카드에 쓰지 않는다.**

### 색 예산 규칙 — 가장 중요한 디시플린

```
글로우 있는 화면 → UI는 무채색만 (#0e0d14, #898989, #ffffff)
글로우 없는 순백 화면 → 액센트 허용
둘 다 풀로 쓰지 않는다. 절제가 무너지면 시스템 전체가 무너진다.
```

---

## 2. 타이포그래피

### 폰트 패밀리

| 토큰 | 패밀리 | 용도 |
|---|---|---|
| `--font-display` | Cal Sans → system-ui | 헤딩·디스플레이 (24px+) |
| `--font-body` | Pretendard Variable → Inter → system-ui | 본문·UI 전체 |
| `--font-mono` | Roboto Mono → ui-monospace | 코드·기술 텍스트 |

**Cal Sans는 절대 본문에 쓰지 않는다.** Pretendard는 절대 헤딩에 쓰지 않는다.

### 타입 위계

| 클래스 | 패밀리 | 크기 | 굵기 | 행간 | 자간 | 용도 |
|---|---|---|---|---|---|---|
| `.display-hero` | Cal Sans | 64px | 600 | 1.10 | 0 | 마케팅 히어로 |
| `.h1` | Cal Sans | 48px | 600 | 1.10 | 0 | 섹션 헤딩 |
| `.h2` | Cal Sans | 24px | 600 | 1.30 | 0 | 피처 헤딩 |
| `.h3` | Cal Sans | 20px | 600 | 1.20 | +0.2px | 서브 헤딩 |
| `.card-title` | Cal Sans | 16px | 600 | 1.10 | +0.2px | 카드 제목 |
| `.eyebrow` | Cal Sans | 12px | 600 | 1.50 | +0.2px | 소형 라벨 |
| `.lead` | Pretendard | 18px | 300 | 1.50 | -0.2px | 인트로 본문 |
| `.body` | Pretendard | 16px | 400 | 1.50 | -0.1px | 일반 본문 |
| `.caption` | Pretendard | 14px | 400 | 1.45 | -0.1px | 설명·부가 텍스트 |
| `.ui-label` | Pretendard | 15px | 500 | 1.0 | 0 | 버튼·내비 라벨 |
| `.micro` | Pretendard | 12px | 500 | 1.0 | 0 | 최소 UI 텍스트 |
| `.code` | Roboto Mono | 14px | 500 | 1.0 | 0 | 코드 스니펫 |

**Cal Sans 24px 미만은 반드시 +0.2px 자간.** 작은 크기에서 자간이 없으면 글자가 뭉친다.

---

## 3. 스페이싱

8px 기본 단위. 28px에서 80px으로의 의도적인 점프가 섹션 리듬을 만든다.

| 토큰 | 값 | 주요 용도 |
|---|---|---|
| `--sp-4` | 4px | 아이콘–텍스트 간격 |
| `--sp-8` | 8px | 기본 요소 간격 |
| `--sp-12` | 12px | 카드 내부 패딩 (소) |
| `--sp-16` | 16px | 버튼 패딩·기본 간격 |
| `--sp-24` | 24px | 카드 내부 패딩 (대) |
| `--sp-28` | 28px | 컴포넌트 간 최대 간격 |
| `--sp-section` | 80px | 섹션 간격 (데스크탑) |
| `--sp-section-lg` | 96px | 넉넉한 섹션 간격 |
| `--sp-section-mobile` | 48px | 섹션 간격 (모바일) |

섹션 간격은 절대 48px 미만으로 줄이지 않는다. 여백이 프리미엄함을 만든다.

---

## 4. 보더 라디우스

| 토큰 | 값 | 용도 |
|---|---|---|
| `--r-1` | 2px | 인라인 소형 요소 |
| `--r-2` | 4px | 소형 UI |
| `--r-3` | 6px | 버튼·이미지 |
| `--r-4` | 8px | **기본 인터랙티브** (버튼·인풋·이미지) |
| `--r-5` | 12px | **카드** |
| `--r-6` | 16px | 대형 컨테이너·프로미넌트 섹션 |
| `--r-7` | 29px | 특수 라운드 요소 |
| `--r-8` | 100px | 거의 원형 소요소 |
| `--r-pill` | 9999px | **뱃지·태그·pill 전부** |

---

## 5. 그림자 & 깊이

**CSS `border`는 쓰지 않는다.** 모든 경계는 ring-shadow로 표현한다.

| 토큰 | 값 | 용도 |
|---|---|---|
| `--elev-inset` | `inset 0 1px 1.9px 0 rgba(0,0,0,0.16)` | 눌린/recessed 요소, 인풋 |
| `--elev-card` | ring + diffuse + contact 3레이어 | **카드 기본 (워크호스)** |
| `--elev-card-alt` | contact + diffuse (ring 없음) | 링 없는 카드 변형 |
| `--elev-btn-hi` | `inset 0 2px 0 rgba(255,255,255,0.15)` | 버튼 상단 하이라이트 |
| `--elev-soft` | `0 4px 8px 0 rgba(34,42,53,0.05)` | 미묘한 ambient shadow |
| `--elev-lift` | ring + sharp + far diffuse | 호버·메뉴·플로팅 요소 |

**`--elev-card` 전체 값:**
```css
box-shadow:
  0 1px 5px -4px rgba(19,19,22,0.7),   /* contact */
  0 0 0 1px rgba(34,42,53,0.08),         /* ring (= border 역할) */
  0 4px 8px 0 rgba(34,42,53,0.05);       /* diffuse */
```

그림자는 항상 미묘하다. 진하고 무거운 그림자는 이 시스템에 없다.

---

## 6. 글로우 시스템

### 자산 사용 규칙

| 자산 | 허용 위치 | 금지 위치 |
|---|---|---|
| `aurora.html` | 마케팅 히어로 (1곳) | 그 외 전부 |
| `orb.html` | 사인인 셸·빈 상태·로딩·auth (최대 1곳/화면) | 본문·카드·폼·표·설정·데이터 뒤 |

**자산 코드는 verbatim으로 사용한다.** 색·blur·opacity·애니메이션 수치 변경 금지.  
**한 화면에 글로우 존은 최대 1개.**

### 글로우 위에 UI가 올 때: white glass plate

```css
background: rgba(255, 255, 255, 0.82);
backdrop-filter: blur(8px);
-webkit-backdrop-filter: blur(8px);
border-radius: 16px;
box-shadow: 0 0 0 1px rgba(34,42,53,0.08), /* --elev-lift */
            0 2px 8px -4px rgba(19,19,22,0.6),
            0 12px 28px -8px rgba(34,42,53,0.12);
```

글로우 위 텍스트는 항상 glass plate 위 또는 충분한 흰 여백 위에 놓는다. 글로우 위 직접 텍스트 금지.

---

## 7. 컴포넌트 가이드

### 버튼

```css
/* Dark primary — 기본 CTA */
background: #0e0d14;
color: #ffffff;
border-radius: 8px;         /* --r-4 */
padding: 11px 18px;
font-family: Pretendard Variable;
font-weight: 500;
font-size: 15px;
box-shadow: inset 0 2px 0 rgba(255,255,255,0.15);  /* --elev-btn-hi */
/* hover: opacity 0.82 */

/* Ghost */
background: #ffffff;
color: #0e0d14;
box-shadow: var(--elev-card);
/* hover: box-shadow → var(--elev-lift) */

/* Pill */
border-radius: 9999px;       /* --r-pill */
/* 나머지는 dark primary와 동일 */
```

- CTA는 항상 검정(`#0e0d14`). 글로우 화면에서도 검정.
- 컬러 버튼 없음. `--sky`나 `--accent-*`를 버튼 fill에 쓰지 않는다.
- hover = opacity 0.82. press = translateY(0.5px). 색 변화 없음.

### 카드

```css
background: #ffffff;
border-radius: 12px;        /* --r-5 */
box-shadow: var(--elev-card);
padding: 18px 20px;
/* hover: box-shadow → var(--elev-lift); transform: translateY(-2px) */
```

CSS `border` 없음. 경계는 ring-shadow가 담당.

### 인풋 / 필드

```css
background: #ffffff;
border: none;
border-radius: 8px;         /* --r-4 */
padding: 11px 14px;
font-family: Pretendard Variable;
font-size: 15px;
box-shadow: var(--elev-card);

/* 에러 상태 */
box-shadow: 0 0 0 1px var(--accent-error), 0 0 0 4px var(--accent-error-ring);

/* 포커스 */
outline: 2px solid var(--focus);
outline-offset: 1px;

/* recessed well (인풋 안쪽) */
box-shadow: var(--elev-inset), 0 0 0 1px var(--ring);
background: #f5f5f5;
```

### 뱃지 / pill

```css
/* Solid */
background: #0e0d14;
color: #ffffff;
border-radius: 9999px;
padding: 5px 12px;
font-size: 12px; font-weight: 500;

/* Soft */
background: #f5f5f5;
color: #0e0d14;
box-shadow: 0 0 0 1px var(--ring);

/* Sky ring (선택 상태) */
box-shadow: 0 0 0 2px var(--sky);
```

상태는 색이 아니라 도트(`●`)나 토글로 표현. 컬러 pill 없음.

### 네비게이션 아이템

```css
/* default */
font-size: 14px; font-weight: 500; color: #898989;
padding: 9px 11px; border-radius: 8px;

/* active */
color: #0e0d14;
background: #f5f5f5;
box-shadow: 0 0 0 1px var(--ring);
/* 글로우 화면에서도 무채색 유지. --sky를 활성 nav fill에 쓰지 않는다 */
```

---

## 8. 레이아웃

- 최대 너비: `1200px` (`--container`), 가운데 정렬
- 패딩: 좌우 32px (모바일 16px)
- 섹션 간격: 80–96px (모바일 48px) — 절대 그 이하 금지
- 상단 nav: sticky, `rgba(255,255,255,0.8)` + `backdrop-filter: blur(10px)`
- 그리드: 히어로 풀폭 → 텍스트 블록 가운데 → 2–3컬럼 피처 그리드
- 브레이크포인트: 640 / 768 / 1024 / 1200px

---

## 9. 아이코노그래피

- **시스템:** Lucide (stroke 1.5–2px, rounded caps, no fill)
- **색:** `currentColor` 상속 → 항상 `#0e0d14` 또는 `#898989`. 컬러 아이콘 없음
- **크기:** 16 / 20 / 24px (짝수 스텝)
- **Emoji:** 절대 금지
- CDN: `lucide` / `lucide-static` 또는 프로젝트 내 `assets/icons/` SVG 파일 직접 참조

---

## 10. 복사 (Copy) 가이드

- **인칭:** you (독자) / we·Wearless (제품)
- **케이스:** 모든 곳에 sentence case. Title Case·ALL CAPS 금지
- **톤:** 평이하고 단호하게. 동사 위주. 형용사 남용 금지. 느낌표 금지
- **길이:** 헤드라인 2–6단어. 서브카피 1–2문장. 버튼 1–2단어
- **금지 단어:** seamless, effortless, revolutionary, powerful, robust, innovative
- **Emoji:** 없음

**예시 (올바른 톤):**
- 히어로: "Scheduling, quietly handled"
- 서브카피: "Wearless books the meeting, clears the timezones, and gets out of your way."
- 빈 상태: "No events yet. When someone books, it lands here."
- CTA: "Get started" (not "Get Started Now!!")

---

## 11. 애니메이션

- **글로우 자산:** aurora 19s ease-in-out drift · orb 12–17s linear rotation — **값 변경 금지**
- **UI 전환:** 짧고 subtle. `opacity`, `box-shadow`, `transform` 위주
- **추천 easing:** `ease` 또는 `ease-out`, 150–200ms
- **금지:** 바운스, springy overshoot, 무한 장식 루프, 슬라이드-인 오버킬

---

## 12. 체크리스트 — UI 만들 때 항목별 확인

```
□ 헤딩은 Cal Sans, 본문은 Pretendard (둘이 절대 바뀌지 않음)
□ Cal Sans 24px 미만 → +0.2px 자간 적용했는가
□ 팔레트가 무채색인가 (글로우 화면 기준)
□ CSS border 없이 ring-shadow로 경계를 표현했는가
□ 카드에 --elev-card 3레이어 그림자가 있는가
□ 섹션 간격이 80px 이상인가 (모바일 48px 이상)
□ 글로우 자산은 verbatim으로 사용했는가 (수치 변경 없음)
□ 한 화면에 글로우 존이 1개 이하인가
□ 글로우 위 텍스트/UI는 glass plate 또는 흰 여백 위에 있는가
□ 컬러 버튼 없음. CTA는 검정 유지
□ 아이콘은 Lucide stroke, 컬러 없음
□ Emoji 없음
□ 에러/성공 색은 순백 화면에서만, 텍스트/아이콘/ring으로만
```

---

## 13. 자산 경로 (디자인 시스템 기준)

```
colors_and_type.css       — CSS 토큰 전체 + 시맨틱 타입 클래스
fonts/PretendardVariable.woff2  — 본문 폰트 (self-hosted)
assets/glow/aurora.html   — 마케팅 aurora (verbatim 사용)
assets/glow/orb.html      — 프로덕트 orb (verbatim 사용)
assets/icons/             — Lucide SVG 서브셋
ui_kits/marketing/        — 마케팅 사이트 참조 구현
ui_kits/product/          — 웹앱 참조 구현
```
