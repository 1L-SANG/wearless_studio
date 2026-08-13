-- 톤 에디터용 착장 마스크 전처리 job kind `editor_garment_mask` 추가.
--
-- 생성된 마네킹컷에서 판매 의류 픽셀만 골라내는 무과금 잡이다. 이미지를 만들지 않고
-- 크레딧도 쓰지 않으며, 실패해도 마네킹 결과에는 영향이 없다(색감·밝기 조정만 막힌다).
--
-- 활성 유니크 인덱스에서 **제외**한다. 한 번의 생성이 여러 컷을 남길 수 있고 각 컷이 자기
-- 마스크를 필요로 하는데, 프로젝트당 하나만 허용하면 나머지 컷이 조용히 버려진다. 중복은
-- 인덱스가 아니라 워커의 멱등키(컷 id + 알고리즘 버전)가 막는다.
--
-- 이력: 이 변경은 2026-08-12 QA 중 실서버 DB 에 승인 하에 선적용·원장 기록됐다. 파일이
-- 잠시 저장소에 없던 사이 main 배포의 `supabase db push` 가 "Remote migration versions
-- not found" 로 중단됐고(run 31615125498), PR #115 가 실서버 정의(pg_constraint·
-- pg_indexes)에서 역추적한 동일 내용으로 파일을 복원했다. 이 판은 그 복원본과 본 기능
-- 브랜치의 원본을 합친 것 — SQL 본문은 두 판이 동일하며, 재실행돼도 결과가 같다(멱등).

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
