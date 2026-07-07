# 핏 프로필 구현 스펙 (마네킹 조정 개편)

> 설계 근거·검증: `documents/fit_axis_market_research.md` (§7 독립 리뷰 반영 포함). PRD §7.3~7.5 대체.
> 원칙: ①모든 축 기본값 = null = "사진 그대로"(자동 추정) — 패널은 확인·수정용, 필수 결정 0개 ②선언 시 사진 인상보다 우선(닻) ③프로필은 프로젝트에 지속돼 마네킹+하위 컷 전부 상속.

## 1. 데이터 계약

### FitProfile (프로젝트당 1개, 메인 의류 대상)
```js
// src/lib/types.js
FitProfile = {
  category: 'top'|'pants'|'skirt'|'dress'|'outer', // §2 유도 규칙
  gender: 'women'|'men',                            // select_base_gender와 동일 규칙
  axes: {                                           // 카테고리별 유효 키만. 값 null = "사진 그대로"
    fit?: string|null,        // top·outer
    length?: string|null,     // 전 카테고리
    cut?: string|null,        // pants 전용
    silhouette?: string|null, // skirt·dress 전용
  },
  source: 'auto'|'seller',   // auto=분석 추정 그대로, seller=하나라도 손댐
  version: 1,
}
```
- 저장 위치: `Analysis.fitProfile` (분석의 일부 — 분석 확정 시 함께 저장, 마네킹 페이지에서 수정 가능).
- 기존 `Analysis.fit` 필드 = `fitProfile.axes.fit`의 **초기 자동 추정값 공급원**으로 흡수. AnalysisForm의 '핏' pill은 유지하되 내부적으로 fitProfile을 씀(셀러에게 '핏' 개념이 두 번 보이면 안 됨).
- `AdjustFit`/`AdjustLength`/`MannequinCut.fitAdjust·lengthAdjust·matchAdjust` **폐기**. `MannequinCut.candidate` 폐기(단일컷), `version`은 유지(히스토리).
- 매칭 의류: 셀러 조절 없음 — 시드 메타(카탈로그의 fit)로 자동. matchAdjust UI·API 제거.

### 카테고리 유도 (fitProfileCategory)
`clothingType` + `subCategory`로 유도: top→top / bottom→(subCategory에 '스커트'·'치마' 포함 시 skirt, 아니면 pants) / dress→dress / outer→outer.

## 2. 축 카탈로그 (단일 정본 — 프론트·백 미러)

프론트 `src/lib/fitAxes.js` + 백엔드 `server/app/agents/fit_axes.py`. 항목: `{ value, label(셀러 한국어), promptEn(생성용 영어 구절) }`. 성별 키: `women`/`men`.

| category.axis | women | men |
|---|---|---|
| top.fit | tight/slim/regular/semi_over/over (타이트/슬림/레귤러/세미오버/오버) | slim/regular/semi_over/over |
| top.length | ultra_crop/crop/basic/long (울트라크롭/크롭/기본/롱) | crop/basic/long |
| pants.cut | skinny/slim/straight/bootcut/wide (스키니/슬림/일자/부츠컷/와이드) | slim/straight/tapered/relaxed/semi_wide/wide (슬림/일자/테이퍼드/릴렉스/세미와이드/와이드) |
| pants.length | above_ankle/ankle/below_ankle (발목 위/발목/발목 덮음) | 동일 |
| skirt.length | mini/midi/long (미니/미디/롱) | (남성 미노출) |
| skirt.silhouette | h_line/a_line/mermaid (H라인/A라인/머메이드) | (미노출) |
| dress.length | mini/midi/long | (미노출) |
| dress.silhouette | h_line/a_line/fit_and_flare/mermaid | (미노출) |
| outer.fit | slim/regular/semi_over/over | 동일 |
| outer.length | crop_short/basic/long (크롭·숏/기본/롱) | 동일 |

promptEn은 검증 생성에 쓴 구절 그대로 (예: pants.cut.wide = "a full, voluminous wide-leg silhouette; broad swinging columns, hem covering most of the shoes"). ⚠️ outer·dress는 생성 미검증(게이트) — 카탈로그엔 넣되 스펙에 🧪 표시.

