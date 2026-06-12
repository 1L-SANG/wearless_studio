# AI 품질 스파이크 하네스

backend_integration_plan.md §10 **0단계** 실험 도구. **일회용** — 서비스 코드(`src/`)와 무관하며, 스파이크 통과 후 폐기한다. 의존성 없음(Node 내장 fetch).

## 목적

인프라(Phase 0) 투자 전에 최대 리스크를 검증한다:
**Gemini 이미지 모델이 실제 상품 사진으로 마네킹 핏 재현·의류 동일성을 달성하는가? 비용·지연은 감당 가능한가?**

## 사용법

```sh
# 1. 키 설정 (없으면 자동 dry-run — 골격 검증용)
cp spike/.env.example spike/.env   # 스왑·마네킹·컷=GEMINI_API_KEY · 베이스 생성=OPENAI_API_KEY

# 2-a. 베이스 마네킹 생성 (gpt-image-2, 사진 폴더 불필요) — 확정본은 spike/base/에 고정
node spike/spike.js --scenario base --provider openai --gender female --n 2 --res 4K
node spike/spike.js --scenario base --provider openai --gender male --n 2 --res 4K

# 2-b. 의류 스왑 (Gemini) — 고정 베이스(2K 축소본)에 상품 사진 폴더의 옷을 입힌다
node spike/spike.js ~/photos/제품A --scenario swap --base spike/base/base-male-2K.png --type 티셔츠 --n 1 --res 2K

# 2-c. 기타 시나리오 (상품 사진 폴더 필요)
node spike/spike.js ~/photos/제품A                      # AG-04 마네킹 A/B 후보
node spike/spike.js ~/photos/제품A --scenario cut --cut-type product   # AG-06 컷

# 3. 평가
open spike/runs/<타임스탬프>/report.html
```

리포트에서 원본↔생성을 나란히 보고 체크리스트(핏 재현 · 의류 동일성 · 디테일 보존 · 색 정확도 · 비용 · 지연)를 채운다. 체크박스·메모는 브라우저에서 바로 입력 가능(저장은 스크린샷/인쇄로).

## 실험 포인트

- **프롬프트**: `spike.js`의 `buildBasePrompt` / `buildSwapPrompt` / `buildMannequinPrompt` / `buildCutPrompt`가 실험 대상. AG-04/AG-06 핵심 제약(ai_agent_modules.md)을 반영한 초안 — 고쳐가며 돌린다.
- **모델**: `spike/.env`의 `MODEL_ROUTING_IMAGE_HIGH`(Gemini) / `OPENAI_IMAGE_MODEL`로 교체.
- **비용 단가**: `spike.js` 상단 `USD_BY_RES` (gemini-3-pro-image 기준, 2026-06-12 가격표).
- Gemini 4K 응답은 이미지 파트가 2개(1K 프리뷰 + 4K 본체) — 가장 큰 파트를 채택한다(구현됨).
- OpenAI 안전 필터가 빈 마네킹 프롬프트를 sexual로 오탐할 수 있음 — 'as it ships from the manufacturer' 류 우회 문구 사용.

## 출력 구조

```
spike/runs/<타임스탬프>-<시나리오>/
  report.html        # 비교 + 체크리스트
  input/             # 입력 사진 사본
  result-A.png …     # 생성 결과 (실호출 시)
  prompt-A.txt …     # 사용 프롬프트
  request-A.json …   # 요청 미리보기 (base64는 바이트 수로 대체)
  meta.json          # 설정·지연·토큰 기록
```

## 통과 기준 (§10)

마네킹 핏 재현 + 의류 동일성이 상용 수준이고 비용·지연이 수용 가능하면 → Phase 0(FastAPI 골격) 진행. 미달이면 모델/프롬프트 재검토가 먼저다.

## 0단계 판정 (2026-06-12) — **조건부 통과**

실험: gpt-image-2로 남녀 베이스 마네킹 4K 확정(`base/`) → 실제 셀러 사진(푸마×AMI 파란 티셔츠, 앞/뒤 바닥샷)을 gemini-3-pro-image 스왑으로 착장. 총 4콜.

| 항목 | 판정 | 근거 |
|---|---|---|
| 스왑 메커니즘 (베이스 고정+의류 교체) | ✅ | 남녀 베이스 모두 마네킹 재질·무드 유지하며 착장 성공 |
| 의류 동일성 (색·구조·핏) | ✅ | 로열블루·크루넥·흰 넥테이프·레귤러핏 재현 |
| 로고/자수 | ⚠️ 근접 | v1 완전 변형 → 프롬프트 강화(v2: 상표 1:1 지시) 후 상용 근접. 1:1 완벽은 아님 |
| 구도 고정 (전신 프레이밍) | ⚠️ 비결정적 | 같은 v2 프롬프트로 남자는 전신 유지, 여자는 허벅지 크롭. 프롬프트만으론 보장 불가 |
| 지연 | ✅ | 스왑 31~66s/콜 |
| 비용 | ✅ | 2K $0.134/콜 (4K 베이스는 1회성 $0.24) |

**잔여 리스크 2건(로고 미세변형·구도 이탈)은 AG-P2(image-qc) 게이트 + 자동 재시도로 흡수하는 설계 필요** — 이미 ai_agent_modules.md에 예고된 모듈이며, Phase 4 설계 시 구도 검사 항목을 추가할 것. 추가 완화 후보: 로고 클로즈업 크롭을 입력에 자동 첨부.
