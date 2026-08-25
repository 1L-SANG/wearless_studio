# 에디터 템플릿·프레임·톤 시스템

- 날짜: 2026-08-21
- 상태: 설계 확정 대기 (오너 리뷰 전)
- 관련: `src/features/editor/editorLibrary.js`(`FRAME_LIBRARY_ITEMS`) · `src/features/editor/presets/infoPresets.js` · `src/features/editor/presets/textPresets.js` · `src/features/editor/EditorPanels.jsx` · `src/features/editor/Editor.jsx` · `server/app/agents/page_assembler.py` · `supabase/migrations/20260612090000_init.sql` · 선례 `docs/superpowers/specs/2026-08-10-detail-point-templates-design.md`
- 참조: 미리캔버스 테마 색상 패널(오너 제공 화면녹화 2026-08-21) · `documents/research/2026-07-29-detail-page-analysis.md`

## 1. 배경

에디터에 "미리 짜인 템플릿을 골라 시작"하는 경험이 없다. 미리캔버스처럼 **썸네일 + 내용까지 채워진 템플릿 갤러리**가 없고, 지금은 프레임(`FRAME_LIBRARY_ITEMS`)을 하나씩 삽입하거나 정보 블록(`infoPresets.js`)을 폼으로 만드는 방식뿐이다.

다만 우리 서비스는 미리캔버스와 전제가 다르다: **사진(모델 착장 컷)이 콘티보드에서 AI로 이미 생성**되고, 각 컷에 역할 태그가 박혀 나온다(`storyboardTaxonomy.js`의 `content_roles`: hero/benefit/coordination/fit/realWear/productOverview/detail). 그래서 "빈 슬롯에 사진을 끼워 맞추는" 미리캔버스식 템플릿은 오히려 사용자에게 일을 시킨다. 대신 **생성된 컷이 역할에 맞춰 슬롯에 자동으로 흘러들어가는 템플릿**이 우리 강점을 살린다.

오너 결정(2026-08-21 대화): 미리캔버스의 **① 브라우징 썸네일 UX ② 팔레트가 페이지 전체 톤을 구동하는 테마 시스템**만 차용하고, 사진은 우리 생성 컷을 자동 채움한다. 배경은 흰색 고정이 아니라 **연한 톤 색**(테마 구동), 상품 사진색을 추출해 중심색으로 쓴다 — 화면녹화의 미리캔버스 "테마 색상" 패널과 같은 결.

## 2. 결정 요약 (오너 확정)

| # | 결정 | 내용 |
|---|---|---|
| D1 | 2단 구조 | **큰틀 = 템플릿**(프레임+정보블록의 순서 묶음), **작은틀 = 프레임**(이미지 슬롯 레이아웃). 프레임은 기존 `FRAME_LIBRARY_ITEMS` 확장 |
| D2 | 슬롯 채움 | **역할 매칭 자동채움 + 빈 폴백.** 슬롯의 기대 역할에 맞는 생성 컷이 있으면 자동으로 넣고, 없으면 "이미지 첨부" 빈 슬롯(placeholder)으로 남긴다 |
| D3 | 톤 시스템 | **처음부터 포함.** 중심색 하나에서 톤온톤/톤인톤/모노톤 팔레트를 만들어 배경·텍스트·구분선·뱃지·프레임 장식에 적용 |
| D4 | 배경 | **연한 톤 배경**(흰색 고정 아님). 중심색의 아주 연한 틴트. 테마 전환 시 라이브로 바뀐다 |
| D5 | 중심색 출처 | **상품 색상(분석 `colors`) 추출**이 1차 경로 + 큐레이션 팔레트 그리드 선택이 2차. 사용자가 중심색을 바꿀 수 있다 |
| D6 | 프레임 큐레이션 | 미리캔버스/쇼핑몰 레퍼런스를 **사람이 직접 셀렉**(AI 아님), 무난~적당한 취향. **기존 예시 프레임은 숨기고** 새 세트로 재저작 |
| D7 | 변형 원칙 | 레퍼런스 **그대로 복제 금지**, 분위기·틀은 유지하되 변형해 구성 |
| D8 | 텍스트 처리 | 일반 문구(CHECK POINT, DETAIL 등)는 프레임에 그대로 baked, 내용 들어갈 자리 = `"내용을 입력하세요."`, 이미지 자리 = 빈 슬롯(이미지 첨부) |
| D9 | 카피 삽입 | 레퍼런스/쇼핑몰에 자주 나오는 큰 문구를 **큐레이션 카피 라이브러리**로 이미지 사이 기본 문구에 주입 |
| D10 | 썸네일 | **정적 저작 자산.** 템플릿당 1회 실 파이프라인으로 생성(AI가 인물·의류만 변형), 개발 중엔 스키매틱 도식 임시. (오너 미확정 — 스펙 기본값, 뒤집기 가능) |
| D11 | 첫 진입 기본값 | `editorTheme` 미설정이면 **흰색 미니멀**(기존 동작)이 baseline. 톤 틴트는 테마/템플릿을 고르는 순간 발동한다. 기존 섹션 순서 유지, 사용자가 프레임·템플릿을 하나씩 추가하는 느낌 |

