# 컨템포러리 브랜드 컷 구조 조사 — 합의 프로토콜 (Claude × Codex ultra)

> 2026-07-23. 오너 지시: 쿠어·COS·레스겟모어·엘무드(+지브·뉴앤비 확인 중) 등의 최근·인기 상품을 카테고리별 조사해 브랜드별 컷 구성 패턴을 발굴한다. 목표는 검증이 아니라 **새 인사이트로 같은 공간 세트를 구조화**하는 것.
> 설계 절차: Claude 초안 → Codex ultra(GPT-5.6-sol) 독립 설계 + 반박 → 본 합의안. 1차 조사(소호 3몰 29상품 268컷, documents/cut_sequence_market_research.md)의 후속.

## 0. 합의된 핵심 원칙 (Codex 반박 수용분)

1. **PDP(상세페이지)와 캠페인/룩북은 끝까지 별도 코호트** — 합산 금지. 캠페인 80장이 PDP 8장을 삼키지 않게 이미지 수가 아니라 **상품·캠페인·세트 단위**로 집계.
2. **관찰값과 해석값 분리** — 코더(비전 에이전트)는 보이는 것만 기록. anchor/variation 같은 역할(cutRole)은 코딩 입력이 아니라 **세트 생성 후 규칙으로 파생**(기대 서사 주입 방지).
3. **"인기"의 엄격한 정의** — 공식 베스트 순위/페이지/배지만 인정. 리뷰 수는 상품 나이의 대리값이라 설명 변수로만. 없으면 `popularity unavailable`로 정직하게 기록. 결과 문구는 "인기 상품"이 아니라 "수집 시점 공식몰 베스트 노출 상품".
4. **sceneId(장소 인스턴스)와 runId(연속 구간) 분리** — 같은 장소가 다른 장면 뒤 재등장하는 경우 대응.
5. **브랜드 선정에 기술 접근성 오염 금지** — 티어 기준으로 먼저 정하고, 접근 실패는 누락으로 보고.
6. **캠페인→PDP 압축 규칙(Bridge)** — 두 코호트에 재사용된 이미지를 연결해 "긴 촬영 세션에서 어떤 컷이 판매 페이지로 살아남는가"를 별도 분석. (Codex 독창 기여 — 세트→콘티 시드 번역에 직결)
7. **아키타입 채택 규칙** — 중복(색상웨이·재사용) 제거 후 3개 이상 상품 + 2개 이상 브랜드에서 관찰돼야 일반 아키타입. 한 브랜드 전용이면 brand-specific grammar, 1~2건은 rare pattern. 이름은 군집 확정 후 마지막에.
8. **Wearless 번역은 별도 단계** — 관찰 빈도와 제품 채택을 한 표에서 결정하지 않음. 아키타입마다 spaceGroupId·full/medium·pose/bg 자산·지지물 의존성(ADR-0009 제약)으로 적용성 평가 후보만 도출, 승격은 오너 결정.

### 실용적 다운스케일 (Codex 원안 대비 — 사유 명시)
- 이중 코딩: 전 표본 20%가 아니라 **파일럿 6상품 이중 코딩**으로 코드북 안정화 → 본조사는 단일 코딩 + Codex 자동 이상치 검출 + 불일치 재판정. (n≈48 규모에서 학술급 신뢰도 절차는 비용 대비 과잉)
- 클러스터링: k-medoids/bootstrap 대신 **토큰화→반복 모티프 채굴→지지도 규칙** 중심. 거리 기반 군집은 표본이 커지는 2라운드부터.
- 신뢰도 지표: kappa 산출은 하되 0.80 컷오프 강제 대신 낮은 필드를 `exploratory` 강등하는 운용.

## 1. 표본

