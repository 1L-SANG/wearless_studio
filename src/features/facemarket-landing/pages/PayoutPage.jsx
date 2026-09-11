import { Navigate } from 'react-router-dom';

// 이전 메일과 북마크에서도 마이페이지의 수익 영역으로 이어져요.
export function PayoutPage() {
  return <Navigate to="/status#earnings" replace />;
}
