# 특징 포인트 템플릿 3종 + 설명 문구 자동 생성

- 날짜: 2026-08-10
- 상태: 확정 (오너 결정 반영)
- 브랜치: `feat/detail-point-templates` (origin/main `ab95c2c` 기준)
- 관련: `documents/PRD.md` §10.14 내용 추가 · `src/features/editor/presets/infoPresets.js` · `server/app/agents/copywriter.py`(AG-02) · `server/app/agents/copy_qc.py`(AG-03)

## 1. 배경

에디터의 `특징 포인트`(`feature_icons`) 블록은 레이아웃이 **1종뿐**이다 — 원형 사진을 가로로 2~5개 나열하고 그 아래 `POINT N` + 제목을 붙인다(`infoPresets.js:256` `buildFeatureIcons`).

실제 상세페이지들이 쓰는 `DETAIL POINT` 섹션은 이 모양이 아니다. 오너가 가져온 레퍼런스 7장은 세 가지 서로 다른 레이아웃으로 갈린다.

| 레퍼런스 | 구성 |
|---|---|
| 스크린샷 1~3 (코듀로이 스커트) | 상단 `DETAIL POINT` 헤딩 → 대형 사진 → 좌측정렬 굵은 제목 → 좌측정렬 설명 2줄. 포인트마다 반복 |
| 스크린샷 4~6 (카고 팬츠) | 대형 사진 → 회색 배지 `DETAIL POINT 01`(중앙) → 중앙 굵은 제목 → 중앙 설명 2줄 |
| 스크린샷 7 (린넨 셔츠) | 2열 그리드: 좌 정사각 사진 / 우 카드에 번호 `01` + 짧은 밑줄 + 하단 라벨 |

두 번째 결함: **설명 문구를 만드는 주체가 없다.** 분석 페이지의 `강조하고 싶은 특징` 칩은 40자 제목 문자열 5개일 뿐이고(`AnalysisForm.jsx:940`), `info.items[].desc` 는 셀러가 에디터에서 손으로 치는 빈칸이다. 레퍼런스의 "하이웨이스트 디자인이 다리를 더욱 길어 보이게 합니다" 같은 2줄이 나올 경로가 없다.

## 2. 결정 요약 (오너 확정)

| # | 결정 | 내용 |
|---|---|---|
| D1 | 레이아웃 | `stack` / `center` / `grid` 3종 신설 + 기존 렌더를 `compact` 로 보존 = 총 4종 |
| D2 | 데이터 모델 | `info.items[]` 구조 불변. `info.layout` 키 하나만 추가 |
| D3 | `grid` 의 설명글 | **렌더 안 함** (레퍼런스 그대로 번호 + 라벨). 단 `desc` 값은 `info` 에 보존 |
| D4 | 선택 UI | 블록 모달 폼 상단 칩 토글 4개. `내용` 목록은 한 줄 그대로 |
| D5 | 문구 출처 | 결정론 사전 우선 + 미매칭만 LLM 1콜 (하이브리드) |
| D6 | 생성 시점 | 상세페이지 생성 잡에서 1회. 분석 폼 UX 불변 |
| D7 | 문체 | 합니다체 (레퍼런스 그대로) |

## 3. 데이터 모델

```js
info = {
  layout: 'stack' | 'center' | 'grid' | 'compact',
  items: [{ title, desc, src }],   // 기존과 동일, 2~5개
}
```

- `layout` 이 없으면 `'compact'` 로 읽는다 → **기존 문서의 블록은 그대로 렌더된다.** 마이그레이션 0건.
- 새로 만드는 블록의 기본값은 `'stack'`.
- 레이아웃을 갈아끼워도 `items` 는 손대지 않는다. `grid` 로 갔다가 `stack` 으로 돌아오면 설명글이 그대로 살아난다(D3).
- `presetTypeOf` 역매핑은 `infoType: 'benefit_copy'` 그대로 — 레이아웃은 블록 종류가 아니라 블록 안의 옵션이다.

