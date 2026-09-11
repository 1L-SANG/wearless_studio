-- 마네킹컷 asset 에 "실인물 파생 아님" 마커 백필.
--
-- 0a7f33f6(2026-08-22) 이후 marker 없는 source='ai' 자산은 보수적으로 생체 파생으로
-- 분류되어 `/v1/assets/{id}/file` 이 R2 공개 URL 대신 `private, no-store` 인
-- `/v1/assets/{id}/bytes` 로 302 한다. 그 커밋은 detail_page·editor_image 종결자에만
-- 마커를 넣었고 마네킹 종결자 두 곳은 빠졌다. 보관함 커버(= 최신 마네킹컷)가 그 경로를
-- 타면서 한 장당 수백 KB를 Cloudflare 엣지 대신 API(us-east-1)에서, 그것도 매 진입마다
-- 새로 받게 됐다.
--
-- mannequin_cuts 에 물린 자산은 finalize_mannequin_success /
-- finalize_mannequin_adjust_success 두 곳만 쓴다(코드 전수 확인). 두 파이프라인 모두
-- FaceMarket 신원 합성 경로가 없어 산출물이 얼굴 없는 마네킹 착장컷이다 — 보수적 폴백이
-- 보호하려는 대상이 애초에 아니다. 그래서 이 조인 범위 안에서만 마커를 박는다.
--
-- 이미 마커가 있는 행은 건드리지 않는다(명시적으로 true 로 표시된 자산 보호 + 재실행 안전).
begin;

update public.assets a
set metadata = coalesce(a.metadata, '{}'::jsonb)
               || jsonb_build_object('facemarket_real_derived', false)
from public.mannequin_cuts mc
where mc.asset_id = a.id
  and not jsonb_exists(coalesce(a.metadata, '{}'::jsonb), 'facemarket_real_derived');

commit;