### 브랜드 (6곳)
| 브랜드 | 성별 | 채널 | 수집 방법 | 인기 축 |
|---|---|---|---|---|
| 쿠어 COOR | 남+여 | coor.kr (Cafe24) | 목록=browser-use, 상세=curl(클러스터 조정 필요) | 베스트 페이지 확인 중 |
| 엘무드 LMOOD | 남+여 | lmood.co.kr (Cafe24) | sitemap 전 상품 + curl 검증됨(16컷) | 목록 정렬 확인 |
| 레스겟모어 | 남 | lessgetmore.com (Cafe24) | curl 검증됨(15컷) | **BEST 카테고리 확보(24종)** |
| COS | 남+여 | cos.com/ko-kr | **browser-use 전면**(curl 차단) | 베스트셀러 섹션 |
| 드로우핏 | 남 | 1차 데이터 재사용 | 코딩 완료분 편입 | — |
| 비교 브랜드 1곳 | — | 티어 기준 선정(오너 승인) | — | — |
| (지브·뉴앤비) | — | **채널 미특정 — 오너 확인 필요** | — | — |

### 상품 (PDP 코호트): 브랜드 × 카테고리(아우터/상의/하의/스커트·원피스) × {신상 1위, 베스트 1위} ≈ **40~48개**
- 카테고리 부재 시 `structural NA`(억지 충원 금지). 신상=베스트 중복 시 한 번 코딩 + 다음 순위 보충.
- 색상웨이 병합은 파일 해시·perceptual near-duplicate 증거로만 (겉보기 판단 금지).

### 캠페인 코호트: 브랜드당 최신 시즌 룩북/캠페인 1~2개 (쿠어 /collection/*.html·레스겟모어 /lookbook/ 확인됨)

## 2. 코딩 스키마 v2 (프레임 관찰값)

`sourceRegion`(detailBody/lookbook/campaign) · `order` · `directionRaw`(front/threequarter/side/back/indeterminate) · `sourceShot`(full/knee/medium/closeup/garmentDetail/flatlay/other — 서비스 값 full|medium은 파생 필드) · `pose` 원자 단위(자세 standing/sitting/walking·지지물 none/wall/chair/other·손 free/pocket/garmentTouch/prop) · `sceneObservables`(장소 유형·주요 소품·조명 한줄) · `garmentCoverage`(실루엣/앞뒤옆/여밈/밑단/소재 노출 — "왜 이 컷이 남았나" 분석용) · `colorwayId`/`modelId` · `confidence`.

**경계값**(인접 쌍): 배경/조명/모델·스타일링 동일성 + 판정 same/probable/uncertain/different → 사후에 sceneId·runId 파생.

## 3. 실행 순서 (역할 분담 — Codex 합의)

```
[Claude] 카탈로그 프레임 수집(브랜드·순위·시각 보존) → 표본표 고정
[Claude] 수집(curl+browser-use, DOM 순서·해시·manifest 보존)
[Claude] 파일럿 6상품 이중 비전 코딩 ──→ [Codex] 신뢰도·혼동표 → 코드북 v1 동결
[Claude] 본조사 비전 코딩(상품별 독립 에이전트)
[Codex]  스키마 검증·중복(pHash)·scene/run 파생·모티프 채굴·지지도 집계
[Claude] 대표·반례 이미지 육안 재판정
[Codex]  적대적 검토(표본·과장·누수)
[공동]   아키타입 레지스트리 동결 → Wearless 적용성 매트릭스 → 오너 결정
```

## 4. 산출물
표본 manifest·코드북(버전 고정)·브랜드별 PDP 패턴 카드·캠페인 패턴 카드·**캠페인→PDP 압축 지도**·아키타입 레지스트리(포함규칙·지지도·반례)·Wearless 적용성 매트릭스·제품 스펙 후보 2~3안(오너 승격 대기).

## 5. 현재 상태 (2026-07-23)
- 접근성 스카우트 완료: 쿠어(카테고리 전체 매핑, 목록 JS→browser-use 확인), 엘무드(sitemap+16컷 추출), 레스겟모어(BEST 24종+15컷 추출), COS(차단→browser-use 필요), 룩북 페이지 추출 가능 확인(쿠어 26이미지).
- 미결: 지브·뉴앤비 채널 특정(오너), 비교 브랜드 승인, 쿠어 상세 클러스터 규칙 조정, 파일럿 착수.
