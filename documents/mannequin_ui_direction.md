# 마네킹컷 페이지 UI 방향서 (as-built)

> 페이지: **"의류 재현성 높이기"** — 셀러 옷의 핏 구현도를 확인·보정하는 단계. 확정된 핏(+매칭)은 garment_ref(`fitProfile`)로 저장돼 이후 컷에 재사용.
> 구현: `src/features/mannequin/Mannequin.jsx` + `Mannequin.css` (커밋 `bbdb261~`). 흐름 정답지 목업: **`documents/mockups/mannequin-ui-matching.html`**(방식 1) · `mannequin-ui-compose.html`(구성 A) · `mannequin-ui-center.html`(이미지 중심 기반). 이전 `mannequin-ui-b2.html`(좌/우 분할·잠금 캐러셀)은 **폐기**.
> 데이터·API 계약: `documents/fit_profile_spec.md` · 에셋 `public/assets/fit-examples/` · 매핑 `src/lib/fitExampleImages.js`(폴백 포함).

## 0. 확정 규칙 (as-built)

1. **AI 추정값을 "선택된 것처럼" 노출 금지.** `fitProfile.axes` 초깃값이 있어도 UI엔 안 보인다. 질문은 "OO을(를) 조정할까요?"뿐.
2. **이미지 중심.** 가운데 큰 컷(내 옷 = **매칭 하의까지 입은 모습**). "내 옷" 태그·원본 사진 행·테두리 **없음**(은은한 그림자만). 아래 버전 썸네일 스트립(클릭 = `selectMannequin`).
3. **순차 확인 스텝 = 핏 · 기장 · … + (상의/아우터면) 매칭 의류 핏.** 스텝마다 **[유지하기] / [조정하기]**(둘 다 중립색). '조정하기'를 누르면 이미지 **오른쪽에 예시가 세로로**(테두리 박스 + 우측 스크롤바, 참고용). 현재 스텝만 질문을 노출.
4. **매칭 의류 핏은 first-class 순차 스텝(방식 1).** 마네킹컷에 하의가 함께 보이므로 조정 시 **재생성(유료)** 이며 `fitProfile.matchCut`(garment_ref)에 저장된다. 스텝 노출 조건 = **상의/아우터 카테고리 + 매칭 의류를 실제 선택한 프로젝트**(`analysis.matchClothing`에 selected 존재 — 컷에 하의가 없는데 하의 핏을 묻지 않기 위함). — *구 b2안의 "매칭은 중심 질문 금지·밑줄 링크로만"·"매칭 단독 변경은 무료·saveAnalysis 즉시 저장"은 **폐기**.*
5. **확인 항목 칩(카드 상단).** 모든 스텝을 칩으로 표시 — 완료 = **파랑 채움 칩**(✓ · "OO 유지" / "OO → 값" · [수정]), 예정 = **고스트 칩**(min-height로 공간 미리 확보 → 버튼 밀림 방지). [수정]을 누르면 그 스텝을 다시 조정(changing).
6. **전부 확인 → 최종 카드.** 변경 ≥1건 → `수정사항 반영하여 재생성 · N 크레딧`(N = `CREDIT_COSTS.mannequinGenerate` = 2, 백엔드 `credit_cost_mannequin_generate` 미러. 재생성 후 스텝 리셋·재확인 루프). 변경 0건 → `상세페이지 구성방식을 선택해주세요` + **[간단형 / 기본형 / 확장형]** 카드 → `이 구성으로 만들기`(`composeMode` store 반영 후 `/create/storyboard` 이동).
7. 예시 타일엔 **"선택됨" 상태 금지**(참고용) — 좌상단 "예시" 점선 태그. 예시 컬럼 헤더는 단일 문구 **"원하는 OO의 예시를 선택해주세요."**(medium).

## 1. 문구·스타일

- 질문 제목: "의류 핏을 조정할까요?" / "기장 길이를 조정할까요?" / "매칭 의류의 핏도 조정할까요?" — 제목 **16.5px·700**(아래 선택지보다 크게).
- 버튼: **유지하기 / 조정하기** (둘 다 중립 — 초록 배경 없음).
- 이 카드(확인 카드)의 글씨 **굵기는 한 단계 낮춤**. 확인 칩·[수정]은 **파랑(`--link`)** 계열.
- 구성 카드 라벨 **15px·600**, **추천 뱃지 없음**. 설명은 간략(mock `composeModes.desc`: 간단형 "한 컬러만 간단히" / 기본형 "대표 컬러 중심으로" / 확장형 "여러 컬러 자세히").
- **토큰만 사용**(하드코딩 hex 금지). 악센트는 `--link`(파랑)·`--fg/--bg/--ring`, CTA는 기존 primary 니어블랙(`Button variant="primary"`).

