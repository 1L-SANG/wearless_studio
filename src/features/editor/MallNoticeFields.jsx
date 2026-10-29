import { useState } from 'react';
import { Button } from '@/components/ui.jsx';
import styles from './MallRegisterModal.module.css';

export function MallNoticeFields({ resolution, onSave, busy, error }) {
  const [fields, setFields] = useState(() => resolution.fields);
  const [chosen, setChosen] = useState('');
  return <section className={styles.zone} aria-labelledby="mall-notice-title">
    <h3 id="mall-notice-title">고시 10항목</h3>
    <p className={styles.notice}>에디터 고시 블록과 판매 기본 정보에서 채웠어요. 빈칸만 넣어 주세요.</p>
    {resolution.conflicts.length > 0 && <label className={styles.row}><span>서로 다른 고시</span>
      <select disabled={busy} value={chosen} onChange={(event) => {
        const id = event.target.value; setChosen(id);
        const selected = resolution.blocks.find((block) => block.id === id);
        if (selected) setFields(resolution.fields.map((field) => ({ ...field, value: selected.fields.find((f) => f.key === field.key)?.value || '', source: '선택한 블록' })));
      }}><option value="">사용할 블록을 골라 주세요</option>{resolution.blocks.map((block, index) => <option key={block.id} value={block.id}>{block.name || '고시'} {index + 1}</option>)}</select>
    </label>}
    {fields.map((field) => <label className={styles.row} key={field.key}>
      <span>{field.label}</span><span><input data-field={`notice.${field.key}`} value={field.value} disabled={busy}
        aria-invalid={resolution.issues?.some((issue) => issue.path === `notice.${field.key}`) || undefined} aria-describedby={`mall-notice-source-${field.key}`} onChange={(event) => setFields((current) => current.map((item) => item.key === field.key ? { ...item, value: event.target.value, source: '직접 입력' } : item))} />
        <small id={`mall-notice-source-${field.key}`} className={styles.notice}>{resolution.issues?.find((issue) => issue.path === `notice.${field.key}`)?.message || field.source}</small></span>
    </label>)}
    {resolution.nameMismatch && <p className={styles.notice}>고시 품명과 등록 상품명이 달라요. 내용을 확인해 주세요.</p>}
    <p className={styles.notice}>여기서 채운 값은 에디터 고시 블록에도 같이 들어가요.</p>
    {error && <p className={styles.error} role="alert">{error}</p>}
    <div className="modal-actions"><Button disabled={busy || resolution.conflicts.length > 0 && !chosen} onClick={() => onSave({ fields })}>{busy ? '저장 중이에요' : '고시 저장'}</Button></div>
  </section>;
}
