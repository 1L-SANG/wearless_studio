-- 사용자 검수 이력 (Phase 3 P0-C 8/N, 2026-08-02).
--
-- machine QC 와 사람의 판단은 **다른 사실**이다. review_required 결과를 사용자가 보고
-- "이대로 쓴다"고 해도 그건 판정이 바뀐 게 아니라 사람이 책임을 진 것이다 — 그래서
-- edit_sessions.status·edit_qc_result 는 건드리지 않고 여기 append-only 로만 쌓는다.
-- 나중에 마음이 바뀌면 그것도 새 행이다(덮어쓰기 없음). 유효 판단은 **가장 최근 행**.

create table if not exists public.edit_review_events (
  id bigserial primary key,
  project_id uuid not null references public.projects (id) on delete cascade,
  edit_session_id uuid not null references public.edit_sessions (id) on delete cascade,
  wardrobe_image_id uuid references public.wardrobe_images (id) on delete set null,
  output_id uuid references public.generation_outputs (id) on delete set null,
  -- 계정이 사라져도 "검수가 있었다"는 사실은 남는다(승인 이력은 사람보다 오래 산다).
  actor_id uuid references auth.users (id) on delete set null,
  decision text not null check (decision in ('accepted', 'rejected')),
  reason text check (reason is null or length(reason) <= 500),
  idempotency_key text,
  created_at timestamptz not null default now()
);

comment on table public.edit_review_events is
  '사용자 검수 이력(append-only). machine QC 를 덮어쓰지 않는다 — qcStatus 는 그대로 '
  'review_required 이고, 이 행들은 "사람이 무엇을 결정했는가"만 말한다. 유효 판단은 최신 행.';
comment on column public.edit_review_events.decision is
  'accepted = 사용자가 확인하고 쓰기로 함 | rejected = 쓰지 않기로 함. rejected 가 결과를 '
  '삭제하지는 않는다 — 나중에 새 event 로 다시 accepted 가 될 수 있다.';

create index if not exists edit_review_events_session_idx
  on public.edit_review_events (edit_session_id, created_at desc);
create index if not exists edit_review_events_project_idx
  on public.edit_review_events (project_id, created_at desc);
-- 같은 요청 키의 재호출은 같은 행이다(중복 event 로 이력이 부풀지 않게).
create unique index if not exists edit_review_events_idempotency
  on public.edit_review_events (edit_session_id, idempotency_key)
  where idempotency_key is not null;

alter table public.edit_review_events enable row level security;
drop policy if exists edit_review_events_owner_select on public.edit_review_events;
create policy edit_review_events_owner_select on public.edit_review_events
  for select using (
    exists (
      select 1 from public.projects p
      where p.id = edit_review_events.project_id and p.user_id = (select auth.uid())
    )
  );
