# 마네킹컷 페이지 UI 방향서 (구현 인수인계용)

> 목적: 사용자 확정 목업(순차 질문 + 캐러셀 예시)을 실제 `Mannequin.jsx`에 이식.
> 확정 목업(인터랙션 정답지): **`documents/mockups/mannequin-ui-b2.html`** (저장소 내 보존 — 브라우저로 열어 동작 확인 가능). 목업이 없어도 아래 §0(절대 규칙)+§2(상태머신)만으로 구현 가능하도록 서술함.
> 관련: `documents/fit_profile_spec.md`(데이터·API 계약) · 에셋 `public/assets/fit-examples/` · 매핑 `src/lib/fitExampleImages.js`(폴백 포함, 완성).

## 0. 사용자 확정 요구 (절대 규칙)

1. **AI가 축 값을 추정해 "선택된 것처럼" 보여주지 않는다.** "AI 추정: 레귤러" 류 표기 금지. 질문은 "이대로 괜찮나요?"뿐. (데이터상 fitProfile.axes 초기값은 있어도 UI에 노출 안 함)
2. **축마다 [괜찮아요, 그대로 →] / [OO 바꿀래요] 를 먼저 선택.** 바꾸기 전엔 예시 캐러셀이 **비활성(흐림 opacity .32 + grayscale + pointer-events:none)**. '바꿀래요' 후 활성.
3. **순차 진행이 자연스럽게 CTA로 흘러야 함**: 축들을 위→아래로 확인하면 하단 CTA 박스가 활성화. 변경 0건 → `좋아요, 다음 단계로 →`(기존 다음 단계 네비 그대로) / 변경 ≥1건 → `다시 생성하고 계속 · 1 크레딧`(재생성 후 새 버전 자동선택).
4. **매칭 의류는 중심 질문 금지** — CTA 아래 작은 밑줄 링크 `매칭 의류(하의)도 수정하기`로만. 누르면 하의 컷 캐러셀 조용히 펼침. 선택하면 완료행+수정 버튼.
5. **예시 선택 후엔 반드시 [수정] 버튼** — 카드가 "핏 → 오버로 변경 ✓" 완료행으로 접히고, 수정 누르면 캐러셀 재오픈.
6. **버전 썸네일 스트립 유지** — 큰 컷 아래 작은 썸네일(v0·v1·…) 가로 배치, 클릭 시 해당 버전 선택(기존 P2 히스토리 기능 재사용).
7. 예시 타일엔 **"선택됨" 상태 표시 금지**(참고용 이미지임을 유지) — 좌상단 "예시" 점선 태그 필수. 큰 컷만 "내 옷".

## 1. 미감 방향 (기존 서비스와 조화)

- **토큰만 사용**: 색·라운드·그림자 전부 `tokens.css` 변수. 하드코딩 hex 금지. 악센트는 기존 Sky(`--ring` 계열), 성공은 기존 성공색.
- **기존 컴포넌트 재사용**: `Button`/`Chips`/`Modal`/`toast`(components/ui.jsx), `PageHead`/`WizardCTA`(shell). 새 시각 요소는 Mannequin.css에만 추가(P2가 만든 파일).
- **카드 문법 통일**: 기존 `.surface` 카드와 같은 결(라운드 12~16, 옅은 보더, 절제된 그림자). 목업의 파랑 그림자/그라데이션 CTA 박스는 **기존 서비스 대비 과하면 완화** — 활성 카드는 보더 accent + 얕은 그림자 정도로.
- **좌(내 옷) 시각 강조**: 큰 컷 보더 2px accent + "🧍 내 옷 · AI 생성" 필 태그. 원본 사진 미니 행(썸네일+파일명) 유지 — "큰 컷=내 옷, 타일=예시" 구분이 이 페이지 핵심 문법.
- 문구 톤: 기존 서비스처럼 해요체·간결 ("괜찮아요, 그대로 →", "수정은 무료", "다시 생성 · 1 크레딧").

## 2. 레이아웃 & 컴포넌트 트리

