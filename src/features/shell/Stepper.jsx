/* =============================================================
   shell/Stepper.jsx — 4-dot progress stepper (PRD §3).
   제품 정보·분석 → 마네킹컷 → 콘티보드 → 에디터.
   input+analysis collapse into step 0; generating shares editor.
   ============================================================= */
import styles from './shell.module.css';

export const WIZARD_STEPS = [
  { key: 'input', label: '제품 정보·분석' },
  { key: 'mannequin', label: '마네킹컷' },
  { key: 'storyboard', label: '콘티보드' },
  { key: 'editor', label: '에디터' },
];

// route step → stepper index
export const STEP_INDEX = { input: 0, analysis: 0, mannequin: 1, storyboard: 2, generating: 3, editor: 3 };

export function Stepper({ current }) {
  const idx = STEP_INDEX[current] ?? 0;
  return (
    <div className={styles.stepper}>
      {WIZARD_STEPS.map((s, i) => (
        <div key={s.key} className={`${styles.step} ${i < idx ? styles.stepDone : ''} ${i === idx ? styles.stepActive : ''}`}>
          {i > 0 && <span className={styles.stepLine} />}
          <span className={styles.stepDot} title={s.label} />
        </div>
      ))}
    </div>
  );
}

export default Stepper;
