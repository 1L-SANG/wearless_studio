import { detailPageCreditCost, CREDIT_COSTS } from '../../lib/limits.js';
import { axesFor, fitProfileCategory } from '../../lib/fitAxes.js';
import { genderForClothingType } from '../../lib/productGender.js';
import { AI_MODELS } from '../analysis/aiModels.js';

const ANALYSIS_FIELDS = new Set(['category', 'fit', 'materials', 'features', 'model', 'matching']);
const CUT_LABELS = { styling: '스타일링', horizon: '호리존', mirror: '거울컷', product: '제품컷', detail: '디테일' };

export function summaryEditTarget(field, projectId) {
  return {
    path: ANALYSIS_FIELDS.has(field) ? '/create/input' : '/create/storyboard',
    state: { summaryEditProjectId: projectId, summaryEditField: field },
  };
}

export function isSummaryAnalysisEdit(entry, project) {
  return Boolean(project.projectPersisted && project.projectId
    && entry?.summaryEditProjectId === project.projectId
    && ANALYSIS_FIELDS.has(entry?.summaryEditField));
}

export function buildGenerationSummary({ analysis = {}, catalogs = {}, blocks = null,
  aiCutCount = null, copywriting = false } = {}) {
  const type = analysis.clothingType;
  const category = analysis.customCategory
    || catalogs.subCategories?.[type]?.find((item) => item.value === analysis.subCategory)?.label
    || catalogs.clothingTypes?.find((item) => item.value === type)?.label
    || '미입력';
  const fitEditor = summaryFitEditor(analysis);
  const fit = fitEditor.options.find((item) => item.value === fitEditor.value)?.label || '사진 그대로';
  const model = AI_MODELS.find((item) => item.id === analysis.selectedModelId)
    || [...(analysis.models || []), ...(catalogs.models || [])].find((item) => item.id === analysis.selectedModelId);
  const matching = (analysis.matchClothing || []).filter((item) => item.selected)
    .sort((a, b) => (a.selOrder || 0) - (b.selOrder || 0));
  const cutCounts = new Map();
  for (const block of blocks || []) {
    const label = block.source === 'mine' ? '내 사진' : block.cutType === 'product' && block.shot === 'detail' ? '디테일' : CUT_LABELS[block.cutType] || '기타 컷';
    cutCounts.set(label, (cutCounts.get(label) || 0) + 1);
  }
  return {
    category,
    fit,
    materials: (analysis.materials || []).map((item) => `${item.name}${item.ratio ? ` ${item.ratio}%` : ''}`).join(', ') || '미입력',
    features: (analysis.sellingPoints || []).join(', ') || '선택 없음',
    featureItems: analysis.sellingPoints || [],
    model: model?.displayName || model?.name || (analysis.selectedModelId ? '선택한 모델' : '선택 없음'),
    modelThumb: model?.thumb || '',
    matching: matching.map((item) => item.name).join(', ') || '선택 없음',
    matchingThumb: matching[0]?.thumb || '',
    cutCount: blocks?.length ?? null,
    cutTypes: [...cutCounts].map(([label, count]) => ({ label, count })),
    cuts: [...cutCounts].map(([label, count]) => `${label} ${count}컷`).join(', ') || '확인 중',
    copywriting: copywriting ? '켜짐' : '꺼짐',
    credits: detailPageCreditCost(aiCutCount),
    minimumNote: aiCutCount >= 1 && aiCutCount < CREDIT_COSTS.storyboardMinCuts
      ? `${aiCutCount}컷도 최소 ${CREDIT_COSTS.storyboardMinCuts}컷 비용이 적용돼요.` : '',
  };
}


export function summaryFitEditor(analysis = {}) {
  const profile = analysis.fitProfile;
  const category = fitProfileCategory(analysis.clothingType, analysis.subCategory);
  const gender = genderForClothingType(analysis.clothingType, analysis.targetGenders);
  const axes = analysis.subCategory === 'leggings' ? {} : axesFor(category, gender);
  const axis = axes.fit ? 'fit' : axes.cut ? 'cut' : 'silhouette';
  return {
    category, gender, axis, options: axes[axis] || [],
    value: profile === null ? null : profile ? profile.axes?.[axis] : analysis.fit,
  };
}

export function summaryFitPatch(analysis, value) {
  const editor = summaryFitEditor(analysis);
  return {
    ...(editor.axis === 'fit' ? { fit: value } : {}),
    fitProfile: {
      ...(analysis.fitProfile || {}), category: editor.category, gender: editor.gender,
      axes: { ...(analysis.fitProfile?.axes || {}), [editor.axis]: value },
      source: 'seller', version: 2,
    },
  };
}
