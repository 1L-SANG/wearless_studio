/* =============================================================
   admin.wearless.kr 의 라우트 트리 — 모델 지원서 검토 콘솔.

   seller/facemarket 과 같은 다중 진입 분리다. 이 번들에는 셀러·모델 등록 화면이 실리지
   않는다(관리자 전용). 보안 경계는 프런트가 아니라 서버다 — 모든 지원서 조회·승인·거절
   API 가 repo.is_admin 을 강제한다. 이 호스트/라우팅은 UX 경계일 뿐이고, 비관리자가
   admin.wearless.kr 에 접속해도 API 가 403 을 준다.
   ============================================================= */
import { Routes, Route, Navigate } from 'react-router-dom';
import { RequireAuth } from '../guards.jsx';
import { AdminApplications } from '@/features/admin/AdminApplications.jsx';
import { isSupabaseConfigured } from '@/lib/supabase.js';

export default function AppAdmin() {
  if (!isSupabaseConfigured) {
    return (
      <div className="route-loading">
        설정 오류: Supabase 환경변수(VITE_SUPABASE_URL·VITE_SUPABASE_ANON_KEY)가 없습니다.
      </div>
    );
  }
  return (
    <Routes>
      <Route element={<RequireAuth />}>
        <Route index element={<AdminApplications />} />
        <Route path="applications" element={<AdminApplications />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
