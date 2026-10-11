import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import { applySellingPointEdit, SELLING_POINTS_MAX } from '../../src/features/analysis/sellingPoints.js';
import { isFeatureRunPending } from '../../src/lib/featureRun.js';
const source = readFileSync(new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8');
const title = source.indexOf('className="sec-title">강조하고 싶은 특징');
const fragment = source.slice(source.lastIndexOf('<div className="surface">', title), source.indexOf('{/* 5. model select', title)).trim();
const { code } = await transformWithEsbuild(`const chips = () => (${fragment});`, 'Chips.jsx', { jsx: 'transform' });
const find = (tree, predicate) => !tree || typeof tree !== 'object' ? null : predicate(tree) ? tree
  : (tree.props?.children || []).flat(Infinity).map((child) => find(child, predicate)).find(Boolean);
for (const fast of [false, true]) test(`특징 삭제 버튼은 붙여넣은 보이지 않는 문구도 지운다, 빠른 진입 ${fast}`, () => {
  let patch;
  const a = { sellingPoints: ['\u200b'], aiSuggestedPoints: [], dismissedAiPoints: [] };
  const bindings = { React: { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) },
    a, spEditIdx: null, spAdding: false, spDraft: '', highlightedPoints: [], aiSet: new Set(), SELLING_POINTS_MAX,
    Icon: 'Icon', isAnalysisFastEntryEnabled: () => fast, isFeatureRunPending, applySellingPointEdit,
    onChange: (value) => { patch = value; }, setSpAdding() {} };
  const chips = new Function(...Object.keys(bindings), `${code}; return chips;`)(...Object.values(bindings));
  const button = find(chips(), (node) => node.props.className === 'sp-chip-x');
  button.props.onClick({ stopPropagation() {} });
  assert.ok(patch);
  assert.deepEqual(patch.sellingPoints, []);
});
test('빠른 진입의 AI 칩 삭제 버튼은 버린 문구 목록도 함께 기록한다', () => {
  let patch; const a = { sellingPoints: ['AI 문구'], aiSuggestedPoints: ['AI 문구'], dismissedAiPoints: [] };
  const bindings = { React: { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) },
    a, spEditIdx: null, spAdding: false, spDraft: '', highlightedPoints: [], aiSet: new Set(a.aiSuggestedPoints), SELLING_POINTS_MAX,
    Icon: 'Icon', isAnalysisFastEntryEnabled: () => true, isFeatureRunPending, applySellingPointEdit,
    onChange: (value) => { patch = value; }, setSpAdding() {} };
  const chips = new Function(...Object.keys(bindings), `${code}; return chips;`)(...Object.values(bindings));
  find(chips(), (node) => node.props.className === 'sp-chip-x').props.onClick({ stopPropagation() {} });
  assert.deepEqual(patch.dismissedAiPoints, ['AI 문구']);
});
