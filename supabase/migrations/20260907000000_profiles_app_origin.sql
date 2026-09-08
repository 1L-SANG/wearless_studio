-- =============================================================
-- 20260907000000_profiles_app_origin.sql
-- profiles.app_origin — 이 계정이 **어느 앱에서 왔는가**.
--
-- 지금 셀러(ai.wearless.kr)와 FaceMarket(facemarket.wearless.kr)은 Supabase 프로젝트
-- 하나를 공유한다. auth.users 도 profiles 도 한 테이블이라, 콘솔에서 두 서비스의 가입자가
-- 구분 없이 섞여 보인다. 계정을 물리적으로 쪼개는 대신 출처를 한 칸에 적는다 —
-- 같은 사람이 두 서비스를 다 쓰는 경우가 실제로 있고(운영진), 그때 계정이 둘로 갈라지면
-- 크레딧·세션·모델 소유가 같이 갈라진다.
--
-- 값은 셋뿐이다. 'both' 는 "가입은 한쪽, 나중에 반대쪽도 씀" 을 조인 없이 표현하려고
-- 둔다(멤버십 테이블을 만들면 목록 화면마다 조인이 붙는다).
--   seller     — ai.wearless.kr 에서 왔다
--   facemarket — facemarket.wearless.kr 에서 왔다
--   both       — 양쪽 다 쓴다
--   null       — 아직 모른다 (다음 로그인 때 POST /v1/me/app-origin 이 채운다)
--
-- 왜 회원가입 트리거(handle_new_user)가 아니라 로그인 후 API 인가:
-- 로그인이 구글·카카오 OAuth 라, auth.users INSERT 시점에 서버가 보는 것은 공급자 콜백뿐이고
-- 거기엔 사용자가 어느 호스트에서 출발했는지가 없다. 브라우저가 붙이는 Origin 헤더를 볼 수
-- 있는 첫 지점이 로그인 이후의 API 호출이다.
-- =============================================================

alter table public.profiles
  add column if not exists app_origin text
    check (app_origin in ('seller', 'facemarket', 'both'));

-- 백필은 관리자만 'both' 로 채운다. 나머지 기존 가입자는 null(=미상) 로 남긴다.
-- 근거 없이 한쪽으로 몰아 넣으면 화면이 "확실하지 않다" 를 말할 방법을 잃는다 —
-- 틀린 라벨은 빈 칸보다 나쁘다. 미상 계정은 다음 로그인 때 스스로 채워진다.
update public.profiles
   set app_origin = 'both'
 where role = 'admin' and app_origin is null;

-- 콘솔 목록의 출처 필터.
create index if not exists profiles_app_origin_idx
  on public.profiles (app_origin);

-- 콘솔 목록의 정렬 + keyset 페이징 (created_at desc, user_id desc).
create index if not exists profiles_created_at_idx
  on public.profiles (created_at desc, user_id desc);
