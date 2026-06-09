/* =============================================================
   shell/ChromeLayout.jsx — app chrome wrapper for non-editor routes.
   Renders background glow + TopNav + main outlet, and shows the
   Stepper on create-flow steps (input / mannequin / storyboard).
   ============================================================= */
import { useEffect } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { TopNav } from '@/features/shell/TopNav.jsx';
import { Stepper } from '@/features/shell/Stepper.jsx';
import { useAppStore } from '@/store/useAppStore.js';
import styles from './shell.module.css';

const STEPPER_STEPS = ['input', 'mannequin', 'storyboard'];

export function ChromeLayout() {
  const { pathname } = useLocation();
  const loadAccount = useAppStore((s) => s.loadAccount);
  const loadCatalogs = useAppStore((s) => s.loadCatalogs);

  useEffect(() => { loadAccount(); loadCatalogs(); }, [loadAccount, loadCatalogs]);

  const step = pathname.startsWith('/create/') ? pathname.split('/')[2] : null;
  const showStepper = STEPPER_STEPS.includes(step);

  return (
    <div className={styles.appShell}>
      <div className="app-bg" />
      <TopNav />
      <main className={`app-main ${styles.main}`}>
        {showStepper && <Stepper current={step} />}
        <Outlet />
      </main>
    </div>
  );
}

export default ChromeLayout;
