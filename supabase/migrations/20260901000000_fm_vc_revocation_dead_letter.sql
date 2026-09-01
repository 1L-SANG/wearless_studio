-- 폐기 잡에 종결 상태(dead)를 더한다.
-- 상한이 없어서 고아 잡 하나가 880회까지 조용히 재시도했다(2026-09-01 prod 실측).
-- 8/29 사고 복구 때 모델·라이선스는 지워졌는데 폐기 잡만 남았고, 폐기 API 가
-- /holder/models/{model_id}/revoke-vc 라 모델이 없으면 영영 성공할 수 없다.
-- 게다가 폐기 대기 잡은 opendid 수요라(#210) 그런 잡 하나가 홀더를 24/7 켜 둔다.
alter table public.fm_vc_revocation_jobs
  drop constraint if exists fm_vc_revocation_jobs_status_check;
alter table public.fm_vc_revocation_jobs
  add constraint fm_vc_revocation_jobs_status_check
  check (status = any (array['pending'::text, 'processing'::text, 'retry'::text,
                             'revoked'::text, 'dead'::text]));
