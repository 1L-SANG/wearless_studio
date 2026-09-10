import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

const modelLicenseSource = read('../../src/features/model/ModelLicense.jsx');
const shapesSource = read('../../src/lib/api/shapes.js');
const analysisFormSource = read('../../src/features/analysis/AnalysisForm.jsx');
const editorSource = read('../../src/features/editor/Editor.jsx');
const editorPanelsSource = read('../../src/features/editor/EditorPanels.jsx');
const typesSource = read('../../src/lib/types.js');

const allowed = [
  '일반 의류',
  '액티브웨어',
  '홈웨어·잠옷',
];
test('frontend brand-use categories exactly match the server contract', async () => {
  const categories = await import('../../src/lib/brandUseCategories.js');

  assert.deepEqual(Object.keys(categories), ['BRAND_USE_CATEGORIES']);
  assert.deepEqual(categories.BRAND_USE_CATEGORIES, allowed);
  assert.equal(new Set(categories.BRAND_USE_CATEGORIES).size, 3);
});

test('ModelLicense reuses the shared categories for toggles', () => {
  assert.match(modelLicenseSource, /useState\(\[\.\.\.BRAND_USE_CATEGORIES\]\)/);
  assert.match(modelLicenseSource, /BRAND_USE_CATEGORIES\.map/);
  assert.match(modelLicenseSource, /<Toggle/);
  assert.doesNotMatch(modelLicenseSource, /forbiddenUse|FORBIDDEN_BRAND_USE_CATEGORIES/);
});

test('analysis shape and form require an explicit category for a real model', () => {
  assert.match(shapesSource, /selectedModelId: null, stylingModelId: null, brandUseCategory: null, models: \[\]/);
  assert.match(analysisFormSource, /import \{ BRAND_USE_CATEGORIES \} from '@\/lib\/brandUseCategories\.js';/);
  assert.match(analysisFormSource, /isRealModelSelection,[\s\S]*resolveSelectedModelId,[\s\S]*resolveStylingModelId,[\s\S]*from '\.\/modelSelection\.js';/);
  assert.match(analysisFormSource, /isRealModelSelection\(a\.selectedModelId\)[\s\S]*?<Chips[\s\S]*?options=\{BRAND_USE_CATEGORIES\}[\s\S]*?value=\{a\.brandUseCategory\}/);
  assert.match(analysisFormSource, /실제 모델을 사용할 브랜드 유형을 선택해 주세요\./);
});

test('frontend analysis and new-cut contracts expose brandUseCategory', () => {
  const analysisContract = typesSource.slice(
    typesSource.indexOf('@typedef {Object} Analysis'),
    typesSource.indexOf('@typedef {Object} FitProfile'),
  );
  const newCutContract = typesSource.slice(
    typesSource.indexOf('@typedef {Object} NewCutRequest'),
    typesSource.indexOf('@typedef {Object} GenStep'),
  );
  assert.match(analysisContract, /@property \{string\|null\} brandUseCategory/);
  assert.match(newCutContract, /@property \{string\|null\} brandUseCategory/);
});

