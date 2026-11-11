import React, { useMemo } from 'react';
import { Icon } from '@/components/ui.jsx';
import { pageTemplatesCatalog } from '../../lib/pageTemplates/catalog.js';
import { addTemplateSection, changeTemplateSectionDesign } from '../../lib/pageTemplates/sectionActions.js';
import { sectionRuns, isTextOnlySection } from '../../lib/pageTemplates/sectionRuns.js';
import { automaticSectionInsertionKey } from '../../lib/pageTemplates/sectionPlacement.js';
import { isHiddenStoryboardKind } from '../../lib/pageTemplates/storyboardPositions.js';
import { sectionDesignChoices, defaultSectionDesignId, currentSectionDesignId } from '../../lib/pageTemplates/designChoices.js';
import { sectionDesignAxes } from '../../lib/pageTemplates/designAxes.js';
import { sectionDesignPreviewSample } from '../../lib/pageTemplates/designPreview.js';
import { templateSectionName } from '../../lib/pageTemplates/slots.js';
import { baseColorOf, resolvePageTokens } from '../../lib/pageTemplates/tokens.js';
import { StoryboardPreview } from './StoryboardPreview.jsx';
import { assembleStoryboardPreview } from './storyboardPreview.js';
import { NEW_SECTION_DRAG_MIME } from './StoryboardPageList.jsx';
import styles from './StoryboardPanels.module.css';

const menuEntries = Object.values(pageTemplatesCatalog.kinds).filter(kind => kind.kind !== 'first' && kind.kind !== 'sns' && !isHiddenStoryboardKind(kind.kind))
  .flatMap(kind => kind.kind === 'wear' ? kind.usages.map(usage => ({ kind: kind.kind, usage })) : [{ kind: kind.kind }]);

