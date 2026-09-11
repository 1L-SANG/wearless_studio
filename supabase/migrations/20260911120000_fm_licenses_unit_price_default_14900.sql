-- 가격 정본: documents/legal/00_facemarket_legal_notice_map_v1.md 부록 C.
-- 기존 행은 보존하고 앞으로 생성되는 라이선스의 플랫폼 공통 단가만 14,900원으로 맞춘다.
alter table public.fm_licenses alter column unit_price set default 14900;
