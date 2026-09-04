/* 관리자 콘솔 셸 — 좌측 고정 내비 + 우측 본문.

   콘솔은 화면 수가 적고 오래 열어 두는 도구라 상단 탭보다 사이드바가 맞는다(현재 위치가
   항상 보이고, 화면이 늘어도 세로로 늘어난다). 모바일은 대상이 아니다 — 작은 화면에서는
   내비가 위로 접힌다. */
import { NavLink, Outlet } from 'react-router-dom';
import { FileText, LayoutDashboard, ShieldCheck, Users } from 'lucide-react';
import { cn } from '@/lib/adminCn.js';

const NAV = [
  { to: '/', label: '대시보드', icon: LayoutDashboard, end: true },
  { to: '/applications', label: '지원서 검토', icon: FileText },
  { to: '/models', label: '모델·유저', icon: Users },
  { to: '/staff', label: '관리자 관리', icon: ShieldCheck },
];

export function AdminShell() {
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground sm:flex-row">
      <aside className="shrink-0 border-b border-border sm:w-56 sm:border-b-0 sm:border-r">
        <div className="px-5 py-4 text-sm font-semibold tracking-tight">Wearless 관리자</div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-3 sm:flex-col sm:overflow-visible">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => cn(
                'flex items-center gap-2 whitespace-nowrap rounded-md px-3 py-2 text-sm transition-colors',
                isActive ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/60',
              )}
            >
              <Icon size={16} aria-hidden />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="min-w-0 flex-1 px-5 py-6 sm:px-8">
        <Outlet />
      </main>
    </div>
  );
}
