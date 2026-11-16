import React, { useEffect, useRef, useId } from 'react';
import { Icon } from '@/components/ui.jsx';
import { PHOTO_PLACE_TABS, framingLabel } from '../../lib/pageTemplates/storyboardPhotoChoices.js';
import styles from './StoryboardChoices.module.css';

const UNAVAILABLE = '이 조건은 아직 서비스에 공개되지 않았어요';
export function PhotoChoiceFilters({ tabs = PHOTO_PLACE_TABS, place, onPlace, disabled = false }) {
  return <div className={styles.places} role="group" aria-label="장소">
    {tabs.map(tab => <button type="button" key={tab.value} disabled={disabled}
      aria-pressed={place === tab.value} onClick={() => onPlace(tab.value)}>{tab.label}</button>)}
  </div>;
}

export function ChoiceReason({ children, reason, id }) {
  const tooltip = useRef(null);
  const show = () => { if (reason && !tooltip.current?.matches?.(':popover-open')) tooltip.current?.showPopover?.(); };
  const hide = () => { if (tooltip.current?.matches?.(':popover-open')) tooltip.current.hidePopover?.(); };
  return <span className={styles.reasonWrap} onMouseEnter={show} onMouseLeave={event => { if (!event.currentTarget.contains(event.currentTarget.ownerDocument?.activeElement)) hide(); }} onFocusCapture={show}
    onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget) && !event.currentTarget.matches(':hover')) hide(); }}>
    {children}{reason && <span className={styles.reason} ref={tooltip} id={id} role="tooltip" popover="manual">{reason}</span>}
  </span>;
}

export function PhotoSegment({ options, value, onChange, label, disabled }) {
  const instanceId = useId();
  return <div className={`modal-actions ${styles.segment}`} role="group" aria-label={label}>
    {options.map((option, index) => {
      const reason = option.disabled ? option.disabledReason || UNAVAILABLE : null;
      const id = `${instanceId}-${index}-reason`;
      return <ChoiceReason key={option.value} reason={reason} id={id}>
        <button type="button" aria-pressed={value === option.value} disabled={disabled}
          aria-disabled={option.disabled || undefined} aria-describedby={reason ? id : undefined}
          onClick={() => { if (!option.disabled) onChange(option.value); }}>
          {option.icon}<span data-label={option.label}>{option.label}</span>
        </button>
      </ChoiceReason>;
    })}
  </div>;
}

function FramingIcon({ shot, bottom }) {
  return <svg className={styles.framingIcon} viewBox="0 0 18 24" aria-hidden="true" fill="currentColor">
    <rect x="1.75" y="1.75" width="14.5" height="20.5" rx="2.6" fill="none" stroke="currentColor" strokeWidth="1.5" />
    {shot === 'full' ? <><circle cx="9" cy="6.9" r="1.7" /><path d="M6.3 10.9a1.6 1.6 0 0 1 1.6-1.6h2.2a1.6 1.6 0 0 1 1.6 1.6v3.9H6.3zM6.8 14.4h2V19a1 1 0 0 1-2 0zM9.2 14.4h2V19a1 1 0 0 1-2 0z" /></>
      : bottom ? <path d="M4.8 2.4h8.4v5.4H4.8zM4.8 7h3.8v10.6a1.9 1.9 0 0 1-3.8 0zM9.4 7h3.8v10.6a1.9 1.9 0 0 1-3.8 0z" />
        : <><circle cx="9" cy="9.2" r="2.9" /><path d="M3.6 21.6v-2.7a5.4 5.4 0 0 1 10.8 0v2.7z" /></>}
  </svg>;
}

export function PhotoFraming({ value, onChange, clothingType, disabled, options = [{ value: 'full' }, { value: 'medium' }] }) {
  return <PhotoSegment label="구도" value={value} onChange={onChange} disabled={disabled}
    options={options.map(option => ({ ...option, label: framingLabel(option.value, clothingType),
      icon: <FramingIcon shot={option.value} bottom={clothingType === 'bottom'} /> }))} />;
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
