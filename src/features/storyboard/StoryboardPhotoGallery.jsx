import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { storyboardSpaceSetsFor } from '../../lib/storyboardSpaceSetCatalog.js';
import { filterSpaceSetsForModel } from '../../lib/identityScope.js';
import { generationExampleImageSources } from '../../lib/generationExamples.js';
import { initialPhotoPlace, initialPhotoFraming, isSinglePhotoSection, photoGalleryChoices, photoChoicePages } from '../../lib/pageTemplates/storyboardPhotoChoices.js';
import { ExampleThumb, MineImageTab } from './StoryboardControls.jsx';
import { PhotoChoiceFilters, PhotoFraming, PhotoChoicePager } from './StoryboardChoiceControls.jsx';
import { MoodGuide as EditorMoodGuide } from './StoryboardExampleGallery.jsx';
import styles from './StoryboardChoices.module.css';

export function StoryboardPhotoGallery({ block, catalogs, cut, shot, shotOptions, onShotChange, clothingType = 'top', gender,
  identityKind, exampleId, onExampleChange, onExampleDrag, spaceContext, signature, projectId,
  refs = [], onRefsChange, onUseMine, onPickRef, busy = false, ...editorProps }) {
  const selected = catalogs.genExamples?.find(example => example.id === exampleId);
  const single = isSinglePhotoSection(block), studio = !single && cut === 'horizon';
  const [place, setPlace] = useState(() => initialPhotoPlace(selected));
  const [light, setLight] = useState('all'), [framing, setFraming] = useState(() => initialPhotoFraming(block, selected));
  const [page, setPage] = useState(0), [expanded, setExpanded] = useState(null), [mine, setMine] = useState(false);
  const ownSet = spaceContext?.set;
  const ownsSelected = ownSet?.members.some(member => member.exampleId === exampleId);
  const [otherOpen, setOtherOpen] = useState(() => !ownSet || spaceContext?.siblings?.length === 1 || !ownsSelected);
  const triggers = useRef(new Map());
  const galleryRegion = useRef(null);
  useEffect(() => {
    setPlace(initialPhotoPlace(selected)); setLight('all'); setFraming(initialPhotoFraming(block, selected)); setPage(0); setExpanded(null);
  }, [block?.id]);
  useEffect(() => { setOtherOpen(!ownSet || spaceContext?.siblings?.length === 1 || !ownsSelected); }, [ownSet?.id, spaceContext?.siblings?.length, ownsSelected]);
  const sets = useMemo(() => filterSpaceSetsForModel(storyboardSpaceSetsFor({ gender, clothingType }), identityKind), [gender, clothingType, identityKind]);
  const choices = useMemo(() => photoGalleryChoices(catalogs.genExamples || [], sets, {
    cutTypes: single ? ['styling'] : [cut], clothingType, gender, identityKind,
    shot: framing, place: studio ? 'studio' : place, light, selectedId: exampleId, projectId,
  }), [catalogs.genExamples, sets, single, cut, clothingType, gender, identityKind, framing, studio, place, light, exampleId, projectId]);
  // Remember the fallback, so relaxing a filter does not jump back to a tab
  // that was already left. Render the resolved tab immediately, before effects.
  useEffect(() => { if (!studio && place !== choices.place) setPlace(choices.place); }, [studio, place, choices.place]);
  const pages = photoChoicePages(choices.entries, signature), currentPage = Math.min(page, pages.length - 1), entries = pages[currentPage];
  const changePage = requested => {
    const next = Math.max(0, Math.min(pages.length - 1, requested));
    if (next === currentPage) return;
    const region = galleryRegion.current;
    if (region?.contains(region.ownerDocument?.activeElement)) region.focus({ preventScroll: true });
    setPage(next); setExpanded(null);
  };
  useEffect(() => { setPage(0); setExpanded(null); }, [choices.place, light, framing, block?.id]);
  const choose = (example, returnTo) => {
    const result = onExampleChange?.(example.id, example.cutType === 'horizon' || example.signature ? 'all' : 'pose');
    Promise.resolve(result).then(() => { setExpanded(null); triggers.current.get(returnTo)?.focus?.({ preventScroll: true }); }).catch(() => {});
  };
  const image = example => <ExampleThumb example={{ ...example, thumb: generationExampleImageSources(example).src || example.thumb }} />;
  const card = (entry, index) => {
    const open = expanded === entry.id, group = entry.kind === 'stack';
    const chosen = group ? entry.members.some(example => example.id === exampleId) : entry.example.id === exampleId;
    return <button type="button" key={entry.id} ref={node => { if (node) triggers.current.set(entry.id, node); else triggers.current.delete(entry.id); }}
      className={`${styles.photoChoice}${chosen ? ` ${styles.chosen}` : ''}${open ? ` ${styles.expandedChoice}` : ''}${expanded && !open ? ` ${styles.dimmed}` : ''}`}
      disabled={busy} aria-pressed={chosen} aria-expanded={group ? open : undefined}
      aria-label={entry.kind === 'signature' ? '시그니처 사진' : group ? `${index + 1}번째 장소 사진 펼치기` : `${index + 1}번째 예시 선택`}
      data-example-id={entry.example.id} data-photo-choice={entry.id}
      onClick={() => group ? setExpanded(open ? null : entry.id) : choose(entry.example, entry.id)}
      draggable={!busy && Boolean(onExampleDrag) && entry.kind !== 'signature'}
      onDragStart={event => { event.dataTransfer.effectAllowed = 'copy'; event.dataTransfer.setData('text/example-id', entry.example.id); onExampleDrag?.(entry.example.id); }}
      onDragEnd={() => onExampleDrag?.(null)}>{image(entry.example)}
      {entry.kind === 'signature' && <span className={styles.signatureBadge}>시그니처</span>}
      {chosen && <span className={styles.check}><Icon name="check" size={12} /></span>}</button>;
  };
  const cells = [];
  entries.forEach((entry, index) => {
    cells.push(card(entry, index));
    if (index % 3 === 2 || index === entries.length - 1) {
      const active = entries.slice(index - index % 3, index + 1).find(item => item.kind === 'stack' && item.id === expanded);
      if (active) cells.push(<div key={`expanded:${active.id}`} className={styles.expansion} role="group" aria-label="이 장소의 사진">
        {active.members.map((example, memberIndex) => <button type="button" key={example.id} disabled={busy}
          className={`${styles.photoChoice}${example.id === exampleId ? ` ${styles.chosen}` : ''}`} aria-pressed={example.id === exampleId}
          aria-label={`${memberIndex + 1}번째 사진 선택`} data-example-id={example.id} onClick={() => choose(example, active.id)}>
          {image(example)}{example.id === exampleId && <span className={styles.check}><Icon name="check" size={12} /></span>}
        </button>)}
      </div>);
    }
  });
  if (cut === 'product') return <EditorMoodGuide {...editorProps} catalogs={catalogs} cut={cut} shot={shot} shotOptions={shotOptions} onShotChange={onShotChange}
    clothingType={clothingType} gender={gender} identityKind={identityKind} exampleId={exampleId} onExampleChange={onExampleChange}
    refs={refs} onRefsChange={onRefsChange} onUseMine={onUseMine} onPickRef={onPickRef} usedExampleIds={new Set()} />;
  return <div className={styles.photoGallery}>
    {ownSet && !single && <div className={styles.samePlace}>
      <h3>같은 장소의 다른 사진</h3><div className={styles.photoGrid}>
        {ownSet.members.map((member, index) => {
          const example = catalogs.genExamples?.find(item => item.id === member.exampleId);
          return example ? <button type="button" key={member.exampleId} disabled={busy} aria-pressed={member.exampleId === exampleId}
            className={`${styles.photoChoice}${member.exampleId === exampleId ? ` ${styles.chosen}` : ''}`} aria-label={`${index + 1}번째 같은 장소 사진`}
            data-example-id={member.exampleId} onClick={() => choose(example)}>{image(example)}
            {member.exampleId === exampleId && <span className={styles.check}><Icon name="check" size={12} /></span>}</button> : null;
        })}
      </div><button type="button" className={styles.textAction} aria-expanded={otherOpen} onClick={() => setOtherOpen(value => !value)}>
        {otherOpen ? '접기' : '이 컷만 다른 사진으로 바꾸기'}<Icon name={otherOpen ? 'chevUp' : 'chevDown'} size={14} />
      </button>
    </div>}
    {(single || otherOpen) && <>
      <PhotoChoiceFilters tabs={choices.tabs} place={choices.place} light={light} studio={studio} disabled={busy} onPlace={setPlace} onLight={setLight} />
      <PhotoFraming value={framing} onChange={setFraming} clothingType={clothingType} single={single} disabled={busy} />
      <div className={styles.galleryRegion} ref={galleryRegion} tabIndex={0} role="region" aria-label="생성예시 갤러리" onKeyDown={event => {
        if (event.key === 'Escape' && expanded) { event.preventDefault(); event.stopPropagation(); triggers.current.get(expanded)?.focus(); setExpanded(null); }
        if (['ArrowLeft','ArrowRight'].includes(event.key)) { event.preventDefault(); changePage(currentPage+(event.key==='ArrowRight'?1:-1)); }
      }}><div className={styles.photoGrid}>{cells}</div></div>
      {!choices.entries.length && <p className={styles.empty}>이 조건의 장소 사진을 준비 중이에요.</p>}
      <PhotoChoicePager page={currentPage} count={pages.length} onChange={changePage} />
    </>}
    {onUseMine && <><button type="button" className={styles.textAction} aria-expanded={mine} onClick={() => setMine(value => !value)}>내 사진으로 바꾸기</button>
      {mine && <MineImageTab images={refs} onImagesChange={onRefsChange} onChoose={onUseMine}
        onPickImage={() => onPickRef ? onPickRef() : api.pickRefImage(useAppStore.getState().projectId)} />}</>}
  </div>;
}
