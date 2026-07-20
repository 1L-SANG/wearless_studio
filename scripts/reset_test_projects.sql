-- 테스트 프로젝트 전체 리셋 v2 (2026-07-17, 분류 v2 전환에 따른 오너 결정)
-- 실행 위치: Supabase 대시보드 → SQL Editor → 붙여넣고 Run
--
-- v1 실행 시 "credit_ledger is append-only" 오류가 난 이유:
--   jobs/projects를 지우면 크레딧 기록의 연결 칸(job_id/project_id)이 자동으로 비워지는데(SET NULL),
--   크레딧 기록은 수정 금지 트리거로 보호되고 있어 충돌. 아래는 삭제 동안만 그 트리거를 끄고 진행.
--   크레딧 기록의 금액·잔액은 전혀 변경되지 않는다 (연결 칸만 비워짐).
--
-- 지우는 것: 프로젝트 전체 + 자동 연쇄(상품·분석·작업·작업이벤트·마네킹컷·옷장이미지·내보내기) + 프로젝트 소속 이미지
-- 지키는 것: 계정, 크레딧(기록 포함), 요금제, 매칭 의류 시드, 프로젝트 소속 아닌 자산(가상모델·마네킹 시드 등)

-- 0) 실행 전 현황 확인 (선택)
SELECT (SELECT count(*) FROM projects)                            AS projects,
       (SELECT count(*) FROM jobs)                                AS jobs,
       (SELECT count(*) FROM assets WHERE project_id IS NOT NULL) AS project_assets,
       (SELECT count(*) FROM assets WHERE project_id IS NULL)     AS keep_assets,
       (SELECT count(*) FROM credit_ledger)                       AS credit_rows;

-- 1) 본 삭제 (한 트랜잭션)
BEGIN;

-- 프로젝트 소속 이미지 목록을 먼저 기억해둔다 (projects 삭제 시 소속 표시가 지워지기 때문)
CREATE TEMP TABLE _doomed_assets ON COMMIT DROP AS
  SELECT id FROM assets WHERE project_id IS NOT NULL;

-- 크레딧 기록 보호 트리거를 삭제 동안만 끈다 (연결 칸 비우기 허용, 금액은 불변)
ALTER TABLE credit_ledger DISABLE TRIGGER USER;

-- 프로젝트 삭제 → 상품·분석·작업(→이벤트)·마네킹컷·옷장이미지·내보내기 자동 연쇄 삭제
DELETE FROM projects;

-- 기억해둔 프로젝트 소속 이미지 삭제
DELETE FROM assets WHERE id IN (SELECT id FROM _doomed_assets);

ALTER TABLE credit_ledger ENABLE TRIGGER USER;

COMMIT;

-- 2) 실행 후 확인 — projects/jobs/project_assets = 0, keep_assets·credit_rows는 그대로여야 정상
SELECT (SELECT count(*) FROM projects)                            AS projects,
       (SELECT count(*) FROM jobs)                                AS jobs,
       (SELECT count(*) FROM assets WHERE project_id IS NOT NULL) AS project_assets,
       (SELECT count(*) FROM assets WHERE project_id IS NULL)     AS keep_assets,
       (SELECT count(*) FROM credit_ledger)                       AS credit_rows;