test('Editor AI panel remediates a missing persisted category in place', () => {
  const aiPanel = editorPanelsSource.slice(editorPanelsSource.indexOf('export function AIPanel'));
  const failedRetryStart = aiPanel.indexOf('if (failedCutRetry)');
  const failedRetryBranch = aiPanel.slice(
    failedRetryStart,
    aiPanel.indexOf('\n\n  return (', failedRetryStart),
  );
  assert.match(editorPanelsSource, /import \{ BRAND_USE_CATEGORIES \} from '@\/lib\/brandUseCategories\.js';/);
  assert.match(editorPanelsSource, /import \{ isRealModelSelection \} from '@\/features\/analysis\/modelSelection\.js';/);
  assert.match(aiPanel, /brandUseCategory = null/);
  assert.match(aiPanel, /brandUseCategorySaving = false/);
  assert.match(aiPanel, /onBrandUseCategoryChange/);
  assert.match(aiPanel, /const categoryRequired = failedCutRetry[\s\S]*?failedCutRetry\.request\?\.cutType === 'horizon'[\s\S]*?isRealModelSelection\(failedCutRetry\.request\?\.modelId\)[\s\S]*?: effectiveCutType === 'horizon' && isRealModelSelection\(model\);/);
  assert.match(aiPanel, /const brandUseCategoryBlocked = categoryRequired[\s\S]*?!brandUseCategory \|\| brandUseCategorySaving/);
  assert.match(aiPanel, /const brandUseCategoryControl = categoryRequired && \([\s\S]*?<Chips[\s\S]*?options=\{BRAND_USE_CATEGORIES\}[\s\S]*?value=\{brandUseCategory\}/);
  assert.match(failedRetryBranch, /\{brandUseCategoryControl\}/);
  assert.match(failedRetryBranch, /disabled=\{brandUseCategoryBlocked\}/);
  assert.equal(aiPanel.match(/\{brandUseCategoryControl\}/g)?.length, 2);
  assert.match(aiPanel, /disabled=\{brandUseCategoryBlocked\} onClick=\{\(\) => onGenerate/);
});

test('Editor persists category selection and keeps persisted analysis request-authoritative', () => {
  assert.match(editorSource, /const \[brandUseCategorySaving, setBrandUseCategorySaving\] = useState\(false\);/);
  assert.match(editorSource, /api\.saveAnalysis\(projectId, \{ brandUseCategory: value \}\)/);
  assert.match(editorSource, /setAnalysis\(\(current\) => \(\{ \.\.\.\(current \|\| \{\}\), \.\.\.\(saved \|\| \{\}\), brandUseCategory: value \}\)\)/);
  assert.match(editorSource, /api\.generateImage\(projectId, \{ mode: 'new', \.\.\.req,[^}]*brandUseCategory: analysis\?\.brandUseCategory \}\)/);
  assert.match(editorSource, /<AIPanel[\s\S]*?brandUseCategory=\{analysis\?\.brandUseCategory\}[\s\S]*?brandUseCategorySaving=\{brandUseCategorySaving\}[\s\S]*?onBrandUseCategoryChange=\{saveBrandUseCategory\}/);
});

test('Editor image generation fails closed before side effects for an unready real-model category', () => {
  const generateImageStart = editorSource.indexOf('const generateImage = async (req) =>');
  const generateImage = editorSource.slice(
    generateImageStart,
    editorSource.indexOf('const failedCutRetry =', generateImageStart),
  );
  const beforeSideEffects = generateImage.slice(0, generateImage.indexOf('const group ='));

  assert.match(editorSource, /isRealModelSelection,[\s\S]*stylingModelPatchForAnalysis,[\s\S]*from '@\/features\/analysis\/modelSelection\.js';/);
  assert.match(beforeSideEffects, /req\.cutType !== 'horizon'[\s\S]*?isRealModelSelection\(req\.modelId\)/);
  assert.match(beforeSideEffects, /req\.cutType === 'horizon'[\s\S]*?isRealModelSelection\(req\.modelId\)/);
  assert.match(beforeSideEffects, /!analysis\?\.brandUseCategory \|\| brandUseCategorySaving/);
  assert.match(beforeSideEffects, /return null;/);
});

test('Editor product cuts discard retained model identity without requiring a category', () => {
  const generateImageStart = editorSource.indexOf('const generateImage = async (req) =>');
  const generateImage = editorSource.slice(
    generateImageStart,
    editorSource.indexOf('const failedCutRetry =', generateImageStart),
  );

  assert.match(generateImage, /api\.generateImage\(projectId, \{ mode: 'new', \.\.\.req, colorId: group,\s*modelId: req\.cutType === 'product' \? null : req\.modelId,\s*brandUseCategory: analysis\?\.brandUseCategory \}\)/);
});
