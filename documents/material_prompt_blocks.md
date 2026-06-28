# 소재 인식 → 프롬프트 블록 라이브러리 (Material-aware prompt blocks)

> AG-04 마네킹 생성에서, 감지된 의류 소재 구성(`materials: [{name, ratio}]`)에 따라 **소재별 시각 묘사 블록을 비율 가중으로 자동 선택·첨부**하기 위한 정본 설계서.
> 작성: Claude + Codex 독립 초안 → 상호 반박 → 수렴(2026-06-26). 블록 본문은 **영어**(이미지 모델 반응 최적화), 감지 키는 **한국어 소재명** 유지.
>
> ⚠️ **개정 안내 — 이 문서는 2026-06-26 원본 설계서이며, 이후 코드가 진화했다. 구체 값이 코드와 다르면 항상 코드(`server/app/agents/materials.py`)가 정본이다:**
> - **(a) Taxonomy 트림(2026-06-29):** 모달·텐셀·리오셀 → `rayon` 한 키로 통합, 인견·메리노·마·모 alias 제거, 저비율(2섬유 <20%) combo 가드 추가, 토큰 단위 소재명 매칭. → 아래 **§2.1 alias 표·§3/§5 조합 열거는 이 변경 전** 내용이다.
> - **(b) 블록 본문 개정(2026-06-29):** §4·§5·§6·§6.5 영어 블록을 전수 객관 감사(소재 32종·소재별 ≥3 웹출처)로 재작성 — 절대·닫힌 표현 제거 + 이미지-우선. 근거·출처 = `documents/material_audit_findings.md`.
>
> 본 문서는 **설계 의도·원칙·구조**의 기록으로 읽고, **구체 열거(alias 키·조합·블록 본문)는 코드를 따른다.** (의도적으로 중복 열거를 코드와 재동기화하지 않는다 — 이중 출처가 곧 드리프트의 원인이므로.)

---

## 0. 한눈 요약 (비개발자용)

옷마다 소재(면·폴리·레이온·울 등)가 다르면 **빛 반사, 주름지는 모양, 늘어지는 느낌, 몸에 붙는 정도**가 완전히 다르게 보입니다. 지금 마네킹 생성기는 "면 60, 폴리 40" 같은 **글자만** 프롬프트에 넣고, 그 소재가 *어떻게 보여야 하는지*는 한 마디도 안 알려줬습니다. 그래서 이 문서는:

1. 한국 쇼핑몰에서 실제로 많이 쓰는 **상의·하의 소재 조합을 우선순위로** 정리하고,
2. 각 조합이 카메라에 어떻게 보이는지를 **영어 묘사 블록**으로 만들고,
3. 소재 비율을 보고 **어떤 블록을 얼마나 강하게 붙일지** 자동 판단하는 규칙을 정의합니다.

핵심 원칙 2가지: ① 소재 블록은 **실제 제품 사진(원본)을 거스르지 않고 보강**만 한다(소재가 사진보다 우선하지 않음). ② 이미지 모델은 **퍼센트 숫자를 계산하지 못한다** → 비율은 "어떤 단어를 쓸지"만 결정하고, 모델에겐 단어로 전달한다.

---

## 1. 핵심 원칙 (Core principles)

1. **Reference-first, material-as-prior.** 마네킹 모델은 판매자의 실제 의류 사진을 입력으로 받는다. 소재 블록은 페인트 지시("make it shiny")가 아니라 **이 섬유가 몸 위에서 어떻게 접히고/늘어지고/빛을 받는지에 대한 행동 사전(behavior prior)**이다. 모든 블록은 "원본 사진과 일치시키되 과장하지 말 것" 가드 문장을 포함한다.
2. **No ratio math.** 확산(diffusion)/이미지 모델은 퍼센트를 보간·평균·계산하지 못한다. 비율은 **등장할 단어와 그 순서(어떤 섬유 큐가 dominant인가)**만 결정한다. 퍼센트는 사람이 읽는 우선순위 라벨로만 남기고, 모델에는 명시적으로 "비율로 계산하지 말라"고 지시한다.
3. **Construction/finish > fiber ratio.** 직조/마감(데님·니트·기모·레더)은 섬유 비율보다 시각적으로 강하다. 최종 우선순위:
   **visible reference image > detected construction/finish > garment category > fiber ratio.**
