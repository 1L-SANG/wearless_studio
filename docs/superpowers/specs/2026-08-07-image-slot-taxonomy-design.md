# 입력 이미지 슬롯 개편 — 앞면 / 뒷면 / 앞면 디테일 / 뒷면 디테일

- 날짜: 2026-08-07
- 상태: 확정 (오너 결정 반영)
- 관련: documents/common_data_contract.md §4 AngleSlot · documents/PRD.md §기준 색상 · documents/pl1_analysis_agent_spec.md §이미지 매니페스트

## 1. 배경과 실측 근거

현행 업로드 슬롯은 `Front / Back / Detail / Fit`(앞면·뒷면·디테일·착용) 4종.
실서버 121개 상품·이미지 313장 전수 조회(2026-08-07, Supabase read-only):

| 슬롯 | 올린 상품 | 비율 |
|---|---|---|
| Front | 121 | 100% |
| Back | 102 | 84% |
| Detail | 70 | 58% (2장 이상 13개) |
| **Fit** | **0** | **0%** |

- **Fit(착용)은 서비스 개시 이래 사용 0건.** 촬영이 없어서 이 서비스를 쓰는 셀러에게 착용 사진을 요구한 것 자체가 모순. 앞면 착용샷이 있는 셀러는 앞면 칸에 자연스럽게 올린다(오너 판단).
- 디테일은 현재 **방향 정보가 없어**, 뒷모습 컷(`direction: 'back'` — 콘티보드 상품컷에 방향 칩 기존재, `Storyboard.jsx:974`)을 그릴 때도 앞면 디테일이 "detail close-up" 라벨로 첨부된다 → **앞가슴 프린트를 등에 그리는 사고 경로**가 열려 있다.

## 2. 결정 요약 (오너 확정)

| # | 결정 | 내용 |
|---|---|---|
| D1 | 토큰 전략 | 기존 `Detail` 값 재사용(=앞면 디테일) + `BackDetail` 신설. **DB 마이그레이션 0건** — 기존 88장은 자동으로 앞면 디테일. |
| D2 | Fit 슬롯 | **완전 삭제** — 토큰·라벨·정렬·프롬프트 흔적까지 전부 제거. 남기면 헷갈린다. |
| D3 | 컷 첨부 규칙 | 방향 우선 + **같은 방향 원본 폴백**. 반대 방향 디테일은 어느 단계에서도 쓰지 않는다. |
| D4 | 필수 조건 | **앞면 + 뒷면 필수** (기준 색상 기준). |

방향이 애매한 디테일(옆면·원단 클로즈업)은 셀러가 넣은 칸이 곧 선언 — 별도 판정 로직을 두지 않는다. 앞면 디테일 라벨을 넓게(위치 비특정 허용), 뒷면 디테일 라벨을 좁게(뒷면 전용 못박기) 써서 흡수한다.

## 3. 슬롯 계약 (common_data_contract §4 AngleSlot 개정)

| 토큰 | 화면 라벨 | 필수 | 의미 |
|---|---|---|---|
| `Front` | 앞면 | ● | 앞모습 전체. 앞면 착용샷도 여기 |
| `Back` | 뒷면 | ● | 뒷모습 전체 |
| `Detail` | 앞면 디테일 | | 앞면 쪽 클로즈업. 옆면·원단 등 위치 비특정 클로즈업 포함 |
| `BackDetail` | 뒷면 디테일 | | 뒷면 쪽 클로즈업 (백넥·등판·뒷주머니 등) |

- `Fit` 토큰 폐기. 서버는 미지 slot을 강제 변환하지 않으므로(빈 값만 Front 폴백, `mannequin.py:88`) 잔존 데이터가 있어도 일반 라벨로 안전 통과 — 실서버는 0건.
- 첨부 정렬 `_SLOT_ORDER`: `Front(0) → Back(1) → Detail(2) → BackDetail(3)`. **기존 3종의 순서는 불변**(기존 상품 결과 재현성 유지), BackDetail만 말미 추가.
- 추가 색상(비기준 색상) 그룹은 현행 유지 — `Front` 고정, 최대 3장.

## 4. 입력 화면 (ProductInput)

2×2 우물 배치 — 세로축 = 앞/뒤, 가로축 = 전체/클로즈업:

