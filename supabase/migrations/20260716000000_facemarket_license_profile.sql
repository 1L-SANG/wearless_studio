-- =============================================================
-- 20260716000000_facemarket_license_profile.sql  (step02 — 라이선스 발급 개인화 연동)
-- 얼굴 라이선스가 개인화 프로필(3각도 QC 통과 얼굴)을 대상으로 발행될 수 있게 하는 포인터.
--
-- 제안서 step02("모델이 얼굴과 사용 조건을 직접 정하면 검증 가능한 라이선스(VC)로 발행"):
--   1.얼굴 업로드(다각도 3장 권장) = 개인화 프로필의 front|side|angle45 슬롯 재사용
--   2.라이선스 조건        = 기존 allowed/forbidden/unit_price/valid_days
-- 이 컬럼이 그 둘을 잇는다 — POST /v1/facemarket/licenses 가 profile_id 를 받으면
-- 프로필 front 슬롯의 r2_key/image_digest 를 라이선스 얼굴로 삼는다(복사 아님, 참조).
--
-- **nullable** — 기존 face 1장 직접 업로드 라이선스와 완전 호환(회귀 0). null = 레거시 경로.
--
-- on delete set null (파기 캐스케이드 계약):
--   · 프로필 행이 hard delete 되면(계정 삭제 → auth.users cascade) 라이선스 **행은 남고**
--     얼굴 참조만 끊긴다. 정산 미러(fm_settlements → license_id)·영수증 이력이 보존돼야 하므로
--     cascade delete 는 금지다.
--   · 개인화 파기 잡(personalization_purge_job)은 프로필 행을 지우지 않고 status='purged' +
--     본문 null 처리만 하므로 이 FK 는 발화하지 않는다. 대신 잡이 얼굴 R2 객체를 지우므로
--     라이선스의 face_image_key 는 dangling 이 되고 얼굴 게이트가 404 로 닫힌다 —
--     "라이선스 행은 남되 얼굴만 사라진다"는 동일 결과로 수렴(의도된 우아한 강등).
-- 멱등(add column if not exists + 제약 존재 검사) — 재실행 안전.
-- =============================================================

alter table public.fm_licenses
  add column if not exists profile_id uuid;

-- FK 는 별도 추가 — `add column if not exists` 로는 재실행 시 제약이 붙지 않고,
-- `add constraint` 에는 if not exists 문법이 없어 카탈로그 선확인으로 멱등을 만든다(선례 없음 → DO 블록).
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.fm_licenses'::regclass
      and conname = 'fm_licenses_profile_id_fkey'
  ) then
    alter table public.fm_licenses
      add constraint fm_licenses_profile_id_fkey
      foreign key (profile_id) references public.personalization_profiles(id)
      on delete set null;
  end if;
end $$;

-- 프로필 → 라이선스 역참조(파기 영향 범위 조회·중복 발행 점검). 부분 인덱스 = 레거시 null 행 제외.
create index if not exists fm_licenses_profile_id_idx
  on public.fm_licenses(profile_id) where profile_id is not null;
