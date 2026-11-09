import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Icon } from '@/components/ui.jsx';
import { selectGenerationExamples, paginateGenerationGalleryItems, generationGalleryPageForKey, generationExampleImageSources, generationGalleryPageForExample, clampGenerationGalleryPage, storedExampleConditionStatus, hasAvailableGenerationRecipe } from '../../lib/generationExamples.js';
import { filterExamplesForModel, filterSpaceSetsForModel } from '../../lib/identityScope.js';
import { storyboardSpaceSetsFor } from '../../lib/storyboardSpaceSetCatalog.js';
import { spaceSetDisplayName } from '../../lib/spaceSetDisplayNames.js';
import { examplePlace, filterGalleryExamples, visiblePlaceTabs, groupGalleryExamples, sampleGalleryEntries, createGallerySessionSeed } from '../../lib/pageTemplates/storyboardGallery.js';
import { isSourceBasedDetailRecipe } from '../../lib/detailRecommendations.js';
import { ExampleThumb, MineImageTab, ShotSegment } from './StoryboardControls.jsx';
import { StoryboardStackPopup } from './StoryboardStackPopup.jsx';
import styles from './StoryboardPanels.module.css';

export function PlaceTabs({ value, onChange }) {
  const [expanded, setExpanded] = useState(false);
  return <div className={styles.placeTabs} aria-label="장소">
    {visiblePlaceTabs(value, expanded).map(tab => <button type="button" key={tab.value}
      className={value === tab.value ? styles.placeOn : ''} aria-pressed={value === tab.value}
      onClick={() => onChange(tab.value)}>{tab.label}</button>)}
    <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? '접기⌃' : '더보기⌄'}</button>
  </div>;
}

