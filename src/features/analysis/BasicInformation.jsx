import { Fragment, useEffect, useId, useRef, useState } from 'react';
import { analysisFitInput, fitNote, fitZones, fitZoneState, resetFitZone } from '@/lib/fitZone.js';

const INFO = ['type', 'sub', 'gender'];
const LABELS = { type: '의류 종류', sub: '세부 카테고리', gender: '대상 성별' };
// 기존 직접 입력 칩의 내용에 따른 폭 계산을 유지한다.
const chWidth = (s) => [...s].reduce((n, ch) => n + (/[가-힣]/.test(ch) ? 1 : 0.55), 0).toFixed(1);

const cardLabel = (label) => {
  const [name, suffix] = label.split(' (');
  return suffix ? <>{name}{' '}<span className="g-card-nowrap">({suffix}</span></> : label;
};

export function BasicInformation({ analysis: a, catalogs, subCats, onTypeChange, onGenderChange,
  onCategoryChange, onAxisChange, onAxisReset }) {
  const [open, setOpen] = useState(null);
  const [just, setJust] = useState(null);
  const [pointer, setPointer] = useState(false);
  const byPointer = useRef(false);
  const tiles = useRef({});
  const id = useId();
  const [ccDraft, setCcDraft] = useState(a.customCategory || '');
  const [ccFocus, setCcFocus] = useState(false);
  useEffect(() => { setCcDraft(a.customCategory || ''); }, [a.customCategory]);
  const input = analysisFitInput(a);
  const zones = fitZones(input);
  const wear = zones.map(({ axis }) => axis);
  const active = [...INFO, ...wear].includes(open) ? open : null;
  const close = (key) => {
    setOpen(null);
    setJust(key);
    if (!byPointer.current) tiles.current[key]?.focus({ preventScroll: true });
  };
  useEffect(() => {
    if (open && ![...INFO, ...wear].includes(open)) setOpen(null);
  }, [open, wear.join(',')]);
  useEffect(() => {
    const keydown = (event) => {
      byPointer.current = false;
      setPointer(false);
      if (event.key !== 'Escape' || !active || event.isComposing || event.defaultPrevented) return;
      if (event.target.closest?.('.surface')?.querySelector('.sp-chipwrap')) return;
      close(active);
    };
    document.addEventListener('keydown', keydown);
    return () => document.removeEventListener('keydown', keydown);
  }, [active]);
  const select = (key, value) => {
    if (key === 'type') {
      if (value === a.clothingType) return close(key);
      onTypeChange(value);
      setOpen('sub');
      setJust('type');
      if (!byPointer.current) tiles.current.sub?.focus({ preventScroll: true });
      return;
    }
    if (key === 'sub') onCategoryChange({ subCategory: value, customCategory: null });
    else if (key === 'gender') onGenderChange(value);
    else onAxisChange(key, value);
    close(key);
  };
  const genderOptions = a.clothingType === 'dress'
    ? catalogs.genders.filter(({ value }) => value === 'women') : catalogs.genders;
  const infoOptions = { type: catalogs.clothingTypes, sub: subCats, gender: genderOptions };
  const infoValues = { type: a.clothingType, sub: a.subCategory, gender: a.targetGenders?.[0] };
  const stateFor = (key) => {
    if (!INFO.includes(key)) return fitZoneState(zones.find(({ axis }) => axis === key), a.fitProfile);
    const label = key === 'sub' && !a.subCategory ? a.customCategory
      : infoOptions[key].find(({ value }) => value === infoValues[key])?.label;
    return { label: label || '선택해 주세요', photoOnly: !label };
  };
  const chip = (key, option, value) => <button type="button" role="radio" key={option.value}
    className={`chip${option.value === value ? ' on' : ''}`} aria-checked={option.value === value}
    onClick={() => select(key, option.value)}>{option.label}</button>;
  const customCategory = <span className="chip-input-wrap">
    <input className={`chip chip-input${ccFocus || (a.customCategory && !a.subCategory) ? ' on' : ''}`}
      value={ccDraft} maxLength={20} placeholder="직접 입력" aria-label="세부 카테고리 직접 입력"
      style={{ width: `calc(${chWidth(ccDraft || '직접 입력')}em + 32px)` }}
      onChange={(event) => setCcDraft(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
          event.currentTarget.blur();
          close('sub');
        }
      }}
      onFocus={() => setCcFocus(true)}
      onBlur={() => {
        setCcFocus(false);
        const value = ccDraft.trim();
        if (value !== (a.customCategory || '')) {
          onCategoryChange(value ? { customCategory: value, subCategory: null } : { customCategory: null });
        }
      }} />
    {ccDraft.trim() !== (a.customCategory || '') && <span className="chip-input-pending">적용 전</span>}
  </span>;
  const panel = (key) => {
    if (INFO.includes(key)) return <div className="chips" role="radiogroup" aria-label={LABELS[key]}>
      {infoOptions[key].map((option) => chip(key, option, key === 'sub' && ccFocus ? null : infoValues[key]))}
      {key === 'sub' && customCategory}
    </div>;
    const zone = zones.find(({ axis }) => axis === key);
    const state = fitZoneState(zone, a.fitProfile);
    if (key === 'fit') return <>
      <div className="chips" role="radiogroup" aria-label={zone.label}>
        {zone.options.map((option) => chip(key, option, state.value))}
      </div>
      <p className="g-note">{fitNote(state.value, input.gender)}</p>
    </>;
    const groups = zone.groups || [{ options: zone.options }];
    const reset = resetFitZone(key, input);
    const resetState = fitZoneState(zone, { ...a.fitProfile, ...reset });
    return <>
      {groups.map((group) => <Fragment key={group.type || key}>
        {groups.length > 1 && <div className="g-group-label">{group.label}</div>}
        <div className={`g-cards ${key === 'sleeve' ? (group.type === 'long' ? 'a-tall' : 'a-sq')
          : zone.category === 'top' ? 'a-len' : zone.category === 'pants' ? 'a-sq' : 'a-skirt'}`}
          data-count={group.options.length} role="radiogroup"
          aria-label={`${zone.label}${groups.length > 1 ? ` ${group.label}` : ''}`}>
          {group.options.map((option) => <button type="button" role="radio" key={option.value}
            className={`g-card${state.value === option.value ? ' on' : ''}`} aria-checked={state.value === option.value}
            onClick={() => select(key, option.value)}>
            <img src={`/fit-cards/${option.card}`} alt="" loading="lazy" />
            <span className="fl">{cardLabel(option.label)}</span>
          </button>)}
        </div>
      </Fragment>)}
      {state.source === 'seller' && <button type="button" className="g-revert"
        onClick={() => { onAxisReset(reset); close(key); }}>{resetState.photoOnly ? '사진대로 두기' : '처음 값으로'}</button>}
    </>;
  };
  return <div className="surface g-surface">
    <div className="sec-title g-title">기본 정보</div>
    <div className="g2" data-pointer={pointer} onPointerDownCapture={() => {
      byPointer.current = true;
      setPointer(true);
    }} onKeyDownCapture={() => { byPointer.current = false; setPointer(false); }}>
      {[INFO, wear].filter((group) => group.length).map((group, index) => <Fragment key={index}>
        <div className="g2-row">
          {group.map((key) => {
            const state = stateFor(key);
            const card = key === 'fit' && input.gender === 'men' ? null : state.card;
            const label = LABELS[key] || zones.find(({ axis }) => axis === key).label;
            const isOpen = active === key;
            return <button type="button" key={key} ref={(node) => { tiles.current[key] = node; }}
              className={`t2${isOpen ? ' open' : ''}${state.photoOnly ? ' soft' : ''}${just === key ? ' just' : ''}`}
              aria-expanded={isOpen} aria-controls={isOpen ? `${id}-panel-${key}` : undefined}
              aria-label={`${label} ${state.label}${state.showMeasurement ? ' 실측' : ''}, ${isOpen ? '닫기' : '변경'}`}
              onClick={() => { setJust(null); setOpen(isOpen ? null : key); }}>
              {card && <img className="t2-thumb" src={`/fit-cards/${card}`} alt="" loading="lazy" />}
              <span className="tx"><span className="k">{label}</span><span className="vr">
                <span className="v">{state.label}</span>{state.showMeasurement && <span className="g-tag">실측</span>}
              </span></span>
              <span className="t2-pill" aria-hidden="true">{isOpen ? '닫기' : '변경'}</span>
            </button>;
          })}
        </div>
        {group.includes(active) && <div className="g-reveal" key={active}>
          <div className="g-reveal-in"><div className="g-panel" id={`${id}-panel-${active}`} data-col={group.indexOf(active)}>
            {panel(active)}
          </div></div>
        </div>}
      </Fragment>)}
    </div>
  </div>;
}
