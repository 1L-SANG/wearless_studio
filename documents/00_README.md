# Wearless — 문서 인덱스

> 패션 셀러용 AI 상세페이지 제작 스튜디오. 이 폴더는 Wearless의 **살아있는 제품·기술 문서 묶음**이다.
> 현행 구현: **Vite + React SPA**(`src/`, mock-first). 백엔드는 **FastAPI + Supabase + R2**로 연동한다(`backend_integration_plan.md`).
> **Next.js 전환은 지금 하지 않는다** — DAU·SEO·공유 링크 신호가 올 때 별도 결정한다(`03_기술스택_결정서.md` §4).
> 최종 갱신: 2026-06-14

---

## 1. 문서 구성

| 문서 | 내용 |
|---|---|
| `PRD.md` | 정본 PRD — 17개 섹션 (화면·정책·데이터·MVP 우선순위) |
| `03_기술스택_결정서.md` | 스택 결정 — Vite 유지·점진 도입 로드맵·Next 전환 신호 |
| `common_data_contract.md` | 공통 데이터 계약 — 엔티티·enum·API·멱등/크레딧 규약 (`src/lib/types.js`와 동기) |
| `frontend_state_model.md` | 프론트 상태 3계층 모델 (서버/전역 클라이언트/화면 로컬) |
| `ai_agent_modules.md` | AI 에이전트 모듈 정의 — tier→모델 라우팅 단일 소스(잠정 배정) |
| `ai_pipeline_spec.md` | AI 파이프라인 명세 — PL-1~6, job 모델, 크레딧 트랜잭션 |
| `backend_integration_plan.md` | 백엔드 연동 계획 + **실행 로드맵(§10)** ← 다음 단계의 정본 |
| `TODO.md` | 구현 현황·할 일 — 코드↔계약 갭(✅/🔶/🆕)·정책 오픈이슈. 설계 문서 대신 **여기만 주기 갱신** |
| `/CONTEXT.md` · `/docs/adr/` | 용어집 · 아키텍처 결정 기록(ADR 0001~0003) |
| `/handoff/` (contracts·design·screens) | 과거 프로토타입 인수인계 산출물 — **참조용**. 계약의 현행 정본은 위 문서들과 `src/lib/types.js` |

## 2. 읽는 순서 (역할별)

- **PM/기획** → `PRD.md` → `backend_integration_plan.md` §11 (미확정 정책: 크레딧 단가·환불·보존 기한)
- **프론트엔드** → `frontend_state_model.md` → `common_data_contract.md` → `PRD.md §10`(에디터)
- **백엔드** → `common_data_contract.md`(계약) → `backend_integration_plan.md`(스키마·HTTP·job) → `ai_pipeline_spec.md`
- **디자이너** → `src/styles/tokens.css` + `handoff/design`·`handoff/screens` + `PRD.md §14`

## 3. 다음 단계 (요약 — 정본은 `backend_integration_plan.md` §10)

1. **AI 품질 스파이크** — 버리는 스크립트로 Gemini 이미지 모델의 마네킹 핏 재현·의류 동일성·비용 검증 (인프라 투자 전 최대 리스크 확인)
2. **Phase 0~1** — FastAPI 골격 · Supabase(스키마+Auth) · Railway 조기 배포 · mock↔HTTP 어댑터 · TanStack Query 도입
3. **Phase 2~7** — R2 업로드 → 분석·매칭 → AI job + 크레딧 원장(reserve-confirm) → 콘티·에디터 영속화 → 다운로드(클라 렌더) → mock 제거
4. **PG(결제)** — 크레딧 원장 위의 '충전' 기능으로 맨 마지막. 베타는 수동 지급으로 PG 없이 런칭 가능

## 4. 반드시 먼저 정할 것 (Blocking)

| 항목 | 현재(임시) | 위치 |
|---|---|---|
| 크레딧 단가 (마네킹 생성/조정, 콘티 컷당, 에디터 이미지) | 2 / 1 / 1 / 1 | `src/lib/limits.js` — **Phase 4 착수 게이트** |
| 크레딧 실패 환불·재시도 정책 | 미정 | PRD §12.2 · backend plan §11 |
| 각종 상한 (강조 특징/색상/매칭/조정) | 5 / 3 / 2 / 2 | `src/lib/limits.js` |
| 인증·소유권 | 설계 확정(Supabase Auth+RLS), 구현은 Phase 0~1 | backend plan §2~§3·§9 |

## 5. 알아둘 점

- 현행 앱은 **mock 데이터로 동작**한다(`src/mock/`). 이 mock은 단순 더미가 아니라 **실행 가능한 계약**(유료 job 멱등 합류, 크레딧 봉투, 매칭 소유권 머지)이며, 백엔드 parity 기준이므로 Phase 7 전까지 제거하지 않는다.
- 모든 API 함수는 **async Promise**이고, 장시간 작업은 `onProgress`/`onStep` 콜백을 받는다 → 실서버는 SSE/폴링을 어댑터가 콜백으로 변환한다(backend plan §5).
- **실측은 AI가 추정하지 않는다** — 분석 응답에서 measurement value는 null, 사용자가 직접 입력한다.
- **세탁 안내는 분석이 아니라 에디터 자동 블록**에서 생성된다.
- 디자인 토큰은 `src/styles/tokens.css`의 `var(--*)`만 사용한다. 임의 색·토큰 금지.
