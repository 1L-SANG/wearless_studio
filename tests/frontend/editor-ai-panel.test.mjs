import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { ALL_CUT_TYPE_OPTIONS } from '../../src/lib/storyboardTaxonomy.js';

// 에디터 AI 탭 '새 이미지 만들기' — 콘티보드와 같은 안전장치가 빠지지 않게 고정한다(PR #123 리뷰 반영).
const panelSource = readFileSync(new URL('../../src/features/editor/EditorPanels.jsx', import.meta.url), 'utf8');
const editorSource = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');

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

test('아우터 열림은 공용 Field에 분석을 넘기고 숨김 여부와 관계없이 기존 생성값을 보낸다', () => {
  const aiPanel = panelSource.slice(panelSource.indexOf('export function AIPanel'));
  assert.match(aiPanel, /const showOuterClosure = clothingType === 'outer' && !isProduct;/);
  assert.match(aiPanel, /<OuterClosureField id="outer-closure-newcut" value=\{outerClosure\}\s*onChange=\{setOuterClosure\} frontClosure=\{frontClosure\}/);
  assert.match(editorSource, /frontClosure=\{analysis\?\.frontClosure\}/);
  assert.match(aiPanel, /outerClosureState: showOuterClosure \? outerClosure : null/);
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

test('새 컷은 가상 모델을 고르고 브랜드 유형은 현재 고른 모델로 판정한다', () => {
  const aiPanel = panelSource.slice(panelSource.indexOf('export function AIPanel'));
  assert.doesNotMatch(aiPanel, /fmList|fmModels|ModelThumb/);
  assert.match(aiPanel, /virtualModels\.map/);
  assert.match(aiPanel, /const categoryRequired = isRealModelSelection\(model\);/);
});

test('생성 요청은 선택한 컷 종류와 서버 방향 값을 사용한다', () => {
  const aiPanel = panelSource.slice(panelSource.indexOf('export function AIPanel'));
  assert.match(aiPanel, /const effectiveRecipe = baseRecipe;/);
  assert.match(aiPanel, /const galleryCutType = cutType/);
  assert.match(aiPanel, /const galleryShotVal = galleryShotOptions\.some/);
  assert.match(aiPanel, /contentRole: inferContentRole\(\{ source: 'ai', cutType: galleryCutType, shot: galleryShotVal \}\)/);
  assert.match(aiPanel, /<MoodGuide catalogs=\{catalogs\} cut=\{galleryCutType\}/);
  assert.match(aiPanel, /direction=\{galleryDirectionVal\} sideStyle=\{directionSpec\.sideStyle\} shot=\{galleryShotVal\}/);
  assert.match(aiPanel, /contentRole: effectiveRecipe\.contentRole/);
  // 착용컷은 예시의 서버 방향 값과 사선 표현을 함께 보낸다.
  assert.match(aiPanel, /cutType: effectiveCutType,/);
  assert.match(aiPanel, /direction: directionSpec\.direction,/);
  assert.match(aiPanel, /sideStyle: directionSpec\.sideStyle,/);
  assert.match(aiPanel, /shot: effectiveShotVal/);
});
