/* =============================================================
   shell/ChromeLayout.jsx — app chrome wrapper for non-editor routes.
   Background orb/aurora (verbatim from prototype app.jsx) + TopNav +
   main outlet, with the dots Stepper on create-flow steps.
   ============================================================= */
import { useEffect } from 'react';
import { Outlet } from 'react-router-dom';
import { TopNav } from '@/features/shell/shell.jsx';
import { useAppStore } from '@/store/useAppStore.js';

export function ChromeLayout() {
  const loadAccount = useAppStore((s) => s.loadAccount);
  const loadCatalogs = useAppStore((s) => s.loadCatalogs);

  useEffect(() => { loadAccount(); loadCatalogs(); }, [loadAccount, loadCatalogs]);

  // Background glow intensity uses the CSS default. Final orb/edge opacity is defined in app.css.
  // The wizard stepper now lives centered inside TopNav (see shell.jsx),
  // so the hero content starts directly under the nav.
  return (
    <div className="app-shell">
      <div className="app-bg">
        <div className="edge" />
        <div className="orb-bg"><div className="l1" /><div className="l2" /><div className="l3" /><div className="hi" /></div>
      </div>
      <TopNav />
      <div className="app-main">
        <Outlet />
      </div>
    </div>
  );
}

export default ChromeLayout;
