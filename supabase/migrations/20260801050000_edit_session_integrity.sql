-- Edit Session 무결성 보정 (Phase 3 P0-C 4/N, 2026-08-01, 20260801040000 후속).
--
-- ① job 당 세션은 하나다.
--    편집 라우트가 create_job 의 created=False(같은 키 재시도 / 다른 활성 job 합류)를
--    구분하지 않고 매번 세션을 만들고 payload 를 덮어썼다. 결과: 같은 job 에 세션이 여러
--    개 붙고, 워커는 마지막 것만 보고, 나머지는 영원히 queued 로 남는 고아가 된다.
--    라우트를 고치되 DB 에서도 막는다 — 애플리케이션 순서에만 기대면 동시 요청에서 뚫린다.
--
-- ② output ↔ session 양방향 연결의 인덱스.
--    "이 세션이 만든 결과"와 "이 결과를 만든 세션"을 양쪽에서 조회한다.

create unique index if not exists edit_sessions_one_per_job
  on public.edit_sessions (job_id) where job_id is not null;

comment on index public.edit_sessions_one_per_job is
  'job 하나에 세션 하나. 라우트가 멱등 재호출을 구분하지 못해 고아 세션이 쌓이던 것을 '
  'DB 에서도 막는다(동시 요청은 애플리케이션 순서로 못 막는다).';

create index if not exists generation_outputs_edit_session_idx
  on public.generation_outputs (edit_session_id);

comment on column public.edit_sessions.output_id is
  '이 세션이 만든 결과. generation_outputs.edit_session_id 와 **양방향**으로 채운다 — '
  '한쪽만 채우면 "결과는 있는데 어느 의도였는지 모르는" 행이 남는다. 채택 output 의 id 를 '
  'insert 의 RETURNING 으로 받아 연결한다(최신 output 추정 금지).';
