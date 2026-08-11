-- Generation Run 보정 — 계보·무결성 (2026-08-01, 20260801000000 후속).
--
-- 앞 migration 은 provider 호출을 행으로 만들었지만 세 가지가 비어 있었다:
--
--   ① 편집 run 이 **무엇을 입력으로 받았는지** 행에 없었다. 최종 바이트 역참조만으로
--      계보를 세우면 회귀 복구(편집을 되돌려 이전 이미지를 채택)와 후보 간 동일 바이트
--      충돌에서 계보가 흔들린다. 입력 이미지 sha 와 부모 run 을 직접 남긴다.
--   ② deterministic 후처리(hybrid composite)가 최종 바이트를 바꾸면 "그 바이트를 만든
--      응답"이 존재하지 않아 output 행이 통째로 누락됐다. generation_run_id 의 의미를
--      **"최종 결과의 마지막 provider 조상"**으로 고정하고, 최종 바이트 해시와 후처리
--      여부를 따로 둔다 — 후처리가 있어도 행은 반드시 남는다.
--   ③ status 에 check 가 없어 오타 상태값이 조용히 들어갈 수 있었다.
--
-- append-only. 전 컬럼 nullable/기본값 — 기존 행과 호환된다.

alter table public.generation_runs
  add column if not exists input_image_sha256 text,
  add column if not exists parent_generation_run_id uuid
    references public.generation_runs (id) on delete set null;

comment on column public.generation_runs.input_image_sha256 is
  '이 호출의 **주 입력 이미지**(편집 대상 = 직전 산출물, 조정 편집의 parent cut) 바이트 sha256. '
  '입력 전체 목록은 input_assets 에 순서대로 있다.';

comment on column public.generation_runs.parent_generation_run_id is
  '주 입력 이미지를 만든 호출. 편집 체인(생성→untuck→axis→bust)을 행만으로 복원한다. '
  'null = 부모가 provider 산출물이 아님(베이스 마네킹·업로드 원본에서 시작).';

create index if not exists generation_runs_parent_idx
  on public.generation_runs (parent_generation_run_id);

-- status 무결성. 기존 행은 전부 created|succeeded|failed 라 not valid 없이 붙는다.
alter table public.generation_runs
  drop constraint if exists generation_runs_status_check;

alter table public.generation_runs
  add constraint generation_runs_status_check
  check (status in ('created', 'succeeded', 'failed'));

alter table public.generation_outputs
  add column if not exists output_sha256 text,
  add column if not exists post_processed boolean not null default false,
  add column if not exists transformation jsonb;

-- ── generation_run_id 의 의미(계약) ──
-- "최종 결과의 **마지막 provider 조상**"이다. "최종 바이트와 완전히 동일한 응답"이 아니다.
-- 둘의 구분은 post_processed 가 한다:
--   · post_processed = false → 그 run 의 응답 바이트 = output_sha256 (정확히 동일)
--   · post_processed = true  → deterministic 후처리(hybrid composite 등)가 바이트를 바꿨다.
--     그래도 행은 존재하며, generation_run_id 는 후처리 직전의 provider 호출을 가리킨다.
comment on column public.generation_outputs.generation_run_id is
  '최종 결과의 마지막 provider 조상(= 후처리 직전 호출). post_processed=false 일 때만 '
  '이 run 의 응답 바이트와 output_sha256 이 동일하다.';

comment on column public.generation_outputs.output_sha256 is
  '사용자에게 실제로 나간 이미지 바이트의 sha256(후처리 결과 포함).';

comment on column public.generation_outputs.post_processed is
  'deterministic 후처리로 provider 응답과 최종 바이트가 달라졌는가.';

comment on column public.generation_outputs.transformation is
  '후처리 메타(예: {"hybridComposite": {"applied": true, "pipelineVersion": "..."}}). '
  '바이트·URL·프롬프트 원문 금지.';

-- ── RLS 보정: runs 도 프로젝트 소유권으로 검증 ──
-- user_id 컬럼 하나만 믿으면, 백엔드 버그로 잘못 채워진 행이 남의 눈에 보인다. 소유권의
-- 정본은 projects 다 — 두 조건을 모두 요구한다(컬럼은 인덱스·조회 편의로 유지).
drop policy if exists generation_runs_owner_select on public.generation_runs;

create policy generation_runs_owner_select on public.generation_runs
  for select using (
    user_id = (select auth.uid())
    and exists (
      select 1 from public.projects p
      where p.id = generation_runs.project_id and p.user_id = (select auth.uid())
    )
  );