## 3. 용어·구조

```
프로젝트
 ├─ editorTheme            ← 톤 설정(중심색 + 팔레트 모드). 신규. 클라·서버 공유
 └─ editor_blocks[]        ← 블록 배열(기존)
      ├─ 컷 블록            ← 콘티 생성 컷(자동 배치, 기존)
      ├─ 프레임 블록        ← 이미지 슬롯 레이아웃(FRAME_LIBRARY). 슬롯에 컷 자동채움
      └─ 정보 블록          ← 표·글·안내(infoPresets, 기존)

템플릿(신규, 저작 데이터)  = [프레임/정보블록 타입 + 순서 + 프레임별 기대 역할 + 기본 카피 + 톤 힌트]
프레임(기존 확장)          = { id, label, h, bg, slots[], elements[] }  (slots = frameSlot 이미지 자리)
슬롯(기존)                = { type:'image', frameSlot:true, src, roleHint? }
```

- **템플릿**은 새 상위 개념이다. 프레임/정보블록을 **어떤 순서로, 각 이미지 슬롯이 어떤 역할 컷을 기대하는지**까지 규정한 저작 데이터. "이 템플릿 적용" = 템플릿의 블록들을 문서에 삽입 + 슬롯에 역할 컷 자동채움 + 톤 적용.
- **프레임**은 기존 `FRAME_LIBRARY_ITEMS` 구조를 그대로 쓴다(`naturalSlot`/`templateText` 헬퍼). 재저작은 데이터 항목 교체이지 엔진 변경이 아니다.

## 4. 데이터 모델

### 4.1 템플릿 정의 (신규 — `src/features/editor/templates/`)

```js
// 저작 데이터. 코드가 아니라 선언적 카탈로그.
template = {
  id: 'summer-fashion',
  label: '썸머 패션',
  category: 'minimal' | 'editorial' | 'lookbook' | ...,
  thumb: '/assets/editor/templates/summer-fashion.png',   // D10 정적 자산
  toneHint: { mode: 'tone-in-tone', bgTint: 0.06 },        // 톤 기본값(중심색은 런타임)
  sections: [
    { block: 'frame', frameId: 'polaroid-hero', roleHints: ['hero'], copy: { headline: 'Summer fashion', sub: '내용을 입력하세요.' } },
    { block: 'frame', frameId: 'split2',         roleHints: ['coordination', 'fit'] },
    { block: 'info',  infoType: 'feature_icons', layout: 'stack' },
    { block: 'frame', frameId: 'grid3',          roleHints: ['detail', 'detail', 'detail'] },
    { block: 'info',  infoType: 'size_table' },
    // ...
  ],
}
```

- `roleHints`는 슬롯 순서대로의 기대 `content_role`. 자동채움 엔진이 이 힌트로 컷을 매칭한다(§6).
- `copy`는 baked 문구(§9). 없으면 프레임 자체의 `templateText`가 정본.
- `toneHint`는 톤 기본값일 뿐, 실제 중심색은 상품색에서 런타임 결정(§8).

### 4.2 톤 설정 (신규 — 프로젝트 수준)

```js
editorTheme = {
  centerColor: '#8a9bb5',        // 중심색 hex
  mode: 'tone-in-tone' | 'tone-on-tone' | 'mono',
  bg: '#f3f5f8',                 // 파생 배경(연한 틴트)
  palette: { ink, muted, faint, line, badgeBg, badgeInk, accent },  // 파생 악센트
}
```

