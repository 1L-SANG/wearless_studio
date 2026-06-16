/* =============================================================
   lib/supabase — Supabase 클라이언트 (인증 전용).
   프론트는 Supabase에 **인증만** 직접 접근하고, 데이터는 FastAPI(api.wearless.kr)가
   담당한다 (backend_integration_plan §1, Data API OFF). 세션의 access_token 은
   httpAdapter 가 Authorization: Bearer 로 주입한다.
   SPA OAuth 권장 설정: PKCE + detectSessionInUrl(리다이렉트 복귀 시 세션 교환).
   ============================================================= */
import { createClient } from '@supabase/supabase-js';

const url = import.meta.env.VITE_SUPABASE_URL;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!url || !anonKey) {
  // 빌드는 통과시키되 런타임에 원인이 분명하도록.
  console.error('VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY 가 설정되지 않았습니다 (.env.local).');
}

export const supabase = createClient(url, anonKey, {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: true,
    flowType: 'pkce',
  },
});