4. **Elastane is fit, not surface.** 스판/엘라스탄은 표면·광택을 *스스로* 만들지 않고 **착용 핏(몸에 가깝게, 회복력, 응력선)** 을 바꾸는 modifier다. 단 이것이 '광택 없음'을 뜻하진 않는다 — 표면·sheen은 **베이스 섬유 + 위브/마감 + 사진**이 결정하므로, 스판 함량 때문에 매트로 강제하지 말고 사진이 무광~글로시/웨트룩이면 그대로 따른다(2026-06-29 감사: 'add no shine'식 표현이 폴리/나일론-스판 광택을 죽이던 오류 수정).
5. **Concise blocks.** 소재 블록이 길어지면 색·로고·실루엣·패턴 보존과 경쟁한다. 최종 첨부는 **우선순위 1줄 + 렌더 1~2줄 + 네거티브 가드 1줄**로 제한.
6. **Dynamic/behavior cues > static adjectives.** "matte/smooth" 같은 정적 형용사보다, **몸 위에서 움직일 때의 거동**(어떻게 늘어지고·흐르고·접히고·빛을 받는가)이 모델을 더 정확히 이끈다. 마네킹은 정지 포즈지만, 관절·어깨·허리에서 그 거동이 주름·장력·하이라이트로 드러난다. 예(현업 디자이너 관찰): 린넨=기분 좋은 까끌거리는 건조한 결, 실크=몸을 타고 흐르며 생기는 불규칙한 주름, 두툼한 울=어깨에 얹히는 묵직한 무게감, 레이온=팔을 뻗어 당겨지는 부위에서 천이 매끈히 당겨지며 빛 받는 곳에만 좁고 은은한 하이라이트가 도는 거동(고무처럼 늘어나거나 새틴광이 아님). 각 블록은 이런 **거동 한 컷**을 포함하도록 작성한다.
7. **닫힌/절대 표현 금지(2026-06-29 감사).** 원칙 1을 실제로 지키려면 블록 본문에 `"NOT shiny satin"`·`"Not glossy"`·`"never/avoid <속성>"` 같은 **절대·닫힌 표현을 쓰지 않는다** — 해당 섬유가 다른 위브/마감에서 그 속성을 실제로 가질 수 있어(예: 코튼 sateen·glazed chintz, 레이온 satin) 사진을 덮어쓰기 때문(A/B 실측에서 새틴 광택이 죽는 역효과 확인). 대신 **"기본 경향 + 사진이 보이면 그 sheen/weave/opacity/stretch를 따르라"** 조건부로 적고, 소재가 절대 가질 수 없는 룩(예: 천연섬유=플라스틱/고무)만 안전 네거티브로 둔다.

---

## 2. 감지 → 비율 가중 알고리즘 (Detection → weighting)

### 2.1 한국어 소재명 정규화 (alias → canonical key)

원본 한국어 이름은 추적용으로 보존하되, 표준 키로 매핑한다.

```js
const MATERIAL_ALIASES = {
  cotton:   ['코튼', '면', '순면', '오가닉코튼', 'cotton'],
  polyester:['폴리에스터', '폴리에스테르', '폴리', 'polyester', 'pe'],
  nylon:    ['나일론', 'nylon', '폴리아미드'],
  rayon:    ['레이온', '비스코스', '비스코스레이온', '인견', 'viscose', 'rayon'],
  modal:    ['모달', 'modal'],
  lyocell:  ['텐셀', '리오셀', '라이오셀', 'tencel', 'lyocell'],
  linen:    ['린넨', '리넨', '마', 'linen'],
  wool:     ['울', '양모', '모', 'wool', 'merino', '메리노'],
  cashmere: ['캐시미어', '캐시미어울', 'cashmere'],
  acrylic:  ['아크릴', 'acrylic'],
  silk:     ['실크', '견', 'silk'],
  acetate:  ['아세테이트', 'acetate'],
  elastane: ['스판', '스판덱스', '폴리우레탄', '엘라스탄', '엘라스테인', 'elastane', 'spandex'],
  // 주의: bare 'pu'는 elastane으로 매핑하지 않는다(폴리우레탄 코팅/합성가죽과 충돌). 'pu레더'는 leather.
  // '폴리우레탄'은 ratio가 낮거나 스판/stretch 맥락과 함께일 때만 elastane으로 해석.
  // 아래는 섬유가 아니라 직조/마감 신호(override) — materials[]에 들어오면 fallback으로만 수용
  denim:      ['데님', '청'],
  leather:    ['레더', '가죽', '합성가죽', '인조가죽', '페이크레더', 'pu레더'],
  brushed:    ['기모', '기모안감', 'fleece'],
  knit:       ['니트', 'knit'],
  // 여름 직조/마감 (§6.5) — 역시 construction override, 섬유 비율보다 우선
  seersucker: ['시어서커', 'seersucker'],
  chiffon:    ['시폰', 'chiffon'],
  gauze:      ['거즈', '더블거즈', 'gauze'],            // '워싱면'은 일반 면/포플린일 수 있어 제외
  mesh:       ['메쉬', '아일렛', 'mesh', 'eyelet'],     // '펀칭'은 전면 오픈워크가 reference/category로 확인될 때만 fallback
  summerknit: ['썸머니트', '썸머 니트', '여름니트', '여름 니트', 'summer knit'],
};
```

> **주의:** `데님·레더·기모·니트·시어서커·시폰·거즈·메쉬·아일렛·썸머니트`는 섬유명이 아니라 직조/마감 신호다. 정상 경로에서는 `category/subcategory/clothingType`로 들어온다. AG-01의 `materials[]`는 **섬유 조성**이므로, override의 **실제 트리거는 category/subcategory + 원본 이미지**이고 위 material-name alias는 **fallback 신호**로만 취급한다.

### 2.2 입력 정리 (cleanup)

1. 이름 trim → alias 정규화 → 빈 이름 제거.
2. ratio를 `0..100`으로 clamp.
3. 합이 `80~120`이면 100으로 정규화. 그 밖이면 원값 유지 + `confidence: low` 플래그.
4. 동일 canonical 키 병합(예: 면+코튼).
5. ratio 내림차순 정렬.
6. 역할 분리: `primary`(최상위) / `secondary`(유의미 기여) / `modifier`(소량이지만 시각적 의미, 특히 elastane) / `trace`(무시).

### 2.3 비율 밴드 표 (ratio bands)

