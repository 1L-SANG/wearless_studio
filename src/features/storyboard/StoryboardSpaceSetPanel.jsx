import React, { useEffect, useMemo, useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import { filterSpaceSetsForModel } from '../../lib/identityScope.js';
import { inferStoryboardSpaceSet, storyboardSpaceSetsFor } from '../../lib/storyboardSpaceSetCatalog.js';
import { examplePlace } from '../../lib/pageTemplates/storyboardGallery.js';
import { initialPhotoPlace, visiblePhotoPlaces, resolvePhotoPlace, orderPhotoChoices, photoChoicePages } from '../../lib/pageTemplates/storyboardPhotoChoices.js';
import { PhotoChoiceFilters, PhotoChoicePager, PhotoSegment, ChoiceReason } from './StoryboardChoiceControls.jsx';
import { PhotoMosaic } from './PhotoMosaic.jsx';
import { OuterLockIcon, ToneIcon } from './SetOptionMarks.jsx';
import { cutTypeDisplay } from '../../lib/storyboardTaxonomy.js';
import { photoSlotForBlock } from '../../lib/pageTemplates/slots.js';
import { setPhotoGroupId, setOptionState } from '../../lib/storyboardSetOptions.js';
import { unusedSpaceSetMembers } from '../../lib/storyboardSpaceSets.js';
import { horizonBackgroundAvailability } from '../../lib/horizonBackgroundAvailability.js';
import styles from './StoryboardChoices.module.css';

const EXPLANATIONS = { background: '뒷배경이 의류색과 톤온톤으로 맞춰져요', outer: '아우터 앞을 잠근 모습으로 만들어요' };
export function StoryboardSpaceSetPanel({ mode, error, onChoose, gender, clothingType, usage, cutType,
  identityKind, busy, projectId, currentMembers = [], product = {}, colorOpts = [], frontClosure, garmentColorEvidence,
  onChangeSpaceBackground, onChangeSpaceOuterClosure, remaining = 12, onExampleDrag, canChooseSet = () => true }) {
  const currentSet = inferStoryboardSpaceSet(currentMembers[0]?.spaceGroupId);
  const display = cutTypeDisplay(cutType, currentMembers[0]?.exampleId);
  const studioPicker = mode === 'replace' && Boolean(display.studioStyle || currentSet?.studioStyle === 'minimal');
  const recipeSet = photoSlotForBlock(currentMembers[0])?.recipe?.set;
  const horizonSetType = currentSet?.setType?.startsWith('horizon-') ? currentSet.setType
    : recipeSet === 'sequence' ? 'horizon-sequence'
      : recipeSet === 'rotation' || usage === 'fit' ? 'horizon-rotation' : 'horizon-sequence';
  const placedSet = Boolean(setPhotoGroupId(currentMembers[0], currentMembers));
  const optionState = setOptionState(currentMembers, { ...product, clothingType }, frontClosure, colorOpts);
  const target = `${mode}:${currentMembers[0]?.spaceGroupId || ''}:${currentSet?.id || ''}:${cutType}:${gender}:${clothingType}`;
  const initial = () => ({ target, place: initialPhotoPlace({ id: currentMembers[0]?.exampleId }), page: 0, studioStyle: display.studioStyle || 'horizon' });
  const [storedControls, setControls] = useState(initial);
  const [saving, setSaving] = useState(false), [optionError, setOptionError] = useState(null);
  const [backgroundDraft, setBackgroundDraft] = useState(optionState.backgroundOn);
  // A replacement starts from the newly saved set. Browsing never writes blocks.
  const controls = storedControls.target === target ? storedControls : initial();
  useEffect(() => { if (storedControls.target !== target) setControls(initial()); }, [target]);
  const { place, page, studioStyle } = controls;
  const updateControls = patch => setControls(previous => ({ ...(previous.target === target ? previous : initial()), ...patch }));
  const setPlace = value => updateControls({ place: value, page: 0 });
  const setPage = value => updateControls({ page: value });
  const availableSets = useMemo(() => filterSpaceSetsForModel(storyboardSpaceSetsFor({ gender, clothingType }), identityKind), [gender, clothingType, identityKind]);
  const matchesStudioStyle = (set, value) => value === 'minimal' ? set.studioStyle === 'minimal' : set.setType === horizonSetType;
  const studioOptions = display.studioOptions.map(option => ({ ...option, disabled: !availableSets.some(set => matchesStudioStyle(set, option.value)),
    disabledReason: '이 조건은 아직 서비스에 공개되지 않았어요' }));
  const sets = availableSets.filter(set => studioPicker ? matchesStudioStyle(set, studioStyle)
    : ((currentSet ? currentSet.setType === 'horizon-rotation' : usage === 'fit' && cutType !== 'styling') ? set.setType === 'horizon-rotation'
      : cutType === 'horizon' ? set.setType === 'horizon-sequence' : set.setType === 'styling') && set.studioStyle !== 'minimal');
  const studio = studioPicker || cutType === 'horizon';
  const eligible = sets.map(set => {
    const cover = set.members.find(member => member.direction === 'front' && member.shot === 'full') || set.members[0];
    return { id: set.id, set, example: { ...cover, id: cover.exampleId, spaceSetId: set.id } };
  });
  const tabs = visiblePhotoPlaces(eligible.map(entry => entry.example)), activePlace = studio ? 'studio' : resolvePhotoPlace(place, tabs);
  useEffect(() => { if (!studio && place !== activePlace) setPlace(activePlace); }, [studio, place, activePlace]);
  const entries = eligible.filter(entry => studio || examplePlace(entry.example).value === activePlace);
  // Keep the existing tone ring, without a light filter or light-based ranking.
  const ordered = orderPhotoChoices(entries, { projectId, scope: `set:${cutType}:${activePlace}:all` });
  const pages = photoChoicePages(ordered), currentPage = Math.min(page, pages.length - 1);
  const backgroundVisible = placedSet && studio && studioStyle === 'horizon' && optionState.chromatic;
  const outerVisible = placedSet && optionState.outerVisible && mode === 'replace';
  const horizonMembers = currentMembers.filter(member => member.cutType === 'horizon');
  const backgroundOn = horizonMembers.length ? optionState.backgroundOn : backgroundDraft;
  useEffect(() => { if (horizonMembers.length) setBackgroundDraft(optionState.backgroundOn); }, [target, optionState.backgroundOn]);
  // When browsing from a minimal set, check the same colors against a horizon
  // candidate. The choice is local until a horizon set is actually selected.
  const availability = horizonBackgroundAvailability(horizonMembers.length ? horizonMembers : currentMembers.map(member => ({ ...member, cutType: 'horizon' })),
    product, garmentColorEvidence, horizonMembers.length ? currentSet?.id : availableSets.find(set => matchesStudioStyle(set, 'horizon'))?.id);
  const backgroundUnavailable = availability.state === 'unavailable';
  const shown = { background: backgroundVisible && backgroundOn, outer: outerVisible && optionState.outerClosed };
  const explanation = shown[controls.explanation] ? controls.explanation : shown.outer ? 'outer' : shown.background ? 'background' : null;
  const changeOption = async kind => {
    if (!placedSet || busy || saving || kind === 'background' && backgroundUnavailable) return;
    const enabled = kind === 'background' ? !backgroundOn : !optionState.outerClosed;
    setOptionError(null); setSaving(true);
    try {
      if (kind === 'outer') await onChangeSpaceOuterClosure?.(enabled);
      else if (horizonMembers.length) await onChangeSpaceBackground?.(enabled ? 'garment-tone' : 'reference');
      if (kind === 'background') setBackgroundDraft(enabled);
      updateControls({ explanation: enabled ? kind : kind === 'outer' ? 'background' : 'outer' });
    } catch { setOptionError('변경 내용을 저장하지 못했어요'); }
    finally { setSaving(false); }
  };
  return <div>
    <div className={styles.setPanelHeader}><h2>{mode === 'replace' ? studio ? '스튜디오 세트 바꾸기' : '장소 바꾸기' : '장소 세트 추가'}</h2>
      {studioPicker && <PhotoSegment options={studioOptions} value={studioStyle} disabled={busy || saving} label="스튜디오컷 종류"
        onChange={value => updateControls({ studioStyle: value, page: 0 })} />}</div>
    {!studio && <PhotoChoiceFilters tabs={tabs} place={activePlace} onPlace={setPlace} disabled={busy || saving} />}
    {(backgroundVisible || outerVisible) && <div className={styles.optionRow}>
      <p className={styles.optionExplanation}><span>{explanation ? EXPLANATIONS[explanation] : ''}</span>
        {Object.entries(EXPLANATIONS).filter(([kind]) => kind === 'outer' ? outerVisible : backgroundVisible).map(([kind, text]) => <span key={kind} className={styles.explanationSize} aria-hidden="true">{text}</span>)}
      </p>
      <div className={`modal-actions ${styles.optionChips}`}>
        {outerVisible && <button type="button" className={styles.optionChip} aria-pressed={optionState.outerClosed} disabled={busy || saving} onClick={() => changeOption('outer')}>
          <OuterLockIcon closed={optionState.outerClosed} /><span data-label="아우터 잠그기">아우터 잠그기</span></button>}
        {backgroundVisible && <ChoiceReason reason={backgroundUnavailable ? availability.reason : null} id="sb-background-reason">
          <button type="button" className={styles.optionChip} aria-pressed={backgroundOn} disabled={busy || saving}
            aria-disabled={backgroundUnavailable || undefined} aria-describedby={backgroundUnavailable ? 'sb-background-reason' : undefined} onClick={() => changeOption('background')}>
            <ToneIcon color={optionState.color} /><span data-label="배경색 맞추기">배경색 맞추기</span></button>
        </ChoiceReason>}
      </div>
    </div>}
    <div className={styles.setGrid}>{pages[currentPage].map(({ set, example }, index) => {
      const full = mode !== 'replace' && set.members.length > remaining || !canChooseSet(set), selected = currentSet?.id === set.id;
      const members = set.members.map(member => ({ ...member, id: member.exampleId }));
      const unplacedExampleIds = mode === 'replace' && selected
        ? unusedSpaceSetMembers(set, currentMembers.filter(block => !block.similarSourceBlockId)).map(member => member.exampleId) : [];
      const placed = set.members.length - unplacedExampleIds.length;
      return <button type="button" key={set.id} disabled={busy || saving || full} aria-pressed={selected}
        className={`${styles.setChoice}${selected ? ` ${styles.chosen}` : ''}`} data-space-set={set.id} data-example-id={example.id}
        aria-label={`${index + 1}번째 ${studio ? '스튜디오' : '장소'} 세트${full ? ', 자리가 부족해요' : ''}${unplacedExampleIds.length ? `, ${set.members.length}장 중 ${placed}장 놓임` : ''}`} onClick={() => onChoose(set, { backgroundMode: backgroundOn ? 'garment-tone' : 'reference' })}
        draggable={!busy && !saving && Boolean(onExampleDrag)} onDragStart={event => {
          event.dataTransfer.effectAllowed = 'copy'; event.dataTransfer.setData('text/example-id', example.id); onExampleDrag?.(example.id);
        }} onDragEnd={() => onExampleDrag?.(null)}>
        <PhotoMosaic members={members} cover={example} unplacedExampleIds={unplacedExampleIds} all />
        {selected && <span className={styles.check}><Icon name="check" size={12} /></span>}
        {placedSet && selected && backgroundOn && set.setType.startsWith('horizon') && <span className={styles.cardTone}><ToneIcon color={optionState.color} dots /></span>}
        <span className={styles.cutCount}>{placed}컷</span>
      </button>;
    })}</div>
    <PhotoChoicePager page={currentPage} count={pages.length} onChange={setPage} />
    {!ordered.length && <p className={styles.empty}>이 조건의 장소 세트를 준비 중이에요.</p>}
    {(error || optionError) && <p className={styles.saveError} role="alert">{error || optionError}</p>}
  </div>;
}
