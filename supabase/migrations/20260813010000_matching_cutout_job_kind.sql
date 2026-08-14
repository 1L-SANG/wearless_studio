-- 커스텀 매칭 의류 누끼(배경 제거) 잡 kind `matching_cutout` 추가.
--
-- 셀러가 올린 커스텀 매칭 의류의 배경을 SAM2 로 제거해 시드 카탈로그처럼 회색 배경
-- 컷으로 만드는 무과금 잡이다. 이미지 생성·크레딧 소비 없음. 실패해도 매칭 등록·선택에
-- 영향이 없다(원본 유지). 커스텀 매칭은 프로젝트당 하나라 활성 유니크 인덱스 정책은
-- 기존과 동일하게 유지한다.

alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
  check (kind in ('analyze', 'mannequin', 'mannequin_adjust', 'detail_page', 'editor_image',
                  'personalization_generation', 'personalization_purge',
                  'fm_model_asset_build', 'export', 'sam_preprocess',
                  'base_fidelity_observe', 'editor_garment_mask', 'matching_cutout'));

drop index if exists public.jobs_active_unique_idx;
create unique index jobs_active_unique_idx on public.jobs (project_id, kind)
  where status in ('pending', 'running')
    and kind not in ('editor_image', 'personalization_generation',
                     'personalization_purge', 'export', 'base_fidelity_observe',
                     'editor_garment_mask');
