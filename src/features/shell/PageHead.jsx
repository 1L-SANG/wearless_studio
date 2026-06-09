/* =============================================================
   shell/PageHead.jsx — centered wizard page heading + subhead.
   ============================================================= */
import styles from './shell.module.css';

export function PageHead({ title, sub }) {
  return (
    <div className={styles.pageHead}>
      <h1 className={styles.pageTitle}>{title}</h1>
      {sub && <p className={styles.pageSub}>{sub}</p>}
    </div>
  );
}

export default PageHead;
