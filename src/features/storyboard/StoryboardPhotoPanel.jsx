import React, { useState, useEffect } from 'react';
import { api } from '@/lib/api/index.js';
import { Icon } from '@/components/ui.jsx';
import OuterClosureField from '@/components/OuterClosureField.jsx';
import { DetailTargetPicker } from './DetailTargetPicker.jsx';
import { ProductSourcePicker } from './ProductSourcePicker.jsx';
import { StoryboardGarmentColorField as GarmentColorField } from './StoryboardGarmentColorField.jsx';
import HorizonBackgroundControl from './HorizonBackgroundControl.jsx';
import { UnderlineTabs, ShotSegment, MineImageTab, MINE_SHOT_OPTION } from './StoryboardControls.jsx';
import { StoryboardPhotoGallery as MoodGuide } from './StoryboardPhotoGallery.jsx';
import { ProductPresentationPicker } from './ProductPresentationPicker.jsx';
import { garmentColorSelectionPatch } from '../../lib/garmentColors.js';
import { referenceFeedbackPatch } from '../../lib/storyboardExampleFeedback.js';
import { photoSlotForBlock } from '../../lib/pageTemplates/slots.js';
import { isSinglePhotoSection } from '../../lib/pageTemplates/storyboardPhotoChoices.js';
import { productSectionState } from '../../lib/pageTemplates/productPresentation.js';
import { horizonBackgroundAvailability } from '../../lib/horizonBackgroundAvailability.js';
import { normalizeOuterClosureState } from '../../lib/outerClosure.js';
import { CONTENT_ROLES, SECTION_ROLES, cutTypeOptionsForSection, normalizedRecipePatch, defaultContentRoleForSection, productPresentationOf } from '../../lib/storyboardTaxonomy.js';
import { hasAvailableGenerationRecipe, isSourceBasedProductRecipe } from '../../lib/generationExamples.js';
import { productSourceColorPatch } from '../../lib/productSourcePhotos.js';
import { generationExampleSelectionPatch } from '../../lib/storyboardExampleSelection.js';
import { mineImageUrl, normalizeMineImages, promoteMineImage } from '../../lib/storyboardMineImages.js';
import { signatureExamplesForBlock, signatureCardForBlock, isSignatureBlock, isSignatureExampleId } from '../../lib/signatureCutPool.js';
import styles from './StoryboardPanels.module.css';

const WORN_CUT_TYPES = new Set(['styling', 'horizon']);
const WORN_ROLE_BY_CUT_TYPE = { styling: CONTENT_ROLES.COORDINATION, horizon: CONTENT_ROLES.FIT };
export const shouldRenderGenerationExampleGuide = block => Boolean(block?.cutType);

