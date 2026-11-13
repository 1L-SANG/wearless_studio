# Wearless 문서 인덱스

Wearless는 패션 셀러가 상품 사진으로 상세페이지를 만들고 편집·다운로드하는 Vite·React SPA다. 백엔드는 FastAPI, Supabase와 R2를 사용한다. 요구사항은 현재 PRD·공통 데이터 계약·용어집과 해당 기능의 현행 ADR을 함께 읽는다. 계획·연구·체크포인트는 요구사항 정본이 아니다.

## 현재 정본

| 문서 | 내용 |
|---|---|
| [PRD.md](PRD.md) | 화면·생성 흐름·정책·제품 요구사항 |
| [common_data_contract.md](common_data_contract.md) | 엔티티·enum·API·크레딧·멱등 계약 |
| [CONTEXT.md](../CONTEXT.md) | 도메인 용어집 |
| [DESIGN.md](../DESIGN.md) | 셀러 디자인·모바일 입력 정책·AI 캡션과 수동 텍스트 기준 |
| [현재 ADR](../docs/adr/) | 기능별 결정과 이유. 대체된 결정 대신 현행 본문을 적용 |
| [frontend_state_model.md](frontend_state_model.md) | 서버 상태·전역 선택값·화면 로컬 상태의 소유권 |
| [backend_integration_plan.md](backend_integration_plan.md) | 저장·API·작업 실행·크레딧 트랜잭션의 경계 |
| [ai_pipeline_spec.md](ai_pipeline_spec.md) | 현재 분석·마네킹·상세페이지·에디터 파이프라인 |
| [ai_agent_modules.md](ai_agent_modules.md) | 현재 AI 모듈의 입력·출력·호출 책임 |
| [editor_text_presets.md](editor_text_presets.md) | 텍스트 프리셋 값·안내 문구 정리·카피 슬롯 계약 |
| [mannequin_ui_direction.md](mannequin_ui_direction.md) · [fit_profile_spec.md](fit_profile_spec.md) | 단일 마네킹컷·버전·핏 설정·재생성 |
| [genexamples_release_contract.md](genexamples_release_contract.md) · [space_set_release_contract.md](space_set_release_contract.md) | 생성예시와 촬영 세트의 발행·소비 계약 |
| [credit_system_design.md](credit_system_design.md) | 크레딧 원천·차감·구독·추가 구매의 구현 계약 |
| [TODOS.md](../TODOS.md) | 루트의 현재 할 일과 구현 갭 |
| [CLAUDE.md](../CLAUDE.md) | 공통 작업 규칙 |

## 읽는 순서

- 제품·기획: PRD, CONTEXT, 해당 기능의 현행 ADR.
- 프론트: 공통 데이터 계약, 프론트 상태 모델, 해당 PRD 화면. 실제 API 경계는 `src/lib/api/`다.
- 백엔드: 공통 데이터 계약, 저장·API·작업 실행 계약, AI 파이프라인. 실제 검증은 `server/app/schemas.py`와 해당 라우트·워커를 확인한다.
- 디자인: DESIGN, PRD §14, `src/styles/tokens.css`와 실제 앱 화면.
- 콘티·생성예시: PRD §8, ADR-0026(템플릿 콘티, ADR-0005 대체), ADR-0004·0006~0011, 생성예시·촬영 세트 릴리스 계약. 템플릿, 섹션 디자인, 조립 엔진의 구조는 `src/lib/pageTemplates/README.md`를 함께 읽는다. 디테일은 ADR-0012·0014, 호리존 배경 맞춤은 ADR-0013도 읽는다.
- 에디터 텍스트: PRD §10.11, editor_text_presets.md, 현재 프리셋과 조립 코드.

## 구현 경계

화면은 `src/lib/api/`를 통해 데이터를 읽고 쓴다. HTTP와 mock 어댑터는 현재 용도에 따라 유지하며, 공개 입력·분석은 로컬 draft를 로그인 후 서버 프로젝트로 승격한다. 크레딧은 서버가 예약·확정하고 화면은 응답 잔액을 반영한다. 다운로드는 에디터의 클라이언트 PNG·ZIP 렌더가 담당한다.

실측은 셀러가 입력하며 AI가 추정하지 않는다. 세탁 안내는 에디터 자동 블록에서 만든다. 콘티는 분석에서 고른 템플릿(감성형 `emo`, 쇼룸형 `show`) 하나로 만든다. 섹션 종류는 첫 장, 착용컷(쓰임에 따라 일상 착용컷과 스튜디오 착용컷), 색상과 제품, 원단 설명, 상품 소개, 디테일 요소, 추천 대상, 후기, 궁금한 점, SNS 화면의 10개이고, 같은 `pageSectionKey`가 이어진 카드 묶음이 한 섹션이며 저장 순서가 페이지 순서다. 컷 종류는 스타일링·호리존·제품이다. 후킹·스타일링·스튜디오·의류 확인은 화면 섹션이 아니라 내부 `sectionRole` 값으로만 남고, 기본형·확장형 사진 양은 없어졌다.
