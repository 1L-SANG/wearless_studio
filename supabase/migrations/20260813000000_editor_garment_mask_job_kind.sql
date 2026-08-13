-- 톤 에디터용 착장 마스크 전처리 job kind `editor_garment_mask` 추가.
--
-- 생성된 마네킹컷에서 판매 의류 픽셀만 골라내는 무과금 잡이다. 이미지를 만들지 않고
-- 크레딧도 쓰지 않으며, 실패해도 마네킹 결과에는 영향이 없다(색감·밝기 조정만 막힌다).
--
-- 활성 유니크 인덱스에서 **제외**한다. 한 번의 생성이 여러 컷을 남길 수 있고 각 컷이 자기
-- 마스크를 필요로 하는데, 프로젝트당 하나만 허용하면 나머지 컷이 조용히 버려진다. 중복은
-- 인덱스가 아니라 워커의 멱등키(컷 id + 알고리즘 버전)가 막는다.

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
