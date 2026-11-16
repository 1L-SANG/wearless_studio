import React, { useState, useEffect } from 'react';
import { api } from '@/lib/api/index.js';
import { Icon } from '@/components/ui.jsx';
import OuterClosureField from '@/components/OuterClosureField.jsx';
import { DetailTargetPicker } from './DetailTargetPicker.jsx';
import { ProductSourcePicker } from './ProductSourcePicker.jsx';
import { StoryboardGarmentColorField as GarmentColorField } from './StoryboardGarmentColorField.jsx';
import { UnderlineTabs, ShotSegment, MineImageTab, MINE_SHOT_OPTION } from './StoryboardControls.jsx';
import { StoryboardPhotoGallery as MoodGuide } from './StoryboardPhotoGallery.jsx';
import { ProductPresentationPicker } from './ProductPresentationPicker.jsx';
import { garmentColorSelectionPatch } from '../../lib/garmentColors.js';
import { referenceFeedbackPatch } from '../../lib/storyboardExampleFeedback.js';
import { photoSlotForBlock } from '../../lib/pageTemplates/slots.js';
import { isSinglePhotoSection } from '../../lib/pageTemplates/storyboardPhotoChoices.js';
import { productSectionState } from '../../lib/pageTemplates/productPresentation.js';
import { setPhotoGroupId } from '../../lib/storyboardSetOptions.js';
import { normalizeOuterClosureState } from '../../lib/outerClosure.js';
import { CONTENT_ROLES, SECTION_ROLES, cutTypeDisplay, cutTypeOptionsForSection, normalizedRecipePatch, defaultContentRoleForSection, productPresentationOf } from '../../lib/storyboardTaxonomy.js';
import { studioStyleOptions } from '../../lib/cutDisplaySelection.js';
import { hasAvailableGenerationRecipe, isSourceBasedProductRecipe } from '../../lib/generationExamples.js';
import { productSourceColorPatch } from '../../lib/productSourcePhotos.js';
import { generationExampleSelectionPatch } from '../../lib/storyboardExampleSelection.js';
import { mineImageUrl, normalizeMineImages, promoteMineImage } from '../../lib/storyboardMineImages.js';
import { signatureExamplesForBlock, signatureCardForBlock, isSignatureBlock, isSignatureExampleId } from '../../lib/signatureCutPool.js';
import styles from './StoryboardPanels.module.css';

const WORN_CUT_TYPES = new Set(['styling', 'horizon']);
const WORN_ROLE_BY_CUT_TYPE = { styling: CONTENT_ROLES.COORDINATION, horizon: CONTENT_ROLES.FIT };
export const shouldRenderGenerationExampleGuide = block => Boolean(block?.cutType);

