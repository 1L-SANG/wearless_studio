-- 테스트 프로젝트 명시 리셋 v3 (2026-07-20, 운영 DB 오실행 fail-safe)
-- 실행 위치: Supabase 대시보드 -> SQL Editor -> 붙여넣고 Run
--
-- 기본 상태로 실행하면 예외가 발생하고 삭제는 0건이다.
-- 실행 전 반드시 아래 _reset_confirm, _reset_test_owner_users, _reset_test_projects 값을
-- 이번에 지울 테스트 소유자/프로젝트로 직접 채운다.
--
-- 지우는 것: 명시한 테스트 소유자의 명시한 프로젝트 + 자동 연쇄
--           (상품·분석·작업·작업이벤트·마네킹컷·옷장이미지·내보내기) + 해당 프로젝트 소속 이미지
-- 지키는 것: 계정, 크레딧(기록 포함), 요금제, 매칭 의류 시드, 프로젝트 소속 아닌 자산
--
-- 안전장치:
--   1) 확인 토큰 + 예상 삭제 수 + 대상 owner/project id가 모두 맞아야 진행.
--   2) DELETE FROM projects 같은 전체 삭제 경로가 없다.
--   3) credit_ledger append-only 트리거를 끄지 않는다.
--      대상 프로젝트/잡을 참조하는 ledger 행이 있으면 hard fail하고 롤백한다.

BEGIN;

-- 0) 실행자가 이번 리셋 대상과 확인값을 직접 채운다.
CREATE TEMP TABLE _reset_confirm (
  confirmation text NOT NULL,
  expected_project_count integer NOT NULL CHECK (expected_project_count > 0)
) ON COMMIT DROP;

-- 예시:
-- INSERT INTO _reset_confirm (confirmation, expected_project_count)
-- VALUES ('RESET_TEST_PROJECTS', 2);

CREATE TEMP TABLE _reset_test_owner_users (
  user_id uuid PRIMARY KEY
) ON COMMIT DROP;

-- 예시:
-- INSERT INTO _reset_test_owner_users (user_id)
-- VALUES ('00000000-0000-0000-0000-000000000000');

CREATE TEMP TABLE _reset_test_projects (
  project_id uuid PRIMARY KEY
) ON COMMIT DROP;

-- 예시:
-- INSERT INTO _reset_test_projects (project_id)
-- VALUES
--   ('11111111-1111-1111-1111-111111111111'),
--   ('22222222-2222-2222-2222-222222222222');

-- 1) 대상 확정: 명시한 owner가 소유한 명시 프로젝트만 삭제 후보로 삼는다.
CREATE TEMP TABLE _reset_scope ON COMMIT DROP AS
  SELECT p.id, p.user_id, p.title, p.status, p.updated_at
  FROM public.projects p
  JOIN _reset_test_owner_users u ON u.user_id = p.user_id
  JOIN _reset_test_projects t ON t.project_id = p.id;

-- 2) 사전 현황 확인.
SELECT 'before' AS phase,
       (SELECT count(*) FROM _reset_scope)                       AS scoped_projects,
       (SELECT count(*) FROM public.projects)                    AS all_projects,
       (SELECT count(*) FROM public.jobs j
          JOIN _reset_scope s ON s.id = j.project_id)            AS scoped_jobs,
       (SELECT count(*) FROM public.assets a
          JOIN _reset_scope s ON s.id = a.project_id)            AS scoped_project_assets,
       (SELECT count(*) FROM public.credit_ledger cl
          LEFT JOIN public.jobs j ON j.id = cl.job_id
          LEFT JOIN _reset_scope sp ON sp.id = cl.project_id
          LEFT JOIN _reset_scope sj ON sj.id = j.project_id
         WHERE sp.id IS NOT NULL OR sj.id IS NOT NULL)           AS blocking_credit_rows;

-- 3) Fail-safe 검증. 실패하면 트랜잭션 전체가 롤백되어 삭제 0건.
DO $$
DECLARE
  confirm_count integer;
  confirm_token text;
  expected_count integer;
  owner_count integer;
  target_count integer;
  scoped_count integer;
  unowned_target_count integer;
  blocking_credit_count integer;
BEGIN
  SELECT count(*), min(confirmation), min(expected_project_count)
    INTO confirm_count, confirm_token, expected_count
  FROM _reset_confirm;

  SELECT count(*) INTO owner_count FROM _reset_test_owner_users;
  SELECT count(*) INTO target_count FROM _reset_test_projects;
  SELECT count(*) INTO scoped_count FROM _reset_scope;

  SELECT count(*)
    INTO unowned_target_count
  FROM _reset_test_projects t
  LEFT JOIN _reset_scope s ON s.id = t.project_id
  WHERE s.id IS NULL;

  SELECT count(*)
    INTO blocking_credit_count
  FROM public.credit_ledger cl
  LEFT JOIN public.jobs j ON j.id = cl.job_id
  LEFT JOIN _reset_scope sp ON sp.id = cl.project_id
  LEFT JOIN _reset_scope sj ON sj.id = j.project_id
  WHERE sp.id IS NOT NULL OR sj.id IS NOT NULL;

  IF confirm_count <> 1
     OR confirm_token <> 'RESET_TEST_PROJECTS'
     OR owner_count = 0
     OR target_count = 0
     OR scoped_count = 0
     OR scoped_count <> expected_count
     OR unowned_target_count <> 0 THEN
    RAISE EXCEPTION
      'reset_test_projects fail-safe: confirmation=%, owners=%, targets=%, scoped=%, expected=%, unowned_targets=%',
      confirm_token, owner_count, target_count, scoped_count, expected_count, unowned_target_count;
  END IF;

  IF blocking_credit_count <> 0 THEN
    RAISE EXCEPTION
      'reset_test_projects blocked: % credit_ledger row(s) reference the scoped projects/jobs; append-only ledger was not modified',
      blocking_credit_count;
  END IF;
END $$;

-- 프로젝트 소속 이미지 목록을 먼저 기억해둔다 (projects 삭제 시 project_id가 set null 될 수 있기 때문).
CREATE TEMP TABLE _reset_doomed_assets ON COMMIT DROP AS
  SELECT a.id
  FROM public.assets a
  JOIN _reset_scope s ON s.id = a.project_id;

-- 4) 명시 scope만 삭제. 연쇄 삭제는 FK에 맡긴다.
DELETE FROM public.projects p
USING _reset_scope s
WHERE p.id = s.id;

DELETE FROM public.assets a
USING _reset_doomed_assets d
WHERE a.id = d.id;

-- 5) 실행 후 확인.
SELECT 'after' AS phase,
       (SELECT count(*) FROM _reset_scope s
          JOIN public.projects p ON p.id = s.id)                 AS remaining_scoped_projects,
       (SELECT count(*) FROM public.jobs j
          JOIN _reset_scope s ON s.id = j.project_id)            AS remaining_scoped_jobs,
       (SELECT count(*) FROM public.assets a
          JOIN _reset_doomed_assets d ON d.id = a.id)            AS remaining_scoped_project_assets,
       (SELECT count(*) FROM public.credit_ledger)               AS credit_rows;

COMMIT;
