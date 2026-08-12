-- `sam_preprocess` 를 허용 job kind 에 추가한다.
--
-- 이게 없으면 라우트가 잡을 만드는 순간 jobs_kind_check 가 터지고, 같은 트랜잭션에서 만든
-- **분석 잡까지 롤백된다** — 즉 POST /analyze 가 통째로 500 이 된다. 로컬 QA 에서 실제로 그
-- 상태를 밟았다. 워커 등록(_WORKERS)과 이 제약은 항상 같이 움직여야 한다.
--
-- 활성 유니크 인덱스(jobs_active_unique_idx)의 제외 목록에는 넣지 않는다. 캐노니컬 전처리는
-- 프로젝트당 하나만 돌면 되고, 동시 요청은 기존 잡에 합류하는 편이 맞다.

alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
  check (kind in ('analyze', 'mannequin', 'mannequin_adjust', 'detail_page', 'editor_image',
                  'personalization_generation', 'personalization_purge',
                  'fm_model_asset_build', 'export', 'sam_preprocess'));
