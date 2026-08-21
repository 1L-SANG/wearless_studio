-- SAM 잡 재시도 푸셔(app/workers/sam_retry_pusher.py)가 15초마다 도는 조회를 위한 partial index.
--
-- 푸셔는 "일시 장애로 끝났고 예산이 남은 SAM 잡"만 본다(app/repo.py list_retryable_sam_jobs).
-- 기존 인덱스는 (project_id, kind, status) 와 pending 전용뿐이라 이 조회에 맞지 않는다 —
-- 전역 스캔이 되면 jobs 가 커질수록 매 15초가 비싸진다.
--
-- partial 조건은 조회의 where 절 중 **변하지 않는 부분**만 담는다. retry 예산과 백오프는
-- 코드 상수라 인덱스에 넣지 않는다(상수가 바뀌면 인덱스를 다시 만들어야 한다).
create index if not exists jobs_sam_retry_idx
  on public.jobs (kind, finished_at desc)
  where status in ('done', 'error')
    and kind in ('sam_preprocess', 'matching_cutout', 'editor_garment_mask');