## 4. 레이아웃 4종 (캔버스 폭 1000, 콘텐츠 60~940)

공통: 계약 §3.5 의 기존 primitives(`text` / `shape` / `line` / `image`)만 쓴다. 캔버스 렌더러·`page_assembler`·다운로드 경로는 건드리지 않는다.

### 4.1 `stack` — 세로형 (기본값)

```
DETAIL POINT                     ← Cal Sans 28, 좌, tracking
┌─────────────────────────────┐
│          사진 880×560         │
└─────────────────────────────┘
하이웨이스트 디자인                ← 22 semibold, 좌
하이웨이스트라 다리가 더 길어 보입니다.  ← 15, #4a4a45, lineHeight 26, 좌
   (포인트마다 반복, 간격 64)
```

- 헤딩은 블록당 1개, 맨 위에만.
- 사진 슬롯 고정 880×560. 비면 기존 이미지 슬롯 placeholder(의류 탭에서 채움).

### 4.2 `center` — 중앙형

```
┌─────────────────────────────┐
│          사진 880×620         │
└─────────────────────────────┘
        ▓ DETAIL POINT 01 ▓      ← 회색 배지(rect #f5f5f5) + Roboto Mono 13, 중앙
          밴딩 웨이스트            ← 22 semibold, 중앙
   허리에 밴딩을 넣어 착용이 편합니다.   ← 15, 중앙, lineHeight 26
   (포인트마다 반복, 간격 80)
```

- 배지 폭은 텍스트 길이와 무관하게 고정 200 (번호가 2자리로 안 간다 — 상한 5개).

### 4.3 `grid` — 2열 그리드

```
┌───────────┐ ┌───────────────┐
│  사진      │ │ 01            │   ← 번호 Roboto Mono 20, 좌상단
│  400×400  │ │ ──            │   ← 밑줄 line 24px
│           │ │               │
└───────────┘ │ 베이직 카라 디자인 │   ← 15, 카드 하단 좌측
              └───────────────┘
   (행마다 반복, 행간 24)
```

- 좌 사진 400×400, 우 카드 440×400(`#fafafa`), 사이 여백 40.
- `desc` 는 렌더하지 않는다(D3). `info` 에는 남는다.

### 4.4 `compact` — 기존 렌더 보존

`buildFeatureIcons` 현행 코드를 이름만 바꿔 그대로 옮긴다. 원형 사진 가로 나열 + `POINT N` + 제목 + 선택 설명. 동작·좌표·높이 계산 **한 글자도 바꾸지 않는다** — 기존 문서 재현성이 걸려 있다.

### 4.5 높이 계산

`stack` / `center` 는 포인트당 600~700px 이라 5개면 3,000px을 넘는다. 넘침을 막는 규칙:

- **사진 높이는 고정 상수.** 이미지 dims 로 유도하지 않는다(`_image_box` 의 파손 dims 방어와 같은 이유).
- **텍스트만 `estLines`** 로 줄 수를 재서 높이에 더한다. 기존 `estLines(text, width, size)` 재사용.
- 포인트 상한 5개(`FEATURE_ITEMS_MAX`)가 이미 있어 발산하지 않는다.

## 5. 설명 문구 생성

### 5.1 흐름

```
analysis.sellingPoints[]  (셀러 칩, 최대 5)
        │
        ▼  detail_page_job 카피 단계에서 1회
feature_copy.generate()
   ├─ 1단계 사전 lookup (결정론)     … 히트 → 문구 확정
   └─ 2단계 LLM 1콜 (미스만 묶어서)   … 실패 → desc 빈칸 (잡은 성공)
        │
        ▼
결정론 출력 필터 (금지어·길이·종결 — 위반 항목만 폐기)
        │
        ▼
analysis.featureCopy = [{ point, desc }]   (repo.save_analysis)
        │
        ▼
Editor buildInfoCtx → ctx.featureCopy → defaultInfoFor('feature_icons') → items 프리필
```

