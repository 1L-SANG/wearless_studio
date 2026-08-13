# 마네킹컷 페이지 UI 방향서 (as-built)

> 페이지: **"의류 재현성 높이기"** — 셀러 옷의 핏 구현도를 확인·보정하는 단계. 확정된 핏(+매칭)은 garment_ref(`fitProfile`)로 저장돼 이후 컷에 재사용.
> 구현: `src/features/mannequin/Mannequin.jsx` + `Mannequin.css`. 과거 순차 질문 카드 목업(`documents/mockups/mannequin-ui-*.html`)은 현재 흐름의 정본이 아니다.
> 데이터·API 계약: `documents/fit_profile_spec.md` · 에셋 `public/assets/fit-examples/` · 매핑 `src/lib/fitExampleImages.js`(폴백 포함).

## 0. 확정 규칙 (as-built)

1. **AI 추정값을 "선택된 것처럼" 노출 금지.** `fitProfile.axes` 초깃값이 있어도 UI엔 안 보인다. 사용자가 직접 고른 값만 강조한다.
2. **이미지 중심.** 가운데 큰 컷(내 옷 = **매칭 하의까지 입은 모습**). "내 옷" 태그·원본 사진 행·테두리 **없음**(은은한 그림자만). 아래 버전 썸네일 스트립(클릭 = `selectMannequin`).
3. **조정은 이미지 핫존에서 바로 시작.** 핏·기장 등 지원 축을 마네킹컷 위에 항상 표시한다. 핫존을 누르면 이미지 오른쪽에 해당 축의 예시 패널이 열리며, 넓은 화면에서는 고정 패널, 좁은 화면에서는 인라인 세로 패널로 표시한다.
4. **매칭 의류 핏도 first-class 핫존.** 실제 선택된 메인 매칭 의류가 지원 대상이면 해당 조정 축을 추가하고, 값은 `fitProfile.matchingFit`(garment_ref)에 의류 id와 함께 저장한다. 조정 반영은 다른 축과 마찬가지로 **유료 재생성**이다.
5. **선택 상태와 취소.** 예시 타일을 고르면 타일과 핫존에 선택값을 표시한다. 선택한 핫존을 다시 열면 현재 타일이 강조되며, `선택 취소`로 이번 재생성에 넣을 변경만 철회한다.
6. **CTA는 항상 이미지 아래에 노출.** 변경 ≥1건이면 `수정 반영 · 2 크레딧`, 변경이 없으면 `이대로 진행 · N 크레딧`이다. 실제 모델 선택 시 이용료를 덧붙인다. 사진 양은 앞선 분석 확인 화면에서 이미 선택된 상태다.
7. **진행 중 조작 잠금.** 저장·생성 요청이 시작되면 CTA, 핫존, 예시 타일, 닫기·선택 취소를 모두 비활성화해 제출 직후 바꾼 값이 유실되지 않게 한다. `submittingRef`로 같은 요청의 동기 이중 클릭도 막는다.

## 1. 문구·스타일

- 패널 제목: "원하는 OO의 예시를 선택해주세요." 아래에 참고 안내 1줄을 둔다. 축은 "예시에 보여지는 의류의 OO만", 매칭은 해당 의류의 핏·실루엣·기장만 참고하도록 설명한다.
- 선택값은 핫존 툴팁과 패널의 `선택: 값` 행에 함께 표시한다.
- **토큰만 사용**(하드코딩 hex 금지). 악센트는 `--link`(파랑)·`--fg/--bg/--ring`, CTA는 기존 primary 니어블랙(`Button variant="primary"`).

## 2. 컴포넌트 구조 (as-built)

```
Mannequin
 ├─ PageHead ("의류 재현성 높이기" / "실제 의류와 비슷해지게끔 조정해보세요.")
 └─ .fit-stage (flex 가운데; changing이면 [이미지 | 예시열] 한 쌍)
     ├─ <MineColumn>       큰 컷 + <FitHotspots> + 버전 스트립 + 비용 CTA
     │                     핫존은 몸 위 부위에 얹는다: 상의 = 왼쪽 겨드랑이(몸통 핏)·오른쪽 겨드랑이(소매 기장)·밑단.
     │                     소매 기장은 상의 전용 축(민소매/반팔). 하의 계열(아우터 밑단·바지 통·스커트 실루엣)은
     │                     힙 주변에서 서로 48px 이상 벌려 겹침을 막는다(테스트 mannequin-fit-hotspots).
     └─ (changing 시) .fit-ex-col   헤더 + 선택값/취소 + <ExampleTiles>
          · 콘텐츠 폭 ≥1100: 페이지 우측 absolute 패널(제목 높이부터, 2열 그리드) — 마네킹 불이동
          · 그 미만: 이미지 옆 인라인 세로 스크롤 박스 (720 이하는 가로 스택)
```