```
<MannequinPage>
 ├─ PageHead ("마네킹컷 확인" / sub "괜찮으면 그대로, 다르면 바꿔주세요 · 수정 무료")
 └─ .fit-stage (grid: .85fr 1.15fr, gap 20, 모바일 1열)
     ├─ <MineCard>  (sticky top)
     │   ├─ 태그 "🧍 내 옷 · AI 생성"
     │   ├─ 큰 컷 이미지 (선택된 버전)
     │   ├─ <VersionStrip>  ← 요구 6 (기존 히스토리 재사용: 클릭=selectMannequin)
     │   └─ 원본 사진 미니 행
     └─ <AxisFlow>
         ├─ <AxisCard key=fit> ... (카테고리×성별에 맞는 축만, fitAxes.axesFor)
         ├─ <AxisCard key=length> ...
         └─ <CtaBox>
             ├─ 상태 문구 + 메인 CTA (규칙 3)
             ├─ 링크 "매칭 의류(하의)도 수정하기" (규칙 4, 매칭 있을 때만)
             └─ <MatchBox> (접힘; 캐러셀 or 완료행)
```

### AxisCard 상태머신 (목업 JS와 동일)
`pending → (keep | changing → picked)` + 렌더 규칙:
- 현재 스텝만 확장(active, 보더 accent), 이후 스텝은 접힘+흐림(opacity .45), 완료 스텝은 한 줄 완료행(✓ · "그대로 두기"/"OO(으)로 변경" · [수정]).
- changing이면 캐러셀 활성, 아니면 locked(흐림) + 힌트 "바꾸려면 'OO 바꿀래요'를 먼저 눌러주세요".
- 캐러셀: 타일 flex 46% snap, 좌우 원형 nav 버튼, 타일=이미지(fitExampleImage(), null이면 텍스트 전용 타일) + 라벨 + "예시" 점선 태그.

### 상태 → 데이터 연결
- draft fitProfile은 P2의 것 재사용: keep=축 값 유지(변경 플래그 false), picked=axes[key]=value·source='seller'.
- "다시 생성" = 기존 `regenerateMannequin(projectId,{fitProfile})` 그대로. 성공 시 새 버전 히스토리 추가·자동 선택(P2 로직 유지), 카드 상태 리셋(pending) 또는 완료 유지 — **리셋(pending)으로**: 새 컷을 다시 확인하는 루프가 자연스러움.
- "다음 단계로" = 기존 페이지의 다음 단계 이동 로직(WizardCTA/네비) 재사용 — 새 라우팅 발명 금지.
- 매칭 하의 픽 = fitProfile에 별도 키(`matchAxes.cut`) 추가하지 말고 **spec 확인**: 현재 계약엔 매칭 프로필 없음 → mock에서는 regenerate 파라미터에 `matchCut` 하나 추가(경량, httpAdapter 스왑 전 mock 전용) + 주석으로 계약 후속 표기.

## 3. 구현 체크리스트 (이어받는 에이전트용)

- [ ] Mannequin.jsx: FitProfilePanel(P2) → AxisFlow/AxisCard/CtaBox로 교체 (위 상태머신)
- [ ] MineCard: 큰 컷 + VersionStrip(기존 히스토리) + 원본 미니 행 (원본 = product 첫 Front 이미지 URL, mock은 placeholder 함수 재사용)
- [ ] Mannequin.css: 목업 스타일을 토큰으로 번역해 추가 (하드코딩 hex 금지)
- [ ] 캐러셀 locked/active, 텍스트 폴백 타일, "예시" 태그
- [ ] CTA 문구 스위칭 + 매칭 링크/박스
- [ ] `pnpm build` 통과 + 흐름 코드리딩 검증 (생성→확인→변경→재생성→새 버전 확인)
- [ ] 커밋 (이 문서 체크박스 갱신)

## 4. 에셋 현황 (fitExampleImages.js와 일치)

있음 36장: top(여 fit5·len4 / 남 fit3) · pants(여 cut5 / 남 cut4 / 공용 len3) · skirt sil3 · dress sil3·len2 · outer fit2·len2.
**갭(텍스트 폴백으로 동작, 추가 생성 백로그)**: top-men semi_over · pants-men slim/straight · skirt length 전부 · dress a_line/midi · outer regular/semi_over/basic.
