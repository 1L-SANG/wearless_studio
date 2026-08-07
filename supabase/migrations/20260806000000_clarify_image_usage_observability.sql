-- image_usage_events는 결제 원장이 아니라 best-effort 운영 관측 데이터다.
-- 최초 마이그레이션의 "append-only" 표현은 과금 호출을 누락 없이 추가한다는 의도였지만,
-- 운영 보정·개인정보 삭제까지 금지하는 DB 불변 원장으로 오해될 수 있어 설명만 바로잡는다.

comment on table public.image_usage_events is
  '이미지 생성 API 비용 관측용 best-effort 운영 데이터. 결제·정산 원장이 아니며 service role은 '
  '운영 보정 또는 삭제를 수행할 수 있다. QC 후보·재시도·이미지 없는 200 응답도 포함하고, '
  'usd가 null인 행은 0원이 아니라 금액 미확인이다. 조회: server/scripts/image_cost_report.py';
