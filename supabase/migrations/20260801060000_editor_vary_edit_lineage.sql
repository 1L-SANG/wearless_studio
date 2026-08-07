-- Editor vary 계보 (Phase 3 P0-C 6/N, 2026-08-02, 20260801050000 후속).
--
-- edit_sessions 는 Approved Baseline 편집만 표현할 수 있었다(baseline_id NOT NULL).
-- 에디터의 생성형 vary 는 baseline 이 없는 **에디터 자산**을 고쳐 만든다 — 그 편집도
-- "무엇을 바꿔달라 했고 무엇은 잠갔는가"를 남겨야 하는데, 지금 스키마로는 행 자체를
-- 만들 수 없다. source 를 일반화한다.
--
-- 거짓 계보를 만들지 않는다: 기존 baseline 세션의 source_asset_id 는 **역산하지 않고**
-- null 로 둔다(baseline_id 가 정본이다). editor vary 는 반대로 source_asset_id 가 정본이고,
-- parent_output_id 는 그 자산에 generation_outputs 행이 **실제로 있을 때만** 채운다.

alter table public.edit_sessions
  add column if not exists source_kind text not null default 'approved_baseline',
  add column if not exists source_asset_id uuid references public.assets (id);

-- 기존 행은 전부 baseline 편집이다(그 경로만 세션을 만들었다). 기본값이 곧 backfill 이다.
-- source_asset_id 는 채우지 않는다 — 컷 asset 을 역산해 넣으면 "그 자산을 편집했다"는
-- 거짓이 된다. baseline → cut → asset 은 조회로 언제든 따라갈 수 있다.

alter table public.edit_sessions alter column baseline_id drop not null;

alter table public.edit_sessions
  drop constraint if exists edit_sessions_source_check;

alter table public.edit_sessions
  add constraint edit_sessions_source_check check (
    (source_kind = 'approved_baseline' and baseline_id is not null)
    or (source_kind = 'editor_asset' and source_asset_id is not null
        and baseline_id is null));

comment on column public.edit_sessions.source_kind is
  'approved_baseline = 승인 컷 편집(baseline_id 정본) | editor_asset = 에디터 자산 vary '
  '(source_asset_id 정본). CHECK 가 두 조합 외를 막는다.';

comment on column public.edit_sessions.source_asset_id is
  'editor vary 의 입력 자산. 이 자산에 generation_outputs 행이 있으면 parent_output_id 도 '
  '채워지고, 없으면(업로드·legacy) null 이다 — 잘못된 output 을 추정하지 않는다. '
  'on delete 는 걸지 않는다(assets 는 soft delete 규율) — 계보는 id 로 남는다.';

create index if not exists edit_sessions_source_asset_idx
  on public.edit_sessions (source_asset_id);

-- 세션 하나에 결과 하나. 같은 output 이 두 세션에 붙거나 한 세션이 여러 output 을
-- 가리키면 "이 편집의 결과"라는 말이 성립하지 않는다.
create unique index if not exists generation_outputs_one_per_edit_session
  on public.generation_outputs (edit_session_id) where edit_session_id is not null;

create unique index if not exists edit_sessions_one_output
  on public.edit_sessions (output_id) where output_id is not null;

-- ── wardrobe: 생성형 vary 결과가 QC 상태 없이 일반 이미지로 보이지 않게 ──
alter table public.wardrobe_images
  add column if not exists edit_session_id uuid
    references public.edit_sessions (id) on delete set null,
  add column if not exists qc_status text;

alter table public.wardrobe_images
  drop constraint if exists wardrobe_images_qc_status_check;

alter table public.wardrobe_images
  add constraint wardrobe_images_qc_status_check
  check (qc_status is null or qc_status in ('pass', 'review_required'));

comment on column public.wardrobe_images.qc_status is
  'pass | review_required | null. null = 판정 대상 아님(legacy·mode:new·플래그 off). '
  'reject 는 값이 아니다 — 거부된 결과는 애초에 여기 들어오지 않는다.';

comment on column public.wardrobe_images.edit_session_id is
  '이 이미지를 만든 편집 세션(생성형 vary). 계보는 세션에서 따라간다.';

create index if not exists wardrobe_images_edit_session_idx
  on public.wardrobe_images (edit_session_id);
