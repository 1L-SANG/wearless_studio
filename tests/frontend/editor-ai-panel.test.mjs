import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { selectGenerationExamples } from '../../src/lib/generationExamples.js';
import { ALL_CUT_TYPE_OPTIONS } from '../../src/lib/storyboardTaxonomy.js';

// 에디터 AI 탭 '새 이미지 추가' — 콘티보드와 같은 안전장치가 빠지지 않게 고정한다(PR #123 리뷰 반영).
const panelSource = readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8');
const editorSource = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const catalog = JSON.parse(readFileSync(new URL('../../src/data/genExamples.json', import.meta.url), 'utf8'));

test('매칭 의류는 단일 선택이다 — matchClothingMax=1 (PRD §6.8, 콘티보드 동일)', () => {
  const aiPanel = panelSource.slice(panelSource.indexOf('export function AIPanel'));
  assert.match(aiPanel, /setMatchIds\(on \? \[\] : \[m\.id\]\)/);
  assert.match(aiPanel, /aria-pressed=\{on\}/);
});

test('컷 종류 탭은 원본 기반 디테일 또는 발행 예시가 있는 종류를 제공한다', () => {
  const aiPanel = panelSource.slice(panelSource.indexOf('export function AIPanel'));
  assert.match(aiPanel, /const cutTypeOptions = ALL_CUT_TYPE_OPTIONS\.map/);
  assert.match(aiPanel, /disabled: !shots\.some\(\(item\) => hasSelectableExamples\(option\.value, item\.value\)\)/);
  assert.match(aiPanel, /<UnderlineTabs options=\{cutTypeOptions\}/);
  assert.deepEqual(ALL_CUT_TYPE_OPTIONS.map((option) => option.value), ['styling', 'horizon', 'product']);
  // 현재 카탈로그 실측 — 거울샷은 발행 예시 0건이라 게이트가 닫혀 있어야 한다(예시가 추가되면 자동 활성).
  const mirrorPublished = ['full', 'medium'].some((shot) => selectGenerationExamples(catalog, {
    cutType: 'mirror', shot, clothingType: 'top', gender: 'women', appendSetOnly: true,
  }).length > 0);
  assert.equal(mirrorPublished, false);
});

test('컷 종류 기본 샷은 원본 기반 디테일을 포함한 사용 가능한 샷으로 고른다', () => {
  const aiPanel = panelSource.slice(panelSource.indexOf('export function AIPanel'));
  assert.match(aiPanel, /hasSelectableExamples\(value, preferred\) \? preferred/);
  assert.match(aiPanel, /nextShotOpts\.find\(\(option\) => hasSelectableExamples\(value, option\.value\)\)/);
});

test('고스트→디테일 샷 전환은 이전 뒷면 방향을 리셋한다 (콘티보드 Codex 리뷰 P1 가드)', () => {
  const aiPanel = panelSource.slice(panelSource.indexOf('export function AIPanel'));
  const onShotChange = aiPanel.slice(aiPanel.indexOf('onShotChange={'), aiPanel.indexOf('clothingType={clothingType}'));
  assert.match(onShotChange, /setExampleId\(null\)/);
  assert.match(onShotChange, /setDetailTargetId\(null\)/);
});

test('컷 종류 전환·예시 교체는 매칭 의류·아우터 열림·내 레퍼런스를 전면 리셋한다 (settingsReset 동일 규칙)', () => {
  const aiPanel = panelSource.slice(panelSource.indexOf('export function AIPanel'));
  assert.match(aiPanel, /const resetRecipeSettings = \(\) => \{\s*setMatchIds\(\[\]\); setMatchOpen\(false\); setOuterClosure\('open'\); setRefImages\(\[\]\);/);
  const selectCutType = aiPanel.slice(aiPanel.indexOf('const selectCutType'), aiPanel.indexOf('const selectExample'));
  assert.match(selectCutType, /resetRecipeSettings\(\)/);
  const selectExample = aiPanel.slice(aiPanel.indexOf('const selectExample'), aiPanel.indexOf('return ('));
  assert.match(selectExample, /if \(replacing\) resetRecipeSettings\(\)/);
});

test('isDetail 은 검증된 effectiveShotVal 을 읽는다 — raw shot 은 카탈로그 폴백과 어긋난다', () => {
  const aiPanel = panelSource.slice(panelSource.indexOf('export function AIPanel'));
  assert.match(aiPanel, /const isDetail = isProduct && effectiveShotVal === 'detail'/);
});

test('갤러리·게이트 성별은 가상 모델 또는 분석 기반 exampleGender를 쓴다', () => {
  const aiPanel = panelSource.slice(panelSource.indexOf('export function AIPanel'));
  assert.match(aiPanel, /virtualModels\.find\(\(item\) => item\.id === model\)\?\.gender\s*\|\| exampleGender/);
  assert.match(editorSource, /exampleGender=\{exampleGenderFromAnalysis\(analysis, catalogs, clothingType\)\}/);
});

test('새 컷은 가상 모델만 고르고 옛 실패 컷의 실제 모델 판정은 유지한다', () => {
  const aiPanel = panelSource.slice(panelSource.indexOf('export function AIPanel'));
  assert.doesNotMatch(aiPanel, /fmList|fmModels|ModelThumb/);
  assert.match(aiPanel, /virtualModels\.map/);
  assert.match(aiPanel, /const categoryRequired = failedCutRetry[\s\S]*isRealModelSelection\(model\);/);
});

test('거울 예시는 파생 유효 레시피로 화면과 생성 페이로드를 함께 전환한다', () => {
  const aiPanel = panelSource.slice(panelSource.indexOf('export function AIPanel'));
  assert.match(aiPanel, /const effectiveRecipe = \{[\s\S]*generationExampleStructuralRecipePatch\(baseRecipe, selectedExample\)/);
  assert.match(aiPanel, /const galleryCutType = cutType/);
  assert.match(aiPanel, /const galleryShotVal = galleryShotOptions\.some/);
  assert.match(aiPanel, /contentRole: inferContentRole\(\{ source: 'ai', cutType: galleryCutType, shot: galleryShotVal \}\)/);
  assert.match(aiPanel, /<MoodGuide catalogs=\{catalogs\} cut=\{galleryCutType\} blockCutType=\{effectiveCutType\}/);
  assert.match(aiPanel, /direction=\{galleryDirectionVal\} sideStyle=\{directionSpec\.sideStyle\} shot=\{galleryShotVal\}/);
  assert.match(aiPanel, /includeMirrorExamples=\{galleryCutType === 'styling'\}/);
  assert.match(aiPanel, /contentRole: effectiveRecipe\.contentRole/);
  // 거울은 방향이 없고(null), 그 외에는 **화면 값이 아니라 컷 스펙**이 나간다 —
  // 방향 칩은 4개(정면·사선·옆모습·뒷면)인데 서버 계약은 front/side/back 셋이다.
  // 화면 값을 그대로 보내면 'threeQuarter' 가 direction 으로 날아간다.
  assert.match(aiPanel, /cutType: effectiveCutType,/);
  assert.match(aiPanel, /direction: isMirror \? null : directionSpec\.direction,/);
  assert.match(aiPanel, /sideStyle: isMirror \? null : directionSpec\.sideStyle,/);
  assert.match(aiPanel, /shot: effectiveShotVal/);
  assert.match(aiPanel, /appendMirror: cut === 'styling'/);
  assert.doesNotMatch(aiPanel, /setCutType\('mirror'\)/, '거울 선택은 원래 컷 탭 상태를 덮어쓰지 않는다');
});