| Ratio | 기본 처리 | 예외 |
|---:|---|---|
| `< 3%` | 무시 | elastane `≥2%`는 유지; metallic/coated/leather/silk가 명시되면 유지 |
| `3–7%` | modifier만 | elastane은 핏 장력·회복만 — 표면 sheen은 베이스 섬유+위브+사진이 결정(매트 강제 안 함) |
| `8–14%` | 약한 secondary | 미묘한 큐 1개만 |
| `15–34%` | secondary | dominant 뒤에 보조 큐 추가 |
| `35–49%` | co-primary 블렌드 | 알려진 combo 블록 우선 |
| `≥ 50%` | primary | dominant 블록이 리드 |
| `≥ 70%` | strong dominant | dominant 블록 + modifier만 (secondary 풀블록 금지) |
| `≥ 90%` | near-single | 단일 섬유 블록; elastane 외 trace 무시 |

### 2.4 elastane(스판) 전용 밴드

| Elastane | 프롬프트 효과(영어) |
|---:|---|
| `< 2%` | ignore |
| `2–4%` | "subtle stretch recovery; fabric sits closer to the body with light tension" |
| `5–8%` | "noticeable stretch; smoother body contour; fewer sharp wrinkles; gentle pull lines at stress points" |
| `> 8%` (레깅스/컴프레션 18–25% 포함) | "high-stretch fabric; firm recovery; close body-skimming fit; tension lines at bends" — **별도 ultra-stretch 티어 없음**(latex/고무 과장 위험) |

> elastane은 스스로 광택을 만들지 않지만 **매트를 강제하지도 않는다** — 표면·sheen은 베이스 섬유+위브+사진이 정한다. 면+스판은 대개 매트하지만, 폴리/나일론-스판처럼 사진이 글로시·웨트룩이면 그대로 따른다(2026-06-29 감사 개정).

### 2.5 블록 선택 로직 (의사코드)

```js
function materialPromptBlocks(materials, clothingType, subCategory) {
  const n = normalize(materials);
  if (n.known.length === 0) return [UNKNOWN_BLOCK];

  const known   = n.known.sort(descRatio);
  const top      = known[0], second = known[1], third = known[2];
  const elastane = known.find(m => m.key === 'elastane' && m.ratio >= 2);

  // 1) 직조/마감 HARD override — denim·leather·brushed만 섬유를 덮는다. (트리거: category/subcategory 우선 + alias fallback)
  //    'knit'은 hard override가 아니라 "construction context"로 둔다: 섬유가 명확하면 아래 fiber/combo가
  //    울/캐시미어/아크릴 니트 구분을 살리고, 섬유가 불명확할 때만 generic KNIT 블록을 쓴다.
  const hardOverride = detectConstruction(clothingType, subCategory, known); // denim|leather|brushed|null
  if (hardOverride) return [OVERRIDE_BLOCK[hardOverride], elastane && ELASTANE_MOD[band(elastane.ratio)]].filter(Boolean);
  const isKnit = detectKnitContext(clothingType, subCategory, known); // boolean — fiber 블록에 니트 구조 큐만 보강

  // 2) 알려진 combo (상위 2섬유 커버리지 ≥85%; 2위 ≥15%, 단 elastane은 ≥2%면 stretch combo 매칭)
  const combo = findKnownCombo(known, clothingType, subCategory);
  if (combo && combo.coverage >= 85)
    return [COMBO_BLOCK[combo.id], elastane && !combo.hasElastane && ELASTANE_MOD[band(elastane.ratio)]].filter(Boolean);

  // 3) 강한 dominant (≥70%)
  if (top.ratio >= 70)
    return [FIBER_BLOCK[top.key], secondaryModifier(second), elastaneModIfNotTop(elastane)].filter(Boolean);

  // 4) co-primary 블렌드
  if (top.ratio >= 45 && second?.ratio >= 20)
    return [synthesizedBlendBlock(top, second, third, elastane)];

  // 5) 일반 균형 블렌드
  return [genericBalancedBlendBlock(known.slice(0, 3))];
}
```

### 2.6 첨부 포맷 (PRODUCT CONTEXT 내부, Material 라인 다음에 첨부)

`[{name:'코튼',ratio:60},{name:'폴리에스터',ratio:40}]` →

```text
- Material: 코튼 60%, 폴리에스터 40%
- Material rendering guidance:
  Composition priority: 코튼 60% is the main visual base; 폴리에스터 40% is a secondary modifier.
  Render as a cotton-polyester blend: mostly matte cotton surface, smoother and more uniform than
  pure cotton, medium body, natural but reduced wrinkles, controlled folds, no strong synthetic shine.
  Do not interpolate, average, or calculate visual properties from the percentages; use the ratio only
  to choose the dominant qualitative cue. Keep this subordinate to the actual product reference image
  if the reference clearly shows a different weave, weight, or finish.
```

`[{name:'폴리에스터',ratio:72},{name:'레이온',ratio:22},{name:'스판',ratio:6}]` →

```text
- Material: 폴리에스터 72%, 레이온 22%, 스판 6%
- Material rendering guidance:
  Composition priority: 폴리에스터 is the main visual base; 레이온 softens the drape; 스판 affects fit only.
  Render as a polyester-rayon-spandex suiting blend: smooth even surface, clean vertical fall, soft
  controlled folds, faint refined sheen under light, slight stretch tension at fitted areas, fewer wrinkles.
  Do not render as cotton jersey, linen, satin, leather, or fuzzy knit unless visible in the reference.
```

---

## 3. 우선순위 소재 조합 (메이저만)

> 각 행: 감지 키 / 대표 비율 / 예시 의류. **Priority 10개 + Extended tail**.

### 3.1 상의 (TOP)

