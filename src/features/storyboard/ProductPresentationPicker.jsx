import React, { useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import manifest from '../../data/productPresentationExamples.json';
import { PRODUCT_PRESENTATION_OPTIONS, productPresentationOf } from '../../lib/storyboardTaxonomy.js';
import { productPresentationExample } from '../../lib/productPresentationExamples.js';
import { productSectionState } from '../../lib/pageTemplates/productPresentation.js';
import { productSourcePhotos } from '../../lib/productSourcePhotos.js';
import styles from './StoryboardChoices.module.css';

function PresentationCard({ mode, clothingType, direction, selected, busy, onChoose, images }) {
  const [failed, setFailed] = useState([]);
  const available = Object.fromEntries(Object.entries(images).filter(([, src]) => !failed.includes(src)));
  const example = productPresentationExample(clothingType, mode.value, direction, available);
  return <button type="button" className={styles.presentationCard} disabled={busy} aria-pressed={selected} onClick={onChoose}>
    <span className={`${styles.presentationImage}${selected ? ` ${styles.chosen}` : ''}`}>
      {example ? <img src={example.src} alt={`${mode.label} ${example.face === 'back' ? '뒷면' : '앞면'} 촬영 방식 예시`}
        onError={() => setFailed(previous => [...previous, example.src])} /> : <span>{mode.label}</span>}
      {selected && <span className={styles.check}><Icon name="check" size={12} /></span>}
    </span><strong>{mode.label}</strong>
    {example?.fallback && <small>앞면 그림, 촬영 방식 참고</small>}
  </button>;
}

export function ProductPresentationPicker({ block, blocks = [block], product, fixedProduct = false, clothingType, onChange, busy = false, images = manifest.images }) {
  const [error, setError] = useState(''), [saving, setSaving] = useState(false), [attempt, setAttempt] = useState(null);
  const state = productSectionState(blocks, block.pageSectionKey);
  const value = state.count ? state.value : productPresentationOf(block);
  const choose = async mode => {
    if (fixedProduct || busy || saving || value === mode && !error) return;
    setSaving(true); setError(''); setAttempt(mode);
    try { await onChange({ productPresentation: mode }, { retryAtomic: true, pickerOwnsError: true, undoLabel: '촬영 방식' }); }
    catch { setError('촬영 방식을 저장하지 못했어요.'); }
    finally { setSaving(false); }
  };
  if (fixedProduct) {
    const baseColor = product?.colors?.find(color => color.isBase) || product?.colors?.[0];
    const source = productSourcePhotos(product, baseColor?.id).front;
    return <div className={styles.productPicker}>
      <h2>고스트컷</h2>
      <p className={styles.fixedProductNote}>기준 색상 · 내 앞면 사진</p>
      {source ? <img className={styles.fixedProductSource} src={source.src} alt={`${baseColor?.name || '기준 색상'} 내 앞면 사진`} />
        : <p className={styles.empty}>기준 색상의 앞면 사진이 필요해요.</p>}
    </div>;
  }
  return <div className={styles.productPicker}>
    <h2>촬영 방식</h2>{!value && <p className={styles.empty}>촬영 방식을 골라 주세요.</p>}
    <div className={styles.presentationGrid} role="group" aria-label="섹션 촬영 방식">
      {PRODUCT_PRESENTATION_OPTIONS.map(mode => <PresentationCard key={`${mode.value}:${clothingType}:${block.direction}`}
        mode={mode} clothingType={clothingType} direction={block.direction} selected={value === mode.value}
        busy={busy || saving} onChoose={() => choose(mode.value)} images={images} />)}
    </div>
    {error && <div className={styles.saveError} role="alert"><p>{error}</p><div className="modal-actions">
      <button type="button" className="btn" disabled={busy || saving} onClick={() => choose(attempt)}>다시 시도</button>
    </div></div>}
  </div>;
}
