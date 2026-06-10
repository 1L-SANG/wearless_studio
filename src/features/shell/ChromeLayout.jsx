/* =============================================================
   shell/ChromeLayout.jsx — app chrome wrapper for non-editor routes.
   Background orb/aurora (verbatim from prototype app.jsx) + TopNav +
   main outlet, with the dots Stepper on create-flow steps.
   ============================================================= */
import { useEffect } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { TopNav, Stepper } from '@/features/shell/shell.jsx';
import { useAppStore } from '@/store/useAppStore.js';

const STEPPER_STEPS = ['input', 'mannequin', 'storyboard'];

export function ChromeLayout() {
  const { pathname } = useLocation();
  const loadAccount = useAppStore((s) => s.loadAccount);
  const loadCatalogs = useAppStore((s) => s.loadCatalogs);

  useEffect(() => { loadAccount(); loadCatalogs(); }, [loadAccount, loadCatalogs]);

  const step = pathname.startsWith('/create/') ? pathname.split('/')[2] : null;
  const showStepper = STEPPER_STEPS.includes(step);

  // background orb glow fixed at the approved 75% (prototype app.jsx)
  return (
    <div className="app-shell" style={{ '--glow-a': 0.75 }}>
      <div className="app-bg">
        <div className="edge" />
        <div className="orb-bg"><div className="l1" /><div className="l2" /><div className="l3" /><div className="hi" /></div>
      </div>
      <TopNav />
      <div className="app-main">
        {showStepper && <Stepper current={step} />}
        <Outlet />
      </div>
    </div>
  );
}

export default ChromeLayout;
