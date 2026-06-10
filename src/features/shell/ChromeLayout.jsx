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

  // background orb glow fixed at the approved 75% (prototype app.jsx).
  // The wizard stepper now lives centered inside TopNav (see shell.jsx),
  // so the hero content starts directly under the nav.
  return (
    <div className="app-shell" style={{ '--glow-a': 0.75 }}>
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
