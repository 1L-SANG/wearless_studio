-- fm_model_loras.lora_sha256 — 원격 렌더 파드가 **받은 가중치가 그 파일인지** 확인하는 값.
--
-- 가중치는 요청마다 presigned GET 으로 파드에 전달된다(파드에 R2 자격증명을 두지 않기 위해서).
-- URL 은 만료가 짧을 뿐 내용을 보증하지 않으므로, 받은 파일의 sha256 이 이 값과 다르면
-- 파드가 그 파일을 지우고 400 을 돌려준다(다른 사람 얼굴이 섞여 나가는 것을 막는다).
--
-- not null 이지만 additive 하다: 이 테이블은 20260910100000 에서 막 생겼고 아직 행이 없다.
-- 혹시 행이 있는 환경이면 NOT NULL 만 건너뛴다(값을 지어내지 않는다 — 시드 스크립트가 채운다).

alter table public.fm_model_loras add column if not exists lora_sha256 text;

do $$
begin
  if not exists (select 1 from public.fm_model_loras where lora_sha256 is null) then
    alter table public.fm_model_loras alter column lora_sha256 set not null;
  else
    raise notice 'fm_model_loras.lora_sha256: 값이 빈 행이 있어 not null 을 건너뛴다';
  end if;
end $$;