- **저장 위치**: `projects` 테이블에 **`editor_theme jsonb` 컬럼 신설**(현재 `editor_blocks jsonb` 옆, migration:54). 다운로드 정본인 서버 `page_assembler.py`도 이 필드를 읽어야 하므로(§7.4 패리티), 블록 배열 안에 숨기지 않고 **형제 필드**로 둔다.
- `palette`는 `centerColor`+`mode`에서 **결정론적으로 파생**(§7.2). 저장하는 이유는 재현성(같은 프로젝트를 다시 열거나 서버가 다운로드 조립할 때 동일 색)과 대비 클램프 결과 고정.

## 5. 컴포넌트

| # | 컴포넌트 | 신규/기존 | 위치(예정) |
|---|---|---|---|
| C1 | 프레임 라이브러리 재저작 | 재저작(데이터) | `editorLibrary.js` `FRAME_LIBRARY_ITEMS` |
| C2 | 템플릿 카탈로그 + 적용 로직 | 신규 | `src/features/editor/templates/` |
| C3 | 자동채움 엔진(역할 매칭) | 반쯤 기존 | 신규 `templates/autofill.js` (page_assembler 역할배치 규칙 재사용) |
| C4 | 톤 시스템(팔레트 파생 + 적용) | 신규 | `src/features/editor/tone/` + `page_assembler.py` |
| C5 | 중심색 픽커(상품색 + 팔레트 그리드) | 신규 | `EditorPanels.jsx` 테마 탭 |
| C6 | 큐레이션 카피 라이브러리 | 신규 | `src/features/editor/templates/copyLibrary.js` |

## 6. 자동채움 규칙 (C3, D2)

슬롯에 `roleHints[i]`가 있을 때:

1. 문서의 **미사용 생성 컷** 중 `content_role`이 힌트와 일치하는 첫 컷을 슬롯 `src`로 채운다.
2. 정확 매칭이 없으면 **같은 섹션 역할**(hooking/styling/studio/product) 컷으로 완화 매칭.
3. 그래도 없으면 슬롯을 **빈 폴백**으로 둔다 — `frameSlot:true`, `src:null`, checkerboard placeholder("이미지 첨부"). 사용자가 드래그로 채운다.
4. 컷 개수가 슬롯보다 **많으면** 남는 컷은 소비하지 않는다(가변 N 안전). **적으면** 남는 슬롯은 빈 폴백.

- 매칭은 서버 `page_assembler`의 역할→배치 규칙과 **같은 순서 규칙**을 따른다. 규칙을 두 곳에 복제하지 않도록 규칙표를 단일 모듈로 뽑는다(클라 `autofill.js` ↔ 서버 동일 로직 미러, `textPresets` 회색 상수 미러링과 같은 방식).
- 한 번 채운 뒤 사용자가 슬롯을 비우면 자동 재채움하지 않는다(사용자 의도 존중, 기존 `editorSelection.js` 빈 슬롯 규칙과 일치).

## 7. 톤 시스템 (C4, D3/D4)

### 7.1 무엇에 적용되나

| 대상 | 톤 적용 | 비고 |
|---|---|---|
| 페이지 배경 | ✅ 연한 틴트 | 중심색을 아주 연하게(명도↑ 채도↓). `editorTheme` 미설정이면 흰색(D11) |
| 헤드라인/소제목/설명 텍스트 | ✅ | 대비 클램프 필수 |
| 구분선(line) | ✅ | |
| 뱃지(rect+text) | ✅ | badgeBg/badgeInk 쌍 |
| 프레임 장식(테두리·포인트) | ✅ | |
| **모델 컷 사진** | ❌ | 절대 건드리지 않음 |
| 정보 블록 표 헤더/강조 | ✅ 악센트만 | 본문 가독 색은 유지 |

### 7.2 팔레트 파생 (결정론)

중심색 `centerColor`(HSL로 변환) + `mode`:

- **mono(모노톤)**: 같은 hue, 채도 낮춤. 명도만 벌려 배경/텍스트/악센트 구분.
- **tone-on-tone(톤온톤)**: 같은 hue, 명도 폭 크게(연배경 ↔ 진한 텍스트).
- **tone-in-tone(톤인톤)**: 인접 hue(±15~30°) 섞어 배경↔악센트에 미묘한 색차.

배경 = 명도 92~96%·채도 8~15%로 클램프한 틴트. 텍스트 잉크 = 명도 12~20%.

### 7.3 대비 안전 (필수)

