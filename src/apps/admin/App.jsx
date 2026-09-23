/* =============================================================
   admin.wearless.kr 의 라우트 트리 — 모델 지원서 검토 콘솔.

   seller/facemarket 과 같은 다중 진입 분리다. 이 번들에는 셀러·모델 등록 화면이 실리지
   않는다(관리자 전용). 보안 경계는 프런트가 아니라 서버다 — 모든 지원서 조회·승인·거절
   API 가 repo.is_admin 을 강제한다. 이 호스트/라우팅은 UX 경계일 뿐이고, 비관리자가
   admin.wearless.kr 에 접속해도 API 가 403 을 준다.
   ============================================================= */
import { Routes, Route, Navigate } from 'react-router-dom';
import { RequireAuth } from '../guards.jsx';
import { RequireDevice } from './RequireDevice.jsx';
import { KakaoCallback } from '@/features/auth/KakaoCallback.jsx';
import { AdminShell } from '@/features/admin/AdminShell.jsx';
import { AdminApplications } from '@/features/admin/AdminApplications.jsx';
import { AdminBankTransfers } from '@/features/admin/AdminBankTransfers.jsx';
import { AdminDashboard } from '@/features/admin/AdminDashboard.jsx';
import { AdminEnrollmentReview } from '@/features/admin/AdminEnrollmentReview.jsx';
import { AdminModels } from '@/features/admin/AdminModels.jsx';
import { AdminStaff } from '@/features/admin/AdminStaff.jsx';
import { AdminUsageReports } from '@/features/admin/AdminUsageReports.jsx';
import { AdminPayoutStatements } from '@/features/admin/AdminPayoutStatements.jsx';
import { AdminUsers } from '@/features/admin/AdminUsers.jsx';
import { isSupabaseConfigured } from '@/lib/supabase.js';
import { redirectToOwnDocumentHost } from '@/lib/host.js';

export default function AppAdmin() {
  // 셀러·facemarket 도메인에서 /admin.html 을 직접 연 경우 관리자 호스트로 되돌린다(host.js).
  if (redirectToOwnDocumentHost('admin.wearless.kr')) {
    return <div className="route-loading">관리자 콘솔로 이동 중이에요…</div>;
  }
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
        {/* 기기 게이트 — 로그인 뒤·콘솔 셸 앞. 서버 가드가 진짜 판정이고 이건 그 판정의 화면이다. */}
        <Route element={<RequireDevice />}>
          <Route element={<AdminShell />}>
            <Route index element={<AdminDashboard />} />
            <Route path="applications" element={<AdminApplications />} />
            <Route path="review" element={<AdminEnrollmentReview />} />
            <Route path="usage-reports" element={<AdminUsageReports />} />
            <Route path="payout-statements" element={<AdminPayoutStatements />} />
            <Route path="bank-transfers" element={<AdminBankTransfers />} />
            <Route path="models" element={<AdminModels />} />
            <Route path="users" element={<AdminUsers />} />
            <Route path="staff" element={<AdminStaff />} />
          </Route>
        </Route>
      </Route>
      {/* 카카오 OIDC 로그인 착지점 — **RequireAuth·RequireDevice 밖**이다.
          이 콘솔은 원래 모든 화면이 두 가드 안에 있는데, 콜백은 정의상 아직 로그인 전이라
          가드 안에 두면 guards.jsx 의 FacemarketLoginPrompt 가 떠서 로그인 모달이 다시
          열리고 인가코드는 소비되지 않는다. catch-all 보다 앞에 둔다.
          ⚠️ 위 RequireAuth → RequireDevice → AdminShell 세 줄의 **등장 순서는 건드리지
          마라** — tests/frontend/admin-device.test.mjs 가 indexOf 로 그 순서를 본다. */}
      <Route path="auth/kakao/callback" element={<KakaoCallback />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