#### Priority
| # | 감지 키 / 트리거 | 대표 비율 | 예시 | 시각 큐(요약) |
|---:|---|---|---|---|
| 1 | `면 100` (cotton-dominant) | 100 / 95+ | 기본 티, 셔츠, 맨투맨 | 매트 면, 자연 결, 부드러운 불규칙 주름 |
| 2 | `면+폴리` | 60/40·70/30·80/20 | 티, 맨투맨, 후드, 캐주얼셔츠 | 매트 면 베이스 + 더 매끈·덜 구겨짐 |
| 3 | `폴리 100` | 100 / 90+ | 블라우스, 우븐셔츠, 스포츠티 | 매끈 합성 드레이프, 광택은 약하게만 |
| 4 | `레이온/비스코스/모달` | 100 / 80+ | 블라우스, 여름 톱 | 유동적 드레이프, 시원·맷-소프트러스터(실크 아님) |
| 5 | `린넨`, `린넨+면` | 55/45·60/40 | 여름 셔츠/블라우스 | 건조한 슬럽, 통기성, 또렷한 주름 |
| 6 | `울 100`, `울+나일론` | 100·80/20 | 메리노 니트, 가디건 | 미세 매트 니트 결, 자연 헤일로, 형태 유지 |
| 7 | `아크릴 100` | 100 | 보급형 니트 | 볼륨감 로프트, 탄력, 합성 퍼지(캐시미어 아님) |
| 8 | `아크릴+나일론+폴리` | 50/30/20·60/25/15 | 합성 니트/가디건 | 부드러운 합성 니트, 탄성·형태유지, 중간 퍼지 |
| 9 | `나일론/폴리+스판` | 88/12·92/8 | 피티드 톱, 베이스레이어 | 매끈 퍼포먼스 표면, 밀착·회복(스판=핏만) |
| 10 | `데님` (셔츠/자켓) | cotton 100 | 데님셔츠/자켓 | 트윌 결, 구조적 주름, 워싱 인디고 |

#### Extended tail
| 감지 키 / 트리거 | 시각 큐 |
|---|---|
| `캐시미어`, `캐시미어+울` | 미세 저밀도 헤일로, 부드러운 유동 폴드, 경량 보온, 아크릴 보풀 없음 |
| `기모` (맨투맨/후드/톱) | 브러시드 매트 나프, 두꺼워진 실루엣, 부드러운 폴드 |
| `레더/가죽/PU` (자켓/톱) | 매끈 가죽 그레인, 구조적 하이라이트(라텍스 아님) |
| `니트`(조성 불명) | 가시적 게이지/카테고리가 텍스처를 먼저 결정 |

### 3.2 하의 (BOTTOM)

#### Priority
| # | 감지 키 / 트리거 | 대표 비율 | 예시 | 시각 큐(요약) |
|---:|---|---|---|---|
| 1 | `데님` (진/스커트) | cotton 98–100 | 청바지, 데님스커트 | 트윌, 구조적 무게, 워싱 면 텍스처(구조>섬유) |
| 2 | `면 100` (chino/twill) | 100 / 95+ | 면바지, 치노, 캐주얼스커트 | 매트 견고 면, 또렷 주름, 자연 구조 |
| 3 | `면+스판` | 97/3·95/5·98/2 | 스키니, 치노, 슬림스커트 | 면 매트 표면 + 회복·밀착(스판=핏만) |
| 4 | `폴리+레이온+스판` | 65/30/5·72/22/6 | 슬랙스, 정장바지, 펜슬스커트 | 깔끔한 수직 폴, 프레스 라인, 매트 슈팅 |
| 5 | `폴리 100` | 100 / 90+ | 플리츠스커트, 와이드 슬랙스, 트랙팬츠 | 매끈 이지케어, 안정적 폴드(새틴 아님) |
| 6 | `레이온/비스코스` | 100 / 80+ | 와이드팬츠, 스커트 | 유동적 낙하, 구조감 적음(기본은 실크광 아님) |
| 7 | `린넨`, `린넨+면` | 55/45 | 린넨팬츠/스커트 | 건조 슬럽, 통기성 주름, 여유로운 구조 |
| 8 | `울`, `울+폴리` | 100·50/50 | 정장바지, 울스커트 | 따뜻한 매트 슈팅, 구조적 드레이프 |
| 9 | `나일론+스판` | 80/20·82/18 | 레깅스, 액티브 하의 | 고신축 밀착, 견고 회복, 매트 퍼포먼스 |
| 10 | `폴리+스판` | 92/8·95/5 | 레깅스, 트레이닝, 저지스커트 | 신축 회복·깔끔 실루엣, 매트 합성(라텍스 아님) |

#### Extended tail
| 감지 키 / 트리거 | 시각 큐 |
|---|---|
| `기모` (레깅스/조거/팬츠) | 브러시드 플리스 보온, 플러시 매트 나프, 두꺼워진 폴드 |
| `레더/가죽/PU` (팬츠/스커트) | 가죽 그레인, 원본 기반 구조적 광(고무 아님) |
| `폰테`, `레이온+나일론+스판` | 조밀 더블니트, 매끈 조형 드레이프, 안정 회복 |
| `캐시미어/울 니트` (스커트/팬츠) | 부드러운 프리미엄 니트 드레이프, 니트 구조가 보일 때만 헤일로 |

---

## 4. 단일 섬유 블록 (영어 본문 · building blocks)

> 더 강한 combo/override 블록이 없을 때 사용. 모든 블록에 reference 가드가 함께 첨부됨(§2.6).

