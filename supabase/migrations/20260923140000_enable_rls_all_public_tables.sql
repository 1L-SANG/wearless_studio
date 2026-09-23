-- 공개 스키마 전 테이블에 RLS 를 보장한다.
--
-- ## 왜 (2026-09-23 실측)
-- 프로덕션(ftjxwxuactfjopbokbni)에서 프런트 번들에 박힌 publishable 키만으로
-- `public.assets`(122행)와 `public.admin_audit_log`(36행)가 **읽히고 쓰였다**:
--
--     GET   /rest/v1/assets?select=*        → 200, content-range 0-0/122
--     PATCH /rest/v1/assets?id=eq.<없는 id> → 200 []   (거부면 401/42501 이 온다)
--
-- 원인은 두 겹이다.
--   ① `admin_audit_log` 를 포함한 9개 테이블은 **마이그레이션에 RLS 문장 자체가 없었다**
--      (아래 목록). 만든 사람이 "Data API 는 안 쓰니까" 로 넘어간 자리다.
--   ② `assets` 는 init.sql 271 행에 `enable row level security` 가 **있는데도** 라이브에서는
--      꺼져 있었다 = 드리프트. us-east-1 이전 때 CI 의 SUPABASE_DB_URL 이 옛 DB 를 가리켜
--      마이그레이션이 프로덕션에 안 붙던 사고(2026-08-29)와 같은 뿌리다.
--
-- ①만 고치면 ②는 다음에 또 샌다. 그래서 이 마이그레이션은 **선언 + 실측 보정** 두 벌이다.
--
-- ## 앱이 안 깨지는 이유
-- 서버(FastAPI)는 DATABASE_URL 의 소유자 롤로 직접 붙고, **테이블 소유자는 RLS 를 우회한다**
-- (`force row level security` 를 쓰지 않는 한). service_role 키도 BYPASSRLS 다.
-- 프런트는 PostgREST 를 한 줄도 쓰지 않는다(`supabase.from(` 호출 0건) — 인증만 쓴다.
-- 그래서 정책을 하나도 만들지 않는다: 정책이 없는 RLS = anon·authenticated 전면 거부이고,
-- 그게 이 레포가 원하는 상태다. 나중에 PostgREST 로 읽을 테이블이 생기면 그때 그 테이블에만
-- 정책을 붙여라. **여기서 정책을 미리 열어 두지 마라.**
--
-- ## 이걸로 충분하지 않다
-- RLS 는 2차 방어선이다. 1차는 Data API 를 끄는 것(Settings → API → Exposed schemas 에서
-- `public` 제거)이고, 그건 대시보드 작업이라 이 파일이 대신해 줄 수 없다.

-- ── ① 마이그레이션에 RLS 선언이 없던 9개 ────────────────────────────────────────
-- 뒤 셋(personalization_*)과 fm_licenses·fm_settlements 는 생체·정산 데이터를 담는다.
-- 지금 비어 있어도 채워지는 날 그대로 새므로 같이 잠근다.
alter table public.admin_audit_log enable row level security;
alter table public.fm_licenses enable row level security;
alter table public.fm_models enable row level security;
alter table public.fm_output_records enable row level security;
alter table public.fm_publication_records enable row level security;
alter table public.fm_settlements enable row level security;
alter table public.personalization_audit_log enable row level security;
alter table public.personalization_consents enable row level security;
alter table public.personalization_profiles enable row level security;

-- ── ② 드리프트 보정: 그래도 꺼져 있는 것이 있으면 전부 켠다 ──────────────────────
-- 위 목록은 '마이그레이션에 문장이 없던 것'이고, 이 블록은 '실제 DB 에서 꺼져 있는 것'을 본다.
-- `assets` 처럼 선언은 있는데 라이브에서 꺼져 있던 경우가 여기서 잡힌다.
-- 멱등이다 — 이미 켜진 테이블은 건드리지 않고, 파티션·외래 테이블·뷰는 대상이 아니다.
do $$
declare
  target record;
  fixed text[] := '{}';
begin
  for target in
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind = 'r'            -- 일반 테이블만(뷰·시퀀스·파티션 제외)
      and not c.relrowsecurity
    order by c.relname
  loop
    execute format('alter table public.%I enable row level security', target.relname);
    fixed := fixed || target.relname;
  end loop;

  if array_length(fixed, 1) is null then
    raise notice 'RLS 드리프트 없음 — 모든 public 테이블이 이미 켜져 있다';
  else
    -- 어떤 테이블이 꺼져 있었는지는 사고 분석에 필요하다. 로그로 남긴다.
    raise notice 'RLS 를 새로 켠 테이블 %개: %', array_length(fixed, 1), array_to_string(fixed, ', ');
  end if;
end $$;
