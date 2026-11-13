import React, { useMemo } from 'react';
import { pageTemplatesCatalog } from '../../lib/pageTemplates/catalog.js';
import { addTemplateSection } from '../../lib/pageTemplates/sectionActions.js';
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
import { StoryboardDesignIllustration } from './StoryboardDesignIllustration.jsx';
import styles from './StoryboardPanels.module.css';
import designStyles from './StoryboardDesignPanel.module.css';

const menuEntries = Object.values(pageTemplatesCatalog.kinds).filter(kind => kind.kind !== 'first' && kind.kind !== 'sns' && !isHiddenStoryboardKind(kind.kind))
  .flatMap(kind => kind.kind === 'wear' ? kind.usages.map(usage => ({ kind: kind.kind, usage })) : [{ kind: kind.kind }]);

function preview(board, run, catalogs) {
  if (!run) return [];
  const sample = sectionDesignPreviewSample(board, run.key);
  return assembleStoryboardPreview({ storyboard: sample, product: catalogs.detailProduct, analysis: catalogs.analysis,
    catalogs }).filter(block => block.pageSectionKey === run.key);
}
function LayoutGlyph({ kind, layout }) {
  const color = kind === 'color_product';
  const cells = layout === 'heroPair' ? [[5, 3, 34, 27], [5, 33, 16, 17], [23, 33, 16, 17]]
    : layout === 'twoColumn' ? [[4, 3, 17, 14], [23, 3, 17, 14], [4, 20, 17, 14], [23, 20, 17, 14], [4, 37, 17, 14], [23, 37, 17, 14]]
    : layout === 'grid' ? [[4, 7, 17, 28], [23, 7, 17, 28]]
    : color ? [[11, 2, 22, 18], [11, 28, 22, 18]] : [[13, 3, 18, 14], [13, 20, 18, 14], [13, 37, 18, 14]];
  return <svg viewBox="0 0 44 52" aria-hidden="true" data-layout-glyph={`${kind}:${layout}`}>
    {cells.map(([x, y, width, height], index) => <rect key={index} x={x} y={y} width={width} height={height} />)}
    {color && (layout === 'grid' ? [7, 26] : [15]).flatMap(x => (layout === 'grid' ? [40] : [24, 50]).map(y =>
      <g key={`${x}:${y}`}><circle cx={x} cy={y} r="1.4" /><rect x={x + 3} y={y - 1} width="8" height="2" /></g>))}
  </svg>;
}

function chooseWithArrow(event, options, index, choose) {
  const delta = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
  if (!delta || options.length < 2) return;
  event.preventDefault();
  const next = (index + delta + options.length) % options.length;
  event.currentTarget.parentElement?.querySelectorAll('button')[next]?.focus({ preventScroll: true });
  choose(options[next].designId);
}

export function StoryboardSectionPicker({ mode, runKey, blocks, catalogs, gender, identityKind, busy,
  onChoose, onDragChange, undoEntry, undoExiting, onUndo, onUndoEnter, onUndoLeave }) {
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
  const currentDesignId = currentSectionDesignId(existing, blocks);
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
  const choose = designId => { if (!busy) onChoose(existing.kind, existing.usage, designId); };
  const thumbnails = axes.hasAxes ? axes.decorations : designs.map(design => ({ id: design.id, name: design.displayName, designId: design.id, selected: design.id === currentDesignId }));
  const thumbnailStep = '디자인 선택';
  return <div className={designStyles.panel}>
    <div className={designStyles.controls}>
      {axes.layouts.length > 1 && <section className={designStyles.step}>
        <div className={designStyles.stepHeading}><h3>배치</h3><span>사진을 놓는 모양</span></div>
        <div className={designStyles.layouts} data-count={axes.layouts.length} role="group" aria-label="배치">
          {axes.layouts.map((option, index) => <button type="button" key={option.id} className={designStyles.layout}
            aria-pressed={option.selected} aria-label={`배치 · ${option.name}`} title={option.name} disabled={busy} onClick={() => choose(option.designId)}
            onKeyDown={event => { if (!busy) chooseWithArrow(event, axes.layouts, index, choose); }}>
            <LayoutGlyph kind={existing.kind} layout={option.id} />
          </button>)}
        </div>
      </section>}
      {thumbnails.length > 1 && <section className={designStyles.step}>
        <div className={designStyles.stepHeading}><h3>{thumbnailStep}</h3><span>{axes.hasAxes ? '고른 배치에 입혀요' : '글과 사진을 놓는 방식'}</span></div>
        <div className={designStyles.thumbnails} role="group" aria-label={thumbnailStep}>
          {thumbnails.map((option, index) => <button type="button" key={option.id} className={designStyles.thumbnail}
            data-design-thumbnail={option.designId} aria-label={`${thumbnailStep} · ${option.name}`} title={option.name} aria-pressed={option.selected}
            disabled={busy} onClick={() => choose(option.designId)}
            onKeyDown={event => { if (!busy) chooseWithArrow(event, thumbnails, index, choose); }}>
            <StoryboardDesignIllustration kind={existing.kind} designId={option.designId} decoration={axes.hasAxes ? option.id : undefined} />
          </button>)}
        </div>
      </section>}
    </div>
    <div className={designStyles.undoSpace}>
      {undoEntry && !undoExiting && <div className={designStyles.undo} role="status" aria-live="polite" title={undoEntry.message}
        onMouseEnter={onUndoEnter} onMouseLeave={onUndoLeave} onFocus={onUndoEnter} onBlur={onUndoLeave}>
        <span>{undoEntry.operationCount}가지 바꿨어요</span><span aria-hidden="true">·</span>
        <button type="button" disabled={busy} onClick={onUndo}>되돌리기</button>
      </div>}
    </div>
  </div>;
}
