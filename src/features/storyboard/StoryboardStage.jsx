import React from 'react';
import { Icon } from '@/components/ui.jsx';
import { packStageRows } from '../../lib/pageTemplates/storyboardStage.js';
import { templateSectionName, wearUsageForBlock } from '../../lib/pageTemplates/slots.js';
import { isTextOnlySection, visibleSectionRuns } from '../../lib/pageTemplates/sectionRuns.js';
import { reuseSlotsForBlock, reuseSourceForSlot } from '../../lib/pageTemplates/sectionDependencies.js';
import { inferStoryboardSpaceSet } from '../../lib/storyboardSpaceSetCatalog.js';
import { spaceSetDisplayName } from '../../lib/spaceSetDisplayNames.js';
import { canPlanSimilarCut } from '../../lib/storyboardSimilarCuts.js';
import { rejectionOfBlock } from '../../lib/identityScope.js';
import { colorDisplayName } from '../../lib/colorwayMatching.js';
import { storyboardPreviewSource } from './storyboardPreview.js';
import { storyboardPositions, positionSwapReason, storyboardPositionSwapReason } from '../../lib/pageTemplates/storyboardPositions.js';
import { StoryboardPreview } from './StoryboardPreview.jsx';
import { StoryboardPaleSlot } from './StoryboardPaleSlot.jsx';
import { stageAdditions } from '../../lib/pageTemplates/stageAdditions.js';
import { pairedPhotoSlot } from '../../lib/pageTemplates/pairedDesigns.js';
import styles from './StoryboardLayout.module.css';

export const PHOTO_POSITION_DRAG_MIME = 'application/x-wearless-photo-position';