### 상태머신
- 스텝 = `axesFor(category,gender)` 축 + 지원되는 메인 매칭 의류의 `__match` 축. 각 스텝: `pending ↔ changing ↔ picked`.
- `openAdjustmentExamples`→changing, `pickStep`→picked, `resetStep`→changing+선택값 없음, `closeAdjustmentExamples`→picked 또는 pending.

### 데이터 연결
- `buildFitProfile()`: 일반 축 picked → `axes[key]`, 매칭 축 picked → `profile.matchingFit`. 선택하지 않은 축은 기존 draft 값을 유지하고, 어떤 축이든 직접 골랐으면 `source='seller'`로 둔다. 레거시 `matchCut`은 반환하지 않는다.
- 재생성 = `regenerateMannequin(projectId,{fitProfile})`. 성공하면 새 버전을 자동 선택하고 핫존 선택 상태를 pending으로 초기화한다.
- 재진입 시 `createFitProfileDraft`가 계약형 `matchingFit`을 복원한다. 현재 매칭 의류와 id·카테고리가 맞지 않는 오래된 값은 제거한다.
- 확정(무변경) CTA도 이동 전에 `saveAnalysis({fitProfile})`를 기다려 저장→생성 순서를 보장한다. 저장·재생성 모두 `submittingRef`와 `busy`로 이중 제출 및 진행 중 조정을 막는다.

## 3. 접근성
- 핫존·예시·버전 썸네일·CTA는 `<button>`으로 키보드 조작이 가능하다.
- 핫존은 열림/선택 상태를 `aria-pressed`와 접근 가능한 이름으로 알리고, 예시 컬럼은 `role=listbox`, 타일은 `role=option` + `aria-selected`를 사용한다.
- `prefers-reduced-motion: reduce`에서는 핫존 점·툴팁을 포함해 전환과 패널 진입 애니메이션을 제거한다.

## 4. 에셋 현황 (fitExampleImages.js와 일치)

있음 36장: top(여 fit5·len4 / 남 fit3) · pants(여 cut5 / 남 cut4 / 공용 len3) · skirt sil3 · dress sil3·len2 · outer fit2·len2.
**갭(텍스트 폴백으로 동작, 추가 생성 백로그)**: top-men semi_over · pants-men slim/straight · skirt length 전부 · dress a_line/midi · outer regular/semi_over/basic · top sleeve sleeveless/short.

## 5. 생성 대기 화면 (2026-07-13 확정 — 의류 인포그래픽 롱 시퀀스)

- **퍼센트·진행바·A/B 스켈레톤 폐기** — 체크포인트 정체가 "17% 멈춤" 실패 오인을 만들던 구조 제거. 상태 문장 2개만: "상품의 형태를 살펴보고 있어요"(최소 4s) → progress≥35 "마네킹컷을 정교하게 다듬고 있어요". 40s 경과 시 "이미지 품질을 확인하고 있어요…" 추가. 실패는 기존 ErrorState.
- **무대 = 다음 화면과 동일한 3:4 중앙 프레임** (연속성). 의류 2D 인포그래픽이 주인공(카테고리 연동: top/outer=티, pants, dress/skirt=원피스) — 마네킹·사진·체크배지는 검토 후 폐기.
- **시퀀스**: 인트로 1회(재단 그리드 스케치 → 밑선/본선 제도 드로잉 → 원단 웨이브 리빌) → 12s 루프(재봉 바늘 도트가 봉제선 주행: 사이드→밑단→넥, 완료마다 스파클 팝 → 핏 화살표 성장 → 기장 자 하강 → 마감 광택+글로우 바운스+피날레 스파클). 상시: 부유(바닥 그림자 역위상)·배경 블루 글로우 펄스.
- **색 규율**: 무채색 + `--link` 블루는 측정 가이드·바늘·스파클·글로우(생성 하이라이트)에만.
- 접근성: `role=status`, `prefers-reduced-motion`이면 완성 정지 화면. mock 생성 9s(실서버 체감 근사). 초기 시안: `documents/mockups/mannequin-loading-v2.html`(로컬 전용, 정본은 컴포넌트).