export function MoodGuide({ catalogs, cut, direction, sideStyle = null, shot, shotOptions = null, onShotChange,
  clothingType = 'top', gender = null, exampleId, onExampleChange, onExampleDrag = null,
  refs = [], onRefsChange, onPickRef, onUseMine = null, identityKind = null, usedExampleIds = new Set(), spaceContext = null,
  gallerySeedFactory = createGallerySessionSeed, productPresentation = null }) {
  const [mode, setMode] = useState('set'), [place, setPlace] = useState('all'), [shotFilter, setShotFilter] = useState('all');
  const [expandedSetId, setExpandedSetId] = useState(null), [mine, setMine] = useState(false);
  const ownSet = spaceContext?.set;
  const ownSetContainsSelected = Boolean(ownSet?.members.some(member => member.exampleId === exampleId));
  const singleton = spaceContext?.siblings?.length === 1;
  const ownSetInstance = spaceContext?.siblings?.map(block => block.spaceGroupId || block.id).join('|');
  const [otherPhotosOpen, setOtherPhotosOpen] = useState(() => Boolean(ownSet && (singleton || !ownSetContainsSelected)));
  const fullGalleryOpen = !ownSet || cut === 'horizon' || otherPhotosOpen;
  useEffect(() => { setOtherPhotosOpen(Boolean(ownSet && (singleton || !ownSetContainsSelected))); }, [ownSet?.id, ownSetInstance, singleton, ownSetContainsSelected]);
  const [popup, setPopup] = useState(null);
  const restoreStackFocus = useRef(null);

  const candidates = useMemo(() => {
    const shots = cut === 'product' ? [shot] : (shotOptions || [{ value: 'full' }, { value: 'medium' }]).map(option => option.value);
    return filterExamplesForModel(shots.flatMap(value => selectGenerationExamples(catalogs.genExamples, {
      cutType: cut, shot: value, clothingType, gender, includeSetOnly: cut !== 'product',
      limit: catalogs.genExamples?.length || 0,
    })), identityKind);
  }, [catalogs.genExamples, cut, shot, shotOptions, clothingType, gender, identityKind, direction, sideStyle]);
  const sets = useMemo(() => filterSpaceSetsForModel(storyboardSpaceSetsFor({ gender, clothingType }), identityKind)
    .filter(set => set.id !== ownSet?.id && set.members.some(member => candidates.some(example => example.id === member.exampleId))), [candidates, gender, clothingType, identityKind, ownSet?.id]);
  const expandedSet = sets.find(set => set.id === expandedSetId);
  const hasSetMode = mode === 'set' && sets.length > 0;
  const showingStacks = hasSetMode && !expandedSet;
  const otherCandidates = ownSet ? candidates.filter(example => !ownSet.members.some(member => member.exampleId === example.id)) : candidates;
  const filtered = filterGalleryExamples(otherCandidates, { place, shot: shotFilter });
  const shotVal = cut === 'product' ? shot : shotFilter;
  const sessionSeed = useMemo(() => gallerySeedFactory(), [gallerySeedFactory, cut, clothingType, gender, identityKind, place, shotVal, fullGalleryOpen]);
  const eligible = expandedSet ? filtered.filter(example => expandedSet.members.some(member => member.exampleId === example.id)) : filtered;
  const entries = groupGalleryExamples(eligible, expandedSet ? [] : sets, { selectedId: exampleId, setsOnly: showingStacks });
  const items = sampleGalleryEntries(entries, { selectedId: exampleId, seed: sessionSeed });
  const examples = items;
  const selectedExample = catalogs.genExamples?.find(example => example.id === exampleId);
  const selectedStatus = exampleId ? storedExampleConditionStatus(selectedExample, { cutType: cut, clothingType, gender }) : 'valid';
  const galleryRef = useRef(null);
  const galleryRegionRef = useRef(null);
  const dotsRef = useRef(null);
  const [dotsOverflow, setDotsOverflow] = useState(false);
  const galleryScrollTimer = useRef(null);
  const galleryPages = paginateGenerationGalleryItems(examples);
  const selectedGalleryId = items.find(item => item.kind === 'stack'
    ? item.members.some(member => member.id === exampleId) : item.example.id === exampleId)?.id;
  const galleryPageCount = galleryPages.length;
  const [galleryPage, setGalleryPage] = useState(() => generationGalleryPageForExample(galleryPages, selectedGalleryId));
  const mineTab = mine;
  const previousGalleryContext = useRef(null);
  const closePopup = (restore, chosenId = null) => {
    if (restore && popup) {
      restoreStackFocus.current = { id: popup.id, chosenId, originalId: exampleId };
    }
    setPopup(null);
  };
  useEffect(() => { setPopup(null); }, [cut, gender, clothingType, identityKind, place, shotVal, mode, expandedSetId, fullGalleryOpen, ownSetInstance]);
  useEffect(() => {
    const request = restoreStackFocus.current;
    if (popup || !request) return;
    if (request.chosenId && exampleId !== request.originalId && exampleId !== request.chosenId) {
      restoreStackFocus.current = null; return;
    }
    const stack = [...(galleryRegionRef.current?.querySelectorAll?.('[data-storyboard-stack]') || [])]
      .find(element => element.dataset.storyboardStack === request.id);
    (stack || galleryRegionRef.current)?.focus?.({ preventScroll: true });
    if (!request.chosenId || request.chosenId === exampleId) restoreStackFocus.current = null;
  }, [popup, exampleId]);
  const setCurrentGalleryPage = (page) => {
    const targetPage = clampGenerationGalleryPage(page, galleryPageCount);
    const grid = galleryRef.current;
    const pageElement = grid?.querySelectorAll('.sb-expage')[targetPage];
    const focusedCell = grid?.ownerDocument.activeElement?.closest?.('.sb-excell');
    if (focusedCell && grid.contains(focusedCell) && pageElement && !pageElement.contains(focusedCell)) {
      galleryRegionRef.current?.focus({ preventScroll: true });
    }
    setGalleryPage(targetPage);
    return targetPage;
  };
  const scrollToGalleryPage = (page, behavior = 'smooth') => {
    clearTimeout(galleryScrollTimer.current);
    const targetPage = setCurrentGalleryPage(page);
    const element = galleryRef.current?.querySelectorAll('.sb-expage')[targetPage];
    if (element) galleryRef.current.scrollTo?.({ left: element.offsetLeft, behavior });
  };
  // 예시 선택으로 바뀌는 direction·sideStyle은 페이지 초기화 조건이 아니다.
  useEffect(() => {
    const conditions = [cut, shotVal, clothingType, gender, place, mode, expandedSetId, fullGalleryOpen];
    const previous = previousGalleryContext.current;
    const conditionsChanged = !previous || conditions.some((value, index) => value !== previous.conditions[index]);
    const selectedPage = generationGalleryPageForExample(galleryPages, selectedGalleryId);
    if (conditionsChanged || (previous.mineTab && !mineTab)) {
      scrollToGalleryPage(selectedPage, 'auto');
    } else if (previous.exampleId !== exampleId
      && galleryPages[selectedPage].some((example) => example.id === selectedGalleryId)
      && selectedPage !== galleryPage) {
      scrollToGalleryPage(selectedPage, 'auto');
    } else if (galleryPage >= galleryPageCount) {
      scrollToGalleryPage(galleryPageCount - 1, 'auto');
    }
    previousGalleryContext.current = { conditions, exampleId, mineTab };
  }, [cut, shotVal, clothingType, gender, exampleId, galleryPage, galleryPageCount, mineTab, place, mode, expandedSetId, fullGalleryOpen]);
  useEffect(() => () => clearTimeout(galleryScrollTimer.current), []);
  useEffect(() => {
    const dots = dotsRef.current;
    if (!dots) return undefined;
    const sync = () => setDotsOverflow(dots.scrollWidth > dots.clientWidth);
    sync();
    if (typeof ResizeObserver !== 'function') return undefined;
    const observer = new ResizeObserver(sync); observer.observe(dots);
    return () => observer.disconnect();
  }, [galleryPageCount, fullGalleryOpen]);
  useEffect(() => {
    const dots = dotsRef.current, current = dots?.querySelector('[aria-current="page"]');
    if (!current) return;
    dots.scrollTo?.({ left: Math.max(0, current.offsetLeft + current.offsetWidth / 2 - dots.clientWidth / 2),
      behavior: typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }, [galleryPage, galleryPageCount, fullGalleryOpen]);
  useEffect(() => {
    const grid = galleryRef.current;
    if (!grid || typeof MutationObserver !== 'function') return;
    // 썸네일 실패 때 나중에 붙는 '다시 시도'도 숨은 페이지의 Tab 순서에서 뺀다.
    const syncPageTabStops = () => {
      grid.querySelectorAll('.sb-expage').forEach((page) => {
        const tabIndex = page.getAttribute('aria-hidden') === 'true' ? -1 : 0;
        page.querySelectorAll('[tabindex]').forEach((element) => {
          if (element.tabIndex !== tabIndex) element.tabIndex = tabIndex;
        });
      });
    };
    syncPageTabStops();
    const observer = new MutationObserver(syncPageTabStops);
    observer.observe(grid, { childList: true, subtree: true, attributes: true, attributeFilter: ['aria-hidden', 'tabindex'] });
    return () => observer.disconnect();
  }, [mineTab, examples.length, fullGalleryOpen]);
  const updateGalleryPageFromScroll = () => {
    clearTimeout(galleryScrollTimer.current);
    // 부드러운 이동 중간 위치가 연속 넘기기의 목표 페이지를 되돌리지 않게 한다.
    galleryScrollTimer.current = setTimeout(() => {
      const pages = [...(galleryRef.current?.querySelectorAll('.sb-expage') || [])];
      if (!pages.length) return;
      const left = galleryRef.current.scrollLeft;
      let closest = 0;
      pages.forEach((page, index) => {
        if (Math.abs(page.offsetLeft - left) < Math.abs(pages[closest].offsetLeft - left)) closest = index;
      });
      setCurrentGalleryPage(closest);
    }, 120);
  };
  const drag = (event, example) => {
    if (!onExampleDrag || cut === 'product') { event.preventDefault(); return; }
    event.dataTransfer.effectAllowed = 'copy'; event.dataTransfer.setData('text/example-id', example.id); onExampleDrag(example.id);
  };
  const productShotControls = cut === 'product' && onShotChange && <ShotSegment
    options={(shotOptions || catalogs.productShotTypes || []).map(option => option.value === 'ghost' ? { ...option, label: '전체컷' } : option)}
    value={shot} onChange={onShotChange} cut={cut} clothingType={clothingType} gender={gender}
    isOptionPublished={candidateShot => hasAvailableGenerationRecipe(catalogs.genExamples, {
      cutType: cut, shot: candidateShot, clothingType, gender,
      productPresentation: candidateShot === 'ghost' ? (productPresentation || 'ghost') : null,
    })} />;
  const mineControl = onUseMine && <><button type="button" className={styles.mineToggle} onClick={() => setMine(!mine)} aria-expanded={mine}>내 사진으로 바꾸기</button>
    {mine && <MineImageTab images={refs} onImagesChange={onRefsChange} onChoose={onUseMine}
      onPickImage={() => onPickRef ? onPickRef() : api.pickRefImage(useAppStore.getState().projectId)} />}</>;
  if (cut === 'product' && shot === 'ghost' && productPresentation) return <div className={`insp-sec ${styles.exampleSection}`}>
    <div className={styles.exampleHeading}><label className="lbl">제품컷 형식</label></div>
    {productShotControls}{mineControl}
  </div>;
  return <div className={`insp-sec ${styles.exampleSection}`}>
    {ownSet && <div className={styles.sameSetSection}>
      <div className={styles.exampleHeading}><label className="lbl">{cut === 'horizon' ? '같은 세트의 다른 사진' : '같은 장소의 다른 사진'}</label><span>{spaceSetDisplayName(ownSet)}</span></div>
      <div className={styles.sameSetPhotos}>{ownSet.members.map(member => {
        const example = candidates.find(example => example.id === member.exampleId);
        if (!example) return null;
        return <div className={styles.galleryItem} key={member.exampleId}>
          <button type="button" className={`sb-excell ${styles.example}${member.exampleId === exampleId ? ' sel' : ''}`}
            aria-pressed={member.exampleId === exampleId} aria-label={`${spaceSetDisplayName(ownSet)} ${member.order}번째 사진`}
            draggable={Boolean(onExampleDrag)} onDragStart={event => drag(event, example)} onDragEnd={() => onExampleDrag?.(null)}
            onClick={() => onExampleChange?.(member.exampleId, cut === 'horizon' ? 'all' : 'pose')}>
            <ExampleThumb example={{ ...example, thumb: generationExampleImageSources(example).src || example.thumb }} />
            {member.exampleId === exampleId && <span className="ck"><Icon name="check" size={11} /></span>}
            {usedExampleIds.has(member.exampleId) && <span className={styles.used}>이미 사용</span>}
          </button><small>{member.shot === 'full' ? '전신' : clothingType === 'bottom' ? '하반신' : '상반신'}, {({ front: '정면', side: '옆면', back: '뒷면' })[member.direction]}</small>
        </div>;
      })}</div>
      {cut !== 'horizon' && !otherPhotosOpen && <button type="button" className={styles.otherPhotosToggle} aria-expanded={false} onClick={() => setOtherPhotosOpen(true)}>
        이 컷만 다른 사진으로 바꾸기<Icon name="chevDown" size={14} /></button>}
    </div>}
    {fullGalleryOpen && <div className={ownSet ? styles.otherPhotos : undefined}>
    <div className={styles.exampleHeading}><label className="lbl">{ownSet ? cut === 'horizon' ? '다른 스튜디오 사진' : '다른 장소 사진' : '예시 사진'}</label>
      {ownSet && <span>이 사진만 바뀌어요{cut !== 'horizon' && <button type="button" className={styles.otherPhotosToggle} aria-expanded={true} onClick={() => setOtherPhotosOpen(false)}>접기<Icon name="chevUp" size={14} /></button>}</span>}
    </div>
    {productShotControls}
    {sets.length > 0 && <div className={`modal-actions ${styles.exampleModes}`}>
      {[['single', '단일컷'], ['set', '세트컷']].map(([value, label]) => <button type="button" key={value}
        aria-pressed={mode === value} className={mode === value ? styles.modeOn : ''}
        onClick={() => { setMode(value); setExpandedSetId(null); }}>{label}</button>)}
    </div>}
    {cut === 'styling' && <PlaceTabs value={place} onChange={value => { setPlace(value); setExpandedSetId(null); }} />}
    {cut !== 'product' && <div className={`modal-actions ${styles.shotPills}`} aria-label="구도">
      {[['all', '전체'], ['full', '전신'], ['medium', clothingType === 'bottom' ? '하반신' : '상반신']].map(([value, label]) => <button type="button" key={value}
        disabled={value !== 'all' && !filterGalleryExamples(otherCandidates, { place, shot: value }).length}
        aria-pressed={shotFilter === value} className={shotFilter === value ? styles.shotOn : ''} onClick={() => setShotFilter(value)}>{label}</button>)}
    </div>}
    {expandedSet && <button type="button" className={styles.back} onClick={() => setExpandedSetId(null)}><Icon name="chevLeft" size={14} />전체 예시</button>}
    {exampleId && selectedStatus !== 'valid' && <div className="sb-current-example has-error">
      <p className="sb-example-error">저장된 예시를 다시 골라주세요</p>
      {candidates.length > 0 && <button type="button" className="sb-example-retry" onClick={() => onExampleChange?.(candidates[0].id, 'all')}>다시 선택</button>}
    </div>}
    {isSourceBasedDetailRecipe({ cutType: cut, shot, clothingType }) && !candidates.length
      ? <p className="hint">예시 없이 내 상품 원본으로 디테일을 촬영해요. 촬영 연출 예시는 선택 사항이에요.</p>
      : <div className={`sb-exgallery ${styles.gallery}`} role="region" aria-label="생성예시 갤러리" tabIndex={0} ref={galleryRegionRef}
      onKeyDown={event => {
        if (popup) return;
        if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
        event.preventDefault(); scrollToGalleryPage(generationGalleryPageForKey(galleryPage, event.key, galleryPageCount));
      }}>
      <div className="sb-exgrid" ref={galleryRef} onScroll={updateGalleryPageFromScroll}>
        {galleryPages.map((entries, pageIndex) => <div key={pageIndex} className="sb-expage" aria-hidden={pageIndex !== galleryPage}>
          {entries.map((item, index) => {
            const stack = item.kind === 'stack', singleStack = stack && !showingStacks;
            const members = stack ? item.members : [item.example];
            const example = item.example;
            const selected = members.some(member => member.id === exampleId);
            return <div className={styles.galleryItem} key={`item:${item.id}`}>
              <button type="button" key={item.id} className={`sb-excell ${styles.example}${stack ? ` ${styles.stack}` : ''}${singleStack ? ` ${styles.singleStack}` : ''}${selected ? ' sel' : ''}`}
                tabIndex={pageIndex === galleryPage ? 0 : -1} aria-pressed={selected}
                aria-haspopup={singleStack ? 'dialog' : undefined} aria-expanded={singleStack ? popup?.id === item.id : undefined}
                data-storyboard-stack={singleStack ? item.id : undefined}
                aria-label={stack ? `${spaceSetDisplayName(item.set)}, ${members.length}장${singleStack ? '. 누르면 펼쳐요' : ' 펼치기'}` : `${examplePlace(example).label} ${example.shot === 'full' ? '전신' : clothingType === 'bottom' ? '하반신' : '상반신'} 예시`}
                draggable={Boolean(onExampleDrag) && cut !== 'product'} onDragStart={event => drag(event, example)} onDragEnd={() => onExampleDrag?.(null)}
                onClick={event => {
                  if (showingStacks) { setExpandedSetId(item.id); return; }
                  if (!singleStack) { onExampleChange?.(example.id, 'all'); return; }
                  if (popup?.id === item.id) { closePopup(true); return; }
                  setPopup({ id: item.id, anchor: event.currentTarget, keyboardOpen: event.detail === 0,
                    row: Math.floor(index / 3), column: index % 3 });
                }}>
                {singleStack && <><span className={styles.stackBack} aria-hidden="true"><img src={generationExampleImageSources(example).src || example.thumb} alt="" /></span>
                  <span className={styles.stackMiddle} aria-hidden="true"><img src={generationExampleImageSources(example).src || example.thumb} alt="" /></span></>}
                <span className={singleStack ? styles.singleStackPhoto : styles.examplePhoto}>
                <ExampleThumb example={{ ...example, thumb: generationExampleImageSources(example).src || example.thumb }} />
                {selected && <span className="ck"><Icon name="check" size={12} stroke={1.5} /></span>}
                {stack && <span className={styles.stackCount}>{members.length}장</span>}
                {usedExampleIds.has(example.id) && <span className={styles.used}>이미 사용</span>}
                </span>
              </button>
              {showingStacks && <small>{spaceSetDisplayName(item.set)}</small>}
            </div>;
          })}
          {!items.length && <div className="sb-exempty">이 조건에 맞는 예시를 준비 중이에요</div>}
        </div>)}
      </div>
      {galleryPageCount > 1 && <div className="sb-excontrols">
        <button type="button" className="sb-expage-hit prev" aria-label="이전 예시 페이지" disabled={galleryPage === 0} onClick={() => scrollToGalleryPage(galleryPage - 1)}><Icon name="chevLeft" size={16} stroke={1.5} /></button>
        <div className="sb-expages" ref={dotsRef} data-overflow={dotsOverflow || undefined}
          data-edge={galleryPage === 0 ? 'start' : galleryPage === galleryPageCount - 1 ? 'end' : 'both'} aria-label={`총 ${galleryPageCount}페이지 중 ${galleryPage + 1}페이지`}>
          {galleryPages.map((_value, index) => <button type="button" key={index} className={galleryPage === index ? 'on' : ''}
            aria-label={`${index + 1}페이지`} aria-current={galleryPage === index ? 'page' : undefined} onClick={() => scrollToGalleryPage(index)} />)}
        </div>
        <button type="button" className="sb-expage-hit next" aria-label="다음 예시 페이지" disabled={galleryPage === galleryPageCount - 1} onClick={() => scrollToGalleryPage(galleryPage + 1)}><Icon name="chevRight" size={16} stroke={1.5} /></button>
      </div>}
      {popup && items.some(item => item.id === popup.id) && <StoryboardStackPopup key={popup.id}
        entry={items.find(item => item.id === popup.id)} selectedId={exampleId} anchor={popup.anchor}
        galleryElement={galleryRegionRef.current} keyboardOpen={popup.keyboardOpen} row={popup.row} column={popup.column}
        usedExampleIds={usedExampleIds} onClose={closePopup} onChoose={id => onExampleChange?.(id, 'all')}
        onDrag={onExampleDrag && cut !== 'product' ? drag : null} onDragEnd={() => onExampleDrag?.(null)} />}
    </div>}
    {mineControl}
    </div>}
  </div>;
}
