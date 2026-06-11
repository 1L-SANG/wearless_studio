# 컷 분류는 CutType(styling·horizon·product) 하나로 통일한다

같은 개념이 세 enum에 걸쳐 다르게 표기됐다 — BlockKind, CutSource(studio/daily/product/mine), Element.cutType(horizon/daily/product). 호리존이 `studio`와 `horizon` 두 토큰으로 존재했고, '일상컷'과 '스타일링컷'이 화면마다 섞여 쓰였다. `CutType = styling | horizon | product` 단일 enum으로 통일하고, 공식 용어는 **스타일링컷·호리존컷**으로 확정한다('일상컷'·'스튜디오컷'과 `daily`·`studio` 토큰 폐기). '내 이미지'는 컷 종류가 아니라 `source: ai | mine`으로 구분한다. BlockKind는 상세페이지 섹션 역할(hook/selling/…)로 CutType과 직교 유지한다. (2026-06-11)

## Consequences

- 토큰 마이그레이션 필요: `cutType: 'daily'` → `'styling'`, CutSource 폐기. 대상: `lib/types.js`, `mock/db.js`, `mock/api.js`, `Storyboard.jsx`, `EditorPanels.jsx`, `Editor.jsx`.
- 콘티보드·에디터 AI 패널의 '일상컷' 라벨이 '스타일링컷'으로 바뀐다 — `documents/PRD.md` §8.4의 탭 명칭 갱신 필요.
- AI 파이프라인 프롬프트·생성 메타데이터도 이 토큰을 쓴다. 이후 토큰 변경은 생성 이력 데이터 마이그레이션을 수반하므로 사실상 비가역.
