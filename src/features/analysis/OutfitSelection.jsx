import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronUp, Sparkles } from 'lucide-react';
import { OUTFIT_SLOT_LABELS, complementaryOutfitSlot, outfitSlotsFor, setOutfitSelection, applyOutfitSet, toggleOutfitOptional } from '@/lib/outfitSelections.js';
import { filterOutfitItems, sortOutfitItems } from '@/lib/outfitPresentation.js';
import { OutfitBoard } from './OutfitBoard.jsx';
import { OutfitColorChips, OutfitMore, OutfitRoleIcon, OutfitTile, OutfitUploadTile, outfitImage, outfitName } from './OutfitCatalog.jsx';
import { OutfitDrawer } from './OutfitDrawer.jsx';
import { useOutfitRecommendation } from './useOutfitRecommendation.js';
import styles from './OutfitSelection.module.css';

export function OutfitSelection({ analysis, onChange, onRecommendation, projectId = null, product, models = [], modelCredit = () => '무료', onUpload, onDeleteCustom, deletingCustom, customUpload }) {
  const [open, setOpen] = useState(null);
  const [drawer, setDrawer] = useState(null);
  const [colors, setColors] = useState({});
  const [stuck, setStuck] = useState(false);
  const listRef = useRef(null);
  const rowRefs = useRef({});
  const moveFrame = useRef(0);
  const recommendationState = useOutfitRecommendation({ analysis, projectId, onRecommendation, onChange });
  const recommendation = recommendationState.recommendation || analysis.outfitRecommendation;
  const current = { ...analysis, outfitRecommendation: recommendation };
  const complementary = complementaryOutfitSlot(analysis.clothingType);
  const setCount = recommendation?.sets?.length || 0;
  useEffect(() => () => cancelAnimationFrame(moveFrame.current), []);
  useEffect(() => {
    let frame;
    const measure = () => setStuck((listRef.current?.getBoundingClientRect().top ?? 61) < 60);
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(measure); };
    measure(); globalThis.window?.addEventListener('scroll', schedule, { passive: true });
    return () => { globalThis.window?.removeEventListener('scroll', schedule); cancelAnimationFrame(frame); };
  }, []);
  useEffect(() => { setOpen(null); setDrawer(null); setColors({}); }, [analysis.clothingType]);
  const openRole = (role) => {
    setOpen((prior) => prior === role ? null : role);
    cancelAnimationFrame(moveFrame.current);
    moveFrame.current = requestAnimationFrame(() => {
      const row = rowRefs.current[role];
      if (!row || row.getAttribute('data-open') !== 'true') return;
      const top = row.getBoundingClientRect().top;
      if (top < 116 || top + row.offsetHeight > window.innerHeight - 80) {
        window.scrollTo({ top: window.scrollY + top - 116, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
      }
    });
  };
  const itemsFor = (role) => {
    if (role === 'model') return models;
    const items = role === complementary ? analysis.matchClothing || [] : recommendation?.candidates?.[role] || [];
    const inSets = new Set(recommendation?.sets?.flatMap((set) => [set.slots?.[role]]) || []);
    return items.map((item) => ({ ...item, isCompatible: item.isCompatible !== false && (role !== complementary || item.clothingType == null || item.clothingType === complementary), inSets: item.inSets || inSets.has(item.id) }));
  };
  const selectedFor = (role) => role === 'model' ? analysis.selectedModelId
    : analysis.outfitSelections?.slots?.[role]?.itemId ?? (role === complementary && !analysis.outfitSelections?.slots?.[role] ? analysis.matchClothing?.find((item) => item.selected)?.id : null);
  const select = (role, id) => {
    if (role === 'model') { onChange({ selectedModelId: id }); return; }
    recommendationState.markEdited();
    const patch = setOutfitSelection(current, role, id === selectedFor(role) ? null : id);
    if (patch) onChange(patch);
  };
  const roles = [...(models.length ? ['model'] : []), ...outfitSlotsFor(analysis.clothingType).filter((role) => recommendation?.roleStatus?.[role] !== 'unavailable' && (itemsFor(role).length > 0 || role === complementary))];
  const baseRoles = roles.filter((role) => !['bag', 'accessory'].includes(role));
  const optionalRoles = roles.filter((role) => ['bag', 'accessory'].includes(role));
  const labelFor = (role) => role === 'model' ? '모델' : OUTFIT_SLOT_LABELS[role];
  const isOptional = (role) => ['bag', 'accessory'].includes(role);
  const enabledFor = (role) => !isOptional(role) || analysis.outfitSelections?.optional?.[role] === true;
  const toggleOptional = (role) => {
    recommendationState.markEdited();
    const enabled = !enabledFor(role);
    onChange(toggleOutfitOptional(current, role, enabled));
    setOpen(enabled ? role : null);
  };
  const panel = (role) => {
    const items = itemsFor(role); const selectedId = selectedFor(role);
    const own = items.find((item) => item.isCustom);
    const upload = role === complementary;
    const filtered = filterOutfitItems(items.filter((item) => !item.isCustom), { role, color: colors[role] || '' });
    const shown = sortOutfitItems(filtered, { role, selectedId }).slice(0, isOptional(role) ? 3 : upload ? 5 : 6);
    return <div className={styles.panel} id={`outfit-panel-${role}`}>
      {role !== 'model' && !isOptional(role) && <OutfitColorChips role={role} items={items} value={colors[role]} onChange={(value) => setColors((prior) => ({ ...prior, [role]: value }))} />}
      <div className={styles.grid}>
        {shown.map((item) => <OutfitTile key={item.id} item={item} role={role} selected={selectedId === item.id} onSelect={(id) => select(role, id)} creditLabel={role === 'model' ? modelCredit(item) : undefined} />)}
        {upload && <div className={styles.ownSlot}>{own ? <OutfitTile item={own} role={role} selected={selectedId === own.id} onSelect={(id) => select(role, id)} busy={customUpload} onDelete={onDeleteCustom} deleting={deletingCustom} /> : <OutfitUploadTile onUpload={onUpload} busy={customUpload} />}</div>}
      </div>
      <div className={styles.panelFoot}><OutfitMore items={items.filter((item) => !item.isCustom)} role={role} onClick={() => setDrawer(role)} /></div>
    </div>;
  };
  const row = (role) => {
    const optional = isOptional(role); const enabled = enabledFor(role);
    const expanded = open === role && enabled; const item = itemsFor(role).find((it) => it.id === selectedFor(role));
    const busy = recommendationState.skeleton && role !== 'model';
    return <section key={role} ref={(node) => { rowRefs.current[role] = node; }} data-open={expanded}
      className={`${styles.row} ${expanded && !optional ? styles.openRow : ''} ${optional ? styles.optionalRow : ''} ${!enabled ? styles.off : ''} ${open && !expanded ? styles.dimmed : ''}`}
      aria-label={`${labelFor(role)} 선택`}>
      <div className={styles.rowHeading}>
        <button type="button" className={styles.rowButton} aria-expanded={expanded} aria-controls={`outfit-panel-${role}`} aria-disabled={!enabled}
          onClick={() => { if (enabled) openRole(role); }}>
          {(!expanded || optional) && <span className={`${styles.thumb} ${role === 'model' ? styles.modelPicture : ''} ${busy ? styles.skeleton : ''}`}>
            {!busy && (outfitImage(item) ? <img src={outfitImage(item)} alt="" width="96" height="120" /> : <OutfitRoleIcon role={role} />)}
          </span>}
          <span className={styles.rowText}>
            <span className={styles.role}>{labelFor(role)}</span>
            <span className={`${styles.value} ${busy ? styles.skeletonText : ''}`}>{!busy && (enabled ? outfitName(item) || '고르지 않았어요' : '넣지 않아요')}{role === 'model' && item && <small> · {modelCredit(item)}</small>}</span>
            {!optional && <span className={styles.change}>{expanded ? '접기' : '바꾸기'}{expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}</span>}
          </span>
        </button>
        {optional && <button type="button" className={styles.switch} role="switch" aria-label={`${labelFor(role)} 넣어요`} aria-checked={enabled} onClick={() => toggleOptional(role)}><span className={styles.switchTrack}><i /></span>넣어요</button>}
      </div>
      {expanded && !optional && panel(role)}
    </section>;
  };
  return <div className={`${styles.outfit} merged-card`}>
    <OutfitBoard analysis={current} product={product} projectId={projectId} onOpenRole={openRole} skeleton={recommendationState.skeleton} />
    <div className={styles.list} ref={listRef}>
      <div className={`${styles.listHead} ${stuck ? styles.stuckHead : ''}`}><h3>코디 구성</h3>
        {recommendationState.lateRecommendation ? <button type="button" className={styles.late} onClick={recommendationState.acceptLate}><Sparkles size={16} />AI 코디 보기</button>
          : setCount > 0 && <button type="button" className={styles.recommend} disabled={setCount < 2 || recommendationState.skeleton}
            onClick={() => { const patch = applyOutfitSet(current, ((analysis.outfitSelections?.setIndex || 0) + 1) % setCount); if (patch) { if (recommendation.status !== 'ready') delete patch.outfitSetIndex; onChange(patch); } }}>
            <Sparkles size={16} />AI 추천<span>{Math.min((analysis.outfitSelections?.setIndex || 0) + 1, setCount)} / {setCount}</span>
          </button>}
      </div>
      {recommendationState.skeleton && <span className="sr-only" role="status">코디를 준비하고 있어요</span>}
      {baseRoles.map(row)}
      {optionalRoles.length > 0 && <><h4 className={styles.optionalTitle}>소품<small>· 넣거나 뺄 수 있어요</small></h4><div className={styles.optionalRows}>{optionalRoles.map(row)}</div>
        {optionalRoles.includes(open) && enabledFor(open) && <section className={styles.optionalPanel} ref={(node) => { rowRefs.current[open] = node; }} data-open="true" aria-label={`${labelFor(open)} 고르기`}>{panel(open)}</section>}
      </>}
    </div>
    {drawer && <OutfitDrawer key={drawer} role={drawer} label={labelFor(drawer)} items={itemsFor(drawer)} selectedId={selectedFor(drawer)} onSelect={(id) => select(drawer, id)}
      onClose={() => setDrawer(null)} modelCredit={modelCredit} onUpload={drawer === complementary && onUpload ? (event) => { setDrawer(null); onUpload(event); } : undefined} />}
  </div>;
}
