-- 생성 중 컷 미리보기(2026-09-26, 오너 결정 b).
--
-- REAL(FaceMarket 실존 모델) 얼굴 컷은 최종 권한 펜스 전까지 출력 주소·키·정리 표식 id 를
-- 이벤트 원장(job_events)에 싣지 않는다(492cbc64). 그래서 대기 화면은 '완성됐어요' 타일만
-- 보였다. 이제 미리보기 라우트(GET /v1/projects/{p}/jobs/{j}/cuts/{block}/preview)가 요청마다
-- 소유·라이선스를 다시 확인한 뒤에만 바이트를 보낸다. 그 라우트가 "이 잡의 이 콘티 블록 자리에
-- 올라간 출력"을 찾을 곳이 여기다 — 원장이 아니라 서버 전용 정리 표식에 블록 id 만 적는다.
--
-- 정리 표식은 출력의 수명과 같이 간다: 올리기 전에 생기고, 성공 종결(assets 행)·삭제 확인 때
-- 지워진다. 표식이 없으면 미리보기도 없다. 복제 컷은 원본 출력 하나를 여러 블록이 같이 쓰므로
-- 배열이다. 추가만 하는 nullable 아님 컬럼(상수 기본값) — 테이블 재작성 없이 붙는다.
alter table public.ai_output_cleanup_intents
  add column if not exists preview_block_ids text[] not null default '{}';
