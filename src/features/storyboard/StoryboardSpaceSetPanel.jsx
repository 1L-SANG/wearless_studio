import React, { useEffect, useMemo, useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import { filterSpaceSetsForModel } from '../../lib/identityScope.js';
import { inferStoryboardSpaceSet, storyboardSpaceSetsFor } from '../../lib/storyboardSpaceSetCatalog.js';
import { examplePlace } from '../../lib/pageTemplates/storyboardGallery.js';
import { initialPhotoPlace, exampleLight, visiblePhotoPlaces, resolvePhotoPlace, orderPhotoChoices, photoChoicePages } from '../../lib/pageTemplates/storyboardPhotoChoices.js';
import { PhotoChoiceFilters, PhotoChoicePager } from './StoryboardChoiceControls.jsx';
import { ExampleThumb } from './StoryboardControls.jsx';
import styles from './StoryboardChoices.module.css';

export function StoryboardSpaceSetPanel({ mode, error, onChoose, gender, clothingType, usage, cutType,
  identityKind, busy, projectId, currentMembers = [], remaining = 12, onExampleDrag, canChooseSet = () => true }) {
  const currentSet = inferStoryboardSpaceSet(currentMembers[0]?.spaceGroupId);
  const target = `${mode}:${currentMembers[0]?.spaceGroupId || ''}:${currentSet?.id || ''}:${cutType}:${gender}:${clothingType}`;
  const initial = () => ({ target, place: initialPhotoPlace({ id: currentMembers[0]?.exampleId }), light: 'all', page: 0 });
  const [storedControls, setControls] = useState(initial);
  // Derive fresh controls during the target's first render. An effect resetting
  // them later would race with fallback persistence from the previous target.
  const { place, light, page } = storedControls.target === target ? storedControls : initial();
  const updateControls = patch => setControls(previous => {
    const current = previous.target === target ? previous : initial();
    if (previous === current && Object.entries(patch).every(([key, value]) => current[key] === value)) return previous;
    return { ...current, ...patch };
  });
  const setPlace = value => updateControls({ place: value, page: 0 });
  const setLight = value => updateControls({ light: value, page: 0 });
  const setPage = value => updateControls({ page: value });
  const sets = useMemo(() => filterSpaceSetsForModel(storyboardSpaceSetsFor({ gender, clothingType }), identityKind)
    .filter(set => (currentSet ? currentSet.setType === 'horizon-rotation' : usage === 'fit' && cutType !== 'styling') ? set.setType === 'horizon-rotation'
      : cutType === 'horizon' ? set.setType === 'horizon-sequence' : set.setType === 'styling'), [gender, clothingType, identityKind, usage, cutType, currentSet?.setType]);
  const eligible = sets.map(set => {
    const cover = set.members.find(member => member.shot === 'full') || set.members[0];
    return { id: set.id, set, example: { ...cover, id: cover.exampleId, spaceSetId: set.id } };
  }).filter(entry => light === 'all' || exampleLight(entry.example) === light);
  const tabs = visiblePhotoPlaces(eligible.map(entry => entry.example));
  const activePlace = cutType === 'horizon' ? 'studio' : resolvePhotoPlace(place, tabs);
  useEffect(() => { if (cutType !== 'horizon' && place !== activePlace) setPlace(activePlace); }, [cutType, place, activePlace]);
  useEffect(() => { setPage(0); }, [target, activePlace, light]);
  const entries = eligible.filter(entry => cutType === 'horizon' || examplePlace(entry.example).value === activePlace);
  const ordered = orderPhotoChoices(entries, { projectId, scope: `set:${cutType}:${activePlace}:${light}` });
  const pages = photoChoicePages(ordered), currentPage = Math.min(page, pages.length - 1);
  return <div>
    <div className={styles.setPanelHeader}><h2>{mode === 'replace' ? cutType === 'horizon' ? '스튜디오 세트 바꾸기' : '장소 바꾸기' : '장소 세트 추가'}</h2></div>
    <PhotoChoiceFilters tabs={tabs} place={activePlace} light={light} studio={cutType === 'horizon'} onPlace={setPlace} onLight={setLight} disabled={busy} />
    <div className={styles.photoGrid}>{pages[currentPage].map(({ set, example }, index) => {
      const full = mode !== 'replace' && set.members.length > remaining || !canChooseSet(set);
      const selected = currentSet?.id === set.id;
      const added = cutType === 'horizon' && mode === 'replace' ? set.members.length - currentMembers.length : 0;
      return <button type="button" key={set.id} disabled={busy || full} aria-pressed={selected}
        className={`${styles.photoChoice}${selected ? ` ${styles.chosen}` : ''}`} data-space-set={set.id} data-example-id={example.id}
        aria-label={`${index + 1}번째 ${cutType === 'horizon' ? '스튜디오' : '장소'} 세트${full ? ', 자리가 부족해요' : ''}`} onClick={() => onChoose(set)}
        draggable={!busy && Boolean(onExampleDrag)} onDragStart={event => {
          event.dataTransfer.effectAllowed = 'copy'; event.dataTransfer.setData('text/example-id', example.id); onExampleDrag?.(example.id);
        }} onDragEnd={() => onExampleDrag?.(null)}>
        <ExampleThumb example={example} />
        {selected && <span className={styles.check}><Icon name="check" size={12} /></span>}
        {added > 0 && !selected && <span className={styles.cutDelta}>+{added}컷</span>}
      </button>;
    })}</div>
    <PhotoChoicePager page={currentPage} count={pages.length} onChange={setPage} />
    {!ordered.length && <p className={styles.empty}>이 조건의 장소 세트를 준비 중이에요.</p>}
    {error && <p className={styles.saveError} role="alert">{error}</p>}
  </div>;
}
