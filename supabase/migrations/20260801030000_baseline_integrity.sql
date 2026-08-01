-- Approved Baseline 무결성 보정 (2026-08-01, 20260801020000 후속).
--
-- ① actor 삭제가 승인 기록을 지우면 안 된다.
--    approved_by 가 `references auth.users(id) on delete cascade` 였다. 탈퇴·계정 삭제 한
--    번에 **그 사람이 승인한 baseline 행 전체가 사라진다** — 상품의 정본 컷과 승인 이력은
--    사람보다 오래 사는 자산이고, 그 위에 파생 계보(generation_outputs.baseline_id)가
--    걸려 있다. 행위자는 없어질 수 있어도 "승인이 있었다"는 사실은 남아야 한다.
--    같은 파일의 baseline_review_events.actor_id 는 이미 `on delete set null` 이었다 —
--    두 테이블의 정책이 갈라져 있던 것 자체가 결함이다. 정책을 일치시킨다.
--
-- ② action 자유 text 는 오타를 영구 데이터로 만든다.
--    현재 코드가 쓰는 값은 정확히 3개다(repo.approve_mannequin_baseline 실측:
--    baseline_approved / baseline_superseded / baseline_reapproved). 추측으로 값을
--    늘리지 않고 그 3개만 허용한다. 새 action 이 필요하면 그때 후속 migration 을 낸다.
--
-- append-only. 20260801020000 파일은 수정하지 않는다.

alter table public.approved_baselines
  alter column approved_by drop not null;

alter table public.approved_baselines
  drop constraint if exists approved_baselines_approved_by_fkey;

alter table public.approved_baselines
  add constraint approved_baselines_approved_by_fkey
  foreign key (approved_by) references auth.users (id) on delete set null;

comment on column public.approved_baselines.approved_by is
  '승인한 사용자. 계정이 삭제되면 null 이 되고 **baseline 행은 남는다**(on delete set null). '
  'cascade 였다면 탈퇴 한 번에 그 사람이 승인한 정본 컷과 파생 계보가 통째로 사라졌다. '
  'baseline_review_events.actor_id 와 같은 정책이다.';

alter table public.baseline_review_events
  drop constraint if exists baseline_review_events_action_check;
alter table public.baseline_review_events
  add constraint baseline_review_events_action_check
  check (action in ('baseline_approved', 'baseline_superseded', 'baseline_reapproved'));

comment on column public.baseline_review_events.action is
  'baseline_approved | baseline_superseded | baseline_reapproved. CHECK 로 고정 — 자유 '
  'text 는 오타를 영구 데이터로 만든다. 새 action 은 후속 migration 에서 목록에 추가한다.';

-- ── Phase 경계 (Phase 3 전까지의 사실) ──
-- 이 migration 까지가 Phase 2 다: baseline 저장과 계보 인프라를 제공한다. 조정 편집의
-- 입력 컷은 여전히 projects.selected_mannequin_id 가 정한다(repo.get_mannequin_edit_parent).
-- 승인이 그 포인터를 함께 맞추므로 보통은 baseline 이 부모가 되지만, 사용자가 PATCH 로
-- 다른 컷을 고르면 부모는 baseline 이 아니고 generation_outputs.baseline_id 는 null 이 된다
-- — 그게 정직한 결과다. "모든 조정이 baseline 기반"이라는 주장은 Phase 3(edit input 의
-- 정본을 active baseline 으로 전환) 이후에만 성립한다.