이 단계는 프로젝트의 `copywriting` 이 켜져 있을 때만 돈다 — 카피 단계 전체가 `if copywriting:` 안에 있고, 문구 생성을 끈 셀러에게 생성 문구를 밀어 넣지 않기 위함이다. 끈 채로 생성하면 `featureCopy` 가 쓰이지 않아 설명이 빈칸으로 남는다(버그 아님, 셀러가 직접 입력).

### 5.2 사전 (`_DETAIL_COPY`)

레퍼런스의 어휘축은 **부위·구조**다: 하이웨이스트 · 지퍼 · 밴딩 웨이스트 · 카고 포켓 · 조절 스트랩 · 플리츠 안감 · 카라 · 햄라인 · 소매 커프스 …

기존 `selling_points._CUES` 는 **소재·핏 감성어**를 canonical 영문 큐로 바꾸는 사전이고 용도가 이미지 프롬프트 주입 방어다. 목적이 달라 **재사용하지 않고 별도 사전**을 만든다. 룩업 방식은 `materials.py` / `selling_points.py` 와 같은 결정론 alias 패턴(전체 exact → 긴 alias 우선 부분일치)을 따른다.

초기 규모 30~40 항목. 운영자가 늘린다(임베딩 도입 아님).

문장 자체는 `humanize-korean` 스킬을 통과시켜 확정한다. AI 티(번역투·기계적 병렬·hype 어휘)가 남은 문구는 사전에 넣지 않는다.

### 5.3 단정 수위 (AG-02 계약 §단정 금지의 연장)

| 판정 | 예 |
|---|---|
| 허용 — 구조가 원인인 시각 효과 | "하이웨이스트라 다리가 더 길어 보입니다" |
| 허용 — 확인된 구조 서술 | "측면에 카고 포켓을 더했습니다" |
| **금지** — 미확인 기능성 단정 | "통기성이 좋아 시원합니다", "구김이 가지 않습니다" |

사전 문구는 작성 시점에 이 규칙으로 걸러 넣고(테스트가 상시 감시), LLM 생성분은 프롬프트 금지 조항 + **결정론 출력 필터**로 이중으로 막는다.

AG-03(`copy_qc.review`)은 쓰지 않는다. 검수 대상이 "금지 어휘 · 60자 · 합니다체 종결"이라는 닫힌 규칙 집합이라 문자열 검사로 전부 잡히고, 여기에 LLM 검수를 한 번 더 얹으면 잡 지연만 두 배가 된다. 반대로 컷 카피(AG-02)는 판정 기준이 열려 있어 AG-03이 계속 필요하다 — 그쪽 경로는 그대로 둔다.

### 5.4 셀러 입력 보호

- **제목은 만들지 않는다.** `items[].title` 은 셀러가 친 칩 문자열 그대로다. 생성 대상은 `desc` 한 줄뿐이라 `featureCopy` 항목은 `{point, desc}` 두 필드다.
- 서버는 `analysis.featureCopy` 에만 쓴다. 셀러가 입력한 `sellingPoints` · `aiSuggestedPoints` 는 **읽기만** 한다.
- `save_analysis` 는 REPLACE 시맨틱이라, 셀러 클라이언트가 다음에 분석을 저장하면 `featureCopy` 가 지워진다. `repo._SERVER_OWNED_ANALYSIS_KEYS` 에 `"featureCopy"` 를 추가해 이월시킨다 — `sourceMirrored` 와 같은 구멍이다.
- 잡이 도는 동안 셀러가 분석을 고칠 수 있으므로, 기록 시점에 `get_analysis` 로 **다시 읽어** `featureCopy` 만 얹는다. 잡 시작 때 읽은 사본으로 덮으면 그 사이 편집이 날아간다.
- 셀러가 칩 문구를 고치면 그 항목의 캐시는 무효 — `point` 문자열이 달라져 매칭이 안 되고, 다음 상세페이지 생성 때 다시 만들어진다.
- 에디터에서 포인트를 직접 추가하면 `desc` 는 빈칸이다(수동 입력). 클라이언트에 사전을 복제하지 않는다.