```
[ 앞면 * ]        [ 뒷면 * ]
[ 앞면 디테일 ]    [ 뒷면 디테일 ]
```

- `catalogs.angleSlots = ['Front', 'Back', 'Detail', 'BackDetail']`, `angleLabels`는 위 표의 화면 라벨. 카탈로그는 클라이언트 전용(`src/lib/api/index.js:18`)이라 서버 배포와 독립.
- 필수 별표(`req-star`)를 Back에도 표시. 안내문 "앞면은 필수예요" → "앞면·뒷면은 필수예요".
- CTA 게이트: `hasFront` → `hasFrontAndBack` — **기준 색상**에 Front·Back 각 1장 이상. (현행은 "아무 색이나 Front"였으나, AI가 소비하는 것은 기준 색상 이미지이므로 판정 기준을 일치시킨다. 초안 복원 판정 `ProductInput.jsx:371`도 동일 기준으로.)
- 뒷면 칸 보조 문구: "뒷면이 없으면 뒷모습 컷을 만들 수 없어요" 톤의 이유 안내(이탈 완화, §9 리스크 참조).

## 5. 컷 생성 — 첨부 우선순위 (D3)

디테일 컷(`cutType: 'product'`, `shot: 'detail'`)은 블록의 `direction`(front/back)에 따라:

| 컷 방향 | 1순위 | 2순위 (색 전환) | 3순위 (구조 확대 모드) | 금지 |
|---|---|---|---|---|
| front | 같은 색 `Detail` | 타색 `Detail` | 같은 색 `Front` 원본 | `BackDetail` |
| back | 같은 색 `BackDetail` | 타색 `BackDetail` | 같은 색 `Back` 원본 | `Detail` |

- 2순위는 현행 `detail_reference_images`(`cut_generator.py:715`)의 타색 폴백을 **같은 방향으로 한정**해 확장한 것. 색 전환 프롬프트(`detailColorTransferLine`)는 현행 유지.
- 3순위 도달 시 **구조 확대 모드**: 전신 원본에서 명확히 확인되는 구조 요소(카라·단추·포켓·밑단·봉제선)만 골라 확대하고, 이 해상도에서 확인 불가한 원단 조직·자수는 그리지 않는다. 앞·뒷면이 필수이므로(D4) 3순위 근거는 항상 존재 → **디테일 컷은 항상 생성 가능**.
- `detail_reference_required` 실패는 "상품 이미지 로드 자체가 실패"한 경우로 의미가 좁아진다(게이트는 유지 — `cut_generator.py:487`, `editor_image_job.py:170`).

### 콘티보드 게이트 완화

- 디테일 컷 역할의 `requiresDetailImage`(`storyboardTaxonomy.js:109`) 게이트 폐기 — 디테일 컷은 항상 제공. `hasDetailSource`는 "정밀 모드 가능 여부" 표시 용도로만 잔존하거나 제거.
- 기본 콘티 구성(`shapes.js defaultStoryboard`)의 디테일 블록 포함 판정·`detailColor` 선택도 방향 인지로 갱신.
- 미세 패턴 원본 통과(`detail_page_job.py:482`, 체크·스트라이프 재현 불가 → 원본 그대로 사용)도 방향 매칭: front 디테일 블록 ↔ `Detail` 자산, back 디테일 블록 ↔ `BackDetail` 자산.

## 6. 프롬프트 · 매니페스트 라벨

고정 문자열 룩업 원칙(셀러 텍스트 미삽입 — 인젝션 방지) 유지. 4개 라벨 맵 공통 개정:

| 파일 | 대상 |
|---|---|
| `server/app/agents/cut_generator.py:757` `_SLOT_LABEL` | 컷 매니페스트 |
| `server/app/agents/feature_extractor.py:77` `_SLOT_LABEL` | 분석 관찰 가이드 |
| `server/app/workers/mannequin_job.py:72` `_SLOT_LABEL` | 마네킹 매니페스트 |
| `server/prompts/cut_generate_v1.txt:115` `[[SHOT:detail]]` | 디테일 컷 지시 |

라벨 방향성(영문 문안은 구현 시 확정, 의미는 다음을 고정):

