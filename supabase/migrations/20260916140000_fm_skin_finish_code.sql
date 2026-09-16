-- 피부 보정 **3종**으로 정본 교체(2026-09-16 대표 결정).
--
--   prod     업스케일러 100% · 네거티브 끔   ← 지금 운영과 바이트 동일. 기본값.
--   texture  업스케일러 100% · 네거티브 켬   ← 모공·잡티가 살아난다.
--   soft50   업스케일러  50% · 네거티브 끔
--
-- 옛 정수 단계(100/50/0)는 "확대기 강도 + 네거티브" 조합이 지금 셋과 달라서, 같은 숫자에
-- 다른 뜻을 얹으면 원장이 거짓이 된다. 그래서 **컬럼을 새로 더한다** — 옛 컬럼(skin_finish)은
-- 그대로 두고 건드리지 않는다. 이 컬럼을 모르는 옛 코드는 skin_finish 를 계속 읽는데,
-- 그 값의 기본이 100(=지금 운영)이라 동작이 안 바뀐다.
alter table public.fm_models
  add column if not exists skin_finish_code text not null default 'prod';

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'fm_models_skin_finish_code_check'
  ) then
    alter table public.fm_models
      add constraint fm_models_skin_finish_code_check
      check (skin_finish_code in ('prod', 'texture', 'soft50'));
  end if;
end $$;

alter table public.fm_model_test_cuts
  add column if not exists skin_finish_code text;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'fm_model_test_cuts_skin_finish_code_check'
  ) then
    alter table public.fm_model_test_cuts
      add constraint fm_model_test_cuts_skin_finish_code_check
      check (skin_finish_code is null or skin_finish_code in ('prod', 'texture', 'soft50'));
  end if;
end $$;

-- 옛 값이 있으면 가장 가까운 자리로 옮긴다. 100 은 그때도 지금도 "지금 운영 그대로" 라
-- 정확히 같은 뜻이고, 50·0 은 네거티브가 켜져 있었으니 근사다(행이 있어도 화면·렌더가
-- 안 깨지게만 한다 — 이 값으로 다시 렌더하지 않는다).
update public.fm_model_test_cuts
   set skin_finish_code = case skin_finish
       when 100 then 'prod' when 50 then 'soft50' when 0 then 'texture' end
 where skin_finish_code is null and skin_finish is not null;

comment on column public.fm_models.skin_finish_code is
  '피부 보정 prod|texture|soft50. 관리자가 테스트컷 4장을 보낼 때 고른다. 얼굴 패스가 이 값으로 네거티브·크롭 확대를 정한다.';
comment on column public.fm_model_test_cuts.skin_finish_code is
  '이 컷을 만든 보정 prod|texture|soft50. 옛 컷은 null — 화면·승인이 그걸 견뎌야 한다.';

-- 관리자 화면이 보정별 묶음으로 읽고, 등록자 목록은 **보낸 보정 하나만** 읽는다.
create index if not exists fm_model_test_cuts_model_finish
  on public.fm_model_test_cuts (model_id, skin_finish_code, kind);