## 6. 변경 파일

### 프론트

| 파일 | 변경 |
|---|---|
| `src/features/editor/presets/infoPresets.js` | `buildFeatureIcons` → `layout` 4분기. `defaultInfoFor('feature_icons')` 에 `layout:'stack'` + `ctx.featureCopy` 프리필 |
| `src/features/editor/InfoBlockModal.jsx` | `FeatureIconsForm` 상단 레이아웃 칩 4개. `grid` 선택 시 설명 입력칸 비활성 표시(값 유지) |
| `src/features/editor/ContentPanel.jsx` | `feature_icons` 스키매틱 썸네일을 `stack` 모양으로 교체 |
| `src/features/editor/Editor.jsx` | `buildInfoCtx` 에 `featureCopy: analysis?.featureCopy` 한 줄 |

### 서버

| 파일 | 변경 |
|---|---|
| `server/app/agents/feature_copy.py` (신규) | 사전 룩업 + LLM 폴백 + JSON 스키마 |
| `server/prompts/feature_copy_v1.txt` (신규) | few-shot 프롬프트 (사전 문구 6~8개를 예시로) |
| `server/app/workers/detail_page_job.py` | 카피 단계에 `feature_copy` 1콜 + `save_analysis` 로 `featureCopy` 기록 |
| `server/app/repo.py:281` | `_SERVER_OWNED_ANALYSIS_KEYS` 에 `"featureCopy"` 추가 (§5.4) |

`page_assembler.py` 는 건드리지 않는다 — 특징 포인트 블록은 지금도 클라이언트 `applyInfoTemplate` 이 만든다.

## 7. 검증

### `tests/frontend/editor-info-presets.test.mjs` (기존 파일에 추가, `pnpm test:frontend`)

- 레이아웃 4종 각각: 요소 y 좌표 단조 증가(겹침 없음), 선언 높이 ≥ 마지막 요소 하단
- `grid` ↔ `stack` 왕복 시 `desc` 보존
- `layout` 키가 없는 레거시 `info` → `compact` 로 렌더 (기존 스냅샷과 동일)
- 슬롯 사진 서수 이월(`carrySlotImages` · `applySlotFillToInfo`)이 4종 모두에서 성립

### `server/tests/test_feature_copy.py`

- 사전 히트가 결정론적(같은 입력 → 같은 문구, 호출 0회)
- 미스만 LLM 에 넘어가고, 히트 항목은 프롬프트에 포함되지 않음
- LLM 실패(`VisionError`) 시 `desc` 빈칸으로 통과 — 잡은 죽지 않음
- 금지 표현(§5.3)이 생성분에 들어오면 해당 항목 폐기

### 회귀

- `server/tests/test_page_assembler.py` 그린
- 기존 정보 블록 테스트 전부 그린

## 8. 리스크

| 리스크 | 대응 |
|---|---|
| `stack`/`center` 블록이 3,000px 넘게 길어짐 | 사진 고정 높이 + 텍스트만 `estLines`, 포인트 상한 5개 (§4.5) |
| 카피 생성 실패가 상세페이지 생성을 막음 | 카피는 게이트 아님 — 기존 AG-02 패턴 그대로 실패 시 빈칸 |
| 기존 문서의 특징 포인트 블록 렌더 변형 | `layout` 미지정 → `compact`, `compact` 코드 무수정 (§4.4) |
| LLM 콜 추가로 인한 과금 변경 | 텍스트 tier 1콜, 기존 카피 콜과 같은 잡 안. 크레딧 정책 변경 없음 |
