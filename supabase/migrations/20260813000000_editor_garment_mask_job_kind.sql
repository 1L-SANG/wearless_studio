-- job kind `editor_garment_mask` 추가 — **원장 복원 파일**.
--
-- 이 변경은 2026-08-12 이전에 실서버 DB에 이미 적용됐으나(스키마 이력 20260813000000
-- editor_garment_mask_job_kind), 파일이 저장소에 커밋되지 않아 main 배포의
-- `supabase db push` 가 "Remote migration versions not found" 로 중단됐다(run 31615125498).
-- 아래 내용은 실서버의 현재 제약·인덱스 정의(pg_constraint·pg_indexes)에서 그대로
-- 역추적한 것이다 — 이미 적용된 DB 에 다시 실행돼도 결과가 같다(멱등).
--
-- editor_garment_mask 는 활성 유니크 인덱스에서 제외한다 — editor_image 류와 같이
-- 프로젝트당 동시 여러 건이 허용되는 편집 보조 잡이다.

alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
  check (kind in ('analyze', 'mannequin', 'mannequin_adjust', 'detail_page', 'editor_image',
                  'personalization_generation', 'personalization_purge',
                  'fm_model_asset_build', 'export', 'sam_preprocess',
                  'base_fidelity_observe', 'editor_garment_mask'));

drop index if exists public.jobs_active_unique_idx;
create unique index jobs_active_unique_idx on public.jobs (project_id, kind)
  where status in ('pending', 'running')
    and kind not in ('editor_image', 'personalization_generation',
                     'personalization_purge', 'export', 'base_fidelity_observe',
                     'editor_garment_mask');
