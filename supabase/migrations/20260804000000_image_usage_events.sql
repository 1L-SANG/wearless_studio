-- 이미지 생성 API 실비 원장 (내부용 — 2026-08-04).
--
-- 왜: 지금까지 Gemini 응답의 usageMetadata 는 받아만 오고 아무 데도 안 남았다. 그래서
-- "완성본 1장에 얼마 쓰나"를 요금표 × 추정 재시도 횟수(상한)로만 답할 수 있었다. QC 재생성·
-- best-of 후보처럼 출고되지 않는 이미지도 과금되므로, 200 응답 1건 = 여기 1행으로 남긴다.
--
-- 성격: 내부 관측 데이터. 셀러에게 노출되지 않고(RLS deny-all), 유실돼도 서비스에 영향이 없다
-- (인서트 실패는 앱에서 로그만 남기고 삼킨다). append-only, init.sql 무수정.

create table if not exists public.image_usage_events (
  id                  bigserial primary key,
  created_at          timestamptz not null default now(),
  -- job 문맥은 contextvar 로 붙는다. 스크립트·실험 호출은 null 이다.
  job_id              uuid references public.jobs(id) on delete set null,
  user_id             uuid,
  stage               text,          -- job kind (mannequin·detail_page·editor_image…)
  model               text not null, -- gemini-3-pro-image | gemini-3.1-flash-image …
  image_size          text not null, -- 1K | 2K | 4K
  input_tokens        integer not null default 0,
  output_text_tokens  integer not null default 0,
  output_image_tokens integer not null default 0,
  -- 달러. 단가표에 없는 모델이면 null (토큰만 남기고 나중에 재계산).
  usd                 numeric(12, 6),
  -- usage = 응답의 실제 토큰, table = 해상도별 요금표 폴백, unknown_model = 단가 미상.
  -- 집계할 때 추정치와 실측치를 섞어 보지 않으려고 남긴다.
  cost_source         text not null default 'usage',
  latency_ms          integer,
  has_image           boolean not null default true,  -- false = 200 인데 이미지 없음(요금은 발생)
  usage_raw           jsonb                            -- 단가 변경 시 과거 행 재계산용 원본
);

create index if not exists image_usage_events_created_at_idx
  on public.image_usage_events (created_at desc);
create index if not exists image_usage_events_job_idx
  on public.image_usage_events (job_id);

alter table public.image_usage_events enable row level security;
-- 정책 없음 = 익명·로그인 사용자 모두 접근 불가. service-role(서버·리포트 스크립트)만 읽고 쓴다.

comment on table public.image_usage_events is
  '이미지 생성 API 호출 1건 = 1행(내부 비용 관측 전용). 채택되지 않은 QC 후보·재생성도 포함한다 — '
  '완성본 1장당 실비는 total(usd) / 완성본 수로 계산한다. 조회: server/scripts/image_cost_report.py';
