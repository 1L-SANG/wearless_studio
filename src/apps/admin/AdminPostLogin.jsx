import { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { forgetPostLogin, readPostLogin } from '@/features/auth/AuthProvider.jsx';
import { AdminDashboard } from '@/features/admin/AdminDashboard.jsx';
import { adminReturnTarget } from './adminReturnTarget.js';

export function AdminPostLogin() {
  const [target] = useState(() => adminReturnTarget(readPostLogin()));

  useEffect(() => {
    if (target && adminReturnTarget(readPostLogin()) === target) forgetPostLogin();
  }, [target]);

  return target ? <Navigate to={target} replace /> : <AdminDashboard />;
}
