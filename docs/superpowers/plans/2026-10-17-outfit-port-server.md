# ADR-0022 최신 main 이식과 서버 계약 개정

> **For agentic workers:** Use superpowers:subagent-driven-development. 사용자가 승인한 작업지시서 A와 ADR-0022를 실행한다. 커밋하지 않는다.

**Goal:** 10/13 전체 코디 구현을 289d7941 위에 이식하고 10/17의 세 벌·소품·백그라운드 추천 계약을 완성한다.

**Architecture:** 기존 matching_items, analyses JSON, 생성 recipe를 확장한다. 서버 정규화 지점에서 선택과 생성 스냅샷을 일관되게 만들고, 조회는 저장된 추천 또는 결정적 폴백만 반환한다. 프론트는 같은 계약의 서비스 정규화와 기존 화면 최소 호환만 구현한다.

**Tech Stack:** FastAPI Python, Vite React JSX, 기존 CSS tokens와 Zustand.

**Spec:** docs/adr/0022-full-outfit-recommendation.md; .scratch/WO-A-outfit-port.md

## Global Constraints

- 구현 모델과 effort: gpt-6-astra, xhigh.
- 이 worktree 밖 쓰기, 유료 API, 외부 네트워크, 브라우저, 패키지 설치, 커밋 금지.
- 마이그레이션을 만들거나 수정하지 않는다. 필요하면 중단해 보고한다.
- 최신 main의 누끼·정돈·색보정·상품 visuals·비슷한 컷·MD 코멘트를 보존한다.
- matchClothing, matchIds 한 벌 계약과 LIMITS.matchClothingMax를 유지한다.
- v18 화면은 B 범위다. A는 10/13 화면의 계약 호환만 구현한다.
- 컴포넌트는 src/lib/api/를 거친다. 기존 DESIGN.md와 한국어 keep-all, CSS 토큰을 유지한다.
- 작업 결과를 탭이나 패널로 자동으로 열지 않는다.

## Review Focus

- 조회·폴링·승격·동시 분석이 중복 유료 추천을 만들지 않는가.
- 늦은 AI 완료가 사용자 선택과 선택 저장을 덮지 않는가.
- 소품 토글 기억과 명시적 null이 세트 적용·승격·생성에서 일관되는가.
- 원본 recipe의 참조가 재시도·비슷한 컷·편집에서 유지되는가.
- 최신 main의 정돈 표시 자산이 생성용 원본 참조를 바꾸지 않는가.

### Task 1: 이식 및 10/17 계약 통합

**Files:** .scratch/outfit-port-1013/tracked-changes-vs-a90afdaa.diff에 나열된 관련 파일과 untracked-files.txt의 관련 새 파일. docs/adr/0022-full-outfit-recommendation.md는 사용자 정본이며 옛 ADR로 덮어쓰지 않는다.

**Interfaces:** ADR-0022의 outfitRecommendation.sets[{slots,reason}], candidates[].inSets, outfitSelections={version,slots,setIndex,optional:{bag:false,accessory:false}}. pending 조회에는 fallbackReady:true와 폴백 sets가 있다. 역할별 unavailable 신호를 서비스 레이어까지 전달한다. 생성 스냅샷은 켜진 소품만 포함한다.

- [ ] 10/13 diff와 untracked 파일을 검토하고 관련 부분만 최신 코드에 의미 단위로 이식한다. 제외 변경을 목록화한다. ADR 번호·경로와 옛 계획을 옮기고 계약 문서를 갱신한다.
- [ ] 기존 10/13 테스트를 옮긴다. 세 벌 검증·폴백·inSets, 세트 적용의 user null 유지와 직접 선택 교체, 소품 토글 기억, 스냅샷 제외, 저장 직후 추천 시작과 중복 합류, pending 폴백 및 조회 무호출의 실패 테스트를 먼저 추가한다.
- [ ] 서버 추천/정규화, 비동기 저장 후 시작, 공개 승격, 생성·확정 GPT·검수 참조를 구현한다. 1회 Gemini 응답으로 최대 세 벌을 검증하며 단일 추천 필드는 제거한다.
- [ ] 프론트 서비스·선택 유틸·목 상태를 맞춘다. 기존 화면에서 첫 세트를 표시하고, 늦은 결과는 셀러 선택을 보존한다. B에서 목 상태를 재현하는 방법을 문서화한다.
- [ ] 관련 테스트 후 전체 서버 `.venv/bin/pytest -q -p no:cacheprovider`, 프론트 `MOCK_API_LATENCY_SCALE=0 pnpm test:frontend`, `pnpm build`를 실행한다. .scratch/baseline 완료 로그와 실패·오류 식별자를 비교한다. 새 실패를 수정한다.
- [ ] 수정 파일·보존한 main 변경·제외 변경·세부 계약·실행 명령 및 숫자·미확인 항목을 한국어 보고서에 기록한다. 커밋하지 않는다.

### Task 2: 독립 통합 리뷰 및 최종 확인

- [ ] Task 1의 작업 diff, 보고서, 테스트 증거를 독립 리뷰어가 검토한다. 보안·상태 경합·snapshot/recipe 일관성과 스코프를 확인한다.
- [ ] 발견한 실제 결함은 구현 담당에게 돌려 수정하고 해당 검증을 실행한다.
- [ ] 최종 변경분 기준 전체 필수 검사 결과와 기준선 비교를 확인하고 사용자 보고를 작성한다.