## 3. 프롬프트 (백엔드)

- `render_mannequin_prompt`에 **FIT PROFILE 블록** 추가: 선언된(=non-null) 축만 영어로 나열. 전부 null이면 블록 생략.
```
FIT PROFILE (seller-declared; overrides any impression from the photos):
- cut: <promptEn>
- length: <promptEn>
```
- 템플릿 우선순위 재작성(현행 "핏 뷰 우선" 문구와 충돌 해소): **선언 프로필 > 핏 참고사진 > 사진 기본 추정.** `${baseFit}` 토큰·A/B용 `${candidate}` 제거.
- `candidate_specs()`·`_FIT_CONTRAST` 폐기 → 단일 spec(프로필 기반).

## 4. 생성 플로우 (단일컷 + 재생성 루프)

- 최초 진입: 자동 생성 **1장** (프로필=분석 자동 추정). 크레딧 placeholder: 장당 1 (기존 A/B 2장=2 → 1장=1, `CREDIT_COSTS.mannequinGenerate=1`로 조정·분석 CTA 라벨 갱신).
- 패널에서 프로필 수정 = **무료**. `다시 생성 · 1 크레딧` 버튼으로 재생성 → 새 버전이 히스토리에 추가·자동 선택. **횟수 제한 없음**(크레딧이 자연 제한, `ADJUST_LIMIT` 폐기).
- 히스토리 스트립 유지(버전별). 재생성 확인 모달 간소화(전부 교체 아님 — 버전 추가).
- 진행률·SSE·멱등 등 기존 generate 파이프라인 재사용. 백엔드 regenerate = 새 job(같은 파이프라인, 프로필만 갱신) — Phase 4.

## 5. UI (Mannequin.jsx 개편)

- A/B 구조 철거: `cutsA/cutsB` 분리·`candidate` 라벨·"후보 A/B" 문구 제거 → 단일 큰 카드 + 히스토리 스트립.
- AdjustPanel → **FitProfilePanel**: 카테고리×성별에 맞는 축만 렌더. 각 축 = 라벨 + 단계 pill(또는 소형 타일). 각 축 맨 앞 pill = `사진 그대로`(null, 기본 선택). 분석 자동 추정값이 있으면 해당 pill에 **`AI 추정` 뱃지**.
- 팬츠 `기장`은 접힌 보조 컨트롤(기본 '사진 그대로'). 매칭 의류 조정 카드 제거.
- 타일 이미지는 후속(백로그) — 1차는 pill 텍스트로 출시, 검증 생성분을 에셋화하는 건 별도 작업.

## 6. 단계별 구현 (커밋 단위)

1. **P1 계약+카탈로그+프롬프트**: types.js(FitProfile, 폐기 표기) · fitAxes.js · fit_axes.py · prompts.py(FIT PROFILE 블록) · mannequin.py(단일 spec) · mannequin_generate_v1.txt(우선순위 재작성) · 백엔드 테스트.
2. **P2 mock+프론트 개편**: mock api(generate 1장·regenerate(profile)·크레딧) · Mannequin.jsx(FitProfilePanel·단일컷·히스토리) · limits.js.
3. **P3 분석 통합**: AnalysisForm 핏 pill→fitProfile 배선 · 카테고리 유도 · CTA 크레딧 라벨.
4. **P4 백엔드 + 스모크 — 완료(2026-07-07)**: ①백엔드 신규 엔드포인트 **불필요 확인** — analysis 저장이 payload JSONB 병합 패스스루라 fitProfile이 그대로 흐르고, 재생성 = analysis PATCH(fitProfile) → 기존 마네킹 job POST(새 멱등키). httpAdapter 스왑 시 이 조합 사용. ②**상하의 동시 착장 스모크 통과**: production 프롬프트 경로(render_mannequin_prompt+fit_profile)로 상의 프로필(오버·크롭 vs 슬림·기본)+매칭 데님 동시 착장 — 프로필이 상의에만 적용되고 하의 다리라인은 사진 그대로(간섭 없음).

미검증 게이트(구현과 별개): 아우터·원피스 실물 이미지 확보 후 생성 검증 → 그때까지 해당 카테고리 축은 카탈로그에 있되 노출 여부 결정.