export function StoryboardStage({ run, number, blocks = [], page, catalogs, toneColor = null, colorOpts = [], photoOrdinals, selectedId, selectedViewId, selectedGroupId, dimOthers, matchClothing = [], identityKind,
  busy, canDesign, onDesign, onSelectPhoto, onRemovePhoto, onRemovePairedPhoto, onAddAddition, onSimilar, onAddSet, onReplaceSet,
  overview, onLeaveOverview, onSelectSection, onHoverPosition, onSwapPosition, onSwapBlocked, onPhotoDragStart, onPhotoDragEnd }) {
  const visibleKeys = visibleSectionRuns(blocks).map(section => section.key);
  if (overview) return <section className={styles.overview} aria-label="페이지 전체 보기">
    <div className={styles.overviewHead}><h2>페이지 전체 보기</h2><button type="button" className={styles.back} onClick={onLeaveOverview} autoFocus>편집으로 돌아가기</button></div>
    {[...new Set(page.filter(block => !block.pageSectionKey || visibleKeys.includes(block.pageSectionKey)).map(block => block.pageSectionKey))].map((key, index) => key
      ? <button type="button" key={key} onClick={() => onSelectSection(key)} className={styles.overviewSection} aria-label={`${templateSectionName(page.find(block => block.pageSectionKey === key))} 편집`}>
        <StoryboardPreview blocks={page.filter(block => block.pageSectionKey === key)} />
      </button> : <StoryboardPreview key={`auto:${index}`} blocks={page.filter(block => !block.pageSectionKey)} label="사이즈와 관리 안내" />)}
  </section>;
  if (!run) return null;
  const ownPhotos = run.blocks.filter(block => block.source !== 'text');
  const reused = reuseSlotsForBlock(run.blocks[0]).flatMap(slot => {
    if (ownPhotos.some(block => block.slotKey?.endsWith(`.${slot.slotKey}`))) return [];
    const source = reuseSourceForSlot(blocks, slot, catalogs.detailProduct);
    return source ? [source] : [];
  });
  const positions = storyboardPositions(page, run.key);
  const byId = new Map(blocks.map(block => [block.id, block]));
  const photos = positions.length ? positions.flatMap(position => {
    const source = byId.get(position.sourceBlockId);
    if (!source) return [];
    const borrowed = source.pageSectionKey !== run.key;
    return [{ ...source, id: positions.filter(item => item.sourceBlockId === source.id).length > 1 ? position.viewId : source.id,
      sourceId: source.id, position, borrowed, ...(borrowed ? { spaceGroupId: null } : {}),
      ...(run.kind === 'points' ? { stageRowKind: borrowed ? 'crop' : 'detail' } : {}) }];
  }) : [...ownPhotos, ...reused];
  const canAdd = run.kind === 'wear' && wearUsageForBlock(run.blocks.find(block => block.source !== 'text') || run.blocks[0]) === 'styling';
  const sourceRun = photos.find(block => block.borrowed);
  const sourceKey = sourceRun && byId.get(sourceRun.sourceId)?.pageSectionKey;
  const sourceNumber = visibleKeys.indexOf(sourceKey) + 1;
  const additions = stageAdditions(blocks, run.key, { product: catalogs.detailProduct, analysis: catalogs.analysis });
  const stagedPhotos = photos.flatMap((block, index) => {
    const addition = run.kind === 'first' && index === 0 ? additions.find(item => item.kind === 'firstGhost')
      : block.spaceGroupId !== photos[index + 1]?.spaceGroupId && additions.find(item => item.groupId === block.spaceGroupId);
    return addition ? [block, { id: addition.id, paleAddition: addition, spaceGroupId: addition.groupId || null, colorId: block.colorId }] : [block];
  });
  stagedPhotos.push(...additions.filter(item => !item.groupId && item.kind !== 'firstGhost').map(item => ({ id: item.id, paleAddition: item,
    ...(run.kind === 'points' ? { stageRowKind: 'detail' } : {}) })));
  const rows = packStageRows(stagedPhotos, { add: canAdd });
  const baseColor = catalogs.detailProduct?.colors?.find(color => color.isBase)?.id || photos[0]?.colorId;
  const colorProducts = run.kind === 'color_product';
  const colorName = id => {
    const colors = catalogs.detailProduct?.colors || [], index = colors.findIndex(color => color.id === id);
    return colorDisplayName(colors[index], index >= 0 ? `색상 ${index + 1}` : '색상');
  };
  const spaceHeading = group => !colorProducts && group.kind === 'space' && (run.kind === 'wear' || group.blocks.length > 1);
  const colorHeading = group => run.kind === 'wear' && group.kind === 'color';
  return <section className={styles.stage} data-storyboard-scroll aria-label={`${templateSectionName(run.blocks[0])} 무대`}>
    <div className={styles.stageHead}><svg className={styles.stageToneBand} width="100%" height="100%" aria-hidden="true"><rect width="100%" height="100%" fill={toneColor || 'var(--bg-2)'} /></svg>
      <div><h2>{number > 0 && <em>Section {number}</em>}<span>{templateSectionName(run.blocks[0])}</span></h2>
      <p>예시 이미지를 클릭해서 변경할 수 있어요</p></div>
      <div className={`modal-actions ${styles.stageActions}`}>
        {canDesign && <button type="button" className={styles.designButton} disabled={busy} onClick={onDesign}><Icon name="pencil" size={14} />섹션 디자인 변경</button>}
      </div>
    </div>
    <div className={`${styles.stageBody}${selectedGroupId ? ` ${styles.groupSelection}` : ''}`}>
      {run.kind === 'points' && sourceNumber > 0 && <p className={styles.stageSourceNote}>Section {sourceNumber} 사진에서 따와요</p>}
      {isTextOnlySection(run) && <p className={styles.stageSourceNote}>글은 생성할 때 AI가 써요</p>}
      {rows.map((row, rowIndex) => {
        const hasHeading = row.some(group => spaceHeading(group) || colorHeading(group));
        return <div className={styles.stageRow} key={rowIndex}>
        {row.map(group => group.kind === 'add' ? <div className={`${styles.addSlot}${hasHeading ? ` ${styles.addWithHeading}` : ''}`} key="add">
          {run.kind === 'wear' && <button type="button" className={styles.pill} disabled={busy} onClick={onAddSet}>＋ 장소 세트 추가</button>}
        </div> : <div className={styles.stageGroup} key={`${group.key}:${rowIndex}`} data-span={group.blocks.length}>
          {hasHeading && <div className={styles.groupHead}>
            {spaceHeading(group) && <><strong>{spaceSetDisplayName(inferStoryboardSpaceSet(group.blocks[0].spaceGroupId))}</strong><i />
              <button type="button" className={styles.pill} disabled={busy} onClick={() => onReplaceSet(blocks.find(block => block.spaceGroupId === group.blocks[0].spaceGroupId) || group.blocks[0])}>{group.blocks[0].cutType === 'horizon' ? '스튜디오 세트 바꾸기' : '장소 바꾸기'}</button></>}
            {colorHeading(group) && <><strong>{group.blocks[0].colorId !== baseColor ? colorName(group.blocks[0].colorId) : '추가한 컷'}</strong><i /></>}
          </div>}
          <div className={styles.groupPhotos} data-columns={group.blocks.length}>
            {group.blocks.map(block => {
              if (block.paleAddition) return <StoryboardPaleSlot key={block.id} item={block.paleAddition} blocks={blocks} runKey={run.key} catalogs={catalogs} disabled={busy} onAdd={onAddAddition} />;
              const source = block.similarSourceBlockId ? blocks.find(item => item.id === block.similarSourceBlockId) : block;
              const optionalPhoto = pairedPhotoSlot(run.designId)
                && (block.position?.slotKey || block.slotKey)?.endsWith(`.${pairedPhotoSlot(run.designId)}`);
              const requiredOpening = run.kind === 'first' && block.slotKey && !block.similarSourceBlockId;
              const removable = optionalPhoto || !block.borrowed && run.kind !== 'sns' && !requiredOpening && block.pageSectionKey === run.key && (ownPhotos.length > 1 || run.blocks.some(item => item.source === 'text'));
              const sourceId = block.sourceId || block.id;
              const ordinal = photoOrdinals.get(sourceId);
              const selected = selectedGroupId ? block.spaceGroupId === selectedGroupId : selectedViewId ? block.position?.viewId === selectedViewId : selectedId === sourceId;
              const swapReason = positionSwapReason(block);
              const geometry = block.position?.element;
              const crop = (block.borrowed || geometry?.previewDetailCrop) && geometry?.crop;
              const matching = block.source === 'ai' && block.cutType !== 'product' && block.matchIdsOrigin === 'user'
                ? matchClothing.find(item => item.id === block.matchIds?.[0]) : null;
              const rejection = rejectionOfBlock(block, identityKind);
              return <article className={`${styles.photoCard}${dimOthers && !selected ? ` ${styles.dimPhoto}` : ''}`} key={block.id} onMouseEnter={() => onHoverPosition?.(block.position?.viewId || null)} onMouseLeave={() => onHoverPosition?.(null)}>
                <div className={styles.photoFrame}>
                  <button type="button" data-storyboard-photo={sourceId} className={`${styles.photo}${selected ? ` ${styles.selectedPhoto}` : ''}`}
                    disabled={busy} onClick={() => onSelectPhoto(sourceId, block.position?.viewId, block.borrowed ? run.key : undefined)}
                    draggable={!busy} onDragStart={event => {
                      if (swapReason) { event.preventDefault(); onSwapBlocked?.(swapReason); return; }
                      event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData(PHOTO_POSITION_DRAG_MIME, sourceId); onPhotoDragStart?.();
                    }} onDragEnd={() => onPhotoDragEnd?.()}
                    onDragEnter={event => { if ([...event.dataTransfer.types].includes(PHOTO_POSITION_DRAG_MIME)) event.preventDefault(); }}
                    onDragOver={event => { if ([...event.dataTransfer.types].includes(PHOTO_POSITION_DRAG_MIME)) event.preventDefault(); }}
                    onDrop={event => {
                      event.preventDefault(); const from = event.dataTransfer.getData(PHOTO_POSITION_DRAG_MIME); onPhotoDragEnd?.();
                      if (!from) return;
                      const reason = swapReason || storyboardPositionSwapReason(blocks, from, sourceId);
                      if (reason) onSwapBlocked?.(reason); else onSwapPosition?.(from, sourceId);
                    }} aria-pressed={selected} aria-label={`${ordinal}번째 컷 사진 바꾸기`}>
                    {block.position?.label && <span className={styles.positionBadge}>{block.position.label}</span>}
                    {crop ? <svg className={styles.cropPreview} viewBox={`0 0 ${geometry.w} ${geometry.h}`} width="100%" height="100%" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
                      <svg width={geometry.w} height={geometry.h} viewBox={`0 0 ${geometry.w} ${geometry.h}`} overflow="hidden">
                        <image href={geometry.src} width={crop.iw || geometry.w} height={crop.ih || geometry.h} x={-(crop.ox || 0)} y={-(crop.oy || 0)} preserveAspectRatio="xMidYMid slice" />
                      </svg>
                    </svg> : storyboardPreviewSource(source, catalogs) ? <img src={storyboardPreviewSource(source, catalogs)} alt="" loading="lazy" decoding="async" /> : <span className={styles.photoMissing}>사진 설정을 확인해주세요</span>}
                    {block.similarSourceBlockId && <span className={styles.similarVeil}>{String(photoOrdinals.get(block.similarSourceBlockId)).padStart(2, '0')}번째 컷과 비슷하게</span>}
                  </button>
                  {removable && <button type="button" className={styles.removePhoto} disabled={busy} onClick={() => optionalPhoto ? onRemovePairedPhoto(run.key) : onRemovePhoto(block.id)} aria-label={optionalPhoto ? `${run.kind === 'first' ? '고스트컷' : '제품컷'} 빼기` : `${ordinal}번째 컷 빼기`}><Icon name="x" size={14} /></button>}
                  {matching?.thumb && <span className={styles.matchBadge} title={`매칭 의류 · ${matching.name || ''}`}><img src={matching.thumb} alt="" /><small>매칭</small></span>}
                  {rejection && <span className={styles.unsupported} title={rejection.message}>이 모델로 만들 수 없는 컷</span>}
                  {run.kind !== 'sns' && block.pageSectionKey === run.key && canPlanSimilarCut(block) && photos.length < 12 && <button type="button" className={`btn btn-primary btn-glowring ${styles.similar}`}
                    disabled={busy} onClick={() => onSimilar(block.id)}>비슷한 컷 추가</button>}
                </div>
                <div className={styles.photoCaption}>{catalogs.detailProduct?.colors?.length > 1 && <svg className={styles.captionColor} viewBox="0 0 14 14" role="img" aria-label={colorName(block.colorId)}>
                  <circle cx="7" cy="7" r="6" fill={colorOpts.find(color => color.id === block.colorId)?.hex || 'var(--fg-3)'} />
                </svg>}<strong>{ordinal}번째 컷</strong></div>
              </article>;
            })}
          </div>
        </div>)}
      </div>;
      })}
      {!photos.length && <StoryboardPreview blocks={page.filter(block => block.pageSectionKey === run.key)} />}
    </div>
  </section>;
}