export function StoryboardPhotoPanel({ block, blocks, busy = false, fixedProduct = false, catalogs, colorOpts, detailColorOpts, clothingType, subCategory, customCategory, exampleGender, identityKind = null, hasDetailImage, projectId, onChange, onAtomicChange, onRetryAtomicSave, requestedRecipe, onCancelRequestedRecipe, matchClothing, customMatchPromotionPending = false, spaceContext, onChangeSpaceSet, onExampleDrag, onChangeSpaceBackground, onUploadStart, usedExampleIds }) {
  const [matchOpen, setMatchOpen] = useState(false);
  const [pendingRecipe, setPendingRecipe] = useState(null);
  const [pendingChoice, setPendingChoice] = useState(null);
  const [pendingError, setPendingError] = useState(null);
  const [pendingSaving, setPendingSaving] = useState(false);
  const [pickerSaveError, setPickerSaveError] = useState(null);
  const [pickerSaving, setPickerSaving] = useState(false);
  useEffect(() => { setMatchOpen(false); }, [block?.id]);
  useEffect(() => {
    // block과 requestedRecipe가 둘 다 null이면 undefined===undefined로 참이 되어
    // null.cutType을 읽다 죽는다(같은 카드 재클릭=선택 해제 시 백지 화면의 원인, 2026-07-29)
    setPendingRecipe(requestedRecipe && block && requestedRecipe.blockId === block.id
      ? { cutType: requestedRecipe.cutType, shot: requestedRecipe.shot,
        ...(requestedRecipe.cutType === 'product' && requestedRecipe.shot === 'ghost'
          ? { productPresentation: productPresentationOf(block) || 'ghost' } : {}) } : null);
    setPendingChoice(null); setPendingError(null); setPendingSaving(false);
    setPickerSaveError(null); setPickerSaving(false);
  }, [block?.id, requestedRecipe?.blockId, requestedRecipe?.cutType, requestedRecipe?.shot]);

  if (!block) return null;
  const productMode = block.source === 'ai' && block.cutType === 'product' && block.shot === 'ghost';
  const productState = productMode ? productSectionState(blocks || [block], block.pageSectionKey) : null;
  const modeRequired = productState && ['missing', 'mixed'].includes(productState.status);
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
    {colorField}
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
  const pendingInSpace = !!block.spaceGroupId && !requestedRecipe;
  // 디테일 샷 상시 제공(2026-08-07 개편) — 디테일 사진이 없어도 서버가 원본 구조 확대로 생성
  const slotDefinition = photoSlotForBlock(block);
  const permittedShots = slotDefinition?.recipe?.allowedShots;
  const wornShotOptions = catalogs.shotTypes.filter(option => !permittedShots || permittedShots.includes(option.value));
  const productShotOptions = catalogs.productShotTypes.filter(option => !permittedShots || permittedShots.includes(option.value));
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
      disabled: !shots.some((shot) => hasSelectableExamples(option.value, shot)),
    };
  });
  const onCutTypeChange = (cutType) => {
    if (block.cutType === cutType) { setPendingRecipe(null); return; }
    const availableShots = cutType === 'product' ? productShotOptions : wornShotOptions;
    const shot = availableShots.find((option) => option.value === block.shot
      && hasSelectableExamples(cutType, option.value))?.value
      || availableShots.find((option) => hasSelectableExamples(cutType, option.value))?.value
      || block.shot;
    setPendingError(null);
    setPendingChoice(null);
    setPendingRecipe({ cutType, shot, ...(cutType === 'product' && shot === 'ghost' ? { productPresentation: 'ghost' } : {}) });
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
  const commitPendingRecipe = async (exampleId) => {
    if (!pendingRecipe || pendingSaving) return;
    const example = (guideCatalogs.genExamples || []).find((item) => item.id === exampleId);
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
      shot: WORN_CUT_TYPES.has(recipePatch.cutType) ? example.shot : recipePatch.shot,
      colorId,
      angle: 'same',
      exampleId,
      baseThumb: current.baseThumb ?? current.thumb,
      exampleSelectionOrigin: exampleId ? 'user' : null,
      refScope: selection.patch.refScope,
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
    const base = opening ? { ...current, cutType: example.cutType, shot: example.shot } : current;
    const recipe = opening ? normalizedRecipePatch(base, CONTENT_ROLES.HERO) : {};
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
    if (block.spaceGroupId) {
      const selected = generationExamplePatch(block, example, scope);
      setPickerSaveError(null);
      setPickerSaving(true);
      return onAtomicChange(selected.changes, {
        retryAtomic: true,
        pickerOwnsError: true,
        undoLabel: selected.settingsReset ? '예시·설정 초기화' : '참조',
      }).catch(() => {
        setPickerSaveError('변경 내용을 저장하지 못했어요');
      }).finally(() => setPickerSaving(false));
    }
    const selected = generationExamplePatch(block, example, scope);
    onChange(selected.changes, {
      undoLabel: selected.settingsReset ? '예시·설정 초기화' : '참조',
    });
    return undefined;
  };
  const showOuterClosure = clothingType === 'outer' && block.source === 'ai' && WORN_CUT_TYPES.has(block.cutType);
  const outerClosureState = normalizeOuterClosureState(block.outerClosureState);
  return (
    <div className={`inspector ${styles.photoPanel}`}>
      {isMine && !block.spaceGroupId ? (
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
      {block.cutType === 'horizon' && block.spaceGroupId && <div className="insp-sec">
        <button type="button" className={styles.studioChange} onClick={onChangeSpaceSet}>다른 스튜디오 세트로 바꾸기</button>
        <HorizonBackgroundControl block={block} memberCount={spaceContext?.siblings?.length}
          onChange={onChangeSpaceBackground} availability={horizonBackgroundAvailability(spaceContext?.siblings, catalogs.detailProduct,
            catalogs.garmentColorEvidence, spaceContext?.set?.id)} />
      </div>}
      {!block.cutType ? (
        <>
          <div className="insp-empty-hint"><Icon name="info" size={15} />이 이미지의 생성 설정을 준비하지 못했어요. 블록을 취소하고 다시 추가해주세요.</div>
        </>
      ) : (
        <>
      {/* 세트 멤버는 컷 종류가 세트에 고정 — 잠금 표시 대신 아예 숨긴다(2026-08-15 오너). */}
      {!spaceContext && !isSinglePhotoSection(block) && (
        <div className="insp-sec">
          <div className="sb-cut-label-row"><label className="lbl">컷 종류</label></div>
          <UnderlineTabs
            options={cutTypeOptions}
            value={pendingRecipe?.cutType || block.cutType}
            onChange={onCutTypeChange} />
        </div>
      )}

      {pendingRecipe ? (
        <div className="sb-pending-recipe">
          {isSourceBasedProductRecipe({ ...pendingRecipe, clothingType }) && <div className="modal-actions">
            <button type="button" className="btn btn-primary" disabled={pendingSaving} onClick={() => commitPendingRecipe(null)}>제품컷 적용</button>
          </div>}
          {requestedRecipe && <button type="button" className="insp-cancel-new" onClick={() => {
            setPendingRecipe(null);
            onCancelRequestedRecipe?.();
          }}>섹션 이동 취소</button>}
          {shouldRenderGenerationExamples && (
            <MoodGuide block={block} projectId={projectId} busy={busy} usedExampleIds={usedExampleIds} identityKind={identityKind} catalogs={guideCatalogs} cut={pendingRecipe.cutType}
              direction={block.direction}
              sideStyle={block.sideStyle ?? null}
              shot={pendingRecipe.shot}
              productPresentation={pendingRecipe.productPresentation}
              shotOptions={pendingRecipe.cutType === 'product' ? productShotOptions : wornShotOptions}
              onShotChange={(shot) => setPendingRecipe((current) => ({ ...current, shot,
                ...(current.cutType === 'product' ? { productPresentation: shot === 'ghost' ? 'ghost' : null } : {}) }))}
              clothingType={clothingType} gender={exampleGender}
              exampleId={pendingChoice} onExampleChange={commitPendingRecipe} onExampleDrag={onExampleDrag}
              refScope={pendingInSpace && pendingRecipe.cutType !== 'horizon' ? 'pose' : 'all'} />
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
          {isProduct && !isDetail && !block.spaceGroupId && <>
            <ProductSourcePicker block={block} product={catalogs.detailProduct} catalogs={catalogs} onChange={onChange} />
            {!productPresentationOf(block) && <p className="hint">예시 사진은 연출만 참고해요.</p>}
          </>}
          {shouldRenderGenerationExamples && (
            <MoodGuide block={block} projectId={projectId} signature={signature} busy={busy || pickerSaving} usedExampleIds={usedExampleIds} identityKind={identityKind} onUploadStart={onUploadStart} onUseMine={(ref) => onChange({
              source: 'mine', title: '내 이미지', cutType: null, contentRole: CONTENT_ROLES.CUSTOM,
              ownImages: [ref?.url || ref], thumb: ref?.url || ref,
              exampleId: null, exampleSelectionOrigin: null, refScope: null,
              refImages: [], refAssetIds: [],
              spaceGroupId: null, spaceVariation: null,
            })} catalogs={guideCatalogs} cut={block.cutType} spaceContext={spaceContext}
              direction={block.direction} sideStyle={block.sideStyle ?? null} shot={block.shot}
              productPresentation={productPresentationOf(block)}
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
          {pickerSaveError && <div className="sb-save-error">{pickerSaveError}
            <button type="button" disabled={pickerSaving} onClick={async () => {
              setPickerSaving(true);
              try {
                const retried = await onRetryAtomicSave();
                if (retried) setPickerSaveError(null);
              } catch {
                setPickerSaveError('변경 내용을 저장하지 못했어요');
              } finally {
                setPickerSaving(false);
              }
            }}>다시 시도</button>
          </div>}
        </>
      )}

      {colorField}

      {showOuterClosure && (
        <OuterClosureField id={`outer-closure-${block.id}`} value={outerClosureState}
          onChange={(value) => onChange({ outerClosureState: value })} frontClosure={catalogs.frontClosure} />
      )}

      {/* 매칭 의류가 없으면 편집 패널이 빈 화면이 되므로 진입 자체를 막는다.
          세트 멤버(spaceGroupId)는 세트 연출이 정본이라 매칭 편집도 숨긴다(2026-08-15 오너). */}
      {WORN_CUT_TYPES.has(block.cutType) && !block.spaceGroupId
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

      {/* 추가 옵션(얼굴 노출·앵글)은 필드와 생성 로직을 유지하고 MVP 이후 재도입한다. */}
        </>
      )}
      </>}

    </div>
  );
}
