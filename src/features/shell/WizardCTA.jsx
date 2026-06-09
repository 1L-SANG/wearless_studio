/* =============================================================
   shell/WizardCTA.jsx — centered CTA footer for wizard pages.
   ============================================================= */
import styles from './shell.module.css';

export function WizardCTA({ children }) {
  return <div className={styles.wizardCta}>{children}</div>;
}

export default WizardCTA;