```text
[cotton / 면]
Render the garment as mostly matte cotton fabric: a natural, slightly dry surface with fine woven or
jersey grain visible but not exaggerated. Medium body; forms soft irregular wrinkles and small
compression creases at bends (waist, elbows, underarms, hem). Not glossy, slippery, satin-like, or plastic.

[polyester / 폴리에스터]
Render as smooth synthetic fabric with a cleaner, more uniform surface than cotton. Low-to-moderate sheen
only on broad folds where light hits; avoid mirror gloss unless the reference shows it. Resists messy
wrinkling, keeps cleaner edges, falls in controlled folds. Not satin, not wet gloss.

[nylon / 나일론]
Render as lightweight technical fabric with a smooth compact surface and subtle sporty sheen. Folds are
crisp, springy, slightly papery. Clean tension over the body, shape recovery, fewer deep wrinkles. The
sheen is practical and sporty, not luxurious satin.

[rayon / viscose / 레이온·비스코스]
Render as soft, fluid fabric with a smooth surface and gentle drape. Falls in rounded folds, hangs close
to the body by gravity, shows soft vertical ripples rather than stiff creases. Where the fabric draws taut
over a bent arm, shoulder, or knee it smooths along the tension with a narrow soft highlight only where
light hits — no elastic recovery, no glossy satin. Surface is otherwise matte to lightly lustrous — NOT
shiny satin or silk. Cool, soft, slightly weighty hang.

[modal / 모달]
Render as very soft, smooth fabric with a matte or faintly lustrous surface. Drapes more fluidly than
cotton, sits close to the body without harsh tension, forms soft shallow folds. Gentle, low-contrast
wrinkles. Avoid crisp tailoring, rough slubs, heavy stiffness.

[lyocell / tencel / 텐셀·리오셀]
Render as smooth, cool-touch fabric with fluid drape and a subtle refined luster. Long soft folds that
lightly follow the body, fewer dry cotton wrinkles. Even, soft surface — not fuzzy or rough. Avoid
high-gloss satin unless the reference clearly shows a satin weave.

[linen / 린넨·마]
Render as dry, airy linen with visible natural slub texture and irregular yarn variation, and a pleasant
dry, grainy/scratchy hand. Matte, slightly coarse, breathable. Forms easy relaxed wrinkles and slightly
angular creases that stay put at sitting points, elbows, waist, hems. Not smooth, stretchy, glossy, or
perfectly pressed.

[wool / 울·양모]
Render as warm wool with a soft matte surface and a subtle fuzzy fiber halo. Has body and thickness;
forms rounded structured folds rather than sharp papery creases. Warmer and denser look than cotton. A
thick/heavy wool (coat, melton) reads weighty — it sits with visible mass on the shoulders and hangs in
deep, structured folds that hold their volume. Avoid synthetic shine, wet gloss, or thin cling unless a
lightweight wool blend is indicated.

[cashmere / 캐시미어]
Render as ultra-soft fine knit with a low-density even halo and a subtle natural sheen. Lightweight with
soft fluid drape and relaxed shape retention (soft recovery, not stiff). Premium matte softness. Explicitly
NOT acrylic fuzz and NOT visible pilling.

[acrylic / 아크릴]
Render as synthetic knit with soft bulk, mild uniform fuzz, and a springy resilient hand. Lighter and
loftier than wool, rounded folds, moderate volume. Matte, slightly plush synthetic yarn — not glossy
plastic, and not the fine even halo of cashmere.

[silk / 실크]  (광택은 직조 의존 — 기본은 매트-소프트, satin/charmeuse일 때만 고광택)
Render as fine, smooth fabric with fluid drape and a soft reference-matched luster; light. As it flows over
the body it forms soft, irregular cascading wrinkles and falls close to the figure. Apply a bright liquid /
satin sheen ONLY if the reference shows a satin or charmeuse weave; a matte silk (crepe, habotai, twill)
should not be rendered glossy.

[acetate / 아세테이트]  (안감·일부 블라우스)
Render as crisp fabric with a glossy satin-like sheen and structured drape.

[elastane / 스판  — modifier only, never a surface]
Treat elastane as stretch behavior, not a surface material: the garment sits closer to the body, recovers
smoothly, and shows gentle tension lines at stress points (shoulders, bust, waist, hips, knees, seat).
Smoother, less-broken wrinkles. Add no shine.
```

---

## 5. 조합 블록 (영어 본문 · combo blocks)

> 단일 섬유를 단순 합치면 큐가 충돌(면=매트/주름 vs 폴리=매끈/광)하므로, 메이저 조합은 **하나의 일관된 타깃**으로 합쳐 기술한다.

