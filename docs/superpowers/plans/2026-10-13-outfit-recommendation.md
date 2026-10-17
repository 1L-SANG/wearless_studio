> 2026-10-17 이식: 이 문서는 10/13 승인 계획의 보관본이다. 단일 추천·진입 시점 등 충돌하는 계약은 [ADR-0022의 10/17 개정](../../adr/0022-full-outfit-recommendation.md) 및 `documents/common_data_contract.md`를 따른다.

# 전체 코디 추천과 실제 아이템 생성 연결 구현 계획

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. 승인된 사용자 구현 요청과 ADR-0022을 따른다. 커밋과 푸시는 하지 않는다.

**Goal:** 분석 진입의 Gemini 코디 추천, 서버 실제 아이템 선택, 생성과 재시도 일관성.

**Architecture:** 기존 matching_items와 analyses JSON 계약을 확장한다. 후보 검증, 역할별 선택, 추천과 생성 스냅샷을 분리하며 레거시 매칭 한 벌을 보존한다.

**Tech Stack:** Vite React JSX, CSS tokens, FastAPI Python, 기존 Postgres repo, Gemini generateContent.

**Spec:** docs/adr/0022-full-outfit-recommendation.md

## Global Constraints

- 기존 미커밋 색 조합 작업과 무관한 파일을 보존한다. 현재 체크아웃의 codex/full-outfit-recommendation 브랜치를 사용한다.
- 커밋, 푸시, 운영 DB 변경, 유료 AI 호출을 하지 않는다. 테스트는 모델과 외부 서비스를 대체한다.
- 역할별 계약과 유료 호출 한도는 ADR-0022 그대로 적용한다. 빈 카탈로그에 가짜 자산을 추가하지 않는다.
- 사용자가 직접 올리는 것은 기존 보완 의류 한 벌뿐이다.
- 구현과 검증 에이전트는 gpt-6.1-sol xhigh. 여러 저장·생성 경로와 권한 검증을 다루기 때문이다.
- API와 DB 호출은 컴포넌트 밖 서비스 계층에 둔다. 화면의 코드 정보 노출과 구현 용어를 피한다.
- 디자인은 기존 DESIGN.md, AnalysisForm, refero-design craft 참조를 고정한다. keep-all, 토큰, 기존 모바일 PC 이어하기를 유지한다.

## Review Focus

- 오래된 AI 응답이 사용자 선택을 덮지 않는가.
- 타 사용자 또는 비활성 아이템이 선택 저장이나 생성 참조로 들어가지 않는가.
- 부분 선택과 의도적인 비움이 승격, 새로고침, 재시도에서 살아남는가.
- 실제 선택이 프롬프트의 기존 흰 이너와 고정 신발 지시로 바뀌지 않는가.
- 새 역할 선택이 레거시 매칭 핏, matchIds 한 벌 상한, 제품 단독컷을 깨뜨리지 않는가.

## Task 1: 서버 추천과 선택 저장

소유: server/app/services/outfit.py, server/app/agents/outfit_recommender.py, server/prompts/outfit_recommendation_v1.txt, server/app/config.py, repo.py, routes.py, public_routes.py, workers/analyze_job.py, 관련 서버 테스트.

인터페이스: ADR의 outfitSelections와 outfitRecommendation. 공용 및 소유 프로젝트 카탈로그 조회, 최초 공개·인증 분석 장식, 선택 PATCH의 검증과 레거시 동기화. 생성 담당 요청에 맞춰 routes의 스냅샷 enqueue 지점을 함께 연결한다.

- [x] 실패 테스트: 역할 분기, subCategory 문맥, user/null 고정, 허위 ID와 private item 거부, empty 무호출.
- [x] Gemini 구조화 1회 호출, 후보 이미지 제한, timeout과 fallback, 키 기반 합류.
- [x] 최초 공개 core와 전체 분석, 인증 분석에 자동 추천 연결. 공개 승격과 서버 소유 필드 보호.
- [x] 선택 저장·카탈로그 갱신·generation snapshot 검증 테스트.

## Task 2: 분석 UI와 로컬/HTTP 상태