## 2. 컴포넌트 구조 (as-built)

```
Mannequin
 ├─ PageHead ("의류 재현성 높이기" / "실제 의류와 비슷해지게끔 조정해보세요.")
 ├─ .fit-stage (flex 가운데; changing이면 [이미지 | 예시열] 한 쌍)
 │   ├─ <MineColumn>       큰 컷 + 버전 스트립 (태그·원본행·테두리 없음)
 │   └─ (changing 시) .fit-ex-col   헤더 + <ExampleTiles> 세로 스크롤(테두리 박스)
 └─ .fit-ask (확인 카드)
     ├─ .fit-doner        스텝 칩(완료=파랑 / 예정=고스트, min-height로 공간 확보)
     └─ 현재 스텝 질문+[유지/조정]  |  조정 중 바 + [취소]  |  최종(재생성 or 구성선택+만들기)
```

### 상태머신
- 스텝 = `axesFor(category,gender)` 축 + (상의/아우터) `__match`(예시는 pants `cut`). 각 스텝: `pending → keep | changing → picked`.
- `changeStep`→changing(예시 옆에 뜸), `pickStep`→picked, `keepStep`→keep, `cancelStep`→pending, `editStep`→changing. 현재 스텝 = 첫 미완료. 전부 done이면 최종.

### 데이터 연결
- `buildFitProfile()`: 축 picked → `axes[key]`, 매칭 picked → `profile.matchCut`. keep은 draft 값 유지. `source`=picked 있으면 'seller'.
- 재생성 = `regenerateMannequin(projectId,{fitProfile})` — 프로필이 `matchCut`까지 포함해 garment_ref로 저장(mock: `DB.analysis.fitProfile` / http: 서버 `mannequins:regenerate`가 analysis에 영속 → 워커 `generation_spec(analysis)`이 읽음). 성공 시 새 버전 자동선택 + 스텝 리셋(pending) 재확인 루프.
- `createFitProfileDraft`가 재진입 시 `existing.matchCut` 복원. 매칭 미선택 프로젝트에선 `buildFitProfile`이 stale `matchCut`을 제거.
- 재생성 **이중 제출 방지**: `submittingRef`(동기 가드) + 버튼 `disabled={busy}`.
- **matchCut 백엔드 소비(완료)**: `server/app/agents/fit_axes.py build_fit_profile_block`이 `matchCut`을 pants.cut 카탈로그 고정 문구("matching bottom (the separate bottom garment …)")로 렌더. 매칭 하의 이미지가 없는 잡에선 `mannequin.effective_fit_profile`이 `matchCut`을 제거(없는 옷 지시 방지). 워커의 매칭 하의 탐색(`main_match_item_id`)은 계약형 `matchSelections` → **레거시 `matchClothing`(selected+selOrder, 실 프론트 저장 형식) 폴백** — 이 폴백이 없으면 UI가 받은 matchCut이 생성에서 조용히 무시된다. 회귀 = `tests/test_mannequin_fit_profile.py`.
- **컷 생성(AG-06)도 fitProfile 소비(완료)**: `cut_generator.render_cut_prompt`가 `analysis.fitProfile`을 동일 카탈로그 블록으로 렌더(FIT PROFILE → PRODUCT CONTEXT 순, 프로필 있으면 레거시 `- Fit:` 생략). 소비처 2곳 모두 배선 — 상세페이지(`detail_page_job`, 기존)와 **에디터 새 컷(`editor_image_job` mode:'new' — analysis 미로드로 fitProfile이 유실되던 것 수정)** — 선택 마네킹컷 **이미지(1번 참조) + 텍스트 제약 이중 전달**로 순종률 확보(컷 파이프라인 계약). matchCut은 매니페스트에 마네킹 참조나 MATCH 첨부가 있을 때만(없는 하의 지어냄 방지). 확정(무변경) CTA는 이동 전 `saveAnalysis({fitProfile})` **await**(저장→생성 순서 보장). 회귀 = `tests/test_cut_generator.py`.

## 3. 접근성
- 예시·버전 썸네일·구성 카드 = `<button>`(키보드 조작) · 이미지 `alt` · 예시 컬럼 `role=listbox` / 타일 `role=option`.

## 4. 에셋 현황 (fitExampleImages.js와 일치)

있음 36장: top(여 fit5·len4 / 남 fit3) · pants(여 cut5 / 남 cut4 / 공용 len3) · skirt sil3 · dress sil3·len2 · outer fit2·len2.
**갭(텍스트 폴백으로 동작, 추가 생성 백로그)**: top-men semi_over · pants-men slim/straight · skirt length 전부 · dress a_line/midi · outer regular/semi_over/basic.
