# QA6 — baseline vs SAM2 vs 3D-ish

6벌 · 3방식 · 24 image calls · 18 AI QC 판정 (각 3샘플, temp 0) · 새 SAM2 15뷰.

## QC tally

| method | PASS | FAIL | UNVERIFIABLE |
|---|---|---|---|
| baseline | 4 | 0 | 2 |
| sam2 | 4 | 0 | 2 |
| stage3d | 2 | 2 | 2 |

UNVERIFIABLE 2건은 두 하의(brown-pants, brown-skirt2)이고 **세 방식 모두 동일**하다. 원인은 입력
전략이 아니라 QC 설계다 — `neckline`·`sleeveConstruction`이 hard check인데 바지·치마에는 존재하지
않아 구조적으로 판정 불가. 즉 **현재 AI QC는 하의를 판정할 수 없다.**

## 방식별 육안 결론

| 의류 | category | views | 추천 |
|---|---|---|---|
| 여성용 시어 반팔 | top | F/B/D | **SAM2** (실루엣) |
| 얇은 회색 니트 | top | F/B | 둘 다 비슷 |
| 갈색 면바지 | bottom | F/B/D | 추가 확인 필요 (QC 불가) |
| 갈색 치마 2 | bottom | F/B/D | 추가 확인 필요 (QC 불가) |
| 빨간 가디건 | outer | F/B | **SAM2** (넥라인) |
| 베이지색 가디건 | outer | F/D | 둘 다 비슷 |

SAM2가 baseline보다 **나쁜 케이스는 0건**. 이긴 2건은 모두 구조 속성이었다.

## 핵심 질문 답

**1. 기존 vs SAM2 — 어느 쪽이 product fidelity가 좋은가**
AI QC로는 **구분 불가**(둘 다 PASS 4). 육안으로는 SAM2가 2/6 우세, 4/6 동등, 0/6 열세.
- `red-cardigan`: 원본은 라운드넥·단추 목까지. baseline이 **V넥으로 바꿨고**, sam2는 맞췄다.
- `sheer-tee`: baseline이 원본의 박시한 실루엣을 슬림하게 만들었고, sam2가 더 가깝다.

**그리고 이게 이번 실험에서 제일 중요한 발견이다: AI QC가 red-cardigan baseline의 넥라인 오류를
PASS로 통과시켰다.** 13체크·3샘플·fail-closed인데도 놓쳤다. QC를 단독 게이트로 믿으면 안 된다.

**2. Front만 vs Front+Back+Detail**
이번 배치에 Front-only arm이 없어서 **직접 답할 수 없다.** 시사점만: SAM2가 이긴 2건은 모두 Back
컷아웃을 가진 케이스였고, Front 컷아웃만 있던 beige-cardigan은 동등했다. 근거는 약하다.

**3. 카테고리별**
- 상의·아우터: SAM2가 우세하거나 동등. 우세는 둘 다 **구조 속성**(넥라인, 실루엣)에서 나왔다.
- 하의: **결론 없음.** QC가 판정 불가고, 육안으로도 세 방식이 비슷했다.
- 이전 6벌 A/B의 "셔츠→AUGMENTED, 블라우스/레이스→RAW" 경향과 정면 충돌하지는 않지만,
  이번엔 니트·가디건에서 RAW가 이긴 사례가 없었다.

**4. 3D-ish 중간 표현이 도움이 되는가**
**아니다.** 2/6 FAIL(sheer-tee: 색이 채도 높은 핑크로 이동 + 포켓 뭉개짐 / red-cardigan: V넥 +
오버사이즈), 나머지 4/6은 동등. 도움이 된 품목은 **하나도 없었다.** 중간 보드가 정체성을 한 번 더
해석하면서 오차가 누적된다 — 이전 2벌 스파이크(스트라이프·4ff)에서 좋아 보였던 결과는 재현되지
않았다.

## 다음 구현 방향 추천

1. **SAM2 arm으로 간다.** 손해 사례 0, 구조 속성에서 이득. 단 이득 폭이 작아 전면 도입보다는
   상의·아우터부터.
2. **3D-ish는 접는다.** 6벌 중 이득 0, 손해 2. 추가 생성 호출 1회를 정당화할 근거가 없다.
3. **QC를 먼저 고쳐야 한다.** 두 가지 결함이 이번에 드러났다:
   - 하의에 대해 구조적으로 판정 불가 (neckline/sleeveConstruction hard check)
   - 넥라인이 바뀐 컷을 PASS로 통과 (red-cardigan baseline)
   QC가 방식 비교의 심판인데 심판이 구분을 못 한다. 여기가 다음 작업 1순위다.
4. **자동 마스크 선택은 여전히 미해결.** 이번 컷아웃 10개는 전부 사람이 골랐다
   (HUMAN_SELECTED_EXPERIMENTAL_MASK). brown-pants Front는 SAM2가 바지를 다리별로 쪼개
   컷아웃 자체를 못 만들었다.

## 기록해야 할 제약

- **confound**: sam2/stage3d arm에는 컷아웃뿐 아니라 슬롯 권한 문구(Front=전면 지오메트리 /
  Back=후면 지오메트리 / Detail=소재·부속 전용)도 함께 들어갔다. baseline은 프로덕션 프롬프트
  그대로다. 즉 두 arm은 **증거 + 문구** 두 가지가 다르다. 분리하려면 4번째 arm이 필요하다.
- 컷아웃 미생성 사유는 전부 기록했다: sheer-tee/brown-pants/brown-skirt2/beige-cardigan의
  Detail은 실루엣이 없는 매크로 크롭, brown-pants Front는 SEGMENTATION_SPLIT_PARTS.
- 성별 근거는 `여성용_시어_반팔` 하나뿐. 나머지 5벌은 파일명에 성별 토큰이 없어 여성 베이스를
  기본값으로 썼고, 추론하지 않았다.
- 폴더명은 카테고리 근거로 쓰지 않았다 — `아우터/`에 청바지가 들어 있다. 파일명 토큰만 사용.