- 배경 대비 본문 텍스트 대비비 **≥ 4.5:1**(WCAG AA 근사), 큰 제목 ≥ 3:1. 미달 시 텍스트 명도를 자동으로 어둡게/밝게 클램프.
- 뱃지 배경↔뱃지 잉크도 동일 클램프.
- 파생 결과를 `editorTheme.palette`에 **고정 저장**해 재현성 확보(런타임마다 재계산해 미세하게 달라지는 것 방지).

### 7.4 page_assembler 패리티 (교차 제약, 핵심)

- **다운로드 정본은 서버 `page_assembler.py`**(메모리 `editor-block-geometry` 확정: 미리보기·다운로드 공유 정본). 현재 색이 하드코딩됨 — `#ffffff`(bg), `#0e0d14`(ink), `#6b6b73`(muted), `#f5f5f5`(badge) (page_assembler.py:272~301 등).
- 톤을 클라 렌더에서만 적용하면 **미리보기 ≠ 다운로드**. 반드시:
  1. `editorTheme`를 프로젝트에 저장(§4.2),
  2. 클라 렌더와 서버 조립이 **같은 `palette`를 읽어** 하드코딩 색을 치환.
- 파생 계산(§7.2)은 클라에서 1회 하고 결과 `palette`를 저장 → 서버는 재계산 없이 **저장된 palette를 소비만** 한다(파이썬에 색연산 복제 안 함). 이게 패리티를 가장 안전하게 만든다.

## 8. 중심색 픽커 (C5, D5)

- **1차: 상품색 추출.** 분석 `colors[].swatchId`가 클라에 옴(`httpAdapter.js:426`). swatchId→hex 매핑이 이미 클라에 있다(`colorAutofill.js`·`colorwayMatching.js`). 대표 색(첫 색/가장 진한 색)을 중심색 기본값으로 제시.
- **2차: 팔레트 그리드.** 미리캔버스 "모든 테마 색상"처럼 큐레이션된 다색 팔레트 목록에서 선택(정적 카탈로그). 선택 시 중심색+모드 세팅.
- 사용자가 스와치/컬러픽커로 중심색 직접 변경 가능. 변경 시 §7.2 재파생 → 라이브 전환.

## 9. 카피 라이브러리 (C6, D9)

- 레퍼런스/쇼핑몰에 반복 등장하는 큰 문구("Summer fashion", "DETAIL", "CHECK POINT", "COLOR", "Fabric" 등)를 **정적 큐레이션 사전**으로 둔다. AI 아님(D6 일관).
- 템플릿의 `sections[].copy` 또는 프레임 `templateText`에 기본 문구로 baked. 사용자가 클릭해 편집(기존 `DEFAULT_TEXT_BODY='내용을 입력하세요.'` + `FRESH_TEXT_IDS` 통째 선택 UX 재사용).
- 서버 `feature_copy`(AG-02, 특징 설명문)와 **역할이 다르다**: 이건 장식적 헤딩 문구(결정론 사전), 저건 특징 설명문(하이브리드 생성). 섞지 않는다.

## 10. 프레임 재저작 (C1, D6/D7)

- 기존 `FRAME_LIBRARY_ITEMS`(single/split2/grid3/grid4/hero2/colorcmp/ba/image-description-3 + 에디토리얼)을 **숨김 처리**(노출 목록에서 제외, 코드/데이터는 보존해 레거시 문서 재현성 유지 — 삭제 아님).
- 새 세트를 미리캔버스 결로 재저작: `naturalSlot`(이미지 자리) + `templateText`(baked 문구). 폴라로이드 프레임, 대형 히어로, 2/3분할, 디테일 포인트, 컬러뷰 등.
- **IP**: 레이아웃 자체는 저작권 보호가 약하고 D7의 변형으로 안전권. 특정 유명 디자인의 픽셀·문구·독자적 그래픽을 그대로 복제하는 것만 피한다.
- 노출 필터는 `EditorPanels.jsx:1025`의 `FRAME_LIBRARY_ITEMS.filter(...)`에 가시성 플래그 추가로 처리.

## 11. 변경 파일

### 프론트

