/* =============================================================
   features/library/Library.jsx — 보관함 (PRD §4.2).
   loading skeleton / empty / error / grid + new-creation tile.
   ============================================================= */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Button } from '@/components/Button.jsx';
import { Icon } from '@/components/Icon.jsx';
import { EmptyState, ErrorState, Skeleton } from '@/components/States.jsx';
import styles from './Library.module.css';

const STATUS = {
  done: { label: '완료', cls: 'stDone' },
  generating: { label: '생성 중', cls: 'stBusy' },
  draft: { label: '초안', cls: 'stDraft' },
};

export function Library() {
  const navigate = useNavigate();
  const resetFlow = useAppStore((s) => s.resetFlow);
  const [phase, setPhase] = useState('loading');
  const [items, setItems] = useState([]);

  const load = useCallback(() => {
    setPhase('loading');
    api.getLibrary({})
      .then((list) => { setItems(list); setPhase('ready'); })
      .catch(() => setPhase('error'));
  }, []);
  useEffect(() => { load(); }, [load]);

  const startCreate = () => { resetFlow(); navigate('/create/input'); };
  const open = (item) => navigate(`/editor/${item.id}`);

  const StatusPill = ({ status }) => {
    const s = STATUS[status] || STATUS.draft;
    return (
      <span className={styles.pill}>
        <span className={`${styles.dot} ${styles[s.cls]}`} />{s.label}
      </span>
    );
  };

  return (
    <div className={styles.wrap}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>보관함</h1>
          <p className={styles.sub}>지금까지 만든 상세페이지를 모아봤어요.</p>
        </div>
        <Button variant="primary" icon="plus" onClick={startCreate}>새 상세페이지</Button>
      </header>

      {phase === 'loading' && (
        <div className={styles.grid}>
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className={styles.skelCard}>
              <Skeleton h={280} r={12} />
              <Skeleton w="70%" h={14} />
              <Skeleton w="45%" h={12} />
            </div>
          ))}
        </div>
      )}

      {phase === 'error' && (
        <div className={styles.surface}>
          <ErrorState desc="보관함을 불러오지 못했어요." onRetry={load} />
        </div>
      )}

      {phase === 'ready' && items.length === 0 && (
        <div className={styles.surface}>
          <EmptyState
            icon="library"
            title="아직 만든 상세페이지가 없어요"
            desc="상품 사진 몇 장이면 첫 상세페이지를 만들 수 있어요."
            action={<Button variant="primary" icon="plus" onClick={startCreate}>첫 상세페이지 만들기</Button>}
          />
        </div>
      )}

      {phase === 'ready' && items.length > 0 && (
        <div className={styles.grid}>
          {items.map((it) => (
            <button type="button" className={styles.card} key={it.id} onClick={() => open(it)}>
              <div className={styles.cover}>
                <img src={it.cover} alt={it.title} loading="lazy" />
                <div className={styles.coverStatus}><StatusPill status={it.status} /></div>
              </div>
              <div className={styles.info}>
                <div className={styles.cardTitle}>{it.title}</div>
                <div className={styles.meta}><span>블록 {it.blocks}</span><span>·</span><span>{it.updatedAt}</span></div>
              </div>
            </button>
          ))}
          <button type="button" className={styles.newTile} onClick={startCreate}>
            <Icon name="plus" size={22} />
            <span>새 상세페이지</span>
          </button>
        </div>
      )}
    </div>
  );
}

export default Library;