function preview(board, run, catalogs, designId) {
  if (!run) return [];
  const changed = designId ? changeTemplateSectionDesign(board, run.key, designId, { product: catalogs.detailProduct }) : board;
  const sample = sectionDesignPreviewSample(changed, run.key);
  return assembleStoryboardPreview({ storyboard: sample, product: catalogs.detailProduct, analysis: catalogs.analysis,
    catalogs }).filter(block => block.pageSectionKey === run.key);
}
export function StoryboardSectionPicker({ mode, runKey, blocks, catalogs, gender, identityKind, busy,
  onChoose, onDragChange, undoEntry, onUndo, onRemove }) {
  const runs = sectionRuns(blocks), existing = runs.find(run => run.key === runKey);
  const pageTokens = resolvePageTokens(catalogs.analysis?.pageStyle, baseColorOf(catalogs.detailProduct).swatchId);
  const toneColors = pageTokens.colorMode === 'point' ? pageTokens.colors : null;
  const entries = useMemo(() => mode !== 'add' ? [] : menuEntries.flatMap(entry => {
    if (entry.kind !== 'wear' && runs.some(run => run.kind === entry.kind)) return [];
    const board = addTemplateSection(blocks, catalogs.detailProduct, catalogs.analysis || {}, entry.kind, entry.usage,
      { gender, identityKind, catalog: catalogs.genExamples, beforeSectionKey: automaticSectionInsertionKey(blocks, entry.kind, entry.usage) });
    const run = sectionRuns(board).find(item => !runs.some(original => original.key === item.key));
    return run && !isTextOnlySection(run) ? [{ ...entry, designId: defaultSectionDesignId(run, board), preview: preview(board, run, catalogs) }] : [];
  }), [mode, blocks, catalogs, gender, identityKind]);
  const designs = sectionDesignChoices(existing, { storyboard: blocks, product: catalogs.detailProduct });
  const axes = sectionDesignAxes(existing, { storyboard: blocks, product: catalogs.detailProduct });
  const defaultDesignId = defaultSectionDesignId(existing, blocks), currentDesignId = currentSectionDesignId(existing, blocks);
  const designPreviews = useMemo(() => new Map(mode === 'design'
    ? designs.map(design => [design.id, preview(blocks, existing, catalogs, design.id)]) : []), [mode, blocks, runKey, catalogs]);
  const startDrag = (event, payload) => {
    if (busy) { event.preventDefault(); return; }
    event.dataTransfer.effectAllowed = 'copy'; event.dataTransfer.setData(NEW_SECTION_DRAG_MIME, JSON.stringify(payload)); onDragChange?.(true);
  };
  if (mode === 'add') return <div className={styles.sectionPicker}>
    <div className={styles.panelHeading}><h2>섹션 추가</h2></div>
    {undoEntry && <div className={styles.panelUndo} role="status"><span>{undoEntry.message}</span><span aria-hidden="true">·</span><button type="button" onClick={onUndo}>되돌리기</button></div>}
    <div className={styles.sectionMenu}>{entries.map(entry => {
      const definition = pageTemplatesCatalog.kinds[entry.kind], label = templateSectionName({ pageSectionKind: entry.kind, pageSectionUsage: entry.usage });
      return <button type="button" key={`${entry.kind}:${entry.usage || ''}`} className={styles.sectionOption} data-default-design={entry.designId}
        disabled={busy} draggable={!busy} onDragStart={event => startDrag(event, { kind: entry.kind, usage: entry.usage, designId: entry.designId })} onDragEnd={() => onDragChange?.(false)}
        onClick={event => onChoose(entry.kind, entry.usage, entry.designId, event.currentTarget.querySelector('[data-section-flight-source]'))}>
        <span className={styles.menuPreview} data-section-flight-source><StoryboardPreview blocks={entry.preview} fit wireframe toneColors={toneColors} label={`${label} 대표 레이아웃`} /></span>
        <span><strong>{label}</strong><small>{entry.kind === 'wear' ? entry.usage === 'fit'
          ? '단색 배경에서 앞, 옆, 뒤를 찍은 사진' : '카페, 골목, 집처럼 장소에서 찍은 사진'
          : definition.storyboardPhotoNote || definition.storyboardTextNote || definition.emptySellerNote || ''}</small></span>
        <span className={styles.roundPlus} aria-label="추가">＋</span>
      </button>;
    })}</div>
  </div>;
  if (!existing) return null;
  const name = templateSectionName(existing.blocks[0]);
  return <div className={styles.sectionPicker}>
    <div className={`${styles.panelHeading} ${styles.panelHeadingRow}`}><h2>{name} 디자인</h2>
      {existing.kind !== 'first' && <button type="button" className="btn" disabled={busy} onClick={() => onRemove?.(existing.key)}>섹션 빼기</button>}
    </div>
    {axes.hasAxes ? [
      { id: 'layout', label: '배치', options: axes.layouts },
      { id: 'decoration', label: '꾸밈', options: axes.decorations },
    ].filter(axis => axis.options.length > 1).map(axis => <section key={axis.id} className={styles.designAxis} role="group" aria-label={axis.label}>
      <h3>{axis.label}</h3>
      <div className={styles.designGrid}>{axis.options.map(option => <button type="button" key={option.id}
        className={`${styles.designCard}${option.selected ? ` ${styles.designSelected}` : ''}`}
        aria-pressed={option.selected} disabled={busy} onClick={() => onChoose(existing.kind, existing.usage, option.designId)}>
        {option.selected && <span className={styles.designCheck}><Icon name="check" size={12} stroke={1.5} /></span>}
        <span className={styles.designPreview}><StoryboardPreview blocks={designPreviews.get(option.designId)} fit wireframe toneColors={toneColors} label={`${name} · ${axis.label} · ${option.name}`} /></span>
        <span className={styles.designName}>{option.isDefault && option.name !== '기본' && <small>기본</small>}<strong>{option.name}</strong></span>
      </button>)}</div>
    </section>) : <div className={styles.designGrid}>{designs.map(design => <button type="button" key={design.id}
      className={`${styles.designCard}${currentDesignId === design.id ? ` ${styles.designSelected}` : ''}`}
      aria-pressed={currentDesignId === design.id} disabled={busy} onClick={() => onChoose(existing.kind, existing.usage, design.id)}>
      {currentDesignId === design.id && <span className={styles.designCheck}><Icon name="check" size={12} stroke={1.5} /></span>}
      <span className={styles.designPreview}><StoryboardPreview blocks={designPreviews.get(design.id)} fit wireframe toneColors={toneColors} label={`${name} · ${design.displayName}`} /></span>
      <span className={styles.designName}>{defaultDesignId === design.id && <small>기본</small>}<strong>{design.displayName}</strong></span>
    </button>)}</div>}
  </div>;
}
