-- fm_model_loras.hair_fringe — 앞머리. 길이·머릿결·색으로는 표현이 안 되는 축이다.
--
-- 왜 필요한가(2026-09-13 운영 테스트컷 실측): LoRA 값이 short/black/straight 뿐이라 gpt-image 바탕이
-- **이마를 드러낸 짧은 머리**를 그렸는데, 실제 학습된 얼굴(v7)은 눈썹까지 오는 앞머리였다. 얼굴 패스는
-- 타원 안만 바꾸므로 헤어라인이 어긋난 채 남는다. 얼굴이 큰 클로즈업 3장에서는 타원 밖에 남은 원본
-- 머리 끝이 **공중에 뜬 검은 덩어리**로 보였다(쓸 수 없는 컷). 바탕을 처음부터 맞게 그리게 하는 것이
-- 유일한 수습이라, 그 정보를 LoRA 행에 둔다.
--
-- 값은 등록자의 현재 모습이 아니라 **이 LoRA 가 학습한 모습**이다(20260910100000 주석과 같은 규칙).
-- additive: 컬럼 추가 + 체크 제약만. 기존 행은 null 이고, null 이면 프롬프트 블록이 예전과 바이트 동일하다.

alter table public.fm_model_loras add column if not exists hair_fringe text;

alter table public.fm_model_loras drop constraint if exists fm_model_loras_hair_fringe_check;
alter table public.fm_model_loras add constraint fm_model_loras_hair_fringe_check
  check (hair_fringe is null or hair_fringe in ('none', 'side_swept', 'brow', 'eye'));

comment on column public.fm_model_loras.hair_fringe is
  '앞머리(none|side_swept|brow|eye). 이 LoRA 가 학습한 모습이며 등록자의 현재 모습이 아니다.';