```text
[면+폴리  cotton-polyester]  (60/40·70/30·80/20)
Cotton-based fabric made smoother and more wrinkle-resistant by polyester. Mostly matte, cleaner and more
uniform than pure cotton. Natural but less-crushed folds, fewer random wrinkles, better shape recovery.
Medium body — not clingy, not liquid. Avoid strong synthetic shine.

[면+스판  cotton-spandex]  (95/5·97/3·98/2)
Matte cotton surface with visible stretch recovery. Sits slightly closer to the body with smooth tension
at fitted areas; softer, more controlled wrinkles than pure cotton; gentle pull lines near shoulders,
bust, waist, hips, knees. Do NOT make it look like shiny athletic fabric.

[폴리+스판  polyester-spandex]  (92/8·95/5)
Smooth stretch synthetic with a clean surface, slight light-catching sheen, and stretch recovery. Follows
curves more closely than cotton; smooth tension lines, fewer broken wrinkles; elastic controlled folds at
joints. Close body-skimming fit only where the category/reference supports it. Avoid rough natural texture,
linen wrinkles, fuzzy wool behavior; not latex, not rubber, not wet gloss.

[폴리+레이온+스판  polyester-rayon-spandex suiting]  (65/30/5·72/22/6)
Smooth suiting fabric: clean vertical fall, soft rayon drape, polyester wrinkle resistance, slight stretch
recovery. Even, mostly matte with a faint refined sheen under light. Folds hang neatly from waist/hips/
pleats with gentle tension at fitted areas. Avoid casual cotton texture, fuzzy knit, or liquid satin gloss.

[레이온+폴리  rayon-polyester]  (55/45·65/35·70/30)
Soft, smooth fabric with rayon-like fluid drape and polyester-added stability. Longer, cleaner folds than
cotton; mild weight, less messy wrinkling; smooth, mostly matte to lightly lustrous. Avoid stiffness, rough
slubs, high-gloss satin unless visible in the reference.

[모달/텐셀+면  modal/tencel-cotton]  (50/50·60/40)
Soft matte fabric with a smoother hand and more fluid fall than pure cotton. Lightly follows the body,
shallow soft folds, natural non-synthetic surface. Low-contrast relaxed wrinkles. Avoid crisp tailoring,
rough linen texture, glossy satin highlights.

[린넨+면  linen-cotton]  (55/45·60/40)
Breathable matte fabric with dry linen texture softened by cotton: subtle slubs, uneven yarn character,
relaxed wrinkles but less harsh crunch than pure linen. Airy structure, slightly angular folds, casual
natural fall. Avoid synthetic sheen, body-hugging stretch, or perfectly wrinkle-free surfaces.

[린넨+레이온  linen-rayon]  (55/45)
Natural summer fabric with linen slub texture and rayon-softened drape: matte, lightly irregular,
breathable, but falls more fluidly than pure linen. Wrinkles exist but look softer. Avoid glossy satin,
stiff canvas, or activewear texture.

[울+합성  wool-synthetic]  (울+나일론/폴리 80/20·70/30)
Warm wool-blend fabric: soft matte texture, subtle fiber halo, improved shape retention from synthetic
fiber. Moderate thickness, rounded structured folds. Cleaner and less fuzzy than pure wool but still warm.
Avoid shiny polyester gloss, thin cling, rough cotton creases.

[울+캐시미어  wool-cashmere knit]  (premium)
Fine premium knit with a low-density even halo and soft fluid folds; lightweight warmth, refined matte
softness, relaxed shape retention (soft recovery). NOT acrylic fuzz, NOT visible pilling. Apply halo only if
knit construction is visible.

[아크릴+나일론+폴리  synthetic knit]  (50/30/20·60/25/15)
Soft synthetic knit with moderate bulk, uniform yarn texture, light fuzzy softness. Rounded folds, cozy
knit volume without heavy wool density. Matte, slightly plush, resilient. Avoid flat woven cotton, slick
satin, hard technical nylon.

[나일론+스판  nylon-spandex technical]  (80/20·85/15·88/12)
Sleek technical stretch with a compact smooth surface, subtle sporty sheen, elastic recovery. Sits close
to the body / holds a clean athletic shape (close fit conditional on category/reference); tension lines at
bends and fitted areas; springy controlled folds. Avoid cotton fuzz, linen slubs, heavy wool texture; not
latex, not rubber, not wet gloss.

[면+폴리+스판  cotton-polyester-spandex]  (70/27/3·60/37/3)
Medium-body casual fabric: cotton matte texture, polyester smoothness, subtle stretch recovery. Cleaner
and less wrinkled than pure cotton but still natural, not glossy. Keeps silhouette while flexing slightly
at waist/hips/knees/elbows. Controlled creases rather than crushed wrinkles.

[레이온+나일론+스판  ponte double-knit]  (65/30/5)
Dense smooth double-knit with a soft, slightly weighty fall and elastic recovery. Compact even surface,
not fuzzy; subtle stretch tension; rounded controlled folds, few sharp creases. Reads as refined stretch
knit — not cotton jersey, satin, or thin activewear.
```

---

## 6. 직조·마감 override 블록 (construction/finish — fiber보다 우선)

> **트리거: category/subcategory + 원본 이미지(우선), material-name alias(fallback).** §1-3 hierarchy 적용.

```text
[denim / 데님]  (rigid; +PU면 stretch denim)
Render as denim twill: sturdy matte cotton surface, visible diagonal twill grain, structured body. Firm,
somewhat angular folds with strong crease memory at knees, hips, waistband, pockets, hems; washed indigo
or black if the reference shows it. Does not drape like rayon or shine like satin.
 + (stretch denim, 코튼+폴리우레탄 98/2·97/3): preserve denim thickness, matte surface, diagonal weave,
   but let it contour slightly closer to the body; softened creases and tension at hips/knees/seat.
   Not thin leggings, not shiny synthetic.

[leather / faux leather / 레더·가죽·PU]
Render as leather-like material with a smooth opaque surface, reflective highlights that follow fold
shapes and seams, and a stiffer body than woven fabric. Broad, sculptural, slightly rigid folds — not soft
cotton wrinkles. Preserve opacity and weight. Avoid fabric weave texture (unless suede/backing is shown)
and avoid plastic/latex/rubber look.

[brushed fleece / 기모]
Render as brushed-fleece (napped) fabric. The OUTER face stays reference-matched (usually a clean jersey/
sweatshirt surface) — do not fuzz the exterior unless the reference clearly shows an exposed brushed nap.
The brushing is mainly interior: render the plush soft nap only at the visible interior or exposed areas
(cuffs, hem, opening). Overall a slightly thicker/cozier silhouette than plain jersey with softened rounded
folds; matte, not slippery, not crisp woven. Negative: do NOT turn it into fur, shearling, mohair, or
towel/terry texture.

[knit (gauge unknown) / 니트]
When construction is "knit" but fiber is unclear, let the visible knit gauge and garment category decide
texture (rib, cable, jersey, waffle) BEFORE fiber ratio. Render visible stitch structure faithfully; do
not invent rib/cable that the reference does not show.
```

