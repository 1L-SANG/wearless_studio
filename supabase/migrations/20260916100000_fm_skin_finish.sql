-- 피부 보정 단계 — **등록자 본인이** 테스트컷을 승인할 때 고른다(셀러는 못 바꾼다).
--
-- 100 = 지금 그대로(네거티브 없음 + 얼굴 크롭 ESRGAN 100%)
--  50 = 네거티브 켬 + ESRGAN 결과와 Lanczos 확대본을 0.5로 섞음
--   0 = 네거티브 켬 + ESRGAN 끔
--
-- 2026-09-16 16장 실측: 피부 결을 실제로 바꾸는 손잡이는 **네거티브 문구**다(피부 결 +11~19%,
-- 닮은 점수 변화는 ±0.03 오차 범위). 업스케일러 강도는 100↔50 차이가 거의 없다.
--
-- 추가만 하는 마이그레이션이다 — 기본값 100 이 지금 동작이라, 이 컬럼을 모르는 옛 코드가
-- 그대로 돌아도 결과가 안 바뀐다.
alter table public.fm_models
  add column if not exists skin_finish int not null default 100;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'fm_models_skin_finish_check'
  ) then
    alter table public.fm_models
      add constraint fm_models_skin_finish_check check (skin_finish in (0, 50, 100));
  end if;
end $$;

comment on column public.fm_models.skin_finish is
  '피부 보정 단계 100|50|0. 등록자가 테스트컷 승인 때 고른다. 얼굴 패스가 이 값으로 네거티브·크롭 확대를 정한다.';

-- 테스트컷은 단계별로 나란히 보여 준다 — 어느 장이 어느 단계인지 행에 남아야 승인 때
-- 그 값을 fm_models 로 복사할 수 있다. 옛 컷(단계 없음)은 null 이다.
alter table public.fm_model_test_cuts
  add column if not exists skin_finish int;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'fm_model_test_cuts_skin_finish_check'
  ) then
    alter table public.fm_model_test_cuts
      add constraint fm_model_test_cuts_skin_finish_check
      check (skin_finish is null or skin_finish in (0, 50, 100));
  end if;
end $$;

comment on column public.fm_model_test_cuts.skin_finish is
  '이 컷을 만든 보정 단계 100|50|0. 옛 컷은 null — 화면·승인이 그걸 견뎌야 한다.';
