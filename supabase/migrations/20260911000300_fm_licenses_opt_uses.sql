-- 모델의 **선택 동의** — 계약이 정한 "새 사용처는 별도 동의" 를 담는 칸.
--
-- 계약 근거(documents/legal/02 v1): 제2조 허용 변형은 장소 연출을 제외하고, 제3조 2항은 사용처를
-- 스튜디오·무지 배경으로 묶고, 제9조 6항은 타인·다른 모델과의 합성을 전면 금지한다. 그래서
-- 스타일링·미러 컷과 룩북 인물 교체는 **지금 계약으로는 불가**이며, 모델이 그 사용처에 따로
-- 동의했을 때만 열린다(동의하지 않아도 불이익 없음 — 기본값 false).
--
-- 저장 위치가 fm_licenses 인 이유: allowed_use/forbidden_use 와 같은 행이 곧 그 모델이 승인한
-- 사용 조건이고, issue_face_vc 가 그 행을 그대로 증서에 싣는다(app/facemarket.py:1093·1120).
-- 사용 조건이 바뀌면 계약 제2조에 따라 증서 재발급 + 이전 증서 폐기가 필요한데, 지금 코드에는
-- **재발급 흐름이 없다**(revoke_license 만 있다) — 새로 만들지 않고 보고한다.
--
-- additive · 기존 행은 전부 false(= 지금 동작 그대로: 스튜디오 전용).

alter table public.fm_licenses
  add column if not exists opt_location_cuts boolean not null default false,
  add column if not exists opt_lookbook_person_replace boolean not null default false,
  add column if not exists opt_consent_version text,
  add column if not exists opt_consented_at timestamptz;

comment on column public.fm_licenses.opt_location_cuts is
  '모델 동의: 스타일링·미러 등 장소 연출 컷에 이 모델의 얼굴을 쓴다(계약 v2 제2조 ⑤).';
comment on column public.fm_licenses.opt_lookbook_person_replace is
  '모델 동의: 브랜드 룩북 사진의 인물(얼굴·머리)을 이 모델로 교체한다(계약 v2 제9조 6항 예외).';
comment on column public.fm_licenses.opt_consent_version is
  '동의한 사용 조건 문서 버전. 조건이 바뀌면 재동의 + 증서 재발급 대상이다.';
