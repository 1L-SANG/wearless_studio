import React, { useEffect, useRef, useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import { PHOTO_PLACE_TABS, PHOTO_LIGHTS, framingLabel } from '../../lib/pageTemplates/storyboardPhotoChoices.js';
import styles from './StoryboardChoices.module.css';

export function PhotoChoiceFilters({ tabs = PHOTO_PLACE_TABS, place, onPlace, light, onLight, studio = false, disabled = false }) {
  const [open, setOpen] = useState(false), root = useRef(null), trigger = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const outside = event => { if (!root.current?.contains(event.target)) setOpen(false); };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  return <div className={styles.filters}>
    {!studio && tabs.length > 0 && <div className={styles.places} role="group" aria-label="장소">
      {tabs.map(tab => <button type="button" key={tab.value} disabled={disabled}
        aria-pressed={place === tab.value} onClick={() => onPlace(tab.value)}>{tab.label}</button>)}
    </div>}
    <div className={styles.light} ref={root} onKeyDown={event => {
      if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); setOpen(false); trigger.current?.focus(); }
    }}>
      <button ref={trigger} type="button" className={`${styles.lightButton}${light !== 'all' ? ` ${styles.lightActive}` : ''}`}
        disabled={disabled} aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen(value => !value)}>
        빛: {PHOTO_LIGHTS.find(item => item.value === light)?.label || '전체'}<Icon name="chevDown" size={12} />
      </button>
      {open && <div className={styles.lightMenu} role="menu" aria-label="빛">
        {PHOTO_LIGHTS.map((option, index) => <button type="button" key={option.value} role="menuitemradio" aria-checked={light === option.value}
          autoFocus={index === Math.max(0, PHOTO_LIGHTS.findIndex(item => item.value === light))}
          onClick={() => { onLight(option.value); setOpen(false); trigger.current?.focus(); }}
          onKeyDown={event => {
            if (event.key === 'Tab') { setOpen(false); return; }
            if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault(); const buttons = event.currentTarget.parentElement.querySelectorAll('button');
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
              : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
            buttons[next].focus();
          }}>{option.label}{light === option.value && <Icon name="check" size={12} />}</button>)}
      </div>}
    </div>
  </div>;
}

export function PhotoFraming({ value, onChange, clothingType, single, disabled }) {
  return <div className={`modal-actions ${styles.framing}`} role="group" aria-label="구도">
    {(single ? ['full', 'medium'] : ['all', 'full', 'medium']).map(shot => <button type="button" key={shot}
      disabled={disabled} aria-pressed={value === shot} onClick={() => onChange(shot)}>
      {shot === 'all' ? '모든 구도' : framingLabel(shot, clothingType)}
    </button>)}
  </div>;
}

export function PhotoChoicePager({ page, count, onChange }) {
  const dots = useRef(null);
  useEffect(() => { dots.current?.querySelector('[aria-current="page"]')?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' }); }, [page]);
  if (count <= 1) return null;
  return <div className={`sb-excontrols ${styles.pager}`}>
    <button type="button" className="sb-expage-hit prev" aria-label="이전 예시 페이지" disabled={page === 0} onClick={() => onChange(page - 1)}><Icon name="chevLeft" size={16} /></button>
    <div className="sb-expages" ref={dots} aria-label={`총 ${count}페이지 중 ${page + 1}페이지`}>
      {Array.from({ length: count }, (_, index) => <button type="button" key={index} className={page === index ? 'on' : ''}
        aria-label={`${index + 1}페이지`} aria-current={page === index ? 'page' : undefined} onClick={() => onChange(index)} />)}
    </div>
    <button type="button" className="sb-expage-hit next" aria-label="다음 예시 페이지" disabled={page === count - 1} onClick={() => onChange(page + 1)}><Icon name="chevRight" size={16} /></button>
  </div>;
}
