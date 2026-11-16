import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { storyboardSpaceSetsFor } from '../../lib/storyboardSpaceSetCatalog.js';
import { filterSpaceSetsForModel } from '../../lib/identityScope.js';
import { generationExampleImageSources } from '../../lib/generationExamples.js';
import { cutTypeDisplay } from '../../lib/storyboardTaxonomy.js';
import { initialPhotoPlace, initialPhotoFraming, framingLabel, photoGalleryChoices, photoChoicePages, photoChoicePageForExample } from '../../lib/pageTemplates/storyboardPhotoChoices.js';
import { ExampleThumb, MineImageTab } from './StoryboardControls.jsx';
import { PhotoChoiceFilters, PhotoFraming, PhotoSegment, PhotoChoicePager } from './StoryboardChoiceControls.jsx';
import { PhotoMosaic } from './PhotoMosaic.jsx';
import { MoodGuide as EditorMoodGuide } from './StoryboardExampleGallery.jsx';
import styles from './StoryboardChoices.module.css';

export function StoryboardPhotoGallery({ block, catalogs, cut, studioStyle: requestedStyle, studioOptions, onStudioStyleChange,
  shot, shotOptions, onShotChange, clothingType = 'top', gender, identityKind, exampleId, onExampleChange, onExampleDrag, signature, projectId,
  refs = [], onRefsChange, onUseMine, onPickRef, onUploadStart, busy = false, ...editorProps }) {
  const selected = catalogs.genExamples?.find(example => example.id === exampleId);
  const display = cutTypeDisplay(cut, exampleId, catalogs.genExamples);
  const studioStyle = requestedStyle ?? display.studioStyle, studio = Boolean(studioStyle);
  const target = `${block?.id}:${cut}:${studioStyle}`;
  const initial = (autoExpand = true) => ({ target, blockId: block?.id, place: initialPhotoPlace(selected), framing: initialPhotoFraming(block, selected), expanded: autoExpand ? undefined : null });
  const [stored, setStored] = useState(() => initial());
  const fresh = () => initial(stored.blockId !== block?.id);
  const controls = stored.target === target ? stored : fresh();
  useEffect(() => { if (stored.target !== target) setStored(fresh()); }, [target]);
  const update = patch => setStored(previous => ({ ...(previous.target === target ? previous : fresh()), ...patch }));
  const [mine, setMine] = useState(false), [dragging, setDragging] = useState(false);
  const triggers = useRef(new Map()), galleryRegion = useRef(null), stickyTop = useRef(null), focusAfterClose = useRef(null);
  const sets = useMemo(() => filterSpaceSetsForModel(storyboardSpaceSetsFor({ gender, clothingType }), identityKind), [gender, clothingType, identityKind]);
  const choicesFor = (framing, resolvedPlace = null) => photoGalleryChoices((catalogs.genExamples || []).filter(example => !String(example.id).startsWith('sig_')), sets, {
    clothingType, gender, identityKind, shot: framing, place: studio ? 'studio' : controls.place, selectedId: exampleId, projectId,
    resolvedPlace, cutTypes: [studio ? studioStyle === 'minimal' ? 'styling' : 'horizon' : cut], studioStyle,
  });
  const all = choicesFor('all'), full = choicesFor('full', all.place), medium = choicesFor('medium', all.place);
  const visibleSignature = studioStyle === 'minimal' ? signature : null;
  const framingOptions = ['full', 'medium'].map(value => ({ value, disabled: !(value === 'full' ? full : medium).entries.length && !visibleSignature }));
  const framing = framingOptions.find(option => option.value === controls.framing && !option.disabled)?.value
    || framingOptions.find(option => !option.disabled)?.value || controls.framing;
  useEffect(() => {
    if (controls.framing !== framing || !studio && controls.place !== all.place) update({ framing, ...(!studio ? { place: all.place } : {}) });
  }, [target, framing, all.place]);
  const choices = framing === 'full' ? full : medium;
  const choiceCount = choices.entries.length + (visibleSignature ? 1 : 0);
  const listName = visibleSignature || choices.entries.some(entry => entry.kind !== 'stack') ? '사진 목록' : '세트 목록';
  const pages = photoChoicePages(choices.entries, visibleSignature);
  const currentPage = Math.min(controls.page ?? photoChoicePageForExample(pages, exampleId), pages.length - 1);
  const expandedId = controls.expanded === undefined ? choices.entries.find(entry => entry.kind === 'stack' && entry.members.some(example => example.id === exampleId))?.id : controls.expanded;
  const expanded = choices.entries.find(entry => entry.kind === 'stack' && entry.id === expandedId && entry.members.length > 0);
  useEffect(() => {
    const trigger = triggers.current.get(focusAfterClose.current);
    if (trigger) { trigger.focus({ preventScroll: true }); focusAfterClose.current = null; }
  }, [currentPage, expanded?.id]);
  const pending = Boolean(expanded && !expanded.members.some(example => example.id === exampleId));
  const changePage = requested => {
    const next = Math.max(0, Math.min(pages.length - 1, requested));
    if (next === currentPage) return;
    const region = galleryRegion.current;
    if (region?.contains(region.ownerDocument?.activeElement)) region.focus({ preventScroll: true });
    update({ page: next });
  };
  const choose = example => onExampleChange?.(example.id, example.cutType === 'horizon' || example.signature ? 'all' : 'pose');
  const image = example => <ExampleThumb example={{ ...example, thumb: generationExampleImageSources(example).src || example.thumb }} />;
  const dragProps = example => ({ draggable: !busy && Boolean(onExampleDrag),
    onDragStart: event => { event.dataTransfer.effectAllowed = 'copy'; event.dataTransfer.setData('text/example-id', example.id); setDragging(true); onExampleDrag?.(example.id); },
    onDragEnd: () => { setDragging(false); onExampleDrag?.(null); } });
  if (cut === 'product') return <EditorMoodGuide {...editorProps} catalogs={catalogs} cut={cut} shot={shot} shotOptions={shotOptions} onShotChange={onShotChange}
    clothingType={clothingType} gender={gender} identityKind={identityKind} exampleId={exampleId} onExampleChange={onExampleChange}
    refs={refs} onRefsChange={onRefsChange} onUseMine={onUseMine} onPickRef={onPickRef} onUploadStart={onUploadStart} usedExampleIds={new Set()} />;
  return <div className={styles.photoGallery}>
    <div className={styles.galleryRegion} ref={galleryRegion} tabIndex={0} role="region" aria-label="생성예시 갤러리" onKeyDown={event => {
      if (event.key === 'Escape' && expanded) {
        event.preventDefault(); event.stopPropagation(); focusAfterClose.current = expanded.id;
        update({ expanded: null, page: Math.max(0, pages.findIndex(items => items.some(entry => entry.id === expanded.id))) });
      }
      if (['ArrowLeft', 'ArrowRight'].includes(event.key)) {
        if (stickyTop.current?.contains(event.target)) return;
        event.preventDefault(); changePage(currentPage + (event.key === 'ArrowRight' ? 1 : -1));
      }
    }}>
      <div className={styles.photoTop} ref={stickyTop}>
        <div className={styles.controlRow}>
          {studio ? studioOptions && <PhotoSegment options={studioOptions} value={studioStyle} label="스튜디오컷 종류" disabled={busy} onChange={onStudioStyleChange} />
            : <PhotoChoiceFilters tabs={all.tabs} place={all.place} disabled={busy} onPlace={place => update({ place, page: undefined, expanded: null })} />}
          <PhotoFraming value={framing} options={framingOptions} onChange={value => update({ framing: value, page: undefined, expanded: (value === 'full' ? full : medium).entries.some(entry => entry.id === expanded?.id && entry.members?.length > 0) ? expanded.id : null })} clothingType={clothingType} disabled={busy} />
        </div>
      {expanded && <div className={styles.tray} data-photo-tray={expanded.id} data-pending={pending} data-dragging={dragging} role="group" aria-label="이 세트에서 한 장을 골라주세요">
        {pending && <svg className={styles.trayAnts} aria-hidden="true"><rect /></svg>}
        <div className={styles.trayHead}><p>이 세트에서 한 장을 골라주세요</p><span>{expanded.members.length}장</span></div>
        <div className={styles.trayCuts} data-scroll={expanded.members.length > 5}>
          {expanded.members.map(example => <button type="button" key={example.id} disabled={busy}
            className={`${styles.photoChoice}${example.id === exampleId ? ` ${styles.chosen}` : ''}${!pending && example.id !== exampleId ? ` ${styles.veiled}` : ''}`}
            aria-pressed={example.id === exampleId} aria-label={`${framingLabel(example.shot, clothingType)} ${{ front: '앞', back: '뒤', side: '옆', side_right: '오른쪽 옆', profile: '옆', profile_right: '오른쪽 옆' }[example.direction] || '앞'}`}
            data-example-id={example.id} onClick={() => choose(example)} {...dragProps(example)}>
            {image(example)}{example.id === exampleId && <span className={styles.check}><Icon name="check" size={12} /></span>}
          </button>)}
        </div>
      </div>}
      </div>
      <div className={styles.photoList} onFocusCapture={event => {
        const target = event.target, top = target.getBoundingClientRect().top;
        const bottom = stickyTop.current?.getBoundingClientRect().bottom;
        const gap = parseFloat(target.ownerDocument?.defaultView?.getComputedStyle(target).scrollMarginTop) || 0;
        if (top < bottom + gap) galleryRegion.current?.closest('[data-storyboard-scroll]')?.scrollBy({ top: top - bottom - gap, behavior: 'instant' });
      }}>
      {choiceCount > 0 && <div className={styles.photoListHead}><p>{listName}</p><span>{choiceCount}개</span></div>}
      <div className={styles.photoGrid}>{pages[currentPage].map((entry, index) => {
        const group = entry.kind === 'stack', open = expanded?.id === entry.id;
        const chosen = group ? entry.members.some(example => example.id === exampleId) : entry.example.id === exampleId;
        return <button type="button" key={entry.id} ref={node => { if (node) triggers.current.set(entry.id, node); else triggers.current.delete(entry.id); }}
          className={`${styles.photoChoice}${chosen ? ` ${styles.chosen}` : ''}${open ? ` ${styles.expandedChoice}` : ''}${expanded && !open ? ` ${styles.dimmed}` : ''}`}
          disabled={busy} aria-pressed={chosen} aria-expanded={group ? open : undefined}
          aria-label={entry.kind === 'signature' ? '시그니처 사진' : group ? `${index + 1}번째 장소 사진 펼치기` : `${index + 1}번째 예시 선택`}
          data-example-id={entry.example.id} data-photo-choice={entry.id}
          onClick={() => {
            if (group && entry.members.length > 1) update({ expanded: entry.id });
            else { if (group) update({ expanded: null }); return choose(entry.example); }
          }}
          {...(entry.kind === 'signature' ? {} : dragProps(entry.example))}>
          {group ? <PhotoMosaic members={entry.members} cover={entry.example} selected={chosen} /> : image(entry.example)}
          {entry.kind === 'signature' && <span className={styles.signatureBadge}>시그니처</span>}
          {chosen && <span className={styles.check}><Icon name="check" size={12} /></span>}
        </button>;
      })}</div>
    {!choices.entries.length && !visibleSignature && <p className={styles.empty}>이 조건의 장소 사진을 준비 중이에요.</p>}
    <PhotoChoicePager page={currentPage} count={pages.length} onChange={changePage} />
    {onUseMine && <><button type="button" className={styles.textAction} aria-expanded={mine} onClick={() => setMine(value => !value)}>내 사진으로 바꾸기</button>
      {mine && <MineImageTab images={refs} onImagesChange={onRefsChange} onChoose={onUseMine} onUploadStart={onUploadStart}
        onPickImage={() => onPickRef ? onPickRef() : api.pickRefImage(useAppStore.getState().projectId)} />}</>}
      </div>
    </div>
  </div>;
}
