import styles from './EditorDownloadModal.module.css';

export function RadioCard({ name, option, checked, disabled, onChange }) {
  return (
    <label className={`${styles.option}${checked ? ` ${styles.selected}` : ''}${disabled ? ` ${styles.disabled}` : ''}`}>
      <input type="radio" name={name} value={option.id} checked={checked} disabled={disabled}
        onChange={() => onChange(option.id)} />
      <span className={styles.optionText}>
        <span className={styles.optionTitle}>{option.title}</span>
        {option.width && <span className={styles.optionSize}>{option.width.toLocaleString('ko-KR')}px</span>}
        {option.desc && <span className={styles.optionDesc}>{option.desc}</span>}
      </span>
    </label>
  );
}
