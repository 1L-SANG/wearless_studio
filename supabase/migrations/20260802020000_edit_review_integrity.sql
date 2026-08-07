-- 검수 이력 보존 강화 (Phase 3 P0-C 8/N 보정, 2026-08-02).
--
-- 20260802010000 은 project_id·edit_session_id 를 cascade 로 걸었다. 그러면 프로젝트나
-- 편집 세션을 hard delete 하는 순간 "사람이 무엇을 승인했는가"가 조용히 같이 사라진다.
-- 이 프로젝트는 projects·assets 를 deleted_at 으로 소프트 삭제하므로 정상 흐름에서
-- hard delete 는 쓰이지 않는다 — 그러니 RESTRICT 로 막아도 운영이 막히지 않고,
-- 대신 실수로 지우는 경로만 실패한다.
--
-- 주의(credit_ledger 와 같은 성질): 이력이 있는 프로젝트를 hard delete 하려면 보존
-- 기한 정책에 따른 명시적 파기 절차가 필요하다. credit_ledger 도 이미 같은 이유로
-- 계정 hard delete 를 막고 있어 새로 생기는 제약은 아니다.

alter table public.edit_review_events
  drop constraint if exists edit_review_events_project_id_fkey;

alter table public.edit_review_events
  add constraint edit_review_events_project_id_fkey
  foreign key (project_id) references public.projects (id) on delete restrict;

alter table public.edit_review_events
  drop constraint if exists edit_review_events_edit_session_id_fkey;

alter table public.edit_review_events
  add constraint edit_review_events_edit_session_id_fkey
  foreign key (edit_session_id) references public.edit_sessions (id) on delete restrict;

-- wardrobe_image_id·output_id·actor_id 는 set null 을 유지한다. 그 셋은 "무엇을/누가"의
-- 부가 정보고, 사라져도 decision·reason·created_at 이라는 판단 자체는 남는다.

-- ── append-only 를 DB 에서 강제 (credit_ledger 와 같은 패턴) ──
-- statement-level 이 아니라 row-level 이다. statement-level 은 대상 0행인 무영향
-- cascade 에도 발동해 삭제 전체를 막는다(20260616105745 에서 겪은 버그).
create or replace function public.forbid_edit_review_event_mutation()
returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'edit_review_events is append-only';
  end if;
  -- FK 파생 무효화(참조 대상이 사라져 null 로 바뀌는 것)만 통과시킨다.
  -- 값이 채워지거나 다른 값으로 바뀌면 그건 cascade 가 아니라 위조다.
  if new.id is distinct from old.id
     or new.project_id is distinct from old.project_id
     or new.edit_session_id is distinct from old.edit_session_id
     or new.decision is distinct from old.decision
     or new.reason is distinct from old.reason
     or new.idempotency_key is distinct from old.idempotency_key
     or new.created_at is distinct from old.created_at
     or (new.wardrobe_image_id is not null
         and new.wardrobe_image_id is distinct from old.wardrobe_image_id)
     or (new.output_id is not null and new.output_id is distinct from old.output_id)
     or (new.actor_id is not null and new.actor_id is distinct from old.actor_id)
  then
    raise exception 'edit_review_events is append-only';
  end if;
  return new;
end;
$$;

drop trigger if exists edit_review_events_append_only on public.edit_review_events;

create trigger edit_review_events_append_only
  before update or delete on public.edit_review_events
  for each row execute function public.forbid_edit_review_event_mutation();

-- PostgREST 직접 쓰기 차단 — 이 테이블은 백엔드(service_role)만 쓴다.
-- (service_role 은 grant 를 우회하므로 위 트리거가 실질 방어선이다.)
revoke insert, update, delete on public.edit_review_events from anon, authenticated;

comment on function public.forbid_edit_review_event_mutation() is
  'edit_review_events append-only 강제. FK 파생 set null 만 예외로 통과시킨다 — '
  '마음이 바뀌면 기존 행을 고치는 게 아니라 새 행을 넣는다.';