---

## 6.5 여름 직조/마감 (summer constructions — 시즌 우선)

> 한국 여름 쇼핑몰의 시그니처. 이들은 **섬유가 아니라 직조/마감**이라 §6와 같은 construction override(트리거: category/subcategory + 원본 이미지, 섬유 비율보다 우선)다. 공통 거동: **가볍고·통기성 있고·몸에서 살짝 떨어져 흐른다.** 비침(semi-sheer)은 **직조/원본이 뒷받침할 때만** 적용(시어서커·더블거즈는 보통 불투명). 섬유 블록(린넨·레이온·모달·텐셀·면)과 **함께** 쌓되, 직조 큐가 표면을 지배한다.

```text
[seersucker / 시어서커]
Render as lightweight puckered cotton: alternating raised crinkled stripes and flat smooth stripes give a
bumpy 3D surface that holds itself slightly away from the body. Matte, dry, airy; casual rumpled look that
reads intentional, not wrinkled-by-accident. Do not press flat or smooth into plain cotton.

[chiffon / 시폰]  (보통 폴리 또는 실크)
Render as very lightweight, airy fabric with a fine grainy/crinkled surface from highly twisted yarns.
Floats and ripples with the slightest movement; soft matte-to-faint sheen. Translucent-to-sheer chiffon
behavior, but PRESERVE the lining, opacity, and garment coverage shown in the reference — do not turn a
lined blouse/dress transparent. Not heavy, not glossy satin, not stiff.

[cotton gauze / 거즈·더블거즈]
Render as soft, loosely woven cotton with an open, breathable, slightly crinkled airy texture. Relaxed
lived-in drape, soft rumpled wrinkles, matte. Single gauze may be slightly translucent; double gauze reads
soft and crinkled but more opaque — do not make lined or double-layer gauze see-through. Avoid crisp ironed
surfaces or synthetic smoothness.

[mesh / eyelet / 메쉬·아일렛]
Render as fabric with a visible regular open structure (mesh holes or embroidered eyelet perforations).
Show-through ONLY through the actual openings; preserve the lining/underlayer and the garment's original
coverage. Light, airy, breathable; keep the hole pattern even and reference-matched. Do not fill the
openings into a solid surface, and do not convert small punched details into full transparency.

[summer knit / 썸머니트·여름 니트]  (면/레이온/린넨 오픈 게이지)
Render as a loose open-gauge knit with visible airy stitch structure, light drape, and a soft semi-sheer
quality (skin/underlayer faintly visible between stitches). Cool and relaxed, not the dense warm bulk of a
winter sweater. Apply only when the reference shows an open summer knit.
```

**여름 우선 조합 빠른 참조** (§3 목록 중 시즌 가중↑): 상의 — `린넨`/`린넨+면`, `레이온/비스코스`, `모달/텐셀+면`, 시폰·거즈·메쉬·썸머니트. 하의 — `린넨`/`린넨+면` 팬츠·스커트, `레이온/비스코스` 와이드, 시어서커 쇼츠·팬츠. F/W의 `울`/`기모`/`두꺼운 니트`는 여름엔 가중치를 낮춘다.

---

## 7. 충돌 해소 규칙 (conflict resolution)

- **Matte vs sheen** — 면·린넨·울: 매트. 폴리·나일론·리오셀: 미묘한 광 가능. 레더: 반사. 실크/새틴: 명시·가시일 때만 고광택. 면+폴리 블렌드는 **기본 매트 + 약한 클린 하이라이트만**(한국 기본템은 면-폴리 저지/플리스가 많아 폴리광 과장 시 싸구려·새틴처럼 보임).
- **Fluid vs structured (drape)** — 레이온/모달/리오셀=유동 낙하, 면=중간 바디, 린넨=통기·각진 주름, 데님=견고 구조, 울=둥근 두께·구조, 폴리=구성에 따라(기본은 "controlled folds"). **충돌 시 garment category가 tie-break**: 블라우스/셔츠→유동 허용, 티/맨투맨→중간 저지 바디, 슬랙스/스커트→깔끔 수직 폴, 데님/팬츠→구조 우선, 니트/가디건→소프트 벌크 우선.
- **Wrinkle** — 면=자연 불규칙, 린넨=뚜렷한 이완 주름, 폴리=적음, 레이온=부드러운 리플, 스판=주름 감소+장력선, 데님=강한 크리스 메모리. "많은 주름"과 "주름 방지"를 한 블록에 같이 쓰지 말 것 → 블렌드는 `natural but reduced wrinkles`.
- **Surface texture 환각 금지** — reference/category가 뒷받침하지 않으면 `ribbed`/`brushed fleece`/`twill`/`satin`을 쓰지 않는다.

---

## 8. 리스크 · 모델별 실패 모드 (반드시 가드)