export function StoryboardPhotoPanel({ block, blocks, busy = false, fixedProduct = false, catalogs, colorOpts, detailColorOpts, clothingType, subCategory, customCategory, exampleGender, identityKind = null, hasDetailImage, projectId, onChange, onAtomicChange, requestedRecipe, onCancelRequestedRecipe, matchClothing, customMatchPromotionPending = false, onExampleDrag, onUploadStart, usedExampleIds }) {
  const [matchOpen, setMatchOpen] = useState(false);
  const [pendingRecipe, setPendingRecipe] = useState(null);
  const [pendingChoice, setPendingChoice] = useState(null);
  const [pendingError, setPendingError] = useState(null);
  const [pendingSaving, setPendingSaving] = useState(false);
  useEffect(() => { setMatchOpen(false); }, [block?.id]);
  useEffect(() => {
    // block과 requestedRecipe가 둘 다 null이면 undefined===undefined로 참이 되어
    // null.cutType을 읽다 죽는다(같은 카드 재클릭=선택 해제 시 백지 화면의 원인, 2026-07-29)
    setPendingRecipe(requestedRecipe && block && requestedRecipe.blockId === block.id
      ? { cutType: requestedRecipe.cutType, shot: requestedRecipe.shot,
        ...(requestedRecipe.cutType === 'product' && requestedRecipe.shot === 'ghost'
          ? { productPresentation: productPresentationOf(block) || 'ghost' } : {}) } : null);
    setPendingChoice(null); setPendingError(null); setPendingSaving(false);
  }, [block?.id, requestedRecipe?.blockId, requestedRecipe?.cutType, requestedRecipe?.shot]);

  const lockedGroupId = setPhotoGroupId(block, blocks || []);
  if (!block || lockedGroupId) return null;
  const productMode = block.source === 'ai' && block.cutType === 'product' && block.shot === 'ghost';
  const productState = productMode ? productSectionState(blocks || [block], block.pageSectionKey) : null;
  const modeRequired = productState && ['missing', 'mixed'].includes(productState.status);
  const optionLock = { className: styles.photoOptions, inert: pendingRecipe ? '' : undefined, 'aria-disabled': pendingRecipe ? true : undefined };
  const colorField = !fixedProduct && block.source === 'ai' && block.cutType ? <GarmentColorField
    colorOpts={detailColorOpts?.length ? detailColorOpts : colorOpts} product={catalogs.detailProduct} block={block}
    value={block.colorId} disabled={busy || Boolean(modeRequired)}
    disabledReason={modeRequired ? '촬영 방식을 먼저 골라 주세요.' : ''} onChange={colorId => {
      if (busy || modeRequired) return;
      const changes = garmentColorSelectionPatch(block, catalogs.detailProduct, colorId);
      if (changes) onChange(changes);
    }} /> : null;
  if (productMode) return <div className={`inspector ${styles.photoPanel}`}>
    <ProductPresentationPicker block={block} blocks={blocks} product={catalogs.detailProduct}
      fixedProduct={fixedProduct} clothingType={clothingType} busy={busy} onChange={onAtomicChange || onChange} />
    <div {...optionLock}>{colorField}</div>
  </div>;

  const signatureExamples = signatureExamplesForBlock(block, { product: catalogs.detailProduct,
    analysis: { subCategory, customCategory }, gender: exampleGender });
  const signature = signatureCardForBlock(block, { product: catalogs.detailProduct, analysis: { subCategory, customCategory }, gender: exampleGender, projectId });
  const guideCatalogs = signatureExamples ? { ...catalogs, genExamples: [...signatureExamples, ...(catalogs.genExamples || [])] } : catalogs;
  const isMine = block.source === 'mine';
  const isProduct = block.cutType === 'product';
  const isDetail = block.contentRole === CONTENT_ROLES.DETAIL;
  const shouldRenderGenerationExamples = shouldRenderGenerationExampleGuide(block);
  const effectiveSectionRole = requestedRecipe?.sectionRole || block.sectionRole;
  // 디테일 샷 상시 제공(2026-08-07 개편) — 디테일 사진이 없어도 서버가 원본 구조 확대로 생성
  const slotDefinition = photoSlotForBlock(block);
  const permittedShots = slotDefinition?.recipe?.allowedShots;
  const wornShotOptions = catalogs.shotTypes.filter(option => !permittedShots || permittedShots.includes(option.value));
  const productShotOptions = catalogs.productShotTypes.filter(option => !permittedShots || permittedShots.includes(option.value));
  const display = cutTypeDisplay(block.cutType, block.exampleId, guideCatalogs.genExamples);
  const activeDisplay = pendingRecipe
    ? { ...cutTypeDisplay(pendingRecipe.cutType, null, guideCatalogs.genExamples),
      ...(pendingRecipe.studioStyle ? { value: 'horizon', studioStyle: pendingRecipe.studioStyle } : {}) } : display;
  const studioOptions = studioStyleOptions(guideCatalogs.genExamples?.filter(example => !isSignatureExampleId(example.id)), {
    shots: wornShotOptions.map(option => option.value), clothingType, gender: exampleGender, identityKind,
  }).map(option => option.value === 'minimal' && signature ? { ...option, disabled: false } : option);
  const hasSelectableExamples = (cutType, shot) => hasAvailableGenerationRecipe(
    guideCatalogs.genExamples,
    {
      cutType,
      shot,
      clothingType,
      ...(cutType === 'product' && shot === 'ghost' ? { productPresentation: 'ghost' } : {}),
      gender: exampleGender,
      appendSetOnly: cutType !== 'product',
    },
  );
  const cutTypeOptions = cutTypeOptionsForSection(block).map((option) => {
    const shots = option.value === 'product'
      ? productShotOptions.map((item) => item.value)
      : wornShotOptions.map((item) => item.value);
    return {
      ...option,
      disabled: option.value === 'horizon'
        ? studioOptions.every(item => item.disabled) : !shots.some((shot) => hasSelectableExamples(option.value, shot)),
    };
  });
  const onCutTypeChange = (value, studioStyle = (display.value === 'horizon' && display.studioStyle)
    || studioOptions.find(option => option.value === 'horizon' && !option.disabled)?.value
    || studioOptions.find(option => !option.disabled)?.value || 'horizon') => {
    const cutType = value === 'horizon' && studioStyle === 'minimal' ? 'styling' : value;
    if (display.value === value && (value !== 'horizon' || display.studioStyle === studioStyle)) { setPendingRecipe(null); return; }
    const availableShots = cutType === 'product' ? productShotOptions : wornShotOptions;
    const shot = availableShots.find((option) => option.value === block.shot
      && hasSelectableExamples(cutType, option.value))?.value
      || availableShots.find((option) => hasSelectableExamples(cutType, option.value))?.value
      || block.shot;
    setPendingError(null);
    setPendingChoice(null);
    setPendingRecipe({ cutType, shot, ...(value === 'horizon' ? { studioStyle } : {}),
      ...(cutType === 'product' && shot === 'ghost' ? { productPresentation: 'ghost' } : {}) });
  };
  const onShotChange = (shot) => {
    if (block.cutType === 'product' && shot === 'detail') {
      if (block.shot === shot) return;
      onChange({ ...normalizedRecipePatch({ ...block, shot }, CONTENT_ROLES.DETAIL),
        detailTargetId: null, detailTargetOrigin: 'user', exampleId: null, exampleSelectionOrigin: null });
      setPendingRecipe(null);
      return;
    }
    if (block.cutType === 'product') {
      if (block.shot === shot) { setPendingRecipe(null); return; }
      setPendingError(null);
      setPendingChoice(null);
      setPendingRecipe({ cutType: 'product', shot, ...(shot === 'ghost' ? { productPresentation: 'ghost' } : {}) });
      return;
    }
    onChange((current) => {
      if (!current.spaceGroupId || !current.exampleId) {
        return {
          shot,
          exampleSelectionOrigin: current.exampleId ? 'user' : null,
        };
      }
      return { shot, refScope: current.cutType === 'horizon' ? 'all' : 'pose', exampleSelectionOrigin: 'user' };
    });
  };
  const commitPendingRecipe = async (exampleId, recipe = pendingRecipe) => {
    if (!recipe || pendingSaving) return;
    const example = (guideCatalogs.genExamples || []).find((item) => item.id === exampleId);
    const pendingRecipe = isSignatureExampleId(exampleId) ? { ...recipe, cutType: 'horizon', shot: 'medium' } : recipe;
    if (!example && !isSourceBasedProductRecipe({ ...pendingRecipe, clothingType })) return;
    const current = block;
    const nextRole = pendingRecipe.cutType === 'product'
      ? (pendingRecipe.shot === 'detail' ? CONTENT_ROLES.DETAIL : CONTENT_ROLES.PRODUCT_OVERVIEW)
      : [SECTION_ROLES.STYLING, SECTION_ROLES.STUDIO].includes(effectiveSectionRole)
        ? WORN_ROLE_BY_CUT_TYPE[pendingRecipe.cutType]
        : [CONTENT_ROLES.HERO, CONTENT_ROLES.BENEFIT].includes(current.contentRole)
          ? current.contentRole : defaultContentRoleForSection(effectiveSectionRole);
    const baseRecipePatch = normalizedRecipePatch({
      ...current,
      source: 'ai',
      sectionRole: effectiveSectionRole,
      cutType: pendingRecipe.cutType,
      exampleId,
      shot: pendingRecipe.shot,
      productPresentation: pendingRecipe.productPresentation ?? null,
    }, nextRole, { hasDetailImage });
    const recipePatch = baseRecipePatch;
    const nextColorOpts = nextRole === CONTENT_ROLES.DETAIL ? detailColorOpts : colorOpts;
    const colorId = nextColorOpts.some((color) => color.id === current.colorId)
      ? current.colorId : nextColorOpts[0]?.id;
    const selection = generationExampleSelectionPatch({ ...current, ...recipePatch }, example, { clothingType, defaultColorId: colorId });
    const changes = referenceFeedbackPatch(current, {
      ...recipePatch,
      ...selection.patch,
      ...(recipePatch.cutType !== 'product' || recipePatch.shot !== 'detail'
        ? { detailTargetId: null, detailTargetOrigin: null } : {}),
      source: 'ai',
      shot: WORN_CUT_TYPES.has(recipePatch.cutType) ? example?.shot || recipePatch.shot : recipePatch.shot,
      colorId,
      angle: 'same',
      exampleId,
      baseThumb: current.baseThumb ?? current.thumb,
      exampleSelectionOrigin: exampleId ? 'user' : null,
      refScope: selection.patch.refScope,
      ...(isSignatureExampleId(exampleId) ? { cutType: 'horizon', shot: 'medium', direction: 'front', sideStyle: null, refScope: 'all',
        spaceGroupId: null, spaceVariation: null, spaceSetMemberOrder: null, setSelectionOrigin: null, horizonBackgroundMode: null } : {}),
      outerClosureState: clothingType === 'outer' && WORN_CUT_TYPES.has(recipePatch.cutType)
        ? normalizeOuterClosureState(current.outerClosureState)
        : null,
      ...(recipePatch.cutType === 'product' ? { matchIds: [], faceExposure: null } : {}),
      ...(recipePatch.cutType === 'product'
        ? { direction: ['front', 'back'].includes(current.direction) ? current.direction : 'front' } : {}),
      ...(recipePatch.cutType === 'product' && catalogs.detailProduct
        ? productSourceColorPatch({ ...current, ...recipePatch,
          direction: current.direction === 'back' ? 'back' : 'front' }, catalogs.detailProduct, colorId) : {}),
    }, guideCatalogs);
    setPendingChoice(exampleId);
    setPendingSaving(true);
    setPendingError(null);
    try {
      await onAtomicChange(changes, { pickerOwnsError: true });
      setPendingRecipe(null);
      setPendingChoice(null);
    } catch {
      setPendingError('변경 내용을 저장하지 못했어요');
    } finally {
      setPendingSaving(false);
    }
  };
  const generationExamplePatch = (current, example, scope) => {
    const opening = isSignatureBlock(current);
    const single = isSinglePhotoSection(current);
    const base = single ? { ...current, cutType: example.cutType, shot: example.shot } : current;
    const recipe = single ? normalizedRecipePatch({ ...base, exampleId: example.id }, opening ? CONTENT_ROLES.HERO : current.contentRole) : {};
    const selected = generationExampleSelectionPatch({ ...base, ...recipe }, example, {
      clothingType,
      defaultColorId: (isDetail ? detailColorOpts : colorOpts)[0]?.id,
      refScope: scope,
    });
    return {
      changes: referenceFeedbackPatch(current, {
        ...recipe,
        ...selected.patch,
        ...(isSignatureExampleId(example.id) ? { cutType: 'horizon', shot: 'medium', direction: 'front', sideStyle: null, refScope: 'all',
          spaceGroupId: null, spaceVariation: null, spaceSetMemberOrder: null, setSelectionOrigin: null, horizonBackgroundMode: null } : {}),
        baseThumb: current.baseThumb ?? current.thumb,
      }, guideCatalogs),
      settingsReset: selected.settingsReset,
    };
  };
  const onGenerationExampleChange = (exampleId, scope) => {
    const example = (guideCatalogs.genExamples || []).find((item) => item.id === exampleId);
    if (!example) return undefined;
    const selected = generationExamplePatch(block, example, scope);
    onChange(selected.changes, {
      undoLabel: selected.settingsReset ? '예시·설정 초기화' : '참조',
    });
    return undefined;
  };
  const showOuterClosure = clothingType === 'outer' && block.source === 'ai' && WORN_CUT_TYPES.has(block.cutType);
  const outerClosureState = normalizeOuterClosureState(block.outerClosureState);
  return (
    <div className={`inspector ${styles.photoPanel}${!isMine && (pendingRecipe?.cutType || block.cutType) !== 'product' ? ` ${styles.splitPhotoPanel}` : ''}`}>
      {isMine && !lockedGroupId ? (
        <>
          <div className="sb-exhead">
            <label className="lbl">샷 종류</label>
            <ShotSegment options={catalogs.shotTypes.map((option) => ({ ...option, disabled: true })).concat(MINE_SHOT_OPTION)}
              value="mine" onChange={() => {}} cut="styling" clothingType={clothingType} gender={exampleGender}
              isOptionPublished={(value) => value === 'mine'} />
          </div>
          <MineImageTab images={block.ownImages || []} onUploadStart={onUploadStart}
            onImagesChange={(images) => {
              const nextImages = normalizeMineImages(images);
              onChange({ ownImages: nextImages, thumb: nextImages[0] || null });
            }}
            onPickImage={() => api.pickAnyImage(projectId)} onChoose={(selectedImage) => {
              const nextImages = promoteMineImage(block.ownImages, selectedImage);
              onChange({ ownImages: nextImages, thumb: nextImages[0] || null });
            }} />
        </>
      ) : <>
      {!block.cutType ? (
        <>
          <div className="insp-empty-hint"><Icon name="info" size={15} />이 이미지의 생성 설정을 준비하지 못했어요. 블록을 취소하고 다시 추가해주세요.</div>
        </>
      ) : (
        <>
      {/* 세트 멤버는 컷 종류가 세트에 고정 — 잠금 표시 대신 아예 숨긴다(2026-08-15 오너). */}
      {cutTypeOptions.length > 1 && <>
      {isSinglePhotoSection(block) && (
        <div className={`insp-sec ${styles.cutTabs}`}>
          <div className="sb-cut-label-row"><label className="lbl">컷 종류</label></div>
          <UnderlineTabs options={cutTypeOptions} value={activeDisplay.value}
            onChange={value => { if (value !== activeDisplay.value) onCutTypeChange(value); }} />
        </div>
      )}
      {!isSinglePhotoSection(block) && (
        <div className={`insp-sec ${styles.cutTabs}`}>
          <div className="sb-cut-label-row"><label className="lbl">컷 종류</label></div>
          <UnderlineTabs
            options={cutTypeOptions}
            value={activeDisplay.value}
            onChange={value => { if (value !== activeDisplay.value) onCutTypeChange(value); }} />
        </div>
      )}
      </>}


      {pendingRecipe ? (
        <div className="sb-pending-recipe">
          {isSourceBasedProductRecipe({ ...pendingRecipe, clothingType }) && <div className="modal-actions">
            <button type="button" className="btn btn-primary" disabled={pendingSaving} onClick={() => commitPendingRecipe(null)}>{`${cutTypeDisplay(pendingRecipe.cutType).label} 적용`}</button>
          </div>}
          {requestedRecipe && <button type="button" className="insp-cancel-new" onClick={() => {
            setPendingRecipe(null);
            onCancelRequestedRecipe?.();
          }}>섹션 이동 취소</button>}
          {shouldRenderGenerationExamples && (
            <MoodGuide block={block} projectId={projectId} signature={signature} busy={busy} usedExampleIds={usedExampleIds} identityKind={identityKind} catalogs={guideCatalogs} cut={pendingRecipe.cutType}
              direction={block.direction}
              sideStyle={block.sideStyle ?? null}
              shot={pendingRecipe.shot}
              productPresentation={pendingRecipe.productPresentation}
              shotOptions={pendingRecipe.cutType === 'product' ? productShotOptions : wornShotOptions}
              onShotChange={(shot) => setPendingRecipe((current) => ({ ...current, shot,
                ...(current.cutType === 'product' ? { productPresentation: shot === 'ghost' ? 'ghost' : null } : {}) }))}
              clothingType={clothingType} gender={exampleGender}
              studioStyle={activeDisplay.studioStyle} studioOptions={studioOptions}
              onStudioStyleChange={value => onCutTypeChange('horizon', value)}
              exampleId={pendingChoice} onExampleChange={exampleId => commitPendingRecipe(exampleId)} onExampleDrag={onExampleDrag}
              refScope="all" />
          )}
          {pendingError && <div className="sb-save-error">{pendingError}
            <button type="button" disabled={(!pendingChoice && !isSourceBasedProductRecipe({ ...pendingRecipe, clothingType })) || pendingSaving}
              onClick={() => commitPendingRecipe(pendingChoice)}>다시 시도</button>
          </div>}
        </div>
      ) : (
        <>
          {isDetail && <DetailTargetPicker block={block} product={catalogs.detailProduct}
            catalogs={catalogs} recommendations={catalogs.detailRecommendations} onChange={onChange} />}
          {isProduct && !isDetail && !lockedGroupId && <>
            <ProductSourcePicker block={block} product={catalogs.detailProduct} catalogs={catalogs} onChange={onChange} />
            {!productPresentationOf(block) && <p className="hint">예시 사진은 연출만 참고해요.</p>}
          </>}
          {shouldRenderGenerationExamples && (
            <MoodGuide block={block} projectId={projectId} signature={signature} busy={busy} usedExampleIds={usedExampleIds} identityKind={identityKind} onUploadStart={onUploadStart} onUseMine={(ref) => onChange({
              source: 'mine', title: '내 이미지', cutType: null, contentRole: CONTENT_ROLES.CUSTOM,
              ownImages: [ref?.url || ref], thumb: ref?.url || ref,
              exampleId: null, exampleSelectionOrigin: null, refScope: null,
              refImages: [], refAssetIds: [],
              spaceGroupId: null, spaceVariation: null,
            })} catalogs={guideCatalogs} cut={block.cutType}
              direction={block.direction} sideStyle={block.sideStyle ?? null} shot={block.shot}
              productPresentation={productPresentationOf(block)}
              studioStyle={activeDisplay.studioStyle} studioOptions={studioOptions}
              onStudioStyleChange={value => onCutTypeChange('horizon', value)}
              shotOptions={isProduct ? productShotOptions : wornShotOptions}
              onShotChange={block.cutType === 'horizon' && block.exampleId && (block.refScope || 'all') === 'all' ? null : onShotChange}
              clothingType={clothingType} gender={exampleGender}
              exampleId={block.exampleId || null}
              onExampleChange={onGenerationExampleChange}
              onExampleDrag={onExampleDrag}
              refScope={block.refScope || 'all'}
              refs={(block.refImages || []).map((value, index) => ({ url: value?.url || value, assetId: value?.assetId || (block.refAssetIds || [])[index] }))}
              onRefsChange={(references) => onChange({
                refImages: references.map((value) => value?.url || value),
                refAssetIds: references.map((value) => value?.assetId).filter(Boolean),
              })} />
          )}

        </>
      )}

      <div {...optionLock}>
      {colorField}

      {showOuterClosure && (
        <OuterClosureField id={`outer-closure-${block.id}`} value={outerClosureState}
          onChange={(value) => onChange({ outerClosureState: value })} frontClosure={catalogs.frontClosure} />
      )}

      {/* 매칭 의류가 없으면 편집 패널이 빈 화면이 되므로 진입 자체를 막는다.
          원본 2장 이상인 세트만 잠그고, 한 장짜리 장소 묶음은 일반 사진처럼 편집한다. */}
      {WORN_CUT_TYPES.has(block.cutType) && !lockedGroupId
        && ((Array.isArray(matchClothing) && matchClothing.length > 0) || customMatchPromotionPending) && (
        <>
          <button className={`insp-detail-btn${matchOpen ? ' open' : ''}`} onClick={() => setMatchOpen((v) => !v)}>
            <Icon name="settings" size={17} />매칭 의류 바꾸기
          </button>
          {matchOpen && (
            <div className="sb-match-inline">
              <div className="match-grid">
                {customMatchPromotionPending && (
                  <div className="match-cell sb-match-pending" role="status" aria-label="내 옷 준비 중">
                    <span className="sb-match-pending-preview"><Icon name="loader" className="spin" size={20} /></span>
                    <span className="ml">내 옷 준비 중</span>
                  </div>
                )}
                {(matchClothing || []).map((m) => {
                  const on = (block.matchIds || []).includes(m.id);
                  return (
                    <button key={m.id} className={`match-cell${on ? ' on' : ''}`} aria-pressed={on}
                      /* origin 'user' = 셀러가 직접 바꾼 매칭 — 카드 우측 하단 오버레이 표시 조건 */
                      onClick={() => onChange({ matchIds: on ? [] : [m.id], matchIdsOrigin: 'user' })}>
                      <img src={m.thumb} alt={m.name} /><span className="ml">{m.name}{on && <Icon name="check" size={12} />}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
      </div>

      {/* 추가 옵션(얼굴 노출·앵글)은 필드와 생성 로직을 유지하고 MVP 이후 재도입한다. */}
        </>
      )}
      </>}

    </div>
  );
}
