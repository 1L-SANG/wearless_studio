/* =============================================================
   product-input/UploadWell.jsx — one upload slot (PRD §5.3).
   Click to "upload" (mock places a placeholder + fake file meta).
   Shows filename / size / format, supports delete.
   ============================================================= */
import { Icon } from '@/components/Icon.jsx';
import styles from './ProductInput.module.css';

export function UploadWell({ label, required, image, onAdd, onRemove }) {
  return (
    <div className={styles.well}>
      <div className={styles.wellHead}>
        <span className={styles.wellLabel}>{label}{required && <span className={styles.req}>*</span>}</span>
        {image && <button type="button" className={styles.wellRemove} onClick={onRemove} aria-label="삭제"><Icon name="trash" size={14} /></button>}
      </div>

      {image ? (
        <div className={styles.wellFilled}>
          <img src={image.src} alt={label} />
          <div className={styles.wellMeta}>
            <span className={styles.wellFile} title={image.file?.name}>{image.file?.name}</span>
            <span className={styles.wellSub}>{image.file?.size} · {image.file?.type}</span>
          </div>
        </div>
      ) : (
        <button type="button" className={styles.wellEmpty} onClick={onAdd}>
          <Icon name="imagePlus" size={22} />
          <span>이미지를<br />업로드해주세요</span>
        </button>
      )}
    </div>
  );
}

export default UploadWell;
