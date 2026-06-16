-- =============================================================
-- 20260616105745_credit_ledger_append_only_per_row.sql
-- credit_ledger append-only 트리거를 statement-level → row-level로 교정.
--
-- 버그: init.sql의 `for each statement` 트리거는 매칭 행이 0개여도 발동한다.
-- 그래서 유저/프로젝트 삭제 cascade가 `update credit_ledger set project_id=null`
-- (FK on delete set null, 대상 0행)을 실행할 때 무영향 statement인데도 막혀
-- 계정 삭제 전체가 P0001로 실패. row-level은 실제 변경 행에만 발동하므로
-- append-only(실제 update/delete 차단)는 유지하면서 무영향 cascade는 통과시킨다.
--
-- 주의(Phase 4 오픈 이슈): ledger 행이 *존재하는* 유저(유료 사용 후)를 삭제하면
-- 여전히 막힌다 — ledger.user_id FK(no action) + project_id set null이 실제 행을
-- 건드리기 때문. 감사 원장 보존 vs 계정 삭제 정책은 보존 기한 정책과 함께 확정
-- (backend_integration_plan §11). 현재 ledger 행 0건이라 영향 없음.
-- =============================================================

drop trigger credit_ledger_append_only on public.credit_ledger;
create trigger credit_ledger_append_only
  before update or delete on public.credit_ledger
  for each row execute function public.forbid_credit_ledger_mutation();