| 파일 | 변경 |
|---|---|
| `src/features/editor/editorLibrary.js` | `FRAME_LIBRARY_ITEMS` 재저작 + `visible` 플래그(레거시 숨김) |
| `src/features/editor/templates/catalog.js` (신규) | 템플릿 카탈로그(선언 데이터) |
| `src/features/editor/templates/applyTemplate.js` (신규) | 템플릿 → 블록 삽입 + 자동채움 호출 + 톤 힌트 적용 |
| `src/features/editor/templates/autofill.js` (신규) | 역할 매칭 자동채움(규칙표 단일 소스) |
| `src/features/editor/templates/copyLibrary.js` (신규) | 큐레이션 카피 사전 |
| `src/features/editor/tone/derivePalette.js` (신규) | 중심색+모드 → palette(대비 클램프 포함) |
| `src/features/editor/tone/applyTone.js` (신규) | palette를 블록 elements 색에 적용(렌더 시) |
| `src/features/editor/EditorPanels.jsx` | 템플릿 갤러리 탭 + 테마(중심색/팔레트) 탭 + 프레임 노출 필터 |
| `src/features/editor/Editor.jsx` | `editorTheme` 로드/저장 배선, 템플릿 적용 진입점, `addFrame` 인접 |

### 서버

| 파일 | 변경 |
|---|---|
| `server/app/agents/page_assembler.py` | 하드코딩 색 → `editorTheme.palette` 소비로 치환(저장된 palette 읽기, 재계산 없음) |
| `supabase/migrations/<new>.sql` | `projects.editor_theme jsonb` 컬럼 신설 |
| `server/app/repo.py` | `editor_theme` 로드/저장 배선(REPLACE 시맨틱 주의, `_SERVER_OWNED` 패턴 검토) |

### 자산

| 항목 | 변경 |
|---|---|
| `public/assets/editor/templates/*.png` | 템플릿 썸네일(D10, 1회 저작) |

## 12. 검증

### 프론트 (`pnpm test:frontend`)

- **자동채움**: roleHints 매칭(정확→섹션완화→빈폴백), 컷 N 가변(많음/적음) 안전, 한 번 비운 슬롯 재채움 안 함.
- **톤 파생**: 같은 (centerColor, mode) → 같은 palette(결정론). mono/on/in 3모드 각각 배경·잉크 명도 관계 성립.
- **대비 클램프**: 저대비 중심색 입력 시 텍스트/뱃지 대비비 ≥ 임계 보장(경계 케이스 스냅샷).
- **템플릿 적용**: 삽입 순서·블록 종류 정확, baked 카피 주입, 기존 문서에 얹어도 컷 블록 불변.
- **레거시**: 숨긴 프레임을 쓰던 기존 문서가 그대로 렌더(재현성).

### 서버 (`pytest`)

- **패리티**: 같은 `editor_blocks`+`editor_theme`로 클라 렌더 색 == page_assembler 조립 색(대표 요소 색 대조).
- `page_assembler`가 `editor_theme` 없을 때 기존 하드코딩 색으로 폴백(마이그레이션 전 문서).

## 13. 리스크

| 리스크 | 대응 |
|---|---|
| 미리보기≠다운로드(톤 불일치) | §7.4 — palette를 프로젝트 저장, 서버는 소비만 |
| 자동 팔레트 저대비로 텍스트 안 읽힘 | §7.3 대비 클램프 + 저장 고정 |
| 컷 개수 가변으로 프레임 깨짐 | §6 역할 슬롯 + 빈 폴백, 픽셀 고정 슬롯 금지 |
| 색연산 클라·서버 이중 구현 표류 | 파생은 클라 1회, 서버는 저장 palette 소비(재계산 없음) |
| 레거시 프레임 문서 파손 | 숨김만(삭제 아님), 데이터 보존 |
| 썸네일 저작 비용/시간 | D10 정적 1회, 개발 중 스키매틱 임시로 병렬화 |
| REPLACE 저장으로 editorTheme 유실 | repo `_SERVER_OWNED`/이월 패턴 검토(선례 `featureCopy`) |

## 14. 열린 항목 (오너 확인 필요)

1. **썸네일(D10)** — 정적 AI 저작 자산으로 확정? 개발 초기 스키매틱 임시 허용?
2. **연한 톤 배경(D4)** — 배경 틴트 강도(예: 명도 94%)와 "흰색에 가깝게" vs "확실히 색감" 사이 강도.
3. **템플릿 초기 세트 규모** — 1차에 몇 종(카테고리 minimal/editorial/lookbook 등 × 개수)?
4. **템플릿 적용이 기존 문서에 얹힐 때** — 가산 삽입만인가, "이 템플릿으로 시작(빈 문서)"만인가, 둘 다인가? (덮어쓰기는 유료 컷 파괴라 지양 — 가산/신규만 권장)
