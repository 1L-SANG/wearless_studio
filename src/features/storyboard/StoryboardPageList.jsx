import React, { useState, useEffect } from 'react';
import { Icon } from '@/components/ui.jsx';
import { StoryboardPreview } from './StoryboardPreview.jsx';
import { templateSectionName } from '../../lib/pageTemplates/slots.js';
import { CATALOG_DRAG_MIME } from '../../lib/storyboardCatalogDrag.js';
import { attachStoryboardDragScroll } from '../../lib/storyboardDragScroll.js';
import { storyboardPositions } from '../../lib/pageTemplates/storyboardPositions.js';
import styles from './StoryboardLayout.module.css';

export const SECTION_DRAG_MIME = 'application/x-wearless-page-section';
export const NEW_SECTION_DRAG_MIME = 'application/x-wearless-new-section';
function MoveDots() {
  return <svg viewBox="0 0 10 14" width="10" height="14" fill="currentColor" aria-hidden="true">
    {[2, 7, 12].map(cy => <g key={cy}><circle cx="2" cy={cy} r="1.3" /><circle cx="7" cy={cy} r="1.3" /></g>)}
  </svg>;
}
export function StoryboardPageList({ runs, page, activeKey, busy, draggingExample, highlightedPosition, selectedSourceId, selectedPositionId, insertedKey,
  onSelect, onAdd, onRemove, onMove, onDropContent, onOverview, onPositionSelect, sectionRefs }) {
  const [dragKey, setDragKey] = useState(null), [over, setOver] = useState(undefined);
  const dragging = Boolean(dragKey || draggingExample);
  useEffect(() => { if (dragging) return attachStoryboardDragScroll(); }, [dragging]);
  const move = (index, delta, action) => {
    if (busy || delta < 0 && index <= 1 || delta > 0 && index === runs.length - 1) return;
    onMove(runs[index].key, delta < 0 ? runs[index - 1].key : runs[index + 2]?.key || null, action);
  };
  const dragStart = (event, run) => {
    if (busy || run.kind === 'first') { event.preventDefault(); return; }
    event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData(SECTION_DRAG_MIME, run.key); setDragKey(run.key);
  };
  const finishDrag = () => { setDragKey(null); setOver(undefined); };
  const accept = event => [...event.dataTransfer.types].some(type => [SECTION_DRAG_MIME, NEW_SECTION_DRAG_MIME, CATALOG_DRAG_MIME, 'text/example-id'].includes(type));
  const insertion = (beforeKey, index) => <div key={`drop:${index}`} aria-hidden="true" data-section-drop={beforeKey || 'end'}
    className={`${styles.insert}${over === index ? ` ${styles.dropOn}` : ''}`}
    onDragEnter={event => { if (accept(event)) { event.preventDefault(); setOver(index); } }}
    onDragOver={event => { if (accept(event)) { event.preventDefault(); event.dataTransfer.dropEffect = dragKey ? 'move' : 'copy'; setOver(index); } }}
    onDragLeave={() => setOver(undefined)} onDrop={event => {
      event.preventDefault(); const key = event.dataTransfer.getData(SECTION_DRAG_MIME); finishDrag();
      if (key) onMove(key, beforeKey, 'handle'); else onDropContent(event.dataTransfer, beforeKey);
    }} />;
  return <nav className={`${styles.outline}${dragging ? ` ${styles.dragging}` : ''}`} aria-label="페이지 구성">
    <div className={styles.outlineHead}><span>페이지 구성</span><button type="button" className={styles.outlineAdd} disabled={busy} onClick={() => onAdd(null)}>＋ 섹션 추가</button></div>
    <div className={styles.outlineScroll} data-storyboard-scroll>
      {runs.map((run, index) => {
        const sectionPage = page.filter(block => block.pageSectionKey === run.key), positions = storyboardPositions(sectionPage, run.key);
        const count = run.blocks.filter(block => block.source !== 'text').length || positions.length;
        const select = () => onSelect(run.key);
        return <React.Fragment key={run.key}>
          <div className={`${styles.sectionRow}${activeKey === run.key ? ` ${styles.selectedSection}` : ''}${dragKey === run.key ? ` ${styles.moving}` : ''}${insertedKey === run.key ? ` ${styles.insertedSection}` : ''}`}
            data-section-key={run.key} ref={node => { if (node) sectionRefs.current.set(run.key, node); else sectionRefs.current.delete(run.key); }}>
            <button type="button" className={styles.sectionTitle} draggable={run.kind !== 'first' && !busy} disabled={busy}
              onDragStart={event => dragStart(event, run)} onDragEnd={finishDrag} onClick={select}>
              <em>Section {index + 1}</em><strong>{templateSectionName(run.blocks[0])}</strong><small>{count}컷</small>
            </button>
            <div className={styles.thumbnailWrap}>
              <div role="button" tabIndex={busy ? -1 : 0} className={styles.sectionSelect} aria-current={activeKey === run.key ? 'true' : undefined}
                aria-label={`${templateSectionName(run.blocks[0])} 열기`} aria-disabled={busy || undefined} onClick={busy ? undefined : select}
                onKeyDown={event => { if (!busy && ['Enter', ' '].includes(event.key)) { event.preventDefault(); select(); } }}>
                <StoryboardPreview blocks={sectionPage} label={`${templateSectionName(run.blocks[0])} 실제 모습`}
                  highlightedPosition={activeKey === run.key ? highlightedPosition : null} selectedSourceId={activeKey === run.key ? selectedSourceId : null}
                  selectedPositionId={activeKey === run.key ? selectedPositionId : null}
                  onPositionClick={busy ? undefined : position => activeKey === run.key ? onPositionSelect?.(run.key, position) : select()} />
              </div>
              {run.kind !== 'first' && <div className={styles.sectionTools} aria-label="섹션 순서와 삭제">
                <button type="button" className={styles.handle} draggable={!busy} disabled={busy} data-move-action="handle" aria-label={`Section ${index + 1} 순서 바꾸기`}
                  onKeyDown={event => { if (['ArrowUp', 'ArrowDown'].includes(event.key)) { event.preventDefault(); move(index, event.key === 'ArrowUp' ? -1 : 1, 'handle'); } }}
                  onDragStart={event => dragStart(event, run)} onDragEnd={finishDrag}><MoveDots /></button>
                <button type="button" data-move-action="up" aria-label={`Section ${index + 1} 위로`} aria-disabled={busy || index <= 1} onClick={() => move(index, -1, 'up')}><Icon name="arrowUp" size={14} /></button>
                <button type="button" data-move-action="down" aria-label={`Section ${index + 1} 아래로`} aria-disabled={busy || index === runs.length - 1} onClick={() => move(index, 1, 'down')}><Icon name="arrowUp" size={14} className={styles.downArrow} /></button>
                <button type="button" className={styles.removeSection} disabled={busy} aria-label={`${templateSectionName(run.blocks[0])} 섹션 빼기`} onClick={() => onRemove(run.key)}><Icon name="trash" size={14} /></button>
              </div>}
            </div>
          </div>{insertion(runs[index + 1]?.key || null, index + 1)}
        </React.Fragment>;
      })}
      <button type="button" className={`${styles.back} ${styles.outlineOverview}`} onClick={onOverview}>전체 보기</button>
    </div>
  </nav>;
}
