import { Icon, IconButton } from '@/components/ui.jsx';
import { collectGifImages } from '@/lib/gif/gifMedia.js';
import styles from './BlockDownloadButton.module.css';

export function BlockDownloadButton({ block, index, onDownload }) {
  if (!collectGifImages([block]).length) return <IconButton name="download" onClick={() => onDownload(block, index)} title="이 블록 다운로드" />;
  const choose = (event, mode) => {
    event.currentTarget.closest('details').open = false;
    onDownload(block, index, mode);
  };
  return <details className={styles.wrap}
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false; }}
    onKeyDown={event => { if (event.key === 'Escape') { event.currentTarget.open = false; event.currentTarget.querySelector('summary').focus(); } }}>
    <summary className="iconbtn" aria-label="이 블록 다운로드" title="이 블록 다운로드"><Icon name="download" size={18} /></summary>
    <div className={`sb-addmenu ${styles.menu}`} aria-label="블록 다운로드 옵션">
      <button type="button" className="sb-addmenu-item" onClick={event => choose(event, 'zip')}>블록 이미지와 GIF 받기 (ZIP)</button>
      <button type="button" className="sb-addmenu-item" onClick={event => choose(event, 'gifs')}>GIF 원본만 받기</button>
    </div>
  </details>;
}