소유: src/features/analysis/**, src/lib/outfitSelections.js, src/lib/api/**, src/mock/**, src/features/product-input/**, src/lib/draftSync.js, src/lib/generationRelevance.js, 관련 프론트 테스트. 이전 색 추천 변경 보존.

인터페이스: 원격 분석의 outfit fields 보존. 기존 matchClothing을 보완 한 벌로 유지하며 새 역할을 별도로 표시. 새 슬롯 업로드 없음. 사용자 origin과 빈 선택 유지. 이후 생성 요청의 선택 snapshot 전달은 서버가 검증한다.

- [x] 실패 테스트: 최초 자동 추천, empty catalog, role 선택, stale 응답, draft 승격.
- [x] 기존 화면을 확장하고 API와 목 데이터에서 같은 selection 규칙 사용.
- [x] 색·카테고리·종류 수정의 응답 동기화와 사용자 선택 고정.
- [x] 생성 관련 변경 판정과 콘티 프리페치 무효화.
- [x] 관련 프론트 테스트, 빌드, 브라우저 검증.

## Task 3: 실제 생성 참조와 기록

소유: server/app/services/outfit_generation.py, workers/mannequin_job.py, workers/detail_page_job.py, workers/editor_image_job.py, agents/mannequin*.py, agents/cut_generator.py, agents/content_roles.py, agents/context_fill.py, agents/horizon_background.py, agents/garment_edit.py, generic/mannequin prompts, 관련 테스트.

인터페이스: 검증된 outfitSelectionsSnapshot. 추가 이미지 표현은 list[tuple[str, InlineImage]], 역할은 inner, footwear, bag, accessory이고 legacy complementary matching은 기존 이미지 인자로 유지한다. build_manifest/render prompt에 outfit_roles를 optional keyword로 전달한다. confirmed packet에는 outfit_images=tuple[(role,InlineImage)]를 optional keyword로 전달한다. 제품컷은 빈 목록. 실제 참조는 ID를 DB에서 조회한다.

- [x] 실패 테스트: snapshot 고정, mannequin inner만, 모든 착용컷 역할 refs, product 제외.
- [x] 공유 resolver와 role 순서, fresh/edit 경로, 실제 생성 recipe 기록.
- [x] 재시도와 비슷한 컷 상속, 과거 사진 착장 보존.
- [x] 부모 호환, 중복 제거, 지문과 선택 변경 분리.

## Task 4: 확정 GPT와 검수 계약, 통합

소유: root가 agents/confirmed_gpt_prompt.py, confirmed_gpt_runtime.py, confirmed_gpt_directing.py, cut_output_qc.py, 확정 template, 해당 테스트, PRD/common_data_contract 문서를 담당한다.

- [x] 실제 역할 이미지를 입력 packet에 명시하고 무참조 v1 호환 유지.
- [x] 실제 선택이 fixed inner/footwear보다 우선하도록 명시. 예시 소품 차단 유지.
- [x] QC는 각 역할을 실제 reference로 읽고 matchingCount와 섞지 않음.
- [x] 통합 테스트, 이미 알려진 baseline 실패 구분, 최종 리뷰, UI QA.

## 진행 기록

2026-10-13: 사용자 승인과 후속 설명 반영. 서버 카탈로그만 사용, 매칭 한 벌만 업로드. 3개 독립 영역 탐색 완료. 이전 기능과 미커밋 변경을 유지하려고 현 체크아웃에서 새 feature 브랜치로 이동했다. 작업간 공유 파일은 routes.py는 서버 담당, confirmed/QC는 root, 나머지 생성은 생성 담당으로 고정했다.

2026-10-13 완료: 세 담당 구현과 root 확정 생성·QC 통합을 검증했다. 독립 리뷰의 문맥 승격, fit/material 전달, 과거 AI 선택 고정 문제를 수정하고 재검토했다. 서버 전체 9379개 통과, 기존 로컬 DB 오류 105개 유지. 프론트 297개 파일 중 293개 통과, 기존 인증 테스트 4개 파일 실패 유지. 분류별 추천 지침 추가 후 추천·라우트 56개 통과, 최종 빌드 통과. 유료 모델 호출과 실제 생성 품질 검증은 실행하지 않았다. 브라우저 5개 폭의 overflow와 스크린샷, 선택 복원·종류 분기를 확인했다. 별도 Safari와 글자별 자동 줄바꿈 측정은 도구 지원 범위 때문에 미실행이다. 상세 기록은 .scratch/outfit-recommendation-20261013/progress.md.
