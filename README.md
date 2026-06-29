# Wearless Studio

패션 이커머스 셀러가 상품 사진 몇 장과 기본 정보를 입력하면, AI가 상세페이지 초안(이미지 및 마케팅 카피)을 자동으로 생성하고 이를 편집·다운로드할 수 있는 AI 상세페이지 제작 스튜디오 웹 애플리케이션입니다.

---

## 📌 주요 문서 링크 (Documentation)

상세 기획서, 도메인 아키텍처, 데이터 스펙 등의 정본 문서는 `documents/` 디렉토리에 정의되어 있습니다.

- **[용어집 (CONTEXT.md)](./CONTEXT.md)**: 프로젝트 내부의 핵심 도메인 용어 정의 (컷 종류, 블록 종류, 생성 플로우 용어 등)
- **[문서 인덱스 (00_README.md)](./documents/00_README.md)**: 전체 설계 문서 목록 및 개발 로드맵 인덱스
- **[제품 요구사항 정의서 (PRD.md)](./documents/PRD.md)**: 17개 섹션으로 구성된 기획 명세, 화면 정의 및 정책
- **[공통 데이터 계약 (common_data_contract.md)](./documents/common_data_contract.md)**: 백엔드-프론트엔드 연동을 위한 데이터 스키마 및 API 명세
- **[프론트엔드 상태 모델 (frontend_state_model.md)](./documents/frontend_state_model.md)**: 프론트엔드의 3계층(서버/전역 클라이언트/화면 로컬) 상태 설계 구조
- **[백엔드 연동 계획 (backend_integration_plan.md)](./documents/backend_integration_plan.md)**: 서비스 아키텍처 설계 및 FastAPI + Supabase 전환 로드맵
- **[AI 파이프라인 명세 (ai_pipeline_spec.md)](./documents/ai_pipeline_spec.md)**: 마네킹 피팅 이미지 생성, 백그라운드 작업(Job), 크레딧 트랜잭션 규약
- **[개발 현황 및 할 일 (TODO.md)](./documents/TODO.md)**: 구현 진행도, 정책 오픈 이슈 및 마일스톤 관리 대장

---

## 🛠 기술 스택 (Tech Stack)

### Frontend

- **Core**: HTML5, React (v18)
- **Build Tool**: Vite (Next.js는 도입하지 않고 Single Page Application으로 개발)
- **State Management**: Zustand (전역 상태 관리)
- **Routing**: React Router DOM (v6)
- **Data Fetching**: TanStack React Query (v5)
- **Styling**: Vanilla CSS + CSS Variables (`src/styles/tokens.css` 기반)
- **Canvas Editor**: React Moveable

### Backend & DB (과도기/예정)

- **API Framework**: FastAPI (Python)
- **Database / Auth / Storage**: Supabase (PostgreSQL, Supabase Auth, Row Level Security)
- **Asset Storage**: Cloudflare R2 (이미지 업로드)

---

## 📁 프로젝트 폴더 구조

```text
wearless_studio/
├── documents/          # 기획, 제품(PRD) 및 아키텍처 설계 문서 정본
├── public/             # 정적 리소스
├── src/
│   ├── components/     # 공통 UI 컴포넌트 (버튼, 다이얼로그, 레이아웃 등)
│   ├── features/       # 도메인 피처 단위 기능 컴포넌트 및 페이지
│   │   ├── library/    # 보관함(프로젝트 목록) 화면
│   │   ├── analyzer/   # 제품 이미지 분석 및 정보 입력 화면
│   │   ├── contiboard/ # 콘티 구성 및 계획 화면
│   │   └── editor/     # 상세페이지 편집용 캔버스 에디터 화면
│   ├── fonts/          # 프리미엄 폰트 파일
│   ├── lib/            # 유틸리티 함수, Supabase 클라이언트 및 전역 타입 정의
│   │   ├── types.js    # 공통 데이터 모델 타입 정의
│   │   └── limits.js   # 비즈니스 정책 상한선 및 과금 단가 정의
│   ├── mock/           # Mock API 및 시뮬레이션 서비스 (백엔드 Parity 준수)
│   ├── store/          # Zustand 전역 스토어
│   ├── styles/         # 디자인 시스템 토큰(tokens.css) 및 글로벌/피처 스타일
│   ├── App.jsx         # 메인 라우트 구조 정의
│   └── main.jsx        # 어플리케이션 마운트 엔트리 포인트
├── .env.example        # 환경 변수 템플릿 파일
├── package.json        # 의존성 및 스크립트 정의
└── vite.config.js      # Vite 번들러 설정
```

---

## 🚀 시작 가이드 (Getting Started)

이 프로젝트는 패키지 매니저로 `pnpm`을 사용합니다.

### 1. 의존성 패키지 설치

```bash
pnpm install
```

### 2. 로컬 개발 서버 실행

```bash
pnpm dev
```

- 로컬 개발 환경은 `src/mock/`을 통하여 동작하므로, 별도의 백엔드 연동 없이도 이미지 분석, 마네킹 피팅, 에디팅 등 주요 생성 흐름을 완벽하게 테스트할 수 있습니다.

### 3. 프로덕션 빌드 및 검증

```bash
pnpm build
```

---

## ⚠️ 개발 원칙 및 주의 사항 (Core Rules)

프로젝트 개발 및 수정을 진행할 때는 [agents.md](./agents.md)에 정의된 다음 가이드라인을 엄격하게 지켜주세요.

1. **Vite React SPA 구조 엄수 (Vite-First)**
    - 이 프로젝트는 Vite 기반 React SPA입니다. Next.js로의 마이그레이션은 현재 고려 대상이 아니므로, `app/` 이나 `pages/` 같은 Next.js 컨벤션, API routes, Server Actions, `next/image` 등은 사용을 금지합니다.
2. **디자인 시스템 토큰 강제 사용**
    - 모든 스타일링 작업은 `src/styles/tokens.css`에 정의된 CSS 변수(`var(--*)`)만을 사용하여 이루어집니다. 컴포넌트 내에 임의의 HEX 색상 코드, 인라인 스타일, 독자적인 아웃라인 등 디자인 시스템에 어긋나는 요소는 삽입할 수 없습니다.
3. **Mock-First 원칙**
    - 프론트엔드는 UI와 로컬 상태만을 소유하며 Supabase나 실서버 AI 엔드포인트를 컴포넌트 레벨에서 직접 호출하지 않습니다. 모든 통신 흐름은 `src/mock/` layer의 `api.js`, `db.js`, `placeholders.js`를 먼저 거치도록 구현해야 합니다.
4. **Append-Only 데이터베이스 마이그레이션**
    - `supabase/migrations/*.sql`에 정의된 마이그레이션 히스토리는 절대 사후 편집하거나 삭제하지 않습니다. 스키마 변경 시에는 반드시 새로운 포워드 마이그레이션 파일(`<timestamp>_*.sql`)을 추가해야 합니다.
