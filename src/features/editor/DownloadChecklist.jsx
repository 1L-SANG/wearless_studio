import { downloadChecks } from './editorDownloadWarnings.js';
import styles from './DownloadChecklist.module.css';

const LABELS = {
  failed: (count) => `만들지 못한 컷 ${count}장`,
  empty: (count) => `빈 사진 자리 ${count}곳`,
  notice: (count) => `비어 있는 고시정보 ${count}칸`,
  pending: (count) => `아직 만드는 중인 컷 ${count}장`,
};

export function DownloadChecklist({ blocks, busy, onClose, onJumpToBlock }) {
  const items = downloadChecks(blocks);
  if (!items.length) return null;

  const jump = (blockId) => {
    if (busy) return;
    onClose();
    onJumpToBlock(blockId);
  };

  return (
    <section className={styles.box} aria-label="받기 전에 확인할 것">
      <div className={styles.head}>
        <strong className={styles.title}>받기 전에 확인해 주세요</strong>
        <span className={styles.hint}>그대로 받아도 돼요</span>
      </div>
      <ul className={styles.list} role="list">
        {items.map(({ kind, count, blockId, labels }) => {
          const label = LABELS[kind](count);
          const pending = kind === 'pending';
          const action = kind === 'notice' ? '채우기' : '보러 가기';
          return (
            <li key={kind} className={`${styles.row}${pending ? ` ${styles.muted}` : ''}`}>
              <span className={styles.text}>
                {label}
                {kind === 'notice' && <span className={styles.sub}>{labels.join(' · ')}</span>}
                {pending && <span className={styles.sub}>다 되면 자동으로 들어가요</span>}
              </span>
              {!pending && (
                <button type="button" className={styles.jump} aria-label={`${label} ${action}`}
                  disabled={busy} onClick={() => jump(blockId)}>
                  {action}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
