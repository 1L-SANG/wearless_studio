-- Editor vary 결과 무결성 (Phase 3 P0-C 7/N, 2026-08-02, 20260801060000 후속).
--
-- 6/N 이 wardrobe_images 에 edit_session_id·qc_status 를 더했지만 **둘의 관계**는 열려
-- 있었다. 한쪽만 채워진 행은 두 가지 거짓 중 하나다: 판정 없이 세션에 붙었거나(qc_status
-- null), 어느 편집의 결과인지 모르는 판정이 붙었거나(edit_session_id null).
--
-- REJECT 는 여기 값이 아니다 — 거부된 결과는 애초에 wardrobe 행을 만들지 않는다.

alter table public.wardrobe_images
  drop constraint if exists wardrobe_images_edit_lineage_check;
alter table public.wardrobe_images
  add constraint wardrobe_images_edit_lineage_check check (
    (edit_session_id is null and qc_status is null)
    or (edit_session_id is not null
        and qc_status in ('pass', 'review_required')));

comment on constraint wardrobe_images_edit_lineage_check on public.wardrobe_images is
  '세션과 판정은 함께 있거나 함께 없다. legacy·mode:new·플래그 off 는 둘 다 null 이고, '
  'Phase 3 vary 결과는 둘 다 있다. 기존 행은 전부 (null, null) 이라 그대로 통과한다.';

-- 세션 하나에 wardrobe 결과 하나. 같은 편집이 두 번 진열되면 사용자는 같은 요청의 결과를
-- 두 개 보고 어느 쪽이 판정된 것인지 알 수 없다.
create unique index if not exists wardrobe_images_one_per_edit_session
  on public.wardrobe_images (edit_session_id) where edit_session_id is not null;
