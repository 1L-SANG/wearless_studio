/* =============================================================
   lib/supabase — Supabase 클라이언트 (인증 전용).
   프론트는 Supabase에 **인증만** 직접 접근하고, 데이터는 FastAPI(api.wearless.kr)가
   담당한다 (backend_integration_plan §1, Data API OFF). 세션의 access_token 은
   httpAdapter 가 Authorization: Bearer 로 주입한다.
   SPA OAuth 설정: PKCE. 리다이렉트 복귀의 code 교환은 AuthProvider 가 명시적으로 처리한다.
   ============================================================= */
import { createClient } from '@supabase/supabase-js';

const url = import.meta.env.VITE_SUPABASE_URL;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

// 미설정 시 createClient 가 모듈 로드에서 throw → SPA 전체 화이트스크린이 된다.
// 그걸 막기 위해 형식상 유효한 placeholder 로 생성하고, App 이 이 플래그로 설정 안내를 렌더.
export const isSupabaseConfigured = Boolean(url && anonKey);

if (!isSupabaseConfigured) {
  console.error(
    'Supabase 환경변수 미설정: VITE_SUPABASE_URL · VITE_SUPABASE_ANON_KEY (.env.local 또는 Vercel 환경변수).',
  );
}

export const supabase = createClient(
  url || 'https://placeholder.supabase.co',
  anonKey || 'placeholder-anon-key',
  {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: false,
      flowType: 'pkce',
    },
  },
);
