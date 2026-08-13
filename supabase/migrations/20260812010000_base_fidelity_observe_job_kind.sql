-- 관측 전용 job kind `base_fidelity_observe` 추가.
--
-- 사용자가 마네킹 결과를 거부하고 재생성을 요청할 때, **거부된 컷**을 베이스 마네킹과 대조해
-- 판정만 남기는 잡이다. 이미지를 만들지 않고 크레딧도 쓰지 않는다.
--
-- 활성 유니크 인덱스에서 **제외**한다. 다른 무과금 잡(sam_preprocess)은 프로젝트당 하나면
-- 충분하지만, 이건 다르다: 셀러가 연달아 두 번 거부하면 그 두 컷이 각각 표본이고, 하나가
-- 도는 동안 다른 하나가 조용히 버려지면 코퍼스에 구멍이 생긴다. 중복은 인덱스가 아니라
-- 라우트의 멱등키(거부된 컷 id 기준)가 막는다.

alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
  check (kind in ('analyze', 'mannequin', 'mannequin_adjust', 'detail_page', 'editor_image',
                  'personalization_generation', 'personalization_purge',
                  'fm_model_asset_build', 'export', 'sam_preprocess',
                  'base_fidelity_observe'));

drop index if exists public.jobs_active_unique_idx;
create unique index jobs_active_unique_idx on public.jobs (project_id, kind)
  where status in ('pending', 'running')
    and kind not in ('editor_image', 'personalization_generation',
                     'personalization_purge', 'export', 'base_fidelity_observe');