1. **Ratio ≠ visible fabric.** 60/40 면-폴리 니트와 우븐 셔츠는 다르게 보인다. 비율은 직조/중량/마감을 결정 못함 → 항상 `Keep this subordinate to the actual product reference image` 가드.
2. **강한 텍스처 단어 과민반응.** `linen slub`, `fuzzy wool`, `satin`, `leather`, `ribbed`, `mesh`, `denim twill`는 의류 정체성을 바꿀 수 있다 → material key/reference가 뒷받침할 때만.
3. **폴리 → 새틴 오인.** 안전 표현: `smooth synthetic fabric with subtle sheen only on broad highlights` + `not satin, not wet gloss`.
4. **스판 → 라텍스 오인.** `stretchy, body-hugging, shiny`로 쓰면 레깅스/라텍스/액티브로 변질 → `stretch recovery, smoother contour, gentle tension lines` (핏 행동으로만).
5. **레이온/텐셀 → 새틴·잠옷 오인.** `smooth, fluid, softly lustrous` 사용, `silky/glossy/satin-like` 금지(가시일 때 제외).
6. **울/아크릴 → 과도한 보풀(모헤어).** `subtle fiber halo`, `mild fuzzy softness`. shaggy/boucle/mohair는 명시일 때만.
7. **데님은 마감이지 섬유가 아님.** `코튼 100`만으로 데님 렌더 금지 — `데님/청` 토큰 또는 jeans/denim 카테고리 또는 reference가 보일 때만.
8. **프롬프트 길이 희석.** 긴 소재 블록이 색·로고·넥라인·패턴 보존과 경쟁 → 우선순위 1줄 + 렌더 1~2줄 + 네거티브 1줄로 압축.
9. **마감 우선 계층.** 같은 100% 폴리도 시폰/새틴/트랙팬츠가 다 다름. 가능하면 `visible reference > construction/finish > category > fiber ratio`.
10. **한국어 셀러 용어 모호성.** `스판`=신축 있음(엘라스탄 함량 불명일 수 있음), `폴리`=안감/레이스 트림일 수도. 소재 블록은 사실 인증이 아니라 시각 가이드로만 사용.

---

## 9. 기존 파이프라인 연동 (구현 메모)

- **첨부 지점(구현 완료):** `server/app/agents/prompts.py`의 `_product_block()`가 `Material: ...` 줄 **다음에** §2.6 포맷의 `Material rendering guidance` 블록을 자동 첨부한다(`materials.material_guidance()` 호출). 감지·정규화·선택 로직은 `server/app/agents/materials.py`.
- **(해결됨) `_sanitize` dict-mangle 버그:** `analysis.materials`의 `{name, ratio}` dict를 `str()`로 깨뜨리던 문제는 수정됨 — `_product_block`이 dict에서 `name`/`ratio`를 꺼내 sanitize·`int(ratio)%` 표기 후, 정규화는 `material_guidance()`(§2.1~2.2)가 담당한다.
- **블록 위치 권장:** PRODUCT CONTEXT(ground-truth) 블록 안. base 템플릿 본문이 아니라 컨텍스트 블록에 둬야 "reference에 종속" 원칙과 일관.
- **데이터 의존성:** override(데님·레더·기모·니트) 트리거는 `clothingType/category/subcategory`. AG-01 `materials[]`는 섬유 조성만 신뢰. 카테고리 enum은 `documents/common_data_contract.md` 참조.
- **검증 방법:** 프로젝트에 테스트 스위트 없음(agents.md) → 대표 조합 5~6개(면100 / 면폴리 / 폴리레이온스판 슬랙스 / 데님 / 나일론스판 레깅스 / 기모 맨투맨)로 마네킹 생성 A/B 후 드레이프·광택·주름 육안 확인.

---

## 부록 A. 작업 방식 (Claude ↔ Codex 수렴 기록)

- 두 에이전트가 **독립 초안** 작성 → Claude가 6개 쟁점으로 반박(니트 커버리지 갭, 기모 누락, override 트리거가 materials[]냐 category냐, 퍼센트 원본 표기, 스판 밴드, 스코프) → Codex **6개 전부 수렴**(정제 포함) → 본 정본으로 병합.
- Claude 기여: behavior-prior 원칙, 캐시미어/울/아크릴 니트 분리, 기모 추가, override 트리거=category 명시.
- Codex 기여: ratio 밴드 표, 입력 정규화/confidence, 레더 블록, category tie-break, `reference>construction>category>ratio` 계층, 네거티브 가드 문구.

## 부록 B. 출처 (fiber 특성 근거)

- Drape/stiffness/sheen 비교: packlove "What Is Drape", TREASURIE Fabric Drape, Sino Silk *Rayon vs Polyester*, Anuprerna *Viscose Rayon*.
- 면-폴리 60/40: northshorecrafts, Alibaba product-insights(60/40), CatKissFish.
- 데님 조성·트윌·위스커: Lee Denim Glossary, Fibre2Fashion *What is Denim*, szoneierfabrics *Stretch Denim*, Levi's Denim Terminology.
- 니트(울/아크릴/캐시미어): Tissura *Cashmere Fabrics*, Yardblox *Sweater Knit*, Regen-tech *Cashmere Blend*; 캐시미어 vs 아크릴 식별: VCG *How to Identify Cashmere*, Cashmere Connoisseur, SELVANE.
- 레이온/모달/텐셀/리오셀: Stone Mountain, Yardblox *Rayon Guide*, Taihu Snow, VNPOLYFIBER.
- 린넨/린넨블렌드: Eton *Cotton-Linen*, Pier St Barth, Faros Linen, Put This On.
- 나일론/폴리 스판 액티브웨어: Spandexbyyard(*Nylon vs Poly Spandex*, *Activewear Fabric Guide*), Sportek.
- 기모/브러시드 플리스: MEXESS *Brushed Fleece*, KnitFabric.com, JcwTextile *Fleece Types*, Sewing.org *Napped Fabrics*.