- `Detail` — "front-side detail close-up. May also show fabric or trims whose location is not side-specific." (넓게)
- `BackDetail` — "back-side detail close-up (back neck, back yoke, back pocket). **This detail exists on the back only — never place it on the front.**" (좁게, 위치 못박기)
- `Fit` 라벨 4곳 전부 삭제.
- `[[SHOT:detail]]`은 2모드 분기: **정밀 모드**(디테일 사진 첨부 — 현행 "보이는 것만 재현") / **구조 확대 모드**(원본 폴백 — §5 3순위 지시). `cut_generator.py:487`의 라벨 존재 검사는 방향별 라벨·원본 라벨을 모두 인정하도록 수정.

## 7. 변경 지점 전수

**프론트**
- `src/lib/types.js:45` — `AngleSlot`: FIT 제거, BACK_DETAIL 추가 (키 이름 `DETAIL`→`DETAIL_FRONT` 병기 여부는 구현 재량, 값은 `'Detail'` 불변)
- `src/mock/db.js:96-97` — angleSlots/angleLabels 개정, `:397` Fit 시드 이미지 → BackDetail로 교체
- `src/features/product-input/ProductInput.jsx` — 2×2 배치·필수 게이트·문구 (§4)
- `src/lib/storyboardTaxonomy.js:109·176·347` — requiresDetailImage 폐기·hasDetailSource 정리
- `src/lib/api/shapes.js:121-122` — 방향 인지 detailColor

**서버**
- `server/app/agents/mannequin.py:11·84` — `_SLOT_ORDER` 개정, docstring 갱신
- `server/app/agents/cut_generator.py:487·715·757` — 게이트·타색 폴백 방향 한정·라벨
- `server/app/agents/feature_extractor.py:77` — 라벨
- `server/app/workers/mannequin_job.py:72` — 라벨
- `server/app/workers/editor_image_job.py:170` — 방향 인지 게이트
- `server/app/workers/detail_page_job.py:482` — 방향 매칭 원본 통과
- `server/prompts/cut_generate_v1.txt` — [[SHOT:detail]] 2모드
- `server/app/repo.py:118` — Front 우선 정렬 확인(변경 불요 예상, 검증만)

**문서** — `documents/common_data_contract.md:83·375`, `documents/PRD.md:145`, `documents/pl1_analysis_agent_spec.md:114·127`(화이트리스트 서술 포함), ADR 신설은 불요(본 스펙이 정본).

**테스트** — Fit 참조 제거(`test_cut_generator.py` 등), BackDetail 첨부·폴백 매트릭스(§5 표의 8칸), 구조 확대 모드 프롬프트 분기, 필수 게이트(기준 색상 Front+Back) 프론트 테스트.

## 8. 마이그레이션 · 배포

- **DB 마이그레이션 없음** (D1). 기존 `Detail` 88장 = 앞면 디테일로 자동 재해석.
- 프론트·서버 어느 쪽이 먼저 배포돼도 안전: 구 프론트가 보내는 `Detail`은 신 서버에서 앞면 디테일로 유효, 신 프론트의 `BackDetail`을 구 서버가 받으면 일반 라벨로 통과(기능 손실만, 오동작 없음). 단, 뒷면 필수 게이트는 프론트 단독 게이트이므로 순서 무관.
- 진행 중 초안: Back 없는 초안은 CTA가 잠기고 뒷면 1장 추가로 해소. 이미 분석을 마친 기존 프로젝트는 영향 없음(게이트는 입력 단계에만).

## 9. 리스크와 완화

| 리스크 | 실측 | 완화 |
|---|---|---|
| 뒷면 필수화 이탈 | 기존 121개 중 19개(16%)는 이 규칙이면 진입 불가였음 | 뒷면 칸 이유 안내(§4). 지표 관찰 후 필요시 재논의 |
| 구조 확대 모드 품질 | 신규 프롬프트 경로 — 미검증 | 구현 후 A/B 소규모 실생성 검증(기존 컷 eval 프로그램 활용) |
| BackDetail 미업로드 편중 | 뒷면 디테일 업로드율 미지수 | 3순위 폴백으로 컷은 항상 성립 — 품질 하한 보장 |

## 10. 비범위

- 콘티보드 기본 구성에 뒷면 디테일 컷 **기본 포함** 여부 — 별도 결정(현행: 셀러가 방향 칩으로 전환).
- 마네킹·분석 파이프라인의 소비 로직 변경 없음(첨부 순서·라벨만 갱신).
- 추가 색상 그룹의 슬롯 확장 없음.
