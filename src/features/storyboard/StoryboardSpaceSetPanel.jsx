import React, { useMemo, useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import { filterSpaceSetsForModel } from '../../lib/identityScope.js';
import { inferStoryboardSpaceSet, storyboardSpaceSetsFor } from '../../lib/storyboardSpaceSetCatalog.js';
import { examplePlace } from '../../lib/pageTemplates/storyboardGallery.js';
import { PlaceTabs } from './StoryboardExampleGallery.jsx';
import { spaceSetDisplayName } from '../../lib/spaceSetDisplayNames.js';
import styles from './StoryboardPanels.module.css';

export function StoryboardSpaceSetPanel({ mode, error, onChoose, gender, clothingType, usage, cutType,
  identityKind, busy, currentMembers = [], remaining = 12, onExampleDrag, canChooseSet = () => true }) {
  const [place, setPlace] = useState('all');
  const currentSet = inferStoryboardSpaceSet(currentMembers[0]?.spaceGroupId);
  const sets = useMemo(() => filterSpaceSetsForModel(storyboardSpaceSetsFor({ gender, clothingType }), identityKind)
    .filter(set => (currentSet ? currentSet.setType === 'horizon-rotation' : usage === 'fit' && cutType !== 'styling') ? set.setType === 'horizon-rotation'
      : cutType === 'horizon' ? set.setType === 'horizon-sequence' : set.setType === 'styling'), [gender, clothingType, identityKind, usage, cutType, currentSet?.setType]);
  const shown = sets.filter(set => place === 'all' || examplePlace({ id: set.members[0].exampleId }).value === place);
  return <div className={styles.setPanel}>
    <div className={styles.panelHeading}><h2>{mode === 'replace' ? cutType === 'horizon' ? '스튜디오 세트 바꾸기' : '장소 바꾸기' : '장소 세트 추가'}</h2></div>
    {cutType !== 'horizon' && <PlaceTabs value={place} onChange={setPlace} />}
    <div className={styles.setList}>{shown.map(set => {
      const full = mode !== 'replace' && set.members.length > remaining || !canChooseSet(set);
      return <article className={styles.setCard} key={set.id}>
        <button type="button" disabled={busy || full} aria-pressed={currentSet?.id === set.id}
          className={`${styles.setRepresentative}${currentSet?.id === set.id ? ` ${styles.setSelected}` : ''}`} onClick={() => onChoose(set)}>
          <img src={set.members[0].thumb} alt="" loading="lazy" />
          <span><strong>{spaceSetDisplayName(set)}</strong><small>{set.members.length}장{full ? ', 자리가 부족해요' : ''}</small></span>
          {currentSet?.id === set.id && <Icon name="check" size={16} />}
        </button>
        <div className={styles.setMembers}>{set.members.map(member => <img key={member.exampleId} src={member.thumb} alt="세트 사진"
          loading="lazy" draggable={!busy && Boolean(onExampleDrag)}
          onDragStart={event => { event.dataTransfer.effectAllowed = 'copy'; event.dataTransfer.setData('text/example-id', member.exampleId); onExampleDrag?.(member.exampleId); }}
          onDragEnd={() => onExampleDrag?.(null)} />)}</div>
      </article>;
    })}</div>
    {!shown.length && <p className="hint">이 상품에 맞는 장소 세트를 준비 중이에요.</p>}
    {error && <p className="sb-save-error" role="alert">{error}</p>}
  </div>;
}
