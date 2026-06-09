/* =============================================================
   shell/TopNav.jsx — top navigation (PRD §4.1).
   wearless | 상세페이지 제작 | 보관함 | 크레딧 | 플랜 | 프로필
   Hidden inside the editor (handled by the router layout).
   ============================================================= */
import { NavLink, useNavigate } from 'react-router-dom';
import { Icon } from '@/components/Icon.jsx';
import { useAppStore } from '@/store/useAppStore.js';
import styles from './shell.module.css';

export function TopNav() {
  const account = useAppStore((s) => s.account);
  const resetFlow = useAppStore((s) => s.resetFlow);
  const navigate = useNavigate();

  const startCreate = () => { resetFlow(); navigate('/create/input'); };

  return (
    <nav className={styles.topnav}>
      <NavLink to="/create/input" className={styles.brand} onClick={resetFlow}>wearless</NavLink>
      <div className={styles.navLinks}>
        <button type="button" className={styles.navLink} onClick={startCreate}>상세페이지 제작</button>
        <NavLink to="/library" className={({ isActive }) => `${styles.navLink} ${isActive ? styles.navActive : ''}`}>보관함</NavLink>
      </div>
      <div className={styles.navRight}>
        <span className={styles.creditBadge}>
          <Icon name="coins" size={15} stroke={1.8} />크레딧 <b>{account?.credits ?? '—'}</b>
        </span>
        <span className={styles.planBadge}>{account?.plan ?? ''}</span>
        {account?.avatar
          ? <img className={styles.avatar} src={account.avatar} alt={account.name} title={account.name} />
          : <span className={styles.avatar} aria-hidden="true" />}
      </div>
    </nav>
  );
}

export default TopNav;
